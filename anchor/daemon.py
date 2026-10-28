"""Single-instance daemon pinning stream heads to Base Sepolia EAS every minute and OTS every hour.

    python -m anchor run [--root data/witness] [--every 60]
"""
import dataclasses
import fcntl
import json
import os
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from anchor import ots
from anchor.eas import DEFAULT_CHAIN_ID, EAS_ADDRESS
from ingest.chain import canonical_json

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LOCK_PATH = REPO_ROOT / "data" / "anchors" / "anchor.lock"
STUCK_TX_TIMEOUT_S = 180.0
FEE_BUMP_FACTOR = 1.3
MAX_REPLACE_FAILURES = 3
LOW_BALANCE_WARN_INTERVAL_S = 600.0
OTS_INTERVAL_S = 3600.0
DEFAULT_GAS_ESTIMATE = 60_000


def _iso_utc(ts: float | int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _now_iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _read_last_chain_entry(chain_path: Path) -> dict | None:
    if not chain_path.exists():
        return None
    raw = chain_path.read_bytes()
    if not raw:
        return None
    for line in reversed(raw.splitlines()):
        if line.strip():
            return json.loads(line.decode("utf-8"))
    return None


def _load_anchored_hashes(stream_dir: Path) -> set[str]:
    p = stream_dir / "anchors.jsonl"
    if not p.exists():
        return set()
    out: set[str] = set()
    for line in p.read_bytes().splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line.decode("utf-8"))
            h = rec.get("head_hash")
            if isinstance(h, str):
                out.add(h.lower())
        except Exception:
            continue
    return out


def _append_anchor_record(stream_dir: Path, rec: dict):
    p = stream_dir / "anchors.jsonl"
    line = (canonical_json(rec) + "\n").encode("utf-8")
    with open(p, "ab") as f:
        f.write(line)
        f.flush()
        os.fsync(f.fileno())


def _is_insufficient_funds(exc: Exception) -> bool:
    msg = str(exc).lower()
    return "insufficient funds" in msg or "insufficient balance" in msg or "not enough funds" in msg


@dataclasses.dataclass
class PendingTarget:
    stream_dir: Path
    n: int
    head_hash: str


@dataclasses.dataclass
class InFlightTx:
    tx_hashes: list[str]
    nonce: int
    sent_mono: float
    sent_utc: str
    targets: list[PendingTarget]
    fee_multiplier: float
    max_priority_fee: int
    max_fee: int
    replace_failures: int = 0

    @property
    def tx_hash(self) -> str:
        return self.tx_hashes[-1]


