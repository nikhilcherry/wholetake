owns: verdict/restore.py verdict/service.py verdict/page.py tests/test_restore.py
context: verdict/service.py verdict/page.py tests/test_bot.py

# Task: "Hear what they cut" (F1) + Before/After context drawers (F2) on the evidence page.

We agreed this in the feature debate (same design as your critique, with my rulings R2/R5 and your objection #2).

## Facts (verified on disk)
- Witness: data/witness/<stream>/<seq:06d>.wav (16 kHz mono s16 PCM, 20 s), <seq:06d>.json transcript
  {"words":[{"w","s","e"},...], "start_utc_est", "duration_s", ...}. A missing seq may be <seq:06d>.gap.json:
  {"seq","kind":"gap","from_utc","to_utc","duration_s","reason",...} (real one: {"seq":3,"kind":"gap","stream":"aje","reason":"stream ended",
  "from_utc":"2026-10-28T18:53:57.409Z","to_utc":"2026-10-28T18:54:07.734Z","duration_s":10.325}. In the real demo
  data, seq 3 is a gap and the clip comes from seq 4, so "before" must stop at that gap). The witness root comes from verdict.service.default_witness_root().
- Clip: manager.resolve_clip_wav(job) gives a 16 kHz mono s16 wav (already served at /v/<id>/clip.wav with Range).
- Verdict: job["result"]["detection"]["witness"] has stream, pieces[{clip_s,seq,src_s}],
  removed[{after_clip_s,seq,src_s,words}], inserted[]. src_s is relative to that seq's wav.

## Build
1. verdict/restore.py (new, stdlib `wave` + `struct` only, plus numpy only if already imported elsewhere in verdict/; prefer stdlib):
   - `build_restored(clip_wav: Path, witness_root: Path, stream: str, removed: list[dict], out_wav: Path) -> dict`.
     Output wav = clip[0:seam1] + 120 ms silence + RECORD[removed1.src_s] + 120 ms silence + clip[seam1:seam2] + ... for
     EVERY removed span, sorted by after_clip_s. A src span that crosses into seq+1 (src_s[1] > chunk duration) continues
     from the next chunk's wav. If the input sample rates differ, raise ValueError (don't resample silently). Returns the
     manifest: {"duration_s", "segments":[{"kind":"clip"|"silence"|"restored","t0","t1","clip_s"?:[a,b],
     "seq"?,"src_s"?, "words"?:[{"w","t0","t1"}]}]}, where the restored segments carry their words re-timed into OUTPUT
     time (from the transcript json words that overlap src_s), for karaoke. Write the wav atomically.
     If ANY needed witness wav is missing, raise RestoreUnavailable("witness audio for seq N not on disk").
   - `edge_context(witness_root, stream, pieces, window_s=15.0) -> {"before": {...}|None, "after": {...}|None}`.
     before = transcript words in the 15 s of RECORD immediately before pieces[0] (by ordering pieces on (seq, src_s[0]),
     take the earliest), crossing back into seq-1 when needed. after = symmetric after the latest piece end. Each side:
     {"words":[{"w","seq","s","e"}], "stops": null | {"kind":"gap","from_utc","to_utc"} | {"kind":"stream_start"} |
     {"kind":"live_edge"}}. "stops" says why the window got cut short (a gap file, no seq-1, no seq+1 yet). Also
     include "audio": {"seq","src_s"} spans so the page can play them via the endpoint below.
2. verdict/service.py, new GET routes (all 404 JSON {"error": ...} when unavailable, never 500 to the browser):
   - /v/<id>/restored.wav: built lazily into data/clips/<key>/restored.wav next to clip.wav, cached and keyed on the
     verdict_id (rebuild if the verdict changes). Served with the same Range support as clip.wav (factor the range
     code into a helper and use it for both).
   - /v/<id>/restored.json: the manifest.
   - /v/<id>/context/<before|after>.wav: the record audio for that side (concatenated across seqs), Range-served.
   Only for jobs with status done. Validate <id> exactly as the existing routes do.
3. verdict/page.py:
   - "Hear the cut" becomes "▶ Hear what they cut" when restore is available (check on the server at render time: the
     verdict is cut, there's ≥1 removed span, every needed witness wav exists). It plays restored.wav in a SECOND
     hidden <audio id="restoredAudio">, pausing the clip player first, so exactly one element plays in this mode:
     it starts 2.5 s before the first seam (in output time) and runs to 2.5 s after the restored span ends. While a
     "restored" segment plays: the record track's red excised block pulses, the words light up one by one (karaoke)
     in the transcript's removed pill, and a banner over the SVG reads "▶ From the record: the 0.9 s the clip
     removed". The two playheads follow output time mapped back (clip segments → clip time; restored → record time).
     When restore is unavailable, keep the current "Hear the cut" (clip only) and add a quiet note: "Record audio
     not on this server: the words come from the hash-chained transcript".
   - Before/After drawers around "What the record says": "◀ 15 s before the clip" / "15 s after the clip ▶", collapsed
     by default as <details>, with the words, a small play button (context/<side>.wav), and the stop reason
     ("stream started here" / "witness gap 18:52:10–18:52:40 UTC" / "live edge").
   - Keep everything from the aesthetics rounds intact, and keep every string the tests check.
4. tests/test_restore.py: synth wavs with numpy-free stdlib (or ffmpeg sine like tests/test_witness.py) into a tmp
   witness root; test build_restored (duration = clip + removed + 0.24 s per cut; the restored samples equal the
   record samples exactly; multi-cut order; a span crossing into seq+1; RestoreUnavailable on a missing wav),
   edge_context (crosses into seq-1, stops at a gap file, stream_start, live_edge), and the HTTP routes through the
   service the way tests/test_bot.py does (404 for a missing witness wav, 206 with a Range).
