# Exchange capability registry and I3 probe procedure

I0 makes no exchange network calls and uses no trading credentials. No venue
behavior below has been tested. A package import or advertised CCXT `has` flag
is not an observed venue/account capability. The reproducible package version
is recorded in `uv.lock` and the iteration evidence; every future probe must
also record its installed distribution version and upstream source identity.

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
