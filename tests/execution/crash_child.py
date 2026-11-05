"""Real SIGKILL around MockExecutor sends; outside-process fake receipt is test-only."""

import asyncio
import json
import os
import signal
import sys
from decimal import Decimal as D
from pathlib import Path

from grid.domain.market import Money
from grid.domain.model import ActualFee, Context, Scope
from grid.execution.client_ids import MappedSyntheticExchange
from grid.execution.executor import MockExecutor
from grid.execution.models import Evidence, LimitRequest, Limits, OrderFact
from grid.execution.synthetic import SyntheticExchange
from grid.persistence import codec
from grid.persistence.records import Bounds, ClientIdPolicy
from grid.persistence.writer import AsyncWriter


async def main() -> None:
    data = json.loads(sys.stdin.read())
    scope, context = codec.loads(data["scope"], Scope), codec.loads(data["context"], Context)
    path = Path(data["path"])
    posting = data["boundary"].startswith(("bind:", "event:"))
    armed = not posting

    def crash(stage: str) -> None:
        if armed and stage == data["boundary"]:
            os.kill(os.getpid(), signal.SIGKILL)

    writer = await AsyncWriter.open(
        path, Bounds(*data["bounds"]), frozenset(data["sources"]), mock_storage=True, fault=crash
    )
    await writer.submit(
        lambda ledger: ledger.credit("credit", scope.venue, scope.account, Money("QUOTE", D(1000)))
    )
    await writer.submit(
        lambda ledger: ledger.start("start", scope, (Money("QUOTE", D(1000)),), context)
    )
    venue = (
        MappedSyntheticExchange(100, writer, ClientIdPolicy("mock-prefix-v1", "t-"))
        if data.get("mapped", False)
        else SyntheticExchange(100)
    )
    original = venue.submit_limit

    async def send(request: LimitRequest) -> OrderFact:
        crash("fake:before_effect")
        result = await original(request)
        identity = (
            {
                "wire_client_id": venue.wire_orders[result.external_id].client_id,
                "submitted_client_id": venue.submitted_clients[result.external_id],
            }
            if isinstance(venue, MappedSyntheticExchange)
            else {"client_id": request.client_id, "order_id": request.order_id}
        )
        # This records a synthetic venue receipt, not a financial database.
        with path.with_suffix(".receipt").open("x") as stream:
            stream.write(
                json.dumps(
                    {
                        "external_id": result.external_id,
                        "price": request.price,
                        "amount": request.amount,
                        **identity,
                    }
                )
            )
            stream.flush()
            os.fsync(stream.fileno())
        crash("fake:after_effect")
        return result

    venue.submit_limit = send  # type: ignore[method-assign]
    executor = MockExecutor(
        writer, venue, (scope,), Limits(*data["limits"]), Evidence(True, True, True), fault=crash
    )
    if not await executor.service(scope, context):
        raise RuntimeError(executor.last_error)
    await executor.dispatch(scope, context)
    if posting:
        venue.fill("order-1", "fill", D(1), D(100), (ActualFee("QUOTE", D(0), None),))
        armed = True
        await executor.service(scope, context)
    raise RuntimeError("requested fault boundary was not reached")


if __name__ == "__main__":
    asyncio.run(main())
