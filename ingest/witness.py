"""Witness a public livestream: cut its audio into fixed chunks, timestamp each, never hide a hole.

    python -m ingest.witness URL --name aljazeera            # runs until Ctrl-C
    python -m ingest.witness some.mp4 --name test --secs 90  # a local file plays at real-time speed (for tests)

Writes into data/witness/<name>/:
    000000.wav + 000000.meta.json   one chunk (16 kHz mono s16), meta written last
    000007.gap.json                 the stream (or the witness) was down from..to

Every seq number is either a chunk or a gap, so "no unacknowledged gaps" is checkable.
The meta file appears only after its wav is complete; downstream workers watch for *.meta.json.

Time claim: received_utc is when the witness held the whole chunk (that is what anchoring pins,
"this audio existed by T"). start/end_utc_est are received minus duration, a guess that is off
while streamlink bursts the HLS backlog right after connecting.
"""
import argparse
import fcntl
import hashlib
import json
import os
import re
import select
import signal
import subprocess
import sys
import threading
import time
import wave
from datetime import datetime, timezone
from pathlib import Path

RATE = 16000
BYTES_PER_S = RATE * 2
STALL_S = 30           # no audio for this long = the stream is dead, reconnect
MIN_TAIL_S = 1.0       # shorter leftovers at a drop are not worth a chunk (they still fall inside the gap)
BACKOFF = [5, 10, 20, 40, 60]
OPTIN_EVERY_S = 3600   # re-check the creator's claim code this often while live
OPTIN_GRACE_S = 7200   # if a re-check can't reach YouTube, a code seen this recently still counts

SKIP_MARKERS = (
    "Sequence gap of",
    "Download of segment",
    "Failed to fetch segment",
    "stream discontinuity",
)

BIN = Path(sys.executable).parent  # streamlink lives in the same venv


