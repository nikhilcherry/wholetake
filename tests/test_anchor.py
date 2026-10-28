"""Unit tests for N2 anchoring (wallet, FakeEAS daemon flows, verify/best_anchor, and OTS)."""
import json
import os
import stat
from pathlib import Path
from unittest.mock import patch

import pytest

from anchor import __main__ as anchor_main
from anchor import daemon, ots, verify, wallet
from ingest import chain


def _write_chunk(stream_dir: Path, seq: int, wav_bytes: bytes = b"RIFFfakewav", duration_s: float = 10.0) -> str:
    stream_dir.mkdir(parents=True, exist_ok=True)
    wav_p = stream_dir / f"{seq:06d}.wav"
    meta_p = stream_dir / f"{seq:06d}.meta.json"
    wav_p.write_bytes(wav_bytes)
    wav_sha = chain.sha256_bytes(wav_bytes)
    meta = {
        "seq": seq,
        "stream": stream_dir.name,
        "source": f"https://example.com/{stream_dir.name}",
        "received_utc": "2026-10-29T12:00:00.000Z",
        "duration_s": duration_s,
        "sha256": wav_sha,
    }
    meta_p.write_text(json.dumps(meta), encoding="utf-8")
    return wav_sha


def _write_transcript(stream_dir: Path, seq: int, wav_sha: str, text: str = "hello world"):
    tx_p = stream_dir / f"{seq:06d}.json"
    mel_p = stream_dir / f"{seq:06d}.mel.npy"
    mel_bytes = f"mel-{seq}-{text}".encode("utf-8")
    mel_p.write_bytes(mel_bytes)
    mel_sha = chain.sha256_bytes(mel_bytes)
    tx = {
        "seq": seq,
        "wav_sha256": wav_sha,
        "mel_sha256": mel_sha,
        "text": text,
    }
    tx_p.write_text(json.dumps(tx), encoding="utf-8")


def _step_appender(stream_dir: Path):
    app = chain.StreamAppender(stream_dir)
    try:
        app.step()
    finally:
        app.close()


