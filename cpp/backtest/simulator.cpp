#include "simulator.hpp"

#include <algorithm>
#include <deque>
#include <set>
#include <sstream>

namespace grid {
namespace {
Rational base_value(std::int64_t units, const EngineContext& context) { return Rational(units) * context.base_quantum; }
Rational quote_value(std::int64_t units, const EngineContext& context) { return Rational(units) * context.quote_quantum; }
} // namespace
Simulator::Simulator(EngineState initial, EngineContext facts, BarModel assumptions)
    : state(std::move(initial)), context(std::move(facts)), model(std::move(assumptions)) {
    if (model.model != "strict_skip_ambiguous" && model.model != "touch_buy_priority" && model.model != "touch_sell_priority")
        throw Error("unsupported OHLC execution model");
    if (model.latency_bars < 1 || model.latency_bars > 1000000) throw Error("bounded positive latency required");
    if (model.spread.sign() < 0 || model.spread > context.policy.max_spread)
        throw Error("synthetic spread exceeds configured strategy admission");
}
std::vector<Event> Simulator::accept_once(Transition transition, std::int64_t index) {
    if (!transition.accepted || transition.state.reconcile_reason)
        throw Error("replay needs unsupported reconciliation");
    std::vector<Event> followups;
    state = std::move(transition.state);
    for (const auto& notice : transition.notices) ++metrics.notices[notice];
    if (metrics.notices.size() > 128) throw Error("research notice capacity exceeded");
    for (const auto& command : transition.commands) {
        if (command.type == "PlaceLimit") {
            if (!command.order) throw Error("placement command has no order");
            eligible[command.order->id] = checked_add(index, model.latency_bars);
            ++metrics.placed_orders;
            followups.emplace_back(ReportEvent {state.scope, command.order->id, "open", Rational(), false});
        } else if (command.type == "Cancel") cancel_due.try_emplace(command.order_id, checked_add(index, model.latency_bars));
        else throw Error("OHLC cannot supply reconciliation evidence");
    }
    return followups;
}
void Simulator::invariant() const {
    if (state.quote_cash < 0 || state.position_cost < 0 || state.owned_base() < 0)
        throw Error("negative synthetic ownership");
    if (reserved(state, context, "BASE") > state.owned_base() || reserved(state, context, "QUOTE") > state.quote_cash)
        throw Error("replay reservation exceeds finite ownership");
    for (const auto& [currency, balance] : state.fee_balances)
        if (reserved(state, context, currency) > balance) throw Error("replay reservation exceeds finite ownership");
}
void Simulator::drain(std::vector<Event> initial, std::int64_t index) {
    std::deque<Event> pending(initial.begin(), initial.end());
    unsigned processed = 0;
    while (!pending.empty()) {
        if (++processed > context.policy.max_open_orders * 4 + 16) throw Error("synthetic command cascade bound exceeded");
        const auto current = std::move(pending.front()); pending.pop_front();
        auto followups = accept_once(reduce(state, current, context), index);
        for (auto& next : followups) pending.push_back(std::move(next));
    }
    invariant();
}
void Simulator::event(const Event& current, std::int64_t index) { drain({current}, index); }
void Simulator::accept(Transition transition, std::int64_t index) { drain(accept_once(std::move(transition), index), index); }
void Simulator::compact() {
    std::set<std::int64_t> keep, working;
    std::optional<EngineOrder> lowest;
    for (const auto& item : state.orders) {
        if (!item.terminal()) { keep.insert(item.id); working.insert(item.id); }
        if (item.round_id == state.round_id && item.level && (!lowest || item.price < lowest->price)) lowest = item;
    }
    for (const auto& lot : state.lots) keep.insert(lot.order_id);
    if (state.seed_id) keep.insert(*state.seed_id);
    if (lowest) keep.insert(lowest->id);
    std::erase_if(state.orders, [&keep](const auto& item) { return !keep.contains(item.id); });
    std::erase_if(state.fills, [&working](const auto& fill) { return !working.contains(fill.order_id); });
    std::erase_if(eligible, [&working](const auto& entry) { return !working.contains(entry.first); });
    std::erase_if(cancel_due, [&working](const auto& entry) { return !working.contains(entry.first); });
    if (state.orders.size() > context.policy.max_order_records || state.fills.size() > context.policy.max_fill_records)
        throw Error("safe checkpoint exceeds state bounds");
}
void Simulator::step(const Bar& bar, std::int64_t index) {
    if (index < 0 || (last_timestamp_ && bar.timestamp <= *last_timestamp_)) throw Error("bar chronology must strictly advance");
    unsigned incompatible = 0;
    for (const auto& price : bar.prices) if (!price.tick_compatible(context.market.price_tick)) ++incompatible;
    if (incompatible != 0) {
        if (model.require_tick_compatibility) throw Error("float32 price incompatible with supplied market precision");
        ++metrics.tick_incompatible_bars; metrics.tick_incompatible_fields += incompatible;
    }
    const bool after_gap = last_timestamp_ && bar.timestamp - *last_timestamp_ > minute_ms;
    // Advance the real clock but preserve the preceding completed observation.
    // A missing-bar gap must make that old book stale, not permit replacement
    // orders using an artificially frozen observation clock.
    context.now_ms = bar.timestamp;
    const auto due = cancel_due;
    for (const auto& [id, when] : due) if (when <= index) {
        cancel_due.erase(id);
        const auto found = std::find_if(state.orders.begin(), state.orders.end(), [id](const auto& item) { return item.id == id && !item.terminal(); });
        if (found != state.orders.end()) {
            event(ReportEvent {state.scope, id, "canceled", base_value(found->filled, context), true}, index);
            ++metrics.canceled_orders;
        }
    }
    std::vector<EngineOrder> selected;
    bool buy_cross = false, sell_cross = false;
    const bool strict = model.model == "strict_skip_ambiguous";
    for (const auto& item : state.orders) if (!item.terminal()) {
        const auto found = eligible.find(item.id);
        if (found == eligible.end() || found->second > index) continue;
        const bool crossed = item.buy ? bar.prices[2].buy_crossed(item.price, strict)
                                      : bar.prices[1].sell_crossed(item.price, strict);
        if (crossed) { selected.push_back(item); buy_cross = buy_cross || item.buy; sell_cross = sell_cross || !item.buy; }
    }
    if (buy_cross && sell_cross) {
        ++metrics.ambiguous_bars;
        if (strict) selected.clear();
        else {
            const bool prefer_buy = model.model == "touch_buy_priority";
            std::erase_if(selected, [prefer_buy](const auto& item) { return item.buy != prefer_buy; });
        }
    }
    std::sort(selected.begin(), selected.end(), [](const auto& left, const auto& right) {
        if (left.price == right.price) return left.id < right.id;
        return left.buy ? left.price > right.price : left.price < right.price;
    });
    context.now_ms = checked_add(bar.timestamp, minute_ms);
    context.rules_observed_ms = context.fees_observed_ms = context.now_ms;
    const Rational close = bar.prices[3].exact();
    context.book = Book {close, close * (Rational(1) + model.spread), context.now_ms, context.generation};
    if (!begun_) { event(StartEvent {state.scope}, index); begun_ = true; }
    for (const auto& item : selected) {
        Fee fee = item.fee_rule; fee.max_settlements = 1;
        const Rational quantity = base_value(item.remaining(), context);
        const Rational charged = fee.estimate(item.price, quantity);
        const Rational fee_quote = fee.quote_cost(charged, item.price);
        const auto before = state.realized_pnl;
        FillEvent fill {state.scope, "ohlc:" + std::to_string(index) + ':' + std::to_string(item.id), item.id,
                        quantity, item.price, std::vector<ActualFee>{{fee.currency, charged, fee_quote}}};
        event(fill, index);
        ++metrics.fill_count;
        metrics.turnover = metrics.turnover + quantity * item.price;
        metrics.fees[fee.currency] = metrics.fees[fee.currency] + charged;
        metrics.fees_quote = metrics.fees_quote + fee_quote;
        if (!item.buy) {
            ++metrics.sell_count;
            if (item.role == "portfolio_exit") ++metrics.portfolio_exit_sells;
            else ++metrics.ordinary_basket_sells;
            const Rational pnl = quote_value(checked_sub(state.realized_pnl, before), context);
            metrics.gross_profit = metrics.gross_profit + max(Rational(), pnl);
            metrics.gross_loss = metrics.gross_loss + max(Rational(), Rational() - pnl);
        }
    }
    if (after_gap && !state.reconcile_reason && std::none_of(state.orders.begin(), state.orders.end(), [](const auto& item) {
        return item.unknown || (item.terminal_amount && !item.terminal());
    })) {
        // This assertion covers only entirely known synthetic state. It cannot
        // clear an unknown submission, missing fill or fee-overrun halt. The
        // refreshed completed close can plan orders eligible on a future bar.
        event(ReconciledEvent {state.scope, context.config_revision, context.generation,
                              std::nullopt, state.reconcile_revision}, index);
        ++metrics.gap_refreshes;
        ++metrics.notices["post_gap_known_state_refresh"];
    }
    compact(); last_timestamp_ = bar.timestamp;
}
std::string Simulator::json() const {
    std::ostringstream out;
    out << "{\"after\":" << observation_json(state, context) << ",\"fill_count\":" << metrics.fill_count
        << ",\"sell_count\":" << metrics.sell_count << ",\"fees_quote\":" << json_quote(metrics.fees_quote.str())
        << ",\"turnover\":" << json_quote(metrics.turnover.str()) << ",\"gross_profit\":" << json_quote(metrics.gross_profit.str())
        << ",\"gross_loss\":" << json_quote(metrics.gross_loss.str()) << ",\"ambiguous_bars\":" << metrics.ambiguous_bars
        << ",\"fees\":{";
    bool comma = false;
    for (const auto& [currency, amount] : metrics.fees) { if (comma) out << ','; out << json_quote(currency) << ':' << json_quote(amount.str()); comma = true; }
    out << "},\"notices\":{"; comma = false;
    for (const auto& [notice, occurrences] : metrics.notices) { if (comma) out << ','; out << json_quote(notice) << ':' << occurrences; comma = true; }
    out << "}}"; return out.str();
}
} // namespace grid
