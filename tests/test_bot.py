"""Tests for the Wholetake X reply bot, verdict page, and SSRF allowlist."""
import json
import threading
import urllib.parse
import urllib.request
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from bot.reply import MAX_TWEET_LEN, reply_text
from bot.runner import NO_VIDEO_REPLY, OPT_OUT_REPLY, BotRunner, VerdictClient
from bot.xclient import XClient
from verdict.fetch import SSRFError, validate_public_url
from verdict.service import VerdictManager, make_handler


class FakeXState:
    def __init__(self):
        self.bot_user = {"id": "100", "username": "wholetake", "name": "Wholetake"}
        self.users = {
            "100": self.bot_user,
            "200": {"id": "200", "username": "alice", "name": "Alice"},
            "201": {"id": "201", "username": "bob", "name": "Bob"},
        }
        self.tweets: dict[str, dict] = {}
        self.media: dict[str, dict] = {}
        self.posted: list[dict] = []
        self.next_id = 1000
        self.rate_limit_next = False

    def add_media(self, media_key: str, variants: list[dict], mtype: str = "video") -> None:
        self.media[media_key] = {
            "media_key": media_key,
            "type": mtype,
            "duration_ms": 12000,
            "variants": variants,
        }

    def add_tweet(
        self,
        text: str,
        author_id: str = "200",
        replied_to: str | None = None,
        quoted: str | None = None,
        media_keys: list[str] | None = None,
    ) -> dict:
        tid = str(self.next_id)
        self.next_id += 1
        tw: dict = {
            "id": tid,
            "text": text,
            "author_id": author_id,
            "created_at": "2026-10-28T18:55:00Z",
            "conversation_id": tid,
        }
        refs = []
        if replied_to:
            refs.append({"type": "replied_to", "id": str(replied_to)})
        if quoted:
            refs.append({"type": "quoted", "id": str(quoted)})
        if refs:
            tw["referenced_tweets"] = refs
        if media_keys:
            tw["attachments"] = {"media_keys": list(media_keys)}
        self.tweets[tid] = tw
        return tw


def start_fake_x(state: FakeXState) -> tuple[ThreadingHTTPServer, str]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args) -> None:
            return

        def _json(self, code: int, obj: dict, headers: dict[str, str] | None = None) -> None:
            raw = json.dumps(obj).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(raw)

        def _check_auth(self) -> bool:
            auth = self.headers.get("Authorization", "")
            if auth != "Bearer sim-wholetake":
                self._json(401, {"title": "Unauthorized", "detail": "bad token", "status": 401})
                return False
            if state.rate_limit_next:
                state.rate_limit_next = False
                self._json(
                    429,
                    {"title": "Too Many Requests", "detail": "rate limit", "status": 429},
                    headers={"x-rate-limit-reset": "105"},
                )
                return False
            return True

        def do_GET(self) -> None:
            if not self._check_auth():
                return
            parsed = urllib.parse.urlsplit(self.path)
            path = parsed.path
            qs = urllib.parse.parse_qs(parsed.query)

            if path == "/2/users/me":
                self._json(200, {"data": state.bot_user})
                return

            if path.startswith("/2/users/") and path.endswith("/mentions"):
                uid = path.split("/2/users/", 1)[1].rsplit("/mentions", 1)[0]
                bot_uname = state.users[uid]["username"].lower()
                since_id = qs.get("since_id", [None])[0]
                mentions = []
                for tw in state.tweets.values():
                    if tw["author_id"] == uid:
                        continue
                    if f"@{bot_uname}" not in tw["text"].lower():
                        continue
                    if since_id and int(tw["id"]) <= int(since_id):
                        continue
                    mentions.append(tw)
                mentions.sort(key=lambda t: int(t["id"]), reverse=True)
                if not mentions:
                    self._json(200, {"meta": {"result_count": 0}})
                    return

                inc_tweets = []
                inc_media = []
                seen_tw = set()
                seen_m = set()
                for m in mentions:
                    for mk in (m.get("attachments") or {}).get("media_keys") or []:
                        if mk in state.media and mk not in seen_m:
                            inc_media.append(state.media[mk])
                            seen_m.add(mk)
                    for r in m.get("referenced_tweets") or []:
                        rt = state.tweets.get(r["id"])
                        if rt and rt["id"] not in seen_tw:
                            inc_tweets.append(rt)
                            seen_tw.add(rt["id"])
                            for mk in (rt.get("attachments") or {}).get("media_keys") or []:
                                if mk in state.media and mk not in seen_m:
                                    inc_media.append(state.media[mk])
                                    seen_m.add(mk)

                self._json(
                    200,
                    {
                        "data": mentions,
                        "includes": {
                            "users": list(state.users.values()),
                            "tweets": inc_tweets,
                            "media": inc_media,
                        },
                        "meta": {
                            "result_count": len(mentions),
                            "newest_id": mentions[0]["id"],
                            "oldest_id": mentions[-1]["id"],
                        },
                    },
                )
                return

            self._json(404, {"title": "Not Found", "detail": path, "status": 404})

        def do_POST(self) -> None:
            if not self._check_auth():
                return
            if self.path != "/2/tweets":
                self._json(404, {"title": "Not Found", "detail": self.path, "status": 404})
                return
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length).decode("utf-8"))
            text = body.get("text", "")
            if len(text) > 280:
                self._json(400, {"title": "Invalid Request", "detail": "text > 280", "status": 400})
                return
            reply_to = (body.get("reply") or {}).get("in_reply_to_tweet_id")
            tw = state.add_tweet(text=text, author_id="100", replied_to=reply_to)
            state.posted.append({"id": tw["id"], "text": text, "in_reply_to_tweet_id": reply_to, "raw": body})
            self._json(201, {"data": {"id": tw["id"], "text": text}})

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


