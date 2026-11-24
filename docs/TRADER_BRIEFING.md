# GRID Spot Strategy Review Backtest Results and Trader Questions

Prepared for the project owner and the consulting grid trader. **Evidence includes completed runs through 10 November 2026 at 21:22:12 Moscow time (18:22:12 UTC).** This is the final consultation brief for that snapshot; the historical comparison itself remains unfinished.

**Assessment:** retain the new implementation and resolve its economic rules before adding features. The accounting and recovery foundation has substantial offline evidence. Live readiness and profitability across bullish, bearish and flat markets remain unproven. All three completed matched portfolio tests lost money because open-inventory losses exceeded realized trading gains.

**The strategy to evaluate is a spot basket grid.**

Trade only owned spot assets with finite, separately allocated venue funds. No margin, borrowing, derivatives, leverage, leveraged tokens or martingale sizing. Every order, including an exit, must be LIMIT.

A round starts with one minimum executable BUY. Actual fills establish inventory and the anchor. Lower BUY prices follow `anchor / (1 + spacing)^k`, with valid exchange rounding. Multiple lower BUYs may remain pending within capital and order limits. Maintain one aggregate basket SELL; new purchases update its quantity and target only through safe cancellation/reconciliation. After full sale, drain remaining BUYs and account for late fills before reentry.

The basket SELL must satisfy both the geometric markup over acquisition cost and an absolute positive net-profit target after forecast selling fees. It is not simply a percentage above the most recent BUY. Minimum lot is recalculated at each price; it does not imply constant coin quantity. BUY spacing and SELL markup currently share a parameter.

At +10% consolidated, cash-flow-adjusted net equity, freeze new entries and attempt bounded LIMIT liquidation. Actual exit proceeds can be lower. **Capital, lot and range caps stop further buying; there is no automatic drawdown stop, inventory-age exit or general loss-cutting rule.**

Research-only variations include a first entry one step lower, fresh-reference single-BUY placement, automatic symbol/exchange profit triggers and automatic portfolio restart. They are not current Python defaults; the ordinary Python global-cycle controller requires explicit restart.

