"""Reproducer test for F275: _tempo_from_text corrupted by internal cuts within a single SW piece."""
from __future__ import annotations

import sys
from unittest.mock import patch

from wholetake import asr, audio, match, record, selftest


def test_sw_pieces_and_tempo_from_text_direction() -> None:
    # Construct a witness transcript of 40 distinct content words at 2 words/s (0.5 s per word, 0..20 s)
    vocab = [
        "quantum", "lattice", "photon", "harmonic", "resonator", "coherent", "manifold", "spectrum",
        "gradient", "tensor", "soliton", "dispersion", "boundary", "operator", "eigenvalue", "dynamics",
        "entangled", "polariton", "cavity", "superfluid", "vortex", "topology", "symmetry", "gauge",
        "fermion", "boson", "plasmon", "exciton", "magnon", "phonon", "skyrmion", "holonomy",
        "instantons", "monopole", "curvature", "geodesic", "invariant", "lagrangian", "hamiltonian", "action",
    ]
    ww: list[asr.Word] = []
    for i, tok in enumerate(vocab):
        t0 = i * 0.5
        ww.append(asr.Word(i=i, tok=tok, raw=tok, start=t0, end=t0 + 0.45, prob=0.99))

    # Clip has a true tempo speed-up of 1.25x AND an internal cut removing words 14..21 (4.0 s of witness)
    true_tempo = 1.25
    kept_indices = list(range(0, 14)) + list(range(22, 36))
    cw: list[asr.Word] = []
    clip_t = 0.0
    for ci, wi in enumerate(kept_indices):
        dur_c = 0.5 / true_tempo
        cw.append(asr.Word(i=ci, tok=ww[wi].tok, raw=ww[wi].raw, start=clip_t, end=clip_t + 0.45 / true_tempo, prob=0.99))
        clip_t += dur_c

    tp = match._sw_pieces(cw, ww)
    assert len(tp) == 1, f"expected 1 bridged SW piece, got {len(tp)}"
    alpha_t = match._tempo_from_text(tp, cw, ww)
    assert alpha_t is not None, "expected non-None alpha_t"
    assert abs(alpha_t - true_tempo) <= 0.03, (
        f"_tempo_from_text corrupted by cut inside single SW piece: true tempo={true_tempo}, "
        f"got alpha_t={alpha_t} (tp pieces={len(tp)})"
    )


def test_case_tempo_cut_with_asr_words() -> None:
    # Verify end-to-end case_tempo_cut when ASR words are present on witness and clip
    rec = record.load(selftest.REC_DIR)
    W = audio.decode(selftest.TALK)
    clip, ok, _ = selftest.case_tempo_cut(W, rec)

    # Build synthetic ASR words for witness [60..76.5] and clip (cut 68..69.5, atempo 1.25)
    toks = [
        "conference", "auditorium", "microphones", "recording", "signature", "quieter", "conditioning",
        "system", "speaker", "transmits", "verified", "cryptographic", "beacons", "continuously",
        "deleted", "segment", "removed",  # 68.0..69.5 cut
        "audience", "members", "upload", "unedited", "footage", "directly", "browser",
        "verdict", "confirms", "exact", "timestamps", "restores", "missing", "context",
    ]
    ww: list[asr.Word] = []
    # Pre-cut 14 words in 60.0..68.0, cut 3 words in 68.0..69.5, post-cut 14 words in 69.5..76.5
    for idx in range(14):
        t0 = 60.0 + idx * (8.0 / 14.0)
        ww.append(asr.Word(i=len(ww), tok=toks[idx], raw=toks[idx], start=t0, end=t0 + 0.4, prob=0.99))
    for idx in range(3):
        t0 = 68.0 + idx * (1.5 / 3.0)
        ww.append(asr.Word(i=len(ww), tok=toks[14 + idx], raw=toks[14 + idx], start=t0, end=t0 + 0.4, prob=0.99))
    for idx in range(14):
        t0 = 69.5 + idx * (7.0 / 14.0)
        ww.append(asr.Word(i=len(ww), tok=toks[17 + idx], raw=toks[17 + idx], start=t0, end=t0 + 0.4, prob=0.99))

    cw: list[asr.Word] = []
    for idx in range(14):
        w_t0 = idx * (8.0 / 14.0)
        c_t0 = w_t0 / 1.25
        cw.append(asr.Word(i=len(cw), tok=toks[idx], raw=toks[idx], start=c_t0, end=c_t0 + 0.32, prob=0.99))
    for idx in range(14):
        w_t0 = 8.0 + idx * (7.0 / 14.0)
        c_t0 = w_t0 / 1.25
        cw.append(asr.Word(i=len(cw), tok=toks[17 + idx], raw=toks[17 + idx], start=c_t0, end=c_t0 + 0.32, prob=0.99))

    rec_with_words = record.Record(meta=rec.meta, words=ww, feats=rec.feats)
    with patch("wholetake.asr.transcribe", return_value=cw):
        res = match.match(rec_with_words, clip)
    assert ok(res), f"case_tempo_cut failed with ASR active: verdict={res['verdict']}, tempo={res['tempo']}, gaps={res['gaps']}"


def main() -> int:
    try:
        test_sw_pieces_and_tempo_from_text_direction()
        print("PASS sw_pieces_and_tempo_from_text_direction")
        test_case_tempo_cut_with_asr_words()
        print("PASS case_tempo_cut_with_asr_words")
        return 0
    except Exception as e:
        print(f"FAIL sw_pieces_and_tempo_from_text_direction: {type(e).__name__}: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
