"""Reproducer for F148: wt-detect certifies wall-clock 'Recorded at' verdicts on completely unanchored sessions."""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
import sys
import tempfile

import numpy as np

from roomstamp import engine, selftest, spec, wavio, wt_detect
from roomstamp.spec import FS

T0 = 1793430600


def _write_session(d: str, key: bytes, x: np.ndarray, anchored_fn) -> np.ndarray:
    p = engine.StampParams(mode="pa")
    rand = lambda t: hashlib.sha256(b"fake-drand|%d" % spec.round_for(t)).digest()
    lookup = lambda t: rand(t) if anchored_fn(t) else None
    out, _, secs = engine.render(x, key, T0, p, lookup, return_parts=True)
    os.makedirs(d, exist_ok=True)
    wavio.write(os.path.join(d, f"feed_{T0}.wav"), x)
    rec = lambda t: {
        "t": t,
        "round": spec.round_for(t),
        "anchored": bool(anchored_fn(t)),
        "randomness": rand(t).hex() if anchored_fn(t) else None,
    }
    with open(os.path.join(d, f"session_{T0}.jsonl"), "w", encoding="utf-8") as f:
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
            f.write(
                json.dumps({"type": "sec", **rec(s.t), "anchor": s.anchor, "dac_utc": s.t + 0.012})
                + "\n"
            )
    return out.astype(np.float64)


def main() -> None:
    root = tempfile.mkdtemp(prefix="test_f148_")
    try:
        key = secrets.token_bytes(32)
        kf = os.path.join(root, "venue.key")
        with open(kf, "w", encoding="utf-8") as f:
            f.write(key.hex())

        x = selftest.load_speech(None, 12.0)

        # 1. Completely unanchored session (0% anchored)
        v_unanch = os.path.join(root, "venue_unanchored")
        out_unanch = _write_session(v_unanch, key, x, lambda t: False)
        clip_path = os.path.join(root, "clip_unanch.wav")
        wavio.write(clip_path, out_unanch[1 * FS : 11 * FS])

        doc = wt_detect.run(clip_path, venue_dir=v_unanch, key_file=kf, fast=True)
        if doc["verdict"]["recorded_here"] != "verified":
            print(f"FAIL test_F148: expected verified, got {doc['verdict']['recorded_here']}")
            sys.exit(1)
        if doc["proof"]["drand_rounds"] != []:
            print(f"FAIL test_F148: expected empty drand_rounds, got {doc['proof']['drand_rounds']}")
            sys.exit(1)
        if doc["proof"].get("anchored_fraction") != 0.0:
            print(f"FAIL test_F148: expected anchored_fraction == 0.0, got {doc['proof'].get('anchored_fraction')}")
            sys.exit(1)
        if not any("unanchored_session" in w for w in doc.get("warnings", [])):
            print(f"FAIL test_F148: missing unanchored_session warning in {doc.get('warnings')}")
            sys.exit(1)
        if "unanchored" not in doc["verdict"]["headline"].lower():
            print(f"FAIL test_F148: headline does not qualify unanchored time: {doc['verdict']['headline']!r}")
            sys.exit(1)

        # 2. Partially anchored session (1 out of 12 seconds anchored -> < 50%)
        v_part = os.path.join(root, "venue_partial")
        out_part = _write_session(v_part, key, x, lambda t: t == T0 + 3)
        clip_part = os.path.join(root, "clip_part.wav")
        wavio.write(clip_part, out_part[1 * FS : 11 * FS])
        doc_part = wt_detect.run(clip_part, venue_dir=v_part, key_file=kf, fast=True)
        if not any("unanchored_session" in w for w in doc_part.get("warnings", [])):
            print(f"FAIL test_F148: partial (1/10) anchored session did not emit unanchored_session warning: {doc_part.get('warnings')}")
            sys.exit(1)

        # 3. Sessionless blind-L2 check (sl is None) must not be falsely flagged as unanchored_session
        clip_obj = wt_detect.ingest(clip_path)
        doc_sless = wt_detect._empty_doc(["wt-detect", clip_path])
        doc_sless["warnings_base"] = []
        doc_sless["clip"] = {"duration_s": clip_obj.duration_s}
        doc_sless["verdict"]["venue"] = {"name": "HH Goa demo stage", "key_id": spec.key_id(key), "session": None, "mode": None}
        doc_sless["layers"]["l2"] = wt_detect._stage("present", "s3", "locked", z=6.2, p_value=1e-4, recorded_at_unix=float(T0 + 1))
        wt_detect.fuse(doc_sless)
        doc_sless["proof"] = wt_detect._proof(None, doc_sless, clip_obj)
        wt_detect.fuse(doc_sless)
        if any("unanchored_session" in w for w in doc_sless.get("warnings", [])):
            print(f"FAIL test_F148: sessionless L2 falsely flagged as unanchored_session: {doc_sless.get('warnings')}")
            sys.exit(1)
        if "unanchored" in doc_sless["verdict"]["headline"].lower():
            print(f"FAIL test_F148: sessionless L2 headline falsely marked unanchored: {doc_sless['verdict']['headline']!r}")
            sys.exit(1)

        print("PASS test_F148")
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    main()
