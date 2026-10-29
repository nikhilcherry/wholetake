"""S2: layer-1 verifier, informed mode (we hold the venue's clean feed).

clip + key + session -> continuous pieces of venue time, the cuts between them (in seconds,
sample-precise), and per piece whether the keyed layer-1 stamp is present.

Split of work (research r118): the *speech* alignment against the feed measures where each
piece sits in venue time and so the size of every cut; the *stamp* certifies that each piece
was recorded in the room at that time. Pipeline:
 1. align   1 s clip windows against the feed at 8 kHz (band 300-3400 Hz, regularised PHAT),
            local search around the running offset, global search to (re)acquire.
 2. pieces  group windows with one offset (<= 5 ms apart), merge across pauses, fit
            feed_idx = a + (1 + b) * clip_idx per piece at 48 kHz (b = clock drift).
 3. cuts    between two pieces, pick the split sample that minimises the speech-cancellation
            residual of "left model before, right model after"; removed = offset jump there.
 4. render  re-render what the speaker played for the covered venue span (10 s warm-up).
 5. cancel  per piece: one channel estimate H (2 s+ STFT frames, ridge), residual y - H v.
 6. score   residual vs the keyed stamp passed through the same H (pa/file: the stamp left the
            same speaker, so it lands at lag 0 +- 1 ms) or unfiltered (table: +- 15 ms).
            Per 1 s window z at a leave-one-out lag; piece z on the summed correlation.
            Three derived wrong keys run through the identical scoring as a control.
"""
from __future__ import annotations

import hashlib
import os
import time
from dataclasses import dataclass, field

import numpy as np
import scipy.fft as sfft
from scipy.signal import istft, resample_poly, sosfilt, stft

from . import engine, spec
from .session import Session
from .spec import FS, L2_NBANDS, SOS_L1, SOS_L2

# --- alignment (8 kHz) ---
DEC = 6
FS8 = FS // DEC
AWIN = FS8                 # 1 s windows
AHOP = FS8 // 2            # 0.5 s hop
LOCAL_S = 3.0              # local search +- seconds around the running offset
ACQ_WIN = 3 * FS8          # window used to (re)acquire with a global search
PSR_LOCAL = 7.0            # peak / robust sigma to accept a local match that continues the offset
PSR_JUMP = 12.0            # ... or that jumps to a new offset within the local span
PSR_GLOBAL_EXTRA = 3.0     # global threshold = sqrt(2 ln N_lags) + this
OFF_TOL = int(0.005 * FS)  # windows within 5 ms belong to one piece
EDIT_MIN = int(0.004 * FS) # offset jumps smaller than 4 ms are not reported as edits
MAX_DRIFT = 300e-6
GAP_UNMATCHED = int(2.5 * FS)  # longer unmatched gaps between pieces = foreign audio

# --- stamp scoring (48 kHz) ---
SEARCH = {"pa": int(0.001 * FS), "file": int(0.001 * FS), "table": int(0.015 * FS)}
Z_WIN = 5.0
# piece z (summed correlation, max over the lag search) to call present. table searches
# +-15 ms two-sided, so its null max is higher: derived-key controls reached 5.2 there (vs 3.5 pa)
CLIP_Z_MIN = {"pa": 6.0, "file": 6.0, "table": 8.0}
CLIP_Z_STRONG = 8.5        # added to the mode offset (CLIP_Z_MIN - 6)
# a speech match must also explain the clip: genuine pieces cancel 15+ dB of speech at PSR 35+,
# false locks on unrelated audio cancelled ~2 dB at PSR 8-9
MATCH_MIN_DB = 6.0
MATCH_PSR_ALONE = 15.0
# a short piece between two same-offset pieces is a repeated phrase (merged) if the neighbours'
# alignment cancels its speech within BLIP_DB of its own alignment
BLIP_MAX_WIN, BLIP_MAX_S, BLIP_DB = 3, 2.0, 3.0
MIN_PIECE_S = 0.15         # pieces squeezed shorter than this by neighbouring cuts are dropped
WRONG_MARGIN = 2.5         # present also needs clip_z >= best wrong-key clip_z + this
N_WRONG = 3
WARMUP_S = 10


_DITHER = np.random.default_rng(0).standard_normal(FS) * 1e-9  # -180 dBFS, fixed


def _band(x: np.ndarray) -> np.ndarray:
    # digital silence drives IIR state into denormals (40x slower); a fixed tiny dither avoids it
    d = np.resize(_DITHER, len(x))
    return sosfilt(SOS_L1, x + d)


def _to8k(x: np.ndarray) -> np.ndarray:
    return resample_poly(_band(x), 1, DEC)


def _sigma(c: np.ndarray) -> float:
    return 1.4826 * float(np.median(np.abs(c - np.median(c)))) + 1e-20


