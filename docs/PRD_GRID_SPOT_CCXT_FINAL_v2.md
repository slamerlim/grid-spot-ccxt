GRID Spot CCXT — Final Implementation PRD
Version: 2.0 / implementation specification
Issued: 2026-11-03
Target: https://github.com/slamerlim/grid-spot-ccxt
Status: Approved as an engineering specification, not as evidence that trading is safe, profitable, tested against live accounts, or deployable today.
Authority: This PRD, ADR-0001, the acceptance plan, and explicit user constraints take precedence over legacy code. A later ADR may change a decision only with documented evidence and authorization.
0. Executive decision and guardrails
Create a single-host, asynchronous Python modular monolith using the official free/open-source CCXT distribution. The WebSocket interface is exposed under ccxt.pro even though it is bundled with CCXT for free. The project does not purchase a CCXT Pro subscription, depend on a separate proprietary library, or write native exchange WebSocket integrations by default. CCXT's REST methods are used for submissions/cancels, authoritative recovery and rate-aware reconciliation; private watch_orders / watch_my_trades are preferred when each pinned-version, account-type, venue capability test proves support.

Seven target venues: OKX, HTX, Gate, XT, BingX, KuCoin, Bitfinex. None is assumed production-ready until validated; each can be enabled/disabled independently at runtime. Exact class IDs and spot account options must be tested against the installed CCXT release rather than guessed. A venue with missing safe private order-event support may use tested REST polling, with a visible degradation state and bounded quotas.

Non-negotiable rules:
1. SPOT ONLY; no margin, borrowing, derivatives, leverage or leveraged tokens. Use dedicated trade-only accounts/subaccounts where available; withdrawals disabled.
2. LIMIT ONLY. No code path may issue an exchange market order, including cycle exits, emergency exits, rebalances, or initial buys. Marketable limit orders are legal only within configured price guards; postOnly is an optional maker preference, never an excuse to change the order type.
3. Geometric / logarithmic price grid: P(k) = A × exp(k × d), where d = ln(1 + g), g > 0. For lower buy levels, k < 0. Price formatting obeys each venue's actual precision mode and increments; unique levels must remain distinct after formatting.
4. Every individual buy entry is exactly one smallest valid executable lot at that limit price, subject to sufficient available quote balance, both buy- and sell-side constraints, and fee reserves. Do not increase lot sizes to conceal an invalid minimum-lot market. Total inventory can exceed one lot as multiple entries fill.
5. At most ONE active aggregate take-profit SELL per allocation (venue/account/symbol/strategy/cycle); many pending lower-priced BUY orders are permitted within budget. This is specifically an aggregate basket take-profit grid, not an orthodox independent-lot grid. Never allow overlapping old/new sells across cancel/replace races.
6. Confirmed order fills—not candles, indicators, or price-crossing assumptions—cause normal live-grid progression. Order books and fresh quotes are allowed strictly for initial/order pricing, market selection, valuation and risk. No continuous public price WebSocket is mandatory; start with private order/fill WebSockets plus rate-limited on-demand order-book REST snapshots.
7. No increasing averaging/martingale sizing in v1. Individual buys remain minimum lots. A bounded stack of equal-sized buys naturally changes weighted average cost; this is not an authorization to increase quantity after losses.
8. Consolidated +10% portfolio cycle: trigger when cash-flow-adjusted, marked-to-market, net-of-estimated-exit-costs equity is at least 110% of the cycle equity baseline. Freeze new entries, cancel buys, and attempt sale of all bot-owned non-reporting assets across enabled and draining exchanges using limit sells only. This is an exit attempt, not a profit guarantee. It is complete only after actual fills, reconciliation, and explicit residual/dust treatment.
9. Auto/manual/hybrid dynamic symbol universes with online allow/deny/pin and venue enable/disable. Removing a symbol must not orphan an order or inventory, or silently force liquidation.
10. One financial authority for commitments, reservations, orders, fills, and allocation states. No cache-derived balance permits trading; no unbounded tasks, queues, polling or history scans; exchange API limits take priority over speed.
Scope boundary: The bot manages only inventory and cash explicitly allocated to its strategies. The phrase “get rid of all other assets” refers to those bot-owned assets, not unrelated manual holdings. An operator wanting liquidation of an entire wallet must explicitly dedicate that wallet to the bot or issue a separately authorized import/allocation decision. Never sell unknown assets.
1. Expected outcomes and explicit non-goals
Success measures
- Trades never exceed allocated quote funds or sell more base than reconciled, reserved inventory.
- Every fill is recorded once, including after reconnect, cancel races and restart; uncertain external side effects block duplication.
- Runtime venue and symbol configuration changes preserve ownership and state.
- All live actions can be replayed from durable records and periodically reconciled to exchange truth.
- Backtests are reproducible and report uncertainty, exposure, cost and risk rather than presenting candle touches as guaranteed fills.
- One NVMe-backed Linux host can serve trading and bounded research without unnecessary services.
Non-goals for v1
High-frequency market making; market-order exits; continuously maintaining a full-depth order book for every listed instrument; derivatives; exchange-to-exchange arbitrage/transfers; indicators; complex ML symbol selection; custom binary transaction logs replacing a database; distributed consensus, Kafka, Redis, microservices, Go/Node/C++ live daemons; GUI-first development; automatic account-wide liquidation; performance/profitability guarantees.
2. Units, identity, custody, risk ownership
Account model: One configured bot-owned, dedicated spot account or subaccount per venue is the preferred v1 operating model. Multiple allocations may share it only through the same reservation ledger and single order executor. External/manual trading is disallowed for allocated funds; detect unexpected orders, fills, balance drift and deposits/withdrawals, pause new buys and reconcile. Do not blindly adopt external fills into strategy lots.

