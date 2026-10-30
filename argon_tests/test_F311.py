"""Reproducer test for F311: detect() crashes with internal ValueError when tempos or taus are empty or non-positive."""
from __future__ import annotations

import sys
import numpy as np

from roomstamp import blind
from roomstamp.spec import FS


def main() -> None:
    clip = np.zeros(int(8.5 * FS), dtype=np.float64)
    key = b"\x11" * 32

    class SpySource(blind.RandomnessSource):
        def __init__(self):
            super().__init__(unanchored=True)
            self.prefetch_calls = 0

        def prefetch(self, t_lo: int, t_hi: int) -> None:
            self.prefetch_calls += 1

    one_row_tempo = (blind.HOP * 1.5) / len(clip)
    cases = [
        ("tempos=()", {"tempos": ()}),
        ("taus=()", {"taus": ()}),
        ("tempos=(0.0,)", {"tempos": (0.0,)}),
        ("tempos=(-1.0,)", {"tempos": (-1.0,)}),
        ("tempos=(0.0001,)", {"tempos": (0.0001,)}),
        ("tempos=(1-row,)", {"tempos": (one_row_tempo,)}),
        ("taus=(-1.0,)", {"taus": (-1.0,)}),
        ("tempos=(nan,)", {"tempos": (float("nan"),)}),
        ("taus=(inf,)", {"taus": (float("inf"),)}),
    ]

    internal_crashes = (
        "max() arg is an empty sequence",
        "zero-size array",
        "Invalid number of",
        "cannot convert float",
    )

    failures = []
    for label, kwargs in cases:
        src = SpySource()
        try:
            blind.detect(clip, key, src, 1000, 1050, n_null=10, **kwargs)
        except ValueError as e:
            msg = str(e)
            if any(sub in msg for sub in internal_crashes):
                failures.append(f"{label} crashed internally with: {msg}")
            elif src.prefetch_calls > 0:
                failures.append(f"{label} executed src.prefetch before failing ({msg})")
        except Exception as e:
            failures.append(f"{label} raised {type(e).__name__}: {e}")
        else:
            failures.append(f"{label} did not raise ValueError")

    if failures:
        print("FAIL test_F311: " + "; ".join(failures))
        sys.exit(1)
    print("PASS test_F311")


if __name__ == "__main__":
    main()
