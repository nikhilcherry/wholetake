import hashlib
import json
import os
import secrets
import shutil
import sys
import tempfile
from unittest.mock import patch

import numpy as np

from roomstamp import engine, spec, wavio, wt_detect
from roomstamp.spec import FS


def main():
    root = tempfile.mkdtemp(prefix="wt_f137_")
    try:
        key = secrets.token_bytes(32)
        kf = os.path.join(root, "venue.key")
        with open(kf, "w", encoding="utf-8") as f:
            f.write(key.hex())

        venue_dir = os.path.join(root, "logs")
        os.makedirs(venue_dir, exist_ok=True)
        t0 = 1793430600
        dur_session = 10800  # 3-hour session
        p = engine.StampParams(mode="pa")
        sid_path = os.path.join(venue_dir, f"session_{t0}.jsonl")
        with open(sid_path, "w", encoding="utf-8") as f:
            f.write(json.dumps({
                "type": "session",
                "spec": spec.SPEC_VERSION,
                "key_id": spec.key_id(key),
                "t_start": t0,
                "fs": FS,
                "block": engine.BLOCK,
                "params": p.as_log(),
                "round_lag": spec.ROUND_LAG,
                "drand_chain": spec.DRAND_CHAIN,
                "feed_wav": None,
                "feed_source": "none",
            }) + "\n")
            f.write(json.dumps({"type": "sec", "t": t0, "round": spec.round_for(t0), "anchored": False, "randomness": None}) + "\n")
            f.write(json.dumps({"type": "sec", "t": t0 + dur_session - 1, "round": spec.round_for(t0 + dur_session - 1), "anchored": False, "randomness": None}) + "\n")

        clip_path = os.path.join(root, "clip.wav")
        rng = np.random.default_rng(42)
        wavio.write(clip_path, (rng.standard_normal(10 * FS) * 0.05).astype(np.float64))

        captured_windows = []

        def fake_run_l2(clip, sl, k, window, work, timeout_s, allow_network=False):
            captured_windows.append(window)
            return wt_detect._stage("absent", "s3", "mocked", window=[int(window[0]), int(window[1])])

        with patch.object(wt_detect, "run_l2", side_effect=fake_run_l2):
            # 1. With --around inside a 3-hour session without feed
            around_t = t0 + 3600
            wt_detect.run(clip_path, venue_dir=venue_dir, key_file=kf, around=around_t)
            w_around = captured_windows[-1]
            if w_around != (around_t - 1800, around_t + 600):
                print(f"FAIL F137_around: expected {(around_t - 1800, around_t + 600)}, got {w_around}")
                sys.exit(1)

            # 2. Without --around on a 3-hour session: must still be capped to <= L2_MAX_WINDOW_S
            wt_detect.run(clip_path, venue_dir=venue_dir, key_file=kf)
            w_uncapped = captured_windows[-1]
            span = w_uncapped[1] - w_uncapped[0]
            if span > 2400:
                print(f"FAIL F137_cap: window {w_uncapped} span {span}s exceeds 2400s cap")
                sys.exit(1)

        print("PASS F137_l2_window_bounded")
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    main()
