import os
import socket
import sys
import numpy as np

# Simulate default environment without WHOLETAKE_ASR=none and block outbound network
os.environ.pop("WHOLETAKE_ASR", None)

_orig_connect = socket.socket.connect
def _no_net(self, *args, **kwargs):
    raise RuntimeError("Outbound network connection attempted in offline mode")
socket.socket.connect = _no_net

try:
    from wholetake import asr, selftest
    # a model that is never in the local cache, so the offline fallback runs on any machine
    # (large-v3-turbo is cached on dev boxes and then loads fine offline)
    asr.MODEL = "Systran/faster-whisper-not-cached-f013"
    asr._model = None

    words = asr.transcribe(np.zeros(16000, dtype=np.float32))
    if words != [] or asr.MODEL != "none":
        print(f"FAIL F013: expected fallback to MODEL='none' and [], got MODEL={asr.MODEL!r}, words={words!r}")
        sys.exit(1)

    asr.MODEL = "large-v3-turbo"
    asr._model = None
    rc = selftest.main(["--only", "6"])
    if rc != 0:
        print(f"FAIL F013: selftest returned {rc}")
        sys.exit(1)

    print("PASS F013")
except Exception as e:
    print(f"FAIL F013: {type(e).__name__}: {e}")
    sys.exit(1)
finally:
    socket.socket.connect = _orig_connect
