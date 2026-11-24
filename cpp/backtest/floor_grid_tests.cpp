#include "portfolio.hpp"
#include "simulator.hpp"

#include <algorithm>
#include <bit>
#include <iostream>

namespace {
using grid::Rational;
Rational q(const char* s) { return Rational(s); }
unsigned checks = 0;
void check(bool value, const char* message) { ++checks; if (!value) throw grid::Error(message); }
grid::EngineContext context() {
    grid::EngineContext c;
    c.market = {q(".000001"), q(".001"), {q(".000001"), q("1000000")},
                {q(".001"), std::nullopt}, {q("5"), std::nullopt},
                {q(".001"), std::nullopt}, {q("5"), std::nullopt}};
    auto& p = c.policy;
    p.spacing = q(".01"); p.net_target = q(".01"); p.quote_reserve = q("100");
    p.max_quote_committed = q("900"); p.lower_price = q(".000001"); p.upper_price = q("1000000");
    p.cost_quantum = q("1e-12"); p.log_percent_grid = true; p.buy_floor_fraction = q(".6");
    p.max_lots = 10000; p.max_open_orders = 10001; p.max_source_age_ms = 60000;
    c.buy_fee = {"QUOTE", q(".002"), q(".00000001"), std::nullopt, 64}; c.sell_fee = c.buy_fee;
    c.entry_policy = "no_seed_lower"; c.ladder_policy = "ladder_infinite";
    c.book = grid::Book{q("100"), q("100"), 0, 1};
    return c;
}
grid::EngineState initial(const grid::EngineContext& c, const char* venue = "htx", const char* cash = "1000") {
    grid::EngineState s; s.scope = {venue, "research", "BTC/USDT", venue, "grid", "cycle"};
    s.quote_cash = q(cash).exact_units(c.quote_quantum); return s;
}
grid::Bar bar(std::int64_t index, float high, float low, float close) {
    grid::Bar b{}; b.timestamp = (index + 1) * grid::minute_ms;
    const float prices[] = {close, high, low, close};
    for (unsigned i = 0; i < 4; ++i) b.prices[i] = {std::bit_cast<std::uint32_t>(prices[i])};
    return b;
}
void prices_and_atomicity() {
    auto c = context();
    auto prices = grid::floor_grid_prices(q("100"), c);
    check(prices.size() == 92, "one-percent grid reaches the sixty-percent floor in 92 orders");
    const char* first[] = {"99", "98.01", "97.0299", "96.059601"};
    for (unsigned i = 0; i < 4; ++i) check(prices[i] == q(first[i]), "hand-calculated logarithmic level");
    check(prices.back() == q("40"), "last level reaches the exact representable floor");
    for (std::size_t i = 1; i < prices.size(); ++i)
        check(prices[i] < prices[i-1] && prices[i] >= q("40"), "all prices distinct and inside floor");
    c.policy.buy_floor_fraction = q(".025");
    prices = grid::floor_grid_prices(q("100"), c);
    check(prices == std::vector<Rational>{q("99"), q("98.01"), q("97.5")}, "short final interval lands exactly on floor");
    c.policy.buy_floor_fraction = q(".0199");
    check(grid::floor_grid_prices(q("100"), c).size() == 2, "exact level at floor is not duplicated");
    c = context(); c.market.price_tick = q(".01");
    check(grid::floor_grid_prices(q("100.003"), c).back() == q("40.01"), "tick rounding never goes below mathematical floor");
    c = context();
    check(grid::floor_grid_prices(q("99.99999999999999999999999999999999999999"), c).front() == q("98.999999"),
          "exact fallback resolves a price just below a tick boundary");
    check(grid::floor_grid_prices(q("100.00000000000000000000000000000000000001"), c).front() == q("99"),
          "bounded evaluation resolves a price just above a tick boundary");
    for (unsigned kind = 0; kind < 7; ++kind) {
        c = context(); auto s = initial(c);
        if (kind == 0) { s.quote_cash = q("110").exact_units(c.quote_quantum); }
        if (kind == 1) c.policy.max_quote_committed = q("10");
        if (kind == 2) c.policy.max_lots = 2;
        if (kind == 3) { c.market.price_tick = q("1"); c.policy.spacing = q(".000001"); }
        if (kind == 4) {
            c.buy_fee = {"TOKEN", q(".002"), q(".00000001"), q("2"), 64}; c.sell_fee = c.buy_fee;
            s.fee_balances["TOKEN"] = q(".02").exact_units(c.token_quantum);
        }
        if (kind == 5) c.policy.upper_price = q("60");
        if (kind == 6) c.market.buy_amount.maximum = q(".1");
        const auto result = grid::reduce(s, grid::StartEvent{s.scope}, c);
        check(!result.accepted && result.commands.size() == 1 && result.commands[0].type == "Reconcile",
              "whole grid refuses inadequate funding, commitment, capacity, duplicate ticks or market constraints");
        check(result.state.orders.empty() && !result.state.anchor && !result.state.entry_reference &&
              result.state.quote_cash == s.quote_cash && result.state.fee_balances == s.fee_balances &&
              grid::reserved(result.state, c, "QUOTE") == 0,
              "refusal publishes no partial order grid, reservation or anchor");
    }
}
void full_placement_and_fills() {
    auto c = context(); grid::Simulator sim(initial(c), c, {"strict_skip_ambiguous", 1, q("0"), false});
    grid::Simulator unfunded(initial(c, "htx", "110"), c, {"strict_skip_ambiguous", 1, q("0"), false});
    bool refused = false;
    try { unfunded.step(bar(0, 100, 100, 100), 0); }
    catch (const grid::Error& e) { refused = std::string(e.what()).find("htx:BTC/USDT: insufficient allocated quote") != std::string::npos; }
    check(refused, "public replay reports the actual grid refusal and market");
    check(unfunded.state.orders.empty() && grid::reserved(unfunded.state, c, "QUOTE") == 0,
          "public replay refusal commits no partial orders or reservations");
    sim.step(bar(0, 100, 100, 100), 0);
    check(sim.metrics.fill_count == 0 && sim.state.orders.size() == 92, "all BUYs placed together without invented entry fills");
    check(sim.state.anchor == q("100") && !sim.state.seed_id && sim.state.full_grid_placed, "fixed close anchor and no seed BUY");
    for (const auto& order : sim.state.orders)
        check(order.buy && order.acknowledged && sim.eligible.at(order.id) == 1, "every minimum LIMIT has the same future eligibility");
    check(grid::reserved(sim.state, sim.context, "QUOTE") > 0 && grid::available_quote(sim.state, sim.context) >= 0,
          "all quote and fee reservations remain finitely funded");
    sim.step(bar(1, 108, 105, 108), 1);
    check(sim.state.anchor == q("100") && sim.state.orders.size() == 92 && sim.metrics.fill_count == 0,
          "later price movement does not reanchor or rebuild the pending grid");
    sim.step(bar(2, 100, 98.5F, 99), 2);
    check(sim.metrics.fill_count == 1 && sim.state.entry_reference == q("100"), "actual first fill retains the declared entry reference");
    check(std::count_if(sim.state.orders.begin(), sim.state.orders.end(), [](const auto& o) { return !o.buy && !o.terminal(); }) == 1,
          "first actual acquisition creates one aggregate SELL");
    sim.step(bar(3, 99, 39, 50), 3);
    check(sim.metrics.fill_count == 92 && sim.state.anchor == q("100"), "pre-existing lower LIMITs can fill together under the declared OHLC convention");
    check(std::count_if(sim.state.orders.begin(), sim.state.orders.end(), [](const auto& o) { return !o.buy && !o.terminal(); }) <= 1,
          "multiple BUY fills never overlap aggregate SELL replacements");
    check(std::none_of(sim.state.orders.begin(), sim.state.orders.end(), [](const auto& o) { return o.buy && !o.terminal(); }),
          "filled full grid never replenishes below its floor");
}
void scoped_exits() {
    auto c = context(); auto a = initial(c, "htx", "120"), b = initial(c, "gate", "80");
    grid::PortfolioPolicy p; p.exchange_enabled = true; p.restart_after_exit = false;
    grid::PortfolioCoordinator coordinator(p, {a.scope, b.scope}, {q("100"), q("100")});
    auto step = coordinator.evaluate({{&a,&c,std::nullopt,std::nullopt},{&b,&c,std::nullopt,std::nullopt}}, 0);
    check(coordinator.cycles()[0].triggers == 0, "a winning exchange cannot hide losses in global equity");
    check(std::count_if(step.events.begin(),step.events.end(),[](const auto& e){return e.kind=="trigger" && e.tier=="exchange";}) == 1,
          "only the profitable exchange triggers");
    for (auto& action : step.actions) if (action.allocation == 0) a = std::move(action.transition.state);
    c.now_ms = c.rules_observed_ms = c.fees_observed_ms = 60000;
    step = coordinator.evaluate({{&a,&c,std::nullopt,std::nullopt},{&b,&c,std::nullopt,std::nullopt}}, 60000);
    check(a.mode == "portfolio_exit" && a.orders.empty() && step.actions.empty(), "completed exchange stays stopped on later bars");
    b.quote_cash = q("130").exact_units(c.quote_quantum);
    step = coordinator.evaluate({{&a,&c,std::nullopt,std::nullopt},{&b,&c,std::nullopt,std::nullopt}}, 120000);
    check(coordinator.cycles()[0].triggers == 1, "global equity includes previously exited exchange cash");
    check(std::none_of(step.events.begin(),step.events.end(),[](const auto& e){return e.kind=="trigger" && e.tier=="exchange";}),
          "global trigger takes precedence over simultaneous exchange trigger");
    p.exchange_enabled = false; p.global_threshold = q(".5");
    a = initial(c, "htx", "120"); b = initial(c, "gate", "120");
    grid::PortfolioCoordinator custom(p, {a.scope,b.scope}, {q("100"),q("100")});
    (void)custom.evaluate({{&a,&c,std::nullopt,std::nullopt},{&b,&c,std::nullopt,std::nullopt}}, 0);
    check(custom.cycles()[0].triggers == 0, "configured global threshold replaces hard-coded ten percent");
    a.quote_cash = b.quote_cash = q("150").exact_units(c.quote_quantum);
    (void)custom.evaluate({{&a,&c,std::nullopt,std::nullopt},{&b,&c,std::nullopt,std::nullopt}}, 60000);
    check(custom.cycles()[0].triggers == 1, "global threshold triggers at exact equality");
}
void actual_limit_exit_stays_stopped() {
    auto c = context();
    c.book = grid::Book{q("120"), q("120"), 0, 1};
    auto s = initial(c, "htx", "0");
    s.lots.push_back({99, q("10").exact_units(c.base_quantum), q("100")});
    s.position_cost = q("1000").exact_units(c.quote_quantum); s.phase = "accumulating";
    grid::Simulator sim(s, c, {"strict_skip_ambiguous", 1, q("0"), false});
    grid::PortfolioPolicy p; p.exchange_enabled = true; p.restart_after_exit = false;
    grid::PortfolioCoordinator controller(p, {s.scope}, {q("1000")});
    auto step = controller.evaluate({{&sim.state,&sim.context,std::nullopt,std::nullopt}}, 0);
    check(step.events.empty(), "unknown owned-inventory mark authorizes neither exchange nor global TP");
    sim.context.now_ms = sim.context.rules_observed_ms = sim.context.fees_observed_ms = 1;
    sim.context.book->observed_ms = 1;
    step = controller.evaluate({{&sim.state,&sim.context,q("120"),1}}, 1);
    check(controller.cycles()[0].phase == "exiting" && controller.cycles()[0].triggers == 1,
          "net inventory equity authorizes global LIMIT exit before exchange exit");
    for (auto& action : step.actions) sim.accept(std::move(action.transition), 0);
    check(sim.state.orders.size() == 1 && !sim.state.orders.front().buy && sim.state.orders.front().role == "portfolio_exit",
          "take-profit creates one real pending LIMIT SELL and no BUY");
    check(controller.cycles()[0].completions == 0 && sim.metrics.fill_count == 0, "placement does not claim locked-in profit");
    sim.step(bar(0, 130, 120, 120), 1);
    step = controller.evaluate({{&sim.state,&sim.context,q("120"),sim.context.now_ms}}, sim.context.now_ms);
    for (auto& action : step.actions) sim.accept(std::move(action.transition), 1);
    check(controller.cycles()[0].phase == "complete" && sim.state.owned_base() == 0 && sim.metrics.sell_count == 1,
          "only actual reconciled LIMIT fills complete the exit");
    check(Rational(sim.state.quote_cash) * c.quote_quantum == q("1197.6"), "realized exit cash includes its actual fee exactly once");
    sim.step(bar(1, 140, 130, 135), 2);
    step = controller.evaluate({{&sim.state,&sim.context,q("135"),sim.context.now_ms}}, sim.context.now_ms);
    check(step.actions.empty() && sim.state.mode == "portfolio_exit" && sim.metrics.placed_orders == 1,
          "later rising prices cannot restart a completed take-profit scope");
}
}
int main() {
    try { prices_and_atomicity(); full_placement_and_fills(); scoped_exits(); actual_limit_exit_stays_stopped(); std::cout << checks << " logarithmic floor grid checks passed\n"; return 0; }
    catch (const std::exception& e) { std::cerr << "floor grid check " << checks << ": " << e.what() << '\n'; return 1; }
}
