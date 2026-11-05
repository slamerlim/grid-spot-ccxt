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

`docs/I8-PORTFOLIO-SERIES.md` records bounded continuous portfolio replay.
Use one ledger and original funding across at most 32 explicitly requested
cycles; restart on a later bar only through the reconciled controller begin path.
Keep conversion allocations paused, residual ownership intact and order/fill IDs
continuous. Distinguish each cycle's outcome from original-funding returns and
keep cumulative risk/benchmark baselines unchanged. Account for extra completion
intervals within existing bounds. This does not integrate portfolio walk-forward
studies or authorize further exchange access, I9, paid GitHub or deployment.

`docs/I8-PORTFOLIO-STUDIES.md` records portfolio walk-forward integration.
Compare all candidates at the same declared window endpoint, continuing fresh
valuation after the requested cycle cap while preserving completed exit receipts.
Keep funding continuous within each window and reset explicitly between separate
training/test experiments. Unknown training NAV withholds the whole fold/scenario
selection; never discard the uncertain member or replace a winner from test data.
Retain all outcomes, preflight every effective configuration and count EOF plus
completion intervals in the risk bound. Static historical assumptions, independent-
lot studies, resource/deployment evidence and external I8/I9 gates remain unfinished.

`docs/I8-STORAGE-QUERIES.md` records the bounded UTC query benchmark.
Keep the optional shared timestamp index manifest-bound and account for its
preparation cost. Exact half-open windows preserve gaps/empty results; all
formats and repeats must match raw record bytes before publishing. Retain work
caps, rotating reader order and source/derived identity checks. Warm-cache
query timings, logical byte counts and observed NVMe backing are distinct from
cold-device/replay throughput or deployment certification. Multi-source merges,
replay reuse, resource evidence and external gates remain open. Keep raw inputs.

`docs/I8-LOT-STUDIES.md` records `--study PLAN --compare-lots`.
Select spacing independently from each research policy's training NAV and retain
all held-out outcomes. Compare complete common configurations, slice manifests
and funding; count both policies in replay/bar limits and the combined report
cap. Distinguish matched-candidate differences from separately selected-candidate
differences. Reference studies have no portfolio exits or live policy export;
production still has one aggregate SELL. Historical assumptions, resource work
and external acceptance gates remain open.

`docs/I8-STORAGE-MERGES.md` records `bench-storage --merge-plan PLAN`.
Preserve source windows and lexicographic market tie ordering across formats;
require individual source and combined chronological hashes. Bound the roster,
aggregate records, decoded blocks, derived artifacts and reports. Close each
block reader before yielding rows; do not hold one file open per symbol.
Synthetic 100/1,000-source warm measurements favor retaining raw and do not
certify device throughput, archive representativeness, replay or deployment.
Keep all original inputs and remaining external gates unchanged. Continue the
authorized offline gaps without further public requests or paid GitHub features.

`docs/I8-MIXED-CONTENTION.md` records optional repeated BUY/SELL contention rounds.
Keep minimum buys, one possibly active SELL, late inventory and cancellation
finality under pressure. The executor may avoid duplicate posting only after
the complete snapshot and exact immutable SQLite binding/fill records have been
validated by its sole writer. Do not cache financial facts or skip current open,
partial, uncertain or changed reports. Retain failed timing evidence and the
unchanged workload/threshold when measuring improvements. This finite isolated
mock experiment does not cover 1,000 funded allocations, sustained fixed-rate
traffic, the portfolio supervisor, deployment quotas or the observed I8 gate.
Continue authorized offline work; no further public access is implied.

`docs/I8-SDK-ORDER-AUDIT.md` records the fixed offline `exchange.order_audit`
command. Pinned asynchronous SDK builders use invented inputs and stop at the
first intercepted request; lower signing/transport paths are prohibited. Preserve
the BaseException stop sentinel: ordinary SDK exception fallback must not assume
account mode after interception. No arbitrary credentials, order parameters or
exchange access belongs in this command. Keep numeric/ID findings visible; it is
not a trading gateway or venue acceptance. Do not truncate durable IDs or bypass
BingX float conversion, XT/Bitfinex rules, account-mode verification or live gates
based on this diagnostic alone.

