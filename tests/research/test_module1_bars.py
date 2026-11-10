"""Existing Simulator observations remain the C++ bar adapter's exact oracle."""

import sys
from dataclasses import replace
from decimal import Decimal as D
from pathlib import Path
from typing import cast

import pytest

import grid.research.module1_bars as driver
from grid.domain.model import State
from grid.domain.numbers import DomainError
from grid.research.artifacts import canonical, plain
from grid.research.replay import MODELS


def case(name: str, model: str = "strict_skip_ambiguous") -> driver.BarCase:
    return next(row for row in driver.cases() if row.name == f"{name}__{model}")


def state(row: dict[str, object]) -> State:
    return cast(State, cast(dict[str, object], row["after"])["state"])


def test_handwritten_round_trip_conserves_cash_and_cannot_fill_new_sell_on_same_bar() -> None:
    rows = driver.expected(case("round_trip"))
    assert state(rows[0]).quote_cash == 1000 and rows[0]["fill_count"] == 0
    assert state(rows[1]).quote_cash == 900 and state(rows[1]).owned_base == 1
    assert state(rows[-1]).quote_cash == 1010 and state(rows[-1]).owned_base == 0
    assert rows[-1]["fill_count"] == 2 and rows[-1]["sell_count"] == 1
    assert rows[-1]["gross_profit"] == 10 and rows[-1]["turnover"] == 210
    for model in MODELS:
        same_bar = driver.expected(case("same_bar_sell_forbidden", model))[-1]
        assert same_bar["fill_count"] == 1 and same_bar["sell_count"] == 0
        assert state(same_bar).owned_base == 1


def test_ambiguity_and_touch_conventions_are_observed_without_changing_simulator() -> None:
    expected = ((5, 1, 1), (8, 1, 2), (2, 1, 1))
    for model, (fills, sells, ambiguous) in zip(MODELS, expected, strict=True):
        rows = driver.expected(case("ambiguous_sharp_drop", model))
        assert (rows[-1]["fill_count"], rows[-1]["sell_count"]) == (fills, sells)
        assert rows[-1]["ambiguous_bars"] == ambiguous
    strict = driver.expected(case("touch_is_not_strict_cross"))[-1]
    assert strict["fill_count"] == 0
    assert (
        driver.expected(case("touch_is_not_strict_cross", "touch_buy_priority"))[-1]["fill_count"]
        == 2
    )


def test_cancel_latency_preserves_late_inventory_and_many_rounds_compact_history() -> None:
    late = driver.expected(case("cancel_fill_race"))[-1]
    assert late["fill_count"] == 3 and late["sell_count"] == 1
    assert state(late).quote_cash == D("919.1") and state(late).owned_base == 1
    rows = driver.expected(case("compacted_rounds"))
    assert rows[-1]["fill_count"] == 85 and rows[-1]["sell_count"] == 42
    assert state(rows[-1]).quote_cash == 1320 and state(rows[-1]).realized_pnl == 420
    assert max(len(state(row).orders) for row in rows) < 15
    assert max(len(state(row).fills) for row in rows) < 5


def test_handwritten_quote_base_and_token_fees_preserve_distinct_ownership() -> None:
    quote = driver.expected(case("fees_quote"))[-1]
    assert state(quote).quote_cash == D("1009.8998")
    assert quote["fees"] == {"QUOTE": D("0.2102")}
    base = driver.expected(case("fees_base"))[-1]
    assert state(base).quote_cash == D("1009.78964") and state(base).owned_base == D("0.001")
    assert base["fees"] == {"BASE": D("0.002")}
    token = driver.expected(case("fees_token"))[-1]
    assert state(token).quote_cash == D("1010.11") and state(token).owned_base == 0
    assert token["fees"] == {"TOKEN": D("0.1051")}
    assert state(token).realized_pnl == state(quote).realized_pnl == D("9.8998")


def test_export_preserves_exact_raw_bytes_and_shared_context_without_event_injection() -> None:
    selected = case("cancel_fill_race")
    text = driver.export(selected).decode()
    assert text.startswith("GRID_MODULE1_TRACE\t1\n")
    assert "BAR_CONFIG\tlatency_bars\t2\n" in text
    assert "BAR_CONFIG\tsynthetic_spread\t0.0001\n" in text
    assert "BAR_CONFIG\tmodel\tstrict_skip_ambiguous\n" in text  # pragma: allowlist secret
    assert "EVENT\t" not in text
    assert [line[4:] for line in text.splitlines() if line.startswith("BAR\t")] == [
        bar.raw_hex for bar in selected.bars
    ]
    assert driver.export(selected) == driver.export(selected)
    assert len(driver.cases()) == 27


@pytest.mark.parametrize("fault", ["short", "long", "gap", "raw"])
def test_oracle_rejects_unbounded_nonconsecutive_or_fabricated_bars(fault: str) -> None:
    selected = case("round_trip")
    bars = selected.bars
    if fault == "short":
        bars = bars[:1]
    elif fault == "long":
        bars *= 129
    elif fault == "gap":
        bars = (bars[0], *bars[2:])
    else:
        bars = (replace(bars[0], close=D("100.5")), *bars[1:])
    with pytest.raises(DomainError):
        driver.export(replace(selected, bars=bars))


@pytest.mark.parametrize("fault", [None, "cash", "counter", "missing_bar", "signal", "stderr"])
def test_harness_compares_every_bar_state_and_counter_and_reports_process_failures(
    monkeypatch: pytest.MonkeyPatch, fault: str | None
) -> None:
    selected = case("round_trip")

    def invoke(
        program: Path, source: Path, timeout: float = 30.0, *, command: str = "trace"
    ) -> tuple[int, bytes, bytes]:
        assert program.is_file() and timeout == 30 and command == "bars"
        assert source.read_bytes() == driver.export(selected)
        observations = cast(list[dict[str, object]], plain(driver.expected(selected)))
        if fault == "cash":
            cast(dict[str, object], cast(dict[str, object], observations[1]["after"])["state"])[
                "quote_cash"
            ] = "900.000000000001"
        elif fault == "counter":
            observations[1]["turnover"] = "99.99"
        elif fault == "missing_bar":
            observations.pop(1)
        payload = b"\n".join(canonical(row) for row in observations) + b"\n"
        return (-11 if fault == "signal" else 0), payload, b"error" if fault == "stderr" else b""

    monkeypatch.setattr(driver, "_invoke", invoke)
    report = driver.compare(Path(sys.executable), (selected,))
    assert report["passed"] == int(fault is None)
    assert report["failed"] == int(fault is not None)
    if fault:
        assert cast(list[dict[str, object]], report["cases"])[0]["difference"]
