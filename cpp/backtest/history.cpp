#include "history.hpp"

#include <algorithm>
#include <bit>
#include <cerrno>
#include <cstring>
#include <fcntl.h>
#include <limits>
#include <sstream>
#include <unistd.h>

namespace grid {
namespace {
std::uint64_t little(std::span<const std::byte> bytes) {
    std::uint64_t result = 0;
    for (std::size_t index = 0; index < bytes.size(); ++index)
        result |= static_cast<std::uint64_t>(std::to_integer<unsigned char>(bytes[index])) << (index * 8);
    return result;
}
bool same_identity(const struct stat& a, const struct stat& b) {
    return a.st_dev == b.st_dev && a.st_ino == b.st_ino && a.st_size == b.st_size &&
           a.st_mtim.tv_sec == b.st_mtim.tv_sec && a.st_mtim.tv_nsec == b.st_mtim.tv_nsec &&
           a.st_ctim.tv_sec == b.st_ctim.tv_sec && a.st_ctim.tv_nsec == b.st_ctim.tv_nsec;
}
void require_digest(int success) {
    if (success != 1) throw Error("source digest failed");
}
std::string optional_ms(const std::optional<std::int64_t>& timestamp) {
    return timestamp ? std::to_string(*timestamp) : "null";
}
} // namespace

Rational Float32Price::exact() const {
    const auto exponent = (bits >> 23U) & 255U;
    const auto fraction = bits & 0x7fffffU;
    if ((bits >> 31U) != 0 || exponent == 255 || (exponent == 0 && fraction == 0))
        throw Error("positive finite float32 price required");
    return exponent == 0 ? Rational::dyadic(fraction, -149)
                         : Rational::dyadic(fraction | 0x800000U, static_cast<int>(exponent) - 150);
}
Rational Float32Price::rounding_radius() const {
    (void)exact();
    const auto exponent = (bits >> 23U) & 255U;
    // max(distance to either finite neighbor) / 2, including powers of two,
    // subnormals and the largest finite value. This is a declared source
    // nearest-rounding assumption; it cannot restore original decimal prices.
    return Rational::dyadic(1, exponent == 0 ? -150 : static_cast<int>(exponent) - 151);
}
bool Float32Price::tick_compatible(const Rational& tick) const {
    const Rational center = exact();
    const Rational distance = min(center - center.rounded(tick, false),
                                  center.rounded(tick, true) - center);
    return distance <= rounding_radius();
}
bool Float32Price::buy_crossed(const Rational& limit, bool strict) const {
    return strict ? exact() + rounding_radius() < limit : exact() <= limit;
}
bool Float32Price::sell_crossed(const Rational& limit, bool strict) const {
    return strict ? exact() - rounding_radius() > limit : exact() >= limit;
}
Bar decode(std::span<const std::byte> raw) {
    if (raw.size() != record_bytes) throw Error("malformed_record_length");
    const auto unsigned_timestamp = little(raw.first(8));
    if (unsigned_timestamp > static_cast<std::uint64_t>(max_timestamp_ms))
        throw Error("invalid_utc_minute");
    const auto timestamp = static_cast<std::int64_t>(unsigned_timestamp);
    if (timestamp <= 0 || timestamp % minute_ms != 0) throw Error("invalid_utc_minute");
    Bar result {timestamp, {}, {}};
    for (std::size_t index = 0; index < result.prices.size(); ++index) {
        const auto bits = static_cast<std::uint32_t>(little(raw.subspan(8 + index * 4, 4)));
        if (((bits >> 23U) & 255U) == 255U) throw Error("nonfinite_price");
        if ((bits >> 31U) != 0 || bits == 0) throw Error("nonpositive_price");
        result.prices[index] = {bits};
    }
    // Positive IEEE754 encodings have exactly the same ordering as their
    // mathematical values; no floating calculation or loss of precision.
    const auto opened = result.prices[0].bits;
    const auto high = result.prices[1].bits;
    const auto low = result.prices[2].bits;
    const auto close = result.prices[3].bits;
    if (low > std::min(opened, close) || std::max(opened, close) > high)
        throw Error("impossible_ohlc");
    std::copy(raw.begin(), raw.end(), result.raw.begin());
    return result;
}

RawReader::RawReader(std::string path, std::string expected_sha256,
                     std::function<void(const Gap&)> gap_sink)
    : path_(std::move(path)), expected_sha256_(std::move(expected_sha256)),
      hash_(EVP_MD_CTX_new(), EVP_MD_CTX_free), gap_sink_(std::move(gap_sink)) {
    if (!expected_sha256_.empty() &&
        (expected_sha256_.size() != 64 || expected_sha256_.find_first_not_of("0123456789abcdef") != std::string::npos))
        throw Error("expected SHA256 must contain 64 lowercase hexadecimal digits");
    if (!hash_) throw Error("source hash allocation failed");
    require_digest(EVP_DigestInit_ex(hash_.get(), EVP_sha256(), nullptr));
    fd_ = ::open(path_.c_str(), O_RDONLY | O_CLOEXEC | O_NOFOLLOW | O_NONBLOCK);
    if (fd_ < 0) throw Error("cannot open read-only source: " + std::string(std::strerror(errno)));
    if (::fstat(fd_, &initial_) != 0 || !S_ISREG(initial_.st_mode) || initial_.st_size <= 0 ||
        initial_.st_size % static_cast<off_t>(record_bytes) != 0) {
        ::close(fd_); fd_ = -1;
        throw Error("source must be a nonempty regular file of complete 24-byte records");
    }
}
RawReader::~RawReader() { if (fd_ >= 0) ::close(fd_); }
bool RawReader::read_record(std::array<std::byte, record_bytes>& raw) {
    if (stats_.complete) return false;
    if (cursor_ == available_) {
        cursor_ = available_ = 0;
        // Loop handles interrupted/short reads without accidentally treating a
        // partially delivered record as an on-disk structural error.
        while (available_ < buffer_.size()) {
            const auto amount = ::read(fd_, buffer_.data() + available_, buffer_.size() - available_);
            if (amount < 0) { if (errno == EINTR) continue; throw Error("source read failed"); }
            if (amount == 0) break;
            available_ += static_cast<std::size_t>(amount);
        }
        if (available_ == 0) { finish(); return false; }
        if (available_ % record_bytes != 0) throw Error("source truncated during read");
        const auto initial_bytes = static_cast<std::uint64_t>(initial_.st_size);
        if (stats_.bytes > initial_bytes || available_ > initial_bytes - stats_.bytes)
            throw Error("source grew during read");
        require_digest(EVP_DigestUpdate(hash_.get(), buffer_.data(), available_));
        stats_.bytes += available_;
    }
    std::copy_n(buffer_.begin() + static_cast<std::ptrdiff_t>(cursor_), record_bytes, raw.begin());
    cursor_ += record_bytes;
    return true;
}
bool RawReader::next(Bar& result) {
    std::array<std::byte, record_bytes> raw {};
    while (read_record(raw)) {
        const std::uint64_t index = stats_.records++;
        std::optional<Bar> bar;
        try {
            bar = decode(raw);
            if (stats_.last_ms && bar->timestamp <= *stats_.last_ms)
                throw Error(bar->timestamp == *stats_.last_ms ? "duplicate_minute" : "out_of_order");
        } catch (const Error& error) {
            ++stats_.rejected;
            ++stats_.reasons[error.what()];
            if (stats_.examples.size() < 16) stats_.examples.push_back({index, error.what(), hex(raw)});
            continue;
        }
        if (stats_.last_ms && bar->timestamp - *stats_.last_ms > minute_ms) {
            const auto missing = static_cast<std::uint64_t>((bar->timestamp - *stats_.last_ms) / minute_ms - 1);
            stats_.missing_minutes += missing;
            ++stats_.gap_count;
            if (gap_sink_) gap_sink_({*stats_.last_ms, bar->timestamp, missing});
        }
        if (!stats_.first_ms) stats_.first_ms = bar->timestamp;
        stats_.last_ms = bar->timestamp;
        ++stats_.accepted;
        result = *bar;
        return true;
    }
    return false;
}
void RawReader::finish() {
    struct stat current {}, path_info {};
    if (::fstat(fd_, &current) != 0 || ::lstat(path_.c_str(), &path_info) != 0 ||
        !same_identity(initial_, current) || !same_identity(initial_, path_info) ||
        stats_.bytes != static_cast<std::uint64_t>(initial_.st_size))
        throw Error("source changed during read; discard results");
    std::array<std::byte, EVP_MAX_MD_SIZE> digest {};
    unsigned length = 0;
    require_digest(EVP_DigestFinal_ex(hash_.get(), reinterpret_cast<unsigned char*>(digest.data()), &length));
    if (length != 32) throw Error("unexpected source digest length");
    stats_.sha256 = hex(std::span(digest).first(length));
    if (!expected_sha256_.empty() && stats_.sha256 != expected_sha256_)
        throw Error("source SHA256 mismatch; discard results");
    stats_.complete = true;
}
std::string hex(std::span<const std::byte> raw) {
    constexpr std::string_view digits = "0123456789abcdef";
    std::string result;
    result.reserve(raw.size() * 2);
    for (std::byte byte : raw) {
        const auto value = std::to_integer<unsigned char>(byte);
        result.push_back(digits[value >> 4U]); result.push_back(digits[value & 15U]);
    }
    return result;
}
std::string json_quote(std::string_view value) {
    std::string result = "\"";
    constexpr std::string_view digits = "0123456789abcdef";
    for (char character : value) {
        const auto byte = static_cast<unsigned char>(character);
        if (character == '"' || character == '\\') { result.push_back('\\'); result.push_back(character); }
        else if (byte < 32) { result += "\\u00"; result.push_back(digits[byte >> 4U]); result.push_back(digits[byte & 15U]); }
        else result.push_back(character);
    }
    result.push_back('"');
    return result;
}
std::string stats_json(const ScanStats& stats) {
    std::ostringstream out;
    out << "{\"schema\":\"grid-source-scan-v1\",\"source_status\":\"UNVERIFIED SOURCE\",\"bytes\":" << stats.bytes
        << ",\"records\":" << stats.records << ",\"accepted\":" << stats.accepted << ",\"rejected\":" << stats.rejected
        << ",\"first_ms\":" << optional_ms(stats.first_ms) << ",\"last_ms\":" << optional_ms(stats.last_ms)
        << ",\"missing_minutes\":" << stats.missing_minutes << ",\"gap_count\":" << stats.gap_count
        << ",\"sha256\":" << json_quote(stats.sha256) << ",\"complete\":" << (stats.complete ? "true" : "false")
        << ",\"reasons\":{";
    bool comma = false;
    for (const auto& [reason, count] : stats.reasons) {
        if (comma) out << ',';
        out << json_quote(reason) << ':' << count;
        comma = true;
    }
    out << "},\"rejection_examples\":[";
    comma = false;
    for (const auto& example : stats.examples) {
        if (comma) out << ',';
        out << "{\"record\":" << example.record << ",\"reason\":" << json_quote(example.reason)
            << ",\"raw_hex\":" << json_quote(example.raw_hex) << '}';
        comma = true;
    }
    out << "]}";
    return out.str();
}
} // namespace grid
