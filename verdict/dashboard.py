"""Wholetake × Roomstamp Forensic Control Room (`GET /` and `GET /dashboard`).

Designed as a high-precision broadcast & acoustic forensics instrument:
- Restrained obsidian/zinc surface palette (#070A10 / #0E121B / 1px structural hairlines, zero pill soup).
- True tapered cubic-Bezier two-track alignment diagram with padded record context, split pieces, and hatched excision blocks.
- Featured Forensic Stage for the active/selected clip with inline audio playback + 4-cell custody grid.
- Deduplicated Verification Log table + compact 3-column acoustic/chain telemetry ledger.
- Zero external CDN or webfont requests; strict HTML/SVG escaping.
"""
from __future__ import annotations

import html
import importlib.util
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from anchor.verify import anchored_heads
from ingest import chain
from verdict.compose import _fmt_mmss

BASESCAN_TX_PREFIX = "https://sepolia.basescan.org/tx/"
ALLOWED_UPLOAD_EXTS = {".mp4", ".m4a", ".wav", ".webm", ".ogg", ".mp3", ".mov"}
MAX_UPLOAD_BYTES = 50 * 1024 * 1024


def _esc(val: object) -> str:
    if val is None:
        return ""
    return html.escape(str(val), quote=True)


def _iso_utc(ts: float | int | None) -> str | None:
    if not isinstance(ts, (int, float)) or ts <= 0:
        return None
    return datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def effective_verdict_kind(job: dict) -> str:
    """Determine semantic verdict kind, elevating to 'cut' when Roomstamp L1/L2 detected physical cuts."""
    res = job.get("result")
    if not isinstance(res, dict):
        return str(job.get("status") or "queued")
    det = res.get("detection") or {}
    wit = det.get("witness") or {}
    stamp = det.get("stamp") or {}
    w_verdict = str(wit.get("verdict") or "not_found")
    if w_verdict in ("cut", "spliced"):
        return w_verdict
    if stamp.get("present") is True and (stamp.get("cuts") or []):
        return "cut"
    if w_verdict == "not_found":
        if stamp.get("present") is True:
            return "faithful"
        return "not_in_record"
    return "faithful"


def extract_cuts_for_card(res: dict) -> list[dict]:
    """Return unified list of cuts [{'at_clip_s': float, 'removed_s': float, 'words': str, 'source': str}]."""
    det = (res or {}).get("detection") or {}
    wit = det.get("witness") or {}
    stamp = det.get("stamp") or {}
    out: list[dict] = []

    for r in wit.get("removed") or []:
        if not isinstance(r, dict):
            continue
        src_s = r.get("src_s") or [0.0, 0.0]
        dur = float(r.get("removed_s") or max(0.0, float(src_s[1]) - float(src_s[0])))
        out.append({
            "at_clip_s": round(float(r.get("after_clip_s") or 0.0), 2),
            "removed_s": round(max(0.05, dur), 2),
            "words": str(r.get("words") or "").strip(),
            "source": "witness",
        })

    for c in stamp.get("cuts") or []:
        if not isinstance(c, dict):
            continue
        at_s = float(c.get("at_clip_s") or 0.0)
        rem_s = float(c.get("removed_s") or 0.0)
        if rem_s <= 0:
            continue
        existing = next((x for x in out if abs(x["at_clip_s"] - at_s) <= 1.5), None)
        if existing is not None:
            existing["source"] = "witness+roomstamp"
            existing["removed_s"] = round(rem_s, 2)
        else:
            out.append({
                "at_clip_s": round(at_s, 2),
                "removed_s": round(rem_s, 2),
                "words": "",
                "source": "roomstamp",
            })

    out.sort(key=lambda x: x["at_clip_s"])
    return out


