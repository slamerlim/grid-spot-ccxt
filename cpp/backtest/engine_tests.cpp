#include "simulator.hpp"

#include <algorithm>
#include <bit>
#include <iostream>

namespace {
using grid::Rational;
unsigned checks = 0;
Rational q(const char* text) { return Rational(text); }
void check(bool accepted, const char* message) { ++checks; if (!accepted) throw grid::Error(message); }
grid::EngineContext context() {
    grid::EngineContext c;
    c.market = {q(".01"), q(".001"), {q(".01"), q("1000000")},
                {q(".001"), std::nullopt}, {q("5"), std::nullopt},
                {q(".001"), std::nullopt}, {q("5"), std::nullopt}};
    c.policy.spacing = q(".01"); c.policy.net_target = q(".01");
    c.policy.quote_reserve = q("100"); c.policy.max_quote_committed = q("900");
    c.policy.lower_price = q(".01"); c.policy.upper_price = q("1000000");
    c.policy.cost_quantum = q("1e-12"); c.policy.max_spread = q(".01");
    c.policy.max_source_age_ms = 60000;
    c.buy_fee = {"QUOTE", q(".001"), q(".00001"), std::nullopt, 64}; c.sell_fee = c.buy_fee;
    c.book = grid::Book {q("100"), q("100.1"), 0, 1};
    return c;
}
grid::EngineState initial(const grid::EngineContext& c) {
    grid::EngineState s; s.scope = {"venue", "research", "BTC/USDT", "one", "grid", "cycle"};
    s.quote_cash = q("1000").exact_units(c.quote_quantum); return s;
}
grid::Bar bar(std::int64_t index, float high, float low, float close) {
    grid::Bar result {}; result.timestamp = (index + 1) * grid::minute_ms;
    const std::array<float, 4> prices {close, high, low, close};
    for (std::size_t n = 0; n < 4; ++n) result.prices[n] = {std::bit_cast<std::uint32_t>(prices[n])};
    return result;
}
void direct_admission() {
    auto c = context(); auto s = grid::reduce(initial(c), grid::StartEvent {initial(c).scope}, c).state;
    s.fee_balances["TOKEN"] = q("1").exact_units(c.token_quantum);
    const auto cash = s.quote_cash;
    const auto buy = s.orders.front();
    grid::FillEvent bad {s.scope, "bad", buy.id, Rational(buy.amount) * c.base_quantum, buy.price,
                        std::vector<grid::ActualFee>{{"TOKEN", q(".001"), q("-1")}}};
    auto result = grid::reduce(s, bad, c);
    check(!result.accepted && result.state.quote_cash == cash && result.state.position_cost == 0,
          "direct fee-sign mismatch rejects atomically");
    check(result.state.fee_balances == s.fee_balances && result.state.fills.empty(), "rejected fee changes no balances or fills");
    bad.fees = std::vector<grid::ActualFee>(17, grid::ActualFee {"QUOTE", q("0"), q("0")});
    result = grid::reduce(s, bad, c);
    check(!result.accepted && result.state.quote_cash == cash, "direct oversized fee list rejects atomically");
    result = grid::reduce(s, grid::ControlEvent {s.scope, 1, "invalid"}, c);
    check(!result.accepted && result.state.mode == "active", "direct invalid control mode rejects");
}
void gap_and_policies() {
    auto c = context(); c.policy.ladder_size = 1; c.ladder_policy = "ladder_one";
    grid::Simulator simulation(initial(c), c, {"strict_skip_ambiguous", 1, q(".001"), true});
    simulation.step(bar(0, 100, 50, 100), 0);
    check(simulation.metrics.fill_count == 0 && simulation.state.orders.front().price == q("100"), "seed cannot fill on creation bar");
    simulation.step(bar(1, 100, 99, 100), 1);
    simulation.step(bar(2, 100, 98.5F, 99), 2);
    check(simulation.metrics.fill_count == 2 && !simulation.cancel_due.empty(), "second acquisition requires SELL cancellation");
    const auto fills = simulation.metrics.fill_count;
    simulation.step(bar(10, 50, 50, 50), 10);
    check(simulation.metrics.gap_refreshes == 1, "known modeled state refreshes once after gap");
    check(simulation.metrics.notices.contains("stale_market_or_fee_facts"), "gap boundary refuses stale replacement facts");
    check(simulation.metrics.fill_count == fills, "gap recovery never creates retrospective fills");
    const auto sell = std::find_if(simulation.state.orders.begin(), simulation.state.orders.end(), [](const auto& order) { return !order.buy && !order.terminal(); });
    check(sell != simulation.state.orders.end() && simulation.eligible.at(sell->id) == 11, "fresh replacement SELL waits future bar");
    const auto buy = std::find_if(simulation.state.orders.begin(), simulation.state.orders.end(), [](const auto& order) { return order.buy && !order.terminal(); });
    check(buy != simulation.state.orders.end() && buy->price == q("49.50") && simulation.eligible.at(buy->id) == 11,
          "one lower BUY references fresh post-drop book and future eligibility");
    c = context(); c.entry_policy = "no_seed_lower";
    grid::Simulator lower(initial(c), c, {"strict_skip_ambiguous", 1, q(".001"), true});
    lower.step(bar(0, 100, 100, 100), 0);
    check(lower.state.orders.front().price == q("99"), "no-seed-lower first entry is one geometric step below");
    lower.step(bar(1, 100, 98.5F, 99), 1);
    lower.step(bar(2, 103, 100, 102), 2);
    lower.step(bar(3, 102, 102, 102), 3);
    check(lower.state.round_id == 2 && lower.state.seed_id.has_value(), "completed basket advances round without resetting IDs");
    const auto seed = std::find_if(lower.state.orders.begin(), lower.state.orders.end(), [&](const auto& order) { return order.id == *lower.state.seed_id; });
    check(seed != lower.state.orders.end() && seed->price == q("102"), "later round uses shared fresh-book reentry");
    check(lower.eligible.at(seed->id) == 4 && lower.metrics.fill_count == 2,
          "fresh-book reentry is eligible only after its creation bar");
    c.reentry_policy = "reentry_one_step_below_sell";
    grid::Simulator one_step(initial(c), c, {"strict_skip_ambiguous", 1, q(".001"), true});
    one_step.step(bar(0, 100, 100, 100), 0);
    one_step.step(bar(1, 100, 98.5F, 99), 1);
    one_step.step(bar(2, 103, 100, 102), 2);
    one_step.step(bar(3, 102, 102, 102), 3);
    check(one_step.state.round_id == 2 && one_step.state.last_executed_sell_price == q("100.10"),
          "one-step fixture records actual aggregate SELL execution");
    const auto next = std::find_if(one_step.state.orders.begin(), one_step.state.orders.end(), [&](const auto& order) {
        return one_step.state.seed_id && order.id == *one_step.state.seed_id;
    });
    // Handwritten floor_tick(min(102, 100.10 / 1.01)) = 99.10.
    check(next != one_step.state.orders.end() && next->price == q("99.10"),
          "one-step reentry differs from fresh-book102 by declared executed-SELL formula");
    check(one_step.eligible.at(next->id) == 4 && one_step.metrics.fill_count == 2,
          "one-step reentry is eligible only after its creation bar");
    one_step.step(bar(4, 100, 98, 99), 4);
    check(one_step.metrics.fill_count == 3 && one_step.state.owned_base() > 0,
          "one-step seed can fill on the subsequent eligible bar");
}
} // namespace
int main() {
    try { direct_admission(); gap_and_policies(); std::cout << checks << " engine integration checks passed\n"; return 0; }
    catch (const std::exception& error) { std::cerr << "engine check " << checks << ": " << error.what() << '\n'; return 1; }
}