`docs/I8-BINGX-TEXT-PREPARATION.md` records the subsequent fixed `--bingx-text`
trial. Its decimal-string override exists only in guarded disposable BingX
diagnostic clients on the pinned SDK; never patch global classes or install it
as a trading sender based on this experiment. Preserve unmodified controls,
independent exact-payload/query checks and lower signing/transport refusals.
No timestamp/signature or authenticated request is produced. Passing candidate
fixtures does not establish venue ID acceptance, rules, account mode or readiness.
Continue authorized offline work; exchange access and deployment gates remain.

`docs/I8-DURABLE-CLIENT-IDS.md` records schema-6 immutable external-ID mappings
and the explicitly admitted `MappedSyntheticExchange`. Keep full internal IDs,
account-scoped uniqueness and original registration receipts. Registration must
precede an attempt; it is never send permission. Preserve IDs after terminal
orders and reject retroactive registration or policy changes. Normalize returned
IDs through the sole writer before binding/posting; never adopt by price/size.
No venue profile is certified, and default mocks remain unchanged. Old code
cannot open v6; never drop mappings to downgrade or recreate unknown submissions
after restoring an older backup. Continue offline work with external gates closed.

The owner's CCXT clarification and numeric correction are recorded in
`docs/I8-CCXT-UNIFIED-NUMBERS.md`. CCXT owns exchange-specific parsing and unified
structures. Use its documented `number = str` mode with shared Decimal domain
conversion; do not substitute `Decimal` into the SDK or duplicate venue parsers.
Keep compatibility exceptions narrow, pinned and reproduced by offline fixtures.
The current public boundary retains exact book-level parsing and numeric sorting
for demonstrated SDK gaps. Missing optional data remains unknown; require only
the evidence needed by the affected operation. Accounting, ownership, identity
and reconciliation remain application responsibilities. Pure OKX parser fixtures
do not establish authenticated support or certify the other venues. Continue
offline development; completed public-run and live gates remain unchanged.

`docs/I8-UNIFIED-RECONCILIATION.md` connects shared CCXT observation parsers to
the mapped mock executor. Its reports now use unified dictionaries instead of
`WireOrder`. Prove identity/attempt/terms through the sole writer. A missing client
ID may use an existing exact scoped binding; a supplied unknown ID cannot.
Optional order terms/trade side may come from the proven owned intent, while
status, cumulative quantity and actual execution/fee evidence remain required
for the complete reconciliation snapshot. Never infer chronology from page order
or FX from later quotes. The fake supplies explicit ordering/settlement context;
only a known zero fee has zero cost without FX. Preserve full-snapshot validation
before binding/posting and all original financial/crash invariants. No private
transport, real pagination contract, venue certification or new access is implied.

`docs/I8-RECOVERY-PLAN.md` adds read-only planning from the sole writer and
bounded inspection of known external order IDs through the shared unified reader.
Keep attempted orders with unknown external IDs explicit; registration alone is
not an attempt. Validate original placement terms and exact durable bindings,
preflight the whole call budget and recheck the plan after reads. Missing optional
descriptive fields remain unknown. Inspection cannot post money, clear uncertainty
or grant readiness. Retain the executor's complete-history/terminal checks until
account-wide discovery, unknown lookup, committed-fill comparison and settlement
ordering are integrated. No new exchange access or acceptance waiver is implied.

## Invariants for subsequent approved iterations

`docs/I8-ORDER-HISTORY.md` adds a shared per-order read primitive, tested with
offline fakes and guarded pinned OKX unified methods. At most three unified calls
bracket one limited trade query; unique quantities must match stable cumulative
reports. Preserve optional unknowns and cancellation. Quantity matching grants
no ownership, fee, chronology or send authority. SDK internal HTTP requests and
bytes still need transport enforcement. Keep the executor's existing omission
and terminal checks until complete account-wide recovery replaces its fake
history contract. No authenticated transport or new exchange access is implied.

`docs/I8-TERMINAL-RECOVERY.md` distinguishes delayed private events from a current
recovery snapshot. An observed OPEN or changed cumulative quantity for a durable
terminal order invalidates the entire snapshot before new binding/fill posting.
Keep that check for compacted orders as well as retained ones. Do not erase old
facts or loosen omission checks to implement bounded history; the existing fake's
complete-history/revision/sequence contract is not a proven CCXT capability.

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
