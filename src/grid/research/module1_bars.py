"""Per-bar C++ differential observations from the existing Python Simulator."""

from __future__ import annotations

import argparse
import hashlib
import json

# Only the shared bounded local parity process is invoked.
import subprocess  # nosec B404
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass, replace
from decimal import Decimal as D
from pathlib import Path

from grid.domain.market import FeeRule, Money
from grid.domain.model import Context, Scope, Start, State
from grid.domain.numbers import DomainError
from grid.research.artifacts import canonical, digest
from grid.research.history import MINUTE, RECORD, Bar, Source, decode
from grid.research.module1_lifecycle import Case, _invoke, compare_value
from grid.research.module1_lifecycle import export as export_lifecycle
from grid.research.module1_reference import fixture_context, observation
from grid.research.replay import MODELS, Allocation, Capital, Model, Replay, Simulator

SCHEMA = "grid-module1-bar-differential-v1"
MAX_BARS = 512


@dataclass(frozen=True)
class BarCase:
    name: str
    context: Context
    bars: tuple[Bar, ...]
    model: Model = "strict_skip_ambiguous"
    latency_bars: int = 1
    synthetic_spread: D = D("0.0001")
    quote_cash: D = D("1000")
    fee_balances: tuple[Money, ...] = ()


def _bars(prices: tuple[tuple[int, int, int, int], ...]) -> tuple[Bar, ...]:
    return tuple(
        decode(RECORD.pack(1704067200000 + index * MINUTE, *row))
        for index, row in enumerate(prices)
    )


def cases() -> tuple[BarCase, ...]:
    context = fixture_context()
    round_trip = (
        (100, 101, 99, 100),
        (100, 101, 99, 100),
        (110, 111, 100, 110),
        (110, 111, 109, 110),
    )
    paths = (
        ("round_trip", round_trip, 1),
        ("same_bar_sell_forbidden", ((100, 101, 99, 100), (100, 150, 99, 100)), 1),
        (
            "ambiguous_sharp_drop",
            (
                (100, 101, 99, 100),
                (100, 101, 99, 100),
                (100, 150, 40, 50),
                (50, 70, 45, 60),
                (60, 100, 55, 90),
                (90, 130, 85, 120),
                (120, 121, 119, 120),
            ),
            1,
        ),
        (
            "cancel_fill_race",
            ((100, 101, 99, 100),) * 4
            + ((90, 100, 89, 90), (110, 120, 95, 110), (110, 111, 109, 110)),
            2,
        ),
        (
            "touch_is_not_strict_cross",
            ((100, 101, 99, 100), (100, 150, 100, 100), (110, 110, 100, 110)),
            1,
        ),
        ("compacted_rounds", ((100, 150, 99, 100),) * 128, 1),
    )
    result = [
        BarCase(f"{name}__{model}", context, _bars(prices), model, latency)
        for name, prices, latency in paths
        for model in MODELS
    ]
    for currency in ("BASE", "QUOTE", "TOKEN"):
        fee = FeeRule(currency, D("0.001"), D("0.0001"), D("2") if currency == "TOKEN" else None, 4)
        funded = (Money("TOKEN", D("10")),) if currency == "TOKEN" else ()
        for model in MODELS:
            result.append(
                BarCase(
                    f"fees_{currency.lower()}__{model}",
                    replace(context, buy_fee=fee, sell_fee=fee),
                    _bars(round_trip),
                    model,
                    fee_balances=funded,
                )
            )
    return tuple(result)


def setup(case: BarCase) -> tuple[Replay, State]:
    if not 2 <= len(case.bars) <= MAX_BARS:
        raise DomainError("bounded bar oracle requires 2..512 bars")
    start = case.bars[0].timestamp
    for index, bar in enumerate(case.bars):
        if bar.timestamp != start + index * MINUTE or decode(bytes.fromhex(bar.raw_hex)) != bar:
            raise DomainError("bar oracle requires exact consecutive decoded source records")
    context = case.context
    scope = Scope(
        "reference", "allocated-account", context.market.symbol, "allocation", "grid", "cycle"
    )
    source = Source(
        Path("caller-provided-fixture-bars"), scope.venue, scope.symbol, 0, len(case.bars)
    )
    allocation = Allocation(scope, source, context, case.quote_cash, case.fee_balances)
    capital = tuple(
        Capital(scope.venue, scope.account, m.currency, m.amount)
        for m in (Money(context.market.quote, case.quote_cash), *case.fee_balances)
    )
    spec = Replay(
        (allocation,),
        capital,
        start,
        start + len(case.bars) * MINUTE,
        "actual-python-simulator-oracle",
        case.model,
        case.latency_bars,
        MAX_BARS,
        case.synthetic_spread,
    )
    return spec, State(
        scope,
        case.quote_cash,
        case.fee_balances,
        context.config_revision,
        context.market.generation,
    )


