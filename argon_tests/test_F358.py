import shutil
import sys
import tempfile
import threading

from roomstamp.emitter import Emitter
from roomstamp.engine import StampParams


def main() -> None:
    tmpdir = tempfile.mkdtemp(prefix="test_f358_")
    try:
        key = b"\x11" * 32
        params = StampParams(mode="table")

        # 1. Test invalid latency values in Emitter.__init__
        invalid_latencies = [-0.5, 0.0, float("nan"), float("inf"), float("-inf"), True, False, "0.1"]
        for lat in invalid_latencies:
            try:
                Emitter(
                    key,
                    params,
                    log_dir=tmpdir,
                    in_device="none",
                    out_device=None,
                    hostapi=None,
                    latency=lat,
                )
            except (ValueError, SystemExit):
                pass
            else:
                print(f"FAIL test_F358: Emitter accepted invalid latency={lat!r} without raising ValueError/SystemExit")
                sys.exit(1)

        # 2. Test invalid duration values in Emitter.run()
        em = Emitter(
            key,
            params,
            log_dir=tmpdir,
            in_device="none",
            out_device=None,
            hostapi=None,
            latency=0.1,
        )
        # Replace drand.start with a sentinel so if validation fails to fire BEFORE drand.start(),
        # we catch it immediately without starting network threads or hanging on audio.
        started_drand = False

        def fake_start():
            nonlocal started_drand
            started_drand = True
            raise RuntimeError("drand.start() was called before rejecting invalid duration")

        em.drand.start = fake_start

        invalid_durations = [0, 0.0, -1.0, float("nan"), float("inf"), float("-inf"), True, False, "5"]
        for dur in invalid_durations:
            started_drand = False
            try:
                em.run(duration=dur)
            except (ValueError, SystemExit):
                if started_drand:
                    print(f"FAIL test_F358: Emitter.run started drand before validating duration={dur!r}")
                    sys.exit(1)
            except RuntimeError as e:
                print(f"FAIL test_F358: Emitter.run did not reject invalid duration={dur!r} ({e})")
                sys.exit(1)
            else:
                print(f"FAIL test_F358: Emitter.run accepted invalid duration={dur!r}")
                sys.exit(1)

        print("PASS test_F358")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    main()
