# I8 follow-up — offline pinned CCXT order-request audit

Implemented 2026-11-05 from base `1268e30705a341cbcf8e6b969eb29874ab3b4c26`
on `i8/public-paper-hour`, under the owner's instruction to continue offline
development. This adds a diagnostic command and regression tests for pinned
CCXT request builders. It does not add a trading gateway, use an account, sign
a request, send HTTP or change financial identities.

## Purpose and experiment

The existing execution tests run venue precision helpers and capture a
`LimitRequest` in `SyntheticExchange`. They cannot establish what the SDK later
puts in its exchange-specific request. The new audit invokes the asynchronous
`ccxt.pro` inherited builders on invented metadata and intercepts their first
`request()` invocation, before authentication/signing or HTTP encoding.

This is a pre-signing SDK observation, **not an authenticated wire or venue
acceptance test**. No response is fabricated to imply submission. Every venue
remains NOT_READY. The normal executor still accepts only the in-memory synthetic
exchange, and its XT/Bitfinex rule refusals remain intact.

With the locked environment and a new output path outside Git:

```sh
uv run --locked python -m grid.exchange.order_audit --code-revision "$(git rev-parse HEAD)" --output /tmp/grid-sdk-audit-new/report.json
```

Only a revision label and exclusive output path are accepted. There are no
credential, account, market, order-param, live or sandbox arguments. Existing
protected-output/provenance behavior applies. The inner schema is
`grid-sdk-order-audit-v1`, with operation `sdk-order-audit`.

Exactly 13 cases per venue, 91 total:

- One default-option BUY with integer price/amount and an invented short ID.
- Twelve explicit fixture cases: BUY/SELL × three numeric pairs × existing
  64-character internal digest or a 16-character diagnostic candidate.
- Numeric pairs: `(100, 1)`, `(12.34567891, 0.12345678)` and
  `(123456789.12345677, 0.12345678)`, supplied as decimal strings.

Invented BASE/QUOTE metadata declares eight-decimal price/amount ticks, except
Bitfinex's five-significant-digit price and eight-decimal amount conventions.
Invented HTX spot account `101` is preloaded. This attests to no real market,
minimum lot, fees or account type. Fractional Bitfinex prices deliberately violate
that fixture's precision to expose rounding; production intents must already
pass independent domain precision validation.

Explicit fixture options select OKX's single-order builder, Gate's declared
non-unified mode, and KuCoin's declared non-UTA/non-HF mode. These are invented
diagnostic assumptions, not real-account settings. The short ID is not a durable
encoding, migration or suggestion to truncate identities. Ledger IDs are unchanged.

## Boundary and result interpretation

Each case owns one client and awaits close before the next case. Its subclass
overrides `request`, `sign`, `fetch` and `fetch2`. `request` captures one bounded
plain payload and raises a private `BaseException` sentinel: an SDK
`except Exception` fallback must not swallow interception and assume account
mode after refused discovery. Lower signing/transport guards raise a separate
sentinel and invalidate the entire audit. CLI input cannot disable those guards.
Test sockets independently deny TCP.

Capture permits one flat object or one-item batch, at most 24 fields, bounded
keys/scalars and 16 KiB serialized bytes. Unexpected headers/body, nested payloads,
larger batches or a second capture refuse the experiment. Builder execution has
a five-second asynchronous timeout; awaited close has a three-second timeout.
These are not process CPU/RSS or OS network quotas, nor a sandbox for arbitrary
modified dependency code. Captured dictionaries are detached from the SDK.

Checks compare endpoint/API namespace/method, spot LIMIT fields, exact price/base
amount, expected client encoding, literal ID preservation and the complete field
set. Numeric values may be exact strings or integers; floats and booleans fail.
Bitfinex SELL amounts use their explicit negative sign. A prefix transformation
and an unchanged internal ID are distinct checks.

Exit 1 publishes a complete audit with failed fixture checks. Exit 0 would prove
only these checks, never readiness. Unexpected failure returns 2 without a
partial successful report; caught interruption returns 130. Existing outputs
are never overwritten. Errors omit arbitrary exception/argument values.

