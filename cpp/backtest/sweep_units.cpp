#include "sweep_units.hpp"

#include <algorithm>
#include <array>
#include <bit>
#include <charconv>
#include <cmath>
#include <limits>
#include <numeric>
#include <utility>

namespace grid::sweep::units {
namespace {
using Big = std::unique_ptr<BIGNUM, decltype(&BN_free)>;
using Context = std::unique_ptr<BN_CTX, decltype(&BN_CTX_free)>;
constexpr int maximum_bits = 131072;
constexpr std::size_t maximum_decimal_chars = 39458;
constexpr unsigned __int128 sign_bit = static_cast<unsigned __int128>(1) << 127U;
constexpr __int128 minimum_native = -static_cast<__int128>(sign_bit - 1) - 1;

void require(int success) {
    if (success != 1) throw Error("exact sweep integer operation failed");
}
Big make_big() {
    Big result(BN_new(), BN_free);
    if (!result) throw Error("exact sweep integer allocation failed");
    return result;
}
Context context() {
    Context result(BN_CTX_new(), BN_CTX_free);
    if (!result) throw Error("exact sweep integer context allocation failed");
    return result;
}
class Frame {
public:
    Frame() : ctx_(local()) { BN_CTX_start(ctx_); }
    ~Frame() { BN_CTX_end(ctx_); }
    Frame(const Frame&) = delete;
    [[nodiscard]] BN_CTX* get() const { return ctx_; }
    [[nodiscard]] BIGNUM* number() const {
        auto* result = BN_CTX_get(ctx_);
        if (!result) throw Error("exact sweep integer scratch allocation failed");
        return result;
    }
private:
    BN_CTX* ctx_;
    static BN_CTX* local() {
        thread_local Context instance = context();
        return instance.get();
    }
};
void bound(const BIGNUM* value) {
    if (BN_num_bits(value) > maximum_bits) throw Error("exact sweep integer bit bound exceeded");
}
unsigned __int128 magnitude(__int128 value) {
    const auto bits = static_cast<unsigned __int128>(value);
    return value < 0 ? static_cast<unsigned __int128>(0) - bits : bits;
}
__int128 signed_value(unsigned __int128 value, bool negative) {
    // value may be 2^127 only for a negative integer. Avoid negating INT128_MIN.
    return negative && value != 0 ? -static_cast<__int128>(value - 1) - 1 : static_cast<__int128>(value);
}
Integer power(Integer base, unsigned exponent) {
    Integer result(1);
    while (exponent != 0) {
        if ((exponent & 1U) != 0) result *= base;
        exponent >>= 1U;
        if (exponent != 0) base *= base;
    }
    return result;
}
} // namespace

struct NumericAccess {
    static bool native(const Rational& value, std::int64_t& numerator, std::int64_t& denominator) noexcept {
        if (!value.small_) return false;
        numerator = value.small_numerator_; denominator = value.small_denominator_; return true;
    }
    static void append(std::string& output, const Rational& value) {
        if (value.small_) {
            char digits[24];
            const auto n = std::to_chars(digits, digits + sizeof(digits), value.small_numerator_);
            output.append(digits, n.ptr); output.push_back('/');
            const auto d = std::to_chars(digits, digits + sizeof(digits), value.small_denominator_);
            output.append(digits, d.ptr);
            return;
        }
        const auto part = [&](const BIGNUM* number) {
            char* raw = BN_bn2dec(number);
            if (!raw) throw Error("exact sweep rational rendering failed");
            output.append(raw); OPENSSL_free(raw);
        };
        part(value.numerator_.get()); output.push_back('/'); part(value.denominator_.get());
    }
    static unsigned bits(const Integer& value) noexcept {
        if (value.big_) return static_cast<unsigned>(BN_num_bits(value.big_.get()));
        const auto absolute = magnitude(value.small_);
        const auto upper = static_cast<std::uint64_t>(absolute >> 64U);
        return upper != 0 ? 64U + static_cast<unsigned>(std::bit_width(upper)) :
            static_cast<unsigned>(std::bit_width(static_cast<std::uint64_t>(absolute)));
    }
    static std::pair<Integer, Integer> parts(const Rational& value) {
        if (value.small_) return {Integer(value.small_numerator_), Integer(value.small_denominator_)};
        Big numerator(BN_dup(value.numerator_.get()), BN_free), denominator(BN_dup(value.denominator_.get()), BN_free);
        if (!numerator || !denominator) throw Error("exact sweep rational conversion allocation failed");
        return {Integer(std::move(numerator)), Integer(std::move(denominator))};
    }
    static Rational rational(const Integer& numerator, const Integer& denominator) {
        // Rational::native uses signed-int128 intermediates. Its private path
        // deliberately remains restricted to its original int64 input range.
        if (numerator.is_small() && denominator.is_small() &&
            numerator.small_ >= std::numeric_limits<std::int64_t>::min() &&
            numerator.small_ <= std::numeric_limits<std::int64_t>::max() &&
            denominator.small_ <= std::numeric_limits<std::int64_t>::max())
            return Rational::native(numerator.small_, denominator.small_);
        return Rational(numerator.expanded(), denominator.expanded());
    }
    static long double approximate(const Rational& value) {
        if (value.small_) return static_cast<long double>(value.small_numerator_) /
                                 static_cast<long double>(value.small_denominator_);
        Frame frame;
        const auto top = [&](const BIGNUM* source, int& shift) {
            shift = std::max(0, BN_num_bits(source) - 63);
            auto* scratch = frame.number(); require(BN_rshift(scratch, source, shift));
            return static_cast<long double>(BN_get_word(scratch));
        };
        int nshift = 0, dshift = 0;
        const auto n = top(value.numerator_.get(), nshift), d = top(value.denominator_.get(), dshift);
        return std::ldexp(n / d, nshift - dshift);
    }
};

Integer::Integer(std::string_view text) {
    if (text.empty() || text.size() > maximum_decimal_chars) throw Error("bounded sweep integer required");
    bool negative = false;
    if (text.front() == '-' || text.front() == '+') {
        negative = text.front() == '-';
        text.remove_prefix(1);
    }
    if (text.empty()) throw Error("invalid sweep integer");
    unsigned __int128 value = 0;
    const auto limit = negative ? sign_bit : sign_bit - 1;
    bool overflow = false;
    for (const char digit : text) {
        if (digit < '0' || digit > '9') throw Error("invalid sweep integer");
        const auto number = static_cast<unsigned>(digit - '0');
        if (!overflow) {
            if (value > (limit - number) / 10U) overflow = true;
            else value = value * 10U + number;
        }
    }
    if (!overflow) { small_ = signed_value(value, negative); return; }
    big_ = make_big();
    BIGNUM* raw = big_.release();
    const std::string decimal(text);
    const int read = BN_dec2bn(&raw, decimal.c_str());
    big_.reset(raw);
    if (read <= 0 || static_cast<std::size_t>(read) != decimal.size()) throw Error("invalid sweep integer");
    BN_set_negative(big_.get(), negative ? 1 : 0);
    bound(big_.get());
}
Integer::Integer(Big value) : big_(std::move(value)) {
    bound(big_.get());
    capture_small();
}
Integer::Integer(const Integer& other) : small_(other.small_) {
    if (other.big_) {
        big_.reset(BN_dup(other.big_.get()));
        if (!big_) throw Error("exact sweep integer copy failed");
    }
}
Integer& Integer::operator=(const Integer& other) {
    if (this != &other) { Integer replacement(other); *this = std::move(replacement); }
    return *this;
}
Integer::Big Integer::expanded() const {
    if (big_) {
        Big result(BN_dup(big_.get()), BN_free);
        if (!result) throw Error("exact sweep integer copy failed");
        return result;
    }
    auto result = make_big();
    std::array<unsigned char, 16> bytes{};
    auto value = magnitude(small_);
    for (std::size_t index = bytes.size(); index != 0; --index) {
        bytes[index - 1] = static_cast<unsigned char>(value & 255U);
        value >>= 8U;
    }
    if (!BN_bin2bn(bytes.data(), static_cast<int>(bytes.size()), result.get()))
        throw Error("exact sweep integer expansion failed");
    BN_set_negative(result.get(), small_ < 0 ? 1 : 0);
    return result;
}
const BIGNUM* Integer::operand(BIGNUM* scratch) const {
    if (big_) return big_.get();
    std::array<unsigned char, 16> bytes{};
    auto value = magnitude(small_);
    for (std::size_t index = bytes.size(); index != 0; --index) {
        bytes[index - 1] = static_cast<unsigned char>(value & 255U);
        value >>= 8U;
    }
    if (!BN_bin2bn(bytes.data(), static_cast<int>(bytes.size()), scratch))
        throw Error("exact sweep integer scratch expansion failed");
    BN_set_negative(scratch, small_ < 0 ? 1 : 0);
    return scratch;
}
void Integer::capture_small() {
    const int bits = BN_num_bits(big_.get());
    const bool negative = BN_is_negative(big_.get()) != 0;
    if (bits > 128 || (bits == 128 && !negative)) return;
    std::array<unsigned char, 16> bytes{};
    if (BN_bn2binpad(big_.get(), bytes.data(), static_cast<int>(bytes.size())) != static_cast<int>(bytes.size()))
        throw Error("exact sweep integer narrowing failed");
    unsigned __int128 value = 0;
    for (const auto byte : bytes) value = (value << 8U) | byte;
    if (value > (negative ? sign_bit : sign_bit - 1)) return;
    small_ = signed_value(value, negative);
    big_.reset();
}
std::string Integer::str() const {
    std::string result;
    append_to(result);
    return result;
}
void Integer::append_to(std::string& result) const {
    if (big_) {
        char* raw = BN_bn2dec(big_.get());
        if (!raw) throw Error("exact sweep integer rendering failed");
        result.append(raw);
        OPENSSL_free(raw);
        return;
    }
    auto value = magnitude(small_);
    if (small_ < 0) result.push_back('-');
    char digits[40];
    if (value <= std::numeric_limits<std::uint64_t>::max()) {
        const auto rendered = std::to_chars(digits, digits + sizeof(digits), static_cast<std::uint64_t>(value));
        result.append(digits, rendered.ptr);
        return;
    }
    // At most three base-10^19 chunks replace up to 39 int128 divisions.
    constexpr std::uint64_t radix = 10000000000000000000ULL;
    std::array<std::uint64_t, 3> chunks{};
    std::size_t count = 0;
    while (value != 0) {
        chunks[count++] = static_cast<std::uint64_t>(value % radix);
        value /= radix;
    }
    const auto first = std::to_chars(digits, digits + sizeof(digits), chunks[--count]);
    result.append(digits, first.ptr);
    while (count != 0) {
        const auto rendered = std::to_chars(digits, digits + sizeof(digits), chunks[--count]);
        const auto size = static_cast<std::size_t>(rendered.ptr - digits);
        result.append(19U - size, '0'); result.append(digits, rendered.ptr);
    }
}
std::string Integer::fraction(const Integer& denominator) const {
    if (denominator.sign() <= 0) throw Error("positive exact fraction denominator required");
    if (sign() == 0) return "0/1";
    if (!big_ && !denominator.big_) {
        auto a = magnitude(small_), b = magnitude(denominator.small_);
        unsigned __int128 divisor = 0;
        if ((a | b) <= std::numeric_limits<std::uint64_t>::max())
            divisor = std::gcd(static_cast<std::uint64_t>(a), static_cast<std::uint64_t>(b));
        else {
            while (b != 0) { const auto remainder = a % b; a = b; b = remainder; }
            divisor = a;
        }
        Integer n, d;
        n.small_ = signed_value(magnitude(small_) / divisor, small_ < 0);
        d.small_ = static_cast<__int128>(magnitude(denominator.small_) / divisor);
        auto result = n.str(); result.push_back('/'); d.append_to(result); return result;
    }
    Frame frame;
    const auto* n = operand(frame.number()); const auto* d = denominator.operand(frame.number());
    auto* divisor = frame.number(); require(BN_gcd(divisor, n, d, frame.get()));
    auto numerator = make_big(), reduced_denominator = make_big();
    require(BN_div(numerator.get(), nullptr, n, divisor, frame.get()));
    require(BN_div(reduced_denominator.get(), nullptr, d, divisor, frame.get()));
    auto result = Integer(std::move(numerator)).str(); result.push_back('/');
    Integer(std::move(reduced_denominator)).append_to(result); return result;
}
std::size_t Integer::storage_bytes_estimate() const noexcept {
    if (!big_) return 0;
    const auto bits = static_cast<std::size_t>(BN_num_bits(big_.get()));
    const auto words = (bits + BN_BITS2 - 1U) / BN_BITS2;
    return words * sizeof(BN_ULONG) + 128U;
}
unsigned Integer::bit_width() const noexcept {
    if (big_) return static_cast<unsigned>(BN_num_bits(big_.get()));
    const auto absolute = magnitude(small_);
    const auto upper = static_cast<std::uint64_t>(absolute >> 64U);
    return upper != 0 ? 64U + static_cast<unsigned>(std::bit_width(upper)) :
        static_cast<unsigned>(std::bit_width(static_cast<std::uint64_t>(absolute)));
}
int Integer::large_sign() const noexcept {
    return BN_is_zero(big_.get()) != 0 ? 0 : (BN_is_negative(big_.get()) != 0 ? -1 : 1);
}
Integer Integer::from_rational_integer(const Rational& value) {
    auto [numerator, denominator] = NumericAccess::parts(value);
    if (denominator != Integer(1)) throw Error("sweep rational is not an integer");
    return numerator;
}
Integer Integer::ceil_div_positive(const Integer& numerator, const Integer& denominator) {
    if (numerator.sign() < 0 || denominator.sign() <= 0)
        throw Error("nonnegative numerator and positive denominator required for sweep ceiling");
    if (!numerator.big_ && !denominator.big_) {
        Integer result;
        result.small_ = numerator.small_ / denominator.small_ + (numerator.small_ % denominator.small_ != 0);
        return result;
    }
    Frame frame;
    const auto* left = numerator.operand(frame.number());
    const auto* right = denominator.operand(frame.number());
    auto out = make_big(); auto* remainder = frame.number();
    require(BN_div(out.get(), remainder, left, right, frame.get()));
    if (!BN_is_zero(remainder)) require(BN_add_word(out.get(), 1));
    return Integer(std::move(out));
}
Integer Integer::floor_div_positive(const Integer& numerator, const Integer& denominator) {
    if (numerator.sign() < 0 || denominator.sign() <= 0)
        throw Error("nonnegative numerator and positive denominator required for sweep floor");
    if (!numerator.big_ && !denominator.big_) {
        Integer result;
        result.small_ = numerator.small_ / denominator.small_;
        return result;
    }
    Frame frame;
    const auto* left = numerator.operand(frame.number());
    const auto* right = denominator.operand(frame.number());
    auto out = make_big();
    require(BN_div(out.get(), nullptr, left, right, frame.get()));
    return Integer(std::move(out));
}
Integer Integer::add_large(const Integer& a, const Integer& b) {
    Frame frame;
    const auto* left = a.operand(frame.number()); const auto* right = b.operand(frame.number());
    auto out = make_big(); require(BN_add(out.get(), left, right));
    return Integer(std::move(out));
}
Integer Integer::subtract_large(const Integer& a, const Integer& b) {
    Frame frame;
    const auto* left = a.operand(frame.number()); const auto* right = b.operand(frame.number());
    auto out = make_big(); require(BN_sub(out.get(), left, right));
    return Integer(std::move(out));
}
Integer Integer::multiply_large(const Integer& a, const Integer& b) {
    if (a.sign() == 0 || b.sign() == 0) return Integer();
    Frame frame;
    const auto* left = a.operand(frame.number()); const auto* right = b.operand(frame.number());
    if (BN_num_bits(left) + BN_num_bits(right) > maximum_bits + 1)
        throw Error("exact sweep integer product bound exceeded");
    auto out = make_big();
    require(BN_mul(out.get(), left, right, frame.get()));
    return Integer(std::move(out));
}
Integer operator/(const Integer& a, const Integer& b) {
    if (b.sign() == 0) throw Error("sweep integer division by zero");
    if (!a.big_ && !b.big_ && !(a.small_ == minimum_native && b.small_ == -1)) {
        if (a.small_ % b.small_ != 0) throw Error("sweep integer division is not exact");
        Integer result; result.small_ = a.small_ / b.small_; return result;
    }
    Frame frame;
    const auto* left = a.operand(frame.number()); const auto* right = b.operand(frame.number());
    auto out = make_big(); auto* remainder = frame.number();
    require(BN_div(out.get(), remainder, left, right, frame.get()));
    if (BN_is_zero(remainder) == 0) throw Error("sweep integer division is not exact");
    return Integer(std::move(out));
}
Integer Integer::operator-() const { return Integer() - *this; }
Integer& Integer::add_assign_large(const Integer& other) {
    // Keep the old value intact if crossing the configured hard bit bound.
    if (!big_ || BN_num_bits(big_.get()) >= maximum_bits ||
        (other.big_ && BN_num_bits(other.big_.get()) >= maximum_bits)) return *this = *this + other;
    Frame frame;
    require(BN_add(big_.get(), big_.get(), other.operand(frame.number())));
    capture_small(); return *this;
}
Integer& Integer::subtract_assign_large(const Integer& other) {
    if (!big_ || BN_num_bits(big_.get()) >= maximum_bits ||
        (other.big_ && BN_num_bits(other.big_.get()) >= maximum_bits)) return *this = *this - other;
    Frame frame;
    require(BN_sub(big_.get(), big_.get(), other.operand(frame.number())));
    capture_small(); return *this;
}
Integer Integer::shifted(int places) const {
    if (places <= -maximum_bits || places >= maximum_bits) throw Error("sweep integer shift bound exceeded");
    if (sign() == 0 || places == 0) return *this;
    if (!big_) {
        const auto absolute = magnitude(small_);
        if (places > 0 && places < 128) {
            const auto limit = small_ < 0 ? sign_bit : sign_bit - 1;
            if (absolute <= (limit >> static_cast<unsigned>(places))) {
                Integer result; result.small_ = signed_value(absolute << static_cast<unsigned>(places), small_ < 0);
                return result;
            }
        } else if (places < 0) {
            const auto count = static_cast<unsigned>(-places);
            if (count >= 128 || (absolute & ((static_cast<unsigned __int128>(1) << count) - 1)) != 0)
                throw Error("sweep integer binary division is not exact");
            Integer result; result.small_ = signed_value(absolute >> count, small_ < 0); return result;
        }
    }
    Frame frame; const auto* input = operand(frame.number()); auto out = make_big();
    if (places > 0) {
        if (BN_num_bits(input) > maximum_bits - places) throw Error("exact sweep integer bit bound exceeded");
        require(BN_lshift(out.get(), input, places));
    } else {
        const auto count = -places;
        for (int bit = 0; bit < count; ++bit)
            if (BN_is_bit_set(input, bit)) throw Error("sweep integer binary division is not exact");
        require(BN_rshift(out.get(), input, count));
    }
    return Integer(std::move(out));
}

Scale::Scale(unsigned twos, unsigned fives) : twos_(twos), fives_(fives) {
    if (twos >= static_cast<unsigned>(maximum_bits) || fives >= static_cast<unsigned>(maximum_bits))
        throw Error("exact sweep scale bound exceeded");
    denominator_ = power(Integer(2), twos) * power(Integer(5), fives);
}
Integer Scale::encode(const Rational& value) const {
    auto [numerator, denominator] = NumericAccess::parts(value);
    return numerator * (denominator_ / denominator);
}
Rational Scale::decode(const Integer& value) const {
    return NumericAccess::rational(value, denominator_);
}
std::string Scale::render(const Integer& value) const {
    std::string result;
    append(result, value);
    return result;
}
void Scale::append(std::string& output, const Integer& value) const {
    if (value.sign() == 0) { output += "0/1"; return; }
    if (value.is_small() && denominator_.is_small()) {
        auto numerator = magnitude(value.small_), denominator = magnitude(denominator_.small_);
        auto a = numerator, b = denominator;
        if ((a | b) <= std::numeric_limits<std::uint64_t>::max())
            a = std::gcd(static_cast<std::uint64_t>(a), static_cast<std::uint64_t>(b));
        else while (b != 0) { const auto remainder = a % b; a = b; b = remainder; }
        Integer reduced_n, reduced_d;
        reduced_n.small_ = signed_value(numerator / a, value.small_ < 0);
        reduced_d.small_ = static_cast<__int128>(denominator / a);
        reduced_n.append_to(output); output.push_back('/'); reduced_d.append_to(output); return;
    }
    auto numerator = value.expanded(), denominator = denominator_.expanded();
    unsigned twos = 0;
    while (twos < twos_ && BN_is_bit_set(numerator.get(), static_cast<int>(twos)) == 0) ++twos;
    if (twos != 0) {
        require(BN_rshift(numerator.get(), numerator.get(), static_cast<int>(twos)));
        require(BN_rshift(denominator.get(), denominator.get(), static_cast<int>(twos)));
    }
    unsigned fives = 0;
    while (fives < fives_ && BN_mod_word(numerator.get(), 5) == 0) {
        if (BN_div_word(numerator.get(), 5) != 0 || BN_div_word(denominator.get(), 5) != 0)
            throw Error("exact sweep rendering division failed");
        ++fives;
    }
    Integer(std::move(numerator)).append_to(output); output.push_back('/');
    Integer(std::move(denominator)).append_to(output);
}
Integer Scale::coefficient_to(const Scale& wider) const {
    if (wider.twos_ < twos_ || wider.fives_ < fives_) throw Error("sweep destination scale is not wider");
    return wider.denominator_ / denominator_;
}
Scale Scale::for_value(const Rational& value) {
    auto parts = NumericAccess::parts(value);
    auto denominator = parts.second.expanded();
    unsigned twos = 0, fives = 0;
    while (BN_is_bit_set(denominator.get(), static_cast<int>(twos)) == 0) ++twos;
    require(BN_rshift(denominator.get(), denominator.get(), static_cast<int>(twos)));
    while (BN_mod_word(denominator.get(), 5) == 0) {
        if (BN_div_word(denominator.get(), 5) != 0) throw Error("exact sweep scale factorization failed");
        ++fives;
    }
    if (BN_is_one(denominator.get()) == 0) throw Error("sweep value has non-decimal denominator factors");
    return Scale(twos, fives);
}
Scale common_scale(const Scale& a, const Scale& b) {
    return Scale(std::max(a.twos(), b.twos()), std::max(a.fives(), b.fives()));
}
void append_ratio(std::string& output, const Rational& value) { NumericAccess::append(output, value); }
std::string render_ratio(const Rational& value) {
    std::string result; append_ratio(result, value); return result;
}
bool native_ratio(const Rational& value, std::int64_t& numerator, std::int64_t& denominator) noexcept {
    return NumericAccess::native(value, numerator, denominator);
}
Rational rounded_product(const Rational& a, const Rational& b, const Rational& tick) {
    if (a.sign() < 0 || b.sign() < 0 || tick.sign() <= 0)
        throw Error("sweep rounded product requires nonnegative factors and positive tick");
    if (a.sign() == 0 || b.sign() == 0) return Rational();
    const auto legacy = [&] {
        // The legacy arithmetic may materialize const operands lazily. Copies
        // retain the read-only contract even when shared inputs need fallback.
        return (Rational(a) * Rational(b)).rounded(Rational(tick), false);
    };
    const auto [an, ad] = NumericAccess::parts(a);
    const auto [bn, bd] = NumericAccess::parts(b);
    const auto [tn, td] = NumericAccess::parts(tick);
    // Omitting intermediate reduction can enlarge the integers even when the
    // legacy sequence remains within its bound. Fall back before multiplying;
    // do not widen numeric limits or reject an otherwise valid legacy price.
    if (NumericAccess::bits(an) + NumericAccess::bits(bn) + NumericAccess::bits(td) > maximum_bits ||
        NumericAccess::bits(ad) + NumericAccess::bits(bd) + NumericAccess::bits(tn) > maximum_bits)
        return legacy();
    const auto whole = Integer::floor_div_positive(an * bn * td, ad * bd * tn);
    if (NumericAccess::bits(whole) + NumericAccess::bits(tn) > maximum_bits) return legacy();
    // Only the final order price needs a canonical fraction. Integer division
    // above is exact floor, including equality and prices next to a tick.
    return NumericAccess::rational(whole * tn, td);
}
PreparedProduct::PreparedProduct(const Rational& factor, const Rational& tick) : factor_(factor), tick_(tick) {
    if (factor.sign() < 0 || tick.sign() <= 0) throw Error("invalid prepared grid factor or tick");
    auto [fn, fd] = NumericAccess::parts(factor);
    auto [tn, td] = NumericAccess::parts(tick);
    numerator_ = fn * td; denominator_ = fd * tn;
    tick_numerator_ = std::move(tn); tick_denominator_ = std::move(td);
    numerator_bits_ = NumericAccess::bits(numerator_); denominator_bits_ = NumericAccess::bits(denominator_);
    tick_bits_ = NumericAccess::bits(tick_numerator_);
}
Rational PreparedProduct::operator()(const Rational& anchor) const {
    if (anchor.sign() < 0) throw Error("negative prepared grid anchor");
    if (anchor.sign() == 0 || numerator_.sign() == 0) return Rational();
    const auto [n, d] = NumericAccess::parts(anchor);
    if (NumericAccess::bits(n) + numerator_bits_ > maximum_bits ||
        NumericAccess::bits(d) + denominator_bits_ > maximum_bits)
        return rounded_product(anchor, factor_, tick_);
    const auto whole = Integer::floor_div_positive(n * numerator_, d * denominator_);
    if (NumericAccess::bits(whole) + tick_bits_ > maximum_bits) return rounded_product(anchor, factor_, tick_);
    return NumericAccess::rational(whole * tick_numerator_, tick_denominator_);
}
Integer float32_coefficient(std::uint32_t bits, const Rational& amount_tick, const Scale& scale) {
    return Float32Coefficient(amount_tick, scale)(bits);
}
Float32Coefficient::Float32Coefficient(const Rational& amount_tick, const Scale& scale) {
    if (amount_tick.sign() <= 0) throw Error("positive sweep amount tick required");
    const auto tick_scale = Scale::for_value(amount_tick);
    if (scale.fives() < tick_scale.fives()) throw Error("sweep float32 monetary scale is insufficient");
    auto parts = NumericAccess::parts(amount_tick);
    multiplier_ = parts.first * power(Integer(5), scale.fives() - tick_scale.fives());
    exponent_ = static_cast<int>(scale.twos()) - static_cast<int>(tick_scale.twos());
}
Integer Float32Coefficient::operator()(std::uint32_t bits) const {
    if (bits == 0 || bits > 0x7f7fffffU) throw Error("positive finite sweep float32 price required");
    const auto exponent_bits = (bits >> 23U) & 255U;
    const auto significand = exponent_bits == 0 ? bits & 0x7fffffU : (bits & 0x7fffffU) | 0x800000U;
    const int exponent = exponent_bits == 0 ? -149 : static_cast<int>(exponent_bits) - 150;
    return (multiplier_ * Integer(significand)).shifted(exponent + exponent_);
}
std::uint32_t strict_float32_cutoff(const Rational& limit, bool buy) {
    constexpr std::uint32_t last = 0x7f7fffffU;
    if (limit.sign() <= 0) return buy ? 0U : 1U;
    const auto [numerator, denominator] = NumericAccess::parts(limit);
    const auto compare = [&](std::uint32_t bits) {
        const auto exponent_bits = (bits >> 23U) & 255U;
        const auto significand = exponent_bits == 0 ? bits & 0x7fffffU : (bits & 0x7fffffU) | 0x800000U;
        const int exponent = exponent_bits == 0 ? -149 : static_cast<int>(exponent_bits) - 150;
        auto left = denominator * Integer(significand), right = numerator;
        if (exponent >= 0) left = left.shifted(exponent); else right = right.shifted(-exponent);
        return left < right ? -1 : (left == right ? 0 : 1);
    };
    // Floating point chooses only a candidate. Both strict inequalities defining
    // the returned discrete boundary are proved with exact integer arithmetic.
    const auto approximate = NumericAccess::approximate(limit);
    std::uint32_t candidate = 1;
    if (approximate >= static_cast<long double>(std::numeric_limits<float>::max())) candidate = last;
    else if (approximate > 0) candidate = std::max(1U, std::bit_cast<std::uint32_t>(static_cast<float>(approximate)));
    candidate = std::min(candidate, last);
    const auto comparison = compare(candidate);
    if (buy) {
        const auto boundary = comparison < 0 ? candidate : candidate - 1;
        if ((boundary == 0 || compare(boundary) < 0) && (boundary == last || compare(boundary + 1) >= 0))
            return boundary;
    } else {
        const auto boundary = comparison > 0 ? candidate : candidate + 1;
        if ((boundary == last + 1 || compare(boundary) > 0) && (boundary == 1 || compare(boundary - 1) <= 0))
            return boundary;
    }
    // Independent legacy search remains a fail-safe if a platform conversion
    // produces a candidate outside the two neighboring representable values.
    const Rational own(limit);
    std::uint32_t lower = 1, upper = last + 1;
    while (lower < upper) {
        const auto middle = lower + (upper - lower) / 2;
        const auto exponent_bits = (middle >> 23U) & 255U;
        const auto significand = exponent_bits == 0 ? middle & 0x7fffffU : (middle & 0x7fffffU) | 0x800000U;
        const int exponent = exponent_bits == 0 ? -149 : static_cast<int>(exponent_bits) - 150;
        const auto price = Rational::dyadic(significand, exponent);
        if (buy ? price < own : price <= own) lower = middle + 1; else upper = middle;
    }
    return buy ? lower - 1 : lower;
}

} // namespace grid::sweep::units
