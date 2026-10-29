"""S4 transcript matcher (witness mode): clip + witness record -> spans in the original + verdict.

Text proposes, acoustics decide (Argon red-team, 2026-10-29):
1. Smith-Waterman over normalised Whisper tokens (IDF-weighted, phonetic fuzzy match, repeated
   with masking) finds *where* in a long record the clip may come from and a first tempo guess.
   Whisper word times jitter by 80–250 ms and a cut "not" can be decoded back in, so text never
   decides a cut on its own.
2. The clip's log-mel is time-warped by the tempo, and every 1 s window (0.1 s hop) is correlated
   against the witness in those regions. A continuous piece of the original is a run of windows
   whose witness offset stays on one line. A step in the offset is a cut (forward) or a splice
   (backward); speech that matches nowhere between two pieces is a foreign insertion.
3. Each boundary is placed where "left piece's offset" stops explaining the clip and "right
   piece's offset" starts (cumulative-similarity argmax), offsets are refined to sub-frame, and
   the removed duration is their difference. Witness words in the removed span are restored.

Verdicts: faithful | cut | spliced | not_found. Gaps that remove no words (pause trims) are
reported but don't make a clip "cut".
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

import numpy as np

from . import asr, audio, text
from .audio import FPS
from .record import Record

# Text alignment
SW_GAP_CLIP = -0.8         # clip token with no witness partner (ASR insertion / foreign word)
SW_GAP_WIT = -0.6          # witness token skipped (ASR deletion / a cut)
SW_MISMATCH = -1.0
SW_PHON = 1.5
SW_MIN_SCORE = 6.0
SW_MAX_PIECES = 6
REGION_PAD_S = 3.0
WHOLE_RECORD_MAX_S = 20 * 60   # no text hit: search the whole record acoustically if it's this short

# Acoustic tracking
WIN = 100                  # 1.0 s windows
HOP = 10                   # 0.1 s
MIN_ACTIVE = 0.4           # a window needs 40% non-silent frames to vote
R_MIN = 0.35               # window peak cosine
Z_MIN = 3.5                # window peak vs the rest of the search region (robust z); 4 agreeing windows make a piece
TRACK_TOL = 3              # frames (30 ms): same piece if the offset stays within this of its line
MIN_PIECE_WINDOWS = 12     # 1 s windows at 0.1 s hop overlap 90%: 12 agreeing = 2.1 s of evidence
JUMP_MIN = 0.06            # s: smaller offset steps are below resolution and merged
FOREIGN_MIN_S = 0.30
FOREIGN_JUMP_TOL = 0.25     # s: piece edges around an insertion are only this sharp
EDGE_THETA = 0.25          # per-frame cosine a piece must keep up to "own" a frame at a boundary
REFINE_S = 2.0


@dataclass
class Piece:
    u0: int                # warped-clip frame of first matched window start
    u1: int                # warped-clip frame of last matched window end
    c0: float              # offset model: witness_frame = u + c0 + c1 * u
    c1: float
    windows: list = field(default_factory=list)
    r: float = 0.0

    def off(self, u: float) -> float:
        return self.c0 + self.c1 * u


# ---------------------------------------------------------------------------- text alignment

def _sw_pieces(cw: list[asr.Word], ww: list[asr.Word]) -> list[list[tuple[int, int]]]:
    """Iterative local alignment. Returns anchor lists [(clip_idx, witness_idx), ...]."""
    M, N = len(cw), len(ww)
    if M == 0 or N == 0:
        return []
    vocab: dict[str, int] = {}
    wid = np.array([vocab.setdefault(w.tok, len(vocab)) for w in ww])
    cid = np.array([vocab.get(w.tok, -1) for w in cw])
    pvocab: dict[str, int] = {}
    wph = np.array([pvocab.setdefault(text.phon(w.tok), len(pvocab)) if len(w.tok) >= 3 else -2 for w in ww])
    cph = np.array([pvocab.get(text.phon(w.tok), -1) if len(w.tok) >= 3 else -3 for w in cw])
    df = np.bincount(wid, minlength=len(vocab))
    idf = np.log((N + 1) / (df[wid] + 1))
    match_w = 1.0 + np.minimum(2.0, idf / 2.0)          # "the" ~ 1.3, a content word ~ 3
    S = np.where(cid[:, None] == wid[None, :], match_w[None, :],
                 np.where(cph[:, None] == wph[None, :], SW_PHON, SW_MISMATCH))
    idx = np.arange(N + 1)
    pieces = []
    for _ in range(SW_MAX_PIECES):
        H = np.zeros((M + 1, N + 1))
        for i in range(1, M + 1):
            e = np.zeros(N + 1)
            e[1:] = np.maximum(H[i - 1, :-1] + S[i - 1], H[i - 1, 1:] + SW_GAP_CLIP)
            e = np.maximum(e, 0.0)
            H[i] = np.maximum.accumulate(e - SW_GAP_WIT * idx) + SW_GAP_WIT * idx
        i, j = np.unravel_index(int(np.argmax(H)), H.shape)
        if H[i, j] < SW_MIN_SCORE:
            break
        anchors = []
        while i > 0 and j > 0 and H[i, j] > 1e-9:
            if abs(H[i, j] - (H[i - 1, j - 1] + S[i - 1, j - 1])) < 1e-9:
                if S[i - 1, j - 1] > 0:
                    anchors.append((i - 1, j - 1))
                i, j = i - 1, j - 1
            elif abs(H[i, j] - (H[i - 1, j] + SW_GAP_CLIP)) < 1e-9:
                i -= 1
            else:
                j -= 1
        anchors.reverse()
        if len(anchors) < 3:
            break
        pieces.append(anchors)
        S[anchors[0][0]:anchors[-1][0] + 1] = -10.0   # mask these clip tokens and look again
    return pieces


def _tempo_from_text(pieces, cw, ww) -> float | None:
    slopes = []
    for p in pieces:
        pts = [(cw[c].start, ww[w].start) for c, w in p]
        for a in range(len(pts)):
            for b in range(a + 1, len(pts)):
                dc = pts[b][0] - pts[a][0]
                if dc >= 1.5:
                    slopes.append((pts[b][1] - pts[a][1]) / dc)
    if len(slopes) < 5:
        return None
    return float(np.clip(np.median(slopes), 0.7, 1.5))


# ---------------------------------------------------------------------------- acoustics

def warp(F: np.ndarray, alpha: float) -> np.ndarray:
    """Clip features resampled in time so one frame = one witness frame (alpha = clip speed-up)."""
    if abs(alpha - 1.0) < 1e-6:
        return F
    T = len(F)
    n = max(1, int(round(T * alpha)))
    p = np.arange(n) / alpha
    i0 = np.clip(np.floor(p).astype(int), 0, T - 1)
    i1 = np.clip(i0 + 1, 0, T - 1)
    f = (p - i0)[:, None]
    G = F[i0] * (1 - f) + F[i1] * f
    active = (np.abs(F[i0]).sum(1) > 0) & (np.abs(F[i1]).sum(1) > 0)
    G /= np.linalg.norm(G, axis=1, keepdims=True) + 1e-9
    G[~active] = 0.0
    return G.astype(np.float32)


def _regions(pieces, cw, ww, alpha, clip_s, rec_s) -> list[tuple[int, int]]:
    spans = []
    for p in pieces:
        b = float(np.median([ww[w].start - alpha * cw[c].start for c, w in p]))
        spans.append((b - REGION_PAD_S, b + alpha * clip_s + REGION_PAD_S))
    if not spans and rec_s <= WHOLE_RECORD_MAX_S:
        spans = [(0.0, rec_s)]
    spans.sort()
    merged: list[list[float]] = []
    for a, b in spans:
        if merged and a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    return [(max(0, int(a * FPS)), min(int(rec_s * FPS), int(b * FPS))) for a, b in merged]


def _scan(Fc: np.ndarray, Fw: np.ndarray, regions, win=WIN, hop=HOP, starts=None):
    """Best witness start, peak cosine and robust z for each clip window."""
    starts = np.arange(0, max(1, len(Fc) - win + 1), hop) if starts is None else np.asarray(starts, int)
    starts = starts[starts + win <= len(Fc)]
    active = (np.abs(Fc).sum(1) > 0).astype(np.float32)
    n_act = np.array([active[u:u + win].sum() for u in starts])
    # Window score at witness start s = sum_k cos(clip[u+k], wit[s+k]): diagonal sums of the
    # frame-similarity matrix, accumulated one diagonal offset k at a time for the chosen rows.
    rows = []
    for r0, r1 in regions:
        L = r1 - r0 - win + 1
        if L <= 0:
            continue
        D = Fc @ Fw[r0:r1].T
        S = np.zeros((len(starts), L), np.float32)
        for k in range(win):
            S += D[starts + k, k:k + L]
        rows.append((r0, S))
    out = []
    for i, u in enumerate(starts):
        if not rows or n_act[i] < MIN_ACTIVE * win:
            out.append((int(u), None, 0.0, 0.0))
            continue
        allc = np.concatenate([S[i] for _, S in rows]) / n_act[i]
        k = int(np.argmax(allc))
        off = 0
        for r0_, S in rows:
            if k < off + S.shape[1]:
                pos = r0_ + (k - off)
                break
            off += S.shape[1]
        med = np.median(allc)
        mad = 1.4826 * np.median(np.abs(allc - med)) + 1e-6
        out.append((int(u), int(pos), float(allc[k]), float((allc[k] - med) / mad)))
    return out


def _tempo_refine(F, Fw, regions, alpha0: float, text_tempo: bool) -> float:
    def score(a):
        G = warp(F, a)
        if len(G) < 2 * WIN:
            return -1.0
        st = np.linspace(0, len(G) - 2 * WIN, num=min(10, max(1, len(G) // (2 * WIN)))).astype(int)
        res = [r for _, s, r, _ in _scan(G, Fw, regions, win=2 * WIN, starts=st) if s is not None]
        return float(np.mean(sorted(res)[len(res) // 2:])) if res else -1.0

    # coarse: around the text guess, plain speed, and the usual editor presets (text can be absent)
    coarse = {round(a0 + d, 3) for a0 in {round(alpha0, 3), 1.0} for d in (-0.02, 0.0, 0.02)}
    coarse |= {0.9, 1.1, 1.2, 1.25, 1.3, 1.4, 1.5} if not text_tempo else set()
    best_a, best_s = 1.0, -2.0
    for a in sorted(coarse):
        s = score(a)
        if s > best_s:
            best_a, best_s = a, s
    for a in np.arange(best_a - 0.02, best_a + 0.0201, 0.01):
        s = score(a)
        if s > best_s:
            best_a, best_s = a, s
    for a in np.arange(best_a - 0.008, best_a + 0.0081, 0.002):
        s = score(a)
        if s > best_s:
            best_a, best_s = a, s
    if abs(best_a - 1.0) < 0.01 and score(1.0) >= best_s - 0.01:
        best_a = 1.0  # unedited speed unless something else is clearly better
    return float(best_a)


def _group(track) -> list[Piece]:
    good = [(u, s - u, r) for u, s, r, z in track if s is not None and r >= R_MIN and z >= Z_MIN]
    groups: list[list] = []
    for u, off, r in good:
        g = groups[-1] if groups else None
        if g is not None:
            us = np.array([w[0] for w in g[-20:]])
            os_ = np.array([w[1] for w in g[-20:]])
            pred = (np.polyval(np.polyfit(us, os_, 1), u) if len(g) >= 5 and np.ptp(us) > 0 else np.median(os_))
            if abs(off - pred) <= TRACK_TOL:
                g.append((u, off, r))
                continue
        groups.append([(u, off, r)])
    groups = [g for g in groups if len(g) >= MIN_PIECE_WINDOWS]
    merged: list[list] = []
    for g in groups:  # same line after an interruption (noise burst, applause) = still continuous
        if merged and abs(np.median([w[1] for w in g]) - np.median([w[1] for w in merged[-1]])) <= TRACK_TOL:
            merged[-1] += g
        else:
            merged.append(g)
    pieces = []
    for g in merged:
        us = np.array([w[0] for w in g], float)
        os_ = np.array([w[1] for w in g], float)
        c1, c0 = (np.polyfit(us, os_, 1) if len(g) >= 5 and np.ptp(us) > 0 else (0.0, float(np.median(os_))))
        if abs(c1) > 0.01:  # tempo residual can only be tiny after warping; else fall back to flat
            c1, c0 = 0.0, float(np.median(os_))
        pieces.append(Piece(int(us[0]), int(us[-1]) + WIN, float(c0), float(c1), g, float(np.median([w[2] for w in g]))))
    return pieces


def _frame_sim(Fc, Fw, piece: Piece, lo: int, hi: int, pause: float | None = None) -> np.ndarray:
    """Per-frame cosine between the clip and the witness under this piece's offset. With `pause`,
    frames that land on silence in the witness score `pause` instead: a pause in the original
    is explained by the piece whatever the clip has there (room noise, a music bed)."""
    k = np.arange(lo, hi)
    j = np.round(k + piece.off(k)).astype(int)
    ok = (j >= 0) & (j < len(Fw)) & (k >= 0) & (k < len(Fc))
    d = np.zeros(len(k))
    d[ok] = np.einsum("ij,ij->i", Fc[k[ok]], Fw[j[ok]])
    if pause is not None:
        silent = np.zeros(len(k), bool)
        silent[ok] = ~np.any(Fw[j[ok]] != 0, axis=1)
        d[silent] = pause
    return d


def _subframe_off(Fc, Fw, piece: Piece, lo: int, hi: int) -> float:
    """Piece offset near [lo, hi), refined to a fraction of a frame by a parabola over +-2 frames."""
    lo, hi = max(lo, 0), min(hi, len(Fc))
    mid = (lo + hi) / 2
    base = int(round(piece.off(mid)))
    k = np.arange(lo, hi)
    vals = []
    for o in range(base - 2, base + 3):
        j = k + o
        ok = (j >= 0) & (j < len(Fw))
        vals.append(float(np.einsum("ij,ij->", Fc[k[ok]], Fw[j[ok]])) if ok.any() else -1e9)
    i = int(np.argmax(vals))
    if 0 < i < 4:
        a, b, c = vals[i - 1], vals[i], vals[i + 1]
        den = a - 2 * b + c
        frac = 0.5 * (a - c) / den if den < 0 else 0.0
    else:
        frac = 0.0
    return base - 2 + i + float(np.clip(frac, -0.5, 0.5))


def _boundary(Fc, Fw, A: Piece, B: Piece):
    """(a_end, b_start, u_cut, offA, offB) in warped-clip frames for the junction A -> B."""
    lo, hi = max(0, A.u1 - WIN - HOP), min(len(Fc), B.u0 + WIN + HOP)
    if hi <= lo + 2:
        lo, hi = min(A.u1, B.u0) - WIN, max(A.u1, B.u0) + WIN
    P = EDGE_THETA + 0.05
    dA, dB = _frame_sim(Fc, Fw, A, lo, hi, P), _frame_sim(Fc, Fw, B, lo, hi, P)
    cA = np.concatenate([[0.0], np.cumsum(dA)])
    cB = np.concatenate([[0.0], np.cumsum(dB)])
    u_cut = lo + int(np.argmax(cA + (cB[-1] - cB)))
    a_end = lo + int(np.argmax(np.concatenate([[0.0], np.cumsum(dA - EDGE_THETA)])))
    tail = np.concatenate([np.cumsum((dB - EDGE_THETA)[::-1])[::-1], [0.0]])
    b_start = lo + int(np.argmax(tail))
    R = int(REFINE_S * FPS)
    offA = _subframe_off(Fc, Fw, A, max(A.u0, u_cut - R), u_cut)
    offB = _subframe_off(Fc, Fw, B, u_cut, min(B.u1, u_cut + R))
    return a_end, b_start, u_cut, offA, offB


# ---------------------------------------------------------------------------- verdict

def _words_in(ww: list[asr.Word], t0: float, t1: float, min_overlap: float = 0.5) -> list[asr.Word]:
    out = []
    for w in ww:
        d = max(1e-3, w.end - w.start)
        if min(w.end, t1) - max(w.start, t0) >= min_overlap * d:
            out.append(w)
    return out


def _context(ww, t0, t1, removed, pad=6.0) -> str:
    ids = {w.i for w in removed}
    parts, open_ = [], False
    for w in ww:
        if w.end < t0 - pad or w.start > t1 + pad:
            continue
        if w.i in ids and not open_:
            parts.append("[")
            open_ = True
        if w.i not in ids and open_:
            parts[-1] += "]"
            open_ = False
        parts.append(w.tok.upper() if w.i in ids else w.tok)
    if open_:
        parts[-1] += "]"
    return " ".join(parts).replace("[ ", "[")


def match(rec: Record, clip: np.ndarray, language: str | None = "en") -> dict:
    tm = {}
    t0 = time.time()
    clip_s = len(clip) / audio.SR
    Fc = audio.features(clip)
    tm["features_ms"] = (time.time() - t0) * 1e3
    t1 = time.time()
    cw = asr.transcribe(clip, language)
    tm["clip_asr_ms"] = (time.time() - t1) * 1e3
    t2 = time.time()
    ww, Fw = rec.words, rec.feats
    tp = _sw_pieces(cw, ww)
    alpha_t = _tempo_from_text(tp, cw, ww)
    alpha0 = alpha_t or 1.0
    regions = _regions(tp, cw, ww, alpha0, clip_s, rec.duration)
    tm["text_ms"] = (time.time() - t2) * 1e3

    t3 = time.time()
    result = {"verdict": "not_found", "record_id": rec.meta["id"], "clip_duration_s": round(clip_s, 3),
              "tempo": 1.0, "pieces": [], "gaps": [], "foreign_insertions": [], "lead_in_s": None, "outro_s": None,
              "decisive_words_removed": [], "clip_transcript": " ".join(w.raw for w in _dedup_raw(cw)),
              "text_pieces": len(tp), "timing_ms": tm}
    if not regions:
        tm["acoustic_ms"] = (time.time() - t3) * 1e3
        return _done(result, t0)
    alpha = _tempo_refine(Fc, Fw, regions, alpha0, alpha_t is not None)
    G = warp(Fc, alpha)
    pieces = _group(_scan(G, Fw, regions))
    tm["acoustic_ms"] = (time.time() - t3) * 1e3
    result["tempo"] = round(alpha, 3)
    covered = sum(p.u1 - p.u0 for p in pieces) / FPS
    if not pieces or covered < min(1.5, 0.3 * clip_s * alpha):
        return _done(result, t0)

    def clip_t(u):  # warped frame -> clip seconds
        return float(u) / FPS / alpha

    bounds = [_boundary(G, Fw, A, B) for A, B in zip(pieces, pieces[1:])]
    foreign = [(b_start - a_end) / FPS >= FOREIGN_MIN_S and _speech_frac(G, a_end, b_start) > 0.5
               for a_end, b_start, *_ in bounds]
    # piece k spans [starts[k], ends[k]): junctions at the cut, or around a foreign insertion
    starts = [0] + [b[1] if f else b[2] for b, f in zip(bounds, foreign)]
    ends = [b[0] if f else b[2] for b, f in zip(bounds, foreign)] + [len(G)]
    # outer edges: where the first/last piece stops explaining the clip (lead-in / outro)
    first, last = pieces[0], pieces[-1]
    d0 = _frame_sim(G, Fw, first, 0, first.u0 + WIN, EDGE_THETA + 0.05)
    starts[0] = int(np.argmax(np.concatenate([np.cumsum((d0 - EDGE_THETA)[::-1])[::-1], [0.0]])))
    d1 = _frame_sim(G, Fw, last, last.u1 - WIN, len(G), EDGE_THETA + 0.05)
    ends[-1] = last.u1 - WIN + int(np.argmax(np.concatenate([[0.0], np.cumsum(d1 - EDGE_THETA)])))
    for p, a, b in zip(pieces, starts, ends):
        wa, wb = (a + p.off(a)) / FPS, (b + p.off(b)) / FPS
        result["pieces"].append({
            "clip_start_s": round(clip_t(a), 3), "clip_end_s": round(clip_t(b), 3),
            "witness_start_s": round(wa, 3), "witness_end_s": round(wb, 3), "similarity": round(p.r, 3),
            "witness_text": " ".join(w.tok for w in _words_in(ww, wa, wb))})
    if starts[0] / FPS > 0.5 and _speech_frac(G, 0, starts[0]) > 0.3:
        result["lead_in_s"] = round(clip_t(starts[0]), 3)
    if (len(G) - ends[-1]) / FPS > 0.5 and _speech_frac(G, ends[-1], len(G)) > 0.3:
        result["outro_s"] = round(clip_t(len(G)) - clip_t(ends[-1]), 3)

    spliced = cut = False
    for (a_end, b_start, u_cut, offA, offB), is_foreign in zip(bounds, foreign):
        ua, ub = (a_end, b_start) if is_foreign else (u_cut, u_cut)
        wl, wr = (ua + offA) / FPS, (ub + offB) / FPS
        jump = wr - wl   # witness time missing between the two pieces (negative: went back)
        if is_foreign:
            spliced = True
            result["foreign_insertions"].append({
                "clip_start_s": round(clip_t(a_end), 3), "clip_end_s": round(clip_t(b_start), 3),
                "clip_text": " ".join(w.raw for w in _dedup_raw(_words_in(cw, clip_t(a_end), clip_t(b_start), 0.5)))})
        gap = {"clip_at_s": round(clip_t(ua), 3), "witness_from_s": round(wl, 3), "witness_to_s": round(wr, 3),
               "removed_s": round(jump, 3)}
        tol = FOREIGN_JUMP_TOL if is_foreign else JUMP_MIN
        if jump < -tol:
            spliced = True
            gap["type"] = "reorder"
        elif jump > tol:
            removed = _words_in(ww, wl, wr)
            # no transcript (acoustic-only record): can't tell words from pauses, call it a deletion
            gap["type"] = "deletion" if removed or not ww else "pause_trim"
            gap["restored_text"] = " ".join(w.tok for w in removed)
            gap["restored_words"] = [{"word": w.tok, "start_s": round(w.start, 3), "end_s": round(w.end, 3)}
                                     for w in removed]
            dec = sorted({w.tok for w in removed if w.tok in text.DECISIVE})
            gap["decisive_words"] = dec
            gap["context"] = _context(ww, wl, wr, removed)
            result["decisive_words_removed"] += dec
            cut |= gap["type"] == "deletion"
            if is_foreign:
                gap["type"] = "replacement"
        else:
            if not is_foreign:
                continue
            gap["type"] = "insertion"
        result["gaps"].append(gap)
    result["verdict"] = "spliced" if spliced else "cut" if cut else "faithful"
    return _done(result, t0)


def _done(result: dict, t0: float) -> dict:
    tm = result["timing_ms"]
    tm["total_ms"] = (time.time() - t0) * 1e3
    for k in tm:
        tm[k] = round(tm[k])
    return result


def _speech_frac(F, a, b) -> float:
    a, b = max(0, int(a)), min(len(F), int(b))
    return float((np.abs(F[a:b]).sum(1) > 0).mean()) if b > a else 0.0


def _dedup_raw(words: list[asr.Word]) -> list[asr.Word]:
    """Contractions become several tokens sharing a raw word; show the raw word once."""
    out = []
    for w in words:
        if out and out[-1].raw == w.raw and abs(out[-1].end - w.start) < 1e-6:
            continue
        out.append(w)
    return out
