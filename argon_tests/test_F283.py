import sys
import numpy as np
from roomstamp import engine, spec
from roomstamp.engine import BLOCK, StampParams, Stamper, prepare_second


def main() -> None:
    key = b"\x11" * 32
    ks0 = spec.SecondKeystream(key, 100, None, spec.round_for(100), spec.ZERO_R)
    ks1 = spec.SecondKeystream(key, 101, None, spec.round_for(101), ks0.seed)
    x = np.zeros(BLOCK, dtype=np.float64)

    bad_cases = [
        {"l1_db": float("nan")},
        {"l1_db": float("inf")},
        {"l1_db": float("-inf")},
        {"l1_db": 1e4},
        {"l1_dbfs": float("nan"), "mode": "table"},
        {"l1_dbfs": float("inf"), "mode": "table"},
        {"l1_dbfs": float("-inf"), "mode": "table"},
        {"l1_dbfs": 1e4, "mode": "table"},
        {"l2_delta_db": float("nan")},
        {"l2_delta_db": float("inf")},
        {"l2_delta_db": float("-inf")},
        {"l2_delta_db": -1.0},
        {"l2_delta_db": 1e3},
        {"voice_gain": float("nan")},
        {"voice_gain": float("inf")},
        {"voice_gain": float("-inf")},
        {"voice_gain": -0.5},
        {"voice_gain": 1e9},
        {"l1_floor": float("nan")},
        {"l1_floor": float("inf")},
        {"l1_floor": -0.1},
        {"l1_floor": 1.5},
        {"l1_db": True},
        {"voice_gain": False},
    ]

    for kw in bad_cases:
        try:
            p = StampParams(**kw)
            sec = prepare_second(ks1, ks0.signs, p.l2_delta_db if p.mode != "table" else 0.0)
            st = Stamper(p)
            out, stamp, _ = st.process(x, sec, 0)
            if not (np.all(np.isfinite(out)) and np.all(np.isfinite(stamp))):
                print(f"FAIL test_stamp_params_nonfinite_or_extreme: Non-finite StampParams {kw} produced NaN/Inf audio: out has {np.isnan(out).sum()} NaNs")
                sys.exit(1)
            print(f"FAIL test_stamp_params_nonfinite_or_extreme: StampParams({kw}) did not raise ValueError")
            sys.exit(1)
        except ValueError:
            pass

    for kw in [
        {},
        {"mode": "table", "l1_dbfs": -50.0, "l1_floor": 0.3},
        {"mode": "pa", "l1_db": -30.0, "l2_delta_db": 0.0, "voice_gain": 0.0},
        {"mode": "file", "l1_db": -35.0, "l2_delta_db": 4.0, "voice_gain": 1.0},
    ]:
        p = StampParams(**kw)
        sec = prepare_second(ks1, ks0.signs, p.l2_delta_db if p.mode != "table" else 0.0)
        st = Stamper(p)
        out, stamp, _ = st.process(x, sec, 0)
        if not (np.all(np.isfinite(out)) and np.all(np.isfinite(stamp))):
            print(f"FAIL test_stamp_params_nonfinite_or_extreme: valid params {kw} produced non-finite output")
            sys.exit(1)

    print("PASS test_stamp_params_nonfinite_or_extreme")


if __name__ == "__main__":
    main()
