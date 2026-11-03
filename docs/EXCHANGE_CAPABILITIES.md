# Exchange capability registry and I3 probe procedure

Current evidence is the approved I3 public pass below. The I0 table is retained
as historical evidence. A package import or advertised CCXT `has` flag is not
an observed venue/account capability. All authenticated and trading behavior
remains NOT_TESTED; every venue remains NOT_READY.

## I3 observed public pass

One serial pass, **2026-11-03 22:23:12–22:23:20 UTC** (2026-11-04 locally),
Python 3.12.16, official CCXT **4.5.91**, selected unified symbol **BTC/USDT**.
Exactly **14 HTTP GET attempts**, all HTTP 200, no retry, credentials or WS.
Each client was reused for metadata/book and explicitly closed. Detailed bounds,
source identity and reproducibility are in `I3-DESIGN.md` / `I3-EVIDENCE.md`.

| Venue | Raw market ID | Metadata | Book validation | Spot account/options | Private reads / WS | Trading readiness |
| --- | --- | --- | --- | --- | --- | --- |
| OKX | BTC-USDT | OBSERVED | OBSERVED, 20/20 levels | Public spot profile; account UNKNOWN | NOT_TESTED | NOT_READY |
| HTX | btcusdt | OBSERVED | OBSERVED, 20/20 | Public spot profile; account UNKNOWN | NOT_TESTED | NOT_READY |
| Gate | BTC_USDT | OBSERVED | OBSERVED, 20/20 | Public spot profile; account UNKNOWN | NOT_TESTED | NOT_READY |
| XT | btc_usdt | OBSERVED | REJECTED: MALFORMED | Public spot profile; account UNKNOWN | NOT_TESTED | NOT_READY |
| BingX | BTC-USDT | OBSERVED | OBSERVED, 20/20 | Public spot profile; account UNKNOWN | NOT_TESTED | NOT_READY |
| KuCoin | BTC-USDT | OBSERVED | OBSERVED, 20/20 | Classic public spot profile; account/UTA UNKNOWN | NOT_TESTED | NOT_READY |
| Bitfinex | tBTCUST | OBSERVED | OBSERVED, 25/25 P0; source timestamp UNKNOWN | Public spot profile; exchange account UNKNOWN | NOT_TESTED | NOT_READY |

OBSERVED means this individual response passed the stated public checks. It does
not establish account eligibility, sustained availability, freshness under an
operator policy, executable minimum-lot depth, complete rules or correct fees.
XT returned HTTP 200 and parsed metadata, but its book failed normalized validation.
The sanitized run did not retain the rejected raw book or detailed shape, so its
precise cause is **not established**. Do not silently truncate it or label it a
venue outage. Further targeted diagnostics need a new explicit bounded probe
scope; no additional request was made during this pass.

### Public rule observations

These are the selected fields surfaced by pinned CCXT, not complete verified
trading rules. `?` means unknown, including missing maxima. Fees are unverified
CCXT estimates and may be library defaults; none is an actual account fee source.

| Venue | Price precision | Amount precision | Generic amount min / max | Generic cost min / max | Maker / taker estimate |
| --- | --- | --- | --- | --- | --- |
| OKX | tick 0.1 | tick 1e-8 | 0.00001 / 9999999999 | ? / 1000000 | ? / ? |
| HTX | tick 0.01 | tick 0.000001 | 0.00001 / 1000 | 1 / ? | .002 / .002, hardcoded |
| Gate | tick 0.1 | tick 0.000001 | 0.000001 / ? | 3 / ? | .002 / .002, unverified |
| XT | tick 0.01 | tick 0.00001 | ? / ? | 1 / ? | .002 / .002, unverified |
| BingX | tick 0.01 | tick 0.000001 | ? / ? | .5 / 7588825.16352942 | .001 / .001, library default |
| KuCoin | tick 0.1 | tick 1e-8 | 0.00001 / 10000000000 | .1 / 99999999 | ? / ? |
| Bitfinex | 5 significant digits AND at most 8 decimals | 8 decimal places | .00004 / 2000 | ? / ? | ? / ? |

