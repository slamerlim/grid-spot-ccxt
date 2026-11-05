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

## Unfinished bounded execution recovery

The mock executor still fetches complete lifetime order/trade history on every
reconciliation. Page/record bounds stop oversized runs, but cannot support
indefinite operation. Its stable snapshot revision and explicit trade sequence
are fake-source contracts, not guarantees from CCXT's unified dictionaries.
`I8-TERMINAL-RECOVERY.md` records the investigation and prerequisite correction.
Continue toward bounded outstanding-order/history queries while retaining durable
terminal facts, duplicate/conflict checks and detection of unexpected account
activity. Pagination/retention evidence and settlement ordering must be explicit;
neither deleting omission checks nor sorting CCXT timestamps proves completeness.
Private transport and actual account validation remain separately gated.
`I8-ORDER-HISTORY.md` now provides the first bounded read primitive: shared
unified parsers, two order observations around one limited order-trade query,
deduplication and exact cumulative matching. Pinned OKX offline SDK fixtures
verify the request/parse path. Its result is observational, with no ledger or
admission effect; the account-wide integration described above remains open.
`I8-RECOVERY-PLAN.md` now selects outstanding orders through indexed sole-writer
reads, preserves unbound attempted orders, checks owned terms against unified
observations and revalidates the plan after the bounded reads. It is read-only:
no fill posting, uncertainty resolution or readiness is derived from inspection.
Account-wide discovery, unknown lookup, committed-fill integration with posting
and settlement ordering remain required before replacing the executor's history path.
`I8-ACCOUNT-HISTORY.md` implements an explicit alternative through unified
`fetch_my_trades`, with guarded XT/BingX SDK fixtures. It queries one bounded
symbol page for the known orders, preserves unmatched/unattributed observations,
and shares sole-writer plan revalidation. Missing quantities and unknown orders
remain unresolved; bounded continuation and complete account recovery are still
required before financial integration.
`I8-COMMITTED-RECOVERY.md` now compares recent observations with a bounded baseline
of committed working-order fills. Indexed reads validate retained state, canonical
payloads, external identities, fee projections and cumulative quantity/gross cost;
the entire plan is checked again after observation. New identities must cover the
remaining reported quantity exactly. Returned terminal/foreign-allocation replays
are checked by scoped identity without being adopted or scanning lifetime history.
Standalone history helpers retain their full-quantity requirement. Inspection
remains read-only and does not establish settlement order, account completeness,
fee evidence for new fills or dispatch readiness. Missing new quantities, unknown
submissions, unexpected activity, bounded continuation and atomic integration with
the executor remain open; its original completeness checks are unchanged.

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