Reporting numeraire: Single configured quote/reporting currency for v1, preferably USDT if supported by the selected markets. Additional quote currencies are disabled by default until freshness-checked conversion and currency-specific fee/accounting tests are implemented. The seven venues need not have identical market availability; a venue with no eligible markets stays disabled.

Business IDs: venue_id, account_id, symbol_id (CCXT unified plus raw exchange market id), allocation_id, cycle_id, round_id, order_intent_id, client_order_id where accepted, exchange_order_id, and fill_id. All values are scoped so one exchange's BTC/USDT is never the same economic instrument or order as another's.

Financial quantities: Decimal or integer currency quanta, with lossless serialization; no binary floats for submitted price/size, fees, positions, reservations, PnL, or valuations. Do not assume CCXT precision is always a tick size—`TICK_SIZE` and SIGNIFICANT_DIGITS need distinct handling. CCXT formatting helpers are permitted only with post-format independent validation of exchange constraints.

Risk limits: Allocated quote cash by account, max quote spent per symbol/venue/global, max active symbols, max pending buys per symbol, total number of accumulated buy lots, residual/dust ceiling, max open orders, fee buffer, maximum source data age, maximum exit-pricing deviation, and disallowed markets. All live caps must be explicitly configured; no fictional safe default portfolio budget. Exposure limits take priority over keeping the ladder full. An eligible result of zero symbols is valid.
3. Exact executable grid behavior
3.1 Parameters and validation
Per allocation: g (positive grid spacing), N (target simultaneous outstanding buy levels, v1 candidate default 3 for research), max_lots, max_quote_committed, quote_reserve, minimum net target after forecast fees, seed-entry policy, active trading range, price/order TTL policy, and terminal/retirement policy. No unsafe numeric grid spacing or capital budget is silently chosen for live mode. Reject non-distinct quantized levels, unachievable minimum sizes, inadequate sellability after fees, and expected unprofitable spread.

For proposed buy price p, calculate the minimum increment-conforming amount q_min(p) satisfying all venue amount, cost/notional, and price constraints, fee reservations and expected sell-side minima. If the smallest valid buy cannot later produce a valid sell because of fee-in-base or other constraints, mark the market ineligible rather than enlarging the lot in baseline mode. Limits can be missing or exchange-specific; do not assume missing data means unlimited size. Re-check at actual placement time.

