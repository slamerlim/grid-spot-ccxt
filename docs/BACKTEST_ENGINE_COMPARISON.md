# Binance BTC/USDT: actual browser runs and engine comparison

Historical report: these runs used different funded and audited models. New paired
runs now use the explicitly approved matching research mode; see
[the current comparison and improvement plan](STRATEGY_IMPROVEMENT_PLAN.md).
The historical measurements below remain retained as evidence.

Verified on 2026-11-11 UTC / 2026-11-12 Asia/Tokyo. The two requested web interfaces were run separately and sequentially on the complete Binance BTC/USDT archive. The primary runs changed only the selected market and full-history period; all other controls kept the values presented by a fresh browser session.

**The hosted paired run now finishes in about 57 seconds.** The current funded engine itself took **49.058 seconds**, compared with the preserved **685.704-second** run. Its complete result is byte-identical to that earlier result. The local Streamlit simulator showed its result in **3.010 seconds** and its chart in **4.973 seconds**. These are different workloads: the hosted button runs the funded engine and then the audited simulator; the local button runs only the audited simulator. Final complete chart rendering and interactions were separately verified after the renderer correction described below.

**The live interfaces also had different defaults.** The local Streamlit process presented a **100 USDT order and averaging factor 1**. The hosted audited form presented **10 USDT and 0.0001**. This was recorded before submission and confirmed in the saved run configurations. A separate local run using the hosted settings produced exactly the same financial results, trades and chart data as the hosted audited run.

## What was run

| Item | Observed value |
|---|---|
| Local interface | `http://127.0.0.1:8502/` |
| Hosted interface | `http://103.1.225.114/module1/backtesting/` |
| Market | `binance:BTC/USDT` |
| Source | `/opt/datasets/crypto_1m/binance_USDT_BTC.bin` |
| File size / records | 116,110,488 bytes / 4,837,937 one-minute candles |
| First candle | 2017-08-17 04:00 UTC |
| Last candle | 2026-11-03 20:08 UTC |
| Exact requested interval | `[2017-08-17 04:00, 2026-11-03 20:09)` UTC |
| Hosted displayed interval | 2017-08-17 07:00 → 2026-11-03 23:09 Europe/Moscow |
| Source SHA-256 | `b32ee355e9b5e0bd49ec65a47ac3bb602493b55c1f4888805cdf552ab4b6fc0c` |
| Internal gaps / missing minutes | 35 gaps / 8,632 missing minutes |
| Invalid candles in audited validation | 0 |
| Hosted paired run | `paired-20261111T192938Z-068e5198`, PASS |
| Hosted funded run | `run-20261111T192943Z-68e8b481`, PASS |
| Hosted audited run | `legacy-20261111T193032Z-a01042f6`, PASS |
| Local default run directory | `/opt/module1_n/runtime/runs/run-_dre1eyl` |
| Local matched-settings directory | `/opt/module1_n/runtime/runs/run-7d7h2i_d` |

“Full overlap” covers the whole common endpoint interval; it does **not** imply continuous history. The 8,632 missing minutes are about 0.1781% of the interval. Both engines retained the gaps and processed the same 4,837,937 observed candles. No missing candles were manufactured. All source bytes remained unchanged.

The audited runs used the same saved CCXT Binance market snapshot, fetched at `2026-11-11T13:26:09.194195+00:00`, approximately six hours before these runs. The snapshot was within the local simulator's 24-hour cache lifetime, so the runs did not need a refresh. It records price tick `0.01` USDT and amount tick `0.00001` BTC. A present-day snapshot does not establish that these rules applied throughout 2017–2026.

## Defaults and model differences

| Setting or behavior | Local Streamlit, actual defaults | Hosted audited simulator, defaults | Hosted current funded engine, defaults |
|---|---|---|---|
| Engine | `/opt/module1_n` audited C++ | Same audited C++ binary | Current C++ replay engine |
| Initial funding | No funded starting NAV; negative cash allowed | Same | 1,000 USDT, including 100 USDT reserve |
| BUY size | 100 USDT first order | 10 USDT first order | Minimum executable lot; synthetic 5 USDT minimum notional |
| Averaging factor | 1 | 0.0001 | No increasing lot sizing |
| Grid step | 1% | 1% | 1% |
| Ordinary basket target | 1% above average price before fees | Same | 1% geometric floor over fee-inclusive cost, plus minimum 0.01 USDT net basket profit |
| BUY / SELL fee | 0.2% / 0.2% | 0.2% / 0.2% | 0.2% / 0.2% |
| Price / amount tick | 0.01 USDT / 0.00001 BTC, saved CCXT | Same saved CCXT rules | Synthetic 0.00000001 USDT / 0.0001 BTC |
| First entry | First crossed grid level; no seed purchase | Same | Minimum passive seed LIMIT |
| Candle execution | Synthetic O → H → L → C path, all crossed levels | Same | Strict convention; ambiguous BUY/SELL bars skipped |
| New order / cancel delay | Intrabar synthetic path behavior | Same | 1 bar; no creation-bar fills |
| Lower pending BUYs | Crossed-level simulator behavior | Same | 3 levels, at most 100 held lots |
| Spread | No order-book spread model | Same | Synthetic 0.1% |
| Account and committed-cost limits | No finite wallet constraint | Same | Finite cash, reservations, 900 USDT commitment cap |
| Portfolio exit | No funded +10% NAV cycle | Same | +10% NAV trigger enabled; no trigger occurred here |
| Historical minimum order limits | Not modeled by audited simulator | Same | Declared synthetic minimums enforced |
| Arithmetic | Audited binary's floating-point accounting | Same | Exact rational/quantized financial arithmetic |

