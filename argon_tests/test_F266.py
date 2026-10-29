import os
import sys

os.environ["WHOLETAKE_ASR"] = "none"

from wholetake import audio, match, record, selftest


def main() -> int:
    here = os.path.join("testdata", "witness")
    rec = record.load(os.path.join(here, "record_noasr"))
    W = audio.decode(os.path.join(here, "talk.wav"))

    failed = False

    # 1. 0.80x slowdown faithful clip
    clip_080 = selftest.atempo(selftest.seg(W, 15.0, 30.0), 0.80)
    r_080 = match.match(rec, clip_080)
    p_080 = r_080.get("pieces", [])
    ok_080 = (
        r_080.get("verdict") == "faithful"
        and abs(r_080.get("tempo", 0.0) - 0.80) <= 0.02
        and len(p_080) == 1
        and abs(p_080[0]["witness_start_s"] - 15.0) <= 0.25
        and abs(p_080[0]["witness_end_s"] - 30.0) <= 0.25
    )
    if ok_080:
        print(f"PASS test_03_slowdown_080x_faithful (verdict={r_080['verdict']}, tempo={r_080['tempo']})")
    else:
        print(f"FAIL test_03_slowdown_080x_faithful: expected faithful on 0.80x clip, got {r_080.get('verdict')} (tempo={r_080.get('tempo')})")
        failed = True

    # 2. 1.15x midpoint speedup faithful clip (formerly in 0.10 blind spot between 1.10 and 1.20)
    clip_115 = selftest.atempo(selftest.seg(W, 60.0, 74.0), 1.15)
    r_115 = match.match(rec, clip_115)
    p_115 = r_115.get("pieces", [])
    ok_115 = (
        r_115.get("verdict") == "faithful"
        and abs(r_115.get("tempo", 0.0) - 1.15) <= 0.02
        and len(p_115) == 1
        and abs(p_115[0]["witness_start_s"] - 60.0) <= 0.25
        and abs(p_115[0]["witness_end_s"] - 74.0) <= 0.25
    )
    if ok_115:
        print(f"PASS test_midpoint_115x_faithful (verdict={r_115['verdict']}, tempo={r_115['tempo']})")
    else:
        print(f"FAIL test_midpoint_115x_faithful: expected faithful on 1.15x clip, got {r_115.get('verdict')} (tempo={r_115.get('tempo')})")
        failed = True

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
