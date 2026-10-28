"""Pure X API v2 contract logic + rate limiter + UI feed helpers over Store."""
import re
import threading
import time
from copy import deepcopy
from simx.store import Store

RATE_LIMIT_MAX = 300
RATE_LIMIT_WINDOW_S = 900  # 15 minutes


class APIError(Exception):
    def __init__(self, status: int, title: str, detail: str, headers: dict | None = None):
        super().__init__(detail)
        self.status = status
        self.title = title
        self.detail = detail
        self.headers = headers or {}

    def to_dict(self) -> dict:
        return {"title": self.title, "detail": self.detail, "status": self.status}


class RateLimiter:
    def __init__(self, limit: int = RATE_LIMIT_MAX, window_s: int = RATE_LIMIT_WINDOW_S):
        self.limit = limit
        self.window_s = window_s
        self.lock = threading.Lock()
        self.buckets: dict[str, list[ int | float ]] = {}

    def check(self, token: str, now: float | None = None) -> dict[str, str]:
        now = time.time() if now is None else now
        with self.lock:
            b = self.buckets.get(token)
            if not b or now >= b[1]:
                b = [0, int(now + self.window_s)]
                self.buckets[token] = b
            if b[0] >= self.limit:
                headers = {
                    "x-rate-limit-limit": str(self.limit),
                    "x-rate-limit-remaining": "0",
                    "x-rate-limit-reset": str(int(b[1])),
                }
                raise APIError(429, "Too Many Requests", "Rate limit exceeded", headers=headers)
            b[0] += 1
            rem = max(0, self.limit - b[0])
            return {
                "x-rate-limit-limit": str(self.limit),
                "x-rate-limit-remaining": str(rem),
                "x-rate-limit-reset": str(int(b[1])),
            }


def public_user(u: dict) -> dict:
    return {"id": u["id"], "username": u["username"], "name": u["name"]}


def format_media(m: dict, base_url: str) -> dict:
    url = f"{base_url.rstrip('/')}/media/{m['media_key']}.mp4"
    return {
        "media_key": m["media_key"],
        "type": m.get("type", "video"),
        "duration_ms": m.get("duration_ms", 10000),
        "variants": [
            {
                "content_type": "video/mp4",
                "bit_rate": m.get("bit_rate", 832000),
                "url": url,
            }
        ],
    }


def build_includes(store: Store, tweets: list[dict], expansions: set[str], base_url: str) -> dict:
    users_map: dict[str, dict] = {}
    tweets_map: dict[str, dict] = {}
    media_map: dict[str, dict] = {}

    want_author = "author_id" in expansions
    want_ref_tweets = "referenced_tweets.id" in expansions or "referenced_tweets.id.attachments.media_keys" in expansions
    want_media = "attachments.media_keys" in expansions
    want_ref_media = "referenced_tweets.id.attachments.media_keys" in expansions

    for t in tweets:
        if want_author:
            u = store.user_by_id(t["author_id"])
            if u:
                users_map[u["id"]] = public_user(u)
        if want_media:
            for mk in t.get("attachments", {}).get("media_keys", []):
                m = store.media_by_id_or_key(mk)
                if m:
                    media_map[mk] = format_media(m, base_url)
        if want_ref_tweets:
            for ref in t.get("referenced_tweets", []):
                rt = store.get_tweet(ref["id"])
                if rt:
                    tweets_map[rt["id"]] = deepcopy(rt)
                    if want_author:
                        ru = store.user_by_id(rt["author_id"])
                        if ru:
                            users_map[ru["id"]] = public_user(ru)
                    if want_ref_media or want_media:
                        for mk in rt.get("attachments", {}).get("media_keys", []):
                            m = store.media_by_id_or_key(mk)
                            if m:
                                media_map[mk] = format_media(m, base_url)

    includes: dict[str, list] = {}
    if want_author:
        includes["users"] = list(users_map.values())
    if want_ref_tweets:
        includes["tweets"] = list(tweets_map.values())
    if want_media or want_ref_media:
        includes["media"] = list(media_map.values())
    return includes


def parse_expansions(query: dict[str, list[str]]) -> set[str]:
    raw = ",".join(query.get("expansions", []))
    return {x.strip() for x in raw.split(",") if x.strip()}


def get_users_me(store: Store, caller: dict) -> dict:
    return {"data": public_user(caller)}


def get_user_by_username(store: Store, username: str) -> dict:
    u = store.user_by_username(username)
    if not u:
        raise APIError(404, "Not Found Error", f"Could not find user with username: [{username}].")
    return {"data": public_user(u)}


