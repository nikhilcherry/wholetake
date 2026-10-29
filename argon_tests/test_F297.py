import os
import secrets
import shutil
import sys
import tempfile

from roomstamp import wavio, wt_detect, wt_test


def main() -> None:
    os.environ["WHOLETAKE_ASR"] = "none"
    root = tempfile.mkdtemp(prefix="test_f297_")
    try:
        key = secrets.token_bytes(32)
        kf = os.path.join(root, "venue.key")
        with open(kf, "w", encoding="utf-8") as f:
            f.write(key.hex())

        pa = os.path.join(root, "venue_pa")
        x, out = wt_test.make_venue(pa, "pa", key, seconds=40.0)
        sess_file = os.path.join(pa, f"session_{wt_test.T0}.jsonl")

        # Simulate a live emitter mid-write or power loss leaving a truncated trailing line
        with open(sess_file, "a", encoding="utf-8") as f:
            f.write('{"type": "sec", "t": 1793430640, "randomness": "deadbeef')

        clip_path = os.path.join(root, "clip.wav")
        y = wt_test.phone(out, x, "pa", 8.0, 20.0)
        wavio.write(clip_path, y)

        out_json = os.path.join(root, "detection.json")
        doc = wt_detect.run(clip_path, out_path=out_json, venue_dir=pa, key_file=kf, fast=True)

        verdict = doc.get("verdict", {}).get("recorded_here")
        l1_status = doc.get("layers", {}).get("l1", {}).get("status")
        warns = doc.get("warnings", [])

        if verdict != "verified" or l1_status != "present" or any("session_log_unreadable" in w for w in warns):
            print(
                f"FAIL test_truncated_last_jsonl_line_resilience: "
                f"Truncated final line in session_*.jsonl caused entire 40s session to be discarded! "
                f"verdict={verdict}, warnings={warns}"
            )
            sys.exit(1)

        # Also verify explicit --session with a truncated trailing line works cleanly
        doc_sess = wt_detect.run(
            clip_path, out_path=out_json, venue_dir=pa, key_file=kf, session_log=sess_file, fast=True
        )
        if doc_sess.get("verdict", {}).get("recorded_here") != "verified":
            print(
                f"FAIL test_truncated_last_jsonl_line_resilience: "
                f"--session failed on truncated trailing line: verdict={doc_sess.get('verdict', {}).get('recorded_here')}"
            )
            sys.exit(1)

        print("PASS test_truncated_last_jsonl_line_resilience")
        sys.exit(0)
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    main()