def _phat_search(seg: np.ndarray, ref: np.ndarray) -> tuple[int, float]:
    """Best start of `seg` inside `ref` (8 kHz, both band-limited). Returns (index, PSR)."""
    n = len(ref)
    if n < len(seg):
        return 0, 0.0
    nfft = 1 << (n + len(seg) - 1).bit_length()
    S = sfft.rfft(seg * np.hanning(len(seg)), nfft)
    R = sfft.rfft(ref, nfft)
    X = R * S.conj()
    f = np.fft.rfftfreq(nfft, 1 / FS8)
    band = (f >= 300) & (f <= 3400)
    mag = np.abs(X)
    floor = 0.05 * np.median(mag[band]) + 1e-30
    X = np.where(band, X / np.maximum(mag, floor), 0.0)
    c = sfft.irfft(X, nfft)[:n - len(seg) + 1]
    k = int(np.argmax(c))
    return k, float(c[k] / _sigma(c))


@dataclass
class Win:
    c8: int          # clip start (8 kHz samples)
    f8: int = -1     # matched feed start (8 kHz), -1 = no match
    psr: float = 0.0
    rms: float = 0.0


def _local(seg: np.ndarray, x8: np.ndarray, c8: int, off: int) -> tuple[int, float]:
    lo = max(0, c8 - off - int(LOCAL_S * FS8))
    hi = min(len(x8), c8 - off + len(seg) + int(LOCAL_S * FS8))
    k, psr = _phat_search(seg, x8[lo:hi])
    return lo + k, psr


def align_windows(y8: np.ndarray, x8: np.ndarray, log=None) -> list[Win]:
    if len(y8) < AWIN or len(x8) < AWIN:
        return []
    wins = [Win(c8=c) for c in range(0, len(y8) - AWIN + 1, AHOP)]
    for w in wins:
        w.rms = float(np.sqrt(np.mean(y8[w.c8:w.c8 + AWIN] ** 2)))
    loud = np.percentile([w.rms for w in wins], 90) if wins else 0.0
    voiced = [w.rms >= loud * 10 ** (-30 / 20) for w in wins]
    n_glob = max(len(x8) - AWIN, 1)
    psr_glob = np.sqrt(2 * np.log(n_glob)) + PSR_GLOBAL_EXTRA
    cur = None  # running offset c8 - f8
    for w, v in zip(wins, voiced):
        if not v:
            continue  # silence / pause: no speech to align
        seg = y8[w.c8:w.c8 + AWIN]
        if cur is not None:
            f, psr = _local(seg, x8, w.c8, cur)
            # continuing the running offset needs PSR_LOCAL; a jump inside the local span needs
            # more (unrelated audio reaches PSR 8-10 somewhere in +-3 s, and a false lock would
            # drag the running offset away from where the next real match is)
            same = abs((w.c8 - f) - cur) * DEC <= OFF_TOL
            if (same and psr >= PSR_LOCAL) or psr >= PSR_JUMP:
                w.f8, w.psr, cur = f, psr, w.c8 - f
                continue
        # (re)acquire: a 3 s window has ~sqrt(3) more PSR than 1 s (a 1 s window just after a
        # long cut can sit under the global bar on table audio)
        k, psr = _phat_search(y8[w.c8:w.c8 + ACQ_WIN], x8)
        if psr >= psr_glob:
            w.f8, w.psr, cur = k, psr, w.c8 - k
    # backward pass: voiced windows left unmatched just before a matched one usually belong to
    # it (the forward pass only learned the new offset some windows after a big jump)
    nxt = None
    for w, v in zip(reversed(wins), reversed(voiced)):
        if w.f8 >= 0:
            nxt = w.c8 - w.f8
        elif v and nxt is not None:
            f, psr = _local(y8[w.c8:w.c8 + AWIN], x8, w.c8, nxt)
            if psr >= PSR_LOCAL and abs((w.c8 - f) - nxt) * DEC <= OFF_TOL:
                w.f8, w.psr = f, psr
    return wins


@dataclass
class Piece:
    c0: int              # clip sample span [c0, c1) at 48 kHz
    c1: int
    a: float             # feed_idx = a + (1 + b) * clip_idx
    b: float = 0.0
    n_win: int = 0
    psr: float = 0.0
    # filled by scoring
    stamp: dict = field(default_factory=dict)

    def f(self, c):
        return self.a + (1.0 + self.b) * np.asarray(c, dtype=np.float64)

    def off(self, c) -> float:
        return float(c - self.f(c))


def group_pieces(wins: list[Win]) -> list[list[Win]]:
    """Consecutive matched windows with one offset. A single window whose offset is not
    confirmed by its neighbour and that sits between two windows agreeing with each other is
    a straddler (window across a cut) and is dropped."""
    m = [w for w in wins if w.f8 >= 0]
    groups: list[list[Win]] = []
    for w in m:
        off = (w.c8 - w.f8) * DEC
        if groups and abs(off - (groups[-1][-1].c8 - groups[-1][-1].f8) * DEC) <= OFF_TOL:
            groups[-1].append(w)
        else:
            groups.append([w])
    # drop unconfirmed singletons (straddlers, repeated-phrase blips) unless very strong
    keep = [g for g in groups if len(g) >= 2 or g[0].psr >= 2 * PSR_LOCAL]
    # merge neighbours that ended up with the same offset (pause or dropped blip in between)
    out: list[list[Win]] = []
    for g in keep:
        if out and abs((g[0].c8 - g[0].f8) - (out[-1][-1].c8 - out[-1][-1].f8)) * DEC <= OFF_TOL:
            out[-1].extend(g)
        else:
            out.append(g)
    return out


