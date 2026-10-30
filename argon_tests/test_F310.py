import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import numpy as np
from roomstamp import blind, wavio


def run_cli(argv):
    err = io.StringIO()
    out = io.StringIO()
    code = 0
    with contextlib.redirect_stderr(err), contextlib.redirect_stdout(out):
        try:
            blind.main(argv)
        except SystemExit as e:
            code = e.code if isinstance(e.code, int) else 1
    return code, out.getvalue(), err.getvalue()


def main():
    tmp = tempfile.mkdtemp(prefix="test_f310_")
    try:
        key_path = os.path.join(tmp, "venue.key")
        with open(key_path, "w", encoding="utf-8") as f:
            f.write("11" * 32 + "\n")

        clip_path = os.path.join(tmp, "clip.wav")
        wavio.write(clip_path, np.zeros(48000, dtype=np.float32), 48000)

        empty_log = os.path.join(tmp, "empty.jsonl")
        with open(empty_log, "w", encoding="utf-8") as f:
            pass

        header_only_log = os.path.join(tmp, "header_only.jsonl")
        with open(header_only_log, "w", encoding="utf-8") as f:
            f.write(json.dumps({"type": "session", "spec": "roomstamp-v1", "t_start": 1700000000}) + "\n")

        for label, log_file in [("empty", empty_log), ("header_only", header_only_log)]:
            code, _, err = run_cli([clip_path, "--key-file", key_path, "--log", log_file])
            if code != 2:
                print(f"FAIL test_F310: expected exit code 2 for {label} log, got {code}; stderr={err!r}")
                sys.exit(1)
            if "give --log" in err:
                print(f"FAIL test_F310: CLI falsely claimed '--log' was not given for {label} log: {err!r}")
                sys.exit(1)
            if "no recorded seconds" not in err.lower():
                print(f"FAIL test_F310: expected 'no recorded seconds' in stderr for {label} log, got: {err!r}")
                sys.exit(1)

        code_no_args, _, err_no_args = run_cli([clip_path, "--key-file", key_path])
        if code_no_args != 2 or "give --log, --around, or --from/--to" not in err_no_args:
            print(f"FAIL test_F310: missing-window error broken: code={code_no_args}, stderr={err_no_args!r}")
            sys.exit(1)

        print("PASS test_F310")
        sys.exit(0)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
