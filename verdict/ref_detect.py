"""Reference text-only detector (stand-in for S5) using faster-whisper + Smith-Waterman word alignment.

    ./py -m verdict.ref_detect <clip.wav> --witness-root data/witness --out detection.json
"""
import argparse
import json
import os
import re
import sys
import wave
from pathlib import Path

from ingest.chain import sha256_file, write_atomic

MODEL_NAME = os.environ.get("WT_WHISPER_MODEL", "large-v3-turbo")
_MODEL = None


def norm_word(w: str) -> str:
    """Lowercase and strip punctuation so 'Changed,' and 'changed' align."""
    return re.sub(r"[^\w']+", "", w.lower()).strip("'")


def _wav_duration_s(wav_path: Path) -> float:
    with wave.open(str(wav_path), "rb") as wf:
        return round(wf.getnframes() / float(wf.getframerate()), 3)


def get_whisper_model():
    global _MODEL
    if _MODEL is None:
        from faster_whisper import WhisperModel

        device = os.environ.get("WT_WHISPER_DEVICE", "cuda")
        compute_type = os.environ.get("WT_WHISPER_COMPUTE", "float16" if device == "cuda" else "int8")
        try:
            _MODEL = WhisperModel(MODEL_NAME, device=device, compute_type=compute_type)
        except Exception:
            _MODEL = WhisperModel(MODEL_NAME, device="cpu", compute_type="int8")
    return _MODEL


def transcribe_clip(wav_path: Path) -> list[dict]:
    """Run faster-whisper with word timestamps on `wav_path`, returning [{w, s, e, p}]."""
    model = get_whisper_model()
    segments, _info = model.transcribe(
        str(wav_path),
        beam_size=5,
        word_timestamps=True,
        condition_on_previous_text=False,
    )
    words: list[dict] = []
    for seg in segments:
        for wd in seg.words or []:
            text = (wd.word or "").strip()
            if not text:
                continue
            words.append({
                "w": text,
                "s": round(float(wd.start), 3),
                "e": round(float(wd.end), 3),
                "p": round(float(getattr(wd, "probability", 1.0)), 3),
            })
    return words


def load_witness_streams(witness_root: Path) -> dict[str, list[dict]]:
    """Load all stream transcripts under `witness_root` into {stream: [chunk_dict, ...]}."""
    streams: dict[str, list[dict]] = {}
    if not witness_root.exists():
        return streams

    for sdir in sorted(witness_root.iterdir()):
        if not sdir.is_dir():
            continue
        chunks: list[dict] = []
        for tx_p in sorted(sdir.glob("[0-9][0-9][0-9][0-9][0-9][0-9].json")):
            try:
                seq = int(tx_p.stem)
                tx = json.loads(tx_p.read_text())
            except Exception:
                continue
            words = tx.get("words")
            if not isinstance(words, list):
                continue
            dur = float(tx.get("duration_s") or 60.0)
            chunks.append({
                "seq": seq,
                "duration_s": dur,
                "received_utc": tx.get("received_utc"),
                "words": words,
            })
        if chunks:
            streams[sdir.name] = chunks
    return streams


