# Wholetake × Roomstamp

**Others prove a video wasn't edited. Wholetake proves what the clip left out.**

Wholetake witnesses public livestreams as they happen and commits a hash-chained record of them before any clip exists. When a clip of that stream goes viral, Wholetake matches it against the record and tells you, word for word, what was cut. Then it plays you the missing audio.

- **Live verdict desk:** https://wholetake.premortem.tech
- **3-minute film:** https://youtu.be/Voqsauus4wA
- **Launch thread:** https://x.com/Arvyo170438/status/2116173204410827241

Built at HH Goa 2026 by team ARVYO ([@nikhilcherry](https://github.com/nikhilcherry), [@shreyasrajshekar](https://github.com/shreyasrajshekar)).

## Verdicts

| Verdict | Meaning |
|---|---|
| `faithful` | The clip is a contiguous piece of the record |
| `cut` | Words were removed. Shows exactly which ones, with timestamps on both sides |
| `spliced` | The clip joins pieces out of order, or inserts audio the record doesn't contain |
| `not_found` | Nothing like this exists in the record |

Verdicts are deterministic. They come from text alignment plus acoustic matching, never from an LLM. Gemini only writes a short label for cuts the detector already found, and it can't change the verdict.

**Hear what they cut:** for a `cut` verdict, the evidence page rebuilds the clip with the missing span put back, taken byte-for-byte from the witnessed record, and plays it next to the original.

## How it works

```
livestream ──► ingest/      witness chunks, Whisper transcript, log-mel, explicit GAP entries
                  │
                  ▼
              hash chain ──► anchor/   chain heads → EAS timestamp on Base Sepolia + OpenTimestamps
                  │
viral clip ──► verdict/     text alignment → S4 acoustic match (wholetake/) → Roomstamp check (roomstamp/)
                  │
                  ├──► evidence page  /v/<id>: two-track record vs clip, the cut, restored audio
                  ├──► bot/           X reply bot (mention-only, one reply per mention)
                  └──► extension/     "Check this clip with Wholetake" in Chrome, Edge and Firefox
```

- **Witness, opt-in only.** A creator opts in by putting a claim code in their channel bio. They don't install anything. If the code disappears, witnessing stops.
- **Gaps are recorded, not hidden.** Dropped segments go into the chain as `GAP` entries, so the record never claims coverage it doesn't have.
- **Roomstamp** (`roomstamp/`) embeds a two-layer acoustic watermark in the room at live venues: a noise layer plus an envelope layer built to survive a vocoder. A re-filmed or re-voiced clip still carries the venue and the UTC time it was recorded.
- **Detector contract.** The verdict service calls the detector through `$WT_DETECT` with no network access. See [CONTRACTS.md](CONTRACTS.md) and [docs/DETECTION_JSON.md](docs/DETECTION_JSON.md).

## Try it

Paste a clip URL into the desk at https://wholetake.premortem.tech. The demo record is a real witnessed Al Jazeera English live stream.

Run it locally (Python 3.12, ffmpeg on PATH; a CUDA GPU is optional but makes Whisper much faster):

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt
./py -m verdict check path/to/clip.mp4 --out verdict.json   # one-off verdict
./py -m verdict serve                                       # desk + API on 127.0.0.1:8790
```

The full demo stack (simulated X on :8791, the verdict service and the reply bot) and the systemd deployment for witnessing real streams are in [deploy/README.md](deploy/README.md). The browser extension is in [extension/README.md](extension/README.md).

## Tests

```bash
./py -m pytest                  # ingest, chain, anchor, verdict, bot, simx
./py -m wholetake.selftest      # S4 acoustic matcher
./py -m roomstamp.wt_test       # Roomstamp against the detector contract
argon_tests/run_all.sh
```

## Status (honest)

- Anchoring code is complete and has been verified against Base Sepolia, but the event's anchor wallet isn't funded yet, so chain heads currently show as *pending* rather than anchored.
- The X bot runs against `simx/`, a local subset of the X API v2. On real X it needs a bot account with the Automated label.
- One known failing test, `tests/test_verdict.py::test_service_end_to_end_ssrf_cache_and_restart` (pytest is 93 passed, 1 failed). The S4 selftest passes 7/7.
