import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PS1_PATH = REPO_ROOT / "deploy" / "deploy.ps1"


def fail(reason: str) -> None:
    print(f"FAIL test_F336: {reason}")
    sys.exit(1)


def main() -> None:
    if not PS1_PATH.is_file():
        fail("deploy/deploy.ps1 does not exist")

    ps1_text = PS1_PATH.read_text(encoding="utf-8")

    # 1. Static check: Start-Process for simx must not pass bare unquoted $simxData in -ArgumentList
    m = re.search(
        r'\$pSimx\s*=\s*Start-Process\s+-FilePath\s+"python"\s+-ArgumentList\s+(@\([^)]+\))',
        ps1_text,
    )
    if not m:
        fail("Could not find $pSimx Start-Process -ArgumentList in deploy/deploy.ps1")

    arg_list_expr = m.group(1)
    if re.search(r'"--data"\s*,\s*\$simxData\b', arg_list_expr):
        fail(
            f"deploy.ps1 passes unquoted $simxData in Start-Process -ArgumentList ({arg_list_expr}), "
            "splitting paths with spaces on Windows"
        )

    # Ensure $simxData is wrapped in escaped quotes either inline or via $quotedSimxData
    if "$quotedSimxData" in arg_list_expr:
        if not re.search(r'\$quotedSimxData\s*=\s*"`"\$simxData`""', ps1_text):
            fail("$quotedSimxData is used in -ArgumentList but not defined with escaped quotes")
    elif not re.search(r'"--data"\s*,\s*"`"\$simxData`""', arg_list_expr):
        fail(f"Start-Process -ArgumentList does not quote $simxData: {arg_list_expr}")

    # 2. Dynamic check with PowerShell on Windows using a --root path containing spaces
    ps_bin = shutil.which("powershell") or shutil.which("pwsh")
    if ps_bin:
        base_tmp = Path(tempfile.mkdtemp(prefix="wt_f336_"))
        try:
            space_root = base_tmp / "root with spaces"
            res = subprocess.run(
                [
                    ps_bin,
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(PS1_PATH),
                    "local-demo",
                    "start",
                    "--dry-run",
                    "--root",
                    str(space_root),
                ],
                capture_output=True,
                text=True,
                timeout=20,
            )
            if res.returncode != 0:
                fail(f"deploy.ps1 local-demo start --dry-run failed (rc={res.returncode}): {res.stderr}")
            if '--data "' not in res.stdout:
                fail(f"deploy.ps1 --dry-run output did not quote --data path with spaces: {res.stdout!r}")
            if not (space_root / "data" / "simx").is_dir():
                fail("deploy.ps1 did not create data/simx under root path with spaces")
        finally:
            shutil.rmtree(base_tmp, ignore_errors=True)

    print("PASS test_F336")


if __name__ == "__main__":
    main()
