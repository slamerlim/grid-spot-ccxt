# Module 1 offline C++ research engine

This directory is an isolated C++20 research component. It neither constructs
CCXT clients nor writes a trading database. It implements source scanning,
checked money arithmetic, the allocation event reducer, delayed OHLC execution,
portfolio exit coordination and streaming historical study orchestration.
Implementation is not evidence that a full historical study has passed: retain
the exact source/configuration/build identities and validation/run receipts.
Source scan throughput must not be reported as trading-backtest throughput.

## Build and verify

Requires a C++20 GCC/Clang compiler, CMake >=3.20, and existing OpenSSL >=3
development headers/library. OpenSSL supplies SHA-256 and bounded exact integer
operations; no downloaded/vendored framework is used. On the current host:

```sh
/opt/grid-tools/module1/bin/cmake -S cpp/backtest -B /opt/grid-builds/module1/debug -DCMAKE_BUILD_TYPE=Debug -DGRID_PYTHON=/opt/grid-tools/module1-py312/bin/python
/opt/grid-tools/module1/bin/cmake --build /opt/grid-builds/module1/debug -j2
/opt/grid-tools/module1/bin/ctest --test-dir /opt/grid-builds/module1/debug --output-on-failure
```

For a portable optimized build use `-DCMAKE_BUILD_TYPE=Release` with a distinct
build directory. For the small test workload use `-DGRID_SANITIZE=ON` and Debug.
Project warnings are errors. Neither fast math nor native-CPU flags are enabled.
`GRID_PYTHON` is optional for standalone builds; omitting it skips the independent
Python parity gate and must not be reported as passing that gate. The reference
uses the actual existing Python financial functions, reducer and simulator.
Comparisons cover every quantized financial field and complete allocation state.
Standalone synthetic integration tests use temporary generated archives only.

## Decimal and integer contract

All command inputs/financial outputs are decimal strings. Original float32
observations remain exact binary rationals. No binary floating-point arithmetic
is used in financial operations or order/price comparisons.

* Ledger primitives use signed 64-bit currency units, with explicit decimal
  scales per operand and checked 128-bit intermediate products. A product that
  is not representable in the destination currency is rejected, never rounded.
  Scale counts are 0–18; overflow is a controlled error.
* The frozen initial research profile uses price scale 8, amount scale 4 and
  quote/cost scale 12. Thus every submitted `price * amount` is exactly
  representable. Quote fee quantum is `1e-8`. Other profiles are supported only
  when each actual posting is provably representable. A third-token conversion
  does not justify silently rounding its quote cost.
* Exact rational helpers are the slow order-placement path: minimum lots,
  geometric powers, fees, tick rounding and basket targets. OpenSSL BIGNUM
  values are capped at 131,072 bits (including checked intermediate products).
  Decimal input is bounded to 96 mantissa digits and exponent -96..96;
  geometric index is 1..10,000, with at most 256 requested levels. An arithmetic
  bound violation rejects the request; it never falls back to `double`.
* The implemented market precision mode is explicit constant tick size.
  Significant-digit venue precision is **not implemented**. The math CLI has
  explicit minima and known-unbounded maxima; the C++ `Market` API supports
  finite maxima. It must not be used to assert unknown historical venue limits.
* `BASE`/`QUOTE` identify the allocation currencies. Any other fee currency
  requires an explicit positive quote-per-token conversion. Fees preserve the
  existing forecast settlement-rounding reserve. A minimum BUY that becomes
  unsellable after fees is rejected; its amount is never increased to make it fit.
* Python planner operations explicitly using rounded arithmetic are reproduced
  with a bounded 96-significant-digit, half-even helper: grid powers/divisions,
  aggregate TP target intermediates, the minimum-BUY floor and weighted seed/lot
  prices. This preserves even Python's one-tick boundary effect after recurring
  division and multiplication; regression fixtures cover two discovered cases.
  Ledger postings, proportional cost flooring and fee quantization remain exact.
  This is a declared planner compatibility subset, not a general Decimal runtime;
  exponent/subnormal contexts outside its bounds are rejected. Other rational
  diagnostics may use `numerator/denominator`. Fixture success does not certify
  every possible 96-digit input or every unsupported venue precision mode.

## Source contract

Sources are nonempty regular files containing `<qffff` little-endian 24-byte
records: UTC-millisecond int64 and four IEEE754 float32 prices, OHLC. The decoder
checks positive minute-aligned timestamps no later than `253402300740000`,
positive finite prices and consistent OHLC ranges. The streaming reader checks
strict chronology, counts duplicate/out-of-order/invalid records, and records
gaps between accepted records without interpolation. There are at most 16
rejection examples in memory; every gap may stream separately. Its input buffer
is 98,304 bytes. No entire archive/candle series is retained.

Files open `O_RDONLY|O_NOFOLLOW|O_CLOEXEC|O_NONBLOCK`; nonregular files and final
symlinks are rejected. SHA-256 covers all source bytes, including invalid rows.
Size/device/inode/mtime/ctime on both descriptor and path are checked at EOF.
An expected SHA-256 mismatch or source mutation invalidates the entire result.
An early-stopped reader has `complete=false`; it is not a verified source scan.
Structural scanning intentionally continues after invalid records for diagnostics.
Study replay requires zero rejected records, nonempty per-source selected
coverage and completed expected-hash verification before publishing any result.