class FakeEAS:
    """In-memory fake EAS client modeling nonces, pending pool, and controllable failure modes."""

    def __init__(self):
        self.chain_id = 84532
        self.eas_address = "0x4200000000000000000000000000000000000021"
        self.timestamps: dict[str, int] = {}
        self.sent_txs: list[dict] = []
        self.tx_by_hash: dict[str, dict] = {}
        self.receipts: dict[str, dict] = {}
        self.mined_nonce_count = 0
        self.pending_nonce_count = 0
        self.next_block = 1000
        self.current_ts = 1_761_740_000
        self.balance = 10**18  # 1 ETH

        # Failure mode switches
        self.fail_rpc = False
        self.fail_send: Exception | None = None
        self.hold_receipt = False
        self.revert_next = False
        self.insufficient_funds = False

    def balance_wei(self, _address: str | None = None) -> int:
        if self.fail_rpc:
            raise ConnectionError("simulated RPC error in balance_wei")
        return self.balance

    def nonce_mined(self, nonce: int) -> bool:
        if self.fail_rpc:
            raise ConnectionError("simulated RPC error in nonce_mined")
        return self.mined_nonce_count > nonce

    def get_timestamp(self, hash_hex: str) -> int:
        if self.fail_rpc:
            raise ConnectionError("simulated RPC error in get_timestamp")
        return self.timestamps.get(hash_hex.lower(), 0)

    def mine_tx(self, tx_hash: str, revert: bool = False):
        """Explicitly mine a previously sent tx_hash, consuming its nonce."""
        rec = self.tx_by_hash[tx_hash]
        hashes = rec["hashes"]
        max_fee = rec["maxFeePerGas"]
        nonce = rec["nonce"]

        if revert or any(self.timestamps.get(h.lower(), 0) != 0 for h in hashes):
            self.receipts[tx_hash] = {
                "status": 0,
                "blockNumber": self.next_block,
                "gasUsed": 50_000,
                "effectiveGasPrice": max_fee,
                "logs": [],
            }
        else:
            self.current_ts += 10
            parsed_map = {}
            for h in hashes:
                self.timestamps[h.lower()] = self.current_ts
                parsed_map[h.lower()] = self.current_ts
            self.receipts[tx_hash] = {
                "status": 1,
                "blockNumber": self.next_block,
                "gasUsed": 45_000,
                "effectiveGasPrice": max_fee,
                "_parsed": parsed_map,
            }

        self.next_block += 1
        self.mined_nonce_count = max(self.mined_nonce_count, nonce + 1)
        self.pending_nonce_count = max(self.pending_nonce_count, self.mined_nonce_count)

    def send_multi_timestamp(
        self,
        hashes: list[str],
        nonce: int | None = None,
        fee_multiplier: float = 1.0,
        min_priority_fee: int | None = None,
        min_max_fee: int | None = None,
    ) -> tuple[str, dict]:
        if self.fail_rpc:
            raise ConnectionError("simulated RPC error in send_multi_timestamp")
        if self.insufficient_funds:
            raise RuntimeError("insufficient funds for gas * price + value")
        if self.fail_send is not None:
            raise self.fail_send

        used_nonce = self.pending_nonce_count if nonce is None else nonce
        if used_nonce < self.mined_nonce_count:
            raise RuntimeError(f"nonce too low: next nonce {self.mined_nonce_count}, tx nonce {used_nonce}")

        if nonce is None:
            self.pending_nonce_count = max(self.pending_nonce_count, used_nonce + 1)

        prio = max(int(1_000_000 * fee_multiplier), min_priority_fee or 0)
        max_fee = max(int(3_000_000 * fee_multiplier), min_max_fee or 0, prio)
        tx_hash = f"0x{len(self.sent_txs) + 1:064x}"
        meta = {
            "nonce": used_nonce,
            "gas": 50_000,
            "maxPriorityFeePerGas": prio,
            "maxFeePerGas": max_fee,
        }
        rec = {"tx_hash": tx_hash, "hashes": list(hashes), **meta}
        self.sent_txs.append(rec)
        self.tx_by_hash[tx_hash] = rec

        if self.hold_receipt:
            return tx_hash, meta

        do_revert = self.revert_next
        self.revert_next = False
        self.mine_tx(tx_hash, revert=do_revert)
        return tx_hash, meta

    def receipt(self, tx_hash: str) -> dict | None:
        if self.fail_rpc:
            raise ConnectionError("simulated RPC error in receipt")
        return self.receipts.get(tx_hash)

    def parse_timestamped(self, rcpt: dict) -> dict[str, int]:
        return dict(rcpt.get("_parsed", {}))


def test_wallet_permissions_no_overwrite_and_no_leak(tmp_path: Path, monkeypatch, capsys):
    key_file = tmp_path / "anchor.key"
    monkeypatch.setenv("WT_ANCHOR_KEY_FILE", str(key_file))

    rc = anchor_main.main(["wallet", "new"])
    assert rc == 0
    out_new = capsys.readouterr().out.strip()
    assert out_new.startswith("0x") and len(out_new) == 42

    mode = stat.S_IMODE(os.stat(key_file).st_mode)
    assert mode == 0o600

    secret_hex = key_file.read_text(encoding="ascii").strip()
    assert len(secret_hex) == 66
    assert secret_hex not in out_new
    assert secret_hex[2:] not in out_new

    # Refuse overwrite
    rc2 = anchor_main.main(["wallet", "new"])
    assert rc2 == 1
    captured2 = capsys.readouterr()
    assert secret_hex not in captured2.out and secret_hex not in captured2.err
    assert secret_hex[2:] not in captured2.out and secret_hex[2:] not in captured2.err

    # Address matches and never leaks private key
    rc3 = anchor_main.main(["wallet", "address"])
    assert rc3 == 0
    captured3 = capsys.readouterr()
    assert captured3.out.strip() == out_new
    assert secret_hex not in captured3.out and secret_hex[2:] not in captured3.out


