from __future__ import annotations

import hashlib
import sys
import time

import roomstamp.drand as drand_mod
from roomstamp.drand import DrandCache


def _make_beacon(rnd: int) -> dict:
    sig = f"sig-{rnd}".encode("ascii")
    rand = hashlib.sha256(sig).hexdigest()
    return {"round": rnd, "randomness": rand, "signature": sig.hex()}


def main() -> None:
    orig_fetch = drand_mod.fetch_round
    latest_round = [100]

    def fake_fetch(rnd, timeout=2.0):
        r = latest_round[0] if rnd == "latest" else int(rnd)
        return _make_beacon(r)

    drand_mod.fetch_round = fake_fetch
    try:
        cache = DrandCache(keep=8)

        # Calling stop() before start() should not raise
        cache.stop()

        cache.start()
        # Calling start() while already running should be idempotent
        cache.start()

        deadline = time.time() + 1.0
        while cache.last_ok == 0.0 and time.time() < deadline:
            time.sleep(0.01)
        if cache.last_ok == 0.0:
            print("FAIL test_F362: DrandCache did not complete initial poll")
            sys.exit(1)
        if not cache._thread.is_alive():
            print("FAIL test_F362: DrandCache._thread not alive while running")
            sys.exit(1)

        cache.stop()
        if cache._thread.is_alive():
            print("FAIL test_F362: DrandCache._thread is still alive immediately after cache.stop()")
            sys.exit(1)

        # Restarting after stop() should work and poll new rounds
        latest_round[0] = 200
        try:
            cache.start()
        except RuntimeError as exc:
            print(f"FAIL test_F362: cache.start() after cache.stop() raised RuntimeError: {exc}")
            sys.exit(1)

        deadline = time.time() + 1.0
        while cache.get(200) is None and time.time() < deadline:
            time.sleep(0.01)
        if cache.get(200) is None:
            cache.stop()
            print("FAIL test_F362: restarted DrandCache did not poll round 200")
            sys.exit(1)

        cache.stop()
        if cache._thread.is_alive():
            print("FAIL test_F362: DrandCache._thread is still alive after second stop()")
            sys.exit(1)

        print("PASS test_F362")
        sys.exit(0)
    finally:
        drand_mod.fetch_round = orig_fetch


if __name__ == "__main__":
    main()
