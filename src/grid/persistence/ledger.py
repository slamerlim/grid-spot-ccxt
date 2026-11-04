"""Atomic dedicated funding, reducer posting and durable command uncertainty."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

from grid.domain.market import Money
from grid.domain.model import (
    Cancel,
    CancelAcknowledged,
    Command,
    Context,
    Event,
    Fill,
    Mode,
    Order,
    OrderReport,
    PlaceLimit,
    Reconcile,
    Reconciled,
    Scope,
    Side,
    Start,
    State,
    SubmissionUnknown,
    Transition,
)
from grid.domain.numbers import ZERO, decimal, exact, identifier
from grid.domain.reducer import reduce, reserved
from grid.persistence import codec
from grid.persistence.records import Backup, Bounds, Conflict, Intent, LedgerError, Outcome
from grid.persistence.storage import Fault, Storage, no_fault
from grid.universe.model import BuyAdmission, UniverseSnapshot


def client_identity(scope: Scope, order_id: int) -> str:
    """Synthetic identity only; real venues need verified length/charset semantics."""
    return codec.digest(("client", scope, order_id))


def trade_identity(scope: Scope, external_id: str) -> str:
    identifier(external_id)
    return codec.digest(("trade", scope.venue, scope.account, scope.symbol, external_id))


@exact
def reservation_rows(state: State) -> tuple[tuple[int, str, str, str], ...]:
    rows: list[tuple[int, str, str, str]] = []
    for order in state.working:
        for money in order.reserves:
            paid = max(
                ZERO,
                sum(
                    (
                        fee.amount
                        for fill in state.fills
                        if fill.order_id == order.id
                        for fee in fill.fees or ()
                        if fee.currency == money.currency
                    ),
                    ZERO,
                ),
            )
            original = principal = ZERO
            if money.currency == order.principal_currency:
                original = order.amount * order.price if order.side is Side.BUY else order.amount
                principal = (
                    order.remaining * order.price if order.side is Side.BUY else order.remaining
                )
            fee = max(ZERO, money.amount - original - paid)
            rows.append((order.id, money.currency, codec.amount(principal), codec.amount(fee)))
    return tuple(sorted(rows))


def balances(state: State, context: Context) -> dict[str, Decimal]:
    return {
        context.market.quote: state.quote_cash,
        context.market.base: state.owned_base,
        **{money.currency: money.amount for money in state.fee_balances},
    }


def compact(state: State, context: Context) -> State:
    """Only settled history can leave memory; all facts and keys remain in SQLite."""
    keep = {order.id for order in state.working} | {lot.order_id for lot in state.lots}
    if state.seed_id is not None:
        keep.add(state.seed_id)
    ladder = [
        order
        for order in state.orders
        if order.round_id == state.round_id and order.level is not None
    ]
    if ladder:
        keep.add(min(ladder, key=lambda order: order.price).id)
    result = replace(
        state,
        orders=tuple(order for order in state.orders if order.id in keep),
        fills=tuple(
            fill
            for fill in state.fills
            if any(order.id == fill.order_id and not order.terminal for order in state.orders)
        ),
    )
    if (
        len(result.orders) > context.policy.max_order_records
        or len(result.fills) > context.policy.max_fill_records
    ):
        raise LedgerError("safe checkpoint exceeds state bounds")
    return result


class Ledger:
    """Synchronous core, used by AsyncWriter's sole worker; never share across threads."""

    def __init__(
        self,
        path: Path,
        bounds: Bounds,
        approved_sources: frozenset[str],
        *,
        mock_storage: bool,
        fault: Fault = no_fault,
    ) -> None:
        self.storage = Storage(
            path, bounds, approved_sources, mock_storage=mock_storage, fault=fault
        )
        self.db, self.bounds = self.storage.connection, bounds
        try:
            row = self.db.execute(
                "SELECT revision,payload FROM universe_revisions ORDER BY revision DESC LIMIT 1"
            ).fetchone()
            if row is not None:
                policy = codec.loads(row[1], UniverseSnapshot, limit=self.bounds.snapshot_bytes)
                if policy.revision != row[0]:
                    raise LedgerError("universe revision differs from projection")
                for member in policy.members:
                    owned, _, _, _ = self._load(codec.digest(member.scope))
                    if owned.scope != member.scope:
                        raise LedgerError("universe membership lacks financial ownership")
            for venue, account, currency, value in self.db.execute(
                "SELECT venue,account,currency,amount FROM pools ORDER BY venue,account,currency"
            ):
                funds = codec.parse_amount(value)
                decimal(funds)
                if funds != self._journal_total(
                    codec.digest((venue, account)), currency, "unassigned"
                ):
                    raise LedgerError("unassigned funds journal differs from pool")
            # Bounded streaming validation; history is not hydrated into one giant state.
            for allocation in self._allocation_ids():
                state, context, version, recovery = self._load(allocation)
                self._verify_projection(allocation, state, context)
                self._verify_identities(allocation, state.scope)
                if recovery and state.reconcile_reason is None:
                    state = replace(
                        state,
                        reconcile_reason="ledger owner recovery",
                        reconcile_revision=state.reconcile_revision + 1,
                    )

                    def recover(
                        allocation: str = allocation,
                        state: State = state,
                        context: Context = context,
                        version: int = version,
                    ) -> Outcome:
                        self._save(allocation, state, context, version + 1, recovery=True)
                        intents = self._commands(
                            allocation,
                            (Reconcile(state.scope, "ledger owner recovery"),),
                            version + 1,
                        )
                        return Outcome(state, version + 1, True, intents)

                    self._request(
                        "recovery-" + self.storage.run_id + "-" + allocation,
                        ("recovery", state.scope, self.storage.run_id, version),
                        "recovery",
                        allocation,
                        recover,
                    )
        except BaseException:
            self.storage.close()
            raise

    def _allocation_ids(self) -> Iterator[str]:
        """Finish each bounded SELECT before recovery writes/checkpoints on this connection."""
        after = ""
        while rows := self.db.execute(
            "SELECT id FROM allocations WHERE id>? ORDER BY id LIMIT ?",
            (after, self.bounds.read_rows),
        ).fetchall():
            for (allocation,) in rows:
                yield allocation
            after = rows[-1][0]

    def _verify_identities(self, allocation: str, scope: Scope) -> None:
        for order_id, venue, account, symbol, external_id, client_id in self.db.execute(
            "SELECT order_id,venue,account,symbol,external_id,client_id FROM order_bindings "
            "WHERE allocation=? ORDER BY order_id",
            (allocation,),
        ):
            identifier(external_id)
            if (
                (venue, account, symbol) != (scope.venue, scope.account, scope.symbol)
                or client_id != client_identity(scope, order_id)
                or not self.attempted(scope, order_id)
            ):
                raise LedgerError("stored external order identity differs from attempted intent")
        for venue, account, symbol, external_id, trade_id, payload in self.db.execute(
            "SELECT t.venue,t.account,t.symbol,t.external_id,t.trade_id,f.payload "
            "FROM trade_keys t JOIN fills f ON f.allocation=t.allocation AND f.trade_id=t.trade_id "
            "WHERE t.allocation=? ORDER BY t.trade_id",
            (allocation,),
        ):
            fill = codec.loads(payload, Fill, limit=self.bounds.snapshot_bytes)
            if (
                (venue, account, symbol) != (scope.venue, scope.account, scope.symbol)
                or trade_id != trade_identity(scope, external_id)
                or fill.scope != scope
                or fill.trade_id != trade_id
                or self.binding(scope, fill.order_id) is None
            ):
                raise LedgerError("stored external trade identity differs from posted fill")

    def _text(self, value: object) -> str:
        return codec.dumps(value, limit=self.bounds.snapshot_bytes)

    def _load(self, allocation: str) -> tuple[State, Context, int, bool]:
        row = self.db.execute(
            "SELECT scope,state,context,version,recovery FROM allocations WHERE id=?", (allocation,)
        ).fetchone()
        if row is None:
            raise LedgerError("unknown allocation")
        state = codec.loads(row[1], State, limit=self.bounds.snapshot_bytes)
        context = codec.loads(row[2], Context, limit=self.bounds.snapshot_bytes)
        if codec.digest(state.scope) != allocation or row[0] != self._text(state.scope):
            raise LedgerError("stored allocation identity mismatch")
        self._validate(state, context)
        return state, context, int(row[3]), bool(row[4])

    @staticmethod
    def _validate(state: State, context: Context) -> None:
        if state.scope.symbol != context.market.symbol or any(
            money.currency in (context.market.base, context.market.quote)
            for money in state.fee_balances
        ):
            raise LedgerError("allocation currency/scope mismatch")
        if (
            len(state.orders) > context.policy.max_order_records
            or len(state.fills) > context.policy.max_fill_records
        ):
            raise LedgerError("stored state exceeds history bounds")
        if sum(order.side is Side.SELL for order in state.working) > 1:
            raise LedgerError("multiple unresolved SELLs")

    def snapshot(self, scope: Scope) -> Outcome:
        state, _, version, _ = self._load(codec.digest(scope))
        return Outcome(state, version, True)

    def lookup(self, request_id: str) -> Outcome | None:
        identifier(request_id)
        row = self.db.execute("SELECT result FROM requests WHERE id=?", (request_id,)).fetchone()
        return (
            None if row is None else codec.loads(row[0], Outcome, limit=self.bounds.snapshot_bytes)
        )

    def _request(
        self,
        request_id: str,
        payload: object,
        label: str,
        allocation: str | None,
        action: Callable[[], Outcome],
    ) -> Outcome:
        identifier(request_id)
        encoded = self._text(payload)
        fingerprint = codec.digest(payload)
        self.storage.checkpoint()
        with self.storage.transaction(label):
            previous = self.db.execute(
                "SELECT fingerprint,result FROM requests WHERE id=?", (request_id,)
            ).fetchone()
            if previous is not None:
                if previous[0] != fingerprint:
                    raise Conflict("request ID reused with different inputs")
                return codec.loads(previous[1], Outcome, limit=self.bounds.snapshot_bytes)
            self.db.execute(
                "INSERT INTO requests VALUES (?,?,?)", (request_id, fingerprint, "pending")
            )
            outcome = action()
            self.db.execute(
                "UPDATE requests SET result=? WHERE id=?", (self._text(outcome), request_id)
            )
            self.db.execute(
                "INSERT INTO events(request_id,allocation,payload,accepted) VALUES (?,?,?,?)",
                (request_id, allocation, encoded, int(outcome.accepted)),
            )
            self.storage.fault(f"{label}:after_posting")
        return outcome

    def pool(self, venue: str, account: str, currency: str) -> Decimal:
        for part in (venue, account, currency):
            identifier(part)
        row = self.db.execute(
            "SELECT amount FROM pools WHERE venue=? AND account=? AND currency=?",
            (venue, account, currency),
        ).fetchone()
        return ZERO if row is None else codec.parse_amount(row[0])

    @exact
    def _pool_delta(self, request_id: str, venue: str, account: str, money: Money) -> None:
        after = self.pool(venue, account, money.currency) + money.amount
        decimal(after)
        self.db.execute(
            "INSERT INTO pools VALUES (?,?,?,?) ON CONFLICT(venue,account,currency) "
            "DO UPDATE SET amount=excluded.amount",
            (venue, account, money.currency, codec.amount(after)),
        )
        self._journal(
            request_id, codec.digest((venue, account)), money.currency, "unassigned", money.amount
        )

    def _journal(self, request_id: str, key: str, currency: str, kind: str, delta: Decimal) -> None:
        self.db.execute(
            "INSERT INTO journal VALUES (?,?,?,?,?)",
            (request_id, key, currency, kind, codec.amount(delta)),
        )

    def credit(self, request_id: str, venue: str, account: str, money: Money) -> Outcome:
        """Explicit synthetic dedicated-funds evidence, never a copied exchange balance."""
        decimal(money.amount, positive=True)

        def action() -> Outcome:
            self._pool_delta(request_id, venue, account, money)
            return Outcome(None, None, True)

        return self._request(request_id, ("credit", venue, account, money), "credit", None, action)

    def start(
        self, request_id: str, scope: Scope, funds: tuple[Money, ...], context: Context
    ) -> Outcome:
        """Assignment and seed reservation share one transaction, including all fee currencies."""
        allocation = codec.digest(scope)
        if (
            not isinstance(funds, tuple)
            or not funds
            or len({money.currency for money in funds}) != len(funds)
        ):
            raise LedgerError("explicit unique currency assignments required")
        for money in funds:
            decimal(money.amount, positive=True)
            if money.currency == context.market.base:
                raise LedgerError("base assignment requires lot provenance, outside I2 funding API")

        @exact
        def action() -> Outcome:
            if self.db.execute("SELECT 1 FROM allocations WHERE id=?", (allocation,)).fetchone():
                raise Conflict("allocation already exists")
            for money in funds:
                self._pool_delta(
                    request_id, scope.venue, scope.account, Money(money.currency, -money.amount)
                )
            state = State(
                scope,
                next(
                    (money.amount for money in funds if money.currency == context.market.quote),
                    ZERO,
                ),
                tuple(money for money in funds if money.currency != context.market.quote),
                context.config_revision,
                context.market.generation,
            )
            transition = reduce(state, Start(scope), context)
            if not transition.accepted or not any(
                isinstance(command, PlaceLimit) for command in transition.commands
            ):
                raise LedgerError("assignment refused: no valid seed reservation")
            self.db.execute(
                "INSERT INTO allocations VALUES (?,?,?,?,1,0)",
                (allocation, self._text(scope), self._text(state), self._text(context)),
            )
            self._changes(request_id, allocation, None, transition.state, context)
            self._save(allocation, transition.state, context, 1, recovery=False)
            intents = self._commands(allocation, transition.commands, 1)
            return Outcome(transition.state, 1, True, intents, transition.notices)

        return self._request(
            request_id, ("start", scope, funds, context), "start", allocation, action
        )

    @exact
    def _changes(
        self, request_id: str, allocation: str, before: State | None, after: State, context: Context
    ) -> None:
        old = {} if before is None else balances(before, context)
        new = balances(after, context)
        for currency in sorted(old.keys() | new.keys()):
            self._journal(
                request_id,
                allocation,
                currency,
                "balance",
                new.get(currency, ZERO) - old.get(currency, ZERO),
            )
        self._journal(
            request_id,
            allocation,
            context.market.quote,
            "cost",
            after.position_cost - (ZERO if before is None else before.position_cost),
        )
        self._journal(
            request_id,
            allocation,
            context.market.quote,
            "realized",
            after.realized_pnl - (ZERO if before is None else before.realized_pnl),
        )

    def _save(
        self, allocation: str, state: State, context: Context, version: int, *, recovery: bool
    ) -> None:
        self._validate(state, context)
        self.db.execute(
            "UPDATE allocations SET state=?,context=?,version=?,recovery=? WHERE id=?",
            (self._text(state), self._text(context), version, int(recovery), allocation),
        )
        # Resolve an old slot before inserting its replacement.
        for order in sorted(state.orders, key=lambda item: not item.terminal):
            self.db.execute(
                "INSERT INTO orders VALUES (?,?,?,?,?) ON CONFLICT(allocation,id) "
                "DO UPDATE SET unresolved=excluded.unresolved,payload=excluded.payload",
                (allocation, order.id, str(order.side), int(not order.terminal), self._text(order)),
            )
            if order.terminal or (order.acknowledged and not order.unknown):
                self.db.execute(
                    "UPDATE intents SET status='resolved' WHERE allocation=? AND order_id=? "
                    "AND (kind='place' OR ?)",
                    (allocation, order.id, int(order.terminal)),
                )
        self.db.execute("DELETE FROM reservations WHERE allocation=?", (allocation,))
        self.db.executemany(
            "INSERT INTO reservations VALUES (?,?,?,?,?)",
            ((allocation, *row) for row in reservation_rows(state)),
        )
        self.db.execute("DELETE FROM balances WHERE allocation=?", (allocation,))
        self.db.executemany(
            "INSERT INTO balances VALUES (?,?,?)",
            (
                (allocation, currency, codec.amount(value))
                for currency, value in balances(state, context).items()
            ),
        )
        self.db.execute(
            "INSERT INTO positions VALUES (?,?,?,?) ON CONFLICT(allocation) DO UPDATE SET "
            "quantity=excluded.quantity,cost=excluded.cost,realized=excluded.realized",
            (
                allocation,
                codec.amount(state.owned_base),
                codec.amount(state.position_cost),
                codec.amount(state.realized_pnl),
            ),
        )

    def _commands(
        self, allocation: str, commands: tuple[Command, ...], version: int
    ) -> tuple[str, ...]:
        result = []
        for command in commands:
            kind = (
                "place"
                if isinstance(command, PlaceLimit)
                else "cancel"
                if isinstance(command, Cancel)
                else "reconcile"
            )
            order_id = (
                command.order.id
                if isinstance(command, PlaceLimit)
                else command.order_id
                if isinstance(command, Cancel)
                else None
            )
            key = codec.digest((allocation, kind, order_id, version if order_id is None else None))
            self.db.execute(
                "INSERT INTO intents VALUES (?,?,?,?,?,'queued',NULL) ON CONFLICT(id) DO NOTHING",
                (key, allocation, order_id, kind, self._text(command)),
            )
            result.append(key)
        return tuple(result)

    def _versioned(self, scope: Scope, expected: int) -> tuple[str, State, Context, int, bool]:
        allocation = codec.digest(scope)
        state, context, version, recovery = self._load(allocation)
        if type(expected) is not int or version != expected:
            raise Conflict("stale allocation version")
        return allocation, state, context, version, recovery

    def release(self, request_id: str, scope: Scope, expected: int, money: Money) -> Outcome:
        decimal(money.amount, positive=True)

        @exact
        def action() -> Outcome:
            allocation, state, context, version, recovery = self._versioned(scope, expected)
            if recovery or state.reconcile_reason is not None:
                raise LedgerError("cannot release funds before reconciliation")
            owned = balances(state, context).get(money.currency, ZERO)
            if (
                money.currency == context.market.base
                or owned - reserved(state, money.currency) < money.amount
            ):
                raise LedgerError("release would consume reserved funds or inventory")
            updated = (
                replace(state, quote_cash=state.quote_cash - money.amount)
                if money.currency == context.market.quote
                else replace(
                    state,
                    fee_balances=tuple(
                        replace(item, amount=item.amount - money.amount)
                        if item.currency == money.currency
                        else item
                        for item in state.fee_balances
                    ),
                )
            )
            self._pool_delta(request_id, scope.venue, scope.account, money)
            self._changes(request_id, allocation, state, updated, context)
            self._save(allocation, updated, context, version + 1, recovery=False)
            return Outcome(updated, version + 1, True)

        return self._request(
            request_id, ("release", scope, expected, money), "release", codec.digest(scope), action
        )

    @staticmethod
    def _block(state: State, reason: str) -> Transition:
        return Transition(
            replace(
                state,
                reconcile_reason=reason,
                reconcile_revision=state.reconcile_revision + int(state.reconcile_reason != reason),
            ),
            (Reconcile(state.scope, reason),),
            (reason,),
            False,
        )

    def _transition(
        self, allocation: str, state: State, event: Event, context: Context
    ) -> Transition:
        if isinstance(event, Fill):
            row = self.db.execute(
                "SELECT payload FROM fills WHERE allocation=? AND trade_id=?",
                (allocation, event.trade_id),
            ).fetchone()
            if row is not None:
                return (
                    Transition(state)
                    if row[0] == self._text(event)
                    else self._block(state, "conflicting duplicate fill")
                )
        if isinstance(event, (OrderReport, CancelAcknowledged, SubmissionUnknown)) and not any(
            order.id == event.order_id for order in state.orders
        ):
            row = self.db.execute(
                "SELECT payload FROM orders WHERE allocation=? AND id=?",
                (allocation, event.order_id),
            ).fetchone()
            if row is not None:
                order = codec.loads(row[0], Order)
                if order.terminal:
                    if (
                        not isinstance(event, OrderReport)
                        or event.status == "open"
                        or (
                            event.cumulative == order.filled
                            and (event.status != "filled" or event.cumulative == order.amount)
                            and (event.status != "rejected" or event.cumulative == ZERO)
                        )
                    ):
                        return Transition(state)
                    return self._block(state, "conflicting archived order report")
        return reduce(state, event, context)

    def apply(
        self,
        request_id: str,
        expected: int,
        event: Event,
        context: Context,
        *,
        external: tuple[str, str] | None = None,
    ) -> Outcome:
        allocation = codec.digest(event.scope)

        def action() -> Outcome:
            _, state, previous_context, version, recovery = self._versioned(event.scope, expected)
            if (previous_context.market.base, previous_context.market.quote) != (
                context.market.base,
                context.market.quote,
            ):
                raise LedgerError("allocation currencies cannot change")
            if external is not None:
                if not isinstance(event, Fill):
                    raise LedgerError("external trade identity requires a fill")
                external_order, external_trade = external
                if (
                    self.binding(event.scope, event.order_id) != external_order
                    or trade_identity(event.scope, external_trade) != event.trade_id
                ):
                    raise Conflict("external fill does not match its durable binding")
                identity = self.db.execute(
                    "SELECT allocation,trade_id FROM trade_keys "
                    "WHERE venue=? AND account=? AND symbol=? AND external_id=?",
                    (event.scope.venue, event.scope.account, event.scope.symbol, external_trade),
                ).fetchone()
                if identity is not None and identity != (allocation, event.trade_id):
                    raise Conflict("external trade was already assigned to another allocation")
            transition = self._transition(allocation, state, event, context)
            after = transition.state
            if (
                isinstance(event, Fill)
                and transition.accepted
                and any(fill.trade_id == event.trade_id for fill in after.fills)
            ):
                existing = self.db.execute(
                    "SELECT 1 FROM fills WHERE allocation=? AND trade_id=?",
                    (allocation, event.trade_id),
                ).fetchone()
                if existing is None:
                    self.db.execute(
                        "INSERT INTO fills VALUES (?,?,?,?)",
                        (allocation, event.trade_id, event.order_id, self._text(event)),
                    )
                    self.db.executemany(
                        "INSERT INTO fees VALUES (?,?,?,?,?,?)",
                        (
                            (
                                allocation,
                                event.trade_id,
                                index,
                                fee.currency,
                                codec.amount(fee.amount),
                                None if fee.quote_value is None else codec.amount(fee.quote_value),
                            )
                            for index, fee in enumerate(event.fees or ())
                        ),
                    )
                    if external is not None:
                        self.db.execute(
                            "INSERT INTO trade_keys VALUES (?,?,?,?,?,?)",
                            (
                                event.scope.venue,
                                event.scope.account,
                                event.scope.symbol,
                                external[1],
                                allocation,
                                event.trade_id,
                            ),
                        )
            if (
                isinstance(event, Reconciled)
                and transition.accepted
                and after.reconcile_reason is None
            ):
                recovery = False
                self.db.execute(
                    "UPDATE intents SET status='resolved' WHERE allocation=? AND kind='reconcile'",
                    (allocation,),
                )
            self._changes(request_id, allocation, state, after, context)
            self._save(allocation, after, context, version + 1, recovery=recovery)
            intents = self._commands(allocation, transition.commands, version + 1)
            return Outcome(after, version + 1, transition.accepted, intents, transition.notices)

        payload = ("event", expected, event, context)
        return self._request(
            request_id,
            payload if external is None else (*payload, external),
            "event",
            allocation,
            action,
        )

    def order(self, scope: Scope, order_id: int) -> Order:
        row = self.db.execute(
            "SELECT payload FROM orders WHERE allocation=? AND id=?",
            (codec.digest(scope), order_id),
        ).fetchone()
        if row is None:
            raise LedgerError("unknown internal order")
        return codec.loads(row[0], Order, limit=self.bounds.snapshot_bytes)

    def attempted(self, scope: Scope, order_id: int) -> bool:
        return (
            self.db.execute(
                "SELECT 1 FROM intents WHERE allocation=? AND order_id=? "
                "AND kind='place' AND attempt_run IS NOT NULL",
                (codec.digest(scope), order_id),
            ).fetchone()
            is not None
        )

    def revoke_unsent(
        self,
        request_id: str,
        scope: Scope,
        expected: int,
        order_id: int,
        context: Context,
    ) -> Outcome:
        """Sole-thread proof that local cancellation has no external side effect."""
        if self.attempted(scope, order_id):
            raise Conflict("attempted order requires external reconciliation")
        return self.apply(
            request_id, expected, OrderReport(scope, order_id, "canceled", ZERO, True), context
        )

    def binding(self, scope: Scope, order_id: int) -> str | None:
        row = self.db.execute(
            "SELECT external_id FROM order_bindings WHERE allocation=? AND order_id=?",
            (codec.digest(scope), order_id),
        ).fetchone()
        return None if row is None else str(row[0])

    def bind(
        self,
        request_id: str,
        scope: Scope,
        order_id: int,
        external_id: str,
        client_id: str,
    ) -> Outcome:
        for value in (external_id, client_id):
            identifier(value)
        allocation = codec.digest(scope)

        def action() -> Outcome:
            if client_id != client_identity(scope, order_id) or not self.attempted(scope, order_id):
                raise Conflict("binding lacks an attempted matching client identity")
            values = (
                allocation,
                order_id,
                scope.venue,
                scope.account,
                scope.symbol,
                external_id,
                client_id,
            )
            previous = self.db.execute(
                "SELECT * FROM order_bindings WHERE allocation=? AND order_id=? "
                "OR venue=? AND account=? AND symbol=? AND external_id=?",
                (allocation, order_id, scope.venue, scope.account, scope.symbol, external_id),
            ).fetchall()
            if previous and previous != [values]:
                raise Conflict("conflicting external order binding")
            if not previous:
                self.db.execute("INSERT INTO order_bindings VALUES (?,?,?,?,?,?,?)", values)
            return self.snapshot(scope)

        return self._request(
            request_id,
            ("bind", scope, order_id, external_id, client_id),
            "bind",
            allocation,
            action,
        )

    def hold(self, request_id: str, scope: Scope, reason: str) -> Outcome:
        identifier(reason)
        allocation = codec.digest(scope)

        def action() -> Outcome:
            state, context, version, _ = self._load(allocation)
            after = self._block(state, reason).state
            self._save(allocation, after, context, version + 1, recovery=True)
            intents = self._commands(allocation, (Reconcile(scope, reason),), version + 1)
            return Outcome(after, version + 1, True, intents)

        return self._request(request_id, ("hold", scope, reason), "hold", allocation, action)

    def attempt_once(
        self,
        request_id: str,
        scope: Scope,
        expected: int,
        intent_id: str,
        admission: BuyAdmission | None = None,
    ) -> Outcome:
        """Only a fresh committed claim can grant a send, never an old receipt.

        Both calls run in the sole writer thread without yielding. A lost commit
        acknowledgement leaves uncertainty; the caller must reconcile, not send.
        """
        if self.lookup(request_id) is not None:
            raise Conflict("attempt receipt replay cannot authorize another send")
        return self.attempt(request_id, scope, expected, intent_id, admission)

    def intents(self, scope: Scope, *, after: str = "") -> tuple[Intent, ...]:
        rows = self.db.execute(
            "SELECT id,payload,status FROM intents WHERE allocation=? AND id>? "
            "AND status!='resolved' ORDER BY id LIMIT ?",
            (codec.digest(scope), after, self.bounds.read_rows),
        ).fetchall()
        return tuple(Intent(*row) for row in rows)

    def attempt(
        self,
        request_id: str,
        scope: Scope,
        expected: int,
        intent_id: str,
        admission: BuyAdmission | None = None,
    ) -> Outcome:
        """Mark a mock send boundary uncertain BEFORE any fake external effect.

        Replaying this request returns its receipt, never new send permission.
        There is intentionally no sender in this package.
        """
        allocation = codec.digest(scope)

        def action() -> Outcome:
            _, state, context, version, recovery = self._versioned(scope, expected)
            if recovery or state.reconcile_reason is not None:
                raise LedgerError("intent blocked pending reconciliation")
            row = self.db.execute(
                "SELECT order_id,kind,status FROM intents WHERE id=? AND allocation=?",
                (intent_id, allocation),
            ).fetchone()
            if row is None or row[1] == "reconcile" or row[2] != "queued":
                raise LedgerError("intent is not eligible for a first mock attempt")
            order = next((order for order in state.working if order.id == row[0]), None)
            if order is None:
                raise LedgerError("intent order is no longer working")
            if row[1] == "place" and order.side is Side.BUY:
                universe_row = self.db.execute(
                    "SELECT payload FROM universe_revisions ORDER BY revision DESC LIMIT 1"
                ).fetchone()
                if universe_row is not None:
                    universe = codec.loads(
                        universe_row[0], UniverseSnapshot, limit=self.bounds.snapshot_bytes
                    )
                    if (
                        admission is None
                        or admission.scope != scope
                        or admission.universe_revision != universe.revision
                        or not universe.observed_ms <= admission.now_ms <= admission.expires_ms
                        or state.mode is not Mode.ACTIVE
                        or not any(m.scope == scope and m.selected for m in universe.members)
                        or admission.context_digest
                        != codec.digest(
                            (
                                context.market,
                                context.policy,
                                context.buy_fee,
                                context.sell_fee,
                                context.config_revision,
                            )
                        )
                    ):
                        raise LedgerError("fresh current universe admission required")
            transition = reduce(state, SubmissionUnknown(scope, order.id), context)
            self.db.execute(
                "UPDATE intents SET status='uncertain',attempt_run=? WHERE id=?",
                (self.storage.run_id, intent_id),
            )
            self._save(allocation, transition.state, context, version + 1, recovery=False)
            intents = self._commands(allocation, transition.commands, version + 1)
            return Outcome(transition.state, version + 1, True, intents, transition.notices)

        payload = ("attempt", scope, expected, intent_id)
        return self._request(
            request_id,
            payload if admission is None else (*payload, admission),
            "attempt",
            allocation,
            action,
        )

    def compact(self, request_id: str, scope: Scope, expected: int) -> Outcome:
        allocation = codec.digest(scope)

        def action() -> Outcome:
            _, state, context, version, recovery = self._versioned(scope, expected)
            after = compact(state, context)
            self._save(allocation, after, context, version + 1, recovery=recovery)
            watermark = int(
                self.db.execute("SELECT coalesce(max(seq),0) FROM events").fetchone()[0]
            )
            self.db.execute(
                "INSERT INTO checkpoints VALUES (?,?,?,?) ON CONFLICT(allocation) DO UPDATE SET "
                "version=excluded.version,watermark=excluded.watermark,snapshot_hash=excluded.snapshot_hash",
                (allocation, version + 1, watermark, codec.digest(after)),
            )
            return Outcome(after, version + 1, True)

        result = self._request(
            request_id, ("compact", scope, expected), "compact", allocation, action
        )
        self.storage.checkpoint()
        return result

    def _verify_projection(self, allocation: str, state: State, context: Context) -> None:
        stored_balances = {
            row[0]: codec.parse_amount(row[1])
            for row in self.db.execute(
                "SELECT currency,amount FROM balances WHERE allocation=?", (allocation,)
            )
        }
        position = self.db.execute(
            "SELECT quantity,cost,realized FROM positions WHERE allocation=?", (allocation,)
        ).fetchone()
        if stored_balances != balances(state, context) or position != (
            codec.amount(state.owned_base),
            codec.amount(state.position_cost),
            codec.amount(state.realized_pnl),
        ):
            raise LedgerError("financial projection differs from snapshot")
        rows = tuple(
            self.db.execute(
                "SELECT order_id,currency,principal,fee FROM reservations "
                "WHERE allocation=? ORDER BY order_id,currency",
                (allocation,),
            )
        )
        if rows != reservation_rows(state):
            raise LedgerError("reservation projection differs from snapshot")
        for order in state.orders:
            row = self.db.execute(
                "SELECT side,unresolved,payload FROM orders WHERE allocation=? AND id=?",
                (allocation, order.id),
            ).fetchone()
            if row != (str(order.side), int(not order.terminal), self._text(order)):
                raise LedgerError("order projection differs from snapshot")
        for fill in state.fills:
            row = self.db.execute(
                "SELECT payload FROM fills WHERE allocation=? AND trade_id=?",
                (allocation, fill.trade_id),
            ).fetchone()
            if row != (self._text(fill),):
                raise LedgerError("fill projection differs from snapshot")
        working_ids = {
            row[0]
            for row in self.db.execute(
                "SELECT id FROM orders WHERE allocation=? AND unresolved=1", (allocation,)
            )
        }
        maximum = self.db.execute(
            "SELECT coalesce(max(id),0) FROM orders WHERE allocation=?", (allocation,)
        ).fetchone()[0]
        if working_ids != {order.id for order in state.working} or maximum >= state.next_id:
            raise LedgerError("durable order history differs from snapshot")
        for order_id, side, unresolved, payload in self.db.execute(
            "SELECT id,side,unresolved,payload FROM orders WHERE allocation=? ORDER BY id",
            (allocation,),
        ):
            order = codec.loads(payload, Order, limit=self.bounds.snapshot_bytes)
            if (order.id, str(order.side), int(not order.terminal)) != (order_id, side, unresolved):
                raise LedgerError("durable order identity differs from payload")
            self._verify_fills(allocation, state.scope, order, state)
        for currency, value in stored_balances.items():
            if self._journal_total(allocation, currency, "balance") != value:
                raise LedgerError("balance journal differs from projection")
        for kind, value in (("cost", state.position_cost), ("realized", state.realized_pnl)):
            if self._journal_total(allocation, context.market.quote, kind) != value:
                raise LedgerError("position journal differs from projection")

    @exact
    def _journal_total(self, key: str, currency: str, kind: str) -> Decimal:
        total = ZERO
        for (delta,) in self.db.execute(
            "SELECT delta FROM journal WHERE account_key=? AND currency=? "
            "AND kind=? ORDER BY rowid",
            (key, currency, kind),
        ):
            total += codec.parse_amount(delta)
        return total

    @exact
    def _verify_fills(self, allocation: str, scope: Scope, order: Order, state: State) -> None:
        quantity = gross = ZERO
        retained = {fill.trade_id: fill for fill in state.fills if fill.order_id == order.id}
        count = 0
        for trade_id, payload in self.db.execute(
            "SELECT trade_id,payload FROM fills WHERE allocation=? AND order_id=? "
            "ORDER BY trade_id",
            (allocation, order.id),
        ):
            fill = codec.loads(payload, Fill, limit=self.bounds.snapshot_bytes)
            if (
                fill.scope != scope
                or fill.trade_id != trade_id
                or fill.order_id != order.id
                or fill.fees is None
            ):
                raise LedgerError("durable fill identity/fees mismatch")
            expected = tuple(
                (
                    index,
                    fee.currency,
                    codec.amount(fee.amount),
                    None if fee.quote_value is None else codec.amount(fee.quote_value),
                )
                for index, fee in enumerate(fill.fees)
            )
            observed = tuple(
                self.db.execute(
                    "SELECT ordinal,currency,amount,quote_value FROM fees "
                    "WHERE allocation=? AND trade_id=? ORDER BY ordinal",
                    (allocation, trade_id),
                )
            )
            if expected != observed:
                raise LedgerError("fee projection differs from durable fill")
            if not order.terminal and retained.get(trade_id) != fill:
                raise LedgerError("working-order fee facts missing from snapshot")
            quantity += fill.amount
            gross += fill.amount * fill.price
            count += 1
        if (
            quantity != order.filled
            or gross != order.gross_cost
            or (not order.terminal and count != len(retained))
        ):
            raise LedgerError("durable fills differ from order totals")

    def backup(self, path: Path) -> Backup:
        return self.storage.backup(path)

    def close(self) -> None:
        self.storage.close()
