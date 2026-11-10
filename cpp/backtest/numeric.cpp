#include "numeric.hpp"

#include <algorithm>
#include <charconv>
#include <climits>
#include <limits>
#include <numeric>
#include <utility>

namespace grid {
namespace {
using Integer = std::unique_ptr<BIGNUM, decltype(&BN_free)>;
using Context = std::unique_ptr<BN_CTX, decltype(&BN_CTX_free)>;
constexpr int max_bits = 131072;

Integer integer() {
    Integer result(BN_new(), BN_free);
    if (!result) throw Error("integer allocation failed");
    return result;
}
Context context() {
    Context result(BN_CTX_new(), BN_CTX_free);
    if (!result) throw Error("integer context allocation failed");
    return result;
}
void require(int success) {
    if (success != 1) throw Error("exact integer operation failed");
}
void bound(const BIGNUM* value) {
    if (BN_num_bits(value) > max_bits) throw Error("exact rational bit bound exceeded");
}
Integer decimal_integer(std::string_view value) {
    auto result = integer();
    BIGNUM* raw = result.release();
    const std::string text(value);
    const int read = BN_dec2bn(&raw, text.c_str());
    result.reset(raw);
    if (read <= 0 || static_cast<std::size_t>(read) != text.size())
        throw Error("invalid integer");
    bound(result.get());
    return result;
}
std::string text(const BIGNUM* value) {
    char* raw = BN_bn2dec(value);
    if (raw == nullptr) throw Error("integer rendering failed");
    const std::string result(raw);
    OPENSSL_free(raw);
    return result;
}
Integer product(const BIGNUM* a, const BIGNUM* b) {
    if (BN_num_bits(a) + BN_num_bits(b) > max_bits + 1)
        throw Error("exact rational product bound exceeded");
    auto result = integer();
    auto ctx = context();
    require(BN_mul(result.get(), a, b, ctx.get()));
    bound(result.get());
    return result;
}
Integer ten_power(unsigned exponent) {
    if (exponent > 10000) throw Error("decimal exponent bound exceeded");
    auto result = integer();
    require(BN_one(result.get()));
    for (unsigned index = 0; index < exponent; ++index) require(BN_mul_word(result.get(), 10));
    return result;
}
std::int64_t narrow(__int128 value) {
    if (value < std::numeric_limits<std::int64_t>::min() ||
        value > std::numeric_limits<std::int64_t>::max())
        throw Error("int64 currency overflow");
    return static_cast<std::int64_t>(value);
}
__int128 power10(unsigned exponent) {
    if (exponent > 36) throw Error("currency scale bound exceeded");
    __int128 result = 1;
    for (unsigned index = 0; index < exponent; ++index) result *= 10;
    return result;
}
} // namespace

Rational::Rational(std::int64_t value)
    : numerator_(decimal_integer(std::to_string(value))), denominator_(integer()) {
    require(BN_one(denominator_.get()));
}

Rational::Rational(std::string_view value) : numerator_(integer()), denominator_(integer()) {
    if (value.empty() || value.size() > 256) throw Error("bounded decimal string required");
    const auto marker = value.find_first_of("eE");
    std::string_view mantissa = value.substr(0, marker);
    int exponent = 0;
    if (marker != std::string_view::npos) {
        auto exp = value.substr(marker + 1);
        if (!exp.empty() && exp.front() == '+') exp.remove_prefix(1);
        const auto parsed = std::from_chars(exp.data(), exp.data() + exp.size(), exponent);
        if (parsed.ec != std::errc{} || parsed.ptr != exp.data() + exp.size() ||
            exponent < -96 || exponent > 96) throw Error("invalid decimal exponent");
    }
    bool negative = false;
    if (!mantissa.empty() && (mantissa.front() == '-' || mantissa.front() == '+')) {
        negative = mantissa.front() == '-';
        mantissa.remove_prefix(1);
    }
    std::string digits;
    bool point = false;
    unsigned places = 0;
    for (char ch : mantissa) {
        if (ch == '.' && !point) { point = true; continue; }
        if (ch < '0' || ch > '9') throw Error("invalid decimal digit");
        digits.push_back(ch);
        if (point) ++places;
    }
    if (digits.empty() || digits.size() > 96) throw Error("decimal digit bound exceeded");
    numerator_ = decimal_integer(digits);
    BN_set_negative(numerator_.get(), negative ? 1 : 0);
    const int scale = static_cast<int>(places) - exponent;
    require(BN_one(denominator_.get()));
    if (scale >= 0) denominator_ = ten_power(static_cast<unsigned>(scale));
    else numerator_ = product(numerator_.get(), ten_power(static_cast<unsigned>(-scale)).get());
    normalize();
}
Rational::Rational(Integer numerator, Integer denominator)
    : numerator_(std::move(numerator)), denominator_(std::move(denominator)) { normalize(); }
Rational::Rational(const Rational& other) : numerator_(integer()), denominator_(integer()) {
    if (BN_copy(numerator_.get(), other.numerator_.get()) == nullptr ||
        BN_copy(denominator_.get(), other.denominator_.get()) == nullptr)
        throw Error("integer copy failed");
}
Rational& Rational::operator=(const Rational& other) {
    if (this != &other) { Rational replacement(other); *this = std::move(replacement); }
    return *this;
}
void Rational::normalize() {
    if (BN_is_zero(denominator_.get())) throw Error("division by zero");
    if (BN_is_negative(denominator_.get())) {
        BN_set_negative(denominator_.get(), 0);
        BN_set_negative(numerator_.get(), BN_is_negative(numerator_.get()) == 0 ? 1 : 0);
    }
    bound(numerator_.get()); bound(denominator_.get());
    if (BN_is_zero(numerator_.get())) { require(BN_one(denominator_.get())); return; }
    if (BN_is_one(denominator_.get())) return;
    constexpr int word_bits = static_cast<int>(sizeof(BN_ULONG) * CHAR_BIT);
    if (BN_num_bits(numerator_.get()) <= word_bits && BN_num_bits(denominator_.get()) <= word_bits) {
        const auto divisor = std::gcd(BN_get_word(numerator_.get()), BN_get_word(denominator_.get()));
        if (divisor != 1 && (BN_div_word(numerator_.get(), divisor) != 0 || BN_div_word(denominator_.get(), divisor) != 0))
            throw Error("exact native GCD division failed");
        return;
    }
    // These are public historical quantities, not secret cryptographic values.
    // Ordinary Euclid gives the same canonical fraction without BN_gcd's
    // constant-time bit-by-bit swapping overhead on every price/valuation.
    auto gcd = integer(); auto divisor = integer(); auto remainder = integer(); auto ctx = context();
    if (BN_copy(gcd.get(), numerator_.get()) == nullptr || BN_copy(divisor.get(), denominator_.get()) == nullptr)
        throw Error("integer copy failed");
    BN_set_negative(gcd.get(), 0);
    unsigned iterations = 0;
    while (!BN_is_zero(divisor.get())) {
        if (++iterations > static_cast<unsigned>(max_bits * 2)) throw Error("exact GCD iteration bound exceeded");
        require(BN_mod(remainder.get(), gcd.get(), divisor.get(), ctx.get()));
        gcd.swap(divisor); divisor.swap(remainder);
    }
    require(BN_div(numerator_.get(), nullptr, numerator_.get(), gcd.get(), ctx.get()));
    require(BN_div(denominator_.get(), nullptr, denominator_.get(), gcd.get(), ctx.get()));
}
int Rational::sign() const {
    return BN_is_zero(numerator_.get()) ? 0 : (BN_is_negative(numerator_.get()) ? -1 : 1);
}
std::string Rational::ratio() const { return text(numerator_.get()) + "/" + text(denominator_.get()); }
std::string Rational::str() const {
    auto d = integer();
    if (BN_copy(d.get(), denominator_.get()) == nullptr) throw Error("integer copy failed");
    unsigned twos = 0, fives = 0;
    while (!BN_is_odd(d.get())) { require(BN_rshift1(d.get(), d.get())); ++twos; }
    while (BN_mod_word(d.get(), 5) == 0) { (void)BN_div_word(d.get(), 5); ++fives; }
    if (!BN_is_one(d.get())) return ratio();
    const unsigned places = std::max(twos, fives);
    if (places > 10000) throw Error("decimal rendering bound exceeded");
    auto n = integer(); auto ctx = context();
    auto expanded = product(numerator_.get(), ten_power(places).get());
    require(BN_div(n.get(), nullptr, expanded.get(), denominator_.get(), ctx.get()));
    std::string result = text(n.get());
    const bool negative = !result.empty() && result.front() == '-';
    if (negative) result.erase(0, 1);
    if (places != 0) {
        if (result.size() <= places) result.insert(0, places + 1 - result.size(), '0');
        result.insert(result.size() - places, 1, '.');
        while (result.back() == '0') result.pop_back();
        if (result.back() == '.') result.pop_back();
    }
    return (negative ? "-" : "") + result;
}
Rational operator+(const Rational& a, const Rational& b) {
    auto left = product(a.numerator_.get(), b.denominator_.get());
    auto right = product(b.numerator_.get(), a.denominator_.get());
    auto n = integer(); require(BN_add(n.get(), left.get(), right.get()));
    return Rational(std::move(n), product(a.denominator_.get(), b.denominator_.get()));
}
Rational operator-(const Rational& a, const Rational& b) {
    Rational negative(b);
    BN_set_negative(negative.numerator_.get(), negative.sign() > 0 ? 1 : 0);
    return a + negative;
}
Rational operator*(const Rational& a, const Rational& b) {
    return Rational(product(a.numerator_.get(), b.numerator_.get()),
                    product(a.denominator_.get(), b.denominator_.get()));
}
Rational operator/(const Rational& a, const Rational& b) {
    if (b.sign() == 0) throw Error("division by zero");
    return Rational(product(a.numerator_.get(), b.denominator_.get()),
                    product(a.denominator_.get(), b.numerator_.get()));
}
bool operator==(const Rational& a, const Rational& b) {
    return BN_cmp(a.numerator_.get(), b.numerator_.get()) == 0 &&
           BN_cmp(a.denominator_.get(), b.denominator_.get()) == 0;
}
bool operator<(const Rational& a, const Rational& b) {
    const auto left = product(a.numerator_.get(), b.denominator_.get());
    const auto right = product(b.numerator_.get(), a.denominator_.get());
    return BN_cmp(left.get(), right.get()) < 0;
}
Rational Rational::power(unsigned exponent) const {
    if (exponent > 10000) throw Error("geometric exponent bound exceeded");
    Rational result(1), base(*this);
    while (exponent != 0) {
        if ((exponent & 1U) != 0) result = result * base;
        exponent >>= 1;
        if (exponent != 0) base = base * base;
    }
    return result;
}
Rational Rational::decimal96() const {
    if (sign() == 0) return *this;
    auto n = integer();
    if (BN_copy(n.get(), numerator_.get()) == nullptr) throw Error("integer copy failed");
    BN_set_negative(n.get(), 0);
    const auto numerator_digits = text(n.get()).size();
    const auto denominator_digits = text(denominator_.get()).size();
    int adjusted = static_cast<int>(numerator_digits) - static_cast<int>(denominator_digits);
    if (adjusted >= 0) {
        if (BN_cmp(n.get(), product(denominator_.get(), ten_power(static_cast<unsigned>(adjusted)).get()).get()) < 0)
            --adjusted;
    } else if (BN_cmp(product(n.get(), ten_power(static_cast<unsigned>(-adjusted)).get()).get(), denominator_.get()) < 0)
        --adjusted;
    // Bound the compatibility subset; do not emulate Decimal overflow/subnormal
    // contexts or silently return a different value outside this contract.
    if (adjusted < -999 || adjusted > 999) throw Error("Decimal96 planner exponent unsupported");
    const int scale = 95 - adjusted;
    auto denominator = integer();
    if (BN_copy(denominator.get(), denominator_.get()) == nullptr) throw Error("integer copy failed");
    if (scale >= 0) n = product(n.get(), ten_power(static_cast<unsigned>(scale)).get());
    else denominator = product(denominator.get(), ten_power(static_cast<unsigned>(-scale)).get());
    auto whole = integer(); auto remainder = integer(); auto ctx = context();
    require(BN_div(whole.get(), remainder.get(), n.get(), denominator.get(), ctx.get()));
    require(BN_lshift1(remainder.get(), remainder.get()));
    const int halfway = BN_cmp(remainder.get(), denominator.get());
    if (halfway > 0 || (halfway == 0 && BN_is_odd(whole.get()))) require(BN_add_word(whole.get(), 1));
    auto divisor = integer(); require(BN_one(divisor.get()));
    if (scale >= 0) divisor = ten_power(static_cast<unsigned>(scale));
    else whole = product(whole.get(), ten_power(static_cast<unsigned>(-scale)).get());
    BN_set_negative(whole.get(), sign() < 0 ? 1 : 0);
    return Rational(std::move(whole), std::move(divisor));
}
Rational Rational::rounded(const Rational& quantum, bool up) const {
    if (sign() < 0 || quantum.sign() <= 0) throw Error("rounding requires nonnegative value and positive quantum");
    const Rational ratio_value = *this / quantum;
    auto whole = integer(); auto remainder = integer(); auto ctx = context();
    require(BN_div(whole.get(), remainder.get(), ratio_value.numerator_.get(),
                   ratio_value.denominator_.get(), ctx.get()));
    if (up && !BN_is_zero(remainder.get())) require(BN_add_word(whole.get(), 1));
    auto one = integer(); require(BN_one(one.get()));
    return Rational(std::move(whole), std::move(one)) * quantum;
}
std::int64_t Rational::exact_units(const Rational& quantum) const {
    if (quantum.sign() <= 0) throw Error("positive currency quantum required");
    const Rational units = *this / quantum;
    if (!BN_is_one(units.denominator_.get())) throw Error("financial value is not representable in currency quanta");
    const std::string value = text(units.numerator_.get());
    std::int64_t result = 0;
    const auto parsed = std::from_chars(value.data(), value.data() + value.size(), result);
    if (parsed.ec != std::errc{} || parsed.ptr != value.data() + value.size())
        throw Error("int64 currency overflow");
    return result;
}
Rational Rational::dyadic(std::uint32_t significand, int exponent) {
    if (exponent < -150 || exponent > 128) throw Error("float32 dyadic exponent unsupported");
    auto n = decimal_integer(std::to_string(significand)); auto d = integer(); require(BN_one(d.get()));
    if (exponent >= 0) require(BN_lshift(n.get(), n.get(), exponent));
    else require(BN_lshift(d.get(), d.get(), -exponent));
    return Rational(std::move(n), std::move(d));
}
std::int64_t checked_add(std::int64_t a, std::int64_t b) { return narrow(static_cast<__int128>(a) + b); }
std::int64_t checked_sub(std::int64_t a, std::int64_t b) { return narrow(static_cast<__int128>(a) - b); }
std::int64_t exact_product(std::int64_t a, unsigned a_scale, std::int64_t b,
                           unsigned b_scale, unsigned result_scale) {
    if (a_scale > 18 || b_scale > 18 || result_scale > 18) throw Error("currency scale bound exceeded");
    __int128 value = static_cast<__int128>(a) * b;
    const unsigned combined = a_scale + b_scale;
    if (combined > result_scale) {
        const __int128 divisor = power10(combined - result_scale);
        if (value % divisor != 0) throw Error("exact product not representable in result currency");
        value /= divisor;
    } else if (combined < result_scale) {
        __int128 expanded = 0;
        if (__builtin_mul_overflow(value, power10(result_scale - combined), &expanded))
            throw Error("int128 currency product overflow");
        value = expanded;
    }
    return narrow(value);
}
Rational max(const Rational& a, const Rational& b) { return a < b ? b : a; }
Rational min(const Rational& a, const Rational& b) { return a < b ? a : b; }

} // namespace grid