## Observed CCXT 4.5.91 findings

The fresh standalone run completed all 91 cases in **23.6693 seconds**, exited
**1** and preserved its findings. Within Python, it captured 83 order POST
attempts and two account-discovery GET attempts; six cases were refused before
capture. **Zero requests reached signing or external transport.** These method
attempts inside guarded fixtures are not exchange calls or submitted orders.

| Venue | Observed fixture behavior | Remaining implication |
|---|---|---|
| OKX | Default one-item batch; explicit single-order option preserves tested strings and LIMIT/cash fields | Passing an ID through the SDK proves no venue length/charset acceptance, uniqueness or lookup |
| HTX | Uses preloaded fake spot account; tested numbers remain strings and type is side-specific LIMIT | Actual account lookup, ownership and ID semantics untested |
| Gate | Default first attempts account-mode GET; explicit mode avoids it; six full-digest cases refused; six short IDs gain `t-` | Needs verified account mode and durable collision-controlled external-ID mapping, not silent truncation |
| XT | Tested spot LIMIT values remain strings with GTC | Existing offset/protection and full precision blockers remain; fixtures cannot remove them |
| BingX | Eight fractional cases convert both numbers to floats; the large price loses a decimal quantum | Earlier precision helpers do not protect later SDK conversion; no float-based sender is approved |
| KuCoin | Default first attempts account-mode GET; declared non-UTA mode reaches spot builder with strings | Actual account mode, endpoint eligibility and ID semantics untested |
| Bitfinex | SELL amount negated; eight nonconforming prices rounded; type is EXCHANGE LIMIT | Compound precision remains independently validated; string CID pass-through proves no venue legality |

BingX changes `123456789.12345677` into numeric `123456789.12345678` before
signing. Fractional quantity `0.12345678` becomes a float even where its shortest
decimal representation looks unchanged. The inspected `parse_to_numeric` directly
chooses `float` or `int`; an earlier successful formatter cannot establish
preservation. No override, dependency patch or new numeric policy is added here.

Bitfinex rounds `12.34567891` to `12.346` and the large price to `123460000`.
This supports refusing invalid reserved values, not accepting those prices.
The existing boundary refuses formatter-induced changes and keeps that venue
blocked. Passing any fixture is not minimum-lot or venue certification.

Artifacts are under `/tmp/grid-i8-sdk-order-audit-evidence/`: report, CLI receipt
and summary. Report SHA-256:
`5aa4a8c8290a38dd76e9ac4b3c85256360656dfe3ab0968b74d2fd29378da146`.
Application SHA-256:
`e07753ddb3f9dc6f67247768f804ed7e6ed91aec6fc90aeb9ade87b900aeda84`.
The supplied base revision truthfully has uncommitted source mismatch. Each case
also records its installed async builder module/file hash. Disk fingerprints are
not loaded-dependency attestation. Preserve this historical provenance after commit.

## Validation and remaining work

Twenty-nine new tests passed in 28.36 seconds before the full suites. They cover
the 91-case matrix, report hashes, refusals/conversions, both sides, lower-path
guards, cancellation cleanup, bounded detached capture, coercion, changed
type/account/fields/IDs/endpoints, sanitized errors, exclusive outputs and no
partial report after later-case failure. The existing static/execution boundary
group passed: 27 tests in 1.21 seconds.

The static application guard permits only this diagnostic's six-argument SDK
call with literal `limit` and its two CCXT imports. Other exchange-write
restrictions remain. That exception alone is not effect analysis; guarded-client
and TCP-denied runtime tests provide separate evidence.

Both full suites completed with frozen source, before an identifier-only scanner
correction:

| Python | Result | Pytest elapsed seconds |
|---|---|---:|
| 3.12.16 | 1,200 passed; zero failed/skipped | 465.92 |
| 3.13.18 | 1,200 passed; zero failed/skipped | 484.03 |

