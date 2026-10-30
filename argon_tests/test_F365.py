import sys
import numpy as np
from roomstamp import blind
from roomstamp.spec import L2_NBANDS

def main() -> None:
    try:
        # 1. Clip shorter than STFT_WIN -> band_power returns (0, L2_NBANDS) -> features
        cbp = blind.clip_band_power(np.zeros(100))
        P = blind.band_power(cbp, 100, 1.0)
        assert P.shape == (0, L2_NBANDS), f"unexpected P shape: {P.shape}"
        F = blind.features(P)
        assert F.shape == (0, L2_NBANDS), f"unexpected F shape: {F.shape}"
        assert np.all(np.isfinite(F))

        # 2. Tiny tempo where int(n_samples * tempo // HOP) == 0
        cbp2 = blind.clip_band_power(np.ones(48000))
        P2 = blind.band_power(cbp2, 48000, 1e-4)
        assert P2.shape == (0, L2_NBANDS), f"unexpected P2 shape: {P2.shape}"
        F2 = blind.features(P2)
        assert F2.shape == (0, L2_NBANDS), f"unexpected F2 shape: {F2.shape}"

        # 3. reverb_variant on empty feature array (both tau=0 and tau>0)
        R0 = blind.reverb_variant(F, 0.0)
        R4 = blind.reverb_variant(F, 4.0)
        assert R0.shape == (0, L2_NBANDS), f"unexpected R0 shape: {R0.shape}"
        assert R4.shape == (0, L2_NBANDS), f"unexpected R4 shape: {R4.shape}"

        # 4. Single-row band power through features and reverb_variant
        P1 = np.ones((1, L2_NBANDS))
        F1 = blind.features(P1)
        assert F1.shape == (1, L2_NBANDS), f"unexpected F1 shape: {F1.shape}"
        assert np.all(np.isfinite(F1))
        R1 = blind.reverb_variant(F1, 4.0)
        assert R1.shape == (1, L2_NBANDS), f"unexpected R1 shape: {R1.shape}"
        assert np.all(np.isfinite(R1))
    except Exception as e:
        print(f"FAIL test_F365: {type(e).__name__}: {e}")
        sys.exit(1)
    print("PASS test_F365")

if __name__ == "__main__":
    main()
