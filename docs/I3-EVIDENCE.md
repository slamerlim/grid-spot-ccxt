# I3 local validation and public discovery evidence

Recorded 2026-11-04 local date / 2026-11-03 UTC. The owner explicitly approved
**"Approve I3 with local validation"**, including bounded public market/book
probes. Authenticated access, orders, cancellations, transfers and deployment
were outside that approval and were not performed.

**Result: the I3 local implementation/mocked gate passes. Public metadata was
observed on seven venues and validated books on six; XT's book remains rejected
and unresolved. Hosted CI remains PENDING. LIVE remains DISABLED.**

## Revision and scope

- Branch: `i3/read-only-adapters`.
- Base: `9609ddff35211a90f250c76604b257c9de5f0202` (published I2 handoff).
- The implementation and this evidence are published together; the final chat
  handoff records the exact commit after remote verification. Reproduce using
  that commit, not an assumed default branch or a future branch tip.
- PR: not created. Git SSH publication is available; no authenticated GitHub PR
  API tool/CLI was available. No unsupported claim of PR creation or merging.
- No workflow changes, reruns, dispatches or paid GitHub features. Use `[skip ci]`
  while the known account lock remains unchanged; a skipped run is not a pass.

Added `grid.exchange` (observation types/mapping, bounded client pool, public
CCXT/HTTP transport, pure private contracts and explicit probe module), plus
63 exchange test cases. The existing 270 tests remain. Doctor remains offline.
The static network-import guard now permits only ccxt.pro/aiohttp in the dedicated
transport; order/process execution prohibitions remain. `aiohttp==3.14.5`, already
locked transitively through CCXT, is now an explicit dependency; no installed
dependency version changed. The four imported specifications, I1 strategy,
I2 ledger, legacy tree and datasets were not changed.

## Validation actually executed

Environments: uv 0.13.7; `/tmp/grid-i2-py312` uses Python 3.12.16 and
`/tmp/grid-i2-py313` uses Python 3.13.18. Both link the I2-approved mock SQLite
3.53.1 source build. Environment names are retained from I2; they are isolated
test environments, not production deployment approvals.

| Check | Result |
| --- | --- |
| Full pytest, Python 3.12.16 | **333 passed**, 0 failed/skipped; 16.39 seconds |
| Full pytest, Python 3.13.18 | **333 passed**, 0 failed/skipped; 19.20 seconds |
| Strict mypy, application and tests | PASS, 43 source files |
| Ruff lint / formatting | PASS; 64 files formatted |
| `uv lock --check` | PASS, 65 packages |
| Bandit | PASS, no findings or suppressions |
| Tracked/staged secret scan | PASS; baseline unchanged, no new allowlists |
| Dependency advisory audit | PASS; no known vulnerabilities found |
| Wheel / sdist build | PASS; both built successfully |
| Hosted GitHub Actions | PENDING / known account lock; not retried |

Commands are the README verification commands. For this host prefix uv with
`UV_PYTHON=3.12.16 UV_PROJECT_ENVIRONMENT=/tmp/grid-i2-py312` and use
`/tmp/grid-tooling/uv`; substitute 3.13.18 and `/tmp/grid-i2-py313` for the second
suite. Advisory checks contact advisory services; pytest disables Internet sockets.
Async fixtures permit only the local Unix socket needed for asyncio's wakeup pipe
and explicitly verify that TCP remains blocked.
The actual offline doctor was also run with `config.example.yaml`: normal report
exit 0, I3 label, LIVE DISABLED and ready false. The advisory tool emitted its
generic warning about the no-hash requirements export; installation remains
governed by the hashed single `uv.lock`. An initial formatting check identified
one boundary-test layout, which was formatted before the final passing check.

Development failures were fixed before the passing results: the real CCXT book
parser ignored its numeric mode and returned floats; BingX adds a public timestamp;
KuCoin passes empty headers; Bitfinex raw `info` is an array; and its amount
precision overrides the exchange-wide mode. The regression tests use the actual
pinned SDK. A further review fixed failed-refresh cache resurrection: after a
refresh fails, an older transport cache cannot be stamped as a fresh generation.

## Test evidence and limits

