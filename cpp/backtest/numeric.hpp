#pragma once

#include <cstdint>
#include <memory>
#include <openssl/bn.h>
#include <stdexcept>
#include <string>
#include <string_view>

namespace grid {

class Error : public std::runtime_error {
public:
    using std::runtime_error::runtime_error;
};

// Slow-path exact ratios for order placement and original float32 uncertainty.
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
    using Integer = std::unique_ptr<BIGNUM, decltype(&BN_free)>;
    Integer numerator_;
    Integer denominator_;
    Rational(Integer numerator, Integer denominator);
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
