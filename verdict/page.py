"""Self-contained HTML evidence page renderer for Wholetake verdicts (no external requests)."""
import html
import json
from datetime import datetime, timezone
from pathlib import Path

from verdict.compose import _fmt_mmss
from verdict.restore import can_restore, edge_context, preview_restored_manifest, select_span_word_indices

BASESCAN_TX_PREFIX = "https://sepolia.basescan.org/tx/"

STREAM_NAMES: dict[str, str] = {
    "aje": "Al Jazeera English · Live",
}

VERDICT_META: dict[str, dict[str, str]] = {
    "cut": {
        "stamp": "CUT",
        "primary": "#F43F5E",
        "glow": "rgba(244, 63, 94, 0.18)",
        "pill_bg": "#4C0519",
        "pill_text": "#FFE4E6",
    },
    "spliced": {
        "stamp": "SPLICED",
        "primary": "#F59E0B",
        "glow": "rgba(245, 158, 11, 0.18)",
        "pill_bg": "#451A03",
        "pill_text": "#FEF3C7",
    },
    "faithful": {
        "stamp": "FAITHFUL",
        "primary": "#10B981",
        "glow": "rgba(16, 185, 129, 0.18)",
        "pill_bg": "#064E3B",
        "pill_text": "#D1FAE5",
    },
    "not_in_record": {
        "stamp": "NO RECORD",
        "primary": "#64748B",
        "glow": "rgba(100, 116, 139, 0.16)",
        "pill_bg": "#1E293B",
        "pill_text": "#E2E8F0",
    },
}


def _esc(val: object) -> str:
    if val is None:
        return ""
    return html.escape(str(val), quote=True)


def _parse_iso(ts: str | None) -> datetime | None:
    if not ts or not isinstance(ts, str):
        return None
    try:
        s = ts.strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _fmt_utc_human(ts: str | None) -> str:
    dt = _parse_iso(ts)
    if not dt:
        return ""
    return f"{dt.day} {dt.strftime('%b %Y, %H:%M:%S')} UTC"


def _fmt_wall_hms(dt: datetime | None) -> str:
    if not dt:
        return ""
    return f"{dt.strftime('%H:%M:%S')} UTC"


def _load_chunk_meta(witness_root: Path, stream: str | None, seq: int) -> dict:
    if not stream or not isinstance(seq, int):
        return {}
    p = witness_root / stream / f"{seq:06d}.json"
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text())
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _load_sentence_highlight(
    witness_root: Path,
    stream: str | None,
    rem: dict,
    manifest_tokens: list[str] | None = None,
) -> tuple[str, list[str], str]:
    """Return (before_words, removed_tokens, after_words) from the witness chunk transcript."""
    words_removed = str(rem.get("words") or "").strip()
    seq = rem.get("seq")
    src_s = rem.get("src_s") or [0.0, 0.0]
    s0, s1 = float(src_s[0]), float(src_s[1])

    if stream and isinstance(seq, int):
        tx = _load_chunk_meta(witness_root, stream, seq)
        wlist = tx.get("words") or []
        if wlist:
            try:
                hits = select_span_word_indices(wlist, s0, s1)
                if hits:
                    lo = max(0, hits[0] - 8)
                    hi = min(len(wlist), hits[-1] + 1 + 8)
                    before = " ".join(str(w.get("w", "")).strip() for w in wlist[lo : hits[0]]).strip()
                    mid_tokens = (
                        list(manifest_tokens)
                        if manifest_tokens
                        else [str(wlist[i].get("w", "")).strip() for i in hits if str(wlist[i].get("w", "")).strip()]
                    )
                    after = " ".join(str(w.get("w", "")).strip() for w in wlist[hits[-1] + 1 : hi]).strip()
                    return (
                        (f"… {before} " if lo > 0 and before else (f"{before} " if before else "")),
                        mid_tokens or ([t for t in words_removed.split(" ") if t] if words_removed else []),
                        (f" {after} …" if hi < len(wlist) and after else (f" {after}" if after else "")),
                    )
            except Exception:
                pass
    fallback_tokens = list(manifest_tokens) if manifest_tokens else ([t for t in words_removed.split(" ") if t] if words_removed else [])
    return ("… ", fallback_tokens, " …")


def _quote_wall_span(witness_root: Path, stream: str | None, rem: dict, proofs: list[dict] | None = None) -> str:
    """Return 'chunk #4 · received 18:54:11 UTC' using only pinned receipt time (never start_utc_est)."""
    seq = rem.get("seq")
    seq_label = f"chunk #{seq}" if seq is not None else "record"
    if not isinstance(seq, int):
        return seq_label

    rec_str: str | None = None
    if stream:
        tx = _load_chunk_meta(witness_root, stream, seq)
        if isinstance(tx.get("received_utc"), str):
            rec_str = tx["received_utc"]
    if not rec_str and proofs:
        for pr in proofs:
            if isinstance(pr, dict) and pr.get("seq") == seq and isinstance(pr.get("chunk_received_utc"), str):
                rec_str = pr["chunk_received_utc"]
                break

    rec_dt = _parse_iso(rec_str)
    if not rec_dt:
        return seq_label
    return f"{seq_label} · received {rec_dt.strftime('%H:%M:%S')} UTC"


def _fmt_stop_reason(stops: dict | None) -> str:
    if not isinstance(stops, dict):
        return ""
    kind = stops.get("kind")
    if kind == "stream_start":
        return "stream started here"
    if kind == "live_edge":
        return "live edge"
    if kind == "gap":
        dt_from = _parse_iso(stops.get("from_utc"))
        dt_to = _parse_iso(stops.get("to_utc"))
        if dt_from and dt_to:
            return f"witness gap {dt_from.strftime('%H:%M:%S')}–{dt_to.strftime('%H:%M:%S')} UTC"
        return "witness gap"
    return ""


def _render_context_drawer(job_id: str, side: str, ctx_side: dict | None) -> str:
    if not isinstance(ctx_side, dict):
        return ""
    words = ctx_side.get("words") or []
    stops = ctx_side.get("stops")
    if not words and not stops:
        return ""

    label = "◀ 15 s before the clip" if side == "before" else "15 s after the clip ▶"
    words_str = " ".join(str(w.get("w") or "").strip() for w in words if isinstance(w, dict)).strip()
    stop_str = _fmt_stop_reason(stops)

    play_btn = ""
    if ctx_side.get("audio_available") and ctx_side.get("audio"):
        play_btn = (
            f'<button type="button" class="ctx-play-btn" data-ctx-side="{_esc(side)}" '
            f'data-ctx-src="/v/{_esc(job_id)}/context/{_esc(side)}.wav">▶ Play {_esc(side)}</button>'
        )

    stop_html = f'<span class="ctx-stop-note">({_esc(stop_str)})</span>' if stop_str else ""
    body_text = f"“{_esc(words_str)}”" if words_str else '<span class="ctx-empty">No words in window</span>'
    return (
        f'<details class="context-drawer context-{_esc(side)}">'
        f"<summary><span>{_esc(label)}</span>{stop_html}</summary>"
        '<div class="context-drawer-body">'
        f'<div class="context-words">{body_text}</div>'
        f"{play_btn}"
        "</div></details>"
    )


def _plain_english_summary(vkind: str, pieces: list[dict], removed: list[dict], inserted: list[dict]) -> str:
    if vkind == "cut":
        if len(removed) == 1:
            r0 = removed[0]
            src_s = r0.get("src_s") or [0.0, 0.0]
            cut_dur = max(0.1, round(float(src_s[1]) - float(src_s[0]), 1))
            at_s = float(r0.get("after_clip_s") or 0.0)
            return f"{cut_dur:.1f} seconds were removed at {_fmt_mmss(at_s)}"
        if len(removed) > 1:
            total = sum(
                max(0.1, float((r.get("src_s") or [0.0, 0.0])[1]) - float((r.get("src_s") or [0.0, 0.0])[0]))
                for r in removed
            )
            return f"{round(total, 1):.1f} seconds removed across {len(removed)} cuts"
        return "Audio was removed from the witnessed broadcast"

    if vkind == "spliced":
        if inserted:
            ins_dur = sum(
                max(0.1, float((ins.get("clip_s") or [0.0, 0.0])[1]) - float((ins.get("clip_s") or [0.0, 0.0])[0]))
                for ins in inserted
                if isinstance(ins, dict) and ins.get("clip_s")
            )
            if ins_dur > 0:
                return f"Joins non-contiguous broadcast segments ({ins_dur:.1f}s inserted)"
        n_parts = max(2, len(pieces))
        return f"Joins {n_parts} separate segments from the witnessed record"

    if vkind == "faithful":
        return "Every word matches the witnessed broadcast continuously"

    return "No matching segment found in our witnessed record"


