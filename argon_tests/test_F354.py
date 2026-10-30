"""Reproducer for F354: fuse() and _proof() when sec records omit the optional 'round' field."""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
import sys
import tempfile

from roomstamp import engine, selftest, spec, wavio, wt_detect, wt_test
from roomstamp.spec import FS


def main() -> None:
    name = "t12_anchored_secs_without_round_number"
    tmp = tempfile.mkdtemp(prefix="test_f354_")
    try:
        t0 = 1793430600
        key = secrets.token_bytes(32)
        key_file = os.path.join(tmp, "venue.key")
        with open(key_file, "w", encoding="utf-8") as f:
            f.write(key.hex())

        venue_dir = os.path.join(tmp, "venue")
        os.makedirs(venue_dir, exist_ok=True)
        x = selftest.load_speech(None, 32.0)
        p = engine.StampParams(mode="pa")
        rand = lambda t: hashlib.sha256(b"fake-drand|%d" % spec.round_for(t)).digest()
        anchored = lambda t: (t - t0) % 9 not in (0, 4)
        lookup = lambda t: rand(t) if anchored(t) else None
        out, _, secs = engine.render(x, key, t0, p, lookup, return_parts=True)
        wavio.write(os.path.join(venue_dir, f"feed_{t0}.wav"), x)

        # Write session log where 'sec' records have 'anchored' and 'randomness', but omit 'round'
        rec_no_round = lambda t: {
            "t": t,
            "anchored": anchored(t),
            "randomness": rand(t).hex() if anchored(t) else None,
        }
        log_path = os.path.join(venue_dir, f"session_{t0}.jsonl")
        with open(log_path, "w", encoding="utf-8") as f:
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
                "warmup": rec_no_round(t0 - 1),
            }) + "\n")
            for s in secs:
                f.write(json.dumps({
                    "type": "sec",
                    **rec_no_round(s.t),
                    "anchor": s.anchor,
                    "dac_utc": s.t + 0.012,
                }) + "\n")

        y = wt_test.phone(out.astype(float), x, "pa", 8.0, 22.0)
        clip_path = os.path.join(tmp, "clip.wav")
        wavio.write(clip_path, y)

        out_json = os.path.join(tmp, "detection.json")
        doc = wt_detect.run(clip_path, out_path=out_json, venue_dir=venue_dir, key_file=key_file, fast=True)

        af = (doc.get("proof") or {}).get("anchored_fraction")
        rounds = (doc.get("proof") or {}).get("drand_rounds") or []
        warns = doc.get("warnings") or []
        headline = (doc.get("verdict") or {}).get("headline", "")
        rec_here = (doc.get("verdict") or {}).get("recorded_here")

        if rec_here != "verified":
            print(f"FAIL {name}: expected recorded_here='verified', got {rec_here!r}")
            sys.exit(1)
        if af is None or af < 0.7:
            print(f"FAIL {name}: expected anchored_fraction >= 0.7, got {af!r}")
            sys.exit(1)
        if any("unanchored_session" in w for w in warns) or "(unanchored" in headline:
            print(f"FAIL {name}: anchored_fraction={af}, warnings={warns}, headline={headline!r}")
            sys.exit(1)
        if not rounds:
            print(f"FAIL {name}: expected non-empty drand_rounds, got {rounds!r}")
            sys.exit(1)

        # Direct fuse() test when drand_rounds is empty but anchored_fraction > 0.5
        doc2 = json.loads(json.dumps(doc))
        doc2["warnings_base"] = []
        doc2["proof"]["drand_rounds"] = []
        doc2["proof"]["anchored_fraction"] = 0.7826
        wt_detect.fuse(doc2)
        if any("unanchored_session" in w for w in doc2.get("warnings", [])) or "(unanchored" in doc2["verdict"]["headline"]:
            print(f"FAIL {name}: direct fuse() with af=0.7826 and drand_rounds=[] still flagged unanchored: warnings={doc2.get('warnings')}, headline={doc2['verdict']['headline']!r}")
            sys.exit(1)

        # Ensure genuinely unanchored (af=0.0) still triggers unanchored warning
        doc3 = json.loads(json.dumps(doc))
        doc3["warnings_base"] = []
        doc3["proof"]["drand_rounds"] = []
        doc3["proof"]["anchored_fraction"] = 0.0
        wt_detect.fuse(doc3)
        if not any("0% of matched seconds are drand-anchored" in w for w in doc3.get("warnings", [])) or "(unanchored: time not verified)" not in doc3["verdict"]["headline"]:
            print(f"FAIL {name}: genuinely unanchored (af=0.0) did not emit unanchored warning/headline")
            sys.exit(1)

        print(f"PASS {name}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
