#pragma once

#include <cstdint>
#include <memory>
#include <openssl/bn.h>
#include <stdexcept>
#include <string>
#include <string_view>

namespace grid {

namespace sweep::units { struct NumericAccess; }

class Error : public std::runtime_error {
public:
    using std::runtime_error::runtime_error;
};

// Exact ratios for order placement and original float32 uncertainty. Ordinary
// reduced fractions stay in native integers; large planner values fall back to
// bounded BIGNUM arithmetic. Neither path uses binary floating point.
// Ledger storage is explicitly quantized int64; never use a ratio as an implicit
// permission to post a fractional currency quantum.
class Rational {
public:
    explicit Rational(std::int64_t value = 0);
    explicit Rational(std::string_view decimal);
    Rational(const Rational& other);
    Rational(Rational&&) noexcept = default;
    Rational& operator=(const Rational& other);
    Rational& operator=(Rational&&) noexcept = default;

    [[nodiscard]] std::string str() const;
    [[nodiscard]] std::string ratio() const;
    [[nodiscard]] int sign() const;
    [[nodiscard]] Rational power(unsigned exponent) const;
    // Compatibility only for Python's explicitly rounded planner operations.
    // Ledger postings and fee arithmetic remain exact.
    [[nodiscard]] Rational decimal96() const;
    [[nodiscard]] Rational rounded(const Rational& quantum, bool up) const;
    [[nodiscard]] std::int64_t exact_units(const Rational& quantum) const;
    [[nodiscard]] static Rational dyadic(std::uint32_t significand, int exponent);

    friend Rational operator+(const Rational& a, const Rational& b);
    friend Rational operator-(const Rational& a, const Rational& b);
    friend Rational operator*(const Rational& a, const Rational& b);
    friend Rational operator/(const Rational& a, const Rational& b);
    friend bool operator==(const Rational& a, const Rational& b);
    friend bool operator<(const Rational& a, const Rational& b);
    friend bool operator>(const Rational& a, const Rational& b) { return b < a; }
    friend bool operator<=(const Rational& a, const Rational& b) { return !(b < a); }
    friend bool operator>=(const Rational& a, const Rational& b) { return !(a < b); }

private:
    // Read-only/exact conversion bridge implemented by the research sweep.
    // It does not change any live/legacy arithmetic or materialize shared values.
    friend struct sweep::units::NumericAccess;
    using Integer = std::unique_ptr<BIGNUM, decltype(&BN_free)>;
    mutable Integer numerator_{nullptr, BN_free};
    mutable Integer denominator_{nullptr, BN_free};
    bool small_ = true;
    std::int64_t small_numerator_ = 0, small_denominator_ = 1;
    Rational(Integer numerator, Integer denominator);
    [[nodiscard]] static Rational native(__int128 numerator, __int128 denominator);
    void materialize() const;
    void capture_native();
    void normalize();
};

[[nodiscard]] std::int64_t checked_add(std::int64_t a, std::int64_t b);
[[nodiscard]] std::int64_t checked_sub(std::int64_t a, std::int64_t b);
// scales are decimal-place counts, not arbitrary common implicit units.
[[nodiscard]] std::int64_t exact_product(std::int64_t a, unsigned a_scale,
                                        std::int64_t b, unsigned b_scale,
                                        unsigned result_scale);
[[nodiscard]] Rational max(const Rational& a, const Rational& b);
[[nodiscard]] Rational min(const Rational& a, const Rational& b);

} // namespace grid
