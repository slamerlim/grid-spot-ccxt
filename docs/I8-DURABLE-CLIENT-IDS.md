# I8 durable client-ID mapping and mock recovery

Base: `ee4ccfc1b47be3dc4496b14c243e05368780051f`, branch
`i8/public-paper-hour`. This implements another offline gap in PRD §§2/6,
ADR order identity and Q08/Q09/Q19. The owner authorized continued mock
development after the completed public hour. No additional exchange access,
credentials, trading or deployment is included. GitHub Free only; use local
validation and `[skip ci]` while the hosted CI account lock is unchanged.

Later API update: `I8-UNIFIED-RECONCILIATION.md` replaces the `WireOrder` mock
report described below with CCXT unified dictionaries and shared observation
parsers. This document retains the original increment's evidence; the durable
schema, identity and attempt contracts below remain unchanged.

## Identity and transaction contract

Internal order identities remain the original full 64-character digest of scope
and order number. New external IDs are independent **128-bit random values**,
encoded as 26 canonical lowercase base32 characters. There is no truncation or
replacement of internal IDs. `ClientIdPolicy` declares a bounded literal returned
prefix; a mock `t-` prefix exercises differing submitted/returned forms. No
target-venue policy is certified or enabled. Numeric-only IDs and other venue
conventions are outside this first text-ID implementation.

`persistence/client_ids.py` registers an immutable mapping through the existing
sole writer and audited request transaction. Schema **6** adds `client_ids`,
with a foreign key to the existing order and original registration request.
Unique indexes cover internal, submitted and returned identities within a
venue/account, across allocations and historical orders. A maximum of eight
collision attempts is allowed; exhaustion rolls back the complete registration.
Randomness is not a claim of impossible collisions: the ledger enforces local
uniqueness. Cross-database or independently cloned owners are not coordinated.

Registration requires an existing, unattempted, queued placement. It commits
before the separate `attempt_once` claim. A crash between them leaves an unused
stable ID and no permission to send. A lost claim acknowledgement remains
uncertain under the existing no-retry rule. Registration receipt replay cannot
authorize a send. Already attempted orders without a mapping cannot acquire a
new identity retroactively. Existing mappings remain after terminal orders and
cannot change policies, even when the caller uses a new request ID.

The stored payload, indexed projection and original receipt digest are checked
on lookup and during streaming startup verification. Backup semantic hashes
include the new table. Full mapping rows and audit records are required for
recovery; regenerating random IDs by rerunning registration is not replay.
Original migrations 1–5 and existing financial record encodings are unchanged.
An empty new table still changes the schema-6 semantic hash domain; it does not
mean balances changed. Old backup hashes are verified under their old schema
before migration. No mappings are invented by migration.

## Mock execution integration

`MappedSyntheticExchange` is a second explicitly admitted in-memory fake, sharing
the executor's exact writer. The existing fake remains the default; neither can
access an exchange. Before a mapped placement claim, `MockExecutor` registers
the ID. The fake records the submitted form and reports the declared returned
form. No user CLI or live/sandbox option is added.

The new `WireOrder` carries no internal order number or allocation identity.
Normalization must resolve its returned ID in the correct venue/account and
prove the full scope and prior attempt. Symbol, LIMIT type, side, exact decimal
price/amount and cumulative/status consistency are independently checked against
the reserved order. Unknown, changed, cross-account or floating-point facts fail;
there is no fallback matching by price, amount or a shortened internal digest.
Only then does normal reconciliation bind the exchange order and post actual
synthetic trades. Status/cumulative values alone still create no financial fill.

The usual one-SELL, reservations, fee posting, cancellation finality, admission,
portfolio and operational checks remain. The mapped path is tested through seed,
ladder, duplicate reconciliation, a lower BUY fill, partial SELL during cancel
and a reconciled successor SELL. The supplied fake wire schema is not a CCXT
private-event normalizer, a venue pagination contract or acceptance evidence.

## Verification

Reproduce the focused group with the approved local Python/SQLite environment:

```sh
uv run --locked pytest -q tests/persistence/test_client_ids.py tests/execution/test_client_ids.py tests/persistence/test_execution_identity.py tests/universe/test_persistence.py tests/portfolio/test_persistence.py tests/operations/test_faults.py tests/execution/test_crashes.py tests/test_i0_boundaries.py
```

