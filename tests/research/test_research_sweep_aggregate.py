"""Independent exact examples for venue/portfolio exits and the bounded matrix."""

from __future__ import annotations

import hashlib
import json
import struct
from collections import Counter
from fractions import Fraction as F
from pathlib import Path
from typing import Any

import pytest

from scripts.research_sweep_reference import (
    EXPANDED_CONFIGURATION_COUNT,
    AggregateCycle,
    Position,
    aggregate_threshold,
    expanded_configuration,
    expanded_matrix,
    fixture_sources,
    matrix,
    reference,
    trade_digest,
)

START = 1_704_067_200_000
RULES = {
    "price_mode": "tick",
    "price_tick": "0.01",
    "amount_tick": "1",
    "min_amount": "1",
    "min_cost": "0",
}


def source(
    directory: Path, venue: str, symbol: str, bars: list[tuple[int, float]]
) -> dict[str, Any]:
    """Flat source bars keep each hand calculation independent of OHLC ambiguity."""
    path = directory / f"{venue}-{symbol}.bin"
    payload = b"".join(
        struct.pack("<qffff", START + minute * 60_000, *(price,) * 4) for minute, price in bars
    )
    path.write_bytes(payload)
    return {
        "id": f"{venue}:{symbol}/USDT",
        "venue": venue,
        "symbol": f"{symbol}/USDT",
        "path": str(path),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "records": len(bars),
        "first_ms": START + bars[0][0] * 60_000,
        "last_ms": START + bars[-1][0] * 60_000,
        "rules": dict(RULES),
    }


def config(**changes: Any) -> dict[str, Any]:
    return {
        "id": "aggregate",
        "step_pct": "10",
        "tp_pct": "15",
        "exchange_tp_pct": None,
        "global_tp_pct": None,
        **changes,
    }


def replay(sources: list[dict[str, Any]], **changes: Any) -> dict[str, Any]:
    result: dict[str, Any] = reference({"sources": sources, "configurations": [config(**changes)]})[
        "aggregate"
    ]
    return result


def test_expanded_matrix_streams_every_axis_and_keeps_original_ids() -> None:
    stream = expanded_matrix()
    assert iter(stream) is stream
    enabled: Counter[tuple[bool, bool]] = Counter()
    off = iter(matrix())
    for index, candidate in enumerate(stream):
        assert candidate == expanded_configuration(index)
        axes = (candidate["exchange_tp_pct"] is not None, candidate["global_tp_pct"] is not None)
        enabled[axes] += 1
        if not any(axes):
            assert {key: candidate[key] for key in ("id", "step_pct", "tp_pct")} == next(off)
    assert index + 1 == EXPANDED_CONFIGURATION_COUNT == 423_360
    assert enabled == {
        (False, False): 540,
        (True, False): 14_580,
        (False, True): 14_580,
        (True, True): 393_660,
    }
    assert expanded_configuration(0)["id"] == "s01-t04"
    assert expanded_configuration(1)["id"] == "s01-t04-e00-g04"
    assert expanded_configuration(28)["id"] == "s01-t04-e04-g00"
    assert expanded_configuration(423_359)["id"] == "s20-t30-e30-g30"
    assert next(off, None) is None


@pytest.mark.parametrize("index", [-1, 423_360])
def test_matrix_address_rejects_outside_range(index: int) -> None:
    with pytest.raises(IndexError):
        expanded_configuration(index)


@pytest.mark.parametrize("value", ["0", "1.5", "2.1", "15.5"])
def test_unknown_aggregate_axis_is_not_treated_as_disabled(value: str) -> None:
    with pytest.raises(ValueError):
        aggregate_threshold(value)


def test_exact_threshold_includes_both_commissions_and_equality() -> None:
    state = Position(RULES, F(1, 10), F(15, 100))
    state.point(F(100), START)
    cycle = AggregateCycle()
    cycle.observe(state.spent, START)
    # At this exact price the sale nets 110.20; purchase cost + fee were 100.20.
    state.mark = F(55_100, 499)
    assert cycle.net_after_exit([state]) == 10
    assert cycle.reached(F(1, 10), [state])
    state.mark -= F(1, 499_000)
    assert not cycle.reached(F(1, 10), [state])
    state.mark += F(2, 499_000)
    assert cycle.reached(F(1, 10), [state])


def test_zero_peak_never_fires_even_with_positive_lifetime_profit() -> None:
    state = Position(RULES, F(1, 10), F(15, 100))
    state.realized = F(100)
    assert not AggregateCycle().reached(F(2, 100), [state])


def test_gap_free_oracle_does_not_trigger_global_tp_using_ended_history(
    tmp_path: Path,
) -> None:
    sources = [
        source(tmp_path, "okx", "A", [(0, 100), (1, 110)]),
        source(tmp_path, "htx", "B", [(0, 100)]),
    ]
    plan = {
        "sources": sources,
        "configurations": [config(global_tp_pct="2")],
        "source_selection": {"history_policy": "gap_free_own_span"},
    }
    actual = reference(plan)["aggregate"]
    assert actual["aggregate_cycles"]["global"]["completed_count"] == 0
    assert actual["total"]["sell_count"] == 0
    assert actual["total_pnl"] == F("9.6")  # 210 marked - 200 spent - 0.4 BUY fees.
    assert all(state.qty == 1 for state in actual["pairs"].values())
    del plan["source_selection"]
    legacy = reference(plan)["aggregate"]
    assert legacy["aggregate_cycles"]["global"]["completed_count"] == 1


