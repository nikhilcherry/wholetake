from __future__ import annotations

import os
import shutil
import sys
import tempfile
import wave

os.environ["WHOLETAKE_ASR"] = "none"

import numpy as np
from scipy.signal import resample_poly

from roomstamp.witness import wt_stage
from wholetake import audio
from wholetake.audio import SR


def _write_wav(path: str, x: np.ndarray, sr: int = SR) -> None:
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((np.clip(x, -1.0, 0.99997) * 32768).astype("<i2").tobytes())


def main() -> int:
    test_name = "wt_stage_cache_invalidation_on_modified_witness_file"
    tmp_dir = tempfile.mkdtemp(prefix="f273_")
    try:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        talk_path = os.path.join(root, "testdata", "witness", "talk.wav")
        other_path = os.path.join(root, "testdata", "witness", "other.wav")

        talk_16k = audio.decode(talk_path)
        other_16k = audio.decode(other_path)

        witness_wav = os.path.join(tmp_dir, "witness.wav")
        _write_wav(witness_wav, other_16k[5 * SR : 20 * SR], sr=SR)

        clip_16k = talk_16k[15 * SR : 25 * SR]
        clip_48k = resample_poly(clip_16k, 3, 1).astype(np.float32)

        res1 = wt_stage(clip_48k, witness_wav)
        if res1.get("status") != "absent":
            print(f"FAIL {test_name}: expected initial status='absent', got {res1.get('status')!r}")
            return 1

        _write_wav(witness_wav, talk_16k[10 * SR : 30 * SR], sr=SR)

        res2 = wt_stage(clip_48k, witness_wav)
        if res2.get("status") != "present" or res2.get("verdict") != "faithful":
            print(
                f"FAIL {test_name}: wt_stage served stale cached .wtrec after witness file changed on disk "
                f"(expected status='present', got status={res2.get('status')!r}, summary={res2.get('summary')!r})"
            )
            return 1

        print(f"PASS {test_name}")
        return 0
    except Exception as e:
        print(f"FAIL {test_name}: {type(e).__name__}: {e}")
        return 1
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
