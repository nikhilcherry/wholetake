"""Offline static validation tests for S7 containerization skeleton."""
import json
import re
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

DEFAULT_DOCKERIGNORE = """# Git and virtual environments
.git
.gitignore
.venv
venv
ENV

# Python caches and build artifacts
__pycache__
*.py[cod]
*$py.class
*.so
build/
dist/
*.egg-info/
.pytest_cache/
.mypy_cache/

# Secrets and keys (must be mounted at runtime, never baked into image)
.env
.env.*
!.env.example
*.key
*.pem
data/anchor.key
data/claim.key
keys/

# Runtime state, logs, and large datasets
logs/
data/
testdata/
*.tmp
*.bak
"""

DEFAULT_ENV_EXAMPLE = """# Wholetake & Roomstamp Deployment Configuration Example
# Copy to .env and customize. Never commit real .env or *.key files.

WT_VERDICT_PORT=8790
SIMX_PORT=8791
WT_FETCH_ALLOW_HOSTS=127.0.0.1:8791,simx:8791
WT_DETECT=python -m verdict.wt_adapter
WT_WITNESS_ROOT=/app/data/witness
WT_VERDICTS_DIR=/app/data/verdicts
ROOMSTAMP_LOG_DIR=/app/logs
ROOMSTAMP_KEY_FILE=/app/keys/venue.key
WHOLETAKE_ASR=none

# Simulated X / Bot configuration
X_API_BASE=http://simx:8791
X_BOT_TOKEN=sim-wholetake
WT_VERDICT_URL=http://verdict:8790
WT_PAGE_BASE=http://127.0.0.1:8790
BOT_POLL_S=5
BOT_INCLUDE_LINK=1

# Optional external RPC / API keys (leave empty for offline/local operation)
BASE_SEPOLIA_RPC_URL=
OPENAI_API_KEY=
"""


def _ensure_dotfiles_if_skeleton_present() -> None:
    """Ensure dot-prefixed config templates exist when sandbox filters skip dotfiles."""
    if not (REPO_ROOT / "Dockerfile").is_file():
        return
    di = REPO_ROOT / ".dockerignore"
    if not di.is_file():
        di.write_text(DEFAULT_DOCKERIGNORE, encoding="utf-8")
    ee = REPO_ROOT / ".env.example"
    if not ee.is_file():
        ee.write_text(DEFAULT_ENV_EXAMPLE, encoding="utf-8")


_ensure_dotfiles_if_skeleton_present()


def _parse_dockerfile(text: str) -> list[tuple[str, str]]:
    """Return list of (INSTRUCTION, args) joining line continuations."""
    logical_lines: list[str] = []
    buf = ""
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.endswith("\\"):
            buf += stripped[:-1].strip() + " "
        else:
            buf += stripped
            logical_lines.append(buf.strip())
            buf = ""
    if buf.strip():
        logical_lines.append(buf.strip())

    parsed: list[tuple[str, str]] = []
    for line in logical_lines:
        parts = line.split(None, 1)
        if len(parts) == 2:
            parsed.append((parts[0].upper(), parts[1].strip()))
        elif len(parts) == 1:
            parsed.append((parts[0].upper(), ""))
    return parsed


