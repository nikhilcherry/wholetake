"""Reproducer test for F349: balanced differential / inverted stereo feed_wav in _feed_env and _read_feed."""
from __future__ import annotations

import os
import secrets
import shutil
import sys
import tempfile

import numpy as np
import soundfile as sf

from roomstamp import wavio, wt_detect, wt_test
from roomstamp.spec import FS


def main() -> None:
    root = tempfile.mkdtemp(prefix="test_f349_")
    try:
        key = secrets.token_bytes(32)
        kf = os.path.join(root, "venue.key")
        with open(kf, "w", encoding="utf-8") as f:
            f.write(key.hex())

        pa = os.path.join(root, "venue_pa")
        x, out = wt_test.make_venue(pa, "pa", key, seconds=32.0)
        feed_path = os.path.join(pa, f"feed_{wt_test.T0}.wav")

        # Overwrite venue feed_wav as a balanced differential stereo file (L = -R)
        sf.write(feed_path, np.stack([x, -x], axis=1), FS, subtype="PCM_16")

        y = wt_test.phone(out, x, "pa", 5.0, 20.0, cuts=[(10.0, 0.30)])
        clip_path = os.path.join(root, "clip.wav")
        wavio.write(clip_path, y)

        out_json = os.path.join(root, "detection.json")
        doc = wt_detect.run(clip_path, out_path=out_json, venue_dir=pa, key_file=kf, fast=True)

        warns = doc.get("warnings", [])
        l1_status = doc.get("layers", {}).get("l1", {}).get("status")
        verdict = doc.get("verdict", {})
        edits = verdict.get("edits", [])

        if any("feed_silent" in w for w in warns) or l1_status != "present":
            print(f"FAIL t4_stereo_differential_feed_wav: l1={l1_status}, warnings={warns}")
            sys.exit(1)
        if verdict.get("recorded_here") != "verified" or verdict.get("integrity") != "edited":
            print(f"FAIL t4_stereo_differential_feed_wav: verdict={verdict}")
            sys.exit(1)
        if len(edits) != 1 or edits[0].get("kind") != "removed" or abs((edits[0].get("removed_s") or 0.0) - 0.30) > 0.015:
            print(f"FAIL t4_stereo_differential_feed_wav: edits={edits}")
            sys.exit(1)
        restored_rel = edits[0].get("restored_audio")
        restored_path = os.path.join(root, restored_rel) if restored_rel else None
        if not restored_path or not os.path.isfile(restored_path):
            print(f"FAIL t4_stereo_differential_feed_wav: missing restored_audio={restored_rel}")
            sys.exit(1)
        rx, _ = sf.read(restored_path, dtype="float64")
        if float(np.sqrt(np.mean(rx * rx))) < 1e-3:
            print("FAIL t4_stereo_differential_feed_wav: restored_audio is silent")
            sys.exit(1)

        print("PASS t4_stereo_differential_feed_wav")
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    main()
