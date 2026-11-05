"""SIGKILL fixture for subprocess tests; never imported by the application."""

import json
import os
import signal
import sys
from decimal import Decimal as D
from pathlib import Path

from grid.domain.market import Money
from grid.domain.model import ActualFee, Context, Fill, Scope
from grid.persistence import client_ids, codec
from grid.persistence.ledger import Ledger
from grid.persistence.records import Bounds, ClientIdPolicy


def main() -> None:
    data = json.loads(sys.stdin.read())
    scope = codec.loads(data["scope"], Scope)
    context = codec.loads(data["context"], Context)
    path, action, boundary = Path(data["path"]), data["action"], data["boundary"]
    bounds = Bounds(*data["bounds"])
    ledger = Ledger(path, bounds, frozenset(data["sources"]), mock_storage=True)
    if action != "credit":
        ledger.credit("setup-funds", scope.venue, scope.account, Money("QUOTE", D(1000)))
        ledger.credit("setup-token", scope.venue, scope.account, Money("TOKEN", D(10)))
    if action not in {"credit", "start"}:
        ledger.start("setup-seed", scope, (Money("QUOTE", D(1000)), Money("TOKEN", D(10))), context)
    if action == "compact":
        ledger.apply("setup-fill", 1, Fill(scope, "seed-fill", 1, D(1), D(100), ()), context)

    def crash(stage: str) -> None:
        if stage == boundary:
            os.kill(os.getpid(), signal.SIGKILL)

    ledger.storage.fault = crash
    if action == "credit":
        ledger.credit("subject", scope.venue, scope.account, Money("QUOTE", D(100)))
    elif action == "start":
        ledger.start("subject", scope, (Money("QUOTE", D(1000)), Money("TOKEN", D(10))), context)
    elif action == "event":
        ledger.apply(
            "subject",
            1,
            Fill(
                scope,
                "trade",
                1,
                D("0.5"),
                D(100),
                (
                    ActualFee("QUOTE", D("0.1"), None),
                    ActualFee("BASE", D("0.001"), None),
                    ActualFee("TOKEN", D("0.01"), D("0.2")),
                ),
            ),
            context,
        )
    elif action == "release":
        ledger.release("subject", scope, 1, Money("QUOTE", D(900)))
    elif action == "compact":
        ledger.compact("subject", scope, 2)
    elif action == "client-id":
        client_ids.register(ledger, "subject", scope, 1, ClientIdPolicy("mock-prefix-v1", "t-"))
    elif action == "attempt":
        intent = ledger.intents(scope)[0]
        ledger.attempt("subject", scope, 1, intent.id)
        crash("fake:before_effect")
        path.with_name("fake-effect").write_text("one synthetic send\n")
        crash("fake:after_effect")
    else:
        raise ValueError("unknown crash action")
    ledger.close()


if __name__ == "__main__":
    main()
