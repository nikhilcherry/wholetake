from __future__ import annotations

import sys
import numpy as np

from roomstamp import engine, l1verify, spec
from roomstamp.session import Session
from roomstamp.spec import FS


def main() -> int:
    key = bytes(range(32))
    t0 = 1700000000
    params = engine.StampParams(mode="pa", l2_delta_db=4.0)
    secs = {
        t0 + s: {"t": t0 + s, "anchored": False, "randomness": None, "ref_rms": 0.1, "gain": 1.0}
        for s in range(20)
    }
    sess = Session(
        key_id=spec.key_id(key),
        t_start=t0,
        params=params,
        feed="feed.wav",
        secs=secs,
    )
    feed = np.zeros(3 * FS, dtype=np.float64)

    try:
        # Case 1: f_lo and f_hi past the end of feed (with logged ref_rms so warm=0)
        r1 = l1verify.render_span(feed, sess, key, 5 * FS, 6 * FS)
        assert len(r1.feed) > 0 and len(r1.voice) == len(r1.feed) and len(r1.stamp) == len(r1.feed)

        # Case 2: f_lo and f_hi far past end of feed without logged ref_rms
        sess_nowarm = Session(key_id=spec.key_id(key), t_start=t0, params=params, feed="feed.wav")
        r2 = l1verify.render_span(feed, sess_nowarm, key, 20 * FS, 22 * FS)
        assert len(r2.feed) > 0 and len(r2.voice) == len(r2.feed) and len(r2.stamp) == len(r2.feed)

        # Case 3: f_hi <= -FS (before start of feed)
        r3 = l1verify.render_span(feed, sess, key, -4 * FS, -2 * FS)
        assert len(r3.feed) > 0 and len(r3.voice) == len(r3.feed) and len(r3.stamp) == len(r3.feed)

        # Case 4: empty feed and n=0 direct calls to l1_carrier and _voice
        r4 = l1verify.render_span(np.zeros(0, dtype=np.float64), sess, key, 0.0, 0.0)
        assert len(r4.feed) == 0 and len(r4.voice) == 0 and len(r4.stamp) == 0

        c0 = l1verify.l1_carrier(key, t0, 0, sess.lookup())
        v0 = l1verify._voice(np.zeros(0, dtype=np.float64), key, t0, params, sess.lookup(), np.zeros(0, dtype=np.float64))
        assert len(c0) == 0 and len(v0) == 0
    except Exception as e:
        print(f"FAIL render_span_beyond_feed_end: unexpected {type(e).__name__}: {e}")
        return 1

    print("PASS render_span_beyond_feed_end")
    return 0


if __name__ == "__main__":
    sys.exit(main())
