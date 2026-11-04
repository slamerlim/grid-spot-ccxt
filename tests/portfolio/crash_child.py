"""Real process death at portfolio transaction boundaries, with a synthetic venue."""

import asyncio
import json
import os
import signal
import sys
from dataclasses import replace
from decimal import Decimal as D
from pathlib import Path

from grid.domain.model import Context, Fill, State
from grid.persistence import codec
from grid.persistence.ledger import Ledger
from grid.persistence.records import Bounds
from grid.portfolio.model import Cashflow, PoolKey, PortfolioPolicy


async def main() -> None:
    sys.path.insert(0, str(Path(__file__).parents[1]))
    from execution.helpers import ZERO_FEES

    from portfolio.helpers import harness

    data = json.loads(sys.stdin.read())
    state = codec.loads(data["state"], State)
    context = codec.loads(data["context"], Context)
    policy = codec.loads(data["policy"], PortfolioPolicy)
    operation = data["operation"]
    armed = False

    def crash(stage: str) -> None:
        if armed and stage == data["boundary"]:
            os.kill(os.getpid(), signal.SIGKILL)

    async with harness(
        Path(data["path"]), Bounds(*data["bounds"]), {state.scope: context}, policy
    ) as h:

        def install(ledger: Ledger) -> None:
            ledger.storage.fault = crash

        await h.writer.submit(install)
        armed = operation == "begin"
        await h.owner.begin("begin", 0, "cycle", policy, "tester", "baseline")
        if operation == "cashflow":
            key = PoolKey(state.scope.venue, state.scope.account, "QUOTE")
            flow = Cashflow("deposit", key, D(500), 10000, "external proof", "unclassified")
            await h.owner.cashflow("pending", 1, flow, "tester", "classify later")
            armed = True
            await h.owner.cashflow(
                "adjust", 2, replace(flow, disposition="adjusted"), "tester", "verified deposit"
            )
        await h.seed(state.scope)
        armed = operation == "latch"
        await h.trigger()
        armed = operation == "plan"
        await h.advance("plan")
        assert await h.executor.dispatch(state.scope, h.contexts[state.scope])
        h.venue.fill("order-2", "exit", D(1), D(200), ZERO_FEES)
        armed = operation == "fill"
        if armed:

            def post(ledger: Ledger) -> None:
                snapshot = ledger.snapshot(state.scope)
                assert snapshot.state is not None and snapshot.version is not None
                order = next(o for o in snapshot.state.working if o.role == "portfolio_exit")
                ledger.apply(
                    "exit-fill",
                    snapshot.version,
                    Fill(state.scope, "exit", order.id, D(1), D(200), ZERO_FEES),
                    h.contexts[state.scope],
                )

            await h.writer.submit(post)
        assert await h.service()
        armed = operation == "complete"
        await h.advance("complete")
        raise RuntimeError("fault boundary not reached")


if __name__ == "__main__":
    asyncio.run(main())
