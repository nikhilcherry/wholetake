"""Roomstamp keystream spec v1 — the single source of truth shared by the emitter (S1)
and the verifier (S2). Everything here is a pure function of (key, t, drand randomness):
no NumPy RNG streams (not stable across versions), only SHA-2/SHAKE from hashlib, so
any language can reimplement it bit-for-bit.

Definitions
- FS = 48000 Hz mono. "Second t" = integer nominal UTC second; the emitter's output
  sample n (from stream start) belongs to second t_start + n // FS.
- drand quicknet round used for second t: round_for(t) = expected_round(t) - ROUND_LAG.
  It was published 9-12 s before t, so the emitter (which prepares <= 5 s ahead) always
  has it cached in time, and the verifier can fetch it by number. If the emitter did not
  have it, R = 32 zero bytes and the second is "unanchored": it then proves key possession
  only, never time. "Not before" for an anchored second t is therefore t - 12 s.
- S_t      = HMAC-SHA256(K, b"roomstamp-v1|" + R + u64le(t))
- anchor_t = SHA256(b"anchor|" + S_t)                 (public, goes in the Merkle tree)
- Layer 1 white noise w_t: SHAKE-256(b"L1|" + S_t) -> FS uint16 LE -> (u - 32767.5) / 18918.6
  (uniform, zero mean, unit variance)
- Layer 1 shaped noise u_t = last FS samples of sosfilt(SOS_L1, concat(w_{t-1}, w_t)),
  zero initial state, divided by L1_NORM. Stateless per second: one second of warm-up.
- Layer 2 signs for second t: 10 frames x 10 bands. For frame m, take 10 u32 LE from
  SHAKE-256(b"L2|" + S_t) bytes [40m, 40m+40); the 5 largest bands get +1, the rest -1
  (ties broken by lower band index first via stable argsort of -value).
- Layer 2 gain in dB for band b at sample i of frame m: delta * s_{m,b}, except the first
  N_TR samples, which raised-cosine from the previous frame's sign (frame -1 = frame 9 of
  second t-1).
- Filters: all IIR filtering is scipy.signal.sosfilt semantics (cascaded biquads, Direct
  Form II transposed, zero initial state unless stated). The normative coefficients and
  L1_NORM are in spec_v1.json (`python -m roomstamp spec-json`); other implementations must
  load them rather than re-derive Butterworth designs.
- SHAKE/u16/u32 byte order is little-endian; u32 values are unsigned.
- Layer 2 applied to voice x: x + sum_b (10**(dB_b/20) - 1) * bandpass_b(x).
"""
from __future__ import annotations

import hashlib
import hmac
import struct

import numpy as np
from scipy.signal import butter, sosfilt

SPEC_VERSION = "roomstamp-v1"
FS = 48000

DRAND_CHAIN = "52db9ba70e0cc0f6eaf7803dd07447a1f5477735fd3f661792ba94600c84e971"
DRAND_GENESIS = 1692803367
DRAND_PERIOD = 3
ROUND_LAG = 3

L1_BAND = (300.0, 3400.0)
SOS_L1 = butter(4, L1_BAND, btype="bandpass", fs=FS, output="sos")

L2_NBANDS = 10
L2_FRAMES_PER_SEC = 10
L2_FRAME = FS // L2_FRAMES_PER_SEC  # 4800
L2_EDGES = 250.0 * (3400.0 / 250.0) ** (np.arange(L2_NBANDS + 1) / L2_NBANDS)
SOS_L2 = [butter(2, (float(L2_EDGES[b]), float(L2_EDGES[b + 1])), btype="bandpass", fs=FS, output="sos")
          for b in range(L2_NBANDS)]
N_TR = 1440  # 30 ms raised-cosine transition (smoother than 20 ms; less "warble")
RC = 0.5 * (1.0 - np.cos(np.pi * np.arange(N_TR) / N_TR))

ZERO_R = bytes(32)


def _measure_l1_norm() -> float:
    # Stationary RMS of the filtered unit-variance noise, measured once on a fixed sequence
    # (the analytic bandwidth ratio is off for a 4th-order Butterworth skirt).
    w = np.frombuffer(hashlib.shake_256(b"roomstamp-v1 norm").digest(2 * FS * 20), dtype="<u2")
    w = (w.astype(np.float64) - 32767.5) / 18918.6
    y = sosfilt(SOS_L1, w)[FS:]
    return float(np.sqrt(np.mean(y * y)))


L1_NORM = _measure_l1_norm()


def expected_round(t: int) -> int:
    """Latest quicknet round published at or before second t."""
    return (int(t) - DRAND_GENESIS) // DRAND_PERIOD + 1


def round_for(t: int) -> int:
    return expected_round(t) - ROUND_LAG


