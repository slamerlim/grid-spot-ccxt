# GRID implementation rules

Read the full PRD, ADR-0001, acceptance plan and master implementation prompt
under `docs/` before editing. They are authoritative over the legacy simulator.
User instructions take precedence. Preserve imported specifications verbatim;
record clarifications separately with evidence and approval requirements.

## Simplicity and development focus

The owner's attached Module 1 C++ backtest master prompt now authorizes offline
research implementation and actual full-history replay from `/opt/datasets/`.
Read `docs/MODULE1_RESEARCH_SPEC_v1.md` and the attached prompt before that work.
Never modify or remove any dataset. Preserve the Python live core, executor,
ledger/schema, CCXT gateway and operations behavior. New seed/ladder and tier-TP
policies are research-only. Explicit assumption-based replay is authorized even
when source provenance is unverified; never relabel it certified historical
performance or weaken the older strict replay's admission. C++ research, local
tool restoration, tests and historical runs are authorized; no exchange access,
credentials, live/sandbox orders, deployment or Module 2 work is included.
Use existing ordinary commit/push authorization and GitHub Free only. Large
outputs belong outside Git, compact manifests and reports may be committed.

The owner reinforced: "don't overengineer, the system shall be easy and robust
as ak-47, remember the initial prompt!" Keep the original single-process design:
official CCXT, one executor, one SQLite writer and a pure strategy core.
Prioritize a working, integrated execution/recovery path. Consolidate existing
helpers before adding parallel APIs, frameworks or speculative abstractions.
Add an abstraction only for a concrete implementation need or useful removal of
duplication. Preserve exact accounting, ownership and unknown-order safeguards.
Keep required tests focused on observable behavior and failure recovery. Update
existing review/runbook documents for small changes instead of creating another
plan/evidence document each time. Finish the essential runtime before expanding
optional research or diagnostics. This changes priorities, not PRD acceptance.

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

The current ordering integration is recorded in `docs/IMPLEMENTATION-REVIEW.md`.
Use a proven source sequence or distinct unified trade timestamps for new fills,
never array order or lexicographic trade IDs. A batch must have one ordering kind
and advance the committed position; ties, missing data or regressions stop it
before financial posting. Schema 7 records each position with its original fill
receipt in the same transaction. Do not fabricate positions for pre-existing
fills or switch ordering kinds to bypass a stop. Legacy ledgers remain readable
but the executor requires a complete ordering baseline before sending again.
Keep original migrations/financial payloads unchanged and preserve backup/restore
and crash checks. Timestamp semantics and finer ordering still require actual
venue/account evidence before live use; the pinned parser fixtures are offline.

`docs/I8-RECOVERY-PLAN.md` adds read-only planning from the sole writer and
bounded inspection of known external order IDs through the shared unified reader.
Keep attempted orders with unknown external IDs explicit; registration alone is
not an attempt. Validate original placement terms and exact durable bindings,
preflight the whole call budget and recheck the plan after reads. Missing optional
descriptive fields remain unknown. Inspection cannot post money, clear uncertainty
or grant readiness. The executor now shares this plan for bounded mock recovery;
current terminal checks remain mandatory. Real account-wide discovery, unknown
lookup and settlement ordering still need integration. No new access is implied.

`docs/I8-COMMITTED-RECOVERY.md` adds bounded durable fill baselines to both
read-only inspection paths. Recent unified windows may omit old fills only when
the ledger proves their identities, canonical payloads, fees and cumulative
quantity/cost. Recheck this baseline after reads; every new quantity must be
covered by new observed identities. Compare returned terminal and other-allocation
trades by scoped identity without adopting them or rewriting settlement facts.
Optional unknowns stay unknown. Keep standalone history readers strict, inspection
classifications separate from financial posting, and all call/deadline/record
bounds. The executor integration is recorded in `docs/IMPLEMENTATION-REVIEW.md`:
all attempted working orders remain required, while committed old quantities
come from SQLite. Both mocks return current open orders, exact requested clients
and bounded recent history. Revalidate the plan before posting, preserve fee-stop
intents across compaction/restart and never clear an unknown from absence alone.
The finite fake's source completeness and ordering are not CCXT guarantees.
Real account discovery, history continuation and settlement evidence remain open.
No new exchange access or schema change is implied.