def test_gap_free_synthetic_fixture_is_explicit_and_preserves_legacy_default(
    tmp_path: Path,
) -> None:
    previous = fixture_sources(tmp_path / "legacy", minutes=48, exact_records=True)
    continuous = fixture_sources(
        tmp_path / "continuous", minutes=48, exact_records=True, gap_free=True
    )
    assert [row["id"] for row in previous] == [row["id"] for row in continuous]
    assert previous[0]["sha256"] == continuous[0]["sha256"]
    for row in continuous:
        timestamps = [
            item[0] for item in struct.iter_unpack("<qffff", Path(row["path"]).read_bytes())
        ]
        assert len(timestamps) == 48
        assert all(
            right - left == 60_000
            for left, right in zip(timestamps[:-1], timestamps[1:], strict=True)
        )
    assert previous[1]["last_ms"] > continuous[1]["last_ms"]


def test_peak_spent_denominator_does_not_shrink_with_current_positions() -> None:
    state = Position(RULES, F(1, 10), F(15, 100))
    state.spent, state.qty, state.mark = F(600), F(1), F(600)
    state.realized = F("111.2")
    cycle = AggregateCycle()
    cycle.observe(F(1000), START)
    cycle.observe(F(600), START + 1000)
    assert cycle.net_after_exit([state]) == 110
    assert not cycle.reached(F(15, 100), [state])
    assert cycle.reached(F(11, 100), [state])
    assert cycle.peak_spent_ms == START


def test_global_exit_has_priority_and_fees_are_charged_once(tmp_path: Path) -> None:
    fixture = source(tmp_path, "okx", "A", [(0, 100), (1, 104)])
    result = replay([fixture], exchange_tp_pct="2", global_tp_pct="2")
    state = result["pairs"][fixture["id"]]
    sells = [trade for trade in state.trades if trade["side"] == "SELL"]
    assert len(sells) == 1
    assert sells[0]["exit_scope"] == "global"
    assert state.buys == 2
    assert state.realized == 4
    assert state.fees == F("0.616")  # initial buy .20 + sell .208 + new buy .208
    assert state.pnl == F("3.384")
    assert state.trades[-1]["timestamp_ms"] == START + 61_000
    cycles = result["aggregate_cycles"]
    assert cycles["global"]["completed_count"] == 1
    assert cycles["global"]["baseline_fees"] == F("0.408")
    assert cycles["global"]["peak_spent"] == 104
    assert cycles["venues"][0]["completed_count"] == 1


def test_exact_float32_threshold_fires_but_preceding_price_does_not(tmp_path: Path) -> None:
    bits = struct.unpack("<I", struct.pack("<f", 511))[0]
    below = struct.unpack("<f", struct.pack("<I", bits - 1))[0]
    equal_source = source(tmp_path, "okx", "EQUAL", [(0, 499), (1, 511)])
    below_source = source(tmp_path, "okx", "BELOW", [(0, 499), (1, below)])
    equal = replay([equal_source], global_tp_pct="2")
    below_result = replay([below_source], global_tp_pct="2")
    # 511 - 499 - 0.998 - 1.022 = 9.98, exactly 2% of the 499 peak.
    assert equal["aggregate_cycles"]["global"]["completed_count"] == 1
    assert below_result["aggregate_cycles"]["global"]["completed_count"] == 0


def test_local_sale_precedes_global_signal_and_flat_pairs_are_reseeded(tmp_path: Path) -> None:
    fixture = source(tmp_path, "okx", "A", [(0, 100), (1, 116)])
    result = replay([fixture], tp_pct="10", exchange_tp_pct="2", global_tp_pct="2")
    state = result["pairs"][fixture["id"]]
    sells = [trade for trade in state.trades if trade["side"] == "SELL"]
    assert len(sells) == 1
    assert sells[0]["price"] == 110
    assert "exit_scope" not in sells[0]
    assert state.trades[-1]["price"] == 116
    assert state.trades[-1]["timestamp_ms"] == START + 61_000
    assert state.fees == F("0.652")
    assert result["aggregate_cycles"]["global"]["completed_count"] == 1


def test_global_exit_closes_an_ended_source_without_refreshing_its_price(tmp_path: Path) -> None:
    stale = source(tmp_path, "gate", "OLD", [(0, 100)])
    current = source(tmp_path, "okx", "NEW", [(0, 100), (10, 110)])
    result = replay([stale, current], global_tp_pct="4")
    old_state = result["pairs"][stale["id"]]
    new_state = result["pairs"][current["id"]]
    sale = old_state.trades[-1]
    assert sale["side"] == "SELL" and sale["price"] == 100
    assert sale["timestamp_ms"] == START + 600_000
    assert sale["quote_timestamp_ms"] == START + 3000
    assert old_state.last_ms == START + 3000
    assert old_state.longest == 600_000
    assert old_state.qty == 0 and old_state.buys == 1
    assert not old_state.started
    assert old_state.fees == F("0.4")
    assert new_state.buys == 2 and new_state.trades[-1]["timestamp_ms"] == START + 601_000
    assert result["total"]["fees"] == F("1.04")


