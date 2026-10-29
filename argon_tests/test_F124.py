import hashlib
import json
import os
import shutil
import sys
import tempfile
import numpy as np

from roomstamp import engine, selftest, spec, wavio, wt_detect
from roomstamp.spec import FS

T0 = 1793430600


def _make_unique_speech(seconds: float) -> np.ndarray:
    need = int(round(seconds * FS))
    parts = []
    for rel in (
        ("testdata", "speech.wav"),
        ("testdata", "witness", "talk.wav"),
        ("testdata", "witness", "other.wav"),
        ("testdata", "witness", "insert.wav"),
    ):
        p = os.path.join(selftest.HERE, *rel)
        if os.path.exists(p):
            y = wavio.read(p).astype(np.float64)
            pk = np.max(np.abs(y)) + 1e-9
            parts.append(y / pk * 0.25)
    raw = np.concatenate(parts) if parts else selftest.load_speech(None, seconds)
    if len(raw) < need:
        reps = int(np.ceil(need / len(raw)))
        chunks = []
        rng = np.random.default_rng(124)
        for k in range(reps):
            seg = raw.copy() if k % 2 == 0 else raw[::-1].copy()
            # Apply distinct syllabic amplitude envelope per pass so envelopes never repeat
            t = np.arange(len(seg)) / FS
            f1 = 1.7 + 0.37 * k
            f2 = 3.1 + 0.53 * k
            mod = 0.65 + 0.35 * np.sin(2 * np.pi * f1 * t + rng.uniform(0, 6.28)) * np.cos(2 * np.pi * f2 * t)
            chunks.append(seg * mod)
        raw = np.concatenate(chunks)
    return raw[:need]


def test_large_cut_exceeding_30s_margin() -> None:
    name = "test_F124_large_cut_exceeding_slice_margin"
    root = tempfile.mkdtemp(prefix="wt_f124_")
    try:
        key = hashlib.sha256(b"f124-test-key").digest()
        kf = os.path.join(root, "venue.key")
        with open(kf, "w", encoding="utf-8") as f:
            f.write(key.hex())

        venue_dir = os.path.join(root, "venue")
        os.makedirs(venue_dir, exist_ok=True)

        seconds = 85.0
        x = _make_unique_speech(seconds)
        p = engine.StampParams(mode="pa")
        rand = lambda t: hashlib.sha256(b"fake-drand|%d" % spec.round_for(t)).digest()
        anchored = lambda t: True
        lookup = lambda t: rand(t)
        out, _, secs = engine.render(x, key, T0, p, lookup, return_parts=True)

        wavio.write(os.path.join(venue_dir, f"feed_{T0}.wav"), x)
        rec = lambda t: {
            "t": t,
            "round": spec.round_for(t),
            "anchored": anchored(t),
            "randomness": rand(t).hex(),
        }
        log_path = os.path.join(venue_dir, f"session_{T0}.jsonl")
        with open(log_path, "w", encoding="utf-8") as f:
            f.write(
                json.dumps(
                    {
                        "type": "session",
                        "spec": spec.SPEC_VERSION,
                        "key_id": spec.key_id(key),
                        "t_start": T0,
                        "fs": FS,
                        "block": engine.BLOCK,
                        "params": p.as_log(),
                        "round_lag": spec.ROUND_LAG,
                        "drand_chain": spec.DRAND_CHAIN,
                        "feed_wav": f"feed_{T0}.wav",
                        "feed_source": "wav",
                        "warmup": rec(T0 - 1),
                    }
                )
                + "\n"
            )
            for s in secs:
                f.write(json.dumps({"type": "sec", **rec(s.t), "anchor": s.anchor, "dac_utc": s.t + 0.012}) + "\n")

        # Simulate room recording; crop 66 s starting at 4.0 s (covering 4.0..70.0 s of venue)
        # Then cut 50.0 s at clip_t = 11.0 s -> 11.0 s pre-cut (4..15 s) + 5.0 s post-cut (65..70 s) = 16.0 s clip
        sim = selftest.simulate(out.astype(np.float64), 0.4, 18.0)
        lead = 1.3
        start_s, span_s = 4.0, 66.0
        y = sim[int((lead + start_s) * FS) : int((lead + start_s + span_s) * FS)]
        cut_at_s, cut_len_s = 11.0, 50.0
        a = int(cut_at_s * FS)
        b = int((cut_at_s + cut_len_s) * FS)
        y_cut = np.concatenate([y[:a], y[b:]])
        y_cut = y_cut / (np.max(np.abs(y_cut)) + 1e-9) * 0.7

        clip_path = os.path.join(root, "clip_50s_cut.wav")
        wavio.write(clip_path, y_cut)
        out_json = os.path.join(root, "detection.json")

        doc = wt_detect.run(clip_path, out_path=out_json, venue_dir=venue_dir, key_file=kf, fast=True)
        env_z = (doc.get("session_match") or {}).get("env_z", 0.0)
        if env_z < wt_detect.ENV_Z_MIN:
            print(f"FAIL {name}: expected coarse match env_z >= {wt_detect.ENV_Z_MIN}, got {env_z}")
            sys.exit(1)

        verdict = doc.get("verdict", {})
        edits = verdict.get("edits", [])
        removed = [e for e in edits if e.get("kind") == "removed" and abs((e.get("removed_s") or 0.0) - cut_len_s) < 0.5]
        if verdict.get("recorded_here") != "verified" or verdict.get("integrity") != "edited" or not removed:
            print(
                f"FAIL {name}: 50s cut was missed: recorded_here={verdict.get('recorded_here')!r}, "
                f"integrity={verdict.get('integrity')!r}, edits={edits}, headline={verdict.get('headline')!r}"
            )
            sys.exit(1)

        print(f"PASS {name}")
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    test_large_cut_exceeding_30s_margin()
