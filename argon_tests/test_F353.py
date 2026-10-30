"""Reproducer test for F353:
`fuse()`, `merge_edits()`, and `run()` let an `inconclusive` L1 (even with `content_match=False`)
override a `present` L2 verdict's timestamp, tempo, and edits.
"""
from __future__ import annotations

import sys
from roomstamp import wt_detect


def _make_doc(l1_status: str, l1_content_match: bool, l1_rec: float,
              l2_status: str, l2_rec: float, l2_tempo: float, l2_edits: list[dict]) -> dict:
    doc = wt_detect._empty_doc(["wt-detect", "clip.wav"])
    doc["clip"] = {
        "path": "clip.wav",
        "sha256": "0" * 64,
        "bytes": 1000,
        "duration_s": 20.0,
        "orig_rate": 48000,
        "orig_channels": 1,
        "codec": "wav/pcm_16",
        "decoder": "soundfile",
    }
    doc["verdict"]["venue"] = {
        "name": "HH Goa demo stage",
        "key_id": "deadbeef01234567",
        "session": "session_1793430600",
        "mode": "pa",
    }
    doc["warnings_base"] = []
    doc["layers"]["l1"] = wt_detect._stage(
        l1_status,
        "s1-minimal",
        "weak noise peak",
        z=4.2,
        recorded_at_unix=l1_rec,
        venue_t_start=l1_rec,
        venue_t_end=l1_rec + 20.0,
        content_match=l1_content_match,
        integrity_checked=False,
    )
    doc["layers"]["l2"] = wt_detect._stage(
        l2_status,
        "s3",
        "L2 present",
        z=6.1,
        p_value=1e-4,
        recorded_at_unix=l2_rec,
        venue_t_start=l2_rec,
        venue_t_end=l2_rec + 20.0 * l2_tempo,
        tempo=l2_tempo,
        edits=l2_edits,
    )
    return doc


def main() -> int:
    failures = []

    l2_cut = [{
        "kind": "removed",
        "clip_t": 10.0,
        "clip_t_range": [8.5, 11.5],
        "removed_s": 2.4,
        "duration_s": None,
        "venue_t_from": None,
        "venue_t_to": None,
        "precision_s": 0.1,
        "source": "l2",
    }]

    # Case 1: L1 is inconclusive with content_match=False, L2 is present
    doc1 = _make_doc(
        l1_status="inconclusive",
        l1_content_match=False,
        l1_rec=1793430602.0,
        l2_status="present",
        l2_rec=1793430610.0,
        l2_tempo=1.25,
        l2_edits=l2_cut,
    )
    wt_detect.fuse(doc1)
    v1 = doc1["verdict"]
    if (
        v1["recorded_here"] != "verified"
        or v1["recorded_at_source"] != "l2"
        or v1["recorded_at_unix"] != 1793430610.0
        or v1["recorded_at_precision_s"] != 0.02
        or v1["tempo_ratio"] != 1.25
        or len(v1["edits"]) != 1
        or v1["edits"][0]["source"] != "l2"
        or any("l2_time_mismatch" in w for w in doc1["warnings"])
    ):
        reason = (
            f"src={v1['recorded_at_source']}, rec={v1['recorded_at_unix']}, "
            f"tempo={v1['tempo_ratio']}, edits={len(v1['edits'])}, warnings={doc1['warnings']}"
        )
        print(f"FAIL t10_fuse_present_l2_beats_inconclusive_unmatched_l1: {reason}")
        failures.append("t10_fuse_present_l2_beats_inconclusive_unmatched_l1")
    else:
        print("PASS t10_fuse_present_l2_beats_inconclusive_unmatched_l1")

    # Case 2: L1 is inconclusive with content_match=True (e.g., speech matched, stamp weak/vocoded), L2 is present
    doc2 = _make_doc(
        l1_status="inconclusive",
        l1_content_match=True,
        l1_rec=1793430602.0,
        l2_status="present",
        l2_rec=1793430610.0,
        l2_tempo=1.25,
        l2_edits=l2_cut,
    )
    wt_detect.fuse(doc2)
    v2 = doc2["verdict"]
    if (
        v2["recorded_here"] != "verified"
        or v2["recorded_at_source"] != "l2"
        or v2["recorded_at_unix"] != 1793430610.0
        or v2["recorded_at_precision_s"] != 0.02
        or v2["tempo_ratio"] != 1.25
        or len(v2["edits"]) != 1
        or v2["edits"][0]["source"] != "l2"
        or any("l2_time_mismatch" in w for w in doc2["warnings"])
    ):
        reason = (
            f"src={v2['recorded_at_source']}, rec={v2['recorded_at_unix']}, "
            f"tempo={v2['tempo_ratio']}, edits={len(v2['edits'])}, warnings={doc2['warnings']}"
        )
        print(f"FAIL t11_fuse_present_l2_beats_inconclusive_matched_l1: {reason}")
        failures.append("t11_fuse_present_l2_beats_inconclusive_matched_l1")
    else:
        print("PASS t11_fuse_present_l2_beats_inconclusive_matched_l1")

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
