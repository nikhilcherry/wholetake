owns: verdict/page.py simx/ui.html
context: verdict/page.py simx/ui.html

# Task: design argument about the two demo surfaces. Round 1: write NO files.

Wholetake gets demoed at HH Goa on Saturday 31 Oct in a 3-minute demo-day slot, on a projector, judged
by builders. The demo goes like this: someone posts a clipped video on our simulated X (simx/ui.html), we
reply "@wholetake is this real?", the bot answers with a verdict link, and we open the evidence page
(verdict/page.py). Those two screens ARE the demo. Right now they look like a working prototype and not
like a product. I want them to look a lot better by tomorrow, without touching any other module.

## What is on screen now (I took screenshots; described here, you can't see them)

Verdict page (/v/<id>, 760 px column on #f8f7f4 beige, a serif headline):
- A small grey caps label "WHOLETAKE EVIDENCE RECORD", then two pills at the top right: grey "cut" and red
  "text match only".
- The H1 is the whole `headline` string in a ~32 px serif: `0.9 s removed at 0:07: the record says "… prices,
  gas prices are through the roof, my rent is up, …" (text match only)`. It wraps to three lines.
- Card "CLIP & ALIGNMENT TIMELINE": a native <audio> player, then ONE solid blue bar for the whole clip with
  a thin red tick at 0:07, then "Duration: 13.1s · Stream: aje" in monospace.
- Card "WHAT THE RECORD SAYS": a grey quote block with a red left border, "Cut at 0:07 (0.9 s removed · chunk #4)",
  then the record's words with "are through the roof, my" on a pink highlight.
- Card "AI NOTE (ADVISORY)": "minor: Omitting 'are through the roof, my' removes emphasis on 'gas prices'."
- Card "PROOF": a table: Chunk #4 | 2026-10-28T18:54:11.829Z | anchoring pending | n=4 / – | verified.
- Footer: "verdict_id: 57a363c84fdd4c54…" and "Download verdict.json".

simx (an X clone, dark, 3 columns): left nav (X logo, Home, Light mode), centre feed, right column with
"Who's checking clips" (the bot account card) and "Demo flow" (3 numbered steps). The bot's reply is
"✂️ Cut — 0.9 s removed at 0:07. The record says: “…prices, gas prices are through the roof, my rent is up,…”
(aje · 28 Oct 18:54 UTC) · text match only http://127.0.0.1:8790/v/402ce33f5557a46e" as a raw link.
The clipped post has a video with a "BREAKING / Al Jazeera live" poster frame.

A real verdict payload (the page reads `result`; the job wrapper also has status queued/running/error):

```json
{"clip":{"duration_s":13.056,"sha256":"8436…","source_url":"http://127.0.0.1:8791/media/7_….mp4"},
 "coverage":{"complete":true,"gaps":[],"last_chained_seq":6},
 "detection":{"detector":{"name":"ref-text-only"},"stamp":null,
   "witness":{"stream":"aje","verdict":"cut",
     "pieces":[{"clip_s":[0.0,6.96],"seq":4,"src_s":[1.74,8.98],"audio_score":null},
               {"clip_s":[6.96,12.96],"seq":4,"src_s":[9.9,16.12],"audio_score":null}],
     "removed":[{"after_clip_s":6.96,"seq":4,"src_s":[8.98,9.9],"words":"are through the roof, my"}],
     "inserted":[]}},
 "detector_kind":"text-only",
 "headline":"0.9 s removed at 0:07: the record says \"… prices, gas prices are through the roof, my rent is up, …\" (text match only)",
 "label":{"model":"gemini-3.5-flash","spans":[{"effect":"minor","removed_words":"are through the roof, my",
   "why":"Omitting 'are through the roof, my' removes emphasis on 'gas prices'."}]},
 "proofs":[{"seq":4,"chunk_received_utc":"2026-10-28T18:54:11.829Z","proof_ok":true,"anchor":{"status":"pending"}}],
 "verdict_id":"57a363c8…"}
```
Other verdicts the page must handle: faithful, spliced (`inserted` non-empty), not_in_record, and later a
Roomstamp `stamp` object (`present`, `recorded_at_utc`).

## My position. Attack it.

1. The verdict page should open on a **verdict, not a sentence**: a huge stamped word (CUT / FAITHFUL /
   SPLICED / NOT IN THE RECORD) in a verdict colour, then one big plain-English line ("0.9 seconds were
   removed at 0:07"), then the stream and time ("Al Jazeera English live · 28 Oct, 18:54 UTC"). The
   `headline` string stays as <title> and og:description only.
2. The hero visual should be **two tracks**: the RECORD on top (continuous) and the CLIP below it, with each
   piece drawn as a band joined to its source span, so the removed span shows up as a visible hole in the
   clip track that the bands jump over. That one picture explains the product in two seconds. The
   transcript under it should run inline, with the removed words struck through in the verdict colour.
3. Make it **dark**, to match the X clone and the projector: near-black, one accent per verdict, a grotesk
   for UI text and a mono for hashes. No serif.
4. "text match only" in red reads as an ERROR to a judge. It's a caveat, so demote it to a neutral chip with
   a tooltip.
5. Replace the PROOF table with a **chain-of-custody strip**: Witnessed 18:54:11 → Hash-chained (n=4) →
   Anchored on Base (tx link, or a pulsing "pending") → OpenTimestamps. Each step is a check that
   lights up.
6. simx: the bot reply should unfurl into an **X-style link card** (verdict word + one line + the mini
   two-track picture) instead of a raw URL. simx must not fetch anything external, so the card is built from a
   simx endpoint that reads the verdict job JSON from the verdict server, or from data/verdicts directly.
7. Fonts: system stacks only. No webfonts, because there's no network at the venue.

## Hard constraints (don't argue these)
- verdict/page.py stays self-contained: no external requests, no JS frameworks. Small inline JS is OK.
  Every user string is escaped (tests check XSS). The queued page keeps `http-equiv="refresh" content="2"`.
  The page must still contain the literal strings "text match only" and "AI note (advisory)",
  the basescan tx link, and "Who's checking clips" in simx. simx/ui.html must contain no "http://" or
  "https://" or protocol-relative src/href.
- The public function signatures in page.py stay the same.

## What I want from you this round
A design critique, in NOTES (ignore its 15-line limit):
(a) Where each of my 7 points is wrong, weak, or will look worse on a projector. Be blunt, and say what
    you would do instead.
(b) What I missed: the top 5 changes ranked by demo impact, including anything about motion (e.g. the
    reply arriving, the hole opening).
(c) A concrete spec: palette hex values per verdict, type scale, layout grid at 1280×720, and the exact
    structure of the two-track SVG. Write it tight enough that whoever implements it can't drift.
Don't write any files yet.