def test_daemon_batches_two_streams_and_skips_unchanged(tmp_path: Path):
    w_root = tmp_path / "witness"
    s1 = w_root / "alpha"
    s2 = w_root / "beta"
    _write_chunk(s1, 0, b"wav-alpha-0")
    _write_chunk(s2, 0, b"wav-beta-0")
    _step_appender(s1)
    _step_appender(s2)

    fake = FakeEAS()
    now = [1000.0]
    d = daemon.AnchorDaemon(
        root=w_root,
        client=fake,
        lock_path=tmp_path / "anchor.lock",
        clock=lambda: now[0],
        ots_runner=lambda _: None,
    )
    try:
        d.step()
        assert len(fake.sent_txs) == 1
        assert len(fake.sent_txs[0]["hashes"]) == 2

        a1 = verify.anchored_heads(s1)
        a2 = verify.anchored_heads(s2)
        assert len(a1) == 1 and a1[0]["n"] == 1
        assert len(a2) == 1 and a2[0]["n"] == 1
        assert a1[0]["tx"] == fake.sent_txs[0]["tx_hash"]
        assert a2[0]["tx"] == fake.sent_txs[0]["tx_hash"]

        # Next tick with unchanged heads -> no new tx
        now[0] += 60.0
        d.step()
        assert len(fake.sent_txs) == 1
    finally:
        d.close()


def test_daemon_tampered_head_json_skipped_other_anchored(tmp_path: Path, capsys):
    w_root = tmp_path / "witness"
    s_bad = w_root / "bad_stream"
    s_good = w_root / "good_stream"
    _write_chunk(s_bad, 0, b"wav-bad")
    _write_chunk(s_good, 0, b"wav-good")
    _step_appender(s_bad)
    _step_appender(s_good)

    # Tamper with bad_stream's head.json
    head_p = s_bad / "head.json"
    tampered = json.loads(head_p.read_text())
    tampered["hash"] = "f" * 64
    head_p.write_text(json.dumps(tampered))

    fake = FakeEAS()
    d = daemon.AnchorDaemon(
        root=w_root,
        client=fake,
        lock_path=tmp_path / "anchor.lock",
        clock=lambda: 1000.0,
        ots_runner=lambda _: None,
    )
    try:
        d.step()
        err_1 = capsys.readouterr().err
        assert "head.json mismatch" in err_1
        assert len(fake.sent_txs) == 1
        assert len(fake.sent_txs[0]["hashes"]) == 1
        assert verify.anchored_heads(s_bad) == []
        assert len(verify.anchored_heads(s_good)) == 1

        # Second step with same mismatch does not spam duplicate log line
        d.step()
        err_2 = capsys.readouterr().err
        assert "head.json mismatch" not in err_2
    finally:
        d.close()


def test_daemon_hash_already_onchain_recorded_without_tx(tmp_path: Path):
    w_root = tmp_path / "witness"
    s1 = w_root / "pre_anchored"
    _write_chunk(s1, 0, b"wav-pre")
    _step_appender(s1)

    head_hash = json.loads((s1 / "head.json").read_text())["hash"]
    fake = FakeEAS()
    fake.timestamps[head_hash.lower()] = 1_761_700_555

    d = daemon.AnchorDaemon(
        root=w_root,
        client=fake,
        lock_path=tmp_path / "anchor.lock",
        clock=lambda: 1000.0,
        ots_runner=lambda _: None,
    )
    try:
        d.step()
        assert len(fake.sent_txs) == 0
        recs = verify.anchored_heads(s1)
        assert len(recs) == 1
        assert recs[0]["head_hash"] == head_hash
        assert recs[0]["onchain_ts"] == 1_761_700_555
        assert recs[0]["tx"] is None
        assert recs[0]["tx_unknown"] is True
    finally:
        d.close()


