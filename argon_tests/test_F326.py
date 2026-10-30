import os
import sys
import numpy as np

from wholetake import audio, match, record, selftest


def test_01_equal_length_replacement_absorbed():
    here = os.path.join("testdata", "witness")
    rec = record.load(os.path.join(here, "record_noasr"))
    w = audio.decode(os.path.join(here, "talk.wav"))
    ins = audio.decode(os.path.join(here, "insert.wav"))
    # Replace 3.0 s of witness [146.0, 149.0] with 3.0 s of foreign speech [0.0, 3.0]
    clip = selftest.crossfade_join([
        selftest.seg(w, 140.0, 146.0),
        selftest.seg(ins, 0.0, 3.0),
        selftest.seg(w, 149.0, 155.0),
    ], 0.01)
    r = match.match(rec, clip)
    if (
        r["verdict"] != "spliced"
        or len(r["pieces"]) != 2
        or len(r["foreign_insertions"]) != 1
        or not r["gaps"]
        or r["gaps"][0]["type"] != "replacement"
    ):
        print(
            f"FAIL test_01_equal_length_replacement_absorbed: 3.0s foreign speech replacing 3.0s of witness "
            f"returned verdict={r['verdict']!r}, pieces={len(r['pieces'])}, "
            f"gaps={r['gaps']}, foreign_insertions={r['foreign_insertions']}"
        )
        return False
    print("PASS test_01_equal_length_replacement_absorbed")
    return True


def test_02_group_direct_call_respects_active_gap():
    # Two window groups with identical witness offset (1000) separated by a 3.0 s gap [300, 600)
    g1 = [(u, u + 1000, 0.85, 8.0) for u in range(0, 200, 10)]
    gap = [(u, u + 500, 0.15, 1.2) for u in range(200, 600, 10)]
    g2 = [(u, u + 1000, 0.85, 8.0) for u in range(600, 800, 10)]
    track = g1 + gap + g2

    F_active = np.ones((900, 40), dtype=np.float32)
    pieces_active = match._group(track, F_active)
    pieces_no_f = match._group(track)

    F_silent = np.ones((900, 40), dtype=np.float32)
    F_silent[300:600] = 0.0
    pieces_silent = match._group(track, F_silent)

    if len(pieces_active) != 2 or len(pieces_no_f) != 2 or len(pieces_silent) != 1:
        print(
            f"FAIL test_02_group_direct_call_respects_active_gap: "
            f"active={len(pieces_active)}, no_f={len(pieces_no_f)}, silent={len(pieces_silent)}"
        )
        return False
    print("PASS test_02_group_direct_call_respects_active_gap")
    return True


if __name__ == "__main__":
    ok1 = test_01_equal_length_replacement_absorbed()
    ok2 = test_02_group_direct_call_respects_active_gap()
    sys.exit(0 if (ok1 and ok2) else 1)
