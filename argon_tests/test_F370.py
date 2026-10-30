"""Reproducer for F370: deploy.sh and deploy.ps1 local-demo must export ROOMSTAMP_KEY_FILE,
ROOMSTAMP_LOG_DIR, and WT_UPLOADS_DIR (and create data/uploads) when --root is used.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_02_deploy_local_demo_roomstamp_env_wiring() -> None:
    sh_path = REPO_ROOT / "deploy" / "deploy.sh"
    ps1_path = REPO_ROOT / "deploy" / "deploy.ps1"
    sh_text = sh_path.read_text(encoding="utf-8")
    ps1_text = ps1_path.read_text(encoding="utf-8")

    sh_demo_match = re.search(r"run_local_demo\(\)\s*\{(.*?)\n\}", sh_text, re.DOTALL)
    ps1_demo_match = re.search(r"function\s+Invoke-LocalDemo\s*\{(.*?)\n\}", ps1_text, re.DOTALL)
    assert sh_demo_match, "Could not locate run_local_demo() in deploy/deploy.sh"
    assert ps1_demo_match, "Could not locate Invoke-LocalDemo in deploy/deploy.ps1"

    sh_demo = sh_demo_match.group(1)
    ps1_demo = ps1_demo_match.group(1)

    sh_has_roomstamp_env = (
        "ROOMSTAMP_KEY_FILE=" in sh_demo
        and "ROOMSTAMP_LOG_DIR=" in sh_demo
        and "$ROOT_DIR/keys/venue.key" in sh_demo
        and "--port 8790" in sh_demo
    )
    ps1_has_roomstamp_env = (
        "$env:ROOMSTAMP_KEY_FILE" in ps1_demo
        and "$env:ROOMSTAMP_LOG_DIR" in ps1_demo
        and "ROOMSTAMP_KEY_FILE=" in ps1_demo
        and "ROOMSTAMP_LOG_DIR=" in ps1_demo
    )
    if not (sh_has_roomstamp_env and ps1_has_roomstamp_env):
        raise AssertionError(
            f"deploy.sh (has_roomstamp_env={sh_has_roomstamp_env}) and "
            f"deploy.ps1 (has_roomstamp_env={ps1_has_roomstamp_env}) create "
            "$ROOT_DIR/keys/venue.key and $ROOT_DIR/logs in local-demo, "
            "but never export ROOMSTAMP_KEY_FILE or ROOMSTAMP_LOG_DIR to verdict serve!"
        )


def test_09_upload_endpoint_filename_sanitization_and_root_isolation() -> None:
    sh_path = REPO_ROOT / "deploy" / "deploy.sh"
    ps1_path = REPO_ROOT / "deploy" / "deploy.ps1"
    sh_text = sh_path.read_text(encoding="utf-8")
    ps1_text = ps1_path.read_text(encoding="utf-8")

    sh_demo_match = re.search(r"run_local_demo\(\)\s*\{(.*?)\n\}", sh_text, re.DOTALL)
    ps1_demo_match = re.search(r"function\s+Invoke-LocalDemo\s*\{(.*?)\n\}", ps1_text, re.DOTALL)
    sh_demo = sh_demo_match.group(1) if sh_demo_match else ""
    ps1_demo = ps1_demo_match.group(1) if ps1_demo_match else ""

    sh_sets_uploads = (
        "WT_UPLOADS_DIR=" in sh_demo
        and "$ROOT_DIR/data/uploads" in sh_demo
        and "$ROOT_DIR/data/uploads" in sh_text
    )
    ps1_sets_uploads = (
        "$env:WT_UPLOADS_DIR" in ps1_demo
        and "WT_UPLOADS_DIR=" in ps1_demo
        and "data/uploads" in ps1_text
    )
    if not (sh_sets_uploads and ps1_sets_uploads):
        raise AssertionError(
            "deploy.sh and deploy.ps1 local-demo do not set WT_UPLOADS_DIR=$ROOT_DIR/data/uploads "
            "when --root <path> is used, causing /api/verdict/upload to write uploaded clips "
            "into REPO_ROOT/data/uploads!"
        )

    tmp_root = Path(tempfile.mkdtemp(prefix="wt_f370_"))
    try:
        ps_bin = shutil.which("powershell") or shutil.which("pwsh")
        bash_bin = shutil.which("bash")
        if ps_bin:
            res = subprocess.run(
                [ps_bin, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ps1_path),
                 "local-demo", "start", "--dry-run", "--root", str(tmp_root)],
                capture_output=True,
                text=True,
                timeout=20,
            )
            if res.returncode != 0:
                raise AssertionError(f"deploy.ps1 local-demo start --dry-run failed: {res.stderr}")
            out = res.stdout
            for var in ("WT_UPLOADS_DIR=", "ROOMSTAMP_LOG_DIR=", "ROOMSTAMP_KEY_FILE="):
                if var not in out:
                    raise AssertionError(f"deploy.ps1 dry-run missing {var} in output: {out}")
            if not (tmp_root / "data" / "uploads").is_dir():
                raise AssertionError("deploy.ps1 did not create $ROOT_DIR/data/uploads")
        elif bash_bin and os.name != "nt":
            res = subprocess.run(
                [bash_bin, str(sh_path), "local-demo", "start", "--dry-run", "--root", str(tmp_root)],
                capture_output=True,
                text=True,
                timeout=20,
            )
            if res.returncode != 0:
                raise AssertionError(f"deploy.sh local-demo start --dry-run failed: {res.stderr}")
            out = res.stdout
            for var in ("WT_UPLOADS_DIR=", "ROOMSTAMP_LOG_DIR=", "ROOMSTAMP_KEY_FILE="):
                if var not in out:
                    raise AssertionError(f"deploy.sh dry-run missing {var} in output: {out}")
            if not (tmp_root / "data" / "uploads").is_dir():
                raise AssertionError("deploy.sh did not create $ROOT_DIR/data/uploads")
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)


def main() -> int:
    failed = False
    for fn in (
        test_02_deploy_local_demo_roomstamp_env_wiring,
        test_09_upload_endpoint_filename_sanitization_and_root_isolation,
    ):
        name = fn.__name__
        try:
            fn()
            print(f"PASS {name}")
        except Exception as exc:
            print(f"FAIL {name}: {exc}")
            failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
