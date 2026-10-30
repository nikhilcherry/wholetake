"""Background drand quicknet fetcher. Never called from the audio thread."""
from __future__ import annotations

import hashlib
import json
import threading
import time
import urllib.request

from .spec import DRAND_CHAIN

RELAYS = [
    f"https://api.drand.sh/{DRAND_CHAIN}",
    f"https://drand.cloudflare.com/{DRAND_CHAIN}",
    f"https://api2.drand.sh/{DRAND_CHAIN}",
]


def _valid_beacon(d: object, rnd: int | str) -> bool:
    if not isinstance(d, dict):
        return False
    r_num = d.get("round")
    if isinstance(r_num, bool) or not isinstance(r_num, int) or r_num <= 0:
        return False
    if rnd != "latest":
        if isinstance(rnd, bool):
            return False
        if isinstance(rnd, int):
            want_rnd = rnd
        elif isinstance(rnd, str) and rnd.isascii() and rnd.isdigit():
            want_rnd = int(rnd)
        else:
            return False
        if want_rnd <= 0 or r_num != want_rnd:
            return False
    rand_hex = d.get("randomness")
    sig_hex = d.get("signature")
    if not isinstance(rand_hex, str) or not isinstance(sig_hex, str):
        return False
    if len(rand_hex) != 64 or any(c.isspace() for c in rand_hex) or any(c.isspace() for c in sig_hex):
        return False
    try:
        rand_bytes = bytes.fromhex(rand_hex)
        sig_bytes = bytes.fromhex(sig_hex)
    except ValueError:
        return False
    if len(rand_bytes) != 32 or not sig_bytes:
        return False
    return hashlib.sha256(sig_bytes).digest() == rand_bytes


def fetch_round(rnd: int | str, timeout: float = 2.0) -> dict | None:
    """`rnd` is a round number or "latest". Returns {"round", "randomness", "signature"} or None."""
    if rnd != "latest":
        if isinstance(rnd, bool):
            return None
        if isinstance(rnd, int):
            if rnd <= 0:
                return None
        elif isinstance(rnd, str) and rnd.isascii() and rnd.isdigit():
            if int(rnd) <= 0:
                return None
        else:
            return None
    for base in RELAYS:
        try:
            with urllib.request.urlopen(f"{base}/public/{rnd}", timeout=timeout) as r:
                d = json.load(r)
            if _valid_beacon(d, rnd):
                d["randomness"] = bytes.fromhex(d["randomness"]).hex()
                return d
        except Exception:
            continue
    return None


class DrandCache:
    """Polls `latest` every second and keeps recent rounds by number."""

    def __init__(self, keep: int = 64):
        if isinstance(keep, bool) or not isinstance(keep, int) or keep < 1:
            raise ValueError(f"keep must be an int >= 1, got {keep!r}")
        self._rounds: dict[int, dict] = {}
        self._lock = threading.Lock()
        self._keep = keep
        self._stop = threading.Event()
        self.last_ok = 0.0
        self._thread = threading.Thread(target=self._run, daemon=True, name="drand")

    def start(self) -> None:
        self._thread.start()   # first poll happens on the thread; start() never blocks

    def stop(self) -> None:
        self._stop.set()

    def _store(self, d: dict) -> None:
        with self._lock:
            self._rounds[int(d["round"])] = d
            if self._keep <= 0:
                self._rounds.clear()
            elif len(self._rounds) > self._keep:
                for r in sorted(self._rounds)[:-self._keep]:
                    del self._rounds[r]
        self.last_ok = time.time()

    def _poll(self) -> None:
        d = fetch_round("latest")
        if d is None:
            return
        self._store(d)
        # backfill the rounds before latest that round_for() asks for (lag 3 + slack)
        for r in range(int(d["round"]) - 5, int(d["round"])):
            if self.get(r) is None:
                dd = fetch_round(r)
                if dd:
                    self._store(dd)

    def _run(self) -> None:
        while True:
            try:
                self._poll()
            except Exception:
                pass
            if self._stop.wait(1.0):
                return

    def get(self, rnd: int) -> dict | None:
        with self._lock:
            return self._rounds.get(rnd)