Finite-positive IEEE754 bit ordering avoids converting prices to rounded decimal
strings. Half-ULP is the larger adjacent finite-value spacing divided by two,
including powers of two, subnormals and maximum finite. It is an explicit
nearest-rounding collection assumption, not recovered original precision.
Strict BUY eligibility is `low + radius < limit`; strict SELL eligibility is
`high - radius > limit`. Touch assumptions compare exact centers inclusively.

The decoder deliberately represents all positive finite float32 values. This
extends the Python decoder's supported decimal domain: the smallest float32
subnormals expand to more than 96 Decimal digits and Python rejects them. They
are explicitly incompatible with the frozen financial price tick and cannot
become a valid order price. This is not blanket source-decoder parity.

## CLI v1

Success exits 0; invalid input/arithmetic/IO exits 2 with a JSON error on stderr.
Usage errors also exit 2. Stdout is deterministic JSON except `--gaps`, which
emits one gap JSON line per gap followed by the summary. Redirect outputs only
outside the immutable dataset, never into `/opt/datasets/`.

```text
grid_backtest scan SOURCE [EXPECTED_SHA256] [--gaps]
grid_backtest decode HEX_24_BYTES
grid_backtest math-grid ANCHOR SPACING PRICE_TICK INDEX...
grid_backtest math-buy PRICE <MARKET> <BUY_FEE> <SELL_FEE> SPACING NET_TARGET AVAILABLE_QUOTE
grid_backtest math-tp OWNED COST <MARKET> <SELL_FEE> SPACING NET_TARGET
grid_backtest trace TRACE_TSV
grid_backtest bars BAR_TSV
grid_backtest replay STUDY_TSV

MARKET = PRICE_TICK AMOUNT_TICK BUY_MIN_AMOUNT BUY_MIN_COST SELL_MIN_AMOUNT SELL_MIN_COST
FEE = CURRENCY RATE INCREMENT CONVERSION_OR_- MAX_SETTLEMENTS
```

`math-grid` returns `{"prices":[...]}`. BUY/TP objects use the corresponding
Python plan field names except `market_generation` (there is no exchange metadata
generation in this primitive CLI). Fee objects contain `currency` and `amount`.
All numeric plan fields are strings. The three unrounded diagnostic fields may
contain exact ratios as documented above. A scan is labeled `UNVERIFIED SOURCE`;
valid bytes and a filename do not prove spot identity, fees or provenance.

The versioned trace protocol starts `GRID_MODULE1_TRACE\t1`; the Python reference
drivers `grid.research.module1_lifecycle --export CASE` and
`grid.research.module1_bars --export CASE` export its exact configuration and
event/raw-bar rows. `trace` emits a complete transition JSON object per event;
`bars` emits complete financial state and execution counters per bar. Baseline
bar parity requires source tick compatibility and consecutive observations.

Study inputs start `GRID_MODULE1_REPLAY\t1`, with explicit `SOURCE`, `CONFIG`,
`FEE_RULE` and `REPLAY_CONFIG` rows. `scripts/module1_study.py` exports the frozen
experiment contract; `study_tests.py` is a self-contained small synthetic example.
Unknown/duplicate fields, unsupported research switches, mixed quotes, duplicate
markets and unrepresentable financial postings fail explicitly. The current
historical study supports separately funded USDT allocations and QUOTE fees only.
Every source is read and hashed in full even when the replay window selects a
subset. The merger retains one next bar per source and evaluates portfolio
decisions after the entire equal-timestamp group, without forward-filled marks.

On a missing-bar gap, the simulator advances the boundary clock before handling
due cancellations; old observations cannot become artificially fresh. After the
first completed post-gap bar it refreshes planning only for entirely known
modeled state, without clearing reconciliation or fee halts. New orders always
wait a future bar. This is declared synthetic scheduling, not exchange evidence.
The configured strategy freshness tolerance still applies: with a 60-second
maximum age, a boundary observation exactly 60 seconds old remains admissible
(`age <= maximum`). Portfolio marks independently require the current group.

Study JSON uses `grid-module1-replay-v1`. Raw and exit-adjusted NAV remain distinct;
unknown owned-asset marks remain null. Residual inventory remains owned, with its
raw value and zero estimated liquidation proceeds. The benchmark is the existing
funded buy-and-hold counterfactual, not evidence of an achievable LIMIT fill.
Actual full-history receipts and comparative returns are generated separately;
the executable itself makes no profitability or live-readiness claim.

Study configuration and trace files have a 1 MiB byte bound. Study JSON is capped
at 32 MiB, including every cycle event; exceeding a bound rejects the result
without truncating evidence. `nav_curve` retains the first observed group, the
first observed group of each new UTC day and the final observed group (at most
100,000 samples). Drawdown statistics use every observed group. Declared-window
gaps, source endpoint coverage and `terminal_assessment` remain separate, so the
last known curve point is not mistaken for an unobserved terminal valuation.
Utilization is explicitly sampled original-funding utilization; ordinary basket
SELLs, portfolio exits and allocation round advances have separate counters.
Each allocation/exchange/portfolio `metrics.planning_notice_counts` map retains
the existing bounded planner diagnostics, including budget, minimum-lot,
staleness and reconciliation-related notices. Exchange/portfolio counts sum
their allocations. These count emitted synthetic transition notices, not elapsed
inactive time or one observation per candle; repeated acknowledgements may emit
the same notice. Do not convert them into durations or eligibility probabilities.
