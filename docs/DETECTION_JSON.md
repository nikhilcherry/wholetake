# `wt-detect` → `detection.json` (schema `wt-detect/1.0`)

The one command the product side (verdict page, X bot, anchoring) calls. Everything DSP lives
behind it: S2 (layer-1 informed, `l1verify.py`), S3 (layer-2 blind, `blind.py`), S4 (witness
matcher, `witness.py`). Code: `roomstamp/wt_detect.py`. Tests: `python -m roomstamp.wt_test`.

## Calling it

```
wt-detect CLIP -o detection.json [--venue logs] [--key-file venue.key] [--around TIME] [--witness REC]
                                  [--fast | --layers l2 --merge | --confirm] [--quiet]
```

- `CLIP`: any audio/video (mp4, m4a, ogg, webm, mp3, wav…), 2 s – 10 min.
- `--venue DIR`: the emitter's `session_*.jsonl` + `feed_*.wav` (default `logs`).
- `--around TIME`: the X post time (unix or ISO; UTC if no zone). Optional, narrows the search.
- `--witness REC`: S4 witness record (dir or audio file). Optional.
- Import instead of shelling out: `from roomstamp.wt_detect import run; doc = run(path, out_path=..., ...)`.

**Exit codes**: `0` = verdict written (any verdict, including "not verified"); `3` = clip unreadable
(file still written, `status: "error"`); `4` = internal error (file still written); `2` = usage error
(bad args / missing key; nothing written). **Key off the JSON, not the exit code.**

### Two-step page flow (fast verdict, then backup check)

```
wt-detect clip.mp4 -o d.json --fast                     # ~5-10 s: L1 (+witness). Never runs L2.
# if d.json.pending_layers is non-empty, show the verdict + spinner, and in the background:
wt-detect clip.mp4 -o d.json --layers l2 --merge        # runs L2, merges, re-fuses, atomically rewrites d.json
```

Without `--fast`, one call does both (L2 runs only when L1 did not lock). The file is always written
atomically (tmp + rename), so a poller never reads half a file.

## What to render: only these fields are stable

Anything under `layers.*.detail` is raw stage output for a "debug" accordion: it **will** change.
New keys may be added; existing keys are never renamed or retyped; absent values are `null`.

| field | type | meaning |
|---|---|---|
| `schema` | `"wt-detect/1.0"` | |
| `status` | `ok` \| `error` | `error` = clip unreadable / internal; `error.code` ∈ `NOT_FOUND EMPTY_FILE DECODE_FAILED NO_AUDIO_STREAM TOO_SHORT TOO_LONG INTERNAL` |
| `pending_layers` | `["l2"]` or `[]` | a `--merge` run would add evidence; keep the spinner |
| `verdict.headline` | string | **show verbatim.** One or two sentences, IST times, clip times as m:ss |
| `verdict.x_reply_text` | string ≤ 260 | reply for the X bot (no URL) |
| `verdict.recorded_here` | `verified` \| `likely` \| `not_verified` \| `unknown` | see rules below |
| `verdict.integrity` | `intact` \| `edited` \| `unknown` | `intact` only if a cut-checking layer covered the clip |
| `verdict.recorded_at_unix` / `_iso` | float / string | wall-clock time of the clip's first sample (`+05:30`) |
| `verdict.recorded_at_precision_s` | 0.01 / 0.02 / 0.1 | L1 / L2 / witness |
| `verdict.recorded_at_source` | `l1` \| `l2` \| `wt` \| null | |
| `verdict.tempo_ratio` | float \| null | 1.25 = clip was sped up 1.25× (only L2 measures it) |
| `verdict.content_match` | bool | the words match the venue's own recording |
| `verdict.venue` | `{name, key_id, session, mode}` | `mode` ∈ `pa` `table` `file` |
| `verdict.edits[]` | list | below; deduplicated across layers, sorted by `clip_t` |
| `verdict.confidence` | `{l1, l2, wt}` | each layer's status |
| `layers.{l1,l2,wt}.status` | `present likely inconclusive absent too_short skipped error timeout pending` | |
| `layers.*.{impl, elapsed_s, summary, z, p_value}` | | `impl`: `s2`/`s1-minimal`, `s3`, `s4` |
| `clip` | `{path, sha256, bytes, duration_s, orig_rate, orig_channels, codec, decoder}` | `sha256` of the uploaded bytes |
| `proof` | below | for the proof panel and the §63 kit |
| `warnings[]` | strings, `code: text` | show in a "notes" fold, never as the verdict |
| `run` | `{tool, version, git_commit, started_unix, elapsed_s, seed, argv, env}` | reproducibility |

### `verdict.edits[]`

