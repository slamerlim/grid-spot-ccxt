#include "history.hpp"
#include "market.hpp"
#include "trace.hpp"
#include "study.hpp"

#include <charconv>
#include <iostream>
#include <string_view>

namespace {
unsigned integer(std::string_view text) {
    unsigned result = 0;
    const auto parsed = std::from_chars(text.data(), text.data() + text.size(), result);
    if (parsed.ec != std::errc{} || parsed.ptr != text.data() + text.size()) throw grid::Error("unsigned integer required");
    return result;
}
grid::Market market(char** args) {
    return {grid::Rational(args[0]), grid::Rational(args[1]), {grid::Rational(), std::nullopt},
            {grid::Rational(args[2]), std::nullopt}, {grid::Rational(args[3]), std::nullopt},
            {grid::Rational(args[4]), std::nullopt}, {grid::Rational(args[5]), std::nullopt}};
}
grid::Fee fee(char** args) {
    return {args[0], grid::Rational(args[1]), grid::Rational(args[2]),
            std::string_view(args[3]) == "-" ? std::nullopt : std::optional(grid::Rational(args[3])), integer(args[4])};
}
void usage() {
    std::cerr << "grid_backtest scan SOURCE [EXPECTED_SHA256] [--gaps]\n"
              << "grid_backtest decode HEX_24_BYTES\n"
              << "grid_backtest trace TRACE_TSV_FILE\n"
              << "grid_backtest bars BAR_TSV_FILE\n"
              << "grid_backtest replay STUDY_TSV_FILE\n"
              << "grid_backtest math-grid ANCHOR SPACING PRICE_TICK INDEX...\n"
              << "grid_backtest math-buy PRICE <MARKET> <BUY_FEE> <SELL_FEE> SPACING NET_TARGET AVAILABLE_QUOTE\n"
              << "grid_backtest math-tp OWNED COST <MARKET> <SELL_FEE> SPACING NET_TARGET\n"
              << "MARKET: PRICE_TICK AMOUNT_TICK BUY_MIN_AMOUNT BUY_MIN_COST SELL_MIN_AMOUNT SELL_MIN_COST\n"
              << "FEE: CURRENCY RATE INCREMENT CONVERSION_OR_- MAX_SETTLEMENTS\n";
}
} // namespace

int main(int argc, char** argv) {
    try {
        if (argc < 2) { usage(); return 2; }
        const std::string_view command(argv[1]);
        if (command == "scan" && argc >= 3 && argc <= 5) {
            bool gaps = false;
            std::string digest;
            for (int index = 3; index < argc; ++index) {
                if (std::string_view(argv[index]) == "--gaps") gaps = true;
                else if (digest.empty()) digest = argv[index];
                else throw grid::Error("duplicate expected digest");
            }
            grid::RawReader reader(argv[2], digest, gaps ? std::function<void(const grid::Gap&)>([](const grid::Gap& gap) {
                std::cout << "{\"type\":\"gap\",\"previous_ms\":" << gap.previous_ms << ",\"next_ms\":" << gap.next_ms
                          << ",\"missing_minutes\":" << gap.missing_minutes << "}\n";
            }) : std::function<void(const grid::Gap&)>());
            grid::Bar bar {};
            while (reader.next(bar)) {}
            std::cout << grid::stats_json(reader.stats()) << '\n';
        } else if (command == "trace" && argc == 3) {
            grid::run_trace(argv[2], std::cout);
        } else if (command == "bars" && argc == 3) {
            grid::run_bars(argv[2], std::cout);
        } else if (command == "replay" && argc == 3) {
            grid::run_study(grid::read_study(argv[2]), std::cout);
        } else if (command == "decode" && argc == 3) {
            const std::string_view raw(argv[2]);
            if (raw.size() != grid::record_bytes * 2) throw grid::Error("48 hexadecimal digits required");
            std::array<std::byte, grid::record_bytes> bytes {};
            for (std::size_t index = 0; index < bytes.size(); ++index) {
                unsigned value = 0;
                const auto parsed = std::from_chars(raw.data() + index * 2, raw.data() + index * 2 + 2, value, 16);
                if (parsed.ec != std::errc{} || parsed.ptr != raw.data() + index * 2 + 2) throw grid::Error("invalid hexadecimal");
                bytes[index] = static_cast<std::byte>(value);
            }
            const grid::Bar bar = grid::decode(bytes);
            std::cout << "{\"timestamp\":" << bar.timestamp << ",\"prices\":[";
            for (std::size_t index = 0; index < bar.prices.size(); ++index) {
                if (index != 0) std::cout << ',';
                std::cout << grid::json_quote(bar.prices[index].exact().str());
            }
            std::cout << "],\"radii\":[";
            for (std::size_t index = 0; index < bar.prices.size(); ++index) {
                if (index != 0) std::cout << ',';
                std::cout << grid::json_quote(bar.prices[index].rounding_radius().str());
            }
            std::cout << "]}\n";
        } else if (command == "math-grid" && argc >= 6) {
            const grid::Limits unbounded {grid::Rational(), std::nullopt};
            const grid::Market rules {grid::Rational(argv[4]), grid::Rational(1), unbounded, unbounded,
                                      unbounded, unbounded, unbounded};
            std::vector<unsigned> indices;
            for (int index = 5; index < argc; ++index) indices.push_back(integer(argv[index]));
            const auto prices = grid::grid_prices(grid::Rational(argv[2]), grid::Rational(argv[3]), indices, rules);
            std::cout << "{\"prices\":[";
            for (std::size_t index = 0; index < prices.size(); ++index) {
                if (index != 0) std::cout << ',';
                std::cout << grid::json_quote(prices[index].str());
            }
            std::cout << "]}\n";
        } else if (command == "math-buy" && argc == 22) {
            const grid::Fee buy_fee = fee(argv + 9), sell_fee = fee(argv + 14);
            const auto plan = grid::minimum_buy(grid::Rational(argv[2]), market(argv + 3), buy_fee, sell_fee,
                                                grid::Rational(argv[19]), grid::Rational(argv[20]), grid::Rational(argv[21]));
            std::cout << grid::buy_json(plan, buy_fee, sell_fee) << '\n';
        } else if (command == "math-tp" && argc == 17) {
            const grid::Fee sell_fee = fee(argv + 10);
            const auto plan = grid::aggregate_take_profit(grid::Rational(argv[2]), grid::Rational(argv[3]), market(argv + 4),
                                                          grid::Rational(argv[15]), grid::Rational(argv[16]), sell_fee);
            std::cout << grid::tp_json(plan, sell_fee) << '\n';
        } else { usage(); return 2; }
        if (!std::cout) throw grid::Error("output write failed");
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "{\"error\":" << grid::json_quote(error.what()) << "}\n";
        return 2;
    }
}
