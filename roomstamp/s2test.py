"""S2 acceptance tests (no phone needed): S1 renders a session, a simulated hall records it,
a "clipper" cuts the recording, a platform codec squeezes it, and S2 must report the cut size
and the stamp, while derived wrong keys never lock.

    python -m roomstamp.s2test            # full suite
    python -m roomstamp.s2test --quick    # a few cases
"""
from __future__ import annotations

import argparse
import os
import secrets
import tempfile
import time

import numpy as np
from scipy.signal import resample_poly

from . import engine, l1verify, media, selftest, spec, wavio
from .session import Session
from .spec import FS

T0 = selftest.T0
CUT_TOL_S = 0.010


def make_session(mode: str, seed: int = 1):
    """79 s venue session: the test speech, then the same speech time-reversed (speech-like,
    but never a repeat). Returns (key, session, feed, speaker_out)."""
    x = selftest.load_speech()
    x = np.concatenate([x, x[::-1]])
    # a real mic feed never has digital silence: add a -80 dBFS self-noise floor
    x = x + 10 ** (-80 / 20) * np.random.default_rng(seed).standard_normal(len(x))
    key = secrets.token_bytes(32)
    p = engine.StampParams(mode=mode) if mode != "table" else engine.StampParams(mode="table", l1_dbfs=-45.0)
    out = engine.render(x, key, T0, p).astype(np.float64)
    d = tempfile.mkdtemp(prefix="rs_s2_")
    wavio.write(os.path.join(d, "feed.wav"), x)
    sess = Session(key_id=spec.key_id(key), t_start=T0, params=p, feed="feed.wav", venue="HH Goa test stage")
    sess.save(os.path.join(d, "session.json"))
    return key, sess, x, out


def record(out, x, mode, rt60=0.6, snr_db=15.0, seed=3):
    """Hall recording of the whole session (lead-in of 1.3 s before the stamped audio)."""
    return selftest.simulate(out, rt60, snr_db, seed=seed, mouth=x if mode == "table" else None)


def clipper(rec, start_s, dur_s, cuts=(), drift_ppm=0.0):
    """Take [start, start+dur) of the recording, then remove (at_s, len_s) spans (clip-time,
    applied right to left). Optional phone clock drift (resample)."""
    y = rec[int(start_s * FS):int((start_s + dur_s) * FS)]
    for at, ln in sorted(cuts, reverse=True):
        a = int(at * FS)
        y = np.concatenate([y[:a], y[a + int(ln * FS):]])
    if drift_ppm:
        n = 10 ** 6
        y = resample_poly(y, n, int(round(n * (1 + drift_ppm * 1e-6))))
    return y


def check(name, r, want_cuts, want_stamp=True, tol=CUT_TOL_S):
    removed = [e for e in r["edits"] if e["kind"] == "removed"]
    others = [e for e in r["edits"] if e["kind"] != "removed"]
    ok = len(removed) == len(want_cuts) and not others
    detail = []
    for (at, ln), e in zip(sorted(want_cuts), removed):
        ok &= abs(e["removed_s"] - ln) <= tol and abs(e["clip_t"] - at) <= 0.25
        detail.append(f"{e['removed_s']:.3f}s@{e['clip_t']:.2f}")
    st = r["stamp"]
    ok &= (st == "present") if want_stamp else (st != "present")
    if not want_stamp:  # wrong key must never lock: no piece present, no window over z=5 beyond chance
        ok &= all(p.get("l1_status") != "present" for p in r["pieces"])
    zs = ", ".join(f"z{p.get('l1_clip_z', 0):.1f}/{p.get('l1_frac_z5', 0):.0%}/w{p.get('l1_wrong_key_max_z', 0):.1f}"
                   for p in r["pieces"])
    print(f"  {'PASS' if ok else 'FAIL'}  {name:<44} stamp={st:<12} cuts=[{' '.join(detail)}] "
          f"pieces={len(r['pieces'])} [{zs}] {r['runtime_s']:.1f}s")
    if not ok:
        print(f"        verdict: {r['verdict']}")
        for e in r["edits"]:
            print(f"        edit: {e}")
    return ok


