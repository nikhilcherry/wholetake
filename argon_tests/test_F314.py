"""Reproducer test for F314: _regions() uses unverified text tempo alpha0 to extrapolate
witness search bounds, clipping the true witness start/end when alpha0 is biased.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import patch

from wholetake import asr, audio, match, record


def test_regions_clipped_by_biased_text_tempo() -> None:
    # 15 s clip starting at witness 20.0 s (true tempo = 1.0, true witness span = [20.0, 35.0] s
    # -> frames [2000, 3500]). Anchors sit in the late part of the clip (12.5..14.5 s).
    cw = [
        asr.Word(i, f"word{i}", f"word{i}", 12.5 + 0.5 * i, 12.8 + 0.5 * i, 0.99)
        for i in range(5)
    ]
    ww = [
        asr.Word(i, f"word{i}", f"word{i}", 32.5 + 0.5 * i, 32.8 + 0.5 * i, 0.99)
        for i in range(5)
    ]
    piece = [(i, i) for i in range(5)]

    # Low-biased text tempo alpha0 = 0.7
    regions_low = match._regions([piece], cw, ww, 0.7, clip_s=15.0, rec_s=60.0)
    if not regions_low or regions_low[0][0] > 2000 or regions_low[0][1] < 3500:
        raise AssertionError(
            f"_regions with alpha=0.7 clipped true witness span [2000, 3500]: regions={regions_low}"
        )

    # High-biased text tempo alpha0 = 1.4 with early anchors (0.5..2.5 s) and internal cut shift
    cw_early = [
        asr.Word(i, f"word{i}", f"word{i}", 0.5 + 0.5 * i, 0.8 + 0.5 * i, 0.99)
        for i in range(5)
    ]
    ww_early = [
        asr.Word(i, f"word{i}", f"word{i}", 20.5 + 0.5 * i, 20.8 + 0.5 * i, 0.99)
        for i in range(5)
    ]
    regions_high = match._regions([piece], cw_early, ww_early, 1.4, clip_s=15.0, rec_s=60.0)
    if not regions_high or regions_high[0][0] > 2000 or regions_high[0][1] < 3500:
        raise AssertionError(
            f"_regions with alpha=1.4 clipped true witness span [2000, 3500]: regions={regions_high}"
        )


def test_match_end_to_end_with_biased_text_tempo() -> None:
    here = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "testdata", "witness")
    rec_dir = os.path.join(here, "record_noasr")
    talk_wav = os.path.join(here, "talk.wav")
    if not (os.path.exists(rec_dir) and os.path.exists(talk_wav)):
        return

    rec = record.load(rec_dir)
    w_audio = audio.decode(talk_wav)
    # Slice a 15 s faithful clip from 20.0 s to 35.0 s
    clip = w_audio[int(20.0 * audio.SR):int(35.0 * audio.SR)]

    # Construct witness and clip words where aligned tokens are near the end of the clip
    # and timestamp jitter biases _tempo_from_text to alpha0 = 0.7.
    toks = ["alpha", "bravo", "charlie", "delta", "echo", "foxtrot"]
    cw_times = [11.5, 12.0, 12.5, 13.5, 14.0, 14.5]
    # Witness times chosen so (ww.start - cw.start) has slope ~0.7 across the 3 s span,
    # while actual audio is at witness 20.0 + clip_t (so at clip_t=13.5, witness is 33.5).
    ww_times = [33.5 + 0.7 * (t - 13.5) for t in cw_times]

    cw = [asr.Word(i, tok, tok, t, t + 0.2, 0.99) for i, (tok, t) in enumerate(zip(toks, cw_times))]
    rec.words = [asr.Word(i, tok, tok, t, t + 0.2, 0.99) for i, (tok, t) in enumerate(zip(toks, ww_times))]

    with patch("wholetake.asr.transcribe", return_value=cw):
        res = match.match(rec, clip)

    if res["verdict"] != "faithful" or len(res["pieces"]) != 1:
        raise AssertionError(f"Expected faithful 1-piece match, got: {res}")
    p0 = res["pieces"][0]
    if abs(p0["witness_start_s"] - 20.0) > 0.25 or abs(p0["witness_end_s"] - 35.0) > 0.25:
        raise AssertionError(
            f"Witness bounds clipped under biased alpha0: got [{p0['witness_start_s']}, {p0['witness_end_s']}], "
            f"expected [20.0, 35.0]"
        )


def main() -> int:
    try:
        test_regions_clipped_by_biased_text_tempo()
        test_match_end_to_end_with_biased_text_tempo()
    except Exception as e:
        print(f"FAIL test_F314: {e}")
        return 1
    print("PASS test_F314")
    return 0


if __name__ == "__main__":
    sys.exit(main())
