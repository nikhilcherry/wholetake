from __future__ import annotations

import os
import secrets
import shutil
import sys
import tempfile
import numpy as np

from roomstamp import engine, l1verify, selftest, spec, s2test, wavio
from roomstamp.session import Session
from roomstamp.spec import FS


def main() -> int:
    d = tempfile.mkdtemp(prefix="rs_f341_")
    try:
        x = selftest.load_speech()[:35 * FS]
        x = x + 10 ** (-80 / 20) * np.random.default_rng(1).standard_normal(len(x))
        key = secrets.token_bytes(32)
        p = engine.StampParams(mode="pa")
        out = engine.render(x, key, selftest.T0, p).astype(np.float64)
        wavio.write(os.path.join(d, "feed.wav"), x)
        sess = Session(key_id=spec.key_id(key), t_start=selftest.T0, params=p, feed="feed.wav", venue="HH Goa test stage")
        sess.save(os.path.join(d, "session.json"))

        rec = selftest.simulate(out, 0.6, 15.0, seed=3)
        for drift in (400.0, -450.0):
            y = s2test.clipper(rec, start_s=5, dur_s=25, cuts=(), drift_ppm=drift)
            r = l1verify.verify(y, x, sess, key, log=lambda *_: None)
            pieces_info = [(round(pc.get("drift_ppm", 0.0), 1), pc.get("l1_status"), pc.get("l1_clip_z", 0.0)) for pc in r.get("pieces", [])]
            if (
                not r.get("matched")
                or r.get("stamp") != "present"
                or len(r.get("pieces", [])) != 1
                or r["pieces"][0].get("l1_status") != "present"
                or abs(r["pieces"][0].get("drift_ppm", 0.0) - drift) > 25.0
                or r.get("edits") != []
            ):
                print(
                    f"FAIL clock_drift_above_300ppm: {drift:+.0f} ppm clock drift caused stamp verification failure: "
                    f"matched={r.get('matched')}, stamp={r.get('stamp')}, pieces={pieces_info}, edits={r.get('edits')}"
                )
                return 1
        print("PASS clock_drift_above_300ppm")
        return 0
    finally:
        shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
