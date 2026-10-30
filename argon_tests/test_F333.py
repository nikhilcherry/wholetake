"""Reproducer test for FINDING F333:
_pair_replacements misreports a cut followed by an equal-length stutter/repeat within 3s as a 'replaced' edit.
"""
from __future__ import annotations

import sys
import numpy as np

from roomstamp import engine, l1verify, selftest, spec
from roomstamp.session import Session
from roomstamp.spec import FS


def test_unit_pair_replacements() -> bool:
    name = "test_unit_pair_replacements_cut_then_stutter"
    # Case 1: +0.5s deletion at 5.0s followed by -0.5s repeat/stutter at 7.0s (duration 2.0s > 0.5s).
    # The middle piece overlaps the adjacent continuous speech and is NOT a disjoint substitution.
    edits_overlap = [
        {"kind": "removed", "clip_t": 5.0, "removed_s": 0.5, "venue_t_from": 105.0, "venue_t_to": 105.5},
        {"kind": "repeated", "clip_t": 7.0, "removed_s": -0.5, "venue_t_from": 107.5, "venue_t_to": 107.0},
    ]
    res1 = l1verify._pair_replacements(edits_overlap)
    if any(e["kind"] == "replaced" for e in res1) or len(res1) != 2:
        print(f"FAIL {name}: overlapping cut+stutter was falsely paired into 'replaced': {res1}")
        return False

    # Case 2: Equal duration and offset (abs(removed_s) == duration_s) also touches/overlaps boundary.
    edits_equal = [
        {"kind": "removed", "clip_t": 5.0, "removed_s": 1.0, "venue_t_from": 105.0, "venue_t_to": 106.0},
        {"kind": "repeated", "clip_t": 6.0, "removed_s": -1.0, "venue_t_from": 107.0, "venue_t_to": 106.0},
    ]
    res2 = l1verify._pair_replacements(edits_equal)
    if any(e["kind"] == "replaced" for e in res2) or len(res2) != 2:
        print(f"FAIL {name}: equal-offset cut+repeat was falsely paired into 'replaced': {res2}")
        return False

    # Case 3: True disjoint substitution (+15.0s jump for 1.2s span, then -15.0s jump back).
    edits_disjoint = [
        {"kind": "removed", "clip_t": 10.0, "removed_s": 15.0, "venue_t_from": 110.0, "venue_t_to": 125.0},
        {"kind": "repeated", "clip_t": 11.2, "removed_s": -15.0, "venue_t_from": 126.2, "venue_t_to": 111.2},
    ]
    res3 = l1verify._pair_replacements(edits_disjoint)
    if len(res3) != 1 or res3[0]["kind"] != "replaced":
        print(f"FAIL {name}: genuine disjoint substitution was not paired into 'replaced': {res3}")
        return False

    print(f"PASS {name}")
    return True


def test_e2e_verify_cut_then_stutter() -> bool:
    name = "test_e2e_verify_cut_then_stutter"
    rng = np.random.default_rng(42)
    speech = selftest.load_speech()[:16 * FS]
    feed = speech + 10 ** (-80 / 20) * rng.standard_normal(len(speech))
    key = b"\x11" * 32
    t0 = 1700000000
    params = engine.StampParams(mode="pa")
    out = engine.render(feed, key, t0, params).astype(np.float64)
    sess = Session(key_id=spec.key_id(key), t_start=t0, params=params, feed="", venue="TestHall")

    # Construct a clip with:
    # - Piece 1: [0.0s .. 5.0s] of venue (duration 5.0s)
    # - Cut of +0.5s at clip_t = 5.0s -> jumps to 5.5s of venue
    # - Piece 2: [5.5s .. 7.5s] of venue (duration 2.0s, clip_t 5.0s .. 7.0s)
    # - Stutter/repeat of -0.5s at clip_t = 7.0s -> jumps back to 7.0s of venue
    # - Piece 3: [7.0s .. 14.0s] of venue (duration 7.0s, clip_t 7.0s .. 14.0s)
    clip = np.concatenate([
        out[0:int(5.0 * FS)],
        out[int(5.5 * FS):int(7.5 * FS)],
        out[int(7.0 * FS):int(14.0 * FS)],
    ])

    r = l1verify.verify(clip, feed, sess, key, log=lambda *_: None)
    edits = r["edits"]
    verdict = r["verdict"]
    if any(e["kind"] == "replaced" for e in edits) or "replaced" in verdict:
        print(f"FAIL {name}: A 0.5s deletion at 5.0s followed by a 0.5s stutter at 7.0s "
              f"was falsely paired into a 'replaced' edit! edits={edits}, verdict={verdict!r}")
        return False

    kinds = [e["kind"] for e in edits]
    if "removed" not in kinds or "repeated" not in kinds:
        print(f"FAIL {name}: expected both 'removed' and 'repeated' edits, got edits={edits}, verdict={verdict!r}")
        return False

    print(f"PASS {name}")
    return True


if __name__ == "__main__":
    ok = test_unit_pair_replacements() and test_e2e_verify_cut_then_stutter()
    sys.exit(0 if ok else 1)
