"""Witness record: what is precomputed once when a stream/talk has been recorded in full.

<dir>/meta.json      id, source file sha256, duration, ASR model, feature params
<dir>/words.json     normalised tokens with times and probabilities
<dir>/features.npy   (frames, 40) float16 unit-norm log-mel at 100 fps
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass

import numpy as np

from . import asr, audio

FORMAT = "wholetake-witness-v1"


@dataclass
class Record:
    meta: dict
    words: list[asr.Word]
    feats: np.ndarray

    @property
    def duration(self) -> float:
        return self.meta["duration_s"]


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def build(src: str, out_dir: str, record_id: str | None = None, language: str | None = "en",
          t_start_unix: float | None = None) -> Record:
    t0 = time.time()
    x = audio.decode(src)
    words = asr.transcribe(x, language)
    feats = audio.features(x)
    meta = {
        "format": FORMAT, "id": record_id or os.path.splitext(os.path.basename(src))[0],
        "source": os.path.basename(src), "source_sha256": sha256_file(src),
        "duration_s": round(len(x) / audio.SR, 3), "t_start_unix": t_start_unix, "asr_model": asr.MODEL, "language": language,
        "fps": audio.FPS, "n_mels": audio.NMELS, "n_words": len(words), "build_s": round(time.time() - t0, 1),
    }
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=1)
    with open(os.path.join(out_dir, "words.json"), "w", encoding="utf-8") as f:
        json.dump([w.as_json() for w in words], f, ensure_ascii=False)
    np.save(os.path.join(out_dir, "features.npy"), feats.astype(np.float16))
    return Record(meta, words, feats)


def load(rec_dir: str) -> Record:
    with open(os.path.join(rec_dir, "meta.json"), encoding="utf-8") as f:
        meta = json.load(f)
    if meta.get("format") != FORMAT:
        raise ValueError(f"{rec_dir}: not a {FORMAT} record")
    with open(os.path.join(rec_dir, "words.json"), encoding="utf-8") as f:
        words = [asr.Word(**w) for w in json.load(f)]
    feats = np.load(os.path.join(rec_dir, "features.npy")).astype(np.float32)
    return Record(meta, words, feats)
