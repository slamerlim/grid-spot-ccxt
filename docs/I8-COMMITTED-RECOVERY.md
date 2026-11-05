# I8 committed fills and recent unified recovery windows

Base: `485d30f180802b3497dc3d3b3600cd52cdc54832`, branch
`i8/public-paper-hour`. This continues approved offline development after the
completed public hour, using local validation and GitHub Free only.

## Problem and responsibility

The previous inspection required a bounded CCXT trade page to contain every
fill of each outstanding order. An order with older fills could therefore be
refused even when those fills were already durably posted. Repeatedly downloading
lifetime history is not a bounded recovery strategy.

CCXT still owns exchange-specific parsing and unified records. This increment
compares those records with the bot's accounting history; it introduces no venue
response parser. The [CCXT order API documentation](https://github.com/ccxt/ccxt/wiki/Manual#understanding-the-orders-api-design)
describes differing history capabilities and the need for local order tracking.
The [numeric correction](I8-CCXT-UNIFIED-NUMBERS.md) remains applicable: documented
string-number mode, shared Decimal conversion and narrow reproduced SDK exceptions.

## Durable baseline and comparison

The sole writer builds a `RecoveryPlan` for working orders. In addition to
placement intent, attempt and order/client binding checks, it now validates:

- Retained working-order fills against their exact canonical SQLite payloads.
- Venue/account/symbol/external trade identity and the owned order binding.
- The fee projection, including previously committed quote valuations.
- Exact sums matching the order's committed filled amount and gross cost.

`RecoveryTarget.committed` retains each external trade ID and its saved `Fill`.
`max_committed_trades` defaults to 1,000, accepts 1–10,000, and applies across the
allocation's working orders, including queued orders checked before targeting.
Oversized or inconsistent baselines refuse before any external read. Fill ranges
use the existing allocation/order index and a limit of remaining allowance plus
one; fee reads are keyed and bounded. No new schema or index is introduced.

Both inspection paths then obtain stable bounded unified observations. A returned
trade with a committed identity must agree with its saved amount, price, order
and any supplied fees. Supplied side/type/cost must also agree with the owned
LIMIT order. Known trades cannot be reassigned to another order or allocation.
Missing optional fields remain unknown; they never overwrite saved fees or FX.

For each inspected order the acceptance equation is:

```text
committed filled quantity + newly observed trade quantity
    = stable exchange-reported filled quantity
```

Already-posted trades may appear again or be omitted. A page missing new quantity
still refuses; observing the same old trade twice cannot fill the gap. Changed
cumulative reports or conflicting identities also refuse. This is evidence for
the observed quantity, not a proof of complete account history or settlement order.

After the reads, the same sole-writer callback rebuilds the entire plan, including
the committed baseline, before comparing observations. A changed version, binding,
retained fill, payload, fee projection or baseline identity prevents a result.
No database transaction is held across the external await.

Unmatched account trades remain unmatched. A returned identity already committed
for a terminal order or another allocation is checked through indexed durable
records, including after state compaction. It is not imported into the current
allocation. Missing order attribution remains missing, even when a trade identity
can be checked against saved facts. Terminal history is retained, not downloaded
or scanned in full on each inspection.

## API and authority

`read_order_history` and `read_account_history` retain their strict original
contract: returned unique trades must cover the entire reported filled quantity.
New explicit `read_order_window` and `read_account_window` functions share the
same parsing, identity, stability, cancellation and bounds checks but permit
less than that quantity. Only ledger-aware inspection can prove the omitted part.

`inspect_orders` and `inspect_account_orders` now use those window readers.
Their `histories` can contain a recent subset; consumers must not interpret it
as lifetime history. `RecoveryInspection` additionally exposes `new_trades` and
`committed_trades`. These are classifications of returned observations. The
latter can include unmatched terminal/other-allocation replays and is not a list
of all saved fills; those for targeted working orders are in the plan baseline.
Unrecorded unmatched trades remain only in `unmatched_trades`.

New trades may still lack fees or settlement context. Neither their presence nor
the quantity equation authorizes financial posting. Inspection does not bind an
order, change funds, clear uncertainty/halts or enable dispatch. Attempted orders
without external IDs remain unresolved. Call/deadline/trade caps and transport
ownership are unchanged; underlying HTTP/byte enforcement remains caller work.
With no known external IDs, inspection makes no account query or discovery claim.

## Offline checks

`tests/execution/test_recovery_fills.py` adds 44 cases covering both per-order and
account-trade inspection: omitted committed fills, missing new fills, repeated
inspection, conflicting prices/amounts/fees/IDs/order terms, corrupted baseline
records, cap exhaustion before reads, optional unknowns, retained fee valuation,
terminal replay after compaction, reused terminal IDs, foreign allocation identity
and evidence changed during reads without a version change.

A populated-ledger SQL trace verifies read-only indexed searches, bounded working
fill ranges and no lifetime scans. Inspection leaves durable financial state
unchanged and does not clear an executor halt. The existing executor alone posts
the simulated new fill in the follow-up test. All inputs and databases are fake
or temporary; sockets are blocked by the test fixtures.

The focused file passed all 44 cases in 5.35 seconds. The wider command
`pytest -q tests/exchange tests/execution tests/test_i0_boundaries.py` passed
442 cases in 80.23 seconds. An initial command named a nonexistent boundary
test file and collected no tests; the corrected command is the one reported here.

Final full suites on identical application/test bytes:

| Interpreter | Result | pytest elapsed |
| --- | --- | --- |
| Python 3.12.16 | 1,479 passed | 501.23 seconds |
| Python 3.13.18 | 1,479 passed | 515.08 seconds |

Both wrapper processes completed with exit 0; neither final suite failed or
skipped tests. Application source digest before and after each run was
`36c8ab50d11ad380fbe913120224a6d565048135ec38ce600644b0a8675299e0`.
The digest covering application/test Python files and dependency manifests stayed
`d14a77d63743dc3fa19acf1e7c5dd0f9609bf2f9223dc5edbac34c505995004c`.
These fingerprints exclude documentation and do not certify installed dependency
bytes. Logs and JSON receipts are in `/tmp/grid-i8-committed-recovery-checks/`,
with stems `3.12-1793859267391686596` and `3.13-1793859267388781751`.
Wrapper elapsed times were 501.777319 and 515.616714 seconds respectively.

Ruff lint/format, strict mypy over 202 source files, Bandit over source/scripts,
lock validation and the full locked-dependency advisory check passed; the audit
reported no known vulnerabilities. The four imported specifications, dependency
manifests, secret baseline, schema, codec and executor remain unchanged.
These are local results, not hosted CI or live acceptance.

Package validation compares application module bytes, the embedded README and
all required source-distribution files with the checkout, and excludes generated
data/databases. Its separate receipt is
`/tmp/grid-i8-committed-recovery-package-verification.json`; artifacts are under
`/tmp/grid-i8-committed-recovery-dist/`. These local `/tmp` paths are evidence
pointers, not Git artifacts or guaranteed permanent storage.

## Remaining work and rollback

The mock executor is unchanged and still requires complete fake history with
explicit revisions and execution sequences. Account-wide discovery, unknown
submission lookup, unexpected activity detection, bounded continuation, actual
settlement ordering and atomic executor integration remain unfinished. Timestamp
or page order is not established execution order. Missing new quantity or fee
evidence must not be guessed to permit posting.

No dependency, schema, codec or financial event format changes. Rollback is a
scoped code/test/documentation revert; no database downgrade or rewrite is needed.
No new exchange requests, credentials, orders, transfers or deployment occurred.
The original 48-hour observed gate, first-venue account evidence and I9 remain
open. Hosted CI remains pending under the unchanged account lock; publication
uses `[skip ci]` without retrying it. Preserve completed public-run evidence.
