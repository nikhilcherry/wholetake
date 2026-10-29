import sys
import numpy as np
from roomstamp import engine, spec

def test_stamper_nan_state_poisoning():
    key = b'\x01' * 32
    params = engine.StampParams(mode='pa')
    stamper = engine.Stamper(params)
    seq = engine.KeystreamSequencer(key, 1700000000, params.l2_delta_db, lambda t: None)
    sec = seq.next()

    # Block 0 with a single NaN and Inf
    x_bad = np.zeros(engine.BLOCK, dtype=np.float32)
    x_bad[10] = np.nan
    x_bad[20] = np.inf
    x_bad[30] = -np.inf
    out0, stamp0, st0 = stamper.process(x_bad, sec, 0)

    # Subsequent clean block
    x_clean = np.full(engine.BLOCK, 0.1, dtype=np.float32)
    out1, stamp1, st1 = stamper.process(x_clean, sec, engine.BLOCK)

    if not (np.all(np.isfinite(out0)) and np.all(np.isfinite(out1)) and np.all(np.isfinite(stamp1))):
        print('FAIL stamper_nan_state_poisoning: Single NaN/Inf sample permanently poisoned Stamper output!')
        sys.exit(1)
    if not (np.isfinite(stamper.ref_rms) and np.all(np.isfinite(stamper.env_zi))):
        print('FAIL stamper_nan_state_poisoning: Stamper internal state poisoned with NaN/Inf!')
        sys.exit(1)
    print('PASS stamper_nan_state_poisoning')

if __name__ == '__main__':
    test_stamper_nan_state_poisoning()
