"""Self-contained HTML evidence page renderer for Wholetake verdicts (no external requests)."""
import html
import json
from pathlib import Path

from verdict.compose import _fmt_mmss

BASESCAN_TX_PREFIX = "https://sepolia.basescan.org/tx/"


def _esc(val: object) -> str:
    if val is None:
        return ""
    return html.escape(str(val), quote=True)


def _load_sentence_highlight(witness_root: Path, stream: str | None, rem: dict) -> tuple[str, str, str]:
    """Return (before_words, removed_words, after_words) from the witness chunk transcript."""
    words_removed = str(rem.get("words") or "").strip()
    seq = rem.get("seq")
    src_s = rem.get("src_s") or [0.0, 0.0]
    s0, s1 = float(src_s[0]), float(src_s[1])

    if stream and isinstance(seq, int):
        tx_p = witness_root / stream / f"{seq:06d}.json"
        if tx_p.exists():
            try:
                tx = json.loads(tx_p.read_text())
                wlist = tx.get("words") or []
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


def _css() -> str:
    return """
:root {
  --bg: #f8f7f4;
  --surface: #ffffff;
  --surface-subtle: #f1efe9;
  --text: #18181b;
  --muted: #52525b;
  --border: #e4e2dd;
  --accent-match: #2563eb;
  --accent-cut: #dc2626;
  --accent-cut-bg: #fee2e2;
  --accent-cut-text: #7f1d1d;
  --accent-ok: #15803d;
  --accent-warn: #b45309;
  --badge-bg: #e4e4e7;
  --badge-text: #27272a;
  --font-sans: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  --font-mono: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #0f1012;
    --surface: #18191c;
    --surface-subtle: #202227;
    --text: #f4f4f5;
    --muted: #a1a1aa;
    --border: #2c2e33;
    --accent-match: #3b82f6;
    --accent-cut: #ef4444;
    --accent-cut-bg: #451a1a;
    --accent-cut-text: #fecaca;
    --accent-ok: #22c55e;
    --accent-warn: #f59e0b;
    --badge-bg: #27272a;
    --badge-text: #e4e4e7;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0;
  padding: 24px 16px 56px;
  background: var(--bg);
  color: var(--text);
  font-family: var(--font-sans);
  line-height: 1.55;
}
main {
  max-width: 760px;
  margin: 0 auto;
}
header.brand {
  display: flex;
  justify-content: space-between;
  align-items: center;
  border-bottom: 1px solid var(--border);
  padding-bottom: 12px;
  margin-bottom: 24px;
  font-size: 0.875rem;
  color: var(--muted);
}
.badges { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.badge {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 4px;
  background: var(--badge-bg);
  color: var(--badge-text);
  font-size: 0.75rem;
  font-weight: 600;
  letter-spacing: 0.02em;
}
.badge.warn {
  background: var(--accent-cut-bg);
  color: var(--accent-cut-text);
}
h1.headline {
  font-size: clamp(1.4rem, 3.8vw, 2rem);
  line-height: 1.25;
  margin: 0 0 20px;
  font-weight: 700;
  letter-spacing: -0.015em;
}
section.card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 18px 20px;
  margin-bottom: 16px;
}
section.card.advisory {
  background: var(--surface-subtle);
  font-size: 0.92rem;
  color: var(--muted);
}
h2.sec-title {
  font-size: 0.85rem;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--muted);
  margin: 0 0 12px;
}
audio.player {
  width: 100%;
  margin-bottom: 14px;
}
.timeline-wrap {
  position: relative;
  height: 28px;
  background: var(--surface-subtle);
  border: 1px solid var(--border);
  border-radius: 6px;
  margin: 12px 0 8px;
  overflow: visible;
}
.piece-bar {
  position: absolute;
  top: 0;
  bottom: 0;
  background: var(--accent-match);
  opacity: 0.78;
  border-radius: 3px;
}
.seam-marker {
  position: absolute;
  top: -5px;
  bottom: -5px;
  width: 6px;
  margin-left: -3px;
  background: var(--accent-cut);
  border-radius: 3px;
  cursor: pointer;
  z-index: 3;
}
.seam-marker .seam-tip {
  display: none;
  position: absolute;
  bottom: 36px;
  left: 50%;
  transform: translateX(-50%);
  min-width: 180px;
  max-width: 260px;
  padding: 6px 10px;
  background: var(--surface);
  color: var(--text);
  border: 1px solid var(--border);
  border-radius: 6px;
  font-size: 0.8rem;
  box-shadow: 0 4px 12px rgba(0,0,0,0.15);
  z-index: 10;
}
.seam-marker:hover .seam-tip,
.seam-marker:focus .seam-tip,
.seam-marker:active .seam-tip {
  display: block;
}
.record-quote {
  font-size: 1.05rem;
  margin: 10px 0;
  padding: 10px 14px;
  background: var(--surface-subtle);
  border-left: 3px solid var(--accent-cut);
  border-radius: 4px;
}
mark.removed {
  background: var(--accent-cut-bg);
  color: var(--accent-cut-text);
  padding: 1px 5px;
  border-radius: 3px;
  font-weight: 600;
}
.proof-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.88rem;
}
.proof-table th, .proof-table td {
  text-align: left;
  padding: 8px 6px;
  border-bottom: 1px solid var(--border);
}
.mono {
  font-family: var(--font-mono);
  font-size: 0.82rem;
}
a { color: var(--accent-match); text-decoration: none; }
a:hover { text-decoration: underline; }
footer.links {
  margin-top: 20px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 0.88rem;
  color: var(--muted);
}
""".strip()


