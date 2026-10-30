"""Reproducer test for F323: cli.load_key on missing key file and cmd_render on empty WAV."""
from __future__ import annotations

import os
import shutil
import sys
import tempfile

import numpy as np

from roomstamp import cli, wavio
from roomstamp.spec import FS


def main() -> None:
    tmpdir = tempfile.mkdtemp(prefix="test_F323_")
    old_env = os.environ.pop("ROOMSTAMP_KEY", None)
    try:
        # 1. Missing key file in load_key should raise SystemExit, not FileNotFoundError/OSError
        missing_key = os.path.join(tmpdir, "nonexistent_venue.key")
        try:
            cli.load_key(missing_key)
            print("FAIL test_F323: load_key did not raise SystemExit on missing key file")
            sys.exit(1)
        except SystemExit as e:
            msg = str(e)
            if "cannot read key file" not in msg:
                print(f"FAIL test_F323: unexpected SystemExit message from load_key: {msg!r}")
                sys.exit(1)
        except Exception as e:
            print(f"FAIL test_F323: load_key raised {type(e).__name__} instead of SystemExit: {e}")
            sys.exit(1)

        # 2. Empty WAV in cmd_render should raise SystemExit and not write 0-second output WAV
        key_file = os.path.join(tmpdir, "venue.key")
        with open(key_file, "w", encoding="utf-8") as f:
            f.write("ab" * 32 + "\n")

        empty_wav = os.path.join(tmpdir, "empty.wav")
        out_wav = os.path.join(tmpdir, "out.wav")
        wavio.write(empty_wav, np.zeros(0))

        try:
            cli.main(["render", empty_wav, out_wav, "--key-file", key_file, "--unanchored", "--t-start", "1793209200"])
            print("FAIL test_F323: cmd_render did not raise SystemExit on empty WAV")
            sys.exit(1)
        except SystemExit as e:
            msg = str(e)
            if "empty" not in msg.lower():
                print(f"FAIL test_F323: unexpected SystemExit message from cmd_render: {msg!r}")
                sys.exit(1)
        except Exception as e:
            print(f"FAIL test_F323: cmd_render raised {type(e).__name__} instead of SystemExit: {e}")
            sys.exit(1)

        if os.path.exists(out_wav):
            print("FAIL test_F323: cmd_render wrote output WAV for empty input")
            sys.exit(1)

        # 3. Sanity check: valid 1-second WAV renders cleanly
        valid_wav = os.path.join(tmpdir, "valid.wav")
        valid_out = os.path.join(tmpdir, "valid_out.wav")
        wavio.write(valid_wav, np.zeros(FS))
        cli.main(["render", valid_wav, valid_out, "--key-file", key_file, "--unanchored", "--t-start", "1793209200"])
        rendered = wavio.read(valid_out)
        if len(rendered) != FS:
            print(f"FAIL test_F323: expected {FS} samples in rendered WAV, got {len(rendered)}")
            sys.exit(1)

        print("PASS test_F323")
        sys.exit(0)
    finally:
        if old_env is not None:
            os.environ["ROOMSTAMP_KEY"] = old_env
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    main()
