#pragma once
#include <iosfwd>
#include <string>
namespace grid {
void run_trace(const std::string& path, std::ostream& output);
void run_bars(const std::string& path, std::ostream& output);
}
