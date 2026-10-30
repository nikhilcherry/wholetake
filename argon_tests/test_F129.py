import sys
from roomstamp.wt_detect import merge_edits


def main() -> None:
    name = "test_F129_merge_edits_dedup_spanning_edit"
    # Case 1: overlapping foreign edits [10.0, 14.0] (L1) and [12.5, 14.0] (wt)
    layers = {
        "l1": {
            "status": "present",
            "content_match": True,
            "integrity_checked": True,
            "edits": [
                {
                    "kind": "foreign",
                    "clip_t": 10.0,
                    "clip_t_range": [10.0, 14.0],
                    "duration_s": 4.0,
                    "source": "l1",
                    "precision_s": 0.5,
                }
            ],
        },
        "wt": {
            "status": "present",
            "content_match": True,
            "integrity_checked": True,
            "edits": [
                {
                    "kind": "foreign",
                    "clip_t": 12.5,
                    "clip_t_range": [12.5, 14.0],
                    "duration_s": 1.5,
                    "source": "wt",
                    "precision_s": 0.2,
                }
            ],
        },
        "l2": {"status": "skipped", "edits": []},
    }
    edits, _ = merge_edits(layers)
    if len(edits) != 1 or edits[0]["source"] != "l1":
        print(f"FAIL {name}: overlapping foreign edits [10.0, 14.0] (L1) and [12.5, 14.0] (wt) were not deduplicated: got {len(edits)} edits {edits}")
        sys.exit(1)

    # Case 2: overlapping replaced edits [5.0, 9.5] (L1) and [7.8, 9.5] (wt) with restored_text
    layers2 = {
        "l1": {
            "status": "present",
            "content_match": True,
            "integrity_checked": True,
            "edits": [
                {
                    "kind": "replaced",
                    "clip_t": 5.0,
                    "clip_t_range": [5.0, 9.5],
                    "duration_s": 4.5,
                    "source_offset_s": 15.0,
                    "source": "l1",
                    "precision_s": 0.5,
                }
            ],
        },
        "wt": {
            "status": "present",
            "content_match": True,
            "integrity_checked": True,
            "edits": [
                {
                    "kind": "replaced",
                    "clip_t": 7.8,
                    "clip_t_range": [7.8, 9.5],
                    "duration_s": 1.7,
                    "source": "wt",
                    "precision_s": 0.05,
                    "restored_text": "original words",
                }
            ],
        },
        "l2": {"status": "skipped", "edits": []},
    }
    edits2, _ = merge_edits(layers2)
    if len(edits2) != 1 or edits2[0]["restored_text"] != "original words":
        print(f"FAIL {name}: overlapping replaced edits were not deduplicated or lost restored_text: {edits2}")
        sys.exit(1)

    # Case 3: non-overlapping foreign edits separated by > 2.0 s must remain distinct
    layers3 = {
        "l1": {
            "status": "present",
            "content_match": True,
            "integrity_checked": True,
            "edits": [
                {
                    "kind": "foreign",
                    "clip_t": 10.0,
                    "clip_t_range": [10.0, 14.0],
                    "duration_s": 4.0,
                    "source": "l1",
                    "precision_s": 0.5,
                }
            ],
        },
        "wt": {
            "status": "present",
            "content_match": True,
            "integrity_checked": True,
            "edits": [
                {
                    "kind": "foreign",
                    "clip_t": 16.5,
                    "clip_t_range": [16.5, 18.0],
                    "duration_s": 1.5,
                    "source": "wt",
                    "precision_s": 0.2,
                }
            ],
        },
        "l2": {"status": "skipped", "edits": []},
    }
    edits3, _ = merge_edits(layers3)
    if len(edits3) != 2:
        print(f"FAIL {name}: non-overlapping edits should stay separate, got {edits3}")
        sys.exit(1)

    print(f"PASS {name}")


if __name__ == "__main__":
    main()
