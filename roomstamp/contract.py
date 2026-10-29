"""The product side's detector contract (wholetake repo, CONTRACTS.md) on top of wt-detect.

    $WT_DETECT <clip.wav> --witness-root <root> --out <detection.json>

Top-level keys the verdict service validates: detector, clip, stamp, witness. wt-detect's own
richer keys (verdict, layers, proof, ...) ride along; the validator ignores extra keys.

witness section: the S4 matcher (wholetake.match) run against the ingest daemon's chunked
streams <root>/<stream>/<seq:06d>.wav + .meta.json (+ .json Whisper words from the transcriber,
+ .gap.json when the stream was down). A wholetake Record is assembled from a window of chunks
(no ASR needed on the witness side: the transcriber already wrote word timestamps), and every
match is mapped back to (seq, seconds into that chunk), split at chunk boundaries.
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass

import numpy as np
from scipy.signal import resample_poly

WSR = 16000                      # ingest chunks and S4 features are 16 kHz
WINDOW_NO_ASR_S = 19 * 60        # S4 searches a record acoustically only up to 20 min without clip text
WINDOW_ASR_S = 3 * 3600
AROUND_PAD_S = 9 * 60            # window around a known recording time
GAP_PAD_MAX_S = 5.0              # silence inserted where the stream was down (never bridge a gap)
MAX_STREAMS = 4
NOT_FOUND_COVER = 0.60           # contract: < 60 % aligned -> not_found
MAX_FORWARD_JUMP_S = 60.0        # contract: a forward jump beyond this is a splice
MIN_SUBPIECE_S = 0.05


@dataclass
class Chunk:
    seq: int
    wav: str
    duration_s: float
    received_unix: float | None
    words: list[dict]            # [{w, s, e, p?}] seconds into this chunk
    off_s: float = 0.0           # position in the assembled witness audio


def _read_json(p: str) -> dict | None:
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def list_streams(root: str) -> list[str]:
    if not root or not os.path.isdir(root):
        return []
    out = []
    for name in sorted(os.listdir(root)):
        d = os.path.join(root, name)
        if os.path.isdir(d) and any(f.endswith(".meta.json") and f[:6].isdigit() for f in os.listdir(d)):
            out.append(name)
    return out


def load_chunks(stream_dir: str) -> tuple[list[Chunk], list[dict]]:
    chunks, gaps = [], []
    for f in sorted(os.listdir(stream_dir)):
        m = re.fullmatch(r"(\d{6})\.meta\.json", f)
        if m:
            seq = int(m.group(1))
            meta = _read_json(os.path.join(stream_dir, f)) or {}
            wav = os.path.join(stream_dir, f"{seq:06d}.wav")
            if not os.path.isfile(wav):
                continue
            tr = _read_json(os.path.join(stream_dir, f"{seq:06d}.json")) or {}
            dur = meta.get("duration_s")
            if not isinstance(dur, (int, float)) or dur <= 0:
                import soundfile as sf
                dur = sf.info(wav).duration
            chunks.append(Chunk(seq, wav, float(dur), meta.get("received_utc_unix"), tr.get("words") or []))
        elif re.fullmatch(r"\d{6}\.gap\.json", f):
            g = _read_json(os.path.join(stream_dir, f))
            if g:
                gaps.append({**g, "seq": int(f[:6])})
    chunks.sort(key=lambda c: c.seq)
    return chunks, gaps


def pick_window(chunks: list[Chunk], around: float | None, max_s: float) -> list[Chunk]:
    """Chunks near `around` (unix), else the most recent ones, at most max_s of audio."""
    if not chunks:
        return []
    if around is not None and any(c.received_unix for c in chunks):
        lo, hi = around - AROUND_PAD_S, around + AROUND_PAD_S
        sel = [c for c in chunks if c.received_unix and c.received_unix >= lo and c.received_unix - c.duration_s <= hi]
        if sel:
            chunks = sel
    out, tot = [], 0.0
    for c in reversed(chunks):
        if tot + c.duration_s > max_s and out:
            break
        out.append(c)
        tot += c.duration_s
    return out[::-1]


def assemble(chunks: list[Chunk], gaps: list[dict]):
    """Witness audio (16 kHz) + S4 Record. Consecutive seqs are joined seamlessly (the daemon
    cuts one continuous stream into chunks); a missing seq or a gap file inserts silence so no
    match can bridge time the witness never heard."""
    import soundfile as sf
    from wholetake import asr, audio, record, text
    gap_after = {g["seq"] - 1: float(g.get("duration_s") or GAP_PAD_MAX_S) for g in gaps}
    parts, words, pos, pads = [], [], 0.0, []
    prev = None
    for c in chunks:
        if prev is not None and (c.seq != prev.seq + 1 or prev.seq in gap_after):
            pad = min(GAP_PAD_MAX_S, gap_after.get(prev.seq, GAP_PAD_MAX_S))
            parts.append(np.zeros(int(pad * WSR)))
            pads.append((pos, pos + pad))
            pos += pad
        x, sr = sf.read(c.wav, dtype="float64", always_2d=True)
        x = x.mean(axis=1)
        if sr != WSR:
            g = int(np.gcd(sr, WSR))
            x = resample_poly(x, WSR // g, sr // g)
        c.off_s = pos
        c.duration_s = len(x) / WSR
        parts.append(x)
        for w in c.words:
            toks = text.tokens(str(w.get("w", "")))
            if not toks:
                continue
            s, e = float(w["s"]) + pos, float(w["e"]) + pos
            step = (e - s) / len(toks)
            for k, t in enumerate(toks):
                words.append(asr.Word(len(words), t, str(w["w"]).strip(), s + k * step, s + (k + 1) * step,
                                      float(w.get("p", 1.0))))
        pos += len(x) / WSR
        prev = c
    xw = np.concatenate(parts) if parts else np.zeros(0)
    meta = {"format": record.FORMAT, "id": "witness-root", "duration_s": round(len(xw) / WSR, 3),
            "t_start_unix": None, "asr_model": "ingest-transcriber", "fps": audio.FPS, "n_mels": audio.NMELS,
            "n_words": len(words)}
    return record.Record(meta, words, audio.features(xw)), pads


def _locate(chunks: list[Chunk], w: float) -> tuple[Chunk, float]:
    """Witness-audio seconds -> (chunk, seconds into it); a time in a pad snaps to the nearest edge."""
    best = None
    for c in chunks:
        if c.off_s <= w < c.off_s + c.duration_s:
            return c, w - c.off_s
        d = min(abs(w - c.off_s), abs(w - (c.off_s + c.duration_s)))
        if best is None or d < best[0]:
            best = (d, c)
    c = best[1]
    return c, min(max(0.0, w - c.off_s), c.duration_s)


def _split_piece(chunks: list[Chunk], p: dict) -> list[dict]:
    ca, cb, wa, wb = p["clip_start_s"], p["clip_end_s"], p["witness_start_s"], p["witness_end_s"]
    if cb - ca <= 0 or wb - wa <= 0:
        return []
    k = (cb - ca) / (wb - wa)
    out = []
    for c in chunks:
        a, b = max(wa, c.off_s), min(wb, c.off_s + c.duration_s)
        if b - a < MIN_SUBPIECE_S:
            continue
        out.append({"clip_s": [round(ca + (a - wa) * k, 3), round(ca + (b - wa) * k, 3)], "seq": c.seq,
                    "src_s": [round(a - c.off_s, 3), round(b - c.off_s, 3)],
                    "audio_score": round(float(min(1.0, max(0.0, p.get("similarity", 0.0)))), 3)})
    return out


def _raw_words(c: Chunk, s0: float, s1: float) -> str:
    return " ".join(str(w["w"]).strip() for w in c.words if s0 <= 0.5 * (w["s"] + w["e"]) < s1)


def _match_stream(clip16: np.ndarray, root: str, stream: str, around: float | None, asr_on: bool) -> dict | None:
    from wholetake import match
    chunks, gaps = load_chunks(os.path.join(root, stream))
    win = pick_window(chunks, around, WINDOW_ASR_S if asr_on else WINDOW_NO_ASR_S)
    if not win:
        return None
    rec, pads = assemble(win, gaps)
    r = match.match(rec, clip16.astype(np.float32))
    return {"stream": stream, "chunks": win, "pads": pads, "r": r,
            "covered": sum(p["clip_end_s"] - p["clip_start_s"] for p in r["pieces"]) / max(r["clip_duration_s"], 1e-9)}


def empty_witness(reason: str | None = None) -> dict:
    return {"verdict": "not_found", "stream": None, "pieces": [], "removed": [], "inserted": [],
            "transcript_clip": "", "note": reason}


def witness_section(clip_x48k: np.ndarray, root: str | None, around: float | None, warnings: list[str],
                    budget_s: float = 120.0) -> tuple[dict, dict | None]:
    """-> (contract witness object, raw S4 result of the best stream or None)."""
    streams = list_streams(root) if root else []
    if not streams:
        return empty_witness("no witness streams under --witness-root" if root else "no --witness-root"), None
    if "WHOLETAKE_ASR" not in os.environ:   # never start a 1.6 GB model download mid-demo
        os.environ["WHOLETAKE_ASR"] = "none"
        warnings.append("wt_asr_off: clip not transcribed (WHOLETAKE_ASR unset); witness words still come "
                        "from the ingest transcripts")
    asr_on = os.environ.get("WHOLETAKE_ASR", "none") != "none"
    clip16 = resample_poly(np.asarray(clip_x48k, dtype=np.float64), 1, 3)
    # most recently active streams first
    def last_mtime(s):
        d = os.path.join(root, s)
        return max(os.path.getmtime(os.path.join(d, f)) for f in os.listdir(d))
    streams = sorted(streams, key=last_mtime, reverse=True)[:MAX_STREAMS]
    best, t0 = None, time.time()
    for s in streams:
        if time.time() - t0 > budget_s:
            warnings.append(f"witness_budget: stopped after {len(streams)} streams")
            break
        try:
            m = _match_stream(clip16, root, s, around, asr_on)
        except Exception as e:
            warnings.append(f"witness_stream_failed: {s}: {type(e).__name__}: {e}")
            continue
        if m and (best is None or m["covered"] > best["covered"]):
            best = m
    if best is None:
        return empty_witness("no witness chunk could be matched"), None
    return to_contract_witness(best, warnings), best["r"]


def to_contract_witness(m: dict, warnings: list[str]) -> dict:
    r, chunks, pads = m["r"], m["chunks"], m["pads"]
    pieces = [sp for p in r["pieces"] for sp in _split_piece(chunks, p)]
    if m["covered"] < NOT_FOUND_COVER or not pieces:
        w = empty_witness(f"only {m['covered']:.0%} of the clip aligned to stream {m['stream']}")
        w["transcript_clip"] = r.get("clip_transcript") or ""
        return w
    removed, inserted, spliced = [], [], r["verdict"] == "spliced"
    for g in r["gaps"]:
        if g.get("type") in ("deletion", "replacement"):
            wl, wr = g["witness_from_s"], g["witness_to_s"]
            if any(a <= 0.5 * (wl + wr) < b for a, b in pads):
                warnings.append(f"witness_gap_at_cut: the stream was down where the clip jumps at {g['clip_at_s']:.1f} s")
                spliced = True
                continue
            c, s0 = _locate(chunks, wl)
            s1 = min(c.duration_s, s0 + max(0.0, wr - wl))
            removed.append({"after_clip_s": round(g["clip_at_s"], 3), "seq": c.seq, "src_s": [round(s0, 3), round(s1, 3)],
                            "words": _raw_words(c, s0, s1) or g.get("restored_text", ""),
                            "removed_s": round(g["removed_s"], 3)})
            if wr - wl > MAX_FORWARD_JUMP_S:
                spliced = True
        elif g.get("type") == "reorder":
            spliced = True
    for f in r["foreign_insertions"]:
        inserted.append({"clip_s": [round(f["clip_start_s"], 3), round(max(f["clip_end_s"], f["clip_start_s"] + 0.01), 3)],
                         "text": f.get("clip_text", "")})
    verdict = "spliced" if spliced else ("cut" if removed else "faithful")
    tr = r.get("clip_transcript") or ""
    if not tr:   # no clip ASR: what the matched spans say, from the witness transcript
        tr = " ".join(filter(None, (_raw_words(next(c for c in chunks if c.seq == p["seq"]), *p["src_s"])
                                    for p in pieces)))
    return {"verdict": verdict, "stream": m["stream"], "pieces": pieces, "removed": removed, "inserted": inserted,
            "transcript_clip": tr, "tempo": r.get("tempo"), "coverage": round(m["covered"], 3)}


def stage_from_s4(r: dict | None, wsec: dict) -> dict:
    """wt-detect layer record (for fuse()) from the S4 result + the contract witness object."""
    from .wt_detect import _stage
    if r is None:
        return _stage("skipped", "s4", wsec.get("note") or "no witness record")
    status = {"faithful": "present", "cut": "present", "spliced": "present", "not_found": "absent"}[wsec["verdict"]]
    edits = []
    for g in wsec["removed"]:
        edits.append({"kind": "removed", "clip_t": g["after_clip_s"], "clip_t_range": [g["after_clip_s"]] * 2,
                      "removed_s": g.get("removed_s"), "restored_text": g["words"] or None, "precision_s": 0.05,
                      "source": "wt", "witness_seq": g["seq"], "witness_src_s": g["src_s"]})
    for i in wsec["inserted"]:
        edits.append({"kind": "foreign", "clip_t": i["clip_s"][0], "clip_t_range": i["clip_s"],
                      "duration_s": i["clip_s"][1] - i["clip_s"][0], "removed_s": None, "restored_text": None,
                      "precision_s": 0.2, "source": "wt"})
    summary = {"faithful": "Continuous span of the witnessed stream, nothing removed.",
               "cut": f"{len(wsec['removed'])} cut(s) vs the witnessed stream",
               "spliced": "Spliced relative to the witnessed stream",
               "not_found": wsec.get("note") or "Not found in the witnessed streams."}[wsec["verdict"]]
    return _stage(status, "s4", summary, edits=edits, content_match=status == "present",
                  integrity_checked=status == "present", wt_verdict=wsec["verdict"], detail=None)


# ------------------------------------------------------------------------------- stamp section

def _z_score01(z: float | None) -> float | None:
    """L1 piece z -> [0, 1]; 0.5 at the 'present' threshold (z 6), ~0.9 at z 18."""
    if z is None:
        return None
    z = max(0.0, float(z))
    return round(z * z / (z * z + 36.0), 3)


def _p_score01(p: float | None) -> float | None:
    """L2 p-value -> [0, 1]; 0.5 at p = 1e-3 (the 'present' bar), 1.0 at p <= 1e-6."""
    if p is None:
        return None
    return round(float(min(1.0, max(0.0, -np.log10(max(float(p), 1e-300)) / 6.0))), 3)


def _iso_utc(t: float) -> str:
    ms = int(round(float(t) * 1000))
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(ms // 1000)) + f".{ms % 1000:03d}Z"


def stamp_section(doc: dict) -> dict | None:
    L, v = doc["layers"], doc["verdict"]
    s1, s2 = (L.get("l1") or {}).get("status"), (L.get("l2") or {}).get("status")
    ran = [s for s in (s1, s2) if s in ("present", "likely", "inconclusive", "absent")]
    if not ran:
        return None
    rec = v.get("recorded_at_unix") if v["recorded_here"] in ("verified", "likely") else None
    cuts = [{"at_clip_s": round(e["clip_t"], 3), "removed_s": round(e["removed_s"], 3)}
            for e in v.get("edits", []) if e["kind"] == "removed" and e["source"] in ("l1", "l2")
            and (e.get("removed_s") or 0) > 0]
    return {"present": v["recorded_here"] == "verified",
            "layer1_score": _z_score01((L.get("l1") or {}).get("z")) if s1 in ran else None,
            "layer2_score": _p_score01((L.get("l2") or {}).get("p_value")) if s2 in ran else None,
            "recorded_at_utc": _iso_utc(rec) if rec is not None else None,
            "venue": (v.get("venue") or {}).get("name"), "cuts": cuts}
