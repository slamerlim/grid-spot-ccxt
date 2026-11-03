# I0 requirements trace

The four imported specifications remain unchanged. This trace records their
implementation destinations; it does not replace them. I0 supplies packaging,
strict configuration, offline diagnostics, tests and CI only. I1–I9 remain
unimplemented and require their stated approval gates. Every Q01–Q20 test below
is **NOT IMPLEMENTED / NOT RUN** in I0; scaffold tests are not substitutes.

References: **PRD** = `PRD_GRID_SPOT_CCXT_FINAL_v2.md`; **ADR** =
`ADR-0001-NVMe-Storage-and-Caching.md`; **QA** =
`ITERATIONS_AND_ACCEPTANCE_FINAL.md`; **Master** =
`MASTER_IMPLEMENTATION_PROMPT.md`. Section numbers refer to those documents.

## Hard requirements and verification destinations

| Requirement preserved | Authority | Implementation destination | Future acceptance evidence |
| --- | --- | --- | --- |
| New implementation in a dedicated repository; no inherited simulator accounting | PRD §§0, 9; Master operating rules 1–2 | I0 scaffolding; I1–I9 independent implementation | I0 import/provenance checks; Q03, Q10, Q14–Q16 |
| Official free CCXT, including bundled `ccxt.pro`; one reproducible dependency lock | PRD §0; Master source-of-truth and I0 instructions | I0 package pin; I3 clients | I0 package/import checks; Q13, Q17, Q18 |
| OKX, HTX, Gate, XT, BingX, KuCoin, Bitfinex independently configurable; actual account capabilities verified | PRD §§0, 6; QA I3 | I0 disabled entries; I3 discovery; I6 runtime changes | Q11, Q13, Q17; seven-row observed capability evidence |
| Spot only; exclude leverage, margin, borrowing, derivatives and leveraged tokens | PRD §0.1; §§2, 5 | I1 eligibility contract; I3 mapping; I6 universe | Q02, Q11, Q14, Q20 |
| Limit only on seed, ladder, replacements, exits, manual actions and shutdown; bounded marketable limits allowed | PRD §0.2; §§3, 4, 6 | I1 proposed intent types; I4 gateway; I7 exit | Q01 with normalized and outgoing payload spies |
| Geometric levels `A/(1+g)^n`; Decimal/quanta; side-aware precision and distinct quantized levels | PRD §0.3; §§2, 3.1 | I1 math; I3 metadata boundary | Q02, Q06; I4 numeric payload regression |
| Each BUY is exactly the smallest exchange-valid BUY at its price; reject if post-fee inventory cannot support a valid SELL | PRD §0.4; §3.1; QA Q02 | I1 two-stage minimum/sellability calculation | Q02: buying 1, receiving .999, SELL minimum 1 means ineligible; never enlarge BUY to hide it |
| No escalating/martingale sizing; bounded pending minimum lots and accumulated lots | PRD §0.7; §§2, 3 | I1 reducer and budget math; I2 reservations | Q03, Q06 |
| One known-or-possibly-active aggregate SELL **per allocation_id**; reserve slot until terminal proof | PRD §0.5; §3.2; ADR concurrency | I1 slot model; I2 ledger; I4 executor | Q05, Q07, Q09; not one SELL per exchange or per purchased lot |
| Fill-driven progression; books only for legal pricing, eligibility, risk and NAV; no indicators/live candles | PRD §0.6; §§3.3, 5 | I1 normalized events; I3 book snapshots; I4 intake | Q04, Q06–Q08, Q13, Q17 |
| Real minimum LIMIT seed; acknowledgment is not fill; actual weighted execution anchor; unsellable partial tracked | PRD §3.2; QA Q04 | I1 pure state transitions; I4 execution | Q04 plus explicit partial-seed lifecycle clarification before I1 |
| Full aggregate SELL drains old BUYs; late BUY becomes next-round inventory; no duplicate seed | PRD §3.2 | I1 reducer; I4 cancellation/reconciliation | Q05–Q09 |
| Unknown submit/cancel blocks blind retry or overlapping replacement; confirmed fills posted once | PRD §§3.2–3.3, 6; ADR live DB/concurrency | I1 event model; I2 intent uniqueness; I4 recovery | Q05, Q08, Q09, Q13, Q19 |
| Dedicated bot-owned cash/inventory only; finite shared reservations; detect external activity | PRD §§0 scope boundary, 2 | I1 budget math; I2 ledger; I4 reconciliation; I7 valuation | Q03, Q10, Q12, Q19, Q20 |
| Explicit scoped identities and lossless money; no mixed-currency addition | PRD §2 | I1 types; I2 schema; I3 normalization; I7 valuation | Q03, Q08, Q10, Q12 |
| Actual base/quote/third-token fees, rebates and conservative estimates; no universal fee | PRD §§2–4, 6 | I1 math; I2 postings; I3 fee evidence; I7 NAV | Q02, Q04, Q10, Q12 |
| Mandatory configured funds, risk limits, freshness, dust and exit guards; zero eligible symbols is valid | PRD §§2, 3.1, 5, 12 | I0 unset configuration/readiness; I1 validation; I6 admission | Q02, Q03, Q06, Q11, Q12, Q17, Q18 |
| Combined net, cash-flow-adjusted NAV >=1.10 baseline triggers bounded LIMIT exit of bot-owned assets | PRD §0.8; §4 | I7 portfolio supervisor | Q12: winners and losers, stale FX, late fills, outages, unreachable prices |
| +10% is a trigger, not guaranteed realized profit; no EXIT_COMPLETE/restart with unresolved orders or unapproved residuals | PRD §4 | I7 exit state machine | Q12; realized return, trigger NAV, residuals and shortfall reported separately |
| Auto/manual/hybrid; deny overrides pin; changes invalidate generations and retain supervision of disabled/retiring allocations | PRD §0.9; §5 | I6 universe/runtime control | Q11, Q17; I7 includes draining venues in exit |
| One process, controller, order gateway and financial writer; no network waits inside DB transactions | PRD §§0.10, 7–8; ADR DB/concurrency | I2 controller/ledger; I4 executor | Q03, Q08, Q09, Q19 |
| Patched linked SQLite, WAL/FULL/FK, local filesystem; short transactions, safe backup and tested restore | PRD §8; ADR exact live database contract | I0 version diagnostic only; I2 persistence; I8 restore drills | Q19; live rejected for unsupported runtime/full disk/corruption/failed restore |
| Durable intent/reservation before send; atomic fill, fee, inventory and transition posting | PRD §§6–7; ADR exact live database contract | I2 ledger; I4 network boundary | Q03, Q08–Q10, Q19 fault injection at every boundary |
| One long-lived CCXT instance per venue/account; rate budgets, private events, bounded REST fallback | PRD §§6–7; ADR CCXT/cache policy | I3 client lifecycle; I4 supervision | Q13, Q17, Q18; `has` flags alone are insufficient |
| Bounded priority queues/tasks/cache/retries; overload pauses exposure, preserves fill evidence and favors reconciliation/exits | PRD §7; ADR cache/concurrency | I3–I4 scheduling; I6 caches; I8 resources | Q13, Q17, Q18; synthetic 1/7 venues × 10/100/1,000 symbols |
| Caches cannot authorize spending; single-flight refresh, TTL, provenance and generations | PRD §7; ADR exact cache contract | I3/I6 cache implementations | Q17; stale values block orders and NAV triggers |
| Preserve raw history unchanged; validate 24-byte layout/endianness, provenance, checksums and market identity | PRD §§8–9; ADR research | I5 importer/manifest | Q14; invalid data is recorded, never repaired by invented prices |
| Deterministic UTC multi-market replay, finite wallet, fees, conservative OHLC ambiguity and honest gaps | PRD §9; QA I5 | I5 research adapter sharing reducer contracts | Q14–Q16; final incomplete reporting interval retained |
| Reproducible research evidence, correct PnL/NAV/drawdown/profit factor, matched baselines and uncertainty | PRD §9; ADR benchmark plan | I5 reports; later evidence tiers only as approved | Q15–Q16 plus research metrics/baseline tests; no backtest profitability guarantee |
| Raw mmap first; Arrow/Parquet and compiled kernels only with end-to-end evidence; bound research resources | PRD §§7–9; ADR selection rule/future ADRs | I5 sample benchmark; I8 deployment hardware profile | Q18; byte-exact decode and identical accounting across formats |
| Strict local CLI, audited revisioned mutations, explicit sensitive-action gates; no GUI-first expansion | PRD §10 | I0 doctor only; I6/I8 control commands | Q11, Q20; command mutation replay tests later |
| No secrets in Git/logs/artifacts/DB; trade-only credentials; least-privilege systemd later; no deployment in I0 | PRD §10; Master rules 1, 9 | I0 ignore/CI/defaults; I8 operations | Q20; withdrawal permissions/account access verified later |
| Every iteration has reproducible tests/evidence/rollback; 48-hour paper soak and explicit per-venue canary authorization | PRD §11; QA §§1–2, 5, 7; Master finish rule | I0 evidence; I8 soak; I9 canary | Q01–Q20 plus observed soak, restore and operator approval records |

