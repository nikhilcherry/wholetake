"""Append-only, per-stream hash chain of witnessed chunks, gaps, and transcripts.

    python -m ingest.chain [--root data/witness]
    python -m ingest.chain --verify data/witness/<name>

Watches every stream directory under --root and maintains <dir>/chain.jsonl and <dir>/head.json.
Chunks and gaps are chained in strict contiguous seq order (0, 1, 2, ...). Transcripts may arrive
minutes later and out of order, and are chained once their corresponding chunk is already in the log.
"""
import argparse
import fcntl
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ZERO_HASH = "0" * 64
POLL_S = 1.0


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def write_atomic(path: Path, data: bytes):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def canonical_json(obj: dict) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def entry_hash(e: dict) -> str:
    """Compute sha256 hex of the canonical JSON of entry `e` without its `hash` field."""
    body = {k: v for k, v in e.items() if k != "hash"}
    return sha256_bytes(canonical_json(body).encode("utf-8"))


def load(stream_dir: Path | str) -> list[dict]:
    """Load all complete JSON entries from <stream_dir>/chain.jsonl."""
    p = Path(stream_dir) / "chain.jsonl"
    if not p.exists():
        return []
    entries = []
    for line in p.read_bytes().splitlines():
        if line.strip():
            entries.append(json.loads(line.decode("utf-8")))
    return entries


def _repair_and_load(chain_path: Path) -> list[dict]:
    """Load existing log; if the last line is a partial write (no newline or bad JSON), truncate it."""
    if not chain_path.exists():
        return []
    raw = chain_path.read_bytes()
    if not raw:
        return []

    lines = raw.splitlines(keepends=True)
    valid_bytes = 0
    entries = []
    for i, line in enumerate(lines):
        is_last = i == len(lines) - 1
        if not line.endswith(b"\n"):
            if is_last:
                print(f"warning: truncating partial last line (no newline) in {chain_path}", file=sys.stderr, flush=True)
                break
            raise ValueError(f"line {i} missing newline in {chain_path}")
        try:
            obj = json.loads(line.decode("utf-8"))
            if not isinstance(obj, dict) or "hash" not in obj:
                raise ValueError("missing hash field")
        except Exception as e:
            if is_last:
                print(f"warning: truncating corrupted last line in {chain_path}: {e}", file=sys.stderr, flush=True)
                break
            raise ValueError(f"corrupted line n={i} in {chain_path}: {e}") from e
        entries.append(obj)
        valid_bytes += len(line)

    if valid_bytes < len(raw):
        with open(chain_path, "r+b") as f:
            f.truncate(valid_bytes)
            f.flush()
            os.fsync(f.fileno())
    return entries


