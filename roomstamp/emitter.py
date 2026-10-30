"""S1 real-time stamp emitter.

Threads
- drand:    DrandCache polls quicknet every second (network only here).
- producer: KeystreamSequencer prepares seconds >= 3 s ahead of the audio clock.
- audio:    PortAudio callback; reads prepared arrays, runs Stamper.process, never blocks.
- main:     drains the callback queue -> clean feed WAV (+ optional output WAV) + JSONL log.

Log (logs/session_<t_start>.jsonl): one "session" header line, then one "sec" line per
emitted second with the drand round used, anchor hash, and timing (estimated UTC at which
the second's first sample hit the DAC). The key itself is never logged, only key_id.
"""
from __future__ import annotations

import gc
import json
import math
import os
import platform
import queue
import sys
import threading
import time

import numpy as np
import scipy
import sounddevice as sd

from . import engine, spec, wavio
from .drand import DrandCache
from .engine import BLOCK, StampParams
from .spec import FS

LEAD_SECONDS = 3          # prepared keystream ahead of the audio clock
START_DELAY = 3           # t_start = now + this
FADE_IN = int(0.2 * FS)
FADE_OUT = int(0.1 * FS)
DRIFT_WARN_MS = 1500.0


def find_device(spec_: str | int | None, kind: str, hostapi: str | None) -> int | None:
    if spec_ is None:
        if hostapi:
            for i, h in enumerate(sd.query_hostapis()):
                if hostapi.lower() in h["name"].lower():
                    d = h["default_output_device" if kind == "output" else "default_input_device"]
                    return d if d >= 0 else None
        return None
    if isinstance(spec_, bool):
        raise SystemExit(f"no {kind} device matching {spec_!r} (host API {hostapi}); run `roomstamp devices`")
    ch_key = "max_output_channels" if kind == "output" else "max_input_channels"
    devs = list(sd.query_devices())
    if isinstance(spec_, int) or str(spec_).isdigit():
        idx = int(spec_)
        if 0 <= idx < len(devs) and devs[idx][ch_key] > 0:
            return idx
        raise SystemExit(f"no {kind} device matching {spec_!r} (host API {hostapi}); run `roomstamp devices`")
    want = str(spec_).lower()
    apis = sd.query_hostapis()
    for i, d in enumerate(devs):
        if d[ch_key] > 0 and want in d["name"].lower() and (not hostapi or hostapi.lower() in apis[d["hostapi"]]["name"].lower()):
            return i
    raise SystemExit(f"no {kind} device matching {spec_!r} (host API {hostapi}); run `roomstamp devices`")


