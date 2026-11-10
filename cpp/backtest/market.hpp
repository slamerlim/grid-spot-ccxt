#pragma once

#include "numeric.hpp"

#include <optional>
#include <string>
#include <vector>

namespace grid {
struct Limits {
    Rational minimum;
    std::optional<Rational> maximum;
    [[nodiscard]] bool admits(const Rational& value) const;
};
struct Market {
    Rational price_tick;
    Rational amount_tick;
    Limits prices;
    Limits buy_amount;
    Limits buy_cost;
    Limits sell_amount;
    Limits sell_cost;
    void validate() const;
    [[nodiscard]] bool valid(const Rational& price, const Rational& amount, bool buy) const;
};
struct Fee {
    std::string currency; // BASE, QUOTE, or an explicit third-token identifier
    Rational rate;
    Rational increment;
    std::optional<Rational> quote_per_token;
    unsigned max_settlements;
    void validate() const;
    [[nodiscard]] Rational estimate(const Rational& price, const Rational& amount) const;
    [[nodiscard]] Rational quote_cost(const Rational& fee, const Rational& price) const;
};
struct TakeProfit {
    Rational price;
    Rational amount;
    Rational reserved_base;
    Rational residual_base;
    Rational expected_profit;
    Rational fee;
    Rational geometric_floor;
    Rational unrounded_price_floor;
};
struct MinimumBuy {
    Rational price;
    Rational amount;
    Rational fee;
    Rational quote_required;
    Rational net_base;
    Rational acquisition_cost;
    TakeProfit prospective_sell;
    Rational unrounded_amount_floor;
};
[[nodiscard]] std::vector<Rational> grid_prices(const Rational& anchor, const Rational& spacing,
                                               const std::vector<unsigned>& indices, const Market& market);
[[nodiscard]] TakeProfit aggregate_take_profit(const Rational& owned, const Rational& cost,
                                               const Market& market, const Rational& spacing,
                                               const Rational& net_target, const Fee& fee);
[[nodiscard]] MinimumBuy minimum_buy(const Rational& price, const Market& market,
                                     const Fee& buy_fee, const Fee& sell_fee,
                                     const Rational& spacing, const Rational& net_target,
                                     const Rational& available_quote);
[[nodiscard]] std::string tp_json(const TakeProfit& plan, const Fee& fee);
[[nodiscard]] std::string buy_json(const MinimumBuy& plan, const Fee& buy_fee, const Fee& sell_fee);
} // namespace grid