def check_render(mode: str) -> bool:
    """The verifier's fast re-render must equal what S1's engine played, mid-session
    (rendered from a later second with S2's warm-up)."""
    key, sess, x, out = make_session(mode)
    _, stamp, _ = engine.render(x, key, T0, sess.params, return_parts=True)
    voice = out - stamp
    f_lo, f_hi = 30 * FS, 50 * FS
    rend = l1verify.render_span(x, sess, key, f_lo, f_hi)
    a, b = f_lo, f_hi
    ra, rb = a - rend.base, b - rend.base
    rel = lambda u, v: float(np.sqrt(np.mean((u - v) ** 2) / (np.mean(v ** 2) + 1e-30)))
    e_s = rel(rend.stamp[ra:rb], stamp[a:b])
    e_v = rel(rend.voice[ra:rb], voice[a:b]) if mode != "table" else rel(rend.voice[ra:rb], x[a:b])
    ok = e_s < 1e-2 and e_v < 1e-4  # stamp: level-tracker warm-up (scale only)
    print(f"  {'PASS' if ok else 'FAIL'}  {mode}: re-render == S1 engine (stamp err {e_s:.1e}, voice err {e_v:.1e})")
    return ok


def check_blips(mode: str) -> list[bool]:
    """(a) a real substitution (1.2 s of other venue audio dropped in) must stay reported;
    (b) a repeated phrase the aligner matched at its other copy must merge back (no false splice)."""
    out_ok = []
    key, sess, x, out = make_session(mode)
    rec = record(out, x, mode)
    y = clipper(rec, 20, 25)
    a, n = int(10 * FS), int(1.2 * FS)
    ys = y.copy()
    ys[a:a + n] = rec[int(45 * FS):int(45 * FS) + n]  # other words, other venue time, same length
    r = l1verify.verify(media.transcode(ys, "aac64"), x, sess, key, log=lambda *_: None)
    rep = [e for e in r["edits"] if e["kind"] == "replaced"]
    ok = (len(r["edits"]) == 1 and len(rep) == 1 and abs(rep[0]["clip_t"] - 10.0) <= 0.25
          and abs(rep[0]["duration_s"] - 1.2) <= 0.25 and abs(rep[0]["source_offset_s"] - 15.0) <= 0.01)
    print(f"  {'PASS' if ok else 'FAIL'}  {mode}: 1.2 s substitution stays reported -> {r['verdict']}")
    out_ok.append(ok)

    # (b) feed that really contains one phrase twice, 5 s apart; force the aligner's mistake
    xs = x.copy()
    p0, p1, ln = int(25 * FS), int(30 * FS), int(1.2 * FS)
    xs[p1:p1 + ln] = x[p0:p0 + ln]
    outs = engine.render(xs, key, T0, sess.params).astype(np.float64)
    recs = record(outs, xs, mode)
    lead = int(1.3 * FS)
    y = clipper(recs, 26, 10)                      # covers the second copy at clip ~[2.7, 3.9] s
    c0 = lead + p1 - int(26 * FS)
    with l1verify.sfft.set_workers(4):
        yb, xb = l1verify._band(y), l1verify._band(xs)
        y8, x8 = l1verify.resample_poly(yb, 1, l1verify.DEC), l1verify.resample_poly(xb, 1, l1verify.DEC)
        groups = l1verify.group_pieces(l1verify.align_windows(y8, x8))
        whole = l1verify.fit_piece(max(groups, key=len), yb, xb)  # the true continuous offset
        Lp = l1verify.Piece(c0=0, c1=c0, a=whole.a, b=whole.b, n_win=5)
        Mp = l1verify.Piece(c0=c0, c1=c0 + ln, a=whole.a - (p1 - p0), n_win=2)   # matched the first copy
        Rp = l1verify.Piece(c0=c0 + ln, c1=len(y), a=whole.a, b=whole.b, n_win=10)
        rend = l1verify.render_span(xs, sess, key, 15 * FS, 40 * FS)
        ch = lambda p: l1verify.Channel(y[p.c0:p.c1], l1verify.warp([rend.voice], p.f(np.arange(p.c0, p.c1)) - rend.base)[0])
        ps = [Lp, Mp, Rp]
        merged, _ = l1verify._absorb_blips(y, rend, ps, [ch(p) for p in ps], ch)
    ok = len(merged) == 1
    print(f"  {'PASS' if ok else 'FAIL'}  {mode}: repeated phrase matched at its other copy is merged ({len(merged)} piece(s))")
    out_ok.append(ok)
    return out_ok


