# S1 — Roomstamp stamp emitter

drand round + venue key → live audio to the speaker/PA (layer 1 keyed noise + layer 2 keyed
spectral-envelope wobble) + a per-second JSONL log. Spec: `roomstamp/spec.py` (docstring) and
`spec_v1.json` (normative coefficients, `python -m roomstamp spec-json`).

## Run it

```
pip install -r requirements.txt
python -m roomstamp keygen                      # venue.key (gitignored; never logged, only key_id)
python -m roomstamp devices
# stamp-only speaker at a table; the laptop mic records the clean feed:
python -m roomstamp run --mode table --out "<speaker>" --in "<mic>"
# talker on a mic -> laptop -> PA (layer 1 + layer 2):
python -m roomstamp run --mode pa --out "<PA out>" --in "<lav/USB mic>"
# canned speech through the speaker (fallback demo):
python -m roomstamp run --mode file --wav-in testdata/speech.wav
```
Output: `logs/session_<t_start>.jsonl`, `logs/feed_<t_start>.wav` (clean feed), plus
`out_<t_start>.wav` with `--save-output`.

| mode | speaker plays | level | feed |
|---|---|---|---|
| `pa` | voice with L2 wobble + gated L1 | L1 −30 dB re tracked speech, floor 0.1, L2 nominal ±4 dB (≈ ±1.5 dB effective) | mic |
| `file` | same, voice from WAV | same | WAV |
| `table` | gated L1 only (L2 needs the voice in the speaker) | −50 dBFS absolute, floor 0.3 | mic (for gating + verifier) |

## Checks

```
python -m roomstamp selftest                    # offline: dry, hall RT60 0.6 s @15 dB SNR, table mode
python -m roomstamp selftest --loopback --out "Speakers (Realtek" --in "Microphone Array"
python -m roomstamp audibility                  # objective pre-check (pause hiss, L2 step size)
python -m roomstamp abtest                      # blind human A/B (interactive)
python -m roomstamp detect REC.wav --log logs/session_X.jsonl [--start S --dur D] [--check-drand]
```

## Measured (2026-10-29, this laptop)
- Offline, 40 s TTS speech: dry → L1 100% windows, L2 z 22.9; hall RT60 0.6 s + 15 dB SNR →
  L1 98% (clip z 107), L2 z 4.8; table −50 dBFS → L1 95%. Wrong key: never passes (clip z ≤ 4).
  L2 in a hall needs ~30–40 s with this simple detector (r128's reverb-aware template is S2's job).
- Live: saved output == offline re-render from feed + log + key within 1 int16 LSB, for the whole
  session and from mid-session (logged `ref_rms`/`gain`). `detect --start 20` finds a mid-session
  section at lag +20.000 s.
- Timing (WASAPI, laptop speaker): output buffer 30 ms slips ~40 ms / 26 s, 50 ms ~20 ms (silent
  underruns, not flagged by WASAPI), **80/100 ms: 0 slip** → defaults pa 80 ms, others 100 ms.
  Bluetooth output rebuffers (+30…170 ms jumps): **use wired out at the venue.**
- Network down: emitter never blocks (cache-only drand); seconds logged `anchored: false`.
- Layer 2 effective modulation ≈ 0.38 × `--l2-delta` (overlapping 2nd-order bands partly cancel;
  found by S3): δ 2 → 0.80 dB, 3 → 1.17 dB, 4 → 1.50 dB. Default raised to 4 (still below the lab's
  ±2 dB effective). Hall test 25 s: L2 z 3.8 at δ 2 → 5.3 at δ 4. S3 blind, re-filmed 20 s: 1/6 → 6/6.
- Audibility proxy at δ 4: pause residual −46 dB re speech; L2 frame step p95 4.3 dB (unvalidated).

## Not done / owed
- **Human listening check** (`abtest`, 2 people; try `--l2-delta 4`, then 3) — "nobody notices" isn't proven.
  If 4 is audible, S3's blind check weakens sharply (needs re-filmed clips ≥ 20 s at 3).
- **Acoustic loopback / phone at 3–5 m**: the laptop's internal mic returns exact zeros
  (muted/privacy-blocked); unmute it, then `selftest --loopback`. Same cause: every table-mode
  log so far has an empty feed (run with `--in none`), so S2 can't do an informed check on them.
- `pa` mode untested live (no working mic); live voice through the PA lags ~85 ms.
- Time claims: only seconds with `anchored: true` AND randomness matching the public beacon
  (`detect --check-drand`) prove "not before t − 12 s". Unanchored seconds prove key possession only.
