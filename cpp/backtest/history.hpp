#pragma once

#include "numeric.hpp"

#include <array>
#include <cstddef>
#include <cstdint>
#include <functional>
#include <map>
#include <memory>
#include <openssl/evp.h>
#include <optional>
#include <span>
#include <string>
#include <sys/stat.h>
#include <vector>

namespace grid {
constexpr std::int64_t minute_ms = 60000;
constexpr std::int64_t max_timestamp_ms = 253402300740000;
constexpr std::size_t record_bytes = 24;

struct Float32Price {
    std::uint32_t bits;
    [[nodiscard]] Rational exact() const;
    [[nodiscard]] Rational rounding_radius() const;
    [[nodiscard]] bool tick_compatible(const Rational& tick) const;
    [[nodiscard]] bool buy_crossed(const Rational& limit, bool strict) const;
    [[nodiscard]] bool sell_crossed(const Rational& limit, bool strict) const;
};
struct Bar {
    std::int64_t timestamp;
    std::array<Float32Price, 4> prices; // open, high, low, close
    std::array<std::byte, record_bytes> raw;
};
[[nodiscard]] Bar decode(std::span<const std::byte> raw);

struct Gap {
    std::int64_t previous_ms;
    std::int64_t next_ms;
    std::uint64_t missing_minutes;
};
struct Rejection {
    std::uint64_t record;
    std::string reason;
    std::string raw_hex;
};
struct ScanStats {
    std::uint64_t bytes = 0;
    std::uint64_t records = 0;
    std::uint64_t accepted = 0;
    std::uint64_t rejected = 0;
    std::uint64_t missing_minutes = 0;
    std::uint64_t gap_count = 0;
    std::optional<std::int64_t> first_ms;
    std::optional<std::int64_t> last_ms;
    std::map<std::string, std::uint64_t> reasons;
    std::vector<Rejection> examples; // at most 16; full gaps may stream to caller
    std::string sha256;
    bool complete = false;
};

// At most 98,304 source bytes buffered; no writable source descriptor/mapping.
// The immutable identity is checked on both the open fd and its original path.
class RawReader {
public:
    explicit RawReader(std::string path, std::string expected_sha256 = {},
                       std::function<void(const Gap&)> gap_sink = {});
    ~RawReader();
    RawReader(const RawReader&) = delete;
    RawReader& operator=(const RawReader&) = delete;
    [[nodiscard]] bool next(Bar& result);
    [[nodiscard]] const ScanStats& stats() const { return stats_; }
    [[nodiscard]] const std::string& path() const { return path_; }

private:
    std::string path_;
    std::string expected_sha256_;
    int fd_ = -1;
    struct stat initial_ {};
    std::unique_ptr<EVP_MD_CTX, decltype(&EVP_MD_CTX_free)> hash_;
    std::array<std::byte, record_bytes * 4096> buffer_ {};
    std::size_t cursor_ = 0;
    std::size_t available_ = 0;
    ScanStats stats_;
    std::function<void(const Gap&)> gap_sink_;
    [[nodiscard]] bool read_record(std::array<std::byte, record_bytes>& raw);
    void finish();
};

[[nodiscard]] std::string hex(std::span<const std::byte> raw);
[[nodiscard]] std::string json_quote(std::string_view value);
[[nodiscard]] std::string stats_json(const ScanStats& stats);

} // namespace grid
