import json
import os
import secrets
import shutil
import sys
import tempfile
import numpy as np

from roomstamp import engine, spec, wavio, wt_detect
from roomstamp.spec import FS

def test_wall_offset_uses_winning_source():
    name = "wall_offset_uses_winning_source"
    tmp = tempfile.mkdtemp(prefix="wt_f117_")
    try:
        t0 = 1793430600
        key = secrets.token_bytes(32)
        key_file = os.path.join(tmp, "venue.key")
        with open(key_file, "w", encoding="utf-8") as f:
            f.write(key.hex())

        venue_dir = os.path.join(tmp, "venue")
        os.makedirs(venue_dir, exist_ok=True)
        p = engine.StampParams(mode="pa")
        with open(os.path.join(venue_dir, f"session_{t0}.jsonl"), "w", encoding="utf-8") as f:
            f.write(json.dumps({
                "type": "session",
                "spec": spec.SPEC_VERSION,
                "key_id": spec.key_id(key),
                "t_start": t0,
                "fs": FS,
                "block": engine.BLOCK,
                "params": p.as_log(),
                "round_lag": spec.ROUND_LAG,
                "drand_chain": spec.DRAND_CHAIN,
            }) + "\n")
            for i in range(10):
                t = t0 + i
                f.write(json.dumps({
                    "type": "sec",
                    "t": t,
                    "round": spec.round_for(t),
                    "anchored": False,
                    "randomness": None,
                    "anchor": "00" * 32,
                    "dac_utc": t + 0.25,
                }) + "\n")

        clip_path = os.path.join(tmp, "clip.wav")
        rng = np.random.default_rng(42)
        wavio.write(clip_path, (rng.standard_normal(3 * FS) * 0.1).astype(np.float64))

        orig_run_l2 = wt_detect.run_l2
        orig_run_wt = wt_detect.run_wt
        try:
            wt_detect.run_l2 = lambda *a, **kw: wt_detect._stage(
                "absent", "s3", "no stamp", recorded_at_unix=float(t0 + 5000),
                venue_t_start=float(t0 + 5000), venue_t_end=float(t0 + 5003)
            )
            wt_detect.run_wt = lambda *a, **kw: wt_detect._stage(
                "present", "s4", "matched witness", recorded_at_unix=float(t0 + 2),
                venue_t_start=float(t0 + 2), venue_t_end=float(t0 + 5),
                content_match=True, integrity_checked=True
            )
            doc = wt_detect.run(clip_path, venue_dir=venue_dir, key_file=key_file, witness="dummy")
        finally:
            wt_detect.run_l2 = orig_run_l2
            wt_detect.run_wt = orig_run_wt

        got = doc["verdict"]["recorded_at_unix"]
        want = float(t0 + 2) + 0.25
        if got != want:
            print(f"FAIL {name}: wall={got} != {want}")
            sys.exit(1)
        print(f"PASS {name}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

if __name__ == "__main__":
    test_wall_offset_uses_winning_source()
