import sys
from roomstamp.wt_detect import _empty_doc, _stage, fuse

def make_doc(l1_st, l2_st, wt_st):
    doc = _empty_doc(["clip.wav"])
    doc["clip"] = {"duration_s": 20.0}
    doc["layers"]["l1"] = l1_st
    doc["layers"]["l2"] = l2_st
    doc["layers"]["wt"] = wt_st
    return fuse(doc)

# Case 1: L1 inconclusive with recorded_at_unix, L2 present with tempo=1.25
d1 = make_doc(
    _stage("inconclusive", "s2", recorded_at_unix=1000.0, content_match=True),
    _stage("present", "s3", recorded_at_unix=1000.0, tempo=1.25),
    _stage("skipped"),
)
if d1["verdict"]["tempo_ratio"] != 1.25:
    print(f"FAIL test_tempo_ratio_populated_when_l2_measures_it: got {d1['verdict']['tempo_ratio']} instead of 1.25")
    sys.exit(1)

# Case 2: witness-only match (L1/L2 skipped) -> tempo_ratio must be None
d2 = make_doc(
    _stage("skipped"),
    _stage("skipped"),
    _stage("present", "s4", recorded_at_unix=1000.0, content_match=True),
)
if d2["verdict"]["tempo_ratio"] is not None:
    print(f"FAIL test_tempo_ratio_populated_when_l2_measures_it: witness-only got {d2['verdict']['tempo_ratio']} instead of None")
    sys.exit(1)

# Case 3: L1 absent with content_match=True, L2 skipped -> tempo_ratio must be None
d3 = make_doc(
    _stage("absent", "s2", recorded_at_unix=1000.0, content_match=True),
    _stage("skipped"),
    _stage("skipped"),
)
if d3["verdict"]["tempo_ratio"] is not None:
    print(f"FAIL test_tempo_ratio_populated_when_l2_measures_it: unstamped L1 got {d3['verdict']['tempo_ratio']} instead of None")
    sys.exit(1)

# Case 4: L1 present, L2 skipped -> tempo_ratio is 1.0
d4 = make_doc(
    _stage("present", "s2", recorded_at_unix=1000.0, content_match=True),
    _stage("skipped"),
    _stage("skipped"),
)
if d4["verdict"]["tempo_ratio"] != 1.0:
    print(f"FAIL test_tempo_ratio_populated_when_l2_measures_it: L1 present got {d4['verdict']['tempo_ratio']} instead of 1.0")
    sys.exit(1)

print("PASS test_tempo_ratio_populated_when_l2_measures_it")
