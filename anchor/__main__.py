"""CLI entry point for Wholetake on-chain anchoring.

    python -m anchor wallet new|address|balance
    python -m anchor run [--root data/witness] [--every 60]
    python -m anchor ots [--root data/witness]
    python -m anchor verify <stream_dir>
"""
import argparse
import sys
from decimal import Decimal
from pathlib import Path

from anchor import daemon, eas, ots, verify, wallet


def _cmd_wallet(args: argparse.Namespace) -> int:
    action = args.wallet_action
    try:
        if action == "new":
            addr = wallet.create_wallet()
            print(addr)
            return 0
        elif action == "address":
            print(wallet.get_address())
            return 0
        elif action == "balance":
            acct = wallet.load_account()
            client = eas.EASClient(account=acct)
            wei = client.balance_wei(acct.address)
            eth = Decimal(wei) / Decimal(10**18)
            print(f"{acct.address} {eth:.8f} ETH ({wei} wei)")
            return 0
        else:
            print(f"ERROR: unknown wallet action {action!r}", file=sys.stderr)
            return 1
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1


def _cmd_run(args: argparse.Namespace) -> int:
    try:
        acct = wallet.load_account()
        client = eas.EASClient(account=acct)
        d = daemon.AnchorDaemon(root=Path(args.root), client=client)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    d.run_forever(every=float(args.every))
    return 0


def _cmd_ots(args: argparse.Namespace) -> int:
    res = ots.run_ots_cycle(witness_root=Path(args.root))
    if res.get("stamped"):
        print(f"Stamped: {res['stamped']['file']} ({len(res['stamped']['streams'])} streams)")
    else:
        print("OTS: no new anchored heads to stamp")
    if res.get("upgraded"):
        print(f"Upgraded: {res['upgraded']}")
    return 0


def _cmd_verify(args: argparse.Namespace) -> int:
    try:
        client = eas.EASClient(account=None)
    except Exception as e:
        print(f"ERROR connecting to RPC: {e}", file=sys.stderr)
        return 1
    return verify.run_verify_cli(Path(args.stream_dir), client)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m anchor", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="subcmd", required=True)

    p_wallet = sub.add_parser("wallet", help="manage throwaway anchoring wallet")
    p_wallet.add_argument("wallet_action", choices=["new", "address", "balance"])

    p_run = sub.add_parser("run", help="run the EAS + OTS anchoring daemon")
    p_run.add_argument("--root", default="data/witness", help="witness data root directory")
    p_run.add_argument("--every", type=float, default=60.0, help="seconds between ticks")

    p_ots = sub.add_parser("ots", help="run an hourly OpenTimestamps batch + upgrade cycle now")
    p_ots.add_argument("--root", default="data/witness", help="witness data root directory")

    p_verify = sub.add_parser("verify", help="verify a stream's anchors.jsonl against chain.jsonl and Base Sepolia")
    p_verify.add_argument("stream_dir", help="path to stream witness directory")

    args = ap.parse_args(argv)
    if args.subcmd == "wallet":
        return _cmd_wallet(args)
    elif args.subcmd == "run":
        return _cmd_run(args)
    elif args.subcmd == "ots":
        return _cmd_ots(args)
    elif args.subcmd == "verify":
        return _cmd_verify(args)
    return 1


if __name__ == "__main__":
    sys.exit(main())
