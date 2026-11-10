"""Bounded C++ lifecycle differential driver using the actual Python reducer.

The TSV protocol is research-only. It carries no credentials, touches no source
history and never opens a financial database. Every expected transition is
computed by grid.domain.reducer.reduce, including rejection and recovery states.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import selectors

# Offline local C++ fixture process with bounded I/O.
import subprocess  # nosec B404
import sys
import tempfile
import time
from collections.abc import Sequence
from dataclasses import dataclass, fields, is_dataclass, replace
from decimal import Decimal as D
from decimal import localcontext
from pathlib import Path
from typing import Literal, cast

from grid.domain.model import (
    ActualFee,
    CancelAcknowledged,
    Context,
    Control,
    Event,
    Fill,
    Mode,
    OrderReport,
    Reconciled,
    Side,
    Start,
    State,
    SubmissionUnknown,
)
from grid.domain.numbers import ARITHMETIC, ZERO, DomainError
from grid.domain.reducer import reduce
from grid.persistence.codec import amount
from grid.research.artifacts import canonical, digest
from grid.research.module1_reference import (
    Recorder,
    fixture_context,
    lifecycle_cases,
    observation,
)

SCHEMA = "grid-module1-lifecycle-differential-v1"
MAX_EVENTS = 256
MAX_INPUT_BYTES = 262144
MAX_OUTPUT_BYTES = 16 * 1024 * 1024


@dataclass(frozen=True)
class Case:
    name: str
    context: Context
    initial: State
    events: tuple[Event, ...]


def _recorded(row: dict[str, object]) -> Case:
    return Case(
        str(row["name"]),
        cast(Context, row["context"]),
        cast(State, row["initial_state"]),
        tuple(cast(Event, step["event"]) for step in cast(list[dict[str, object]], row["steps"])),
    )


def cases() -> tuple[Case, ...]:
    """Funded-empty traces, including reproducible adversarial interleavings."""
    originals = {case.name: case for case in map(_recorded, lifecycle_cases())}
    result = []
    for name, case in originals.items():
        if name.startswith("sell_fee_"):
            parent = originals[name.replace("sell_fee_", "partial_fee_", 1)]
            if expected(parent)[-1]["after"] != observation(case.initial, case.context):
                raise DomainError("continued SELL case does not match its funded parent")
            case = replace(case, initial=parent.initial, events=parent.events + case.events)
        result.append(case)
    context = fixture_context()
    for name in (
        "duplicate_and_conflicting_fill",
        "unknown_then_reconciled",
        "missing_fee_evidence",
        "terminal_before_fill",
        "control_hold",
        "quote_rebate",
    ):
        recorder = Recorder(context)
        recorder.apply(Start(recorder.state.scope))
        seed = recorder.order(Side.BUY)
        if name == "duplicate_and_conflicting_fill":
            event = Fill(recorder.state.scope, "identical", seed.id, seed.amount, seed.price, ())
            recorder.apply(event)
            recorder.apply(event)
            recorder.apply(replace(event, price=seed.price - 1))
        elif name == "unknown_then_reconciled":
            recorder.apply(SubmissionUnknown(recorder.state.scope, seed.id))
            recorder.apply(OrderReport(recorder.state.scope, seed.id, "open", ZERO, False))
            recorder.apply(
                Reconciled(
                    recorder.state.scope,
                    1,
                    1,
                    recorder.state.reconcile_reason,
                    recorder.state.reconcile_revision,
                )
            )
        elif name == "missing_fee_evidence":
            recorder.fill(seed, "missing", fees=None)
        elif name == "terminal_before_fill":
            recorder.apply(OrderReport(recorder.state.scope, seed.id, "filled", seed.amount, False))
            recorder.fill(seed, "terminal-trade")
            recorder.apply(
                Reconciled(
                    recorder.state.scope,
                    1,
                    1,
                    recorder.state.reconcile_reason,
                    recorder.state.reconcile_revision,
                )
            )
        elif name == "control_hold":
            recorder.acknowledge()
            recorder.apply(Control(recorder.state.scope, 1, Mode.PAUSED))
            recorder.apply(Control(recorder.state.scope, 2, Mode.HOLD))
            recorder.apply(CancelAcknowledged(recorder.state.scope, seed.id))
            recorder.cancel(seed)
            recorder.apply(Control(recorder.state.scope, 3, Mode.ACTIVE))
            recorder.apply(Control(recorder.state.scope, 2, Mode.HOLD))
        elif name == "quote_rebate":
            recorder.fill(seed, "rebate", fees=(ActualFee("QUOTE", D("-0.01"), D("-0.01")),))
        result.append(_recorded(recorder.result(name)))

    # Deterministic bounded permutations use only events valid for the current
    # actual reducer state; no alternative accounting implementation is involved.
    for seed_number in range(4):
        # Reproducible nonsecurity event fixtures.
        rng = random.Random(seed_number)  # nosec B311
        recorder = Recorder(context)
        recorder.apply(Start(recorder.state.scope))
        recorder.fill(recorder.order(Side.BUY), "seed")
        recorder.acknowledge()
        buys = tuple(order for order in recorder.state.working if order.side is Side.BUY)
        recorder.fill(recorder.order(Side.SELL), "full-sell")
        chosen = rng.randrange(len(buys))
        actions = list(range(len(buys)))
        rng.shuffle(actions)
        for index in actions:
            buy = buys[index]
            if index == chosen:
                recorder.fill(buy, f"late-{seed_number}", D("0.4"))
                # Exact duplicate is observed twice, but must post only once.
                recorder.fill(buy, f"late-{seed_number}", D("0.4"))
            recorder.cancel(buy, D("0.4") if index == chosen else ZERO)
        result.append(_recorded(recorder.result(f"seeded_drain_{seed_number}")))
    return tuple(result)


def expected(case: Case) -> tuple[dict[str, object], ...]:
    if not 1 <= len(case.events) <= MAX_EVENTS:
        raise DomainError("lifecycle event bound exceeded")
    state = case.initial
    rows = []
    for event in case.events:
        transition = reduce(state, event, case.context)
        rows.append(
            {
                "accepted": transition.accepted,
                "commands": tuple(
                    {"type": type(command).__name__, "value": command}
                    for command in transition.commands
                ),
                "notices": transition.notices,
                "after": observation(transition.state, case.context),
            }
        )
        state = transition.state
    return tuple(rows)


def _token(value: object) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return str(int(value))
    text = amount(value) if isinstance(value, D) else str(value)
    if not text or any(character in text for character in "\t\n\r"):
        raise DomainError("TSV token contains a delimiter or is empty")
    return text


def event_fields(event: Event) -> tuple[object, ...]:
    if isinstance(event, Start):
        return ("Start",)
    if isinstance(event, Fill):
        fees: list[object] = []
        for fee in event.fees or ():
            fees.extend((fee.currency, fee.amount, fee.quote_value))
        return (
            "Fill",
            event.trade_id,
            event.order_id,
            event.amount,
            event.price,
            -1 if event.fees is None else len(event.fees),
            *fees,
        )
    if isinstance(event, OrderReport):
        return (
            "OrderReport",
            event.order_id,
            event.status,
            event.cumulative,
            event.trades_reconciled,
        )
    if isinstance(event, SubmissionUnknown | CancelAcknowledged):
        return (type(event).__name__, event.order_id)
    if isinstance(event, Control):
        return ("Control", event.revision, event.mode.value)
    if isinstance(event, Reconciled):
        return (
            "Reconciled",
            event.config_revision,
            event.market_generation,
            event.expected_reason,
            event.revision,
        )
    raise DomainError("unsupported lifecycle event")


def export(case: Case) -> bytes:
    """Versioned strict TSV: scalar configuration, funded empty state, event rows."""
    context, state = case.context, case.initial
    market, policy, book = context.market, context.policy, context.book
    if market.symbol != "BASE/QUOTE" or market.price.tick is None or market.amount.tick is None:
        raise DomainError("lifecycle v1 fixtures require BASE/QUOTE with tick precision")
    if book is None:
        raise DomainError("lifecycle v1 fixtures require an explicit book")
    empty = State(
        state.scope,
        state.quote_cash,
        state.fee_balances,
        state.config_revision,
        state.market_generation,
    )
    if state != empty or (state.config_revision, state.market_generation) != (
        context.config_revision,
        market.generation,
    ):
        raise DomainError("lifecycle TSV requires matched funded empty initial state")
    if not 1 <= len(case.events) <= MAX_EVENTS or any(
        event.scope != state.scope for event in case.events
    ):
        raise DomainError("bounded same-scope lifecycle events required")
    rows: list[tuple[object, ...]] = [
        ("GRID_MODULE1_TRACE", 1),
        ("SCOPE", *(getattr(state.scope, field.name) for field in fields(state.scope))),
    ]
    config: dict[str, object] = {
        "config_revision": context.config_revision,
        "now_ms": context.now_ms,
        "rules_observed_ms": context.rules_observed_ms,
        "fees_observed_ms": context.fees_observed_ms,
        "allow_new_exposure": context.allow_new_exposure,
        "market_generation": market.generation,
        "price_tick": market.price.tick,
        "amount_tick": market.amount.tick,
        "book_bid": book.bid,
        "book_ask": book.ask,
        "book_observed_ms": book.observed_ms,
        "book_generation": book.market_generation,
        "initial_quote_cash": state.quote_cash,
        "base_quantum": D("1e-12"),
        "quote_quantum": D("1e-12"),
        "token_quantum": D("1e-12"),
    }
    for name, limits in (
        ("price", market.prices),
        ("buy_amount", market.buy_amount),
        ("buy_cost", market.buy_cost),
        ("sell_amount", market.sell_amount),
        ("sell_cost", market.sell_cost),
    ):
        config[f"{name}_min"], config[f"{name}_max"] = limits.minimum, limits.maximum
    config.update({field.name: getattr(policy, field.name) for field in fields(policy)})
    rows.extend(("CONFIG", key, value) for key, value in sorted(config.items()))
    for side, fee in (("buy", context.buy_fee), ("sell", context.sell_fee)):
        rows.append(
            (
                "FEE_RULE",
                side,
                fee.currency,
                fee.rate,
                fee.increment,
                fee.quote_per_token,
                fee.max_settlements,
            )
        )
    rows.extend(("TOKEN", money.currency, money.amount) for money in state.fee_balances)
    rows.extend(("EVENT", *event_fields(event)) for event in case.events)
    result = ("\n".join("\t".join(map(_token, row)) for row in rows) + "\n").encode()
    if len(result) > MAX_INPUT_BYTES:
        raise DomainError("lifecycle input byte bound exceeded")
    return result


def compare_value(expected_value: object, observed: object, path: str = "transition") -> None:
    """No omitted state fields or tolerance; rationals only for non-money averages."""
    if isinstance(expected_value, D):
        if not isinstance(observed, str):
            raise DomainError(f"{path}: financial decimal must be encoded as a string")
        if "/" in observed:
            if not (path.endswith(".anchor") or path.endswith(".execution_price")):
                raise DomainError(
                    f"{path}: rational diagnostics cannot replace exact financial money"
                )
            numerator, denominator = observed.split("/")
            with localcontext(ARITHMETIC):
                parsed = D(numerator) / D(denominator)
        else:
            parsed = D(observed)
        if not parsed.is_finite() or parsed != expected_value:
            raise DomainError(f"{path}: expected {expected_value}, observed {observed}")
        return
    if is_dataclass(expected_value) and not isinstance(expected_value, type):
        expected_value = {
            field.name: getattr(expected_value, field.name) for field in fields(expected_value)
        }
    if isinstance(expected_value, dict):
        if not isinstance(observed, dict) or set(observed) != set(expected_value):
            raise DomainError(f"{path}: complete object keys differ")
        for key, value in expected_value.items():
            compare_value(value, observed[key], f"{path}.{key}")
    elif isinstance(expected_value, tuple | list):
        if not isinstance(observed, list) or len(expected_value) != len(observed):
            raise DomainError(f"{path}: array type or length differs")
        for index, (left, right) in enumerate(zip(expected_value, observed, strict=True)):
            compare_value(left, right, f"{path}[{index}]")
    elif isinstance(expected_value, bool | int):
        if type(observed) is not type(expected_value) or observed != expected_value:
            raise DomainError(f"{path}: exact scalar type/value differs")
    elif observed != expected_value:
        raise DomainError(f"{path}: expected {expected_value!r}, observed {observed!r}")


def _invoke(
    program: Path,
    source: Path | tuple[str, ...],
    timeout: float = 30.0,
    *,
    command: Literal["trace", "bars", "--nav-probe"] = "trace",
) -> tuple[int, bytes, bytes]:
    """Bound total stdout/stderr bytes while draining both pipes; always reap child."""
    if command not in ("trace", "bars", "--nav-probe"):
        raise DomainError("unsupported local research subprocess command")
    if command == "--nav-probe":
        if (
            not isinstance(source, tuple)
            or len(source) != 13
            or any(
                not isinstance(value, str) or len(value) > 128 or "\x00" in value
                for value in source
            )
        ):
            raise DomainError("bounded explicit NAV probe arguments required")
        arguments = source
    elif isinstance(source, Path):
        arguments = (str(source),)
    else:
        raise DomainError("local research trace input must be a path")
    started = time.monotonic()
    # Explicit argv without a shell; enforce the deadline and output cap below.
    with subprocess.Popen(  # nosec B603
        [str(program), command, *arguments], stdout=subprocess.PIPE, stderr=subprocess.PIPE
    ) as child:
        if child.stdout is None or child.stderr is None:
            raise DomainError("lifecycle subprocess pipes unavailable")
        buffers = {"stdout": bytearray(), "stderr": bytearray()}
        with selectors.DefaultSelector() as selector:
            selector.register(child.stdout, selectors.EVENT_READ, "stdout")
            selector.register(child.stderr, selectors.EVENT_READ, "stderr")
            try:
                while selector.get_map():
                    remaining = timeout - (time.monotonic() - started)
                    if remaining <= 0:
                        raise DomainError("lifecycle executable exceeded deadline")
                    for key, _ in selector.select(min(remaining, 0.1)):
                        block = os.read(key.fd, 65536)
                        if not block:
                            selector.unregister(key.fileobj)
                        else:
                            buffers[key.data].extend(block)
                            if sum(map(len, buffers.values())) > MAX_OUTPUT_BYTES:
                                raise DomainError("lifecycle executable exceeded output byte bound")
                status = child.wait(timeout=max(0.001, timeout - (time.monotonic() - started)))
            except BaseException:
                child.kill()
                child.wait()
                raise
        return status, bytes(buffers["stdout"]), bytes(buffers["stderr"])


def compare(executable: Path, selected: tuple[Case, ...] | None = None) -> dict[str, object]:
    program = executable.resolve(strict=True)
    if not program.is_file():
        raise DomainError("lifecycle executable must be a regular file")
    selected = cases() if selected is None else selected
    if not 1 <= len(selected) <= 64 or len({case.name for case in selected}) != len(selected):
        raise DomainError("unique bounded lifecycle roster required")
    results: list[dict[str, object]] = []
    with tempfile.TemporaryDirectory(prefix="grid-module1-lifecycle-") as directory:
        for index, case in enumerate(selected):
            payload, wanted = export(case), expected(case)
            source = Path(directory) / f"trace-{index}.tsv"
            source.write_bytes(payload)
            row: dict[str, object] = {
                "case": case.name,
                "events": len(case.events),
                "input_sha256": hashlib.sha256(payload).hexdigest(),
                "expected_sha256": digest(wanted),
            }
            try:
                status, stdout, stderr = _invoke(program, source)
                row.update(
                    exit_code=status,
                    stderr=stderr.decode(errors="replace"),
                    stdout_sha256=hashlib.sha256(stdout).hexdigest(),
                )
                if status != 0 or stderr:
                    raise DomainError("C++ trace did not complete cleanly")
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
        "numeric_contract": (
            "Exact money/state/order equality; rational anchor/lot display rounds to Decimal96."
        ),
        "limitations": "Finite synthetic fixtures, not historical or live exchange certification.",
    }
    result["result_sha256"] = digest(result)
    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--export", help="emit one named TSV fixture to stdout")
    mode.add_argument("--cpp", type=Path, help="run exact C++ transition comparisons")
    parser.add_argument("--case", help="restrict C++ comparison to one named fixture")
    arguments = parser.parse_args(argv)
    roster = cases()
    if arguments.export:
        selected = next((case for case in roster if case.name == arguments.export), None)
        if selected is None:
            parser.error("unknown fixture")
        sys.stdout.buffer.write(export(selected))
        return 0
    if arguments.cpp:
        if arguments.case:
            roster = tuple(case for case in roster if case.name == arguments.case)
            if not roster:
                parser.error("unknown fixture")
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
