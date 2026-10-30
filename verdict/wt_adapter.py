"""The real detector behind $WT_DETECT: text alignment, then Shreyas's S4 acoustic matcher, then Roomstamp.

    WT_DETECT="./py -m verdict.wt_adapter" ./py -m verdict serve
    ./py -m verdict.wt_adapter <clip.wav> --witness-root data/witness --out detection.json

It speaks CONTRACTS.md on the outside and wt-detect/S4 on the inside:
1. ref_detect's word alignment finds the stream and chunks (cheap, and it works on any length of record).
2. S4 (wholetake.match) re-matches the clip ACOUSTICALLY against those chunks, stitched into one in-memory
   record (our own transcripts, wholetake's own features), so cuts land at 10 ms instead of word edges and every
   piece gets a real audio_score. S4 only replaces the text result when it passes S4's own "strong" test;
   otherwise the text result stands and stays labelled text-only.
3. Roomstamp (roomstamp.wt_detect, fast = layer 1 only) fills `stamp` when a venue key and emitter logs exist.
Every stage is optional: a failing stage leaves a warning on stderr, never a wrong answer.
"""
import argparse
import json
import os
import sys
import wave
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from ingest.chain import write_atomic
from verdict import ref_detect

AROUND_CHUNKS = 2  # chunks of record context on each side of the text match that S4 searches
STRONG_COVER, STRONG_SIM = 0.5, 0.45  # S4's own present/likely rule (roomstamp/witness.py)
STATUS_SCORE = {"present": 0.95, "likely": 0.7, "inconclusive": 0.4, "absent": 0.0}
REMOVED_KINDS = ("deletion", "pause_trim")


def warn(msg: str) -> None:
    print(f"wt_adapter: {msg}", file=sys.stderr, flush=True)


