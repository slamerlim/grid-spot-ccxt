"""Offline compatibility checks; optional frozen external oracle stays read-only."""

from __future__ import annotations

import hashlib
import json
import math
import os
import random
import struct
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

TRADE = struct.Struct("<q7dII")
EXCLUDED = {
    "elapsed_seconds",
    "candles_per_second",
    "research_mode",
    "engine_identity",
    "funding_model",
    "ohlc_model",
    "numeric_model",
    "reference_source_sha256",
}
REFERENCE_SHA = (
    "7405ec85f42811ac7cea6b1693592e3db574528142ccaecf0f600e8261dbe06f"  # pragma: allowlist secret
)


def main() -> None:
    program = Path(sys.argv[1]).resolve(strict=True)
    reference = Path(sys.argv[2]).resolve(strict=True) if len(sys.argv) > 2 else None
    if reference:
        assert hashlib.sha256(reference.read_bytes()).hexdigest() == REFERENCE_SHA
    checks = 0
    with tempfile.TemporaryDirectory(prefix="grid-comparison-") as directory:
        root = Path(directory)

        def case(
            rows: list[tuple[int, float, float, float, float]], **changes: Any
        ) -> dict[str, Any]:
            nonlocal checks
            checks += 1
            folder = root / str(checks)
            folder.mkdir()
            dataset = folder / "source.bin"
            dataset.write_bytes(b"".join(struct.pack("<qffff", *row) for row in rows))
            config: dict[str, Any] = {
                "dataset": str(dataset),
                "symbol": "BASE/USDT",
                "step_pct": 10.0,
                "take_profit_pct": 10.0,
                "order_quote": 90.0,
                "averaging": 0.0,
                "precision_mode": "tick_size",
                "price_precision": 0.01,
                "amount_precision": 1.0,
                "max_points": 3000,
            }
            config.update(changes)
            results = []
            runners = [(program, ["compare-reference"], "current")]
            if reference:
                runners.append((reference, [], "reference"))
            for executable, arguments, name in runners:
                output = folder / name
                config["output_dir"] = str(output)
                path = folder / f"{name}.json"
                path.write_text(json.dumps(config))
                progress = folder / "progress.json"
                child = subprocess.run(
                    [str(executable), *arguments, str(path)],
                    capture_output=True,
                    timeout=15,
                    check=False,
                    env={**os.environ, "GRID_BACKTEST_PROGRESS_PATH": str(progress)},
                )
                results.append((child, output))
            child, output = results[0]
            if reference:
                other, other_output = results[1]
                assert (child.returncode == 0) == (other.returncode == 0), (child, other)
                if child.returncode == 0:
                    left = {k: v for k, v in json.loads(child.stdout).items() if k not in EXCLUDED}
                    right = {k: v for k, v in json.loads(other.stdout).items() if k not in EXCLUDED}
                    assert left == right, (checks, left, right)
                    for filename in ["trades.bin", "series.bin"]:
                        assert (output / filename).read_bytes() == (
                            other_output / filename
                        ).read_bytes(), (checks, filename)
            if child.returncode:
                return {"error": child.stderr.decode()}
            assert not child.stderr
            summary = json.loads(child.stdout)
            assert summary["research_mode"] == "audited_grid_compatibility_v1"
            assert summary["funding_model"] == "unfunded_not_live"
            assert summary["engine_identity"] == "grid-current-comparison"
            assert json.loads((output / "summary.json").read_text()) == summary
            progress = json.loads((folder / "progress.json").read_text())
            assert progress["stage"] == "complete"
            assert progress["processed"] == progress["total"] == summary["candles"]
            return {
                **summary,
                "trade_records": list(TRADE.iter_unpack((output / "trades.bin").read_bytes())),
            }

        hand = case([(60000, 100, 100, 80, 100)])
        assert hand["buys"] == 2 and hand["sells"] == 1
        assert [trade[1] for trade in hand["trade_records"]] == [90.0, 81.0, 94.05]
        assert [trade[2] for trade in hand["trade_records"]] == [1.0, 1.0, 2.0]
        assert [trade[0] for trade in hand["trade_records"]] == [60002, 60002, 60003]
        assert math.isclose(hand["pnl"], 16.3818, abs_tol=1e-12)
        assert math.isclose(hand["fees"], 0.7182, abs_tol=1e-12)
        assert math.isclose(hand["max_cash_deficit"], 171.342, abs_tol=1e-12)
        negative = case([(60000, 100, 100, 90, 100)], take_profit_pct=0.1)
        assert negative["pnl"] < 0, "research must retain negative returns"
        unfunded = case([(60000, 100, 100, 1, 1)], take_profit_pct=100)
        assert unfunded["cash"] < -2000 and unfunded["buys"] > 30
        idle = case([(60000, 100, 100, 100, 100), (240000, 110, 110, 110, 110)])
        assert idle["trades"] == 0 and idle["missing_minutes"] == 2
        assert idle["next_buy_price"] == 99.0, "idle BUY must follow an increasing reference"
        sliced = case(
            [(60000 * n, 100, 100, 80, 100) for n in range(1, 4)], start_ts=120000, end_ts=180000
        )
        assert sliced["candles"] == 1 and sliced["first_ts"] == sliced["last_ts"] == 120000
        assert "validation failed" in case([(60000, 100, 100, 0, 100)], start_ts=120000)["error"]
        assert "validation failed" in case([(60000, 100, 100, 100, 100)] * 2)["error"]
        assert "rounds to zero" in case([(60000, 100, 100, 80, 100)], order_quote=0.001)["error"]
        # Current admission policy is deliberately stricter than the immutable oracle.
        for quote in ("BTC", "USDC", "USD"):
            config_path = root / ("reject-" + quote + ".json")
            config_path.write_text(json.dumps({"symbol": "BASE/" + quote, "dataset": "unopened"}))
            refused = subprocess.run(
                [str(program), "compare-reference", str(config_path)],
                capture_output=True,
                timeout=5,
                check=False,
            )
            assert refused.returncode != 0 and b"USDT-only" in refused.stderr
            checks += 1
        assert (
            "identity differs" in case([(60000, 100, 100, 80, 100)], dataset_identity={})["error"]
        )
        for mode in ["decimal_places", "significant_digits"]:
            large_lots = case(
                [(60000, 100, 100, 80, 100)],
                precision_mode=mode,
                price_precision=-1,
                amount_precision=-1,
                order_quote=10000,
            )
            assert large_lots["buys"] > 0
        months = case(
            [(1609459200000, 100, 120, 80, 100), (1612137600000, 100, 140, 90, 110)],
            chart_calendar="month",
            amount_precision=0.001,
        )
        assert months["chart_points"] == 2
        rng = random.Random(482013)
        for precision in [
            {"price_precision": 0.01, "amount_precision": 0.0001},
            {"precision_mode": "decimal_places", "price_precision": 2, "amount_precision": 4},
            {"precision_mode": "significant_digits", "price_precision": 5, "amount_precision": 5},
            {
                "price_precision_mode": "significant_digits",
                "price_precision": 5,
                "price_min_tick": 0.001,
                "price_max_tick": 0.1,
                "amount_precision": 0.001,
            },
        ]:
            for averaging in [0, 0.0001, 1, 10]:
                rows = []
                price = 100.0
                for index in range(96):
                    closed = price * rng.uniform(0.9, 1.1)
                    high = max(price, closed) * rng.uniform(1, 1.1)
                    low = min(price, closed) * rng.uniform(0.9, 1)
                    rows.append(((index + 1) * 60000, price, high, low, closed))
                    price = closed
                case(
                    rows,
                    **precision,
                    averaging=averaging,
                    order_quote=10.0,
                    step_pct=1.0,
                    take_profit_pct=1.0,
                    chart_interval_ms=300000,
                )
        for prohibited in ["/opt/module1_n", "/opt/datasets", "/datasets/crypto_1m"]:
            config = root / "guard.json"
            config.write_text(
                json.dumps(
                    {
                        "dataset": str(root / "1" / "source.bin"),
                        "output_dir": prohibited,
                        "symbol": "BASE/USDT",
                        "step_pct": 10,
                        "take_profit_pct": 10,
                        "order_quote": 90,
                        "averaging": 0,
                        "precision_mode": "tick_size",
                        "price_precision": 0.01,
                        "amount_precision": 1,
                    }
                )
            )
            child = subprocess.run(
                [str(program), "compare-reference", str(config)],
                capture_output=True,
                check=False,
                timeout=10,
            )
            assert child.returncode != 0 and b"cannot modify reference code" in child.stderr
    print(
        json.dumps(
            {"cases": checks, "frozen_reference_checked": reference is not None, "status": "PASS"}
        )
    )


if __name__ == "__main__":
    main()