```json
{"kind": "removed", "clip_t": 12.04, "clip_t_range": [12.04, 12.04], "removed_s": 0.300,
 "venue_t_from": 1793430620.1, "venue_t_to": 1793430620.4, "precision_s": 0.002, "source": "l1",
 "restored_text": "not", "restored_audio": "detection_edit0.wav", "restored_audio_venue_t": [..., ...]}
```

- `kind`: `removed` (cut), `repeated` (`removed_s` < 0), `foreign` (audio not from this venue,
  `duration_s` set), `replaced` (venue audio from elsewhere in the talk swapped in: `duration_s`,
  `source_offset_s` = where it came from, + later / − earlier; from L1 or S4), `reordered`,
  `inserted` (S4).
- `source`: `l1` (sample-precise), `wt` (word-level), `l2` (coarse: `clip_t_range` spans several s,
  `precision_s` 0.1; say "about").
- `restored_audio`: path **relative to detection.json** of a WAV cut from the venue feed around the
  removed span (±1.5 s): the "here's what was said" player. Only for L1 cuts with a feed.
- `restored_text`: words from S4 (null until S4 runs with ASR on).
- A cut made inside a pause can sit anywhere in that pause (the audio is identical), so `clip_t`
  is ±0.5 s there; `removed_s` stays exact.

### `proof` (proof panel, §63 kit)

`spec`, `drand_chain`, `key_id`, `session_id`, `session_log_sha256`, `feed_wav_sha256`, `clip_sha256`,
`normalized_pcm_sha256` (+ `normalized_format`), `matched_t_range: [t0, t1)`, `drand_rounds[]`
(anchored rounds in that range), `anchors[]` = `{t, round, anchored, randomness, anchor, dac_utc}`
per matched second (exactly the emitter log lines; `anchor = SHA256("anchor|" + S_t)` is what goes
on chain). `merkle` is `null`: the Merkle tree / EAS tx / OTS proof belong to the anchoring
service. Join on `session_id` + `t`.

## Verdict rules (in `fuse()`; pure function of the stage records)

1. `verified` **only** if L1 or L2 says `present`. L1 at `present` has a wrong-key control built in;
   L2 at `present` needs p ≤ 1e-3 and beats every random key.
2. `likely` if L2 says `likely` or L1 says `inconclusive`.
3. `not_verified` if a stamp layer ran and found nothing. If the words still match the venue
   recording: "Words match …, but the room stamp is missing" (e.g. re-recorded from a stream).
4. `unknown` if no stamp layer could run (no session for the key, unreadable clip, all skipped).
5. Time: L1 beats L2 beats witness. If L1 and L2 disagree by > 1 s, L1 wins and a warning is added.
6. Edits: L1 beats witness beats L2. A coarser edit within 2 s of a finer one is the same edit
   (the witness's `restored_text` is copied onto it); a size disagreement is kept as a warning.
7. Table mode (our demo rig) has no layer 2: `l2.status = "skipped"`, which is normal there.

## Headlines you will see

- `Recorded at HH Goa demo stage, 31 Oct 2026, 11:42:07 IST. 0.30 s removed at 0:12.`
- `Recorded at …. Complete: nothing removed (21.3 s).`
- `Recorded at …. 1.20 s at 0:10 replaced with audio from 15.0 s later in the talk.`
- `Recorded at …. Room stamp verified; cuts not checked.` (L2 only: no feed)
- `Probably recorded at … (weak room stamp).`
- `Words match …, but the room stamp is missing: this audio wasn't recorded in the room (e.g. re-recorded from a stream).`
- `No room stamp found. This clip can't be tied to HH Goa demo stage.`
- `No room stamp found, and the clip is too short (6.0 s) for the backup check (needs 8 s).`
- `Couldn't check this clip: <reason>.`
- suffix `Still checking the backup layer…` while `pending_layers` is non-empty.

## Stage adapter contracts (for S2/S3/S4 owners)

- **S2**: `l1verify.verify(clip48k, feed_slice, Session, key, log=)` with `Session(secs={t: log record})`
  (per-second randomness and stamper state). wt-detect slices the feed around the speech-envelope match.
- **S3**: `blind.detect(clip48k, key, RandomnessSource(log, unanchored, cache), t_lo, t_hi, seed=)`, run in a
  killable subprocess (`--l2-timeout`, default 150 s). Seed = first 4 bytes of the clip's sha256.
- **S4**: `witness.wt_stage(clip48k, witness_path) -> {status, summary, edits[], recorded_at_unix,
  venue_t_start, venue_t_end (seconds into the record), transcript}`. `WHOLETAKE_ASR` defaults to
  `none` inside wt-detect (no model download mid-demo); set it to enable transcripts.
