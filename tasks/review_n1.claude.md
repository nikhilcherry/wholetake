# Claude's independent N1 review (written before reading Argon's)

1. [high] No single-instance guard on witness or transcriber. Two `witness --name aje` (easy to do at 3 AM
   in two terminals) both start at the same seq and overwrite each other's chunks; two transcribers race on
   the same chunk, Whisper is not bit-deterministic, so the chain can hash one json and the other one lands
   on disk afterwards -> verify BROKEN for an honest record. Fix: flock per stream dir in witness, per root in transcriber.
2. [high] Nothing restarts a daemon that dies. Any uncaught exception (disk full in emit_chunk, a yt-dlp JSON
   surprise) ends witnessing silently for the rest of the night. Fix: systemd --user units, Restart=always.
3. [medium-high] Consent trail is not tamper-evident. optin.jsonl sits beside the chain but is not in it, so
   "this channel had opted in when we recorded" can be rewritten later. The genesis entry has the URL but not
   the channel_id. Fix: chain each opt-in check as its own entry kind.
4. [medium] Sub-second audio is discarded without a GAP covering it. At a drop, a tail < MIN_TAIL_S is thrown
   away but down_since = `last` (arrival of that tail), so the gap starts *after* audio we threw away.
   Small, but "no unacknowledged gaps" is our headline claim. Fix: start the gap at last - tail duration.
5. [medium] No fsync in witness writes. A hard power-off (laptop on battery at a venue) can leave a meta whose
   wav has zeroed blocks; chain then halts that stream permanently, and on every restart again. Fix: fsync wav
   before writing meta, fsync meta. Plus: a halted stream needs a visible alarm, not a stderr line.
6. [low] Opt-in assumes YouTube (bio fetched from youtube.com/channel/<id>); any other platform never starts.
   Fine for the event, should be an explicit error, not "unreachable" forever.
7. [low] `transcribe --once` while the witness is live finalises the newest chunk without next-context.
8. [low] received_utc trusts the laptop clock. Anchoring bounds it; worth one sanity check vs NTP at start.
