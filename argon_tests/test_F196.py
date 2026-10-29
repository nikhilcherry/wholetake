"""Reproducer test for F196: Digital silence in clip and feed causes false alignment lock and division-by-zero in Channel."""
import sys
import warnings
import numpy as np

from roomstamp import engine, l1verify, selftest, spec
from roomstamp.session import Session
from roomstamp.spec import FS


def main() -> int:
    key = b"\x11" * 32
    sess = Session(
        key_id=spec.key_id(key),
        t_start=selftest.T0,
        params=engine.StampParams(mode="pa"),
        feed="feed.wav",
        venue="F196 test stage",
    )
    clip_zeros = np.zeros(10 * FS, dtype=np.float64)
    feed_zeros = np.zeros(20 * FS, dtype=np.float64)

    try:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always", RuntimeWarning)
            r = l1verify.verify(clip_zeros, feed_zeros, sess, key, log=lambda *_: None)
            ch = l1verify.Channel(np.zeros(2 * FS, dtype=np.float64), np.zeros(2 * FS, dtype=np.float64))

        div_warns = [w for w in caught if "divide by zero" in str(w.message).lower()]
        assert not div_warns, f"RuntimeWarning divide by zero emitted: {div_warns[0].message}"
        assert r["matched"] is False, f"all-zero silence false-locked: matched={r['matched']}, pieces={r['pieces']}"
        assert r["stamp"] == "absent", f"expected stamp='absent', got {r['stamp']!r}"
        assert r["windows_matched"] == 0, f"expected 0 matched windows, got {r['windows_matched']}"
        assert r["pieces"] == [], f"expected no pieces, got {r['pieces']}"
        assert np.isfinite(ch.removed_db) and ch.removed_db == 0.0, f"unexpected Channel.removed_db={ch.removed_db}"

        # Also test silent clip against real speech feed, and real speech clip against silent feed (muted mic)
        speech = selftest.load_speech()[:15 * FS].astype(np.float64)
        r_silent_clip = l1verify.verify(clip_zeros, speech, sess, key, log=lambda *_: None)
        assert r_silent_clip["matched"] is False and r_silent_clip["stamp"] == "absent", (
            f"silent clip matched speech feed: {r_silent_clip}"
        )
        r_silent_feed = l1verify.verify(speech[:10 * FS], feed_zeros, sess, key, log=lambda *_: None)
        assert r_silent_feed["matched"] is False and r_silent_feed["stamp"] == "absent", (
            f"speech clip matched silent feed: {r_silent_feed}"
        )
        print("PASS all_zero_silence")
        return 0
    except Exception as e:
        print(f"FAIL all_zero_silence: {type(e).__name__}: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
