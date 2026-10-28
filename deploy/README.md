# Wholetake Systemd Deployment

User-level `systemd` units for witnessing streams, transcribing chunks, maintaining the hash chain, anchoring chain heads on Base Sepolia (EAS) and Bitcoin (OpenTimestamps), serving clip verdicts + evidence pages, and running the X reply bot.

## Demo Stack Quickstart (`simx` + `verdict` + `bot`)

For the local hackathon demo where the simulated X server runs on `127.0.0.1:8791` and the verdict service runs on `127.0.0.1:8790`:

1. **Simulated X (`simx`)** on port `8791`:
   ```bash
   ./py -m simx serve --port 8791
   ```
2. **Verdict API & Evidence Page (`verdict`)** on port `8790` (allow-listing `127.0.0.1:8791` so `fetch` can pull demo videos from `simx` without weakening SSRF blocks for any other host/port):
   ```bash
   WT_FETCH_ALLOW_HOSTS="127.0.0.1:8791" ./py -m verdict serve --port 8790
   ```
   Evidence pages are served at `http://127.0.0.1:8790/v/<job_id>` (plus `/v/<job_id>/clip.wav` and `/v/<job_id>/verdict.json`).
3. **X Reply Bot (`bot`)**:
   ```bash
   X_API_BASE="http://127.0.0.1:8791" \
   X_BOT_TOKEN="sim-wholetake" \
   WT_VERDICT_URL="http://127.0.0.1:8790" \
   WT_PAGE_BASE="http://127.0.0.1:8790" \
   BOT_POLL_S="5" \
   BOT_INCLUDE_LINK="1" \
   ./py -m bot run
   ```
   *(On real X, set `X_API_BASE=https://api.x.com`, `X_BOT_TOKEN=<real-token>`, and leave `BOT_INCLUDE_LINK=0` by default.)*

## Anchor Wallet Setup (One-Time)

1. Generate the throwaway anchoring wallet (`data/anchor.key`, mode `0600`):
   ```bash
   ./py -m anchor wallet new
   ```
2. Fund the printed address with Base Sepolia ETH from a faucet (e.g. Alchemy, Coinbase Developer Platform, or Superchain Base Sepolia faucet).
3. Verify the balance on Base Sepolia:
   ```bash
   ./py -m anchor wallet balance
   ```

## Install & Enable

```bash
mkdir -p ~/.config/systemd/user ~/Projects/wholetake/deploy/streams
cp ~/Projects/wholetake/deploy/*.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now wholetake-transcribe.service wholetake-chain.service wholetake-anchor.service wholetake-verdict.service wholetake-bot.service
loginctl enable-linger "$USER"
```

## Add a Stream

1. Generate the creator's opt-in claim code (also creates `data/claim.key` if not present):
   ```bash
   ./py -m ingest.optin claim "https://www.youtube.com/@creator"
   ```
2. Create `deploy/streams/<name>.env` with the livestream URL:
   ```bash
   echo 'WT_URL=https://www.youtube.com/@creator/live' > deploy/streams/creator.env
   ```
3. Enable and start the witness instance:
   ```bash
   systemctl --user enable --now wholetake-witness@creator.service
   ```
   *(If the channel removes its claim code, the witness exits with status `3` and systemd will not restart it.)*

## Verdict Service & CLI

Check a clip URL or local file directly from the command line (writes `verdict.json` and prints the headline):

```bash
./py -m verdict check "https://www.youtube.com/watch?v=..." --out verdict.json
```

Or run the HTTP verdict service directly (listens on `127.0.0.1:8790` by default):

```bash
./py -m verdict serve
```

## Verify Anchors

To verify every on-chain anchor for a stream against local `chain.jsonl` and live Base Sepolia `getTimestamp`:

```bash
./py -m anchor verify data/witness/<name>
```

## Status & Logs

```bash
systemctl --user status 'wholetake-*'
journalctl --user -u wholetake-witness@creator -u wholetake-transcribe -u wholetake-chain -u wholetake-anchor -u wholetake-verdict -u wholetake-bot -f
```

## Quarantine a Corrupt Unchained Chunk

If `wholetake-chain` halts on a corrupted chunk before chaining it, inspect the logs for the exact quarantine command:

```bash
./py -m ingest.chain --quarantine data/witness/<name> <seq>
systemctl --user restart wholetake-chain.service
```