def render_verdict_page(job: dict, witness_root: Path | str) -> str:
    """Render a self-contained HTML page for a queued, running, error, or completed verdict job."""
    wroot = Path(witness_root)
    job_id = str(job.get("id") or "")
    status = str(job.get("status") or "queued")
    result = job.get("result")

    if status != "done" or not isinstance(result, dict):
        if status == "error":
            err = _esc(job.get("error") or "Unknown error")
            body = (
                f'<h1 class="headline">Couldn’t check this clip</h1>'
                f'<section class="card"><p>{err}</p></section>'
            )
            meta_refresh = ""
        else:
            body = (
                f'<h1 class="headline">Checking clip… ({_esc(status)})</h1>'
                f'<section class="card"><p>Job <span class="mono">{_esc(job_id)}</span> is currently '
                f'<strong>{_esc(status)}</strong>. This page refreshes automatically every 2 seconds.</p></section>'
            )
            meta_refresh = '<meta http-equiv="refresh" content="2">'
        return (
            "<!doctype html>\n<html lang=\"en\">\n<head>\n"
            "<meta charset=\"utf-8\">\n"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
            f"{meta_refresh}\n"
            f"<title>Wholetake Verdict · {_esc(job_id)}</title>\n"
            f"<style>{_css()}</style>\n"
            "</head>\n<body>\n<main>\n"
            '<header class="brand"><span>WHOLETAKE EVIDENCE RECORD</span></header>\n'
            f"{body}\n"
            "</main>\n</body>\n</html>\n"
        )

    headline = _esc(result.get("headline") or "Verdict")
    det_kind = str(result.get("detector_kind") or "full")
    clip = result.get("clip") or {}
    dur_s = max(0.001, float(clip.get("duration_s") or 1.0))
    detection = result.get("detection") or {}
    wit = detection.get("witness") or {}
    stream = wit.get("stream")
    pieces = wit.get("pieces") or []
    removed = wit.get("removed") or []
    coverage = result.get("coverage") or {}
    label = result.get("label")
    proofs = result.get("proofs") or []

    badges_html = [f'<span class="badge">{_esc(wit.get("verdict") or "unknown")}</span>']
    if det_kind == "text-only":
        badges_html.append('<span class="badge warn">text match only</span>')
    else:
        badges_html.append('<span class="badge">acoustic + transcript match</span>')

    # Timeline pieces & cut seams
    piece_bars: list[str] = []
    for p in pieces:
        cs = p.get("clip_s") or [0.0, 0.0]
        s0 = max(0.0, min(dur_s, float(cs[0])))
        s1 = max(s0, min(dur_s, float(cs[1])))
        left_pct = round((s0 / dur_s) * 100.0, 2)
        width_pct = max(0.5, round(((s1 - s0) / dur_s) * 100.0, 2))
        seq = p.get("seq")
        src_s = p.get("src_s") or [0.0, 0.0]
        tip = f"seq {seq} [{float(src_s[0]):.1f}s–{float(src_s[1]):.1f}s]"
        piece_bars.append(
            f'<div class="piece-bar" style="left:{left_pct}%;width:{width_pct}%;" title="{_esc(tip)}"></div>'
        )

    seam_markers: list[str] = []
    record_sentences: list[str] = []
    for r in removed:
        at_s = max(0.0, min(dur_s, float(r.get("after_clip_s") or 0.0)))
        left_pct = round((at_s / dur_s) * 100.0, 2)
        src_s = r.get("src_s") or [0.0, 0.0]
        cut_dur = max(0.1, round(float(src_s[1]) - float(src_s[0]), 1))
        rem_words = str(r.get("words") or "").strip()
        seam_markers.append(
            f'<div class="seam-marker" tabindex="0" style="left:{left_pct}%;">'
            f'<span class="seam-tip">{_esc(f"{cut_dur:.1f}s removed at {_fmt_mmss(at_s)}: “{rem_words}”")}</span>'
            f"</div>"
        )
        before_w, mid_w, after_w = _load_sentence_highlight(wroot, stream, r)
        record_sentences.append(
            f'<div class="record-quote">'
            f'<div class="mono" style="color:var(--muted);margin-bottom:4px;">'
            f'Cut at {_esc(_fmt_mmss(at_s))} ({_esc(f"{cut_dur:.1f} s")} removed · chunk #{_esc(r.get("seq"))})</div>'
            f"<span>{_esc(before_w)}<mark class=\"removed\">{_esc(mid_w)}</mark>{_esc(after_w)}</span>"
            f"</div>"
        )

    if record_sentences:
        record_sec = (
            '<section class="card">'
            '<h2 class="sec-title">What the record says</h2>'
            + "".join(record_sentences)
            + "</section>"
        )
    else:
        record_sec = (
            '<section class="card">'
            '<h2 class="sec-title">What the record says</h2>'
            "<p>No words were removed between matched segments of the witnessed record.</p>"
            "</section>"
        )

    # Coverage notes (gaps) if any
    cov_notes = coverage.get("notes") or []
    if cov_notes:
        items = "".join(f"<li>{_esc(n)}</li>" for n in cov_notes)
        coverage_sec = (
            '<section class="card">'
            '<h2 class="sec-title">Coverage notes</h2>'
            f"<ul>{items}</ul>"
            "</section>"
        )
    else:
        coverage_sec = ""

    # Advisory AI note (secondary)
    advisory_items: list[str] = []
    if isinstance(label, dict):
        # label.py's shape: {"model", "spans": [{"effect", "why", "removed_words", ...}]}
        labels_list = label.get("spans") if isinstance(label.get("spans"), list) else []
        for item in labels_list:
            if not isinstance(item, dict):
                continue
            eff = item.get("effect")
            why = item.get("why")
            if eff or why:
                advisory_items.append(
                    f"<p><strong>{_esc(eff or 'advisory')}</strong>: {_esc(why or '')}</p>"
                )
    if advisory_items:
        advisory_sec = (
            '<section class="card advisory">'
            '<h2 class="sec-title">AI note (advisory)</h2>'
            + "".join(advisory_items)
            + "</section>"
        )
    else:
        advisory_sec = ""

    # Proof table per chunk
    proof_rows: list[str] = []
    for pr in proofs:
        if not isinstance(pr, dict):
            continue
        seq = pr.get("seq")
        rec_utc = pr.get("chunk_received_utc") or "—"
        anchor = pr.get("anchor") if isinstance(pr.get("anchor"), dict) else {}
        tx = anchor.get("tx") or anchor.get("tx_hash")
        if tx:
            short_tx = f"{str(tx)[:10]}…{str(tx)[-6:]}" if len(str(tx)) > 18 else str(tx)
            tx_html = f'<a class="mono" href="{_esc(BASESCAN_TX_PREFIX + str(tx))}">{_esc(short_tx)}</a>'
        else:
            tx_html = '<span class="mono">anchoring pending</span>'

        head_seq = anchor.get("head_seq") if anchor.get("head_seq") is not None else pr.get("head_seq", seq)
        head_hash = str(anchor.get("head_hash") or pr.get("head_hash") or pr.get("entry_hash") or "")
        short_hash = f"{head_hash[:10]}…" if len(head_hash) > 10 else (head_hash or "—")
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

    proof_body = (
        '<table class="proof-table">'
        "<thead><tr><th>Chunk</th><th>Received (UTC)</th><th>Base Anchor</th><th>Chain Head</th><th>Proof</th></tr></thead>"
        f"<tbody>{''.join(proof_rows)}</tbody></table>"
        if proof_rows
        else "<p>No matched witness chunks.</p>"
    )
    proof_sec = f'<section class="card"><h2 class="sec-title">Proof</h2>{proof_body}</section>'

    return (
        "<!doctype html>\n<html lang=\"en\">\n<head>\n"
        "<meta charset=\"utf-8\">\n"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
        f"<title>{headline} · Wholetake</title>\n"
        f"<style>{_css()}</style>\n"
        "</head>\n<body>\n<main>\n"
        '<header class="brand">'
        "<span>WHOLETAKE EVIDENCE RECORD</span>"
        f'<div class="badges">{"".join(badges_html)}</div>'
        "</header>\n"
        f'<h1 class="headline">{headline}</h1>\n'
        '<section class="card">'
        '<h2 class="sec-title">Clip &amp; alignment timeline</h2>'
        f'<audio class="player" controls preload="metadata" src="/v/{_esc(job_id)}/clip.wav"></audio>'
        f'<div class="timeline-wrap">{"".join(piece_bars)}{"".join(seam_markers)}</div>'
        f'<div class="mono" style="color:var(--muted);">Duration: {_esc(f"{dur_s:.1f}s")} · '
        f'Stream: {_esc(stream or "none")}</div>'
        "</section>\n"
        f"{record_sec}\n"
        f"{coverage_sec}\n"
        f"{advisory_sec}\n"
        f"{proof_sec}\n"
        '<footer class="links">'
        f'<span class="mono">verdict_id: {_esc(str(result.get("verdict_id") or "")[:16])}…</span>'
        f'<a href="/v/{_esc(job_id)}/verdict.json" download="verdict-{_esc(job_id)}.json">Download verdict.json</a>'
        "</footer>\n"
        "</main>\n</body>\n</html>\n"
    )