def test_venue_exit_resets_only_its_cycle_and_keeps_global_profit(tmp_path: Path) -> None:
    rising = source(tmp_path, "gate", "A", [(0, 100), (1, 104)])
    flat = source(tmp_path, "okx", "B", [(0, 100), (1, 100)])
    result = replay([rising, flat], exchange_tp_pct="2", global_tp_pct="15")
    left, right = result["pairs"][rising["id"]], result["pairs"][flat["id"]]
    assert [trade["exit_scope"] for trade in left.trades if trade["side"] == "SELL"] == ["venue"]
    assert right.buys == 1 and right.sells == 0
    cycles = result["aggregate_cycles"]
    assert cycles["global"]["completed_count"] == 0
    assert cycles["global"]["baseline_realized_gross"] == 0
    assert cycles["global"]["baseline_fees"] == 0
    venues = {cycle["venue"]: cycle for cycle in cycles["venues"]}
    assert venues["gate"]["completed_count"] == 1
    assert venues["gate"]["baseline_realized_gross"] == 4
    assert venues["okx"]["completed_count"] == 0
    assert result["total_pnl"] == F("3.184")


def test_realized_venue_profit_later_contributes_to_global_exit(tmp_path: Path) -> None:
    rising = source(tmp_path, "gate", "A", [(0, 100), (1, 104), (2, 108)])
    flat = source(tmp_path, "okx", "B", [(0, 100), (1, 100), (2, 100)])
    result = replay([rising, flat], exchange_tp_pct="2", global_tp_pct="2")
    left, right = result["pairs"][rising["id"]], result["pairs"][flat["id"]]
    assert [trade["exit_scope"] for trade in left.trades if trade["side"] == "SELL"] == [
        "venue",
        "global",
    ]
    assert [trade["exit_scope"] for trade in right.trades if trade["side"] == "SELL"] == ["global"]
    cycles = result["aggregate_cycles"]
    assert cycles["global"]["completed_count"] == 1
    assert cycles["global"]["baseline_realized_gross"] == 8
    venues = {cycle["venue"]: cycle for cycle in cycles["venues"]}
    assert venues["gate"]["completed_count"] == 2
    assert venues["okx"]["completed_count"] == 1


def test_global_reset_does_not_count_an_unstarted_venue_cycle(tmp_path: Path) -> None:
    early = source(tmp_path, "gate", "EARLY", [(0, 100), (1, 104)])
    future = source(tmp_path, "okx", "FUTURE", [(10, 100)])
    result = replay([early, future], global_tp_pct="2")
    cycles = {cycle["venue"]: cycle for cycle in result["aggregate_cycles"]["venues"]}
    assert cycles["gate"]["completed_count"] == 1
    assert cycles["okx"]["completed_count"] == 0
    assert cycles["okx"]["last_exit_ms"] == START + 60_000
    assert cycles["okx"]["peak_spent"] == 100
    future_state = result["pairs"][future["id"]]
    assert future_state.buys == 1 and future_state.trades[0]["timestamp_ms"] == START + 600_000


def test_disabled_aggregate_axes_preserve_legacy_positions_and_trades(tmp_path: Path) -> None:
    fixture = source(tmp_path, "okx", "A", [(0, 100), (1, 90), (2, 112), (3, 80)])
    original = {"id": "same", "step_pct": "1", "tp_pct": "10"}
    legacy = reference({"sources": [fixture], "configurations": [original]})["same"]
    extended = reference(
        {
            "sources": [fixture],
            "configurations": [dict(original, exchange_tp_pct=None, global_tp_pct=None)],
        }
    )["same"]
    for key in ("peak_spent", "max_drawdown", "total_pnl", "fees"):
        assert extended[key] == legacy[key]
    assert extended["pairs"] == legacy["pairs"]


def test_trade_hash_binds_stale_quote_and_exit_scope() -> None:
    trade = {
        "timestamp_ms": START + 60_000,
        "quote_timestamp_ms": START + 3000,
        "exit_scope": "global",
        "side": "SELL",
        "price": F(100),
        "quantity": F(1),
        "cost": F(100),
        "fee": F(1, 5),
    }
    wanted_payload = {
        "config_id": "c",
        "source_id": "okx:A/USDT",
        "sequence": 1,
        **{
            key: f"{value.numerator}/{value.denominator}" if isinstance(value, F) else value
            for key, value in trade.items()
        },
    }
    expected = hashlib.sha256(
        bytes(32) + json.dumps(wanted_payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    assert trade_digest("c", "okx:A/USDT", [trade]) == expected
    assert trade_digest("c", "okx:A/USDT", [dict(trade, quote_timestamp_ms=START)]) != expected
    assert trade_digest("c", "okx:A/USDT", [dict(trade, exit_scope="venue")]) != expected
