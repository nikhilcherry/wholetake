"""Block DSP engine. The same code runs inside the real-time audio callback and in offline
renders, so a verifier can re-render exactly what the speaker played from the clean feed.

Modes
- "pa":    voice in (mic) -> layer-2 envelope wobble + gated layer-1 noise -> PA out.
- "file":  same as "pa" but the voice comes from a WAV (fallback demo, self-tests).
- "table": speaker plays ONLY gated layer-1 noise at an absolute level; the mic is used
           for gating and is archived as the clean feed. Layer 2 needs the voice to pass
           through the speaker, so it is off in this mode.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
from scipy.signal import lfilter, lfilter_zi, sosfilt

from . import spec
from .spec import FS, L2_NBANDS, SOS_L2

BLOCK = 480  # 10 ms; must divide FS so a block never straddles a second
assert FS % BLOCK == 0

ENV_TAU = 0.020                       # gate envelope follower
ENV_A = 1.0 - np.exp(-1.0 / (FS * ENV_TAU))
REF_INIT_DBFS = -18.0                 # speech-level tracker start value
REF_MIN_BLOCK_DBFS = -45.0            # blocks quieter than this don't update the tracker
REF_COEF = 0.005                      # per-block EMA, ~2 s time constant at 100 blocks/s
GATE_SPEECH = 0.6                     # env (mean |x|) at which the gate fully opens, x ref_rms
HOWL_DBFS = -3.0                      # pa mode feedback breaker threshold (input block RMS)
LIMIT_KNEE = 0.85
LIMIT_CEIL = 0.98


@dataclass
class StampParams:
    mode: str = "file"
    l1_db: float = -30.0        # pa/file: layer-1 RMS relative to tracked speech RMS (dB)
    l1_dbfs: float = -50.0      # table: absolute layer-1 RMS at the output (dBFS)
    l1_floor: float | None = None  # gate floor in pauses (0.1 = -20 dB; table default 0.3); 1.0 = no gating
    l2_delta_db: float = 4.0    # layer-2 nominal +-dB per band (pa/file). Overlapping bands deliver ~0.38x: 4 -> ~1.5 dB effective
    voice_gain: float = 1.0     # pa/file: output gain (voice and stamp together)

    def __post_init__(self) -> None:
        if self.mode not in ("pa", "file", "table"):
            raise ValueError(f"invalid mode: {self.mode!r}")
        if self.l1_floor is None:
            self.l1_floor = 0.3 if self.mode == "table" else 0.1
        for name in ("l1_db", "l1_dbfs", "l1_floor", "l2_delta_db", "voice_gain"):
            val = getattr(self, name)
            if isinstance(val, bool) or not isinstance(val, (int, float, np.floating)):
                raise ValueError(f"{name} must be a finite number, got {val!r}")
            fval = float(val)
            if not math.isfinite(fval):
                raise ValueError(f"{name} must be finite, got {val!r}")
            setattr(self, name, fval)
        if not (0.0 <= self.l1_floor <= 1.0):
            raise ValueError(f"l1_floor must be in [0, 1], got {self.l1_floor!r}")
        if self.l1_db > 200.0:
            raise ValueError(f"l1_db out of range: {self.l1_db!r}")
        if self.l1_dbfs > 200.0:
            raise ValueError(f"l1_dbfs out of range: {self.l1_dbfs!r}")
        if not (0.0 <= self.l2_delta_db <= 100.0):
            raise ValueError(f"l2_delta_db must be in [0, 100], got {self.l2_delta_db!r}")
        if not (0.0 <= self.voice_gain <= 1e6):
            raise ValueError(f"voice_gain must be in [0, 1e6], got {self.voice_gain!r}")

    def as_log(self) -> dict:
        return {k: getattr(self, k) for k in ("mode", "l1_db", "l1_dbfs", "l1_floor", "l2_delta_db", "voice_gain")}


@dataclass
class PreparedSecond:
    """Keystream for one second, expanded to per-sample arrays (float32)."""
    ks: spec.SecondKeystream
    l1: np.ndarray                 # (FS,)
    l2_gm1: np.ndarray | None      # (10, FS) = 10**(dB/20) - 1, or None if layer 2 off


def prepare_second(ks: spec.SecondKeystream, prev_signs: np.ndarray, delta_db: float) -> PreparedSecond:
    gm1 = None
    if delta_db > 0:
        gm1 = (10.0 ** (spec.l2_gain_db(prev_signs, ks.signs, delta_db) / 20.0) - 1.0).astype(np.float32)
    return PreparedSecond(ks=ks, l1=ks.l1.astype(np.float32), l2_gm1=gm1)


@dataclass
class BlockStats:
    voice_sq: float = 0.0
    stamp_sq: float = 0.0
    limited: int = 0
    missing: bool = False
    howl: bool = False


class Stamper:
    """All state that shapes the output lives here and depends only on the input x, so a
    verifier holding the clean feed reproduces it exactly (incl. howl protection).
    To start mid-session, seed `ref_rms` (and `gain`) from the per-second log."""

    def __init__(self, params: StampParams, ref_rms: float | None = None, gain: float | None = None):
        if params.mode not in ("pa", "file", "table"):
            raise ValueError(params.mode)
        self.p = params
        self.l2_on = params.mode != "table" and params.l2_delta_db > 0
        self.env_zi = lfilter_zi([ENV_A], [1.0, -(1.0 - ENV_A)]) * 0.0
        self.band_zi = [np.zeros((s.shape[0], 2)) for s in SOS_L2]
        default_ref = 10.0 ** (REF_INIT_DBFS / 20.0)
        if ref_rms is not None and not isinstance(ref_rms, bool):
            try:
                r = float(ref_rms)
            except (TypeError, ValueError):
                r = float("nan")
            self.ref_rms = max(r, 1e-20) if (math.isfinite(r) and r > 0.0) else default_ref
        else:
            self.ref_rms = default_ref

        default_gain = float(params.voice_gain) if (
            not isinstance(params.voice_gain, bool)
            and isinstance(params.voice_gain, (int, float, np.floating))
            and math.isfinite(float(params.voice_gain))
            and float(params.voice_gain) >= 0.0
        ) else 1.0
        if gain is not None and not isinstance(gain, bool):
            try:
                g = float(gain)
            except (TypeError, ValueError):
                g = float("nan")
            self.gain = g if (math.isfinite(g) and g > 0.0) else default_gain
        else:
            self.gain = default_gain
        self.howl_blocks = 0
        self.mute_blocks = 0

    def process(self, x: np.ndarray, sec: PreparedSecond | None, pos: int) -> tuple[np.ndarray, np.ndarray, BlockStats]:
        """x: one block of clean voice (pa/file) or mic (table), float. pos: sample offset
        of this block inside its second. Returns (out, stamp_component, stats)."""
        n = len(x)
        st = BlockStats()
        x = np.nan_to_num(np.asarray(x, dtype=np.float64), nan=0.0, posinf=1.0, neginf=-1.0)
        if pos + n > FS:   # never happens with fixed BLOCK; never crash the audio thread
            st.missing = True
            return np.zeros(n, np.float32), np.zeros(n, np.float32), st
        if n == 0:
            return np.zeros(0, np.float32), np.zeros(0, np.float32), st

        env, self.env_zi = lfilter([ENV_A], [1.0, -(1.0 - ENV_A)], np.abs(x), zi=self.env_zi)
        blk_rms = float(np.sqrt(np.mean(x * x) + 1e-20))
        if 20 * np.log10(blk_rms) > REF_MIN_BLOCK_DBFS:
            self.ref_rms += REF_COEF * (blk_rms - self.ref_rms)

        floor = self.p.l1_floor
        gate = np.clip(floor + (1.0 - floor) * env / max(GATE_SPEECH * self.ref_rms, 1e-20), floor, 1.0) if floor < 1.0 else 1.0

        if self.p.mode == "table":
            amp = 10.0 ** (self.p.l1_dbfs / 20.0)
        else:
            amp = 10.0 ** (self.p.l1_db / 20.0) * self.ref_rms

        if sec is None:
            st.missing = True
            stamp = np.zeros(n)
        else:
            stamp = amp * gate * sec.l1[pos:pos + n]

        if self.p.mode == "table":
            voice = np.zeros(n)
        else:
            voice = x.copy()
            if self.l2_on:
                for b in range(L2_NBANDS):
                    yb, self.band_zi[b] = sosfilt(SOS_L2[b], x, zi=self.band_zi[b])
                    if sec is not None:
                        voice += sec.l2_gm1[b, pos:pos + n] * yb
            if self.p.mode == "pa":   # howl breaker: input this hot for 100 ms -> mute 1 s, halve gain
                hot = blk_rms > 10 ** (HOWL_DBFS / 20)
                self.howl_blocks = self.howl_blocks + 1 if hot else max(0, self.howl_blocks - 1)
                if self.howl_blocks >= 10:
                    self.mute_blocks, self.howl_blocks = FS // n, 0
                    self.gain *= 0.5
                    st.howl = True
            # output gain applies to voice AND stamp so their ratio never changes
            g = 0.0 if self.mute_blocks else self.gain
            if self.mute_blocks:
                self.mute_blocks -= 1
            voice *= g
            stamp = stamp * g

        out = voice + stamp
        a = np.abs(out)
        hot = a > LIMIT_KNEE
        if hot.any():
            st.limited = int(hot.sum())
            span = LIMIT_CEIL - LIMIT_KNEE
            out[hot] = np.sign(out[hot]) * (LIMIT_KNEE + span * np.tanh((a[hot] - LIMIT_KNEE) / span))
        st.voice_sq = float(np.sum(x * x))
        st.stamp_sq = float(np.sum(stamp * stamp))
        return out.astype(np.float32), stamp.astype(np.float32), st


class KeystreamSequencer:
    """Produces PreparedSeconds in order (t, t+1, ...). `randomness_for_second(t)` returns the
    32-byte drand randomness the emitter used for second t (round spec.round_for(t)), or None
    if that second was unanchored. Keyed by SECOND, not round: three seconds share a round
    and the emitter may have had it for some of them but not others."""

    def __init__(self, key: bytes, t_start: int, delta_db: float, randomness_for_second):
        self.key = key
        self.delta = delta_db
        self.lookup = randomness_for_second
        self.next_t = int(t_start)
        if self.next_t < 1:
            raise ValueError(f"t_start must be >= 1, got {t_start}")
        # second t_start-1 is the warm-up predecessor: its seed (layer-1 warm-up) and its
        # signs (layer-2 transition into frame 0) shape the first real second
        t = self.next_t - 1
        r = self.lookup(t)
        self.warmup = spec.SecondKeystream(self.key, t, r, spec.round_for(t), spec.ZERO_R)  # .l1 unused
        self.prev_seed = self.warmup.seed
        self.prev_signs = self.warmup.signs

    def next(self) -> PreparedSecond:
        t = self.next_t
        ks = spec.SecondKeystream(self.key, t, self.lookup(t), spec.round_for(t), self.prev_seed)
        ps = prepare_second(ks, self.prev_signs, self.delta)
        self.prev_seed, self.prev_signs = ks.seed, ks.signs
        self.next_t += 1
        return ps


def render(x: np.ndarray, key: bytes, t_start: int, params: StampParams, randomness_for_second=lambda t: None,
           return_parts: bool = False, ref_rms: float | None = None, gain: float | None = None,
           states: list[tuple[float, float]] | None = None):
    """Offline render through the exact real-time code path. len(x) is padded to whole seconds.
    For a mid-session section pass the logged ref_rms/gain of its first second."""
    n_sec = -(-len(x) // FS)
    xp = np.zeros(n_sec * FS)
    xp[:len(x)] = x
    seq = KeystreamSequencer(key, t_start, params.l2_delta_db if params.mode != "table" else 0.0, randomness_for_second)
    eng = Stamper(params, ref_rms=ref_rms, gain=gain)
    out = np.empty(n_sec * FS, dtype=np.float32)
    stamp = np.empty(n_sec * FS, dtype=np.float32)
    seconds = []
    for s in range(n_sec):
        sec = seq.next()
        seconds.append(sec.ks)
        if states is not None:
            states.append((eng.ref_rms, eng.gain))
        for pos in range(0, FS, BLOCK):
            i = s * FS + pos
            o, sp, _ = eng.process(xp[i:i + BLOCK], sec, pos)
            out[i:i + BLOCK] = o
            stamp[i:i + BLOCK] = sp
    if return_parts:
        return out, stamp, seconds
    return out
