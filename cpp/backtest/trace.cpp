#include "trace.hpp"

#include "engine.hpp"
#include "simulator.hpp"
#include "study.hpp"

#include <charconv>
#include <fstream>
#include <set>
#include <sstream>

namespace grid {
namespace {
using Tokens = std::vector<std::string>;
Tokens split(const std::string& line) {
    Tokens result;
    std::size_t start = 0;
    for (;;) {
        const auto tab = line.find('\t', start);
        result.push_back(line.substr(start, tab == std::string::npos ? tab : tab - start));
        if (tab == std::string::npos) return result;
        start = tab + 1;
    }
}
void count(const Tokens& tokens, std::size_t expected) {
    if (tokens.size() != expected) throw Error("wrong trace field count");
}
std::int64_t integer(const std::string& text, std::int64_t minimum = 0,
                     std::int64_t maximum = 9007199254740991LL) {
    std::int64_t result = 0;
    const auto parsed = std::from_chars(text.data(), text.data() + text.size(), result);
    if (parsed.ec != std::errc{} || parsed.ptr != text.data() + text.size() || result < minimum || result > maximum)
        throw Error("invalid bounded trace integer");
    return result;
}
bool boolean(const std::string& text) { return integer(text, 0, 1) != 0; }
std::optional<Rational> optional_number(const std::string& text) {
    return text == "-" ? std::nullopt : std::optional<Rational>(Rational(text));
}
void identifier(const std::string& text) {
    if (text.empty() || text.size() > 128) throw Error("invalid trace identifier");
    for (char ch : text) if (static_cast<unsigned char>(ch) < 32 || static_cast<unsigned char>(ch) > 126)
        throw Error("trace identifier must be printable ASCII");
}
void mode(const std::string& text) {
    const std::set<std::string> modes {"active", "hold_inventory", "paused", "retiring", "portfolio_exit", "exit_blocked"};
    if (!modes.contains(text)) throw Error("unsupported control mode");
}
struct Configuration {
    std::map<std::string, std::string> values;
    std::map<std::string, Fee> fees;
    std::map<std::string, Rational> tokens;
    std::optional<Scope> scope;
    std::string take(const std::string& key) {
        const auto found = values.find(key);
        if (found == values.end()) throw Error("missing trace config: " + key);
        std::string result = found->second; values.erase(found); return result;
    }
    Rational number(const std::string& key) { return Rational(take(key)); }
    std::int64_t whole(const std::string& key, std::int64_t minimum = 0,
                      std::int64_t maximum = 9007199254740991LL) { return integer(take(key), minimum, maximum); }
    Limits limits(const std::string& prefix) {
        return {number(prefix + "_min"), optional_number(take(prefix + "_max"))};
    }
    std::pair<EngineContext, EngineState> build() {
        if (!scope || fees.size() != 2 || !fees.contains("buy") || !fees.contains("sell"))
            throw Error("trace scope and both fee rules required");
        EngineContext context;
        context.market.price_tick = number("price_tick"); context.market.amount_tick = number("amount_tick");
        context.market.prices = limits("price"); context.market.buy_amount = limits("buy_amount");
        context.market.buy_cost = limits("buy_cost"); context.market.sell_amount = limits("sell_amount");
        context.market.sell_cost = limits("sell_cost"); context.market.validate();
        context.config_revision = whole("config_revision"); context.generation = whole("market_generation");
        context.now_ms = whole("now_ms"); context.rules_observed_ms = whole("rules_observed_ms");
        context.fees_observed_ms = whole("fees_observed_ms"); context.allow_new_exposure = boolean(take("allow_new_exposure"));
        context.buy_fee = fees.at("buy"); context.sell_fee = fees.at("sell");
        context.base_quantum = number("base_quantum"); context.quote_quantum = number("quote_quantum");
        context.token_quantum = number("token_quantum");
        if (context.base_quantum.sign() <= 0 || context.quote_quantum.sign() <= 0 || context.token_quantum.sign() <= 0)
            throw Error("positive ledger quanta required");
        auto& policy = context.policy;
        policy.spacing = number("spacing"); policy.net_target = number("net_target");
        policy.quote_reserve = number("quote_reserve"); policy.max_quote_committed = number("max_quote_committed");
        policy.lower_price = number("lower_price"); policy.upper_price = number("upper_price");
        policy.cost_quantum = number("cost_quantum"); policy.max_spread = number("max_spread");
        for (const auto* required : {&policy.spacing, &policy.net_target, &policy.max_quote_committed,
                                    &policy.lower_price, &policy.upper_price, &policy.cost_quantum})
            if (required->sign() <= 0) throw Error("positive trace policy field required");
        if (policy.quote_reserve.sign() < 0 || policy.max_spread.sign() < 0 || policy.lower_price >= policy.upper_price)
            throw Error("invalid trace risk range");
        policy.ladder_size = static_cast<unsigned>(whole("ladder_size", 1, 256));
        policy.max_lots = static_cast<unsigned>(whole("max_lots", 1, 10000));
        policy.max_open_orders = static_cast<unsigned>(whole("max_open_orders", 1, 1024));
        policy.max_order_records = static_cast<unsigned>(whole("max_order_records", policy.max_open_orders, 100000));
        policy.max_fill_records = static_cast<unsigned>(whole("max_fill_records", 1, 100000));
        policy.max_replacements = static_cast<unsigned>(whole("max_replacements", 0, 10000));
        policy.max_source_age_ms = whole("max_source_age_ms", 1);
        const auto seed_at = take("seed_at");
        if (seed_at != "bid" && seed_at != "ask") throw Error("invalid seed reference");
        policy.seed_at_ask = seed_at == "ask";
        const auto bid = take("book_bid"), ask = take("book_ask");
        const auto observed = take("book_observed_ms"), generation = take("book_generation");
        if (bid != "-") {
            Book book {Rational(bid), Rational(ask), integer(observed), integer(generation)};
            if (book.bid.sign() <= 0 || book.ask < book.bid) throw Error("invalid trace book");
            context.book = std::move(book);
        } else if (ask != "-" || observed != "-" || generation != "-") throw Error("partial missing book");
        EngineState state;
        state.scope = *scope; state.config_revision = context.config_revision; state.generation = context.generation;
        state.quote_cash = number("initial_quote_cash").exact_units(context.quote_quantum);
        if (state.quote_cash < 0) throw Error("negative initial cash");
        for (const auto& [currency, balance] : tokens) {
            const auto units = balance.exact_units(context.quantum(currency));
            if (units < 0) throw Error("negative fee token funds");
            state.fee_balances.emplace(currency, units);
        }
        if (!values.empty()) throw Error("unknown trace config: " + values.begin()->first);
        return {std::move(context), std::move(state)};
    }
};
Event event(const Tokens& tokens, const Scope& scope) {
    if (tokens.size() < 2) throw Error("missing trace event type");
    const auto& type = tokens[1];
    if (type == "Start") { count(tokens, 2); return StartEvent {scope}; }
    if (type == "Fill") {
        if (tokens.size() < 7) throw Error("short fill trace");
        identifier(tokens[2]);
        FillEvent fill {scope, tokens[2], integer(tokens[3], 1), Rational(tokens[4]), Rational(tokens[5]), std::nullopt};
        if (fill.amount.sign() <= 0 || fill.price.sign() <= 0) throw Error("positive fill values required");
        const auto fee_count = integer(tokens[6], -1, 16);
        count(tokens, 7 + static_cast<std::size_t>(std::max<std::int64_t>(0, fee_count)) * 3);
        if (fee_count >= 0) {
            fill.fees.emplace();
            for (std::int64_t index = 0; index < fee_count; ++index) {
                const auto position = 7 + static_cast<std::size_t>(index) * 3;
                identifier(tokens[position]);
                ActualFee fee {tokens[position], Rational(tokens[position + 1]), optional_number(tokens[position + 2])};
                if (fee.quote_value && fee.amount.sign() != fee.quote_value->sign()) throw Error("fee conversion sign mismatch");
                fill.fees->push_back(std::move(fee));
            }
        }
        return fill;
    }
    if (type == "OrderReport") {
        count(tokens, 6);
        const std::set<std::string> statuses {"open", "canceled", "rejected", "filled"};
        if (!statuses.contains(tokens[3])) throw Error("unsupported order status");
        Rational cumulative(tokens[4]);
        if (cumulative.sign() < 0) throw Error("negative reported cumulative");
        return ReportEvent {scope, integer(tokens[2], 1), tokens[3], cumulative, boolean(tokens[5])};
    }
    if (type == "CancelAcknowledged") { count(tokens, 3); return CancelAckEvent {scope, integer(tokens[2], 1)}; }
    if (type == "SubmissionUnknown") { count(tokens, 3); return UnknownEvent {scope, integer(tokens[2], 1)}; }
    if (type == "Control") { count(tokens, 4); mode(tokens[3]); return ControlEvent {scope, integer(tokens[2], 1), tokens[3]}; }
    if (type == "Reconciled") {
        count(tokens, 6);
        return ReconciledEvent {scope, integer(tokens[2]), integer(tokens[3]),
                               tokens[4] == "-" ? std::nullopt : std::optional(tokens[4]), integer(tokens[5])};
    }
    throw Error("unsupported trace event");
}
} // namespace

namespace {
Bar raw_bar(const std::string& text) {
    if (text.size() != record_bytes * 2) throw Error("48 hexadecimal digits required");
    std::array<std::byte, record_bytes> bytes {};
    for (std::size_t index = 0; index < bytes.size(); ++index) {
        unsigned byte = 0;
        const auto parsed = std::from_chars(text.data() + index * 2, text.data() + index * 2 + 2, byte, 16);
        if (parsed.ec != std::errc{} || parsed.ptr != text.data() + index * 2 + 2) throw Error("invalid raw bar hexadecimal");
        bytes[index] = static_cast<std::byte>(byte);
    }
    return decode(bytes);
}
void run_file(const std::string& path, std::ostream& output, bool bars) {
    std::ifstream file(path, std::ios::binary);
    if (!file) throw Error("cannot open trace file");
    std::string bytes;
    char byte = 0;
    while (file.get(byte)) {
        if (bytes.size() >= 1048576) throw Error("trace byte bound exceeded");
        bytes.push_back(byte);
    }
    if (!file.eof()) throw Error("trace input read failed");
    std::istringstream stream(bytes);
    std::string line;
    if (!std::getline(stream, line) || line != "GRID_MODULE1_TRACE\t1") throw Error("unsupported trace schema");
    Configuration config;
    std::optional<EngineContext> context;
    std::optional<EngineState> state;
    std::optional<Simulator> simulator;
    BarModel bar_model;
    std::set<std::string> model_fields;
    unsigned rows = 0, events = 0;
    while (std::getline(stream, line)) {
        if (++rows > 20000 || line.size() > 65536) throw Error("trace input bound exceeded");
        const auto tokens = split(line);
        if (tokens.empty() || tokens[0].empty()) throw Error("empty trace row");
        if (tokens[0] == "BAR_CONFIG" && bars) {
            count(tokens, 3);
            if (context || !model_fields.insert(tokens[1]).second) throw Error("late or duplicate bar config");
            if (tokens[1] == "model") bar_model.model = tokens[2];
            else if (tokens[1] == "latency_bars") bar_model.latency_bars = integer(tokens[2], 1, 1000000);
            else if (tokens[1] == "synthetic_spread") bar_model.spread = Rational(tokens[2]);
            else throw Error("unknown bar config");
            continue;
        }
        if (tokens[0] == "BAR" && bars) {
            count(tokens, 2);
            if (++events > 10000) throw Error("bar input bound exceeded");
            if (!context) {
                if (model_fields.size() != 3) throw Error("all bar model fields required");
                auto initial = config.build(); context = initial.first; state = initial.second;
                simulator.emplace(std::move(initial.second), std::move(initial.first), bar_model);
            }
            simulator->step(raw_bar(tokens[1]), static_cast<std::int64_t>(events - 1));
            output << simulator->json() << '\n';
            if (!output) throw Error("bar output failed");
            continue;
        }
        if (tokens[0] == "EVENT" && !bars) {
            if (++events > 10000) throw Error("trace event bound exceeded");
            if (!context) { auto initial = config.build(); context = std::move(initial.first); state = std::move(initial.second); }
            const auto transition = reduce(*state, event(tokens, state->scope), *context);
            output << transition_json(transition, *context) << '\n';
            if (!output) throw Error("trace output failed");
            state = transition.state;
            continue;
        }
        if (context) throw Error("trace config after events");
        if (tokens[0] == "SCOPE") {
            count(tokens, 7);
            if (config.scope) throw Error("duplicate trace scope");
            for (std::size_t index = 1; index < tokens.size(); ++index) identifier(tokens[index]);
            config.scope = Scope {tokens[1], tokens[2], tokens[3], tokens[4], tokens[5], tokens[6]};
        } else if (tokens[0] == "CONFIG") {
            count(tokens, 3);
            if (!config.values.emplace(tokens[1], tokens[2]).second) throw Error("duplicate trace config");
        } else if (tokens[0] == "FEE_RULE") {
            count(tokens, 7);
            if (tokens[1] != "buy" && tokens[1] != "sell") throw Error("unknown fee rule side");
            identifier(tokens[2]);
            Fee fee {tokens[2], Rational(tokens[3]), Rational(tokens[4]), optional_number(tokens[5]),
                     static_cast<unsigned>(integer(tokens[6], 1, 100000))};
            fee.validate();
            if (!config.fees.emplace(tokens[1], std::move(fee)).second) throw Error("duplicate fee rule");
        } else if (tokens[0] == "TOKEN") {
            count(tokens, 3); identifier(tokens[1]);
            if (!config.tokens.emplace(tokens[1], Rational(tokens[2])).second) throw Error("duplicate token funds");
        } else throw Error("unknown trace row type");
    }
    if (!stream.eof()) throw Error("trace read failed");
    if (events == 0) throw Error("trace requires at least one event");
}
} // namespace
void run_trace(const std::string& path, std::ostream& output) { run_file(path, output, false); }
void run_bars(const std::string& path, std::ostream& output) { run_file(path, output, true); }

StudyConfig read_study(const std::string& path) {
    std::ifstream input(path, std::ios::binary);
    if (!input) throw Error("cannot open replay configuration");
    std::string raw;
    std::array<char, 4096> chunk {};
    while (input) {
        input.read(chunk.data(), static_cast<std::streamsize>(chunk.size()));
        raw.append(chunk.data(), static_cast<std::size_t>(input.gcount()));
        if (raw.size() > 1048576) throw Error("replay configuration exceeds byte bound");
    }
    if (!input.eof()) throw Error("replay configuration read failed");
    std::array<std::byte, 32> digest {};
    unsigned digest_size = 0;
    if (EVP_Digest(raw.data(), raw.size(), reinterpret_cast<unsigned char*>(digest.data()), &digest_size, EVP_sha256(), nullptr) != 1 || digest_size != 32)
        throw Error("replay input digest failed");
    StudyConfig result;
    result.input_sha256 = hex(digest);
    std::istringstream stream(raw);
    std::string line;
    if (!std::getline(stream, line) || line != "GRID_MODULE1_REPLAY\t1") throw Error("unsupported replay schema");
    Configuration config;
    std::map<std::string, std::string> extra;
    unsigned rows = 0;
    while (std::getline(stream, line)) {
        if (++rows > 512 || line.size() > 65536) throw Error("replay config row bound exceeded");
        const auto tokens = split(line);
        if (tokens[0] == "SOURCE") {
            count(tokens, 9);
            for (std::size_t index = 1; index < 7; ++index) identifier(tokens[index]);
            if (result.sources.size() >= 64) throw Error("source count exceeds bound");
            result.sources.push_back({{tokens[1], tokens[2], tokens[3], tokens[4], tokens[5], tokens[6]}, tokens[7], tokens[8]});
        } else if (tokens[0] == "CONFIG") {
            count(tokens, 3);
            if (!config.values.emplace(tokens[1], tokens[2]).second) throw Error("duplicate replay config");
        } else if (tokens[0] == "REPLAY_CONFIG") {
            count(tokens, 3);
            if (!extra.emplace(tokens[1], tokens[2]).second) throw Error("duplicate replay policy");
        } else if (tokens[0] == "FEE_RULE") {
            count(tokens, 7);
            if (tokens[1] != "buy" && tokens[1] != "sell") throw Error("unknown fee rule side");
            Fee fee {tokens[2], Rational(tokens[3]), Rational(tokens[4]), optional_number(tokens[5]),
                     static_cast<unsigned>(integer(tokens[6], 1, 100000))};
            fee.validate();
            if (!config.fees.emplace(tokens[1], std::move(fee)).second) throw Error("duplicate fee rule");
        } else throw Error("unknown replay row type");
    }
    if (result.sources.empty()) throw Error("replay sources required");
    std::sort(result.sources.begin(), result.sources.end(), [](const auto& a, const auto& b) {
        return std::tie(a.scope.venue, a.scope.symbol, a.scope.account, a.scope.allocation)
             < std::tie(b.scope.venue, b.scope.symbol, b.scope.account, b.scope.allocation);
    });
    std::set<std::pair<std::string, std::string>> markets;
    std::set<std::string> paths;
    for (const auto& source : result.sources) {
        if (!source.scope.symbol.ends_with("/USDT")) throw Error("primary study requires common USDT spot quote");
        if (!markets.emplace(source.scope.venue, source.scope.symbol).second || !paths.insert(source.path).second)
            throw Error("duplicate study market or source");
    }
    config.scope = result.sources.front().scope;
    auto initial = config.build(); result.context = std::move(initial.first);
    result.initial_quote = Rational(initial.second.quote_cash) * result.context.quote_quantum;
    if (result.context.buy_fee.currency != "QUOTE" || result.context.sell_fee.currency != "QUOTE")
        throw Error("historical primary study supports explicitly funded quote fees only");
    const auto take = [&](const std::string& key) {
        const auto found = extra.find(key);
        if (found == extra.end()) throw Error("missing replay policy: " + key);
        std::string value = found->second; extra.erase(found); return value;
    };
    const auto start = take("start_ms"), end = take("end_ms");
    if (start != "-") result.start_ms = integer(start, minute_ms, max_timestamp_ms);
    if (end != "-") result.end_ms = integer(end, minute_ms, max_timestamp_ms + minute_ms);
    if (result.start_ms.has_value() != result.end_ms.has_value() ||
        (result.start_ms && (*result.start_ms % minute_ms != 0 || *result.end_ms % minute_ms != 0 || *result.start_ms >= *result.end_ms)))
        throw Error("invalid replay study interval");
    result.model.model = take("model"); result.model.latency_bars = integer(take("latency_bars"), 1, 1000000);
    result.model.spread = Rational(take("synthetic_spread"));
    result.model.require_tick_compatibility = boolean(take("require_source_tick_compatibility"));
    result.context.entry_policy = take("entry"); result.context.ladder_policy = take("ladder");
    result.context.reentry_policy = take("reentry_policy");
    if (result.context.entry_policy != "seed_min_limit" && result.context.entry_policy != "no_seed_lower") throw Error("unsupported entry policy");
    if (result.context.ladder_policy != "ladder_multi" && result.context.ladder_policy != "ladder_one") throw Error("unsupported ladder policy");
    if (result.context.ladder_policy == "ladder_one" && result.context.policy.ladder_size != 1) throw Error("single ladder requires one pending slot");
    if (result.context.reentry_policy != "reentry_near_fresh_book" && result.context.reentry_policy != "reentry_one_step_below_sell")
        throw Error("unsupported reentry policy");
    auto& portfolio = result.portfolio;
    portfolio.exchange_enabled = boolean(take("exchange_tp_enabled")); portfolio.symbol_enabled = boolean(take("symbol_tp_enabled"));
    portfolio.exchange_threshold = Rational(take("exchange_tp_threshold")); portfolio.symbol_threshold = Rational(take("symbol_tp_threshold"));
    portfolio.max_deviation = Rational(take("max_deviation")); portfolio.mark_max_age_ms = integer(take("mark_max_age_ms"));
    portfolio.ttl_ms = integer(take("exit_ttl_ms"), 1); portfolio.duration_ms = integer(take("exit_duration_ms"), 1);
    portfolio.max_reprices = static_cast<unsigned>(integer(take("max_reprices"), 0, 10000));
    portfolio.max_cycles = static_cast<unsigned>(integer(take("max_cycles"), 1, 10000));
    portfolio.nav_order_budget = static_cast<unsigned>(integer(take("nav_order_budget"), 1, 10000));
    if (!extra.empty()) throw Error("unknown replay policy: " + extra.begin()->first);
    return result;
}
} // namespace grid
