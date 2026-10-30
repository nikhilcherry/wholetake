"""S5: `wt-detect CLIP` -> one detection.json. The only thing the product side calls.

    wt-detect clip.mp4 -o detection.json                 # L1 (S2) first; L2 (S3) only if L1 didn't lock
    wt-detect clip.mp4 -o detection.json --fast          # L1 only; "pending_layers": ["l2"] if L2 is worth running
    wt-detect clip.mp4 -o detection.json --layers l2 --merge   # 2nd step: run L2, merge, re-fuse in place
    wt-detect clip.mp4 --around 2026-10-31T11:42+05:30   # X bot: pass the post time (narrows the search)

Pipeline: ingest (hash, decode any format at native rate, stereo-phase check, resample to
48 kHz) -> find the S1 session(s) for this key in --venue (speech-envelope match against each
session's clean feed gives the session and the clip's rough position) -> stages, each wrapped
so a crash or hang becomes a status, never a missing file:
  l1  S2 informed verifier (l1verify.verify) on a feed slice around the match;
      falls back to S1's minimal detector (then integrity is never claimed);
  l2  S3 blind envelope detector (blind.detect), in a killable subprocess with a timeout;
      skipped for table-mode sessions (layer 2 is off there);
  wt  S4 witness matcher, if a witness record is given and the module exists.
-> fuse() turns the stage records into the verdict + headline (pure function of the JSON,
so `--merge` can re-fuse after a later stage) -> atomic write.

Contract: docs/DETECTION_JSON.md. Exit codes: 0 detection.json written with a verdict (any
verdict), 2 usage error, 3 clip unreadable, 4 internal error. Files are written in all of 0/3/4.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import multiprocessing as mp
import os
import platform
import re
import subprocess
import sys
import tempfile
import time
import traceback
from dataclasses import dataclass, field

import numpy as np
from scipy.signal import fftconvolve, resample_poly, sosfilt

from . import contract, engine, spec
from .session import Session
from .spec import FS, SOS_L1

SCHEMA = "wt-detect/1.0"
VERSION = "0.1.0"
DEFAULT_VENUE_NAME = "HH Goa demo stage"
TZ_NAME, TZ_H = "IST", 5.5

MIN_CLIP_S, MAX_CLIP_S = 2.0, 600.0
MIN_SR, MAX_SR = 4000, 384000
MAX_RESAMPLE_FACTOR = 1000
ENV_HOP = 480                 # 10 ms envelope frames for the coarse session match
ENV_Z_MIN = 6.0               # envelope peak / robust sigma to accept a session match
FULL_FEED_MAX_S = 900         # without an envelope match, hand S2 the whole feed only if it's short
FEED_SILENT_DBFS = -80.0      # a feed this quiet was never recorded (mic missing)
MAX_SESSION_TRIES = 3         # same speech can match several sessions; only the stamp decides
SLICE_MARGIN_S = 180          # feed slice margin around the matched span (cuts remove venue time)
L2_TIMEOUT_S = 150.0
L2_MAX_WINDOW_S = 2400        # cap blind L2 span (~40 min) to keep FFT memory < 1 GB
BUDGET_S = float(os.environ.get("WT_BUDGET_S", 270))   # the verdict service kills us at 300 s
L2_MIN_CLIP_S = 8.0           # S3 refuses shorter clips
L1_TIME_MISMATCH_S = 1.0
FOREIGN_MARGIN_MIN_S = 1.0    # unmatched clip lead-in / tail threshold for a foreign edit
STATUSES = ("present", "likely", "inconclusive", "absent", "too_short", "skipped", "error", "timeout", "pending")


class ClipError(Exception):
    def __init__(self, code: str, msg: str):
        super().__init__(msg)
        self.code = code


# ------------------------------------------------------------------------------- ingest

@dataclass
class Clip:
    path: str
    sha256: str
    n_bytes: int
    x: np.ndarray                 # 48 kHz mono float64
    orig_rate: int
    orig_channels: int
    codec: str
    decoder: str
    pcm_sha256: str               # of the normalised float32 LE samples
    seed: int
    warnings: list[str] = field(default_factory=list)

    @property
    def duration_s(self) -> float:
        return len(self.x) / FS


def _sha256_file(path: str) -> tuple[str, int]:
    h, n = hashlib.sha256(), 0
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
            n += len(b)
    return h.hexdigest(), n


def _decode_soundfile(path: str) -> tuple[np.ndarray, int, str]:
    import soundfile as sf
    info = sf.info(path)
    x, sr = sf.read(path, dtype="float64", always_2d=True)
    return x, int(sr), f"{info.format}/{info.subtype}".lower()


_STREAM_RE = re.compile(r"Stream #\d+:\d+.*?: Audio: ([^,\s]+).*?, (\d+) Hz, ([^,]+)")
_LAYOUT_CHANNELS = {
    "mono": 1,
    "stereo": 2,
    "downmix": 2,
    "quad": 4,
    "hexagonal": 6,
    "octagonal": 8,
    "cube": 8,
    "hexadecagonal": 16,
}


def _parse_channels(layout: str) -> int:
    base = layout.strip().lower().split("(", 1)[0].strip()
    if base in _LAYOUT_CHANNELS:
        return _LAYOUT_CHANNELS[base]
    m = re.match(r"^(\d+)\s*(?:channels?|ch)?$", base)
    if m:
        return max(1, int(m.group(1)))
    m = re.match(r"^(\d+(?:\.\d+)+)$", base)
    if m:
        return max(1, sum(int(part) for part in m.group(1).split(".")))
    return 2


def _decode_ffmpeg(path: str) -> tuple[np.ndarray, int, str]:
    """Native rate and channels, float32 (downmix and resampling are done in Python: deterministic)."""
    from .media import ffmpeg_exe
    exe = ffmpeg_exe()
    p = subprocess.run([exe, "-hide_banner", "-nostdin", "-i", path, "-map", "0:a:0",
                        "-f", "f32le", "-c:a", "pcm_f32le", "-"], capture_output=True)
    err = p.stderr.decode(errors="replace")
    if p.returncode != 0 or not p.stdout:
        if "matches no streams" in err or "does not contain any stream" in err:
            raise ClipError("NO_AUDIO_STREAM", "the file has no audio track")
        raise ClipError("DECODE_FAILED", err.strip().splitlines()[-1] if err.strip() else "ffmpeg failed")
    m = _STREAM_RE.search(err)
    if not m:
        raise ClipError("DECODE_FAILED", "could not read the audio stream's sample rate")
    codec, sr, layout = m.group(1), int(m.group(2)), m.group(3).strip()
    ch = _parse_channels(layout)
    raw = np.frombuffer(p.stdout, dtype="<f4")
    n = (len(raw) // ch) * ch
    if n == 0:
        raise ClipError("DECODE_FAILED", "decoded audio stream is empty")
    x = raw[:n].astype(np.float64).reshape(-1, ch)
    return x, sr, codec


def _downmix(x: np.ndarray, warnings: list[str], sr: int = FS) -> np.ndarray:
    """Average two channels unless they would cancel (inverted/independent mics): then keep
    the channel with more 300-3400 Hz energy. For >2 channels, pick the highest-energy speech
    channel as reference and average correlated active channels."""
    if x.shape[1] == 1:
        return x[:, 0]
    sos = SOS_L1
    if sr != FS and sr > 700:
        from scipy.signal import butter
        sos = butter(4, [300.0, min(3400.0, 0.45 * sr)], btype="bandpass", fs=sr, output="sos")
    if x.shape[1] > 2:
        n, ch = x.shape
        blk = max(1, sr // 2)
        nb = n // blk
        if nb >= 2:
            xb = x[:nb * blk].reshape(nb, blk, ch)
            e = np.sum(np.mean((xb - xb.mean(axis=1, keepdims=True)) ** 2, axis=1), axis=1)
            emax = float(np.max(e))
            if emax > 0:
                keep = np.flatnonzero(e >= 0.05 * emax)
                if len(keep) > 60:
                    top = np.argsort(e[keep])[-60:]
                    keep = np.sort(keep[top])
                xa = xb[keep].reshape(-1, ch)
            else:
                xa = x[:min(n, 10 * sr)]
        else:
            xa = x
        yb = sosfilt(sos, xa, axis=0)
        ech = np.sum(yb * yb, axis=0)
        ref = int(np.argmax(ech))
        if ech[ref] <= 0:
            return np.mean(x, axis=1)
        ref_y = yb[:, ref]
        ref_e = float(ech[ref])
        keep_ch = [ref]
        for c in range(ch):
            if c == ref or ech[c] < 0.05 * ref_e:
                continue
            rho_c = float(np.sum(ref_y * yb[:, c]) / (np.sqrt(ref_e * float(ech[c])) + 1e-20))
            if rho_c >= 0.3:
                keep_ch.append(c)
        if len(keep_ch) == 1:
            return x[:, ref].copy()
        return np.mean(x[:, keep_ch], axis=1)
    L, R = x[:, 0], x[:, 1]
    if np.allclose(L, R):
        return L.copy()
    n = len(L)
    blk = max(1, sr // 2)
    nb = n // blk
    if nb >= 2:
        Lb = L[:nb * blk].reshape(nb, blk)
        Rb = R[:nb * blk].reshape(nb, blk)
        e = np.mean((Lb - Lb.mean(axis=1, keepdims=True)) ** 2 +
                    (Rb - Rb.mean(axis=1, keepdims=True)) ** 2, axis=1)
        emax = float(np.max(e))
        if emax > 0:
            keep = np.flatnonzero(e >= 0.05 * emax)
            if len(keep) > 60:
                top = np.argsort(e[keep])[-60:]
                keep = np.sort(keep[top])
            La = Lb[keep].reshape(-1)
            Ra = Rb[keep].reshape(-1)
        else:
            La, Ra = L[:min(n, 10 * sr)], R[:min(n, 10 * sr)]
    else:
        La, Ra = L, R
    lb, rb = sosfilt(sos, La), sosfilt(sos, Ra)
    el, er = float(np.sum(lb * lb)), float(np.sum(rb * rb))
    rho = float(np.sum(lb * rb) / (np.sqrt(el * er) + 1e-20))
    if rho < 0.3:
        ch = 0 if el >= er else 1
        warnings.append(f"stereo_phase_anomaly: channels correlate {rho:+.2f}; using channel {ch} only")
        return x[:, ch].copy()
    return 0.5 * (L + R)


def ingest(path: str, max_s: float = MAX_CLIP_S) -> Clip:
    if not os.path.isfile(path):
        raise ClipError("NOT_FOUND", f"no such file: {path}")
    sha, n_bytes = _sha256_file(path)
    if n_bytes == 0:
        raise ClipError("EMPTY_FILE", "the file is empty")
    try:
        x, sr, codec = _decode_soundfile(path)
        decoder = "soundfile"
    except Exception:
        x, sr, codec = _decode_ffmpeg(path)
        decoder = "ffmpeg"
    if sr < MIN_SR or sr > MAX_SR:
        raise ClipError("DECODE_FAILED", f"unsupported sample rate: {sr} Hz")
    raw_dur = len(x) / sr
    if raw_dur < MIN_CLIP_S:
        raise ClipError("TOO_SHORT", f"clip is {raw_dur:.1f} s; need at least {MIN_CLIP_S:.0f} s")
    if raw_dur > max_s:
        raise ClipError("TOO_LONG", f"clip is {raw_dur:.0f} s; limit {max_s:.0f} s (use --max-duration)")
    if not np.all(np.isfinite(x)):
        raise ClipError("DECODE_FAILED", "non-finite audio samples")
    warnings: list[str] = []
    ch = x.shape[1]
    m = _downmix(x, warnings, sr=sr)
    if sr != FS:
        g = int(np.gcd(sr, FS))
        up, down = FS // g, sr // g
        if max(up, down) > MAX_RESAMPLE_FACTOR:
            from fractions import Fraction
            frac = Fraction(FS, sr).limit_denominator(MAX_RESAMPLE_FACTOR)
            up, down = frac.numerator, frac.denominator
        if up != down:
            m = resample_poly(m, up, down)
    dur = len(m) / FS
    if dur < MIN_CLIP_S and raw_dur < MIN_CLIP_S:
        raise ClipError("TOO_SHORT", f"clip is {dur:.1f} s; need at least {MIN_CLIP_S:.0f} s")
    if dur > max_s and raw_dur > max_s:
        raise ClipError("TOO_LONG", f"clip is {dur:.0f} s; limit {max_s:.0f} s (use --max-duration)")
    rms = float(np.sqrt(np.mean(m * m)))
    if rms < 1e-5:
        warnings.append("clip_is_silent")
    if np.mean(np.abs(m) >= 0.999) > 0.05:
        warnings.append("clip_heavily_clipped")
    pcm = m.astype("<f4")
    return Clip(path=os.path.abspath(path), sha256=sha, n_bytes=n_bytes, x=pcm.astype(np.float64), orig_rate=sr,
                orig_channels=ch, codec=codec, decoder=decoder, pcm_sha256=hashlib.sha256(pcm.tobytes()).hexdigest(),
                seed=int.from_bytes(bytes.fromhex(sha)[:4], "little"), warnings=warnings)


# ------------------------------------------------------------------------------- sessions

@dataclass
class SessionLog:
    sid: str
    path: str
    header: dict
    secs: list[dict]
    t_start: int
    t_end: int                    # exclusive
    key_id: str
    params: engine.StampParams
    feed_path: str | None
    venue_name: str
    # filled by matching
    env_z: float = 0.0
    feed_offset_s: float | None = None   # feed time of clip sample 0 (rough)
    feed_span_s: tuple[float, float] | None = None

    @property
    def mode(self) -> str:
        return self.params.mode

    @property
    def l2_on(self) -> bool:
        return self.params.mode != "table" and self.params.l2_delta_db > 0

    def dac_utc(self, t: int) -> float | None:
        i = t - self.t_start
        if 0 <= i < len(self.secs) and self.secs[i].get("t") == t:
            return self.secs[i].get("dac_utc")
        for s in self.secs:
            if s.get("t") == t:
                return s.get("dac_utc")
        return None

    def wall(self, t_nominal: float) -> float:
        """Nominal stream time -> wall clock, using the emitter's per-second DAC timestamps."""
        t_sec = int(np.floor(t_nominal))
        d = self.dac_utc(t_sec)
        if d is not None:
            return float(d + (t_nominal - t_sec))
        if self.secs:
            valid = [s for s in self.secs if s.get("dac_utc") is not None and s.get("t") is not None]
            if valid:
                nearest = min(valid, key=lambda s: abs(int(s["t"]) - t_sec))
                return float(nearest["dac_utc"]) + (t_nominal - float(nearest["t"]))
        return float(t_nominal)

    def session(self, t0_offset_s: int = 0) -> Session:
        """S2's Session for a feed slice starting t0_offset_s into the session. It carries the
        per-second log records, so S2 re-renders with exactly the randomness the emitter used
        (anchored seconds only, never fetched) and seeds the stamper from the logged state."""
        secs = {int(s["t"]): s for s in self.secs}
        if self.header.get("warmup"):
            secs.setdefault(int(self.header["warmup"]["t"]), self.header["warmup"])
        return Session(key_id=self.key_id, t_start=self.t_start + t0_offset_s, params=self.params,
                       feed=self.feed_path or "", venue=self.venue_name, secs=secs, path=None)


