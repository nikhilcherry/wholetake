"""S5 acceptance tests: wt-detect end to end, no phone needed. Each case builds its own venue
dir (S1 session log + clean feed), records it through a simulated hall, edits/encodes the
clip the way a phone or platform would, runs the real CLI, and checks detection.json.

    python -m roomstamp.wt_test            # full suite
    python -m roomstamp.wt_test --quick    # contract + error paths + one hero case
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import time

import numpy as np
from scipy.signal import resample_poly

from . import engine, media, selftest, spec, wavio
from .spec import FS

T0 = 1793430600            # 31 Oct 2026 10:40:00 UTC = 16:10 IST
TOL_CUT = 0.010


def make_venue(d: str, mode: str, key: bytes, seconds: float = 40.0, feed: bool = True) -> tuple[np.ndarray, np.ndarray]:
    """S1-style session in d: session_<T0>.jsonl (+ feed_<T0>.wav). Returns (feed, speaker_out)."""
    x = selftest.load_speech(None, seconds)
    p = engine.StampParams(mode=mode) if mode != "table" else engine.StampParams(mode="table", l1_dbfs=-45.0)
    # anchored, with drand "arriving late" inside some rounds: per second, not per round
    rand = lambda t: hashlib.sha256(b"fake-drand|%d" % spec.round_for(t)).digest()
    anchored = lambda t: (t - T0) % 9 not in (0, 4)
    lookup = lambda t: rand(t) if anchored(t) else None
    out, _, secs = engine.render(x, key, T0, p, lookup, return_parts=True)
    os.makedirs(d, exist_ok=True)
    if feed:
        wavio.write(os.path.join(d, f"feed_{T0}.wav"), x)
    rec = lambda t: {"t": t, "round": spec.round_for(t), "anchored": anchored(t),
                     "randomness": rand(t).hex() if anchored(t) else None}
    with open(os.path.join(d, f"session_{T0}.jsonl"), "w", encoding="utf-8") as f:
        f.write(json.dumps({"type": "session", "spec": spec.SPEC_VERSION, "key_id": spec.key_id(key), "t_start": T0,
                            "fs": FS, "block": engine.BLOCK, "params": p.as_log(), "round_lag": spec.ROUND_LAG,
                            "drand_chain": spec.DRAND_CHAIN, "feed_wav": f"feed_{T0}.wav", "feed_source": "wav",
                            "warmup": rec(T0 - 1)}) + "\n")
        for s in secs:
            f.write(json.dumps({"type": "sec", **rec(s.t), "anchor": s.anchor, "dac_utc": s.t + 0.012}) + "\n")
    return x, out.astype(np.float64)


def phone(out: np.ndarray, x: np.ndarray, mode: str, start_s: float, dur_s: float, cuts=(), rt60=0.6, snr=15.0):
    """Hall recording (1.3 s lead-in) -> crop [start, start+dur) of venue time -> cut spans (clip time)."""
    rec = selftest.simulate(out, rt60, snr, mouth=x if mode == "table" else None)
    lead = 1.3
    y = rec[int((lead + start_s) * FS):int((lead + start_s + dur_s) * FS)]
    for at, ln in sorted(cuts, reverse=True):
        a = int(at * FS)
        y = np.concatenate([y[:a], y[a + int(ln * FS):]])
    return y / (np.max(np.abs(y)) + 1e-9) * 0.7


def save_m4a(y: np.ndarray, path: str) -> str:
    """AAC-128k in an .m4a container, the way X / Instagram hand clips back."""
    src = path + ".f32"
    y.astype("<f4").tofile(src)
    subprocess.run([media.ffmpeg_exe(), "-v", "error", "-nostdin", "-y", "-f", "f32le", "-ar", str(FS), "-ac", "1",
                    "-i", src, "-c:a", "aac", "-b:a", "128k", path], check=True)
    os.remove(src)
    return path


def cli(*args, env=None) -> tuple[int, dict | None, float]:
    t = time.time()
    p = subprocess.run([sys.executable, "-m", "roomstamp.wt_detect", *args, "--quiet"], capture_output=True,
                       env={**os.environ, **(env or {})})
    out = args[args.index("-o") + 1] if "-o" in args else None
    doc = json.load(open(out, encoding="utf-8")) if out and os.path.exists(out) else None
    if p.returncode not in (0, 3) or doc is None:
        print(p.stderr.decode(errors="replace")[-3000:])
    return p.returncode, doc, time.time() - t


RESULTS = []


def check(name: str, ok: bool, doc: dict | None, dt: float, extra: str = "") -> bool:
    v = (doc or {}).get("verdict", {})
    lay = " ".join(f"{k}={(doc or {}).get('layers', {}).get(k, {}).get('status')}" for k in ("l1", "l2", "wt"))
    print(f"  {'PASS' if ok else 'FAIL'}  {name:<46} {dt:5.1f}s  {v.get('recorded_here')}/{v.get('integrity')} {lay} {extra}")
    print(f"        “{v.get('headline')}”")
    if not ok and doc:
        print("        warnings:", doc.get("warnings"))
        for e in v.get("edits", []):
            print("        edit:", {k: e.get(k) for k in ("kind", "clip_t", "removed_s", "source")})
    RESULTS.append((name, ok))
    return ok


def required_keys_ok(doc: dict) -> bool:
    """The contract: these keys always exist (null allowed), whatever happened."""
    top = ["schema", "status", "error", "pending_layers", "verdict", "clip", "layers", "proof", "warnings", "run"]
    ver = ["recorded_here", "integrity", "headline", "x_reply_text", "recorded_at_unix", "recorded_at_iso",
           "recorded_at_precision_s", "recorded_at_source", "tempo_ratio", "content_match", "venue", "edits",
           "confidence"]
    lay = ["status", "impl", "elapsed_s", "summary", "z", "p_value", "recorded_at_unix", "edits", "error"]
    ok = all(k in doc for k in top) and all(k in doc["verdict"] for k in ver)
    ok &= all(all(k in doc["layers"][n] for k in lay) for n in ("l1", "l2", "wt"))
    ok &= doc["schema"] == "wt-detect/1.0" and len(doc["verdict"]["x_reply_text"]) <= 260
    ok &= doc["verdict"]["recorded_here"] in ("verified", "likely", "not_verified", "unknown")
    ok &= doc["verdict"]["integrity"] in ("intact", "edited", "unknown")
    return ok


def main(argv=None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--keep", action="store_true", help="keep the temp dir (example detection.json files)")
    a = ap.parse_args(argv)
    root = tempfile.mkdtemp(prefix="wt_s5_")
    key, wrong = secrets.token_bytes(32), secrets.token_bytes(32)
    kf, wf = os.path.join(root, "venue.key"), os.path.join(root, "wrong.key")
    open(kf, "w").write(key.hex())
    open(wf, "w").write(wrong.hex())
    print(f"[wt_test] work dir {root}")

    pa = os.path.join(root, "venue_pa")
    x, out = make_venue(pa, "pa", key)
    J = lambda n: os.path.join(root, n + ".json")
    want_start = T0 + 8.0 + 0.012     # crop starts 8 s into the session; DAC 12 ms late

    # 1. hero: judge films 22 s mid-session, trims 0.30 s at 0:12, posts (AAC m4a). A cut inside a
    #    pause can be placed anywhere in that pause (same audio), so position tolerance is 0.5 s;
    #    the size must be exact.
    y = phone(out, x, "pa", 8.0, 22.0, cuts=[(12.0, 0.30)])
    clip = save_m4a(y, os.path.join(root, "hero_cut.m4a"))
    rc, d, dt = cli(clip, "-o", J("hero_cut"), "--venue", pa, "--key-file", kf)
    e = d["verdict"]["edits"] if d else []
    ok = rc == 0 and required_keys_ok(d) and d["verdict"]["recorded_here"] == "verified" \
        and d["verdict"]["integrity"] == "edited" and len(e) == 1 and e[0]["kind"] == "removed" and abs(e[0]["removed_s"] - 0.30) <= TOL_CUT \
        and abs(e[0]["clip_t"] - 12.0) <= 0.5 and "0.30 s removed at 0:12" in d["verdict"]["headline"] \
        and abs(d["verdict"]["recorded_at_unix"] - want_start) <= 0.05 and d["clip"]["decoder"] == "ffmpeg" \
        and e[0].get("restored_audio") and os.path.exists(os.path.join(root, e[0]["restored_audio"])) \
        and d["proof"]["matched_t_range"] and len(d["proof"]["anchors"]) >= 22
    check("hero: 0.30 s cut, AAC m4a, mid-session", ok, d, dt,
          f"cut={(e[0]['removed_s'] or 0):.3f}{'' if len(e) == 1 else f' +{len(e)-1} more'}@{e[0]['clip_t']:.2f} t_err={(d['verdict']['recorded_at_unix'] - want_start) * 1000:+.0f}ms" if e else "")

    # 2. contract + error paths (fast)
    bad = os.path.join(root, "corrupt.mp4")
    open(bad, "wb").write(os.urandom(300))
    rc, d, dt = cli(bad, "-o", J("corrupt"), "--venue", pa, "--key-file", kf)
    check("corrupt file -> status error, exit 3", rc == 3 and d and required_keys_ok(d) and d["status"] == "error"
          and d["verdict"]["recorded_here"] == "unknown", d, dt, (d or {}).get("error", {}).get("code", ""))
    short = os.path.join(root, "short.wav")
    wavio.write(short, y[:FS])
    rc, d, dt = cli(short, "-o", J("short"), "--venue", pa, "--key-file", kf)
    check("1 s clip -> TOO_SHORT, exit 3", rc == 3 and d and d["error"]["code"] == "TOO_SHORT", d, dt)
    empty = os.path.join(root, "empty_venue")
    os.makedirs(empty)
    cw = os.path.join(root, "intact.wav")
    wavio.write(cw, phone(out, x, "pa", 8.0, 20.0))
    rc, d, dt = cli(cw, "-o", J("no_session"), "--venue", empty, "--key-file", kf)
    check("no session for key -> unknown", rc == 0 and required_keys_ok(d) and d["verdict"]["recorded_here"] == "unknown",
          d, dt)
    p = subprocess.run([sys.executable, "-m", "roomstamp.wt_detect", cw, "--key-file", os.path.join(root, "nokey")],
                       capture_output=True)
    check("missing key file -> exit 2", p.returncode == 2, None, 0.0)
    if a.quick:
        return _done(root, a.keep)

    # 2b. + S4 witness record (a livestream-like recording of the venue): both layers see the cut,
    #     the verdict must show it once, with L1's sample-precise numbers
    wit = os.path.join(root, "witness_stream.wav")
    wavio.write(wit, out / (np.max(np.abs(out)) + 1e-9) * 0.7)
    rc, d, dt = cli(clip, "-o", J("hero_witness"), "--venue", pa, "--key-file", kf, "--witness", wit)
    e = d["verdict"]["edits"]
    wt = d["layers"]["wt"]
    wt_cuts = [x_ for x_ in wt.get("edits", []) if x_["kind"] == "removed"]
    check("hero + witness: S4 agrees, edit merged once", rc == 0 and wt["status"] in ("present", "likely")
          and wt["impl"] == "s4" and len(wt_cuts) == 1 and abs(wt_cuts[0]["removed_s"] - 0.30) <= 0.03
          and len(e) == 1 and e[0]["source"] == "l1" and abs(e[0]["removed_s"] - 0.30) <= TOL_CUT
          and d["verdict"]["recorded_here"] == "verified", d, dt,
          f"wt={wt['status']} wt_cut={wt_cuts[0]['removed_s']:.3f}@{wt_cuts[0]['clip_t']:.2f}" if wt_cuts else f"wt={wt['status']} {wt['summary'][:80]}")

    # 3. intact clip (WAV, no edits)
    rc, d, dt = cli(cw, "-o", J("intact"), "--venue", pa, "--key-file", kf)
    check("intact 20 s clip", rc == 0 and d["verdict"]["recorded_here"] == "verified"
          and d["verdict"]["integrity"] == "intact" and d["verdict"]["edits"] == []
          and abs(d["verdict"]["recorded_at_unix"] - want_start) <= 0.05, d, dt)

    # 4. 44.1 kHz stereo with one channel inverted (downmix would cancel the stamp)
    y44 = resample_poly(y, 147, 160)
    st = os.path.join(root, "stereo_inverted_44k.wav")
    import soundfile as sf
    sf.write(st, np.stack([y44, -0.95 * y44], axis=1), 44100, subtype="PCM_16")
    rc, d, dt = cli(st, "-o", J("stereo"), "--venue", pa, "--key-file", kf)
    e = d["verdict"]["edits"]
    check("44.1 kHz stereo, inverted channel", rc == 0 and d["verdict"]["recorded_here"] == "verified"
          and any("stereo_phase_anomaly" in w for w in d["warnings"]) and len(e) == 1
          and e[0]["kind"] == "removed" and abs(e[0]["removed_s"] - 0.30) <= TOL_CUT, d, dt, f"cut={(e[0]['removed_s'] or 0):.3f}{'' if len(e) == 1 else f' +{len(e)-1} more'}" if e else "")

    # 5. wrong key forced onto the session: must never verify
    rc, d, dt = cli(clip, "-o", J("wrong_key"), "--venue", pa, "--key-file", wf,
                    "--session", os.path.join(pa, f"session_{T0}.jsonl"))
    check("wrong key never verifies", rc == 0 and d["verdict"]["recorded_here"] in ("not_verified", "unknown")
          and d["layers"]["l1"]["status"] != "present" and d["layers"]["l2"]["status"] not in ("present", "likely"),
          d, dt)

    # 6. unstamped audio of the same words (e.g. re-recorded from a stream): words match, no stamp
    un = os.path.join(root, "unstamped.wav")
    yu = selftest.simulate(x, 0.6, 15.0)[int(9.3 * FS):int(29.3 * FS)]
    wavio.write(un, yu / (np.max(np.abs(yu)) + 1e-9) * 0.7)
    rc, d, dt = cli(un, "-o", J("unstamped"), "--venue", pa, "--key-file", kf)
    check("unstamped, same words -> not verified", rc == 0 and d["verdict"]["recorded_here"] == "not_verified"
          and d["verdict"]["content_match"] and "room stamp is missing" in d["verdict"]["headline"], d, dt)

    # 7. table mode (our demo rig): stamp-only speaker, presenter's voice, lav mic feed
    tb = os.path.join(root, "venue_table")
    xt, outt = make_venue(tb, "table", key)
    yt = phone(outt, xt, "table", 8.0, 22.0, cuts=[(12.0, 0.30)])
    ct = save_m4a(yt, os.path.join(root, "table_cut.m4a"))
    rc, d, dt = cli(ct, "-o", J("table_cut"), "--venue", tb, "--key-file", kf)
    e = d["verdict"]["edits"]
    check("table mode, 0.30 s cut, m4a", rc == 0 and d["verdict"]["recorded_here"] == "verified"
          and d["layers"]["l2"]["status"] == "skipped" and len(e) == 1 and e[0]["kind"] == "removed" and abs(e[0]["removed_s"] - 0.30) <= TOL_CUT,
          d, dt, f"cut={(e[0]['removed_s'] or 0):.3f}{'' if len(e) == 1 else f' +{len(e)-1} more'}" if e else "")

    # 8. no clean feed (emitter mic failed): L1 impossible -> --fast marks L2 pending -> --merge runs it
    nf = os.path.join(root, "venue_nofeed")
    shutil.copytree(pa, nf)
    os.remove(os.path.join(nf, f"feed_{T0}.wav"))
    c8 = os.path.join(root, "nofeed_32s.wav")   # blind L2 margin is thin under ~30 s (r128/r131)
    wavio.write(c8, phone(out, x, "pa", 6.0, 32.0))
    rc, d, dt = cli(c8, "-o", J("nofeed"), "--venue", nf, "--key-file", kf, "--fast")
    check("no feed, --fast -> L2 pending", rc == 0 and d["pending_layers"] == ["l2"]
          and d["layers"]["l2"]["status"] == "pending", d, dt)
    rc, d, dt = cli(c8, "-o", J("nofeed"), "--venue", nf, "--key-file", kf, "--layers", "l2", "--merge")
    want6 = T0 + 6.0 + 0.012
    ok = rc == 0 and d["pending_layers"] == [] and d["layers"]["l2"]["status"] in ("present", "likely") \
        and d["verdict"]["recorded_here"] in ("verified", "likely") and d["verdict"]["recorded_at_source"] == "l2" \
        and abs(d["verdict"]["recorded_at_unix"] - want6) <= 0.1 and d["verdict"]["integrity"] == "unknown"
    l2 = d["layers"]["l2"]
    check("  ... --layers l2 --merge -> blind L2 verdict", ok, d, dt,
          f"z={l2.get('z')} p={l2.get('p_value')} beats={l2.get('beats')} t_err={(d['verdict']['recorded_at_unix'] or 0) - want6:+.3f}s")
    z1 = l2.get("z")
    rc, d2, dt = cli(c8, "-o", J("nofeed_again"), "--venue", nf, "--key-file", kf, "--layers", "l2")
    check("  ... L2 is reproducible (same clip -> same z)", d2["layers"]["l2"].get("z") == z1, d2, dt)

    # 9. a hung L2 is killed at the timeout; L1 result kept
    rc, d, dt = cli(cw, "-o", J("timeout"), "--venue", pa, "--key-file", kf, "--confirm", "--l2-timeout", "4",
                    env={"WT_TEST_L2_SLEEP": "60"})
    check("hung L2 killed at timeout, L1 kept", rc == 0 and d["layers"]["l2"]["status"] == "timeout"
          and d["layers"]["l1"]["status"] == "present" and dt < 120, d, dt)
    _done(root, a.keep)


def _done(root: str, keep: bool) -> None:
    n_ok = sum(ok for _, ok in RESULTS)
    print(f"[wt_test] {n_ok}/{len(RESULTS)} passed" + ("" if n_ok == len(RESULTS) else "  <-- FAILURES"))
    if keep:
        print(f"[wt_test] example outputs kept in {root}")
    else:
        shutil.rmtree(root, ignore_errors=True)
    sys.exit(0 if n_ok == len(RESULTS) else 1)


if __name__ == "__main__":
    main()
