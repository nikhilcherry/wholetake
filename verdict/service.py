"""HTTP server and single-GPU-worker queue for Wholetake verdicts.

    ./py -m verdict serve [--port 8790]
    ./py -m verdict check <url-or-file> [--out verdict.json]
"""
import hashlib
import json
import os
import queue
import re
import secrets
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable

from ingest.chain import canonical_json, sha256_bytes, utc_now, write_atomic
from verdict.compose import compose_verdict
from verdict.dashboard import ALLOWED_UPLOAD_EXTS, MAX_UPLOAD_BYTES, collect_status_telemetry, render_dashboard_page
from verdict.detect import run_detector
from verdict.fetch import SSRFError, clips_root, fetch, validate_public_url
from verdict.label import label_cuts
from verdict.page import render_verdict_page
from verdict.proofs import build_proofs
from verdict.restore import RestoreUnavailable, build_context_wav, build_restored, edge_context

REPO_ROOT = Path(__file__).resolve().parents[1]


def default_witness_root() -> Path:
    return Path(os.environ.get("WT_WITNESS_ROOT", REPO_ROOT / "data" / "witness"))


def default_verdicts_dir() -> Path:
    return Path(os.environ.get("WT_VERDICTS_DIR", REPO_ROOT / "data" / "verdicts"))


def witness_heads_snapshot(witness_root: Path) -> dict[str, str]:
    """Return {stream: head_hash} across all streams in `witness_root` for cache invalidation."""
    heads: dict[str, str] = {}
    if not witness_root.exists():
        return heads
    for sdir in sorted(witness_root.iterdir()):
        if not sdir.is_dir():
            continue
        hp = sdir / "head.json"
        if hp.exists():
            try:
                obj = json.loads(hp.read_text())
                if isinstance(obj.get("hash"), str):
                    heads[sdir.name] = obj["hash"]
                    continue
            except Exception:
                pass
        cp = sdir / "chain.jsonl"
        if cp.exists():
            try:
                lines = [ln for ln in cp.read_bytes().splitlines() if ln.strip()]
                if lines:
                    heads[sdir.name] = json.loads(lines[-1].decode("utf-8"))["hash"]
            except Exception:
                pass
    return heads


def run_pipeline(
    url_or_path: str,
    witness_root: Path | str | None = None,
    fetch_fn: Callable[[str], dict] = fetch,
    detect_fn: Callable[[Path | str, Path | str], dict] = run_detector,
    label_fn: Callable[[dict, Path | str], dict | None] = label_cuts,
    status_cb: Callable[[str], None] | None = None,
) -> dict:
    """Execute fetch -> detect -> proofs -> label -> compose synchronously and return verdict.json."""
    wroot = Path(witness_root) if witness_root is not None else default_witness_root()
    if status_cb:
        status_cb("fetching")
    clip_info = fetch_fn(url_or_path)

    if status_cb:
        status_cb("detecting")
    detection = detect_fn(clip_info["wav"], wroot)

    proofs = build_proofs(detection, wroot)

    label = None
    if detection.get("witness", {}).get("verdict") in ("cut", "spliced") and detection.get("witness", {}).get("removed"):
        if status_cb:
            status_cb("labeling")
        label = label_fn(detection, wroot)

    return compose_verdict(clip_info, detection, label, proofs, wroot)


def detector_identity() -> str:
    """Which detector a cached result came from: the $WT_DETECT command plus the repo commit.

    The cache keys on the clip and the witness heads; without this, upgrading the detector (text-only ->
    S4 + Roomstamp) would keep serving the old verdicts for every clip seen before."""
    cmd = os.environ.get("WT_DETECT") or "verdict.wt_adapter"
    try:
        import subprocess
        rev = subprocess.run(["git", "rev-parse", "HEAD"], cwd=Path(__file__).resolve().parents[1],
                             capture_output=True, text=True, timeout=5).stdout.strip()
    except Exception:
        rev = ""
    return f"{cmd}@{rev or 'unknown'}"


