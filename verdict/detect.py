"""Run the external detector command and validate its detection.json against CONTRACTS.md."""
import json
import os
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

DETECT_TIMEOUT_S = 300
VALID_VERDICTS = ("faithful", "cut", "spliced", "not_found")


class DetectorError(RuntimeError):
    """Raised when the detector command fails or produces an invalid detection.json."""


def _is_num(x: object) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _check_span(span: object, label: str, max_s: float | None = None, allow_zero_len: bool = False) -> tuple[float, float]:
    if not isinstance(span, list) or len(span) != 2 or not _is_num(span[0]) or not _is_num(span[1]):
        raise DetectorError(f"{label} must be a [start_s, end_s] number pair, got {span!r}")
    s, e = float(span[0]), float(span[1])
    if s < 0 or (e < s if allow_zero_len else e <= s):
        raise DetectorError(f"{label} invalid range [{s}, {e}]")
    if max_s is not None and e > max_s + 0.5:
        raise DetectorError(f"{label} [{s}, {e}] exceeds duration {max_s}s")
    return s, e


def _chunk_duration_s(witness_root: Path | None, stream: str | None, seq: int) -> float | None:
    if witness_root is None or not stream:
        return None
    d = witness_root / stream
    for name in (f"{seq:06d}.meta.json", f"{seq:06d}.json"):
        p = d / name
        if p.exists():
            try:
                obj = json.loads(p.read_text())
                dur = obj.get("duration_s")
                if _is_num(dur) and float(dur) > 0:
                    return float(dur)
            except Exception:
                pass
    return None


def validate_detection(data: object, witness_root: Path | str | None = None) -> dict:
    """Validate `data` against CONTRACTS.md; return `data` or raise DetectorError."""
    if not isinstance(data, dict):
        raise DetectorError("detection.json root must be a JSON object")

    wroot = Path(witness_root) if witness_root is not None else None

    for req_key in ("detector", "clip", "stamp", "witness"):
        if req_key not in data:
            raise DetectorError(f"missing top-level field {req_key!r}")

    det = data["detector"]
    if not isinstance(det, dict):
        raise DetectorError("detector must be an object")
    if not isinstance(det.get("name"), str) or not det["name"].strip():
        raise DetectorError("detector.name must be a non-empty string")
    if not isinstance(det.get("version"), str) or not det["version"].strip():
        raise DetectorError("detector.version must be a non-empty string")

    clip = data["clip"]
    if not isinstance(clip, dict):
        raise DetectorError("clip must be an object")
    if not _is_num(clip.get("duration_s")) or float(clip["duration_s"]) <= 0:
        raise DetectorError("clip.duration_s must be a positive number")
    clip_dur = float(clip["duration_s"])
    sha = clip.get("sha256")
    if not isinstance(sha, str) or len(sha) != 64 or any(c not in "0123456789abcdef" for c in sha):
        raise DetectorError("clip.sha256 must be a 64-character lowercase hex string")

    stamp = data["stamp"]
    if stamp is not None:
        if not isinstance(stamp, dict):
            raise DetectorError("stamp must be null or an object")
        if not isinstance(stamp.get("present"), bool):
            raise DetectorError("stamp.present must be a boolean")
        for score_field in ("layer1_score", "layer2_score"):
            sc = stamp.get(score_field)
            if sc is not None and (not _is_num(sc) or not (0.0 <= float(sc) <= 1.0)):
                raise DetectorError(f"stamp.{score_field} must be null or a float in [0, 1]")
        for str_field in ("recorded_at_utc", "venue"):
            val = stamp.get(str_field)
            if val is not None and not isinstance(val, str):
                raise DetectorError(f"stamp.{str_field} must be null or a string")
        cuts = stamp.get("cuts")
        if not isinstance(cuts, list):
            raise DetectorError("stamp.cuts must be a list")
        for idx, c in enumerate(cuts):
            if not isinstance(c, dict):
                raise DetectorError(f"stamp.cuts[{idx}] must be an object")
            if not _is_num(c.get("at_clip_s")) or float(c["at_clip_s"]) < 0:
                raise DetectorError(f"stamp.cuts[{idx}].at_clip_s must be >= 0")
            if not _is_num(c.get("removed_s")) or float(c["removed_s"]) <= 0:
                raise DetectorError(f"stamp.cuts[{idx}].removed_s must be > 0")

    wit = data["witness"]
    if not isinstance(wit, dict):
        raise DetectorError("witness must be an object")
    for k in ("verdict", "stream", "pieces", "removed", "inserted", "transcript_clip"):
        if k not in wit:
            raise DetectorError(f"missing witness.{k}")

    verdict = wit["verdict"]
    if verdict not in VALID_VERDICTS:
        raise DetectorError(f"invalid witness.verdict {verdict!r}; expected one of {VALID_VERDICTS}")

    stream = wit["stream"]
    if verdict == "not_found":
        if stream is not None and not isinstance(stream, str):
            raise DetectorError("witness.stream must be null or string when not_found")
    else:
        if not isinstance(stream, str) or not stream.strip():
            raise DetectorError(f"witness.stream must be a non-empty string when verdict={verdict!r}")

    if not isinstance(wit["transcript_clip"], str):
        raise DetectorError("witness.transcript_clip must be a string")

    pieces = wit["pieces"]
    if not isinstance(pieces, list):
        raise DetectorError("witness.pieces must be a list")
    if verdict != "not_found" and not pieces:
        raise DetectorError(f"witness.pieces cannot be empty when verdict={verdict!r}")

    for i, p in enumerate(pieces):
        if not isinstance(p, dict):
            raise DetectorError(f"witness.pieces[{i}] must be an object")
        _check_span(p.get("clip_s"), f"witness.pieces[{i}].clip_s", max_s=clip_dur)
        seq = p.get("seq")
        if not isinstance(seq, int) or isinstance(seq, bool) or seq < 0:
            raise DetectorError(f"witness.pieces[{i}].seq must be a non-negative int")
        p_stream = p.get("stream") if isinstance(p.get("stream"), str) else stream
        c_dur = _chunk_duration_s(wroot, p_stream, seq)
        _check_span(p.get("src_s"), f"witness.pieces[{i}].src_s", max_s=c_dur)
        aus = p.get("audio_score")
        if aus is not None and (not _is_num(aus) or not (-1.0 <= float(aus) <= 1.0)):
            raise DetectorError(f"witness.pieces[{i}].audio_score must be null or float in [-1, 1]")

    removed = wit["removed"]
    if not isinstance(removed, list):
        raise DetectorError("witness.removed must be a list")
    if verdict == "cut" and not removed:
        raise DetectorError("witness.removed must be non-empty when verdict='cut'")
    if verdict == "faithful" and removed:
        raise DetectorError("witness.removed must be empty when verdict='faithful'")

    for i, r in enumerate(removed):
        if not isinstance(r, dict):
            raise DetectorError(f"witness.removed[{i}] must be an object")
        if not _is_num(r.get("after_clip_s")) or float(r["after_clip_s"]) < 0 or float(r["after_clip_s"]) > clip_dur + 0.5:
            raise DetectorError(f"witness.removed[{i}].after_clip_s out of range")
        seq = r.get("seq")
        if not isinstance(seq, int) or isinstance(seq, bool) or seq < 0:
            raise DetectorError(f"witness.removed[{i}].seq must be a non-negative int")
        c_dur = _chunk_duration_s(wroot, stream, seq)
        _check_span(r.get("src_s"), f"witness.removed[{i}].src_s", max_s=c_dur, allow_zero_len=True)
        if not isinstance(r.get("words"), str):
            raise DetectorError(f"witness.removed[{i}].words must be a string")

    inserted = wit["inserted"]
    if not isinstance(inserted, list):
        raise DetectorError("witness.inserted must be a list")
    for i, ins in enumerate(inserted):
        if not isinstance(ins, dict):
            raise DetectorError(f"witness.inserted[{i}] must be an object")
        _check_span(ins.get("clip_s"), f"witness.inserted[{i}].clip_s", max_s=clip_dur)
        if not isinstance(ins.get("text"), str):
            raise DetectorError(f"witness.inserted[{i}].text must be a string")

    return data


