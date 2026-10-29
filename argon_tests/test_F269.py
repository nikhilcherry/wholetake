import sys
from roomstamp import wt_detect


def main() -> int:
    failures = []

    # 1. Single removed edit with removed_s=None (l1 source)
    try:
        s = wt_detect._edit_summary([
            {"kind": "removed", "source": "l1", "clip_t": 12.0, "clip_t_range": [12.0, 12.0], "removed_s": None}
        ])
        assert "0.00 s removed at 0:12" in s, f"unexpected summary: {s}"
        print("PASS 01_single_removed_l1_null_removed_s")
    except Exception as e:
        print(f"FAIL 01_single_removed_l1_null_removed_s: {type(e).__name__}: {e}")
        failures.append("01")

    # 2. Single removed edit with removed_s=None (l2 source)
    try:
        s = wt_detect._edit_summary([
            {"kind": "removed", "source": "l2", "clip_t": 12.0, "clip_t_range": [10.0, 14.0], "removed_s": None}
        ])
        assert "About 0.0 s removed between 0:10 and 0:14" in s, f"unexpected summary: {s}"
        print("PASS 02_single_removed_l2_null_removed_s")
    except Exception as e:
        print(f"FAIL 02_single_removed_l2_null_removed_s: {type(e).__name__}: {e}")
        failures.append("02")

    # 3. Multiple removed edits with removed_s=None
    try:
        s = wt_detect._edit_summary([
            {"kind": "removed", "source": "l1", "clip_t": 5.0, "clip_t_range": [5.0, 5.0], "removed_s": None},
            {"kind": "removed", "source": "l1", "clip_t": 15.0, "clip_t_range": [15.0, 15.0], "removed_s": 0.5},
        ])
        assert "2 cuts (0.50 s removed in total, first at 0:05)" in s, f"unexpected summary: {s}"
        print("PASS 03_multiple_removed_null_removed_s")
    except Exception as e:
        print(f"FAIL 03_multiple_removed_null_removed_s: {type(e).__name__}: {e}")
        failures.append("03")

    # 4. Repeated edit with removed_s=None
    try:
        s = wt_detect._edit_summary([
            {"kind": "repeated", "source": "l1", "clip_t": 8.0, "clip_t_range": [8.0, 8.0], "removed_s": None}
        ])
        assert "0.00 s repeated at 0:08" in s, f"unexpected summary: {s}"
        print("PASS 04_repeated_null_removed_s")
    except Exception as e:
        print(f"FAIL 04_repeated_null_removed_s: {type(e).__name__}: {e}")
        failures.append("04")

    # 5. End-to-end fuse() with removed_s=None
    try:
        doc = wt_detect._empty_doc(["test.wav"])
        doc["clip"] = {"duration_s": 20.0}
        doc["layers"]["l1"].update({
            "status": "present",
            "content_match": True,
            "integrity_checked": True,
            "recorded_at_unix": 1793430600.0,
            "edits": [
                {"kind": "removed", "source": "l1", "clip_t": 12.0, "clip_t_range": [12.0, 12.0], "removed_s": None}
            ],
        })
        wt_detect.fuse(doc)
        assert doc["verdict"]["integrity"] == "edited"
        assert "0.00 s removed at 0:12" in doc["verdict"]["headline"]
        print("PASS 05_fuse_with_null_removed_s")
    except Exception as e:
        print(f"FAIL 05_fuse_with_null_removed_s: {type(e).__name__}: {e}")
        failures.append("05")

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
