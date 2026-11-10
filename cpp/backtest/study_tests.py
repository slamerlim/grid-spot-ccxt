"""Bounded synthetic end-to-end tests; never open the historical archives."""

from __future__ import annotations

import hashlib
import json
import struct
import subprocess
import sys
import tempfile
from decimal import ROUND_CEILING
from decimal import Decimal as D
from pathlib import Path
from typing import Any, cast


def configuration(sources: list[tuple[Path, str]], **changes: str) -> str:
    facts = dict(
        allow_new_exposure="1",
        amount_tick="0.001",
        base_quantum="0.001",
        book_ask="-",
        book_bid="-",
        book_generation="-",
        book_observed_ms="-",
        buy_amount_max="1000000",
        buy_amount_min="0.001",
        buy_cost_max="-",
        buy_cost_min="5",
        config_revision="1",
        cost_quantum="1e-12",
        fees_observed_ms="0",
        initial_quote_cash="1000",
        ladder_size="3",
        lower_price="0.01",
        market_generation="1",
        max_fill_records="1000",
        max_lots="100",
        max_open_orders="8",
        max_order_records="1000",
        max_quote_committed="900",
        max_replacements="1000",
        max_source_age_ms="60000",
        max_spread="0.01",
        net_target="0.01",
        now_ms="0",
        price_max="1000000",
        price_min="0.01",
        price_tick="0.01",
        quote_quantum="1e-12",
        quote_reserve="100",
        rules_observed_ms="0",
        seed_at="bid",
        sell_amount_max="1000000",
        sell_amount_min="0.001",
        sell_cost_max="-",
        sell_cost_min="5",
        spacing="0.01",
        token_quantum="1e-12",
        upper_price="1000000",
    )
    policy = dict(
        start_ms="-",
        end_ms="-",
        entry="seed_min_limit",
        exchange_tp_enabled="0",
        exchange_tp_threshold="0.10",
        exit_duration_ms="14400000",
        exit_ttl_ms="3600000",
        ladder="ladder_multi",
        latency_bars="1",
        mark_max_age_ms="0",
        max_cycles="10000",
        max_deviation="0.05",
        max_reprices="3",
        model="strict_skip_ambiguous",
        nav_order_budget="4",
        reentry_policy="reentry_near_fresh_book",
        require_source_tick_compatibility="0",
        symbol_tp_enabled="0",
        symbol_tp_threshold="0.10",
        synthetic_spread="0.001",
    )
    for key, value in changes.items():
        (facts if key in facts else policy)[key] = value
    rows = ["GRID_MODULE1_REPLAY\t1"]
    for index, (path, digest) in enumerate(sources):
        rows.append(
            f"SOURCE\tvenue{index}\tresearch\tASSET{index}/USDT\tallocation{index}\tgrid\tcycle\t{path}\t{digest}"
        )
    rows += [f"CONFIG\t{key}\t{value}" for key, value in sorted(facts.items())]
    rows += [f"FEE_RULE\t{side}\tQUOTE\t0.001\t0.00000001\t-\t64" for side in ("buy", "sell")]
    rows += [f"REPLAY_CONFIG\t{key}\t{value}" for key, value in sorted(policy.items())]
    return "\n".join(rows) + "\n"


