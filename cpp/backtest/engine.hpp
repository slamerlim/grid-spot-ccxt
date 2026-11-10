#pragma once

#include "market.hpp"

#include <map>
#include <variant>

namespace grid {
struct Scope {
    std::string venue, account, symbol, allocation, strategy, cycle;
    bool operator==(const Scope&) const = default;
};
struct Policy {
    Rational spacing, net_target, quote_reserve, max_quote_committed;
    Rational lower_price, upper_price, cost_quantum, max_spread;
    unsigned ladder_size = 3, max_lots = 100, max_open_orders = 8;
    unsigned max_order_records = 1000, max_fill_records = 1000;
    unsigned max_replacements = 10;
    std::int64_t max_source_age_ms = 1000;
    bool seed_at_ask = false;
};
struct Book {
    Rational bid, ask;
    std::int64_t observed_ms = 0, generation = 0;
};
struct EngineContext {
    Market market;
    Policy policy;
    Fee buy_fee, sell_fee;
    std::int64_t config_revision = 1, generation = 1, now_ms = 0;
    std::int64_t rules_observed_ms = 0, fees_observed_ms = 0;
    std::optional<Book> book;
    bool allow_new_exposure = true;
    Rational base_quantum {"1e-12"}, quote_quantum {"1e-12"}, token_quantum {"1e-12"};
    // Explicit research-only switches; defaults preserve Python baseline.
    std::string entry_policy = "seed_min_limit", ladder_policy = "ladder_multi";
    std::string reentry_policy = "reentry_near_fresh_book";
    [[nodiscard]] bool fresh(std::int64_t observed_ms) const;
    [[nodiscard]] const Rational& quantum(std::string_view currency) const;
};
struct ActualFee {
    std::string currency;
    Rational amount;
    std::optional<Rational> quote_value;
    bool operator==(const ActualFee&) const = default;
};
struct FillEvent {
    Scope scope;
    std::string trade_id;
    std::int64_t order_id;
    Rational amount, price;
    std::optional<std::vector<ActualFee>> fees;
    bool operator==(const FillEvent&) const = default;
};
struct StartEvent { Scope scope; };
struct ReportEvent {
    Scope scope;
    std::int64_t order_id;
    std::string status;
    Rational cumulative;
    bool reconciled;
};
struct UnknownEvent { Scope scope; std::int64_t order_id; };
struct CancelAckEvent { Scope scope; std::int64_t order_id; };
struct ControlEvent { Scope scope; std::int64_t revision; std::string mode; };
struct ReconciledEvent {
    Scope scope;
    std::int64_t config_revision, generation;
    std::optional<std::string> expected_reason;
    std::int64_t revision;
};
using Event = std::variant<StartEvent, FillEvent, ReportEvent, UnknownEvent, CancelAckEvent, ControlEvent, ReconciledEvent>;
struct EngineOrder {
    std::int64_t id = 0, round_id = 0;
    bool buy = true;
    std::string role;
    Rational price;
    std::int64_t amount = 0, filled = 0, gross_cost = 0, acquisition_fee = 0;
    std::int64_t config_revision = 0, generation = 0;
    std::map<std::string, std::int64_t> reserves;
    Fee fee_rule;
    std::optional<unsigned> level;
    bool acknowledged = false, unknown = false, cancel_requested = false;
    std::optional<std::int64_t> terminal_amount;
    bool terminal_proven = false;
    [[nodiscard]] bool terminal() const;
    [[nodiscard]] std::int64_t remaining() const;
};
struct EngineLot {
    std::int64_t order_id, quantity;
    Rational execution_price;
};
struct EngineState {
    Scope scope;
    std::int64_t quote_cash = 0, position_cost = 0, realized_pnl = 0;
    std::map<std::string, std::int64_t> fee_balances;
    std::int64_t config_revision = 1, generation = 1;
    std::string phase = "inactive", mode = "active";
    std::int64_t control_revision = 0, round_id = 1, next_id = 1;
    unsigned next_level = 1, ladder_credits = 0;
    std::optional<Rational> anchor;
    std::optional<std::int64_t> seed_id;
    std::vector<EngineOrder> orders;
    std::vector<FillEvent> fills;
    std::vector<EngineLot> lots;
    std::optional<std::string> reconcile_reason;
    std::int64_t reconcile_revision = 0;
    bool sell_dirty = false;
    unsigned replacements = 0;
    std::optional<Rational> last_executed_sell_price;
    [[nodiscard]] std::int64_t owned_base() const;
};
struct Command {
    std::string type;
    std::optional<EngineOrder> order;
    std::int64_t order_id = 0;
    std::string reason;
};
struct Transition {
    EngineState state;
    std::vector<Command> commands;
    std::vector<std::string> notices;
    bool accepted = true;
};
[[nodiscard]] std::int64_t reserved(const EngineState&, const EngineContext&, std::string_view currency);
[[nodiscard]] std::int64_t available_quote(const EngineState&, const EngineContext&);
[[nodiscard]] Transition reduce(EngineState state, const Event&, const EngineContext&);
[[nodiscard]] Transition request_cancel(EngineState state, std::int64_t order_id);
[[nodiscard]] Transition place_exit(EngineState state, const EngineContext&, const Rational& price,
                                    const Rational& amount);
[[nodiscard]] std::string observation_json(const EngineState&, const EngineContext&);
[[nodiscard]] std::string transition_json(const Transition&, const EngineContext&);
} // namespace grid
