import argparse
import json
import os
import shutil
import sys
import tempfile
import numpy as np

from roomstamp import cli, engine, spec, wavio
from roomstamp.spec import FS


def main() -> None:
    tmp = tempfile.mkdtemp(prefix="test_f360_")
    errors = []
    try:
        key = bytes.fromhex("11" * 32)
        key_path = os.path.join(tmp, "venue.key")
        with open(key_path, "w", encoding="utf-8") as f:
            f.write(key.hex() + "\n")

        feed_path = os.path.join(tmp, "feed.wav")
        rec_path = os.path.join(tmp, "rec.wav")
        rng = np.random.default_rng(42)
        audio = (rng.standard_normal(FS) * 0.05).astype(np.float32)
        wavio.write(feed_path, audio)
        wavio.write(rec_path, audio)

        base_header = {
            "type": "session",
            "spec": spec.SPEC_VERSION,
            "key_id": spec.key_id(key),
            "t_start": 1700000000,
            "fs": FS,
            "block": engine.BLOCK,
            "params": engine.StampParams().as_log(),
            "feed_wav": "feed.wav",
            "warmup": {"t": 1699999999, "round": 1, "anchored": False, "randomness": None},
        }
        sec_line = {
            "type": "sec",
            "t": 1700000000,
            "round": 1,
            "anchored": False,
            "randomness": None,
            "anchor": "00" * 32,
            "ref_rms": 0.1,
            "gain": 1.0,
        }

        def make_args(log_file: str) -> argparse.Namespace:
            return argparse.Namespace(
                rec=rec_path,
                log=log_file,
                feed=feed_path,
                key_file=key_path,
                start=None,
                dur=30.0,
                json=True,
                check_drand=False,
            )

        # 1. Mid-file corrupt JSONL line should not crash read_log or cmd_detect with JSONDecodeError
        corrupt_log = os.path.join(tmp, "mid_corrupt.jsonl")
        with open(corrupt_log, "w", encoding="utf-8") as f:
            f.write(json.dumps(base_header) + "\n")
            f.write("{corrupt_mid_file_line\n")
            f.write(json.dumps(sec_line) + "\n")

        try:
            cli.cmd_detect(make_args(corrupt_log))
        except SystemExit:
            pass
        except Exception as e:
            errors.append(f"cmd_detect crashed with raw {type(e).__name__} on mid-file corrupt line: {e}")

        # 2. Malformed headers (#0..#4) must raise SystemExit, never raw TypeError/ValueError or accept bool t_start
        bad_headers = [
            {**base_header, "params": {**base_header["params"], "injected_field": 1}},
            {**base_header, "params": "not_a_mapping"},
            {**base_header, "t_start": -5},
            {**base_header, "t_start": True},
            {**base_header, "t_start": "not_an_int"},
        ]
        for idx, bh in enumerate(bad_headers):
            bh_log = os.path.join(tmp, f"bad_header_{idx}.jsonl")
            with open(bh_log, "w", encoding="utf-8") as f:
                f.write(json.dumps(bh) + "\n")
                f.write(json.dumps(sec_line) + "\n")
            try:
                cli.cmd_detect(make_args(bh_log))
                errors.append(f"cmd_detect accepted bad header #{idx}: {bh}")
            except SystemExit:
                pass
            except Exception as e:
                errors.append(f"cmd_detect crashed with raw {type(e).__name__} on bad header #{idx}: {e}")

    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if errors:
        print("FAIL test_F360: " + "; ".join(errors))
        sys.exit(1)
    print("PASS test_F360")


if __name__ == "__main__":
    main()
