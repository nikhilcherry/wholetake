"""Reproducer test for F319: ingest() must not discard Center/higher channels on >2-channel clips."""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import numpy as np
import soundfile as sf

from roomstamp import wt_detect, wt_test
from roomstamp.spec import FS


def main() -> None:
    root = tempfile.mkdtemp(prefix="test_f319_")
    try:
        key = bytes.fromhex("11" * 32)
        kf = os.path.join(root, "venue.key")
        with open(kf, "w", encoding="utf-8") as f:
            f.write(key.hex())

        pa = os.path.join(root, "venue_pa")
        x, out = wt_test.make_venue(pa, "pa", key, seconds=25.0)
        y = wt_test.phone(out, x, "pa", 4.0, 15.0)

        rng = np.random.default_rng(42)
        n = len(y)

        # Case 1: 6-channel (5.1) clip with stamped dialogue on Center channel (ch 2)
        # and quiet uncorrelated ambient noise on FL/FR (ch 0, 1).
        mc = np.zeros((n, 6), dtype=np.float64)
        mc[:, 0] = 1e-3 * rng.standard_normal(n)
        mc[:, 1] = 1e-3 * rng.standard_normal(n)
        mc[:, 2] = y
        mc[:, 3] = 5e-4 * rng.standard_normal(n)
        clip1 = os.path.join(root, "surround_center.wav")
        sf.write(clip1, mc, FS, subtype="PCM_16")

        doc1 = wt_detect.run(clip1, venue_dir=pa, key_file=kf)
        if doc1["clip"]["orig_channels"] != 6:
            print(f"FAIL test_F319: expected orig_channels=6, got {doc1['clip']['orig_channels']}")
            sys.exit(1)
        if doc1["verdict"]["recorded_here"] != "verified" or doc1["layers"]["l1"]["status"] != "present":
            print(
                f"FAIL test_F319: 6-channel (5.1) clip with stamped speech on Center channel (ch 2) "
                f"failed to verify: recorded_here={doc1['verdict']['recorded_here']}, "
                f"l1={doc1['layers']['l1']['status']}, warnings={doc1.get('warnings')}"
            )
            sys.exit(1)

        # Case 2: 6-channel clip where Center (ch 2) has stamped speech and ch 5 has inverted speech
        # (phase-opposed channel must not cancel the reference channel).
        mc2 = np.zeros((n, 6), dtype=np.float64)
        mc2[:, 2] = y
        mc2[:, 5] = -0.95 * y
        clip2 = os.path.join(root, "surround_inverted.wav")
        sf.write(clip2, mc2, FS, subtype="PCM_16")

        doc2 = wt_detect.run(clip2, venue_dir=pa, key_file=kf)
        if doc2["verdict"]["recorded_here"] != "verified" or doc2["layers"]["l1"]["status"] != "present":
            print(
                f"FAIL test_F319: 6-channel clip with inverted surround channel failed to verify: "
                f"recorded_here={doc2['verdict']['recorded_here']}, l1={doc2['layers']['l1']['status']}"
            )
            sys.exit(1)

        print("PASS test_F319")
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    main()
