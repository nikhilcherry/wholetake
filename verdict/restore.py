"""Reconstruct excised broadcast audio ("Hear what they cut") and before/after witness context."""
import io
import json
import wave
from pathlib import Path

from ingest.chain import write_atomic

SILENCE_PAD_S = 0.12  # 120 ms silence on each side of a restored excised span


class RestoreUnavailable(Exception):
    """Raised when witness audio required to reconstruct a cut or context window is missing."""


def _read_wav_info_and_frames(path: Path) -> tuple[int, int, int, bytes]:
    if not path.exists() or not path.is_file():
        raise RestoreUnavailable(f"wav file not on disk: {path}")
    with wave.open(str(path), "rb") as wf:
        ch = wf.getnchannels()
        sw = wf.getsampwidth()
        sr = wf.getframerate()
        nframes = wf.getnframes()
        raw = wf.readframes(nframes)
    frame_bytes = ch * sw
    actual_frames = len(raw) // frame_bytes if frame_bytes > 0 else 0
    return ch, sw, sr, raw[: actual_frames * frame_bytes]


def _write_wav_atomic(path: Path, ch: int, sw: int, sr: int, pcm: bytes) -> None:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(ch)
        wf.setsampwidth(sw)
        wf.setframerate(sr)
        wf.writeframes(pcm)
    write_atomic(path, buf.getvalue())


def _load_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        obj = json.loads(path.read_text())
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


def _chunk_wav_path(witness_root: Path, stream: str, seq: int) -> Path:
    return witness_root / stream / f"{seq:06d}.wav"


def _chunk_json_path(witness_root: Path, stream: str, seq: int) -> Path:
    return witness_root / stream / f"{seq:06d}.json"


def _chunk_gap_path(witness_root: Path, stream: str, seq: int) -> Path:
    return witness_root / stream / f"{seq:06d}.gap.json"


def select_span_word_indices(wlist: list[dict], s0: float, s1: float) -> list[int]:
    """Select transcript word indices overlapping [s0, s1] with shared boundary tolerance."""
    hits = [
        i
        for i, w in enumerate(wlist)
        if isinstance(w, dict)
        and str(w.get("w") or "").strip()
        and float(w.get("e", 0.0)) > s0 + 0.01
        and float(w.get("s", 0.0)) < s1 - 0.01
    ]
    if not hits:
        hits = [
            i
            for i, w in enumerate(wlist)
            if isinstance(w, dict)
            and str(w.get("w") or "").strip()
            and float(w.get("e", 0.0)) >= s0 - 0.02
            and float(w.get("s", 0.0)) <= s1 + 0.02
        ]
    return hits


def _normalize_retimed_words(words: list[dict], seg_t0: float, seg_t1: float) -> list[dict]:
    """Ensure non-overlapping, contiguous [t0, t1) word intervals across a restored segment for karaoke."""
    if not words:
        return []
    out: list[dict] = []
    for w in words:
        t0 = max(seg_t0, min(seg_t1, float(w["t0"])))
        t1 = max(t0, min(seg_t1, float(w["t1"])))
        out.append({"w": str(w["w"]), "t0": round(t0, 6), "t1": round(t1, 6)})

    # If the first word starts within 30ms of the segment start, snap it to seg_t0 so there is no dead lead-in
    if out[0]["t0"] - seg_t0 <= 0.03:
        out[0]["t0"] = round(seg_t0, 6)

    for i in range(len(out) - 1):
        next_t0 = out[i + 1]["t0"]
        if next_t0 < out[i]["t0"]:
            next_t0 = min(seg_t1, out[i]["t0"] + 0.04)
            out[i + 1]["t0"] = round(next_t0, 6)
        # Close inter-word gaps or DTW overlaps so exactly one word is active at any time t in [t0, next_t0)
        out[i]["t1"] = round(next_t0, 6)

    # Extend final word to the end of the restored segment if it ends within 150ms or had zero duration
    if seg_t1 - out[-1]["t1"] <= 0.15 or out[-1]["t1"] <= out[-1]["t0"]:
        out[-1]["t1"] = round(seg_t1, 6)

    return out


