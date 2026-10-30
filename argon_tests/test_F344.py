import json
import multiprocessing as mp
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import time

import numpy as np

from roomstamp import wavio, wt_detect, wt_test
from roomstamp.spec import FS


def _run_in_subproc(q, clip_path, out_path, venue_dir, key_file):
    try:
        doc = wt_detect.run(clip_path, out_path=out_path, venue_dir=venue_dir, key_file=key_file, fast=True)
        q.put(("ok", doc))
    except BaseException as e:
        q.put(("err", f"{type(e).__name__}: {e}"))


def test_10_corrupt_session_sec_timestamp_no_dos_hang(root: str) -> tuple[bool, str]:
    d = os.path.join(root, "t10")
    os.makedirs(d, exist_ok=True)
    key = secrets.token_bytes(32)
    kf = os.path.join(d, "venue.key")
    with open(kf, "w", encoding="utf-8") as f:
        f.write(key.hex())
    venue = os.path.join(d, "venue")
    x, out = wt_test.make_venue(venue, "pa", key, seconds=25.0)
    y = wt_test.phone(out, x, "pa", 2.0, 15.0)
    clip_path = os.path.join(d, "clip.wav")
    wavio.write(clip_path, y)

    sess_path = os.path.join(venue, f"session_{wt_test.T0}.jsonl")
    with open(sess_path, "a", encoding="utf-8") as f:
        # Corrupt millisecond-scale timestamp t (1793430600000)
        f.write(json.dumps({
            "type": "sec",
            "t": wt_test.T0 * 1000,
            "round": 12345,
            "anchored": True,
            "randomness": "00" * 32,
            "anchor": "11" * 32,
            "dac_utc": wt_test.T0 + 25.0,
        }) + "\n")

    out_json = os.path.join(d, "detection.json")
    ctx = mp.get_context("spawn")
    q = ctx.Queue()
    p = ctx.Process(target=_run_in_subproc, args=(q, clip_path, out_json, venue, kf), daemon=True)
    t0 = time.time()
    p.start()
    p.join(timeout=15.0)
    dt = time.time() - t0
    if p.is_alive():
        p.kill()
        p.join(2.0)
        return False, f"_proof() / Session.anchored_fraction() hung (> {dt:.1f} s) on corrupt large t"
    if q.empty():
        return False, f"worker exited with code {p.exitcode} without result"
    status, res = q.get()
    if status != "ok":
        return False, f"wt_detect.run raised {res}"
    doc = res
    if doc["verdict"]["recorded_here"] != "verified":
        return False, f"expected verified, got {doc['verdict']['recorded_here']}"
    af = (doc.get("proof") or {}).get("anchored_fraction")
    if af is None or not (0.0 <= af <= 1.0):
        return False, f"invalid anchored_fraction: {af}"
    return True, f"completed in {dt:.2f}s, anchored_fraction={af}"


def test_11_corrupt_dac_utc_in_session_log_no_crash(root: str) -> tuple[bool, str]:
    d = os.path.join(root, "t11")
    os.makedirs(d, exist_ok=True)
    key = secrets.token_bytes(32)
    kf = os.path.join(d, "venue.key")
    with open(kf, "w", encoding="utf-8") as f:
        f.write(key.hex())
    venue = os.path.join(d, "venue")
    x, out = wt_test.make_venue(venue, "pa", key, seconds=25.0)
    y = wt_test.phone(out, x, "pa", 2.0, 15.0)
    clip_path = os.path.join(d, "clip.wav")
    wavio.write(clip_path, y)

    sess_path = os.path.join(venue, f"session_{wt_test.T0}.jsonl")
    with open(sess_path, "r", encoding="utf-8") as f:
        lines = [json.loads(line) for line in f if line.strip()]
    with open(sess_path, "w", encoding="utf-8") as f:
        for i, rec in enumerate(lines):
            if rec.get("type") == "sec":
                if i % 2 == 0:
                    rec["dac_utc"] = "corrupt_not_a_float"
                else:
                    rec["dac_utc"] = "NaN"
            f.write(json.dumps(rec) + "\n")

    out_json = os.path.join(d, "detection.json")
    try:
        doc = wt_detect.run(clip_path, out_path=out_json, venue_dir=venue, key_file=kf, fast=True)
    except Exception as e:
        return False, f"Corrupt dac_utc in session_*.jsonl crashed wt_detect.run(): {type(e).__name__}: {e}"
    if not os.path.isfile(out_json):
        return False, "detection.json was not written"
    if doc["verdict"]["recorded_here"] != "verified":
        return False, f"expected verified, got {doc['verdict']['recorded_here']}"
    rec_at = doc["verdict"]["recorded_at_unix"]
    if rec_at is None or not np.isfinite(rec_at):
        return False, f"expected finite recorded_at_unix, got {rec_at}"
    return True, f"recorded_at_unix={rec_at}"


