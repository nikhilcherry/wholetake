"""S2 command line: informed layer-1 verdict for one clip.

    python -m roomstamp.verify CLIP --session logs/session_<t>.jsonl --key venue.key [--json out.json]

CLIP is any media ffmpeg reads (mp4 from X/WhatsApp, m4a, ogg, wav). --session takes the S1
emitter log (or a session.json manifest). --key is the venue key file (hex). Prints the human
verdict, the pieces and the edits; --json writes the full result.
"""
from __future__ import annotations

import argparse
import json
import sys

from . import l1verify, media, spec, wavio
from .session import Session


def _read_key(path: str) -> bytes:
    raw = open(path, "rb").read().strip()
    try:
        k = bytes.fromhex(raw.decode())
    except ValueError:
        k = raw
    if len(k) != 32:
        raise SystemExit(f"{path}: expected a 32-byte key (64 hex chars), got {len(k)} bytes")
    return k


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m roomstamp.verify", description=__doc__.split("\n\n")[0])
    ap.add_argument("clip")
    ap.add_argument("--session", required=True, help="S1 log (.jsonl) or session.json")
    ap.add_argument("--key", required=True, help="venue key file (hex)")
    ap.add_argument("--json", help="write the full result here")
    a = ap.parse_args(argv)

    sess = Session.load(a.session)
    key = _read_key(a.key)
    if spec.key_id(key) != sess.key_id:
        print(f"warning: key id {spec.key_id(key)} != session key id {sess.key_id} (expect 'stamp absent')",
              file=sys.stderr)
    if not sess.feed:
        raise SystemExit(f"{a.session}: session has no clean feed; use the blind checker instead")
    feed = wavio.read(sess.feed_path)
    if not (abs(feed).max() > 1e-6):
        raise SystemExit(f"{sess.feed_path}: clean feed is silent; informed check impossible (use blind)")
    clip = media.decode(a.clip)
    r = l1verify.verify(clip, feed, sess, key, log=lambda *m: print(*m, file=sys.stderr))

    print(r["verdict"])
    for i, p in enumerate(r["pieces"]):
        print(f"  piece {i}: clip {p['clip_start']:6.2f}-{p['clip_end']:6.2f} s  venue {l1verify._fmt_utc(p['venue_t_start'])}"
              f"  stamp {p.get('l1_status', '-'):<12} z {p.get('l1_clip_z', 0):5.1f}"
              f"  windows z>5 {p.get('l1_frac_z5', 0):4.0%}  wrong-key z {p.get('l1_wrong_key_max_z', 0):4.1f}"
              f"  drift {p['drift_ppm']:+.0f} ppm")
    for e in r["edits"]:
        print(f"  edit: {e}")
    print(f"  ({r['runtime_s']:.1f} s)", file=sys.stderr)
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(r, f, indent=1, default=float)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
