import os
import shutil
import subprocess
import sys
import tempfile
import numpy as np

from roomstamp.media import ffmpeg_exe
from roomstamp.wt_detect import _decode_ffmpeg, ingest


def test_decode_ffmpeg_preserves_orig_channels() -> None:
    tmp = tempfile.mkdtemp(prefix="test_f122_")
    try:
        sr = 48000
        n_samples = 3 * sr  # 144000 samples (3.0 s)
        ch = 4
        t = np.arange(n_samples, dtype=np.float32) / sr
        sig = np.stack([
            0.2 * np.sin(2 * np.pi * 440.0 * t),
            0.2 * np.sin(2 * np.pi * 550.0 * t),
            0.1 * np.sin(2 * np.pi * 660.0 * t),
            0.1 * np.sin(2 * np.pi * 880.0 * t),
        ], axis=1).astype("<f4")

        raw_path = os.path.join(tmp, "in4ch.f32")
        sig.tofile(raw_path)

        mp4_path = os.path.join(tmp, "clip4ch.mp4")
        subprocess.run(
            [
                ffmpeg_exe(), "-v", "error", "-nostdin", "-y",
                "-f", "f32le", "-ar", str(sr), "-ac", str(ch), "-i", raw_path,
                "-c:a", "aac", "-b:a", "256k", mp4_path,
            ],
            check=True,
        )

        x, dec_sr, codec = _decode_ffmpeg(mp4_path)
        if x.shape[1] != 4:
            print(
                f"FAIL test_decode_ffmpeg_preserves_orig_channels: "
                f"_decode_ffmpeg forced '-ac 2' and returned shape {x.shape}, "
                f"losing original channel count (4) for clip.orig_channels"
            )
            sys.exit(1)
        if dec_sr != sr:
            print(f"FAIL test_decode_ffmpeg_preserves_orig_channels: expected sr={sr}, got {dec_sr}")
            sys.exit(1)

        clip = ingest(mp4_path)
        if clip.decoder != "ffmpeg":
            print(f"FAIL test_decode_ffmpeg_preserves_orig_channels: expected decoder='ffmpeg', got {clip.decoder!r}")
            sys.exit(1)
        if clip.orig_channels != 4:
            print(
                f"FAIL test_decode_ffmpeg_preserves_orig_channels: "
                f"expected clip.orig_channels=4, got {clip.orig_channels}"
            )
            sys.exit(1)

        print("PASS test_decode_ffmpeg_preserves_orig_channels")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    test_decode_ffmpeg_preserves_orig_channels()
