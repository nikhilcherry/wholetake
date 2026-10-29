import hashlib
import os
import shutil
import sys
import tempfile
import numpy as np

from roomstamp import engine, l1verify, media, selftest, spec, wavio
from roomstamp.session import Session
from roomstamp.spec import FS


def main() -> int:
    d = tempfile.mkdtemp(prefix="rs_f135_")
    try:
        x = selftest.load_speech()[:30 * FS]
        x = x + 10 ** (-80 / 20) * np.random.default_rng(1).standard_normal(len(x))
        key = hashlib.sha256(b"f135-venue-key").digest()
        wrong_key = hashlib.sha256(b"f135-wrong-key").digest()
        t0 = selftest.T0

        for mode in ("pa", "table"):
            p = engine.StampParams(mode=mode) if mode != "table" else engine.StampParams(mode="table", l1_dbfs=-45.0)
            out = engine.render(x, key, t0, p).astype(np.float64)
            feed_wav = os.path.join(d, f"feed_{mode}.wav")
            wavio.write(feed_wav, x)
            sess = Session(key_id=spec.key_id(key), t_start=t0, params=p, feed=feed_wav, venue="HH Goa test stage")
            rec = selftest.simulate(out, 0.6, 15.0, seed=3, mouth=x if mode == "table" else None)

            start_s, dur_s, cut_at, cut_len = 3.0, 22.0, 8.0, 0.30
            y = rec[int(start_s * FS):int((start_s + dur_s) * FS)]
            a = int(cut_at * FS)
            y = np.concatenate([y[:a], y[a + int(cut_len * FS):]])
            y = -media.transcode(y, "aac64")

            r = l1verify.verify(y, x, sess, key, log=lambda *_: None)
            if not r.get("matched") or r.get("stamp") != "present":
                print(f"FAIL polarity_inverted_clip: Expected {mode} stamp='present' under polarity inversion, got {r.get('stamp')}")
                return 1
            removed = [e for e in r.get("edits", []) if e.get("kind") == "removed"]
            if len(removed) != 1 or abs(removed[0]["removed_s"] - cut_len) > 0.010 or abs(removed[0]["clip_t"] - cut_at) > 0.25:
                print(f"FAIL polarity_inverted_clip: {mode} cut mismatch: {r.get('edits')}")
                return 1

            rw = l1verify.verify(y, x, sess, wrong_key, log=lambda *_: None)
            if rw.get("stamp") == "present":
                print(f"FAIL polarity_inverted_clip: {mode} wrong key locked under polarity inversion")
                return 1

        print("PASS polarity_inverted_clip")
        return 0
    finally:
        shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