| Area | Executed checks |
| --- | --- |
| Financial normalization | Decimal literals beyond float precision; no float/nonfinite/oversized input; tick/significant/decimal modes; raw/unified identity; missing minima/maxima/fees; explicit side constraints; verified evidence/generation required |
| Market eligibility | Derivative/settlement/contract rejection; suspicious leveraged products and HTX ETP tags; unknown product evidence cannot become approved; unrepresented Bitfinex/XT constraints block strategy conversion |
| Book validation | Positive noncrossed sorted unique levels; finite depth; symbol identity; source/local clock distinction; missing, future and stale source timestamps refused by freshness gate |
| Ownership/lifecycle | Same pool returns one client; generation/TTL; busy refusal; request timeout and cancellation; close in flight; caller cancellation preserves cleanup; failed close is not silently successful |
| Actual pinned CCXT | All seven real parsers/signers run on synthetic HTTP; exact normalized prices; Bitfinex raw UST mapping and amount formatting; cache reuse; budget exhaustion before a third wire call |
| HTTP/security | Exact GET/path/host/query/header/body allowlist; bounded decompressed response; redirect refusal; no environment proxies; no cookies; 429/403/5xx/timeout/malformed classification; no retry; sanitized errors |
| Recovery boundary | Failed metadata refresh invalidates old rules; rate-limit latch prevents old CCXT cache from being reintroduced; no wallet observations enter I2 funding or posting |
| Synthetic private data | Missing balance values remain unknown; actual fee currency/rebates preserved; missing fees stay missing; duplicate trades remain available for scoped reconciliation; external IDs retained; status-only orders create no fills |

I3 does not claim I4's private-stream recovery supervisor, actual private
pagination, account-scoped event deduplication or execution fault scripts.
Those remain separately gated. No actual account fee tier, settlement currency,
withdrawal permission or dedicated funding ownership was tested. No financial
database was created except existing temporary synthetic fixtures in the suite.

## Actual public pass

Executed once:

```sh
/tmp/grid-i2-py312/bin/python -m grid.exchange.probe --public > /tmp/grid-i3-public-probe.json
```

The generated report remains outside Git. Its original SHA-256 is
`039f20bfa6250d96cc1f586745b992011101cbde4ae6603cb644b62a35d2a782`.
The report contains public normalized observations and source fingerprints, no
credential/account state. Curated results and latency/size tables are in
`EXCHANGE_CAPABILITIES.md`. UTC interval: 22:23:12–22:23:20, 2026-11-03.
Exactly 14 public GET attempts, all HTTP 200; seven metadata observations;
six normalized books; XT book MALFORMED. All clients closed. No additional
public retry, private request or WS subscription followed.

The report is a historical observation, not a live configuration. The final
source review corrected Bitfinex amount precision, added separate HTX LIMIT
side projections and fixed BingX timestamp-key reporting **offline**. The
registry explicitly marks these post-probe/source-backed corrections; the
original report is not rewritten to pretend those were live-observed checks.
XT's rejected raw book was not retained; its precise failure cause is unknown.

The pinned PyPI wheel SHA-256 in `uv.lock` is
`883d36142f3921a7383b413851915a2ea6e55ff3dce7e1a8af3856c7a5fa6b72`.
Installed source fingerprints recorded during the probe include:

| Installed file | SHA-256 |
| --- | --- |
| ccxt/base/exchange.py | d25df201c2e9aaa933f06ac0e585d3be838bd22019d68ad08dbe1828cae3676f |
| ccxt/async_support/base/exchange.py | 48fea8a578a7325d9f18206a64ce1e84770da8b0d722b26d783e65fc4c6fd680 |
| ccxt/async_support/okx.py | 5115b54fe79096abc9d739c601e7588f45f5bcfed5c5b84c23668cb063f821b7 |
| ccxt/async_support/htx.py | e44639b206a2f228333f21b6e5831e74ce27eb3e89270b9e40e051ab0e6433bc |
| ccxt/async_support/gate.py | 51ebdd0e8dfa24ec2e62ca00cfa6d7186b0520b98c4d416e71b27ed337c23052 |
| ccxt/async_support/xt.py | 2bc86106983882310aa1a39191381979f9129e006ca4d67265070172a7ec4216 |
| ccxt/async_support/bingx.py | 0533fd790542dcd91511fc88d89dc9373788f8242ae0f7a0ebef6d3130773760 |
| ccxt/async_support/kucoin.py | 1ab80ce72df3221b0b58b42bf3c0ddeabbe07c0b652b6c86c591ddd035efb2c4 |
| ccxt/async_support/bitfinex.py | 74c296972ef2bed6ee81988253b8fca7f05a45c39d95ce57c692f06fb5c9ee51 |

The local report additionally records each ccxt.pro venue-file fingerprint. These
identify installed source; no unverified upstream Git commit is invented.

## Review decision and remaining gates

Recommend review of the proposed **mock-only I4** scope in `I4-PLAN.md`.
Do not infer approval from this recommendation. XT book diagnostics, compound
precision/offset/protection rules, actual fees/accounts/private events, broader
symbol profiles, hardware resource benchmarks and hosted CI remain open.
Production readiness is **HOLD for all seven venues**. I8 soak and I9 per-venue
canary authorization remain mandatory.

Rollback: revert the scoped I3 code/docs/lock commit(s) on a branch and restore
the matching locked environment; retain user preferences and authorization
history. No external order, deployed database or service was changed.
