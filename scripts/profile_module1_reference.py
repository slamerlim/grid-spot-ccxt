"""Bounded real-bar timing of the existing Python oracle, never a full study result.

All scenario inputs come from the frozen Module 1 experiment. The input window
is chosen by timestamp/count, not by observed returns. Originals are read-only.
"""

from __future__ import annotations

import argparse
import cProfile
import hashlib
import json
import os
import pstats
import resource
import shutil
import time
from datetime import UTC, datetime
from decimal import Decimal as D
from pathlib import Path
from typing import Any

from grid.domain.market import FeeRule, Limits, Market, Precision, PrecisionMode
from grid.domain.model import Context, Policy
from grid.research.artifacts import canonical
from grid.research.history import RECORD, decode
from grid.research.module1_reference import MAX_BARS, run_bars


def context(config: dict[str, Any], symbol: str, base: str) -> Context:
    rules, fees, grid, funding = (
        config[key] for key in ("market_rules", "fees", "grid", "funding")
    )
    quote = config["reporting_currency"]
    market = Market(
        symbol=symbol,
        base=base,
        quote=quote,
        generation=1,
        price=Precision(PrecisionMode.TICK, tick=D(rules["price_tick"])),
        amount=Precision(PrecisionMode.TICK, tick=D(rules["amount_tick"])),
        prices=Limits(D(rules["minimum_price"]), D(rules["maximum_price"])),
        buy_amount=Limits(D(rules["minimum_buy_amount"]), D(rules["maximum_amount"])),
        buy_cost=Limits(D(rules["minimum_buy_notional"]), None),
        sell_amount=Limits(D(rules["minimum_sell_amount"]), D(rules["maximum_amount"])),
        sell_cost=Limits(D(rules["minimum_sell_notional"]), None),
        spot=True,  # Explicit hypothetical scenario, never historical identity certification.
        leveraged=False,
    )
    policy = Policy(
        spacing=D(grid["spacing"]),
        net_target=D(grid["minimum_net_target_quote"]),
        quote_reserve=D(funding["quote_reserve_per_allocation"]),
        max_quote_committed=D(funding["maximum_committed_cost_per_allocation"]),
        lower_price=D(rules["minimum_price"]),
        upper_price=D(rules["maximum_price"]),
        cost_quantum=D(rules["cost_quantum"]),
        max_spread=D(config["execution"]["synthetic_spread"]),
        ladder_size=grid["multi_pending_buy_levels"],
        max_lots=grid["maximum_accumulated_lots"],
        max_open_orders=grid["maximum_open_orders"],
        max_order_records=10000,
        max_fill_records=10000,
        max_replacements=grid["maximum_sell_replacements_per_round"],
        max_source_age_ms=60000,
        seed_at="bid",
    )
    buy = FeeRule(
        fees["currency"],
        D(fees["buy_rate"]),
        D(fees["settlement_quantum"]),
        None,
        fees["forecast_max_settlements"],
    )
    sell = FeeRule(
        fees["currency"],
        D(fees["sell_rate"]),
        D(fees["settlement_quantum"]),
        None,
        fees["forecast_max_settlements"],
    )
    return Context(market, policy, buy, sell, 1, 0, 0, 0, None, True)


def file_identity(info: os.stat_result) -> tuple[int, ...]:
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--offset", type=int, required=True)
    parser.add_argument("--count", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not 2 <= args.count <= MAX_BARS or args.offset < 0:
        parser.error("bounded nonnegative window required")
    source = args.source.resolve(strict=True)
    output = args.output.resolve()
    if Path("/opt/datasets") == output or Path("/opt/datasets") in output.parents:
        parser.error("output must be outside the immutable archive")
    if output == source or source.parent == output or source.parent in output.parents:
        parser.error("output must be outside the source directory")
    output.mkdir(parents=True, exist_ok=False)
    frozen = args.config.read_bytes()
    config = json.loads(frozen)
    with (output / "experiment_config.json").open("xb") as stream:
        stream.write(frozen)
    shutil.copyfile(Path(__file__), output / "executed_profile.py")
    before = file_identity(source.stat())
    started = datetime.now(UTC).isoformat()
    wall, cpu = time.perf_counter(), time.process_time()
    with source.open("rb") as stream:
        stream.seek(args.offset * RECORD.size)
        raw = stream.read(args.count * RECORD.size)
        if file_identity(os.fstat(stream.fileno())) != before:
            raise ValueError("source changed during read")
    if len(raw) != args.count * RECORD.size or file_identity(source.stat()) != before:
        raise ValueError("incomplete or changed source window")
    bars = tuple(decode(raw[i : i + RECORD.size]) for i in range(0, len(raw), RECORD.size))
    market_context = context(config, args.symbol, args.symbol.split("/")[0])
    timings = []
    hashes = []
    result: dict[str, object] = {}
    for repeat in range(3):
        tick, process_tick = time.perf_counter(), time.process_time()
        result = run_bars(
            market_context,
            bars,
            quote_cash=D(config["funding"]["initial_quote_per_allocation"]),
            synthetic_spread=D(config["execution"]["synthetic_spread"]),
            latency_bars=config["execution"]["order_eligibility_delay_bars"],
            trace=False,
        )
        timings.append(
            {
                "repeat": repeat,
                "wall_seconds": time.perf_counter() - tick,
                "cpu_seconds": time.process_time() - process_tick,
            }
        )
        hashes.append(result["result_sha256"])
    if len(set(hashes)) != 1:
        raise ValueError("reference rerun mismatch")
    profile = cProfile.Profile()
    profile.enable()
    repeated = run_bars(
        market_context,
        bars,
        quote_cash=D(config["funding"]["initial_quote_per_allocation"]),
        synthetic_spread=D(config["execution"]["synthetic_spread"]),
        latency_bars=config["execution"]["order_eligibility_delay_bars"],
        trace=False,
    )
    profile.disable()
    if repeated["result_sha256"] != hashes[0]:
        raise ValueError("profiled reference result mismatch")
    if file_identity(source.stat()) != before:
        raise ValueError("source changed during experiment")
    with (output / "profile.txt").open("x") as stream:
        pstats.Stats(profile, stream=stream).sort_stats("cumulative").print_stats(30)
    encoded = canonical(result)
    with (output / "reference_result.json").open("xb") as stream:
        stream.write(encoded + b"\n")
    receipt = {
        "schema": "grid-module1-reference-timing-v1",
        "kind": "bounded_python_oracle_profile_NOT_full_history_or_portfolio_results",
        "start_utc": started,
        "end_utc": datetime.now(UTC).isoformat(),
        "source": str(source),
        "source_identity_before_after": before,
        "source_changed": False,
        "offset_records": args.offset,
        "records": args.count,
        "window_sha256": hashlib.sha256(raw).hexdigest(),
        "config_sha256": hashlib.sha256(frozen).hexdigest(),
        "timings": timings,
        "deterministic_result_sha256": hashes[0],
        "total_wall_seconds_including_profile": time.perf_counter() - wall,
        "total_cpu_seconds_including_profile": time.process_time() - cpu,
        "process_peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        "cache_state": "warm_no_eviction",
        "financial_output": "reference_result.json",
        "scope": "no global TP in this bounded per-allocation baseline oracle",
        "source_status": config["source_status"],
    }
    with (output / "receipt.json").open("x") as stream:
        json.dump(receipt, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps(receipt, sort_keys=True))


if __name__ == "__main__":
    main()
