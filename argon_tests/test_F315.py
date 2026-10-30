"""Reproducer test for F315: pollVerdict negative or zero timing options."""
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
API_JS = REPO_ROOT / "extension" / "api.js"


def test_01_poll_verdict_timing_chaos() -> None:
    node_bin = shutil.which("node")
    assert node_bin, "node binary required for extension/api.js test"
    tmp_dir = Path(tempfile.mkdtemp())
    try:
        runner = tmp_dir / "test_f315.js"
        runner.write_text(
            f"""
const assert = require('assert');
const api = require({json.dumps(str(API_JS))});

(async () => {{
  let calls = 0;
  const mockFetch = async () => {{
    calls += 1;
    return {{
      ok: true,
      status: 200,
      text: async () => JSON.stringify({{ id: 'job123', status: 'queued' }}),
      json: async () => ({{ id: 'job123', status: 'queued' }}),
    }};
  }};

  // 1. Negative intervalMs and negative timeoutMs must not hang or tight-loop
  const t0 = Date.now();
  const resNeg = await Promise.race([
    api.pollVerdict('http://127.0.0.1:8790', 'job123', {{
      intervalMs: -500,
      timeoutMs: -10,
      fetchImpl: mockFetch,
    }}),
    new Promise((_, rej) => setTimeout(() => rej(new Error('pollVerdict hung on negative options')), 1500)),
  ]);
  const elapsedNeg = Date.now() - t0;
  assert.strictEqual(resNeg.ok, false, 'expected queued job with negative timeoutMs to time out');
  assert.ok(elapsedNeg < 1000, `expected fast termination (<1000ms), took ${{elapsedNeg}}ms`);
  assert.ok(calls >= 1 && calls <= 3, `expected bounded poll count, got ${{calls}}`);

  // 2. Zero intervalMs and zero timeoutMs must not hang or tight-loop
  calls = 0;
  const t1 = Date.now();
  const resZero = await Promise.race([
    api.pollVerdict('http://127.0.0.1:8790', 'job123', {{
      intervalMs: 0,
      timeoutMs: 0,
      fetchImpl: mockFetch,
    }}),
    new Promise((_, rej) => setTimeout(() => rej(new Error('pollVerdict hung on zero options')), 1500)),
  ]);
  const elapsedZero = Date.now() - t1;
  assert.strictEqual(resZero.ok, false, 'expected queued job with zero timeoutMs to time out');
  assert.ok(elapsedZero < 1000, `expected fast termination (<1000ms), took ${{elapsedZero}}ms`);
  assert.ok(calls >= 1 && calls <= 3, `expected bounded poll count on zero options, got ${{calls}}`);

  // 3. Negative intervalMs with small positive timeoutMs must clamp intervalMs >= 10 (no tight loop)
  calls = 0;
  const t2 = Date.now();
  const resClampInterval = await api.pollVerdict('http://127.0.0.1:8790', 'job123', {{
    intervalMs: -500,
    timeoutMs: 60,
    fetchImpl: mockFetch,
  }});
  const elapsedClamp = Date.now() - t2;
  assert.strictEqual(resClampInterval.ok, false);
  assert.ok(elapsedClamp < 1000, `expected <1000ms, took ${{elapsedClamp}}ms`);
  assert.ok(calls <= 12, `expected intervalMs clamped to >=10ms (<=12 calls in 60ms), got ${{calls}}`);
}})().catch(err => {{
  console.error(err);
  process.exit(1);
}});
""",
            encoding="utf-8",
        )
        proc = subprocess.run([node_bin, str(runner)], capture_output=True, text=True, timeout=10)
        if proc.returncode != 0:
            raise AssertionError(f"Node runner failed: {proc.stderr or proc.stdout}")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def main() -> int:
    try:
        test_01_poll_verdict_timing_chaos()
        print("PASS test_01_poll_verdict_timing_chaos")
    except Exception as exc:
        print(f"FAIL test_01_poll_verdict_timing_chaos: {exc}")
        return 1

    from extension.test_extension import run_all
    if run_all() != 0:
        print("FAIL extension_test_suite: regression in extension/test_extension.py")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
