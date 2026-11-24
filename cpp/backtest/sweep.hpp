#pragma once

#include "numeric.hpp"
#include <cstdint>
#include <string>

namespace grid::sweep {
// Positive finite IEEE754 source prices have the same ordering as their bits.
// Cutoffs include explicit empty-range sentinels; no floating arithmetic is used.
[[nodiscard]] std::uint32_t buy_cutoff(const Rational& limit);
[[nodiscard]] std::uint32_t sell_cutoff(const Rational& limit);
int run(const std::string& plan_path);
} // namespace grid::sweep
