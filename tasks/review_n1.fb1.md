Claude's rulings on your 13, with evidence. Then my own list for you to attack. Argue back where I'm wrong.

ACCEPTED
- #1 REAL, and the worst finding. Verified in streamlink 8.6.2 source: segmented.py check_sequence_gap logs
  "Sequence gap of N segments ... incoherent output data" and hls.py logs "Download of segment N failed" /
  "Encountered a stream discontinuity". We run streamlink with --quiet, so every within-session skip is
  silently glued into one chunk. My proposed fix differs from yours: don't infer from wall-clock drift (a
  delayed-then-caught-up stream looks the same as a skip). Run streamlink with --loglevel warning, read its
  stderr on a thread, and on any of those three messages end the session (existing path: tail chunk, then
  down_since=last, reconnect, gap with reason "streamlink: <message>"). Seam accuracy is limited by pipe
  buffering (<1 s); the gap sits at the seam within that. Challenge this if you see a hole.
- #6 (= my #1) REAL, reproduced: two witnesses on one --name both wrote seq 0,1,2 and overwrote each other.
- #9 REAL: transient CUDA OOM blacklists a chunk until restart. Retry with backoff, give up after N.
- #12 REAL, and worse than you said: KEY_FILE is "data/claim.key" relative to the CWD. Start the witness from
  any other directory and it silently creates a NEW key, every code mismatches, ok=False, the witness exits as
  "revoked". Fix: path relative to the repo, and only `claim` may create the key; check/witness must fail loud
  if it's missing.
- #4 partly: proof() must include the transcript entry for seq (the matcher uses json + mel, not just wav),
  so proof picks head_n >= that transcript's n and check_proof requires it. Opt-in checks must be chained (= my #3).
  REJECTED part: head.json is a derived convenience file; the anchored hash is the truth. Missing transcripts are
  a coverage report, not an integrity failure: --verify should print them as warnings, not BROKEN.
- #5 accepted as medium: replace sys.exit-in-handler with a stop flag checked in the select loop and backoff wait.
- #11, #13, #10 (explicit out.close()) accepted as low, cheap.
- #7 low: one lock around consent(); not a real harm today, but needed once checks get chained.
- #8.3 accepted: yt-dlp timeout 120 -> 30.

REJECTED (with evidence)
- #2: unreachable. Every new session after an outage starts with a gap seq (emit_gap on first block), and a
  restart always emits a "witness offline" gap from the last file. So seq-1 / seq+1 being a chunk means same
  continuous capture. Adding a session check also misfires: `session` restarts at 0 on every process restart.
- #3: you resolved it yourself mid-answer: ".json" sorts before ".meta.json", so done[-1] is the meta/gap.
  The "no files yet, crash" case records nothing and claims nothing: the record starts at genesis.
  I'll still narrow the glob to meta/gap as hardening.
- #8.1 / #8.2: tested live. `optin check https://www.youtube.com/@aljazeeraenglish/live` resolved channel_id
  UCNye-wNBqNL5ZzHSJj3l8Bg (same as `claim` on the @handle URL) and fetched the real bio. Same code both ways.
- #10 "audio_only": the youtube plugin has no audio_only stream for live HLS (grep finds none). Keep "worst".

MY LIST (attack it)
A. No supervisor: any uncaught exception (disk full in emit_chunk) ends witnessing for the night. Plan: systemd
   --user units with Restart=always for the three daemons.
B. Sub-second tail discarded at a drop but the gap starts at `last` (after that tail), so up to 1 s of audio we
   held and threw away isn't inside any gap. Fix: down_since = last - tail_duration when the tail is dropped.
C. No fsync in witness: after a hard power-off a meta can point at a wav with zeroed blocks; chain halts that
   stream forever (every restart hits the same mismatch). Fix: fsync wav, then write+fsync meta.
D. received_utc trusts the laptop clock; anchoring bounds it, but the head entries carry our clock. One NTP
   offset check at witness start, logged into the record.

Reply in NOTES: for each of my rejections, "concede" or a concrete counter-scenario; for A-D, agree/disagree
with reasons; anything both of us missed. Write NO files.