def test_daemon_stuck_tx_replaced_after_180s_same_nonce_higher_fee(tmp_path: Path):
    w_root = tmp_path / "witness"
    s1 = w_root / "stuck_stream"
    _write_chunk(s1, 0, b"wav-stuck")
    _step_appender(s1)

    fake = FakeEAS()
    fake.hold_receipt = True
    now = [1000.0]

    d = daemon.AnchorDaemon(
        root=w_root,
        client=fake,
        lock_path=tmp_path / "anchor.lock",
        clock=lambda: now[0],
        ots_runner=lambda _: None,
    )
    try:
        d.step()
        assert len(fake.sent_txs) == 1
        first_tx = fake.sent_txs[0]

        # At +120s: still waiting, not replaced yet
        now[0] += 120.0
        d.step()
        assert len(fake.sent_txs) == 1

        # At +185s: replaced with same nonce and bumped fees, now allow receipt
        now[0] += 65.0
        fake.hold_receipt = False
        d.step()
        assert len(fake.sent_txs) == 2
        second_tx = fake.sent_txs[1]
        assert second_tx["nonce"] == first_tx["nonce"]
        assert second_tx["maxPriorityFeePerGas"] >= int(first_tx["maxPriorityFeePerGas"] * 1.3)
        assert second_tx["maxFeePerGas"] >= int(first_tx["maxFeePerGas"] * 1.3)

        # Next step processes the receipt of the replacement tx
        now[0] += 5.0
        d.step()
        recs = verify.anchored_heads(s1)
        assert len(recs) == 1
        assert recs[0]["tx"] == second_tx["tx_hash"]
    finally:
        d.close()


def test_replacement_race_original_mined_after_replacement_sent(tmp_path: Path):
    """Test 1: original tx is mined after replacement was sent -> settled from original's receipt, next tick anchors newer head."""
    w_root = tmp_path / "witness"
    s1 = w_root / "race_stream"
    _write_chunk(s1, 0, b"wav-race-0")
    _step_appender(s1)

    fake = FakeEAS()
    fake.hold_receipt = True
    now = [1000.0]

    d = daemon.AnchorDaemon(
        root=w_root,
        client=fake,
        lock_path=tmp_path / "anchor.lock",
        clock=lambda: now[0],
        ots_runner=lambda _: None,
    )
    try:
        # Tick 1: sends tx1 (nonce 0)
        d.step()
        assert len(fake.sent_txs) == 1
        tx1_hash = fake.sent_txs[0]["tx_hash"]

        # Tick 2 (+185s): tx1 still unmined, sends replacement tx2 (nonce 0), both held
        now[0] += 185.0
        d.step()
        assert len(fake.sent_txs) == 2
        tx2_hash = fake.sent_txs[1]["tx_hash"]
        assert d.in_flight is not None
        assert d.in_flight.tx_hashes == [tx1_hash, tx2_hash]

        # Now original tx1 gets mined (not tx2!) and a new chunk arrives on disk
        fake.mine_tx(tx1_hash)
        fake.hold_receipt = False
        _write_chunk(s1, 1, b"wav-race-1")
        _step_appender(s1)

        # Tick 3 (+60s): settles nonce 0 from tx1's receipt AND immediately anchors the new head (n=2) with nonce 1
        now[0] += 60.0
        d.step()
        assert d.in_flight is None
        assert len(fake.sent_txs) == 3
        tx3 = fake.sent_txs[2]
        assert tx3["nonce"] == 1

        recs = verify.anchored_heads(s1)
        assert len(recs) == 2
        assert recs[0]["n"] == 1 and recs[0]["tx"] == tx1_hash
        assert recs[1]["n"] == 2 and recs[1]["tx"] == tx3["tx_hash"]
    finally:
        d.close()


