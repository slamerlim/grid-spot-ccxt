# I8 offline portfolio allocation risk reporting

Base: `a14d9404211c35fb8f165cfc69fb50638e41c99e`, branch
`i8/public-paper-hour`. This continues approved offline development after the
completed public hour. It addresses the missing allocation-level exposure,
unrealized PnL and drawdown in PRD §9. Synthetic histories and temporary ledgers
are used throughout; no exchange access, account access or deployment is included.
The original specifications, financial schema, dependency lock and GitHub Free
restriction remain unchanged.

## Report interface and valuation basis

The existing command needs no new option:

```sh
uv run --locked gridctl backtest --config /tmp/study/replay.json --portfolio /tmp/study/portfolio.json --output /tmp/study/new-result.json
```

The `grid-portfolio-research-v1` engine result now includes `allocation_risk`
with schema `grid-portfolio-allocation-risk-v1`. Each retained interval also
includes `allocation_risk_before_actions` and `allocation_risk`, paired with its
existing pre/post-action NAV. Scenario comparisons retain these fields inside
each complete portfolio result. The outer provenance envelope and other engine
schemas are unchanged. Fields are additive; deterministic result hashes change.

Each point pairs committed allocation state with the complete controller NAV
proof for that same timestamp. No independent allocation valuation resets the
shared FX capacity or invents additional executable depth. Owner, currency,
amount, timestamp and mark uniqueness must match. If consolidated NAV is unknown,
all allocation marks remain null, including an allocation that might be valued
in isolation. Its committed cash, fee balances, inventory quantity, position cost,
realized PnL and reservations remain visible. Earlier marks are not carried forward.

For each allocation, in the common reporting/quote currency:

- `net_nav` sums its existing cash, fee-asset and base-inventory marks. Reserved
  funds remain part of owned balances and are never added a second time.
- `inventory_net_exit_value` is the existing base mark after estimated exit
  costs and unpriced residual treatment. It is conditional on synthetic depth,
  fees, sell constraints and the bounded order budget, not a guaranteed fill.
- `inventory_unrealized_pnl_quote` subtracts remaining committed position cost
  from that net exit value. Actual entry fees already in cost and estimated exit
  costs must not disappear behind positive realized grid earnings.
- `nonreporting_asset_value` includes the marked base and owned fee assets.
  Current/maximum base quantities remain available even when marks are unknown.
- `committed_quote` is position cost plus outstanding quote reservation. It is
  a cost/reservation measure, distinct from marked exposure and spendable cash.
- The inventory mark's estimated fee and unpriced residual quantity remain
  explicit. A base-currency fee can reduce modeled sellable quantity rather
  than appear as a separate quote fee; these fields are not interchangeable.

`last_attribution` retains aggregate inventory value/unrealized PnL, committed
cost and realized PnL, allocated NAV, unallocated pool value and unassigned
liability value. Pool assets are not assigned to a convenient winning strategy.
Portfolio-level liabilities remain separate; the reporter does not guess which
allocation caused them. Current portfolio replay supplies no outside liabilities.
The known allocation/pool marks reconcile to consolidated NAV after liabilities.
Fee-asset FX changes and internal movements mean inventory PnL alone need not
equal the change in an allocation's total marked wealth.

## Internal transfers and drawdown

The portfolio controller can move owned tokens from allocations or pools to
explicit conversion allocations during exit. A raw ownership-value decrease
would falsely label that transfer as a loss at its source. A zero-funded
conversion allocation would appear to earn its incoming principal.

The reporter reads only the committed `portfolio_advance` request's journal.
It verifies the accepted operation, scoped owners and exact conservation by
venue/account/currency across allocation and unallocated-pool balance movements.
Cost-basis changes are distinct from cash movement; an unexpected realized
posting or unrecognized owner refuses the report. No synthetic fill is created.
The financial writer's transactions and records are not modified.
Transfer sums and mark products use the existing exact Decimal context: exceeding
its supported precision refuses the run. Rounded cancellation of very large
opposite postings cannot hide a small nonzero currency imbalance.

Transfer receipts retain request ID, source/recipient allocation (or null for an
unallocated pool), currency, signed amount and its same-time reporting value.
Non-reporting currency uses the explicit fresh direct bid conversion; aggregate
incoming quantity is checked once against capacity, rather than counting both
sides of an internal transfer as two executions. This is a reporting convention,
not a currency sale. Missing/stale/insufficient conversion evidence makes that
flow's value unknown. Later data cannot retroactively price it.

Define cumulative net valued internal inflow as `F(t)`, and adjusted allocation
wealth as `W(t) = allocation_NAV(t) - F(t)`. Initial wealth is that allocation's
share of the cycle baseline. The report records:

- Net PnL after internal flows: `W(t) - initial_wealth`.
- PnL fraction: that PnL divided by positive initial wealth. This additive-flow
  convention is **not** a time-weighted or money-weighted investment return.
- Observed drawdown: the largest difference between a prior adjusted-wealth
  peak and a subsequent known `W(t)`, plus its fraction of that prior peak.
  Both pre-action and post-action observations are processed in that order.
  Unknown observations are counted and keep `drawdown_complete=false`; no gap
  or intrabar path is interpolated. An unknown historical flow permanently
  withholds adjusted wealth and subsequent adjusted PnL.