def _extract_witness_span(
    witness_root: Path,
    stream: str,
    start_seq: int,
    s0: float,
    s1: float,
    expected_params: tuple[int, int, int],
) -> tuple[bytes, list[dict]]:
    """Read PCM frames and transcript words for [s0, s1] relative to `start_seq`, crossing into seq+1 if needed."""
    ch, sw, sr = expected_params
    frame_bytes = ch * sw
    target_dur = max(0.0, s1 - s0)
    target_frames = int(round(target_dur * sr))
    if target_frames <= 0:
        return b"", []

    pcm_parts: list[bytes] = []
    words_out: list[dict] = []
    frames_collected = 0
    cur_seq = start_seq
    cur_offset_s = max(0.0, s0)
    elapsed_out_s = 0.0

    while frames_collected < target_frames:
        wpath = _chunk_wav_path(witness_root, stream, cur_seq)
        if not wpath.exists():
            raise RestoreUnavailable(f"witness audio for seq {cur_seq} not on disk")
        w_ch, w_sw, w_sr, w_raw = _read_wav_info_and_frames(wpath)
        if (w_ch, w_sw, w_sr) != (ch, sw, sr):
            raise ValueError(
                f"sample format mismatch in seq {cur_seq}: got {(w_ch, w_sw, w_sr)}, expected {(ch, sw, sr)}"
            )
        total_chunk_frames = len(w_raw) // frame_bytes
        chunk_dur_s = total_chunk_frames / sr
        start_frame = min(total_chunk_frames, max(0, int(round(cur_offset_s * sr))))
        avail_frames = total_chunk_frames - start_frame
        if avail_frames <= 0:
            cur_offset_s = max(0.0, cur_offset_s - chunk_dur_s)
            cur_seq += 1
            continue

        need_frames = target_frames - frames_collected
        take_frames = min(avail_frames, need_frames)
        end_frame = start_frame + take_frames
        pcm_parts.append(w_raw[start_frame * frame_bytes : end_frame * frame_bytes])

        seg_local_s0 = start_frame / sr
        seg_local_s1 = end_frame / sr
        tx = _load_json(_chunk_json_path(witness_root, stream, cur_seq)) or {}
        wlist = tx.get("words") or []
        hits = select_span_word_indices(wlist, seg_local_s0, seg_local_s1)
        for i in hits:
            w = wlist[i]
            ws = float(w.get("s", 0.0))
            we = float(w.get("e", 0.0))
            w_text = str(w.get("w") or "").strip()
            rel_t0 = max(0.0, ws - seg_local_s0) + elapsed_out_s
            rel_t1 = min(seg_local_s1 - seg_local_s0, max(ws + 0.02, we) - seg_local_s0) + elapsed_out_s
            if rel_t1 > rel_t0:
                words_out.append({"w": w_text, "rel_t0": rel_t0, "rel_t1": rel_t1})

        frames_collected += take_frames
        elapsed_out_s = frames_collected / sr
        cur_offset_s = 0.0
        cur_seq += 1

    return b"".join(pcm_parts), words_out


