"""Transcribe witnessed audio chunks with boundary overlap and emit log-mel fingerprints.

    python -m ingest.transcribe [--root data/witness] [--once]

Watches every stream directory under --root for <seq>.meta.json files without a corresponding
<seq>.json transcript. To prevent words straddling 60s boundaries from being mangled or duplicated,
chunk k is transcribed with up to 2s of audio from chunk k-1 and k+1, and only words whose
midpoint falls inside chunk k's own span [0, duration_s) are kept.
"""
import argparse
import hashlib
import io
import json
import os
import sys
import time
import wave
from pathlib import Path

import numpy as np

from ingest.chain import fcntl

RATE = 16000
CONTEXT_S = 2.0
POLL_S = 2.0
N_MELS = 64
WIN_LEN = 400   # 25 ms at 16 kHz
HOP_LEN = 160   # 10 ms at 16 kHz
N_FFT = 512
RETRY_SCHEDULE_S = [30, 120, 600, 1800]


def write_atomic(path: Path, data: bytes):
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def read_wav(path: Path, expected_sha256: str | None = None) -> np.ndarray:
    raw = path.read_bytes()
    if expected_sha256 is not None:
        actual = hashlib.sha256(raw).hexdigest()
        if actual != expected_sha256:
            raise ValueError(f"sha256 mismatch on {path}: expected {expected_sha256}, got {actual}")
    with wave.open(io.BytesIO(raw), "rb") as w:
        if w.getnchannels() != 1 or w.getsampwidth() != 2 or w.getframerate() != RATE:
            raise ValueError(f"unexpected wav format in {path}")
        pcm = w.readframes(w.getnframes())
    return np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0


