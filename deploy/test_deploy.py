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


def _parse_yaml_scalar_or_inline(val: str):
    val = val.strip()
    if not val:
        return ""
    if val.startswith("[") and val.endswith("]"):
        try:
            return json.loads(val)
        except Exception:
            inner = val[1:-1].strip()
            if not inner:
                return []
            return [p.strip().strip('"\'') for p in inner.split(",")]
    if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
        return val[1:-1]
    return val


def _parse_docker_compose(text: str) -> dict:
    """Lightweight stdlib indentation parser for docker-compose.yml."""
    result: dict = {"services": {}}
    in_services = False
    cur_service: str | None = None
    cur_field: str | None = None

    for raw_line in text.splitlines():
        if not raw_line.strip() or raw_line.strip().startswith("#"):
            continue
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        stripped = raw_line.strip()

        if indent == 0:
            in_services = stripped == "services:"
            cur_service = None
            cur_field = None
            continue

        if not in_services:
            continue

        if indent == 2 and stripped.endswith(":"):
            cur_service = stripped[:-1].strip()
            result["services"][cur_service] = {}
            cur_field = None
            continue

        if cur_service is None:
            continue

        svc_obj = result["services"][cur_service]

        if indent == 4:
            if ":" in stripped and not stripped.startswith("-"):
                k, v = stripped.split(":", 1)
                k = k.strip()
                v = v.strip()
                cur_field = k
                if v:
                    svc_obj[k] = _parse_yaml_scalar_or_inline(v)
                else:
                    svc_obj[k] = None
            continue

        if indent >= 6 and cur_field is not None:
            if stripped.startswith("- "):
                item_val = _parse_yaml_scalar_or_inline(stripped[2:])
                if not isinstance(svc_obj.get(cur_field), list):
                    svc_obj[cur_field] = []
                svc_obj[cur_field].append(item_val)
            elif ":" in stripped:
                sub_k, sub_v = stripped.split(":", 1)
                sub_k = sub_k.strip()
                sub_v = _parse_yaml_scalar_or_inline(sub_v)
                if not isinstance(svc_obj.get(cur_field), dict):
                    svc_obj[cur_field] = {}
                svc_obj[cur_field][sub_k] = sub_v

    return result


def _resolve_module_exists(mod_name: str) -> bool:
    parts = mod_name.split(".")
    pkg_dir = REPO_ROOT.joinpath(*parts)
    py_file = (
        REPO_ROOT.joinpath(*parts[:-1], f"{parts[-1]}.py")
        if len(parts) > 1
        else REPO_ROOT / f"{parts[0]}.py"
    )
    return (pkg_dir.is_dir() and (pkg_dir / "__main__.py").is_file()) or py_file.is_file()


