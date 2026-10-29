"""Tests for 'Hear what they cut' (F1) audio restoration, before/after context (F2), and HTTP routes."""
import json
import struct
import threading
import urllib.error
import urllib.request
import wave
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from verdict.restore import RestoreUnavailable, build_restored, edge_context
from verdict.service import VerdictManager, make_handler

SR = 16000


def _write_synth_wav(path: Path, samples: list[int], sr: int = SR) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pcm = struct.pack(f"<{len(samples)}h", *samples)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(pcm)


def _read_samples(path: Path) -> list[int]:
    with wave.open(str(path), "rb") as wf:
        raw = wf.readframes(wf.getnframes())
    n = len(raw) // 2
    return list(struct.unpack(f"<{n}h", raw))


def _write_chunk_json(
    path: Path,
    words: list[dict],
    duration_s: float = 4.0,
    start_utc: str = "2026-10-28T18:53:51Z",
    received_utc: str = "2026-10-28T18:54:11Z",
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "duration_s": duration_s,
                "start_utc_est": start_utc,
                "received_utc": received_utc,
                "words": words,
            }
        )
    )


def test_build_restored_samples_multicut_cross_seq_and_missing(tmp_path: Path):
    wroot = tmp_path / "witness"
    sdir = wroot / "aje"

    seq4_samples = [4000 + (i % 1000) for i in range(4 * SR)]
    _write_synth_wav(sdir / "000004.wav", seq4_samples)
    _write_chunk_json(
        sdir / "000004.json",
        words=[
            {"w": "first", "s": 0.5, "e": 0.9},
            {"w": "excised_one", "s": 1.0, "e": 1.5},
            {"w": "middle", "s": 2.0, "e": 2.5},
            {"w": "cross_a", "s": 3.5, "e": 3.95},
        ],
        duration_s=4.0,
    )

    seq5_samples = [5000 + (i % 1000) for i in range(4 * SR)]
    _write_synth_wav(sdir / "000005.wav", seq5_samples)
    _write_chunk_json(
        sdir / "000005.json",
        words=[
            {"w": "cross_b", "s": 0.05, "e": 0.45},
            {"w": "tail", "s": 1.0, "e": 1.8},
        ],
        duration_s=4.0,
    )

    clip_wav = tmp_path / "clip.wav"
    clip_samples = [1111] * (3 * SR)
    _write_synth_wav(clip_wav, clip_samples)

    removed = [
        {"after_clip_s": 2.0, "seq": 4, "src_s": [3.5, 4.5], "words": "cross_a cross_b"},
        {"after_clip_s": 1.0, "seq": 4, "src_s": [1.0, 1.5], "words": "excised_one"},
    ]

    out_wav = tmp_path / "restored.wav"
    manifest = build_restored(clip_wav, wroot, "aje", removed, out_wav)

    assert pytest.approx(manifest["duration_s"], abs=1e-4) == 3.0 + 1.5 + 2 * 0.24
    out_samples = _read_samples(out_wav)
    assert len(out_samples) == int(round(4.98 * SR))

    sil_n = int(round(0.12 * SR))
    idx = 0
    assert out_samples[idx : idx + SR] == [1111] * SR
    idx += SR
    assert out_samples[idx : idx + sil_n] == [0] * sil_n
    idx += sil_n
    assert out_samples[idx : idx + int(0.5 * SR)] == seq4_samples[int(1.0 * SR) : int(1.5 * SR)]
    idx += int(0.5 * SR)
    assert out_samples[idx : idx + sil_n] == [0] * sil_n
    idx += sil_n
    assert out_samples[idx : idx + SR] == [1111] * SR
    idx += SR
    assert out_samples[idx : idx + sil_n] == [0] * sil_n
    idx += sil_n
    expected_cross = seq4_samples[int(3.5 * SR) : int(4.0 * SR)] + seq5_samples[0 : int(0.5 * SR)]
    assert out_samples[idx : idx + SR] == expected_cross
    idx += SR
    assert out_samples[idx : idx + sil_n] == [0] * sil_n
    idx += sil_n
    assert out_samples[idx : idx + SR] == [1111] * SR

    restored_segs = [s for s in manifest["segments"] if s["kind"] == "restored"]
    assert len(restored_segs) == 2
    assert restored_segs[0]["after_clip_s"] == 1.0
    assert restored_segs[0]["cut_index"] == 1
    assert restored_segs[0]["words"][0]["w"] == "excised_one"
    assert pytest.approx(restored_segs[0]["words"][0]["t0"], abs=1e-3) == 1.12
    assert pytest.approx(restored_segs[0]["words"][0]["t1"], abs=1e-3) == 1.62

    assert restored_segs[1]["after_clip_s"] == 2.0
    assert restored_segs[1]["cut_index"] == 0
    assert [w["w"] for w in restored_segs[1]["words"]] == ["cross_a", "cross_b"]
    assert pytest.approx(restored_segs[1]["words"][0]["t0"], abs=1e-3) == 2.86
    assert pytest.approx(restored_segs[1]["words"][0]["t1"], abs=1e-3) == 2.86 + 0.55
    assert pytest.approx(restored_segs[1]["words"][1]["t0"], abs=1e-3) == 2.86 + 0.55
    assert pytest.approx(restored_segs[1]["words"][1]["t1"], abs=1e-3) == 3.86

    bad_clip = tmp_path / "bad_sr.wav"
    _write_synth_wav(bad_clip, [0] * 8000, sr=8000)
    with pytest.raises(ValueError, match="sample format mismatch"):
        build_restored(bad_clip, wroot, "aje", removed, tmp_path / "never.wav")

    with pytest.raises(RestoreUnavailable, match="seq 99"):
        build_restored(
            clip_wav,
            wroot,
            "aje",
            [{"after_clip_s": 0.5, "seq": 99, "src_s": [0.0, 0.5], "words": "gone"}],
            tmp_path / "never2.wav",
        )


