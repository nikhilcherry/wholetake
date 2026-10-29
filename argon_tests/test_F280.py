import math
import sys
import warnings
import numpy as np
from roomstamp.engine import Stamper, StampParams, KeystreamSequencer, BLOCK


def main() -> None:
    key = b"\x11" * 32
    seq = KeystreamSequencer(key, 1793209200, 4.0, lambda t: None)
    sec = seq.next()

    for mode in ("pa", "file", "table"):
        s = Stamper(StampParams(mode=mode))
        ref0 = s.ref_rms
        gain0 = s.gain

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            for cur_sec in (sec, None):
                out, stamp, st = s.process(np.zeros(0, dtype=np.float32), cur_sec, pos=0)
                assert out.dtype == np.float32 and len(out) == 0, f"bad out: {out}"
                assert stamp.dtype == np.float32 and len(stamp) == 0, f"bad stamp: {stamp}"
                assert not st.missing, "empty block within bounds should not mark missing"
                assert math.isfinite(s.ref_rms) and s.ref_rms == ref0, f"ref_rms mutated: {s.ref_rms}"
                assert math.isfinite(s.gain) and s.gain == gain0, f"gain mutated: {s.gain}"
            runtime_warns = [item for item in w if issubclass(item.category, RuntimeWarning)]
            assert not runtime_warns, f"Unexpected RuntimeWarning: {runtime_warns}"

        # Follow with 1-sample and 480-sample blocks to verify filter states are healthy
        out1, stamp1, st1 = s.process(np.array([0.1], dtype=np.float32), sec, pos=0)
        assert len(out1) == 1 and len(stamp1) == 1 and np.all(np.isfinite(out1))
        out_blk, stamp_blk, st_blk = s.process(np.full(BLOCK, 0.05, dtype=np.float32), sec, pos=1)
        assert len(out_blk) == BLOCK and np.all(np.isfinite(out_blk)) and np.all(np.isfinite(stamp_blk))

    print("PASS test_F280")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"FAIL test_F280: {type(e).__name__}: {e}")
        sys.exit(1)
