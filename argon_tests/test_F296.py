"""Reproducer test for F296: Path traversal in VerdictManager.get_job / _job_path / _load_existing_jobs."""
import json
import shutil
import sys
import tempfile
from pathlib import Path

from verdict.service import VerdictManager


def test_verdict_manager_path_traversal() -> None:
    tmp_root = Path(tempfile.mkdtemp(prefix="wt_f296_"))
    try:
        verdicts_dir = tmp_root / "verdicts"
        witness_root = tmp_root / "witness"
        verdicts_dir.mkdir(parents=True, exist_ok=True)
        witness_root.mkdir(parents=True, exist_ok=True)

        # Sensitive file outside verdicts_dir
        outside_file = tmp_root / "outside.json"
        outside_payload = {"id": "leaked", "status": "done", "secret": "topsecret"}
        outside_file.write_text(json.dumps(outside_payload), encoding="utf-8")

        # Valid job inside verdicts_dir
        valid_file = verdicts_dir / "valid_job_123.json"
        valid_file.write_text(
            json.dumps({"id": "valid_job_123", "status": "done", "created_utc": "2026-10-29T00:00:00Z"}),
            encoding="utf-8",
        )

        # Crafted job JSON attempting path traversal on reload / _save_job -> _job_path
        poison_file = verdicts_dir / "poison.json"
        poison_file.write_text(
            json.dumps({"id": "../outside", "status": "queued", "secret": "clobbered"}),
            encoding="utf-8",
        )

        mgr = VerdictManager(witness_root=witness_root, verdicts_dir=verdicts_dir, start_worker=False)

        # 1. Verify outside.json was not overwritten via _load_existing_jobs -> _save_job -> _job_path("../outside")
        after_outside = json.loads(outside_file.read_text(encoding="utf-8"))
        if after_outside.get("secret") != "topsecret":
            print(f"FAIL F296_outside_file_clobbered: outside.json was overwritten via traversal: {after_outside}")
            sys.exit(1)

        # 2. Verify traversal IDs were not loaded into mgr._jobs / list_jobs()
        listed_ids = {j.get("id") for j in mgr.list_jobs()}
        if "../outside" in listed_ids or "../outside" in mgr._jobs:
            print(f"FAIL F296_jobs_cache_polluted: traversal job_id loaded into manager: {listed_ids}")
            sys.exit(1)

        # 3. Verify get_job rejects traversal payloads and does not leak outside.json
        for bad_id in ("../outside", "..\\outside", "..", "a/b", "a\\b", ""):
            res = mgr.get_job(bad_id)
            if res is not None:
                print(f"FAIL F296_get_job_traversal: get_job({bad_id!r}) leaked {res}")
                sys.exit(1)

        # 4. Verify _job_path enforces strict job_id validation and directory containment
        for bad_id in ("../outside", "..\\outside", "..", "/outside", "outside/../../secret"):
            try:
                p = mgr._job_path(bad_id)
            except ValueError:
                pass
            else:
                print(f"FAIL F296_job_path_traversal: _job_path({bad_id!r}) returned {p} instead of raising ValueError")
                sys.exit(1)

        # 5. Verify valid job is still accessible via get_job
        valid_File_data = mgr.get_job("valid_job_123")
        if not isinstance(valid_File_data, dict) or valid_File_data.get("id") != "valid_job_123":
            print(f"FAIL F296_valid_job: expected valid_job_123, got {valid_File_data}")
            sys.exit(1)

        print("PASS F296_verdict_manager_path_traversal")
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)


if __name__ == "__main__":
    test_verdict_manager_path_traversal()
