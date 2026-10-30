"""Reproducer test for F309: RandomnessSource loads cache file even when unanchored=True
and crashes on corrupt or non-dict JSON cache."""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile

from roomstamp.blind import RandomnessSource


def fail(msg: str) -> None:
    print(f"FAIL test_F309: {msg}")
    sys.exit(1)


def main() -> None:
    tmpdir = tempfile.mkdtemp(prefix="test_f309_")
    try:
        cache_path = os.path.join(tmpdir, "drand_cache.json")

        # 1. Corrupt JSON cache with unanchored=True must not be loaded or crash
        with open(cache_path, "w", encoding="utf-8") as f:
            f.write("{corrupt_json")
        try:
            src = RandomnessSource(unanchored=True, cache_path=cache_path)
        except Exception as e:
            fail(f"unanchored=True parsed corrupt cache_path and crashed with {type(e).__name__}: {e}")
        if src.by_round != {}:
            fail(f"expected empty by_round when unanchored=True, got {src.by_round!r}")
        if src.get(1793214220) is not None:
            fail("expected src.get(...) to be None when unanchored=True")

        # 2. Non-dict JSON cache with unanchored=True must not be loaded or crash
        for payload in ("[1, 2, 3]", '"hello"', "123", "null"):
            with open(cache_path, "w", encoding="utf-8") as f:
                f.write(payload)
            try:
                src = RandomnessSource(unanchored=True, cache_path=cache_path)
            except Exception as e:
                fail(f"unanchored=True parsed {payload} cache_path and crashed with {type(e).__name__}: {e}")

        # 3. Non-dict JSON cache with unanchored=False must raise ValueError (not AttributeError/TypeError)
        for payload in ("[1, 2, 3]", '"hello"', "123", "null"):
            with open(cache_path, "w", encoding="utf-8") as f:
                f.write(payload)
            try:
                RandomnessSource(unanchored=False, cache_path=cache_path)
            except ValueError:
                pass
            except Exception as e:
                fail(f"non-dict JSON {payload} raised {type(e).__name__} instead of ValueError: {e}")
            else:
                fail(f"non-dict JSON {payload} with unanchored=False did not raise ValueError")

        # 4. Non-string value in dict cache with unanchored=False must raise ValueError (not TypeError)
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump({"12345": 999}, f)
        try:
            RandomnessSource(unanchored=False, cache_path=cache_path)
        except ValueError:
            pass
        except Exception as e:
            fail(f"non-string dict value raised {type(e).__name__} instead of ValueError: {e}")
        else:
            fail("non-string dict value did not raise ValueError")

        # 5. Valid dict cache (including null entry per F198 and 32-byte hex entry) loads properly
        hex32 = "ab" * 32
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump({"100": hex32, "101": None}, f)
        src_ok = RandomnessSource(unanchored=False, cache_path=cache_path)
        if src_ok.by_round.get(100) != bytes.fromhex(hex32) or src_ok.by_round.get(101) is not None:
            fail(f"valid cache did not load expected rounds: {src_ok.by_round!r}")

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    print("PASS test_F309")


if __name__ == "__main__":
    main()
