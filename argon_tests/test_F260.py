import json
import os
import shutil
import struct
import sys
import tempfile
import numpy as np

from roomstamp import cli, engine, spec, wavio

def test_second_seed_and_sequencer_bounds():
    key = b"\x11" * 32
    try:
        spec.second_seed(key, -1, None)
        print("FAIL test_second_seed_and_sequencer_bounds: second_seed(-1) did not raise")
        sys.exit(1)
    except ValueError:
        pass
    except struct.error as e:
        print(f"FAIL test_second_seed_and_sequencer_bounds: second_seed(-1) raised struct.error: {e}")
        sys.exit(1)

    try:
        engine.KeystreamSequencer(key, 0, 0.0, lambda t: None)
        print("FAIL test_second_seed_and_sequencer_bounds: KeystreamSequencer(t_start=0) did not raise")
        sys.exit(1)
    except ValueError:
        pass
    except struct.error as e:
        print(f"FAIL test_second_seed_and_sequencer_bounds: KeystreamSequencer(t_start=0) raised struct.error: {e}")
        sys.exit(1)
    print("PASS test_second_seed_and_sequencer_bounds")

def test_cmd_render_t_start_zero():
    d = tempfile.mkdtemp()
    try:
        key_path = os.path.join(d, "venue.key")
        with open(key_path, "w", encoding="utf-8") as f:
            f.write("22" * 32 + "\n")
        wav_in = os.path.join(d, "in.wav")
        wav_out = os.path.join(d, "out.wav")
        wavio.write(wav_in, np.zeros(spec.FS, dtype=np.float32))
        try:
            cli.main(["render", wav_in, wav_out, "--key-file", key_path, "--t-start", "0", "--unanchored"])
            log_path = os.path.join(d, "out.jsonl")
            hdr = json.loads(open(log_path, encoding="utf-8").readline())
            print(f"FAIL test_cmd_render_t_start_zero: --t-start 0 was not rejected (logged t_start={hdr.get('t_start')})")
            sys.exit(1)
        except ValueError:
            pass

        cli.main(["render", wav_in, wav_out, "--key-file", key_path, "--t-start", "1", "--unanchored"])
        log_path = os.path.join(d, "out.jsonl")
        hdr = json.loads(open(log_path, encoding="utf-8").readline())
        if hdr.get("t_start") != 1:
            print(f"FAIL test_cmd_render_t_start_zero: --t-start 1 logged t_start={hdr.get('t_start')}")
            sys.exit(1)
        print("PASS test_cmd_render_t_start_zero")
    finally:
        shutil.rmtree(d, ignore_errors=True)

if __name__ == "__main__":
    test_second_seed_and_sequencer_bounds()
    test_cmd_render_t_start_zero()