The account inspection can explicitly use the same caller-owned transport for
bounded open/closed order lookup when an attempted order has a saved client ID
but no exchange ID. Match only the exact registered returned client ID; preserve
unmatched orders and reject duplicate claims or binding/term conflicts. Preflight
the worst-case call budget and recheck the sole-writer plan after each read phase.
Discovered IDs remain read-only observations; original unknowns, holds and send
restrictions stay intact. Absence is never rejection, including when the SDK's
closed-order method omits canceled orders. See `docs/IMPLEMENTATION-REVIEW.md`.

The mapped mock executor now accepts explicit `account_reads` and optional
`lookup` on `service`. It invokes the existing reader under its one lock, reserves
the worst-case unified-call budget, rechecks the plan/epoch and uses its existing
binding/fill/report loop. Standalone inspections remain read-only. The executor
can resolve individual order identities and post verified fills, but account
reads cannot clear the global recovery hold or grant dispatch readiness; `False`
does not mean that no fills committed. Reject unowned observations and incomplete
fee/ordering evidence before binding. Recheck returned committed timestamps.
Preserve crash recovery, cancellation finality and durable fee-overrun stops.
No real client construction, account access or sender is enabled by this path.

## Invariants for subsequent approved iterations

`docs/I8-ACCOUNT-HISTORY.md` adds the explicit unified `fetch_my_trades` alternative
to per-order history. Query one bounded symbol page for all known order IDs and
retain unmatched trades, including missing order attribution. Such observations
cannot satisfy an owned order's cumulative total or enter financial posting.
Check duplicate IDs across the entire page and preflight the full `2*N+1` call
budget. Reuse the writer's plan/terms/revalidation checks without clearing halts
or unknown submissions. Offline XT/BingX SDK fixtures are not account acceptance;
bounded continuation, account-wide discovery and financial integration remain
open. No new exchange access, live transport or acceptance waiver is implied.

`docs/I8-ORDER-HISTORY.md` adds a shared per-order read primitive, tested with
offline fakes and guarded pinned OKX unified methods. At most three unified calls
bracket one limited trade query; unique quantities must match stable cumulative
reports. Preserve optional unknowns and cancellation. Quantity matching grants
no ownership, fee, chronology or send authority. SDK internal HTTP requests and
bytes still need transport enforcement. Keep the executor's working-order
coverage, quantity and terminal checks. No authenticated transport or new
exchange access is implied.

`docs/I8-TERMINAL-RECOVERY.md` distinguishes delayed private events from a current
recovery snapshot. An observed OPEN or changed cumulative quantity for a durable
terminal order invalidates the entire snapshot before new binding/fill posting.
Keep that check for compacted orders as well as retained ones. Do not erase old
facts or drop working-order coverage to force recovery. The fake's current-roster,
revision and sequence contracts are not proven CCXT capabilities.

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

The owner requested a minimal local web interface for existing backtests.
`python -m scripts.backtest_web` wraps the existing Module 1 executable and
financial checks; see `docs/BACKTEST_UI.md`. The owner subsequently requested
`http://103.1.225.114/module1/backtesting`, authorizing deployment of this
research UI on that host. Keep the Python listener on loopback, with the
public Nginx path and the dedicated `grid-backtest-ui` service. The owner
subsequently removed HTTP Basic login for this research UI; keep the listener
on loopback and preserve bounded requests and one owned worker. Anyone who can
reach this HTTP URL can start or cancel research runs and view their results.
This supersedes the previous local-only restriction for this UI only. Keep one
owned worker per output directory and no exchange transport. Preserve
the frozen study and dataset bytes; retain failed/cancelled exploratory runs
outside Git. Frontend display rounding must never alter exact engine inputs
or results. This does not authorize live trading, bot deployment or paid GitHub.

