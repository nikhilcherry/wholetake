"""Hourly OpenTimestamps batching and Bitcoin upgrade for EAS-anchored stream heads.

    python -m anchor ots [--root data/witness] [--ots-dir data/anchors/ots]
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from ingest.chain import canonical_json, utc_now, write_atomic

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OTS_DIR = REPO_ROOT / "data" / "anchors" / "ots"
DEFAULT_OTS_LOG = REPO_ROOT / "data" / "anchors" / "ots.jsonl"


def find_ots_bin() -> str:
    venv_ots = REPO_ROOT / ".venv" / "bin" / "ots"
    if venv_ots.exists():
        return str(venv_ots)
    which = shutil.which("ots")
    return which or "ots"


def _latest_eas_heads(witness_root: Path) -> dict[str, list]:
    """Return {stream: [n, head_hash]} for the latest EAS-anchored head in each stream."""
    heads: dict[str, list] = {}
    if not witness_root.exists():
        return heads
    for d in sorted(witness_root.iterdir()):
        if not d.is_dir():
            continue
        anchors_p = d / "anchors.jsonl"
        if not anchors_p.exists():
            continue
        best_n = -1
        best_hash = None
        for line in anchors_p.read_bytes().splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line.decode("utf-8"))
            except Exception:
                continue
            n = rec.get("n")
            h = rec.get("head_hash")
            if isinstance(n, int) and isinstance(h, str) and n > best_n:
                best_n = n
                best_hash = h
        if best_hash is not None:
            heads[d.name] = [best_n, best_hash]
    return heads


def _load_ots_log(log_path: Path) -> list[dict]:
    if not log_path.exists():
        return []
    out = []
    for line in log_path.read_bytes().splitlines():
        if line.strip():
            try:
                out.append(json.loads(line.decode("utf-8")))
            except Exception:
                continue
    return out


def _append_ots_log(log_path: Path, rec: dict):
    log_path.parent.mkdir(parents=True, exist_ok=True)
    line = (canonical_json(rec) + "\n").encode("utf-8")
    with open(log_path, "ab") as f:
        f.write(line)
        f.flush()
        os.fsync(f.fileno())


def _is_upgraded_complete(output: str) -> bool:
    low = output.lower()
    if "pending" in low or "waiting" in low or "not yet" in low:
        return False
    return "complete" in low or "success" in low or "bitcoin block" in low


def run_ots_cycle(
    witness_root: Path | str = REPO_ROOT / "data" / "witness",
    ots_dir: Path | str = DEFAULT_OTS_DIR,
    log_path: Path | str = DEFAULT_OTS_LOG,
    now_dt: datetime | None = None,
    ots_bin: str | None = None,
) -> dict:
    """Stamp latest EAS-anchored heads if changed, and attempt `ots upgrade` on pending .ots files."""
    w_root = Path(witness_root)
    o_dir = Path(ots_dir)
    l_path = Path(log_path)
    binary = ots_bin or find_ots_bin()
    now = now_dt or datetime.now(timezone.utc)

    result = {"stamped": None, "upgraded": [], "errors": []}

    existing = _load_ots_log(l_path)
    last_streams = None
    upgraded_files: set[str] = set()
    stamped_files: list[str] = []

    for rec in existing:
        if "streams" in rec and rec.get("file"):
            last_streams = rec["streams"]
            f_str = rec["file"]
            if f_str not in stamped_files:
                stamped_files.append(f_str)
        if rec.get("upgraded_utc") and rec.get("ots_file"):
            upgraded_files.add(rec["ots_file"])
        elif rec.get("upgraded_utc") and rec.get("file"):
            upgraded_files.add(rec["file"] + ".ots")

    heads = _latest_eas_heads(w_root)
    if heads and heads != last_streams:
        o_dir.mkdir(parents=True, exist_ok=True)
        hour_tag = now.strftime("%Y%m%dT%H") + "Z"
        txt_path = o_dir / f"{hour_tag}.txt"
        suffix = 1
        while txt_path.exists():
            txt_path = o_dir / f"{hour_tag}_{suffix}.txt"
            suffix += 1

        lines = [f"{stream} {n} {h}\n" for stream, (n, h) in sorted(heads.items())]
        content = "".join(lines).encode("utf-8")
        write_atomic(txt_path, content)
        sha256_hex = hashlib.sha256(content).hexdigest()

        try:
            proc = subprocess.run(
                [binary, "stamp", str(txt_path)],
                cwd=str(o_dir),
                capture_output=True,
                text=True,
                timeout=60,
            )
            ots_file = Path(str(txt_path) + ".ots")
            if proc.returncode != 0 and not ots_file.exists():
                err = (proc.stderr or proc.stdout or f"exit {proc.returncode}").strip()
                raise RuntimeError(f"ots stamp failed: {err}")

            rel_file = str(txt_path)
            stamp_rec = {
                "file": rel_file,
                "sha256": sha256_hex,
                "streams": heads,
                "stamped_utc": utc_now(),
            }
            _append_ots_log(l_path, stamp_rec)
            stamped_files.append(rel_file)
            result["stamped"] = stamp_rec
        except Exception as e:
            msg = f"OTS stamp error on {txt_path}: {e}"
            print(f"WARN [ots] {msg}", file=sys.stderr, flush=True)
            result["errors"].append(msg)

    # Discover all .ots files in ots_dir as well as any recorded in ots.jsonl
    candidate_ots: list[Path] = []
    if o_dir.exists():
        candidate_ots.extend(sorted(o_dir.glob("*.ots")))
    for f_str in stamped_files:
        p = Path(f_str + ".ots")
        if p.exists() and p not in candidate_ots:
            candidate_ots.append(p)

    for ots_p in candidate_ots:
        ots_str = str(ots_p)
        if ots_str in upgraded_files:
            continue
        try:
            proc = subprocess.run(
                [binary, "upgrade", ots_str],
                cwd=str(ots_p.parent),
                capture_output=True,
                text=True,
                timeout=60,
            )
            combined = f"{proc.stdout or ''}\n{proc.stderr or ''}"
            if proc.returncode == 0 and _is_upgraded_complete(combined):
                up_rec = {
                    "file": ots_str[:-4] if ots_str.endswith(".ots") else ots_str,
                    "ots_file": ots_str,
                    "upgraded_utc": utc_now(),
                }
                _append_ots_log(l_path, up_rec)
                upgraded_files.add(ots_str)
                result["upgraded"].append(ots_str)
        except Exception as e:
            msg = f"OTS upgrade error on {ots_p}: {e}"
            print(f"WARN [ots] {msg}", file=sys.stderr, flush=True)
            result["errors"].append(msg)

    return result
