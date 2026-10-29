# S3: blind layer-2 detector (`roomstamp/blind.py`)

Clip + venue key, no clean feed -> present / likely / absent, recording time (venue clock), confidence.

```
python -m roomstamp.blind CLIP.wav --log logs/session_<t>.jsonl          # window = the session
python -m roomstamp.blind CLIP.wav --around 2026-10-31T06:12:00Z --span 600 # no log: fetch drand rounds
python -m roomstamp.blindtest [--delta 4] [--null 200] [--only refilm]     # self-test, real Opus/MP3 codecs
```
Exit code 0 only for "present". `--json` returns `verdict, recording_start_unix/utc, tempo, z, sigma_over_null,
beats, p_value, cut{removed_s, at_clip_s, near_clip_s}`. Python: `blind.detect(clip, key, RandomnessSource(log), t_lo, t_hi, seed=...)`.

## How it works
- Band power with sharp STFT bands at the spec's 10 edges (80 ms Hann, 5 ms hop), resampled onto a 10 ms venue grid per tempo.
  Weighted by speech presence x per-band SNR (a re-film has nothing under ~400 Hz), weighted 1 s high-pass,
  per-row band mean removed (balanced pattern => AGC/loudness cancel), robust scale, clip at +-2.5.
- Template = `spec.l2_gain_db` block-averaged (ramp from `spec.N_TR`), same high-pass. FFT correlation at every lag
  where the clip fits; robust z over lags. Variants: tempo {1, 0.9, 1.1, 1.25, 1.5} (only if tempo 1 isn't decisive),
  reverb smear {0, 40 ms}, and a cut-tolerant pair statistic (2nd half may sit 0-3 s later).
- Confidence: the identical search for random keys on the same clip (adaptive, 50-200 keys) -> beats N/N,
  sigma over null, Gumbel-fit p. Present = p <= 1e-3 and beats all; likely = p <= 1e-2.

## Results (20 s clips unless noted, 140 s search window, simulated chains with real Opus/MP3)
| emitter `--l2-delta` | re-film (hall+Opus -> phone speaker -> room -> MP3), 6 keys | 15 s clip | controls |
|---|---|---|---|
| 2 (old default; logs before 2026-10-29 ~01:00) | **1/6 present** | 0/6 | 12/12 absent |
| 4 (S1 default now) | **6/6 present**, median 11.6 sigma, time err <= 12 ms | 4/6 present + 2 likely | 12/12 absent |

At +-4 the single runs also pass: digital, hall+Opus, hall+MP3, 1.25x speed-up (tempo found), and a 0.30 s cut
(reported "0.30 s removed at clip 9.5-10.0 s"). Real S1 output (`logs/out_1793214220.wav`, +-2, no room): time
correct to 10 ms but only "likely" (4.7 sigma).

## Why +-2 is too weak (S1 finding)
The spec's 10 IIR bands (2nd-order Butterworth, ~0.38 octave each) overlap heavily, so neighbours with opposite
signs cancel: `--l2-delta 2` delivers only ~0.7 dB of real band modulation (measured 0.5-0.87 dB per band).
Sharper IIRs are worse (phase rotation cancels the gain); an STFT-domain EQ delivers ~1 dB.
**Done by S1: pa/file default is now `--l2-delta 4` (~1.4 dB effective, still below the lab's +-2 dB that scored
PESQ 4.49). No spec change.** Still owed: `python -m roomstamp abtest --l2-delta 4` with two listeners; fallback 3.

## Limits
- Needs the voice to pass through the stamping speaker: **table mode has no layer 2**, so S3 can't check table-demo clips.
- Not tested yet: real phones, AAC (no encoder here), music beds, pitch-changing speed-ups, multiple cuts.
- Time error is -40..+2 ms (reverb pulls slightly early). Clips < 8 s are refused; < 15 s are unreliable.
- Speed: 2-15 s at tempo 1; ~25-45 s when the tempo search runs (absent or sped-up clips).
