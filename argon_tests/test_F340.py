import sys
import numpy as np
from scipy.signal import resample_poly
from roomstamp import l1verify, selftest
from roomstamp.spec import FS


def test_fine_lag_near_feed_boundary() -> bool:
    speech = selftest.load_speech()[:5 * FS]
    xb = l1verify._band(speech)

    # Case 1a: 1.4 s clip starting at feed[3] (within 48 samples of feed start)
    n_clip = int(1.4 * FS)  # 67200 samples
    true_start = 3
    yb_start = xb[true_start:true_start + n_clip].copy()
    g_start = [
        l1verify.Win(c8=0, f8=1, psr=30.0, rms=0.1),
        l1verify.Win(c8=4000, f8=4001, psr=30.0, rms=0.1),
    ]
    p_start = l1verify.fit_piece(g_start, yb_start, xb)
    start_err = abs(p_start.a - true_start)

    # Case 1b: 1.4 s clip ending 3 samples before feed end
    true_end_start = len(xb) - n_clip - 3
    yb_end = xb[true_end_start:true_end_start + n_clip].copy()
    f8_0 = int(round(true_end_start / l1verify.DEC))
    g_end = [
        l1verify.Win(c8=0, f8=f8_0, psr=30.0, rms=0.1),
        l1verify.Win(c8=4000, f8=f8_0 + 4000, psr=30.0, rms=0.1),
    ]
    p_end = l1verify.fit_piece(g_end, yb_end, xb)
    end_err = abs(p_end.a - true_end_start)

    if start_err > 0.25 or end_err > 0.25:
        print(f"FAIL fine_lag_near_feed_boundary: start_err={start_err:.3f} (p.a={p_start.a:.3f} vs {true_start}), "
              f"end_err={end_err:.3f} (p.a={p_end.a:.3f} vs {true_end_start})")
        return False
    print(f"PASS fine_lag_near_feed_boundary (start_err={start_err:.4f}, end_err={end_err:.4f} samples)")
    return True


def test_boundary_length_47995_fine_lag() -> bool:
    speech = selftest.load_speech()[:5 * FS]
    xb = l1verify._band(speech)
    true_off = 1003
    yb = xb[true_off:true_off + 47995].copy()
    y8 = resample_poly(yb, 1, l1verify.DEC)
    x8 = resample_poly(xb, 1, l1verify.DEC)
    assert len(y8) == l1verify.AWIN == 8000
    wins = l1verify.align_windows(y8, x8)
    if not wins or wins[0].f8 < 0:
        print("FAIL boundary_length_47995_fine_lag: align_windows did not match 47995-sample clip")
        return False
    lag, psr = l1verify._fine_lag(yb, xb, 0, wins[0].f8 * l1verify.DEC)
    if not np.isfinite(lag) or abs(lag - true_off) > 0.25:
        print(f"FAIL boundary_length_47995_fine_lag: _fine_lag returned {lag} (expected {true_off})")
        return False
    p = l1verify.fit_piece(wins, yb, xb)
    if abs(p.a - true_off) > 0.25:
        print(f"FAIL boundary_length_47995_fine_lag: fit_piece p.a={p.a:.3f} vs {true_off}")
        return False
    print("PASS boundary_length_47995_fine_lag")
    return True


if __name__ == "__main__":
    ok1 = test_fine_lag_near_feed_boundary()
    ok2 = test_boundary_length_47995_fine_lag()
    sys.exit(0 if (ok1 and ok2) else 1)
