import hashlib
import sys
from roomstamp.drand import DrandCache, _valid_beacon, fetch_round


def main() -> int:
    failures = []
    sig_bytes = b"\xab" * 48
    rand_bytes = hashlib.sha256(sig_bytes).digest()
    valid = {
        "round": 1,
        "randomness": rand_bytes.hex(),
        "signature": sig_bytes.hex(),
    }

    if not _valid_beacon(valid, 1) or not _valid_beacon(valid, "latest"):
        failures.append("_valid_beacon rejected valid round=1 beacon")

    try:
        if _valid_beacon(valid, "invalid_round_str"):
            failures.append("_valid_beacon accepted rnd='invalid_round_str'")
    except Exception as e:
        failures.append(f"_valid_beacon raised {type(e).__name__} on rnd='invalid_round_str': {e}")

    try:
        if _valid_beacon(valid, True):
            failures.append("_valid_beacon accepted boolean rnd=True as round 1")
    except Exception as e:
        failures.append(f"_valid_beacon raised {type(e).__name__} on rnd=True: {e}")

    try:
        if _valid_beacon(valid, None):
            failures.append("_valid_beacon accepted rnd=None")
    except Exception as e:
        failures.append(f"_valid_beacon raised {type(e).__name__} on rnd=None: {e}")

    padded = dict(valid, randomness="  " + valid["randomness"] + "\n")
    if _valid_beacon(padded, 1):
        failures.append("_valid_beacon accepted whitespace-padded randomness string with len != 64")

    inner_space = dict(valid, randomness=" " + valid["randomness"][1:])
    if _valid_beacon(inner_space, 1):
        failures.append("_valid_beacon accepted randomness string with whitespace")

    if fetch_round(True) is not None or fetch_round(None) is not None or fetch_round("invalid_round_str") is not None:
        failures.append("fetch_round did not reject invalid rnd before network call")

    for bad_keep in (0, -1, False, "64"):
        try:
            c = DrandCache(keep=bad_keep)
        except ValueError:
            pass
        else:
            for r in range(1, 25):
                c._store({"round": r, "randomness": valid["randomness"], "signature": valid["signature"]})
            if len(c._rounds) == 24:
                failures.append(f"DrandCache(keep={bad_keep!r}) failed to evict anything (sorted(...)[:-0] is []), leaking all {len(c._rounds)} rounds")
            else:
                failures.append(f"DrandCache(keep={bad_keep!r}) did not raise ValueError")

    c5 = DrandCache(keep=5)
    for r in range(1, 25):
        c5._store({"round": r, "randomness": valid["randomness"], "signature": valid["signature"]})
    if sorted(c5._rounds) != [20, 21, 22, 23, 24]:
        failures.append(f"DrandCache(keep=5) kept {sorted(c5._rounds)}, expected [20, 21, 22, 23, 24]")

    if failures:
        print("FAIL test_F361: " + "; ".join(failures))
        return 1
    print("PASS test_F361")
    return 0


if __name__ == "__main__":
    sys.exit(main())
