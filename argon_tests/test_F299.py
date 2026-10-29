import os
import sys
from unittest.mock import patch
import numpy as np

from roomstamp import witness
from wholetake import audio


def test_wt_stage_mixed_cut_and_splice_summary() -> None:
    # 1. Unit test with mocked match output
    class DummyRec:
        meta = {"t_start_unix": 1700000000.0}

    fake_match = {
        "verdict": "spliced",
        "clip_duration_s": 15.0,
        "pieces": [
            {"clip_start_s": 0.0, "clip_end_s": 4.5, "witness_start_s": 40.0, "witness_end_s": 44.5, "similarity": 0.85},
            {"clip_start_s": 4.5, "clip_end_s": 9.0, "witness_start_s": 47.5, "witness_end_s": 52.0, "similarity": 0.85},
            {"clip_start_s": 9.0, "clip_end_s": 15.0, "witness_start_s": 20.0, "witness_end_s": 26.0, "similarity": 0.85},
        ],
        "gaps": [
            {"type": "deletion", "clip_at_s": 4.5, "witness_from_s": 44.5, "witness_to_s": 47.5, "removed_s": 3.0, "restored_text": "important clause"},
            {"type": "reorder", "clip_at_s": 9.0, "witness_from_s": 52.0, "witness_to_s": 20.0, "removed_s": -32.0},
        ],
        "foreign_insertions": [],
        "decisive_words_removed": [],
        "clip_transcript": "test transcript",
    }

    with patch("roomstamp.witness._record", return_value=DummyRec()), \
         patch("wholetake.match.match", return_value=fake_match):
        res = witness.wt_stage(np.zeros(4800, dtype=np.float32), "dummy")

    kinds = {e["kind"] for e in res["edits"]}
    if kinds != {"cut", "splice"}:
        print(f"FAIL test_wt_stage_mixed_cut_and_splice_summary: unexpected edits={kinds}")
        sys.exit(1)
    summary = res["summary"]
    if "cut" not in summary and "3.00 s removed" not in summary:
        print(f"FAIL test_wt_stage_mixed_cut_and_splice_summary: wt_stage summary completely omits a 3.0s cut when clip also has a splice! edits={kinds}, summary={summary!r}")
        sys.exit(1)
    if "3.00 s removed at 4.50 s" not in summary or "important clause" not in summary:
        print(f"FAIL test_wt_stage_mixed_cut_and_splice_summary: missing cut details in summary={summary!r}")
        sys.exit(1)

    # 2. End-to-end acoustic test on testdata/witness/talk.wav + record_noasr
    talk_path = os.path.join("testdata", "witness", "talk.wav")
    rec_dir = os.path.join("testdata", "witness", "record_noasr")
    if os.path.exists(talk_path) and os.path.exists(rec_dir):
        w48 = audio.decode(talk_path, sr=48000)
        sr = 48000
        p1 = w48[int(40.0 * sr):int(46.5 * sr)]
        p2 = w48[int(49.8 * sr):int(56.0 * sr)]
        p3 = w48[int(20.0 * sr):int(27.0 * sr)]
        clip48 = np.concatenate([p1, p2, p3])
        res_e2e = witness.wt_stage(clip48, rec_dir)
        e2e_kinds = {e["kind"] for e in res_e2e["edits"]}
        e2e_summary = res_e2e["summary"]
        if res_e2e["verdict"] != "spliced" or e2e_kinds != {"cut", "splice"}:
            print(f"FAIL test_wt_stage_mixed_cut_and_splice_summary: e2e expected spliced with cut+splice, got verdict={res_e2e['verdict']!r}, edits={e2e_kinds}")
            sys.exit(1)
        if "cut" not in e2e_summary or "removed at" not in e2e_summary:
            print(f"FAIL test_wt_stage_mixed_cut_and_splice_summary: e2e summary omits cut: {e2e_summary!r}")
            sys.exit(1)

    print("PASS test_wt_stage_mixed_cut_and_splice_summary")


if __name__ == "__main__":
    test_wt_stage_mixed_cut_and_splice_summary()