def _build_record_time_domain(
    witness_root: Path,
    stream: str | None,
    pieces: list[dict],
    removed: list[dict],
) -> tuple[dict[int, float], dict[int, float], list[int]]:
    """Compute chunk-relative record offsets (seconds from first chunk start) per seq without using start_utc_est."""
    seqs: set[int] = set()
    for item in list(pieces) + list(removed):
        sq = item.get("seq")
        if isinstance(sq, int):
            seqs.add(sq)
    if not seqs:
        return ({0: 0.0}, {0: 20.0}, [0])

    min_seq = min(seqs)
    max_seq = max(seqs)
    base_seqs = list(range(min_seq, max_seq + 1)) if (max_seq - min_seq) <= 12 else sorted(seqs)

    probe_seqs = list(base_seqs)
    if stream:
        if min_seq > 0 and (witness_root / stream / f"{min_seq - 1:06d}.json").exists():
            probe_seqs.insert(0, min_seq - 1)
        if (witness_root / stream / f"{max_seq + 1:06d}.json").exists():
            probe_seqs.append(max_seq + 1)

    dur_by_seq: dict[int, float] = {}
    for sq in probe_seqs:
        m = _load_chunk_meta(witness_root, stream, sq)
        dur_by_seq[sq] = float(m.get("duration_s") or 20.0)

    ref_seq = base_seqs[0]
    default_dur = dur_by_seq.get(ref_seq, 20.0)

    offsets: dict[int, float] = {ref_seq: 0.0}
    for sq in sorted(probe_seqs):
        if sq == ref_seq:
            continue
        if sq < ref_seq:
            cur = 0.0
            for k in range(ref_seq - 1, sq - 1, -1):
                cur -= dur_by_seq.get(k, default_dur)
            offsets[sq] = cur
        else:
            cur = 0.0
            for k in range(ref_seq, sq):
                cur += dur_by_seq.get(k, default_dur)
            offsets[sq] = cur

    return (offsets, dur_by_seq, probe_seqs)


def _fmt_chunk_rel_label(abs_t: float, offsets: dict[int, float], all_seqs: list[int]) -> str:
    """Format an absolute record domain coordinate as chunk-relative ('#4 +1.7 s')."""
    if not all_seqs:
        return f"+{abs_t:.1f} s"
    chosen_sq = all_seqs[0]
    for sq in sorted(all_seqs):
        if offsets.get(sq, 0.0) <= abs_t + 1e-6:
            chosen_sq = sq
        else:
            break
    local_s = max(0.0, abs_t - offsets.get(chosen_sq, 0.0))
    return f"#{chosen_sq} +{local_s:.1f} s"


