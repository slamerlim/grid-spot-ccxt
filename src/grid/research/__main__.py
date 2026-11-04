"""Offline research CLI; outputs must be explicitly outside the repository."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import NoReturn

from grid.domain.numbers import DomainError
from grid.research.artifacts import output_directory, publish
from grid.research.benchmark import benchmark
from grid.research.comparison import run as compare_scenarios
from grid.research.config import load
from grid.research.demo import create
from grid.research.history import Source, validate
from grid.research.lot_reference import compare as compare_lots
from grid.research.portfolio_config import load as load_portfolio
from grid.research.portfolio_replay import run as run_portfolio
from grid.research.provenance import Observation
from grid.research.replay import run
from grid.research.study import run as run_study
from grid.research.study_config import load as load_study


class Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        self.exit(2, "research: invalid arguments; use --help\n")


def configure(command: argparse.ArgumentParser, name: str) -> None:
    if name in ("validate", "benchmark"):
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
    elif name == "replay":
        command.add_argument("--config", type=Path, required=True)
        command.add_argument("--portfolio", type=Path, help="explicit synthetic portfolio settings")
        command.add_argument(
            "--portfolio-cycles",
            type=int,
            help="1..32 cycles with continuous funding; requires --portfolio (default: 1)",
        )
        comparisons = command.add_mutually_exclusive_group()
        comparisons.add_argument(
            "--compare-scenarios",
            action="store_true",
            help="compare three fixed OHLC fill conventions",
        )
        comparisons.add_argument(
            "--compare-lots",
            action="store_true",
            help="compare basket and independent-lot research on the configured OHLC model",
        )
        comparisons.add_argument(
            "--study", type=Path, help="declared walk-forward sensitivity plan"
        )
        command.add_argument("--output", type=Path, required=True)
    elif name == "demo":
        command.add_argument("--output-directory", type=Path, required=True)
        command.add_argument("--code-revision", required=True)
    else:
        raise DomainError("unknown research operation")


def main(
    argv: Sequence[str] | None = None,
    *,
    operation: str | None = None,
    prog: str = "python -m grid.research",
) -> int:
    parser = Parser(prog=prog, description="Bounded offline research; LIVE DISABLED")
    if operation is None:
        commands = parser.add_subparsers(dest="command", required=True)
        for name in ("validate", "benchmark", "replay", "demo"):
            configure(commands.add_parser(name), name)
    else:
        configure(parser, operation)
        parser.set_defaults(command=operation)
    args = parser.parse_args(argv)
    try:
        if args.command == "demo":
            create(args.output_directory, args.code_revision)
            print("Synthetic research bundle published; no exchange observations.")
            return 0
        # Refuse a known occupied/protected destination before replay or derived writes.
        output_directory(args.output.parent)
        if args.output.exists() or args.output.is_symlink():
            raise DomainError("existing result")
        if args.command == "replay":
            spec = load(args.config)
            observation = Observation.start(spec.code_revision)
            settings = None if args.portfolio is None else load_portfolio(args.portfolio)
            if args.portfolio_cycles is not None and settings is None:
                raise DomainError("portfolio cycle limit requires portfolio settings")
            max_cycles = 1 if args.portfolio_cycles is None else args.portfolio_cycles
            result: object
            if args.study is not None:
                if settings is not None:
                    raise DomainError("study runner has no portfolio exit model")
                result = run_study(spec, load_study(args.study))
            elif args.compare_lots:
                if settings is not None:
                    raise DomainError("independent-lot comparison has no portfolio exit model")
                result = compare_lots(spec)
            elif args.compare_scenarios:
                result = compare_scenarios(spec, settings, max_cycles=max_cycles)
            else:
                result = (
                    run(spec)
                    if settings is None
                    else run_portfolio(spec, settings, max_cycles=max_cycles)
                )
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
            observation = Observation.start(args.code_revision)
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
        publish(args.output, observation.finish(args.command, result))
        print("Research report published; simulated execution only.")
        return code
    except KeyboardInterrupt:
        print("research interrupted; inspect retained artifacts before retrying", file=sys.stderr)
        return 130
    except Exception:
        # Arguments, config values, source paths and third-party exceptions may contain secrets.
        print("research refused or failed; inspect output artifacts", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
