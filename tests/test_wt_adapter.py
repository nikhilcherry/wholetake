import json
import wave
from pathlib import Path

import numpy as np
import pytest

from verdict import detect, wt_adapter

REPO = Path(__file__).resolve().parents[1]
SPEECH = REPO / "testdata" / "speech.wav"  # Shreyas's 48 kHz speech sample
SR = 16000


def _write_wav(path: Path, x: np.ndarray) -> None:
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SR)
        wf.writeframes(np.clip(x * 32767, -32768, 32767).astype("<i2").tobytes())


def _speech16k() -> np.ndarray:
    from scipy.signal import resample_poly
    with wave.open(str(SPEECH), "rb") as wf:
        assert wf.getframerate() == 48000 and wf.getnchannels() == 1
        x = np.frombuffer(wf.readframes(wf.getnframes()), "<i2").astype(np.float64) / 32768.0
    return resample_poly(x, 1, 3).astype(np.float32)


def _witness(root: Path, x: np.ndarray, chunk_s: float = 10.0, gap_at: int | None = None) -> Path:
    """N1-shaped stream 'talk': 10 s chunks with empty transcripts (acoustic-only), optional gap file."""
    sdir = root / "talk"
    sdir.mkdir(parents=True)
    n = int(chunk_s * SR)
    for seq in range(len(x) // n):
        if seq == gap_at:
            (sdir / f"{seq:06d}.gap.json").write_text(json.dumps({"seq": seq, "kind": "gap"}))
            continue
        _write_wav(sdir / f"{seq:06d}.wav", x[seq * n:(seq + 1) * n])
        (sdir / f"{seq:06d}.json").write_text(json.dumps({"seq": seq, "duration_s": chunk_s, "words": []}))
    return sdir


def test_offsets_and_splits_never_cross_a_chunk():
    offsets = [(4, 0.0, 10.0), (5, 10.0, 10.0)]
    assert wt_adapter.to_chunk(offsets, 3.5) == (4, 3.5)
    assert wt_adapter.to_chunk(offsets, 12.25) == (5, 2.25)
    ps = wt_adapter.split_piece(offsets, 0.0, 8.0, 6.0, 14.0, 0.93)
    assert [p["seq"] for p in ps] == [4, 5]
    assert ps[0]["src_s"] == [6.0, 10.0] and ps[1]["src_s"] == [0.0, 4.0]
    assert ps[0]["clip_s"] == [0.0, 4.0] and ps[1]["clip_s"] == [4.0, 8.0]
    rs = wt_adapter.split_removed(offsets, 7.0, 9.5, 10.75, "not at all")
    assert [(r["seq"], r["src_s"]) for r in rs] == [(4, [9.5, 10.0]), (5, [0.0, 0.75])]
    assert rs[0]["words"] == "not at all" and rs[1]["words"] == ""  # words once, on the first part


def test_contiguous_run_stops_at_gaps(tmp_path: Path):
    x = np.zeros(SR * 60, np.float32)
    sdir = _witness(tmp_path, x, gap_at=2)
    assert wt_adapter.contiguous_run(sdir, [4]) == [3, 4, 5]  # seq 2 is a gap: never stitched across
    assert wt_adapter.contiguous_run(sdir, [1]) == [0, 1]
    assert wt_adapter.contiguous_run(sdir, [1, 3]) == []  # the match itself straddles a gap


@pytest.mark.skipif(not SPEECH.exists(), reason="needs testdata/speech.wav")
def test_s4_finds_a_cut_across_a_chunk_boundary(tmp_path: Path, monkeypatch):
    from wholetake import asr
    monkeypatch.setattr(asr, "MODEL", "none")  # acoustic-only: no Whisper in the unit tests
    x = _speech16k()
    _witness(tmp_path / "w", x)
    # clip = record [6.0, 13.0) + [14.2, 19.0): 1.2 s removed at clip 7.0, record 13.0-14.2 = seq 1 [3.0, 4.2]
    clip = np.concatenate([x[int(6.0 * SR):int(13.0 * SR)], x[int(14.2 * SR):int(19.0 * SR)]])
    clip_wav = tmp_path / "clip.wav"
    _write_wav(clip_wav, clip)
    base = {"verdict": "cut", "stream": "talk", "transcript_clip": "",
            "pieces": [{"seq": 0, "clip_s": [0, 4], "src_s": [6, 10]}, {"seq": 1, "clip_s": [4, 11.8], "src_s": [0, 9]}]}

    w = wt_adapter.acoustic_witness(clip_wav, tmp_path / "w", base)

    assert w is not None and w["verdict"] == "cut" and w["stream"] == "talk"
    (rem,) = w["removed"]
    assert rem["seq"] == 1 and rem["src_s"] == pytest.approx([3.0, 4.2], abs=0.05)
    assert rem["after_clip_s"] == pytest.approx(7.0, abs=0.05)
    assert all(p["audio_score"] >= 0.8 for p in w["pieces"])
    assert {p["seq"] for p in w["pieces"]} == {0, 1}  # the first piece crosses 10 s and gets split
    for p in w["pieces"]:
        assert 0 <= p["src_s"][0] < p["src_s"][1] <= 10.0 + 1e-6
    det = {"detector": {"name": "wt-detect", "version": "t"}, "clip": {"duration_s": 11.8, "sha256": "0" * 64},
           "stamp": None, "witness": w}
    detect.validate_detection(det, tmp_path / "w")  # the result satisfies CONTRACTS.md as the service checks it
    assert detect.detector_kind(det) != "text-only"


@pytest.mark.skipif(not SPEECH.exists(), reason="needs testdata/speech.wav")
def test_unrelated_audio_keeps_the_text_result(tmp_path: Path, monkeypatch):
    from wholetake import asr
    monkeypatch.setattr(asr, "MODEL", "none")
    x = _speech16k()
    _witness(tmp_path / "w", x[: SR * 20])
    noise = np.random.default_rng(1).normal(0, 0.1, SR * 8).astype(np.float32)
    clip_wav = tmp_path / "clip.wav"
    _write_wav(clip_wav, noise)
    base = {"verdict": "cut", "stream": "talk", "pieces": [{"seq": 0, "clip_s": [0, 8], "src_s": [0, 8]}]}
    assert wt_adapter.acoustic_witness(clip_wav, tmp_path / "w", base) is None


def test_stamp_null_without_a_venue_key(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("ROOMSTAMP_KEY", raising=False)
    monkeypatch.setenv("WT_VENUE_KEY", str(tmp_path / "missing.key"))
    assert wt_adapter.roomstamp_stamp(tmp_path / "clip.wav") is None


def test_stamp_maps_wt_detect_verdict(tmp_path: Path, monkeypatch):
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs" / "session_1.jsonl").write_text("{}\n")
    monkeypatch.setenv("WT_VENUE", str(tmp_path / "logs"))
    monkeypatch.setenv("ROOMSTAMP_KEY", "00" * 32)
    from roomstamp import wt_detect
    doc = {"status": "ok", "pending_layers": [],
           "verdict": {"recorded_here": "verified", "recorded_at_unix": 1793430620.25, "headline": "Recorded at stage.",
                       "venue": {"name": "HH Goa demo stage"},
                       "edits": [{"kind": "removed", "source": "l1", "clip_t": 12.04, "removed_s": 0.3},
                                 {"kind": "removed", "source": "wt", "clip_t": 3.0, "removed_s": 0.5}]},
            "layers": {"l1": {"status": "present", "p_value": 1e-6}, "l2": {"status": "skipped"}}}
    monkeypatch.setattr(wt_detect, "run", lambda *a, **k: doc)
    s = wt_adapter.roomstamp_stamp(tmp_path / "clip.wav")
    assert s["present"] is True and s["venue"] == "HH Goa demo stage"
    assert s["layer1_score"] == pytest.approx(1.0, abs=1e-5) and s["layer2_score"] is None
    assert s["recorded_at_utc"].startswith("2026-") and s["recorded_at_utc"].endswith("Z")
    assert s["cuts"] == [{"at_clip_s": 12.04, "removed_s": 0.3}]  # stamp cuts come from the stamp layers only