Use Python 3.12/3.13, `uv` and the single `uv.lock`. Run the commands documented
in README before committing. Tests must be offline by default and deterministic;
use temporary paths and mock external data. Typecheck application and tests.
Keep commits scoped and describe behavior, validation and remaining limitations.
Do not modify `/opt/module_1_multi`, `/opt/module1_n` or `/opt/datasets`.

The owner explicitly approved "Exact matching research mode; keep funded
validation separate" on 2026-11-11. New paired UI runs use a single shared
configuration for the current C++ `compare-reference` command and the unchanged
audited executable. Freeze the same source, UTC window and precision for both;
verify deterministic summaries and full trade/chart bytes. The compatibility
mode deliberately reproduces the reference's unfunded synthetic O/H/L/C and
floating-point research conventions. It is isolated from exact funded accounting
and the Python live core. Do not relabel it funded validation or a profitable
trading-engine upgrade. Preserve older unequal-model runs with explicit context;
new paired launches must reject separate divergent engine settings. Use copies
of the audited binary with all new outputs outside `/opt/module1_n`.

The owner also requires one-click whole-exchange selection, multiple-pair totals
and a detailed per-pair table. The owner explicitly chose each pair's own full
available history for whole-exchange runs when no shared window exists. Dates
must still match exactly between engines for each pair. Keep shared-period mode
as an alternative. The owner's subsequent instruction requires combined charts
and ending PnL to include every simulated trade in the selected histories; this
supersedes the earlier restriction to overlapping timestamps. Process all retained
trades and chart closes, with OHLC aggregation preserving event extrema. Before
a pair begins its contribution is zero; between updates and after its history
ends carry its last recorded mark and disclose that it can be stale. Ending PnL
is the sum of all included pairs' terminal summaries, even at differing dates,
not synchronized portfolio NAV. Continue other pairs when one is invalid; label
partial results and exclusions. Sum only native-quote-compatible amounts; never
add FX currencies or individual drawdown maxima. Explain the deterministic
synthetic tie order and the limits of trade/retained-close drawdown coverage.
Retain missing marks and original precision. Funded accounting stays exact;
matching research preserves the immutable reference's long-double/binary64
contract. Float32 archives cannot regain discarded digits. Display rounding
must not alter settings, accounting or downloadable raw artifacts.

Whole-exchange preparation must run outside the HTTP event loop. Reuse at most
one parsed saved venue snapshot, keyed by its complete file identity and
invalidated on change/removal. Freeze just the selected market's unchanged
precision entry for each new pair; both engines reuse that identical snapshot.
Large totals run in one background calculation and return HTTP 202 while busy;
reuse only a disposable result whose complete retained-input and output identities
remain unchanged. Preserve full hash verification before publishing totals.
Identical, verified full trade/chart files and summary values may share one
derived aggregate calculation; verify both artifacts and record that reuse.
Interrupted/cancelled matching suites may be explicitly resumed with their
original request: revalidate completed pairs, reuse only exact matches, retain
failed pairs, and replay unfinished pairs from the beginning. Never resume
automatically at startup or change settings to bypass an archived refusal.

The owner requires historical USDT valuation across all quote currencies and
reiterated that the actual strategy must use the smallest executable order for
each venue, symbol and limit price. A constant 10 native quote units is only
the immutable simulator's comparison parameter, never an exchange-minimum rule.
Use verified per-market amount/notional limits and precision for minimum sizing;
do not treat precision alone, 5 native quote units, or 10 USDT as a universal
minimum. Missing historical market rules remain explicit research assumptions.
The funded calculator already computes minimum buys; its synthetic UI rules do
not certify actual exchange minima. Keep module1_n unchanged, and explain that
fixed-quote exact parity and a minimum-order strategy are different experiments.

Historical USDT reports are a derived reporting layer, preserving the original
native runs and their parity. Use same-venue direct/inverse/two-leg archived
conversion prices, with only USDT/USDT fixed at one. Never assume USD or another
stablecoin is USDT. Use completed minute closes, no future conversion data,
interpolation or stale price carry; missing values make totals incomplete.
Distinguish historical-cost trade PnL from native balances translated at the
valuation time and from funded portfolio NAV. Unfunded reference output cannot
establish funded NAV/ROI. Expose arbitrary completed-minute inspection and the
sampling limits of overview charts. Preserve archives and the audited simulator.

