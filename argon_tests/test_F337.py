import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def fail(reason: str) -> None:
    print(f"FAIL test_F337: {reason}")
    sys.exit(1)


def main() -> None:
    ps1_path = REPO_ROOT / "deploy" / "deploy.ps1"
    sh_path = REPO_ROOT / "deploy" / "deploy.sh"
    if not ps1_path.is_file() or not sh_path.is_file():
        fail("deploy.ps1 or deploy.sh missing")

    sh_text = sh_path.read_text(encoding="utf-8")
    if "kill -0" not in sh_text or "^[0-9]+$" not in sh_text:
        fail("deploy.sh does not validate PID integer format and process liveness via kill -0")

    ps_bin = shutil.which("powershell") or shutil.which("pwsh")
    tmp_dir = Path(tempfile.mkdtemp(prefix="wt_f337_"))
    try:
        logs_dir = tmp_dir / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)

        if ps_bin:
            (logs_dir / "simx.pid").write_text("corrupt_not_an_int\n", encoding="utf-8")
            (logs_dir / "verdict.pid").write_text("99999999\n", encoding="utf-8")

            res_status = subprocess.run(
                [
                    ps_bin,
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(ps1_path),
                    "local-demo",
                    "status",
                    "--root",
                    str(tmp_dir),
                ],
                capture_output=True,
                text=True,
                timeout=20,
            )
            if res_status.returncode != 0:
                fail(f"deploy.ps1 local-demo status exited with {res_status.returncode}: {res_status.stderr}")
            if "pid=corrupt_not_an_int" in res_status.stdout or "pid=99999999" in res_status.stdout:
                fail(f"local-demo status blindly reports dead/corrupt PID files as running: {res_status.stdout!r}")
            for svc in ("simx", "verdict", "bot"):
                if f"local-demo {svc} stopped" not in res_status.stdout:
                    fail(f"expected 'local-demo {svc} stopped' in status output: {res_status.stdout!r}")

            # Re-seed corrupt, dead, and empty PID files for local-demo stop
            (logs_dir / "simx.pid").write_text("corrupt_not_an_int\n", encoding="utf-8")
            (logs_dir / "verdict.pid").write_text("99999999\n", encoding="utf-8")
            (logs_dir / "bot.pid").write_text("", encoding="utf-8")

            res_stop = subprocess.run(
                [
                    ps_bin,
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(ps1_path),
                    "local-demo",
                    "stop",
                    "--root",
                    str(tmp_dir),
                ],
                capture_output=True,
                text=True,
                timeout=20,
            )
            if res_stop.returncode != 0:
                fail(f"local-demo stop crashed (rc={res_stop.returncode}) on corrupt PID file: {res_stop.stderr}")
            for svc in ("simx", "verdict", "bot"):
                if (logs_dir / f"{svc}.pid").exists():
                    fail(f"local-demo stop did not remove {svc}.pid")

            # Verify live PID is reported as running
            live_pid = os.getpid()
            (logs_dir / "simx.pid").write_text(f"{live_pid}\n", encoding="utf-8")
            try:
                res_live = subprocess.run(
                    [
                        ps_bin,
                        "-NoProfile",
                        "-ExecutionPolicy",
                        "Bypass",
                        "-File",
                        str(ps1_path),
                        "local-demo",
                        "status",
                        "--root",
                        str(tmp_dir),
                    ],
                    capture_output=True,
                    text=True,
                    timeout=20,
                )
                if res_live.returncode != 0:
                    fail(f"local-demo status failed on live PID: {res_live.stderr}")
                if f"local-demo simx pid={live_pid}" not in res_live.stdout:
                    fail(f"expected live PID {live_pid} reported running, got: {res_live.stdout!r}")
            finally:
                (logs_dir / "simx.pid").unlink(missing_ok=True)

        bash_bin = shutil.which("bash")
        if bash_bin and os.name != "nt":
            (logs_dir / "simx.pid").write_text("corrupt_not_an_int\n", encoding="utf-8")
            (logs_dir / "verdict.pid").write_text("99999999\n", encoding="utf-8")
            res_sh_status = subprocess.run(
                [bash_bin, str(sh_path), "local-demo", "status", "--root", str(tmp_dir)],
                capture_output=True,
                text=True,
                timeout=20,
            )
            if res_sh_status.returncode != 0:
                fail(f"deploy.sh local-demo status failed: {res_sh_status.stderr}")
            if "pid=corrupt_not_an_int" in res_sh_status.stdout or "pid=99999999" in res_sh_status.stdout:
                fail(f"deploy.sh local-demo status reported dead/corrupt PID as running: {res_sh_status.stdout!r}")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    print("PASS test_F337")


if __name__ == "__main__":
    main()
