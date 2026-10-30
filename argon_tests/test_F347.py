"""Reproducer test for F347: gumbel_p() and detect() on zero-variance clips."""
from __future__ import annotations

import sys
import numpy as np

from roomstamp import blind
from roomstamp.spec import FS


def main() -> None:
    name = "test_F347_zero_variance_gumbel_p"
    try:
        # 1. Direct unit tests of gumbel_p on degenerate zero-variance null arrays
        p0 = blind.gumbel_p(0.0, np.zeros(50))
        if p0 != 1.0:
            print(f"FAIL {name}: gumbel_p(0.0, zeros)={p0}, expected 1.0")
            sys.exit(1)

        p_const = blind.gumbel_p(3.5, np.full(50, 3.5))
        if p_const != 1.0:
            print(f"FAIL {name}: gumbel_p(3.5, full(3.5))={p_const}, expected 1.0")
            sys.exit(1)

        p_above = blind.gumbel_p(1.0, np.zeros(50))
        if p_above != 0.0:
            print(f"FAIL {name}: gumbel_p(1.0, zeros)={p_above}, expected 0.0")
            sys.exit(1)

        # 2. End-to-end detect() tests on pure silence and constant DC clips
        key = b"\x11" * 32
        src = blind.RandomnessSource(unanchored=True)
        t_lo, t_hi = 1700000000, 1700000040

        for label, clip in (
            ("silence", np.zeros(10 * FS, dtype=np.float64)),
            ("constant_dc", np.full(10 * FS, 0.25, dtype=np.float64)),
        ):
            r = blind.detect(
                clip, key, src, t_lo, t_hi,
                n_null=50, tempos=(1.0,), taus=(0.0,), seed=42,
            )
            if r["verdict"] != "absent" or r["present"] is not False:
                print(f"FAIL {name}: {label} verdict={r['verdict']!r}, present={r['present']!r}")
                sys.exit(1)
            if r["z"] != 0.0 or r["sigma_over_null"] != 0.0 or r["beats"] != "0/50":
                print(f"FAIL {name}: {label} unexpected stats z={r['z']}, sigma={r['sigma_over_null']}, beats={r['beats']}")
                sys.exit(1)
            if r["p_value"] != 1.0:
                print(f"FAIL {name}: {label} clip produced p_value={r['p_value']}, expected 1.0")
                sys.exit(1)
    except Exception as exc:
        print(f"FAIL {name}: unexpected exception: {exc}")
        sys.exit(1)

    print(f"PASS {name}")
    sys.exit(0)


if __name__ == "__main__":
    main()