def can_restore(witness_root: Path, stream: str | None, vkind: str, removed: list[dict]) -> bool:
    """Fast render-time check: True when verdict is 'cut', has >=1 removed span, and every needed witness wav exists."""
    if vkind != "cut" or not stream or not removed:
        return False
    for rem in removed:
        if not isinstance(rem, dict):
            return False
        seq = rem.get("seq")
        src_s = rem.get("src_s")
        if not isinstance(seq, int) or not isinstance(src_s, list) or len(src_s) < 2:
            return False
        s0, s1 = float(src_s[0]), float(src_s[1])
        if s1 <= s0:
            return False
        cur_seq = seq
        rem_end = s1
        while True:
            wpath = _chunk_wav_path(witness_root, stream, cur_seq)
            if not wpath.exists() or not wpath.is_file():
                return False
            try:
                with wave.open(str(wpath), "rb") as wf:
                    sr = wf.getframerate()
                    nframes = wf.getnframes()
                chunk_dur = nframes / sr if sr > 0 else 20.0
            except Exception:
                return False
            if chunk_dur <= 0:
                return False
            if rem_end <= chunk_dur + 1e-6:
                break
            rem_end -= chunk_dur
            cur_seq += 1
    return True


def build_restored(
    clip_wav: Path | str,
    witness_root: Path | str,
    stream: str,
    removed: list[dict],
    out_wav: Path | str,
) -> dict:
    """Splice 120 ms silence + witness record audio + 120 ms silence into `clip_wav` at each removed seam."""
    cpath = Path(clip_wav)
    wroot = Path(witness_root)
    opath = Path(out_wav)
    if not stream:
        raise RestoreUnavailable("missing witness stream")

    ch, sw, sr, clip_pcm = _read_wav_info_and_frames(cpath)
    frame_bytes = ch * sw
    total_clip_frames = len(clip_pcm) // frame_bytes
    clip_dur_s = total_clip_frames / sr

    indexed_removed = [
        (orig_idx, r) for orig_idx, r in enumerate(removed) if isinstance(r, dict)
    ]
    indexed_removed.sort(
        key=lambda item: (float(item[1].get("after_clip_s") or 0.0), int(item[1].get("seq") or 0))
    )

    silence_frames = int(round(SILENCE_PAD_S * sr))
    silence_pcm = b"\x00" * (silence_frames * frame_bytes)

    out_pcm_parts: list[bytes] = []
    segments: list[dict] = []
    cur_clip_frame = 0
    out_frames = 0

    for orig_idx, rem in indexed_removed:
        seq = rem.get("seq")
        src_s = rem.get("src_s") or [0.0, 0.0]
        if not isinstance(seq, int) or len(src_s) < 2:
            raise RestoreUnavailable("invalid removed span entry")
        s0, s1 = float(src_s[0]), float(src_s[1])
        seam_s = max(0.0, min(clip_dur_s, float(rem.get("after_clip_s") or 0.0)))
        seam_frame = min(total_clip_frames, max(cur_clip_frame, int(round(seam_s * sr))))

        # 1. Clip segment before this seam
        if seam_frame > cur_clip_frame:
            seg_frames = seam_frame - cur_clip_frame
            t0 = out_frames / sr
            t1 = (out_frames + seg_frames) / sr
            c0 = cur_clip_frame / sr
            c1 = seam_frame / sr
            out_pcm_parts.append(clip_pcm[cur_clip_frame * frame_bytes : seam_frame * frame_bytes])
            segments.append(
                {
                    "kind": "clip",
                    "t0": round(t0, 6),
                    "t1": round(t1, 6),
                    "clip_s": [round(c0, 6), round(c1, 6)],
                }
            )
            out_frames += seg_frames
            cur_clip_frame = seam_frame

        # 2. Leading 120 ms silence
        if silence_frames > 0:
            t0 = out_frames / sr
            t1 = (out_frames + silence_frames) / sr
            out_pcm_parts.append(silence_pcm)
            segments.append({"kind": "silence", "t0": round(t0, 6), "t1": round(t1, 6)})
            out_frames += silence_frames

        # 3. Excised witness audio
        excised_pcm, rel_words = _extract_witness_span(wroot, stream, seq, s0, s1, (ch, sw, sr))
        excised_frames = len(excised_pcm) // frame_bytes
        t0 = out_frames / sr
        t1 = (out_frames + excised_frames) / sr
        out_pcm_parts.append(excised_pcm)

        raw_retimed = [
            {
                "w": w["w"],
                "t0": round(t0 + w["rel_t0"], 6),
                "t1": round(t0 + w["rel_t1"], 6),
            }
            for w in rel_words
        ]
        if not raw_retimed and str(rem.get("words") or "").strip():
            fallback_tokens = [tok for tok in str(rem.get("words") or "").strip().split() if tok]
            span_dur = max(0.01, t1 - t0)
            step = span_dur / len(fallback_tokens)
            for wi, tok in enumerate(fallback_tokens):
                raw_retimed.append(
                    {
                        "w": tok,
                        "t0": round(t0 + wi * step, 6),
                        "t1": round(t0 + (wi + 1) * step, 6),
                    }
                )
        retimed_words = _normalize_retimed_words(raw_retimed, t0, t1)

        segments.append(
            {
                "kind": "restored",
                "cut_index": orig_idx,
                "t0": round(t0, 6),
                "t1": round(t1, 6),
                "after_clip_s": round(seam_s, 6),
                "seq": seq,
                "src_s": [round(s0, 6), round(s1, 6)],
                "words": retimed_words,
            }
        )
        out_frames += excised_frames

        # 4. Trailing 120 ms silence
        if silence_frames > 0:
            t0 = out_frames / sr
            t1 = (out_frames + silence_frames) / sr
            out_pcm_parts.append(silence_pcm)
            segments.append({"kind": "silence", "t0": round(t0, 6), "t1": round(t1, 6)})
            out_frames += silence_frames

    # Remaining clip tail after the final cut seam
    if cur_clip_frame < total_clip_frames:
        seg_frames = total_clip_frames - cur_clip_frame
        t0 = out_frames / sr
        t1 = (out_frames + seg_frames) / sr
        c0 = cur_clip_frame / sr
        c1 = total_clip_frames / sr
        out_pcm_parts.append(clip_pcm[cur_clip_frame * frame_bytes : total_clip_frames * frame_bytes])
        segments.append(
            {
                "kind": "clip",
                "t0": round(t0, 6),
                "t1": round(t1, 6),
                "clip_s": [round(c0, 6), round(c1, 6)],
            }
        )
        out_frames += seg_frames

    _write_wav_atomic(opath, ch, sw, sr, b"".join(out_pcm_parts))
    return {
        "duration_s": round(out_frames / sr, 6),
        "sample_rate": sr,
        "segments": segments,
    }


