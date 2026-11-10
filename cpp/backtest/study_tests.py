"""Bounded synthetic end-to-end tests; never open the historical archives."""

from __future__ import annotations

import hashlib
import json
import resource
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
    refusals = 0
    max_stream_bytes = 0
    with tempfile.TemporaryDirectory(prefix="grid-cpp-study-") as directory:
        root = Path(directory)

        def source(
            name: str, bars: list[tuple[int, float, float, float, float]]
        ) -> tuple[Path, str]:
            data = b"".join(struct.pack("<qffff", *bar) for bar in bars)
            path = root / name
            path.write_bytes(data)
            return path, hashlib.sha256(data).hexdigest()

        def run(
            text: str, success: bool = True, expected_error: str | None = None
        ) -> dict[str, Any]:
            nonlocal refusals
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
            error = json.loads(completed.stderr)["error"]
            assert isinstance(error, str) and error
            if expected_error is not None:
                assert expected_error in error, (expected_error, error)
            refusals += 1
            return {}

        def refuse(arguments: list[str], expected_error: str, *, usage: bool = False) -> None:
            nonlocal refusals
            completed = subprocess.run(
                [str(program), *arguments], capture_output=True, timeout=30, check=False
            )
            assert completed.returncode == 2, completed
            assert not completed.stdout, "invalid admission must not publish output"
            error = completed.stderr.decode() if usage else json.loads(completed.stderr)["error"]
            assert isinstance(error, str) and expected_error in error, error
            refusals += 1

        def stream_file(
            mode: str, payload: str, expected_error: str | None, expected_rows: int = 0
        ) -> None:
            nonlocal refusals, max_stream_bytes
            path, output_path = root / "trace.tsv", root / "trace-output.jsonl"
            path.write_text(payload)

            def output_limit() -> None:
                resource.setrlimit(resource.RLIMIT_FSIZE, (16 * 1024 * 1024,) * 2)

            with output_path.open("wb") as output:
                completed = subprocess.run(
                    [str(program), mode, str(path)],
                    stdout=output,
                    stderr=subprocess.PIPE,
                    timeout=30,
                    check=False,
                    preexec_fn=output_limit,
                )
            max_stream_bytes = max(max_stream_bytes, output_path.stat().st_size)
            assert completed.returncode == (0 if expected_error is None else 2), completed
            if expected_error is None:
                assert not completed.stderr, completed.stderr
            else:
                error = json.loads(completed.stderr)["error"]
                assert isinstance(error, str) and expected_error in error, error
                refusals += 1
            # Trace/bar commands stream each completed observation before a later refusal.
            # Unlike replay, they do not promise all-or-nothing stdout publication.
            with output_path.open("rb") as rendered:
                row_count = 0
                for line in rendered:
                    assert isinstance(json.loads(line), dict)
                    row_count += 1
            assert row_count == expected_rows, (mode, expected_rows, row_count)

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

        # Admission mutations target specific guards, not merely any nonzero exit.
        first_source = text.splitlines()[1]
        buy_fee = "FEE_RULE\tbuy\tQUOTE\t0.001\t0.00000001\t-\t64"
        invalid_inputs = (
            (text.replace("GRID_MODULE1_REPLAY\t1", "GRID_MODULE1_REPLAY\t2"), "schema"),
            (text.replace(first_source + "\n", ""), "sources required"),
            (text.replace("CONFIG\tspacing\t0.01\n", ""), "missing trace config: spacing"),
            (text.replace(buy_fee + "\n", ""), "both fee rules required"),
            (
                text.replace("REPLAY_CONFIG\tmodel\tstrict_skip_ambiguous\n", ""),
                "missing replay policy",
            ),
            (text + "CONFIG\tspacing\t0.01\n", "duplicate replay config"),
            (text + "REPLAY_CONFIG\tmodel\tstrict_skip_ambiguous\n", "duplicate replay policy"),
            (text + buy_fee + "\n", "duplicate fee rule"),
            (text.replace("FEE_RULE\tbuy\t", "FEE_RULE\tother\t"), "unknown fee rule side"),
            (text + "UNKNOWN\trow\n", "unknown replay row type"),
            (text.replace("CONFIG\tspacing\t0.01", "CONFIG\tspacing"), "field count"),
            (text.replace("SOURCE\tvenue0\t", "SOURCE\t\t"), "identifier"),
            (text.replace("SOURCE\tvenue0\t", "SOURCE\tven\x01ue\t"), "printable ASCII"),
            (text.replace("SOURCE\tvenue0\t", "SOURCE\t" + "v" * 129 + "\t"), "identifier"),
            (text.replace("ASSET0/USDT", "ASSET0/BTC"), "common USDT"),
            (text + first_source + "\n", "duplicate study market or source"),
            (text.replace(buy_fee, buy_fee.replace("QUOTE", "TOKEN")), "fee-token conversion"),
            (text.replace(buy_fee, buy_fee.replace("0.001", "1")), "below one"),
            (text.replace(buy_fee, buy_fee.replace("\t64", "\t0")), "bounded trace integer"),
        )
        for invalid_text, reason in invalid_inputs:
            run(invalid_text, False, reason)
        invalid_fields: tuple[tuple[dict[str, str], str], ...] = (
            ({"initial_quote_cash": "-1"}, "negative initial cash"),
            ({"initial_quote_cash": "0.0000000000001"}, "not representable"),
            ({"initial_quote_cash": "10000000"}, "int64 currency overflow"),
            ({"quote_quantum": "0"}, "positive ledger quanta"),
            ({"spacing": "0"}, "positive trace policy"),
            ({"spacing": "1e97"}, "decimal exponent"),
            ({"spacing": "NaN"}, "decimal digit"),
            ({"spacing": "1" * 97}, "decimal digit bound"),
            ({"buy_amount_max": "0"}, "invalid market maximum"),
            ({"quote_reserve": "-1"}, "invalid trace risk range"),
            ({"lower_price": "1000000"}, "invalid trace risk range"),
            ({"allow_new_exposure": "2"}, "bounded trace integer"),
            ({"ladder_size": "257"}, "bounded trace integer"),
            ({"max_open_orders": "0"}, "bounded trace integer"),
            ({"max_order_records": "7"}, "bounded trace integer"),
            ({"seed_at": "close"}, "invalid seed reference"),
            ({"book_ask": "100"}, "partial missing book"),
            (
                {
                    "book_bid": "100",
                    "book_ask": "99",
                    "book_observed_ms": "0",
                    "book_generation": "1",
                },
                "invalid trace book",
            ),
            ({"start_ms": "60000"}, "invalid replay study interval"),
            ({"start_ms": "60001", "end_ms": "120000"}, "invalid replay study interval"),
            ({"start_ms": "120000", "end_ms": "60000"}, "invalid replay study interval"),
            ({"entry": "unknown"}, "unsupported entry"),
            ({"ladder": "unknown"}, "unsupported ladder"),
            ({"ladder": "ladder_one"}, "single ladder requires one"),
            ({"reentry_policy": "unknown"}, "unsupported reentry"),
            ({"model": "unknown"}, "unsupported OHLC"),
            ({"synthetic_spread": "0.02"}, "spread exceeds"),
            ({"latency_bars": "0"}, "bounded trace integer"),
            ({"max_cycles": "10001"}, "bounded trace integer"),
        )
        for changes, reason in invalid_fields:
            run(configuration([flat], **changes), False, reason)
        checks += 1

        # Parser bounds need at most 1 MiB of invented text and 1,536 source bytes.
        header = "GRID_MODULE1_REPLAY\t1\n"
        run(header + "x" * (1048576 - len(header)), False, "row bound")
        run(header + "x" * (1048577 - len(header)), False, "byte bound")
        row_prefix = "CONFIG\tpadding\t"
        run(header + row_prefix + "x" * (65536 - len(row_prefix)), False, "sources required")
        run(header + row_prefix + "x" * (65537 - len(row_prefix)), False, "row bound")
        rows = [f"CONFIG\tpadding{index}\t0\n" for index in range(513)]
        run(header + "".join(rows[:512]), False, "sources required")
        run(header + "".join(rows), False, "row bound")
        tiny_sources = [
            source(f"cap{index}.bin", [(60000, 100, 100, 100, 100)]) for index in range(64)
        ]
        maximum = run(configuration(tiny_sources))
        assert maximum["processed_rows"] == 64 and len(maximum["sources"]) == 64
        run(configuration([*tiny_sources, flat]), False, "source count exceeds")
        checks += 1

        # Whole-source verification also covers invalid records outside the window.
        run(configuration([invalid], start_ms="60000", end_ms="120000"), False, "source integrity")
        later = source("later.bin", [(600000, 100, 100, 100, 100)])
        run(
            configuration([flat, later], start_ms="60000", end_ms="120000"),
            False,
            "no observations",
        )
        empty = root / "empty.bin"
        empty.write_bytes(b"")
        partial = root / "partial.bin"
        partial.write_bytes(b"x")
        linked = root / "link.bin"
        linked.symlink_to(flat[0])
        for path in (empty, partial, root):
            refuse(["scan", str(path)], "nonempty regular file")
        for path in (root / "missing.bin", linked):
            refuse(["scan", str(path)], "cannot open read-only source")
        refuse(["scan", str(flat[0]), "A" * 64], "64 lowercase hexadecimal")
        refuse(["scan", str(flat[0]), flat[1], flat[1]], "duplicate expected digest")
        refuse(["replay", str(root / "absent.tsv")], "cannot open replay configuration")
        for arguments in ([], ["unknown"], ["replay"]):
            refuse(arguments, "grid_backtest scan", usage=True)
        refuse(["decode", "00"], "48 hexadecimal")
        refuse(["decode", "z" * 48], "invalid hexadecimal")
        refuse(["replay", str(root)], "replay configuration read failed")
        refuse(["math-grid", "1", "1e96", ".01", "10000"], "exact rational product bound exceeded")
        refuse(["math-grid", "1", "9", ".01", "1000"], "Decimal96 planner exponent unsupported")
        refuse(
            [
                "math-tp",
                "1",
                "1",
                ".01",
                "1",
                "1",
                "0",
                "1",
                "0",
                "QUOTE",
                ".999",
                "1",
                "-",
                "64",
                ".01",
                "1",
            ],
            "SELL price search bound exceeded",
        )
        refuse(["math-grid", "100", ".01", ".01", "-1"], "unsigned integer")
        refuse(["math-grid", "100", ".01", ".01", "10001"], "geometric level index")
        refuse(["math-grid", "100", ".01", ".01", *(["1"] * 257)], "too many grid levels")
        checks += 1

        # Portfolio constructors impose stricter bounds than the generic TSV integers.
        for changes, reason in (
            ({"initial_quote_cash": "0"}, "positive unique allocation funding"),
            ({"exchange_tp_threshold": "0"}, "invalid bounded portfolio policy"),
            ({"symbol_tp_threshold": "-0.1"}, "invalid bounded portfolio policy"),
            ({"max_deviation": "-0.1"}, "invalid bounded portfolio policy"),
            ({"max_deviation": "1"}, "invalid bounded portfolio policy"),
            ({"max_reprices": "1001"}, "invalid bounded portfolio policy"),
            ({"nav_order_budget": "1001"}, "invalid bounded portfolio policy"),
            ({"max_reprices": "-1"}, "bounded trace integer"),
            ({"nav_order_budget": "0"}, "bounded trace integer"),
            ({"mark_max_age_ms": "-1"}, "bounded trace integer"),
            ({"exit_ttl_ms": "0"}, "bounded trace integer"),
            ({"exit_duration_ms": "0"}, "bounded trace integer"),
            ({"exchange_tp_enabled": "2"}, "bounded trace integer"),
            ({"symbol_tp_enabled": "2"}, "bounded trace integer"),
        ):
            run(configuration([flat], **changes), False, reason)
        run(
            configuration(
                [flat],
                max_reprices="1000",
                nav_order_budget="1000",
                max_deviation="0",
                exit_ttl_ms="1",
                exit_duration_ms="1",
                max_cycles="1",
            )
        )
        checks += 1

        trace_header = "GRID_MODULE1_TRACE\t1\n"
        scope_row = "SCOPE\tv\ta\tASSET/USDT\tx\tg\tc\n"
        trace = (
            trace_header
            + scope_row
            + "".join(
                line + "\n"
                for line in text.splitlines()
                if line.startswith(("CONFIG\t", "FEE_RULE\t"))
            )
        )
        start = "EVENT\tStart\n"
        stream_file("trace", trace + start, None, 1)
        trace_invalid = (
            ("GRID_MODULE1_TRACE\t2\n", "unsupported trace schema"),
            (trace_header + "\n", "empty trace row"),
            (trace_header + "UNKNOWN\t1\n", "unknown trace row type"),
            (trace, "at least one event"),
            (trace + scope_row, "duplicate trace scope"),
            (trace + "CONFIG\tspacing\t0.01\n", "duplicate trace config"),
            (trace + "CONFIG\textra\t1\n" + start, "unknown trace config"),
            (trace + buy_fee + "\n", "duplicate fee rule"),
            (trace + "TOKEN\tX\t1\nTOKEN\tX\t1\n", "duplicate token funds"),
            (trace + "TOKEN\tX\t-1\n" + start, "negative fee token funds"),
            (trace + "TOKEN\tX\t0.0000000000001\n" + start, "not representable"),
            (trace.replace(scope_row, "") + start, "scope and both fee rules"),
            (trace.replace(scope_row, "SCOPE\tv\n"), "field count"),
            (trace + "TOKEN\tX\n", "field count"),
            (trace.replace("FEE_RULE\tbuy\t", "FEE_RULE\tother\t"), "fee rule side"),
            (trace.replace("FEE_RULE\tbuy\tQUOTE", "FEE_RULE\tbuy\t"), "identifier"),
        )
        for payload, reason in trace_invalid:
            stream_file("trace", payload, reason)
        for event_row, reason in (
            ("EVENT", "missing trace event type"),
            ("EVENT\tOther", "unsupported trace event"),
            ("EVENT\tStart\textra", "field count"),
            ("EVENT\tFill\tt", "short fill trace"),
            ("EVENT\tFill\tt\t0\t1\t100\t0", "bounded trace integer"),
            ("EVENT\tFill\tt\t1\t0\t100\t0", "positive fill values"),
            ("EVENT\tFill\tt\t1\t1\t-1\t0", "positive fill values"),
            ("EVENT\tFill\tt\t1\t1\t100\t17", "bounded trace integer"),
            ("EVENT\tFill\tt\t1\t1\t100\t1", "field count"),
            ("EVENT\tFill\tt\t1\t1\t100\t1\tX\t1\t-1", "fee conversion sign"),
            ("EVENT\tOrderReport\t1\tunknown\t0\t1", "unsupported order status"),
            ("EVENT\tOrderReport\t1\topen\t-1\t1", "negative reported cumulative"),
            ("EVENT\tOrderReport\t1\topen\t0\t2", "bounded trace integer"),
            ("EVENT\tCancelAcknowledged\t0", "bounded trace integer"),
            ("EVENT\tSubmissionUnknown\t1\textra", "field count"),
            ("EVENT\tControl\t1\tunknown", "unsupported control mode"),
            ("EVENT\tControl\t0\tpaused", "bounded trace integer"),
            ("EVENT\tReconciled\t1", "field count"),
        ):
            stream_file("trace", trace + event_row + "\n", reason)
        stream_file("trace", trace + start + "CONFIG\tspacing\t0.01\n", "config after events", 1)
        refuse(["trace", str(root / "missing-trace.tsv")], "cannot open trace file")
        refuse(["trace", str(root)], "trace input read failed")
        checks += 1

        bar_models = (
            "BAR_CONFIG\tmodel\tstrict_skip_ambiguous\n"
            "BAR_CONFIG\tlatency_bars\t1\nBAR_CONFIG\tsynthetic_spread\t0.001\n"
        )
        bars_prefix = trace.replace(
            "CONFIG\tallow_new_exposure\t1", "CONFIG\tallow_new_exposure\t0"
        )
        bars_prefix += bar_models
        raw_bar = struct.pack("<qffff", 60000, 100, 100, 100, 100).hex()
        bar_row = f"BAR\t{raw_bar}\n"
        stream_file("bars", bars_prefix + bar_row, None, 1)
        for payload, reason in (
            (trace + bar_row, "all bar model fields required"),
            (trace + "BAR_CONFIG\tmodel\n", "field count"),
            (trace + "BAR_CONFIG\tother\t1\n", "unknown bar config"),
            (bars_prefix + "BAR_CONFIG\tmodel\tstrict_skip_ambiguous\n", "duplicate bar config"),
            (bars_prefix.replace("strict_skip_ambiguous", "other") + bar_row, "unsupported OHLC"),
            (
                bars_prefix.replace("BAR_CONFIG\tlatency_bars\t1", "BAR_CONFIG\tlatency_bars\t0"),
                "bounded trace integer",
            ),
            (bars_prefix + "BAR\n", "field count"),
            (bars_prefix + "BAR\t00\n", "48 hexadecimal"),
            (bars_prefix + "BAR\t" + "z" * 48 + "\n", "invalid raw bar hexadecimal"),
        ):
            stream_file("bars", payload, reason)
        incompatible = struct.pack("<qffff", 60000, 100.001, 100.001, 100.001, 100.001).hex()
        stream_file("bars", bars_prefix + f"BAR\t{incompatible}\n", "incompatible")
        stream_file("bars", bars_prefix + bar_row * 2, "chronology must strictly advance", 1)
        stream_file("bars", bars_prefix + bar_row + bar_models, "late or duplicate bar config", 1)
        checks += 1

        # These protocol caps use small empty-state records, with output limited to 16 MiB.
        stream_file("trace", trace_header + "x" * (1048576 - len(trace_header)), "input bound")
        stream_file("trace", trace_header + "x" * (1048577 - len(trace_header)), "byte bound")
        stream_file(
            "trace",
            trace_header + row_prefix + "x" * (65536 - len(row_prefix)),
            "at least one event",
        )
        stream_file(
            "trace", trace_header + row_prefix + "x" * (65537 - len(row_prefix)), "input bound"
        )
        trace_rows = [f"CONFIG\tpadding{index}\t0\n" for index in range(20001)]
        stream_file("trace", trace_header + "".join(trace_rows[:20000]), "at least one event")
        stream_file("trace", trace_header + "".join(trace_rows), "input bound")
        stream_file("trace", trace + start * 10001, "trace event bound exceeded", 10000)
        raw_rows = "".join(
            "BAR\t" + struct.pack("<qffff", index * 60000, 100, 100, 100, 100).hex() + "\n"
            for index in range(1, 10002)
        )
        stream_file("bars", bars_prefix + raw_rows, "bar input bound exceeded", 10000)
        checks += 1

        # Small buffered responses must report final-flush errors, not exit successfully.
        full_device = Path("/dev/full")
        assert full_device.is_char_device(), "Linux /dev/full is required for output-failure checks"
        trace_path, bars_path, replay_path = (
            root / "output-trace.tsv",
            root / "output-bars.tsv",
            root / "output-replay.tsv",
        )
        trace_path.write_text(trace + start)
        bars_path.write_text(bars_prefix + bar_row)
        replay_path.write_text(configuration([flat]))
        for arguments, expected_errors in (
            (["math-grid", "100", ".01", ".01", "1"], ("output write failed",)),
            (["decode", raw_bar], ("output write failed",)),
            (["scan", str(flat[0]), flat[1]], ("output write failed",)),
            (["trace", str(trace_path)], ("output write failed", "trace output failed")),
            (["bars", str(bars_path)], ("output write failed", "bar output failed")),
            (["replay", str(replay_path)], ("output write failed", "study output failed")),
        ):
            output_path = root / "output-control.json"
            with output_path.open("wb") as output:
                completed = subprocess.run(
                    [str(program), *arguments],
                    stdout=output,
                    stderr=subprocess.PIPE,
                    timeout=10,
                    check=False,
                )
            assert completed.returncode == 0 and not completed.stderr, completed
            observed = json.loads(output_path.read_bytes())
            assert isinstance(observed, dict)
            if arguments[0] == "math-grid":
                assert observed == {"prices": ["99"]}
            with full_device.open("wb", buffering=0) as full_output:
                completed = subprocess.run(
                    [str(program), *arguments],
                    stdout=full_output,
                    stderr=subprocess.PIPE,
                    timeout=10,
                    check=False,
                )
            assert completed.returncode == 2, (arguments, completed)
            error = json.loads(completed.stderr)
            assert set(error) == {"error"} and error["error"] in expected_errors, error
            refusals += 1
        checks += 1
    print(
        f"{checks} integrated study scenarios passed; {refusals} controlled refusals verified; "
        f"maximum streamed output {max_stream_bytes} bytes"
    )


if __name__ == "__main__":
    main()
