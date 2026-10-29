import secrets
import sys
import numpy as np
from roomstamp import engine, l1verify, selftest, spec
from roomstamp.session import Session
from roomstamp.spec import FS

def main():
    x = selftest.load_speech()[:10 * FS].astype(np.float64)
    key = secrets.token_bytes(32)
    p = engine.StampParams(mode="pa")
    out = engine.render(x, key, selftest.T0, p).astype(np.float64)
    sess = Session(key_id=spec.key_id(key), t_start=selftest.T0, params=p, feed="", venue="Test")

    # Case 1: sub-second clip (0.5 s = 24000 samples < FS)
    sub_clip = out[2 * FS : 2 * FS + FS // 2]
    try:
        r1 = l1verify.verify(sub_clip, x, sess, key, log=lambda *_: None)
        if r1["pieces"] and r1["pieces"][0]["clip_end"] > len(sub_clip) / FS + 1e-9:
            print("FAIL f105_sub_second_clip: piece clip_end exceeds clip length")
            sys.exit(1)
        print("PASS f105_sub_second_clip")
    except Exception as e:
        print(f"FAIL f105_sub_second_clip: {type(e).__name__}: {e}")
        sys.exit(1)

    # Case 2: clip 4 samples shy of a 0.5 s multiple (3 * FS - 4 = 143996 samples)
    shy_clip = out[2 * FS : 5 * FS - 4]
    try:
        r2 = l1verify.verify(shy_clip, x, sess, key, log=lambda *_: None)
        if not r2["matched"] or r2["pieces"][-1]["clip_end"] > len(shy_clip) / FS + 1e-9:
            print(f"FAIL f105_shy_multiple_clip: matched={r2['matched']}, pieces={r2['pieces']}")
            sys.exit(1)
        print("PASS f105_shy_multiple_clip")
    except Exception as e:
        print(f"FAIL f105_shy_multiple_clip: {type(e).__name__}: {e}")
        sys.exit(1)

    # Case 3: _span_cancel_db with unclamped Piece.c1 > len(clip)
    try:
        rend = l1verify.render_span(x, sess, key, FS, 6 * FS)
        span = l1verify.Piece(c0=2 * FS, c1=len(shy_clip) + 4, a=float(2 * FS))
        ch = l1verify.Channel(shy_clip[:2 * FS], out[2 * FS : 4 * FS])
        db = l1verify._span_cancel_db(shy_clip, rend, span, ch, span)
        assert np.isfinite(db)
        print("PASS f105_span_cancel_db_unclamped")
    except Exception as e:
        print(f"FAIL f105_span_cancel_db_unclamped: {type(e).__name__}: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
