# I8 current recovery versus terminal orders

Base: `8a5de0af693bf730c85bcdc6b37eab0db4336b34`, branch
`i8/public-paper-hour`. This is continued offline development under the existing
approval, with local checks and GitHub Free only.

## Reproduced problem and correction

The reducer deliberately ignores a delayed OPEN event for a terminal order.
That behavior is appropriate for out-of-order private updates, but the executor
also allowed such a report in its supposedly current, complete recovery snapshot.
A previously canceled aggregate SELL could therefore appear open while recovery
authorized its replacement. A different terminal cumulative quantity was rejected
later during posting, after another valid new fill could already have committed.
Neither behavior meets the executor's whole-snapshot validation contract.

`MockExecutor._validate_snapshot` now checks every observed order against its
durable terminal state before constructing a posting plan. An OPEN report or
different cumulative quantity for a terminal order refuses the entire recovery.
No new bindings or fills from that snapshot are posted, the recovery hold remains,
and dispatch stays blocked. This applies both to orders retained in the reducer
snapshot and to compacted orders loaded by their indexed SQLite identity.

Matching terminal reports still reuse the verified committed facts. The reducer's
handling of delayed events is unchanged; private hints still only request recovery.
The source must provide a consistent current observation before recovery can
resume. The application never repairs venue reports or changes old ledger facts
to make a contradiction disappear.

Eight regression cases combine the ordinary and unified mapped mock gateways,
retained and compacted orders, and reopened versus changed-quantity reports.
They first settle a partial SELL cancellation, leave its successor queued, and
introduce a new lower BUY alongside the contradiction. They verify unchanged
financial state/fill rows and no successor submission. After the fixture supplies
consistent reports, the BUY posts once and exactly one replacement SELL manages
the reconciled 2.75 units. These are invented accounts and executions only.

Before the fix, all eight cases failed: four admitted the reopened SELL snapshot,
and four changed money before rejecting the contradictory late fill. The focused
history group passed all 15 cases after the fix in 6.91 seconds, before formatting.
Final full-suite and packaging evidence is recorded below.

## Remaining bounded-history work

This investigation also confirmed that the executor still requires every bound
order and posted trade in each recovery. Its fake supplies a stable revision,
explicit completion and execution sequence. These are synthetic contracts, not
properties established by CCXT's unified response schema.

The [CCXT manual](https://github.com/ccxt/ccxt/wiki/Manual#querying-multiple-orders-and-trades)
describes unified history calls with exchange-dependent filtering and pagination.
Its [order-trade query](https://github.com/ccxt/ccxt/wiki/Manual#trades-by-order-id)
can provide a useful bounded query where supported. Neither a short page nor
an absent order proves that all required execution evidence has been recovered.

Inspection of the installed [CCXT 4.5.91 OKX adapter](https://github.com/ccxt/ccxt/blob/v4.5.91/python/ccxt/async_support/okx.py)
shows that `fetch_order_trades` adds `ordId` and delegates to `fetch_my_trades`.
The latter omits its normal wire `limit` when `since` is supplied, then applies
the requested limit while parsing results. A caller's returned-list bound is
therefore not itself proof of the HTTP response bound or complete history.
Reuse the unified methods and test their bounded request behavior; do not add a
parallel venue response parser. This source inspection is not account evidence.

A later recovery increment must query outstanding/uncertain intents explicitly,
retain committed terminal facts locally, and validate any returned historical
contradictions. It must also cover unexpected account activity, delayed trades,
retention limits and interrupted pagination before granting new order authority.
Current fill records do not persist the fake's execution sequence; do not invent
a durable exchange cursor from timestamps, trade-ID sorting or page order.
Increasing lifetime page limits or deleting omission checks alone is insufficient.

## Validation and rollback

Full suites with the final formatted application/test bytes:

| Interpreter | Result | Pytest elapsed |
|---|---|---:|
| Python 3.12.16 | 1,319 passed | 489.00 seconds |
| Python 3.13.18 | 1,319 passed | 503.40 seconds |

Both processes exited 0 with no failed or skipped tests. Wrapper elapsed times
were 489.533813 and 503.939781 seconds. Application source stayed at digest
`354f550bddd506b4659b3e8db1aa6a7f7173571ae46918faf328a659d647ab06`;
the source/test Python and dependency-manifest digest stayed at
`5ef8320efe21d9f7b20ce47efe8f5b8e01c3e1d84940239b9d9a437cef9218f6`.
Receipts/logs are under `/tmp/grid-i8-terminal-checks/`, with stems
`3.12-1793852829967857197` and `3.13-1793852829964434735`.

Ruff lint/format, strict mypy, Bandit over source/scripts, lock validation and the
full locked dependency advisory audit passed; no known vulnerabilities were
reported. Secret scanning covers all 274 repository files with the existing
baseline unchanged. All four original specification hashes are unchanged.
Package verification compares the wheel's 89 application modules and embedded
README with the checkout, plus 272 required source-distribution files, and
rejects generated database/data artifacts. Its receipt is
`/tmp/grid-i8-terminal-package-verification.json`; built artifacts are under
`/tmp/grid-i8-terminal-dist/`. These temporary paths are local evidence, not
published artifacts or guaranteed permanent storage.

No schema, financial codec, dependency or original specification changes are
needed. Reproduce the focused check with
`uv run --locked pytest -q tests/execution/test_reconcile_history.py`.
No new exchange calls, credentials, private transport, service installation or
deployment are included. Full I8 observed acceptance and I9 remain open.

A scoped revert restores the earlier validator and its documented defect; do not
clear a recovery hold or rewrite financial history as a rollback procedure.
Preserve contradictory observations for investigation and keep placement halted
until the source and ledger can be reconciled. Hosted CI remains pending under
the unchanged account lock; use local checks and `[skip ci]` publication.
