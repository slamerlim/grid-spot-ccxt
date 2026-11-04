"""Atomic universe revisions and retirement, using the existing sole ledger writer."""

from collections.abc import Callable
from dataclasses import replace

from grid.domain.model import Command, Control, Mode, OrderReport, Scope, Side
from grid.domain.numbers import ZERO, bounded_int, identifier
from grid.domain.reducer import reduce
from grid.persistence import codec
from grid.persistence.ledger import Ledger
from grid.persistence.records import Conflict, LedgerError, Outcome
from grid.universe.model import MarketKey, Membership, UniversePolicy, UniverseSnapshot


def current(ledger: Ledger) -> UniverseSnapshot | None:
    row = ledger.db.execute(
        "SELECT revision,payload FROM universe_revisions ORDER BY revision DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    snapshot = codec.loads(row[1], UniverseSnapshot, limit=ledger.bounds.snapshot_bytes)
    if snapshot.revision != row[0]:
        raise LedgerError("universe revision projection differs")
    return snapshot


def commit(
    ledger: Ledger,
    request_id: str,
    expected: int,
    policy: UniversePolicy,
    scopes: tuple[Scope, ...],
    actor: str,
    reason: str,
    now_ms: int,
    choose: Callable[[UniverseSnapshot | None], tuple[Scope, ...]],
) -> Outcome:
    identifier(actor)
    identifier(reason)
    bounded_int(expected, 0, 2**63 - 2)
    bounded_int(now_ms, 0, 2**53 - 1)
    if not 1 <= len(scopes) <= policy.candidate_limit or len(set(scopes)) != len(scopes):
        raise LedgerError("bounded unique supervised roster required")
    if len({MarketKey.of(scope) for scope in scopes}) != len(scopes):
        raise LedgerError("one allocation per account/symbol required")

    def action() -> Outcome:
        before = current(ledger)
        if expected != (0 if before is None else before.revision):
            raise Conflict("stale universe configuration revision")
        if before is not None and (
            now_ms < before.observed_ms or {m.scope for m in before.members} != set(scopes)
        ):
            raise Conflict("regressing clock or removal of supervised ownership")
        selected = set(choose(before))
        if not selected <= set(scopes) or len(selected) > policy.capacity:
            raise LedgerError("invalid selected roster")
        previous = {} if before is None else {m.scope: m for m in before.members}
        members: list[Membership] = []
        for scope in sorted(scopes, key=codec.dumps):
            allocation = codec.digest(scope)
            state, context, version, recovery = ledger._load(allocation)
            original = state
            context = replace(context, allow_new_exposure=False)
            old = previous.get(scope)
            own_retirement = bool(
                old
                and old.managed_retirement
                and old.control_revision == state.control_revision
                and state.mode is Mode.RETIRING
            )
            admitted = scope in selected
            if admitted and (
                not policy.permits(MarketKey.of(scope))
                or state.mode is not Mode.ACTIVE
                and not own_retirement
            ):
                raise LedgerError("selection cannot override an external control")
            commands: list[Command] = []
            if not admitted:
                # Revoke only ledger-proven unsent BUYs. Attempted/unknown orders survive.
                for order in tuple(state.working):
                    if (
                        order.side is Side.BUY
                        and order.filled == ZERO
                        and not order.unknown
                        and not ledger.attempted(scope, order.id)
                    ):
                        transition = reduce(
                            state, OrderReport(scope, order.id, "canceled", ZERO, True), context
                        )
                        if not transition.accepted:
                            raise LedgerError("unsent retirement revocation failed")
                        state = transition.state
                        commands.extend(transition.commands)
            desired = Mode.ACTIVE if admitted else Mode.RETIRING
            managed = own_retirement
            was_paused = state.mode is Mode.PAUSED
            if (
                state.mode in (Mode.ACTIVE, Mode.PAUSED)
                and not admitted
                or admitted
                and own_retirement
            ):
                transition = reduce(
                    state, Control(scope, state.control_revision + 1, desired), context
                )
                if not transition.accepted:
                    raise LedgerError("universe control rejected")
                state = transition.state
                commands.extend(transition.commands)
                managed = not admitted and not was_paused
            # A paused scope may retire, but can never be auto-resumed by this owner.
            # External EXIT/RETIRING controls remain untouched. Supervision remains.
            ledger._changes(request_id, allocation, original, state, context)
            ledger._save(allocation, state, context, version + 1, recovery=recovery)
            ledger._commands(allocation, tuple(commands), version + 1)
            since = old.since_ms if admitted and old and old.selected else now_ms
            members.append(Membership(scope, admitted, since, state.control_revision, managed))
        snapshot = UniverseSnapshot(expected + 1, policy, tuple(members), now_ms, actor, reason)
        ledger.db.execute(
            "INSERT INTO universe_revisions VALUES (?,?,?)",
            (snapshot.revision, request_id, ledger._text(snapshot)),
        )
        return Outcome(None, snapshot.revision, True)

    return ledger._request(
        request_id,
        ("universe", expected, policy, scopes, actor, reason, now_ms),
        "universe",
        None,
        action,
    )
