"""Build hash-chain and Base Sepolia anchor proofs for every witnessed chunk referenced in a detection."""
from datetime import datetime, timezone
from pathlib import Path

from anchor.verify import best_anchor
from ingest import chain

BASESCAN_TX_PREFIX = "https://sepolia.basescan.org/tx/"


def _ts_to_utc(ts: int | float | None) -> str | None:
    if not isinstance(ts, (int, float)) or ts <= 0:
        return None
    return datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _chunk_received_utc(stream_dir: Path, entries: list[dict], seq: int) -> str | None:
    for e in entries:
        if e.get("kind") == "chunk" and e.get("seq") == seq:
            if isinstance(e.get("received_utc"), str):
                return e["received_utc"]
    for suffix in (f"{seq:06d}.meta.json", f"{seq:06d}.json"):
        p = stream_dir / suffix
        if p.exists():
            try:
                import json

                obj = json.loads(p.read_text())
                if isinstance(obj.get("received_utc"), str):
                    return obj["received_utc"]
            except Exception:
                pass
    return None


def build_proofs(detection: dict, witness_root: Path | str) -> list[dict]:
    """Return per-seq inclusion and anchor verification status for all seqs in `detection`."""
    wit = detection.get("witness") or {}
    default_stream = wit.get("stream")
    if not default_stream:
        return []

    wroot = Path(witness_root)
    referenced: list[tuple[str, int]] = []
    seen: set[tuple[str, int]] = set()

    for item in [*(wit.get("pieces") or []), *(wit.get("removed") or [])]:
        seq = item.get("seq")
        sname = item.get("stream") if isinstance(item.get("stream"), str) else default_stream
        if isinstance(seq, int) and isinstance(sname, str):
            key = (sname, seq)
            if key not in seen:
                seen.add(key)
                referenced.append(key)

    referenced.sort(key=lambda x: (x[0], x[1]))
    results: list[dict] = []

    for sname, seq in referenced:
        sdir = wroot / sname
        entries = chain.load(sdir) if sdir.exists() else []
        received_utc = _chunk_received_utc(sdir, entries, seq) if sdir.exists() else None

        anc = best_anchor(sdir, seq) if sdir.exists() else None
        if anc is None:
            # Even if not yet anchored on-chain, check whether local chain proof to current head is valid
            proof_ok = False
            try:
                prf = chain.proof(sdir, seq)
                proof_ok = chain.check_proof(prf, prf["head_hash"])
            except Exception:
                proof_ok = False
            results.append({
                "seq": seq,
                "chunk_received_utc": received_utc,
                "anchor": {"status": "pending"},
                "proof_ok": proof_ok,
            })
            continue

        head_n = anc.get("n")
        head_hash = anc.get("head_hash")
        proof_ok = False
        if isinstance(head_n, int) and isinstance(head_hash, str):
            try:
                prf = chain.proof(sdir, seq, head_n=head_n)
                proof_ok = chain.check_proof(prf, head_hash)
            except Exception:
                proof_ok = False

        tx_hash = anc.get("tx_hash") or anc.get("tx") or ""
        if isinstance(tx_hash, str) and tx_hash and not tx_hash.startswith("0x"):
            tx_hash = "0x" + tx_hash

        onchain_utc = anc.get("onchain_utc") or _ts_to_utc(anc.get("onchain_ts"))
        anchor_info = {
            "chain_id": anc.get("chain_id", 84532),
            "tx": tx_hash,
            "block": anc.get("block_number") if "block_number" in anc else anc.get("block"),
            "onchain_utc": onchain_utc,
            "head_n": head_n,
            "head_hash": head_hash,
            "explorer": f"{BASESCAN_TX_PREFIX}{tx_hash}" if tx_hash else None,
        }
        results.append({
            "seq": seq,
            "chunk_received_utc": received_utc,
            "anchor": anchor_info,
            "proof_ok": proof_ok,
        })

    return results