def _read_pcm(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as wf:
        if wf.getframerate() != 16000 or wf.getnchannels() != 1 or wf.getsampwidth() != 2:
            raise ValueError(f"{path.name}: expected 16 kHz mono s16")
        return np.frombuffer(wf.readframes(wf.getnframes()), dtype="<i2").astype(np.float32) / 32768.0


def contiguous_run(sdir: Path, seqs: list[int], around: int = AROUND_CHUNKS) -> list[int]:
    """The matched seqs plus up to `around` neighbours each side, stopping at gaps and missing chunks.

    Stitching across a gap would invent continuity the witness never saw, so a run never crosses one."""
    def ok(s: int) -> bool:
        return s >= 0 and (sdir / f"{s:06d}.wav").exists() and (sdir / f"{s:06d}.json").exists()

    lo, hi = min(seqs), max(seqs)
    if not all(ok(s) for s in range(lo, hi + 1)):
        return []
    while lo - 1 >= min(seqs) - around and ok(lo - 1):
        lo -= 1
    while hi + 1 <= max(seqs) + around and ok(hi + 1):
        hi += 1
    return list(range(lo, hi + 1))


def build_record(sdir: Path, run: list[int]):
    """An S4 Record over chunks `run`: one stitched waveform + our transcript words on the stitched clock."""
    from wholetake import asr, audio, record, text

    xs, offsets, words, t = [], [], [], 0.0
    for s in run:
        x = _read_pcm(sdir / f"{s:06d}.wav")
        tx = json.loads((sdir / f"{s:06d}.json").read_text())
        for w in tx.get("words") or []:
            toks = text.tokens(str(w.get("w", "")))
            if not toks:
                continue
            ws, we = float(w.get("s", 0.0)), float(w.get("e", 0.0))
            step = (we - ws) / len(toks)  # same contraction split as wholetake.asr.transcribe
            for k, tok in enumerate(toks):
                words.append(asr.Word(len(words), tok, str(w.get("w", "")).strip(), t + ws + k * step,
                                      t + ws + (k + 1) * step, float(w.get("p", 1.0))))
        dur = len(x) / audio.SR
        offsets.append((s, t, dur))
        xs.append(x)
        t += dur
    x = np.concatenate(xs)
    meta = {"format": record.FORMAT, "id": f"{sdir.name}:{run[0]}-{run[-1]}", "duration_s": round(len(x) / audio.SR, 3),
            "t_start_unix": None, "fps": audio.FPS, "n_mels": audio.NMELS, "n_words": len(words)}
    return record.Record(meta, words, audio.features(x)), offsets


def to_chunk(offsets: list[tuple[int, float, float]], t: float) -> tuple[int, float]:
    """Stitched-record time -> (seq, seconds into that chunk's wav)."""
    for s, start, dur in offsets:
        if t < start + dur - 1e-6:
            return s, round(max(0.0, t - start), 3)
    s, start, dur = offsets[-1]
    return s, round(min(dur, t - start), 3)


def split_piece(offsets, cs: float, ce: float, ws: float, we: float, score: float) -> list[dict]:
    """One S4 piece -> contract pieces that never cross a chunk boundary (clip time mapped linearly)."""
    rate = (ce - cs) / (we - ws) if we > ws else 1.0
    out = []
    for s, start, dur in offsets:
        a, b = max(ws, start), min(we, start + dur)
        if b - a < 0.01:
            continue
        out.append({"clip_s": [round(cs + (a - ws) * rate, 3), round(cs + (b - ws) * rate, 3)], "seq": s,
                    "src_s": [round(a - start, 3), round(b - start, 3)], "audio_score": round(min(1.0, max(0.0, score)), 3)})
    return out


def split_removed(offsets, at: float, wf: float, wt: float, words: str) -> list[dict]:
    """One S4 gap -> contract removed entries, split where the removed span crosses a chunk boundary."""
    out = []
    for s, start, dur in offsets:
        a, b = max(wf, start), min(wt, start + dur)
        if b - a < 0.005:
            continue
        out.append({"after_clip_s": round(at, 3), "seq": s, "src_s": [round(a - start, 3), round(b - start, 3)],
                    "words": words if not out else ""})
    return out


def acoustic_witness(clip_wav: Path, witness_root: Path, base: dict) -> dict | None:
    """S4 over the text match's chunks. None = keep the text result (no match to refine, or S4 not strong)."""
    stream = base.get("stream")
    pieces = base.get("pieces") or []
    if base.get("verdict") == "not_found" or not stream or not pieces:
        return None
    sdir = witness_root / stream
    run = contiguous_run(sdir, sorted({p["seq"] for p in pieces if p.get("stream", stream) == stream}))
    if not run:
        warn(f"{stream}: matched chunks aren't contiguous on disk; keeping the text result")
        return None

    from wholetake import asr, match
    if asr.MODEL != "none":
        asr._model = ref_detect.get_whisper_model()  # one Whisper on the GPU, not two
    rec, offsets = build_record(sdir, run)
    r = match.match(rec, _read_pcm(clip_wav))

    sp = r.get("pieces") or []
    covered = sum(p["clip_end_s"] - p["clip_start_s"] for p in sp)
    if not sp or covered < STRONG_COVER * r["clip_duration_s"] or np.median([p["similarity"] for p in sp]) < STRONG_SIM:
        warn(f"S4 not strong (covered {covered:.1f}/{r['clip_duration_s']:.1f} s); keeping the text result")
        return None

    out_pieces = [q for p in sp for q in split_piece(offsets, p["clip_start_s"], p["clip_end_s"],
                                                     p["witness_start_s"], p["witness_end_s"], p["similarity"])]
    removed = [q for g in r.get("gaps") or [] if g.get("type") in REMOVED_KINDS
               for q in split_removed(offsets, g["clip_at_s"], g["witness_from_s"], g["witness_to_s"],
                                      g.get("restored_text") or "")]
    inserted = [{"clip_s": [f["clip_start_s"], f["clip_end_s"]], "text": f.get("clip_text", "")}
                for f in r.get("foreign_insertions") or []]
    verdict = r["verdict"]
    if verdict == "cut" and not removed:  # S4 called it a cut through a gap kind we don't list as removal
        verdict = "spliced"
    if verdict == "faithful" and removed:
        verdict = "cut"
    return {"verdict": verdict, "stream": stream, "pieces": sorted(out_pieces, key=lambda q: q["clip_s"][0]),
            "removed": removed, "inserted": inserted, "transcript_clip": base.get("transcript_clip", "")}


def _layer_score(st: dict | None) -> float | None:
    if not st or st.get("status") in (None, "skipped", "error", "timeout", "pending", "too_short"):
        return None
    p = st.get("p_value")
    if isinstance(p, (int, float)):
        return round(min(1.0, max(0.0, 1.0 - float(p))), 4)
    return STATUS_SCORE.get(st["status"])


def venue_paths() -> tuple[Path, Path]:
    """(emitter log dir, venue key file). Accepts every name the stack uses: ours (WT_VENUE*), wt-detect's
    (WT_VENUE_DIR, WT_KEY_FILE) and deploy's (ROOMSTAMP_*); defaults are repo-relative, and deploy.sh's
    keys/venue.key is found when there is no venue.key at the root."""
    repo = Path(__file__).resolve().parents[1]
    env = lambda *names: next((os.environ[n] for n in names if os.environ.get(n)), None)
    venue = Path(env("WT_VENUE", "WT_VENUE_DIR", "ROOMSTAMP_LOG_DIR") or repo / "logs")
    key = env("WT_VENUE_KEY", "WT_KEY_FILE", "ROOMSTAMP_KEY_FILE")
    if key is None:
        key = next((p for p in (repo / "venue.key", repo / "keys" / "venue.key") if p.is_file()), repo / "venue.key")
    return venue, Path(key)


def roomstamp_stamp(clip_wav: Path) -> dict | None:
    """Layer-1 Roomstamp check when this box has a venue key and emitter logs; None means 'not checked'."""
    venue, key_file = venue_paths()
    if not os.environ.get("ROOMSTAMP_KEY") and not key_file.is_file():
        return None
    if not venue.is_dir() or not any(venue.glob("session_*.jsonl")):
        return None
    from roomstamp import wt_detect
    doc = wt_detect.run(str(clip_wav), venue_dir=str(venue), key_file=str(key_file), layers="l1,l2", fast=True)
    if doc.get("status") != "ok":
        warn(f"roomstamp: {doc.get('error')}")
        return None
    v, layers = doc.get("verdict") or {}, doc.get("layers") or {}
    at = v.get("recorded_at_unix")
    cuts = [{"at_clip_s": round(float(e["clip_t"]), 3), "removed_s": round(float(e["removed_s"]), 3)}
            for e in v.get("edits") or []
            if e.get("kind") == "removed" and e.get("source") in ("l1", "l2") and (e.get("removed_s") or 0) > 0]
    return {"present": v.get("recorded_here") == "verified",
            "layer1_score": _layer_score(layers.get("l1")), "layer2_score": _layer_score(layers.get("l2")),
            "recorded_at_utc": (datetime.fromtimestamp(at, timezone.utc).isoformat(timespec="milliseconds")
                                .replace("+00:00", "Z") if isinstance(at, (int, float)) else None),
            "venue": (v.get("venue") or {}).get("name"), "cuts": cuts,
            # wt-detect's own words, for the page; extra keys are allowed by the contract's validator
            "recorded_here": v.get("recorded_here"), "headline": v.get("headline"),
            "pending_layers": doc.get("pending_layers") or []}


def detect_file(clip_wav: Path, witness_root: Path) -> dict:
    det = ref_detect.detect_file(clip_wav, witness_root)
    try:
        refined = acoustic_witness(clip_wav, witness_root, det["witness"])
    except Exception as e:  # S4 is an upgrade, never a reason to fail the verdict
        warn(f"S4 failed ({type(e).__name__}: {e}); keeping the text result")
        refined = None
    if refined is not None:
        det["witness"] = refined
        det["detector"] = {"name": "wt-detect", "version": "adapter-0.1+s4"}
    try:
        det["stamp"] = roomstamp_stamp(clip_wav)
    except Exception as e:
        warn(f"roomstamp failed ({type(e).__name__}: {e}); stamp left unchecked")
        det["stamp"] = None
    return det


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("clip_wav", help="path to 16 kHz mono s16 wav")
    ap.add_argument("--witness-root", required=True, help="witness root directory")
    ap.add_argument("--out", required=True, help="path to write detection.json")
    a = ap.parse_args()
    try:
        res = detect_file(Path(a.clip_wav), Path(a.witness_root))
        out_p = Path(a.out)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        write_atomic(out_p, (json.dumps(res, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    except Exception as e:
        print(f"ERROR: wt_adapter failed: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