def preview_restored_manifest(
    witness_root: Path | str,
    stream: str,
    clip_dur_s: float,
    removed: list[dict],
    sr: int = 16000,
) -> dict:
    """Compute the restored manifest without reading `clip.wav` so the page can embed it with zero fetch lag."""
    wroot = Path(witness_root)
    total_clip_frames = max(1, int(round(clip_dur_s * sr)))
    silence_frames = int(round(SILENCE_PAD_S * sr))

    indexed_removed = [
        (orig_idx, r) for orig_idx, r in enumerate(removed) if isinstance(r, dict)
    ]
    indexed_removed.sort(
        key=lambda item: (float(item[1].get("after_clip_s") or 0.0), int(item[1].get("seq") or 0))
    )

    segments: list[dict] = []
    cur_clip_frame = 0
    out_frames = 0

    for orig_idx, rem in indexed_removed:
        seq = rem.get("seq")
        src_s = rem.get("src_s") or [0.0, 0.0]
        if not isinstance(seq, int) or len(src_s) < 2:
            continue
        s0, s1 = float(src_s[0]), float(src_s[1])
        seam_s = max(0.0, min(clip_dur_s, float(rem.get("after_clip_s") or 0.0)))
        seam_frame = min(total_clip_frames, max(cur_clip_frame, int(round(seam_s * sr))))

        if seam_frame > cur_clip_frame:
            seg_frames = seam_frame - cur_clip_frame
            segments.append(
                {
                    "kind": "clip",
                    "t0": round(out_frames / sr, 6),
                    "t1": round((out_frames + seg_frames) / sr, 6),
                    "clip_s": [round(cur_clip_frame / sr, 6), round(seam_frame / sr, 6)],
                }
            )
            out_frames += seg_frames
            cur_clip_frame = seam_frame

        if silence_frames > 0:
            segments.append(
                {
                    "kind": "silence",
                    "t0": round(out_frames / sr, 6),
                    "t1": round((out_frames + silence_frames) / sr, 6),
                }
            )
            out_frames += silence_frames

        excised_pcm, rel_words = _extract_witness_span(wroot, stream, seq, s0, s1, (1, 2, sr))
        excised_frames = len(excised_pcm) // 2
        t0 = out_frames / sr
        t1 = (out_frames + excised_frames) / sr

        raw_retimed = [
            {
                "w": w["w"],
                "t0": round(t0 + w["rel_t0"], 6),
                "t1": round(t0 + w["rel_t1"], 6),
            }
            for w in rel_words
        ]
        if not raw_retimed and str(rem.get("words") or "").strip():
            fallback_tokens = [tok for tok in str(rem.get("words") or "").strip().split() if tok]
            span_dur = max(0.01, t1 - t0)
            step = span_dur / len(fallback_tokens)
            for wi, tok in enumerate(fallback_tokens):
                raw_retimed.append(
                    {
                        "w": tok,
                        "t0": round(t0 + wi * step, 6),
                        "t1": round(t0 + (wi + 1) * step, 6),
                    }
                )
        retimed_words = _normalize_retimed_words(raw_retimed, t0, t1)

        segments.append(
            {
                "kind": "restored",
                "cut_index": orig_idx,
                "t0": round(t0, 6),
                "t1": round(t1, 6),
                "after_clip_s": round(seam_s, 6),
                "seq": seq,
                "src_s": [round(s0, 6), round(s1, 6)],
                "words": retimed_words,
            }
        )
        out_frames += excised_frames

        if silence_frames > 0:
            segments.append(
                {
                    "kind": "silence",
                    "t0": round(out_frames / sr, 6),
                    "t1": round((out_frames + silence_frames) / sr, 6),
                }
            )
            out_frames += silence_frames

    if cur_clip_frame < total_clip_frames:
        seg_frames = total_clip_frames - cur_clip_frame
        segments.append(
            {
                "kind": "clip",
                "t0": round(out_frames / sr, 6),
                "t1": round((out_frames + seg_frames) / sr, 6),
                "clip_s": [round(cur_clip_frame / sr, 6), round(total_clip_frames / sr, 6)],
            }
        )
        out_frames += seg_frames

    return {
        "duration_s": round(out_frames / sr, 6),
        "sample_rate": sr,
        "segments": segments,
    }


