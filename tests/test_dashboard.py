"""Tests for the Wholetake Broadcast Forensics Dashboard (`verdict/dashboard.py` and `verdict/service.py`)."""
from __future__ import annotations

import json
import re
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from verdict.service import VerdictManager, make_handler


def _start_test_server(tmp_path: Path, monkeypatch):
    wroot = tmp_path / "witness"
    vdir = tmp_path / "verdicts"
    udir = tmp_path / "uploads"
    ddir = tmp_path / "demo_clips"
    wroot.mkdir()
    vdir.mkdir()
    udir.mkdir()
    ddir.mkdir()
    (ddir / "cut_sample.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42")

    monkeypatch.setenv("WT_UPLOADS_DIR", str(udir))
    monkeypatch.setenv("WT_DEMO_CLIPS_DIR", str(ddir))

    def fake_fetch(url_or_path: str) -> dict:
        return {
            "wav": str(tmp_path / "dummy.wav"),
            "sha256": "a" * 64,
            "duration_s": 20.0,
            "source_url": url_or_path if url_or_path.startswith("http") else None,
            "title": Path(url_or_path).name,
        }

    def fake_detect(_wav, _wroot) -> dict:
        return {
            "detector": {"name": "wt-detect", "version": "0.1"},
            "clip": {"duration_s": 20.0, "sha256": "a" * 64},
            "stamp": {
                "present": True,
                "layer1_score": 0.98,
                "layer2_score": 0.91,
                "recorded_at_utc": "2026-10-31T10:40:08Z",
                "venue": "HH Goa Main Stage",
                "cuts": [{"at_clip_s": 12.0, "removed_s": 0.30}],
            },
            "witness": {
                "verdict": "faithful",
                "stream": "hhgoa-stage",
                "pieces": [{"clip_s": [0.0, 20.0], "seq": 0, "src_s": [0.0, 20.0], "audio_score": 0.95}],
                "removed": [],
                "inserted": [],
                "transcript_clip": "We will raise ticket prices this year",
            },
        }

    mgr = VerdictManager(
        witness_root=wroot,
        verdicts_dir=vdir,
        fetch_fn=fake_fetch,
        detect_fn=fake_detect,
        label_fn=lambda *_: None,
        start_worker=True,
    )
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(mgr))
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    return httpd, mgr, base, udir


def test_dashboard_html_and_no_external_cdns(tmp_path: Path, monkeypatch):
    httpd, mgr, base, _ = _start_test_server(tmp_path, monkeypatch)
    try:
        for route in ("/", "/dashboard"):
            with urllib.request.urlopen(f"{base}{route}", timeout=5) as r:
                assert r.status == 200
                html_doc = r.read().decode("utf-8")
            assert "#070A10" in html_doc
            assert "http://127.0.0.1:8791" in html_doc
            assert "300-3400 Hz" in html_doc
            # Zero external font/CDN links in <script src>, <link href>, <img src>, or @import
            ext_assets = re.findall(r'(?:src|href)=["\'](https?://[^"\']+)["\']', html_doc)
            for url in ext_assets:
                assert url.startswith(("http://127.0.0.1:8791", "https://sepolia.basescan.org/tx/")), (
                    f"unexpected external URL in dashboard: {url}"
                )
    finally:
        httpd.shutdown()
        mgr.close()


def test_api_status_schema_and_no_host_paths(tmp_path: Path, monkeypatch):
    httpd, mgr, base, _ = _start_test_server(tmp_path, monkeypatch)
    try:
        with urllib.request.urlopen(f"{base}/api/status", timeout=5) as r:
            assert r.status == 200
            st = json.loads(r.read().decode("utf-8"))
        for key in ("detector", "roomstamp_sessions", "witness_streams", "demo_clips", "counts"):
            assert key in st
        assert st["demo_clips"] == [{"preset": "cut_sample.mp4", "label": "Cut Clip (-0.30s 'not' removed)", "size_bytes": 12}]
        assert str(tmp_path) not in json.dumps(st["demo_clips"])
    finally:
        httpd.shutdown()
        mgr.close()


def test_upload_path_traversal_xss_and_stamp_cuts_svg(tmp_path: Path, monkeypatch):
    httpd, mgr, base, udir = _start_test_server(tmp_path, monkeypatch)
    try:
        req = urllib.request.Request(
            f"{base}/api/verdict/upload",
            data=b"fake-mp4-bytes",
            headers={
                "Content-Type": "application/octet-stream",
                "X-Filename": "../../<script>alert(1)</script>evil.mp4",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as r:
            assert r.status == 202
            job_info = json.loads(r.read().decode("utf-8"))

        # Verify file was written strictly inside udir with sanitized filename
        saved = list(udir.glob("*.mp4"))
        assert len(saved) == 1
        assert saved[0].parent.resolve() == udir.resolve()
        assert "<script>" not in saved[0].name

        # Wait for worker to finish job
        import time
        for _ in range(40):
            j = mgr.get_job(job_info["id"])
            if j and j["status"] in ("done", "error"):
                break
            time.sleep(0.05)

        with urllib.request.urlopen(f"{base}/", timeout=5) as r:
            html_doc = r.read().decode("utf-8")

        # Verify XSS is never raw and Roomstamp cut (`stamp.cuts` with empty `witness.removed`) renders on SVG ribbon
        assert "<script>alert(1)</script>" not in html_doc
        assert "-0.30s @ 0:12" in html_doc
        assert "ROOMSTAMP VERIFIED" in html_doc
        assert "0.30 s removed at 0:12 (Roomstamp verified at HH Goa Main Stage)" in html_doc
    finally:
        httpd.shutdown()
        mgr.close()


if __name__ == "__main__":
    import os
    import shutil
    import tempfile

    class _MP:
        def __init__(self):
            self._old = {}

        def setenv(self, k: str, v: str):
            if k not in self._old:
                self._old[k] = os.environ.get(k)
            os.environ[k] = v

        def restore(self):
            for k, v in self._old.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    for fn in (
        test_dashboard_html_and_no_external_cdns,
        test_api_status_schema_and_no_host_paths,
        test_upload_path_traversal_xss_and_stamp_cuts_svg,
    ):
        td = Path(tempfile.mkdtemp(prefix="wt_dash_"))
        mp = _MP()
        try:
            fn(td, mp)
            print(f"PASS {fn.__name__}")
        finally:
            mp.restore()
            shutil.rmtree(td, ignore_errors=True)
    print("SUMMARY: 3 passed, 0 failed, 0 skipped")

