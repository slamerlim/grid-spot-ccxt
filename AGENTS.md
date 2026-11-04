# GRID implementation rules

Read the full PRD, ADR-0001, acceptance plan and master implementation prompt
under `docs/` before editing. They are authoritative over the legacy simulator.
User instructions take precedence. Preserve imported specifications verbatim;
record clarifications separately with evidence and approval requirements.

## Current boundary: I8 public-paper harness and approved one-hour run

On 2026-11-03 the owner approved `docs/I1-PLAN.md`, including its partial-seed
rule, then explicitly selected "Proceed with local validation for I1" to
replace the hosted-CI prerequisite for starting this iteration. I1
pure Decimal strategy code and mock-event tests were covered by that approval.
Do not request it again. I1 implementation and review have been published.

The owner subsequently explicitly selected "Approve I2 with local validation"
for `docs/I2-PLAN.md`. Implement the single-owner SQLite ledger, atomic funding,
reservations/fill posting and mock crash/backup/restore tests using temporary
databases. This approval replaces the hosted-CI prerequisite for I2. Hosted CI
remains pending, not passed. Record local checks and remaining coverage precisely.
I2 is implemented and reviewed. Do not request the I2 approval again.
I2 implementation and local evidence are recorded in `docs/I2-EVIDENCE.md`.

On 2026-11-04 the owner explicitly selected "Approve I3 with local validation"
for `docs/I3-PLAN.md`. Implement read-only CCXT adapters, mock tests and bounded
public market/order-book probes on the seven target venues. This supersedes the
previous stop-after-I2 gate. Do not request I3 approval again. Authenticated
probes require separately authorized account access; do not discover credentials.
I3 implementation and review are complete. Live remains unavailable under
every configuration. Hosted CI remains pending; use local validation only.
I3 implementation, validation and the bounded public pass are recorded in
`docs/I3-EVIDENCE.md`.

On 2026-11-04 the owner explicitly selected "Approve I4 with local validation"
for `docs/I4-PLAN.md`. Implement the mock executor, event intake and recovery
tests using synthetic exchanges and temporary ledgers. This supersedes the
stop-after-I3 gate. Do not request I4 approval again. No authenticated access,
additional public probes, real or sandbox orders/cancellations, transfers,
deployment or live financial databases are authorized. Use local validation
and GitHub Free only. Publish I4 evidence and an I5 proposal; stop after I4 review.
I4 implementation and local evidence are in `docs/I4-EVIDENCE.md`.
On 2026-11-04 the owner explicitly selected "Approve I5 with local validation"
for `docs/I5-PLAN.md`. Implement read-only historical validation, conservative
OHLC replay and bounded sample benchmarks. This supersedes the stop-after-I4
gate. Do not request I5 approval again. Preserve all original legacy/data files;
generated samples, manifests and results stay outside Git. No exchange access,
real/sandbox trading, transfers or deployment are authorized. Use local validation
and GitHub Free only. Publish I5 evidence and an I6 proposal; stop after I5 review.
I5 implementation and local evidence are in `docs/I5-EVIDENCE.md`. The next
proposal was `docs/I6-PLAN.md`.

On 2026-11-04 the owner explicitly selected "Approve I6 with local validation"
for `docs/I6-PLAN.md`. Implement dynamic universe selection and safe hot
reconfiguration using synthetic facts, temporary mock ledgers and local checks.
This supersedes the stop-after-I5 gate; do not request I6 approval again.
No exchange access, real/sandbox trading, transfers or deployment are authorized.
Use GitHub Free only. Publish I6 evidence and an I7 proposal; stop after I6 review.
Implementation and local evidence are recorded in `docs/I6-EVIDENCE.md`.
The owner subsequently explicitly selected "Approve I7 with local validation"
for `docs/I7-PLAN.md`. Implement portfolio NAV and durable bounded LIMIT-only
global exit using synthetic facts/exchanges and temporary mock ledgers, including
additive migration and crash/restore tests. This supersedes the I6 review gate;
do not request I7 approval again. No exchange access, real/sandbox trading,
transfers, deployment or live financial databases are authorized. Use local
validation and GitHub Free only. Publish I7 evidence and an I8 proposal;
stop after I7 review. I8 requires separate approval.
I7 design and local evidence are recorded in `docs/I7-DESIGN.md` and
`docs/I7-EVIDENCE.md`. The next proposal, `docs/I8-PLAN.md`, separates offline
operational preparation from the still-required observed 48-hour soak and
venue/account validation. Preparation alone cannot complete I8 acceptance.