The owner authorized evaluating minimum sizing in the funded research engine,
while retaining module1_n as a labeled fixed-size benchmark, and requested
full-period/monthly PnL %, Profit Factor, Win Rate and Sharpe at the UI top.
The owner selected the current funded engine's actual capital amount as the
reference denominator; do not invent a separate capital or hindsight peak-funding
baseline. Native quote capital remains explicit and is valued with historical FX.
Only same-roster, same-window funded runs may appear as comparison choices.
Minimum mode uses each market's saved CCXT limits and compatible accounting
quanta, never a universal notional or precision. Missing rules, unsupported modes
and the unvalidated deprecated Bybit mapping remain refusals, not guessed minima.
Keep dated current rules applied to history labeled assumption-based.

Metrics use USDT marked equity after paid fees, including open inventory and quote
FX; monthly returns start from that month's equity. Use UTC calendar months and
complete UTC daily simple returns for Sharpe, sample SD, zero risk-free rate and
sqrt(365) annualization. Missing daily/end marks, zero variance or insufficient
reference cash make affected values unavailable. Profit Factor and Win Rate use
net completed positions, combining partial sells; breakevens stay in Win Rate's
denominator. No losses means undefined Profit Factor, never fabricated infinity.
Preserve every fill and the original artifacts; snapshot charts are not a source
for trade counts or exact monthly statistics. Funded fills use exact sidecars,
receipt hashes and completed-bar timestamps; arbitrary-minute USDT inspection
must keep native quantities, fees, actual cash, equity and trade PnL distinct.

The owner's 2026-11-12 HTX/Gate audit brief authorizes targeted implementation in
four review batches: (1) public market rules and funded sizing, (2) accounting and
metrics, (3) persistence/dashboard, (4) justified historical and browser checks.
HTX and Gate are the priority venues. Return a review checkpoint after Batch 1
and Batch 2; do not run broad historical replays in Batch 1. Reuse the retained
65 GB archive audit and verify file identities instead of rescanning unchanged
files. Keep module1_n, datasets, funding amounts/allocation and strategy intact.
Only bounded public spot metadata requests are authorized; no private calls or
orders. Refresh HTX/Gate through `scripts.backtest_minimum`, outside module1_n,
using pinned CCXT and the existing bounded transport. Preserve dated snapshot
versions, raw exceptions, maximum bounds and required price-reference context.
Historical price-band checks use explicit close-based proxies, never claims of
observed venue reference prices or historically verified rules. Do not silently
ignore Gate's two-minute reference window or bridge missing minutes.

Parallel work is allowed with disjoint file ownership. Report actual evidence;
unavailable repository access or capabilities are BLOCKED/NOT TESTED, not PASS.

On 2026-11-12 the owner redirected the historical-data collection batch:
"stop struggling on Gate, let's use HTX for now". HTX is the active venue;
stop Gate requests, downloads and gap investigations unless the owner reopens
that work. Retain existing Gate artifacts. Use the HTX-only collection manifest
under `/opt/grid-runs/historical-collection-20261112/htx-focus`; do not resume
the earlier combined manifest. The collection brief authorizes bounded public
historical source checks and separate immutable storage, without credentials.
Reuse the retained integrity audit. Preserve datasets, module1_n, trading rules,
funding and prior results. Complete the acquisition/validation report and stop
for review before Batch 2 accounting, metrics or dashboard changes. Candle
timestamp coverage is not proof of fresh trades, liquidity or exact fills.

The owner then explicitly stopped further data fetching: existing HTX coverage
is sufficient for the next work. Do not fetch more archives or pursue residual
gaps unless the owner requests it. Focus on the remaining accounting, historical
USDT reconciliation and metrics requirements, using existing HTX inputs. The
current request is to present that implementation plan; preserve the Batch 2
review checkpoint before dashboard work.

