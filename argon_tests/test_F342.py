"""Reproducer for F342: Session validation allows t_start <= 0 and non-32-byte randomness hex,
crashing mid-verification in KeystreamSequencer or second_seed."""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile

import numpy as np

from roomstamp import engine, l1verify, selftest, spec
from roomstamp.session import Session
from roomstamp.spec import FS


def run_test() -> tuple[bool, str]:
    tmp = tempfile.mkdtemp(prefix="test_f342_")
    try:
        key = b"\x42" * 32
        kid = spec.key_id(key)
        params = engine.StampParams(mode="pa")
        speech = selftest.load_speech()[:4 * FS].astype(np.float64)
        t0 = 1730000000

        # 1. Session.load and Session construction must reject t_start <= 0, bool, and >= 2^64
        for bad_t in (0, -1, -100, True, False, 1 << 64, 1.5):
            p = os.path.join(tmp, f"sess_t_{bad_t}.json")
            with open(p, "w", encoding="utf-8") as f:
                json.dump({
                    "spec_version": spec.SPEC_VERSION,
                    "venue": "Test",
                    "key_id": kid,
                    "t_start": bad_t,
                    "fs": FS,
                    "params": params.as_log(),
                    "feed": "feed.wav",
                    "seconds": {},
                }, f)
            try:
                sess = Session.load(p)
            except ValueError:
                pass
            else:
                try:
                    l1verify.verify(speech, speech, sess, key, log=lambda *_: None)
                except Exception as e:
                    return False, f"Session.load allowed t_start={bad_t!r} and verify crashed in KeystreamSequencer: {e}"
                return False, f"Session.load allowed invalid t_start={bad_t!r}"

            try:
                Session(key_id=kid, t_start=bad_t, params=params, feed="feed.wav")
            except ValueError:
                pass
            else:
                return False, f"Session.__init__ allowed invalid t_start={bad_t!r}"

        # 2. Session.load, Session.from_log, and Session construction must reject non-32-byte / malformed randomness
        for idx, bad_r in enumerate(("not_hex_at_all", "deadbeef", "", "00" * 31, "00" * 33, "zz" * 32, 12345)):
            p_json = os.path.join(tmp, f"sess_r_{idx}.json")
            with open(p_json, "w", encoding="utf-8") as f:
                json.dump({
                    "spec_version": spec.SPEC_VERSION,
                    "venue": "Test",
                    "key_id": kid,
                    "t_start": t0,
                    "fs": FS,
                    "params": params.as_log(),
                    "feed": "feed.wav",
                    "seconds": {str(t0): bad_r},
                }, f)
            try:
                sess = Session.load(p_json)
            except ValueError:
                pass
            else:
                try:
                    l1verify.verify(speech, speech, sess, key, log=lambda *_: None)
                except Exception as e:
                    return False, f"Session.load allowed bad randomness {bad_r!r} and verify crashed: {e}"
                return False, f"Session.load allowed bad randomness {bad_r!r}"

            p_jsonl = os.path.join(tmp, f"sess_r_{idx}.jsonl")
            with open(p_jsonl, "w", encoding="utf-8") as f:
                f.write(json.dumps({
                    "type": "session",
                    "spec": spec.SPEC_VERSION,
                    "key_id": kid,
                    "t_start": t0,
                    "fs": FS,
                    "params": params.as_log(),
                    "feed_wav": "feed.wav",
                }) + "\n")
                f.write(json.dumps({
                    "type": "sec",
                    "t": t0,
                    "round": spec.round_for(t0),
                    "anchored": True,
                    "randomness": bad_r,
                }) + "\n")
            try:
                Session.from_log(p_jsonl)
            except ValueError:
                pass
            else:
                return False, f"Session.from_log allowed bad randomness {bad_r!r}"

        # 3. Post-init mutated secs in Session.lookup() must safely return None instead of crashing
        valid_hex = "ab" * 32
        sess_ok = Session(key_id=kid, t_start=t0, params=params, feed="feed.wav",
                          secs={t0: {"t": t0, "anchored": True, "randomness": valid_hex},
                                t0 + 1: {"t": t0 + 1, "anchored": False, "randomness": None}})
        if sess_ok.lookup()(t0) != bytes.fromhex(valid_hex):
            return False, "Session.lookup() failed to decode valid 32-byte randomness hex"
        if sess_ok.lookup()(t0 + 1) is not None:
            return False, "Session.lookup() did not return None for unanchored second"

        sess_ok.secs[t0] = {"t": t0, "anchored": True, "randomness": "deadbeef"}
        sess_ok.secs[t0 + 1] = {"t": t0 + 1, "anchored": True, "randomness": "zzzz"}
        if sess_ok.lookup()(t0) is not None or sess_ok.lookup()(t0 + 1) is not None:
            return False, "Session.lookup() did not return None for post-init corrupted randomness"

        return True, "ok"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    ok, msg = run_test()
    if ok:
        print("PASS session_invalid_t_start_and_randomness")
        sys.exit(0)
    else:
        print(f"FAIL session_invalid_t_start_and_randomness: {msg}")
        sys.exit(1)
