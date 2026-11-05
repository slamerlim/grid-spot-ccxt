# Implementation review and remaining requirements

Reviewed 2026-11-05 against the four unchanged imported specifications and the
current branch after base `fee2cdc39067623cd1955e72e928386634866fab`. This is a
current implementation/evidence map, not an amendment to the PRD or acceptance
plan. `I0-REQUIREMENTS-TRACE.md` remains the historical I0 mapping. The new
research CLI increment and its final validation are in `I8-RESEARCH-CLI.md`.
The subsequent source/runtime reporting increment, based on `5637656`, is in
`I8-RESEARCH-PROVENANCE.md`; its current status is incorporated below.
The optional historical portfolio adapter, based on `84caf18`, is recorded in
`I8-PORTFOLIO-RESEARCH.md` and incorporated below.
The three-convention comparison based on `f9ffa8a` is recorded in
`I8-RESEARCH-SCENARIOS.md` and incorporated below.
Realized statistics and funded portfolio benchmarks based on `553216d` are
recorded in `I8-RESEARCH-PERFORMANCE.md` and incorporated below.
The independent-lot research reference based on `0c54773` is recorded in
`I8-INDEPENDENT-LOT-REFERENCE.md` and incorporated below.
The declared walk-forward/sensitivity runner based on `0fe53c2` is recorded in
`I8-RESEARCH-STUDIES.md` and incorporated below.
The resource-contention harness and measured schema-cache optimization based on
`4c44624` are recorded in `I8-RESOURCE-CONTENTION.md` and incorporated below.
Per-allocation portfolio risk based on `a14d940` is recorded in
`I8-ALLOCATION-RISK.md` and incorporated below.
Bounded continuous portfolio replay based on `41138d5` is recorded in
`I8-PORTFOLIO-SERIES.md` and incorporated below.
Portfolio walk-forward integration based on `9eecbca` is recorded in
`I8-PORTFOLIO-STUDIES.md` and incorporated below.
Bounded UTC storage queries based on `92cfdd4` are recorded in
`I8-STORAGE-QUERIES.md` and incorporated below.
Paired independent-lot walk-forward studies based on `1f3290e` are recorded in
`I8-LOT-STUDIES.md` and incorporated below.
Bounded chronological storage merges based on `ffd02ed` are recorded in
`I8-STORAGE-MERGES.md` and incorporated below.
Repeated mixed BUY/SELL contention and committed-history reconciliation reuse
based on `24bae1d` are recorded in `I8-MIXED-CONTENTION.md` and incorporated below.
The pinned asynchronous SDK request audit based on `1268e30` is recorded in
`I8-SDK-ORDER-AUDIT.md`; its numeric/account/ID findings are incorporated below.
The correction to documented CCXT string math, based on `97469fb`, is recorded
in `I8-CCXT-UNIFIED-NUMBERS.md`. Seven-venue public fixtures and pure OKX
order/trade parser fixtures cover numeric conversion; authenticated behavior
remains unverified.
The subsequent `I8-UNIFIED-RECONCILIATION.md` connects the shared order/trade
observations to the mapped mock's existing ledger/executor. Real account source,
pagination, trade ordering and fee provenance still require separate evidence.
`I8-TERMINAL-RECOVERY.md` subsequently fixes current snapshots that contradict
durable terminal orders, before any new financial posting or SELL replacement.

**The system has substantial offline implementation and mock evidence, but
full I8 acceptance and I9 are incomplete.** No venue is certified for live
execution. Passing a mock test establishes its tested behavior, not an exchange,
account, deployment or profitability guarantee. Historical iteration evidence
retains its original scope; later approval and changes are recorded in AGENTS.md.

## Acceptance map

The table identifies existing implementation and representative test files.
It deliberately does not give a blanket PASS to Q01–Q20. Runtime guards and
synthetic tests must be retained while the remaining integration is developed.

