import sys
import unittest
from pathlib import Path

candidates = [Path.cwd(), *[Path(p) for p in sys.path if p], Path(__file__).resolve().parent]
root = Path.cwd()
for cand in candidates:
    if (cand / "verdict").is_dir() and (cand / "deploy").is_dir():
        root = cand.resolve()
        break

if str(root) not in sys.path:
    sys.path.insert(0, str(root))

if not (root / "Dockerfile").is_file():
    print("FAIL S7_skeleton: missing Dockerfile")
    sys.exit(1)

if not (root / "deploy" / "test_deploy.py").is_file():
    print("FAIL S7_skeleton: missing deploy/test_deploy.py")
    sys.exit(1)

from deploy import test_deploy

suite = unittest.defaultTestLoader.loadTestsFromModule(test_deploy)
runner = unittest.TextTestRunner(stream=sys.stderr, verbosity=1)
result = runner.run(suite)
if not result.wasSuccessful():
    print("FAIL S7_skeleton: test_deploy suite failed")
    sys.exit(1)

print("PASS S7_skeleton")
