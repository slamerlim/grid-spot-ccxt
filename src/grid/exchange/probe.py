"""Explicit opt-in public probe. Doctor/tests never invoke it automatically."""

from __future__ import annotations

import argparse
import asyncio
import json
import platform
from collections.abc import Sequence
from dataclasses import asdict
from datetime import UTC, datetime

from grid.config import VENUE_IDS
from grid.exchange.adapter import ClientPool, ReadLimits, ReadOnlyClient
from grid.exchange.ccxt_transport import CCXT_VERSION, SYMBOL, PublicCCXT
from grid.exchange.models import ReadError


async def probe() -> dict[str, object]:
    limits = ReadLimits()
    transports: dict[str, PublicCCXT] = {}

    def factory(venue: str) -> ReadOnlyClient:
        transport = PublicCCXT(venue, limits)
        transports[venue] = transport
        return ReadOnlyClient(venue, transport, (SYMBOL,), limits)

    pool = ClientPool(factory)
    results: list[dict[str, object]] = []
    closed = False
    try:
        for venue in VENUE_IDS:
            result: dict[str, object] = {
                "venue": venue,
                "utc": datetime.now(UTC).isoformat(),
                "symbol": SYMBOL,
                "market_status": "NOT_TESTED",
                "book_status": "NOT_TESTED",
                "authenticated": "NOT_TESTED",
                "trading_ready": False,
            }
            client = pool.get(venue)
            transport = transports[venue]
            result["advertised"] = dict(transport.advertised)
            result["source_sha256"] = dict(transport.source_hashes())
            stage = "market_status"
            try:
                async with asyncio.timeout(40):
                    markets = await client.load_markets()
                    result["market"] = asdict(markets[SYMBOL])
                    result[stage] = "OBSERVED"
                    stage = "book_status"
                    book = await client.fetch_order_book(
                        SYMBOL, depth=25 if venue == "bitfinex" else 20
                    )
                    result["book"] = asdict(book)
                    result[stage] = "OBSERVED"
            except ReadError as exc:
                result[stage] = exc.failure.value
            except TimeoutError:
                result[stage] = "TIMEOUT"
            finally:
                result["requests"] = [asdict(entry) for entry in transport.evidence]
                result["catalog_rows_received"] = transport.catalog_rows
                results.append(result)
    finally:
        await pool.close()
        closed = True
    return {
        "schema_version": 1,
        "ccxt": CCXT_VERSION,
        "python": platform.python_version(),
        "limits": asdict(limits),
        "clients_closed": closed,
        "results": results,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Bounded, credential-free I3 public probe")
    parser.add_argument(
        "--public", action="store_true", required=True, help="authorize public GETs"
    )
    parser.parse_args(argv)
    print(json.dumps(asyncio.run(probe()), default=str, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
