import sys
from roomstamp.wt_detect import _edit_summary, _empty_doc, _stage, fuse


def main() -> None:
    # 1. Direct _edit_summary with L2 cut and clip_t_range=[None, None]
    e1 = {
        "kind": "removed",
        "clip_t": 12.0,
        "clip_t_range": [None, None],
        "removed_s": 1.5,
        "source": "l2",
    }
    s1 = _edit_summary([e1])
    if s1 != "About 1.5 s removed between 0:12 and 0:12":
        print(f"FAIL test_F346: unexpected summary s1={s1!r}")
        sys.exit(1)

    # 2. L2 cut with clip_t_range=[10.0, None] and clip_t=15.0
    e2 = {
        "kind": "removed",
        "clip_t": 15.0,
        "clip_t_range": [10.0, None],
        "removed_s": 2.0,
        "source": "l2",
    }
    s2 = _edit_summary([e2])
    if s2 != "About 2.0 s removed between 0:10 and 0:15":
        print(f"FAIL test_F346: unexpected summary s2={s2!r}")
        sys.exit(1)

    # 3. L2 cut with clip_t=None and clip_t_range=None
    e3 = {
        "kind": "removed",
        "clip_t": None,
        "clip_t_range": None,
        "removed_s": None,
        "source": "l2",
    }
    s3 = _edit_summary([e3])
    if s3 != "About 0.0 s removed between 0:00 and 0:00":
        print(f"FAIL test_F346: unexpected summary s3={s3!r}")
        sys.exit(1)

    # 4. End-to-end fuse() with an L2 edit having clip_t_range=[None, None]
    doc = _empty_doc(["wt-detect", "clip.wav"])
    doc["clip"] = {"duration_s": 25.0}
    doc["layers"]["l2"] = _stage(
        "present",
        "s3",
        "present",
        recorded_at_unix=1793430600.0,
        edits=[
            {
                "kind": "removed",
                "clip_t": 12.0,
                "clip_t_range": [None, None],
                "removed_s": 1.5,
                "duration_s": None,
                "venue_t_from": None,
                "venue_t_to": None,
                "precision_s": 0.1,
                "source": "l2",
            }
        ],
    )
    fuse(doc)
    head = doc["verdict"]["headline"]
    if "About 1.5 s removed between 0:12 and 0:12." not in head:
        print(f"FAIL test_F346: unexpected headline={head!r}")
        sys.exit(1)

    print("PASS test_F346")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"FAIL test_F346: {type(exc).__name__}: {exc}")
        sys.exit(1)
