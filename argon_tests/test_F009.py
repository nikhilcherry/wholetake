import sys
import numpy as np
from roomstamp import blind, spec

def main():
    rng = np.random.default_rng(42)
    mono = rng.standard_normal(int(9.0 * spec.FS))
    stereo = np.stack([mono, mono], axis=-1)
    key = b"\x01" * 32
    src = blind.RandomnessSource(unanchored=True)
    t0 = 1793214200
    try:
        r_stereo = blind.detect(stereo, key, src, t0, t0 + 30, n_null=2, tempos=(1.0,), taus=(0.0,), seed=1, pair=False)
        r_mono = blind.detect(mono, key, src, t0, t0 + 30, n_null=2, tempos=(1.0,), taus=(0.0,), seed=1, pair=False)
    except Exception as e:
        print(f"FAIL F009: detect() crashed on 2D stereo array: {type(e).__name__}: {e}")
        sys.exit(1)
    if r_stereo.get("z") != r_mono.get("z"):
        print(f"FAIL F009: stereo downmix mismatch: {r_stereo.get('z')} != {r_mono.get('z')}")
        sys.exit(1)
    print("PASS F009")

if __name__ == "__main__":
    main()
