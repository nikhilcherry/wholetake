import os
import shutil
import sys
import tempfile
import numpy as np
import soundfile as sf
from roomstamp import wt_detect

name = "f019_nonfinite_float_wav"
tmp = tempfile.mkdtemp(prefix="wt_f019_")
try:
    for label, bad_val in [("nan", np.nan), ("inf", np.inf)]:
        wav_path = os.path.join(tmp, f"bad_{label}.wav")
        x = np.zeros((48000 * 3, 2), dtype=np.float32)
        x[1000, 0] = bad_val
        sf.write(wav_path, x, 48000, subtype="FLOAT")
        try:
            wt_detect.ingest(wav_path)
            print(f"FAIL {name}: ingest() did not raise ClipError on {label} WAV")
            sys.exit(1)
        except wt_detect.ClipError as e:
            if e.code != "DECODE_FAILED":
                print(f"FAIL {name}: expected DECODE_FAILED on {label}, got {e.code}")
                sys.exit(1)
        except Exception as e:
            print(f"FAIL {name}: unexpected exception {type(e).__name__}: {e}")
            sys.exit(1)
        doc = wt_detect.run(wav_path, key_file=os.path.join(tmp, "nokey"))
        if doc.get("status") != "error" or (doc.get("error") or {}).get("code") != "DECODE_FAILED":
            print(f"FAIL {name}: run() returned status={doc.get('status')} error={doc.get('error')}")
            sys.exit(1)
    print(f"PASS {name}")
finally:
    shutil.rmtree(tmp, ignore_errors=True)
