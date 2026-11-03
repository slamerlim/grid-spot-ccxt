GRID Spot CCXT — Codex Development Iterations, QA Matrix, and Release Gates
Companion to PRD v2.0 · 2026-11-03
Operating principle: Implement one iteration per reviewable PR (or small, self-contained commits when PRs are unavailable). No autonomous, all-at-once live deployment. Preserve narrow scope; no feature may bypass financial/recovery invariants.
1. Definition of Done for every iteration
Every delivered increment contains: (1) production code only within that increment's scope, (2) deterministic tests with commands and outputs, (3) a short CHANGELOG/PR summary, (4) any needed config schema or migration and rollback notes, (5) updated relevant documentation, (6) evidence of lint, typecheck, unit and integration runs, and (7) clearly listed gaps and unverified exchange behavior. Do not claim a test passed if it was not run. On failures, fix or clearly block the phase. No live keys in CI, fixtures, trace files or source control. Use one reproducible package lock, commit hash, and Python interpreter version.

Recommended tooling (keep minimal): Python 3.12/3.13 as verified, asyncio, official ccxt, sqlite3 linked to patched SQLite, pytest, hypothesis for invariants, ruff, mypy or pyright, uv lock or equivalent; add numpy for existing binary file access, and add Parquet/DuckDB only when the relevant iteration needs them. Use a single packaging tool, not three conflicting lockfiles. For live money use Decimal/integer quantized values and make the CCXT request boundary explicitly tested for float/string conversion and precision.
2. Work plan with gates
Iteration	Scope and outputs	Mandatory exit gate
I0 — Repository & specification lock	Verify slamerlim/grid-spot-ccxt access, inspect actual working tree; archive legacy review; commit PRD/ADR/this plan; create pyproject.toml, lock, src/grid/, tests/, .gitignore, README, sample safe config and GitHub CI; grid doctor starts with LIVE=false. Produce exact exchange probe plan.	Reproducible install, lint/types/pytest pass in CI; no credentials or datasets in Git; explicit repo-access status. Do not connect trading credentials.
I1 — Pure deterministic strategy + math	Pure functions for Decimal/log grid, minimum executable lot, fee/sellability validation, one aggregate TP calculation, deterministic round reducer returning intents. Fake exchange metadata and fill events only.	Property tests for quantity rounding, budget, min notional, non-overlapping sell plan, duplicate events, last BUY fill after sell drain; no network code in strategy.
I2 — Single-owner ledger	SQLite WAL patched runtime checks, migrations, fill/fee/position/reservation/order intent schema, transactional posting, process singleton guard, backup/restore. No real network orders.	Crash, idempotency, double reservation, partial fill and database restore tests pass. DB writer always serial; an unresolved intent remains unresolved after crash.
I3 — CCXT venue discovery and read-only adapters	One long-lived client per venue/account, load_markets, market/fee/precision mapper, rate budgets, fetch_order_book and authenticated read-only order/trade/balance probes where credentials are safely supplied. Seven-row capability matrix with CCXT commit/version and observed result. WS private subscriptions where supported.	Document per-venue yes/no/unknown, supported spot account, minima, fee source, stream behavior. XT unsupported/unknown path degrades via REST. No live order submissions; unavailable credentials stay NOT_TESTED.
I4 — Order executor, event intake, recovery	Intent-before-send, limit-only gateway, cancel/replace slot, private WS/REST reconciliation, timeout unknown state, duplicate fill prevention, bounded queue and supervision. Fake exchange first; optional venue sandbox calls only with approval.	All fault scripts in §§3–4 pass: no second sell, no overspend, no resubmission on unknown; restart reconciles before new BUY; no API method emits a market order.
I5 — Legacy-data validation and Tier-A backtest	Read-only raw 24-byte importer, data provenance manifest, bad candle quarantine, deterministic UTC multi-market replay, conservative ambiguous fill engine, finite-wallet accounting, net fees, metrics and baselines. Benchmark raw mmap vs derived formats on a sample, not full forced conversion.	Same sample/config -> identical result and hashes; zero/gap/minute/crossing fixtures handled; end-of-file reports flush; honest OHLC execution limitations published.
I6 — Dynamic universe & hot reconfiguration	Auto/manual/hybrid rules, on-demand order-book eligibility, allow/deny/pin, hysteresis, config generations and safe symbol/venue retirement with existing orders. Reuse caches and single-flight refresh.	Concurrent exclusion/partial fill/exit and cache invalidation tests pass. No orphan assets; no duplicate CCXT clients; REST limits and memory queues stay bounded.
I7 — Portfolio accounting and global cycle exit	Fresh conservative portfolio NAV, third-token fees and quote-conversion policy, external cashflow rule, >=10% detection, all-venue freeze/cancel/reconcile and LIMIT-only liquidation, blocked/dust states.	Test cross-exchange winners+losers, stale FX, late fills, partial exit, venue outage, unreachable limit price. Distinguish trigger NAV from realized outcome; never restart unresolved cycle.
I8 — Paper/sandbox soak, operational readiness	Forward simulator with actual order-event feed where available, sandbox tests, failure/429/disconnect/disk-fault drills; systemd unit, alerting, CLI runbooks, resource benchmarks, security review and target-venue candidate dataset capture.	Minimum 48-hour observed paper/sandbox soak with injected disconnects and restart; zero unexplained balance drift or invariant breaches; restore drill; venue matrix completed for intended first venue. If evidence lacking, remain paper-only.
I9 — Explicitly approved production canary	Deploy to one approved exchange / small approved allocation, trading disabled until operator arms live config. Observe actual fills, fee currency, maker/taker execution, reconciliation, drift, slippage. Then review each additional venue independently.	Human signoff per venue and allocation; runbooks, monitoring, limits, backup/restore and rollback demonstrated. No autonomous 'all seven at once' switch.


