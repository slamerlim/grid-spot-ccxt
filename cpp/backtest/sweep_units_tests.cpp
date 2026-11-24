#include "sweep_units.hpp"

#include <array>
#include <atomic>
#include <functional>
#include <iostream>
#include <limits>
#include <random>
#include <thread>
#include <vector>

namespace {
using grid::Rational;
using grid::sweep::units::Integer;
using grid::sweep::units::Scale;
using grid::sweep::units::Float32Coefficient;
void check(bool condition, const char* message) { if (!condition) throw grid::Error(message); }
void rejects(const std::function<void()>& action, const char* message) {
    try { action(); } catch (const grid::Error&) { return; }
    throw grid::Error(message);
}
void arithmetic() {
    const Integer minimum("-170141183460469231731687303715884105728");
    const Integer maximum("170141183460469231731687303715884105727");
    const Integer above("170141183460469231731687303715884105728");
    const Integer below("-170141183460469231731687303715884105729");
    check(minimum.is_small() && maximum.is_small(), "full signed int128 range stays native");
    check(Integer().bit_width() == 0 && Integer(1).bit_width() == 1 && Integer(-1).bit_width() == 1,
          "zero and signed native bit widths are exact");
    check(minimum.bit_width() == 128 && maximum.bit_width() == 127 &&
          above.bit_width() == 128 && below.bit_width() == 128,
          "bit width preserves signed boundaries and wide promotion");
    check(!above.is_small() && !below.is_small(), "outside signed int128 promotes");
    check(minimum.str() == "-170141183460469231731687303715884105728", "minimum renders safely");
    std::string appended("exact:"); minimum.append_to(appended);
    check(appended == "exact:" + minimum.str(), "append preserves the destination prefix and signed minimum");
    check(minimum.ratio() == minimum.str() + "/1", "integer ratio has denominator one");
    check(maximum + Integer(1) == above, "addition overflow promotes");
    check(minimum - Integer(1) == below, "subtraction overflow promotes");
    check(-minimum == above && -above == minimum, "negation around signed minimum");
    check(minimum / Integer(-1) == above, "minimum divided by minus one promotes");
    check(minimum * Integer(-1) == above, "minimum multiplied by minus one promotes");
    check(above - Integer(1) == maximum && (above - Integer(1)).is_small(), "big subtraction narrows");
    check(below + Integer(1) == minimum && (below + Integer(1)).is_small(), "negative big addition narrows");
    check(minimum < maximum && below < minimum && maximum < above && below < above, "mixed comparisons");
    check(above > Integer() && below < Integer() && Integer() < above, "mixed zero comparisons");
    check(minimum.sign() == -1 && above.sign() == 1 && Integer().sign() == 0, "native and big signs");
    check(Integer("-0") == Integer("+000") && Integer("-0").str() == "0", "zero is canonical");
    const auto square = above * above;
    check(minimum.storage_bytes_estimate() == 0 && Integer().storage_bytes_estimate() == 0,
          "native integer adds no heap estimate");
    check(above.storage_bytes_estimate() >= 16 &&
          above.storage_bytes_estimate() == below.storage_bytes_estimate(),
          "big storage estimate uses magnitude and includes limb bytes");
    check(square.storage_bytes_estimate() > above.storage_bytes_estimate(),
          "big storage estimate grows with limb count");
    check(above.str() == "170141183460469231731687303715884105728",
          "storage observation preserves exact operand");
    check(!square.is_small() && square / above == above, "large exact product and quotient");
    check((-square) / above == -above && square / (-above) == -above, "signed big exact division");
    check(square * Integer() == Integer(), "large zero product");
    check(Integer::ceil_div_positive(Integer(17), Integer(5)) == Integer(4), "native positive ceiling");
    check(Integer::ceil_div_positive(Integer(15), Integer(5)) == Integer(3), "exact ceiling unchanged");
    check(Integer::ceil_div_positive(Integer(), above) == Integer(), "zero big-divisor ceiling");
    check(Integer::ceil_div_positive(above * Integer(3) + Integer(1), Integer(3)) == above + Integer(1),
          "large positive ceiling preserves remainder");
    check(Integer::ceil_div_positive(above - Integer(1), above) == Integer(1), "small over big ceiling");
    check(Integer::ceil_div_positive(square, above) == above, "exact large ceiling");
    for (std::int64_t numerator = 0; numerator <= 40; ++numerator)
        for (std::int64_t denominator = 1; denominator <= 13; ++denominator)
            check(Integer::floor_div_positive(Integer(numerator), Integer(denominator)) ==
                  Integer(numerator / denominator), "native floor agrees with integer quotient");
    check(Integer::floor_div_positive(Integer(), above) == Integer(), "zero big-divisor floor");
    check(Integer::floor_div_positive(above - Integer(1), above) == Integer(), "small over big floor");
    check(Integer::floor_div_positive(square, above) == above, "exact big floor");
    check(Integer::floor_div_positive(square + above - Integer(1), above) == above,
          "big floor discards only the positive remainder");
    const auto narrowed_floor = Integer::floor_div_positive(above, Integer(2));
    check(narrowed_floor.is_small() && narrowed_floor * Integer(2) == above,
          "big floor narrows into native range exactly");
    rejects([] { (void)Integer::floor_div_positive(Integer(-1), Integer(3)); }, "negative floor numerator rejects");
    rejects([] { (void)Integer::floor_div_positive(Integer(1), Integer(-1)); }, "negative floor divisor rejects");
    rejects([] { (void)Integer::floor_div_positive(Integer(), Integer()); }, "zero floor divisor rejects");
    rejects([] { (void)Integer::ceil_div_positive(Integer(-1), Integer(3)); }, "negative ceiling numerator rejects");
    rejects([] { (void)Integer::ceil_div_positive(Integer(1), Integer(-1)); }, "negative ceiling divisor rejects");
    rejects([] { (void)Integer::ceil_div_positive(Integer(), Integer()); }, "zero ceiling divisor rejects");
    Integer accumulated(2);
    accumulated *= Integer(3); accumulated += Integer(5); accumulated -= Integer(1); accumulated /= Integer(2);
    check(accumulated == Integer(5), "compound operations");
    auto alias = above;
    alias += alias;
    check(alias == above * Integer(2), "big compound self-add preserves operands");
    alias -= alias;
    check(alias == Integer() && alias.is_small(), "big compound self-subtract narrows");
    alias = above;
    alias -= Integer(1);
    check(alias == maximum && alias.is_small(), "compound big-to-native narrowing");
    alias = below; alias += Integer(1);
    check(alias == minimum && alias.is_small(), "negative compound narrowing");
    check(Integer(1).shifted(127) == above, "left shift promotes at signed boundary");
    check(Integer(-1).shifted(127) == minimum, "negative left shift retains native minimum");
    check(minimum.shifted(-127) == Integer(-1), "minimum right shift exact");
    check(above.shifted(500).shifted(-500) == above, "big shift roundtrip");
    rejects([] { (void)Integer(-3).shifted(-1); }, "negative right shift must not truncate");
    rejects([] { (void)Integer(1).shifted(-128); }, "native large right shift must not erase value");
    rejects([&] { (void)(above + Integer(1)).shifted(-1); }, "big right shift must not truncate");
    rejects([] { (void)(Integer(1) / Integer()); }, "zero divisor must reject");
    rejects([] { (void)(Integer(-5) / Integer(2)); }, "native division must not truncate");
    rejects([&] { (void)((square + Integer(1)) / above); }, "big division must not truncate");
    for (const std::string invalid : {"", "+", "-", " 1", "1 ", "1.0", "1e2", "--1", "0x1"})
        rejects([&] { (void)Integer(invalid); }, "noninteger syntax must reject");
    const auto bounded = Scale(131071, 0).denominator();
    check(bounded.sign() == 1 && !bounded.is_small(), "maximum bit bound allows exact boundary");
    rejects([&] { (void)(bounded + bounded); }, "addition must enforce bit bound");
    rejects([&] { (void)(bounded * Integer(2)); }, "product must enforce bit bound");
    auto unchanged = bounded;
    rejects([&] { unchanged += bounded; }, "compound sum must enforce bit bound");
    check(unchanged == bounded, "rejected compound sum retains original value");
    rejects([&] { (void)bounded.shifted(1); }, "shift must enforce bit bound");
    rejects([&] { (void)Integer(bounded.str() + "0"); }, "parse must enforce bit bound");
    rejects([] { (void)Scale(131072, 0); }, "scale must enforce bit bound");
    rejects([] { (void)Scale(100000, 20000); }, "combined scale must enforce bit bound");
}
void scales() {
    const auto cents = Scale::for_value(Rational("0.01"));
    check(cents.twos() == 2 && cents.fives() == 2, "decimal denominator factorization");
    const auto eighth = Scale::for_value(Rational("0.125"));
    check(eighth.twos() == 3 && eighth.fives() == 0, "factor only reduced denominator");
    check(Scale::for_value(Rational()).denominator() == Integer(1), "zero has unit scale");
    check(Scale::for_value(Rational("-0.08")).denominator() == Integer(25), "negative scale factorization");
    check(cents.encode(Rational("-12.34")) == Integer(-1234), "negative money converts exactly");
    check(cents.decode(Integer(-1234)).ratio() == "-617/50", "decoded ratio is canonical");
    check(cents.render(Integer(-1234)) == "-617/50", "direct scale rendering is canonical");
    check(cents.render(Integer()) == "0/1", "direct scale zero canonical");
    check(Scale().encode(Rational(7)) == Integer(7), "integral unit scale");
    check(Integer::from_rational_integer(Rational(10) / Rational(2)) == Integer(5), "exact integer from ratio");
    rejects([] { (void)Integer::from_rational_integer(Rational("0.5")); }, "fractional integer conversion rejects");
    rejects([&] { (void)cents.encode(Rational("0.001")); }, "nonrepresentable monetary value rejects");
    rejects([] { (void)Scale::for_value(Rational(1) / Rational(3)); }, "third denominator factor rejects");
    rejects([] { (void)Scale(3, 3).encode(Rational(1) / Rational(3)); }, "nondecimal conversion rejects");
    const auto common = grid::sweep::units::common_scale(cents, eighth);
    check(common.denominator() == Integer(200), "common scale uses maximum prime exponents");
    check(cents.coefficient_to(common) == Integer(2), "cents common multiplier");
    check(eighth.coefficient_to(common) == Integer(25), "eighth common multiplier");
    check(common.decode(cents.encode(Rational("12.34")) * cents.coefficient_to(common)) == Rational("12.34"),
          "common scale preserves value");
    rejects([&] { (void)common.coefficient_to(cents); }, "narrowing scale rejects");
    const Integer long_value("123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901");
    check(Scale().decode(long_value).ratio() == long_value.ratio(), "over 96 digits decode without decimal truncation");
    check(cents.encode(cents.decode(long_value)) == long_value, "large money roundtrip");
    check(cents.encode(cents.decode(-long_value)) == -long_value, "negative large money roundtrip");
    const auto large_scale = Scale(1500, 500);
    check(large_scale.encode(large_scale.decode(Integer(-17))) == Integer(-17), "large scale roundtrip");
    const std::array<Integer, 9> render_values{Integer(), Integer(1), Integer(-1), Integer(12000),
        Integer(-1000000), long_value, -long_value,
        Integer("-170141183460469231731687303715884105728"), large_scale.denominator()};
    for (const auto& scale : {Scale(), cents, Scale(149, 8), large_scale})
        for (const auto& value : render_values)
            check(scale.render(value) == scale.decode(value).ratio(), "direct render matches exact Rational canonical ratio");
    for (const auto& denominator : {Integer(1), Integer(500), Integer("170141183460469231731687303715884105727"),
                                   large_scale.denominator()})
        for (const auto& value : render_values)
            check(value.fraction(denominator) == (Scale().decode(value) / Scale().decode(denominator)).ratio(),
                  "direct general fraction preserves exact sign and reduction across native and big integers");
    for (const auto& value : {Rational(), Rational("-12.34"), Rational("1e-96"), Rational("1e96"),
                             Rational(std::numeric_limits<std::int64_t>::min())}) {
        check(grid::sweep::units::render_ratio(value) == Rational(value).ratio(),
              "read-only rational rendering matches canonical bytes without materializing native values");
        std::string prefix("exact:"); grid::sweep::units::append_ratio(prefix, value);
        check(prefix == "exact:" + Rational(value).ratio(), "rational append retains existing bytes");
    }
    rejects([] { (void)Integer(1).fraction(Integer()); }, "zero fraction denominator rejects");
    rejects([] { (void)Integer(1).fraction(Integer(-1)); }, "negative fraction denominator rejects");
}
void native_fraction_reduction() {
    // Exercise signed boundaries and wide intermediates through the public
    // arithmetic interface, independently of the chosen gcd implementation.
    const Rational max64(std::numeric_limits<std::int64_t>::max());
    const Rational min64(std::numeric_limits<std::int64_t>::min());
    check((Rational(12) / Rational(-18)).ratio() == "-2/3", "negative divisor canonicalizes before gcd");
    check((Rational() / max64).ratio() == "0/1", "zero numerator retains unit denominator");
    check(((max64 / Rational(2)) * Rational(2)).ratio() == "9223372036854775807/1",
          "uint64-size intermediate reduces without narrowing before gcd");
    check(((min64 / Rational(3)) * Rational(3)).ratio() == "-9223372036854775808/1",
          "negative uint64-size intermediate preserves the signed int64 boundary");
    check(((max64 / Rational(2)) * (Rational(2) / max64)).ratio() == "1/1",
          "wide intermediate reduces across the uint64 boundary");
    check((max64 * max64).ratio() == "85070591730234615847396907784232501249/1",
          "wide irreducible result promotes without truncation");
    for (std::int64_t n = -73; n <= 73; ++n) {
        for (std::int64_t d = 1; d <= 59; ++d) {
            const auto value = Rational(n) / Rational(d);
            check(value * Rational(d) == Rational(n), "native arithmetic preserves the exact ratio across signs");
            check((value + value) / Rational(2) == value, "native reduction preserves addition roundtrip");
        }
    }
}
void rounded_products() {
    using grid::sweep::units::rounded_product;
    check(rounded_product(Rational(108), Rational(".99"), Rational(".01")) == Rational("106.92"),
          "executed sale anchor produces the exact next BUY");
    check(rounded_product(Rational("98.5"), Rational(".985"), Rational(1)) == Rational(97),
          "the next BUY continues from the unrounded prior level");
    check(rounded_product(Rational(), Rational(7), Rational(".01")) == Rational() &&
          rounded_product(Rational(7), Rational(), Rational(".01")) == Rational(),
          "either zero factor produces exact zero");
    check(rounded_product(Rational(".1"), Rational(".1"), Rational(1)) == Rational(),
          "sub-tick positive product rounds down to zero");
    rejects([&] { (void)rounded_product(Rational(-1), Rational(1), Rational(1)); }, "negative first factor rejects");
    rejects([&] { (void)rounded_product(Rational(1), Rational(-1), Rational(1)); }, "negative second factor rejects");
    rejects([&] { (void)rounded_product(Rational(), Rational(-1), Rational(1)); }, "zero does not hide a negative factor");
    rejects([&] { (void)rounded_product(Rational(), Rational(1), Rational()); }, "zero does not hide a zero tick");
    rejects([&] { (void)rounded_product(Rational(1), Rational(1), Rational(-1)); }, "negative tick rejects");
    const std::array<Rational, 5> ticks{Rational(1), Rational(".01"), Rational(".00000001"),
        Rational(".125"), Rational(1) / Rational(3)};
    for (const auto& tick : ticks) {
        const auto boundary = tick * Rational(7);
        const auto neighbor = tick / Rational(10000);
        check(rounded_product(Rational(3), boundary / Rational(3), tick) == boundary,
              "exact tick equality is unchanged");
        check(rounded_product(Rational(3), (boundary - neighbor) / Rational(3), tick) == tick * Rational(6),
              "exact product immediately below a tick rounds to the prior tick");
        check(rounded_product(Rational(3), (boundary + neighbor) / Rational(3), tick) == boundary,
              "exact product immediately above a tick rounds down without epsilon");
    }
    const std::array<Rational, 5> anchors{Rational(100), Rational(108), Rational(".00000003"),
        Rational::dyadic(16777215U, 104), Rational(1) / Rational(3)};
    for (std::int64_t step = 1; step <= 20; ++step) {
        const auto factor = Rational(200 - step) / Rational(200);
        Rational multiplier(1);
        for (unsigned level = 1; level <= 184; ++level) {
            multiplier = multiplier * factor;
            const auto& anchor = anchors[(static_cast<unsigned>(step) + level) % anchors.size()];
            const auto& tick = ticks[(static_cast<unsigned>(step) + 2 * level) % ticks.size()];
            check(rounded_product(anchor, multiplier, tick) == (anchor * multiplier).rounded(tick, false),
                  "all 20 grid steps and 184 unrounded levels match legacy exact rounding");
            const grid::sweep::units::PreparedProduct prepared(multiplier, tick);
            check(prepared(anchor) == (anchor * multiplier).rounded(tick, false),
                  "prepared shared grid factors preserve every exact rounded BUY level");
        }
    }
    const Rational native_max(std::numeric_limits<std::int64_t>::max());
    check(rounded_product(native_max, native_max, Rational(".01")) ==
          (native_max * native_max).rounded(Rational(".01"), false),
          "native operands promote an overflowing int128 quotient without losing precision");
    const Scale sixty_thousand(60000, 0), eighty_thousand(80000, 0);
    const auto large = Scale().decode(sixty_thousand.denominator());
    const auto reciprocal = sixty_thousand.decode(Integer(1));
    const auto tiny_tick = eighty_thousand.decode(Integer(1));
    rejects([&] { (void)(sixty_thousand.denominator() * eighty_thousand.denominator()); },
            "the unnormalized fused intermediate genuinely exceeds the integer bit bound");
    check((large * reciprocal).rounded(tiny_tick, false) == Rational(1),
          "legacy cancellation makes the exceptional large product valid");
    check(rounded_product(large, reciprocal, tiny_tick) == Rational(1),
          "exceptional intermediate overflow falls back to valid legacy exact arithmetic");
    const auto oversized = Scale().decode(Scale(70000, 0).denominator());
    rejects([&] { (void)(oversized * oversized).rounded(Rational(1), false); },
            "a genuinely oversized legacy product rejects");
    rejects([&] { (void)rounded_product(oversized, oversized, Rational(1)); },
            "fused rounding retains the genuine legacy bit-bound rejection");
}
void floats() {
    using grid::sweep::units::float32_coefficient;
    const Rational tick("0.00001");
    const Scale scale(154, 5);
    const std::array<std::pair<std::uint32_t, Rational>, 6> values{{
        {1, Rational::dyadic(1, -149)},
        {0x007fffffU, Rational::dyadic(8388607U, -149)},
        {0x00800000U, Rational::dyadic(1, -126)},
        {0x3dcccccdU, Rational(13421773) / Rational(134217728)},
        {0x3f800000U, Rational(1)},
        {0x7f7fffffU, Rational::dyadic(16777215U, 104)}}};
    for (const auto& [bits, price] : values) {
        const auto coefficient = float32_coefficient(bits, tick, scale);
        check(Float32Coefficient(tick, scale)(bits) == coefficient, "prepared converter matches compatibility call");
        check(scale.decode(coefficient) == price * tick, "float coefficient agrees with exact known value");
    }
    check(float32_coefficient(1, tick, scale) == Integer(1), "smallest subnormal is preserved");
    rejects([&] { (void)float32_coefficient(1, tick, Scale(153, 5)); }, "insufficient binary scale rejects");
    for (const auto bits : {0U, 0x80000000U, 0xbf800000U, 0x7f800000U, 0x7fc00000U})
        rejects([&] { (void)float32_coefficient(bits, tick, scale); }, "invalid price bit patterns reject");
    rejects([&] { (void)float32_coefficient(0x3f800000U, Rational(), scale); }, "zero amount tick rejects");
    check(Float32Coefficient(Rational(".5"), Scale())(0x40000000U) == Integer(1),
          "positive price exponent can cancel tick binary denominator");
    rejects([] { (void)Float32Coefficient(Rational(".2"), Scale()); }, "binary prices cannot cancel a missing factor of five");
    std::mt19937 random(49173);
    for (const auto* text : {"1", ".01", ".00000001", ".125", "2.5", "1e-30", "1e30"}) {
        const Rational amount(text);
        const auto tick_scale = Scale::for_value(amount);
        const Scale money(149 + tick_scale.twos(), tick_scale.fives());
        const Float32Coefficient prepared(amount, money);
        for (unsigned i = 0; i < 500; ++i) {
            const auto bits = static_cast<std::uint32_t>(1U + random() % 0x7f7fffffU);
            const auto exponent_bits = (bits >> 23U) & 255U;
            const auto significand = exponent_bits == 0 ? bits & 0x7fffffU : (bits & 0x7fffffU) | 0x800000U;
            const auto exponent = exponent_bits == 0 ? -149 : static_cast<int>(exponent_bits) - 150;
            check(money.decode(prepared(bits)) == Rational::dyadic(significand, exponent) * amount,
                  "prepared random float32 coefficient matches exact independent price");
        }
    }
}
Rational exact_price(std::uint32_t bits) {
    const auto exponent_bits = (bits >> 23U) & 255U;
    const auto significand = exponent_bits == 0 ? bits & 0x7fffffU : (bits & 0x7fffffU) | 0x800000U;
    return Rational::dyadic(significand, exponent_bits == 0 ? -149 : static_cast<int>(exponent_bits) - 150);
}
std::uint32_t reference_cutoff(const Rational& limit, bool buy) {
    std::uint32_t lower = 1, upper = 0x7f800000U;
    while (lower < upper) {
        const auto middle = lower + (upper - lower) / 2;
        const auto price = exact_price(middle);
        if (buy ? price < limit : price <= limit) lower = middle + 1; else upper = middle;
    }
    return buy ? lower - 1 : lower;
}
void cutoffs() {
    const auto verify = [](const Rational& limit) {
        for (const auto buy : {true, false})
            check(grid::sweep::units::strict_float32_cutoff(limit, buy) == reference_cutoff(limit, buy),
                  "candidate cutoff agrees with independent exact binary search");
    };
    for (const auto* text : {"0", "-1", "1e-90", "1e90", ".1", "99", "110", "106.92", "43.20"})
        verify(Rational(text));
    verify(Rational(1) / Rational(3));
    for (const auto bits : {1U, 2U, 0x007fffffU, 0x00800000U, 0x3f7fffffU,
                           0x3f800000U, 0x3f800001U, 0x4b800000U, 0x7f7ffffeU, 0x7f7fffffU}) {
        const auto value = exact_price(bits);
        verify(value);
        verify(value - exact_price(1) / Rational(2));
        verify(value + exact_price(1) / Rational(2));
        check(grid::sweep::units::strict_float32_cutoff(value, true) == bits - 1, "equal BUY never executes");
        check(grid::sweep::units::strict_float32_cutoff(value, false) == bits + 1, "equal SELL never executes");
        if (bits < 0x7f7fffffU) verify((value + exact_price(bits + 1)) / Rational(2));
    }
    std::mt19937 random(7821);
    for (unsigned i = 0; i < 500; ++i) {
        const auto bits = static_cast<std::uint32_t>(1U + random() % 0x7f7fffffU);
        const auto value = exact_price(bits);
        verify(value); verify(value * Rational(101) / Rational(100));
    }
}
void oracle_and_shared_reads() {
    std::mt19937_64 random(2749);
    for (unsigned index = 0; index < 2000; ++index) {
        const auto a = static_cast<std::int64_t>(random() % static_cast<std::uint64_t>(std::numeric_limits<std::int64_t>::max()));
        const auto b = -static_cast<std::int64_t>(random() % static_cast<std::uint64_t>(std::numeric_limits<std::int64_t>::max()));
        const Integer left(a), right(b);
        check((left + right).ratio() == (Rational(a) + Rational(b)).ratio(), "random addition exact reference");
        check((left - right).ratio() == (Rational(a) - Rational(b)).ratio(), "random subtraction exact reference");
        check((left * right).ratio() == (Rational(a) * Rational(b)).ratio(), "random multiplication exact reference");
        if (b != 0) check((left * right) / right == left, "random product exact divide");
        const auto big_left = left * left * left;
        const auto big_right = right * right * right;
        check((big_left + big_right) - big_right == big_left, "random large add subtract");
        check(big_left >= Integer() && big_right <= Integer(), "random large signs");
    }
    const Integer shared("170141183460469231731687303715884105728");
    const Rational shared_tick("0.00001");
    const Rational shared_limit("106.92");
    const auto expected_cutoff = reference_cutoff(Rational("106.92"), true);
    const Rational shared_anchor(108);
    const auto shared_multiplier = (Rational(199) / Rational(200)).power(184);
    const auto shared_round_tick = Rational(1) / Rational(100);
    const auto shared_native_multiplier = Rational(99) / Rational(100);
    const auto expected_rounded = (Rational(shared_anchor) * Rational(shared_multiplier))
        .rounded(Rational(shared_round_tick), false).ratio();
    const auto shared_fallback_factor = Rational(1) / Rational(2);
    const auto shared_fallback_large = Scale().decode(Scale(70000, 0).denominator());
    const auto shared_fallback_tick = Scale(61072, 0).decode(Integer(1));
    const auto expected_fallback = (Rational(shared_fallback_factor) * Rational(shared_fallback_large))
        .rounded(Rational(shared_fallback_tick), false).ratio();
    const auto expected_floor = Integer::floor_div_positive(shared, Integer(3));
    const Scale shared_scale(154, 5);
    const Float32Coefficient prepared(shared_tick, shared_scale);
    std::atomic<bool> okay{true};
    std::vector<std::thread> threads;
    for (unsigned worker = 0; worker < 8; ++worker) threads.emplace_back([&] {
        try {
            for (unsigned repeat = 0; repeat < 100; ++repeat) {
                check(shared / Integer(2) * Integer(2) == shared, "shared big integer remains unchanged");
                check(grid::sweep::units::float32_coefficient(1, shared_tick, shared_scale) == Integer(1),
                      "shared rational and scale remain unchanged");
                check(prepared(1) == Integer(1), "prepared converter shared immutable reads");
                check(shared_scale.render(shared) == shared_scale.decode(shared).ratio(), "shared direct rendering");
                check(shared_scale.encode(shared_tick) == shared_scale.encode(Rational(".00001")), "shared direct rational encoding");
                check(grid::sweep::units::strict_float32_cutoff(shared_limit, true) == expected_cutoff,
                      "cutoff reads shared nonmaterialized ratio without mutation");
                check(Integer::floor_div_positive(shared, Integer(3)) == expected_floor,
                      "floor division reads shared big operands without mutation");
                check(grid::sweep::units::rounded_product(shared_anchor, shared_native_multiplier,
                          shared_round_tick).ratio() == "2673/25",
                      "rounded product reads shared native operands without materializing them");
                check(grid::sweep::units::rounded_product(shared_anchor, shared_multiplier,
                          shared_round_tick).ratio() == expected_rounded,
                      "rounded product reads shared native and big operands without mutation");
                if (repeat < 4)
                    check(grid::sweep::units::rounded_product(shared_fallback_factor,
                              shared_fallback_large, shared_fallback_tick).ratio() == expected_fallback,
                          "fallback copies shared native operands before legacy materialization");
            }
        } catch (...) { okay.store(false); }
    });
    for (auto& thread : threads) thread.join();
    check(okay.load(), "concurrent immutable operands");
}
} // namespace

int main() {
    try {
        arithmetic(); scales(); native_fraction_reduction(); rounded_products(); floats(); cutoffs(); oracle_and_shared_reads();
        std::cout << "research sweep scaled integer tests passed\n";
        return 0;
    } catch (const std::exception& error) { std::cerr << error.what() << '\n'; return 1; }
}
