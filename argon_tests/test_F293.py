"""Reproducer for F293: Threshold mismatch between MIN_PIECE_S (0.15 s) and scoring cutoff (0.25 s)
leaves squeezed pieces unscored, omits schema keys, and causes false 'stamp absent' verdicts."""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from unittest import mock

import numpy as np

from roomstamp import engine, l1verify, selftest, spec, wavio
from roomstamp.session import Session
from roomstamp.spec import FS

REQUIRED_L1_KEYS = (
    "l1_windows",
    "l1_clip_z",
    "l1_frac_z5",
    "l1_median_z",
    "l1_stamp_lag_ms",
    "l1_wrong_key_max_z",
    "l1_wrong_key_frac_z5",
    "l1_speech_removed_db",
    "l1_status",
)


def main() -> int:
    name = "piece_between_min_piece_s_and_quarter_sec"
    tmp = tempfile.mkdtemp(prefix="rs_f293_")
    try:
        x = selftest.load_speech()[:8 * FS]
        key = b"\x11" * 32
        t0 = selftest.T0
        params = engine.StampParams(mode="pa")
        out = engine.render(x, key, t0, params).astype(np.float64)
        wavio.write(os.path.join(tmp, "feed.wav"), x)
        sess = Session(
            key_id=spec.key_id(key),
            t_start=t0,
            params=params,
            feed="feed.wav",
            venue="F293 test stage",
            path=os.path.join(tmp, "session.json"),
        )

        # Case 1: Two pieces squeezed into [0.15 s, 0.25 s) (e.g. 0.20 s = 9600 samples) by _locate_bounds.
        # Before fix: MIN_PIECE_S=0.15 retained both 0.20 s pieces, _verify skipped scoring (n < FS // 4),
        # omitted schema keys, and set stamp='absent' ('the room stamp is missing').
        clip = np.concatenate([out[1 * FS:3 * FS], out[4 * FS:6 * FS]])
        squeezed_len = int(0.20 * FS)  # 9600 samples: 0.15 s <= len < 0.25 s

        def fake_locate_bounds(y, rend, left, right, chl, chr_):
            return left.c0 + squeezed_len, max(left.c0 + squeezed_len, right.c1 - squeezed_len)

        with mock.patch.object(l1verify, "_locate_bounds", side_effect=fake_locate_bounds):
            r1 = l1verify.verify(clip, x, sess, key, log=lambda *_: None)

        for p in r1["pieces"]:
            missing = [k for k in REQUIRED_L1_KEYS if k not in p]
            if missing or r1["stamp"] == "absent" or "stamp is missing" in r1["verdict"]:
                print(
                    f"FAIL {name}: Piece with status={p.get('l1_status')!r} (0.15s <= len < 0.25s) "
                    f"omits schema keys {missing} and reports false 'stamp is missing' verdict ({r1['verdict']!r})"
                )
                return 1

        if r1["stamp"] != "present":
            print(f"FAIL {name}: Expected surviving piece to score 'present', got stamp={r1['stamp']!r}")
            return 1

        # Case 2: Even when a single piece in [0.15 s, 0.25 s) is retained (len(pieces) == 1),
        # all schema keys must be present and top-level verdict must be 'inconclusive', never 'absent'.
        orig_split = l1verify._split_pieces

        def single_short_piece(clip_arr, rend, pieces, chans, channel=None):
            p = pieces[0]
            p.c0, p.c1 = 0, squeezed_len
            return [p], [chans[0]]

        with mock.patch.object(l1verify, "_split_pieces", side_effect=single_short_piece):
            r2 = l1verify.verify(clip, x, sess, key, log=lambda *_: None)

        for p in r2["pieces"]:
            missing = [k for k in REQUIRED_L1_KEYS if k not in p]
            if missing:
                print(f"FAIL {name}: Short piece omits schema keys {missing}")
                return 1
        if r2["stamp"] == "absent" or "stamp is missing" in r2["verdict"]:
            print(f"FAIL {name}: Unscored short piece caused false absent verdict: {r2['verdict']!r}")
            return 1
        if r2["stamp"] != "inconclusive":
            print(f"FAIL {name}: Expected stamp='inconclusive' for unscored short piece, got {r2['stamp']!r}")
            return 1

        print(f"PASS {name}")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
