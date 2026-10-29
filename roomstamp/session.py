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

from . import engine, spec
from .spec import FS


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
            return bytes.fromhex(r["randomness"]) if r and r.get("anchored") and r.get("randomness") else None
        return f

    def state_at(self, t: int) -> tuple[float | None, float | None]:
        """(ref_rms, gain) the emitter logged for second t, to start a mid-session render."""
        r = self.secs.get(int(t)) or {}
        return r.get("ref_rms"), r.get("gain")

    def anchored_fraction(self, t0: float, t1: float) -> float:
        ts = range(int(t0), int(t1) + 1)
        return sum(bool((self.secs.get(t) or {}).get("anchored")) for t in ts) / max(1, len(ts))

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
        return cls(key_id=h["key_id"], t_start=int(h["t_start"]), params=engine.StampParams(**h["params"]),
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
        secs = {int(t): {"t": int(t), "anchored": h is not None, "randomness": h}
                for t, h in d.get("seconds", {}).items()}
        return cls(key_id=d["key_id"], t_start=int(d["t_start"]), params=engine.StampParams(**d["params"]),
                   feed=d["feed"], venue=d.get("venue", "Roomstamp venue"), secs=secs, path=path)