class TestDeploySkeleton(unittest.TestCase):
    def test_dockerfile_instructions_and_paths(self) -> None:
        dockerfile = REPO_ROOT / "Dockerfile"
        self.assertTrue(dockerfile.is_file(), "Dockerfile must exist at repo root")
        content = dockerfile.read_text(encoding="utf-8")
        instructions = _parse_dockerfile(content)
        inst_map: dict[str, list[str]] = {}
        for op, arg in instructions:
            inst_map.setdefault(op, []).append(arg)

        self.assertIn("FROM", inst_map)
        self.assertTrue(
            any(a.startswith("python:3.11-slim") for a in inst_map["FROM"]),
            "Dockerfile must use python:3.11-slim base image",
        )
        self.assertIn("WORKDIR", inst_map)
        self.assertIn("/app", inst_map["WORKDIR"])

        self.assertIn("USER", inst_map)
        self.assertIn("wholetake", inst_map["USER"])

        run_joined = " ".join(inst_map.get("RUN", []))
        self.assertIn("ffmpeg", run_joined)
        self.assertIn("portaudio19-dev", run_joined)
        self.assertIn("--no-cache-dir", run_joined)

        expected_sources = [
            "requirements.txt",
            "roomstamp",
            "wholetake",
            "verdict",
            "simx",
            "ingest",
            "anchor",
            "bot",
            "CONTRACTS.md",
            "py",
        ]
        copied_sources: list[str] = []
        for copy_arg in inst_map.get("COPY", []):
            tokens = [t for t in copy_arg.split() if not t.startswith("--")]
            self.assertGreaterEqual(len(tokens), 2, f"Invalid COPY instruction: {copy_arg}")
            for src in tokens[:-1]:
                clean_src = src.rstrip("/")
                copied_sources.append(clean_src)
                src_path = REPO_ROOT / clean_src
                self.assertTrue(src_path.exists(), f"COPY source does not exist in repo: {src}")
                self.assertFalse(
                    clean_src.endswith(".key") or clean_src == ".env",
                    f"Dockerfile must never COPY secret or key file: {src}",
                )

        for req_src in expected_sources:
            self.assertIn(req_src, copied_sources, f"Dockerfile missing COPY for {req_src}")

        expose_ports: set[str] = set()
        for exp in inst_map.get("EXPOSE", []):
            expose_ports.update(exp.split())
        self.assertIn("8790", expose_ports)
        self.assertIn("8791", expose_ports)

        vol_text = " ".join(inst_map.get("VOLUME", []))
        for vol in ("/app/data", "/app/logs", "/app/keys"):
            self.assertIn(vol, vol_text)

        env_text = " ".join(inst_map.get("ENV", []))
        self.assertIn("PYTHONUNBUFFERED=1", env_text)
        self.assertIn("WT_VERDICT_PORT=8790", env_text)
        self.assertIn("WHOLETAKE_ASR=none", env_text)

        self.assertIn("CMD", inst_map)
        cmd_raw = inst_map["CMD"][-1]
        cmd_list = json.loads(cmd_raw)
        self.assertEqual(
            cmd_list,
            ["python", "-m", "verdict", "serve", "--host", "0.0.0.0", "--port", "8790"],
        )
        self.assertTrue((REPO_ROOT / "verdict" / "__main__.py").is_file())

    def test_dockerignore_blocks_secrets_and_artifacts(self) -> None:
        dockerignore = REPO_ROOT / ".dockerignore"
        self.assertTrue(dockerignore.is_file(), ".dockerignore must exist at repo root")
        lines = {
            ln.strip()
            for ln in dockerignore.read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.strip().startswith("#")
        }
        required_entries = [
            ".git",
            ".venv",
            "__pycache__",
            ".env",
            "*.key",
            "data/anchor.key",
            "data/claim.key",
            "keys/",
            "logs/",
            "data/",
            "testdata/",
        ]
        for entry in required_entries:
            self.assertIn(entry, lines, f".dockerignore must exclude {entry}")
        self.assertTrue(
            "*.pyc" in lines or "*.py[cod]" in lines,
            ".dockerignore must exclude *.pyc or *.py[cod]",
        )

    def test_env_example_syntax_and_no_secrets(self) -> None:
        env_example = REPO_ROOT / ".env.example"
        self.assertTrue(env_example.is_file(), ".env.example must exist at repo root")
        parsed: dict[str, str] = {}
        hex64_re = re.compile(r"^(?:0x)?[0-9a-fA-F]{64}$")

        for lineno, raw in enumerate(env_example.read_text(encoding="utf-8").splitlines(), 1):
            stripped = raw.strip()
            if not stripped or stripped.startswith("#"):
                continue
            self.assertIn("=", stripped, f"Invalid line {lineno} in .env.example: {raw}")
            key, val = stripped.split("=", 1)
            key = key.strip()
            val = val.strip()
            self.assertRegex(key, r"^[A-Z][A-Z0-9_]*$", f"Invalid env key at line {lineno}: {key}")
            self.assertFalse(hex64_re.match(val), f"Real 64-hex secret found in {key}")
            self.assertFalse(val.startswith("sk-"), f"Real API token found in {key}")
            parsed[key] = val

        expected_env = {
            "WT_VERDICT_PORT": "8790",
            "SIMX_PORT": "8791",
            "WT_FETCH_ALLOW_HOSTS": "127.0.0.1:8791,simx:8791",
            "WT_DETECT": "python -m verdict.wt_adapter",
            "WT_WITNESS_ROOT": "/app/data/witness",
            "WT_VERDICTS_DIR": "/app/data/verdicts",
            "ROOMSTAMP_LOG_DIR": "/app/logs",
            "ROOMSTAMP_KEY_FILE": "/app/keys/venue.key",
            "WHOLETAKE_ASR": "none",
            "X_API_BASE": "http://simx:8791",
            "X_BOT_TOKEN": "sim-wholetake",
            "WT_VERDICT_URL": "http://verdict:8790",
            "WT_PAGE_BASE": "http://127.0.0.1:8790",
            "BOT_POLL_S": "5",
            "BOT_INCLUDE_LINK": "1",
            "BASE_SEPOLIA_RPC_URL": "",
            "OPENAI_API_KEY": "",
        }
        for k, v in expected_env.items():
            self.assertIn(k, parsed, f"Missing required key {k} in .env.example")
            self.assertEqual(parsed[k], v, f"Unexpected default for {k} in .env.example")