| Gate | Current local implementation/evidence | Remaining boundary |
|---|---|---|
| Q01 LIMIT-only | Domain commands, mock executor boundary, manual/scoped/global exits and intercepted pinned-SDK request fixtures; `tests/execution/test_boundary.py`, `tests/exchange/test_order_audit.py`, operations exit tests | Authenticated HTTP encoding and venue acceptance untested; BingX fractional conversion is a known blocker; no real sender exists |
| Q02 minimum lot | Decimal quantizers, independent buy/sell minima and fee-aware eligibility; `tests/domain/test_market.py`, `test_boundaries.py` | Verified account-specific fees/rules at actual venue placement unavailable |
| Q03 finite funds | One ledger writer, atomic reservations and scope/pool ownership; `tests/persistence/test_ledger.py`, `tests/research/test_replay.py` | Dedicated real balances and external activity must be verified separately |
| Q04 seed | Actual weighted fills, partial residual handling and approved terminal-seed anchor rule; `tests/domain/test_reducer.py`, unified parser-to-ledger fixtures | Actual account settlement and venue event evidence remain untested |
| Q05 one SELL | Possibly-active slot persists through cancellation/unknowns; current terminal contradictions block recovery before replacement; domain, execution history, scoped/global tests | Live cancel/fill races have synthetic evidence only |
| Q06 ladder | Minimum lots, quantized distinct prices, range/budget/order bounds; domain market/reducer tests | Account/market limits not independently certified |
| Q07 drain/late BUY | Drain, reconcile and retain late inventory without duplicate seed; domain/execution recovery tests | Exchange truth/restart behavior requires target-venue evidence |
| Q08 idempotency | Scoped identities, atomic fill posting, unified observations, replay and revision history; persistence/execution identity, unified and crash tests | Private feed and actual paginated trade semantics untested |
| Q09 unknown submit | Durable intents, no blind resend, schema-6 immutable external-ID mapping and returned-ID normalization through a mapped fake; SIGKILL/restore tests and SDK audit | No approved venue ID policy or real uniqueness/lookup/retention evidence; no authenticated sender |
| Q10 fees/accounting | Base/quote/token fees, liabilities, rebates and conservation; ledger, portfolio, research and pinned OKX parser-to-ledger tests | Actual account tiers, fee currencies and conversion evidence unavailable |
| Q11 hot changes | Revisioned universe control, explicit retirement, confirmed local commands; `tests/universe/`, `tests/operations/test_configuration*.py` | Mock owner only; no live configuration changes authorized |
| Q12 +10% portfolio exit | Net consolidated valuation, cash-flow handling, bounded LIMIT exits, conversion, residual review; `tests/portfolio/`, scoped dust and optional historical portfolio replay tests | Replay uses explicitly synthetic liquidity/FX/account facts; actual venue liquidity/account valuation remains unverified |
| Q13 fallback | Bounded synthetic private hints and authoritative fake REST recovery; execution recovery/capacity tests | No real private subscription, disconnect or REST recovery integration |
| Q14 corrupt history | Read-only 24-byte importer, manifest, quarantine counts and strict provenance/precision checks; `tests/research/test_history.py` | Original archive provenance and coverage are not certified by bounded historical samples |
| Q15 OHLC ambiguity | Three fixed conventions and a comparison command preserve one side per allocation/bar and delayed new orders; `tests/research/test_replay.py`, `test_comparison.py` | The reported range covers only these assumptions, not every feasible execution path; queue/volume evidence and broader studies remain absent |
| Q16 chronology/restart | UTC merge, canonical ties, final partial interval, deterministic restart, separately hashed source/runtime/Git report envelope; research replay/provenance/crash tests | No checkpoint resume or loaded-code/dependency attestation; broader multi-market study required |
| Q17 cache | Single-flight refresh, freshness/generation gates, bounded LRU and 100-caller mock test; `tests/universe/test_discovery.py` | Full real-venue discovery/admission integration untested |
| Q18 overload/resources | Bounded writer/intake/cache, overload halt, candidate matrix and 1/7-venue seed-partial and repeated mixed BUY/SELL contention with a bounded research child; execution/operations tests | Not 1,000 funded allocations, sustained fixed-rate traffic, kernel RSS/device quotas or deployment sizing |
| Q19 controller/storage | Exclusive mock ownership, WAL/FULL ledger, crash, backup/restore and audited storage controls; `tests/persistence/`, `tests/operations/test_storage*.py` | Real venue reconciliation after restore, deployment filesystem/power faults and production runtime remain unverified |
| Q20 security/deployment | Live unavailable, sanitized CLI, same-UID IPC, audited confirmations, secret scan and inert service template; config/doctor/operations tests | No installed service, withdrawal-permission verification, real restart drill or external alert delivery |

