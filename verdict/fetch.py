"""Fetch and normalise a clip URL or local media file into a 16 kHz mono s16 WAV."""
import hashlib
import ipaddress
import json
import os
import shutil
import socket
import subprocess
import sys
import urllib.parse
import urllib.request
import wave
from pathlib import Path

from ingest.chain import sha256_file, write_atomic

BIN = Path(sys.executable).parent
REPO_ROOT = Path(__file__).resolve().parents[1]

MAX_DURATION_S = 600.0
MAX_FILESIZE_BYTES = 200 * 1024 * 1024
FETCH_TIMEOUT_S = 120


class FetchError(RuntimeError):
    """Raised when a clip cannot be fetched, fails SSRF checks, or exceeds hard limits."""


class SSRFError(FetchError):
    """Raised when a URL uses a forbidden scheme or resolves to a non-public IP."""


def clips_root() -> Path:
    env = os.environ.get("WT_CLIPS_ROOT")
    return Path(env) if env else REPO_ROOT / "data" / "clips"


def is_url(url_or_path: str) -> bool:
    return url_or_path.startswith(("http://", "https://"))


def _allowed_host_ports() -> set[str]:
    raw = os.environ.get("WT_FETCH_ALLOW_HOSTS", "")
    out: set[str] = set()
    for item in raw.split(","):
        tok = item.strip().lower()
        if tok and ":" in tok:
            out.add(tok)
    return out


