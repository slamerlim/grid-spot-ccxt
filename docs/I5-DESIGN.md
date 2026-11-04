# I5 — immutable history and conservative research

Later extension: `I8-RESEARCH-SCENARIOS.md` adds the fixed touch/SELL-priority
convention and a three-scenario comparison command. The original two conventions
and the historical I5 evidence retain their original scope below.

The owner approved I5 with local validation on 2026-11-04. This is an offline
research module, not a trading transport, new financial database or live NAV
engine. The I1 reducer, accounting/rounding rules and I2 ledger remain unchanged.
No exchange access is required. The imported specifications remain unchanged.

## Data contracts and provenance

`/opt/datasets.md` documents flat `<venue>_<QUOTE>_<BASE>.bin` files containing
24-byte little-endian `<qffff` records: signed int64 UTC milliseconds, then open,
high, low, close as float32. There is no volume. The referenced downloader at
`/opt/abn/dtdwn` is absent, so collection/repair history cannot be independently
established here. The actual C++ `module_1_multi/module.cpp` instead reads 16-byte
price/timestamp records; it is not evidence of the OHLC format. No legacy binary
or accounting code is imported or executed.

`Source` requires an explicit mapping, offset and count. Format, spot identity
and collection evidence are separate fields; a filename never supplies them.
Known suspicious leveraged-token suffixes block replay even if a caller asserts
spot. This conservative name screen is not a complete token-classification
service. Evidence references are operator assertions, not automatically verified
documents. Actual account fees and historical constraint changes remain unknown.

The validator opens regular files read-only, rejects final-component symlinks,
and reads only the requested records. Sequential reads are buffered; mmap maps
only the selected window. A file identity tuple (device, inode, length, mtime,
ctime) is checked across reading; replay checks it again and re-hashes the window
on its second streaming pass. Any detected change discards the result. Readers
require immutable sources: a hostile concurrent mmap truncation can terminate a
process before a Python exception; restart from an immutable snapshot, never
interpret a failed read as a completed validation.

Each manifest includes schema, original path/size, identity, selection, SHA-256,
accepted/rejected counts, rejection categories, up to 32 raw-byte examples,
first/last accepted UTC minute, missing valid minutes and provenance blockers.
Only a selection covering every byte gets a whole-file hash. Prefix hashes are
explicitly window hashes. No full-archive certification is implied.

Nonpositive/nonfinite/impossible OHLC, invalid UTC minutes, duplicate or backward
timestamps, unsupported Decimal magnitude/precision, malformed file length and
unknown identity are rejected/flagged. Gaps count missing valid observations;
quarantined rows are not repaired or interpolated. Replay requires an entirely
valid, contiguous selection with exactly `[start_ms, end_ms)` coverage for every
allocation. An empty or overrun selection is refused. Archive files never change.

Decoded prices use `Decimal.from_float` to preserve the exact supplied float32
value, plus raw bytes. They are not claimed to be original decimal exchange
prices. Replay checks whether a supplied market tick/significant-digit rule has
a price within the nearest-rounding half-ULP cell. An incompatible rule blocks
the run. Strict crossings clear the uncertainty cell, not merely the stored
central value. Nearest float32 rounding is an explicit research assumption;
collection provenance still has to establish whether it applies.

## Replay and financial ownership

Allocations receive finite quote and optional third-token balances from explicit
venue/account/currency capital pools. Their sum cannot exceed each pool. No
borrowing, automatic transfer or unlike-quote aggregation exists. Unallocated
funds are excluded equally from grid, cash and buy-and-hold comparisons. Third
token valuations must be explicit and consistent within an allocation; principal
currencies cannot also be fee wallets.

Validated streams merge with one cursor per allocation, ordered by UTC then
canonical full scope. Input tuple order does not affect results. The I1 reducer
owns minimum lot sizing, geometric ladder, fees, reservations, inventory cost
basis, one aggregate SELL and draining. Synthetic fills are typed `Fill` events;
accounting is not reimplemented. State compaction reuses the existing pure
checkpoint helper. Historical fact retention here is a rolling deterministic
event hash, not a durable trading ledger; replay restarts from immutable inputs.

The separate `Simulator` adapter assumes immediate acceptance of a proposed LIMIT
and full single-settlement fills at the limit price without improvement. Order
eligibility and final cancellation wait 1–60 bars. Cancel acknowledgments never
free funds; the modeled terminal report does. An old SELL can fill while cancel
is pending. These are synthetic latency/settlement assumptions, not exchange
capabilities. Partial fills, maker queue, volume limits, L2, outages and real
market impact cannot be inferred from these candles.

The first completed close supplies a declared synthetic bid/ask proxy. It cannot
fill an order in that candle. At each subsequent bar, eligible pre-existing
orders are snapshotted before processing fills. Commands emitted by those fills
wait later bars; the current close is used only for later order pricing. Boundary
cancellations use the preceding completed close. No OHLC price is presented as
an observed order book, and no missing live quote is rescued by a candle.