The limit price grid is defined in unrounded Decimal mathematics and formatted by an exchange-specific side-aware quantizer. Validate monotonic ordering after rounding. The prospective aggregate SELL is the least exchange-valid sell price that satisfies both (a) the configured geometric round-trip target over current net inventory weighted cost, and (b) a conservative strictly positive net profit after estimated selling fees and fee-token conversions if all owned sellable inventory fills at that limit. Record the exact formula and fee estimate in the order-intent audit fields. This is the baseline basket TP; do not silently implement one sell per purchased lot.
3.2 The round state machine (normative)
States: INACTIVE → SEED_PENDING → ACCUMULATING → SELL_REPLACING → DRAINING → (SEED_PENDING | ACCUMULATING), with PAUSED, RECONCILING, RETIRING, PORTFOLIO_EXIT, EXIT_BLOCKED overlays that suppress incompatible actions.

Seed: A new round with no sellable base uses one minimum-lot limit BUY priced from a fresh, rate-budgeted book. Maker-post-only may be requested where supported. A seed acknowledgement is not a fill. A seed that never fills leaves the allocation waiting; do not chase price or reanchor automatically from indicators. A timed cancel/reseed policy may be configured, but must require a fresh quote, bounded retries and a logged reason. Initial anchor A is the confirmed weighted execution price, not the merely submitted seed limit.

After a seed becomes sellable: Ledger fills and actual fee currencies; place one aggregate SELL at the valid basket TP and reserve its base inventory. Place up to N distinct lower-priced minimum-lot LIMIT BUYs at A / (1+g)^1, A / (1+g)^2, ...; reserve their exact quote plus fee buffer before submission. No open buy may exceed the allocation budget or configured maximum max_lots. Partially filled seed quantities smaller than a valid sell are tracked as unsellable residuals; do not fabricate a sell or oversell.

When a lower BUY fills: Persist the fill exactly once and release proportional quote reservation. Increase owned inventory by the true net base amount. Recalculate basket TP and aggregate SELL quantity. If a sell already exists, first obtain confirmed cancellation or a proven safe atomic edit; while its status is unknown do not place a second sell. After reconciliation, submit the successor sell for the exact unreserved, sellable base quantity. Maintain at most N active lower buys by adding the next unused lower level if budget/range/max_lots allow. A partially filled BUY may remain working; no repeated replenishment on duplicate updates. Repricing/cancel-replace churn must be bounded and measured.

When SELL partially fills: Apply each trade once, reduce the exact reserved base amount, and recompute owned inventory. Do not count the whole basket as realized or close the round. New BUY fills during the partial SELL follow the same safe cancel/reconcile/replace rule. Never submit an additional sell while old sell ownership is unknown.

When the aggregate SELL fully fills: Enter DRAINING; stop new ladder admissions, cancel remaining open BUYs, wait for terminal cancellation/reconciliation and reconcile final fills. If a late BUY filled, its net owned base becomes the next round's existing seed inventory, and a single SELL may be placed when valid—do not purchase a duplicate seed. If no base remains, start one new minimum-lot seed order using a fresh book only when the allocation remains enabled and risk permits. Do not anchor the next round to an old theoretical TP or stale observation.

Out-of-range / budget exhaustion: Stop adding new BUYs; maintain the existing sell and inventory under risk policy. Do not create an infinite descending ladder. A bounded strategic pause is different from forced liquidation. A risk violation blocks new exposure but does not discard responsibility for outstanding orders.

One-sell scope: At most one confirmed-or-possibly-active SELL per allocation_id. During safe cancel/replace there may temporarily be zero sells. If the exchange cannot prove cancellation or order status, enter RECONCILING/`OPERATOR_ACTION_REQUIRED`, not optimistic replacement. Protect against duplicate external sells from other controllers by account ownership checks.
3.3 Event discipline
The order's acknowledgement is not an execution. A private stream's cumulative filled value is not automatically an idempotent trade: prefer exchange trade IDs and/or reconcile actual fetch_my_trades; when unavailable use a carefully tested per-venue dedup/cumulative delta model. Duplicated, reordered, gapped, missing and late events must not duplicate cash movement. Committed events, not WebSocket callback order, drive strategy state transitions.

