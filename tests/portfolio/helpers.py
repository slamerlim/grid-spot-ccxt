from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from decimal import Decimal as D
from pathlib import Path

from execution.helpers import EVIDENCE, LIMITS, ZERO_FEES
from persistence.conftest import SOURCES

from grid.domain.market import Money
from grid.domain.model import Context, Scope, State
from grid.execution.executor import MockExecutor
from grid.execution.synthetic import SyntheticExchange
from grid.persistence.ledger import Ledger
from grid.persistence.portfolio import register_sink
from grid.persistence.records import Bounds
from grid.persistence.writer import AsyncWriter
from grid.portfolio.controller import PortfolioController
from grid.portfolio.model import Cycle, PortfolioPolicy, ValuationFacts

from .conftest import packet, prices


@dataclass
class Harness:
    writer: AsyncWriter
    executor: MockExecutor
    venue: SyntheticExchange
    owner: PortfolioController
    contexts: dict[Scope, Context]
    now: list[int]
    policy: PortfolioPolicy
    revision: int = 0

    async def state(self, scope: Scope) -> State:
        result = await self.writer.submit(lambda ledger: ledger.snapshot(scope))
        assert result.state is not None
        return result.state

    async def facts(self) -> ValuationFacts:
        self.revision += 1
        rows = tuple([(await self.state(scope), c) for scope, c in self.contexts.items()])
        return packet(rows, self.revision)

    async def observe(self) -> None:
        await self.owner.observe(await self.facts())

    async def status(self) -> Cycle:
        cycle = await self.owner.status()
        assert cycle is not None
        return cycle

    async def service(self) -> bool:
        results = [await self.executor.service(scope, c) for scope, c in self.contexts.items()]
        return all(results)

    def tick(self, milliseconds: int = 1, bid: D | None = None) -> None:
        self.now[0] += milliseconds
        self.contexts = {
            s: prices(c, bid if bid is not None else c.book.bid if c.book else D(100), self.now[0])
            for s, c in self.contexts.items()
        }

    async def seed(self, scope: Scope) -> None:
        assert await self.executor.dispatch(scope, self.contexts[scope]), self.executor.last_error
        external = "order-" + str(len(self.venue.sent))
        self.venue.fill(external, "seed-" + external, D(1), D(100), ZERO_FEES)
        assert await self.service(), self.executor.last_error

    async def trigger(self, bid: D = D(200)) -> None:
        self.tick(bid=bid)
        assert await self.service(), self.executor.last_error
        await self.observe()
        cycle = await self.status()
        await self.owner.evaluate("trigger", cycle.revision, "tester", "threshold sample")
        assert await self.service(), self.executor.last_error

    async def advance(self, name: str) -> Cycle:
        await self.observe()
        cycle = await self.status()
        await self.owner.advance(name, cycle.revision, "tester", "exit supervision")
        return await self.status()


@asynccontextmanager
async def harness(
    path: Path,
    bounds: Bounds,
    contexts: dict[Scope, Context],
    policy: PortfolioPolicy,
    *,
    token: D = D(0),
) -> AsyncIterator[Harness]:
    writer = await AsyncWriter.open(path, bounds, SOURCES, mock_storage=True)
    try:
        for index, (scope, context) in enumerate(contexts.items()):

            def setup(
                ledger: Ledger, index: int = index, scope: Scope = scope, context: Context = context
            ) -> None:
                if scope in policy.sinks:
                    register_sink(ledger, f"sink-{index}", scope, context)
                    return
                ledger.credit(
                    f"quote-{index}",
                    scope.venue,
                    scope.account,
                    Money(context.market.quote, D(1000)),
                )
                funds: tuple[Money, ...] = (Money(context.market.quote, D(1000)),)
                if token:
                    ledger.credit(
                        f"token-{index}", scope.venue, scope.account, Money("TOKEN", token)
                    )
                    funds += (Money("TOKEN", token),)
                ledger.start(f"start-{index}", scope, funds, context)

            await writer.submit(setup)
        venue = SyntheticExchange(1000)
        executor = MockExecutor(
            writer, venue, tuple(contexts), replace(LIMITS, quiet_ms=10000), EVIDENCE
        )
        now = [next(iter(contexts.values())).now_ms]
        owner = PortfolioController(executor, lambda: (now[0], now[0] * 1000000))
        result = Harness(writer, executor, venue, owner, contexts, now, policy)
        try:
            assert await result.service()
            await result.observe()
            yield result
        finally:
            await owner.close()
            await executor.close()
    finally:
        await writer.close()