def _best_local_run(
    clip_norms: list[str],
    stream_words: list[dict],
    used_clip: list[bool],
    used_src: list[bool],
) -> tuple[int, list[tuple[int, int]]]:
    """Find the highest-scoring local Smith-Waterman run between unused clip and stream words."""
    m = len(clip_norms)
    n = len(stream_words)
    if m == 0 or n == 0:
        return 0, []

    match_score = 3
    mismatch_score = -2
    gap_Clip = -4  # disallow skipping clip words inside a single contiguous piece
    gap_Src = -4   # disallow skipping source words inside a single contiguous piece

    # H[i][j] holds score ending at clip[i-1], src[j-1]; trace: 1=diag, 2=up, 3=left
    H = [[0] * (n + 1) for _ in range(m + 1)]
    T = [[0] * (n + 1) for _ in range(m + 1)]

    best_score = 0
    best_pos = (0, 0)

    src_norms = [w["norm"] for w in stream_words]
    src_seqs = [w["seq"] for w in stream_words]

    for i in range(1, m + 1):
        if used_clip[i - 1] or not clip_norms[i - 1]:
            continue
        ci = clip_norms[i - 1]
        h_row = H[i]
        h_prev = H[i - 1]
        t_row = T[i]
        for j in range(1, n + 1):
            if used_src[j - 1] or not src_norms[j - 1]:
                continue
            # Keep diagonal transitions inside the same seq or adjacent seq without gaps
            if j > 1 and src_seqs[j - 1] != src_seqs[j - 2]:
                # Across chunk boundaries, only allow diagonal if it's contiguous in the word list
                pass
            sub = match_score if ci == src_norms[j - 1] else mismatch_score
            diag = h_prev[j - 1] + sub
            up = h_prev[j] + gap_Clip
            left = h_row[j - 1] + gap_Src

            val = diag
            tr = 1
            if up > val:
                val = up
                tr = 2
            if left > val:
                val = left
                tr = 3
            if val <= 0:
                val = 0
                tr = 0

            h_row[j] = val
            t_row[j] = tr
            if val > best_score:
                best_score = val
                best_pos = (i, j)

    if best_score < match_score:
        return 0, []

    pairs: list[tuple[int, int]] = []
    i, j = best_pos
    while i > 0 and j > 0 and H[i][j] > 0:
        tr = T[i][j]
        if tr == 1:
            pairs.append((i - 1, j - 1))
            i -= 1
            j -= 1
        elif tr == 2:
            i -= 1
        elif tr == 3:
            j -= 1
        else:
            break
    pairs.reverse()

    # Trim leading/trailing mismatches so piece boundaries land on exact word matches
    while pairs and clip_norms[pairs[0][0]] != src_norms[pairs[0][1]]:
        pairs.pop(0)
    while pairs and clip_norms[pairs[-1][0]] != src_norms[pairs[-1][1]]:
        pairs.pop()

    # Split if any two consecutive pairs had an internal gap or >=2 consecutive mismatches
    cleaned: list[tuple[int, int]] = []
    consec_mm = 0
    for k, (ci, sj) in enumerate(pairs):
        if k > 0:
            prev_c, prev_s = pairs[k - 1]
            if ci != prev_c + 1 or sj != prev_s + 1:
                break
        if clip_norms[ci] != src_norms[sj]:
            consec_mm += 1
            if consec_mm > 1:
                cleaned.pop()
                break
        else:
            consec_mm = 0
        cleaned.append((ci, sj))

    while cleaned and clip_norms[cleaned[-1][0]] != src_norms[cleaned[-1][1]]:
        cleaned.pop()

    return best_score, cleaned


