import json
import math
import sys
import warnings
import numpy as np

from roomstamp import blind
from roomstamp.spec import FS


def test_clip_band_power_and_features_extreme_float():
    rng = np.random.default_rng(42)
    base = rng.standard_normal(10 * FS)
    for scale in (1e155, 1e200, 1e300):
        clip = base * scale
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            cbp = blind.clip_band_power(clip)
            bp = blind.band_power(cbp, len(clip), 1.0)
            F = blind.features(bp)
            overflow_warns = [item for item in w if "overflow" in str(item.message).lower()]
            assert not overflow_warns, f"unexpected overflow warning at scale {scale}: {overflow_warns[0].message}"
        assert np.all(np.isfinite(cbp[0])), f"non-finite clip_band_power at scale {scale}"
        assert np.all(np.isfinite(F)), f"non-finite features at scale {scale}"


def test_detect_extreme_finite_float_overflow():
    rng = np.random.default_rng(7)
    base_clip = rng.standard_normal(10 * FS) * 0.2
    huge_clip = base_clip * 1e200
    key = b"\x11" * 32
    src = blind.RandomnessSource(unanchored=True)
    t_lo, t_hi = 1700000000, 1700000040

    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        r_huge = blind.detect(
            huge_clip, key, src, t_lo, t_hi, n_null=10, tempos=(1.0,), taus=(0.0,), seed=1
        )
        overflow_warns = [item for item in w if "overflow" in str(item.message).lower()]
        assert not overflow_warns, f"unexpected overflow warning in detect(): {overflow_warns[0].message}"

    assert math.isfinite(r_huge["z"]), f"non-finite z={r_huge['z']} on 1e200 amplitude clip"
    assert math.isfinite(r_huge["sigma_over_null"]), f"non-finite sigma_over_null={r_huge['sigma_over_null']}"
    assert math.isfinite(r_huge["p_value"]), f"non-finite p_value={r_huge['p_value']}"
    json.dumps(r_huge, allow_nan=False)

    r_base = blind.detect(
        base_clip, key, src, t_lo, t_hi, n_null=10, tempos=(1.0,), taus=(0.0,), seed=1
    )
    assert r_huge["z"] == r_base["z"], f"scale invariance broken: {r_huge['z']} vs {r_base['z']}"
    assert r_huge["sigma_over_null"] == r_base["sigma_over_null"]
    assert r_huge["p_value"] == r_base["p_value"]


if __name__ == "__main__":
    failed = False
    for fn in (test_clip_band_power_and_features_extreme_float, test_detect_extreme_finite_float_overflow):
        name = fn.__name__
        try:
            fn()
            print(f"PASS {name}")
        except Exception as e:
            print(f"FAIL {name}: {type(e).__name__}: {e}")
            failed = True
    sys.exit(1 if failed else 0)
