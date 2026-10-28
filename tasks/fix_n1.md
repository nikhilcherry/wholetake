owns: ingest/witness.py ingest/transcribe.py ingest/chain.py ingest/optin.py tests/test_witness.py tests/test_chain.py tests/test_optin.py tests/test_transcribe.py deploy/wholetake-witness@.service deploy/wholetake-transcribe.service deploy/wholetake-chain.service deploy/README.md
context: ingest/witness.py ingest/transcribe.py ingest/chain.py ingest/optin.py tests/test_chain.py tests/test_optin.py tests/test_transcribe.py

# Task: apply the agreed N1 review fixes (from our review debate). Keep every existing behaviour and test
# passing unless a fix below changes it; update tests accordingly. Emit every file you change in full.

F1 witness: within-session skips. Run streamlink with `--loglevel warning` (not --quiet); read its stderr on a
   daemon thread. On a line containing "Sequence gap of", "Download of segment", "Failed to fetch segment" or
   "stream discontinuity": terminate streamlink ONLY, keep reading ffmpeg stdout until EOF (drain, so audio before
   the seam is kept), then end the session with down_reason "streamlink: <the message, trimmed>". The existing path
   then writes the tail chunk, reconnects and emits the gap. Streamlink binary overridable via env WT_STREAMLINK
   (for tests). Test (tests/test_witness.py): a fake streamlink script (python, written into tmp_path) that writes
   ~6 s of raw MPEG-TS/wav audio to stdout (generate with ffmpeg into a file first, then cat it in two halves), prints
   "[stream.segmented][warning] Sequence gap of 3 segments at position 7. ..." to stderr between the halves, and
   continues. Expect: chunk(s) before, a gap whose reason starts with "streamlink: Sequence gap", chunk(s) after.
F2 witness: exclusive non-blocking fcntl.flock on <dir>/witness.lock in __init__; a second witness on the same
   --name exits with a clear message and exit code 1. Test it.
F3 transcribe: exclusive flock on <root>/transcribe.lock (one worker per root). Replace the permanent `bad` set by
   retries: a failing chunk is retried after 30 s, 2 min, 10 min, 30 min, then given up for this run with one loud
   line. Use time.monotonic() for retry timing and for the stale-chunk eligibility age (compare file mtime age via
   time.time() is fine there, but don't mix clocks within one computation). Test the retry schedule with a
   fake transcribe_fn that fails twice then succeeds (inject the clock so the test is instant).
F4 optin key: KEY_FILE = env WT_CLAIM_KEY_FILE or <repo root>/data/claim.key (repo root = parents[1] of
   ingest/optin.py), never CWD-relative. Only `claim` may create the key, atomically (write tmp, fsync, os.link to
   KEY_FILE, catch FileExistsError, unlink tmp) so no reader ever sees an empty key. `check` with no key raises
   a clear error. In witness, a missing key is fatal (exit 1), never treated as "revoked".
F5 optin: channel_id must be a YouTube channel id (startswith "UC", len 24); never fall back to d["id"]; raise
   otherwise. yt-dlp timeout 30 s. Witness wraps consent() in a threading.Lock.
F6 chain:
   a) chain opt-in checks: the daemon reads <dir>/optin.jsonl incrementally and appends kind "optin" entries:
      {line: <0-based line index>, line_sha256, ok, checked_utc}. verify recomputes each from the file (a line edited,
      removed or reordered = problem). Optin entries are independent of seq ordering.
   b) proof(dir, seq, head_n=None) must include the transcript entry for seq: if head_n is given and the transcript
      for seq is not at n <= head_n, raise ValueError("transcript for seq X not chained under head n=Y"); if
      head_n is None use the current head and require the transcript exists. check_proof requires, inside the
      entries, a transcript entry with that seq whose chunk_n equals the first entry's n.
   c) verify: both <seq>.meta.json and <seq>.gap.json existing for a chained seq = problem. Chunks without a
      chained transcript are WARNINGS, not problems: verify returns (ok, problems, warnings); --verify prints
      "WARN:" lines and still exits 0 if no problems. Update all callers/tests.
   d) quarantine: `python -m ingest.chain --quarantine DIR SEQ`. Allowed only if SEQ is exactly the next unchained
      seq and its chunk fails validation (missing wav, unreadable meta, sha mismatch). Moves <seq>.wav/.meta.json
      into DIR/quarantine/, writes <seq>.gap.json {seq, kind: gap, stream, reason: "corrupt on disk before
      chaining (quarantined)", from_utc/to_utc from the meta's start/end_utc_est when readable else null,
      duration_s or null}. Refuse otherwise. The daemon, when halted on a validation failure, prints the exact
      quarantine command once. Test it.
F7 witness misc: (a) replace the SIGTERM handler's sys.exit with a stop flag (threading.Event) checked in the select
   loop and used for the backoff wait (stop_evt.wait); SIGINT same. (b) time.monotonic() for STALL_S, OPTIN_EVERY_S,
   OPTIN_GRACE_S math; time.time() only for the utc timestamps written to disk. (c) write_atomic in all three
   modules fsyncs the tmp file before os.replace; witness also fsyncs the wav before writing meta. (d) when a tail
   < MIN_TAIL_S is dropped, down_since = last - tail_duration. (e) resume glob only [0-9]*.meta.json and
   [0-9]*.gap.json; parse seq with name.split(".")[0]. (f) close ffmpeg's stdout explicitly at session end.
   (g) exit codes: 0 = clean stop/--secs, 3 = not opted in / revoked, 1 = fatal error.
F8 deploy/: systemd --user units. wholetake-witness@.service (instance %i = stream name; URL from
   EnvironmentFile=%h/Projects/wholetake/deploy/streams/%i.env with WT_URL=...), wholetake-transcribe.service,
   wholetake-chain.service. WorkingDirectory=%h/Projects/wholetake, ExecStart via the repo's ./py (it sets the
   CUDA libs) or .venv/bin/python, Restart=on-failure, RestartSec=10, RestartPreventExitStatus=3. deploy/README.md:
   install/enable/status/logs commands, how to add a stream, how to quarantine. Keep it short.

Don't change: chunk/gap/transcript/chain entry formats beyond what's listed, the midpoint seam rule, the mel spec.