def _sample_cut_verdict(quote_words: str = "gas prices are through the roof, my rent is up", anchored: bool = False, det_kind: str = "full") -> dict:
    anchor = {"tx": "0xabc1234567890def", "head_seq": 41, "head_hash": "deadbeef12345678"} if anchored else None
    return {
        "verdict_id": "v1234567890abcdef",
        "job_id": "job999",
        "headline": f'0.9 s removed at 0:07: the record says "… {quote_words} …"',
        "detector_kind": det_kind,
        "clip": {"sha256": "00" * 32, "duration_s": 20.0, "source_url": "http://example.com/c.mp4", "title": "clip"},
        "detection": {
            "detector": {"name": "wt-detect", "version": "0.1"},
            "clip": {"duration_s": 20.0, "sha256": "00" * 32},
            "witness": {
                "verdict": "cut",
                "stream": "aje",
                "pieces": [
                    {"clip_s": [0.0, 7.0], "seq": 41, "src_s": [10.0, 17.0], "audio_score": 0.95},
                    {"clip_s": [7.0, 20.0], "seq": 41, "src_s": [17.9, 30.9], "audio_score": 0.94},
                ],
                "removed": [
                    {"after_clip_s": 7.0, "seq": 41, "src_s": [17.0, 17.9], "words": quote_words}
                ],
                "inserted": [],
                "transcript_clip": "hello world",
            },
        },
        "label": {"effect": "context omitted", "why": "GEMINI_SECRET_ADVISORY_NOTE"},
        "proofs": [
            {
                "seq": 41,
                "chunk_received_utc": "2026-10-28T18:54:00Z",
                "proof_ok": True,
                "anchor": anchor,
            }
        ],
        "coverage": {"complete": True, "gaps": [], "notes": []},
    }


def test_reply_text_shapes_truncation_and_link():
    v = _sample_cut_verdict()
    txt_no_link = reply_text(v, include_link=False, page_base="http://127.0.0.1:8790")
    assert txt_no_link == (
        "✂️ Cut — 0.9 s removed at 0:07. The record says: "
        "“…gas prices are through the roof, my rent is up…” (aje · 28 Oct 18:54 UTC)"
    )
    assert "http://" not in txt_no_link
    assert "GEMINI_SECRET_ADVISORY_NOTE" not in txt_no_link

    # With link + anchored + text-only
    v_full = _sample_cut_verdict(anchored=True, det_kind="text-only")
    txt_link = reply_text(v_full, include_link=True, page_base="http://127.0.0.1:8790")
    assert " · anchored on Base" in txt_link
    assert " · text match only" in txt_link
    assert txt_link.endswith(" http://127.0.0.1:8790/v/job999")
    assert len(txt_link) <= MAX_TWEET_LEN

    # Very long quote truncates the quote first, preserving verdict word, cut duration, and link
    long_quote = " ".join(["very_important_context_word"] * 40)
    v_long = _sample_cut_verdict(quote_words=long_quote, anchored=True, det_kind="text-only")
    txt_long = reply_text(v_long, include_link=True, page_base="http://127.0.0.1:8790")
    assert len(txt_long) <= MAX_TWEET_LEN
    assert txt_long.startswith("✂️ Cut — 0.9 s removed at 0:07. The record says: “…")
    assert txt_long.endswith(" http://127.0.0.1:8790/v/job999")


