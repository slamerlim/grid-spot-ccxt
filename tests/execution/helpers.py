from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from decimal import Decimal as D
from pathlib import Path

from persistence.conftest import SOURCES

from grid.domain.market import Money
from grid.domain.model import ActualFee, Context, Scope, State
from grid.execution.executor import MockExecutor
from grid.execution.models import Evidence, Limits
from grid.execution.synthetic import SyntheticExchange
from grid.persistence.records import Bounds
from grid.persistence.writer import AsyncWriter

ZERO_FEES = (ActualFee("QUOTE", D(0), None),)
LIMITS = Limits(4, 20, 20, 1000, 1000, 1000, 1000, 1000, 1, 1000)
EVIDENCE = Evidence(True, True, True)


@dataclass
class Harness:
    writer: AsyncWriter
    executor: MockExecutor
    venue: SyntheticExchange
    scope: Scope
    context: Context

    async def current(self) -> State:
        result = await self.writer.submit(lambda ledger: ledger.snapshot(self.scope))
        assert result.state is not None
        return result.state

    async def service(self) -> bool:
        return await self.executor.service(self.scope, self.context)

    async def send(self) -> bool:
        return await self.executor.dispatch(self.scope, self.context)

    def advance(self, milliseconds: int = 1001) -> None:
        now = self.context.now_ms + milliseconds
        assert self.context.book is not None
        self.context = replace(
            self.context,
            now_ms=now,
            rules_observed_ms=now,
            fees_observed_ms=now,
            book=replace(self.context.book, observed_ms=now),
        )

    async def seed(self) -> None:
        assert await self.service(), self.executor.last_error
        assert await self.send(), self.executor.last_error
        self.venue.fill("order-1", "seed", D(1), D(100), ZERO_FEES)
        assert await self.service(), self.executor.last_error


@asynccontextmanager
async def harness(
    path: Path,
    bounds: Bounds,
    state: State,
    context: Context,
    *,
    limits: Limits = LIMITS,
) -> AsyncIterator[Harness]:
    writer = await AsyncWriter.open(path, bounds, SOURCES, mock_storage=True)
    scope = state.scope
    try:
        await writer.submit(
            lambda ledger: ledger.credit(
                "funds", scope.venue, scope.account, Money("QUOTE", D(1000))
            )
        )
        await writer.submit(
            lambda ledger: ledger.credit("token", scope.venue, scope.account, Money("TOKEN", D(10)))
        )
        await writer.submit(
            lambda ledger: ledger.start(
                "start",
                scope,
                (Money("QUOTE", D(1000)), Money("TOKEN", D(10))),
                context,
            )
        )
        venue = SyntheticExchange(1000)
        executor = MockExecutor(writer, venue, (scope,), limits, EVIDENCE)
        try:
            yield Harness(writer, executor, venue, scope, context)
        finally:
            await executor.close()
    finally:
        await writer.close()
