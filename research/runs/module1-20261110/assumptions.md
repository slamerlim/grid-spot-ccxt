# Frozen ten-market exploratory assumptions

The exact scenario is `experiment_config.json`. Its fees, ticks, lot/notional
minima, spread, latency, fill capacity and exit guards are explicit hypothetical
research inputs declared before comparative returns. They are not observations
of historical venue rules. Source identity and collection provenance remain
unverified, even when raw bytes pass structural checks. Strict certified venue
performance is NOT COMPUTABLE from the available evidence.

The selected ten-market manifest was chosen on structural coverage and quality,
never strategy returns. A pre-return first candidate roster was rejected for
zero shared complete days; that rejected roster is preserved externally. The
second rule requires >=99% endpoint density, >=365 complete UTC days per source
and >=365 complete days shared by accepted sources. Source histories, gaps and
failed candidates remain intact. Surviving-listing/coverage selection can still
introduce survivorship bias; no point-in-time investment universe is certified.

The shared calendar interval is about 695 days, containing 478 shared complete
UTC days but only 66 consecutive complete UTC days. Do not join scattered days,
forward-fill missing bars or label this a gap-free year. Replay every actual
record in full individual histories and preserve the common interval's gaps.
Flat-price bars may reflect upstream collection behavior or inactivity; these
possibilities cannot be distinguished from this archive alone.

Price tick 1e-8 and quantity tick 1e-4 imply exact product quantum 1e-12. Quote
fees settle at 1e-8. Checked integer arithmetic must preserve those quantities
and reject overflow/unrepresentable values; source float32 values are decoded
exactly before side-aware tick rounding. This cannot recover discarded source
precision. Strict fill comparisons include the source half-ULP uncertainty.

The completed close is a bid proxy and close*(1+0.001) an ask proxy. No actual
book or volume is reconstructed. Orders become eligible on a later bar; cancel
finality also waits one bar. Primary fills consume full remaining eligible size
under each named OHLC convention. Partial fills and late-cancel behavior are
tested with invented events; their actual historical occurrence is unknowable.

All 10,000 USDT is funded at the common study start, including allocation cash
waiting for an observation. Per-venue capital is the sum of its fixed allocations;
no implicit transfers are allowed. Portfolio equity includes reserved cash once,
remaining inventory and modeled liquidation fees. A missing required current
mark makes NAV unknown and prevents TP triggers. Report sampled risk completeness.

Global +10% is enabled; exchange/symbol switches are disabled for the fixed
12-case comparison and tested independently in fixtures. Exits remain LIMIT-only,
with fixed trigger-price deviation guards and bounded reprices; incomplete
liquidation/dust is reported, never forced to complete. A cycle cap is a resource
bound, not permission to reset funds or ignore owned inventory.

Historical execution and representative out-of-sample profitability are not
proven by this study. This ten-market cohort does not satisfy a later request
for dozens of symbols. All negative, failed and incomplete experiments remain
in the trial registry; followups must use new declared case IDs.

For descriptive reporting after execution, monthly direction labels use only
the funded buy/hold benchmark's raw NAV: above +5% is rising, below -5% falling,
otherwise flat. Use the first and last retained daily observations in each UTC
month, report their actual timestamps, and withhold a label if either endpoint
is unknown or the span is under 20 days. These simple retrospective labels are
not a validated regime model or strategy input. Compare all strategies over the
same endpoints with continuous original funding; do not reset capital per month
or select a variant using these labels. Unknown intermediate valuations and
daily curve sampling remain disclosed. This reporting convention was recorded
before the full-history/portfolio matrix, without changing any trading input.
