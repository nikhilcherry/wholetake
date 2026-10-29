"""Reproducer for F125: _downmix checks stereo phase correlation only in the middle 10s window,
cancelling inverted speech when the middle is quiet."""
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
    root = tempfile.mkdtemp(prefix="test_f125_")
    try:
        # 1. Direct unit test of _downmix with phase-inverted speech at start/end and quiet correlated middle 10s
        n = 24 * FS
        t = np.arange(n, dtype=np.float64) / FS
        speech = 0.5 * np.sin(2.0 * np.pi * 800.0 * t)
        # Silence the middle 10 seconds (7s to 17s)
        speech[7 * FS:17 * FS] = 0.0
        # Low-level correlated room hum across the whole clip (dominates the silent middle 10s)
        hum = 1e-4 * np.sin(2.0 * np.pi * 400.0 * t)
        L = speech + hum
        R = -speech + hum
        x_stereo = np.stack([L, R], axis=1)
        warns: list[str] = []
        mono = wt_detect._downmix(x_stereo, warns)
        active_rms = float(np.sqrt(np.mean(mono[:7 * FS] ** 2)))
        if active_rms < 0.1 or not any("stereo_phase_anomaly" in w for w in warns):
            print(f"FAIL test_F125: _downmix cancelled inverted active speech (active_rms={active_rms:.6f}, warnings={warns})")
            sys.exit(1)

        # 2. End-to-end wt_detect.run() verification on a stamped clip with a quiet middle pause and inverted stereo phase
        key = bytes.fromhex("11" * 32)
        key_file = os.path.join(root, "venue.key")
        with open(key_file, "w", encoding="utf-8") as f:
            f.write(key.hex())
        venue_dir = os.path.join(root, "venue_pa")
        x_feed, out_pa = wt_test.make_venue(venue_dir, "pa", key, seconds=34.0)
        y = wt_test.phone(out_pa, x_feed, "pa", start_s=4.0, dur_s=24.0)
        # Create a 10-second pause in the middle (7s..17s) with tiny in-phase hum, and invert R on active speech
        L_clip = y.copy()
        L_clip[7 * FS:17 * FS] = 0.0
        R_clip = -0.95 * L_clip
        L_clip += hum
        R_clip += hum
        stereo_wav = os.path.join(root, "stereo_pause_mid.wav")
        sf.write(stereo_wav, np.stack([L_clip, R_clip], axis=1), FS, subtype="FLOAT")
        out_json = os.path.join(root, "detection.json")
        doc = wt_detect.run(stereo_wav, out_path=out_json, venue_dir=venue_dir, key_file=key_file, fast=True)
        verdict = doc["verdict"]["recorded_here"]
        l1_status = doc["layers"]["l1"]["status"]
        doc_warns = doc.get("warnings", [])
        if verdict != "verified" or l1_status != "present" or not any("stereo_phase_anomaly" in w for w in doc_warns):
            print(f"FAIL test_F125: wt_detect failed on stereo inverted clip with quiet middle! "
                  f"verdict={verdict}, l1={l1_status}, warnings={doc_warns}")
            sys.exit(1)

        print("PASS test_F125")
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    main()
