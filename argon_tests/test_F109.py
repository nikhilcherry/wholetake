import sys
import numpy as np
from roomstamp import blind, spec

def test_signs_for_conformity():
    key = b"k" * 32
    t_lo, t_hi = 100, 102

    # Valid None and 32-byte randomness must match spec.second_seed + spec.l2_signs
    r32 = bytes(range(32))
    rand = [None, r32, None]
    got = blind.signs_for(key, t_lo, t_hi, rand)
    ref = np.concatenate([
        spec.l2_signs(spec.second_seed(key, t, rand[i]))
        for i, t in enumerate(range(t_lo - 1, t_hi))
    ])
    if not np.array_equal(got, ref):
        print("FAIL signs_for_vs_spec_second_seed_conformity: output mismatch on valid randomness")
        sys.exit(1)

    # Invalid randomness (empty bytes, short bytes, long bytes) must raise like spec.second_seed
    for bad in [b"", b"short", b"x" * 31, b"x" * 33]:
        spec_raised = False
        try:
            spec.second_seed(key, t_lo, bad)
        except (ValueError, AssertionError):
            spec_raised = True
        if not spec_raised:
            print(f"FAIL signs_for_vs_spec_second_seed_conformity: spec.second_seed did not reject {bad!r}")
            sys.exit(1)

        blind_raised = False
        try:
            blind.signs_for(key, t_lo, t_hi, [bad, None, None])
        except (ValueError, AssertionError):
            blind_raised = True
        if not blind_raised:
            print("FAIL signs_for_vs_spec_second_seed_conformity: AssertionError: signs_for silently accepts non-32-byte randomness without validating len(r) == 32, diverging from spec.second_seed which enforces len(r) == 32")
            sys.exit(1)

    print("PASS signs_for_vs_spec_second_seed_conformity")

if __name__ == "__main__":
    test_signs_for_conformity()