Evidence details are in `I1-EVIDENCE.md` through `I8-EVIDENCE.md` and the later
operator/manual-exit/allocation-exit/hot-config/storage/scoped-dust documents.
The main CLI now has research aliases; financial commands intentionally remain
under `gridctl operator` and use its existing same-user mock owner.

## Bounded execution recovery and remaining integration

The existing mock executor now uses the shared sole-writer recovery plan and
committed-fill baseline. It rechecks both after observation and before new
bindings or financial posting. Recovery requires every attempted working order,
but no longer requires terminal orders or posted trades to remain in every
exchange window. Committed quantity plus newly observed unique fills must equal
each current cumulative report. Returned old identities must still match their
immutable ledger facts; current terminal contradictions stop the whole pass.

Both synthetic gateways use the same execution path. Their finite source returns
all current open orders for the account/symbol, exact requested client identities,
and bounded recent orders/trades with matching order reports. Page, record,
timeout and rate limits still apply. The mapped gateway uses the shared CCXT
unified parsers; neither gateway accesses an exchange. Stable revision tokens,
exact client lookup and any supplied trade sequences remain explicit mock guarantees.

Working-order committed fills have an independent cap, using the executor's
record limit. Baseline reads and submission-attempt checks use indexed identities;
normal recovery no longer scans all durable bindings or trade keys. Startup
integrity verification still checks the durable history. Fee/settlement overruns
persist a reconciliation intent atomically with the fill, including when already
held; automatic recovery cannot clear it after window omission or compaction.
A concurrent allocation/control change invalidates the saved plan and requires
another bounded pass. Unknown submissions are never inferred rejected or retried.

`tests/execution/test_reconcile_history.py` compares ten complete BUY/SELL rounds
under a 12-record recovery cap with the same run using a wide window, with matching
final state and all 20 fills retained. It also covers omitted partial fills and
fees across writer restart, persistent fee stops, corrupted baselines and current
contradictions for compacted orders. The existing crash and cancellation suites
exercise this same executor. Local validation passed **1,488 tests on Python
3.12.16** (565.42 seconds) and **1,488 on Python 3.13.18** (581.38 seconds),
with no skips. Application/test/dependency bytes were unchanged across both runs.
Receipts and logs are in `/tmp/grid-i8-executor-window-checks/`. The focused
recovery-history suite passed 24 tests; lint, formatting, strict typing, Bandit,
lock verification, the locked dependency audit and secret scanning also passed.
Hosted CI remains pending under the unchanged account lock; publication uses
`[skip ci]` and GitHub Free only.

The standalone inspection helpers described in `I8-ORDER-HISTORY.md`,
`I8-RECOVERY-PLAN.md`, `I8-ACCOUNT-HISTORY.md` and `I8-COMMITTED-RECOVERY.md` remain
read-only. This integration does not make their observations dispatch authority.
Real account-wide discovery and send admission, bounded continuation after a
history gap, actual settlement ordering/FX evidence and authenticated transport
remain unfinished.
A missing new quantity stops recovery; no wider unbounded scan or blind resend
is attempted. CCXT dictionaries do not establish account completeness or an atomic
snapshot. Continue integrating the essential runtime; no new exchange access,
private account evidence, observed-soak acceptance or deployment is implied.

### Execution ordering through unified timestamps

