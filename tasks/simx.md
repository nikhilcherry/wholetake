owns: simx/__init__.py simx/__main__.py simx/store.py simx/api.py simx/server.py simx/ui.html tests/test_simx.py deploy/wholetake-simx.service
context: tasks/_xapi_contract.md ingest/witness.py

# Task: simx — a simulated X (Twitter) for the Wholetake demo

Wholetake's demo surface is an X reply bot: someone posts a clipped video, a person replies "@wholetake is this
real?", the bot replies with a verdict. We demo on a SIMULATED X so we control it fully; the bot talks to it through
the API contract below, so switching to real X later is only a base URL + token change. Build the simulated X.

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


## Server (`python -m simx serve [--port 8791] [--data data/simx]`, stdlib ThreadingHTTPServer, bind 127.0.0.1)
- `store.py`: users, tweets, media in memory, persisted atomically to <data>/state.json after each write
  (tmp + fsync + os.replace); media files in <data>/media/<media_key>.mp4. Thread-safe (one lock).
- `api.py`: the contract endpoints exactly (pure functions over the store + a thin HTTP layer), incl. expansions,
  since_id, max_results, the 280-char rule, 429 rate limit with headers. Media served at GET /media/<key>.mp4 with
  HTTP Range support (video players and ffmpeg seek).
- Seed on first start: users nikhil (Nikhil AV), shreyas (Shreyas R), clipper (ClipFarm 🔥), newsdesk (India Newsdesk),
  wholetake (Wholetake, automated=true, bio "Automated · checks clips against witnessed livestreams · operated by
  @nikhil · reply 'stop' to opt out"). `python -m simx seed-clip FILE --as clipper --text "..."` uploads a video and
  posts it (prints the tweet id) — for demo prep.
- UI-only JSON under /sim/ (not part of the contract): GET /sim/timeline, GET /sim/thread/<id>, GET /sim/users,
  POST /sim/post (as the user picked in the UI; multipart or raw upload for a video). No auth beyond picking a user
  (it's a local demo); still escape everything rendered.

## UI (`simx/ui.html`, served at /, single self-contained file, vanilla JS, no external requests at all)
It will be on a projector at a hackathon demo, so it must look like a credible X clone, not a debug page:
- Three-column X layout on desktop (nav / feed / right rail with "Who's checking clips"), single column under 700 px.
  Dark theme default (X "lights out" palette: #000 bg, #16181c cards, #2f3336 borders, #e7e9ea text, #71767b muted,
  #1d9bf0 accent), light theme toggle. System font stack.
- Top: "Posting as" switcher (avatar initials + name). Composer with text, character counter (280), video attach
  with preview. Feed: posts with avatar, name, @handle, relative time, text with @mentions/#tags linkified, inline
  <video> player, reply/quote counts, Reply and Quote buttons opening an inline composer prefilled with the mention.
  Automated accounts show X's grey "Automated" label under the name.
- /status/<id> view: the post, then its replies thread, newest bot reply highlighted briefly when it appears.
- Live: poll /sim/timeline every 2 s; new posts slide in without losing scroll or an open composer.
- Bot replies get no special formatting from the UI — they are plain text like on real X.

## Tests (tests/test_simx.py, run the server in-process on port 0)
Contract conformance: create/reply/quote, 280 limit, mentions only for the mentioned user and not own posts,
since_id paging + newest_id/oldest_id + empty response has no "data", expansions return referenced tweets and their
media with variants.url that actually downloads the uploaded bytes, Range request returns 206 with the right slice,
401 on bad token, 429 after the limit, persistence across a restart. UI: GET / returns HTML that references no
external URLs (assert no "http://" or "https://" except inside comments/docs strings you control).

deploy/wholetake-simx.service like the others. Style: match ingest/witness.py. No placeholders.
