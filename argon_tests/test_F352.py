"""Reproducer test for F352: l2_window in run() must be clamped to [sl.t_start, sl.t_end]."""
import os
import secrets
import shutil
import sys
import tempfile

from roomstamp import wavio, wt_detect, wt_test


def main() -> None:
    root = tempfile.mkdtemp(prefix="wt_f352_")
    try:
        key = secrets.token_bytes(32)
        kf = os.path.join(root, "venue.key")
        with open(kf, "w", encoding="utf-8") as f:
            f.write(key.hex())

        pa = os.path.join(root, "venue_pa")
        x, out = wt_test.make_venue(pa, "pa", key, seconds=40.0)
        y = wt_test.phone(out, x, "pa", start_s=7.0, dur_s=12.0)
        clip_path = os.path.join(root, "clip.wav")
        wavio.write(clip_path, y)

        session_bounds = (wt_test.T0, wt_test.T0 + 40)
        captured_windows = []
        orig_run_l2 = wt_detect.run_l2

        def stub_run_l2(clip, sl, key_bytes, window, work, timeout_s, allow_network=False):
            captured_windows.append((int(window[0]), int(window[1])))
            return wt_detect._stage(
                "absent",
                "s3",
                "stubbed L2",
                window=[int(window[0]), int(window[1])],
                elapsed_s=0.01,
            )

        wt_detect.run_l2 = stub_run_l2
        try:
            # Scenario A: l2_window derived from L1 recorded_at_unix (with confirm=True)
            wt_detect.run(clip_path, venue_dir=pa, key_file=kf, confirm=True)
            if not captured_windows:
                print("FAIL t8_l2_window_clamped_to_session_bounds: run_l2 not called for L1 branch")
                sys.exit(1)
            w_l1 = captured_windows[-1]
            if w_l1[0] < session_bounds[0] or w_l1[1] > session_bounds[1] or w_l1[0] >= w_l1[1]:
                print(
                    f"FAIL t8_l2_window_clamped_to_session_bounds: "
                    f"l2_window={w_l1} outside session bounds {session_bounds} (L1 branch)"
                )
                sys.exit(1)

            # Scenario B: l2_window derived from sl.feed_offset_s (layers='l2')
            captured_windows.clear()
            wt_detect.run(clip_path, venue_dir=pa, key_file=kf, layers="l2")
            if not captured_windows:
                print("FAIL t8_l2_window_clamped_to_session_bounds: run_l2 not called for feed_offset_s branch")
                sys.exit(1)
            w_feed = captured_windows[-1]
            if w_feed[0] < session_bounds[0] or w_feed[1] > session_bounds[1] or w_feed[0] >= w_feed[1]:
                print(
                    f"FAIL t8_l2_window_clamped_to_session_bounds: "
                    f"l2_window={w_feed} outside session bounds {session_bounds} (feed_offset_s branch)"
                )
                sys.exit(1)
        finally:
            wt_detect.run_l2 = orig_run_l2

        print("PASS t8_l2_window_clamped_to_session_bounds")
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    main()
