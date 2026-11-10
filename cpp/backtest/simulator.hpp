#pragma once

#include "engine.hpp"
#include "history.hpp"

namespace grid {
struct BarModel {
    std::string model = "strict_skip_ambiguous";
    std::int64_t latency_bars = 1;
    Rational spread;
    bool require_tick_compatibility = true;
};
struct SimulationMetrics {
    std::uint64_t fill_count = 0, sell_count = 0, ambiguous_bars = 0;
    std::uint64_t tick_incompatible_bars = 0, tick_incompatible_fields = 0;
    std::uint64_t canceled_orders = 0, placed_orders = 0;
    std::uint64_t ordinary_basket_sells = 0, portfolio_exit_sells = 0;
    std::uint64_t gap_refreshes = 0;
    Rational fees_quote, turnover, gross_profit, gross_loss;
    std::map<std::string, Rational> fees;
    std::map<std::string, std::uint64_t> notices;
};
class Simulator {
public:
    EngineState state;
    EngineContext context;
    BarModel model;
    std::map<std::int64_t, std::int64_t> eligible, cancel_due;
    SimulationMetrics metrics;
    Simulator(EngineState initial, EngineContext facts, BarModel assumptions);
    void event(const Event&, std::int64_t index);
    void accept(Transition, std::int64_t index);
    void step(const Bar&, std::int64_t index);
    [[nodiscard]] std::string json() const;
private:
    bool begun_ = false;
    std::optional<std::int64_t> last_timestamp_;
    void compact();
    [[nodiscard]] std::vector<Event> accept_once(Transition, std::int64_t index);
    void drain(std::vector<Event>, std::int64_t index);
    void invariant() const;
};
} // namespace grid
