# WholeTake & Roomstamp Deployment Runbook (`DEPLOY.md`)

This runbook covers deploying the full **WholeTake × Roomstamp** verification stack for both a **local laptop stage demo** (Docker Compose or native Python on Windows 11 / Linux / macOS) and a **small cloud Linux VM** (`systemd` or Docker Compose), while keeping all cryptographic keys outside git and container layers.

---

## 1. Architecture & Ports Overview

| Component | Port / Type | Entrypoint | Data / Volume Mounts | Purpose |
| :--- | :--- | :--- | :--- | :--- |
| **`verdict`** | `:8790` (HTTP) | `python -m verdict serve --host 0.0.0.0 --port 8790` | `./data/witness`, `./data/verdicts`, `./logs`, `./keys` | Verdict HTTP API (`/api/verdict`, `/api/verdicts`), HTML evidence pages (`/v/<job_id>`), restored audio, and BSA §63(4) proof kits. Invokes `$WT_DETECT`. |
| **`simx`** | `:8791` (HTTP) | `python -m simx serve --host 0.0.0.0 --port 8791 --data data/simx` | `./data/simx` | Local simulated X v2 API & timeline UI for offline/stage clipper demos without external rate limits. |
| **`wt-detect` / `roomstamp`** | CLI (one-shot / live DAC) | `python -m verdict.wt_adapter` / `python -m roomstamp` | `./logs`, `./keys/venue.key`, `./data/witness` | S5 unified detector (`CONTRACTS.md` & `wt-detect/1.0`) combining S4 witness alignment (`wholetake`) and S1/S2/S3 acoustic watermark verification (`roomstamp`). |
| **`bot`** *(optional daemon)* | Worker (`--profile daemons`) | `python -m bot run` | Connects to `simx:8791` & `verdict:8790` | Polls X / `simx` mentions, submits clips to `verdict`, and posts concise verdict replies. |
| **`witness` / `transcribe` / `chain` / `anchor`** *(optional daemons)* | Workers (`deploy/*.service` / `--profile daemons`) | `python -m ingest.witness`, `python -m ingest.transcribe`, `python -m ingest.chain`, `python -m anchor` | `./data/witness`, `./data/anchor.key`, `./data/claim.key` | Continuous livestream capture, Whisper ASR chunking, SHA-256 hash-chaining (`chain.jsonl`), and Base Sepolia EAS + OpenTimestamps anchoring. |

---

## 2. Local Laptop Demo (1-Command Docker & Native Python / Windows 11 WASAPI Fallback)

### A. 1-Command Startup (Docker Compose)

 Both `deploy/deploy.sh` (Linux/macOS/Git-Bash) and `deploy/deploy.ps1` (Windows PowerShell) automatically initialize runtime directories (`data/witness`, `data/verdicts`, `data/simx`, `logs`, `keys`) and generate a fresh 32-byte CSPRNG venue key at `keys/venue.key` (mode `0600`) if one is not already present.

**Linux / macOS / WSL:**
```bash
# Validate directories and generate keys/venue.key without starting containers
./deploy/deploy.sh check-env

# Build and start core services (verdict :8790 + simx :8791)
./deploy/deploy.sh up

# Or include the reply bot & background daemons
./deploy/deploy.sh up --with-daemons
```

**Windows 11 PowerShell:**
```powershell
# Validate environment and ensure runtime directories + keys/venue.key
.\deploy\deploy.ps1 check-env

# Start core stack (or add --with-daemons)
.\deploy\deploy.ps1 up --with-daemons
```

### B. Native Python `local-demo` Fallback (Zero Docker Required)

If Docker Desktop is unavailable or paused on the stage laptop to conserve RAM/CPU:

**Windows 11 PowerShell:**
```powershell
.\deploy\deploy.ps1 local-demo start --with-daemons
.\deploy\deploy.ps1 local-demo status
# When finished:
.\deploy\deploy.ps1 local-demo stop
```

**Linux / macOS:**
```bash
./deploy/deploy.sh local-demo start --with-daemons
./deploy/deploy.sh local-demo status
# When finished:
./deploy/deploy.sh local-demo stop
```

### C. Windows 11 Real-Time Audio Setup (`WASAPI` & `Speakers (Realtek`)

Containers on Windows/macOS do not have low-latency, jitter-free access to the host soundcard DAC. For live acoustic emission (`S1`), **always run `python -m roomstamp` natively on the Windows 11 host** sharing `./logs` and `./keys/venue.key` with the `verdict` service:

1. **Avoid Bluetooth Output**: Bluetooth headsets rebuffer with **+30 ms to +170 ms jumps**, breaking sample-accurate phase alignment. Always use wired output via hostapi **`WASAPI`** and the output device matching **`Speakers (Realtek`** at **48,000 Hz** (`80 ms` buffer in `pa` mode, `100 ms` buffer in `table`/`file` mode).
2. **Hardware-Muted Laptop Mic & Table Mode**: If the internal laptop microphone is hardware-muted (returning zeros), use an external USB/lav mic for the clean speech feed or run pre-rendered / file-backed emission (`python -m roomstamp render`) so a `logs/session_*.jsonl` log and its clean feed WAV exist next to each other.
3. **Offline ASR Mode**: When `faster-whisper` models are not downloaded on the demo machine, keep `WHOLETAKE_ASR=none` (default in `.env.example` and `Dockerfile`) so `wholetake` uses its deterministic acoustic matcher.

```powershell
# Render a stamped session into ./logs using the shared venue key
# writes logs/session_demo.wav (what the PA plays), logs/session_demo.jsonl (the session log the
# verdict service looks for) and logs/session_demo_feed.wav (the clean reference)
python -m roomstamp render testdata/speech.wav logs/session_demo.wav --key-file keys/venue.key --mode pa
```

