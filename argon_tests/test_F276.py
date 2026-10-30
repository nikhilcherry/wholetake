"""Reproducer test for F276: Piece clip_end_s and witness_end_s exceed clip_duration_s and rec.duration."""
from __future__ import annotations

import os
import sys

os.environ["WHOLETAKE_ASR"] = "none"

from wholetake import audio, match, record


def main() -> int:
    talk_path = os.path.join("testdata", "witness", "talk.wav")
    x = audio.decode(talk_path)

    # 1. 8.0 s witness record matched against an 8.0 s clip
    w8 = x[: 8 * audio.SR]
    rec8 = record.Record(
        meta={"format": record.FORMAT, "id": "test-8s", "duration_s": 8.0},
        words=[],
        feats=audio.features(w8),
    )
    res8 = match.match(rec8, w8)
    if not res8["pieces"]:
        print("FAIL piece_witness_bounds_within_record_duration: no pieces returned")
        return 1
    for p in res8["pieces"]:
        ws, we = p["witness_start_s"], p["witness_end_s"]
        cs, ce = p["clip_start_s"], p["clip_end_s"]
        if not (0.0 <= ws <= we <= rec8.duration):
            print(f"FAIL piece_witness_bounds_within_record_duration: witness span [{ws}, {we}] exceeds record [0.0, {rec8.duration}]")
            return 1
        if not (0.0 <= cs <= ce <= res8["clip_duration_s"]):
            print(f"FAIL piece_witness_bounds_within_record_duration: clip span [{cs}, {ce}] exceeds clip [0.0, {res8['clip_duration_s']}]")
            return 1

    # 2. 10.0 s clip matched against a longer witness record
    w20 = x[: 20 * audio.SR]
    rec20 = record.Record(
        meta={"format": record.FORMAT, "id": "test-20s", "duration_s": 20.0},
        words=[],
        feats=audio.features(w20),
    )
    c10 = x[: 10 * audio.SR]
    res10 = match.match(rec20, c10)
    if not res10["pieces"]:
        print("FAIL piece_clip_bounds_within_clip_duration: no pieces returned")
        return 1
    for p in res10["pieces"]:
        ws, we = p["witness_start_s"], p["witness_end_s"]
        cs, ce = p["clip_start_s"], p["clip_end_s"]
        if not (0.0 <= cs <= ce <= res10["clip_duration_s"]):
            print(f"FAIL piece_clip_bounds_within_clip_duration: clip span [{cs}, {ce}] exceeds clip [0.0, {res10['clip_duration_s']}]")
            return 1
        if not (0.0 <= ws <= we <= rec20.duration):
            print(f"FAIL piece_clip_bounds_within_clip_duration: witness span [{ws}, {we}] exceeds record [0.0, {rec20.duration}]")
            return 1

    print("PASS piece_witness_bounds_within_record_duration")
    return 0


if __name__ == "__main__":
    sys.exit(main())
