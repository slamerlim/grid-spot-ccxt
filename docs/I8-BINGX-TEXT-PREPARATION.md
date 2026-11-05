# I8 offline BingX decimal-text preparation

The previous pinned SDK audit showed that fractional BingX LIMIT price/quantity
become binary floats after CCXT precision formatting. The large invented price
`123456789.12345677` changes to `123456789.12345678`. This follow-up tests a
lossless candidate inside the existing guarded offline diagnostic. It does not
install a sender, patch the dependency, or change venue readiness.

Base: `4e47943a56125fa308eab239bd651c0f0ca20530`, branch
`i8/public-paper-hour`. Owner authorization remains offline/mock development
after the completed public hour; no further public or private exchange calls.
GitHub Free only, local validation, hosted CI account lock unchanged.

## Implementation and boundaries

`exchange/bingx_numeric.py` accepts bounded positive plain ASCII decimal strings.
It preserves their exact text and refuses floats, integers, Decimal objects,
booleans, exponent notation, whitespace and values outside the existing domain
range. It never reconstructs an intended number from an already rounded float.

Only the diagnostic's disposable BingX subclass overrides `parse_to_numeric`,
using this string-preserving helper. It requires explicit fixture mode and pinned
CCXT **4.5.91**. The installed SDK and other client classes remain unchanged.
The existing `request` interception and lower `sign`/`fetch`/`fetch2` refusals
remain. Each client closes before the next is created. No financial ledger opens.

After capture, independent validation requires the exact spot endpoint, POST,
invented symbol, LIMIT type, side, fixture ID and complete six-field payload.
Both decimal values must equal the input values without floats. Unexpected
rounding, symbols, IDs, type changes or extra parameters fail before publication.
The resulting immutable query text is independently encoded with the standard
library and compared with the pinned SDK's `keysort`, `parse_params`, `rawencode`
and `urlencode` steps. These are the pure encoding helpers inspected in BingX's
signing code; **`sign()` itself is never called successfully**. No timestamp,
HMAC, signature, credentials, URL, headers or authenticated wire message is made.
Fixture characters need no URL escaping; broader encoding is not established.

This is deliberately a fixed experiment, not a reusable production gateway.
The identifier bounds and invented metadata prove no venue legality, minimum
lot, fee, account mode or ID acceptance. Internal ledger IDs remain unchanged.
Actual signing, venue acceptance and reconciliation remain untested. The runtime
executor remains synthetic-only and all venues remain **NOT_READY**.

## Reproduction and report interpretation

```sh
uv run --locked python -m grid.exchange.order_audit --bingx-text --code-revision "$(git rev-parse HEAD)" --output /tmp/grid-bingx-text-new/report.json
```

The only new CLI option is the fixed `--bingx-text` trial. Existing exclusive
output and provenance safeguards apply. There are 24 sequential calls: twelve
unmodified controls and twelve candidates, paired by BUY/SELL, three numerical
pairs and two fixture IDs. All twelve candidates must pass; the eight fractional
control failures remain visible separately. No failure is repaired or discarded.

The outer report remains `grid-research-run-v1`, operation `sdk-order-audit`;
the inner schema is `grid-bingx-text-preparation-v1`. Exit 0 means the candidate
fixture checks pass, not that controls pass or a venue is ready. Exit 2 means
unexpected/refused/incomplete preparation, with no partial successful report;
130 means interrupted. The original command without the new flag retains its
91-case audit and negative findings unchanged.

## Validation evidence

Focused checks cover original SDK findings, the complete paired trial, exact
query decoding, no global SDK modification, changed/extra request fields,
rounding and float injection, changed encoding, input bounds, forbidden lower
paths (including from an encoding helper), client close, version/venue/mode
confinement, sanitized failures and exclusive/all-or-fail publication.
Tests independently deny TCP; the Python guards are not an OS sandbox for
arbitrary modified dependency code.

