owns: ingest/transcribe.py tests/test_transcribe.py
context: ingest/witness.py

# Task: the transcriber worker (ingest part 2)

`ingest/witness.py` (shown below) writes, per stream, into `data/witness/<name>/`:
`000000.wav` (16 kHz mono s16) + `000000.meta.json` (meta written last, atomically), or `000007.gap.json`.
Every seq number is exactly one chunk or one gap. Read its docstring and meta fields.

Write `ingest/transcribe.py`: a long-running worker that turns each chunk into a transcript record
that the clip matcher (a teammate's code) reads. Run as `python -m ingest.transcribe [--root data/witness] [--once]`.

## Output per chunk: `<seq>.json` (written atomically via tmp + os.replace, like witness.py)
```json
{"seq": 12, "stream": "aje", "kind": "transcript",
 "wav_sha256": "<copied from meta>", "received_utc": "...", "start_utc_est": "...", "duration_s": 60.0,
 "words": [{"w": "hello", "s": 0.52, "e": 0.81, "p": 0.97}],
 "text": "hello ...",
 "mel": "000012.mel.npy", "mel_sha256": "...",
 "model": "large-v3-turbo", "context": {"prev_s": 2.0, "next_s": 2.0}}
```
- `s`/`e` are seconds **relative to the start of this chunk's wav**, rounded to 0.01. Words may have
  `s < 0` or `e > duration_s` only if they straddle a boundary (see below); clamp nothing else.
- `<seq>.mel.npy`: float16 log-mel, shape (64, frames), 25 ms window / 10 ms hop over this chunk's own
  audio only (no context). The matcher uses it for acoustic confirmation without re-reading the wav.
  Compute it with numpy only (write a tiny STFT + mel filterbank; no librosa/torchaudio dependency).
- Gaps produce no transcript. Note: a chunk adjacent to a gap must not borrow context across the gap.

## The boundary problem (the hard part)
Speech runs straight across chunk boundaries. Transcribing each 60 s chunk alone mangles or duplicates
the word that straddles the cut. Required behaviour:
- Transcribe chunk k with up to 2.0 s of audio from the end of chunk k-1 prepended and up to 2.0 s from
  the start of chunk k+1 appended (only if those neighbours are chunks, same stream, and seq-adjacent).
- Keep a word in chunk k iff its **midpoint** falls inside chunk k's own span [0, duration_s). Shift
  times so 0 = chunk k's first sample. Each spoken word then lands in exactly one chunk.
- That means chunk k waits for chunk k+1. Process chunk k when: k+1 exists as a chunk (meta present), OR
  k+1 is a gap, OR chunk k's meta file is older than 1.5 × its duration_s + 10 s (stream stopped; transcribe
  without next-context and record `"next_s": 0`). `--once` processes everything available now, treating
  any chunk with no successor as final, then exits.

## Worker behaviour
- Watch every stream dir under root, poll every 2 s; process oldest unprocessed eligible chunk first,
  across streams. Skip chunks that already have `<seq>.json`. Never crash on one bad chunk: log it and
  write nothing (it will be retried next start), but don't spin on it in a loop within one run.
- One faster-whisper model, loaded once: `WhisperModel("large-v3-turbo", device="cuda", compute_type="float16")`
  with `word_timestamps=True, vad_filter=True, language=` from `--lang` (default "en"; "auto" = None).
  `--device cpu` / `--model` flags for machines without the GPU (cpu uses compute_type int8).
- Verify the wav's sha256 equals meta["sha256"] before transcribing; on mismatch log loudly and skip
  (the record was tampered with or half-written).
- Print one line per chunk: seq, stream, word count, audio seconds, wall seconds.

## Tests: `tests/test_transcribe.py` (pytest)
- Pure-function tests that need no GPU/model: the midpoint keep/shift rule (words straddling both
  boundaries, word exactly on a boundary, empty input), the eligibility rule (next chunk / next gap /
  stale / not yet), the context assembly refusing to cross a gap, the mel shape for 60 s and 1.3 s input
  and that mel is deterministic. Factor the code so these are testable functions.
- One slow integration test marked `@pytest.mark.slow` and skipped unless env `WT_AUDIO` points at a
  speech wav: slice its first 90 s into 20 s chunks (last one shorter) with real meta files (correct
  sha256) in a tmp dir, run `--once` logic in-process, then assert: every chunk got a record; the
  concatenation of all chunk words (shifted by cumulative duration) matches a single whole-file
  transcription of the same 90 s with ≥ 95% word overlap (case/punctuation-insensitive sequence match,
  difflib ratio); and no word appears twice at any boundary (no consecutive duplicate spanning a seam
  in the concatenation where the whole-file transcript has one).

Keep it to these two files. Match witness.py's style.
