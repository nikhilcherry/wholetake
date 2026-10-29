import sys
import numpy as np
from roomstamp import blind
from roomstamp.spec import L2_NBANDS

try:
    for n in (0, 100, 1000, blind.STFT_WIN - 1):
        x = np.zeros(n, dtype=np.float64)
        cbp = blind.clip_band_power(x)
        bp = blind.band_power(cbp, len(x), tempo=1.0)
        expected_rows = int(n // blind.HOP)
        assert bp.shape == (expected_rows, L2_NBANDS), f"unexpected shape {bp.shape} for n={n}"
    print("PASS short_clip_band_power")
except Exception as e:
    print(f"FAIL short_clip_band_power: {type(e).__name__}: {e}")
    sys.exit(1)
