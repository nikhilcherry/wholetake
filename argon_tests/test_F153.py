import json
import os
import shutil
import sys
import tempfile
import numpy as np
import soundfile as sf

from roomstamp import wt_detect
from wholetake import record


def main() -> None:
    name = "test_F153"
    tmp = tempfile.mkdtemp(prefix="argon_f153_")
    try:
        os.environ["WHOLETAKE_ASR"] = "none"
        speech_path = os.path.join("testdata", "speech.wav")
        x, sr = sf.read(speech_path, dtype="float64", always_2d=True)
        x = x[:, 0]
        assert sr == 48000, f"unexpected sr {sr}"

        seg1 = x[1 * sr : 9 * sr]
        seg2 = x[10 * sr : 18 * sr]
        pause_wit = np.zeros(int(1.20 * sr), dtype=np.float64)
        pause_clip = np.zeros(int(0.20 * sr), dtype=np.float64)

        wit_audio = np.concatenate([seg1, pause_wit, seg2])
        clip_audio = np.concatenate([seg1, pause_clip, seg2])

        wit_wav = os.path.join(tmp, "witness.wav")
        clip_wav = os.path.join(tmp, "clip.wav")
        sf.write(wit_wav, wit_audio, sr, subtype="PCM_16")
        sf.write(clip_wav, clip_audio, sr, subtype="PCM_16")

        rec_dir = wit_wav + ".wtrec"
        record.build(wit_wav, rec_dir)

        words = [
            {"word": "alpha", "start": 1.5, "end": 2.0, "prob": 0.99},
            {"word": "bravo", "start": 3.0, "end": 3.5, "prob": 0.99},
            {"word": "charlie", "start": 11.5, "end": 12.0, "prob": 0.99},
            {"word": "delta", "start": 13.0, "end": 13.5, "prob": 0.99},
        ]
        with open(os.path.join(rec_dir, "words.json"), "w", encoding="utf-8") as f:
            json.dump(words, f)

        doc = wt_detect.run(
            clip_wav,
            witness=wit_wav,
            key_file=os.path.join(tmp, "nonexistent.key"),
            venue_dir=os.path.join(tmp, "empty_venue"),
            layers="wt",
        )

        wt_layer = doc["layers"]["wt"]
        verdict = doc["verdict"]
        if wt_layer.get("wt_verdict") != "faithful":
            print(f"FAIL {name}: expected S4 wt_verdict='faithful', got {wt_layer.get('wt_verdict')!r} (edits={wt_layer.get('edits')})")
            sys.exit(1)

        wt_kinds = [e.get("kind") for e in wt_layer.get("edits", [])]
        if "removed" in wt_kinds or "pause_trim" not in wt_kinds:
            print(f"FAIL {name}: expected 'pause_trim' (not 'removed') in layers.wt.edits, got {wt_kinds}")
            sys.exit(1)

        if verdict.get("integrity") != "intact":
            print(f"FAIL {name}: expected verdict.integrity='intact' on faithful pause-trimmed clip, got {verdict.get('integrity')!r}")
            sys.exit(1)

        headline = verdict.get("headline", "")
        if "removed at" in headline or "Complete: nothing removed" not in headline:
            print(f"FAIL {name}: unexpected headline for faithful clip: {headline!r}")
            sys.exit(1)

        print(f"PASS {name}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
