"""S3: blind layer-2 detector. Clip + venue key -> stamp present?, when it was recorded, confidence.

No clean feed. The clip's own band log-energies are correlated with the keyed envelope
pattern at every 10 ms position of a search window of venue time (research r131), so the
peak position *is* the recording time.

Features (per clip, 10 ms rows, sharp STFT bands at the spec's 10 band edges):
- E[t,b] = band log-energy; weights W = speech presence x per-band SNR (a re-filmed clip
  has no energy left under ~400 Hz, so those bands drop out instead of adding noise);
- a weighted 1 s high-pass (pauses don't leak into it), then the per-block band mean is
  removed (the pattern is balanced 5 up / 5 down, so loudness and AGC cancel), then a
  robust per-band scale and a +-2.5 clip, times W.
Template (per key, venue time, 10 ms): spec.l2_gain_db block-averaged (raised-cosine
included), same 1 s high-pass and band-mean removal. Reverb: the clip features are
smoothed anti-causally with exp(-k/tau), which equals correlating a causally smeared
template, so a reverberant clip still peaks at the true position.
Score: per (tempo, reverb) variant, robust z of the correlation over all lags where the
clip fits inside the window; take the max. Calibration: the same full search for N random
keys on the same clip -> "beats N/N", sigma over the null, and a Gumbel-fit p-value.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import hashlib
import hmac
import json
import os
import struct
import sys
import time
from dataclasses import dataclass, field

import numpy as np
from scipy.fft import irfft, next_fast_len, rfft
from scipy.ndimage import convolve1d, correlate1d, maximum_filter1d
from scipy.signal import stft

from . import spec, wavio
from .spec import FS, L2_EDGES, L2_FRAME, L2_FRAMES_PER_SEC, L2_NBANDS, N_TR, RC

HOP = 480                               # 10 ms analysis block
STFT_WIN = 3840                         # 80 ms band-power window (at tempo 1)
HPS = FS // HOP                         # 100 blocks per second
BPF = L2_FRAME // HOP                   # 10 blocks per pattern frame
HP_LEN = 101                            # 1 s Hann high-pass
HP_WIN = np.hanning(HP_LEN + 2)[1:-1]
CLIP_SIG = 2.5
TEMPOS = (1.0, 0.9, 1.1, 1.25, 1.5)
TAUS = (0.0, 4.0)                       # reverb energy time constants, rows (RT60 ~ 13.8 tau x 10 ms)
MAX_CUT = 300                           # rows: cuts up to 3 s of venue time
MIN_CUT = 8                             # rows: report a cut from 80 ms
MIN_CLIP_S = 8.0
MIN_PAD_S = 20.0                        # window must exceed the clip by this (robust z needs off-peak lags)
P_PRESENT = 1e-3
NULL_BATCH = 25
NULL_MIN = 50
P_LIKELY = 1e-2

# block averages of the raised-cosine ramp (fraction of the *new* sign) for the blocks it spans
_RAMP = [float(np.mean(RC[k * HOP:(k + 1) * HOP])) for k in range(N_TR // HOP)]


# ---------------------------------------------------------------- keystream / template

class RandomnessSource:
    """Per-second drand randomness: from an S1 session log, else fetched by round number."""

    def __init__(self, log: str | None = None, unanchored: bool = False, cache_path: str | None = None):
        self.by_sec: dict[int, bytes | None] = {}
        self.by_round: dict[int, bytes | None] = {}
        self.header: dict | None = None
        self.unanchored = unanchored
        self.cache_path = cache_path
        self.fetched = 0
        if log:   # S1's own reader: randomness per SECOND, only where that second was anchored
            from .cli import log_randomness, read_log
            self.header, secs = read_log(log)
            lookup = log_randomness(secs)
            self.by_sec = {int(t): lookup(int(t)) for t in secs}
        if cache_path and os.path.exists(cache_path):
            with open(cache_path, encoding="utf-8") as f:
                for k, v in json.load(f).items():
                    self.by_round.setdefault(int(k), bytes.fromhex(v))

    def log_span(self) -> tuple[int, int] | None:
        return (min(self.by_sec), max(self.by_sec) + 1) if self.by_sec else None

    def prefetch(self, t_lo: int, t_hi: int) -> None:
        if self.unanchored:
            return
        need = sorted({spec.round_for(t) for t in range(t_lo - 1, t_hi) if t not in self.by_sec}
                      - set(self.by_round))
        if not need:
            return
        from .drand import fetch_round
        with cf.ThreadPoolExecutor(16) as ex:
            for rnd, d in zip(need, ex.map(lambda r: fetch_round(r, timeout=4.0), need)):
                self.by_round[rnd] = bytes.fromhex(d["randomness"]) if d else None
                self.fetched += 1
        missing = [r for r in need if self.by_round[r] is None]
        if missing:
            print(f"warning: {len(missing)} drand rounds unavailable; those seconds assumed unanchored", file=sys.stderr)
        if self.cache_path:
            os.makedirs(os.path.dirname(os.path.abspath(self.cache_path)), exist_ok=True)
            with open(self.cache_path, "w", encoding="utf-8") as f:
                json.dump({str(k): v.hex() for k, v in self.by_round.items() if v is not None}, f)

    def get(self, t: int) -> bytes | None:
        if self.unanchored:
            return None
        if t in self.by_sec:
            return self.by_sec[t]
        return self.by_round.get(spec.round_for(t))


def signs_for(key: bytes, t_lo: int, t_hi: int, rand: list[bytes | None]) -> np.ndarray:
    """spec.l2_signs for seconds t_lo-1 .. t_hi-1 -> ((t_hi - t_lo + 1) * 10 frames, 10 bands).
    rand[i] is the randomness for second t_lo - 1 + i."""
    n = t_hi - t_lo + 1
    raw = np.empty((n, L2_FRAMES_PER_SEC * L2_NBANDS), dtype="<u4")
    zero = spec.ZERO_R
    for i, t in enumerate(range(t_lo - 1, t_hi)):
        s_t = hmac.new(key, b"roomstamp-v1|" + (rand[i] or zero) + struct.pack("<Q", t), hashlib.sha256).digest()
        raw[i] = np.frombuffer(hashlib.shake_256(b"L2|" + s_t).digest(4 * L2_NBANDS * L2_FRAMES_PER_SEC), dtype="<u4")
    raw = raw.reshape(n * L2_FRAMES_PER_SEC, L2_NBANDS).astype(np.int64)
    order = np.argsort(-raw, axis=1, kind="stable")
    signs = -np.ones(raw.shape)
    np.put_along_axis(signs, order[:, :L2_NBANDS // 2], 1.0, axis=1)
    return signs


def _hp(x: np.ndarray) -> np.ndarray:
    return x - convolve1d(x, HP_WIN / HP_WIN.sum(), axis=-2, mode="reflect")


def template(signs: np.ndarray) -> np.ndarray:
    """signs_for() output (predecessor second first) -> (window frames * 10 blocks, 10), filtered."""
    signs = signs[L2_FRAMES_PER_SEC - 1:]          # keep only the predecessor's last frame
    prev, cur = signs[:-1], signs[1:]
    T = np.repeat(cur[:, None, :], BPF, axis=1)
    for k, a in enumerate(_RAMP):
        T[:, k] = (1.0 - a) * prev + a * cur
    T = _hp(T.reshape(-1, L2_NBANDS))
    T -= T.mean(axis=1, keepdims=True)
    return T


# ---------------------------------------------------------------- clip features

def clip_band_power(x: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Band power of the clip (sharp STFT bands at the spec's edges, 80 ms Hann, 5 ms hop) and
    each frame's centre in clip samples. Not the spec's IIR filters: those overlap so much that
    neighbouring bands (opposite signs) cancel, and measuring with them loses ~1/3 of z."""
    hop = HOP // 2
    if len(x) < STFT_WIN:
        return np.zeros((0, L2_NBANDS)), np.zeros(0)
    f, _, X = stft(x, FS, window="hann", nperseg=STFT_WIN, noverlap=STFT_WIN - hop, boundary=None, padded=False)
    P = np.abs(X) ** 2
    P = np.stack([P[(f >= L2_EDGES[b]) & (f < L2_EDGES[b + 1])].sum(axis=0) for b in range(L2_NBANDS)], axis=1)
    return P, np.arange(P.shape[0]) * hop + STFT_WIN / 2


