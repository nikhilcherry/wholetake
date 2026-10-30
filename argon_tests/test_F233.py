"""Standalone reproducer test for F233: Docker Compose Stack & Static Validation."""
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def main() -> int:
    compose_file = REPO_ROOT / "docker-compose.yml"
    if not compose_file.is_file():
        print("FAIL test_F233: docker-compose.yml does not exist at repository root")
        return 1

    try:
        from deploy import test_deploy
    except Exception as exc:
        print(f"FAIL test_F233: could not import deploy.test_deploy: {exc}")
        return 1

    if not hasattr(test_deploy, "TestDockerComposeStack") or not hasattr(test_deploy, "_parse_docker_compose"):
        print("FAIL test_F233: TestDockerComposeStack or _parse_docker_compose missing in deploy/test_deploy.py")
        return 1

    suite = unittest.defaultTestLoader.loadTestsFromModule(test_deploy)
    runner = unittest.TextTestRunner(verbosity=0)
    result = runner.run(suite)
    if not result.wasSuccessful():
        reasons = [str(err[0]) for err in (result.failures + result.errors)]
        print(f"FAIL test_F233: unit test failures: {reasons}")
        return 1

    print("PASS test_F233")
    return 0


if __name__ == "__main__":
    sys.exit(main())