An allocation starting with zero funds has no initial-capital denominator:
percentage return and percentage drawdown are null. Its absolute exposure,
absolute observed drawdown and transfer-adjusted PnL remain reported where known.
Transfer-adjusted wealth may be negative; it is not a spendable balance.

Mean committed capital and inventory time use post-action samples for every
simulated minute. Mean marked exposure uses only known post-action samples and
reports that denominator explicitly. Peaks include both observations; null marks
are not zero exposure. Metrics stop at actual cycle completion or unfinished EOF.
Later bars are still validated but cannot change completed performance.

## Bounds and compatibility

One bounded tracker is kept per allocation. Retained interval points are limited
to 20,000 across the full declared roster and both pre/post snapshots. The
conservative bound is checked before reading history or creating a ledger, using
the declared full range; a possible earlier exit does not waive it. Increase the
report interval or shorten the study to satisfy this explicit bound. Streaming
statistics still observe every simulated minute.

Transfer journal reads and total retained movement receipts are each capped at
10,000 rows. Existing input, venue, ledger and 32 MiB encoded-result bounds remain.
These limits bound logical work/storage and do not establish OS memory or CPU
quotas. Additional reporting reads the existing writer and existing NAV proofs;
it neither changes the execution model nor grants order authority.

The plain basket replay and financial serialization remain unchanged. Existing
portfolio result fields are preserved; consumers can use the additive allocation
risk schema without treating it as a new trading policy. Rollback is an ordinary
revert of this reporting increment, with no database migration or dependency
downgrade. Preserve previously published result bytes and their source revisions.

## Validation and outstanding scope

Focused evidence covers realized gains alongside larger unrealized losses,
reservation double-counting, fee-in-base/quote/token accounting, trigger peaks,
unknown depth, multiple allocations and unallocated cash, early completion,
conversion transfers from both allocation and pool, unknown transfer valuation,
nonconserving journal corruption, mismatched NAV ownership and report bounds.

In an invented single-allocation run, realized earnings of 10 coexist with
remaining inventory cost 110 and net inventory value 50. The report retains
unrealized PnL of -60 and total allocation PnL of -50; marked NAV is 950 from
initial funding 1,000. Observed absolute drawdown is 60 from the prior 1,010 peak.
In the conversion fixture, 10 tokens move from one allocation and 5 from its
unallocated pool into a zero-funded conversion allocation. The recipient's
15-unit final cash is entirely transferred principal, so its adjusted PnL is
zero. Source PnL remains 50 instead of misclassifying the outgoing tokens as a loss.

The committed base engine was independently loaded from Git for the loss,
completed-exit and internal-conversion fixtures. Every pre-existing result field
matched, including orders, fills, cycle states, events/trace, benchmarks and
intervals after removing only the new risk fields and result checksum. The
comparison receipt is `/tmp/grid-i8-risk-compatibility-20261104.json`, outside Git.
This is finite compatibility evidence, not proof across every possible input.
The later exact-arithmetic guard rejects the reproduced precision-loss fault;
ordinary conversion postings still pass. No financial schema or policy changed.

Final local validation on 2026-11-04 UTC:

| Check | Observed result |
|---|---|
| Python 3.12.16 full suite | 1,009 passed in 265.30 seconds; no failures/skips |
| Python 3.13.18 full suite | 1,009 passed in 274.32 seconds; no failures/skips |
| Ruff lint and format | Passed; 235 formatted Python files |
| Strict mypy | Passed; 178 source/test files |
| Bandit over application/scripts | Passed |
| Tracked-file secret scan | Passed; existing baseline unchanged |
| Locked dependencies and advisory audit | Unchanged 66-package graph; no known vulnerabilities reported |
| Wheel/source distribution | 83 application modules and 239 required source/test/document/manifest files byte-verified |
| Original specifications and attachments | All four pairs retain their original SHA-256 fingerprints |

Both final full-suite receipts confirm unchanged application-source SHA-256
`ef5b0379fcf4dbc11528f6cdb227312a4dbf62cda2e841c3b524bbc70d2bde07`.
Receipts and logs are outside Git under `/tmp/grid-i8-risk-final-checks/`, with
final stems `3.12-1793832213026869532` and `3.13-1793832213015743524`.
Earlier full-suite receipts there precede the exact-arithmetic fix and are
intermediate evidence. The precision-loss test failed before that fix and passed
afterward; it is included among the 19 new cases in the final full suites.

Tests ran as `python -m pytest -q` through locked `uv` environments
`/tmp/grid-i2-py312` and `/tmp/grid-i2-py313`, selecting Python 3.12.16 and 3.13.18
respectively. Other checks use README's verification commands with those
interpreter/environment selections; the existing approved SQLite gate remains
required. Package artifacts are under `/tmp/grid-i8-risk-dist/`, outside Git.
No generated ledger/history/journal payloads are included in the wheel.

Delivery uses an ordinary scoped commit on the existing branch; no PR is created
under that established publication workflow. Network activity for this increment
is limited to Git publication/access checks and dependency advisory services;
no exchange endpoint was probed. Hosted CI remains pending under the unchanged
known account lock. Publication uses GitHub Free and `[skip ci]` without retrying
that lock. Continue authorized offline development; hold live activation.

Historical FX/rule/fee changes, point-in-time rosters and delistings, continuous
multi-cycle studies, independent-lot study integration and the full storage/
deployment resource benchmark matrix remain incomplete. This report extension
does not complete I8's observed 48-hour or first-venue evidence gates, authorize
I9, establish execution quality or imply a profitable strategy.
