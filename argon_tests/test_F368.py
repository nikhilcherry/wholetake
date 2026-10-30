from __future__ import annotations

import os
import sys

import numpy as np

os.environ["WHOLETAKE_ASR"] = "none"

from wholetake import audio, match, record


def main() -> int:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    talk_path = os.path.join(root, "testdata", "witness", "talk.wav")
    other_path = os.path.join(root, "testdata", "witness", "other.wav")

    w_speech = audio.decode(talk_path)[: 6 * audio.SR]
    w_sparse = np.concatenate([w_speech, np.zeros(24 * audio.SR, dtype=np.float32)])
    Fw = audio.features(w_sparse)

    clip = audio.decode(other_path)[5 * audio.SR : 15 * audio.SR]
    Fc = audio.features(clip)

    track = match._scan(Fc, Fw, [(0, len(Fw))])
    zs = [z for _, s, _, z in track if s is not None]
    max_z = max(zs) if zs else 0.0
    if max_z > 15.0:
        print(f"FAIL test_F368: MAD collapse in _scan on sparse witness inflated robust z-score on unrelated speech to {max_z:.1f}")
        return 1

    rec = record.Record(
        meta={"format": record.FORMAT, "id": "sparse-test", "duration_s": round(len(w_sparse) / audio.SR, 3)},
        words=[],
        feats=Fw,
    )
    res = match.match(rec, clip)
    if res["verdict"] != "not_found" or res["pieces"]:
        print(f"FAIL test_F368: expected not_found on unrelated clip vs sparse witness, got {res['verdict']} with {len(res['pieces'])} pieces")
        return 1

    print(f"PASS test_F368 (max_z={max_z:.2f}, verdict={res['verdict']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