class AnchorDaemon:
    """Coordinates periodic EAS multiTimestamp transactions and hourly OTS batches."""

    def __init__(
        self,
        root: Path | str,
        client: Any,
        lock_path: Path | str = DEFAULT_LOCK_PATH,
        clock: Callable[[], float] = time.time,
        ots_runner: Callable[[Path], Any] | None = None,
    ):
        self.root = Path(root)
        self.client = client
        self.lock_path = Path(lock_path)
        self.clock = clock
        self.ots_runner = ots_runner

        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        self.lock_fd = open(self.lock_path, "a+b")
        try:
            fcntl.flock(self.lock_fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.lock_fd.close()
            self.lock_fd = None
            raise BlockingIOError(f"anchor.lock already held at {self.lock_path}")

        self.in_flight: InFlightTx | None = None
        self.mismatch_state: dict[Path, tuple[Any, Any, Any, Any]] = {}
        self.last_gas_used: int = DEFAULT_GAS_ESTIMATE
        self.last_gas_price: int = 2_000_000_000
        self.last_no_funds_log: float = -1e9
        self.last_low_bal_log: float = -1e9
        self.last_ots_time: float = -1e9
        self.stop_requested = False

    def close(self):
        if getattr(self, "lock_fd", None) is not None:
            try:
                fcntl.flock(self.lock_fd.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass
            self.lock_fd.close()
            self.lock_fd = None

    def _record_anchor(
        self,
        target: PendingTarget,
        onchain_ts: int,
        sent_utc: str,
        tx_hash: str | None,
        block_num: int | None,
        tx_unknown: bool = False,
    ):
        # Avoid duplicate lines for the same head_hash in anchors.jsonl
        if target.head_hash.lower() in _load_anchored_hashes(target.stream_dir):
            return
        chain_id = getattr(self.client, "chain_id", DEFAULT_CHAIN_ID)
        eas_addr = getattr(self.client, "eas_address", EAS_ADDRESS)
        rec = {
            "n": target.n,
            "head_hash": target.head_hash,
            "method": "eas.multiTimestamp",
            "chain_id": chain_id,
            "eas": eas_addr,
            "tx": tx_hash,
            "block": block_num,
            "onchain_ts": int(onchain_ts),
            "onchain_utc": _iso_utc(int(onchain_ts)),
            "sent_utc": sent_utc,
        }
        if tx_unknown:
            rec["tx_unknown"] = True
        _append_anchor_record(target.stream_dir, rec)

    def _check_balance(self, now: float) -> bool:
        """Check wallet balance if supported by client. Returns False if definitely unable to pay for 1 tx."""
        if not hasattr(self.client, "balance_wei"):
            return True
        try:
            bal = int(self.client.balance_wei())
        except Exception as e:
            print(f"WARN [anchor] failed to query wallet balance: {e}", file=sys.stderr, flush=True)
            return True

        one_tx_cost = self.last_gas_used * self.last_gas_price
        if bal < one_tx_cost and one_tx_cost > 0:
            if now - self.last_no_funds_log >= LOW_BALANCE_WARN_INTERVAL_S:
                print(
                    f"ERROR [anchor] insufficient balance ({bal} wei < ~{one_tx_cost} wei per tx); waiting for funding",
                    file=sys.stderr,
                    flush=True,
                )
                self.last_no_funds_log = now
            return False

        if bal < 20 * one_tx_cost:
            if now - self.last_low_bal_log >= LOW_BALANCE_WARN_INTERVAL_S:
                print(
                    f"WARN [anchor] low wallet balance: {bal} wei (< 20 txs at last gas {self.last_gas_used})",
                    file=sys.stderr,
                    flush=True,
                )
                self.last_low_bal_log = now
        return True

    def _settle_from_receipt(self, tx: InFlightTx, settled_hash: str, rcpt: dict) -> bool:
        """Settle in_flight from a mined receipt. Returns True if succeeded, False if reverted."""
        self.in_flight = None
        gas_used = rcpt.get("gasUsed")
        eff_price = rcpt.get("effectiveGasPrice")
        if isinstance(gas_used, int) and gas_used > 0:
            self.last_gas_used = gas_used
        if isinstance(eff_price, int) and eff_price > 0:
            self.last_gas_price = eff_price

        status = rcpt.get("status", 1)
        block_num = rcpt.get("blockNumber")
        if status == 1:
            parsed = self.client.parse_timestamped(rcpt)
            for t in tx.targets:
                h_low = t.head_hash.lower()
                ev_ts = parsed.get(h_low)
                chain_ts = int(self.client.get_timestamp(t.head_hash))
                ts = ev_ts if (ev_ts is not None and ev_ts == chain_ts and chain_ts > 0) else (chain_ts or ev_ts or 0)
                if ts > 0:
                    self._record_anchor(t, ts, tx.sent_utc, settled_hash, block_num)
                else:
                    print(
                        f"WARN [anchor] tx {settled_hash} succeeded but hash {t.head_hash} has ts=0",
                        file=sys.stderr,
                        flush=True,
                    )
            return True
        else:
            print(
                f"WARN [anchor] tx {settled_hash} reverted; re-checking hashes via getTimestamp",
                file=sys.stderr,
                flush=True,
            )
            for t in tx.targets:
                chain_ts = int(self.client.get_timestamp(t.head_hash))
                if chain_ts > 0:
                    self._record_anchor(t, chain_ts, tx.sent_utc, None, None, tx_unknown=True)
            return False

    def _fallback_settle_via_get_timestamp(self, tx: InFlightTx):
        """Settle in_flight when the nonce is consumed or replacement failed repeatedly without a known receipt."""
        self.in_flight = None
        for h in tx.tx_hashes:
            rcpt = self.client.receipt(h)
            if rcpt is not None:
                self._settle_from_receipt(tx, h, rcpt)
                return

        for t in tx.targets:
            chain_ts = int(self.client.get_timestamp(t.head_hash))
            if chain_ts > 0:
                self._record_anchor(t, chain_ts, tx.sent_utc, tx_hash=None, block_num=None, tx_unknown=True)

    def _is_nonce_mined(self, nonce: int) -> bool:
        if hasattr(self.client, "nonce_mined"):
            return bool(self.client.nonce_mined(nonce))
        return False

    def _handle_in_flight(self, now: float) -> bool:
        """Check or replace an in-flight tx. Returns True if cleared and tick can proceed, False if still waiting."""
        tx = self.in_flight
        if tx is None:
            return True

        # 1. Check receipts across ALL tx hashes sent for this nonce
        for candidate_hash in tx.tx_hashes:
            rcpt = self.client.receipt(candidate_hash)
            if rcpt is not None:
                return self._settle_from_receipt(tx, candidate_hash, rcpt)

        # 2. Not mined yet: wait until STUCK_TX_TIMEOUT_S before replacing
        elapsed = now - tx.sent_mono
        if elapsed < STUCK_TX_TIMEOUT_S:
            return False

        # 3. Before replacing, check if the nonce was already mined (e.g. by an earlier or unknown tx)
        if self._is_nonce_mined(tx.nonce):
            print(
                f"WARN [anchor] nonce {tx.nonce} already mined while {tx.tx_hashes} had no receipt; settling via fallback",
                file=sys.stderr,
                flush=True,
            )
            self._fallback_settle_via_get_timestamp(tx)
            return True

        # 4. Attempt replacement with same nonce and 1.3x fees
        new_mult = tx.fee_multiplier * FEE_BUMP_FACTOR
        min_prio = int(tx.max_priority_fee * FEE_BUMP_FACTOR) + 1
        min_max = int(tx.max_fee * FEE_BUMP_FACTOR) + 1
        hashes = list(dict.fromkeys(t.head_hash for t in tx.targets))
        print(
            f"WARN [anchor] tx {tx.tx_hash} stuck for {elapsed:.0f}s; replacing with nonce={tx.nonce} fee x{FEE_BUMP_FACTOR}",
            file=sys.stderr,
            flush=True,
        )
        sent_utc = _now_iso(now)
        try:
            res = self.client.send_multi_timestamp(
                hashes,
                nonce=tx.nonce,
                fee_multiplier=new_mult,
                min_priority_fee=min_prio,
                min_max_fee=min_max,
            )
        except Exception as e:
            # If the replacement failed because the nonce was just mined, settle immediately
            try:
                mined = self._is_nonce_mined(tx.nonce)
            except Exception:
                mined = False

            if mined:
                print(
                    f"WARN [anchor] replacement for nonce {tx.nonce} raised ({e}) because nonce is now mined; settling",
                    file=sys.stderr,
                    flush=True,
                )
                self._fallback_settle_via_get_timestamp(tx)
                return True

            tx.replace_failures += 1
            if tx.replace_failures >= MAX_REPLACE_FAILURES:
                print(
                    f"ERROR [anchor] replacement for nonce {tx.nonce} failed {tx.replace_failures} times ({e}); "
                    f"clearing in_flight via getTimestamp fallback",
                    file=sys.stderr,
                    flush=True,
                )
                try:
                    self._fallback_settle_via_get_timestamp(tx)
                except Exception:
                    self.in_flight = None
                return False

            print(
                f"WARN [anchor] replacement attempt {tx.replace_failures}/{MAX_REPLACE_FAILURES} for nonce {tx.nonce} failed: {e}",
                file=sys.stderr,
                flush=True,
            )
            return False

        new_hash, meta = self._unpack_send_result(res, tx.nonce, min_prio, min_max)
        all_hashes = list(tx.tx_hashes)
        if new_hash not in all_hashes:
            all_hashes.append(new_hash)

        self.in_flight = InFlightTx(
            tx_hashes=all_hashes,
            nonce=meta["nonce"],
            sent_mono=now,
            sent_utc=sent_utc,
            targets=tx.targets,
            fee_multiplier=new_mult,
            max_priority_fee=meta["maxPriorityFeePerGas"],
            max_fee=meta["maxFeePerGas"],
            replace_failures=0,
        )
        return False

    @staticmethod
    def _unpack_send_result(res: Any, default_nonce: int = 0, default_prio: int = 1000, default_max: int = 2000) -> tuple[str, dict]:
        if isinstance(res, tuple) and len(res) == 2 and isinstance(res[1], dict):
            tx_hash, meta = res
            return str(tx_hash), {
                "nonce": meta.get("nonce", default_nonce),
                "maxPriorityFeePerGas": meta.get("maxPriorityFeePerGas", default_prio),
                "maxFeePerGas": meta.get("maxFeePerGas", default_max),
                "gas": meta.get("gas", DEFAULT_GAS_ESTIMATE),
            }
        return str(res), {
            "nonce": default_nonce,
            "maxPriorityFeePerGas": default_prio,
            "maxFeePerGas": default_max,
            "gas": DEFAULT_GAS_ESTIMATE,
        }

    def _collect_candidates(self, now: float) -> list[PendingTarget]:
        if not self.root.exists():
            return []

        to_send: list[PendingTarget] = []
        sent_utc = _now_iso(now)

        for d in sorted(self.root.iterdir()):
            if not d.is_dir():
                continue
            head_p = d / "head.json"
            chain_p = d / "chain.jsonl"
            if not head_p.exists():
                continue

            try:
                head_obj = json.loads(head_p.read_text(encoding="utf-8"))
                last_entry = _read_last_chain_entry(chain_p)
            except Exception as e:
                print(f"ERROR [{d.name}] reading head/chain: {e}", file=sys.stderr, flush=True)
                continue

            if not last_entry:
                continue

            h_n = head_obj.get("n")
            h_hash = head_obj.get("hash")
            c_n = last_entry.get("n")
            c_hash = last_entry.get("hash")

            if h_n != c_n or h_hash != c_hash:
                state_tuple = (h_n, h_hash, c_n, c_hash)
                if self.mismatch_state.get(d) != state_tuple:
                    print(
                        f"ERROR [{d.name}] head.json mismatch vs chain.jsonl last line: "
                        f"head=({h_n}, {h_hash}) != chain=({c_n}, {c_hash}); skipping stream",
                        file=sys.stderr,
                        flush=True,
                    )
                    self.mismatch_state[d] = state_tuple
                continue
            else:
                self.mismatch_state.pop(d, None)

            if not isinstance(c_n, int) or not isinstance(c_hash, str) or len(c_hash) != 64:
                continue

            anchored = _load_anchored_hashes(d)
            if c_hash.lower() in anchored:
                continue

            target = PendingTarget(stream_dir=d, n=c_n, head_hash=c_hash)
            onchain_ts = int(self.client.get_timestamp(c_hash))
            if onchain_ts > 0:
                self._record_anchor(target, onchain_ts, sent_utc, tx_hash=None, block_num=None, tx_unknown=True)
                continue

            to_send.append(target)

        return to_send

    def step(self):
        """Execute one daemon tick. Catches and logs RPC/funding errors without crashing."""
        now = self.clock()
        try:
            if not self._handle_in_flight(now):
                self._maybe_run_ots(now)
                return

            candidates = self._collect_candidates(now)
            if candidates:
                if not self._check_balance(now):
                    self._maybe_run_ots(now)
                    return

                unique_hashes = list(dict.fromkeys(t.head_hash for t in candidates))
                sent_utc = _now_iso(now)
                try:
                    res = self.client.send_multi_timestamp(unique_hashes)
                except Exception as e:
                    if _is_insufficient_funds(e):
                        if now - self.last_no_funds_log >= LOW_BALANCE_WARN_INTERVAL_S:
                            print(f"ERROR [anchor] insufficient funds sending tx: {e}", file=sys.stderr, flush=True)
                            self.last_no_funds_log = now
                        self._maybe_run_ots(now)
                        return
                    raise

                tx_hash, meta = self._unpack_send_result(res)
                self.last_gas_used = meta.get("gas", self.last_gas_used)
                self.last_gas_price = meta.get("maxFeePerGas", self.last_gas_price)

                self.in_flight = InFlightTx(
                    tx_hashes=[tx_hash],
                    nonce=meta["nonce"],
                    sent_mono=now,
                    sent_utc=sent_utc,
                    targets=candidates,
                    fee_multiplier=1.0,
                    max_priority_fee=meta["maxPriorityFeePerGas"],
                    max_fee=meta["maxFeePerGas"],
                )
                # Immediately check if receipt is already available (e.g. fast L2 or synchronous test fake)
                self._handle_in_flight(now)

        except Exception as e:
            print(f"WARN [anchor] RPC or tick error (will retry next tick): {e}", file=sys.stderr, flush=True)

        self._maybe_run_ots(now)

    def _maybe_run_ots(self, now: float):
        if now - self.last_ots_time < OTS_INTERVAL_S:
            return
        self.last_ots_time = now
        try:
            if self.ots_runner is not None:
                self.ots_runner(self.root)
            else:
                ots.run_ots_cycle(witness_root=self.root)
        except Exception as e:
            print(f"WARN [anchor] OTS cycle error: {e}", file=sys.stderr, flush=True)

    def run_forever(self, every: float = 60.0):
        def _sig_handler(_signum, _frame):
            self.stop_requested = True

        old_int = signal.signal(signal.SIGINT, _sig_handler)
        old_term = signal.signal(signal.SIGTERM, _sig_handler)
        try:
            while not self.stop_requested:
                self.step()
                if self.stop_requested:
                    break
                deadline = time.monotonic() + every
                while not self.stop_requested and time.monotonic() < deadline:
                    time.sleep(min(0.5, max(0.0, deadline - time.monotonic())))
        finally:
            signal.signal(signal.SIGINT, old_int)
            signal.signal(signal.SIGTERM, old_term)
            self.close()
