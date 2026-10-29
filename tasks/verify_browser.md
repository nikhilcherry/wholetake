owns: verdict/bundle.py verdict/service.py verdict/page.py tests/test_bundle.py
context: verdict/service.py ingest/chain.py anchor/verify.py

# Task: F5, "Verify it yourself" in the browser (agreed in the feature debate: rulings R6 + your objection #3).

A builder judge clicks one button on /v/<id> and watches their own browser re-check the evidence with WebCrypto,
with no trust in our server's word. Every claim the browser makes has to be computed in the browser from raw bytes.

## Real formats (verified on disk; don't invent fields)
- <witness>/<stream>/chain.jsonl: one canonical JSON line per entry (sort_keys, separators (",",":")),
  hash = sha256(utf8(canonical(entry without "hash"))). Linear: entry.prev == previous entry.hash, n increments
  from 0 (genesis prev = 64 zeros). Kinds and fields:
  chunk:      {"duration_s","hash","kind":"chunk","meta_sha256","n","prev","received_utc","seq","wav_sha256"}
  transcript: {"chunk_n","hash","json_sha256","kind":"transcript","mel_sha256","n","prev","seq"}
  plus genesis / gap / optin.
- NEVER re-serialize in JS (Python writes 20.0, JS writes 20). Verify from the RAW line text: delete the substring
  `"hash":"<64 hex>",` (or `,"hash":"<64 hex>"` if it's last) and sha256 the rest. I checked this: 14/14 real lines match.
- <stream>/anchors.jsonl (may NOT exist; on the demo box it currently doesn't): {"n","head_hash","method":
  "eas.multiTimestamp","chain_id","eas","tx","block","onchain_ts","onchain_utc","sent_utc"}. anchor.verify.best_anchor
  picks the earliest record with n >= the transcript entry's n.
- Witness files: <seq:06d>.wav (sha → chunk.wav_sha256), <seq:06d>.meta.json (sha → chunk.meta_sha256),
  <seq:06d>.json transcript (sha → transcript.json_sha256).

## Build
1. verdict/bundle.py: `build_bundle(job, witness_root) -> dict` for each seq in the verdict's proofs:
   {"stream", "chunks":[{"seq", "files":{"wav":url,"meta":url,"json":url}, "present":{...bools}}],
    "chain_lines": [raw line strings from the first needed entry through the anchored head, or through the end of
    the chain if there's no anchor], "anchor": record|null, "basescan": url|null}.
   It sends RAW lines as strings so the browser hashes exactly those bytes. No hashes computed server-side get sent
   as "answers": the bundle only carries what the browser needs in order to recompute.
2. verdict/service.py routes: GET /v/<id>/bundle.json and GET /v/<id>/witness/<seq:06d>.(wav|meta.json|json), raw
   bytes, the wav Range-served with the existing helper. Seq must be in the verdict's proofs (no directory
   browsing), a strict regex on the filename, and 404 JSON if missing. Only for done jobs.
3. verdict/page.py: in the custody strip, a button "Verify it yourself, in your browser". It opens a panel with
   lines that fill in live, each with ✓/✗ and the actual hex prefix computed in the browser:
     ① Downloaded 640 KB of witness audio for chunk #4 · sha256 = 0439c6… ✓ matches chain entry n=…
     ② Transcript #4 · sha256 = ab91a6… ✓ matches chain entry n=…   (meta too, in one line)
     ③ Hash chain: re-hashed N entries n=a…b in this browser · every prev link holds ✓
     ④ Anchored head: entry n=… hash 5f31d2… == the head pinned on Base Sepolia (tx 0x… link) ✓
        or, with no anchor: "⏳ not anchored yet: chain head n=… awaits the next anchor" (amber, not ✗)
   A missing wav gives "audio pruned on this server: hash still pinned in the chain" (neutral) and the remaining
   checks still run. Any mismatch is a loud red ✗ with both hashes shown. It uses crypto.subtle.digest, which needs a
   secure context: http://localhost counts, but a LAN IP (http://192.168.x.y, which our phone demo uses) does NOT.
   In that case, include a small pure-JS SHA-256 (~60 lines, a standard compact implementation, no external fetch)
   as the fallback, and say which engine ran ("WebCrypto" / "JS SHA-256").
   At the bottom: "Re-run this offline: python -m anchor verify data/witness/<stream>". Keep all prior features
   (restore, drawers, aesthetics) intact.
4. tests/test_bundle.py: build a small chain with ingest/chain.py's real writer in a tmp witness root (or copy the
   style of the existing chain tests), and assert that the bundle's raw lines verify with the delete-the-hash-substring
   rule in Python, that the routes 404 on a seq not in proofs or on path tricks, and that wav Range gives 206. Also
   test the JS SHA-256 fallback on "abc" and on a 1 MB buffer, by running it through node if `node` is on PATH
   (skip otherwise).