Checks include receipt idempotency, forced collisions/exhaustion, policy changes,
scope isolation, malformed policy/encoding, attempts without prior mapping,
terminal retention, corrupted payload/receipt refusal, v5 backup migration and
restore. Migration fixtures for v1–v4 now remove the new table when reconstructing
those historical schemas. The first focused run found one v4 fixture still
retaining the version-6 migration row; correcting its removal to `version>=5`
restored the intended old fixture rather than weakening schema validation.

Three registration SIGKILL cases cover posting, pre-commit and post-commit.
Nine combined sender SIGKILL cases cover registration, claim and simulated
external effect. The external receipt contains submitted/returned client IDs and
order terms, with no internal order/allocation identity. Recovery resolves only
the returned form and proves an accepted fake order can be found, an unattempted
order can proceed once with its retained ID, and an uncertain claim with no
external evidence remains blocked.
Additional exception/restart cases and original sender crash tests remain.
These are synthetic process-kill tests, not power-loss or venue consistency tests.

The final focused group passed **100 tests in 14.63 seconds** on Python 3.12.16.
The preceding run passed 94 checks before the final policy/encoding cases and
mapped partial-SELL extension. The first migration-fixture run had 66 passes
and one failure, described above; no failed run is counted as a pass.

Review also compared each original SQL statement tuple and checksum with the
base commit: migrations 1–5 match exactly. The receipt is
`/tmp/grid-i8-client-ids-original-contracts.json`. Dependency manifests, the secret
baseline and all four original specification/attachment bytes remain unchanged.

Both full suites passed on the final unchanged application source, including
37 new tests:

| Python | Result | Pytest elapsed seconds |
|---|---|---:|
| 3.12.16 | 1,283 passed; zero failed/skipped | 480.88 |
| 3.13.18 | 1,283 passed; zero failed/skipped | 499.05 |

`/tmp/grid-client-ids-check.py` ran `python -m pytest -q` in the existing locked
environments selected by `UV_PYTHON` and `UV_PROJECT_ENVIRONMENT`. Receipts/logs
are under `/tmp/grid-i8-client-ids-checks/`, stems
`3.12-1793847786980378095` and `3.13-1793847786996781279`. Wrapper elapsed
times were 481.383853 and 499.615258 seconds. Both before/after application hashes
are `5d5d5cfd2b5b64148bb001ab5917feba57fb9571695a7801c04088c78129ba8e`.
All validation process handles completed.

Ruff lint/format passed (261 files), strict mypy passed (195 files), Bandit
passed on `src`/`scripts`, and the 66-package lock check passed. The locked
full-group dependency advisory audit reported no known vulnerabilities.
The unchanged-baseline secret scan covers all 270 tracked/new files.

Final packages use `uv build --no-sources` with separate wheel/source selections
and stay outside Git in `/tmp/grid-i8-client-ids-dist/`.
`/tmp/grid-client-ids-packages.py` verifies all 89 application modules in the
wheel, embedded README bytes and 265 required source-archive files, excluding
generated database/market-data payloads. Artifact hashes are retained in
`/tmp/grid-i8-client-ids-package-verification.json`. Final secret scanning and
`git diff --check` precede publication. Hosted CI remains pending, not passed.

## Migration, rollback and remaining gates

The new schema is additive and used only in temporary mock databases here.
Older code rejects schema 6. Prefer retaining the upgraded ledger and disabling
the optional mapped fake when investigating a problem. A code rollback that
needs an older schema must use a reviewed pre-upgrade backup in a new path;
never drop mapping rows or downgrade a database in place. A backup predating an
attempt may lack its identity, so restore still requires independent reconciliation
and must never recreate an unknown submission. Preserve the newer database.

No real gateway uses this mapping. Venue length/charset, prefix behavior,
uniqueness scope, retention, account mode, signed numeric serialization and
lookup/event consistency still require implementation review and separately
authorized evidence. The completed public-hour budget is not reused. Full I8
observed acceptance, intended-first-venue evidence, I9 and deployment remain open.
