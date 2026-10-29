import sys
import numpy as np
from wholetake.match import Piece, _subframe_off


def main() -> int:
    Fc = np.zeros((100, 40), dtype=np.float32)
    for k in range(100):
        Fc[k, k % 40] = 1.0
    Fw = np.zeros((120, 40), dtype=np.float32)
    Fw[15:115] = Fc

    piece = Piece(u0=0, u1=100, c0=15.0, c1=0.0)

    # 1. Empty range (lo == hi and lo > hi)
    off_eq = _subframe_off(Fc, Fw, piece, 20, 20)
    if abs(off_eq - 15.0) > 1e-6:
        print(f"FAIL test_F018: empty range lo==hi returned {off_eq}, expected 15.0")
        return 1

    off_gt = _subframe_off(Fc, Fw, piece, 30, 20)
    if abs(off_gt - 15.0) > 1e-6:
        print(f"FAIL test_F018: empty range lo>hi returned {off_gt}, expected 15.0")
        return 1

    # 2. Silent range (all frames in [lo, hi) zeroed by silence gate)
    Fc_silent = Fc.copy()
    Fc_silent[20:60] = 0.0
    off_silent = _subframe_off(Fc_silent, Fw, piece, 20, 60)
    if abs(off_silent - 15.0) > 1e-6:
        print(f"FAIL test_F018: silent range returned {off_silent}, expected 15.0")
        return 1

    # 3. Active speech range still refines to true offset
    off_active = _subframe_off(Fc, Fw, piece, 20, 60)
    if abs(off_active - 15.0) > 1e-6:
        print(f"FAIL test_F018: active speech returned {off_active}, expected 15.0")
        return 1

    print("PASS test_F018")
    return 0


if __name__ == "__main__":
    sys.exit(main())