def align_clip_words(
    clip_words: list[dict],
    streams: dict[str, list[dict]],
    clip_duration_s: float | None = None,
) -> dict:
    """Pure word-level alignment of `clip_words` against `streams`, returning the `witness` object."""
    transcript_clip = " ".join(w["w"].strip() for w in clip_words if w.get("w", "").strip())
    c_words = [
        {
            "w": w["w"].strip(),
            "s": float(w["s"]),
            "e": max(float(w["e"]), float(w["s"]) + 0.01),
            "norm": norm_word(w["w"]),
        }
        for w in clip_words
        if norm_word(w.get("w", ""))
    ]

    if not c_words or not streams:
        return {
            "verdict": "not_found",
            "stream": None,
            "pieces": [],
            "removed": [],
            "inserted": [],
            "transcript_clip": transcript_clip,
        }

    if clip_duration_s is None:
        clip_duration_s = max(w["e"] for w in c_words)

    flat_streams: dict[str, list[dict]] = {}
    chunk_dur_lookup: dict[tuple[str, int], float] = {}
    for sname, chunks in streams.items():
        flat: list[dict] = []
        offset = 0.0
        prev_seq = None
        for ch in sorted(chunks, key=lambda x: x["seq"]):
            seq = int(ch["seq"])
            dur = float(ch.get("duration_s") or 60.0)
            chunk_dur_lookup[(sname, seq)] = dur
            if prev_seq is not None and seq > prev_seq:
                offset += (seq - prev_seq) * dur
            elif prev_seq is None:
                offset = seq * dur
            prev_seq = seq
            for w in ch.get("words", []):
                nw = norm_word(w.get("w", ""))
                if not nw:
                    continue
                ws = float(w["s"])
                we = max(float(w["e"]), ws + 0.01)
                flat.append({
                    "stream": sname,
                    "seq": seq,
                    "w": w["w"].strip(),
                    "norm": nw,
                    "s": ws,
                    "e": we,
                    "abs_s": offset + ws,
                    "abs_e": offset + we,
                    "chunk_dur": dur,
                })
        if flat:
            flat_streams[sname] = flat

    if not flat_streams:
        return {
            "verdict": "not_found",
            "stream": None,
            "pieces": [],
            "removed": [],
            "inserted": [],
            "transcript_clip": transcript_clip,
        }

    clip_norms = [w["norm"] for w in c_words]

    # Iteratively extract maximal local runs across all available streams
    used_clip = [False] * len(c_words)
    used_src_by_stream = {sname: [False] * len(words) for sname, words in flat_streams.items()}
    runs: list[dict] = []

    while True:
        best_sname = None
        best_score = 0
        best_pairs: list[tuple[int, int]] = []
        for sname, s_words in flat_streams.items():
            sc, pairs = _best_local_run(clip_norms, s_words, used_clip, used_src_by_stream[sname])
            if len(pairs) >= 2 and sc > best_score:
                best_score = sc
                best_sname = sname
                best_pairs = pairs
        if best_sname is None or not best_pairs:
            break
        for ci, sj in best_pairs:
            used_clip[ci] = True
            used_src_by_stream[best_sname][sj] = True
        runs.append({
            "stream": best_sname,
            "pairs": best_pairs,
            "clip_start_idx": best_pairs[0][0],
            "clip_end_idx": best_pairs[-1][0],
            "src_start_idx": best_pairs[0][1],
            "src_end_idx": best_pairs[-1][1],
        })

    # Rescue single-word boundary matches adjacent to existing runs if separated by a short cut
    runs.sort(key=lambda r: r["clip_start_idx"])
    total_aligned = sum(len(r["pairs"]) for r in runs)
    if total_aligned / float(len(c_words)) < 0.60:
        return {
            "verdict": "not_found",
            "stream": None,
            "pieces": [],
            "removed": [],
            "inserted": [],
            "transcript_clip": transcript_clip,
        }

    stream_counts: dict[str, int] = {}
    for r in runs:
        stream_counts[r["stream"]] = stream_counts.get(r["stream"], 0) + len(r["pairs"])
    primary_stream = max(stream_counts.items(), key=lambda kv: kv[1])[0]
    multi_stream = len(stream_counts) > 1

    # Build pieces (splitting any run that crosses a chunk seq boundary)
    pieces: list[dict] = []
    for r in runs:
        sname = r["stream"]
        s_words = flat_streams[sname]
        sub_runs: list[list[tuple[int, int]]] = []
        for ci, sj in r["pairs"]:
            if not sub_runs or s_words[sj]["seq"] != s_words[sub_runs[-1][-1][1]]["seq"]:
                sub_runs.append([(ci, sj)])
            else:
                sub_runs[-1].append((ci, sj))
        for sr in sub_runs:
            c0, s0 = sr[0]
            c1, s1 = sr[-1]
            seq = s_words[s0]["seq"]
            c_start = round(max(0.0, c_words[c0]["s"]), 3)
            c_end = round(min(float(clip_duration_s), max(c_start + 0.01, c_words[c1]["e"])), 3)
            src_start = round(max(0.0, s_words[s0]["s"]), 3)
            max_cdur = chunk_dur_lookup.get((sname, seq), 60.0)
            src_end = round(min(max_cdur, max(src_start + 0.01, s_words[s1]["e"])), 3)
            piece = {
                "clip_s": [c_start, c_end],
                "seq": seq,
                "src_s": [src_start, src_end],
                "audio_score": None,
            }
            if multi_stream:
                piece["stream"] = sname
            pieces.append(piece)

    # Identify inserted clip runs (unaligned clip words)
    inserted: list[dict] = []
    idx = 0
    while idx < len(c_words):
        if used_clip[idx]:
            idx += 1
            continue
        j = idx
        while j < len(c_words) and not used_clip[j]:
            j += 1
        span_words = c_words[idx:j]
        c_start = round(max(0.0, span_words[0]["s"]), 3)
        c_end = round(min(float(clip_duration_s), max(c_start + 0.01, span_words[-1]["e"])), 3)
        inserted.append({
            "clip_s": [c_start, c_end],
            "text": " ".join(w["w"] for w in span_words),
        })
        idx = j

    # Analyse transitions between consecutive runs for cuts vs splices
    removed: list[dict] = []
    is_spliced = multi_stream

    for k in range(len(runs) - 1):
        r_prev = runs[k]
        r_next = runs[k + 1]
        if r_prev["stream"] != r_next["stream"]:
            is_spliced = True
            continue
        sname = r_prev["stream"]
        s_words = flat_streams[sname]
        prev_s_idx = r_prev["src_end_idx"]
        next_s_idx = r_next["src_start_idx"]

        prev_w = s_words[prev_s_idx]
        next_w = s_words[next_s_idx]
        gap_s = next_w["abs_s"] - prev_w["abs_e"]

        if next_s_idx <= prev_s_idx or gap_s < -0.25 or gap_s > 60.0:
            is_spliced = True
            continue

        if next_s_idx > prev_s_idx + 1:
            skipped = s_words[prev_s_idx + 1 : next_s_idx]
            seam_clip_s = round(c_words[r_prev["clip_end_idx"]]["e"], 3)
            # Group skipped words by seq
            by_seq: dict[int, list[dict]] = {}
            for sw in skipped:
                by_seq.setdefault(sw["seq"], []).append(sw)
            for seq, sw_list in sorted(by_seq.items()):
                max_cdur = chunk_dur_lookup.get((sname, seq), 60.0)
                s0 = round(max(0.0, sw_list[0]["s"]), 3)
                s1 = round(min(max_cdur, max(s0, sw_list[-1]["e"])), 3)
                removed.append({
                    "after_clip_s": seam_clip_s,
                    "seq": seq,
                    "src_s": [s0, s1],
                    "words": " ".join(w["w"] for w in sw_list),
                })

    if is_spliced:
        verdict = "spliced"
    elif removed or inserted:
        verdict = "cut" if removed else "spliced"
    else:
        verdict = "faithful"

    return {
        "verdict": verdict,
        "stream": primary_stream,
        "pieces": pieces,
        "removed": removed,
        "inserted": inserted,
        "transcript_clip": transcript_clip,
    }


def detect_file(clip_wav: Path, witness_root: Path) -> dict:
    duration_s = _wav_duration_s(clip_wav)
    sha = sha256_file(clip_wav)
    clip_words = transcribe_clip(clip_wav)
    streams = load_witness_streams(witness_root)
    witness = align_clip_words(clip_words, streams, clip_duration_s=duration_s)
    return {
        "detector": {"name": "ref-text-only", "version": "0.1"},
        "clip": {"duration_s": duration_s, "sha256": sha},
        "stamp": None,
        "witness": witness,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
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
        print(f"ERROR: ref_detect failed: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
