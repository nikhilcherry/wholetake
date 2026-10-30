"""Reproducer test for F316: getVerdict must reject JSON array payloads."""
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
API_JS = REPO_ROOT / "extension" / "api.js"


def test_f316() -> None:
    assert API_JS.is_file(), f"Missing {API_JS}"
    node_bin = shutil.which("node")
    assert node_bin, "node binary is required to test extension/api.js"

    tmp_dir = Path(tempfile.mkdtemp())
    try:
        runner = tmp_dir / "repro_f316.js"
        runner.write_text(
            f"""
const assert = require('assert');
const api = require({json.dumps(str(API_JS))});

(async () => {{
  const invalidBodies = ['[]', '[1, 2, 3]', '[{{"id":"deadbeef","status":"done"}}]', 'null', '"hello"', '42'];
  for (const body of invalidBodies) {{
    const res = await api.getVerdict('http://127.0.0.1:8790', 'deadbeef', {{
      fetchImpl: async () => ({{
        ok: true,
        status: 200,
        text: async () => body,
        json: async () => JSON.parse(body),
      }}),
    }});
    assert.strictEqual(
      res.ok,
      false,
      `getVerdict should reject non-object body=${{body}}, got ${{JSON.stringify(res)}}`
    );
    assert.strictEqual(res.offline, false);
    assert.strictEqual(res.status, 200);
    assert.ok(typeof res.error === 'string' && res.error.length > 0);
  }}

  let polls = 0;
  const pollRes = await api.pollVerdict('http://127.0.0.1:8790', 'deadbeef', {{
    intervalMs: 5,
    timeoutMs: 500,
    fetchImpl: async () => {{
      polls += 1;
      return {{
        ok: true,
        status: 200,
        text: async () => '[]',
        json: async () => [],
      }};
    }},
  }});
  assert.strictEqual(pollRes.ok, false, 'pollVerdict must fail on array response');
  assert.strictEqual(polls, 1, 'pollVerdict must fail fast on malformed array payload without looping');

  const validRes = await api.getVerdict('http://127.0.0.1:8790', 'deadbeef', {{
    fetchImpl: async () => ({{
      ok: true,
      status: 200,
      text: async () => JSON.stringify({{ id: 'deadbeef', status: 'done', result: {{}} }}),
      json: async () => ({{ id: 'deadbeef', status: 'done', result: {{}} }}),
    }}),
  }});
  assert.strictEqual(validRes.ok, true);
  assert.strictEqual(validRes.job.id, 'deadbeef');
}})().catch((err) => {{
  console.error(err);
  process.exit(1);
}});
""",
            encoding="utf-8",
        )
        proc = subprocess.run([node_bin, str(runner)], capture_output=True, text=True, timeout=15)
        if proc.returncode != 0:
            raise AssertionError(proc.stderr.strip() or proc.stdout.strip() or "node exited non-zero")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


if __name__ == "__main__":
    try:
        test_f316()
        print("PASS test_F316")
        sys.exit(0)
    except Exception as exc:
        print(f"FAIL test_F316: {exc}")
        sys.exit(1)
