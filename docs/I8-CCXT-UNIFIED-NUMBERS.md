# I8 CCXT unified-data and numeric correction

Base: `97469fbb74940054acccafb439ed3f5d18ff2827`, branch
`i8/public-paper-hour`. This follows the owner's clarification that CCXT should
provide unified formats and types. It is continued offline development under
the existing approval, with local validation and GitHub Free only.

## Responsibility and correction

CCXT owns venue-specific response parsing, unified symbols, standard order/trade
fields and the common API. The application consumes those unified structures,
converts exact financial values to its Decimal model, and checks the facts needed
for the relevant operation. Ownership, reservations, deduplication, accounting
and recovery belong to the bot. Missing optional fields stay unknown; a missing
field is not automatically grounds to reject every operation or the whole venue.

The [CCXT manual's string math section](https://github.com/ccxt/ccxt/wiki/Manual#string-math)
documents Python `exchange.number = str` alongside the default `float` mode.
Directly substituting `Decimal` is not that documented contract. An exploratory
unmodified OKX parser with `number = Decimal` lost fee information in the tested
fixture; this was not evidence that CCXT's normal supported output was broken.
The same invented trade under string mode preserved its price and fee. These
were local parser calls, not account observations.

`PublicCCXT` now selects string mode and removes its custom `safe_string`
extension. Existing shared market/book mappers convert unified financial values
to Decimal. Timestamps/counts remain integers. JSON decimal literals still
decode as strings before CCXT sees them, avoiding an earlier binary-float
conversion; duplicate JSON keys and invalid/nonfinite values remain rejected.
The original I3 public evidence retains its historical configuration and is not
relabelled as validation of this later change.

Two small book compatibility measures remain for pinned **CCXT 4.5.91**:

1. Its base `parse_order_book_bid_ask` calls `safe_float` directly, ignoring
   `number`. The existing exact level override remains, with CCXT still handling
   venue schemas and the rest of the book structure.
2. Bitfinex's separate book parser respects string mode but uses the common
   native-key sort, which sorts string prices lexicographically. The transport
   now numerically sorts bounded **unified** price levels before the shared
   validator. That validator still rejects duplicate/crossed prices, invalid
   quantities, oversized books and incomplete required observations. Raw venue
   messages are not parsed again by an application-specific book parser.

These behaviors were checked in the installed pinned
[base implementation](https://github.com/ccxt/ccxt/blob/v4.5.91/python/ccxt/base/exchange.py)
and [Bitfinex adapter](https://github.com/ccxt/ccxt/blob/v4.5.91/python/ccxt/async_support/bitfinex.py).
Do not patch SDK global classes or generalize these exceptions to unrelated
methods. Review and remove exceptions when an upgraded, tested SDK resolves them.

## Offline coverage

`tests/exchange/test_ccxt.py` exercises actual pinned SDK code with synthetic
HTTP, blocked Internet sockets and explicit client closure. The seven existing
public market/book cases now contain unsorted prices spanning different digit
widths, including a JSON price beyond binary-float precision. They verify exact
prices and numeric bid/ask ordering, cached markets and bounded requests.

Four new pure OKX order/trade parser cases verify exact price/amount/cost,
integer timestamps, order identity and charges, explicit zero fees, rebates and
missing fees. Optional trade type stays `None`; missing fees are not invented
as zero. Both unified `fee` and `fees` represent the same tested fee, not two
charges. These fixtures do not call private endpoints or post ledger fills.

Reproduce with the approved Python/SQLite test environment:

```sh
uv run --locked pytest -q tests/exchange
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked mypy
uv run --locked pytest -q
```

The focused exchange group passed **142 tests in 49.37 seconds** on Python
3.12.16. Final full-suite results:

| Interpreter | Result | Pytest elapsed |
|---|---|---:|
| Python 3.12.16 | 1,287 passed | 484.35 seconds |
| Python 3.13.18 | 1,287 passed | 498.96 seconds |

Both wrapper processes completed with exit 0. Application source digest before
and after each run was
`27126f73f10ec1c76b60febebb9f57826a9be477fd7dfe6a8d0a32038ad6d1a9`.
The additional digest covering application/test Python files plus the two
dependency manifests stayed
`f082e7cbfc6777a9246ee0b8dfa73e228827070bb39406eaaf97c30de67a1e51`.
These fingerprints do not include documentation or certify the installed SDK.
Logs and JSON receipts are in `/tmp/grid-i8-ccxt-numbers-checks/`, with stems
`3.12-1793849896937174171` and `3.13-1793849896929456140`; wrapper elapsed times
were 484.928041 and 499.520576 seconds respectively.

Ruff lint/format, strict mypy, Bandit over source/scripts, lock validation and
the full locked-dependency advisory check passed; the advisory result reported
no known vulnerabilities. Secret scanning covered 271 repository files with
the existing baseline unchanged. All four imported specification hashes and
both dependency manifests remain unchanged. No tests were skipped or failed
in the final suites. These local results are not hosted CI or live acceptance.

Package verification compares all 89 application modules, embedded README bytes
and 266 required source-distribution files with the checkout. Its local receipt
is `/tmp/grid-i8-ccxt-numbers-package-verification.json`; distribution artifacts
are under `/tmp/grid-i8-ccxt-numbers-dist/`. Generated data and databases must not
enter either package. These local `/tmp` paths are evidence pointers, not Git
artifacts or guaranteed permanent storage.

## Limits and rollback

There is no new private normalizer, sender, account configuration or migration.
The unfinished uncommitted private-adapter draft was removed during review.
Outgoing BingX numeric conversion, venue client-ID policies, private pagination,
stream ordering, actual fee evidence and integration with the mock ledger remain
separate work. Passing OKX parser fixtures does not certify all seven venues.
No new exchange request, credentials, orders, transfers or deployment occurred.
The original observed 48-hour gate and I9 remain open; hosted CI remains pending
under the unchanged account lock and must not be retried without new evidence.

Rollback is a normal revert of this scoped source/test/documentation increment;
no database downgrade or rewrite is involved. A revert restores the earlier
custom Decimal configuration and does not establish that it is preferred or
ready for private trading. Preserve the completed public-hour evidence unchanged.
