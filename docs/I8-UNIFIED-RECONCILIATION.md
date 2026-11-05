# I8 unified observations into mock reconciliation

Base: `286940d6117dff52c6b52a9f34c6bd7f9599c0de`, branch
`i8/public-paper-hour`. This continues the approved offline development and the
owner's clarification that CCXT supplies unified formats. No exchange access,
private credentials, sandbox/live trading or deployment is included.

## Shared boundary

The existing `exchange/private.py` parsers now preserve optional client ID,
order type/side/price/amount and trade type/cost alongside their existing exact
observations. There is no additional venue parser or new execution protocol.
`MappedSyntheticExchange` now produces CCXT unified dictionaries; its old
invented `WireOrder` dataclass is removed. Default `SyntheticExchange` callers
and all durable record formats remain unchanged.

`execution/client_ids.normalize` passes each order through the shared parser,
then resolves its returned client ID through the sole writer's durable registry.
It verifies the complete allocation scope and prior attempt. If the client ID
is absent, only an already committed exact external-order binding in that scope
can establish identity. A present but unknown or contradictory client ID never
falls back to another identity. There is no price/size matching or ID truncation.

Optional original terms may be recovered from that proven owned intent. Any
terms actually supplied must agree with the reserved LIMIT order; a supplied
market type, changed side, amount or price is refused. Cumulative quantity and
status are still required for this complete-snapshot interface. Supplied
remaining quantity must agree. CCXT `closed` becomes the existing fully-filled
status; `expired` becomes terminal cancellation while retaining actual fills.
An incomplete acknowledgement is not a complete reconciled snapshot.

Trades reuse the shared parser and must reference their matching owned order.
Optional trade side/type can use that order; contradictory supplied values fail.
Prices must respect its limit. A supplied quote cost must equal exact amount
times price: a venue's different settlement-rounding convention remains
unsupported until the financial model can represent it explicitly. The adapter
does not silently discard the difference. Cumulative order values never produce
invented trades.

The existing shared fee parser prefers `fees` when defined, otherwise `fee`.
These fields are not added together. Missing/empty/incomplete fee evidence cannot
post a fill. Charges and rebates retain their actual currencies and signs.
The mock supplies optional settlement quote valuations bound to matching fee
currencies and amounts. Nonzero third-token fees require that explicit value;
a known zero charge has zero quote cost without an FX quote. No later market
price or forecast fee is substituted for actual settlement evidence.

The fake supplies its explicit execution sequence, independent of page/arrival
order. Neither CCXT timestamps nor trade-ID sorting are promoted to proof of a
total real execution order. All pages retain the original complete-history,
stable-revision, scope and record bounds. The executor validates the entire
snapshot before binding or posting. Existing reservation, one-SELL, cancellation,
duplicate-fill, reconciliation and restart rules remain in force.

## Verification

The new `tests/execution/test_unified.py` uses real pinned **CCXT 4.5.91** OKX
parsers over invented data. Signing/request/fetch methods refuse access, Internet
sockets are blocked and every client is closed. The normalized outputs flow
through one-record mock pages into the real temporary ledger, then through
repeated replay and a writer reopen. Six cases cover quote/base/token charges,
rebates and explicit zero quote/token fees, checking exact cash, owned base,
position cost, fee-token balance and one durable fill.

Other cases cover missing optional terms, previously bound anonymous reports,
unknown/foreign identities, expiration mapping, order/trade contradictions,
missing/partial fees and FX, float input, changed valuation context, reordered
and duplicate trades. Rejected complete snapshots leave zero new bindings and
fills. Existing mapped seed/ladder/partial-SELL/cancel-replace and nine SIGKILL
submission cases now use the unified report format. The new tests do not claim
new power-loss or authenticated crash evidence.

Reproduce using the approved local Python/SQLite environment:

```sh
uv run --locked pytest -q tests/execution tests/exchange tests/test_i0_boundaries.py
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked mypy
uv run --locked pytest -q
```

The broad focused group passed 274 tests in 64.93 seconds before the final
optional-trade-side adjustment. The shared-parser/unified group then passed
52 tests in 4.12 seconds. Full application validation:

| Interpreter | Result | Pytest elapsed |
|---|---|---:|
| Python 3.12.16 | 1,311 passed | 485.98 seconds |
| Python 3.13.18 | 1,311 passed | 501.63 seconds |

Both wrappers completed with exit 0; elapsed times including wrapper overhead
were 486.500424 and 502.168128 seconds. The application source digest before
and after both runs remained
`c90e1235820019bee06705a0c24e8a54d94e9a8970ec0e32e2e7ea4b6efb048c`.
The additional source/test Python plus dependency-manifest digest remained
`02998ad2759de2e28d41819de4e86d258819bdb1dedb862e5296d5267ee524ab`
during those runs. JSON receipts/logs are under `/tmp/grid-i8-unified-checks/`,
with stems `3.12-1793851630723905425` and `3.13-1793851630716605741`.

After the full suites, the new test fixture gained an explicit `finally` close
for its replacement executor before closing the writer. Application code did
not change. The final fixture passed all **24 cases on each interpreter**, in
4.48 and 4.41 seconds respectively; lint/format/type checks also passed again.
The final source/test digest is
`2ffc2056c94bb728c431581771d8609af88863f60fc770abdc5adc3046cfb3ff`,
recorded with the unchanged application digest in
`/tmp/grid-i8-unified-final-source.json`. The full suites were not rerun for
this test-only cleanup; the distinction is preserved rather than claiming the
final test-file bytes were those of the earlier full runs.

Ruff, strict mypy, Bandit over source/scripts, lock validation and the complete
locked-dependency advisory audit passed; no known vulnerabilities were reported.
Secret scanning covered 273 repository files with the existing baseline
unchanged. The four original specification hashes, dependency manifests, schema
and financial codec remain unchanged. No tests failed or were skipped in the
full suites or final fixture checks. Hosted CI remains pending.

Package byte verification checks 89 application modules, the embedded README
and 268 required source-distribution files, excluding generated data/databases.
Its receipt is `/tmp/grid-i8-unified-package-verification.json`; artifacts are
under `/tmp/grid-i8-unified-dist/`. These `/tmp` pointers are local evidence,
not Git artifacts or guaranteed permanent storage.

## Remaining evidence and rollback

Dictionary fields do not independently establish authenticated account/spot
source, complete exchange history, fee provenance or settlement chronology.
The CCXT manual describes optional identifiers and fees that may be provided
or calculated: [order structure](https://github.com/ccxt/ccxt/wiki/Manual#order-structure)
and [personal trades](https://github.com/ccxt/ccxt/wiki/Manual#personal-trades).
This increment's source and settlement facts are explicitly synthetic. Actual
endpoint/account behavior still needs separately authorized validation. No
private REST/WS client, real pagination policy or sender is added; other venues
are not certified by the OKX fixtures. Numeric-only client IDs and the known
outgoing BingX conversion issue also remain open.

No database migration, immutable source change or dependency update is required.
A scoped revert restores the prior mock report API; preserve temporary evidence
and reconcile retained mock external state under the selected version. Do not
rewrite financial records or restore an old backup to manufacture missing IDs.
GitHub Free only, local checks and `[skip ci]` remain required while the hosted
CI account lock is unchanged. Full observed I8 acceptance and I9 remain open.
