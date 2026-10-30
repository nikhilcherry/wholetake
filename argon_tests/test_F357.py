import os
import shutil
import sys
import tempfile
import numpy as np

from roomstamp import cli, engine, spec, wavio
from roomstamp.spec import FS


def main() -> int:
    key = bytes(range(32))
    tmpdir = tempfile.mkdtemp(prefix="test_f357_")
    try:
        # 1. KeystreamSequencer must reject t_start >= 2^64 upfront in __init__
        for bad_t in (1 << 64, (1 << 64) + 10, 0, -5):
            try:
                engine.KeystreamSequencer(key, bad_t, 4.0, lambda t: None)
                print(f"FAIL test_F357: KeystreamSequencer(t_start={bad_t}) succeeded in __init__")
                return 1
            except ValueError:
                pass

        # 2. KeystreamSequencer and spec.second_seed must reject boolean t_start / t
        for bval in (True, False):
            try:
                engine.KeystreamSequencer(key, bval, 4.0, lambda t: None)
                print(f"FAIL test_F357: KeystreamSequencer(t_start={bval!r}) succeeded")
                return 1
            except ValueError:
                pass
            try:
                spec.second_seed(key, bval, None)
                print(f"FAIL test_F357: second_seed(t={bval!r}) succeeded")
                return 1
            except ValueError:
                pass
            try:
                spec.SecondKeystream(key, bval, None, 1, bytes(32))
                print(f"FAIL test_F357: SecondKeystream(t={bval!r}) succeeded")
                return 1
            except ValueError:
                pass

        # 3. render must reject t_start + n_sec > 2^64 upfront without invoking lookup
        calls = []
        def spy_lookup(t):
            calls.append(t)
            return None

        try:
            engine.render(np.zeros(2 * FS), key, (1 << 64) - 1, engine.StampParams(), spy_lookup)
            print("FAIL test_F357: render(2s, t_start=2^64-1) succeeded")
            return 1
        except ValueError:
            if calls:
                print(f"FAIL test_F357: render invoked lookup before rejecting t_start+n_sec overflow: {calls}")
                return 1

        try:
            engine.render(np.zeros(FS), key, True, engine.StampParams(), spy_lookup)
            print("FAIL test_F357: render(t_start=True) succeeded")
            return 1
        except ValueError:
            pass

        # 4. cmd_render with --t-start (2^64 - 1) on a 2-second clip must fail upfront
        key_path = os.path.join(tmpdir, "venue.key")
        with open(key_path, "w", encoding="utf-8") as f:
            f.write(key.hex() + "\n")
        wav_in = os.path.join(tmpdir, "in.wav")
        wav_out = os.path.join(tmpdir, "out.wav")
        wavio.write(wav_in, np.zeros(2 * FS))
        try:
            cli.main(["render", wav_in, wav_out, "--key-file", key_path, "--t-start", str((1 << 64) - 1), "--unanchored"])
            print("FAIL test_F357: cmd_render(2s, --t-start 2^64-1) succeeded")
            return 1
        except ValueError:
            pass

        # 5. Valid 1-second render at t_start = 2^64 - 1 must still succeed
        out = engine.render(np.zeros(FS), key, (1 << 64) - 1, engine.StampParams())
        if len(out) != FS:
            print(f"FAIL test_F357: valid boundary render length {len(out)} != {FS}")
            return 1

        print("PASS test_F357")
        return 0
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