Deferred research only (not a blocking I0–I9 dependency): Tier B trade/volume replay, Tier C L2/queue modeling, validated bounded averaging, a C++/Rust replay kernel if profiling justifies it, dashboard, analytics server, distributed deployment and extra venues. If Tier C target-venue evidence is unavailable, do not claim an execution edge or autonomously size up; keep production canary gated by explicit acceptance of that uncertainty.
3. Deterministic functional test matrix
Q01 Limit-only: Wrap/spying on every create_order path, including seed, buy ladder, cancel-replace, global exit, manual exit and shutdown. Assert type='limit' in normalized call and outgoing payload. Reject market supplied via params or unexpected helper. A marketable limit remains type limit.

Q02 Minimum lot: minAmount, minCost, significant digits, price tick, low-cost coin, different buy/sell minima. At buy=1 base @ 10 quote, base fee .001 base, sell min 1 base, the smallest buy leaves .999 base, so that market/lot is ineligible; do not round up to hide invalidity in baseline mode.

Q03 Finite accounts: Two symbols race for the last quote balance; exactly one can reserve it. Partial fill consumes only executed portion and releases/remains exact outstanding reservation. No negative available cash.

Q04 Seed correctness: Seed is accepted but not filled -> no sell and no ladder. Seed partial below sell minimum -> residual tracked, no invalid sell. On complete seed, anchor is actual weighted fill price, not order request price.

Q05 One SELL: First SELL active, subsequent BUY fill initiates replacement. Cancel ack delayed -> new sell not submitted. If old SELL fills during cancel, ledger exact old fills first; only remaining owned base can be sold afterward. A duplicate cancel ack does not open another SELL.

Q06 Ladder: N=3 distinct lower buys; multiple fills replenish only to configured N, max_lots and quote budget; no infinite averaging. Venue quantization collapsing levels rejects duplicate prices. Out-of-range leaves inventory managed without new exposure.

Q07 Sell full + leftover buys: Aggregate SELL fully fills while buys are still open; enter DRAINING. A buy fills during cancel -> it becomes next-round seed inventory; no duplicate seed buy. Without late fills -> a fresh-book-priced next seed is allowed only after all cancellations reconcile.

Q08 Event idempotency: Same trade ID twice, out-of-order cum-filled order messages, stream replay after reconnect and REST trade replay -> identical final financial state. Never create PnL from status-only order events.

Q09 Submission unknown: Network timeout after venue accepts LIMIT BUY, no acknowledgment. Durable state = UNKNOWN. Restart/retry loops do not submit it twice. If the exchange cannot confirm status, block new related orders and alert operator.

Q10 Reservation, fee and accounting: Fee paid in base/quote/third token, fee rebate, different tier, no fee data. Do not silently ignore third-token liability. Total cash + inventory cost + fees match conservation equations after every commit.

Q11 Runtime symbol hot changes: Manual deny while BUY open and partially filled -> no new buys, existing SELL and inventory supervised. Venue disable while unknown order exists -> remain visible and reconcile. Pinning never overrides spot/minimum/risk eligibility.

Q12 Portfolio +10%: Exchange A has a winning asset, exchange B losing; aggregate NAV, not sum of winning closes, determines trigger. Add stale conversion quote -> no trigger. On threshold: cancel buys everywhere, reconcile, place only bounded LIMIT sells. A blocked fill prevents EXIT_COMPLETE; realized return may be <10%.

Q13 Exchange fallback: watch_orders unsupported or disconnects; mark stream degraded, reconcile REST with limits, never infer that no update means no fills. Reconnect requires epoch/generation update and read-after-reconcile before resuming.

Q14 Data corruption: Non-24-byte file, wrong endian, NaN/Inf/zero, incorrect OHLC range, out-of-order/duplicate minute, 468-day gap, missing provenance, float32 precision mismatch or suspicious leveraged-token symbol -> reject/quarantine/flag with counts; do not silently manufacture prices or fills.

Q15 OHLC ambiguity: A one-minute bar touches both pending buy and sell levels but contains no sequencing or volume. Conservative replay cannot claim a profitable within-bar two-sided round-trip. The run records its execution model and uncertainty.

