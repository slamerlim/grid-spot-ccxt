"""Real process-death fixture at universe commit boundaries; no external access."""

import json
import os
import signal
import sys
from decimal import Decimal as D
from pathlib import Path

from grid.domain.market import Money
from grid.domain.model import Context, Scope
from grid.persistence import codec
from grid.persistence.ledger import Ledger
from grid.persistence.records import Bounds
from grid.persistence.universe import commit
from grid.universe.model import UniversePolicy


def main() -> None:
    data = json.loads(sys.stdin.read())
    scope = codec.loads(data["scope"], Scope)
    context = codec.loads(data["context"], Context)
    policy = codec.loads(data["policy"], UniversePolicy)
    ledger = Ledger(
        Path(data["path"]), Bounds(*data["bounds"]), frozenset(data["sources"]), mock_storage=True
    )
    ledger.credit("fund", scope.venue, scope.account, Money("QUOTE", D(1000)))
    ledger.start("start", scope, (Money("QUOTE", D(1000)),), context)

    def crash(stage: str) -> None:
        if stage == data["boundary"]:
            os.kill(os.getpid(), signal.SIGKILL)

    ledger.storage.fault = crash
    commit(
        ledger, "retire", 0, policy, (scope,), "test-owner", "retire", 10000, lambda previous: ()
    )
    ledger.close()


if __name__ == "__main__":
    main()
