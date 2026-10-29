import sys
import numpy as np
from roomstamp import engine, spec

def test_stamper_zero_ref_rms_seed():
    key = b"\x01" * 32
    t0 = 1793209200
    for mode in ("file", "pa", "table"):
        params = engine.StampParams(mode=mode)
        x = np.zeros(engine.FS, dtype=np.float64)
        out, stamp, _ = engine.render(x, key, t0, params, return_parts=True, ref_rms=0.0)
        if not (np.all(np.isfinite(out)) and np.all(np.isfinite(stamp))):
            print(f"FAIL test_stamper_zero_ref_rms_seed: mode={mode} ref_rms=0.0 with silent block produced NaN/Inf in output/stamp")
            sys.exit(1)
        eng = engine.Stamper(params, ref_rms=0.0)
        eng.ref_rms = 0.0
        ks = spec.SecondKeystream(key, t0, None, spec.round_for(t0), spec.ZERO_R)
        ps = engine.prepare_second(ks, ks.signs, params.l2_delta_db if mode != "table" else 0.0)
        o_blk, s_blk, _ = eng.process(np.zeros(engine.BLOCK), ps, 0)
        if not (np.all(np.isfinite(o_blk)) and np.all(np.isfinite(s_blk))):
            print(f"FAIL test_stamper_zero_ref_rms_seed: direct process with ref_rms=0.0 produced NaN/Inf")
            sys.exit(1)
    print("PASS test_stamper_zero_ref_rms_seed")

if __name__ == "__main__":
    test_stamper_zero_ref_rms_seed()
