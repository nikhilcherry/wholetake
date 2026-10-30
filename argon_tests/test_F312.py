"""Reproducer for F312: audio.logmel() silence-gate energy band mismatch (0-8000 Hz vs 80-7600 Hz)."""
from __future__ import annotations

import os
import sys

os.environ["WHOLETAKE_ASR"] = "none"

import numpy as np

from wholetake import audio, match, record
from wholetake.audio import SR

HERE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "testdata", "witness")
TALK = os.path.join(HERE, "talk.wav")
REC_DIR = os.path.join(HERE, "record_noasr")


def test_07_sub80hz_thump_and_dc_offset_silence_gate(W: np.ndarray, rec: record.Record) -> tuple[bool, str]:
    """A strong sub-80 Hz thump / DC offset burst must not blind in-band (80-7600 Hz) speech features."""
    clip = W[15 * SR : 30 * SR].copy().astype(np.float64)
    # Inject a 1.5 s sub-80 Hz (40 Hz + DC) burst ~40 dB above speech RMS.
    # At SR=16000, NFFT=400 (40 Hz bin spacing), 0 Hz (DC) and 40 Hz (bin 1) with a Hann window
    # have support only on bins 0, 1, 2 (0, 40, 80 Hz), where MEL_FB weight is strictly 0.
    burst_n = int(1.5 * SR)
    t = np.arange(burst_n) / SR
    env = np.hanning(burst_n)
    clip[:burst_n] += env * (25.0 + 25.0 * np.cos(2.0 * np.pi * 40.0 * t))
    r = match.match(rec, clip.astype(np.float32))
    p = r.get("pieces", [])
    if r.get("verdict") != "faithful" or len(p) != 1:
        return False, (
            f"sub-80Hz mic thump blinded in-band (80-7600Hz) speech features: "
            f"verdict={r.get('verdict')}, pieces={p}"
        )
    if abs(p[0]["witness_start_s"] - 15.0) > 0.25 or abs(p[0]["witness_end_s"] - 30.0) > 0.25:
        return False, f"unexpected witness span: {p[0]}"
    return True, "ok"


def test_08_out_of_band_hum_on_silent_leadin(W: np.ndarray, rec: record.Record) -> tuple[bool, str]:
    """A silent 2.0 s lead-in with 40 Hz mains hum must be gated as silence, not reported as active foreign speech."""
    speech = W[20 * SR : 32 * SR].copy().astype(np.float64)
    lead_n = int(2.0 * SR)
    rng = np.random.default_rng(42)
    t = np.arange(lead_n) / SR
    # 40 Hz out-of-band hum (-15 dB re unit, well above loud - 35 dB) + tiny -80 dB thermal noise floor
    lead_in = 0.18 * np.sin(2.0 * np.pi * 40.0 * t) + 1e-4 * rng.standard_normal(lead_n)
    # Smooth 25 ms fade on the 40 Hz hum at the join so there is no broadband step click
    fade = int(0.025 * SR)
    lead_in[-fade:] *= 0.5 * (1.0 + np.cos(np.pi * np.arange(fade) / fade))
    clip = np.concatenate([lead_in, speech]).astype(np.float32)
    r = match.match(rec, clip)
    if r.get("lead_in_s") is not None:
        return False, (
            f"2.0s silent lead-in with 40Hz hum falsely reported as active foreign speech "
            f"lead_in_s={r.get('lead_in_s')}s because full-band energy prevented silence gating "
            f"and unit-normed thermal noise in mel bands"
        )
    if r.get("verdict") != "faithful" or len(r.get("pieces", [])) != 1:
        return False, f"expected faithful match, got verdict={r.get('verdict')}, pieces={r.get('pieces')}"
    return True, "ok"


def main() -> int:
    rec = record.load(REC_DIR)
    W = audio.decode(TALK)
    tests = [
        test_07_sub80hz_thump_and_dc_offset_silence_gate,
        test_08_out_of_band_hum_on_silent_leadin,
    ]
    failed = 0
    for fn in tests:
        name = fn.__name__
        try:
            ok, msg = fn(W, rec)
        except Exception as e:
            ok, msg = False, f"{type(e).__name__}: {e}"
        if ok:
            print(f"PASS {name}")
        else:
            print(f"FAIL {name}: {msg}")
            failed += 1
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
