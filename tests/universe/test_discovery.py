import asyncio
from dataclasses import replace

import pytest

from grid.config import VENUE_IDS
from grid.exchange.adapter import ClientPool, ReadLimits, ReadOnlyClient
from grid.exchange.models import Failure, ReadError
from grid.universe.discovery import Discovery, DiscoveryLimits


class Transport:
    precision_mode = 4
    source_timestamp = True

    def __init__(self, raw: dict[str, object], clock: list[int]) -> None:
        self.raw, self.clock = raw, clock
        self.loads = self.books = self.closes = 0
        self.entered, self.release = asyncio.Event(), asyncio.Event()
        self.release.set()

    async def load_markets(self, reload: bool) -> object:
        assert reload
        self.loads += 1
        self.entered.set()
        await self.release.wait()
        return {"BTC/USDT": self.raw}

    async def fetch_order_book(self, symbol: str, depth: int) -> object:
        self.books += 1
        return {
            "symbol": symbol,
            "bids": [["100", "10"]],
            "asks": [["101", "10"]],
            "timestamp": self.clock[0] // 1000000,
        }

    async def close(self) -> None:
        self.closes += 1


def setup(
    raw: dict[str, object], limits: DiscoveryLimits
) -> tuple[Discovery, dict[str, Transport], list[int]]:
    clock = [10000000000]
    created: dict[str, Transport] = {}

    def factory(venue: str) -> ReadOnlyClient:
        assert venue not in created
        row = {**raw, "precision": {"price": "0.01", "amount": 6}} if venue == "bitfinex" else raw
        created[venue] = Transport(row, clock)
        return ReadOnlyClient(
            venue,
            created[venue],
            ("BTC/USDT",),
            ReadLimits(),
            monotonic_ns=lambda: clock[0],
            wall_ms=lambda: clock[0] // 1000000,
        )

    return (
        Discovery(
            ClientPool(factory),
            limits,
            monotonic_ns=lambda: clock[0],
            wall_ms=lambda: clock[0] // 1000000,
        ),
        created,
        clock,
    )


LIMITS = DiscoveryLimits(2, 100, 1000, 32, 10000, 1, 1000)


@pytest.mark.usefixtures("async_sockets")
def test_100_callers_share_refresh_and_one_cancellation_does_not_cancel_read(
    raw_market: dict[str, object],
) -> None:
    async def scenario() -> None:
        cache, created, _ = setup(raw_market, LIMITS)
        cache.pool.get("okx")
        transport = created["okx"]
        transport.release.clear()
        tasks = [asyncio.create_task(cache.markets("okx", refresh=True)) for _ in range(100)]
        await transport.entered.wait()
        assert cache.active_requests == 1 and transport.loads == 1
        with pytest.raises(ReadError, match=Failure.BUSY):
            await cache.markets("okx", refresh=True)
        tasks[0].cancel()
        with pytest.raises(asyncio.CancelledError):
            await tasks[0]
        transport.release.set()
        results = await asyncio.gather(*tasks[1:])
        assert all(row["BTC/USDT"].generation == 1 for row in results)
        assert await cache.markets("okx") == results[0]
        assert transport.loads == 1 and cache.active_requests == 0
        await cache.close()
        assert transport.closes == 1

    asyncio.run(scenario())


@pytest.mark.usefixtures("async_sockets")
def test_inflight_invalidation_and_all_waiter_cancellation(raw_market: dict[str, object]) -> None:
    async def scenario() -> None:
        cache, created, clock = setup(raw_market, LIMITS)
        cache.pool.get("okx")
        transport = created["okx"]
        transport.release.clear()
        task = asyncio.create_task(cache.markets("okx"))
        await transport.entered.wait()
        cache.invalidate("okx")
        transport.release.set()
        with pytest.raises(ReadError, match=Failure.STALE):
            await task
        clock[0] += 1000000
        rows = await cache.markets("okx")
        assert rows["BTC/USDT"].generation == 2
        clock[0] += 1000000
        transport.release.clear()
        transport.entered.clear()
        task = asyncio.create_task(cache.markets("okx", refresh=True))
        await transport.entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        cache.invalidate("okx")
        transport.release.set()
        # Closure owns the abandoned task and retrieves its result or cancellation.
        await cache.close()
        assert cache.active_requests == 0 and transport.closes == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("venue_count", [1, 7])
@pytest.mark.usefixtures("async_sockets")
def test_one_or_seven_clients_bounded_lru_and_generational_expiry(
    raw_market: dict[str, object], venue_count: int
) -> None:
    async def scenario() -> None:
        cache, created, clock = setup(raw_market, LIMITS)
        venues = VENUE_IDS[:venue_count]
        for venue in venues:
            await cache.markets(venue)
            clock[0] += 1000000
            first = await cache.book(venue, "BTC/USDT")
            assert await cache.book(venue, "BTC/USDT") == first
        assert len(created) == venue_count
        assert cache.cache_entries == min(2, venue_count)
        venue = venues[-1]
        transport = created[venue]
        old = await cache.book(venue, "BTC/USDT")
        clock[0] += 1001000000
        with pytest.raises(ReadError, match=Failure.BUDGET):
            await cache.book(venue, "BTC/USDT")
        clock[0] += 1000000
        new = await cache.book(venue, "BTC/USDT")
        assert new.generation > old.generation and new.source_ms != old.source_ms
        assert transport.loads == 2 and transport.books == 2
        cache.invalidate(venue)
        assert cache.cache_entries <= 1
        await cache.close()
        assert all(client.closes == 1 for client in created.values())
        with pytest.raises(ReadError):
            await cache.markets(venue)

    asyncio.run(scenario())


@pytest.mark.usefixtures("async_sockets")
def test_request_budget_and_clock_regression_fail_closed(raw_market: dict[str, object]) -> None:
    async def scenario() -> None:
        cache, created, clock = setup(raw_market, replace(LIMITS, calls_per_window=1))
        await cache.markets("okx")
        clock[0] += 1000000
        with pytest.raises(ReadError, match=Failure.BUDGET):
            await cache.book("okx", "BTC/USDT")
        assert created["okx"].books == 0
        clock[0] = 0
        with pytest.raises(ReadError, match=Failure.POLICY):
            await cache.markets("okx")
        await cache.close()

    asyncio.run(scenario())
