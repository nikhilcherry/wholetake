import os
import secrets
import shutil
import sys
import tempfile
import numpy as np

from roomstamp import wavio, wt_detect, wt_test
from roomstamp.spec import FS


def main() -> None:
    tmp = tempfile.mkdtemp(prefix="test_f126_")
    try:
        key = secrets.token_bytes(32)
        kf = os.path.join(tmp, "venue.key")
        with open(kf, "w", encoding="utf-8") as f:
            f.write(key.hex())

        venue_dir = os.path.join(tmp, "venue")
        x, out = wt_test.make_venue(venue_dir, "pa", key, seconds=20.0)
        log_path = os.path.join(venue_dir, f"session_{wt_test.T0}.jsonl")
        sl = wt_detect.load_session_log(log_path, "Test Venue")

        t0 = float(wt_test.T0)
        off_at_t0 = sl.wall(t0) - t0
        off_before = sl.wall(t0 - 0.1) - (t0 - 0.1)
        off_lead_in = sl.wall(t0 - 1.5) - (t0 - 1.5)
        off_after = sl.wall(float(sl.t_end) + 0.2) - (float(sl.t_end) + 0.2)

        if abs(off_at_t0 - 0.012) > 1e-6:
            print(f"FAIL test_F126: expected +0.0120s offset at T0, got {off_at_t0:+.4f}s")
            sys.exit(1)
        if abs(off_before - 0.012) > 1e-6:
            print(
                f"FAIL test_F126: SessionLog.wall() loses DAC UTC offset when clip starts "
                f"slightly before T0 (lead-in): offset at T0 is {off_at_t0:+.4f}s, "
                f"at T0-0.1s is {off_before:+.4f}s"
            )
            sys.exit(1)
        if abs(off_lead_in - 0.012) > 1e-6:
            print(f"FAIL test_F126: expected +0.0120s offset at T0-1.5s, got {off_lead_in:+.4f}s")
            sys.exit(1)
        if abs(off_after - 0.012) > 1e-6:
            print(f"FAIL test_F126: expected +0.0120s offset at t_end+0.2s, got {off_after:+.4f}s")
            sys.exit(1)

        # End-to-end check: clip with 0.5 s pre-T0 lead-in followed by [T0, T0+12s)
        rng = np.random.default_rng(42)
        lead_in = rng.normal(0.0, 0.002, int(0.5 * FS))
        clip_sig = np.concatenate([lead_in, out[: int(12.0 * FS)]])
        clip_path = os.path.join(tmp, "leadin_clip.wav")
        wavio.write(clip_path, clip_sig)

        doc = wt_detect.run(clip_path, venue_dir=venue_dir, key_file=kf, layers="l1", fast=True)
        l1_rec = doc["layers"]["l1"]["recorded_at_unix"]
        verdict_rec = doc["verdict"]["recorded_at_unix"]
        if l1_rec is None or verdict_rec is None or l1_rec >= t0:
            print(f"FAIL test_F126: unexpected recorded_at_unix (l1={l1_rec}, verdict={verdict_rec})")
            sys.exit(1)
        wall_off = verdict_rec - l1_rec
        if abs(wall_off - 0.012) > 1e-6:
            print(f"FAIL test_F126: end-to-end verdict dropped DAC offset: got {wall_off:+.4f}s")
            sys.exit(1)

        print("PASS test_F126")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
