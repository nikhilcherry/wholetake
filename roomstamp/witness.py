"""wt-detect (S5) stage for the S4 witness matcher (wholetake.match).

wt_stage(clip_x48k, witness_path) -> {status: present|likely|absent|error, summary,
    edits: [{kind, clip_t, clip_t_range, removed_s, restored_text, precision_s}],
    recorded_at_unix, venue_t_start, venue_t_end, transcript}

`witness_path` is a wholetake record dir, or a witness audio file (its record is built once into
<file>.wtrec/ next to it). venue_t_* are seconds into the witness; recorded_at_unix needs the
record's t_start_unix (python -m wholetake build ... --t-start UNIX).
"""
from __future__ import annotations

import os

import numpy as np
from scipy.signal import resample_poly

KIND = {"deletion": "cut", "pause_trim": "pause_trim", "reorder": "splice", "insertion": "insertion",
        "replacement": "replacement"}


def _record(path: str):
    from wholetake import record
    if os.path.isdir(path):
        return record.load(path)
    rec_dir = path + ".wtrec"
    if os.path.exists(os.path.join(rec_dir, "meta.json")):
        try:
            rec = record.load(rec_dir)
            if rec.meta.get("source_sha256") == record.sha256_file(path):
                return rec
        except Exception:
            pass
    return record.build(path, rec_dir)


def wt_stage(clip_x48k: np.ndarray, witness_path: str) -> dict:
    try:
        from wholetake import match
        rec = _record(witness_path)
        arr = np.nan_to_num(np.asarray(clip_x48k, dtype=np.float64).ravel(), copy=True, nan=0.0, posinf=0.0, neginf=0.0)
        x = resample_poly(arr, 1, 3).astype(np.float32) if len(arr) else np.zeros((0,), dtype=np.float32)
        r = match.match(rec, x)
    except Exception as e:  # the stage must never take down the pipeline
        return {"status": "error", "summary": f"witness matcher failed: {type(e).__name__}: {e}", "edits": [],
                "recorded_at_unix": None, "venue_t_start": None, "venue_t_end": None, "transcript": ""}

    pieces = r["pieces"]
    edits = []
    for g in r["gaps"]:
        kind = KIND.get(g["type"], g["type"])
        c0 = g["clip_at_s"]
        dur = g.get("duration_s")
        c1 = g.get("clip_end_s", c0 + dur if (dur and kind in ("replacement", "insertion")) else c0)
        if dur is None and c1 > c0:
            dur = round(c1 - c0, 3)
        ed = {"kind": kind, "clip_t": c0,
              "clip_t_range": [c0, g.get("clip_end_s", c0)], "removed_s": g.get("removed_s"),
              "restored_text": g.get("restored_text", ""), "precision_s": 0.01}
        if dur is not None:
            ed["duration_s"] = round(float(dur), 3)
        if g.get("source_offset_s") is not None:
            ed["source_offset_s"] = round(float(g["source_offset_s"]), 3)
        edits.append(ed)
    for f in r["foreign_insertions"]:
        c0, c1 = f["clip_start_s"], f["clip_end_s"]
        dur = f.get("duration_s")
        if dur is None:
            dur = c1 - c0
        edits.append({"kind": "foreign", "clip_t": c0,
                      "clip_t_range": [c0, c1], "removed_s": 0.0,
                      "duration_s": round(float(dur), 3),
                      "restored_text": "", "precision_s": 0.2})
    edits.sort(key=lambda e: e["clip_t"])

    if not pieces:
        status, summary = "absent", "Not found in the witness record."
    else:
        covered = sum(p["clip_end_s"] - p["clip_start_s"] for p in pieces)
        strong = covered >= 0.5 * r["clip_duration_s"] and np.median([p["similarity"] for p in pieces]) >= 0.45
        status = "present" if strong else "likely"
        cuts = [e for e in edits if e["kind"] == "cut"]
        cut_desc = "; ".join(f'{e["removed_s"]:.2f} s removed at {e["clip_t"]:.2f} s'
                             + (f' ("{e["restored_text"]}")' if e["restored_text"] else "") for e in cuts)
        pause_trims = [e for e in edits if e["kind"] == "pause_trim"]
        if r["verdict"] == "faithful":
            if pause_trims:
                pt_desc = "; ".join(f'{e["removed_s"]:.2f} s pause trimmed at {e["clip_t"]:.2f} s' for e in pause_trims)
                summary = f"Faithful to the witness record (pause trim: {pt_desc})."
            else:
                summary = "Continuous span of the witness record, nothing removed."
        elif r["verdict"] == "cut":
            summary = cut_desc
        else:
            summary = "Spliced: " + ", ".join(sorted({e["kind"] for e in edits if e["kind"] != "pause_trim"}))
            if cut_desc:
                summary += f" ({cut_desc})"
        if r["decisive_words_removed"]:
            summary += f' — removed: {", ".join(r["decisive_words_removed"])}'
    t0 = rec.meta.get("t_start_unix")
    vs = min(p["witness_start_s"] for p in pieces) if pieces else None
    ve = max(p["witness_end_s"] for p in pieces) if pieces else None
    return {"status": status, "summary": summary, "edits": edits,
            "recorded_at_unix": (t0 + vs) if (t0 is not None and vs is not None) else None,
            "venue_t_start": vs, "venue_t_end": ve, "transcript": r["clip_transcript"],
            "verdict": r["verdict"], "match": r}
