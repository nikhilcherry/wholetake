"""python -m wholetake build <witness audio> <record dir> [--id ID] [--t-start UNIX]
   python -m wholetake match <record dir> <clip> [--json out.json]
   python -m wholetake selftest [...]
"""
from __future__ import annotations

import argparse
import json
import sys

from . import audio, match, record, selftest


def main(argv=None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["selftest"]:
        return selftest.main(argv[1:])
    ap = argparse.ArgumentParser(prog="wholetake")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="precompute a witness record (transcript + features)")
    b.add_argument("src")
    b.add_argument("out")
    b.add_argument("--id")
    b.add_argument("--t-start", type=float, help="unix time of the record's first sample")
    b.add_argument("--language", default="en")
    m = sub.add_parser("match", help="match a clip against a witness record")
    m.add_argument("record")
    m.add_argument("clip")
    m.add_argument("--json")
    m.add_argument("--language", default="en")
    a = ap.parse_args(argv)
    if a.cmd == "build":
        r = record.build(a.src, a.out, a.id, a.language, t_start_unix=a.t_start)
        print(json.dumps(r.meta, indent=1))
        return 0
    res = match.match(record.load(a.record), audio.decode(a.clip), a.language)
    out = json.dumps(res, indent=1, ensure_ascii=False)
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            f.write(out)
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
