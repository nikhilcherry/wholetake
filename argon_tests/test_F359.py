"""Reproducer test for F359: wavio.read allocates gigabytes in resample_poly on WAV headers
with large coprime sample rates."""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import wave

import numpy as np

from roomstamp import wavio


def _write_pcm16_wav(path: str, sr: int, samples: int = 100) -> None:
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(np.zeros(samples, dtype="<i2").tobytes())


def main() -> None:
    tmp = tempfile.mkdtemp(prefix="test_f359_")
    orig_resample = wavio.resample_poly
    try:
        def guarded_resample(x, up, down, *args, **kwargs):
            if max(int(up), int(down)) > 4096:
                print(
                    f"FAIL test_F359: resample_poly called with huge polyphase factors "
                    f"up={up}, down={down} (~{20 * max(int(up), int(down)) * 8 / 1e6:.0f} MB filter allocation)"
                )
                sys.exit(1)
            return orig_resample(x, up, down, *args, **kwargs)

        wavio.resample_poly = guarded_resample

        # 1. Tiny WAV with huge coprime sample rate sr = 9999991
        p_huge = os.path.join(tmp, "huge_sr.wav")
        _write_pcm16_wav(p_huge, 9999991, samples=100)
        try:
            wavio.read(p_huge)
            print("FAIL test_F359: wavio.read accepted sr=9999991 without raising ValueError")
            sys.exit(1)
        except ValueError:
            pass

        # 2. In-range but coprime sample rate sr = 44101 (up=48000, down=44101)
        p_coprime = os.path.join(tmp, "coprime_sr.wav")
        _write_pcm16_wav(p_coprime, 44101, samples=100)
        try:
            wavio.read(p_coprime)
            print("FAIL test_F359: wavio.read accepted coprime sr=44101 without raising ValueError")
            sys.exit(1)
        except ValueError:
            pass

        # 3. Out-of-range low sample rate sr = 100 and invalid fs
        p_low = os.path.join(tmp, "low_sr.wav")
        _write_pcm16_wav(p_low, 100, samples=100)
        try:
            wavio.read(p_low)
            print("FAIL test_F359: wavio.read accepted sr=100 without raising ValueError")
            sys.exit(1)
        except ValueError:
            pass

        p_valid = os.path.join(tmp, "valid_16k.wav")
        t = np.arange(16000) / 16000.0
        sig = 0.5 * np.sin(2.0 * np.pi * 440.0 * t)
        wavio.write(p_valid, sig, fs=16000)

        for bad_fs in (0, -48000, 9999991, True, False):
            try:
                wavio.read(p_valid, fs=bad_fs)
                print(f"FAIL test_F359: wavio.read accepted invalid fs={bad_fs!r}")
                sys.exit(1)
            except ValueError:
                pass

        # 4. Standard sample rates (16000 -> 48000, 44100 -> 48000) work properly
        y_16k = wavio.read(p_valid, fs=48000)
        if len(y_16k) != 48000 or not np.all(np.isfinite(y_16k)):
            print(f"FAIL test_F359: unexpected resampled output for 16 kHz WAV (len={len(y_16k)})")
            sys.exit(1)

        p_44k = os.path.join(tmp, "valid_44k.wav")
        t44 = np.arange(44100) / 44100.0
        wavio.write(p_44k, 0.5 * np.sin(2.0 * np.pi * 440.0 * t44), fs=44100)
        y_44k = wavio.read(p_44k, fs=48000)
        if len(y_44k) != 48000 or not np.all(np.isfinite(y_44k)):
            print(f"FAIL test_F359: unexpected resampled output for 44.1 kHz WAV (len={len(y_44k)})")
            sys.exit(1)

        print("PASS test_F359")
    finally:
        wavio.resample_poly = orig_resample
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