def post_tweet(store: Store, caller: dict, body: dict) -> dict:
    if not isinstance(body, dict):
        raise APIError(400, "Invalid Request", "Request body must be a JSON object.")
    text = body.get("text")
    if not isinstance(text, str) or not text.strip():
        raise APIError(400, "Invalid Request", " Tweet text must be a non-empty string.")
    if len(text) > 280:
        raise APIError(400, "Invalid Request", f"Tweet text exceeds 280 characters ({len(text)}).")

    reply_id = None
    if "reply" in body and body["reply"] is not None:
        if not isinstance(body["reply"], dict) or not body["reply"].get("in_reply_to_tweet_id"):
            raise APIError(400, "Invalid Request", "reply.in_reply_to_tweet_id is required when reply is set.")
        reply_id = str(body["reply"]["in_reply_to_tweet_id"])

    quote_id = str(body["quote_tweet_id"]) if body.get("quote_tweet_id") else None

    media_ids = None
    if "media" in body and body["media"] is not None:
        if not isinstance(body["media"], dict) or not isinstance(body["media"].get("media_ids"), list):
            raise APIError(400, "Invalid Request", "media.media_ids must be a list.")
        media_ids = [str(x) for x in body["media"]["media_ids"]]

    try:
        tw = store.create_tweet(
            author_id=caller["id"],
            text=text,
            in_reply_to_id=reply_id,
            quote_tweet_id=quote_id,
            media_ids=media_ids,
        )
    except KeyError as e:
        raise APIError(400, "Invalid Request", str(e))
    return {"data": {"id": tw["id"], "text": tw["text"]}}


def get_tweet_by_id(store: Store, tid: str, query: dict[str, list[str]], base_url: str) -> dict:
    tw = store.get_tweet(tid)
    if not tw:
        raise APIError(404, "Not Found Error", f"Could not find tweet with id: [{tid}].")
    expansions = parse_expansions(query)
    out = {"data": deepcopy(tw)}
    if expansions:
        out["includes"] = build_includes(store, [tw], expansions, base_url)
    return out


def mentions_username(text: str, username: str) -> bool:
    pat = rf"(?<![A-Za-z0-9_])@{re.escape(username)}(?![A-Za-z0-9_])"
    return re.search(pat, text, flags=re.IGNORECASE) is not None


def get_user_mentions(store: Store, uid: str, query: dict[str, list[str]], base_url: str) -> dict:
    target = store.user_by_id(uid)
    if not target:
        raise APIError(404, "Not Found Error", f"Could not find user with id: [{uid}].")

    since_id = (query.get("since_id") or [None])[0]
    since_int = int(since_id) if since_id else None

    max_results_raw = (query.get("max_results") or ["10"])[0]
    try:
        max_results = int(max_results_raw)
    except ValueError:
        raise APIError(400, "Invalid Request", "max_results must be an integer")
    if max_results < 5 or max_results > 100:
        raise APIError(400, "Invalid Request", "max_results must be between 5 and 100")

    uname = target["username"]
    matched = []
    for t in store.all_tweets_newest_first():
        if since_int is not None and int(t["id"]) <= since_int:
            continue
        if t["author_id"] == target["id"]:
            continue
        if mentions_username(t["text"], uname):
            matched.append(deepcopy(t))
            if len(matched) >= max_results:
                break

    if not matched:
        return {"meta": {"result_count": 0}}

    expansions = parse_expansions(query)
    res: dict = {
        "data": matched,
        "meta": {
            "result_count": len(matched),
            "newest_id": matched[0]["id"],
            "oldest_id": matched[-1]["id"],
        },
    }
    if expansions:
        res["includes"] = build_includes(store, matched, expansions, base_url)
    return res


def enrich_for_ui(store: Store, base_url: str) -> list[dict]:
    """Return all tweets newest-first with author details, media URLs, quoted/replied previews, and counts."""
    all_tweets = store.all_tweets_newest_first()
    reply_counts: dict[str, int] = {}
    quote_counts: dict[str, int] = {}
    for t in all_tweets:
        for ref in t.get("referenced_tweets", []):
            if ref["type"] == "replied_to":
                reply_counts[ref["id"]] = reply_counts.get(ref["id"], 0) + 1
            elif ref["type"] == "quoted":
                quote_counts[ref["id"]] = quote_counts.get(ref["id"], 0) + 1

    by_id = {t["id"]: t for t in all_tweets}

    def pack(t: dict, depth: int = 0) -> dict:
        u = store.user_by_id(t["author_id"]) or {"id": t["author_id"], "username": "unknown", "name": "Unknown"}
        media_items = []
        for mk in t.get("attachments", {}).get("media_keys", []):
            m = store.media_by_id_or_key(mk)
            if m:
                media_items.append(format_media(m, base_url))
        replied_to = None
        quoted = None
        for ref in t.get("referenced_tweets", []):
            rt = by_id.get(ref["id"])
            if rt and depth < 1:
                if ref["type"] == "replied_to":
                    replied_to = pack(rt, depth + 1)
                elif ref["type"] == "quoted":
                    quoted = pack(rt, depth + 1)
        return {
            "id": t["id"],
            "text": t["text"],
            "created_at": t["created_at"],
            "conversation_id": t["conversation_id"],
            "author": {
                "id": u["id"],
                "username": u["username"],
                "name": u["name"],
                "automated": bool(u.get("automated")),
                "bio": u.get("bio", ""),
            },
            "media": media_items,
            "replied_to": replied_to,
            "quoted": quoted,
            "reply_count": reply_counts.get(t["id"], 0),
            "quote_count": quote_counts.get(t["id"], 0),
        }

    return [pack(t) for t in all_tweets]
