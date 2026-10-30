from __future__ import annotations

import sys
from wholetake import text


def fail(reason: str) -> None:
    print(f"FAIL test_F327: {reason}")
    sys.exit(1)


def main() -> None:
    # 1. Nukta followed by vowel matra (both decomposed and precomposed forms)
    badaa_dec = "\u092c\u0921\u093c\u093e"  # बड़ा
    badaa_pre = "\u092c\u095c\u093e"        # बड़ा (precomposed U+095C)
    got_badaa_rom = text.romanise(badaa_dec)
    if got_badaa_rom != "badaa":
        fail(f"Vowel matra dropped after nukta in 'बड़ा': romanise() got {got_badaa_rom!r}")
    got_badaa_tok = text.tokens(badaa_dec)
    if got_badaa_tok != ["badaa"]:
        fail(f"Vowel matra dropped after nukta in 'बड़ा': tokens() got {got_badaa_tok!r}")
    got_pre_rom = text.romanise(badaa_pre)
    if got_pre_rom != "badaa":
        fail(f"Precomposed nukta failed in romanise: got {got_pre_rom!r}")
    got_pre_tok = text.tokens(badaa_pre)
    if got_pre_tok != ["badaa"]:
        fail(f"Precomposed nukta failed in tokens: got {got_pre_tok!r}")

    # 2. फ़ैसला (nukta + matra 'ै' + inherent schwa)
    faisla = "फ़ैसला"
    got_faisla_rom = text.romanise(faisla)
    if got_faisla_rom != "phaisalaa":
        fail(f"Matra 'ै' dropped after nukta in 'फ़ैसला': got {got_faisla_rom!r}")
    got_faisla_tok = text.tokens(faisla)
    if got_faisla_tok != ["phaisalaa"]:
        fail(f"tokens('फ़ैसला') failed: got {got_faisla_tok!r}")

    # 3. ज़्यादा (nukta + virama '्')
    zyada = "ज़्यादा"
    got_zyada_rom = text.romanise(zyada)
    if got_zyada_rom != "jyaadaa":
        fail(f"Virama after nukta failed in 'ज़्यादा': got {got_zyada_rom!r}")
    got_zyada_tok = text.tokens(zyada)
    if got_zyada_tok != ["jyaadaa"]:
        fail(f"tokens('ज़्यादा') failed: got {got_zyada_tok!r}")

    # 4. Standalone matra handling in romanise
    got_mat = text.romanise("ा")
    if got_mat != "aa":
        fail(f"Standalone matra dropped in romanise: got {got_mat!r}")

    # 5. Devanagari digits (०-९, U+0966..U+096F)
    got_all_dig = text.romanise("०१२३४५६७८९")
    if got_all_dig != "0123456789":
        fail(f"Devanagari digits not mapped in romanise: got {got_all_dig!r}")
    got_100 = text.tokens("१००")
    if got_100 != ["100"]:
        fail(f"Devanagari digits erased in tokens('१००'): got {got_100!r}")
    got_2026 = text.tokens("२०२६")
    if got_2026 != ["2026"]:
        fail(f"Devanagari digits erased in tokens('२०२६'): got {got_2026!r}")
    got_mixed = text.tokens("धारा-१४४")
    if got_mixed != ["dhaaraa", "144"]:
        fail(f"Mixed Devanagari word and digits failed: got {got_mixed!r}")

    # 6. Regression check on existing Hinglish negations
    got_nahi = text.tokens("नहीं")
    if got_nahi != ["nahi"]:
        fail(f"Regression on 'नहीं': got {got_nahi!r}")

    print("PASS test_F327")
    sys.exit(0)


if __name__ == "__main__":
    main()
