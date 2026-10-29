import os
import shutil
import sys
import tempfile
import types
import numpy as np

from roomstamp import emitter, engine, wavio

def main():
    name = "emitter_empty_wav_loop_callback"
    tmp = tempfile.mkdtemp()
    try:
        empty_wav = os.path.join(tmp, "empty.wav")
        wavio.write(empty_wav, np.zeros(0, dtype=np.float64))
        valid_wav = os.path.join(tmp, "valid.wav")
        wavio.write(valid_wav, np.zeros(480, dtype=np.float64))

        key = b"\x01" * 32
        params = engine.StampParams(mode="file")

        try:
            em = emitter.Emitter(key, params, log_dir=tmp, wav_in=empty_wav, loop_wav=True, hostapi=None)
        except SystemExit:
            em = emitter.Emitter(key, params, log_dir=tmp, wav_in=valid_wav, loop_wav=True, hostapi=None)
            em.file_audio = np.zeros(0, dtype=np.float64)
        else:
            print(f"FAIL {name}: Emitter.__init__ did not raise SystemExit on empty WAV")
            sys.exit(1)

        em.t_start = 1793209200
        outdata = np.zeros((engine.BLOCK, 1), dtype=np.float32)
        tinfo = types.SimpleNamespace(outputBufferDacTime=1.0, currentTime=1.0)
        status = types.SimpleNamespace(output_underflow=False, input_overflow=False)

        try:
            em._callback(None, outdata, engine.BLOCK, tinfo, status)
        except Exception as e:
            print(f"FAIL {name}: {type(e).__name__}: {e}")
            sys.exit(1)

        print(f"PASS {name}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

if __name__ == "__main__":
    main()