def test_bot_mentions_replied_quoted_own_novideo_optout_and_restart(tmp_path: Path):
    xstate = FakeXState()
    xstate.add_media(
        "m_replied",
        [
            {"content_type": "video/mp4", "bit_rate": 400000, "url": "http://127.0.0.1:8791/low.mp4"},
            {"content_type": "video/mp4", "bit_rate": 1200000, "url": "http://127.0.0.1:8791/high_replied.mp4"},
        ],
    )
    xstate.add_media(
        "m_quoted",
        [{"content_type": "video/mp4", "bit_rate": 800000, "url": "http://127.0.0.1:8791/quoted.mp4"}],
    )
    xstate.add_media(
        "m_own",
        [{"content_type": "video/mp4", "bit_rate": 900000, "url": "http://127.0.0.1:8791/own.mp4"}],
    )

    xsrv, xbase = start_fake_x(xstate)
    try:
        checked_urls: list[str] = []

        class FakeVerdictClient(VerdictClient):
            def check_url(self, clip_url: str) -> dict:
                checked_urls.append(clip_url)
                return _sample_cut_verdict()

        state_file = tmp_path / "bot_state.json"

        def make_runner() -> BotRunner:
            return BotRunner(
                xclient=XClient(base_url=xbase, token="sim-wholetake"),
                verdict_client=FakeVerdictClient(),
                state_path=state_file,
                include_link=True,
                page_base="http://127.0.0.1:8790",
            )

        # 1. Mention replying to a video tweet -> picks highest bitrate mp4 and replies to the mention
        src_tw = xstate.add_tweet("Watch this clip", author_id="201", media_keys=["m_replied"])
        m1 = xstate.add_tweet("@wholetake is this real?", author_id="200", replied_to=src_tw["id"])

        # 2. Mention quoting a video tweet
        q_tw = xstate.add_tweet("Another clip", author_id="201", media_keys=["m_quoted"])
        m2 = xstate.add_tweet("check @Wholetake", author_id="200", quoted=q_tw["id"])

        # 3. Mention with its own video attachment
        m3 = xstate.add_tweet("@wholetake check my upload", author_id="200", media_keys=["m_own"])

        # 4. Mention with no video anywhere
        m4 = xstate.add_tweet("hey @wholetake what do you do?", author_id="200")

        runner = make_runner()
        assert runner.poll_once() == 4
        assert checked_urls == [
            "http://127.0.0.1:8791/high_replied.mp4",
            "http://127.0.0.1:8791/quoted.mp4",
            "http://127.0.0.1:8791/own.mp4",
        ]
        assert len(xstate.posted) == 4
        assert xstate.posted[0]["in_reply_to_tweet_id"] == m1["id"]
        assert xstate.posted[0]["text"].startswith("✂️ Cut — 0.9 s removed at 0:07.")
        assert xstate.posted[1]["in_reply_to_tweet_id"] == m2["id"]
        assert xstate.posted[2]["in_reply_to_tweet_id"] == m3["id"]
        assert xstate.posted[3]["in_reply_to_tweet_id"] == m4["id"]
        assert xstate.posted[3]["text"] == NO_VIDEO_REPLY
        assert runner.state["since_id"] == m4["id"]

        # Re-polling or restarting does not re-reply
        assert runner.poll_once() == 0
        runner2 = make_runner()
        assert runner2.poll_once() == 0
        assert len(xstate.posted) == 4

        # 5. Opt-out flow: Bob says "@wholetake STOP", gets one confirmation, then future mentions are ignored
        m_stop = xstate.add_tweet("@wholetake STOP", author_id="201")
        assert runner2.poll_once() == 1
        assert xstate.posted[-1]["in_reply_to_tweet_id"] == m_stop["id"]
        assert xstate.posted[-1]["text"] == OPT_OUT_REPLY

        m_after_stop = xstate.add_tweet("@wholetake check this", author_id="201", media_keys=["m_own"])
        assert runner2.poll_once() == 0
        assert len(xstate.posted) == 5
        assert runner2.state["since_id"] == m_after_stop["id"]

        # 6. Simulated crash leaving a mention in "posting": restart logs & never double-replies
        m_crash = xstate.add_tweet("@wholetake crash test", author_id="200", media_keys=["m_own"])
        saved = json.loads(state_file.read_text())
        saved["handled"][m_crash["id"]] = {"state": "posting"}
        state_file.write_text(json.dumps(saved))

        runner3 = make_runner()
        assert runner3.poll_once() == 0
        assert len(xstate.posted) == 5
    finally:
        xsrv.shutdown()
        xsrv.server_close()


def test_xclient_429_rate_limit_sleep():
    xstate = FakeXState()
    xstate.rate_limit_next = True
    xsrv, xbase = start_fake_x(xstate)
    slept: list[float] = []
    try:
        cli = XClient(
            base_url=xbase,
            token="sim-wholetake",
            sleep_fn=lambda s: slept.append(s),
            now_fn=lambda: 100.0,
        )
        me = cli.get_me()
        assert me["username"] == "wholetake"
        assert slept == [5.0]
    finally:
        xsrv.shutdown()
        xsrv.server_close()


