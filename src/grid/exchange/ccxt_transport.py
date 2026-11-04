"""Pinned CCXT public REST boundary. All network traffic passes an exact allowlist.

Any is confined to CCXT's generated, dynamically typed interface. The application
receives object and validates it. No ambient configuration, keys, proxies, debug
logging, automatic retries, redirects or authenticated endpoints are accepted.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from email.utils import parsedate_to_datetime
from importlib import metadata
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import parse_qsl, urlsplit

import aiohttp
import ccxt.pro as ccxt  # type: ignore[import-untyped]

from grid.config import VENUE_IDS
from grid.exchange.adapter import ReadLimits
from grid.exchange.models import Failure, ReadError, money

CCXT_VERSION = "4.5.91"
SYMBOL = "BTC/USDT"


@dataclass(frozen=True)
class Endpoint:
    host: str
    path: str
    query: tuple[tuple[str, str], ...] = ()


# A deliberately small probe profile, not a configurable generic HTTP client.
# The book depth is 20 except Bitfinex's supported P0 aggregation of 25 levels.
ENDPOINTS: dict[str, tuple[Endpoint, Endpoint]] = {
    "okx": (
        Endpoint(
            "www.okx.com",
            "/api/v5/public/instruments",
            (("instType", "SPOT"), ("instId", "BTC-USDT")),
        ),
        Endpoint("www.okx.com", "/api/v5/market/books", (("instId", "BTC-USDT"), ("sz", "20"))),
    ),
    "htx": (
        Endpoint("api.huobi.pro", "/v1/common/symbols"),
        Endpoint(
            "api.huobi.pro",
            "/market/depth",
            (("symbol", "btcusdt"), ("type", "step0"), ("depth", "20")),
        ),
    ),
    "gate": (
        Endpoint("api.gateio.ws", "/api/v4/spot/currency_pairs/BTC_USDT"),
        Endpoint(
            "api.gateio.ws",
            "/api/v4/spot/order_book",
            (("currency_pair", "BTC_USDT"), ("limit", "20"), ("with_id", "true")),
        ),
    ),
    "xt": (
        Endpoint("sapi.xt.com", "/v4/public/symbol", (("symbol", "btc_usdt"),)),
        Endpoint("sapi.xt.com", "/v4/public/depth", (("symbol", "btc_usdt"), ("limit", "20"))),
    ),
    "bingx": (
        Endpoint(
            "open-api.bingx.com", "/openApi/spot/v1/common/symbols", (("symbol", "BTC-USDT"),)
        ),
        Endpoint(
            "open-api.bingx.com",
            "/openApi/spot/v1/market/depth",
            (("symbol", "BTC-USDT"), ("limit", "20")),
        ),
    ),
    "kucoin": (
        Endpoint("api.kucoin.com", "/api/v2/symbols"),
        Endpoint("api.kucoin.com", "/api/v1/market/orderbook/level2_20", (("symbol", "BTC-USDT"),)),
    ),
    "bitfinex": (
        Endpoint("api-pub.bitfinex.com", "/v2/conf/pub:info:pair"),
        Endpoint("api-pub.bitfinex.com", "/v2/book/tBTCUST/P0", (("len", "25"),)),
    ),
}


def check_endpoint(venue: str, url: str, method: str, headers: object, body: object) -> int:
    if venue not in ENDPOINTS or method != "GET" or body is not None or len(url) > 1024:
        raise ReadError(Failure.POLICY)
    if headers not in (None, {}, {"Content-Type": "application/json"}):
        raise ReadError(Failure.POLICY)
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.username or parsed.password or parsed.fragment:
        raise ReadError(Failure.POLICY)
    query = parse_qsl(parsed.query, keep_blank_values=True)
    if venue == "bingx":
        timestamps = [value for key, value in query if key == "timestamp"]
        if (
            len(timestamps) != 1
            or len(timestamps[0]) != 13
            or not timestamps[0].isascii()
            or not timestamps[0].isdigit()
        ):
            raise ReadError(Failure.POLICY)
        query = [(key, value) for key, value in query if key != "timestamp"]
    for index, endpoint in enumerate(ENDPOINTS[venue]):
        if (
            parsed.netloc == endpoint.host
            and parsed.path == endpoint.path
            and sorted(query) == sorted(endpoint.query)
        ):
            return index
    raise ReadError(Failure.POLICY)


@dataclass(frozen=True)
class WireReply:
    status: int
    body: bytes
    retry_after_ms: int | None = None


def retry_after(value: str | None, now: float) -> int | None:
    """Unknown/malformed Retry-After stops the observed runner, never shortens a wait."""
    if value is None:
        return None
    if len(value) > 128:
        raise ReadError(Failure.MALFORMED)
    try:
        seconds = (
            int(value)
            if value.isascii() and value.isdigit()
            else (parsedate_to_datetime(value).timestamp() - now)
        )
        if seconds < 0 or seconds > 86400:
            raise ValueError
        return max(60000, int(seconds * 1000) + 1000)
    except (ValueError, TypeError, OverflowError):
        raise ReadError(Failure.MALFORMED) from None


class PublicWire(Protocol):
    async def get(self, url: str, limits: ReadLimits) -> WireReply: ...
    async def close(self) -> None: ...


class HTTPWire:
    """One lazy HTTP session. Decode only after bounding the decompressed body."""

    def __init__(self) -> None:
        self._session: aiohttp.ClientSession | None = None

    async def get(self, url: str, limits: ReadLimits) -> WireReply:
        if self._session is None:
            self._session = aiohttp.ClientSession(
                connector=aiohttp.TCPConnector(limit=1, limit_per_host=1),
                trust_env=False,
                cookie_jar=aiohttp.DummyCookieJar(),
            )
        timeout = aiohttp.ClientTimeout(total=limits.timeout_ms / 1000)
        async with self._session.get(url, timeout=timeout, allow_redirects=False) as response:
            chunks = bytearray()
            async for chunk in response.content.iter_chunked(16 * 1024):
                if len(chunks) + len(chunk) > limits.response_bytes:
                    raise ReadError(Failure.BUDGET)
                chunks.extend(chunk)
            return WireReply(
                response.status,
                bytes(chunks),
                retry_after(response.headers.get("Retry-After"), time.time())
                if response.status in {418, 429}
                else None,
            )

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None


def classify(error: Exception) -> Failure:
    if isinstance(error, ReadError):
        return error.failure
    if isinstance(error, (TimeoutError, ccxt.RequestTimeout)):
        return Failure.TIMEOUT
    if isinstance(error, (ccxt.RateLimitExceeded, ccxt.DDoSProtection)):
        return Failure.RATE_LIMIT
    if isinstance(error, (ccxt.AuthenticationError, ccxt.PermissionDenied)):
        return Failure.AUTH
    if isinstance(error, ccxt.NotSupported):
        return Failure.UNSUPPORTED
    if isinstance(error, (aiohttp.ClientError, ccxt.NetworkError, ccxt.ExchangeNotAvailable)):
        return Failure.UNAVAILABLE
    if isinstance(error, (ValueError, TypeError, KeyError, IndexError, ccxt.BadResponse)):
        return Failure.MALFORMED
    if isinstance(error, ccxt.ExchangeError):
        return Failure.UNAVAILABLE
    return Failure.INTERNAL


def _reject_constant(value: str) -> None:
    raise ReadError(Failure.MALFORMED)


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ReadError(Failure.MALFORMED)
        result[key] = value
    return result


def decode_json(body: bytes, maximum: int) -> Any:
    if len(body) > maximum:
        raise ReadError(Failure.BUDGET)
    try:
        # JSON fractional numbers stay decimal strings; integers remain integers
        # for CCXT flags/timestamps. This avoids the JSON -> float -> Decimal loss.
        return json.loads(
            body,
            parse_float=str,
            parse_constant=_reject_constant,
            object_pairs_hook=_unique_object,
        )
    except (ValueError, UnicodeError, RecursionError):
        raise ReadError(Failure.MALFORMED) from None


@dataclass(frozen=True)
class RequestEvidence:
    endpoint: str
    parameter_names: tuple[str, ...]
    status: int | None
    elapsed_ms: int
    bytes: int
    outcome: str


class PublicCCXT:
    """Actual public implementation: no authenticated client can be configured."""

    def __init__(self, venue: str, limits: ReadLimits, *, wire: PublicWire | None = None) -> None:
        if (
            venue not in VENUE_IDS
            or metadata.version("ccxt") != CCXT_VERSION
            or limits.observed_hour
            and venue != "okx"
        ):
            raise ReadError(Failure.POLICY)
        self.venue = venue
        self._limits = limits
        self._wire = HTTPWire() if wire is None else wire
        self._closed = False
        self._halted = False
        self._requests = 0
        self._last_ns = 0
        self._fetching = False
        self._evidence: list[RequestEvidence] = []
        self.catalog_rows = 0
        self.source_timestamp = venue != "bitfinex"  # CCXT fabricates local timestamp there.
        options: dict[str, object] = {
            "defaultType": "spot",
            "adjustForTimeDifference": False,
            "maxRetriesOnFailure": 0,
            "fetchMarkets": {"types": ["spot"], "fetchTickersFees": False},
        }
        if venue == "htx":
            options["fetchMarkets"] = {"types": {"spot": True, "linear": False, "inverse": False}}
        if venue == "bitfinex":
            options["fetchOrderBook"] = {"precision": "P0"}
        self._client: Any = getattr(ccxt, venue)(
            {
                "enableRateLimit": True,
                "timeout": limits.timeout_ms,
                "timeout_on_exit": 0,
                "verbose": False,
                "enableLastHttpResponse": False,
                "enableLastJsonResponse": False,
                "enableLastResponseHeaders": False,
                "fetchHistoryCacheSize": 0,
                "options": options,
            }
        )
        self._client.number = Decimal
        original_string = self._client.safe_string

        def safe_string(container: Any, key: Any, default: Any = None) -> Any:
            # CCXT's default safe_string explicitly excludes Decimal. Supporting
            # it here preserves exact values through its market cache formatter.
            try:
                value = container[key]
            except (KeyError, IndexError, TypeError):
                return default
            return (
                str(value)
                if isinstance(value, Decimal)
                else original_string(container, key, default)
            )

        self._client.safe_string = safe_string

        def exact_bid_ask(
            bidask: Any,
            priceKey: Any = 0,
            amountKey: Any = 1,
            countOrIdKey: Any = 2,
        ) -> list[object]:
            # The pinned base parser hardcodes safe_float, ignoring .number.
            # Preserve the raw decimal before CCXT sorts/normalizes the book.
            level: list[object] = [
                money(bidask[priceKey], positive=True),
                money(bidask[amountKey], positive=True),
            ]
            count = self._client.safe_integer(bidask, countOrIdKey)
            if count is not None:
                level.append(count)
            return level

        self._client.parse_order_book_bid_ask = exact_bid_ask
        self._client.has["fetchCurrencies"] = False
        self._client.fetch = self._fetch
        if venue in {"xt", "bingx"}:
            # Their pinned fetch_markets unconditionally includes derivatives.
            self._client.fetch_markets = self._client.fetch_spot_markets
        if venue == "gate":

            async def no_margin(params: Any) -> list[object]:
                return []

            async def one_pair(params: Any) -> list[object]:
                return [
                    await self._client.publicSpotGetCurrencyPairsCurrencyPair(
                        {"currency_pair": "BTC_USDT"}
                    )
                ]

            self._client.publicMarginGetCurrencyPairs = no_margin
            self._client.publicSpotGetCurrencyPairs = one_pair
        self.precision_mode: int = self._client.precisionMode

    @property
    def evidence(self) -> tuple[RequestEvidence, ...]:
        return tuple(self._evidence)

    def recover_observed(self) -> None:
        """Explicit observer checkpoint; retains this client's request budget."""
        if not self._limits.observed_hour or self.venue != "okx" or self._closed or self._fetching:
            raise ReadError(Failure.POLICY)
        self._halted = False

    @property
    def advertised(self) -> Mapping[str, object]:
        return {
            key: self._client.has.get(key)
            for key in (
                "fetchMarkets",
                "fetchOrderBook",
                "fetchBalance",
                "fetchOpenOrders",
                "fetchMyTrades",
                "fetchTradingFee",
                "watchOrders",
                "watchMyTrades",
            )
        }

    def source_hashes(self) -> Mapping[str, str]:
        distribution = metadata.distribution("ccxt")
        names = (
            f"ccxt/pro/{self.venue}.py",
            f"ccxt/async_support/{self.venue}.py",
            "ccxt/base/exchange.py",
            "ccxt/async_support/base/exchange.py",
        )
        return {
            name: hashlib.sha256(Path(str(distribution.locate_file(name))).read_bytes()).hexdigest()
            for name in names
        }

    def _select_catalog(self, response: Any) -> Any:
        if self.venue == "gate":
            self.catalog_rows = 1
            return response
        key, raw_id = {
            "okx": ("instId", "BTC-USDT"),
            "htx": ("symbol", "btcusdt"),
            "xt": ("symbol", "btc_usdt"),
            "bingx": ("symbol", "BTC-USDT"),
            "kucoin": ("symbol", "BTC-USDT"),
            "bitfinex": ("", "BTCUST"),
        }[self.venue]
        if self.venue == "bitfinex":
            rows = response[0]
        elif self.venue == "xt":
            rows = response["result"]["symbols"]
        elif self.venue == "bingx":
            rows = response["data"]["symbols"]
        else:
            rows = response["data"]
        if not isinstance(rows, list) or len(rows) > self._limits.catalog_rows:
            raise ReadError(Failure.BUDGET)
        self.catalog_rows = len(rows)
        selected = [
            row for row in rows if (row[0] if self.venue == "bitfinex" else row.get(key)) == raw_id
        ]
        if len(selected) != 1:
            raise ReadError(Failure.INCOMPLETE)
        rows[:] = selected
        return response

    async def _fetch(
        self,
        url: str,
        method: str = "GET",
        headers: Any = None,
        body: Any = None,
    ) -> Any:
        endpoint_index = check_endpoint(self.venue, url, method, headers, body)
        if self._closed or self._halted:
            raise ReadError(Failure.CLOSED)
        if self._fetching:
            raise ReadError(Failure.BUSY)
        if self._requests >= self._limits.requests:
            raise ReadError(Failure.BUDGET)
        self._fetching = True
        self._requests += 1  # attempted requests consume budget, even on timeout
        started = time.monotonic_ns()
        status = None
        size = 0
        outcome = "OK"
        try:
            async with asyncio.timeout(self._limits.timeout_ms / 1000):
                delay = self._limits.min_interval_ms / 1000 - (started - self._last_ns) / 1e9
                if delay > 0:
                    await asyncio.sleep(delay)
                self._last_ns = time.monotonic_ns()
                reply = await self._wire.get(url, self._limits)
                status, size = reply.status, len(reply.body)
                if status in {418, 429}:
                    raise ReadError(Failure.RATE_LIMIT)
                if status == 401:
                    raise ReadError(Failure.AUTH)
                if status == 403:
                    raise ReadError(Failure.ACCESS)
                if status != 200:
                    raise ReadError(Failure.UNAVAILABLE)
                response = decode_json(reply.body, self._limits.response_bytes)
                self._client.handle_errors(status, "", url, method, {}, "", response, {}, None)
                return self._select_catalog(response) if endpoint_index == 0 else response
        except asyncio.CancelledError:
            self._halted = True
            outcome = "CANCELED"
            raise
        except Exception as exc:
            self._halted = True
            failure = classify(exc)
            outcome = failure.value
            raise ReadError(failure) from None
        finally:
            endpoint = ENDPOINTS[self.venue][endpoint_index]
            self._evidence.append(
                RequestEvidence(
                    endpoint.path,
                    tuple(sorted(key for key, _ in parse_qsl(urlsplit(url).query))),
                    status,
                    (time.monotonic_ns() - started) // 1_000_000,
                    size,
                    outcome,
                )
            )
            self._fetching = False

    async def load_markets(self, reload: bool) -> object:
        if self._closed or self._halted:
            raise ReadError(Failure.CLOSED)
        params = {
            "okx": {"instId": "BTC-USDT"},
            "xt": {"symbol": "btc_usdt"},
            "bingx": {"symbol": "BTC-USDT"},
            "bitfinex": {"config": "pub:info:pair"},
        }.get(self.venue, {})
        try:
            return await self._client.load_markets(reload, params)
        except Exception as exc:
            raise ReadError(classify(exc)) from None

    async def fetch_order_book(self, symbol: str, depth: int) -> object:
        if self._closed or self._halted:
            raise ReadError(Failure.CLOSED)
        if symbol != SYMBOL or depth != (25 if self.venue == "bitfinex" else 20):
            raise ReadError(Failure.POLICY)
        try:
            return await self._client.fetch_order_book(symbol, depth)
        except Exception as exc:
            raise ReadError(classify(exc)) from None

    async def close(self) -> None:
        self._closed = True
        try:
            await self._wire.close()
        finally:
            await self._client.close()
