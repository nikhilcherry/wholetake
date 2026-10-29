owns:
context: CONTRACTS.md verdict/compose.py

# Task: feature debate. Round 1: write NO files.

State on Thu 29 Oct, 09:00 IST: the hack ends Sat 31 Oct at 14:00, and the 3-minute demo is on Saturday. Builders judge. Day 2 is
"triangle day" (problem/solution/market).
Done and tested (63 tests): N1 witness ingest (16 kHz 20 s chunks, wav + word-timestamped transcript + log-mel per chunk, a
hash chain, explicit GAP entries, opt-in by an HMAC code in the channel bio); N2 anchoring (EAS multiTimestamp on Base Sepolia
every minute, OpenTimestamps hourly); N3 verdict service (clip URL → fetch → detector → Gemini labels the removed spans
→ verdict.json with proofs); N4 the evidence page (/v/<id>: stamp, two-track SVG, "Hear the cut", custody strip); N5 the
@wholetake bot on a simulated X, whose replies unfurl into a verdict card.
Not mine: S5, the teammate's acoustic detector + Roomstamp (a keyed audio watermark the venue PA plays, which gives
where/when/how-many-seconds-cut from the sound alone). It's being built in parallel and plugs in via CONTRACTS.md.
Until it lands, the detector is text-only.

Event context: the organisers themselves floated a "gate-lock" check that work was made onsite. The X Community Notes
"media notes" attach to every repost of the same video. An official event account (@247pmstudio) posts clips of talks
on X. An Edge City speaker and a TigerGraph speaker are onsite. Nobody can leave the venue (we're under 21), so the venue
is the whole lab. All code must be written at the event.

## My candidate features (2–6 hours each). Attack them, kill some, add better ones.

F1 "Hear what they cut": the evidence page plays the clip up to the seam, then the RECORD's own audio for the removed
   span (sliced from the witness chunk wav), tinted red with the words karaoke-highlighted, then the rest of the
   clip. You literally hear the missing 0.9 s. It's server-side slicing from data/witness/<stream>/<seq>.wav, served
   with Range.
F2 Context collapse: show the record ±30 s around the clip ("Before the clip" / "After the clip") so out-of-context
   trims at the edges show up too, not only internal cuts. Possibly a new verdict flavour "trimmed: the clip starts 4 s
   after 'they claim that'".
F3 A "Draft a Community Note" button: generates a neutral, sourced media-note text (≤280 chars + link) from the
   verdict, ready to paste. A pitch bridge to a real distribution channel.
F4 /streams, a public witness ledger: every witnessed stream with uptime, its chain head, the last anchor tx, and
   gaps. It proves we were watching BEFORE the clip existed.
F5 Verify it yourself, in the browser: a button on the page re-hashes the chunk wav + transcript with WebCrypto, walks
   the hash chain to the anchored head, and shows each step green. "Don't trust us" without installing anything.
   (The on-chain read of the EAS tx needs RPC, so that's an optional link-out.)
F6 og:image: a server-rendered SVG/PNG of the stamp + two-track for real X/WhatsApp unfurls (PNG needs a rasterizer,
   which is a dependency question).

## What I want
(a) Rank F1–F6 by (demo wow per hour) × (risk it breaks on stage). Be blunt about which ones are cute but don't win.
(b) At least 3 features I didn't think of that fit THIS event and THESE judges. Each with a "wait, how?" moment.
    Ideas that just say "add receipts/verification for X" are banned.
(c) The two we should build today, with the smallest correct design for each: which files, endpoints and data they
    touch, and how each one fails safely if a piece is missing (e.g. the witness wav got pruned).
Put it all in NOTES (ignore the 15-line limit).
