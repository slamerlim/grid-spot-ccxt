#include "history.hpp"
#include "market.hpp"

#include <array>
#include <bit>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <limits>
#include <random>
#include <string_view>
#include <unistd.h>

namespace {
unsigned checks = 0;
void check(bool condition, const char* detail) {
    ++checks;
    if (!condition) throw std::runtime_error(detail);
}
template <typename Function> void rejects(Function function, const char* detail) {
    bool rejected = false;
    try { function(); } catch (const grid::Error&) { rejected = true; }
    check(rejected, detail);
}
grid::Rational q(const char* value) { return grid::Rational(value); }
grid::Market market() { return {q(".01"), q(".001"), {q(".01"), q("1000000")},
                                {q(".001"), std::nullopt}, {q("10"), std::nullopt},
                                {q(".001"), std::nullopt}, {q("10"), std::nullopt}}; }
grid::Fee fee() { return {"QUOTE", q(".001"), q(".00001"), std::nullopt, 1}; }
std::array<std::byte, 24> record(std::int64_t timestamp, std::array<float, 4> prices) {
    std::array<std::byte, 24> result {};
    const auto stamp = static_cast<std::uint64_t>(timestamp);
    for (std::size_t index = 0; index < 8; ++index) result[index] = static_cast<std::byte>((stamp >> (index * 8)) & 255U);
    for (std::size_t field = 0; field < 4; ++field) {
        const auto bits = std::bit_cast<std::uint32_t>(prices[field]);
        for (std::size_t index = 0; index < 4; ++index)
            result[8 + field * 4 + index] = static_cast<std::byte>((bits >> (index * 8)) & 255U);
    }
    return result;
}
struct Temporary {
    std::filesystem::path path;
    Temporary() {
        std::array<char, 64> name {};
        const std::string pattern = "/tmp/grid-cpp-primitives-XXXXXX";
        std::copy(pattern.begin(), pattern.end(), name.begin());
        char* made = ::mkdtemp(name.data());
        if (made == nullptr) throw std::runtime_error("temporary directory failed");
        path = made;
    }
    ~Temporary() { std::error_code ignored; std::filesystem::remove_all(path, ignored); }
};
void append(const std::filesystem::path& path, const std::array<std::byte, 24>& raw) {
    std::ofstream out(path, std::ios::binary | std::ios::app);
    out.write(reinterpret_cast<const char*>(raw.data()), static_cast<std::streamsize>(raw.size()));
    if (!out) throw std::runtime_error("fixture write failed");
}
void numeric() {
    check(q(".1") + q(".2") == q(".3"), "decimal addition");
    check((q("1") / q("3")).ratio() == "1/3", "recurring exact ratio");
    const auto thirds = (q("1") / q("3")).decimal96();
    check(thirds.str() == "0." + std::string(96, '3'), "Decimal96 recurring division");
    const auto tie_even = q("1") + q("5e-96");
    check(tie_even.decimal96() == q("1"), "Decimal96 half-even even neighbor");
    check((q("1") + q("15e-96")).decimal96() == q("1") + q("2e-95"), "Decimal96 half-even odd neighbor");
    check((q("0") - tie_even).decimal96() == q("-1"), "Decimal96 symmetric negative ties");
    check((q("38") / q("103")).decimal96() * q("1.03") > q(".38"), "Decimal96 operation-order boundary");
    check((q("-18446744073709551615") / q("3")) == q("-6148914691236517205"), "native GCD preserves negative numerator at word maximum");
    check((q("18446744073709551616") / q("-18446744073709551616")).ratio() == "-1/1", "large GCD normalizes denominator sign");
    check((q("0") / q("1e90")).ratio() == "0/1", "zero fraction canonicalizes directly");
    grid::Rational first(1), second(1);
    for (unsigned n = 0; n < 256; ++n) { auto next = first + second; first = second; second = next; }
    const auto coprime = first / second;
    check(coprime.ratio() == first.str() + "/" + second.str(), "large consecutive Fibonacci coprimes stay canonical");
    check((coprime * second) == first, "large Euclidean normalization preserves exact value");
    check(((first * q("123456789")) / (second * q("123456789"))) == coprime, "large common factor removed exactly");
    check((q("1") / q("8")).str() == "0.125", "terminating decimal");
    check((q("1.1") - q("2.1")).str() == "-1", "signed arithmetic");
    check(q("1e-12").str() == "0.000000000001", "exponent parse");
    check(q("0.009").rounded(q(".005"), false) == q(".005"), "arbitrary tick floor");
    check(q("0.009").rounded(q(".005"), true) == q(".01"), "arbitrary tick ceil");
    check((q("1") / q("3")).rounded(q(".01"), true) == q(".34"), "rational ceil");
    check(q("-1.25").exact_units(q(".01")) == -125, "signed units");
    check(grid::exact_product(12345678, 8, 1234, 4, 12) == 15234566652LL, "price8 times quantity4 exact quote12");
    rejects([] { (void)grid::exact_product(1, 8, 1, 4, 8); }, "posting never rounds");
    rejects([] { (void)grid::checked_add(std::numeric_limits<std::int64_t>::max(), 1); }, "add overflow");
    rejects([] { (void)grid::checked_sub(std::numeric_limits<std::int64_t>::min(), 1); }, "subtract overflow");
    rejects([] { (void)grid::exact_product(std::numeric_limits<std::int64_t>::max(), 0,
                                          std::numeric_limits<std::int64_t>::max(), 0, 18); }, "intermediate overflow");
    rejects([] { (void)q(".001").exact_units(q(".01")); }, "unrepresentable currency");
    rejects([] { (void)(q("1") / q("0")); }, "divide by zero");
    rejects([] { (void)q("NaN"); }, "nonfinite decimal");
    rejects([] { (void)q("1e97"); }, "decimal exponent bound");
    rejects([] { (void)q("1").power(10001); }, "geometric index bound");
    const auto render_boundary = q(".1").power(10000);
    check(render_boundary.str() == "0." + std::string(9999, '0') + "1",
          "10,000 decimal places remain renderable exactly");
    bool render_rejected = false;
    try { (void)(render_boundary * q(".1")).str(); }
    catch (const grid::Error& error) {
        render_rejected = std::string_view(error.what()) == "decimal rendering bound exceeded";
    }
    check(render_rejected, "10,001 decimal places reach the rendering guard");
    std::mt19937 random(581);
    std::int64_t cash = 1000000000000000LL;
    for (unsigned index = 0; index < 10000; ++index) {
        const auto amount = static_cast<std::int64_t>(random() % 1000000U);
        const auto before = cash;
        cash = grid::checked_sub(cash, amount);
        cash = grid::checked_add(cash, amount);
        check(cash == before, "seeded conservation");
    }
}
void financial() {
    const auto rules = market();
    auto boundary_rules = rules;
    boundary_rules.buy_cost.minimum = boundary_rules.sell_cost.minimum = q("0");
    boundary_rules.buy_amount.minimum = q("1"); boundary_rules.sell_amount.minimum = q(".1");
    const grid::Fee boundary_fee {"QUOTE", q(".001"), q(".0001"), std::nullopt, 64};
    check(grid::aggregate_take_profit(q("103"), q("38"), boundary_rules, q(".03"), q(".01"), boundary_fee).price == q(".39"),
          "Python Decimal96 boundary preserves plus-one-tick TP");
    check(grid::aggregate_take_profit(q("3"), q("5"), boundary_rules, q("2"), q(".01"), boundary_fee).price == q("5.01"),
          "Python Decimal96 recurring division multiplication regression");
    const auto prices = grid::grid_prices(q("100"), q(".1"), {1, 2, 3}, rules);
    check(prices[0] == q("90.90") && prices[1] == q("82.64") && prices[2] == q("75.13"), "geometric exact floor");
    const auto long_grid = grid::grid_prices(q("1000"), q(".01"), {1, 100, 1000}, rules);
    check(long_grid.size() == 3 && long_grid[2] == q(".04"), "wide geometric power");
    rejects([&] { (void)grid::grid_prices(q(".01"), q(".00001"), {1, 2}, rules); }, "collapsed level");
    rejects([&] { (void)grid::grid_prices(q("100"), q(".01"), {2, 1}, rules); }, "unsorted levels");
    const auto plan = grid::minimum_buy(q("9.09"), rules, fee(), fee(), q(".01"), q(".01"), q("100"));
    check(plan.amount == q("1.101"), "smallest executable buy");
    check(plan.quote_required == q("10.0181"), "fee and full cash reserve");
    check(plan.prospective_sell.price == q("9.20"), "aggregate sell geometric floor");
    check(plan.prospective_sell.reserved_base == plan.net_base, "one base reserve");
    const grid::Fee base {"BASE", q(".001"), q(".001"), std::nullopt, 1};
    auto strict_market = rules;
    strict_market.buy_amount.minimum = q("1");
    strict_market.sell_amount.minimum = q("1");
    rejects([&] { (void)grid::minimum_buy(q("10"), strict_market, base, fee(), q(".01"), q(".01"), q("100")); }, "never enlarge minimum to hide base fee");
    const grid::Fee token {"TOKEN", q(".001"), q(".0001"), q("2"), 4};
    const auto token_buy = grid::minimum_buy(q("10"), rules, token, fee(), q(".01"), q(".01"), q("100"));
    check(token_buy.quote_required == q("10"), "third token not quote debit");
    check(token_buy.acquisition_cost == q("10.0106"), "third token has quote cost");
    const auto base_sell = grid::aggregate_take_profit(q("1.001"), q("10"), rules, q(".01"), q(".01"), base);
    check(base_sell.amount == q("1") && base_sell.reserved_base == q("1.001"), "base sell fee fits");
    rejects([&] { (void)grid::minimum_buy(q("10"), rules, fee(), fee(), q(".01"), q(".01"), q("10")); }, "cash budget includes fee");
}
void source() {
    const auto raw = record(60000, {1.25F, 1.5F, 1.0F, 1.375F});
    const auto bar = grid::decode(raw);
    check(bar.timestamp == 60000 && bar.prices[0].exact() == q("1.25"), "little endian layout");
    check(bar.prices[2].rounding_radius() == q("0.000000059604644775390625"), "power-of-two half ULP");
    check(!bar.prices[2].buy_crossed(q("1"), true) && bar.prices[2].buy_crossed(q("1"), false), "strict versus touch buy");
    const auto upper_boundary = bar.prices[2].exact() + bar.prices[2].rounding_radius();
    check(!bar.prices[2].buy_crossed(upper_boundary, true) &&
          bar.prices[2].buy_crossed(upper_boundary + q("1e-30"), true), "strict half-ULP equality and through");
    check(!bar.prices[1].sell_crossed(q("1.5"), true) && bar.prices[1].sell_crossed(q("1.5"), false), "strict versus touch sell");
    check(bar.prices[0].tick_compatible(q(".01")), "tick compatible");
    check(!bar.prices[0].tick_compatible(q(".1")), "tick incompatible");
    check(grid::Float32Price{1}.rounding_radius() == grid::Rational::dyadic(1, -150), "subnormal radius");
    check(!grid::Float32Price{1}.tick_compatible(q("1e-8")), "subnormal incompatible with declared financial tick");
    check(grid::Float32Price{0x7f7fffffU}.rounding_radius() == grid::Rational::dyadic(1, 103), "maximum finite radius");
    rejects([&] { (void)grid::decode(std::span(raw).first(23)); }, "partial record");
    rejects([] { (void)grid::decode(record(60001, {1, 1, 1, 1})); }, "minute alignment");
    rejects([] { (void)grid::decode(record(-60000, {1, 1, 1, 1})); }, "negative timestamp");
    rejects([] { (void)grid::decode(record(60000, {0, 1, 1, 1})); }, "zero price");
    rejects([] { (void)grid::decode(record(60000, {-1, 1, 1, 1})); }, "negative price");
    rejects([] { (void)grid::decode(record(60000, {1, std::numeric_limits<float>::infinity(), 1, 1})); }, "nonfinite price");
    rejects([] { (void)grid::decode(record(60000, {2, 1, 1, 1})); }, "impossible OHLC");
    Temporary temporary;
    const auto path = temporary.path / "history.bin";
    append(path, raw);
    append(path, raw);
    append(path, record(120000, {0, 1, 1, 1}));
    append(path, record(240000, {1, 1, 1, 1}));
    append(path, record(180000, {1, 1, 1, 1}));
    append(path, record(grid::max_timestamp_ms, {1, 1, 1, 1}));
    unsigned gap_count = 0;
    grid::RawReader reader(path.string(), {}, [&gap_count](const grid::Gap&) { ++gap_count; });
    grid::Bar current {};
    while (reader.next(current)) {}
    const auto stats = reader.stats();
    check(stats.accepted == 3 && stats.rejected == 3 && stats.records == 6, "bounded invalid diagnostics");
    check(stats.reasons.at("duplicate_minute") == 1 && stats.reasons.at("out_of_order") == 1, "chronological rejections");
    check(gap_count == 2 && stats.missing_minutes == static_cast<std::uint64_t>(grid::max_timestamp_ms / 60000 - 3), "long-span gap accounting");
    check(stats.sha256.size() == 64 && stats.complete && stats.bytes == 144, "source hash completion");
    grid::RawReader again(path.string(), stats.sha256);
    while (again.next(current)) {}
    check(again.stats().sha256 == stats.sha256, "rerun source hash");
    rejects([&] { grid::RawReader bad_hash(path.string(), std::string(64, '0')); while (bad_hash.next(current)) {} }, "hash mismatch");
    grid::RawReader changing(path.string());
    check(changing.next(current), "first buffered bar");
    append(path, record(grid::max_timestamp_ms, {1, 1, 1, 1}));
    rejects([&] { while (changing.next(current)) {} }, "source mutation rejects entire run");
    const auto initially_tiny = temporary.path / "growing.bin";
    append(initially_tiny, raw);
    grid::RawReader growing(initially_tiny.string());
    append(initially_tiny, record(120000, {1, 1, 1, 1}));
    rejects([&] { (void)growing.next(current); }, "growth before first read cannot underflow size bound");
    const auto truncated = temporary.path / "truncated.bin";
    append(truncated, raw);
    append(truncated, record(120000, {1, 1, 1, 1}));
    grid::RawReader shrinking(truncated.string());
    std::filesystem::resize_file(truncated, 25);
    bool truncation_rejected = false;
    try { (void)shrinking.next(current); }
    catch (const grid::Error& error) {
        truncation_rejected = std::string(error.what()) == "source truncated during read";
    }
    check(truncation_rejected, "partial source truncation after admission rejects before first bar");
    const auto linked = temporary.path / "linked.bin";
    std::filesystem::create_symlink(path, linked);
    rejects([&] { grid::RawReader symlink(linked.string()); }, "source symlink refused");
    const auto malformed = temporary.path / "partial.bin";
    { std::ofstream out(malformed); out << 'x'; }
    rejects([&] { grid::RawReader partial(malformed.string()); }, "malformed source length");
}
} // namespace

int main() {
    try {
        numeric(); financial(); source();
        std::cout << checks << " primitive checks passed\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "check " << checks << " failed: " << error.what() << '\n';
        return 1;
    }
}
