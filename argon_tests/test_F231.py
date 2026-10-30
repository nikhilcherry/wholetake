"""Reproducer test for F231: Unpacked Install README and Full End-to-End Verification."""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def main() -> int:
    try:
        readme_path = REPO_ROOT / "extension" / "README.md"
        assert readme_path.is_file(), "extension/README.md does not exist"

        import extension.test_extension as ext_tests

        for fn_name in (
            "test_readme_completeness",
            "test_asset_and_html_references",
            "test_cors_and_error_edge_cases",
            "test_no_secrets_in_extension",
        ):
            assert hasattr(ext_tests, fn_name), f"extension/test_extension.py missing {fn_name}"

        rc = ext_tests.run_all()
        assert rc == 0, f"extension.test_extension.run_all() returned non-zero: {rc}"

        print("PASS test_F231")
        return 0
    except Exception as exc:
        print(f"FAIL test_F231: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
