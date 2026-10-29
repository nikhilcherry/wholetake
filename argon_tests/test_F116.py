import os
import sys
import tempfile
import shutil
from unittest.mock import patch
from roomstamp import wt_detect

EXPECTED_KEYS = {"path", "sha256", "bytes", "duration_s", "orig_rate", "orig_channels", "codec", "decoder"}

d = tempfile.mkdtemp(prefix="test_f116_")
try:
    # 1. Test ClipError path in run()
    missing_clip = os.path.join(d, "nonexistent.wav")
    doc = wt_detect.run(missing_clip, key_file=os.path.join(d, "nokey"))
    if doc.get("status") != "error":
        print(f"FAIL test_error_clip_schema_fields: expected status 'error', got {doc.get('status')!r}")
        sys.exit(1)
    clip_obj = doc.get("clip")
    if not isinstance(clip_obj, dict):
        print(f"FAIL test_error_clip_schema_fields: doc['clip'] is not a dict: {clip_obj!r}")
        sys.exit(1)
    missing = sorted(EXPECTED_KEYS - set(clip_obj.keys()))
    if missing:
        print(f"FAIL test_error_clip_schema_fields: doc['clip'] on error is missing documented schema keys: {missing}")
        sys.exit(1)

    # 2. Test INTERNAL error path in main()
    out_json = os.path.join(d, "out.json")
    with patch("roomstamp.wt_detect.run", side_effect=RuntimeError("boom")):
        try:
            wt_detect.main([missing_clip, "-o", out_json, "--quiet"])
        except SystemExit as e:
            if e.code != 4:
                print(f"FAIL test_error_clip_schema_fields: expected exit 4, got {e.code}")
                sys.exit(1)
    import json
    with open(out_json, encoding="utf-8") as f:
        doc2 = json.load(f)
    clip_obj2 = doc2.get("clip") or {}
    missing2 = sorted(EXPECTED_KEYS - set(clip_obj2.keys()))
    if missing2:
        print(f"FAIL test_error_clip_schema_fields: doc['clip'] on INTERNAL error is missing documented schema keys: {missing2}")
        sys.exit(1)

    print("PASS test_error_clip_schema_fields")
finally:
    shutil.rmtree(d, ignore_errors=True)
