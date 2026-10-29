import sys
import numpy as np
from roomstamp import blind, spec

def main():
    rng = np.random.default_rng(42)
    clip = rng.standard_normal(int(10 * spec.FS))
    key = b'\x01' * 32
    src = blind.RandomnessSource(unanchored=True)
    try:
        res = blind.detect(clip, key, src, 1000, 1060, n_null=2, tempos=(1.25,), taus=(0.0,), seed=1, pair=False)
    except KeyError as e:
        print(f'FAIL custom_tempo_no_1.0: KeyError {e}')
        sys.exit(1)
    except Exception as e:
        print(f'FAIL custom_tempo_no_1.0: unexpected {type(e).__name__}: {e}')
        sys.exit(1)
    if res.get('tempo') != 1.25:
        print(f'FAIL custom_tempo_no_1.0: expected tempo 1.25, got {res.get("tempo")}')
        sys.exit(1)
    print('PASS custom_tempo_no_1.0')

if __name__ == '__main__':
    main()
