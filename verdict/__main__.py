"""CLI entrypoint for `python -m verdict serve` and `python -m verdict check <url-or-file>`."""
import argparse
import json
import os
import sys
from pathlib import Path

from ingest.chain import write_atomic
from verdict.service import default_verdicts_dir, default_witness_root, run_pipeline, serve


def main():
    ap = argparse.ArgumentParser( prog="python -m verdict", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    sp_serve = sub.add_parser("serve", help="run the HTTP verdict service on 127.0.0.1:8790")
    sp_serve.add_argument("--host", default="127.0.0.1", help="bind address (default 127.0.0.1)")
    sp_serve.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("WT_VERDICT_PORT", "8790")),
        help="bind port (default WT_VERDICT_PORT or 8790)",
    )

    sp_check = sub.add_parser("check", help="run the verdict pipeline on a URL or local file without a server")
    sp_check.add_argument("target", help="clip URL or local file path")
    sp_check.add_argument("--witness-root", default=None, help="witness root directory (default data/witness)")
    sp_check.add_argument("--out", default=None, help="output path for verdict.json")

    a = ap.parse_args()
    if a.cmd == "serve":
        serve(host=a.host, port=a.port)
        return

    if a.cmd == "check":
        wroot = Path(a.witness_root) if a.witness_root else default_witness_root()
        try:
            verdict = run_pipeline(a.target, witness_root=wroot)
        except Exception as e:
            print(f"ERROR: {e}", file=sys.stderr)
            sys.exit(1)

        if a.out:
            out_p = Path(a.out)
        else:
            vdir = default_verdicts_dir()
            vdir.mkdir(parents=True, exist_ok=True)
            out_p = vdir / f"{verdict['verdict_id'][:16]}.verdict.json"

        out_p.parent.mkdir(parents=True, exist_ok=True)
        write_atomic(out_p, (json.dumps(verdict, indent=2, sort_keys=True) + "\n").encode("utf-8"))
        print(verdict["headline"])
        print(str(out_p))


if __name__ == "__main__":
    main()
