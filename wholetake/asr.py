"""faster-whisper wrapper. Witness and clip go through identical settings (research r55).

condition_on_previous_text=False makes each 30 s window decode on its own, so a cropped clip and
the full record are transcribed as alike as Whisper allows. Word times are only used to *locate*
the clip; the acoustic tracker measures the cuts.
"""
from __future__ import annotations

import os
from dataclasses import asdict, dataclass

import numpy as np

from . import text

MODEL = os.environ.get("WHOLETAKE_ASR", "large-v3-turbo")
_model = None


@dataclass
class Word:
    i: int            # index in the token list
    tok: str          # normalised token
    raw: str          # Whisper's word (a contraction yields several tokens with the same raw)
    start: float
    end: float
    prob: float

    def as_json(self) -> dict:
        d = asdict(self)
        d["start"], d["end"], d["prob"] = round(self.start, 3), round(self.end, 3), round(self.prob, 3)
        return d


def model():
    global _model, MODEL
    if MODEL == "none":
        return None
    if _model is None:
        try:
            import torch  # noqa: F401  (puts cuBLAS/cuDNN on the DLL path for ctranslate2 on Windows)
            cuda = torch.cuda.is_available()
        except ImportError:
            cuda = False
        try:
            from faster_whisper import WhisperModel
            _model = WhisperModel(MODEL, device="cuda" if cuda else "cpu", compute_type="float16" if cuda else "int8",
                                  local_files_only=True)
        except Exception:
            MODEL = "none"
            return None
    return _model


def transcribe(x: np.ndarray, language: str | None = "en") -> list[Word]:
    m = model()
    if m is None:  # acoustic-only mode (records up to 20 min are searched without text)
        return []
    arr = np.nan_to_num(np.asarray(x, dtype=np.float32), copy=True, nan=0.0, posinf=0.0, neginf=0.0)
    segs, _ = m.transcribe(arr, language=language, word_timestamps=True,
                           vad_filter=True, condition_on_previous_text=False, beam_size=5, temperature=0.0)
    out: list[Word] = []
    for s in segs:
        for w in s.words or []:
            toks = text.tokens(w.word)
            if not toks:
                continue
            step = (w.end - w.start) / len(toks)  # split contractions' time evenly
            for k, t in enumerate(toks):
                out.append(Word(len(out), t, w.word.strip(), w.start + k * step, w.start + (k + 1) * step,
                                float(w.probability)))
    return out
