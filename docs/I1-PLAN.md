# I1 review plan — deterministic strategy and Decimal math

**Status: approved on 2026-11-03 with local validation.** The owner initially
selected "Approve this I1 plan after CI passes", then explicitly selected
"Proceed with local validation for I1" after the hosted account lock persisted.
This replaces the prerequisite for starting I1; hosted CI stays pending and
must not be reported as passed. The scope and partial-seed decision below remain
approved. Record local execution evidence and hosted limitations separately.

I1 is pure domain code using fake metadata and confirmed mock events. It adds
no CCXT client, network request, credential access, database, executor or deployment.
The authoritative acceptance matrix remains unchanged.

## Approval decision and inputs

The owner approved the following resolution of the PRD §3.2 / QA Q04
sellable-partial-seed ambiguity: manage sellable inventory immediately, but
wait for the seed order's reconciled terminal state before fixing its final
weighted-execution anchor and admitting lower BUYs. When the aggregate SELL
fully fills, unresolved seed orders participate in DRAINING alongside ladder
BUYs; reconcile their cancellation and late fills before a new round.

The revised approval permits local validation for I1 while hosted CI is
unavailable. Imported specifications remain unchanged; this records the owner's
explicit exception separately. I2 and later iterations are outside this
approval, and live trading remains prohibited.

The owner also requires GitHub Free only: never activate or use paid GitHub
features. Use local validation and preserve the verified free CI configuration.
An account lock is not authorization to pay; the local-validation exception
does not turn skipped or blocked hosted runs into successful runs.

All live policy values remain unconfigured. Tests use explicitly synthetic
fixtures for spacing, budgets, caps, precision, fees, ranges, freshness and
pricing guards. A fixture number is never promoted to a production default.

## Approved modules and contracts

- **Domain types:** frozen typed records for scoped allocation/round/order/fill
  identity, explicit currency amounts, precision rules, fees, market constraints,
  immutable committed state, risk snapshot and normalized events. Decimal
  constructors reject float, nonfinite values and unknown required constraints.
  Inputs carry revisions; the core does not read clocks, randomness or I/O.
- **Math:** `grid_prices(anchor, spacing, indices, rules)`,
  `minimum_buy(price, rules, fee_policy, sell_policy)`, and
  `aggregate_take_profit(position, rules, target, fee_policy)` return validated
  plans or explicit ineligibility reasons. Plans retain unformatted and
  formatted amounts, rounding decisions, fee estimates and applicable rule
  revisions for later audit. Formatting logic is independently testable; CCXT
  helpers are outside I1.
- **Reducer:** `reduce(state, event, context) -> Transition` returns a new
  immutable state plus proposed limit-order, cancellation or reconciliation
  intents. This is not authority to send orders or reserve actual funds.
  `context` supplies explicit current risk/config/market revisions and mock
  verified pricing facts. I2 will persist accepted transitions and reservations;
  I4 alone will perform exchange effects.

Keep these responsibilities in a small `grid.domain` package. Create modules
when implementing their behavior, not empty placeholders for I2–I9. Do not add
an unrelated framework, service or trading API to the scaffold.

## Mathematical and state behavior

- Compute lower levels as `A / (1 + g) ** n` with an explicit Decimal context;
  this is the integer-index equivalent of the specified logarithmic grid and
  avoids a float `log`/`exp` round trip. Require `g > 0`, valid positive prices,
  monotonic levels, allowed range and distinct prices after side-aware venue
  quantization. Tick-size and significant-digit modes have separate fixtures.
- Find the smallest precision-conforming BUY meeting BUY amount/notional/price
  constraints at that price. Check budget with conservative fee reserve. Then
  validate its post-buy-fee inventory against SELL amount/notional rules and
  fee reserve. **Reject** a minimum BUY that becomes unsellable; never increase
  it to make this second check pass. Minimum base quantities may differ by
  price when notional minima bind; no averaging multiplier is introduced.
