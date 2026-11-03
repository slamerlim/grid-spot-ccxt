# I3 read-only boundary and observations

The owner approved I3 with local validation on 2026-11-04. This increment adds
public discovery and pure private-response contracts. It adds no authenticated
transport, execution service, financial database or deployment. I1 strategy and
I2 persistence contracts remain unchanged.

## Ownership and scope

One controller-owned `ClientPool` holds at most seven public clients. Repeated
lookups return the same object and CCXT rate limiter. There is no account/key
configuration in this implementation; each client is scoped to `public`.
Future authorized accounts must have explicit distinct ownership and cannot reuse
this public profile as an authenticated gateway.

`ReadOnlyClient` exposes only market loading, selected-book reads and closure.
Concurrent callers receive BUSY immediately, so no unbounded waiter queue exists.
Market cache reads reuse a generation until explicit refresh, with a monotonic
TTL. A refresh invalidates the old normalized cache before loading. No automatic
refresh, retries, pagination, background subscriptions or discovery loop exists.
Single-flight fan-in and prioritized runtime scheduling belong to I6/I4.

The application can represent up to eight explicitly selected symbols, but the
actual I3 HTTP profile intentionally permits **BTC/USDT only** on each venue.
Expanding the real profile requires endpoint/symbol review and corresponding
request-boundary tests; this increment is not a general universe scanner.

## Network controls

`ccxt_transport.py` is the only production module allowed to import CCXT/aiohttp.
It constructs official bundled `ccxt.pro` classes from pinned CCXT 4.5.91, using
their REST methods, parsers, signers and weighted limiter. No WS is opened.
The raw client stays internal. Application classes expose no arbitrary dispatch,
HTTP params, order, cancellation, transfer or withdrawal method.

Every actual HTTP request passes a separate exact HTTPS host/path/query/method
allowlist. Only two public GET routes per venue are permitted. Unexpected headers,
bodies, credentials, symbol/depth changes and derivative/private paths fail
before HTTP. BingX's generated public millisecond timestamp is explicitly checked.
Empty headers and XT's JSON content type are permitted. This is an application
boundary, not a sandbox against arbitrary code with access to Python internals.

The bounded HTTP implementation replaces CCXT's default `fetch`, whose response
text read has no application byte cap. It uses a reused aiohttp session with TLS
verification, one connection, environment proxies disabled, no cookies and no
redirect following. Response chunks are capped **after decompression**, before
JSON decoding. No raw errors, bodies, URLs containing query values or headers are
logged. Diagnostics retain fixed status codes, endpoint paths, parameter names,
response lengths and elapsed time only. HTTP 403 means access denied, not proof
that account credentials or payment are required.

Default probe limits: two HTTP attempts per venue, at least one second between
starts in addition to CCXT's limiter, 15 seconds per read including waits, 8 MiB
per response, 20,000 catalog rows, one retained symbol, 20 book levels per side
(Bitfinex P0: 25), and 60-second market cache TTL. The probe runs venues serially
with a 40-second per-venue envelope. A failed request consumes its budget and
halts further wire calls on that client. There is no retry on 429, timeout or
other failures. Closure has two bounded cooperative phases, at most three seconds
each for canceling the active call and closing the transport. Caller cancellation
does not discard cleanup. These are asyncio/cooperative bounds, not a guarantee
against a frozen interpreter, OS or a cancellation-resistant replacement transport.

All catalogs are byte/row bounded. HTX, KuCoin and Bitfinex expose the used rules
via a catalog response, so those responses contain other spot pairs. Only the
selected row reaches CCXT's market cache. No per-instrument loop, derivatives
catalog, all-books request or market-ranking scan runs.

## Pinned CCXT adaptations

Source inspection and offline tests found these version-specific requirements:

- Disable `fetchCurrencies`: loading markets otherwise invokes extra currency
  requests and can require authentication on some venues.
- OKX and HTX require explicit spot-only discovery options. OKX also receives
  `instId=BTC-USDT`.
- XT and BingX `fetch_markets` include derivatives unconditionally in this pin.
  Bind their existing `fetch_spot_markets` implementations instead and supply the
  selected raw symbol.
- Gate spot discovery also fetches margin metadata. Replace that read with an
  empty local margin list, and obtain the selected spot pair through the official
  single-pair endpoint. This does not establish margin/account support.
- KuCoin discovery defaults include derivatives and fee/ticker requests. Select
  spot, disable ticker-fee loading, and leave UTA/authentication unconfigured.
- Bitfinex discovery uses only `pub:info:pair`; no futures or margin catalogs.
  Its book uses aggregated P0 levels. Its parser fabricates a local timestamp;
  the normalized source timestamp therefore stays UNKNOWN.
  A final source review also found that amount formatting overrides the global
  significant-digit mode with eight decimal places. The corrected amount mapper
  is checked against the actual offline CCXT formatter. Prices combine five
  significant digits with an eight-decimal-place cap; I1 cannot express that
  intersection, so Bitfinex strategy conversion explicitly remains blocked.
  The original public probe's amount-mode projection is superseded by this
  source-backed correction; no additional public request was made.

