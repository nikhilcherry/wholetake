"""Reproducer for F251: Non-48 kHz venue feed_wav graceful handling in wt_detect."""
import os
import shutil
import sys
import tempfile

import numpy as np
import soundfile as sf
from scipy.signal import resample_poly

from roomstamp import wavio, wt_detect, wt_test


def main() -> None:
    tmp = tempfile.mkdtemp(prefix="test_f251_")
    try:
        key = bytes.fromhex("11" * 32)
        key_file = os.path.join(tmp, "venue.key")
        with open(key_file, "w", encoding="utf-8") as f:
            f.write(key.hex())

        venue_dir = os.path.join(tmp, "venue_44k")
        x, out = wt_test.make_venue(venue_dir, "pa", key, seconds=25.0)

        # Overwrite venue feed with a 44.1 kHz WAV
        feed_path = os.path.join(venue_dir, f"feed_{wt_test.T0}.wav")
        x_44k = resample_poly(x, 147, 160)
        sf.write(feed_path, x_44k, 44100, subtype="PCM_16")

        # Simulate a 15 s clip starting at 4.0 s
        y = wt_test.phone(out, x, "pa", 4.0, 15.0)
        clip_path = os.path.join(tmp, "clip.wav")
        wavio.write(clip_path, y)

        out_json = os.path.join(tmp, "detection.json")
        doc = wt_detect.run(clip_path, out_path=out_json, venue_dir=venue_dir, key_file=key_file, fast=True)

        l1_status = doc["layers"]["l1"]["status"]
        l1_err = doc["layers"]["l1"].get("error")
        if l1_status == "error":
            print(f"FAIL test_non_48k_feed_graceful_handling: 44.1kHz feed_wav caused L1 to crash with status='error' ({l1_err}) instead of resampling or warning")
            sys.exit(1)
        if doc["status"] != "ok":
            print(f"FAIL test_non_48k_feed_graceful_handling: top-level status={doc['status']!r}")
            sys.exit(1)
        if l1_status != "present" or doc["verdict"]["recorded_here"] != "verified":
            print(f"FAIL test_non_48k_feed_graceful_handling: expected L1 present/verified, got l1={l1_status!r}, recorded_here={doc['verdict']['recorded_here']!r}")
            sys.exit(1)
        if not any("44100" in w or "feed_resampled" in w for w in doc.get("warnings", [])):
            print(f"FAIL test_non_48k_feed_graceful_handling: expected warning about 44100 Hz feed, got {doc.get('warnings')}")
            sys.exit(1)

        print("PASS test_non_48k_feed_graceful_handling")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
