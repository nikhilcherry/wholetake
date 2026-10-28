owns: anchor/__init__.py anchor/wallet.py anchor/eas.py anchor/ots.py anchor/daemon.py anchor/verify.py anchor/__main__.py tests/test_anchor.py deploy/wholetake-anchor.service deploy/README.md
context: ingest/chain.py deploy/README.md deploy/wholetake-chain.service

# Task: N2 anchoring — pin every stream's chain head on Base Sepolia (EAS) every minute, and in Bitcoin (OpenTimestamps) every hour

`ingest/chain.py` (shown) keeps `data/witness/<stream>/chain.jsonl` and a derived `head.json`
(`{"stream","n","hash","kinds","last_seq","updated_utc"}`). The anchor is what makes the record's
"existed by T" claim true for outsiders: once a head hash is on-chain, nobody (including us) can later
produce a different history up to that head. Read chain.py's proof/check_proof: the verdict step (N3,
later) will pick an anchored head that covers a chunk *and* its transcript, then hand out proof + tx.

## Verified facts (checked live 2026-10-29, don't re-derive)
- RPC https://sepolia.base.org, chain id 84532. EAS predeploy proxy 0x4200000000000000000000000000000000000021
  (same address on Base mainnet), version 1.4.1-beta.3, implementation has:
  `timestamp(bytes32) returns (uint64)` 0x4d003070, `multiTimestamp(bytes32[]) returns (uint64)` 0xe71ff365,
  `getTimestamp(bytes32) view returns (uint64)` 0xd45c4435 (0 = never timestamped),
  event `Timestamped(bytes32 indexed data, uint64 indexed timestamp)` topic0
  0x5aafceeb1c7ad58e4a84898bdee37c02c0fc46e7d24e6b60e8209449f183459f.
  Re-timestamping an already-timestamped value reverts (AlreadyTimestamped) — so never resend a hash that
  getTimestamp already reports; check before sending.
- web3 (v7) and opentimestamps-client (`.venv/bin/ots`, v0.8.0: `ots stamp FILE` -> FILE.ots,
  `ots upgrade FILE.ots`, `ots verify`) are installed in the venv. Python 3.12. No solc/anvil.
- Head hashes are sha256 hex (64 chars) -> bytes32 directly.

## Modules
- `anchor/wallet.py`: throwaway key at `<repo>/data/anchor.key` (env WT_ANCHOR_KEY_FILE), created only by
  `python -m anchor wallet new` (atomic, 0600, refuse to overwrite), `wallet address`, `wallet balance`.
  Never print or log the private key. Repo root = parents[1] of the module, never CWD-relative.
- `anchor/eas.py`: a small client class around web3: `get_timestamp(hash_hex) -> int`,
  `send_multi_timestamp(hashes) -> tx_hash` (EIP-1559: maxPriorityFee from the node, maxFee = 2*baseFee + prio,
  gas = estimate * 1.2), `receipt(tx) -> dict|None`, `parse_timestamped(receipt) -> {hash_hex: ts}`.
  Check chain id == WT_CHAIN_ID (default 84532) on connect; RPC from WT_RPC. Keep it injectable: the daemon
  takes any object with these methods so tests use a fake.
- `anchor/daemon.py`: `python -m anchor run [--root data/witness] [--every 60]`. Single instance (flock
  `<repo>/data/anchors/anchor.lock`). Each tick:
  1. For each stream dir with head.json: re-derive the head from chain.jsonl's last line (n + hash must equal
     head.json; on mismatch skip that stream with one loud log line per change — head.json is a convenience
     file, the log is the truth). Collect heads whose hash isn't anchored yet (local record) and whose
     getTimestamp is 0 (if it's already on-chain, just record it locally with that ts, no tx).
  2. If any: one multiTimestamp tx for all of them. At most one tx in flight: if the previous tx has no
     receipt after 180 s, replace it (same nonce, fees x1.3); after a receipt, record and continue. A reverted
     tx: log, re-check each hash with getTimestamp, retry next tick.
  3. Record per stream `<stream dir>/anchors.jsonl` lines:
     `{"n","head_hash","method":"eas.multiTimestamp","chain_id","eas","tx","block","onchain_ts","onchain_utc","sent_utc"}`
     (write + flush + fsync). onchain_ts comes from the Timestamped event, cross-checked with getTimestamp.
  4. Balance: log a warning when balance < 20 tx at the last gas used; when it can't pay, don't crash —
     log once every 10 min and keep ticking.
  RPC errors never kill the daemon: log, back off, next tick. Ctrl-C / SIGTERM: stop flag, finish the tick.
- `anchor/ots.py`: hourly (and on demand `python -m anchor ots`): write
  `data/anchors/ots/<YYYYMMDDTHH>Z.txt` with one line per stream `"<stream> <n> <head_hash>"` (latest
  EAS-anchored head per stream; sorted; skip if there's nothing new since the last file), `ots stamp` it,
  record `{file, sha256, streams:{stream: [n, hash]}, stamped_utc}` in data/anchors/ots.jsonl. Each hour also
  `ots upgrade` every .ots not yet upgraded (Bitcoin confirmation takes hours) and record when one completes.
  OTS failures are logged, never fatal. The daemon calls this; it's also usable alone.
- `anchor/verify.py` (N3 imports this; pure functions, network only through the injected client):
  - `anchored_heads(stream_dir) -> list[dict]` from anchors.jsonl, ascending n.
  - `best_anchor(stream_dir, seq) -> dict | None`: the earliest anchored head whose n >= the n of seq's
    *transcript* entry (chain.load), or None if the transcript isn't under any anchor yet.
  - `verify_anchor(stream_dir, rec, client) -> (ok, problems)`: getTimestamp(head_hash) on-chain == rec
    onchain_ts and != 0; entry n in chain.jsonl has exactly that hash.
  - `python -m anchor verify <stream_dir>`: verifies every anchors.jsonl line live, prints a summary,
    exit 0/1. Also prints which seqs are fully covered (chunk+transcript under an anchor) vs not yet.
- `anchor/__main__.py`: subcommands `wallet new|address|balance`, `run`, `ots`, `verify`.

## Tests `tests/test_anchor.py` (no network; build stream dirs with ingest.chain.StreamAppender on real files)
- A FakeEAS with an in-memory timestamp map and controllable failure modes (RPC error, no receipt, revert,
  already-timestamped). Cover: two streams batched into one tx; unchanged heads -> no tx; head.json tampered
  vs chain.jsonl -> that stream skipped, other anchored; hash already on-chain -> recorded without a tx;
  stuck tx replaced after 180 s with the same nonce and higher fee (inject the clock); RPC error -> no crash,
  retried next tick; insufficient funds -> no crash.
- best_anchor: chunk anchored but transcript not yet -> None; after the next anchor -> that anchor; picks
  the earliest qualifying. verify_anchor catches an edited anchors.jsonl ts and a hash not in the chain.
- ots: file content/sorting/skip-if-unchanged with `ots` subprocess mocked.
- wallet: key file 0600, refuses overwrite, key never appears in any printed output.

## Deploy
`deploy/wholetake-anchor.service` in the same style as the chain unit (Restart=on-failure, RestartSec=10).
Update deploy/README.md: add the anchor unit, `python -m anchor wallet new`, funding from a Base Sepolia faucet,
`python -m anchor verify`.

Style: match ingest/*.py (short docstring with usage at top, comments say why). No placeholders.
