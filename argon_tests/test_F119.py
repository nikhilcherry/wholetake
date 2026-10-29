import sys
from roomstamp.wt_detect import _empty_doc, _stage, fuse

doc = _empty_doc(["clip.wav"])
doc["clip"] = {"duration_s": 20.0}
doc["verdict"]["venue"] = {"name": "HH Goa demo stage", "key_id": "abc", "session": "s1", "mode": "pa"}
doc["layers"]["l1"] = _stage("absent", "s2", content_match=False)
doc["layers"]["l2"] = _stage("skipped", "s3")
doc["layers"]["wt"] = _stage(
    "present", "s4",
    recorded_at_unix=None,
    content_match=True,
    integrity_checked=True,
    edits=[{"kind": "removed", "clip_t": 12.0, "clip_t_range": [12.0, 12.0], "removed_s": 0.30, "source": "wt"}]
)
fuse(doc)
v = doc["verdict"]
h = v["headline"]
if v["recorded_here"] != "not_verified" or not v["content_match"]:
    print(f"FAIL f119: unexpected verdict {v}")
    sys.exit(1)
if not h.startswith("Words match HH Goa demo stage, but the room stamp is missing:") or "0.30 s removed at 0:12" not in h:
    print(f"FAIL f119: bad headline when witness has no unix start time: {h!r}")
    sys.exit(1)
print("PASS f119")
