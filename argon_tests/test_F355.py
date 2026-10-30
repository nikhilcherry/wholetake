import gc
import os
import shutil
import sys
import tempfile
import time
import sounddevice as sd
from roomstamp import emitter
from roomstamp.engine import StampParams


def main() -> int:
    tmpdir = tempfile.mkdtemp(prefix="test_f355_")
    orig_start_delay = emitter.START_DELAY
    orig_query_devices = sd.query_devices
    orig_output_stream = sd.OutputStream
    orig_switch = sys.getswitchinterval()
    try:
        emitter.START_DELAY = 0

        class FakeStream:
            latency = 0.1
            closed = False

            def __init__(self, **kw):
                pass

            def start(self):
                raise RuntimeError("simulated PortAudio stream.start failure")

            def stop(self):
                pass

            def close(self):
                self.closed = True

        sd.query_devices = lambda *a, **kw: {"name": "FakeSpeaker", "max_output_channels": 2, "max_input_channels": 0, "hostapi": 0}
        created_streams = []

        def make_stream(**kw):
            s = FakeStream(**kw)
            created_streams.append(s)
            return s

        sd.OutputStream = make_stream

        gc.enable()
        sys.setswitchinterval(0.005)

        em = emitter.Emitter(
            key=b"\x11" * 32,
            params=StampParams(mode="table"),
            log_dir=tmpdir,
            out_device=None,
            in_device="none",
            hostapi=None,
            save_output=True,
            quiet=True,
        )
        drand_stopped = False
        em.drand.start = lambda: None
        em.drand.last_ok = time.time()

        def fake_stop():
            nonlocal drand_stopped
            drand_stopped = True

        em.drand.stop = fake_stop

        try:
            em.run(duration=1.0)
        except RuntimeError:
            pass
        else:
            print("FAIL test_F355: expected RuntimeError from stream.start()")
            return 1

        failures = []
        if not gc.isenabled():
            failures.append("gc.isenabled() left False (garbage collector permanently disabled!)")
            gc.enable()
        if abs(sys.getswitchinterval() - 0.005) > 1e-6:
            failures.append(f"sys.getswitchinterval() left at {sys.getswitchinterval()} instead of 0.005")
        if not em.done.is_set():
            failures.append("em.done Event not set (producer daemon thread leaked running forever)")
            em.done.set()
        if not drand_stopped:
            failures.append("em.drand.stop() not called (drand polling thread leaked)")
        if not created_streams or not created_streams[0].closed:
            failures.append("stream.close() was not called after stream.start() failed")

        # Verify all opened files in tmpdir are closed (on Windows os.remove fails on open handles)
        for fname in os.listdir(tmpdir):
            fpath = os.path.join(tmpdir, fname)
            try:
                os.remove(fpath)
            except OSError as exc:
                failures.append(f"file {fname} leaked open handle: {exc}")

        if failures:
            print("FAIL test_F355: " + "; ".join(failures))
            return 1

        print("PASS test_F355")
        return 0
    finally:
        emitter.START_DELAY = orig_start_delay
        sd.query_devices = orig_query_devices
        sd.OutputStream = orig_output_stream
        gc.enable()
        sys.setswitchinterval(orig_switch)
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