The owner's subsequent HTX-only Batch 2 brief explicitly authorizes implementing
Steps 1–4 in order: completed cycles/PF, funded accounting and historical USDT
reconciliation, authoritative portfolio metrics, and independent HTX verification.
Validate each stage before continuing. Complete Step 4 and then stop for review;
do not stop after Step 1 merely to request the already-granted scope again.
No Gate processing, exchange requests, data collection, strategy/funding/sizing/
fee/execution changes, legacy changes, or Step 5 frontend work. Preserve Batch 1
HTX snapshots/rules and all historical inputs. New evidence belongs under
`/opt/grid-runs/htx-batch2-20261112`. Missing required marks make Sharpe/drawdown
explicitly unavailable; do not quietly use a subset. Portfolio metrics use
combined equity and completed cycles, never averages of pair-level ratios.

The owner's subsequent instruction restricts all NEW backtests and trading
selections to spot pairs quoted in USDT. This supersedes earlier requests to
admit BTC/USDC/other quote currencies and authorizes the narrow selector/API/
configuration/admission changes needed to enforce it. "Select all" means all
eligible USDT pairs on that exchange. Reject explicit non-USDT requests before
execution. The owner explicitly requires that the panel send no non-USDT job to
module1_n: enforce this in direct/bulk/matched launch, resume and worker admission,
including the archive filename's quote; do not rely on dropdown filtering alone.
Preserve all existing datasets, module1_n, saved runs and historical
accounting evidence; old records may remain readable. Do not run further
non-USDT historical simulations. This does not authorize unrelated dashboard
metrics work, live trading, new exchange calls or a change to sizing/funding.

## Funded research floor grid — owner clarification, 2026-11-17 UTC

The owner confirmed logarithmic price spacing as
`log(Pn) = log(entry) + n * log(1 - step_pct/100)` for new funded C++ runs.
Use the shared `step_pct` control; no second funded spacing parameter. This is
an explicit research clarification to the older reciprocal-step contract, not
a change to the Python live strategy or the unchanged module1_n comparison.
Infinite BUY mode means every distinct minimum LIMIT level down to the entry
floor is planned/reserved together, with no seed fill and a fixed completed-close
anchor. The floor may be at most 60% below entry; its final interval can be
shorter and its price rounds upward to a valid tick. Inadequate funds, duplicate
ticks, market constraints or the finite research capacity refuse the whole grid.
Never truncate it to a convenient funded subset or create unfunded orders.

Exchange and global take-profit default independently to 10% of original scoped
funding, using complete net liquidation equity including inventory and fees.
Global takes priority. Reconcile cancellations, use LIMIT exits only, retain
blocked exposure and keep a completed scope stopped for the remainder of the
run. New panel runs use the completed-close proxy without synthetic spread.
Keep archived contracts/results accurately labeled and unchanged.

The owner requires implementation, generated-data tests and C++ profiling first,
then separate approval before any historical batch. The old 185,376-job matrix
remains stopped; never resume it or replace its frozen source hashes. Preserve
datasets, module1_n, the reviewed Batch 2 tree and earlier isolated candidates.
See `docs/BACKTEST_UI.md` for the effective settings and validation evidence.

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

## Interview research sweep — owner approval, 2026-11-19

The owner explicitly approved implementation of the plan based on
`docs/RESEARCH_INTERVIEW_2026-11-19.md`: a separate unfunded, exact research
sweep of 540 configurations, all archived spot USDT pairs on the seven venues,
and each pair's complete available history. This supersedes the earlier
historical-batch approval restriction for this new mode only. Full hypothetical
fills, strict price crossings, O/H/L/C event ordering, minimum buys, gross local
TP, 0.2% USDT fees per side, a 60% lower grid boundary and no global sale are
explicit research assumptions. Keep the funded modes and Python live core intact.

