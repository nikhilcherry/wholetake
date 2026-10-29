"""Reproducer for F272: _split_pieces leaves adjacent same-offset pieces unmerged
after dropping a squeezed middle piece."""
from __future__ import annotations

import sys
import numpy as np

from roomstamp import engine, l1verify, selftest, spec
from roomstamp.session import Session
from roomstamp.spec import FS


def test_split_pieces_drops_squeezed_middle_piece_same_offset() -> None:
    name = "split_pieces_drops_squeezed_middle_piece_same_offset"
    try:
        x = selftest.load_speech()[:12 * FS].copy()
        rng = np.random.default_rng(42)
        x += 10 ** (-80 / 20) * rng.standard_normal(len(x))
        key = b"\x11" * 32
        t0 = 1700000000
        p = engine.StampParams(mode="pa")
        out = engine.render(x, key, t0, p).astype(np.float64)
        sess = Session(key_id=spec.key_id(key), t_start=t0, params=p, feed="feed.wav", venue="Test Venue")

        # Take a 6 s continuous clip starting at 2 s into the session
        off = 2 * FS
        clip = out[off:off + 6 * FS].copy()
        rend = l1verify.render_span(x, sess, key, 0, len(x))

        def ch(piece: l1verify.Piece) -> l1verify.Channel:
            c = np.arange(piece.c0, piece.c1)
            return l1verify.Channel(clip[piece.c0:piece.c1], l1verify.warp([rend.voice], piece.f(c) - rend.base)[0])

        # L and R share the true offset (a = off); M sits between them with a wrong offset
        # and gets squeezed to < MIN_PIECE_S (0.15 s) by _locate_cut.
        L = l1verify.Piece(c0=0, c1=int(2.5 * FS), a=float(off), b=0.0, n_win=5, psr=30.0)
        M = l1verify.Piece(c0=int(2.5 * FS), c1=int(2.6 * FS), a=float(off + 3 * FS), b=0.0, n_win=1, psr=15.0)
        R = l1verify.Piece(c0=int(2.6 * FS), c1=len(clip), a=float(off), b=0.0, n_win=6, psr=32.0)

        pieces = [L, M, R]
        chans = [ch(piece) for piece in pieces]
        pieces_out, chans_out = l1verify._split_pieces(clip, rend, pieces, chans)

        assert len(pieces_out) == 1, (
            f"_split_pieces dropped squeezed middle piece but left {len(pieces_out)} "
            f"adjacent pieces with identical offset instead of merging them"
        )
        assert len(chans_out) == 1, f"expected 1 channel after merge, got {len(chans_out)}"
        assert pieces_out[0].c0 == 0 and pieces_out[0].c1 == len(clip), (
            f"merged piece span {(pieces_out[0].c0, pieces_out[0].c1)} != (0, {len(clip)})"
        )
        assert pieces_out[0].n_win == 11, f"expected merged n_win=11, got {pieces_out[0].n_win}"
        print(f"PASS {name}")
    except Exception as e:
        print(f"FAIL {name}: {e}")
        sys.exit(1)


if __name__ == "__main__":
    test_split_pieces_drops_squeezed_middle_piece_same_offset()
