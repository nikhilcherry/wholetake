Round 1 review. The backend is right: I byte-compared restored.wav on real data. The restored span == record seq 4
[8.98:9.90] exactly, the head and tail == the clip, the 120 ms pads are true zeros, the duration is 14.216 s, Range gives
206, and before-context stops at the real gap file. Tests pass (67). The UI has bugs I MEASURED in Chromium by sampling
every 100 ms after clicking "Hear what they cut" (restored segment = output 7.08–8.00, words are 7.08 are,
7.20 through, 7.36 the, ...):

1. KARAOKE IS WRONG: from 7.49 to 8.30, the only .kw-active word is "the", for the whole span. It never sweeps
   are → through → the → roof, → my. Find the bug (index mapping between manifest words and pill spans? the pill
   splits on whitespace and punctuation differently?) and make each word active exactly within [t0, t1). Words
   already played stay "spoken" (a dimmer lit state), so the line fills in like karaoke.
2. TIMING LAG: the banner and highlight turn on ~0.4 s late (7.49 vs 7.08) and stay ~0.3 s late (8.30 vs 8.00).
   timeupdate fires at ~4 Hz, so drive restored mode from requestAnimationFrame reading audio.currentTime, and
   start the loop on 'playing', stop it on 'pause'/'ended'.
3. TIMESTAMP CONTRADICTION a judge will catch: the quote header says "chunk #4 · 18:53:57–18:54:03 UTC", and the
   drawer right above it says "witness gap 18:53:57–18:54:07 UTC". So the words appear to be inside a gap. Cause:
   start_utc_est = received − duration, which the witness docstring calls "a guess that is off" while the HLS
   backlog bursts. Rule: the only wall-clock we claim is received_utc (what anchoring pins). Quote header:
   "chunk #4 · received 18:54:11 UTC". Record axis labels: chunk-relative ("#4 +1.7 s" … "#4 +16.1 s"), not
   est wall-clock. Gap labels keep from/to (those are real receipt times).
4. Clicking "Hear what they cut" while it's already playing should restart it, not stack a second loop. Playing the
   normal clip player must leave restored mode cleanly (banner off, words reset).
5. The longer button label pushes the H1 to two lines at 1280. Label it "▶ Hear what they cut" (drop the "(0:07)", which the
   H1 already says).
Emit only the files you change.
