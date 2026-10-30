"""Reproducer for F249: _restore_audio numbers restored_audio files by unsorted L1 edit index."""
import os
import shutil
import sys
import tempfile
import numpy as np

from roomstamp import engine, wavio, wt_detect
from roomstamp.spec import FS


def test_restore_audio_numbering_matches_sorted_verdict_edits() -> None:
    name = "test_restore_audio_numbering_matches_sorted_verdict_edits"
    tmp = tempfile.mkdtemp(prefix="f249_")
    try:
        feed_path = os.path.join(tmp, "feed_1000.wav")
        t_rng = np.arange(20 * FS, dtype=np.float64) / FS
        wavio.write(feed_path, 0.2 * np.sin(2.0 * np.pi * 440.0 * t_rng))

        sl = wt_detect.SessionLog(
            sid="session_1000",
            path=os.path.join(tmp, "session_1000.jsonl"),
            header={}, 
            secs=[],
            t_start=1000,
            t_end=1020,
            key_id="0" * 16,
            params=engine.StampParams(),
            feed_path=feed_path,
            venue_name="Test Venue",
        )

        doc = wt_detect._empty_doc(["wt-detect"])
        doc["warnings_base"] = []
        doc["clip"] = {"duration_s": 20.0}
        doc["layers"]["l1"] = wt_detect._stage(
            "present",
            "s2",
            "verified",
            recorded_at_unix=1000.0,
            venue_t_start=1000.0,
            venue_t_end=1020.0,
            content_match=True,
            integrity_checked=True,
            edits=[
                {
                    "kind": "foreign",
                    "clip_t": 0.0,
                    "clip_t_range": [0.0, 1.5],
                    "removed_s": None,
                    "duration_s": 1.5,
                    "venue_t_from": 1000.0,
                    "venue_t_to": 1000.0,
                    "precision_s": 0.5,
                    "source": "l1",
                },
                {
                    "kind": "removed",
                    "clip_t": 12.0,
                    "clip_t_range": [12.0, 12.0],
                    "removed_s": 0.5,
                    "venue_t_from": 1012.0,
                    "venue_t_to": 1012.5,
                    "precision_s": 0.002,
                    "source": "l1",
                },
                {
                    "kind": "removed",
                    "clip_t": 5.0,
                    "clip_t_range": [5.0, 5.0],
                    "removed_s": 0.3,
                    "venue_t_from": 1005.0,
                    "venue_t_to": 1005.3,
                    "precision_s": 0.002,
                    "source": "l1",
                },
            ],
        )

        out_path = os.path.join(tmp, "detection.json")
        wt_detect._restore_audio(doc, sl, out_path)
        wt_detect.fuse(doc)

        verdict_edits = doc["verdict"]["edits"]
        removed = [e for e in verdict_edits if e.get("kind") == "removed"]
        if len(removed) != 2:
            print(f"FAIL {name}: expected 2 removed edits, got {len(removed)}")
            sys.exit(1)
        if removed[0].get("restored_audio") != "detection_edit0.wav":
            print(
                f"FAIL {name}: first verdict edit (clip_t={removed[0].get('clip_t')}) "
                f"has restored_audio={removed[0].get('restored_audio')!r} instead of 'detection_edit0.wav'"
            )
            sys.exit(1)
        if removed[1].get("restored_audio") != "detection_edit1.wav":
            print(
                f"FAIL {name}: second verdict edit (clip_t={removed[1].get('clip_t')}) "
                f"has restored_audio={removed[1].get('restored_audio')!r} instead of 'detection_edit1.wav'"
            )
            sys.exit(1)
        for fn in ("detection_edit0.wav", "detection_edit1.wav"):
            if not os.path.isfile(os.path.join(tmp, fn)):
                print(f"FAIL {name}: missing restored file {fn}")
                sys.exit(1)
        print(f"PASS {name}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    test_restore_audio_numbering_matches_sorted_verdict_edits()