def load_session_log(path: str, venue_name: str) -> SessionLog:
    header, secs = None, []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(r, dict):
                continue
            if r.get("type") in ("session", "session_start"):
                if "t_start" in r and "key_id" in r and isinstance(r.get("params"), dict):
                    header = r
            elif r.get("type") == "sec":
                try:
                    r["t"] = int(r["t"])
                    secs.append(r)
                except (KeyError, TypeError, ValueError):
                    continue
    if header is None:
        raise ValueError(f"{path}: no session header")
    if header.get("spec") not in (None, spec.SPEC_VERSION):
        raise ValueError(f"{path}: spec {header.get('spec')!r}, wt-detect speaks {spec.SPEC_VERSION!r}")
    secs.sort(key=lambda s: s["t"])
    feed = header.get("feed_wav")
    feed_path = os.path.join(os.path.dirname(os.path.abspath(path)), feed) if feed else None
    if feed_path and not os.path.isfile(feed_path):
        feed_path = None
    t0 = int(header["t_start"])
    t_end = int(secs[-1]["t"]) + 1 if secs else t0
    if feed_path and not secs:
        import soundfile as sf
        t_end = t0 + int(np.ceil(sf.info(feed_path).frames / FS))
    p = engine.StampParams(**{k: v for k, v in header["params"].items() if k in engine.StampParams.__dataclass_fields__})
    return SessionLog(sid=os.path.splitext(os.path.basename(path))[0], path=os.path.abspath(path), header=header,
                      secs=secs, t_start=t0, t_end=t_end, key_id=header["key_id"], params=p, feed_path=feed_path,
                      venue_name=header.get("venue") or venue_name)


def find_sessions(venue_dir: str, venue_name: str, warnings: list[str]) -> list[SessionLog]:
    out = []
    if not os.path.isdir(venue_dir):
        return out
    for name in sorted(os.listdir(venue_dir)):
        if name.startswith("session_") and name.endswith(".jsonl"):
            try:
                out.append(load_session_log(os.path.join(venue_dir, name), venue_name))
            except Exception as e:
                warnings.append(f"session_log_unreadable: {name}: {e}")
    return out


def _env(x: np.ndarray, zi=None):
    y, zi = sosfilt(SOS_L1, x, zi=zi if zi is not None else np.zeros((SOS_L1.shape[0], 2)))
    n = len(y) // ENV_HOP
    e = np.log10(np.mean(y[:n * ENV_HOP].reshape(n, ENV_HOP) ** 2, axis=1) + 1e-12)
    return e, zi


def _feed_env(path: str) -> tuple[np.ndarray, float]:
    """Envelope of the whole feed (streamed) and its RMS in dBFS."""
    import soundfile as sf
    parts, zi, ss, n = [], None, 0.0, 0
    for blk in sf.blocks(path, blocksize=ENV_HOP * 2000, dtype="float64", always_2d=True):
        m = blk.mean(axis=1)
        ss, n = ss + float(np.sum(m * m)), n + len(m)
        e, zi = _env(m, zi)
        parts.append(e)
    rms_db = 10 * np.log10(ss / max(n, 1) + 1e-24)
    return (np.concatenate(parts) if parts else np.zeros(0)), float(rms_db)


def _hp_env(e: np.ndarray) -> np.ndarray:
    if len(e) == 0:
        return e
    active = e[e >= np.percentile(e, 95) - 3.0]
    floor = np.percentile(active if len(active) else e, 20)
    e = np.maximum(e, floor)                       # pauses -> floor, so silence doesn't dominate
    k = np.ones(101) / 101
    pad = np.pad(e, 50, mode="edge")
    return e - np.convolve(pad, k, mode="valid")