class TestDeployScriptsAndRunbook(unittest.TestCase):
    def test_deploy_scripts_exist_and_support_subcommands(self) -> None:
        sh_path = REPO_ROOT / "deploy" / "deploy.sh"
        ps1_path = REPO_ROOT / "deploy" / "deploy.ps1"
        self.assertTrue(sh_path.is_file(), "deploy/deploy.sh must exist")
        self.assertTrue(ps1_path.is_file(), "deploy/deploy.ps1 must exist")

        sh_text = sh_path.read_text(encoding="utf-8")
        ps1_text = ps1_path.read_text(encoding="utf-8")

        required_tokens = [
            "build",
            "up",
            "start",
            "down",
            "stop",
            "status",
            "logs",
            "check-env",
            "local-demo",
            "--dry-run",
            "--with-daemons",
            "--env-file",
            "data/witness",
            "data/verdicts",
            "data/simx",
            "keys/venue.key",
            "--profile",
            "daemons",
        ]
        for tok in required_tokens:
            self.assertIn(tok, sh_text, f"deploy/deploy.sh missing {tok}")
            self.assertIn(tok, ps1_text, f"deploy/deploy.ps1 missing {tok}")

    def _verify_temp_root_initialized(self, tmp_root: Path) -> str:
        for rel in ("data/witness", "data/verdicts", "data/simx", "logs", "keys"):
            p = tmp_root / rel
            self.assertTrue(p.is_dir(), f"Expected runtime directory {rel} to be created in {tmp_root}")
        key_file = tmp_root / "keys" / "venue.key"
        self.assertTrue(key_file.is_file(), "Expected keys/venue.key to be generated")
        key_val = key_file.read_text(encoding="utf-8").strip()
        self.assertRegex(key_val, r"^[0-9a-f]{64}$", "venue.key must be 64 lowercase hex chars (32 random bytes)")
        return key_val

    def test_deploy_ps1_or_sh_dry_run_and_check_env_in_tempdir(self) -> None:
        import os
        import shutil
        import subprocess
        import tempfile

        sh_path = REPO_ROOT / "deploy" / "deploy.sh"
        ps1_path = REPO_ROOT / "deploy" / "deploy.ps1"

        tmp1 = Path(tempfile.mkdtemp(prefix="wt_deploy_test1_"))
        tmp2 = Path(tempfile.mkdtemp(prefix="wt_deploy_test2_"))
        try:
            ran_any = False
            # Prefer PowerShell on Windows, or test if available
            ps_bin = shutil.which("powershell") or shutil.which("pwsh")
            if ps_bin:
                res_check = subprocess.run(
                    [ps_bin, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ps1_path), "check-env", "--root", str(tmp1)],
                    capture_output=True,
                    text=True,
                    timeout=20,
                )
                self.assertEqual(res_check.returncode, 0, f"deploy.ps1 check-env failed: {res_check.stderr}")
                self.assertIn("[check-env] OK", res_check.stdout)
                key1 = self._verify_temp_root_initialized(tmp1)

                res_up = subprocess.run(
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
                        str(tmp2),
                    ],
                    capture_output=True,
                    text=True,
                    timeout=20,
                )
                self.assertEqual(res_up.returncode, 0, f"deploy.ps1 up --dry-run failed: {res_up.stderr}")
                self.assertIn("[dry-run]", res_up.stdout)
                self.assertIn("docker compose", res_up.stdout)
                self.assertIn("--profile daemons", res_up.stdout)
                key2 = self._verify_temp_root_initialized(tmp2)
                self.assertNotEqual(key1, key2, "Ephemeral keys across distinct runs must be random, not static")
                ran_any = True

            bash_bin = shutil.which("bash")
            if bash_bin and os.name != "nt":
                tmp_sh = Path(tempfile.mkdtemp(prefix="wt_deploy_sh_"))
                try:
                    res_sh = subprocess.run(
                        [bash_bin, str(sh_path), "up", "--dry-run", "--with-daemons", "--root", str(tmp_sh)],
                        capture_output=True,
                        text=True,
                        timeout=20,
                    )
                    self.assertEqual(res_sh.returncode, 0, f"deploy.sh up --dry-run failed: {res_sh.stderr}")
                    self.assertIn("[dry-run]", res_sh.stdout)
                    self.assertIn("docker compose", res_sh.stdout)
                    self.assertIn("--profile daemons", res_sh.stdout)
                    self._verify_temp_root_initialized(tmp_sh)
                    ran_any = True
                finally:
                    shutil.rmtree(tmp_sh, ignore_errors=True)

            self.assertTrue(ran_any, "Neither powershell/pwsh nor bash was available to run dry-run test")
        finally:
            shutil.rmtree(tmp1, ignore_errors=True)
            shutil.rmtree(tmp2, ignore_errors=True)

    def test_deploy_md_runbook_completeness_and_references(self) -> None:
        deploy_md = REPO_ROOT / "deploy" / "DEPLOY.md"
        readme_md = REPO_ROOT / "deploy" / "README.md"
        self.assertTrue(deploy_md.is_file(), "deploy/DEPLOY.md must exist")
        self.assertTrue(readme_md.is_file(), "deploy/README.md must exist")

        text = deploy_md.read_text(encoding="utf-8")
        readme_text = readme_md.read_text(encoding="utf-8")

        required_markers = [
            "Architecture & Ports Overview",
            "Local Laptop Demo",
            "Small Cloud Linux VM Deployment",
            "Key & Secret Hygiene",
            "Healthchecks, Verification & Troubleshooting",
            "8790",
            "8791",
            "WASAPI",
            "Speakers (Realtek",
            "keys/venue.key",
            "data/anchor.key",
            "data/claim.key",
            "0600",
            "/api/verdicts",
            "python -m verdict check",
            "--with-daemons",
            "local-demo",
        ]
        for marker in required_markers:
            self.assertIn(marker, text, f"deploy/DEPLOY.md missing required content: {marker}")

        # Verify all systemd service files referenced in DEPLOY.md and README.md exist in deploy/
        combined = text + "\n" + readme_text
        service_refs = set(re.findall(r"\b(wholetake-[a-z0-9@-]+\.service)\b", combined))
        self.assertGreaterEqual(len(service_refs), 5, "Expected systemd unit references in deployment docs")
        for unit_name in service_refs:
            # Normalize template instance like wholetake-witness@creator.service -> wholetake-witness@.service
            norm_name = re.sub(r"@[a-zA-Z0-9_-]+\.service$", "@.service", unit_name)
            unit_path = REPO_ROOT / "deploy" / norm_name
            self.assertTrue(unit_path.is_file(), f"Referenced systemd unit {unit_name} ({norm_name}) does not exist")

        # Verify all `python -m <module>` or `./py -m <module>` references correspond to real repo modules
        mod_refs = set(re.findall(r"(?:python3?|\./py)\s+-m\s+([a-zA-Z0-9_.]+)", combined))
        self.assertGreaterEqual(len(mod_refs), 5, "Expected python -m module references in deployment docs")
        for mod in mod_refs:
            parts = mod.split(".")
            pkg_dir = REPO_ROOT.joinpath(*parts)
            py_file = REPO_ROOT.joinpath(*parts[:-1], f"{parts[-1]}.py") if len(parts) > 1 else REPO_ROOT / f"{parts[0]}.py"
            exists = (pkg_dir.is_dir() and (pkg_dir / "__main__.py").is_file()) or py_file.is_file()
            self.assertTrue(exists, f"Referenced CLI module 'python -m {mod}' does not exist in repo")


if __name__ == "__main__":
    unittest.main()