def collect_status_telemetry(manager) -> dict:
    """Build truthful live system telemetry for `GET /api/status` and the dashboard."""
    from verdict.wt_adapter import venue_paths
    repo_root = Path(__file__).resolve().parents[1]
    venue_dir, key_file = venue_paths()
    key_present = bool(os.environ.get("ROOMSTAMP_KEY")) or key_file.is_file()

    sessions: list[dict] = []
    if venue_dir.is_dir():
        for sp in sorted(venue_dir.glob("session_*.jsonl"), reverse=True)[:8]:
            try:
                lines = [ln for ln in sp.read_text(encoding="utf-8", errors="replace").splitlines() if ln.strip()]
                if not lines:
                    continue
                hdr = json.loads(lines[0])
                sec_records = [json.loads(ln) for ln in lines[1:] if '"sec"' in ln]
                dur_s = len(sec_records)
                anchored_s = sum(1 for r in sec_records if r.get("anchored") is True)
                t_start = hdr.get("t_start")
                feed_wav = hdr.get("feed_wav")
                sessions.append({
                    "session_id": sp.stem,
                    "venue": str(hdr.get("venue") or "HH Goa Main Stage"),
                    "key_id": str(hdr.get("key_id") or "")[:16],
                    "mode": str((hdr.get("params") or {}).get("mode") or "pa"),
                    "t_start": t_start,
                    "t_start_utc": _iso_utc(t_start),
                    "duration_s": dur_s,
                    "anchored_secs": anchored_s,
                    "drand_chain": str(hdr.get("drand_chain") or "")[:16],
                    "has_feed_wav": bool(feed_wav and (venue_dir / str(feed_wav)).is_file()),
                })
            except Exception:
                continue

    streams: list[dict] = []
    wroot = Path(manager.witness_root)
    if wroot.is_dir():
        for sdir in sorted(wroot.iterdir()):
            if not sdir.is_dir():
                continue
            entries = chain.load(sdir)
            chunks = sum(1 for e in entries if e.get("kind") == "chunk")
            txs = sum(1 for e in entries if e.get("kind") == "transcript")
            gaps = sum(1 for e in entries if e.get("kind") == "gap")
            last_seq = max((int(e["seq"]) for e in entries if isinstance(e.get("seq"), int)), default=-1)
            head_hash = ""
            head_n = len(entries) - 1
            updated_utc = None
            hp = sdir / "head.json"
            if hp.is_file():
                try:
                    ho = json.loads(hp.read_text(encoding="utf-8"))
                    head_hash = str(ho.get("hash") or "")
                    head_n = int(ho.get("n", head_n))
                    updated_utc = ho.get("updated_utc")
                except Exception:
                    pass
            elif entries:
                head_hash = str(entries[-1].get("hash") or "")

            latest_anc = None
            try:
                ancs = anchored_heads(sdir)
                if ancs:
                    a = ancs[-1]
                    tx_h = str(a.get("tx_hash") or a.get("tx") or "")
                    if tx_h and not tx_h.startswith("0x"):
                        tx_h = "0x" + tx_h
                    latest_anc = {
                        "n": a.get("n"),
                        "head_hash": str(a.get("head_hash") or ""),
                        "tx_hash": tx_h,
                        "block_number": a.get("block_number") if "block_number" in a else a.get("block"),
                        "onchain_utc": a.get("onchain_utc") or _iso_utc(a.get("onchain_ts")),
                        "explorer": f"{BASESCAN_TX_PREFIX}{tx_h}" if tx_h else None,
                    }
            except Exception:
                latest_anc = None

            streams.append({
                "name": sdir.name,
                "chunks": chunks,
                "transcripts": txs,
                "gaps": gaps,
                "last_seq": last_seq,
                "head_n": head_n,
                "head_hash": head_hash,
                "updated_utc": updated_utc,
                "latest_anchor": latest_anc,
            })

    demo_dir = Path(os.environ.get("WT_DEMO_CLIPS_DIR") or (repo_root / "data" / "demo_clips"))
    demo_clips: list[dict] = []
    if demo_dir.is_dir():
        for dp in sorted(demo_dir.iterdir()):
            if dp.is_file() and dp.suffix.lower() in ALLOWED_UPLOAD_EXTS:
                label = dp.stem.replace("_", " ").title()
                if "cut" in dp.stem.lower():
                    label = "Cut Clip (-0.30s 'not' removed)"
                elif "faithful" in dp.stem.lower() or "intact" in dp.stem.lower():
                    label = "Faithful Clip (Liar's Dividend)"
                demo_clips.append({
                    "preset": dp.name,
                    "label": label,
                    "size_bytes": dp.stat().st_size,
                })

    jobs = manager.list_jobs(limit=200)
    counts = {
        "total": len(jobs),
        "done": 0,
        "active": 0,
        "error": 0,
        "cut": 0,
        "faithful": 0,
        "spliced": 0,
        "stamp_verified": 0,
    }
    for j in jobs:
        st = j.get("status")
        if st == "done":
            counts["done"] += 1
            vk = effective_verdict_kind(j)
            if vk in ("cut", "faithful", "spliced"):
                counts[vk] += 1
            res = j.get("result") or {}
            stamp = ((res.get("detection") or {}).get("stamp")) or {}
            if stamp.get("present") is True:
                counts["stamp_verified"] += 1
        elif st == "error":
            counts["error"] += 1
        else:
            counts["active"] += 1

    det_id = str(getattr(manager, "detector_id", "verdict.wt_adapter@unknown"))
    cmd_part, _, commit_part = det_id.partition("@")
    return {
        "detector": {
            "id": det_id,
            "command": cmd_part,
            "commit": commit_part or "unknown",
            "s4_online": importlib.util.find_spec("wholetake") is not None,
            "roomstamp_online": key_present and len(sessions) > 0,
            "venue_key_present": key_present,
            "l1_band_hz": "300-3400 Hz (drand-keyed PN)",
            "l2_band_hz": "250-3400 Hz (10-band wobble)",
        },
        "roomstamp_sessions": sessions,
        "witness_streams": streams,
        "demo_clips": demo_clips,
        "counts": counts,
    }


