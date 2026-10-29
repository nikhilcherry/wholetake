"""Background drand quicknet fetcher. Never called from the audio thread."""
from __future__ import annotations

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


def fetch_round(rnd: int | str, timeout: float = 2.0) -> dict | None:
    """`rnd` is a round number or "latest". Returns {"round", "randomness", "signature"} or None."""
    for base in RELAYS:
        try:
            with urllib.request.urlopen(f"{base}/public/{rnd}", timeout=timeout) as r:
                d = json.load(r)
            if "randomness" in d:
                return d
        except Exception:
            continue
    return None


class DrandCache:
    """Polls `latest` every second and keeps recent rounds by number."""

    def __init__(self, keep: int = 64):
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
