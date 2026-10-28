"""Mention polling loop, clip extraction, verdict submission, and idempotent reply state."""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable

from bot.reply import MAX_TWEET_LEN, reply_text
from bot.xclient import XClient
from ingest.chain import write_atomic

REPO_ROOT = Path(__file__).resolve().parents[1]

OPT_OUT_REPLY = "Got it — I won't reply to you again."
NO_VIDEO_REPLY = "I check videos: reply to or quote a post with a video and tag me."
MENTION_TOKEN_RE = re.compile(r"@[A-Za-z0-9_]+")


def default_state_path() -> Path:
    return Path(os.environ.get("BOT_STATE_PATH", REPO_ROOT / "data" / "bot" / "state.json"))


def strip_mentions(text: str) -> str:
    """Remove all @username tokens and collapse surrounding whitespace."""
    return " ".join(MENTION_TOKEN_RE.sub(" ", text or "").split()).strip()


def is_opt_out_command(text: str) -> bool:
    cleaned = strip_mentions(text).lower().strip(".,!?:;\"'")
    return cleaned in ("stop", "unsubscribe")


def pick_best_mp4_variant(media_obj: dict) -> str | None:
    """Return the URL of the video/mp4 variant with the highest bit_rate."""
    if not isinstance(media_obj, dict) or media_obj.get("type") != "video":
        return None
    variants = media_obj.get("variants") or []
    mp4s = [
        v
        for v in variants
        if isinstance(v, dict) and v.get("content_type") == "video/mp4" and isinstance(v.get("url"), str) and v["url"]
    ]
    if not mp4s:
        return None
    mp4s.sort(key=lambda v: int(v.get("bit_rate") or 0), reverse=True)
    return mp4s[0]["url"]


def find_clip_url(mention: dict, includes: dict) -> str | None:
    """Locate the first video in: (a) mention's attachments, (b) replied_to tweet, (c) quoted tweet."""
    media_by_key: dict[str, dict] = {}
    for m in (includes or {}).get("media") or []:
        if isinstance(m, dict) and isinstance(m.get("media_key"), str):
            media_by_key[m["media_key"]] = m

    tweets_by_id: dict[str, dict] = {}
    for t in (includes or {}).get("tweets") or []:
        if isinstance(t, dict) and isinstance(t.get("id"), str):
            tweets_by_id[t["id"]] = t

    def _from_tweet(tw: dict | None) -> str | None:
        if not isinstance(tw, dict):
            return None
        keys = ((tw.get("attachments") or {}).get("media_keys")) or []
        for k in keys:
            url = pick_best_mp4_variant(media_by_key.get(k) or {})
            if url:
                return url
        return None

    # (a) Mention's own attachments
    own = _from_tweet(mention)
    if own:
        return own

    refs = mention.get("referenced_tweets") or []
    # (b) Tweet the mention replies to
    for r in refs:
        if isinstance(r, dict) and r.get("type") == "replied_to":
            url = _from_tweet(tweets_by_id.get(str(r.get("id") or "")))
            if url:
                return url

    # (c) Tweet the mention quotes
    for r in refs:
        if isinstance(r, dict) and r.get("type") == "quoted":
            url = _from_tweet(tweets_by_id.get(str(r.get("id") or "")))
            if url:
                return url

    return None


class VerdictClient:
    """HTTP client for the local Wholetake verdict service."""

    def __init__(
        self,
        base_url: str | None = None,
        timeout_s: float = 600.0,
        poll_interval_s: float = 0.5,
        sleep_fn: Callable[[float], None] = time.sleep,
        now_fn: Callable[[], float] = time.time,
    ):
        self.base_url = (base_url or os.environ.get("WT_VERDICT_URL") or "http://127.0.0.1:8790").rstrip("/")
        self.timeout_s = timeout_s
        self.poll_interval_s = poll_interval_s
        self._sleep = sleep_fn
        self._now = now_fn

    def check_url(self, clip_url: str) -> dict:
        req = urllib.request.Request(
            f"{self.base_url}/api/verdict",
            data=json.dumps({"url": clip_url}).encode("utf-8"),
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=15.0) as resp:
                sub = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            try:
                err_obj = json.loads(e.read().decode("utf-8", errors="replace"))
                reason = err_obj.get("error") or e.reason
            except Exception:
                reason = e.reason
            raise RuntimeError(str(reason)) from e
        except Exception as e:
            raise RuntimeError(str(e)) from e

        job_id = sub["id"]
        deadline = self._now() + self.timeout_s
        while True:
            greq = urllib.request.Request(
                f"{self.base_url}/api/verdict/{job_id}",
                headers={"Accept": "application/json"},
                method="GET",
            )
            try:
                with urllib.request.urlopen(greq, timeout=15.0) as resp:
                    job = json.loads(resp.read().decode("utf-8"))
            except Exception as e:
                raise RuntimeError(str(e)) from e

            st = job.get("status")
            if st == "done":
                res = dict(job.get("result") or {})
                res["job_id"] = job_id
                return res
            if st == "error":
                raise RuntimeError(str(job.get("error") or "verdict error"))
            if self._now() >= deadline:
                raise RuntimeError("verdict timed out")
            self._sleep(self.poll_interval_s)


def _short_reason(err: Exception | str) -> str:
    msg = " ".join(str(err).strip().splitlines()).strip()
    if not msg:
        msg = "processing failed"
    prefix = "Couldn't check this clip: "
    budget = MAX_TWEET_LEN - len(prefix)
    if len(msg) > budget:
        msg = msg[: budget - 1].rstrip() + "…"
    return f"{prefix}{msg}"


