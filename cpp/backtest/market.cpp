#include "market.hpp"

#include "history.hpp"

#include <sstream>

namespace grid {
namespace {
void positive(const Rational& value, std::string_view message) {
    if (value.sign() <= 0) throw Error(std::string(message));
}
void nonnegative(const Rational& value, std::string_view message) {
    if (value.sign() < 0) throw Error(std::string(message));
}
void field(std::ostringstream& out, std::string_view key, const Rational& value) {
    out << json_quote(key) << ':' << json_quote(value.str());
}
} // namespace

bool Limits::admits(const Rational& value) const { return value >= minimum && (!maximum || value <= *maximum); }
void Market::validate() const {
    positive(price_tick, "positive price tick required");
    positive(amount_tick, "positive amount tick required");
    for (const auto* limit : {&prices, &buy_amount, &buy_cost, &sell_amount, &sell_cost}) {
        nonnegative(limit->minimum, "negative market minimum");
        if (limit->maximum && (limit->maximum->sign() <= 0 || *limit->maximum < limit->minimum))
            throw Error("invalid market maximum");
    }
}
bool Market::valid(const Rational& price, const Rational& amount, bool buy) const {
    positive(price, "positive order price required"); positive(amount, "positive order amount required");
    return price.rounded(price_tick, false) == price && amount.rounded(amount_tick, false) == amount &&
           prices.admits(price) && (buy ? buy_amount : sell_amount).admits(amount) &&
           (buy ? buy_cost : sell_cost).admits(price * amount);
}
void Fee::validate() const {
    if (currency.empty() || currency.size() > 128) throw Error("explicit fee currency required");
    nonnegative(rate, "negative fee forecast");
    if (rate >= Rational(1)) throw Error("fee forecast must be below one");
    positive(increment, "positive fee increment required");
    if (max_settlements == 0 || max_settlements > 100000) throw Error("fee settlement bound invalid");
    if (quote_per_token) positive(*quote_per_token, "positive fee conversion required");
    if (currency != "BASE" && currency != "QUOTE" && !quote_per_token)
        throw Error("missing fee-token conversion");
}
Rational Fee::estimate(const Rational& price, const Rational& amount) const {
    validate(); positive(price, "positive fee price required"); nonnegative(amount, "negative fee amount");
    const Rational raw = currency == "BASE" ? amount * rate : amount * price * rate;
    const Rational conversion = (currency != "BASE" && currency != "QUOTE") ? *quote_per_token : Rational(1);
    const Rational reserve = rate.sign() > 0 ? increment * Rational(static_cast<std::int64_t>(max_settlements - 1)) : Rational();
    return (raw / conversion).rounded(increment, true) + reserve;
}
Rational Fee::quote_cost(const Rational& fee, const Rational& price) const {
    if (currency == "QUOTE") return fee;
    if (currency == "BASE") return fee * price;
    if (!quote_per_token) throw Error("missing fee-token conversion");
    return fee * *quote_per_token;
}
std::vector<Rational> grid_prices(const Rational& anchor, const Rational& spacing,
                                   const std::vector<unsigned>& indices, const Market& market) {
    market.validate(); positive(anchor, "positive anchor required"); positive(spacing, "positive spacing required");
    if (indices.size() > 256) throw Error("too many grid levels");
    Rational previous(anchor);
    unsigned previous_index = 0;
    std::vector<Rational> result;
    result.reserve(indices.size());
    for (unsigned index : indices) {
        if (index == 0 || index > 10000 || index <= previous_index) throw Error("invalid geometric level index");
        Rational price = (anchor / (Rational(1) + spacing).decimal96().power(index).decimal96())
            .decimal96().rounded(market.price_tick, false);
        if (price.sign() <= 0 || price >= previous || !market.prices.admits(price))
            throw Error("collapsed or out-of-range grid level");
        previous = price; previous_index = index; result.push_back(std::move(price));
    }
    return result;
}
TakeProfit aggregate_take_profit(const Rational& owned, const Rational& cost, const Market& market,
                                 const Rational& spacing, const Rational& net_target, const Fee& fee_rule) {
    market.validate(); fee_rule.validate(); positive(owned, "positive inventory required");
    nonnegative(cost, "negative acquisition cost"); positive(spacing, "positive spacing required");
    positive(net_target, "positive net target required");
    const auto base_required = [&fee_rule](const Rational& candidate) {
        return candidate + (fee_rule.currency == "BASE" ? fee_rule.estimate(Rational(1), candidate) : Rational());
    };
    Rational amount = owned.rounded(market.amount_tick, false);
    if (fee_rule.currency == "BASE" && fee_rule.rate.sign() > 0) {
        amount = (owned / (Rational(1) + fee_rule.rate).decimal96()).decimal96().rounded(market.amount_tick, false);
        if (base_required(amount) > owned) {
            Rational lower, upper(amount);
            bool complete = false;
            for (unsigned step = 0; step < 1024; ++step) {
                const Rational midpoint = ((lower + upper) / Rational(2)).rounded(market.amount_tick, false);
                if (midpoint <= lower) { complete = true; break; }
                if (base_required(midpoint) <= owned) lower = midpoint; else upper = midpoint;
            }
            if (!complete) throw Error("base-fee quantity search bound exceeded");
            amount = lower;
        }
    }
    if (amount.sign() <= 0 || !market.sell_amount.admits(amount))
        throw Error("inventory below SELL amount minimum or above maximum");
    const Rational geometric = ((cost / owned).decimal96() * (Rational(1) + spacing).decimal96()).decimal96();
    Rational target = max(max(geometric, market.prices.minimum), (market.sell_cost.minimum / amount).decimal96());
    target = max(target, fee_rule.currency != "BASE"
                         ? ((cost + net_target).decimal96() / (amount * (Rational(1) - fee_rule.rate).decimal96()).decimal96()).decimal96()
                         : ((cost + net_target).decimal96() / amount).decimal96());
    Rational price = target.rounded(market.price_tick, true);
    Rational profit, fee;
    bool complete = false;
    for (unsigned step = 0; step < 256; ++step) {
        fee = fee_rule.estimate(price, amount);
        const Rational fee_cost = fee_rule.currency == "BASE" ? Rational() : fee_rule.quote_cost(fee, price);
        profit = amount * price - fee_cost - cost;
        if (profit >= net_target) { complete = true; break; }
        if (fee_rule.currency == "BASE") throw Error("inconsistent base-fee target");
        const Rational required = (((cost + net_target).decimal96() + fee_rule.quote_cost(fee, price)).decimal96() / amount).decimal96();
        const Rational successor = required.rounded(market.price_tick, true);
        price = successor > price ? successor : price + market.price_tick;
    }
    if (!complete) throw Error("SELL price search bound exceeded");
    if (price * owned < cost * (Rational(1) + spacing) || !market.valid(price, amount, false))
        throw Error("no valid profitable aggregate SELL");
    const Rational reserved = base_required(amount);
    if (reserved > owned) throw Error("SELL fee exceeds owned base");
    return {price, amount, reserved, owned - reserved, profit, fee, geometric, target};
}
MinimumBuy minimum_buy(const Rational& price, const Market& market, const Fee& buy_fee,
                       const Fee& sell_fee, const Rational& spacing, const Rational& net_target,
                       const Rational& available_quote) {
    market.validate(); buy_fee.validate(); sell_fee.validate(); positive(price, "positive BUY price required");
    nonnegative(available_quote, "negative available quote");
    Rational lower = max(market.buy_amount.minimum, (market.buy_cost.minimum / price).decimal96());
    if (lower.sign() == 0) lower = market.amount_tick;
    Rational amount = lower.rounded(market.amount_tick, true);
    if (amount * price < market.buy_cost.minimum) amount = amount + market.amount_tick;
    if (!market.valid(price, amount, true)) throw Error("minimum BUY violates market constraints");
    const Rational previous = max(Rational(), amount - market.amount_tick);
    if (previous.sign() > 0 && market.valid(price, previous, true)) throw Error("minimum BUY could not be proven minimal");
    const Rational fee = buy_fee.estimate(price, amount);
    const Rational net_base = amount - (buy_fee.currency == "BASE" ? fee : Rational());
    const Rational quote_required = price * amount + (buy_fee.currency == "QUOTE" ? fee : Rational());
    if (quote_required > available_quote) throw Error("insufficient allocated quote");
    const Rational acquisition = price * amount + (buy_fee.currency == "BASE" ? Rational() : buy_fee.quote_cost(fee, price));
    return {price, amount, fee, quote_required, net_base, acquisition,
            aggregate_take_profit(net_base, acquisition, market, spacing, net_target, sell_fee), lower};
}
std::string tp_json(const TakeProfit& plan, const Fee& fee) {
    std::ostringstream out;
    out << '{'; field(out, "price", plan.price); out << ','; field(out, "amount", plan.amount);
    out << ','; field(out, "reserved_base", plan.reserved_base); out << ','; field(out, "residual_base", plan.residual_base);
    out << ','; field(out, "expected_profit", plan.expected_profit);
    out << ",\"fee\":{\"currency\":" << json_quote(fee.currency) << ','; field(out, "amount", plan.fee); out << '}';
    out << ','; field(out, "geometric_floor", plan.geometric_floor);
    out << ','; field(out, "unrounded_price_floor", plan.unrounded_price_floor); out << '}';
    return out.str();
}
std::string buy_json(const MinimumBuy& plan, const Fee& buy_fee, const Fee& sell_fee) {
    std::ostringstream out;
    out << '{'; field(out, "price", plan.price); out << ','; field(out, "amount", plan.amount);
    out << ",\"fee\":{\"currency\":" << json_quote(buy_fee.currency) << ','; field(out, "amount", plan.fee); out << '}';
    out << ','; field(out, "quote_required", plan.quote_required); out << ','; field(out, "net_base", plan.net_base);
    out << ','; field(out, "acquisition_cost", plan.acquisition_cost);
    out << ",\"prospective_sell\":" << tp_json(plan.prospective_sell, sell_fee);
    out << ','; field(out, "unrounded_amount_floor", plan.unrounded_amount_floor); out << '}';
    return out.str();
}
} // namespace grid