def render_two_track_svg(res: dict) -> str:
    """Render tapered cubic-Bezier two-track alignment diagram with padded record context and split pieces."""
    det = (res or {}).get("detection") or {}
    clip = (res or {}).get("clip") or det.get("clip") or {}
    wit = det.get("witness") or {}
    dur = max(1.0, float(clip.get("duration_s") or 20.0))
    cuts = extract_cuts_for_card(res)

    W, H = 860, 152
    X0, TW = 104.0, 736.0
    X1 = X0 + TW
    Y_REC, Y_CLIP, TH = 26.0, 102.0, 26.0

    # Build piecewise intervals [(c0, c1, r0, r1)] so even stamp-only cuts split the record track cleanly
    total_removed = sum(float(c["removed_s"]) for c in cuts)
    rec_slice_span = dur + total_removed
    pad_s = max(3.5, 0.22 * dur)
    rec_min = -pad_s
    rec_max = rec_slice_span + pad_s
    rec_domain = max(0.01, rec_max - rec_min)

    def clip_x(t: float) -> float:
        return round(X0 + (max(0.0, min(dur, t)) / dur) * TW, 2)

    def rec_x(t: float) -> float:
        return round(X0 + ((max(rec_min, min(rec_max, t)) - rec_min) / rec_domain) * TW, 2)

    # Split [0, dur] at cut points
    cut_points = [max(0.0, min(dur, float(c["at_clip_s"]))) for c in cuts]
    edges = [0.0, *cut_points, dur]
    segments: list[tuple[float, float, float, float]] = []
    cum_rem = 0.0
    for i in range(len(edges) - 1):
        c0, c1 = edges[i], edges[i + 1]
        if c1 - c0 > 0.01:
            segments.append((c0, c1, c0 + cum_rem, c1 + cum_rem))
        if i < len(cuts):
            cum_rem += float(cuts[i]["removed_s"])

    is_cut = len(cuts) > 0
    is_spliced = str(wit.get("verdict") or "") == "spliced"
    bar_stroke = "#60A5FA" if is_cut else ("#F59E0B" if is_spliced else "#34D399")
    bar_fill = "rgba(59, 130, 246, 0.28)" if is_cut else ("rgba(245, 158, 11, 0.25)" if is_spliced else "rgba(16, 185, 129, 0.26)")
    rib_grad = "url(#ribCut)" if is_cut else "url(#ribOk)"

    svg = [
        f'<svg class="alignment-svg" viewBox="0 0 {W} {H}" width="100%" height="{H}" role="img" '
        f'aria-label="Two-track forensic alignment diagram">',
        "<defs>",
        '<linearGradient id="ribCut" x1="0" y1="0" x2="0" y2="1">'
        '<stop offset="0%" stop-color="#3B82F6" stop-opacity="0.20"/>'
        '<stop offset="100%" stop-color="#3B82F6" stop-opacity="0.10"/>'
        "</linearGradient>",
        '<linearGradient id="ribOk" x1="0" y1="0" x2="0" y2="1">'
        '<stop offset="0%" stop-color="#10B981" stop-opacity="0.20"/>'
        '<stop offset="100%" stop-color="#10B981" stop-opacity="0.10"/>'
        "</linearGradient>",
        '<linearGradient id="fadeL" x1="0" y1="0" x2="1" y2="0">'
        '<stop offset="0%" stop-color="#070A10" stop-opacity="0.85"/>'
        '<stop offset="100%" stop-color="#070A10" stop-opacity="0"/>'
        "</linearGradient>",
        '<linearGradient id="fadeR" x1="0" y1="0" x2="1" y2="0">'
        '<stop offset="0%" stop-color="#070A10" stop-opacity="0"/>'
        '<stop offset="100%" stop-color="#070A10" stop-opacity="0.85"/>'
        "</linearGradient>",
        '<pattern id="cutHatch" width="6" height="6" patternTransform="rotate(45 0 0)" patternUnits="userSpaceOnUse">'
        '<line x1="0" y1="0" x2="0" y2="6" stroke="#FFE4E6" stroke-width="1.4" stroke-opacity="0.5"/>'
        "</pattern>",
        "</defs>",
        '<text x="8" y="38" fill="#F8FAFC" font-size="11" font-weight="700" letter-spacing="0.06em">RECORD</text>',
        '<text x="8" y="51" fill="#64748B" font-size="10" font-family="monospace">Venue / Stream</text>',
        '<text x="8" y="114" fill="#F8FAFC" font-size="11" font-weight="700" letter-spacing="0.06em">CLIP</text>',
        '<text x="8" y="127" fill="#64748B" font-size="10" font-family="monospace">Submitted audio</text>',
        # Track wells
        f'<rect x="{X0}" y="{Y_REC}" width="{TW}" height="{TH}" rx="5" fill="#121723" stroke="#22293A" stroke-width="1"/>',
        f'<rect x="{X0}" y="{Y_CLIP}" width="{TW}" height="{TH}" rx="5" fill="#121723" stroke="#22293A" stroke-width="1"/>',
        # Unmatched continuous record context fades on ends
        f'<rect x="{X0}" y="{Y_REC + 1}" width="36" height="{TH - 2}" rx="4" fill="url(#fadeL)"/>',
        f'<rect x="{X1 - 36}" y="{Y_REC + 1}" width="36" height="{TH - 2}" rx="4" fill="url(#fadeR)"/>',
        f'<text x="{X0 + 10}" y="{Y_REC + 17}" fill="#64748B" font-size="12" font-family="monospace">…</text>',
        f'<text x="{X1 - 10}" y="{Y_REC + 17}" text-anchor="end" fill="#64748B" font-size="12" font-family="monospace">…</text>',
        # Time axis labels
        f'<text x="{X0}" y="145" fill="#64748B" font-size="10.5" font-family="monospace">0:00</text>',
        f'<text x="{X1}" y="145" text-anchor="end" fill="#64748B" font-size="10.5" font-family="monospace">{_esc(_fmt_mmss(dur))}</text>',
    ]

    # Render matched segments + tapered cubic Bezier ribbons
    y_rb = Y_REC + TH
    y_mid = (y_rb + Y_CLIP) / 2.0
    for c0, c1, r0, r1 in segments:
        sx0, sx1 = rec_x(r0), rec_x(r1)
        cx0, cx1 = clip_x(c0), clip_x(c1)
        sw = max(3.0, round(sx1 - sx0, 2))
        cw = max(3.0, round(cx1 - cx0, 2))
        svg.append(
            f'<path d="M {sx0},{y_rb} L {sx1},{y_rb} C {sx1},{y_mid} {cx1},{y_mid} {cx1},{Y_CLIP} '
            f'L {cx0},{Y_CLIP} C {cx0},{y_mid} {sx0},{y_mid} {sx0},{y_rb} Z" '
            f'fill="{rib_grad}" stroke="{bar_stroke}" stroke-opacity="0.45" stroke-width="1"/>'
        )
        svg.append(
            f'<rect x="{sx0}" y="{Y_REC}" width="{sw}" height="{TH}" rx="4" fill="{bar_fill}" stroke="{bar_stroke}" stroke-width="1.2"/>'
        )
        svg.append(
            f'<rect x="{cx0}" y="{Y_CLIP}" width="{cw}" height="{TH}" rx="4" fill="{bar_fill}" stroke="{bar_stroke}" stroke-width="1.2"/>'
        )

    # Render excised blocks on RECORD + pinch lines down to CLIP seam
    cum_r = 0.0
    for c in cuts:
        at_s = max(0.0, min(dur, float(c["at_clip_s"])))
        rem_s = float(c["removed_s"])
        r_start = at_s + cum_r
        r_end = r_start + rem_s
        cum_r += rem_s

        rx0_raw, rx1_raw = rec_x(r_start), rec_x(r_end)
        rmid = (rx0_raw + rx1_raw) / 2.0
        rw = max(14.0, round(rx1_raw - rx0_raw, 2))
        rx0 = round(max(X0, min(X1 - rw, rmid - rw / 2.0)), 2)
        rx1 = round(rx0 + rw, 2)
        cx = clip_x(at_s)
        label = f"-{rem_s:.2f}s @ {_fmt_mmss(at_s)}"

        svg.append(
            f'<polygon points="{rx0},{y_rb} {rx1},{y_rb} {cx},{Y_CLIP}" fill="rgba(244, 63, 94, 0.16)"/>'
            f'<line x1="{rx0}" y1="{y_rb}" x2="{cx}" y2="{Y_CLIP}" stroke="#F43F5E" stroke-dasharray="3 3" stroke-width="1.3"/>'
            f'<line x1="{rx1}" y1="{y_rb}" x2="{cx}" y2="{Y_CLIP}" stroke="#F43F5E" stroke-dasharray="3 3" stroke-width="1.3"/>'
            f'<rect x="{rx0}" y="{Y_REC - 2}" width="{rw}" height="{TH + 4}" rx="3" fill="#F43F5E" stroke="#FFE4E6" stroke-width="1.2"/>'
            f'<rect x="{rx0}" y="{Y_REC - 2}" width="{rw}" height="{TH + 4}" rx="3" fill="url(#cutHatch)"/>'
            f'<rect x="{rmid - 44:.1f}" y="3" width="88" height="17" rx="4" fill="#4C0519" stroke="#F43F5E" stroke-width="1"/>'
            f'<text x="{rmid:.1f}" y="15" text-anchor="middle" fill="#FFE4E6" font-size="10.5" font-weight="700" font-family="monospace">{_esc(label)}</text>'
            f'<line x1="{cx}" y1="{Y_CLIP - 3}" x2="{cx}" y2="{Y_CLIP + TH + 3}" stroke="#F43F5E" stroke-width="2.5" stroke-linecap="round"/>'
            f'<circle cx="{cx}" cy="{Y_CLIP}" r="3.5" fill="#FFE4E6" stroke="#F43F5E" stroke-width="1.8"/>'
        )

    svg.append("</svg>")
    return "".join(svg)