def validate_public_url(url: str) -> None:
    """Reject non-http(s) URLs, credentials in URLs, and private/loopback/link-local hosts."""
    try:
        parsed = urllib.parse.urlsplit(url)
    except Exception as e:
        raise SSRFError(f"invalid URL: {e}") from e

    if parsed.scheme not in ("http", "https"):
        raise SSRFError(f"unsupported URL scheme {parsed.scheme!r} (only http/https allowed)")
    if not parsed.hostname:
        raise SSRFError("URL is missing hostname")
    if parsed.username or parsed.password:
        raise SSRFError("URLs with embedded credentials are not allowed")

    host = parsed.hostname.strip().lower()
    effective_port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if f"{host}:{effective_port}" in _allowed_host_ports():
        # Exact host:port match in WT_FETCH_ALLOW_HOSTS is exempted so simulated X on 127.0.0.1:8791 can serve clips.
        return

    if host in ("localhost", "localhost.localdomain") or host.endswith((".local", ".internal", ".localhost")):
        raise SSRFError(f"forbidden local hostname: {host}")

    def _check_ip(ip_str: str) -> None:
        ip = ipaddress.ip_address(ip_str)
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            raise SSRFError(f"forbidden non-public IP address: {ip}")

    try:
        _check_ip(host)
        return
    except ValueError:
        pass

    try:
        infos = socket.getaddrinfo(host, effective_port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as e:
        raise FetchError(f"DNS resolution failed for {host}: {e}") from e

    if not infos:
        raise FetchError(f"no DNS records found for {host}")

    for info in infos:
        sockaddr = info[4]
        if sockaddr:
            _check_ip(sockaddr[0])


def _wav_duration_s(wav_path: Path) -> float:
    with wave.open(str(wav_path), "rb") as wf:
        frames = wf.getnframes()
        rate = wf.getframerate()
        if rate <= 0:
            raise FetchError(f"invalid sample rate {rate} in {wav_path}")
        return round(frames / float(rate), 3)


def _find_ffmpeg() -> str:
    found = shutil.which("ffmpeg")
    if found:
        return found
    for candidate in (BIN / "ffmpeg", BIN / "ffmpeg.exe"):
        if candidate.exists():
            return str(candidate)
    try:
        from roomstamp.media import ffmpeg_exe
        return ffmpeg_exe()
    except Exception:
        return "ffmpeg"


def _normalise_to_wav(src_path: Path, dst_wav: Path) -> None:
    ffmpeg = _find_ffmpeg()
    tmp_wav = dst_wav.with_suffix(".tmp.wav")
    cmd = [
        ffmpeg,
        "-nostdin",
        "-y",
        "-v",
        "error",
        "-i",
        str(src_path),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-acodec",
        "pcm_s16le",
        "-t",
        str(int(MAX_DURATION_S) + 2),
        str(tmp_wav),
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=FETCH_TIMEOUT_S)
    except subprocess.TimeoutExpired as e:
        tmp_wav.unlink(missing_ok=True)
        raise FetchError(f"ffmpeg timed out after {FETCH_TIMEOUT_S}s") from e

    if res.returncode != 0 or not tmp_wav.exists():
        tmp_wav.unlink(missing_ok=True)
        err = (res.stderr or "").strip().splitlines()[-1:]
        raise FetchError(f"ffmpeg failed on {src_path.name}: {err}")

    os.replace(tmp_wav, dst_wav)


def fetch(url_or_path: str) -> dict:
    """Download or convert `url_or_path` to 16 kHz mono s16 WAV and return clip metadata."""
    s = url_or_path.strip()
    if not s:
        raise FetchError("empty url_or_path")

    parsed = urllib.parse.urlsplit(s)
    if parsed.scheme and parsed.scheme not in ("http", "https"):
        raise SSRFError(f"unsupported URL scheme {parsed.scheme!r}")

    cache_key = hashlib.sha256(s.encode("utf-8")).hexdigest()
    cache_dir = clips_root() / cache_key
    wav_path = cache_dir / "clip.wav"
    meta_path = cache_dir / "meta.json"

    if is_url(s):
        validate_public_url(s)
        if wav_path.exists() and meta_path.exists():
            try:
                cached = json.loads(meta_path.read_text())
                if cached.get("sha256") == sha256_file(wav_path):
                    cached["wav"] = str(wav_path)
                    return cached
            except Exception:
                pass

        cache_dir.mkdir(parents=True, exist_ok=True)
        for old in cache_dir.glob("raw.*"):
            old.unlink(missing_ok=True)

        ytdlp_bin = None
        for cand in (BIN / "yt-dlp", BIN / "yt-dlp.exe", Path(r"C:\Users\rajsh\yt-dlp\yt-dlp.exe")):
            if cand.exists():
                ytdlp_bin = str(cand)
                break
        if not ytdlp_bin:
            ytdlp_bin = shutil.which("yt-dlp")

        title = None
        raw_files: list[Path] = []
        if ytdlp_bin:
            out_tmpl = str(cache_dir / "raw.%(ext)s")
            cmd = [
                ytdlp_bin,
                "-4",
                "--js-runtimes",
                "node",
                "--no-playlist",
                "--max-filesize",
                "200M",
                "--match-filter",
                f"duration <=? {int(MAX_DURATION_S)}",  # "?": unknown duration passes; the wav is length-checked after conversion
                "-f",
                "bestaudio/best",
                "--print-json",
                "-o",
                out_tmpl,
                s,
            ]
            cookies = os.environ.get("WT_YTDLP_COOKIES")
            if cookies and Path(cookies).exists():
                cmd[1:1] = ["--cookies", cookies]
            try:
                res = subprocess.run(cmd, capture_output=True, text=True, timeout=FETCH_TIMEOUT_S)
            except subprocess.TimeoutExpired as e:
                raise FetchError(f"yt-dlp timed out after {FETCH_TIMEOUT_S}s") from e

            if res.returncode != 0:
                err = (res.stderr or "").strip().splitlines()[-1:]
                raise FetchError(f"yt-dlp failed for {s}: {err}")

            for line in res.stdout.splitlines():
                line = line.strip()
                if line.startswith("{") and line.endswith("}"):
                    try:
                        info = json.loads(line)
                        title = info.get("title")
                        dur = info.get("duration")
                        if isinstance(dur, (int, float)) and dur > MAX_DURATION_S:
                            raise FetchError(f"clip duration {dur}s exceeds limit of {MAX_DURATION_S}s")
                    except FetchError:
                        raise
                    except Exception:
                        pass
            raw_files = [p for p in cache_dir.glob("raw.*") if not p.name.endswith(".part")]
        else:
            # Direct HTTP media download fallback when yt-dlp is not installed (e.g. local simx / direct mp4/wav)
            ext = Path(parsed.path).suffix or ".mp4"
            dl_path = cache_dir / f"raw{ext}"
            try:
                req = urllib.request.Request(s, headers={"User-Agent": "Wholetake/1.0"})
                with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT_S) as resp, open(dl_path, "wb") as out_f:
                    total = 0
                    while True:
                        chunk = resp.read(65536)
                        if not chunk:
                            break
                        total += len(chunk)
                        if total > MAX_FILESIZE_BYTES:
                            raise FetchError(f"downloaded file exceeds {MAX_FILESIZE_BYTES} bytes")
                        out_f.write(chunk)
                title = Path(parsed.path).stem or "clip"
                raw_files = [dl_path]
            except FetchError:
                dl_path.unlink(missing_ok=True)
                raise
            except Exception as e:
                dl_path.unlink(missing_ok=True)
                raise FetchError(f"direct download failed for {s}: {e}") from e
        if not raw_files:
            raise FetchError(f"yt-dlp produced no media file for {s} (may exceed duration or size limits)")
        raw_path = raw_files[0]
        try:
            if raw_path.stat().st_size > MAX_FILESIZE_BYTES:
                raise FetchError(f"downloaded file exceeds {MAX_FILESIZE_BYTES} bytes")
            _normalise_to_wav(raw_path, wav_path)
        finally:
            for p in cache_dir.glob("raw.*"):
                p.unlink(missing_ok=True)
        source_url = s
    else:
        src = Path(s)
        if not src.exists() or not src.is_file():
            raise FetchError(f"file not found: {s}")
        if src.stat().st_size > MAX_FILESIZE_BYTES:
            raise FetchError(f"local file {src.name} exceeds {MAX_FILESIZE_BYTES} bytes")
        cache_dir.mkdir(parents=True, exist_ok=True)
        _normalise_to_wav(src, wav_path)
        source_url = None
        title = src.stem

    duration_s = _wav_duration_s(wav_path)
    if duration_s <= 0:
        raise FetchError("clip has zero duration")
    if duration_s > MAX_DURATION_S:
        wav_path.unlink(missing_ok=True)
        raise FetchError(f"clip duration {duration_s}s exceeds max {MAX_DURATION_S}s")

    sha = sha256_file(wav_path)
    result = {
        "wav": str(wav_path),
        "sha256": sha,
        "duration_s": duration_s,
        "source_url": source_url,
        "title": title,
    }
    write_atomic(meta_path, (json.dumps(result, sort_keys=True) + "\n").encode("utf-8"))
    return result
