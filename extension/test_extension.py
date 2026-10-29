"""Offline-safe test suite for Component S6 (Wholetake MV3 browser extension)."""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

EXT_DIR = Path(__file__).resolve().parent

SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"AIza[0-9A-Za-z\-_]{35}"),
    re.compile(r"ghp_[0-9A-Za-z]{30,}"),
    re.compile(r"-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"(?i)(api_key|secret_key|private_key|venue_key)\s*[:=]\s*['\"][0-9a-fA-F]{32,}['\"]"),
]


def test_manifest_schema() -> None:
    manifest_path = EXT_DIR / "manifest.json"
    assert manifest_path.is_file(), "extension/manifest.json missing"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert manifest.get("manifest_version") == 3, "manifest_version must be 3"
    assert isinstance(manifest.get("name"), str) and manifest["name"].strip()
    assert isinstance(manifest.get("version"), str) and manifest["version"].strip()

    perms = set(manifest.get("permissions", []))
    for req in ("contextMenus", "storage", "activeTab", "scripting"):
        assert req in perms, f"missing required permission: {req}"

    host_perms = set(manifest.get("host_permissions", []))
    assert "http://*/*" in host_perms and "https://*/*" in host_perms, (
        "host_permissions must include http://*/* and https://*/* for cross-origin POST /api/verdict"
    )

    bg = manifest.get("background", {})
    assert isinstance(bg.get("service_worker"), str) and bg["service_worker"], "missing background.service_worker"
    assert isinstance(bg.get("scripts"), list) and len(bg["scripts"]) > 0, "missing Firefox background.scripts fallback"

    action = manifest.get("action", {})
    assert action.get("default_popup") == "popup.html", "action.default_popup must be popup.html"

    icons = manifest.get("icons", {})
    assert icons, "manifest.icons must not be empty"
    for _size, rel_icon in icons.items():
        icon_file = EXT_DIR / rel_icon
        assert icon_file.is_file(), f"referenced icon does not exist: {rel_icon}"


def test_no_secrets_in_extension() -> None:
    assert EXT_DIR.is_dir(), "extension/ directory missing"
    scanned = 0
    for path in EXT_DIR.rglob("*"):
        if not path.is_file() or "__pycache__" in path.parts or path.name == "test_extension.py":
            continue
        scanned += 1
        text = path.read_text(encoding="utf-8", errors="ignore")
        for pat in SECRET_PATTERNS:
            m = pat.search(text)
            assert m is None, f"potential secret matched in {path.name}: {m.group(0)[:16]}..."
    assert scanned >= 2, "expected at least manifest.json and api.js in extension/"


def _start_mock_verdict_server():
    jobs = {}
    poll_counts = {}

    class MockHandler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            return

        def _send(self, code: int, obj: dict, cors: bool = False):
            raw = (json.dumps(obj) + "\n").encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            if cors:
                self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(raw)

        def do_POST(self):
            if self.path.rstrip("/") != "/api/verdict":
                self._send(404, {"error": "not found"}, cors=False)
                return
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length).decode("utf-8"))
            url = body.get("url", "")
            if not isinstance(url, str) or not url.startswith(("http://", "https://")):
                self._send(400, {"error": "invalid url"}, cors=False)
                return
            job_id = "deadbeefcafebabe"
            jobs[job_id] = {
                "id": job_id,
                "url": url,
                "status": "queued",
                "result": None,
                "error": None,
            }
            poll_counts[job_id] = 0
            # Note: cors=False matches verdict/service.py do_POST
            self._send(202, {"id": job_id, "status": "queued"}, cors=False)

        def do_GET(self):
            if self.path.startswith("/api/verdict/"):
                job_id = self.path.split("/api/verdict/", 1)[1]
                job = jobs.get(job_id)
                if not job:
                    self._send(404, {"error": "verdict job not found"}, cors=True)
                    return
                poll_counts[job_id] = poll_counts.get(job_id, 0) + 1
                if poll_counts[job_id] >= 2:
                    job["status"] = "done"
                    job["result"] = {
                        "verdict_id": job_id,
                        "page_url": f"/v/{job_id}",
                        "detection": {
                            "stamp": {
                                "present": True,
                                "venue": "main-hall",
                                "recorded_at_utc": "2026-10-31T06:12:07Z",
                                "layer1_score": 0.96,
                                "layer2_score": 0.88,
                                "cuts": [{"at_clip_s": 12.3, "removed_s": 0.6}],
                            },
                            "witness": {
                                "verdict": "cut",
                                "stream": "aje",
                                "removed": [
                                    {
                                        "after_clip_s": 12.3,
                                        "seq": 41,
                                        "src_s": [45.4, 46.0],
                                        "words": "not",
                                    }
                                ],
                            },
                        },
                    }
                self._send(200, job, cors=True)
                return
            self._send(404, {"error": "not found"}, cors=True)

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), MockHandler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    return httpd


