"""Persistent one-hour budget in front of the existing exact GET allowlist."""

from __future__ import annotations

import hashlib
from collections.abc import Callable

from grid.exchange.adapter import ReadLimits
from grid.exchange.ccxt_transport import PublicWire, WireReply, check_endpoint
from grid.exchange.models import Failure, ReadError
from grid.paper.journal import Journal, clock


class ObservedWire:
    def __init__(
        self,
        journal: Journal,
        wire: PublicWire,
        now: Callable[[], tuple[int, int]] = clock,
    ) -> None:
        self.journal, self.wire, self.now = journal, wire, now
        self.busy = False

    async def get(self, url: str, limits: ReadLimits) -> WireReply:
        endpoint = check_endpoint("okx", url, "GET", None, None)
        ms, ns = self.now()
        starts = self.journal.kinds("start")
        attempts = self.journal.kinds("attempt")
        holds = self.journal.kinds("hold")
        if len(starts) != 1 or self.journal.kinds("finished"):
            raise ReadError(Failure.POLICY)
        start = starts[0]
        if (
            ns < start["ns"]
            or ms < start["ms"]
            or abs((ms - start["ms"]) - (ns - start["ns"]) // 1000000) > 2000
            or ns - start["ns"] >= 3600 * 10**9
            or ms - start["ms"] >= 3600000
        ):
            raise ReadError(Failure.BUDGET)
        if self.busy:
            raise ReadError(Failure.BUSY)
        if (
            len(attempts) >= 750
            or endpoint == 0
            and sum(r["payload"]["endpoint"] == 0 for r in attempts) >= 3
        ):
            raise ReadError(Failure.BUDGET)
        if (
            attempts
            and ns - attempts[-1]["ns"] < 5 * 10**9
            or any(ns < row["payload"]["until_ns"] for row in holds)
        ):
            raise ReadError(Failure.RATE_LIMIT)
        if not limits.observed_hour:
            raise ReadError(Failure.POLICY)
        self.busy = True
        try:
            # This fsync must succeed BEFORE the wire attempt. Restart never refunds it.
            claim = self.journal.append("attempt", {"endpoint": endpoint}, ms, ns)
            reply = await self.wire.get(url, limits)
            ms, ns = self.now()
            self.journal.append(
                "response",
                {
                    "attempt": claim["sequence"],
                    "status": reply.status,
                    "sha256": hashlib.sha256(reply.body).hexdigest(),
                    "body_hex": reply.body.hex(),
                    "retry_after_ms": reply.retry_after_ms,
                },
                ms,
                ns,
            )
            if reply.status in {418, 429}:
                self.journal.append(
                    "hold",
                    {
                        "until_ns": ns + max(60000, reply.retry_after_ms or 0) * 1000000,
                        "reason": "server-rate-limit",
                    },
                    ms,
                    ns,
                )
            return reply
        finally:
            self.busy = False

    async def close(self) -> None:
        await self.wire.close()