The approval includes implementation, local tests, bounded public spot-rule
catalogues on all seven venues, load testing, technical calibration, existing
research-UI integration and the full run after its declared gates. Do not fetch
new price histories, edit source datasets, access accounts or send orders.
Missing required pair rules block the full roster; never invent minima or
silently run a smaller full study. The owner selected checkpoint recovery and
an explicit resume action. Preserve failed/cancelled evidence outside Git.
See `docs/RESEARCH_SWEEP.md` for the interface and actual validation status.

The owner's subsequent 2026-11-19 filtered full-backtest plan supersedes the
strict all-pair admission rule for a separate, explicit selection action.
It permits excluding pairs only for unknown agreed research-model rules,
with a recorded reason per pair, and requires at least one admitted spot USDT
pair on each of the same seven venues. Reaudit all original sources, preserve
the original strict manifest and datasets, and bind every validation and run
stage to the new selected manifest. The full history of every admitted pair
and all 540 configurations remain required. Do not present a four-venue or
partially completed run as the authorized full seven-venue result.

The owner approved the subsequent acceleration plan: new research jobs use
`chart_mode=on_demand`, retaining every exact financial result, extremum/date
and trade hash. Aggregate charts replay one selected configuration over its
original complete roster/horizon and must match the original results before
caching. Preserve earlier eager-chart results and the separate pair chart.
The new `optimized-600s-v1` acceptance policy replaces the one-hour endurance
gate with 600 seconds on all 4,792 admitted pairs and 540 configurations, at
least two durable checkpoints and verified recovery. This is a ten-minute
test, never evidence of a one-hour pass. Then perform the seven-day rehearsal
and full histories with 32 calculation threads across configurations. Bind
all new evidence to the exact code, executable and unchanged selected manifest;
do not resume old checkpoints with a changed build. Short current-candidate
comparisons are authorized; do not restart lengthy old-version benchmarks.
Keep old artifacts intact, source datasets immutable and existing resource
limits, exact accounting, checkpoint publication and source hashes enforced.

The owner subsequently approved the expanded research TP matrix (2026-11-20):
540 existing combinations × 28 exchange TP choices × 28 global TP choices =
423,360 configurations. Each extra axis is off or 2–15% in 0.5-point steps;
the exchange threshold is shared but each venue acts independently. After
ordinary trades at each common O/H/L/C point, global TP takes precedence over
venue TP. Aggregate profit includes all exit fees and is compared using >= to
the attained peak spent of that scope's current cycle. Aggregate liquidation
immediately sells all owned positions at their last known prices, even stale;
preserve quote timestamps separately from sale timestamps. This is a new
research assumption, not live/funded permission. Reset only the exited venue's
cycle on venue exits, all cycles on global exits; reseed all affected pairs on
their next actual price observation, retaining lifetime totals. Use bounded
configuration batches and binary exact financial summaries for every config;
pair details and charts replay on demand with result/hash verification. Do not
load the full matrix's states into RAM or change/delete old data/artifacts.
The accepted answers are in docs/RESEARCH_INTERVIEW_2026-11-19.md.

On 2026-11-21 the owner increased the research artifact limit to 64 GiB and
requested individually measured optimizations which preserve exact financial
results, events, hashes, coverage and recovery. New study identities bind this
limit; old studies/checkpoints retain their recorded limits. Keep the 64 GiB
worker RSS limit and 32 GiB free RAM/disk floors. Do not modify old artifacts
or original datasets. No further web-interface work is requested.

The owner subsequently requested monthly and final spent/PnL/profit factor and
Sharpe/Sortino immediately for every expanded-matrix configuration, for the
portfolio and seven venues, followed by speed checks, ten-minute load and the
seven-day rehearsal. PF uses completed pair positions after both fees, grouped
by sale month. The owner selected monthly observations and cumulative monthly
Sharpe/Sortino from history start, with 12-period annualization; partial months
stay in financial totals but not the complete-month risk sample. Preserve exact
money/moments and explicit unavailable ratios. Keep charts/pair details on
request, all source histories and old artifacts intact; no UI work is requested.

