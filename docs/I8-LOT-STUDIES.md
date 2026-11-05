# I8 follow-up — paired basket and independent-lot studies

Implemented 2026-11-05 from base `1f3290e3c970d508393b621675ba91acfaeeea30`
on `i8/public-paper-hour`, under the owner's instruction to continue offline
work after the completed public hour. This closes the independent-lot
walk-forward integration gap in PRD §9; it does not change the production
single-aggregate-SELL policy or authorize further exchange access.

## Interface and research contract

Combine `backtest --study PLAN.json --compare-lots` to compare two predetermined
research policies across the same declared folds, spacing candidates and
fee/latency/OHLC scenarios. The existing study plan schema is unchanged.
The result schema is `grid-lot-walk-forward-comparison-v1`, inside the existing
CLI provenance envelope. It retains complete `basket_single_sell` and
`independent_lots` studies, plus per-fold/scenario comparisons.

Each policy selects spacing independently using its own training total NAV,
including unrealized inventory. Lexicographic candidate names break ties.
Selection is frozen before that policy's first held-out replay. The basket
study executes first, then the independent-lot study; the second does not use
results from the first. Held-out outcomes never choose parameters or a policy.
The final comparison validates the retained selections against training results
and checks that selected metrics agree with the corresponding retained members.
Every declared candidate's training and test results remain visible.

The selected-return difference may compare different spacing candidates, since
each policy can select its own training winner. Separate matched-candidate
return differences are reported for training and testing. All differences are
independent-lot return minus basket return. Both are modeled observations, not a policy ranking, significance test or basis for live export.
Fees, fill conventions and latency remain separately declared scenarios and are
not optimized. All windows start with fresh funds and state; no cross-window
carry, compounded returns or stitched NAV curve is manufactured.

The independent reference uses the existing `fixed-anchor-independent-lots-v1`
convention and shared reducer/math. It retains shared allocation cash, risk caps,
fee-token escrow, minimum buy lots, declared shallow-level refill priority and
one-side-per-original-allocation/bar OHLC ambiguity. Its full rules remain in
`I8-INDEPENDENT-LOT-REFERENCE.md`. It has no portfolio-exit model.

`--portfolio` and `--portfolio-cycles` therefore remain incompatible with the
paired lot study. `--compare-scenarios` is also excluded because the plan already
specifies scenarios. Standalone `--compare-lots`, ordinary basket studies and
portfolio studies retain their existing behavior and schemas. Direct offline
API `study.run(..., policy="independent_lots")` produces
`grid-independent-lot-walk-forward-study-v1`; the default remains `"basket"`.
Unknown policy values and independent-lot/portfolio combinations are rejected.

## Bounds, consistency and failures

Preflight counts both policies: `4 × candidates × scenarios × folds` must be
at most 128 replays. Combined simulated allocation/bar steps remain at most
2,000,000. The reference's total pocket capacity remains at most 4,096. These
checks happen before either policy reads history or starts replay. All effective
candidate/scenario/window configurations are checked before basket execution;
reference-specific capacity is checked before either study.

The complete combined result, including retained studies and comparisons, is
capped at 64 MiB. Each engine/study retains its own existing bounds. These are
work/output limits, not a 64 MiB RSS guarantee or OS CPU/IO isolation; validation,
JSON encoding and retained Python objects have additional bounded overhead.

Both studies require complete eligible base history, including spans outside
selected folds. Their complete base manifests must agree and remain unchanged.
Every paired candidate must also have identical effective configuration,
slice manifests and positive starting NAV. Missing cases/candidates, changed
funding/input facts, inconsistent selected metrics, a failed replay, an oversized
result or source mutation refuses the entire report. The CLI keeps exclusive
publication and sanitized errors; it never publishes a successful subset.

No dependency, financial schema, order executor or strategy reducer changed.
No real or sandbox orders, additional public probes, account access, service
installation or deployment occurred.

## Retained synthetic example

Files under `/tmp/grid-i8-lot-study-evidence/` contain the invented history,
`replay.json`, `plan.json`, full `report.json` and `summary.json`. The generating
wrapper is `/tmp/grid-lot-study-evidence.py`. The fourteen-bar source is split
into seven training and seven held-out bars. Two spacing candidates (0.1/0.2),
three explicit OHLC conventions, zero modeled fees and one-bar latency yield
24 fresh-funded replays. Initial NAV is 1,000 quote units per experiment.

The separate CLI process completed in 0.549887 seconds. All three conventions
happened to give the same selections and returns on this invented path:

| Policy | Selected spacing | Training return | Held-out return |
| --- | ---: | ---: | ---: |
| Basket aggregate SELL | 0.1 | +2.736% | −37.586% |
| Fixed-anchor independent lots | 0.1 | +1.736% | −34.681% |

