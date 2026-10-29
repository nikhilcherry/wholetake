"""Decode any clip (mp4/m4a/ogg/webm/wav) to 48 kHz mono float with ffmpeg, and simulate
platform codecs for tests. ffmpeg comes from PATH, $FFMPEG, or the imageio-ffmpeg wheel."""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile

import numpy as np

from .spec import FS


def ffmpeg_exe() -> str:
    exe = os.environ.get("FFMPEG") or shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as e:  # pragma: no cover
        raise RuntimeError("ffmpeg not found: install it or `pip install imageio-ffmpeg`") from e


def decode(path: str, fs: int = FS) -> np.ndarray:
    """Any media file -> mono float64 at fs (first audio stream)."""
    cmd = [ffmpeg_exe(), "-v", "error", "-nostdin", "-i", path, "-map", "0:a:0", "-ac", "1",
           "-ar", str(fs), "-f", "f32le", "-"]
    p = subprocess.run(cmd, capture_output=True)
    if p.returncode != 0:
        raise RuntimeError(f"ffmpeg could not decode {path}: {p.stderr.decode(errors='replace').strip()}")
    return np.frombuffer(p.stdout, dtype="<f4").astype(np.float64)


# name -> (extension, encoder args). Rates are what the platforms roughly use.
CODECS = {
    "aac128": (".m4a", ["-c:a", "aac", "-b:a", "128k"]),           # X / Instagram upload
    "aac64": (".m4a", ["-c:a", "aac", "-b:a", "64k"]),
    "opus16": (".ogg", ["-c:a", "libopus", "-b:a", "16k"]),        # WhatsApp voice-note-ish
    "mp3": (".mp3", ["-c:a", "libmp3lame", "-b:a", "96k"]),
    "amr122": (".amr", ["-ar", "8000", "-c:a", "libopencore_amrnb", "-b:a", "12.2k"]),
    "amr475": (".amr", ["-ar", "8000", "-c:a", "libopencore_amrnb", "-b:a", "4.75k"]),
}


def transcode(x: np.ndarray, codec: str, filters: str | None = None, fs: int = FS) -> np.ndarray:
    """Round-trip x through an encoder (and optional ffmpeg -af filter chain, e.g. 'atempo=1.1')."""
    ext, args = CODECS[codec] if codec != "none" else (".wav", ["-c:a", "pcm_s16le"])
    with tempfile.TemporaryDirectory() as d:
        src, dst = os.path.join(d, "in.f32"), os.path.join(d, "out" + ext)
        np.asarray(x, dtype="<f4").tofile(src)
        cmd = [ffmpeg_exe(), "-v", "error", "-nostdin", "-y", "-f", "f32le", "-ar", str(fs), "-ac", "1", "-i", src]
        if filters:
            cmd += ["-af", filters]
        p = subprocess.run(cmd + args + [dst], capture_output=True)
        if p.returncode != 0:
            raise RuntimeError(p.stderr.decode(errors="replace"))
        return decode(dst, fs)
