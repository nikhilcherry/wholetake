import os
import sys

import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from wholetake import asr, audio, match, record, text


def main() -> int:
    cases = [
        ("मत,", ["mat"]),
        ("मत।", ["mat"]),
        ("मत?", ["mat"]),
        ("मत", ["mat"]),
        ("कब?", ["kab"]),
        ("नहीं,", ["nahi"]),
        ("नहीं।", ["nahi"]),
        ("मत-करो", ["mat", "karo"]),
    ]
    for raw, expected in cases:
        got = text.tokens(raw)
        if got != expected:
            print(f"FAIL test_F015: tokens({raw!r}) == {got!r}, expected {expected!r}")
            return 1

    if text.tokens("मत,")[0] not in text.DECISIVE:
        print("FAIL test_F015: 'mat' from 'मत,' not in DECISIVE")
        return 1

    # End-to-end check with match.match() on a cut removing 'मत,'
    os.environ["WHOLETAKE_ASR"] = "none"
    asr.MODEL = "none"
    rec_dir = os.path.join("testdata", "witness", "record_noasr")
    rec = record.load(rec_dir)
    w_audio = audio.decode(os.path.join("testdata", "witness", "talk.wav"))
    sr = audio.SR
    # Cut 46.5..49.8s from 40.0..56.0s
    clip = np.concatenate([w_audio[int(40.0 * sr):int(46.5 * sr)], w_audio[int(49.8 * sr):int(56.0 * sr)]])
    tok = text.tokens("मत,")[0]
    rec.words = [
        asr.Word(0, "aisa", "aisa", 44.0, 45.0, 0.99),
        asr.Word(1, tok, "मत,", 47.5, 48.2, 0.99),
        asr.Word(2, "karo", "karo", 51.0, 52.0, 0.99),
    ]
    res = match.match(rec, clip)
    if "mat" not in res.get("decisive_words_removed", []):
        print(f"FAIL test_F015: 'mat' not in decisive_words_removed: {res.get('decisive_words_removed')}")
        return 1

    print("PASS test_F015")
    return 0


if __name__ == "__main__":
    sys.exit(main())
