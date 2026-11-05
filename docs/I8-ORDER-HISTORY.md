# I8 bounded unified history for one order

Base: `1c862888d81520953324ddff657fcb7a1ad90d87`, branch
`i8/public-paper-hour`. This continues authorized offline development with local
validation and GitHub Free only. It adds a read primitive for bounded recovery,
not an authenticated gateway or a replacement for account-wide reconciliation.

## Behavior

`exchange.private.read_order_history` reuses the existing shared CCXT order/trade
observation parsers. It receives a caller-owned transport exposing only the two
unified methods `fetch_order` and `fetch_order_trades`. It creates no SDK client,
reads no configuration or credentials, starts no worker and makes no write call.

One invocation performs at most three sequential unified calls within one
caller-selected 1–30,000 ms deadline:

1. Fetch the identified order and require its ID, status and cumulative quantity.
2. Fetch that order's trades with `since=None` and an explicit 1–100 record limit.
3. Refetch the order and compare status/cumulative with the first observation.

Conflicting known descriptive terms also refuse the result. A field may be
missing on one read and supplied on the other; the final observation preserves
its own unknowns. No original terms, fee values or executions are invented.
Trade identity, symbol and order must agree. Identical duplicate trades count
once; conflicting duplicates fail. The exact sum of unique positive trade
amounts must equal the stable reported cumulative quantity. An empty or short
page with a nonzero mismatch is incomplete, never proof of no further fills.
Exact arithmetic is independent of the caller's ambient Decimal precision.

The return value is an `OrderHistoryObservation`. Matching quantity is only
evidence about these reported observations. Optional fees may remain unknown;
no sequence, ownership decision, settlement FX, ledger posting or send authority
is produced. Arrival order and equal timestamps do not establish execution order.
Any malformed, conflicting or interrupted read returns no partial observation.
Timeouts and ordinary transport errors are sanitized; caller cancellation
propagates. There is no retry or automatic pagination loop.

The three-call bound applies to unified invocations, not hidden SDK HTTP calls.
The eventual transport must enforce HTTP byte/request/rate limits and capability
settings, including disabled or bounded SDK automatic pagination. This helper
does not replace that transport budget. It leaves the reused client's closure
and lifetime with its owner. Oversized decoded lists are rejected, but a list
limit alone does not bound an HTTP response that the SDK has already parsed.

## Pinned SDK evidence

Two fixtures invoke actual **CCXT 4.5.91** OKX `fetch_order` and
`fetch_order_trades` through the inherited asynchronous implementation.
Preloaded invented `BASE/QUOTE` markets prevent discovery. Each first request
boundary validates the expected GET method, private route and exact parameters,
then returns invented replies. `fetch`, `fetch2` and signing refuse access with
the existing BaseException guard; Internet sockets are blocked and clients close
in `finally`. No authenticated request or exchange connection occurs.

The successful case observes exactly `trade/order`, `trade/fills-history`,
`trade/order`. The trade request includes the owned order ID, instrument ID,
`instType=SPOT` and `limit=2`, without `since` filtering. CCXT parses exact long
decimal prices and two fills with equal timestamps; the shared reader checks
their summed quantity and preserves fees. The incomplete case returns one fill
for a reported total of one unit and refuses the result, despite the short page.
These fixtures prove this pinned SDK path with synthetic data only.

The [CCXT manual](https://github.com/ccxt/ccxt/wiki/Manual#trades-by-order-id)
documents the unified order-trade method. Availability and semantics still need
per-venue/account evidence; this OKX fixture does not certify the seven venues.
The earlier [terminal recovery investigation](I8-TERMINAL-RECOVERY.md) records
why `since` and returned-list limits alone are insufficient for completeness.

## Validation and remaining integration

The new group passed 36 cases in 1.20 seconds before formatting. Cases include
duplicate/gapped trades, changing status/quantity/known terms, optional unknowns,
foreign IDs/symbols/sides, floats, row limits, exact sums under a low-precision
ambient context, invalid bounds, and timeout/cancellation/error at each call.
Reproduce with `uv run --locked pytest -q tests/exchange/test_order_history.py`.
After formatting, the broader exchange, unified-execution and static-boundary
group passed 206 tests in 54.40 seconds.
Final complete suites used the same application/test bytes throughout:

| Interpreter | Result | Pytest elapsed |
|---|---|---:|
| Python 3.12.16 | 1,355 passed | 490.24 seconds |
| Python 3.13.18 | 1,355 passed | 505.59 seconds |

Both exited 0, with no failures or skips. Including wrapper overhead, elapsed
times were 490.788203 and 506.142708 seconds. The application digest remained
`363081ccc7b0c5514fd17a9529c4a6cc07556396f1a8eb3c9ac694f420b82b8c`;
the source/test Python plus dependency-manifest digest remained
`bcbfd1f4c5d9455e64ac324f1e50ffbb1f4d2ebe30d3530bef2e5f90240c20e2`.
Logs and JSON receipts are under `/tmp/grid-i8-order-history-checks/`, with stems
`3.12-1793853937885317919` and `3.13-1793853937882201032`.

Ruff lint/format, strict mypy, Bandit over source/scripts, lock verification and
the full locked dependency advisory audit passed; no known vulnerabilities were
reported. Secret scanning covers 276 repository files with the baseline unchanged.
The four original specification hashes, dependencies, schema and financial codec
remain unchanged. Package verification compares 89 wheel application modules,
the embedded README and 274 required source-distribution files against the
checkout, excluding generated databases/data. Artifacts are under
`/tmp/grid-i8-order-history-dist/`; the receipt is
`/tmp/grid-i8-order-history-package-verification.json`. These temporary paths are
local evidence, not Git artifacts or a promise of permanent storage.

The executor still uses its existing complete-history fake contract. Integration
must select unresolved/uncertain orders from the sole writer, compare returned
fills with durable identities and totals, and separately cover account-wide open
orders, unexpected activity, retention and settlement ordering before admission.
No omission check, terminal contradiction guard, financial record or migration
changes here. There is no new authenticated transport, exchange access, live
configuration, deployment or acceptance waiver. Full I8 and I9 remain open.

Rollback is a scoped code/docs/test revert; no database downgrade or exchange
action is needed. Preserve existing evidence and ownership records. Hosted CI
remains pending under the unchanged account lock; use `[skip ci]` publication.
