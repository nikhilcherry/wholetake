import math
import sys
import numpy as np

from roomstamp import detect
from roomstamp.spec import FS


def main() -> None:
    rng = np.random.default_rng(0)
    res = rng.standard_normal(3 * FS)

    cases = [
        ("empty_stamp", np.zeros(0, dtype=np.float64)),
        ("tiny_stamp_10", np.ones(10, dtype=np.float64)),
        ("one_sec_stamp", rng.standard_normal(FS)),
        ("zero_3s_stamp", np.zeros(3 * FS, dtype=np.float64)),
    ]

    for label, stamp in cases:
        try:
            r = detect.layer1(res, stamp)
        except Exception as exc:
            print(f"FAIL test_F279: {label} raised {type(exc).__name__}: {exc}")
            sys.exit(1)

        if r.get("windows") != 3:
            print(f"FAIL test_F279: {label} expected windows=3, got {r.get('windows')}")
            sys.exit(1)

        for k in ("frac_z5", "median_z", "clip_z", "lag_ms"):
            val = r.get(k)
            if not isinstance(val, (int, float)) or not math.isfinite(float(val)):
                print(f"FAIL test_F279: {label} key {k!r} is not finite float: {val!r}")
                sys.exit(1)

    print("PASS test_F279")


if __name__ == "__main__":
    main()
