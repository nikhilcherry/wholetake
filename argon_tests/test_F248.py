import os
import shutil
import sys
import tempfile
import numpy as np

sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__)) if "__file__" in globals() else "."))

from roomstamp import engine, spec, wavio, wt_detect
from roomstamp.spec import FS


def test_match_feed_sparse_mad_zero_explosion():
    tmp = tempfile.mkdtemp(prefix="test_f248_")
    try:
        rng = np.random.default_rng(248)
        # 1. Sparse venue feed: 60 s total, >80% silence with a few bursts of speech/noise
        # so overall RMS is well above -80 dBFS (~ -22 dBFS), but >50% of _hp_env frames are 0.0.
        feed = np.zeros(60 * FS, dtype=np.float64)
        speech_path = os.path.join("testdata", "speech.wav")
        other_path = os.path.join("testdata", "witness", "other.wav")
        sp = wavio.read(speech_path)
        oth = wavio.read(other_path)

        feed[5 * FS:9 * FS] = sp[0 * FS:4 * FS] * 0.5
        feed[35 * FS:39 * FS] = sp[10 * FS:14 * FS] * 0.5
        feed_wav = os.path.join(tmp, "feed_1793430600.wav")
        wavio.write(feed_wav, feed)

        sl = wt_detect.SessionLog(
            sid="session_1793430600",
            path=os.path.join(tmp, "session_1793430600.jsonl"),
            header={},
            secs=[],
            t_start=1793430600,
            t_end=1793430660,
            key_id="0" * 16,
            params=engine.StampParams(mode="pa"),
            feed_path=feed_wav,
            venue_name="Test Venue",
        )

        # Case A: unrelated sparse clip (20 s with a 4 s burst of unrelated speech)
        clip_sparse = np.zeros(20 * FS, dtype=np.float64)
        clip_sparse[8 * FS:12 * FS] = oth[2 * FS:6 * FS] * 0.5
        warns = []
        wt_detect.match_feed(clip_sparse, sl, warns)
        if sl.env_z >= wt_detect.ENV_Z_MIN:
            print(f"FAIL test_F248: match_feed on unrelated sparse audio exploded to env_z={sl.env_z:.2e} (expected < {wt_detect.ENV_Z_MIN})")
            sys.exit(1)

        # Case B: unrelated continuous speech clip against sparse feed
        sl2 = wt_detect.SessionLog(
            sid="session_1793430600",
            path=os.path.join(tmp, "session_1793430600.jsonl"),
            header={},
            secs=[],
            t_start=1793430600,
            t_end=1793430660,
            key_id="0" * 16,
            params=engine.StampParams(mode="pa"),
            feed_path=feed_wav,
            venue_name="Test Venue",
        )
        clip_dense = oth[:20 * FS] * 0.5
        wt_detect.match_feed(clip_dense, sl2, warns)
        if sl2.env_z >= wt_detect.ENV_Z_MIN:
            print(f"FAIL test_F248: match_feed on unrelated dense clip vs sparse feed got env_z={sl2.env_z:.2e} (expected < {wt_detect.ENV_Z_MIN})")
            sys.exit(1)

        # Case C: genuinely matching clip from the continuous speech feed still locks with env_z >= ENV_Z_MIN
        feed_full_wav = os.path.join(tmp, "feed_full.wav")
        wavio.write(feed_full_wav, sp[:40 * FS])
        sl3 = wt_detect.SessionLog(
            sid="session_full",
            path=os.path.join(tmp, "session_full.jsonl"),
            header={},
            secs=[],
            t_start=1793430600,
            t_end=1793430640,
            key_id="0" * 16,
            params=engine.StampParams(mode="pa"),
            feed_path=feed_full_wav,
            venue_name="Test Venue",
        )
        wt_detect.match_feed(sp[8 * FS:28 * FS], sl3, warns)
        if sl3.env_z < wt_detect.ENV_Z_MIN or abs((sl3.feed_offset_s or 0.0) - 8.0) > 0.2:
            print(f"FAIL test_F248: matching clip failed to lock (env_z={sl3.env_z:.2f}, offset={sl3.feed_offset_s})")
            sys.exit(1)

        print(f"PASS test_F248 (sparse_unrelated_z={sl.env_z:.2f}, dense_unrelated_z={sl2.env_z:.2f}, matched_z={sl3.env_z:.2f})")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    test_match_feed_sparse_mad_zero_explosion()
