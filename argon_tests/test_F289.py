"""Reproducer for F289: coprime sample rate DoS in wt_detect.ingest()."""
from __future__ import annotations

import os
import shutil
import struct
import sys
import tempfile
import time

import numpy as np
import soundfile as sf

from roomstamp import wt_detect


def _write_raw_wav(path: str, sr: int, samples_i16: np.ndarray) -> None:
    raw = samples_i16.astype("<i2").tobytes()
    n_bytes = len(raw)
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        36 + n_bytes,
        b"WAVE",
        b"fmt ",
        16,
        1,
        1,
        sr,
        sr * 2,
        2,
        16,
        b"data",
        n_bytes,
    )
    with open(path, "wb") as f:
        f.write(header)
        f.write(raw)


def main() -> None:
    tmp = tempfile.mkdtemp(prefix="test_f289_")
    try:
        # 1. Coprime sample rate (192001 Hz, 3 seconds)
        sr_prime = 192001
        n = 3 * sr_prime
        t = np.arange(n, dtype=np.float64) / sr_prime
        sig = (0.3 * np.sin(2.0 * np.pi * 440.0 * t)).astype(np.float32)
        wav_prime = os.path.join(tmp, "prime_192001.wav")
        sf.write(wav_prime, sig, sr_prime, subtype="PCM_16")

        t0 = time.perf_counter()
        clip = wt_detect.ingest(wav_prime)
        dt = time.perf_counter() - t0
        if dt > 0.5:
            print(f"FAIL test_F289: 3s WAV at {sr_prime} Hz took {dt:.2f}s (expected < 0.5s)")
            sys.exit(1)
        if abs(clip.duration_s - 3.0) > 0.02:
            print(f"FAIL test_F289: unexpected resampled duration {clip.duration_s:.4f}s")
            sys.exit(1)

        # 2. Absurd low sample rate (sr = 1 Hz, 5000 samples) -> must reject before 48000x upsample
        wav_low = os.path.join(tmp, "sr_1hz.wav")
        _write_raw_wav(wav_low, 1, np.zeros(5000, dtype=np.int16))
        t0 = time.perf_counter()
        try:
            wt_detect.ingest(wav_low)
            print("FAIL test_F289: sr=1 Hz was not rejected")
            sys.exit(1)
        except wt_detect.ClipError as e:
            if e.code != "DECODE_FAILED":
                print(f"FAIL test_F289: sr=1 Hz raised {e.code}, expected DECODE_FAILED")
                sys.exit(1)
        if time.perf_counter() - t0 > 0.5:
            print("FAIL test_F289: sr=1 Hz rejection was too slow")
            sys.exit(1)

        # 3. Absurd high coprime sample rate (sr = 999991 Hz)
        wav_high = os.path.join(tmp, "sr_999991hz.wav")
        _write_raw_wav(wav_high, 999991, np.zeros(30000, dtype=np.int16))
        t0 = time.perf_counter()
        try:
            wt_detect.ingest(wav_high)
            print("FAIL test_F289: sr=999991 Hz was not rejected")
            sys.exit(1)
        except wt_detect.ClipError as e:
            if e.code != "DECODE_FAILED":
                print(f"FAIL test_F289: sr=999991 Hz raised {e.code}, expected DECODE_FAILED")
                sys.exit(1)
        if time.perf_counter() - t0 > 0.5:
            print("FAIL test_F289: sr=999991 Hz rejection was too slow")
            sys.exit(1)

        print("PASS test_F289")
        sys.exit(0)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