## I0 verification boundary

I0 tests check safe configuration, unsupported live mode, malformed inputs,
offline doctor output, linked SQLite version classification, and the absence of
trading/network/database effects. The following test identifiers are present
in the implementation. Parent integration reported **104 tests passed on
Python 3.12.3**, including these cases, with Ruff and mypy passing. The final
iteration evidence report records additional interpreter/build/security checks
and their actual status; this does not certify any future financial gate.

| I0 concern | Test identifier |
| --- | --- |
| Imported specifications retained verbatim | `test_imported_specifications_match_recorded_source_hashes` |
| No exchange/process execution paths in application | `test_i0_application_contains_no_exchange_or_process_execution_paths` |
| Private state ignored, reviewable inputs retained | `test_gitignore_excludes_private_state_and_keeps_reviewable_inputs` |
| Example disabled with explicit missing inputs | `test_example_is_disabled_and_explicitly_unconfigured` |
| Money string parsing lossless; unsafe numeric values rejected | `test_decimal_string_is_lossless_and_zero_reserve_allowed`, `test_invalid_financial_values_are_rejected` |
| Malformed/untrusted config fails closed | `test_unsafe_or_malformed_yaml_is_rejected`, `test_unknown_keys_rejected_without_echoing_input`, `test_oversized_config_is_rejected_before_parsing` |
| Environment/config cannot enable live | `test_live_environment_flags_cannot_enable_runtime`, `test_config_live_request_remains_disabled` |
| Version thresholds are not full storage readiness | `test_sqlite_threshold_does_not_approve_backports_or_provenance` |
| No credential reads/database opens in doctor | `test_doctor_does_not_read_credentials_or_open_database` |
| CLI readiness blocked and no files created | `test_cli_json_check_is_nonzero_and_creates_no_files` |
| Sanitized config failures and no live command | `test_cli_config_errors_are_sanitized`, `test_no_live_command_exists` |

