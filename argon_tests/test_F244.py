import sys
import numpy as np
from roomstamp import blind, engine
from roomstamp.selftest import T0, load_speech
from roomstamp.spec import FS

key = bytes(range(32))
speech = load_speech()
stamped = engine.render(speech, key, T0, engine.StampParams(mode="file", l1_db=-30, l2_delta_db=4.0))[:len(speech)].astype(np.float64)
src = blind.RandomnessSource(unanchored=True)
W = (T0 - 20, T0 + 60)
i0 = int(12.0 * FS)

# 1. Duplicated 10s stamped half must not falsely claim a forward cut
half = stamped[i0:i0 + 10 * FS]
dup_clip = np.concatenate([half, half])
r_dup = blind.detect(dup_clip, key, src, *W, n_null=50, tempos=(1.0,), seed=1)
if "cut" in r_dup:
    print(f"FAIL duplicated_stamped_half_no_false_cut: falsely claimed cut {r_dup['cut']}")
    sys.exit(1)

# 2. Real 0.3s forward cut must still be detected
cut_src = np.concatenate([stamped[:i0 + 10 * FS], stamped[i0 + int(10.3 * FS):]])
cut_clip = cut_src[i0:i0 + 20 * FS]
r_cut = blind.detect(cut_clip, key, src, *W, n_null=50, tempos=(1.0,), seed=1)
if "cut" not in r_cut or abs(r_cut["cut"]["removed_s"] - 0.3) > 0.05:
    print(f"FAIL real_forward_cut_detected: expected ~0.3s cut, got {r_cut.get('cut')}")
    sys.exit(1)

print("PASS duplicated_stamped_half_no_false_cut")
