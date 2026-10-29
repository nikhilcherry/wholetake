import sys
from unittest.mock import patch
import numpy as np
from roomstamp import blind
from roomstamp.spec import FS

def main():
    name = "f011_centisecond_carry"
    clip = np.zeros(int(8.0 * FS), dtype=np.float64)
    key = b"\x01" * 32
    src = blind.RandomnessSource(unanchored=True)
    # t_lo = 100, t_hi = 130 (since 8.0 * 1.0 + 20.0 = 28 <= 30, t_lo stays 100)
    # lag = 99.96 -> start = 100 + 99.96 / 100 = 100.9996
    with patch.object(blind, "locate", return_value={"lag": 99.96, "cut": None}):
        res = blind.detect(clip, key, src, 100, 130, n_null=2, tempos=(1.0,), taus=(0.0,), seed=0, pair=False)
    unix_t = res["recording_start_unix"]
    utc_t = res["recording_start_utc"]
    if unix_t != 101.0:
        print(f"FAIL {name}: expected recording_start_unix 101.0, got {unix_t}")
        sys.exit(1)
    if not utc_t.endswith(":01:41.00Z"):
        print(f"FAIL {name}: expected recording_start_utc ending with :01:41.00Z, got {utc_t} (unix={unix_t})")
        sys.exit(1)
    print(f"PASS {name}")

if __name__ == "__main__":
    main()
