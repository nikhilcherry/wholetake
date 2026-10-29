"""Reproducer for F270: match_feed rejects clips with >50% foreign intro/outro near feed boundaries."""
import os
import shutil
import sys
import tempfile

import numpy as np

from roomstamp import engine, selftest, wavio, wt_detect
from roomstamp.spec import FS


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="test_f270_")
    try:
        feed = selftest.load_speech(None, 40.0)
        feed_path = os.path.join(tmp, "feed_1000.wav")
        wavio.write(feed_path, feed)

        other_path = os.path.join(selftest.HERE, "testdata", "witness", "other.wav")
        foreign = wavio.read(other_path)[:12 * FS]
        if len(foreign) < 12 * FS:
            foreign = np.tile(foreign, int(np.ceil(12 * FS / len(foreign))))[:12 * FS]

        # Case 1: 12 s foreign intro (60% of 20 s clip) + first 8 s of venue feed (true lag = -12.0 s)
        clip_intro = np.concatenate([foreign, feed[:8 * FS]])
        sl_intro = wt_detect.SessionLog(
            sid="session_1000",
            path=os.path.join(tmp, "session_1000.jsonl"),
            header={},
            secs=[],
            t_start=1000,
            t_end=1040,
            key_id="0" * 16,
            params=engine.StampParams(),
            feed_path=feed_path,
            venue_name="Test Venue",
        )
        warnings = []
        wt_detect.match_feed(clip_intro, sl_intro, warnings)
        if sl_intro.feed_offset_s is None or abs(sl_intro.feed_offset_s - (-12.0)) > 0.10 or sl_intro.env_z < wt_detect.ENV_Z_MIN:
            print(
                f"FAIL F270: foreign intro clip expected feed_offset_s=-12.0 and env_z>={wt_detect.ENV_Z_MIN}, "
                f"got feed_offset_s={sl_intro.feed_offset_s}, env_z={sl_intro.env_z:.2f}"
            )
            return 1

        # Case 2: last 8 s of venue feed (t=32..40 s) + 12 s foreign outro (true lag = +32.0 s)
        clip_outro = np.concatenate([feed[32 * FS:40 * FS], foreign])
        sl_outro = wt_detect.SessionLog(
            sid="session_1000",
            path=os.path.join(tmp, "session_1000.jsonl"),
            header={},
            secs=[],
            t_start=1000,
            t_end=1040,
            key_id="0" * 16,
            params=engine.StampParams(),
            feed_path=feed_path,
            venue_name="Test Venue",
        )
        wt_detect.match_feed(clip_outro, sl_outro, warnings)
        if sl_outro.feed_offset_s is None or abs(sl_outro.feed_offset_s - 32.0) > 0.10 or sl_outro.env_z < wt_detect.ENV_Z_MIN:
            print(
                f"FAIL F270: foreign outro clip expected feed_offset_s=32.0 and env_z>={wt_detect.ENV_Z_MIN}, "
                f"got feed_offset_s={sl_outro.feed_offset_s}, env_z={sl_outro.env_z:.2f}"
            )
            return 1

        print(f"PASS F270: intro=(offset={sl_intro.feed_offset_s:.2f}s, z={sl_intro.env_z:.2f}) outro=(offset={sl_outro.feed_offset_s:.2f}s, z={sl_outro.env_z:.2f})")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
