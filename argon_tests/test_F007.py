import json
import math
import sys
import numpy as np
from roomstamp import blind
from roomstamp.spec import FS

def main():
    rng = np.random.default_rng(42)
    clip = rng.standard_normal(10 * FS).astype(np.float64)
    key = b'\x01' * 32
    src = blind.RandomnessSource(unanchored=True)
    for n_null in (-1, 0, 1):
        try:
            res = blind.detect(clip, key, src, 1700000000, 1700000040, n_null=n_null, tempos=(1.0,), seed=1)
        except Exception as e:
            print(f'FAIL f007_n_null: detect(n_null={n_null}) raised {type(e).__name__}: {e}')
            sys.exit(1)
        for field in ('p_value', 'sigma_over_null'):
            v = res.get(field)
            if v is None or math.isnan(v) or math.isinf(v):
                print(f'FAIL f007_n_null: detect(n_null={n_null}) produced invalid {field}={v}')
                sys.exit(1)
        try:
            json.dumps(res, allow_nan=False)
        except ValueError as e:
            print(f'FAIL f007_n_null: detect(n_null={n_null}) result not strict JSON: {e}')
            sys.exit(1)
    print('PASS f007_n_null')

if __name__ == '__main__':
    main()
