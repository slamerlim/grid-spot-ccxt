#pragma once

#include "numeric.hpp"
#include <cstddef>
#include <cstdint>
#include <memory>
#include <string>
#include <string_view>

namespace grid::sweep::units {

// Signed exact integers for the research sweep only. Ordinary values need no
// allocations; overflow promotes to bounded BIGNUM storage. Const operations
// never materialize or mutate either operand, including when shared by workers.
class Integer {
public:
    explicit Integer(std::int64_t value = 0) noexcept : small_(value) {}
    explicit Integer(std::string_view value);
    Integer(const Integer& other);
    Integer(Integer&&) noexcept = default;
    Integer& operator=(const Integer& other);
    Integer& operator=(Integer&&) noexcept = default;

    [[nodiscard]] std::string str() const;
    void append_to(std::string& output) const;
    // Canonical reduced ratio, with a positive denominator, without Rational
    // materialization. Inputs stay read-only even on the large-integer path.
    [[nodiscard]] std::string fraction(const Integer& denominator) const;
    [[nodiscard]] std::string ratio() const { return str() + "/1"; }
    [[nodiscard]] int sign() const noexcept {
        return !big_ ? (small_ == 0 ? 0 : (small_ < 0 ? -1 : 1)) : large_sign();
    }
    [[nodiscard]] bool is_small() const noexcept { return !big_; }
    [[nodiscard]] unsigned bit_width() const noexcept;
    [[nodiscard]] bool native_value(__int128& value) const noexcept {
        if (big_) return false;
        value = small_; return true;
    }
    // Extra heap storage estimate, excluding this object. BIGNUM capacity and
    // allocator metadata are opaque; this is telemetry, not an RSS measurement.
    [[nodiscard]] std::size_t storage_bytes_estimate() const noexcept;
    [[nodiscard]] static Integer from_rational_integer(const Rational& value);
    [[nodiscard]] static Integer floor_div_positive(const Integer& numerator, const Integer& denominator);
    [[nodiscard]] static Integer ceil_div_positive(const Integer& numerator, const Integer& denominator);