The owner then explicitly required the load test to cover **all 423,360
configurations**, superseding the 540-representative expanded-matrix load stage.
The `complete-matrix-600s-v2` policy processes all configurations in bounded
batches over one identical deterministic synthetic history, retaining all 4,792
admitted pair IDs, venues and rules. Require at least 600 seconds of summed
complete-batch replay, two durable checkpoints and exact full-batch recovery.
This is not 600 seconds per batch or a simultaneous full-matrix RAM allocation.
Only short calibration may use the 540 representatives. Old load evidence
does not satisfy this expanded coverage; keep interrupted artifacts intact.

The owner's subsequent selected-matrix plan supersedes that configuration
count for the current research: exactly 4,480 combinations, grid step 1–10%
by 1, pair TP 2–8% by 1, and independently off or 2–8% by 1 for exchange and
global TP. Keep the grid depth at 60%, all 4,792 admitted pairs and their full
histories. Both common TP axes remain present. Preserve canonical config IDs
and bind admission/recovery to the immutable selected list, not 423,360.
Use framed C++ MessagePack and exact compressed positional records in
`results.bin`; `results.sqlite` is only an index. Production summary/execution
JSON must not contain financial tables. Preserve reading of old artifacts.
New evidence requires a real short measurement with 32 calculation threads
and batches of 1,024, then all 4,480 configurations on all admitted pair/rule
identities in the load test. Start at exactly 1,024 synthetic records per source
(preserve time gaps; a minute-count fixture with fewer records is insufficient) and double
up to 32,000 if summed replay is below 600 seconds; prove two checkpoints and
exact full-batch recovery, then the seven-day rehearsal and full histories.
The 1–3 day duration is a target, never permission to drop work. Keep the
64 GiB artifact/RSS limits, 32 GiB free RAM/disk floors, datasets, old evidence
and the web interface unchanged. Local implementation and these runs are
explicitly authorized; no new live/exchange or paid GitHub permission is implied.

On 2026-11-21 the owner explicitly removed the seven-day rehearsal from the
current selected-matrix chain: "давай обойдемся без недельной репетиции и
приступим к полному прогону сразу после проверок". The required order is now
validation/calibration, the complete selected-matrix load and exact full-batch
recovery with two checkpoints, then full history directly. Record the rehearsal
as WAIVED_BY_OWNER, never passed. Keep all other admission, financial, source,
identity, resource and final independent-audit checks. The dated amendment is
`/opt/grid-runs/tp-selected-bin-v086fr43/exact-records-r2/rehearsal-waiver.json`.
This only changes scheduling; the same 4,480 configurations, 4,792 pairs,
32 workers, 60% depth, financial code/executable and immutable study remain.


The owner then explicitly replaced the entire old backtest web interface with
an SSH-only Streamlit results table. This supersedes prior no-new-UI and public
Nginx instructions for this requested replacement only. Remove the old HTTP
server/static assets and its service/proxy configuration; keep Nginx stopped,
disabled and masked. The new `grid-results-viewer` service binds only
127.0.0.1:80 at /module1/backtesting/. Client access is the approved SSH port
15407 tunnel from 127.0.0.1:8888 to the host's 127.0.0.1:80. Keep the viewer
read-only over published `results.bin` and its SQLite index, with portfolio/venue
and overall/month choices and sorting/filtering on every table field. Display
rounding must not change exact numeric filters, sidebar sorting or saved data.
No calculation launch/cancel/resume, exchange transport or deployment of the
trading bot is authorized by this viewer change. Preserve offline engine/CLI,
original datasets, completed artifacts and financial rules. Continue to use
one uv.lock, local tests and GitHub Free only. See docs/BACKTEST_UI.md.

The owner subsequently approved the selected-configuration detail replay plan.
This supersedes the viewer-only launch restriction for one explicitly confirmed
configuration at a time, on its original complete 4,792-pair seven-venue history.
Streamlit must offer Yes/No confirmation; dismissal does not start work. Use a
separate owned worker and the existing heavy lock, explicit checkpoint resume,
binary exact details and read-only access to old results/datasets. No matrix run
or exchange transport is authorized. Monthly drawdown means the largest net
PnL shortfall from its lifetime peak attained in the month, carrying peaks across
months (historical peak 100, path 80/70/90 means 30). Percentage uses that month's
peak spent; medians use complete UTC months. Save portfolio/venue monthly risk,
charts and pair totals, not a full trade journal. Derivatives must match all old
financial fields and hashes before publication. Keep the SSH-only listener,
Nginx masked, 64 GiB artifact/worker RSS caps and 32 GiB free RAM/disk floors.

