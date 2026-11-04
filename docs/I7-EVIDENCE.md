# I7 local portfolio accounting and exit evidence

Recorded 2026-11-04 local date. The owner explicitly selected **"Approve I7 with
local validation"** for `I7-PLAN.md`. Only synthetic facts/exchanges and temporary
mock databases were used. No exchange probe, authenticated access, real/sandbox
order/cancellation, exchange transfer or deployment occurred. Internal ledger
ownership movements are synthetic accounting operations, not exchange transfers.

**Result: I7 implementation and local checks PASS. Hosted CI is PENDING under
the unchanged account lock. Live remains DISABLED and all real venues remain HOLD.**

## Revision and changes

- Branch: `i7/portfolio-cycle-exit`.
- Base: `1c09876e29371c1bbeb27a546af6bb4271446d8c`, published I6 handoff.
- Code, tests and evidence are published together. The final chat identifies
  the exact remotely verified commit. No merge is claimed.
- PR not created: no GitHub PR connector is available and `gh` is absent.
  Existing authorized SSH publication uses `[skip ci]`; no paid capability,
  spending setting, account-lock retry or skipped-as-passed CI claim is involved.
- Implementation fingerprint:
  `8dd85d4651c0bf5aa58f4234da4baca0b2f17445fd9f1ef9cf0f551dbfaeb900`.
  SHA-256 over 102 sorted paths: `src/**/*.py`, `tests/**/*.py`, `pyproject.toml`
  and `uv.lock`. For each append UTF-8 relative path, NUL, unsigned 8-byte
  big-endian byte length, then file bytes. Docs are excluded to avoid self-hashing.

Added typed portfolio policy/evidence/cycles, conservative valuation, bounded
LIMIT exit planning, a mock controller and writer-owned portfolio persistence.
Schema v4 appends cycle revisions and cashflow projections. Actual fills still
use the existing reducer and exact-once ledger; the domain adds an exit order
role and explicit internally transferred lots without fabricated trades.
Executor and ledger claims enforce current portfolio admission alongside I6
universe admission. Updated doctor, README, runbook and approval guidance.

`tests/portfolio/` adds 64 cases, including 18 real SIGKILL cases. Old-schema
fixtures remove v4 tables when constructing genuine earlier-version backups.
No dependency, lock, CI workflow, secret baseline, example production setting,
legacy source, original dataset or imported specification was changed.

## Executed checks

uv **0.13.7**, Python **3.12.16** in `/tmp/grid-i2-py312` and **3.13.18** in
`/tmp/grid-i2-py313`. Both link the existing synthetic-test-approved SQLite
**3.53.1** source identity. CCXT remains **4.5.91**. This is local mock evidence,
not production storage provenance or venue certification.

| Check | Observed result |
| --- | --- |
| Full pytest, Python 3.12.16 | **582 passed**, 0 failed/skipped, 86.84 seconds |
| Full pytest, Python 3.13.18 | **582 passed**, 0 failed/skipped, 89.10 seconds |
| New I7 cases | 64; included in both full suites |
| Ruff lint / formatting | PASS; 132 Python files formatted |
| Strict mypy | PASS; 100 application/test source files |
| `uv lock --check` | PASS; 66 packages, lock unchanged |
| Bandit | PASS; no findings or added suppressions |
| Dependency advisory audit | PASS; no known vulnerabilities found |
| Tracked/staged secret scan | PASS; baseline unchanged |
| Wheel and source distribution | PASS |
| Actual offline doctor JSON | Exit 0, iteration I7, `live: DISABLED`, `ready: false` |
| Imported specs, deps, example config, CI workflow | Unchanged from I6 |
| Hosted GitHub Actions | PENDING; known lock not retried |

Reproduce with README commands and explicit interpreter/environment selection:

```sh
UV_PYTHON=3.12.16 UV_PROJECT_ENVIRONMENT=/tmp/grid-i2-py312 /tmp/grid-tooling/uv run --locked pytest
UV_PYTHON=3.13.18 UV_PROJECT_ENVIRONMENT=/tmp/grid-i2-py313 /tmp/grid-tooling/uv run --locked pytest
```

Other checks used `uv lock --check`, `uv run --locked ruff check .`,
`uv run --locked ruff format --check .`, `uv run --locked mypy`,
`uv run --locked bandit -q -r src`, the README tracked/staged secret scan,
`uv export --locked --all-groups --no-emit-project --no-hashes` to
`/tmp/grid-i7-audit-requirements.txt`, `uv run --locked pip-audit --strict
--disable-pip --no-deps -r` that file, `uv build --no-sources`, and
`uv run --locked gridctl doctor --config config.example.yaml --json`.
Test internet sockets remain disabled; asyncio permits AF_UNIX only. Advisory
services and GitHub SSH publication are separate from the offline tests. The
audit's generic no-hash-export warning does not change locked installation.

