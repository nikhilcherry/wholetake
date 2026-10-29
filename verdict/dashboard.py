"""Self-contained Broadcast Forensics Control Room dashboard for Wholetake (`GET /` and `GET /dashboard`).

Zero external CDN/font requests. Renders server-side initial state (verdict jobs + live Roomstamp/Witness/Anchor
telemetry) with a live client-side polling loop, URL + drag-and-drop file upload intake, 1-click stage demo presets,
and inline SVG 2-track cut ribbons supporting both witness and Roomstamp L1/L2 cuts.
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
        # Merge if within 1.5s of an existing witness cut
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
    """Build truthful live system telemetry for `GET /api/status` and the dashboard Right Rail."""
    repo_root = Path(__file__).resolve().parents[1]
    venue_dir = Path(os.environ.get("WT_VENUE") or os.environ.get("WT_VENUE_DIR") or (repo_root / "logs"))
    key_file = Path(os.environ.get("WT_VENUE_KEY") or os.environ.get("WT_KEY_FILE") or (repo_root / "venue.key"))
    key_present = bool(os.environ.get("ROOMSTAMP_KEY")) or key_file.is_file()

    # 1. Roomstamp sessions
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

    # 2. Witness streams
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

    # 3. Demo clips (expose safe basenames only, never host filesystem paths)
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

    # 4. Counts across jobs
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
    """Render unified inline SVG 2-Track Cut Ribbon supporting both witness.removed and stamp.cuts."""
    det = (res or {}).get("detection") or {}
    clip = (res or {}).get("clip") or det.get("clip") or {}
    wit = det.get("witness") or {}
    stamp = det.get("stamp") or {}
    dur = max(1.0, float(clip.get("duration_s") or 20.0))
    cuts = extract_cuts_for_card(res)
    pieces = wit.get("pieces") or []

    W, H = 680, 112
    PAD_L, PAD_R = 88, 20
    TW = W - PAD_L - PAD_R
    Y_REC, Y_CLIP, TH = 22, 74, 16

    svg = [
        f'<svg class="cut-ribbon-svg" viewBox="0 0 {W} {H}" width="100%" height="{H}" role="img" '
        f'aria-label="Two-track forensic alignment ribbon">',
        f'<rect x="0" y="0" width="{W}" height="{H}" rx="8" fill="#0B101C" stroke="#1E293B" stroke-width="1"/>',
        f'<text x="12" y="{Y_REC + 12}" fill="#94A3B8" font-size="11" font-weight="700" font-family="monospace">RECORD</text>',
        f'<text x="12" y="{Y_CLIP + 12}" fill="#94A3B8" font-size="11" font-weight="700" font-family="monospace">CLIP</text>',
        f'<rect x="{PAD_L}" y="{Y_REC}" width="{TW}" height="{TH}" rx="4" fill="#1E293B"/>',
        f'<rect x="{PAD_L}" y="{Y_CLIP}" width="{TW}" height="{TH}" rx="4" fill="#1E293B"/>',
    ]

    vk = "cut" if cuts else (str(wit.get("verdict") or "faithful"))
    track_col = "#F43F5E" if vk == "cut" else ("#F59E0B" if vk == "spliced" else "#10B981")
    ribbon_fill = "rgba(16, 185, 129, 0.16)" if vk == "faithful" else "rgba(56, 189, 248, 0.14)"

    if pieces and not cuts:
        for p in pieces:
            cs = p.get("clip_s") or [0.0, dur]
            x0 = PAD_L + max(0.0, min(1.0, float(cs[0]) / dur)) * TW
            x1 = PAD_L + max(0.0, min(1.0, float(cs[1]) / dur)) * TW
            w = max(4.0, x1 - x0)
            svg.append(
                f'<path d="M {x0:.1f} {Y_REC + TH} L {x1:.1f} {Y_REC + TH} L {x1:.1f} {Y_CLIP} L {x0:.1f} {Y_CLIP} Z" '
                f'fill="{ribbon_fill}"/>'
            )
            svg.append(f'<rect x="{x0:.1f}" y="{Y_REC}" width="{w:.1f}" height="{TH}" rx="3" fill="{track_col}" opacity="0.85"/>')
            svg.append(f'<rect x="{x0:.1f}" y="{Y_CLIP}" width="{w:.1f}" height="{TH}" rx="3" fill="{track_col}"/>')
    else:
        svg.append(
            f'<path d="M {PAD_L} {Y_REC + TH} L {PAD_L + TW} {Y_REC + TH} L {PAD_L + TW} {Y_CLIP} L {PAD_L} {Y_CLIP} Z" '
            f'fill="{ribbon_fill}"/>'
        )
        svg.append(f'<rect x="{PAD_L}" y="{Y_REC}" width="{TW}" height="{TH}" rx="3" fill="#10B981" opacity="0.8"/>')
        svg.append(f'<rect x="{PAD_L}" y="{Y_CLIP}" width="{TW}" height="{TH}" rx="3" fill="#38BDF8" opacity="0.85"/>')

    # Overlay crimson excision wedges for every cut (from witness.removed OR stamp.cuts)
    for c in cuts:
        at_s = max(0.0, min(dur, float(c["at_clip_s"])))
        rem_s = float(c["removed_s"])
        cx = PAD_L + (at_s / dur) * TW
        rec_w = max(14.0, min(90.0, (rem_s / max(dur, 1.0)) * TW * 2.5))
        r0 = max(float(PAD_L), cx - rec_w / 2.0)
        r1 = min(float(PAD_L + TW), cx + rec_w / 2.0)
        # Excised block on RECORD track + V-wedge down to the seam on CLIP track
        svg.append(f'<rect x="{r0:.1f}" y="{Y_REC - 1}" width="{max(6.0, r1 - r0):.1f}" height="{TH + 2}" rx="2" fill="#F43F5E"/>')
        svg.append(
            f'<polygon points="{r0:.1f},{Y_REC + TH} {r1:.1f},{Y_REC + TH} {cx + 2.5:.1f},{Y_CLIP} {cx - 2.5:.1f},{Y_CLIP}" '
            f'fill="rgba(244, 63, 94, 0.38)" stroke="#F43F5E" stroke-width="1"/>'
        )
        svg.append(f'<rect x="{cx - 2.0:.1f}" y="{Y_CLIP - 2}" width="4" height="{TH + 4}" rx="1" fill="#F43F5E"/>')
        label = f"-{rem_s:.2f}s @ {_fmt_mmss(at_s)}"
        lx = max(PAD_L + 44, min(PAD_L + TW - 44, cx))
        svg.append(
            f'<rect x="{lx - 42:.1f}" y="{Y_REC + TH + 8}" width="84" height="18" rx="4" fill="#4C0519" stroke="#F43F5E" stroke-width="1"/>'
        )
        svg.append(
            f'<text x="{lx:.1f}" y="{Y_REC + TH + 20}" text-anchor="middle" fill="#FFE4E6" font-size="10.5" '
            f'font-weight="700" font-family="monospace">{_esc(label)}</text>'
        )

    if stamp.get("present") is True:
        svg.append(
            f'<text x="{PAD_L + TW}" y="14" text-anchor="end" fill="#38BDF8" font-size="10" font-weight="700" '
            f'font-family="monospace">ROOMSTAMP L1/L2 LOCKED</text>'
        )

    svg.append("</svg>")
    return "".join(svg)


def _render_verdict_card_html(job: dict) -> str:
    jid = _esc(job.get("id") or "")
    status = str(job.get("status") or "queued")
    url_raw = str(job.get("url") or "")
    # Never leak host paths if a local demo clip or upload was submitted
    display_src = Path(url_raw).name if (not url_raw.startswith(("http://", "https://")) and url_raw) else url_raw

    if status != "done" or not isinstance(job.get("result"), dict):
        if status == "error":
            err_msg = _esc(job.get("error") or "Detector error")
            return (
                f'<article class="verdict-card card-error" data-job-id="{jid}">'
                f'<div class="card-top"><span class="stamp-pill pill-cut">ERROR</span>'
                f'<span class="mono-meta">job:{jid}</span></div>'
                f'<div class="card-headline">{err_msg}</div>'
                f'<div class="mono-meta">Source: {_esc(display_src)}</div></article>'
            )
        return (
            f'<article class="verdict-card card-active" data-job-id="{jid}">'
            f'<div class="card-top"><span class="stamp-pill pill-active">{_esc(status.upper())}</span>'
            f'<span class="mono-meta">job:{jid} · {_esc(display_src)}</span></div>'
            f'<div class="card-headline">Running forensic audio + Roomstamp verification…</div></article>'
        )

    res = job["result"]
    det = res.get("detection") or {}
    wit = det.get("witness") or {}
    stamp = det.get("stamp") or {}
    clip = res.get("clip") or det.get("clip") or {}
    proofs = res.get("proofs") or []

    vk = effective_verdict_kind(job)
    cuts = extract_cuts_for_card(res)
    badge_map = {
        "cut": ("CUT", "pill-cut"),
        "spliced": ("SPLICED", "pill-spliced"),
        "faithful": ("FAITHFUL", "pill-faithful"),
        "not_in_record": ("NO RECORD", "pill-neutral"),
    }
    badge_text, badge_cls = badge_map.get(vk, ("VERDICT", "pill-neutral"))

    headline = str(res.get("headline") or "")
    if vk == "cut" and cuts and "Faithful:" in headline:
        c0 = cuts[0]
        venue_name = stamp.get("venue") or wit.get("stream") or "stage"
        headline = (
            f"{c0['removed_s']:.2f} s removed at {_fmt_mmss(c0['at_clip_s'])} "
            f"(Roomstamp L1 phase discontinuity verified at {venue_name})."
        )

    stamp_pill = ""
    if stamp.get("present") is True:
        l1 = stamp.get("layer1_score")
        l1_s = f" · L1:{float(l1):.2f}" if isinstance(l1, (int, float)) else ""
        venue_s = _esc(stamp.get("venue") or "Venue")
        stamp_pill = f'<span class="stamp-pill pill-roomstamp">ROOMSTAMP VERIFIED · {venue_s}{_esc(l1_s)}</span>'

    cut_pill = ""
    if cuts:
        c0 = cuts[0]
        words_s = f' ("{_esc(c0["words"])}")' if c0.get("words") else ""
        cut_pill = (
            f'<span class="mono-chip chip-cut">✂ -{c0["removed_s"]:.2f}s @ {_fmt_mmss(c0["at_clip_s"])}{words_s} '
            f'({len(cuts)} cut{"s" if len(cuts) != 1 else ""})</span>'
        )

    anchor_html = '<span class="mono-chip">Anchor: pending</span>'
    for pr in proofs:
        anc = (pr or {}).get("anchor") or {}
        if anc.get("tx"):
            tx_short = _esc(str(anc["tx"])[:12] + "…")
            exp_url = _esc(anc.get("explorer") or f"{BASESCAN_TX_PREFIX}{anc['tx']}")
            anchor_html = f'<a class="mono-chip chip-anchor" href="{exp_url}" target="_blank" rel="noopener">Base EAS {tx_short} ↗</a>'
            break

    dur_s = float(clip.get("duration_s") or 0.0)
    stream_s = _esc(wit.get("stream") or stamp.get("venue") or "unmatched")
    svg_ribbon = render_two_track_svg(res)

    return (
        f'<article class="verdict-card" data-job-id="{jid}" data-verdict="{_esc(vk)}">'
        f'<div class="card-top">'
        f'<div class="badge-group"><span class="stamp-pill {badge_cls}">{_esc(badge_text)}</span>{stamp_pill}{cut_pill}</div>'
        f'<span class="mono-meta num">{dur_s:.1f}s · {stream_s} · id:{jid[:10]}</span>'
        f'</div>'
        f'<h3 class="card-headline">{_esc(headline)}</h3>'
        f'{svg_ribbon}'
        f'<div class="card-footer">'
        f'<div class="telemetry-chips">{anchor_html}'
        f'<span class="mono-chip">SHA256: {_esc(str(clip.get("sha256") or "")[:12])}…</span></div>'
        f'<div class="card-actions">'
        f'<a class="btn-secondary" href="/v/{jid}/verdict.json" target="_blank">JSON</a>'
        f'<a class="btn-primary" href="/v/{jid}">Open Evidence Record →</a>'
        f'</div></div></article>'
    )


def render_dashboard_page(manager) -> str:
    """Render the complete self-contained Broadcast Forensics Control Room HTML page."""
    telemetry = collect_status_telemetry(manager)
    jobs = manager.list_jobs(limit=25)
    counts = telemetry["counts"]
    det = telemetry["detector"]
    sessions = telemetry["roomstamp_sessions"]
    streams = telemetry["witness_streams"]
    demos = telemetry["demo_clips"]

    cards_html = "".join(_render_verdict_card_html(j) for j in jobs)
    if not cards_html:
        cards_html = (
            '<div class="empty-state">No clips checked yet. Paste a clip URL, drop an MP4/WAV file, '
            'or click a Stage Demo Preset above to run the full acoustic + Roomstamp pipeline.</div>'
        )

    demo_btns = []
    for d in demos:
        preset = _esc(d["preset"])
        lbl = _esc(d["label"])
        cls = "preset-cut" if "cut" in d["preset"].lower() else "preset-faithful"
        demo_btns.append(
            f'<button type="button" class="preset-btn {cls}" data-preset="{preset}">'
            f'▶ {lbl}</button>'
        )
    demos_html = "".join(demo_btns) or '<span class="mono-meta">No demo clips in data/demo_clips/</span>'

    sess_rows = []
    for s in sessions[:4]:
        sess_rows.append(
            f'<div class="ledger-item">'
            f'<div class="ledger-row"><strong>{_esc(s["venue"])}</strong>'
            f'<span class="pill-ok">mode:{_esc(s["mode"])} · {s["duration_s"]}s</span></div>'
            f'<div class="mono-meta">key:{_esc(s["key_id"])} · anchored:{s["anchored_secs"]}/{s["duration_s"]}s · '
            f'drand:{_esc(s["drand_chain"])}…</div></div>'
        )
    sessions_html = "".join(sess_rows) or '<div class="mono-meta">No active session_*.jsonl in logs/</div>'

    stream_rows = []
    for st in streams:
        anc = st.get("latest_anchor")
        anc_badge = '<span class="mono-meta">EAS: pending</span>'
        if anc and anc.get("tx_hash"):
            exp = _esc(anc.get("explorer") or f"{BASESCAN_TX_PREFIX}{anc['tx_hash']}")
            txs = _esc(str(anc["tx_hash"])[:10] + "…")
            anc_badge = f'<a class="chip-anchor" href="{exp}" target="_blank" rel="noopener">Base EAS {txs} ↗</a>'
        stream_rows.append(
            f'<div class="ledger-item">'
            f'<div class="ledger-row"><strong>stream/{_esc(st["name"])}</strong>'
            f'<span class="pill-ok">{st["chunks"]} chunks · {st["gaps"]} gaps</span></div>'
            f'<div class="mono-meta">head (n={st["head_n"]}): {_esc(st["head_hash"][:16])}…</div>'
            f'<div class="ledger-row" style="margin-top:4px"><span class="mono-meta">transcripts: {st["transcripts"]}</span>'
            f'{anc_badge}</div></div>'
        )
    streams_html = "".join(stream_rows) or '<div class="mono-meta">No witnessed streams under data/witness/</div>'

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Wholetake · Broadcast Forensics &amp; Roomstamp Control Room</title>
<style>
  :root {{
    --bg-canvas: #070A10;
    --bg-panel: #0D1320;
    --bg-card: #131C2E;
    --bg-track: #1E293B;
    --border-subtle: #1E293B;
    --border-strong: #334155;
    --text-primary: #F8FAFC;
    --text-secondary: #94A3B8;
    --text-muted: #64748B;
    --cut: #F43F5E;
    --spliced: #F59E0B;
    --faithful: #10B981;
    --roomstamp: #38BDF8;
    --font-sans: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    --font-mono: ui-monospace, SFMono-Regular, "Cascadia Mono", Consolas, monospace;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0;
    background: var(--bg-canvas);
    color: var(--text-primary);
    font-family: var(--font-sans);
    line-height: 1.45;
    -webkit-font-smoothing: antialiased;
  }}
  .num, .mono-meta, .mono-chip {{
    font-family: var(--font-mono);
    font-variant-numeric: tabular-nums;
  }}
  a {{ color: var(--roomstamp); text-decoration: none; }}
  a:hover {{ text-decoration: underline; }}

  .topbar {{
    position: sticky;
    top: 0;
    z-index: 20;
    background: rgba(7, 10, 16, 0.92);
    border-bottom: 1px solid var(--border-strong);
    padding: 10px 20px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 16px;
    flex-wrap: wrap;
  }}
  .brand-wrap {{ display: flex; align-items: center; gap: 12px; }}
  .brand-dot {{
    width: 10px; height: 10px; border-radius: 50%;
    background: var(--faithful);
    box-shadow: 0 0 10px var(--faithful);
  }}
  .brand-title {{ font-size: 16px; font-weight: 800; letter-spacing: 0.04em; text-transform: uppercase; }}
  .brand-sub {{ font-size: 12px; color: var(--text-secondary); font-family: var(--font-mono); }}
  .topbar-stats {{ display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }}
  .stat-pill {{
    background: var(--bg-panel);
    border: 1px solid var(--border-strong);
    border-radius: 6px;
    padding: 4px 10px;
    font-size: 12px;
    font-family: var(--font-mono);
    color: var(--text-secondary);
  }}
  .stat-pill strong {{ color: var(--text-primary); margin-left: 4px; }}
  .simx-link {{
    background: #1D9BF0;
    color: #FFFFFF !important;
    font-weight: 700;
    font-size: 12.5px;
    padding: 6px 12px;
    border-radius: 6px;
    text-decoration: none !important;
  }}

  .grid-shell {{
    max-width: 1360px;
    margin: 0 auto;
    padding: 16px 20px 40px;
    display: grid;
    grid-template-columns: minmax(0, 65fr) minmax(340px, 35fr);
    gap: 18px;
    align-items: start;
  }}
  @media (max-width: 980px) {{
    .grid-shell {{ grid-template-columns: 1fr; }}
  }}

  .panel {{
    background: var(--bg-panel);
    border: 1px solid var(--border-strong);
    border-radius: 10px;
    padding: 16px;
    margin-bottom: 16px;
  }}
  .panel-title {{
    margin: 0 0 10px 0;
    font-size: 12px;
    font-weight: 800;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: var(--text-secondary);
    display: flex;
    justify-content: space-between;
    align-items: center;
  }}

  .intake-row {{
    display: flex;
    gap: 8px;
    margin-bottom: 10px;
  }}
  .url-input {{
    flex: 1;
    background: var(--bg-canvas);
    border: 1px solid var(--border-strong);
    border-radius: 6px;
    color: var(--text-primary);
    padding: 9px 12px;
    font-size: 14px;
    font-family: var(--font-mono);
  }}
  .url-input:focus {{ outline: none; border-color: var(--roomstamp); }}
  .btn-submit {{
    background: var(--roomstamp);
    color: #070A10;
    border: 0;
    border-radius: 6px;
    padding: 9px 16px;
    font-weight: 800;
    font-size: 13.5px;
    cursor: pointer;
  }}
  .drop-bar {{
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 10px;
    flex-wrap: wrap;
    padding: 10px 12px;
    border: 1px dashed var(--border-strong);
    border-radius: 8px;
    background: rgba(19, 28, 46, 0.45);
    margin-bottom: 10px;
  }}
  .drop-bar.dragover {{ border-color: var(--roomstamp); background: rgba(56, 189, 248, 0.1); }}
  .file-label {{
    background: var(--bg-card);
    border: 1px solid var(--border-strong);
    color: var(--text-primary);
    padding: 5px 11px;
    border-radius: 6px;
    font-size: 12.5px;
    font-weight: 600;
    cursor: pointer;
  }}
  .preset-bar {{ display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }}
  .preset-btn {{
    border-radius: 6px;
    padding: 6px 12px;
    font-size: 12.5px;
    font-weight: 700;
    cursor: pointer;
    border: 1px solid var(--border-strong);
    background: var(--bg-card);
    color: var(--text-primary);
  }}
  .preset-cut {{ border-color: var(--cut); color: #FFE4E6; background: rgba(244, 63, 94, 0.16); }}
  .preset-faithful {{ border-color: var(--faithful); color: #D1FAE5; background: rgba(16, 185, 129, 0.16); }}
  .status-banner {{
    display: none;
    margin-top: 10px;
    padding: 8px 12px;
    border-radius: 6px;
    background: rgba(56, 189, 248, 0.14);
    border: 1px solid var(--roomstamp);
    font-family: var(--font-mono);
    font-size: 12.5px;
    color: var(--text-primary);
  }}

  .verdict-card {{
    background: var(--bg-card);
    border: 1px solid var(--border-strong);
    border-radius: 10px;
    padding: 14px 16px;
    margin-bottom: 12px;
  }}
  .card-top {{
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    flex-wrap: wrap;
    margin-bottom: 8px;
  }}
  .badge-group {{ display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }}
  .stamp-pill {{
    display: inline-block;
    padding: 3px 9px;
    border-radius: 4px;
    font-size: 11.5px;
    font-weight: 800;
    letter-spacing: 0.06em;
    font-family: var(--font-mono);
  }}
  .pill-cut {{ background: #4C0519; color: #FFE4E6; border: 1px solid var(--cut); }}
  .pill-spliced {{ background: #451A03; color: #FEF3C7; border: 1px solid var(--spliced); }}
  .pill-faithful {{ background: #064E3B; color: #D1FAE5; border: 1px solid var(--faithful); }}
  .pill-roomstamp {{ background: rgba(56, 189, 248, 0.16); color: #E0F2FE; border: 1px solid var(--roomstamp); }}
  .pill-neutral {{ background: #1E293B; color: #E2E8F0; border: 1px solid #475569; }}
  .pill-active {{ background: #1E3A8A; color: #DBEAFE; border: 1px solid var(--roomstamp); }}
  .card-headline {{ margin: 0 0 10px 0; font-size: 17px; font-weight: 700; color: var(--text-primary); }}
  .cut-ribbon-svg {{ display: block; margin-bottom: 10px; }}
  .card-footer {{
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 10px;
    flex-wrap: wrap;
  }}
  .telemetry-chips {{ display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }}
  .mono-chip {{
    font-size: 11.5px;
    padding: 3px 8px;
    border-radius: 4px;
    background: var(--bg-panel);
    border: 1px solid var(--border-subtle);
    color: var(--text-secondary);
  }}
  .chip-cut {{ color: #FFE4E6; border-color: rgba(244, 63, 94, 0.5); background: rgba(76, 5, 25, 0.5); }}
  .chip-anchor {{ color: var(--roomstamp); border-color: rgba(56, 189, 248, 0.4); }}
  .mono-meta {{ font-size: 12px; color: var(--text-secondary); }}
  .card-actions {{ display: flex; align-items: center; gap: 8px; }}
  .btn-primary {{
    background: var(--roomstamp);
    color: #070A10 !important;
    font-weight: 800;
    font-size: 12.5px;
    padding: 6px 12px;
    border-radius: 6px;
    text-decoration: none !important;
  }}
  .btn-secondary {{
    background: var(--bg-panel);
    border: 1px solid var(--border-strong);
    color: var(--text-secondary) !important;
    font-size: 12px;
    font-family: var(--font-mono);
    padding: 5px 10px;
    border-radius: 6px;
    text-decoration: none !important;
  }}
  .ledger-item {{
    padding: 10px;
    border-radius: 8px;
    background: var(--bg-card);
    border: 1px solid var(--border-subtle);
    margin-bottom: 8px;
  }}
  .ledger-row {{ display: flex; justify-content: space-between; align-items: center; gap: 8px; font-size: 13px; }}
  .pill-ok {{
    font-family: var(--font-mono);
    font-size: 11px;
    color: #D1FAE5;
    background: #064E3B;
    padding: 2px 7px;
    border-radius: 4px;
  }}
  .empty-state {{
    padding: 28px;
    text-align: center;
    color: var(--text-secondary);
    border: 1px dashed var(--border-strong);
    border-radius: 8px;
  }}
</style>
</head>
<body>
<header class="topbar">
  <div class="brand-wrap">
    <span class="brand-dot"></span>
    <div>
      <div class="brand-title">Wholetake × Roomstamp</div>
      <div class="brand-sub">Acoustic Watermark &amp; Provable-Cut Forensics Control Room</div>
    </div>
  </div>
  <div class="topbar-stats">
    <span class="stat-pill">Verdicts <strong id="cntTotal" class="num">{counts["total"]}</strong></span>
    <span class="stat-pill">Cuts Caught <strong id="cntCut" class="num" style="color:var(--cut)">{counts["cut"]}</strong></span>
    <span class="stat-pill">Faithful <strong id="cntFaithful" class="num" style="color:var(--faithful)">{counts["faithful"]}</strong></span>
    <span class="stat-pill">Roomstamp Locked <strong id="cntStamp" class="num" style="color:var(--roomstamp)">{counts["stamp_verified"]}</strong></span>
    <a class="simx-link" href="http://127.0.0.1:8791" target="_blank" rel="noopener">Open Simulated X (:8791) ↗</a>
  </div>
</header>

<main class="grid-shell">
  <section class="left-col">
    <div class="panel">
      <div class="panel-title">
        <span>1. Inspect a Clip (URL, Direct Upload, or Stage Demo Preset)</span>
        <span class="mono-meta">16 kHz Witness + 48 kHz Roomstamp L1/L2</span>
      </div>
      <form id="urlForm" class="intake-row">
        <input id="clipUrlInput" class="url-input" type="text" placeholder="Paste clip URL (e.g. http://127.0.0.1:8791/media/...) and press Enter" autocomplete="off">
        <button type="submit" class="btn-submit">Check Clip →</button>
      </form>
      <div id="dropZone" class="drop-bar">
        <span class="mono-meta">Drag &amp; drop a phone recording (<code>.mp4</code>, <code>.m4a</code>, <code>.wav</code>, <code>.webm</code> ≤ 50 MB)</span>
        <label class="file-label">
          Upload Media File
          <input id="fileInput" type="file" accept=".mp4,.m4a,.wav,.webm,.ogg,.mp3,.mov" style="display:none">
        </label>
      </div>
      <div class="preset-bar">
        <span class="mono-meta">Stage Demo Presets:</span>
        {demos_html}
      </div>
      <div id="statusBanner" class="status-banner"></div>
    </div>

    <div class="panel">
      <div class="panel-title">
        <span>2. Forensic Evidence Feed &amp; Two-Track Cut Ribbons</span>
        <span id="pollIndicator" class="mono-meta">Live auto-refresh active</span>
      </div>
      <div id="verdictsFeed">
        {cards_html}
      </div>
    </div>
  </section>

  <aside class="right-col">
    <div class="panel">
      <div class="panel-title">
        <span>Roomstamp Acoustic Stage Emitter</span>
        <span class="pill-ok">{'ONLINE' if det['roomstamp_online'] else 'STANDBY'}</span>
      </div>
      <div class="ledger-item">
        <div class="ledger-row"><strong>In-Band Carrier Spec</strong><span class="mono-meta">roomstamp-v1 (48 kHz)</span></div>
        <div class="mono-meta" style="margin-top:4px">L1: {_esc(det["l1_band_hz"])}</div>
        <div class="mono-meta">L2: {_esc(det["l2_band_hz"])}</div>
        <div class="mono-meta">Detector: {_esc(det["command"])}@{_esc(det["commit"][:7])}</div>
      </div>
      {sessions_html}
    </div>

    <div class="panel">
      <div class="panel-title">
        <span>Witness Hash-Chain &amp; Base Sepolia EAS Ledger</span>
        <span class="pill-ok">{len(streams)} STREAM(S)</span>
      </div>
      {streams_html}
    </div>
  </aside>
</main>

<script>
(function() {{
  const urlForm = document.getElementById('urlForm');
  const urlInput = document.getElementById('clipUrlInput');
  const fileInput = document.getElementById('fileInput');
  const dropZone = document.getElementById('dropZone');
  const banner = document.getElementById('statusBanner');

  function showStatus(msg) {{
    banner.style.display = 'block';
    banner.textContent = msg;
  }}

  async function pollJobUntilDone(jobId) {{
    showStatus('Job ' + jobId + ' queued — running acoustic & Roomstamp detector...');
    for (let i = 0; i < 180; i++) {{
      await new Promise(r => setTimeout(r, 1000));
      try {{
        const res = await fetch('/api/verdict/' + encodeURIComponent(jobId));
        if (!res.ok) continue;
        const job = await res.json();
        showStatus('Job ' + jobId + ' status: ' + String(job.status || '').toUpperCase());
        if (job.status === 'done' || job.status === 'error') {{
          window.location.reload();
          return;
        }}
      }} catch (_e) {{}}
    }}
  }}

  urlForm.addEventListener('submit', async function(e) {{
    e.preventDefault();
    const u = urlInput.value.trim();
    if (!u) return;
    showStatus('Submitting clip URL...');
    try {{
      const r = await fetch('/api/verdict', {{
        method: 'POST',
        headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify({{url: u}})
      }});
      const data = await r.json();
      if (!r.ok) {{
        showStatus('Error: ' + (data.error || r.statusText));
        return;
      }}
      urlInput.value = '';
      await pollJobUntilDone(data.id);
    }} catch (err) {{
      showStatus('Submission failed: ' + err);
    }}
  }});

  document.querySelectorAll('.preset-btn').forEach(function(btn) {{
    btn.addEventListener('click', async function() {{
      const preset = btn.getAttribute('data-preset');
      if (!preset) return;
      showStatus('Running stage preset: ' + preset + '...');
      try {{
        const r = await fetch('/api/verdict', {{
          method: 'POST',
          headers: {{'Content-Type': 'application/json'}},
          body: JSON.stringify({{preset: preset}})
        }});
        const data = await r.json();
        if (!r.ok) {{
          showStatus('Error: ' + (data.error || r.statusText));
          return;
        }}
        await pollJobUntilDone(data.id);
      }} catch (err) {{
        showStatus('Preset submission failed: ' + err);
      }}
    }});
  }});

  async function uploadFile(file) {{
    if (!file) return;
    showStatus('Uploading ' + file.name + ' (' + Math.round(file.size / 1024) + ' KB)...');
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
        showStatus('Upload error: ' + (data.error || r.statusText));
        return;
      }}
      await pollJobUntilDone(data.id);
    }} catch (err) {{
      showStatus('Upload failed: ' + err);
    }}
  }}

  fileInput.addEventListener('change', function() {{
    if (fileInput.files && fileInput.files[0]) uploadFile(fileInput.files[0]);
  }});

  dropZone.addEventListener('dragover', function(e) {{
    e.preventDefault();
    dropZone.classList.add('dragover');
  }});
  dropZone.addEventListener('dragleave', function() {{
    dropZone.classList.remove('dragover');
  }});
  dropZone.addEventListener('drop', function(e) {{
    e.preventDefault();
    dropZone.classList.remove('dragover');
    if (e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0]) {{
      uploadFile(e.dataTransfer.files[0]);
    }}
  }});
}})();
</script>
</body>
</html>
"""
