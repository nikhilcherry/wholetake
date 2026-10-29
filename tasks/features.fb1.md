Rulings. Reply with OBJECTIONS ONLY (in NOTES, numbered). If you agree with a ruling, skip it. Write no files.

R1 B1 (live stage-mic witness + an audience member trims a clip on their phone) is THE demo. Accepted, and promoted
   above F1. What it needs from us: (i) N1 witness gets a `mic:<pulse-device>` source (ffmpeg -f pulse, no -re). (ii) simx
   binds 0.0.0.0, so a phone on venue Wi-Fi can open simx and post the trimmed clip itself: "the clipper is in the
   audience". (iii) Latency budget: 20 s chunk (use --chunk 10 for the stage) + transcribe + chain ≈ 25 s, so the chunk
   is in the record before the phone has finished trimming. Neutral sentence, not a provocative one: judges remember
   the provocation, not the tech.
R2 F1 and the contradiction in your own answer. In (a) you said: server-stitch one wav, never orchestrate two
   <audio> elements. In (c) you designed exactly that JS pause/play/resume. Your (a) was right. One
   `/v/<id>/restored.wav` = clip[0:seam] + RECORD[removed.src_s] + clip[seam:], resampled to the clip's rate, and a
   `/v/<id>/restored.json` with segment boundaries so ONE <audio> drives the red karaoke. No chime: we never add a
   synthetic sound to an evidence artifact. Use 120 ms of silence on each side of the restored span instead. Both
   wavs are already 16 kHz mono PCM. The stdlib `wave` module is enough.
R3 B2 is F1 with a noisy clip. Not a separate feature. Kept as a talking point.
R4 B3 (real @247pmstudio clips): that's a test set, not a feature. It works only if we witness the talks ourselves via
   R1's mic source. Kept as an ops task.
R5 F2 accepted as Before/After drawers (±15 s, crossing into seq±1, honouring gap files), computed at render time.
   No change to the verdict schema or the enum.
R6 F5. Your design invented fields. The real formats:
   - chain.jsonl is a LINEAR hash chain, not a Merkle tree. Each line is canonical JSON (sort_keys, separators (",",":"))
     with hash = sha256(canonical(entry minus "hash")). Fields: n, prev, kind (genesis|chunk|transcript|gap|optin),
     seq, wav_sha256, meta_sha256, received_utc, duration_s, ...
   - Re-canonicalizing in JS is WRONG: Python writes 20.0 and JS writes 20. The trick that works (I verified 14/14
     lines): take the RAW line text, delete the substring `"hash":"<64hex>",` (or `,"hash":"<64hex>"` when it's last),
     sha256 the rest. JS must verify against raw lines, never against re-stringified objects.
   - Anchors: <stream>/anchors.jsonl, records the chain head n+hash and the EAS tx (see anchor/verify.py best_anchor).
     The browser checks that the walk from the chunk's entry reaches the anchored head hash, then links to basescan.
   - The wav check: fetch /v/<id>/witness/<seq>.wav, sha256 it in the browser, and compare to the chain's wav_sha256.
     This is the "don't trust us" moment.
R7 F3 Community Note: 45 min, Friday, after the above.
R8 Build order today: (1) mic source + simx LAN bind [me, small], (2) F1+F2 [you], (3) F5 [you]. Each is its own
   task file with its own owns:, run sequentially because 2 and 3 both touch verdict/service.py and page.py.
