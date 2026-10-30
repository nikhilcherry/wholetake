"""Reproducer test for Finding F123:
Sub-resolution offset steps (< JUMP_MIN) and residual tempo slopes split faithful clips into multiple pieces.
"""
from __future__ import annotations

import os
import sys

os.environ["WHOLETAKE_ASR"] = "none"

import numpy as np

from wholetake import audio, match, record, selftest


def test_01_group_slope_continuity() -> None:
    # Synthetic track with a residual tempo slope c1 = 0.004 across 200 windows (20 s)
    # and a brief 4-window interruption in the middle. Global medians differ by ~4 frames (> TRACK_TOL),
    # but all windows lie on the exact same line.
    track = []
    for k in range(200):
        u = k * 10
        if 98 <= k <= 101:
            track.append((u, None, 0.0, 0.0))
        else:
            off = int(round(3000 + 0.004 * u))
            track.append((u, u + off, 0.92, 12.0))
    pieces = match._group(track)
    if len(pieces) != 1:
        raise AssertionError(f"expected 1 continuous piece on sloped track, got {len(pieces)}: {pieces}")


def test_02_small_tempo_4pct_faithful() -> None:
    rec_dir = os.path.join(selftest.HERE, "record_noasr")
    rec = record.load(rec_dir)
    W = audio.decode(selftest.TALK)
    clip = selftest.atempo(selftest.seg(W, 30.0, 50.0), 1.04)
    r = match.match(rec, clip)
    if r["verdict"] != "faithful":
        raise AssertionError(f"expected faithful verdict, got {r['verdict']}")
    if len(r["pieces"]) != 1:
        raise AssertionError(f"expected 1 continuous piece, got {len(r['pieces'])}: {r['pieces']}")
    if r["gaps"]:
        raise AssertionError(f"expected no gaps, got {r['gaps']}")
    p = r["pieces"][0]
    if abs(p["witness_start_s"] - 30.0) > 0.25 or abs(p["witness_end_s"] - 50.0) > 0.25:
        raise AssertionError(f"unexpected witness span: {p}")


def test_03_sub_jump_min_merges_pieces() -> None:
    rec_dir = os.path.join(selftest.HERE, "record_noasr")
    rec = record.load(rec_dir)
    W = audio.decode(selftest.TALK)
    # Create a clip with a 35 ms (< JUMP_MIN = 60 ms) micro-trim at a pause around 38.0 s
    clip = np.concatenate([selftest.seg(W, 30.0, 38.0), selftest.seg(W, 38.035, 46.0)])
    r = match.match(rec, clip)
    if r["verdict"] != "faithful" or r["gaps"]:
        raise AssertionError(f"expected faithful with 0 gaps for 35 ms step, got verdict={r['verdict']} gaps={r['gaps']}")
    if len(r["pieces"]) != 1:
        raise AssertionError(f"expected sub-JUMP_MIN step to merge into 1 piece, got {len(r['pieces'])}: {r['pieces']}")


def main() -> int:
    tests = [
        ("test_01_group_slope_continuity", test_01_group_slope_continuity),
        ("test_02_small_tempo_4pct_faithful", test_02_small_tempo_4pct_faithful),
        ("test_03_sub_jump_min_merges_pieces", test_03_sub_jump_min_merges_pieces),
    ]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS {name}")
        except Exception as e:
            print(f"FAIL {name}: {e}")
            failed += 1
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
