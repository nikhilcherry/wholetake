"""Deterministic X reply formatter for Wholetake verdicts (always <= 280 chars)."""
from datetime import datetime
from pathlib import Path

from verdict.compose import _fmt_mmss, _record_snippet

MAX_TWEET_LEN = 280


def _fmt_stream_dt(utc_str: str | None) -> str | None:
    if not isinstance(utc_str, str) or not utc_str:
        return None
    try:
        dt = datetime.fromisoformat(utc_str.replace("Z", "+00:00"))
        return f"{dt.day} {dt.strftime('%b %H:%M')} UTC"
    except Exception:
        return None


def _stream_tag(verdict: dict) -> str:
    wit = (verdict.get("detection") or {}).get("witness") or {}
    stream = wit.get("stream")
    proofs = verdict.get("proofs") or []
    rec_utc = None
    for p in proofs:
        if isinstance(p, dict) and p.get("chunk_received_utc"):
            rec_utc = p["chunk_received_utc"]
            break
    dt_str = _fmt_stream_dt(rec_utc)
    if stream and dt_str:
        return f"({stream} · {dt_str})"
    if stream:
        return f"({stream})"
    return ""


def _all_anchored(verdict: dict) -> bool:
    proofs = verdict.get("proofs") or []
    if not proofs:
        return False
    for p in proofs:
        if not isinstance(p, dict) or not p.get("proof_ok"):
            return False
        anchor = p.get("anchor")
        if not isinstance(anchor, dict):
            return False
        if not (anchor.get("tx") or anchor.get("tx_hash") or anchor.get("uid") or anchor.get("eas_uid")):
            return False
    return True


def _clean_quote_words(verdict: dict, r0: dict, witness_root: Path | str | None = None) -> str:
    """Extract the quote text (without outer ellipses) from the headline or transcript."""
    headline = str(verdict.get("headline") or "")
    if 'the record says "' in headline and headline.count('"') >= 2:
        inner = headline.split('the record says "', 1)[1].rsplit('"', 1)[0].strip()
        inner = inner.strip("…").strip()
        if inner:
            return inner
    if witness_root is not None:
        wit = (verdict.get("detection") or {}).get("witness") or {}
        snip = _record_snippet(Path(witness_root), wit.get("stream"), r0).strip("…").strip()
        if snip:
            return snip
    return str(r0.get("words") or "").strip()


def reply_text(
    verdict: dict,
    include_link: bool = False,
    page_base: str = "http://127.0.0.1:8790",
    job_id: str | None = None,
    witness_root: Path | str | None = None,
) -> str:
    """Format a deterministic, non-accusatory reply <= 280 characters.

    Never includes the advisory Gemini label. If truncation is needed on a cut
    verdict, the record quote is shortened first so the verdict word, cut duration,
    and link/badges always survive intact.
    """
    wit = (verdict.get("detection") or {}).get("witness") or {}
    vkind = wit.get("verdict") or "not_found"
    pieces = wit.get("pieces") or []
    removed = wit.get("removed") or []

    # Roomstamp (venue PA stamp) speaks for clips the witness never saw; same rule as compose.build_headline
    stamp = (verdict.get("detection") or {}).get("stamp") or {}
    by_stamp = stamp.get("present") is True and vkind in ("faithful", "not_found") and not removed and (
        bool(stamp.get("cuts")) or vkind == "not_found")

    suffixes: list[str] = []
    if _all_anchored(verdict):
        suffixes.append(" · anchored on Base")
    if verdict.get("detector_kind") == "text-only" and not by_stamp:
        suffixes.append(" · text match only")

    jid = job_id or verdict.get("job_id") or verdict.get("id") or verdict.get("verdict_id")
    link_part = ""
    if include_link and page_base and jid:
        link_part = f" {page_base.rstrip('/')}/v/{jid}"

    tail = "".join(suffixes) + link_part
    tag = _stream_tag(verdict)

    if by_stamp:
        where = stamp.get("venue") or "the venue"
        when = _fmt_stream_dt(stamp.get("recorded_at_utc"))
        heard = f"Roomstamp: recorded at {where}" + (f", {when}" if when else "")
        cuts = stamp.get("cuts") or []
        if cuts:
            c0 = cuts[0]
            dur_rem = max(0.1, round(float(c0.get("removed_s") or 0.0), 1))
            at_str = _fmt_mmss(float(c0.get("at_clip_s") or 0.0))
            more = f" (+{len(cuts) - 1} more cut{'s' if len(cuts) > 2 else ''})" if len(cuts) > 1 else ""
            return (f"✂️ Cut — {dur_rem:.1f} s removed at {at_str}{more}. {heard}." + tail)[:MAX_TWEET_LEN]
        return (f"✅ Whole take — nothing removed. {heard}." + tail)[:MAX_TWEET_LEN]

    if vkind == "faithful":
        tag_part = f" {tag}" if tag else ""
        base = f"✅ Faithful — every word is in the witnessed record{tag_part}."
        out = base + tail
        return out[:MAX_TWEET_LEN]

    if vkind == "cut":
        if removed:
            r0 = removed[0]
            src_s = r0.get("src_s") or [0.0, 0.0]
            dur_rem = max(0.1, round(float(src_s[1]) - float(src_s[0]), 1))
            at_str = _fmt_mmss(float(r0.get("after_clip_s", 0.0)))
            prefix = f"✂️ Cut — {dur_rem:.1f} s removed at {at_str}. The record says: “…"
            tag_part = f" {tag}" if tag else ""
            closing = f"…”{tag_part}"
            quote = _clean_quote_words(verdict, r0, witness_root=witness_root)

            # Budget characters for the quote so prefix + quote + closing + tail <= 280.
            budget = MAX_TWEET_LEN - len(prefix) - len(closing) - len(tail)
            if budget <= 0:
                short_base = f"✂️ Cut — {dur_rem:.1f} s removed at {at_str}.{tag_part}"
                return (short_base + tail)[:MAX_TWEET_LEN]
            if len(quote) > budget:
                clipped = quote[:budget].rsplit(" ", 1)[0].strip()
                quote = clipped if clipped else quote[:budget].strip()
            return f"{prefix}{quote}{closing}{tail}"

        tag_part = f" {tag}" if tag else ""
        return (f"✂️ Cut — removed audio detected in the witnessed record{tag_part}." + tail)[:MAX_TWEET_LEN]

    if vkind == "spliced":
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
            gap_str = f"{mins} min"
        else:
            secs = max(1, int(round(max_gap_s)))
            gap_str = f"{secs} s"
        base = f"🧩 Spliced — joins {n_parts} parts recorded {gap_str} apart."
        return (base + tail)[:MAX_TWEET_LEN]

    base = "❓ Not in our witnessed record."
    return (base + tail)[:MAX_TWEET_LEN]
