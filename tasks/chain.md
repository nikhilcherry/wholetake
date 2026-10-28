owns: ingest/chain.py tests/test_chain.py
context: ingest/witness.py ingest/transcribe.py

# Task: the live hash chain (ingest part 3)

`ingest/witness.py` writes per stream into `data/witness/<name>/`: `<seq>.wav` + `<seq>.meta.json`
(meta last, atomic) or `<seq>.gap.json`. Every seq is exactly one chunk or one gap, contiguous from 0.
`ingest/transcribe.py` later writes `<seq>.json` + `<seq>.mel.npy` for chunks (out of order across
streams, and possibly minutes later). Both are shown below.

Write `ingest/chain.py`: an append-only, per-stream, tamper-evident log of everything the witness
holds, appended live, whose head another component (the anchorer, not this task) pins on-chain every
minute. Run as `python -m ingest.chain [--root data/witness]` (daemon, polls every 1 s) or
`python -m ingest.chain --verify data/witness/<name>` (exit 0 = OK, 1 = BROKEN, prints why).

## Log format: `<stream dir>/chain.jsonl`, one JSON object per line, sorted keys, compact separators
Every entry has `n` (0,1,2... position in the log), `prev` (previous entry's `hash`, 64 zeros for n=0),
`kind`, and `hash` = sha256 hex of the canonical JSON of the entry *without* the `hash` field
(`json.dumps(e, sort_keys=True, separators=(",", ":"))`). Kinds:
- `chunk`: `seq`, `wav_sha256` (recomputed from the file, must equal meta's), `meta_sha256` (of the meta
  file bytes: this pins received_utc), `received_utc`, `duration_s`.
- `gap`: `seq`, `gap_sha256` (file bytes), `from_utc`, `to_utc`.
- `transcript`: `seq` (must refer to an earlier `chunk` entry in this log), `json_sha256`, `mel_sha256`
  (both recomputed from files; mel must equal the record's `mel_sha256`), `chunk_n` (n of that chunk entry).
- `chain` entries: n=0 is a `genesis` entry: `stream`, `source` (from the first meta, or null), `created_utc`.

## Ordering rules (the verifier enforces all of them)
- `chunk`/`gap` entries appear in strictly increasing seq order with **no skipped seq**. The daemon must
  never append seq k+1 before k; if k is not on disk yet it waits.
- A `transcript` for seq k appears only after the `chunk` entry for k, at most once per seq.
- Files are hashed from disk at append time. Never append a chunk whose wav sha256 ≠ meta sha256
  (log loudly, stop appending for that stream: something is wrong with the record, don't paper over it).

## Head: `<stream dir>/head.json` (atomic tmp + os.replace after every append)
`{"stream", "n", "hash", "kinds": {"chunk": c, "gap": g, "transcript": t}, "last_seq", "updated_utc"}`
The anchorer reads only this file.

## Durability / restart
- Append = write one line + flush + os.fsync. On start, load the existing log; if the last line is a
  partial write (no trailing newline or bad JSON) truncate it away and log that. Rebuild in-memory state
  (last seq chained, which transcripts are chained) from the log, then resume. Restart must never
  duplicate or reorder entries.
- One process owns a stream's chain: take an exclusive non-blocking `fcntl.flock` on `<dir>/chain.lock`
  and refuse to start for that stream if held.

## Library API (other components import these; keep them pure and documented)
- `entry_hash(e: dict) -> str`
- `load(dir) -> list[dict]`
- `verify(dir, check_files=True) -> (ok: bool, problems: list[str])`: recompute every hash and link,
  the ordering rules, and (if check_files) every referenced file's sha256 against disk. A missing or
  edited file, an edited/removed/reordered line, a skipped seq: each is a problem naming n and seq.
- `proof(dir, seq) -> dict`: the entries from the `chunk` entry for seq up to the current head, so a
  third party holding only an anchored head hash can check the link.
  Also `check_proof(proof, head_hash) -> bool`.

## Tests: `tests/test_chain.py` (pytest, no GPU, build fake stream dirs with real wav bytes in tmp_path)
- Appends chunk/gap/transcript in order; transcript before its chunk is held back; seq 3 arriving before
  seq 2 waits until 2 exists. Verify passes, head.json matches the last line.
- Tamper cases each make verify fail with a message naming the right n/seq: edit one byte of a wav;
  edit received_utc in a meta file; delete a middle log line; swap two lines; change a field in a line
  and recompute only that line's hash; delete a transcript json.
- Restart: a log with a truncated last line is repaired and appending resumes with correct n/prev;
  restarting twice appends nothing new when nothing changed.
- Lock: a second appender for the same stream refuses.
- proof/check_proof: valid for a middle chunk against the current head; fails against a wrong head
  and if any entry in it is altered.
- wav/meta sha mismatch → that stream stops appending; other streams keep going.

Keep it to these two files, stdlib only (plus numpy only in tests if needed). Match the style of the
existing files.
