"""Reproducer test for FINDING F278: extension/api.js fetch timeout on stalled connections."""
import json
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
API_JS = REPO_ROOT / "extension" / "api.js"


def _start_hanging_tcp_server():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(16)
    accepted = []
    stop_event = threading.Event()

    def _accept_loop():
        while not stop_event.is_set():
            try:
                conn, _ = srv.accept()
                accepted.append(conn)
            except OSError:
                break

    t = threading.Thread(target=_accept_loop, daemon=True)
    t.start()
    return srv, accepted, stop_event


def test_hanging_server_request_timeout() -> None:
    node_bin = shutil.which("node")
    assert node_bin, "node executable is required"

    srv, accepted, stop_event = _start_hanging_tcp_server()
    port = srv.getsockname()[1]
    stalled_url = f"http://127.0.0.1:{port}"
    tmp_dir = Path(tempfile.mkdtemp())

    try:
        test_js = tmp_dir / "test.js"
        test_js.write_text(
            f"""
const assert = require('assert');
const api = require({json.dumps(str(API_JS))});

(async () => {{
  const t0 = Date.now();
  const pollRes = await api.pollVerdict({json.dumps(stalled_url)}, 'deadbeefcafebabe', {{
    intervalMs: 50,
    timeoutMs: 400
  }});
  const pollElapsed = Date.now() - t0;
  assert.strictEqual(pollRes.ok, false, 'pollVerdict must fail on hanging server');
  assert.strictEqual(pollRes.offline, true, 'pollVerdict must report offline: true on timeout');
  assert.ok(pollElapsed < 2000, `pollVerdict took too long: ${{pollElapsed}}ms`);

  const t1 = Date.now();
  const subRes = await api.submitClip({json.dumps(stalled_url)}, 'https://x.com/user/status/123', {{
    requestTimeoutMs: 300
  }});
  const subElapsed = Date.now() - t1;
  assert.strictEqual(subRes.ok, false, 'submitClip must fail on hanging server');
  assert.strictEqual(subRes.offline, true, 'submitClip must report offline: true on timeout');
  assert.ok(subElapsed < 2000, `submitClip took too long: ${{subElapsed}}ms`);

  const t2 = Date.now();
  const getRes = await api.getVerdict({json.dumps(stalled_url)}, 'deadbeefcafebabe', {{
    requestTimeoutMs: 300
  }});
  const getElapsed = Date.now() - t2;
  assert.strictEqual(getRes.ok, false, 'getVerdict must fail on hanging server');
  assert.strictEqual(getRes.offline, true, 'getVerdict must report offline: true on timeout');
  assert.ok(getElapsed < 2000, `getVerdict took too long: ${{getElapsed}}ms`);
}})().catch(err => {{
  console.error(err);
  process.exit(1);
}});
""",
            encoding="utf-8",
        )
        proc = subprocess.run([node_bin, str(test_js)], capture_output=True, text=True, timeout=6)
        assert proc.returncode == 0, f"node test failed: {proc.stderr or proc.stdout}"
    finally:
        stop_event.set()
        try:
            srv.close()
        except OSError:
            pass
        for conn in accepted:
            try:
                conn.close()
            except OSError:
                pass
        shutil.rmtree(tmp_dir, ignore_errors=True)


def main() -> int:
    try:
        test_hanging_server_request_timeout()
        from extension.test_extension import run_all
        assert run_all() == 0, "extension/test_extension.py failed"
        print("PASS hanging_server_request_timeout")
        return 0
    except Exception as exc:
        print(f"FAIL hanging_server_request_timeout: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
