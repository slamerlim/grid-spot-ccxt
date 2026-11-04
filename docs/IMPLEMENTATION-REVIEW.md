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
| Q01 LIMIT-only | Domain commands, mock executor payload boundary, manual/scoped/global exits; `tests/execution/test_boundary.py`, `tests/operations/test_liquidation.py`, `test_allocation_exit.py` | Actual authenticated CCXT order serialization and venue acceptance untested; no real sender exists |
| Q02 minimum lot | Decimal quantizers, independent buy/sell minima and fee-aware eligibility; `tests/domain/test_market.py`, `test_boundaries.py` | Verified account-specific fees/rules at actual venue placement unavailable |
| Q03 finite funds | One ledger writer, atomic reservations and scope/pool ownership; `tests/persistence/test_ledger.py`, `tests/research/test_replay.py` | Dedicated real balances and external activity must be verified separately |
| Q04 seed | Actual weighted fills, partial residual handling and approved terminal-seed anchor rule; `tests/domain/test_reducer.py` | Actual fee/fill normalization and venue event evidence remain untested |
| Q05 one SELL | Possibly-active slot persists through cancellation/unknowns; domain, execution, scoped/global tests | Live cancel/fill races have synthetic evidence only |
| Q06 ladder | Minimum lots, quantized distinct prices, range/budget/order bounds; domain market/reducer tests | Account/market limits not independently certified |
| Q07 drain/late BUY | Drain, reconcile and retain late inventory without duplicate seed; domain/execution recovery tests | Exchange truth/restart behavior requires target-venue evidence |
| Q08 idempotency | Scoped identities, atomic fill posting, replay and revision history; persistence/execution identity and crash tests | Private feed and actual paginated trade semantics untested |
| Q09 unknown submit | Durable intents, unknown state, no blind resend; `tests/execution/test_crashes.py`, `test_recovery.py` | Real client-ID lookup, retention and ambiguous venue responses untested |
| Q10 fees/accounting | Base/quote/token fees, liabilities, rebates and conservation; ledger, portfolio and research tests | Actual account tiers, fee currencies and conversion evidence unavailable |
| Q11 hot changes | Revisioned universe control, explicit retirement, confirmed local commands; `tests/universe/`, `tests/operations/test_configuration*.py` | Mock owner only; no live configuration changes authorized |
| Q12 +10% portfolio exit | Net consolidated valuation, cash-flow handling, bounded LIMIT exits, conversion, residual review; `tests/portfolio/`, scoped dust and optional historical portfolio replay tests | Replay uses explicitly synthetic liquidity/FX/account facts; actual venue liquidity/account valuation remains unverified |
| Q13 fallback | Bounded synthetic private hints and authoritative fake REST recovery; execution recovery/capacity tests | No real private subscription, disconnect or REST recovery integration |
| Q14 corrupt history | Read-only 24-byte importer, manifest, quarantine counts and strict provenance/precision checks; `tests/research/test_history.py` | Original archive provenance and coverage are not certified by bounded historical samples |
| Q15 OHLC ambiguity | Three fixed conventions and a comparison command preserve one side per allocation/bar and delayed new orders; `tests/research/test_replay.py`, `test_comparison.py` | The reported range covers only these assumptions, not every feasible execution path; queue/volume evidence and broader studies remain absent |
| Q16 chronology/restart | UTC merge, canonical ties, final partial interval, deterministic restart, separately hashed source/runtime/Git report envelope; research replay/provenance/crash tests | No checkpoint resume or loaded-code/dependency attestation; broader multi-market study required |
| Q17 cache | Single-flight refresh, freshness/generation gates, bounded LRU and 100-caller mock test; `tests/universe/test_discovery.py` | Full real-venue discovery/admission integration untested |
| Q18 overload/resources | Bounded writer/intake/cache, halt on overload, synthetic 1/7-venue × 10/100/1,000-candidate matrix; execution/operations tests | Not 1,000 funded allocations; no constrained research/fill-storm contention or deployment sizing |
| Q19 controller/storage | Exclusive mock ownership, WAL/FULL ledger, crash, backup/restore and audited storage controls; `tests/persistence/`, `tests/operations/test_storage*.py` | Real venue reconciliation after restore, deployment filesystem/power faults and production runtime remain unverified |
| Q20 security/deployment | Live unavailable, sanitized CLI, same-UID IPC, audited confirmations, secret scan and inert service template; config/doctor/operations tests | No installed service, withdrawal-permission verification, real restart drill or external alert delivery |

Evidence details are in `I1-EVIDENCE.md` through `I8-EVIDENCE.md` and the later
operator/manual-exit/allocation-exit/hot-config/storage/scoped-dust documents.
The main CLI now has research aliases; financial commands intentionally remain
under `gridctl operator` and use its existing same-user mock owner.

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
   One-cycle synthetic integration does not establish execution quality or the
   full research comparison suite. Historical conversion/rule/fee changes and
   automatic multi-cycle studies remain undeveloped. Preserve ownership and
   reconciliation contracts when extending either mode.
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
   and manifests. It has no portfolio exit or automatic scenario cross-product.
   `--study` now evaluates declared chronological folds and spacing candidates
   under separate fee/model/latency scenarios using the base basket engine.
   Training total NAV chooses spacing before any test replay; all held-out
   outcomes are retained with reset funding. Regime labels and universe evidence
   are caller declarations. Point-in-time survivorship/delisting checks, verified
   historical fee tiers, continuous portfolio studies and independent-lot study
   integration remain missing. A plan hash is not proof of preregistration.
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
   remain distinct. Full portfolio per-allocation unrealized/exposure/drawdown reporting and
   the broader study suite remain incomplete. Extend metrics/comparisons while
   keeping machine observations outside deterministic accounting.
4. **Resource isolation and contention.** Input/queue bounds and sequential
   decoding are not CPU/RSS/IO quotas. `operations/experiment.py:benchmark_case`
   assesses up to 1,000 candidates, then supervises one allocation per logical
   venue. It does not run an offline replay worker against a concurrent fill
   storm. ADR benchmark 6 and Q18 require bounded competing research, measured
   trading responsiveness, preserved durable fills and research yielding under
   pressure. Develop the offline harness before claiming this acceptance.
5. **Full storage benchmark matrix.** The current benchmark measures bounded
   warm read/decode/repack/hash samples with byte-equivalent raw/Arrow/Parquet
   reconstruction. Cold-ish scans, UTC/random slicing, 100/1,000-symbol merges,
   repeated parameter replay reuse and deployment contention are not measured
   by it. Existing SQLite mock stress/crash/backup tests are useful evidence,
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
only the in-memory `SyntheticExchange`. Neither is an authenticated trading
gateway. `universe/discovery.py` provides bounded cache/client coordination but
does not turn probe evidence into certified account capabilities. The capability
registry remains NOT_READY for each intended venue. Private fees, balances,
permissions, client-ID behavior and order-event semantics require actual evidence.

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