class VerdictManager:
    """Single-worker queue with persistent job state under `verdicts_dir` and clip+heads caching."""

    def __init__(
        self,
        witness_root: Path | str | None = None,
        verdicts_dir: Path | str | None = None,
        fetch_fn: Callable[[str], dict] = fetch,
        detect_fn: Callable[[Path | str, Path | str], dict] = run_detector,
        label_fn: Callable[[dict, Path | str], dict | None] = label_cuts,
        start_worker: bool = True,
    ):
        self.witness_root = Path(witness_root) if witness_root is not None else default_witness_root()
        self.verdicts_dir = Path(verdicts_dir) if verdicts_dir is not None else default_verdicts_dir()
        self.verdicts_dir.mkdir(parents=True, exist_ok=True)
        self.fetch_fn = fetch_fn
        self.detect_fn = detect_fn
        self.detector_id = detector_identity()
        self.label_fn = label_fn

        self._lock = threading.Lock()
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._jobs: dict[str, dict] = {}
        self._stopped = False

        self._load_existing_jobs()
        self._worker = threading.Thread(target=self._worker_loop, name="verdict-worker", daemon=True)
        if start_worker:
            self._worker.start()

    def _job_path(self, job_id: str) -> Path:
        return self.verdicts_dir / f"{job_id}.json"

    def _save_job(self, job: dict) -> None:
        write_atomic(self._job_path(job["id"]), (json.dumps(job, indent=2, sort_keys=True) + "\n").encode("utf-8"))

    def _load_existing_jobs(self) -> None:
        resumable: list[tuple[str, str]] = []
        for p in sorted(self.verdicts_dir.glob("*.json")):
            try:
                job = json.loads(p.read_text())
            except Exception:
                continue
            if not isinstance(job, dict) or "id" not in job:
                continue
            jid = job["id"]
            self._jobs[jid] = job
            if job.get("status") in ("queued", "fetching", "detecting", "labeling"):
                job["status"] = "queued"
                job["updated_utc"] = utc_now()
                self._save_job(job)
                resumable.append((job.get("created_utc", ""), jid))

        resumable.sort()
        for _created, jid in resumable:
            self._queue.put(jid)

    def _find_cached_result(self, clip_sha256: str, heads: dict[str, str]) -> dict | None:
        with self._lock:
            for job in self._jobs.values():
                if (
                    job.get("status") == "done"
                    and job.get("clip_sha256") == clip_sha256
                    and job.get("witness_heads") == heads
                    and job.get("detector_id") == self.detector_id
                    and isinstance(job.get("result"), dict)
                ):
                    return job["result"]
        return None

    def submit(self, url: str, allow_local: bool = False) -> dict:
        if not isinstance(url, str) or not url.strip():
            raise ValueError("missing or empty 'url'")
        url = url.strip()
        if not allow_local:
            validate_public_url(url)

        now = utc_now()
        job_id = sha256_bytes(f"{url}:{now}:{secrets.token_hex(4)}".encode("utf-8"))[:16]
        job = {
            "id": job_id,
            "url": url,
            "status": "queued",
            "created_utc": now,
            "updated_utc": now,
            "clip_sha256": None,
            "clip_wav": None,
            "witness_heads": None,
            "result": None,
            "error": None,
        }
        with self._lock:
            self._jobs[job_id] = job
            self._save_job(job)
        self._queue.put(job_id)
        return {"id": job_id, "status": job["status"]}

    def get_job(self, job_id: str) -> dict | None:
        if not isinstance(job_id, str) or not re.fullmatch(r"[0-9a-zA-Z_-]{1,64}", job_id):
            return None
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                p = self._job_path(job_id)
                if p.exists():
                    try:
                        job = json.loads(p.read_text())
                        self._jobs[job_id] = job
                    except Exception:
                        return None
            if job is None:
                return None
            return dict(job)

    def resolve_clip_wav(self, job: dict) -> Path | None:
        wav = job.get("clip_wav")
        if isinstance(wav, str) and wav:
            p = Path(wav)
            if p.exists() and p.is_file():
                return p
        url = str(job.get("url") or "").strip()
        if url:
            key = hashlib.sha256(url.encode("utf-8")).hexdigest()
            cand = clips_root() / key / "clip.wav"
            if cand.exists() and cand.is_file():
                return cand
        return None

    def resolve_restored(self, job: dict) -> tuple[Path, dict]:
        """Build or return cached (restored_wav_path, manifest) keyed on verdict_id next to clip.wav."""
        if job.get("status") != "done" or not isinstance(job.get("result"), dict):
            raise RestoreUnavailable("verdict job is not done")
        result = job["result"]
        verdict_id = str(result.get("verdict_id") or job.get("id") or "")
        wit = (result.get("detection") or {}).get("witness") or {}
        if wit.get("verdict") != "cut":
            raise RestoreUnavailable("verdict is not a cut")
        stream = str(wit.get("stream") or "").strip()
        removed = wit.get("removed") or []
        if not stream or not removed:
            raise RestoreUnavailable("no removed spans to restore")

        clip_wav = self.resolve_clip_wav(job)
        if clip_wav is None:
            raise RestoreUnavailable("clip.wav not found")

        if clip_wav.name == "clip.wav" and clip_wav.parent != clips_root():
            cache_dir = clip_wav.parent
        else:
            key = hashlib.sha256(str(job.get("url") or job.get("id") or "").encode("utf-8")).hexdigest()
            cache_dir = clips_root() / key
            cache_dir.mkdir(parents=True, exist_ok=True)

        restored_wav = cache_dir / "restored.wav"
        restored_json = cache_dir / "restored.json"

        with self._lock:
            if restored_wav.exists() and restored_json.exists():
                try:
                    cached_manifest = json.loads(restored_json.read_text())
                    if isinstance(cached_manifest, dict) and cached_manifest.get("verdict_id") == verdict_id:
                        return restored_wav, cached_manifest
                except Exception:
                    pass

            manifest = build_restored(clip_wav, self.witness_root, stream, removed, restored_wav)
            manifest["verdict_id"] = verdict_id
            write_atomic(restored_json, (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8"))
            return restored_wav, manifest

    def resolve_context_wav(self, job: dict, side: str) -> bytes:
        """Build WAV bytes for 'before' or 'after' witness context around the matched clip pieces."""
        if side not in ("before", "after"):
            raise RestoreUnavailable("invalid context side")
        if job.get("status") != "done" or not isinstance(job.get("result"), dict):
            raise RestoreUnavailable("verdict job is not done")
        wit = (job["result"].get("detection") or {}).get("witness") or {}
        stream = str(wit.get("stream") or "").strip()
        pieces = wit.get("pieces") or []
        if not stream or not pieces:
            raise RestoreUnavailable("no matched witness pieces")
        ctx = edge_context(self.witness_root, stream, pieces)
        side_obj = ctx.get(side)
        if not isinstance(side_obj, dict) or not side_obj.get("audio"):
            raise RestoreUnavailable(f"no {side} context available")
        return build_context_wav(self.witness_root, stream, side_obj["audio"])

    def list_jobs(self, limit: int = 20) -> list[dict]:
        with self._lock:
            items = list(self._jobs.values())
        items.sort(key=lambda j: j.get("created_utc") or "", reverse=True)
        return [dict(j) for j in items[: max(1, min(limit, 200))]]

    def _set_status(self, job_id: str, status: str, **extra) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job["status"] = status
            job["updated_utc"] = utc_now()
            job.update(extra)
            self._save_job(job)

    def process_one(self, job_id: str) -> None:
        job = self.get_job(job_id)
        if not job:
            return
        url = job["url"]
        try:
            self._set_status(job_id, "fetching")
            clip_info = self.fetch_fn(url)
            heads = witness_heads_snapshot(self.witness_root)

            cached = self._find_cached_result(clip_info["sha256"], heads)
            if cached is not None:
                self._set_status(
                    job_id,
                    "done",
                    clip_sha256=clip_info["sha256"],
                    clip_wav=str(clip_info.get("wav") or ""),
                    witness_heads=heads,
                    detector_id=self.detector_id,
                    result=cached,
                    error=None,
                )
                return

            self._set_status(
                job_id,
                "detecting",
                clip_sha256=clip_info["sha256"],
                clip_wav=str(clip_info.get("wav") or ""),
                witness_heads=heads,
            )
            detection = self.detect_fn(clip_info["wav"], self.witness_root)
            proofs = build_proofs(detection, self.witness_root)

            label = None
            wit = detection.get("witness") or {}
            if wit.get("verdict") in ("cut", "spliced") and wit.get("removed"):
                self._set_status(job_id, "labeling")
                label = self.label_fn(detection, self.witness_root)

            verdict = compose_verdict(clip_info, detection, label, proofs, self.witness_root)
            self._set_status(
                job_id,
                "done",
                clip_sha256=clip_info["sha256"],
                clip_wav=str(clip_info.get("wav") or ""),
                witness_heads=heads,
                detector_id=self.detector_id,
                result=verdict,
                error=None,
            )
        except Exception as e:
            self._set_status(job_id, "error", error=str(e))

    def _worker_loop(self) -> None:
        while not self._stopped:
            jid = self._queue.get()
            if jid is None:
                self._queue.task_done()
                break
            try:
                self.process_one(jid)
            finally:
                self._queue.task_done()

    def close(self) -> None:
        self._stopped = True
        self._queue.put(None)
        if self._worker.is_alive():
            self._worker.join(timeout=5.0)


def make_handler(manager: VerdictManager):
    class VerdictHTTPHandler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args) -> None:
            return

        def _send_json(self, code: int, obj: object, cors: bool = False) -> None:
            raw = (json.dumps(obj, sort_keys=True) + "\n").encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            if cors:
                self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(raw)

        def _send_html(self, code: int, html_str: str) -> None:
            raw = html_str.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def _send_wav_bytes(self, data: bytes) -> None:
            """Serve WAV bytes with HTTP Range support so browsers allow <audio> seeking."""
            m = re.fullmatch(r"bytes=(\d*)-(\d*)", self.headers.get("Range", "").strip())
            if m and (m.group(1) or m.group(2)):
                n = len(data)
                if m.group(1):
                    start, end = int(m.group(1)), min(int(m.group(2) or n - 1), n - 1)
                else:
                    start, end = max(0, n - int(m.group(2))), n - 1
                if start > end or start >= n:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{n}")
                    self.end_headers()
                    return
                self.send_response(206)
                self.send_header("Content-Range", f"bytes {start}-{end}/{n}")
                data = data[start : end + 1]
            else:
                self.send_response(200)
            self.send_header("Content-Type", "audio/wav")
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:
            parsed = urllib.parse.urlsplit(self.path)
            path = parsed.path.rstrip("/")
            if path in ("", "/dashboard"):
                html_doc = render_dashboard_page(manager)
                self._send_html(200, html_doc)
                return

            if path == "/api/status":
                self._send_json(200, collect_status_telemetry(manager), cors=True)
                return

            if path == "/api/verdicts":
                qs = urllib.parse.parse_qs(parsed.query)
                try:
                    limit = int(qs.get("limit", ["20"])[0])
                except ValueError:
                    limit = 20
                jobs = manager.list_jobs(limit=limit)
                self._send_json(200, {"verdicts": jobs}, cors=True)
                return

            if path.startswith("/api/verdict/"):
                job_id = path.split("/api/verdict/", 1)[1]
                if not job_id or "/" in job_id:
                    self._send_json(404, {"error": "not found"}, cors=True)
                    return
                job = manager.get_job(job_id)
                if job is None:
                    self._send_json(404, {"error": "verdict job not found"}, cors=True)
                    return
                self._send_json(
                    200,
                    {
                        "id": job["id"],
                        "url": job.get("url"),
                        "status": job["status"],
                        "created_utc": job.get("created_utc"),
                        "updated_utc": job.get("updated_utc"),
                        "result": job.get("result"),
                        "error": job.get("error"),
                    },
                    cors=True,
                )
                return

            if path.startswith("/v/"):
                tail = path.split("/v/", 1)[1]
                parts = [p for p in tail.split("/") if p]
                if len(parts) == 1:
                    job = manager.get_job(parts[0])
                    if job is None:
                        self._send_html(404, "<!doctype html><html><body><h1>404 Not Found</h1></body></html>")
                        return
                    if job.get("status") == "done":
                        try:
                            manager.resolve_restored(job)
                        except Exception:
                            pass
                    html_doc = render_verdict_page(job, manager.witness_root)
                    self._send_html(200, html_doc)
                    return

                if len(parts) == 2 and parts[1] == "verdict.json":
                    job = manager.get_job(parts[0])
                    if job is None or not isinstance(job.get("result"), dict):
                        self._send_json(404, {"error": "verdict.json not found"}, cors=True)
                        return
                    self._send_json(200, job["result"], cors=True)
                    return

                if len(parts) == 2 and parts[1] == "clip.wav":
                    job = manager.get_job(parts[0])
                    if job is None:
                        self._send_json(404, {"error": "job not found"}, cors=False)
                        return
                    wav_path = manager.resolve_clip_wav(job)
                    if wav_path is None:
                        self._send_json(404, {"error": "clip.wav not found"}, cors=False)
                        return
                    self._send_wav_bytes(wav_path.read_bytes())
                    return

                if len(parts) == 2 and parts[1] in ("restored.wav", "restored.json"):
                    job = manager.get_job(parts[0])
                    if job is None or job.get("status") != "done":
                        self._send_json(404, {"error": "restored audio not available"}, cors=False)
                        return
                    try:
                        r_wav, r_manifest = manager.resolve_restored(job)
                    except (RestoreUnavailable, ValueError, OSError) as e:
                        self._send_json(404, {"error": str(e)}, cors=False)
                        return
                    if parts[1] == "restored.json":
                        self._send_json(200, r_manifest, cors=True)
                    else:
                        self._send_wav_bytes(r_wav.read_bytes())
                    return

                if len(parts) == 3 and parts[1] == "context" and parts[2] in ("before.wav", "after.wav"):
                    job = manager.get_job(parts[0])
                    if job is None or job.get("status") != "done":
                        self._send_json(404, {"error": "context audio not available"}, cors=False)
                        return
                    side = "before" if parts[2] == "before.wav" else "after"
                    try:
                        wav_bytes = manager.resolve_context_wav(job, side)
                    except (RestoreUnavailable, ValueError, OSError) as e:
                        self._send_json(404, {"error": str(e)}, cors=False)
                        return
                    self._send_wav_bytes(wav_bytes)
                    return

            self._send_json(404, {"error": "not found"}, cors=True)

        def do_POST(self) -> None:
            parsed = urllib.parse.urlsplit(self.path)
            post_path = parsed.path.rstrip("/")
            if post_path == "/api/verdict/upload":
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    length = 0
                if length <= 0:
                    self._send_json(400, {"error": "empty upload body"}, cors=False)
                    return
                if length > MAX_UPLOAD_BYTES:
                    self._send_json(413, {"error": f"upload exceeds {MAX_UPLOAD_BYTES} bytes"}, cors=False)
                    return
                raw_name = self.headers.get("X-Filename") or "upload.mp4"
                base_name = Path(raw_name.replace("\\", "/")).name
                ext = Path(base_name).suffix.lower()
                if ext not in ALLOWED_UPLOAD_EXTS:
                    self._send_json(400, {"error": f"unsupported file extension {ext!r}"}, cors=False)
                    return
                safe_stem = re.sub(r"[^A-Za-z0-9_-]", "_", Path(base_name).stem)[:48] or "clip"
                data = self.rfile.read(length)
                uploads_dir = Path(os.environ.get("WT_UPLOADS_DIR") or (REPO_ROOT / "data" / "uploads"))
                uploads_dir.mkdir(parents=True, exist_ok=True)
                sha16 = sha256_bytes(data)[:16]
                dst_path = (uploads_dir / f"{sha16}_{safe_stem}{ext}").resolve()
                if not dst_path.is_relative_to(uploads_dir.resolve()):
                    self._send_json(400, {"error": "invalid upload filename"}, cors=False)
                    return
                write_atomic(dst_path, data)
                try:
                    res = manager.submit(str(dst_path), allow_local=True)
                except Exception as e:
                    self._send_json(400, {"error": str(e)}, cors=False)
                    return
                self._send_json(202, res, cors=False)
                return

            if post_path != "/api/verdict":
                self._send_json(404, {"error": "not found"}, cors=False)
                return

            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = 0
            if length <= 0 or length > 65536:
                self._send_json(400, {"error": "invalid request body length"}, cors=False)
                return

            try:
                body = json.loads(self.rfile.read(length).decode("utf-8"))
            except Exception:
                self._send_json(400, {"error": "invalid JSON body"}, cors=False)
                return

            if isinstance(body, dict) and isinstance(body.get("preset"), str) and body["preset"].strip():
                demo_dir = Path(os.environ.get("WT_DEMO_CLIPS_DIR") or (REPO_ROOT / "data" / "demo_clips")).resolve()
                preset_name = Path(body["preset"].replace("\\", "/")).name
                cand = (demo_dir / preset_name).resolve()
                if not cand.is_relative_to(demo_dir) or not cand.is_file():
                    self._send_json(404, {"error": f"demo preset not found: {preset_name}"}, cors=False)
                    return
                try:
                    res = manager.submit(str(cand), allow_local=True)
                except Exception as e:
                    self._send_json(400, {"error": str(e)}, cors=False)
                    return
                self._send_json(202, res, cors=False)
                return

            if not isinstance(body, dict) or not isinstance(body.get("url"), str):
                self._send_json(400, {"error": "body must be JSON object with string 'url'"}, cors=False)
                return

            try:
                res = manager.submit(body["url"])
            except SSRFError as e:
                self._send_json(400, {"error": str(e)}, cors=False)
                return
            except Exception as e:
                self._send_json(400, {"error": str(e)}, cors=False)
                return

            self._send_json(202, res, cors=False)

    return VerdictHTTPHandler


def serve(host: str = "127.0.0.1", port: int | None = None, manager: VerdictManager | None = None) -> None:
    actual_port = port if port is not None else int(os.environ.get("WT_VERDICT_PORT", "8790"))
    mgr = manager or VerdictManager()
    httpd = ThreadingHTTPServer((host, actual_port), make_handler(mgr))
    print(f"wholetake-verdict listening on http://{host}:{actual_port}", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
        mgr.close()
