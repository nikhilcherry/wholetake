import io
import json
import os
import shutil
import sys
import tempfile
import warnings
from contextlib import redirect_stdout

import numpy as np

from roomstamp import cli, detect, engine, spec, wavio
from roomstamp.spec import FS


def main() -> None:
    tmpdir = tempfile.mkdtemp(prefix="test_f261_")
    try:
        rng = np.random.default_rng(42)
        feed = rng.standard_normal(2 * FS) * 0.1
        key = bytes(range(32))
        t_start = 1793209200
        params = engine.StampParams(mode="file")

        # Case A: completely silent recording (e.g. muted laptop microphone)
        rec_silent = np.zeros(2 * FS, dtype=np.float64)
        with warnings.catch_warnings(record=True) as w_list:
            warnings.simplefilter("always", RuntimeWarning)
            r_silent = detect.detect(rec_silent, feed, key, t_start, params)

        div_warnings = [w for w in w_list if "divide by zero" in str(w.message)]
        if div_warnings:
            print(f"FAIL detect_silence_and_dc: RuntimeWarning emitted: {div_warnings[0].message}")
            sys.exit(1)

        if not np.isfinite(r_silent["speech_removed_db"]):
            print(f"FAIL detect_silence_and_dc: non-finite speech_removed_db: {r_silent['speech_removed_db']}")
            sys.exit(1)

        if r_silent["speech_removed_db"] != 0.0:
            print(f"FAIL detect_silence_and_dc: expected 0.0 dB on silence, got {r_silent['speech_removed_db']}")
            sys.exit(1)

        try:
            json.dumps(r_silent, allow_nan=False)
        except ValueError as e:
            print(f"FAIL detect_silence_and_dc: non-standard JSON in r_silent: {e}")
            sys.exit(1)

        # Case B: CLI `roomstamp detect --json` on silent WAV
        key_path = os.path.join(tmpdir, "venue.key")
        with open(key_path, "w", encoding="utf-8") as f:
            f.write(key.hex() + "\n")

        feed_in = os.path.join(tmpdir, "in.wav")
        out_wav = os.path.join(tmpdir, "out.wav")
        rec_wav = os.path.join(tmpdir, "rec_silent.wav")
        wavio.write(feed_in, feed)
        wavio.write(rec_wav, rec_silent)

        buf = io.StringIO()
        with redirect_stdout(buf):
            cli.main(["render", feed_in, out_wav, "--key-file", key_path, "--t-start", str(t_start), "--unanchored"])

        log_path = os.path.splitext(out_wav)[0] + ".jsonl"
        buf = io.StringIO()
        with redirect_stdout(buf):
            cli.main(["detect", rec_wav, "--log", log_path, "--key-file", key_path, "--json"])

        out_json = buf.getvalue()

        def disallow_inf_nan(val: str):
            raise ValueError(f"Non-standard JSON constant: {val}")

        try:
            parsed = json.loads(out_json, parse_constant=disallow_inf_nan)
        except Exception as e:
            print(f"FAIL detect_silence_and_dc: CLI --json emitted invalid JSON: {e}\n{out_json}")
            sys.exit(1)

        if not np.isfinite(parsed["speech_removed_db"]):
            print(f"FAIL detect_silence_and_dc: CLI parsed speech_removed_db not finite: {parsed['speech_removed_db']}")
            sys.exit(1)

        print("PASS detect_silence_and_dc")
        sys.exit(0)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    main()