Normal trading progression depends on confirmed buy/sell executions. On-demand books do not trigger speculative trading when price moves; they are inputs for seed pricing, order limit legality, symbol eligibility and NAV. Failure to obtain a sufficiently fresh book prevents the corresponding new order or valuation; it must not be replaced with an invented market price. The preferred WebSocket subscriptions are private orders/trades; public books need not run continuously.
4. Portfolio cycle, +10% exit and valuations
Define E0 as the reconciled start-of-cycle reporting-currency NAV: cash available plus cash reserved (counted once), bot-owned inventory valued at conservative, fresh, executable bid-side prices, minus expected fees and other known liabilities. Trading fees in third currencies and any approved conversion path are included. External contributions are time-weighted or cash-flow adjusted under an explicitly implemented ledger convention; for v1 require no outside transfers within a cycle unless the adjustment logic is tested and the operator accepts it. Never simply add USD, BTC and USDT amounts.

Trigger one portfolio exit at the first fresh and sufficiently complete valuation satisfying adjusted_equity / adjusted_cycle_baseline >= 1.10. Sampling frequency is budgeted—checking equity requires public price information, even though prices are not trade signals. Stale or missing valuations make the target status unknown, not achieved. Record the snapshot and conversion quotes that triggered exit.

Portfolio-exit state machine:
1. Commit EXIT_REQUESTED, revoke new buy intents and freeze dynamic symbol admission globally; high-priority exit supervision supersedes local strategy replenishment.
2. Cancel all pending buys; reconcile late fills, unknown submit outcomes and actual inventory across every participating account, including paused/draining venues.
3. Cancel or safely replace ordinary TP sells as necessary. For each sellable owned base position, submit a bounded LIMIT SELL. Post-only/passive or explicitly enabled marketable-limit pricing are permitted, but type=market is always forbidden. Price floor, retry count, TTL, acceptable drift and minimum after-fee outcome must be explicit. A marketable limit is not a guaranteed fill and may execute at taker fees; represent that in NAV.
4. On partial fills, recalculate ownership/fees and iteratively manage only the remaining valid sellable amount. Dust not satisfying venue minima is tracked, not erased; do not transfer or dispose of it implicitly.
5. If all orders and bot-owned non-reporting positions are reconciled to zero except approved, explicitly quantified unavoidable dust, mark EXIT_COMPLETE, record actual net realized cycle return, and only then initialize the next cycle. If a venue or order is unavailable/uncertain, mark EXIT_BLOCKED, alert operator, and do not start a new cycle.
Crucial: +10% is a trigger threshold, not locked-in profit. Actual limit executions may realize less than +10% or even a loss. Report trigger NAV, realized exit NAV, outstanding exposure and exit shortfall distinctly. Risk commands are semantically different: pause-new-buys, cancel-buys, stop-and-reconcile, retire-symbol, limit-liquidate, emergency-halt. None silently changes order type.
5. Dynamic symbol selection without indicators
Three config modes, editable at runtime:
- manual: eligible symbols = explicit allowlist minus exclusions;
- automatic: discover spot candidates from each active venue subject to hard exclusions;
- hybrid: automatic candidates plus manually pinned symbols, with manual exclusions always winning.
Universe eligibility may use load_markets, trading status, market type, quote whitelist, fee tier, precision/minima, available allocated cash, spread/depth for minimum lot, recent exchange trades and realized order fill behavior where available. None of these becomes a technical-indicator or candle-based entry signal. Discovery uses sparse/on-demand snapshots, budgets and refresh windows; an exchange-wide continuous book stream is not a v1 requirement. Avoid leveraged-token listings via metadata and cautious symbol-pattern exclusions, with manual review for uncertain listings.

Score/categorize candidates using documented cost/spread/depth/stability gates, not a magic profitability score. Prevent churn using time-based residency and hysteresis. Pinned candidates bypass ranking, not min-notional/liquidity/fee/risk checks. It is correct to trade nothing.

