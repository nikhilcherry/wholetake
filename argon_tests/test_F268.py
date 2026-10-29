import os
import shutil
import sys
import tempfile
import numpy as np

os.environ["WHOLETAKE_ASR"] = "none"

from roomstamp import wavio, wt_detect
from roomstamp.spec import FS


def main():
    tmp = tempfile.mkdtemp(prefix="test_f268_")
    try:
        talk_path = os.path.join("testdata", "witness", "talk.wav")
        rec_dir = os.path.join("testdata", "witness", "record_noasr")
        witness_arg = rec_dir if os.path.isdir(rec_dir) else talk_path

        x = wavio.read(talk_path)
        # 1. Edited clip: 5..25s with a 0.50s cut at 8s into clip (13s into talk)
        part1 = x[int(5.0 * FS):int(13.0 * FS)]
        part2 = x[int(13.5 * FS):int(25.0 * FS)]
        clip_edit = np.concatenate([part1, part2])
        edit_wav = os.path.join(tmp, "clip_edited.wav")
        wavio.write(edit_wav, clip_edit)

        # 2. Intact clip: 5..20s
        clip_intact = x[int(5.0 * FS):int(20.0 * FS)]
        intact_wav = os.path.join(tmp, "clip_intact.wav")
        wavio.write(intact_wav, clip_intact)

        nokey = os.path.join(tmp, "nonexistent.key")
        empty_venue = os.path.join(tmp, "empty_logs")
        os.makedirs(empty_venue, exist_ok=True)

        doc_edit = wt_detect.run(
            edit_wav,
            venue_dir=empty_venue,
            key_file=nokey,
            witness=witness_arg,
        )
        v_edit = doc_edit["verdict"]
        if v_edit["recorded_here"] != "unknown":
            print(f"FAIL F268: expected recorded_here='unknown', got {v_edit['recorded_here']!r}")
            sys.exit(1)
        if not v_edit["content_match"]:
            print("FAIL F268: expected content_match=True on edited witness clip")
            sys.exit(1)
        if v_edit["integrity"] != "edited" or not v_edit["edits"]:
            print(f"FAIL F268: expected integrity='edited' with edits, got {v_edit['integrity']!r}, {v_edit['edits']}")
            sys.exit(1)
        es = wt_detect._edit_summary(v_edit["edits"])
        head_edit = v_edit["headline"]
        if "Couldn't check this clip" in head_edit:
            print(f"FAIL F268: witness-only edited clip headline discarded witness result: {head_edit!r}")
            sys.exit(1)
        if "Words match" not in head_edit or es not in head_edit:
            print(f"FAIL F268: headline missing 'Words match' or edit summary {es!r}: {head_edit!r}")
            sys.exit(1)

        doc_intact = wt_detect.run(
            intact_wav,
            venue_dir=empty_venue,
            key_file=nokey,
            witness=witness_arg,
        )
        v_intact = doc_intact["verdict"]
        head_intact = v_intact["headline"]
        if v_intact["recorded_here"] != "unknown" or not v_intact["content_match"] or v_intact["integrity"] != "intact":
            print(f"FAIL F268: unexpected intact verdict fields: {v_intact}")
            sys.exit(1)
        if "Couldn't check this clip" in head_intact:
            print(f"FAIL F268: witness-only intact clip headline discarded witness result: {head_intact!r}")
            sys.exit(1)
        if "Words match" not in head_intact or "Complete: nothing removed" not in head_intact:
            print(f"FAIL F268: intact headline missing expected text: {head_intact!r}")
            sys.exit(1)

        print("PASS F268")
        sys.exit(0)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