The local disk file `/opt/module1_n/app.py` declares 10 and 0.0001, but the actual fresh-browser controls and saved primary run used 100 and 1. The local service process started at 2026-11-11 14:16:34 UTC, before that file's 17:22:34 UTC modification. A retained older runtime is consistent with those observations; the comparison does not assert a deeper cause without inspecting or restarting that process. The primary evidence preserves what the live interface actually did; the read-only [runtime observation](../../grid-runs/ui-comparison-20261112/local-runtime-observation.json) records the process identity and timestamps.

Averaging factor 1 does not mean doubling each order: at the default 1% grid step, the documented formula multiplies the next amount by `1 + 1 × 1 / 100 = 1.01`. Factor 0.0001 gives a multiplier of `1.000001`. Combined with the tenfold larger initial order, this changes capital use and results substantially.

## Results in USDT

Values below are rounded for reading; the linked JSON evidence retains the original values.

| Metric | Local actual defaults: audited | Hosted audited defaults | Hosted current funded defaults |
|---|---:|---:|---:|
| Total PnL including open position | 9,439.041374 | 758.649402 | 30.668053 |
| Realized PnL | 9,474.027367 | 761.973320 | 30.668053 |
| Unrealized PnL | −34.985993 | −3.323917 | 0 |
| Paid fees | 6,370.471320 | 512.387498 | 15.433983 |
| BUY fills / SELL events | 15,188 / 5,821 | 12,931 / 4,888 | 739 / 208 |
| Peak position cost | 14,001.651830 | 996.116742 | 228.213307 |
| Peak cash deficit | 10,609.924662 | 588.728546 | None; finite wallet enforced |
| End position, BTC | 0.01298 | 0.00120 | 0 |
| End position cost | 1,041.397380 | 96.366394 | 0 |
| Maximum drawdown | 4,141.467313 USDT | 373.262341 USDT | 4.436069% of liquidation-adjusted funded NAV |
| Starting / ending funded NAV | Not defined | Not defined | 1,000 / 1,030.668053 |
| Funded return | Not defined | Not defined | +3.066805% |
| Terminal state | Open basket | Open basket | Seed pending; one open BUY, 5.378215 USDT reserved |

The audited cash deficit is a financing requirement, not a permitted negative spot wallet. Its PnL is not a return percentage and cannot be compared directly with the current engine's +3.066805% as if both invested the same capital. Its absolute drawdown also has a different denominator from the funded NAV drawdown.

The funded buy-and-hold counterfactual ended at **16,422.565170 USDT liquidation-adjusted NAV**, a **+1,542.256517%** return, with approximately **82.056091%** observed drawdown. It uses the same original funding/reserve and configured fees, but assumes an immediate benchmark purchase rather than proving a real LIMIT fill. Cash held at par stayed at 1,000 USDT.

### Why the funded engine traded much less

The current engine implements a different strategy and execution contract: minimum-size seed, fixed pending orders, finite reservations, delayed order eligibility/cancellation, strict ambiguity handling, and a geometric basket target with an additional absolute net-profit floor. The audited simulator repeatedly traverses its assumed intrabar path and can continue accumulating without a wallet cap. Their different precision rules and lot sizes also change which orders and exits are representable.

The funded result's mean observed position-cost utilization was **0.164621%** of original capital; mean cost plus reservations was **0.736998%**. Inventory was present at about **4.144763%** of observed minute closes. The cash cap was therefore not the only explanation for its lower PnL: much of its history was idle.

The retained daily funded NAV is flat at the terminal 1,030.668053 USDT from **2020-03-14 through 2026-11-03**, covering 2,427 retained samples. The final state has no BTC and a pending seed BUY. This is consistent with a passive fixed-price reentry that the rising market never revisits. Daily chart samples alone do not establish the exact last-fill timestamp. The full replay nevertheless consumed every candle and completed normally; the flat chart is not an unfinished process or a performance regression.