def _chunk_duration_s(witness_root: Path, stream: str, seq: int, tx: dict | None = None) -> float:
    if tx is None:
        tx = _load_json(_chunk_json_path(witness_root, stream, seq))
    if isinstance(tx, dict) and tx.get("duration_s") is not None:
        try:
            d = float(tx["duration_s"])
            if d > 0:
                return d
        except Exception:
            pass
    wpath = _chunk_wav_path(witness_root, stream, seq)
    if wpath.exists():
        try:
            with wave.open(str(wpath), "rb") as wf:
                sr = wf.getframerate()
                nf = wf.getnframes()
                if sr > 0 and nf > 0:
                    return nf / sr
        except Exception:
            pass
    return 20.0


def edge_context(
    witness_root: Path | str,
    stream: str | None,
    pieces: list[dict],
    window_s: float = 15.0,
) -> dict[str, dict | None]:
    """Gather up to `window_s` of transcript words and audio spans immediately before and after `pieces`."""
    wroot = Path(witness_root)
    valid_pieces = [
        p
        for p in (pieces or [])
        if isinstance(p, dict) and isinstance(p.get("seq"), int) and isinstance(p.get("src_s"), list) and len(p["src_s"]) >= 2
    ]
    if not stream or not valid_pieces or window_s <= 0:
        return {"before": None, "after": None}

    earliest = min(valid_pieces, key=lambda p: (int(p["seq"]), float(p["src_s"][0])))
    latest = max(valid_pieces, key=lambda p: (int(p["seq"]), float(p["src_s"][1])))

    before_ctx = _collect_before(
        wroot, stream, int(earliest["seq"]), max(0.0, float(earliest["src_s"][0])), float(window_s)
    )
    after_ctx = _collect_after(
        wroot, stream, int(latest["seq"]), max(0.0, float(latest["src_s"][1])), float(window_s)
    )
    return {"before": before_ctx, "after": after_ctx}