class BotRunner:
    """Polls mentions, enforces opt-out & idempotency rules, and replies once per mention."""

    def __init__(
        self,
        xclient: XClient,
        verdict_client: VerdictClient,
        state_path: Path | str | None = None,
        poll_s: float = 5.0,
        include_link: bool = False,
        page_base: str = "http://127.0.0.1:8790",
        sleep_fn: Callable[[float], None] = time.sleep,
    ):
        self.xclient = xclient
        self.verdict_client = verdict_client
        self.state_path = Path(state_path) if state_path is not None else default_state_path()
        self.poll_s = poll_s
        self.include_link = include_link
        self.page_base = page_base.rstrip("/")
        self._sleep = sleep_fn
        self._bot_user_id: str | None = None

        self.state = self._load_state()
        self._log_interrupted_postings()

    @classmethod
    def from_env(cls) -> "BotRunner":
        poll_s = float(os.environ.get("BOT_POLL_S", "5"))
        include_link = os.environ.get("BOT_INCLUDE_LINK", "0").strip() in ("1", "true", "yes")
        page_base = os.environ.get("WT_PAGE_BASE") or os.environ.get("WT_VERDICT_URL") or "http://127.0.0.1:8790"
        return cls(
            xclient=XClient(),
            verdict_client=VerdictClient(),
            state_path=default_state_path(),
            poll_s=poll_s,
            include_link=include_link,
            page_base=page_base,
        )

    def _load_state(self) -> dict:
        if self.state_path.exists():
            try:
                raw = json.loads(self.state_path.read_text())
                if isinstance(raw, dict):
                    raw.setdefault("since_id", None)
                    raw.setdefault("handled", {})
                    raw.setdefault("opt_outs", [])
                    return raw
            except Exception:
                pass
        return {"since_id": None, "handled": {}, "opt_outs": []}

    def _save_state(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        write_atomic(
            self.state_path,
            (json.dumps(self.state, indent=2, sort_keys=True) + "\n").encode("utf-8"),
        )

    def _log_interrupted_postings(self) -> None:
        # Mentions left in "posting" from an earlier crash are logged for human inspection and never re-posted.
        for mid, info in sorted((self.state.get("handled") or {}).items()):
            if isinstance(info, dict) and info.get("state") == "posting":
                print(
                    f"[bot] mention {mid} was left in state='posting' across restart; skipping to prevent double-reply",
                    file=sys.stderr,
                    flush=True,
                )

    def _ensure_bot_user(self) -> str:
        if not self._bot_user_id:
            me = self.xclient.get_me()
            self._bot_user_id = str(me["id"])
        return self._bot_user_id

    def _reply_once(self, mention_id: str, text: str) -> None:
        handled = self.state.setdefault("handled", {})
        if mention_id in handled:
            return
        # Persist "posting" atomically BEFORE sending the HTTP POST so a crash cannot double-reply.
        handled[mention_id] = {"state": "posting"}
        self._save_state()

        posted = self.xclient.post_tweet(text=text, in_reply_to_tweet_id=mention_id)
        handled[mention_id] = {"state": "done", "reply_id": str(posted["id"])}
        self._save_state()

    def _advance_since_id(self, mention_id: str) -> None:
        cur = self.state.get("since_id")
        if cur is None or int(mention_id) > int(cur):
            self.state["since_id"] = str(mention_id)
            self._save_state()

    def poll_once(self) -> int:
        bot_uid = self._ensure_bot_user()
        resp = self.xclient.get_mentions(bot_uid, since_id=self.state.get("since_id"))
        mentions = resp.get("data") or []
        includes = resp.get("includes") or {}
        if not mentions:
            return 0

        # Process mentions in ascending ID order so since_id advances monotonically.
        ordered = sorted(mentions, key=lambda t: int(t["id"]))
        count = 0
        for m in ordered:
            mid = str(m["id"])
            author_id = str(m.get("author_id") or "")
            handled = self.state.setdefault("handled", {})
            opt_outs: list[str] = self.state.setdefault("opt_outs", [])

            if mid in handled:
                self._advance_since_id(mid)
                continue

            if author_id == bot_uid:
                handled[mid] = {"state": "ignored_self"}
                self._advance_since_id(mid)
                continue

            if author_id in opt_outs:
                handled[mid] = {"state": "opted_out"}
                self._advance_since_id(mid)
                continue

            if is_opt_out_command(str(m.get("text") or "")):
                if author_id and author_id not in opt_outs:
                    opt_outs.append(author_id)
                    self._save_state()
                self._reply_once(mid, OPT_OUT_REPLY)
                self._advance_since_id(mid)
                count += 1
                continue

            clip_url = find_clip_url(m, includes)
            if not clip_url:
                self._reply_once(mid, NO_VIDEO_REPLY)
                self._advance_since_id(mid)
                count += 1
                continue

            try:
                verdict = self.verdict_client.check_url(clip_url)
                text = reply_text(
                    verdict,
                    include_link=self.include_link,
                    page_base=self.page_base,
                    job_id=verdict.get("job_id"),
                )
            except Exception as e:
                text = _short_reason(e)

            self._reply_once(mid, text)
            self._advance_since_id(mid)
            count += 1

        return count

    def run_forever(self) -> None:
        while True:
            try:
                self.poll_once()
            except KeyboardInterrupt:
                break
            except Exception as e:
                print(f"[bot] poll error: {e}", file=sys.stderr, flush=True)
            self._sleep(self.poll_s)
