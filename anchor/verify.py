"""Pure verification and lookup functions for anchored stream heads.

    python -m anchor verify data/witness/<stream>
"""
import json
import sys
from pathlib import Path
from typing import Any

from ingest import chain


def anchored_heads(stream_dir: Path | str) -> list[dict]:
    """Load all records from <stream_dir>/anchors.jsonl sorted by ascending `n`."""
    p = Path(stream_dir) / "anchors.jsonl"
    if not p.exists():
        return []
    recs = []
    for line in p.read_bytes().splitlines():
        if line.strip():
            recs.append(json.loads(line.decode("utf-8")))
    recs.sort(key=lambda r: (r.get("n", 0), r.get("onchain_ts", 0)))
    return recs


def best_anchor(stream_dir: Path | str, seq: int) -> dict | None:
    """Return the earliest anchored head covering both chunk `seq` and its transcript, or None."""
    d = Path(stream_dir)
    entries = chain.load(d)
    if not entries:
        return None

    tx_n = None
    for e in entries:
        if e.get("kind") == "transcript" and e.get("seq") == seq:
            tx_n = e.get("n")
            break
    if tx_n is None:
        return None

    for rec in anchored_heads(d):
        rec_n = rec.get("n")
        if isinstance(rec_n, int) and rec_n >= tx_n:
            return rec
    return None


def verify_anchor(stream_dir: Path | str, rec: dict, client: Any) -> tuple[bool, list[str]]:
    """Verify an anchor record against local chain.jsonl and live on-chain EAS getTimestamp."""
    d = Path(stream_dir)
    problems: list[str] = []

    n = rec.get("n")
    head_hash = rec.get("head_hash")
    expected_ts = rec.get("onchain_ts")

    if not isinstance(n, int) or n < 0:
        problems.append(f"invalid n={n!r} in anchor record")
    if not isinstance(head_hash, str) or len(head_hash) != 64:
        problems.append(f"invalid head_hash={head_hash!r} in anchor record")
    if not isinstance(expected_ts, int) or expected_ts <= 0:
        problems.append(f"invalid onchain_ts={expected_ts!r} in anchor record")

    entries = chain.load(d)
    if isinstance(n, int):
        if n >= len(entries):
            problems.append(f"anchor n={n} exceeds chain.jsonl length ({len(entries)})")
        else:
            chain_entry = entries[n]
            if chain_entry.get("n") != n:
                problems.append(f"chain.jsonl entry at index {n} has n={chain_entry.get('n')}")
            if chain_entry.get("hash") != head_hash:
                problems.append(
                    f"n={n}: head_hash mismatch (anchor={head_hash} != chain={chain_entry.get('hash')})"
                )

    if isinstance(head_hash, str) and len(head_hash) == 64:
        try:
            actual_ts = int(client.get_timestamp(head_hash))
            if actual_ts == 0:
                problems.append(f"n={n} hash={head_hash[:12]}...: not timestamped on-chain (getTimestamp=0)")
            elif actual_ts != expected_ts:
                problems.append(
                    f"n={n} hash={head_hash[:12]}...: onchain_ts mismatch (anchor={expected_ts} != chain={actual_ts})"
                )
        except Exception as e:
            problems.append(f"n={n} hash={head_hash[:12]}...: RPC error checking timestamp: {e}")

    return len(problems) == 0, problems


def run_verify_cli(stream_dir: Path | str, client: Any) -> int:
    """Verify every line in anchors.jsonl, print coverage summary, and return exit code (0 or 1)."""
    d = Path(stream_dir)
    recs = anchored_heads(d)
    entries = chain.load(d)

    if not recs:
        print(f"BROKEN: no anchors found in {d / 'anchors.jsonl'}")
        return 1

    all_ok = True
    for rec in recs:
        ok, problems = verify_anchor(d, rec, client)
        if not ok:
            all_ok = False
            for p in problems:
                print(f"BROKEN: {p}")

    chunk_seqs = sorted(e["seq"] for e in entries if e.get("kind") == "chunk" and isinstance(e.get("seq"), int))
    covered = []
    uncovered = []
    for s in chunk_seqs:
        if best_anchor(d, s) is not None:
            covered.append(s)
        else:
            uncovered.append(s)

    print(
        f"Anchors verified: {len(recs)} | "
        f"chunks covered (chunk+transcript anchored): {covered} | "
        f"not yet covered: {uncovered}"
    )
    if all_ok:
        print(f"OK ({len(recs)} anchors verified for {d.name})")
        return 0
    return 1
