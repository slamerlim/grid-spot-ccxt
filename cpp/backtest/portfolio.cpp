#include "portfolio.hpp"

#include <algorithm>
#include <set>
#include <tuple>

namespace grid {
namespace {
bool working(const EngineState& state) {
    return std::any_of(state.orders.begin(), state.orders.end(), [](const auto& order) { return !order.terminal(); });
}
bool uncertain(const EngineState& state) {
    return state.reconcile_reason.has_value() || std::any_of(state.orders.begin(), state.orders.end(),
        [](const auto& order) { return !order.terminal() && order.unknown; });
}
bool mark_fresh(const PortfolioAllocation& row, const PortfolioPolicy& policy, std::int64_t now) {
    return row.current_close && row.current_close->sign() > 0 && row.mark_ms &&
           *row.mark_ms >= 0 && *row.mark_ms <= now && now - *row.mark_ms <= policy.mark_max_age_ms;
}
Rational fee_assets(const EngineState& state, const EngineContext& context) {
    Rational total;
    for (const auto& [currency, units] : state.fee_balances) {
        if (units < 0 || currency == "BASE" || currency == "QUOTE") throw Error("invalid separate fee-token ownership");
        if (units == 0) continue;
        std::optional<Rational> conversion;
        for (const auto* fee : {&context.buy_fee, &context.sell_fee}) {
            if (fee->currency == currency && fee->quote_per_token) {
                if (conversion && *conversion != *fee->quote_per_token) throw Error("fee-token conversions disagree");
                conversion = fee->quote_per_token;
            }
        }
        if (!conversion) throw Error("explicit fee-token valuation missing");
        total = total + (Rational(units) * context.token_quantum * *conversion).rounded(context.quote_quantum, false);
    }
    return total;
}
std::optional<Rational> group_nav(const PortfolioNav& nav, const std::vector<std::size_t>& members, bool net = true) {
    Rational total;
    for (const auto member : members) {
        const auto& value = net ? nav.allocations[member].net : nav.allocations[member].raw;
        if (!value) return std::nullopt;
        total = total + *value;
    }
    return total;
}
bool contains_all(const std::vector<std::size_t>& outer, const std::vector<std::size_t>& inner) {
    return std::all_of(inner.begin(), inner.end(), [&](auto index) {
        return std::find(outer.begin(), outer.end(), index) != outer.end();
    });
}
} // namespace

Rational sell_quantity(const Rational& owned, const Market& market, const Fee& fee, const Rational& price) {
    if (owned.sign() < 0 || price.sign() <= 0) throw Error("invalid liquidation quantity input");
    market.validate(); fee.validate();
    Rational upper = min(owned, market.sell_amount.maximum.value_or(owned));
    if (market.sell_cost.maximum) upper = min(upper, *market.sell_cost.maximum / price);
    Rational amount = upper.rounded(market.amount_tick, false);
    if (fee.currency != "BASE" || amount + fee.estimate(price, amount) <= owned) return amount;
    Rational lower;
    upper = amount;
    for (unsigned step = 0; step < 1024; ++step) {
        const Rational middle = ((lower + upper) / Rational(2)).rounded(market.amount_tick, false);
        if (middle <= lower) return lower;
        if (middle + fee.estimate(price, middle) <= owned) lower = middle;
        else upper = middle;
    }
    throw Error("base-fee liquidation quantity search bound");
}

AllocationNav allocation_nav(const PortfolioAllocation& row, const PortfolioPolicy& policy, std::int64_t now_ms) {
    AllocationNav result;
    try {
        if (!row.state || !row.context || now_ms < 0 || policy.mark_max_age_ms < 0 ||
            policy.nav_order_budget == 0 || policy.nav_order_budget > 1000) throw Error("invalid bounded valuation input");
        const auto& state = *row.state;
        const auto& context = *row.context;
        result.owned = Rational(state.owned_base()) * context.base_quantum;
        result.residual = result.owned;
        if (uncertain(state)) throw Error("ownership requires reconciliation");
        if (state.config_revision != context.config_revision || state.generation != context.generation)
            throw Error("valuation rules generation differs from ownership");
        if (state.quote_cash < 0 || result.owned.sign() < 0 || context.quote_quantum.sign() <= 0)
            throw Error("negative ownership or invalid quote quantum");
        const Rational cash = Rational(state.quote_cash) * context.quote_quantum + fee_assets(state, context);
        if (result.owned.sign() == 0) { result.raw = cash; result.net = cash; return result; }
        if (!mark_fresh(row, policy, now_ms)) throw Error("owned asset current mark unavailable");
        if (context.now_ms != now_ms || !context.fresh(context.rules_observed_ms) || !context.fresh(context.fees_observed_ms))
            throw Error("fresh market and fee assumptions required");
        result.raw = cash + (result.owned * *row.current_close).rounded(context.quote_quantum, false);
        result.exit_bid = row.current_close->rounded(context.market.price_tick, false);
        Rational proceeds;
        if (result.exit_bid.sign() > 0) {
            for (unsigned index = 0; index < policy.nav_order_budget; ++index) {
                const Rational amount = sell_quantity(result.residual, context.market, context.sell_fee, result.exit_bid);
                if (amount.sign() == 0 || !context.market.valid(result.exit_bid, amount, false)) break;
                const Rational fee = context.sell_fee.estimate(result.exit_bid, amount);
                const Rational charge = context.sell_fee.currency == "BASE" ? Rational() :
                    context.sell_fee.quote_cost(fee, result.exit_bid);
                const Rational value = amount * result.exit_bid - charge;
                if (value.sign() < 0) throw Error("negative estimated exit proceeds");
                proceeds = proceeds + value;
                result.estimated_fee = result.estimated_fee + charge;
                result.sellable = result.sellable + amount;
                result.residual = result.residual - amount - (context.sell_fee.currency == "BASE" ? fee : Rational());
            }
            const Rational extra = sell_quantity(result.residual, context.market, context.sell_fee, result.exit_bid);
            if (extra.sign() > 0 && context.market.valid(result.exit_bid, extra, false))
                throw Error("sellable inventory exceeds bounded valuation order budget");
        }
        result.net = cash + proceeds.rounded(context.quote_quantum, false);
    } catch (const Error& error) {
        // Raw marks can remain informative when only liquidation is inadmissible.
        // Unknown ownership or missing marks fail before assigning raw.
        result.net.reset(); result.reason = error.what();
    }
    return result;
}

PortfolioCoordinator::PortfolioCoordinator(PortfolioPolicy policy, std::vector<Scope> scopes,
                                           const std::vector<Rational>& funding)
    : policy_(std::move(policy)), scopes_(std::move(scopes)), slots_(scopes_.size()), owners_(scopes_.size()) {
    if (scopes_.empty() || scopes_.size() > 100 || funding.size() != scopes_.size() ||
        policy_.mark_max_age_ms < 0 || policy_.ttl_ms <= 0 || policy_.duration_ms <= 0 ||
        policy_.max_reprices > 1000 || policy_.max_cycles == 0 || policy_.max_cycles > 10000 ||
        policy_.nav_order_budget == 0 || policy_.nav_order_budget > 1000 ||
        policy_.max_deviation.sign() < 0 || policy_.max_deviation >= Rational(1) ||
        policy_.exchange_threshold.sign() <= 0 || policy_.symbol_threshold.sign() <= 0)
        throw Error("invalid bounded portfolio policy");
    PortfolioCycle global; global.tier = "global"; global.key = "all";
    std::map<std::pair<std::string, std::string>, std::vector<std::size_t>> venues;
    std::set<std::tuple<std::string, std::string, std::string, std::string>> unique;
    for (std::size_t index = 0; index < scopes_.size(); ++index) {
        const auto& scope = scopes_[index];
        const auto account = std::pair(scope.venue, scope.account);
        const auto identity = std::tuple(scope.venue, scope.account, scope.symbol, scope.allocation);
        if (funding[index].sign() <= 0 || !unique.insert(identity).second) throw Error("positive unique allocation funding required");
        venues[account].push_back(index); global.members.push_back(index);
        global.baseline = global.baseline + funding[index];
    }
    global.original_funding = global.baseline; cycles_.push_back(std::move(global));
    for (const auto& [key, members] : venues) {
        PortfolioCycle cycle; cycle.tier = "exchange"; cycle.key = key.first + "/" + key.second; cycle.members = members;
        for (const auto index : members) cycle.baseline = cycle.baseline + funding[index];
        cycle.original_funding = cycle.baseline; cycles_.push_back(std::move(cycle));
    }
    for (std::size_t index = 0; index < scopes_.size(); ++index) {
        PortfolioCycle cycle; cycle.tier = "symbol";
        cycle.key = scopes_[index].venue + "/" + scopes_[index].account + "/" + scopes_[index].symbol;
        cycle.members = {index}; cycle.baseline = funding[index]; cycle.original_funding = funding[index];
        cycles_.push_back(std::move(cycle));
    }
}

PortfolioStep PortfolioCoordinator::evaluate(const std::vector<PortfolioAllocation>& rows, std::int64_t now) {
    if (rows.size() != scopes_.size() || now < 0 || (last_ms_ && now <= *last_ms_))
        throw Error("complete increasing timestamp groups required");
    for (std::size_t index = 0; index < rows.size(); ++index)
        if (!rows[index].state || !rows[index].context || rows[index].state->scope != scopes_[index])
            throw Error("portfolio roster changed");
    PortfolioStep step;
    Rational raw, net;
    bool raw_known = true, net_known = true;
    for (const auto& row : rows) {
        auto nav = allocation_nav(row, policy_, now);
        if (nav.raw) raw = raw + *nav.raw; else raw_known = false;
        if (nav.net) net = net + *nav.net; else net_known = false;
        step.nav.allocations.push_back(std::move(nav));
    }
    if (raw_known) step.nav.raw = raw;
    if (net_known) step.nav.net = net;
    std::vector<std::optional<Transition>> changed(rows.size());
    const auto state_at = [&](std::size_t index) -> const EngineState& {
        return changed[index] ? changed[index]->state : *rows[index].state;
    };
    const auto apply = [&](std::size_t index, Transition transition) {
        if (!transition.accepted) throw Error("portfolio authoritative transition refused");
        if (changed[index]) {
            auto& prior = *changed[index];
            prior.state = std::move(transition.state);
            prior.commands.insert(prior.commands.end(), transition.commands.begin(), transition.commands.end());
            prior.notices.insert(prior.notices.end(), transition.notices.begin(), transition.notices.end());
        } else changed[index] = std::move(transition);
    };
    const auto event = [&](std::string kind, const PortfolioCycle& cycle, std::string reason) {
        step.events.push_back({std::move(kind), cycle.tier, cycle.key, std::move(reason), now,
            cycle.cycles, cycle.baseline, cycle.original_funding,
            group_nav(step.nav, cycle.members, false), group_nav(step.nav, cycle.members),
            cycle.trigger_ms, cycle.completed_ms, cycle.trigger_nav, cycle.trigger_baseline, cycle.completion_nav});
    };
    // Completed scopes stay held until a strictly later observation. Higher-tier
    // ownership wins even when a lower tier completed on the preceding bar.
    for (std::size_t ci = 0; ci < cycles_.size(); ++ci) {
        auto& cycle = cycles_[ci];
        if (cycle.phase != "complete" || !cycle.completed_ms || now <= *cycle.completed_ms) continue;
        if (std::any_of(cycle.members.begin(), cycle.members.end(), [&](auto i) { return owners_[i] != ci; })) continue;
        bool higher_trigger = false;
        for (std::size_t parent = 0; parent < ci; ++parent) {
            const auto& above = cycles_[parent];
            if (above.phase != "active" || !contains_all(above.members, cycle.members) ||
                (above.tier == "exchange" && !policy_.exchange_enabled)) continue;
            const auto value = group_nav(step.nav, above.members);
            const Rational threshold = above.tier == "global" ? Rational(".10") : policy_.exchange_threshold;
            if (value && *value >= above.baseline * (Rational(1) + threshold)) higher_trigger = true;
        }
        if (higher_trigger) continue; // No transient BUY between completed lower exit and higher handoff.
        if (cycle.cycles >= policy_.max_cycles) { cycle.phase = "cycle_limit"; event("blocked", cycle, "maximum cycle count"); continue; }
        const auto value = group_nav(step.nav, cycle.members);
        if (!value || value->sign() <= 0 || std::any_of(cycle.members.begin(), cycle.members.end(), [&](auto i) {
                return state_at(i).owned_base() != 0 || working(state_at(i)) || uncertain(state_at(i)); })) continue;
        const auto members = cycle.members;
        event("restart", cycle, "reconciled later-bar restart");
        for (auto& child : cycles_) {
            if (!contains_all(members, child.members)) continue;
            const auto baseline = group_nav(step.nav, child.members);
            if (!baseline || baseline->sign() <= 0) throw Error("invalid completed scope baseline");
            child.baseline = *baseline; child.phase = "active"; child.trigger_ms.reset(); child.completed_ms.reset(); ++child.cycles;
            child.trigger_nav.reset(); child.trigger_raw_nav.reset(); child.trigger_baseline.reset();
            child.completion_nav.reset(); child.completion_raw_nav.reset();
        }
        for (const auto index : members) {
            owners_[index].reset(); slots_[index] = PortfolioExitSlot{};
            const auto& state = state_at(index);
            // Cash can be valued without a current asset mark; new exposure
            // cannot. A missing allocation's context still carries its last
            // actual bar, so never let that old clock certify its book fresh.
            auto resume_context = *rows[index].context;
            resume_context.now_ms = now;
            resume_context.allow_new_exposure = resume_context.allow_new_exposure && mark_fresh(rows[index], policy_, now);
            apply(index, reduce(state, ControlEvent{state.scope, checked_add(state.control_revision, 1), "active"}, resume_context));
        }
    }
    // Stored order is precedence: global, accounts, individual allocations.
    for (std::size_t ci = 0; ci < cycles_.size(); ++ci) {
        auto& cycle = cycles_[ci];
        if (cycle.phase != "active" || (cycle.tier == "exchange" && !policy_.exchange_enabled) ||
            (cycle.tier == "symbol" && !policy_.symbol_enabled)) continue;
        if (std::any_of(cycle.members.begin(), cycle.members.end(), [&](auto i) { return owners_[i] && *owners_[i] < ci; })) continue;
        const auto value = group_nav(step.nav, cycle.members);
        const Rational threshold = cycle.tier == "global" ? Rational(".10") :
            cycle.tier == "exchange" ? policy_.exchange_threshold : policy_.symbol_threshold;
        if (!value || *value < cycle.baseline * (Rational(1) + threshold)) continue;
        cycle.phase = "exiting"; cycle.trigger_ms = now; ++cycle.triggers;
        cycle.trigger_nav = *value; cycle.trigger_raw_nav = group_nav(step.nav, cycle.members, false);
        cycle.trigger_baseline = cycle.baseline;
        event("trigger", cycle, "exit-adjusted NAV threshold reached");
        for (const auto index : cycle.members) {
            // A higher tier inherits an existing exit's fixed floor and budget.
            // This prevents a handoff from refreshing spent retries or widening
            // the loss floor of an already protected allocation.
            if (!owners_[index]) {
                slots_[index] = PortfolioExitSlot{};
                slots_[index].started_ms = now;
                if (mark_fresh(rows[index], policy_, now))
                    slots_[index].anchor_bid = rows[index].current_close->rounded(rows[index].context->market.price_tick, false);
            }
            owners_[index] = ci;
            const auto& state = state_at(index);
            if (state.mode != "portfolio_exit" && state.mode != "exit_blocked")
                apply(index, reduce(state, ControlEvent{state.scope, checked_add(state.control_revision, 1), "portfolio_exit"}, *rows[index].context));
        }
    }
    for (std::size_t ci = 0; ci < cycles_.size(); ++ci) {
        auto& cycle = cycles_[ci];
        if (cycle.phase != "exiting" && cycle.phase != "blocked") continue;
        if (std::any_of(cycle.members.begin(), cycle.members.end(), [&](auto i) { return owners_[i] != ci; })) {
            cycle.phase = "superseded"; continue;
        }
        if (!cycle.trigger_ms) throw Error("latched exit without trigger time");
        const bool expired = now - *cycle.trigger_ms >= policy_.duration_ms;
        bool barrier = false;
        for (const auto index : cycle.members) {
            const auto snapshot = state_at(index);
            if (uncertain(snapshot)) barrier = true;
            for (const auto& order : snapshot.orders) {
                if (order.terminal()) continue;
                if (expired || (slots_[index].started_ms && now - *slots_[index].started_ms >= policy_.duration_ms))
                    apply(index, request_cancel(state_at(index), order.id));
                if (order.buy || order.role != "portfolio_exit") {
                    barrier = true; apply(index, request_cancel(state_at(index), order.id));
                }
            }
        }
        bool hard_block = false;
        for (const auto index : cycle.members) {
            auto& slot = slots_[index];
            const auto& context = *rows[index].context;
            const bool slot_expired = expired || (slot.started_ms && now - *slot.started_ms >= policy_.duration_ms);
            if (uncertain(state_at(index))) { slot.reason = "reconciliation required"; hard_block = true; continue; }
            if (barrier) { slot.reason = "ordinary orders must reconcile across exit scope"; continue; }
            const auto snapshot = state_at(index);
            const auto live = std::find_if(snapshot.orders.begin(), snapshot.orders.end(), [](const auto& o) { return !o.terminal(); });
            if (live != snapshot.orders.end()) {
                const bool obsolete = live->config_revision != context.config_revision || live->generation != context.generation;
                if (slot_expired || !slot.created_ms || now - *slot.created_ms >= policy_.ttl_ms || obsolete || live->cancel_requested) {
                    apply(index, request_cancel(state_at(index), live->id)); slot.reason = "exit cancellation pending";
                } else slot.reason = "awaiting actual exit fills";
                continue;
            }
            if (snapshot.owned_base() == 0) { slot.reason = "base reconciled to zero"; slot.order_id.reset(); continue; }
            if (slot_expired || slot.orders >= 1 + policy_.max_reprices) {
                slot.reason = "exit time or order budget exhausted"; hard_block = true; continue;
            }
            if (!mark_fresh(rows[index], policy_, now)) { slot.reason = "owned asset current mark unavailable"; hard_block = true; continue; }
            if (context.now_ms != now || !context.fresh(context.rules_observed_ms) || !context.fresh(context.fees_observed_ms) ||
                snapshot.config_revision != context.config_revision || snapshot.generation != context.generation) {
                slot.reason = "fresh reconciled market and fee facts required"; hard_block = true; continue;
            }
            if (!slot.anchor_bid || slot.anchor_bid->sign() <= 0) { slot.reason = "trigger bid unavailable"; hard_block = true; continue; }
            const Rational lower = max(context.market.prices.minimum, *slot.anchor_bid * (Rational(1) - policy_.max_deviation));
            const Rational price = max(lower, *rows[index].current_close).rounded(context.market.price_tick, true);
            if (price > *slot.anchor_bid * (Rational(1) + policy_.max_deviation)) {
                slot.reason = "exit price outside permitted drift"; hard_block = true; continue;
            }
            const Rational amount = sell_quantity(Rational(snapshot.owned_base()) * context.base_quantum, context.market, context.sell_fee, price);
            if (amount.sign() <= 0 || !context.market.valid(price, amount, false)) {
                slot.reason = "unsellable residual"; hard_block = true; continue;
            }
            const Rational fee = context.sell_fee.estimate(price, amount);
            if (amount * price - (context.sell_fee.currency == "BASE" ? Rational() : context.sell_fee.quote_cost(fee, price)) < Rational()) {
                slot.reason = "negative exit proceeds"; hard_block = true; continue;
            }
            try {
                auto placed = place_exit(snapshot, context, price, amount);
                slot.order_id = placed.state.orders.back().id; slot.created_ms = now; ++slot.orders;
                slot.reason = "exit intent reserved"; apply(index, std::move(placed));
            } catch (const Error& error) { slot.reason = error.what(); hard_block = true; }
        }
        const bool complete = std::all_of(cycle.members.begin(), cycle.members.end(), [&](auto i) {
            const auto& state = state_at(i);
            return state.owned_base() == 0 && !working(state) && !uncertain(state);
        });
        if (complete) {
            cycle.phase = "complete"; cycle.completed_ms = now; ++cycle.completions;
            cycle.completion_nav = group_nav(step.nav, cycle.members);
            cycle.completion_raw_nav = group_nav(step.nav, cycle.members, false);
            event("complete", cycle, "all orders terminal and base ownership zero");
        } else {
            const std::string phase = hard_block ? "blocked" : "exiting";
            if (phase == "blocked" && cycle.phase != "blocked") event("blocked", cycle, "owned exposure retained; inspect exit slots");
            cycle.phase = phase;
        }
    }
    for (std::size_t index = 0; index < changed.size(); ++index)
        if (changed[index]) step.actions.push_back({index, std::move(*changed[index])});
    last_ms_ = now;
    return step;
}
} // namespace grid
