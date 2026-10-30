"""Reproducer for F246: merge_edits associates coarser-layer edits with the first cut in out within +-2.0 s instead of the nearest cut."""
from __future__ import annotations

import sys
from roomstamp.wt_detect import _empty_doc, _stage, fuse, merge_edits


def test_merge_edits_nearest_cut_association() -> None:
    layers = {
        "l1": _stage(
            "present",
            "s2",
            "stamp present",
            recorded_at_unix=1793430600.0,
            venue_t_start=1793430600.0,
            venue_t_end=1793430620.0,
            content_match=True,
            integrity_checked=True,
            edits=[
                {
                    "kind": "removed",
                    "clip_t": 10.0,
                    "clip_t_range": [10.0, 10.0],
                    "removed_s": 0.20,
                    "precision_s": 0.002,
                    "source": "l1",
                },
                {
                    "kind": "removed",
                    "clip_t": 11.5,
                    "clip_t_range": [11.5, 11.5],
                    "removed_s": 0.55,
                    "precision_s": 0.002,
                    "source": "l1",
                },
            ],
        ),
        "l2": _stage("skipped"),
        "wt": _stage(
            "present",
            "s4",
            "cut",
            recorded_at_unix=1793430600.0,
            venue_t_start=1793430600.0,
            venue_t_end=1793430620.0,
            content_match=True,
            integrity_checked=True,
            edits=[
                {
                    "kind": "removed",
                    "clip_t": 11.5,
                    "clip_t_range": [11.5, 11.5],
                    "removed_s": 0.55,
                    "precision_s": 0.05,
                    "restored_text": "not",
                    "source": "wt",
                },
            ],
        ),
    }

    edits, warnings = merge_edits(layers)
    if len(edits) != 2:
        print(f"FAIL test_merge_edits_nearest_cut_association: expected 2 edits, got {len(edits)}: {edits}")
        sys.exit(1)
    cut10, cut115 = edits[0], edits[1]
    if cut10.get("restored_text") is not None or cut115.get("restored_text") != "not" or warnings:
        print(
            f"FAIL test_merge_edits_nearest_cut_association: wt edit at 11.5s was merged into L1 cut at 10.0s "
            f"instead of 11.5s: cut@10.0 text={cut10.get('restored_text')!r}, "
            f"cut@11.5 text={cut115.get('restored_text')!r}, warnings={warnings!r}"
        )
        sys.exit(1)

    doc = _empty_doc(["wt-detect", "clip.wav"])
    doc["clip"] = {"duration_s": 20.0}
    doc["layers"] = layers
    doc["warnings_base"] = []
    fuse(doc)
    v_edits = doc["verdict"]["edits"]
    if v_edits[0].get("restored_text") is not None or v_edits[1].get("restored_text") != "not":
        print(f"FAIL test_merge_edits_nearest_cut_association: fuse verdict.edits mismatch: {v_edits}")
        sys.exit(1)
    if any("edit_size_mismatch" in w for w in doc["warnings"]):
        print(f"FAIL test_merge_edits_nearest_cut_association: unexpected edit_size_mismatch warning: {doc['warnings']}")
        sys.exit(1)

    print("PASS test_merge_edits_nearest_cut_association")


if __name__ == "__main__":
    test_merge_edits_nearest_cut_association()
