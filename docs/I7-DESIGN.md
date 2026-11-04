# I7 portfolio NAV and bounded mock cycle exit

The owner approved `I7-PLAN.md` with local validation. This implementation runs
on synthetic facts, the existing `SyntheticExchange` and temporary mock ledgers.
It adds no exchange access, trading CLI, service or production configuration.
Imported specifications remain unchanged. Actual validation is in `I7-EVIDENCE.md`.

## Ownership and valuation

`grid.portfolio` defines immutable Decimal policies, evidence and cycle records.
`PortfolioController` shares the existing `AsyncWriter` with the mock executor
and universe controller. Financial ownership always comes from committed ledger
state, including reserved money exactly once. The policy must name every
registered allocation, including retired allocations and empty conversion sinks.
Only explicitly listed account pools belong to the cycle. Unassigned account
funds are excluded. There is one allocation per venue/account/symbol, with a
fixed roster of at most 1,000; adding allocations after the first cycle is refused.

Fresh verified account, market, fee and order-book facts are required for every
allocation, even empty ones. UTC and monotonic receipt ages, rule/configuration
generations, ordered uncrossed bounded depth, and current fee conversions are
checked. Unknown orders or startup reconciliation prevent NAV evaluation and
new BUYs. Such uncertainty must be resolved before an initial trigger can latch;
the controller does not guess the NAV of an in-flight unconfirmed execution.

Cash and fee assets use reporting-currency identity or an explicit direct
conversion. There are no inferred stablecoin parities or automatic multi-hop
routes. `Conversion.bid_net` includes executable conversion costs and fees;
`ask_gross` includes liability replacement costs and fees. Both are reviewed
synthetic evidence, not rates fetched by this component. Conversion capacity is
shared across all uses of that account/currency quote in one valuation. Missing,
stale, insufficient or inconsistent fee-token evidence blocks valuation.

Owned base requires bid depth covering the entire position. The lowest consumed
bid, rounded down, values all executable sale chunks conservatively. Amount/cost
maximums split the position into a bounded number of orders; each chunk includes
its separately rounded worst-case settlement fees. Inventory needing more chunks
than the policy's order budget blocks valuation. Base fees reduce sell quantity;
quote and token fees reduce proceeds at conservative conversion rates. Actual
posted fees/rebates are already reflected in holdings and are not subtracted a
second time. Known liabilities are subtracted; BUYs and cycle creation also refuse
unpaid liabilities. Minimum/precision residuals have zero executable NAV.

`Nav` persists evidence identity/revision, marks, quantities, residuals, fee
estimates, source timestamps, conversion proofs and liabilities. The baseline,
trigger and final snapshots persist; ordinary below-threshold evaluations retain
the request's evidence digest and evaluation time, not a full NAV time series.
The latest input packet is disposable and bounded. It is not restored as fresh
evidence after restart. Full raw source packet archival and a monitoring time
series remain operational work.

## Cycle baseline and external cashflows

`begin` is an explicit, revision-checked, idempotent action with actor/reason and
a never-reused cycle identity. It establishes a positive reconciled baseline.
The default policy must explicitly choose `no_transfers`; the alternative
`additive_baseline` is a tested convention, never inferred authorization.

External events first enter as `unclassified`, freezing BUYs, evaluation and
completion. A reviewed `not_external` classification changes no funds.
`adjusted` is allowed only for reporting-currency flows into an explicitly owned
pool under `additive_baseline`: the same transaction posts the pool delta and
baseline adjustment. Withdrawals must fit pool funds and preserve a positive
adjusted baseline. An external event ID cannot be posted twice or reclassified
after resolution. Events before the cycle or in the future are refused. Requests,
classification, identity, evidence and review reasons are audited. Legacy
credit/release/start APIs cannot bypass an unresolved cycle's cashflow controls.
There is no transfer endpoint or automated deposit/withdrawal detector.

Let B be starting NAV, F classified net contributions, and E current NAV.
The trigger is `E >= 1.10 * (B + F)`. This additive capital convention is not a
time-weighted performance metric. The policy is explicit and requires operator
acceptance before any future real use. Cashflow classification can continue
during an exit without clearing the latch; the baseline at the trigger is stored
separately so later flows cannot rewrite that historical threshold proof.

## Latch, claims and cancellation

`evaluate` executes inside the single writer transaction. Threshold equality
latches `exit_requested`, stores trigger NAV/baseline/time, sets every allocation
to portfolio-exit control, and revokes proven-unsent ordinary orders or schedules
their cancellation. The state transitions and command reservations commit
together. No universe change or ordinary Control can clear the latch.

Every BUY claim rechecks complete fresh NAV in the same writer operation that
would claim its send. If the threshold is reached there, it first commits the
global latch and refuses that BUY. The ledger independently requires a current
portfolio receipt; removing the controller callback cannot reopen BUYs. I6
universe admission remains independently required when installed.

