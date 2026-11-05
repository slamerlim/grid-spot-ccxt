"""Sole-writer external client IDs, committed before any submission attempt."""

from __future__ import annotations

import base64
import secrets
from typing import TYPE_CHECKING

from grid.domain.model import Scope
from grid.domain.numbers import bounded_int, identifier
from grid.persistence import codec
from grid.persistence.records import ClientIdentity, ClientIdPolicy, Conflict, LedgerError, Outcome

if TYPE_CHECKING:
    from grid.persistence.ledger import Ledger


def client_identity(scope: Scope, order_id: int) -> str:
    """Unchanged internal identity, distinct from a venue-facing client ID."""
    return codec.digest(("client", scope, order_id))


def _decode(ledger: Ledger, row: tuple[object, ...]) -> ClientIdentity:
    payload = row[8]
    if not isinstance(payload, str):
        raise LedgerError("client identity payload missing")
    record = codec.loads(payload, ClientIdentity, limit=ledger.bounds.snapshot_bytes)
    expected = (
        codec.digest(record.scope),
        record.order_id,
        record.scope.venue,
        record.scope.account,
        client_identity(record.scope, record.order_id),
        record.submitted_id,
        record.returned_id,
    )
    if row[:7] != expected or record.internal_id != expected[4]:
        raise LedgerError("client identity projection mismatch")
    ledger.order(record.scope, record.order_id)
    receipt = ledger.lookup(str(row[7]))
    if receipt is None or receipt.notices != (codec.digest(record),):
        raise LedgerError("client identity lacks matching registration receipt")
    return record


def lookup(ledger: Ledger, scope: Scope, order_id: int) -> ClientIdentity | None:
    bounded_int(order_id, 1, 2**63 - 1)
    row = ledger.db.execute(
        "SELECT * FROM client_ids WHERE allocation=? AND order_id=?",
        (codec.digest(scope), order_id),
    ).fetchone()
    return None if row is None else _decode(ledger, row)


def resolve(ledger: Ledger, venue: str, account: str, returned_id: str) -> ClientIdentity | None:
    for value in (venue, account, returned_id):
        identifier(value)
    row = ledger.db.execute(
        "SELECT * FROM client_ids WHERE venue=? AND account=? AND returned_id=?",
        (venue, account, returned_id),
    ).fetchone()
    return None if row is None else _decode(ledger, row)


def verify(ledger: Ledger, scope: Scope) -> None:
    # Streaming indexed traversal; never retain all historical IDs in memory.
    for row in ledger.db.execute(
        "SELECT * FROM client_ids WHERE allocation=? ORDER BY order_id", (codec.digest(scope),)
    ):
        if _decode(ledger, row).scope != scope:
            raise LedgerError("client identity belongs to another scope")


def register(
    ledger: Ledger, request_id: str, scope: Scope, order_id: int, policy: ClientIdPolicy
) -> ClientIdentity:
    if type(policy) is not ClientIdPolicy:
        raise LedgerError("explicit client-ID policy required")
    allocation = codec.digest(scope)

    def action() -> Outcome:
        previous = lookup(ledger, scope, order_id)
        if previous is not None:
            if previous.policy != policy:
                raise Conflict("client-ID policy cannot change for an existing order")
            return Outcome(None, None, True, notices=(codec.digest(previous),))
        order = ledger.order(scope, order_id)
        if (
            order.terminal
            or ledger.attempted(scope, order_id)
            or ledger.db.execute(
                "SELECT 1 FROM intents WHERE allocation=? AND order_id=? "
                "AND kind='place' AND status='queued' AND attempt_run IS NULL",
                (allocation, order_id),
            ).fetchone()
            is None
        ):
            raise Conflict("new client identity requires an unattempted queued placement")
        for _ in range(8):
            submitted = base64.b32encode(secrets.token_bytes(16)).decode().rstrip("=").lower()
            returned = policy.returned_prefix + submitted
            if (
                ledger.db.execute(
                    "SELECT 1 FROM client_ids WHERE venue=? AND account=? "
                    "AND (submitted_id=? OR returned_id=?) LIMIT 1",
                    (scope.venue, scope.account, submitted, returned),
                ).fetchone()
                is not None
            ):
                continue
            record = ClientIdentity(
                scope, order_id, client_identity(scope, order_id), submitted, returned, policy
            )
            ledger.db.execute(
                "INSERT INTO client_ids VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    allocation,
                    order_id,
                    scope.venue,
                    scope.account,
                    record.internal_id,
                    submitted,
                    returned,
                    request_id,
                    codec.dumps(record),
                ),
            )
            return Outcome(None, None, True, notices=(codec.digest(record),))
        raise Conflict("bounded client-ID collision attempts exhausted")

    ledger._request(
        request_id, ("client-id", scope, order_id, policy), "client-id", allocation, action
    )
    result = lookup(ledger, scope, order_id)
    if result is None or result.policy != policy:
        raise LedgerError("client identity unavailable after registration")
    return result