The owner explicitly selected "Approve I8 operational preparation with local
validation" for `docs/I8-PLAN.md`. This supersedes the stop-after-I7 gate for
offline preparation only; do not request that approval again. Implement the
mock supervisor, audited local controls/status, bounded synthetic resource tests,
temporary storage drills, uninstalled service templates and a short synthetic
dry run. No exchange access, public feeds, credentials, sandbox/real trading,
external notifications, transfers, installed service, deployment, live databases
or long-running soak are authorized. Use local checks and GitHub Free only.
Publish preparation evidence and a concrete separately gated soak plan, then
stop for review. I8 acceptance remains incomplete until its observed minimum
48-hour soak and intended-first-venue evidence are actually obtained.
Preparation implementation and local evidence are in `docs/I8-DESIGN.md` and
`docs/I8-EVIDENCE.md`. The subsequent proposal was `docs/I8-SOAK-PLAN.md`;
the owner's amended authorization is recorded below.

The owner subsequently answered: "approve harness and perform a quick run for
1 hour, then continue development". Implement and mock-test the published paper
harness, then perform one observed hour of OKX BTC/USDT public-data collection
and paper simulation under the bounded amended `docs/I8-SOAK-PLAN.md`. This
supersedes the preparation review gate and the proposed 48-hour execution
duration, but does not waive the original 48-hour acceptance requirement.
Continue mock-based development afterward without requesting this approval again.
Private accounts/credentials, real or sandbox orders/cancellations, transfers,
deployment, other venues/endpoints and paid GitHub capabilities remain excluded.
Record the actual one-hour evidence and any refusals honestly; never label the
quick run a full I8 acceptance pass. Use local validation and `[skip ci]` pushes.

The requested hour completed on 2026-11-04, 14:37:26–15:37:26 UTC. Its linked
replacement collected 460 validated books; combined with the preserved first
partial attempt it spent 629/750 GET attempts and all 3/3 instrument attempts.
No paper allocation/order/fill was eligible because required public rules were
unknown. See `docs/I8-PAPER-EVIDENCE.md` for audit, storage and timing limitations.
That public-run authorization is completed; do not rerun or extend it under the
old approval. Continue offline/mock development. The original 48-hour gate,
private account evidence, I9 and deployment remain separately gated. Retain the
two `/opt/grid-runs/i8-public-hour/` evidence directories at least seven days
after review, with no automatic deletion. GitHub Free-only rules still apply.

The offline monitoring follow-up is recorded in `docs/I8-MONITORING-EVIDENCE.md`.
It adds early sampling, independent CPU windows and fail-closed timing evidence;
it does not revalidate the completed public run. Continue toward the full PRD,
including the remaining local operator CLI over the existing mock supervisor.
Keep one financial writer and do not treat partial mocks as completed live gates.

