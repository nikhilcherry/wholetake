from __future__ import annotations

import os
import shutil
import sys
import tempfile

from roomstamp import cli


def test_load_key_malformed_hex() -> None:
    tmp = tempfile.mkdtemp(prefix="argon_f282_")
    old_env = os.environ.pop("ROOMSTAMP_KEY", None)
    try:
        # 1. Non-hex characters in key file
        bad_file = os.path.join(tmp, "bad.key")
        with open(bad_file, "w", encoding="utf-8") as f:
            f.write("not-a-valid-hex-key!" + "0" * 44 + "\n")
        try:
            cli.load_key(bad_file)
            print("FAIL test_load_key_malformed_hex: load_key did not raise on non-hex key file")
            sys.exit(1)
        except SystemExit:
            pass
        except ValueError as e:
            print(f"FAIL test_load_key_malformed_hex: Raised raw ValueError instead of SystemExit on invalid key file: {e}")
            sys.exit(1)

        # 2. Odd-length hex in key file (63 hex chars)
        odd_file = os.path.join(tmp, "odd.key")
        with open(odd_file, "w", encoding="utf-8") as f:
            f.write("a" * 63 + "\n")
        try:
            cli.load_key(odd_file)
            print("FAIL test_load_key_malformed_hex: load_key did not raise on odd-length hex key file")
            sys.exit(1)
        except SystemExit:
            pass
        except ValueError as e:
            print(f"FAIL test_load_key_malformed_hex: Raised raw ValueError instead of SystemExit on odd-length key file: {e}")
            sys.exit(1)

        # 3. Non-hex characters in ROOMSTAMP_KEY env var
        os.environ["ROOMSTAMP_KEY"] = "zzzz"
        try:
            cli.load_key(os.path.join(tmp, "nonexistent.key"))
            print("FAIL test_load_key_malformed_hex: load_key did not raise on invalid ROOMSTAMP_KEY")
            sys.exit(1)
        except SystemExit:
            pass
        except ValueError as e:
            print(f"FAIL test_load_key_malformed_hex: Raised raw ValueError instead of SystemExit on invalid ROOMSTAMP_KEY: {e}")
            sys.exit(1)
        finally:
            os.environ.pop("ROOMSTAMP_KEY", None)

        # 4. Valid 64-char hex key still works
        valid_file = os.path.join(tmp, "valid.key")
        valid_hex = "ab" * 32
        with open(valid_file, "w", encoding="utf-8") as f:
            f.write(valid_hex + "\n")
        k = cli.load_key(valid_file)
        if k != bytes.fromhex(valid_hex):
            print("FAIL test_load_key_malformed_hex: valid key did not round-trip")
            sys.exit(1)
    finally:
        if old_env is not None:
            os.environ["ROOMSTAMP_KEY"] = old_env
        shutil.rmtree(tmp, ignore_errors=True)

    print("PASS test_load_key_malformed_hex")


if __name__ == "__main__":
    test_load_key_malformed_hex()