def _build_two_track_svg(
    vkind: str,
    dur_s: float,
    pieces: list[dict],
    removed: list[dict],
    inserted: list[dict],
    offsets: dict[int, float],
    dur_by_seq: dict[int, float],
    all_seqs: list[int],
) -> tuple[str, list[dict], list[dict]]:
    """Build the 880x168 two-track alignment SVG and JS playhead mapping segments."""
    x0, width = 116.0, 744.0
    x1 = x0 + width

    def clip_x(t: float) -> float:
        clamped = max(0.0, min(dur_s, t))
        return round(x0 + (clamped / dur_s) * width, 2)

    if vkind == "not_in_record" or not pieces:
        svg = (
            '<svg class="alignment-svg" viewBox="0 0 880 168" role="img" '
            'aria-label="Two-track alignment diagram: no matching witness segment">'
            "<defs>"
            '<pattern id="unmatchedHatch" width="8" height="8" patternTransform="rotate(45 0 0)" patternUnits="userSpaceOnUse">'
            '<line x1="0" y1="0" x2="0" y2="8" stroke="#475569" stroke-width="2" />'
            "</pattern>"
            "</defs>"
            '<text x="12" y="36" class="svg-track-title">RECORD</text>'
            '<text x="12" y="51" class="svg-track-sub">Witnessed</text>'
            f'<rect x="{x0}" y="20" width="{width}" height="34" rx="6" fill="#1E293B" stroke="#475569" stroke-dasharray="6 4" stroke-width="1.5"/>'
            '<text x="488" y="42" text-anchor="middle" class="svg-empty-label">No matching segment found in witnessed record</text>'
            '<text x="12" y="130" class="svg-track-title">CLIP</text>'
            '<text x="12" y="145" class="svg-track-sub">Posted audio</text>'
            f'<rect x="{x0}" y="114" width="{width}" height="34" rx="6" fill="url(#unmatchedHatch)" stroke="#64748B" stroke-width="1.5"/>'
            f'<text x="{x0}" y="163" class="svg-axis">0:00</text>'
            f'<text x="{x1}" y="163" text-anchor="end" class="svg-axis">{_esc(_fmt_mmss(dur_s))}</text>'
            f'<line id="playheadClip" x1="{x0}" y1="108" x2="{x0}" y2="154" stroke="#F8FAFC" stroke-width="2.5" opacity="0.9"/>'
            "</svg>"
        )
        return svg, [], []

    abs_points: list[float] = []
    for p in pieces:
        sq = int(p.get("seq") or 0)
        off = offsets.get(sq, 0.0)
        s0, s1 = float((p.get("src_s") or [0.0, 0.0])[0]), float((p.get("src_s") or [0.0, 0.0])[1])
        abs_points.extend([off + s0, off + s1])
    for r in removed:
        sq = int(r.get("seq") or 0)
        off = offsets.get(sq, 0.0)
        s0, s1 = float((r.get("src_s") or [0.0, 0.0])[0]), float((r.get("src_s") or [0.0, 0.0])[1])
        abs_points.extend([off + s0, off + s1])

    piece_min = min(abs_points) if abs_points else 0.0
    piece_max = max(abs_points) if abs_points else dur_s

    cov_min = min(offsets[sq] for sq in all_seqs) if all_seqs else 0.0
    cov_max = max(offsets[sq] + max(dur_by_seq.get(sq, 20.0), piece_max - offsets[sq]) for sq in all_seqs) if all_seqs else max(20.0, piece_max)
    pad_s = max(4.0, 0.30 * dur_s)
    rec_min = max(min(cov_min, piece_min), piece_min - pad_s)
    rec_max = min(max(cov_max, piece_max), piece_max + pad_s)
    rec_span = max(0.001, rec_max - rec_min)

    def rec_x(abs_t: float) -> float:
        clamped = max(rec_min, min(rec_max, abs_t))
        return round(x0 + ((clamped - rec_min) / rec_span) * width, 2)

    parts: list[str] = [
        '<svg class="alignment-svg" viewBox="0 0 880 168" role="img" '
        'aria-label="Two-track forensic alignment between witnessed record and posted clip">',
        "<defs>",
        '<linearGradient id="ribbonGrad" x1="0" y1="0" x2="0" y2="1">'
        '<stop offset="0%" stop-color="#3B82F6" stop-opacity="0.18"/>'
        '<stop offset="100%" stop-color="#3B82F6" stop-opacity="0.14"/>'
        "</linearGradient>",
        '<linearGradient id="faithfulGrad" x1="0" y1="0" x2="0" y2="1">'
        '<stop offset="0%" stop-color="#10B981" stop-opacity="0.18"/>'
        '<stop offset="100%" stop-color="#10B981" stop-opacity="0.14"/>'
        "</linearGradient>",
        '<linearGradient id="edgeFadeLeft" x1="0" y1="0" x2="1" y2="0">'
        '<stop offset="0%" stop-color="#090D16" stop-opacity="0.72"/>'
        '<stop offset="100%" stop-color="#090D16" stop-opacity="0"/>'
        "</linearGradient>",
        '<linearGradient id="edgeFadeRight" x1="0" y1="0" x2="1" y2="0">'
        '<stop offset="0%" stop-color="#090D16" stop-opacity="0"/>'
        '<stop offset="100%" stop-color="#090D16" stop-opacity="0.72"/>'
        "</linearGradient>",
        '<pattern id="cutHatch" width="7" height="7" patternTransform="rotate(45 0 0)" patternUnits="userSpaceOnUse">'
        '<line x1="0" y1="0" x2="0" y2="7" stroke="#FFE4E6" stroke-width="1.5" stroke-opacity="0.45"/>'
        "</pattern>",
        '<pattern id="spliceHatch" width="7" height="7" patternTransform="rotate(-45 0 0)" patternUnits="userSpaceOnUse">'
        '<line x1="0" y1="0" x2="0" y2="7" stroke="#FEF3C7" stroke-width="1.5" stroke-opacity="0.45"/>'
        "</pattern>",
        "</defs>",
        '<text x="10" y="35" class="svg-track-title">RECORD</text>',
        '<text x="10" y="50" class="svg-track-sub">Witnessed</text>',
        '<text x="10" y="129" class="svg-track-title">CLIP</text>',
        '<text x="10" y="144" class="svg-track-sub">Posted audio</text>',
        f'<rect x="{x0}" y="20" width="{width}" height="34" rx="6" fill="#1E293B" stroke="#334155" stroke-width="1.5"/>',
        f'<rect x="{x0}" y="114" width="{width}" height="34" rx="6" fill="#1E293B" stroke="#334155" stroke-width="1.5"/>',
        f'<rect x="{x0}" y="21" width="32" height="32" rx="5" fill="url(#edgeFadeLeft)"/>',
        f'<rect x="{x1 - 32}" y="21" width="32" height="32" rx="5" fill="url(#edgeFadeRight)"/>',
        f'<text x="{x0 + 8}" y="41" class="svg-context-dots">…</text>',
        f'<text x="{x1 - 8}" y="41" text-anchor="end" class="svg-context-dots">…</text>',
    ]

    for sq in all_seqs:
        off = offsets.get(sq, 0.0)
        if rec_min < off < rec_max:
            bx = rec_x(off)
            parts.append(
                f'<line x1="{bx}" y1="16" x2="{bx}" y2="56" stroke="#64748B" stroke-dasharray="2 2" stroke-width="1"/>'
            )
            parts.append(f'<text x="{bx + 3}" y="14" class="svg-chunk-tick">#{_esc(sq)}</text>')

    start_rel = _fmt_chunk_rel_label(rec_min, offsets, all_seqs)
    end_rel = _fmt_chunk_rel_label(rec_max, offsets, all_seqs)
    parts.append(f'<text x="{x0 + 2}" y="14" class="svg-axis">{_esc(start_rel)}</text>')
    parts.append(f'<text x="{x1}" y="14" text-anchor="end" class="svg-axis">{_esc(end_rel)}</text>')

    parts.append(f'<text x="{x0}" y="163" class="svg-axis">0:00</text>')
    parts.append(f'<text x="{x1}" y="163" text-anchor="end" class="svg-axis">{_esc(_fmt_mmss(dur_s))}</text>')

    ribbon_fill = "url(#faithfulGrad)" if vkind == "faithful" else "url(#ribbonGrad)"
    ribbon_stroke = "#10B981" if vkind == "faithful" else "#3B82F6"
    bar_fill = "rgba(16, 185, 129, 0.36)" if vkind == "faithful" else "rgba(59, 130, 246, 0.36)"
    bar_stroke = "#34D399" if vkind == "faithful" else "#60A5FA"

    js_segments: list[dict] = []
    n_pieces = len(pieces)
    for idx, p in enumerate(pieces):
        sq = int(p.get("seq") or 0)
        off = offsets.get(sq, 0.0)
        src_s = p.get("src_s") or [0.0, 0.0]
        clip_s = p.get("clip_s") or [0.0, 0.0]
        rs0, rs1 = off + float(src_s[0]), off + float(src_s[1])
        cs0, cs1 = float(clip_s[0]), float(clip_s[1])
        sx0_raw, sx1_raw = rec_x(rs0), rec_x(rs1)
        cx0_raw, cx1_raw = clip_x(cs0), clip_x(cs1)

        sx0 = round(sx0_raw + (1.0 if idx > 0 else 0.0), 2)
        sx1 = round(sx1_raw - (1.0 if idx < n_pieces - 1 else 0.0), 2)
        if sx1 <= sx0:
            sx0, sx1 = sx0_raw, sx1_raw

        cx0 = round(cx0_raw + (1.0 if idx > 0 else 0.0), 2)
        cx1 = round(cx1_raw - (1.0 if idx < n_pieces - 1 else 0.0), 2)
        if cx1 <= cx0:
            cx0, cx1 = cx0_raw, cx1_raw

        sw = max(2.0, round(sx1 - sx0, 2))
        cw = max(2.0, round(cx1 - cx0, 2))
        tip = f"Chunk #{sq} [{float(src_s[0]):.1f}s–{float(src_s[1]):.1f}s] → Clip [{cs0:.1f}s–{cs1:.1f}s]"

        parts.append(
            f'<path d="M {sx0},54 L {sx1},54 C {sx1},84 {cx1},84 {cx1},114 L {cx0},114 C {cx0},84 {sx0},84 {sx0},54 Z" '
            f'fill="{ribbon_fill}" stroke="{ribbon_stroke}" stroke-opacity="0.7" stroke-width="1.25">'
            f"<title>{_esc(tip)}</title></path>"
        )
        parts.append(
            f'<rect x="{sx0}" y="20" width="{sw}" height="34" rx="4" fill="{bar_fill}" stroke="{bar_stroke}" stroke-width="1.25">'
            f"<title>{_esc(tip)}</title></rect>"
        )
        parts.append(
            f'<rect x="{cx0}" y="114" width="{cw}" height="34" rx="4" fill="{bar_fill}" stroke="{bar_stroke}" stroke-width="1.25">'
            f"<title>{_esc(tip)}</title></rect>"
        )
        js_segments.append({"c0": cs0, "c1": cs1, "rx0": sx0_raw, "rx1": sx1_raw, "cx0": cx0_raw, "cx1": cx1_raw})

    js_cuts: list[dict] = []
    for idx, r in enumerate(removed):
        sq = int(r.get("seq") or 0)
        off = offsets.get(sq, 0.0)
        src_s = r.get("src_s") or [0.0, 0.0]
        rs0, rs1 = off + float(src_s[0]), off + float(src_s[1])
        cut_dur = max(0.1, round(float(src_s[1]) - float(src_s[0]), 1))
        at_s = max(0.0, min(dur_s, float(r.get("after_clip_s") or 0.0)))
        rem_words = str(r.get("words") or "").strip()

        rx0_raw, rx1_raw = rec_x(rs0), rec_x(rs1)
        rmid = (rx0_raw + rx1_raw) / 2.0
        rw = max(10.0, round(rx1_raw - rx0_raw, 2))
        rx0 = round(max(x0, min(x1 - rw, rmid - rw / 2.0)), 2)
        rx1 = round(rx0 + rw, 2)
        cx = clip_x(at_s)
        tip_text = f"{cut_dur:.1f}s removed at {_fmt_mmss(at_s)}: “{rem_words}”"

        parts.append(
            f'<polygon points="{rx0},54 {rx1},54 {cx},114" fill="rgba(244, 63, 94, 0.16)"/>'
            f'<line x1="{rx0}" y1="54" x2="{cx}" y2="114" stroke="#F43F5E" stroke-dasharray="3 3" stroke-width="1.4"/>'
            f'<line x1="{rx1}" y1="54" x2="{cx}" y2="114" stroke="#F43F5E" stroke-dasharray="3 3" stroke-width="1.4"/>'
        )
        parts.append(
            f'<g class="cut-callout" data-cut-at="{at_s:.2f}" data-cut-idx="{idx}" tabindex="0">'
            f"<title>{_esc(tip_text)}</title>"
            f'<rect id="excisedBlock{idx}" x="{rx0}" y="16" width="{rw}" height="42" rx="4" fill="#F43F5E" stroke="#FFE4E6" stroke-width="1.5"/>'
            f'<rect x="{rx0}" y="16" width="{rw}" height="42" rx="4" fill="url(#cutHatch)"/>'
            f'<rect x="{rmid - 26:.1f}" y="2" width="52" height="15" rx="4" fill="#4C0519" stroke="#F43F5E" stroke-width="1"/>'
            f'<text x="{rmid:.1f}" y="13" text-anchor="middle" class="svg-cut-pill">−{_esc(f"{cut_dur:.1f}s")}</text>'
            f'<line x1="{cx}" y1="108" x2="{cx}" y2="154" stroke="#F43F5E" stroke-width="3" stroke-linecap="round"/>'
            f'<circle cx="{cx}" cy="114" r="4" fill="#FFE4E6" stroke="#F43F5E" stroke-width="2"/>'
            f'<text x="{cx}" y="163" text-anchor="middle" class="svg-seam-time">✂ {_esc(_fmt_mmss(at_s))}</text>'
            "</g>"
        )
        js_cuts.append(
            {
                "idx": idx,
                "at_s": at_s,
                "dur_s": cut_dur,
                "rx0": rx0,
                "rx1": rx1,
                "cx": cx,
            }
        )

    for ins in inserted:
        if not isinstance(ins, dict):
            continue
        cs = ins.get("clip_s") or [0.0, 0.0]
        ic0, ic1 = float(cs[0]), float(cs[1])
        ins_dur = max(0.1, round(ic1 - ic0, 1))
        ix0_raw, ix1_raw = clip_x(ic0), clip_x(ic1)
        imid = (ix0_raw + ix1_raw) / 2.0
        iw = max(10.0, round(ix1_raw - ix0_raw, 2))
        ix0 = round(max(x0, min(x1 - iw, imid - iw / 2.0)), 2)
        parts.append(
            f'<g class="splice-callout">'
            f"<title>{_esc(f'{ins_dur:.1f}s inserted in clip [{_fmt_mmss(ic0)}–{_fmt_mmss(ic1)}]')}</title>"
            f'<rect x="{ix0}" y="110" width="{iw}" height="42" rx="4" fill="#F59E0B" stroke="#FEF3C7" stroke-width="1.5"/>'
            f'<rect x="{ix0}" y="110" width="{iw}" height="42" rx="4" fill="url(#spliceHatch)"/>'
            f'<text x="{imid:.1f}" y="163" text-anchor="middle" class="svg-splice-time">+{_esc(f"{ins_dur:.1f}s")}</text>'
            "</g>"
        )

    if vkind == "spliced" and not inserted and len(pieces) >= 2:
        for i in range(len(pieces) - 1):
            p_a = pieces[i]
            c_seam = clip_x(float((p_a.get("clip_s") or [0.0, 0.0])[1]))
            parts.append(
                f'<line x1="{c_seam}" y1="108" x2="{c_seam}" y2="154" stroke="#F59E0B" stroke-width="3" stroke-linecap="round"/>'
                f'<text x="{c_seam}" y="163" text-anchor="middle" class="svg-splice-time">🧩 splice</text>'
            )

    init_rx = js_segments[0]["rx0"] if js_segments else x0
    parts.append(
        f'<line id="playheadRec" x1="{init_rx}" y1="14" x2="{init_rx}" y2="60" stroke="#F8FAFC" stroke-width="2.5"/>'
        f'<line id="playheadClip" x1="{x0}" y1="108" x2="{x0}" y2="154" stroke="#F8FAFC" stroke-width="2.5"/>'
    )
    parts.append("</svg>")
    return "".join(parts), js_segments, js_cuts


