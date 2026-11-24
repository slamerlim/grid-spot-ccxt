# HTX and Gate: complete archive integrity audit

## Active scope: HTX, 2026-11-12

The owner stopped Gate recovery work and selected HTX for now. Gate downloads
already collected remain retained; no further Gate requests or gap investigations
are part of this batch. The original full audit below remains unchanged evidence.

The active collection is
`/opt/grid-runs/historical-collection-20261112/htx-focus/`.
Use its `requirements.json` and `monthly-manifest-reviewed.json`, not the earlier
combined collection manifest. Detailed results are in `coverage-reviewed.json`,
`independent-validation-final.jsonl`, `independent-trade-validation-final.json`,
`publisher-checksum-validation.json` and `acquisition-report.json` there.

**Directly verified:** the current official HTX archive is published under
[HTX Vision](https://www.htx.com/vision/), using
`/data/historical_data/spot/daily/klines/ETH-BTC/1m/` and the corresponding
`trades/ETH-BTC/` directory. Its filenames and CSV headers differ from the older
[HTX archive documentation](https://github.com/hbdmapi/huobi_public_data/blob/master/README.md).
Four candle ZIPs and one ETH/BTC trade ZIP for the filename date `2026-08-31`
were collected: **61,473 compressed bytes**, 5,760 candle records and 85 trades.
All five original ZIPs match the exchange-published SHA-256 sidecars.

The four daily candle files actually span **2026-08-30 16:00 through
2026-08-31 16:00 UTC, end exclusive**: the filename labels a UTC+08 day.
All four originals were out of timestamp order. Separate chronological copies
retain every row and decimal value; no source row was removed or fabricated.
The original files, initial parser refusals and subsequent validation remain
retained. Version 3 understands the current header and validates instrument IDs,
the source window, prices, ordering and duplicates. New volume fields remain
preserved without guessing their base/quote units.

**Derived from validated source data:** six absent candle timestamps can be
supplied separately: ETH/BTC (3), ETH/USDT (1), USDC/USDT (1), BTC/USDC (1).
This is an external overlay inventory; neither the datasets nor the engines
have been changed to consume it.

| Representative HTX market | UTC start | UTC end, exclusive | Original candles | Additional candles | Remaining absent timestamps |
| --- | --- | --- | ---: | ---: | ---: |
| BTC/USDT | 2022-08-04 12:06 | 2026-11-04 02:08 | 2,235,722 | 0 | 0 |
| ETH/BTC | 2022-08-04 12:06 | 2026-11-04 02:08 | 2,235,719 | 3 | 0 |
| ETH/USDC | 2022-08-04 14:04 | 2026-11-04 02:09 | 2,235,605 | 0 | 0 |

The three recovered ETH/BTC minutes are **2026-08-31 07:15, 07:16 and 07:18 UTC**.
Each official candle is flat at `0.031286`, with all reported volume fields zero;
the downloaded trade source contains no trades in these minutes. Across its
whole daily window the source records 85 trades in only 38 distinct minutes.
This is `no_trades_observed_in_source`, not proof of outage, a missed fill or
a new tradable price. Source-feed completeness itself is not certified.

**Directly verified conflict:** five ETH/USDT candles disagree with the legacy
archive beyond binary32 rounding: **2026-08-31 07:04, 07:07, 07:09, 07:12 and
07:22 UTC**. Exact prices are retained in `independent-validation-final.jsonl`.
The coverage helper excludes conflicted source timestamps; it never silently
replaces legacy observations. Selecting a canonical source is still a review item.

| Quote conversion for the representative runs | Completed-candle candidates / required minute timestamps | Direct | Derived | Unavailable |
| --- | ---: | ---: | ---: | ---: |
| BTC → USDT for ETH/BTC | 2,235,721 / 2,235,722 = 99.999955272% | 2,235,721 | 0 | 1 |
| USDC → USDT for ETH/USDC | 2,235,605 / 2,235,605 = 100.000000000% | 2,235,605 | 0 | 0 |

The BTC start at **2022-08-04 12:06 UTC** lacks the preceding completed candle;
using its own unfinished candle would introduce lookahead. Before collection,
one USDC minute needed `BTC/USDT ÷ BTC/USDC` at the same completed minute.
The recovered direct USDC/USDT candle now supplies that timestamp. Exact routes,
source hashes, monthly counts and time alignment remain in the coverage report.
Only USDT itself has a fixed 1:1 USDT valuation.

**Unavailable:** certified fresh-trade valuation coverage for the full histories,
historical spreads/depth/queue positions and defensible exact fills. Candle
occupancy and the conversion percentages above are not those measures. Legacy
prices remain binary32 and have no saved volume or collection provenance.
Decimal parsing preserves newly acquired values but cannot recover precision
already lost in old files. No stale-price carry or interpolation has been enabled.
HTX's current catalogue lists historical order books; their suitability and
continuity were **not investigated** in this bounded gap check.

**Implementation and validation:** added `scripts/historical_collect.py`,
`scripts/historical_coverage.py`, and `tests/research/test_historical_collect.py`;
updated this report and `AGENTS.md`. CCXT remains 4.5.91. The standalone collector
permits only exact public archive GET/HEAD URLs, bounded sizes/extraction/retries,
no redirects, no credentials and no private endpoints. Published originals are
immutable and resume checks hashes. The current store admits one selected source
archive per market/month/kind; it deliberately does not implement a general daily
archive merger. Monthly status records refer to the selected source, with exact
local counts and covered source windows alongside them.

46 focused offline tests pass; Ruff and targeted mypy checks pass. Protection
results and precise storage/file inventories are recorded in
`acquisition-report.json` and the parent `protection-after.json`. The 65 GB audit
was reused with file-identity checks, not repeated. Batch 2 accounting, metrics,
dashboard integration and further historical replays remain outside this batch.

Completed 2026-11-11T19:41:38.169541+00:00 (UTC). Actual dataset root: `/opt/datasets/crypto_1m`; `/datasets` does not exist on this host.

**All 3,230 files were fully read and SHA-256 hashed: 2,721,157,870 24-byte candles, 65,307,788,880 source bytes.** No source was excluded because of its quote currency, suspicious name or data quality. Both complete source rosters and every source identity remained unchanged. Original datasets were never edited.

The key finding is a large difference in temporal completeness. HTX is almost continuous within each file’s own first/last timestamps. Gate has extensive missing minute timestamps. Clean binary structure does not make those missing intervals complete historical market evidence.

## Overall results

| Venue | Files | Candles | Source bytes | Contiguous files | Files with gaps | Structurally invalid files | Gap intervals | Missing minute timestamps | Weighted coverage |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| HTX | 625 | 763,107,522 | 18,314,580,528 | 394 | 231 | 0 | 427 | 449 | 99.999941% |
| GATE | 2,605 | 1,958,050,348 | 46,993,208,352 | 307 | 2,298 | 0 | 472,086,368 | 1,593,247,054 | 55.136197% |

Coverage = observed distinct minute timestamps divided by the sum of each file’s own endpoint span. Missing minutes are **pair-minutes summed across files**, not a continuous period of exchange downtime. This does not infer missing history before the first or after the last observed candle.

The full scans found **zero** partial records, empty files, out-of-range/off-grid/future timestamps, duplicate timestamps, backwards timestamps, nonfinite/nonpositive prices or impossible OHLC bounds.

## Coverage by quote currency

| Venue | Quote | Files | Candles | Missing minutes | Gap intervals | Weighted coverage | Flat OHLC candles |
| --- | --- | --- | --- | --- | --- | --- | --- |
| gate | BTC | 24 | 30,551,072 | 51,946,999 | 10,691,746 | 37.032468% | 72.226% |
| gate | ETH | 39 | 46,108,323 | 68,460,061 | 17,072,619 | 40.245242% | 76.843% |
| gate | GUSD | 9 | 2,526,125 | 2,891,191 | 848,106 | 46.630564% | 74.008% |
| gate | RLUSD | 3 | 46,919 | 0 | 0 | 100.000000% | 19.154% |
| gate | USD | 11 | 176,704 | 0 | 0 | 100.000000% | 68.177% |
| gate | USD1 | 14 | 3,939,655 | 4,120,309 | 1,121,284 | 48.879313% | 56.266% |
| gate | USDC | 66 | 51,230,316 | 37,602,449 | 4,883,633 | 57.670518% | 46.415% |
| gate | USDT | 2,439 | 1,823,471,234 | 1,428,226,045 | 437,468,980 | 56.077521% | 53.558% |
| htx | BTC | 7 | 15,649,710 | 5 | 4 | 99.999968% | 90.098% |
| htx | ETH | 3 | 4,079,046 | 4 | 3 | 99.999902% | 88.662% |
| htx | KGST | 1 | 262,822 | 3 | 3 | 99.998859% | 42.490% |
| htx | USDC | 3 | 4,734,056 | 1 | 1 | 99.999979% | 55.774% |
| htx | USDD | 1 | 658,921 | 1 | 1 | 99.999848% | 5.850% |
| htx | USDT | 610 | 737,722,967 | 435 | 415 | 99.999941% | 40.709% |

A flat candle has open = high = low = close and positive finite prices. It can be valid; it is not evidence of available liquidity or proof of a collector defect. Sparse quote histories and many flat candles are consistent with several explanations, including illiquid/no-trade minutes and incomplete collection. These files contain no volume or collection provenance, so the cause cannot be determined from this audit.

## Most consequential gaps

Largest individual missing spans (start inclusive, end exclusive; all timestamps UTC):

| Venue:market | Missing minutes | Missing days | Missing interval start | Missing interval end |
| --- | --- | --- | --- | --- |
| gate:BTC/USDC | 1,011,202 | 702.224 | 2021-08-01T00:00:00+00:00 | 2023-07-04T05:22:00+00:00 |
| gate:TUSD/USDT | 990,161 | 687.612 | 2021-07-31T23:10:00+00:00 | 2023-06-19T13:51:00+00:00 |
| gate:FAST/USDT | 767,758 | 533.165 | 2022-12-26T06:02:00+00:00 | 2024-06-11T10:00:00+00:00 |
| gate:DEFIBOX/USDT | 603,323 | 418.974 | 2025-06-05T06:03:00+00:00 | 2026-07-29T05:26:00+00:00 |
| gate:GM/USDT | 423,965 | 294.420 | 2024-07-08T23:55:00+00:00 | 2025-04-29T10:00:00+00:00 |
| gate:CLEAR/USDT | 335,551 | 233.022 | 2024-06-02T00:00:00+00:00 | 2025-01-21T00:31:00+00:00 |
| gate:D/USDT | 313,922 | 218.001 | 2024-06-02T00:00:00+00:00 | 2025-01-06T00:02:00+00:00 |
| gate:IAG/USDT | 263,723 | 183.141 | 2022-08-02T01:42:00+00:00 | 2023-02-01T05:05:00+00:00 |
| gate:ITA/USDT | 165,615 | 115.010 | 2022-12-14T03:26:00+00:00 | 2023-04-08T03:41:00+00:00 |
| gate:GALO/USDT | 159,349 | 110.659 | 2022-12-18T11:50:00+00:00 | 2023-04-08T03:39:00+00:00 |
| gate:VADER/USDT | 156,069 | 108.381 | 2024-09-26T02:51:00+00:00 | 2025-01-12T12:00:00+00:00 |
| gate:CRE8/USDT | 142,618 | 99.040 | 2022-11-16T10:05:00+00:00 | 2023-02-23T11:03:00+00:00 |

Largest cumulative missing timestamp counts within a file:

| Venue:market | Observed candles | Missing minutes | Coverage | Gap intervals |
| --- | --- | --- | --- | --- |
| gate:TUSD/USDT | 702,279 | 3,475,965 | 16.807994% | 233,118 |
| gate:DAI/USDT | 879,437 | 3,466,658 | 20.235108% | 395,435 |
| gate:ZSC/USDT | 1,319,350 | 3,330,534 | 28.373826% | 513,175 |
| gate:ELF/ETH | 1,261,968 | 3,326,393 | 27.503677% | 600,817 |
| gate:WAVES/BTC | 1,207,708 | 3,302,669 | 26.776210% | 544,844 |
| gate:IOTA/BTC | 1,380,416 | 3,268,979 | 29.690229% | 524,112 |
| gate:QTUM/BTC | 1,397,036 | 3,252,489 | 30.046854% | 557,295 |
| gate:CVC/ETH | 1,398,312 | 3,251,213 | 30.074298% | 486,108 |
| gate:LINK/ETH | 1,398,328 | 3,251,056 | 30.075554% | 486,968 |
| gate:XRP/BTC | 1,424,742 | 3,224,784 | 30.642736% | 531,283 |
| gate:LTC/BTC | 1,441,188 | 3,208,338 | 30.996450% | 532,444 |
| gate:ADA/BTC | 1,452,372 | 3,197,152 | 31.237004% | 509,817 |

HTX files with any missing timestamps are fully enumerated in `files.csv`; the largest HTX gaps are:

| Market | Total missing minutes | Gap intervals | Largest gap, minutes | Largest gap start UTC |
| --- | --- | --- | --- | --- |
| SUPER/USDT | 4 | 2 | 3 | 2026-08-31T06:54:00+00:00 |
| ETH/BTC | 3 | 2 | 2 | 2026-08-31T07:15:00+00:00 |
| TRX/ETH | 3 | 2 | 2 | 2026-08-31T07:05:00+00:00 |
| ANIME/USDT | 4 | 3 | 2 | 2026-08-31T07:05:00+00:00 |
| AXS/USDT | 2 | 1 | 2 | 2026-08-31T07:24:00+00:00 |
| BOME/USDT | 9 | 8 | 2 | 2026-08-31T07:07:00+00:00 |
| EIGEN/USDT | 5 | 4 | 2 | 2026-08-31T07:15:00+00:00 |
| EURQ/USDT | 3 | 2 | 2 | 2026-08-31T07:15:00+00:00 |
| FUN/USDT | 3 | 2 | 2 | 2026-08-31T07:15:00+00:00 |
| LAT/USDT | 3 | 2 | 2 | 2026-08-31T07:15:00+00:00 |

HTX's 449 missing pair-minutes occur on only three UTC dates: **423 on
2026-08-31, 11 on 2026-10-27 and 15 on 2026-10-29**. The minute
2026-08-31 07:24 UTC is absent from 120 files. This clustering is evidence of a
shared interruption in the recorded histories; it does not identify whether
the cause was the exchange, the collector or another upstream source.
The longest HTX gap is three minutes. Every HTX interval is available together
in the small [all-HTX-gaps CSV](/opt/grid-runs/htx-gate-integrity-20261112-compact/htx_all_gaps.csv)
and the [concentration summary](/opt/grid-runs/htx-gate-integrity-20261112-compact/htx_gap_concentration.json).

## History length and common periods

| Venue | Earliest first candle UTC | Latest last candle UTC | Files >=99% coverage | Files with a gap >=1 day | Shared endpoint interval across all files |
| --- | --- | --- | --- | --- | --- |
| HTX | 2022-08-04T12:05:00+00:00 | 2026-11-04T02:10:00+00:00 | 625 | 0 | None: file lifetimes do not overlap |
| GATE | 2018-01-01T00:00:00+00:00 | 2026-11-04T02:09:00+00:00 | 310 | 791 | None: file lifetimes do not overlap |

“Full overlap” in a selected-market backtest means common first/last boundaries; it does **not** mean every minute inside that interval exists. Do not concatenate separated periods or forward-fill missing candles as executable prices. The archive ends before the audit date; post-file-end history is unknown, not counted as a detected internal gap.

## What this evidence is sufficient for

- Complete structural admission decisions for these exact hashed source versions, with exact timestamp coverage and explicit gap accounting.
- Reproducible OHLC scenario comparisons over selected windows when the engine skips missing observations and reports stale/incomplete valuation honestly.
- Identifying datasets/windows that require rejection or a declared missing-data assumption before any strategy comparison.

It remains insufficient for proving executable profitability. The files have no trade volume, bid/ask spread, order-book depth, maker queue position, trade-by-trade ordering, private order lifecycle, historical fees, verified historical precision/minimum rules or collection provenance. Float32 OHLC also cannot restore the original price precision. HTX’s excellent timestamp continuity does not supply those missing fields, and a Gate gap does not prove that trading stopped.

The existing name heuristic flags 448 Gate and 3 HTX files as possibly leveraged. These counts are **not** verified market classifications: it also flags ordinary `JUP` names because they end in `UP`. The audit retains them all; historical spot identity must be established separately. No UI classifier was changed by this audit.

## Full artifacts and exact-gap access

- [Per-file CSV, every market](/opt/grid-runs/htx-gate-integrity-20261112-compact/files.csv): market identity, SHA-256, dates, errors, missing-minute totals, biggest gap, flat candles and exact gap-artifact location/hash. The detailed JSONL additionally includes OS file identities and error examples.
- [Per-file detailed JSONL](/opt/grid-runs/htx-gate-integrity-20261112-compact/files.jsonl), [machine-readable summary](/opt/grid-runs/htx-gate-integrity-20261112-compact/summary.json), [quote summary CSV](/opt/grid-runs/htx-gate-integrity-20261112-compact/venue_quote_summary.csv).
- [Independent verification](/opt/grid-runs/htx-gate-integrity-20261112-compact/independent_verification.json), [original inventory](/opt/grid-runs/htx-gate-integrity-20261112-compact/inventory_before.json), [final inventory](/opt/grid-runs/htx-gate-integrity-20261112-compact/inventory_after.json).

Exact ranges are stored per file. For the first 501 checkpointed files they remain original readable CSV. Subsequent files use a compact headerless binary record of two little-endian signed int64 values, `<qq`: the **previous observed timestamp** and the **next observed timestamp**, in UTC milliseconds. The missing interval is `[previous + 60000, next)`; missing minutes = `(next - previous) / 60000 - 1`. No gaps were sampled, collapsed or dropped. Per-file metadata identifies the format, absolute location and SHA-256.

Export one compact gap file to readable CSV without loading it all into RAM:

```bash
cd /opt/grid
/opt/module1_n/.venv/bin/python -m scripts.audit_archive_integrity \
  --export-gaps /opt/grid-runs/htx-gate-integrity-20261112-compact/gaps/htx_USDT_SUPER.bin.gaps.bin \
  --csv-output /opt/grid-runs/htx-super-gaps-export.csv
```

The destination must be new. CSV includes market identity, previous/next timestamps, UTC missing-interval boundaries and missing-minute counts. Original archive files are never changed.

## Method, checkpoint and validation

Each source was checked as little-endian `<qffff`: int64 UTC milliseconds followed by four float32 open/high/low/close fields. Every row was checked for minute alignment, chronology, duplicate timestamps, finite positive prices and low ≤ open/close ≤ high. All source bytes were hashed; device/inode/size/mtime/ctime and the complete roster were checked before and after. Normal scanning uses 262,144-record blocks; rare malformed/disordered timestamp cases use a bounded temporary on-disk index.

The first sequential scan spent most CPU time formatting tens of millions of repeated CSV fields and UTC strings. It was briefly SIGSTOP-paused for the separate UI benchmarks, then checkpointed after 501 complete sources. Its immutable source snapshot and all complete/partial outputs remain at `/opt/grid-runs/htx-gate-integrity-20261112`. Complete JSONL records were reused only after exact inventory/source-identity equality and hashing each retained gap artifact. The unfinished file was rescanned in the new directory; the old worker was deliberately terminated after continuation validation, so its exit 143 is not an audit failure.

The compact continuation completed in 265.987s wall / 229.423s CPU, peak RSS 107,428 KiB, using Python 3.12.3 and NumPy 2.5.4. These times include checkpoint verification but exclude the first scan’s ~562 seconds of CPU and intentional pauses. They are not the total elapsed time for the entire two-stage audit.

Validation includes seven focused tests (corrupt values/trailers, extreme timestamps, duplicate/disordered records, exact block-boundary gaps, binary-to-CSV byte parity, checkpoint partial-record handling and changed-source refusal), Ruff and strict mypy. Independent verification checks all result/source counts, endpoint missing-minute identities and exact-gap artifact record counts; selected sources also match the older standard-library row-by-row scanner.

Reproduce a fresh full audit in a new output directory:

```bash
cd /opt/grid
nice -n 15 ionice -c 3 /opt/module1_n/.venv/bin/python \
  -m scripts.audit_archive_integrity --root /opt/datasets/crypto_1m \
  --output /opt/grid-runs/NEW-HTX-GATE-AUDIT
```

Scanner SHA-256 used by the compact continuation: `4608870d2a61b3b76e2f1f52e67df6ff6b4d338bcd50ae1189c9c3b01ba0602b`. No new packages were installed; the audit uses the NumPy already present in the audited simulator’s environment.
