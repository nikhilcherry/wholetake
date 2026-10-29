"""Self-tests that need no phone: offline (simulated hall) and loopback (laptop speaker -> mic)."""
from __future__ import annotations

import os
import secrets
import time

import numpy as np
from scipy.signal import fftconvolve

from . import detect, engine, wavio
from .spec import FS

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_SPEECH = os.path.join(HERE, "testdata", "speech.wav")
T0 = 1793209200  # any nominal second; offline tests run unanchored


def load_speech(path: str | None = None, seconds: float | None = None) -> np.ndarray:
    x = wavio.read(path or DEFAULT_SPEECH)
    if seconds is not None:
        x = x[:max(0, int(seconds * FS))]
    if len(x) == 0:
        return x
    return x / (np.sqrt(np.mean(x ** 2)) + 1e-12) * 10 ** (-20 / 20)  # -20 dBFS speech


def hall_rir(rt60: float = 0.6, drr_db: float = 0.0, seed: int = 7) -> np.ndarray:
    rng = np.random.default_rng(seed)
    n = int(rt60 * 1.5 * FS)
    t = np.arange(n) / FS
    tail = rng.standard_normal(n) * np.exp(-6.91 * t / rt60)
    tail[:int(0.004 * FS)] = 0
    h = np.zeros(n)
    h[int(0.003 * FS)] = 1.0
    tail *= 10 ** (-drr_db / 20) / np.sqrt(np.sum(tail ** 2))
    return h + tail


def simulate(out: np.ndarray, rt60: float, snr_db: float, lead_s: float = 1.3, seed: int = 3,
             mouth: np.ndarray | None = None) -> np.ndarray:
    """Speaker output (and optionally a live talker, on a different room path) -> hall -> noise."""
    rng = np.random.default_rng(seed)
    y = fftconvolve(out, hall_rir(rt60)) if rt60 > 0 else out.astype(np.float64)
    if mouth is not None:  # table mode: voice comes from the presenter, ~0.8 m further away
        m = np.concatenate([np.zeros(int(0.0025 * FS)), mouth])
        m = fftconvolve(m, hall_rir(rt60, seed=11)) if rt60 > 0 else m
        y = np.pad(y, (0, max(0, len(m) - len(y))))
        y[:len(m)] += m
    y = np.concatenate([np.zeros(int(lead_s * FS)), y])
    p_speech = np.mean(y[y != 0] ** 2)
    noise = rng.standard_normal(len(y))
    noise = np.cumsum(noise) * 0.02 + noise  # pinkish
    noise -= np.convolve(noise, np.ones(4800) / 4800, mode="same")
    noise *= np.sqrt(p_speech / np.mean(noise ** 2)) * 10 ** (-snr_db / 20)
    return y + noise


def run_offline(params: engine.StampParams, rt60: float = 0.6, snr_db: float = 15.0, seconds: float = 30.0,
                speech: str | None = None) -> bool:
    key, wrong = secrets.token_bytes(32), secrets.token_bytes(32)
    x = load_speech(speech, seconds)
    t = time.time()
    out = engine.render(x, key, T0, params)
    rec = simulate(out, rt60, snr_db, mouth=x if params.mode == "table" else None)
    lvl = f"L1 {params.l1_dbfs} dBFS abs" if params.mode == "table" else f"L1 {params.l1_db} dB"
    print(f"[selftest] {params.mode} {lvl}  L2 +-{params.l2_delta_db} dB  floor {params.l1_floor} | "
          f"hall RT60 {rt60}s, SNR {snr_db} dB, {seconds:.0f}s clip")
    ok = True
    for name, k in (("right key", key), ("wrong key", wrong)):
        r = detect.detect(rec, x, k, T0, params)
        print(f"  {name}: {detect.verdict(r)} | speech removed {r['speech_removed_db']:.1f} dB")
        l1_present = r["l1"]["frac_z5"] >= 0.20 and r["l1"]["clip_z"] >= detect.CLIP_Z_MIN
        l2_present = r.get("l2", {}).get("z", 0) > 4
        ok &= (l1_present and (l2_present or "l2" not in r)) if name == "right key" else not (l1_present or l2_present)
    print(f"  {'PASS' if ok else 'FAIL'} ({time.time() - t:.1f}s)")
    return ok


def run_loopback(seconds: float = 20.0, params: engine.StampParams | None = None, gain: float = 0.5) -> bool:
    """Play a stamped clip through the default speaker and record it with the default mic."""
    import sounddevice as sd
    params = params or engine.StampParams(mode="file")
    key, wrong = secrets.token_bytes(32), secrets.token_bytes(32)
    x = load_speech(None, seconds)
    out = engine.render(x, key, T0, params)
    play = np.concatenate([np.zeros(FS // 2), out * gain, np.zeros(FS)]).astype(np.float32)
    print(f"[loopback] playing {len(play) / FS:.1f}s through '{sd.query_devices(kind='output')['name']}', "
          f"recording '{sd.query_devices(kind='input')['name']}'")
    rec = sd.playrec(play[:, None], samplerate=FS, channels=1, dtype="float32")
    sd.wait()
    rec = rec[:, 0].astype(np.float64)
    wavio.write(os.path.join(HERE, "logs", "loopback_rec.wav"), rec) if os.path.isdir(os.path.join(HERE, "logs")) else None
    print(f"  recorded level {10 * np.log10(np.mean(rec ** 2) + 1e-20):.1f} dBFS")
    ok = True
    for name, k in (("right key", key), ("wrong key", wrong)):
        r = detect.detect(rec, x, k, T0, params)
        print(f"  {name}: {detect.verdict(r)} | speech removed {r['speech_removed_db']:.1f} dB | lag {r['lag_samples'] / FS:.3f}s")
        l1_present = r["l1"]["frac_z5"] >= 0.20 and r["l1"]["clip_z"] >= detect.CLIP_Z_MIN
        ok &= l1_present if name == "right key" else not (l1_present or r.get("l2", {}).get("z", 0) > 4)
    print(f"  {'PASS' if ok else 'FAIL'} (layer 2 is reported but not required: near-field laptop path)")
    return ok