- Track exact net owned base `Q`, remaining allocated acquisition cost `C`, and
  currency-specific liabilities after confirmed fees/fills. Proposed aggregate
  target is the least valid sell price satisfying the geometric bound over
  remaining net-inventory weighted cost and the configured strictly positive
  net profit requirement. Test the final predicate explicitly:
  `expected_sell_proceeds - remaining_cost - expected_exit_costs >= net_target`
  with `net_target > 0`, plus the geometric bound. Round SELL price upward to
  the first valid price satisfying both; reserve any base-denominated exit fee
  before determining sellable amount. Missing fee/conversion facts block the
  plan. Document the exact cost allocation and predicate in test fixtures.
- Preserve `INACTIVE`, `SEED_PENDING`, `ACCUMULATING`, `SELL_REPLACING` and
  `DRAINING`; suppress incompatible actions under paused, reconciling, retiring
  or portfolio-exit overlays. Acknowledgments/status-only events do not create
  fills, cost basis or PnL. Duplicate confirmed fill identities are no-ops;
  contradictory or insufficient events request reconciliation.
- Maintain one SELL slot per allocation, including a pending or uncertain
  submission. A new BUY fill may request cancellation/reconciliation but cannot
  allocate a successor SELL until the old order is proven terminal. A partial
  SELL accounts only its confirmed execution; a fill during cancel reduces the
  remaining available quantity before any successor is planned.
- Replenish lower BUYs only up to configured N, max lots, exact remaining funds,
  open-order limits and range. Account for partially working orders. Unknown
  submissions and obsolete generations block related replacements. No price
  observation alone advances the round.
- A full aggregate SELL starts DRAINING. Resolve all remaining seed/ladder
  BUYs and late fills before the next round. Existing net base becomes seed
  inventory; a new seed requires no remaining base and no unresolved BUY,
  enabled allocation, adequate risk budget and explicitly fresh pricing facts.
  Retiring allocations continue to own their obligations.

## Tests and exit criteria

Add Hypothesis only when these property tests are introduced, through the same
`uv.lock`. Test generated Decimal magnitudes, ticks/significant digits, min
amount/cost combinations and event interleavings; retain failing seeds.

| Future gate | I1 evidence; later gates remain separate |
| --- | --- |
| Q01 | Proposed order intents admit only LIMIT; wire-payload proof remains I4 |
| Q02 | Exact smallest BUY, mixed precision modes, missing limits, fee-base .999 rejection, differing SELL minima, no hidden quantity increase |
| Q03, Q10 | Pure budget/conservation and fee arithmetic; transactional reservation races remain I2 |
| Q04 | Acknowledgment without fill, unsellable partial, approved sellable-partial rule, weighted execution anchor |
| Q05 | Delayed cancel, partial SELL, fill during cancel, duplicate terminal event, successor quantity and single-slot invariant |
| Q06 | N=3 synthetic ladder, collapsed price rejection, finite caps/range, duplicate updates do not replenish twice |
| Q07 | SELL drained with pending BUYs, late fill reused, no duplicate seed, unknown cancellation blocks next round |
| Q08, Q09 | Deterministic replay, duplicate no-op, reordered/contradictory events, unknown submit blocks new intent; DB/network recovery remains I2/I4 |
| Q11, Q17 | State overlays and revision/freshness rejection only; actual universe/cache behavior remains I6 |

Require the existing I0 checks plus unit/property tests, typechecking of source
and tests, deterministic replay comparison, and a static/import boundary check
that domain code has no CCXT, socket, filesystem or persistence dependency.
Review all failure paths for explicit blocked reasons. Do not label entire
Q01–Q20 gates passed from partial I1 coverage.

## Delivery and rollback

Use a small I1 branch/PR with formulas, fixtures, actual test output and remaining
limitations in the evidence pack. Run no exchange or storage migrations. Keep
I0 doctor semantics and LIVE prohibition unchanged. If review rejects I1,
revert its scoped domain/test/dependency commit(s) and synchronize the preceding
lockfile; there is no database, wallet state or deployed service to migrate or
roll back. Stop after I1 review; I2 is a separate iteration.