def test_api_js_and_http_contract() -> None:
    api_path = EXT_DIR / "api.js"
    assert api_path.is_file(), "extension/api.js missing"
    api_src = api_path.read_text(encoding="utf-8")
    for fn_name in (
        "normalizeServiceUrl",
        "extractClipCandidate",
        "submitClip",
        "getVerdict",
        "pollVerdict",
        "formatVerdictSummary",
    ):
        assert fn_name in api_src, f"api.js missing export {fn_name}"

    httpd = _start_mock_verdict_server()
    port = httpd.server_address[1]
    base_url = f"http://127.0.0.1:{port}"

    try:
        node_bin = shutil.which("node")
        if node_bin:
            tmp_dir = Path(tempfile.mkdtemp())
            try:
                runner = tmp_dir / "run_api_test.js"
                runner.write_text(
                    f"""
const assert = require('assert');
const api = require({json.dumps(str(api_path))});

(async () => {{
  assert.strictEqual(api.normalizeServiceUrl('  http://localhost:8790/// '), 'http://localhost:8790');
  assert.strictEqual(api.normalizeServiceUrl('127.0.0.1:8790/'), 'http://127.0.0.1:8790');
  assert.strictEqual(api.normalizeServiceUrl(''), 'http://127.0.0.1:8790');

  const c1 = api.extractClipCandidate({{ linkUrl: 'https://x.com/user/status/123', pageUrl: 'https://x.com/home' }});
  assert.strictEqual(c1.url, 'https://x.com/user/status/123');
  assert.strictEqual(c1.source, 'link');
  assert.strictEqual(c1.isKnownMediaOrSocial, true);

  const c2 = api.extractClipCandidate({{ srcUrl: 'https://cdn.example.com/audio.mp4', pageUrl: 'https://example.com' }});
  assert.strictEqual(c2.url, 'https://cdn.example.com/audio.mp4');
  assert.strictEqual(c2.source, 'media');
  assert.strictEqual(c2.isKnownMediaOrSocial, true);

  const offlineRes = await api.submitClip('http://127.0.0.1:1', 'https://youtube.com/watch?v=abc', {{
    fetchImpl: async () => {{ throw new Error('ECONNREFUSED'); }}
  }});
  assert.strictEqual(offlineRes.ok, false);
  assert.strictEqual(offlineRes.offline, true);

  const sub = await api.submitClip({json.dumps(base_url)}, 'https://www.instagram.com/reel/xyz/');
  assert.strictEqual(sub.ok, true);
  assert.strictEqual(sub.id, 'deadbeefcafebabe');

  const polled = await api.pollVerdict({json.dumps(base_url)}, sub.id, {{ intervalMs: 10, timeoutMs: 2000 }});
  assert.strictEqual(polled.ok, true);
  assert.strictEqual(polled.job.status, 'done');

  const summary = api.formatVerdictSummary(polled.job, {json.dumps(base_url)});
  assert.strictEqual(summary.verdict, 'cut');
  assert.strictEqual(summary.stamp.present, true);
  assert.strictEqual(summary.stamp.venue, 'main-hall');
  assert.strictEqual(summary.cuts.length, 1);
  assert.strictEqual(summary.cuts[0].words, 'not');
  assert.strictEqual(summary.proofUrl, {json.dumps(base_url)} + '/v/deadbeefcafebabe');
}})().catch(err => {{
  console.error(err);
  process.exit(1);
}});
""",
                    encoding="utf-8",
                )
                proc = subprocess.run([node_bin, str(runner)], capture_output=True, text=True, timeout=15)
                assert proc.returncode == 0, f"node api.js test failed: {proc.stderr or proc.stdout}"
            finally:
                shutil.rmtree(tmp_dir, ignore_errors=True)
        else:
            # Fallback HTTP contract verification in Python if Node is not installed
            req = urllib.request.Request(
                f"{base_url}/api/verdict",
                data=json.dumps({"url": "https://x.com/i/status/1"}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                assert resp.status == 202
                sub_data = json.loads(resp.read().decode("utf-8"))
            job_id = sub_data["id"]
            for _ in range(3):
                with urllib.request.urlopen(f"{base_url}/api/verdict/{job_id}", timeout=5) as resp:
                    job_data = json.loads(resp.read().decode("utf-8"))
                if job_data["status"] == "done":
                    break
            assert job_data["status"] == "done"
    finally:
        httpd.shutdown()
        httpd.server_close()


def run_all() -> int:
    tests = [
        ("manifest_schema", test_manifest_schema),
        ("no_secrets_in_extension", test_no_secrets_in_extension),
        ("api_js_and_http_contract", test_api_js_and_http_contract),
    ]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS {name}")
        except Exception as exc:
            print(f"FAIL {name}: {exc}")
            failed += 1
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(run_all())