Two explicit execution scenarios are provided:

- `strict_skip_ambiguous`: low/high must cross beyond float32 uncertainty. If
  pending orders on both sides cross, neither side fills in that bar.
- `touch_buy_priority`: central-value touches may fill; if both sides touch,
  only pre-existing BUYs fill. Price priority then order ID breaks same-side ties.

Neither allows a newly generated profitable two-sided same-bar round trip.
These are sensitivity scenarios, **not mathematical lower/upper PnL bounds**.
A strict crossing still does not prove real execution. Insufficient queue,
volume or sequencing evidence cannot be repaired by choosing the optimistic case.
Unexpected reconciliation requirements or capacity failures abort publication.

## Reports and restart

Reports include config/source/revision/model hashes, pinned CCXT version,
chronology/event hashes, final state, realized PnL, open mark-to-close PnL, NAV,
bar-close drawdown, fees by currency and modeled quote value, turnover, mean
committed capital, inventory time, unfinished orders and a conservative
amount/minimum/fee screen for untradeable base. Mark-to-close NAV includes fee
wallets but excludes hypothetical liquidation fees/slippage. Inventory is not
force-sold. This is not the fresh, conservative executable NAV required by I7.
Dust screening can conservatively classify the full residual as untradeable
when a base-currency exit fee requires a smaller sell quantity.

Cash retains the same allocated starting assets. Buy-and-hold makes a declared
hypothetical purchase at the first completed close plus synthetic spread, rounded
to market precision, with the same fee rule, quote reserve, currency funds and
constraints. It spends the funded amount, not an infinite wallet. This immediate
benchmark purchase is an assumption, not a claimed historical limit fill.
Buy-and-hold remains marked, with no terminal forced sale or fabricated profit.

Profit factor is null without at least two realized SELL fills and positive
gross losses. Report intervals include the final unfinished interval, and the
last timestamp is the exact exclusive end. A result hash covers deterministic
content; measurements with wall time/RSS are separate. The code revision is a
required caller-supplied reference, not silently inferred from an arbitrary
working tree. For reproducibility use a clean commit, or record a source-tree
fingerprint when benchmarking an uncommitted implementation.

Publication fsyncs an exclusive temporary file, then atomically links it to a
new destination. Existing targets/symlinks are never overwritten. A failed or
killed run has no completed result name; a temporary sibling can remain after
SIGKILL and is not a completed report. Restart replays from the beginning and
publishes identical deterministic results, including the final interval. This
is process-crash behavior, not certified directory/power-loss durability.
Outputs are rejected inside any Git tree or original dataset/legacy source tree.

## Bounds and storage comparison

- Each input selection: at most 1,000,000 records (24 MB); 1–64 allocations;
  2–1,000,000 UTC minutes; at most 10,000 reporting intervals.
- One streaming cursor per allocation; financial records bounded by the existing
  per-allocation policy and compaction; at most 128 distinct notices. No parameter
  sweep or archive-sized Python collection exists.
- Benchmark: at most 131,072 records (3 MiB raw), 1–10 repetitions, 4,096-row
  columnar batches, and at most 128 MiB combined derived Arrow/Parquet sample.
  Fixed-width schemas and the much smaller row cap keep the first pass below
  that byte ceiling. All sample directories are unique and outside Git.
- Strict JSON config is capped at 1 MiB, rejects duplicate/unknown keys and uses
  the existing allowlisted Decimal/domain codec. `demo` emits a synthetic bundle
  for editing via Python `dataclasses.replace` and `config.bundle`/`publish`.

PyArrow 26.0.1 is an optional pinned research dependency included by the dev group
so both test environments exercise it. Runtime trading dependencies are unchanged.
[Arrow IPC](https://arrow.apache.org/docs/python/ipc.html) and
[Parquet](https://arrow.apache.org/docs/python/parquet/index.html) follow the
official APIs. The sample keeps int64 timestamps and four float32 columns;
Parquet uses Zstd, no dictionaries. Each reader reconstructs the original records
and must match the raw window SHA-256. Tests also compare financial replay across
both reconstructed formats.

Timing includes read/decode/repack/hash for every format, with three default
repetitions and a fixed disclosed order. Conversion time, sizes, wall/CPU times,
process I/O counters, lifetime peak RSS and Arrow pool peak are recorded. RSS
includes imports/conversion and is not per-format memory attribution. Validation
and conversion warm caches; cache eviction is not attempted. Hardware evidence
uses the source/output device numbers and sysfs NVMe controller/model links;
unavailable evidence is BLOCKED, never inferred from filesystem names.

Keep original raw files. A small warm scan cannot justify whole-archive conversion
or establish cold NVMe throughput, 1,000-market scaling, live/research isolation
or a trading edge. Those broader ADR/resource/venue gates remain open.
