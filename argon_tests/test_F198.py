import json
import os
import shutil
import sys
import tempfile

from roomstamp import blind, spec

tmp = tempfile.mkdtemp()
try:
    cache_file = os.path.join(tmp, "drand_cache.json")
    t0 = 1700000000
    r0 = spec.round_for(t0)
    r1 = spec.round_for(t0 + 3)
    valid_hex = "ab" * 32
    with open(cache_file, "w", encoding="utf-8") as f:
        json.dump({str(r0): None, str(r1): valid_hex}, f)
    try:
        src = blind.RandomnessSource(cache_path=cache_file)
    except Exception as e:
        print(f"FAIL drand_cache_with_null_entry: unexpected {type(e).__name__}: {e}")
        sys.exit(1)
    if src.get(t0) is not None:
        print(f"FAIL drand_cache_with_null_entry: expected None for null round, got {src.get(t0)}")
        sys.exit(1)
    if src.get(t0 + 3) != bytes.fromhex(valid_hex):
        print("FAIL drand_cache_with_null_entry: valid round mismatch")
        sys.exit(1)
    print("PASS drand_cache_with_null_entry")
finally:
    shutil.rmtree(tmp, ignore_errors=True)