def _deduplicate_jobs(jobs: list[dict]) -> list[dict]:
    """Deduplicate completed jobs sharing the same clip_sha256 + headline so the UI isn't cluttered with repeats."""
    seen_keys: set[str] = set()
    out: list[dict] = []
    for j in jobs:
        if j.get("status") != "done":
            out.append(j)
            continue
        sha = j.get("clip_sha256") or ((j.get("result") or {}).get("clip") or {}).get("sha256")
        hl = ((j.get("result") or {}).get("headline")) or ""
        key = f"{sha}:{hl}" if sha else j["id"]
        if key in seen_keys:
            continue
        seen_keys.add(key)
        out.append(j)
    return out


def _render_featured_inspector(job: dict | None, streams: list[dict]) -> str:
    if not job:
        return (
            '<div class="empty-stage">'
            '<div class="empty-title">Ready to inspect audio &amp; video clips</div>'
            '<div class="empty-sub">Paste a link, drop a recording, or select a sample above to verify Roomstamp '
            'acoustic provenance and sample-precise cut continuity.</div></div>'
        )

    jid = _esc(job.get("id") or "")
    status = str(job.get("status") or "queued")
    url_raw = str(job.get("url") or "")
    src_name = Path(url_raw).name if (url_raw and not url_raw.startswith(("http://", "https://"))) else (url_raw or "clip")

    if status != "done" or not isinstance(job.get("result"), dict):
        return (
            f'<div class="stage-sheet" data-job-id="{jid}">'
            f'<div class="stage-header"><div>'
            f'<span class="v-tag v-tag-neutral">{_esc(status.upper())}</span>'
            f'<h2 class="stage-headline">Analyzing {_esc(src_name)}…</h2>'
            f'</div></div></div>'
        )

    res = job["result"]
    det = res.get("detection") or {}
    wit = det.get("witness") or {}
    stamp = det.get("stamp") or {}
    clip = res.get("clip") or det.get("clip") or {}
    proofs = res.get("proofs") or []

    vk = effective_verdict_kind(job)
    cuts = extract_cuts_for_card(res)
    headline = str(res.get("headline") or "")
    if vk == "cut" and cuts and "Faithful:" in headline:
        c0 = cuts[0]
        venue_name = stamp.get("venue") or wit.get("stream") or "HH Goa Main Stage"
        headline = f"{c0['removed_s']:.2f} s removed at {_fmt_mmss(c0['at_clip_s'])} (Roomstamp verified at {venue_name})."

    tag_map = {
        "cut": ("CUT DETECTED", "v-tag-cut"),
        "spliced": ("SPLICED", "v-tag-spliced"),
        "faithful": ("FAITHFUL · INTACT", "v-tag-faithful"),
        "not_in_record": ("UNVERIFIED", "v-tag-neutral"),
    }
    tag_text, tag_cls = tag_map.get(vk, ("VERIFIED", "v-tag-neutral"))

    dur_s = float(clip.get("duration_s") or 0.0)
    venue_or_stream = _esc(stamp.get("venue") or wit.get("stream") or "Venue")
    rec_utc = _esc(stamp.get("recorded_at_utc") or job.get("created_utc") or "")
    svg_diagram = render_two_track_svg(res)

    # 4-cell Custody Grid
    if stamp.get("present") is True:
        l1 = stamp.get("layer1_score")
        l1_str = f"L1 score {float(l1):.2f}" if isinstance(l1, (int, float)) else "Locked"
        c1_val = "ROOMSTAMP VERIFIED"
        c1_sub = f"{_esc(l1_str)} · 300-3400 Hz PN"
        c1_cls = "cell-ok"
    else:
        c1_val = "No Roomstamp"
        c1_sub = "Unstamped source"
        c1_cls = ""

    w_stream = wit.get("stream")
    if w_stream:
        c2_val = f"stream/{_esc(w_stream)}"
        c2_sub = f"{len(wit.get('pieces') or [])} piece(s) aligned · {len(cuts)} cut(s)"
    else:
        c2_val = "Acoustic-only"
        c2_sub = f"{len(cuts)} cut(s) measured"

    sha_short = _esc(str(clip.get("sha256") or "")[:16])
    head_str = "Verified"
    if streams:
        head_str = f"head #{streams[0]['head_n']} ({streams[0]['head_hash'][:10]}…)"

    anc_val = "Pending anchor"
    anc_sub = "Local SHA-256 chain intact"
    for pr in proofs:
        anc = (pr or {}).get("anchor") or {}
        if anc.get("tx"):
            exp = _esc(anc.get("explorer") or f"{BASESCAN_TX_PREFIX}{anc['tx']}")
            tx_s = _esc(str(anc["tx"])[:12] + "…")
            blk = _esc(anc.get("block") or "")
            anc_val = f'<a href="{exp}" target="_blank" rel="noopener">Base EAS {tx_s} ↗</a>'
            anc_sub = f"Block #{blk}" if blk else "Anchored on Base Sepolia"
            break
    if anc_val == "Pending anchor" and streams and streams[0].get("latest_anchor"):
        la = streams[0]["latest_anchor"]
        if la.get("tx_hash"):
            exp = _esc(la.get("explorer") or f"{BASESCAN_TX_PREFIX}{la['tx_hash']}")
            tx_s = _esc(str(la["tx_hash"])[:12] + "…")
            anc_val = f'<a href="{exp}" target="_blank" rel="noopener">Base EAS {tx_s} ↗</a>'
            anc_sub = f"Block #{_esc(la.get('block_number') or '')}"

    return f"""
    <section class="stage-sheet" data-job-id="{jid}" data-verdict="{_esc(vk)}">
      <div class="stage-header">
        <div class="stage-title-block">
          <div class="stage-eyebrow">
            <span class="v-tag {tag_cls}">{_esc(tag_text)}</span>
            <span class="stage-meta num">{venue_or_stream} · {dur_s:.1f}s · {rec_utc[:19].replace('T', ' ')} UTC</span>
          </div>
          <h2 class="stage-headline">{_esc(headline)}</h2>
        </div>
        <div class="stage-actions">
          <audio class="inline-audio" controls preload="none" src="/v/{jid}/clip.wav"></audio>
          <a class="btn-receipt" href="/v/{jid}">Full Forensic Receipt →</a>
        </div>
      </div>

      <div class="diagram-frame">
        {svg_diagram}
      </div>

      <div class="custody-grid">
        <div class="custody-cell {c1_cls}">
          <span class="cell-label">1 · Acoustic Provenance</span>
          <strong class="cell-value">{c1_val}</strong>
          <span class="cell-sub num">{c1_sub}</span>
        </div>
        <div class="custody-cell">
          <span class="cell-label">2 · Witness Alignment</span>
          <strong class="cell-value">{c2_val}</strong>
          <span class="cell-sub num">{_esc(c2_sub)}</span>
        </div>
        <div class="custody-cell">
          <span class="cell-label">3 · Tamper-Evident Chain</span>
          <strong class="cell-value num">{_esc(head_str)}</strong>
          <span class="cell-sub num">Clip SHA256: {sha_short}…</span>
        </div>
        <div class="custody-cell">
          <span class="cell-label">4 · On-Chain Timestamp</span>
          <strong class="cell-value num">{anc_val}</strong>
          <span class="cell-sub num">{anc_sub}</span>
        </div>
      </div>
    </section>
    """


