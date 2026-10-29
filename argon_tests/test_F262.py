import json
import math
import os
import shutil
import sys
import tempfile
import numpy as np

from roomstamp import cli, detect, engine, spec, wavio
from roomstamp.spec import FS


def test_stamper_init_nan_gain_or_ref_rms():
    key = bytes(range(32))
    t0 = 1793209200
    rng = np.random.default_rng(42)
    x = (rng.standard_normal(FS) * 0.1).astype(np.float64)
    params = engine.StampParams(mode="file")

    bad_vals = [
        float("nan"),
        float("inf"),
        float("-inf"),
        0.0,
        -1.0,
        "not-a-number",
        True,
        False,
    ]

    for r_val in bad_vals:
        eng = engine.Stamper(params, ref_rms=r_val, gain=1.0)
        assert math.isfinite(eng.ref_rms) and eng.ref_rms >= 1e-20, f"invalid ref_rms state {eng.ref_rms} for {r_val!r}"
        out, stamp, _ = engine.render(x, key, t0, params, return_parts=True, ref_rms=r_val, gain=1.0)
        assert np.all(np.isfinite(out)) and np.all(np.isfinite(stamp)), (
            f"non-finite render output for ref_rms={r_val}, gain=1.0"
        )
        assert float(np.sum(stamp * stamp)) > 0.0, f"zero stamp output for ref_rms={r_val}"

    for g_val in bad_vals:
        eng = engine.Stamper(params, ref_rms=0.05, gain=g_val)
        assert math.isfinite(eng.gain) and eng.gain > 0.0, f"invalid gain state {eng.gain} for {g_val!r}"
        out, stamp, _ = engine.render(x, key, t0, params, return_parts=True, ref_rms=0.05, gain=g_val)
        assert np.all(np.isfinite(out)) and np.all(np.isfinite(stamp)), (
            f"non-finite render output for ref_rms=0.05, gain={g_val}"
        )
        assert float(np.sum(out * out)) > 0.0, f"zero output for gain={g_val}"

    # Also test poisoned JSONL log via detect / cmd_detect
    tmp = tempfile.mkdtemp(prefix="test_f262_")
    try:
        feed_2s = (rng.standard_normal(2 * FS) * 0.1).astype(np.float64)
        out_2s = engine.render(feed_2s, key, t0, params)
        rec_path = os.path.join(tmp, "rec.wav")
        feed_path = os.path.join(tmp, "feed.wav")
        log_path = os.path.join(tmp, "session.jsonl")
        key_path = os.path.join(tmp, "venue.key")
        wavio.write(rec_path, out_2s[FS:])
        wavio.write(feed_path, feed_2s)
        with open(key_path, "w", encoding="utf-8") as f:
            f.write(key.hex() + "\n")

        header = {
            "type": "session",
            "spec": spec.SPEC_VERSION,
            "key_id": spec.key_id(key),
            "t_start": t0,
            "fs": FS,
            "block": engine.BLOCK,
            "params": params.as_log(),
            "round_lag": spec.ROUND_LAG,
            "drand_chain": spec.DRAND_CHAIN,
            "feed_wav": "feed.wav",
            "warmup": {"t": t0 - 1, "round": spec.round_for(t0 - 1), "anchored": False, "randomness": None},
        }
        with open(log_path, "w", encoding="utf-8") as f:
            f.write(json.dumps(header) + "\n")
            f.write(json.dumps({"type": "sec", "t": t0, "round": spec.round_for(t0), "anchored": False, "randomness": None, "ref_rms": 0.1, "gain": 1.0}) + "\n")
            # Second t0 + 1 has NaN and -Infinity in JSONL
            f.write(f'{{"type": "sec", "t": {t0 + 1}, "round": {spec.round_for(t0 + 1)}, "anchored": false, "randomness": null, "ref_rms": NaN, "gain": -Infinity}}\n')

        res = detect.detect(out_2s[FS:], feed_2s[FS:], key, t0 + 1, params, ref_rms=float("nan"), gain=float("-inf"))
        assert math.isfinite(res["speech_removed_db"]), f"non-finite speech_removed_db: {res}"
        assert math.isfinite(res["l1"]["clip_z"]), f"non-finite clip_z: {res}"

        cli.main(["detect", rec_path, "--log", log_path, "--key-file", key_path, "--start", "1", "--dur", "1", "--json"])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    try:
        test_stamper_init_nan_gain_or_ref_rms()
        print("PASS stamper_init_nan_gain_or_ref_rms")
    except Exception as e:
        print(f"FAIL stamper_init_nan_gain_or_ref_rms: {e}")
        sys.exit(1)
