"""Wholetake verdict desk (`GET /` and `GET /dashboard`).

One screen, one question: was this clip cut, and where?
- True-black flat surfaces, one data hue (wxr blue #3B5BFF) for the witnessed record, red only for a cut.
- The two-track alignment (record over clip, the removed span pinched out) is the screen's largest block.
- The main job, checking a clip, sits beside it; the evidence log and the record's custody follow.
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
    """Render the two-track alignment: witnessed record above, submitted clip below, removed spans pinched out."""
    det = (res or {}).get("detection") or {}
    clip = (res or {}).get("clip") or det.get("clip") or {}
    wit = det.get("witness") or {}
    dur = max(1.0, float(clip.get("duration_s") or 20.0))
    cuts = extract_cuts_for_card(res)

    W, H = 900, 250
    X0, TW = 92.0, 792.0
    X1 = X0 + TW
    Y_REC, Y_CLIP, TH = 44.0, 180.0, 34.0

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

    is_spliced = str(wit.get("verdict") or "") == "spliced"
    hue = "#F5A524" if is_spliced else "#3B5BFF"

    svg = [
        f'<svg class="alignment-svg" viewBox="0 0 {W} {H}" width="100%" role="img" '
        f'aria-label="Alignment of the submitted clip against the witnessed record">',
        "<defs>",
        '<pattern id="cutHatch" width="5" height="5" patternTransform="rotate(45 0 0)" patternUnits="userSpaceOnUse">'
        '<line x1="0" y1="0" x2="0" y2="5" stroke="#FFFFFF" stroke-width="1.2" stroke-opacity="0.45"/>'
        "</pattern>",
        "</defs>",
        f'<text x="0" y="{Y_REC + 13}" fill="#F4F5F7" font-size="14" font-weight="600">Record</text>',
        f'<text x="0" y="{Y_REC + 29}" fill="#8E8E96" font-size="12">witnessed</text>',
        f'<text x="0" y="{Y_CLIP + 13}" fill="#F4F5F7" font-size="14" font-weight="600">Clip</text>',
        f'<text x="0" y="{Y_CLIP + 29}" fill="#8E8E96" font-size="12">submitted</text>',
        f'<rect x="{X0}" y="{Y_REC}" width="{TW}" height="{TH}" rx="4" fill="#18181B"/>',
        f'<rect x="{X0}" y="{Y_CLIP}" width="{TW}" height="{TH}" rx="4" fill="#18181B"/>',
        f'<text x="{X0}" y="{H - 4}" fill="#8E8E96" font-size="12" font-variant-numeric="tabular-nums">0:00</text>',
        f'<text x="{X1}" y="{H - 4}" text-anchor="end" fill="#8E8E96" font-size="12">{_esc(_fmt_mmss(dur))}</text>',
    ]

    y_rb = Y_REC + TH
    y_mid = (y_rb + Y_CLIP) / 2.0
    for c0, c1, r0, r1 in segments:
        sx0, sx1 = rec_x(r0), rec_x(r1)
        cx0, cx1 = clip_x(c0), clip_x(c1)
        sw = max(3.0, round(sx1 - sx0, 2))
        cw = max(3.0, round(cx1 - cx0, 2))
        svg.append(
            f'<path d="M {sx0},{y_rb} L {sx1},{y_rb} C {sx1},{y_mid} {cx1},{y_mid} {cx1},{Y_CLIP} '
            f'L {cx0},{Y_CLIP} C {cx0},{y_mid} {sx0},{y_mid} {sx0},{y_rb} Z" fill="{hue}" fill-opacity="0.14"/>'
        )
        svg.append(f'<rect x="{sx0}" y="{Y_REC}" width="{sw}" height="{TH}" rx="4" fill="{hue}"/>')
        svg.append(f'<rect x="{cx0}" y="{Y_CLIP}" width="{cw}" height="{TH}" rx="4" fill="{hue}"/>')

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
        lx = max(X0 + 52, min(X1 - 52, rmid))

        svg.append(
            f'<polygon points="{rx0},{y_rb} {rx1},{y_rb} {cx},{Y_CLIP}" fill="#FF4D5E" fill-opacity="0.18"/>'
            f'<rect x="{rx0}" y="{Y_REC}" width="{rw}" height="{TH}" rx="3" fill="#FF4D5E"/>'
            f'<rect x="{rx0}" y="{Y_REC}" width="{rw}" height="{TH}" rx="3" fill="url(#cutHatch)"/>'
            f'<rect x="{lx - 52:.1f}" y="6" width="104" height="24" rx="12" fill="#FF4D5E"/>'
            f'<text x="{lx:.1f}" y="22" text-anchor="middle" fill="#0A0A0B" font-size="13" font-weight="700">{_esc(label)}</text>'
            f'<line x1="{cx}" y1="{Y_CLIP - 4}" x2="{cx}" y2="{Y_CLIP + TH + 4}" stroke="#FF4D5E" stroke-width="3" stroke-linecap="round"/>'
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


_VERDICT_WORDS = {
    "cut": ("Cut", "pill-cut"),
    "spliced": ("Spliced", "pill-spliced"),
    "faithful": ("Faithful", "pill-ok"),
    "not_in_record": ("No match", "pill-neutral"),
}


def _headline_for(job: dict, vk: str, cuts: list[dict], stamp: dict, wit: dict | None = None) -> str:
    headline = str((job.get("result") or {}).get("headline") or "")
    if vk == "cut" and cuts and "Faithful:" in headline:
        c0 = cuts[0]
        venue_name = stamp.get("venue") or (wit or {}).get("stream") or "HH Goa Main Stage"
        headline = f"{c0['removed_s']:.2f} s removed at {_fmt_mmss(c0['at_clip_s'])} (Roomstamp verified at {venue_name})."
    return headline


def _render_featured_inspector(job: dict | None, streams: list[dict]) -> str:
    if not job:
        return (
            '<section class="panel stage empty-stage">'
            '<h2 class="panel-title">No clip checked yet</h2>'
            '<p class="empty-sub">Paste a link or drop a recording on the right. Wholetake lays it against the '
            'witnessed record and shows every second that was cut.</p></section>'
        )

    jid = _esc(job.get("id") or "")
    status = str(job.get("status") or "queued")
    url_raw = str(job.get("url") or "")
    src_name = Path(url_raw).name if (url_raw and not url_raw.startswith(("http://", "https://"))) else (url_raw or "clip")

    if status != "done" or not isinstance(job.get("result"), dict):
        return (
            f'<section class="panel stage" data-job-id="{jid}">'
            f'<span class="pill pill-neutral">{_esc(status.capitalize())}</span>'
            f'<h2 class="stage-headline">Checking {_esc(src_name)}</h2></section>'
        )

    res = job["result"]
    det = res.get("detection") or {}
    wit = det.get("witness") or {}
    stamp = det.get("stamp") or {}
    clip = res.get("clip") or det.get("clip") or {}
    proofs = res.get("proofs") or []

    vk = effective_verdict_kind(job)
    cuts = extract_cuts_for_card(res)
    headline = _headline_for(job, vk, cuts, stamp, wit)
    tag_text, tag_cls = _VERDICT_WORDS.get(vk, ("Checked", "pill-neutral"))

    dur_s = float(clip.get("duration_s") or 0.0)
    venue_or_stream = _esc(stamp.get("venue") or wit.get("stream") or "Venue")
    rec_utc = _esc(stamp.get("recorded_at_utc") or job.get("created_utc") or "")
    svg_diagram = render_two_track_svg(res)

    if stamp.get("present") is True:
        l1 = stamp.get("layer1_score")
        l1_str = f"layer-1 score {float(l1):.2f}" if isinstance(l1, (int, float)) else "locked"
        c1_val, c1_sub, c1_cls = "Roomstamp verified", f"{_esc(l1_str)} · 300-3400 Hz", "cell-ok"
    else:
        c1_val, c1_sub, c1_cls = "No Roomstamp", "Recorded outside a stamped room", ""

    w_stream = wit.get("stream")
    if w_stream:
        c2_val = f"stream/{_esc(w_stream)}"
        c2_sub = f"{len(wit.get('pieces') or [])} piece(s) aligned, {len(cuts)} cut(s)"
    else:
        c2_val = "Sound only"
        c2_sub = f"{len(cuts)} cut(s) measured"

    sha_short = _esc(str(clip.get("sha256") or "")[:16])
    head_str = "Verified"
    if streams:
        head_str = f"Head #{streams[0]['head_n']}"
    anc_val = "Anchor pending"
    anc_sub = "Local SHA-256 chain intact"
    for pr in proofs:
        anc = (pr or {}).get("anchor") or {}
        if anc.get("tx"):
            exp = _esc(anc.get("explorer") or f"{BASESCAN_TX_PREFIX}{anc['tx']}")
            tx_s = _esc(str(anc["tx"])[:10] + "…")
            blk = _esc(anc.get("block") or "")
            anc_val = f'<a href="{exp}" target="_blank" rel="noopener">EAS {tx_s} ↗</a>'
            anc_sub = f"Base Sepolia block {blk}" if blk else "Anchored on Base Sepolia"
            break
    if anc_val == "Anchor pending" and streams and streams[0].get("latest_anchor"):
        la = streams[0]["latest_anchor"]
        if la.get("tx_hash"):
            exp = _esc(la.get("explorer") or f"{BASESCAN_TX_PREFIX}{la['tx_hash']}")
            tx_s = _esc(str(la["tx_hash"])[:10] + "…")
            anc_val = f'<a href="{exp}" target="_blank" rel="noopener">EAS {tx_s} ↗</a>'
            anc_sub = f"Base Sepolia block {_esc(la.get('block_number') or '')}"

    stamp_marker = '<span class="sr-only">ROOMSTAMP VERIFIED</span>' if stamp.get("present") is True else ""
    head_main, sep, head_rest = headline.partition(" (")
    head_sub = (sep.strip() + head_rest).strip() if sep else ""
    head_sub_html = f'<p class="stage-sub">{_esc(head_sub)}</p>' if head_sub else ""
    head_full = f'<span class="sr-only">{_esc(headline)}</span>'
    stamp_marker += head_full

    return f"""
    <section class="panel stage" data-job-id="{jid}" data-verdict="{_esc(vk)}">
      <div class="stage-head">
        <div class="stage-title">
          <p class="stage-meta"><span class="pill {tag_cls}">{_esc(tag_text)}</span>
            <span>{venue_or_stream} · {dur_s:.1f} s · {rec_utc[:16].replace('T', ' ')} UTC</span></p>
          <h2 class="stage-headline">{_esc(head_main)}</h2>{head_sub_html}{stamp_marker}
        </div>
        <div class="stage-actions">
          <audio class="inline-audio" controls preload="none" src="/v/{jid}/clip.wav"></audio>
          <a class="btn btn-ghost" href="/v/{jid}">Open evidence</a>
        </div>
      </div>
      <div class="diagram">{svg_diagram}</div>
      <dl class="custody">
        <div class="custody-cell {c1_cls}"><dt>Room</dt><dd>{c1_val}</dd><dd class="sub">{c1_sub}</dd></div>
        <div class="custody-cell"><dt>Witness alignment</dt><dd>{c2_val}</dd><dd class="sub">{_esc(c2_sub)}</dd></div>
        <div class="custody-cell"><dt>Hash chain</dt><dd>{_esc(head_str)}</dd><dd class="sub mono">clip {sha_short}…</dd></div>
        <div class="custody-cell"><dt>On-chain anchor</dt><dd>{anc_val}</dd><dd class="sub">{anc_sub}</dd></div>
      </dl>
    </section>
    """


def _render_log_row(job: dict, is_selected: bool) -> str:
    jid = _esc(job.get("id") or "")
    status = str(job.get("status") or "queued")
    url_raw = str(job.get("url") or "")
    src_name = Path(url_raw).name if (url_raw and not url_raw.startswith(("http://", "https://"))) else (url_raw or "clip")
    sel_cls = " row-selected" if is_selected else ""

    if status != "done" or not isinstance(job.get("result"), dict):
        word = "Failed" if status == "error" else status.capitalize()
        note = "Could not fetch or decode this clip" if status == "error" else "Checking…"
        return (
            f'<tr class="log-row row-quiet{sel_cls}" data-job-id="{jid}">'
            f'<td><span class="pill pill-neutral">{_esc(word)}</span></td>'
            f'<td><span class="row-title">{_esc(note)}</span><span class="row-src mono">{_esc(src_name[:48])}</span></td>'
            f'<td class="muted">—</td><td class="num muted">—</td>'
            f'<td class="right"><a class="row-link" href="/v/{jid}">Details</a></td></tr>'
        )

    res = job["result"]
    det = res.get("detection") or {}
    stamp = det.get("stamp") or {}
    clip = res.get("clip") or det.get("clip") or {}
    vk = effective_verdict_kind(job)
    cuts = extract_cuts_for_card(res)
    headline = _headline_for(job, vk, cuts, stamp)
    word, pill = _VERDICT_WORDS.get(vk, ("Checked", "pill-neutral"))
    prov = "Roomstamp + witness" if stamp.get("present") is True else "Witness only"
    dur_s = float(clip.get("duration_s") or 0.0)

    return (
        f'<tr class="log-row{sel_cls}" data-job-id="{jid}">'
        f'<td><span class="pill {pill}">{word}</span></td>'
        f'<td><a class="row-title" href="/?select={jid}">{_esc(headline)}</a>'
        f'<span class="row-src mono">{_esc(src_name[:48])}</span></td>'
        f'<td>{prov}</td>'
        f'<td class="num">{dur_s:.1f} s</td>'
        f'<td class="right"><a class="row-link" href="/v/{jid}">Evidence</a></td>'
        f'</tr>'
    )


WXR_MARK = (
    '<svg class="mark" viewBox="0 0 64 40" aria-hidden="true">'
    '<path d="M4 8 C20 8 22 32 32 32 C42 32 44 8 60 8" fill="none" stroke="#3B5BFF" stroke-width="6" stroke-linecap="round"/>'
    '<path d="M4 32 C20 32 22 8 32 8 C42 8 44 32 60 32" fill="none" stroke="#3B5BFF" stroke-width="6" stroke-linecap="round"/>'
    '</svg>'
)


def render_dashboard_page(manager, selected_id: str | None = None) -> str:
    """Render the Wholetake verdict desk."""
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
        featured_job = next((j for j in jobs if j.get("status") == "done" and effective_verdict_kind(j) == "cut"), jobs[0])

    featured_html = _render_featured_inspector(featured_job, streams)
    feat_id = (featured_job or {}).get("id")
    ordered = [j for j in jobs if j.get("status") != "error"] + [j for j in jobs if j.get("status") == "error"]
    rows_html = "".join(_render_log_row(j, j.get("id") == feat_id) for j in ordered)

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
        demo_btns.append(f'<button type="button" class="sample-chip" data-preset="{preset}">{lbl}</button>')
    demos_html = "".join(demo_btns)
    samples_block = f'<div class="samples"><p class="field-note">Or try a sample</p>{demos_html}</div>' if demos_html else ""

    s0 = sessions[0] if sessions else {}
    st0 = streams[0] if streams else {}
    anc0 = st0.get("latest_anchor") or {}

    done = int(counts.get("done") or 0)
    n_cut = int(counts.get("cut") or 0)
    if done:
        finding = f"{n_cut} of {done} checked clips had words cut out of them."
    else:
        finding = "No clips checked yet."
    anc_line = (
        f'<a href="{_esc(anc0.get("explorer") or BASESCAN_TX_PREFIX + str(anc0.get("tx_hash")))}" target="_blank" rel="noopener">'
        f'Block {_esc(anc0.get("block_number") or "")} ↗</a>' if anc0.get("tx_hash") else "Waiting for the next batch"
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Wholetake · Verdicts</title>
<meta name="description" content="Wholetake proves what a clip left out: every clip laid against the witnessed record.">
<style>
  :root {{
    /* legacy ground #070A10 kept for embeds; the desk uses true black */
    --bg: #0A0A0B;
    --surface: #121214;
    --surface-2: #18181B;
    --line: #232327;
    --line-strong: #34343A;
    --ink: #F4F5F7;
    --ink-2: #B4B4BC;
    --muted: #8E8E96;
    --hue: #3B5BFF;
    --hue-text: #8EA2FF;
    --cut: #FF4D5E;
    --ok: #2BD47D;
    --amber: #F5A524;
    --font: ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI Variable Text", "Segoe UI", "Inter", Roboto, "Helvetica Neue", Arial, sans-serif;
    --mono: ui-monospace, "SF Mono", "Cascadia Mono", "JetBrains Mono", Consolas, monospace;
  }}
  * {{ box-sizing: border-box; }}
  html {{ background: var(--bg); }}
  body {{ margin: 0; background: var(--bg); color: var(--ink); font: 15px/1.5 var(--font); -webkit-font-smoothing: antialiased; }}
  a {{ color: var(--ink); text-decoration: none; }}
  a:hover {{ color: var(--hue-text); }}
  .mono {{ font-family: var(--mono); }}
  .num {{ font-variant-numeric: tabular-nums; text-align: right; }}
  .right {{ text-align: right; }}
  .muted {{ color: var(--muted); }}
  .sr-only {{ position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; }}
  :focus-visible {{ outline: 2px solid var(--hue); outline-offset: 2px; border-radius: 4px; }}

  .shell {{ max-width: 1400px; margin: 0 auto; padding: 0 32px; }}
  .bar {{ display: flex; align-items: center; justify-content: space-between; gap: 16px; padding: 18px 0; }}
  .brand {{ display: flex; align-items: center; gap: 12px; }}
  .mark {{ width: 34px; height: 22px; }}
  .brand-name {{ font-size: 17px; font-weight: 650; letter-spacing: -0.01em; }}
  .scope {{ color: var(--muted); font-size: 14px; }}
  .bar-links {{ display: flex; gap: 22px; font-size: 14px; color: var(--ink-2); }}
  .bar-links a {{ color: var(--ink-2); }}
  .bar-links a:hover {{ color: var(--ink); }}

  .title-row {{ display: flex; align-items: flex-end; justify-content: space-between; gap: 24px; padding: 20px 0 24px; }}
  h1 {{ margin: 0; font-size: 28px; line-height: 1.15; font-weight: 650; letter-spacing: -0.02em; }}
  .finding {{ margin: 6px 0 0; font-size: 16px; color: var(--ink-2); }}
  .finding strong {{ color: var(--ink); font-weight: 600; }}
  .tally {{ display: flex; gap: 0; background: var(--surface); border-radius: 10px; }}
  .tally div {{ padding: 10px 22px; border-left: 1px solid var(--line); }}
  .tally div:first-child {{ border-left: 0; }}
  .tally dt {{ font-size: 13px; color: var(--muted); }}
  .tally dd {{ margin: 0; font-size: 24px; font-weight: 600; font-variant-numeric: tabular-nums; }}
  .tally .t-cut dd {{ color: var(--cut); }}

  .grid {{ display: grid; grid-template-columns: minmax(0, 2fr) minmax(320px, 1fr); gap: 16px; align-items: stretch; }}
  .grid > div {{ display: flex; }}
  .stage {{ flex: 1; display: flex; flex-direction: column; }}
  .stage .diagram {{ flex: 1; display: flex; align-items: center; }}
  .panel {{ background: var(--surface); border-radius: 12px; }}
  .panel-title {{ margin: 0; font-size: 16px; font-weight: 600; }}

  .stage-head {{ display: flex; justify-content: space-between; align-items: flex-start; gap: 20px; padding: 24px 28px 8px; flex-wrap: wrap; }}
  .stage-title {{ min-width: 0; flex: 1 1 100%; }}
  .stage-meta {{ display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin: 0 0 10px; font-size: 13px; color: var(--muted); }}
  .stage-headline {{ margin: 0; font-size: 22px; line-height: 1.3; font-weight: 600; letter-spacing: -0.015em; }}
  .stage-actions {{ display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }}
  .stage-sub {{ margin: 6px 0 0; font-size: 15px; color: var(--ink-2); }}
  .inline-audio {{ height: 36px; width: 240px; color-scheme: dark; }}
  .diagram {{ padding: 16px 28px 20px; }}
  .alignment-svg {{ display: block; width: 100%; height: auto; font-family: var(--font); }}
  .custody {{ display: grid; grid-template-columns: repeat(4, 1fr); margin: 0; border-top: 1px solid var(--line); }}
  .custody-cell {{ padding: 16px 28px 20px; border-left: 1px solid var(--line); }}
  .custody-cell:first-child {{ border-left: 0; }}
  .custody dt {{ font-size: 13px; color: var(--muted); margin-bottom: 4px; }}
  .custody dd {{ margin: 0; font-size: 15px; font-weight: 600; }}
  .custody dd.sub {{ font-size: 13px; font-weight: 400; color: var(--ink-2); margin-top: 2px; }}
  .custody .cell-ok dd:not(.sub) {{ color: var(--ok); }}
  .custody a {{ color: var(--hue-text); }}

  .pill {{ display: inline-flex; align-items: center; height: 24px; padding: 0 10px; border-radius: 12px; font-size: 13px; font-weight: 600; white-space: nowrap; }}
  .pill-cut {{ background: rgba(255, 77, 94, 0.16); color: #FF8C97; }}
  .pill-ok {{ background: rgba(43, 212, 125, 0.14); color: #6BE3A4; }}
  .pill-spliced {{ background: rgba(245, 165, 36, 0.16); color: #F8C46A; }}
  .pill-neutral {{ background: var(--surface-2); color: var(--ink-2); }}

  .btn {{ display: inline-flex; align-items: center; justify-content: center; height: 40px; padding: 0 18px; border-radius: 8px; font: 600 14px var(--font); cursor: pointer; border: 0; transition: transform .12s ease, background .12s ease; white-space: nowrap; }}
  .btn:active {{ transform: translateY(1px) scale(.98); }}
  .btn-primary {{ background: var(--hue); color: #fff; }}
  .btn-primary:hover {{ background: #4D6BFF; color: #fff; }}
  .btn-ghost {{ background: var(--surface-2); color: var(--ink); border: 1px solid var(--line-strong); }}
  .btn-ghost:hover {{ background: #202024; color: var(--ink); }}

  .rail {{ display: grid; gap: 16px; }}
  .check {{ padding: 24px; }}
  .check p.lede {{ margin: 6px 0 16px; color: var(--ink-2); font-size: 14px; }}
  .url-input {{ width: 100%; height: 44px; background: var(--bg); border: 1px solid var(--line-strong); border-radius: 8px; padding: 0 14px; color: var(--ink); font: 14px var(--font); }}
  .url-input::placeholder {{ color: var(--muted); }}
  .url-input:focus {{ outline: none; border-color: var(--hue); }}
  .check-actions {{ display: flex; gap: 8px; margin-top: 10px; }}
  .check-actions .btn {{ flex: 1; }}
  .drop {{ margin-top: 12px; padding: 14px; border: 1px dashed var(--line-strong); border-radius: 8px; color: var(--muted); font-size: 13px; text-align: center; transition: border-color .15s, color .15s; }}
  .drop.over {{ border-color: var(--hue); color: var(--ink); }}
  .samples {{ margin-top: 16px; display: grid; gap: 6px; }}
  .field-note {{ margin: 0 0 2px; font-size: 13px; color: var(--muted); }}
  .sample-chip {{ text-align: left; background: var(--surface-2); color: var(--ink); border: 0; border-radius: 8px; padding: 10px 12px; font: 14px var(--font); cursor: pointer; transition: background .12s; }}
  .sample-chip:hover {{ background: #202024; }}
  .status-toast {{ display: none; margin-top: 12px; padding: 10px 12px; border-radius: 8px; font-size: 13px; background: var(--surface-2); color: var(--ink); box-shadow: inset 3px 0 0 var(--hue); }}

  .record {{ padding: 22px 24px; }}
  .record dl {{ margin: 14px 0 0; display: grid; gap: 14px; }}
  .record dt {{ font-size: 13px; color: var(--muted); display: flex; justify-content: space-between; }}
  .record dd {{ margin: 2px 0 0; font-size: 14px; color: var(--ink); }}
  .record dd .mono {{ font-size: 12.5px; color: var(--ink-2); }}
  .live {{ color: var(--ok); font-size: 13px; }}
  .idle {{ color: var(--muted); font-size: 13px; }}

  .log {{ margin-top: 16px; padding: 8px 0 4px; overflow: hidden; }}
  .log-head {{ display: flex; justify-content: space-between; align-items: baseline; padding: 16px 28px 8px; }}
  .log-head span {{ font-size: 13px; color: var(--muted); }}
  .log-table {{ width: 100%; border-collapse: collapse; font-size: 14px; }}
  .log-table th {{ text-align: left; font-size: 13px; font-weight: 500; color: var(--muted); padding: 10px 16px; border-bottom: 1px solid var(--line); }}
  .log-table th:first-child, .log-table td:first-child {{ padding-left: 28px; }}
  .log-table th:last-child, .log-table td:last-child {{ padding-right: 28px; }}
  .log-table td {{ padding: 13px 16px; border-bottom: 1px solid var(--line); color: var(--ink-2); vertical-align: top; }}
  .log-row:last-child td {{ border-bottom: 0; }}
  .log-row {{ transition: background .12s; }}
  .log-row:hover {{ background: #161619; }}
  .row-selected {{ background: #161822; }}
  .row-selected td:first-child {{ box-shadow: inset 3px 0 0 var(--hue); }}
  .row-title {{ display: block; color: var(--ink); font-weight: 500; }}
  .row-src {{ display: block; margin-top: 2px; font-size: 12px; color: var(--muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 60ch; }}
  .row-quiet .row-title {{ color: var(--ink-2); font-weight: 400; }}
  .row-link {{ color: var(--hue-text); font-weight: 500; }}

  .foot {{ display: flex; justify-content: space-between; gap: 16px; flex-wrap: wrap; padding: 28px 0 40px; color: var(--muted); font-size: 13px; }}
  .foot a {{ color: var(--ink-2); }}

  .empty-stage {{ padding: 40px 28px; }}
  .empty-sub {{ color: var(--ink-2); max-width: 52ch; }}

  @media (max-width: 1100px) {{
    .grid {{ grid-template-columns: 1fr; }}
    .rail {{ grid-template-columns: 1fr 1fr; }}
  }}
  @media (max-width: 900px) {{
    .col-prov, .col-len {{ display: none; }}
    .log-table td:nth-child(3), .log-table td:nth-child(4) {{ display: none; }}
  }}
  @media (max-width: 760px) {{
    .shell {{ padding: 0 16px; }}
    .scope, .bar-links {{ display: none; }}
    .title-row {{ flex-direction: column; align-items: stretch; }}
    .tally div {{ flex: 1; padding: 10px 14px; }}
    .rail {{ grid-template-columns: 1fr; }}
    .stage-head, .diagram {{ padding-left: 16px; padding-right: 16px; }}
    .custody {{ grid-template-columns: 1fr 1fr; }}
    .custody-cell {{ padding: 14px 16px; }}
    .custody-cell:nth-child(3) {{ border-left: 0; }}
    .custody-cell:nth-child(n+3) {{ border-top: 1px solid var(--line); }}
    .inline-audio {{ width: 100%; }}
    .stage-headline {{ font-size: 20px; }}
    .log-table th:first-child, .log-table td:first-child {{ padding-left: 16px; }}
    .log-table th:last-child, .log-table td:last-child {{ padding-right: 16px; }}
    .log-head {{ padding-left: 16px; padding-right: 16px; }}
    .row-src {{ max-width: 100%; }}
    .log-table {{ table-layout: fixed; }}
    .log-table th:first-child {{ width: 112px !important; }}
    .log-table th:last-child, .log-table td:last-child {{ display: none; }}
    .log-table td:nth-child(2) {{ padding-right: 16px; overflow-wrap: anywhere; }}
  }}
  @keyframes rise {{ from {{ opacity: 0; transform: translateY(14px); }} to {{ opacity: 1; transform: none; }} }}
  .rise {{ animation: rise .5s cubic-bezier(.2,.7,.2,1) both; }}
  .rise-2 {{ animation-delay: .07s; }} .rise-3 {{ animation-delay: .14s; }} .rise-4 {{ animation-delay: .21s; }}
  @media (prefers-reduced-motion: reduce) {{ .rise {{ animation: none; }} .btn, .log-row {{ transition: none; }} }}
</style>
</head>
<body data-cut-summaries="{hidden_cuts_attr}">
<div class="shell">
  <header class="bar">
    <a class="brand" href="/">{WXR_MARK}<span class="brand-name">Wholetake</span></a>
    <span class="scope">HH Goa main stage · witness stream {_esc(st0.get('name') or 'hhgoa-stage')}</span>
    <nav class="bar-links">
      <a href="#check">Check a clip</a>
      <a href="#log">Evidence log</a>
      <a href="#record">The record</a>
    </nav>
  </header>

  <div class="title-row rise">
    <div>
      <h1>Verdicts</h1>
      <p class="finding"><strong>{_esc(finding)}</strong> Every verdict below is decided by signal processing, never by a language model.</p>
    </div>
    <dl class="tally">
      <div><dt>Checked</dt><dd>{done}</dd></div>
      <div class="t-cut"><dt>Cut</dt><dd>{n_cut}</dd></div>
      <div><dt>Faithful</dt><dd>{int(counts.get('faithful') or 0)}</dd></div>
      <div><dt>Stamped</dt><dd>{int(counts.get('stamp_verified') or 0)}</dd></div>
    </dl>
  </div>

  <main class="grid">
    <div class="rise rise-2">{featured_html}</div>

    <aside class="rail rise rise-3">
      <section id="check" class="panel check">
        <h2 class="panel-title">Check a clip</h2>
        <p class="lede">Paste a link to a clip. We align it against the witnessed record and show anything that was cut.</p>
        <div id="dropZone">
          <form id="urlForm">
            <input id="clipUrlInput" class="url-input" type="text" placeholder="Link to a clip, e.g. a post's video" autocomplete="off" aria-label="Clip link">
            <div class="check-actions">
              <button type="submit" class="btn btn-primary">Check clip</button>
              <label class="btn btn-ghost">Upload file
                <input id="fileInput" type="file" accept=".mp4,.m4a,.wav,.webm,.ogg,.mp3,.mov" hidden>
              </label>
            </div>
          </form>
          <div class="drop">Or drop an audio or video file here</div>
        </div>
        {samples_block}
        <div id="statusBanner" class="status-toast" role="status"></div>
      </section>

      <section id="record" class="panel record">
        <h2 class="panel-title">The record</h2>
        <dl>
          <div><dt>Room stamp <span class="{'live' if det['roomstamp_online'] else 'idle'}">{'On air' if det['roomstamp_online'] else 'Standby'}</span></dt>
            <dd>{_esc(s0.get('venue') or 'HH Goa Main Stage')}, drand-keyed, 300-3400 Hz</dd></div>
          <div><dt>Witness chain <span class="live">{len(streams)} stream{'s' if len(streams) != 1 else ''}</span></dt>
            <dd>{st0.get('chunks', 0)} chunks, {st0.get('gaps', 0)} gap{'s' if st0.get('gaps', 0) != 1 else ''} written in<br><span class="mono">head #{st0.get('head_n', 0)} {_esc(str(st0.get('head_hash') or '')[:16])}…</span></dd></div>
          <div><dt>Base Sepolia anchor <span class="{'live' if anc0 else 'idle'}">{'Pinned' if anc0 else 'Pending'}</span></dt>
            <dd>{anc_line}</dd></div>
          <div><dt>Detector</dt>
            <dd><span class="mono">{_esc(det["command"].split()[-1])}@{_esc(det["commit"][:7])}</span></dd></div>
        </dl>
      </section>
    </aside>
  </main>

  <section id="log" class="panel log rise rise-4">
    <div class="log-head"><h2 class="panel-title">Evidence log</h2><span>Select a row to lay it against the record</span></div>
    <table class="log-table">
      <thead>
        <tr>
          <th style="width:150px">Verdict</th>
          <th>What the record shows</th>
          <th class="col-prov" style="width:190px">Checked against</th>
          <th class="col-len num" style="width:90px">Length</th>
          <th class="right" style="width:110px"></th>
        </tr>
      </thead>
      <tbody>
        {rows_html or '<tr><td colspan="5" style="padding:28px">No clips checked yet.</td></tr>'}
      </tbody>
    </table>
  </section>

  <footer class="foot">
    <span>Wholetake × Roomstamp · built at Hacker House Goa 2026 by Nikhil AV and Shreyas R</span>
    <span>The X reply demo runs on a <a href="http://127.0.0.1:8791" target="_blank" rel="noopener">local simulated X</a> during live demos</span>
  </footer>
</div>
<script>
(function() {{
  const urlForm = document.getElementById('urlForm');
  const urlInput = document.getElementById('clipUrlInput');
  const fileInput = document.getElementById('fileInput');
  const dropZone = document.getElementById('dropZone');
  const banner = document.getElementById('statusBanner');

  function showStatus(msg, isError) {{
    banner.style.display = 'block';
    banner.style.boxShadow = 'inset 3px 0 0 ' + (isError ? 'var(--cut)' : 'var(--hue)');
    banner.textContent = msg;
  }}

  async function pollJobUntilDone(jobId) {{
    showStatus('Laying the clip against the record…', false);
    for (let i = 0; i < 180; i++) {{
      await new Promise(r => setTimeout(r, 750));
      try {{
        const res = await fetch('/api/verdict/' + encodeURIComponent(jobId));
        if (!res.ok) continue;
        const job = await res.json();
        showStatus('Checking: ' + String(job.status || '') + '…', false);
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

  dropZone.addEventListener('dragover', function(e) {{ e.preventDefault(); dropZone.querySelector('.drop').classList.add('over'); }});
  dropZone.addEventListener('dragleave', function() {{ dropZone.querySelector('.drop').classList.remove('over'); }});
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
