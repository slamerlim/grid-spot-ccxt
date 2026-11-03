# Legacy review — provenance summary

This file summarizes the earlier assistant audit in the same conversation. It
is **not** a verbatim copy of the missing `Pasted markdown.md` referenced by the
PRD, and it is **not** a new inspection or certification of legacy source/data.
The four attached documents contain the PRD, ADR, acceptance plan and master
prompt; none supplies that original legacy-review file. Preserve this distinction
when citing evidence.

## Findings reported by the previous chat audit

- A 963-line C++ simulator under `/opt/module_1_multi` combined parsing,
  averaging-down strategy, accounting and reports. It was not a live CCXT bot.
  The audit reported a syntax-only check with warnings, not a successful trading
  execution. It did not execute the simulator because normal execution writes
  CSV outputs.
- The simulated strategy accumulated successive lower buys with optional larger
  sizing and one basket take-profit. It had no live order lifecycle, durable
  financial ledger, partial-fill/cancel recovery or CCXT integration.
- Quote balances began at zero and could become negative; currencies could be
  summed across incompatible quotes. Fees were hard-coded. Reported defects
  also included stale basket exits/restart anchors, disabled invalid-price
  rejection, local-time date filtering and dropped final report buffers.
- The bundled simulator expected dated 16-byte event records in nested paths;
  the historical archive used flat 24-byte OHLC records. It was not directly
  compatible input. Legacy outputs are not a correctness oracle.
- The source review recommendation was a new production implementation,
  retaining legacy code only as research reference and data only after
  validation. This aligns with the subsequently supplied PRD; legacy behavior
  does not supersede the stricter minimum-lot, limit-only, finite-capital rules.

## Historical archive evidence, dated snapshot only

The earlier audit reported **1,785 completed files**, about **28.36 GiB** and
**1,269,018,424 records** under the documented 24-byte layout. The directory
changed during that inspection, so these numbers are not a current inventory.

| Filename venue classification | Files in the reported snapshot |
| --- | ---: |
| Aster | 72 |
| Bequant | 150 |
| BigONE | 929 |
| Binance | 633 |
| Bitstamp | 1 |

These names do not independently establish market identity, spot eligibility or
target-venue relevance. Reported format: an int64 millisecond Unix timestamp and
four float32 OHLC values, with no volume or order book. Filename convention was
`<exchange>_<QUOTE>_<BASE>.bin`. I5 must independently establish exact byte order,
identity, provenance and numerical suitability before importing any data.

The prior audit sampled **14,647,976 candles across 1,769 files**, then fully
scanned four files flagged for zeros. All completed files in its final metadata
snapshot had sizes divisible by 24. It reported gaps in 1,100 sampled files,
very short histories and a roughly 468-day gap in one short file. Sampled
absence of NaN, invalid OHLC ordering or timestamp disorder is not a whole-archive
guarantee. Some gaps may represent no-trade periods; provenance is needed to
distinguish collection gaps.

| Fully scanned file reported in the prior audit | Total records | All-zero OHLC records |
| --- | ---: | ---: |
| `bigone_USDT_BOX.bin` | 656,630 | 153,968 |
| `bigone_USDT_JASMY3L.bin` | 1,182,059 | 407,385 |
| `bigone_USDT_JASMY3S.bin` | 1,182,029 | 407,467 |
| `bigone_USDT_SNX.bin` | 1,182,040 | 840,527 |
| Total | 4,202,758 | **1,809,347** |

The reported 626 filenames ending in `3L`, `3S`, `5L` or `5S` are screening
candidates for leveraged-token exclusion, not verified instrument metadata.
Float32 prices lose precision relative to exact venue ticks. An OHLC candle
cannot establish intraminute fill order, queue priority or volume capacity.

## Consequences for the new repository

Do not copy legacy executables, profiles, datasets or simulator accounting into
this repository. I0 leaves `/opt/module_1_multi` and `/opt/datasets` untouched.
I5 will read immutable sources, create checksum/provenance manifests and report
invalid records/exclusions in separate outputs. No source repair, interpolation
or conversion of candles into purported real ticks is authorized. Preserve raw
bytes; any optional derived format must retain provenance and demonstrate an
end-to-end measured benefit.

Use these reported defects to design future Q03, Q10 and Q14–Q16 fixtures. A
matching legacy result, a sampled check or this document does not establish
that those future tests have passed.
