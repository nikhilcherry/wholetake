"""Minimal X API v2 client for the Wholetake bot contract."""
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable

MENTION_EXPANSIONS = (
    "author_id,referenced_tweets.id,attachments.media_keys,referenced_tweets.id.attachments.media_keys"
)
MEDIA_FIELDS = "type,duration_ms,variants"


class XError(RuntimeError):
    """Raised when the X API returns an HTTP error."""

    def __init__(self, status: int, title: str, detail: str, headers: dict[str, str] | None = None):
        super().__init__(f"X API {status} {title}: {detail}")
        self.status = status
        self.title = title
        self.detail = detail
        self.headers = headers or {}


class XClient:
    """Thin urllib wrapper around the X v2 endpoints used by the reply bot."""

    def __init__(
        self,
        base_url: str | None = None,
        token: str | None = None,
        timeout: float = 15.0,
        sleep_fn: Callable[[float], None] = time.sleep,
        now_fn: Callable[[], float] = time.time,
    ):
        self.base_url = (base_url or os.environ.get("X_API_BASE") or "http://127.0.0.1:8791").rstrip("/")
        self.token = token if token is not None else os.environ.get("X_BOT_TOKEN", "")
        self.timeout = timeout
        self._sleep = sleep_fn
        self._now = now_fn

    def _request(
        self,
        method: str,
        path: str,
        query: dict[str, str] | None = None,
        body: dict | None = None,
    ) -> dict:
        url = f"{self.base_url}{path}"
        if query:
            qs = urllib.parse.urlencode({k: v for k, v in query.items() if v is not None and v != ""})
            if qs:
                url = f"{url}?{qs}"

        data = json.dumps(body).encode("utf-8") if body is not None else None
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json",
        }
        if data is not None:
            headers["Content-Type"] = "application/json"

        while True:
            req = urllib.request.Request(url, data=data, headers=headers, method=method)
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    raw = resp.read().decode("utf-8")
                    return json.loads(raw) if raw else {}
            except urllib.error.HTTPError as e:
                hdrs = {k.lower(): v for k, v in (e.headers.items() if e.headers else [])}
                raw_err = ""
                try:
                    raw_err = e.read().decode("utf-8", errors="replace")
                    err_obj = json.loads(raw_err)
                except Exception:
                    err_obj = {}

                if e.code == 429:
                    # Respect x-rate-limit-reset when throttled so we never burn retries in a tight loop.
                    reset_str = hdrs.get("x-rate-limit-reset")
                    wait_s = 1.0
                    if reset_str:
                        try:
                            wait_s = max(0.0, float(reset_str) - self._now())
                        except ValueError:
                            wait_s = 1.0
                    self._sleep(wait_s)
                    continue

                title = str(err_obj.get("title") or e.reason or "Error")
                detail = str(err_obj.get("detail") or raw_err or e.reason or "")
                raise XError(e.code, title, detail, headers=hdrs) from e

    def get_me(self) -> dict:
        return self._request("GET", "/2/users/me")["data"]

    def get_user_by_username(self, username: str) -> dict:
        return self._request("GET", f"/2/users/by/username/{urllib.parse.quote(username)}")["data"]

    def get_tweet(self, tweet_id: str) -> dict:
        return self._request(
            "GET",
            f"/2/tweets/{urllib.parse.quote(tweet_id)}",
            query={
                "expansions": MENTION_EXPANSIONS,
                "media.fields": MEDIA_FIELDS,
            },
        )

    def get_mentions(
        self,
        user_id: str,
        since_id: str | None = None,
        max_results: int = 100,
    ) -> dict:
        q: dict[str, str] = {
            "max_results": str(max(5, min(100, int(max_results)))),
            "expansions": MENTION_EXPANSIONS,
            "media.fields": MEDIA_FIELDS,
        }
        if since_id:
            q["since_id"] = str(since_id)
        return self._request("GET", f"/2/users/{urllib.parse.quote(user_id)}/mentions", query=q)

    def post_tweet(
        self,
        text: str,
        in_reply_to_tweet_id: str | None = None,
        quote_tweet_id: str | None = None,
        media_ids: list[str] | None = None,
    ) -> dict:
        body: dict = {"text": text}
        if in_reply_to_tweet_id:
            body["reply"] = {"in_reply_to_tweet_id": str(in_reply_to_tweet_id)}
        if quote_tweet_id:
            body["quote_tweet_id"] = str(quote_tweet_id)
        if media_ids:
            body["media"] = {"media_ids": list(media_ids)}
        return self._request("POST", "/2/tweets", body=body)["data"]
