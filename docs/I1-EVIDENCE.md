# I1 evidence — pure strategy and mock events

Validation completed 2026-11-03 UTC (2026-11-04 Europe/Moscow). The owner
explicitly selected **"Proceed with local validation for I1"**, replacing the
earlier condition to wait for hosted I0 CI. Hosted CI remains pending. Imported
PRD, ADR, acceptance plan and master-prompt bytes remain unchanged.

## Revision and change summary

- Branch: `i1/deterministic-strategy`.
- Base: `e68a8413240aed99633dfad611cd8c205aef20b2`.
- Math/types commit: `d23860ffe588f336b40aead460b2f459d9f4a163`.
- Tested implementation head: `472d0829cc3d3bcce9cd6b59b22379be6fd7425d`.
- This evidence and the README/runbook corrections are a subsequent docs-only
  commit. Use `git rev-parse HEAD` for that final documentation revision; the
  delivery response records the verified remote head.
- PR: **not created**. Git SSH write access is available through the existing
  repository deploy key; no authenticated PR API capability is available.

`src/grid/domain/` adds Decimal/quanta helpers, precision/fee/market planning,
immutable scoped types and a deterministic reducer returning proposed LIMIT,
cancel and reconcile commands. `tests/domain/` adds unit, property, generated
sequence, replay and import-boundary tests. Hypothesis 6.170.14 is pinned in the
single `uv.lock`. Doctor labels now identify I1 while retaining `ready=false`,
LIVE disabled and the existing blocked `--check` behavior.

The minimum BUY is independently proven before sellability validation. SELL
replacement retains one unresolved slot until terminal proof. Fill-driven
replenishment, late-fill reuse, explicit fee reserves, finite allocated funds
and history/search bounds are implemented. See `I1-DESIGN.md` for formulas,
normalized-event contracts and limits. No exchange, persistence or executor
module was added. No live configuration values were invented.

## Executed validation

Tools: uv 0.13.7, Ruff 0.17.3, mypy 2.4.1, pytest 9.2.0 and Hypothesis 6.170.14
from the lock. The commands below used `/tmp/grid-tooling/uv` as `uv`.

| Check actually run | Result |
| --- | --- |
| Locked install / `uv lock --check` | PASS; one lock, 65 resolved packages across environment markers |
| `uv run --locked ruff check .` | PASS |
| `uv run --locked ruff format --check .` | PASS; 30 files formatted |
| `uv run --locked mypy` | PASS; 17 source/test files, both Python environments |
| `uv run --locked pytest -q` on Python 3.12.3 | **176 passed**, 0 failed, 0 skipped |
| Same suite on Python 3.13.18 | **176 passed**, 0 failed, 0 skipped |
| Hypothesis drain-ordering test with statistics | **24 passing** orderings; 0 failing, 0 invalid |
| `uv run --locked bandit -q -r src` | PASS |
| detect-secrets hook over tracked and new reviewable files, `--no-verify` | PASS; no secret baseline expansion |
| Export all locked groups; `pip-audit --strict --disable-pip --no-deps` | PASS; no known vulnerabilities reported by advisory services |
| `uv build --no-sources` | PASS; sdist and wheel built |
| Doctor JSON on both environments | I1, LIVE DISABLED, `ready=false` |
| Doctor `--check` on Python 3.12 | Expected exit **1**; live prerequisites blocked |
| `git diff --check` and staged checks | PASS |
| Imported specification SHA-256 checks | PASS within the suite |

The 176 tests comprise the existing 104 configuration/doctor/boundary cases and
72 domain cases. Property tests use deterministic Hypothesis generation; fixtures
are immutable and synthetic. Socket use is disabled by pytest. Early test/setup
failures and review findings were corrected before these final results; no
remaining failure is hidden as a skip.

The 3.13 run selected its interpreter explicitly, avoiding uv's ambient 3.12
selection, with:

```sh
export UV_PROJECT_ENVIRONMENT=/tmp/grid-i0-py313 UV_PYTHON=3.13
uv sync --locked
uv run --locked python --version
uv run --locked mypy
uv run --locked pytest -q
```