---

## 3. Small Cloud Linux VM Deployment

### A. VM Sizing & Prerequisites
- **Instance**: 2 vCPU, 4 GB RAM, 20 GB SSD running Ubuntu 22.04 / 24.04 LTS or Debian 12.
- **Packages**:
  ```bash
  sudo apt-get update && sudo apt-get install -y git ffmpeg portaudio19-dev python3-venv docker.io docker-compose-v2
  ```

### B. Configuration & Starting with Docker Compose
1. Clone the repository to `/opt/wholetake` and copy `.env.example` to `.env`:
   ```bash
   cd /opt/wholetake
   cp .env.example .env
   chmod 600 .env
   ```
2. Initialize directories and `keys/venue.key` (`0600`), then launch with daemons:
   ```bash
   ./deploy/deploy.sh check-env
   ./deploy/deploy.sh up --with-daemons
   ```
3. **SSRF Protection**: Ensure `WT_FETCH_ALLOW_HOSTS` in `.env` lists only trusted internal simulation hosts (e.g. `127.0.0.1:8791,simx:8791`). All private/loopback IPs outside `WT_FETCH_ALLOW_HOSTS` are blocked by `verdict/fetch.py`.

### C. Alternative: Native `systemd` Units (`deploy/*.service`)
Consistent with `deploy/README.md`, you can run the services directly via `systemd` units located in `deploy/`:
- `deploy/wholetake-verdict.service` (`python -m verdict serve` on `:8790`)
- `deploy/wholetake-simx.service` (`python -m simx serve --port 8791 --data /opt/wholetake/data/simx`)
- `deploy/wholetake-bot.service` (`python -m bot run`)
- `deploy/wholetake-chain.service` (`python -m ingest.chain data/witness`)
- `deploy/wholetake-transcribe.service` (`python -m ingest.transcribe data/witness`)
- `deploy/wholetake-anchor.service` (`python -m anchor run data/witness`)
- `deploy/wholetake-witness@.service` (`python -m ingest.witness "$WT_URL" --out "data/witness/%i"`)

```bash
mkdir -p ~/.config/systemd/user ~/Projects/wholetake/deploy/streams
cp deploy/*.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now wholetake-verdict.service wholetake-simx.service wholetake-chain.service wholetake-transcribe.service wholetake-anchor.service wholetake-bot.service
loginctl enable-linger "$USER"
```

### D. Reverse Proxy TLS & Firewall Rules
- Bind `:8790` and `:8791` to `127.0.0.1` on the host and terminate HTTPS with Caddy or Nginx:
  ```bash
  sudo ufw default deny incoming
  sudo ufw allow OpenSSH
  sudo ufw allow 80/tcp
  sudo ufw allow 443/tcp
  sudo ufw enable
  ```
- Example Caddyfile for public evidence pages:
  ```text
  verdict.example.org {
      reverse_proxy 127.0.0.1:8790
  }
  ```

---

## 4. Key & Secret Hygiene

**Zero secrets may ever be committed to git or baked into Docker images.**

1. **Venue Acoustic Key (`keys/venue.key`)**:
   - Mounted at runtime into `/app/keys/venue.key` (`ROOMSTAMP_KEY_FILE`).
   - Generated automatically by `./deploy/deploy.sh check-env` or `python -m roomstamp keygen keys/venue.key` with permissions `0600` (32 random bytes = 64 hex chars).
2. **On-Chain Anchoring Key (`data/anchor.key`)**:
   - Created via `python -m anchor wallet new` with mode `0600` (`chmod 600 data/anchor.key`).
3. **Creator Opt-In Claim Key (`data/claim.key`)**:
   - Created via `python -m ingest.optin claim "https://www.youtube.com/@creator"` with mode `0600`.
4. **Key Rotation & Layer Audit**:
   - To rotate the venue key between events, archive `logs/` and run `rm -f keys/venue.key && ./deploy/deploy.sh check-env`.
   - `.dockerignore` explicitly excludes `.env`, `*.key`, `keys/`, `data/anchor.key`, and `data/claim.key`.

---

## 5. Healthchecks, Verification & Troubleshooting

### A. HTTP Healthchecks
Verify that `verdict` (`:8790`) and `simx` (`:8791`) are healthy:
```bash
curl -fsS http://127.0.0.1:8790/api/verdicts
curl -fsS "http://127.0.0.1:8791/2/tweets/search/recent?query=wholetake"
```

### B. One-Shot CLI Verification (`python -m verdict check`)
Run a standalone end-to-end check on a local WAV file without needing the HTTP server:
```bash
WHOLETAKE_ASR=none python -m verdict check testdata/witness/talk.wav --witness-root testdata/witness --out /tmp/verdict.json
```

Or invoke the S5 detector adapter directly per `CONTRACTS.md`:
```bash
WHOLETAKE_ASR=none python -m verdict.wt_adapter testdata/witness/talk.wav --witness-root testdata/witness --out /tmp/detection.json
```

### C. Seeding a Simulated X Clip & Inspecting Logs
```bash
# Seed a demo clip into simx
python -m simx seed-clip testdata/witness/talk.wav --as clipper --text "@wholetake check this clip"

# Tail Docker Compose logs
./deploy/deploy.sh logs

# Or inspect systemd user unit logs
journalctl --user -u wholetake-verdict -u wholetake-simx -u wholetake-bot -f
```

### D. Quarantine Recovery for Corrupted Witness Chunks
If `wholetake-chain` halts on an unchained corrupt chunk:
```bash
python -m ingest.chain --quarantine data/witness/<stream> <seq>
```
