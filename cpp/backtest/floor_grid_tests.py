"""Independent Fraction arithmetic against the public native replay command."""

from __future__ import annotations

import hashlib
import json
import os
import struct
import subprocess
import sys
import tempfile
from fractions import Fraction as F
from pathlib import Path
from typing import Any, cast

from study_tests import configuration


def ceil_tick(value: F, tick: F) -> F:
    return -((-value) // tick) * tick


def main() -> None:
    binary = Path(sys.argv[1]).resolve(strict=True)
    checks = 0
    with tempfile.TemporaryDirectory(prefix="grid-floor-integration-") as directory:
        root = Path(directory)
        path = root / "generated.bin"

        def run(text: str, success: bool = True) -> dict[str, Any]:
            config = root / "input.tsv"
            config.write_text(text)
            child = subprocess.run(
                [str(binary), "replay", str(config)],
                capture_output=True,
                timeout=20,
                env={**os.environ, "GRID_BACKTEST_LEDGER_PATH": str(root / "fills.jsonl")},
                check=False,
            )
            assert (child.returncode == 0) == success, child.stderr.decode()
            if not success:
                assert not child.stdout
                return {}
            return cast(dict[str, Any], json.loads(child.stdout))

        def text(step: str, floor: str, entry: float = 100.0, **changes: str) -> str:
            path.write_bytes(struct.pack("<qffff", 1735689600000, entry, entry, entry, entry))
            value = configuration(
                [(path, hashlib.sha256(path.read_bytes()).hexdigest())],
                entry="no_seed_lower",
                ladder="ladder_infinite",
                spacing=step,
                max_lots="10000",
                max_open_orders="10001",
                max_order_records="20002",
                synthetic_spread="0",
                **changes,
            )
            return (
                value
                + "CONFIG\tlog_percent_grid\t1\n"
                + f"CONFIG\tbuy_floor_fraction\t{floor}\n"
                + "REPLAY_CONFIG\trestart_after_exit\t0\n"
            )

        for step, floor, entry in [
            (s, f, 100.0) for s in (".01", ".0125", ".02", ".05") for f in (".1", ".6")
        ] + [(".01", ".333", 100.003)]:
            result = run(text(step, floor, entry))
            # Python float holds a decoded float32 exactly; Fraction preserves it.
            decoded = struct.unpack("<f", struct.pack("<f", entry))[0]
            anchor = F(decoded)
            tick, amount_tick = F(".01"), F(".001")
            bottom = ceil_tick(anchor * (1 - F(floor)), tick)
            prices: list[F] = []
            raw = anchor
            while not prices or prices[-1] != bottom:
                raw *= 1 - F(step)
                prices.append(max(bottom, raw // tick * tick))
            reserve = F(0)
            for price in prices:
                amount = ceil_tick(F(5) / price, amount_tick)
                assert amount * price >= 5 and (amount - amount_tick) * price < 5
                reserve += (
                    amount * price
                    + ceil_tick(amount * price * F(".001"), F(".00000001"))
                    + F(".00000063")
                )
                checks += 1
            allocation = result["allocations"][0]
            assert allocation["open_orders"] == len(prices)
            assert allocation["metrics"]["placed_orders"] == len(prices)
            assert F(allocation["reserved_quote"]) == reserve
            assert F(allocation["cash"]) == 1000 and F(allocation["owned_base"]) == 0
            assert (
                F(allocation["entry_reference"]) == anchor
                and F(allocation["buy_floor_price"]) == bottom
            )
            assert result["funded_strategy"]["restart_after_exit"] is False
            checks += 6
        for changes in (
            {"initial_quote_cash": "110"},
            {"max_quote_committed": "10"},
            {"max_lots": "2"},
        ):
            original = text(".01", ".6")
            for key, value in changes.items():
                rows = original.splitlines()
                original = (
                    "\n".join(
                        f"CONFIG\t{key}\t{value}" if r.startswith(f"CONFIG\t{key}\t") else r
                        for r in rows
                    )
                    + "\n"
                )
            run(original, False)
            assert not (root / "fills.jsonl").read_bytes()
            checks += 2
        run(text(".01", ".600001"), False)
        run(
            text(".01", ".6").replace(
                "REPLAY_CONFIG\tsynthetic_spread\t0\n",  # pragma: allowlist secret
                "REPLAY_CONFIG\tsynthetic_spread\t.001\n",  # pragma: allowlist secret
            ),
            False,
        )
        checks += 2
    print(json.dumps({"checks": checks, "status": "PASS", "sources": "generated binary only"}))


if __name__ == "__main__":
    main()
