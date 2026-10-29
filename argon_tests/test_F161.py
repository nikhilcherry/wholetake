"""Reproducer for F161: Acoustic-only witness mode (WHOLETAKE_ASR=none) pause trim vs speech deletion."""
from __future__ import annotations

import os
import sys
import numpy as np

os.environ["WHOLETAKE_ASR"] = "none"

from wholetake import asr, audio, match, record
from wholetake.audio import SR
from wholetake.selftest import TALK, HERE, crossfade_join, seg


def main() -> int:
    asr.MODEL = "none"
    asr._model = None

    W = audio.decode(TALK)

    # Case A: Pure-silence pause trim in acoustic-only mode (ww == []) must be classified as
    # "pause_trim" and keep verdict == "faithful".
    part1 = seg(W, 15.0, 21.5)
    part2 = seg(W, 21.5, 28.0)
    pause_full = np.zeros(int(0.60 * SR), dtype=np.float32)
    pause_trimmed = np.zeros(int(0.10 * SR), dtype=np.float32)

    w_pause = np.concatenate([part1, pause_full, part2])
    clip_pause = np.concatenate([part1, pause_trimmed, part2])

    rec_pause = record.Record(
        meta={"format": record.FORMAT, "id": "test-f161-pause", "duration_s": round(len(w_pause) / SR, 3)},
        words=[],
        feats=audio.features(w_pause),
    )

    r1 = match.match(rec_pause, clip_pause)
    if not r1["gaps"]:
        print(f"FAIL test_F161: expected a gap for 0.50s pause trim, got gaps={r1['gaps']}")
        return 1
    g1 = r1["gaps"][0]
    if g1["type"] != "pause_trim" or r1["verdict"] != "faithful":
        print(
            f"FAIL test_F161: pause trim in acoustic-only mode misclassified: "
            f"verdict={r1['verdict']!r}, gap_type={g1['type']!r}"
        )
        return 1

    # Case B: Active speech cut in acoustic-only mode must still be classified as
    # "deletion" and set verdict == "cut".
    rec_noasr = record.load(os.path.join(HERE, "record_noasr"))
    clip_cut = crossfade_join([seg(W, 40.0, 46.5), seg(W, 49.8, 56.0)], 0.01)
    r2 = match.match(rec_noasr, clip_cut)
    if r2["verdict"] != "cut" or not r2["gaps"] or r2["gaps"][0]["type"] != "deletion":
        print(
            f"FAIL test_F161: active speech cut in acoustic-only mode failed: "
            f"verdict={r2['verdict']!r}, gaps={r2['gaps']!r}"
        )
        return 1

    print("PASS test_F161")
    return 0


if __name__ == "__main__":
    sys.exit(main())
