"""Reproducer test for F371: VerdictManager cache invalidation on ROOMSTAMP_KEY_FILE and ROOMSTAMP_LOG_DIR changes."""
import hashlib
import os
import shutil
import sys
import tempfile
from pathlib import Path

from verdict.service import VerdictManager


def test_01_cache_invalidation_across_key_rotation_and_session_logs() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="wt_f371_"))
    old_key_env = os.environ.get("ROOMSTAMP_KEY_FILE")
    old_log_env = os.environ.get("ROOMSTAMP_LOG_DIR")
    try:
        witness_root = tmp / "witness"
        verdicts_dir = tmp / "verdicts"
        logs_dir = tmp / "logs"
        keys_dir = tmp / "keys"
        for d in (witness_root, verdicts_dir, logs_dir, keys_dir):
            d.mkdir(parents=True, exist_ok=True)

        clip_wav = tmp / "clip.wav"
        clip_bytes = b"RIFF\x24\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00\x80>\x00\x00\x00}\x00\x00\x02\x00\x10\x00data\x00\x00\x00\x00"
        clip_wav.write_bytes(clip_bytes)
        clip_sha256 = hashlib.sha256(clip_bytes).hexdigest()

        key_file = keys_dir / "venue.key"
        key_1 = "11" * 32
        key_2 = "22" * 32
        key_file.write_text(key_1 + "\n", encoding="utf-8")

        os.environ["ROOMSTAMP_KEY_FILE"] = str(key_file)
        os.environ["ROOMSTAMP_LOG_DIR"] = str(logs_dir)

        detect_calls = 0

        def fake_fetch(url: str) -> dict:
            return {
                "url": url,
                "wav": str(clip_wav),
                "sha256": clip_sha256,
                "duration_s": 10.0,
                "title": "test clip",
            }

        def fake_detect(wav_path: Path | str, wroot: Path | str) -> dict:
            nonlocal detect_calls
            detect_calls += 1
            cur_key = Path(os.environ["ROOMSTAMP_KEY_FILE"]).read_text(encoding="utf-8").strip()
            cur_logs = list(Path(os.environ["ROOMSTAMP_LOG_DIR"]).glob("session_*.jsonl"))
            stamp_ok = (cur_key == key_2) and (len(cur_logs) > 0)
            return {
                "detector": {"name": "wt-detect", "version": "0.1"},
                "clip": {"duration_s": 10.0, "sha256": clip_sha256},
                "stamp": {
                    "present": stamp_ok,
                    "layer1_score": 0.95 if stamp_ok else 0.0,
                    "layer2_score": 0.90 if stamp_ok else 0.0,
                    "recorded_at_utc": "2026-10-31T06:12:07Z" if stamp_ok else None,
                    "venue": "demo-hall" if stamp_ok else None,
                    "cuts": [],
                },
                "witness": {
                    "verdict": "not_found",
                    "stream": None,
                    "pieces": [],
                    "removed": [],
                    "inserted": [],
                    "transcript_clip": "",
                },
            }

        mgr = VerdictManager(
            witness_root=witness_root,
            verdicts_dir=verdicts_dir,
            fetch_fn=fake_fetch,
            detect_fn=fake_detect,
            start_worker=False,
        )

        # 1. Initial submission -> runs detector (detect_calls == 1, stamp.present == False)
        sub1 = mgr.submit(str(clip_wav), allow_local=True)
        mgr.process_one(sub1["id"])
        j1 = mgr.get_job(sub1["id"])
        assert j1 is not None and j1["status"] == "done", f"job 1 failed: {j1}"
        if detect_calls != 1:
            raise AssertionError(f"Expected detect_calls=1 after first run, got {detect_calls}")

        # 2. Repeat submission with identical key & logs -> hits cache (detect_calls == 1)
        sub2 = mgr.submit(str(clip_wav), allow_local=True)
        mgr.process_one(sub2["id"])
        j2 = mgr.get_job(sub2["id"])
        assert j2 is not None and j2["status"] == "done", f"job 2 failed: {j2}"
        if detect_calls != 1:
            raise AssertionError(f"Expected cache hit when key/logs unchanged (detect_calls=1), got {detect_calls}")

        # 3. Rotate ROOMSTAMP_KEY_FILE -> must invalidate cache (detect_calls == 2)
        key_file.write_text(key_2 + "\n", encoding="utf-8")
        sub3 = mgr.submit(str(clip_wav), allow_local=True)
        mgr.process_one(sub3["id"])
        j3 = mgr.get_job(sub3["id"])
        assert j3 is not None and j3["status"] == "done", f"job 3 failed: {j3}"
        if detect_calls != 2:
            raise AssertionError(
                f"VerdictManager served stale cached result across ROOMSTAMP_KEY_FILE rotation (detect_calls={detect_calls})"
            )

        # 4. Emit new session log into ROOMSTAMP_LOG_DIR -> must invalidate cache (detect_calls == 3, stamp.present == True)
        (logs_dir / "session_1730355127.jsonl").write_text(
            '{"type": "session", "spec": "roomstamp-v1", "t_start": 1730355127}\n', encoding="utf-8"
        )
        sub4 = mgr.submit(str(clip_wav), allow_local=True)
        mgr.process_one(sub4["id"])
        j4 = mgr.get_job(sub4["id"])
        assert j4 is not None and j4["status"] == "done", f"job 4 failed: {j4}"
        stamp_present = ((j4.get("result") or {}).get("detection") or {}).get("stamp", {}).get("present")
        if detect_calls != 3 or stamp_present is not True:
            raise AssertionError(
                f"VerdictManager served stale cached result across ROOMSTAMP_KEY_FILE rotation and ROOMSTAMP_LOG_DIR update "
                f"(detect_calls={detect_calls}, stamp.present={stamp_present})"
            )
    finally:
        if old_key_env is None:
            os.environ.pop("ROOMSTAMP_KEY_FILE", None)
        else:
            os.environ["ROOMSTAMP_KEY_FILE"] = old_key_env
        if old_log_env is None:
            os.environ.pop("ROOMSTAMP_LOG_DIR", None)
        else:
            os.environ["ROOMSTAMP_LOG_DIR"] = old_log_env
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    try:
        test_01_cache_invalidation_across_key_rotation_and_session_logs()
        print("PASS test_01_cache_invalidation_across_key_rotation_and_session_logs")
    except Exception as exc:
        print(f"FAIL test_01_cache_invalidation_across_key_rotation_and_session_logs: {exc}")
        sys.exit(1)
