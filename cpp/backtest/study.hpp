#pragma once
#include "portfolio.hpp"
#include "simulator.hpp"
#include <iosfwd>

namespace grid {
struct StudySource { Scope scope; std::string path, sha256; };
struct StudyConfig {
    EngineContext context;
    Rational initial_quote;
    BarModel model;
    PortfolioPolicy portfolio;
    std::optional<std::int64_t> start_ms, end_ms;
    std::vector<StudySource> sources;
    std::string input_sha256;
};
[[nodiscard]] StudyConfig read_study(const std::string& path);
void run_study(const StudyConfig&, std::ostream& output);
} // namespace grid
