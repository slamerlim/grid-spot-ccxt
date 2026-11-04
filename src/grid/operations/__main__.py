"""Explicit, finite offline operational preparation command."""

import argparse
import asyncio
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import NoReturn

from grid.operations.contention import experiment as contention
from grid.operations.experiment import experiment
from grid.research.artifacts import output_directory, publish


class Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        self.exit(2, "operations: invalid arguments; use --help\n")


def main(argv: Sequence[str] | None = None) -> int:
    parser = Parser(description="GRID I8 finite synthetic preparation; LIVE DISABLED")
    parser.add_argument("kind", choices=("dry-run", "benchmark", "contention"))
    parser.add_argument("--code-revision", required=True)
    parser.add_argument("--sqlite-source-id", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    code = 0
    try:
        output_directory(args.output.parent)
        if args.output.exists() or args.output.is_symlink():
            raise ValueError("existing result")
        report = asyncio.run(
            contention(args.code_revision, args.sqlite_source_id)
            if args.kind == "contention"
            else experiment(args.kind, args.code_revision, args.sqlite_source_id)
        )
        if args.kind == "contention":
            result = report.get("result")
            if (
                not isinstance(result, dict)
                or type(result.get("resource_checks_passed")) is not bool
            ):
                raise ValueError("contention result is missing checks")
            code = 0 if result["resource_checks_passed"] else 1
        publish(args.output, report)
    except KeyboardInterrupt:
        print("operations interrupted; inspect retained artifacts", file=sys.stderr)
        return 130
    except Exception:
        # Arbitrary arguments, paths, exception messages and chained traces stay local.
        print("operations refused or failed; no successful report published", file=sys.stderr)
        return 2
    print("Synthetic preparation report published; observed soak remains NOT TESTED.")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
