"""S4 acceptance suite: 6 cases + the one-word "not" cut, all cut from the test witness.

usage: python -m wholetake.selftest [--rebuild] [--only 1,7] [-v]

The witness is testdata/witness/talk.wav (226 s, TTS, see tools/tts_testdata.py); its record is
built once into testdata/witness/record/. Clips go through real codecs via ffmpeg (AAC, Opus,
atempo) plus simulated reverb, noise and a music bed. Truth is known to the sample.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time

import numpy as np
from scipy.signal import fftconvolve

from . import asr, audio, match, record
from .audio import SR

HERE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "testdata", "witness")
TALK, OTHER, INSERT = (os.path.join(HERE, f) for f in ("talk.wav", "other.wav", "insert.wav"))
REC_DIR = os.path.join(HERE, "record" if asr. model() is not None else "record_noasr")


# ---------------------------------------------------------------------------- transforms

def ffmpeg_chain(x: np.ndarray, out_args: list[str], ext: str, filt: str | None = None) -> np.ndarray:
    with tempfile.TemporaryDirectory() as d:
        src, dst = os.path.join(d, "in.wav"), os.path.join(d, "out." + ext)
        _write_wav(src, x)
        cmd = [audio.ffmpeg_exe(), "-v", "error", "-y", "-i", src] + (["-af", filt] if filt else []) + out_args + [dst]
        subprocess.run(cmd, check=True, capture_output=True)
        return audio.decode(dst)


def _write_wav(path: str, x: np.ndarray, sr: int = SR) -> None:
    import wave
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((np.clip(x, -1, 0.99997) * 32768).astype("<i2").tobytes())


def aac(x, kbps=64):
    return ffmpeg_chain(x, ["-c:a", "aac", "-b:a", f"{kbps}k"], "m4a")


def opus(x, kbps=32):
    return ffmpeg_chain(x, ["-c:a", "libopus", "-b:a", f"{kbps}k"], "ogg")


def atempo(x, rate):
    return ffmpeg_chain(x, ["-c:a", "pcm_s16le"], "wav", filt=f"atempo={rate}")


def reverb(x, rt60=0.35, seed=5):
    rng = np.random.default_rng(seed)
    n = int(rt60 * 1.5 * SR)
    t = np.arange(n) / SR
    h = rng.standard_normal(n) * np.exp(-6.91 * t / rt60)
    h[: int(0.003 * SR)] = 0
    h /= np.sqrt(np.sum(h ** 2))
    h[0] = 1.0
    y = fftconvolve(x, h)[: len(x)]
    return y / (np.max(np.abs(y)) + 1e-9) * 0.7


def noise(x, snr_db, seed=9):
    rng = np.random.default_rng(seed)
    n = rng.standard_normal(len(x))
    n = np.cumsum(n) * 0.02 + n
    n -= np.convolve(n, np.ones(800) / 800, mode="same")
    p = np.mean(x[np.abs(x) > 1e-4] ** 2)
    return x + n * np.sqrt(p / np.mean(n ** 2)) * 10 ** (-snr_db / 20)


def music_bed(x, rel_db=-12.0):
    t = np.arange(len(x)) / SR
    chords = [(220, 277, 330), (196, 247, 294), (175, 220, 262), (196, 247, 294)]
    m = np.zeros(len(x))
    for k, ch in enumerate(chords * (int(t[-1] / 2) // 4 + 1)):
        a, b = int(k * 2 * SR), int((k + 1) * 2 * SR)
        if a >= len(x):
            break
        seg = t[a:b]
        m[a:b] = sum(np.sin(2 * np.pi * f * seg) for f in ch)
    beat = (np.mod(t, 0.5) < 0.03) * np.random.default_rng(1).standard_normal(len(x))
    m = m / np.std(m) + 0.8 * beat / (np.std(beat) + 1e-9)
    return x + m * np.std(x) * 10 ** (rel_db / 20)


def crossfade_join(parts: list[np.ndarray], fade_s=0.005) -> np.ndarray:
    n = int(fade_s * SR)
    out = parts[0].copy()
    ramp = 0.5 - 0.5 * np.cos(np.pi * np.arange(n) / n)
    for p in parts[1:]:
        p = p.copy()
        out[-n:] = out[-n:] * (1 - ramp) + p[:n] * ramp
        out = np.concatenate([out, p[n:]])
    return out


def seg(x, a, b):
    return x[int(round(a * SR)):int(round(b * SR))]


def quiet_point(x, t, search=0.06):
    """Nearest 10 ms energy minimum to t (an editor cuts in the gap between words)."""
    a, b = int((t - search) * SR), int((t + search) * SR)
    e = np.convolve(x[a:b] ** 2, np.ones(160) / 160, mode="same")
    return (a + int(np.argmin(e))) / SR


# ---------------------------------------------------------------------------- cases

def near(v, target, tol):
    return v is not None and abs(v - target) <= tol


def case_faithful(W, rec):
    clip = aac(noise(reverb(seg(W, 15.0, 32.0)), 18))
    def ok(r):
        p = r["pieces"]
        return (r["verdict"] == "faithful" and len(p) == 1 and not r["gaps"]
                and near(p[0]["witness_start_s"], 15.0, 0.15) and near(p[0]["witness_end_s"], 32.0, 0.15))
    return clip, ok, "faithful 17 s, reverb RT60 0.35 + noise 18 dB + AAC-64k"


def case_clause_cut(W, rec):
    clip = opus(crossfade_join([seg(W, 40.0, 46.5), seg(W, 49.8, 56.0)], 0.01))
    def ok(r):
        g = r["gaps"]
        return (r["verdict"] == "cut" and len(g) == 1 and near(g[0]["clip_at_s"], 6.5, 0.08)
                and near(g[0]["removed_s"], 3.30, 0.05))
    return clip, ok, "3.30 s removed at 6.5 s, Opus-32k"


def case_reorder(W, rec):
    clip = aac(music_bed(crossfade_join([seg(W, 120.0, 127.0), seg(W, 20.0, 27.0)], 0.01), -12))
    def ok(r):
        p = r["pieces"]
        return (r["verdict"] == "spliced" and len(p) == 2 and near(p[0]["witness_start_s"], 120.0, 0.15)
                and near(p[1]["witness_start_s"], 20.0, 0.15) and near(p[1]["witness_end_s"], 27.0, 0.15))
    return clip, ok, "witness 120–127 then 20–27, music bed −12 dB + AAC-64k"


def case_tempo_cut(W, rec):
    clip = aac(atempo(crossfade_join([seg(W, 60.0, 68.0), seg(W, 69.5, 76.5)], 0.01), 1.25))
    def ok(r):
        g = r["gaps"]
        return (r["verdict"] == "cut" and near(r["tempo"], 1.25, 0.02) and len(g) == 1
                and near(g[0]["clip_at_s"], 8.0 / 1.25, 0.10) and near(g[0]["removed_s"], 1.50, 0.08))
    return clip, ok, "1.50 s cut, then atempo 1.25x + AAC-64k"


def case_foreign(W, rec):
    ins = audio.decode(INSERT)
    clip = noise(crossfade_join([seg(W, 140.0, 146.0), ins, seg(W, 146.0, 152.0)], 0.01), 20)
    a, b = 6.0, 6.0 + len(ins) / SR
    def ok(r):
        f = r["foreign_insertions"]
        return (r["verdict"] == "spliced" and len(f) == 1 and near(f[0]["clip_start_s"], a, 0.2)
                and near(f[0]["clip_end_s"], b, 0.2))
    return clip, ok, f"same-voice TTS sentence ({b - a:.2f} s) inserted at 6.0 s, noise 20 dB"


def case_not_found(W, rec):
    clip = seg(audio.decode(OTHER), 5.0, 17.0)
    def ok(r):
        return r["verdict"] == "not_found" and not r["pieces"]
    return clip, ok, "12 s of a different talk (different voice)"


def find_not(rec) -> tuple[float, float] | None:
    toks = [w.tok for w in rec.words]
    for i in range(len(toks) - 3):
        if toks[i:i + 4] == ["will", "not", "raise", "ticket"]:
            return rec.words[i + 1].start, rec.words[i + 1].end
    return None


def case_not(W, rec):
    span = find_not(rec)
    if span is None:
        return None
    t1, t2 = quiet_point(W, span[0]), quiet_point(W, span[1])
    clip = noise(aac(crossfade_join([seg(W, t1 - 5.0, t1), seg(W, t2, t2 + 5.0)]), 96), 22)
    removed = round(t2 * SR) / SR - round(t1 * SR) / SR
    def ok(r):
        g = r["gaps"]
        return (r["verdict"] == "cut" and len(g) == 1 and "not" in r["decisive_words_removed"]
                and near(g[0]["clip_at_s"], 5.0, 0.05) and near(g[0]["removed_s"], removed, 0.03))
    return clip, ok, f'"not" removed ({removed:.3f} s at 5.0 s), AAC-96k + noise 22 dB'


CASES = [case_faithful, case_clause_cut, case_reorder, case_tempo_cut, case_foreign, case_not_found, case_not]


def main(argv=None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--rebuild", action="store_true")
    ap.add_argument("--only", default="")
    ap.add_argument("-v", action="store_true")
    a = ap.parse_args(argv)
    rec_dir = REC_DIR if os.path.exists(os.path.join(REC_DIR, "meta.json")) or asr.model() is not None else os.path.join(HERE, "record_noasr")
    if a.rebuild or not os.path.exists(os.path.join(rec_dir, "meta.json")):
        t = time.time()
        record.build(TALK, rec_dir, "hh-test-talk")
        print(f"built witness record in {time.time() - t:.1f} s -> {rec_dir}")
    rec = record.load(rec_dir)
    W = audio.decode(TALK)
    only = {int(s) for s in a.only.split(",") if s}
    passed = total = 0
    for n, fn in enumerate(CASES, 1):
        if only and n not in only:
            continue
        made = fn(W, rec)
        if made is None:
            print(f"[{n}] {fn.__name__}: SKIP (needs ASR words in the record)")
            continue
        clip, ok, desc = made
        r = match.match(rec, clip)
        good = bool(ok(r))
        passed += good
        total += 1
        gaps = "; ".join(f'{g["type"]} {g["removed_s"]:+.3f}s@{g["clip_at_s"]:.2f}'
                         + (f' "{g.get("restored_text", "")}"' if g.get("restored_text") else "") for g in r["gaps"])
        print(f"[{n}] {'PASS' if good else 'FAIL'} {fn.__name__}: {desc}\n"
              f"     -> {r['verdict']}, tempo {r['tempo']}, pieces "
              + ", ".join(f'{p["clip_start_s"]:.2f}-{p["clip_end_s"]:.2f}→{p["witness_start_s"]:.2f}-{p["witness_end_s"]:.2f}'
                          for p in r["pieces"])
              + (f" | gaps: {gaps}" if gaps else "")
              + (f" | foreign: {r['foreign_insertions']}" if r["foreign_insertions"] else "")
              + f" | {r['timing_ms'].get('total_ms', '?')} ms")
        if a.v:
            print(json.dumps(r, indent=1, ensure_ascii=False))
    print(f"{passed}/{total} passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
