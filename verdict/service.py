"""HTTP server and single-GPU-worker queue for Wholetake verdicts.

    ./py -m verdict serve [--port 8790]
    ./py -m verdict check <url-or-file> [--out verdict.json]
"""
import hashlib
import json
import os
import queue
import secrets
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable

from ingest.chain import canonical_json, sha256_bytes, utc_now, write_atomic
from verdict.compose import compose_verdict
from verdict.detect import run_detector
from verdict.fetch import SSRFError, clips_root, fetch, validate_public_url
from verdict.label import label_cuts
from verdict.page import render_verdict_page
from verdict.proofs import build_proofs

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
                    and isinstance(job.get("result"), dict)
                ):
                    return job["result"]
        return None

    def submit(self, url: str) -> dict:
        if not isinstance(url, str) or not url.strip():
            raise ValueError("missing or empty 'url'")
        url = url.strip()
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

        def do_GET(self) -> None:
            parsed = urllib.parse.urlsplit(self.path)
            path = parsed.path.rstrip("/")
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
                    data = wav_path.read_bytes()
                    self.send_response(200)
                    self.send_header("Content-Type", "audio/wav")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return

            self._send_json(404, {"error": "not found"}, cors=True)

        def do_POST(self) -> None:
            parsed = urllib.parse.urlsplit(self.path)
            if parsed.path.rstrip("/") != "/api/verdict":
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
