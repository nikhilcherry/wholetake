import sys
from roomstamp.wt_detect import _empty_doc, _stage, fuse


def _make_doc(l1_status: str):
    doc = _empty_doc([])
    doc["clip"] = {"duration_s": 20.0}
    doc["verdict"]["venue"] = {
        "name": "HH Goa demo stage",
        "key_id": "0" * 16,
        "session": "session_1",
        "mode": "pa",
    }
    doc["layers"]["l1"] = _stage(
        l1_status,
        "s2",
        recorded_at_unix=1793430600.0,
        content_match=True,
        integrity_checked=True,
        edits=[
            {
                "kind": "removed",
                "clip_t": 12.0,
                "clip_t_range": [12.0, 12.0],
                "removed_s": 0.30,
                "precision_s": 0.002,
                "source": "l1",
            }
        ],
    )
    doc["layers"]["wt"] = _stage(
        "present",
        "s4",
        recorded_at_unix=1793430600.0,
        content_match=True,
        integrity_checked=True,
        edits=[
            {
                "kind": "removed",
                "clip_t": 12.0,
                "clip_t_range": [12.0, 12.0],
                "removed_s": 0.30,
                "precision_s": 0.01,
                "source": "wt",
                "restored_text": "not",
            }
        ],
    )
    return fuse(doc)


def main():
    # Case 1: recorded_here == "likely" (L1 inconclusive)
    d_likely = _make_doc("inconclusive")
    v_likely = d_likely["verdict"]
    if v_likely["recorded_here"] != "likely":
        print(f"FAIL test_F287: expected recorded_here='likely', got {v_likely['recorded_here']!r}")
        sys.exit(1)
    if "0.30 s removed at 0:12." not in v_likely["headline"]:
        print(f"FAIL test_F287: missing edit summary in likely headline: {v_likely['headline']!r}")
        sys.exit(1)
    if 'Missing words: "not".' not in v_likely["headline"]:
        print(f"FAIL test_F287: missing restored_text in likely headline: {v_likely['headline']!r}")
        sys.exit(1)

    # Case 2: recorded_here == "not_verified" stream rip with content_match == True
    d_nv = _make_doc("absent")
    v_nv = d_nv["verdict"]
    if v_nv["recorded_here"] != "not_verified":
        print(f"FAIL test_F287: expected recorded_here='not_verified', got {v_nv['recorded_here']!r}")
        sys.exit(1)
    if "0.30 s removed at 0:12." not in v_nv["headline"]:
        print(f"FAIL test_F287: missing edit summary in not_verified headline: {v_nv['headline']!r}")
        sys.exit(1)
    if 'Missing words: "not".' not in v_nv["headline"]:
        print(f"FAIL test_F287: missing restored_text in not_verified headline: {v_nv['headline']!r}")
        sys.exit(1)

    print("PASS test_F287")
    sys.exit(0)


if __name__ == "__main__":
    main()
