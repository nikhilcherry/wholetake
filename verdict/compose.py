"""Compose the final tamper-evident verdict.json with coverage checks and deterministic headlines."""
from datetime import datetime
import json
from pathlib import Path

from ingest import chain
from ingest.chain import canonical_json, sha256_bytes, utc_now
from verdict.detect import detector_kind

# Number of record words shown on each side of a removed span in the headline quote
HEADLINE_CONTEXT_WORDS = 3


def _fmt_hms(utc_str: str | None) -> str:
    if not isinstance(utc_str, str) or not utc_str:
        return "unknown"
    try:
        dt = datetime.fromisoformat(utc_str.replace("Z", "+00:00"))
        return dt.strftime("%H:%M:%S")
    except Exception:
        return utc_str


def _fmt_date_hm_utc(utc_str: str | None) -> str | None:
    if not isinstance(utc_str, str) or not utc_str:
        return None
    try:
        dt = datetime.fromisoformat(utc_str.replace("Z", "+00:00"))
        return f"{dt.day} {dt.strftime('%b %H:%M')} UTC"
    except Exception:
        return None


def _fmt_mmss(sec: float) -> str:
    sec = max(0.0, float(sec))
    total_s = int(round(sec))
    m, s = divmod(total_s, 60)
    return f"{m}:{s:02d}"


EDGE_S = 1.0


def _chunk_duration(sdir: Path, seq: int) -> float | None:
    try:
        return float(json.loads((sdir / f"{seq:06d}.meta.json").read_text())["duration_s"])
    except Exception:
        return None


def compute_coverage(detection: dict, witness_root: Path | str) -> dict:
    """Check whether matched pieces touch any recorded gap or lie after the last chained entry."""
    wit = detection.get("witness") or {}
    stream = wit.get("stream")
    pieces = wit.get("pieces") or []
    removed = wit.get("removed") or []

    if not stream or not pieces:
        return {"complete": False, "gaps": [], "notes": []}

    sdir = Path(witness_root) / stream
    entries = chain.load(sdir) if sdir.exists() else []

    referenced_seqs = sorted({
        int(x["seq"])
        for x in [*pieces, *removed]
        if isinstance(x.get("seq"), int)
    })
    if not referenced_seqs:
        return {"complete": False, "gaps": [], "notes": []}

    # Gaps strictly inside the matched span always matter. A gap right before/after it only matters when the
    # matched audio reaches that chunk edge (within EDGE_S): only then could the clip's context be in the gap.
    spans = [(int(x["seq"]), float(x["src_s"][0]), float(x["src_s"][1]))
             for x in [*pieces, *removed] if isinstance(x.get("seq"), int) and x.get("src_s")]
    first_seq, last_seq = referenced_seqs[0], referenced_seqs[-1]
    starts_at_edge = any(q == first_seq and a < EDGE_S for q, a, _ in spans)
    last_dur = _chunk_duration(sdir, last_seq)
    ends_at_edge = last_dur is None or any(q == last_seq and b > last_dur - EDGE_S for q, _, b in spans)
    min_seq = first_seq - 1 if starts_at_edge and first_seq > 0 else first_seq
    max_seq = last_seq + 1 if ends_at_edge else last_seq

    last_chained_seq = -1
    chained_gaps_by_seq: dict[int, dict] = {}
    for e in entries:
        if e.get("kind") in ("chunk", "gap") and isinstance(e.get("seq"), int):
            last_chained_seq = max(last_chained_seq, e["seq"])
        if e.get("kind") == "gap" and isinstance(e.get("seq"), int):
            chained_gaps_by_seq[e["seq"]] = e

    gaps: list[dict] = []
    notes: list[str] = []

    for seq in range(min_seq, max_seq + 1):
        gap_p = sdir / f"{seq:06d}.gap.json"
        if seq in chained_gaps_by_seq or gap_p.exists():
            gap_obj: dict = {}
            if gap_p.exists():
                try:
                    gap_obj = json.loads(gap_p.read_text())
                except Exception:
                    gap_obj = {}
            from_u = gap_obj.get("from_utc") or chained_gaps_by_seq.get(seq, {}).get("from_utc")
            to_u = gap_obj.get("to_utc") or chained_gaps_by_seq.get(seq, {}).get("to_utc")
            reason = gap_obj.get("reason") or "stream interrupted"
            msg = f"witness was offline {_fmt_hms(from_u)}–{_fmt_hms(to_u)}: {reason}"
            gaps.append({
                "seq": seq,
                "from_utc": from_u,
                "to_utc": to_u,
                "reason": reason,
                "message": msg,
            })
            notes.append(msg)

    for seq in referenced_seqs:
        if last_chained_seq == -1 or seq > last_chained_seq:
            msg = f"seq {seq} lies after the stream's last chained entry (last_seq={last_chained_seq})"
            notes.append(msg)

    return {
        "complete": len(notes) == 0,
        "last_chained_seq": last_chained_seq,
        "gaps": gaps,
        "notes": notes,
    }


