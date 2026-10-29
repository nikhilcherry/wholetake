import sys
from pathlib import Path

root = Path.cwd()
test_file = root / "extension" / "test_extension.py"
if not test_file.is_file():
    print("FAIL extension_task1: extension/test_extension.py does not exist")
    sys.exit(1)

from extension.test_extension import run_all
rc = run_all()
if rc != 0:
    print("FAIL extension_task1: test suite returned non-zero")
    sys.exit(1)
print("PASS extension_task1")
