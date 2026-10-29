"""Reproducer test for F291: Channel crashes with IndexError on pieces < 4096 samples."""
from __future__ import annotations

import sys
import numpy as np

from roomstamp import l1verify
from roomstamp.spec import FS


def test_channel_short_pieces() -> None:
    rng = np.random.default_rng(42)
    # Test 4000 samples (< 4096 floor, triggers IndexError: 2001 vs 2049)
    # and 1024 samples (< hop=2048, triggers ValueError/IndexError without padding)
    for n in (4000, 1024, 64):
        v = rng.standard_normal(n)
        y = 0.8 * v + 0.05 * rng.standard_normal(n)
        ch = l1verify.Channel(y, v)
        assert ch.H.shape == (ch.nps // 2 + 1,), f"unexpected H shape {ch.H.shape}"
        assert np.isfinite(ch.removed_db), f"non-finite removed_db for n={n}"
        rw = ch.residual_white()
        assert rw.shape == (n,) and np.all(np.isfinite(rw)), f"bad residual_white for n={n}"
        pred = ch.predict(v)
        assert pred.shape == (n,) and np.all(np.isfinite(pred)), f"bad predict for n={n}"
        ss = rng.standard_normal((2, n))
        wh = ch.white(ss, through_h=True)
        assert wh.shape == (2, n) and np.all(np.isfinite(wh)), f"bad white for n={n}"


def test_split_pieces_squeezed_and_overlapping() -> None:
    rng = np.random.default_rng(7)
    n_voice = 6 * FS
    voice = rng.standard_normal(n_voice)
    clip = voice[:2 * FS].copy()
    rend = l1verify.Render(base=0, feed=voice, voice=voice, stamp=np.zeros(n_voice), wrong=[])

    # Two pieces whose spans after cut / overlap leave a 4000-sample piece (< 4096)
    p1 = l1verify.Piece(c0=0, c1=4000, a=0.0, b=0.0, n_win=1, psr=20.0)
    p2 = l1verify.Piece(c0=3800, c1=7800, a=0.0, b=0.0, n_win=1, psr=20.0)
    chans = [
        l1verify.Channel(clip[p.c0:p.c1], l1verify.warp([rend.voice], p.f(np.arange(p.c0, p.c1)) - rend.base)[0])
        for p in (p1, p2)
    ]
    pieces, chans_out = l1verify._split_pieces(clip, rend, [p1, p2], chans)
    assert len(pieces) >= 1 and len(chans_out) == len(pieces)


def main() -> int:
    for name, fn in [
        ("channel_short_pieces", test_channel_short_pieces),
        ("split_pieces_squeezed_and_overlapping", test_split_pieces_squeezed_and_overlapping),
    ]:
        try:
            fn()
            print(f"PASS {name}")
        except Exception as e:
            print(f"FAIL {name}: {type(e).__name__}: {e}")
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