def test_replacement_send_raises_nonce_too_low_recovers(tmp_path: Path):
    """Test 2: original tx is mined right as replacement is attempted (or receipt was missed) -> recovers and anchors next head."""
    w_root = tmp_path / "witness"
    s1 = w_root / "nonce_low_stream"
    _write_chunk(s1, 0, b"wav-nl-0")
    _step_appender(s1)

    fake = FakeEAS()
    fake.hold_receipt = True
    now = [1000.0]

    d = daemon.AnchorDaemon(
        root=w_root,
        client=fake,
        lock_path=tmp_path / "anchor.lock",
        clock=lambda: now[0],
        ots_runner=lambda _: None,
    )
    try:
        # Tick 1: sends tx1 (nonce 0)
        d.step()
        tx1_hash = fake.sent_txs[0]["tx_hash"]

        # Simulate tx1 being mined on-chain right when replacement is about to be sent
        # (Say receipt lookup returned None at the top of _handle_in_flight, then mined before send_multi_timestamp)
        orig_nonce_mined = fake.nonce_mined
        calls = [0]

        def delayed_nonce_mined(n: int) -> bool:
            calls[0] += 1
            # First check (before send) returns False; right before send, tx1 mines so send raises "nonce too low"
            if calls[0] == 1:
                fake.mine_tx(tx1_hash)
                return False
            return orig_nonce_mined(n)

        fake.nonce_mined = delayed_nonce_mined  # type: ignore[method-assign]
        fake.hold_receipt = False

        # Advance stream to n=2 as well
        _write_chunk(s1, 1, b"wav-nl-1")
        _step_appender(s1)

        # Tick 2 (+185s): replacement send raises "nonce too low", daemon detects nonce mined, settles n=1, and anchors n=2
        now[0] += 185.0
        d.step()
        assert d.in_flight is None

        recs = verify.anchored_heads(s1)
        assert len(recs) == 2
        assert recs[0]["n"] == 1 and recs[0]["tx"] == tx1_hash
        assert recs[1]["n"] == 2
    finally:
        d.close()


def test_daemon_restarted_with_pending_tx_from_previous_process_then_mined(tmp_path: Path):
    """Test 3: daemon restarts while a tx from the previous process is still pending, then mines -> no wedge, no duplicates."""
    w_root = tmp_path / "witness"
    s1 = w_root / "restart_stream"
    _write_chunk(s1, 0, b"wav-restart-0")
    _step_appender(s1)

    fake = FakeEAS()
    fake.hold_receipt = True
    now = [1000.0]

    # Process 1 sends tx1 (nonce 0, still pending in mempool) and then exits/restarts
    d1 = daemon.AnchorDaemon(
        root=w_root,
        client=fake,
        lock_path=tmp_path / "anchor.lock",
        clock=lambda: now[0],
        ots_runner=lambda _: None,
    )
    try:
        d1.step()
        assert len(fake.sent_txs) == 1
        tx1_hash = fake.sent_txs[0]["tx_hash"]
        assert verify.anchored_heads(s1) == []
    finally:
        d1.close()

    # Process 2 starts fresh (in_flight is None). Suppose Process 2 sends a tx (or tx1 mines while Process 2 is running)
    d2 = daemon.AnchorDaemon(
        root=w_root,
        client=fake,
        lock_path=tmp_path / "anchor.lock",
        clock=lambda: now[0],
        ots_runner=lambda _: None,
    )
    try:
        # Case A: tx1 from process 1 is still pending when d2 ticks, so d2 sends tx2, then tx1 mines!
        now[0] += 10.0
        d2.step()
        assert len(fake.sent_txs) == 2

        # Now tx1 (unknown to d2's in_flight) mines on-chain, consuming nonce 0 and setting timestamp on EAS
        fake.mine_tx(tx1_hash)
        # Suppose d2's tx2 also shared nonce 0 (e.g. if node's pending count hadn't seen tx1 or was replaced)
        assert d2.in_flight is not None
        d2.in_flight.nonce = 0

        # After 185s, d2 sees nonce 0 is mined even though tx2 has no receipt; falls back to getTimestamp
        now[0] += 185.0
        fake.hold_receipt = False
        d2.step()
        assert d2.in_flight is None

        recs = verify.anchored_heads(s1)
        assert len(recs) == 1
        assert recs[0]["n"] == 1
        assert recs[0]["tx"] is None
        assert recs[0]["tx_unknown"] is True

        # Subsequent ticks do not write duplicate anchor lines for the same head
        now[0] += 60.0
        d2.step()
        assert len(verify.anchored_heads(s1)) == 1
    finally:
        d2.close()