def _collect_before(
    witness_root: Path, stream: str, start_seq: int, start_pos_s: float, window_s: float
) -> dict:
    rem = window_s
    cur_seq = start_seq
    cur_end = start_pos_s
    spans_rev: list[dict] = []
    words_rev: list[list[dict]] = []
    stops: dict | None = None

    while rem > 1e-6:
        if cur_seq < 0:
            stops = {"kind": "stream_start"}
            break

        if cur_seq != start_seq:
            gap = _load_json(_chunk_gap_path(witness_root, stream, cur_seq))
            if gap is not None and gap.get("kind") == "gap":
                stops = {
                    "kind": "gap",
                    "seq": cur_seq,
                    "from_utc": gap.get("from_utc"),
                    "to_utc": gap.get("to_utc"),
                }
                break
            tx = _load_json(_chunk_json_path(witness_root, stream, cur_seq))
            wpath = _chunk_wav_path(witness_root, stream, cur_seq)
            if tx is None and not wpath.exists():
                stops = {"kind": "stream_start"}
                break
            cur_end = _chunk_duration_s(witness_root, stream, cur_seq, tx)
        else:
            tx = _load_json(_chunk_json_path(witness_root, stream, cur_seq))

        take = min(cur_end, rem)
        s0 = max(0.0, cur_end - take)
        s1 = cur_end
        if s1 > s0 + 1e-6:
            spans_rev.append({"seq": cur_seq, "src_s": [round(s0, 6), round(s1, 6)]})
            chunk_words: list[dict] = []
            for w in (tx or {}).get("words") or []:
                if not isinstance(w, dict):
                    continue
                ws = float(w.get("s", 0.0))
                we = float(w.get("e", 0.0))
                wt = str(w.get("w") or "").strip()
                if wt and we > s0 + 0.01 and ws < s1 - 0.01:
                    chunk_words.append({"w": wt, "seq": cur_seq, "s": round(ws, 3), "e": round(we, 3)})
            words_rev.append(chunk_words)

        rem -= take
        if rem <= 1e-6:
            break
        if cur_seq == 0:
            stops = {"kind": "stream_start"}
            break
        cur_seq -= 1

    spans = list(reversed(spans_rev))
    words: list[dict] = []
    for cw in reversed(words_rev):
        words.extend(cw)

    audio_available = bool(spans) and all(
        _chunk_wav_path(witness_root, stream, int(sp["seq"])).exists() for sp in spans
    )
    total_dur = round(sum(float(sp["src_s"][1]) - float(sp["src_s"][0]) for sp in spans), 3)
    return {
        "words": words,
        "stops": stops,
        "audio": spans,
        "audio_available": audio_available,
        "duration_s": total_dur,
    }


