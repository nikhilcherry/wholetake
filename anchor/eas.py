"""Web3 client for the EAS predeploy timestamping functions on Base Sepolia."""
import os
from typing import Any

from eth_account.signers.local import LocalAccount
from web3 import Web3
from web3.exceptions import TransactionNotFound

DEFAULT_RPC = "https://sepolia.base.org"
DEFAULT_CHAIN_ID = 84532
EAS_ADDRESS = "0x4200000000000000000000000000000000000021"
TIMESTAMPED_TOPIC0 = "0x5aafceeb1c7ad58e4a84898bdee37c02c0fc46e7d24e6b60e8209449f183459f"

EAS_ABI = [
    {
        "inputs": [{"internalType": "bytes32", "name": "data", "type": "bytes32"}],
        "name": "timestamp",
        "outputs": [{"internalType": "uint64", "name": "", "type": "uint64"}],
        "stateMutability": "nonpayable",
        "type": "function",
    },
    {
        "inputs": [{"internalType": "bytes32[]", "name": "data", "type": "bytes32[]"}],
        "name": "multiTimestamp",
        "outputs": [{"internalType": "uint64", "name": "", "type": "uint64"}],
        "stateMutability": "nonpayable",
        "type": "function",
    },
    {
        "inputs": [{"internalType": "bytes32", "name": "data", "type": "bytes32"}],
        "name": "getTimestamp",
        "outputs": [{"internalType": "uint64", "name": "", "type": "uint64"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "anonymous": False,
        "inputs": [
            {"indexed": True, "internalType": "bytes32", "name": "data", "type": "bytes32"},
            {"indexed": True, "internalType": "uint64", "name": "timestamp", "type": "uint64"},
        ],
        "name": "Timestamped",
        "type": "event",
    },
]


def _norm_hash(h: str) -> bytes:
    clean = h[2:] if h.startswith(("0x", "0X")) else h
    if len(clean) != 64:
        raise ValueError(f"expected 64-char sha256 hex, got {h!r}")
    return bytes.fromhex(clean)


def _hex0x(val: bytes | str) -> str:
    if isinstance(val, (bytes, bytearray)):
        return "0x" + val.hex()
    return val if val.startswith("0x") else "0x" + val


class EASClient:
    """Thin wrapper around web3 for EAS timestamping."""

    def __init__(
        self,
        account: LocalAccount | None = None,
        rpc_url: str | None = None,
        chain_id: int | None = None,
        eas_address: str = EAS_ADDRESS,
    ):
        self.rpc_url = rpc_url or os.environ.get("WT_RPC") or DEFAULT_RPC
        self.chain_id = int(chain_id if chain_id is not None else os.environ.get("WT_CHAIN_ID", DEFAULT_CHAIN_ID))
        self.eas_address = Web3.to_checksum_address(eas_address)
        self.account = account

        self.w3 = Web3(Web3.HTTPProvider(self.rpc_url, request_kwargs={"timeout": 20}))
        actual_chain = int(self.w3.eth.chain_id)
        if actual_chain != self.chain_id:
            raise RuntimeError(f"RPC chain_id mismatch: expected {self.chain_id}, got {actual_chain}")

        self.contract = self.w3.eth.contract(address=self.eas_address, abi=EAS_ABI)

    def balance_wei(self, address: str | None = None) -> int:
        addr = address or (self.account.address if self.account else None)
        if not addr:
            raise ValueError("no address available for balance check")
        return int(self.w3.eth.get_balance(Web3.to_checksum_address(addr)))

    def nonce_mined(self, nonce: int) -> bool:
        """Return True if the account's confirmed (latest) transaction count is strictly greater than `nonce`."""
        if not self.account:
            raise ValueError("EASClient has no account configured for nonce check")
        latest_count = int(self.w3.eth.get_transaction_count(self.account.address, "latest"))
        return latest_count > nonce

    def get_timestamp(self, hash_hex: str) -> int:
        b32 = _norm_hash(hash_hex)
        return int(self.contract.functions.getTimestamp(b32).call())

    def send_multi_timestamp(
        self,
        hashes: list[str],
        nonce: int | None = None,
        fee_multiplier: float = 1.0,
        min_priority_fee: int | None = None,
        min_max_fee: int | None = None,
    ) -> tuple[str, dict[str, int]]:
        """Build, sign, and broadcast multiTimestamp(hashes). Returns (tx_hash_0x, tx_meta)."""
        if not self.account:
            raise ValueError("EASClient has no account configured for signing")
        if not hashes:
            raise ValueError("hashes must not be empty")

        b32_list = [_norm_hash(h) for h in hashes]
        sender = self.account.address
        call = self.contract.functions.multiTimestamp(b32_list)

        if nonce is None:
            nonce = int(self.w3.eth.get_transaction_count(sender, "pending"))

        prio_fee = int(self.w3.eth.max_priority_fee)
        latest_block = self.w3.eth.get_block("latest")
        base_fee = int(latest_block.get("baseFeePerGas") or 1_000_000)

        prio_fee = max(1, int(prio_fee * fee_multiplier))
        max_fee = max(prio_fee, int((2 * base_fee + prio_fee) * fee_multiplier))

        if min_priority_fee is not None:
            prio_fee = max(prio_fee, int(min_priority_fee))
        if min_max_fee is not None:
            max_fee = max(max_fee, int(min_max_fee), prio_fee)

        est_gas = int(call.estimate_gas({"from": sender}))
        gas_limit = int(est_gas * 1.2)

        tx = call.build_transaction({
            "chainId": self.chain_id,
            "from": sender,
            "nonce": nonce,
            "gas": gas_limit,
            "maxPriorityFeePerGas": prio_fee,
            "maxFeePerGas": max_fee,
        })
        signed = self.account.sign_transaction(tx)
        tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
        return _hex0x(tx_hash), {
            "nonce": nonce,
            "gas": gas_limit,
            "maxPriorityFeePerGas": prio_fee,
            "maxFeePerGas": max_fee,
        }

    def receipt(self, tx_hash: str) -> dict[str, Any] | None:
        try:
            r = self.w3.eth.get_transaction_receipt(tx_hash)
            return dict(r)
        except TransactionNotFound:
            return None

    def parse_timestamped(self, rcpt: dict[str, Any]) -> dict[str, int]:
        """Extract {hash_hex_lower_64: unix_ts} from Timestamped logs in a receipt."""
        out: dict[str, int] = {}
        target_topic0 = TIMESTAMPED_TOPIC0.lower()
        eas_addr_lower = self.eas_address.lower()
        for log in rcpt.get("logs", []):
            addr = str(log.get("address", "")).lower()
            if addr and addr != eas_addr_lower:
                continue
            topics = [_hex0x(t).lower() for t in log.get("topics", [])]
            if len(topics) >= 3 and topics[0] == target_topic0:
                h_hex = topics[1][2:].rjust(64, "0")[-64:]
                ts = int(topics[2], 16)
                out[h_hex] = ts
        return out
