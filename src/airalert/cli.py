"""Lazy command dispatch keeps unfinished pipeline stages from breaking the scaffold."""

import argparse
import importlib
import sys
from datetime import UTC, datetime


def main() -> int:
    """Load only the selected stage, and identify its owner when it is not built yet."""
    parser = argparse.ArgumentParser(prog="airalert", description="Air alert forecasting pipeline")
    commands = parser.add_subparsers(dest="command", required=True)
    stages = (
        ("fetch", "data", "fetch", "A1"),
        ("episodes", "data", "build_episodes", "A1"),
        ("geo", "geo", "build", "A2"),
        ("features", "features", "build", "B1"),
        ("backtest", "backtest", "run", "B2"),
        ("precompute", "precompute", "run", "C2"),
        ("serve", "api", "serve", "C2"),
        ("backfill", "ingest", "catch_up", "I1"),
        ("live-once", "ingest", "live_state", "I1"),
    )
    for command, module, function, owner in stages:
        subparser = commands.add_parser(command)
        subparser.set_defaults(module=f"airalert.{module}", function=function, owner=owner)
        if command == "backtest":
            subparser.add_argument(
                "--final", action="store_true", help="Score the held-out test set"
            )
        elif command == "serve":
            subparser.add_argument("--port", type=int, default=8000)
    train = commands.add_parser("train")
    train.add_argument("--model", choices=("B0", "B1", "M1", "D0", "D1"), required=True)
    args = parser.parse_args()
    call_args: tuple[object, ...] = ()
    if args.command == "train":
        if args.model == "B0":
            args.module, args.owner = "airalert.models.baseline", "B2"
        elif args.model in {"B1", "M1"}:
            args.module, args.owner = "airalert.models.onset", "C1"
            call_args = (args.model,)
        else:
            args.module, args.owner = "airalert.models.duration", "B3"
            call_args = (args.model,)
        args.function = "train"
    elif args.command == "backtest":
        call_args = (args.final,)
    elif args.command == "serve":
        call_args = (args.port,)
    elif args.command in {"backfill", "live-once"}:
        # Both ingest entry points take the current aware UTC time.
        call_args = (datetime.now(UTC),)
    try:
        module = importlib.import_module(args.module)
    except ModuleNotFoundError as error:
        # A missing dependency inside an implemented module is a real error, not an absent stage.
        if error.name is None or not (
            args.module == error.name or args.module.startswith(f"{error.name}.")
        ):
            raise
        module = None
    target = getattr(module, args.function, None)
    if target is None:
        print(f"{args.command}: not implemented yet (owner {args.owner})", file=sys.stderr)
        return 1
    target(*call_args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
