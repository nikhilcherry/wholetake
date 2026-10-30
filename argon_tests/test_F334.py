"""Reproducer test for F334: deploy.sh and deploy.ps1 missing or flag-valued --root / --env-file."""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEPLOY_SH = REPO_ROOT / "deploy" / "deploy.sh"
DEPLOY_PS1 = REPO_ROOT / "deploy" / "deploy.ps1"


def _to_bash_path(path: Path) -> str:
    s = path.resolve().as_posix()
    if len(s) >= 3 and s[1] == ":" and s[2] == "/":
        drive = s[0].lower()
        return f"/{drive}{s[2:]}"
    return s


def main() -> int:
    work_dir = Path(tempfile.mkdtemp(prefix="wt_f334_"))
    try:
        ran_any = False
        ps_bin = shutil.which("powershell") or shutil.which("pwsh")
        if ps_bin:
            ran_any = True
            ps_cases = [
                ["check-env", "--root"],
                ["up", "--env-file"],
                ["check-env", "--root", "--dry-run"],
                ["up", "--env-file", "--dry-run"],
            ]
            for args in ps_cases:
                res = subprocess.run(
                    [ps_bin, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(DEPLOY_PS1)] + args,
                    cwd=str(work_dir),
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
                out = (res.stdout or "") + "\n" + (res.stderr or "")
                stray_dir = work_dir / "--dry-run"
                created = stray_dir.exists()
                if res.returncode == 0 or created:
                    print(
                        f"FAIL test_F334: deploy.ps1 {' '.join(args)} accepted invalid flag "
                        f"(rc={res.returncode}, created={created})"
                    )
                    return 1
                if "Missing value" not in out:
                    print(
                        f"FAIL test_F334: deploy.ps1 {' '.join(args)} did not report 'Missing value': {out.strip()}"
                    )
                    return 1

        bash_bin = shutil.which("bash")
        if bash_bin:
            probe = subprocess.run(
                [bash_bin, "-c", "echo ok"],
                cwd=str(work_dir),
                capture_output=True,
                text=True,
                timeout=10,
            )
            if probe.returncode == 0 and "ok" in (probe.stdout or ""):
                ran_any = True
                sh_script = _to_bash_path(DEPLOY_SH)
                sh_cases = [
                    ["check-env", "--root"],
                    ["up", "--env-file"],
                    ["check-env", "--root", "--dry-run"],
                    ["up", "--env-file", "--dry-run"],
                ]
                for args in sh_cases:
                    res = subprocess.run(
                        [bash_bin, sh_script] + args,
                        cwd=str(work_dir),
                        capture_output=True,
                        text=True,
                        timeout=15,
                    )
                    out = (res.stdout or "") + "\n" + (res.stderr or "")
                    stray_dir = work_dir / "--dry-run"
                    created = stray_dir.exists()
                    if "unbound variable" in out:
                        print(
                            f"FAIL test_F334: deploy.sh '{' '.join(args)}' crashed with bash unbound variable: "
                            f"{out.strip()}"
                        )
                        return 1
                    if res.returncode == 0 or created:
                        print(
                            f"FAIL test_F334: deploy.sh '{' '.join(args)}' accepted invalid flag "
                            f"(rc={res.returncode}, created={created})"
                        )
                        return 1
                    if "Missing value" not in out:
                        print(
                            f"FAIL test_F334: deploy.sh '{' '.join(args)}' did not report 'Missing value': "
                            f"{out.strip()}"
                        )
                        return 1

        if not ran_any:
            print("FAIL test_F334: neither powershell/pwsh nor bash was available")
            return 1

        print("PASS test_F334")
        return 0
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
