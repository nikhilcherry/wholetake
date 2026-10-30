"""Reproducer for F247: Unmatched feed session (L1='absent', env_z < ENV_Z_MIN) overwrites closer no-feed candidate session in run()."""
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


def _write_session(d: str, t0: int, key: bytes, secs_count: int = 15, feed_audio: np.ndarray | None = None) -> None:
    p = engine.StampParams(mode="pa")
    rand = lambda t: hashlib.sha256(b"fake-drand|%d" % spec.round_for(t)).digest()
    rec = lambda t: {
        "t": t,
        "round": spec.round_for(t),
        "anchored": True,
        "randomness": rand(t).hex(),
    }
    feed_name = f"feed_{t0}.wav"
    if feed_audio is not None:
        wavio.write(os.path.join(d, feed_name), feed_audio)
    with open(os.path.join(d, f"session_{t0}.jsonl"), "w", encoding="utf-8") as f:
        f.write(
            json.dumps(
                {
                    "type": "session",
                    "spec": spec.SPEC_VERSION,
                    "key_id": spec.key_id(key),
                    "t_start": t0,
                    "fs": FS,
                    "block": engine.BLOCK,
                    "params": p.as_log(),
                    "round_lag": spec.ROUND_LAG,
                    "drand_chain": spec.DRAND_CHAIN,
                    "feed_wav": feed_name,
                    "feed_source": "wav",
                    "warmup": rec(t0 - 1),
                }
            )
            + "\n"
        )
        for i in range(secs_count):
            t = t0 + i
            s_seed = spec.second_seed(key, t, rand(t))
            f.write(
                json.dumps(
                    {
                        "type": "sec",
                        **rec(t),
                        "anchor": spec.anchor_hash(s_seed),
                        "dac_utc": t + 0.01,
                    }
                )
                + "\n"
            )


def test_unmatched_feed_session_hijacks_nofeed_candidate() -> None:
    name = "test_unmatched_feed_session_hijacks_nofeed_candidate"
    root = tempfile.mkdtemp(prefix="f247_")
    try:
        venue_dir = os.path.join(root, "venue")
        os.makedirs(venue_dir, exist_ok=True)
        key = secrets.token_bytes(32)
        key_file = os.path.join(root, "venue.key")
        with open(key_file, "w", encoding="utf-8") as f:
            f.write(key.hex())

        t_old = 1793430600
        t_target = 1793434200

        # Clip is 10 s of real speech from testdata
        speech = selftest.load_speech(None, 25.0)
        clip_x = speech[: 10 * FS]
        clip_path = os.path.join(root, "clip.wav")
        wavio.write(clip_path, clip_x)

        # Older session has an unrelated feed (different slice of speech/noise so env_z < ENV_Z_MIN and L1='absent')
        rng = np.random.default_rng(42)
        unrelated_feed = 0.05 * rng.standard_normal(12 * FS)
        _write_session(venue_dir, t_old, key, secs_count=12, feed_audio=unrelated_feed)

        # Closer/newer target session has no clean feed (L2-only session)
        _write_session(venue_dir, t_target, key, secs_count=15, feed_audio=None)

        out_json = os.path.join(root, "detection.json")
        doc = wt_detect.run(
            clip_path,
            out_path=out_json,
            venue_dir=venue_dir,
            key_file=key_file,
            around=t_target + 2,
            fast=True,
        )

        chosen = doc["verdict"]["venue"]["session"]
        sm_chosen = doc.get("session_match", {}).get("session")
        if chosen != f"session_{t_target}" or sm_chosen != f"session_{t_target}":
            print(
                f"FAIL {name}: Session with unrelated feed (L1={doc['layers']['l1']['status']!r}) "
                f"hijacked `sl` from closer no-feed session: chose {chosen!r} instead of 'session_{t_target}'"
            )
            sys.exit(1)
        print(f"PASS {name}")
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    test_unmatched_feed_session_hijacks_nofeed_candidate()
