import sys
import numpy as np
from roomstamp import engine, l1verify, spec
from roomstamp.session import Session
from roomstamp.spec import FS

key = b"\x11" * 32
sess = Session(key_id=spec.key_id(key), t_start=1700000000, params=engine.StampParams(mode="pa"), feed="feed.wav")
feed_ok = np.zeros(2 * FS, dtype=np.float64)

cases = [
    ("test_01_empty_clip", np.zeros(0), feed_ok),
    ("test_02_empty_feed", feed_ok, np.zeros(0)),
    ("test_03_one_sample_clip_and_feed", np.ones(1), np.ones(1)),
    ("test_04_half_second_clip", np.ones(FS // 2), feed_ok),
]

failed = False
for name, clip, feed in cases:
    try:
        r = l1verify.verify(clip, feed, sess, key, log=lambda *_: None)
        if r.get("matched") is not False or r.get("stamp") != "absent" or r.get("pieces") != []:
            print(f"FAIL {name}: unexpected result {r}")
            failed = True
        else:
            print(f"PASS {name}")
    except Exception as e:
        print(f"FAIL {name}: unexpected {type(e).__name__}: {e}")
        failed = True

wins = l1verify.align_windows(np.ones(1), np.ones(l1verify.AWIN * 2))
if wins != []:
    print(f"FAIL test_05_align_windows_short: got {wins}")
    failed = True
else:
    print("PASS test_05_align_windows_short")

sys.exit(1 if failed else 0)