def _collect_after(
    witness_root: Path, stream: str, start_seq: int, start_pos_s: float, window_s: float
) -> dict:
    rem = window_s
    cur_seq = start_seq
    cur_start = start_pos_s
    spans: list[dict] = []
    words: list[dict] = []
    stops: dict | None = None

    while rem > 1e-6:
        if cur_seq != start_seq:
            gap = _load_json(_chunk_gap_path(witness_root, stream, cur_seq))
            if gap is not None and gap.get("kind") == "gap":
                stops = {
                    "kind": "gap",
                    "seq": cur_seq,
                    "from_utc": gap.get("from_utc"),
                    "to_utc": gap.get("to_utc"),
                }
                break
            tx = _load_json(_chunk_json_path(witness_root, stream, cur_seq))
            wpath = _chunk_wav_path(witness_root, stream, cur_seq)
            if tx is None and not wpath.exists():
                stops = {"kind": "live_edge"}
                break
        else:
            tx = _load_json(_chunk_json_path(witness_root, stream, cur_seq))

        chunk_dur = _chunk_duration_s(witness_root, stream, cur_seq, tx)
        avail = max(0.0, chunk_dur - cur_start)
        take = min(avail, rem)
        s0 = cur_start
        s1 = cur_start + take
        if s1 > s0 + 1e-6:
            spans.append({"seq": cur_seq, "src_s": [round(s0, 6), round(s1, 6)]})
            for w in (tx or {}).get("words") or []:
                if not isinstance(w, dict):
                    continue
                ws = float(w.get("s", 0.0))
                we = float(w.get("e", 0.0))
                wt = str(w.get("w") or "").strip()
                if wt and we > s0 + 0.01 and ws < s1 - 0.01:
                    words.append({"w": wt, "seq": cur_seq, "s": round(ws, 3), "e": round(we, 3)})

        rem -= take
        if rem <= 1e-6:
            break
        cur_seq += 1
        cur_start = 0.0

    audio_available = bool(spans) and all(
        _chunk_wav_path(witness_root, stream, int(sp["seq"])).exists() for sp in spans
    )
    total_dur = round(sum(float(sp["src_s"][1]) - float(sp["src_s"][0]) for sp in spans), 3)
    return {
        "words": words,
        "stops": stops,
        "audio": spans,
        "audio_available": audio_available,
        "duration_s": total_dur,
    }


def build_context_wav(
    witness_root: Path | str,
    stream: str,
    audio_spans: list[dict],
) -> bytes:
    """Concatenate witness wav slices across `audio_spans` and return a complete WAV file as bytes."""
    wroot = Path(witness_root)
    if not stream or not audio_spans:
        raise RestoreUnavailable("no context audio spans available")

    ch = sw = sr = 0
    pcm_parts: list[bytes] = []
    for sp in audio_spans:
        seq = int(sp["seq"])
        s0, s1 = float(sp["src_s"][0]), float(sp["src_s"][1])
        wpath = _chunk_wav_path(wroot, stream, seq)
        if not wpath.exists():
            raise RestoreUnavailable(f"witness audio for seq {seq} not on disk")
        w_ch, w_sw, w_sr, w_raw = _read_wav_info_and_frames(wpath)
        if sr == 0:
            ch, sw, sr = w_ch, w_sw, w_sr
        elif (w_ch, w_sw, w_sr) != (ch, sw, sr):
            raise ValueError(
                f"sample format mismatch in seq {seq}: got {(w_ch, w_sw, w_sr)}, expected {(ch, sw, sr)}"
            )
        frame_bytes = ch * sw
        total_frames = len(w_raw) // frame_bytes
        f0 = min(total_frames, max(0, int(round(s0 * sr))))
        f1 = min(total_frames, max(f0, int(round(s1 * sr))))
        if f1 > f0:
            pcm_parts.append(w_raw[f0 * frame_bytes : f1 * frame_bytes])

    pcm = b"".join(pcm_parts)
    if not pcm or sr == 0:
        raise RestoreUnavailable("context audio span is empty")

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(ch)
        wf.setsampwidth(sw)
        wf.setframerate(sr)
        wf.writeframes(pcm)
    return buf.getvalue()
