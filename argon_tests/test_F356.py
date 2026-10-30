import shutil
import sys
import tempfile
import numpy as np

from roomstamp import engine
from roomstamp.emitter import Emitter
from roomstamp.engine import StampParams


def main() -> int:
    tmp_dir = tempfile.mkdtemp(prefix="test_F356_")
    try:
        params = StampParams(mode="table", l1_floor=0.3)
        key = b"\x11" * 32
        em = Emitter(
            key=key,
            params=params,
            log_dir=tmp_dir,
            out_device=None,
            in_device="none",
            hostapi=None,
        )
        if params.l1_floor != 0.3:
            print(f"FAIL test_F356: caller StampParams.l1_floor mutated in-place to {params.l1_floor!r}")
            return 1
        if em.p.l1_floor != 1.0 or em.stamper.p.l1_floor != 1.0:
            print(
                f"FAIL test_F356: emitter internal l1_floor should be 1.0, "
                f"got em.p={em.p.l1_floor!r}, stamper.p={em.stamper.p.l1_floor!r}"
            )
            return 1
        if em.p is params or em.stamper.p is params:
            print("FAIL test_F356: Emitter aliases caller's StampParams instance")
            return 1

        # Verify subsequent render with caller's params retains gating (l1_floor=0.3 vs 1.0 on silence)
        silence = np.zeros(48000, dtype=np.float64)
        out_gated = engine.render(silence, key, 1793209200, params)
        out_ungated = engine.render(silence, key, 1793209200, StampParams(mode="table", l1_floor=1.0))
        ratio = float(np.sqrt(np.mean(out_gated ** 2)) / np.sqrt(np.mean(out_ungated ** 2)))
        if not (0.29 < ratio < 0.31):
            print(f"FAIL test_F356: subsequent render gating ratio expected ~0.3, got {ratio:.4f}")
            return 1

        print("PASS test_F356")
        return 0
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