def expected(case: BarCase) -> tuple[dict[str, object], ...]:
    spec, state = setup(case)
    simulator = Simulator(state, case.context, case.latency_bars)
    rows = []
    for index, bar in enumerate(case.bars):
        simulator.step(bar, index, spec)
        rows.append(
            {
                "after": observation(simulator.state, simulator.context),
                "fill_count": simulator.fill_count,
                "sell_count": simulator.sell_count,
                "fees_quote": simulator.fees_quote,
                "fees": dict(simulator.fees),
                "turnover": simulator.turnover,
                "gross_profit": simulator.gross_profit,
                "gross_loss": simulator.gross_loss,
                "ambiguous_bars": simulator.ambiguous_bars,
                "notices": dict(simulator.notices),
            }
        )
    return tuple(rows)


def export(case: BarCase) -> bytes:
    _, state = setup(case)
    header = export_lifecycle(Case(case.name, case.context, state, (Start(state.scope),)))
    lines = [line for line in header.splitlines() if not line.startswith(b"EVENT\t")]
    lines.extend(
        (
            f"BAR_CONFIG\tmodel\t{case.model}".encode(),
            f"BAR_CONFIG\tlatency_bars\t{case.latency_bars}".encode(),
            f"BAR_CONFIG\tsynthetic_spread\t{case.synthetic_spread}".encode(),
        )
    )
    lines.extend(f"BAR\t{bar.raw_hex}".encode() for bar in case.bars)
    return b"\n".join(lines) + b"\n"


def compare(executable: Path, selected: tuple[BarCase, ...] | None = None) -> dict[str, object]:
    program = executable.resolve(strict=True)
    if not program.is_file():
        raise DomainError("bar executable must be a regular file")
    selected = cases() if selected is None else selected
    if not 1 <= len(selected) <= 64 or len({case.name for case in selected}) != len(selected):
        raise DomainError("unique bounded bar fixture roster required")
    results: list[dict[str, object]] = []
    with tempfile.TemporaryDirectory(prefix="grid-module1-bars-") as directory:
        for index, case in enumerate(selected):
            payload, wanted = export(case), expected(case)
            source = Path(directory) / f"bars-{index}.tsv"
            source.write_bytes(payload)
            row: dict[str, object] = {
                "case": case.name,
                "bars": len(case.bars),
                "input_sha256": hashlib.sha256(payload).hexdigest(),
                "expected_sha256": digest(wanted),
            }
            try:
                status, stdout, stderr = _invoke(program, source, command="bars")
                row.update(
                    exit_code=status,
                    stderr=stderr.decode(errors="replace"),
                    stdout_sha256=hashlib.sha256(stdout).hexdigest(),
                )
                if status != 0 or stderr:
                    raise DomainError("C++ bar trace did not complete cleanly")
                observed = [json.loads(line) for line in stdout.splitlines()]
                compare_value(wanted, observed)
            except (ValueError, ArithmeticError, OSError, subprocess.TimeoutExpired) as exc:
                row.update(status="FAIL", difference=str(exc))
            else:
                row["status"] = "PASS"
            results.append(row)
    result: dict[str, object] = {
        "schema": SCHEMA,
        "executable": str(program),
        "cases": results,
        "passed": sum(row["status"] == "PASS" for row in results),
        "failed": sum(row["status"] != "PASS" for row in results),
        "scope": "Full Simulator state and metrics after every bar, all three OHLC conventions.",
        "limitations": "This baseline gate does not certify portfolio TP or research policies.",
    }
    result["result_sha256"] = digest(result)
    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--export", help="emit a named bar fixture TSV")
    mode.add_argument("--cpp", type=Path, help="compare actual Python Simulator with C++")
    parser.add_argument("--case", help="restrict C++ comparison to one named case")
    arguments = parser.parse_args(argv)
    roster = cases()
    if arguments.export:
        selected = next((case for case in roster if case.name == arguments.export), None)
        if selected is None:
            parser.error("unknown bar fixture")
        sys.stdout.buffer.write(export(selected))
        return 0
    if arguments.cpp:
        if arguments.case:
            roster = tuple(case for case in roster if case.name == arguments.case)
            if not roster:
                parser.error("unknown bar fixture")
        report = compare(arguments.cpp, roster)
        sys.stdout.buffer.write(canonical(report) + b"\n")
        return int(report["failed"] != 0)
    if arguments.case:
        parser.error("--case requires --cpp")
    sys.stdout.buffer.write(
        canonical({"schema": SCHEMA, "cases": tuple(case.name for case in roster)}) + b"\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
