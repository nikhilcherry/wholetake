import sys
from roomstamp import cli, drand, spec


def test_check_drand_corrupt_log_entries():
    valid_hex = "ab" * 32
    orig_fetch = drand.fetch_round
    drand.fetch_round = lambda rnd, timeout=2.0: (
        {"round": rnd, "randomness": valid_hex, "signature": "00"}
        if isinstance(rnd, int) and not isinstance(rnd, bool) and rnd > 0
        else None
    )
    try:
        t0 = 1793209200
        t_r1 = spec.DRAND_GENESIS + 3 * spec.ROUND_LAG  # round_for(t_r1) == 1
        t_pre = 1000  # pre-genesis: round_for(t_pre) < 0

        secs = {
            # 1. anchored=True but 'randomness' key omitted entirely
            t0: {"type": "sec", "t": t0, "round": spec.round_for(t0), "anchored": True},
            # 2. anchored=True with randomness=None
            t0 + 1: {"type": "sec", "t": t0 + 1, "round": spec.round_for(t0 + 1), "anchored": True, "randomness": None},
            # 3. anchored=True with non-string randomness
            t0 + 2: {"type": "sec", "t": t0 + 2, "round": spec.round_for(t0 + 2), "anchored": True, "randomness": 12345},
            # 4. anchored=True with malformed/short hex randomness
            t0 + 3: {"type": "sec", "t": t0 + 3, "round": spec.round_for(t0 + 3), "anchored": True, "randomness": "deadbeef"},
            # 5. anchored=True with non-hex 64-char string
            t0 + 4: {"type": "sec", "t": t0 + 4, "round": spec.round_for(t0 + 4), "anchored": True, "randomness": "zz" * 32},
            # 6. anchored=True with float round (float(rnd) == rnd in Python)
            t0 + 5: {"type": "sec", "t": t0 + 5, "round": float(spec.round_for(t0 + 5)), "anchored": True, "randomness": valid_hex},
            # 7. anchored=True with boolean round=True at round 1 (True == 1 in Python)
            t_r1: {"type": "sec", "t": t_r1, "round": True, "anchored": True, "randomness": valid_hex},
            # 8. anchored=True at pre-genesis timestamp (rnd <= 0)
            t_pre: {"type": "sec", "t": t_pre, "round": spec.round_for(t_pre), "anchored": True, "randomness": valid_hex},
            # 9. corrupt non-dict entry in secs
            t0 + 6: "corrupt-entry",
            # 10. valid anchored entry matching beacon
            t0 + 7: {"type": "sec", "t": t0 + 7, "round": spec.round_for(t0 + 7), "anchored": True, "randomness": valid_hex},
        }

        ts = [t0, t0 + 1, t0 + 2, t0 + 3, t0 + 4, t0 + 5, t_r1, t_pre, t0 + 6, t0 + 7]
        res = cli.check_drand(secs, ts)
        expected = "1 ok, 8 MISMATCH"
        if res != expected:
            print(f"FAIL test_check_drand_corrupt_log_entries: expected {expected!r}, got {res!r}")
            sys.exit(1)

        lookup = cli.log_randomness(secs)
        for t in [t0, t0 + 1, t0 + 2, t0 + 3, t0 + 4, t0 + 6]:
            if lookup(t) is not None:
                print(f"FAIL test_check_drand_corrupt_log_entries: expected None for t={t}, got {lookup(t)!r}")
                sys.exit(1)
        if lookup(t0 + 7) != bytes.fromhex(valid_hex):
            print("FAIL test_check_drand_corrupt_log_entries: valid randomness lookup failed")
            sys.exit(1)
    except Exception as e:
        print(f"FAIL test_check_drand_corrupt_log_entries: {type(e).__name__}: {e}")
        sys.exit(1)
    finally:
        drand.fetch_round = orig_fetch

    print("PASS test_check_drand_corrupt_log_entries")


if __name__ == "__main__":
    test_check_drand_corrupt_log_entries()
