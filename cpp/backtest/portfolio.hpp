#pragma once

#include "engine.hpp"

namespace grid {
// Offline USDT/common-quote scenario only. Accounts/allocations remain distinct;
// this coordinator has no balances of its own and never posts a fill.
struct PortfolioPolicy {
    bool exchange_enabled = false, symbol_enabled = false;
    Rational exchange_threshold{".10"}, symbol_threshold{".10"};
    Rational max_deviation{".05"};
    std::int64_t mark_max_age_ms = 0, ttl_ms = 3600000, duration_ms = 14400000;
    unsigned max_reprices = 3, max_cycles = 10000, nav_order_budget = 4;
};
struct PortfolioAllocation {
    const EngineState* state;
    const EngineContext* context;
    std::optional<Rational> current_close;
    std::optional<std::int64_t> mark_ms;
};
struct AllocationNav {
    std::optional<Rational> raw, net;
    Rational owned, sellable, residual, exit_bid, estimated_fee;
    std::string reason;
};
struct PortfolioNav {
    std::vector<AllocationNav> allocations;
    std::optional<Rational> raw, net;
};
[[nodiscard]] Rational sell_quantity(const Rational& owned, const Market&, const Fee&, const Rational& price);
[[nodiscard]] AllocationNav allocation_nav(const PortfolioAllocation&, const PortfolioPolicy&, std::int64_t now_ms);

struct PortfolioExitSlot {
    std::optional<Rational> anchor_bid;
    std::optional<std::int64_t> order_id, created_ms, started_ms;
    unsigned orders = 0;
    std::string reason;
};
struct PortfolioCycle {
    std::string tier, key, phase = "active";
    std::vector<std::size_t> members;
    Rational baseline, original_funding;
    std::optional<std::int64_t> trigger_ms, completed_ms;
    std::optional<Rational> trigger_nav, trigger_raw_nav, trigger_baseline;
    std::optional<Rational> completion_nav, completion_raw_nav;
    unsigned cycles = 1, triggers = 0, completions = 0;
};
struct PortfolioAction { std::size_t allocation; Transition transition; };
struct PortfolioEvent {
    std::string kind, tier, key, reason;
    std::int64_t timestamp;
    unsigned cycle_number;
    Rational baseline, original_funding;
    std::optional<Rational> raw_nav, net_nav;
    std::optional<std::int64_t> trigger_ms, completion_ms;
    std::optional<Rational> trigger_nav, trigger_baseline, completion_nav;
};
struct PortfolioStep {
    PortfolioNav nav;
    std::vector<PortfolioAction> actions;
    std::vector<PortfolioEvent> events;
};
class PortfolioCoordinator {
public:
    PortfolioCoordinator(PortfolioPolicy, std::vector<Scope>, const std::vector<Rational>& initial_funding);
    // Exactly once per complete timestamp group, strictly increasing timestamps.
    // Apply every returned transition before calling again; process its commands
    // through the same delayed bar adapter as all ordinary strategy commands.
    [[nodiscard]] PortfolioStep evaluate(const std::vector<PortfolioAllocation>&, std::int64_t now_ms);
    [[nodiscard]] const std::vector<PortfolioCycle>& cycles() const { return cycles_; }
    [[nodiscard]] const std::vector<PortfolioExitSlot>& slots() const { return slots_; }
    [[nodiscard]] const std::vector<std::optional<std::size_t>>& owners() const { return owners_; }
private:
    PortfolioPolicy policy_;
    std::vector<Scope> scopes_;
    std::vector<PortfolioCycle> cycles_;
    std::vector<PortfolioExitSlot> slots_;
    std::vector<std::optional<std::size_t>> owners_;
    std::optional<std::int64_t> last_ms_;
};
} // namespace grid