class Emitter:
    def __init__(self, key: bytes, params: StampParams, log_dir: str = "logs", wav_in: str | None = None,
                 loop_wav: bool = True, out_device=None, in_device=None, hostapi: str | None = "WASAPI",
                 save_output: bool = False, quiet: bool = False, latency: float | None = None):
        self.key, self.p, self.log_dir, self.quiet = key, params, log_dir, quiet
        self.use_input = params.mode in ("pa", "table") and in_device != "none"
        self.save_output = save_output
        # Python callbacks occasionally stall 25-40 ms (GIL/GC); WASAPI fills a starved
        # buffer with silence *without* flagging an underflow, so the buffer must be larger
        # than any stall. Measured on this laptop (25 s runs): 30 ms slips ~40 ms, 50 ms ~20 ms,
        # 80 and 100 ms slip 0. pa mode uses 80 ms (live voice through the PA lags ~85 ms).
        if latency is not None:
            if isinstance(latency, bool) or not isinstance(latency, (int, float, np.floating)):
                raise ValueError(f"latency must be a finite positive number, got {latency!r}")
            latency = float(latency)
            if not math.isfinite(latency) or latency <= 0.0:
                raise ValueError(f"latency must be > 0 and finite, got {latency!r}")
        self.latency = latency if latency is not None else (0.08 if params.mode == "pa" else 0.1)
        self.hostapi = hostapi
        self.out_dev = find_device(out_device, "output", hostapi)
        self.in_dev = find_device(None if in_device == "none" else in_device, "input", hostapi) if self.use_input else None
        if params.mode == "table" and not self.use_input and params.l1_floor < 1.0:
            print("[roomstamp] table mode without a mic: gating off (--l1-floor 1.0), feed will be empty")
            params.l1_floor = 1.0
        self.silent_secs = 0
        self.file_audio = None
        if params.mode == "file":
            if not wav_in:
                raise SystemExit("--wav-in is required for --mode file")
            self.file_audio = wavio.read(wav_in)
            if len(self.file_audio) == 0:
                raise SystemExit("--wav-in file is empty")
            self.loop_wav = loop_wav
            self.file_pos = 0
        os.makedirs(log_dir, exist_ok=True)

        self.drand = DrandCache()
        self.stamper = engine.Stamper(params)
        self.prepared: dict[int, engine.PreparedSecond] = {}
        self.q: queue.SimpleQueue = queue.SimpleQueue()
        self.n = 0                       # samples emitted
        self.stopping = False
        self.fade_left = FADE_OUT
        self.done = threading.Event()

    # ---- keystream -------------------------------------------------------------------
    def _randomness(self, t: int) -> bytes | None:
        """Cache-only: never touches the network (DNS lookups can hang far past any
        timeout when venue Wi-Fi drops). A round that isn't cached by the time its second
        is prepared makes that second UNANCHORED, which the log records."""
        d = self.drand.get(spec.round_for(t))
        return bytes.fromhex(d["randomness"]) if d else None

    def _producer(self) -> None:
        while not self.done.is_set():
            audio_t = self.t_start + self.n // FS
            while self.seq.next_t <= audio_t + LEAD_SECONDS:
                ps = self.seq.next()
                self.prepared[ps.ks.t] = ps
            for t in [t for t in self.prepared if t < audio_t - 2]:
                del self.prepared[t]
            time.sleep(0.05)

    # ---- audio callback --------------------------------------------------------------
    def _callback(self, indata, outdata, frames, tinfo, status):
        if frames != BLOCK:   # fixed blocksize makes this impossible; never raise in the audio thread
            outdata[:] = 0
            return
        n0 = self.n
        t = self.t_start + n0 // FS
        pos = n0 % FS
        if self.p.mode == "file":
            fa = self.file_audio
            x = np.zeros(frames)
            if len(fa) > 0 and (self.loop_wav or self.file_pos < len(fa)):
                idx = (self.file_pos + np.arange(frames)) % len(fa) if self.loop_wav else \
                    np.arange(self.file_pos, min(self.file_pos + frames, len(fa)))
                x[:len(idx)] = fa[idx]
                self.file_pos += frames
        elif indata is not None:
            x = indata[:, 0].astype(np.float64)
        else:
            x = np.zeros(frames)

        x = np.nan_to_num(x, nan=0.0, posinf=1.0, neginf=-1.0)
        state = (self.stamper.ref_rms, self.stamper.gain) if pos == 0 else None   # logged: lets a verifier start mid-session
        out, stamp, st = self.stamper.process(x, self.prepared.get(t), pos)
        # fades are emitter-only (first 0.2 s / last 0.1 s of a session); verifiers skip them
        if n0 < FADE_IN:
            out *= np.minimum(1.0, (n0 + np.arange(frames)) / FADE_IN).astype(np.float32)
        if self.stopping:
            g = np.clip((self.fade_left - np.arange(frames)) / FADE_OUT, 0.0, 1.0).astype(np.float32)
            out *= g
            self.fade_left = max(0, self.fade_left - frames)

        outdata[:] = out[:, None]
        self.n = n0 + frames
        dac_utc = time.time() + (tinfo.outputBufferDacTime - tinfo.currentTime)
        self.q.put_nowait((t, pos, x.astype(np.float32), out if self.save_output else None, st, dac_utc,
                           bool(status.output_underflow), bool(status.input_overflow), state))

    # ---- main --------------------------------------------------------------------------
    def run(self, duration: float | None = None) -> str:
        if duration is not None:
            if isinstance(duration, bool) or not isinstance(duration, (int, float, np.floating)):
                raise ValueError(f"duration must be a finite positive number, got {duration!r}")
            duration = float(duration)
            if not math.isfinite(duration) or duration <= 0.0:
                raise ValueError(f"duration must be > 0 and finite, got {duration!r}")
        self.drand.start()
        for _ in range(30):            # give the first poll up to 3 s, never more
            if self.drand.last_ok:
                break
            time.sleep(0.1)
        if not self.drand.last_ok:
            print("[roomstamp] WARNING: drand unreachable; seconds will be UNANCHORED until it recovers")
        self.t_start = int(time.time()) + START_DELAY
        self.seq = engine.KeystreamSequencer(self.key, self.t_start,
                                             self.p.l2_delta_db if self.p.mode != "table" else 0.0, self._randomness)
        for _ in range(LEAD_SECONDS):
            ps = self.seq.next()
            self.prepared[ps.ks.t] = ps
        threading.Thread(target=self._producer, daemon=True, name="producer").start()

        out_info = sd.query_devices(self.out_dev if self.out_dev is not None else sd.default.device[1])
        n_out = min(2, out_info["max_output_channels"])
        extra = None
        if self.hostapi and "wasapi" in self.hostapi.lower():
            extra = sd.WasapiSettings(auto_convert=True)
        kw = dict(samplerate=FS, blocksize=BLOCK, dtype="float32", latency=self.latency)
        if self.use_input:
            stream = sd.Stream(device=(self.in_dev, self.out_dev), channels=(1, n_out),
                               extra_settings=(extra, extra) if extra else None, callback=self._callback, **kw)
        else:
            stream = sd.OutputStream(device=self.out_dev, channels=n_out, extra_settings=extra,
                                     callback=lambda o, f, ti, s: self._callback(None, o, f, ti, s), **kw)

        tag = f"{self.t_start}"
        log_path = os.path.join(self.log_dir, f"session_{tag}.jsonl")
        feed_path = os.path.join(self.log_dir, f"feed_{tag}.wav")
        out_path = os.path.join(self.log_dir, f"out_{tag}.wav") if self.save_output else None
        lat = stream.latency
        header = {
            "type": "session", "spec": spec.SPEC_VERSION, "key_id": spec.key_id(self.key), "t_start": self.t_start,
            "fs": FS, "block": BLOCK, "params": self.p.as_log(), "round_lag": spec.ROUND_LAG,
            "drand_chain": spec.DRAND_CHAIN,
            "warmup": {"t": self.seq.warmup.t, "round": self.seq.warmup.round, "anchored": self.seq.warmup.anchored,
                       "randomness": self.seq.warmup.randomness.hex() if self.seq.warmup.randomness else None},
            "devices": {"out": out_info["name"], "in": sd.query_devices(self.in_dev)["name"] if self.use_input and self.in_dev is not None else None,
                        "hostapi": self.hostapi, "latency_s": list(lat) if isinstance(lat, tuple) else lat},
            "feed_wav": os.path.basename(feed_path), "out_wav": os.path.basename(out_path) if out_path else None,
            "feed_source": {"pa": "input", "table": "input", "file": "wav"}[self.p.mode],
            "versions": {"python": platform.python_version(), "numpy": np.__version__, "scipy": scipy.__version__},
            "note": "feed sample i and output sample i share the stream clock; output sample i belongs to second t_start + i // fs",
        }
        feed = wavio.StreamWriter(feed_path)
        outw = wavio.StreamWriter(out_path) if out_path else None
        logf = open(log_path, "w", encoding="utf-8")
        logf.write(json.dumps(header) + "\n")
        logf.flush()

        print(f"[roomstamp] {spec.SPEC_VERSION} mode={self.p.mode} key_id={header['key_id']} out='{out_info['name']}'"
              f"{' in=' + str(header['devices']['in']) if self.use_input else ''}")
        print(f"[roomstamp] log {log_path}  feed {feed_path}  (Ctrl+C to stop)")

        # Start at t_start. PortAudio pre-fills the whole buffer immediately, so sample 0
        # reaches the DAC ~t_start (measured; subtracting the latency put it ~90 ms early).
        # The exact DAC time of every second is logged anyway (dac_utc / drift_ms).
        wait = self.t_start - time.time()
        if wait > 0:
            time.sleep(wait)
        gc.disable()   # cyclic GC pauses stall the callback; refcounting still frees memory
        old_switch = sys.getswitchinterval()
        sys.setswitchinterval(0.0005)   # hand the GIL to the audio thread fast (default 5 ms)
        stream.start()

        cur = None   # per-second accumulator

        def handle_item(item):
            nonlocal cur
            t, pos, x, out, st, dac_utc, uf, of, state = item
            feed.write(x)
            if outw:
                outw.write(out)
            if pos == 0:
                if cur:
                    self._emit(logf, cur)
                ps = self.prepared.get(t)
                cur = {"t": t, "dac_utc": dac_utc, "ks": ps.ks if ps else None, "v": 0.0, "s": 0.0,
                       "lim": 0, "miss": 0, "uf": 0, "of": 0, "howl": 0, "state": state}
            if cur:
                cur["v"] += st.voice_sq; cur["s"] += st.stamp_sq; cur["lim"] += st.limited
                cur["miss"] += st.missing; cur["uf"] += uf; cur["of"] += of; cur["howl"] += st.howl

        try:
            while True:
                try:
                    item = self.q.get(timeout=0.2)
                except queue.Empty:
                    item = None
                if item is not None:
                    handle_item(item)
                if self.stopping and self.fade_left == 0:
                    break
                if duration is not None and self.n >= duration * FS and not self.stopping:
                    self.stopping = True
        except KeyboardInterrupt:
            self.stopping = True
            print("\n[roomstamp] fading out...")
            time.sleep(0.25)
        finally:
            gc.enable()
            sys.setswitchinterval(old_switch)
            stream.stop()
            stream.close()
            while not self.q.empty():   # flush what was already played
                handle_item(self.q.get_nowait())
            if cur:
                self._emit(logf, cur)
            self.done.set()
            self.drand.stop()
            feed.close()
            if outw:
                outw.close()
            logf.close()
        print(f"[roomstamp] stopped after {self.n / FS:.1f}s; log {log_path}")
        return log_path

    def _emit(self, logf, c: dict) -> None:
        ks = c["ks"]
        drift_ms = (c["dac_utc"] - c["t"]) * 1000
        rec = {
            "type": "sec", "t": c["t"], "round": ks.round if ks else None, "anchored": bool(ks and ks.anchored),
            "randomness": ks.randomness.hex() if ks and ks.randomness else None,
            "anchor": ks.anchor if ks else None,
            "dac_utc": round(c["dac_utc"], 4), "drift_ms": round(drift_ms, 1),
            "voice_dbfs": round(10 * np.log10(c["v"] / FS + 1e-20), 1),
            "stamp_dbfs": round(10 * np.log10(c["s"] / FS + 1e-20), 1),
            "limited": c["lim"], "missing_blocks": c["miss"], "underflows": c["uf"], "overflows": c["of"],
            "ref_rms": c["state"][0] if c["state"] else None, "gain": c["state"][1] if c["state"] else None,
            "howl": c["howl"],
        }
        logf.write(json.dumps(rec) + "\n")
        logf.flush()
        warn = []
        if not rec["anchored"]:
            warn.append("UNANCHORED")
        if rec["missing_blocks"]:
            warn.append(f"MISSING {rec['missing_blocks']}")
        if rec["underflows"]:
            warn.append(f"UNDERFLOW {rec['underflows']}")
        if rec["howl"]:
            warn.append("HOWL: muted 1 s, gain halved")
        if rec["limited"]:
            warn.append(f"LIMITED {rec['limited']}")
        if self.use_input:
            self.silent_secs = self.silent_secs + 1 if rec["voice_dbfs"] < -90 else 0
            if self.silent_secs >= 3:
                warn.append("MIC SILENT (muted/blocked? the clean feed is empty)")
        if abs(drift_ms) > DRIFT_WARN_MS:
            warn.append("DRIFT")
        if not self.quiet or warn:
            print(f"  t={c['t']} round={rec['round']} voice {rec['voice_dbfs']:6.1f} dBFS  stamp {rec['stamp_dbfs']:6.1f} dBFS  "
                  f"drift {drift_ms:+7.1f} ms {' '.join(warn)}", file=sys.stderr if warn else sys.stdout)
