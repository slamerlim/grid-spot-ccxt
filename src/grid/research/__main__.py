"""Offline research CLI; outputs must be explicitly outside the repository."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import NoReturn

from grid.domain.numbers import DomainError
from grid.research.artifacts import output_directory, publish
from grid.research.benchmark import QueryPlan, benchmark
from grid.research.comparison import run as compare_scenarios
from grid.research.config import load
from grid.research.demo import create
from grid.research.history import Source, validate
from grid.research.lot_reference import compare as compare_lots
from grid.research.merge_benchmark import benchmark as benchmark_merge
from grid.research.merge_benchmark import load as load_merge
from grid.research.portfolio_config import load as load_portfolio
from grid.research.portfolio_replay import run as run_portfolio
from grid.research.provenance import Observation
from grid.research.replay import run
from grid.research.study import compare_lots as compare_study_lots
from grid.research.study import run as run_study
from grid.research.study_config import load as load_study


class Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        self.exit(2, "research: invalid arguments; use --help\n")


def configure(command: argparse.ArgumentParser, name: str) -> None:
    if name in ("validate", "benchmark"):
        command.add_argument("source", type=Path, nargs="?" if name == "benchmark" else None)
        command.add_argument("--venue", required=name == "validate")
        command.add_argument("--symbol", required=name == "validate")
        command.add_argument("--offset", type=int)
        command.add_argument("--count", type=int, required=name == "validate")
        command.add_argument("--output", type=Path, required=True)
        command.add_argument("--code-revision", required=True)
        command.add_argument("--format-evidence")
        command.add_argument("--identity-evidence")
        command.add_argument("--collection-evidence")
        command.add_argument("--verified-unleveraged-spot", action="store_true")
        if name == "benchmark":
            command.add_argument(
                "--merge-plan", type=Path, help="bounded multi-source format merge"
            )
            command.add_argument("--repeats", type=int, default=3, help="1..10 scan/query repeats")
            command.add_argument(
                "--utc-start-ms", type=int, help="optional inclusive UTC query start"
            )
            command.add_argument("--utc-end-ms", type=int, help="optional exclusive UTC query end")
            command.add_argument(
                "--random-windows",
                type=int,
                default=0,
                help="seeded UTC query count; at most 32 queries total",
            )
            command.add_argument(
                "--window-minutes", type=int, help="explicit random query duration"
            )
            command.add_argument(
                "--query-seed", type=int, help="explicit random query seed, 0..2^64-1"
            )
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
        command.add_argument(
            "--study", type=Path, help="declared walk-forward plan; supports --compare-lots"
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
    if args.command == "replay" and args.study is not None and args.compare_scenarios:
        parser.error("study already declares scenarios")
    if args.command == "benchmark":
        if args.merge_plan is not None:
            if (
                any(
                    value is not None
                    for value in (
                        args.source,
                        args.venue,
                        args.symbol,
                        args.count,
                        args.offset,
                        args.format_evidence,
                        args.identity_evidence,
                        args.collection_evidence,
                        args.utc_start_ms,
                        args.utc_end_ms,
                        args.window_minutes,
                        args.query_seed,
                    )
                )
                or args.verified_unleveraged_spot
                or args.random_windows
            ):
                parser.error("merge plan cannot combine with single-source options")
        elif any(value is None for value in (args.source, args.venue, args.symbol, args.count)):
            parser.error("single-source benchmark requires source, venue, symbol and count")
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
                if args.compare_lots and settings is not None:
                    raise DomainError("independent-lot studies have no portfolio exit model")
                plan = load_study(args.study)
                result = (
                    compare_study_lots(spec, plan)
                    if args.compare_lots
                    else run_study(spec, plan, settings, max_cycles=max_cycles)
                )
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
        elif args.command == "benchmark" and args.merge_plan is not None:
            observation = Observation.start(args.code_revision)
            result = {
                "code_revision": args.code_revision,
                **benchmark_merge(
                    load_merge(args.merge_plan), args.output.parent, repeats=args.repeats
                ),
            }
            code = 0
        else:
            source = Source(
                args.source,
                args.venue,
                args.symbol,
                0 if args.offset is None else args.offset,
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
                queries = None
                if (
                    any(
                        value is not None
                        for value in (
                            args.utc_start_ms,
                            args.utc_end_ms,
                            args.window_minutes,
                            args.query_seed,
                        )
                    )
                    or args.random_windows != 0
                ):
                    queries = QueryPlan(
                        args.utc_start_ms,
                        args.utc_end_ms,
                        args.random_windows,
                        args.window_minutes,
                        args.query_seed,
                    )
                result = {
                    "code_revision": args.code_revision,
                    **benchmark(source, args.output.parent, repeats=args.repeats, queries=queries),
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
