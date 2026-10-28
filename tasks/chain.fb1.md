Review round 1: good structure, 12/12 of your tests pass. Fix these four, emit both files in full again.

1. **Proofs must target an anchored head, not only the current head.** The anchorer pins head.json every
   minute, so by the time someone checks a clip the log has grown past the anchored hash and
   `check_proof(proof(dir, seq), anchored_hash)` is always False. Change to
   `proof(dir, seq, head_n: int | None = None)`: entries from the chunk entry up to and including entry
   `head_n` (default: the current last entry); raise ValueError if head_n < the chunk's n or beyond the log.
   Test: append more entries after taking an "anchored" head, build the proof with that head_n, it checks
   against the old hash and fails against the new one.

2. **Bind the transcript to the audio it claims to transcribe.** Neither the appender nor verify checks
   that the transcript record's `wav_sha256` equals the chained chunk's `wav_sha256`. A transcript of
   different audio would be chained happily. Add the check in both places (appender: halt the stream like
   the other mismatches; verify: a problem naming n and seq). Test it.

3. **One broken stream must not crash the daemon.** `_repair_and_load` raises on a corrupt *middle* line
   (correct: don't silently truncate history), but `ChainDaemon.step` only catches BlockingIOError, so a
   single bad stream kills chaining for every stream. Catch any exception when creating an appender, log it
   once (don't re-log every second; remember failed dirs), and keep going. Same for an exception escaping
   `StreamAppender.step`: mark that stream halted, log, continue. Test: two streams, one with a corrupt
   middle line, the daemon still chains the healthy one.

4. Small: in `step`, `self._ensure_genesis(); if not self.entries: appended += 1` is dead code (entries is
   never empty after ensure), so the genesis entry isn't counted. Count by `len(self.entries)` before/after
   instead, in both the chunk and gap branches.