def match_feed(clip_x: np.ndarray, sl: SessionLog, warnings: list[str]) -> None:
    """Rough position of the clip in this session's feed via 10 ms log-energy envelopes."""
    if not sl.feed_path:
        return
    fe, rms_db = _feed_env(sl.feed_path)
    if rms_db < FEED_SILENT_DBFS:
        warnings.append(f"feed_silent: {sl.sid} recorded no clean feed ({rms_db:.0f} dBFS); "
                        f"informed check impossible for that session")
        sl.feed_path = None
        return
    ce = _hp_env(_env(clip_x)[0])
    fe = _hp_env(fe)
    if len(ce) < 100 or len(fe) < 100 or np.std(ce) < 1e-3:
        return
    c = fftconvolve(fe, ce[::-1], mode="full")    # index i -> clip frame 0 at feed frame i - (len(ce) - 1)
    lags = np.arange(len(c)) - (len(ce) - 1)
    overlap = np.minimum(lags + len(ce), len(fe)) - np.maximum(lags, 0)
    min_ov = max(int(MIN_CLIP_S * FS // ENV_HOP), int(0.2 * min(len(ce), len(fe))))
    c0 = np.maximum(0, -lags)
    c1 = np.minimum(len(ce), len(fe) - lags)
    f0 = np.maximum(0, lags)
    f1 = np.minimum(len(fe), lags + len(ce))
    ce_cs = np.concatenate(([0.0], np.cumsum(ce * ce)))
    fe_cs = np.concatenate(([0.0], np.cumsum(fe * fe)))
    ce_nz = (np.abs(ce) > 1e-6).astype(np.float64)
    fe_nz = (np.abs(fe) > 1e-6).astype(np.float64)
    ce_nz_cs = np.concatenate(([0.0], np.cumsum(ce_nz)))
    fe_nz_cs = np.concatenate(([0.0], np.cumsum(fe_nz)))
    n_ce = ce_nz_cs[c1] - ce_nz_cs[c0]
    n_fe = fe_nz_cs[f1] - fe_nz_cs[f0]
    ov_act = np.maximum(np.minimum(n_ce, n_fe), 1.0)
    ov_both = np.maximum(fftconvolve(fe_nz, ce_nz[::-1], mode="full"), 0.0)
    ov = np.maximum(overlap, 1)
    e_ce = ce_cs[c1] - ce_cs[c0]
    e_fe = fe_cs[f1] - fe_cs[f0]
    ok = (overlap >= min_ov) & (e_ce > 1e-6 * ov) & (e_fe > 1e-6 * ov)
    if not ok.any():
        return
    denom = np.sqrt(np.maximum(e_ce * e_fe, 1e-12))
    cv = np.where(ok, (c / denom) * np.sqrt(ov_act), np.nan)
    v = cv[ok]
    med = float(np.median(v))
    mad_sig = 1.4826 * float(np.median(np.abs(v - med)))
    alpha = np.clip((ov_both[ok] * ov_act[ok]) / np.maximum(n_ce[ok] * n_fe[ok], 1.0), 0.0, 1.0)
    mean_alpha = max(float(np.mean(alpha)), 1e-6)
    std_sig = float(np.std(v)) / np.sqrt(mean_alpha)
    vnz = v[np.abs(v) > 1e-9]
    nz_sig = float(np.std(vnz)) if len(vnz) >= 10 else 0.0
    sig = max(mad_sig, std_sig, nz_sig, 1.0) + 1e-12
    i = int(np.nanargmax(cv))
    sl.env_z = float((cv[i] - med) / sig)
    sl.feed_offset_s = float(lags[i] * ENV_HOP / FS)
    lo, hi = sl.feed_offset_s, sl.feed_offset_s + len(clip_x) / FS
    w = min(500, len(ce) // 2)
    if w >= 200 and len(fe) >= w:
        fe_w_e = np.maximum(fe_cs[w:] - fe_cs[:-w], 0.0)
        fe_w_n = np.maximum(fe_nz_cs[w:] - fe_nz_cs[:-w], 0.0)
        for sub, pad, off_s in ((ce[:w], 0.0, 0.0), (ce[-w:], w * ENV_HOP / FS, (len(ce) - w) * ENV_HOP / FS)):
            sub_e = float(np.sum(sub * sub))
            sub_n = float(np.sum(np.abs(sub) > 1e-6))
            if np.std(sub) < 1e-3 or sub_e <= 1e-6 * w or sub_n < 50:
                continue
            cs_raw = fftconvolve(fe, sub[::-1], mode="valid")
            ok_s = fe_w_e > 1e-6 * w
            if not ok_s.any():
                continue
            ov_s = np.maximum(np.minimum(sub_n, fe_w_n), 1.0)
            denom_s = np.sqrt(np.maximum(sub_e * fe_w_e, 1e-12))
            cs = np.where(ok_s, (cs_raw / denom_s) * np.sqrt(ov_s), 0.0)
            vs = cs[ok_s]
            ms = float(np.median(vs))
            mad_s = 1.4826 * float(np.median(np.abs(vs - ms)))
            ov_both_s = np.maximum(fftconvolve(fe_nz, (np.abs(sub) > 1e-6).astype(np.float64)[::-1], mode="valid"), 0.0)
            alpha_s = np.clip((ov_both_s[ok_s] * ov_s[ok_s]) / np.maximum(sub_n * fe_w_n[ok_s], 1.0), 0.0, 1.0)
            std_s = float(np.std(vs)) / np.sqrt(max(float(np.mean(alpha_s)), 1e-6))
            vsnz = vs[np.abs(vs) > 1e-9]
            nz_s = float(np.std(vsnz)) if len(vsnz) >= 10 else 0.0
            ss = max(mad_s, std_s, nz_s, 1.0) + 1e-12
            j = int(np.argmax(cs))
            z_sub = float((cs[j] - ms) / ss)
            if z_sub >= ENV_Z_MIN:
                t_sub = float(j * ENV_HOP / FS)
                if sl.env_z < ENV_Z_MIN and z_sub > sl.env_z:
                    sl.env_z = z_sub
                    sl.feed_offset_s = t_sub - off_s
                    lo, hi = sl.feed_offset_s, sl.feed_offset_s + len(clip_x) / FS
                lo, hi = min(lo, t_sub), max(hi, t_sub + pad)
    sl.feed_span_s = (lo, hi)


def _read_feed(path: str, s0: int, s1: int) -> np.ndarray:
    import soundfile as sf
    x, sr = sf.read(path, start=s0, stop=s1, dtype="float64", always_2d=True)
    if sr != FS:
        raise ValueError(f"feed {path} is {sr} Hz, expected {FS}")
    return x.mean(axis=1)


def _refine_piece_margins(clip_x: np.ndarray, feed: np.ndarray, sess_t_start: int, pieces: list[dict],
                          lead_in_s: float | None, unmatched_tail_s: float | None) -> tuple[float, float, bool]:
    """Trim coarse S2 piece boundaries when an unmatched clip lead-in or tail overlaps foreign audio."""
    lead_in = float(lead_in_s or 0.0)
    tail = float(unmatched_tail_s or 0.0)
    if not pieces or (lead_in < 0.4 and tail < 0.4):
        return lead_in, tail, False
    ce = _hp_env(_env(clip_x)[0])
    fe = _hp_env(_env(feed)[0])
    if len(ce) < 100 or len(fe) < 100:
        return lead_in, tail, False
    fps = FS / ENV_HOP
    trimmed = False
    if lead_in >= 0.4:
        p0 = pieces[0]
        c0_s, c1_s = float(p0["clip_start"]), float(p0["clip_end"])
        vt0_s = float(p0["venue_t_start"])
        lag_f = int(round(((vt0_s - sess_t_start) - c0_s) * fps))
        i0 = min(len(ce) - 1, max(0, int(round((c0_s + min(2.0, 0.25 * (c1_s - c0_s))) * fps))))
        i1 = min(len(ce), max(i0 + 20, int(round((c1_s - min(1.0, 0.15 * (c1_s - c0_s))) * fps))))
        j0 = max(0, min(len(fe) - 20, i0 + lag_f))
        j1 = max(j0 + 20, min(len(fe), i1 + lag_f))
        rms_c = float(np.sqrt(np.mean(ce[i0:i1] ** 2)) + 1e-9)
        rms_f = float(np.sqrt(np.mean(fe[j0:j1] ** 2)) + 1e-9)
        k_lo = max(0, -lag_f, int(round((c0_s - 0.5) * fps)))
        k_hi = min(len(ce), len(fe) - lag_f, int(round(min(c1_s - 1.0, c0_s + 3.0) * fps)))
        if k_hi - k_lo >= 20:
            u = ce[k_lo:k_hi] / rms_c
            v = fe[k_lo + lag_f:k_hi + lag_f] / rms_f
            cs = np.concatenate(([0.0], np.cumsum(u * v - 0.25)))
            new_k0 = k_lo + int(np.argmin(cs))
            orig_k0 = max(k_lo, int(round(c0_s * fps)))
            if new_k0 > orig_k0 + 10:
                tu = ce[orig_k0:new_k0] / rms_c
                tv = fe[orig_k0 + lag_f:new_k0 + lag_f] / rms_f
                if float(np.mean(tu * tv)) < 0.35:
                    new_c0 = new_k0 / fps
                    delta = new_c0 - c0_s
                    p0["clip_start"] = round(new_c0, 4)
                    p0["venue_t_start"] = round(vt0_s + delta, 4)
                    lead_in = float(p0["clip_start"])
                    trimmed = True
    if tail >= 0.4:
        p_last = pieces[-1]
        c0_s, c1_s = float(p_last["clip_start"]), float(p_last["clip_end"])
        vt1_s = float(p_last["venue_t_end"])
        lag_f = int(round(((vt1_s - sess_t_start) - c1_s) * fps))
        i0 = min(len(ce) - 1, max(0, int(round((c0_s + min(1.0, 0.15 * (c1_s - c0_s))) * fps))))
        i1 = min(len(ce), max(i0 + 20, int(round((c1_s - min(2.0, 0.25 * (c1_s - c0_s))) * fps))))
        j0 = max(0, min(len(fe) - 20, i0 + lag_f))
        j1 = max(j0 + 20, min(len(fe), i1 + lag_f))
        rms_c = float(np.sqrt(np.mean(ce[i0:i1] ** 2)) + 1e-9)
        rms_f = float(np.sqrt(np.mean(fe[j0:j1] ** 2)) + 1e-9)
        k_lo = max(0, -lag_f, int(round(max(c0_s + 1.0, c1_s - 3.0) * fps)))
        k_hi = min(len(ce), len(fe) - lag_f, int(round((c1_s + 0.5) * fps)))
        if k_hi - k_lo >= 20:
            u = ce[k_lo:k_hi] / rms_c
            v = fe[k_lo + lag_f:k_hi + lag_f] / rms_f
            cs = np.concatenate(([0.0], np.cumsum(u * v - 0.25)))
            new_k1 = k_lo + int(np.argmax(cs))
            orig_k1 = min(k_hi, int(round(c1_s * fps)))
            if new_k1 < orig_k1 - 10:
                tu = ce[new_k1:orig_k1] / rms_c
                tv = fe[new_k1 + lag_f:orig_k1 + lag_f] / rms_f
                if float(np.mean(tu * tv)) < 0.35:
                    new_c1 = new_k1 / fps
                    delta = c1_s - new_c1
                    p_last["clip_end"] = round(new_c1, 4)
                    p_last["venue_t_end"] = round(vt1_s - delta, 4)
                    tail = max(0.0, float(len(clip_x) / FS - p_last["clip_end"]))
                    trimmed = True
    return lead_in, tail, trimmed


# ------------------------------------------------------------------------------- stages

def _stage(status: str, impl: str | None = None, summary: str = "", **kw) -> dict:
    assert status in STATUSES, status
    d = {"status": status, "impl": impl, "elapsed_s": None, "summary": summary, "z": None, "p_value": None,
         "recorded_at_unix": None, "venue_t_start": None, "venue_t_end": None, "tempo": None,
         "content_match": None, "edits": [], "detail": None, "error": None}
    d.update(kw)
    return d


def _jsonable(o):
    if isinstance(o, dict):
        return {str(k): _jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_jsonable(v) for v in o]
    if isinstance(o, (np.floating, float)):
        f = float(o)
        return f if np.isfinite(f) else None
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.bool_):
        return bool(o)
    if isinstance(o, np.ndarray):
        return _jsonable(o.tolist())
    if isinstance(o, bytes):
        return o.hex()
    return o


def run_l1(clip: Clip, sl: SessionLog, key: bytes, warnings: list[str]) -> tuple[dict, dict | None]:
    """Returns (stage record, slice info for restored audio / proof)."""
    t = time.time()
    if not sl.feed_path:
        return _stage("skipped", summary="session has no clean feed recording"), None
    import soundfile as sf
    n_feed = sf.info(sl.feed_path).frames
    if sl.env_z >= ENV_Z_MIN and sl.feed_offset_s is not None:
        margin = SLICE_MARGIN_S + 0.5 * clip.duration_s
        lo, hi = sl.feed_span_s or (sl.feed_offset_s, sl.feed_offset_s + clip.duration_s)
        s0 = max(0, int(np.floor(lo - margin)))
        s1 = min(int(np.ceil(n_feed / FS)), int(np.ceil(hi + margin)))
    elif n_feed / FS <= FULL_FEED_MAX_S:
        s0, s1 = 0, int(np.ceil(n_feed / FS))
    else:
        return _stage("absent", summary="the clip's speech doesn't match this session's recording",
                      content_match=False), None
    feed = _read_feed(sl.feed_path, s0 * FS, min(n_feed, s1 * FS))
    sess = sl.session(s0)
    sl_info = {"s0": s0, "t0": sl.t_start + s0, "feed": feed}
    try:
        from . import l1verify
    except Exception as e:  # S2 missing/broken: S1's minimal detector, integrity never claimed
        warnings.append(f"l1_fallback: S2 unavailable ({type(e).__name__}: {e}); using S1 minimal detector")
        return _run_l1_minimal(clip, sess, key, feed, t), sl_info
    r = l1verify.verify(clip.x, feed, sess, key, log=lambda *a, **k: None)
    pieces = r.get("pieces") or []
    lead_in_s = float(r.get("lead_in_s") or 0.0)
    unmatched_tail_s = float(r.get("unmatched_tail_s") or 0.0)
    if pieces:
        lead_in_s, unmatched_tail_s, trimmed = _refine_piece_margins(
            clip.x, feed, sess.t_start, pieces, lead_in_s, unmatched_tail_s
        )
        if trimmed and (lead_in_s >= FOREIGN_MARGIN_MIN_S or unmatched_tail_s >= FOREIGN_MARGIN_MIN_S):
            sub_s0 = max(0, int(round(pieces[0]["clip_start"] * FS)))
            sub_s1 = min(len(clip.x), int(round(pieces[-1]["clip_end"] * FS)))
            if sub_s1 - sub_s0 >= int(MIN_CLIP_S * FS):
                r_sub = l1verify.verify(clip.x[sub_s0:sub_s1], feed, sess, key, log=lambda *a, **k: None)
                if r_sub.get("matched") and r_sub.get("pieces") and (
                    L1_RANK.get(r_sub.get("stamp"), 0) >= L1_RANK.get(r.get("stamp"), 0)
                ):
                    sub_off = sub_s0 / FS
                    for p in r_sub["pieces"]:
                        p["clip_start"] = round(float(p["clip_start"]) + sub_off, 4)
                        p["clip_end"] = round(float(p["clip_end"]) + sub_off, 4)
                    for e in r_sub.get("edits", []):
                        e["clip_t"] = round(float(e["clip_t"]) + sub_off, 4)
                        if e.get("clip_t_end") is not None:
                            e["clip_t_end"] = round(float(e["clip_t_end"]) + sub_off, 4)
                    r = r_sub
                    pieces = r["pieces"]
                    lead_in_s = float(pieces[0]["clip_start"])
                    unmatched_tail_s = max(0.0, float(clip.duration_s - pieces[-1]["clip_end"]))
                    r["lead_in_s"] = round(lead_in_s, 4)
                    r["unmatched_tail_s"] = round(unmatched_tail_s, 4)
    edits = []
    if pieces and lead_in_s >= FOREIGN_MARGIN_MIN_S:
        p0 = pieces[0]
        edits.append({
            "kind": "foreign", "clip_t": 0.0, "clip_t_range": [0.0, round(lead_in_s, 3)],
            "removed_s": None, "duration_s": round(lead_in_s, 3),
            "venue_t_from": p0["venue_t_start"], "venue_t_to": p0["venue_t_start"],
            "source_offset_s": None, "precision_s": 0.5, "source": "l1",
        })
    for e in r.get("edits", []):
        kind = {"removed": "removed", "repeated": "repeated", "unmatched": "foreign"}.get(e["kind"], e["kind"])
        ed = {"kind": kind, "clip_t": e["clip_t"], "clip_t_range": [e["clip_t"], e.get("clip_t_end", e["clip_t"])],
              "removed_s": e.get("removed_s"), "duration_s": e.get("duration_s"),
              "venue_t_from": e.get("venue_t_from"), "venue_t_to": e.get("venue_t_to"),
              "source_offset_s": e.get("source_offset_s"),
              "precision_s": 0.002 if kind in ("removed", "repeated") else 0.5, "source": "l1"}
        edits.append(ed)
    if pieces and unmatched_tail_s >= FOREIGN_MARGIN_MIN_S:
        p_last = pieces[-1]
        c_end = round(float(p_last["clip_end"]), 3)
        edits.append({
            "kind": "foreign", "clip_t": c_end, "clip_t_range": [c_end, round(clip.duration_s, 3)],
            "removed_s": None, "duration_s": round(unmatched_tail_s, 3),
            "venue_t_from": p_last["venue_t_end"], "venue_t_to": p_last["venue_t_end"],
            "source_offset_s": None, "precision_s": 0.5, "source": "l1",
        })
    rec_at = vt0 = vt1 = None
    if pieces:
        p0 = pieces[0]
        rec_at = p0["venue_t_start"] if lead_in_s >= FOREIGN_MARGIN_MIN_S else (p0["venue_t_start"] - p0["clip_start"])
        vt0, vt1 = min(p["venue_t_start"] for p in pieces), max(p["venue_t_end"] for p in pieces)
    zs = [p.get("l1_clip_z") for p in pieces if p.get("l1_clip_z") is not None]
    status = r["stamp"] if r.get("matched") else "absent"
    st = _stage(status, "s2", r.get("verdict", ""), z=max(zs) if zs else None, recorded_at_unix=rec_at,
                venue_t_start=vt0, venue_t_end=vt1, content_match=bool(r.get("matched")), edits=edits,
                integrity_checked=True,
                lead_in_s=round(lead_in_s, 4) if pieces else r.get("lead_in_s"),
                unmatched_tail_s=round(unmatched_tail_s, 4) if pieces else r.get("unmatched_tail_s"),
                detail=r)
    st["elapsed_s"] = round(time.time() - t, 2)
    return st, sl_info


def _run_l1_minimal(clip: Clip, sess: Session, key: bytes, feed: np.ndarray, t: float) -> dict:
    from . import detect
    r = detect.detect(clip.x, feed, key, sess.t_start, sess.params, sess.lookup())
    l1 = r["l1"]
    frac = l1["frac_z5"] if l1["clip_z"] >= detect.CLIP_Z_MIN else 0.0
    status = "present" if frac >= 0.20 else ("inconclusive" if frac >= 0.05 else "absent")
    rec_at = sess.t_start - r["lag_samples"] / FS
    st = _stage(status, "s1-minimal", detect.verdict(r), z=l1["clip_z"], recorded_at_unix=rec_at,
                venue_t_start=rec_at, venue_t_end=rec_at + clip.duration_s,
                content_match=r["speech_removed_db"] > 6.0, integrity_checked=False, detail=r)
    st["elapsed_s"] = round(time.time() - t, 2)
    return st


def _l2_worker(q, kw: dict) -> None:
    try:
        if os.environ.get("WT_TEST_L2_SLEEP"):
            time.sleep(float(os.environ["WT_TEST_L2_SLEEP"]))
        from . import blind
        x = np.load(kw["clip_npy"])
        src = blind.RandomnessSource(kw["log"], kw["unanchored"], kw["cache"])
        if not kw.get("allow_network"):   # contract: zero network; unlogged seconds count as unanchored
            src.prefetch = lambda *a, **k: None
        r = blind.detect(x, kw["key"], src, kw["t_lo"], kw["t_hi"], seed=kw["seed"])
        q.put(("ok", _jsonable(r)))
    except BaseException:
        q.put(("error", traceback.format_exc()))


def run_l2(clip: Clip, sl: SessionLog | None, key: bytes, window: tuple[int, int], work: str,
           timeout_s: float, allow_network: bool = False) -> dict:
    t = time.time()
    if clip.duration_s < L2_MIN_CLIP_S:
        return _stage("too_short", "s3", f"blind envelope check needs >= {L2_MIN_CLIP_S:.0f} s of clip")
    npy = os.path.join(work, "clip48k.npy")
    if not os.path.exists(npy):
        np.save(npy, clip.x)
    unanch = sl is not None and not any(s.get("anchored") for s in sl.secs)
    log_path = None
    if sl is not None:
        log_path = os.path.join(work, f"{sl.sid}.jsonl")
        with open(log_path, "w", encoding="utf-8") as f:
            f.write(json.dumps(sl.header) + "\n")
            for s in sl.secs:
                f.write(json.dumps(s) + "\n")
    kw = {"clip_npy": npy, "key": key, "log": log_path, "unanchored": unanch,
          "cache": os.path.join(work, "drand_cache.json"), "t_lo": int(window[0]), "t_hi": int(window[1]),
          "seed": clip.seed, "allow_network": allow_network}
    ctx = mp.get_context("spawn")
    q = ctx.Queue()
    proc = ctx.Process(target=_l2_worker, args=(q, kw), daemon=True)
    proc.start()
    res = None
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            res = q.get(timeout=0.25)
            break
        except Exception:
            if not proc.is_alive():
                try:
                    res = q.get(timeout=0.5)
                except Exception:
                    res = ("error", f"L2 worker exited with code {proc.exitcode}")
                break
    if res is None:
        proc.kill()
        proc.join(5)
        return _stage("timeout", "s3", f"L2 did not finish in {timeout_s:.0f} s", elapsed_s=round(time.time() - t, 2))
    proc.join(5)
    if res[0] != "ok":
        return _stage("error", "s3", "L2 crashed", error=res[1][-2000:], elapsed_s=round(time.time() - t, 2))
    r = res[1]
    status = {"present": "present", "likely": "likely", "absent": "absent", "too short": "too_short"}.get(
        r.get("verdict"), "error")
    edits = []
    if r.get("cut"):
        a, b = r["cut"]["near_clip_s"]
        edits.append({"kind": "removed" if r["cut"]["removed_s"] > 0 else "repeated", "clip_t": 0.5 * (a + b),
                      "clip_t_range": [a, b], "removed_s": r["cut"]["removed_s"], "duration_s": None,
                      "venue_t_from": None, "venue_t_to": None, "precision_s": 0.1, "source": "l2"})
    tempo = r.get("tempo")
    rec = r.get("recording_start_unix")
    from . import blind
    st = _stage(status, "s3", blind.summary(r) if "verdict" in r else "", z=r.get("z"), p_value=r.get("p_value"),
                recorded_at_unix=rec, venue_t_start=rec,
                venue_t_end=(rec + clip.duration_s * (tempo or 1.0)) if rec is not None else None,
                tempo=tempo, edits=edits, sigma_over_null=r.get("sigma_over_null"), beats=r.get("beats"),
                window=[int(window[0]), int(window[1])], detail=r)
    st["elapsed_s"] = round(time.time() - t, 2)
    return st


WT_KIND = {"cut": "removed", "pause_trim": "pause_trim", "splice": "reordered", "insertion": "inserted",
           "replacement": "replaced", "foreign": "foreign"}


def run_wt(clip: Clip, witness: str | None, warnings: list[str]) -> dict:
    """S4 adapter. Expected module API (see docs/DETECTION_JSON.md):
    roomstamp.witness.wt_stage(clip_x48k, witness_path) -> {status, summary, edits[], recorded_at_unix,
    venue_t_start, venue_t_end, transcript?, ...}"""
    if not witness:
        return _stage("skipped", summary="no witness record given")
    try:
        from . import witness as wmod
    except Exception:
        return _stage("skipped", summary="S4 witness matcher not installed yet")
    if "WHOLETAKE_ASR" not in os.environ:   # never start a 1.6 GB model download mid-demo
        os.environ["WHOLETAKE_ASR"] = "none"
        warnings.append("wt_asr_off: WHOLETAKE_ASR unset, ran the witness matcher acoustic-only "
                        "(no restored words); set WHOLETAKE_ASR to enable transcripts")
    t = time.time()
    r = wmod.wt_stage(clip.x, witness)
    status = r.get("status", "error")
    status = status if status in STATUSES else "error"
    # S4 reports venue_t_* as seconds into the witness record; the contract wants unix time
    rec = r.get("recorded_at_unix")
    t0 = rec - r["venue_t_start"] if rec is not None and r.get("venue_t_start") is not None else None
    vt0 = t0 + r["venue_t_start"] if t0 is not None else None
    vt1 = t0 + r["venue_t_end"] if t0 is not None and r.get("venue_t_end") is not None else None
    st = _stage(status, "s4", r.get("summary", ""), detail={k: v for k, v in r.items() if k != "match"},
                recorded_at_unix=rec, venue_t_start=vt0, venue_t_end=vt1,
                witness_t_range=[r.get("venue_t_start"), r.get("venue_t_end")], wt_verdict=r.get("verdict"))
    wt_edits = []
    for e in r.get("edits", []):
        k = WT_KIND.get(e.get("kind"), e.get("kind"))
        rng = e.get("clip_t_range") or [e.get("clip_t", 0.0), e.get("clip_t", 0.0)]
        dur = e.get("duration_s")
        if dur is None and len(rng) == 2 and rng[1] is not None and rng[0] is not None and rng[1] > rng[0]:
            dur = round(float(rng[1] - rng[0]), 3)
        ed = {**e, "kind": k, "wt_kind": e.get("kind"),
              "source": "wt", "precision_s": e.get("precision_s", 0.05),
              "restored_text": e.get("restored_text") or None,
              "removed_s": e.get("removed_s") if k in ("removed", "repeated", "pause_trim") else None}
        if dur is not None or k in ("foreign", "replaced"):
            ed["duration_s"] = dur
        if e.get("source_offset_s") is not None or k == "replaced":
            ed["source_offset_s"] = e.get("source_offset_s")
        wt_edits.append(ed)
    st["edits"] = wt_edits
    st["content_match"] = status in ("present", "likely")
    st["integrity_checked"] = st["content_match"]
    st["transcript"] = r.get("transcript")
    st["elapsed_s"] = round(time.time() - t, 2)
    return st


# ------------------------------------------------------------------------------- fusion

def fmt_time(t: float) -> str:
    tt = time.gmtime(round(t) + TZ_H * 3600)
    return time.strftime("%d %b %Y, %H:%M:%S", tt) + f" {TZ_NAME}"


def iso_time(t: float) -> str:
    t = round(t, 3)
    tt = time.gmtime(np.floor(t) + TZ_H * 3600)
    ms = int(round((t - np.floor(t)) * 1000)) % 1000
    sign = "+" if TZ_H >= 0 else "-"
    hh, mm = int(abs(TZ_H)), int(round((abs(TZ_H) % 1) * 60))
    return time.strftime("%Y-%m-%dT%H:%M:%S", tt) + f".{ms:03d}{sign}{hh:02d}:{mm:02d}"


def fmt_clock(s: float) -> str:
    s = int(round(max(0.0, s)))
    return f"{s // 60}:{s % 60:02d}"


def _kinds_compatible(k1: str | None, k2: str | None) -> bool:
    if k1 == k2:
        return True
    for group in ({"removed", "reordered"}, {"foreign", "inserted", "replaced"}):
        if k1 in group and k2 in group:
            return True
    return False


def merge_edits(layers: dict) -> tuple[list[dict], list[str]]:
    """Precise sources win; a coarser edit near a precise one is the same edit."""
    warns = []
    rank = {"l1": 0, "wt": 1, "l2": 2}
    integrity_covered = any(
        (layers.get(k) or {}).get("integrity_checked") and (layers.get(k) or {}).get("content_match")
        for k in ("l1", "wt")
    )
    l1_st = layers.get("l1") or {}
    l2_st = layers.get("l2") or {}
    l1r, l2r = l1_st.get("recorded_at_unix"), l2_st.get("recorded_at_unix")
    l2_time_mismatch = (
        l1_st.get("status") in ("present", "inconclusive")
        and l2_st.get("status") in ("present", "likely")
        and l1r is not None and l2r is not None
        and abs(l1r - l2r) > L1_TIME_MISMATCH_S
    )
    pool = []
    for name in ("l1", "wt", "l2"):
        st = layers.get(name) or {}
        if st.get("status") in ("present", "likely", "inconclusive", "absent") and st.get("edits"):
            if name == "l2" and (st.get("status") not in ("present", "likely") or integrity_covered or l2_time_mismatch):
                continue
            if name in ("l1", "wt") and not st.get("content_match"):
                continue
            pool.extend(e for e in st["edits"] if e.get("kind") != "pause_trim")
    pool.sort(key=lambda e: (rank.get(e.get("source"), 9), e.get("clip_t", 0)))
    out: list[dict] = []
    for e in pool:
        r_e = e.get("clip_t_range")
        lo = r_e[0] if (r_e and len(r_e) >= 2 and r_e[0] is not None) else e["clip_t"]
        hi = r_e[1] if (r_e and len(r_e) >= 2 and r_e[1] is not None) else e["clip_t"]
        dup = None
        for k in out:
            r_k = k.get("clip_t_range")
            k_lo = r_k[0] if (r_k and len(r_k) >= 2 and r_k[0] is not None) else k["clip_t"]
            k_hi = r_k[1] if (r_k and len(r_k) >= 2 and r_k[1] is not None) else k["clip_t"]
            if (
                rank[k["source"]] < rank[e["source"]]
                and _kinds_compatible(k.get("kind"), e.get("kind"))
                and lo - 2.0 <= k_hi and k_lo <= hi + 2.0
            ):
                dup = k
                break
        if dup is None:
            out.append(dict(e))
            continue
        if e["source"] == "wt" and e.get("restored_text") and not dup.get("restored_text"):
            dup["restored_text"] = e["restored_text"]
        if e.get("removed_s") is not None and dup.get("removed_s") is not None \
                and abs(e["removed_s"] - dup["removed_s"]) > max(0.15, 3 * e.get("precision_s", 0.1)):
            warns.append(f"edit_size_mismatch: {dup['source']} says {dup['removed_s']:.2f} s, "
                         f"{e['source']} says {e['removed_s']:.2f} s near {fmt_clock(dup['clip_t'])}; using {dup['source']}")
    out.sort(key=lambda e: e["clip_t"])
    for e in out:
        e.setdefault("restored_text", None)
        e.setdefault("restored_audio", None)
    return out, warns


def _edit_summary(edits: list[dict]) -> str:
    cuts = [e for e in edits if e["kind"] == "removed"]
    other = [e for e in edits if e["kind"] not in ("removed", "pause_trim")]
    parts = []
    if len(cuts) == 1:
        e = cuts[0]
        rem = float(e.get("removed_s") or 0.0)
        if e.get("source") == "l2":
            a, b = e["clip_t_range"]
            parts.append(f"About {rem:.1f} s removed between {fmt_clock(a)} and {fmt_clock(b)}")
        else:
            parts.append(f"{rem:.2f} s removed at {fmt_clock(e['clip_t'])}")
    elif cuts:
        tot = sum(float(e.get("removed_s") or 0.0) for e in cuts)
        parts.append(f"{len(cuts)} cuts ({tot:.2f} s removed in total, first at {fmt_clock(cuts[0]['clip_t'])})")
    for e in other:
        if e["kind"] == "repeated":
            rem = float(e.get("removed_s") or 0.0)
            parts.append(f"{-rem:.2f} s repeated at {fmt_clock(e['clip_t'])}")
        elif e["kind"] == "replaced" and e.get("source_offset_s") is not None:
            off = e["source_offset_s"]
            parts.append(f"{(e.get('duration_s') or 0):.2f} s at {fmt_clock(e['clip_t'])} replaced with audio from "
                         f"{abs(off):.1f} s {'later' if off > 0 else 'earlier'} in the talk")
        elif e["kind"] == "foreign":
            parts.append(f"{(e.get('duration_s') or 0):.1f} s not from this venue at {fmt_clock(e['clip_t'])}")
        else:
            parts.append(f"{e['kind']} at {fmt_clock(e['clip_t'])}")
    return ". ".join(parts)


def fuse(doc: dict) -> dict:
    """Verdict from the stage records only (so --merge can re-run it). Never 'verified' without
    a stamp layer at 'present'; never 'intact' unless an integrity-checking layer covered it."""
    L = doc["layers"]
    s = {k: (L.get(k) or {}).get("status", "skipped") for k in ("l1", "l2", "wt")}
    clip_s = doc["clip"]["duration_s"]
    venue = doc["verdict"]["venue"]
    vname = venue.get("name") or DEFAULT_VENUE_NAME
    warns = list(doc.get("warnings_base", []))
    pending = [k for k in ("l1", "l2", "wt") if s[k] == "pending"]

    edits, w = merge_edits(L)
    warns += w
    # where & when: L1 (sample precise) > L2 (+-20 ms) > witness; stamp-locked layers beat content_match fallbacks
    rec, prec, src = None, None, None
    for allow_content in (False, True):
        for k, p in (("l1", 0.01), ("l2", 0.02), ("wt", 0.1)):
            st = L.get(k) or {}
            ok = (
                st.get("status") in ("present", "likely", "inconclusive")
                if not allow_content
                else (k != "l2" and st.get("content_match"))
            )
            if ok and st.get("recorded_at_unix") is not None:
                rec, prec, src = st["recorded_at_unix"], p, k
                break
        if rec is not None:
            break
    l1r, l2r = (L.get("l1") or {}).get("recorded_at_unix"), (L.get("l2") or {}).get("recorded_at_unix")
    l2_time_mismatch = (
        s["l1"] in ("present", "inconclusive")
        and s["l2"] in ("present", "likely")
        and l1r is not None and l2r is not None
        and abs(l1r - l2r) > L1_TIME_MISMATCH_S
    )
    if l2_time_mismatch:
        warns.append(f"l2_time_mismatch: L2 placed the clip {l2r - l1r:+.1f} s from L1; using L1")
    wall = rec
    if rec is not None and doc.get("_wall_offset") is not None:
        wall = rec + doc["_wall_offset"]

    stamp_present = s["l1"] == "present" or s["l2"] == "present"
    weak = s["l2"] == "likely" or s["l1"] == "inconclusive"
    content = any((L.get(k) or {}).get("content_match") for k in ("l1", "wt"))
    ran = [k for k in ("l1", "l2") if s[k] in ("present", "likely", "inconclusive", "absent")]
    integrity_layers = [
        k for k in ("l1", "wt")
        if (L.get(k) or {}).get("integrity_checked")
        and (L.get(k) or {}).get("content_match")
        and (
            k != "l1"
            or (
                float((L.get("l1") or {}).get("lead_in_s") or 0.0) < FOREIGN_MARGIN_MIN_S
                and float((L.get("l1") or {}).get("unmatched_tail_s") or 0.0) < FOREIGN_MARGIN_MIN_S
            )
        )
    ]

    if stamp_present:
        recorded = "verified"
    elif weak:
        recorded = "likely"
    elif ran:
        recorded = "not_verified"
    else:
        recorded = "unknown"
    cuts = [e for e in edits if e["kind"] in ("removed", "repeated", "foreign", "reordered", "inserted", "replaced")]
    if cuts:
        integrity = "edited"
    elif integrity_layers and (recorded in ("verified", "likely") or content):
        integrity = "intact"
    else:
        integrity = "unknown"
    when = fmt_time(wall) if wall is not None else None
    proof = doc.get("proof")
    unanchored = False
    if isinstance(proof, dict) and proof.get("session_id") is not None:
        af = proof.get("anchored_fraction")
        if not proof.get("drand_rounds") or (af is not None and af == 0.0):
            unanchored = True
        elif af is not None and af < 0.5:
            warns.append(
                f"unanchored_session: only {af * 100:.0f}% of matched seconds are drand-anchored"
            )

    es = _edit_summary(edits)
    missing = [e["restored_text"] for e in edits if e.get("restored_text")]
    missing_tail = (" Missing words: " + ", ".join(f'"{m}"' for m in missing) + ".") if missing else ""
    if recorded == "verified":
        if unanchored:
            warns.append(
                "unanchored_session: 0% of matched seconds are drand-anchored; "
                "proves key possession only, not wall-clock time"
            )
            head = f"Recorded at {vname}, {when} (unanchored: time not verified)."
        else:
            head = f"Recorded at {vname}, {when}."
        if integrity == "edited":
            head += f" {es}."
        elif integrity == "intact":
            head += f" Complete: nothing removed ({clip_s:.1f} s)."
        else:
            head += " Room stamp verified; cuts not checked" + (" yet." if pending else ".")
        head += missing_tail
    elif recorded == "likely":
        if unanchored:
            warns.append(
                "unanchored_session: 0% of matched seconds are drand-anchored; "
                "proves key possession only, not wall-clock time"
            )
        head = f"Probably recorded at {vname}" + (f", around {when}" if when else "") + " (weak room stamp)."
        if es:
            head += f" {es}."
        head += missing_tail
    elif recorded == "not_verified":
        if content:
            head = (f"Words match {vname}" + (f", {when}" if when else "")
                    + ", but the room stamp is missing: this audio wasn't recorded "
                    "in the room (e.g. re-recorded from a stream).")
            if es:
                head += f" {es}."
            head += missing_tail
        elif s["l2"] == "too_short" and s["l1"] != "present":
            head = f"No room stamp found, and the clip is too short ({clip_s:.1f} s) for the backup check (needs 8 s)."
        else:
            head = f"No room stamp found. This clip can't be tied to {vname}."
    else:
        if content:
            reason = doc.get("_unknown_reason") or "no stamp layer could run"
            head = f"Words match {vname}" + (f", {when}" if when else "") + f" (room stamp not checked: {reason})."
            if integrity == "edited" and es:
                head += f" {es}."
            elif integrity == "intact":
                head += f" Complete: nothing removed ({clip_s:.1f} s)."
            head += missing_tail
        else:
            reason = doc.get("_unknown_reason") or "no layer could run"
            head = f"Couldn't check this clip: {reason}."
    if pending and recorded != "verified":
        head += " Still checking the backup layer…"

    x = head
    if len(x) > 260:
        x = x[:257].rstrip() + "…"

    v = doc["verdict"]
    v.update({"recorded_here": recorded, "integrity": integrity, "headline": head, "x_reply_text": x,
              "recorded_at_unix": wall, "recorded_at_iso": iso_time(wall) if wall is not None else None,
              "recorded_at_precision_s": prec, "recorded_at_source": src,
              "tempo_ratio": (L.get("l2") or {}).get("tempo") if (s["l2"] in ("present", "likely") and not l2_time_mismatch) else (1.0 if s["l1"] in ("present", "inconclusive") else None),
              "edits": edits, "content_match": bool(content),
              "confidence": {k: s[k] for k in ("l1", "l2", "wt")}})
    doc["pending_layers"] = pending
    doc["warnings"] = sorted(set(warns), key=warns.index)
    return doc


# ------------------------------------------------------------------------------- orchestration

def _git_rev(path: str) -> str | None:
    try:
        p = subprocess.run(["git", "-C", path, "rev-parse", "--short", "HEAD"], capture_output=True, timeout=3)
        return p.stdout.decode().strip() or None
    except Exception:
        return None


def _versions() -> dict:
    import scipy
    d = {"python": platform.python_version(), "platform": platform.platform(), "numpy": np.__version__,
         "scipy": scipy.__version__}
    try:
        import soundfile
        d["soundfile"] = soundfile.__version__
    except Exception:
        pass
    try:
        from .media import ffmpeg_exe
        d["ffmpeg"] = os.path.basename(ffmpeg_exe())
    except Exception:
        d["ffmpeg"] = None
    return d


def load_key(key_file: str | None) -> bytes:
    raw = os.environ.get("ROOMSTAMP_KEY")
    if not raw:
        if not key_file or not os.path.isfile(key_file):
            raise FileNotFoundError(f"venue key not found ({key_file}); pass --key-file or set ROOMSTAMP_KEY")
        raw = open(key_file, encoding="utf-8").read().strip()
    key = bytes.fromhex(raw)
    if len(key) != 32:
        raise ValueError("venue key must be 32 bytes (64 hex chars)")
    return key


def parse_time(s: str | float | None) -> float | None:
    if s is None or s == "":
        return None
    if isinstance(s, (int, float)):
        return float(s)
    if re.fullmatch(r"\d+(\.\d+)?", s):
        return float(s)
    from datetime import datetime, timezone
    d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.timestamp()


def _empty_doc(argv: list[str]) -> dict:
    return {"detector": {"name": "wt-detect", "version": VERSION}, "stamp": None,
            "witness": contract.empty_witness(None),
            "schema": SCHEMA, "status": "ok", "error": None, "pending_layers": [],
            "verdict": {"recorded_here": "unknown", "integrity": "unknown", "headline": "", "x_reply_text": "",
                        "recorded_at_unix": None, "recorded_at_iso": None, "recorded_at_precision_s": None,
                        "recorded_at_source": None, "tempo_ratio": None, "content_match": False,
                        "venue": {"name": None, "key_id": None, "session": None, "mode": None},
                        "edits": [], "confidence": {"l1": "skipped", "l2": "skipped", "wt": "skipped"}},
            "clip": None, "layers": {"l1": _stage("skipped"), "l2": _stage("skipped"), "wt": _stage("skipped")},
            "proof": None, "warnings": [],
            "run": {"tool": "wt-detect", "version": VERSION, "schema": SCHEMA, "argv": argv,
                    "started_unix": time.time(), "elapsed_s": None, "seed": None,
                    "git_commit": _git_rev(os.path.dirname(os.path.abspath(__file__))), "env": _versions()}}


def _proof(sl: SessionLog | None, doc: dict, clip: Clip) -> dict:
    d = {"spec": spec.SPEC_VERSION, "drand_chain": spec.DRAND_CHAIN, "key_id": sl.key_id if sl else doc["verdict"]["venue"].get("key_id"),
         "session_id": sl.sid if sl else None, "session_log_sha256": _sha256_file(sl.path)[0] if sl else None,
         "feed_wav_sha256": _sha256_file(sl.feed_path)[0] if sl and sl.feed_path else None,
         "clip_sha256": clip.sha256, "normalized_pcm_sha256": clip.pcm_sha256,
         "normalized_format": "float32le mono 48000 Hz", "matched_t_range": None, "drand_rounds": [],
         "anchored_fraction": None, "anchors": [], "merkle": None}
    v = doc["verdict"]
    src = v.get("recorded_at_source")
    cand_keys = ([src] if src in ("l1", "l2", "wt") else []) + [k for k in ("l1", "l2", "wt") if k != src]
    t0 = t1 = None
    if sl:
        for k in cand_keys:
            st = doc["layers"].get(k) or {}
            if st.get("status") == "absent":
                continue
            vt0, vt1 = st.get("venue_t_start"), st.get("venue_t_end")
            if vt0 is not None and vt1 is not None:
                lo, hi = min(vt0, vt1), max(vt0, vt1)
                if hi > sl.t_start and lo < sl.t_end:
                    t0, t1 = max(float(sl.t_start), lo), min(float(sl.t_end), hi)
                    break
    if sl and t0 is not None and t1 is not None and v["recorded_here"] in ("verified", "likely", "not_verified"):
        a, b = max(sl.t_start, int(np.floor(t0))), min(sl.t_end, int(np.ceil(t1)))
        if a < b:
            d["matched_t_range"] = [a, b]
            secs = [s for s in sl.secs if a <= s["t"] < b]
            d["anchors"] = [{"t": s["t"], "round": s.get("round"), "anchored": s.get("anchored"),
                             "randomness": s.get("randomness"), "anchor": s.get("anchor"),
                             "dac_utc": s.get("dac_utc")} for s in secs]
            d["drand_rounds"] = sorted({s["round"] for s in secs if s.get("round") is not None and s.get("anchored")})
            d["anchored_fraction"] = round(sl.session().anchored_fraction(a, b - 1), 4)
    elif sl and sl.secs:
        d["anchored_fraction"] = round(sl.session().anchored_fraction(sl.t_start, max(sl.t_start, sl.t_end - 1)), 4)
    return d


def _restore_audio(doc: dict, sl: SessionLog | None, out_path: str | None) -> None:
    """For each precise removed edit, save what the venue feed had there (+-1.5 s context)."""
    if not sl or not sl.feed_path or not out_path:
        return
    from . import wavio
    stem = os.path.splitext(os.path.abspath(out_path))[0]
    for i, e in enumerate(doc["layers"]["l1"].get("edits") or []):  # fuse() copies these into the verdict
        if e["source"] != "l1" or e["kind"] != "removed" or e.get("venue_t_from") is None or e.get("venue_t_to") is None:
            continue
        a = (e["venue_t_from"] - 1.5 - sl.t_start) * FS
        b = (e["venue_t_to"] + 1.5 - sl.t_start) * FS
        try:
            s0 = max(0, int(a))
            x = _read_feed(sl.feed_path, s0, max(0, int(b)))
            if len(x) == 0:
                continue
            path = f"{stem}_edit{i}.wav"
            pk = np.max(np.abs(x)) + 1e-9
            wavio.write(path, x * min(1.0, 0.9 / pk))
            e["restored_audio"] = os.path.relpath(path, os.path.dirname(os.path.abspath(out_path)))
            e["restored_audio_venue_t"] = [sl.t_start + s0 / FS, sl.t_start + (s0 + len(x)) / FS]
        except Exception as ex:
            doc["warnings_base"].append(f"restored_audio_failed: {ex}")


def rank_sessions(sessions: list[SessionLog], key_id: str, around: float | None, session_path: str | None,
                  clip: Clip, warnings: list[str]) -> list[SessionLog]:
    """Candidates for this key, best first: envelope-matched sessions by match strength, then
    the rest by closeness to --around (or recency)."""
    if session_path:
        sp = os.path.abspath(session_path)
        cands = [s for s in sessions if s.path == sp]
        if not cands:
            try:
                cands = [load_session_log(sp, DEFAULT_VENUE_NAME)]
            except Exception as e:
                warnings.append(f"session_log_unreadable: {os.path.basename(sp)}: {e}")
                return []
        if cands[0].key_id != key_id:
            warnings.append(f"key_mismatch: key {key_id} != session key {cands[0].key_id}; expect 'absent'")
    else:
        cands = [s for s in sessions if s.key_id == key_id]
        if around is not None:
            cands = [s for s in cands if s.t_start - 600 <= around <= s.t_end + 3600]
    for s in cands:
        try:
            match_feed(clip.x, s, warnings)
        except Exception as e:
            warnings.append(f"feed_match_failed: {s.sid}: {e}")

    def order(s: SessionLog):
        matched = s.env_z >= ENV_Z_MIN
        near = -abs(around - s.t_start) if around is not None else s.t_start
        return (matched, s.env_z if matched else 0.0, near)
    return sorted(cands, key=order, reverse=True)


L1_RANK = {"present": 4, "inconclusive": 3, "absent": 1, "too_short": 0, "skipped": 0, "error": 0}


def _l1_score(st: dict) -> tuple:
    return (L1_RANK.get(st.get("status"), 0), bool(st.get("content_match")), st.get("z") or 0.0)


def run(clip_path: str, *, out_path: str | None = None, venue_dir: str = "logs", key_file: str | None = "venue.key",
        session_log: str | None = None, around: str | float | None = None, witness: str | None = None,
        layers: str = "auto", fast: bool = False, confirm: bool = False, merge: bool = False,
        l2_timeout_s: float = L2_TIMEOUT_S, venue_name: str | None = None, max_duration_s: float = MAX_CLIP_S,
        witness_root: str | None = None, allow_network: bool = False, budget_s: float = BUDGET_S,
        argv: list[str] | None = None) -> dict:
    """Never raises for bad input or a failing stage; returns the detection.json dict (and
    writes it atomically to out_path if given). Raises only for usage errors (key file)."""
    t_run = time.time()
    deadline = t_run + budget_s
    doc = _empty_doc(argv if argv is not None else [clip_path])
    doc["warnings_base"] = []
    W = doc["warnings_base"]
    prev = None
    if merge and out_path and os.path.isfile(out_path):
        try:
            with open(out_path, encoding="utf-8") as f:
                prev = json.load(f)
        except Exception:
            prev = None
    try:
        key = load_key(key_file)
    except (FileNotFoundError, ValueError) as e:   # no venue key here: witness-only run, stamp = null
        key = None
        W.append(f"no_venue_key: {e}; stamp layers skipped")
    try:
        clip = ingest(clip_path, max_duration_s)
    except ClipError as e:
        doc.update(status="error", error={"code": e.code, "message": str(e)})
        doc["clip"] = {"path": os.path.abspath(clip_path), "sha256": None, "bytes": None, "duration_s": 0.0,
                       "orig_rate": None, "orig_channels": None, "codec": None, "decoder": None}
        doc["_unknown_reason"] = str(e)
        fuse(doc)
        return _finish(doc, out_path, t_run)
    if prev and (prev.get("clip") or {}).get("sha256") != clip.sha256:
        W.append("merge_ignored: existing detection.json is for a different clip")
        prev = None
    W.extend(clip.warnings)
    doc["run"]["seed"] = clip.seed
    doc["clip"] = {"path": clip.path, "sha256": clip.sha256, "bytes": clip.n_bytes,
                   "duration_s": round(clip.duration_s, 3), "orig_rate": clip.orig_rate,
                   "orig_channels": clip.orig_channels, "codec": clip.codec, "decoder": clip.decoder}
    vname = venue_name or DEFAULT_VENUE_NAME
    work = os.path.join(tempfile.gettempdir(), "wt-detect", clip.sha256[:16])
    os.makedirs(work, exist_ok=True)

    sessions = find_sessions(venue_dir, vname, W) if key else []
    if prev and session_log is None:
        prev_argv = (prev.get("run") or {}).get("argv") or []
        for i, arg in enumerate(prev_argv):
            if arg == "--session" and i + 1 < len(prev_argv):
                session_log = prev_argv[i + 1]
                break
            if isinstance(arg, str) and arg.startswith("--session="):
                session_log = arg.split("=", 1)[1]
                break
        if session_log is None:
            prev_sid = ((prev.get("verdict") or {}).get("venue") or {}).get("session")
            prev_sp = (prev.get("session_match") or {}).get("path")
            if prev_sp and os.path.isfile(prev_sp) and not any(s.sid == prev_sid for s in sessions):
                session_log = prev_sp
    kid = spec.key_id(key) if key else None
    t_around = parse_time(around)
    cands = rank_sessions(sessions, kid, t_around, session_log, clip, W) if key else []
    want = {"l1", "l2", "wt"} if layers == "auto" else set(layers.split(","))
    prevL = (prev or {}).get("layers", {})
    L = doc["layers"]

    # L1: the same speech can match several sessions (rehearsals, replays); only the stamp
    # decides. Try the best few until one locks; at most MAX_SESSION_TRIES stamp tests.
    sl = cands[0] if cands else None
    tries = []
    if "l1" in want:
        best = None
        for s in [c for c in cands if c.feed_path][:MAX_SESSION_TRIES]:
            try:
                st, _ = run_l1(clip, s, key, W)
            except Exception:
                st = _stage("error", None, "L1 crashed", error=traceback.format_exc()[-2000:])
            tries.append({"session": s.sid, "env_z": round(min(s.env_z, 999.0), 2), "l1": st["status"]})
            if best is None or _l1_score(st) > _l1_score(best[0]):
                best = (st, s)
            if st["status"] == "present":
                break
        if best is not None:
            L["l1"], sl = best
        elif cands:
            L["l1"] = _stage("skipped", summary="no session for this key has a usable clean feed")
        else:
            L["l1"] = _stage("skipped", summary="no session for this key")
    elif prevL.get("l1"):
        L["l1"] = prevL["l1"]
        sl = next((c for c in cands if c.sid == (prev or {}).get("verdict", {}).get("venue", {}).get("session")), sl)
    if sl:
        doc["verdict"]["venue"] = {"name": sl.venue_name if sl.venue_name != DEFAULT_VENUE_NAME else vname,
                                   "key_id": sl.key_id, "session": sl.sid, "mode": sl.mode}
        doc["session_match"] = {"session": sl.sid, "path": sl.path, "env_z": round(min(sl.env_z, 999.0), 2),
                                "feed_offset_s": None if sl.feed_offset_s is None else round(sl.feed_offset_s, 2),
                                "tried": tries,
                                "candidates": [{"session": s.sid, "env_z": round(min(s.env_z, 999.0), 2)} for s in cands]}
    else:
        doc["verdict"]["venue"] = {"name": vname, "key_id": kid, "session": None, "mode": None}
        doc["_unknown_reason"] = ("no venue key configured" if key is None else
                                  f"no stamped session for this venue key ({kid}) in {os.path.abspath(venue_dir)}"
                                  + (" near the given time" if t_around is not None else ""))

    wsec = contract.empty_witness("no --witness-root")
    if "wt" in want and witness_root:
        l1r = (
            L["l1"].get("recorded_at_unix")
            if (L["l1"].get("status") in ("present", "inconclusive") or L["l1"].get("content_match"))
            else None
        )
        try:
            wsec, s4 = contract.witness_section(clip.x, witness_root, l1r or t_around, W,
                                                budget_s=max(10.0, deadline - time.time() - 60))
            L["wt"] = contract.stage_from_s4(s4, wsec)
        except Exception:
            L["wt"] = _stage("error", "s4", "witness matcher crashed", error=traceback.format_exc()[-2000:])
            wsec = contract.empty_witness("witness matcher crashed")
    elif "wt" in want:
        try:
            L["wt"] = run_wt(clip, witness, W)
        except Exception:
            L["wt"] = _stage("error", "s4", "witness matcher crashed", error=traceback.format_exc()[-2000:])
    elif prevL.get("wt"):
        L["wt"] = prevL["wt"]
        wsec = (prev or {}).get("witness") or wsec

    # L2: applicable? worth it? now or later?
    l2_window = None
    if key is None:
        l2_skip = "no venue key configured"
    elif sl is not None and not sl.l2_on:
        l2_skip = f"layer 2 is off in this session ({sl.mode} mode)"
    elif clip.duration_s < L2_MIN_CLIP_S:
        l2_skip = None  # run_l2 reports too_short
    else:
        l2_skip = None
    if l2_skip is None:
        l1 = L["l1"]
        if l1.get("recorded_at_unix") is not None and (
            l1.get("status") in ("present", "inconclusive") or l1.get("content_match")
        ):
            c = l1["recorded_at_unix"]
            l2_window = (int(c - 30), int(c + clip.duration_s * 1.6 + 30))
        elif sl is not None:
            l2_window = (sl.t_start, sl.t_end)
            wt = L.get("wt") or {}
            if sl.feed_offset_s is not None and sl.env_z >= ENV_Z_MIN:
                c = sl.t_start + sl.feed_offset_s
                l2_window = (int(c - 60), int(c + clip.duration_s * 1.6 + 60))
            elif wt.get("content_match") and wt.get("recorded_at_unix") is not None:
                c = wt["recorded_at_unix"]
                w0, w1 = max(sl.t_start, int(c - 120)), min(sl.t_end, int(c + clip.duration_s * 1.6 + 120))
                if w0 < w1:
                    l2_window = (w0, w1)
            elif t_around is not None:
                w0, w1 = max(sl.t_start, int(t_around - 1800)), min(sl.t_end, int(t_around + 600))
                if w0 < w1:
                    l2_window = (w0, w1)
            if l2_window[1] - l2_window[0] > L2_MAX_WINDOW_S:
                l2_window = (l2_window[1] - L2_MAX_WINDOW_S, l2_window[1])
        elif t_around is not None:
            l2_window = (int(t_around - 1800), int(t_around + 600))
        else:
            l2_skip = "no session or time window to search"
    explicit_l2 = layers != "auto" and "l2" in want
    l1_locked = L["l1"].get("status") == "present"
    run_l2_now = l2_skip is None and (explicit_l2 or (layers == "auto" and (not l1_locked or confirm)))
    if l2_skip is not None:
        L["l2"] = prevL.get("l2") if (prevL.get("l2") and not explicit_l2) else _stage("skipped", summary=l2_skip)
    elif run_l2_now and fast and not explicit_l2:
        L["l2"] = _stage("pending", "s3", "queued: run again with --layers l2 --merge")
    elif run_l2_now:
        try:
            L["l2"] = run_l2(clip, sl, key, l2_window, work,
                             max(5.0, min(l2_timeout_s, deadline - time.time() - 10)), allow_network)
        except Exception:
            L["l2"] = _stage("error", "s3", "L2 crashed", error=traceback.format_exc()[-2000:])
    elif prevL.get("l2"):
        L["l2"] = prevL["l2"]
    else:
        L["l2"] = _stage("skipped", "s3", "L1 already locked (pass --confirm to run L2 too)")

    if sl is not None:
        for allow_content in (False, True):
            for k in ("l1", "l2", "wt"):
                st = L.get(k) or {}
                ok = (
                    st.get("status") in ("present", "likely", "inconclusive")
                    if not allow_content
                    else (k != "l2" and st.get("content_match"))
                )
                if ok and st.get("recorded_at_unix") is not None:
                    rec = st["recorded_at_unix"]
                    doc["_wall_offset"] = sl.wall(rec) - rec
                    break
            if "_wall_offset" in doc:
                break
    fuse(doc)
    if sl is not None:
        doc["proof"] = _proof(sl, doc, clip)
        _restore_audio(doc, sl, out_path)
        fuse(doc)
    else:
        doc["proof"] = _proof(None, doc, clip)
    doc["stamp"] = contract.stamp_section(doc)
    doc["witness"] = wsec
    return _finish(doc, out_path, t_run)


def _finish(doc: dict, out_path: str | None, t_run: float) -> dict:
    doc["run"]["elapsed_s"] = round(time.time() - t_run, 2)
    for k in ("warnings_base", "_wall_offset", "_unknown_reason"):
        doc.pop(k, None)
    doc = _jsonable(doc)
    if out_path:
        d = os.path.dirname(os.path.abspath(out_path))
        os.makedirs(d, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".wt-", suffix=".json", dir=d)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(doc, f, indent=1, ensure_ascii=False)
        os.replace(tmp, out_path)
    return doc


def main(argv=None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)
    ap = argparse.ArgumentParser(prog="wt-detect", description="Roomstamp/Wholetake detector: clip -> detection.json")
    ap.add_argument("clip", help="any audio/video file (wav, m4a, mp4, ogg, mp3, webm...)")
    ap.add_argument("-o", "--out", help="detection.json path (default: print JSON to stdout)")
    ap.add_argument("--witness-root", default=os.environ.get("WT_WITNESS_ROOT"),
                    help="ingest witness dir <root>/<stream>/<seq>.wav (CONTRACTS.md witness section)")
    ap.add_argument("--venue", default=os.environ.get("WT_VENUE_DIR", "logs"),
                    help="directory with the emitter's session_*.jsonl + feed_*.wav (env WT_VENUE_DIR)")
    ap.add_argument("--venue-name", help=f"display name (default '{DEFAULT_VENUE_NAME}')")
    ap.add_argument("--key-file", default=os.environ.get("WT_KEY_FILE", "venue.key"),
                    help="venue key (or env ROOMSTAMP_KEY / WT_KEY_FILE); without one, stamp = null")
    ap.add_argument("--session", help="force one session log")
    ap.add_argument("--around", help="claimed / posted time, unix or ISO (UTC if no zone)")
    ap.add_argument("--witness", help="S4 witness record")
    ap.add_argument("--layers", default="auto", help="auto | comma list of l1,l2,wt")
    ap.add_argument("--fast", action="store_true", help="skip L2 now; mark it pending for a --merge run")
    ap.add_argument("--confirm", action="store_true", help="run L2 even when L1 locked")
    ap.add_argument("--merge", action="store_true", help="keep layers from the existing --out file, re-fuse")
    ap.add_argument("--l2-timeout", type=float, default=L2_TIMEOUT_S)
    ap.add_argument("--max-duration", type=float, default=MAX_CLIP_S)
    ap.add_argument("--allow-network", action="store_true",
                    help="let L2 fetch drand rounds missing from the log (off: CONTRACTS.md zero-network rule)")
    ap.add_argument("--quiet", action="store_true", help="with -o: print nothing")
    a = ap.parse_args(argv)
    bad = set(a.layers.split(",")) - {"l1", "l2", "wt"} if a.layers != "auto" else set()
    if bad:
        ap.error(f"unknown layers: {sorted(bad)}")
    try:
        doc = run(a.clip, out_path=a.out, venue_dir=a.venue, key_file=a.key_file, session_log=a.session,
                  around=a.around, witness=a.witness, layers=a.layers, fast=a.fast, confirm=a.confirm,
                  merge=a.merge, l2_timeout_s=a.l2_timeout, venue_name=a.venue_name,
                  max_duration_s=a.max_duration, witness_root=a.witness_root, allow_network=a.allow_network,
                  argv=["wt-detect"] + argv)
    except (FileNotFoundError, ValueError) as e:
        print(f"wt-detect: {e}", file=sys.stderr)
        sys.exit(2)
    except Exception:
        tb = traceback.format_exc()
        doc = _empty_doc(["wt-detect"] + argv)
        doc.update(status="error", error={"code": "INTERNAL", "message": tb.strip().splitlines()[-1], "trace": tb[-3000:]})
        doc["clip"] = {"path": os.path.abspath(a.clip), "sha256": None, "bytes": None, "duration_s": 0.0,
                       "orig_rate": None, "orig_channels": None, "codec": None, "decoder": None}
        doc["warnings_base"] = []
        doc["_unknown_reason"] = "internal error"
        fuse(doc)
        _finish(doc, a.out, time.time())
        print(tb, file=sys.stderr)
        sys.exit(4)
    if a.out:
        if not a.quiet:
            print(f"{doc['verdict']['headline']}  ->  {a.out}")
    else:
        sys.stdout.reconfigure(encoding="utf-8")
        print(json.dumps(doc, indent=1, ensure_ascii=False))
    sys.exit(3 if doc["status"] == "error" else 0)


if __name__ == "__main__":
    main()
