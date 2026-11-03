# I5 review proposal — historical validation and conservative Tier-A replay

**Status: approved on 2026-11-04.** The owner explicitly selected "Approve I5
with local validation" for this plan. Approval ends after I5 review. Use local
validation and GitHub Free only. Hosted CI remains pending.

## Proposed authorization

Implement a read-only historical importer, deterministic conservative OHLC replay
and bounded sample benchmarks. Inspect original legacy binary/schema evidence and
small historical samples under `/opt/module_1_multi` and `/opt/datasets` without
editing, deleting or converting originals in place. Generated manifests, rejected
record reports, replay results and optional derived samples stay in explicit
temporary/output directories outside Git. No network exchange calls, account
access, real/sandbox orders, transfers, services or deployment are included.

## Concrete implementation

1. Confirm the legacy 24-byte record layout and byte order against source and
   sample evidence before decoding: signed int64 millisecond timestamp and four
   float32 OHLC fields. Do not infer proven provenance from filenames. Record
   source path, file size, schema/endian choice, hash, UTC coverage, market mapping,
   configuration and code revision. Separate inspected evidence from unknowns.
2. Add a bounded read-only `struct`/`mmap` importer. Reject malformed length,
   nonfinite/nonpositive values, impossible OHLC, misaligned or nonmonotonic UTC
   minutes, duplicates and unsupported market identity. Preserve raw float32
   representation/provenance, and document conversion uncertainty; do not invent
   exchange price precision. Quarantine in derived reports, never source edits.
   Detect gaps explicitly without interpolation or invented fills.
3. Merge validated streams chronologically with deterministic tie ordering and
   bounded per-market state. Keep finite dedicated cash, fee currencies,
   reservations, owned inventory and the existing I1 strategy contracts. Use a
   separate simulated execution adapter; OHLC observations are not actual trades.
   Synthetic fills carry explicit execution-model provenance.
4. Implement conservative bar semantics: only already eligible orders can fill;
   a high/low touch does not guarantee execution. Ambiguous two-sided bars cannot
   create an assumed profitable same-bar round trip. Compare conservative and
   explicitly labeled alternative bounds, with no maker queue, volume-capacity
   or L2 claims. Respect minimum lots, fees and finite cash in every model.
5. Produce reproducible reports: realized/unrealized PnL, NAV/drawdown, fee burden,
   utilization/inventory, unfinished orders/dust and reporting intervals including
   the final partial interval. Never add unlike quote currencies. Compare cash
   and buy-and-hold on the same eligible observations, capital and fee assumptions.
   Profit factor remains undefined for zero gross losses/inadequate samples.
6. Benchmark raw sequential/mmap access against a bounded Arrow/Parquet sample
   where dependencies and storage permit. Add only necessary pinned packages to
   the existing lock. Use at most a 128 MiB derived sample for this first pass;
   no whole-archive conversion. Record machine/filesystem, cold/warm limitations,
   wall time and memory. Do not call an overlay test an NVMe benchmark. If actual
   NVMe is unavailable, report that part BLOCKED rather than fabricate evidence.

## Exit evidence

Add deterministic fixtures for Q14–Q16, plus finite-wallet/minimum/fee regression
checks and same input/config -> identical replay hashes. Exercise file-end flush,
gaps, zero candles, malformed bytes, chronology ties and same-bar ambiguity.
Real samples are read-only evidence, not committed test fixtures or proof of a
trading edge. Validation must distinguish accepted/rejected/unknown provenance.

Run both supported Python suites, lint/types, security scans, build and bounded
sample benchmarks locally. Publish code, design/evidence, reproducibility commands,
remaining gaps and an I6 proposal; stop at review. No requirement for paid GitHub
features or a hosted-CI retry is introduced.

Rollback: scoped code/docs/lock revert; generated samples are disposable derived
artifacts, originals remain unchanged. No financial deployment is migrated.
Target-venue trade/L2 evidence, actual account fees, paper soak and live canary
approval remain later gates. No profitability or immediate LIMIT exit guarantee.