Whether an unfilled seed should expire, follow the market, or remain fixed is a strategy decision for the trader. Changing it would change the strategy contract; it was not silently changed as a performance optimization. Likewise, the audited simulator's larger reported PnL is not evidence that its unconstrained fill and funding assumptions are achievable.

## Same audited settings: exact agreement

A supplemental local UI run explicitly changed only order size to **10 USDT** and averaging to **0.0001**, matching the hosted audited form. This is separate from the requested actual-default comparison.

- All **39 deterministic summary fields** match exactly, including PnL, fees, counts, inventory, drawdown and gaps. Runtime, throughput and wrapper-added hashes were excluded from that field comparison.
- The audited executable SHA-256 is identical: `7405ec85f42811ac7cea6b1693592e3db574528142ccaecf0f600e8261dbe06f`.
- `trades.bin` is byte-identical: SHA-256 `b682fd7303882bed11cae1c39070cbd2b1bd1fd8b9a67fb1b9885bb1c795faff`.
- `series.bin` is byte-identical: SHA-256 `882f0b38c7f71328a7c1911a527c1c95dbade2c8e926ad987aa22a7cd942a4a8`.

Thus the observed local-versus-hosted audited difference comes from the actual defaults. The hosted wrapper did not change the simulated trades or chart data when settings, source, window, binary and precision matched.

## Why the earlier suite was incomplete

The preserved suite `paired-20261111T185737Z-1bf7c379` ended as `INCOMPLETE`, with stage `audited_incomplete`. Its funded leg completed successfully after 685.704 seconds; the following audited leg `legacy-20261111T190908Z-b92e3533` then exited with the exact error:

> Order quantity rounds to zero; increase order_quote

That audited configuration had no saved Binance market snapshot and therefore used the declared synthetic amount tick **0.0001 BTC**. A 10 USDT purchase above 100,000 USDT/BTC is smaller than that tick and rounds down to zero. This is a precision/admission failure in the second leg, not a funded replay that ran indefinitely. The old suite's generic error did not explain that distinction well.

The hosted audited runner now has the same existing saved Binance snapshot as the local simulator, with the finer **0.00001 BTC** amount tick. The snapshot was copied locally; no exchange request was needed. With the same 10 USDT order and complete history, the new audited leg passes. Specific engine refusal reasons are now surfaced by the runner. The current funded engine retains its declared synthetic market rules, as shown in the settings table; this correction does not silently equate the two models' precision assumptions.

The original [suite status](../../grid-runs/ui-comparison-20261112/original-paired.json), [audited configuration](../../grid-runs/ui-comparison-20261112/original-audited-config.json), [stderr](../../grid-runs/ui-comparison-20261112/original-audited-stderr.txt) and [failure receipt](../../grid-runs/ui-comparison-20261112/original-audited-receipt.json) are preserved for inspection.

## Timing and the performance fix

| Measurement | Seconds | Scope |
|---|---:|---|
| Preserved original funded run | 685.704 | Same complete source/config/result; original process receipt |
| Optimized clean funded benchmark | 51.200 | Full replay; archive audit paused; **13.39×** faster |
| New actual hosted funded process | 49.058 | Progress-enabled production research binary; **13.98×** relative to preserved receipt |
| New hosted audited process | 1.484 | Audited binary; internal engine timer 1.472 s |
| New hosted suite server elapsed | 55.835 | Input validation, both engines, checks and publication |
| New hosted click → terminal PASS observed | 56.723 | Browser observation, including polling interval |
| New hosted click → both charts present | 59.701 | Browser rendering and data retrieval included |
| Local default click → result | 3.010 | Fresh local browser, actual 100/1 controls |
| Local default click → chart present | 4.973 | Plotly rendering included |
| Local default C++ / reported whole run | 1.507 / 1.595 | Local displayed timers; not identical to click-to-result scope |

The hosted suite spent about **4.650 seconds before starting the funded subprocess**, 49.058 seconds in that subprocess, 0.498 seconds between the subprocesses, 1.484 seconds in the audited subprocess and 0.145 seconds finishing publication. The browser polls status, so observed completion can lag server completion. These timings are observations on this machine, not universal latency promises. The concurrent archive audit was paused for the primary measurements; filesystem cache was not forcibly cleared.

Profiling identified exact-rational normalization and large-integer arithmetic as the main funded replay cost. The optimized arithmetic uses a checked native-integer fast path with arbitrary-precision fallback while retaining exact results. The recorded profile window had 18,964,054 normalizations and 52,948,679 large-integer products. The change preserves every source record, source hashing, financial checks and per-bar NAV/drawdown calculation; it does not speed up by dropping candles.

