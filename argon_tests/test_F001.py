import math
import os
import queue
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from roomstamp import cli, emitter, engine, spec, wavio


class InterruptingQueue:
    def __init__(self, em, interrupt_at):
        self._q = queue.SimpleQueue()
        self._em = em
        self._interrupt_at = interrupt_at
        self._gets = 0

    def put_nowait(self, item):
        self._q.put_nowait(item)

    def get(self, timeout=None):
        self._gets += 1
        if self._gets == self._interrupt_at:
            # Simulate Ctrl+C while callback continues pushing blocks across a second boundary
            raise KeyboardInterrupt()
        return self._q.get(timeout=timeout)

    def get_nowait(self):
        return self._q.get_nowait()

    def empty(self):
        return self._q.empty()


class FakeOutputStream:
    def __init__(self, callback=None, blocksize=480, channels=1, **kw):
        self.callback = callback
        self.blocksize = blocksize
        self.channels = channels
        self.latency = 0.1

    def start(self):
        outdata = np.zeros((self.blocksize, self.channels), dtype=np.float32)
        tinfo = SimpleNamespace(outputBufferDacTime=0.0, currentTime=0.0)
        status = SimpleNamespace(output_underflow=False, input_overflow=False)
        # 105 blocks = 1.05 seconds (crosses second boundary at block 100, pos == 0)
        for _ in range(105):
            self.callback(outdata, self.blocksize, tinfo, status)

    def stop(self):
        pass

    def close(self):
        pass


def main():
    tmpdir = tempfile.mkdtemp()
    key = b"\x11" * 32
    params = engine.StampParams(mode="table", l1_floor=1.0)

    with patch.object(emitter, "START_DELAY", 0), \
         patch.object(emitter, "find_device", return_value=0), \
         patch("roomstamp.emitter.sd.query_devices", return_value={"name": "FakeSpeaker", "max_output_channels": 2}), \
         patch("roomstamp.emitter.sd.OutputStream", FakeOutputStream), \
         patch("roomstamp.emitter.sd.WasapiSettings", return_value=None):
        em = emitter.Emitter(key, params, log_dir=tmpdir, in_device="none", hostapi=None, quiet=True)
        em.drand.start = lambda: None
        em.drand.stop = lambda: None
        em.drand.last_ok = 1.0
        em.drand.get = lambda rnd: {"round": rnd, "randomness": ("aa" * 32)}
        # Interrupt at block 80 so blocks 80..104 (crossing pos == 0 at block 100) are drained in finally:
        em.q = InterruptingQueue(em, interrupt_at=80)
        log_path = em.run()

    header, secs = cli.read_log(log_path)
    feed_path = os.path.join(tmpdir, header["feed_wav"])
    feed = wavio.read(feed_path)
    n_sec = math.ceil(len(feed) / spec.FS)
    t0 = header["t_start"]
    logged_sec_ts = sorted(t for t in secs if t >= t0)
    expected_ts = list(range(t0, t0 + n_sec))

    if logged_sec_ts != expected_ts:
        print(f"FAIL F001: feed has {n_sec} seconds ({expected_ts}) but JSONL logged {logged_sec_ts}")
        sys.exit(1)
    for t in expected_ts:
        if not secs[t].get("anchored"):
            print(f"FAIL F001: second {t} not anchored in log")
            sys.exit(1)

    print("PASS F001")


if __name__ == "__main__":
    main()
