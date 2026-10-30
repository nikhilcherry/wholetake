"""Reproducer test for F301: Background Service Worker, Context Menu, Popup UI & Page Media Detector."""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def main() -> int:
    ext_dir = REPO_ROOT / "extension"
    required_files = ["background.js", "popup.html", "popup.css", "popup.js"]
    for fname in required_files:
        fpath = ext_dir / fname
        if not fpath.is_file() or fpath.stat().st_size == 0:
            print(f"FAIL test_F301: missing or empty extension/{fname}")
            return 1

    from extension import test_extension

    if not hasattr(test_extension, "test_background_and_popup_wiring"):
        print("FAIL test_F301: extension.test_extension missing test_background_and_popup_wiring")
        return 1

    try:
        test_extension.test_background_and_popup_wiring()
        test_extension.test_asset_and_html_references()
        test_extension.test_no_secrets_in_extension()
    except Exception as exc:
        print(f"FAIL test_F301: {exc}")
        return 1

    print("PASS test_F301")
    return 0


if __name__ == "__main__":
    sys.exit(main())
