"""Audio decode (anything ffmpeg reads) and the acoustic features both sides share.

Features: 40-band log-mel at 100 frames/s (16 kHz, 25 ms window, 10 ms hop, 80–7600 Hz),
minus a 3 s sliding per-band mean (removes room/phone/codec EQ, which is a constant in log
space), each frame scaled to unit L2 norm so a dot product is a cosine similarity. Frames more
than 35 dB below the file's loud frames are zeroed: pauses carry no evidence either way.
"""
from __future__ import annotations

import os
import shutil
import subprocess

import numpy as np
from scipy.ndimage import uniform_filter1d
from scipy.signal import stft

SR = 16000
FPS = 100
HOP = SR // FPS
NFFT = 400
NMELS = 40
CMVN_S = 3.0
SILENCE_DB = 35.0


def ffmpeg_exe() -> str:
    """ffmpeg from $FFMPEG, PATH, or the imageio-ffmpeg wheel (same order as roomstamp.media)."""
    exe = os.environ.get("FFMPEG") or shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as e:
        raise RuntimeError("ffmpeg not found: install it or `pip install imageio-ffmpeg`") from e


def decode(path: str, sr: int = SR) -> np.ndarray:
    """Any audio/video file -> mono float32 at `sr`."""
    cmd = [ffmpeg_exe(), "-v", "error", "-nostdin", "-i", path, "-vn", "-ac", "1", "-ar", str(sr), "-f", "f32le", "-"]
    out = subprocess.run(cmd, capture_output=True, check=True).stdout
    arr = np.frombuffer(out, dtype="<f4").copy()
    return np.nan_to_num(arr, copy=False, nan=0.0, posinf=0.0, neginf=0.0)


def _mel_fb() -> np.ndarray:
    def hz2mel(f):
        return 2595.0 * np.log10(1.0 + f / 700.0)

    def mel2hz(m):
        return 700.0 * (10.0 ** (m / 2595.0) - 1.0)

    pts = mel2hz(np.linspace(hz2mel(80.0), hz2mel(7600.0), NMELS + 2))
    freqs = np.arange(NFFT // 2 + 1) * SR / NFFT
    fb = np.zeros((NMELS, len(freqs)))
    for m in range(NMELS):
        lo, c, hi = pts[m], pts[m + 1], pts[m + 2]
        fb[m] = np.clip(np.minimum((freqs - lo) / (c - lo), (hi - freqs) / (hi - c)), 0.0, None)
    return fb


MEL_FB = _mel_fb()


def logmel(x: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(frames, 40) raw log-mel in dB and per-frame energy in dB; frame k is centred at k / FPS s."""
    x = np.nan_to_num(np.asarray(x, dtype=np.float64).ravel(), copy=True, nan=0.0, posinf=0.0, neginf=0.0)
    if len(x) == 0:
        return np.zeros((0, NMELS), dtype=np.float64), np.zeros((0,), dtype=np.float64)
    if len(x) < NFFT:
        x = np.pad(x, (0, NFFT - len(x)))
    _, _, Z = stft(x, fs=SR, window="hann", nperseg=NFFT, noverlap=NFFT - HOP,
                   boundary="even", padded=True)
    p = (np.abs(Z) ** 2).T
    mel_p = p @ MEL_FB.T
    mel = 10.0 * np.log10(mel_p + 1e-10)
    energy = 10.0 * np.log10(mel_p.sum(axis=1) + 1e-10)
    return mel, energy


def features(x: np.ndarray) -> np.ndarray:
    """(frames, 40) float32 unit-norm channel-normalised log-mel (zero rows in silence)."""
    x = np.nan_to_num(np.asarray(x, dtype=np.float64).ravel(), copy=True, nan=0.0, posinf=0.0, neginf=0.0)
    if len(x) == 0:
        return np.zeros((0, NMELS), dtype=np.float32)
    mel, energy = logmel(x)
    mel = mel - uniform_filter1d(mel, size=int(CMVN_S * FPS), axis=0, mode="nearest")
    mel /= np.linalg.norm(mel, axis=1, keepdims=True) + 1e-9
    loud = np.percentile(energy, 95)
    mel[energy < loud - SILENCE_DB] = 0.0
    return mel.astype(np.float32)
