import json
import os
import shutil
import sys
import tempfile

from roomstamp.cli import read_log


def test_read_log_truncated_last_line():
    tmp = tempfile.mkdtemp()
    try:
        p = os.path.join(tmp, "session_test.jsonl")
        header = {
            "type": "session",
            "spec": "roomstamp-v1",
            "key_id": "0123456789abcdef",
            "t_start": 1000,
            "warmup": {"t": 999, "round": 10, "anchored": False, "randomness": None},
        }
        sec1 = {"type": "sec", "t": 1000, "round": 10, "anchored": False, "randomness": None}
        sec2 = {"type": "sec", "t": 1001, "round": 11, "anchored": False, "randomness": None}

        with open(p, "w", encoding="utf-8") as f:
            f.write(json.dumps(header) + "\n\n   \n")
            f.write(json.dumps(sec1) + "\n")
            f.write(json.dumps(sec2)[:44])

        h, secs = read_log(p)
        assert h is not None and h["t_start"] == 1000, f"unexpected header: {h}"
        assert 999 in secs and 1000 in secs, f"missing expected seconds: {secs}"
        assert 1001 not in secs, "truncated record should have been ignored"

        with open(p, "a", encoding="utf-8") as f:
            f.write("\n   \n")
        h2, secs2 = read_log(p)
        assert h2["t_start"] == 1000 and 1000 in secs2
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    try:
        test_read_log_truncated_last_line()
        print("PASS test_read_log_truncated_last_line")
    except Exception as e:
        print(f"FAIL test_read_log_truncated_last_line: {type(e).__name__}: {e}")
        sys.exit(1)
