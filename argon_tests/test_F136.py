"""Reproducer test for F136: _scan must not allocate O(len(Fc) * len(Fw)) dense float32 matrices."""
from __future__ import annotations

import os
import sys
import time
import tracemalloc

import numpy as np

os.environ["WHOLETAKE_ASR"] = "none"

from wholetake import match, record


def _unit_rows(x: np.ndarray) -> np.ndarray:
    x = x.astype(np.float32, copy=False)
    n = np.linalg.norm(x, axis=1, keepdims=True) + 1e-9
    return (x / n).astype(np.float32)


def main() -> int:
    try:
        # 1. Exact numerical equivalence against a small reference implementation
        rec_dir = os.path.join("testdata", "witness", "record_noasr")
        rec = record.load(rec_dir)
        Fw_small = rec.feats[:4000]
        Fc_small = rec.feats[1500:3200].copy()
        regions_small = [(0, 2000), (2100, 4000)]

        # Reference calculation
        win, hop = match.WIN, match.HOP
        starts = np.arange(0, max(1, len(Fc_small) - win + 1), hop)
        starts = starts[starts + win <= len(Fc_small)]
        active = (np.abs(Fc_small).sum(1) > 0).astype(np.float32)
        n_act = np.array([active[u:u + win].sum() for u in starts])
        rows_ref = []
        for r0, r1 in regions_small:
            L = r1 - r0 - win + 1
            D = Fc_small @ Fw_small[r0:r1].T
            S = np.zeros((len(starts), L), np.float32)
            for k in range(win):
                S += D[starts + k, k:k + L]
            rows_ref.append((r0, S))
        out_ref = []
        for i, u in enumerate(starts):
            if not rows_ref or n_act[i] < match.MIN_ACTIVE * win:
                out_ref.append((int(u), None, 0.0, 0.0))
                continue
            allc = np.concatenate([S[i] for _, S in rows_ref]) / n_act[i]
            k = int(np.argmax(allc))
            off = 0
            for r0_, S in rows_ref:
                if k < off + S.shape[1]:
                    pos = r0_ + (k - off)
                    break
                off += S.shape[1]
            med = np.median(allc)
            mad = 1.4826 * np.median(np.abs(allc - med)) + 1e-6
            out_ref.append((int(u), int(pos), float(allc[k]), float((allc[k] - med) / mad)))

        out_got = match._scan(Fc_small, Fw_small, regions_small)
        if len(out_got) != len(out_ref):
            print(f"FAIL test_F136: length mismatch {len(out_got)} != {len(out_ref)}")
            return 1
        for (u1, p1, r1, z1), (u2, p2, r2, z2) in zip(out_got, out_ref):
            if u1 != u2 or p1 != p2 or abs(r1 - r2) > 1e-4 or abs(z1 - z2) > 1e-3:
                print(f"FAIL test_F136: mismatch at u={u1}: got {(u1, p1, r1, z1)} vs ref {(u2, p2, r2, z2)}")
                return 1

        # 2. Maximum supported dimensions: 600 s clip (60,000 frames) vs 1200 s witness (120,000 frames).
        # Unpatched _scan allocates D of shape (60000, 120000) float32 = 28.8 GB -> immediate MemoryError.
        rng = np.random.default_rng(136)
        T_w = 120000  # 20 min witness at 100 fps (WHOLE_RECORD_MAX_S)
        T_c = 60000   # 10 min clip at 100 fps (MAX_CLIP_S)
        Fw_long = _unit_rows(rng.standard_normal((T_w, 40), dtype=np.float32))
        Fc_long = np.zeros((T_c, 40), dtype=np.float32)
        # Embed 20 s of active speech at clip [0..2000) matching witness [50000..52000)
        true_off = 50000
        Fc_long[:2000] = Fw_long[true_off:true_off + 2000]

        tracemalloc.start()
        t0 = time.time()
        # Test sparse starts (_tempo_refine pattern) on full 600s x 1200s
        st = np.linspace(0, len(Fc_long) - 2 * match.WIN, num=10).astype(int)
        res_sparse = match._scan(Fc_long, Fw_long, [(0, T_w)], win=2 * match.WIN, starts=st)
        # Test dense _scan across full 600s clip vs 1200s witness
        res_dense = match._scan(Fc_long, Fw_long, [(0, T_w)])
        elapsed = time.time() - t0
        _, peak_bytes = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        peak_mb = peak_bytes / (1024 * 1024)
        if peak_mb > 250.0:
            print(f"FAIL test_F136: peak memory {peak_mb:.1f} MB exceeds 250 MB limit (< 1 GB required)")
            return 1
        if elapsed > 30.0:
            print(f"FAIL test_F136: _scan took {elapsed:.1f}s (> 30s)")
            return 1

        if res_sparse[0][1] != true_off or res_sparse[0][2] < 0.99:
            print(f"FAIL test_F136: sparse scan failed to lock witness offset {true_off}: got {res_sparse[0]}")
            return 1

        pieces = match._group(res_dense)
        if len(pieces) != 1 or abs(pieces[0].c0 - true_off) > 1.0:
            print(f"FAIL test_F136: dense scan failed to recover piece at offset {true_off}: got {pieces}")
            return 1

        print(f"PASS test_F136 (peak_ram={peak_mb:.1f}MB, time={elapsed:.2f}s)")
        return 0
    except Exception as e:
        print(f"FAIL test_F136: {type(e).__name__}: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
