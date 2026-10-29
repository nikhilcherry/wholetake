from __future__ import annotations

import sys
import numpy as np

from roomstamp import engine, l1verify, selftest, spec
from roomstamp.session import Session
from roomstamp.spec import FS


def main() -> int:
    name = "frankenstein_genuine_plus_unstamped_splice"
    rng = np.random.default_rng(134)
    x = selftest.load_speech()[:25 * FS].astype(np.float64)
    x = x + 10 ** (-80 / 20) * rng.standard_normal(len(x))
    key = bytes.fromhex("11" * 32)
    p = engine.StampParams(mode="pa")
    out = engine.render(x, key, selftest.T0, p).astype(np.float64)
    sess = Session(key_id=spec.key_id(key), t_start=selftest.T0, params=p, feed="feed.wav", venue="A5 Test Arena")

    # Piece 1: 0..10s genuine stamped speaker output; 5s cut; Piece 2: 15..25s unstamped clean feed
    clip = np.concatenate([out[:10 * FS], x[15 * FS:25 * FS]])
    r = l1verify.verify(clip, x, sess, key, log=lambda *_: None)

    pieces = r.get("pieces", [])
    if len(pieces) != 2:
        print(f"FAIL {name}: expected 2 pieces, got {len(pieces)}")
        return 1
    if pieces[0].get("l1_status") != "present":
        print(f"FAIL {name}: expected piece 1 l1_status='present', got {pieces[0].get('l1_status')!r}")
        return 1
    if pieces[1].get("l1_status") != "absent":
        print(f"FAIL {name}: expected piece 2 l1_status='absent', got {pieces[1].get('l1_status')!r}")
        return 1

    st = r.get("stamp")
    verdict = r.get("verdict", "")
    if st == "present" or verdict.startswith("Recorded at ") or "missing" not in verdict.lower():
        print(
            f"FAIL {name}: Top-level stamp={st} and verdict={verdict!r} certifies the whole spliced clip "
            f"as Recorded at venue even though Piece 2 (10s) has NO stamp (l1_status={pieces[1].get('l1_status')})!"
        )
        return 1

    print(f"PASS {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
