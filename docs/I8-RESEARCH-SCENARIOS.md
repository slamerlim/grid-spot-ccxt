# I8 offline follow-up — fixed OHLC scenario comparisons

Later extension: `I8-RESEARCH-PERFORMANCE.md` adds funded portfolio benchmarks and
gross realized statistics within member results. Evidence below describes this
comparison's original increment.

Base: `f9ffa8a91d4fa5e0781e71e7b80cd4cf5e836064`, branch
`i8/public-paper-hour`. This is authorized continued mock research development.
It uses synthetic tests, local validation and GitHub Free only. It does not
repeat the completed public hour, access exchanges/accounts, install a service
or enable trading. Imported specifications and original archives remain unchanged.

## Three declared execution conventions

PRD §9 requests conservative, baseline and optimistic treatment of ambiguous
OHLC execution. The replay now supports three fixed conventions:

| Suite label | Model in the replay bundle | Fill selection |
|---|---|---|
| `conservative` | `strict_skip_ambiguous` | Require a crossing beyond the float32 rounding interval; skip all fills when both pre-existing sides cross |
| `baseline` | `touch_buy_priority` | Permit central-value touches; choose only pre-existing BUYs when both sides touch |
| `optimistic` | `touch_sell_priority` | Permit central-value touches; choose only pre-existing SELLs when both sides touch |

The first two models retain their previous behavior. The third favors SELL
settlement on an ambiguous bar; it does not optimize future returns. Baseline
BUY priority is a chosen reference convention, not an estimate calibrated to a
venue. All three use deterministic price/order-ID priority, minimum-lot and
funding rules, fees, delayed order eligibility and delayed cancellation finality.
At most one side per allocation fills per bar, and a newly emitted order cannot
fill in its creation bar. The shared selector serves both the pure-domain and portfolio
ledger adapters. Live strategy behavior and the one-aggregate-SELL invariant
are unchanged.

The PRD calls for bounds, but a guaranteed lower/upper PnL bound cannot be inferred
from these OHLC inputs. The implementation reports the observed range of the
three specified model outcomes, with that limitation explicit. It does not claim
to enumerate every feasible execution path or recover queue, volume or depth.
In the synthetic ambiguity fixture, the same 1,000 allocation ends at 1,000,
1,026.46 and 1,010 respectively: the baseline exceeds the optimistic result.
Labels therefore never sort results by return, identify a winner for deployment,
or authorize parameter transfer to live execution.

## CLI and artifact contract

```sh
uv run --locked gridctl backtest --config /tmp/study/replay.json --compare-scenarios --output /tmp/study/new-comparison.json
uv run --locked gridctl backtest --config /tmp/study/replay.json --portfolio /tmp/study/portfolio.json --compare-scenarios --output /tmp/study/new-portfolio-comparison.json
```

`python -m grid.research replay` accepts the same flags. Without
`--compare-scenarios`, execution still runs the single model named in the bundle;
`touch_sell_priority` can also be selected that way. A previous application
version will reject this new model rather than silently choose another one.

The comparison flag explicitly requests all three members in fixed order. The
original configured model remains recorded in `input_config`; each member's
effective config replaces only that model. Capital, contexts, fees, latency,
sources and optional portfolio settings are otherwise shared. Each run starts
from fresh state. Portfolio members run sequentially with separate temporary
mock ledgers, using the existing SQLite approval and protected-path checks.

The CLI retains the `grid-research-run-v1` provenance envelope. Its `result` is
now `grid-scenario-comparison-v1` when the comparison flag is present. It contains
the original normalized configuration, a config hash including all scenario
conventions, common manifests, reporting currency, valuation basis, assumptions,
three full engine results and compact summaries. Each full result retains its
own hash; the comparison has a separate deterministic `result_sha256`. The
outer envelope binds the combined payload to the observed source/runtime/Git
evidence. Direct comparison API calls return the inner object without that
environmental envelope, just like the other direct research engines.

The summaries distinguish starting NAV, observed ending NAV/return, evaluation
endpoint, phase, actual completed cycle return and exit shortfall. Unknown NAV
stays null. Ordinary replay uses allocation-matched mark-to-close NAV without
estimated exit costs; portfolio mode uses its controller's full owned-capital
net valuation, including unallocated owned pools. A report never mixes the two
bases. The normal default mode's cash/buy-hold results remain in each full member;
this increment does not add those benchmarks to the portfolio engine.