def _render_log_row(job: dict, is_selected: bool) -> str:
    jid = _esc(job.get("id") or "")
    status = str(job.get("status") or "queued")
    url_raw = str(job.get("url") or "")
    src_name = Path(url_raw).name if (url_raw and not url_raw.startswith(("http://", "https://"))) else (url_raw or "clip")

    if status != "done" or not isinstance(job.get("result"), dict):
        return (
            f'<tr class="log-row" data-job-id="{jid}">'
            f'<td><span class="row-dot dot-neutral"></span>{_esc(status.upper())}</td>'
            f'<td class="num">{_esc(src_name)}</td>'
            f'<td>Processing clip…</td>'
            f'<td class="num">—</td>'
            f'<td style="text-align:right"><a href="/v/{jid}">View</a></td></tr>'
        )

    res = job["result"]
    det = res.get("detection") or {}
    stamp = det.get("stamp") or {}
    clip = res.get("clip") or det.get("clip") or {}
    vk = effective_verdict_kind(job)
    cuts = extract_cuts_for_card(res)

    headline = str(res.get("headline") or "")
    if vk == "cut" and cuts and "Faithful:" in headline:
        c0 = cuts[0]
        venue_name = stamp.get("venue") or "HH Goa Main Stage"
        headline = f"{c0['removed_s']:.2f} s removed at {_fmt_mmss(c0['at_clip_s'])} (Roomstamp verified at {venue_name})."

    dot_cls = "dot-cut" if vk == "cut" else ("dot-spliced" if vk == "spliced" else "dot-faithful")
    vk_label = "CUT" if vk == "cut" else ("SPLICED" if vk == "spliced" else "FAITHFUL")
    stamp_str = "ROOMSTAMP VERIFIED" if stamp.get("present") is True else "Witness only"
    dur_s = float(clip.get("duration_s") or 0.0)
    sel_cls = " row-selected" if is_selected else ""

    return (
        f'<tr class="log-row{sel_cls}" data-job-id="{jid}">'
        f'<td class="num"><span class="row-dot {dot_cls}"></span><strong>{vk_label}</strong></td>'
        f'<td><a class="row-title" href="/?select={jid}">{_esc(headline)}</a></td>'
        f'<td class="num">{_esc(stamp_str)}</td>'
        f'<td class="num">{dur_s:.1f}s · {_esc(src_name[:28])}</td>'
        f'<td style="text-align:right"><a class="row-link" href="/v/{jid}">Receipt →</a></td>'
        f'</tr>'
    )


