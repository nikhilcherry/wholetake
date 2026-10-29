"""Reproducer test for F239: witness.wt_stage and run_wt drop duration_s and source_offset_s
on S4 foreign and replacement edits."""
from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from roomstamp import witness, wt_detect
from roomstamp.spec import FS


def _make_clip(dur_s: float = 10.0) -> wt_detect.Clip:
    x = np.zeros(int(dur_s * FS), dtype=np.float64)
    return wt_detect.Clip(
        path="dummy.wav",
        sha256="00" * 32,
        n_bytes=1000,
        x=x,
        orig_rate=FS,
        orig_channels=1,
        codec="pcm_s16le",
        decoder="soundfile",
        pcm_sha256="11" * 32,
        seed=1234,
    )


def test_wt_foreign_insertion_duration_preserved() -> tuple[bool, str]:
    fake_rec = SimpleNamespace(meta={"t_start_unix": 1700000000.0})
    fake_match_res = {
        "pieces": [{"clip_start_s": 3.4, "clip_end_s": 10.0, "witness_start_s": 0.0, "witness_end_s": 6.6, "similarity": 0.85}],
        "gaps": [],
        "foreign_insertions": [{"clip_start_s": 0.0, "clip_end_s": 3.4}],
        "clip_duration_s": 10.0,
        "verdict": "spliced",
        "decisive_words_removed": [],
        "clip_transcript": "",
    }
    clip = _make_clip(10.0)
    with patch("roomstamp.witness._record", return_value=fake_rec), \
         patch("wholetake.match.match", return_value=fake_match_res):
        warns: list[str] = []
        st = wt_detect.run_wt(clip, "dummy_witness", warns)

    edits = st.get("edits", [])
    if len(edits) != 1:
        return False, f"expected 1 edit, got {edits}"
    e = edits[0]
    dur = e.get("duration_s")
    summary = wt_detect._edit_summary(edits)
    if dur != 3.4:
        return False, f"S4 foreign insertion lost duration_s (got {dur!r}); _edit_summary rendered: {summary!r}"
    if "3.4 s not from this venue at 0:00" not in summary:
        return False, f"unexpected _edit_summary: {summary!r}"

    # Also test run_wt fallback when wt_stage edit only has clip_t_range without duration_s
    raw_stage = {
        "status": "present",
        "summary": "Spliced: foreign",
        "edits": [{"kind": "foreign", "clip_t": 1.0, "clip_t_range": [1.0, 3.5], "removed_s": 0.0, "restored_text": "", "precision_s": 0.2}],
        "recorded_at_unix": 1700000000.0,
        "venue_t_start": 0.0,
        "venue_t_end": 6.5,
        "transcript": "",
        "verdict": "spliced",
    }
    with patch("roomstamp.witness.wt_stage", return_value=raw_stage):
        st2 = wt_detect.run_wt(clip, "dummy_witness", [])
    e2 = st2["edits"][0]
    if e2.get("duration_s") != 2.5:
        return False, f"run_wt did not derive duration_s=2.5 from clip_t_range: got {e2}"
    return True, "ok"


def test_wt_replacement_and_reorder_edit_schema_and_headline() -> tuple[bool, str]:
    fake_rec = SimpleNamespace(meta={"t_start_unix": 1700000000.0})
    fake_match_res = {
        "pieces": [
            {"clip_start_s": 0.0, "clip_end_s": 5.0, "witness_start_s": 0.0, "witness_end_s": 5.0, "similarity": 0.88},
            {"clip_start_s": 6.25, "clip_end_s": 10.0, "witness_start_s": 6.2, "witness_end_s": 9.95, "similarity": 0.88},
        ],
        "gaps": [
            {
                "type": "replacement",
                "clip_at_s": 5.0,
                "removed_s": 1.2,
                "duration_s": 1.25,
                "source_offset_s": 15.0,
                "restored_text": "original phrase",
            }
        ],
        "foreign_insertions": [],
        "clip_duration_s": 10.0,
        "verdict": "spliced",
        "decisive_words_removed": [],
        "clip_transcript": "",
    }
    clip = _make_clip(10.0)
    with patch("roomstamp.witness._record", return_value=fake_rec), \
         patch("wholetake.match.match", return_value=fake_match_res):
        doc = wt_detect._empty_doc(["wt-detect", "dummy.wav"])
        doc["clip"] = {"duration_s": 10.0}
        doc["layers"]["l1"] = wt_detect._stage("absent", "s2", "no stamp", content_match=False)
        doc["layers"]["wt"] = wt_detect.run_wt(clip, "dummy_witness", [])
        wt_detect.fuse(doc)

    edits = doc["verdict"]["edits"]
    if len(edits) != 1:
        return False, f"expected 1 merged edit, got {edits}"
    e = edits[0]
    if e.get("duration_s") != 1.25 or e.get("source_offset_s") != 15.0:
        return False, f"witness.wt_stage dropped duration_s and source_offset_s on S4 'replacement' edit: got {e}"
    expected_phrase = "1.25 s at 0:05 replaced with audio from 15.0 s later in the talk"
    headline = doc["verdict"]["headline"]
    if expected_phrase not in headline:
        return False, f"expected {expected_phrase!r} in headline, got {headline!r}"
    return True, "ok"


def main() -> None:
    failed = False
    for name, fn in [
        ("wt_foreign_insertion_duration_preserved", test_wt_foreign_insertion_duration_preserved),
        ("wt_replacement_and_reorder_edit_schema_and_headline", test_wt_replacement_and_reorder_edit_schema_and_headline),
    ]:
        try:
            ok, msg = fn()
        except Exception as exc:
            ok, msg = False, f"{type(exc).__name__}: {exc}"
        if ok:
            print(f"PASS {name}")
        else:
            print(f"FAIL {name}: {msg}")
            failed = True
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
