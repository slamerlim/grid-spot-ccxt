"""One bounded read-only client per venue/account in a controller-owned pool."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Protocol

from grid.config import VENUE_IDS
from grid.exchange.models import (
    BookObservation,
    Failure,
    MarketObservation,
    ReadError,
    integer,
    map_book,
    map_market,
    mapping,
)


@dataclass(frozen=True)
class ReadLimits:
    requests: int = 2
    timeout_ms: int = 15_000
    close_ms: int = 3_000
    response_bytes: int = 8 * 1024 * 1024
    catalog_rows: int = 20_000
    cache_age_ms: int = 60_000
    min_interval_ms: int = 1_000
    observed_hour: bool = False

    def __post_init__(self) -> None:
        if type(self.observed_hour) is not bool:
            raise ReadError(Failure.POLICY)
        for value, low, high in (
            (self.requests, 1, 750 if self.observed_hour else 32),
            (self.timeout_ms, 1, 30_000),
            (self.close_ms, 1, 5_000),
            (self.response_bytes, 256, 16 * 1024 * 1024),
            (self.catalog_rows, 1, 30_000),
            (self.cache_age_ms, 1, 3_600_000 if self.observed_hour else 300_000),
            (self.min_interval_ms, 1_000, 60_000),
        ):
            integer(value, low, high)
        if self.observed_hour and (
            self.timeout_ms > 5000 or self.response_bytes > 65536 or self.min_interval_ms < 5000
        ):
            raise ReadError(Failure.POLICY)


class ReadTransport(Protocol):
    precision_mode: int
    source_timestamp: bool

    async def load_markets(self, reload: bool) -> object: ...
    async def fetch_order_book(self, symbol: str, depth: int) -> object: ...
    async def close(self) -> None: ...


class ReadOnlyClient:
    """No generic dispatch, arbitrary params, credentials or write operations.

    Busy callers fail immediately rather than accumulating tasks/lock waiters.
    A controller can coalesce refresh demand upstream in I6. No retry loop here.
    """

    def __init__(
        self,
        venue: str,
        transport: ReadTransport,
        symbols: tuple[str, ...],
        limits: ReadLimits,
        *,
        monotonic_ns: Callable[[], int] = time.monotonic_ns,
        wall_ms: Callable[[], int] = lambda: time.time_ns() // 1_000_000,
    ) -> None:
        if (
            venue not in VENUE_IDS
            or not 1 <= len(symbols) <= 8
            or len(set(symbols)) != len(symbols)
        ):
            raise ReadError(Failure.POLICY)
        self.venue = venue
        self.symbols = symbols
        self._transport = transport
        self._limits = limits
        self._now = monotonic_ns
        self._wall = wall_ms
        self._generation = 0
        self._markets: dict[str, MarketObservation] = {}
        self._active: asyncio.Task[object] | None = None
        self._closed = False
        self._closing: asyncio.Task[None] | None = None

    def _enter(self) -> None:
        if self._closed:
            raise ReadError(Failure.CLOSED)
        if self._active is not None:
            raise ReadError(Failure.BUSY)
        self._active = asyncio.current_task()

    async def load_markets(self, *, refresh: bool = False) -> Mapping[str, MarketObservation]:
        self._enter()
        try:
            if self._markets and not refresh:
                for market in self._markets.values():
                    market.require_fresh(self._now(), self._limits.cache_age_ms * 1_000_000)
                return dict(self._markets)
            self._markets.clear()
            async with asyncio.timeout(self._limits.timeout_ms / 1000):
                # After any prior generation, an invalidated normalized cache
                # must never re-stamp the transport's older cached rules as new.
                rows = mapping(await self._transport.load_markets(refresh or self._generation > 0))
                if len(rows) > self._limits.catalog_rows:
                    raise ReadError(Failure.BUDGET)
                next_generation = self._generation + 1
                received = self._now()
                parsed = {
                    symbol: map_market(
                        rows[symbol],
                        self.venue,
                        self._transport.precision_mode,
                        next_generation,
                        received,
                    )
                    for symbol in self.symbols
                    if symbol in rows
                }
                if not parsed or any(key != row.symbol for key, row in parsed.items()):
                    raise ReadError(Failure.INCOMPLETE)
                self._markets = parsed
                self._generation = next_generation
                return dict(parsed)
        except TimeoutError:
            raise ReadError(Failure.TIMEOUT) from None
        finally:
            self._active = None

    async def fetch_order_book(self, symbol: str, *, depth: int = 20) -> BookObservation:
        self._enter()
        try:
            if symbol not in self._markets or symbol not in self.symbols:
                raise ReadError(Failure.POLICY)
            integer(depth, 1, 100)
            market = self._markets[symbol]
            market.require_fresh(self._now(), self._limits.cache_age_ms * 1_000_000)
            async with asyncio.timeout(self._limits.timeout_ms / 1000):
                raw = await self._transport.fetch_order_book(symbol, depth)
                # The request itself can outlive a market generation's TTL.
                market.require_fresh(self._now(), self._limits.cache_age_ms * 1_000_000)
                return map_book(
                    raw,
                    market,
                    depth,
                    self._wall(),
                    self._now(),
                    source_timestamp=self._transport.source_timestamp,
                )
        except TimeoutError:
            raise ReadError(Failure.TIMEOUT) from None
        finally:
            self._active = None

    async def _cleanup(self) -> None:
        try:
            try:
                async with asyncio.timeout(self._limits.close_ms / 1000):
                    if self._active is not None:
                        self._active.cancel()
                        await asyncio.gather(self._active, return_exceptions=True)
            finally:
                # Always attempt transport closure, even after cancellation of
                # an in-flight read expires. Two independently bounded phases.
                async with asyncio.timeout(self._limits.close_ms / 1000):
                    await self._transport.close()
        except TimeoutError:
            raise ReadError(Failure.TIMEOUT) from None

    async def close(self) -> None:
        if self._closing is None:
            self._closed = True
            self._markets.clear()
            self._closing = asyncio.create_task(self._cleanup())
        try:
            await asyncio.shield(self._closing)
        except asyncio.CancelledError:
            # An interrupted caller cannot discard its owned HTTP session.
            await asyncio.shield(self._closing)
            raise


class ClientPool:
    """Controller owns exactly one pool. Public clients use the scope 'public'."""

    def __init__(self, factory: Callable[[str], ReadOnlyClient]) -> None:
        self._factory = factory
        self._clients: dict[str, ReadOnlyClient] = {}
        self._closed = False

    def get(self, venue: str) -> ReadOnlyClient:
        if self._closed:
            raise ReadError(Failure.CLOSED)
        if venue not in VENUE_IDS:
            raise ReadError(Failure.POLICY)
        if venue not in self._clients:
            self._clients[venue] = self._factory(venue)
        return self._clients[venue]

    async def close(self) -> None:
        self._closed = True
        failures = await asyncio.gather(
            *(client.close() for client in self._clients.values()),
            return_exceptions=True,
        )
        if any(isinstance(result, BaseException) for result in failures):
            raise ReadError(Failure.INTERNAL)
