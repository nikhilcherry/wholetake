import io
import os
import sys
import tempfile
import warnings
from contextlib import redirect_stdout
import numpy as np

from roomstamp import cli, wavio
from roomstamp.spec import FS

def main():
    tmp = tempfile.mkdtemp()
    zero_wav = os.path.join(tmp, "zero.wav")
    dc_wav = os.path.join(tmp, "dc.wav")
    wavio.write(zero_wav, np.zeros(2 * FS))
    wavio.write(dc_wav, np.full(2 * FS, 1.0 / 32768.0))

    try:
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            buf1 = io.StringIO()
            with redirect_stdout(buf1):
                cli.main(["audibility", "--wav-in", zero_wav])
            out1 = buf1.getvalue()
            if any("divide by zero" in str(item.message) for item in w):
                print("FAIL F005: RuntimeWarning divide by zero on silent WAV")
                sys.exit(1)
            if "-inf" in out1 or "+inf" in out1:
                print(f"FAIL F005: -inf/+inf in output on silent WAV: {out1!r}")
                sys.exit(1)

            buf2 = io.StringIO()
            with redirect_stdout(buf2):
                cli.main(["audibility", "--wav-in", dc_wav])
    except Exception as e:
        print(f"FAIL F005: raised {type(e).__name__}: {e}")
        sys.exit(1)

    print("PASS F005")

if __name__ == "__main__":
    main()