The 2.905 percentage-point held-out difference does not conceal either loss.
The full report also retains the unselected 0.2 candidate and all assumptions.
Agreement between the three conventions on this path is not evidence that OHLC
execution uncertainty is resolved, nor a performance recommendation.

Application source SHA256:
`b7bd30b0b86b7b90d1d358c7b8b47fb1930e0f1626313460334e6a01284ea9f0`.
Config SHA256:
`1e8380e150721dd0c47496233292d09b220f019e40238fd68165771ed0b8d566`.
Deterministic result SHA256:
`6ec910ad2af376c8b81b4c84cc623b51954e02a5a3b4d0587c4d1aa360bbea1b`.
The CLI envelope accurately reports changed application source relative to the
claimed base commit. Runtime observations are separate from financial hashes.

Reproduce with a new, unused output:

```sh
uv run --locked gridctl backtest \
  --config /tmp/grid-i8-lot-study-evidence/replay.json \
  --study /tmp/grid-i8-lot-study-evidence/plan.json --compare-lots \
  --output /tmp/grid-lot-study-review-new/report.json
```

The retained config claims its original base commit. Preserve it to reproduce
that claim, or publish a new version of the config with an updated code revision;
never overwrite the original evidence. Current source observations identify
what actually executes in either case.

## Validation and remaining work

The 22 new cases in `tests/research/test_lot_study.py` cover actual standalone
member equivalence, retained losses, training-only decisions for both policies,
independent parameter selection using controlled engine outcomes, separate
fee/model/latency scenarios, canonical ordering and ambient Decimal precision,
rolling folds/nonzero offsets, fresh funding, preflight doubled work bounds,
changed/inconsistent evidence, exclusive subprocess publication, failed members,
combined result limits and rejected portfolio combinations.

The focused study/reference group passed 56 tests in 4.04 seconds. A separate
comparison loaded `study.py` from the previous Git commit and verified every
field and canonical byte of ordinary basket and portfolio study results on the
same fixtures. Receipt: `/tmp/grid-i8-lot-study-compatibility.json`.

Executed local checks in the locked Python 3.12 environment:

- `ruff check .` and `ruff format --check .`: passed (243 formatted files).
- `mypy`: passed across 182 application/test files.
- `bandit -q -r src scripts`: passed.
- `uv lock --check`: 66 packages resolved with the lock unchanged.
- Locked all-group export and `pip-audit --strict --disable-pip --no-deps`:
  no known vulnerabilities reported. Advisory lookup used network access,
  not an exchange transport.
- Unchanged-baseline secret scan: passed across 252 tracked/new files.
- Wheel byte verification: all 83 application modules matched the checkout,
  with no raw histories, databases or JSONL files in the artifact.
- All four specifications and original attachments matched their recorded hashes;
  dependency manifests and security baseline matched the base commit.

Full offline suites passed with no failures or skips:

| Interpreter | Tests passed | Pytest elapsed | Wrapper elapsed |
| --- | ---: | ---: | ---: |
| Python 3.12.16 | 1,114 | 642.60 s | 643.140 s |
| Python 3.13.18 | 1,114 | 679.15 s | 679.714 s |

Both ran `python -m pytest -q` through `/tmp/grid-lot-study-check.py`, with
`UV_PYTHON` and `UV_PROJECT_ENVIRONMENT` selecting the locked interpreters as
in README. Source was frozen throughout both complete suites, and before/after
application hashes matched the SHA256 above. Full logs and durable JSON receipts:

- `/tmp/grid-i8-lot-study-final-checks/3.12-1793837517693932626.{json,log}`.
- `/tmp/grid-i8-lot-study-final-checks/3.13-1793837517691017741.{json,log}`.

Wheel and source distribution are under `/tmp/grid-i8-lot-study-dist/`.
Byte verification covers all 83 application modules and 247 required source,
test, documentation and root-manifest files, with no raw histories, databases
or JSONL files. Receipt: `/tmp/grid-i8-lot-study-package-verification.json`.
The final tracked/new-file secret scan uses the unchanged baseline. No security
exception or dependency change was introduced.

Historical rule/fee/FX changes, point-in-time universe/delisting evidence,
representative target-venue execution data, the remaining storage benchmark
matrix and deployment resource acceptance remain incomplete. A plan hash is not
proof of preregistration. The observed minimum 48-hour I8 gate, intended-venue
account evidence, I9 and deployment remain separately gated. Continue offline
work without reusing the completed public-hour authorization.

Hosted CI remains pending under the unchanged account lock. Use GitHub Free
only and `[skip ci]` publication; local tests are not hosted-CI evidence. A scoped
commit follows the existing iteration workflow; no PR is created. Rollback is
a normal revert of this increment, with no database migration. Preserve already
published results and original histories.
