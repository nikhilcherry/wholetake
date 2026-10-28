owns: bot/__init__.py bot/__main__.py bot/xclient.py bot/reply.py bot/runner.py tests/test_bot.py verdict/page.py verdict/service.py verdict/fetch.py deploy/wholetake-bot.service deploy/README.md
context: tasks/_xapi_contract.md verdict/service.py verdict/fetch.py verdict/compose.py CONTRACTS.md deploy/README.md

# Task: N5 the X reply bot + N4 the verdict page

Wholetake checks clipped videos against witnessed livestreams. `verdict/service.py` (shown) is an HTTP API on
127.0.0.1:8790: POST /api/verdict {"url"} -> {"id","status"}; GET /api/verdict/<id> -> status + result (verdict.json,
see compose.py). The bot lives on X; for the demo it runs against a simulated X that implements the contract below
(another engineer is building that server right now — code only to the contract).

## Simulated X API contract (shared by simx/ and bot/; a strict subset of real X API v2 shapes)
Base URL: env X_API_BASE (simulated: http://127.0.0.1:8791; real: https://api.x.com). Auth header
`Authorization: Bearer <token>`; simulated tokens are "sim-<username>" (seeded), real ones are user-context tokens.
IDs are decimal strings, increasing over time (like snowflakes). Lists are newest first, like X.
- GET  /2/users/me -> {"data": {"id","username","name"}}
- GET  /2/users/by/username/{username} -> {"data": {"id","username","name"}}
- POST /2/tweets  body {"text", "reply"?: {"in_reply_to_tweet_id"}, "quote_tweet_id"?, "media"?: {"media_ids": [..]}}
       -> 201 {"data": {"id","text"}}. text <= 280 chars or 400 {"title":"Invalid Request","detail":...}.
- GET  /2/tweets/{id}?expansions=...&media.fields=... -> {"data": tweet, "includes": {...}}
- GET  /2/users/{id}/mentions?since_id=&max_results=(5..100, default 10)&expansions=author_id,referenced_tweets.id,
       attachments.media_keys,referenced_tweets.id.attachments.media_keys&media.fields=type,duration_ms,variants
       -> {"data": [tweet...], "includes": {"users": [...], "tweets": [...], "media": [...]},
           "meta": {"result_count", "newest_id", "oldest_id"}}  (no "data" key when empty, like X)
       A mention = a tweet by someone else whose text contains "@<username>" (case-insensitive).
- tweet = {"id","text","author_id","created_at" (ISO-8601 Z),"conversation_id",
           "referenced_tweets"?: [{"type": "replied_to"|"quoted", "id"}], "attachments"?: {"media_keys": [..]}}
- media = {"media_key","type":"video","duration_ms","variants":[{"content_type":"video/mp4","bit_rate","url"}]}
- POST /2/media/upload?media_category=tweet_video  raw body (video/mp4) -> {"data": {"id","media_key"}}  (simulated only;
       real X uses chunked upload, the bot never uploads)
Errors: X-style {"title","detail","status"} with 401 (bad token), 404, 400, 429 (simulated rate limit: 300 req / 15 min / token,
headers x-rate-limit-limit / -remaining / -reset).


## Bot (`python -m bot run`), package bot/
- `xclient.py`: tiny client for exactly the contract endpoints (urllib, timeouts 15 s, X-style error -> XError with
  status; on 429 sleep until x-rate-limit-reset). Base URL env X_API_BASE, token env X_BOT_TOKEN.
- `runner.py`: poll mentions of the bot account every BOT_POLL_S (default 5; real X bills reads, so keep since_id
  and never re-read). For each new mention, in id order:
  1. Opt-out: if the mention text (minus mentions) is exactly "stop"/"unsubscribe" (case-insens.), add the author to
     the opt-out list, reply once "Got it — I won't reply to you again." and stop. Never reply to opted-out authors.
  2. Find the clip: the first video among (a) the mention's own attachments, (b) the tweet it replies to, (c) the
     tweet it quotes — via expansions. No video -> reply "I check videos: reply to or quote a post with a video and
     tag me." (once).
  3. Pick the variant: mp4, highest bit_rate. POST it to the verdict API, poll until done/error (timeout 10 min).
  4. Reply ONCE, in_reply_to the MENTION tweet itself (X's 2026 rule: replies only to the post whose author summoned
     the bot), with the reply text from reply.py. On verdict error reply "Couldn't check this clip: <short reason>".
  Idempotency: state in data/bot/state.json (since_id, handled mention ids -> reply id, opt-outs), written atomically
  BEFORE posting is marked done, so a crash between verdict and reply can't double-reply (record "posting" first; on
  restart a "posting" entry is checked against the thread via GET /2/tweets? — simplest correct approach: before
  replying, re-fetch nothing; instead persist {"state":"posting"} then post then {"state":"done","reply_id"}; on
  restart, entries left in "posting" are NOT re-posted, they're logged for a human). Several mentions of one clip
  -> one verdict computation (verdict API caches by clip), separate replies.
  Never DM, like, follow, or mention anyone except replying to the summoner. Max 1 reply per mention.
- `reply.py`: `reply_text(verdict, include_link: bool, page_base: str) -> str`, <= 280 chars always (truncate the
  record quote first, never the verdict word or the cut size). No URL unless include_link (env BOT_INCLUDE_LINK=1,
  default 0 on real X where any URL costs $0.20/reply; the demo turns it on). Shapes, from verdict.json:
  faithful: "✅ Faithful — every word is in the witnessed record (aje · 28 Oct 18:54 UTC)."
  cut: "✂️ Cut — 0.9 s removed at 0:07. The record says: “…gas prices are through the roof, my rent is up…” (aje · 28 Oct 18:54 UTC)"
  spliced: "🧩 Spliced — joins 2 parts recorded 14 min apart." ; not_found: "❓ Not in our witnessed record."
  Append " · anchored on Base" when every proof is anchored, " · text match only" for the text-only detector,
  and the link " <page_base>/v/<job id>" when enabled. No "misleading", no accusations: quote, don't judge.
  Never include the Gemini label text in the reply (it's advisory; the page shows it).

## Verdict page (N4): `verdict/page.py` + routes in verdict/service.py
- GET /v/<job id> -> self-contained HTML (no external requests), GET /v/<job id>/clip.wav serves the fetched clip.
- Content, top to bottom: the deterministic headline, large; the clip player; a timeline bar of the clip with
  matched pieces in one colour and each removed span as a marked seam, hover/tap shows the removed record words;
  "What the record says" with the removed words highlighted inside their record sentence (from the witness
  transcript); coverage notes (gaps) if any; "AI note (advisory)" with Gemini's effect+why, visually secondary;
  "Proof": per chunk — received time, anchored tx with a basescan link or "anchoring pending", chain head n/hash
  (short), proof_ok; the detector kind ("text match only" badge for the reference detector); a "Download
  verdict.json" link (GET /v/<id>/verdict.json). A queued/running job shows status and auto-refreshes every 2 s.
- Design: calm and serious (it's evidence), light theme default + dark via prefers-color-scheme, tokens on :root,
  readable on a phone, escape everything.
- fetch.py: env WT_FETCH_ALLOW_HOSTS (comma-separated host:port) exempts exact host:port pairs from the private-IP
  SSRF block — needed because the simulated X serves media from 127.0.0.1:8791. Everything else stays blocked.

## Tests (tests/test_bot.py; no network: run a tiny fake X server in-process that follows the contract, and a fake
verdict API) — mention with replied_to video -> exactly one reply in_reply_to the mention with the right text;
quoted video; own attachment; no video -> help reply; opt-out flow; restart doesn't double-reply (incl. the
"posting" crash case); since_id advances; reply_text <= 280 for long quotes and keeps verdict word + cut size;
link only when enabled; 429 handling; page renders for done/queued/error jobs with everything escaped
(a transcript containing <script> stays inert); fetch allowlist exempts only the listed host:port.

deploy/wholetake-bot.service; README: how to run the demo stack (simx, verdict, bot) with the env vars.
Style: match the repo. No placeholders.
