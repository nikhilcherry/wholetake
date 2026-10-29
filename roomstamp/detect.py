"""Minimal *informed* detector for self-tests (the full verifier is S2).

Given a recording and the venue's clean feed + key + session params, it re-renders what
the speaker played, aligns the recording, cancels the predicted voice, and scores:
- layer 1: matched filter of the residual against the keyed noise, per 1 s window
  (z = peak within +-10 ms vs robust spread over +-1 s of lags), as in research r118;
- layer 2: band log-energy of recording minus feed, high-passed over 1 s, frame-mean
  removed, correlated with the keyed +-1 pattern; z against random balanced patterns.
Assumes a static channel and no cuts / tempo changes (that's S2's job).
"""
from __future__ import annotations

import numpy as np
from scipy.signal import fftconvolve, istft, sosfilt, stft

from . import engine, spec
from .spec import FS, L2_FRAME, L2_NBANDS, SOS_L1, SOS_L2

NPERSEG, HOP = 65536, 16384  # ~1.4 s: longer than a hall reverb tail (r127b)


def _align(rec: np.ndarray, ref: np.ndarray) -> int:
    """Lag (samples) such that rec[lag + n] ~ ref[n]."""
    if len(rec) == 0 or len(ref) == 0:
        return 0
    a = sosfilt(SOS_L1, rec)
    b = sosfilt(SOS_L1, ref)
    c = fftconvolve(a, b[::-1], mode="full")
    return int(np.argmax(np.abs(c))) - (len(b) - 1)


def _crop(rec: np.ndarray, lag: int, n: int) -> np.ndarray:
    out = np.zeros(n)
    lo, hi = max(lag, 0), min(lag + n, len(rec))
    if hi > lo:
        out[lo - lag:hi - lag] = rec[lo:hi]
    return out