Hot actions: enable_venue, disable_venue(mode), set_symbol_mode, allow, deny, pin, unpin, set_capacity, pause, resume, retire, reconcile. Persist a generation/revision for every action and invalidate pending intents created under an obsolete config. A denied symbol stops new buys and enters a selected manage-existing, cancel-buys-and-hold, or bounded-limit-exit path. Its order and inventory supervision remains alive until ownership is fully resolved. Disabling a venue is not deletion of its records or elimination from portfolio valuation/exit obligations.
6. Exchange adaptation and execution
A minimal ExchangeGateway protocol exposes validated operations: load markets, get fee and precision rules, fetch fresh book, place/cancel/fetch limit order, open/closed order query, personal trades, balances and private order/trade event subscription. Its capability registry is data, not optimistic method availability: record yes/no/degraded/unknown, tested CCXT version, spot-account options, client-order-ID semantics, pagination, private stream accuracy, rate costs and test date. New venues implement this contract, not new strategy code.
- Maintain a long-lived CCXT object per venue/account (or a documented shared rate-limiter topology for multiple accounts). Reuse load_markets, connections, queues and rate-limit state. Do not instantiate clients inside quote polls or per-symbol tasks.
- Confirm per-venue behavior of watch_orders and watch_my_trades, especially XT. has flags alone do not prove actual authenticated streaming. Private stream disconnects trigger bounded REST reconciliation before trading resumes.
- An order timeout leaves SUBMISSION_UNKNOWN; first search client IDs/open orders/trades, observe a bounded consistency window, reconcile, and escalate if uncertain. Never retry a create order blindly. Do not rely on clientOrderId unless the venue is tested to accept and return it.
- Cancellation is an asynchronous request, not a fact. Account for fill-during-cancel, partial-fill-after-request, order_not_found ambiguity, inconsistent status and late trade messages.
- Use per-venue and per-endpoint rate budgets (IP and key weights, subscription limits, bans and maintenance windows). Prioritize exits, reconciliation and private fills over metadata discovery. Backoff with jitter; never use unbounded exponential spawning.
- Normalize venue-specific fees, amount units, notional minima, order type spelling, funding balances, expiry, time-in-force and symbol IDs behind the gateway. Never hard-code a universal maker fee or assume post-only is universally supported.
7. Minimal architecture and resource management
One Linux host, one live Python process (one owner of trading keys)
  config / CLI control
  portfolio risk and cycle supervisor
  symbol manager and bounded market-data cache
  deterministic per-allocation grid reducers
  single order-command authority + CCXT gateway clients
  private event ingestion + periodic reconciliation
  SQLite: intents, orders, fills, reservations, balances, audit

Separate offline/research command (resource-limited; NO live keys)
  read-only original binary archive + validated manifest
  deterministic OHLC/backtest replay
  Parquet/Arrow optional data views and reproducible reports
Do not introduce services, Redis, Kafka, PostgreSQL, a custom ACID binary ledger, a cloud event bus, a separate microservice per venue, or a C++/Go/Node execution component without measured necessity and an ADR. The code structure should be modular but not fragmented into dozens of tiny abstractions. Strategy reducer returns intents; the executor validates and submits them; live, paper and backtest share the same reducer and risk/accounting contracts.

Concurrency: bounded priority queues; single designated live writer for SQLite; serialized commands per allocation; financial reservations atomically committed before HTTP side effects; explicit run_id/intent generations; process-level single-controller lock and startup reconciliation; bounded workers, retries and timeouts; supervised tasks with clean shutdown and draining. Do not hold database transactions across network await calls. Only the executor is permitted to submit/cancel. Separate research CPU/IO priority/process pool from live runtime; a slow scan must not starve a live fill handler.

