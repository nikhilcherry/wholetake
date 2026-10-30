import sys
import warnings
import numpy as np

from roomstamp import engine, l1verify, selftest, spec
from roomstamp.session import Session
from roomstamp.spec import FS


def main() -> int:
    fails = 0

    x = selftest.load_speech()[:10 * FS].astype(np.float64)
    key = bytes(range(32))
    t0 = selftest.T0
    p = engine.StampParams(mode="pa")
    out = engine.render(x, key, t0, p).astype(np.float64)
    sess = Session(key_id=spec.key_id(key), t_start=t0, params=p, feed="feed.wav", venue="Test Venue")

    # 1. Single NaN in clip
    clip_nan = out[1 * FS:9 * FS].copy()
    clip_nan[100] = np.nan
    with warnings.catch_warnings(record=True) as w_rec:
        warnings.simplefilter("always")
        r1 = l1verify.verify(clip_nan, x, sess, key, log=lambda *_: None)
    if r1.get("matched") is True and r1.get("stamp") == "present":
        print("PASS nan_in_clip_handled")
    else:
        print(f"FAIL nan_in_clip_handled: Single NaN sample poisoned entire 8s clip via IIR sosfilt: matched={r1.get('matched')}, stamp={r1.get('stamp')}")
        fails += 1

    # 2. Single Inf / -Inf in feed
    feed_inf = x.copy()
    feed_inf[0] = np.inf
    feed_inf[500] = -np.inf
    clip_clean = out[1 * FS:9 * FS].copy()
    with warnings.catch_warnings(record=True) as w_rec2:
        warnings.simplefilter("always")
        r2 = l1verify.verify(clip_clean, feed_inf, sess, key, log=lambda *_: None)
    if r2.get("matched") is True and r2.get("stamp") == "present":
        print("PASS inf_in_feed_handled")
    else:
        print(f"FAIL inf_in_feed_handled: Single Inf at feed[0] poisoned entire feed via sosfilt: matched={r2.get('matched')}, stamp={r2.get('stamp')}")
        fails += 1

    # 3. Direct _band and render_span sanitization
    arr = x[:2 * FS].copy()
    arr[10] = np.nan
    arr[20] = np.inf
    arr[30] = -np.inf
    b_out = l1verify._band(arr)
    rend = l1verify.render_span(feed_inf, sess, key, 0, 2 * FS)
    if (np.isfinite(b_out).all() and np.isfinite(rend.feed).all()
            and np.isfinite(rend.voice).all() and np.isfinite(rend.stamp).all()):
        print("PASS band_and_render_span_sanitized")
    else:
        print("FAIL band_and_render_span_sanitized: non-finite values remained in _band or render_span output")
        fails += 1

    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