def main() -> None:
    program = Path(sys.argv[1]).resolve(strict=True)
    checks = 0
    with tempfile.TemporaryDirectory(prefix="grid-cpp-study-") as directory:
        root = Path(directory)

        def source(
            name: str, bars: list[tuple[int, float, float, float, float]]
        ) -> tuple[Path, str]:
            data = b"".join(struct.pack("<qffff", *bar) for bar in bars)
            path = root / name
            path.write_bytes(data)
            return path, hashlib.sha256(data).hexdigest()

        def run(text: str, success: bool = True) -> dict[str, Any]:
            path = root / "input.tsv"
            path.write_text(text)
            completed = subprocess.run(
                [str(program), "replay", str(path)], capture_output=True, timeout=30, check=False
            )
            if success:
                assert completed.returncode == 0, completed.stderr.decode()
                assert not completed.stderr
                result = json.loads(completed.stdout)
                assert result["input_sha256"] == hashlib.sha256(text.encode()).hexdigest()
                assert all(item["complete"] for item in result["sources"])
                return cast(dict[str, Any], result)
            assert completed.returncode == 2, completed
            assert not completed.stdout, "invalid study must not publish a partial result"
            assert json.loads(completed.stderr)["error"]
            return {}

        flat = source("flat.bin", [(60000 * n, 100, 100, 100, 100) for n in range(1, 5)])
        text = configuration([flat])
        result = run(text)
        assert result == run(text), "repeated replay must be deterministic"
        assert result["processed_rows"] == 4 and result["portfolio"]["cash"] == "1000"
        assert result["portfolio"]["metrics"]["buy_fills"] == 0
        # Independently calculated funded counterfactual, including actual entry
        # rounding and forecast settlement reserve on the exit valuation.
        quantity, price = D("8.982"), D("100.1")
        entry_fee = (quantity * price * D(".001")).quantize(D("1e-8"), rounding=ROUND_CEILING)
        cash = D(1000) - quantity * price - entry_fee
        raw = cash + quantity * D(100)
        exit_fee = (quantity * D(100) * D(".001")).quantize(D("1e-8"), rounding=ROUND_CEILING) + D(
            ".00000063"
        )
        assert D(result["portfolio"]["buy_hold_raw"]["ending_nav"]) == raw
        assert D(result["portfolio"]["buy_hold_net"]["ending_nav"]) == raw - exit_fee
        assert len(result["nav_curve"]) == 2 and result["nav_curve"][-1]["timestamp_ms"] == 300000
        checks += 1

        rising = source(
            "rising.bin",
            [
                (60000, 100, 100, 100, 100),
                (120000, 100, 100, 99, 100),
                (180000, 3000, 3000, 97, 3000),
                (240000, 3000, 3000, 3000, 3000),
                (300000, 3000, 3001, 3000, 3000),
                (360000, 3000, 3000, 3000, 3000),
            ],
        )
        result = run(configuration([rising]))
        history = result["cycle_events"]
        kinds = [item["kind"] for item in history]
        assert "trigger" in kinds and "complete" in kinds and "restart" in kinds, kinds
        trigger = next(item for item in history if item["kind"] == "trigger")
        complete = next(item for item in history if item["kind"] == "complete")
        restart = next(item for item in history if item["kind"] == "restart")
        assert D(trigger["trigger_nav"]) >= D(trigger["trigger_baseline"]) * D("1.10")
        assert complete["completion_ms"] > trigger["trigger_ms"]
        assert (
            restart["timestamp_ms"] > complete["completion_ms"]
            and restart["completion_nav"] == complete["completion_nav"]
        )
        assert D(result["allocations"][0]["max_reserved_quote"]) >= D(
            result["allocations"][0]["reserved_quote"]
        )
        checks += 1

        one = source(
            "one.bin",
            [
                (60000, 100, 100, 100, 100),
                (120000, 100, 100, 99, 100),
                (180000, 100, 100, 100, 100),
            ],
        )
        two = source("two.bin", [(60000, 100, 100, 100, 100), (120000, 100, 100, 99, 100)])
        result = run(configuration([one, two]))
        assert result["processed_rows"] == 5 and result["complete_timestamp_groups"] == 2
        assert result["portfolio"]["raw"]["ending_nav"] is None, (
            "missing owned mark must remain unknown"
        )
        assert result["portfolio"]["raw"]["unknown_samples"] == 1
        assert all(D(item["owned_base"]) > 0 for item in result["allocations"])
        checks += 1

        gap = source(
            "gap.bin",
            [
                (60000, 100, 100, 100, 100),
                (120000, 100, 100, 99, 100),
                (180000, 99, 100, 98.5, 99),
                (660000, 50, 50, 50, 50),
            ],
        )
        result = run(configuration([gap], ladder="ladder_one", ladder_size="1"))
        assert result["sources"][0]["window_missing_minutes"] == 7
        assert result["portfolio"]["metrics"]["gap_refreshes"] == 1
        assert result["portfolio"]["metrics"]["buy_fills"] == 2
        allocation = result["allocations"][0]
        assert D(allocation["cash"]) + D(allocation["position_cost"]) == D(1000) + D(
            allocation["realized_pnl"]
        )
        checks += 1

        window = run(configuration([flat], start_ms="120000", end_ms="240000"))
        assert window["sources"][0]["records"] == 4 and window["processed_rows"] == 2
        assert window["source_rows"] == 4 and window["sources"][0]["bytes"] == 96
        terminal_gap = run(configuration([one], start_ms="60000", end_ms="360000"))
        assert (
            terminal_gap["declared_end_ms_exclusive"] == 360000
            and terminal_gap["end_ms_exclusive"] == 240000
        )
        assert terminal_gap["portfolio"]["raw"]["ending_nav"] is None
        assert (
            terminal_gap["terminal_assessment"]["raw_nav"] is None
            and not terminal_gap["terminal_assessment"]["observed_timestamp_group"]
        )
        assert (
            terminal_gap["calendar_minutes"] == 5
            and terminal_gap["unobserved_timestamp_minutes"] == 2
        )
        assert terminal_gap["sources"][0]["trailing_missing_minutes"] == 2
        run(configuration([flat], start_ms="600000", end_ms="660000"), False)
        run(configuration([(flat[0], "0" * 64)]), False)
        invalid = source("invalid.bin", [(60000, 100, 100, 100, 100), (120000, 100, 99, 101, 100)])
        run(configuration([invalid]), False)
        run(configuration([flat]).replace("FEE_RULE\tbuy\tQUOTE", "FEE_RULE\tbuy\tBASE"), False)
        run(configuration([flat]) + "REPLAY_CONFIG\tunknown_flag\t1\n", False)
        checks += 1
    print(f"{checks} integrated study scenarios passed")


if __name__ == "__main__":
    main()
