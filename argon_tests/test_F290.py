import json
import sys
import numpy as np
from roomstamp import blind
from roomstamp.spec import FS

def main():
    name = "detect_numpy_int_bounds_json_serializable"
    try:
        rng = np.random.default_rng(42)
        clip = rng.standard_normal(int(10 * FS)) * 0.05
        key = b"\x01" * 32
        src = blind.RandomnessSource(unanchored=True)
        bounds = np.array([1000, 1100], dtype=np.int64)
        res = blind.detect(clip, key, src, bounds[0], bounds[1], n_null=2, tempos=(1.0,), taus=(0.0,), seed=1, pair=False)
        s = json.dumps(res)
        assert isinstance(res["window"][0], int) and not isinstance(res["window"][0], np.integer)
        assert isinstance(res["window"][1], int) and not isinstance(res["window"][1], np.integer)
        print(f"PASS {name}")
    except Exception as e:
        print(f"FAIL {name}: {type(e).__name__}: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
