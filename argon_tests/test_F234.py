"""Standalone reproducer test for F234: Cross-Platform Deployment Scripts and DEPLOY.md Runbook."""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def fail(reason: str) -> None:
    print(f"FAIL test_F234: {reason}")
    sys.exit(1)


def main() -> None:
    sh_path = REPO_ROOT / "deploy" / "deploy.sh"
    ps1_path = REPO_ROOT / "deploy" / "deploy.ps1"
    deploy_md = REPO_ROOT / "deploy" / "DEPLOY.md"

    for p in (sh_path, ps1_path, deploy_md):
        if not p.is_file():
            fail(f"Missing required file: {p.relative_to(REPO_ROOT)}")

    # Run dry-run and check-env in isolated temp directory
    tmp_dir = Path(tempfile.mkdtemp(prefix="f234_repro_"))
    try:
        ps_bin = shutil.which("powershell") or shutil.which("pwsh")
        bash_bin = shutil.which("bash")
        if ps_bin:
            res = subprocess.run(
                [
                    ps_bin,
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(ps1_path),
                    "up",
                    "--dry-run",
                    "--with-daemons",
                    "--root",
                    str(tmp_dir),
                ],
                capture_output=True,
                text=True,
                timeout=20,
            )
            if res.returncode != 0:
                fail(f"deploy.ps1 failed with exit {res.returncode}: {res.stderr}")
            if "--profile daemons" not in res.stdout or "docker compose" not in res.stdout:
                fail(f"deploy.ps1 dry-run output missing expected compose command: {res.stdout}")
        elif bash_bin:
            res = subprocess.run(
                [bash_bin, str(sh_path), "up", "--dry-run", "--with-daemons", "--root", str(tmp_dir)],
                capture_output=True,
                text=True,
                timeout=20,
            )
            if res.returncode != 0:
                fail(f"deploy.sh failed with exit {res.returncode}: {res.stderr}")
            if "--profile daemons" not in res.stdout or "docker compose" not in res.stdout:
                fail(f"deploy.sh dry-run output missing expected compose command: {res.stdout}")
        else:
            fail("No shell interpreter found to test deploy scripts")

        for rel in ("data/witness", "data/verdicts", "data/simx", "logs", "keys"):
            if not (tmp_dir / rel).is_dir():
                fail(f"Runtime directory not created: {rel}")

        key_file = tmp_dir / "keys" / "venue.key"
        if not key_file.is_file():
            fail("Ephemeral keys/venue.key was not created")
        key_hex = key_file.read_text(encoding="utf-8").strip()
        if not re.fullmatch(r"[0-9a-f]{64}", key_hex):
            fail(f"Invalid keys/venue.key content: {key_hex!r}")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    # Run full deploy/test_deploy.py unittest suite
    import deploy.test_deploy as td

    loader = unittest.TestLoader()
    suite = loader.loadTestsFromModule(td)
    if not hasattr(td, "TestDeployScriptsAndRunbook"):
        fail("deploy/test_deploy.py is missing TestDeployScriptsAndRunbook")

    runner = unittest.TextTestRunner(stream=open(os.devnull, "w"), verbosity=0)
    result = runner.run(suite)
    if not result.wasSuccessful():
        details = [
            f"{tc}: {tb.splitlines()[-1] if tb else 'error'}"
            for tc, tb in (result.failures + result.errors)
        ]
        fail("unittest failures: " + "; ".join(details))

    print("PASS test_F234")


if __name__ == "__main__":
    main()