def _css(vmeta: dict[str, str]) -> str:
    primary = vmeta["primary"]
    glow = vmeta["glow"]
    pill_bg = vmeta["pill_bg"]
    pill_text = vmeta["pill_text"]
    return f"""
:root {{
  --bg: #090D16;
  --surface: #111827;
  --surface-2: #1E293B;
  --surface-subtle: #1E293B;
  --border: #283548;
  --text: #F8FAFC;
  --text-secondary: #CBD5E1;
  --muted: #94A3B8;
  --verdict-primary: {primary};
  --verdict-glow: {glow};
  --verdict-pill-bg: {pill_bg};
  --verdict-pill-text: {pill_text};
  --accent-match: #3B82F6;
  --accent-cut: #F43F5E;
  --accent-ok: #10B981;
  --accent-warn: #F59E0B;
  --font-sans: "Inter", "Noto Sans", "Segoe UI", "Helvetica Neue", Arial, sans-serif;
  --font-mono: "JetBrains Mono", "Noto Sans Mono", "DejaVu Sans Mono", ui-monospace, Menlo, Consolas, monospace;
}}
* {{ box-sizing: border-box; }}
html, body {{
  max-width: 100%;
  overflow-x: hidden;
}}
body {{
  margin: 0;
  padding: 0 0 48px;
  background: var(--bg);
  color: var(--text);
  font-family: var(--font-sans);
  line-height: 1.5;
  -webkit-font-smoothing: antialiased;
}}
.top-ribbon {{
  height: 4px;
  width: 100%;
  background: var(--verdict-primary);
}}
main {{
  max-width: 960px;
  margin: 0 auto;
  padding: 18px 24px 0;
}}
@media (max-width: 600px) {{
  main {{ padding: 14px 14px 0; }}
}}
.num, .mono, time {{
  font-variant-numeric: tabular-nums;
}}
.mono {{
  font-family: var(--font-mono);
  font-size: 0.82rem;
  font-variant-numeric: tabular-nums;
}}
header.brand {{
  display: flex;
  justify-content: space-between;
  align-items: center;
  border-bottom: 1.5px solid var(--border);
  padding-bottom: 12px;
  margin-bottom: 18px;
  gap: 12px;
  flex-wrap: wrap;
}}
.brand-left {{
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 0.78rem;
  font-weight: 700;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--muted);
}}
.brand-dot {{
  width: 8px;
  height: 8px;
  border-radius: 999px;
  background: var(--verdict-primary);
  display: inline-block;
}}
.badges {{
  display: flex;
  gap: 8px;
  align-items: center;
  flex-wrap: wrap;
}}
.badge, .caveat-chip {{
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 10px;
  border-radius: 999px;
  background: var(--surface-2);
  border: 1px solid var(--border);
  color: var(--muted);
  font-size: 0.76rem;
  font-weight: 600;
  letter-spacing: 0.02em;
  position: relative;
}}
.caveat-chip {{
  cursor: help;
}}
.caveat-tip {{
  display: none;
  position: absolute;
  top: calc(100% + 6px);
  right: 0;
  width: 260px;
  padding: 8px 10px;
  background: var(--surface);
  color: var(--text-secondary);
  border: 1.5px solid var(--border);
  border-radius: 8px;
  font-size: 0.75rem;
  font-weight: 400;
  line-height: 1.4;
  z-index: 20;
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.45);
}}
.caveat-chip:hover .caveat-tip,
.caveat-chip:focus .caveat-tip {{
  display: block;
}}
.hero-banner {{
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 20px;
  padding: 18px 22px;
  margin-bottom: 16px;
  background: radial-gradient(circle at 12% 50%, var(--verdict-glow), transparent 55%), var(--surface);
  border: 1.5px solid var(--border);
  border-left: 5px solid var(--verdict-primary);
  border-radius: 12px;
}}
.hero-left {{
  display: flex;
  align-items: center;
  gap: 18px;
  flex: 1;
  min-width: 0;
}}
.verdict-stamp {{
  display: inline-flex;
  align-items: center;
  gap: 10px;
  padding: 8px 16px;
  border-radius: 8px;
  background: var(--verdict-pill-bg);
  color: var(--verdict-pill-text);
  border: 2px solid var(--verdict-primary);
  font-size: 2.35rem;
  font-weight: 900;
  letter-spacing: 0.06em;
  line-height: 1;
  text-transform: uppercase;
  white-space: nowrap;
  flex-shrink: 0;
}}
.stamp-dot {{
  width: 11px;
  height: 11px;
  border-radius: 999px;
  background: var(--verdict-primary);
}}
.hero-copy {{
  min-width: 0;
}}
h1.headline {{
  font-size: clamp(1.35rem, 2.5vw, 1.85rem);
  line-height: 1.2;
  margin: 0 0 6px;
  font-weight: 800;
  letter-spacing: -0.02em;
  color: var(--text);
  font-variant-numeric: tabular-nums;
}}
.hero-meta {{
  font-size: 0.9rem;
  color: var(--muted);
  font-variant-numeric: tabular-nums;
}}
.hear-cut-btn {{
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 10px 16px;
  border-radius: 8px;
  background: var(--verdict-primary);
  color: #FFFFFF;
  border: 0;
  font-family: var(--font-sans);
  font-size: 0.9rem;
  font-weight: 700;
  cursor: pointer;
  white-space: nowrap;
  flex-shrink: 0;
  font-variant-numeric: tabular-nums;
}}
.hear-cut-btn:hover {{
  filter: brightness(1.1);
}}
.restore-unavail-note {{
  font-size: 0.76rem;
  color: var(--muted);
  margin-top: 6px;
}}
@media (max-width: 760px) {{
  .hero-banner {{
    flex-direction: column;
    align-items: stretch;
    gap: 14px;
    padding: 16px;
  }}
  .hero-left {{
    flex-direction: column;
    align-items: flex-start;
    gap: 12px;
  }}
  .verdict-stamp {{
    font-size: 1.85rem;
  }}
  .hear-cut-btn {{
    width: 100%;
  }}
}}
section.card {{
  background: var(--surface);
  border: 1.5px solid var(--border);
  border-radius: 12px;
  padding: 16px 20px;
  margin-bottom: 16px;
}}
section.card.advisory {{
  background: var(--surface);
  color: var(--text-secondary);
}}
.sec-head {{
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  margin-bottom: 10px;
  flex-wrap: wrap;
}}
h2.sec-title {{
  font-size: 0.78rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: var(--muted);
  margin: 0;
}}
.sec-sub {{
  font-size: 0.82rem;
  color: var(--muted);
}}
.svg-stage {{
  position: relative;
}}
.restored-banner {{
  display: none;
  align-items: center;
  gap: 8px;
  padding: 6px 12px;
  margin-bottom: 6px;
  border-radius: 6px;
  background: #4C0519;
  border: 1px solid #F43F5E;
  color: #FFE4E6;
  font-size: 0.82rem;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
}}
.restored-banner.active {{
  display: inline-flex;
}}
.alignment-svg {{
  width: 100%;
  max-width: 100%;
  height: auto;
  display: block;
  margin: 4px 0 8px;
  overflow: hidden;
}}
.svg-track-title {{
  fill: #F8FAFC;
  font-family: var(--font-sans);
  font-size: 13px;
  font-weight: 800;
  letter-spacing: 0.06em;
}}
.svg-track-sub {{
  fill: #94A3B8;
  font-family: var(--font-sans);
  font-size: 10.5px;
}}
.svg-axis {{
  fill: #94A3B8;
  font-family: var(--font-mono);
  font-size: 11px;
  font-variant-numeric: tabular-nums;
}}
.svg-chunk-tick {{
  fill: #CBD5E1;
  font-family: var(--font-mono);
  font-size: 10.5px;
  font-weight: 600;
}}
.svg-context-dots {{
  fill: #64748B;
  font-family: var(--font-mono);
  font-size: 13px;
  font-weight: 700;
}}
.svg-cut-pill {{
  fill: #FFE4E6;
  font-family: var(--font-mono);
  font-size: 11px;
  font-weight: 700;
}}
.svg-seam-time {{
  fill: #FDA4AF;
  font-family: var(--font-mono);
  font-size: 11px;
  font-weight: 700;
}}
.svg-splice-time {{
  fill: #FCD34D;
  font-family: var(--font-mono);
  font-size: 11px;
  font-weight: 700;
}}
.svg-empty-label {{
  fill: #94A3B8;
  font-family: var(--font-sans);
  font-size: 13px;
  font-weight: 600;
}}
@media (max-width: 560px) {{
  .svg-axis,
  .svg-chunk-tick,
  .svg-track-sub,
  .svg-seam-time,
  .svg-splice-time,
  .svg-context-dots {{
    display: none;
  }}
  .svg-track-title {{
    font-size: 22px;
  }}
  .svg-cut-pill {{
    font-size: 18px;
  }}
}}
.cut-callout {{
  cursor: pointer;
}}
.pulse-excised {{
  filter: drop-shadow(0 0 8px rgba(244, 63, 94, 0.9));
  stroke-width: 2.5px !important;
}}
.audio-bar {{
  display: flex;
  align-items: center;
  gap: 14px;
  margin-top: 8px;
  flex-wrap: wrap;
}}
audio.player {{
  flex: 1;
  width: 100%;
  min-width: 0;
  height: 36px;
}}
.mid-grid {{
  display: grid;
  grid-template-columns: 1.55fr 1fr;
  gap: 16px;
  margin-bottom: 16px;
}}
.mid-grid.single {{
  grid-template-columns: 1fr;
}}
.mid-grid > section.card {{
  margin-bottom: 0;
}}
@media (max-width: 780px) {{
  .mid-grid {{ grid-template-columns: 1fr; }}
}}
.record-quote {{
  font-size: 1.15rem;
  line-height: 1.65;
  margin: 8px 0 0;
  padding: 12px 16px;
  background: var(--surface-2);
  border-left: 3.5px solid var(--verdict-primary);
  border-radius: 8px;
  color: var(--text);
}}
.quote-meta {{
  font-family: var(--font-mono);
  font-size: 0.78rem;
  color: var(--muted);
  margin-bottom: 6px;
  font-variant-numeric: tabular-nums;
}}
mark.removed {{
  background: rgba(244, 63, 94, 0.22);
  color: #FFE4E6;
  border: 1px solid #F43F5E;
  padding: 2px 7px;
  border-radius: 5px;
  font-weight: 400;
  text-decoration-line: line-through;
  text-decoration-color: color-mix(in srgb, var(--verdict-primary) 40%, transparent);
  text-decoration-thickness: 1px;
}}
mark.removed.flash-cut {{
  background: rgba(244, 63, 94, 0.5);
  box-shadow: 0 0 0 3px rgba(244, 63, 94, 0.35);
}}
.kw-word {{
  transition: background-color 0.06s ease, color 0.06s ease;
  border-radius: 3px;
  padding: 0 1px;
}}
.kw-word.kw-spoken {{
  background: rgba(244, 63, 94, 0.42);
  color: #FFF1F2;
  text-decoration: none;
}}
.kw-word.kw-active {{
  background: #F43F5E;
  color: #FFFFFF;
  text-decoration: none;
  font-weight: 700;
}}
.cut-tag {{
  display: inline-block;
  font-family: var(--font-mono);
  font-size: 0.72rem;
  font-weight: 700;
  padding: 1px 6px;
  margin-right: 4px;
  border-radius: 4px;
  background: #4C0519;
  color: #FDA4AF;
  vertical-align: middle;
  font-variant-numeric: tabular-nums;
}}
details.context-drawer {{
  margin: 8px 0;
  padding: 8px 12px;
  background: rgba(30, 41, 59, 0.55);
  border: 1px solid var(--border);
  border-radius: 8px;
  font-size: 0.86rem;
}}
details.context-drawer summary {{
  cursor: pointer;
  color: var(--muted);
  font-weight: 600;
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
  user-select: none;
}}
.ctx-stop-note {{
  font-family: var(--font-mono);
  font-size: 0.75rem;
  color: var(--muted);
  font-weight: 400;
}}
.context-drawer-body {{
  margin-top: 8px;
  padding-top: 8px;
  border-top: 1px solid var(--border);
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 12px;
  flex-wrap: wrap;
}}
.context-words {{
  flex: 1;
  min-width: 180px;
  color: var(--text-secondary);
  line-height: 1.5;
}}
.ctx-play-btn {{
  padding: 4px 10px;
  border-radius: 6px;
  background: var(--surface-2);
  border: 1px solid var(--border);
  color: var(--text);
  font-family: var(--font-sans);
  font-size: 0.78rem;
  font-weight: 600;
  cursor: pointer;
  white-space: nowrap;
}}
.ctx-play-btn:hover {{
  border-color: var(--accent-match);
}}
.effect-pill {{
  display: inline-block;
  padding: 2px 8px;
  border-radius: 4px;
  background: var(--surface-2);
  border: 1px solid var(--border);
  color: var(--text);
  font-size: 0.75rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  margin-right: 6px;
}}
.custody-strip {{
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(175px, 1fr));
  gap: 10px;
  margin-top: 10px;
}}
.custody-step {{
  background: var(--surface-2);
  border: 1.5px solid var(--border);
  border-radius: 8px;
  padding: 10px 12px;
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}}
.custody-step.ok {{
  border-color: rgba(16, 185, 129, 0.45);
}}
.custody-step.pending {{
  border-color: rgba(245, 158, 11, 0.45);
}}
.step-top {{
  display: flex;
  align-items: center;
  gap: 7px;
  font-size: 0.82rem;
  font-weight: 700;
  color: var(--text);
}}
.step-icon {{
  width: 18px;
  height: 18px;
  border-radius: 999px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 0.7rem;
  font-weight: 900;
  flex-shrink: 0;
}}
.step-icon.ok {{
  background: #064E3B;
  color: #34D399;
  border: 1px solid #10B981;
}}
.step-icon.pending {{
  background: #451A03;
  color: #FCD34D;
  border: 1px solid #F59E0B;
}}
.step-icon.neutral {{
  background: #1E293B;
  color: #CBD5E1;
  border: 1px solid #475569;
}}
.step-detail {{
  font-family: var(--font-mono);
  font-size: 0.76rem;
  color: var(--muted);
  font-variant-numeric: tabular-nums;
  overflow-wrap: anywhere;
}}
details.raw-proofs {{
  margin-top: 12px;
  border-top: 1px solid var(--border);
  padding-top: 10px;
}}
details.raw-proofs summary {{
  cursor: pointer;
  font-size: 0.8rem;
  font-weight: 600;
  color: var(--muted);
  user-select: none;
}}
.proof-table-wrap {{
  overflow-x: auto;
}}
.proof-table {{
  width: 100%;
  border-collapse: collapse;
  font-size: 0.82rem;
  margin-top: 8px;
}}
.proof-table th, .proof-table td {{
  text-align: left;
  padding: 7px 6px;
  border-bottom: 1px solid var(--border);
}}
.proof-table th {{
  color: var(--muted);
  font-weight: 600;
}}
a {{ color: #60A5FA; text-decoration: none; }}
a:hover {{ text-decoration: underline; }}
footer.links {{
  margin-top: 16px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 0.84rem;
  color: var(--muted);
  flex-wrap: wrap;
  gap: 10px;
}}
@media (prefers-reduced-motion: no-preference) {{
  .stamp-dot, .pulse-dot {{
    animation: pulseOpacity 1.8s infinite ease-in-out;
  }}
  @keyframes pulseOpacity {{
    0%, 100% {{ opacity: 1; transform: scale(1); }}
    50% {{ opacity: 0.4; transform: scale(0.88); }}
  }}
}}
""".strip()


