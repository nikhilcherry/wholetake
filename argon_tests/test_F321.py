import json
import os
import shutil
import sys
import tempfile
import numpy as np

from roomstamp import cli, spec, wavio
from roomstamp.engine import StampParams
from roomstamp.spec import FS


def main() -> None:
    tmp = tempfile.mkdtemp(prefix="argon_f321_")
    try:
        key = bytes.fromhex("11" * 32)
        key_path = os.path.join(tmp, "venue.key")
        with open(key_path, "w", encoding="utf-8") as f:
            f.write(key.hex() + "\n")

        rec_path = os.path.join(tmp, "rec.wav")
        feed_path = os.path.join(tmp, "feed.wav")
        wavio.write(rec_path, np.zeros(FS, dtype=np.float32))
        wavio.write(feed_path, np.zeros(FS, dtype=np.float32))

        valid_header = {
            "type": "session",
            "spec": spec.SPEC_VERSION,
            "key_id": spec.key_id(key),
            "t_start": 1793209200,
            "fs": FS,
            "block": 480,
            "params": StampParams(mode="file").as_log(),
            "feed_wav": "feed.wav",
        }

        bad_cases = [
            ("empty_log", ""),
            ("sec_only_no_session", json.dumps({"type": "sec", "t": 1793209200, "anchored": False}) + "\n"),
        ]

        for req in ("key_id", "feed_wav", "params", "t_start"):
            h = dict(valid_header)
            del h[req]
            bad_cases.append((f"missing_{req}", json.dumps(h) + "\n"))

        malformed_overrides = [
            ("key_id_int", {"key_id": 123}),
            ("key_id_none", {"key_id": None}),
            ("feed_wav_none", {"feed_wav": None}),
            ("feed_wav_empty", {"feed_wav": ""}),
            ("params_str", {"params": "not-a-dict"}),
            ("params_bad_mode", {"params": {"mode": "invalid_mode"}}),
            ("params_unknown_kw", {"params": {"unknown_param": 1}}),
            ("t_start_str", {"t_start": "bad"}),
            ("t_start_zero", {"t_start": 0}),
            ("t_start_bool", {"t_start": True}),
        ]
        for label, ov in malformed_overrides:
            h = dict(valid_header)
            h.update(ov)
            bad_cases.append((label, json.dumps(h) + "\n"))

        for label, content in bad_cases:
            log_path = os.path.join(tmp, f"{label}.jsonl")
            with open(log_path, "w", encoding="utf-8") as f:
                f.write(content)
            try:
                cli.main(["detect", rec_path, "--log", log_path, "--feed", feed_path, "--key-file", key_path])
            except SystemExit:
                pass
            except Exception as e:
                print(f"FAIL test_F321: case {label} raised {type(e).__name__}: {e}")
                sys.exit(1)
            else:
                print(f"FAIL test_F321: case {label} did not raise SystemExit")
                sys.exit(1)

        # Verify a valid log still succeeds cleanly
        good_log = os.path.join(tmp, "good.jsonl")
        with open(good_log, "w", encoding="utf-8") as f:
            f.write(json.dumps(valid_header) + "\n")
        try:
            cli.main(["detect", rec_path, "--log", good_log, "--feed", feed_path, "--key-file", key_path, "--json"])
        except Exception as e:
            print(f"FAIL test_F321: valid log raised {type(e).__name__}: {e}")
            sys.exit(1)

        print("PASS test_F321")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