def test_13_corrupt_feed_rate_writes_json_not_exit_2(root: str) -> tuple[bool, str]:
    d = os.path.join(root, "t13")
    os.makedirs(d, exist_ok=True)
    key = secrets.token_bytes(32)
    kf = os.path.join(d, "venue.key")
    with open(kf, "w", encoding="utf-8") as f:
        f.write(key.hex())
    venue = os.path.join(d, "venue")
    x, out = wt_test.make_venue(venue, "pa", key, seconds=25.0)
    y = wt_test.phone(out, x, "pa", 2.0, 15.0)
    clip_path = os.path.join(d, "clip.wav")
    wavio.write(clip_path, y)

    # Corrupt dac_utc in all sec lines and remove exact sec hit so wall() hits nearest fallback
    sess_path = os.path.join(venue, f"session_{wt_test.T0}.jsonl")
    with open(sess_path, "r", encoding="utf-8") as f:
        lines = [json.loads(line) for line in f if line.strip()]
    with open(sess_path, "w", encoding="utf-8") as f:
        for rec in lines:
            if rec.get("type") == "sec":
                if rec["t"] != wt_test.T0 + 24:
                    continue
                rec["dac_utc"] = "bad_float_string"
            f.write(json.dumps(rec) + "\n")

    out_json = os.path.join(d, "detection.json")
    p = subprocess.run(
        [sys.executable, "-m", "roomstamp.wt_detect", clip_path, "-o", out_json,
         "--venue", venue, "--key-file", kf, "--fast", "--quiet"],
        capture_output=True,
        env={**os.environ, "WHOLETAKE_ASR": "none"},
    )
    file_written = os.path.isfile(out_json)
    if p.returncode == 2 or not file_written:
        return False, f"main() exited with code {p.returncode} (file_written={file_written}), violating contract"
    with open(out_json, encoding="utf-8") as f:
        doc = json.load(f)
    if doc.get("schema") != "wt-detect/1.0":
        return False, f"invalid schema in detection.json: {doc.get('schema')}"
    return True, f"exit={p.returncode}, file_written={file_written}"


def main() -> None:
    os.environ["WHOLETAKE_ASR"] = "none"
    root = tempfile.mkdtemp(prefix="test_f344_")
    failed = False
    try:
        for fn in (
            test_10_corrupt_session_sec_timestamp_no_dos_hang,
            test_11_corrupt_dac_utc_in_session_log_no_crash,
            test_13_corrupt_feed_rate_writes_json_not_exit_2,
        ):
            name = fn.__name__
            try:
                ok, msg = fn(root)
            except Exception as e:
                ok, msg = False, f"{type(e).__name__}: {e}"
            if ok:
                print(f"PASS {name}: {msg}")
            else:
                print(f"FAIL {name}: {msg}")
                failed = True
    finally:
        shutil.rmtree(root, ignore_errors=True)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