JSON decimal literals decode as strings, followed by Decimal parsing. CCXT's
numeric converter is Decimal, with a narrow `safe_string` extension for Decimal.
Its base book-level parser nevertheless hardcodes `safe_float`; a narrow level
parser override preserves exact prices/amounts before CCXT sorts them. Tests use
a numeric JSON price with more significant digits than a float can preserve.
No `Decimal(str(float))` conversion is accepted at the financial boundary.
Duplicate JSON keys, nonfinite, oversized and malformed numbers are rejected.

These adaptations are version-gated and tested with the actual installed library.
A dependency upgrade requires rerunning the boundary fixtures and reviewing
changed source; an advertised `has` flag is never an observed capability.

## Financial observations and unresolved facts

The mapper retains venue, raw/unified currency and market IDs, generation,
precision mode and known/unknown bounds. Missing minima never become zero;
missing maxima never become unlimited. Generic public bounds remain distinct
from separately verified side-specific trading rules. HTX's explicit limit BUY
and SELL amount bounds are preserved independently when present (mock-tested).
The public pass preceded this extra HTX projection; its table reports generic
CCXT fields only, not observed verification of those side-specific fields.

Spot classification alone cannot prove a token unleveraged. Suspicious names and
HTX ETP tags are rejected; other products remain unverified. Conversion to an I1
Market requires fresh observation plus explicit reviewed product/rule evidence
for the same venue/raw ID/generation. Reviewed rules cannot weaken observed
bounds. No actual venue/account evidence supplied by I3 satisfies all live gates.
XT offset/protection filters are also marked unrepresented and block strategy
conversion until their venue-specific constraints have been implemented/reviewed.

Decimal maker/taker fields are **unverified CCXT estimates**, sometimes hardcoded
library defaults. They do not establish fee tier, currency, rounding, settlement
count, rebates or conversion prices, and are never converted automatically to
an I1 FeeRule. Missing actual fill fees stay missing. Books require positive,
unique sorted levels, a non-crossed spread, explicit depth bounds and matching
symbol. Shape validation does not itself assert freshness or executable depth.
Consumers must check monotonic receipt age and source age; unknown/future/stale
source timestamps fail the conservative freshness check. Minimum-lot depth and
net-fee eligibility await verified account fee/rule facts.

Pure private fixtures preserve missing balance components, external trade/order
IDs, signed actual fees and duplicates. They create no deposits or ledger fills.
I4 must establish account scope, deduplication, fee completeness and bounded
history pagination before posting financial events. An order status cannot
manufacture a fill. Wallet free balance never assigns bot ownership.

## Private-stream fallback contract for I4

All seven pinned classes advertise private order/trade streams, including XT;
none was authenticated or subscribed. The intended executor must treat an
unsupported, disconnected, quiet or timed-out stream as insufficient evidence:
pause new exposure, advance the reconciliation epoch, and perform bounded REST
order/trade reads under account-specific quotas. Resume only after complete
reconciliation. If REST cannot establish truth, remain blocked. Empty pages,
quiet streams and positive `has` values cannot prove absence of fills.

I3 tests error classification and synthetic private normalization; it does not
implement or claim to test a private subscription/reconciliation supervisor.
That supervisor and its fault scripts are explicitly in I4.

## Sources inspected

The installed CCXT source was inspected before implementation; source fingerprints
and the actual public observations are in `I3-EVIDENCE.md` and the capability
registry. Official documentation consulted on 2026-11-03 UTC:

- [CCXT manual](https://github.com/ccxt/ccxt/wiki/Manual) for client reuse, rate
  limiting, precision modes, market loading and public/private method contracts.
- [OKX API](https://www.okx.com/docs-v5/en/#rest-api-public-data-get-instruments).
- [HTX spot API](https://huobiapi.github.io/docs/spot/v1/en/).
- [Gate API v4](https://www.gate.com/docs/developers/apiv4/en/).
- [XT symbol information](https://doc.xt.com/docs/spot/Market/GetSymbolInformation),
  including offset tick/quantity and protection filters requiring later review.
- [BingX API](https://bingx-api.github.io/docs-v3/) was reachable but the rendered
  documentation body was unavailable; pinned source and mock/public evidence
  supplied the usable interface facts. Do not claim a full BingX docs review.
- [KuCoin symbols](https://www.kucoin.com/docs-new/rest/spot-trading/market-data/get-all-symbols).
- [Bitfinex configs](https://docs.bitfinex.com/reference/rest-public-conf).
- [Bitfinex precision rules](https://docs.bitfinex.com/docs/introduction#price-precision)
  confirmed the final formatter review findings.
