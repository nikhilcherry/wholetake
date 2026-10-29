"""Self-contained HTML evidence page renderer for Wholetake verdicts (no external requests)."""
import html
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from verdict.compose import _fmt_mmss

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


def _load_sentence_highlight(witness_root: Path, stream: str | None, rem: dict) -> tuple[str, str, str]:
    """Return (before_words, removed_words, after_words) from the witness chunk transcript."""
    words_removed = str(rem.get("words") or "").strip()
    seq = rem.get("seq")
    src_s = rem.get("src_s") or [0.0, 0.0]
    s0, s1 = float(src_s[0]), float(src_s[1])

    if stream and isinstance(seq, int):
        tx = _load_chunk_meta(witness_root, stream, seq)
        wlist = tx.get("words") or []
        if wlist:
            try:
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
                    lo = max(0, hits[0] - 8)
                    hi = min(len(wlist), hits[-1] + 1 + 8)
                    before = " ".join(str(w.get("w", "")).strip() for w in wlist[lo : hits[0]]).strip()
                    mid = " ".join(str(w.get("w", "")).strip() for w in wlist[hits[0] : hits[-1] + 1]).strip()
                    after = " ".join(str(w.get("w", "")).strip() for w in wlist[hits[-1] + 1 : hi]).strip()
                    return (
                        (f"… {before} " if lo > 0 and before else (f"{before} " if before else "")),
                        mid or words_removed,
                        (f" {after} …" if hi < len(wlist) and after else (f" {after}" if after else "")),
                    )
            except Exception:
                pass
    return ("… ", words_removed, " …")


def _quote_wall_span(witness_root: Path, stream: str | None, rem: dict) -> str:
    """Return 'chunk #4 · 18:54:00–18:54:11 UTC' if chunk timestamps exist, else 'chunk #4'."""
    seq = rem.get("seq")
    seq_label = f"chunk #{seq}" if seq is not None else "record"
    if not stream or not isinstance(seq, int):
        return seq_label

    tx = _load_chunk_meta(witness_root, stream, seq)
    base_dt = _parse_iso(tx.get("start_utc_est") or tx.get("received_utc"))
    if not base_dt:
        return seq_label

    src_s = rem.get("src_s") or [0.0, 0.0]
    s0, s1 = float(src_s[0]), float(src_s[1])
    t_lo, t_hi = s0, s1

    wlist = tx.get("words") or []
    if wlist:
        try:
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
                lo = max(0, hits[0] - 8)
                hi = min(len(wlist), hits[-1] + 1 + 8)
                t_lo = float(wlist[lo].get("s", s0))
                t_hi = float(wlist[hi - 1].get("e", s1))
        except Exception:
            pass

    dt0 = base_dt + timedelta(seconds=max(0.0, t_lo))
    dt1 = base_dt + timedelta(seconds=max(t_lo, t_hi))
    return f"{seq_label} · {dt0.strftime('%H:%M:%S')}–{dt1.strftime('%H:%M:%S')} UTC"


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
) -> tuple[dict[int, float], dict[int, float], datetime | None, list[int]]:
    """Compute absolute record offset (seconds from first chunk start) per seq, including neighboring context chunks."""
    seqs: set[int] = set()
    for item in list(pieces) + list(removed):
        sq = item.get("seq")
        if isinstance(sq, int):
            seqs.add(sq)
    if not seqs:
        return ({0: 0.0}, {0: 20.0}, None, [0])

    min_seq = min(seqs)
    max_seq = max(seqs)
    base_seqs = list(range(min_seq, max_seq + 1)) if (max_seq - min_seq) <= 12 else sorted(seqs)

    # Probe adjacent chunk files (min_seq - 1, max_seq + 1) so padding can use real witnessed neighbors if present
    probe_seqs = list(base_seqs)
    if stream:
        if min_seq > 0 and (witness_root / stream / f"{min_seq - 1:06d}.json").exists():
            probe_seqs.insert(0, min_seq - 1)
        if (witness_root / stream / f"{max_seq + 1:06d}.json").exists():
            probe_seqs.append(max_seq + 1)

    meta_by_seq: dict[int, dict] = {sq: _load_chunk_meta(witness_root, stream, sq) for sq in probe_seqs}
    dt_by_seq: dict[int, datetime] = {}
    dur_by_seq: dict[int, float] = {}
    for sq in probe_seqs:
        m = meta_by_seq.get(sq) or {}
        dt = _parse_iso(m.get("start_utc_est") or m.get("received_utc"))
        if dt:
            dt_by_seq[sq] = dt
        dur_by_seq[sq] = float(m.get("duration_s") or 20.0)

    ref_seq = base_seqs[0]
    ref_dt = dt_by_seq.get(ref_seq)
    default_dur = dur_by_seq.get(ref_seq, 20.0)

    offsets: dict[int, float] = {}
    for sq in probe_seqs:
        if ref_dt and sq in dt_by_seq:
            offsets[sq] = (dt_by_seq[sq] - ref_dt).total_seconds()
        else:
            offsets[sq] = (sq - ref_seq) * default_dur

    return (offsets, dur_by_seq, ref_dt, probe_seqs)


