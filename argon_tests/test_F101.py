import json
import os
import sys
import tempfile
import numpy as np

from roomstamp import cli, detect, engine, spec, wavio
from roomstamp.spec import FS

def main():
    key = b"\x11" * 32
    p = engine.StampParams(mode="file")
    t0 = 1793209200

    try:
        # 1. 1-second clip (< NPERSEG=65536 and n_frames=10 < 11)
        feed_1s = np.random.default_rng(0).standard_normal(FS) * 0.1
        rec_1s = engine.render(feed_1s, key, t0, p).astype(np.float64)
        r1 = detect.detect(rec_1s, feed_1s, key, t0, p)
        assert r1["seconds"] == 1 and r1["l1"]["windows"] == 1 and r1["l2"]["frames"] == 10

        # 2. Degenerate 1-sample clip
        r_1samp = detect.detect(np.ones(1), np.ones(1), key, t0, p)
        assert r_1samp["seconds"] == 1
        assert len(detect._cancel(np.ones(1), np.ones(1))) == 1

        # 3. Empty feed or empty rec
        r_empty_feed = detect.detect(rec_1s, np.zeros(0), key, t0, p)
        assert r_empty_feed["seconds"] == 0 and r_empty_feed["l1"]["windows"] == 0
        r_empty_rec = detect.detect(np.zeros(0), feed_1s, key, t0, p)
        assert r_empty_rec["seconds"] == 0 and r_empty_rec["l1"]["windows"] == 0

        # 4. CLI detect with out-of-bounds --start
        tmp = tempfile.mkdtemp()
        key_path = os.path.join(tmp, "venue.key")
        with open(key_path, "w", encoding="utf-8") as f:
            f.write(key.hex() + "\n")
        feed_path = os.path.join(tmp, "feed.wav")
        rec_path = os.path.join(tmp, "rec.wav")
        log_path = os.path.join(tmp, "session.jsonl")
        wavio.write(feed_path, feed_1s)
        wavio.write(rec_path, rec_1s)
        with open(log_path, "w", encoding="utf-8") as f:
            f.write(json.dumps({
                "type": "session", "spec": spec.SPEC_VERSION, "key_id": spec.key_id(key),
                "t_start": t0, "fs": FS, "block": engine.BLOCK, "params": p.as_log(),
                "feed_wav": "feed.wav"
            }) + "\n")
        cli.main(["detect", rec_path, "--log", log_path, "--key-file", key_path, "--start", "99", "--dur", "5"])
        cli.main(["detect", rec_path, "--log", log_path, "--key-file", key_path, "--start", "0", "--dur", "1"])
        print("PASS detect_short_and_empty")
    except Exception as e:
        print(f"FAIL detect_short_and_empty: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