Bitfinex's precision row includes a **post-probe correction from installed source
and offline formatter tests**: its amount formatter overrides the global
significant-digit mode. Its compound price constraint is not representable by
the I1 Precision type, so strategy conversion is blocked. The raw/unified quote
mapping UST → USDT is retained. See the official
[Bitfinex precision rules](https://docs.bitfinex.com/docs/introduction#price-precision).
HTX's additional side-specific LIMIT fields are projected by the final mapper
and mock-tested; the public pass above reports only its captured generic fields.
XT offset/protection filters and other venue-specific price bands remain review
gaps. No invented zero minimum, unbounded maximum or fee currency fills these gaps.

### Request evidence and budgets

The actual endpoint paths/query-key allowlist is in `ccxt_transport.py`. Markets
use `load_markets` with spot-only adaptations; books use `fetch_order_book` with
20 levels (Bitfinex: 25/P0). No pagination. The small total-attempt cap and one-second
minimum interval apply in addition to CCXT's limiter. Costs below were inspected
with the pinned SDK and synthetic responses; they are **library cost units**,
not independently observed venue/account quota allowances.

| Venue | Market/book CCXT costs | Base interval ms | Catalog rows received → retained | Response bytes market / book | Elapsed ms market / book |
| --- | --- | --- | --- | --- | --- |
| OKX | 1 / .5 | 110 | 1 → 1 | 1083 / 1364 | 33 / 905 |
| HTX | 1 / 1 | 100 | 2175 → 1 | 1459898 / 910 | 113 / 796 |
| Gate | 1 / 1 | 50 | 1 → 1 | 514 / 951 | 68 / 888 |
| XT | 1 / 10 | 100 | 1 → 1 | 1409 / 4736 | 154 / 825 |
| BingX | 1 / 1 | 100 | 1 → 1 | 356 / 1071 | 99 / 884 |
| KuCoin | 4 / 4 | 7.5 | 978 → 1 | 699357 / 1080 | 86 / 901 |
| Bitfinex | 2.7 / 1 | 250 | 194 → 1 | 17615 / 1047 | 26 / 548 |

Elapsed values include the application's minimum-interval wait, not just network
latency. HTX/KuCoin/Bitfinex returned bounded spot catalogs; only one selected row
entered the market cache. BingX adds a public `timestamp` query field to both
requests; the original report omitted that key from its parameter-name summary,
which the final recorder corrects. No query value is printed in diagnostics.

All seven installed classes advertise fetchMarkets, fetchOrderBook, fetchBalance,
fetchOpenOrders, fetchMyTrades, watchOrders and watchMyTrades. fetchTradingFee is
advertised for six and false for Bitfinex. This is **advertised only**, including
XT private WS. No authenticated read, subscription, fill replay or account fee
lookup was performed. The intended bounded REST recovery policy is documented
in `I3-DESIGN.md`; its private runtime and fault supervision belong to I4.

## I0 baseline

Offline inspection of installed official **CCXT 4.5.91** found all seven class
IDs below in both `ccxt` and bundled `ccxt.pro`; both modules imported
successfully. No exchange instance was created in that check. Class presence is
not a validated spot configuration. All enabled flags remain false in I0.

| Target venue | Class ID present in CCXT 4.5.91 | Spot account/options | Public metadata/book behavior | Private orders/trades/balance/WS behavior | Submit/cancel/client-ID behavior | Production readiness |
| --- | --- | --- | --- | --- | --- | --- |
| OKX | `okx` | UNKNOWN | NOT_TESTED | NOT_TESTED | NOT_TESTED | NOT_READY |
| HTX | `htx` | UNKNOWN | NOT_TESTED | NOT_TESTED | NOT_TESTED | NOT_READY |
| Gate | `gate` | UNKNOWN | NOT_TESTED | NOT_TESTED | NOT_TESTED | NOT_READY |
| XT | `xt` | UNKNOWN | NOT_TESTED | NOT_TESTED | NOT_TESTED | NOT_READY |
| BingX | `bingx` | UNKNOWN | NOT_TESTED | NOT_TESTED | NOT_TESTED | NOT_READY |
| KuCoin | `kucoin` | UNKNOWN | NOT_TESTED | NOT_TESTED | NOT_TESTED | NOT_READY |
| Bitfinex | `bitfinex` | UNKNOWN | NOT_TESTED | NOT_TESTED | NOT_TESTED | NOT_READY |

## Evidence states and records

- **NOT_TESTED:** no actual call or insufficient authenticated evidence.
- **UNKNOWN:** required configuration/semantics have not been established.
- **SUPPORTED / UNSUPPORTED:** an explicit observed result with venue, account
  type, pinned version, method and timestamp; never inferred from `has` alone.
- **DEGRADED:** a tested fallback works under documented limits, while the
  preferred path is unavailable. REST fallback is itself tested first.
- **BLOCKED:** a required prerequisite prevents this probe or safe use.

Each observation must store: UTC timestamp; sanitized fixture/evidence ID;
Python/CCXT version and distribution hash or upstream commit; venue/class ID;
actual spot account type and options; unified/raw market identifiers; advertised
capability separately from observed result; method and safe parameter names;
latency, pagination, endpoint weight and quota; normalized result/schema and
failure category; applicable rule/fee revision; reviewer and remaining gaps.
Never record keys, signatures, tokens, raw authentication headers or unsanitized
account identifiers in public artifacts.

## Exact read-only I3 procedure, after its gate opens

1. **Preparation:** pin and inspect the official CCXT release; verify class
   existence and bundled `ccxt.pro` import locally. Record advertised `has`
   values as claims only. Read the corresponding official exchange account,
   precision, fee, rate-limit and private-stream documentation. Confirm configured
   dedicated spot account ownership separately. Do not guess derivatives or
   margin defaults. Unknown account options block authenticated probes.
2. **Client lifecycle and budgets:** create one long-lived rate-limited client
   per venue/account, with explicitly verified spot options, finite timeout and
   concurrency budget. Reuse connections/market cache. Instrument client count,
   requests and retries. No order create/edit/cancel method is called in I3.
3. **Public market mapping:** run `load_markets` within quota; select an approved
   spot market for the configured quote. Record active/spot/type/contract and
   leveraged-token exclusions, raw/unified symbols, precision mode, amount/price
   precision, buy/sell amount and notional minima/maxima. Missing or contradictory
   constraints are UNKNOWN, not unlimited. Test formatting against synthetic
   boundary values independently; public metadata does not establish fee tier.
4. **Public book:** call `fetch_order_book` on that market with a bounded depth.
   Validate sorted positive bids/asks, depth, timestamp provenance, local
   monotonic receipt age, spread, available minimum-lot depth and malformed/stale
   response handling. Use no book observation as a directional trading signal.
5. **Authenticated reads, only if safely provided for I3:** explicitly approved
   trade-only credentials remain outside Git. Obtain actual balance/account
   identity, account fee tier/currency evidence, `fetch_open_orders`, available
   closed-order/order lookup and `fetch_my_trades` pages. Verify pagination,
   scoped trade IDs, cumulative quantities, fee/rebate fields and symbol mapping
   using existing known activity. Empty history proves a successful empty read,
   not fill semantics. Missing credentials leave these fields NOT_TESTED.
6. **Private streams:** with the same account/options, probe `watch_orders` and
   `watch_my_trades` where advertised, with bounded subscription count, event
   buffer and observation interval. Record authentication, snapshot/sliding-cache
   semantics, duplicates and reconnect behavior. Compare observed existing
   events with REST. No observed execution means execution-event fidelity is
   still NOT_TESTED; a quiet stream never proves absence of fills. I3 does not
   place an order merely to manufacture an event.
7. **Fallback and failure tests:** inject mock unsupported-method, disconnect,
   timeout, 429 and malformed-response scenarios; observe bounded backoff,
   degraded state and REST reconciliation scheduling. Exercise the real
   read-only fallback only within documented budgets. XT has no assumed special
   support: unknown/unsupported WS follows the same tested REST policy or stays
   disabled if safe recovery cannot be proved.
8. **Close and publish:** await subscription/task/client closure; verify no
   leaked clients/tasks and no secrets in evidence. Publish a per-method result
   for all seven rows, including exact reasons for unavailable tests. Upgrade
   evidence after a CCXT/account/options change; invalidate old approvals.

## What I3 cannot certify

Client-order-ID acceptance/uniqueness/lookup consistency, new-order wire
precision, post-only behavior, cancellation races, late fills, actual maker/taker
fees and unknown-submission recovery require I4 fake tests plus explicitly
approved later sandbox/canary observations. Read-only inspection can collect
historical evidence without proving these submission semantics. Sandboxes may
have different markets/minima/fees; label their evidence separately. A live
venue remains gated by I8 readiness and I9 per-venue authorization even after
all read-only probes succeed.
