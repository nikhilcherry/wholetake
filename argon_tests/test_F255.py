import os
import sys
import numpy as np

os.environ["WHOLETAKE_ASR"] = "none"

from wholetake import audio, match, record
from roomstamp.witness import wt_stage


def main() -> int:
    name = "short_and_zero_length_clips"
    try:
        rng = np.random.default_rng(42)
        for n in (0, 1, 100, 240, 241, 399, 400):
            x = rng.standard_normal(n).astype(np.float32) * 0.1
            mel, energy = audio.logmel(x)
            if mel.ndim != 2 or mel.shape[1] != audio.NMELS:
                print(f"FAIL {name}: logmel({n}) bad shape {mel.shape}")
                return 1
            if energy.ndim != 1 or len(energy) != len(mel):
                print(f"FAIL {name}: energy({n}) bad shape {energy.shape}")
                return 1
            if n == 0 and len(mel) != 0:
                print(f"FAIL {name}: logmel(0) expected 0 frames, got {len(mel)}")
                return 1
            if n > 0 and len(mel) == 0:
                print(f"FAIL {name}: logmel({n}) expected >0 frames, got 0")
                return 1
            F = audio.features(x)
            if F.shape != mel.shape or F.dtype != np.float32:
                print(f"FAIL {name}: features({n}) bad shape/dtype {F.shape} {F.dtype}")
                return 1

        rec_dir = os.path.join("testdata", "witness", "record_noasr")
        rec = record.load(rec_dir)
        for n in (0, 1, 100, 240, 241, 399):
            clip = rng.standard_normal(n).astype(np.float32) * 0.1
            res = match.match(rec, clip)
            if res.get("verdict") != "not_found" or res.get("pieces") != []:
                print(f"FAIL {name}: match({n}) expected not_found, got {res.get('verdict')}")
                return 1

        for n48 in (0, 3, 300, 720, 900):
            clip48 = rng.standard_normal(n48).astype(np.float32) * 0.1
            st = wt_stage(clip48, rec_dir)
            if st.get("status") != "absent":
                print(f"FAIL {name}: wt_stage({n48}) expected status='absent', got {st.get('status')} ({st.get('summary')})")
                return 1
    except Exception as e:
        print(f"FAIL {name}: unexpected {type(e).__name__}: {e}")
        return 1

    print(f"PASS {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