def _record_snippet(witness_root: Path, stream: str | None, rem: dict) -> str:
    """Return the removed words plus HEADLINE_CONTEXT_WORDS (3) record words on each side, wrapped in ellipses."""
    words_removed = str(rem.get("words") or "").strip()
    seq = rem.get("seq")
    src_s = rem.get("src_s") or [0.0, 0.0]
    s0, s1 = float(src_s[0]), float(src_s[1])

    if stream and isinstance(seq, int):
        tx_p = Path(witness_root) / stream / f"{seq:06d}.json"
        if tx_p.exists():
            try:
                tx = json.loads(tx_p.read_text())
                wlist = tx.get("words") or []
                # Match words whose interval overlaps [s0, s1] by more than a 50ms tolerance
                hits = [
                    i
                    for i, w in enumerate(wlist)
                    if float(w.get("e", 0.0)) > s0 + 0.02 and float(w.get("s", 0.0)) < s1 - 0.02
                ]
                if not hits:
                    hits = [
                        i
                        for i, w in enumerate(wlist)
                        if float(w.get("e", 0.0)) >= s0 - 0.02 and float(w.get("s", 0.0)) <= s1 + 0.02
                    ]
                if hits:
                    lo = max(0, hits[0] - HEADLINE_CONTEXT_WORDS)
                    hi = min(len(wlist), hits[-1] + 1 + HEADLINE_CONTEXT_WORDS)
                    snippet = " ".join(str(w.get("w", "")).strip() for w in wlist[lo:hi]).strip()
                    if snippet:
                        return f"… {snippet} …"
            except Exception:
                pass
    return f"… {words_removed} …" if words_removed else "…"


def build_headline(
    detection: dict,
    proofs: list[dict],
    witness_root: Path | str,
    det_kind: str,
) -> str:
    """Build a deterministic, non-accusatory headline strictly from signal-processing outputs."""
    wit = detection.get("witness") or {}
    verdict = wit.get("verdict")
    stream = wit.get("stream")
    pieces = wit.get("pieces") or []
    removed = wit.get("removed") or []
    wroot = Path(witness_root)

    suffix = " (text match only)" if det_kind == "text-only" else ""
    stamp = detection.get("stamp") or {}
    stamp_cuts = stamp.get("cuts") or []
    if stamp.get("present") is True and stamp_cuts and verdict in ("faithful", "not_found") and not removed:
        c0 = stamp_cuts[0]
        dur_rem = max(0.1, round(float(c0.get("removed_s", 0.0)), 2))
        at_str = _fmt_mmss(float(c0.get("at_clip_s", 0.0)))
        venue_str = stamp.get("venue") or stream or "stage"
        return f"{dur_rem:.2f} s removed at {at_str} (Roomstamp verified at {venue_str}).{suffix}"

    if verdict == "not_found":
        if stamp.get("present") is True:
            venue_str = stamp.get("venue") or "stage"
            return f"Faithful: Roomstamp verified at {venue_str}.{suffix}"
        return f"Not in the record.{suffix}"

    if verdict == "faithful":
        rec_utc = None
        for p in proofs:
            if p.get("chunk_received_utc"):
                rec_utc = p["chunk_received_utc"]
                break
        if rec_utc is None and stream and pieces:
            seq0 = pieces[0].get("seq")
            if isinstance(seq0, int):
                for name in (f"{seq0:06d}.meta.json", f"{seq0:06d}.json"):
                    fp = wroot / stream / name
                    if fp.exists():
                        try:
                            rec_utc = json.loads(fp.read_text()).get("received_utc")
                            if rec_utc:
                                break
                        except Exception:
                            pass
        dt_str = _fmt_date_hm_utc(rec_utc)
        if stream and dt_str:
            base = f"Faithful: every word is in the record ({stream}, {dt_str})."
        elif stream:
            base = f"Faithful: every word is in the record ({stream})."
        else:
            base = "Faithful: every word is in the record."
        return f"{base}{suffix}"

    if verdict == "cut":
        if removed:
            r0 = removed[0]
            s0, s1 = float(r0["src_s"][0]), float(r0["src_s"][1])
            dur_rem = max(0.1, round(s1 - s0, 1))
            at_str = _fmt_mmss(float(r0.get("after_clip_s", 0.0)))
            snippet = _record_snippet(wroot, stream, r0)
            base = f"{dur_rem:.1f} s removed at {at_str}: the record says \"{snippet}\""
        else:
            base = "Cut detected in the record."
        return f"{base}{suffix}"

    if verdict == "spliced":
        n_parts = max(2, len(pieces))
        max_gap_s = 0.0
        for i in range(len(pieces) - 1):
            p_a, p_b = pieces[i], pieces[i + 1]
            seq_a, seq_b = int(p_a.get("seq", 0)), int(p_b.get("seq", 0))
            pos_a = seq_a * 60.0 + float(p_a["src_s"][1])
            pos_b = seq_b * 60.0 + float(p_b["src_s"][0])
            max_gap_s = max(max_gap_s, abs(pos_b - pos_a))
        if max_gap_s >= 60.0:
            mins = max(1, int(round(max_gap_s / 60.0)))
            base = f"Spliced: {n_parts} parts from {mins} min apart."
        else:
            secs = max(1, int(round(max_gap_s)))
            base = f"Spliced: {n_parts} parts from {secs} s apart."
        return f"{base}{suffix}"

    return f"Verdict: {verdict}.{suffix}"


def compose_verdict(
    clip_info: dict,
    detection: dict,
    label: dict | None,
    proofs: list[dict],
    witness_root: Path | str,
    created_utc: str | None = None,
) -> dict:
    """Assemble the complete verdict.json structure and compute its canonical `verdict_id`."""
    det_kind = detector_kind(detection)
    coverage = compute_coverage(detection, witness_root)
    headline = build_headline(detection, proofs, witness_root, det_kind)

    clip_public = {
        "sha256": clip_info["sha256"],
        "duration_s": clip_info["duration_s"],
        "source_url": clip_info.get("source_url"),
        "title": clip_info.get("title"),
    }

    body = {
        "created_utc": created_utc or utc_now(),
        "clip": clip_public,
        "detection": detection,
        "label": label,
        "proofs": proofs,
        "coverage": coverage,
        "headline": headline,
        "detector_kind": det_kind,
    }
    verdict_id = sha256_bytes(canonical_json(body).encode("utf-8"))
    return {"verdict_id": verdict_id, **body}
