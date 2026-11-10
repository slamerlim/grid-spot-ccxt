"""Actual Python portfolio valuation with the declared research NAV rounding wrapper."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass, replace
from decimal import Decimal as D
from pathlib import Path

from grid.domain.market import FeeRule, Limits, Money, Precision, PrecisionMode
from grid.domain.model import Book, Context, State, TransferredLot
from grid.domain.numbers import ZERO, DomainError, exact, units
from grid.exchange.models import BookObservation
from grid.portfolio.model import (
    Conversion,
    ExitRule,
    ExitSlot,
    PoolKey,
    PortfolioPolicy,
    ValuationFacts,
)
from grid.portfolio.orders import plan_exit
from grid.portfolio.valuation import sell_quantity, valuation
from grid.research.artifacts import canonical, digest
from grid.research.module1_lifecycle import _invoke, compare_value
from grid.research.module1_reference import fixture_context, fixture_state
from grid.universe.model import Facts, MarketKey

SCHEMA = "grid-module1-portfolio-differential-v1"
QUOTE_QUANTUM = D("1e-12")


@dataclass(frozen=True)
class NavCase:
    name: str
    owned: D = D("1.23456")
    cash: D = D("100")
    close: D = D("10.12345")
    price_tick: D = D("0.01")
    amount_tick: D = D("0.001")
    min_notional: D = D("5")
    fee_currency: str = "QUOTE"
    rate: D = D("0.001")
    fee_increment: D = D("0.00001")
    settlements: int = 1
    token_quote_rate: D = D("2")
    token_balance: D = D("1")
    nav_order_budget: int = 3

    def arguments(self) -> tuple[str, ...]:
        return tuple(
            str(value)
            for value in (
                self.owned,
                self.cash,
                self.close,
                self.price_tick,
                self.amount_tick,
                self.min_notional,
                self.fee_currency,
                self.rate,
                self.fee_increment,
                self.settlements,
                self.token_quote_rate,
                self.token_balance,
                self.nav_order_budget,
            )
        )


def cases() -> tuple[NavCase, ...]:
    basic = NavCase("quote_single_settlement")
    return (
        basic,
        replace(basic, name="quote_64_settlements", settlements=64),
        replace(basic, name="cash_only", owned=ZERO),
        replace(basic, name="zero_fee", rate=ZERO, settlements=64),
        replace(basic, name="below_amount_tick", owned=D("0.0005")),
        replace(basic, name="below_minimum_notional", owned=D("0.1")),
        replace(
            basic,
            name="base_fee_exact_coverage",
            owned=D("1.001"),
            close=D("10"),
            fee_currency="BASE",
            fee_increment=D("0.001"),
        ),
        replace(basic, name="base_fee_residual", fee_currency="BASE", settlements=64),
        replace(basic, name="token_fee", fee_currency="TOKEN"),
        replace(basic, name="token_unfunded_exit", fee_currency="TOKEN", token_balance=ZERO),
        replace(
            basic,
            name="exact_float32_raw_mark",
            close=D("0.100000001490116119384765625"),
            owned=D("100.123456"),
            price_tick=D("0.00000001"),
        ),
        replace(
            basic,
            name="subquantum_token_asset",
            fee_currency="TOKEN",
            token_quote_rate=D("2.0000001"),
            token_balance=D("0.000000000001"),
        ),
        replace(
            basic,
            name="net_asset_quantization",
            owned=D("1.123456789"),
            amount_tick=D("0.000000001"),
            price_tick=D("0.00000001"),
            close=D("10.12345678"),
            fee_increment=D("0.00000001"),
        ),
    )


@exact
def setup(case: NavCase) -> tuple[State, Context, PortfolioPolicy, ValuationFacts]:
    context = fixture_context()
    market = replace(
        context.market,
        price=Precision(PrecisionMode.TICK, tick=case.price_tick),
        amount=Precision(PrecisionMode.TICK, tick=case.amount_tick),
        prices=Limits(case.price_tick, D("1000000")),
        buy_amount=Limits(case.amount_tick, None),
        sell_amount=Limits(case.amount_tick, None),
        buy_cost=Limits(case.min_notional, None),
        sell_cost=Limits(case.min_notional, None),
    )
    fee = FeeRule(
        case.fee_currency,
        case.rate,
        case.fee_increment,
        case.token_quote_rate if case.fee_currency == "TOKEN" else None,
        case.settlements,
    )
    now = context.now_ms
    context = replace(
        context,
        market=market,
        buy_fee=fee,
        sell_fee=fee,
        book=Book(case.close, case.close + case.price_tick, now, market.generation),
    )
    balances = (Money("TOKEN", case.token_balance),) if case.fee_currency == "TOKEN" else ()
    state = replace(
        fixture_state(cash=case.cash),
        fee_balances=balances,
        lots=(TransferredLot("synthetic-valuation-owned-inventory", case.owned, case.close),)
        if case.owned
        else (),
    )
    rule = ExitRule(state.scope, "bid", case.price_tick, D("0.05"), ZERO, 60000, 3)
    policy = PortfolioPolicy(
        "QUOTE",
        (),
        (replace(rule, max_orders=case.nav_order_budget),),
        (),
        (),
        ZERO,
        1000,
        1,
        240000,
        "no_transfers",
    )
    book = BookObservation(
        market.symbol,
        market.generation,
        ((case.close, max(case.owned, D("1"))),),
        ((case.close + case.price_tick, max(case.owned, D("1"))),),
        now,
        now,
        now * 1000000,
    )
    facts = Facts(
        MarketKey.of(state.scope),
        context,
        book,
        1,
        now,
        now * 1000000,
        "synthetic-module1-valuation",
        True,
        True,
        True,
    )
    conversions = (
        (
            Conversion(
                PoolKey(state.scope.venue, state.scope.account, "TOKEN"),
                "QUOTE",
                case.token_quote_rate,
                case.token_quote_rate,
                D("100000000"),
                now,
                now * 1000000,
                "synthetic-module1-token-conversion",
            ),
        )
        if case.fee_currency == "TOKEN"
        else ()
    )
    return (
        state,
        context,
        policy,
        ValuationFacts(
            1, now, now * 1000000, "synthetic-module1-portfolio", True, (facts,), conversions, ()
        ),
    )


@exact
def oracle(case: NavCase) -> dict[str, object]:
    """Keep original Python NAV and show every declared asset-rounding difference."""
    state, context, policy, facts = setup(case)
    nav = valuation(
        ((state, context),), (), facts, policy, context.now_ms, context.now_ms * 1000000
    )
    base_mark = next((mark for mark in nav.marks if mark.key.currency == "BASE"), None)
    raw_exact = case.cash + case.owned * case.close
    raw = case.cash + units(case.owned * case.close, QUOTE_QUANTUM, up=False)
    if case.fee_currency == "TOKEN":
        token_value = case.token_balance * case.token_quote_rate
        raw_exact += token_value
        raw += units(token_value, QUOTE_QUANTUM, up=False)
    net = sum(
        (
            mark.value
            if mark.key.currency == "QUOTE"
            else units(mark.value, QUOTE_QUANTUM, up=False)
            for mark in nav.marks
        ),
        ZERO,
    )
    quantity = ZERO
    if base_mark is not None and base_mark.amount > base_mark.residual:
        # The probe has no maximum amount/notional, hence at most one executable chunk.
        quantity = sell_quantity(case.owned, context.market, context.sell_fee, base_mark.price)
    expected = {
        "raw": raw,
        "net": net,
        "sell_quantity": quantity,
        "residual": base_mark.residual if base_mark else ZERO,
        "fee": base_mark.estimated_fee if base_mark else ZERO,
        "reason": "",
    }
    rule = policy.exits[0]
    slot = ExitSlot(state.scope, case.close, 0, None, None, "synthetic-trigger")
    after, commands, planned = plan_exit(
        state, context, rule, slot, context.now_ms, context.now_ms + 240000, frozenset()
    )
    return {
        "expected": expected,
        "python_nav": nav,
        "unrounded_raw": raw_exact,
        "raw_rounding_delta": raw_exact - raw,
        "net_rounding_delta": nav.net - net,
        "exit_proposal": {"state": after, "commands": commands, "slot": planned},
        "rounding": "Floor once per nonnegative asset subtotal to 1e-12; cash and fees exact.",
    }


def compare(executable: Path, selected: tuple[NavCase, ...] | None = None) -> dict[str, object]:
    program = executable.resolve(strict=True)
    if not program.is_file():
        raise DomainError("portfolio probe must be a regular executable file")
    roster = cases() if selected is None else selected
    if not 1 <= len(roster) <= 64 or len({case.name for case in roster}) != len(roster):
        raise DomainError("bounded unique portfolio fixture roster required")
    results: list[dict[str, object]] = []
    for case in roster:
        evidence = oracle(case)
        row: dict[str, object] = {
            "case": case.name,
            "arguments": case.arguments(),
            "oracle": evidence,
            "expected_sha256": digest(evidence["expected"]),
        }
        try:
            status, stdout, stderr = _invoke(program, case.arguments(), command="--nav-probe")
            row.update(
                exit_code=status,
                stderr=stderr.decode(errors="replace"),
                stdout_sha256=hashlib.sha256(stdout).hexdigest(),
            )
            if status != 0 or stderr:
                raise DomainError("C++ portfolio probe did not complete cleanly")
            compare_value(evidence["expected"], json.loads(stdout))
        except (ValueError, ArithmeticError, OSError) as exc:
            row.update(status="FAIL", difference=str(exc))
        else:
            row["status"] = "PASS"
        results.append(row)
    report: dict[str, object] = {
        "schema": SCHEMA,
        "executable": str(program),
        "cases": results,
        "passed": sum(row["status"] == "PASS" for row in results),
        "failed": sum(row["status"] != "PASS" for row in results),
        "scope": "Actual Python valuation plus declared research NAV rounding, min-only markets.",
        "limitations": "Exit proposals are Python evidence, not yet C++ lifecycle comparisons.",
    }
    report["result_sha256"] = digest(report)
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cpp", type=Path)
    arguments = parser.parse_args(argv)
    report = (
        compare(arguments.cpp)
        if arguments.cpp
        else {
            "schema": SCHEMA,
            "cases": tuple({"case": case.name, "oracle": oracle(case)} for case in cases()),
        }
    )
    sys.stdout.buffer.write(canonical(report) + b"\n")
    return int(bool(report.get("failed", 0)))


if __name__ == "__main__":
    raise SystemExit(main())
