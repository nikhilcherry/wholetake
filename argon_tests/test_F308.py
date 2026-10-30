"""Reproducer for F308: detect() returns non-JSON-serializable numpy scalars when tempos or taus contain numpy scalar types."""
import json
import sys
import numpy as np

from roomstamp import blind
from roomstamp.spec import FS


def main() -> None:
    rng = np.random.default_rng(0)
    clip = rng.standard_normal(int(9.0 * FS)).astype(np.float64) * 0.1
    key = b"\x11" * 32
    src = blind.RandomnessSource(unanchored=True)

    try:
        res = blind.detect(
            clip,
            key,
            src,
            t_lo=1000,
            t_hi=1040,
            n_null=5,
            tempos=np.array([1.0], dtype=np.float32),
            taus=np.array([0, 4], dtype=np.int64),
            seed=0,
            pair=False,
        )
        json.dumps(res)
        if type(res["tempo"]) is not float or type(res["reverb_tau_ms"]) is not float:
            print(f"FAIL test_F308: expected Python float for tempo/reverb_tau_ms, got {type(res['tempo'])} and {type(res['reverb_tau_ms'])}")
            sys.exit(1)

        res2 = blind.detect(
            clip,
            key,
            src,
            t_lo=1000,
            t_hi=1040,
            n_null=5,
            tempos=[np.int64(1)],
            taus=[np.int64(0)],
            seed=0,
            pair=False,
        )
        json.dumps(res2)
        if type(res2["tempo"]) is not float or type(res2["reverb_tau_ms"]) is not float:
            print(f"FAIL test_F308: expected Python float for tempo/reverb_tau_ms, got {type(res2['tempo'])} and {type(res2['reverb_tau_ms'])}")
            sys.exit(1)
    except Exception as exc:
        print(f"FAIL test_F308: {exc}")
        sys.exit(1)

    print("PASS test_F308")


if __name__ == "__main__":
    main()
