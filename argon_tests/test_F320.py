from __future__ import annotations

import os
import secrets
import shutil
import sys
import tempfile

from roomstamp import wavio, wt_detect, wt_test


def main() -> None:
    root = tempfile.mkdtemp(prefix="wt_f320_")
    try:
        key = secrets.token_bytes(32)
        kf = os.path.join(root, "venue.key")
        with open(kf, "w", encoding="utf-8") as f:
            f.write(key.hex())

        ext_dir = os.path.join(root, "external_logs")
        empty_venue = os.path.join(root, "empty_venue")
        os.makedirs(empty_venue, exist_ok=True)

        # Create a stamped session without a clean feed in ext_dir (outside empty_venue)
        x, out = wt_test.make_venue(ext_dir, "pa", key, seconds=40.0, feed=False)
        sess_path = os.path.join(ext_dir, f"session_{wt_test.T0}.jsonl")
        want_sid = f"session_{wt_test.T0}"

        clip_path = os.path.join(root, "clip_32s.wav")
        wavio.write(clip_path, wt_test.phone(out, x, "pa", 6.0, 32.0))

        # 1. Test CLI two-step flow (--fast with --session outside --venue, then --layers l2 --merge without --session)
        out_cli = os.path.join(root, "d_cli.json")
        rc1, d1, _ = wt_test.cli(
            clip_path, "-o", out_cli, "--venue", empty_venue, "--key-file", kf, "--session", sess_path, "--fast"
        )
        if rc1 != 0 or not d1 or d1["verdict"]["venue"]["session"] != want_sid or d1["pending_layers"] != ["l2"]:
            print(f"FAIL test_F320: Step 1 (CLI) unexpected result: venue={d1.get('verdict', {}).get('venue') if d1 else None}")
            sys.exit(1)

        rc2, d2, _ = wt_test.cli(
            clip_path, "-o", out_cli, "--venue", empty_venue, "--key-file", kf, "--layers", "l2", "--merge"
        )
        v2 = (d2 or {}).get("verdict", {}).get("venue", {})
        l2_st = (d2 or {}).get("layers", {}).get("l2", {})
        if rc2 != 0 or v2.get("session") != want_sid or l2_st.get("status") not in ("present", "likely"):
            print(
                f"FAIL test_F320: Step 2 (--layers l2 --merge) lost Step 1's session: "
                f"venue={v2}, l2={l2_st.get('status')} ({l2_st.get('summary')})"
            )
            sys.exit(1)

        # 2. Test Python API run() two-step flow (where argv does not contain --session)
        out_api = os.path.join(root, "d_api.json")
        da1 = wt_detect.run(
            clip_path, out_path=out_api, venue_dir=empty_venue, key_file=kf, session_log=sess_path, fast=True
        )
        if da1["verdict"]["venue"]["session"] != want_sid:
            print(f"FAIL test_F320: Step 1 (API) unexpected venue: {da1['verdict']['venue']}")
            sys.exit(1)
        da2 = wt_detect.run(
            clip_path, out_path=out_api, venue_dir=empty_venue, key_file=kf, layers="l2", merge=True
        )
        va2 = da2.get("verdict", {}).get("venue", {})
        la2 = da2.get("layers", {}).get("l2", {})
        if va2.get("session") != want_sid or la2.get("status") not in ("present", "likely"):
            print(
                f"FAIL test_F320: Step 2 (API --merge) lost Step 1's session: "
                f"venue={va2}, l2={la2.get('status')} ({la2.get('summary')})"
            )
            sys.exit(1)

        print("PASS test_F320")
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    main()