def _fine_lag(y: np.ndarray, x: np.ndarray, c: int, f_guess: float, n: int = FS, span: int = 48) -> tuple[float, float]:
    """Sub-sample feed position of clip window [c, c+n) near f_guess (48 kHz, band-limited)."""
    f0 = int(round(f_guess)) - span
    if c < 0 or c + n > len(y) or f0 < 0 or f0 + n + 2 * span > len(x):
        return np.nan, 0.0
    seg = y[c:c + n] * np.hanning(n)
    ref = x[f0:f0 + n + 2 * span]
    nfft = 1 << (len(ref) + n).bit_length()
    X = sfft.rfft(ref, nfft) * sfft.rfft(seg, nfft).conj()
    f = np.fft.rfftfreq(nfft, 1 / FS)
    band = (f >= 300) & (f <= 3400)
    mag = np.abs(X)
    X = np.where(band, X / np.maximum(mag, 0.05 * np.median(mag[band]) + 1e-30), 0.0)
    cc = sfft.irfft(X, nfft)[:2 * span + 1]
    k = int(np.argmax(cc))
    if 0 < k < len(cc) - 1:
        d = cc[k - 1] - 2 * cc[k] + cc[k + 1]
        k = k + (0.5 * (cc[k - 1] - cc[k + 1]) / d if d < 0 else 0.0)
    return f0 + k, float(cc[int(round(k))] / _sigma(cc))


def fit_piece(g: list[Win], yb: np.ndarray, xb: np.ndarray) -> Piece:
    c = np.array([w.c8 * DEC for w in g], dtype=np.float64)
    fg = np.array([w.f8 * DEC for w in g], dtype=np.float64)
    fine = np.array([_fine_lag(yb, xb, int(ci), fi)[0] for ci, fi in zip(c, fg)])
    ok = np.isfinite(fine)
    c, fine = c[ok], fine[ok]
    if len(c) == 0:
        c, fine = np.array([g[0].c8 * DEC], float), np.array([g[0].f8 * DEC], float)
    b = 0.0
    if len(c) >= 4 and c[-1] - c[0] >= 4 * FS:
        for _ in range(3):  # least squares with outlier rejection
            A = np.vstack([np.ones_like(c), c]).T
            coef, *_ = np.linalg.lstsq(A, fine, rcond=None)
            r = fine - A @ coef
            keep = np.abs(r) <= max(3.0, 3 * 1.4826 * np.median(np.abs(r)))
            if keep.all():
                break
            c, fine = c[keep], fine[keep]
        b = float(coef[1] - 1.0)
        if abs(b) > MAX_DRIFT:
            b = 0.0
    a = float(np.median(fine - (1.0 + b) * c))
    p = Piece(c0=int(g[0].c8 * DEC), c1=min(int(g[-1].c8 * DEC + FS), len(yb)), a=a, b=b, n_win=len(g),
              psr=float(np.median([w.psr for w in g])))
    return p


# --- re-render what the speaker played -------------------------------------------------

@dataclass
class Render:
    base: int            # feed index of element 0
    feed: np.ndarray
    voice: np.ndarray    # what the speaker played minus the stamp (table: the feed itself)
    stamp: np.ndarray    # exact layer-1 component the speaker played
    wrong: list          # stamps for derived wrong keys (control)


def wrong_keys(key: bytes, n: int = N_WRONG) -> list[bytes]:
    return [hashlib.sha256(b"roomstamp-wrong|" + key + bytes([i])).digest() for i in range(n)]


