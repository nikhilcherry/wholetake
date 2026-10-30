"""Reproducer test for F338: VerdictManager._worker_loop survives resumed job missing 'url'."""
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

from verdict.service import VerdictManager


def main() -> None:
    tmp_dir = Path(tempfile.mkdtemp(prefix="test_f338_"))
    mgr = None
    try:
        verdicts_dir = tmp_dir / "verdicts"
        witness_root = tmp_dir / "witness"
        verdicts_dir.mkdir(parents=True, exist_ok=True)
        witness_root.mkdir(parents=True, exist_ok=True)

        # Resumed job missing 'url' key entirely
        (verdicts_dir / "badjob01.json").write_text(
            json.dumps(
                {
                    "id": "badjob01",
                    "status": "queued",
                    "created_utc": "2026-10-29T00:00:00Z",
                    "updated_utc": "2026-10-29T00:00:00Z",
                }
            ),
            encoding="utf-8",
        )
        # Resumed job with null 'url'
        (verdicts_dir / "badjob02.json").write_text(
            json.dumps(
                {
                    "id": "badjob02",
                    "url": None,
                    "status": "fetching",
                    "created_utc": "2026-10-29T00:00:01Z",
                    "updated_utc": "2026-10-29T00:00:01Z",
                }
            ),
            encoding="utf-8",
        )

        dummy_wav = tmp_dir / "clip.wav"
        dummy_wav.write_bytes(b"RIFF" + b"\x00" * 40)

        def mock_fetch(url: str) -> dict:
            return {
                "url": url,
                "wav": str(dummy_wav),
                "sha256": "a" * 64,
                "duration_s": 10.0,
                "title": "test",
            }

        def mock_detect(wav_path, wroot) -> dict:
            return {
                "detector": {"name": "wt-detect", "version": "0.1"},
                "clip": {"duration_s": 10.0, "sha256": "a" * 64},
                "stamp": None,
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
            fetch_fn=mock_fetch,
            detect_fn=mock_detect,
            label_fn=lambda det, wroot: None,
            start_worker=True,
        )

        sub = mgr.submit("http://127.0.0.1:8791/valid.mp4", allow_local=True)
        valid_id = sub["id"]

        deadline = time.time() + 5.0
        while time.time() < deadline:
            j = mgr.get_job(valid_id)
            if j and j.get("status") in ("done", "error"):
                break
            if not mgr._worker.is_alive():
                break
            time.sleep(0.05)

        if not mgr._worker.is_alive():
            print("FAIL test_F338: VerdictManager worker thread died after encountering resumed job without 'url'")
            sys.exit(1)

        b1 = mgr.get_job("badjob01")
        if not b1 or b1.get("status") != "error":
            print(f"FAIL test_F338: expected badjob01 status 'error', got {b1}")
            sys.exit(1)

        b2 = mgr.get_job("badjob02")
        if not b2 or b2.get("status") != "error":
            print(f"FAIL test_F338: expected badjob02 status 'error', got {b2}")
            sys.exit(1)

        good = mgr.get_job(valid_id)
        if not good or good.get("status") != "done":
            print(f"FAIL test_F338: subsequent valid job did not complete: {good}")
            sys.exit(1)

        print("PASS test_F338")
    finally:
        if mgr is not None:
            mgr.close()
        shutil.rmtree(tmp_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
