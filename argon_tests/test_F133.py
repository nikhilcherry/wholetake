"""Reproducer for F133: Foreign speech insertions hidden or falsifying reported cut sizes."""
from __future__ import annotations

import os
import secrets
import shutil
import sys
import tempfile

import numpy as np

from roomstamp import engine, l1verify, selftest, spec, wavio
from roomstamp.session import Session
from roomstamp.spec import FS


def _setup_session():
    speech = selftest.load_speech()
    feed = speech[:25 * FS].copy()
    feed += 10 ** (-80 / 20) * np.random.default_rng(1).standard_normal(len(feed))
    # Foreign speech not in the 25s feed: time-reversed later span of speech
    foreign = speech[25 * FS:35 * FS][::-1].copy()
    key = secrets.token_bytes(32)
    t0 = 1700000000
    params = engine.StampParams(mode="pa")
    out = engine.render(feed, key, t0, params).astype(np.float64)
    rec = selftest.simulate(out, 0.4, 18.0, seed=7)
    lead = int(1.3 * FS)
    rec_aligned = rec[lead:lead + len(feed)]
    d = tempfile.mkdtemp(prefix="f133_")
    wavio.write(os.path.join(d, "feed.wav"), feed)
    sess = Session(key_id=spec.key_id(key), t_start=t0, params=params, feed="feed.wav", venue="A5 Test Arena")
    sess.save(os.path.join(d, "session.json"))
    return d, key, sess, feed, rec_aligned, foreign


def test_foreign_insertion_with_zero_offset_jump(key, sess, feed, rec, foreign) -> bool:
    name = "foreign_insertion_with_zero_offset_jump"
    # Overwrite 4s of venue speech [10s, 14s] in-place with 4s of foreign speech (zero net offset jump)
    clip = rec[:22 * FS].copy()
    clip[10 * FS:14 * FS] = foreign[:4 * FS]
    r = l1verify.verify(clip, feed, sess, key, log=lambda *_: None)
    unm = [e for e in r["edits"] if e["kind"] == "unmatched"]
    rem = [e for e in r["edits"] if e["kind"] == "removed"]
    if not unm or "Continuous: nothing removed" in r["verdict"]:
        print(f"FAIL {name}: 4s foreign speech replacement with zero offset shift was silently merged/hidden! "
              f"pieces={len(r['pieces'])}, edits={r['edits']}, verdict={r['verdict']!r}")
        return False
    if abs(unm[0]["duration_s"] - 4.0) > 0.35 or abs(unm[0]["clip_t"] - 10.0) > 0.35:
        print(f"FAIL {name}: unexpected unmatched span {unm[0]}")
        return False
    if not rem or abs(rem[0]["removed_s"] - 4.0) > 0.35:
        print(f"FAIL {name}: expected ~4.0s removed venue speech, got edits={r['edits']}")
        return False
    print(f"PASS {name}")
    return True


def test_foreign_insertion_plus_venue_cut(key, sess, feed, rec, foreign) -> bool:
    name = "foreign_insertion_plus_venue_cut"
    # Remove 5s of venue speech [10s, 15s] and insert 4s of foreign speech in its place
    clip = np.concatenate([rec[:10 * FS], foreign[:4 * FS], rec[15 * FS:24 * FS]])
    r = l1verify.verify(clip, feed, sess, key, log=lambda *_: None)
    unm = [e for e in r["edits"] if e["kind"] == "unmatched"]
    rem = [e for e in r["edits"] if e["kind"] == "removed"]
    if len(unm) != 1:
        print(f"FAIL {name}: Expected 1 unmatched edit for 4s foreign insert, got {r['edits']}")
        return False
    if abs(unm[0]["duration_s"] - 4.0) > 0.35:
        print(f"FAIL {name}: Expected ~4.0s unmatched duration, got {unm[0]}")
        return False
    if len(rem) != 1 or abs(rem[0]["removed_s"] - 5.0) > 0.35:
        print(f"FAIL {name}: Expected ~5.0s removed_s, got {r['edits']}")
        return False
    print(f"PASS {name}")
    return True


def test_2s_foreign_bridge_between_cuts(key, sess, feed, rec, foreign) -> bool:
    name = "2s_foreign_bridge_between_cuts"
    # Remove 5s of venue speech [10s, 15s] and bridge with 2.0s of foreign speech (< GAP_UNMATCHED=2.5s)
    clip = np.concatenate([rec[:10 * FS], foreign[:2 * FS], rec[15 * FS:24 * FS]])
    r = l1verify.verify(clip, feed, sess, key, log=lambda *_: None)
    unm = [e for e in r["edits"] if e["kind"] == "unmatched"]
    rem = [e for e in r["edits"] if e["kind"] == "removed"]
    if len(unm) != 1 or len(rem) != 1 or abs(rem[0]["removed_s"] - 5.0) > 0.35 or abs(unm[0]["duration_s"] - 2.0) > 0.35:
        got_rem = rem[0]["removed_s"] if rem else 0.0
        print(f"FAIL {name}: 2.0s foreign bridge (< GAP_UNMATCHED=2.5s) forced _split_pieces to join pieces "
              f"and report removed_s={got_rem:.2f}s instead of 5.00s (edits={r['edits']})!")
        return False
    print(f"PASS {name}")
    return True


def main() -> int:
    d, key, sess, feed, rec, foreign = _setup_session()
    try:
        ok = True
        ok &= test_foreign_insertion_with_zero_offset_jump(key, sess, feed, rec, foreign)
        ok &= test_foreign_insertion_plus_venue_cut(key, sess, feed, rec, foreign)
        ok &= test_2s_foreign_bridge_between_cuts(key, sess, feed, rec, foreign)
        return 0 if ok else 1
    finally:
        shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
