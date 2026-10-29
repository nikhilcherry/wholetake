import os
import shutil
import sys
import tempfile
import warnings
import numpy as np

from roomstamp import selftest, wavio
from roomstamp.spec import FS


def main() -> None:
    name = "test_selftest_load_speech_zero_seconds"
    tmpdir = tempfile.mkdtemp()
    try:
        wav_path = os.path.join(tmpdir, "speech_2s.wav")
        empty_wav_path = os.path.join(tmpdir, "empty.wav")
        t = np.arange(2 * FS) / FS
        sig = 0.25 * np.sin(2 * np.pi * 440.0 * t)
        wavio.write(wav_path, sig, fs=FS)
        wavio.write(empty_wav_path, np.zeros(0, dtype=np.float64), fs=FS)

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            out_zero_float = selftest.load_speech(wav_path, seconds=0.0)
            out_zero_int = selftest.load_speech(wav_path, seconds=0)
            out_tiny = selftest.load_speech(wav_path, seconds=1e-6)
            out_neg = selftest.load_speech(wav_path, seconds=-1.0)
            out_empty_file = selftest.load_speech(empty_wav_path, seconds=10.0)

        if len(out_zero_float) != 0:
            print(f"FAIL {name}: load_speech(seconds=0.0) ignored 0.0 due to falsy check and returned {len(out_zero_float)} samples")
            sys.exit(1)
        if len(out_zero_int) != 0:
            print(f"FAIL {name}: load_speech(seconds=0) returned {len(out_zero_int)} samples")
            sys.exit(1)
        if len(out_tiny) != 0 or len(out_neg) != 0 or len(out_empty_file) != 0:
            print(f"FAIL {name}: expected empty arrays for tiny/negative/empty inputs")
            sys.exit(1)

        runtime_warnings = [item for item in w if issubclass(item.category, RuntimeWarning)]
        if runtime_warnings:
            print(f"FAIL {name}: emitted RuntimeWarning on empty slice: {runtime_warnings[0].message}")
            sys.exit(1)

        out_full = selftest.load_speech(wav_path, seconds=None)
        if len(out_full) != 2 * FS:
            print(f"FAIL {name}: load_speech(seconds=None) returned {len(out_full)} samples instead of {2 * FS}")
            sys.exit(1)

        out_1s = selftest.load_speech(wav_path, seconds=1.0)
        if len(out_1s) != FS:
            print(f"FAIL {name}: load_speech(seconds=1.0) returned {len(out_1s)} samples instead of {FS}")
            sys.exit(1)
        rms_dbfs = 20 * np.log10(np.sqrt(np.mean(out_1s ** 2)))
        if not np.isclose(rms_dbfs, -20.0, atol=0.1):
            print(f"FAIL {name}: expected -20 dBFS RMS, got {rms_dbfs:.2f} dBFS")
            sys.exit(1)

        print(f"PASS {name}")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    main()
