"""Reproducer for F142: STFT channel estimator uses >= 2 s frames for hall reverb tails."""
from __future__ import annotations

import sys
import numpy as np

from roomstamp import l1verify, selftest
from roomstamp.spec import FS


def main() -> int:
    # 1. Long pieces (>= 5.46 s) must use >= 2 s STFT frames (>= 96,000 samples at 48 kHz)
    nps_10s = l1verify._nperseg(10 * FS)
    if nps_10s < 2 * FS:
        print(f"FAIL test_F142: _nperseg(10 * FS) returned {nps_10s} ({nps_10s / FS:.3f} s), expected >= {2 * FS} (>= 2.0 s)")
        return 1
    if nps_10s != (1 << 17):
        print(f"FAIL test_F142: expected power-of-two 131072 (1 << 17), got {nps_10s}")
        return 1

    # 2. Shorter pieces must still step down adaptively via nps <= n // 2
    if l1verify._nperseg(3 * FS) != (1 << 16):
        print(f"FAIL test_F142: expected _nperseg(3 * FS) == 65536, got {l1verify._nperseg(3 * FS)}")
        return 1
    if l1verify._nperseg(1 * FS) != (1 << 14):
        print(f"FAIL test_F142: expected _nperseg(1 * FS) == 16384, got {l1verify._nperseg(1 * FS)}")
        return 1

    # 3. Verify Channel speech cancellation on a 1.0 s RT60 hall recording
    speech = selftest.load_speech()[:15 * FS].astype(np.float64)
    rec = selftest.simulate(speech, rt60=1.0, snr_db=35.0, seed=42)
    lead = int(1.3 * FS)
    y = rec[lead:lead + len(speech)]
    ch = l1verify.Channel(y, speech)
    if ch.nps < 2 * FS:
        print(f"FAIL test_F142: Channel.nps is {ch.nps} (< {2 * FS})")
        return 1
    if ch.removed_db < 18.0:
        print(f"FAIL test_F142: Channel speech cancellation too low on 1.0 s RT60 hall: {ch.removed_db:.2f} dB")
        return 1

    print(f"PASS test_F142 (nps={ch.nps} samples = {ch.nps / FS:.2f}s, removed_db={ch.removed_db:.1f} dB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