def band_power(cbp: tuple[np.ndarray, np.ndarray], n_samples: int, tempo: float = 1.0) -> np.ndarray:
    """Clip band power resampled onto the *venue* grid: row j is centred on venue block j.
    A clip sped up by `tempo` covers 10 ms of venue time in 10/tempo ms of clip time."""
    P, centres = cbp
    rows = int(n_samples * tempo // HOP)
    at = (np.arange(rows) + 0.5) * HOP / tempo
    return np.stack([np.interp(at, centres, P[:, b]) for b in range(L2_NBANDS)], axis=1)


def features(P: np.ndarray) -> np.ndarray:
    """Band power rows -> weighted, normalised features (same rows)."""
    P = np.maximum(P, P.max() * 1e-9 + 1e-20)
    E = 10.0 * np.log10(P)

    Et = 10.0 * np.log10(P.sum(axis=1))
    v = np.clip((Et - (np.percentile(Et, 95) - 25.0)) / 15.0, 0.0, 1.0)
    floor = np.percentile(E, 10, axis=0)
    u = np.clip((E - floor - 3.0) / 10.0, 0.0, 1.0)
    W = v[:, None] * u

    h = HP_WIN / HP_WIN.sum()
    num = convolve1d(E * W, h, axis=0, mode="reflect")
    den = convolve1d(W, h, axis=0, mode="reflect")
    F = E - num / (den + 1e-6)
    F -= (F * W).sum(axis=1, keepdims=True) / (W.sum(axis=1, keepdims=True) + 1e-6)
    speech = v > 0.5
    for b in range(L2_NBANDS):
        ref = np.abs(F[speech, b]) if speech.sum() > 50 else np.abs(F[:, b])
        F[:, b] /= 1.4826 * np.median(ref) + 1e-9
    return np.clip(F, -CLIP_SIG, CLIP_SIG) * W


def reverb_variant(F: np.ndarray, tau: float) -> np.ndarray:
    """G[t] = sum_k h[k] F[t+k], h[k] = exp(-k/tau): correlating G with T equals correlating F
    with T smeared causally by the room's energy decay."""
    if tau <= 0:
        return F
    k = np.arange(int(6 * tau) + 1)
    h = np.exp(-k / tau)
    h /= np.sqrt(np.sum(h * h))
    # correlate1d: G[i] = sum_j w[j] F[i + j - c], c = len(w) // 2 = K - 1, so w[K - 1 + k] = h[k]
    w = np.concatenate([np.zeros(len(k) - 1), h])
    return correlate1d(F, w, axis=0, mode="constant")


# ---------------------------------------------------------------- search

@dataclass
class Variant:
    tempo: float
    tau: float
    F: np.ndarray
    parts: list = field(default=None, repr=False)


def _robust_z(c: np.ndarray) -> np.ndarray:
    med = np.median(c)
    return (c - med) / (1.4826 * np.median(np.abs(c - med)) + 1e-12)


class Searcher:
    """Correlates clip features with a key's template at every lag where the clip fits.

    Two statistics per variant, both robust z over lags:
    - whole: the clip at one lag;
    - pair (cut-tolerant): first half at lag l, second half at l + M/2 + d, d in [0, MAX_CUT]
      (a cut removes venue time, so the second half can only move later), (zA + zB) / sqrt 2.
    A key's score is the max over variants and both statistics; random keys get exactly
    the same search, so the null absorbs the extra look-elsewhere."""

    def __init__(self, variants: list[Variant], n_blocks: int, pair: bool = True):
        self.variants = variants
        self.N = n_blocks
        self.pair = pair
        self.nfft = next_fast_len(n_blocks + max(len(v.F) for v in variants))
        for v in variants:
            ma = len(v.F) // 2
            v.parts = [np.conj(rfft(x, n=self.nfft, axis=0)) for x in (v.F, v.F[:ma], v.F[ma:])]

    def _z(self, Tf: np.ndarray, part: np.ndarray, m: int) -> np.ndarray:
        return _robust_z(irfft((Tf * part).sum(axis=1), n=self.nfft)[:self.N - m + 1])

    def scan(self, T: np.ndarray) -> list[dict]:
        Tf = rfft(T, n=self.nfft, axis=0)
        out = []
        for v in self.variants:
            m = len(v.F)
            ma = m // 2
            r = {"whole": self._z(Tf, v.parts[0], m)}
            best = float(r["whole"].max())
            if self.pair:
                za = self._z(Tf, v.parts[1], ma)[:self.N - m + 1]
                zb = self._z(Tf, v.parts[2], m - ma)[ma:]           # index l -> second half at l + ma
                fwd = maximum_filter1d(zb, MAX_CUT + 1, origin=-(MAX_CUT // 2), mode="constant", cval=-np.inf)
                r["pair"] = (za + fwd) / np.sqrt(2.0)
                r["za"], r["zb"] = za, zb
                best = max(best, float(r["pair"].max()))
            r["best"] = best
            out.append(r)
        return out


def gumbel_p(x: float, null: np.ndarray) -> float:
    mu, sd = float(np.mean(null)), float(np.std(null, ddof=1)) + 1e-12
    beta = sd * np.sqrt(6.0) / np.pi
    loc = mu - 0.5772156649 * beta
    return float(-np.expm1(-np.exp(-(x - loc) / beta)))


def _run_stage(key: bytes, null_keys: list[bytes], src: RandomnessSource, t_lo: int, t_hi: int,
               variants: list[Variant], pair: bool) -> dict:
    rand = [src.get(t) for t in range(t_lo - 1, t_hi)]
    s = Searcher(variants, (t_hi - t_lo) * HPS, pair)
    scans = s.scan(template(signs_for(key, t_lo, t_hi, rand)))
    best = max(range(len(scans)), key=lambda i: scans[i]["best"])
    score = scans[best]["best"]

    def one(k: bytes) -> float:
        return max(r["best"] for r in s.scan(template(signs_for(k, t_lo, t_hi, rand))))

    # adaptive: stop once the answer can't change (clearly absent, or far beyond every null key)
    null: list[float] = []
    with cf.ThreadPoolExecutor(min(8, os.cpu_count() or 1)) as ex:
        for i in range(0, len(null_keys), NULL_BATCH):
            null += list(ex.map(one, null_keys[i:i + NULL_BATCH]))
            if len(null) >= NULL_MIN:
                p = gumbel_p(score, np.array(null))
                if p > 0.05 or (p < 1e-6 and score > max(null)):
                    break
    return {"score": score, "variant": best, "scan": scans[best], "null": np.array(null), "searcher": s,
            "rand": rand}


def _peak(z: np.ndarray, i: int) -> float:
    """Sub-row peak position by parabolic interpolation."""
    if 0 < i < len(z) - 1:
        a, b, c = z[i - 1], z[i], z[i + 1]
        den = a - 2 * b + c
        if den < 0:
            return i + float(np.clip(0.5 * (a - c) / den, -0.5, 0.5))
    return float(i)


def locate(scan: dict, F: np.ndarray, T: np.ndarray) -> dict:
    """Clip start lag (rows, fractional) and, if the cut-tolerant fit won clearly, the cut."""
    zw = scan["whole"]
    lw = int(np.argmax(zw))
    out = {"lag": _peak(zw, lw), "cut": None}
    if "pair" not in scan:
        return out
    lp = int(np.argmax(scan["pair"]))
    zb = scan["zb"]
    d = int(np.argmax(zb[lp:lp + MAX_CUT + 1]))
    if d < MIN_CUT or scan["pair"][lp] < zw[lw] + 0.5:
        return out
    # where in the clip: per 0.5 s block, evidence for lag lp vs lp + d; best single split
    blk = HPS // 2
    nb = len(F) // blk
    ea = np.array([np.sum(F[i * blk:(i + 1) * blk] * T[lp + i * blk:lp + (i + 1) * blk]) for i in range(nb)])
    eb = np.array([np.sum(F[i * blk:(i + 1) * blk] * T[lp + d + i * blk:lp + d + (i + 1) * blk]) for i in range(nb)])
    gain = np.concatenate([[0.0], np.cumsum(ea)]) + np.concatenate([np.cumsum(eb[::-1])[::-1], [0.0]])
    k = int(np.argmax(gain))
    out.update(lag=_peak(scan["za"], lp), cut={"removed_venue_s": round(d / HPS, 2), "at_row": k * blk,
                                                 "z_first": round(float(scan["za"][lp]), 2),
                                                 "z_second": round(float(zb[lp + d]), 2)})
    return out


def detect(clip: np.ndarray, key: bytes, src: RandomnessSource, t_lo: int, t_hi: int, n_null: int = 200,
           tempos=TEMPOS, taus=TAUS, seed: int | None = None, pair: bool = True) -> dict:
    t0 = time.time()
    clip = np.nan_to_num(np.asarray(clip, dtype=np.float64), nan=0.0, posinf=0.0, neginf=0.0)
    if clip.ndim > 1:
        clip = clip.mean(axis=-1)
    n_null = max(2, int(n_null))
    dur = len(clip) / FS
    if dur < MIN_CLIP_S:
        return {"verdict": "too short", "present": False, "clip_s": round(dur, 1),
                "note": f"blind envelope check needs >= {MIN_CLIP_S:.0f} s (>= 20 s for a confident verdict)"}
    if t_lo > t_hi:
        t_lo, t_hi = t_hi, t_lo
    need = int(np.ceil(dur * max(tempos) + MIN_PAD_S))
    if t_hi - t_lo < need:
        pad = int(np.ceil((dur * max(tempos) + MIN_PAD_S - (t_hi - t_lo)) / 2))
        t_lo = max(1, t_lo - pad)
        t_hi = max(t_hi + pad, t_lo + need)
    elif t_lo < 1:
        t_lo, t_hi = 1, max(t_hi, 1 + need)
    src.prefetch(t_lo, t_hi)

    rng = np.random.default_rng(seed)
    null_keys = [rng.bytes(32) for _ in range(n_null)]
    cbp = clip_band_power(clip)
    feats = {tp: features(band_power(cbp, len(clip), tp)) for tp in tempos}

    def variants(tps):
        return [Variant(tp, tau, reverb_variant(feats[tp], tau)) for tp in tps for tau in taus]

    stage = 1
    r = _run_stage(key, null_keys, src, t_lo, t_hi, variants([tempos[0]]), pair)
    p = gumbel_p(r["score"], r["null"])
    if p > P_PRESENT and len(tempos) > 1:
        r2 = _run_stage(key, null_keys, src, t_lo, t_hi, variants(tempos), pair)
        p2 = gumbel_p(r2["score"], r2["null"])
        if p2 < p:
            r, p, stage = r2, p2, 2
    v = r["searcher"].variants[r["variant"]]
    null = r["null"]
    beats = int(np.sum(null < r["score"]))
    sigma = (r["score"] - null.mean()) / (null.std(ddof=1) + 1e-12)
    verdict = "present" if (p <= P_PRESENT and beats == len(null)) else ("likely" if p <= P_LIKELY else "absent")

    T = template(signs_for(key, t_lo, t_hi, r["rand"]))
    loc = locate(r["scan"], v.F, T)
    start = t_lo + loc["lag"] / HPS
    res = {
        "verdict": verdict, "present": verdict == "present", "layer": "envelope (L2), blind",
        "recording_start_unix": round(start, 3),
        "recording_start_utc": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(int(start)))
        + f".{min(99, int(round((start % 1) * 100))):02d}Z",
        "venue_span_s": round(len(v.F) / HPS, 1), "clip_s": round(dur, 1), "tempo": v.tempo,
        "reverb_tau_ms": v.tau * 10, "z": round(r["score"], 2), "sigma_over_null": round(float(sigma), 2),
        "beats": f"{beats}/{len(null)}", "p_value": float(f"{p:.2g}"),
        "null_mean_max": [round(float(null.mean()), 2), round(float(null.max()), 2)],
        "stage": stage, "window": [t_lo, t_hi], "key_id": spec.key_id(key),
    }
    if loc["cut"] and verdict != "absent":
        cut = loc["cut"]
        at = cut["at_row"] / HPS / v.tempo
        res["cut"] = {"removed_s": cut["removed_venue_s"], "at_clip_s": round(at, 1),
                      "near_clip_s": [round(max(0.0, at - 0.5), 1), round(min(dur, at + 0.5), 1)],
                      "z_before": cut["z_first"], "z_after": cut["z_second"]}
    res["elapsed_s"] = round(time.time() - t0, 1)
    return res


def summary(r: dict) -> str:
    if r["verdict"] == "too short":
        return f"TOO SHORT ({r['clip_s']:.1f} s): {r['note']}"
    s = (f"{r['verdict'].upper()}: recorded at {r['recording_start_utc']} (venue time, +-0.02 s), "
         f"tempo {r['tempo']}x, z {r['z']} = {r['sigma_over_null']} sigma over {r['beats'].split('/')[1]} random keys "
         f"(beats {r['beats']}, p~{r['p_value']:.1g})")
    if "cut" in r:
        s += f" | CUT: ~{r['cut']['removed_s']} s removed near clip {r['cut']['at_clip_s']} s"
    return s + f" | {r['elapsed_s']} s"


# ---------------------------------------------------------------- CLI

def _parse_time(s: str) -> int:
    if s.isdigit():
        return int(s)
    from datetime import datetime, timezone
    d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return int(d.timestamp())


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="python -m roomstamp.blind",
                                 description="S3 blind layer-2 check: clip + key -> present?, recording time, confidence")
    ap.add_argument("clip", help="WAV (16-bit PCM, any rate/channels)")
    ap.add_argument("--key-file", default="venue.key")
    ap.add_argument("--log", help="S1 session log (JSONL): exact per-second drand randomness + default window")
    ap.add_argument("--from", dest="t_from", help="window start (unix or ISO, UTC if no zone)")
    ap.add_argument("--to", dest="t_to", help="window end")
    ap.add_argument("--around", help="claimed/upload time (unix or ISO); window = around +- span/2")
    ap.add_argument("--span", type=float, default=600, help="seconds, with --around (default 600)")
    ap.add_argument("--unanchored", action="store_true", help="session ran without drand (zero randomness)")
    ap.add_argument("--null", type=int, default=200, help="random keys for calibration")
    ap.add_argument("--no-tempo", action="store_true", help="skip the speed-up search")
    ap.add_argument("--drand-cache", default=os.path.join("logs", "drand_cache.json"))
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    from .cli import load_key
    key = load_key(a.key_file)
    src = RandomnessSource(a.log, a.unanchored, a.drand_cache)
    if a.around:
        c = _parse_time(a.around)
        t_lo, t_hi = int(c - a.span / 2), int(c + a.span / 2)
    elif a.t_from and a.t_to:
        t_lo, t_hi = _parse_time(a.t_from), _parse_time(a.t_to)
    elif src.log_span():
        t_lo, t_hi = src.log_span()
        if src.header and src.header.get("key_id") not in (None, spec.key_id(key)):
            print(f"note: key_id {spec.key_id(key)} != session key_id {src.header['key_id']} (expect 'absent')",
                  file=sys.stderr)
    else:
        ap.error("give --log, --around, or --from/--to")
    if t_lo > t_hi:
        t_lo, t_hi = t_hi, t_lo
    if t_hi - t_lo > 3600:
        print(f"note: {(t_hi - t_lo) / 3600:.1f} h window; consider --around the upload time (faster, ~+1 sigma stronger)",
              file=sys.stderr)
    clip = wavio.read(a.clip)
    r = detect(clip, key, src, t_lo, t_hi, n_null=a.null, tempos=(1.0,) if a.no_tempo else TEMPOS)
    print(json.dumps(r, indent=1) if a.json else summary(r))
    sys.exit(0 if r["present"] else 1)


if __name__ == "__main__":
    main()
