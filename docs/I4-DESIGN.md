# I4 mock execution, durable identity and recovery

The owner approved this scope on 2026-11-04 with local validation. All effects
are synthetic, and all financial databases exercised by the tests are temporary.
The public I3 transport cannot place or cancel orders. `MockExecutor` accepts
only the concrete in-memory `SyntheticExchange`; no authenticated gateway,
private subscription, daemon, trading CLI or live arming path is implemented.

## Ownership and send boundary

One executor owns an `AsyncWriter`. A second executor on the same writer is
refused. The existing SQLite owner lock excludes a second writer/process.
`service(scope, context)` and `dispatch(scope, context)` serialize all actions;
concurrent callers receive backpressure immediately instead of accumulating
lock waiters. There is no internally spawned retry or polling task.

Every new executor starts without send permission. A complete fake REST pass
is required even for a brand-new, unsent seed. The ledger already holds dedicated
funds and reservations before an intent can reach the executor. Dispatch orders
eligible work as cancellations, then SELL placements, then BUY placements, and
performs at most one external attempt per call. Existing pause, retirement,
draining and exit control states also gate queued placements.

Before the fake effect, `attempt_once` commits the existing I2 attempt protocol:
the durable intent becomes uncertain, its order becomes unknown, and a
reconciliation request is recorded. It refuses any replayed claim receipt.
Requests have fresh IDs, but the durable intent must still be queued: a fresh
request ID cannot make an uncertain intent eligible again. The sole writer
performs receipt lookup and the attempt synchronously without yielding.

A lost commit acknowledgement causes no send. A timeout or caller cancellation
after acceptance keeps uncertainty and revokes in-memory admission. The executor
does not turn exceptions into proof of rejection. An unknown order missing from
even a complete snapshot remains blocked indefinitely; there is no automatic
resubmit or operator bypass. Acknowledgements do not post financial fills.

## LIMIT payload and eligibility

`LimitRequest` has fixed LIMIT type, explicit side, scoped client identity,
canonical decimal-string price/amount and rule/config revisions. It has no
arbitrary params, type override, market helper or derivative account option.
Admission requires matching unleveraged spot identity, fresh book/rules/fees,
verified account-fee evidence, matching fee forecast and unchanged generations.
Normalized precision, quantity and cost bounds are checked again before sending.

A formatter may supply the pinned SDK's price/amount precision conversion.
The boundary compares exact Decimal values with the reserved order and rejects
any numerical change; padded strings may be canonicalized, floats are rejected.
Offline tests use all seven real CCXT formatters and inspect the resulting fake
gateway payload. This verifies the numeric boundary, **not actual private HTTP
serialization or exchange acceptance**. No CCXT order method is invoked.

Bitfinex and XT scope names remain explicitly blocked even with generic evidence:
Bitfinex combines five significant price digits with eight decimal places,
and XT has unrepresented offset/protection rules. The formatter test confirms
Bitfinex rounds significant digits before applying its decimal-place cap. No
actual venue's fees, precision eligibility or client-ID semantics are certified.

## Durable external identities: migration 2

The original migration SQL and checksum remain unchanged. Migration 2 adds:

- `order_bindings`: allocation/internal order to venue/account/symbol/external
  order ID, plus deterministic client identity. External order IDs are unique
  within account/market, client IDs within account. A binding requires a matching
  durable first attempt and cannot be reassigned to another allocation.
- `trade_keys`: actual external trade identity scoped by venue/account/symbol,
  linked to the ledger fill. Its unique claim is committed in the same transaction
  as the fill, actual fees, financial journal and position/reservation updates.

Trade identity deliberately excludes allocation: the same physical trade cannot
be booked into two allocations. The existing fill fingerprint rejects conflicting
duplicates, including a different internal order. Normal replay changes no cash,
fees, inventory or PnL; distinct reconciliation requests still append audit events
and advance allocation versions. Status-only reports never manufacture trades.

Startup checks identities against allocation scope, attempted orders and posted
fills, in addition to existing financial projection/journal checks. Both tables
participate in backup semantic hashes. The upgrader first verifies the exact old
schema and any old backup hash, then adds migration 2 transactionally. A restored
v1 backup is verified before upgrade and receives a matching v2 backup watermark.
Every restored/reopened owner still requires reconciliation.