`/tmp/grid-sdk-audit-check.py` invoked `python -m pytest -q` in the existing
locked environments and recorded matching before/after application SHA-256
`e07753ddb3f9dc6f67247768f804ed7e6ed91aec6fc90aeb9ade87b900aeda84`.
Receipts/logs: `/tmp/grid-i8-sdk-order-audit-final-checks/`, stems
`3.12-1793844502896118044` and `3.13-1793844502889833629`. Wrapper elapsed
times were 466.413122 and 484.543976 seconds. Despite that directory name, these
receipts describe the pre-rename source, not the final byte hash.

The secret scanner then flagged eight assignments of API field names to a local
variable called `client_key`. Inspection confirmed literals such as `clientOid`
and `cid`, not credentials. Renaming it to `client_field` removed the findings
without changing the baseline or adding allowlist comments. An AST comparison
proves this was the only code change, preserving all API field literals and
behavior; `/tmp/grid-i8-sdk-order-audit-rename-proof.json` records that comparison.
Affected audit/static-boundary tests passed on the final source: **33 passed**
in 28.84 seconds on Python 3.12.16 and **33 passed** in 28.87 seconds on 3.13.18.
Both before/after application hashes are
`ba5a1451a166082b26112815b90a6916b1ede979e4c98c788dfa177f60c2f139`.
Receipts/logs: `/tmp/grid-i8-sdk-order-audit-focused-checks/`, stems
`3.12-1793845102052414912` and `3.13-1793845102043763846`. Their wrapper
elapsed times were 29.255098 and 29.666733 seconds. Full suites were not repeated
for this proven identifier-only change; final affected checks and capture were.

The fresh final audit also completed all 91 cases, with exit 1 for the same
retained findings and zero external requests. Its **entire result payload** equals
the original, including every captured request and check. Comparison receipt:
`/tmp/grid-i8-sdk-order-audit-final-comparison.json`; common payload SHA-256:
`f6ebf17ee5118011911ad1e7b02924bb0baefc9d418b452a27dc049a9d0a581d`.
Final report/receipt/summary are under
`/tmp/grid-i8-sdk-order-audit-final-evidence/`; report artifact SHA-256:
`73109ff9fac03e65ac8c5a53f33554717df6eb2dd1c7372760363761ad4e31c8`.
The final invocation overlapped focused tests, so its elapsed time is not a
standalone performance comparison. All validation process handles completed.

Ruff lint/format passed (252 files), strict mypy passed (189 files), Bandit and
the 66-package lock check passed. The full-group locked dependency advisory audit
reported no known vulnerabilities. The 262-file secret scan passed after the
rename. Original specification/attachment hashes, dependency manifests and
secret baseline remain unchanged. Packaging verifies source bytes after final docs.
Final wheel/source archives are under `/tmp/grid-i8-sdk-order-audit-final-dist/`,
built with `uv build --no-sources` using the corresponding wheel/sdist selections.
`/tmp/grid-sdk-audit-packages.py` verifies all 86 application modules in the wheel
and 257 required source/test/document/root-manifest files in the source archive,
with no generated database or market-data payloads. The artifact hashes and
verification result are in `/tmp/grid-i8-sdk-order-audit-package-verification.json`.
The earlier wheel directory contains pre-rename intermediate bytes, not this
final package. Final secret scan and `git diff --check` precede publication.

No financial schema, state transition, dependency or deployment template changes.
Rollback is a normal reviewed revert; no database migration or host service needs
reversal. Preserve generated evidence and original inputs.

Remaining integration needs lossless outgoing serialization, durable venue ID
encoding/lookup, account-mode evidence, complete constraints, actual fee/private
events and separately authorized venue observations. This command neither
implements nor authorizes that sender. The completed public hour is not extended.
Full observed I8, intended-first-venue evidence, I9 and deployment remain gated.
GitHub Free only; local checks are not hosted CI. Publish a scoped `[skip ci]`
commit under existing authorization without retrying the account lock or creating
a PR. No extra approval is inferred for account access or trading.