Q01–Q20 remain future acceptance gates even if I0 tests cover one prerequisite,
such as LIVE being disabled. There are no order fills, ledger, strategy, venue
network probes, database migrations, hardware benchmarks or live-readiness
claims in I0.

## Unconfigured operator inputs and missing evidence

These are explicit live blockers, not authorization to choose profitable or
"safe" capital values: account identity and dedicated ownership; reporting
currency and permitted quotes; venue market/account options; actual fees and
fee currencies; funds and per-symbol/venue/global caps; maximum symbols, buys,
lots and orders; spacing, minimum net target and trading range; fee reserves;
seed and exit pricing policy; expiry, retry and deviation limits; approved dust;
book/NAV/conversion age and reconciliation cadence; resource/rate budgets;
retirement behavior; cycle cash-flow policy; tested storage/restore; target-venue
execution evidence; and operator signoff for each live allocation and venue.

The installed Python-linked SQLite runtime reported during I0 preparation was
3.45.1. It does not satisfy the accepted live policy. Offline diagnostics and
mock tests are allowed; live must remain unavailable. Recheck the actual linked
runtime for every deployment interpreter; a CLI `sqlite3` version is insufficient.

## Specification tensions requiring explicit treatment

1. **Minimum BUY versus SELL eligibility:** PRD §3.1 mentions both sides in its
   minimum calculation, but PRD §0.4 and QA Q02 explicitly forbid increasing the
   BUY merely to overcome post-fee unsellability. Preserve the strict two-stage
   rule: determine the smallest BUY; then reject it if it cannot support a SELL.
2. **"Equal-sized" versus minimum at each price:** PRD §0.7 describes equal-sized
   buys, while §0.4 and §3.1 require the minimum lot at each price. Minimum
   notional can make that base quantity differ by level. "No increasing sizing"
   means no strategy multiplier; it cannot impose constant base quantity at the
   expense of the explicit minimum rule.
3. **Partial-seed anchor lifecycle:** PRD §3.2 starts after the seed becomes
   sellable; QA Q04 explicitly describes completion setting the weighted-fill
   anchor. A sellable partial seed with a still-working remainder needs an
   approved rule for anchor fixation and ladder admission before I1. The plan
   records this decision without silently inventing a policy.
4. **No float finances versus CCXT's runtime boundary:** PRD §2 requires exact
   financial values; QA §1 specifically requires testing float/string conversion
   and the outgoing payload. I1 stays Decimal-only. I3/I4 must verify lossless
   venue serialization for the pinned CCXT version; unsupported cases remain
   ineligible rather than weakening precision guarantees.
5. **Read-only I3 versus order semantics:** Read-only queries can establish
   market metadata and observed historical order behavior. They cannot prove
   new-order client-ID acceptance, cancel races or post-only behavior. Keep such
   registry fields NOT_TESTED until an explicitly approved later order probe.
6. **Original legacy review artifact absent:** The four supplied attachments do
   not contain `Pasted markdown.md`. `LEGACY_REVIEW.md` is a labeled summary of
   the previous chat audit, not a fabricated verbatim attachment or a fresh
   data certification.

None of these observations changes the imported specifications or authorizes
I1 execution. The partial-seed policy requires a concrete decision during I1
plan review. All future financial behavior remains unimplemented in I0.
