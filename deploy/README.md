# Wholetake Systemd Deployment

User-level `systemd` units for witnessing streams, transcribing chunks, and maintaining the hash chain.

## Install & Enable

```bash
mkdir -p ~/.config/systemd/user ~/Projects/wholetake/deploy/streams
cp ~/Projects/wholetake/deploy/*.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now wholetake-transcribe.service wholetake-chain.service
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

## Status & Logs

```bash
systemctl --user status 'wholetake-*'
journalctl --user -u wholetake-witness@creator -u wholetake-transcribe -u wholetake-chain -f
```

## Quarantine a Corrupt Unchained Chunk

If `wholetake-chain` halts on a corrupted chunk before chaining it, inspect the logs for the exact quarantine command:

```bash
./py -m ingest.chain --quarantine data/witness/<name> <seq>
systemctl --user restart wholetake-chain.service
```
