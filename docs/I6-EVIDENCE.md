# I6 local universe and reconfiguration evidence

Recorded 2026-11-04 local date. The owner explicitly selected **"Approve I6 with
local validation"** for `I6-PLAN.md`. Only synthetic observations/exchanges and
temporary mock ledgers were used. No public exchange probe, authenticated access,
real/sandbox trading, transfer or deployment was performed. Original legacy/data
files and imported specifications remain unchanged.

**Result: I6 implementation and local checks pass. Hosted CI is PENDING under
the unchanged account lock. Live remains DISABLED; all real venues remain HOLD.**

## Revision and change summary

- Branch: `i6/universe-reconfiguration`.
- Base: `525a92e6cfdc2d6d4d1d478e7a5473ad9eed988c`, the published I5 handoff.
- Code, tests and this evidence are published together. The final chat records
  the exact head after remote verification; no merge is claimed.
- PR: not created. No GitHub PR connector is available; `gh` is absent. Ordinary
  SSH commit/push is authorized. Publication uses `[skip ci]`, without retrying
  the known Actions lock or activating any paid GitHub feature.
- Implementation fingerprint:
  `b3327716b31fa68a7e9849c59fd5e7bb4bfceff991ee13a16627ce0959a83670`.
  SHA-256 over 86 sorted paths: all `src/**/*.py`, `tests/**/*.py`,
  `pyproject.toml`, `uv.lock`. For each, append UTF-8 relative path, NUL,
  unsigned 8-byte big-endian file length, then bytes. Docs are excluded to avoid
  a self-hash cycle. No benchmark or generated dataset is committed.

Added `src/grid/universe/` policy/selection, public observation cache and mock
controller APIs; `persistence/universe.py` implements atomic policy/retirement
revisions. Ledger schema v3 adds one audit table. Executor BUY claims now check
current universe evidence through the same writer operation, with an independently
required ledger admission receipt. Existing deployments are absent; no deployed
database was migrated. Updated doctor, README, runbook, approval guidance and
the proposed I7 gate. `tests/universe/` adds 57 cases, including three SIGKILL
boundaries. The old v1 fixture was adapted to remove later tables when constructing
its genuine old schema. No dependency, CI workflow, secret baseline, example
production setting, imported specification or strategy-domain file changed.

## Commands and results actually observed

uv **0.13.7**, Python **3.12.16** in `/tmp/grid-i2-py312` and **3.13.18** in
`/tmp/grid-i2-py313`; both use the existing approved synthetic-test SQLite
**3.53.1** build. CCXT remains **4.5.91**. These are local test environments,
not production provenance or exchange certification.

| Check | Result |
| --- | --- |
| Full pytest, Python 3.12.16 | **518 passed**, 0 failed/skipped, 47.54 seconds |
| Full pytest, Python 3.13.18 | **518 passed**, 0 failed/skipped, 49.06 seconds |
| New I6 cases | **57 passed**, included in both full suites |
| Ruff lint / formatting | PASS; 113 Python files formatted |
| Strict mypy | PASS; 84 application/test source files |
| `uv lock --check` | PASS; 66 packages, lock unchanged |
| Bandit | PASS; no findings or new suppressions |
| Dependency advisory audit | PASS; no known vulnerabilities found |
| Secret scan | PASS; tracked/staged files, baseline unchanged |
| Wheel and source distribution | PASS |
| Actual offline doctor JSON | Exit 0; iteration I6, `live: DISABLED`, `ready: false` |
| Imported specifications, lock, example config, CI workflow | Unchanged from I5 |
| Hosted GitHub Actions | PENDING; known lock not retried |

Reproduce with the README commands, explicit `UV_PYTHON` and
`UV_PROJECT_ENVIRONMENT`, and `/tmp/grid-tooling/uv` on this host. For example:

```sh
UV_PYTHON=3.12.16 UV_PROJECT_ENVIRONMENT=/tmp/grid-i2-py312 /tmp/grid-tooling/uv run --locked pytest
UV_PYTHON=3.13.18 UV_PROJECT_ENVIRONMENT=/tmp/grid-i2-py313 /tmp/grid-tooling/uv run --locked pytest
```