Q16 Multi-market chronological replay: UTC start/end exact, deterministic cross-symbol merge and tie ordering, reproducible fees/NAV reports, correct final unfinished report interval even when crash/restart occurs.

Q17 Dynamic cache: 100 simultaneous load_markets misses for same venue share one refresh; old generation cannot authorize an order after a fee/minimum change. A stale book prevents new order placement even if a close happened.

Q18 Limits and overload: 1/7 venues and synthetic 10/100/1,000 candidate symbols remain within configured memory, task and API subscription bounds. Under overload, new exposure pauses and historical research yields; no lost durable fills or uncontrolled restart loop.

Q19 Single controller and storage: Start second live process -> refuses order authority. Kill at every DB commit/network-side-effect boundary; restoring backup preserves truth and reconciliation resumes. Full NVMe, corrupted DB, unpatched SQLite and failed restore -> fail live startup.

Q20 Security & deployment: No secrets in logs, git, trace data or CI artifacts; LIVE false by default. systemctl restart does not submit unapproved orders or market orders. Withdrawal permissions denied, emergency halt stops new exposure but retains visible open orders.
4. Test pyramid and practical testing restrictions
- Pure unit and property tests: Decimal/precision, grid reducer, event state, risk, symbol policies. Use generated event interleavings and frozen time/fixtures; save failing seeds.
- SQLite integration: migration fixtures, uniqueness, fill/fee transactions, backup/restore and crashed writer. Use isolated temporary DBs; never test against a live state file.
- Fake venue integration: programmable exchange that accepts/loses/duplicates/reorders messages and replies, requires price precision, simulates partial fills and 429s. This is the main failure-testing instrument; do not need paid sandboxes to reach high coverage.
- Real CCXT read-only smoke: version-logged has properties, load_markets, precision/fees, read-only orders/balances and streams only with user-provided credentials; enumerate which calls are possible without authentication. Do not claim seven-venue approval from has alone.
- Venue sandbox: optional supported markets, never assume sandbox mirrors production lot rules or fee settlement. Trading in sandbox requires a distinct deliberate enable/credential set.
- On-NVMe research benchmarks: exact source hashes and machine specs; report worst, median and CPU/RSS/IO of end-to-end tasks. Separate live and research memory and IO quotas.
- Paper/live canary: match actual exchange execution events against the durable ledger. Exit only with explicit human signoff. No guarantee that paper fills simulate queue priority.
5. Evidence pack / Codex output per iteration
At each iteration return a concise report including:
Iteration: I#
Git branch, base/head SHA, PR URL or 'not created' and why:
Files added/changed:
Architecture decisions changed (if any):
Commands actually run:
Tests passed / failed / skipped with reason:
Exchange/network methods actually probed:
Financial invariants checked:
Open security, precision, latency and data limitations:
How to reproduce and how to rollback:
Recommendation: GO to next phase / HOLD, with evidence:
No fake status: Missing repo permissions, datasets, credentials, API access, sandbox capability or disk benchmarks must be NOT TESTED or BLOCKED, never PASS.
6. Minimal target repository structure
grid-spot-ccxt/
  README.md
  AGENTS.md                         # Astra/Codex implementation rules, pointers to docs
  pyproject.toml
  uv.lock                           # or one equivalent lock
  .gitignore                        # data/, *.db*, .env*, secrets, local reports
  .github/workflows/ci.yml
  config.example.yaml
  docs/
    PRD_GRID_SPOT_CCXT_FINAL_v2.md
    ADR-0001-NVMe-Storage-and-Caching.md
    ITERATIONS_AND_ACCEPTANCE_FINAL.md
    LEGACY_REVIEW.md
    EXCHANGE_CAPABILITIES.md
    RUNBOOK.md
  src/grid/
    app.py
    domain/                         # pure strategy + accounting math
    execution/                      # order intents, only command authority
    exchange/                       # CCXT gateway, seven validated configurations
    persistence/                    # SQLite + migrations + backups
    universe/                       # dynamic symbol admission
    research/                       # importer/backtest/reports
    cli.py
  tests/
    unit/ integration/ fault/ replay/
  scripts/
    doctor.py bench_storage.py
Create these packages as needed by iterations, not dozens of empty directories on I0.
7. Repository and deployment preflight
At draft time, https://github.com/slamerlim/grid-spot-ccxt was not accessible via the connected GitHub API (404) and not listed among accessible repositories for slamerlim. This can mean nonexistent, private with unavailable token rights, or a temporary access limitation. Astra must inspect its actual local clone/remote and permissions, not assume it was created. If missing, perform local I0 only and report the exact blocker; do not initialize or overwrite another repository without user authorization. No PR or live trade is claimed as created by this handoff.

Live deployment signoff requires: exact accounts and funding caps; trading-only credentials; IP restrictions as available; dedicated funds with approved ownership; one venue with actually tested order endpoints/private events; software and DB version checks; backup restore; risk and exit config; successful fake and paper fault tests; resource soak; operator approves LIVE=true and a specific one-venue canary. Add more exchanges only after separate capability and risk review.