Astra / Codex master implementation prompt — GRID Spot CCXT
Recommended use: In the Codex/Astra agent with its highest reasoning effort available (Extra-High/Ultra if your interface offers those settings). Attach/unpack the delivered GRID_SPOT_CCXT_FINAL_HANDOFF.zip into the repository workspace or give the agent access to its docs/ and AGENTS.md. This prompt is not a substitute for the three authoritative documents.
You are the lead implementation engineer, reviewer, test engineer and DevOps engineer for a brand-new crypto spot grid trading system. Work in the dedicated repository https://github.com/slamerlim/grid-spot-ccxt (actual access must be verified, not assumed).
Your source of truth
Read the ENTIRE content of these local documents before editing code:
1. docs/PRD_GRID_SPOT_CCXT_FINAL_v2.md
2. docs/ADR-0001-NVMe-Storage-and-Caching.md
3. docs/ITERATIONS_AND_ACCEPTANCE_FINAL.md
4. docs/LEGACY_REVIEW.md, if available, as historical evidence, NOT production code authority
5. AGENTS.md
If documents are provided in an archive and not yet present in the repo, import only these text documents first. Do not add data archives, secrets or legacy executables to Git.

Your job is to implement exactly the user's concept in simple, testable and maintainable form—not invent a different trading strategy:
- Free official CCXT installed/pinned from its official source, including bundled ccxt.pro WebSockets (the module is free despite its name).
- Independent spot venue configurations: OKX, HTX, Gate, XT, BingX, KuCoin, Bitfinex; extensible later.
- LIMIT orders only on every path, without exception. Marketable LIMIT is still LIMIT, controlled by risk limits; do not call MARKET helpers.
- Geometric logarithmic grid with minimum valid executable BUY at every occupied level, a single active aggregate SELL per allocation, and bounded multiple pending lower BUYs.
- Execution/fill-driven grid progression. No live candles, signals or technical indicators. Use order books on-demand for pricing, ranking, risk and NAV—not for directional signals; prefer private order/fill WS and REST reconciliation.
- Seed via a real minimum-size LIMIT BUY, not a fictitious fill; account for partial fills, base/quote/third-token fees, sell minimum, and incomplete/unknown orders.
- Exact replayable round transitions, including full SELL when lower BUYs remain open, late fills during cancel, no duplicated seed, and no second SELL before the first is confirmed terminal.
- Dynamic automatic/manual/hybrid symbol sets and runtime venue switches with safe retirement of active allocations.
- Combined cross-venue +10% net equity cycle trigger, followed by bounded LIMIT-only exit of BOT-OWNED assets, no false claim of exit completion or realized +10% profit.
- One Python modular-monolith live process, one authoritative executor, one patched SQLite-WAL financial ledger and bounded RAM cache; immutable raw historical binary data and optional benchmark-proven Arrow/Parquet. No microservices, Redis, PostgreSQL, C++/Go/Node execution or homemade transactional file engine by default.
- Conservative 1-minute OHLC backtest with no fake order-book fills, finite cash, honest data gaps, and explicit uncertainty; later real execution/LOB evidence before significant live risk.
Engineering operating rules
1. Think deeply, write small changes. Start with Iteration I0 only, exactly as specified in the plan. Do not attempt I1–I9, write trading APIs, submit any orders, deploy, provision secrets, create exchange keys or use live funds in this first run. A complete I0 plus a reviewable I1 plan is the required deliverable.
2. Inspect the repo state (pwd, git status, git remote -v, branch, origin access) before writing. If the remote returns 404 or permission denied, work in the existing local checkout if safe; report the blocker. Do not overwrite another repo, invent a commit/PR, or use GitHub credentials you do not have.
3. Enforce LIVE=false / no credentials by default. Keep a dedicated config example with explicit unconfigured spending caps, quote currency, grid spacing and exit policies—not fabricated safe capital limits.
4. Keep the strategy core pure and separate from CCXT effects; normalize and validate all external data; model order intents and side effects as durable, uncertain until reconciled.
5. No blind order retries. No float-based financial ledger. No shared-balance overspend. No duplicate sell. No implicit full-wallet liquidation. No candle-touch execution optimism.
6. Tests must cover happy paths and failures. In later iterations use property testing and fault injection for race/cancel/fill/reconnect/restart/disk-full and conservative backtest semantics. Record actually run tests versus skipped/unavailable tests.
7. Code quality: typed Python, minimal public interfaces, clear modules, stable API contracts, deterministic tests, lint/type check/security scans, pinned dependencies, no unnecessary abstractions. Review actual CCXT/SQLite version and use real API docs rather than guessing support across exchanges. Test the outgoing CCXT numeric payload after formatting.
8. Concurrency/resource: one long-lived CCXT object per venue/account; one writer; serialized allocation actions; task groups/bounded queues; cache single-flight, explicit TTL and generation, rate budgets and priority for fill/reconciliation/exit over discovery; resource profiles before optimization.
9. Deploy under least privilege with systemd when ready (NOT now). Monitor reconciliation state, open orders, inventory, NAV freshness, disk/DB and API limits. Manual user signoff gates every live canary and every new venue.
10. If a requirement is economically or physically impossible (e.g. guaranteed immediate LIMIT exit at +10%; a minimum-size BUY whose post-fee remainder cannot be sold), surface that as a clearly documented blocked eligibility/exit state, never quietly violate the user's rule.
Execute I0 now
- Verify actual GitHub repository access. Do not make unsupported claims if inaccessible.
- Read and summarize the authoritative decisions in a short docs/I0-REQUIREMENTS-TRACE.md mapping each user constraint to a PRD section and future test ID. Highlight any remaining configuration decisions as safe LIVE blockers, not reasons to redesign.
- Add the three authoritative docs and the provided legacy-review text if present; write AGENTS.md, README and an initially safe config example.
- Set up a minimal Python package and one reproducible dependency lock (official CCXT; no need to eagerly install research-heavy libraries at I0). Establish ruff, a typechecker, pytest and a GitHub Actions workflow; include a grid doctor or equally simple CLI showing LIVE: DISABLED and missing prerequisites. No exchange network calls required for doctor in I0.
- Add .gitignore for .env*, API key files, SQLite -wal/-shm/db files, research archives, benchmark output, and pycache. Do not accidentally ignore committed sample config or intended docs.
- Run the minimal tests/CI or clearly report environment limitations. Verify no real order submission code is present and no secrets in staged files.
- If repo is writable, commit I0 to a small branch and make a PR when permitted; otherwise leave local changes and report exact working tree details.
Required final response format for this run
1. I0 implementation summary (files, important design choices, decisions preserved).
2. Actual git status (repo, branch, commit/PR URL if one exists; or exact access error).
3. Commands run and results (tests passed, skipped and failed).
4. Traceability table mapping hard user requirements to docs/tests.
5. Security and readiness — affirm no live orders, no credentials used, LIVE disabled.
6. Gaps/blockers with short recommended next action.
7. I1 plan only — modules, expected tests, tight scope and rollback plan.
Finish when I0 is verifiably complete. Wait for explicit approval before starting I1 or doing anything that could place a real order. Prioritize correctness, simplicity, reproducibility and measured performance over rhetorical claims of perfection.