class StreamAppender:
    """Single-process owner of a stream's chain.jsonl and head.json."""

    def __init__(self, stream_dir: Path):
        self.dir = Path(stream_dir)
        self.stream = self.dir.name
        self.chain_path = self.dir / "chain.jsonl"
        self.head_path = self.dir / "head.json"
        self.lock_path = self.dir / "chain.lock"
        self.halted = False

        self.lock_fd = open(self.lock_path, "a+b")
        try:
            fcntl.flock(self.lock_fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.lock_fd.close()
            self.lock_fd = None
            raise BlockingIOError(f"chain.lock already held for {self.dir}")

        try:
            self.entries: list[dict] = []
            self.counts = {"chunk": 0, "gap": 0, "transcript": 0}
            self.last_seq = -1
            self.chunk_n_by_seq: dict[int, int] = {}
            self.chunk_wav_sha_by_seq: dict[int, str] = {}
            self.transcribed_seqs: set[int] = set()
            self._load_state()
        except Exception:
            self.close()
            raise

    def close(self):
        if getattr(self, "lock_fd", None) is not None:
            try:
                fcntl.flock(self.lock_fd.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass
            self.lock_fd.close()
            self.lock_fd = None

    def _load_state(self):
        loaded = _repair_and_load(self.chain_path)
        for e in loaded:
            self._apply_state(e)
        if self.entries:
            self._write_head()

    def _apply_state(self, e: dict):
        self.entries.append(e)
        kind = e["kind"]
        if kind in self.counts:
            self.counts[kind] += 1
        if kind in ("chunk", "gap"):
            seq = e["seq"]
            self.last_seq = seq
            if kind == "chunk":
                self.chunk_n_by_seq[seq] = e["n"]
                self.chunk_wav_sha_by_seq[seq] = e["wav_sha256"]
        elif kind == "transcript":
            self.transcribed_seqs.add(e["seq"])

    def _append_entry(self, body: dict) -> dict:
        n = len(self.entries)
        prev = self.entries[-1]["hash"] if self.entries else ZERO_HASH
        entry = {"n": n, "prev": prev, **body}
        entry["hash"] = entry_hash(entry)
        line = (canonical_json(entry) + "\n").encode("utf-8")

        with open(self.chain_path, "ab") as f:
            f.write(line)
            f.flush()
            os.fsync(f.fileno())

        self._apply_state(entry)
        self._write_head()
        return entry

    def _write_head(self):
        if not self.entries:
            return
        last = self.entries[-1]
        head = {
            "stream": self.stream,
            "n": last["n"],
            "hash": last["hash"],
            "kinds": dict(self.counts),
            "last_seq": self.last_seq,
            "updated_utc": utc_now(),
        }
        write_atomic(self.head_path, (canonical_json(head) + "\n").encode("utf-8"))

    def _ensure_genesis(self):
        if self.entries:
            return
        source = None
        meta0 = self.dir / "000000.meta.json"
        if meta0.exists():
            try:
                source = json.loads(meta0.read_text()).get("source")
            except Exception:
                source = None
        self._append_entry({
            "kind": "genesis",
            "stream": self.stream,
            "source": source,
            "created_utc": utc_now(),
        })

    def step(self) -> int:
        """Scan stream directory and append any ready items. Returns number of entries appended."""
        if self.halted:
            return 0

        before = len(self.entries)

        # 1. Advance contiguous chunk / gap sequence
        while not self.halted:
            next_seq = self.last_seq + 1
            meta_p = self.dir / f"{next_seq:06d}.meta.json"
            gap_p = self.dir / f"{next_seq:06d}.gap.json"

            if meta_p.exists():
                wav_p = self.dir / f"{next_seq:06d}.wav"
                if not wav_p.exists():
                    print(f"ERROR [{self.stream}] missing wav for {meta_p.name}; halting stream", file=sys.stderr, flush=True)
                    self.halted = True
                    break
                try:
                    meta_bytes = meta_p.read_bytes()
                    meta = json.loads(meta_bytes.decode("utf-8"))
                    wav_bytes = wav_p.read_bytes()
                except Exception as e:
                    print(f"ERROR [{self.stream}] reading seq {next_seq:06d}: {e}; halting stream", file=sys.stderr, flush=True)
                    self.halted = True
                    break

                wav_sha = sha256_bytes(wav_bytes)
                if wav_sha != meta.get("sha256"):
                    print(
                        f"ERROR [{self.stream}] seq {next_seq:06d} wav sha256 mismatch: "
                        f"file={wav_sha} != meta={meta.get('sha256')}; halting stream",
                        file=sys.stderr,
                        flush=True,
                    )
                    self.halted = True
                    break

                self._ensure_genesis()
                self._append_entry({
                    "kind": "chunk",
                    "seq": next_seq,
                    "wav_sha256": wav_sha,
                    "meta_sha256": sha256_bytes(meta_bytes),
                    "received_utc": meta["received_utc"],
                    "duration_s": meta["duration_s"],
                })

            elif gap_p.exists():
                try:
                    gap_bytes = gap_p.read_bytes()
                    gap = json.loads(gap_bytes.decode("utf-8"))
                except Exception as e:
                    print(f"ERROR [{self.stream}] reading gap {next_seq:06d}: {e}; halting stream", file=sys.stderr, flush=True)
                    self.halted = True
                    break

                self._ensure_genesis()
                self._append_entry({
                    "kind": "gap",
                    "seq": next_seq,
                    "gap_sha256": sha256_bytes(gap_bytes),
                    "from_utc": gap["from_utc"],
                    "to_utc": gap["to_utc"],
                })
            else:
                break

        if self.halted:
            return len(self.entries) - before

        # 2. Append transcripts whose chunk is already chained
        for seq in sorted(self.chunk_n_by_seq):
            if seq in self.transcribed_seqs:
                continue
            tx_p = self.dir / f"{seq:06d}.json"
            if not tx_p.exists():
                continue
            mel_p = self.dir / f"{seq:06d}.mel.npy"
            if not mel_p.exists():
                print(f"ERROR [{self.stream}] missing mel for transcript {seq:06d}; halting stream", file=sys.stderr, flush=True)
                self.halted = True
                break
            try:
                tx_bytes = tx_p.read_bytes()
                tx = json.loads(tx_bytes.decode("utf-8"))
                mel_bytes = mel_p.read_bytes()
            except Exception as e:
                print(f"ERROR [{self.stream}] reading transcript {seq:06d}: {e}; halting stream", file=sys.stderr, flush=True)
                self.halted = True
                break

            expected_wav_sha = self.chunk_wav_sha_by_seq[seq]
            if tx.get("wav_sha256") != expected_wav_sha:
                print(
                    f"ERROR [{self.stream}] seq {seq:06d} transcript wav_sha256 mismatch: "
                    f"tx={tx.get('wav_sha256')} != chunk={expected_wav_sha}; halting stream",
                    file=sys.stderr,
                    flush=True,
                )
                self.halted = True
                break

            mel_sha = sha256_bytes(mel_bytes)
            if mel_sha != tx.get("mel_sha256"):
                print(
                    f"ERROR [{self.stream}] seq {seq:06d} mel sha256 mismatch: "
                    f"file={mel_sha} != json={tx.get('mel_sha256')}; halting stream",
                    file=sys.stderr,
                    flush=True,
                )
                self.halted = True
                break

            self._append_entry({
                "kind": "transcript",
                "seq": seq,
                "json_sha256": sha256_bytes(tx_bytes),
                "mel_sha256": mel_sha,
                "chunk_n": self.chunk_n_by_seq[seq],
            })

        return len(self.entries) - before


def verify(stream_dir: Path | str, check_files: bool = True) -> tuple[bool, list[str]]:
    """Verify the entire chain and (optionally) all referenced files on disk."""
    d = Path(stream_dir)
    chain_p = d / "chain.jsonl"
    problems: list[str] = []

    if not chain_p.exists():
        return False, ["chain.jsonl does not exist"]

    raw_lines = chain_p.read_bytes().splitlines(keepends=True)
    if not raw_lines:
        return False, ["chain.jsonl is empty"]

    entries: list[dict] = []
    for idx, raw_line in enumerate(raw_lines):
        if not raw_line.endswith(b"\n"):
            problems.append(f"n={idx}: line missing trailing newline")
        try:
            e = json.loads(raw_line.decode("utf-8"))
        except Exception as exc:
            problems.append(f"n={idx}: invalid JSON ({exc})")
            return False, problems
        entries.append(e)

    expected_seq = 0
    chunks_by_seq: dict[int, int] = {}
    chunk_wav_sha_by_seq: dict[int, str] = {}
    transcripts_by_seq: set[int] = set()

    for idx, e in enumerate(entries):
        n = e.get("n")
        seq = e.get("seq")
        tag = f"n={idx}" + (f" (entry n={n})" if n != idx else "") + (f" seq={seq}" if seq is not None else "")

        if n != idx:
            problems.append(f"{tag}: expected n={idx}, got n={n}")

        expected_prev = ZERO_HASH if idx == 0 else entries[idx - 1].get("hash")
        if e.get("prev") != expected_prev:
            problems.append(f"{tag}: prev link mismatch (expected {expected_prev}, got {e.get('prev')})")

        recomputed = entry_hash(e)
        if e.get("hash") != recomputed:
            problems.append(f"{tag}: entry hash mismatch (expected {recomputed}, got {e.get('hash')})")

        kind = e.get("kind")
        if idx == 0:
            if kind != "genesis":
                problems.append(f"{tag}: first entry must be genesis, got {kind}")
            continue
        elif kind == "genesis":
            problems.append(f"{tag}: unexpected genesis entry after n=0")
            continue

        if kind == "chunk":
            if seq != expected_seq:
                problems.append(f"{tag}: out-of-order or skipped seq (expected seq={expected_seq}, got seq={seq})")
            if isinstance(seq, int):
                expected_seq = seq + 1
                chunks_by_seq[seq] = idx
                chunk_wav_sha_by_seq[seq] = e.get("wav_sha256", "")
            if check_files and isinstance(seq, int):
                wav_p = d / f"{seq:06d}.wav"
                meta_p = d / f"{seq:06d}.meta.json"
                if not wav_p.exists():
                    problems.append(f"{tag}: missing file {wav_p.name}")
                else:
                    actual_wav = sha256_file(wav_p)
                    if actual_wav != e.get("wav_sha256"):
                        problems.append(f"{tag}: wav_sha256 mismatch on {wav_p.name}")
                if not meta_p.exists():
                    problems.append(f"{tag}: missing file {meta_p.name}")
                else:
                    meta_bytes = meta_p.read_bytes()
                    actual_meta = sha256_bytes(meta_bytes)
                    if actual_meta != e.get("meta_sha256"):
                        problems.append(f"{tag}: meta_sha256 mismatch on {meta_p.name}")
                    try:
                        meta_obj = json.loads(meta_bytes.decode("utf-8"))
                        if meta_obj.get("sha256") != e.get("wav_sha256"):
                            problems.append(f"{tag}: meta.json sha256 does not match wav_sha256")
                        if meta_obj.get("received_utc") != e.get("received_utc"):
                            problems.append(f"{tag}: received_utc does not match meta.json")
                    except Exception as exc:
                        problems.append(f"{tag}: invalid JSON in {meta_p.name} ({exc})")

        elif kind == "gap":
            if seq != expected_seq:
                problems.append(f"{tag}: out-of-order or skipped seq (expected seq={expected_seq}, got seq={seq})")
            if isinstance(seq, int):
                expected_seq = seq + 1
            if check_files and isinstance(seq, int):
                gap_p = d / f"{seq:06d}.gap.json"
                if not gap_p.exists():
                    problems.append(f"{tag}: missing file {gap_p.name}")
                else:
                    actual_gap = sha256_file(gap_p)
                    if actual_gap != e.get("gap_sha256"):
                        problems.append(f"{tag}: gap_sha256 mismatch on {gap_p.name}")

        elif kind == "transcript":
            if seq not in chunks_by_seq:
                problems.append(f"{tag}: transcript for seq={seq} appears before its chunk entry")
            else:
                if e.get("chunk_n") != chunks_by_seq[seq]:
                    problems.append(f"{tag}: chunk_n={e.get('chunk_n')} does not match chunk n={chunks_by_seq[seq]}")
            if seq in transcripts_by_seq:
                problems.append(f"{tag}: duplicate transcript for seq={seq}")
            if isinstance(seq, int):
                transcripts_by_seq.add(seq)
            if check_files and isinstance(seq, int):
                tx_p = d / f"{seq:06d}.json"
                mel_p = d / f"{seq:06d}.mel.npy"
                if not tx_p.exists():
                    problems.append(f"{tag}: missing file {tx_p.name}")
                else:
                    tx_bytes = tx_p.read_bytes()
                    actual_tx = sha256_bytes(tx_bytes)
                    if actual_tx != e.get("json_sha256"):
                        problems.append(f"{tag}: json_sha256 mismatch on {tx_p.name}")
                    try:
                        tx_obj = json.loads(tx_bytes.decode("utf-8"))
                        if tx_obj.get("mel_sha256") != e.get("mel_sha256"):
                            problems.append(f"{tag}: transcript record mel_sha256 mismatch")
                        if seq in chunk_wav_sha_by_seq and tx_obj.get("wav_sha256") != chunk_wav_sha_by_seq[seq]:
                            problems.append(f"{tag}: transcript wav_sha256 does not match chunk wav_sha256")
                    except Exception as exc:
                        problems.append(f"{tag}: invalid JSON in {tx_p.name} ({exc})")
                if not mel_p.exists():
                    problems.append(f"{tag}: missing file {mel_p.name}")
                else:
                    actual_mel = sha256_file(mel_p)
                    if actual_mel != e.get("mel_sha256"):
                        problems.append(f"{tag}: mel_sha256 mismatch on {mel_p.name}")
        else:
            problems.append(f"{tag}: unknown kind {kind!r}")

    return len(problems) == 0, problems


def proof(stream_dir: Path | str, seq: int, head_n: int | None = None) -> dict:
    """Return a hash-chain inclusion proof from the chunk entry for `seq` up to `head_n` (default: current head)."""
    d = Path(stream_dir)
    entries = load(d)
    if not entries:
        raise ValueError(f"no chain entries in {d}")

    start_idx = None
    for idx, e in enumerate(entries):
        if e.get("kind") == "chunk" and e.get("seq") == seq:
            start_idx = idx
            break
    if start_idx is None:
        raise KeyError(f"chunk seq={seq} not found in chain")

    end_idx = len(entries) - 1 if head_n is None else head_n
    if end_idx < start_idx or end_idx >= len(entries):
        raise ValueError(f"invalid head_n={head_n} for chunk at n={start_idx} (log length={len(entries)})")

    sliced = entries[start_idx : end_idx + 1]
    return {
        "stream": d.name,
        "seq": seq,
        "chunk_n": entries[start_idx]["n"],
        "head_n": sliced[-1]["n"],
        "head_hash": sliced[-1]["hash"],
        "entries": sliced,
    }


def check_proof(prf: dict, head_hash: str) -> bool:
    """Verify that a proof starts with the claimed chunk entry and links intact to `head_hash`."""
    try:
        entries = prf["entries"]
        seq = prf["seq"]
        if not isinstance(entries, list) or not entries:
            return False
        first = entries[0]
        if first.get("kind") != "chunk" or first.get("seq") != seq:
            return False
        if prf.get("chunk_n") is not None and first.get("n") != prf["chunk_n"]:
            return False
        if prf.get("head_n") is not None and entries[-1].get("n") != prf["head_n"]:
            return False
        for i, e in enumerate(entries):
            if not isinstance(e, dict):
                return False
            if e.get("hash") != entry_hash(e):
                return False
            if i > 0:
                if e.get("n") != entries[i - 1].get("n") + 1:
                    return False
                if e.get("prev") != entries[i - 1].get("hash"):
                    return False
        return entries[-1].get("hash") == head_hash
    except Exception:
        return False


class ChainDaemon:
    """Manages StreamAppenders across all stream directories under a root directory."""

    def __init__(self, root: Path | str):
        self.root = Path(root)
        self.appenders: dict[Path, StreamAppender] = {}
        self.failed_dirs: set[Path] = set()

    def step(self) -> int:
        if not self.root.exists():
            return 0
        total = 0
        for p in sorted(self.root.iterdir()):
            if not p.is_dir() or p in self.failed_dirs:
                continue
            if p not in self.appenders:
                has_items = (
                    (p / "chain.jsonl").exists()
                    or any(p.glob("[0-9]*.meta.json"))
                    or any(p.glob("[0-9]*.gap.json"))
                )
                if not has_items:
                    continue
                try:
                    self.appenders[p] = StreamAppender(p)
                except Exception as e:
                    print(f"ERROR [{p.name}] failed to initialize stream appender: {e}", file=sys.stderr, flush=True)
                    self.failed_dirs.add(p)
                    continue
            app = self.appenders[p]
            if app.halted:
                continue
            try:
                total += app.step()
            except Exception as e:
                print(f"ERROR [{p.name}] unexpected error in step: {e}; halting stream", file=sys.stderr, flush=True)
                app.halted = True
        return total

    def close(self):
        for app in self.appenders.values():
            app.close()
        self.appenders.clear()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default="data/witness", help="witness data root directory")
    ap.add_argument("--verify", metavar="STREAM_DIR", help="verify a single stream directory and exit")
    a = ap.parse_args()

    if a.verify:
        ok, problems = verify(Path(a.verify), check_files=True)
        if ok:
            entries = load(a.verify)
            print(f"OK ({len(entries)} entries, head={entries[-1]['hash']})")
            sys.exit(0)
        else:
            for p in problems:
                print(f"BROKEN: {p}")
            sys.exit(1)

    daemon = ChainDaemon(Path(a.root))
    try:
        while True:
            daemon.step()
            time.sleep(POLL_S)
    except KeyboardInterrupt:
        pass
    finally:
        daemon.close()


if __name__ == "__main__":
    main()
