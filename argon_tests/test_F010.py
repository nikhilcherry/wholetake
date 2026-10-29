import sys
import numpy as np
from roomstamp import blind, spec

def main():
    rng = np.random.default_rng(42)
    clip = rng.standard_normal(int(8.0 * spec.FS)) * 0.1
    key = b"\x11" * 32
    src = blind.RandomnessSource(unanchored=True)

    try:
        r1 = blind.detect(clip, key, src, t_lo=10, t_hi=20, n_null=2, tempos=(1.0,), taus=(0.0,), seed=1, pair=False)
    except Exception as e:
        print(f"FAIL F010_small_t_lo: {e}")
        sys.exit(1)
    if r1["window"][0] < 1 or r1["window"][1] - r1["window"][0] < 28:
        print(f"FAIL F010_small_t_lo: unexpected window {r1['window']}")
        sys.exit(1)

    r2 = blind.detect(clip, key, src, t_lo=2000, t_hi=1900, n_null=2, tempos=(1.0,), taus=(0.0,), seed=1, pair=False)
    if r2["window"] != [1900, 2000]:
        print(f"FAIL F010_inverted_window: expected [1900, 2000], got {r2['window']}")
        sys.exit(1)

    print("PASS F010")

if __name__ == "__main__":
    main()
