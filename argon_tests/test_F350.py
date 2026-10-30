"""Reproducer test for F350: _hp_env squashes speech envelope when clip has >=5% loud transient/mic-bump."""
import os
import secrets
import shutil
import sys
import tempfile
import numpy as np

from roomstamp import wt_detect, wt_test
from roomstamp.spec import FS


def test_f350_hp_env_loud_mic_bump_transient():
    name = "t5_hp_env_loud_mic_bump_transient"
    tmp = tempfile.mkdtemp(prefix="test_f350_")
    try:
        key = secrets.token_bytes(32)
        venue_dir = os.path.join(tmp, "venue")
        feed, out = wt_test.make_venue(venue_dir, "pa", key, seconds=40.0)

        # 20 s phone clip starting at 8.0 s into the session
        y = wt_test.phone(out, feed, "pa", start_s=8.0, dur_s=20.0)
        # Attenuate normal speech so a full-scale mic handling bump is >30 dB (3.0 in log10 power) louder
        clip_x = (y * 0.015).copy()
        # Inject a 1.4 s (7% >= 5% of clip) loud mic bump / transient in the 300-3400 Hz band
        rng = np.random.default_rng(350)
        bump_len = int(1.4 * FS)
        t_bump = np.arange(bump_len) / FS
        bump = 0.95 * np.sin(2 * np.pi * 800.0 * t_bump) + 0.15 * rng.standard_normal(bump_len)
        clip_x[int(2.0 * FS):int(2.0 * FS) + bump_len] = np.clip(bump, -1.0, 1.0)

        sl = wt_detect.load_session_log(
            os.path.join(venue_dir, f"session_{wt_test.T0}.jsonl"), "Test Venue"
        )
        warnings = []
        wt_detect.match_feed(clip_x, sl, warnings)

        if sl.env_z < wt_detect.ENV_Z_MIN or sl.feed_offset_s is None:
            print(f"FAIL {name}: env_z={sl.env_z:.2f} (need >={wt_detect.ENV_Z_MIN}), feed_offset_s={sl.feed_offset_s}")
            sys.exit(1)
        if abs(sl.feed_offset_s - 8.0) > 0.5:
            print(f"FAIL {name}: feed_offset_s={sl.feed_offset_s:.2f} (expected ~8.0 s), env_z={sl.env_z:.2f}")
            sys.exit(1)
        print(f"PASS {name}: env_z={sl.env_z:.2f}, feed_offset_s={sl.feed_offset_s:.2f}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    test_f350_hp_env_loud_mic_bump_transient()
