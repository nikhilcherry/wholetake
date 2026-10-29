from __future__ import annotations

import sys
from wholetake import asr, match, text


def make_words(raw_words: list[str]) -> list[asr.Word]:
    out: list[asr.Word] = []
    t = 0.0
    for raw in raw_words:
        toks = text.tokens(raw)
        for tok in toks:
            out.append(asr.Word(len(out), tok, raw, t, t + 0.4, 0.99))
            t += 0.5
    return out


def main() -> int:
    try:
        # 1. Bare consonant followed by anusvara/chandrabindu retains inherent 'a'
        cases_roman = [
            ("बंद", "band"),
            ("संत", "sant"),
            ("रंग", "rang"),
            ("हंस", "hans"),
            ("मंगल", "mangal"),
            ("माँ", "maan"),
        ]
        for dev, expected in cases_roman:
            got_rom = text.romanise(dev)
            assert got_rom == expected, f"romanise({dev!r}) == {got_rom!r}, expected {expected!r}"
            got_tok = text.tokens(dev)
            assert got_tok == [expected], f"tokens({dev!r}) == {got_tok!r}, expected {[expected]!r}"

        # 2. Hinglish normalization for में / men and नहीं / नहिं / नहं / nhn
        cases_tokens = [
            ("में", ["mein"]),
            ("men", ["mein"]),
            ("mein", ["mein"]),
            ("नहीं", ["nahi"]),
            ("नहिं", ["nahi"]),
            ("नहं", ["nahi"]),
            ("nhn", ["nahi"]),
        ]
        for raw, expected_toks in cases_tokens:
            got = text.tokens(raw)
            assert got == expected_toks, f"tokens({raw!r}) == {got!r}, expected {expected_toks!r}"

        # 3. Cross-script Smith-Waterman alignment and user-facing restored_text / context
        wit_raw = ["भारत", "में", "बाजार", "बंद", "नहीं", "हैं", "संत", "और", "हंस", "का", "रंग"]
        clip_raw = ["bharat", "mein", "bazar", "hai", "aur", "hans", "ka", "rang"]
        ww = make_words(wit_raw)
        cw = make_words(clip_raw)

        # Check witness token strings directly surfaced in witness_text / restored_text
        wit_toks = [w.tok for w in ww]
        assert "band" in wit_toks and "bnd" not in wit_toks, f"unexpected witness tokens: {wit_toks}"
        assert "sant" in wit_toks and "snt" not in wit_toks, f"unexpected witness tokens: {wit_toks}"
        assert "mein" in wit_toks and "men" not in wit_toks, f"unexpected witness tokens: {wit_toks}"
        assert "nahi" in wit_toks, f"unexpected witness tokens: {wit_toks}"

        pieces = match._sw_pieces(cw, ww)
        assert len(pieces) >= 1, f"expected SW alignment pieces, got {pieces}"
        matched_wit = {w_idx for p in pieces for _, w_idx in p}
        # 'mein' (idx 1), 'hans' (idx 8), 'rang' (idx 10) should be aligned
        assert 1 in matched_wit, f"'में' vs 'mein' failed to align in SW pieces: {pieces}"
        assert 8 in matched_wit, f"'हंस' vs 'hans' failed to align in SW pieces: {pieces}"
        assert 10 in matched_wit, f"'रंग' vs 'rang' failed to align in SW pieces: {pieces}"

        # Verify _context formatting when 'बंद नहीं' is removed
        removed = [ww[3], ww[4]]
        ctx = match._context(ww, ww[3].start, ww[4].end, removed)
        assert "[BAND NAHI]" in ctx, f"expected '[BAND NAHI]' in context, got {ctx!r}"

    except AssertionError as e:
        print(f"FAIL test_F174: {e}")
        return 1
    except Exception as e:
        print(f"FAIL test_F174: unexpected {type(e).__name__}: {e}")
        return 1

    print("PASS test_F174")
    return 0


if __name__ == "__main__":
    sys.exit(main())