The old result, clean optimized benchmark and new hosted run all produce the same **658,567-byte** result with SHA-256 `9db0134df3c078e605f650c887ef7674961fad0543795ac604fff90d0c6f4e76`. The benchmark receipt also records 6,081 arithmetic-oracle cases and eight passing CTests in each of release and sanitizer builds. Focused Python validation covered 107 checks for the changed components plus three regressions: rerunning with a different pair, counting completed legs during final verification, and honoring cancellation during that verification. All 110 checks passed. This separates a demonstrated speed improvement from any change in trading outcomes.

## Progress and charts verified in the hosted browser

The progress display showed the active engine, selected market, stage, measured candle count and denominator, stage percentage, completed engine legs, last measurement age and elapsed time. For example, during the funded leg it reported more than 3.1 million of 4,837,937 candles and continued increasing. The audited leg uses an activity indicator when its executable does not report an incremental counter. Stage percentages are not presented as overall-suite percentages, and no estimated remaining time is displayed.

The hosted paired run loaded the following complete Plotly chart data:

- Current funded NAV and buy-and-hold: two Plotly scatter traces, 3,367 retained points each.
- Audited price, PnL, invested cost and drawdown: four Plotly OHLC traces, 3,366 daily bars each, plus all 12,931 BUY and 4,888 SELL marker records in `scattergl` traces.

This uses the same Plotly technology and OHLC/WebGL trace types as the local simulator. The observed local browser runtime was Plotly **4.2.0**; the vendored hosted runtime is the official **3.7.0 strict bundle**, which renders accelerated markers under the existing script policy. They are not claimed to be identical versions. The funded engine saves daily NAV samples and endpoints, so its line chart does not invent intraday NAV OHLC bars. The audited OHLC series preserves its engine's aggregated open/high/low/close values.

Final live browser checks after deployment verified actual marker painting, pointer hover with quantity/quote/fee details, pan, linked zoom, independent logarithmic axes and PNG export. A second browser with WebGL disabled painted all **17,819 markers** through the SVG fallback. Desktop and 390-pixel mobile layouts had no horizontal overflow or browser errors. See the [rendering evidence](../../grid-runs/ui-qa-20261112/live-charts.json) and [zoomed trade screenshot](../../grid-runs/ui-qa-20261112/live-strict-webgl-chart.png). The earlier 59.701-second browser measurement predates this renderer correction and measures chart-host availability; it is not a new timing claim for final complete marker painting.

The same browser session verified 7-day, 30-day and full-overlap controls, 0.2% BUY/SELL defaults, absence of the two assumption-confirmation checkboxes, and an enabled Run button after terminal PASS. Selecting Binance ETH/USDT afterward recalculated its full interval and left Run enabled. No browser page errors occurred. This last interaction checked readiness; it did not launch an additional ETH run.

## Evidence and interpretation limits

All comparison evidence is in `/opt/grid-runs/ui-comparison-20261112/`, outside Git:

- [Local default browser receipt](../../grid-runs/ui-comparison-20261112/local-browser.json), [config](../../grid-runs/ui-comparison-20261112/local-config.json), [summary](../../grid-runs/ui-comparison-20261112/local-summary.json), [screenshot](../../grid-runs/ui-comparison-20261112/local-result.png).
- [Hosted browser receipt](../../grid-runs/ui-comparison-20261112/hosted-browser.json), [progress observations](../../grid-runs/ui-comparison-20261112/hosted-progress.json), [paired result](../../grid-runs/ui-comparison-20261112/hosted-result.json), [screenshot](../../grid-runs/ui-comparison-20261112/hosted-result.png).
- [Funded process receipt](../../grid-runs/ui-comparison-20261112/hosted-current-receipt.json), [full result](../../grid-runs/ui-comparison-20261112/hosted-current-result.json), [exact experiment configuration](../../grid-runs/ui-comparison-20261112/hosted-current-experiment_config.json).
- [Audited process receipt](../../grid-runs/ui-comparison-20261112/hosted-audited-receipt.json), [config](../../grid-runs/ui-comparison-20261112/hosted-audited-config.json), [summary](../../grid-runs/ui-comparison-20261112/hosted-audited-summary.json).
- [Matched-settings verification](../../grid-runs/ui-comparison-20261112/matched-simulator-verification.json), [supplemental local receipt](../../grid-runs/ui-comparison-20261112/matched-local/local-browser.json).
- [Independent optimization benchmark](../../grid-runs/engine-performance-20261112/benchmark.json).

The full HTX/Gate archive findings are reported separately in [HTX/Gate data integrity audit](HTX_GATE_DATA_AUDIT.md).

These runs prove reproducibility under their declared models. They do not recover missing minutes, historical venue-rule changes, historical fee tiers, volume, order-book depth, queue priority or actual fills. The same source and dates make the comparison transparent; they do not make distinct strategies economically equivalent. Trader review should explicitly decide seed expiry/repricing, order sizing, wallet limits, basket-profit definition and intrabar execution assumptions before using either result to choose a production strategy.