def test_replacement_repeated_failures_unwedges_after_3_attempts(tmp_path: Path):
    """After 3 consecutive replacement failures for the same nonce, in_flight is cleared via fallback."""
    w_root = tmp_path / "witness"
    s1 = w_root / "unwedge_stream"
    _write_chunk(s1, 0, b"wav-unwedge-0")
    _step_appender(s1)

    fake = FakeEAS()
    fake.hold_receipt = True
    now = [1000.0]

    d = daemon.AnchorDaemon(
        root=w_root,
        client=fake,
        lock_path=tmp_path / "anchor.lock",
        clock=lambda: now[0],
        ots_runner=lambda _: None,
    )
    try:
        d.step()
        assert d.in_flight is not None

        fake.fail_send = RuntimeError("replacement underpriced")
        now[0] += 185.0
        d.step()
        assert d.in_flight is not None and d.in_flight.replace_failures == 1

        now[0] += 60.0
        d.step()
        assert d.in_flight is not None and d.in_flight.replace_failures == 2

        now[0] += 60.0
        d.step()
        assert d.in_flight is None  # Unwedged after 3rd consecutive failure

        # Once RPC/mempool recovers, next tick anchors cleanly
        fake.fail_send = None
        fake.hold_receipt = False
        now[0] += 60.0
        d.step()
        assert len(verify.anchored_heads(s1)) == 1
    finally:
        d.close()


def test_daemon_rpc_error_and_insufficient_funds_recover(tmp_path: Path):
    w_root = tmp_path / "witness"
    s1 = w_root / "resilient"
    _write_chunk(s1, 0, b"wav-resilient")
    _step_appender(s1)

    fake = FakeEAS()
    now = [1000.0]
    d = daemon.AnchorDaemon(
        root=w_root,
        client=fake,
        lock_path=tmp_path / "anchor.lock",
        clock=lambda: now[0],
        ots_runner=lambda _: None,
    )
    try:
        # 1. RPC error -> no crash
        fake.fail_rpc = True
        d.step()
        assert len(fake.sent_txs) == 0

        # 2. Insufficient funds -> no crash
        fake.fail_rpc = False
        fake.insufficient_funds = True
        now[0] += 60.0
        d.step()
        assert len(fake.sent_txs) == 0

        # 3. Zero wallet balance -> no crash, skipped before send
        fake.insufficient_funds = False
        fake.balance = 0
        now[0] += 60.0
        d.step()
        assert len(fake.sent_txs) == 0

        # 4. Restored balance -> succeeds on next tick
        fake.balance = 10**18
        now[0] += 60.0
        d.step()
        assert len(fake.sent_txs) == 1
        assert len(verify.anchored_heads(s1)) == 1
    finally:
        d.close()


