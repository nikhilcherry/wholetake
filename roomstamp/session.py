"""What the verifier (S2) needs from a stamped session, so it can re-render exactly what the
speaker played: key id, t_start, params, the clean feed, and the drand randomness *per second*
(the emitter may have had a round for one second and not for the next one sharing it).

Two sources:
- Session.from_log(logs/session_<t>.jsonl): the S1 emitter log (normative). Per-second records
  carry anchored/randomness plus ref_rms and gain, so a mid-session span re-renders exactly.
- Session.load(session.json) / save(): a small manifest for tests and hand-made sessions.
  Its "seconds" map lists {t: randomness hex | null}; a second it doesn't list is unanchored.
Randomness is never fetched from drand here: an unanchored second was played with zero
randomness, and fetching its round would re-render a stamp the speaker never played.
Feed sample n belongs to second t_start + n // FS. The key itself is never stored.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

import numpy as np

from . import engine, spec
from .spec import FS


def _parse_randomness(val) -> bytes | None:
    if val is None:
        return None
    if isinstance(val, (bytes, bytearray, memoryview)):
        b = bytes(val)
        if len(b) != 32:
            raise ValueError(f"randomness must be 32 bytes, got {len(b)} bytes")
        return b
    if isinstance(val, str):
        s = val.strip()
        if len(s) != 64:
            raise ValueError(f"randomness must be 32 bytes (64 hex chars), got {val!r}")
        try:
            b = bytes.fromhex(s)
        except ValueError as e:
            raise ValueError(f"randomness must be valid 64-char hex, got {val!r}") from e
        if len(b) != 32:
            raise ValueError(f"randomness must be 32 bytes (64 hex chars), got {val!r}")
        return b
    raise ValueError(f"randomness must be a 64-char hex string or 32 bytes, got {val!r}")


@dataclass
class Session:
    key_id: str
    t_start: int
    params: engine.StampParams
    feed: str
    venue: str = "Roomstamp venue"
    secs: dict[int, dict] = field(default_factory=dict)  # t -> S1 log record (anchored, randomness, ref_rms, gain)
    rounds: dict = field(default_factory=dict)  # legacy (round-keyed); ignored, kept so old callers construct
    spec_version: str = spec.SPEC_VERSION
    path: str | None = None  # where the manifest / log was loaded from

    def __post_init__(self) -> None:
        if self.spec_version != spec.SPEC_VERSION:
            raise ValueError(f"spec {self.spec_version!r}, verifier speaks {spec.SPEC_VERSION!r}")
        if isinstance(self.t_start, bool) or not isinstance(self.t_start, (int, np.integer)):
            raise ValueError(f"t_start must be an integer, got {self.t_start!r}")
        self.t_start = int(self.t_start)
        if self.t_start < 1 or self.t_start >= (1 << 64):
            raise ValueError(f"t_start must be >= 1 and < 2^64, got {self.t_start}")
        if not isinstance(self.secs, dict):
            raise ValueError(f"secs must be a dict, got {type(self.secs).__name__}")
        norm_secs: dict[int, dict] = {}
        for k, r in self.secs.items():
            if isinstance(k, bool) or not isinstance(k, (int, np.integer, str)):
                raise ValueError(f"invalid second key in secs: {k!r}")
            try:
                tk = int(k)
            except ValueError as e:
                raise ValueError(f"invalid second key in secs: {k!r}") from e
            if tk < 0 or tk >= (1 << 64):
                raise ValueError(f"second timestamp must be in [0, 2^64), got {k!r}")
            if not isinstance(r, dict):
                raise ValueError(f"second {tk} record must be a dict, got {r!r}")
            rnd = r.get("randomness")
            anch = bool(r.get("anchored"))
            if rnd is not None:
                _parse_randomness(rnd)
            elif anch:
                raise ValueError(f"second {tk}: anchored is True but randomness is None")
            norm_secs[tk] = r
        self.secs = norm_secs

    @property
    def feed_path(self) -> str:
        if os.path.isabs(self.feed) or not self.path:
            return self.feed
        return os.path.join(os.path.dirname(os.path.abspath(self.path)), self.feed)

    def lookup(self):
        """randomness_for_second(t) for engine.KeystreamSequencer / engine.render."""
        secs = self.secs

        def f(t):
            r = secs.get(int(t))
            if not isinstance(r, dict) or not r.get("anchored"):
                return None
            rnd = r.get("randomness")
            if not rnd:
                return None
            try:
                return _parse_randomness(rnd)
            except ValueError:
                return None
        return f

    def state_at(self, t: int) -> tuple[float | None, float | None]:
        """(ref_rms, gain) the emitter logged for second t, to start a mid-session render."""
        r = self.secs.get(int(t)) or {}
        return r.get("ref_rms"), r.get("gain")

    def anchored_fraction(self, t0: float, t1: float) -> float:
        lo, hi = int(t0), int(t1)
        if hi < lo:
            return 0.0
        n_total = hi - lo + 1
        n_anch = 0
        for t, r in self.secs.items():
            if isinstance(t, (int, np.integer)) and not isinstance(t, bool) and lo <= int(t) <= hi and isinstance(r, dict) and bool(r.get("anchored")):
                try:
                    if _parse_randomness(r.get("randomness")) is not None:
                        n_anch += 1
                except ValueError:
                    pass
        return n_anch / max(1, n_total)

    @classmethod
    def from_log(cls, path: str, venue: str | None = None) -> "Session":
        from .cli import read_log
        h, secs = read_log(path)
        if h is None:
            raise ValueError(f"{path}: no session header")
        if h.get("spec") != spec.SPEC_VERSION:
            raise ValueError(f"{path}: spec {h.get('spec')!r}, verifier speaks {spec.SPEC_VERSION!r}")
        if int(h.get("fs", FS)) != FS:
            raise ValueError(f"{path}: fs {h['fs']} != {FS}")
        t_raw = h.get("t_start")
        if isinstance(t_raw, bool) or not isinstance(t_raw, (int, np.integer)):
            raise ValueError(f"{path}: t_start must be an integer, got {t_raw!r}")
        return cls(key_id=h["key_id"], t_start=int(t_raw), params=engine.StampParams(**h["params"]),
                   feed=h.get("feed_wav") or "", venue=venue or h.get("venue") or "Roomstamp venue",
                   secs=secs, path=path)

    def save(self, path: str) -> None:
        d = {"spec_version": self.spec_version, "venue": self.venue, "key_id": self.key_id,
             "t_start": self.t_start, "fs": FS, "params": self.params.as_log(), "feed": self.feed,
             "seconds": {str(t): (r.get("randomness") if r.get("anchored") else None)
                         for t, r in sorted(self.secs.items())}}
        with open(path, "w", encoding="utf-8") as f:
            json.dump(d, f, indent=1)
        self.path = path

    @classmethod
    def load(cls, path: str) -> "Session":
        """session.json manifest, or an S1 .jsonl log."""
        if path.endswith(".jsonl"):
            return cls.from_log(path)
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        if d.get("spec_version") != spec.SPEC_VERSION:
            raise ValueError(f"{path}: spec {d.get('spec_version')!r}, verifier speaks {spec.SPEC_VERSION!r}")
        if int(d.get("fs", FS)) != FS:
            raise ValueError(f"{path}: fs {d['fs']} != {FS}")
        t_raw = d.get("t_start")
        if isinstance(t_raw, bool) or not isinstance(t_raw, (int, np.integer)):
            raise ValueError(f"{path}: t_start must be an integer, got {t_raw!r}")
        secs = {int(t): {"t": int(t), "anchored": h is not None, "randomness": h}
                for t, h in d.get("seconds", {}).items()}
        return cls(key_id=d["key_id"], t_start=int(t_raw), params=engine.StampParams(**d["params"]),
                   feed=d["feed"], venue=d.get("venue", "Roomstamp venue"), secs=secs, path=path)
