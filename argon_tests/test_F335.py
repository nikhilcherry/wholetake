"""Reproducer test for F335: deploy.sh and deploy.ps1 check-env corrupt venue.key handling."""
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PS1_PATH = REPO_ROOT / "deploy" / "deploy.ps1"
SH_PATH = REPO_ROOT / "deploy" / "deploy.sh"
HEX64_RE = re.compile(r"^[0-9a-fA-F]{64}$")


def _fail(msg: str) -> None:
    print(f"FAIL test_F335: {msg}")
    sys.exit(1)


def _run_ps1_check_env(ps_bin: str, root_dir: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [ps_bin, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(PS1_PATH), "check-env", "--root", str(root_dir)],
        capture_output=True,
        text=True,
        timeout=20,
    )


def _run_sh_check_env(bash_bin: str, root_dir: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [bash_bin, SH_PATH.as_posix(), "check-env", "--root", root_dir.as_posix()],
        capture_output=True,
        text=True,
        timeout=20,
    )


def _test_runner(name: str, run_fn) -> None:
    corrupt_cases = [
        ("non-hex-64", "Z" * 64 + "\n"),
        ("whitespace-only", "   \n\t\n"),
        ("truncated-hex", "deadbeef\n"),
        ("overlong-hex", "a" * 66 + "\n"),
    ]
    for label, bad_content in corrupt_cases:
        tmp = Path(tempfile.mkdtemp(prefix=f"f335_{name}_{label}_"))
        try:
            keys_dir = tmp / "keys"
            keys_dir.mkdir(parents=True, exist_ok=True)
            key_file = keys_dir / "venue.key"
            key_file.write_text(bad_content, encoding="utf-8")

            res = run_fn(tmp)
            if res.returncode != 0:
                _fail(f"{name} ({label}) exited with rc={res.returncode}: {res.stderr}")
            if "[check-env] OK" not in res.stdout:
                _fail(f"{name} ({label}) did not print [check-env] OK: {res.stdout}")

            after = key_file.read_text(encoding="utf-8").strip()
            if not HEX64_RE.match(after):
                _fail(f"{name} ({label}) left invalid venue.key: {after!r}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    # Valid 64-hex key should be preserved
    tmp_valid = Path(tempfile.mkdtemp(prefix=f"f335_{name}_valid_"))
    try:
        keys_dir = tmp_valid / "keys"
        keys_dir.mkdir(parents=True, exist_ok=True)
        key_file = keys_dir / "venue.key"
        valid_key = "a1b2c3d4" * 8
        key_file.write_text(valid_key + "\n", encoding="utf-8")

        res = run_fn(tmp_valid)
        if res.returncode != 0:
            _fail(f"{name} (valid) exited with rc={res.returncode}: {res.stderr}")
        after = key_file.read_text(encoding="utf-8").strip()
        if after != valid_key:
            _fail(f"{name} (valid) overwrote valid key {valid_key!r} with {after!r}")
    finally:
        shutil.rmtree(tmp_valid, ignore_errors=True)


def main() -> None:
    ran_any = False
    ps_bin = shutil.which("powershell") or shutil.which("pwsh")
    if ps_bin:
        _test_runner("deploy.ps1", lambda root: _run_ps1_check_env(ps_bin, root))
        ran_any = True

    bash_bin = shutil.which("bash")
    if bash_bin:
        # Verify bash can run a basic command before invoking deploy.sh (avoid broken WSL bash stubs on Windows)
        probe = subprocess.run([bash_bin, "-c", "echo ok"], capture_output=True, text=True, timeout=5)
        if probe.returncode == 0 and "ok" in probe.stdout:
            _test_runner("deploy.sh", lambda root: _run_sh_check_env(bash_bin, root))
            ran_any = True

    if not ran_any:
        _fail("Neither powershell/pwsh nor bash available")

    print("PASS test_F335")


if __name__ == "__main__":
    main()
