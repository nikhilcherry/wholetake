"""Reproducer for F127: _restore_audio crashes with ValueError on empty feed slice."""
import os
import shutil
import sys
import tempfile
import numpy as np

from roomstamp import engine, wavio, wt_detect
from roomstamp.spec import FS


def main() -> None:
    tmp = tempfile.mkdtemp(prefix="test_f127_")
    try:
        feed_path = os.path.join(tmp, "feed.wav")
        wavio.write(feed_path, np.zeros(2 * FS, dtype=np.float64))
        out_path = os.path.join(tmp, "detection.json")

        sl = wt_detect.SessionLog(
            sid="session_1000",
            path=os.path.join(tmp, "session_1000.jsonl"),
            header={},
            secs=[],
            t_start=1000,
            t_end=1002,
            key_id="0" * 16,
            params=engine.StampParams(),
            feed_path=feed_path,
            venue_name="Test Venue",
        )

        doc = {
            "warnings_base": [],
            "layers": {
                "l1": {
                    "edits": [
                        {
                            "source": "l1",
                            "kind": "removed",
                            "clip_t": 0.0,
                            "removed_s": 0.5,
                            "venue_t_from": 995.0,
                            "venue_t_to": 996.0,
                        },
                        {
                            "source": "l1",
                            "kind": "removed",
                            "clip_t": 2.0,
                            "removed_s": 0.5,
                            "venue_t_from": 1010.0,
                            "venue_t_to": 1010.5,
                        },
                    ]
                }
            },
        }

        wt_detect._restore_audio(doc, sl, out_path)

        warns = doc["warnings_base"]
        if warns:
            print(f"FAIL test_F127: unexpected warnings in _restore_audio: {warns}")
            sys.exit(1)

        for i, e in enumerate(doc["layers"]["l1"]["edits"]):
            if e.get("restored_audio") is not None:
                print(f"FAIL test_F127: edit {i} should not have restored_audio set")
                sys.exit(1)

        print("PASS test_F127")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
