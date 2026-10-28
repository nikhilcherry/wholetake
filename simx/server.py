"""ThreadingHTTPServer serving the X API v2 contract, HTTP Range media, and the projector UI."""
import cgi
import json
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from simx import api
from simx.store import Store

UI_PATH = Path(__file__).with_name("ui.html")


class SimXServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, server_address, store: Store, rate_limit: int = api.RATE_LIMIT_MAX):
        super().__init__(server_address, SimXHandler)
        self.store = store
        self.limiter = api.RateLimiter(limit=rate_limit)


class SimXHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        if os.environ.get("SIMX_QUIET") == "1":
            return
        super().log_message(fmt, *args)

    def _base_url(self) -> str:
        host = self.headers.get("Host") or f"{self.server.server_address[0]}:{self.server.server_address[1]}"
        return f"http://{host}"

    def _send_json(self, status: int, payload: dict, extra_headers: dict | None = None):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra_headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, status: int, html_bytes: bytes):
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(html_bytes)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(html_bytes)

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(length) if length > 0 else b""

    def _authenticate(self) -> tuple[dict, dict[str, str]]:
        auth = self.headers.get("Authorization") or ""
        if not auth.startswith("Bearer "):
            raise api.APIError(401, "Unauthorized", "Missing or invalid Authorization Bearer header.")
        token = auth[7:].strip()
        user = self.server.store.user_by_token(token)
        if not user:
            raise api.APIError(401, "Unauthorized", "Invalid bearer token.")
        rl_headers = self.server.limiter.check(token)
        return user, rl_headers

    def _serve_media(self, key_part: str):
        p = self.server.store.media_path(key_part)
        if not p:
            self._send_json(404, {"title": "Not Found Error", "detail": "Media not found", "status": 404})
            return
        size = p.stat().st_size
        range_hdr = self.headers.get("Range")
        if range_hdr:
            m = re.match(r"^bytes=(\d*)-(\d*)$", range_hdr.strip())
            if not m:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            start_s, end_s = m.group(1), m.group(2)
            if start_s == "" and end_s == "":
                start, end = 0, size - 1
            elif start_s == "":
                suffix = int(end_s)
                start = max(0, size - suffix)
                end = size - 1
            else:
                start = int(start_s)
                end = int(end_s) if end_s != "" else size - 1
            if start >= size or start > end:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            end = min(end, size - 1)
            length = end - start + 1
            with open(p, "rb") as f:
                f.seek(start)
                chunk = f.read(length)
            self.send_response(206)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.send_header("Content-Length", str(len(chunk)))
            self.end_headers()
            self.wfile.write(chunk)
            return

        data = p.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)
        base_url = self._base_url()

        if path == "/favicon.ico":  # browsers ask on every load; a 401 here shows up as a console error
            self.send_response(204)
            self.end_headers()
            return

        if path == "/" or path.startswith("/status/"):
            self._send_html(200, UI_PATH.read_bytes())
            return

        if path.startswith("/media/"):
            self._serve_media(path[len("/media/"):])
            return

        # UI endpoints under /sim/
        if path == "/sim/users":
            self._send_json(200, {"users": self.server.store.list_users()})
            return

        if path == "/sim/timeline":
            items = api.enrich_for_ui(self.server.store, base_url)
            self._send_json(200, {"tweets": items, "users": self.server.store.list_users()})
            return

        if path.startswith("/sim/thread/"):
            tid = path[len("/sim/thread/"):].strip("/")
            all_items = api.enrich_for_ui(self.server.store, base_url)
            by_id = {x["id"]: x for x in all_items}
            focus = by_id.get(tid)
            if not focus:
                self._send_json(404, {"error": "Tweet not found"})
                return
            conv_id = focus["conversation_id"]
            thread = [x for x in reversed(all_items) if x["conversation_id"] == conv_id or x["id"] == conv_id]
            self._send_json(200, {"focus": focus, "thread": thread})
            return

        # X API v2 contract endpoints
        rl_headers: dict[str, str] = {}
        try:
            caller, rl_headers = self._authenticate()
            if path == "/2/users/me":
                self._send_json(200, api.get_users_me(self.server.store, caller), rl_headers)
                return
            m = re.match(r"^/2/users/by/username/([^/]+)$", path)
            if m:
                self._send_json(200, api.get_user_by_username(self.server.store, m.group(1)), rl_headers)
                return
            m = re.match(r"^/2/tweets/([^/]+)$", path)
            if m:
                self._send_json(200, api.get_tweet_by_id(self.server.store, m.group(1), query, base_url), rl_headers)
                return
            m = re.match(r"^/2/users/([^/]+)/mentions$", path)
            if m:
                self._send_json(200, api.get_user_mentions(self.server.store, m.group(1), query, base_url), rl_headers)
                return
            raise api.APIError(404, "Not Found Error", f"Unknown endpoint {path}")
        except api.APIError as e:
            hdrs = {**rl_headers, **e.headers}
            self._send_json(e.status, e.to_dict(), hdrs)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/sim/post":
            self._handle_sim_post()
            return

        rl_headers: dict[str, str] = {}
        try:
            caller, rl_headers = self._authenticate()
            if path == "/2/media/upload":
                raw = self._read_body()
                if not raw:
                    raise api.APIError(400, "Invalid Request", "Empty media upload body.")
                m = self.server.store.save_media(raw)
                self._send_json(200, {"data": {"id": m["id"], "media_key": m["media_key"]}}, rl_headers)
                return
            if path == "/2/tweets":
                raw = self._read_body()
                try:
                    body = json.loads(raw.decode("utf-8")) if raw else {}
                except Exception:
                    raise api.APIError(400, "Invalid Request", "Malformed JSON body.")
                res = api.post_tweet(self.server.store, caller, body)
                self._send_json(201, res, rl_headers)
                return
            raise api.APIError(404, "Not Found Error", f"Unknown endpoint {path}")
        except api.APIError as e:
            hdrs = {**rl_headers, **e.headers}
            self._send_json(e.status, e.to_dict(), hdrs)

    def _handle_sim_post(self):
        ctype = self.headers.get("Content-Type") or ""
        try:
            username = "nikhil"
            text = ""
            reply_to = None
            quote_id = None
            media_ids = []

            if ctype.startswith("multipart/form-data"):
                env = {"REQUEST_METHOD": "POST", "CONTENT_TYPE": ctype,
                       "CONTENT_LENGTH": self.headers.get("Content-Length", "0")}
                form = cgi.FieldStorage(fp=self.rfile, headers=self.headers, environ=env)
                username = (form.getfirst("username") or "nikhil").strip()
                text = form.getfirst("text") or ""
                reply_to = form.getfirst("in_reply_to_tweet_id") or None
                quote_id = form.getfirst("quote_tweet_id") or None
                if "video" in form and getattr(form["video"], "file", None):
                    vbytes = form["video"].file.read()
                    if vbytes:
                        m = self.server.store.save_media(vbytes)
                        media_ids.append(m["id"])
            else:
                raw = self._read_body()
                data = json.loads(raw.decode("utf-8")) if raw else {}
                username = (data.get("username") or "nikhil").strip()
                text = data.get("text") or ""
                reply_to = data.get("in_reply_to_tweet_id") or None
                quote_id = data.get("quote_tweet_id") or None
                if data.get("media_id"):
                    media_ids.append(str(data["media_id"]))

            user = self.server.store.user_by_username(username)
            if not user:
                raise api.APIError(400, "Invalid Request", f"Unknown user {username}")
            body: dict = {"text": text}
            if reply_to:
                body["reply"] = {"in_reply_to_tweet_id": str(reply_to)}
            if quote_id:
                body["quote_tweet_id"] = str(quote_id)
            if media_ids:
                body["media"] = {"media_ids": media_ids}
            res = api.post_tweet(self.server.store, user, body)
            self._send_json(201, res)
        except api.APIError as e:
            self._send_json(e.status, e.to_dict())