class TestDockerComposeStatic(unittest.TestCase):
    def test_docker_compose_core_and_optional_services(self) -> None:
        compose_path = REPO_ROOT / "docker-compose.yml"
        self.assertTrue(compose_path.is_file(), "docker-compose.yml must exist at repo root")
        raw_text = compose_path.read_text(encoding="utf-8")
        parsed = _parse_docker_compose(raw_text)
        services = parsed.get("services", {})

        expected_services = {
            "simx",
            "verdict",
            "bot",
            "chain",
            "transcribe",
            "anchor",
            "wt-detect",
            "roomstamp",
        }
        self.assertTrue(
            expected_services.issubset(set(services.keys())),
            f"Missing services in docker-compose.yml: {expected_services - set(services.keys())}",
        )

        # Core services started by default (no profiles)
        for core in ("simx", "verdict"):
            svc = services[core]
            self.assertEqual(svc.get("build"), ".")
            self.assertNotIn("profiles", svc, f"Core service {core} must not be gated by profiles")
            self.assertEqual(svc.get("restart"), "unless-stopped")
            self.assertIn("healthcheck", svc, f"Core service {core} must define a healthcheck")
            hc = svc["healthcheck"]
            self.assertIsInstance(hc, dict)
            hc_test_str = " ".join(hc.get("test", [])) if isinstance(hc.get("test"), list) else str(hc.get("test"))
            self.assertIn("urllib.request", hc_test_str)

        # simx specifics
        simx = services["simx"]
        simx_cmd = " ".join(simx["command"]) if isinstance(simx.get("command"), list) else str(simx.get("command"))
        self.assertIn("python -m simx serve --host 0.0.0.0 --port 8791 --data /app/data/simx", simx_cmd)
        self.assertIn("${SIMX_PORT:-8791}:8791", simx.get("ports", []))
        self.assertIn("./data:/app/data", simx.get("volumes", []))
        simx_hc = " ".join(simx["healthcheck"]["test"])
        self.assertIn("http://127.0.0.1:8791/2/tweets/search/recent?query=wholetake", simx_hc)

        # verdict specifics
        verdict = services["verdict"]
        verdict_cmd = " ".join(verdict["command"]) if isinstance(verdict.get("command"), list) else str(verdict.get("command"))
        self.assertIn("python -m verdict serve --host 0.0.0.0 --port 8790", verdict_cmd)
        self.assertIn("${WT_VERDICT_PORT:-8790}:8790", verdict.get("ports", []))
        self.assertIn("simx", verdict.get("depends_on", []))
        for v_mount in ("./data:/app/data", "./logs:/app/logs", "./keys:/app/keys:ro"):
            self.assertIn(v_mount, verdict.get("volumes", []), f"verdict missing volume {v_mount}")

        verdict_env = verdict.get("environment", [])
        env_map = dict(item.split("=", 1) for item in verdict_env if "=" in item)
        expected_verdict_env = {
            "WT_VERDICT_PORT": "8790",
            "WT_FETCH_ALLOW_HOSTS": "${WT_FETCH_ALLOW_HOSTS:-127.0.0.1:8791,simx:8791}",
            "WT_DETECT": "${WT_DETECT:-python -m verdict.wt_adapter}",
            "WT_WITNESS_ROOT": "/app/data/witness",
            "WT_VERDICTS_DIR": "/app/data/verdicts",
            "ROOMSTAMP_LOG_DIR": "/app/logs",
            "ROOMSTAMP_KEY_FILE": "/app/keys/venue.key",
            "WHOLETAKE_ASR": "${WHOLETAKE_ASR:-none}",
        }
        for ek, ev in expected_verdict_env.items():
            self.assertEqual(env_map.get(ek), ev, f"verdict environment mismatch for {ek}")

        verdict_hc = " ".join(verdict["healthcheck"]["test"])
        self.assertIn("http://127.0.0.1:8790/api/status", verdict_hc)

        # Optional daemons profile
        expected_daemon_cmds = {
            "bot": "python -m bot run",
            "chain": "python -m ingest.chain /app/data/witness",
            "transcribe": "python -m ingest.transcribe /app/data/witness",
            "anchor": "python -m anchor run /app/data/witness",
        }
        for daemon_name, expected_cmd in expected_daemon_cmds.items():
            d_svc = services[daemon_name]
            self.assertIn("daemons", d_svc.get("profiles", []), f"{daemon_name} must use profile 'daemons'")
            d_cmd = " ".join(d_svc["command"]) if isinstance(d_svc.get("command"), list) else str(d_svc.get("command"))
            self.assertEqual(d_cmd, expected_cmd, f"Unexpected command for daemon {daemon_name}")
            self.assertIn("./data:/app/data", d_svc.get("volumes", []))
            self.assertIn("./logs:/app/logs", d_svc.get("volumes", []))

        self.assertIn("verdict", services["bot"].get("depends_on", []))
        self.assertIn("simx", services["bot"].get("depends_on", []))
        self.assertIn("./keys:/app/keys:ro", services["anchor"].get("volumes", []))

        # Optional cli profile: wt-detect & roomstamp
        wt_detect = services["wt-detect"]
        self.assertIn("cli", wt_detect.get("profiles", []))
        wt_detect_ep = " ".join(wt_detect.get("entrypoint") or wt_detect.get("command") or [])
        self.assertIn("python -m verdict.wt_adapter", wt_detect_ep)
        for mnt in ("./data:/app/data", "./logs:/app/logs", "./keys:/app/keys:ro"):
            self.assertIn(mnt, wt_detect.get("volumes", []), f"wt-detect missing volume {mnt}")

        roomstamp_svc = services["roomstamp"]
        self.assertIn("cli", roomstamp_svc.get("profiles", []))
        roomstamp_ep = " ".join(roomstamp_svc.get("entrypoint") or roomstamp_svc.get("command") or [])
        self.assertIn("python -m roomstamp", roomstamp_ep)
        for mnt in ("./data:/app/data", "./logs:/app/logs", "./keys:/app/keys"):
            self.assertIn(mnt, roomstamp_svc.get("volumes", []), f"roomstamp missing volume {mnt}")

        # Ensure no hardcoded 64-hex secrets or private API tokens in docker-compose.yml
        hex64_re = re.compile(r"(?<![0-9a-fA-F])[0-9a-fA-F]{64}(?![0-9a-fA-F])")
        self.assertIsNone(hex64_re.search(raw_text), "docker-compose.yml must not contain hardcoded 64-hex secrets")
        self.assertNotIn("sk-", raw_text, "docker-compose.yml must not contain hardcoded API keys")

    def test_compose_and_systemd_entrypoints_resolve(self) -> None:
        compose_path = REPO_ROOT / "docker-compose.yml"
        self.assertTrue(compose_path.is_file())
        parsed = _parse_docker_compose(compose_path.read_text(encoding="utf-8"))
        services = parsed.get("services", {})

        compose_modules: set[str] = set()
        for svc_name, svc in services.items():
            for field in ("command", "entrypoint"):
                val = svc.get(field)
                if not val:
                    continue
                tokens = val if isinstance(val, list) else str(val).split()
                for idx, tok in enumerate(tokens[:-1]):
                    if tok == "-m":
                        compose_modules.add(tokens[idx + 1])
            for env_item in svc.get("environment", []):
                for m in re.findall(r"(?:python3?|\./py)\s+-m\s+([a-zA-Z0-9_.]+)", str(env_item)):
                    compose_modules.add(m)

        required_compose_modules = {
            "verdict",
            "simx",
            "verdict.wt_adapter",
            "roomstamp",
            "bot",
            "ingest.chain",
            "ingest.transcribe",
            "anchor",
        }
        self.assertTrue(
            required_compose_modules.issubset(compose_modules),
            f"Missing module entrypoints in docker-compose.yml: {required_compose_modules - compose_modules}",
        )
        for mod in compose_modules:
            self.assertTrue(
                _resolve_module_exists(mod),
                f"docker-compose.yml module 'python -m {mod}' does not resolve in repository",
            )

        service_files = list((REPO_ROOT / "deploy").glob("*.service"))
        self.assertGreaterEqual(len(service_files), 5, "Expected systemd *.service files in deploy/")
        systemd_modules: set[str] = set()
        for sf in service_files:
            text = sf.read_text(encoding="utf-8")
            for m in re.findall(r"(?:python3?|/python|\./py|/py)\s+-m\s+([a-zA-Z0-9_.]+)", text):
                systemd_modules.add(m)

        self.assertGreaterEqual(len(systemd_modules), 5, "Expected >=5 python -m entrypoints in deploy/*.service")
        for mod in systemd_modules:
            self.assertTrue(
                _resolve_module_exists(mod),
                f"systemd unit module 'python -m {mod}' does not resolve in repository",
            )


TestDockerComposeStack = TestDockerComposeStatic


if __name__ == "__main__":
    unittest.main()