## Gap-free research selection — owner approval, 2026-11-22

The owner approved a separate derived selection excluding each exchange+pair
with even one missing minute between its own first and last archive records.
Use five venues (BingX, Gate, HTX, KuCoin, OKX); Bitfinex and XT have no eligible
gap-free sources in the frozen 4,792-pair parent. Expected structural selection
is 1,644 candidates and 3,148 gap exclusions, not final historical certification.
Do not fill gaps, trim histories, impose a new minimum duration, globally ban a
ticker because another venue has gaps, or overwrite old manifests/results/data.
Reaudit complete bytes, timestamps and SHA-256 before publication; keep exact
parent/roster/policy identities and all 4,480 selected configuration IDs. Old
seven-venue admission remains strict for its original policy. New-policy common
TP requires a current observation at the shared OHLC point for every open
position in that scope. Missing/ended prices prevent that trigger, not ordinary
trading or another fully observed venue's TP. Full execution remains assumed;
no fictitious stale-price sale or zero valuation is allowed. A started exit is
not revoked because its expected profit falls. Preserve explicit stale marks
and open remnants. This approval covers implementation, bounded exact tests
and the new source audit/selection, not a new full backtest: suspicious-price
and historical-rule decisions remain pending. Use local tools and GitHub Free
only; preserve the SSH viewer and live/funded behavior.

The owner reiterated that all identified unclear/abnormal assets must actually
be excluded, not retained as warnings. The derived selection-v3 now removes
749 additional exchange+pair sources from the 1,644 gap-free candidates and
retains 895 on the same five venues. Its immutable exclusion review is under
`/opt/grid-runs/reviewed-selection-20261122-1mgj2aww/`; use its `study/`
for subsequent preparation, not the superseded 1,644-pair study. Preserve all
4,480 configuration IDs and original data/results. Explicit conservative screens
exclude symmetric minute price ratios >=2, identical flat OHLC runs >=1,440
records, identical nonflat runs >=60 records, and identified unsupported
leveraged/equity/rebasing asset models. These screens can reject real market
events; they are uncertainty exclusions, not proof of corruption or certification
of the remaining prices. No exclusion uses backtest PnL. Historical-rule policy
and the reproduced exchange-calendar statistics defect remain unresolved;
full launch/resume is blocked pending their resolution. See docs/RESEARCH_SWEEP.md.

Subsequent owner decisions supersede that historical-rule hold: use the dated
current public CCXT snapshot on full histories, the corrected own-venue calendar,
and the separate one-hour-flat-price exclusion. The completed active study now
contains 109 pairs on BingX/Gate/HTX/KuCoin/OKX and 4,480 configurations; its
immutable selection is `/opt/grid-runs/flat-hour-20261122-0435/selection/`.
Earlier datasets, selections, builds and results remain preserved. The owner
waived repeated load tests and the weekly rehearsal; do not start them again.

The owner then approved the next matrix: all grid steps 1–10 by1, coin TP2–8
by1, exchange and global TP always on and each2–12 by1, and BUY depths
30/35/40/45/50/55/60 percent. Add the 11 agreed fractional probes at all seven
depths: total59,367. See the exact interview clarification and RESEARCH_SWEEP.md.
This depth is an order-price boundary, not a stop loss or incoming-price filter.
Preserve strict crossings, fees, current-price aggregate TP, exact arithmetic,
legacy depth60 IDs and all original results. New depth IDs use -d30…-d55.
Implement and validate this matrix on the same109 pairs; preparation alone does
not start its full history replay. Do not silently prune configurations by old
profit. Keep binary results, SSH loopback viewer/native table double-click and
the existing current publication until new results are independently verified.
