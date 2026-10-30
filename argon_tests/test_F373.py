"""Reproducer test for F373: watermark-only cut propagation in formatVerdictSummary."""
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
API_JS = REPO_ROOT / "extension" / "api.js"


def test_01_watermark_only_cut_propagation() -> None:
    assert API_JS.is_file(), f"Missing {API_JS}"
    node_bin = shutil.which("node")
    assert node_bin, "node binary required to test extension/api.js"

    tmp_dir = Path(tempfile.mkdtemp())
    try:
        runner = tmp_dir / "check_f373.js"
        runner.write_text(
            f"""
const assert = require('assert');
const api = require({json.dumps(str(API_JS))});

// Case 1: Unmatched witness stream ('not_found'), L1 Roomstamp phase cut detected
const jobNotFoundWithStampCut = {{
  id: 'job_stamp_cut_1',
  status: 'done',
  result: {{
    verdict_id: 'job_stamp_cut_1',
    page_url: '/v/job_stamp_cut_1',
    detection: {{
      stamp: {{
        present: true,
        venue: 'main-hall',
        recorded_at_utc: '2026-10-31T06:12:07Z',
        layer1_score: 0.95,
        layer2_score: null,
        cuts: [{{ at_clip_s: 12.3, removed_s: 0.6 }}],
      }},
      witness: {{
        verdict: 'not_found',
        stream: null,
        removed: [],
      }},
    }},
  }},
}};
const sum1 = api.formatVerdictSummary(jobNotFoundWithStampCut, 'http://127.0.0.1:8790');
if (sum1.verdict !== 'cut' && sum1.verdict !== 'edited') {{
  throw new Error("verdict should reflect cut/edited when stamp.cuts is non-empty, got '" + sum1.verdict + "'");
}}
assert.strictEqual(sum1.cuts.length, 1);

// Case 2: Matched witness stream ('faithful', cut inside untranscribed pause), L1 Roomstamp cut detected
const jobFaithfulPauseCut = {{
  id: 'job_stamp_cut_2',
  status: 'done',
  result: {{
    verdict_id: 'job_stamp_cut_2',
    page_url: '/v/job_stamp_cut_2',
    detection: {{
      stamp: {{
        present: true,
        venue: 'main-hall',
        cuts: [{{ at_clip_s: 8.5, removed_s: 0.4 }}],
      }},
      witness: {{
        verdict: 'faithful',
        stream: 'aje',
        removed: [],
      }},
    }},
  }},
}};
const sum2 = api.formatVerdictSummary(jobFaithfulPauseCut, 'http://127.0.0.1:8790');
if (sum2.verdict !== 'cut' && sum2.verdict !== 'edited') {{
  throw new Error("verdict should reflect cut/edited on pause cut when stamp.cuts is non-empty, got '" + sum2.verdict + "'");
}}
assert.strictEqual(sum2.cuts.length, 1);

// Case 3: Truly faithful clip with no cuts anywhere
const jobFaithfulUncut = {{
  id: 'job_uncut_3',
  status: 'done',
  result: {{
    verdict_id: 'job_uncut_3',
    page_url: '/v/job_uncut_3',
    detection: {{
      stamp: {{
        present: true,
        venue: 'main-hall',
        cuts: [],
      }},
      witness: {{
        verdict: 'faithful',
        stream: 'aje',
        removed: [],
      }},
    }},
  }},
}};
const sum3 = api.formatVerdictSummary(jobFaithfulUncut, 'http://127.0.0.1:8790');
assert.strictEqual(sum3.verdict, 'faithful');
assert.strictEqual(sum3.cuts.length, 0);
""",
            encoding="utf-8",
        )
        proc = subprocess.run([node_bin, str(runner)], capture_output=True, text=True, timeout=15)
        if proc.returncode != 0:
            msg = (proc.stderr or proc.stdout or "node script failed").strip().splitlines()[-1]
            raise AssertionError(msg)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


if __name__ == "__main__":
    try:
        test_01_watermark_only_cut_propagation()
        print("PASS test_01_watermark_only_cut_propagation")
        sys.exit(0)
    except Exception as exc:
        print(f"FAIL test_01_watermark_only_cut_propagation: {exc}")
        sys.exit(1)