def test_best_anchor_and_verify_anchor(tmp_path: Path):
    w_root = tmp_path / "witness"
    s1 = w_root / "verifiable"
    wav_sha0 = _write_chunk(s1, 0, b"wav-0")
    _step_appender(s1)  # n=0 genesis, n=1 chunk 0

    fake = FakeEAS()
    now = [1000.0]
    d = daemon.AnchorDaemon(
        root=w_root,
        client=fake,
        lock_path=tmp_path / "anchor.lock",
        clock=lambda: now[0],
        ots_runner=lambda _: None,
    )
    try:
        # Anchor #1 covers chunk 0 (n=1), but transcript 0 is not chained yet
        d.step()
        assert verify.best_anchor(s1, 0) is None

        # Add transcript for seq 0 -> chained at n=2, and anchor #2
        _write_transcript(s1, 0, wav_sha0, "transcript zero")
        _step_appender(s1)
        now[0] += 60.0
        d.step()

        # Add chunk 1 -> chained at n=3, and anchor #3
        _write_chunk(s1, 1, b"wav-1")
        _step_appender(s1)
        now[0] += 60.0
        d.step()

        # best_anchor for seq 0 must pick the earliest qualifying anchor (n=2, not n=3)
        best = verify.best_anchor(s1, 0)
        assert best is not None
        assert best["n"] == 2
        assert verify.best_anchor(s1, 1) is None  # seq 1 has no transcript yet

        # Proof for seq 0 up to best["n"] passes check_proof
        prf = chain.proof(s1, 0, head_n=best["n"])
        assert chain.check_proof(prf, best["head_hash"])

        # Live verification passes
        ok, problems = verify.verify_anchor(s1, best, fake)
        assert ok and not problems
        assert verify.run_verify_cli(s1, fake) == 0

        # Tamper with onchain_ts -> caught by verify_anchor
        bad_ts_rec = {**best, "onchain_ts": best["onchain_ts"] + 999}
        ok_ts, probs_ts = verify.verify_anchor(s1, bad_ts_rec, fake)
        assert not ok_ts
        assert any("onchain_ts mismatch" in p for p in probs_ts)

        # Tamper with head_hash -> caught by verify_anchor
        bad_hash_rec = {**best, "head_hash": "a" * 64}
        ok_h, probs_h = verify.verify_anchor(s1, bad_hash_rec, fake)
        assert not ok_h
        assert any("head_hash mismatch" in p for p in probs_h)
    finally:
        d.close()


def test_ots_sorting_skip_unchanged_and_upgrade(tmp_path: Path):
    w_root = tmp_path / "witness"
    ots_dir = tmp_path / "anchors" / "ots"
    ots_log = tmp_path / "anchors" / "ots.jsonl"

    # Create two streams out of alphabetical order with anchored heads
    for name, n, h in [("zeta", 5, "b" * 64), ("alpha", 2, "a" * 64)]:
        sd = w_root / name
        sd.mkdir(parents=True, exist_ok=True)
        (sd / "anchors.jsonl").write_text(json.dumps({"n": n, "head_hash": h}) + "\n")

    calls = []

    def fake_run(cmd, **_kwargs):
        calls.append(cmd)
        if cmd[1] == "stamp":
            txt_p = Path(cmd[2])
            Path(str(txt_p) + ".ots").write_bytes(b"fake-ots-proof")

            class Proc:
                returncode = 0
                stdout = "Submitting to remote calendar"
                stderr = ""

            return Proc()
        elif cmd[1] == "upgrade":

            class Proc:
                returncode = 0
                stdout = "Success! Timestamp complete"
                stderr = ""

            return Proc()
        raise AssertionError(f"unexpected command {cmd}")

    with patch("anchor.ots.subprocess.run", side_effect=fake_run):
        res1 = ots.run_ots_cycle(witness_root=w_root, ots_dir=ots_dir, log_path=ots_log, ots_bin="ots")
        assert res1["stamped"] is not None
        txt_file = Path(res1["stamped"]["file"])
        assert txt_file.read_text(encoding="utf-8") == f"alpha 2 {'a' * 64}\nzeta 5 {'b' * 64}\n"
        assert len(res1["upgraded"]) == 1

        # Second run with unchanged heads -> skips stamping and skips already-upgraded .ots
        calls.clear()
        res2 = ots.run_ots_cycle(witness_root=w_root, ots_dir=ots_dir, log_path=ots_log, ots_bin="ots")
        assert res2["stamped"] is None
        assert res2["upgraded"] == []
        assert calls == []
