# I7 review proposal — portfolio accounting and global cycle exit

**Status: approved.** The owner explicitly selected "Approve I7 with local
validation". Approval ends at I7 review.
Use local validation and GitHub Free only; hosted CI remains pending while the
known account lock is unchanged. Do not retry it or request paid capabilities.

## Proposed authorization

Implement portfolio NAV and a durable global cycle-exit coordinator with synthetic
market/FX/fee/account facts, the existing mock executor and temporary ledgers.
This includes additive persistence migrations and crash/backup/restore tests.
No exchange access, public probes, authenticated access, real/sandbox trading,
transfers, service, deployment or live financial databases are included.
Original datasets, legacy source and imported specifications remain unchanged.

## Concrete work

1. Define Decimal portfolio ownership and valuation snapshots across funded
   allocations and explicitly assigned account pools, counting reserved funds
   once. Include all winners, losers, residual inventory and third-token fee
   assets/liabilities. Use fresh conservative bid-side executable valuation,
   explicit bounded conversion paths and estimated exit fees. Unknown/stale
   quotes, missing fee conversions or account ownership block a trigger.
2. Persist cycle identity, approved starting NAV/baseline and external-cashflow
   records. No silent deposit/withdrawal profit: baseline changes require an
   explicit auditable rule; unclassified cashflows block evaluation. Mock-only
   tests supply these facts, and no transfer endpoint is introduced.
3. At aggregate net NAV >= 1.10 times the approved cycle baseline, atomically
   latch the exit and freeze new BUY claims across every supervised venue/scope.
   Universe selection cannot clear the latch. Cancel BUYs, reconcile uncertain
   outcomes and preserve late fills before completing the exit. A committed
   send remains uncertain even when an exit is latched during its I/O wait.
4. Add an explicitly configured bounded LIMIT-only exit policy: price bounds,
   attempts/time budgets, minimum-size/dust treatment, current fees, and one
   unresolved SELL per allocation. Reuse cancellation/replacement ownership;
   never create an extra SELL while its predecessor is unresolved. An unavailable
   venue, unreachable permitted price or unsellable residual produces a visible
   blocked state, not fabricated completion or a market-order fallback.
5. Persist trigger NAV separately from realized proceeds, fees and final return.
   A +10% trigger does not guarantee +10% realized return. EXIT_COMPLETE requires
   reconciled terminal orders and explicitly accounted residuals. No automatic
   new cycle while anything is uncertain; restarting a completed cycle requires
   a separately explicit mock controller decision and fresh baseline/evidence.

Implementation choices must follow PRD §4 and the existing ledger ownership model.
Do not invent production funding, fee tiers, FX routes, liquidation concessions
or account capabilities. Required operator choices remain explicit fields with
synthetic test values, never example production defaults.

## Exit evidence and review

Cover Q12 and related Q01/Q03/Q05/Q09/Q10/Q11/Q17/Q19 cases: cross-venue winners
plus losers; reservations counted once; fresh/stale/missing conversion paths;
third-token fees/rebates; threshold equality; external cashflow classification;
concurrent universe change/partial fill/exit; late BUY/old SELL fills; partial
exit; unavailable venue; unreachable price; dust; duplicate exit/restart requests;
SIGKILL around latch/fill/completion commits; backup/restore of a blocked cycle.
Assert no overspend, double SELL, market-order path or restart of uncertain work.

Run both Python suites and the README lint/type/security/build checks locally.
Publish design, exact evidence, remaining limits and a concrete I8 proposal;
then stop for review. Any future I8 public feeds/sandbox calls/48-hour soak need
their own approval and observed evidence; mock checks cannot substitute for them.

Rollback is a scoped code/docs revert. Preserve financial and audit history;
test additive migration and separate-backup recovery rather than destructive
schema downgrade. No deployed database migration is authorized by this proposal.