def utc(t):
    return datetime.fromtimestamp(t, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def write_atomic(path, data):
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _trim_streamlink_msg(line: str) -> str:
    msg = re.sub(r"^(\[[^\]]+\])+\s*", "", line.strip())
    return msg or line.strip()


def start_source(src):
    """Return (procs, pcm_stdout, skip_holder). A local file is played at real time so it behaves like a live stream."""
    to_pcm = ["-vn", "-ac", "1", "-ar", str(RATE), "-f", "s16le", "-"]
    skip_holder = []
    if Path(src).exists():
        ff = subprocess.Popen(["ffmpeg", "-loglevel", "error", "-re", "-i", src, *to_pcm],
                              stdout=subprocess.PIPE, stdin=subprocess.DEVNULL)
        return [ff], ff.stdout, skip_holder
    sl_bin = os.environ.get("WT_STREAMLINK") or str(BIN / "streamlink")
    sl = subprocess.Popen([sl_bin, "--loglevel", "warning", "--ipv4", "--stdout", src, "worst"],
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL)
    ff = subprocess.Popen(["ffmpeg", "-loglevel", "error", "-i", "-", *to_pcm],
                          stdin=sl.stdout, stdout=subprocess.PIPE)
    sl.stdout.close()  # ffmpeg owns it now; streamlink gets SIGPIPE if ffmpeg dies

    def watch_stderr(proc, err_pipe):
        try:
            for raw in iter(err_pipe.readline, b""):
                text = raw.decode("utf-8", errors="replace").strip()
                if any(m in text for m in SKIP_MARKERS):
                    if not skip_holder:
                        skip_holder.append(f"streamlink: {_trim_streamlink_msg(text)}")
                    if proc.poll() is None:
                        proc.terminate()
        finally:
            try:
                err_pipe.close()
            except Exception:
                pass

    threading.Thread(target=watch_stderr, args=(sl, sl.stderr), daemon=True).start()
    return [sl, ff], ff.stdout, skip_holder


def stop(procs):
    for p in procs:
        if p.poll() is None:
            p.terminate()
    for p in procs:
        try:
            p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            p.kill()


class Witness:
    def __init__(self, src, name, chunk_s, root, optin=True):
        self.src, self.name, self.chunk_s = src, name, chunk_s
        self.optin = optin and not Path(src).exists()  # local test files have no channel to consent
        self.optin_ok_at = None  # monotonic timestamp of when claim code was last seen
        self.revoked = False
        self.fatal = False
        self.consent_lock = threading.Lock()
        self.dir = Path(root) / name
        self.dir.mkdir(parents=True, exist_ok=True)

        self.lock_path = self.dir / "witness.lock"
        self.lock_fd = open(self.lock_path, "a+b")
        try:
            fcntl.flock(self.lock_fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.lock_fd.close()
            self.lock_fd = None
            print(f"ERROR: another witness is already running for '{name}' ({self.lock_path} locked)", file=sys.stderr, flush=True)
            sys.exit(1)

        done = sorted(
            [*self.dir.glob("[0-9]*.meta.json"), *self.dir.glob("[0-9]*.gap.json")],
            key=lambda p: int(p.name.split(".")[0]),
        )
        self.seq = int(done[-1].name.split(".")[0]) + 1 if done else 0
        # restarting after the witness itself was down: that downtime is a gap too
        last = json.loads(done[-1].read_text()) if done else {}
        self.down_since = last.get("received_utc_unix") or last.get("to_utc_unix")
        self.down_reason = "witness offline"
        self.session = 0

    def close(self):
        if getattr(self, "lock_fd", None) is not None:
            try:
                fcntl.flock(self.lock_fd.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass
            self.lock_fd.close()
            self.lock_fd = None

    def consent(self):
        """True = code present, False = code absent (revoked / never claimed), None = couldn't check."""
        from ingest import optin
        with self.consent_lock:
            try:
                r = optin.check(self.src)
            except optin.MissingKeyError as e:
                print(f"FATAL: {e}", file=sys.stderr, flush=True)
                self.fatal = True
                return None
            except Exception as e:
                r = {"ok": None, "error": str(e)[:300], "url": self.src, "checked_utc": optin.utc_now()}
            optin.log_check(self.dir, r)
            if r["ok"]:
                self.optin_ok_at = time.monotonic()
            print(f"opt-in check: {'present' if r['ok'] else 'ABSENT' if r['ok'] is False else 'unreachable'}"
                  + (f" ({r.get('code')} in {r['where']})" if r["ok"] else ""), flush=True)
            return r["ok"]

    def allowed(self):
        """Gate before every connect: never witness a channel that hasn't (or no longer has) the code up."""
        if not self.optin:
            return True
        if self.optin_ok_at is not None and time.monotonic() - self.optin_ok_at < OPTIN_EVERY_S:
            return True
        ok = self.consent()
        if self.fatal:
            return False
        if ok is False:
            self.revoked = True
        return bool(ok) or (ok is None and self.optin_ok_at is not None and time.monotonic() - self.optin_ok_at < OPTIN_GRACE_S)

    def recheck_loop(self, stop_evt):
        while not stop_evt.wait(OPTIN_EVERY_S):
            ok = self.consent()
            if self.fatal:
                return
            if ok is False:
                self.revoked = True
                return

    def emit_gap(self, until):
        if self.down_since is None:
            return
        gap = {"seq": self.seq, "kind": "gap", "stream": self.name, "reason": self.down_reason,
               "from_utc": utc(self.down_since), "to_utc": utc(until), "to_utc_unix": round(until, 3),
               "duration_s": round(until - self.down_since, 3)}
        write_atomic(self.dir / f"{self.seq:06d}.gap.json", json.dumps(gap, indent=1).encode())
        print(f"[{self.seq:06d}] GAP {gap['duration_s']:.1f}s ({self.down_reason})", flush=True)
        self.seq += 1
        self.down_since = None

    def emit_chunk(self, pcm, received):
        dur = len(pcm) / BYTES_PER_S
        base = self.dir / f"{self.seq:06d}"
        tmp = base.with_suffix(".wav.tmp")
        with open(tmp, "wb") as raw_f:
            with wave.open(raw_f, "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(RATE)
                w.writeframes(pcm)
            raw_f.flush()
            os.fsync(raw_f.fileno())
        os.replace(tmp, base.with_suffix(".wav"))
        meta = {"seq": self.seq, "kind": "chunk", "stream": self.name, "source": self.src,
                "session": self.session, "file": base.name + ".wav",
                "sha256": hashlib.sha256(base.with_suffix(".wav").read_bytes()).hexdigest(),
                "samples": len(pcm) // 2, "duration_s": round(dur, 3),
                "received_utc": utc(received), "received_utc_unix": round(received, 3),
                "start_utc_est": utc(received - dur), "end_utc_est": utc(received)}
        write_atomic(base.with_suffix(".meta.json"), json.dumps(meta, indent=1).encode())
        print(f"[{self.seq:06d}] chunk {dur:5.1f}s  received {meta['received_utc']}", flush=True)
        self.seq += 1

    def run_session(self, until_mono=None, stop_evt=None):
        """Capture until the source ends or stalls (or `until_mono` passes). Returns True if any audio arrived."""
        procs, out, skip_holder = start_source(self.src)
        self.session += 1
        want = int(self.chunk_s * BYTES_PER_S)
        buf, got_any = bytearray(), False
        last_mono = time.monotonic()
        last_wall = time.time()
        try:
            while True:
                now_mono = time.monotonic()
                if (stop_evt is not None and stop_evt.is_set()) or (until_mono is not None and now_mono >= until_mono):
                    self.down_reason = "witness stopped"
                    break
                if self.fatal:
                    self.down_reason = "fatal error"
                    break
                if self.revoked:
                    self.down_reason = "creator opted out"
                    break
                if now_mono - last_mono >= STALL_S:
                    self.down_reason = skip_holder[0] if skip_holder else f"stalled >{STALL_S}s"
                    break
                wait = STALL_S - (now_mono - last_mono)
                if until_mono is not None:
                    wait = min(wait, until_mono - now_mono)
                ready, _, _ = select.select([out], [], [], max(0.0, min(wait, 1.0)))  # wake up to notice stop/opt-out
                if not ready:
                    continue
                last_mono = time.monotonic()
                last_wall = time.time()
                block = os.read(out.fileno(), 65536)
                if not block:
                    self.down_reason = skip_holder[0] if skip_holder else "stream ended"
                    break
                if not got_any:
                    got_any = True
                    self.emit_gap(last_wall - len(block) / BYTES_PER_S)
                buf += block
                while len(buf) >= want:
                    self.emit_chunk(bytes(buf[:want]), time.time())
                    del buf[:want]
        finally:
            stop(procs)  # stop ffmpeg before closing its pipe, or it logs broken-pipe errors on every stop
            try:
                out.close()
            except Exception:
                pass
            # the stream died when audio last arrived, not when we noticed (a stall takes STALL_S to notice)
            tail_dur = len(buf) / BYTES_PER_S
            if len(buf) >= MIN_TAIL_S * BYTES_PER_S:
                self.emit_chunk(bytes(buf), last_wall)
                if got_any:
                    self.down_since = last_wall
            else:
                if got_any:
                    # dropped short tail still falls inside the gap
                    self.down_since = last_wall - tail_dur
        return got_any

    def run(self, until=None, stop_evt=None):
        tries = 0
        if stop_evt is None:
            stop_evt = threading.Event()
        until_mono = None
        if until is not None:
            until_mono = time.monotonic() + max(0.0, until - time.time())
        if self.optin:
            threading.Thread(target=self.recheck_loop, args=(stop_evt,), daemon=True).start()
        try:
            while not stop_evt.is_set() and (until_mono is None or time.monotonic() < until_mono):
                if not self.allowed():
                    if self.fatal:
                        return 1
                    if self.revoked:
                        print("claim code not on the channel: not witnessing (add it to the bio, then restart)", flush=True)
                        return 3
                    self.down_reason = "opt-in check unreachable"
                    self.down_since = self.down_since or time.time()
                elif self.run_session(until_mono=until_mono, stop_evt=stop_evt):
                    tries = 0
                else:
                    self.down_since = self.down_since or time.time()
                if self.fatal:
                    return 1
                if self.revoked:
                    print("creator removed the claim code: witnessing stopped", flush=True)
                    return 3
                if stop_evt.is_set() or (until_mono is not None and time.monotonic() >= until_mono):
                    break
                wait = BACKOFF[min(tries, len(BACKOFF) - 1)]
                tries += 1
                print(f"source down ({self.down_reason}), retry in {wait}s", flush=True)
                if until_mono is not None and time.monotonic() + wait >= until_mono:
                    break
                if stop_evt.wait(wait):
                    break
            return 0
        finally:
            stop_evt.set()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("src", help="livestream URL (anything streamlink opens) or a local media file")
    ap.add_argument("--name", required=True, help="stream id, used as the folder name")
    ap.add_argument("--chunk", type=float, default=60, help="chunk length in seconds (default 60)")
    ap.add_argument("--secs", type=float, help="stop after this many seconds (default: forever)")
    ap.add_argument("--root", default="data/witness")
    ap.add_argument("--no-optin", action="store_true", help="skip the claim-code check (our own test streams only)")
    a = ap.parse_args()

    stop_evt = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop_evt.set())
    signal.signal(signal.SIGINT, lambda *_: stop_evt.set())

    w = Witness(a.src, a.name, a.chunk, a.root, optin=not a.no_optin)
    try:
        rc = w.run(until=time.time() + a.secs if a.secs else None, stop_evt=stop_evt)
    finally:
        w.close()
    sys.exit(rc)


if __name__ == "__main__":
    main()