## Snapshot and private-event discipline

The bounded `Notice` queue accepts scoped order/trade/heartbeat hints with a
reconciliation epoch. Hints revoke admission and schedule truth recovery through
an explicit service call. They do not directly post money. This intentionally
conservative I4 implementation uses REST snapshots to settle every private hint;
direct incremental private trade posting awaits real venue evidence.

The synthetic snapshot contract covers complete order/trade history for an
account and market. It returns a stable snapshot token, echoed epoch, explicit
completion and bounded cursor pages. All pages are checked before financial
posting: no token/epoch changes, repeated cursors, empty continuation pages,
oversized pages or ambiguous completion. Missing attempted orders, previously
bound orders or previously posted trades block recovery. An empty complete
snapshot is sufficient only for a scope with no attempted external obligations.

External orders must match the saved client identity, side, quantity, price and
generations. Foreign orders are refused rather than adopted. This mock profile
admits one allocation per venue/account/symbol; parallel allocations on one pair
require a later reviewed routing contract. Fake snapshots expose foreign
allocations on that account/market instead of silently filtering them out.

Trades require matching orders, actual identities, explicit fee entries (including
an explicit zero fee), and synthetic sequence numbers. Conflicting duplicates,
missing fee currencies/conversions and cumulative totals inconsistent with actual
trades cannot authorize admission. Actual trades post in synthetic execution
sequence before terminal reports. Sequence numbers and complete-history snapshots
are a **fake contract**, not an assertion about any real exchange's API.

Valid actual fees exceeding the forecast are booked, including token liability,
then block admission. Repeated recovery cannot erase that breach. Missing facts
are not replaced with zero fees. Positive and negative actual fees remain signed.

## Cancel races and supervision

A cancel acknowledgement leaves both the old order and cancel intent unresolved.
Even an OPEN REST report cannot make that cancellation retryable. Only reconciled
terminal status and matching actual trades release its reservation and SELL slot.
A fill during cancellation posts against the old SELL first. A full SELL drains
remaining BUYs; a late BUY becomes next-round seed inventory without another seed
purchase. The existing reducer retains the one-SELL invariant throughout.

The pure reducer cannot distinguish an unsent intent from a lost acknowledgement.
When draining or replacing, the writer can atomically prove that a queued intent
has never been attempted and revoke it locally. Attempted intents never take this
shortcut. This handles cancellation of reserved but unsent ladder/SELL orders
without fictitious exchange acknowledgements or unnecessary external calls.

Unsupported/disconnected streams explicitly degrade supervision and advance the
epoch. Quiet streams cannot extend send permission beyond the configured age.
Heartbeat hints also require recovery. A full queue refuses the new hint, retains
admitted inputs and requires a complete snapshot; callers retain refused inputs.
The tests recover an actual fill after a burst of 1,000 hints.

Explicit limits bound scopes (at most 1,000), queue slots, pages, rows, read/send
calls, quiet age, deadlines and backoff. Rate budgets and retry-after deadlines
are per venue/account and shared by reads and writes. One unavailable account
does not spend another's recovery budget. No automatic retry loop exists: the
caller must schedule the next service tick. A 429 consumes the request budget
and honors retry-after before subsequent recovery calls; its send outcome remains
unknown. Backoff has bounded deterministic jitter. Calls use cooperative asyncio
timeouts; a stuck OS/interpreter or deliberately cancellation-resistant injected
method is outside those timing guarantees.

## Remaining work and sources

I4 uses finite synthetic histories. Reaching a record/page/history bound is a
visible hold, not permission to evict financial evidence. Production history
windows, per-venue cursor/watermark proofs, balance drift reconciliation,
subscription handling, historical sequence ambiguities, sustained throughput,
hardware budgets and the paper soak remain unverified. Configuration and clocks
are explicit trusted inputs to this mock API, not a deployed scheduler.

The I1 reducer, dependency lock and real exchange read-only boundary remain
unchanged. Source and official docs consulted: pinned CCXT 4.5.91 precision
implementations, the [CCXT manual](https://github.com/ccxt/ccxt/wiki/Manual), and
[SQLite transaction semantics](https://www.sqlite.org/lang_transaction.html).
These references guide implementation; executed tests are listed separately.
