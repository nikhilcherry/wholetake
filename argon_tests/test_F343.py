import os
import secrets
import shutil
import sys
import tempfile
import numpy as np

from roomstamp import wavio, wt_detect, wt_test
from roomstamp.spec import FS


def main() -> None:
    os.environ["WHOLETAKE_ASR"] = "none"
    root = tempfile.mkdtemp(prefix="test_f343_")
    try:
        key = secrets.token_bytes(32)
        kf = os.path.join(root, "venue.key")
        with open(kf, "w", encoding="utf-8") as f:
            f.write(key.hex())

        pa = os.path.join(root, "venue_pa")
        wt_test.make_venue(pa, "pa", key, seconds=12.0)

        rng = np.random.default_rng(343)
        t = np.arange(int(5.0 * FS)) / FS
        unrelated = 0.2 * np.sin(2 * np.pi * 440.0 * t) + 0.05 * rng.standard_normal(len(t))
        clip_path = os.path.join(root, "short_5s.wav")
        wavio.write(clip_path, unrelated)

        out_json = os.path.join(root, "det.json")
        doc = wt_detect.run(clip_path, out_path=out_json, venue_dir=pa, key_file=kf, fast=True)

        l2_status = doc["layers"]["l2"]["status"]
        pending = doc["pending_layers"]
        headline = doc["verdict"]["headline"]

        if l2_status != "too_short":
            print(f"FAIL test_F343: expected layers.l2.status == 'too_short', got {l2_status!r}")
            sys.exit(1)
        if pending != []:
            print(f"FAIL test_F343: expected pending_layers == [], got {pending!r}")
            sys.exit(1)
        if "Still checking the backup layer" in headline:
            print(f"FAIL test_F343: unexpected 'Still checking the backup layer' in headline: {headline!r}")
            sys.exit(1)
        if "too short (5.0 s) for the backup check (needs 8 s)" not in headline:
            print(f"FAIL test_F343: expected too_short headline, got: {headline!r}")
            sys.exit(1)

        print("PASS test_F343")
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    main()