def test_edge_context_crosses_seq_and_stops_at_gap_stream_start_live_edge(tmp_path: Path):
    wroot = tmp_path / "witness"
    sdir = wroot / "aje"

    (sdir / "000003.gap.json").parent.mkdir(parents=True, exist_ok=True)
    (sdir / "000003.gap.json").write_text(
        json.dumps(
            {
                "seq": 3,
                "kind": "gap",
                "stream": "aje",
                "reason": "stream ended",
                "from_utc": "2026-10-28T18:53:57.409Z",
                "to_utc": "2026-10-28T18:54:07.734Z",
                "duration_s": 10.325,
            }
        )
    )

    _write_synth_wav(sdir / "000004.wav", [100] * (10 * SR))
    _write_chunk_json(
        sdir / "000004.json",
        words=[
            {"w": "early4", "s": 1.0, "e": 2.0},
            {"w": "clip4", "s": 5.0, "e": 9.0},
            {"w": "late4", "s": 9.2, "e": 9.8},
        ],
        duration_s=10.0,
    )
    _write_synth_wav(sdir / "000005.wav", [200] * (10 * SR))
    _write_chunk_json(
        sdir / "000005.json",
        words=[
            {"w": "early5", "s": 0.5, "e": 1.5},
            {"w": "mid5", "s": 3.0, "e": 6.0},
            {"w": "after5", "s": 7.0, "e": 8.0},
        ],
        duration_s=10.0,
    )

    pieces = [
        {"clip_s": [0.0, 5.0], "seq": 4, "src_s": [4.0, 9.0]},
        {"clip_s": [5.0, 8.5], "seq": 5, "src_s": [3.0, 6.5]},
    ]
    ctx = edge_context(wroot, "aje", pieces, window_s=15.0)
    assert [w["w"] for w in ctx["before"]["words"]] == ["early4"]
    assert ctx["before"]["stops"]["kind"] == "gap"
    assert ctx["before"]["stops"]["from_utc"] == "2026-10-28T18:53:57.409Z"
    assert ctx["before"]["audio"] == [{"seq": 4, "src_s": [0.0, 4.0]}]

    assert [w["w"] for w in ctx["after"]["words"]] == ["after5"]
    assert ctx["after"]["stops"] == {"kind": "live_edge"}
    assert ctx["after"]["audio"] == [{"seq": 5, "src_s": [6.5, 10.0]}]

    ctx_cross = edge_context(wroot, "aje", [{"clip_s": [0.0, 3.0], "seq": 5, "src_s": [2.0, 5.0]}], window_s=10.0)
    assert ctx_cross["before"]["stops"] is None
    assert [w["w"] for w in ctx_cross["before"]["words"]] == ["clip4", "late4", "early5"]
    assert ctx_cross["before"]["audio"] == [
        {"seq": 4, "src_s": [2.0, 10.0]},
        {"seq": 5, "src_s": [0.0, 2.0]},
    ]

    _write_synth_wav(sdir / "000000.wav", [50] * (5 * SR))
    _write_chunk_json(sdir / "000000.json", words=[{"w": "genesis", "s": 0.5, "e": 1.5}], duration_s=5.0)
    ctx_start = edge_context(wroot, "aje", [{"clip_s": [0.0, 2.0], "seq": 0, "src_s": [2.0, 4.0]}], window_s=15.0)
    assert ctx_start["before"]["stops"] == {"kind": "stream_start"}
    assert [w["w"] for w in ctx_start["before"]["words"]] == ["genesis"]


