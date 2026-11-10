#include "study.hpp"

#include <algorithm>
#include <limits>
#include <queue>
#include <set>
#include <sstream>

namespace grid {
namespace {
constexpr std::streamoff result_byte_limit = 32 * 1024 * 1024;
std::string number(const Rational& value) { return json_quote(value.str()); }
std::string optional_number(const std::optional<Rational>& value) { return value ? number(*value) : "null"; }
std::string optional_time(const std::optional<std::int64_t>& value) { return value ? std::to_string(*value) : "null"; }
std::string event_json(const PortfolioEvent& event) {
    return "{\"kind\":" + json_quote(event.kind) + ",\"tier\":" + json_quote(event.tier) + ",\"key\":" + json_quote(event.key) +
        ",\"reason\":" + json_quote(event.reason) + ",\"timestamp_ms\":" + std::to_string(event.timestamp) +
        ",\"cycle_number\":" + std::to_string(event.cycle_number) + ",\"baseline\":" + number(event.baseline) +
        ",\"original_funding\":" + number(event.original_funding) + ",\"raw_nav\":" + optional_number(event.raw_nav) +
        ",\"net_nav\":" + optional_number(event.net_nav) + ",\"trigger_ms\":" + optional_time(event.trigger_ms) +
        ",\"trigger_nav\":" + optional_number(event.trigger_nav) + ",\"trigger_baseline\":" + optional_number(event.trigger_baseline) +
        ",\"completion_ms\":" + optional_time(event.completion_ms) + ",\"completion_nav\":" + optional_number(event.completion_nav) + '}';
}
Rational ratio_display(const Rational& value) {
    const bool negative = value.sign() < 0;
    const Rational absolute = negative ? Rational() - value : value;
    const auto rounded = absolute.rounded(Rational("1e-18"), false);
    return negative ? Rational() - rounded : rounded;
}
struct Curve {
    Rational initial, peak, max_drawdown;
    std::optional<Rational> ending;
    std::uint64_t known = 0, unknown = 0;
    std::optional<std::int64_t> below_since, last_ms;
    std::int64_t observed_underwater_ms = 0, longest_observed_underwater_span_ms = 0;
    bool preceding_below = false, preceding_known = false;
    explicit Curve(Rational funding) : initial(funding), peak(std::move(funding)) {}
    void sample(const std::optional<Rational>& nav, std::int64_t now) {
        ending = nav;
        if (!nav) { ++unknown; preceding_known = false; preceding_below = false; below_since.reset(); last_ms = now; return; }
        const bool adjacent_known = last_ms && preceding_known && now - *last_ms == minute_ms;
        if (!adjacent_known) below_since.reset();
        ++known;
        if (*nav >= peak) {
            peak = *nav;
            if (below_since) longest_observed_underwater_span_ms = std::max(longest_observed_underwater_span_ms, now - *below_since);
            below_since.reset();
        } else {
            if (!below_since) below_since = adjacent_known ? *last_ms : now;
            longest_observed_underwater_span_ms = std::max(longest_observed_underwater_span_ms, now - *below_since);
        }
        if (peak.sign() > 0) max_drawdown = max(max_drawdown, (peak - *nav) / peak);
        if (last_ms && preceding_known && preceding_below && now - *last_ms == minute_ms)
            observed_underwater_ms = checked_add(observed_underwater_ms, minute_ms);
        preceding_below = *nav < peak; preceding_known = true; last_ms = now;
    }
    std::string json() const {
        const auto gain = ending ? std::optional<Rational>(*ending - initial) : std::nullopt;
        const auto rate = gain && initial.sign() > 0 ? std::optional<Rational>(ratio_display(*gain / initial)) : std::nullopt;
        return "{\"beginning_nav\":" + number(initial) + ",\"ending_nav\":" + optional_number(ending) +
            ",\"net_pnl\":" + optional_number(gain) + ",\"net_return\":" + optional_number(rate) +
            ",\"max_observed_drawdown\":" + number(ratio_display(max_drawdown)) +
            ",\"known_samples\":" + std::to_string(known) + ",\"unknown_samples\":" + std::to_string(unknown) +
            ",\"known_observed_underwater_ms\":" + std::to_string(observed_underwater_ms) +
            ",\"longest_observed_underwater_span_ms\":" + std::to_string(longest_observed_underwater_span_ms) + '}';
    }
};
struct AllocationStats {
    Curve raw, net, hold_raw, hold_net;
    std::uint64_t processed = 0;
    std::optional<std::int64_t> first_ms, last_ms;
    std::uint64_t missing_minutes = 0, observed_inventory_samples = 0;
    Rational max_position_cost, max_reserved_quote;
    Rational observed_position_cost_sum, observed_reserved_quote_sum;
    std::map<std::int64_t, std::int64_t> lot_opened;
    std::int64_t longest_observed_lot_hold_ms = 0;
    std::optional<EngineState> hold;
    std::string hold_reason;
    AllocationNav last_nav, last_hold_nav;
    explicit AllocationStats(const Rational& funding) : raw(funding), net(funding), hold_raw(funding), hold_net(funding) {}
};
struct SourceCursor {
    std::unique_ptr<RawReader> reader;
    std::optional<Bar> next;
};
EngineState buy_hold(const EngineState& initial, const EngineContext& context, const Bar& first,
                      const Rational& spread, std::string& reason) {
    EngineState result = initial;
    const auto& market = context.market;
    Fee fee = context.buy_fee; fee.max_settlements = 1;
    const Rational price = (first.prices[3].exact() * (Rational(1) + spread)).rounded(market.price_tick, true);
    const Rational budget = max(Rational(), Rational(initial.quote_cash) * context.quote_quantum - context.policy.quote_reserve);
    Rational quantity = (budget / price).rounded(market.amount_tick, false);
    if (market.buy_amount.maximum) quantity = min(quantity, market.buy_amount.maximum->rounded(market.amount_tick, false));
    if (market.buy_cost.maximum) quantity = min(quantity, (*market.buy_cost.maximum / price).rounded(market.amount_tick, false));
    Rational lower, upper(quantity), chosen;
    bool complete = false;
    for (unsigned iteration = 0; iteration < 1024; ++iteration) {
        const Rational charged = fee.estimate(price, quantity);
        if (quantity * price + charged <= budget) { chosen = quantity; lower = quantity; }
        else upper = quantity;
        const Rational midpoint = ((lower + upper) / Rational(2)).rounded(market.amount_tick, false);
        if (midpoint <= lower || midpoint >= upper) { complete = true; break; }
        quantity = midpoint;
    }
    if (!complete) throw Error("baseline funding search exceeded bound");
    if (chosen.sign() <= 0 || !market.valid(price, chosen, true)) { reason = "minimum_or_budget"; return result; }
    const Rational charged = fee.estimate(price, chosen);
    const Rational cost = chosen * price + charged;
    result.quote_cash = checked_sub(result.quote_cash, cost.exact_units(context.quote_quantum));
    result.position_cost = cost.exact_units(context.quote_quantum);
    // Counterfactual ownership, explicitly not a fictitious exchange fill.
    result.lots.push_back({0, chosen.exact_units(context.base_quantum), price});
    return result;
}
std::string metrics_json(const SimulationMetrics& metrics) {
    std::string result = "{\"buy_fills\":" + std::to_string(metrics.fill_count - metrics.sell_count) +
        ",\"sell_fills\":" + std::to_string(metrics.sell_count) + ",\"fees_quote\":" + number(metrics.fees_quote) +
        ",\"fully_filled_ordinary_basket_sells\":" + std::to_string(metrics.ordinary_basket_sells) +
        ",\"portfolio_exit_sells\":" + std::to_string(metrics.portfolio_exit_sells) +
        ",\"turnover\":" + number(metrics.turnover) + ",\"gross_realized_profit\":" + number(metrics.gross_profit) +
        ",\"gross_realized_loss\":" + number(metrics.gross_loss) + ",\"profit_factor\":" +
        (metrics.sell_count >= 2 && metrics.gross_loss.sign() > 0 ? number(ratio_display(metrics.gross_profit / metrics.gross_loss)) : "null") +
        ",\"ambiguous_bars\":" + std::to_string(metrics.ambiguous_bars) +
        ",\"tick_incompatible_bars\":" + std::to_string(metrics.tick_incompatible_bars) +
        ",\"tick_incompatible_fields\":" + std::to_string(metrics.tick_incompatible_fields) +
        ",\"canceled_orders\":" + std::to_string(metrics.canceled_orders) +
        ",\"gap_refreshes\":" + std::to_string(metrics.gap_refreshes) +
        ",\"placed_orders\":" + std::to_string(metrics.placed_orders) + ",\"planning_notice_counts\":{";
    bool comma = false;
    for (const auto& [notice, occurrences] : metrics.notices) {
        if (comma) result += ',';
        result += json_quote(notice) + ':' + std::to_string(occurrences);
        comma = true;
    }
    return result + "}}";
}
void merge_metrics(SimulationMetrics& total, const SimulationMetrics& current) {
    total.fill_count += current.fill_count; total.sell_count += current.sell_count;
    total.ambiguous_bars += current.ambiguous_bars; total.tick_incompatible_bars += current.tick_incompatible_bars;
    total.tick_incompatible_fields += current.tick_incompatible_fields; total.canceled_orders += current.canceled_orders;
    total.placed_orders += current.placed_orders;
    total.gap_refreshes += current.gap_refreshes;
    total.ordinary_basket_sells += current.ordinary_basket_sells; total.portfolio_exit_sells += current.portfolio_exit_sells;
    total.fees_quote = total.fees_quote + current.fees_quote; total.turnover = total.turnover + current.turnover;
    total.gross_profit = total.gross_profit + current.gross_profit; total.gross_loss = total.gross_loss + current.gross_loss;
    for (const auto& [notice, occurrences] : current.notices) total.notices[notice] += occurrences;
}
std::optional<Rational> sum_known(const std::vector<AllocationNav>& values, const std::vector<std::size_t>& members, bool net) {
    Rational sum;
    for (const auto index : members) {
        const auto& value = net ? values[index].net : values[index].raw;
        if (!value) return std::nullopt;
        sum = sum + *value;
    }
    return sum;
}
} // namespace

void run_study(const StudyConfig& config, std::ostream& output) {
    const std::size_t count = config.sources.size();
    std::vector<Simulator> simulations;
    std::vector<AllocationStats> statistics;
    std::vector<Scope> scopes;
    std::vector<SourceCursor> cursors;
    std::map<std::string, std::vector<std::size_t>> exchanges;
    using Next = std::pair<std::int64_t, std::size_t>;
    std::priority_queue<Next, std::vector<Next>, std::greater<Next>> heap;
    for (std::size_t index = 0; index < count; ++index) {
        const auto& source = config.sources[index];
        EngineState state;
        state.scope = source.scope; state.quote_cash = config.initial_quote.exact_units(config.context.quote_quantum);
        state.config_revision = config.context.config_revision; state.generation = config.context.generation;
        simulations.emplace_back(state, config.context, config.model);
        statistics.emplace_back(config.initial_quote); scopes.push_back(source.scope);
        exchanges[source.scope.venue].push_back(index);
        SourceCursor cursor {std::make_unique<RawReader>(source.path, source.sha256), std::nullopt};
        Bar bar {};
        if (cursor.reader->next(bar)) { cursor.next = bar; heap.emplace(bar.timestamp, index); }
        cursors.push_back(std::move(cursor));
    }
    const Rational funding = config.initial_quote * Rational(static_cast<std::int64_t>(count));
    PortfolioCoordinator coordinator(config.portfolio, scopes, std::vector<Rational>(count, config.initial_quote));
    Curve portfolio_raw(funding), portfolio_net(funding), benchmark_raw(funding), benchmark_net(funding);
    std::map<std::string, Curve> exchange_raw, exchange_net, exchange_hold_raw, exchange_hold_net;
    for (const auto& [venue, members] : exchanges) {
        const auto venue_funding = config.initial_quote * Rational(static_cast<std::int64_t>(members.size()));
        exchange_raw.emplace(venue, Curve(venue_funding)); exchange_net.emplace(venue, Curve(venue_funding));
        exchange_hold_raw.emplace(venue, Curve(venue_funding)); exchange_hold_net.emplace(venue, Curve(venue_funding));
    }
    std::optional<std::int64_t> first_ms, last_ms;
    std::uint64_t groups = 0, complete_groups = 0, whole_sources_rows = 0, window_rows = 0;
    std::map<std::string, std::uint64_t> portfolio_events;
    std::ostringstream event_history, nav_samples;
    std::uint64_t event_count = 0, nav_sample_count = 0;
    std::optional<std::int64_t> sampled_day, sampled_ms;
    const auto sample_nav = [&](std::int64_t now) {
        if (++nav_sample_count > 100000) throw Error("NAV sample capacity exceeded; discard study result");
        if (nav_sample_count > 1) nav_samples << ',';
        nav_samples << "{\"timestamp_ms\":" << now << ",\"raw_nav\":" << optional_number(portfolio_raw.ending)
                    << ",\"net_nav\":" << optional_number(portfolio_net.ending)
                    << ",\"buy_hold_raw_nav\":" << optional_number(benchmark_raw.ending)
                    << ",\"buy_hold_net_nav\":" << optional_number(benchmark_net.ending)
                    << ",\"cash_benchmark\":" << number(funding) << '}';
        if (nav_samples.tellp() > result_byte_limit) throw Error("NAV sample bytes exceeded; discard study result");
        sampled_ms = now; sampled_day = now / 86400000;
    };
    std::vector<AllocationNav> final_nav(count), hold_nav(count);
    while (!heap.empty()) {
        const std::int64_t timestamp = heap.top().first;
        std::vector<std::size_t> members;
        while (!heap.empty() && heap.top().first == timestamp) { members.push_back(heap.top().second); heap.pop(); }
        const bool in_window = (!config.start_ms || timestamp >= *config.start_ms) && (!config.end_ms || timestamp < *config.end_ms);
        if (in_window) {
            if (!first_ms) first_ms = timestamp;
            last_ms = timestamp; ++groups;
            if (members.size() == count) ++complete_groups;
            const std::int64_t index = (timestamp - *first_ms) / minute_ms;
            std::vector<std::optional<Rational>> closes(count);
            for (const auto member : members) {
                auto& simulation = simulations[member]; auto& stats = statistics[member];
                const auto& bar = *cursors[member].next;
                if (!stats.first_ms) {
                    stats.first_ms = timestamp;
                    stats.hold = buy_hold(simulation.state, simulation.context, bar, config.model.spread, stats.hold_reason);
                }
                if (stats.last_ms) stats.missing_minutes += static_cast<std::uint64_t>((timestamp - *stats.last_ms) / minute_ms - 1);
                stats.last_ms = timestamp; ++stats.processed; ++window_rows;
                simulation.step(bar, index);
                closes[member] = bar.prices[3].exact();
                const auto cost = Rational(simulation.state.position_cost) * simulation.context.quote_quantum;
                stats.max_position_cost = max(stats.max_position_cost, cost);
                stats.max_reserved_quote = max(stats.max_reserved_quote,
                    Rational(reserved(simulation.state, simulation.context, "QUOTE")) * simulation.context.quote_quantum);
                if (simulation.state.owned_base() > 0) ++stats.observed_inventory_samples;
                std::set<std::int64_t> retained;
                for (const auto& lot : simulation.state.lots) {
                    retained.insert(lot.order_id); stats.lot_opened.try_emplace(lot.order_id, timestamp);
                    stats.longest_observed_lot_hold_ms = std::max(stats.longest_observed_lot_hold_ms, timestamp - stats.lot_opened.at(lot.order_id));
                }
                for (const auto& [id, opened] : stats.lot_opened) if (!retained.contains(id))
                    stats.longest_observed_lot_hold_ms = std::max(stats.longest_observed_lot_hold_ms, timestamp - opened);
                std::erase_if(stats.lot_opened, [&retained](const auto& item) { return !retained.contains(item.first); });
            }
            const auto now = checked_add(timestamp, minute_ms);
            std::vector<PortfolioAllocation> observations;
            observations.reserve(count);
            for (std::size_t member = 0; member < count; ++member)
                observations.push_back({&simulations[member].state, &simulations[member].context, closes[member],
                                        closes[member] ? std::optional(now) : std::nullopt});
            auto step = coordinator.evaluate(observations, now);
            final_nav = step.nav.allocations;
            portfolio_raw.sample(step.nav.raw, now); portfolio_net.sample(step.nav.net, now);
            Rational hold_raw_sum, hold_net_sum;
            bool hold_raw_known = true, hold_net_known = true;
            for (std::size_t member = 0; member < count; ++member) {
                auto& stats = statistics[member];
                stats.last_nav = final_nav[member];
                stats.raw.sample(final_nav[member].raw, now); stats.net.sample(final_nav[member].net, now);
                if (stats.hold) hold_nav[member] = allocation_nav({&*stats.hold, &simulations[member].context, closes[member],
                                                                   closes[member] ? std::optional(now) : std::nullopt}, config.portfolio, now);
                else { hold_nav[member].raw = config.initial_quote; hold_nav[member].net = config.initial_quote; }
                stats.last_hold_nav = hold_nav[member];
                stats.hold_raw.sample(hold_nav[member].raw, now); stats.hold_net.sample(hold_nav[member].net, now);
                if (hold_nav[member].raw) hold_raw_sum = hold_raw_sum + *hold_nav[member].raw; else hold_raw_known = false;
                if (hold_nav[member].net) hold_net_sum = hold_net_sum + *hold_nav[member].net; else hold_net_known = false;
            }
            benchmark_raw.sample(hold_raw_known ? std::optional(hold_raw_sum) : std::nullopt, now);
            benchmark_net.sample(hold_net_known ? std::optional(hold_net_sum) : std::nullopt, now);
            if (!sampled_day || now / 86400000 != *sampled_day) sample_nav(now);
            for (const auto& [venue, venue_members] : exchanges) {
                exchange_raw.at(venue).sample(sum_known(final_nav, venue_members, false), now);
                exchange_net.at(venue).sample(sum_known(final_nav, venue_members, true), now);
                exchange_hold_raw.at(venue).sample(sum_known(hold_nav, venue_members, false), now);
                exchange_hold_net.at(venue).sample(sum_known(hold_nav, venue_members, true), now);
            }
            for (const auto& event : step.events) {
                ++portfolio_events[event.tier + ":" + event.kind];
                if (event_count++ != 0) event_history << ',';
                event_history << event_json(event);
                if (event_history.tellp() > result_byte_limit) throw Error("cycle evidence bytes exceeded; discard study result");
            }
            for (auto& action : step.actions) {
                auto& simulation = simulations.at(action.allocation);
                simulation.accept(std::move(action.transition), index);
                auto& stats = statistics.at(action.allocation);
                stats.max_reserved_quote = max(stats.max_reserved_quote,
                    Rational(reserved(simulation.state, simulation.context, "QUOTE")) * simulation.context.quote_quantum);
            }
            for (const auto member : members) {
                const auto& simulation = simulations[member]; auto& stats = statistics[member];
                stats.observed_position_cost_sum = stats.observed_position_cost_sum + Rational(simulation.state.position_cost) * simulation.context.quote_quantum;
                stats.observed_reserved_quote_sum = stats.observed_reserved_quote_sum + Rational(reserved(simulation.state, simulation.context, "QUOTE")) * simulation.context.quote_quantum;
            }
        }
        for (const auto member : members) {
            ++whole_sources_rows;
            Bar next {};
            if (cursors[member].reader->next(next)) { cursors[member].next = next; heap.emplace(next.timestamp, member); }
            else cursors[member].next.reset();
        }
    }
    if (!first_ms || !last_ms) throw Error("study interval has no valid observations");
    if (sampled_ms != checked_add(*last_ms, minute_ms)) sample_nav(checked_add(*last_ms, minute_ms));
    const auto declared_start = config.start_ms.value_or(*first_ms);
    const auto declared_end = config.end_ms.value_or(checked_add(*last_ms, minute_ms));
    const auto calendar_minutes = (declared_end - declared_start) / minute_ms;
    if (declared_end > checked_add(*last_ms, minute_ms)) {
        // Assessment of an unobserved declared endpoint, never a synthetic bar
        // or portfolio signal. Cash remains known; owned assets have no mark.
        for (std::size_t index = 0; index < count; ++index) {
            auto& stats = statistics[index]; const auto& simulation = simulations[index];
            if (!stats.hold) throw Error("selected market has no observations in study interval");
            final_nav[index] = allocation_nav({&simulation.state, &simulation.context, std::nullopt, std::nullopt}, config.portfolio, declared_end);
            stats.last_nav = final_nav[index]; stats.raw.ending = final_nav[index].raw; stats.net.ending = final_nav[index].net;
            hold_nav[index] = allocation_nav({&*stats.hold, &simulation.context, std::nullopt, std::nullopt}, config.portfolio, declared_end);
            stats.last_hold_nav = hold_nav[index]; stats.hold_raw.ending = hold_nav[index].raw; stats.hold_net.ending = hold_nav[index].net;
        }
        std::vector<std::size_t> all;
        for (std::size_t index = 0; index < count; ++index) all.push_back(index);
        portfolio_raw.ending = sum_known(final_nav, all, false); portfolio_net.ending = sum_known(final_nav, all, true);
        benchmark_raw.ending = sum_known(hold_nav, all, false); benchmark_net.ending = sum_known(hold_nav, all, true);
        for (const auto& [venue, members] : exchanges) {
            exchange_raw.at(venue).ending = sum_known(final_nav, members, false);
            exchange_net.at(venue).ending = sum_known(final_nav, members, true);
            exchange_hold_raw.at(venue).ending = sum_known(hold_nav, members, false);
            exchange_hold_net.at(venue).ending = sum_known(hold_nav, members, true);
        }
    }
    for (std::size_t index = 0; index < count; ++index) {
        const auto& scan = cursors[index].reader->stats();
        if (!scan.complete || scan.rejected != 0 || scan.sha256 != config.sources[index].sha256)
            throw Error("source integrity gate failed; discard entire study result");
        if (statistics[index].processed == 0) throw Error("selected market has no observations in study interval");
    }
    SimulationMetrics combined;
    Rational total_cash, total_cost, total_realized, total_reserved;
    for (const auto& simulation : simulations) {
        merge_metrics(combined, simulation.metrics);
        total_cash = total_cash + Rational(simulation.state.quote_cash) * simulation.context.quote_quantum;
        total_cost = total_cost + Rational(simulation.state.position_cost) * simulation.context.quote_quantum;
        total_realized = total_realized + Rational(simulation.state.realized_pnl) * simulation.context.quote_quantum;
        total_reserved = total_reserved + Rational(reserved(simulation.state, simulation.context, "QUOTE")) * simulation.context.quote_quantum;
    }
    if (total_cash + total_cost != funding + total_realized) throw Error("portfolio quote/cost/PnL conservation failed");
    std::ostringstream result;
    result << "{\"schema\":\"grid-module1-replay-v1\",\"input_sha256\":" << json_quote(config.input_sha256)
           << ",\"source_status\":\"UNVERIFIED SOURCE / ASSUMPTION-BASED\",\"strict_historical_performance\":\"NOT_COMPUTABLE\""
           << ",\"start_ms\":" << *first_ms << ",\"end_ms_exclusive\":" << *last_ms + minute_ms
           << ",\"declared_start_ms\":" << declared_start << ",\"declared_end_ms_exclusive\":" << declared_end
           << ",\"ending_nav_timestamp_ms\":" << declared_end
           << ",\"source_rows\":" << whole_sources_rows << ",\"processed_rows\":" << window_rows
           << ",\"observed_timestamp_groups\":" << groups << ",\"complete_timestamp_groups\":" << complete_groups
           << ",\"calendar_minutes\":" << calendar_minutes << ",\"sources\":[";
    for (std::size_t index = 0; index < count; ++index) {
        if (index != 0) result << ',';
        const auto& scan = cursors[index].reader->stats();
        result << "{\"path\":" << json_quote(config.sources[index].path) << ",\"sha256\":" << json_quote(scan.sha256)
               << ",\"complete\":true,\"bytes\":" << scan.bytes << ",\"records\":" << scan.records
               << ",\"processed_rows\":" << statistics[index].processed << ",\"missing_minutes\":" << scan.missing_minutes
               << ",\"window_missing_minutes\":" << statistics[index].missing_minutes
               << ",\"leading_missing_minutes\":" << (*statistics[index].first_ms - declared_start) / minute_ms
               << ",\"trailing_missing_minutes\":" << (declared_end - *statistics[index].last_ms) / minute_ms - 1 << '}';
    }
    result << "],\"portfolio\":{\"raw\":" << portfolio_raw.json() << ",\"net\":" << portfolio_net.json()
           << ",\"cash_benchmark\":" << number(funding) << ",\"buy_hold_raw\":" << benchmark_raw.json()
           << ",\"buy_hold_net\":" << benchmark_net.json() << ",\"metrics\":" << metrics_json(combined)
           << ",\"cash\":" << number(total_cash) << ",\"position_cost\":" << number(total_cost)
           << ",\"realized_pnl\":" << number(total_realized) << ",\"reserved_quote\":" << number(total_reserved)
           << ",\"raw_unrealized_pnl\":" << (portfolio_raw.ending ? number(*portfolio_raw.ending - total_cash - total_cost) : "null")
           << ",\"net_unrealized_pnl\":" << (portfolio_net.ending ? number(*portfolio_net.ending - total_cash - total_cost) : "null") << '}'
           << ",\"allocations\":[";
    for (std::size_t index = 0; index < count; ++index) {
        if (index != 0) result << ',';
        const auto& simulation = simulations[index]; const auto& stats = statistics[index];
        const auto cash = Rational(simulation.state.quote_cash) * simulation.context.quote_quantum;
        const auto cost = Rational(simulation.state.position_cost) * simulation.context.quote_quantum;
        const auto realized = Rational(simulation.state.realized_pnl) * simulation.context.quote_quantum;
        const auto reserve = Rational(reserved(simulation.state, simulation.context, "QUOTE")) * simulation.context.quote_quantum;
        if (cash + cost != config.initial_quote + realized) throw Error("allocation quote/cost/PnL conservation failed");
        std::uint64_t open = 0, unknown = 0;
        for (const auto& item : simulation.state.orders) if (!item.terminal()) { ++open; if (item.unknown) ++unknown; }
        result << "{\"venue\":" << json_quote(simulation.state.scope.venue) << ",\"symbol\":" << json_quote(simulation.state.scope.symbol)
               << ",\"allocation\":" << json_quote(simulation.state.scope.allocation) << ",\"raw\":" << stats.raw.json()
               << ",\"net\":" << stats.net.json() << ",\"buy_hold_raw\":" << stats.hold_raw.json()
               << ",\"buy_hold_net\":" << stats.hold_net.json() << ",\"buy_hold_reason\":" << json_quote(stats.hold_reason)
               << ",\"metrics\":" << metrics_json(simulation.metrics) << ",\"cash\":" << number(cash)
               << ",\"position_cost\":" << number(cost) << ",\"realized_pnl\":" << number(realized)
               << ",\"raw_unrealized_pnl\":" << (stats.raw.ending ? number(*stats.raw.ending - cash - cost) : "null")
               << ",\"net_unrealized_pnl\":" << (stats.net.ending ? number(*stats.net.ending - cash - cost) : "null")
               << ",\"owned_base\":" << number(Rational(simulation.state.owned_base()) * simulation.context.base_quantum)
               << ",\"sellable_base\":" << (stats.last_nav.net ? number(stats.last_nav.sellable) : "null")
               << ",\"residual_base\":" << (stats.last_nav.net ? number(stats.last_nav.residual) : "null")
               << ",\"liquidation_valuation_known\":" << (stats.last_nav.net ? "true" : "false")
               << ",\"valuation_reason\":" << json_quote(stats.last_nav.reason) << ",\"reserved_quote\":" << number(reserve)
               << ",\"max_position_cost\":" << number(stats.max_position_cost) << ",\"max_reserved_quote\":" << number(stats.max_reserved_quote)
               << ",\"observed_inventory_samples\":" << stats.observed_inventory_samples
               << ",\"observed_inventory_sample_fraction\":" << number(ratio_display(Rational(static_cast<std::int64_t>(stats.observed_inventory_samples)) / Rational(static_cast<std::int64_t>(stats.processed))))
               << ",\"mean_observed_cost_utilization\":" << number(ratio_display(stats.observed_position_cost_sum / (Rational(static_cast<std::int64_t>(stats.processed)) * config.initial_quote)))
               << ",\"mean_observed_committed_utilization\":" << number(ratio_display((stats.observed_position_cost_sum + stats.observed_reserved_quote_sum) / (Rational(static_cast<std::int64_t>(stats.processed)) * config.initial_quote)))
               << ",\"utilization_scope\":\"post-action cost and quote reservation divided by original allocation funding, sampled at this source's completed bars only\""
               << ",\"longest_observed_lot_hold_ms\":" << stats.longest_observed_lot_hold_ms
               << ",\"allocation_round_advances\":" << simulation.state.round_id - 1 << ",\"open_orders\":" << open
               << ",\"unknown_orders\":" << unknown << ",\"phase\":" << json_quote(simulation.state.phase)
               << ",\"mode\":" << json_quote(simulation.state.mode) << '}';
    }
    result << "],\"exchanges\":[";
    bool comma = false;
    for (const auto& [venue, members] : exchanges) {
        if (comma) result << ',';
        SimulationMetrics aggregate;
        Rational cash, cost, realized;
        for (auto member : members) {
            merge_metrics(aggregate, simulations[member].metrics);
            cash = cash + Rational(simulations[member].state.quote_cash) * simulations[member].context.quote_quantum;
            cost = cost + Rational(simulations[member].state.position_cost) * simulations[member].context.quote_quantum;
            realized = realized + Rational(simulations[member].state.realized_pnl) * simulations[member].context.quote_quantum;
        }
        result << "{\"venue\":" << json_quote(venue) << ",\"raw\":" << exchange_raw.at(venue).json()
               << ",\"net\":" << exchange_net.at(venue).json() << ",\"metrics\":" << metrics_json(aggregate)
               << ",\"buy_hold_raw\":" << exchange_hold_raw.at(venue).json() << ",\"buy_hold_net\":" << exchange_hold_net.at(venue).json()
               << ",\"cash_benchmark\":" << number(config.initial_quote * Rational(static_cast<std::int64_t>(members.size())))
               << ",\"cash\":" << number(cash) << ",\"position_cost\":" << number(cost) << ",\"realized_pnl\":" << number(realized) << '}';
        comma = true;
    }
    result << "],\"cycles\":["; comma = false;
    for (const auto& cycle : coordinator.cycles()) {
        if (comma) result << ',';
        result << "{\"tier\":" << json_quote(cycle.tier) << ",\"key\":" << json_quote(cycle.key) << ",\"phase\":" << json_quote(cycle.phase)
               << ",\"baseline\":" << number(cycle.baseline) << ",\"cycles\":" << cycle.cycles << ",\"triggers\":" << cycle.triggers
               << ",\"original_funding\":" << number(cycle.original_funding)
               << ",\"trigger_ms\":" << optional_time(cycle.trigger_ms) << ",\"completed_ms\":" << optional_time(cycle.completed_ms)
               << ",\"trigger_nav\":" << optional_number(cycle.trigger_nav) << ",\"trigger_raw_nav\":" << optional_number(cycle.trigger_raw_nav)
               << ",\"trigger_baseline\":" << optional_number(cycle.trigger_baseline)
               << ",\"completion_nav\":" << optional_number(cycle.completion_nav) << ",\"completion_raw_nav\":" << optional_number(cycle.completion_raw_nav)
               << ",\"completions\":" << cycle.completions << '}';
        comma = true;
    }
    result << "],\"exit_slots\":["; comma = false;
    for (const auto& slot : coordinator.slots()) {
        if (comma) result << ',';
        result << "{\"anchor_bid\":" << optional_number(slot.anchor_bid) << ",\"orders\":" << slot.orders
               << ",\"order_id\":" << optional_time(slot.order_id) << ",\"created_ms\":" << optional_time(slot.created_ms)
               << ",\"started_ms\":" << optional_time(slot.started_ms)
               << ",\"reason\":" << json_quote(slot.reason) << '}'; comma = true;
    }
    result << "],\"portfolio_events\":{"; comma = false;
    for (const auto& [kind, occurrences] : portfolio_events) { if (comma) result << ','; result << json_quote(kind) << ':' << occurrences; comma = true; }
    result << "},\"cycle_events\":[" << event_history.str() << "],\"nav_curve\":[" << nav_samples.str()
           << "],\"nav_sampling\":\"first observed group, first observed group in each new UTC day, final observed group; all drawdowns use every observed group\""
           << ",\"unobserved_timestamp_minutes\":" << static_cast<std::uint64_t>(calendar_minutes) - groups
           << ",\"sample_coverage_scope\":\"curve known/unknown samples count observed timestamp groups; calendar gaps and unobserved declared endpoint are separate\""
           << ",\"terminal_assessment\":{\"timestamp_ms\":" << declared_end
           << ",\"observed_timestamp_group\":" << (declared_end == checked_add(*last_ms, minute_ms) ? "true" : "false")
           << ",\"raw_nav\":" << optional_number(portfolio_raw.ending) << ",\"net_nav\":" << optional_number(portfolio_net.ending)
           << ",\"buy_hold_raw_nav\":" << optional_number(benchmark_raw.ending) << ",\"buy_hold_net_nav\":" << optional_number(benchmark_net.ending) << '}'
           << ",\"limitations\":[\"Hypothetical OHLC executions, not observed fills\",\"Unverified source identity and historical venue rules\","
           << "\"No volume, queue or depth evidence\",\"Drawdowns sampled at known closes; gaps retained\","
           << "\"Instant funded buy-hold is a counterfactual benchmark, not LIMIT execution evidence\"]}";
    if (result.tellp() >= result_byte_limit) throw Error("study JSON exceeds 32 MiB; discard entire result");
    output << result.str() << '\n';
    if (!output) throw Error("study output failed");
}
} // namespace grid
