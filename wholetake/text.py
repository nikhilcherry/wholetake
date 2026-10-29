"""Word normalisation shared by the witness record and the clip (same rules on both sides).

- lowercase, strip punctuation, expand English contractions into separate tokens
  ("didn't" -> "did", "not"), split hyphens, spell nothing out (Whisper's own number style
  is consistent across both sides because both use the same model and settings);
- romanise Devanagari with a small table and fold common Hinglish spellings
  (nahin/nhi/नहीं -> nahi);
- a crude phonetic skeleton for fuzzy matching (ASR spelling variants, Hinglish).
"""
from __future__ import annotations

import re
import unicodedata

CONTRACTIONS = {
    "won't": ["will", "not"], "can't": ["can", "not"], "cannot": ["can", "not"], "shan't": ["shall", "not"],
    "ain't": ["is", "not"], "let's": ["let", "us"], "i'm": ["i", "am"],
}
SUFFIXES = [("n't", ["not"]), ("'re", ["are"]), ("'ve", ["have"]), ("'ll", ["will"]), ("'d", ["would"]),
            ("'m", ["am"]), ("'s", [])]  # 's: possessive or "is"; dropped on both sides

HINGLISH = {"nahin": "nahi", "naheen": "nahi", "nahee": "nahi", "nahiin": "nahi", "nahan": "nahi", "nhi": "nahi", "nhn": "nahi", "nai": "nahi", "nahi": "nahi", "mt": "mat",
            "hain": "hai", "hy": "hai", "kya": "kya", "kyaa": "kya", "mai": "main", "mein": "mein", "men": "mein", "me": "mein"}

DECISIVE = {
    "not", "no", "never", "neither", "nor", "none", "nobody", "nothing", "nowhere", "without",
    "hardly", "barely", "rarely", "deny", "denied", "refuse", "refused", "reject", "rejected", "false",
    "only", "except", "unless", "if", "all", "every", "always", "allegedly",
    "nahi", "na", "mat", "bina", "kabhi", "sirf", "agar",
}

# Minimal Devanagari -> Roman map (enough to make negations and function words line up).
_DEV_CONS = dict(zip("कखगघङचछजझञटठडढणतथदधनपफबभमयरलवशषसह",
                     ["k", "kh", "g", "gh", "n", "ch", "chh", "j", "jh", "n", "t", "th", "d", "dh", "n", "t", "th",
                      "d", "dh", "n", "p", "ph", "b", "bh", "m", "y", "r", "l", "v", "sh", "sh", "s", "h"]))
_DEV_VOW = dict(zip("अआइईउऊएऐओऔऋ", ["a", "aa", "i", "ee", "u", "oo", "e", "ai", "o", "au", "ri"]))
_DEV_MAT = dict(zip("ािीुूेैोौृ", ["aa", "i", "ee", "u", "oo", "e", "ai", "o", "au", "ri"]))
_DEV_NASAL = set("ंँ")
_VIRAMA = "्"


def romanise(word: str) -> str:
    if not any("ऀ" <= ch <= "ॿ" for ch in word):
        return word
    out, i = [], 0
    while i < len(word):
        ch = word[i]
        if ch in _DEV_CONS:
            out.append(_DEV_CONS[ch])
            nxt = word[i + 1] if i + 1 < len(word) else ""
            if nxt in _DEV_MAT:
                out.append(_DEV_MAT[nxt])
                i += 1
            elif nxt == _VIRAMA:
                i += 1
            elif nxt in _DEV_CONS or nxt in _DEV_VOW or nxt in _DEV_MAT or nxt in _DEV_NASAL or nxt.isalnum():
                out.append("a")  # inherent schwa, dropped at word end
        elif ch in _DEV_VOW:
            out.append(_DEV_VOW[ch])
        elif ch in _DEV_NASAL:
            out.append("n")
        elif ch.isascii():
            out.append(ch)
        i += 1
    return "".join(out)


def tokens(raw: str) -> list[str]:
    """One ASR word -> zero or more normalised tokens."""
    w = unicodedata.normalize("NFC", raw).strip().lower().replace("’", "'").replace("‘", "'")
    out: list[str] = []
    for part in re.split(r"[-–—/]", w):
        part = re.sub(r"[^\w\u0900-\u0963\u0966-\u097f']", "", part).strip("'")
        part = romanise(part)
        part = re.sub(r"[^\w']", "", part).strip("'")
        if not part:
            continue
        if part in CONTRACTIONS:
            out += CONTRACTIONS[part]
            continue
        for suf, rep in SUFFIXES:
            if part.endswith(suf) and len(part) > len(suf):
                stem = part[: -len(suf)]
                out += ([stem] if stem else []) + rep
                break
        else:
            part = part.replace("'", "")
            out.append(HINGLISH.get(part, part))
    return out


_PH = [("ph", "f"), ("bh", "b"), ("th", "t"), ("dh", "d"), ("kh", "k"), ("gh", "g"), ("sh", "s"),
       ("ch", "c"), ("ck", "k"), ("q", "k"), ("v", "w"), ("z", "s"), ("x", "ks")]


def phon(tok: str) -> str:
    """Consonant skeleton: first letter kept, later vowels/h/y/w dropped, doubles collapsed."""
    s = tok
    for a, b in _PH:
        s = s.replace(a, b)
    if not s:
        return s
    head, tail = s[0], re.sub(r"[aeiouyhw]", "", s[1:])
    out = head
    for c in tail:
        if c != out[-1]:
            out += c
    return out


def similarity(a: str, b: str) -> float:
    """Token similarity in [0, 1]: 1 exact, 0.8 same skeleton, else normalised edit similarity."""
    if a == b:
        return 1.0
    if len(a) >= 3 and len(b) >= 3 and phon(a) == phon(b):
        return 0.8
    la, lb = len(a), len(b)
    if min(la, lb) < 3:
        return 0.0
    prev = list(range(lb + 1))
    for i in range(1, la + 1):
        cur = [i] + [0] * lb
        for j in range(1, lb + 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (a[i - 1] != b[j - 1]))
        prev = cur
    r = 1.0 - prev[lb] / max(la, lb)
    return r if r >= 0.7 else 0.0
