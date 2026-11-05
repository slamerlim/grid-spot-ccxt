# I8 recovery through unified personal-trade history

Base: `942564d8259ebcb7edaea89486296abdf30ddd8a`, branch
`i8/public-paper-hour`. This continues authorized offline development under local
validation and GitHub Free only. It implements the read alternative identified in
`I8-RECOVERY-PLAN.md` for venues without unified per-order trade queries.

## Shared read path

`exchange.private.read_account_history` accepts a caller-owned transport exposing
CCXT's unified `fetch_order` and `fetch_my_trades`. For 1–100 distinct known order
IDs in one symbol, it reads each order, queries personal trades once with
`since=None` and an explicit 1–100 row limit, then reads each order again. The
whole operation has one 1–30,000 ms deadline and at most `2*N+1` unified calls.
The trade limit covers the complete returned page, including duplicates and
trades outside the requested order set. There is no automatic pagination, retry,
SDK construction or per-order repetition of the account-trade query.

The existing shared order/trade parsers perform Decimal conversion. Duplicate
trade IDs are checked across the entire symbol page: identical observations count
once, while conflicting amounts, prices, fees or order attribution refuse the
batch. Each requested order then uses the existing stability and exact cumulative
quantity checks. A short page, equal timestamps or apparently quiet order is not
proof that its missing fills do not exist. If quantities do not match, no partial
batch is returned. Timeouts and ordinary failures are sanitized; caller
cancellation propagates and no retry is attempted.

The result is an `OrderHistoryBatch` containing the matched histories and
`unmatched_trades`. Trades for terminal, other or unknown orders remain observable
for subsequent ownership checks. Their presence is not permission to adopt them,
and their absence does not establish account completeness. The shared
`TradeObservation.order_id` now permits `None`, preserving a missing unified
field. Such a trade remains unmatched; it cannot contribute to any known order's
cumulative total. Per-order reads and financial normalization still require
actual order attribution. Known trade identity, symbol, amount and price remain
required for these observations. Missing optional fees are not invented as zero.

`execution.recovery.inspect_account_orders` integrates the reader with the same
sole-writer plan, original-intent checks and final plan revalidation used by
`inspect_orders`. It preflights `2*N+1` against the whole unified-call budget
before the first read. If no external IDs are known, it makes no calls. Attempted
orders with unknown external IDs remain unresolved, including when other orders
are successfully observed. Unmatched trades are retained on `RecoveryInspection`.
Both inspection paths are read-only and cannot clear a halt, resolve an intent,
post a fill or grant dispatch authority. No database transaction spans an await.

The caller chooses this path explicitly; an error from a per-order method does
not silently trigger extra requests. Client lifetime, spot-account configuration,
SDK capability checks, HTTP/rate/byte budgets and any internal SDK pagination
remain transport responsibilities. Unified call counts do not count internal
HTTP requests. No authenticated transport or operator entry point is added.

## Actual pinned SDK fixtures

Tests invoke inherited asynchronous methods from **CCXT 4.5.91** XT and BingX
classes, using invented preloaded spot markets and documented string-number mode.
Both lack declared `fetchOrderTrades` support in this pinned SDK. The unsupported
method, lower fetching and signing are guarded with the existing BaseException
refusal; Internet sockets are blocked. Every client closes in `finally`.

For two orders, the intercepted sequence is two order queries, one personal-trade
query and two order queries. Fixtures assert the exact GET method, API family,
route and parameters at every invocation:

| SDK class | Order route | Trade route | Trade parameters |
|---|---|---|---|
| XT | `order/{orderId}` | `trade` | symbol, `bizType=SPOT`, limit |
| BingX | `trade/query` | `trade/myTrades` | symbol, limit |

Each successful fixture preserves a price beyond binary-float precision, the
parsed fee and an unmatched trade. Each incomplete fixture withholds one order's
fill while retaining the other rows; matching the other order cannot produce a
partial success. No credentials, timestamp/signature or external request is
produced. These fixtures establish the tested SDK request/parse path, not actual
account availability, retention, fee settlement or venue readiness.

The [CCXT order API documentation](https://github.com/ccxt/ccxt/wiki/Manual#understanding-the-orders-api-design)
describes using personal trade history when order-history capabilities differ.
The [pagination contract](https://github.com/ccxt/ccxt/wiki/Manual#querying-multiple-orders-and-trades)
leaves exchange-specific availability and default windows explicit. The helper
uses unified output throughout; all raw XT/BingX response fixtures are parsed by
the real pinned SDK, with no application venue-response parser.

## Validation

The initial focused group passed 112 cases in 5.71 seconds. Subsequent regression
cases reproduced the overstrict rejection of a missing optional trade order ID:
both failed before preserving the field as unknown. After that correction, the
broader exchange/execution/static-boundary group passed **398 tests in 77.29
seconds**, including refusal to post an unattributed fill through the real mock
ledger. Reproduce with `uv run --locked pytest -q tests/exchange tests/execution
tests/test_i0_boundaries.py`.

Ruff lint/format passed (273 Python files), as did strict mypy over 201 source
files, Bandit over source/scripts and the 66-package lock check. The locked
dependency advisory audit reported no known vulnerabilities. Original
specification hashes, dependency manifests, schema, financial codec and existing
executor remain unchanged. Final full suites passed with no failures or skips:

| Interpreter | Result | Pytest elapsed |
|---|---|---:|
| Python 3.12.16 | 1,435 passed | 497.45 seconds |
| Python 3.13.18 | 1,435 passed | 513.32 seconds |

Both exited 0. Including wrapper overhead, elapsed times were 498.021958 and
514.288563 seconds. The application digest remained
`692a7c8f6ecac72d55f7a6740f1a392e2d647d68037b90880b4503723fc5e14e`;
the application/test Python plus dependency-manifest digest remained
`3f3937697c31c05dbdddd0f0cf00350b7113dabbc5c9dc9a7e0701a59841559c`.
Logs and JSON receipts are under `/tmp/grid-i8-account-history-checks/`, with stems
`3.12-1793856861264449424` and `3.13-1793856861260183411`.

Secret scanning covers 282 repository files with the baseline unchanged. Package
verification compares 90 wheel application modules, the embedded README and 280
required source-distribution files with the checkout. Generated databases/data
are excluded. Artifacts are under `/tmp/grid-i8-account-history-dist/`; the
receipt is `/tmp/grid-i8-account-history-package-verification.json`. These are
local temporary evidence paths, not Git artifacts or permanent storage.

## Remaining integration and rollback

This read path does not complete account-wide recovery. A limited recent trade
page may omit older fills or an entire unknown order. Required missing quantities
therefore remain incomplete; long-lived/high-fill-count orders need bounded
history continuation or validated durable-fill comparison, not larger unbounded
scans. Unknown-submission lookup, open-order discovery, unexpected account
activity, retention guarantees, settlement ordering and financial reconciliation
remain unfinished. Stable per-order reports do not form an atomic account
snapshot. The existing executor's complete-history, terminal contradiction,
ownership and financial checks remain in force.

No schema, dependency or financial-record migration is introduced. Rollback is a
scoped code/tests/docs revert; no database downgrade or exchange action is needed.
Preserve the completed public-hour evidence. Full I8/I9, account validation and
deployment remain gated. Hosted CI is pending under the unchanged account lock;
publish with `[skip ci]` and no paid GitHub capabilities.