def detector_kind(detection: dict) -> str:
    """Return 'text-only' if the detector is text-only or lacks audio scores, else 'audio+text'."""
    name = detection.get("detector", {}).get("name", "")
    if "text-only" in name:
        return "text-only"
    pieces = detection.get("witness", {}).get("pieces") or []
    if pieces and all(p.get("audio_score") is None for p in pieces):
        return "text-only"
    return "audio+text"


def run_detector(
    clip_wav: Path | str,
    witness_root: Path | str,
    out_path: Path | str | None = None,
    cmd_override: str | None = None,
) -> dict:
    """Execute $WT_DETECT on `clip_wav` against `witness_root`, validate, and return detection dict."""
    wav_p = Path(clip_wav)
    wroot = Path(witness_root)
    if not wav_p.exists():
        raise DetectorError(f"clip wav does not exist: {wav_p}")

    raw_cmd = cmd_override or os.environ.get("WT_DETECT")
    if raw_cmd:
        base_argv = shlex.split(raw_cmd)
    else:
        base_argv = [sys.executable, "-m", "verdict.ref_detect"]

    def _invoke(target_out: Path) -> dict:
        argv = [*base_argv, str(wav_p), "--witness-root", str(wroot), "--out", str(target_out)]
        try:
            res = subprocess.run(argv, capture_output=True, text=True, timeout=DETECT_TIMEOUT_S)
        except subprocess.TimeoutExpired as e:
            raise DetectorError(f"detector timed out after {DETECT_TIMEOUT_S}s") from e
        except OSError as e:
            raise DetectorError(f"failed to start detector {base_argv}: {e}") from e

        if res.returncode != 0:
            err = (res.stderr or res.stdout or "").strip().splitlines()[-1:]
            raise DetectorError(f"detector exited with code {res.returncode}: {err}")
        if not target_out.exists():
            raise DetectorError("detector exited 0 but did not write output file")
        try:
            obj = json.loads(target_out.read_text())
        except Exception as e:
            raise DetectorError(f"detector wrote invalid JSON: {e}") from e
        return validate_detection(obj, witness_root=wroot)

    if out_path is not None:
        out_p = Path(out_path)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        return _invoke(out_p)

    with tempfile.TemporaryDirectory(prefix="wt-detect-") as tmpdir:
        return _invoke(Path(tmpdir) / "detection.json")
