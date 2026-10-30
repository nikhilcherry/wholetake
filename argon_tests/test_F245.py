"""Reproducer test for F245: fuse() and run() must not let absent L1 (content_match=True)
override present/likely L2 for recorded_at_source, recorded_at_unix, or _wall_offset.
"""
from __future__ import annotations

import json
import os
import secrets
import shutil
import sys
import tempfile

import numpy as np

from roomstamp import engine, spec, wavio, wt_detect
from roomstamp.spec import FS


def test_fuse_absent_l1_vs_present_l2() -> None:
    doc = wt_detect._empty_doc(["wt-detect", "clip.wav"])
    doc["clip"] = {
        "path": "clip.wav",
        "sha256": "00" * 32,
        "bytes": 1000,
        "duration_s": 20.0,
        "orig_rate": 48000,
        "orig_channels": 1,
        "codec": "wav",
        "decoder": "soundfile",
    }
    doc["verdict"]["venue"] = {
        "name": "HH Goa demo stage",
        "key_id": "0123456789abcdef",
        "session": "session_1793430600",
        "mode": "pa",
    }
    doc["layers"]["l1"] = wt_detect._stage(
        "absent",
        "s2",
        "stamp absent",
        recorded_at_unix=1793430610.5,
        venue_t_start=1793430610.5,
        venue_t_end=1793430630.5,
        content_match=True,
        integrity_checked=True,
    )
    doc["layers"]["l2"] = wt_detect._stage(
        "present",
        "s3",
        "stamp present",
        recorded_at_unix=1793430610.0,
        venue_t_start=1793430610.0,
        venue_t_end=1793430630.8,
        tempo=1.04,
        z=6.2,
        p_value=1e-4,
    )
    wt_detect.fuse(doc)
    v = doc["verdict"]
    if v["recorded_at_source"] != "l2":
        raise AssertionError(
            f"absent L1 overrode present L2: recorded_at_source={v['recorded_at_source']!r} (expected 'l2')"
        )
    if v["recorded_at_precision_s"] != 0.02:
        raise AssertionError(
            f"recorded_at_precision_s={v['recorded_at_precision_s']!r} (expected 0.02)"
        )
    if v["recorded_at_unix"] != 1793430610.0:
        raise AssertionError(
            f"recorded_at_unix={v['recorded_at_unix']!r} (expected 1793430610.0)"
        )
    if v["tempo_ratio"] != 1.04:
        raise AssertionError(
            f"tempo_ratio={v['tempo_ratio']!r} (expected 1.04)"
        )


def test_run_wall_offset_uses_present_l2_over_absent_l1() -> None:
    tmp = tempfile.mkdtemp(prefix="test_f245_")
    try:
        key = secrets.token_bytes(32)
        key_file = os.path.join(tmp, "venue.key")
        with open(key_file, "w", encoding="utf-8") as f:
            f.write(key.hex())

        t0 = 1793430600
        dur_s = 12
        rng = np.random.default_rng(42)
        # Synthetic speech-like envelope modulated signal (unstamped, so L1 is absent but content_match=True)
        t = np.arange(dur_s * FS) / FS
        mod = 0.5 + 0.5 * np.sin(2 * np.pi * 3.0 * t) * np.cos(2 * np.pi * 0.7 * t)
        carrier = rng.standard_normal(len(t)) * 0.1
        feed = (carrier * mod).astype(np.float64)

        venue_dir = os.path.join(tmp, "logs")
        os.makedirs(venue_dir, exist_ok=True)
        feed_path = os.path.join(venue_dir, f"feed_{t0}.wav")
        wavio.write(feed_path, feed)

        p = engine.StampParams(mode="pa")
        sess_path = os.path.join(venue_dir, f"session_{t0}.jsonl")
        with open(sess_path, "w", encoding="utf-8") as f:
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
                "feed_wav": f"feed_{t0}.wav",
                "feed_source": "wav",
                "warmup": {"t": t0 - 1, "round": spec.round_for(t0 - 1), "anchored": False, "randomness": None},
            }) + "\n")
            for sec_i in range(dur_s):
                t_sec = t0 + sec_i
                # Give t0 + 0 a distinct DAC offset (+0.25s) vs t0 + 2 (+0.75s)
                dac_off = 0.25 if sec_i < 2 else 0.75
                f.write(json.dumps({
                    "type": "sec",
                    "t": t_sec,
                    "round": spec.round_for(t_sec),
                    "anchored": True,
                    "randomness": "11" * 32,
                    "anchor": "22" * 32,
                    "dac_utc": t_sec + dac_off,
                }) + "\n")

        clip_path = os.path.join(tmp, "clip.wav")
        wavio.write(clip_path, feed)
        clip_info = wt_detect.ingest(clip_path)

        out_json = os.path.join(tmp, "detection.json")
        prev_doc = wt_detect._empty_doc(["wt-detect", clip_path])
        prev_doc["clip"] = {
            "path": clip_info.path,
            "sha256": clip_info.sha256,
            "bytes": clip_info.n_bytes,
            "duration_s": round(clip_info.duration_s, 3),
            "orig_rate": clip_info.orig_rate,
            "orig_channels": clip_info.orig_channels,
            "codec": clip_info.codec,
            "decoder": clip_info.decoder,
        }
        prev_doc["verdict"]["venue"] = {
            "name": "HH Goa demo stage",
            "key_id": spec.key_id(key),
            "session": f"session_{t0}",
            "mode": "pa",
        }
        # L1 at t0 + 0.0 (absent, content_match=True), L2 at t0 + 3.0 (present)
        prev_doc["layers"]["l1"] = wt_detect._stage(
            "absent",
            "s2",
            "stamp absent",
            recorded_at_unix=float(t0),
            venue_t_start=float(t0),
            venue_t_end=float(t0 + 10),
            content_match=True,
            integrity_checked=True,
        )
        prev_doc["layers"]["l2"] = wt_detect._stage(
            "present",
            "s3",
            "stamp present",
            recorded_at_unix=float(t0 + 3),
            venue_t_start=float(t0 + 3),
            venue_t_end=float(t0 + 11),
            tempo=1.04,
            z=6.5,
            p_value=1e-4,
        )
        with open(out_json, "w", encoding="utf-8") as f:
            json.dump(prev_doc, f)

        res = wt_detect.run(
            clip_path,
            out_path=out_json,
            venue_dir=venue_dir,
            key_file=key_file,
            layers="wt",
            merge=True,
        )
        v = res["verdict"]
        expected_wall = float(t0 + 3) + 0.75
        if v["recorded_at_source"] != "l2":
            raise AssertionError(f"run() recorded_at_source={v['recorded_at_source']!r}, expected 'l2'")
        if abs((v["recorded_at_unix"] or 0.0) - expected_wall) > 1e-3:
            raise AssertionError(
                f"run() recorded_at_unix={v['recorded_at_unix']!r}, expected {expected_wall!r} (with L2 wall offset)"
            )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> None:
    try:
        test_fuse_absent_l1_vs_present_l2()
        test_run_wall_offset_uses_present_l2_over_absent_l1()
    except Exception as e:
        print(f"FAIL test_F245: {e}")
        sys.exit(1)
    print("PASS test_F245")
    sys.exit(0)


if __name__ == "__main__":
    main()
