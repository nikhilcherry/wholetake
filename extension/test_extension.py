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
            if length <= 0 or length > 65536:
                self._send(400, {"error": "invalid request body length"}, cors=False)
                return
            body = json.loads(self.rfile.read(length).decode("utf-8"))
            url = body.get("url", "")
            if not isinstance(url, str) or not url.startswith(("http://", "https://")):
                self._send(400, {"error": "invalid url"}, cors=False)
                return
            if any(h in url for h in ("127.0.0.1", "localhost", "169.254.169.254", "[::1]")):
                self._send(400, {"error": "SSRF blocked: loopback or private address not allowed"}, cors=False)
                return
            if "trigger-error" in url:
                job_id = "errjob0000000001"
            else:
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
                    if job_id == "errjob0000000001":
                        job["status"] = "error"
                        job["error"] = "detector failed: clip has no audio stream"
                        job["result"] = None
                    else:
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


def test_readme_completeness() -> None:
    readme_path = EXT_DIR / "README.md"
    assert readme_path.is_file(), "extension/README.md missing"
    text = readme_path.read_text(encoding="utf-8")
    assert len(text.strip()) >= 400, "extension/README.md is too short"

    required_tokens = [
        "chrome://extensions",
        "Developer mode",
        "Load unpacked",
        "edge://extensions",
        "about:debugging#/runtime/this-firefox",
        "Load Temporary Add-on",
        "http://127.0.0.1:8790",
        "Check this clip with Wholetake",
        "/api/verdict",
        "CORS",
        "SSRF",
        "host_permissions",
        "400",
        "404",
    ]
    for token in required_tokens:
        assert token in text, f"extension/README.md missing required onboarding/troubleshooting info: {token!r}"

    lower = text.lower()
    for platform in ("youtube", "instagram", "x.com"):
        assert platform in lower, f"extension/README.md missing platform usage reference: {platform!r}"
    for topic in ("offline", "tunnel", "zero"):
        assert topic in lower, f"extension/README.md missing topic: {topic!r}"


def _assert_safe_ext_relpath(rel_path: str, must_exist: bool = False) -> Path:
    assert isinstance(rel_path, str) and rel_path.strip(), "empty asset path reference"
    assert not rel_path.startswith(("/", "\\", "http://", "https://")), f"asset path must be relative: {rel_path}"
    resolved = (EXT_DIR / rel_path).resolve()
    assert resolved.is_relative_to(EXT_DIR.resolve()), f"asset path escapes extension/: {rel_path}"
    if must_exist or resolved.exists():
        assert resolved.is_file(), f"referenced asset missing on disk: {rel_path}"
        assert resolved.stat().st_size > 0, f"referenced asset is empty: {rel_path}"
    return resolved


def test_asset_and_html_references() -> None:
    manifest_path = EXT_DIR / "manifest.json"
    assert manifest_path.is_file(), "extension/manifest.json missing"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    # Always-required core files
    _assert_safe_ext_relpath("api.js", must_exist=True)
    _assert_safe_ext_relpath("README.md", must_exist=True)

    bg = manifest.get("background", {})
    if bg.get("service_worker"):
        _assert_safe_ext_relpath(bg["service_worker"])
    for script_rel in bg.get("scripts", []):
        _assert_safe_ext_relpath(script_rel)

    action = manifest.get("action", {})
    if action.get("default_popup"):
        _assert_safe_ext_relpath(action["default_popup"])
    for _sz, icon_rel in (action.get("default_icon") or {}).items():
        _assert_safe_ext_relpath(icon_rel, must_exist=True)
    for _sz, icon_rel in (manifest.get("icons") or {}).items():
        _assert_safe_ext_relpath(icon_rel, must_exist=True)

    for cs in manifest.get("content_scripts", []):
        for js_rel in cs.get("js", []):
            _assert_safe_ext_relpath(js_rel)
        for css_rel in cs.get("css", []):
            _assert_safe_ext_relpath(css_rel)

    # Validate all local <script src="..."> and <link href="..."> references in any HTML files in extension/
    attr_pat = re.compile(r"<(?:script|link)\b[^>]*(?:src|href)=['\"]([^'\"]+)['\"]", re.IGNORECASE)
    for html_file in sorted(EXT_DIR.rglob("*.html")):
        html_text = html_file.read_text(encoding="utf-8")
        refs = attr_pat.findall(html_text)
        assert refs, f"{html_file.name} has no script or stylesheet references"
        for ref in refs:
            if ref.startswith(("http://", "https://", "data:", "#")):
                continue
            clean_ref = ref.split("?", 1)[0].split("#", 1)[0]
            target = (html_file.parent / clean_ref).resolve()
            assert target.is_relative_to(EXT_DIR.resolve()), f"HTML reference escapes extension/: {ref}"
            assert target.is_file(), f"{html_file.name} references missing file: {ref}"
            assert target.stat().st_size > 0, f"{html_file.name} references empty file: {ref}"

    # Validate any importScripts(...) calls in JS files point to existing files in extension/
    import_pat = re.compile(r"importScripts\(\s*['\"]([^'\"]+)['\"]\s*\)")
    for js_file in sorted(EXT_DIR.rglob("*.js")):
        js_text = js_file.read_text(encoding="utf-8")
        for imp in import_pat.findall(js_text):
            _assert_safe_ext_relpath(imp, must_exist=True)


