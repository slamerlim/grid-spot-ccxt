# I2 local validation evidence

Recorded 2026-11-03 UTC (2026-11-04 in the owner's current timezone).

**Result: I2 mock-ledger gate passes local validation. Hosted CI remains pending.
LIVE remains disabled. I3 has not been authorized or started.**

The owner explicitly replied **"Approve I2 with local validation"** to the
published `I2-PLAN.md` request. This authorized temporary synthetic databases,
ledger code, crash/backup/restore tests and local checks. It did not authorize
exchange access, credentials, trading or deployment. GitHub Free restrictions
remain in both project guidance and the runbook.

## Revision and delivered scope

- Branch: `i2/single-owner-ledger`.
- Base: `572d5c968192e16381b248ded57eaeb41936df90` (published I1 review/plan).
- Tested implementation: `3c6188eab8fdfa4c0e5a5ef1033da1f8adffcc8a`.
- This evidence and the next review proposal are a documentation-only follow-up.
  Remote publication must be checked against `git ls-remote`; final publication
  verification is reported in the handoff, not inferred from a configured remote.
- PR: not created. The prepared SSH deploy key supports Git commits/pushes;
  no authenticated GitHub PR API/CLI capability was available in this session.
- No workflow changes, dispatches, reruns, paid products or spending changes.
  Both scoped commits use `[skip ci]` while the known account lock is unchanged.

Added `grid.persistence`: strict codec, versioned schema, checked local storage,
single-owner ledger, bounded async writer and backup/restore. Added 86 persistence
test cases and a subprocess crash fixture. Shared immutable domain fixtures moved
to `tests/conftest.py` without changing their values. Narrowed the original I0
`connect` ban only for `sqlite3.connect` in the persistence storage module; network,
exchange/process execution and pure-domain import guards still pass. Updated
offline doctor labels, owner-state ignore rules, approval history, design and
runbook. No runtime dependency, lockfile, imported specification or I1 strategy
code changed. Legacy and dataset paths were untouched.

## Environments actually exercised

| Environment | Python | Linked SQLite | Result |
| --- | --- | --- | --- |
| `/tmp/grid-i2-py312` | 3.12.16 | 3.53.1 | Full positive suite passes |
| `/tmp/grid-i2-py313` | 3.13.18 | 3.53.1 | Full positive suite passes |
| `/usr/bin/python3.12` | 3.12.3 | 3.45.1 | Actual ledger constructor refuses before database creation |

Both isolated builds reported source ID:

```text
2026-05-05 10:34:17 c88b22011a54b4f6fbd149e9f8e4de77658ce58143a1af0e3785e4e6475127e9
```

Compile-option inspection reported `COMPILER=clang-23.1.3`, `THREADSAFE=1`,
`MUTEX_PTHREADS`, `DEFAULT_SYNCHRONOUS=2`, `DEFAULT_WAL_SYNCHRONOUS=2` and no
`OMIT_WAL`. The library also records the full option list/source/runtime in its
private test ledger metadata. The builds came through uv's managed CPython
distribution; system Python was not replaced. Tests explicitly approve this
source identity for mocks. These observations are not production build approval,
an independent supply-chain audit or proof of deployment storage suitability.

## Commands and results

uv was invoked as `/tmp/grid-tooling/uv` (0.13.7). Both environments were installed
with `uv sync --locked` using explicit `UV_PYTHON` and `UV_PROJECT_ENVIRONMENT`.
For each interpreter, the full run used:

```sh
UV_PYTHON=3.12.16 UV_PROJECT_ENVIRONMENT=/tmp/grid-i2-py312 /tmp/grid-tooling/uv run --locked pytest -q
UV_PYTHON=3.13.18 UV_PROJECT_ENVIRONMENT=/tmp/grid-i2-py313 /tmp/grid-tooling/uv run --locked pytest -q
```

| Check | Observed final result |
| --- | --- |
| Full pytest, Python 3.12.16 | **268 passed**, 0 failed, 0 skipped; 5.92 s |
| Full pytest, Python 3.13.18 | **268 passed**, 0 failed, 0 skipped; 5.94 s |
| `uv lock --check` | Pass; 65 packages resolved; lock unchanged |
| `ruff check .` | All checks passed |
| `ruff format --check .` | Pass; 47 files already formatted at implementation check |
| `mypy` | Success; 31 source files checked |
| `bandit -q -r src` | Pass; no findings or suppressions added |
| Tracked/new-file `detect-secrets-hook` | Pass; baseline unchanged |
| Strict `pip-audit` of the locked all-groups export | No known vulnerabilities found |
| `uv build --no-sources` | Source distribution and wheel built successfully |
| `git diff --check` / staged check | Pass |
| Actual old-system-runtime negative probe | `3.12.3 3.45.1 PASS: refused before database creation` |

The remaining README checks used the 3.12 environment, either through its direct
tool executables or `uv run --locked` with the explicit environment above.
Secret scanning covered tracked **and untracked intended additions** using
`git ls-files --cached --others --exclude-standard -z` and the existing baseline.
The advisory export was `/tmp/grid-i2-audit-requirements.txt`, produced with
`uv export --locked --all-groups --no-emit-project --no-hashes`; audit used
`pip-audit --strict --disable-pip --no-deps -r` on that complete pinned export.
Its generic no-hashes guidance was printed; the audit completed with no known
vulnerabilities. No second dependency lock was introduced.

The application and tests made **zero exchange calls**. Installation and advisory
auditing used package/advisory services. Async tests allow only Unix socketpair
for asyncio's local wakeup; an explicit assertion verifies Internet sockets stay
blocked. All other tests retain the existing socket prohibition. The source guard
also forbids exchange/network modules throughout application code.

## Financial and recovery evidence

| Gate | Actual fixture evidence |
| --- | --- |
| Q03 finite funding | Concurrent allocations for different symbols share one pool; exactly one gets the final funds and seed reserve. Failed seed rolls assignment back. Release rejects reserved funds. |
| Q05 one SELL | Partial unique constraint rejects a second unresolved SELL. Cancel ack and late partial fill keep the old slot; replacement appears only after terminal proof. An uncertain SELL survives restart and a later BUY fill. |
| Q08 idempotency | Same trade and same request replay once; changed payload blocks/conflicts. Status does not post money. Completed-round compaction preserves archived trade/status identity and subsequent commands. |
| Q09 uncertainty | Attempt becomes durable before a fake boundary. Restart never makes it unsent. Reconciliation cannot clear an unknown order without matching observations. |
| Q10 exact postings | Partial fill, base/quote/token fees, rebates, reserve consumption, cost and PnL, tiny/large Decimal values and low ambient precision. Snapshot projections and journal totals are checked against durable history. |
| Q19 ownership/storage | Cross-process and same-process lock rejection; path aliases; permissions; schema/index/checksum corruption; actual old runtime refusal; wrong pragmas; nonlocal mount refusal. |
| Bounded admission | Sole connection owner thread, last-funds race, full queue refusal, canceled caller after commit with durable receipt lookup, connection rejects cross-thread use. |
| Storage faults | Actual SQLITE_BUSY via competing transaction; actual SQLITE_FULL via per-database page cap; injected I/O errors before/after commit; pinned-reader WAL bound refusal without retry loop. |
| Backup/restore | Online backup into new private paths; separate restore; integrity/FK and business-record hash comparison; uncertain intents retained; schema tamper, content tamper, deadline and injected copy faults refused. |

Twenty process-death cases use actual **SIGKILL**:

- `credit`, `start`, `event`, `release`, `attempt`, `compact`, each killed after
  posting but before commit, immediately before commit, and immediately after
  commit: 18 cases.
- Two more kills immediately before and after a fake-send marker, following the
  durable uncertain-attempt transaction.

The fill crash fixture includes three simultaneous fee currencies. Recovery
observes either the prior state or the complete cash/base/token/fee/cost/journal
posting, never a partial accepted event. The fake-send cases retain uncertainty
whether or not the marker exists. Fixtures constrain only temporary database
pages; no host disk was filled. These are process-death tests, not simulated
kernel/hardware power failure.

During development, tests exposed and corrected the maximum-exponent Decimal
round-trip case and a test-fixture interaction with pytest-socket's nested
wrappers. Review added full schema verification before restore and stronger
journal/fill projection validation. Formatting/security findings were resolved;
final checks above contain no outstanding failure. No test was skipped to obtain
a positive patched-runtime result.

## Boundaries and recommendation

**GO for I2 review under the approved local-validation condition. HOLD before I3
until its separate approval.** See `I3-PLAN.md` for the concrete next proposal.

Hosted CI is **PENDING/BLOCKED by the previously observed account lock**, not
passed and not retried. No claim is made about a current account-state change.
The original hosted acceptance condition remains separately outstanding.

Production build provenance/NVMe durability, performance/resource limits,
priority intake/supervision, exchange trade-ID normalization, seven-venue probes,
authenticated recovery, executor behavior, paper/sandbox soak and live readiness
remain **NOT TESTED**. Backup deadlines do not bound a hung kernel fsync; a single
atomic transaction may temporarily exceed the WAL admission threshold. These
limits are explicit in `I2-DESIGN.md` and do not grant live authority.

Reproduce using the explicit interpreter commands and README checks. Roll back
with normal scoped revert commits and the matching lock; no operator database
exists to migrate or downgrade. Preserve immutable specs, legacy/data paths,
GitHub Free preferences and any diagnostic artifacts outside Git.