def _build_two_track_svg(
    vkind: str,
    dur_s: float,
    pieces: list[dict],
    removed: list[dict],
    inserted: list[dict],
    offsets: dict[int, float],
    dur_by_seq: dict[int, float],
    first_dt: datetime | None,
    all_seqs: list[int],
) -> tuple[str, list[dict]]:
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
        return svg, []

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

    # Compute what the witnessed chunks actually cover, and pad the record domain on both sides
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
        # Faint edge fade + "…" on the far ends of the padded record track
        f'<rect x="{x0}" y="21" width="32" height="32" rx="5" fill="url(#edgeFadeLeft)"/>',
        f'<rect x="{x1 - 32}" y="21" width="32" height="32" rx="5" fill="url(#edgeFadeRight)"/>',
        f'<text x="{x0 + 8}" y="41" class="svg-context-dots">…</text>',
        f'<text x="{x1 - 8}" y="41" text-anchor="end" class="svg-context-dots">…</text>',
    ]

    # Chunk boundary ticks on the record track
    for sq in all_seqs:
        off = offsets.get(sq, 0.0)
        if rec_min <= off <= rec_max:
            bx = rec_x(off)
            parts.append(
                f'<line x1="{bx}" y1="16" x2="{bx}" y2="56" stroke="#64748B" stroke-dasharray="2 2" stroke-width="1"/>'
            )
            parts.append(f'<text x="{bx + 3}" y="14" class="svg-chunk-tick">#{_esc(sq)}</text>')
        elif off < rec_min < off + dur_by_seq.get(sq, 20.0):
            parts.append(f'<text x="{x0 + 2}" y="14" class="svg-chunk-tick">#{_esc(sq)}</text>')

    # Record axis wall-clock or relative labels for the padded record span
    if first_dt is not None:
        start_wall = _fmt_wall_hms(first_dt + timedelta(seconds=rec_min))
        end_wall = _fmt_wall_hms(first_dt + timedelta(seconds=rec_max))
    else:
        start_wall = f"{rec_min:.1f}s"
        end_wall = f"{rec_max:.1f}s"
    parts.append(f'<text x="{x0 + 36}" y="14" class="svg-axis">{_esc(start_wall)}</text>')
    parts.append(f'<text x="{x1}" y="14" text-anchor="end" class="svg-axis">{_esc(end_wall)}</text>')

    # Clip axis labels
    parts.append(f'<text x="{x0}" y="163" class="svg-axis">0:00</text>')
    parts.append(f'<text x="{x1}" y="163" text-anchor="end" class="svg-axis">{_esc(_fmt_mmss(dur_s))}</text>')

    # Ribbons (~0.16 fill opacity + 1.25px stroke) and matched piece bars with a 2px gap at piece boundaries
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

    # Excised spans on RECORD track (with pinch guide lines to CLIP seam)
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
            f'<g class="cut-callout" data-cut-at="{at_s:.2f}" tabindex="0">'
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

    # Inserted / Spliced spans on CLIP track (inverse funnel)
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
    return "".join(parts), js_segments


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

    # Top right caveat / match badge (no duplicate verdict word chip)
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

    # Stream & timestamp line
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

    # Action button to hear the first cut seam
    hear_btn_html = ""
    if removed:
        first_at = max(0.0, min(dur_s, float(removed[0].get("after_clip_s") or 0.0)))
        seek_to = max(0.0, round(first_at - 2.5, 2))
        hear_btn_html = (
            f'<button type="button" class="hear-cut-btn" id="hearCutBtn" '
            f'data-seek="{seek_to}" data-cut="{first_at:.2f}">'
            f"▶ Hear the cut ({_esc(_fmt_mmss(first_at))})</button>"
        )

    # Record time domain & Two-Track SVG
    offsets, dur_by_seq, first_dt, all_seqs = _build_record_time_domain(wroot, stream, pieces, removed)
    svg_html, js_segments = _build_two_track_svg(
        vkind, dur_s, pieces, removed, inserted, offsets, dur_by_seq, first_dt, all_seqs
    )

    # Transcript diff section ("What the record says")
    record_sentences: list[str] = []
    for idx, r in enumerate(removed):
        src_s = r.get("src_s") or [0.0, 0.0]
        cut_dur = max(0.1, round(float(src_s[1]) - float(src_s[0]), 1))
        before_w, mid_w, after_w = _load_sentence_highlight(wroot, stream, r)
        quote_header = _quote_wall_span(wroot, stream, r)
        record_sentences.append(
            f'<div class="record-quote">'
            f'<div class="quote-meta">{_esc(quote_header)}</div>'
            f'<span>{_esc(before_w)}<span class="cut-tag">✂ −{_esc(f"{cut_dur:.1f}s")}</span>'
            f'<mark class="removed" id="removedMark{idx}">{_esc(mid_w)}</mark>{_esc(after_w)}</span>'
            f"</div>"
        )

    if record_sentences:
        record_sec = (
            '<section class="card">'
            '<div class="sec-head"><h2 class="sec-title">What the record says</h2></div>'
            + "".join(record_sentences)
            + "</section>"
        )
    elif vkind == "faithful":
        record_sec = (
            '<section class="card">'
            '<div class="sec-head"><h2 class="sec-title">What the record says</h2></div>'
            "<p>No words were removed between matched segments of the witnessed record.</p>"
            "</section>"
        )
    elif vkind == "spliced":
        record_sec = (
            '<section class="card">'
            '<div class="sec-head"><h2 class="sec-title">What the record says</h2></div>'
            "<p>This clip joins non-contiguous segments from the witnessed record.</p>"
            "</section>"
        )
    else:
        record_sec = (
            '<section class="card">'
            '<div class="sec-head"><h2 class="sec-title">What the record says</h2></div>'
            "<p>No matching segment was found in our witnessed record.</p>"
            "</section>"
        )

    # Secondary column: Advisory AI note + Coverage notes
    right_col_cards: list[str] = []
    advisory_items: list[str] = []
    if isinstance(label, dict):
        labels_list = label.get("spans") if isinstance(label.get("spans"), list) else []
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

    # Chain-of-custody strip + collapsible raw proof table
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
    # Step 1: Witnessed (HH:MM:SS UTC without milliseconds so it never wraps mid-word)
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

    # Step 2: Hash-chained (no duplicate "verified · verified")
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

    # Step 3: Alignment step (honest label per detector_kind)
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

    # Step 4: Base Anchor
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

    # Step 5: Optional Roomstamp step ONLY when detection.stamp is present
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

    # Inline JS for synced dual playhead & "Hear the cut" button
    first_cut_at = float(removed[0].get("after_clip_s") or -1.0) if removed else -1.0
    inline_js = f"""
<script>
(() => {{
  const audio = document.getElementById("clipAudio");
  const phClip = document.getElementById("playheadClip");
  const phRec = document.getElementById("playheadRec");
  const hearBtn = document.getElementById("hearCutBtn");
  const dur = {dur_s:.4f};
  const segs = {json.dumps(js_segments)};
  const cutAt = {first_cut_at:.3f};

  function syncPlayheads() {{
    if (!audio) return;
    const t = Math.max(0, Math.min(dur, audio.currentTime || 0));
    const cx = 116 + (t / dur) * 744;
    if (phClip) {{
      phClip.setAttribute("x1", cx.toFixed(2));
      phClip.setAttribute("x2", cx.toFixed(2));
    }}
    if (phRec && segs.length > 0) {{
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

  if (audio) {{
    audio.addEventListener("play", () => requestAnimationFrame(syncPlayheads));
    audio.addEventListener("timeupdate", syncPlayheads);
    audio.addEventListener("seeked", syncPlayheads);
  }}
  if (hearBtn && audio) {{
    hearBtn.addEventListener("click", () => {{
      const seek = parseFloat(hearBtn.getAttribute("data-seek") || "0");
      audio.currentTime = isNaN(seek) ? 0 : seek;
      audio.play().catch(() => {{}});
    }});
  }}
  for (const el of document.querySelectorAll(".cut-callout[data-cut-at]")) {{
    el.addEventListener("click", () => {{
      if (!audio) return;
      const at = parseFloat(el.getAttribute("data-cut-at") || "0");
      audio.currentTime = Math.max(0, at - 2.0);
      audio.play().catch(() => {{}});
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
        f"{svg_html}"
        '<div class="audio-bar">'
        f'<audio id="clipAudio" class="player" controls preload="metadata" src="/v/{_esc(job_id)}/clip.wav"></audio>'
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
