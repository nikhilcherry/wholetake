"""Synthesise the S4 test witness with Gemini TTS (one request per paragraph, joined with pauses).

usage: python tools/tts_testdata.py   (needs GEMINI_API_KEY; writes testdata/witness/*.wav)
"""
from __future__ import annotations

import base64
import json
import os
import sys
import time
import urllib.request

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from roomstamp import wavio  # noqa: E402

MODEL = "gemini-3.8-flash-tts"
TTS_FS = 24000
HERE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "testdata", "witness")


def api_key() -> str:
    k = os.environ.get("GEMINI_API_KEY")
    if not k and sys.platform == "win32":
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as h:
            k = winreg.QueryValueEx(h, "GEMINI_API_KEY")[0]
    return k


def tts(text: str, voice: str) -> np.ndarray:
    body = {
        "contents": [{"parts": [{"text": "Read this aloud at a calm public-speaking pace:\n\n" + text}]}],
        "generationConfig": {"responseModalities": ["AUDIO"],
                             "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}}}},
    }
    req = urllib.request.Request(
        f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent",
        data=json.dumps(body).encode(), headers={"x-goog-api-key": api_key(), "Content-Type": "application/json"})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                out = json.load(r)
            pcm = base64.b64decode(out["candidates"][0]["content"]["parts"][0]["inlineData"]["data"])
            return np.frombuffer(pcm, dtype="<i2").astype(np.float64) / 32768.0
        except Exception as e:  # rate limits / transient 5xx
            print(f"  retry {attempt + 1}: {e}", file=sys.stderr)
            time.sleep(5 * (attempt + 1))
    raise RuntimeError("TTS failed")


def render(txt_name: str, voice: str, wav_name: str) -> None:
    paras = [p.strip() for p in open(os.path.join(HERE, txt_name), encoding="utf-8").read().split("\n\n") if p.strip()]
    pause = np.zeros(int(0.7 * TTS_FS))
    parts = []
    for i, p in enumerate(paras):
        print(f"{wav_name}: paragraph {i + 1}/{len(paras)}", file=sys.stderr)
        parts += [tts(p, voice), pause]
    x = np.concatenate(parts)
    wavio.write(os.path.join(HERE, wav_name), x, fs=TTS_FS)
    print(f"{wav_name}: {len(x) / TTS_FS:.1f} s", file=sys.stderr)


if __name__ == "__main__":
    render("talk.txt", "Charon", "talk.wav")
    render("other.txt", "Kore", "other.wav")
    # Same voice as the talk, words never said in it: the "foreign insertion" case.
    x = tts("And frankly, we will double the ticket prices next year.", "Charon")
    wavio.write(os.path.join(HERE, "insert.wav"), x, fs=TTS_FS)
