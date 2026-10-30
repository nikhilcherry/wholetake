"""16-bit PCM WAV I/O with the stdlib (no soundfile dependency)."""
from __future__ import annotations

import wave

import numpy as np
from scipy.signal import resample_poly

from .spec import FS


def read(path: str, fs: int = FS) -> np.ndarray:
    with wave.open(path, "rb") as w:
        sr, ch, sw = w.getframerate(), w.getnchannels(), w.getsampwidth()
        raw = w.readframes(w.getnframes())
    if sw != 2:
        raise ValueError(f"{path}: only 16-bit PCM supported (got {8 * sw}-bit)")
    x = np.frombuffer(raw, dtype="<i2").astype(np.float64) / 32768.0
    if ch > 1:
        x = x.reshape(-1, ch).mean(axis=1)
    if sr != fs:
        g = np.gcd(sr, fs)
        x = resample_poly(x, fs // g, sr // g)
    return x


def to_pcm16(x: np.ndarray) -> bytes:
    arr = np.asarray(x, dtype=np.float64)
    if arr.ndim != 1:
        raise ValueError(f"to_pcm16 requires a 1-D mono array, got {arr.ndim}-D shape {arr.shape}")
    arr = np.nan_to_num(arr, nan=0.0, posinf=1.0, neginf=-1.0)
    return (np.clip(arr, -1.0, 32767 / 32768) * 32768.0).astype("<i2").tobytes()


def write(path: str, x: np.ndarray, fs: int = FS) -> None:
    pcm = to_pcm16(x)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(fs)
        w.writeframes(pcm)


class StreamWriter:
    """Append-only mono WAV writer for long sessions (header fixed up on close)."""

    def __init__(self, path: str, fs: int = FS):
        self.w = wave.open(path, "wb")
        self.w.setnchannels(1)
        self.w.setsampwidth(2)
        self.w.setframerate(fs)

    def write(self, x: np.ndarray) -> None:
        self.w.writeframesraw(to_pcm16(x))

    def close(self) -> None:
        self.w.close()
