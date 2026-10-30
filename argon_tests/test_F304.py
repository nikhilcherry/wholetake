import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def main() -> None:
    try:
        from deploy import test_deploy

        if not hasattr(test_deploy, "TestDockerComposeStatic"):
            print("FAIL test_F304: deploy.test_deploy missing TestDockerComposeStatic")
            sys.exit(1)

        compose_path = REPO_ROOT / "docker-compose.yml"
        if not compose_path.is_file():
            print("FAIL test_F304: docker-compose.yml does not exist at repo root")
            sys.exit(1)

        parsed = test_deploy._parse_docker_compose(compose_path.read_text(encoding="utf-8"))
        services = parsed.get("services", {})
        required_services = {
            "verdict",
            "simx",
            "wt-detect",
            "roomstamp",
            "bot",
            "chain",
            "transcribe",
            "anchor",
        }
        missing = required_services - set(services.keys())
        if missing:
            print(f"FAIL test_F304: docker-compose.yml missing services: {sorted(missing)}")
            sys.exit(1)

        verdict_hc = " ".join(services["verdict"].get("healthcheck", {}).get("test", []))
        if "http://127.0.0.1:8790/api/status" not in verdict_hc:
            print(f"FAIL test_F304: verdict healthcheck does not probe /api/status: {verdict_hc}")
            sys.exit(1)

        suite = unittest.defaultTestLoader.loadTestsFromModule(test_deploy)
        runner = unittest.TextTestRunner(verbosity=0)
        result = runner.run(suite)
        if not result.wasSuccessful():
            reasons = [f"{t}: {err.splitlines()[-1]}" for t, err in result.failures + result.errors]
            print(f"FAIL test_F304: unittest failures: {'; '.join(reasons)}")
            sys.exit(1)

        print("PASS test_F304")
    except Exception as exc:
        print(f"FAIL test_F304: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
