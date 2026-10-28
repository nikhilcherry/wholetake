"""Throwaway EOA wallet management for Base Sepolia EAS anchoring.

    python -m anchor wallet new
    python -m anchor wallet address
    python -m anchor wallet balance
"""
import os
import secrets
from pathlib import Path

from eth_account import Account
from eth_account.signers.local import LocalAccount

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_KEY_FILE = REPO_ROOT / "data" / "anchor.key"


def key_path(path: Path | str | None = None) -> Path:
    if path is not None:
        return Path(path)
    env = os.environ.get("WT_ANCHOR_KEY_FILE")
    return Path(env) if env else DEFAULT_KEY_FILE


def create_wallet(path: Path | str | None = None) -> str:
    """Atomically generate a new 32-byte key at 0600, refusing to overwrite an existing key."""
    kp = key_path(path)
    if kp.exists():
        raise FileExistsError(f"wallet key already exists at {kp}; refusing to overwrite")

    kp.parent.mkdir(parents=True, exist_ok=True)
    raw_hex = "0x" + secrets.token_hex(32)
    acct = Account.from_key(raw_hex)

    tmp = kp.with_suffix(kp.suffix + f".tmp.{os.getpid()}")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(fd, (raw_hex + "\n").encode("ascii"))
        os.fsync(fd)
    finally:
        os.close(fd)

    try:
        os.link(tmp, kp)
    except FileExistsError:
        os.unlink(tmp)
        raise FileExistsError(f"wallet key already exists at {kp}; refusing to overwrite")
    finally:
        if tmp.exists():
            os.unlink(tmp)

    os.chmod(kp, 0o600)
    return acct.address


def load_account(path: Path | str | None = None) -> LocalAccount:
    """Load the LocalAccount from the key file without exposing the private key in errors."""
    kp = key_path(path)
    if not kp.exists():
        raise FileNotFoundError(f"wallet key not found at {kp} (run: python -m anchor wallet new)")
    raw = kp.read_text(encoding="ascii").strip()
    return Account.from_key(raw)


def get_address(path: Path | str | None = None) -> str:
    return load_account(path).address
