# I8 offline follow-up — scoped residual inventory

Base: `759342d9c82d107eab713c9cada736628038e67e`, branch `i8/public-paper-hour`.
The owner authorized continued mock development after the completed public hour.
This increment adds scoped dust completion and reviewed resume, using synthetic
venues and temporary ledgers only. It adds no exchange access, credentials,
transfers, asset disposal or deployment. GitHub remains Free-only, with local
checks and `[skip ci]` publication under the unchanged hosted-CI lock.

## Behavior

A scoped LIMIT exit can complete with nonzero principal inventory only when:

- Ownership is reconciled, no orders remain, and market/account/fee/book and any
  required currency-conversion evidence are fresh.
- The quantity cannot meet sell minima even at the highest price allowed by the
  reviewed exit deviation and venue price cap. An exhausted deadline, order
  budget, price floor or after-fee outcome alone is not dust permission.
- The existing cycle policy explicitly approves the venue/account/base currency,
  amount and reporting-currency value. Cumulative approvals apply to repeated
  assets, and a shared reporting-currency ceiling applies across scoped dust.
- There are no unclassified cashflows or outstanding liabilities that invalidate
  completion evidence.

The policy is the same immutable policy digest reviewed by `limit-liquidate` or
the bounded-exit retirement command. No new default allowance or CLI override is
introduced. The shipped synthetic session still has zero dust allowance.
Missing approval remains `exit_blocked` with the reason visible in status.

`status` exposes each scoped exit's optional `residual` with exact owned amount
and marked value. Completion does not debit inventory, remove lots, release cost
basis, create proceeds or claim realized profit. It is completion of the approved
scoped liquidation obligation, while the portfolio remains active. This scope
covers principal base; non-reporting quote/fee assets still use the global exit's
existing conversion/ownership rules.

Completed residuals are rechecked by the bounded supervisor. A stale proof,
unreconciled ownership, disallowed value increase or aggregate cap breach removes
current completion and reports a block. Sellable inventory continues through the
original LIMIT exit within its retained deadline/order budget. No budget resets
or implicit transfers are introduced. Approval at one observation is not permanent
permission to ignore changing constraints.

## Confirmed resume and continued ownership

`resume` retains its existing saved-request, current-revision, actor/reason and
second-confirmation procedure. For a completed scoped exit containing dust, it
also checks fresh ownership, fees, unsellability and the shared dust caps. The
receipt records `retained_dust`, canonical residual proof, observation time and
evidence ID. The completion release, ordinary control transition and receipt
commit atomically. Failed checks leave the request uncommitted.

The ordinary reducer carries the original lots and exact cost into its next
round, using their actual execution-price anchor. It does not buy an enlarged or
duplicate seed to make a residual sellable. Its normal aggregate-sell eligibility
guard can leave that inventory waiting. Resume therefore does not promise another
order, recovery of the residual's value or permission to bypass market minima.

A resumed residual retains a scoped record with `released: true` until it is
reconciled to zero or becomes ordinary sellable inventory. It continues consuming
the shared dust allowance. This record does not retain the old liquidation's
order authority: ordinary pause/hold controls remain effective, and the dust
observer cannot cancel or place ordinary orders. Uncertain ordinary orders block
fresh dust certification. A new explicitly reviewed scoped exit may replace the
released record, preserving previous history in the revision journal.

Global takeover always retains the assets and counts them once. Unreleased scoped
exits retain their earlier budgets/deadlines. A released record contributes no
active liquidation deadline or order slot; global takeover uses its newly
approved global exit rules. Final global NAV still values unavoidable unsold dust
at zero, and reports its quantity/value separately rather than as proceeds.

## Compatibility, tests and rollback

SQL schema v5, dependencies and existing financial snapshots are unchanged.
`AllocationExit` gains optional `residual` and `released` fields, omitted when
empty/false so old canonical bytes remain identical. A golden digest was captured
from the base commit. New populated records require a compatible runtime; do not
downgrade an active ledger or edit a session's source-digest marker. Preserve all
requests, journals and backups during a scoped code rollback.

Tests cover explicit approval and individual/combined caps; fresh revaluation
after resume; stale resume refusal; preservation of lots, cash and cost; no extra
seed; backup/restore; whole-portfolio takeover before/after resume; old codec
bytes; sellable inventory after deadline; and seven real SIGKILL boundaries during
dust completion and atomic release. Existing portfolio, scoped exit and CLI
regressions remain required. The focused portfolio/scoped selection passed
**101 tests in 96.51 seconds**. That selection overlaps the full suites below.

| Final check | Result |
|---|---|
| Full pytest, Python 3.12.16 | **824 passed**, 228.85 s; no failures/skips |
| Full pytest, Python 3.13.18 | **824 passed**, 237.67 s; no failures/skips |
| Ruff lint/format | Passed |
| Strict mypy | Passed, 151 source/test files |
| Bandit (`src`, `scripts`) | Passed |
| Secret scan, including staged additions | Passed; baseline unchanged; one narrow golden-digest annotation added |
| Lock and dependency advisory audit | Unchanged 66-package graph; no known vulnerabilities found |
| Original/imported specifications and public journals | Recorded hashes unchanged |
| Hosted CI / exchange execution | Not run; unchanged account lock and authorization boundaries |

Application-source SHA-256:
`1c84f9aca98996bf03e74687a9ff0c08a6f99bb7da9404b6fffa9a679c1d16c5`.
Wheel/sdist destination: `/tmp/grid-i8-scoped-dust-dist/`, outside Git.
The package has 69 application modules; generated databases/journals stay outside Git.

The original observed 48-hour acceptance requirement, intended-first-venue
evidence, private accounts, I9 and deployment remain separately gated. The
completed one-hour public authorization is spent. Remaining offline integration
includes the research/storage CLI commands and requirement-by-requirement review.