The fresh standalone candidate trial exited **0**: all 12 candidates passed,
with eight fractional control failures retained, 24 fixture calls and **zero
external requests**. The original 91-case audit exited **1** as expected; its
entire result payload equals the previous final audit, including every negative
finding. Both ran while the full test suites were active, so elapsed times are
observations, not a performance comparison.

Artifacts and CLI receipt: `/tmp/grid-i8-bingx-text-evidence/`.

| Artifact | SHA-256 |
|---|---|
| `candidate.json` | `d3d0cc46c99b681a869c1837000bc3b6a4b05e9ea5622bec7fa82e48c3efe167` |
| Candidate result payload | `3a76bc030a40de0554af0600c22f5645d5c326e565cc7046504b8afe65b4679f` |
| `baseline.json` | `1bc008dbff74879af2ff92e0cc4011550c0e1a6cb556f2c361160bce5dabffb4` |
| Unchanged baseline result payload | `f6ebf17ee5118011911ad1e7b02924bb0baefc9d418b452a27dc049a9d0a581d` |

The reports truthfully record the base commit with uncommitted application
changes. Do not relabel them as measurements of a subsequent clean commit.
Standalone wrapper elapsed times: 6.653265 seconds candidate, 23.633935 seconds
baseline. No order was signed or submitted.

Both full suites completed on the unchanged final application source:

| Python | Result | Pytest elapsed seconds |
|---|---|---:|
| 3.12.16 | 1,246 passed; zero failed/skipped | 480.32 |
| 3.13.18 | 1,246 passed; zero failed/skipped | 494.07 |

`/tmp/grid-bingx-text-check.py` ran `python -m pytest -q` in the existing locked
environments selected by `UV_PYTHON` and `UV_PROJECT_ENVIRONMENT`. Logs and
receipts: `/tmp/grid-i8-bingx-text-checks/`, stems
`3.12-1793846046996269120` and `3.13-1793846046989183841`. Wrapper elapsed
times were 480.820531 and 494.590854 seconds. Before/after application SHA-256
matches both standalone reports:
`a31075eb15f5121ea4411f7c616a61c7c30a50566cd8c1e0ed65c02a3e494e5e`.
All validation process handles completed. The earlier focused run passed 78
tests before the final additional encoding-path refusal test; both full suites
include that final case, for 46 new tests in this increment.

Ruff lint/format passed (256 files), strict mypy passed (191 files), Bandit
passed on `src`/`scripts`, and the lock check resolved 66 packages. The locked
full-group dependency advisory audit reported no known vulnerabilities.
Secret scanning covers all 265 tracked/new files with the unchanged baseline.
All four specifications still match their original attachment bytes and hashes.
Dependency manifests, financial schemas and static network-import/write
exceptions remain unchanged.

Final wheel/source packages are built after documentation using `uv build
--no-sources`, outside Git under `/tmp/grid-i8-bingx-text-final-dist/`.
`/tmp/grid-bingx-text-packages.py` checks all 87 application modules against
wheel bytes, the wheel's embedded README, required source archive bytes and the
absence of generated database/market-data payloads. Its artifact hashes and
counts are retained in `/tmp/grid-i8-bingx-text-package-verification.json`.
Final secret scan and `git diff --check` precede publication. Local checks are
not a hosted CI pass; no PR or new CI run is requested.

## Remaining work and rollback

Production integration still requires a separately reviewed command boundary,
durable venue ID encoding/lookup, account options, complete market constraints,
actual fees/private events and authorized venue evidence. This trial supplies
no authority to spend a reservation or send this text. The observed 48-hour I8
gate, intended-first-venue evidence, I9 and deployment remain open.

Rollback is an ordinary scoped revert; no database migration, financial state,
dependency, configuration or installed service changed. Preserve evidence and
the original inputs. Publish with `[skip ci]`; do not retry the known hosted-CI
lock or use paid features.
