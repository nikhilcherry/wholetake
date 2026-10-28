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
import hashlib
import json
import os
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

BIN = Path(sys.executable).parent  # streamlink lives in the same venv


def utc(t):
    return datetime.fromtimestamp(t, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def write_atomic(path, data):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def start_source(src):
    """Return (procs, pcm_stdout). A local file is played at real time so it behaves like a live stream."""
    to_pcm = ["-vn", "-ac", "1", "-ar", str(RATE), "-f", "s16le", "-"]
    if Path(src).exists():
        ff = subprocess.Popen(["ffmpeg", "-loglevel", "error", "-re", "-i", src, *to_pcm],
                              stdout=subprocess.PIPE, stdin=subprocess.DEVNULL)
        return [ff], ff.stdout
    sl = subprocess.Popen([str(BIN / "streamlink"), "--quiet", "--ipv4", "--stdout", src, "worst"],
                          stdout=subprocess.PIPE, stdin=subprocess.DEVNULL)
    ff = subprocess.Popen(["ffmpeg", "-loglevel", "error", "-i", "-", *to_pcm],
                          stdin=sl.stdout, stdout=subprocess.PIPE)
    sl.stdout.close()  # ffmpeg owns it now; streamlink gets SIGPIPE if ffmpeg dies
    return [sl, ff], ff.stdout


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
        self.optin_ok_at = None  # last time the claim code was seen
        self.revoked = False
        self.dir = Path(root) / name
        self.dir.mkdir(parents=True, exist_ok=True)
        done = sorted(self.dir.glob("[0-9]*.json"))
        self.seq = int(done[-1].name[:6]) + 1 if done else 0
        # restarting after the witness itself was down: that downtime is a gap too
        last = json.loads(done[-1].read_text()) if done else {}
        self.down_since = last.get("received_utc_unix") or last.get("to_utc_unix")
        self.down_reason = "witness offline"
        self.session = 0

    def consent(self):
        """True = code present, False = code absent (revoked / never claimed), None = couldn't check."""
        from ingest import optin
        try:
            r = optin.check(self.src)
        except Exception as e:
            r = {"ok": None, "error": str(e)[:300], "url": self.src, "checked_utc": optin.utc_now()}
        optin.log_check(self.dir, r)
        if r["ok"]:
            self.optin_ok_at = time.time()
        print(f"opt-in check: {'present' if r['ok'] else 'ABSENT' if r['ok'] is False else 'unreachable'}"
              + (f" ({r.get('code')} in {r['where']})" if r["ok"] else ""), flush=True)
        return r["ok"]

    def allowed(self):
        """Gate before every connect: never witness a channel that hasn't (or no longer has) the code up."""
        if not self.optin:
            return True
        if self.optin_ok_at and time.time() - self.optin_ok_at < OPTIN_EVERY_S:
            return True
        ok = self.consent()
        if ok is False:
            self.revoked = True
        return bool(ok) or (ok is None and self.optin_ok_at is not None and time.time() - self.optin_ok_at < OPTIN_GRACE_S)

    def recheck_loop(self, stop_evt):
        while not stop_evt.wait(OPTIN_EVERY_S):
            if self.consent() is False:
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
        with wave.open(str(tmp), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(RATE)
            w.writeframes(pcm)
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

    def run_session(self, until=None):
        """Capture until the source ends or stalls (or `until` passes). Returns True if any audio arrived."""
        procs, out = start_source(self.src)
        self.session += 1
        want = int(self.chunk_s * BYTES_PER_S)
        buf, got_any = bytearray(), False
        last = time.time()
        try:
            while True:
                now = time.time()
                if until is not None and now >= until:
                    self.down_reason = "witness stopped"
                    break
                if self.revoked:
                    self.down_reason = "creator opted out"
                    break
                if now - last >= STALL_S:
                    self.down_reason = f"stalled >{STALL_S}s"
                    break
                wait = STALL_S - (now - last) if until is None else min(STALL_S - (now - last), until - now)
                ready, _, _ = select.select([out], [], [], min(wait, 5))  # wake up to notice an opt-out
                if not ready:
                    continue
                last = time.time()
                block = os.read(out.fileno(), 65536)
                if not block:
                    self.down_reason = "stream ended"
                    break
                if not got_any:
                    got_any = True
                    self.emit_gap(time.time() - len(block) / BYTES_PER_S)
                buf += block
                while len(buf) >= want:
                    self.emit_chunk(bytes(buf[:want]), time.time())
                    del buf[:want]
        finally:
            stop(procs)
            # the stream died when audio last arrived, not when we noticed (a stall takes STALL_S to notice)
            if len(buf) >= MIN_TAIL_S * BYTES_PER_S:
                self.emit_chunk(bytes(buf), last)
            if got_any:
                self.down_since = last
        return got_any

    def run(self, until=None):
        tries = 0
        stop_evt = threading.Event()
        if self.optin:
            threading.Thread(target=self.recheck_loop, args=(stop_evt,), daemon=True).start()
        while until is None or time.time() < until:
            if not self.allowed():
                if self.revoked:
                    print("claim code not on the channel: not witnessing (add it to the bio, then restart)", flush=True)
                    break
                self.down_reason = "opt-in check unreachable"
                self.down_since = self.down_since or time.time()
            elif self.run_session(until):
                tries = 0
            else:
                self.down_since = self.down_since or time.time()
            if self.revoked:
                print("creator removed the claim code: witnessing stopped", flush=True)
                break
            if until is not None and time.time() >= until:
                break
            wait = BACKOFF[min(tries, len(BACKOFF) - 1)]
            tries += 1
            print(f"source down ({self.down_reason}), retry in {wait}s", flush=True)
            if until is not None and time.time() + wait >= until:
                break
            time.sleep(wait)
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

    w = Witness(a.src, a.name, a.chunk, a.root, optin=not a.no_optin)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))  # turns into SystemExit, so finally blocks flush
    try:
        w.run(until=time.time() + a.secs if a.secs else None)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
