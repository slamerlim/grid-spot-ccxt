"""Existing financial supervisor driven by hypothetical subsequent-book fills.

Public unknowns never become unrestricted rules. Ineligible runs retain the
invented 1,000 USDT pool and collect observations without creating an allocation.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import replace
from decimal import Decimal as D
from typing import Any

from grid.domain.market import FeeRule, Limits, Market, Money
from grid.domain.model import ActualFee, Book, Context, Scope, Side
from grid.domain.numbers import DomainError, exact, units
from grid.exchange.models import BookObservation, MarketObservation
from grid.execution.models import LimitRequest
from grid.execution.synthetic import SyntheticExchange
from grid.operations.experiment import control
from grid.operations.model import Action
from grid.operations.supervisor import MockSupervisor
from grid.operations.synthetic import EXECUTION, OPERATIONS, STORAGE, fixture
from grid.paper.journal import Journal, clock
from grid.persistence import codec
from grid.persistence.storage import restore
from grid.persistence.writer import AsyncWriter
from grid.portfolio.model import CyclePhase, ExitRule, PortfolioPolicy, ValuationFacts
from grid.universe.model import Facts, MarketKey, SelectionMode, UniversePolicy

SCOPE = Scope("paper-okx", "invented-account", "BTC/USDT", "paper-allocation", "grid", "hour")
FEE = FeeRule("USDT", D("0.002"), D("0.00000001"), None, 64)


def missing_rules(market: MarketObservation) -> tuple[str, ...]:
    reasons: list[str] = []
    if (
        (market.venue, market.symbol, market.base, market.quote, market.raw_id)
        != ("okx", "BTC/USDT", "BTC", "USDT", "BTC-USDT")
        or market.active is not True
        or market.unmapped_constraints
    ):
        reasons.append("identity-or-active-rules")
    if market.price is None or market.amount is None:
        reasons.append("precision")
    for name in ("prices", "amounts", "costs", "buy_amount", "sell_amount"):
        limits = getattr(market, name)
        if limits.minimum is None:
            reasons.append(name + "-minimum")
        if limits.maximum is None:
            reasons.append(name + "-maximum")
    return tuple(reasons)


def contract(market: MarketObservation) -> Market:
    if missing_rules(market) or market.price is None or market.amount is None:
        raise DomainError("incomplete public market constraints")

    def bounds(name: str) -> Limits:
        observed = getattr(market, name)
        minimum, maximum = observed.minimum, observed.maximum
        if not isinstance(minimum, D) or not isinstance(maximum, D):
            raise DomainError("unknown is not unlimited")
        return Limits(minimum, maximum)

    return Market(
        "BTC/USDT",
        "BTC",
        "USDT",
        market.generation,
        market.price,
        market.amount,
        bounds("prices"),
        bounds("buy_amount"),
        bounds("costs"),
        bounds("sell_amount"),
        bounds("costs"),
        True,
        False,
    )


async def replay_gateway(journal: Journal) -> SyntheticExchange:
    gateway = SyntheticExchange(1000)
    for row in journal.kinds("venue"):
        kind, payload = row["payload"]["kind"], row["payload"]["value"]
        if kind == "submit":
            scope = Scope(**payload["scope"])
            request = LimitRequest(
                scope,
                payload["order_id"],
                payload["client_id"],
                Side(payload["side"]),
                payload["amount"],
                payload["price"],
                payload["config_revision"],
                payload["market_generation"],
            )
            await gateway.submit_limit(request)
        elif kind == "cancel":
            await gateway.cancel_limit(gateway.orders[payload].request.scope, payload)
        elif kind == "settle":
            gateway.settle_cancel(payload)
        elif kind == "fill":
            fees = tuple(
                ActualFee(
                    f["currency"],
                    D(f["amount"]),
                    None if f["quote_value"] is None else D(f["quote_value"]),
                )
                for f in payload["fees"]
            )
            gateway.fill(
                payload["external_order"],
                payload["external_id"],
                D(payload["amount"]),
                D(payload["price"]),
                fees,
            )
        else:
            raise DomainError("unknown synthetic venue journal event")
    return gateway


@exact
def cross(gateway: SyntheticExchange, book: BookObservation, sequence: int) -> None:
    """At most one full fill using strictly crossed visible depth, then cancellations.

    Called before the supervisor sees this book, so an order created from this
    observation can never fill on it. No assertion about real queue priority.
    """
    for external, order in gateway.orders.items():
        if order.status != "open":
            continue
        limit = D(order.request.price)
        remaining = D(order.request.amount) - order.cumulative
        buy = order.request.side is Side.BUY
        levels = book.asks if buy else book.bids
        visible = sum((q for p, q in levels if (p < limit if buy else p > limit)), D(0))
        if visible >= remaining:
            fee = units(remaining * limit * FEE.rate, D("0.00000001"), up=True)
            gateway.fill(
                external,
                f"paper-trade-{sequence}",
                remaining,
                limit,
                (ActualFee("USDT", fee, None),),
            )
            break
    # A cancellation ACK can race a fill; settle only on a later fresh observation.
    for external in gateway.cancels:
        gateway.settle_cancel(external)


class PaperEngine:
    def __init__(
        self,
        journal: Journal,
        sources: frozenset[str],
        gateway: SyntheticExchange,
        now: Callable[[], tuple[int, int]],
    ) -> None:
        self.journal, self.sources, self.gateway, self.now = journal, sources, gateway, now
        self.path = journal.directory / "paper.db"
        self.writer: AsyncWriter
        self.owner: MockSupervisor | None = None
        self.context: Context | None = None
        self.universe: UniversePolicy | None = None
        self.portfolio: PortfolioPolicy | None = None
        self.reason: tuple[str, ...] = ("awaiting-complete-public-rules",)
        self.ready = False
        self.closed = False
        self.pool_cash = D(0)
        faults = journal.kinds("admission-fault")
        self.fatal_reason: str | None = faults[-1]["payload"]["reason"] if faults else None
        processed = journal.kinds("paper-observe")
        self.last_source_ms = processed[-1]["payload"]["source_ms"] if processed else 0

    @classmethod
    async def open(
        cls,
        journal: Journal,
        sources: frozenset[str],
        now: Callable[[], tuple[int, int]] = clock,
    ) -> PaperEngine:
        gateway = await replay_gateway(journal)
        engine = cls(journal, sources, gateway, now)

        def record(kind: str, value: object) -> None:
            journal.append("venue", {"kind": kind, "value": value}, *now())

        gateway.record = record
        engine.writer = await AsyncWriter.open(engine.path, STORAGE, sources, mock_storage=True)
        try:
            await engine.writer.submit(
                lambda ledger: ledger.credit(
                    "paper-funding", SCOPE.venue, SCOPE.account, Money("USDT", D(1000))
                )
            )
            saved = journal.kinds("paper-contract")
            if saved:
                data = saved[0]["payload"]
                engine.context = codec.loads(data["context"], Context)
                engine.universe = codec.loads(data["universe"], UniversePolicy)
                engine.portfolio = codec.loads(data["portfolio"], PortfolioPolicy)
                await engine._owner()
            return engine
        except BaseException:
            await engine.close()
            raise

    async def _owner(self) -> None:
        context = self.context
        if context is None:
            raise DomainError("missing paper context")

        # Credit above and allocation use stable IDs; an interrupted first boot
        # cannot double fund or start a second allocation.
        def prepare(ledger: Any) -> None:
            if ledger.lookup("paper-start") is None:
                ledger.start("paper-start", SCOPE, (Money("USDT", D(1000)),), context)

        await self.writer.close()
        self.owner = await MockSupervisor.open(
            self.path,
            STORAGE,
            self.sources,
            (SCOPE,),
            self.gateway,
            EXECUTION,
            replace(OPERATIONS, tick_ms=5000, max_wal_bytes=16 * 1024**2),
            self.now,
            prepare,
        )
        self.writer = self.owner.writer

    async def observe(
        self, market: MarketObservation, book: BookObservation, sequence: int
    ) -> None:
        if self.fatal_reason is not None:
            self.reason = (self.fatal_reason,)
            await self.halt(self.fatal_reason)
            raise DomainError("paper admission remains latched for operator review")
        ms, ns = self.now()
        book.require_fresh(ns, ms, 10000)
        market.require_fresh(ns, 3600 * 10**9)
        if (
            book.generation != market.generation
            or book.source_ms is None
            or book.source_ms <= self.last_source_ms
        ):
            raise DomainError("duplicate or mismatched paper observation")
        self.journal.append("paper-observe", {"source_ms": book.source_ms}, ms, ns)
        self.last_source_ms = book.source_ms
        self.reason = missing_rules(market)
        if self.reason:
            await self.halt("incomplete-rules")
            return
        rules = contract(market)
        if self.context is None:
            original = fixture(1, 1).contexts[0][1]
            bid = book.bids[0][0]
            policy = replace(
                original.policy,
                spacing=D("0.01"),
                quote_reserve=D(100),
                max_quote_committed=D(900),
                lower_price=bid * D("0.5"),
                upper_price=bid * D(2),
                max_source_age_ms=3600000,
                cost_quantum=D("0.00000001"),
                seed_at="bid",
            )
            self.context = Context(
                rules,
                policy,
                FEE,
                FEE,
                1,
                ms,
                ms,
                ms,
                Book(bid, book.asks[0][0], book.source_ms or ms, 1),
                True,
            )
            self.universe = UniversePolicy(
                SelectionMode.MANUAL,
                (SCOPE.venue,),
                "USDT",
                (MarketKey.of(SCOPE),),
                (),
                (),
                1,
                1,
                1,
                10000,
                0,
                D(0),
                D("0.01"),
                D(2),
                D("0.01"),
            )
            self.portfolio = PortfolioPolicy(
                "USDT",
                (),
                (ExitRule(SCOPE, "bid", bid * D("0.8"), D("0.05"), D(0), 30000, 3),),
                (),
                (),
                D(0),
                10000,
                5000,
                120000,
                "no_transfers",
            )
            self.journal.append(
                "paper-contract",
                {
                    "context": codec.dumps(self.context),
                    "universe": codec.dumps(self.universe),
                    "portfolio": codec.dumps(self.portfolio),
                    "fee_provenance": "assumed-paper-only-0.2-percent-quote",
                    "real_account_verified": False,
                },
                ms,
                ns,
            )
            await self._owner()
        if self.context.market != rules:
            raise DomainError("public rules changed; paper admission halted")
        owner = self.owner
        if owner is None or self.universe is None or self.portfolio is None:
            raise DomainError("paper owner unavailable")
        cycle = await self.writer.submit(lambda ledger: ledger.portfolio())
        if cycle is not None and cycle.phase not in (CyclePhase.ACTIVE, CyclePhase.COMPLETE):
            # I7 uses a stricter 95% trigger anchor. Also refuse a step if its
            # next candidate could exceed this fresh bid's 5% band.
            # No widening to manufacture an exit after the public price moves.
            for slot in cycle.slots:
                bid = book.bids[0][0]
                candidate = rules.price.round(
                    max(self.portfolio.exits[0].floor, slot.anchor_bid * D("0.95"), bid), up=True
                )
                if not bid * D("0.95") <= candidate <= bid * D("1.05"):
                    await self.refuse("exit-reference-moved")
        cross(self.gateway, book, sequence)
        context = replace(
            self.context,
            now_ms=ms,
            # Revalidate the immutable PAPER contract and assumed fee, not the
            # public metadata timestamp (preserved in MarketObservation above).
            rules_observed_ms=ms,
            fees_observed_ms=ms,
            book=Book(book.bids[0][0], book.asks[0][0], book.source_ms or ms, book.generation),
        )
        # These flags describe ONLY the invented paper account/rules/fee model.
        # The public/private evidence report keeps real account verification false.
        row = Facts(
            MarketKey.of(SCOPE),
            context,
            book,
            sequence,
            ms,
            ns,
            f"paper-assumptions-{sequence}",
            True,
            True,
            True,
        )
        await owner.observe(
            ValuationFacts(sequence, ms, ns, f"paper-packet-{sequence}", True, (row,), (), ())
        )
        await owner.step()
        if not self.ready:
            if await self.writer.submit(lambda ledger: ledger.lookup("paper-universe")) is None:
                await owner.universe.configure(
                    "paper-universe", 0, self.universe, "paper-runner", "explicit simulation", ms
                )
            if await self.writer.submit(lambda ledger: ledger.portfolio()) is None:
                await owner.portfolio.begin(
                    "paper-cycle",
                    0,
                    "paper-cycle",
                    self.portfolio,
                    "paper-runner",
                    "explicit simulation",
                )
            await control(owner, Action.RESUME, f"paper-resume-{sequence}")
            self.ready = True

    async def halt(self, reason: str) -> None:
        if self.owner is not None and self.ready:
            await control(self.owner, Action.HALT, f"paper-halt-{len(self.journal.rows)}-{reason}")
        self.ready = False

    async def refuse(self, reason: str) -> None:
        """A risk refusal survives both fresh prices and process restart."""
        self.fatal_reason = reason  # Stop locally even if either durable write fails.
        self.reason = (reason,)
        try:
            self.journal.append("admission-fault", {"reason": reason}, *self.now())
        finally:
            await self.halt(reason)
        raise DomainError("paper admission requires operator review")

    async def status(self) -> dict[str, object]:
        if not self.closed:
            self.pool_cash = await self.writer.submit(
                lambda ledger: ledger.pool(SCOPE.venue, SCOPE.account, "USDT")
            )
        return {
            "eligibility_missing": self.reason,
            "paper_active": self.ready,
            "real_account_verified": False,
            "admission_fault": self.fatal_reason,
            "fee_assumption": "0.002 USDT-side rate",
            "synthetic_orders": len(self.gateway.sent),
            "synthetic_fills": len(self.gateway.trades),
            "synthetic_cancels": len(self.gateway.cancels),
            "unallocated_synthetic_usdt": format(self.pool_cash, "f"),
            "unallocated_only_nav_usdt": format(self.pool_cash, "f")
            if self.owner is None
            else None,
            "unallocated_only_return": "0"
            if self.owner is None and self.pool_cash == 1000
            else None,
            "health": await self.owner.status() if self.owner else None,
        }

    async def backup(self, number: int) -> None:
        directory = self.journal.directory
        target, verified = directory / f"backup-{number}.db", directory / f"verified-{number}.db"
        if self.owner:
            stamp: object = await self.owner.backup(target, verified)
        else:
            backup = await self.writer.submit(lambda ledger: ledger.backup(target))
            stamp = await asyncio.to_thread(
                restore, backup, verified, STORAGE, self.sources, mock_storage=True
            )
        self.journal.append(
            "backup",
            {"number": number, "stamp": stamp, "journal_prefix_sha256": self.journal.previous},
            *self.now(),
        )

    async def close(self) -> None:
        if self.closed:
            return
        try:
            if hasattr(self, "writer"):
                await self.status()
        finally:
            try:
                if self.owner:
                    await self.owner.close()
                elif hasattr(self, "writer"):
                    await self.writer.close()
            finally:
                self.closed = True
