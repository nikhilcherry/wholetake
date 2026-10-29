Rulings on your critique. You won most of it. Where I'm pushing back, I give evidence. After the rulings, IMPLEMENT: emit verdict/page.py and simx/ui.html in full.

ACCEPTED
- #2, and you're right that I had the physics backwards. The hole goes in the RECORD track (a red excised block) and the
  clip gets a thin seam, with ribbons pinching across. For spliced it's the inverse. Take your SVG structure as written,
  including the dual playhead that leaps the excised block, the "▶ Hear the cut" button (seek to after_clip_s − 2.5), and
  the 10 px minimum width (the label still shows the true duration).
- #3: navy, not black, so the evidence page reads as a different, authoritative place. Use your palette.
- #4 and #7 as you wrote them, plus tabular-nums.
- #5: no OpenTimestamps step (proofs don't carry it). Keep raw proofs in <details>. The anchor shape per proof is
  {status, tx, head_seq, head_hash} and the link is BASESCAN_TX_PREFIX + tx.
- #6: parse the bot's own tweet text in simx/ui.html. It's our format, so the parse is deterministic. The exact
  formats from bot/reply.py are pasted below. A tweet that has a /v/<id> link from @wholetake becomes a card.
  If the parse fails, it still becomes a card with just the verdict word, so never a raw URL. The card also needs
  light mode: simx has a Light mode toggle, so use its existing CSS vars.
- Queued/running page restyled into the same shell. It must keep http-equiv refresh content="2" and the word "queued".
- Faithful / not_in_record / stamp handling as you described.

REJECTED / CHANGED
- R1 "keep the full headline visible for tests": no test asserts headline-in-page (I grepped tests/). Use it in
  <title> and og:description only. Don't show the same fact twice on a projector.
- R2 "UNWITNESSED": it reads as "nobody witnessed the event", which is a claim about the world. The real meaning is
  that we found no match in OUR record. Label it NO RECORD (two short words, stamp-sized).
- R3 Custody step 3 "Detector Verified" with a green check while the detector is ref-text-only overclaims: it's the
  exact thing a judge pokes at. Name the step by what happened: "Transcript aligned · 2 pieces" when
  detector_kind == "text-only", "Audio + transcript matched" otherwise. Then add a 5th step "Roomstamp" ONLY when
  detection.stamp is present.
- R4 Bot-thinking skeleton with cycling "Hashing → Aligning → Checking Base anchor": those are fake progress steps,
  because simx can't see the job status. The "no mock logic" rule covers UI too. Do it the honest way: one row,
  "@wholetake is checking this clip" with animated dots, shown while the newest post that mentions @wholetake is
  < 60 s old and has no @wholetake reply in its thread. That's like X's own typing indicator.
- R5 Auto-switching the actor when someone clicks Reply is hidden magic, and it bites on stage when you want to reply
  as someone else. Instead, make each Demo-flow step a button that does its step: step 1 switches to @clipper and
  focuses the composer; step 2 switches to @nikhil and opens the reply on the newest video post, prefilled with
  "@wholetake is this real?". Every effect is visible.
- R6 Record-track time domain across chunks. src_s is relative to its chunk's WAV. Every chunk json at
  witness_root/<stream>/<seq:06d>.json has start_utc_est (ISO) and duration_s (20.0). So build absolute record time
  as (start_utc_est(seq) − start_utc_est(first seq)) + src_s, and fall back to seq*duration_s when the file is
  missing. Draw a faint tick + "#seq" label at each chunk boundary inside the shown span. Axis labels on the record
  track are wall-clock (18:54:13 UTC); on the clip track they're clip time (0:00, 0:07, 0:13).
- R7 Stream name map: {"aje": "Al Jazeera English · Live"}. For an unknown slug, show the slug as-is, not .upper().
- R8 Transcript: sans, not Georgia. One family on the page, except mono for hashes.
- R9 Every animation (pulse, glow, dots) goes under @media (prefers-reduced-motion: no-preference).

KEEP (hard constraints, restated): everything self-contained, no external requests, everything escaped, the same
public function signatures, the literal "text match only", "AI note (advisory)", and "Who's checking clips", and
no http:// or https:// or // src/href in simx/ui.html (build the card href as a relative-looking string from the
parsed URL at runtime, never as a literal in the file). Don't break simx's existing JS behaviour (posting, attach,
actor switch, polling, theme toggle).

bot/reply.py reply_text (the formats you parse):
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

    suffixes: list[str] = []
    if _all_anchored(verdict):
        suffixes.append(" · anchored on Base")
    if verdict.get("detector_kind") == "text-only":
        suffixes.append(" · text match only")

    jid = job_id or verdict.get("job_id") or verdict.get("id") or verdict.get("verdict_id")
    link_part = ""
    if include_link and page_base and jid:
        link_part = f" {page_base.rstrip('/')}/v/{jid}"

    tail = "".join(suffixes) + link_part
    tag = _stream_tag(verdict)

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
