import sys
import numpy as np
from roomstamp import blind


def test_detect_min_clip_boundary_rounding():
    key = b"\x01" * 32
    src = blind.RandomnessSource(unanchored=True)

    for n_samples in (381600, 383999):
        clip = np.zeros(n_samples, dtype=np.float64)
        res = blind.detect(clip, key, src, 1000, 1030, n_null=2, tempos=(1.0,), taus=(0.0,), seed=1)
        msg = blind.summary(res)
        assert res["verdict"] == "too short", f"expected 'too short' for {n_samples} samples, got {res['verdict']!r}"
        assert res["clip_s"] < blind.MIN_CLIP_S, (
            f"contradictory summary for {n_samples}-sample clip: {msg!r} (clip_s={res['clip_s']})"
        )
        assert res["clip_s"] == 7.9, f"expected clip_s=7.9 for {n_samples} samples, got {res['clip_s']}"
        assert "TOO SHORT (8.0 s)" not in msg, f"contradictory summary: {msg!r}"
        assert "TOO SHORT (7.9 s)" in msg, f"expected 'TOO SHORT (7.9 s)' in {msg!r}"

    clip_ok = np.zeros(384000, dtype=np.float64)
    res_ok = blind.detect(clip_ok, key, src, 1000, 1030, n_null=2, tempos=(1.0,), taus=(0.0,), seed=1)
    assert res_ok["verdict"] != "too short", f"384000-sample clip (8.0 s) should not be 'too short': {res_ok}"
    assert res_ok["clip_s"] == 8.0, f"expected clip_s=8.0 for 384000 samples, got {res_ok['clip_s']}"


if __name__ == "__main__":
    try:
        test_detect_min_clip_boundary_rounding()
        print("PASS test_detect_min_clip_boundary_rounding")
    except Exception as e:
        print(f"FAIL test_detect_min_clip_boundary_rounding: {e}")
        sys.exit(1)