The first local CLI increment is recorded in `docs/I8-OPERATOR-DESIGN.md` and
`docs/I8-OPERATOR-EVIDENCE.md`. `gridctl operator` exposes mock status and existing
audited controls through same-user AF_UNIX IPC. Its finite invented-account owner
starts halted; sensitive commands need a saved request and second confirmation.
The full-cycle manual LIMIT exit is recorded in `docs/I8-MANUAL-EXIT.md`. Its
approval is bound to cycle identity and exit-policy digest; operator audit and
exit latch commit atomically. It retains explicit halts and uses an additive
snapshot extension without rewriting old financial records. The scoped follow-up
is recorded in `docs/I8-ALLOCATION-EXIT.md`: explicit allocation selectors retain
an active portfolio, while global takeover inherits exit budgets and deadlines.
Completed scopes require confirmed resume. `docs/I8-SCOPED-DUST.md` adds explicitly
approved quantified dust, fresh revalidation and atomic resume retaining its lots
and cost. Released residuals still consume shared dust allowance; do not erase
them or buy more to manufacture sellability. Unapproved dust remains blocked.
`docs/I8-HOT-CONFIG.md` records the local universe CLI: both revisions
are checked, every edit is confirmed, and deny/disable retirement is explicit.
Inventory hold cancels outstanding orders and blocks new placements while retaining
reconciliation. Configuration plus any new scoped exit commit in one transaction.
`docs/I8-STORAGE-CONTROLS.md` records owner-mediated backup, independent restore
verification and durable storage-status receipts. Preserve incomplete/failed jobs
and artifacts; replay must never perform a second copy. Only an owner's verified
backup refreshes its backup stamp. Restore verification never activates a ledger.
The research CLI follow-up is recorded in `docs/I8-RESEARCH-CLI.md`:
`gridctl backtest` and `bench-storage` share the existing bounded offline engines,
sanitize failures and preflight exclusive outputs. They do not impose OS resource
quotas or integrate portfolio exits into historical replay. The current evidence
and remaining requirements are mapped in `docs/IMPLEMENTATION-REVIEW.md`.
`docs/I8-RESEARCH-PROVENANCE.md` records the subsequent report envelope:
CLI results now live under `result` in `grid-research-run-v1`; deterministic
engine hashes remain inside that result. Source/runtime/Git observations are
separate and checked again before publication. Git verification is scoped to
application Python files and dependency manifests, never a claim that the whole
repository or installed dependencies are clean. Keep Git reads bounded, local,
transport-denied and isolated from ambient environment/configuration.
`docs/I8-PORTFOLIO-RESEARCH.md` records the optional `backtest --portfolio` adapter
over the existing mock ledger/executor/portfolio controller. Require complete
pool ownership, explicit synthetic liquidity/FX/exit assumptions and an approved
SQLite source ID. Temporary ledgers are not live state. Preserve delayed fills
and cancel finality, fee settlement bounds, blocked/unfinished cycles and the
distinct result schema. Complete source validation even after a cycle exits.
`docs/I8-RESEARCH-SCENARIOS.md` records the third fixed OHLC convention and
`--compare-scenarios` for both replay engines. Preserve all three outcomes and
their common immutable inputs. Scenario labels never rank profit or choose a
live policy; unknown NAV or different ending horizons cannot form a shared range.
Keep fresh state per sequential member and the combined result cap.
`docs/I8-RESEARCH-PERFORMANCE.md` records additive gross realized statistics and
funded portfolio cash/buy-hold references. Use committed ledger postings for
realized results; preserve unrealized inventory and unposted venue fills separately.
Benchmarks retain all owned pools and end with the strategy, use the same NAV
constraints, and leave unavailable values null. Partial fills of one SELL order
cannot satisfy the minimum distinct-order count for profit factor.
`docs/I8-INDEPENDENT-LOT-REFERENCE.md` records `backtest --compare-lots`.
The fixed-anchor independent-lot policy is research-only; never turn its lot
states into a production multi-SELL mode. Preserve shared parent funding/risk
caps and apply OHLC ambiguity across the original allocation, not separately
per lot. Its fixed levels, shallow admission priority, fee-token escrow and
pending-order behavior are declared conventions, not live strategy amendments.
Continue the offline research and resource gaps recorded there; a green mock suite
does not close external acceptance gates. No new exchange or deployment
authorization is implied.

`docs/I8-RESEARCH-STUDIES.md` records `backtest --study PLAN.json`: declared
chronological folds with fresh-funded training/test replays, spacing selection
from training total NAV only and complete retained held-out outcomes. Keep fees,
latency and OHLC models in separate scenarios; never optimize assumed execution
conditions for favorable returns. Preserve immutable base/slice manifests, caps
and all-or-fail publication. Static rosters and caller evidence do not establish
survivorship-free or preregistered research. No continuous portfolio, independent-
lot study, live export or resource isolation is implied. Continue the remaining
offline work without reusing the completed public-hour authorization.

