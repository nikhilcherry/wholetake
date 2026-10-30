"""Reproducer test for F366: detect() and clip_band_power() on 0D, (N, 0), and >2D arrays."""
import sys
import numpy as np
from roomstamp import blind
from roomstamp.spec import FS


def main() -> None:
    key = b"\x11" * 32
    src = blind.RandomnessSource(unanchored=True)
    t_lo, t_hi = 1793214220, 1793214260

    bad_shapes = [
        ("0D scalar", np.array(0.5)),
        ("2D 0-channel (short)", np.zeros((100, 0))),
        ("2D 0-channel (10s)", np.zeros((10 * FS, 0))),
        ("3D (1, N, 2)", np.zeros((1, 100, 2))),
        ("3D (N, 2, 1)", np.zeros((10 * FS, 2, 1))),
    ]

    for label, arr in bad_shapes:
        try:
            r = blind.detect(arr, key, src, t_lo, t_hi, n_null=10, tempos=(1.0,), taus=(0.0,), seed=1)
        except ValueError:
            pass
        except Exception as e:
            print(f"FAIL test_F366: detect({label}) raised {type(e).__name__}: {e} instead of ValueError")
            sys.exit(1)
        else:
            print(f"FAIL test_F366: detect({label}) did not raise ValueError, returned {r}")
            sys.exit(1)

        try:
            blind.clip_band_power(arr)
        except ValueError:
            pass
        except Exception as e:
            print(f"FAIL test_F366: clip_band_power({label}) raised {type(e).__name__}: {e} instead of ValueError")
            sys.exit(1)
        else:
            print(f"FAIL test_F366: clip_band_power({label}) did not raise ValueError")
            sys.exit(1)

    # Valid short 1D and 2D arrays should return 'too short' without raising
    for valid_arr in (np.zeros(0), np.zeros((0, 2)), np.zeros((100, 2))):
        r = blind.detect(valid_arr, key, src, t_lo, t_hi, n_null=10, tempos=(1.0,), taus=(0.0,), seed=1)
        if r.get("verdict") != "too short":
            print(f"FAIL test_F366: expected 'too short' for shape {valid_arr.shape}, got {r}")
            sys.exit(1)
        P, c = blind.clip_band_power(valid_arr)
        if P.shape != (0, 10) or c.shape != (0,):
            print(f"FAIL test_F366: unexpected clip_band_power shape for {valid_arr.shape}: {P.shape}, {c.shape}")
            sys.exit(1)

    print("PASS test_F366")


if __name__ == "__main__":
    main()
