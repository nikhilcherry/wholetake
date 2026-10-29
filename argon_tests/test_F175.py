import sys
from roomstamp.wt_detect import _empty_doc, _stage, fuse


def _make_doc(l1_edits, wt_edits):
    doc = _empty_doc(["wt-detect", "clip.wav"])
    doc["clip"] = {
        "path": "clip.wav",
        "sha256": "00" * 32,
        "bytes": 1000,
        "duration_s": 20.0,
        "orig_rate": 48000,
        "orig_channels": 1,
        "codec": "wav",
        "decoder": "soundfile",
    }
    doc["verdict"]["venue"] = {
        "name": "HH Goa demo stage",
        "key_id": "0123456789abcdef",
        "session": "session_1793430600",
        "mode": "pa",
    }
    doc["layers"]["l1"] = _stage(
        "present",
        "s2",
        "verified",
        recorded_at_unix=1793430608.0,
        venue_t_start=1793430608.0,
        venue_t_end=1793430628.0,
        content_match=True,
        integrity_checked=True,
        edits=l1_edits,
    )
    doc["layers"]["wt"] = _stage(
        "present",
        "s4",
        "cut",
        recorded_at_unix=1793430608.0,
        venue_t_start=1793430608.0,
        venue_t_end=1793430628.0,
        content_match=True,
        integrity_checked=True,
        edits=wt_edits,
    )
    return doc


def main():
    # Case 1: Earlier wordless edit at edits[0], followed by a word-deleting cut at edits[1]
    l1_edits_1 = [
        {"kind": "removed", "clip_t": 2.0, "clip_t_range": [2.0, 2.0], "removed_s": 0.25, "precision_s": 0.002, "source": "l1"},
        {"kind": "removed", "clip_t": 12.0, "clip_t_range": [12.0, 12.0], "removed_s": 0.30, "precision_s": 0.002, "source": "l1"},
    ]
    wt_edits_1 = [
        {"kind": "removed", "clip_t": 2.0, "clip_t_range": [2.0, 2.0], "removed_s": 0.25, "precision_s": 0.05, "source": "wt", "restored_text": None},
        {"kind": "removed", "clip_t": 12.0, "clip_t_range": [12.0, 12.0], "removed_s": 0.30, "precision_s": 0.05, "source": "wt", "restored_text": "not"},
    ]
    doc1 = fuse(_make_doc(l1_edits_1, wt_edits_1))
    h1 = doc1["verdict"]["headline"]
    x1 = doc1["verdict"]["x_reply_text"]
    if 'Missing words: "not".' not in h1 or 'Missing words: "not".' not in x1:
        print(f"FAIL test_F175: Case 1 missing restored words in headline: {h1!r}")
        sys.exit(1)

    # Case 2: Two word-deleting cuts at edits[0] and edits[1]
    l1_edits_2 = [
        {"kind": "removed", "clip_t": 4.0, "clip_t_range": [4.0, 4.0], "removed_s": 0.30, "precision_s": 0.002, "source": "l1"},
        {"kind": "removed", "clip_t": 15.0, "clip_t_range": [15.0, 15.0], "removed_s": 0.35, "precision_s": 0.002, "source": "l1"},
    ]
    wt_edits_2 = [
        {"kind": "removed", "clip_t": 4.0, "clip_t_range": [4.0, 4.0], "removed_s": 0.30, "precision_s": 0.05, "source": "wt", "restored_text": "not"},
        {"kind": "removed", "clip_t": 15.0, "clip_t_range": [15.0, 15.0], "removed_s": 0.35, "precision_s": 0.05, "source": "wt", "restored_text": "never"},
    ]
    doc2 = fuse(_make_doc(l1_edits_2, wt_edits_2))
    h2 = doc2["verdict"]["headline"]
    if 'Missing words: "not", "never".' not in h2:
        print(f"FAIL test_F175: Case 2 did not include both restored words in headline: {h2!r}")
        sys.exit(1)

    # Case 3: Single cut at edits[0] with restored_text
    l1_edits_3 = [
        {"kind": "removed", "clip_t": 12.0, "clip_t_range": [12.0, 12.0], "removed_s": 0.30, "precision_s": 0.002, "source": "l1"},
    ]
    wt_edits_3 = [
        {"kind": "removed", "clip_t": 12.0, "clip_t_range": [12.0, 12.0], "removed_s": 0.30, "precision_s": 0.05, "source": "wt", "restored_text": "not"},
    ]
    doc3 = fuse(_make_doc(l1_edits_3, wt_edits_3))
    h3 = doc3["verdict"]["headline"]
    if not h3.endswith(' Missing words: "not".'):
        print(f"FAIL test_F175: Case 3 unexpected single-cut headline format: {h3!r}")
        sys.exit(1)

    print("PASS test_F175")


if __name__ == "__main__":
    main()
