import io
import os
import secrets
import shutil
import sys
import tempfile
import numpy as np

from roomstamp import cli, engine, wavio
from roomstamp.spec import FS

tmp = tempfile.mkdtemp(prefix="f002_")
try:
    key_path = os.path.join(tmp, "venue.key")
    key = secrets.token_bytes(32)
    with open(key_path, "w", encoding="utf-8") as f:
        f.write(key.hex() + "\n")

    rng = np.random.default_rng(42)
    x = (rng.standard_normal(4 * FS) * 0.02).astype(np.float64)
    # Trigger exactly one howl event (10 blocks = 100 ms > -3 dBFS) in second 0
    x[:10 * engine.BLOCK] = 0.8

    wav_in = os.path.join(tmp, "in.wav")
    wav_out = os.path.join(tmp, "out.wav")
    log_path = os.path.join(tmp, "out.jsonl")
    wavio.write(wav_in, x)

    t0 = 1793209200
    cli.main(["render", wav_in, wav_out, "--mode", "pa", "--key-file", key_path,
              "--t-start", str(t0), "--unanchored"])

    header, secs = cli.read_log(log_path)
    sec2 = secs.get(t0 + 2, {})
    if sec2.get("ref_rms") is None or sec2.get("gain") is None:
        print(f"FAIL F002: sec record missing ref_rms or gain: {sec2}")
        sys.exit(1)
    if abs(sec2["gain"] - 0.5) > 1e-6:
        print(f"FAIL F002: expected halved gain 0.5 after howl, got {sec2['gain']}")
        sys.exit(1)

    # Re-render mid-session slice [2s, 4s) using logged ref_rms and gain; verify it matches full render
    p = engine.StampParams(**header["params"])
    full_out, full_stamp, _ = engine.render(x, key, t0, p, return_parts=True)
    slice_out, slice_stamp, _ = engine.render(x[2 * FS:], key, t0 + 2, p, return_parts=True,
                                              ref_rms=sec2["ref_rms"], gain=sec2["gain"])
    # After a short filter warm-up (0.1 s), sliced stamp must match full stamp closely
    err = float(np.max(np.abs(full_stamp[2 * FS + FS // 10:] - slice_stamp[FS // 10:])))
    if err > 1e-5:
        print(f"FAIL F002: sliced stamp diverged from full render by {err}")
        sys.exit(1)

    # Also verify cmd_detect --start 2 does not print missing ref_rms note
    buf = io.StringIO()
    old_stdout = sys.stdout
    sys.stdout = buf
    try:
        cli.main(["detect", wav_out, "--log", log_path, "--key-file", key_path,
                  "--start", "2", "--dur", "2"])
    finally:
        sys.stdout = old_stdout
    if "log has no ref_rms" in buf.getvalue():
        print(f"FAIL F002: cmd_detect printed missing ref_rms warning: {buf.getvalue()}")
        sys.exit(1)

    print("PASS F002")
finally:
    shutil.rmtree(tmp, ignore_errors=True)
