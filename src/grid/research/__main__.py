"""Offline research CLI; outputs must be explicitly outside the repository."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from grid.domain.numbers import DomainError
from grid.research.artifacts import publish
from grid.research.benchmark import benchmark
from grid.research.config import load
from grid.research.demo import create
from grid.research.history import Source, validate
from grid.research.replay import run


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="GRID I5 offline research; LIVE DISABLED")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("validate", "benchmark"):
        command = commands.add_parser(name)
        command.add_argument("source", type=Path)
        command.add_argument("--venue", required=True)
        command.add_argument("--symbol", required=True)
        command.add_argument("--offset", type=int, default=0)
        command.add_argument("--count", type=int, required=True)
        command.add_argument("--output", type=Path, required=True)
        command.add_argument("--code-revision", required=True)
        command.add_argument("--format-evidence")
        command.add_argument("--identity-evidence")
        command.add_argument("--collection-evidence")
        command.add_argument("--verified-unleveraged-spot", action="store_true")
    replay = commands.add_parser("replay")
    replay.add_argument("--config", type=Path, required=True)
    replay.add_argument("--output", type=Path, required=True)
    demo = commands.add_parser("demo")
    demo.add_argument("--output-directory", type=Path, required=True)
    demo.add_argument("--code-revision", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "demo":
            print(create(args.output_directory, args.code_revision))
            return 0
        if args.command == "replay":
            result: object = run(load(args.config))
            code = 0
        else:
            source = Source(
                args.source,
                args.venue,
                args.symbol,
                args.offset,
                args.count,
                args.format_evidence,
                args.identity_evidence,
                args.collection_evidence,
                args.verified_unleveraged_spot,
            )
            if args.command == "validate":
                manifest = validate(source)
                result = {
                    "code_revision": args.code_revision,
                    "manifest": manifest,
                    "replay_eligible": manifest.replay_eligible,
                }
                code = 0 if manifest.replay_eligible else 1
            else:
                result = {
                    "code_revision": args.code_revision,
                    **benchmark(source, args.output.parent),
                }
                code = 0
        publish(args.output, result)
        print(args.output)
        return code
    except (DomainError, OSError) as exc:
        print(f"research refused: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