`docs/I8-RESOURCE-CONTENTION.md` records the finite `operations contention`
experiment: distinct seed partials on 1/7 synthetic venues, one bounded replay
child, pressure pause/resume, durable fill identities and clean writer reopen.
Preserve every financial validation while caching only the fixed codec schema.
Report actual timing failures, CPU/address-space ceilings and logical read pacing
separately from unverified deployment RSS/device quotas. This is not a live
research scheduler or completed Q18 hardware acceptance. Keep workers owned,
bounded and reaped, and never mutate host cgroups to manufacture an acceptance
result. Public-run, account, I9 and deployment authorization remain unchanged.

`docs/I8-ALLOCATION-RISK.md` records per-allocation portfolio replay risk.
Pair committed state with the same complete NAV proof; unknown marks remain
null while quantities/costs remain visible. Account for committed internal
conversion transfers before computing adjusted allocation PnL/drawdown. Never
double-count reservations, reset shared FX capacity for independent marks or
price unknown transfers from later data. Keep explicit sampling and report bounds.
This is additive research reporting, not a change to financial authority or
completion of the remaining research, observed I8, account or deployment gates.

## Invariants for subsequent approved iterations

- Spot only, limit only; geometric grid; minimum executable buy lots; one
  possibly active aggregate sell per allocation; no increasing lot sizing.
- Confirmed fills drive progression. Persist reservations/intents before sends;
  unknown submissions/cancellations block replacement and require reconciliation.
- Decimal or currency quanta for finances. Dedicated allocated funds only.
- One controller/writer, patched SQLite WAL/FULL, bounded queues/caches/retries.
- A +10% net NAV trigger starts a bounded limit exit, not a guaranteed profit.
- Retiring symbols/venues retain supervision of orders and inventory.
- Immutable historical inputs; conservative and explicitly uncertain OHLC fills.
- No credentials, private keys, wallet state, datasets or generated reports in Git.

## Development

Use Python 3.12/3.13, `uv` and the single `uv.lock`. Run the commands documented
in README before committing. Tests must be offline by default and deterministic;
use temporary paths and mock external data. Typecheck application and tests.
Keep commits scoped and describe behavior, validation and remaining limitations.
Do not modify `/opt/module_1_multi` or `/opt/datasets`.

Parallel work is allowed with disjoint file ownership. Report actual evidence;
unavailable repository access or capabilities are BLOCKED/NOT TESTED, not PASS.

## GitHub Free only

The owner uses GitHub Free and prohibits paid GitHub features. Never activate,
trial, buy or use paid plans/products/runners or metered overages; never add
payment details, raise spending budgets or recommend payment as a prerequisite.
Use local tools and verified free GitHub capabilities. Ordinary commit/push
authorization is not permission to spend.

The public repository's standard `ubuntu-latest` runner is free under the
verified GitHub terms. CI must skip private repositories before runner
allocation; never assume unused private-repository allowance. Keep remote
cache/artifact uploads disabled and do not add paid integrations. A billing-lock
message does not establish that an upgrade or payment is needed. Do not retry
a known lock without evidence of changed access; report local checks separately
and use free support/account review when needed. While the lock is unchanged,
use the documented `[skip ci]` commit directive for necessary authorized pushes
so publication does not retry it. A skipped run is not a CI pass. Use the I8 preparation
local-validation and public-paper authorization above. See `docs/RUNBOOK.md` for sources and recovery
guidance.

## GitHub access for workspace agents

The user authorized reusable GitHub access and ordinary commits/pushes for work
within an approved task. Git access alone does not authorize new iterations or
live trading; the current I8 authorization is recorded above. Use the
existing `origin` for `slamerlim/grid-spot-ccxt`; do not force-push or rewrite
published history as part of routine development.

In the prepared `/opt/grid` checkout, repository-local Git configuration selects
a dedicated SSH deploy key outside the repository. Agents running as the same
OS account on this host can reuse it after the owner enables write access in
GitHub. A different host, account or fresh clone needs its own authentication.
Never copy the private key into project files, logs, messages or commits.

The local default author is `GRID Automation <grid-automation@localhost>`, a
neutral automation identity, not the owner's personal GitHub identity. Follow
the checks above, review the staged changes, commit with a descriptive message,
then use `git push --set-upstream origin HEAD`. Check the command's result and
remote commit before reporting publication. See `docs/RUNBOOK.md` for setup,
verification and revocation; a configured remote alone is not proof of access.