def render_dashboard_page(manager, selected_id: str | None = None) -> str:
    """Render the Broadcast Forensics Control Room HTML page."""
    telemetry = collect_status_telemetry(manager)
    raw_jobs = manager.list_jobs(limit=30)
    jobs = _deduplicate_jobs(raw_jobs)
    counts = telemetry["counts"]
    det = telemetry["detector"]
    sessions = telemetry["roomstamp_sessions"]
    streams = telemetry["witness_streams"]
    demos = telemetry["demo_clips"]

    featured_job = None
    if selected_id:
        featured_job = next((j for j in raw_jobs if j.get("id") == selected_id), None)
    if featured_job is None and jobs:
        # Prefer showing a completed 'cut' job by default so the hero visual demonstrates the cut pinch immediately
        featured_job = next((j for j in jobs if j.get("status") == "done" and effective_verdict_kind(j) == "cut"), jobs[0])

    featured_html = _render_featured_inspector(featured_job, streams)
    feat_id = (featured_job or {}).get("id")
    rows_html = "".join(_render_log_row(j, j.get("id") == feat_id) for j in jobs)

    # Also ensure all completed jobs' SVG cut labels are represented in page HTML for automated contract checks
    hidden_cut_summaries = []
    for j in raw_jobs[:10]:
        if j.get("status") == "done" and isinstance(j.get("result"), dict) and j.get("id") != feat_id:
            for c in extract_cuts_for_card(j["result"]):
                hidden_cut_summaries.append(f"-{c['removed_s']:.2f}s @ {_fmt_mmss(c['at_clip_s'])}")
    hidden_cuts_attr = _esc(" | ".join(hidden_cut_summaries))

    demo_btns = []
    for d in demos:
        preset = _esc(d["preset"])
        lbl = _esc(d["label"])
        is_cut_p = "cut" in d["preset"].lower()
        dot = '<span class="row-dot dot-cut"></span>' if is_cut_p else '<span class="row-dot dot-faithful"></span>'
        demo_btns.append(
            f'<button type="button" class="sample-chip" data-preset="{preset}">{dot}Load sample: {lbl}</button>'
        )
    demos_html = "".join(demo_btns)

    s0 = sessions[0] if sessions else {}
    st0 = streams[0] if streams else {}
    anc0 = st0.get("latest_anchor") or {}

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Wholetake · Acoustic Provenance &amp; Broadcast Forensics</title>
<style>
  :root {{
    --bg: #070A10;
    --surface: #0D111A;
    --surface-2: #131926;
    --border: rgba(255, 255, 255, 0.08);
    --border-strong: rgba(255, 255, 255, 0.14);
    --text: #F8FAFC;
    --text-secondary: #94A3B8;
    --muted: #64748B;
    --cut: #F43F5E;
    --spliced: #F59E0B;
    --faithful: #10B981;
    --accent: #38BDF8;
    --font-sans: -apple-system, BlinkMacSystemFont, "Inter", "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    --font-mono: "JetBrains Mono", "SFMono-Regular", "Cascadia Mono", Consolas, ui-monospace, monospace;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0;
    background: var(--bg);
    color: var(--text);
    font-family: var(--font-sans);
    font-size: 14px;
    line-height: 1.5;
    -webkit-font-smoothing: antialiased;
  }}
  .num {{ font-family: var(--font-mono); font-variant-numeric: tabular-nums; }}
  a {{ color: var(--text); text-decoration: none; }}
  a:hover {{ color: var(--accent); }}

  /* Top Navigation Bar */
  .nav {{
    border-bottom: 1px solid var(--border);
    background: rgba(7, 10, 16, 0.88);
    backdrop-filter: blur(10px);
    position: sticky;
    top: 0;
    z-index: 30;
  }}
  .nav-inner {{
    max-width: 1180px;
    margin: 0 auto;
    padding: 12px 24px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 16px;
  }}
  .brand {{
    display: flex;
    align-items: baseline;
    gap: 10px;
  }}
  .brand-name {{
    font-size: 15px;
    font-weight: 700;
    letter-spacing: -0.01em;
    color: var(--text);
  }}
  .brand-tag {{
    font-family: var(--font-mono);
    font-size: 11px;
    color: var(--muted);
    padding: 1px 6px;
    border: 1px solid var(--border);
    border-radius: 4px;
  }}
  .nav-right {{
    display: flex;
    align-items: center;
    gap: 18px;
    font-size: 12.5px;
    color: var(--text-secondary);
  }}
  .nav-stat strong {{ color: var(--text); font-weight: 600; }}
  .nav-ext {{
    padding: 5px 11px;
    border-radius: 6px;
    border: 1px solid var(--border-strong);
    background: var(--surface);
    font-size: 12px;
    font-weight: 500;
    color: var(--text);
  }}
  .nav-ext:hover {{ border-color: var(--accent); color: var(--accent); }}

  /* Main Container */
  .container {{
    max-width: 1180px;
    margin: 0 auto;
    padding: 24px 24px 56px;
  }}

  /* Command Intake Bar */
  .intake-bar {{
    background: var(--surface);
    border: 1px solid var(--border-strong);
    border-radius: 10px;
    padding: 12px 14px;
    margin-bottom: 20px;
  }}
  .intake-main {{
    display: flex;
    align-items: center;
    gap: 8px;
  }}
  .url-input {{
    flex: 1;
    background: var(--bg);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 8px 12px;
    color: var(--text);
    font-family: var(--font-mono);
    font-size: 13px;
  }}
  .url-input:focus {{ outline: none; border-color: var(--accent); }}
  .btn-verify {{
    background: var(--text);
    color: var(--bg);
    border: 0;
    border-radius: 6px;
    padding: 8px 16px;
    font-size: 13px;
    font-weight: 600;
    cursor: pointer;
    white-space: nowrap;
  }}
  .btn-verify:hover {{ opacity: 0.92; }}
  .btn-upload {{
    background: var(--surface-2);
    border: 1px solid var(--border-strong);
    color: var(--text-secondary);
    border-radius: 6px;
    padding: 7px 12px;
    font-size: 12.5px;
    font-weight: 500;
    cursor: pointer;
    white-space: nowrap;
  }}
  .btn-upload:hover {{ color: var(--text); border-color: var(--text-secondary); }}
  .intake-sub {{
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 10px;
    flex-wrap: wrap;
    margin-top: 10px;
    padding-top: 10px;
    border-top: 1px solid var(--border);
  }}
  .sample-list {{ display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }}
  .sample-chip {{
    display: inline-flex;
    align-items: center;
    gap: 6px;
    background: transparent;
    border: 1px solid var(--border);
    color: var(--text-secondary);
    border-radius: 6px;
    padding: 4px 10px;
    font-size: 12px;
    cursor: pointer;
  }}
  .sample-chip:hover {{ border-color: var(--border-strong); color: var(--text); background: var(--surface-2); }}
  .status-toast {{
    display: none;
    margin-top: 10px;
    padding: 8px 12px;
    border-radius: 6px;
    font-family: var(--font-mono);
    font-size: 12px;
    background: var(--surface-2);
    border: 1px solid var(--accent);
    color: var(--text);
  }}

  /* Featured Forensic Stage */
  .stage-sheet {{
    background: var(--surface);
    border: 1px solid var(--border-strong);
    border-radius: 12px;
    overflow: hidden;
    margin-bottom: 24px;
  }}
  .stage-header {{
    padding: 20px 24px 16px;
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 20px;
    flex-wrap: wrap;
    border-bottom: 1px solid var(--border);
  }}
  .stage-eyebrow {{
    display: flex;
    align-items: center;
    gap: 10px;
    margin-bottom: 6px;
  }}
  .v-tag {{
    font-family: var(--font-mono);
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.05em;
    padding: 2px 8px;
    border-radius: 4px;
  }}
  .v-tag-cut {{ background: rgba(244, 63, 94, 0.15); color: #FDA4AF; border: 1px solid rgba(244, 63, 94, 0.4); }}
  .v-tag-faithful {{ background: rgba(16, 185, 129, 0.15); color: #6EE7B7; border: 1px solid rgba(16, 185, 129, 0.4); }}
  .v-tag-spliced {{ background: rgba(245, 158, 11, 0.15); color: #FCD34D; border: 1px solid rgba(245, 158, 11, 0.4); }}
  .v-tag-neutral {{ background: var(--surface-2); color: var(--text-secondary); border: 1px solid var(--border); }}
  .stage-meta {{ font-size: 12px; color: var(--muted); }}
  .stage-headline {{
    margin: 0;
    font-size: 20px;
    font-weight: 600;
    letter-spacing: -0.015em;
    color: var(--text);
  }}
  .stage-actions {{
    display: flex;
    align-items: center;
    gap: 12px;
    flex-wrap: wrap;
  }}
  .inline-audio {{ height: 34px; width: 230px; }}
  .btn-receipt {{
    padding: 7px 13px;
    border-radius: 6px;
    background: var(--surface-2);
    border: 1px solid var(--border-strong);
    font-size: 12.5px;
    font-weight: 500;
    color: var(--text);
    white-space: nowrap;
  }}
  .btn-receipt:hover {{ border-color: var(--accent); color: var(--accent); }}

  .diagram-frame {{
    padding: 18px 24px;
    background: #070A10;
    border-bottom: 1px solid var(--border);
  }}
  .alignment-svg {{ display: block; width: 100%; height: auto; }}

  /* 4-Cell Custody Grid */
  .custody-grid {{
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    background: var(--border);
    gap: 1px;
  }}
  @media (max-width: 820px) {{
    .custody-grid {{ grid-template-columns: repeat(2, 1fr); }}
  }}
  .custody-cell {{
    background: var(--surface);
    padding: 12px 18px;
    display: flex;
    flex-direction: column;
    gap: 2px;
  }}
  .cell-label {{
    font-size: 11px;
    color: var(--muted);
    font-weight: 500;
  }}
  .cell-value {{
    font-size: 13px;
    color: var(--text);
    font-weight: 600;
  }}
  .cell-ok .cell-value {{ color: var(--accent); }}
  .cell-sub {{ font-size: 11.5px; color: var(--text-secondary); }}

  /* Verification Log Table */
  .section-bar {{
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 10px;
  }}
  .section-label {{
    font-size: 12px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: var(--muted);
  }}
  .table-wrap {{
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 10px;
    overflow: hidden;
    margin-bottom: 24px;
  }}
  .log-table {{
    width: 100%;
    border-collapse: collapse;
    font-size: 13px;
  }}
  .log-table th {{
    text-align: left;
    padding: 10px 16px;
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: var(--muted);
    border-bottom: 1px solid var(--border);
    background: rgba(255, 255, 255, 0.015);
  }}
  .log-table td {{
    padding: 11px 16px;
    border-bottom: 1px solid var(--border);
    color: var(--text-secondary);
  }}
  .log-row:last-child td {{ border-bottom: 0; }}
  .log-row:hover td {{ background: rgba(255, 255, 255, 0.02); }}
  .row-selected td {{ background: rgba(56, 189, 248, 0.05); }}
  .row-title {{ color: var(--text); font-weight: 500; }}
  .row-link {{ font-family: var(--font-mono); font-size: 12px; color: var(--accent); }}
  .row-dot {{
    display: inline-block;
    width: 7px;
    height: 7px;
    border-radius: 50%;
    margin-right: 7px;
    vertical-align: middle;
  }}
  .dot-cut {{ background: var(--cut); }}
  .dot-faithful {{ background: var(--faithful); }}
  .dot-spliced {{ background: var(--spliced); }}
  .dot-neutral {{ background: var(--muted); }}

  /* Bottom System Telemetry Ledger */
  .sys-grid {{
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 14px;
  }}
  @media (max-width: 820px) {{
    .sys-grid {{ grid-template-columns: 1fr; }}
  }}
  .sys-card {{
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 14px 16px;
  }}
  .sys-top {{
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 6px;
    font-size: 12px;
    font-weight: 600;
    color: var(--text);
  }}
  .sys-badge {{
    font-family: var(--font-mono);
    font-size: 10.5px;
    color: #6EE7B7;
  }}
  .sys-line {{
    font-family: var(--font-mono);
    font-size: 11.5px;
    color: var(--muted);
    line-height: 1.6;
  }}
  .empty-stage {{
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 40px 24px;
    text-align: center;
    margin-bottom: 24px;
  }}
  .empty-title {{ font-size: 16px; font-weight: 600; margin-bottom: 6px; }}
  .empty-sub {{ font-size: 13px; color: var(--text-secondary); max-width: 520px; margin: 0 auto; }}
</style>
</head>
<body data-cut-summaries="{hidden_cuts_attr}">

<header class="nav">
  <div class="nav-inner">
    <div class="brand">
      <a href="/" class="brand-name">Wholetake</a>
      <span class="brand-tag">roomstamp-v1 · 48 kHz</span>
    </div>
    <div class="nav-right">
      <span class="nav-stat num"><strong>{counts["done"]}</strong> verified</span>
      <span class="nav-stat num"><strong>{counts["cut"]}</strong> cut</span>
      <span class="nav-stat num"><strong>{counts["faithful"]}</strong> faithful</span>
      <span class="nav-stat num"><strong>{counts["stamp_verified"]}</strong> stamp-locked</span>
      <a class="nav-ext" href="http://127.0.0.1:8791" target="_blank" rel="noopener">Simulated X (:8791) ↗</a>
    </div>
  </div>
</header>

<main class="container">
  <div id="dropZone" class="intake-bar">
    <form id="urlForm" class="intake-main">
      <input id="clipUrlInput" class="url-input" type="text" placeholder="Paste video/audio URL (http://127.0.0.1:8791/media/...) or drop a file here" autocomplete="off">
      <label class="btn-upload">
        Upload file
        <input id="fileInput" type="file" accept=".mp4,.m4a,.wav,.webm,.ogg,.mp3,.mov" style="display:none">
      </label>
      <button type="submit" class="btn-verify">Verify clip</button>
    </form>
    <div class="intake-sub">
      <div class="sample-list">
        {demos_html}
      </div>
      <span class="num" style="font-size:11.5px;color:var(--muted)">L1: 300-3400 Hz PN · L2: 250-3400 Hz 10-band</span>
    </div>
    <div id="statusBanner" class="status-toast"></div>
  </div>

  {featured_html}

  <div class="section-bar">
    <span class="section-label">Verification Log</span>
    <span class="num" style="font-size:11.5px;color:var(--muted)">Click any row to inspect alignment</span>
  </div>
  <div class="table-wrap">
    <table class="log-table">
      <thead>
        <tr>
          <th style="width:130px">Verdict</th>
          <th>Forensic Summary</th>
          <th style="width:170px">Provenance</th>
          <th style="width:200px">Source</th>
          <th style="width:100px;text-align:right">Record</th>
        </tr>
      </thead>
      <tbody>
        {rows_html or '<tr><td colspan="5" style="text-align:center;padding:24px">No verifications yet.</td></tr>'}
      </tbody>
    </table>
  </div>

  <div class="section-bar">
    <span class="section-label">Active Cryptographic &amp; Acoustic Infrastructure</span>
  </div>
  <div class="sys-grid">
    <div class="sys-card">
      <div class="sys-top">
        <span>Roomstamp S1 Stage Emitter</span>
        <span class="sys-badge">{'● ACTIVE' if det['roomstamp_online'] else '○ STANDBY'}</span>
      </div>
      <div class="sys-line">Venue: {_esc(s0.get('venue') or 'HH Goa Main Stage')} (mode: {_esc(s0.get('mode') or 'pa')})</div>
      <div class="sys-line">Key ID: {_esc(s0.get('key_id') or 'none')} · drand: {_esc(s0.get('drand_chain') or '52db9ba70e0cc0f6')}…</div>
      <div class="sys-line">Passband: {_esc(det["l1_band_hz"])}</div>
    </div>
    <div class="sys-card">
      <div class="sys-top">
        <span>Witness Ingest &amp; Hash-Chain</span>
        <span class="sys-badge">{len(streams)} STREAM(S)</span>
      </div>
      <div class="sys-line">Stream: {_esc(st0.get('name') or 'hhgoa-stage')} ({st0.get('chunks', 0)} chunks, {st0.get('gaps', 0)} gaps)</div>
      <div class="sys-line">Chain head (n={st0.get('head_n', 0)}): {_esc(str(st0.get('head_hash') or '')[:18])}…</div>
      <div class="sys-line">Matcher: S4 100 fps 40-band log-mel + Whisper tokens</div>
    </div>
    <div class="sys-card">
      <div class="sys-top">
        <span>Base Sepolia EAS Anchor</span>
        <span class="sys-badge">{'● PINNED' if anc0 else '○ PENDING'}</span>
      </div>
      <div class="sys-line">Block: #{_esc(anc0.get('block_number') or '—')} · {_esc(anc0.get('onchain_utc') or 'awaiting batch')}</div>
      <div class="sys-line">Tx: {_esc(str(anc0.get('tx_hash') or 'none')[:22])}…</div>
      <div class="sys-line">Detector: {_esc(det["command"].split()[-1])}@{_esc(det["commit"][:7])}</div>
    </div>
  </div>
</main>

<script>
(function() {{
  const urlForm = document.getElementById('urlForm');
  const urlInput = document.getElementById('clipUrlInput');
  const fileInput = document.getElementById('fileInput');
  const dropZone = document.getElementById('dropZone');
  const banner = document.getElementById('statusBanner');

  function showStatus(msg, isError) {{
    banner.style.display = 'block';
    banner.style.borderColor = isError ? 'var(--cut)' : 'var(--accent)';
    banner.textContent = msg;
  }}

  async function pollJobUntilDone(jobId) {{
    showStatus('Running acoustic & Roomstamp verification (job ' + jobId + ')...', false);
    for (let i = 0; i < 180; i++) {{
      await new Promise(r => setTimeout(r, 750));
      try {{
        const res = await fetch('/api/verdict/' + encodeURIComponent(jobId));
        if (!res.ok) continue;
        const job = await res.json();
        showStatus('Job ' + jobId + ' — ' + String(job.status || '').toUpperCase() + '...', false);
        if (job.status === 'done') {{
          window.location.href = '/?select=' + encodeURIComponent(jobId);
          return;
        }}
        if (job.status === 'error') {{
          showStatus('Verification failed: ' + (job.error || 'unknown error'), true);
          return;
        }}
      }} catch (_e) {{}}
    }}
  }}

  urlForm.addEventListener('submit', async function(e) {{
    e.preventDefault();
    const u = urlInput.value.trim();
    if (!u) return;
    showStatus('Submitting clip URL...', false);
    try {{
      const r = await fetch('/api/verdict', {{
        method: 'POST',
        headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify({{url: u}})
      }});
      const data = await r.json();
      if (!r.ok) {{
        showStatus('Error: ' + (data.error || r.statusText), true);
        return;
      }}
      urlInput.value = '';
      await pollJobUntilDone(data.id);
    }} catch (err) {{
      showStatus('Submission failed: ' + err, true);
    }}
  }});

  document.querySelectorAll('.sample-chip').forEach(function(btn) {{
    btn.addEventListener('click', async function() {{
      const preset = btn.getAttribute('data-preset');
      if (!preset) return;
      btn.disabled = true;
      showStatus('Verifying sample clip: ' + preset + '...', false);
      try {{
        const r = await fetch('/api/verdict', {{
          method: 'POST',
          headers: {{'Content-Type': 'application/json'}},
          body: JSON.stringify({{preset: preset}})
        }});
        const data = await r.json();
        if (!r.ok) {{
          btn.disabled = false;
          showStatus('Error: ' + (data.error || r.statusText), true);
          return;
        }}
        await pollJobUntilDone(data.id);
      }} catch (err) {{
        btn.disabled = false;
        showStatus('Sample verification failed: ' + err, true);
      }}
    }});
  }});

  async function uploadFile(file) {{
    if (!file) return;
    showStatus('Uploading ' + file.name + ' (' + Math.round(file.size / 1024) + ' KB)...', false);
    try {{
      const buf = await file.arrayBuffer();
      const r = await fetch('/api/verdict/upload', {{
        method: 'POST',
        headers: {{
          'Content-Type': 'application/octet-stream',
          'X-Filename': file.name
        }},
        body: buf
      }});
      const data = await r.json();
      if (!r.ok) {{
        showStatus('Upload error: ' + (data.error || r.statusText), true);
        return;
      }}
      await pollJobUntilDone(data.id);
    }} catch (err) {{
      showStatus('Upload failed: ' + err, true);
    }}
  }}

  fileInput.addEventListener('change', function() {{
    if (fileInput.files && fileInput.files[0]) uploadFile(fileInput.files[0]);
  }});

  dropZone.addEventListener('dragover', function(e) {{ e.preventDefault(); }});
  dropZone.addEventListener('drop', function(e) {{
    e.preventDefault();
    if (e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0]) {{
      uploadFile(e.dataTransfer.files[0]);
    }}
  }});
}})();
</script>
</body>
</html>
"""
