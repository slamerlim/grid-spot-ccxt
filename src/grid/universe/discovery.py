"""Single-flight bounded public observations; pool ownership stays venue-scoped."""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict, deque
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass

from grid.config import VENUE_IDS
from grid.domain.numbers import bounded_int
from grid.exchange.adapter import ClientPool
from grid.exchange.models import BookObservation, Failure, MarketObservation, ReadError


@dataclass(frozen=True)
class DiscoveryLimits:
    cache_size: int
    waiters: int
    freshness_ms: int
    calls_per_window: int
    window_ms: int
    min_interval_ms: int
    close_ms: int

    def __post_init__(self) -> None:
        for value, lower, upper in (
            (self.cache_size, 1, 1000),
            (self.waiters, 1, 1000),
            (self.freshness_ms, 1, 300000),
            (self.calls_per_window, 1, 32),
            (self.window_ms, 1, 300000),
            (self.min_interval_ms, 1, 60000),
            (self.close_ms, 1, 10000),
        ):
            bounded_int(value, lower, upper)


@dataclass
class Flight:
    key: str
    task: asyncio.Task[object]
    waiters: int = 0


class Discovery:
    def __init__(
        self,
        pool: ClientPool,
        limits: DiscoveryLimits,
        *,
        monotonic_ns: Callable[[], int] = time.monotonic_ns,
        wall_ms: Callable[[], int] = lambda: time.time_ns() // 1000000,
    ) -> None:
        self.pool, self.limits, self._now, self._wall = pool, limits, monotonic_ns, wall_ms
        self._epochs: dict[str, int] = {}
        self._markets: dict[str, Mapping[str, MarketObservation]] = {}
        self._books: OrderedDict[tuple[str, str], BookObservation] = OrderedDict()
        self._flights: dict[str, Flight] = {}
        self._calls: dict[str, deque[int]] = {}
        self._closed = False
        self._last_clock = 0

    @property
    def cache_entries(self) -> int:
        return len(self._books)

    @property
    def active_requests(self) -> int:
        return len(self._flights)

    def _check(self, venue: str) -> int:
        now = self._now()
        if self._closed or venue not in VENUE_IDS or now < self._last_clock:
            raise ReadError(Failure.POLICY)
        self._last_clock = now
        return now

    def invalidate(self, venue: str) -> None:
        self._check(venue)
        self._epochs[venue] = self._epochs.get(venue, 0) + 1
        self._markets.pop(venue, None)
        self._books = OrderedDict((k, v) for k, v in self._books.items() if k[0] != venue)

    async def _shared(
        self, venue: str, key: str, operation: Callable[[], Awaitable[object]]
    ) -> object:
        now = self._check(venue)
        flight = self._flights.get(venue)
        if flight is not None and flight.key != key:
            raise ReadError(Failure.BUSY)
        if flight is None:
            calls = self._calls.setdefault(venue, deque())
            while calls and now - calls[0] >= self.limits.window_ms * 1000000:
                calls.popleft()
            if len(calls) >= self.limits.calls_per_window or (
                calls and now - calls[-1] < self.limits.min_interval_ms * 1000000
            ):
                raise ReadError(Failure.BUDGET)
            calls.append(now)
            epoch = self._epochs.get(venue, 0)

            async def guarded() -> object:
                result = await operation()
                if self._closed or self._epochs.get(venue, 0) != epoch:
                    raise ReadError(Failure.STALE)
                return result

            task = asyncio.create_task(guarded())
            flight = Flight(key, task)
            self._flights[venue] = flight

            def finished(done: asyncio.Task[object]) -> None:
                if not done.cancelled():
                    done.exception()  # Retrieve a failure even if every waiter was canceled.
                if self._flights.get(venue) is flight:
                    del self._flights[venue]

            task.add_done_callback(finished)
        if flight.waiters >= self.limits.waiters:
            raise ReadError(Failure.BUSY)
        flight.waiters += 1
        try:
            return await asyncio.shield(flight.task)
        finally:
            flight.waiters -= 1

    async def markets(
        self, venue: str, *, refresh: bool = False
    ) -> Mapping[str, MarketObservation]:
        now = self._check(venue)
        flight = self._flights.get(venue)
        cached = self._markets.get(venue)
        if cached is not None and not refresh:
            try:
                for row in cached.values():
                    row.require_fresh(now, self.limits.freshness_ms * 1000000)
                return dict(cached)
            except ReadError:
                pass
        if flight is None or flight.key != "markets":
            self.invalidate(venue)
        epoch = self._epochs.get(venue, 0)

        async def read() -> object:
            return await self.pool.get(venue).load_markets(refresh=True)

        result = await self._shared(venue, "markets", read)
        if not isinstance(result, Mapping) or any(
            not isinstance(row, MarketObservation) for row in result.values()
        ):
            raise ReadError(Failure.MALFORMED)
        parsed: dict[str, MarketObservation] = dict(result)
        # Invalidation may occur after the flight completes but before this waiter resumes.
        if self._epochs.get(venue, 0) != epoch:
            raise ReadError(Failure.STALE)
        for row in parsed.values():
            row.require_fresh(self._now(), self.limits.freshness_ms * 1000000)
        self._markets[venue] = parsed
        return dict(parsed)

    async def book(self, venue: str, symbol: str) -> BookObservation:
        now = self._check(venue)
        key = venue, symbol
        cached = self._books.get(key)
        if cached is not None:
            try:
                market = self._markets.get(venue, {}).get(symbol)
                if market is None or cached.generation != market.generation:
                    raise ReadError(Failure.STALE)
                market.require_fresh(now, self.limits.freshness_ms * 1000000)
                cached.require_fresh(now, self._wall(), self.limits.freshness_ms)
                self._books.move_to_end(key)
                return cached
            except ReadError:
                del self._books[key]
        markets = await self.markets(venue)
        if symbol not in markets:
            raise ReadError(Failure.POLICY)
        epoch = self._epochs.get(venue, 0)

        async def read() -> object:
            return await self.pool.get(venue).fetch_order_book(symbol)

        result = await self._shared(venue, "book:" + symbol, read)
        if (
            not isinstance(result, BookObservation)
            or result.generation != markets[symbol].generation
        ):
            raise ReadError(Failure.STALE)
        if self._epochs.get(venue, 0) != epoch:
            raise ReadError(Failure.STALE)
        result.require_fresh(self._now(), self._wall(), self.limits.freshness_ms)
        self._books[key] = result
        self._books.move_to_end(key)
        while len(self._books) > self.limits.cache_size:
            self._books.popitem(last=False)
        return result

    async def close(self) -> None:
        self._closed = True
        tasks = [flight.task for flight in self._flights.values()]
        for task in tasks:
            task.cancel()
        try:
            async with asyncio.timeout(self.limits.close_ms / 1000):
                await asyncio.gather(*tasks, return_exceptions=True)
        finally:
            self._markets.clear()
            self._books.clear()
            await self.pool.close()