    friend Integer operator+(const Integer& a, const Integer& b) {
        Integer result;
        if (!a.big_ && !b.big_ && !__builtin_add_overflow(a.small_, b.small_, &result.small_)) return result;
        return add_large(a, b);
    }
    friend Integer operator-(const Integer& a, const Integer& b) {
        Integer result;
        if (!a.big_ && !b.big_ && !__builtin_sub_overflow(a.small_, b.small_, &result.small_)) return result;
        return subtract_large(a, b);
    }
    friend Integer operator*(const Integer& a, const Integer& b) {
        Integer result;
        if (!a.big_ && !b.big_ && !__builtin_mul_overflow(a.small_, b.small_, &result.small_)) return result;
        return multiply_large(a, b);
    }
    // Division rejects a nonzero remainder; no truncation or rounding.
    friend Integer operator/(const Integer& a, const Integer& b);
    [[nodiscard]] Integer operator-() const;
    Integer& operator+=(const Integer& other) {
        __int128 result = 0;
        if (!big_ && !other.big_ && !__builtin_add_overflow(small_, other.small_, &result)) {
            small_ = result; return *this;
        }
        return add_assign_large(other);
    }
    Integer& operator-=(const Integer& other) {
        __int128 result = 0;
        if (!big_ && !other.big_ && !__builtin_sub_overflow(small_, other.small_, &result)) {
            small_ = result; return *this;
        }
        return subtract_assign_large(other);
    }
    Integer& operator*=(const Integer& other) { return *this = *this * other; }
    Integer& operator/=(const Integer& other) { return *this = *this / other; }
    friend bool operator==(const Integer& a, const Integer& b) {
        if (!a.big_ && !b.big_) return a.small_ == b.small_;
        if (a.big_ && b.big_) return BN_cmp(a.big_.get(), b.big_.get()) == 0;
        return false; // Every BIGNUM result is narrowed when representable.
    }
    friend bool operator<(const Integer& a, const Integer& b) {
        if (!a.big_ && !b.big_) return a.small_ < b.small_;
        if (a.big_ && b.big_) return BN_cmp(a.big_.get(), b.big_.get()) < 0;
        return a.big_ ? a.sign() < 0 : b.sign() > 0;
    }
    friend bool operator!=(const Integer& a, const Integer& b) { return !(a == b); }
    friend bool operator>(const Integer& a, const Integer& b) { return b < a; }
    friend bool operator<=(const Integer& a, const Integer& b) { return !(b < a); }
    friend bool operator>=(const Integer& a, const Integer& b) { return !(a < b); }
    // Exact multiplication/division by a power of two; right shifts reject loss.
    [[nodiscard]] Integer shifted(int places) const;

private:
    using Big = std::unique_ptr<BIGNUM, decltype(&BN_free)>;
    __int128 small_ = 0;
    Big big_{nullptr, BN_free};
    explicit Integer(Big value);
    [[nodiscard]] Big expanded() const;
    [[nodiscard]] const BIGNUM* operand(BIGNUM* scratch) const;
    void capture_small();
    [[nodiscard]] int large_sign() const noexcept;
    [[nodiscard]] static Integer add_large(const Integer& a, const Integer& b);
    [[nodiscard]] static Integer subtract_large(const Integer& a, const Integer& b);
    [[nodiscard]] static Integer multiply_large(const Integer& a, const Integer& b);
    Integer& add_assign_large(const Integer& other);
    Integer& subtract_assign_large(const Integer& other);
    friend class Scale;
    friend struct NumericAccess;
};

// A monetary value is its integer coefficient divided by 2^twos * 5^fives.
// Scale construction and Rational conversions belong at preparation, trades,
// checkpoints and reports, rather than the price-only accounting hot path.
class Scale {
public:
    explicit Scale(unsigned twos = 0, unsigned fives = 0);
    [[nodiscard]] unsigned twos() const noexcept { return twos_; }
    [[nodiscard]] unsigned fives() const noexcept { return fives_; }
    [[nodiscard]] const Integer& denominator() const noexcept { return denominator_; }
    [[nodiscard]] Integer encode(const Rational& value) const;
    [[nodiscard]] Rational decode(const Integer& value) const;
    [[nodiscard]] std::string render(const Integer& value) const;
    void append(std::string& output, const Integer& value) const;
    // Multiply a coefficient in this scale to represent it in a wider scale.
    [[nodiscard]] Integer coefficient_to(const Scale& wider) const;
    [[nodiscard]] static Scale for_value(const Rational& value);

private:
    unsigned twos_ = 0, fives_ = 0;
    Integer denominator_{1};
};

[[nodiscard]] Scale common_scale(const Scale& a, const Scale& b);
// Read canonical Rational components directly; do not materialize small values
// as BIGNUMs just to print them. Suitable for shared read-only constants.
void append_ratio(std::string& output, const Rational& value);
[[nodiscard]] std::string render_ratio(const Rational& value);
[[nodiscard]] bool native_ratio(const Rational& value, std::int64_t& numerator, std::int64_t& denominator) noexcept;
// Exact fixed-tick floor of a*b without normalizing intermediate fractions.
// Inputs are read-only; exceptional large intermediates use the legacy path.
[[nodiscard]] Rational rounded_product(const Rational& a, const Rational& b, const Rational& tick);
// Immutable exact factors of b/tick, reused by all configurations sharing a
// grid step and price tick. Only the changing anchor is decoded per new BUY.
class PreparedProduct {
public:
    PreparedProduct(const Rational& factor, const Rational& tick);
    [[nodiscard]] Rational operator()(const Rational& anchor) const;
private:
    Rational factor_, tick_;
    Integer numerator_, denominator_, tick_numerator_, tick_denominator_;
    unsigned numerator_bits_ = 0, denominator_bits_ = 0, tick_bits_ = 0;
};
// Accepts only finite positive IEEE754 binary32 prices, without float math.
[[nodiscard]] Integer float32_coefficient(std::uint32_t bits, const Rational& amount_tick,
                                        const Scale& scale);

// Immutable per-pair conversion factors. Preparation may use Rational; decoding
// a price uses only its exact IEEE754 significand/exponent and checked integers.
class Float32Coefficient {
public:
    Float32Coefficient(const Rational& amount_tick, const Scale& scale);
    [[nodiscard]] Integer operator()(std::uint32_t bits) const;
private:
    Integer multiplier_;
    int exponent_ = 0;
};

// BUY: last finite positive float32 strictly below the limit, or zero.
// SELL: first finite positive float32 strictly above it, or 0x7f800000.
// An approximate candidate is accepted only after exact neighbor comparisons.
[[nodiscard]] std::uint32_t strict_float32_cutoff(const Rational& limit, bool buy);

} // namespace grid::sweep::units