Other executed commands: `uv lock --check`, `uv run --locked ruff check .`,
`uv run --locked ruff format --check .`, `uv run --locked mypy`,
`uv run --locked bandit -q -r src`, the README tracked-file secret scan,
`uv export --locked --all-groups --no-emit-project --no-hashes` to
`/tmp/grid-i6-audit-requirements.txt`, `uv run --locked pip-audit --strict
--disable-pip --no-deps -r` that file, `uv build --no-sources`, and
`uv run --locked gridctl doctor --config config.example.yaml --json`.
Internet sockets remain disabled in tests; asyncio fixtures permit only AF_UNIX.
The advisory audit uses its services and publication uses GitHub SSH. No exchange
method was invoked outside the synthetic test transports. The generic advisory
tool warning about no-hash export does not alter installation through `uv.lock`.

Development tests exposed a v2-backup compatibility defect after adding v3:
the restore path admitted only v1 or current. It now accepts every known migration
version, and the final suite exercises v2 restore plus existing v1 migration.
Fixture corrections included venue-specific Bitfinex amount precision and an
assertion that cancel acknowledgement leaves an order unresolved. No financial
invariant, socket restriction or required gate was relaxed to obtain these passes.

## Acceptance evidence and claim boundaries

| IDs | Executed fixtures | Limits |
| --- | --- | --- |
| Q11 | Manual/auto/hybrid, deny over pin, ineligible pins; denied partial seed keeps one SELL; old SELL fills while BUY cancel is pending; disable during uncertain send; fill arrives during exclusion and portfolio-exit control; external pause/retire/exit cannot be auto-resumed | Synthetic gateway only; global NAV trigger/liquidation remains I7 |
| Q17 | 100 simultaneous market misses share one refresh; one canceled waiter does not cancel shared work; all-waiter cancellation cleanup; in-flight invalidation rejects old generation; expiry reloads rather than re-stamps; stale fee/minimum/book facts, invalidation, closed or removed guard block queued BUY | No real venue cache/reconnect certification; no automatic live-facts bridge |
| Q18 | Deterministic 10/100/1,000-candidate selection; one/seven synthetic client ownership and closure; bounded LRU and revision tombstones; waiter/rate-budget refusal; existing writer/executor overload fixtures retained | No large CCXT roster, measured RSS/latency ceiling, continuous scheduler, research arbitration or soak claim |
| Q03/Q05/Q09/Q10 | No financial reallocation by selection; reservations and partial inventory retained; no second unresolved SELL; unsent revocation versus possibly sent order; attempted unknown stays unresolved; all prior accounting tests rerun | No actual balances, fee tiers, queue priority or execution-depth guarantee |
| Q19 | SIGKILL at policy after-posting/before-commit/after-commit proves all-or-nothing policy and retirement; backup/restore retains partial inventory and retirement audit; v1/v2 migration; corrupted policy rejected; restart needs full roster and fresh evidence | Process death on mock storage, not production power-loss or hardware certification |
| Q01/Q20 | Existing limit-only/security boundary tests pass; no live transport or arming command added; doctor remains blocked | Authenticated exchange permissions and deployment remain untested |

Financial ownership comes from the committed ledger, never cached spendable
balances. Selection can retire/resume only its own control and cannot manufacture
funds. Changes committed before a first-send claim prevent stale permission;
after-claim changes retain the order as uncertain and supervised. A partial fill
arriving during policy change is posted without reactivating new BUYs.

## Open limits, rollback and recommendation

The automatic universe selects a fixed, pre-funded roster. The public I3 client
still has its eight-configured-symbol bound, and no public discoveries are
automatically promoted into verified account/fee facts. Runtime roster growth,
automatic budget reassignment, audit retention, maximum-roster transaction latency,
hardware resource measurements and production scheduling remain future work.
Costs/depth are eligibility inputs, not a profitability claim. An empty canceled
seed needs an explicit new-round decision. Retirement retains exposure until
reconciled; it does not liquidate inventory. Private adapters, real fills/fees,
storage approval and paper/sandbox soak remain unverified.

Rollback: scoped code/docs revert, preserving user approval history and GitHub
Free rules. Do not drop v3 audit records or downgrade a ledger in place. Use a
verified separate pre-migration backup with matching earlier code when testing
rollback. No external orders, deployed databases or original data were changed.

**Recommendation: GO to I7 planning review; HOLD I7 implementation until explicit
approval of `I7-PLAN.md`.** I6 is complete within its approved synthetic/local
scope. Local passes do not resolve the hosted CI or real-venue acceptance gaps.