The mapped mock now accepts CCXT's unified integer trade `timestamp` when it has
no proven source sequence. The executor validates all new positions before
binding/posting, requires distinct values in one ordering kind, sorts by those
values and requires advancement beyond the last committed position. Timestamp
ties, missing new ordering data, regressions and changed ordering kinds keep
dispatch blocked. Previously committed replays can omit optional ordering fields;
supplied contradictory metadata is rejected. Page order and trade-ID sorting
never become economic chronology. See the [official trade contract](https://github.com/ccxt/ccxt/wiki/Manual#personal-trades).

Schema 7 adds an indexed `execution_positions` table. Position, fill, external
identity, fees and financial state commit together. Each position is checked
against the original fill receipt; startup verifies posting order and coverage.
Old financial payloads and migration SQL/checksums 1–6 are unchanged. Migrated
legacy fills do not acquire invented timestamps: inspection remains possible,
but the executor requires an ordering baseline before further sends. Existing
backup/restore and process-kill tests now cover this transaction, including the
portfolio exit's actual executor path.

The focused ordering tests use guarded pinned OKX parsing, mixed BUY/SELL fills
whose order changes cost basis, reversed pages/IDs, tied and malformed times,
replays, writer reopen and missing metadata. Migration tests cover old receipts,
atomic failure and corrupted positions. This is offline implementation evidence;
actual venue timestamp semantics, source consistency and finer ordering for ties
remain unverified. Account-wide discovery, pagination and authenticated transport
remain unfinished. No financial formula or acceptance gate is changed.

Local validation passed **1,510 tests on Python 3.12.16** (589.14 seconds) and
**1,510 on Python 3.13.18** (611.71 seconds), with no skips. Application,
test and dependency bytes were unchanged across both complete runs. Receipts
are `3.12-1793864948837144663.json` and `3.13-1793864948836621048.json` under
`/tmp/grid-i8-trade-ordering-checks/`, with corresponding logs. Lint, formatting,
strict typing, Bandit, lock verification and the locked dependency audit passed.
Hosted CI remains pending under the unchanged account lock; publication uses
`[skip ci]` and GitHub Free only.

### Locating attempted orders without an exchange ID

`inspect_account_orders` now accepts explicit lookup through the same caller-owned
account client. When an attempted working order has a registered returned client
ID but no exchange ID, it queries bounded unified open and recent closed orders.
Only an exact returned client-ID match can locate that order. Known terms must
match the reserved intent; conflicting identities, multiple external IDs for
one client ID and external IDs already bound to another intent refuse the batch.
Unknown client IDs and missing client IDs remain unmatched observations.

Located IDs use the existing bracketed order/personal-trade reader and committed
fill comparison. The original sole-writer plan is rechecked after lookup and
again after history reads. `discovered_orders` preserves the located targets,
while `plan` and `unresolved` retain the original durable unknowns. Unmatched
orders/trades remain available to the caller. Inspection performs no binding,
financial posting, hold clearing or send admission; registration without an
attempt causes no lookup. No schema or executor dispatch behavior changes.

Lookup adds at most two unified calls and at most `2*max_orders` candidate rows.
The whole worst-case call budget is checked before any read, including history
for every potentially located target. One deadline covers both read phases;
failures are sanitized, cancellation propagates and there is no automatic retry.
Both methods must be supported by the selected account client. CCXT normalizes
the responses, but method availability and retention vary; see its
[order-query contract](https://github.com/ccxt/ccxt/wiki/Manual#querying-orders).
The guarded pinned OKX fixture confirms that its default closed-order request
selects `state=filled`, so absence cannot exclude canceled orders or establish
rejection. All missing matches remain unresolved, even in an empty or short page.

Tests cover lost acknowledgements for open, partial and filled orders, known and
unknown orders in one pass, unrelated orders, duplicate claims, binding conflicts,
plan changes, limits, cancellation and the actual pinned SDK read/parse methods
with signing and transport prohibited. This remains offline evidence. Account-wide
completeness, pagination and authenticated transport remain unfinished. Executor
posting integration is described below.

Local validation passed **1,534 tests on Python 3.12.16** (594.51 seconds) and
**1,534 on Python 3.13.18** (612.23 seconds), with no skips. Application,
test and dependency bytes were unchanged across both complete runs. Receipts
are `3.12-1793887008961338028.json` and `3.13-1793887008957278017.json` under
`/tmp/grid-i8-order-lookup-checks/`, with corresponding logs. The focused recovery
and boundary checks passed 150 tests. Lint, formatting, strict typing, Bandit,
lock verification and the locked dependency audit also passed. Hosted CI remains
pending; publication uses `[skip ci]` and GitHub Free only.

### Account observations through the existing executor

The mapped mock's existing `service` accepts explicit `account_reads` and optional
`lookup`. Under the same executor lock it holds the allocation, plans from the sole
writer, reserves the entire worst-case unified-call cost and invokes the existing
account reader. It rechecks the saved plan and stream epoch, converts the already
parsed observations through the shared normalizers, then validates the complete
batch with `_validate_snapshot`. The existing binding/fill/report loop performs
all posting. No second executor, database writer, venue parser or migration is added.

All attempted working orders must be covered. Unowned orders/trades, insufficient
quantities, missing actual fees, missing third-token settlement valuation, ambiguous
timestamps and changed identities prevent new binding/fill posting. The optional
lookup's exact client-ID proof can support an omitted client ID on subsequent
reads without changing the original observation. Previously committed trades are
compared with their ledger facts before being omitted from new posting; supplied
timestamps must match a saved timestamp position. Optional omissions cannot erase
known fees or ordering. Actual fee overruns still post once and retain their durable
stop. Fill, identity, position and accounting remain one existing transaction.

Individual order identities and reports can now reconcile through account reads,
but these bounded observations cannot establish account-wide readiness. Successful
intake leaves the allocation held and `service` returns `False` with an explicit
message; it never emits the final `Reconciled` event or populates send readiness.
The existing complete fake recovery remains available afterward. Real account
discovery, balances, history continuation, private transport and send admission
are still unfinished. The constructor continues to admit only the two synthetic
gateway types, and no credentials, exchange calls or live sender are enabled.

The reader uses at most 100 targets and 100 trade rows per query, capped further
by the executor's record limit; the combined normalized result is checked before
posting. Lookup retains its separate two-query bounds. Unused reserved call slots
expire with the rate window, without optimistic refunds. The reader's deadline
is capped at 30 seconds. Actual HTTP/rate/byte enforcement remains the transport's
responsibility. Cancellation propagates and revokes readiness; no transaction is
held across an await.

`tests/execution/test_account_posting.py` covers lost acknowledgements, partial
fills, replay, terminal completion, mixed BUY/SELL cost basis, missing optional
client IDs after lookup, changed plans/epochs, unowned observations, limits,
fee-overrun stops, writer restart and fill-during-cancel. Six real SIGKILL cases
exercise the same binding/fill transaction boundaries through this account path.
Timeout and caller-cancellation tests verify no partial batch posting and no
database transaction held across a stalled read.

Local validation passed **1,560 tests on Python 3.12.16** (594.77 seconds) and
**1,560 on Python 3.13.18** (614.52 seconds), with no skips. Application,
test and dependency bytes were unchanged across both complete runs. Receipts
are `3.12-1793889146104436847.json` and `3.13-1793889146107045426.json` under
`/tmp/grid-i8-account-posting-checks/`, with corresponding logs. The new focused
account-posting suite passed all 26 tests. Lint, formatting, strict typing,
Bandit, lock verification and the locked dependency audit also passed. Hosted
CI remains pending; publication uses `[skip ci]` and GitHub Free only.

## Unfinished offline research work

These are implementation gaps against PRD §9 and ADR-0001, not reasons to invent
missing venue evidence or to enable live access.

1. **Portfolio integration and study scope.** Optional `backtest --portfolio`
   now drives the actual mock ledger/executor/I7 controller with explicit synthetic
   depth, per-bar fill capacity and FX/rule/fee assumptions. It includes all owned
   capital, the consolidated +10% trigger, delayed cancellations, partial/late
   fills and bounded exits. It reports unfinished/blocked outcomes without forced
   completion. The default `research/replay.py:run` remains a per-allocation
   mark-to-close simulator; the modes have distinct schemas and assumptions.
   `--portfolio-cycles N` now continues through up to 32 cycles using the same
   ledger/funds, with fresh later-bar restart only after reconciled completion.
   Series reporting preserves original-funding metrics and every cycle outcome;
   the three OHLC scenarios can each run a continuous series. This synthetic
   integration does not establish execution quality or the full study suite.
   Portfolio walk-forward studies now share a fixed evaluation endpoint,
   including post-completion valuation of retained ownership. Historical
   conversion/rule/fee changes remain undeveloped. Preserve ownership and
   reconciliation contracts.
2. **Scenario and comparison suite.** `--compare-scenarios` now runs
   `strict_skip_ambiguous`, `touch_buy_priority` and `touch_sell_priority` in fixed
   order through either engine. It retains complete results, checks common input
   manifests and withholds a shared NAV range for unknown valuations or different
   endpoints. The conservative/baseline/optimistic labels specify fill conventions,
   not PnL ordering or bounds over all feasible executions. Both engines now have
   funded cash/buy-hold references using their respective valuation bases and
   endpoints. `--compare-lots` now adds one declared fixed-anchor independent-lot
   reference using shared accounting and original-allocation OHLC ambiguity.
   Its complete results are compared with the ordinary basket on common funds
   and manifests. It has no portfolio exit. `--study --compare-lots` now pairs
   full basket/reference studies across the plan's declared scenarios, with
   independent training-only spacing selection and complete retained outcomes.
   Combined work/output bounds apply; paired inputs and funding are checked.
   `--study` now evaluates declared chronological folds and spacing candidates
   under separate fee/model/latency scenarios using the basket or portfolio engine.
   Portfolio cycles carry within each window; all candidate endpoint marks must
   be known before selecting by training net NAV. Selection is frozen before test
   replay; all outcomes remain visible with fresh funding per window. Regime labels
   and universe evidence are caller declarations. Point-in-time survivorship/
   delisting checks and verified historical fee/rule/FX changes remain missing.
   A plan hash is not proof of preregistration.
   No suite covers all these yet. Never select fills using future bars or treat
   touches as observed queue fills. Independent-lot comparison stays research-only.
3. **Remaining metrics and provenance limits.** The CLI now wraps unchanged
   engine results in a separately hashed source/runtime/host envelope. It verifies
   the supplied full HEAD SHA against local application Python bytes/modes and
   dependency manifests, explicitly reporting mismatch/unavailability, and refuses
   publication on changes between snapshots. This is not loaded-code or installed
   dependency attestation; direct engine APIs do not add the envelope. Both
   replay engines now serialize gross realized wins/losses and explicitly
   defined profit factor. Portfolio replay adds committed-fill/fee statistics
   and funded cash/buy-hold references with the same ending timestamp and NAV
   constraints; unknown benchmark marks remain null. Trigger/final NAV, exit
   shortfall, observed drawdown/unknown bars, inventory time and committed capital
   remain distinct. Portfolio allocation risk now attributes the complete NAV
   proof to paired committed holdings, reports unrealized inventory PnL and
   exposure, and adjusts observed allocation drawdown for committed internal
   conversion transfers. Unknown values remain null, with sampled completeness
   and denominators explicit; no intrabar path or historical FX is invented.
   The broader study suite remains incomplete. Extend metrics/comparisons while
   keeping machine observations outside deterministic accounting.
4. **Resource isolation and contention.** Input/queue bounds and sequential
   decoding are not CPU/RSS/IO quotas. `operations/experiment.py:benchmark_case`
   assesses up to 1,000 candidates, then supervises one allocation per logical
   venue. The separate `operations contention` harness now reconciles 40/280
   distinct seed partials while a paced replay child runs, stops under pressure
   and resumes with matching replay hashes. It checks durable fill identities,
   duplicate recovery, conservation and writer reopen. A measured codec schema
   cache avoids repeated type reflection without caching financial state.
   Optional `--mixed-rounds N` adds finite repeated lower BUY fills, partial SELLs,
   cancellation races and late BUY inventory. The initial seven-venue three-round
   case failed the unchanged 10-second lag gate; both measurements are retained.
   Full-snapshot validation now avoids re-posting exactly verified immutable
   bindings/fills and already terminal reports, preserving financial records.
   Open/partial/uncertain facts and all new fills still require normal posting.
   CPU-time/address-space ceilings and logical read pacing are explicitly
   distinguished from kernel CPU bandwidth, RSS and device-I/O quotas. Sustained
   fixed-rate mixed traffic, 1,000 funded allocations, deployment limits and actual
   NVMe contention remain outstanding; this is partial Q18/ADR evidence.
5. **Full storage benchmark matrix.** The current benchmark measures bounded
   warm read/decode/repack/hash samples with byte-equivalent raw/Arrow/Parquet
   reconstruction. Optional single-source UTC and seeded minute-window queries
   now use one manifest-bound timestamp index, read only selected blocks and
   retain byte equality, index preparation cost and cache limitations. A 131,072-
   record synthetic sample measured 17 windows with positive NVMe backing evidence
   but zero observed physical input blocks; raw mmap was faster on that warmed
   sample. `--merge-plan` now measures up to 1,000 distinct source files with
   bounded packed blocks and no open reader per symbol. Synthetic 100/1,000-source
   runs matched raw per-source and chronological hashes; raw mmap was fastest,
   with zero observed physical input blocks. These are explicit small-block warm
   merge measurements, not end-to-end replay or deployment certification.
   Cold-ish scans, representative archive/month sampling, repeated parameter
   replay reuse and deployment contention remain incomplete. A bounded 2026-11-05
   prototype compared four spacing values on 1,280 synthetic bars, with three
   rotating warm repeats and exact complete-result equality. Including cache
   preparation and final raw checksum, median raw/reuse wall times were
   2.9957/2.9339 seconds on active-grid data and 0.7807/0.7190 on quiet data.
   This modest local result did not justify adding a shared decoded-history cache.
   No production cache or derived archive was adopted. Prototype/receipts remain
   in `/tmp/grid-reuse-prototype.py` and `/tmp/grid-i8-reuse-prototype/`; profiling
   is in `/tmp/grid-i8-reuse-profile/`. They do not establish representative
   archive/deployment benefit. Existing SQLite mock
   stress/crash/backup tests are useful evidence,
   but are not the complete on-deployment-hardware performance report. Keep raw
   archives immutable and defer any full derived copies until justified.

Continue with historical scenarios, comparisons and remaining metrics,
alongside a bounded mock contention harness. Use separate
reviewable increments; preserve existing canonical financial records and report
compatibility deliberately. Hardware measurements must describe the tested host
and cannot certify an untested deployment. Tier B/C, compiled kernels, dashboards
and distributed deployment remain deferred as specified; increasing lot sizing
is not authorized by this review.

## External acceptance and authorization

`exchange/ccxt_transport.py` is a fixed public GET probe profile, scoped to
BTC/USDT metadata/book endpoints on seven venues. `execution/executor.py` admits
only the in-memory `SyntheticExchange` and `MappedSyntheticExchange`. Neither is an authenticated trading
gateway. `universe/discovery.py` provides bounded cache/client coordination but
does not turn probe evidence into certified account capabilities. The capability
registry remains NOT_READY for each intended venue. Private fees, balances,
permissions, client-ID behavior and order-event semantics require actual evidence.

`exchange/order_audit.py` adds fixed offline SDK fixtures, with the first request
intercepted before signing/transport and lower paths prohibited. It observes
Gate/KuCoin account-discovery branches, Gate's long-ID refusal/prefix, BingX's
fractional float conversion and Bitfinex rounding of deliberately nonconforming
prices. It neither sends an order nor removes any venue blocker. Full financial
IDs remain unchanged; any future external encoding needs durable collision and
recovery semantics, not a silent truncation or an SDK pass-through assumption.

The fixed `--bingx-text` follow-up in `I8-BINGX-TEXT-PREPARATION.md` tests an
offline decimal-string candidate against retained unmodified controls. Exact
payload validation precedes independent unsigned-query comparison with pinned
SDK helpers. It adds no sender, signing, durable ID translation or readiness
override; the BingX production conversion blocker remains open.

`I8-DURABLE-CLIENT-IDS.md` adds separate durable external IDs without changing
internal financial identities. A mapped fake resolves returned wire IDs before
normal reconciliation; registration precedes claims and survives crash/restore.
It does not certify venue policies or activate the guarded BingX trial as a sender.

The completed one-hour OKX observation collected 460 validated books. Required
public rules remained unknown, so it admitted no paper allocations/orders/fills.
The combined retained attempts used 629/750 GETs and all 3/3 instrument requests.
`I8-PAPER-EVIDENCE.md` records the exact run and limitations. Its authorization
is spent; the unused GET allowance does not authorize another run. It does not
replace the minimum observed 48-hour gate or intended-first-venue evidence.

`deploy/grid-mock.service` remains an uninstalled, finite offline template with
review placeholders and network denial. It is not a deployed trading service or
evidence that quotas/alerts/restarts work in production. I9, private account
access, sandbox/real orders, further public collection and deployment remain
separately gated. Do not seek credentials or issue exchange requests to fill a
documentation gap under the current offline authorization.

Hosted CI remains pending because of the unchanged known account lock. Local
validation is authorized, and GitHub Free-only preferences prohibit paid
workarounds. Do not retry the lock without evidence it changed. Publish ordinary
scoped commits with `[skip ci]`; verify the remote SHA rather than calling a
skipped workflow a pass.
