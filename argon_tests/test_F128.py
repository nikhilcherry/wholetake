import os
import secrets
import shutil
import sys
import tempfile
import numpy as np

from roomstamp import selftest, wavio, wt_detect
from roomstamp.spec import FS
from roomstamp.wt_test import T0, make_venue, phone


def main() -> int:
    root = tempfile.mkdtemp(prefix="wt_f128_")
    failed = False
    try:
        key = secrets.token_bytes(32)
        kf = os.path.join(root, "venue.key")
        with open(kf, "w", encoding="utf-8") as f:
            f.write(key.hex())

        pa = os.path.join(root, "venue_pa")
        x, out = make_venue(pa, "pa", key, seconds=30.0)
        venue_clip = phone(out, x, "pa", 8.0, 18.0)
        want_start = T0 + 8.0 + 0.012

        other_path = os.path.join(selftest.HERE, "testdata", "witness", "other.wav")
        foreign = wavio.read(other_path)[:5 * FS]
        foreign = foreign / (np.max(np.abs(foreign)) + 1e-9) * 0.5

        # Test 1: 5 s of foreign speech prepended to 18 s venue clip
        clip_in_path = os.path.join(root, "lead_in_foreign.wav")
        wavio.write(clip_in_path, np.concatenate([foreign, venue_clip]))
        doc1 = wt_detect.run(clip_in_path, venue_dir=pa, key_file=kf, fast=True)
        v1 = doc1["verdict"]
        edits1 = v1.get("edits") or []
        foreign_edits1 = [e for e in edits1 if e.get("kind") == "foreign"]
        rec_at1 = v1.get("recorded_at_unix") or 0.0
        rec_err1 = rec_at1 - want_start
        if (
            v1.get("recorded_here") == "verified"
            and v1.get("integrity") == "edited"
            and foreign_edits1
            and abs((foreign_edits1[0].get("duration_s") or 0.0) - 5.0) <= 0.6
            and abs(rec_err1) <= 0.5
        ):
            print("PASS test_01_foreign_lead_in_integrity_and_time")
        else:
            print(
                f"FAIL test_01_foreign_lead_in_integrity_and_time: "
                f"recorded_here={v1.get('recorded_here')!r}, integrity={v1.get('integrity')!r}, "
                f"rec_at_err={rec_err1:+.2f}s, edits={edits1}, headline={v1.get('headline')!r}"
            )
            failed = True

        # Test 2: 5 s of foreign speech appended to 18 s venue clip
        clip_out_path = os.path.join(root, "outro_foreign.wav")
        wavio.write(clip_out_path, np.concatenate([venue_clip, foreign]))
        doc2 = wt_detect.run(clip_out_path, venue_dir=pa, key_file=kf, fast=True)
        v2 = doc2["verdict"]
        edits2 = v2.get("edits") or []
        foreign_edits2 = [e for e in edits2 if e.get("kind") == "foreign"]
        if (
            v2.get("recorded_here") == "verified"
            and v2.get("integrity") == "edited"
            and foreign_edits2
            and abs((foreign_edits2[-1].get("duration_s") or 0.0) - 5.0) <= 0.6
        ):
            print("PASS test_02_foreign_outro_not_intact")
        else:
            print(
                f"FAIL test_02_foreign_outro_not_intact: "
                f"recorded_here={v2.get('recorded_here')!r}, integrity={v2.get('integrity')!r}, "
                f"edits={edits2}, headline={v2.get('headline')!r}"
            )
            failed = True
    finally:
        shutil.rmtree(root, ignore_errors=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
