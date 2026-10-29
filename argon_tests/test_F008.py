import json
import sys
import numpy as np
from roomstamp import blind, engine
from roomstamp.selftest import T0, load_speech
from roomstamp.spec import FS

key = b"\x11" * 32
speech = load_speech()[:12 * FS]
stamped = engine.render(speech, key, T0, engine.StampParams(mode="file", l1_db=-30, l2_delta_db=4.0)).astype(np.float64)
stamped[1000] = np.nan
stamped[2000] = np.inf
stamped[3000] = -np.inf

src = blind.RandomnessSource(unanchored=True)
r = blind.detect(stamped, key, src, T0 - 15, T0 + 30, n_null=50, tempos=(1.0,), taus=(0.0,), seed=1)

try:
    json.dumps(r, allow_nan=False)
except ValueError as e:
    print(f"FAIL F008: non-finite values in detect() output: {e}")
    sys.exit(1)

if not r.get("present"):
    print(f"FAIL F008: expected present verdict on stamped clip with NaN/Inf sample, got {r}")
    sys.exit(1)

print("PASS F008")
