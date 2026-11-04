# I6 dynamic universe and safe hot reconfiguration

Approved scope: synthetic facts, temporary mock ledgers and local validation.
This implementation provides library APIs, not an operator trading service.
No exchange access, credentials, order endpoint, transfer or deployment is added.
The I1 minimum-lot/fee/one-SELL invariants and existing financial ownership remain
authoritative. No new dependency or change to the imported specifications.

## Selection and financial authority

`grid.universe.model` defines immutable, scoped policies and observations.
`MarketKey` includes venue, account and unified spot symbol. The candidate roster
is the mock executor's fixed set of explicitly funded allocations, at most 1,000
and one per account/symbol. Selection never creates funds, redistributes budgets,
releases reservations or removes an allocation. Adding another funded allocation
to a running roster is deliberately refused; that needs an explicit future
ownership-transfer/registration workflow. Discovery alone cannot authorize funds.

Manual mode considers the allow list. Automatic mode considers the configured
roster. Hybrid mode adds pin priority to automatic selection. Every mode applies
enabled venues, deny, verified unleveraged spot identity, quote identity, reviewed
account/rules/fee evidence, current risk controls, finite funds, minimum executable
BUY and sellability, fee-token funding, source freshness, spread and visible depth.
Deny wins over pin. Naming filters can reject suspicious products but cannot
prove a product unleveraged. Evidence flags are synthetic assertions, not a
replacement for the outstanding real-account/market verification gates.

Eligibility uses both UTC source timestamps and monotonic receipt age. Rules,
fees and books obey the smaller of universe TTL and strategy maximum source age.
The book must match the current market generation and the strategy's exact
top-of-book reference. Depth has at most 100 strictly sorted levels per side.
Visible depth within `depth_deviation` on both sides must cover the minimum BUY
amount times `depth_multiple`. This is a filter, not a fill or liquidity guarantee.

Among eligible candidates, ranking is deterministic:

1. Hybrid pins precede other candidates.
2. Selected incumbents still within `residency_ms` precede nonresidents.
3. Lower estimated cost fraction wins: `(ask - bid) / bid` plus conservative
   BUY and prospective SELL fee cost divided by BUY notional. Incumbents receive
   an additive `hysteresis` discount to reduce marginal switches.
4. Higher minimum two-sided depth/lot ratio wins, then scoped market key breaks ties.

All arithmetic uses the existing isolated Decimal context. The score is a cost
filter, not expected profit, historical return, price prediction or a sizing rule.
Pins can displace residents but never bypass hard eligibility. Hard invalidation
and deny take effect immediately, regardless of residency. Existing reservations
remain owned; eligibility checks committed/open-order limits, and the reducer
and ledger still enforce exact admission of each additional order.

## One writer and the send boundary

`UniverseController.observe`, `invalidate`, `configure` and BUY admission operate
on the existing `AsyncWriter` lane. The controller has no background task, extra
DB connection or lock held over I/O. A policy update uses expected revision,
request ID, actor and reason. Repeated identical requests return their committed
receipt; stale revisions and conflicting reused IDs fail. Regressing clocks fail.

The schema-v3 `universe_revisions` table stores policy, selected and supervised
members, residency timestamps and ownership of retirement controls. The same
transaction applies allocation controls, financial projections/reservations,
intent changes and the request/event audit. No observer can see a committed
policy change without its corresponding retirement effects. Schema v1 and v2
definitions/checksums remain unchanged. Backup semantic hashes cover the new
table; old v1/v2 backups are accepted and migrate additively on opening.

Excluded allocations retain supervision. A zero-fill, non-unknown BUY proven
never attempted can be revoked locally. A possibly sent BUY retains its identity,
reservation and uncertainty until reconciliation proves what happened. Known
working BUYs receive the existing cancellation flow. A cancel acknowledgement
alone is never terminal evidence. Existing SELLs and late fills remain managed;
retirement is not liquidation. A paused allocation may retire to cancel exposure,
but this controller cannot auto-resume it. External retirement and portfolio-exit
controls cannot be overridden by selection. Only retirement initiated by this
universe revision, with its control revision still unchanged, may be readmitted.
An empty canceled seed still needs the existing explicit new-round decision.