def test_service_restored_and_context_routes_range_and_404(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("WT_FETCH_ALLOW_HOSTS", "127.0.0.1:8791")
    monkeypatch.setenv("WT_CLIPS_ROOT", str(tmp_path / "clips"))
    wroot = tmp_path / "witness"
    vdir = tmp_path / "verdicts"
    sdir = wroot / "aje"

    sdir.mkdir(parents=True, exist_ok=True)
    (sdir / "000003.gap.json").write_text(
        json.dumps(
            {
                "seq": 3,
                "kind": "gap",
                "stream": "aje",
                "from_utc": "2026-10-28T18:53:57.409Z",
                "to_utc": "2026-10-28T18:54:07.734Z",
                "duration_s": 10.325,
            }
        )
    )
    _write_synth_wav(sdir / "000004.wav", [3210] * (20 * SR))
    _write_chunk_json(
        sdir / "000004.json",
        words=[
            {"w": "before_word", "s": 0.5, "e": 1.5},
            {"w": "kept_one", "s": 5.7, "e": 8.9},
            {"w": "are", "s": 8.98, "e": 9.10},
            {"w": "through", "s": 9.10, "e": 9.26},
            {"w": "the", "s": 9.26, "e": 9.40},
            {"w": "roof,", "s": 9.40, "e": 9.68},
            {"w": "my", "s": 9.68, "e": 9.90},
            {"w": "kept_two", "s": 9.95, "e": 12.1},
            {"w": "after_word", "s": 13.0, "e": 14.0},
        ],
        duration_s=20.0,
        start_utc="2026-10-28T18:53:51Z",
        received_utc="2026-10-28T18:54:11Z",
    )

    clip_wav = tmp_path / "clips" / "c1" / "clip.wav"
    _write_synth_wav(clip_wav, [1234] * (6 * SR))

    verdict_doc = {
        "verdict_id": "v_restore_test_01",
        "job_id": "j1",
        "headline": '0.9 s removed at 0:03: the record says "… are through the roof, my …"',
        "detector_kind": "full",
        "clip": {"sha256": "aa" * 32, "duration_s": 6.0, "source_url": "http://127.0.0.1:8791/c.mp4"},
        "detection": {
            "witness": {
                "verdict": "cut",
                "stream": "aje",
                "pieces": [
                    {"clip_s": [0.0, 3.0], "seq": 4, "src_s": [5.7, 8.98]},
                    {"clip_s": [3.0, 6.0], "seq": 4, "src_s": [9.90, 12.1]},
                ],
                "removed": [
                    {"after_clip_s": 3.0, "seq": 4, "src_s": [8.98, 9.90], "words": "are through the roof, my"}
                ],
                "inserted": [],
            }
        },
        "label": None,
        "proofs": [{"seq": 4, "chunk_received_utc": "2026-10-28T18:54:11Z", "proof_ok": True, "anchor": None}],
        "coverage": {"complete": True, "gaps": [], "notes": []},
    }

    mgr = VerdictManager(witness_root=wroot, verdicts_dir=vdir, start_worker=False)
    sub = mgr.submit("http://127.0.0.1:8791/c.mp4")
    jid = sub["id"]
    mgr._set_status(jid, "done", clip_wav=str(clip_wav), result=verdict_doc, error=None)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(mgr))
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        # 1. HTML page shows "▶ Hear what they cut" (without time suffix), received UTC quote header, and chunk-relative SVG axis
        with urllib.request.urlopen(f"{base}/v/{jid}") as resp:
            page_html = resp.read().decode("utf-8")
        assert ">▶ Hear what they cut</button>" in page_html
        assert "chunk #4 · received 18:54:11 UTC" in page_html
        assert "18:53:51" not in page_html
        assert "#4 +1.7 s" in page_html
        assert "#4 +16.1 s" in page_html
        assert "◀ 15 s before the clip" in page_html
        assert "15 s after the clip ▶" in page_html
        assert "witness gap 18:53:57–18:54:07 UTC" in page_html
        assert "live edge" in page_html
        assert "before_word" in page_html
        assert "after_word" in page_html
        # Pre-built restored.wav during GET /v/<id>
        assert (clip_wav.parent / "restored.wav").exists()

        # 2. /v/<id>/restored.json has contiguous [t0, t1) word timings and /v/<id>/restored.wav supports 206 Range
        with urllib.request.urlopen(f"{base}/v/{jid}/restored.json") as resp:
            manifest = json.loads(resp.read().decode("utf-8"))
        assert pytest.approx(manifest["duration_s"], abs=1e-3) == 6.0 + 0.92 + 0.24
        r_seg = [s for s in manifest["segments"] if s["kind"] == "restored"][0]
        r_words = r_seg["words"]
        assert [w["w"] for w in r_words] == ["are", "through", "the", "roof,", "my"]
        for i in range(len(r_words) - 1):
            assert r_words[i]["t1"] == r_words[i + 1]["t0"]
            assert r_words[i]["t1"] > r_words[i]["t0"]
        assert r_words[0]["t0"] == r_seg["t0"]
        assert r_words[-1]["t1"] == r_seg["t1"]

        req_range = urllib.request.Request(f"{base}/v/{jid}/restored.wav", headers={"Range": "bytes=0-99"})
        with urllib.request.urlopen(req_range) as resp:
            assert resp.status == 206
            assert resp.headers.get("Content-Range", "").startswith("bytes 0-99/")
            assert len(resp.read()) == 100

        # 3. /v/<id>/context/before.wav and after.wav with Range
        req_ctx = urllib.request.Request(f"{base}/v/{jid}/context/before.wav", headers={"Range": "bytes=0-63"})
        with urllib.request.urlopen(req_ctx) as resp:
            assert resp.status == 206
            assert len(resp.read()) == 64

        # 4. Remove witness wav -> page falls back to "Hear the cut" + quiet note, and /restored.wav returns 404 JSON
        (sdir / "000004.wav").unlink()
        (clip_wav.parent / "restored.wav").unlink()
        (clip_wav.parent / "restored.json").unlink()

        with urllib.request.urlopen(f"{base}/v/{jid}") as resp:
            fallback_html = resp.read().decode("utf-8")
        assert "▶ Hear the cut" in fallback_html
        assert "Record audio not on this server: the words come from the hash-chained transcript" in fallback_html

        for ep in ("restored.wav", "restored.json", "context/before.wav"):
            with pytest.raises(urllib.error.HTTPError) as exc_info:
                urllib.request.urlopen(f"{base}/v/{jid}/{ep}")
            assert exc_info.value.code == 404
            err_body = json.loads(exc_info.value.read().decode("utf-8"))
            assert "error" in err_body
    finally:
        srv.shutdown()
        srv.server_close()
        mgr.close()


def test_detector_words_trim_loose_whisper_edges():
    from verdict.restore import select_span_word_indices
    # "rent" starts 10 ms before a 10 ms-precise S4 cut ends, so timing alone pulls it in
    wl = [{"w": "roof,", "s": 9.30, "e": 9.60}, {"w": "my", "s": 9.60, "e": 9.95}, {"w": "rent", "s": 9.98, "e": 10.30}]
    assert [wl[i]["w"] for i in select_span_word_indices(wl, 9.01, 10.01)] == ["roof,", "my", "rent"]
    assert [wl[i]["w"] for i in select_span_word_indices(wl, 9.01, 10.01, "the roof my")] == ["roof,", "my"]
    # a detector word list that matches nothing never empties the span
    assert len(select_span_word_indices(wl, 9.01, 10.01, "zebra")) == 3