def _cancel(y: np.ndarray, v: np.ndarray) -> np.ndarray:
    """Residual of y after removing the best static linear prediction from v."""
    if len(y) < 2 or len(v) < 2:
        return np.asarray(y, dtype=np.float64).copy()
    nperseg = min(NPERSEG, len(y), len(v))
    noverlap = min(NPERSEG - HOP, nperseg - max(1, nperseg // 4))
    _, _, Y = stft(y, nperseg=nperseg, noverlap=noverlap)
    _, _, V = stft(v, nperseg=nperseg, noverlap=noverlap)
    H = (Y * V.conj()).sum(axis=1, keepdims=True) / ((np.abs(V) ** 2).sum(axis=1, keepdims=True) + 1e-12)
    _, r = istft(Y - H * V, nperseg=nperseg, noverlap=noverlap)
    return r[:len(y)]


def _sigma(c: np.ndarray) -> float:
    """Robust std of a (zero-mean under H0) correlation function."""
    return 1.4826 * float(np.median(np.abs(c - np.median(c)))) + 1e-20


SEARCH = FS // 100  # +-10 ms around zero lag (voice vs stamp path difference)
CLIP_Z_MIN = 6.0    # whole-clip peak, max over 2*SEARCH+1 lags: null 99.9th pct ~4.5


def layer1(res: np.ndarray, stamp: np.ndarray) -> dict:
    """Per 1 s window: correlation over +-1 s of lags. The stamp lag is found once on the
    summed correlation (+-10 ms), then each window is scored *at that lag* (null ~ N(0,1),
    so z>5 per window has ~3e-7 false rate)."""
    n_win = len(res) // FS
    cs = []
    for w in range(n_win):
        seg = res[w * FS:(w + 1) * FS]
        a, b = (w - 1) * FS, (w + 2) * FS
        ref = np.zeros(3 * FS)
        lo, hi = max(a, 0), min(b, len(stamp))
        ref[lo - a:hi - a] = stamp[lo:hi]
        # c[k] = sum_n seg[n] * ref[n + k], k = 0..2FS  (k = FS is zero lag)
        cs.append(fftconvolve(ref, seg[::-1], mode="valid"))
    if not cs:
        return {"windows": 0, "frac_z5": 0.0, "median_z": 0.0, "clip_z": 0.0, "lag_ms": 0.0}
    cs = np.array(cs)
    total = cs.sum(axis=0)
    zt = total / _sigma(total)
    k = FS - SEARCH + int(np.argmax(np.abs(zt[FS - SEARCH:FS + SEARCH + 1])))
    zs = np.array([abs(c[k]) / _sigma(c) for c in cs])
    return {"windows": int(n_win), "frac_z5": float(np.mean(zs > 5)), "median_z": float(np.median(zs)),
            "clip_z": float(abs(zt[k])), "lag_ms": (FS - k) / FS * 1000}


def _band_logE(x: np.ndarray, n_frames: int) -> np.ndarray:
    E = np.empty((n_frames, L2_NBANDS))
    for b in range(L2_NBANDS):
        y = sosfilt(SOS_L2[b], x)[:n_frames * L2_FRAME].reshape(n_frames, L2_FRAME)
        E[:, b] = 10 * np.log10(np.mean(y * y, axis=1) + 1e-14)
    return E


def layer2(y: np.ndarray, feed: np.ndarray, signs: np.ndarray, n_null: int = 500) -> dict:
    n_frames = min(len(y), len(feed)) // L2_FRAME
    if n_frames == 0:
        return {"frames": 0, "z": 0.0}
    d = _band_logE(y, n_frames) - _band_logE(feed, n_frames)
    win = min(11, n_frames)
    k = np.ones(win) / win   # odd length: centred 1.1 s moving average
    for b in range(L2_NBANDS):
        d[:, b] -= np.convolve(d[:, b], k, mode="same")
    d -= d.mean(axis=1, keepdims=True)
    # ignore near-silent frames (no voice -> no wobble to measure)
    loud = _band_logE(feed, n_frames).max(axis=1) > -70
    d[~loud] = 0.0
    s = signs[:n_frames]
    score = float(np.sum(d * s))
    rng = np.random.default_rng(12345)
    null = np.empty(n_null)
    for i in range(0, n_null, 50):  # random balanced patterns, 50 at a time
        ranks = np.argsort(rng.random((50, n_frames, L2_NBANDS)), axis=2).argsort(axis=2)
        null[i:i + 50] = np.einsum("kfb,fb->k", np.where(ranks < L2_NBANDS // 2, 1.0, -1.0), d)[:n_null - i]
    return {"frames": int(n_frames), "z": float((score - null.mean()) / (null.std() + 1e-20))}


def detect(rec: np.ndarray, feed: np.ndarray, key: bytes, t_start: int, params: engine.StampParams,
           randomness_for_second=lambda t: None, ref_rms: float | None = None, gain: float | None = None) -> dict:
    if len(rec) == 0 or len(feed) == 0:
        result = {"lag_samples": 0, "speech_removed_db": 0.0,
                  "l1": {"windows": 0, "frac_z5": 0.0, "median_z": 0.0, "clip_z": 0.0, "lag_ms": 0.0},
                  "seconds": 0, "anchored_seconds": 0}
        if params.mode != "table" and params.l2_delta_db > 0:
            result["l2"] = {"frames": 0, "z": 0.0}
        return result
    out, stamp, secs = engine.render(feed, key, t_start, params, randomness_for_second, return_parts=True,
                                     ref_rms=ref_rms, gain=gain)
    n = len(out)
    feed_p = np.zeros(n)
    feed_p[:len(feed)] = feed
    voice = feed_p if params.mode == "table" else out.astype(np.float64) - stamp
    lag = _align(rec, voice)
    y = _crop(rec, lag, n)
    res = _cancel(y, voice)
    removed_db = 10 * np.log10(np.sum(sosfilt(SOS_L1, y) ** 2) / (np.sum(sosfilt(SOS_L1, res) ** 2) + 1e-20))
    result = {"lag_samples": lag, "speech_removed_db": float(removed_db), "l1": layer1(res, stamp.astype(np.float64)),
              "seconds": len(secs), "anchored_seconds": int(sum(s.anchored for s in secs))}
    if params.mode != "table" and params.l2_delta_db > 0:
        signs = np.concatenate([s.signs for s in secs])
        result["l2"] = layer2(y, feed_p, signs)
    return result


def verdict(r: dict) -> str:
    l1 = r["l1"]["frac_z5"] if r["l1"]["clip_z"] >= CLIP_Z_MIN else 0.0
    l1s = "present" if l1 >= 0.20 else ("inconclusive" if l1 >= 0.05 else "absent")
    out = f"L1 {l1s} ({r['l1']['frac_z5']:.0%} windows z>5, clip z {r['l1']['clip_z']:.1f})"
    if "l2" in r:
        out += f" | L2 z {r['l2']['z']:.1f} ({'present' if r['l2']['z'] > 4 else 'absent'})"
    if "anchored_seconds" in r:
        out += f" | time-anchored {r['anchored_seconds']}/{r['seconds']} s"
        if r.get("drand_checked") is not None:
            out += f" (drand-verified {r['drand_checked']})"
    return out
