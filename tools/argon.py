"""Hand one well-specified build task to gemini-4-argon and write back only the files it owns.

    python tools/argon.py tasks/transcribe.md            # first round
    python tools/argon.py tasks/transcribe.md --feedback tasks/transcribe.fb.md   # next round, same conversation

The task file starts with an `owns:` line (space-separated repo paths). The conversation lives in
tools/.argon/<task>.json so feedback rounds keep context. Claude reviews and tests every round;
Argon never runs anything itself.
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
STATE = REPO / "tools" / ".argon"
MODEL = "gemini-4-argon"
URL = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"

SYSTEM = """You are a senior engineer writing code for Wholetake, a hackathon project (HH Goa 2026).
Wholetake witnesses public livestreams, keeps a tamper-evident record (audio chunks + transcripts +
fingerprints, hash-chained and anchored on-chain), and later tells whether a viral clip was cut.
Write small, readable, dependency-light Python 3.12 that matches the style of the existing files you
are shown. Comments explain *why*, briefly. No placeholders, no TODOs, no mock logic in product code.

OUTPUT FORMAT (strict). Emit every file you create or change in full, each as:
=== FILE: <repo-relative path> ===
<entire file content>
=== END ===
Then one final section:
=== NOTES ===
<= 15 lines: what you built, design choices, what you are unsure of>
=== END ===
No other prose. Only emit the paths listed as owned. Never emit secrets."""


def key():
    k = os.environ.get("GEMINI_API_KEY")
    if not k:
        for line in (Path.home() / ".config/assay/gemini.env").read_text().splitlines():
            if line.startswith("GEMINI_API_KEY="):
                k = line.split("=", 1)[1].strip()
    return k


def post(body):
    data = json.dumps(body).encode()
    for attempt in range(6):
        req = urllib.request.Request(URL, data=data, headers={"Content-Type": "application/json",
                                                              "x-goog-api-key": key()})
        try:
            with urllib.request.urlopen(req, timeout=900) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 503) and attempt < 5:
                time.sleep(15 * (attempt + 1))
                continue
            raise RuntimeError(f"HTTP {e.code}: {e.read().decode()[:300]}")


def turn(history):
    """Argon stops with finishReason CONTINUATION when its thinking budget runs out; resend to continue."""
    contents, text = list(history), ""
    for _ in range(6):
        d = post({"systemInstruction": {"parts": [{"text": SYSTEM}]}, "contents": contents,
                  "generationConfig": {"temperature": 0.4, "maxOutputTokens": 65536}})
        cand = d["candidates"][0]
        parts = cand.get("content", {}).get("parts", [])
        text += "".join(p.get("text", "") for p in parts if not p.get("thought"))
        if cand.get("finishReason") != "CONTINUATION":
            break
        contents += [{"role": "model", "parts": parts},
                     {"role": "user", "parts": [{"text": "Continue exactly where you stopped."}]}]
    return text


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("task")
    ap.add_argument("--feedback", help="file with review notes / test output for the next round")
    a = ap.parse_args()

    task = Path(a.task)
    spec = task.read_text()
    owns = re.search(r"^owns:(.*)$", spec, re.M).group(1).split()
    STATE.mkdir(parents=True, exist_ok=True)
    conv = STATE / f"{task.stem}.json"
    history = json.loads(conv.read_text()) if conv.exists() else []

    if not history:
        ctx = re.search(r"^context:(.*)$", spec, re.M)
        shown = "".join(f"\n--- {p} ---\n{(REPO / p).read_text()}" for p in (ctx.group(1).split() if ctx else []))
        msg = spec + ("\n\nEXISTING FILES FOR CONTEXT:" + shown if shown else "")
    else:
        msg = Path(a.feedback).read_text() if a.feedback else sys.exit("conversation exists: pass --feedback")
    history.append({"role": "user", "parts": [{"text": msg}]})

    t0 = time.time()
    text = turn(history)
    history.append({"role": "model", "parts": [{"text": text}]})
    conv.write_text(json.dumps(history))
    (STATE / f"{task.stem}.last.md").write_text(text)  # full reply, for review-style tasks

    files = dict(re.findall(r"=== FILE: (.+?) ===\n(.*?)\n=== END ===", text, re.S))
    for path, body in files.items():
        path = path.strip()
        if path not in owns:
            print(f"SKIPPED (not owned): {path}")
            continue
        (REPO / path).parent.mkdir(parents=True, exist_ok=True)
        (REPO / path).write_text(body + "\n")
        print(f"wrote {path} ({body.count(chr(10)) + 1} lines)")
    notes = re.search(r"=== NOTES ===\n(.*?)(?:\n=== END ===|$)", text, re.S)
    print(f"--- argon notes ({time.time() - t0:.0f}s) ---\n{notes.group(1).strip() if notes else text[:1500]}")


if __name__ == "__main__":
    main()
