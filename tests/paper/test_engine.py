import asyncio
from dataclasses import replace
from decimal import Decimal as D
from pathlib import Path

import pytest
from persistence.conftest import SOURCES

from grid.domain.model import Side
from grid.domain.numbers import DomainError
from grid.exchange.models import BookObservation, MarketObservation, ObservedLimits, map_market
from grid.paper.engine import PaperEngine, missing_rules
from grid.paper.journal import Journal
from grid.portfolio.model import CyclePhase


def market(ms: int, complete: bool = True) -> MarketObservation:
    result = map_market(
        {
            "id": "BTC-USDT",
            "symbol": "BTC/USDT",
            "base": "BTC",
            "quote": "USDT",
            "baseId": "BTC",
            "quoteId": "USDT",
            "spot": True,
            "type": "spot",
            "contract": False,
            "active": True,
            "precision": {"price": "0.01", "amount": "0.0001"},
            "limits": {
                "price": {"min": "0.01", "max": "1000000"},
                "amount": {"min": "0.0001", "max": "100"},
                "cost": {"min": "5", "max": "1000000"},
            },
        },
        "okx",
        4,
        1,
        ms * 1000000,
    )
    return result if complete else replace(result, costs=ObservedLimits(None, None))


def book(ms: int, bid: str = "100") -> BookObservation:
    return BookObservation(
        "BTC/USDT", 1, ((D(bid), D(100)),), ((D(bid) + D("0.01"), D(100)),), ms, ms, ms * 1000000
    )


@pytest.mark.usefixtures("async_sockets")
def test_unknown_limits_leave_cash_unallocated_and_backups_verify(tmp_path: Path) -> None:
    async def run() -> None:
        journal = Journal(tmp_path)
        now = [10000]
        engine = await PaperEngine.open(journal, SOURCES, lambda: (now[0], now[0] * 1000000))
        try:
            await engine.observe(market(10000, False), book(10000), 1)
            status = await engine.status()
            assert status["unallocated_synthetic_usdt"] == "1000"
            assert not status["real_account_verified"] and not status["paper_active"]
            assert not engine.gateway.sent and missing_rules(market(10000, False))
            await engine.backup(20)
            assert len(journal.kinds("backup")) == 1
        finally:
            await engine.close()
            journal.close()

    asyncio.run(run())


@pytest.mark.usefixtures("async_sockets")
def test_status_failure_still_releases_financial_writer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        journal = Journal(tmp_path)

        def now() -> tuple[int, int]:
            return (10000, 10**10)

        engine = await PaperEngine.open(journal, SOURCES, now)

        async def failed_status() -> dict[str, object]:
            raise OSError("injected status failure")

        monkeypatch.setattr(engine, "status", failed_status)
        with pytest.raises(OSError):
            await engine.close()
        assert engine.closed
        reopened = await PaperEngine.open(journal, SOURCES, now)
        await reopened.close()
        journal.close()

    asyncio.run(run())


@pytest.mark.usefixtures("async_sockets")
def test_subsequent_fills_fees_and_restart_preserve_exact_ownership(tmp_path: Path) -> None:
    async def run() -> None:
        journal = Journal(tmp_path)
        now = [10000]

        def clock() -> tuple[int, int]:
            return now[0], now[0] * 1000000

        engine = await PaperEngine.open(journal, SOURCES, clock)
        rules = market(now[0])
        try:
            for sequence in range(1, 4):
                await engine.observe(rules, book(now[0]), sequence)
                now[0] += 5000
            assert len(engine.gateway.sent) == 1
            assert not engine.gateway.trades  # Touching the bid is not a fill.
            await engine.observe(rules, book(now[0], "98"), 4)
            assert len(engine.gateway.trades) == 1
            trade = engine.gateway.trades[0]
            assert trade.side is Side.BUY and trade.price == D(100)
            assert trade.fees and trade.fees[0].amount == D("0.01")
            assert engine.owner is not None
            health = await engine.owner.status()
            assert health.allocations[0].base == D("0.05")
            assert health.allocations[0].quote_cash == D("994.99")
            with pytest.raises(DomainError, match="duplicate"):
                await engine.observe(rules, book(now[0], "98"), 5)
            await engine.backup(20)
        finally:
            await engine.close()
            journal.close()
        journal = Journal(tmp_path)
        engine = await PaperEngine.open(journal, SOURCES, clock)
        try:
            assert len(engine.gateway.trades) == 1
            assert engine.owner is not None
            assert (await engine.owner.status()).admission_halted
            now[0] += 5000
            await engine.observe(rules, book(now[0], "100"), 6)
            health = await engine.owner.status()
            assert health.allocations[0].base == D("0.05")
            assert health.allocations[0].quote_cash == D("994.99")
        finally:
            await engine.close()
            journal.close()

    asyncio.run(run())


@pytest.mark.usefixtures("async_sockets")
def test_exit_reference_refusal_stays_latched_after_price_recovery_and_restart(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        journal = Journal(tmp_path)
        now = [10000]

        def clock() -> tuple[int, int]:
            return now[0], now[0] * 1000000

        engine = await PaperEngine.open(journal, SOURCES, clock)
        rules = market(10000)
        try:
            for sequence in range(1, 4):
                await engine.observe(rules, book(now[0]), sequence)
                now[0] += 5000
            await engine.observe(rules, book(now[0], "98"), 4)
            # Synthetic external expiry leaves inventory owned. It is reconciled
            # before evaluating the large invented price move below.
            for external, order in engine.gateway.orders.items():
                if order.request.side is Side.SELL:
                    engine.gateway.settle_cancel(external)
            now[0] += 5000
            await engine.observe(rules, book(now[0], "2200"), 5)
            cycle = await engine.writer.submit(lambda ledger: ledger.portfolio())
            assert cycle is not None and cycle.phase is CyclePhase.REQUESTED
            now[0] += 5000
            with pytest.raises(DomainError, match="operator review"):
                await engine.observe(rules, book(now[0], "1000"), 6)
            sent = len(engine.gateway.sent)
            now[0] += 5000
            with pytest.raises(DomainError, match="latched"):
                await engine.observe(rules, book(now[0], "2200"), 7)
            assert len(engine.gateway.sent) == sent
            assert engine.owner is not None and (await engine.owner.status()).admission_halted
        finally:
            await engine.close()
            journal.close()
        journal = Journal(tmp_path)
        engine = await PaperEngine.open(journal, SOURCES, clock)
        try:
            with pytest.raises(DomainError, match="latched"):
                await engine.observe(rules, book(now[0], "2200"), 8)
            assert engine.fatal_reason == "exit-reference-moved"
            assert len(engine.gateway.sent) == sent
        finally:
            await engine.close()
            journal.close()

    asyncio.run(run())
