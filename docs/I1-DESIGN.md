# I1 deterministic strategy contract

This package is pure domain logic for one scoped allocation. It proposes
commands; it cannot send, persist or retry them. All examples in tests are
synthetic. Production values remain unconfigured. The CLI retains LIVE disabled.

## Numerical policy

Money is `Decimal`, never float. Constructors reject nonfinite values, unknown
required constraints and unsupported ranges. General calculations use an
explicit 96-digit Decimal context, independent of the ambient process context.
Financial posting, reservation arithmetic and multiplication back from integer
quanta reject inexact results rather than silently losing digits. Inputs support
up to 96 coefficient digits and adjusted exponents from -96 through 96; not
every combination fits exact posting, so range/precision failures block it.

Grid levels use `A / (1 + g) ** n`, rounded down to the venue's price precision.
Indices are positive, increasing and bounded. Quantized prices must remain
strictly decreasing. Arbitrary positive ticks (including .005 and .25) use exact
integer division. Significant-digit precision handles changes at powers of ten.
The deterministic 96-digit context applies to weighted-price and grid divisions;
cash, base quantities and posted cost never depend on those rounded averages.

At a valid BUY price `p`, the unrounded amount floor is
`max(buy_min_amount, buy_min_cost / p)`. Round up to amount precision, verify every
BUY bound, and prove its immediately preceding amount is invalid. Then check
post-fee sellability separately. Buying 1 with a .001-base fee when SELL minimum
is 1 is ineligible; there is no hidden quantity increase. Plans retain unrounded
bounds, formatted values, estimated fee, rule generation and expected proceeds.

For owned base `Q` and remaining acquisition cost `C`, the aggregate SELL chooses
the largest precision-valid quantity `q` whose base fee reserve also fits `Q`.
One aggregate order must satisfy its amount/cost maxima; the core does not split
it to evade a maximum. The least valid price must satisfy both:

```
p >= (C / Q) * (1 + spacing)
q * p - forecast_nonbase_exit_fee_in_quote - C >= positive_net_target
```

Using the whole `C`, including cost attached to residual dust, is conservative.
Base exit fees reduce `q` and are not deducted twice. Quote fees reduce proceeds;
third-token fees require an explicit quote conversion and separate allocated
token funds. Price is rounded upward. Rounded fees make proceeds discontinuous:
the bounded search skips only prices proven insufficient by the current fee
lower bound, rather than assuming profit is monotonic. No passing price within
the supported search/market bounds means ineligible. Residual base stays owned;
unsellable residual blocks more accumulation and is never silently discarded.

## Fees, reservations and actual posting

`FeeRule` requires currency, nonnegative rate below 1, rounding increment,
explicit conversion when needed, and a maximum number of fee settlements.
The conservative order allowance is `ceil(raw_fee / increment) * increment`
plus `(max_settlements - 1) * increment` for a positive rate. This bounds rounding
each partial execution separately. Future venue normalization must justify that
settlement bound; I1 does not establish one for any exchange. Exceeding the
bound or fee forecast records a valid actual fill, then blocks placement and
requests reconciliation. There is no universal fee rate or assumed rebate.

Reservations retain remaining limit-price principal and unused fee allowance
until proven terminal. Confirmed fees consume only their own currency reserve.
Actual price improvements release quote headroom. Rebates increase actual owned
funds without manufacturing additional fee allowance. New BUYs must fit allocated
cash after quote reserve, acquisition-cost cap, lot and order caps. Third-token
exit funding is checked before a BUY, as well as when proposing its SELL.

Each confirmed BUY adds its net base and actual acquisition cost. Nonbase fees
and rebates are added to cost in quote units using supplied conversion facts.
Each SELL removes actual sold base plus any base fee (minus base rebate).
For a partial sale, allocated basis is `floor((C * consumed_base / Q) /
cost_quantum) * cost_quantum`, computed with exact integer ratios. Consuming all
remaining base consumes all remaining basis. No cost disappears through rounding.
Realized PnL is actual net nonbase proceeds less allocated basis. FIFO lot
provenance retains actual execution prices for the next-round anchor; remaining
accounting cost is allocated proportionally across the aggregate position.

## Events, single SELL and round transitions

`reduce(state, event, context)` returns an immutable `Transition`. Its `accepted`
field indicates whether a normalized fact was accepted; a reconciliation command
can accompany an accepted fill. Rejected facts leave financial state unchanged.
Future I2 must durably deduplicate/post events and reserve all proposed orders
atomically before I4 sends anything. An acknowledgement creates no fill or PnL.

The allocation scope contains venue, account, symbol, allocation, strategy and
portfolio-cycle identity. Local order IDs are monotonic within that scope;
round IDs distinguish rounds. Trade identities are deduplicated within the scope.
Exact duplicate fills are no-ops; conflicting duplicates freeze progression.
Missing fees or third-token conversions block posting until corrected evidence.
Filled quantities alone can establish complete execution; canceled/rejected
remainders need a terminal report, matching posted trades and reconciliation.
A status report received before its trades cannot release the order slot.

The approved partial-seed rule sells sellable inventory immediately. It fixes
the weighted actual seed anchor and admits lower BUYs only after the seed is
terminal. Below-minimum partials remain visible without an invalid SELL.
An unfilled canceled/rejected seed stays waiting; no automatic retry is invented.
The terminal seed grants up to N initial ladder placements. Each ladder order's
first confirmed fill earns one replenishment; subsequent partials do not earn
duplicates. A canceled unfilled ladder order earns none, so status-only updates
cannot move the grid down. Available credits still obey all exposure/cap checks.

A pending/unknown SELL occupies the allocation's only SELL slot. A new BUY fill
may dirty the aggregate target and request cancellation. A cancellation
acknowledgement is not terminal proof. Partial SELL fills during cancellation
consume their actual quantity and cost before a successor is calculated. Bounds
on replacements stop unbounded cancel/reprice loops.

A fully executed aggregate SELL enters DRAINING and cancels remaining seed and
ladder BUYs. Every unresolved BUY must settle before the next round. Late base
becomes the next seed inventory; it never triggers a duplicate seed BUY. An
empty round needs fresh eligible book facts and risk permission for a new seed.

Unknown submission never causes a retry. `Reconciled` is an externally verified
snapshot assertion with current config/market versions, expected blocked reason
and reconciliation revision. An unresolved unknown or unmatched terminal report
rejects it. Old assertions cannot clear a later block. It is not an operator
bypass: future I4 must establish the asserted facts and corrected fee/risk inputs.

Rules, fee data and book facts must be fresh for placements. Generation mismatch
blocks new commands until explicit reconciliation; confirmed facts can still be
posted. PAUSED suppresses new BUYs but retains SELL management. RETIRING also
cancels BUYs while retaining ownership. Portfolio-exit overlays suppress ordinary
grid placement and hand control to the future I7 supervisor; I1 does not liquidate.

## Bounds and intentionally absent integrations

Ladder size, owned/committed lots, quote commitment, open orders, replacements,
grid index, order records and fill records are bounded. Reaching the history cap
requires durable checkpoint/recovery; this pure model does not evict deduplication
evidence or pretend to run indefinitely. Searches are bounded (1,024 quantity
steps, 256 price steps). Reaching a bound yields an explicit rejection.

Immutable tuples favor simple replay/review, with linear scans through bounded
history. No throughput or memory benchmark is claimed. Shared-wallet races,
durable event buffering, checkpointing, crash recovery, controller ownership,
CCXT formatting/payloads, exchange guarantees, portfolio exits and live operation
remain later iterations. None is established by pure reducer tests.
