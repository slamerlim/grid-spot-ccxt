#include "engine.hpp"

#include "history.hpp"

#include <algorithm>
#include <set>
#include <sstream>

namespace grid {
namespace {
Rational value(std::int64_t units, const Rational& quantum) { return Rational(units) * quantum; }
Rational base(std::int64_t units, const EngineContext& context) { return value(units, context.base_quantum); }
Rational quote(std::int64_t units, const EngineContext& context) { return value(units, context.quote_quantum); }
std::int64_t base_units(const Rational& amount, const EngineContext& context) { return amount.exact_units(context.base_quantum); }
std::int64_t quote_units(const Rational& amount, const EngineContext& context) { return amount.exact_units(context.quote_quantum); }
EngineOrder& order(EngineState& state, std::int64_t id) {
    for (auto& item : state.orders) if (item.id == id) return item;
    throw Error("unknown allocation order");
}
const EngineOrder& order(const EngineState& state, std::int64_t id) {
    for (const auto& item : state.orders) if (item.id == id) return item;
    throw Error("unknown allocation order");
}
std::optional<std::int64_t> active_sell(const EngineState& state) {
    std::optional<std::int64_t> result;
    for (const auto& item : state.orders) if (!item.terminal() && !item.buy) {
        if (result) throw Error("multiple possibly-active SELLs");
        result = item.id;
    }
    return result;
}
Transition block(EngineState state, std::string reason, bool accepted = false) {
    Transition result {std::move(state), {}, {reason}, accepted};
    if (!result.state.reconcile_reason) result.commands.push_back({"Reconcile", std::nullopt, 0, reason});
    if (result.state.reconcile_reason != reason)
        result.state.reconcile_revision = checked_add(result.state.reconcile_revision, 1);
    result.state.reconcile_reason = std::move(reason);
    return result;
}
void append(std::vector<Command>& target, std::vector<Command> source) {
    target.insert(target.end(), std::make_move_iterator(source.begin()), std::make_move_iterator(source.end()));
}
std::int64_t token_balance(const EngineState& state, std::string_view currency) {
    const auto found = state.fee_balances.find(std::string(currency));
    return found == state.fee_balances.end() ? 0 : found->second;
}
bool funding_shortfall(const EngineState& state, const EngineContext& context) {
    std::set<std::string> currencies;
    for (const auto& item : state.orders) if (!item.terminal())
        for (const auto& [currency, amount] : item.reserves) { (void)amount; currencies.insert(currency); }
    for (const auto& currency : currencies) {
        const auto funds = currency == "QUOTE" ? state.quote_cash : currency == "BASE" ? state.owned_base()
                                                                                        : token_balance(state, currency);
        if (reserved(state, context, currency) > funds) return true;
    }
    return false;
}
void cancel(EngineState& state, std::int64_t id, std::vector<Command>& commands) {
    auto& item = order(state, id);
    if (item.terminal() || item.cancel_requested) return;
    if (item.unknown || !item.acknowledged) {
        auto blocked = block(std::move(state), "cancel_requires_order_reconciliation", true);
        state = std::move(blocked.state); append(commands, std::move(blocked.commands)); return;
    }
    item.cancel_requested = true;
    commands.push_back({"Cancel", std::nullopt, id, {}});
}
void consume_lots(std::vector<EngineLot>& lots, std::int64_t amount) {
    std::vector<EngineLot> remaining;
    for (auto lot : lots) {
        const auto consumed = std::min(lot.quantity, amount);
        amount = checked_sub(amount, consumed);
        lot.quantity = checked_sub(lot.quantity, consumed);
        if (lot.quantity > 0) remaining.push_back(std::move(lot));
    }
    if (amount != 0) throw Error("SELL exceeds bot-owned base");
    lots = std::move(remaining);
}
EngineState apply_fill(const EngineState& original, const FillEvent& fill, const EngineContext& context) {
    const auto& old = order(original, fill.order_id);
    if (!fill.fees) throw Error("fill fee evidence missing");
    if (fill.fees->size() > 16) throw Error("fill fee count exceeds bound");
    for (const auto& fee : *fill.fees) {
        if (fee.currency.empty() || fee.currency.size() > 128) throw Error("invalid fee currency");
        if (fee.quote_value && fee.amount.sign() != fee.quote_value->sign())
            throw Error("fee conversion sign mismatch");
    }
    if (original.fills.size() >= context.policy.max_fill_records)
        throw Error("fill history capacity requires durable checkpoint");
    const auto filled_units = base_units(fill.amount, context);
    if (filled_units <= 0 || fill.price.sign() <= 0) throw Error("invalid positive fill amount or price");
    if (filled_units > old.remaining()) throw Error("fill exceeds order amount");
    if (old.terminal_amount && checked_add(old.filled, filled_units) > *old.terminal_amount)
        throw Error("fill exceeds reported terminal amount");
    if (old.terminal()) throw Error("new fill contradicts proven terminal order");
    if ((old.buy && fill.price > old.price) || (!old.buy && fill.price < old.price))
        throw Error("fill violates LIMIT price");
    EngineState state(original);
    Rational base_fee, quote_fee, token_cost;
    for (const auto& fee : *fill.fees) {
        if (fee.currency == "BASE") base_fee = base_fee + fee.amount;
        else if (fee.currency == "QUOTE") quote_fee = quote_fee + fee.amount;
        else {
            if (!fee.quote_value) throw Error("actual third-token fee conversion missing");
            token_cost = token_cost + *fee.quote_value;
            auto& balance = state.fee_balances[fee.currency];
            balance = checked_sub(balance, fee.amount.exact_units(context.token_quantum));
            if (balance < 0) throw Error("fee exceeds allocated token funds");
        }
    }
    const Rational gross = fill.amount * fill.price;
    const auto gross_units = quote_units(gross, context);
    const auto actual_quote_fee = quote_units(quote_fee, context);
    if (old.buy) {
        const auto quantity = base_units(fill.amount - base_fee, context);
        if (quantity <= 0) throw Error("nonpositive net BUY quantity");
        state.quote_cash = checked_sub(checked_sub(state.quote_cash, gross_units), actual_quote_fee);
        state.position_cost = checked_add(state.position_cost, quote_units(gross + quote_fee + token_cost, context));
        bool found = false;
        for (auto& lot : state.lots) if (lot.order_id == old.id) {
            const Rational weighted = (((base(lot.quantity, context) * lot.execution_price).decimal96() +
                                        (base(quantity, context) * fill.price).decimal96()).decimal96()
                                      / base(checked_add(lot.quantity, quantity), context)).decimal96();
            lot.quantity = checked_add(lot.quantity, quantity); lot.execution_price = weighted;
            found = true; break;
        }
        if (!found) state.lots.push_back({old.id, quantity, fill.price});
    } else {
        const auto quantity = base_units(fill.amount + base_fee, context);
        const auto owned = state.owned_base();
        if (quantity <= 0 || quantity > owned) throw Error("SELL plus fee exceeds bot-owned base");
        const auto basis = quantity == owned ? state.position_cost
            : quote_units((quote(state.position_cost, context) * base(quantity, context) / base(owned, context))
                          .rounded(context.policy.cost_quantum, false), context);
        state.quote_cash = checked_sub(checked_add(state.quote_cash, gross_units), actual_quote_fee);
        state.position_cost = checked_sub(state.position_cost, basis);
        state.realized_pnl = checked_add(state.realized_pnl,
            quote_units(gross - quote_fee - token_cost - quote(basis, context), context));
        consume_lots(state.lots, quantity);
        state.last_executed_sell_price = fill.price;
    }
    if (state.quote_cash < 0 || state.position_cost < 0) throw Error("negative allocated funds or cost basis");
    auto& updated = order(state, fill.order_id);
    updated.filled = checked_add(updated.filled, filled_units);
    updated.gross_cost = checked_add(updated.gross_cost, gross_units);
    updated.acknowledged = true; updated.unknown = false;
    state.fills.push_back(fill);
    state.sell_dirty = state.sell_dirty || old.buy;
    if (old.role == "ladder" && old.filled == 0) {
        if (state.ladder_credits >= 512) throw Error("integer outside supported range");
        ++state.ladder_credits;
    }
    if (!old.buy && updated.filled == updated.amount) { state.phase = "draining"; state.sell_dirty = false; }
    return state;
}
bool apply_report(EngineState& state, const ReportEvent& report, const EngineContext& context) {
    auto& item = order(state, report.order_id);
    const auto cumulative = base_units(report.cumulative, context);
    if (cumulative < 0) throw Error("invalid cumulative amount");
    if (cumulative > item.amount) throw Error("order report exceeds requested amount");
    if (report.status == "open") {
        if (item.terminal()) return false;
        if (cumulative > item.filled) throw Error("order report has unposted fills");
        const bool changed = !item.acknowledged || item.unknown;
        item.acknowledged = true; item.unknown = false; return changed;
    }
    if (report.status != "canceled" && report.status != "rejected" && report.status != "filled")
        throw Error("unsupported order status");
    if (cumulative < item.filled) throw Error("terminal report contradicts posted fills");
    if (report.status == "filled" && cumulative != item.amount) throw Error("FILLED report has incomplete amount");
    if (report.status == "rejected" && cumulative != 0) throw Error("REJECTED report contains executions");
    if (item.terminal()) {
        if (cumulative != item.filled) throw Error("conflicting terminal report");
        return false;
    }
    const bool changed = !item.acknowledged || item.unknown || item.terminal_amount != cumulative || item.terminal_proven != report.reconciled;
    item.acknowledged = true; item.unknown = false; item.terminal_amount = cumulative;
    item.terminal_proven = report.reconciled; return changed;
}
EngineOrder new_order(EngineState& state, const EngineContext& context, bool buy, std::string role,
                       const Rational& price, const Rational& amount, const std::map<std::string, Rational>& reserves,
                       std::optional<unsigned> level = {}, const Rational& acquisition_fee = Rational()) {
    const auto working_count = std::count_if(state.orders.begin(), state.orders.end(), [](const auto& item) { return !item.terminal(); });
    if (static_cast<std::size_t>(working_count) >= context.policy.max_open_orders) throw Error("open-order cap reached");
    if (state.orders.size() >= context.policy.max_order_records) throw Error("order history capacity requires durable checkpoint");
    if (!buy && active_sell(state)) throw Error("SELL slot already occupied");
    std::map<std::string, std::int64_t> integer_reserves;
    for (const auto& [currency, amount_reserved] : reserves) {
        const auto units = amount_reserved.exact_units(context.quantum(currency));
        if (units < 0) throw Error("negative order reserve");
        if (currency != "BASE" && currency != "QUOTE" &&
            units > checked_sub(token_balance(state, currency), reserved(state, context, currency)))
            throw Error("insufficient reserved fee-token funds");
        integer_reserves.emplace(currency, units);
    }
    EngineOrder item;
    item.id = state.next_id; item.round_id = state.round_id; item.buy = buy; item.role = std::move(role);
    item.price = price; item.amount = base_units(amount, context); item.reserves = std::move(integer_reserves);
    item.level = level; item.fee_rule = buy ? context.buy_fee : context.sell_fee;
    item.acquisition_fee = quote_units(acquisition_fee, context);
    item.config_revision = context.config_revision; item.generation = context.generation;
    if (item.amount <= 0 || price.sign() <= 0) throw Error("invalid order terms");
    const auto next_id = checked_add(state.next_id, 1);
    state.orders.push_back(item); state.next_id = next_id;
    return item;
}
Command buy(EngineState& state, const EngineContext& context, const Rational& price, std::optional<unsigned> level) {
    const auto& policy = context.policy;
    if (price < policy.lower_price || price > policy.upper_price) throw Error("BUY outside configured range");
    std::set<std::int64_t> lots;
    for (const auto& lot : state.lots) lots.insert(lot.order_id);
    for (const auto& item : state.orders) if (!item.terminal() && item.buy) lots.insert(item.id);
    if (lots.size() >= policy.max_lots) throw Error("maximum accumulated lots reached");
    const auto plan = minimum_buy(price, context.market, context.buy_fee, context.sell_fee, policy.spacing,
                                  policy.net_target, quote(available_quote(state, context), context));
    Rational pending;
    for (const auto& item : state.orders) if (!item.terminal() && item.buy)
        pending = pending + item.price * base(item.remaining(), context) + quote(item.acquisition_fee, context);
    if (quote(state.position_cost, context) + pending + plan.acquisition_cost > policy.max_quote_committed)
        throw Error("committed acquisition cost cap reached");
    const auto& sell_currency = context.sell_fee.currency;
    if (sell_currency != "BASE" && sell_currency != "QUOTE") {
        const auto needed = (plan.prospective_sell.fee + (context.buy_fee.currency == sell_currency ? plan.fee : Rational()))
                                .exact_units(context.token_quantum);
        if (needed > checked_sub(token_balance(state, sell_currency), reserved(state, context, sell_currency)))
            throw Error("insufficient fee-token funds for prospective SELL");
    }
    std::map<std::string, Rational> reserves { {"QUOTE", plan.quote_required} };
    if (context.buy_fee.currency != "BASE" && context.buy_fee.currency != "QUOTE")
        reserves.emplace(context.buy_fee.currency, plan.fee);
    const auto item = new_order(state, context, true, level ? "ladder" : "seed", price, plan.amount, reserves,
                                 level, plan.acquisition_cost - price * plan.amount);
    if (!level) { state.seed_id = item.id; state.phase = "seed_pending"; }
    else { state.next_level = *level + 1; --state.ladder_credits; }
    return {"PlaceLimit", item, 0, {}};
}
} // namespace

bool EngineContext::fresh(std::int64_t observed_ms) const { return observed_ms <= now_ms && now_ms - observed_ms <= policy.max_source_age_ms; }
const Rational& EngineContext::quantum(std::string_view currency) const {
    return currency == "BASE" ? base_quantum : currency == "QUOTE" ? quote_quantum : token_quantum;
}
bool EngineOrder::terminal() const { return filled == amount || (terminal_proven && terminal_amount == filled); }
std::int64_t EngineOrder::remaining() const { return checked_sub(amount, filled); }
std::int64_t EngineState::owned_base() const {
    std::int64_t sum = 0;
    for (const auto& lot : lots) sum = checked_add(sum, lot.quantity);
    return sum;
}
std::int64_t reserved(const EngineState& state, const EngineContext& context, std::string_view currency) {
    Rational total;
    for (const auto& item : state.orders) if (!item.terminal()) {
        const auto found = item.reserves.find(std::string(currency));
        if (found == item.reserves.end()) continue;
        Rational paid;
        for (const auto& fill : state.fills) if (fill.order_id == item.id && fill.fees)
            for (const auto& fee : *fill.fees) if (fee.currency == currency) paid = paid + fee.amount;
        const auto reserve = value(found->second, context.quantum(currency));
        const auto principal = item.buy ? "QUOTE" : "BASE";
        if (currency != principal) total = total + max(Rational(), reserve - max(Rational(), paid));
        else if (item.buy) {
            const auto unused = max(Rational(), reserve - base(item.amount, context) * item.price - max(Rational(), paid));
            total = total + base(item.remaining(), context) * item.price + unused;
        } else {
            const auto unused = max(Rational(), reserve - base(item.amount, context) - max(Rational(), paid));
            total = total + base(item.remaining(), context) + unused;
        }
    }
    return total.exact_units(context.quantum(currency));
}
std::int64_t available_quote(const EngineState& state, const EngineContext& context) {
    return std::max<std::int64_t>(0, checked_sub(checked_sub(state.quote_cash, reserved(state, context, "QUOTE")),
                                               context.policy.quote_reserve.exact_units(context.quote_quantum)));
}

namespace {
Transition plan(EngineState state, const EngineContext& context) {
    std::vector<Command> commands;
    std::vector<std::string> notices;
    const auto& policy = context.policy;
    const auto result = [&]() { return Transition {std::move(state), std::move(commands), std::move(notices), true}; };
    const auto cancel_working = [&](bool buys_only) {
        std::vector<std::int64_t> ids;
        for (const auto& item : state.orders) if (!item.terminal() && (!buys_only || item.buy)) ids.push_back(item.id);
        for (auto id : ids) cancel(state, id, commands);
    };
    if (state.mode == "hold_inventory") {
        cancel_working(false); notices.push_back("inventory held; reconciliation continues"); return result();
    }
    if (state.phase == "draining" || state.mode == "retiring" || state.mode == "portfolio_exit" || state.mode == "exit_blocked") {
        cancel_working(true);
        if (state.phase == "draining") {
            if (std::any_of(state.orders.begin(), state.orders.end(), [](const auto& item) { return item.buy && !item.terminal(); })) {
                notices.push_back("draining_unresolved_BUYs"); return result();
            }
            if (state.reconcile_reason) { notices.push_back(*state.reconcile_reason); return result(); }
            state.anchor.reset();
            if (!state.lots.empty()) {
                Rational weighted;
                for (const auto& lot : state.lots)
                    weighted = (weighted + (base(lot.quantity, context) * lot.execution_price).decimal96()).decimal96();
                state.anchor = (weighted / base(state.owned_base(), context)).decimal96();
            }
            state.round_id = checked_add(state.round_id, 1);
            state.phase = state.lots.empty() ? "inactive" : "accumulating";
            state.seed_id.reset(); state.next_level = 1;
            state.ladder_credits = state.lots.empty() ? 0 : policy.ladder_size;
            state.sell_dirty = !state.lots.empty(); state.replacements = 0;
        }
    }
    if (state.reconcile_reason) { notices.push_back(*state.reconcile_reason); return result(); }
    if (state.config_revision != context.config_revision || state.generation != context.generation) {
        auto blocked = block(std::move(state), "stale_config_or_market_generation", true);
        append(commands, std::move(blocked.commands)); blocked.commands = std::move(commands); return blocked;
    }
    if (!context.fresh(context.rules_observed_ms) || !context.fresh(context.fees_observed_ms)) {
        notices.push_back("stale_market_or_fee_facts"); return result();
    }
    if (!context.book || !context.fresh(context.book->observed_ms) || context.book->generation != context.generation) {
        notices.push_back("fresh eligible book required"); return result();
    }
    if (state.mode == "portfolio_exit" || state.mode == "exit_blocked") {
        notices.push_back("portfolio_supervision_required"); return result();
    }
    if (state.seed_id) {
        const auto& seed = order(state, *state.seed_id);
        if (seed.terminal() && seed.filled > 0 && !state.anchor) {
            state.anchor = (quote(seed.gross_cost, context) / base(seed.filled, context)).decimal96();
            state.phase = "accumulating"; state.ladder_credits = policy.ladder_size;
        }
    }
    const auto sell_id = active_sell(state);
    if (!state.lots.empty() && (!sell_id || state.sell_dirty)) {
        try {
            const auto target = aggregate_take_profit(base(state.owned_base(), context), quote(state.position_cost, context),
                                                       context.market, policy.spacing, policy.net_target, context.sell_fee);
            if (sell_id) {
                const auto& sell = order(state, *sell_id);
                if (target.amount != base(sell.remaining(), context) || target.price != sell.price) {
                    if (!sell.cancel_requested && state.replacements >= policy.max_replacements) {
                        auto blocked = block(std::move(state), "SELL replacement bound reached", true);
                        append(commands, std::move(blocked.commands)); blocked.commands = std::move(commands); return blocked;
                    }
                    const auto previous_commands = commands.size();
                    cancel(state, *sell_id, commands);
                    if (commands.size() > previous_commands && commands[previous_commands].type == "Cancel") ++state.replacements;
                    state.phase = "sell_replacing";
                } else state.sell_dirty = false;
            } else {
                std::map<std::string, Rational> reserves {{"BASE", target.reserved_base}};
                if (context.sell_fee.currency != "BASE" && context.sell_fee.currency != "QUOTE")
                    reserves.emplace(context.sell_fee.currency, target.fee);
                auto item = new_order(state, context, false, "take_profit", target.price, target.amount, reserves);
                commands.push_back({"PlaceLimit", std::move(item), 0, {}});
                state.sell_dirty = false; state.phase = state.anchor ? "accumulating" : "seed_pending";
            }
        } catch (const Error& error) { notices.push_back(error.what()); return result(); }
    }
    if (state.reconcile_reason || state.mode != "active") return result();
    if (!context.allow_new_exposure) { notices.push_back("risk blocks new exposure"); return result(); }
    if (state.phase == "sell_replacing") return result();
    if (state.seed_id && !order(state, *state.seed_id).terminal()) return result();
    if (state.lots.empty()) {
        if (state.seed_id) { notices.push_back("seed requires explicit new round"); return result(); }
        if (!context.book || !context.fresh(context.book->observed_ms) || context.book->generation != context.generation ||
            (context.book->ask - context.book->bid) / context.book->bid > policy.max_spread) {
            notices.push_back("fresh eligible book required"); return result();
        }
        try {
            Rational reference = policy.seed_at_ask ? context.book->ask : context.book->bid;
            if (state.round_id == 1 && context.entry_policy == "no_seed_lower")
                reference = reference / (Rational(1) + policy.spacing);
            else if (state.round_id > 1 && context.reentry_policy == "reentry_one_step_below_sell") {
                if (!state.last_executed_sell_price) throw Error("one-step reentry requires last executed SELL price");
                reference = min(reference, *state.last_executed_sell_price / (Rational(1) + policy.spacing));
            }
            const auto price = reference.rounded(context.market.price_tick, false);
            commands.push_back(buy(state, context, price, std::nullopt));
        } catch (const Error& error) { notices.push_back(error.what()); }
    } else if (state.anchor) {
        const auto working = static_cast<unsigned>(std::count_if(state.orders.begin(), state.orders.end(),
                                                    [](const auto& item) { return item.buy && !item.terminal(); }));
        const auto count = std::min(state.ladder_credits, policy.ladder_size > working ? policy.ladder_size - working : 0U);
        for (unsigned index = 0; index < count; ++index) {
            try {
                Rational price = grid_prices(*state.anchor, policy.spacing, {state.next_level}, context.market).front();
                if (context.ladder_policy == "ladder_one") {
                    if (policy.ladder_size != 1) throw Error("single ladder requires exactly one pending slot");
                    price = min(price, (context.book->bid / (Rational(1) + policy.spacing)).rounded(context.market.price_tick, false));
                }
                for (const auto& item : state.orders) if (item.round_id == state.round_id && item.level && price >= item.price)
                    throw Error("grid levels collapse after quantization");
                commands.push_back(buy(state, context, price, state.next_level));
            } catch (const Error& error) { notices.push_back(error.what()); break; }
        }
    }
    return result();
}
Transition fee_breach(EngineState state, const EngineContext& context, std::string reason) {
    auto blocked = block(std::move(state), reason, true);
    state = std::move(blocked.state);
    auto commands = std::move(blocked.commands);
    if (commands.empty()) commands.push_back({"Reconcile", std::nullopt, 0, std::move(reason)});
    if (funding_shortfall(state, context)) {
        std::vector<std::int64_t> ids;
        for (const auto& item : state.orders) if (!item.terminal() && item.acknowledged && !item.unknown) ids.push_back(item.id);
        for (auto id : ids) cancel(state, id, commands);
    }
    auto planned = plan(state, context);
    append(commands, std::move(planned.commands)); planned.commands = std::move(commands); return planned;
}
} // namespace

Transition reduce(EngineState state, const Event& event, const EngineContext& context) {
    const bool same_scope = std::visit([&state](const auto& item) { return item.scope == state.scope; }, event);
    if (!same_scope) return {std::move(state), {}, {"scope mismatch"}, false};
    if (state.fee_balances.contains("BASE") || state.fee_balances.contains("QUOTE"))
        return block(std::move(state), "fee-token funds overlap principal funds");
    bool applied = false;
    try {
        (void)active_sell(state);
        if (const auto* fill = std::get_if<FillEvent>(&event)) {
            for (const auto& previous : state.fills) if (previous.trade_id == fill->trade_id) {
                if (previous == *fill) return {std::move(state), {}, {}, true};
                return block(std::move(state), "conflicting duplicate fill");
            }
            state = apply_fill(state, *fill, context); applied = true;
            const auto& item = order(state, fill->order_id);
            unsigned settlements = 0;
            std::map<std::string, Rational> totals;
            for (const auto& posted : state.fills) if (posted.order_id == item.id) {
                ++settlements;
                if (posted.fees) for (const auto& fee : *posted.fees) totals[fee.currency] = totals[fee.currency] + fee.amount;
            }
            if (settlements > item.fee_rule.max_settlements)
                return fee_breach(state, context, "fee settlement bound exceeded");
            const auto expected = item.fee_rule.estimate(item.price, base(item.amount, context));
            for (const auto& [currency, amount] : totals)
                if (amount > (currency == item.fee_rule.currency ? expected : Rational()))
                    return fee_breach(state, context, "actual fee exceeds forecast");
        } else if (const auto* report = std::get_if<ReportEvent>(&event)) {
            if (!apply_report(state, *report, context)) return {std::move(state), {}, {}, true};
            applied = true;
            const auto& reported = order(state, report->order_id);
            if (reported.terminal_amount && !reported.terminal())
                return block(std::move(state), "terminal report requires reconciled trades", true);
        } else if (const auto* unknown = std::get_if<UnknownEvent>(&event)) {
            auto& item = order(state, unknown->order_id);
            if (item.terminal()) return {std::move(state), {}, {}, true};
            item.unknown = true; return block(std::move(state), "submission outcome unknown", true);
        } else if (const auto* ack = std::get_if<CancelAckEvent>(&event)) {
            (void)order(state, ack->order_id);
            return {std::move(state), {}, {"cancel acknowledgement is not terminal proof"}, true};
        } else if (const auto* control = std::get_if<ControlEvent>(&event)) {
            if (control->revision < 1 || (control->mode != "active" && control->mode != "hold_inventory" &&
                control->mode != "paused" && control->mode != "retiring" && control->mode != "portfolio_exit" &&
                control->mode != "exit_blocked")) throw Error("invalid control revision or mode");
            if (control->revision < state.control_revision) return {std::move(state), {}, {"stale control revision"}, true};
            if (control->revision == state.control_revision) {
                if (control->mode == state.mode) return {std::move(state), {}, {}, true};
                return block(std::move(state), "conflicting control revision");
            }
            state.mode = control->mode; state.control_revision = control->revision; applied = true;
        } else if (const auto* reconciled = std::get_if<ReconciledEvent>(&event)) {
            if (reconciled->revision != state.reconcile_revision)
                return {std::move(state), {}, {"stale reconciliation revision"}, false};
            if (reconciled->expected_reason != state.reconcile_reason)
                return {std::move(state), {}, {"stale reconciliation assertion"}, false};
            const bool uncertain = std::any_of(state.orders.begin(), state.orders.end(), [](const auto& item) {
                return !item.terminal() && (item.unknown || (item.terminal_amount && !item.terminal()));
            });
            if (reconciled->config_revision != context.config_revision || reconciled->generation != context.generation || uncertain)
                return block(std::move(state), "reconciliation incomplete");
            if (funding_shortfall(state, context)) return block(std::move(state), "outstanding reservation exceeds allocated funds");
            state.reconcile_reason.reset();
            state.sell_dirty = state.sell_dirty || (!state.lots.empty() &&
                (state.config_revision != reconciled->config_revision || state.generation != reconciled->generation));
            state.config_revision = reconciled->config_revision; state.generation = reconciled->generation; applied = true;
        } else if (std::holds_alternative<StartEvent>(event)) {
            if (state.phase != "inactive" || !state.orders.empty()) return {std::move(state), {}, {}, true};
        }
        return plan(state, context);
    } catch (const Error& error) { return block(std::move(state), error.what(), applied); }
}

Transition request_cancel(EngineState state, std::int64_t order_id) {
    Transition result {std::move(state), {}, {}, true};
    cancel(result.state, order_id, result.commands);
    return result;
}
Transition place_exit(EngineState state, const EngineContext& context, const Rational& price, const Rational& amount) {
    if (state.mode != "portfolio_exit" && state.mode != "exit_blocked") throw Error("portfolio exit is not latched");
    if (state.reconcile_reason) throw Error("portfolio exit requires complete reconciliation");
    if (std::any_of(state.orders.begin(), state.orders.end(), [](const auto& item) { return !item.terminal(); }))
        throw Error("portfolio exit requires terminal previous orders");
    if (!context.market.valid(price, amount, false)) throw Error("portfolio LIMIT violates market rules");
    const auto fee = context.sell_fee.estimate(price, amount);
    const auto required = amount + (context.sell_fee.currency == "BASE" ? fee : Rational());
    if (required > base(state.owned_base(), context)) throw Error("portfolio LIMIT exceeds owned inventory");
    std::map<std::string, Rational> reserves {{"BASE", required}};
    if (context.sell_fee.currency != "BASE" && context.sell_fee.currency != "QUOTE")
        reserves.emplace(context.sell_fee.currency, fee);
    auto placed = new_order(state, context, false, "portfolio_exit", price, amount, reserves);
    return {std::move(state), {{"PlaceLimit", std::move(placed), 0, {}}}, {}, true};
}

namespace {
const char* boolean(bool condition) { return condition ? "true" : "false"; }
std::string decimal_json(const Rational& number) { return json_quote(number.str()); }
std::string optional_text(const std::optional<std::string>& text) { return text ? json_quote(*text) : "null"; }
std::string scope_json(const Scope& scope) {
    return "{\"venue\":" + json_quote(scope.venue) + ",\"account\":" + json_quote(scope.account) +
           ",\"symbol\":" + json_quote(scope.symbol) + ",\"allocation\":" + json_quote(scope.allocation) +
           ",\"strategy\":" + json_quote(scope.strategy) + ",\"cycle\":" + json_quote(scope.cycle) + '}';
}
std::string fee_json(const Fee& fee) {
    return "{\"currency\":" + json_quote(fee.currency) + ",\"rate\":" + decimal_json(fee.rate) +
           ",\"increment\":" + decimal_json(fee.increment) + ",\"quote_per_token\":" +
           (fee.quote_per_token ? decimal_json(*fee.quote_per_token) : "null") +
           ",\"max_settlements\":" + std::to_string(fee.max_settlements) + '}';
}
std::string money_json(const std::map<std::string, std::int64_t>& amounts, const EngineContext& context) {
    std::string result = "[";
    bool comma = false;
    for (const auto& [currency, units] : amounts) {
        if (comma) result += ',';
        result += "{\"currency\":" + json_quote(currency) + ",\"amount\":" +
                  decimal_json(value(units, context.quantum(currency))) + '}';
        comma = true;
    }
    return result + ']';
}
std::string order_json(const EngineOrder& item, const EngineContext& context) {
    std::ostringstream out;
    out << "{\"id\":" << item.id << ",\"round_id\":" << item.round_id << ",\"side\":" << json_quote(item.buy ? "buy" : "sell")
        << ",\"role\":" << json_quote(item.role) << ",\"price\":" << decimal_json(item.price)
        << ",\"amount\":" << decimal_json(base(item.amount, context)) << ",\"config_revision\":" << item.config_revision
        << ",\"market_generation\":" << item.generation << ",\"reserves\":" << money_json(item.reserves, context)
        << ",\"principal_currency\":" << json_quote(item.buy ? "QUOTE" : "BASE") << ",\"fee_rule\":" << fee_json(item.fee_rule)
        << ",\"acquisition_fee\":" << decimal_json(quote(item.acquisition_fee, context))
        << ",\"level\":" << (item.level ? std::to_string(*item.level) : "null")
        << ",\"filled\":" << decimal_json(base(item.filled, context)) << ",\"gross_cost\":" << decimal_json(quote(item.gross_cost, context))
        << ",\"acknowledged\":" << boolean(item.acknowledged) << ",\"unknown\":" << boolean(item.unknown)
        << ",\"cancel_requested\":" << boolean(item.cancel_requested) << ",\"terminal_amount\":"
        << (item.terminal_amount ? decimal_json(base(*item.terminal_amount, context)) : "null")
        << ",\"terminal_proven\":" << boolean(item.terminal_proven) << '}';
    return out.str();
}
std::string fill_json(const FillEvent& fill) {
    std::ostringstream out;
    out << "{\"scope\":" << scope_json(fill.scope) << ",\"trade_id\":" << json_quote(fill.trade_id)
        << ",\"order_id\":" << fill.order_id << ",\"amount\":" << decimal_json(fill.amount)
        << ",\"price\":" << decimal_json(fill.price) << ",\"fees\":";
    if (!fill.fees) out << "null";
    else {
        out << '[';
        bool comma = false;
        for (const auto& fee : *fill.fees) {
            if (comma) out << ',';
            out << "{\"currency\":" << json_quote(fee.currency) << ",\"amount\":" << decimal_json(fee.amount)
                << ",\"quote_value\":" << (fee.quote_value ? decimal_json(*fee.quote_value) : "null") << '}';
            comma = true;
        }
        out << ']';
    }
    out << '}'; return out.str();
}
std::string state_json(const EngineState& state, const EngineContext& context) {
    std::ostringstream out;
    out << "{\"scope\":" << scope_json(state.scope) << ",\"quote_cash\":" << decimal_json(quote(state.quote_cash, context))
        << ",\"fee_balances\":" << money_json(state.fee_balances, context) << ",\"config_revision\":" << state.config_revision
        << ",\"market_generation\":" << state.generation << ",\"phase\":" << json_quote(state.phase)
        << ",\"mode\":" << json_quote(state.mode) << ",\"control_revision\":" << state.control_revision
        << ",\"round_id\":" << state.round_id << ",\"next_id\":" << state.next_id << ",\"next_level\":" << state.next_level
        << ",\"ladder_credits\":" << state.ladder_credits << ",\"anchor\":" << (state.anchor ? decimal_json(*state.anchor) : "null")
        << ",\"seed_id\":" << (state.seed_id ? std::to_string(*state.seed_id) : "null") << ",\"orders\":[";
    bool comma = false;
    for (const auto& item : state.orders) { if (comma) out << ','; out << order_json(item, context); comma = true; }
    out << "],\"fills\":["; comma = false;
    for (const auto& fill : state.fills) { if (comma) out << ','; out << fill_json(fill); comma = true; }
    out << "],\"lots\":["; comma = false;
    for (const auto& lot : state.lots) {
        if (comma) out << ',';
        out << "{\"order_id\":" << lot.order_id << ",\"quantity\":" << decimal_json(base(lot.quantity, context))
            << ",\"execution_price\":" << decimal_json(lot.execution_price) << '}';
        comma = true;
    }
    out << "],\"position_cost\":" << decimal_json(quote(state.position_cost, context))
        << ",\"realized_pnl\":" << decimal_json(quote(state.realized_pnl, context))
        << ",\"reconcile_reason\":" << optional_text(state.reconcile_reason) << ",\"reconcile_revision\":" << state.reconcile_revision
        << ",\"sell_dirty\":" << boolean(state.sell_dirty) << ",\"replacements\":" << state.replacements << '}';
    return out.str();
}
} // namespace
std::string observation_json(const EngineState& state, const EngineContext& context) {
    std::ostringstream out;
    out << "{\"state\":" << state_json(state, context) << ",\"owned_base\":" << decimal_json(base(state.owned_base(), context))
        << ",\"available_quote\":" << decimal_json(quote(available_quote(state, context), context)) << ",\"reserved\":{";
    std::vector<std::string> currencies {"BASE", "QUOTE"};
    for (const auto& [currency, units] : state.fee_balances) { (void)units; currencies.push_back(currency); }
    bool comma = false;
    for (const auto& currency : currencies) {
        if (comma) out << ',';
        out << json_quote(currency) << ':' << decimal_json(value(reserved(state, context, currency), context.quantum(currency)));
        comma = true;
    }
    out << "},\"working\":["; comma = false;
    for (const auto& item : state.orders) if (!item.terminal()) {
        if (comma) out << ',';
        out << "{\"order\":" << order_json(item, context) << ",\"remaining\":" << decimal_json(base(item.remaining(), context))
            << ",\"terminal\":false}";
        comma = true;
    }
    out << "]}"; return out.str();
}
std::string transition_json(const Transition& transition, const EngineContext& context) {
    std::ostringstream out;
    out << "{\"accepted\":" << boolean(transition.accepted) << ",\"commands\":[";
    bool comma = false;
    for (const auto& command : transition.commands) {
        if (comma) out << ',';
        out << "{\"type\":" << json_quote(command.type) << ",\"value\":{\"scope\":" << scope_json(transition.state.scope);
        if (command.type == "PlaceLimit") {
            if (!command.order) throw Error("placement command has no order");
            out << ",\"order\":" << order_json(*command.order, context) << ",\"order_type\":\"limit\"";
        } else if (command.type == "Cancel") out << ",\"order_id\":" << command.order_id;
        else out << ",\"reason\":" << json_quote(command.reason);
        out << "}}"; comma = true;
    }
    out << "],\"notices\":["; comma = false;
    for (const auto& notice : transition.notices) { if (comma) out << ','; out << json_quote(notice); comma = true; }
    out << "],\"after\":" << observation_json(transition.state, context) << '}'; return out.str();
}

} // namespace grid