`observed_nav_range` is available only if all three endings have known NAV,
equal starting baselines and the same evaluation endpoint. Differing baselines
are an error. Unknown NAV or differing endpoints retain the individual outcomes
but produce no shared minimum/maximum/span. A portfolio cycle that completes
early is not marked forward, restarted or forced to consume more exposure to
align its horizon with another scenario. Unfinished/blocked exits remain so.
The comparison is not an annualized performance ranking or an actual-return bound.

## Integrity, limits and failure behavior

Each engine validates its sources and consumes them through its own checksum
checks. Complete manifests must agree between scenarios. Source identities are
checked again after the last member. Changed bytes, provenance or file identity
refuse a combined report rather than confounding scenario and input changes.
This is ordinary filesystem identity/checksum protection, not isolation from a
hostile process with the same OS identity.

There are exactly three sequential runs, each subject to its existing bounds.
The comparator checks accumulated encoded member sizes and the final encoded
comparison against a 64 MiB inner-result cap. It retains the full results in
memory, and encoding/cap checks occur after allocation. These are not OS RAM/CPU/
disk quotas. The three engines currently repeat validation and streaming; no
validated-shard cache or archive-wide parallel sweep is introduced. Expect more
CPU/IO than one replay. Resource contention and replay reuse remain separate work.

If any member fails, the whole CLI comparison fails and no partial suite is
published. It never drops the failing model and presents the remaining results
as the requested three-case study. Output preflight, exclusive publication,
sanitized errors and interrupted-run handling remain shared with the research
CLI. Originals and the public evidence directories are never outputs.

## Validation and remaining scope

The focused tests cover the fixed order and counterexample to PnL ordering;
strict-versus-touch behavior; no same-bar new opposite order; candidate order
independence and unknown-model refusal; canonical scope ordering and low ambient
Decimal precision; partial portfolio outcomes and unknown-range preservation;
the full portfolio comparison through the CLI with trigger/exit shortfall;
incomparable horizons; source changes between members and after the last member;
failed/capped members without partial publication; new model bundle round-trip;
and a separate CLI process with unchanged input bytes/mtime and envelope hash.

Local validation passed on the final application source:

- Python 3.12.16: **925 tests passed** in 431.53 seconds.
- Python 3.13.18: **925 tests passed** in 458.14 seconds.
- Ruff lint and formatting, strict mypy (161 source/test files), Bandit over
  application/scripts, lock consistency, staged secret scanning with the unchanged
  baseline, and the dependency advisory audit passed. The audit reported no known
  vulnerabilities; that is not a guarantee against undisclosed vulnerabilities.
- Application source SHA-256 remained
  `d02ced1944b3d9cadc7b599113f44b57259a365c0a7a32828dd998ee3ff947b6`
  throughout both full suites. Durable logs and completion receipts are under
  `/tmp/grid-i8-scenario-final-checks/`, with stems
  `3.12-1793824008365684572` and `3.13-1793824008363001352`.
- Wheel and source-distribution artifacts are under
  `/tmp/grid-i8-scenario-dist/`. Package contents are checked against the final
  tree, including all 74 application modules; generated financial/history files
  are excluded. These temporary artifacts and logs are not committed evidence.

Imported specification hashes, dependency manifests and the secret baseline are
unchanged. Hosted CI remains pending under the unchanged account lock;
publication uses `[skip ci]`. No exchange probe or new observed run was performed.

This completes the fixed three-convention comparison, not the whole scientific
evaluation requirement. Independent-lot reference, walk-forward/regime splits,
fee-tier sensitivity, survivorship/delisting studies, remaining metrics and
portfolio baselines, constrained research/fill contention, and the full storage
benchmark matrix remain open. Representative target-venue execution evidence is
still absent. I8's minimum 48-hour observed gate, first-venue readiness, I9 and
deployment remain separately gated.

Rollback can revert this increment without a financial schema migration. Preserve
new reports and use consumers that recognize their schema. An older application
cannot read the new model literal. Existing operator source-digest compatibility
rules still apply; do not bypass them or downgrade a financial ledger in place.
