"""Reproducer test for F017: empty audio array handling in logmel(), features(), and match()."""
from __future__ import annotations

import os
import sys

import numpy as np

os.environ["WHOLETAKE_ASR"] = "none"

from wholetake import audio, match, record


def main() -> int:
    empty = np.zeros(0, dtype=np.float32)
    try:
        mel, energy = audio.logmel(empty)
    except Exception as e:
        print(f"FAIL test_F017: logmel(np.zeros(0)) raised {type(e).__name__}: {e}")
        return 1
    if mel.shape != (0, audio.NMELS) or energy.shape != (0,):
        print(f"FAIL test_F017: unexpected logmel shapes {mel.shape}, {energy.shape}")
        return 1

    try:
        feats = audio.features(empty)
    except Exception as e:
        print(f"FAIL test_F017: features(np.zeros(0)) raised {type(e).__name__}: {e}")
        return 1
    if feats.shape != (0, audio.NMELS) or feats.dtype != np.float32:
        print(f"FAIL test_F017: unexpected features shape/dtype {feats.shape}, {feats.dtype}")
        return 1

    rec_dir = os.path.join("testdata", "witness", "record_noasr")
    rec = record.load(rec_dir)
    res = match.match(rec, empty)
    if res.get("verdict") != "not_found" or res.get("pieces") != []:
        print(f"FAIL test_F017: unexpected verdict/pieces on empty clip: {res}")
        return 1
    if res.get("tempo") != 1.0:
        print(f"FAIL test_F017: empty clip corrupted tempo to {res.get('tempo')} instead of 1.0")
        return 1

    print("PASS test_F017")
    return 0


if __name__ == "__main__":
    sys.exit(main())
