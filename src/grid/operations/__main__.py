"""Explicit, finite offline operational preparation command."""

import argparse
import asyncio
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import NoReturn

from grid.operations.experiment import experiment
from grid.research.artifacts import output_directory, publish


class Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        self.exit(2, "operations: invalid arguments; use --help\n")


def main(argv: Sequence[str] | None = None) -> int:
    parser = Parser(description="GRID I8 finite synthetic preparation; LIVE DISABLED")
    parser.add_argument("kind", choices=("dry-run", "benchmark"))
    parser.add_argument("--code-revision", required=True)
    parser.add_argument("--sqlite-source-id", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        output_directory(args.output.parent)
        if args.output.exists() or args.output.is_symlink():
            raise ValueError("existing result")
        report = asyncio.run(experiment(args.kind, args.code_revision, args.sqlite_source_id))
        publish(args.output, report)
    except Exception:
        # Arbitrary arguments, paths, exception messages and chained traces stay local.
        print("operations refused or failed; no successful report published", file=sys.stderr)
        return 2
    print("Synthetic preparation report published; observed soak remains NOT TESTED.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
