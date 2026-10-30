"""Reproducer test for F329: _sw_pieces merging repeated/cut phrases across witness gaps."""
from __future__ import annotations

import sys
from wholetake import asr, match


def _mk_words(toks: list[str], t0: float = 0.0, dt: float = 0.35) -> list[asr.Word]:
    return [asr.Word(i=i, tok=t, raw=t, start=t0 + i * dt, end=t0 + (i + 0.8) * dt, prob=0.99)
            for i, t in enumerate(toks)]


def test_sw_pieces_repeated_phrase_and_witness_gap() -> None:
    # Build a witness of ~70 words where a 5-word clause occurs at 42..46 and again at 49..53
    # separated by 2 unrelated witness tokens (47, 48), and a clip that repeats the 6-word phrase
    # (with an extra clip token at index 2 and 8).
    filler_pre = [f"word{k}" for k in range(42)]
    phrase_w = ["alpha", "bravo", "charlie", "delta", "echo"]
    gap_w = ["unrelated", "filler"]
    filler_post = [f"tail{k}" for k in range(20)]
    ww = _mk_words(filler_pre + phrase_w + gap_w + phrase_w + filler_post)

    phrase_c = ["alpha", "bravo", "extra", "charlie", "delta", "echo"]
    cw = _mk_words(phrase_c + phrase_c)

    pieces = match._sw_pieces(cw, ww)
    if len(pieces) < 2:
        print(f"FAIL test_F329_repeated_phrase: Expected >= 2 SW pieces for repeated phrase, got {len(pieces)}: {pieces}")
        sys.exit(1)

    # Also check the case where the phrase occurs only once in the witness at 42..46
    # and is repeated twice in the clip, with a weak 2-word partial match at 49..50.
    ww_single = _mk_words(filler_pre + phrase_w + gap_w + ["alpha", "bravo", "other1", "other2", "other3"] + filler_post)
    pieces_single = match._sw_pieces(cw, ww_single)
    if len(pieces_single) < 2:
        print(f"FAIL test_F329_single_witness_repeat: Expected >= 2 SW pieces, got {len(pieces_single)}: {pieces_single}")
        sys.exit(1)

    for p in pieces + pieces_single:
        for (c0, w0), (c1, w1) in zip(p, p[1:]):
            if not (c1 > c0 and w1 > w0 and (w1 - w0) - (c1 - c0) < 2):
                print(f"FAIL test_F329_monotonic_anchors: non-contiguous step ({c0},{w0})->({c1},{w1}) in {p}")
                sys.exit(1)

    print("PASS test_F329")


if __name__ == "__main__":
    test_sw_pieces_repeated_phrase_and_witness_gap()