def key_id(key: bytes) -> str:
    return hashlib.sha256(key).hexdigest()[:16]


def second_seed(key: bytes, t: int, randomness: bytes | None) -> bytes:
    r = randomness if randomness is not None else ZERO_R
    assert len(r) == 32
    return hmac.new(key, b"roomstamp-v1|" + r + struct.pack("<Q", int(t)), hashlib.sha256).digest()


def anchor_hash(s_t: bytes) -> str:
    return hashlib.sha256(b"anchor|" + s_t).hexdigest()


def l1_white(s_t: bytes) -> np.ndarray:
    u = np.frombuffer(hashlib.shake_256(b"L1|" + s_t).digest(2 * FS), dtype="<u2")
    return (u.astype(np.float64) - 32767.5) / 18918.6


def l1_second(s_prev: bytes, s_t: bytes) -> np.ndarray:
    """Shaped, unit-RMS layer-1 noise for one second (float64, length FS)."""
    w = np.concatenate([l1_white(s_prev), l1_white(s_t)])
    return sosfilt(SOS_L1, w)[FS:] / L1_NORM


def l2_signs(s_t: bytes) -> np.ndarray:
    """(10 frames, 10 bands) of +-1, exactly five +1 per frame."""
    raw = np.frombuffer(hashlib.shake_256(b"L2|" + s_t).digest(4 * L2_NBANDS * L2_FRAMES_PER_SEC), dtype="<u4")
    raw = raw.reshape(L2_FRAMES_PER_SEC, L2_NBANDS)
    signs = -np.ones((L2_FRAMES_PER_SEC, L2_NBANDS))
    for m in range(L2_FRAMES_PER_SEC):
        top = np.argsort(-raw[m].astype(np.int64), kind="stable")[: L2_NBANDS // 2]
        signs[m, top] = 1.0
    return signs


def l2_gain_db(signs_prev: np.ndarray, signs: np.ndarray, delta_db: float) -> np.ndarray:
    """(10 bands, FS) per-sample gain in dB for one second. signs_prev = previous second's signs."""
    out = np.empty((L2_NBANDS, FS))
    prev = signs_prev[-1]
    for m in range(L2_FRAMES_PER_SEC):
        cur = signs[m]
        a = m * L2_FRAME
        out[:, a:a + N_TR] = prev[:, None] * (1.0 - RC)[None, :] + cur[:, None] * RC[None, :]
        out[:, a + N_TR:a + L2_FRAME] = cur[:, None]
        prev = cur
    return out * delta_db


class SecondKeystream:
    """Everything the emitter/verifier needs for one second."""

    __slots__ = ("t", "round", "anchored", "randomness", "seed", "anchor", "l1", "signs")

    def __init__(self, key: bytes, t: int, randomness: bytes | None, rnd: int,
                 prev_seed: bytes):
        self.t = int(t)
        self.round = rnd  # always round_for(t); `anchored` says whether its randomness was used
        self.anchored = randomness is not None
        self.randomness = randomness
        self.seed = second_seed(key, t, randomness)
        self.anchor = anchor_hash(self.seed)
        self.l1 = l1_second(prev_seed, self.seed)
        self.signs = l2_signs(self.seed)


def spec_dict() -> dict:
    """Normative constants for independent implementations (floats as repr strings)."""
    f = lambda a: [[repr(float(v)) for v in row] for row in np.asarray(a)]
    return {
        "spec": SPEC_VERSION, "fs": FS, "drand": {"chain": DRAND_CHAIN, "genesis": DRAND_GENESIS,
                                                  "period": DRAND_PERIOD, "round_lag": ROUND_LAG},
        "l1": {"band_hz": list(L1_BAND), "sos": f(SOS_L1), "norm": repr(L1_NORM),
               "white": "SHAKE-256(b'L1|'+S_t), 2*FS bytes, u16 LE, (u-32767.5)/18918.6",
               "second": "sosfilt(sos, concat(white(S_{t-1}), white(S_t)))[FS:] / norm"},
        "l2": {"nbands": L2_NBANDS, "frames_per_sec": L2_FRAMES_PER_SEC, "edges_hz": [repr(float(e)) for e in L2_EDGES],
               "sos": [f(sb) for sb in SOS_L2], "n_tr": N_TR,
               "signs": "SHAKE-256(b'L2|'+S_t), 400 bytes, u32 LE unsigned, frame m = u32[10m:10m+10]; "
                        "top 5 values -> +1 (ties: lower band index first), others -1",
               "apply": "x + sum_b (10**(delta*g_b[n]/20) - 1) * sosfilt(sos_b, x)"},
        "seed": "S_t = HMAC-SHA256(K, b'roomstamp-v1|' + R(32B, zeros if unanchored) + u64le(t)); "
                "anchor_t = SHA256(b'anchor|' + S_t)",
    }
