import os
import shutil
import sys
import tempfile

from roomstamp import blind


def _expect_cli_error(argv: list[str], label: str) -> tuple[bool, str]:
    try:
        blind.main(argv)
    except SystemExit as e:
        if e.code == 2:
            return True, ""
        return False, f"{label} exited with {e.code!r}, expected 2"
    except Exception as e:
        return False, f"{label} raised unhandled {type(e).__name__}: {e}"
    return False, f"{label} did not exit"


def test_02_cli_span_nonfinite_validation() -> tuple[bool, str]:
    tmp = tempfile.mkdtemp(prefix="f307_")
    try:
        bad_key = os.path.join(tmp, "bad.key")
        with open(bad_key, "w", encoding="utf-8") as f:
            f.write("not-a-valid-key")
        good_key = os.path.join(tmp, "good.key")
        with open(good_key, "w", encoding="utf-8") as f:
            f.write("aa" * 32)
        for kf in (bad_key, good_key):
            for sp in ("nan", "inf", "-inf", "-10"):
                ok, msg = _expect_cli_error(
                    ["dummy.wav", "--key-file", kf, "--around", "1700000000", "--span", sp],
                    f"--span {sp}",
                )
                if not ok:
                    return False, msg
        return True, ""
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_03_cli_malformed_timestamp_handling() -> tuple[bool, str]:
    tmp = tempfile.mkdtemp(prefix="f307_")
    try:
        bad_key = os.path.join(tmp, "bad.key")
        with open(bad_key, "w", encoding="utf-8") as f:
            f.write("not-a-valid-key")
        cases = [
            ["--around", "not-a-timestamp"],
            ["--around", "1700_000_000"],
            ["--from", "not-a-timestamp", "--to", "1700000000"],
            ["--from", "1700000000", "--to", "1700_000_000"],
        ]
        for args in cases:
            ok, msg = _expect_cli_error(
                ["dummy.wav", "--key-file", bad_key] + args,
                str(args),
            )
            if not ok:
                return False, msg
        return True, ""
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> None:
    failed = False
    for fn in (test_02_cli_span_nonfinite_validation, test_03_cli_malformed_timestamp_handling):
        ok, msg = fn()
        if ok:
            print(f"PASS {fn.__name__}")
        else:
            print(f"FAIL {fn.__name__}: {msg}")
            failed = True
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
