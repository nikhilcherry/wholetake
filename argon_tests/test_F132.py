import os
import shutil
import sys
import tempfile
import numpy as np

from roomstamp import cli, engine, spec, wavio
from roomstamp.spec import FS

def main():
    tmp = tempfile.mkdtemp()
    try:
        key_path = os.path.join(tmp, "venue.key")
        key = bytes(range(32))
        with open(key_path, "w", encoding="utf-8") as f:
            f.write(key.hex() + "\n")
        wav_in = os.path.join(tmp, "in.wav")
        wav_out = os.path.join(tmp, "out.wav")
        rng = np.random.default_rng(0)
        x = (rng.standard_normal(3 * FS) * 0.05).astype(np.float32)
        wavio.write(wav_in, x)
        cli.main(["render", wav_in, wav_out, "--key-file", key_path, "--t-start", "1700000000", "--unanchored"])
        log_path = os.path.splitext(wav_out)[0] + ".jsonl"

        cases = [
            ("fractional_dur", ["detect", wav_out, "--log", log_path, "--key-file", key_path, "--start", "0.5", "--dur", "0.8"]),
            ("negative_start", ["detect", wav_out, "--log", log_path, "--key-file", key_path, "--start", "-1", "--dur", "2"]),
            ("out_of_bounds_start", ["detect", wav_out, "--log", log_path, "--key-file", key_path, "--start", "10", "--dur", "2"]),
            ("negative_dur", ["detect", wav_out, "--log", log_path, "--key-file", key_path, "--start", "1", "--dur", "-2"]),
        ]
        for name, argv in cases:
            try:
                cli.main(argv)
                print(f"FAIL {name}: expected SystemExit")
                sys.exit(1)
            except SystemExit as e:
                if e.code in (0, None):
                    print(f"FAIL {name}: SystemExit with zero code {e.code}")
                    sys.exit(1)
                print(f"PASS {name}")
            except Exception as e:
                print(f"FAIL {name}: unexpected {type(e).__name__}: {e}")
                sys.exit(1)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

if __name__ == "__main__":
    main()
