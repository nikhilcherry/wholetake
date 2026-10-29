import sys
from roomstamp import cli, spec

def test_untrusted_randomness():
    key = b"\x11" * 32
    t0 = 1793209200

    try:
        spec.second_seed(key, t0, b"\x00" * 16)
        print("FAIL test_untrusted_randomness: second_seed accepted 16-byte randomness")
        sys.exit(1)
    except AssertionError:
        print("FAIL test_untrusted_randomness: second_seed raised raw AssertionError instead of ValueError")
        sys.exit(1)
    except ValueError:
        pass

    secs = {
        t0: {"t": t0, "round": spec.round_for(t0), "anchored": True, "randomness": "abcd"},
        t0 + 1: {"t": t0 + 1, "round": spec.round_for(t0 + 1), "anchored": True, "randomness": "not-valid-hex!"},
        t0 + 2: {"t": t0 + 2, "round": spec.round_for(t0 + 2), "anchored": True, "randomness": "aa" * 32},
    }
    lookup = cli.log_randomness(secs)
    try:
        r0 = lookup(t0)
        r1 = lookup(t0 + 1)
        r2 = lookup(t0 + 2)
        spec.second_seed(key, t0, r0)
        spec.second_seed(key, t0 + 1, r1)
        spec.second_seed(key, t0 + 2, r2)
    except Exception as e:
        print(f"FAIL test_untrusted_randomness: unexpected {type(e).__name__}: {e}")
        sys.exit(1)

    if r0 is not None or r1 is not None or r2 != bytes.fromhex("aa" * 32):
        print(f"FAIL test_untrusted_randomness: unexpected lookup results {r0!r}, {r1!r}, {r2!r}")
        sys.exit(1)

    chk = cli.check_drand(secs, [t0, t0 + 1])
    if "2 MISMATCH" not in chk:
        print(f"FAIL test_untrusted_randomness: expected 2 MISMATCH in check_drand, got {chk!r}")
        sys.exit(1)

    print("PASS test_untrusted_randomness")

if __name__ == "__main__":
    test_untrusted_randomness()
