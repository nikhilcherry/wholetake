from __future__ import annotations

import os
import sys

os.environ["WHOLETAKE_ASR"] = "none"

import numpy as np
from scipy.signal import resample_poly

from roomstamp.witness import wt_stage
from wholetake import audio, match, record


def main() -> int:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    talk_path = os.path.join(root, "testdata", "witness", "talk.wav")
    rec_dir = os.path.join(root, "testdata", "witness", "record_noasr")

    w = audio.decode(talk_path)
    sr = audio.SR

    # 1. Direct logmel() and features() sanitization
    clip8 = w[int(15.0 * sr):int(23.0 * sr)].copy()
    for bad_name, bad_val in [("NaN", np.nan), ("+Inf", np.inf), ("-Inf", -np.inf)]:
        x_bad = clip8.copy()
        x_bad[len(x_bad) // 2] = bad_val
        mel, energy = audio.logmel(x_bad)
        if not np.isfinite(mel).all() or not np.isfinite(energy).all():
            print(f"FAIL test_F331: audio.logmel() produced non-finite values from 1 {bad_name} sample")
            return 1
        feats = audio.features(x_bad)
        bad_frames = int((~np.isfinite(feats).all(axis=1)).sum())
        if bad_frames > 0:
            print(f"FAIL test_F331: audio.features() produced {bad_frames}/{len(feats)} non-finite frames from 1 {bad_name} sample")
            return 1

    # 2. End-to-end match.match() resilience to a single NaN sample
    rec = record.load(rec_dir)
    clip10 = w[int(15.0 * sr):int(25.0 * sr)].copy()
    clip10_nan = clip10.copy()
    clip10_nan[len(clip10_nan) // 2] = np.nan
    res = match.match(rec, clip10_nan)
    if res["verdict"] != "faithful" or len(res["pieces"]) != 1:
        print(f"FAIL test_F331: match.match() failed on clip with 1 NaN sample: verdict={res['verdict']}, pieces={len(res['pieces'])}")
        return 1

    # 3. End-to-end roomstamp.witness.wt_stage() resilience to a single NaN sample
    clip48k = resample_poly(clip10.astype(np.float64), 3, 1).astype(np.float32)
    clip48k[len(clip48k) // 2] = np.nan
    wt_res = wt_stage(clip48k, rec_dir)
    if wt_res["status"] != "present" or wt_res["verdict"] != "faithful":
        print(f"FAIL test_F331: wt_stage() failed on 48kHz clip with 1 NaN sample: status={wt_res['status']}, verdict={wt_res.get('verdict')}")
        return 1

    print("PASS test_F331")
    return 0


if __name__ == "__main__":
    sys.exit(main())
