"""S3 self-test: blind layer-2 detection on simulated chains (no phone, no feed).

  python -m roomstamp.blindtest [--clip 20] [--null 200] [--only refilm]

Chains use real lossy codecs via libsndfile (Opus, MP3); there's no AAC encoder here.
The stamped span is 39 s of speech at T0; the search window is T0-50 .. T0+90 (140 s of
keystream: blind search only needs the key's pattern there, not audio).
"""
from __future__ import annotations

import argparse
import io
import secrets
import time

import numpy as np
import soundfile as sf
from scipy.signal import butter, fftconvolve, lfilter, sosfilt

from . import blind, engine, spec
from .selftest import T0, hall_rir, load_speech
from .spec import FS

CROP_AT = 12.0


def codec(x: np.ndarray, kind: str) -> np.ndarray:
    buf = io.BytesIO()
    x = np.clip(x / (np.max(np.abs(x)) + 1e-9) * 0.7, -1, 1)
    if kind == "opus":     # voice-note-like
        sf.write(buf, x, FS, format="OGG", subtype="OPUS", compression_level=0.85)
    elif kind == "mp3":
        sf.write(buf, x, FS, format="MP3", subtype="MPEG_LAYER_III", compression_level=0.8)
    else:
        raise ValueError(kind)
    buf.seek(0)
    y, sr = sf.read(buf)
    assert sr == FS
    return y if y.ndim == 1 else y.mean(axis=1)


def babble(n: int, speech: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """Four unstamped talkers at random offsets + pinkish noise: non-stationary like a room."""
    out = np.zeros(n)
    for _ in range(4):
        s = np.roll(speech, int(rng.integers(len(speech))))
        out += np.resize(s, n) * rng.uniform(0.5, 1.0)
    pink = np.cumsum(rng.standard_normal(n)) * 0.02 + rng.standard_normal(n)
    pink -= np.convolve(pink, np.ones(4800) / 4800, mode="same")
    pink *= np.std(out) / np.std(pink) * 0.5
    return out + pink


def add_noise(y: np.ndarray, noise: np.ndarray, snr_db: float) -> np.ndarray:
    return y + noise[:len(y)] * np.sqrt(np.mean(y ** 2) / np.mean(noise[:len(y)] ** 2)) * 10 ** (-snr_db / 20)


def room(y: np.ndarray, rt60: float, seed: int) -> np.ndarray:
    return fftconvolve(y, hall_rir(rt60, seed=seed))[:len(y)]


def phone_speaker(y: np.ndarray) -> np.ndarray:
    """Tiny phone speaker: 400-7000 Hz, +6 dB resonance near 1.8 kHz, soft saturation."""
    y = sosfilt(butter(4, (400, 7000), btype="bandpass", fs=FS, output="sos"), y)
    w0, q, g = 2 * np.pi * 1800 / FS, 1.2, 10 ** (6 / 40)
    al = np.sin(w0) / (2 * q)
    b = [1 + al * g, -2 * np.cos(w0), 1 - al * g]
    a = [1 + al / g, -2 * np.cos(w0), 1 - al / g]
    y = lfilter(b, a, y)
    y = y / (np.max(np.abs(y)) + 1e-9)
    return np.tanh(2.0 * y) / np.tanh(2.0)


def chains(stamped: np.ndarray, speech: np.ndarray, rng: np.random.Generator) -> dict:
    nz = babble(len(stamped) + FS, speech, rng)

    def hall(x):
        return add_noise(room(x, 0.6, 7), nz, 10.0)

    def refilm(x):
        first = codec(add_noise(room(x, 0.6, 7), nz, 12.0), "opus")
        second = add_noise(room(phone_speaker(first), 0.3, 21), np.roll(nz, FS * 7), 15.0)
        return codec(second, "mp3")

    def tempo(x, rate):
        import librosa
        return codec(librosa.effects.time_stretch(hall(x).astype(np.float32), rate=rate).astype(np.float64), "opus")

    return {"digital": lambda x: x, "hall+opus": lambda x: codec(hall(x), "opus"),
            "hall+mp3": lambda x: codec(hall(x), "mp3"), "refilm": refilm,
            "speed1.25": lambda x: tempo(x, 1.25)}


def main(argv=None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--clip", type=float, default=20.0)
    ap.add_argument("--null", type=int, default=200)
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--delta", type=float, default=4.0, help="layer-2 +-dB used by the emitter (S1 default 4)")
    ap.add_argument("--cut", type=float, default=0.3)
    a = ap.parse_args(argv)

    key, wrong = secrets.token_bytes(32), secrets.token_bytes(32)
    # spec conformance: vectorised signs == spec.l2_signs
    rs = [None] * 4
    v = blind.signs_for(key, T0, T0 + 3, rs)
    ref = np.concatenate([spec.l2_signs(spec.second_seed(key, t, None)) for t in range(T0 - 1, T0 + 3)])
    assert np.array_equal(v, ref), "signs_for disagrees with spec.l2_signs"

    speech = load_speech()
    stamped = engine.render(speech, key, T0, engine.StampParams(mode="file", l1_db=-30, l2_delta_db=a.delta))
    stamped = stamped[:len(speech)].astype(np.float64)
    n = int(a.clip * FS)
    i0 = int(CROP_AT * FS)
    rng = np.random.default_rng(1)
    ch = chains(stamped, speech, rng)
    src = blind.RandomnessSource(unanchored=True)
    W = (T0 - 50, T0 + 90)

    cases = []
    for name, f in ch.items():
        cases.append((name, f, stamped, key, CROP_AT, 1.0, True))
    cut_src = np.concatenate([stamped[:i0 + int(10 * FS)], stamped[i0 + int((10 + a.cut) * FS):]])
    cases.append((f"hall+opus, {a.cut}s cut", ch["hall+opus"], cut_src, key, CROP_AT, 1.0, True))
    cases.append(("refilm, UNSTAMPED", ch["refilm"], speech, key, CROP_AT, 1.0, False))
    cases.append(("refilm, WRONG KEY", ch["refilm"], stamped, wrong, CROP_AT, 1.0, False))
    if a.only:
        cases = [c for c in cases if any(o in c[0] for o in a.only)]

    print(f"S3 blind self-test: {a.clip:.0f} s clips, L2 +-{a.delta} dB, window {W[1] - W[0]} s, {a.null} null keys")
    ok_all = True
    for name, f, srcsig, k, at, _, want in cases:
        t = time.time()
        y = f(srcsig)
        rate = 1.25 if "speed" in name else 1.0
        j0 = int(at * FS / rate)
        clip = y[j0:j0 + n]
        r = blind.detect(clip, k, src, *W, n_null=a.null, seed=5)
        err = (r.get("recording_start_unix", 0) - (T0 + at)) * 1000
        got = r["present"]
        ok = got == want and (not want or abs(err) <= 50)
        ok_all &= ok
        extra = f" cut {r['cut']}" if "cut" in r else ""
        print(f"  {'PASS' if ok else 'FAIL'} {name:24s} {r['verdict']:8s} z {r.get('z', 0):5.2f} "
              f"sigma {r.get('sigma_over_null', 0):5.2f} beats {r.get('beats', '-'):>7s} p {r.get('p_value', 1):.1g} "
              f"tempo {r.get('tempo', '-')} tau {r.get('reverb_tau_ms', '-')}ms "
              f"time err {err:+.0f} ms{extra} ({time.time() - t:.0f}s)")
    print("ALL PASS" if ok_all else "SOME FAILED")


if __name__ == "__main__":
    main()