def _write_dummy_wav(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(b"\x00\x00" * 16000)


def test_verdict_page_escaping_and_routes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("WT_FETCH_ALLOW_HOSTS", "127.0.0.1:8791")
    wroot = tmp_path / "witness"
    vdir = tmp_path / "verdicts"
    sdir = wroot / "aje"
    sdir.mkdir(parents=True)

    # Chunk transcript containing malicious <script> payload
    (sdir / "000041.json").write_text(
        json.dumps(
            {
                "received_utc": "2026-10-28T18:54:00Z",
                "words": [
                    {"w": "safe", "s": 16.5, "e": 16.9},
                    {"w": "<script>alert(1)</script>", "s": 17.1, "e": 17.8},
                    {"w": "after", "s": 18.0, "e": 18.5},
                ],
            }
        )
    )
    wav_file = tmp_path / "clip.wav"
    _write_dummy_wav(wav_file)

    mgr = VerdictManager(witness_root=wroot, verdicts_dir=vdir, start_worker=False)
    sub = mgr.submit("http://127.0.0.1:8791/media/clip.mp4")
    jid = sub["id"]

    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(mgr))
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        # 1. Queued job page renders auto-refresh
        with urllib.request.urlopen(f"{base}/v/{jid}") as resp:
            queued_html = resp.read().decode("utf-8")
        assert 'http-equiv="refresh" content="2"' in queued_html
        assert "queued" in queued_html

        # 2. Done job page escapes <script> in transcript, headline, and advisory note
        malicious_verdict = _sample_cut_verdict(
            quote_words="<script>alert(1)</script>",
            anchored=True,
            det_kind="text-only",
        )
        malicious_verdict["label"] = {"effect": "<b>cut</b>", "why": "<script>evil()</script>"}
        mgr._set_status(jid, "done", clip_wav=str(wav_file), result=malicious_verdict, error=None)

        with urllib.request.urlopen(f"{base}/v/{jid}") as resp:
            done_html = resp.read().decode("utf-8")
        assert "<script>alert(1)</script>" not in done_html
        assert "<script>evil()</script>" not in done_html
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in done_html
        assert "sepolia.basescan.org/tx/0xabc1234567890def" in done_html
        assert "text match only" in done_html

        # 3. /v/<id>/clip.wav and /v/<id>/verdict.json
        with urllib.request.urlopen(f"{base}/v/{jid}/clip.wav") as resp:
            assert resp.headers.get("Content-Type") == "audio/wav"
            assert len(resp.read()) > 44

        with urllib.request.urlopen(f"{base}/v/{jid}/verdict.json") as resp:
            vjson = json.loads(resp.read().decode("utf-8"))
            assert vjson["verdict_id"] == malicious_verdict["verdict_id"]

        # 4. Error job page escapes error text
        mgr._set_status(jid, "error", error="<img src=x onerror=alert(1)>")
        with urllib.request.urlopen(f"{base}/v/{jid}") as resp:
            err_html = resp.read().decode("utf-8")
        assert "<img src=x" not in err_html
        assert "&lt;img src=x onerror=alert(1)&gt;" in err_html
    finally:
        srv.shutdown()
        srv.server_close()
        mgr.close()


def test_fetch_allowlist_exempts_only_exact_host_port(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("WT_FETCH_ALLOW_HOSTS", "127.0.0.1:8791, localhost:9999")
    # Allowed exact host:port passes
    validate_public_url("http://127.0.0.1:8791/video.mp4")
    validate_public_url("http://localhost:9999/clip.mp4")

    # Different port or host on loopback/private IP stays blocked
    with pytest.raises(SSRFError):
        validate_public_url("http://127.0.0.1:8790/video.mp4")
    with pytest.raises(SSRFError):
        validate_public_url("http://127.0.0.1/video.mp4")
    with pytest.raises(SSRFError):
        validate_public_url("http://localhost:8791/video.mp4")
    with pytest.raises(SSRFError):
        validate_public_url("http://192.168.1.1:8791/video.mp4")


def test_page_renders_ai_note_from_label_spans():
    """Regression (live demo run 2026-10-29): label.py writes "spans", the page looked for "labels" and showed nothing."""
    from verdict.page import render_verdict_page
    job = {"id": "j1", "status": "done", "url": "https://x/1", "result": {
        "headline": "Cut", "detector_kind": "ref-text-only", "detection": {"witness": {"verdict": "cut", "pieces": [], "removed": []}},
        "label": {"model": "m", "spans": [{"effect": "minor", "why": "Omitting 'x' removes <b>emphasis</b>."}]},
        "proofs": [], "coverage": {"complete": True, "gaps": [], "notes": []}, "clip": {"duration_s": 1.0}}}
    html = render_verdict_page(job, "/nonexistent")
    assert "AI note (advisory)" in html and "minor" in html
    assert "<b>emphasis</b>" not in html  # escaped
