"""Read-only realized reporting from committed amounts; never authority to trade."""

from decimal import Decimal

from grid.domain.model import Fill, State
from grid.domain.numbers import ZERO, DomainError, arithmetic
from grid.persistence import codec
from grid.persistence.ledger import Ledger


@arithmetic
def realized(wins: Decimal, losses: Decimal, sell_orders: int) -> dict[str, object]:
    return {
        "gross_wins_quote": wins,
        "gross_losses_quote": losses,
        "filled_sell_orders": sell_orders,
        "profit_factor": wins / losses if sell_orders >= 2 and losses > ZERO else None,
        "profit_factor_note": (
            "Sum of positive / absolute negative net realized SELL-fill postings, after fees "
            "and allocated cost. Undefined without >=2 distinct filled SELL orders and positive "
            "gross losses. Partials do not increase the order count; orders need not be complete "
            "or independent. This minimum is not statistical evidence."
        ),
    }


@arithmetic
def posted(ledger: Ledger, states: tuple[State, ...], quote: str) -> dict[str, object]:
    """Stream bounded temporary ledger history; never infer posting from venue hints."""
    reports: list[dict[str, object]] = []
    total_wins = total_losses = total_turnover = total_fees_quote = ZERO
    total_fills = total_sells = 0
    total_sell_orders = 0
    total_fees: dict[str, Decimal] = {}
    for state in states:
        key = codec.digest(state.scope)
        wins = losses = turnover = fees_quote = ZERO
        fees: dict[str, Decimal] = {}
        fills = sells = 0
        sell_orders: set[int] = set()
        for currency, value in ledger.db.execute(
            "SELECT currency,delta FROM journal WHERE account_key=? AND kind='realized' "
            "ORDER BY request_id,currency",
            (key,),
        ):
            if currency != quote:
                raise DomainError("research cannot sum unlike realized currencies")
            delta = codec.parse_amount(value)
            wins += max(ZERO, delta)
            losses += max(ZERO, -delta)
        if wins - losses != state.realized_pnl:
            raise DomainError("research realized journal differs from final state")
        for payload, side in ledger.db.execute(
            "SELECT f.payload,o.side FROM fills f JOIN orders o "
            "ON o.allocation=f.allocation AND o.id=f.order_id "
            "WHERE f.allocation=? ORDER BY f.trade_id",
            (key,),
        ):
            fill = codec.loads(payload, Fill, limit=ledger.bounds.snapshot_bytes)
            if fill.fees is None or any(f.quote_value is None for f in fill.fees):
                raise DomainError("research posted fees require explicit quote values")
            fills += 1
            sells += int(side == "sell")
            if side == "sell":
                sell_orders.add(fill.order_id)
            turnover += fill.amount * fill.price
            for fee in fill.fees:
                fees[fee.currency] = fees.get(fee.currency, ZERO) + fee.amount
                fees_quote += fee.quote_value or ZERO
        reports.append(
            {
                "scope": state.scope,
                "posted_fills": fills,
                "posted_sell_fills": sells,
                "realized_pnl_quote": state.realized_pnl,
                "posted_turnover_quote": turnover,
                "posted_fees_by_currency": fees,
                "posted_fees_quote": fees_quote,
                **realized(wins, losses, len(sell_orders)),
            }
        )
        total_wins += wins
        total_losses += losses
        total_turnover += turnover
        total_fees_quote += fees_quote
        total_fills += fills
        total_sells += sells
        total_sell_orders += len(sell_orders)
        for currency, amount in fees.items():
            total_fees[currency] = total_fees.get(currency, ZERO) + amount
    return {
        "quote": quote,
        "allocations": tuple(reports),
        "posted_fills": total_fills,
        "posted_sell_fills": total_sells,
        "realized_pnl_quote": total_wins - total_losses,
        "posted_turnover_quote": total_turnover,
        "posted_fees_quote": total_fees_quote,
        "posted_fees_by_currency": total_fees,
        **realized(total_wins, total_losses, total_sell_orders),
    }
