# I8 bounded outstanding-order inspection

Base: `dd36b7b079533563c913927bcc61cf314fd850c8`, branch
`i8/public-paper-hour`. This continues authorized offline development with local
validation and GitHub Free only. CCXT supplies the unified order/trade structures;
this increment connects those observations to the bot's durable order ownership.

## Behavior

`execution.recovery.recovery_plan` reads an allocation through the sole writer.
It selects current working orders, verifies their durable projections and original
placement terms, then checks saved bindings and any registered external client ID.
Placement lookup uses the existing canonical intent primary key. It does not scan
lifetime intents, orders or fills, and adds no index or schema migration.

A registered client ID alone does not prove submission. Queued, unattempted orders
are checked but omitted from the read targets. Attempted orders remain targets
even when their external order ID is unknown. The `unresolved` inspection property
retains those obligations explicitly; no client ID is substituted for an external
order ID, no rejection is inferred and no resend is attempted. Existing legacy
orders without an external client-ID registry remain identifiable by their saved
binding and original internal client ID.

`inspect_orders` builds the plan, reads its known external IDs with the existing
`read_order_history` helper, then rebuilds the plan through the writer. Both plans
must agree, including allocation version, target orders, bindings and registered
client identities. Concurrent changes invalidate the result. Each observation
must match the owned order's ID/symbol and any supplied client ID, side, type,
amount or price. Cumulative quantity cannot regress below the committed quantity
or exceed the original order amount. Supplied remaining quantity must agree;
closed/rejected reports must have the corresponding full/zero cumulative quantity.
Optional missing descriptive fields remain missing in the returned observations.

The caller sets finite limits: 1–100 working orders, 1–100 trades per order,
1–300 unified calls and a 1–30,000 ms deadline for the whole inspection. The order
cap includes queued orders even though they do not cause reads. Three calls per
known external ID must fit the call budget before the first transport invocation.
Reads are sequential and use CCXT's unified `fetch_order`/`fetch_order_trades`
interface. An unsuccessful or interrupted inspection returns no partial batch;
caller cancellation propagates. The caller still owns transport closure and
underlying HTTP byte, request, rate and capability enforcement. Unified method
counts are not counts of the SDK's internal HTTP requests.

This helper makes no financial writes, registers no identities and changes no
halt/readiness state. A successful inspection does not resolve unknown intents,
prove account completeness or grant permission to dispatch. Terminal financial
records remain durable even though they are excluded from the working-order plan.
The existing mock executor's complete-history and terminal-contradiction checks
remain unchanged; this inspection is not yet installed as their replacement.

## Validation

`tests/execution/test_recovery_plan.py` covers registration without submission,
accepted but unbound submissions, mixed known/unknown orders, partial fills before
and after posting, and terminal compaction without deleting fills. It checks
preflight budgets, changed allocation versions/bindings, corrupt/missing placement
records, contradictory source terms, cumulative regression/overfill, optional
unknowns, timeouts and cancellation. Inspection must leave ledger snapshots and
dispatch restrictions unchanged. Tests use temporary mock ledgers and synthetic
unified dictionaries; no exchange access occurs.

The SQL test traces the planner's actual statements and checks SQLite's query
plans. Every observed statement is a SELECT using an indexed SEARCH, without a
table SCAN or any query of lifetime fills/trade keys. This establishes the tested
query shape, not a production latency or large-allocation resource benchmark.

Ruff lint/format passed (269 Python files), as did strict mypy over 199 source
files, Bandit over source/scripts, and the 66-package lock check. The advisory
audit of all locked dependency groups reported no known vulnerabilities. The four
original specification hashes, dependencies, schema, financial codec and existing
executor are unchanged. Final complete suites passed with no failures or skips:

| Interpreter | Result | Pytest elapsed |
|---|---|---:|
| Python 3.12.16 | 1,379 passed | 493.72 seconds |
| Python 3.13.18 | 1,379 passed | 508.72 seconds |

Both processes exited 0. Including wrapper overhead, elapsed times were
494.270867 and 509.653949 seconds. The application digest remained
`ed7399c466f40bfa1e4c32f097f7d02e89f7102633558c157fa6dae8f35e7169`;
the application/test Python plus dependency-manifest digest remained
`d72299d608a35accf876dc525731cffcc836950597070c872011181733e2ee55`.
Logs and JSON receipts are under `/tmp/grid-i8-recovery-plan-checks/`, with stems
`3.12-1793855556159778137` and `3.13-1793855556147218280`.

Secret scanning covers 279 repository files with the baseline unchanged.
Package verification compares 90 wheel application modules, the embedded README
and 277 required source-distribution files against the checkout. Generated
databases/data are excluded. Artifacts are under `/tmp/grid-i8-recovery-plan-dist/`;
the receipt is `/tmp/grid-i8-recovery-plan-package-verification.json`. These
temporary paths are local evidence, not Git artifacts or permanent storage.

## Remaining work and rollback

Account-wide recovery still needs bounded open-order discovery, unknown-submission
lookup, unexpected-activity detection and complete account/retention semantics.
Observed trades must still be compared with committed fill identities and amounts,
with settlement fees and execution ordering established before financial posting.
Quantity matching alone does not provide those facts, and independent order reads
do not provide a globally atomic account snapshot. Keep these requirements in the
eventual integration rather than weakening the executor's existing checks.

An offline inspection of pinned CCXT 4.5.91's declared capabilities also shows
that the two-method read path is not universal: `has['fetchOrderTrades']` is
`False` for XT and `None` for BingX. The other five target classes declare it
`True`; all seven declare `fetchMyTrades` available. These flags are SDK metadata,
not observed account behavior. A bounded fallback using unified trade-history
methods still needs implementation and completeness evidence. Do not duplicate
venue response parsers or treat the current helper as seven-venue coverage.

There is no authenticated client, operator command, deployment or new exchange
authorization in this change. The completed public hour is not rerun. Full I8
acceptance and I9 remain incomplete. Hosted CI remains pending under the unchanged
account lock; publication uses `[skip ci]` without paid GitHub capabilities.

Rollback is a scoped code/test/docs revert. No database downgrade or exchange
action is needed. Preserve all existing financial records and public-run evidence.