Use a separate shell for that environment; run the complete README command set
in the default 3.12 environment. The focused ordering evidence is reproducible:

```sh
uv run --locked pytest tests/domain/test_reducer.py::test_all_drain_event_orderings_reuse_late_inventory -q --hypothesis-show-statistics
```

## Partial acceptance trace

| Gate | I1 evidence | Still required |
| --- | --- | --- |
| Q01 | Every proposed placement is structurally LIMIT; pure import boundary | I4 gateway and outgoing-payload spies, I7 exit paths |
| Q02 | Arbitrary ticks/significant digits, exact minimum lot, fee-base .999 rejection, different minima, tiny prices, caps | Verified venue rules and CCXT serialization |
| Q03, Q10 | Allocated quote/base/token reserves, partial fee consumption, conservative multi-fill rounding, actual fees/rebates, cost conservation, bounded exposure | I2 transactional shared-wallet races and durable posting |
| Q04 | Acknowledgement creates no inventory; below-minimum partial held; sellable partial supervised; terminal weighted seed anchor | I4 verified order/trade intake |
| Q05 | Cancel acknowledgement cannot free SELL; partial/fill-during-cancel applied first; successor uses remaining inventory | Durable intent/send boundaries and exchange cancellation proof |
| Q06 | N=3 ladder, distinct levels, bounds/range, first-fill replenishment only, no movement from unfilled cancellation | Venue metadata drift and executor behavior |
| Q07 | All 24 permutations of a late fill and three terminal BUY reports reuse inventory without a duplicate seed; unresolved seed also drains | Network/restart fault integration |
| Q08, Q09 | Exact duplicate no-op, conflicting/out-of-order facts blocked, deterministic replay, unknown submission blocks retry, stale reconciliation assertion rejected | Durable identity and crash/reconnect recovery |
| Q11, Q17 | Pause/retire/exit overlays retain obligations; version/freshness rejection | I6 universe/cache/hot-change integration |

These are partial pure-domain checks, **not full Q01–Q20 passes**. Q12–Q16 and
Q18–Q20 require their later integrated implementations; no exchange certification,
portfolio-exit proof, resource benchmark or soak result is claimed here.

## External status and security

GitHub public-repository metadata was read successfully. The workflow is
unchanged and uses the public-visibility guard, standard Ubuntu runners and no
remote cache/artifact upload. The current
[official billing guidance](https://docs.github.com/en/actions/concepts/billing-and-usage)
still describes standard public-repository runners as free. No paid GitHub
product, trial, runner, spending increase or overage was used.

The earlier account-lock result remains the latest hosted execution evidence.
No workflow was dispatched or retried. All I1 commits carry `[skip ci]`; this is
deliberately **not** a hosted pass. The local-validation exception permits I1
work but does not certify the original I0 hosted gate or later releases.

Exchange/network methods probed: **none**. Tooling accessed package/advisory
services, public GitHub metadata/documentation and ordinary Git publication.
No exchange credentials, orders, financial database, deployment or legacy/data
modifications occurred. The existing SSH deploy key stays outside Git.

Linked SQLite was 3.45.1 on Python 3.12.3 and 3.53.1 on Python 3.13.18. The former
fails the future-live version gate; neither environment establishes storage
provenance, safety or readiness. I1 creates no database.

## Review decision and rollback

**GO for I1 code review; HOLD I2 and live work until separately authorized.**
The approved partial-seed rule is preserved. Additional explicit implementation
contracts—fee-settlement bounds, integer-ratio partial basis allocation and
reconciliation revisions—are documented in `I1-DESIGN.md`. Venue-specific
normalization must validate their prerequisites before any future use.

Pure immutable state has bounded linear history scans, not a demonstrated live
throughput target. Exhausting history blocks until future durable recovery;
it does not discard deduplication evidence. Missing fee conversions, unsellable
residuals and unsupported arithmetic remain explicit blocked conditions.

Rollback uses normal reviewed revert commits for the I1 implementation, followed
by `uv sync --locked` and the prior checks. No migrations, deployed process or
wallet state need rollback. Preserve GitHub Free guidance, approval history,
immutable specifications and unrelated files; never reset published history.
