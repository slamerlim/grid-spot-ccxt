from __future__ import annotations

import asyncio

import pytest

from grid.exchange.adapter import ClientPool, ReadLimits, ReadOnlyClient
from grid.exchange.models import Failure, ReadError


class FakeTransport:
    precision_mode = 4
    source_timestamp = True

    def __init__(self, raw: dict[str, object]) -> None:
        self.raw = raw
        self.loads = 0
        self.books = 0
        self.closes = 0
        self.delay = False

    async def load_markets(self, reload: bool) -> object:
        self.loads += 1
        if self.delay:
            await asyncio.Event().wait()
        return {"BTC/USDT": self.raw}

    async def fetch_order_book(self, symbol: str, depth: int) -> object:
        self.books += 1
        return {"symbol": symbol, "bids": [["100", "1"]], "asks": [["101", "1"]], "timestamp": 1000}

    async def close(self) -> None:
        self.closes += 1


def test_reuse_cache_generation_and_stale_book_refusal(
    async_sockets: None, raw_market: dict[str, object]
) -> None:
    async def scenario() -> None:
        now = [100]
        fake = FakeTransport(raw_market)
        created = []

        def factory(venue: str) -> ReadOnlyClient:
            created.append(venue)
            return ReadOnlyClient(
                venue, fake, ("BTC/USDT",), ReadLimits(cache_age_ms=1), monotonic_ns=lambda: now[0]
            )

        pool = ClientPool(factory)
        client = pool.get("okx")
        assert pool.get("okx") is client and created == ["okx"]
        first = await client.load_markets()
        assert await client.load_markets() == first and fake.loads == 1
        now[0] += 1_000_001
        with pytest.raises(ReadError, match=Failure.STALE):
            await client.fetch_order_book("BTC/USDT")
        assert fake.books == 0
        second = await client.load_markets(refresh=True)
        assert second["BTC/USDT"].generation == first["BTC/USDT"].generation + 1
        await client.fetch_order_book("BTC/USDT")
        with pytest.raises(ReadError, match=Failure.POLICY):
            await client.fetch_order_book("OTHER/USDT")
        assert fake.books == 1
        assert not any(
            hasattr(client, method)
            for method in ("create_order", "cancel_order", "request", "fetch_balance")
        )
        await pool.close()
        await pool.close()
        assert fake.closes == 1
        with pytest.raises(ReadError, match=Failure.CLOSED):
            pool.get("okx")
        with pytest.raises(ReadError, match=Failure.CLOSED):
            await client.load_markets()

    asyncio.run(scenario())


def test_busy_timeout_cancellation_and_close_in_flight(
    async_sockets: None, raw_market: dict[str, object]
) -> None:
    async def scenario() -> None:
        fake = FakeTransport(raw_market)
        fake.delay = True
        client = ReadOnlyClient("okx", fake, ("BTC/USDT",), ReadLimits(timeout_ms=20))
        task = asyncio.create_task(client.load_markets())
        await asyncio.sleep(0)
        with pytest.raises(ReadError, match=Failure.BUSY):
            await client.load_markets()
        with pytest.raises(ReadError, match=Failure.TIMEOUT):
            await task
        task = asyncio.create_task(client.load_markets())
        await asyncio.sleep(0)
        await client.close()
        assert task.cancelled() and fake.closes == 1

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "limits", [{"requests": 0}, {"requests": 33}, {"timeout_ms": 30_001}, {"min_interval_ms": 0}]
)
def test_resource_limits_reject_unbounded_configuration(limits: dict[str, int]) -> None:
    with pytest.raises(ReadError):
        ReadLimits(
            requests=limits.get("requests", 2),
            timeout_ms=limits.get("timeout_ms", 15000),
            min_interval_ms=limits.get("min_interval_ms", 1000),
        )


def test_close_is_bounded_and_caller_cancellation_preserves_cleanup(
    async_sockets: None,
    raw_market: dict[str, object],
) -> None:
    async def scenario() -> None:
        started, release = asyncio.Event(), asyncio.Event()

        class ClosingTransport(FakeTransport):
            async def close(self) -> None:
                started.set()
                await release.wait()
                self.closes += 1

        fake = ClosingTransport(raw_market)
        client = ReadOnlyClient("okx", fake, ("BTC/USDT",), ReadLimits(close_ms=20))
        task = asyncio.create_task(client.close())
        await started.wait()
        task.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert fake.closes == 1
        await client.close()
        assert fake.closes == 1

        release.clear()
        another = ReadOnlyClient("okx", fake, ("BTC/USDT",), ReadLimits(close_ms=5))
        with pytest.raises(ReadError, match=Failure.TIMEOUT):
            await another.close()
        # A previous failed close is never silently reported as successful.
        with pytest.raises(ReadError, match=Failure.TIMEOUT):
            await another.close()

    asyncio.run(scenario())