def gain_track(x: np.ndarray, p: engine.StampParams, ref_rms: float | None = None,
               gain: float | None = None) -> tuple[np.ndarray, np.ndarray]:
    """Exact per-sample stamp level the emitter applied (amp x gate x output gain), from S1's
    own Stamper driven with a unit carrier, plus the output gain it applied to the voice (incl.
    howl-breaker mutes). The stamp for any key is then g_stamp * l1_key."""
    q = engine.StampParams(**{**p.as_log(), "l2_delta_db": 0.0})
    eng = engine.Stamper(q, ref_rms=ref_rms, gain=gain)
    n_sec = -(-len(x) // FS)
    xp = np.zeros(n_sec * FS)
    xp[:len(x)] = x
    one = engine.PreparedSecond(ks=None, l1=np.ones(FS, dtype=np.float32), l2_gm1=None)
    g = np.empty(n_sec * FS)
    gv = np.empty(n_sec * FS)
    B = engine.BLOCK
    for i in range(0, n_sec * FS, B):
        muted_before = getattr(eng, "mute_blocks", 0) > 0
        _, st, bs = eng.process(xp[i:i + B], one, i % FS)
        g[i:i + B] = st
        # same rule as Stamper.process: g = 0 while muted (or when the breaker just tripped)
        gv[i:i + B] = 0.0 if (muted_before or getattr(bs, "howl", False)) else getattr(eng, "gain", p.voice_gain)
    return g[:len(x)], gv[:len(x)]


def l1_carrier(key: bytes, t0: int, n: int, lookup) -> np.ndarray:
    """Keyed layer-1 noise for seconds t0.. (as float32, like the engine uses it)."""
    seq = engine.KeystreamSequencer(key, t0, 0.0, lookup)
    n_sec = -(-n // FS)
    return np.concatenate([seq.next().l1 for _ in range(n_sec)]).astype(np.float64)[:n]


def _voice(x: np.ndarray, key: bytes, t0: int, p: engine.StampParams, lookup, gv: np.ndarray) -> np.ndarray:
    """Vectorised equivalent of the engine's voice path (layer-2 wobble is linear in x)."""
    if p.mode == "table":
        return x.copy()
    v = x.copy()
    if p.l2_delta_db > 0:
        n_sec = -(-len(x) // FS)
        seq = engine.KeystreamSequencer(key, t0, p.l2_delta_db, lookup)
        gm1 = np.concatenate([seq.next().l2_gm1 for _ in range(n_sec)], axis=1)[:, :len(x)]
        xd = x + np.resize(_DITHER, len(x))
        for b in range(L2_NBANDS):
            v += gm1[b] * sosfilt(SOS_L2[b], xd)
    return v * gv


def render_span(feed: np.ndarray, sess: Session, key: bytes, f_lo: float, f_hi: float) -> Render:
    # with the emitter's logged level/gain state the render starts (almost) where it's needed;
    # without it, 10 s of warm-up let the level tracker converge (scale error < 1%)
    first = max(0, int(np.floor(f_lo / FS)) - 1)
    ref_rms, gain = sess.state_at(sess.t_start + first)
    warm = 0 if ref_rms is not None else WARMUP_S
    s0 = max(0, first - warm) * FS
    if warm and s0 > 0:
        ref_rms, gain = sess.state_at(sess.t_start + s0 // FS)
    s1 = min(len(feed), int(np.ceil(f_hi / FS) + 1) * FS)
    x = feed[s0:s1]
    t0 = sess.t_start + s0 // FS
    lookup = sess.lookup()
    p = sess.params
    g, gv = gain_track(x, p, ref_rms, gain)
    stamp = g * l1_carrier(key, t0, len(x), lookup)
    voice = _voice(x, key, t0, p, lookup, gv)
    if p.mode != "table":  # what the speaker played went through S1's output limiter
        voice = _limit(voice + stamp) - stamp
    return Render(base=s0, feed=x, voice=voice, stamp=stamp,
                  wrong=[g * l1_carrier(k, t0, len(x), lookup) for k in wrong_keys(key)])


def _limit(out: np.ndarray) -> np.ndarray:
    """Same soft limiter as engine.Stamper.process (knee 0.85, ceiling 0.98)."""
    out = out.copy()
    a = np.abs(out)
    hot = a > engine.LIMIT_KNEE
    span = engine.LIMIT_CEIL - engine.LIMIT_KNEE
    out[hot] = np.sign(out[hot]) * (engine.LIMIT_KNEE + span * np.tanh((a[hot] - engine.LIMIT_KNEE) / span))
    return out


def _warp_np(X: np.ndarray, pos: np.ndarray, a: int) -> np.ndarray:
    i0 = np.floor(pos).astype(np.int64)
    fr = pos - i0
    out = np.zeros((X.shape[0], len(pos)))
    for k in range(-a + 1, a + 1):
        t = fr - k
        w = np.sinc(t) * np.sinc(t / a)
        idx = i0 + k
        ok = (idx >= 0) & (idx < X.shape[1])
        out[:, ok] += w[ok] * X[:, idx[ok]]
    return out


try:
    from numba import njit

    @njit(cache=True)
    def _warp_nb(X, pos, a):
        m, n = X.shape
        out = np.zeros((m, pos.shape[0]))
        for j in range(pos.shape[0]):
            i0 = int(np.floor(pos[j]))
            fr = pos[j] - i0
            for k in range(-a + 1, a + 1):
                idx = i0 + k
                if idx < 0 or idx >= n:
                    continue
                t = fr - k
                if abs(t) < 1e-9:
                    w = 1.0
                else:
                    pt = np.pi * t
                    w = a * np.sin(pt) * np.sin(pt / a) / (pt * pt)
                for i in range(m):
                    out[i, j] += w * X[i, idx]
        return out
except Exception:  # numba missing: same result, ~50x slower
    _warp_nb = None


def warp(xs: list[np.ndarray], pos: np.ndarray, a: int = 8) -> list[np.ndarray]:
    """Each x (same length) evaluated at fractional positions (Lanczos-a), zero outside."""
    X = np.ascontiguousarray(np.vstack(xs), dtype=np.float64)
    pos = np.ascontiguousarray(pos, dtype=np.float64)
    out = _warp_nb(X, pos, a) if _warp_nb is not None else _warp_np(X, pos, a)
    return list(out)


# --- channel estimate / cancellation ---------------------------------------------------

def _nperseg(n: int) -> int:
    # >= 2 s frames in halls (r127b), shorter only when the piece itself is short
    nps = 1 << 16
    while nps > 4096 and nps > n // 2:
        nps //= 2
    return nps


class Channel:
    """One linear channel per piece: Y = H V (+ residual), ridge-regularised."""

    def __init__(self, y: np.ndarray, v: np.ndarray):
        self.nps = _nperseg(len(y))
        self.hop = self.nps // 2  # Hann at 50% overlap reconstructs exactly; half the work of 75%
        _, _, Y = self._stft(y)
        _, _, V = self._stft(v)
        f = np.fft.rfftfreq(self.nps, 1 / FS)
        self.band = (f >= 300) & (f <= 3400)
        Pv = (np.abs(V) ** 2).sum(axis=1)
        reg = 1e-3 * float(np.median(Pv[self.band])) + 1e-20
        self.H = (Y * V.conj()).sum(axis=1) / (Pv + reg)
        R = Y - self.H[:, None] * V
        # whitening weight from the residual's own spectrum (smoothed over ~50 Hz)
        Pr = (np.abs(R) ** 2).mean(axis=1)
        k = max(1, int(50 * self.nps / FS))
        Pr = np.convolve(Pr, np.ones(k) / k, mode="same")
        self.W = np.where(self.band, 1.0 / np.sqrt(Pr + 1e-30), 0.0)
        Ry = (np.abs(Y) ** 2).sum(axis=1)
        self.removed_db = float(10 * np.log10(Ry[self.band].sum() / ((np.abs(R) ** 2).sum(axis=1)[self.band].sum() + 1e-30)))
        self._R = R
        self._n = len(y)

    def _stft(self, x):
        return stft(x, nperseg=self.nps, noverlap=self.nps - self.hop, boundary="zeros", padded=True)

    def _istft(self, X, n):
        _, x = istft(X, nperseg=self.nps, noverlap=self.nps - self.hop, boundary=True)
        out = np.zeros(n)
        out[:min(n, len(x))] = x[:n]
        return out

    def residual_white(self) -> np.ndarray:
        return self._istft(self.W[:, None] * self._R, self._n)

    def predict(self, v: np.ndarray) -> np.ndarray:
        _, _, V = self._stft(v)
        return self._istft(self.H[:, None] * V, len(v))

    def white(self, ss: np.ndarray, through_h: bool) -> np.ndarray:
        """ss: (k, n) stamps -> whitened (and optionally channel-filtered) (k, n)."""
        _, _, S = self._stft(ss)  # (k, freq, frames)
        G = self.W * (self.H if through_h else 1.0)
        _, x = istft(G[None, :, None] * S, nperseg=self.nps, noverlap=self.nps - self.hop, boundary=True)
        out = np.zeros(ss.shape)
        m = min(ss.shape[1], x.shape[1])
        out[:, :m] = x[:, :m]
        return out


# --- stamp scoring -----------------------------------------------------------------------

def _windows(n: int) -> list[int]:
    wl = FS if n >= FS else n
    starts = list(range(0, n - wl + 1, wl))
    if n - (starts[-1] + wl) >= FS // 2:
        starts.append(n - wl)  # tail: overlapping last window rather than dropping it
    return starts, wl


def correlate_windows(res: np.ndarray, refs_ext: np.ndarray) -> np.ndarray:
    """res: whitened residual of a piece (length n). refs_ext: (k, n + 2 FS) whitened predicted
    stamps over the piece +- 1 s, so refs_ext[:, FS + i] lines up with res[i].
    Returns (k, n_windows, 2 FS + 1) correlations; index FS = zero lag."""
    n = len(res)
    starts, wl = _windows(n)
    L = wl + 2 * FS
    nfft = sfft.next_fast_len(L + wl)
    Sg = sfft.rfft(np.stack([res[s:s + wl] for s in starts]), nfft, axis=-1).conj()   # (w, f)
    out = np.empty((len(refs_ext), len(starts), 2 * FS + 1))
    for i, r in enumerate(refs_ext):  # one key at a time keeps memory ~ windows x nfft
        R = sfft.rfft(np.stack([r[s:s + L] for s in starts]), nfft, axis=-1)
        out[i] = sfft.irfft(R * Sg, nfft, axis=-1)[:, :2 * FS + 1]
    return out


def score_l1(cs: np.ndarray, search: int, signed: bool) -> dict:
    """cs: (n_windows, 2 FS + 1) correlations of one key."""
    sig = np.array([_sigma(c) for c in cs])
    lo, hi = FS - search, FS + search + 1
    total = cs.sum(axis=0)
    tz = total / _sigma(total)
    pick = (lambda a: a) if signed else np.abs
    k_all = lo + int(np.argmax(pick(tz[lo:hi])))
    zs = []
    for i, c in enumerate(cs):
        loo = total - c  # leave-one-out lag: a window can't vote for its own peak
        k = lo + int(np.argmax(pick(loo[lo:hi]))) if len(cs) > 1 else k_all
        zs.append(float(pick(np.array([c[k] / sig[i]]))[0]))
    zs = np.array(zs)
    return {"windows": len(cs), "frac_z5": float(np.mean(zs > Z_WIN)), "median_z": float(np.median(zs)),
            "clip_z": float(pick(np.array([tz[k_all]]))[0]), "stamp_lag_ms": (k_all - FS) / FS * 1000.0}


def status_of(sc: dict, wrong_max: float, mode: str = "pa") -> str:
    z, f = sc["clip_z"], sc["frac_z5"]
    zmin = CLIP_Z_MIN[mode]
    off = zmin - 6.0
    if sc["windows"] >= 3:
        present = z >= zmin and (f >= 0.20 or z >= CLIP_Z_STRONG + off)
        inconc = (z >= 4.8 + off and f >= 0.05)
    else:
        present = z >= CLIP_Z_STRONG + off
        inconc = z >= 5.5 + off
    if present and z < wrong_max + WRONG_MARGIN:
        present, inconc = False, True
    return "present" if present else ("inconclusive" if inconc else "absent")


# --- orchestration -------------------------------------------------------------------------

def _locate_cut(y: np.ndarray, rend: Render, left: Piece, right: Piece, chl: Channel, chr_: Channel) -> int:
    """Split sample between two pieces minimising the residual of left-model-then-right-model."""
    # windows are 1 s long, so the cut sits within ~1 s of either piece boundary (and the
    # boundaries can overlap when a straddling window was attributed to one side)
    lo = max(left.c0, min(left.c1, right.c0) - FS)
    hi = min(right.c1, max(left.c1, right.c0) + FS)
    if hi - lo < 64:
        return (left.c1 + right.c0) // 2
    n = hi - lo
    pad = FS  # predict over a wider span so the STFT edges don't bias the region
    nn = np.arange(lo - pad, hi + pad)
    e = []
    for p, ch in ((left, chl), (right, chr_)):
        v, = warp([rend.voice], p.f(nn) - rend.base)
        pred = ch.predict(v)[pad:pad + n]
        r = _band(y[lo:hi] - pred)
        e.append(np.convolve(r * r, np.ones(960) / 960, mode="same"))
    el, er = e
    J = np.concatenate([[0.0], np.cumsum(el)]) + (er.sum() - np.concatenate([[0.0], np.cumsum(er)]))
    return lo + int(np.argmin(J))


def _span_cancel_db(clip, rend, span: "Piece", ch: Channel, model: "Piece") -> float:
    """In-band speech cancelled (dB) over span's clip samples, predicting with `ch` and the
    alignment of `model`."""
    c0, c1 = max(0, span.c0), min(len(clip), span.c1)
    if c1 <= c0:
        return 0.0
    pad = FS
    nn = np.arange(c0 - pad, c1 + pad)
    v, = warp([rend.voice], model.f(nn) - rend.base)
    pred = ch.predict(v)[pad:pad + c1 - c0]
    y = _band(clip[c0:c1])
    r = _band(clip[c0:c1] - pred)
    return float(10 * np.log10(np.sum(y * y) / (np.sum(r * r) + 1e-30)))


def _absorb_blips(clip, rend, pieces, chans, channel):
    """A short piece sandwiched between two pieces with the same offset is either a repeated
    phrase that the aligner matched elsewhere in the venue (false splice), or a real
    substitution (foreign/other-time audio dropped in, same length). Tell them apart by
    asking whether the neighbours' alignment explains the short piece's audio as well as its
    own does: a repeat is the same words, so it cancels; a substitution doesn't."""
    i = 1
    while i < len(pieces) - 1:
        L, M, R = pieces[i - 1], pieces[i], pieces[i + 1]
        short = M.n_win <= BLIP_MAX_WIN or (M.c1 - M.c0) <= BLIP_MAX_S * FS
        if short and abs(L.off(M.c0) - R.off(M.c0)) <= OFF_TOL:
            # both alignments judged through the long neighbour's channel (a 1 s span is
            # shorter than a hall's reverb, so its own channel estimate is unreliable)
            own, nb = _span_cancel_db(clip, rend, M, chans[i - 1], M), _span_cancel_db(clip, rend, M, chans[i - 1], L)
            if nb >= MATCH_MIN_DB and nb >= own - BLIP_DB:
                L.c1 = R.c1
                L.n_win += R.n_win
                pieces[i - 1:i + 2] = [L]
                chans[i - 1:i + 2] = [channel(L)]
                continue
        i += 1
    return pieces, chans


def _split_pieces(clip, rend, pieces, chans):
    """Place each cut between consecutive pieces (from their matched spans, not from spans
    already moved by a neighbouring cut). A piece the cuts squeeze to < MIN_PIECE_S is dropped
    and the cut between its neighbours placed again."""
    spans = [(p.c0, p.c1) for p in pieces]
    while True:
        for p, (a, b) in zip(pieces, spans):
            p.c0, p.c1 = a, b
        cuts = []
        for i in range(len(pieces) - 1):
            L, R = pieces[i], pieces[i + 1]
            # straddler windows dropped around a cut leave up to ~1.5 s with no match; only a
            # longer gap is treated as audio that isn't from the venue
            cuts.append(None if R.c0 - L.c1 > GAP_UNMATCHED else _locate_cut(clip, rend, L, R, chans[i], chans[i + 1]))
        for i, k in enumerate(cuts):
            if k is not None:
                pieces[i].c1, pieces[i + 1].c0 = k, k
        for p in pieces:
            p.c0, p.c1 = max(0, p.c0), min(len(clip), p.c1)
        bad = [i for i, p in enumerate(pieces) if p.c1 - p.c0 < MIN_PIECE_S * FS]
        if not bad or len(pieces) == 1:
            return pieces, chans
        i = bad[0]
        del pieces[i], chans[i], spans[i]


def verify(clip: np.ndarray, feed: np.ndarray, sess: Session, key: bytes, log=print) -> dict:
    with sfft.set_workers(os.cpu_count() or 1):
        return _verify(clip, feed, sess, key, log)


def _verify(clip: np.ndarray, feed: np.ndarray, sess: Session, key: bytes, log=print) -> dict:
    t_run = time.time()
    tm = {}
    lap = [time.time()]

    def mark(name):
        now = time.time()
        tm[name] = round(now - lap[0], 3)
        lap[0] = now
    if spec.key_id(key) != sess.key_id:
        log(f"[s2] note: key id {spec.key_id(key)} != session key id {sess.key_id}")
    mode = sess.params.mode
    clip = np.asarray(clip, dtype=np.float64)
    feed = np.asarray(feed, dtype=np.float64)
    if clip.ndim != 1 or feed.ndim != 1:
        raise ValueError("clip and feed must be 1D arrays")
    if len(clip) < FS // 4 or len(feed) < FS // 4:
        return {"mode": mode, "key_id": spec.key_id(key), "session_key_id": sess.key_id, "venue": sess.venue,
                "clip_s": len(clip) / FS, "windows_matched": 0, "windows": 0, "stamp": "absent",
                "matched": False, "pieces": [], "edits": [], "runtime_s": time.time() - t_run,
                "verdict": "No part of this clip matches the venue's recording."}
    yb, xb = _band(clip), _band(feed)
    y8, x8 = resample_poly(yb, 1, DEC), resample_poly(xb, 1, DEC)
    mark("prep")
    wins = align_windows(y8, x8)
    mark("align")
    groups = group_pieces(wins)
    pieces = [p for p in (fit_piece(g, yb, xb) for g in groups) if p.c1 > p.c0]
    mark("fit")
    base = {"mode": mode, "key_id": spec.key_id(key), "session_key_id": sess.key_id, "venue": sess.venue,
            "clip_s": len(clip) / FS, "windows_matched": sum(w.f8 >= 0 for w in wins), "windows": len(wins)}
    if not pieces:
        base.update(stamp="absent", matched=False, pieces=[], edits=[], runtime_s=time.time() - t_run,
                    verdict="No part of this clip matches the venue's recording.")
        return base

    f_lo = min(p.f(p.c0) for p in pieces) - 2 * FS
    f_hi = max(p.f(p.c1) for p in pieces) + 2 * FS
    rend = render_span(feed, sess, key, f_lo, f_hi)
    mark("render")

    def channel(p: Piece) -> Channel:
        p.c0, p.c1 = max(0, p.c0), min(len(clip), p.c1)
        c = np.arange(p.c0, p.c1)
        return Channel(clip[p.c0:p.c1], warp([rend.voice], p.f(c) - rend.base)[0])

    # cut points between consecutive pieces, then final spans
    chans = [channel(p) for p in pieces]
    keep = [i for i, (p, ch) in enumerate(zip(pieces, chans))
            if ch.removed_db >= MATCH_MIN_DB or p.psr >= MATCH_PSR_ALONE]
    pieces, chans = [pieces[i] for i in keep], [chans[i] for i in keep]
    if not pieces:
        base.update(stamp="absent", matched=False, pieces=[], edits=[], runtime_s=time.time() - t_run,
                    verdict="No part of this clip matches the venue's recording.")
        return base
    pieces, chans = _absorb_blips(clip, rend, pieces, chans, channel)
    pieces, chans = _split_pieces(clip, rend, pieces, chans)

    mark("cuts")
    signed = mode != "table"
    search = SEARCH[mode]
    out_pieces = []
    for p in pieces:
        n = p.c1 - p.c0
        if n < FS // 4:
            p.stamp = {"windows": 0, "clip_z": 0.0, "frac_z5": 0.0, "median_z": 0.0, "status": "too short"}
            continue
        # voice, stamp and the wrong-key stamps over the piece +- 1 s, warped in one pass
        ce = np.arange(p.c0 - FS, p.c1 + FS)
        v_ext, s_ext, *w_ext = warp([rend.voice, rend.stamp, *rend.wrong], p.f(ce) - rend.base)
        ch = Channel(clip[p.c0:p.c1], v_ext[FS:FS + n])
        res = ch.residual_white()
        cs = correlate_windows(res, ch.white(np.stack([s_ext, *w_ext]), through_h=signed))
        sc = score_l1(cs[0], search, signed)
        wrong = [score_l1(c, search, signed) for c in cs[1:]]
        wmax = max(w["clip_z"] for w in wrong)
        sc["wrong_key_max_z"] = wmax
        sc["wrong_key_frac_z5"] = max(w["frac_z5"] for w in wrong)
        sc["speech_removed_db"] = ch.removed_db
        sc["status"] = status_of(sc, wmax, mode)
        p.stamp = sc

    mark("score")
    edits = []
    for i in range(len(pieces) - 1):
        L, R = pieces[i], pieces[i + 1]
        at = R.c0
        if R.c0 > L.c1:  # unmatched audio between the pieces
            edits.append({"kind": "unmatched", "clip_t": L.c1 / FS, "clip_t_end": R.c0 / FS,
                          "duration_s": (R.c0 - L.c1) / FS})
            jump = (R.f(R.c0) - L.f(L.c1)) - (R.c0 - L.c1)
        else:
            jump = R.f(at) - L.f(at)
        if abs(jump) >= EDIT_MIN:
            edits.append({"kind": "removed" if jump > 0 else "repeated", "clip_t": at / FS,
                          "removed_s": float(jump / FS), "venue_t_from": float(sess.t_start + L.f(min(at, L.c1)) / FS),
                          "venue_t_to": float(sess.t_start + R.f(at) / FS)})
    edits = _pair_replacements(edits)
    for p in pieces:
        out_pieces.append({"clip_start": p.c0 / FS, "clip_end": p.c1 / FS,
                           "venue_t_start": sess.t_start + float(p.f(p.c0)) / FS,
                           "venue_t_end": sess.t_start + float(p.f(p.c1)) / FS,
                           "offset_samples": p.off(p.c0), "drift_ppm": p.b * 1e6, "align_windows": p.n_win,
                           "align_psr": p.psr, **{f"l1_{k}": v for k, v in p.stamp.items()}})
    statuses = [p.stamp.get("status") for p in pieces]
    stamp = "present" if "present" in statuses else ("inconclusive" if "inconclusive" in statuses else "absent")
    lead = pieces[0].c0 / FS
    tail = (len(clip) - pieces[-1].c1) / FS
    base.update(stamp=stamp, matched=True, pieces=out_pieces, edits=edits, lead_in_s=lead, unmatched_tail_s=tail,
                runtime_s=time.time() - t_run, timings=tm)
    base["verdict"] = human_verdict(base)
    return base


def _pair_replacements(edits: list[dict]) -> list[dict]:
    """A jump of +d into other venue audio and a jump of -d back within a few seconds is one
    edit: that clip span was replaced with audio from d seconds away in the talk."""
    out, i = [], 0
    while i < len(edits):
        a = edits[i]
        b = edits[i + 1] if i + 1 < len(edits) else None
        if (b and a["kind"] in ("removed", "repeated") and b["kind"] in ("removed", "repeated")
                and abs(a["removed_s"] + b["removed_s"]) * FS < EDIT_MIN and b["clip_t"] - a["clip_t"] <= 3.0):
            out.append({"kind": "replaced", "clip_t": a["clip_t"], "clip_t_end": b["clip_t"],
                        "duration_s": b["clip_t"] - a["clip_t"], "source_offset_s": a["removed_s"],
                        "venue_t_from": a["venue_t_to"], "venue_t_to": a["venue_t_to"] + b["clip_t"] - a["clip_t"]})
            i += 2
            continue
        out.append(a)
        i += 1
    return out


def _fmt_clock(s: float) -> str:
    return f"{int(s // 60)}:{s % 60:05.2f}"


def _fmt_utc(t: float, tz_h: float = 5.5, tz: str = "IST") -> str:
    tt = time.gmtime(t + tz_h * 3600)
    return time.strftime("%d %b %H:%M:%S", tt) + f" {tz}"


def human_verdict(r: dict) -> str:
    if not r.get("matched"):
        return "No part of this clip matches the venue's recording."
    p0 = r["pieces"][0]
    where = f"{r['venue']}, {_fmt_utc(p0['venue_t_start'])}"
    st = r["stamp"]
    head = {"present": f"Recorded at {where}.",
            "inconclusive": f"Matches {where}; room stamp inconclusive.",
            "absent": f"Words match {where}, but the room stamp is missing (not recorded in the room, or the key is wrong)."}[st]
    ed = [e for e in r["edits"] if e["kind"] in ("removed", "repeated")]
    parts = []
    for e in ed:
        if e["kind"] == "removed":
            parts.append(f"{e['removed_s']:.2f} s removed at {_fmt_clock(e['clip_t'])}")
        else:
            parts.append(f"{-e['removed_s']:.2f} s repeated at {_fmt_clock(e['clip_t'])}")
    for e in r["edits"]:
        if e["kind"] == "replaced":
            d = e["source_offset_s"]
            parts.append(f"{e['duration_s']:.2f} s at {_fmt_clock(e['clip_t'])} replaced with audio from "
                         f"{abs(d):.1f} s {'later' if d > 0 else 'earlier'} in the talk")
        if e["kind"] == "unmatched":
            parts.append(f"{e['duration_s']:.1f} s not from this venue at {_fmt_clock(e['clip_t'])}")
    tail = (" " + ". ".join(parts) + ".") if parts else " Continuous: nothing removed."
    return head + tail.replace("..", ".")