def _mel_filterbank() -> np.ndarray:
    def hz_to_mel(f):
        return 2595.0 * np.log10(1.0 + f / 700.0)

    def mel_to_hz(m):
        return 700.0 * (10.0 ** (m / 2595.0) - 1.0)

    mels = np.linspace(hz_to_mel(0.0), hz_to_mel(RATE / 2.0), N_MELS + 2)
    hz = mel_to_hz(mels)
    freqs = np.linspace(0.0, RATE / 2.0, N_FFT // 2 + 1)
    fb = np.zeros((N_MELS, N_FFT // 2 + 1), dtype=np.float32)
    for i in range(N_MELS):
        left, center, right = hz[i], hz[i + 1], hz[i + 2]
        up = (freqs - left) / (center - left + 1e-10)
        down = (right - freqs) / (right - center + 1e-10)
        fb[i] = np.maximum(0.0, np.minimum(up, down))
    return fb


_MEL_FB = _mel_filterbank()
_WINDOW = np.hanning(WIN_LEN).astype(np.float32)


def compute_log_mel(audio: np.ndarray) -> np.ndarray:
    """Compute (64, frames) float16 log-mel spectrogram using 25ms window, 10ms hop."""
    if len(audio) == 0:
        return np.zeros((N_MELS, 0), dtype=np.float16)
    pad = WIN_LEN // 2
    padded = np.pad(audio, (pad, pad), mode="reflect") if len(audio) > pad else np.pad(audio, (pad, pad))
    n_frames = 1 + (len(padded) - WIN_LEN) // HOP_LEN
    frames = np.lib.stride_tricks.as_strided(
        padded,
        shape=(n_frames, WIN_LEN),
        strides=(padded.strides[0] * HOP_LEN, padded.strides[0]),
    )
    spec = np.abs(np.fft.rfft(frames * _WINDOW, n=N_FFT, axis=1)) ** 2
    mel = np.dot(_MEL_FB, spec.T)
    return np.log10(np.maximum(mel, 1e-10)).astype(np.float16)


def filter_and_shift_words(words: list[dict], prev_s: float, duration_s: float) -> list[dict]:
    """Keep words whose midpoint falls in chunk k's span [0, duration_s), shifted by prev_s."""
    kept = []
    for w in words:
        s = w["s"] - prev_s
        e = w["e"] - prev_s
        mid = 0.5 * (s + e)
        if 0.0 <= mid < duration_s:
            kept.append({
                "w": w["w"],
                "s": round(s, 2),
                "e": round(e, 2),
                "p": round(float(w["p"]), 2),
            })
    return kept


def check_eligibility(stream_dir: Path, seq: int, duration_s: float, meta_mtime: float, now: float, once: bool) -> tuple[bool, bool]:
    """Return (is_eligible, has_next_chunk)."""
    next_meta = stream_dir / f"{seq + 1:06d}.meta.json"
    if next_meta.exists():
        return True, True
    next_gap = stream_dir / f"{seq + 1:06d}.gap.json"
    if next_gap.exists():
        return True, False
    if once or (now - meta_mtime) > (1.5 * duration_s + 10.0):
        return True, False
    return False, False


def assemble_audio(stream_dir: Path, seq: int, stream: str, main_audio: np.ndarray, has_next_chunk: bool) -> tuple[np.ndarray, float, float]:
    """Prepend up to 2s from seq-1 and append up to 2s from seq+1 if valid adjacent chunks."""
    prev_audio = np.zeros(0, dtype=np.float32)
    next_audio = np.zeros(0, dtype=np.float32)
    want_samples = int(CONTEXT_S * RATE)

    if seq > 0:
        prev_meta_p = stream_dir / f"{seq - 1:06d}.meta.json"
        prev_wav_p = stream_dir / f"{seq - 1:06d}.wav"
        if prev_meta_p.exists() and prev_wav_p.exists():
            try:
                pmeta = json.loads(prev_meta_p.read_text())
                if pmeta.get("kind") == "chunk" and pmeta.get("stream") == stream and pmeta.get("seq") == seq - 1:
                    pa = read_wav(prev_wav_p, pmeta.get("sha256"))
                    prev_audio = pa[-want_samples:] if len(pa) > want_samples else pa
            except Exception as e:
                print(f"warning: skipping prev context from {prev_meta_p}: {e}", file=sys.stderr)

    if has_next_chunk:
        next_meta_p = stream_dir / f"{seq + 1:06d}.meta.json"
        next_wav_p = stream_dir / f"{seq + 1:06d}.wav"
        if next_meta_p.exists() and next_wav_p.exists():
            try:
                nmeta = json.loads(next_meta_p.read_text())
                if nmeta.get("kind") == "chunk" and nmeta.get("stream") == stream and nmeta.get("seq") == seq + 1:
                    na = read_wav(next_wav_p, nmeta.get("sha256"))
                    next_audio = na[:want_samples] if len(na) > want_samples else na
            except Exception as e:
                print(f"warning: skipping next context from {next_meta_p}: {e}", file=sys.stderr)

    prev_s = round(len(prev_audio) / RATE, 3)
    next_s = round(len(next_audio) / RATE, 3)
    combined = np.concatenate([prev_audio, main_audio, next_audio]) if (len(prev_audio) or len(next_audio)) else main_audio
    return combined, prev_s, next_s


def run_whisper(model, audio: np.ndarray, lang: str | None) -> list[dict]:
    segments, _ = model.transcribe(
        audio,
        language=None if lang == "auto" else lang,
        word_timestamps=True,
        vad_filter=True,
    )
    raw_words = []
    for seg in segments:
        for w in (seg.words or []):
            text = w.word.strip()
            if text:
                raw_words.append({"w": text, "s": float(w.start), "e": float(w.end), "p": float(w.probability)})
    return raw_words


def process_chunk(stream_dir: Path, seq: int, meta: dict, has_next_chunk: bool, transcribe_fn, model_name: str):
    t0 = time.monotonic()
    wav_path = stream_dir / f"{seq:06d}.wav"
    main_audio = read_wav(wav_path, expected_sha256=meta["sha256"])
    duration_s = float(meta["duration_s"])
    stream = meta["stream"]

    combined, prev_s, next_s = assemble_audio(stream_dir, seq, stream, main_audio, has_next_chunk)
    raw_words = transcribe_fn(combined)
    words = filter_and_shift_words(raw_words, prev_s, duration_s)
    text = " ".join(w["w"] for w in words)

    mel = compute_log_mel(main_audio)
    mel_name = f"{seq:06d}.mel.npy"
    mel_path = stream_dir / mel_name
    buf = io.BytesIO()
    np.save(buf, mel)
    mel_bytes = buf.getvalue()
    write_atomic(mel_path, mel_bytes)
    mel_sha256 = hashlib.sha256(mel_bytes).hexdigest()

    record = {
        "seq": seq,
        "stream": stream,
        "kind": "transcript",
        "wav_sha256": meta["sha256"],
        "received_utc": meta["received_utc"],
        "start_utc_est": meta["start_utc_est"],
        "duration_s": duration_s,
        "words": words,
        "text": text,
        "mel": mel_name,
        "mel_sha256": mel_sha256,
        "model": model_name,
        "context": {"prev_s": prev_s, "next_s": next_s},
    }
    out_path = stream_dir / f"{seq:06d}.json"
    write_atomic(out_path, json.dumps(record, indent=1).encode())
    wall_s = time.monotonic() - t0
    print(f"[{seq:06d}] {stream}  {len(words)} words  {duration_s:.1f}s audio  {wall_s:.2f}s wall", flush=True)


def _record_failure(meta_path: Path, err: Exception, retries: dict[Path, tuple[int, float]], gave_up: set[Path], now_mono: float):
    count, _ = retries.get(meta_path, (0, 0.0))
    if count >= len(RETRY_SCHEDULE_S):
        retries.pop(meta_path, None)
        gave_up.add(meta_path)
        print(
            f"GIVING UP on {meta_path} after {count + 1} failures (will not retry this run): {err}",
            file=sys.stderr,
            flush=True,
        )
    else:
        delay = RETRY_SCHEDULE_S[count]
        retries[meta_path] = (count + 1, now_mono + delay)
        print(
            f"ERROR processing {meta_path} (attempt {count + 1}/{len(RETRY_SCHEDULE_S) + 1}, retry in {delay}s): {err}",
            file=sys.stderr,
            flush=True,
        )


def find_candidates(
    root: Path,
    retries: dict[Path, tuple[int, float]],
    gave_up: set[Path],
    once: bool,
    now_mono: float,
    first_seen_mono: dict[Path, float],
) -> list[tuple[float, int, Path, dict, bool]]:
    now_wall = time.time()
    candidates = []
    if not root.exists():
        return candidates
    for meta_path in sorted(root.glob("*/[0-9]*.meta.json")):
        if meta_path in gave_up:
            continue
        if meta_path in retries and now_mono < retries[meta_path][1]:
            continue
        stream_dir = meta_path.parent
        try:
            seq = int(meta_path.name.split(".")[0])
        except ValueError:
            continue
        out_path = stream_dir / f"{seq:06d}.json"
        if out_path.exists():
            retries.pop(meta_path, None)
            first_seen_mono.pop(meta_path, None)
            continue
        try:
            meta = json.loads(meta_path.read_text())
            mtime = meta_path.stat().st_mtime
            duration_s = float(meta["duration_s"])
            sort_key = float(meta.get("received_utc_unix", mtime))
        except Exception as e:
            _record_failure(meta_path, e, retries, gave_up, now_mono)
            continue

        if meta_path not in first_seen_mono:
            # Anchor initial file age from wall-clock mtime into monotonic time so eligibility uses monotonic time
            initial_age = max(0.0, now_wall - mtime)
            first_seen_mono[meta_path] = now_mono - initial_age

        eligible, has_next = check_eligibility(stream_dir, seq, duration_s, first_seen_mono[meta_path], now_mono, once)
        if eligible:
            candidates.append((sort_key, seq, meta_path, meta, has_next))

    candidates.sort(key=lambda x: (x[0], x[1], str(x[2])))
    return candidates


def run_worker(
    root: Path,
    transcribe_fn,
    model_name: str,
    once: bool = False,
    clock=time.monotonic,
    sleep_fn=time.sleep,
):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    lock_path = root / "transcribe.lock"
    lock_fd = open(lock_path, "a+b")
    try:
        fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock_fd.close()
        raise BlockingIOError(f"transcribe.lock already held in {root}")

    retries: dict[Path, tuple[int, float]] = {}
    gave_up: set[Path] = set()
    first_seen_mono: dict[Path, float] = {}
    try:
        while True:
            now_mono = clock()
            candidates = find_candidates(root, retries, gave_up, once=once, now_mono=now_mono, first_seen_mono=first_seen_mono)
            if not candidates:
                if once and not retries:
                    break
                if once and retries:
                    # In --once mode with pending retries, advance/wait until the next retry is due
                    next_due = min(due for _, due in retries.values())
                    wait_s = max(0.0, next_due - now_mono)
                    sleep_fn(wait_s)
                    continue
                sleep_fn(POLL_S)
                continue

            _, seq, meta_path, meta, has_next = candidates[0]
            try:
                process_chunk(meta_path.parent, seq, meta, has_next, transcribe_fn, model_name)
                retries.pop(meta_path, None)
                first_seen_mono.pop(meta_path, None)
            except Exception as e:
                _record_failure(meta_path, e, retries, gave_up, clock())
    finally:
        try:
            fcntl.flock(lock_fd.fileno(), fcntl.LOCK_UN)
        except Exception:
            pass
        lock_fd.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default="data/witness", help="witness data root directory")
    ap.add_argument("--once", action="store_true", help="process available chunks and exit")
    ap.add_argument("--model", default="large-v3-turbo", help="faster-whisper model name")
    ap.add_argument("--device", default="cuda", choices=["cuda", "cpu"], help="inference device")
    ap.add_argument("--lang", default="en", help="language code or 'auto'")
    a = ap.parse_args()

    from faster_whisper import WhisperModel
    compute_type = "int8" if a.device == "cpu" else "float16"
    model = WhisperModel(a.model, device=a.device, compute_type=compute_type)
    transcribe_fn = lambda audio: run_whisper(model, audio, a.lang)

    try:
        run_worker(Path(a.root), transcribe_fn, a.model, once=a.once)
    except BlockingIOError as e:
        print(f"ERROR: {e}", file=sys.stderr, flush=True)
        sys.exit(1)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
