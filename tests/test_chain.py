import hashlib
import json
import subprocess
import sys
import wave
from pathlib import Path

import pytest

from ingest.chain import (
    ChainDaemon,
    StreamAppender,
    canonical_json,
    check_proof,
    entry_hash,
    load,
    proof,
    verify,
)


def write_chunk(stream_dir: Path, seq: int, pcm: bytes = b"\x00\x01" * 1600, wav_sha_override: str | None = None):
    stream_dir.mkdir(parents=True, exist_ok=True)
    wav_p = stream_dir / f"{seq:06d}.wav"
    with wave.open(str(wav_p), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(pcm)
    sha = wav_sha_override or hashlib.sha256(wav_p.read_bytes()).hexdigest()
    meta = {
        "seq": seq,
        "kind": "chunk",
        "stream": stream_dir.name,
        "source": "https://example.com/live",
        "session": 1,
        "file": wav_p.name,
        "sha256": sha,
        "samples": len(pcm) // 2,
        "duration_s": round(len(pcm) / 32000, 3),
        "received_utc": f"2026-01-01T00:0{seq}:00.000Z",
        "received_utc_unix": 1767225600.0 + seq * 60,
        "start_utc_est": f"2026-01-01T00:0{seq}:00.000Z",
        "end_utc_est": f"2026-01-01T00:0{seq}:01.000Z",
    }
    meta_p = stream_dir / f"{seq:06d}.meta.json"
    meta_p.write_text(json.dumps(meta, indent=1))


def write_gap(stream_dir: Path, seq: int):
    stream_dir.mkdir(parents=True, exist_ok=True)
    gap = {
        "seq": seq,
        "kind": "gap",
        "stream": stream_dir.name,
        "reason": "stalled >30s",
        "from_utc": f"2026-01-01T00:0{seq}:00.000Z",
        "to_utc": f"2026-01-01T00:0{seq}:15.000Z",
        "to_utc_unix": 1767225615.0 + seq * 60,
        "duration_s": 15.0,
    }
    (stream_dir / f"{seq:06d}.gap.json").write_text(json.dumps(gap, indent=1))


def write_transcript(stream_dir: Path, seq: int, text: str = "hello world", wav_sha_override: str | None = None):
    mel_p = stream_dir / f"{seq:06d}.mel.npy"
    mel_bytes = f"fake-mel-{seq}-{text}".encode()
    mel_p.write_bytes(mel_bytes)
    mel_sha = hashlib.sha256(mel_bytes).hexdigest()
    wav_p = stream_dir / f"{seq:06d}.wav"
    wav_sha = wav_sha_override or (hashlib.sha256(wav_p.read_bytes()).hexdigest() if wav_p.exists() else "0" * 64)
    rec = {
        "seq": seq,
        "stream": stream_dir.name,
        "kind": "transcript",
        "wav_sha256": wav_sha,
        "received_utc": f"2026-01-01T00:0{seq}:00.000Z",
        "start_utc_est": f"2026-01-01T00:0{seq}:00.000Z",
        "duration_s": 0.1,
        "words": [{"w": w, "s": 0.0, "e": 0.05, "p": 0.99} for w in text.split()],
        "text": text,
        "mel": mel_p.name,
        "mel_sha256": mel_sha,
        "model": "test-model",
        "context": {"prev_s": 0.0, "next_s": 0.0},
    }
    (stream_dir / f"{seq:06d}.json").write_text(json.dumps(rec, indent=1))


def test_ordering_and_waiting(tmp_path: Path):
    sdir = tmp_path / "aljazeera"
    write_chunk(sdir, 0, b"\x01\x00" * 800)
    write_gap(sdir, 1)
    # seq 3 arrives before seq 2; transcript for seq 2 arrives before chunk 2 is chained
    write_chunk(sdir, 3, b"\x03\x00" * 800)
    write_transcript(sdir, 2, "early transcript")

    app = StreamAppender(sdir)
    try:
        n1 = app.step()
        assert n1 == 3  # genesis + chunk 0 + gap 1
        entries = load(sdir)
        # Should have genesis (n=0), chunk 0 (n=1), gap 1 (n=2). Seq 3 and transcript 2 held back.
        assert [e["kind"] for e in entries] == ["genesis", "chunk", "gap"]
        assert [e.get("seq") for e in entries] == [None, 0, 1]

        # Now chunk 2 arrives, plus transcript for chunk 0
        write_chunk(sdir, 2, b"\x02\x00" * 800)
        write_transcript(sdir, 2, "early transcript")  # update wav_sha256 to match chunk 2
        write_transcript(sdir, 0, "first chunk words")

        n2 = app.step()
        assert n2 == 4  # chunk 2 + chunk 3 + transcript 0 + transcript 2
        entries = load(sdir)
        # Expect chunk 2 (n=3), chunk 3 (n=4), transcript 0 (n=5), transcript 2 (n=6)
        assert [e["kind"] for e in entries] == [
            "genesis", "chunk", "gap", "chunk", "chunk", "transcript", "transcript"
        ]
        assert entries[3]["seq"] == 2
        assert entries[4]["seq"] == 3
        assert entries[5]["seq"] == 0 and entries[5]["chunk_n"] == 1
        assert entries[6]["seq"] == 2 and entries[6]["chunk_n"] == 3

        ok, problems = verify(sdir)
        assert ok, problems

        head = json.loads((sdir / "head.json").read_text())
        assert head["stream"] == "aljazeera"
        assert head["n"] == entries[-1]["n"] == 6
        assert head["hash"] == entries[-1]["hash"]
        assert head["last_seq"] == 3
        assert head["kinds"] == {"chunk": 3, "gap": 1, "transcript": 2}
    finally:
        app.close()


def _build_valid_stream(tmp_path: Path) -> Path:
    sdir = tmp_path / "stream1"
    write_chunk(sdir, 0, b"\x10\x00" * 800)
    write_gap(sdir, 1)
    write_chunk(sdir, 2, b"\x20\x00" * 800)
    write_transcript(sdir, 0, "zero")
    write_transcript(sdir, 2, "two")
    app = StreamAppender(sdir)
    app.step()
    app.close()
    ok, problems = verify(sdir)
    assert ok, problems
    return sdir


def test_tamper_edit_wav_byte(tmp_path: Path):
    sdir = _build_valid_stream(tmp_path)
    wav0 = sdir / "000000.wav"
    data = bytearray(wav0.read_bytes())
    data[-1] ^= 0xFF
    wav0.write_bytes(bytes(data))

    ok, problems = verify(sdir)
    assert not ok
    assert any("n=1" in p and "seq=0" in p and "wav_sha256" in p for p in problems), problems


def test_tamper_edit_meta_received_utc(tmp_path: Path):
    sdir = _build_valid_stream(tmp_path)
    meta2 = sdir / "000002.meta.json"
    m = json.loads(meta2.read_text())
    m["received_utc"] = "2099-01-01T00:00:00.000Z"
    meta2.write_text(json.dumps(m, indent=1))

    ok, problems = verify(sdir)
    assert not ok
    assert any("n=3" in p and "seq=2" in p and "meta_sha256" in p for p in problems), problems


def test_tamper_delete_middle_line(tmp_path: Path):
    sdir = _build_valid_stream(tmp_path)
    chain_p = sdir / "chain.jsonl"
    lines = chain_p.read_bytes().splitlines(keepends=True)
    # Delete n=2 (gap seq=1)
    del lines[2]
    chain_p.write_bytes(b"".join(lines))

    ok, problems = verify(sdir)
    assert not ok
    assert any("n=2" in p and "seq=2" in p for p in problems), problems


def test_tamper_swap_two_lines(tmp_path: Path):
    sdir = _build_valid_stream(tmp_path)
    chain_p = sdir / "chain.jsonl"
    lines = chain_p.read_bytes().splitlines(keepends=True)
    # Swap n=4 (transcript seq=0) and n=5 (transcript seq=2)
    lines[4], lines[5] = lines[5], lines[4]
    chain_p.write_bytes(b"".join(lines))

    ok, problems = verify(sdir)
    assert not ok
    assert any("n=4" in p and "seq=2" in p for p in problems), problems


def test_tamper_recompute_single_line_hash(tmp_path: Path):
    sdir = _build_valid_stream(tmp_path)
    entries = load(sdir)
    # Modify n=2 (gap seq=1) and recompute only n=2's hash
    entries[2]["to_utc"] = "2026-01-01T00:01:59.000Z"
    entries[2]["hash"] = entry_hash(entries[2])
    (sdir / "chain.jsonl").write_bytes(
        b"".join((canonical_json(e) + "\n").encode() for e in entries)
    )

    ok, problems = verify(sdir)
    assert not ok
    # n=3's prev link no longer matches n=2's new hash
    assert any("n=3" in p and "seq=2" in p and "prev link mismatch" in p for p in problems), problems


def test_tamper_delete_transcript_json(tmp_path: Path):
    sdir = _build_valid_stream(tmp_path)
    (sdir / "000002.json").unlink()

    ok, problems = verify(sdir)
    assert not ok
    assert any("n=5" in p and "seq=2" in p and "000002.json" in p for p in problems), problems


def test_transcript_wav_sha_binding(tmp_path: Path):
    # 1. Appender halts if transcript claims wrong wav_sha256
    sdir = tmp_path / "tx_mismatch"
    write_chunk(sdir, 0, b"\x11\x22" * 800)
    write_transcript(sdir, 0, "wrong audio", wav_sha_override="f" * 64)

    app = StreamAppender(sdir)
    try:
        app.step()
        assert app.halted is True
        entries = load(sdir)
        assert [e["kind"] for e in entries] == ["genesis", "chunk"]
    finally:
        app.close()

    # 2. Verify detects if a chained transcript's wav_sha256 does not match its chunk's wav_sha256
    valid_dir = _build_valid_stream(tmp_path)
    tx_p = valid_dir / "000002.json"
    tx_obj = json.loads(tx_p.read_text())
    tx_obj["wav_sha256"] = "e" * 64
    tx_bytes = json.dumps(tx_obj, indent=1).encode()
    tx_p.write_bytes(tx_bytes)

    # Even if attacker updates json_sha256 and recomputes hash on n=5 (the last entry), verify catches wav_sha256 mismatch
    entries = load(valid_dir)
    entries[5]["json_sha256"] = hashlib.sha256(tx_bytes).hexdigest()
    entries[5]["hash"] = entry_hash(entries[5])
    (valid_dir / "chain.jsonl").write_bytes(b"".join((canonical_json(e) + "\n").encode() for e in entries))

    ok, problems = verify(valid_dir)
    assert not ok
    assert any("n=5" in p and "seq=2" in p and "transcript wav_sha256" in p for p in problems), problems


def test_restart_repairs_truncated_line_and_is_idempotent(tmp_path: Path):
    sdir = tmp_path / "restart_stream"
    write_chunk(sdir, 0, b"\x01\x02" * 800)
    write_chunk(sdir, 1, b"\x03\x04" * 800)

    app = StreamAppender(sdir)
    app.step()
    app.close()

    before = load(sdir)
    assert len(before) == 3  # genesis, chunk 0, chunk 1

    # Simulate partial write at the end of chain.jsonl (no trailing newline / broken JSON)
    with open(sdir / "chain.jsonl", "ab") as f:
        f.write(b'{"n":3,"prev":"abc","kind":"chunk"')

    # Also add chunk 2 so it has new work after repairing
    write_chunk(sdir, 2, b"\x05\x06" * 800)

    app2 = StreamAppender(sdir)
    app2.step()
    app2.close()

    after = load(sdir)
    assert len(after) == 4
    assert after[3]["n"] == 3
    assert after[3]["seq"] == 2
    assert after[3]["prev"] == before[-1]["hash"]
    ok, problems = verify(sdir)
    assert ok, problems

    # Restarting twice with no changes appends nothing
    for _ in range(2):
        app_noop = StreamAppender(sdir)
        assert app_noop.step() == 0
        app_noop.close()
    assert load(sdir) == after


def test_exclusive_flock_refuses_second_appender(tmp_path: Path):
    sdir = tmp_path / "locked_stream"
    write_chunk(sdir, 0)

    app1 = StreamAppender(sdir)
    try:
        with pytest.raises(BlockingIOError):
            StreamAppender(sdir)
    finally:
        app1.close()

    # After closing, a new appender succeeds
    app2 = StreamAppender(sdir)
    app2.close()


def test_proof_and_check_proof_with_anchored_head_n(tmp_path: Path):
    sdir = _build_valid_stream(tmp_path)
    anchored_head = json.loads((sdir / "head.json").read_text())
    anchored_n = anchored_head["n"]
    anchored_hash = anchored_head["hash"]

    # Append more entries after the anchored head
    write_chunk(sdir, 3, b"\x30\x00" * 800)
    write_transcript(sdir, 3, "three")
    app = StreamAppender(sdir)
    app.step()
    app.close()

    new_entries = load(sdir)
    new_head_hash = new_entries[-1]["hash"]
    assert new_entries[-1]["n"] > anchored_n
    assert new_head_hash != anchored_hash

    # Proof targeting the historical anchored head_n succeeds against anchored_hash and fails against new_head_hash
    prf_anchored = proof(sdir, 0, head_n=anchored_n)
    assert prf_anchored["seq"] == 0
    assert prf_anchored["chunk_n"] == 1
    assert prf_anchored["head_n"] == anchored_n
    assert prf_anchored["head_hash"] == anchored_hash
    assert check_proof(prf_anchored, anchored_hash)
    assert not check_proof(prf_anchored, new_head_hash)

    # Default head_n=None targets the current head
    prf_latest = proof(sdir, 0)
    assert prf_latest["head_n"] == new_entries[-1]["n"]
    assert check_proof(prf_latest, new_head_hash)
    assert not check_proof(prf_latest, anchored_hash)

    # Invalid head_n (before chunk's n=1 or beyond log length) raises ValueError
    with pytest.raises(ValueError):
        proof(sdir, 0, head_n=0)
    with pytest.raises(ValueError):
        proof(sdir, 0, head_n=999)

    # Fails if any entry in the proof is altered
    tampered = json.loads(json.dumps(prf_anchored))
    tampered["entries"][1]["to_utc"] = "2099-01-01T00:00:00.000Z"
    assert not check_proof(tampered, anchored_hash)

    # Even if the attacker recomputes that single entry's hash, the link breaks
    tampered["entries"][1]["hash"] = entry_hash(tampered["entries"][1])
    assert not check_proof(tampered, anchored_hash)


def test_sha_mismatch_halts_only_bad_stream(tmp_path: Path):
    bad_dir = tmp_path / "bad_stream"
    good_dir = tmp_path / "good_stream"

    write_chunk(bad_dir, 0, b"\x01\x00" * 800)
    # Chunk 1 has a mismatch between wav and meta sha256
    write_chunk(bad_dir, 1, b"\x02\x00" * 800, wav_sha_override="0" * 64)

    write_chunk(good_dir, 0, b"\x0a\x00" * 800)
    write_chunk(good_dir, 1, b"\x0b\x00" * 800)

    daemon = ChainDaemon(tmp_path)
    try:
        daemon.step()

        # bad_stream chained genesis + seq 0, then halted before seq 1
        bad_entries = load(bad_dir)
        assert [e["kind"] for e in bad_entries] == ["genesis", "chunk"]
        assert bad_entries[-1]["seq"] == 0
        assert daemon.appenders[bad_dir].halted is True

        # good_stream chained genesis + seq 0 + seq 1
        good_entries = load(good_dir)
        assert [e["kind"] for e in good_entries] == ["genesis", "chunk", "chunk"]
        assert good_entries[-1]["seq"] == 1
        ok, problems = verify(good_dir)
        assert ok, problems

        # Adding seq 2 to good_stream continues to work on subsequent steps
        write_chunk(good_dir, 2, b"\x0c\x00" * 800)
        daemon.step()
        assert load(good_dir)[-1]["seq"] == 2
    finally:
        daemon.close()


def test_corrupt_middle_line_in_one_stream_does_not_crash_daemon(tmp_path: Path, capsys):
    corrupt_dir = tmp_path / "a_corrupt"
    healthy_dir = tmp_path / "b_healthy"

    write_chunk(corrupt_dir, 0, b"\x01\x00" * 800)
    write_chunk(corrupt_dir, 1, b"\x02\x00" * 800)
    app = StreamAppender(corrupt_dir)
    app.step()
    app.close()

    # Corrupt middle line (n=1) of corrupt_dir/chain.jsonl
    lines = (corrupt_dir / "chain.jsonl").read_bytes().splitlines(keepends=True)
    lines[1] = b"{corrupt middle json\n"
    (corrupt_dir / "chain.jsonl").write_bytes(b"".join(lines))

    write_chunk(healthy_dir, 0, b"\x0a\x00" * 800)
    write_chunk(healthy_dir, 1, b"\x0b\x00" * 800)

    daemon = ChainDaemon(tmp_path)
    try:
        daemon.step()
        err1 = capsys.readouterr().err
        assert "a_corrupt" in err1
        assert corrupt_dir in daemon.failed_dirs

        # Healthy stream was chained properly
        ok, problems = verify(healthy_dir)
        assert ok, problems
        assert len(load(healthy_dir)) == 3

        # Stepping again does not re-log the failed stream
        write_chunk(healthy_dir, 2, b"\x0c\x00" * 800)
        daemon.step()
        err2 = capsys.readouterr().err
        assert "a_corrupt" not in err2
        assert len(load(healthy_dir)) == 4
    finally:
        daemon.close()


def test_cli_verify_exit_codes(tmp_path: Path):
    sdir = _build_valid_stream(tmp_path)
    res_ok = subprocess.run([sys.executable, "-m", "ingest.chain", "--verify", str(sdir)], capture_output=True, text=True)
    assert res_ok.returncode == 0
    assert "OK" in res_ok.stdout

    (sdir / "000000.wav").unlink()
    res_bad = subprocess.run([sys.executable, "-m", "ingest.chain", "--verify", str(sdir)], capture_output=True, text=True)
    assert res_bad.returncode == 1
    assert "BROKEN" in res_bad.stdout