Once latched, every ordinary BUY/SELL must reconcile terminal before new exit
SELLs are planned. The existing executor preserves actual late fills, order
identity and ambiguous submissions/cancellations. A cancellation acknowledgement
does not release exposure. A committed send remains uncertain through gateway
I/O; configuration can still execute on the writer while that I/O waits. Existing
executor serialization remains unchanged; no new lock spans network I/O.

## Explicit LIMIT exit policy

Each allocation supplies bid/ask pricing, an absolute price floor, symmetric
maximum drift from its trigger bid, minimum after-fee quote proceeds, order TTL
and maximum order count. The cycle also supplies an overall duration. No
production concessions or fee tiers are invented. The caller must provide a
fee bound that covers the permitted execution style, including possible taker
execution for marketable limits. A marketable limit is still not a fill guarantee.

Only one unresolved SELL may exist per allocation. Each plan reserves base and
any separate fee token before sending. Quantities honor amount/cost maximums,
precision and conservative base-fee coverage. Expiry, fee/rule changes, explicit
cancellation and the overall deadline lead to cancellation/reconciliation before
replacement. Planned orders consume the order budget even if revoked unsent.
Time and count exhaustion never reset themselves. Claim-time checks repeat TTL,
deadline, facts, fee conversion, complete ordinary-order cancellation and global
reconciliation checks. No market-order fallback exists.

`exit_blocked` preserves exposure and a reason; `exiting` retains outstanding
exit orders. Slot reasons identify stale facts, unreachable prices, insufficient
fee funds, residuals and exhausted budgets. These states do not promise future
execution or automatic recovery beyond the explicitly invoked controller APIs.

## Fee/quote conversions and dust

Before the cycle, `register_sink` can explicitly register an empty paused
allocation for an asset/reporting-currency market. Sinks require base/quote fees,
remain in the complete supervised roster, and cannot BUY. When principal orders
are terminal and their inventory is zero or unavoidably sub-minimum, fee assets,
foreign quote cash and owned non-reporting pools can move into a same-account
sink. This is an atomic internal ownership change, not an exchange transfer.

`TransferredLot` records provenance and the transfer-time bid as a bookkeeping
basis. It creates no order, fill or invented execution. Both sides of the movement
are journaled once; a savepoint rolls back a failed pool debit before a planning
error can be recorded as blocked. The sink's eventual sale goes through ordinary
mock order claims and actual fill posting. Historical strategy PnL of a converted
asset is not reconstructed from this marked basis; cycle performance uses full
portfolio cash and actual fees instead. Missing sinks leave conversion assets
visible and block completion. No cross-account or cross-venue movement exists.

Sellable inventory cannot be labeled dust. The residual must fail amount/notional
minimums even at the largest permitted exit price. Explicit maximum amount and
value apply cumulatively per venue/account/currency, plus a portfolio-wide
reporting-value ceiling. The residual quantity and fresh mark remain recorded,
but final executable NAV assigns it zero. Any outstanding order, reconciliation
flag, unpaid liability, unclassified flow or non-reporting cash/fee pool prevents
completion. Fee assets are not silently written off as dust.

## Completion, reporting and restart

`exit_complete` requires reconciled terminal orders and accounted residuals.
The final record keeps trigger NAV separately from final net cash NAV. Its
`final_return` is `final_NAV / (B + F) - 1`. `exit_shortfall` is trigger NAV plus
classified flows since the trigger minus final NAV; a negative shortfall is
possible. Approved residual quantities/marks and current allocation snapshots
show outstanding exposure. Final NAV is net of posted actual fees, not a promise
of the +10% trigger return. The integration fixture triggers at 1,100 on a 1,000
baseline, then exits at 1,050: +5% final return and 50 shortfall.

No automatic next cycle exists. A new explicit `begin` requires the previous
cycle complete, fresh reconciliation/evidence, a new identity and explicit
resume scopes. Empty resumed allocations reset the seed round; other scopes
remain controlled. Neither incomplete exits nor an unknown predecessor can restart.

Schema v4 adds portfolio revision and cashflow projection tables without changing
v1/v2/v3 definitions. Requests/events retain classification history. Semantic
backup hashes include v4 tables; old backups are verified in their old schema and
migrated additively. Startup validates the cycle and complete roster and marks
allocations for reconciliation. Separate backup/restore preserves latches,
transferred inventory and audit evidence. Never downgrade/drop financial history
in place; scoped code rollback uses a separate verified compatible backup.

## Remaining integration limits

No live/private adapter, automatic cashflow detection, exchange execution
certification, public-to-verified-account bridge, portfolio CLI, background
scheduler, operational metrics, maximum-roster latency/RSS measurement or 48-hour
soak is claimed. Direct conversions, predeclared ownership and sinks are explicit
implementation bounds. Limits are enforced but their production hardware costs
remain unmeasured. A stalled venue can hold the global cancellation barrier;
the system favors preserving uncertainty over pretending liquidation completed.
All seven real venues remain HOLD. Hosted CI remains pending under its known
account lock; local evidence and GitHub Free-only publication are separate.
