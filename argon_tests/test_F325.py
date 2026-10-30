import os
import shutil
import sys
import tempfile
import warnings

import numpy as np

from roomstamp import wavio


def test_01_wavio_nan_inf_pcm16() -> bool:
    for dt in (np.float64, np.float32):
        x = np.array([np.nan, np.inf, -np.inf, 0.0, 0.5, -0.5], dtype=dt)
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            raw = wavio.to_pcm16(x)
        rw = [item for item in w if issubclass(item.category, RuntimeWarning)]
        vals = np.frombuffer(raw, dtype="<i2")
        if rw or vals[0] != 0:
            print(f"FAIL test_01_wavio_nan_inf_pcm16: NaN cast produced {vals[0]} and {len(rw)} RuntimeWarning(s)")
            return False
        expected = np.array([0, 32767, -32768, 0, 16384, -16384], dtype="<i2")
        if not np.array_equal(vals, expected):
            print(f"FAIL test_01_wavio_nan_inf_pcm16: expected {expected.tolist()}, got {vals.tolist()}")
            return False
    print("PASS test_01_wavio_nan_inf_pcm16")
    return True


def test_02_wavio_write_and_streamwriter_nan() -> bool:
    tmp = tempfile.mkdtemp(prefix="f325_")
    try:
        p1 = os.path.join(tmp, "w1.wav")
        p2 = os.path.join(tmp, "w2.wav")
        x = np.array([0.25, np.nan, np.inf, -np.inf, -0.25], dtype=np.float32)
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            wavio.write(p1, x)
            sw = wavio.StreamWriter(p2)
            try:
                sw.write(x)
            finally:
                sw.close()
        rw = [item for item in w if issubclass(item.category, RuntimeWarning)]
        if rw:
            print(f"FAIL test_02_wavio_write_and_streamwriter_nan: emitted {len(rw)} RuntimeWarning(s)")
            return False
        y1 = wavio.read(p1)
        y2 = wavio.read(p2)
        if y1[1] != 0.0 or y2[1] != 0.0:
            print(f"FAIL test_02_wavio_write_and_streamwriter_nan: NaN read back as {y1[1]}, {y2[1]}")
            return False
        if not (np.isclose(y1[2], 32767 / 32768.0) and np.isclose(y1[3], -1.0)):
            print(f"FAIL test_02_wavio_write_and_streamwriter_nan: Inf read back as {y1[2]}, {y1[3]}")
            return False
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("PASS test_02_wavio_write_and_streamwriter_nan")
    return True


def test_03_wavio_write_2d_stereo() -> bool:
    tmp = tempfile.mkdtemp(prefix="f325_2d_")
    try:
        stereo = np.zeros((480, 2), dtype=np.float32)
        p = os.path.join(tmp, "stereo.wav")
        try:
            wavio.write(p, stereo)
        except ValueError:
            pass
        else:
            y = wavio.read(p)
            if len(y) != 480:
                print(f"FAIL test_03_wavio_write_2d_stereo: writing (480, 2) array produced {len(y)} mono samples instead of 480 or raising ValueError")
                return False
        try:
            wavio.to_pcm16(stereo)
        except ValueError:
            pass
        else:
            print("FAIL test_03_wavio_write_2d_stereo: to_pcm16 accepted 2-D array without raising ValueError")
            return False
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("PASS test_03_wavio_write_2d_stereo")
    return True


if __name__ == "__main__":
    ok = True
    ok &= test_01_wavio_nan_inf_pcm16()
    ok &= test_02_wavio_write_and_streamwriter_nan()
    ok &= test_03_wavio_write_2d_stereo()
    sys.exit(0 if ok else 1)
