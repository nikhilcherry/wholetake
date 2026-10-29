"""Reproducer test for F295: VerdictManager._load_existing_jobs crashes on null created_utc."""
import json
import shutil
import sys
import tempfile
from pathlib import Path

from verdict.service import VerdictManager


def main() -> None:
    tmp_dir = Path(tempfile.mkdtemp(prefix="wt_test_f295_"))
    try:
        witness_root = tmp_dir / "witness"
        verdicts_dir = tmp_dir / "verdicts"
        witness_root.mkdir(parents=True, exist_ok=True)
        verdicts_dir.mkdir(parents=True, exist_ok=True)

        job_a = {
            "id": "job_a",
            "url": "https://example.com/a.mp4",
            "status": "queued",
            "created_utc": "2026-10-31T10:00:00Z",
            "updated_utc": "2026-10-31T10:00:00Z",
        }
        job_b = {
            "id": "job_b",
            "url": "https://example.com/b.mp4",
            "status": "detecting",
            "created_utc": None,
            "updated_utc": "2026-10-31T10:01:00Z",
        }
        (verdicts_dir / "job_a.json").write_text(json.dumps(job_a), encoding="utf-8")
        (verdicts_dir / "job_b.json").write_text(json.dumps(job_b), encoding="utf-8")

        mgr = VerdictManager(
            witness_root=witness_root,
            verdicts_dir=verdicts_dir,
            start_worker=False,
        )
        queued: list[str] = []
        while not mgr._queue.empty():
            item = mgr._queue.get_nowait()
            if item is not None:
                queued.append(item)

        if queued != ["job_b", "job_a"]:
            print(f"FAIL verdict_manager_corrupt_persisted_jobs: unexpected queue order {queued!r}")
            sys.exit(1)

        jobs = mgr.list_jobs()
        if [j["id"] for j in jobs] != ["job_a", "job_b"]:
            print(f"FAIL verdict_manager_corrupt_persisted_jobs: unexpected list_jobs order {[j['id'] for j in jobs]!r}")
            sys.exit(1)

        print("PASS verdict_manager_corrupt_persisted_jobs")
    except Exception as exc:
        print(f"FAIL verdict_manager_corrupt_persisted_jobs: {exc}")
        sys.exit(1)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