def render_verdict_page(job: dict, witness_root: Path | str) -> str:
    """Render a self-contained HTML page for a queued, running, error, or completed verdict job."""
    wroot = Path(witness_root)
    job_id = str(job.get("id") or "")
    status = str(job.get("status") or "queued")
    result = job.get("result")

    if status != "done" or not isinstance(result, dict):
        vmeta = VERDICT_META["not_in_record"]
        if status == "error":
            err = _esc(job.get("error") or "Unknown error")
            body = (
                '<div class="hero-banner">'
                '<div class="hero-left">'
                '<div class="verdict-stamp"><span>ERROR</span></div>'
                '<div class="hero-copy">'
                '<h1 class="headline">Couldn’t check this clip</h1>'
                f'<div class="hero-meta">{err}</div>'
                "</div></div></div>"
            )
            meta_refresh = ""
        else:
            body = (
                '<div class="hero-banner">'
                '<div class="hero-left">'
                f'<div class="verdict-stamp"><span class="stamp-dot"></span><span>{_esc(status.upper())}</span></div>'
                '<div class="hero-copy">'
                f'<h1 class="headline">Checking clip against witnessed record… ({_esc(status)})</h1>'
                f'<div class="hero-meta">Job <span class="mono">{_esc(job_id)}</span> is currently '
                f'<strong>{_esc(status)}</strong> (queued jobs refresh automatically every 2 seconds).</div>'
                "</div></div></div>"
            )
            meta_refresh = '<meta http-equiv="refresh" content="2">'
        return (
            "<!doctype html>\n<html lang=\"en\">\n<head>\n"
            "<meta charset=\"utf-8\">\n"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
            '<link rel="icon" href="data:,">\n'
            f"{meta_refresh}\n"
            f"<title>Wholetake Verdict · {_esc(job_id)}</title>\n"
            f"<style>{_css(vmeta)}</style>\n"
            "</head>\n<body>\n"
            '<div class="top-ribbon"></div>\n<main>\n'
            '<header class="brand"><div class="brand-left"><span class="brand-dot"></span>'
            "<span>WHOLETAKE EVIDENCE RECORD</span></div></header>\n"
            f"{body}\n"
            "</main>\n</body>\n</html>\n"
        )

    headline = _esc(result.get("headline") or "Verdict")
    det_kind = str(result.get("detector_kind") or "full")
    clip = result.get("clip") or {}
    dur_s = max(0.001, float(clip.get("duration_s") or 1.0))
    detection = result.get("detection") or {}
    stamp_obj = detection.get("stamp") if isinstance(detection.get("stamp"), dict) else None
    wit = detection.get("witness") or {}
    vkind = str(wit.get("verdict") or "not_in_record")
    vmeta = VERDICT_META.get(vkind, VERDICT_META["not_in_record"])
    stream = wit.get("stream")
    pieces = wit.get("pieces") or []
    removed = wit.get("removed") or []
    inserted = wit.get("inserted") or []
    coverage = result.get("coverage") or {}
    label = result.get("label")
    proofs = result.get("proofs") or []

    restore_avail = can_restore(wroot, str(stream) if stream else None, vkind, removed)
    embedded_manifest: dict | None = None
    manifest_words_by_cut: dict[int, list[str]] = {}
    if restore_avail and stream:
        try:
            embedded_manifest = preview_restored_manifest(wroot, str(stream), dur_s, removed)
            for seg in embedded_manifest.get("segments") or []:
                if seg.get("kind") == "restored":
                    cidx = int(seg.get("cut_index") or 0)
                    manifest_words_by_cut[cidx] = [str(w.get("w") or "") for w in (seg.get("words") or [])]
        except Exception:
            embedded_manifest = None

    badges_html: list[str] = []
    if det_kind == "text-only":
        badges_html.append(
            '<span class="caveat-chip" tabindex="0">ⓘ text match only'
            '<span class="caveat-tip">Matched via transcript word timing only; acoustic fingerprint '
            "verification was not used for this run.</span></span>"
        )
    else:
        badges_html.append('<span class="badge">acoustic + transcript match</span>')

    if stamp_obj and stamp_obj.get("present"):
        badges_html.append('<span class="badge">Roomstamp verified</span>')

    stream_display = STREAM_NAMES.get(str(stream), str(stream)) if stream else "No matched stream"
    first_rec_utc = None
    for pr in proofs:
        if isinstance(pr, dict) and pr.get("chunk_received_utc"):
            first_rec_utc = str(pr["chunk_received_utc"])
            break
    utc_human = _fmt_utc_human(first_rec_utc)
    meta_parts = [stream_display]
    if utc_human:
        meta_parts.append(utc_human)
    meta_parts.append(f"Clip {dur_s:.1f}s")
    hero_meta_str = " · ".join(_esc(p) for p in meta_parts)

    plain_h1 = _esc(_plain_english_summary(vkind, pieces, removed, inserted))

    hear_btn_html = ""
    if removed:
        first_at = max(0.0, min(dur_s, float(removed[0].get("after_clip_s") or 0.0)))
        seek_to = max(0.0, round(first_at - 2.5, 2))
        if restore_avail:
            hear_btn_html = (
                f'<button type="button" class="hear-cut-btn" id="hearCutBtn" '
                f'data-restore="1" data-seek="{seek_to}" data-cut="{first_at:.2f}">'
                f"▶ Hear what they cut</button>"
            )
        else:
            hear_btn_html = (
                f'<button type="button" class="hear-cut-btn" id="hearCutBtn" '
                f'data-restore="0" data-seek="{seek_to}" data-cut="{first_at:.2f}">'
                f"▶ Hear the cut ({_esc(_fmt_mmss(first_at))})</button>"
            )

    offsets, dur_by_seq, all_seqs = _build_record_time_domain(wroot, stream, pieces, removed)
    svg_html, js_segments, js_cuts = _build_two_track_svg(
        vkind, dur_s, pieces, removed, inserted, offsets, dur_by_seq, all_seqs
    )

    ctx_info = edge_context(wroot, str(stream) if stream else None, pieces) if stream and pieces else {"before": None, "after": None}
    before_drawer_html = _render_context_drawer(job_id, "before", ctx_info.get("before"))
    after_drawer_html = _render_context_drawer(job_id, "after", ctx_info.get("after"))

    record_sentences: list[str] = []
    for idx, r in enumerate(removed):
        src_s = r.get("src_s") or [0.0, 0.0]
        cut_dur = max(0.1, round(float(src_s[1]) - float(src_s[0]), 1))
        before_w, mid_tokens, after_w = _load_sentence_highlight(
            wroot, stream, r, manifest_words_by_cut.get(idx)
        )
        quote_header = _quote_wall_span(wroot, stream, r, proofs)
        token_spans = " ".join(
            f'<span class="kw-word" data-cut-idx="{idx}" data-word-idx="{wi}">{_esc(tok)}</span>'
            for wi, tok in enumerate(mid_tokens)
        )
        record_sentences.append(
            f'<div class="record-quote">'
            f'<div class="quote-meta">{_esc(quote_header)}</div>'
            f'<span>{_esc(before_w)}<span class="cut-tag">✂ −{_esc(f"{cut_dur:.1f}s")}</span>'
            f'<mark class="removed" id="removedMark{idx}">{token_spans}</mark>{_esc(after_w)}</span>'
            f"</div>"
        )

    unavail_note_html = ""
    if removed and not restore_avail:
        unavail_note_html = (
            '<div class="restore-unavail-note">'
            "Record audio not on this server: the words come from the hash-chained transcript"
            "</div>"
        )

    if record_sentences:
        record_sec = (
            '<section class="card">'
            '<div class="sec-head"><h2 class="sec-title">What the record says</h2></div>'
            + before_drawer_html
            + "".join(record_sentences)
            + unavail_note_html
            + after_drawer_html
            + "</section>"
        )
    elif vkind == "faithful":
        record_sec = (
            '<section class="card">'
            '<div class="sec-head"><h2 class="sec-title">What the record says</h2></div>'
            + before_drawer_html
            + "<p>No words were removed between matched segments of the witnessed record.</p>"
            + after_drawer_html
            + "</section>"
        )
    elif vkind == "spliced":
        record_sec = (
            '<section class="card">'
            '<div class="sec-head"><h2 class="sec-title">What the record says</h2></div>'
            + before_drawer_html
            + "<p>This clip joins non-contiguous segments from the witnessed record.</p>"
            + after_drawer_html
            + "</section>"
        )
    else:
        record_sec = (
            '<section class="card">'
            '<div class="sec-head"><h2 class="sec-title">What the record says</h2></div>'
            "<p>No matching segment was found in our witnessed record.</p>"
            "</section>"
        )

    right_col_cards: list[str] = []
    advisory_items: list[str] = []
    if isinstance(label, dict):
        labels_list = label.get("spans") if isinstance(label.get("spans"), list) else []
        if not labels_list and (label.get("effect") or label.get("why")):
            labels_list = [label]
        for item in labels_list:
            if not isinstance(item, dict):
                continue
            eff = item.get("effect")
            why = item.get("why")
            if eff or why:
                advisory_items.append(
                    f'<p style="margin:6px 0 0;"><span class="effect-pill">{_esc(eff or "advisory")}</span>'
                    f"{_esc(why or '')}</p>"
                )
    if advisory_items:
        right_col_cards.append(
            '<section class="card advisory">'
            '<div class="sec-head"><h2 class="sec-title">AI note (advisory)</h2></div>'
            + "".join(advisory_items)
            + "</section>"
        )

    cov_notes = coverage.get("notes") or []
    if cov_notes:
        items = "".join(f"<li>{_esc(n)}</li>" for n in cov_notes)
        right_col_cards.append(
            '<section class="card">'
            '<div class="sec-head"><h2 class="sec-title">Coverage notes</h2></div>'
            f'<ul style="margin:6px 0 0;padding-left:18px;">{items}</ul>'
            "</section>"
        )

    if right_col_cards:
        mid_grid_html = f'<div class="mid-grid">{record_sec}<div>{"".join(right_col_cards)}</div></div>'
    else:
        mid_grid_html = f'<div class="mid-grid single">{record_sec}</div>'

    proof_rows: list[str] = []
    any_proof_ok = False
    first_tx: str | None = None
    first_head_seq: object = None
    first_head_hash: str = ""
    chunk_seqs: list[str] = []

    for pr in proofs:
        if not isinstance(pr, dict):
            continue
        seq = pr.get("seq")
        if seq is not None:
            chunk_seqs.append(f"#{_esc(seq)}")
        rec_utc = pr.get("chunk_received_utc") or "—"
        anchor = pr.get("anchor") if isinstance(pr.get("anchor"), dict) else {}
        tx = anchor.get("tx") or anchor.get("tx_hash")
        if tx and not first_tx:
            first_tx = str(tx)
        if tx:
            short_tx = f"{str(tx)[:10]}…{str(tx)[-6:]}" if len(str(tx)) > 18 else str(tx)
            tx_html = f'<a class="mono" href="{_esc(BASESCAN_TX_PREFIX + str(tx))}">{_esc(short_tx)}</a>'
        else:
            tx_html = '<span class="mono">anchoring pending</span>'

        head_seq = anchor.get("head_seq") if anchor.get("head_seq") is not None else pr.get("head_seq", seq)
        head_hash = str(anchor.get("head_hash") or pr.get("head_hash") or pr.get("entry_hash") or "")
        if first_head_seq is None and head_seq is not None:
            first_head_seq = head_seq
        if not first_head_hash and head_hash:
            first_head_hash = head_hash
        short_hash = f"{head_hash[:10]}…" if len(head_hash) > 10 else (head_hash or "—")
        if pr.get("proof_ok"):
            any_proof_ok = True
        ok_str = "verified" if pr.get("proof_ok") else "unverified"
        proof_rows.append(
            "<tr>"
            f'<td class="mono">#{_esc(seq)}</td>'
            f'<td class="mono">{_esc(rec_utc)}</td>'
            f"<td>{tx_html}</td>"
            f'<td class="mono">n={_esc(head_seq)} / {_esc(short_hash)}</td>'
            f'<td class="mono">{_esc(ok_str)}</td>'
            "</tr>"
        )

    custody_steps: list[str] = []
    if proofs and stream:
        rec_dt = _parse_iso(first_rec_utc)
        t_short = _fmt_wall_hms(rec_dt) if rec_dt else (first_rec_utc or "recorded")
        custody_steps.append(
            '<div class="custody-step ok">'
            '<div class="step-top"><span class="step-icon ok">✓</span><span>Witnessed live</span></div>'
            f'<div class="step-detail">{_esc(stream)} · {", ".join(chunk_seqs)} · {_esc(t_short)}</div>'
            "</div>"
        )
    else:
        custody_steps.append(
            '<div class="custody-step">'
            '<div class="step-top"><span class="step-icon neutral">–</span><span>Witness lookup</span></div>'
            '<div class="step-detail">No matched witness chunks</div>'
            "</div>"
        )

    if proofs and any_proof_ok:
        if first_head_hash:
            h_short = f"{first_head_hash[:10]}…" if len(first_head_hash) > 10 else first_head_hash
            step2_detail = f"n={_esc(first_head_seq)} · {_esc(h_short)} · proof verified"
        else:
            step2_detail = f"n={_esc(first_head_seq)} · proof verified"
        custody_steps.append(
            '<div class="custody-step ok">'
            '<div class="step-top"><span class="step-icon ok">✓</span><span>Hash-chained</span></div>'
            f'<div class="step-detail">{step2_detail}</div>'
            "</div>"
        )
    else:
        custody_steps.append(
            '<div class="custody-step">'
            '<div class="step-top"><span class="step-icon neutral">–</span><span>Hash chain</span></div>'
            '<div class="step-detail">unverified</div>'
            "</div>"
        )

    n_pieces = len(pieces)
    piece_word = "piece" if n_pieces == 1 else "pieces"
    if det_kind == "text-only":
        step3_title = f"Transcript aligned · {n_pieces} {piece_word}"
        step3_detail = "text match only"
        step3_cls = "neutral"
        step3_icon = "ⓘ"
    else:
        step3_title = "Audio + transcript matched"
        step3_detail = f"{n_pieces} {piece_word} aligned"
        step3_cls = "ok"
        step3_icon = "✓"
    custody_steps.append(
        f'<div class="custody-step {step3_cls}">'
        f'<div class="step-top"><span class="step-icon {step3_cls}">{step3_icon}</span><span>{_esc(step3_title)}</span></div>'
        f'<div class="step-detail">{_esc(step3_detail)}</div>'
        "</div>"
    )

    if first_tx:
        short_tx = f"{first_tx[:10]}…{first_tx[-6:]}" if len(first_tx) > 18 else first_tx
        custody_steps.append(
            '<div class="custody-step ok">'
            '<div class="step-top"><span class="step-icon ok">✓</span><span>Anchored on Base</span></div>'
            f'<div class="step-detail"><a class="mono" href="{_esc(BASESCAN_TX_PREFIX + first_tx)}">{_esc(short_tx)}</a></div>'
            "</div>"
        )
    else:
        custody_steps.append(
            '<div class="custody-step pending">'
            '<div class="step-top"><span class="step-icon pending pulse-dot">●</span><span>Base L2 anchor</span></div>'
            '<div class="step-detail">anchoring pending</div>'
            "</div>"
        )

    if stamp_obj is not None:
        st_present = bool(stamp_obj.get("present"))
        st_utc = _esc(stamp_obj.get("recorded_at_utc") or ("verified" if st_present else "not detected"))
        st_cls = "ok" if st_present else "neutral"
        st_icon = "✓" if st_present else "–"
        custody_steps.append(
            f'<div class="custody-step {st_cls}">'
            f'<div class="step-top"><span class="step-icon {st_cls}">{st_icon}</span><span>Roomstamp</span></div>'
            f'<div class="step-detail">{st_utc}</div>'
            "</div>"
        )

    proof_table_html = (
        '<details class="raw-proofs">'
        f"<summary>Raw chunk proofs ({len(proof_rows)})</summary>"
        '<div class="proof-table-wrap"><table class="proof-table">'
        "<thead><tr><th>Chunk</th><th>Received (UTC)</th><th>Base Anchor</th><th>Chain Head</th><th>Proof</th></tr></thead>"
        f"<tbody>{''.join(proof_rows)}</tbody></table></div></details>"
        if proof_rows
        else "<p class=\"mono\" style=\"margin-top:10px;color:var(--muted);\">No matched witness chunks.</p>"
    )

    proof_sec = (
        '<section class="card">'
        '<div class="sec-head"><h2 class="sec-title">Chain of custody &amp; cryptographic proof</h2></div>'
        f'<div class="custody-strip">{"".join(custody_steps)}</div>'
        f"{proof_table_html}"
        "</section>"
    )

    restored_audio_html = (
        f'<audio id="restoredAudio" preload="auto" src="/v/{_esc(job_id)}/restored.wav" style="display:none"></audio>'
        if restore_avail
        else ""
    )
    context_audio_html = '<audio id="contextAudio" preload="none" style="display:none"></audio>'

    first_cut_at = float(removed[0].get("after_clip_s") or -1.0) if removed else -1.0
    first_cut_dur = (
        max(0.1, round(float((removed[0].get("src_s") or [0.0, 0.0])[1]) - float((removed[0].get("src_s") or [0.0, 0.0])[0]), 1))
        if removed
        else 0.9
    )

    inline_js = f"""
<script>
(() => {{
  const audio = document.getElementById("clipAudio");
  const restoredAudio = document.getElementById("restoredAudio");
  const contextAudio = document.getElementById("contextAudio");
  const phClip = document.getElementById("playheadClip");
  const phRec = document.getElementById("playheadRec");
  const hearBtn = document.getElementById("hearCutBtn");
  const banner = document.getElementById("restoredBanner");
  const dur = {dur_s:.4f};
  const segs = {json.dumps(js_segments)};
  const cuts = {json.dumps(js_cuts)};
  const cutAt = {first_cut_at:.3f};
  const restoreAvail = {"true" if restore_avail else "false"};
  const manifestUrl = "/v/{_esc(job_id)}/restored.json";
  let manifestData = {json.dumps(embedded_manifest)};
  let manifestPromise = null;
  let restoredStopAt = null;
  let restoredRafId = null;
  let restoredTimerId = null;
  let restoredMode = false;

  function loadManifest() {{
    if (manifestData) return Promise.resolve(manifestData);
    if (!manifestPromise) {{
      manifestPromise = fetch(manifestUrl)
        .then(r => r.ok ? r.json() : null)
        .then(m => {{ if (m) manifestData = m; return m; }})
        .catch(() => null);
    }}
    return manifestPromise;
  }}

  if (restoreAvail && !manifestData) {{
    loadManifest();
  }}

  function mapClipToRecX(t) {{
    if (segs.length === 0) return 116;
    let rx = segs[0].rx0;
    for (const s of segs) {{
      if (t >= s.c0 && t <= s.c1) {{
        const frac = (s.c1 > s.c0) ? (t - s.c0) / (s.c1 - s.c0) : 0;
        rx = s.rx0 + frac * (s.rx1 - s.rx0);
        break;
      }} else if (t > s.c1) {{
        rx = s.rx1;
      }}
    }}
    return rx;
  }}

  function clearActiveHighlightOnly() {{
    if (banner) banner.classList.remove("active");
    for (const el of document.querySelectorAll(".pulse-excised")) {{
      el.classList.remove("pulse-excised");
    }}
    for (const el of document.querySelectorAll(".kw-active")) {{
      el.classList.remove("kw-active");
    }}
  }}

  function resetAllRestoredUI() {{
    clearActiveHighlightOnly();
    for (const el of document.querySelectorAll(".kw-spoken")) {{
      el.classList.remove("kw-spoken");
    }}
  }}

  function stopRestoredLoop(resetWords) {{
    restoredMode = false;
    if (restoredRafId !== null) {{
      cancelAnimationFrame(restoredRafId);
      restoredRafId = null;
    }}
    if (restoredTimerId !== null) {{
      clearInterval(restoredTimerId);
      restoredTimerId = null;
    }}
    if (resetWords) {{
      resetAllRestoredUI();
    }} else {{
      clearActiveHighlightOnly();
    }}
  }}

  function syncPlayheads() {{
    if (!audio) return;
    const t = Math.max(0, Math.min(dur, audio.currentTime || 0));
    const cx = 116 + (t / dur) * 744;
    if (phClip) {{
      phClip.setAttribute("x1", cx.toFixed(2));
      phClip.setAttribute("x2", cx.toFixed(2));
    }}
    if (phRec && segs.length > 0) {{
      const rx = mapClipToRecX(t);
      phRec.setAttribute("x1", rx.toFixed(2));
      phRec.setAttribute("x2", rx.toFixed(2));
    }}
    const mark0 = document.getElementById("removedMark0");
    if (mark0 && cutAt >= 0) {{
      if (Math.abs(t - cutAt) < 0.45 && !audio.paused) {{
        mark0.classList.add("flash-cut");
      }} else {{
        mark0.classList.remove("flash-cut");
      }}
    }}
    if (!audio.paused) {{
      requestAnimationFrame(syncPlayheads);
    }}
  }}

  function syncRestoredPlayhead() {{
    if (!restoredAudio || !manifestData) return;
    const t = restoredAudio.currentTime || 0;
    if (restoredStopAt !== null && t >= restoredStopAt) {{
      restoredStopAt = null;
      restoredAudio.pause();
      stopRestoredLoop(true);
      return;
    }}
    const mSegs = manifestData.segments || [];
    let activeSeg = null;
    for (const s of mSegs) {{
      if (t >= s.t0 && t < s.t1) {{
        activeSeg = s;
        break;
      }}
    }}

    if (!activeSeg || activeSeg.kind !== "restored") {{
      clearActiveHighlightOnly();
      for (const s of mSegs) {{
        if (s.kind === "restored") {{
          const cIdx = s.cut_index || 0;
          const words = s.words || [];
          for (let wi = 0; wi < words.length; wi++) {{
            const span = document.querySelector('.kw-word[data-cut-idx="' + cIdx + '"][data-word-idx="' + wi + '"]');
            if (!span) continue;
            if (t >= words[wi].t1) {{
              span.classList.add("kw-spoken");
            }} else {{
              span.classList.remove("kw-spoken");
            }}
          }}
        }}
      }}
      if (activeSeg && activeSeg.kind === "clip" && activeSeg.clip_s) {{
        const frac = (activeSeg.t1 > activeSeg.t0) ? (t - activeSeg.t0) / (activeSeg.t1 - activeSeg.t0) : 0;
        const ct = activeSeg.clip_s[0] + frac * (activeSeg.clip_s[1] - activeSeg.clip_s[0]);
        const cx = 116 + (Math.max(0, Math.min(dur, ct)) / dur) * 744;
        const rx = mapClipToRecX(ct);
        if (phClip) {{ phClip.setAttribute("x1", cx.toFixed(2)); phClip.setAttribute("x2", cx.toFixed(2)); }}
        if (phRec) {{ phRec.setAttribute("x1", rx.toFixed(2)); phRec.setAttribute("x2", rx.toFixed(2)); }}
      }}
      return;
    }}

    const cIdx = activeSeg.cut_index || 0;
    const cutInfo = cuts[cIdx] || cuts[0];
    if (cutInfo) {{
      const frac = (activeSeg.t1 > activeSeg.t0) ? (t - activeSeg.t0) / (activeSeg.t1 - activeSeg.t0) : 0;
      const rx = cutInfo.rx0 + frac * (cutInfo.rx1 - cutInfo.rx0);
      if (phClip) {{ phClip.setAttribute("x1", cutInfo.cx.toFixed(2)); phClip.setAttribute("x2", cutInfo.cx.toFixed(2)); }}
      if (phRec) {{ phRec.setAttribute("x1", rx.toFixed(2)); phRec.setAttribute("x2", rx.toFixed(2)); }}
      for (const el of document.querySelectorAll(".pulse-excised")) {{
        if (el.id !== "excisedBlock" + cIdx) el.classList.remove("pulse-excised");
      }}
      const blk = document.getElementById("excisedBlock" + cIdx);
      if (blk) blk.classList.add("pulse-excised");
      if (banner) {{
        banner.textContent = "▶ From the record: the " + cutInfo.dur_s.toFixed(1) + " s the clip removed";
        banner.classList.add("active");
      }}
    }}

    const words = activeSeg.words || [];
    for (let wi = 0; wi < words.length; wi++) {{
      const w = words[wi];
      const span = document.querySelector('.kw-word[data-cut-idx="' + cIdx + '"][data-word-idx="' + wi + '"]');
      if (!span) continue;
      if (t >= w.t0 && t < w.t1) {{
        span.classList.add("kw-active");
        span.classList.remove("kw-spoken");
      }} else if (t >= w.t1) {{
        span.classList.remove("kw-active");
        span.classList.add("kw-spoken");
      }} else {{
        span.classList.remove("kw-active");
        span.classList.remove("kw-spoken");
      }}
    }}
  }}

  function startRestoredLoop() {{
    stopRestoredLoop(false);
    restoredMode = true;
    syncRestoredPlayhead();
    const rafStep = () => {{
      if (!restoredMode || !restoredAudio || restoredAudio.paused) return;
      syncRestoredPlayhead();
      restoredRafId = requestAnimationFrame(rafStep);
    }};
    restoredRafId = requestAnimationFrame(rafStep);
    restoredTimerId = setInterval(() => {{
      if (!restoredMode || !restoredAudio || restoredAudio.paused) return;
      syncRestoredPlayhead();
    }}, 16);
  }}

  function beginRestoredAt(manifest, cutIndex) {{
    if (!restoredAudio || !manifest || !manifest.segments) return;
    if (audio && !audio.paused) audio.pause();
    if (contextAudio && !contextAudio.paused) contextAudio.pause();
    stopRestoredLoop(true);
    const restoredSegs = manifest.segments.filter(s => s.kind === "restored");
    const target = restoredSegs.find(s => (s.cut_index || 0) === cutIndex) || restoredSegs[cutIndex] || restoredSegs[0];
    if (!target) return;
    const startT = Math.max(0, target.t0 - 0.12 - 2.5);
    restoredStopAt = Math.min(manifest.duration_s || (target.t1 + 2.62), target.t1 + 0.12 + 2.5);

    const seekAndPlay = () => {{
      try {{
        restoredAudio.currentTime = startT;
      }} catch (_) {{}}
      syncRestoredPlayhead();
      if (restoredAudio.paused) {{
        restoredAudio.play().then(() => {{
          startRestoredLoop();
        }}).catch(() => {{}});
      }} else {{
        startRestoredLoop();
      }}
    }};

    if (restoredAudio.readyState >= 1) {{
      seekAndPlay();
    }} else {{
      restoredAudio.addEventListener("loadedmetadata", seekAndPlay, {{ once: true }});
      restoredAudio.load();
    }}
  }}

  function playRestoredCut(cutIndex) {{
    if (!restoredAudio) return;
    if (manifestData) {{
      beginRestoredAt(manifestData, cutIndex);
    }} else {{
      loadManifest().then(m => {{
        if (m) beginRestoredAt(m, cutIndex);
      }});
    }}
  }}

  if (audio) {{
    audio.addEventListener("play", () => {{
      if (restoredAudio && !restoredAudio.paused) restoredAudio.pause();
      if (contextAudio && !contextAudio.paused) contextAudio.pause();
      stopRestoredLoop(true);
      requestAnimationFrame(syncPlayheads);
    }});
    audio.addEventListener("playing", () => {{
      stopRestoredLoop(true);
      requestAnimationFrame(syncPlayheads);
    }});
    audio.addEventListener("timeupdate", syncPlayheads);
    audio.addEventListener("seeked", syncPlayheads);
  }}
  if (restoredAudio) {{
    restoredAudio.addEventListener("playing", () => {{
      startRestoredLoop();
    }});
    restoredAudio.addEventListener("timeupdate", () => {{
      if (restoredMode) syncRestoredPlayhead();
    }});
    restoredAudio.addEventListener("seeked", () => {{
      if (restoredMode) syncRestoredPlayhead();
    }});
    restoredAudio.addEventListener("pause", () => {{
      stopRestoredLoop(true);
    }});
    restoredAudio.addEventListener("ended", () => {{
      stopRestoredLoop(true);
    }});
  }}
  if (hearBtn) {{
    hearBtn.addEventListener("click", () => {{
      if (restoreAvail && restoredAudio) {{
        playRestoredCut(0);
      }} else if (audio) {{
        const seek = parseFloat(hearBtn.getAttribute("data-seek") || "0");
        audio.currentTime = isNaN(seek) ? 0 : seek;
        audio.play().catch(() => {{}});
      }}
    }});
  }}
  for (const el of document.querySelectorAll(".cut-callout[data-cut-at]")) {{
    el.addEventListener("click", () => {{
      const cIdx = parseInt(el.getAttribute("data-cut-idx") || "0", 10);
      if (restoreAvail && restoredAudio) {{
        playRestoredCut(isNaN(cIdx) ? 0 : cIdx);
        return;
      }}
      if (!audio) return;
      const at = parseFloat(el.getAttribute("data-cut-at") || "0");
      audio.currentTime = Math.max(0, at - 2.0);
      audio.play().catch(() => {{}});
    }});
  }}
  for (const btn of document.querySelectorAll(".ctx-play-btn[data-ctx-src]")) {{
    btn.addEventListener("click", () => {{
      if (!contextAudio) return;
      if (audio && !audio.paused) audio.pause();
      if (restoredAudio && !restoredAudio.paused) restoredAudio.pause();
      stopRestoredLoop(true);
      const src = btn.getAttribute("data-ctx-src");
      if (src) {{
        contextAudio.src = src;
        contextAudio.currentTime = 0;
        contextAudio.play().catch(() => {{}});
      }}
    }});
  }}
}})();
</script>
""".strip()

    return (
        "<!doctype html>\n<html lang=\"en\">\n<head>\n"
        "<meta charset=\"utf-8\">\n"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
        '<link rel="icon" href="data:,">\n'
        f"<title>{headline} · Wholetake</title>\n"
        f'<meta property="og:description" content="{headline}">\n'
        f"<style>{_css(vmeta)}</style>\n"
        "</head>\n<body>\n"
        '<div class="top-ribbon"></div>\n'
        "<main>\n"
        '<header class="brand">'
        '<div class="brand-left"><span class="brand-dot"></span><span>WHOLETAKE EVIDENCE RECORD</span></div>'
        f'<div class="badges">{"".join(badges_html)}</div>'
        "</header>\n"
        '<div class="hero-banner">'
        '<div class="hero-left">'
        f'<div class="verdict-stamp"><span class="stamp-dot"></span><span>{_esc(vmeta["stamp"])}</span></div>'
        '<div class="hero-copy">'
        f'<h1 class="headline">{plain_h1}</h1>'
        f'<div class="hero-meta">{hero_meta_str}</div>'
        "</div></div>"
        f"{hear_btn_html}"
        "</div>\n"
        '<section class="card">'
        '<div class="sec-head">'
        '<h2 class="sec-title">Two-track alignment</h2>'
        '<span class="sec-sub">Witnessed record vs. posted clip</span>'
        "</div>"
        '<div class="svg-stage">'
        f'<div id="restoredBanner" class="restored-banner">▶ From the record: the {_esc(f"{first_cut_dur:.1f}")} s the clip removed</div>'
        f"{svg_html}"
        "</div>"
        '<div class="audio-bar">'
        f'<audio id="clipAudio" class="player" controls preload="metadata" src="/v/{_esc(job_id)}/clip.wav"></audio>'
        f"{restored_audio_html}{context_audio_html}"
        "</div>"
        "</section>\n"
        f"{mid_grid_html}\n"
        f"{proof_sec}\n"
        '<footer class="links">'
        f'<span class="mono">verdict_id: {_esc(str(result.get("verdict_id") or "")[:16])}…</span>'
        f'<a href="/v/{_esc(job_id)}/verdict.json" download="verdict-{_esc(job_id)}.json">Download verdict.json</a>'
        "</footer>\n"
        "</main>\n"
        f"{inline_js}\n"
        "</body>\n</html>\n"
    )