## Acceptance evidence and limits

| IDs | Executed fixtures | Claim limits |
| --- | --- | --- |
| Q12 | Exact +10% equality, complete multi-venue winners plus losers, reserved funds counted once, positive baseline, stale/missing/account/fee/book/FX/capacity refusals, base-fee quantities and split-order fees | Synthetic facts only; no real executable-depth, account-fee or profitability guarantee |
| Q12/Q03 | Unclassified flow freezes BUY/evaluation; additive deposit/withdrawal adjusts owned pool and baseline atomically; duplicate external ID/replayed request protection; no-transfers refuses adjustment; reviewed non-external event changes no money | Reporting-currency owned-pool convention only; no automatic transfer discovery or multi-currency/time-weighted flow return |
| Q01/Q05/Q09/Q10 | Durable global freeze, threshold discovered at BUY claim, complete ordinary-order cancellation barrier, late BUY and old SELL on a second venue, partial exit, cancellation acknowledgement stays unresolved, one replacement slot, unsent TTL revocation consumes budget, unavailable facts/price/time budget block completion | Strictly SyntheticExchange; no real/sandbox payload sent; initial NAV refuses uncertain orders rather than predicting their outcome |
| Q11/Q17 | Universe disable while an exit send awaits gateway I/O leaves the latch and unknown SELL intact; configuration writer remains responsive; stale/invalidated/removed guard/future context reject BUY claims | Deterministic interleavings, not exhaustive scheduling or a latency/resource benchmark |
| Q12/Q03 | Third-token assets/liabilities use bid/ask conversions, actual exit rebate posts once, token ownership moves to an explicit same-account sink then liquidates through actual mock fills; post-debit injected failure rolls back pool movement; no fake fill created | Direct conversion only; marked transfer basis is not reconstructed historical strategy PnL; fee-asset fixture and pool fault test do not certify every currency route |
| Q12 | Approved versus absent/insufficient dust limits, final residual remains owned and reported with zero executable NAV, actual final return distinct from trigger; 1,000 → 1,100 trigger → 1,050 final gives +5% and 50 shortfall | The fixture proves accounting behavior, not a trading result; broad property coverage of all dust/fee/precision combinations is not claimed |
| Q19 | SIGKILL after-posting/before-commit/after-commit for cycle begin, latch, exit planning, actual exit fill posting, completion and cashflow adjustment; all-or-nothing financial state/history; blocked-cycle and transferred-lot backup/restore; v3→v4 plus existing v1/v2 migrations; malformed cycle refused | Process death on temporary mock storage, not hardware power-loss or deployed-filesystem certification |
| Q01/Q20 | All prior scope/security/accounting tests rerun; live flags still cannot arm; no network/import boundary weakened; corrupt ACTIVE-with-exit proof refused | Real credentials, permissions, private events, deployment and operational security remain unverified |

Review found and corrected conservative edge cases before publication: separately
rounded fees when NAV requires multiple maximum-size orders; cumulative dust
ceilings; claim-time global reconciliation; immutable trigger-baseline evidence;
and rollback of a failed pool debit. Fixture errors included an expected total
that omitted token holdings, assuming cancellation order, and arming a crash on
reconciliation instead of the actual fill transaction. The production assertion
flagged by Bandit became an explicit runtime error. No safety boundary, fee bound,
network restriction or acceptance gate was relaxed to obtain passing checks.

## Remaining work and review decision

See `I7-DESIGN.md` for API semantics, fixed-roster/direct-conversion bounds,
additive-cashflow accounting, dust treatment, marked transfer basis, concurrency,
and restore rules. Unknown submissions block initial NAV evaluation; a completed
claim is never retracted as unsent. Evaluation/planning are explicit calls, with
no background scheduling or operational trading CLI. No full NAV time series,
maximum-roster resource/latency result, account detection, automatic FX routing,
exchange/private execution certification or observed 48-hour soak is claimed.

Rollback is a scoped code/docs revert preserving audit/approval history and
GitHub Free restrictions. Keep v4 financial history; test a separate verified
compatible pre-migration backup instead of dropping tables/downgrading in place.
No deployed database or external order was changed.

Recommendation: **GO to review of `I8-PLAN.md` operational preparation; HOLD
external-access soak and live deployment.** Preparation alone cannot satisfy
I8's required observed minimum 48-hour run or first-venue evidence. Stop after
I7 review until the owner approves the next concrete scope. Hosted CI remains
pending; local passes do not erase that limitation.
