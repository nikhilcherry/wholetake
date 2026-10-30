import os
import sys
import numpy as np

os.environ["WHOLETAKE_ASR"] = "none"

from wholetake import asr, audio, match, record, selftest


def test_decisive_words_removed_deduplicated() -> bool:
    here = os.path.join("testdata", "witness")
    talk_path = os.path.join(here, "talk.wav")
    rec_dir = os.path.join(here, "record_noasr")

    rec = record.load(rec_dir)
    W = audio.decode(talk_path)

    rec.words = [
        asr.Word(0, "we", "we", 41.0, 41.3, 0.99),
        asr.Word(1, "will", "will", 45.2, 45.5, 0.99),
        asr.Word(2, "not", "not", 45.6, 46.0, 0.99),
        asr.Word(3, "accept", "accept", 48.0, 48.5, 0.99),
        asr.Word(4, "do", "do", 52.3, 52.6, 0.99),
        asr.Word(5, "not", "not", 52.7, 53.1, 0.99),
        asr.Word(6, "forget", "forget", 55.0, 55.5, 0.99),
    ]

    clip = selftest.crossfade_join([
        selftest.seg(W, 40.0, 45.0),
        selftest.seg(W, 47.0, 52.0),
        selftest.seg(W, 54.0, 60.0),
    ], 0.01)

    r = match.match(rec, clip)
    if len(r["gaps"]) != 2:
        print(f"FAIL test_decisive_words_removed_deduplicated: expected 2 gaps, got {len(r['gaps'])}")
        return False

    dec = r["decisive_words_removed"]
    if dec != ["not"]:
        print(f"FAIL test_decisive_words_removed_deduplicated: decisive_words_removed contains duplicates: {dec}")
        return False

    print("PASS test_decisive_words_removed_deduplicated")
    return True


if __name__ == "__main__":
    sys.exit(0 if test_decisive_words_removed_deduplicated() else 1)
