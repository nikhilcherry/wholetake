import json
import os
import secrets
import shutil
import sys
import tempfile
import numpy as np

from roomstamp import engine, spec, wavio, wt_detect
from roomstamp.spec import FS


def main() -> None:
    name = "l2_window_ignores_l1_speech_match_when_l1_stamp_absent"
    root = tempfile.mkdtemp(prefix="test_f241_")
    try:
        key = secrets.token_bytes(32)
        key_file = os.path.join(root, "venue.key")
        with open(key_file, "w", encoding="utf-8") as f:
            f.write(key.hex())

        venue_dir = os.path.join(root, "logs")
        os.makedirs(venue_dir, exist_ok=True)
        t0 = 1793430500
        p = engine.StampParams(mode="pa")
        feed_wav = os.path.join(venue_dir, f"feed_{t0}.wav")
        wavio.write(feed_wav, np.zeros(20 * FS, dtype=np.float64))

        sess_path = os.path.join(venue_dir, f"session_{t0}.jsonl")
        with open(sess_path, "w", encoding="utf-8") as f:
            f.write(json.dumps({
                "type": "session",
                "spec": spec.SPEC_VERSION,
                "key_id": spec.key_id(key),
                "t_start": t0,
                "fs": FS,
                "block": engine.BLOCK,
                "params": p.as_log(),
                "feed_wav": f"feed_{t0}.wav",
            }) + "\n")
            for dt in range(500):
                t = t0 + dt
                f.write(json.dumps({
                    "type": "sec",
                    "t": t,
                    "round": spec.round_for(t),
                    "anchored": True,
                    "randomness": "00" * 32,
                    "anchor": "11" * 32,
                    "dac_utc": float(t),
                }) + "\n")

        clip_path = os.path.join(root, "clip.wav")
        rng = np.random.default_rng(42)
        wavio.write(clip_path, 0.05 * rng.standard_normal(12 * FS))

        orig_match_feed = wt_detect.match_feed
        orig_run_l1 = wt_detect.run_l1
        orig_run_l2 = wt_detect.run_l2
        captured = {}

        def fake_match_feed(clip_x, sl, warnings):
            sl.env_z = 8.0
            sl.feed_offset_s = 110.0
            sl.feed_span_s = (110.0, 122.0)

        def fake_run_l1(clip, sl, k, warnings):
            st = wt_detect._stage(
                "absent",
                "s2",
                "speech matched but L1 stamp absent",
                recorded_at_unix=1793430800.0,
                venue_t_start=1793430800.0,
                venue_t_end=1793430812.0,
                content_match=True,
                integrity_checked=True,
            )
            return st, None

        def fake_run_l2(clip, sl, k, window, work, timeout_s, allow_network=False):
            captured["window"] = list(window)
            return wt_detect._stage("absent", "s3", "mock l2", window=list(window))

        wt_detect.match_feed = fake_match_feed
        wt_detect.run_l1 = fake_run_l1
        wt_detect.run_l2 = fake_run_l2
        try:
            wt_detect.run(clip_path, venue_dir=venue_dir, key_file=key_file)
        finally:
            wt_detect.match_feed = orig_match_feed
            wt_detect.run_l1 = orig_run_l1
            wt_detect.run_l2 = orig_run_l2

        win = captured.get("window")
        expected = [int(1793430800 - 30), int(1793430800 + 12.0 * 1.6 + 30)]
        if win != expected:
            print(f"FAIL {name}: L1 matched the speech at 1793430800 (content_match=True, status='absent'), "
                  f"but l2_window ignored l1['recorded_at_unix'] and searched {win} instead of {expected}!")
            sys.exit(1)
        print(f"PASS {name}")
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    main()