Keep the simple architecture: one Python process, official CCXT, one executor and one SQLite writer. CCXT supplies unified exchange structures; the bot owns accounting, reservations and recovery. Missing exchange information remains unknown. The earlier numeric integration was corrected to use CCXT string-number mode and Decimal accounting. [Strategy specification](https://github.com/slamerlim/grid-spot-ccxt/blob/effb4b3490afb38889eeb3e76974a432b0ce1bd8/docs/PRD_GRID_SPOT_CCXT_FINAL_v2.md), [CCXT correction](https://github.com/slamerlim/grid-spot-ccxt/blob/effb4b3490afb38889eeb3e76974a432b0ce1bd8/docs/I8-CCXT-UNIFIED-NUMBERS.md), [official unified order API](https://github.com/ccxt/ccxt/wiki/Manual#querying-orders).

**Implementation status separates tested software from exchange readiness.**

| Area | Completed | Still required |
|---|---|---|
| Strategy and accounting | Decimal grid, fees, reservations, single SELL, partial/late fills, durable ledger | Approved live capital and strategy settings |
| Execution and recovery | Synthetic gateways, unknown-order protection, idempotency and crash/recovery tests | Authenticated sender, private events and real account reconciliation |
| Portfolio and controls | NAV, +10% trigger, bounded exits, retirement, operator controls, backup/restore | Real liquidity/account evidence, installed operation and alert delivery |
| Exchange integration | CCXT public probes and offline parser/request fixtures | All seven target venues remain NOT_READY for live trading |
| Research | Python replay/studies, standalone C++ engine, source validation, benchmarks and actual histories | Remaining portfolio cases, held-out validation and execution calibration |

The intended venues are OKX, HTX, Gate, XT, BingX, KuCoin and Bitfinex. The gateways currently admitted through `execution/synthetic.py` are simulations.

The recorded full regression snapshot passed **1,668 tests on each of Python 3.12 and 3.13**; later changes have separate focused evidence. C++ passed **8/8 CTest groups in each of Debug, Release and sanitizer builds**. These are finite correctness checks, not exchange certification.

The approved one-hour OKX observation collected **460 validated books but zero allocations, orders or fills** because required market constraints remained unknown. It provides feed/operational evidence, not paper-trading returns. The original 48-hour acceptance gate and live deployment remain unfinished.

The old simulator's accounting was not reused. Raw datasets remain unchanged. C++ is an offline research executable; the Python live core was preserved. Local checks are authorized, hosted CI remains pending under the known account lock, and GitHub Free-only remains mandatory. [Implementation boundaries](https://github.com/slamerlim/grid-spot-ccxt/blob/effb4b3490afb38889eeb3e76974a432b0ce1bd8/docs/IMPLEMENTATION-REVIEW.md), [test evidence](https://github.com/slamerlim/grid-spot-ccxt/blob/effb4b3490afb38889eeb3e76974a432b0ce1bd8/docs/CPP_BACKTEST_VERIFICATION.md), [observed hour](https://github.com/slamerlim/grid-spot-ccxt/blob/effb4b3490afb38889eeb3e76974a432b0ce1bd8/docs/I8-PAPER-EVIDENCE.md).

**The historical results are reproducible simulations under explicit assumptions.**

All financial results below are **UNVERIFIED SOURCE / ASSUMPTION-BASED**. Returns include remaining inventory at estimated net liquidation value; no sale is forced at the final timestamp. Drawdown is measured at observed closes, so gaps and intraminute extremes limit it.

Ten individual histories completed: **19,965,272 records**, each separately funded with **1,000 USDT**, using the seeded multi-BUY strategy and strict OHLC convention.

| Venue and market | Net return | Maximum observed drawdown |
|---|---:|---:|
| OKX 1INCH/USDT | +0.45% | 19.91% |
| HTX MINA/USDT | −34.14% | 42.09% |
| Gate WPAY/USDT | +0.76% | 1.60% |
| KuCoin AKT/USDT | +8.86% | 9.53% |
| OKX AGLD/USDT | +24.86% | 18.79% |
| HTX XTZ/USDT | +32.00% | 11.00% |
| Gate BGSC/USDT | −49.20% | 49.21% |
| KuCoin OP/USDT | +12.40% | 19.84% |
| OKX APE/USDT | −42.57% | 46.04% |
| HTX ELA/USDT | +25.09% | 12.38% |

Eight source histories span 4 August 2022 to 4 November 2026; WPAY begins 8 October 2024, and BGSC spans 8 December 2024 to 3 November 2026. These are source endpoints, not verified listing dates. Seven positive and three negative returns over different horizons cannot be averaged into a portfolio result or treated as annual returns.

The matched portfolio interval is **8 December 2024, 14:00 to 3 November 2026, 23:09 Moscow time, end exclusive**: 695.38125 calendar days. Each experiment starts with **10,000 USDT**, split into ten fixed allocations across four venues.

The frozen scenario uses 1% spacing, 5-USDT minimum notional, 0.1% quote fees per side, a 100-USDT reserve per allocation, a 900-USDT commitment cap and 100 accumulated lots. Multi-BUY permits three pending lower orders; single-BUY permits one. These are hypothetical research inputs, not verified venue rules or live recommendations.

| First entry and lower BUY policy | Strict OHLC | BUY priority | SELL priority |
|---|---|---|---|
| Seed plus multiple lower BUYs | Complete | Complete | Complete |
| Seed plus one lower BUY | Unfinished attempt | Not run | Not run |
| Lower first entry plus multiple BUYs | Not run | Not run | Not run |
| Lower first entry plus one BUY | Not run | Not run | Not run |

Only **3/12 matched cases** are complete at the cutoff; one further case has started and eight are unstarted. These three cases compare fill assumptions for the same strategy, not different strategies. The touch conventions are not guaranteed best/worst performance bounds.

Amounts are USDT unless shown as percentages, rounded independently to two decimal places.

| Measure | Strict | BUY priority | SELL priority |
|---|---:|---:|---:|
| Ending estimated net NAV | 9,856.39 | 9,861.97 | 9,846.99 |
| Net return | −1.44% | −1.38% | −1.53% |
| Maximum observed drawdown | 11.37% | 11.38% | 11.38% |
| Realized PnL | +981.44 | +987.02 | +972.03 |
| Net unrealized PnL | −1,125.04 | −1,125.04 | −1,125.04 |
| Total net PnL | −143.61 | −138.03 | −153.01 |
| Modeled fees already included in PnL | 221.04 | 222.28 | 218.94 |

All three retain inventory costing **1,716.72 USDT**, with estimated net proceeds of only **591.67 USDT**. Strict-case cash is **9,264.72 USDT**, including **55.06 USDT reserved for orders**; reservations are not another asset or deduction.

The strict case completed **5,433 profitable basket SELLs yet lost money overall**. Realized losing sales were zero, making profit factor undefined. BGSC and MINA together lost about **929.77 USDT** in this matched period, with inventory held for nearly the entire study. None of these three portfolio cases triggered the global +10% exit; they therefore do not validate its execution.

The funded buy-and-hold benchmark returned **−36.89%**; nominal USDT cash returned **0%**. The grid beat this more heavily invested buy-and-hold counterfactual but lost against cash. Buy-and-hold began about 90% invested, unlike the grid's variable exposure; this does not isolate trading skill.

Retrospective monthly descriptions for the strict portfolio have median endpoint returns of **+1.50% in six rising spans, −0.96% in nine falling spans and −0.29% in eight flat spans**, plus one unclassified span. Labels use benchmark endpoint movement above +5%, below −5%, or otherwise flat, with at least 20 days. They are not live signals, full-month compound returns or proof of performance in every regime.

The ten individual replays took **70.18 minutes** in total; the three portfolios took **33.92, 33.59 and 33.58 minutes**, with peak child memory about **14 MiB**. A measured C++ optimization improved its own benchmark; the narrower Python comparison was faster, so no general C++ speed advantage is claimed. [Detailed results and exact source spans](https://github.com/slamerlim/grid-spot-ccxt/blob/effb4b3490afb38889eeb3e76974a432b0ce1bd8/docs/MODULE1_10_MARKET_BACKTEST_REPORT.md).

**Data sufficiency depends on the claim being made.**

We have enough data for an honest, reproducible experiment that states its assumptions. We do not have enough to claim these were executable historical returns, or that the strategy is profitable in live trading.

| Evidence area | What is available and sufficient | Gap and consequence |
|---|---|---|
| Selected file integrity | Complete hashes and structural scans of all ten files: 19,965,272 records, 479,166,528 bytes, zero structural rejections | Certifies these bytes and their structure, not the whole archive or the economic truth of the prices |
| Schema and chronology | Verified little-endian 24-byte records: int64 millisecond timestamp plus four float32 OHLC prices; ordered observed bars and explicit gaps | No trades, volume or order book; original precision lost to float32 cannot be recovered |
| Individual coverage | Six selected histories have no missing minutes inside their endpoints | WPAY misses 9,413 minutes, BGSC 1,958, ELA 2 and OP 1; no fabricated fills or interpolation across gaps |
| Joint coverage and valuation | Common calendar interval and missing observations are counted; terminal NAV is known | 478 shared complete UTC days, only 66 consecutive complete days; 7,777 minutes lack a market and each portfolio has 2,606 unknown NAV observations. No gap-free one-year or complete drawdown claim |
| Market identity and collection | Filenames and frozen mappings identify the assumed markets | Historical spot status, listing/migration history and collection provenance unverified; flat-price rows comprise 3.81%–65.67% of each source and may reflect inactivity or upstream carry-forward |
| Historical trading constraints | Explicit synthetic price/amount increments and minima make the simulation reproducible | Historical precision, minimum size/notional, trading halts and rule changes are unverified |
| Fees and cost basis | Tested accounting supports declared fees; modeled fees are included | Actual historical account tiers, maker/taker mix, fee currencies and rebates are unknown |
| Fill realism and capacity | Delayed hypothetical fills under three OHLC conventions; synthetic partial/cancel tests | No intraminute trade order, traded volume, spread/depth history, queue position or actual partial-fill/latency evidence; candle touches do not establish fills |
| Selection and generalization | Cohort selected by frozen coverage rules before strategy returns | No certified point-in-time universe, delisting or token-redenomination history; survivorship bias remains. Four venues and ten assets do not represent all intended trading |
| Accounting reproducibility | Thirteen completed result hashes and cash/PnL/return identities checked; Python/C++ differential tests | Agreement establishes tested model behavior, not historical execution or exhaustive correctness |
| Forward and held-out evidence | Public observation plus research tools exist | Zero forward paper fills, no live fills and no completed representative held-out profitability validation |

Today's CCXT metadata and public quotes cannot retroactively establish historical fees, market rules or fills.

Accordingly, **complete/sufficient now:** structural validation of the selected files, replay of their observed records, completed-run accounting, and reproducibility under declared assumptions. **Incomplete/insufficient now:** the full comparison, continuous common-year coverage, certified market/rule/fee history, executable fills/capacity, and robust out-of-sample or live profitability. [Frozen assumptions](https://github.com/slamerlim/grid-spot-ccxt/blob/effb4b3490afb38889eeb3e76974a432b0ce1bd8/research/runs/module1-20261110/assumptions.md), [source and numerical verification](https://github.com/slamerlim/grid-spot-ccxt/blob/effb4b3490afb38889eeb3e76974a432b0ce1bd8/docs/CPP_BACKTEST_VERIFICATION.md).

**The trader should specify behavior in each market condition.**

| Condition | Main exposure | Required decision |
|---|---|---|
| Sustained rise | Unfilled passive entry and low exposure after basket sales | Accept missed upside or define bounded entry/reentry rules |
| Sustained decline | Accumulating depreciating inventory and prolonged capital lockup | Stop adding, hold under a time/risk limit, or attempt a bounded loss exit |
| Oscillating range | Fees, fill quality and basket turnover determine whether oscillations pay | Set profitable spacing/targets using realistic execution costs |
| Quiet or illiquid range | Few opportunities and difficult exits | Define inactivity and market-retirement criteria |

Spot-only trading cannot guarantee profit during sustained depreciation. Permission to remain in cash is part of a robust policy. Total equity, including unsold losses, must determine success; completed grid profits alone are insufficient. [OKX on total PnL and grid profits](https://www.okx.com/en-sg/help/how-do-total-profit-and-loss-pnl-and-grid-profits-work).

**Questions for the trader, in priority order**

1. **Objective:** What return on all funded capital, maximum drawdown, maximum underwater duration and benchmark define success? Is capital preservation the goal in bearish periods?

2. **Loss handling:** At what price, exposure or equity condition do we stop buying, keep holding, or start a LIMIT exit at a loss? What ends indefinite waiting?

3. **Capital:** How should allocation, cash reserve, maximum accumulated lots and simultaneous BUY count fit together? What asset, correlation and venue concentration limits apply?

4. **Spacing and profit:** Should BUY spacing and basket SELL markup remain coupled? What minimum net basket profit is worthwhile after actual fees, spread and holding time?

5. **Minimum lots and basket exit:** Is the smallest executable BUY plus one aggregate SELL the intended economic design? What return and capital utilization should we expect from those constraints?

6. **Entry and reentry:** Enter near the bid or one step lower? When does an unfilled seed expire or reprice? After full sale, reenter near a fresh quote or below the last SELL?

7. **Sharp falls:** Should several pre-existing BUYs fill, or should only one lower BUY work at a time using a fresh reference after reconciliation?

8. **Global cycle:** Is +10% on total net equity appropriate? Should symbol/exchange profit exits exist? When does the next cycle start, and are profits reinvested, reserved or withdrawn?

9. **Blocked exits:** Within LIMIT-only rules, what price deviation, timeout and retry limit apply? What happens if inventory cannot be sold inside those bounds?

10. **Universe:** Which liquidity, spread, listing-age and market-quality rules qualify a spot asset? How are delistings and permanently impaired markets handled without selecting historical winners afterward?

11. **Missing evidence:** What trade/volume/book resolution and actual fee/fill records are needed to calibrate this strategy at minimum size? Can the expert supply anonymized examples including unsold inventory and failed exits?

12. **Acceptance:** Which held-out periods, adverse-cost scenarios, drawdown and capital-lockup limits must pass? What result would make us reject the strategy?

Request answers as **condition → action → numerical cap → resume condition**. Have the expert work through a sustained rise, prolonged decline, repeated range, sudden gap and unavailable exit, tracking cash, inventory and total equity. Do not add a regime classifier or indicators merely to label those scenarios.

**The next work should close the largest evidence gaps first.**

1. Complete the frozen twelve-case matrix and retain every negative or incomplete result.

2. Obtain the trader's concise rule sheet and predeclare the next controlled comparisons.

3. Verify market identity and historical trading rules/fees for a small target roster; preserve gaps and exclude unverifiable claims.

4. Add appropriate trade/volume and order-book evidence. Trades constrain execution assumptions but do not prove maker queue priority; calibrate further with authorized own-order observations.

5. Evaluate untouched periods and adverse costs, then complete one venue's execution/recovery integration and meaningful forward acceptance before expanding operationally.

Keep the original archive and existing tested financial core. No new framework, wholesale data conversion or additional live strategy is needed to make these decisions.

**Evidence reference:** repository publication `effb4b3` and its compact tables cover the ten individual histories plus the first two portfolios. This brief additionally includes the independently checked third result in `/opt/grid-runs/module1-20261110/full-study-v4-notices/seed_multi_sell/`, completed at the cutoff; its result SHA-256 is `9061f41e0ced5642aa8026812e879ad4c35d098a16d64b4a8841d87749c9bfe9`. All thirteen completed-result hashes and cash/PnL/return identities agree. Monthly figures above describe the strict case only. Unfinished results are excluded; the comparative study remains incomplete.
