# I6 review proposal — dynamic universe and safe hot reconfiguration

**Status: approved on 2026-11-04.** The owner explicitly selected "Approve I6
with local validation". Approval ends after I6 review. Use local validation and
GitHub Free only; hosted CI remains pending while the known account lock is unchanged.

## Proposed authorization

Implement offline auto/manual/hybrid universe policy, bounded eligibility/ranking
and generation-aware hot reconfiguration using synthetic market/book/fee/account
facts and temporary mock ledgers. Integrate with existing read-only adapter cache
contracts and the mock executor. No additional public exchange probes,
authenticated access, real/sandbox orders/cancellations, transfers, service or
deployment are included. Original datasets/legacy source remain unchanged.

## Concrete work

1. Typed allow/deny/pin policies, deterministic auto/manual/hybrid selection,
   explicit ranking inputs, hysteresis and minimum residency. A pin affects
   selection only; it cannot bypass verified spot identity, market constraints,
   account allocation, fees, freshness or risk admission.
2. On-demand book eligibility from existing normalized bounded snapshots. Reuse
   long-lived venue/account clients and adapter single-flight metadata refresh;
   do not construct one client per symbol. No new continuous public-price stream
   or candle-based trading signal is introduced.
3. Version configuration and eligibility decisions. Invalidate on rules/fees,
   account, market or book generation changes. Every cached decision has an owner,
   scoped key, revision, timestamp, TTL, capacity and stale/invalidation behavior.
   A stale decision cannot authorize a pending BUY after hot reconfiguration.
4. Exclusion and venue disable stop new exposure while keeping existing orders,
   inventory and unknown outcomes supervised. Use existing reducer controls,
   durable revocation for proven-unsent intents and reconciliation for possibly
   sent orders. Do not treat retirement as liquidation, cancellation completion
   or permission to remove financial ownership.
5. Serialize mock controller policy changes with fill handling and mock execution;
   retain exit/control priority. No lock across remote waits. Bound discovery
   candidates, cached books, queues and refresh work; overload pauses admission.
   Portfolio valuation and the global +10% exit remain I7.

## Exit evidence and review

Add deterministic Q11/Q17 and relevant Q18 fixtures: deny during partial fill,
old SELL filling during retirement, disabled venue with unknown submission,
stale config/fee/minimum/book generations, pin on an ineligible market, simultaneous
refresh callers, cache expiry/invalidation, retirement restart and one/seven
synthetic venue resource ownership. Confirm no orphan orders/assets, second SELL,
duplicate clients, overspend or hidden exchange calls. Reuse existing tests.

Run both Python suites and README lint/type/security/build checks locally. Publish
design, implementation, actual evidence, limitations and a concrete I7 proposal;
then stop at review. Do not retry hosted CI or use paid GitHub features. Local
mock evidence does not certify real account/exchange behavior or production load.

Rollback is scoped code/docs revert; any additive ledger change must retain
financial identity/audit records and include tested migration/restore behavior.
No destructive migration or deployed database change is included in this plan.
