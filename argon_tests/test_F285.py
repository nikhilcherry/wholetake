"""Reproducer test for F285: fuse() and merge_edits() must not adopt L2 coarse cuts or
mismatched tempo when L1/WT verified the clip as intact or at a conflicting timestamp."""
from __future__ import annotations

import sys

from roomstamp import wt_detect


def _make_doc(l1_stage: dict, l2_stage: dict, wt_stage: dict | None = None, duration_s: float = 20.0) -> dict:
    doc = wt_detect._empty_doc(["wt-detect", "clip.wav"])
    doc["clip"] = {
        "path": "clip.wav",
        "sha256": "00" * 32,
        "bytes": 1000,
        "duration_s": duration_s,
        "orig_rate": 48000,
        "orig_channels": 1,
        "codec": "wav/pcm_16",
        "decoder": "soundfile",
    }
    doc["verdict"]["venue"] = {
        "name": "Test Venue",
        "key_id": "0123456789abcdef",
        "session": "session_0",
        "mode": "pa",
    }
    doc["warnings_base"] = []
    doc["layers"]["l1"] = l1_stage
    doc["layers"]["l2"] = l2_stage
    if wt_stage is not None:
        doc["layers"]["wt"] = wt_stage
    return doc


def test_l1_l2_time_mismatch_when_l1_at_unix_zero_and_intact() -> None:
    name = "test_l1_l2_time_mismatch_when_l1_at_unix_zero_and_intact"
    l1 = wt_detect._stage(
        "present",
        "s2",
        "L1 present",
        z=11.5,
        recorded_at_unix=0.0,
        venue_t_start=0.0,
        venue_t_end=20.0,
        content_match=True,
        integrity_checked=True,
        edits=[],
    )
    l2_cut = {
        "kind": "removed",
        "clip_t": 15.0,
        "clip_t_range": [14.0, 16.0],
        "removed_s": 2.0,
        "duration_s": None,
        "venue_t_from": None,
        "venue_t_to": None,
        "precision_s": 0.1,
        "source": "l2",
    }
    l2 = wt_detect._stage(
        "likely",
        "s3",
        "L2 likely",
        z=4.2,
        p_value=0.005,
        recorded_at_unix=300.0,
        venue_t_start=300.0,
        venue_t_end=325.0,
        tempo=1.25,
        edits=[l2_cut],
    )
    doc = _make_doc(l1, l2)
    wt_detect.fuse(doc)
    v = doc["verdict"]
    if v["integrity"] != "intact" or v["edits"]:
        print(f"FAIL {name}: Coarse L2 cut merged into verdict even though L1 verified clip intact: "
              f"integrity={v['integrity']!r}, edits={v['edits']!r}, headline={v['headline']!r}")
        sys.exit(1)
    if not any("l2_time_mismatch" in w for w in doc["warnings"]):
        print(f"FAIL {name}: Missing l2_time_mismatch warning when recorded_at_unix is 0.0: warnings={doc['warnings']!r}")
        sys.exit(1)
    if v["tempo_ratio"] != 1.0:
        print(f"FAIL {name}: Adopted mismatched L2 tempo_ratio={v['tempo_ratio']!r} instead of 1.0")
        sys.exit(1)
    print(f"PASS {name}")


def test_l1_intact_with_agreeing_l2_keeps_tempo_but_suppresses_spurious_l2_cut() -> None:
    name = "test_l1_intact_with_agreeing_l2_keeps_tempo_but_suppresses_spurious_l2_cut"
    l1 = wt_detect._stage(
        "present",
        "s2",
        "L1 present",
        z=10.0,
        recorded_at_unix=1793430600.0,
        venue_t_start=1793430600.0,
        venue_t_end=1793430620.0,
        content_match=True,
        integrity_checked=True,
        edits=[],
    )
    l2_cut = {
        "kind": "removed",
        "clip_t": 15.0,
        "clip_t_range": [14.0, 16.0],
        "removed_s": 2.0,
        "duration_s": None,
        "venue_t_from": None,
        "venue_t_to": None,
        "precision_s": 0.1,
        "source": "l2",
    }
    l2 = wt_detect._stage(
        "present",
        "s3",
        "L2 present",
        z=5.5,
        p_value=0.0005,
        recorded_at_unix=1793430600.2,
        venue_t_start=1793430600.2,
        venue_t_end=1793430622.2,
        tempo=1.1,
        edits=[l2_cut],
    )
    doc = _make_doc(l1, l2)
    wt_detect.fuse(doc)
    v = doc["verdict"]
    if v["integrity"] != "intact" or v["edits"]:
        print(f"FAIL {name}: Expected intact with no edits, got integrity={v['integrity']!r}, edits={v['edits']!r}")
        sys.exit(1)
    if v["tempo_ratio"] != 1.1:
        print(f"FAIL {name}: Expected agreeing L2 tempo_ratio=1.1 (F118), got {v['tempo_ratio']!r}")
        sys.exit(1)
    print(f"PASS {name}")


def test_l2_time_mismatch_without_l1_integrity_check_still_suppresses_l2_edits_and_tempo() -> None:
    name = "test_l2_time_mismatch_without_l1_integrity_check_still_suppresses_l2_edits_and_tempo"
    l1 = wt_detect._stage(
        "present",
        "s1-minimal",
        "L1 minimal present",
        z=9.0,
        recorded_at_unix=1793430600.0,
        venue_t_start=1793430600.0,
        venue_t_end=1793430620.0,
        content_match=True,
        integrity_checked=False,
        edits=[],
    )
    l2_cut = {
        "kind": "removed",
        "clip_t": 10.0,
        "clip_t_range": [9.0, 11.0],
        "removed_s": 1.5,
        "duration_s": None,
        "venue_t_from": None,
        "venue_t_to": None,
        "precision_s": 0.1,
        "source": "l2",
    }
    l2 = wt_detect._stage(
        "likely",
        "s3",
        "L2 likely",
        z=4.1,
        p_value=0.008,
        recorded_at_unix=1793430650.0,
        venue_t_start=1793430650.0,
        venue_t_end=1793430675.0,
        tempo=1.25,
        edits=[l2_cut],
    )
    doc = _make_doc(l1, l2)
    wt_detect.fuse(doc)
    v = doc["verdict"]
    if v["edits"] or v["integrity"] != "unknown":
        print(f"FAIL {name}: Mismatched L2 edits should be ignored: integrity={v['integrity']!r}, edits={v['edits']!r}")
        sys.exit(1)
    if v["tempo_ratio"] != 1.0:
        print(f"FAIL {name}: Expected tempo_ratio=1.0 on L2 time mismatch, got {v['tempo_ratio']!r}")
        sys.exit(1)
    print(f"PASS {name}")


if __name__ == "__main__":
    test_l1_l2_time_mismatch_when_l1_at_unix_zero_and_intact()
    test_l1_intact_with_agreeing_l2_keeps_tempo_but_suppresses_spurious_l2_cut()
    test_l2_time_mismatch_without_l1_integrity_check_still_suppresses_l2_edits_and_tempo()
