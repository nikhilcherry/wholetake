"""CLI entrypoint for the Wholetake X reply bot: `python -m bot run`."""
import argparse
import sys

from bot.runner import BotRunner


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bot", description="Wholetake X reply bot")
    sub = parser.add_subparsers(dest="cmd", required=True)
    run_p = sub.add_parser("run", help="Poll mentions and reply with verdicts")
    run_p.add_argument("--once", action="store_true", help="Run a single poll cycle and exit")
    args = parser.parse_args(argv)

    if args.cmd == "run":
        runner = BotRunner.from_env()
        if args.once:
            runner.poll_once()
        else:
            runner.run_forever()
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
