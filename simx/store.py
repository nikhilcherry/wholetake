"""Thread-safe in-memory store for simulated X, persisted atomically to state.json."""
import json
import os
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

SEED_USERS = [
    {"id": "1001", "username": "nikhil", "name": "Nikhil AV", "automated": False,
     "bio": "Building Wholetake · HH Goa 2026"},
    {"id": "1002", "username": "shreyas", "name": "Shreyas R", "automated": False,
     "bio": "Building Wholetake · HH Goa 2026"},
    {"id": "1003", "username": "clipper", "name": "ClipFarm 🔥", "automated": False,
     "bio": "Breaking clips 24/7. Fastest cuts on the timeline."},
    {"id": "1004", "username": "newsdesk", "name": "India Newsdesk", "automated": False,
     "bio": "Live updates, press conferences, and public broadcasts."},
    {"id": "1005", "username": "wholetake", "name": "Wholetake", "automated": True,
     "bio": "Automated · checks clips against witnessed livestreams · operated by @nikhil · reply 'stop' to opt out"},
]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def write_atomic(path: Path, data: bytes):
    tmp = path.with_suffix(path.suffix + f".{os.getpid()}.{threading.get_ident()}.tmp")
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def probe_duration_ms(mp4_path: Path) -> int:
    """Best-effort duration via ffprobe; falls back to 10000ms for synthetic test payloads."""
    try:
        out = subprocess.check_output(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(mp4_path)],
            stderr=subprocess.DEVNULL, timeout=5,
        ).decode().strip()
        return max(1, int(round(float(out) * 1000)))
    except Exception:
        return 10000


class Store:
    def __init__(self, data_dir: str | Path = "data/simx"):
        self.root = Path(data_dir)
        self.media_dir = self.root / "media"
        self.state_path = self.root / "state.json"
        self.root.mkdir(parents=True, exist_ok=True)
        self.media_dir.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.users: dict[str, dict] = {}
        self.tweets: dict[str, dict] = {}
        self.media: dict[str, dict] = {}
        self.next_id: int = 1700000000000
        self._load_or_seed()

    def _load_or_seed(self):
        with self.lock:
            if self.state_path.exists():
                raw = json.loads(self.state_path.read_text())
                self.users = raw.get("users", {})
                self.tweets = raw.get("tweets", {})
                self.media = raw.get("media", {})
                self.next_id = int(raw.get("next_id", 1700000000000))
                for u in SEED_USERS:
                    self.users.setdefault(u["id"], dict(u))
            else:
                self.users = {u["id"]: dict(u) for u in SEED_USERS}
                self._save_locked()

    def _alloc_id_locked(self) -> str:
        now_ms = int(time.time() * 1000)
        self.next_id = max(self.next_id + 1, now_ms * 1000)
        return str(self.next_id)

    def _save_locked(self):
        payload = {
            "next_id": self.next_id,
            "users": self.users,
            "tweets": self.tweets,
            "media": self.media,
        }
        write_atomic(self.state_path, json.dumps(payload, indent=1).encode("utf-8"))

    def user_by_token(self, token: str | None) -> dict | None:
        if not token or not token.startswith("sim-"):
            return None
        uname = token[4:].strip().lower()
        return self.user_by_username(uname)

    def user_by_username(self, username: str) -> dict | None:
        uname = username.strip().lstrip("@").lower()
        with self.lock:
            for u in self.users.values():
                if u["username"].lower() == uname:
                    return dict(u)
        return None

    def user_by_id(self, uid: str) -> dict | None:
        with self.lock:
            u = self.users.get(str(uid))
            return dict(u) if u else None

    def list_users(self) -> list[dict]:
        with self.lock:
            return [dict(u) for u in sorted(self.users.values(), key=lambda x: int(x["id"]))]

    def save_media(self, raw_bytes: bytes, duration_ms: int | None = None) -> dict:
        with self.lock:
            mid = self._alloc_id_locked()
            mkey = f"7_{mid}"
            mp4_path = self.media_dir / f"{mkey}.mp4"
            write_atomic(mp4_path, raw_bytes)
            dur = duration_ms if duration_ms is not None else probe_duration_ms(mp4_path)
            bit_rate = max(128000, int(len(raw_bytes) * 8 * 1000 / max(dur, 1000)))
            m = {
                "id": mid,
                "media_key": mkey,
                "type": "video",
                "duration_ms": dur,
                "size_bytes": len(raw_bytes),
                "bit_rate": bit_rate,
            }
            self.media[mkey] = m
            self._save_locked()
            return dict(m)

    def media_by_id_or_key(self, val: str) -> dict | None:
        with self.lock:
            if val in self.media:
                return dict(self.media[val])
            for m in self.media.values():
                if m["id"] == val or m["media_key"] == val:
                    return dict(m)
        return None

    def media_path(self, media_key: str) -> Path | None:
        safe = os.path.basename(media_key)
        if safe.endswith(".mp4"):
            safe = safe[:-4]
        with self.lock:
            if safe not in self.media:
                return None
            p = self.media_dir / f"{safe}.mp4"
            return p if p.exists() else None

    def create_tweet(
        self,
        author_id: str,
        text: str,
        in_reply_to_id: str | None = None,
        quote_tweet_id: str | None = None,
        media_ids: list[str] | None = None,
    ) -> dict:
        with self.lock:
            if author_id not in self.users:
                raise ValueError("unknown author_id")
            tid = self._alloc_id_locked()
            conv_id = tid
            refs = []
            if in_reply_to_id:
                parent = self.tweets.get(str(in_reply_to_id))
                if not parent:
                    raise KeyError(f"replied-to tweet {in_reply_to_id} not found")
                conv_id = parent["conversation_id"]
                refs.append({"type": "replied_to", "id": str(in_reply_to_id)})
            if quote_tweet_id:
                quoted = self.tweets.get(str(quote_tweet_id))
                if not quoted:
                    raise KeyError(f"quoted tweet {quote_tweet_id} not found")
                refs.append({"type": "quoted", "id": str(quote_tweet_id)})

            mkeys = []
            for mid in media_ids or []:
                m = self.media_by_id_or_key(str(mid))
                if not m:
                    raise KeyError(f"media {mid} not found")
                mkeys.append(m["media_key"])

            tw = {
                "id": tid,
                "text": text,
                "author_id": str(author_id),
                "created_at": utc_now(),
                "conversation_id": conv_id,
            }
            if refs:
                tw["referenced_tweets"] = refs
            if mkeys:
                tw["attachments"] = {"media_keys": mkeys}

            self.tweets[tid] = tw
            self._save_locked()
            return dict(tw)

    def get_tweet(self, tid: str) -> dict | None:
        with self.lock:
            t = self.tweets.get(str(tid))
            return dict(t) if t else None

    def all_tweets_newest_first(self) -> list[dict]:
        with self.lock:
            return [dict(t) for t in sorted(self.tweets.values(), key=lambda x: int(x["id"]), reverse=True)]
