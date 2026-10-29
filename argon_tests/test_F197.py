import sys
from roomstamp import l1verify

def main() -> int:
    try:
        for bad_t in (-5.0, -30000.0, -100000.0, 40000000000.0, 1e16, float("inf"), float("-inf"), float("nan")):
            s = l1verify._fmt_utc(bad_t)
            if not s.startswith("t="):
                print(f"FAIL fmt_utc_extreme_timestamps: expected fallback for {bad_t}, got {s!r}")
                return 1

        for bad_t in (-30000.0, -100000.0, 40000000000.0):
            r = {
                "matched": True,
                "venue": "Test Venue",
                "stamp": "present",
                "pieces": [{"clip_start": 0.0, "clip_end": 5.0, "venue_t_start": bad_t, "l1_status": "present"}],
                "edits": [],
            }
            v = l1verify.human_verdict(r)
            if "t=" not in v:
                print(f"FAIL fmt_utc_extreme_timestamps: expected fallback in human_verdict for {bad_t}, got {v!r}")
                return 1

        normal = l1verify._fmt_utc(1730137218.0)
        if normal != "28 Oct 23:10:18 IST":
            print(f"FAIL fmt_utc_extreme_timestamps: unexpected normal formatting {normal!r}")
            return 1

        print("PASS fmt_utc_extreme_timestamps")
        return 0
    except Exception as e:
        print(f"FAIL fmt_utc_extreme_timestamps: {type(e).__name__}: {e}")
        return 1

if __name__ == "__main__":
    sys.exit(main())
