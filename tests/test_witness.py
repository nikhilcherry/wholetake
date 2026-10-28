import json
import os
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

from ingest import witness


def test_within_session_skip_drains_and_emits_gap(tmp_path: Path, monkeypatch):
    # Generate ~6s of valid WAV audio with ffmpeg, then split into two halves
    full_wav = tmp_path / "source.wav"
    subprocess.run(
        [
            "ffmpeg", "-loglevel", "error",
            "-f", "lavfi", "-i", "sine=f=440:d=6",
            "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le",
            str(full_wav),
        ],
        check=True,
    )
    raw_bytes = full_wav.read_bytes()
    # Keep WAV header (44 bytes) intact on first half; second half is just a second valid WAV for the reconnect
    half_wav = tmp_path / "half.wav"
    subprocess.run(
        [
            "ffmpeg", "-loglevel", "error",
            "-f", "lavfi", "-i", "sine=f=440:d=3.2",
            "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le",
            str(half_wav),
        ],
        check=True,
    )
    state_file = tmp_path / "sl_state.txt"

    fake_sl = tmp_path / "fake_streamlink.py"
    fake_sl.write_text(
        f"""#!{sys.executable}
import sys, time
from pathlib import Path

half_bytes = Path({str(half_wav)!r}).read_bytes()
state_p = Path({str(state_file)!r})

if not state_p.exists():
    state_p.write_text("1")
    # First session: write first half (~3.2s), emit sequence gap warning to stderr, then keep writing second half
    sys.stdout.buffer.write(half_bytes)
    sys.stdout.buffer.flush()
    time.sleep(0.15)
    sys.stderr.write("[stream.segmented][warning] Sequence gap of 3 segments at position 7. This will result in a discontinuity\\n")
    sys.stderr.flush()
    # Sleep while waiting to be terminated by witness, then try writing more if still alive
    for _ in range(50):
        time.sleep(0.1)
        try:
            sys.stdout.buffer.write(half_bytes[44:44+3200])
            sys.stdout.buffer.flush()
        except BrokenPipeError:
            break
else:
    # Reconnected session: write ~3.2s of audio then hold until witness stops
    sys.stdout.buffer.write(half_bytes)
    sys.stdout.buffer.flush()
    time.sleep(10)
"""
    )
    fake_sl.chmod(fake_sl.stat().st_mode | stat.S_IEXEC)

    monkeypatch.setenv("WT_STREAMLINK", str(fake_sl))
    monkeypatch.setattr(witness, "BACKOFF", [0.2])

    w = witness.Witness("https://example.com/live", "test_skip", chunk_s=2.0, root=tmp_path / "root", optin=False)
    try:
        rc = w.run(until=time.time() + 3.5)
        assert rc == 0
    finally:
        w.close()

    items = sorted(
        [*w.dir.glob("[0-9]*.meta.json"), *w.dir.glob("[0-9]*.gap.json")],
        key=lambda p: int(p.name.split(".")[0]),
    )
    records = [json.loads(p.read_text()) for p in items]
    kinds = [r["kind"] for r in records]

    # Expect chunk(s) before gap, a gap with "streamlink: Sequence gap...", and chunk(s) after gap
    assert "gap" in kinds, f"expected gap in {records}"
    gap_idx = kinds.index("gap")
    assert gap_idx >= 1, f"expected at least one chunk before gap: {records}"
    assert any(k == "chunk" for k in kinds[:gap_idx])
    assert any(k == "chunk" for k in kinds[gap_idx + 1 :]), f"expected chunk(s) after gap: {records}"
    gap_rec = records[gap_idx]
    assert gap_rec["reason"].startswith("streamlink: Sequence gap"), gap_rec


def test_witness_lock_prevents_second_instance(tmp_path: Path):
    root = tmp_path / "witness"
    w1 = witness.Witness("https://example.com/live", "same_stream", chunk_s=10, root=root, optin=False)
    try:
        # Second witness in-process exits with code 1
        with pytest.raises(SystemExit) as excinfo:
            witness.Witness("https://example.com/live", "same_stream", chunk_s=10, root=root, optin=False)
        assert excinfo.value.code == 1

        # Subprocess CLI also exits with code 1 and clear error message
        res = subprocess.run(
            [sys.executable, "-m", "ingest.witness", "https://example.com/live", "--name", "same_stream", "--root", str(root), "--no-optin", "--secs", "1"],
            capture_output=True,
            text=True,
        )
        assert res.returncode == 1
        assert "already running" in res.stderr
    finally:
        w1.close()

    # Once closed, a new witness can acquire the lock
    w2 = witness.Witness("https://example.com/live", "same_stream", chunk_s=10, root=root, optin=False)
    w2.close()


def test_resume_ignores_transcript_json_and_dropped_tail_adjusts_down_since(tmp_path: Path):
    root = tmp_path / "witness"
    sdir = root / "s1"
    sdir.mkdir(parents=True)

    # Write chunk 0 meta and an unrelated 000000.json transcript file
    meta0 = {
        "seq": 0,
        "kind": "chunk",
        "stream": "s1",
        "received_utc": "2026-01-01T00:00:10.000Z",
        "received_utc_unix": 1767225610.0,
    }
    (sdir / "000000.meta.json").write_text(json.dumps(meta0))
    # Transcript JSON has no received_utc_unix; old glob("[0-9]*.json") would have picked it if sorted after meta
    (sdir / "000000.json").write_text(json.dumps({"seq": 0, "kind": "transcript"}))

    # Create a tiny 0.5s audio file (< MIN_TAIL_S=1.0s)
    short_wav = tmp_path / "short.wav"
    subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=f=440:d=0.5", str(short_wav)],
        check=True,
    )

    w = witness.Witness(str(short_wav), "s1", chunk_s=10.0, root=root, optin=False)
    try:
        assert w.seq == 1
        assert w.down_since == 1767225610.0
        got = w.run_session()
        assert got is True
        # Seq 1 was the gap from 1767225610.0 until session start; the 0.5s audio was < MIN_TAIL_S so dropped
        assert (sdir / "000001.gap.json").exists()
        assert not (sdir / "000002.meta.json").exists()
        gap1 = json.loads((sdir / "000001.gap.json").read_text())
        # The dropped 0.5 s must fall inside the next gap: it starts no later than that audio's first arrival
        # (gap1's to_utc), give or take ffmpeg -re delivery jitter, and never after the audio we threw away.
        assert w.down_since <= gap1["to_utc_unix"] + 0.6
        assert w.down_since >= gap1["to_utc_unix"] - 1.0
    finally:
        w.close()
