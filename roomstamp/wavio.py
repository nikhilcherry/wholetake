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
    return (np.clip(np.asarray(x), -1.0, 32767 / 32768) * 32768.0).astype("<i2").tobytes()


def write(path: str, x: np.ndarray, fs: int = FS) -> None:
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(fs)
        w.writeframes(to_pcm16(x))


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