def test_cors_and_error_edge_cases() -> None:
    import urllib.error
    from verdict.service import VerdictManager, make_handler

    api_path = EXT_DIR / "api.js"
    assert api_path.is_file(), "extension/api.js missing"

    # Part 1: Test api.js against mock server edge cases (400 SSRF, 404 unknown job, status: 'error', offline)
    mock_httpd = _start_mock_verdict_server()
    mock_port = mock_httpd.server_address[1]
    mock_base = f"http://127.0.0.1:{mock_port}"

    try:
        node_bin = shutil.which("node")
        if node_bin:
            tmp_dir = Path(tempfile.mkdtemp())
            try:
                runner = tmp_dir / "run_edge_cases.js"
                runner.write_text(
                    f"""
const assert = require('assert');
const api = require({json.dumps(str(api_path))});

(async () => {{
  const baseUrl = {json.dumps(mock_base)};

  // 1. 400 SSRF rejection (loopback / metadata URL)
  const ssrfRes = await api.submitClip(baseUrl, 'http://127.0.0.1:8790/secret.wav');
  assert.strictEqual(ssrfRes.ok, false);
  assert.strictEqual(ssrfRes.status, 400);
  assert.ok(!ssrfRes.offline);
  assert.ok(String(ssrfRes.error || '').toLowerCase().includes('ssrf') || String(ssrfRes.error || '').length > 0);

  // 2. 404 unknown job on getVerdict and pollVerdict
  const get404 = await api.getVerdict(baseUrl, 'nonexistent_job_999');
  assert.strictEqual(get404.ok, false);
  assert.strictEqual(get404.status, 404);

  const poll404 = await api.pollVerdict(baseUrl, 'nonexistent_job_999', {{ intervalMs: 10, timeoutMs: 500 }});
  assert.strictEqual(poll404.ok, false);
  assert.strictEqual(poll404.status, 404);

  // 3. Job transitioning to status: 'error'
  const subErr = await api.submitClip(baseUrl, 'https://example.com/trigger-error.mp4');
  assert.strictEqual(subErr.ok, true);
  assert.strictEqual(subErr.id, 'errjob0000000001');

  const pollErr = await api.pollVerdict(baseUrl, subErr.id, {{ intervalMs: 10, timeoutMs: 2000 }});
  const errJob = pollErr.job || {{ status: 'error', error: pollErr.error }};
  assert.strictEqual(errJob.status, 'error');
  const errMsg = String(pollErr.error || errJob.error || '');
  assert.ok(errMsg.includes('detector failed'), 'expected detector failed message, got: ' + errMsg);

  const errSummary = api.formatVerdictSummary(errJob, baseUrl);
  assert.ok(errSummary && typeof errSummary === 'object');

  // 4. Offline / unreachable server handling on getVerdict and pollVerdict
  const offlineGet = await api.getVerdict('http://127.0.0.1:1', 'deadbeefcafebabe', {{
    fetchImpl: async () => {{ throw new Error('network down'); }}
  }});
  assert.strictEqual(offlineGet.ok, false);
  assert.strictEqual(offlineGet.offline, true);

  const offlinePoll = await api.pollVerdict('http://127.0.0.1:1', 'deadbeefcafebabe', {{
    intervalMs: 10,
    timeoutMs: 200,
    fetchImpl: async () => {{ throw new Error('network down'); }}
  }});
  assert.strictEqual(offlinePoll.ok, false);
  assert.strictEqual(offlinePoll.offline, true);
}})().catch(err => {{
  console.error(err);
  process.exit(1);
}});
""",
                    encoding="utf-8",
                )
                proc = subprocess.run([node_bin, str(runner)], capture_output=True, text=True, timeout=15)
                assert proc.returncode == 0, f"node edge-case test failed: {proc.stderr or proc.stdout}"
            finally:
                shutil.rmtree(tmp_dir, ignore_errors=True)
    finally:
        mock_httpd.shutdown()
        mock_httpd.server_close()

    # Part 2: E2E verification against real verdict.service.VerdictManager + make_handler
    tmp_service_dir = Path(tempfile.mkdtemp())
    mgr = None
    real_httpd = None
    try:
        wroot = tmp_service_dir / "witness"
        vdir = tmp_service_dir / "verdicts"
        wroot.mkdir(parents=True, exist_ok=True)
        vdir.mkdir(parents=True, exist_ok=True)

        def failing_fetch(_url: str) -> dict:
            raise RuntimeError("simulated fetch failure: unsupported media stream")

        mgr = VerdictManager(
            witness_root=wroot,
            verdicts_dir=vdir,
            fetch_fn=failing_fetch,
            start_worker=False,
        )
        real_httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(mgr))
        t = threading.Thread(target=real_httpd.serve_forever, daemon=True)
        t.start()
        rbase = f"http://127.0.0.1:{real_httpd.server_address[1]}"

        # 2a. POST /api/verdict with loopback/metadata URL -> 400 SSRFError and cors=False
        for bad_url in ("http://127.0.0.1:8790/private.wav", "http://169.254.169.254/latest/meta-data/", "file:///etc/passwd"):
            req = urllib.request.Request(
                f"{rbase}/api/verdict",
                data=json.dumps({"url": bad_url}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                urllib.request.urlopen(req, timeout=5)
                raise AssertionError(f"expected HTTP 400 SSRF rejection for {bad_url}")
            except urllib.error.HTTPError as e:
                assert e.code == 400, f"expected 400 for {bad_url}, got {e.code}"
                assert e.headers.get("Access-Control-Allow-Origin") is None, "POST /api/verdict must set cors=False"
                err_payload = json.loads(e.read().decode("utf-8"))
                assert isinstance(err_payload.get("error"), str) and err_payload["error"]

        # 2b. GET /api/verdict/<unknown> -> 404 and cors=True
        try:
            urllib.request.urlopen(f"{rbase}/api/verdict/unknownjob123456", timeout=5)
            raise AssertionError("expected HTTP 404 for unknown job")
        except urllib.error.HTTPError as e:
            assert e.code == 404, f"expected 404, got {e.code}"
            assert e.headers.get("Access-Control-Allow-Origin") == "*", "GET /api/verdict/<id> must set cors=True"
            err_payload = json.loads(e.read().decode("utf-8"))
            assert err_payload.get("error") == "verdict job not found"

        # 2c. Job transitioning to status: 'error' via VerdictManager.process_one
        submitted = mgr.submit("https://example.com/broken_clip.mp4", allow_local=True)
        err_job_id = submitted["id"]
        mgr.process_one(err_job_id)

        with urllib.request.urlopen(f"{rbase}/api/verdict/{err_job_id}", timeout=5) as resp:
            assert resp.status == 200
            assert resp.headers.get("Access-Control-Allow-Origin") == "*"
            job_doc = json.loads(resp.read().decode("utf-8"))
        assert job_doc["status"] == "error"
        assert "simulated fetch failure" in (job_doc.get("error") or "")
    finally:
        if real_httpd is not None:
            real_httpd.shutdown()
            real_httpd.server_close()
        if mgr is not None:
            mgr.close()
        shutil.rmtree(tmp_service_dir, ignore_errors=True)


def _start_staged_mock_verdict_server():
    """Mock server that steps through queued -> fetching -> detecting -> labeling -> done."""
    jobs = {}
    poll_counts = {}
    stage_sequence = ["queued", "fetching", "detecting", "labeling", "done"]

    class StagedHandler(BaseHTTPRequestHandler):
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
            body = json.loads(self.rfile.read(length).decode("utf-8")) if length > 0 else {}
            url = body.get("url", "")
            if not isinstance(url, str) or not url.startswith(("http://", "https://")):
                self._send(400, {"error": "invalid url"}, cors=False)
                return
            job_id = "stagedjob0000001"
            jobs[job_id] = {
                "id": job_id,
                "url": url,
                "status": "queued",
                "result": None,
                "error": None,
            }
            poll_counts[job_id] = 0
            self._send(202, {"id": job_id, "status": "queued"}, cors=False)

        def do_GET(self):
            if self.path.startswith("/api/verdict/"):
                job_id = self.path.split("/api/verdict/", 1)[1]
                job = jobs.get(job_id)
                if not job:
                    self._send(404, {"error": "verdict job not found"}, cors=True)
                    return
                idx = min(poll_counts.get(job_id, 0), len(stage_sequence) - 1)
                poll_counts[job_id] = poll_counts.get(job_id, 0) + 1
                st = stage_sequence[idx]
                job["status"] = st
                if st == "done":
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

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), StagedHandler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    return httpd


NODE_WIRING_TEST_SCRIPT = r"""
const assert = require('assert');
const path = require('path');

const extDir = process.argv[2];
const baseUrl = process.argv[3];

const api = require(path.join(extDir, 'api.js'));
global.WholetakeApi = api;
const bg = require(path.join(extDir, 'background.js'));
const popup = require(path.join(extDir, 'popup.js'));

function createMockExt(initialStore) {
  const store = Object.assign({}, initialStore || {});
  const createdMenus = [];
  const listeners = { onInstalled: [], onClicked: [], onMessage: [] };
  return {
    store,
    createdMenus,
    listeners,
    runtime: {
      onInstalled: { addListener: (fn) => listeners.onInstalled.push(fn) },
      onStartup: { addListener: () => {} },
      onMessage: { addListener: (fn) => listeners.onMessage.push(fn) },
      sendMessage: async () => ({ ok: true }),
    },
    contextMenus: {
      removeAll: (cb) => { createdMenus.length = 0; if (cb) cb(); },
      create: (props, cb) => { createdMenus.push(props); if (cb) cb(); },
      onClicked: { addListener: (fn) => listeners.onClicked.push(fn) },
    },
    storage: {
      local: {
        get: (keys, cb) => {
          const res = {};
          const list = Array.isArray(keys) ? keys : Object.keys(store);
          for (const k of list) {
            if (k in store) res[k] = store[k];
          }
          if (cb) cb(res);
          return Promise.resolve(res);
        },
        set: (items, cb) => {
          Object.assign(store, items);
          if (cb) cb();
          return Promise.resolve();
        },
      },
    },
    action: {
      openPopup: () => Promise.resolve(),
    },
    tabs: {
      query: (_q, cb) => {
        const t = [{ id: 7, url: 'https://x.com/speaker/status/999888777' }];
        if (cb) cb(t);
        return Promise.resolve(t);
      },
    },
    scripting: {
      executeScript: (opts, cb) => {
        const fakeDoc = {
          querySelectorAll: (sel) => {
            if (sel === 'video, audio') {
              return [{ src: 'https://cdn.example.org/stage_clip.mp4' }];
            }
            return [];
          },
          querySelector: () => null,
        };
        const out = [{ result: opts.func(fakeDoc, { href: 'https://example.org/watch' }) }];
        if (cb) cb(out);
        return Promise.resolve(out);
      },
    },
  };
}

function createMockElement(tag, id) {
  const classes = new Set();
  const attrs = {};
  const listeners = {};
  return {
    tagName: String(tag || 'div').toUpperCase(),
    id: id || '',
    value: '',
    textContent: '',
    href: '#',
    hidden: false,
    disabled: false,
    children: [],
    get firstChild() {
      return this.children[0] || null;
    },
    appendChild(child) {
      this.children.push(child);
      return child;
    },
    removeChild(child) {
      const idx = this.children.indexOf(child);
      if (idx >= 0) this.children.splice(idx, 1);
      return child;
    },
    setAttribute(k, v) {
      attrs[k] = String(v);
    },
    getAttribute(k) {
      return attrs[k] || null;
    },
    addEventListener(ev, fn) {
      listeners[ev] = listeners[ev] || [];
      listeners[ev].push(fn);
    },
    trigger(ev) {
      for (const fn of (listeners[ev] || [])) fn();
    },
    classList: {
      add: (...c) => c.forEach((x) => classes.add(x)),
      remove: (...c) => c.forEach((x) => classes.delete(x)),
      toggle: (c, force) => {
        const on = force !== undefined ? Boolean(force) : !classes.has(c);
        if (on) classes.add(c); else classes.delete(c);
      },
      contains: (c) => classes.has(c),
    },
    querySelectorAll() {
      return [];
    },
  };
}

function createMockPopupDom() {
  const elements = {};
  const ids = [
    'service-url-input',
    'save-service-url-btn',
    'service-url-saved',
    'clip-url-input',
    'clip-source-hint',
    'detect-clip-btn',
    'check-clip-btn',
    'status-section',
    'status-text',
    'status-job-id',
    'status-steps',
    'error-banner',
    'error-title',
    'error-message',
    'verdict-card',
    'verdict-badge',
    'verdict-stream',
    'cuts-summary',
    'cuts-list',
    'stamp-badge',
    'stamp-venue',
    'stamp-time',
    'stamp-scores',
    'stamp-cuts-list',
    'proof-link',
  ];
  for (const id of ids) {
    elements[id] = createMockElement('div', id);
  }
  const stepNames = ['queued', 'fetching', 'detecting', 'labeling', 'done'];
  const stepEls = stepNames.map((s) => {
    const el = createMockElement('span', 'step-' + s);
    el.setAttribute('data-step', s);
    return el;
  });
  elements['status-steps'].querySelectorAll = () => stepEls;

  return {
    elements,
    stepEls,
    getElementById(id) {
      return elements[id] || null;
    },
    createElement(tag) {
      return createMockElement(tag);
    },
  };
}

(async () => {
  // 1. Background context menu registration & click handling
  const mockExt = createMockExt();
  bg.initBackground(mockExt);
  assert.strictEqual(mockExt.createdMenus.length, 1);
  assert.strictEqual(mockExt.createdMenus[0].id, 'wholetake-check-clip');
  assert.strictEqual(mockExt.createdMenus[0].title, 'Check this clip with Wholetake');
  assert.deepStrictEqual(mockExt.createdMenus[0].contexts, ['video', 'audio', 'link', 'page']);

  const clickRes = await bg.handleContextMenuClick(
    { menuItemId: 'wholetake-check-clip', linkUrl: 'https://www.youtube.com/watch?v=dQw4w9WgXcQ' },
    { id: 1, url: 'https://www.youtube.com/' },
    { ext: mockExt }
  );
  assert.strictEqual(clickRes.handled, true);
  assert.strictEqual(mockExt.store.pendingClipUrl, 'https://www.youtube.com/watch?v=dQw4w9WgXcQ');
  assert.strictEqual(mockExt.store.lastCandidate.source, 'link');
  assert.strictEqual(typeof mockExt.store.updatedAt, 'number');

  // 2. Background runtime.onMessage (WT_GET_STATE and WT_CHECK_CLIP)
  const stateRes = await bg.handleRuntimeMessage({ type: 'WT_GET_STATE' }, null, { ext: mockExt });
  assert.strictEqual(stateRes.ok, true);
  assert.strictEqual(stateRes.state.pendingClipUrl, 'https://www.youtube.com/watch?v=dQw4w9WgXcQ');

  const checkRes = await bg.handleRuntimeMessage(
    {
      type: 'WT_CHECK_CLIP',
      serviceUrl: baseUrl,
      clipUrl: 'https://x.com/demo/status/10101',
      intervalMs: 5,
    },
    null,
    { ext: mockExt }
  );
  assert.strictEqual(checkRes.ok, true);
  assert.strictEqual(checkRes.summary.verdict, 'cut');
  assert.strictEqual(checkRes.summary.proofUrl, baseUrl + '/v/stagedjob0000001');

  // 3. Page media detector
  const detMedia = popup.detectMediaInPageContext(
    {
      querySelectorAll: (sel) => (sel === 'video, audio' ? [{ currentSrc: 'https://cdn.example.com/clip.mp4' }] : []),
      querySelector: () => null,
    },
    { href: 'https://example.com/page' }
  );
  assert.strictEqual(detMedia.url, 'https://cdn.example.com/clip.mp4');
  assert.strictEqual(detMedia.source, 'media');

  // 4. Popup init + live polling across queued -> fetching -> detecting -> labeling -> done
  const popupExt = createMockExt({});
  const dom = createMockPopupDom();
  const initState = await popup.initPopup({ doc: dom, ext: popupExt });
  assert.strictEqual(initState.serviceUrl, 'http://127.0.0.1:8790');
  assert.strictEqual(initState.clipUrl, 'https://cdn.example.org/stage_clip.mp4');
  assert.strictEqual(dom.elements['clip-url-input'].value, 'https://cdn.example.org/stage_clip.mp4');

  const seenSteps = [];
  const runRes = await popup.startClipCheck({
    doc: dom,
    ext: popupExt,
    serviceUrl: baseUrl,
    clipUrl: 'https://www.instagram.com/reel/abc123/',
    intervalMs: 5,
    onProgress: (j) => {
      if (j && j.status) seenSteps.push(j.status);
    },
  });
  assert.strictEqual(runRes.ok, true);
  for (const expectedStep of ['queued', 'fetching', 'detecting', 'labeling', 'done']) {
    assert.ok(seenSteps.includes(expectedStep), 'missing polled step: ' + expectedStep);
  }
  assert.strictEqual(dom.elements['verdict-card'].hidden, false);
  assert.strictEqual(dom.elements['verdict-badge'].textContent, 'CUT DETECTED');
  assert.strictEqual(dom.elements['stamp-badge'].textContent, 'VERIFIED IN ROOM');
  assert.strictEqual(dom.elements['stamp-venue'].textContent, 'main-hall');
  assert.ok(dom.elements['stamp-scores'].textContent.includes('0.96'));
  assert.ok(dom.elements['stamp-scores'].textContent.includes('0.88'));
  assert.strictEqual(dom.elements['cuts-list'].children.length, 1);
  assert.strictEqual(dom.elements['proof-link'].href, baseUrl + '/v/stagedjob0000001');

  // 5. XSS safety check: javascript: proofUrl rejected
  popup.renderVerdictCard(dom, {
    verdict: 'cut',
    cuts: [{ afterClipS: 1.0, srcS: [1.0, 2.0], words: '<img src=x onerror=alert(1)>' }],
    stamp: { present: false },
    proofUrl: 'javascript:alert(1)',
  });
  assert.strictEqual(dom.elements['proof-link'].href, '#');
  assert.strictEqual(dom.elements['proof-link'].hidden, true);

  // 6. Offline error state in popup
  const offRes = await popup.startClipCheck({
    doc: dom,
    ext: popupExt,
    serviceUrl: 'http://127.0.0.1:1',
    clipUrl: 'https://x.com/user/status/555',
    fetchImpl: async () => { throw new Error('ECONNREFUSED'); },
  });
  assert.strictEqual(offRes.ok, false);
  assert.strictEqual(offRes.offline, true);
  assert.strictEqual(dom.elements['error-banner'].hidden, false);
  assert.ok(dom.elements['error-title'].textContent.includes('Offline'));
})().catch((err) => {
  console.error(err);
  process.exit(1);
});
"""


def test_background_and_popup_wiring() -> None:
    bg_path = EXT_DIR / "background.js"
    popup_html_path = EXT_DIR / "popup.html"
    popup_css_path = EXT_DIR / "popup.css"
    popup_js_path = EXT_DIR / "popup.js"

    for p in (bg_path, popup_html_path, popup_css_path, popup_js_path):
        assert p.is_file(), f"missing required file: {p.name}"
        assert p.stat().st_size > 0, f"file is empty: {p.name}"

    bg_src = bg_path.read_text(encoding="utf-8")
    assert "importScripts('api.js')" in bg_src or 'importScripts("api.js")' in bg_src, (
        "background.js must load api.js via importScripts"
    )
    assert "wholetake-check-clip" in bg_src, "background.js must register menu id 'wholetake-check-clip'"
    assert "Check this clip with Wholetake" in bg_src, (
        "background.js must register title 'Check this clip with Wholetake'"
    )
    for ctx in ("video", "audio", "link", "page"):
        assert ctx in bg_src, f"background.js missing context menu context: {ctx}"
    for key in ("pendingClipUrl", "lastCandidate", "updatedAt", "WT_CHECK_CLIP", "WT_GET_STATE"):
        assert key in bg_src, f"background.js missing required token: {key}"

    html_src = popup_html_path.read_text(encoding="utf-8")
    assert "popup.css" in html_src, "popup.html must link popup.css"
    assert "api.js" in html_src, "popup.html must load api.js"
    assert "popup.js" in html_src, "popup.html must load popup.js"
    assert "Check this clip with Wholetake" in html_src, "popup.html missing primary button label"
    assert "http://127.0.0.1:8790" in html_src, "popup.html missing default service URL"
    for step in ("queued", "fetching", "detecting", "labeling", "done"):
        assert step in html_src, f"popup.html missing status step {step}"

    popup_src = popup_js_path.read_text(encoding="utf-8")
    assert "executeScript" in popup_src, "popup.js must support scripting.executeScript media detection"
    assert "textContent" in popup_src, "popup.js must safely render untrusted strings via textContent"
    assert "innerHTML" not in popup_src, "popup.js must not use innerHTML"

    httpd = _start_staged_mock_verdict_server()
    base_url = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        node_bin = shutil.which("node")
        if node_bin:
            tmp_dir = Path(tempfile.mkdtemp())
            try:
                runner = tmp_dir / "run_wiring_test.js"
                runner.write_text(NODE_WIRING_TEST_SCRIPT, encoding="utf-8")
                proc = subprocess.run(
                    [node_bin, str(runner), str(EXT_DIR), base_url],
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
                assert proc.returncode == 0, f"node wiring test failed: {proc.stderr or proc.stdout}"
            finally:
                shutil.rmtree(tmp_dir, ignore_errors=True)
        else:
            req = urllib.request.Request(
                f"{base_url}/api/verdict",
                data=json.dumps({"url": "https://x.com/i/status/1"}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                sub = json.loads(resp.read().decode("utf-8"))
            seen = []
            for _ in range(6):
                with urllib.request.urlopen(f"{base_url}/api/verdict/{sub['id']}", timeout=5) as resp:
                    doc = json.loads(resp.read().decode("utf-8"))
                seen.append(doc["status"])
                if doc["status"] == "done":
                    break
            assert seen == ["queued", "fetching", "detecting", "labeling", "done"]
    finally:
        httpd.shutdown()
        httpd.server_close()


def run_all() -> int:
    tests = [
        ("manifest_schema", test_manifest_schema),
        ("readme_completeness", test_readme_completeness),
        ("asset_and_html_references", test_asset_and_html_references),
        ("no_secrets_in_extension", test_no_secrets_in_extension),
        ("api_js_and_http_contract", test_api_js_and_http_contract),
        ("cors_and_error_edge_cases", test_cors_and_error_edge_cases),
        ("background_and_popup_wiring", test_background_and_popup_wiring),
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
