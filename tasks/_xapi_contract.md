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
