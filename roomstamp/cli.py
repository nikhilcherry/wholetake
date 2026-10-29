"""roomstamp CLI.

  python -m roomstamp keygen [-o venue.key]
  python -m roomstamp devices
  python -m roomstamp run --mode table|pa|file [--wav-in speech.wav] [--duration S] ...
  python -m roomstamp render IN.wav OUT.wav [--mode file] ...      offline, real drand rounds
  python -m roomstamp detect REC.wav --log logs/session_X.jsonl      minimal informed check
  python -m roomstamp selftest [--loopback]
  python -m roomstamp abtest [--wav-in speech.wav] [--l2-delta 2] [--l1-db -30]
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import time

import numpy as np

from . import detect, engine, spec, wavio
from .engine import StampParams
from .spec import FS


def load_key(path: str) -> bytes:
    raw = os.environ.get("ROOMSTAMP_KEY") or open(path, encoding="utf-8").read().strip()
    key = bytes.fromhex(raw)
    if len(key) != 32:
        raise SystemExit("key must be 32 bytes (64 hex chars)")
    return key


def add_params(p: argparse.ArgumentParser, mode_default: str) -> None:
    p.add_argument("--mode", choices=["pa", "table", "file"], default=mode_default)
    p.add_argument("--l1-db", type=float, default=-30.0, help="pa/file: layer 1 dB re speech RMS")
    p.add_argument("--l1-dbfs", type=float, default=-50.0, help="table: absolute layer 1 output level")
    p.add_argument("--l1-floor", type=float, default=None, help="gate floor in pauses (default pa/file 0.1, table 0.3; 1.0 = no gating)")
    p.add_argument("--l2-delta", type=float, default=4.0, help="layer 2 nominal +-dB per band (0 = off); effective ~0.38x")
    p.add_argument("--voice-gain", type=float, default=1.0)


def params_from(a) -> StampParams:
    floor = a.l1_floor if a.l1_floor is not None else (0.3 if a.mode == "table" else 0.1)
    return StampParams(mode=a.mode, l1_db=a.l1_db, l1_dbfs=a.l1_dbfs, l1_floor=floor,
                       l2_delta_db=a.l2_delta, voice_gain=a.voice_gain)


def cmd_render(a) -> None:
    from .drand import fetch_round
    key = load_key(a.key_file)
    x = wavio.read(a.wav_in)
    p = params_from(a)
    t0 = a.t_start or int(time.time()) - (len(x) // FS + 10)   # recent past: all rounds published
    cache: dict[int, bytes | None] = {}

    def lookup(t):
        r = spec.round_for(t)
        if r not in cache:
            d = None if a.unanchored else fetch_round(r)
            cache[r] = bytes.fromhex(d["randomness"]) if d else None
        return cache[r]

    wu = engine.KeystreamSequencer(key, t0, 0.0, lookup).warmup
    out, _, secs = engine.render(x, key, t0, p, lookup, return_parts=True)
    wavio.write(a.wav_out, out)
    log = os.path.splitext(a.wav_out)[0] + ".jsonl"
    feed = os.path.splitext(a.wav_out)[0] + "_feed.wav"
    wavio.write(feed, x)
    with open(log, "w", encoding="utf-8") as f:
        f.write(json.dumps({"type": "session", "spec": spec.SPEC_VERSION, "key_id": spec.key_id(key), "t_start": t0,
                            "fs": FS, "block": engine.BLOCK, "params": p.as_log(), "round_lag": spec.ROUND_LAG,
                            "drand_chain": spec.DRAND_CHAIN, "feed_wav": os.path.basename(feed),
                            "warmup": {"t": wu.t, "round": wu.round, "anchored": wu.anchored,
                                       "randomness": wu.randomness.hex() if wu.randomness else None},
                            "feed_source": "wav", "offline_render": True}) + "\n")
        for s in secs:
            f.write(json.dumps({"type": "sec", "t": s.t, "round": s.round, "anchored": s.anchored,
                                "randomness": s.randomness.hex() if s.randomness else None, "anchor": s.anchor}) + "\n")
    n_anch = sum(s.anchored for s in secs)
    print(f"wrote {a.wav_out} ({len(out) / FS:.1f}s, {n_anch}/{len(secs)} seconds anchored), {feed}, {log}")


def read_log(path: str) -> tuple[dict, dict[int, dict]]:
    """Header + {t: sec record} (incl. the warm-up second t_start-1 from the header).
    Randomness is per SECOND: the emitter may have used a round for one second and not for
    the next one sharing it; the verifier must reproduce exactly that."""
    header, secs = None, {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            if r["type"] == "session":
                header = r
                if r.get("warmup"):
                    secs[r["warmup"]["t"]] = r["warmup"]
            elif r["type"] == "sec":
                secs[r["t"]] = r
    return header, secs


def log_randomness(secs: dict[int, dict]):
    def lookup(t):
        r = secs.get(t)
        return bytes.fromhex(r["randomness"]) if r and r.get("anchored") and r.get("randomness") else None
    return lookup


def check_drand(secs: dict[int, dict], ts) -> str:
    """Compare logged randomness with the public beacon (the log is UNTRUSTED)."""
    from .drand import fetch_round
    ok = bad = unreachable = 0
    seen = {}
    for t in ts:
        r = secs.get(t)
        if not (r and r.get("anchored")):
            continue
        rnd = spec.round_for(t)
        if r.get("round") != rnd:
            bad += 1
            continue
        if rnd not in seen:
            d = fetch_round(rnd)
            seen[rnd] = d["randomness"] if d else None
        if seen[rnd] is None:
            unreachable += 1
        elif seen[rnd] == r["randomness"]:
            ok += 1
        else:
            bad += 1
    return f"{ok} ok, {bad} MISMATCH" + (f", {unreachable} unreachable" if unreachable else "")


def cmd_detect(a) -> None:
    header, secs = read_log(a.log)
    key = load_key(a.key_file)
    if spec.key_id(key) != header["key_id"]:
        print(f"note: key_id {spec.key_id(key)} != session key_id {header['key_id']} (expect 'absent')")
    feed = wavio.read(a.feed or os.path.join(os.path.dirname(a.log), header["feed_wav"]))
    p = StampParams(**header["params"])
    t0, ref_rms, gain = header["t_start"], None, None
    if a.start is not None:   # a section of a long session: feed seconds [start, start+dur)
        s0 = int(a.start)
        feed = feed[s0 * FS:(s0 + int(a.dur)) * FS]
        t0 += s0
        first = secs.get(t0, {})
        ref_rms, gain = first.get("ref_rms"), first.get("gain")
        if ref_rms is None:
            print("note: log has no ref_rms for that second; stamp level may be off for the first seconds")
    rec = wavio.read(a.rec)
    t = time.time()
    r = detect.detect(rec, feed, key, t0, p, log_randomness(secs), ref_rms=ref_rms, gain=gain)
    if a.check_drand:
        r["drand_checked"] = check_drand(secs, range(t0, t0 + r["seconds"]))
    print(json.dumps(r, indent=1) if a.json else f"{detect.verdict(r)} | speech removed {r['speech_removed_db']:.1f} dB "
          f"| lag {r['lag_samples'] / FS:+.3f}s | {time.time() - t:.1f}s")


def cmd_audibility(a) -> None:
    """Objective pre-check before the human A/B (no listening needed)."""
    from scipy.signal import sosfilt
    from . import selftest
    from .spec import L2_FRAME, L2_NBANDS, SOS_L2
    x = selftest.load_speech(a.wav_in, 30)
    p = StampParams(mode="file", l1_db=a.l1_db, l1_floor=a.l1_floor, l2_delta_db=a.l2_delta)
    out = engine.render(x, secrets.token_bytes(32), selftest.T0, p)[:len(x)].astype(np.float64)
    d = out - x
    blk = 480
    nb = len(x) // blk
    xr = np.sqrt(np.mean(x[:nb * blk].reshape(nb, blk) ** 2, axis=1) + 1e-20)
    dr = np.sqrt(np.mean(d[:nb * blk].reshape(nb, blk) ** 2, axis=1) + 1e-20)
    speech = 20 * np.log10(np.sqrt(np.mean(x ** 2) + 1e-20))
    pause = 20 * np.log10(xr) < speech - 30
    pause_db = 20 * np.log10(np.sqrt(np.mean(dr[pause] ** 2) + 1e-20)) if pause.any() else float("nan")
    talk_db = 20 * np.log10(np.sqrt(np.mean(dr[~pause] ** 2) + 1e-20)) if (~pause).any() else float("nan")
    nf = len(x) // L2_FRAME

    def band_e(sig):
        return np.array([10 * np.log10(np.mean(sosfilt(SOS_L2[b], sig)[:nf * L2_FRAME].reshape(nf, L2_FRAME) ** 2,
                                               axis=1) + 1e-14) for b in range(L2_NBANDS)])

    ex = band_e(x)
    ripple = band_e(out) - ex
    voiced = ex.max(axis=0) > max(speech - 25, -70.0)
    step = np.abs(np.diff(ripple[:, voiced], axis=1))
    med_rip = float(np.median(np.abs(ripple[:, voiced]))) if voiced.any() else float("nan")
    p95_step = float(np.percentile(step, 95)) if step.size else float("nan")
    print(f"speech {speech:.1f} dBFS | stamp+wobble residual: talking {talk_db - speech:+.1f} dB re speech, "
          f"pauses {pause_db - speech:+.1f} dB re speech ({pause_db:.1f} dBFS)")
    print(f"layer-2 band ripple on voiced frames: median |dB| {med_rip:.2f}, "
          f"frame-to-frame step p95 {p95_step:.2f} dB")
    print("pauses should be below ~-40 dB re speech; the step threshold is unvalidated -- only the human A/B (abtest) decides")


def cmd_spec_json(a) -> None:
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(spec.spec_dict(), f, indent=1)
    print(f"wrote {a.out}")


def cmd_selftest(a) -> None:
    from . import selftest
    if a.loopback:
        import sounddevice as sd
        from .emitter import find_device
        sd.default.device = (find_device(a.inp, "input", a.hostapi), find_device(a.out, "output", a.hostapi))
        sd.default.extra_settings = None
        sys.exit(0 if selftest.run_loopback(seconds=a.seconds, gain=a.gain) else 1)
    ok = True
    ok &= selftest.run_offline(StampParams(mode="file"), rt60=0.0, snr_db=40, seconds=a.seconds)
    ok &= selftest.run_offline(StampParams(mode="file"), rt60=0.6, snr_db=15, seconds=a.seconds)
    ok &= selftest.run_offline(StampParams(mode="table", l1_dbfs=-50, l1_floor=0.3), rt60=0.6, snr_db=15, seconds=a.seconds)
    print("ALL PASS" if ok else "SOME FAILED")
    sys.exit(0 if ok else 1)


def cmd_abtest(a) -> None:
    import sounddevice as sd
    from . import selftest
    x = selftest.load_speech(a.wav_in, 8)
    y = engine.render(x, secrets.token_bytes(32), selftest.T0,
                      StampParams(mode="file", l1_db=a.l1_db, l2_delta_db=a.l2_delta))[:len(x)]
    rng = np.random.default_rng()
    right = 0
    print(f"Blind A/B: L1 {a.l1_db} dB, L2 +-{a.l2_delta} dB. Each trial plays A then B; say which one is stamped.")
    for i in range(a.trials):
        b_is_stamped = bool(rng.integers(2))
        for name, sig in (("A", y if not b_is_stamped else x), ("B", y if b_is_stamped else x)):
            print(f"  trial {i + 1}: {name}...")
            sd.play(sig.astype(np.float32), FS)
            sd.wait()
        ans = input("  stamped = A or B? ").strip().upper()
        right += (ans == "B") == b_is_stamped and ans in ("A", "B")
    print(f"{right}/{a.trials} correct -> {'AUDIBLE: lower --l2-delta / --l1-db' if right >= a.trials - 1 else 'not reliably audible (PASS)'}")


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="roomstamp", description="Roomstamp S1: venue stamp emitter")
    sub = ap.add_subparsers(dest="cmd", required=True)

    k = sub.add_parser("keygen")
    k.add_argument("-o", "--out", default="venue.key")

    sub.add_parser("devices")

    r = sub.add_parser("run")
    add_params(r, "table")
    r.add_argument("--key-file", default="venue.key")
    r.add_argument("--wav-in")
    r.add_argument("--no-loop", action="store_true", help="file mode: play the WAV once, then silence")
    r.add_argument("--duration", type=float)
    r.add_argument("--out", help="output device index or name substring")
    r.add_argument("--in", dest="inp", help="input device index or name substring, or 'none'")
    r.add_argument("--hostapi", default="WASAPI" if sys.platform == "win32" else None)
    r.add_argument("--log-dir", default="logs")
    r.add_argument("--save-output", action="store_true", help="also write exactly what was played")
    r.add_argument("--quiet", action="store_true")
    r.add_argument("--latency", type=float, help="output buffer seconds (default 0.1; pa 0.08). Smaller buffers drop blocks silently on WASAPI")

    rd = sub.add_parser("render")
    add_params(rd, "file")
    rd.add_argument("wav_in")
    rd.add_argument("wav_out")
    rd.add_argument("--key-file", default="venue.key")
    rd.add_argument("--t-start", type=int)
    rd.add_argument("--unanchored", action="store_true")

    d = sub.add_parser("detect")
    d.add_argument("rec")
    d.add_argument("--log", required=True)
    d.add_argument("--feed")
    d.add_argument("--key-file", default="venue.key")
    d.add_argument("--start", type=float, help="session second offset of the section to check")
    d.add_argument("--dur", type=float, default=30)
    d.add_argument("--json", action="store_true")
    d.add_argument("--check-drand", action="store_true", help="verify logged randomness against the public beacon")

    au = sub.add_parser("audibility")
    au.add_argument("--wav-in")
    au.add_argument("--l1-db", type=float, default=-30.0)
    au.add_argument("--l1-floor", type=float, default=0.1)
    au.add_argument("--l2-delta", type=float, default=4.0)

    sj = sub.add_parser("spec-json")
    sj.add_argument("-o", "--out", default="spec_v1.json")

    s = sub.add_parser("selftest")
    s.add_argument("--loopback", action="store_true", help="play through this laptop's speaker, record with its mic")
    s.add_argument("--seconds", type=float, default=40, help="clip length; layer 2 in a hall needs ~30-40 s (r128)")
    s.add_argument("--out", help="loopback: output device index or name substring")
    s.add_argument("--in", dest="inp", help="loopback: input device index or name substring")
    s.add_argument("--hostapi", default="MME" if sys.platform == "win32" else None)
    s.add_argument("--gain", type=float, default=0.5)

    ab = sub.add_parser("abtest")
    ab.add_argument("--wav-in")
    ab.add_argument("--l2-delta", type=float, default=4.0)
    ab.add_argument("--l1-db", type=float, default=-30.0)
    ab.add_argument("--trials", type=int, default=6)

    a = ap.parse_args(argv)
    if a.cmd == "keygen":
        if os.path.exists(a.out):
            raise SystemExit(f"{a.out} exists; refusing to overwrite a venue key")
        with open(a.out, "w", encoding="utf-8") as f:
            f.write(secrets.token_hex(32) + "\n")
        print(f"wrote {a.out} (key_id {spec.key_id(load_key(a.out))}); keep it private")
    elif a.cmd == "devices":
        import sounddevice as sd
        print(sd.query_devices())
    elif a.cmd == "run":
        from .emitter import Emitter
        Emitter(load_key(a.key_file), params_from(a), log_dir=a.log_dir, wav_in=a.wav_in, loop_wav=not a.no_loop,
                out_device=a.out, in_device=a.inp, hostapi=a.hostapi, save_output=a.save_output,
                quiet=a.quiet, latency=a.latency).run(a.duration)
    elif a.cmd == "render":
        cmd_render(a)
    elif a.cmd == "detect":
        cmd_detect(a)
    elif a.cmd == "selftest":
        cmd_selftest(a)
    elif a.cmd == "abtest":
        cmd_abtest(a)
    elif a.cmd == "audibility":
        cmd_audibility(a)
    elif a.cmd == "spec-json":
        cmd_spec_json(a)


if __name__ == "__main__":
    main()
