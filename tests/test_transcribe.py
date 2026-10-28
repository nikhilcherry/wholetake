import difflib
import hashlib
import json
import os
import subprocess
import time
import wave
from pathlib import Path

import numpy as np
import pytest

from ingest.transcribe import (
    RATE,
    RETRY_SCHEDULE_S,
    assemble_audio,
    check_eligibility,
    compute_log_mel,
    filter_and_shift_words,
    run_whisper,
    run_worker,
)
from ingest.witness import utc, write_atomic


def write_test_chunk(stream_dir: Path, seq: int, samples: np.ndarray, stream: str = "test", received: float = 1700000000.0):
    stream_dir.mkdir(parents=True, exist_ok=True)
    pcm = (np.clip(samples, -1.0, 1.0) * 32767.0).astype(np.int16).tobytes()
    dur = len(samples) / RATE
    wav_path = stream_dir / f"{seq:06d}.wav"
    with wave.open(str(wav_path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm)
    meta = {
        "seq": seq,
        "kind": "chunk",
        "stream": stream,
        "source": "local",
        "session": 1,
        "file": wav_path.name,
        "sha256": hashlib.sha256(wav_path.read_bytes()).hexdigest(),
        "samples": len(samples),
        "duration_s": round(dur, 3),
        "received_utc": utc(received),
        "received_utc_unix": round(received, 3),
        "start_utc_est": utc(received - dur),
        "end_utc_est": utc(received),
    }
    meta_path = stream_dir / f"{seq:06d}.meta.json"
    write_atomic(meta_path, json.dumps(meta).encode())
    return meta


def test_filter_and_shift_words():
    assert filter_and_shift_words([], prev_s=2.0, duration_s=60.0) == []

    # prev_s = 2.0, duration_s = 10.0 -> chunk span in unshifted time is [2.0, 12.0)
    raw = [
        {"w": "prev_out", "s": 1.0, "e": 1.8, "p": 0.9},       # shifted [-1.0, -0.2], mid -0.6 -> drop
        {"w": "straddle_in", "s": 1.9, "e": 2.2, "p": 0.95},   # shifted [-0.1, 0.2], mid 0.05 -> keep
        {"w": "exact_ zero", "s": 1.8, "e": 2.2, "p": 0.8},    # shifted [-0.2, 0.2], mid 0.0 -> keep
        {"w": "middle", "s": 5.0, "e": 5.5, "p": 0.99},        # shifted [3.0, 3.5], mid 3.25 -> keep
        {"w": "straddle_end", "s": 11.8, "e": 12.1, "p": 0.7}, # shifted [9.8, 10.1], mid 9.95 -> keep
        {"w": "exact_end", "s": 11.8, "e": 12.2, "p": 0.7},    # shifted [9.8, 10.2], mid 10.0 -> drop
        {"w": "next_out", "s": 12.1, "e": 12.5, "p": 0.8},     # shifted [10.1, 10.5], mid 10.3 -> drop
    ]
    res = filter_and_shift_words(raw, prev_s=2.0, duration_s=10.0)
    assert [w["w"] for w in res] == ["straddle_in", "exact_ zero", "middle", "straddle_end"]
    assert res[0]["s"] == -0.1 and res[0]["e"] == 0.2
    assert res[-1]["s"] == 9.8 and res[-1]["e"] == 10.1


def test_eligibility_rule(tmp_path: Path):
    sdir = tmp_path / "aje"
    sdir.mkdir()
    now = 1000.0
    dur = 60.0

    # Not yet eligible (fresh, no successor, not --once)
    assert check_eligibility(sdir, 0, dur, meta_mtime=950.0, now=now, once=False) == (False, False)

    # Eligible via --once
    assert check_eligibility(sdir, 0, dur, meta_mtime=950.0, now=now, once=True) == (True, False)

    # Eligible via staleness (> 1.5 * 60 + 10 = 100s)
    assert check_eligibility(sdir, 0, dur, meta_mtime=899.9, now=now, once=False) == (True, False)

    # Eligible via next gap
    (sdir / "000001.gap.json").write_text("{}")
    assert check_eligibility(sdir, 0, dur, meta_mtime=990.0, now=now, once=False) == (True, False)
    (sdir / "000001.gap.json").unlink()

    # Eligible via next chunk
    (sdir / "000001.meta.json").write_text("{}")
    assert check_eligibility(sdir, 0, dur, meta_mtime=990.0, now=now, once=False) == (True, True)


def test_context_assembly_and_gaps(tmp_path: Path):
    sdir = tmp_path / "aje"
    c0 = np.full(RATE * 5, 0.1, dtype=np.float32)
    c1 = np.full(RATE * 5, 0.2, dtype=np.float32)
    c3 = np.full(RATE * 5, 0.4, dtype=np.float32)

    write_test_chunk(sdir, 0, c0, stream="aje")
    write_test_chunk(sdir, 1, c1, stream="aje")
    (sdir / "000002.gap.json").write_text(json.dumps({"seq": 2, "kind": "gap", "stream": "aje"}))
    write_test_chunk(sdir, 3, c3, stream="aje")

    # Chunk 1 has chunk 0 before it, and gap 2 after it
    elig, has_next = check_eligibility(sdir, 1, 5.0, time.monotonic(), time.monotonic(), once=False)
    assert elig is True and has_next is False
    audio1, prev_s1, next_s1 = assemble_audio(sdir, 1, "aje", c1, has_next)
    assert prev_s1 == 2.0 and next_s1 == 0.0
    assert len(audio1) == RATE * 7

    # Chunk 3 has gap 2 before it, and nothing after it (processed with --once)
    elig3, has_next3 = check_eligibility(sdir, 3, 5.0, time.monotonic(), time.monotonic(), once=True)
    assert elig3 is True and has_next3 is False
    audio3, prev_s3, next_s3 = assemble_audio(sdir, 3, "aje", c3, has_next3)
    assert prev_s3 == 0.0 and next_s3 == 0.0
    assert len(audio3) == RATE * 5


def test_mel_spectrogram():
    rng = np.random.default_rng(42)
    wav_60 = rng.uniform(-0.5, 0.5, size=RATE * 60).astype(np.float32)
    mel_60 = compute_log_mel(wav_60)
    assert mel_60.dtype == np.float16
    assert mel_60.shape == (64, 1 + RATE * 60 // 160)  # centered frames: 6001

    wav_1_3 = rng.uniform(-0.5, 0.5, size=int(RATE * 1.3)).astype(np.float32)
    mel_1_3 = compute_log_mel(wav_1_3)
    assert mel_1_3.dtype == np.float16
    assert mel_1_3.shape == (64, 1 + int(RATE * 1.3) // 160)

    # Determinism
    assert np.array_equal(mel_1_3, compute_log_mel(wav_1_3))


def test_retry_schedule_and_lock(tmp_path: Path):
    import fcntl

    sdir = tmp_path / "s1"
    samples = np.zeros(RATE * 2, dtype=np.float32)
    write_test_chunk(sdir, 0, samples, stream="s1")

    now = [1000.0]
    sleeps = []

    def fake_clock():
        return now[0]

    def fake_sleep(dt):
        sleeps.append(round(dt, 2))
        now[0] += dt

    attempts = []

    def flaky_transcribe(audio):
        attempts.append(now[0])
        if len(attempts) <= 2:
            raise RuntimeError(f"transient GPU error #{len(attempts)}")
        return [{"w": "recovered", "s": 0.1, "e": 0.5, "p": 0.99}]

    run_worker(tmp_path, flaky_transcribe, "test-model", once=True, clock=fake_clock, sleep_fn=fake_sleep)

    assert len(attempts) == 3
    # First retry after 30s, second retry after 120s (2 min)
    assert sleeps == [30.0, 120.0]
    assert attempts == [1000.0, 1030.0, 1150.0]
    out_json = json.loads((sdir / "000000.json").read_text())
    assert out_json["text"] == "recovered"

    # Also verify exclusive flock on <root>/transcribe.lock refuses a second worker
    lock_fd = open(tmp_path / "transcribe.lock", "a+b")
    fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        with pytest.raises(BlockingIOError):
            run_worker(tmp_path, flaky_transcribe, "test-model", once=True, clock=fake_clock, sleep_fn=fake_sleep)
    finally:
        fcntl.flock(lock_fd.fileno(), fcntl.LOCK_UN)
        lock_fd.close()


def test_gives_up_after_retry_schedule_exhausted(tmp_path: Path, capsys):
    sdir = tmp_path / "s2"
    write_test_chunk(sdir, 0, np.zeros(RATE * 2, dtype=np.float32), stream="s2")

    now = [0.0]
    sleeps = []

    def always_fail(audio):
        raise RuntimeError("permanent failure")

    run_worker(
        tmp_path,
        always_fail,
        "test-model",
        once=True,
        clock=lambda: now[0],
        sleep_fn=lambda dt: (sleeps.append(round(dt, 1)), now.__setitem__(0, now[0] + dt)),
    )

    assert sleeps == [float(s) for s in RETRY_SCHEDULE_S]
    err = capsys.readouterr().err
    assert "GIVING UP" in err
    assert not (sdir / "000000.json").exists()


@pytest.mark.slow
def test_integration_real_audio(tmp_path: Path):
    wt_audio = os.environ.get("WT_AUDIO")
    if not wt_audio or not Path(wt_audio).exists():
        pytest.skip("WT_AUDIO not set or file does not exist")

    from faster_whisper import WhisperModel

    raw = subprocess.check_output(
        ["ffmpeg", "-loglevel", "error", "-i", wt_audio, "-ac", "1", "-ar", str(RATE), "-f", "s16le", "-t", "90", "-"]
    )
    samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    assert len(samples) > RATE * 25, "WT_AUDIO must be at least >25s to test multiple 20s chunks"

    sdir = tmp_path / "stream"
    chunk_samples = 20 * RATE
    n_chunks = 0
    for i in range(0, len(samples), chunk_samples):
        part = samples[i : i + chunk_samples]
        if len(part) < RATE:
            break
        write_test_chunk(sdir, n_chunks, part, stream="stream", received=1700000000.0 + i / RATE)
        n_chunks += 1

    device = "cuda"
    try:
        model = WhisperModel("large-v3-turbo", device="cuda", compute_type="float16")
    except Exception:
        device = "cpu"
        model = WhisperModel("large-v3-turbo", device="cpu", compute_type="int8")

    transcribe_fn = lambda audio: run_whisper(model, audio, "en")
    run_worker(tmp_path, transcribe_fn, "large-v3-turbo", once=True)

    norm = lambda w: "".join(c for c in w.lower() if c.isalnum())
    chunk_words = []
    boundary_pairs = []
    offset = 0.0
    total_s = sum(json.loads((sdir / f"{q:06d}.meta.json").read_text())["duration_s"] for q in range(n_chunks))
    # Whisper hallucinates on audio cut off mid-sentence; the tail is noise for both sides, so compare
    # only words that start well before the end. The seams are what this test is about.
    cutoff = total_s - 3.0

    for seq in range(n_chunks):
        rec_path = sdir / f"{seq:06d}.json"
        assert rec_path.exists(), f"missing transcript for chunk {seq}"
        rec = json.loads(rec_path.read_text())
        assert (sdir / rec["mel"]).exists()
        words = [norm(w["w"]) for w in rec["words"] if norm(w["w"]) and w["s"] + offset < cutoff]
        offset += rec["duration_s"]
        if chunk_words and words and chunk_words[-1] == words[0]:
            boundary_pairs.append(words[0])
        chunk_words.extend(words)

    used_samples = samples[: (n_chunks - 1) * chunk_samples + len(samples[(n_chunks - 1) * chunk_samples : n_chunks * chunk_samples])]
    whole_raw = transcribe_fn(used_samples)
    whole_words = [norm(w["w"]) for w in whole_raw if norm(w["w"]) and w["s"] < cutoff]

    ratio = difflib.SequenceMatcher(None, chunk_words, whole_words).ratio()
    assert ratio >= 0.95, f"word overlap {ratio:.3f} < 0.95"

    for dup in boundary_pairs:
        has_double_in_whole = any(whole_words[i] == dup and whole_words[i + 1] == dup for i in range(len(whole_words) - 1))
        assert has_double_in_whole, f"duplicate word '{dup}' across chunk boundary not present in whole-file transcript"