Cache policy: all caches disposable, versioned and bounded. CCXT market snapshots use single-flight refresh; local exchange precision/fee cache invalidates on changes/rejections; book snapshots have source timestamp, age limit, exchange/symbol generation, LRU bound; prices used for NAV have independent freshness requirements; strategy positions and spendable balances are always derived from committed state and reconciliation, not TTL cache. Reconnect triggers a generation change and read-after-reconcile before new placement. The OS page cache handles immutable datasets; do not eagerly load terabytes or the whole historical archive into Python objects.
8. Local storage
- Financial state: one SQLite database on local NVMe, WAL, synchronous=FULL, foreign keys, bounded read transactions, checked backup/restore; patched SQLite 3.51.3+ or a verified fixed backport (including 3.50.7/3.44.6) to avoid the WAL-reset concurrency defect; verify linked runtime library at startup. No network filesystem for WAL. A single owner and short transactions are mandatory even on a patched release.
- Historical: retain existing source .bin files unmodified; validate 24-byte records (int64 ms timestamp plus four float32 OHLC per legacy review), exact byte order, UTC alignment, validity and gaps; create manifests with SHA256, provenance, row counts, exclusions, market mapping, schema version and immutable validated shards. Compare existing raw mmap, Arrow IPC and Parquet on actual NVMe before conversion. No general-purpose server/database for 1-minute data.
- Cache: RAM only, bounded; replay caches can reference immutable manifests and OS page cache. Do not cache private keys, persist every order-book update to SQLite, or silently use stale market constraints.
Full implementation constraints and benchmarks are in ADR-0001-NVMe-Storage-and-Caching.md.
9. Backtesting and scientific evaluation
The legacy developer's review describes 1,785 historical .bin files, approximately 28.36 GiB and 1.269 billion minute records at the review snapshot, primarily from venues other than the seven target venues, with significant gaps and zero-price histories. The original data files and legacy C++ source have not been independently inspected in this authoring session. The review says the old simulator has nonexistent balance enforcement, mixed currencies, hard-coded fees, no real order lifecycle, stale exits, and no intraminute execution ordering. Do not port its accounting or use matching its output as validation. Treat the historical snapshot and any data collection in progress as untrusted until checksummed and validated.

Tier A / baseline: replay validated 1-minute OHLC chronologically across symbols with finite spot wallet, minimum-size constraints, fees, orders, budgets and the same strategy reducer. Only the simulated execution adapter can turn an OHLC touch into a hypothetical fill. Same-bar high/low crossings are ambiguous; publish conservative, baseline and optimistic bounds, with no guaranteed maker queue fills or volume-constrained execution claims. Reject/quarantine zero/nonfinite prices, impossible OHLC, invalid timestamps, bad byte lengths, discontinuities, leveraged markets and unconfirmed market identity. No interpolation over gaps; record exact UTC endpoints and incomplete final interval.

Tier B / evidence upgrade: gather target-venue trade and volume history (and reliable fee schedules) to constrain fills. Use fixed research snapshots, latency/participation assumptions and conservative capacity limits. Do not pretend trades alone establish maker queue priority.

Tier C / credible microstructure: capture L2 books plus own order lifecycle/fills on target venues; calibrate queue models and latency against actual small-size forward execution. An engineering canary still requires separate operator approval; neither paper nor backtest is proof of future profit.

For every run record git commit, CCXT version, normalized config and hash, data manifest/hash, time range, rejected records, fee model, trade/fill assumptions, seed and latency assumptions, machine profile and result files. Reports: net realized/unrealized PnL, NAV curve, max drawdown, gross wins/losses and correctly defined profit factor, turnover, inventory time, utilization, fee burden, benchmark return and exit shortfall. Profit factor is undefined when gross losses = 0 or sample is inadequate; positive realized grid PnL cannot conceal inventory losses. Check lookahead, selection survivorship/delistings, walk-forward splits, fees by tier, up/down/sideways regimes and sensitivity. Compare basket-single-sell against independent-lot reference only in research, plus buy/hold and cash benchmarks with matching funded capital. Introduce increasing averaging only after predeclared out-of-sample results show better net return plus acceptable drawdown and tail exposure, not just higher profit factor.

No profitability claim or automatic optimizer-to-live transfer until sufficient representative target-venue execution evidence exists. A candle archive without order book/trade volume is not enough.
10. Runtime interface, deployment and security
Implement a strict configuration schema and gridctl local CLI before a web dashboard. Commands include doctor, status, markets inspect, venues, symbols, allocations, pause, cancel-buys, limit-liquidate, reconcile, backtest, bench-storage, backup and restore-verify. Mutations require config-revision checking and durable actor+reason audit. Sensitive actions require a second explicit CLI confirmation or manual deployment gate. Live trading disabled by default; distinguish simulated, sandbox and live credentials/DB directories by separate configs and explicit environment flags.