def run(quick: bool = False) -> bool:
    t = time.time()
    results = [check_render(m) for m in ("pa", "table")]
    for m in ("pa", "table"):
        results += check_blips(m)
    for mode in (["pa"] if quick else ["pa", "table"]):
        key, sess, x, out = make_session(mode)
        rec = record(out, x, mode)
        wrong = secrets.token_bytes(32)
        print(f"[s2test] mode={mode}  session {len(x) / FS:.0f}s  hall RT60 0.6 s, SNR 15 dB")
        cases = [
            ("0.30 s cut, wav", dict(start_s=20, dur_s=30, cuts=[(12, 0.30)]), "none", [(12, 0.30)]),
            ("0.30 s cut, AAC-64k", dict(start_s=20, dur_s=30, cuts=[(12, 0.30)]), "aac64", [(12, 0.30)]),
        ]
        if not quick:
            cases += [
                ("no cut, AAC-128k", dict(start_s=20, dur_s=30), "aac128", []),
                ("0.30 s cut, Opus-16k", dict(start_s=20, dur_s=30, cuts=[(12, 0.30)]), "opus16", [(12, 0.30)]),
                ("0.30 s cut, MP3-96k", dict(start_s=20, dur_s=30, cuts=[(12, 0.30)]), "mp3", [(12, 0.30)]),
                ("2 cuts (0.30, 1.20), AAC-64k", dict(start_s=20, dur_s=40, cuts=[(8, 0.30), (22, 1.20)]),
                 "aac64", [(8, 0.30), (22 - 0.30, 1.20)]),
                ("lead-in 1.3 s + cut, AAC-64k", dict(start_s=0, dur_s=25, cuts=[(10, 0.30)]), "aac64", [(10, 0.30)]),
                ("short cut 0.08 s, AAC-64k", dict(start_s=30, dur_s=20, cuts=[(9, 0.08)]), "aac64", [(9, 0.08)]),
                ("long cut 9 s across halves, AAC-64k", dict(start_s=30, dur_s=30, cuts=[(8, 9.0)]), "aac64", [(8, 9.0)]),
                ("long cut 9 s at 12 s (re-acquire), AAC-64k", dict(start_s=30, dur_s=30, cuts=[(12, 9.0)]), "aac64",
                 [(12, 9.0)]),
                ("phone clock +80 ppm, cut, AAC-64k", dict(start_s=20, dur_s=30, cuts=[(12, 0.30)], drift_ppm=80),
                 "aac64", [(12 * (1 - 80e-6), 0.30)]),
                ("AMR-NB 12.2k, cut", dict(start_s=20, dur_s=30, cuts=[(12, 0.30)]), "amr122", [(12, 0.30)]),
            ]
        for name, cl, codec, want in cases:
            y = clipper(rec, **cl)
            if codec != "none":
                y = media.transcode(y, codec)
            r = l1verify.verify(y, x, sess, key, log=lambda *_: None)
            results.append(check(f"{mode}: {name}", r, want))
            if codec == "aac64" and cl.get("cuts") == [(12, 0.30)]:
                rw = l1verify.verify(y, x, sess, wrong, log=lambda *_: None)
                results.append(check(f"{mode}: WRONG KEY, {name}", rw, want, want_stamp=False))
        # unrelated audio: a clip of a different session must not match at all
        if not quick:
            key2, sess2, x2, out2 = make_session(mode)
            y = clipper(record(out, x, mode, seed=9), 5, 20)
            other = engine.render(selftest.load_speech()[::-1][:20 * FS] * 0.7, key2, T0, sess2.params)
            r = l1verify.verify(other.astype(np.float64), x[:10 * FS], sess, key, log=lambda *_: None)
            ok = r["stamp"] != "present"
            print(f"  {'PASS' if ok else 'FAIL'}  {mode}: clip from another venue/feed -> {r['verdict']}")
            results.append(ok)
    n_ok = sum(results)
    print(f"[s2test] {n_ok}/{len(results)} passed in {time.time() - t:.0f}s")
    return n_ok == len(results)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    raise SystemExit(0 if run(ap.parse_args().quick) else 1)
