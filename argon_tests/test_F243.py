import sys
from roomstamp.wt_detect import _empty_doc, fuse, merge_edits


def test_merge_edits_conflates_different_edit_kinds_within_2s():
    name = "merge_edits_conflates_different_edit_kinds_within_2s"
    layers = {
        "l1": {
            "status": "present",
            "content_match": True,
            "integrity_checked": True,
            "recorded_at_unix": 1793430600.0,
            "edits": [
                {
                    "kind": "removed",
                    "clip_t": 11.5,
                    "clip_t_range": [11.5, 11.5],
                    "removed_s": 0.4,
                    "precision_s": 0.002,
                    "source": "l1",
                }
            ],
        },
        "wt": {
            "status": "present",
            "content_match": True,
            "integrity_checked": True,
            "recorded_at_unix": 1793430600.0,
            "edits": [
                {
                    "kind": "foreign",
                    "clip_t": 10.0,
                    "clip_t_range": [10.0, 10.5],
                    "duration_s": 0.5,
                    "removed_s": None,
                    "precision_s": 0.2,
                    "source": "wt",
                },
                {
                    "kind": "removed",
                    "clip_t": 11.52,
                    "clip_t_range": [11.52, 11.52],
                    "removed_s": 0.4,
                    "restored_text": "not",
                    "precision_s": 0.01,
                    "source": "wt",
                },
            ],
        },
        "l2": {"status": "skipped", "edits": []},
    }
    merged, warns = merge_edits(layers)
    kinds = [e["kind"] for e in merged]
    if "foreign" not in kinds:
        print(
            f"FAIL {name}: merge_edits dropped WT's 'foreign' edit at 10.0s because an unrelated L1 'removed' cut existed 1.5s away at 11.5s! Got merged edits: {merged}"
        )
        sys.exit(1)
    if len(merged) != 2:
        print(f"FAIL {name}: expected 2 merged edits (1 foreign + 1 deduplicated removed), got {len(merged)}: {merged}")
        sys.exit(1)
    rem_edit = next(e for e in merged if e["kind"] == "removed")
    if rem_edit.get("restored_text") != "not":
        print(f"FAIL {name}: expected compatible WT 'removed' edit to copy restored_text='not' onto L1 cut, got {rem_edit}")
        sys.exit(1)

    doc = _empty_doc(["wt-detect", "clip.wav"])
    doc["clip"] = {
        "path": "clip.wav",
        "sha256": "0" * 64,
        "bytes": 1000,
        "duration_s": 20.0,
        "orig_rate": 48000,
        "orig_channels": 1,
        "codec": "wav",
        "decoder": "soundfile",
    }
    doc["layers"]["l1"].update(layers["l1"])
    doc["layers"]["wt"].update(layers["wt"])
    fuse(doc)
    hl = doc["verdict"]["headline"]
    if "0.40 s removed" not in hl or "0.5 s not from this venue" not in hl:
        print(f"FAIL {name}: headline missing either removed cut or foreign span: {hl!r}")
        sys.exit(1)

    # Case 2: 'replaced' edit near 'removed' cut should also be preserved
    layers2 = {
        "l1": layers["l1"],
        "wt": {
            "status": "present",
            "content_match": True,
            "integrity_checked": True,
            "edits": [
                {
                    "kind": "replaced",
                    "clip_t": 10.2,
                    "clip_t_range": [10.2, 10.8],
                    "duration_s": 0.6,
                    "source_offset_s": 15.0,
                    "removed_s": None,
                    "precision_s": 0.05,
                    "source": "wt",
                    "restored_text": "swapped words",
                }
            ],
        },
        "l2": {"status": "skipped", "edits": []},
    }
    merged2, _ = merge_edits(layers2)
    if [e["kind"] for e in merged2] != ["replaced", "removed"]:
        print(f"FAIL {name}: WT 'replaced' edit near L1 'removed' cut was dropped: {merged2}")
        sys.exit(1)
    l1_cut = next(e for e in merged2 if e["kind"] == "removed")
    if l1_cut.get("restored_text") is not None:
        print(f"FAIL {name}: incompatible 'replaced' edit grafted restored_text onto L1 'removed' cut: {l1_cut}")
        sys.exit(1)

    print(f"PASS {name}")


if __name__ == "__main__":
    test_merge_edits_conflates_different_edit_kinds_within_2s()