Deploy as one least-privilege systemd service on a Linux NVMe host (container optional, not mandatory), with process restart, resource caps, restricted filesystem and secret permissions, log rotation, monotonic timestamps, UTC storage, backups and health alerts. Shutdown: stop admissions, persist state, stop/cancel according to documented runbook, close streams and connections; never implicitly market-sell. Secrets stay out of git, test fixtures, audit payloads, logs, exception dumps and artifacts. User must create/access exchange API keys separately; no withdrawal scope or custody transfer. Use pinned dependencies and reproducible CI without live secrets.

Alerts: DB/drive faults, reconciliation mismatch, private WS gap, stale NAV, unknown order, orphan inventory, exit blocked, rate bans, server clock drift, excessive memory/CPU/WAL and stale backups. Failure default is stop submitting new orders, preserve existing exposures and surface responsibility; live exchange orders do not disappear just because the process crashed.
11. Implementation gates and acceptance
Follow ITERATIONS_AND_ACCEPTANCE_FINAL.md; every iteration produces a small tested PR/commit, risk summary, runbook updates, precise evidence, and remaining risks. A failing gate blocks forward progress. Start I0 only, not all eleven phases in one Codex run. Never enable live keys or place real orders without separate explicit user authorization after all readiness gates. No independent model or AI can guarantee absolute correctness: use determinism, assertions, fault injection, replay and real exchange validation.

Hard machine-checkable invariants (minimum):
1. A spy around all order APIs sees only limit type, in normal, emergency and exit paths.
2. Per allocation the count of known-or-possibly-active sell orders never exceeds one; late fills and cancel timeouts do not bypass it.
3. No negative spendable quote balance, no selling non-owned base, and no double-counting funds reserved for pending BUY or SELL orders.
4. Replaying the same fill, order update or user command twice produces the same final DB state.
5. An unknown submission cannot be retried until independently reconciled.
6. Crash/restart at all persistence/network boundary points restores truthful order/fill/position state before new buys.
7. Removing a symbol/venue with live orders leaves a supervised retained allocation and makes no new buys.
8. Global exit cannot mark complete while unresolved orders or unapproved sellable residuals exist; trigger return and realized return are distinct.
9. A stale book/FX quote or incomplete portfolio valuation cannot authorize new exposure or a +10% exit trigger.
10. Historical validity rejects and records invalid/gapped candles and pessimistically handles intrabar ambiguity.
11. Resource tests enforce bounded subscriptions, pending tasks, caches and retries; overload yields deliberate trading pause, not missed durable fills.
12. Unpatched SQLite runtime, disabled WAL/FULL sync, failed backup restore, or missing risk limits prevent live-mode startup.
12. Open evidence gates (deliberately not guessed)
The following are not implementation blockers for I0–I6, but are hard blockers for live activation: actual available quote and per-symbol cap; permitted quote and markets on each venue; active account type; verified order event methods and minimums; trading fee tier incl. charged currency; expected spread/depth and practical net-positive grid spacing; choose maker/passive versus bounded aggressive limit for seed/exit; global exit price deviation and retry policy; allowed dust; stale-data thresholds and refresh cadence; deployment hardware quotas; target-venue order-book/trade provenance; verified account ownership; and explicit operator sign-off. Codex should encode these in config, doctor checks and venue-capability matrices, never silently invent their values.
13. Primary references and evidence provenance
- User-provided legacy review: Pasted markdown.md (review of legacy C++ and raw data; not the source code itself).
- Official free bundled CCXT WS documentation: https://github.com/ccxt/ccxt/wiki/ccxt.pro.manual (Python uses import ccxt.pro as ccxtpro).
- CCXT market metadata, precise rounding and order APIs: https://github.com/ccxt/ccxt/wiki/Manual
- SQLite WAL and fixed-release guidance: https://www.sqlite.org/wal.html
- Arrow IPC: https://arrow.apache.org/docs/python/ipc.html
- Parquet: https://parquet.apache.org/docs/overview/
GitHub repository status at drafting: The connected slamerlim GitHub identity was confirmed, but slamerlim/grid-spot-ccxt returned 404 / was not in the accessible repository list. Codex must check whether it is created/accessible; no commit, PR or remote deployment is claimed by these documents.