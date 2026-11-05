"""Committed-history reuse must preserve full identity and cancellation validation."""

import asyncio
from dataclasses import replace
from decimal import Decimal as D
from pathlib import Path
from typing import Any

import pytest

from grid.domain.model import ActualFee, Context, Fill, OrderReport, Reconciled, Side, State
from grid.persistence.ledger import Ledger
from grid.persistence.records import Bounds

from .helpers import ZERO_FEES, harness


def test_duplicate_closed_history_needs_only_hold_and_final_reconciliation(
    tmp_path: Path,
    bounds: Bounds,
    state: State,
    context: Context,
    async_sockets: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        async with harness(tmp_path / "ledger.db", bounds, state, context) as h:
            await h.seed()
            snapshot = await h.writer.submit(lambda ledger: ledger.snapshot(h.scope))
            version = snapshot.version
            assert version is not None
            await h.writer.submit(lambda ledger: ledger.compact("compact", h.scope, version))
            assert not (await h.current()).fills  # Durable fill survives RAM-history compaction.
            count = await h.writer.submit(
                lambda ledger: ledger.db.execute("SELECT count(*) FROM requests").fetchone()[0]
            )
            events: list[object] = []
            original = Ledger.apply

            def track(self: Ledger, *args: Any, **kwargs: Any) -> Any:
                events.append(args[2])
                return original(self, *args, **kwargs)

            monkeypatch.setattr(Ledger, "apply", track)
            for _ in range(3):
                assert await h.service(), h.executor.last_error
            assert len(events) == 3 and all(isinstance(e, Reconciled) for e in events)
            assert not any(isinstance(e, (Fill, OrderReport)) for e in events)
            assert (
                await h.writer.submit(
                    lambda ledger: ledger.db.execute("SELECT count(*) FROM requests").fetchone()[0]
                )
                == count + 6
            )
            after = await h.current()
            assert (after.quote_cash, after.owned_base, after.position_cost) == (
                D(900),
                D(1),
                D(100),
            )
            assert (
                await h.writer.submit(
                    lambda ledger: ledger.db.execute("SELECT count(*) FROM fills").fetchone()[0]
                )
                == 1
            )

    asyncio.run(run())


@pytest.mark.parametrize("fault", ["price", "fee", "trade_key", "missing_key", "binding"])
def test_changed_committed_fact_blocks_entire_snapshot_before_new_fill_posting(
    tmp_path: Path,
    bounds: Bounds,
    state: State,
    context: Context,
    async_sockets: None,
    fault: str,
) -> None:
    async def run() -> None:
        async with harness(tmp_path / "ledger.db", bounds, state, context) as h:
            await h.seed()
            for _ in range(4):
                assert await h.send(), h.executor.last_error
                assert await h.service(), h.executor.last_error
            buy = next(
                f
                for f in h.venue.orders.values()
                if f.request.side is Side.BUY and f.status == "open"
            )
            new = h.venue.fill(buy.external_id, "new-lower", D(1), D(buy.request.price), ZERO_FEES)
            old = h.venue.trades[0]
            old = replace(old, sequence=3)  # New fill would sort before the conflicting old fact.
            if fault == "price":
                old = replace(old, price=D(99))
            elif fault == "fee":
                old = replace(old, fees=(ActualFee("QUOTE", D("-0.01"), None),))
            elif fault == "trade_key":
                await h.writer.submit(
                    lambda ledger: ledger.db.execute("UPDATE trade_keys SET external_id='wrong'")
                )
            elif fault == "missing_key":
                await h.writer.submit(lambda ledger: ledger.db.execute("DELETE FROM trade_keys"))
            else:
                await h.writer.submit(
                    lambda ledger: ledger.db.execute(
                        "UPDATE order_bindings SET client_id='wrong' WHERE order_id=1"
                    )
                )
            h.venue.trades = [new, old]
            assert not await h.service()
            assert not await h.send()
            after = await h.current()
            assert after.reconcile_reason is not None
            assert (after.quote_cash, after.owned_base) == (D(900), D(1))
            assert (
                await h.writer.submit(
                    lambda ledger: ledger.db.execute("SELECT count(*) FROM fills").fetchone()[0]
                )
                == 1
            )

    asyncio.run(run())


def test_open_partial_order_still_requires_current_report_and_new_trade(
    tmp_path: Path,
    bounds: Bounds,
    state: State,
    context: Context,
    async_sockets: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        async with harness(tmp_path / "ledger.db", bounds, state, context) as h:
            assert await h.service() and await h.send()
            h.venue.fill("order-1", "partial", D("0.5"), D(100), ZERO_FEES)
            assert await h.service()
            events: list[object] = []
            original = Ledger.apply

            def track(self: Ledger, *args: Any, **kwargs: Any) -> Any:
                events.append(args[2])
                return original(self, *args, **kwargs)

            monkeypatch.setattr(Ledger, "apply", track)
            assert await h.service()
            assert not any(isinstance(e, Fill) for e in events)
            assert any(isinstance(e, OrderReport) for e in events)
            h.venue.fill("order-1", "remaining", D("0.5"), D(100), ZERO_FEES)
            events.clear()
            assert await h.service()
            assert sum(isinstance(e, Fill) for e in events) == 1
            assert sum(isinstance(e, OrderReport) and e.order_id == 1 for e in events) == 1
            after = await h.current()
            assert (after.quote_cash, after.owned_base) == (D(900), D(1))

    asyncio.run(run())