The executor checks current facts, eligibility, scope selection, mode, strategy
context and order generations inside its writer operation immediately before
the durable first-send claim. A `BuyAdmission` receipt records universe/facts
revisions, evidence ID, source expiry and context digest in the attempt audit.
The ledger independently requires this receipt whenever a universe policy exists;
removing the executor callback cannot bypass the requirement. This is an internal
trusted-process contract, not a security boundary against arbitrary Python code.

A change committed before the claim blocks the stale BUY. Once a claim is
committed, the order is already uncertain: a later disable cannot retract a send
that may occur. The test suite exercises a disable during the fake gateway wait;
configuration proceeds and ownership stays unresolved. Reconciliation, rather
than resubmission or inferred cancellation, resolves this interval. Existing
executor serialization remains; universe changes do not acquire its I/O lock.

Restart restores policy and all supervised scopes, including retired ones.
Financial recovery and fresh synthetic facts are required before new BUYs.
No facts cache or admission permission survives restart. A controller with an
incomplete persisted roster refuses observation/status/admission. The ledger also
checks that persisted membership refers to an existing financial allocation.

## Cache ownership and resource bounds

| Data | Owner/key | Capacity and lifetime | Invalidation / overload |
| --- | --- | --- | --- |
| Public clients | Existing `ClientPool`, venue/public | At most seven long-lived clients; I3 retains eight configured symbols/client | One close per client; no client per symbol |
| Normalized markets | `Discovery`, venue | At most seven maps, each bounded by the I3 configured symbol list; explicit TTL and generation | Refresh calls underlying reload; venue epoch invalidates old in-flight results |
| Books | `Discovery`, venue/symbol | LRU 1–1,000 entries; at most 100 levels/side through existing adapter | Source/receipt TTL and metadata generation must match; no re-stamping cached data |
| Refresh tasks | `Discovery`, venue | One in-flight operation per venue, 1–1,000 shared waiters; no extra queue | Same key shares work; different key or excess waiter fails BUSY |
| Read budget | `Discovery`, venue | 1–32 requests per bounded window and positive minimum interval | Exhaustion fails immediately; no hidden retry/sleep loop |
| Admission facts | `UniverseController`, allocation scope | LRU no larger than constructor and policy cap, up to 1,000; explicit facts revision and TTL | Eviction/invalidation/expiry blocks BUY; per-roster revision tombstones reject older responses |
| Durable control | Existing ledger, policy revision and allocation | Append-only policy/audit history on disk; one latest bounded snapshot loaded | Writer queue/backpressure/WAL bounds remain authoritative |

`invalidate(scope)` returns the new revision tombstone. A subsequent observation
must use a strictly greater revision. The bounded revision map remains even when
facts are evicted. Callers propagate fee/rule/account changes by invalidating or
providing a new reviewed `Facts`; public observations alone never certify fees or
account ownership. Discovery and controller APIs are deliberately separate:
there is no continuously polling scheduler or automatic live-facts bridge.

One canceled refresh waiter does not cancel the shared read. Invalidation rejects
old responses even if completion precedes a waiting caller's resumption. Closing
cancels and awaits pending work, then closes the client pool. A metadata refresh
followed immediately by a book miss may hit the minimum-interval budget: callers
must schedule a later bounded attempt. Cache refresh does not bypass rate limits.

Q18 fixtures cover deterministic selection at 10/100/1,000 candidates, small
controller LRU/restart cases, and one/seven synthetic public clients. This is not
a 1,000-symbol CCXT integration, wall-time/RSS capacity benchmark or soak. Ledger
reconfiguration holds one bounded transaction across the supervised roster;
deployment latency at its maximum size remains unmeasured. Audit retention and
any future incremental roster workflow require separate design.

## Rollback and limits

Revert the scoped code/docs commit on a branch, preserving the owner's approvals
and GitHub Free preferences. Do not downgrade a schema-v3 ledger in place or drop
its audit table: older code must refuse it. For a mock rollback use a separately
verified pre-migration backup and the matching old code; keep the v3 original.
There are no deployed ledgers or external actions to undo in I6.

Portfolio NAV/conversion/global liquidation are I7. Authenticated adapters, real
order-event behavior, production storage, resource soak and live readiness remain
unverified. Tests do not certify queue priority, available execution liquidity,
exchange precision behavior or profitability. Hosted CI remains PENDING.
