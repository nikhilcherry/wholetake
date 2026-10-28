# Wholetake Detector Contract (`detection.json`)

This contract defines the interface between the **verdict service** (`verdict/`, part N3) and the **signal-processing detector** (part S5).

## 1. Invocation & Isolation Rules

The verdict service invokes the detector via `$WT_DETECT` (default `./py -m verdict.ref_detect`):

```bash
$WT_DETECT <clip.wav> --witness-root <root> --out <detection.json>
```

- **Input `<clip.wav>`**: Normalised 16 kHz, mono, signed 16-bit PCM WAV file on local disk.
- **Input `--witness-root <root>`**: Directory containing per-stream witness directories (`<root>/<stream>/000041.wav`, `.meta.json`, `.json` transcript, `.mel.npy`, `chain.jsonl`, etc.).
- **Output `--out <detection.json>`**: Path where the detector must write the JSON output.
- **Exit code**: `0` if and only if `<detection.json>` was written and conforms to this contract. Any non-zero exit code (or missing/invalid JSON) is treated as a detector failure.
- **Timeout**: Hard limit of 300 seconds per invocation.
- **Zero Network Rule**: The detector **must never call the network**. All verification is strictly local signal processing against `<clip.wav>` and `<root>`.
- **Trust Rule**: The detector's `verdict`, `pieces`, `removed`, and `inserted` fields are the sole source of truth for the verdict. Downstream LLM labeling only describes `removed` spans already emitted here and never alters the verdict.

---

## 2. Full Schema & Example

```json
{
  "detector": {
    "name": "wt-detect",
    "version": "0.1"
  },
  "clip": {
    "duration_s": 22.4,
    "sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
  },
  "stamp": {
    "present": true,
    "layer1_score": 0.94,
    "layer2_score": 0.89,
    "recorded_at_utc": "2026-10-29T18:31:00Z",
    "venue": "main-hall",
    "cuts": [
      {
        "at_clip_s": 12.3,
        "removed_s": 0.6
      }
    ]
  },
  "witness": {
    "verdict": "cut",
    "stream": "aje",
    "pieces": [
      {
        "clip_s": [0.0, 12.3],
        "seq": 41,
        "src_s": [33.1, 45.4],
        "audio_score": 0.95
      },
      {
        "clip_s": [12.3, 22.4],
        "seq": 41,
        "src_s": [46.0, 56.1],
        "audio_score": 0.93
      }
    ],
    "removed": [
      {
        "after_clip_s": 12.3,
        "seq": 41,
        "src_s": [45.4, 46.0],
        "words": "not"
      }
    ],
    "inserted": [],
    "transcript_clip": "our policy had changed over the last year"
  }
}
```

When a clip carries no Roomstamp acoustic watermark, `"stamp"` must be `null` (or `"present": false`).

---

## 3. Field Reference & Units

### `detector` (object, required)
- `name` (`str`, non-empty): Identifier of the detector implementation. Use `"wt-detect"` for the full acoustic+text detector, or `"ref-text-only"` for the reference text-only stand-in. If `name` contains `"text-only"` or all pieces have `audio_score: null`, the verdict service marks `detector_kind: "text-only"`.
- `version` (`str`, non-empty): Version string (e.g. `"0.1"`).

### `clip` (object, required)
- `duration_s` (`float > 0`): Duration of `<clip.wav>` in seconds.
- `sha256` (`str`, 64 lowercase hex chars): SHA-256 of the `<clip.wav>` file bytes.

### `stamp` (`null` or object, required)
Roomstamp acoustic watermark result, or `null` when no stamp is present or checked. When an object:
- `present` (`bool`): Whether a valid Roomstamp was detected.
- `layer1_score` (`float | null`): Carrier synchronization / presence score in `[0.0, 1.0]`.
- `layer2_score` (`float | null`): Payload decoding / integrity score in `[0.0, 1.0]`.
- `recorded_at_utc` (`str | null`): ISO-8601 UTC timestamp decoded from the watermark payload.
- `venue` (`str | null`): Venue/room identifier decoded from the watermark payload.
- `cuts` (`list[object]`): Cuts detected directly from watermark phase discontinuities:
  - `at_clip_s` (`float >= 0`): Position in the clip (seconds from clip start) where the discontinuity occurs.
  - `removed_s` (`float > 0`): Estimated duration in seconds removed at that point.

### `witness` (object, required)
Comparison against the witnessed livestream archive under `--witness-root`:

- `verdict` (`str`): Exactly one of:
  - `"faithful"` — The clip plays continuously from a single witnessed stream with no internal deletions (`removed` is empty) and no out-of-order jumps.
  - `"cut"` — The clip comes from a single witnessed stream in forward chronological order (within 60 s between pieces), but one or more internal spans of the source record were omitted (`removed` is non-empty).
  - `"spliced"` — Pieces come from different streams, jump backwards in source time, jump forward by more than 60 s of source time, or include substantial unaligned insertions between witnessed segments.
  - `"not_found"` — Less than 60% of the clip aligns to any witnessed stream in `--witness-root`.
- `stream` (`str | null`): Stream directory name (e.g. `"aje"`) matched under `--witness-root`. Must be `null` when `verdict == "not_found"`, and a non-empty string otherwise.
- `pieces` (`list[object]`): Contiguous matched segments ordered by `clip_s`:
  - `clip_s` (`[start_s, end_s]`): Start and end time in seconds within `<clip.wav>` (`0 <= start_s < end_s <= clip.duration_s + 0.5`).
  - `seq` (`int >= 0`): Chunk sequence number (`<seq:06d>.wav` / `<seq:06d>.json`). **A piece may not cross a chunk boundary**; if a continuous segment spans chunk `41` and chunk `42`, emit two pieces (one with `seq: 41`, one with `seq: 42`).
  - `src_s` (`[start_s, end_s]`): Start and end time in seconds **relative to chunk `seq`'s WAV start** (the exact same coordinate frame as word timestamps `s`/`e` in `<seq:06d>.json`). Must satisfy `0 <= start_s < end_s <= chunk.duration_s + 0.5`.
  - `audio_score` (`float | null`): Normalised cross-correlation (NCC) or acoustic match score in `[0.0, 1.0]` (`>= 0.8` confirms an acoustic match). `null` only for text-only reference detectors.
  - `stream` (`str`, optional): If a cross-stream splice occurs, individual pieces may optionally specify their source `stream`.
- `removed` (`list[object]`): Source spans omitted between consecutive `pieces`:
  - `after_clip_s` (`float`): Timestamp in `<clip.wav>` (seconds) where the cut seam occurs.
  - `seq` (`int >= 0`): Chunk sequence number containing the omitted words/audio.
  - `src_s` (`[start_s, end_s]`): Start and end time in seconds within chunk `seq` (`0 <= start_s <= end_s <= chunk.duration_s + 0.5`).
  - `words` (`str`): Space-joined transcript words omitted from the witness record at this cut.
- `inserted` (`list[object]`): Clip spans that did not match the witness record:
  - `clip_s` (`[start_s, end_s]`): Start and end time in seconds within `<clip.wav>`.
  - `text` (`str`): Transcribed words in that unmatched clip span.
- `transcript_clip` (`str`): Full plain-text transcript of `<clip.wav>` (attacker-controlled data; never trusted for control flow).
