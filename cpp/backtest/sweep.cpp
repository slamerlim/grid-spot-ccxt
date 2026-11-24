#include "sweep.hpp"
#include "sweep_units.hpp"
#include "history.hpp"
#include <json.hpp>

#include <algorithm>
#include <array>
#include <atomic>
#include <bit>
#include <chrono>
#include <charconv>
#include <condition_variable>
#include <csignal>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <functional>
#include <iomanip>
#include <iostream>
#include <limits>
#include <map>
#include <mutex>
#include <openssl/sha.h>
#include <queue>
#include <set>
#include <sstream>
#include <string_view>
#include <thread>
#include <tuple>
#include <fcntl.h>
#include <sys/file.h>
#include <sys/resource.h>
#include <sys/stat.h>
#include <sys/statvfs.h>
#include <unistd.h>

namespace grid::sweep {
namespace {
using J = nlohmann::json;
using Clock = std::chrono::steady_clock;
using units::Integer;
using units::Scale;
namespace fs = std::filesystem;
constexpr std::uint32_t last_float = 0x7f7fffffU;
constexpr std::size_t default_source_buffer_records = 4096;
constexpr std::size_t trace_limit = 100000;
constexpr std::size_t maximum_plan_bytes = 64U * 1024U * 1024U;
volatile std::sig_atomic_t interrupted = 0;
void signal_stop(int) { interrupted = 1; }

void require(bool condition, std::string_view message) { if (!condition) throw Error(std::string(message)); }
std::string hex(const unsigned char* bytes, std::size_t count) {
    static constexpr char digits[] = "0123456789abcdef";
    std::string out(count * 2, '0');
    for (std::size_t i = 0; i < count; ++i) {
        out[i * 2] = digits[bytes[i] >> 4U]; out[i * 2 + 1] = digits[bytes[i] & 15U];
    }
    return out;
}
std::string sha(const std::string& text) {
    std::array<unsigned char, SHA256_DIGEST_LENGTH> result {};
    require(SHA256(reinterpret_cast<const unsigned char*>(text.data()), text.size(), result.data()) != nullptr,
            "SHA256 failed");
    return hex(result.data(), result.size());
}
using Digest = std::array<unsigned char, SHA256_DIGEST_LENGTH>;
void chain(Digest& digest, std::string& payload) {
    // The caller reserves the first digest.size() bytes in its reusable trade
    // buffer. Replacing only that prefix preserves SHA256(digest || JSON) while
    // feeding the contiguous bytes through one provider update per chain.
    std::memcpy(payload.data(), digest.data(), digest.size());
    // Bind SHA256 once: repeating EVP_sha256() here makes OpenSSL fetch the
    // provider under shared locks on every trade. A null type reuses the bound
    // implementation after Final_ex without resetting/freeing its context.
    thread_local const auto context = [] {
        auto initialized = std::unique_ptr<EVP_MD_CTX, decltype(&EVP_MD_CTX_free)>(EVP_MD_CTX_new(), EVP_MD_CTX_free);
        require(initialized && EVP_DigestInit_ex(initialized.get(), EVP_sha256(), nullptr) == 1,
                "trade hash initialization failed");
        return initialized;
    }();
    unsigned length = 0;
    require(EVP_DigestInit_ex(context.get(), nullptr, nullptr) == 1 &&
            EVP_DigestUpdate(context.get(), payload.data(), payload.size()) == 1 &&
            EVP_DigestFinal_ex(context.get(), digest.data(), &length) == 1 && length == digest.size(), "trade hash failed");
}
Digest parse_digest(const std::string& value) {
    require(value.size() == 64, "invalid SHA256");
    Digest result {};
    const auto digit = [](char c) -> unsigned {
        if (c >= '0' && c <= '9') return static_cast<unsigned>(c - '0');
        if (c >= 'a' && c <= 'f') return static_cast<unsigned>(c - 'a' + 10);
        throw Error("SHA256 must be lowercase hexadecimal");
    };
    for (std::size_t i = 0; i < result.size(); ++i)
        result[i] = static_cast<unsigned char>((digit(value[i * 2]) << 4U) | digit(value[i * 2 + 1]));
    return result;
}
struct File {
    int fd = -1;
    explicit File(int value = -1) : fd(value) {}
    File(const File&) = delete;
    File& operator=(const File&) = delete;
    ~File() { if (fd >= 0) ::close(fd); }
};
void write_all(int fd, std::string_view value) {
    while (!value.empty()) {
        const auto count = ::write(fd, value.data(), value.size());
        if (count < 0 && errno == EINTR) continue;
        if (count <= 0) throw Error("research output write failed: " + std::string(std::strerror(errno)));
        value.remove_prefix(static_cast<std::size_t>(count));
    }
}
void sync_directory(const fs::path& path) {
    File file(::open(path.c_str(), O_RDONLY | O_DIRECTORY | O_CLOEXEC));
    require(file.fd >= 0 && ::fsync(file.fd) == 0, "directory synchronization failed");
}
void atomic_json(const fs::path& path, const J& value, bool durable = true) {
    const fs::path temp = path.string() + ".tmp";
    File file(::open(temp.c_str(), O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC | O_NOFOLLOW, 0600));
    require(file.fd >= 0, "cannot create atomic research output");
    write_all(file.fd, value.dump() + "\n");
    if (durable) require(::fsync(file.fd) == 0, "research output fsync failed");
    require(::rename(temp.c_str(), path.c_str()) == 0, "research output rename failed");
    if (durable) sync_directory(path.parent_path());
}
J read_json(const fs::path& path, std::uintmax_t cap = maximum_plan_bytes) {
    require(fs::is_regular_file(path) && fs::file_size(path) <= cap, "missing or oversized JSON input");
    std::ifstream input(path); require(bool(input), "cannot read JSON input");
    return J::parse(input);
}
Rational read_number(const J& value) {
    require(value.is_string(), "exact research values must be decimal strings");
    return Rational(value.get<std::string>());
}
// Checkpoint ratios may be wider than the public 96-digit decimal-input bound.
// Decode bounded integer chunks through the same exact arithmetic, never a float.
Rational checkpoint_number(const J& value) {
    const auto text = value.get<std::string>();
    require(text.size() <= 16384, "checkpoint number too large");
    const auto slash = text.find('/');
    if (slash == std::string::npos) return Rational(text);
    std::int64_t native_numerator = 0, native_denominator = 0;
    const auto n = std::from_chars(text.data(), text.data() + slash, native_numerator);
    const auto d = std::from_chars(text.data() + slash + 1, text.data() + text.size(), native_denominator);
    if (n.ec == std::errc{} && n.ptr == text.data() + slash && d.ec == std::errc{} &&
            d.ptr == text.data() + text.size() && native_denominator > 0)
        return Rational(native_numerator) / Rational(native_denominator);
    const auto integer = [](std::string_view part) {
        bool negative = false;
        if (!part.empty() && part.front() == '-') { negative = true; part.remove_prefix(1); }
        require(!part.empty(), "invalid checkpoint integer");
        Rational number;
        while (!part.empty()) {
            const auto count = std::min<std::size_t>(18, part.size());
            std::int64_t chunk = 0;
            const auto parsed = std::from_chars(part.data(), part.data() + count, chunk);
            require(parsed.ec == std::errc{} && parsed.ptr == part.data() + count && chunk >= 0 &&
                    part.front() >= '0' && part.front() <= '9', "invalid checkpoint integer");
            number = number * Rational(10).power(static_cast<unsigned>(count)) + Rational(chunk);
            part.remove_prefix(count);
        }
        return negative ? Rational() - number : number;
    };
    const auto denominator = integer(std::string_view(text).substr(slash + 1));
    require(denominator.sign() > 0, "invalid checkpoint denominator");
    return integer(std::string_view(text).substr(0, slash)) / denominator;
}
std::string number(const Rational& value) { return units::render_ratio(value); }
bool same_identity(const struct stat& a, const struct stat& b) {
    return a.st_dev == b.st_dev && a.st_ino == b.st_ino && a.st_size == b.st_size &&
        a.st_mtim.tv_sec == b.st_mtim.tv_sec && a.st_mtim.tv_nsec == b.st_mtim.tv_nsec &&
        a.st_ctim.tv_sec == b.st_ctim.tv_sec && a.st_ctim.tv_nsec == b.st_ctim.tv_nsec;
}

struct Rules {
    bool significant = false;
    unsigned digits = 0, max_decimals = 0;
    Rational price_tick, amount_tick, min_amount, min_cost;
    Rational significant_divisor{1};
    const std::vector<Rational>* decimal_powers = nullptr;
    Integer minimum_lots{1};
    explicit Rules(const J& j)
        : amount_tick(read_number(j.at("amount_tick"))), min_amount(read_number(j.at("min_amount"))),
          min_cost(read_number(j.at("min_cost"))) {
        const auto mode = j.at("price_mode").get<std::string>();
        require(mode == "tick" || mode == "significant", "unsupported price precision mode");
        significant = mode == "significant";
        if (significant) {
            digits = j.at("price_digits").get<unsigned>();
            max_decimals = j.at("max_price_decimals").get<unsigned>();
            require(digits > 0 && digits <= 18 && max_decimals <= 18, "invalid significant precision");
            price_tick = Rational("1e-" + std::to_string(max_decimals));
            static const auto powers = [] {
                std::vector<Rational> result; result.reserve(1026);
                auto value = Rational(1) / Rational(10).power(512);
                for (unsigned exponent = 0; exponent < 1026; ++exponent) {
                    result.push_back(value); (void)result.back().ratio(); value = value * Rational(10);
                }
                return result;
            }();
            decimal_powers = &powers;
            significant_divisor = Rational(10).power(digits - 1); (void)significant_divisor.ratio();
        } else price_tick = read_number(j.at("price_tick"));
        require(price_tick.sign() > 0 && amount_tick.sign() > 0 &&
                min_amount.sign() >= 0 && min_cost.sign() >= 0, "invalid market minima or increments");
        // These shared constants must not lazily mutate from worker threads.
        (void)price_tick.ratio(); (void)amount_tick.ratio();
        (void)min_amount.ratio(); (void)min_cost.ratio();
        minimum_lots = Integer::from_rational_integer(max(amount_tick, min_amount).rounded(amount_tick, true) / amount_tick);
    }
    Rational round(const Rational& value, bool up = false) const {
        require(value.sign() > 0, "positive price required");
        if (!significant) return value.rounded(price_tick, up);
        const auto upper = std::upper_bound(decimal_powers->begin(), decimal_powers->end(), value);
        require(upper != decimal_powers->begin() && upper != decimal_powers->end(), "price exponent outside research bound");
        const Rational quantum = max(price_tick, *std::prev(upper) / significant_divisor);
        return value.rounded(quantum, up);
    }
    Integer minimum(const Integer& cost_per_lot, const Integer& minimum_cost_units) const {
        require(cost_per_lot.sign() > 0, "positive minimum lot cost required");
        auto lots = minimum_lots;
        if (minimum_cost_units.sign() > 0) {
            const auto cost_lots = Integer::ceil_div_positive(minimum_cost_units, cost_per_lot);
            if (cost_lots > lots) lots = cost_lots;
        }
        require(lots * cost_per_lot >= minimum_cost_units && lots >= minimum_lots && lots.sign() > 0,
                "minimum lot calculation failed");
        const auto previous = lots - Integer(1);
        require(previous.sign() <= 0 || previous < minimum_lots || previous * cost_per_lot < minimum_cost_units,
                "minimum lot is not minimal");
        return lots;
    }
};
struct Source {
    std::string id, venue, symbol, path, sha256, encoded_id, encoded_venue, encoded_symbol;
    std::uint64_t records = 0;
    std::int64_t first_ms = 0, last_ms = 0;
    std::size_t venue_index = 0;
    std::uint32_t render_group = 0;
    struct stat identity {};
    Rules rules;
    unsigned dyadic_places = 0;
    Scale money_scale;
    Scale quantity_scale;
    Integer quantity_coefficient{1}, minimum_cost_units;
    std::unique_ptr<units::Float32Coefficient> price_coefficient;
    Integer to_total{1}, to_venue{1};
    std::array<const std::vector<units::PreparedProduct>*, 20> grid_products {};
    explicit Source(const J& j) : id(j.at("id")), venue(j.at("venue")), symbol(j.at("symbol")),
        path(j.at("path")), sha256(j.at("sha256")), records(j.at("records")),
        first_ms(j.at("first_ms")), last_ms(j.at("last_ms")), rules(j.at("rules")) {
        require(!id.empty() && id.size() <= 256 && symbol.size() <= 128 && symbol.ends_with("/USDT") &&
                symbol.find(':') == std::string::npos, "only explicitly scoped spot USDT sources are admitted");
        static const std::set<std::string> venues {"okx", "htx", "gate", "xt", "bingx", "kucoin", "bitfinex"};
        require(venues.contains(venue), "unsupported research venue");
        (void)parse_digest(sha256);
        require(records > 0 && records <= static_cast<std::uint64_t>(std::numeric_limits<off_t>::max()) / record_bytes,
                "invalid source record count");
        require(first_ms > 0 && last_ms >= first_ms && first_ms % minute_ms == 0 && last_ms % minute_ms == 0,
                "invalid declared source timestamps");
        require(fs::path(path).is_absolute(), "absolute source paths required");
        encoded_id = J(id).dump(); encoded_venue = J(venue).dump(); encoded_symbol = J(symbol).dump();
    }
    void check_identity() const {
        struct stat current {};
        require(::stat(path.c_str(), &current) == 0 && same_identity(identity, current), "source changed: " + id);
    }
};

struct Audit {
    std::uint64_t bytes = 0, rows = 0, gaps = 0, missing_minutes = 0;
};
struct BatchAudit {
    Audit audit;
    std::string sources_sha256;
    std::vector<struct stat> identities;
    std::vector<unsigned> dyadic_places;
};
Audit audit_source(Source& source, bool initial) {
    File file(::open(source.path.c_str(), O_RDONLY | O_CLOEXEC | O_NOFOLLOW));
    require(file.fd >= 0, "cannot open source: " + source.id);
    struct stat before {}, after {};
    require(::fstat(file.fd, &before) == 0 && S_ISREG(before.st_mode) && before.st_size >= 0 &&
            static_cast<std::uint64_t>(before.st_size) == source.records * record_bytes, "source length mismatch: " + source.id);
    if (!initial) require(same_identity(source.identity, before), "source identity changed: " + source.id);
    auto hash = std::unique_ptr<EVP_MD_CTX, decltype(&EVP_MD_CTX_free)>(EVP_MD_CTX_new(), EVP_MD_CTX_free);
    require(hash && EVP_DigestInit_ex(hash.get(), EVP_sha256(), nullptr) == 1, "source hash initialization failed");
    std::array<std::byte, record_bytes * 4096> buffer {};
    Audit result;
    std::int64_t previous = 0;
    while (result.rows < source.records) {
        const auto count = static_cast<std::size_t>(std::min<std::uint64_t>(4096, source.records - result.rows)) * record_bytes;
        std::size_t got = 0;
        while (got < count) {
            const auto amount = ::read(file.fd, buffer.data() + got, count - got);
            if (amount < 0 && errno == EINTR) continue;
            require(amount > 0, "short source read: " + source.id);
            got += static_cast<std::size_t>(amount);
        }
        require(EVP_DigestUpdate(hash.get(), buffer.data(), got) == 1, "source hash update failed");
        if (!initial) result.rows += got / record_bytes;
        else for (std::size_t index = 0; index < got; index += record_bytes) {
            const auto bar = decode(std::span(buffer).subspan(index, record_bytes));
            if (initial) for (const auto& price : bar.prices) {
                const unsigned exponent = (price.bits >> 23U) & 255U;
                const auto significand = exponent == 0 ? price.bits & 0x7fffffU : (price.bits & 0x7fffffU) | 0x800000U;
                const unsigned raw_places = exponent == 0 ? 149U : (exponent < 150U ? 150U - exponent : 0U);
                const auto zeros = static_cast<unsigned>(std::countr_zero(significand));
                const unsigned places = raw_places > zeros ? raw_places - zeros : 0;
                source.dyadic_places = std::max(source.dyadic_places, places);
            }
            require(bar.timestamp > previous, "source timestamps are not strictly increasing: " + source.id);
            if (result.rows == 0) require(bar.timestamp == source.first_ms, "source first timestamp mismatch");
            if (previous != 0 && bar.timestamp - previous > minute_ms) {
                ++result.gaps;
                result.missing_minutes += static_cast<std::uint64_t>((bar.timestamp - previous) / minute_ms - 1);
            }
            previous = bar.timestamp;
            ++result.rows;
        }
        result.bytes += got;
        if (interrupted) throw Error("interrupted during source validation");
    }
    require(!initial || previous == source.last_ms, "source last timestamp mismatch");
    Digest digest {}; unsigned length = 0;
    require(EVP_DigestFinal_ex(hash.get(), digest.data(), &length) == 1 && length == digest.size(), "source hash finalization failed");
    require(hex(digest.data(), digest.size()) == source.sha256, "source SHA256 mismatch: " + source.id);
    require(::fstat(file.fd, &after) == 0 && same_identity(before, after), "source changed during validation: " + source.id);
    if (initial) source.identity = before;
    source.check_identity();
    return result;
}

Audit audit_sources(std::vector<Source>& sources, bool initial, unsigned count,
                    const std::function<void(std::size_t)>& progress) {
    std::vector<Audit> results(sources.size());
    std::atomic<std::size_t> next{0}, completed{0};
    std::atomic<bool> failed{false};
    std::mutex mutex;
    std::condition_variable changed;
    std::exception_ptr error;
    std::vector<std::thread> workers;
    const auto stop = [&] {
        failed.store(true, std::memory_order_relaxed);
        for (auto& worker : workers) if (worker.joinable()) worker.join();
    };
    try {
        for (unsigned i = 0; i < std::min<std::size_t>(count, sources.size()); ++i) workers.emplace_back([&] {
            try {
                while (!failed.load(std::memory_order_relaxed)) {
                    const auto index = next.fetch_add(1, std::memory_order_relaxed);
                    if (index >= sources.size()) break;
                    results[index] = audit_source(sources[index], initial);
                    completed.fetch_add(1, std::memory_order_relaxed);
                    changed.notify_one();
                }
            } catch (...) {
                { std::lock_guard lock(mutex); if (!error) error = std::current_exception(); }
                failed.store(true, std::memory_order_relaxed); changed.notify_one();
            }
        });
        while (completed.load(std::memory_order_relaxed) != sources.size() && !failed.load(std::memory_order_relaxed)) {
            { std::unique_lock lock(mutex); changed.wait_for(lock, std::chrono::milliseconds(100)); }
            if (progress) progress(completed.load(std::memory_order_relaxed));
        }
        stop();
    } catch (...) { stop(); throw; }
    if (error) std::rethrow_exception(error);
    if (progress) progress(sources.size());
    Audit total;
    for (const auto& result : results) {
        total.bytes += result.bytes; total.rows += result.rows;
        total.gaps += result.gaps; total.missing_minutes += result.missing_minutes;
    }
    return total;
}

struct Metrics {
    const Scale* scale = nullptr;
    Integer spent, realized, fees, value, peak_spent, pnl_peak, drawdown;
    // Only trades change the cash basis. The drawdown floor is the lowest
    // current PnL that would set a new lifetime drawdown at the present peak.
    Integer cash_basis, drawdown_floor;
    std::int64_t peak_ms = 0, drawdown_ms = 0, longest = 0;
    std::uint64_t buys = 0, sells = 0;
    void update_cash_basis() { cash_basis = realized - spent - fees; }
    Integer pnl() const { return cash_basis + value; }
    std::string render(const Integer& amount) const { require(scale != nullptr, "unbound monetary scale"); return scale->render(amount); }
    void observe(std::int64_t timestamp, const Integer& current) {
        if (spent > peak_spent) { peak_spent = spent; peak_ms = timestamp; }
        if (current > pnl_peak) { pnl_peak = current; drawdown_floor = pnl_peak - drawdown; return; }
        if (current < drawdown_floor) {
            drawdown = pnl_peak - current; drawdown_floor = current; drawdown_ms = timestamp;
        }
    }
    void observe(std::int64_t timestamp) { observe(timestamp, pnl()); }
    void observe_price(std::int64_t timestamp, bool rising) {
        // With unchanged inventory/cash the sign of a positive float32 price
        // change is also the sign of PnL. Spent cannot establish a new peak.
        const auto current = pnl();
        if (rising) {
            if (current > pnl_peak) { pnl_peak = current; drawdown_floor = pnl_peak - drawdown; }
        } else if (current < drawdown_floor) {
            drawdown = pnl_peak - current; drawdown_floor = current; drawdown_ms = timestamp;
        }
    }
    J report() const {
        const auto current_pnl = pnl();
        return {{"realized_gross", render(realized)}, {"unrealized_gross", render(value - spent)},
            {"fees", render(fees)}, {"total_pnl", render(current_pnl)}, {"spent", render(spent)}, {"open_value", render(value)},
            {"peak_spent", render(peak_spent)}, {"peak_spent_ms", peak_ms},
            {"return_pct", peak_spent.sign() > 0 ? J((current_pnl * Integer(100)).fraction(peak_spent)) : J(nullptr)},
            {"max_drawdown", render(drawdown)}, {"max_drawdown_ms", drawdown_ms},
            {"longest_hold_ms", longest}, {"buy_count", buys}, {"sell_count", sells}};
    }
    J save() const {
        // Reports include derived PnL/return fields. Recovery needs only the
        // exact independent state, not another reduction of every percentage.
        return {{"spent", render(spent)}, {"realized_gross", render(realized)},
            {"fees", render(fees)}, {"value", render(value)}, {"peak_spent", render(peak_spent)},
            {"pnl_peak", render(pnl_peak)}, {"max_drawdown", render(drawdown)},
            {"peak_spent_ms", peak_ms}, {"max_drawdown_ms", drawdown_ms},
            {"longest_hold_ms", longest}, {"buy_count", buys}, {"sell_count", sells}};
    }
    void restore(const J& j) {
        const auto read = [&](const char* key) { return scale->encode(checkpoint_number(j.at(key))); };
        spent = read("spent"); realized = read("realized_gross");
        fees = read("fees"); value = read("value");
        peak_spent = read("peak_spent"); pnl_peak = read("pnl_peak");
        drawdown = read("max_drawdown"); peak_ms = j.at("peak_spent_ms");
        drawdown_ms = j.at("max_drawdown_ms"); longest = j.at("longest_hold_ms");
        buys = j.at("buy_count"); sells = j.at("sell_count");
        update_cash_basis(); drawdown_floor = pnl_peak - drawdown;
        require(spent.sign() >= 0 && fees.sign() >= 0 && value.sign() >= 0 && peak_spent >= spent &&
                drawdown.sign() >= 0 && longest >= 0, "invalid checkpoint accounting");
    }
};

// Calendar statistics are independent of trading state. Money and the moments
// behind risk ratios remain exact integers in the existing monetary scale.
// Only the displayed square root is truncated, to 24 decimal places; its exact
// squared ratio is retained so presentation never becomes accounting input.
std::int64_t month_boundary(std::int64_t timestamp, bool next) {
    using namespace std::chrono;
    const year_month_day date{floor<days>(sys_time<milliseconds>{milliseconds{timestamp}})};
    auto month = date.year() / date.month();
    if (next) month += months{1};
    return duration_cast<milliseconds>(sys_days{month / day{1}}.time_since_epoch()).count();
}
std::string exact_root_display(const Integer& numerator, const Integer& denominator, int sign) {
    Integer decimal_scale(1);
    for (unsigned i = 0; i < 24; ++i) decimal_scale *= Integer(10);
    const auto scaled = Integer::floor_div_positive(numerator * decimal_scale * decimal_scale, denominator);
    Integer root;
    if (scaled.sign() > 0) {
        root = Integer(1).shifted(static_cast<int>((scaled.bit_width() + 1) / 2));
        for (;;) {
            auto next = Integer::floor_div_positive(root + Integer::floor_div_positive(scaled, root), Integer(2));
            if (next >= root) break;
            root = std::move(next);
        }
    }
    auto digits = root.str();
    if (digits.size() <= 24) digits.insert(0, 25 - digits.size(), '0');
    digits.insert(digits.size() - 24, 1, '.');
    while (digits.back() == '0') digits.pop_back();
    if (digits.back() == '.') digits.pop_back();
    if (sign < 0 && root.sign() != 0) digits.insert(digits.begin(), '-');
    return digits;
}
struct MonthMoments {
    std::uint64_t count = 0;
    Integer sum, squares, downside;
    void add(const Integer& pnl) {
        ++count; sum += pnl;
        const auto square = pnl * pnl;
        squares += square;
        if (pnl.sign() < 0) downside += square;
    }
    void append(J& row) const {
        row["sample_months"] = count;
        row["monthly_sum_units"] = sum.str();
        row["monthly_square_sum_units"] = squares.str();
        row["monthly_downside_square_sum_units"] = downside.str();
        const auto emit = [&](const char* name, const Integer& numerator, const Integer& denominator,
                              const char* unavailable) {
            row[name] = nullptr; row[std::string(name) + "_squared"] = nullptr;
            row[std::string(name) + "_status"] = unavailable;
            if (denominator.sign() > 0) {
                row[name] = exact_root_display(numerator, denominator, sum.sign());
                row[std::string(name) + "_squared"] = numerator.fraction(denominator);
                row[std::string(name) + "_status"] = "available";
            }
        };
        if (count < 2) {
            emit("sharpe", Integer(), Integer(), "insufficient_months");
            emit("sortino", Integer(), Integer(), "insufficient_months");
            return;
        }
        const Integer n(static_cast<std::int64_t>(count));
        const auto sum_squared = sum * sum;
        const auto variance = n * squares - sum_squared;
        require(variance.sign() >= 0, "negative exact monthly variance");
        // Sample SD for Sharpe; downside RMS divides by all observations for
        // Sortino. Zero risk-free/target return and sqrt(12) annualization.
        emit("sharpe", sum_squared * Integer(12) * Integer(static_cast<std::int64_t>(count - 1)),
             n * variance, "zero_variance");
        emit("sortino", sum_squared * Integer(12), n * downside, "no_downside");
    }
};
struct MonthRecord {
    std::int64_t start_ms = 0, end_ms = 0, peak_ms = 0;
    Integer spent_open, spent, peak, pnl, realized, fees, profit, loss;
    Integer drawdown;
    std::int64_t drawdown_ms = 0;
    std::uint64_t buys = 0, sells = 0, wins = 0, losses = 0, breakevens = 0;
    bool complete() const { return start_ms == month_boundary(start_ms, false) && end_ms == month_boundary(start_ms, true); }
    J save(bool detailed = false) const {
        auto row = J::array({start_ms, end_ms, peak_ms, spent_open.str(), spent.str(), peak.str(), pnl.str(),
            realized.str(), fees.str(), profit.str(), loss.str(), buys, sells, wins, losses, breakevens});
        if (detailed) { row.push_back(drawdown.str()); row.push_back(drawdown_ms); }
        return row;
    }
    static MonthRecord restore(const J& row, bool active = false) {
        require(row.is_array() && (row.size() == 16 || row.size() == 18), "invalid monthly checkpoint record");
        MonthRecord result;
        result.start_ms = row.at(0); result.end_ms = row.at(1); result.peak_ms = row.at(2);
        std::array<Integer*, 8> money {&result.spent_open, &result.spent, &result.peak, &result.pnl,
            &result.realized, &result.fees, &result.profit, &result.loss};
        for (std::size_t i = 0; i < money.size(); ++i) *money[i] = Integer(row.at(i + 3).get<std::string>());
        result.buys = row.at(11); result.sells = row.at(12); result.wins = row.at(13);
        result.losses = row.at(14); result.breakevens = row.at(15);
        if (row.size() == 18) { result.drawdown = Integer(row.at(16).get<std::string>()); result.drawdown_ms = row.at(17); }
        require(result.start_ms >= 0 && (active || result.end_ms >= result.start_ms) && result.spent_open.sign() >= 0 &&
                result.spent.sign() >= 0 && result.peak >= result.spent && result.peak >= result.spent_open &&
                result.fees.sign() >= 0 && result.profit.sign() >= 0 && result.loss.sign() >= 0 &&
                (active || result.wins + result.losses + result.breakevens == result.sells),
                "invalid monthly checkpoint accounting");
        return result;
    }
    J report(const Scale& scale) const {
        return {{"start_ms", start_ms}, {"end_ms", end_ms}, {"complete_month", complete()},
            {"spent_open", scale.render(spent_open)}, {"spent", scale.render(spent)},
            {"peak_spent", scale.render(peak)}, {"peak_spent_ms", peak_ms},
            {"total_pnl", scale.render(pnl)}, {"realized_gross", scale.render(realized)}, {"fees", scale.render(fees)},
            {"buy_count", buys}, {"sell_count", sells}, {"net_profit_closed", scale.render(profit)},
            {"net_loss_closed", scale.render(loss)}, {"winning_closures", wins},
            {"losing_closures", losses}, {"breakeven_closures", breakevens},
            {"profit_factor", loss.sign() > 0 ? J(profit.fraction(loss)) : J(nullptr)},
            {"profit_factor_status", sells == 0 ? "no_closures" : (loss.sign() == 0 ? "no_losses" : "available")}};
    }
};
struct Performance {
    const Scale* scale = nullptr;
    MonthRecord current;
    Integer pnl_base, realized_base, fee_base;
    std::uint64_t buys_base = 0, sells_base = 0;
    std::vector<MonthRecord> months;
    std::int64_t next_month = 0;
    bool detailed = false;
    void observe_drawdown(const Metrics& metrics, std::int64_t timestamp) {
        if (!detailed) return;
        const auto value = metrics.pnl_peak - metrics.pnl();
        require(value.sign() >= 0, "detail PnL peak is behind current PnL");
        if (value > current.drawdown) { current.drawdown = value; current.drawdown_ms = timestamp; }
    }
    void start(std::int64_t origin, const Metrics& metrics) {
        scale = metrics.scale; current.start_ms = origin; next_month = month_boundary(origin, true);
    }
    void observe_spent(const Metrics& metrics, std::int64_t timestamp) {
        if (metrics.spent > current.peak) { current.peak = metrics.spent; current.peak_ms = timestamp; }
    }
    void closed(const Integer& net) {
        if (net.sign() > 0) { current.profit += net; ++current.wins; }
        else if (net.sign() < 0) { current.loss -= net; ++current.losses; }
        else ++current.breakevens;
    }
    MonthRecord ending(std::int64_t end, const Metrics& metrics) const {
        auto row = current; row.end_ms = end; row.spent = metrics.spent;
        row.pnl = metrics.pnl() - pnl_base; row.realized = metrics.realized - realized_base;
        row.fees = metrics.fees - fee_base; row.buys = metrics.buys - buys_base; row.sells = metrics.sells - sells_base;
        return row;
    }
    void advance(std::int64_t timestamp, const Metrics& metrics) {
        while (timestamp >= next_month) {
            require(months.size() < 2400, "monthly history exceeds bounded research capacity");
            months.push_back(ending(next_month, metrics));
            pnl_base = metrics.pnl(); realized_base = metrics.realized; fee_base = metrics.fees;
            buys_base = metrics.buys; sells_base = metrics.sells;
            current = MonthRecord(); current.start_ms = next_month;
            current.spent_open = current.peak = metrics.spent;
            if (metrics.spent.sign() > 0) current.peak_ms = next_month;
            observe_drawdown(metrics, next_month);
            next_month = month_boundary(current.start_ms, true);
        }
    }
    J save() const {
        J rows = J::array(); for (const auto& row : months) rows.push_back(row.save(detailed));
        return {{"current", current.save(detailed)}, {"months", std::move(rows)}, {"next_month", next_month},
            {"pnl_base", pnl_base.str()}, {"realized_base", realized_base.str()}, {"fee_base", fee_base.str()},
            {"buys_base", buys_base}, {"sells_base", sells_base}};
    }
    void restore(const J& row, const Metrics& metrics) {
        current = MonthRecord::restore(row.at("current"), true); months.clear();
        require(row.at("months").is_array() && row.at("months").size() <= 2400, "invalid monthly checkpoint capacity");
        for (const auto& item : row.at("months")) months.push_back(MonthRecord::restore(item));
        next_month = row.at("next_month"); pnl_base = Integer(row.at("pnl_base").get<std::string>());
        realized_base = Integer(row.at("realized_base").get<std::string>()); fee_base = Integer(row.at("fee_base").get<std::string>());
        buys_base = row.at("buys_base"); sells_base = row.at("sells_base"); scale = metrics.scale;
        require(next_month == month_boundary(current.start_ms, true) && buys_base <= metrics.buys &&
                sells_base <= metrics.sells && fee_base >= Integer() && fee_base <= metrics.fees &&
                current.wins + current.losses + current.breakevens == metrics.sells - sells_base,
                "invalid monthly checkpoint baseline");
        Integer pnl, realized, fees; std::uint64_t buys = 0, sells = 0;
        for (std::size_t i = 0; i < months.size(); ++i) {
            const auto& item = months[i];
            require(item.end_ms == month_boundary(item.start_ms, true) &&
                    (i == 0 || months[i - 1].end_ms == item.start_ms), "monthly checkpoint calendar gap");
            pnl += item.pnl; realized += item.realized; fees += item.fees; buys += item.buys; sells += item.sells;
        }
        require(pnl == pnl_base && realized == realized_base && fees == fee_base && buys == buys_base && sells == sells_base &&
                (months.empty() || months.back().end_ms == current.start_ms), "monthly checkpoint cumulative mismatch");
    }
    J report(std::int64_t end, const Metrics& metrics) const {
        MonthMoments moments; MonthRecord whole; J rows = J::array();
        // A bounded diagnostic replay can finish before this venue's first
        // observation. Its monthly history is empty at the diagnostic end.
        whole.start_ms = months.empty() ? std::min(current.start_ms, end) : months.front().start_ms;
        const auto append = [&](const MonthRecord& item) {
            if (item.complete()) moments.add(item.pnl);
            auto row = item.report(*scale); moments.append(row); rows.push_back(std::move(row));
            whole.profit += item.profit; whole.loss += item.loss;
            whole.wins += item.wins; whole.losses += item.losses; whole.breakevens += item.breakevens;
        };
        for (const auto& item : months) append(item);
        if (current.start_ms < end) append(ending(end, metrics));
        whole.end_ms = end; whole.spent = metrics.spent; whole.peak = metrics.peak_spent;
        whole.peak_ms = metrics.peak_ms; whole.pnl = metrics.pnl(); whole.realized = metrics.realized;
        whole.fees = metrics.fees; whole.buys = metrics.buys; whole.sells = metrics.sells;
        auto overall = whole.report(*scale); overall.erase("complete_month"); moments.append(overall);
        return {{"money_scale_denominator", scale->denominator().str()}, {"overall", std::move(overall)}, {"monthly", std::move(rows)}};
    }
    J drawdown_report(std::int64_t end, const Metrics& metrics) const {
        J rows = J::array();
        const auto append = [&](const MonthRecord& row) {
            rows.push_back({{"start_ms", row.start_ms}, {"end_ms", row.end_ms},
                {"max_drawdown", scale->render(row.drawdown)}, {"max_drawdown_ms", row.drawdown_ms},
                {"max_drawdown_pct", row.peak.sign() > 0 ? J((row.drawdown * Integer(100)).fraction(row.peak)) : J(nullptr)},
                {"percentage_status", row.peak.sign() > 0 ? "available" : "zero_peak_spent"}});
        };
        for (const auto& row : months) append(row);
        if (current.start_ms < end) append(ending(end, metrics));
        return rows;
    }
};
struct Pair {
    Metrics metrics;
    const Source* source = nullptr;
    Integer quantity;
    Rational anchor, floor, buy, sell;
    bool started = false, reseed = false;
    unsigned level = 0;
    std::uint32_t buy_bits = 0, sell_bits = last_float + 1, last_bits = 0;
    std::int64_t last_ms = 0, entry_ms = 0;
    Digest digest {};
    Rational exact_quantity() const { return source->quantity_scale.decode(quantity * source->quantity_coefficient); }
    std::string render_quantity() const { return source->quantity_scale.render(quantity * source->quantity_coefficient); }
    J save() const {
        J result {{"metrics", metrics.save()}, {"quantity", render_quantity()}, {"anchor", number(anchor)},
            {"buy", number(buy)}, {"sell", number(sell)}, {"started", started}, {"level", level},
            {"last_bits", last_bits}, {"last_ms", last_ms}, {"entry_ms", entry_ms},
            {"trade_hash", hex(digest.data(), digest.size())}};
        if (reseed) result["reseed"] = true;
        return result;
    }
    void restore(const J& j, const Rational& floor_factor) {
        metrics.restore(j.at("metrics")); quantity = Integer::from_rational_integer(checkpoint_number(j.at("quantity")) / source->rules.amount_tick);
        anchor = checkpoint_number(j.at("anchor")); buy = checkpoint_number(j.at("buy"));
        sell = checkpoint_number(j.at("sell")); started = j.at("started"); level = j.at("level");
        reseed = j.value("reseed", false);
        last_bits = j.at("last_bits"); last_ms = j.at("last_ms"); entry_ms = j.at("entry_ms");
        digest = parse_digest(j.at("trade_hash"));
        require(level <= 184 && quantity.sign() >= 0 && buy.sign() >= 0 && sell.sign() >= 0 &&
                ((started && anchor.sign() > 0 && last_bits > 0 && last_bits <= last_float) ||
                 (!started && quantity.sign() == 0 && last_bits == 0)), "invalid checkpoint position");
        require(!reseed || (quantity.sign() == 0 && buy.sign() == 0 && sell.sign() == 0), "invalid checkpoint reseed");
        floor = anchor * floor_factor;
        require(buy.sign() == 0 || buy >= floor, "checkpoint BUY below floor");
        buy_bits = buy.sign() > 0 ? buy_cutoff(buy) : 0;
        sell_bits = sell.sign() > 0 ? sell_cutoff(sell) : last_float + 1;
    }
};
// Only immutable numeric text is cached. Config ID, sequence, trade time,
// quote time and digest are always generated for the actual trade. A direct
// mapped per-thread table bounds memory and needs no locks or entry allocation.
struct TradeRenderCache {
    static constexpr std::size_t capacity = 4096;
    static constexpr std::size_t maximum_text_bytes = 2048;
    static constexpr std::size_t maximum_string_capacity = 4096;
    struct Entry {
        std::uint32_t group = std::numeric_limits<std::uint32_t>::max();
        __int128 price_units = 0, lots = 0;
        std::string cost, tail;
    };
    std::array<Entry, capacity> entries;
};
const TradeRenderCache::Entry* rendered_trade(const Source& source, const Rational& price,
        const Integer& lots, const Integer& cost_per_lot, const Integer& cost, const Integer& fee,
        std::uint64_t& hits, std::uint64_t& misses, std::uint64_t& bypasses) {
    __int128 price_units = 0, quantity = 0;
    if (!cost_per_lot.native_value(price_units) || !lots.native_value(quantity)) { ++bypasses; return nullptr; }
    const auto mix = [](std::uint64_t value) {
        value ^= value >> 30U; value *= 0xbf58476d1ce4e5b9ULL;
        value ^= value >> 27U; value *= 0x94d049bb133111ebULL;
        return value ^ (value >> 31U);
    };
    const auto p = static_cast<unsigned __int128>(price_units), q = static_cast<unsigned __int128>(quantity);
    const auto key = mix(source.render_group) ^
        mix(static_cast<std::uint64_t>(p) ^ static_cast<std::uint64_t>(p >> 64U)) ^
        mix(static_cast<std::uint64_t>(q) ^ std::rotl(static_cast<std::uint64_t>(q >> 64U), 23));
    thread_local auto cache = std::make_unique<TradeRenderCache>();
    auto& entry = cache->entries[key & (TradeRenderCache::capacity - 1U)];
    if (entry.group == source.render_group && entry.price_units == price_units && entry.lots == quantity) { ++hits; return &entry; }
    ++misses;
    entry.group = std::numeric_limits<std::uint32_t>::max(); entry.cost.clear(); entry.tail.clear();
    source.money_scale.append(entry.cost, cost);
    entry.tail += "\",\"fee\":\""; source.money_scale.append(entry.tail, fee);
    entry.tail += "\",\"price\":\""; units::append_ratio(entry.tail, price);
    entry.tail += "\",\"quantity\":\""; source.quantity_scale.append(entry.tail, lots * source.quantity_coefficient);
    entry.tail.push_back('"');
    if (entry.cost.size() + entry.tail.size() > TradeRenderCache::maximum_text_bytes ||
        entry.cost.capacity() + entry.tail.capacity() > TradeRenderCache::maximum_string_capacity) {
        std::string().swap(entry.cost); std::string().swap(entry.tail); ++bypasses; return nullptr;
    }
    entry.price_units = price_units; entry.lots = quantity; entry.group = source.render_group;
    return &entry;
}
struct GridLevelCache {
    static constexpr std::size_t capacity = 16384;
    struct Entry {
        const std::vector<units::PreparedProduct>* table = nullptr;
        std::int64_t anchor_n = 0, anchor_d = 1;
        unsigned level = 0;
        std::uint32_t cutoff = 0;
        Rational price;
    };
    // Only native Rational prices enter this table: no dynamic BIGNUM storage
    // can grow with history. A wide anchor/price takes the exact uncached path.
    std::array<Entry, capacity> entries;
};
std::pair<Rational, std::uint32_t> grid_level(const std::vector<units::PreparedProduct>& products,
        const Rational& anchor, unsigned level, bool cached,
        std::uint64_t& hits, std::uint64_t& misses, std::uint64_t& bypasses) {
    std::int64_t numerator = 0, denominator = 1;
    GridLevelCache::Entry* entry = nullptr;
    if (cached && units::native_ratio(anchor, numerator, denominator)) {
        auto key = static_cast<std::uint64_t>(reinterpret_cast<std::uintptr_t>(&products));
        key ^= static_cast<std::uint64_t>(numerator) * 0x9e3779b97f4a7c15ULL;
        key ^= std::rotl(static_cast<std::uint64_t>(denominator), 23);
        key ^= static_cast<std::uint64_t>(level) * 0xbf58476d1ce4e5b9ULL;
        key ^= key >> 30U; key *= 0xbf58476d1ce4e5b9ULL; key ^= key >> 27U;
        thread_local auto cache = std::make_unique<GridLevelCache>();
        entry = &cache->entries[key & (GridLevelCache::capacity - 1U)];
        if (entry->table == &products && entry->anchor_n == numerator && entry->anchor_d == denominator && entry->level == level) {
            ++hits; return {entry->price, entry->cutoff};
        }
        ++misses;
    } else ++bypasses;
    auto price = products.at(level)(anchor);
    const auto cutoff = price.sign() > 0 ? buy_cutoff(price) : 0;
    std::int64_t price_n = 0, price_d = 1;
    if (entry && units::native_ratio(price, price_n, price_d)) {
        entry->price = price; entry->cutoff = cutoff; entry->anchor_n = numerator; entry->anchor_d = denominator;
        entry->level = level; entry->table = &products;
    }
    return {std::move(price), cutoff};
}

// Lifetime accounting never resets. Only these scoped baselines and the
// attained simultaneous capital peak restart after an aggregate exit.
struct Cycle {
    Integer baseline_realized, baseline_fees, peak_spent, threshold, baseline_target;
    Integer value_cutoff;
    bool cutoff_dirty = true, cutoff_fallback = false;
    std::int64_t peak_ms = 0, last_exit_ms = 0;
    std::uint64_t completed = 0;
    void invalidate() { cutoff_dirty = true; }
    void observe(const Metrics& metrics, std::int64_t timestamp, unsigned half_percent) {
        if (metrics.spent > peak_spent) {
            peak_spent = metrics.spent; peak_ms = timestamp; invalidate();
            if (half_percent != 0)
                threshold = peak_spent * Integer(static_cast<std::int64_t>(half_percent) * 5) + baseline_target;
        }
    }
    bool triggered(const Metrics& metrics, const Integer& pnl, unsigned half_percent) {
        if (half_percent == 0 || peak_spent.sign() == 0) return false;
        if (cutoff_dirty) {
            // 1000 * (cash_basis + value) - 2 * value >= threshold.
            // Value is a nonnegative exact integer in this scope's scale, so
            // ceil((threshold - 1000 * cash_basis) / 998) is the same boundary.
            // Only trades, a new spent peak, or a cycle reset can change it.
            // Near the bounded-bigint limit, cancellation in the original
            // expression can succeed even if forming 1000*cash_basis cannot.
            // Keep that original path for these exceptional magnitudes.
            cutoff_fallback = metrics.cash_basis.bit_width() > 131060 || threshold.bit_width() > 131070;
            if (!cutoff_fallback) {
                const auto numerator = threshold - metrics.cash_basis * Integer(1000);
                value_cutoff = numerator.sign() <= 0 ? Integer() : Integer::ceil_div_positive(numerator, Integer(998));
            }
            cutoff_dirty = false;
        }
        if (cutoff_fallback || (!metrics.value.is_small() && metrics.value.bit_width() > 131060))
            return pnl * Integer(1000) - metrics.value * Integer(2) >= threshold;
        return metrics.value >= value_cutoff;
    }
    void reset(const Metrics& metrics, std::int64_t timestamp) {
        require(metrics.spent.sign() == 0 && metrics.value.sign() == 0, "aggregate cycle reset with open exposure");
        if (peak_spent.sign() > 0) ++completed;
        baseline_realized = metrics.realized; baseline_fees = metrics.fees;
        baseline_target = (baseline_realized - baseline_fees) * Integer(1000);
        peak_spent = Integer(); threshold = Integer(); peak_ms = 0;
        invalidate();
        last_exit_ms = timestamp;
    }
    J report(const Metrics& metrics) const {
        return {{"baseline_realized_gross", metrics.render(baseline_realized)},
            {"baseline_fees", metrics.render(baseline_fees)}, {"peak_spent", metrics.render(peak_spent)},
            {"peak_spent_ms", peak_ms}, {"last_exit_ms", last_exit_ms}, {"completed_count", completed}};
    }
    void restore(const J& j, const Metrics& metrics, unsigned half_percent) {
        baseline_realized = metrics.scale->encode(checkpoint_number(j.at("baseline_realized_gross")));
        baseline_fees = metrics.scale->encode(checkpoint_number(j.at("baseline_fees")));
        peak_spent = metrics.scale->encode(checkpoint_number(j.at("peak_spent")));
        peak_ms = j.at("peak_spent_ms"); last_exit_ms = j.at("last_exit_ms"); completed = j.at("completed_count");
        require(baseline_fees.sign() >= 0 && baseline_fees <= metrics.fees && peak_spent >= metrics.spent &&
                peak_ms >= 0 && last_exit_ms >= 0, "invalid checkpoint aggregate cycle");
        baseline_target = (baseline_realized - baseline_fees) * Integer(1000);
        if (half_percent != 0)
            threshold = peak_spent * Integer(static_cast<std::int64_t>(half_percent) * 5) + baseline_target;
        invalidate();
    }
};

struct Chart {
    const Scale* scale = nullptr;
    bool active = false;
    std::int64_t bin = -1, first_ms = 0, last_ms = 0;
    std::int64_t pnl_min_ms = 0, pnl_max_ms = 0, spent_min_ms = 0, spent_max_ms = 0;
    Integer first_pnl, last_pnl, min_pnl, max_pnl, first_spent, last_spent, min_spent, max_spent;
    bool detailed = false;
    Integer first_dd, last_dd, min_dd, max_dd;
    std::array<std::uint64_t, 8> orders {};
    std::int64_t dd_min_ms = 0, dd_max_ms = 0;
    std::string render(const Integer& value) const { return scale->render(value); }
    J save() const {
        J row {{"active", active}, {"bin", bin}, {"first_ms", first_ms}, {"last_ms", last_ms},
            {"pnl_first", render(first_pnl)}, {"pnl_last", render(last_pnl)},
            {"pnl_min", render(min_pnl)}, {"pnl_max", render(max_pnl)},
            {"spent_first", render(first_spent)}, {"spent_last", render(last_spent)},
            {"spent_min", render(min_spent)}, {"spent_max", render(max_spent)},
            {"pnl_min_ms", pnl_min_ms}, {"pnl_max_ms", pnl_max_ms},
            {"spent_min_ms", spent_min_ms}, {"spent_max_ms", spent_max_ms}};
        if (detailed) row["detail"] = {{"orders", orders}, {"dd_first", render(first_dd)},
            {"dd_last", render(last_dd)}, {"dd_min", render(min_dd)}, {"dd_max", render(max_dd)},
            {"dd_min_ms", dd_min_ms}, {"dd_max_ms", dd_max_ms}};
        return row;
    }
    void restore(const J& j) {
        active = j.at("active"); bin = j.at("bin"); first_ms = j.at("first_ms"); last_ms = j.at("last_ms");
        const auto read = [&](const char* key) { return scale->encode(checkpoint_number(j.at(key))); };
        first_pnl = read("pnl_first"); last_pnl = read("pnl_last");
        min_pnl = read("pnl_min"); max_pnl = read("pnl_max");
        first_spent = read("spent_first"); last_spent = read("spent_last");
        min_spent = read("spent_min"); max_spent = read("spent_max");
        pnl_min_ms = j.at("pnl_min_ms"); pnl_max_ms = j.at("pnl_max_ms");
        spent_min_ms = j.at("spent_min_ms"); spent_max_ms = j.at("spent_max_ms");
        require(!active || (bin >= 0 && last_ms >= first_ms), "invalid checkpoint chart");
        if (detailed) {
            const auto& extra = j.at("detail"); orders = extra.at("orders").get<std::array<std::uint64_t, 8>>();
            first_dd = scale->encode(checkpoint_number(extra.at("dd_first")));
            last_dd = scale->encode(checkpoint_number(extra.at("dd_last")));
            min_dd = scale->encode(checkpoint_number(extra.at("dd_min")));
            max_dd = scale->encode(checkpoint_number(extra.at("dd_max")));
            dd_min_ms = extra.at("dd_min_ms"); dd_max_ms = extra.at("dd_max_ms");
        }
    }
};
struct Record {
    std::int64_t timestamp; std::uint32_t source; std::array<std::uint32_t, 4> prices;
    std::array<Integer, 4> coefficients;
};
struct Block {
    std::vector<Record> records; std::vector<std::uint64_t> positions;
    std::vector<std::size_t> group_offsets;
    std::vector<unsigned> group_venue_masks;
    std::size_t coefficient_heap_estimate = 0;
    bool filled = false;
    std::size_t decoded_bytes() const { return records.size() * sizeof(Record) + coefficient_heap_estimate; }
    std::size_t capacity_bytes_estimate() const {
        return records.capacity() * sizeof(Record) + coefficient_heap_estimate +
            group_offsets.capacity() * sizeof(std::size_t) + group_venue_masks.capacity() * sizeof(unsigned) +
            positions.capacity() * sizeof(std::uint64_t);
    }
};

struct Configuration {
    std::string id, encoded_id, trade_buffer;
    Rational step, tp, ratio, tp_factor, floor_factor;
    unsigned grid_depth_pct = 60;
    bool explicit_grid_depth = false;
    const std::vector<Rational>* factors = nullptr;
    unsigned grid_index = 20;
    std::vector<Pair> pairs;
    Metrics total;
    std::vector<Metrics> venues;
    unsigned exchange_tp_half_percent = 0, global_tp_half_percent = 0;
    bool aggregate_reporting = false;
    bool require_current_quotes = false;
    std::size_t open_positions = 0;
    std::array<std::size_t, 7> venue_open_positions {};
    bool performance_enabled = false;
    bool detail_enabled = false;
    unsigned performance_spent_mask = 0;
    std::vector<Performance> performance;
    std::vector<std::int64_t> venue_performance_starts, venue_performance_ends;
    std::int64_t performance_window_end = 0;
    std::int64_t next_performance_month = 0;
    Cycle global_cycle;
    std::vector<Cycle> venue_cycles;
    std::vector<std::pair<std::size_t, std::size_t>> venue_pair_ranges;
    J pair_fingerprint;
    std::vector<Chart> charts;
    std::vector<J> pending_series, pending_trades;
    Digest digest {};
    bool trace = false;
    bool chart_enabled = true;
    std::size_t chart_pair_index = std::numeric_limits<std::size_t>::max();
    std::uint64_t traced = 0;
    bool render_cache_enabled = true;
    std::uint64_t render_cache_hits = 0, render_cache_misses = 0, render_cache_bypasses = 0;
    bool grid_cache_enabled = true;
    std::uint64_t grid_cache_hits = 0, grid_cache_misses = 0, grid_cache_bypasses = 0;
    std::int64_t chart_origin = 0, chart_width = 1;
    double worker_seconds = 0;
    double last_worker_seconds = 0;
    void bind_scales(const std::vector<Source>& sources, const Scale& total_scale, const std::vector<Scale>& venue_scales) {
        total.scale = &total_scale; charts[0].scale = &total_scale;
        for (std::size_t i = 0; i < venues.size(); ++i) {
            venues[i].scale = &venue_scales[i]; charts[i + 1].scale = &venue_scales[i];
        }
        for (std::size_t i = 0; i < pairs.size(); ++i) {
            pairs[i].source = &sources[i]; pairs[i].metrics.scale = &sources[i].money_scale;
        }
        if (chart_pair_index < pairs.size()) charts.back().scale = &sources[chart_pair_index].money_scale;
        if (performance_enabled) {
            performance.resize(venues.size() + 1);
            performance[0].start(chart_origin, total);
            venue_performance_starts.assign(venues.size(), chart_origin);
            venue_performance_ends.assign(venues.size(), performance_window_end);
            if (require_current_quotes) {
                venue_performance_starts.assign(venues.size(), performance_window_end);
                venue_performance_ends.assign(venues.size(), chart_origin);
                std::vector<std::int64_t> venue_firsts(venues.size(), performance_window_end);
                std::vector<bool> observed(venues.size());
                for (const auto& source : sources) {
                    const auto venue = source.venue_index;
                    venue_firsts[venue] = std::min(venue_firsts[venue], source.first_ms);
                    if (source.last_ms < chart_origin || source.first_ms >= performance_window_end) continue;
                    observed[venue] = true;
                    venue_performance_starts[venue] = std::min(venue_performance_starts[venue],
                        std::clamp(source.first_ms, chart_origin, performance_window_end));
                    venue_performance_ends[venue] = std::max(venue_performance_ends[venue],
                        std::clamp(source.last_ms + minute_ms, chart_origin, performance_window_end));
                }
                for (std::size_t i = 0; i < venues.size(); ++i) if (!observed[i]) {
                    const auto empty_at = venue_firsts[i] < chart_origin ? chart_origin : performance_window_end;
                    venue_performance_starts[i] = venue_performance_ends[i] = empty_at;
                }
            }
            for (std::size_t i = 0; i < venues.size(); ++i) {
                require(venue_performance_starts[i] <= venue_performance_ends[i], "invalid venue research calendar");
                performance[i + 1].start(venue_performance_starts[i], venues[i]);
            }
            next_performance_month = performance[0].next_month;
            if (detail_enabled) {
                for (auto& scope : performance) scope.detailed = true;
                for (auto& chart : charts) chart.detailed = true;
            }
        }
        venue_pair_ranges.assign(venues.size(), {sources.size(), 0});
        for (std::size_t i = 0; i < sources.size(); ++i) {
            auto& range = venue_pair_ranges[sources[i].venue_index];
            range.first = std::min(range.first, i); range.second = i + 1;
        }
    }
    Configuration(const J& j, std::size_t source_count, std::size_t venue_count, bool tracing,
                  std::int64_t origin, std::int64_t end, std::int64_t width, bool aggregate_matrix = false)
      : id(j.at("id")), step(read_number(j.at("step_pct")) / Rational(100)),
        tp(read_number(j.at("tp_pct")) / Rational(100)), ratio(Rational(1) - step), tp_factor(Rational(1) + tp),
        pairs(source_count), venues(venue_count), aggregate_reporting(aggregate_matrix),
        performance_window_end(end), venue_cycles(venue_count), charts(venue_count + 1), trace(tracing),
        chart_origin(origin), chart_width(width) {
        require(!id.empty() && id.size() <= 128 && step >= Rational(".005") && step <= Rational(".10") &&
                tp >= Rational(".02") && tp <= Rational(".15"), "configuration outside approved parameter range");
        explicit_grid_depth = j.contains("grid_depth_pct");
        if (explicit_grid_depth) {
            const auto depth = read_number(j.at("grid_depth_pct"));
            bool allowed = false;
            for (unsigned value = 30; value <= 60; value += 5) if (depth == Rational(value)) {
                grid_depth_pct = value; allowed = true; break;
            }
            require(allowed, "grid depth outside approved 30..60 percent five-point matrix");
        }
        floor_factor = Rational(static_cast<std::int64_t>(100 - grid_depth_pct)) / Rational(100);
        const auto aggregate_tp = [&](const char* field) -> unsigned {
            if (!j.contains(field) || j.at(field).is_null()) return 0;
            require(aggregate_matrix, "aggregate TP requires aggregate_tp_v1 matrix");
            const auto value = read_number(j.at(field)) * Rational(2);
            for (unsigned half_percent = 4; half_percent <= 30; ++half_percent)
                if (value == Rational(static_cast<std::int64_t>(half_percent))) return half_percent;
            throw Error("aggregate TP outside approved 2..15 percent half-point matrix");
        };
        exchange_tp_half_percent = aggregate_tp("exchange_tp_pct");
        global_tp_half_percent = aggregate_tp("global_tp_pct");
        if (aggregate_matrix) {
            require(step * Rational(200) == (step * Rational(200)).rounded(Rational(1), false) &&
                    tp * Rational(200) == (tp * Rational(200)).rounded(Rational(1), false), "configuration outside approved half-point matrix");
            for (unsigned i = 0; i < 20; ++i)
                if (step * Rational(200) == Rational(static_cast<std::int64_t>(i) + 1)) { grid_index = i; break; }
        }
        encoded_id = J(id).dump(); trade_buffer.reserve(1024);
    }
    void next_buy(Pair& pair, const Source& source, const Rational& preceding) {
        const auto& rules = source.rules;
        const auto* prepared = grid_index < 20 ? source.grid_products[grid_index] : nullptr;
        pair.buy = Rational(); pair.buy_bits = 0;
        for (;;) {
            ++pair.level;
            require(pair.level <= 184, "geometric bound exceeded");
            require(factors != nullptr, "missing precomputed geometric multipliers");
            const auto& factor = factors->at(pair.level);
            auto computed = prepared ? grid_level(*prepared, pair.anchor, pair.level, grid_cache_enabled,
                grid_cache_hits, grid_cache_misses, grid_cache_bypasses) :
                std::pair<Rational, std::uint32_t>{rules.significant ? rules.round(pair.anchor * factor) :
                    units::rounded_product(pair.anchor, factor, rules.price_tick), 0};
            const auto& price = computed.first;
            if (price.sign() <= 0 || price < pair.floor) return;
            if (price == preceding) continue;
            require(price < preceding, "non-descending BUY price");
            pair.buy_bits = prepared ? computed.second : buy_cutoff(price); pair.buy = std::move(computed.first); return;
        }
    }
    void fill(Pair& pair, const Source& source, std::int64_t timestamp, bool is_buy,
              const Rational& price, const Integer& quantity_lots, const Integer& cost_per_lot,
              const char* exit_scope = nullptr) {
        const auto cost_units = cost_per_lot * quantity_lots;
        const auto fee_units = cost_units / Integer(500);
        // nlohmann's canonical map-key order and numeric/string encodings are
        // preserved, without allocating a JSON tree for every research fill.
        trade_buffer.resize(SHA256_DIGEST_LENGTH);
        const auto* cached = render_cache_enabled ? rendered_trade(source, price, quantity_lots, cost_per_lot,
            cost_units, fee_units, render_cache_hits, render_cache_misses, render_cache_bypasses) : nullptr;
        trade_buffer += "{\"config_id\":"; trade_buffer += encoded_id;
        trade_buffer += ",\"cost\":\"";
        if (cached) trade_buffer += cached->cost; else source.money_scale.append(trade_buffer, cost_units);
        if (exit_scope) { trade_buffer += "\",\"exit_scope\":\""; trade_buffer += exit_scope; }
        if (cached) trade_buffer += cached->tail;
        else {
            trade_buffer += "\",\"fee\":\""; source.money_scale.append(trade_buffer, fee_units);
            trade_buffer += "\",\"price\":\""; units::append_ratio(trade_buffer, price);
            trade_buffer += "\",\"quantity\":\""; source.quantity_scale.append(trade_buffer, quantity_lots * source.quantity_coefficient);
            trade_buffer += '"';
        }
        if (exit_scope) { trade_buffer += ",\"quote_timestamp_ms\":"; trade_buffer += std::to_string(pair.last_ms); }
        trade_buffer += ",\"sequence\":"; trade_buffer += std::to_string(pair.metrics.buys + pair.metrics.sells + 1);
        trade_buffer += is_buy ? ",\"side\":\"BUY\",\"source_id\":" : ",\"side\":\"SELL\",\"source_id\":";
        trade_buffer += source.encoded_id;
        trade_buffer += ",\"timestamp_ms\":"; trade_buffer += std::to_string(timestamp); trade_buffer += '}';
        chain(pair.digest, trade_buffer); chain(digest, trade_buffer);
        if (trace) {
            require(++traced <= trace_limit, "validation trade trace limit exceeded");
            pending_trades.push_back(J::parse(trade_buffer.begin() + SHA256_DIGEST_LENGTH, trade_buffer.end()));
        }
        pair.metrics.fees += fee_units;
        if (is_buy) {
            if (performance_enabled) performance_spent_mask |= 1U << source.venue_index;
            if (pair.quantity.sign() == 0) {
                pair.entry_ms = timestamp;
                if (require_current_quotes) { ++open_positions; ++venue_open_positions[source.venue_index]; }
            }
            pair.quantity += quantity_lots;
            pair.metrics.spent += cost_units;
            ++pair.metrics.buys;
        } else {
            if (performance_enabled) {
                const auto net = cost_units - pair.metrics.spent - fee_units - pair.metrics.spent / Integer(500);
                performance[0].closed(net * source.to_total);
                performance[source.venue_index + 1].closed(net * source.to_venue);
            }
            pair.metrics.realized += cost_units - pair.metrics.spent;
            pair.metrics.longest = std::max(pair.metrics.longest, timestamp - pair.entry_ms);
            if (require_current_quotes) {
                require(open_positions > 0 && venue_open_positions[source.venue_index] > 0,
                        "open-position counter underflow");
                --open_positions; --venue_open_positions[source.venue_index];
            }
            ++pair.metrics.sells; pair.quantity = Integer(); pair.metrics.spent = Integer();
            if (!exit_scope) {
                pair.anchor = price; pair.floor = price * floor_factor; pair.level = 0;
                next_buy(pair, source, price);
            }
            pair.sell = Rational(); pair.sell_bits = last_float + 1;
        }
    }
    void point(std::size_t source_index, const Source& source, std::uint32_t bits,
               const Integer& coefficient, std::int64_t timestamp) {
        auto& pair = pairs[source_index];
        // Most flat observations need only time metadata. This does not skip a crossing.
        const bool may_trade = !pair.started || pair.reseed || (pair.buy_bits != 0 && bits <= pair.buy_bits) ||
            (pair.quantity.sign() > 0 && bits >= pair.sell_bits);
        if (!may_trade && pair.last_bits == bits) {
            pair.last_ms = timestamp;
            if (pair.quantity.sign() > 0) pair.metrics.longest = std::max(pair.metrics.longest, timestamp - pair.entry_ms);
            total.longest = std::max(total.longest, pair.metrics.longest);
            venues[source.venue_index].longest = std::max(venues[source.venue_index].longest, pair.metrics.longest);
            return;
        }
        if (!may_trade && pair.quantity.sign() == 0) {
            pair.last_bits = bits; pair.last_ms = timestamp;
            return;
        }
        if (!may_trade) {
            const bool rising = bits > pair.last_bits;
            auto value = pair.quantity * coefficient;
            const auto change = value - pair.metrics.value;
            pair.metrics.value = std::move(value); pair.last_bits = bits; pair.last_ms = timestamp;
            pair.metrics.longest = std::max(pair.metrics.longest, timestamp - pair.entry_ms);
            pair.metrics.observe_price(timestamp, rising);
            total.value += change * source.to_total;
            auto& venue = venues[source.venue_index]; venue.value += change * source.to_venue;
            total.longest = std::max(total.longest, pair.metrics.longest);
            venue.longest = std::max(venue.longest, pair.metrics.longest);
            return;
        }
        const auto before_value = pair.metrics.value;
        const auto before_spent = may_trade ? pair.metrics.spent : Integer();
        const auto before_realized = may_trade ? pair.metrics.realized : Integer();
        const auto before_fees = may_trade ? pair.metrics.fees : Integer();
        const auto before_buys = pair.metrics.buys, before_sells = pair.metrics.sells;
        if (!pair.started || pair.reseed) {
            const auto price = Float32Price{bits}.exact();
            pair.started = true; pair.reseed = false; pair.level = 0; pair.anchor = price; pair.floor = price * floor_factor;
            fill(pair, source, timestamp, true, price, source.rules.minimum(coefficient, source.minimum_cost_units), coefficient);
            next_buy(pair, source, price);
        }
        while (pair.buy_bits != 0 && bits <= pair.buy_bits) {
            const auto executed = pair.buy;
            const auto cost_per_lot = source.money_scale.encode(executed * source.rules.amount_tick);
            fill(pair, source, timestamp, true, executed, source.rules.minimum(cost_per_lot, source.minimum_cost_units), cost_per_lot);
            next_buy(pair, source, executed);
        }
        if (pair.metrics.buys != before_buys) {
            pair.sell = source.rules.round(source.money_scale.decode(pair.metrics.spent) / pair.exact_quantity() * tp_factor, true);
            pair.sell_bits = sell_cutoff(pair.sell);
        }
        if (pair.quantity.sign() > 0 && bits >= pair.sell_bits) {
            const auto cost_per_lot = source.money_scale.encode(pair.sell * source.rules.amount_tick);
            fill(pair, source, timestamp, false, pair.sell, pair.quantity, cost_per_lot);
        }
        pair.metrics.value = pair.quantity * coefficient; pair.last_bits = bits; pair.last_ms = timestamp;
        if (pair.quantity.sign() > 0) pair.metrics.longest = std::max(pair.metrics.longest, timestamp - pair.entry_ms);
        const bool traded = pair.metrics.buys != before_buys || pair.metrics.sells != before_sells;
        if (traded) pair.metrics.update_cash_basis();
        pair.metrics.observe(timestamp);
        const auto value_change = pair.metrics.value - before_value;
        const auto spent_change = traded ? pair.metrics.spent - before_spent : Integer();
        const auto realized_change = traded ? pair.metrics.realized - before_realized : Integer();
        const auto fees_change = traded ? pair.metrics.fees - before_fees : Integer();
        const std::array<std::pair<Metrics*, const Integer*>, 2> aggregates {{
            {&total, &source.to_total}, {&venues[source.venue_index], &source.to_venue}}};
        for (auto [aggregate, factor] : aggregates) {
            if (traded) {
                aggregate->spent += spent_change * *factor;
                aggregate->realized += realized_change * *factor;
                aggregate->fees += fees_change * *factor;
                aggregate->buys += pair.metrics.buys - before_buys; aggregate->sells += pair.metrics.sells - before_sells;
                aggregate->update_cash_basis();
            }
            if (value_change.sign() != 0) aggregate->value += value_change * *factor;
            aggregate->longest = std::max(aggregate->longest, pair.metrics.longest);
        }
        if (traded && aggregate_reporting) {
            global_cycle.invalidate(); venue_cycles[source.venue_index].invalidate();
        }
    }
    void flush_chart(std::size_t index) {
        if (!chart_enabled || !charts[index].active) return;
        auto item = charts[index].save(); item["config_id"] = id; item["scope_index"] = index;
        pending_series.push_back(std::move(item)); charts[index].active = false;
        require(pending_series.size() <= 2001 * charts.size(), "chart output bound exceeded");
    }
    void chart(std::size_t index, const Metrics& metrics, std::int64_t timestamp, const Integer& pnl) {
        if (!chart_enabled) return;
        if (detail_enabled && index < performance.size()) performance[index].observe_drawdown(metrics, timestamp);
        const auto bin = (timestamp - chart_origin) / chart_width;
        auto& c = charts[index];
        if (c.active && bin != c.bin) flush_chart(index);
        const bool first = !c.active;
        const bool lower_pnl = first || pnl < c.min_pnl, higher_pnl = first || pnl > c.max_pnl;
        const bool lower_spent = first || metrics.spent < c.min_spent, higher_spent = first || metrics.spent > c.max_spent;
        if (!c.active) {
            c.active = true; c.bin = bin; c.first_ms = timestamp;
            c.first_pnl = c.min_pnl = c.max_pnl = pnl;
            c.first_spent = c.min_spent = c.max_spent = metrics.spent;
            c.pnl_min_ms = c.pnl_max_ms = c.spent_min_ms = c.spent_max_ms = timestamp;
        }
        c.last_ms = timestamp; c.last_pnl = pnl; c.last_spent = metrics.spent;
        if (pnl < c.min_pnl) { c.min_pnl = pnl; c.pnl_min_ms = timestamp; }
        if (pnl > c.max_pnl) { c.max_pnl = pnl; c.pnl_max_ms = timestamp; }
        if (metrics.spent < c.min_spent) { c.min_spent = metrics.spent; c.spent_min_ms = timestamp; }
        if (metrics.spent > c.max_spent) { c.max_spent = metrics.spent; c.spent_max_ms = timestamp; }
        if (detail_enabled) {
            const auto order = ++c.orders[1];
            if (first) c.orders[0] = order;
            if (lower_pnl) c.orders[2] = order;
            if (higher_pnl) c.orders[3] = order;
            if (lower_spent) c.orders[4] = order;
            if (higher_spent) c.orders[5] = order;
            const auto drawdown = metrics.pnl_peak - pnl;
            if (first) c.first_dd = drawdown;
            c.last_dd = drawdown;
            if (first || drawdown < c.min_dd) { c.min_dd = drawdown; c.dd_min_ms = timestamp; c.orders[6] = order; }
            if (first || drawdown > c.max_dd) { c.max_dd = drawdown; c.dd_max_ms = timestamp; c.orders[7] = order; }
        }
    }
    void close_scope(const std::vector<Source>& sources, std::int64_t timestamp, std::size_t venue_index) {
        const bool global = venue_index == venues.size();
        const auto [begin, end] = global ? std::pair<std::size_t, std::size_t>{0, pairs.size()} : venue_pair_ranges[venue_index];
        for (auto i = begin; i < end; ++i) {
            auto& pair = pairs[i]; const auto& source = sources[i];
            if (pair.quantity.sign() > 0) {
                require(pair.last_bits != 0, "aggregate exit lacks an observed price");
                require(!require_current_quotes || pair.last_ms == timestamp,
                        "aggregate exit requires a current quote for every open position");
                const auto before_spent = pair.metrics.spent, before_value = pair.metrics.value;
                const auto price = Float32Price{pair.last_bits}.exact();
                const auto coefficient = (*source.price_coefficient)(pair.last_bits);
                const auto cost = pair.quantity * coefficient, fee = cost / Integer(500);
                fill(pair, source, timestamp, false, price, pair.quantity, coefficient, global ? "global" : "venue");
                pair.metrics.value = Integer(); pair.metrics.update_cash_basis(); pair.metrics.observe(timestamp);
                const auto realized_change = cost - before_spent;
                const std::array<std::pair<Metrics*, const Integer*>, 2> aggregates {{
                    {&total, &source.to_total}, {&venues[source.venue_index], &source.to_venue}}};
                for (auto [aggregate, factor] : aggregates) {
                    aggregate->spent -= before_spent * *factor; aggregate->value -= before_value * *factor;
                    aggregate->realized += realized_change * *factor; aggregate->fees += fee * *factor;
                    aggregate->update_cash_basis();
                    ++aggregate->sells; aggregate->longest = std::max(aggregate->longest, pair.metrics.longest);
                }
                global_cycle.invalidate(); venue_cycles[source.venue_index].invalidate();
            }
            // This includes empty and not-yet-observed pairs. A later real
            // observation, never the retained mark, supplies the next seed.
            pair.reseed = true; pair.buy = Rational(); pair.sell = Rational();
            pair.buy_bits = 0; pair.sell_bits = last_float + 1;
        }
        if (global) {
            global_cycle.reset(total, timestamp);
            for (std::size_t i = 0; i < venues.size(); ++i) venue_cycles[i].reset(venues[i], timestamp);
        } else venue_cycles[venue_index].reset(venues[venue_index], timestamp);
    }
    J cycle_report() const {
        J venue_states = J::array();
        for (std::size_t i = 0; i < venues.size(); ++i) venue_states.push_back(venue_cycles[i].report(venues[i]));
        return {{"global", global_cycle.report(total)}, {"venues", std::move(venue_states)}};
    }
    void advance_performance(std::int64_t timestamp) {
        if (!performance_enabled || timestamp < next_performance_month) return;
        performance[0].advance(timestamp, total);
        for (std::size_t i = 0; i < venues.size(); ++i)
            performance[i + 1].advance(std::min(timestamp, venue_performance_ends[i]), venues[i]);
        next_performance_month = performance[0].next_month;
    }
    J performance_report(std::int64_t end, const std::vector<std::string>& venue_names) const {
        J scoped = J::array();
        for (std::size_t i = 0; i < venues.size(); ++i) {
            auto row = performance[i + 1].report(std::min(end, venue_performance_ends[i]), venues[i]);
            row["venue"] = venue_names[i]; scoped.push_back(std::move(row));
        }
        return {{"schema", "grid-monthly-performance-v1"}, {"total", performance[0].report(end, total)}, {"venues", std::move(scoped)}};
    }
    J performance_save() const {
        J rows = J::array(); for (const auto& scope : performance) rows.push_back(scope.save()); return rows;
    }
    void process(const Block& block, const std::vector<Source>& sources) {
        const auto began = Clock::now();
        for (std::size_t group = 0; group + 1 < block.group_offsets.size(); ++group) {
            const auto begin = block.group_offsets[group], end = block.group_offsets[group + 1];
            for (unsigned phase = 0; phase < 4; ++phase) {
                const auto timestamp = block.records[begin].timestamp + static_cast<std::int64_t>(phase) * 1000;
                advance_performance(timestamp);
                performance_spent_mask = 0;
                std::size_t quoted_open_positions = 0;
                std::array<std::size_t, 7> venue_quoted_open_positions {};
                for (auto row = begin; row < end; ++row) {
                    const auto& r = block.records[row]; point(r.source, sources[r.source], r.prices[phase], r.coefficients[phase], timestamp);
                    // Count during the existing event walk, after ordinary
                    // trades. EOF positions remain in open_positions but
                    // cannot manufacture a fresh quote from their retained mark.
                    if (require_current_quotes && pairs[r.source].quantity.sign() > 0) {
                        ++quoted_open_positions;
                        ++venue_quoted_open_positions[sources[r.source].venue_index];
                    }
                }
                const auto total_pnl = total.pnl();
                if (performance_enabled && performance_spent_mask != 0) {
                    performance[0].observe_spent(total, timestamp);
                    for (std::size_t venue = 0; venue < venues.size(); ++venue)
                        if ((performance_spent_mask & (1U << venue)) != 0)
                            performance[venue + 1].observe_spent(venues[venue], timestamp);
                }
                total.observe(timestamp, total_pnl); chart(0, total, timestamp, total_pnl);
                if (chart_pair_index < pairs.size() && pairs[chart_pair_index].started) {
                    const auto& selected = pairs[chart_pair_index].metrics;
                    chart(charts.size() - 1, selected, timestamp, selected.pnl());
                }
                if (!aggregate_reporting) {
                    for (std::size_t venue = 0; venue < venues.size(); ++venue) {
                        if (!chart_enabled && (block.group_venue_masks[group] & (1U << venue)) == 0) continue;
                        const auto venue_pnl = venues[venue].pnl();
                        venues[venue].observe(timestamp, venue_pnl); chart(venue + 1, venues[venue], timestamp, venue_pnl);
                    }
                    continue;
                }
                bool global_exit = false;
                std::array<bool, 7> venue_exits {};
                global_cycle.observe(total, timestamp, global_tp_half_percent);
                global_exit = (!require_current_quotes || quoted_open_positions == open_positions) &&
                    global_cycle.triggered(total, total_pnl, global_tp_half_percent);
                for (std::size_t venue = 0; venue < venues.size(); ++venue) {
                    if (!chart_enabled && (block.group_venue_masks[group] & (1U << venue)) == 0) continue;
                    const auto venue_pnl = venues[venue].pnl();
                    venues[venue].observe(timestamp, venue_pnl); chart(venue + 1, venues[venue], timestamp, venue_pnl);
                    venue_cycles[venue].observe(venues[venue], timestamp, exchange_tp_half_percent);
                    if (!global_exit && (!require_current_quotes ||
                            venue_quoted_open_positions[venue] == venue_open_positions[venue]))
                        venue_exits[venue] = venue_cycles[venue].triggered(venues[venue], venue_pnl, exchange_tp_half_percent);
                }
                bool exited = global_exit;
                if (global_exit) close_scope(sources, timestamp, venues.size());
                else for (std::size_t venue = 0; venue < venues.size(); ++venue) if (venue_exits[venue]) {
                    close_scope(sources, timestamp, venue); exited = true;
                }
                if (exited) {
                    const auto after_total_pnl = total.pnl();
                    total.observe(timestamp, after_total_pnl); chart(0, total, timestamp, after_total_pnl);
                    for (std::size_t venue = 0; venue < venues.size(); ++venue) if (global_exit || venue_exits[venue]) {
                        const auto after_venue_pnl = venues[venue].pnl();
                        venues[venue].observe(timestamp, after_venue_pnl); chart(venue + 1, venues[venue], timestamp, after_venue_pnl);
                    }
                    if (chart_pair_index < pairs.size() && pairs[chart_pair_index].started &&
                            (global_exit || venue_exits[sources[chart_pair_index].venue_index])) {
                        const auto& selected = pairs[chart_pair_index].metrics;
                        chart(charts.size() - 1, selected, timestamp, selected.pnl());
                    }
                }
            }
        }
        last_worker_seconds = std::chrono::duration<double>(Clock::now() - began).count();
        worker_seconds += last_worker_seconds;
    }
    J save() const {
        J positions = J::array(), venue_states = J::array(), chart_states = J::array();
        for (const auto& pair : pairs) positions.push_back(pair.save());
        for (const auto& venue : venues) venue_states.push_back(venue.save());
        for (const auto& item : charts) chart_states.push_back(item.save());
        J result {{"id", id}, {"pairs", std::move(positions)}, {"total", total.save()},
            {"venues", std::move(venue_states)}, {"charts", std::move(chart_states)},
            {"trade_hash", hex(digest.data(), digest.size())}, {"traced", traced}};
        if (aggregate_reporting) result["aggregate_cycles"] = cycle_report();
        if (performance_enabled) result["performance"] = performance_save();
        if (explicit_grid_depth) result["grid_depth_pct"] = number(Rational(grid_depth_pct));
        return result;
    }
    void restore(const J& j) {
        require(j.at("id") == id && j.at("pairs").size() == pairs.size() &&
                j.at("venues").size() == venues.size() && j.at("charts").size() == charts.size(), "checkpoint roster mismatch");
        require(j.contains("grid_depth_pct") == explicit_grid_depth &&
                (!explicit_grid_depth || checkpoint_number(j.at("grid_depth_pct")) == Rational(grid_depth_pct)),
                "checkpoint grid depth mismatch");
        for (std::size_t i = 0; i < pairs.size(); ++i) pairs[i].restore(j.at("pairs").at(i), floor_factor);
        // Derived counters are rebuilt from exact restored inventory, never
        // accepted as a second checkpoint authority for position ownership.
        open_positions = 0; venue_open_positions.fill(0);
        if (require_current_quotes) for (const auto& pair : pairs) if (pair.quantity.sign() > 0) {
            ++open_positions; ++venue_open_positions[pair.source->venue_index];
        }
        total.restore(j.at("total"));
        for (std::size_t i = 0; i < venues.size(); ++i) venues[i].restore(j.at("venues").at(i));
        for (std::size_t i = 0; i < charts.size(); ++i) charts[i].restore(j.at("charts").at(i));
        if (aggregate_reporting) {
            const auto& cycles = j.at("aggregate_cycles");
            require(cycles.at("venues").size() == venues.size(), "checkpoint cycle scope mismatch");
            global_cycle.restore(cycles.at("global"), total, global_tp_half_percent);
            for (std::size_t i = 0; i < venues.size(); ++i)
                venue_cycles[i].restore(cycles.at("venues").at(i), venues[i], exchange_tp_half_percent);
        }
        if (performance_enabled) {
            require(j.at("performance").size() == performance.size(), "checkpoint performance scope mismatch");
            performance[0].restore(j.at("performance").at(0), total);
            for (std::size_t i = 0; i < venues.size(); ++i) performance[i + 1].restore(j.at("performance").at(i + 1), venues[i]);
            next_performance_month = performance[0].next_month;
            require((performance[0].months.empty() ? performance[0].current.start_ms : performance[0].months.front().start_ms)
                    == chart_origin, "checkpoint portfolio calendar mismatch");
            for (std::size_t i = 0; i < venues.size(); ++i)
                require((performance[i + 1].months.empty() ? performance[i + 1].current.start_ms :
                         performance[i + 1].months.front().start_ms) == venue_performance_starts[i] &&
                        performance[i + 1].current.start_ms <= venue_performance_ends[i] &&
                        (require_current_quotes || performance[i + 1].next_month == next_performance_month),
                        "checkpoint venue calendar mismatch");
        }
        digest = parse_digest(j.at("trade_hash")); traced = j.at("traced");
    }
};

struct ReaderTiming {
    double read_seconds = 0;
    std::uint64_t bytes = 0, refills = 0, seek_probes = 0, decoded_records = 0, prepared_prices = 0, reused_prices = 0;
    J report() const {
        return {{"read_seconds", read_seconds}, {"read_bytes", bytes}, {"buffer_refills", refills},
                {"timestamp_seek_probes", seek_probes}, {"decoded_records", decoded_records},
                {"prepared_prices", prepared_prices}, {"reused_prices", reused_prices}};
    }
};
struct Cursor {
    Source* source;
    ReaderTiming* timing;
    std::size_t buffer_records;
    std::uint64_t position = 0, buffer_start = 0;
    std::uint64_t head_position = std::numeric_limits<std::uint64_t>::max();
    Bar head{};
    std::uint32_t last_price_bits = 0;
    Integer last_coefficient;
    std::vector<std::byte> buffer;
    explicit Cursor(Source& value, std::size_t capacity, ReaderTiming& stats) : source(&value), timing(&stats), buffer_records(capacity) {}
    void seek(std::int64_t timestamp) {
        const auto began = Clock::now();
        source->check_identity();
        File file(::open(source->path.c_str(), O_RDONLY | O_CLOEXEC | O_NOFOLLOW));
        struct stat identity {};
        require(file.fd >= 0 && ::fstat(file.fd, &identity) == 0 && same_identity(source->identity, identity), "source seek identity changed");
        std::uint64_t lower = 0, upper = source->records;
        while (lower < upper) {
            const auto middle = lower + (upper - lower) / 2;
            std::array<unsigned char, 8> bytes{};
            std::size_t got = 0;
            while (got < bytes.size()) {
                const auto amount = ::pread(file.fd, bytes.data() + got, bytes.size() - got,
                    static_cast<off_t>(middle * record_bytes + got));
                if (amount < 0 && errno == EINTR) continue;
                require(amount > 0, "short timestamp seek read"); got += static_cast<std::size_t>(amount);
            }
            std::uint64_t value = 0;
            ++timing->seek_probes; timing->bytes += bytes.size();
            for (unsigned i = 0; i < bytes.size(); ++i) value |= static_cast<std::uint64_t>(bytes[i]) << (i * 8U);
            require(value <= static_cast<std::uint64_t>(max_timestamp_ms), "invalid source seek timestamp");
            if (value < static_cast<std::uint64_t>(timestamp)) lower = middle + 1; else upper = middle;
        }
        require(::fstat(file.fd, &identity) == 0 && same_identity(source->identity, identity), "source changed during seek");
        source->check_identity(); position = lower;
        timing->read_seconds += std::chrono::duration<double>(Clock::now() - began).count();
    }
    const Bar& peek() {
        require(position < source->records, "source cursor past end");
        if (head_position == position) return head;
        if (buffer.empty() || position < buffer_start || position >= buffer_start + buffer.size() / record_bytes) {
            const auto began = Clock::now();
            source->check_identity();
            File file(::open(source->path.c_str(), O_RDONLY | O_CLOEXEC | O_NOFOLLOW));
            struct stat identity {};
            require(file.fd >= 0 && ::fstat(file.fd, &identity) == 0 && same_identity(source->identity, identity), "cannot reopen unchanged source");
            const auto records = std::min<std::uint64_t>(buffer_records, source->records - position);
            buffer.resize(static_cast<std::size_t>(records) * record_bytes); buffer_start = position;
            std::size_t got = 0;
            while (got < buffer.size()) {
                const auto n = ::pread(file.fd, buffer.data() + got, buffer.size() - got,
                    static_cast<off_t>(position * record_bytes + got));
                if (n < 0 && errno == EINTR) continue;
                require(n > 0, "short source replay read"); got += static_cast<std::size_t>(n);
            }
            timing->bytes += got; ++timing->refills;
            timing->read_seconds += std::chrono::duration<double>(Clock::now() - began).count();
        }
        head = decode(std::span(buffer).subspan(static_cast<std::size_t>(position - buffer_start) * record_bytes, record_bytes));
        ++timing->decoded_records;
        head_position = position; return head;
    }
};
struct Merger {
    using Head = std::pair<std::int64_t, std::size_t>;
    ReaderTiming timing;
    std::vector<Cursor> cursors;
    std::priority_queue<Head, std::vector<Head>, std::greater<Head>> heap;
    std::int64_t start, end;
    std::uint64_t max_records, generated = 0;
    std::size_t block_bytes;
    unsigned max_groups;
    std::int64_t warmup_until;
    Merger(std::vector<Source>& sources, const std::vector<std::uint64_t>& positions,
           std::int64_t from, std::int64_t until, std::uint64_t cap, std::size_t bytes,
           unsigned groups, std::int64_t warmup, std::size_t reader_records)
      : start(from), end(until), max_records(cap), block_bytes(bytes), max_groups(groups), warmup_until(warmup) {
        cursors.reserve(sources.size());
        for (std::size_t i = 0; i < sources.size(); ++i) {
            cursors.emplace_back(sources[i], reader_records, timing); auto& cursor = cursors.back();
            if (!positions.empty()) cursor.position = positions[i];
            else if (start > sources[i].first_ms) cursor.seek(start);
            if (cursor.position < sources[i].records) {
                const auto timestamp = cursor.peek().timestamp;
                if (timestamp < end) heap.emplace(timestamp, i);
            }
        }
    }
    bool next(Block& output) {
        output.records.clear(); output.group_offsets.clear(); output.group_venue_masks.clear(); output.filled = false;
        output.coefficient_heap_estimate = 0;
        unsigned groups = 0;
        while (!heap.empty()) {
            const auto timestamp = heap.top().first;
            if (!output.records.empty()) {
                output.filled = output.decoded_bytes() >= block_bytes;
                if (output.filled || groups >= max_groups ||
                    (output.records.front().timestamp < warmup_until && timestamp >= warmup_until)) break;
            }
            std::vector<std::size_t> members;
            while (!heap.empty() && heap.top().first == timestamp) { members.push_back(heap.top().second); heap.pop(); }
            if (max_records != 0 && generated + members.size() > max_records) {
                for (auto index : members) heap.emplace(timestamp, index);
                break;
            }
            ++groups;
            output.group_offsets.push_back(output.records.size());
            unsigned venue_mask = 0;
            for (auto index : members) {
                auto& cursor = cursors[index]; const auto& bar = cursor.peek();
                venue_mask |= 1U << cursor.source->venue_index;
                Record row; row.timestamp = bar.timestamp; row.source = static_cast<std::uint32_t>(index);
                for (std::size_t phase = 0; phase < 4; ++phase) {
                    row.prices[phase] = bar.prices[phase].bits;
                    if (row.prices[phase] == cursor.last_price_bits) {
                        row.coefficients[phase] = cursor.last_coefficient; ++timing.reused_prices;
                    } else if (phase > 0 && row.prices[phase] == row.prices[0]) {
                        row.coefficients[phase] = row.coefficients[0]; ++timing.reused_prices;
                    } else {
                        row.coefficients[phase] = (*cursor.source->price_coefficient)(row.prices[phase]); ++timing.prepared_prices;
                    }
                    cursor.last_price_bits = row.prices[phase]; cursor.last_coefficient = row.coefficients[phase];
                    output.coefficient_heap_estimate += row.coefficients[phase].storage_bytes_estimate();
                }
                output.records.push_back(std::move(row)); ++cursor.position; ++generated;
                if (cursor.position < cursor.source->records) {
                    const auto following = cursor.peek().timestamp;
                    require(following > timestamp, "source ordering changed during replay");
                    if (following < end) heap.emplace(following, index);
                }
            }
            output.group_venue_masks.push_back(venue_mask);
        }
        output.positions.clear();
        for (const auto& cursor : cursors) output.positions.push_back(cursor.position);
        output.group_offsets.push_back(output.records.size());
        output.filled = output.decoded_bytes() >= block_bytes;
        return !output.records.empty();
    }
};

struct WorkerTiming {
    double wall_seconds = 0, worker_wall_seconds = 0, worker_cpu_seconds = 0, tail_wait_seconds = 0;
    void add(const WorkerTiming& other) {
        wall_seconds += other.wall_seconds; worker_wall_seconds += other.worker_wall_seconds;
        worker_cpu_seconds += other.worker_cpu_seconds; tail_wait_seconds += other.tail_wait_seconds;
    }
};
class Workers {
    std::vector<std::thread> threads_;
    mutable std::mutex mutex_;
    std::condition_variable ready_, finished_;
    std::vector<Configuration>& configurations_;
    const std::vector<Source>& sources_;
    std::function<void(std::size_t)> job_;
    std::function<void(std::exception_ptr)> on_failure_;
    std::vector<std::size_t> order_;
    std::vector<double> completion_seconds_;
    std::atomic<std::size_t> next_{0};
    std::atomic<bool> failed_{false};
    std::uint64_t generation_ = 0;
    std::size_t active_ = 0;
    bool stopping_ = false, dynamic_ = true, job_dynamic_ = true, strategy_job_ = false;
    std::exception_ptr error_;
    Clock::time_point submitted_;
    WorkerTiming last_timing_, strategy_timing_;

    static double cpu_seconds() {
        struct rusage usage {};
        require(::getrusage(RUSAGE_THREAD, &usage) == 0, "worker CPU measurement failed");
        return static_cast<double>(usage.ru_utime.tv_sec) + static_cast<double>(usage.ru_stime.tv_sec) +
            (static_cast<double>(usage.ru_utime.tv_usec) + static_cast<double>(usage.ru_stime.tv_usec)) / 1000000.;
    }
    void fail(std::exception_ptr error) {
        std::lock_guard lock(mutex_);
        if (!error_) error_ = error;
        failed_.store(true, std::memory_order_relaxed);
    }
    void dispatch(std::vector<std::size_t> order, std::function<void(std::size_t)> job, bool dynamic, bool strategy,
                  std::function<void(std::exception_ptr)> on_failure = {}) {
        std::lock_guard lock(mutex_);
        require(active_ == 0 && !stopping_, "worker job overlap");
        order_ = std::move(order); job_ = std::move(job); on_failure_ = std::move(on_failure);
        job_dynamic_ = dynamic; strategy_job_ = strategy;
        next_.store(0, std::memory_order_relaxed); failed_.store(false, std::memory_order_relaxed);
        error_ = nullptr; last_timing_ = {}; submitted_ = Clock::now();
        active_ = threads_.size(); ++generation_; ready_.notify_all();
    }
public:
    Workers(unsigned count, std::vector<Configuration>& configurations, const std::vector<Source>& sources,
            bool dynamic = true)
      : configurations_(configurations), sources_(sources), completion_seconds_(count), dynamic_(dynamic) {
        require(count > 0 && count <= 64, "research worker count outside 1..64");
        threads_.reserve(count);
        try {
            for (unsigned worker = 0; worker < count; ++worker) threads_.emplace_back([this, worker, count] {
                std::uint64_t generation = 0;
                for (;;) {
                    { std::unique_lock lock(mutex_); ready_.wait(lock, [&] { return stopping_ || generation_ != generation; });
                      if (stopping_) return;
                      generation = generation_; }
                    const auto began = Clock::now();
                    double cpu_begin = 0, cpu_used = 0;
                    try {
                        cpu_begin = cpu_seconds();
                        if (job_dynamic_) {
                            while (!failed_.load(std::memory_order_relaxed)) {
                                const auto index = next_.fetch_add(1, std::memory_order_relaxed);
                                if (index >= order_.size()) break;
                                job_(order_[index]);
                            }
                        } else {
                            for (std::size_t index = worker; index < order_.size() && !failed_.load(std::memory_order_relaxed); index += count)
                                job_(order_[index]);
                        }
                        cpu_used = cpu_seconds() - cpu_begin;
                    } catch (...) {
                        const auto error = std::current_exception();
                        if (on_failure_) { try { on_failure_(error); } catch (...) {} }
                        fail(error);
                    }
                    const auto done = Clock::now();
                    { std::lock_guard lock(mutex_);
                      completion_seconds_[worker] = std::chrono::duration<double>(done - submitted_).count();
                      last_timing_.worker_wall_seconds += std::chrono::duration<double>(done - began).count();
                      last_timing_.worker_cpu_seconds += cpu_used;
                      --active_;
                      if (active_ == 0) {
                          last_timing_.wall_seconds = *std::max_element(completion_seconds_.begin(), completion_seconds_.end());
                          for (const auto ended : completion_seconds_)
                              last_timing_.tail_wait_seconds += std::max(0., last_timing_.wall_seconds - ended);
                          if (strategy_job_) strategy_timing_.add(last_timing_);
                          finished_.notify_one();
                      } }
                }
            });
        } catch (...) {
            { std::lock_guard lock(mutex_); stopping_ = true; }
            ready_.notify_all(); for (auto& worker : threads_) worker.join(); throw;
        }
    }
    Workers(const Workers&) = delete;
    ~Workers() {
        { std::lock_guard lock(mutex_); stopping_ = true; } ready_.notify_all();
        for (auto& worker : threads_) worker.join();
    }
    [[nodiscard]] std::size_t size() const noexcept { return threads_.size(); }
    [[nodiscard]] WorkerTiming last_timing() const { std::lock_guard lock(mutex_); return last_timing_; }
    [[nodiscard]] WorkerTiming strategy_timing() const { std::lock_guard lock(mutex_); return strategy_timing_; }
    void submit(const Block& block) {
        { std::lock_guard lock(mutex_); require(active_ == 0 && !stopping_, "worker job overlap"); }
        std::vector<std::size_t> order(configurations_.size());
        for (std::size_t i = 0; i < order.size(); ++i) order[i] = i;
        if (dynamic_) std::stable_sort(order.begin(), order.end(), [&](auto a, auto b) {
            return configurations_[a].last_worker_seconds > configurations_[b].last_worker_seconds;
        });
        dispatch(std::move(order), [this, &block](std::size_t i) { configurations_[i].process(block, sources_); }, dynamic_, true);
    }
    void submit_jobs(std::size_t count, std::function<void(std::size_t)> job,
                     std::function<void(std::exception_ptr)> on_failure = {}) {
        std::vector<std::size_t> order(count);
        for (std::size_t i = 0; i < count; ++i) order[i] = i;
        dispatch(std::move(order), std::move(job), true, false, std::move(on_failure));
    }
    void wait(const std::function<void()>& pulse = {}) {
        std::unique_lock lock(mutex_);
        std::exception_ptr pulse_error;
        while (active_ != 0) {
            if (!pulse) finished_.wait(lock, [&] { return active_ == 0; });
            else if (!finished_.wait_for(lock, std::chrono::seconds(1), [&] { return active_ == 0; })) {
                lock.unlock();
                try { pulse(); }
                catch (...) { pulse_error = std::current_exception(); failed_.store(true, std::memory_order_relaxed); }
                lock.lock();
            }
        }
        job_ = {}; on_failure_ = {};
        if (pulse_error) std::rethrow_exception(pulse_error);
        if (error_) std::rethrow_exception(error_);
    }
};

struct Limits {
    std::uint64_t rss = 0, available = 0, disk = 0, output = 0;
};
std::uint64_t rss_bytes() {
    std::ifstream in("/proc/self/statm"); std::uint64_t total = 0, resident = 0;
    require(bool(in >> total >> resident), "cannot inspect process RSS");
    return resident * static_cast<std::uint64_t>(::sysconf(_SC_PAGESIZE));
}
std::uint64_t available_bytes() {
    std::ifstream in("/proc/meminfo"); std::string line;
    while (std::getline(in, line)) if (line.starts_with("MemAvailable:")) {
        std::istringstream values(line.substr(13)); std::uint64_t amount = 0;
        require(bool(values >> amount), "cannot inspect available memory"); return amount * 1024;
    }
    throw Error("available memory unavailable");
}
std::uint64_t output_bytes(const fs::path& output, bool recursive = false) {
    std::uint64_t bytes = 0;
    if (recursive) {
        for (const auto& entry : fs::recursive_directory_iterator(output)) if (entry.is_regular_file()) bytes += entry.file_size();
    } else for (const auto& entry : fs::directory_iterator(output)) if (entry.is_regular_file()) bytes += entry.file_size();
    return bytes;
}
void resources(const Limits& limits, const fs::path& output, bool recursive = false) {
    if (limits.rss) require(rss_bytes() <= limits.rss, "research RSS limit exceeded");
    if (limits.available) require(available_bytes() >= limits.available, "host available-memory floor reached");
    if (limits.disk) {
        struct statvfs stats {}; require(::statvfs(output.c_str(), &stats) == 0, "disk capacity unavailable");
        require(static_cast<std::uint64_t>(stats.f_bavail) * stats.f_frsize >= limits.disk, "free-disk floor reached");
    }
    if (limits.output) require(output_bytes(output, recursive) <= limits.output, "research output limit exceeded");
}
std::string executable_hash() {
    std::ifstream input("/proc/self/exe", std::ios::binary); require(bool(input), "cannot bind research executable");
    std::string all((std::istreambuf_iterator<char>(input)), std::istreambuf_iterator<char>());
    require(all.size() <= 256U * 1024U * 1024U, "research executable size bound exceeded"); return sha(all);
}
std::string scientific_hash(J plan) {
    for (const auto* key : {"output_dir", "threads", "block_bytes", "checkpoint_seconds", "resume", "stop_after_blocks",
                            "runtime_limits", "duration_seconds", "scheduler", "max_timestamp_groups",
                            "reader_buffer_records", "audit_threads", "progress_interval_ms", "keep_results", "summary_stdout", "trade_render_cache", "grid_level_cache"}) plan.erase(key);
    return sha(plan.dump());
}
} // namespace

std::uint32_t buy_cutoff(const Rational& limit) {
    return units::strict_float32_cutoff(limit, true);
}
std::uint32_t sell_cutoff(const Rational& limit) {
    return units::strict_float32_cutoff(limit, false);
}

namespace {
struct SignalGuard {
    using Handler = void (*)(int);
    Handler interrupt_handler, terminate_handler;
    SignalGuard() : interrupt_handler(std::signal(SIGINT, signal_stop)),
                    terminate_handler(std::signal(SIGTERM, signal_stop)) { interrupted = 0; }
    ~SignalGuard() { std::signal(SIGINT, interrupt_handler); std::signal(SIGTERM, terminate_handler); }
};
struct StreamHash {
    std::unique_ptr<EVP_MD_CTX, decltype(&EVP_MD_CTX_free)> context {EVP_MD_CTX_new(), EVP_MD_CTX_free};
    std::uint64_t bytes = 0;
    StreamHash() { require(context && EVP_DigestInit_ex(context.get(), EVP_sha256(), nullptr) == 1, "checkpoint hash init failed"); }
    void add(std::string_view value) {
        require(value.size() <= std::numeric_limits<std::uint64_t>::max() - bytes, "output hash byte count overflow");
        require(EVP_DigestUpdate(context.get(), value.data(), value.size()) == 1, "checkpoint hash update failed");
        bytes += value.size();
    }
    std::string finish() const {
        std::unique_ptr<EVP_MD_CTX, decltype(&EVP_MD_CTX_free)> copy(EVP_MD_CTX_new(), EVP_MD_CTX_free);
        require(copy && EVP_MD_CTX_copy_ex(copy.get(), context.get()) == 1, "checkpoint hash copy failed");
        Digest digest {}; unsigned length = 0;
        require(EVP_DigestFinal_ex(copy.get(), digest.data(), &length) == 1 && length == digest.size(), "checkpoint hash failed");
        return hex(digest.data(), digest.size());
    }
};
std::uint64_t offset(int fd) {
    const auto result = ::lseek(fd, 0, SEEK_CUR); require(result >= 0, "output offset unavailable");
    return static_cast<std::uint64_t>(result);
}
std::string prefix_hash(int fd, std::uint64_t size, StreamHash* seed = nullptr) {
    StreamHash hash;
    std::array<char, 65536> buffer {};
    std::uint64_t position = 0;
    while (position < size) {
        const auto count = static_cast<std::size_t>(std::min<std::uint64_t>(buffer.size(), size - position));
        const auto got = ::pread(fd, buffer.data(), count, static_cast<off_t>(position));
        if (got < 0 && errno == EINTR) continue;
        require(got > 0, "checkpoint committed output prefix is missing");
        hash.add(std::string_view(buffer.data(), static_cast<std::size_t>(got))); position += static_cast<std::uint64_t>(got);
    }
    const auto result = hash.finish();
    if (seed) *seed = std::move(hash);
    return result;
}
void restore_output(int fd, std::uint64_t size, const std::string& expected = {}, StreamHash* seed = nullptr) {
    struct stat stats {};
    require(::fstat(fd, &stats) == 0 && S_ISREG(stats.st_mode) && stats.st_size >= 0 &&
            size <= static_cast<std::uint64_t>(stats.st_size) && size <= static_cast<std::uint64_t>(std::numeric_limits<off_t>::max()),
            "checkpoint output is missing or shorter than committed offset");
    const auto observed = prefix_hash(fd, size, seed);
    require(expected.empty() || observed == expected, "checkpoint committed output prefix hash mismatch");
    require(::ftruncate(fd, static_cast<off_t>(size)) == 0 && ::lseek(fd, static_cast<off_t>(size), SEEK_SET) >= 0 &&
            ::fsync(fd) == 0, "cannot restore committed output offset");
}
struct CheckpointTiming;
std::string framed_msgpack(const J& row) {
    const auto bytes = J::to_msgpack(row);
    require(!bytes.empty() && bytes.size() <= 16U * 1024U * 1024U, "detail frame exceeds bound");
    const std::string payload(reinterpret_cast<const char*>(bytes.data()), bytes.size());
    const auto digest = parse_digest(sha(payload));
    std::string frame(8, '\0');
    for (std::size_t i = 0; i < 8; ++i)
        frame[i] = static_cast<char>((static_cast<std::uint64_t>(bytes.size()) >> (8U * i)) & 255U);
    frame.append(reinterpret_cast<const char*>(digest.data()), digest.size()); frame.append(payload);
    return frame;
}
CheckpointTiming append_pending(std::vector<Configuration>& configurations, int series, int trades,
    const std::vector<std::string>& venues, Workers& workers, StreamHash* series_hash = nullptr, StreamHash* trades_hash = nullptr);
struct CheckpointTiming {
    double preparation_wall_seconds = 0, preparation_worker_wall_seconds = 0, preparation_worker_cpu_seconds = 0;
    double serialization_wait_seconds = 0, write_seconds = 0, fsync_seconds = 0;
    std::uint64_t buffer_budget_bytes = 0;
    void add(const CheckpointTiming& other) {
        preparation_wall_seconds += other.preparation_wall_seconds;
        preparation_worker_wall_seconds += other.preparation_worker_wall_seconds;
        preparation_worker_cpu_seconds += other.preparation_worker_cpu_seconds;
        serialization_wait_seconds += other.serialization_wait_seconds; write_seconds += other.write_seconds;
        fsync_seconds += other.fsync_seconds; buffer_budget_bytes = std::max(buffer_budget_bytes, other.buffer_budget_bytes);
    }
};
// Match Configuration::save().dump() exactly, including lexicographic key order,
// while retaining only one pair/chart/venue JSON object per producer at a time.
void stream_configuration(const Configuration& config, const std::function<void(std::string_view)>& append) {
    const auto item = [&](const J& value) {
        const auto encoded = value.dump();
        require(encoded.size() <= 8U * 1024U * 1024U, "checkpoint state item exceeds serialization bound");
        append(encoded);
    };
    append("{");
    if (config.aggregate_reporting) { append("\"aggregate_cycles\":"); item(config.cycle_report()); append(","); }
    append("\"charts\":[");
    for (std::size_t i = 0; i < config.charts.size(); ++i) { if (i) append(","); item(config.charts[i].save()); }
    append("]");
    if (config.explicit_grid_depth) { append(",\"grid_depth_pct\":"); item(number(Rational(config.grid_depth_pct))); }
    append(",\"id\":"); item(config.id); append(",\"pairs\":[");
    for (std::size_t i = 0; i < config.pairs.size(); ++i) { if (i) append(","); item(config.pairs[i].save()); }
    append("]");
    if (config.performance_enabled) { append(",\"performance\":"); item(config.performance_save()); }
    append(",\"total\":"); item(config.total.save());
    append(",\"traced\":"); item(config.traced);
    append(",\"trade_hash\":"); item(hex(config.digest.data(), config.digest.size()));
    append(",\"venues\":[");
    for (std::size_t i = 0; i < config.venues.size(); ++i) { if (i) append(","); item(config.venues[i].save()); }
    append("]}");
}
using ChunkSink = std::function<void(std::string_view)>;
CheckpointTiming parallel_stream(std::size_t item_count, Workers& workers,
        const std::function<void(std::size_t, const ChunkSink&)>& produce,
        const ChunkSink& emit, const std::function<void(std::size_t, bool)>& boundary = {},
        std::size_t maximum_producers = 32, std::size_t scratch_bytes = 16U * 1024U * 1024U) {
    CheckpointTiming timing;
    const auto seconds = [](Clock::time_point began) { return std::chrono::duration<double>(Clock::now() - began).count(); };
    constexpr std::size_t chunk_bytes = 1024U * 1024U;
    constexpr std::size_t queued_chunks = 2;
    // Fixed scalar shapes + 131072-bit numeric bound keep one serialized item
    // and its temporary JSON/string storage below the 16 MiB scratch allowance.
    // 32 producers * (2 queued + 1 building + 16 scratch) MiB + writer < 1 GiB.
    const auto batch_capacity = std::min(maximum_producers, workers.size());
    require(batch_capacity > 0, "empty serialization pool");
    timing.buffer_budget_bytes = batch_capacity * ((queued_chunks + 1) * chunk_bytes + scratch_bytes) + chunk_bytes;
    require(timing.buffer_budget_bytes <= 1024U * 1024U * 1024U, "checkpoint serialization budget exceeded");
    struct Channel {
        std::mutex mutex;
        std::condition_variable changed;
        std::queue<std::string> chunks;
        bool done = false;
    };
    for (std::size_t batch = 0; batch < item_count; batch += batch_capacity) {
        const auto count = std::min(batch_capacity, item_count - batch);
        auto channels = std::make_unique<Channel[]>(count);
        std::atomic<bool> aborted{false};
        std::mutex error_mutex;
        std::exception_ptr preparation_error;
        const auto cancel = [&](std::exception_ptr error) {
            { std::lock_guard lock(error_mutex); if (!preparation_error) preparation_error = error; }
            aborted.store(true);
            // Pair each wakeup with the channel mutex: otherwise cancellation
            // between predicate inspection and wait registration can be lost.
            for (std::size_t i = 0; i < count; ++i) {
                { std::lock_guard lock(channels[i].mutex); }
                channels[i].changed.notify_all();
            }
        };
        const auto producer = [&](std::size_t index) {
            auto& channel = channels[index];
            std::string chunk; chunk.reserve(chunk_bytes);
            const auto flush = [&] {
                if (chunk.empty()) return;
                std::unique_lock lock(channel.mutex);
                channel.changed.wait(lock, [&] { return aborted.load() || channel.chunks.size() < queued_chunks; });
                require(!aborted.load(), "checkpoint serialization canceled");
                channel.chunks.push(std::move(chunk)); lock.unlock(); channel.changed.notify_all();
                chunk.clear(); chunk.reserve(chunk_bytes);
            };
            const auto append = [&](std::string_view value) {
                while (!value.empty()) {
                    require(!aborted.load(), "checkpoint serialization canceled");
                    const auto take = std::min(chunk_bytes - chunk.size(), value.size());
                    chunk.append(value.data(), take); value.remove_prefix(take);
                    if (chunk.size() == chunk_bytes) flush();
                }
            };
            produce(batch + index, append); flush();
            { std::lock_guard lock(channel.mutex); channel.done = true; } channel.changed.notify_all();
        };
        workers.submit_jobs(count, producer, cancel);
        try {
            for (std::size_t index = 0; index < count; ++index) {
                auto& channel = channels[index];
                if (boundary) boundary(batch + index, true);
                for (;;) {
                    const auto waiting = Clock::now();
                    std::string chunk;
                    { std::unique_lock lock(channel.mutex);
                      channel.changed.wait(lock, [&] { return aborted.load() || channel.done || !channel.chunks.empty(); });
                      timing.serialization_wait_seconds += seconds(waiting);
                      if (aborted.load()) {
                          std::lock_guard error_lock(error_mutex);
                          if (preparation_error) std::rethrow_exception(preparation_error);
                          throw Error("checkpoint serialization canceled");
                      }
                      if (channel.chunks.empty()) { require(channel.done, "checkpoint producer ended without final chunk"); break; }
                      chunk = std::move(channel.chunks.front()); channel.chunks.pop(); }
                    channel.changed.notify_all(); emit(chunk);
                }
                if (boundary) boundary(batch + index, false);
            }
            workers.wait();
        } catch (...) {
            const auto error = std::current_exception(); cancel(error);
            try { workers.wait(); } catch (...) {}
            std::rethrow_exception(error);
        }
        const auto prepared = workers.last_timing();
        timing.preparation_wall_seconds += prepared.wall_seconds;
        timing.preparation_worker_wall_seconds += prepared.worker_wall_seconds;
        timing.preparation_worker_cpu_seconds += prepared.worker_cpu_seconds;
    }
    return timing;
}
CheckpointTiming append_pending(std::vector<Configuration>& configurations, int series, int trades,
        const std::vector<std::string>& venues, Workers& workers, StreamHash* series_hash, StreamHash* trades_hash) {
    std::uint64_t traced = 0;
    bool have_series = false, have_trades = false;
    for (const auto& config : configurations) {
        traced += config.traced;
        require(traced <= trace_limit, "validation total trade trace limit exceeded");
        have_series = have_series || !config.pending_series.empty();
        have_trades = have_trades || !config.pending_trades.empty();
    }
    CheckpointTiming timing;
    const auto seconds = [](Clock::time_point began) { return std::chrono::duration<double>(Clock::now() - began).count(); };
    if (have_series) timing.add(parallel_stream(configurations.size(), workers, [&](std::size_t index, const ChunkSink& append) {
        for (auto& row : configurations[index].pending_series) {
            const auto scope = row.at("scope_index").get<std::size_t>();
            if (scope == 0) row["scope"] = "all";
            else if (scope <= venues.size()) row["scope"] = "venue:" + venues.at(scope - 1);
            else {
                const auto& config = configurations[index];
                require(scope == venues.size() + 1 && config.chart_pair_index < config.pairs.size(), "invalid pair chart scope");
                row["scope"] = "pair:" + config.pairs[config.chart_pair_index].source->id;
            }
            const auto encoded = configurations[index].detail_enabled ? framed_msgpack(row) : row.dump() + "\n";
            require(encoded.size() <= 8U * 1024U * 1024U, "series row exceeds serialization bound");
            append(encoded);
        }
    }, [&](std::string_view chunk) {
        const auto began = Clock::now(); write_all(series, chunk); if (series_hash) series_hash->add(chunk);
        timing.write_seconds += seconds(began);
    }));
    if (have_trades) timing.add(parallel_stream(configurations.size(), workers, [&](std::size_t index, const ChunkSink& append) {
        for (const auto& row : configurations[index].pending_trades) { append(row.dump()); append("\n"); }
    }, [&](std::string_view chunk) {
        const auto began = Clock::now(); write_all(trades, chunk); if (trades_hash) trades_hash->add(chunk);
        timing.write_seconds += seconds(began);
    }));
    for (auto& config : configurations) { config.pending_series.clear(); config.pending_trades.clear(); }
    return timing;
}
CheckpointTiming checkpoint(const fs::path& output, J header, const std::vector<Configuration>& configurations,
                            int series, int trades, Workers& workers, const StreamHash& series_hash,
                            const StreamHash& trades_hash, const std::function<void()>& check_resources = {}) {
    CheckpointTiming timing;
    const auto seconds = [](Clock::time_point began) { return std::chrono::duration<double>(Clock::now() - began).count(); };
    auto began = Clock::now();
    require(::fsync(series) == 0 && (trades < 0 || ::fsync(trades) == 0), "checkpoint outputs fsync failed");
    timing.fsync_seconds += seconds(began);
    if (check_resources) check_resources();
    header["series_offset"] = offset(series); header["trace_offset"] = trades < 0 ? 0 : offset(trades);
    require(series_hash.bytes == header.at("series_offset").get<std::uint64_t>() &&
            (trades < 0 || trades_hash.bytes == header.at("trace_offset").get<std::uint64_t>()),
            "checkpoint incremental output hash offset mismatch");
    header["trace_enabled"] = trades >= 0;
    header["series_sha256"] = series_hash.finish();
    header["trace_sha256"] = trades < 0 ? sha("") : trades_hash.finish();
    const auto temporary = output / "checkpoint.tmp";
    File file(::open(temporary.c_str(), O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC | O_NOFOLLOW, 0600));
    require(file.fd >= 0, "cannot create checkpoint");
    StreamHash hash;
    std::uint64_t checked_at = 0;
    const auto emit = [&](std::string_view value) {
        const auto write_begin = Clock::now();
        write_all(file.fd, value); hash.add(value);
        timing.write_seconds += seconds(write_begin);
        if (check_resources && hash.bytes - checked_at >= 64U * 1024U * 1024U) {
            check_resources(); checked_at = hash.bytes;
        }
    };
    auto first = header.dump(); first.pop_back(); first += ",\"configurations\":[\n"; emit(first);
    timing.add(parallel_stream(configurations.size(), workers, [&](std::size_t index, const ChunkSink& append) {
        std::size_t bytes = 0;
        stream_configuration(configurations[index], [&](std::string_view value) {
            require(value.size() <= 256U * 1024U * 1024U - bytes, "checkpoint configuration exceeds recovery size bound");
            bytes += value.size(); append(value);
        });
        if (index + 1 < configurations.size()) append(",");
        append("\n");
    }, emit));
    const auto footer = "],\"payload_sha256\":\"" + hash.finish() + "\"}\n";
    began = Clock::now(); write_all(file.fd, footer); timing.write_seconds += seconds(began);
    began = Clock::now(); require(::fsync(file.fd) == 0, "checkpoint fsync failed"); timing.fsync_seconds += seconds(began);
    if (check_resources) check_resources();
    const auto latest = output / "checkpoint.latest.json", previous = output / "checkpoint.previous.json";
    if (fs::exists(latest)) require(::rename(latest.c_str(), previous.c_str()) == 0, "checkpoint rotation failed");
    require(::rename(temporary.c_str(), latest.c_str()) == 0, "checkpoint publication failed");
    began = Clock::now(); sync_directory(output); timing.fsync_seconds += seconds(began);
    return timing;
}

J load_checkpoint(const fs::path& path, const std::string& plan_hash, const std::string& build_hash,
                  std::vector<Configuration>& configurations, const std::vector<Source>& sources,
                  Workers& workers, const std::function<void()>& pulse) {
    std::ifstream input(path); require(bool(input), "checkpoint unavailable");
    std::string line; require(bool(std::getline(input, line)), "checkpoint header missing");
    const std::string suffix = ",\"configurations\":[";
    require(line.size() < maximum_plan_bytes && line.ends_with(suffix), "invalid checkpoint header");
    StreamHash hash; hash.add(line + "\n");
    const auto header = J::parse(line.substr(0, line.size() - suffix.size()) + "}");
    require(header.at("schema") == "grid-research-sweep-checkpoint-v1" && header.at("plan_sha256") == plan_hash &&
            header.at("build_sha256") == build_hash, "checkpoint plan or executable mismatch");
    require(header.at("positions").size() == sources.size(), "checkpoint source count mismatch");
    for (std::size_t i = 0; i < sources.size(); ++i)
        require(header.at("positions").at(i).get<std::uint64_t>() <= sources[i].records, "checkpoint cursor outside source");
    const auto verify_configuration = [&](const Configuration& config) {
        Metrics total; std::vector<Metrics> venues(config.venues.size());
        for (std::size_t i = 0; i < sources.size(); ++i) {
            const auto& p = config.pairs[i];
            require(p.last_ms <= header.at("last_ms").get<std::int64_t>() &&
                    ((p.quantity.sign() == 0) == (p.metrics.spent.sign() == 0)), "checkpoint position inconsistency");
            const std::array<std::pair<Metrics*, const Integer*>, 2> aggregates {{
                {&total, &sources[i].to_total}, {&venues[sources[i].venue_index], &sources[i].to_venue}}};
            for (auto [m, factor] : aggregates) {
                m->spent += p.metrics.spent * *factor; m->value += p.metrics.value * *factor;
                m->realized += p.metrics.realized * *factor; m->fees += p.metrics.fees * *factor;
                m->buys += p.metrics.buys; m->sells += p.metrics.sells;
            }
        }
        const auto equal = [](const Metrics& a, const Metrics& b) {
            return a.spent == b.spent && a.value == b.value && a.realized == b.realized &&
                a.fees == b.fees && a.buys == b.buys && a.sells == b.sells;
        };
        require(equal(total, config.total), "checkpoint aggregate accounting mismatch");
        for (std::size_t i = 0; i < venues.size(); ++i)
            require(equal(venues[i], config.venues[i]), "checkpoint venue accounting mismatch");
    };
    // Restore independent configurations on the existing pool. Limit both the
    // number of parsed JSON trees and their combined input; large exceptional
    // configurations retain the original single-item path. In ordinary fixed
    // checkpoint shapes, 32 MiB of encoded data leaves ample space for parser
    // objects within a 1 GiB temporary budget, including one pending line.
    constexpr std::size_t encoded_budget = 32U * 1024U * 1024U;
    std::string pending;
    for (std::size_t begin = 0; begin < configurations.size();) {
        std::vector<std::string> encoded;
        encoded.reserve(std::min<std::size_t>(8, workers.size()));
        std::size_t bytes = 0;
        while (encoded.size() < std::min<std::size_t>(8, workers.size()) && begin + encoded.size() < configurations.size()) {
            if (!pending.empty()) { line = std::move(pending); pending.clear(); }
            else require(bool(std::getline(input, line)), "checkpoint state missing");
            require(line.size() <= 256U * 1024U * 1024U, "checkpoint state oversized");
            if (!encoded.empty() && bytes + line.size() > encoded_budget) { pending = std::move(line); break; }
            hash.add(line); hash.add("\n"); bytes += line.size();
            if (!line.empty() && line.back() == ',') line.pop_back();
            encoded.push_back(std::move(line));
            if (bytes >= encoded_budget) break;
        }
        workers.submit_jobs(encoded.size(), [&](std::size_t offset_index) {
            auto& config = configurations[begin + offset_index];
            config.restore(J::parse(encoded[offset_index])); verify_configuration(config);
        });
        workers.wait(pulse); begin += encoded.size();
        if (pulse) pulse();
    }
    require(pending.empty() && bool(std::getline(input, line)) && line.starts_with("],"), "checkpoint footer missing");
    const auto footer = J::parse("{" + line.substr(2));
    require(footer.at("payload_sha256") == hash.finish() && !std::getline(input, line), "checkpoint integrity mismatch");
    for (const auto* name : {"series", "trace"}) {
        const bool trace = std::string_view(name) == "trace";
        if (trace && !header.at("trace_enabled").get<bool>()) continue;
        const auto filename = path.parent_path() / (trace ? "trades.jsonl" : header.value("binary_series", false) ? "series.bin" : "series.jsonl");
        File file(::open(filename.c_str(), O_RDONLY | O_CLOEXEC | O_NOFOLLOW));
        require(file.fd >= 0 && prefix_hash(file.fd, header.at(std::string(name) + "_offset").get<std::uint64_t>()) ==
                header.at(std::string(name) + "_sha256").get<std::string>(), "checkpoint committed output prefix mismatch");
    }
    return header;
}
// The canonical pair bytes are part of replay verification. Reuse one buffer
// per producer instead of constructing and freeing a JSON map for every pair.
void append_pair_report(std::string& output, const Configuration& config, const Pair& pair, const Source& source) {
    const auto& metrics = pair.metrics;
    const auto integer = [&](auto value) {
        char digits[24]; const auto end = std::to_chars(digits, digits + sizeof(digits), value);
        output.append(digits, end.ptr);
    };
    const auto money = [&](const Integer& value) {
        output.push_back('"'); metrics.scale->append(output, value); output.push_back('"');
    };
    output += "{\"buy_count\":"; integer(metrics.buys);
    output += ",\"config_id\":"; output += config.encoded_id;
    output += ",\"fees\":"; money(metrics.fees);
    output += ",\"last_price\":";
    if (pair.last_bits == 0) output += "null";
    else { output.push_back('"'); units::append_ratio(output, Float32Price{pair.last_bits}.exact()); output.push_back('"'); }
    output += ",\"last_price_ms\":"; integer(pair.last_ms);
    output += ",\"longest_hold_ms\":"; integer(metrics.longest);
    output += ",\"max_drawdown\":"; money(metrics.drawdown);
    output += ",\"max_drawdown_ms\":"; integer(metrics.drawdown_ms);
    output += ",\"open_qty\":\"";
    source.quantity_scale.append(output, pair.quantity * source.quantity_coefficient); output.push_back('"');
    output += ",\"open_value\":"; money(metrics.value);
    output += ",\"peak_spent\":"; money(metrics.peak_spent);
    output += ",\"peak_spent_ms\":"; integer(metrics.peak_ms);
    output += ",\"realized_gross\":"; money(metrics.realized);
    output += ",\"return_pct\":";
    const auto pnl = metrics.pnl();
    if (metrics.peak_spent.sign() == 0) output += "null";
    else { output.push_back('"'); output += (pnl * Integer(100)).fraction(metrics.peak_spent); output.push_back('"'); }
    output += ",\"sell_count\":"; integer(metrics.sells);
    output += ",\"source_id\":"; output += source.encoded_id;
    output += ",\"spent\":"; money(metrics.spent);
    output += ",\"symbol\":"; output += source.encoded_symbol;
    output += ",\"total_pnl\":"; money(pnl);
    output += ",\"trade_hash\":\""; output += hex(pair.digest.data(), pair.digest.size());
    output += "\",\"unrealized_gross\":"; money(metrics.value - metrics.spent);
    output += ",\"venue\":"; output += source.encoded_venue; output.push_back('}');
}
J publish_pairs(const fs::path& output, std::vector<Configuration>& configurations,
                const std::vector<Source>& sources, Workers& workers, bool keep_results,
                CheckpointTiming& timing, const std::function<void()>& check_resources) {
    const bool binary_pairs = configurations.front().detail_enabled;
    const auto pair_name = binary_pairs ? "pairs.bin" : "pairs.jsonl";
    const auto temporary = output / (std::string(pair_name) + ".tmp");
    File file(keep_results ? ::open(temporary.c_str(), O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC | O_NOFOLLOW, 0600) : -1);
    require(!keep_results || file.fd >= 0, "cannot create pair report");
    J index = J::object();
    StreamHash hash;
    std::uint64_t checked_at = 0;
    const auto seconds = [](Clock::time_point began) { return std::chrono::duration<double>(Clock::now() - began).count(); };
    const bool compact_matrix = !keep_results && configurations.front().aggregate_reporting;
    const auto produce = [&](std::size_t config_index, const ChunkSink& append) {
        auto& config = configurations[config_index];
        StreamHash config_hash;
        std::string encoded; encoded.reserve(2048);
        for (std::size_t i = 0; i < sources.size(); ++i) {
            const auto& pair = config.pairs[i]; const auto& source = sources[i];
            encoded.clear(); append_pair_report(encoded, config, pair, source);
            require(encoded.size() <= 8U * 1024U * 1024U, "pair report row exceeds serialization bound");
            if (!compact_matrix) {
                if (binary_pairs) append(framed_msgpack(J::parse(encoded)));
                else { append(encoded); append("\n"); }
            }
            if (config.aggregate_reporting) { config_hash.add(encoded); config_hash.add("\n"); }
        }
        if (config.aggregate_reporting)
            config.pair_fingerprint = {{"sha256", config_hash.finish()}, {"rows", sources.size()}, {"bytes", config_hash.bytes}};
    };
    if (compact_matrix) {
        // No pair file is retained. Each worker proves its exact canonical
        // pair bytes locally; the manifest binds the ordered list of those
        // hashes. Do not move/re-hash gigabytes through a single writer merely
        // to compute a second hash for a nonexistent aggregate pair file.
        const auto began = Clock::now();
        const ChunkSink discard = [](std::string_view) {};
        workers.submit_jobs(configurations.size(), [&](std::size_t ordinal) { produce(ordinal, discard); });
        workers.wait(check_resources);
        const auto prepared = workers.last_timing();
        timing.preparation_wall_seconds += seconds(began);
        timing.preparation_worker_wall_seconds += prepared.worker_wall_seconds;
        timing.preparation_worker_cpu_seconds += prepared.worker_cpu_seconds;
        J fingerprints = J::array();
        std::uint64_t bytes = 0;
        for (const auto& config : configurations) {
            auto item = config.pair_fingerprint; item["config_id"] = config.id;
            fingerprints.push_back(std::move(item));
            bytes += config.pair_fingerprint.at("bytes").get<std::uint64_t>();
        }
        check_resources();
        return {{"pairs_hash_scope", "per_configuration"}, {"pairs_fingerprints_sha256", sha(fingerprints.dump())},
                {"pair_rows", configurations.size() * sources.size()}, {"pairs_bytes", bytes},
                {"configuration_count", configurations.size()}};
    }
    timing.add(parallel_stream(configurations.size(), workers, produce, [&](std::string_view chunk) {
        const auto began = Clock::now();
        if (keep_results) write_all(file.fd, chunk);
        hash.add(chunk); timing.write_seconds += seconds(began);
        if (hash.bytes - checked_at >= 64U * 1024U * 1024U) { check_resources(); checked_at = hash.bytes; }
    }, [&](std::size_t i, bool first) { index[configurations[i].id][first ? "offset" : "end"] = hash.bytes; }));
    if (keep_results) {
        const auto began = Clock::now();
        require(::fsync(file.fd) == 0 && ::rename(temporary.c_str(), (output / pair_name).c_str()) == 0,
                "cannot publish pair report");
        sync_directory(output); atomic_json(output / "pairs.index.json", index);
        timing.fsync_seconds += seconds(began);
    }
    return {{"pairs_sha256", hash.finish()}, {"pair_rows", configurations.size() * sources.size()},
            {"pairs_bytes", hash.bytes}, {"configuration_count", configurations.size()}};
}
double elapsed(Clock::time_point begin) { return std::chrono::duration<double>(Clock::now() - begin).count(); }
} // namespace

int run_plan(const J& plan, BatchAudit* batch = nullptr, const std::function<void(const J&)>& forward_progress = {}) {
    require(plan.at("schema") == "grid-research-sweep-plan-v1", "unsupported research sweep plan");
    const auto mode = plan.at("mode").get<std::string>();
    require(std::set<std::string>{"validation", "calibration", "rehearsal", "full", "endurance", "matrix_batch"}.contains(mode), "invalid research mode");
    const bool aggregate_matrix = plan.value("matrix_kind", std::string()) == "aggregate_tp_v1";
    const auto statistics_mode = plan.value("statistics_mode", std::string("none"));
    require(statistics_mode == "none" || (aggregate_matrix && statistics_mode == "monthly_v1"), "unsupported research statistics mode");
    require(!plan.contains("matrix_kind") || aggregate_matrix, "unsupported research matrix kind");
    const bool matrix_batch = mode == "matrix_batch";
    require(!matrix_batch || aggregate_matrix, "matrix batch requires aggregate_tp_v1 matrix");
    require(mode != "full" || !aggregate_matrix, "aggregate matrix must use bounded matrix_batch mode");
    const bool gap_free = plan.contains("source_selection");
    if (gap_free) {
        const auto& selection = plan.at("source_selection");
        require(selection.is_object() && selection.size() == 5 && aggregate_matrix && mode != "full" &&
                selection.value("history_policy", std::string()) == "gap_free_own_span",
                "unsupported source-selection history policy");
        (void)parse_digest(selection.at("manifest_sha256").get<std::string>());
        const J expected = {"bingx", "gate", "htx", "kucoin", "okx"};
        require(selection.at("expected_venues") == expected && selection.at("source_ids").is_array(),
                "gap-free selection requires the approved five venues and source IDs");
        for (const auto* field : {"source_ids", "selected_source_ids"}) {
            const auto& ids = selection.at(field);
            require(ids.is_array() && !ids.empty() && ids.size() <= 10000,
                    "invalid gap-free source roster");
            std::string previous;
            for (const auto& id : ids) {
                require(id.is_string(), "invalid gap-free source identity");
                const auto value = id.get<std::string>();
                require(!value.empty() && value.size() <= 256 && value > previous,
                        "gap-free source IDs must be sorted and unique");
                previous = value;
            }
        }
        require(!matrix_batch || selection.at("source_ids") == selection.at("selected_source_ids"),
                "matrix batch differs from the full gap-free selected roster");
    }
    const bool full = mode == "full", whole_history = full || matrix_batch;
    const bool validation = mode == "validation", resume = plan.value("resume", false);
    const bool tracing = plan.value("trace", false);
    const auto duration = plan.value("duration_seconds", 0U);
    const auto stop_after = plan.value("stop_after_blocks", std::uint64_t{0});
    require(!tracing || validation, "trade trace is validation-only");
    require(stop_after == 0 || validation, "block-stop hook is validation-only");
    require(!whole_history || (!plan.contains("start_ms") && !plan.contains("end_ms") && !plan.contains("max_records") &&
                     duration == 0 && stop_after == 0), "full replay cannot use diagnostic windows or stops");
    const auto thread_count = plan.value("threads", 1U), chart_bins = plan.value("chart_bins", 2000U);
    const auto block_bytes = plan.value("block_bytes", std::size_t{1024U * 1024U});
    const auto source_buffer_records = plan.value("reader_buffer_records", default_source_buffer_records);
    const auto audit_threads = plan.value("audit_threads", 8U);
    const auto progress_interval_ms = plan.value("progress_interval_ms", 1000U);
    const auto chart_mode = plan.value("chart_mode", std::string("eager"));
    const bool detailed = plan.value("detail_mode", std::string()) == "monthly_drawdown_v1";
    require(!plan.contains("detail_mode") || detailed, "unsupported detail mode");
    require(!detailed || (chart_mode == "eager" && statistics_mode == "monthly_v1" && !tracing &&
            plan.at("configurations").size() == 1), "detail replay requires one untraced monthly configuration");
    require(batch != nullptr || aggregate_matrix || plan.value("keep_results", true), "compact results require a benchmark or aggregate matrix");
    require(chart_mode == "eager" || chart_mode == "on_demand", "invalid research chart mode");
    require(!plan.contains("chart_pair_source_id") || (validation && chart_mode == "eager"), "pair chart requires eager validation replay");
    require(source_buffer_records >= 256 && source_buffer_records <= 65536 && audit_threads >= 1 && audit_threads <= 32 &&
            progress_interval_ms >= 100 && progress_interval_ms <= 10000, "invalid research reader, audit or progress bound");
    const auto checkpoint_seconds = plan.value("checkpoint_seconds", 900U);
    const auto max_groups = plan.value("max_timestamp_groups", 64U);
    const auto warmup_until = plan.value("warmup_until_ms", std::int64_t{0});
    const auto scheduler = plan.value("scheduler", std::string("static"));
    require(thread_count >= 1 && thread_count <= 64 && chart_bins >= 1 && chart_bins <= 2000 &&
            block_bytes >= 4096 && block_bytes <= 64U * 1024U * 1024U && (checkpoint_seconds > 0 || validation),
            "invalid research memory, thread, chart or checkpoint bound");
    require(max_groups >= 1 && max_groups <= 4096 && warmup_until >= 0 && (!whole_history || warmup_until == 0),
            "invalid diagnostic warmup or timestamp-group bound");
    require(scheduler == "static" || scheduler == "dynamic", "invalid research configuration scheduler");
    const auto& limits_json = plan.at("runtime_limits");
    const Limits limits {limits_json.at("rss_bytes"), limits_json.at("min_available_bytes"),
                         limits_json.at("min_free_disk_bytes"), limits_json.at("max_output_bytes")};
    require(validation || (limits.rss && limits.available && limits.disk && limits.output), "non-validation resource limits must be positive");
    require(plan.at("sources").is_array() && !plan.at("sources").empty() && plan.at("sources").size() <= 10000,
            "research source count outside bound");
    require(plan.at("configurations").is_array() && !plan.at("configurations").empty() &&
            plan.at("configurations").size() <= (aggregate_matrix ? 8192U : 540U),
            "research configuration count outside bound");
    std::vector<Source> sources; sources.reserve(plan.at("sources").size());
    std::set<std::string> source_ids, source_paths, markets, venue_set, config_ids;
    std::uint64_t input_rows = 0;
    for (const auto& row : plan.at("sources")) {
        sources.emplace_back(row); const auto& source = sources.back();
        require(source_ids.insert(source.id).second && source_paths.insert(source.path).second &&
                markets.insert(source.venue + ":" + source.symbol).second, "duplicate research source or market");
        venue_set.insert(source.venue);
        require(input_rows <= std::numeric_limits<std::uint64_t>::max() - source.records, "source row total overflow");
        input_rows += source.records;
    }
    std::sort(sources.begin(), sources.end(), [](const Source& a, const Source& b) {
        return std::tie(a.venue, a.symbol, a.id) < std::tie(b.venue, b.symbol, b.id);
    });
    const std::vector<std::string> venues(venue_set.begin(), venue_set.end());
    if (gap_free) {
        const auto& selection = plan.at("source_selection");
        require(J(venues) == selection.at("expected_venues") &&
                J(std::vector<std::string>(source_ids.begin(), source_ids.end())) == selection.at("source_ids"),
                "gap-free selection source roster differs from its manifest binding");
    }
    std::int64_t earliest = sources.front().first_ms, latest = sources.front().last_ms;
    for (auto& source : sources) {
        source.venue_index = static_cast<std::size_t>(std::lower_bound(venues.begin(), venues.end(), source.venue) - venues.begin());
        earliest = std::min(earliest, source.first_ms); latest = std::max(latest, source.last_ms);
        (void)source.rules.price_tick.ratio(); (void)source.rules.amount_tick.ratio();
        (void)source.rules.min_amount.ratio(); (void)source.rules.min_cost.ratio();
    }
    require(latest <= std::numeric_limits<std::int64_t>::max() - minute_ms, "source horizon overflow");
    const auto start = plan.value("start_ms", earliest), end = plan.value("end_ms", latest + minute_ms);
    require(start >= 0 && end > start && end <= max_timestamp_ms + minute_ms, "invalid half-open replay window");
    const auto chart_width = std::max<std::int64_t>(1, (end - start + static_cast<std::int64_t>(chart_bins) - 1) / chart_bins);
    const auto minimum_state_bytes = static_cast<std::uint64_t>(sources.size()) * plan.at("configurations").size() * sizeof(Pair) +
        static_cast<std::uint64_t>(sources.size()) * source_buffer_records * record_bytes +
        2 * ((block_bytes / sizeof(Record) + 1 + sources.size()) * sizeof(Record));
    require(!limits.rss || rss_bytes() + minimum_state_bytes <= limits.rss, "declared RSS limit cannot hold minimum research state");
    require(!limits.available || available_bytes() >= limits.available + minimum_state_bytes,
            "available-memory floor cannot hold minimum research state");
    std::vector<Configuration> configurations; configurations.reserve(plan.at("configurations").size());
    std::map<std::string, std::vector<Rational>> factor_tables;
    std::map<std::pair<unsigned, std::string>, std::vector<units::PreparedProduct>> product_tables;
    std::set<std::tuple<std::string, std::string, unsigned, unsigned, unsigned>> matrix;
    std::size_t chart_pair_index = sources.size();
    if (plan.contains("chart_pair_source_id")) {
        for (std::size_t i = 0; i < sources.size(); ++i) if (sources[i].id == plan.at("chart_pair_source_id")) chart_pair_index = i;
        require(chart_pair_index < sources.size(), "pair chart source is outside research roster");
    }
    for (const auto& row : plan.at("configurations")) {
        configurations.emplace_back(row, sources.size(), venues.size(), tracing, start, end, chart_width, aggregate_matrix);
        configurations.back().require_current_quotes = gap_free;
        configurations.back().chart_enabled = chart_mode == "eager";
        configurations.back().performance_enabled = statistics_mode == "monthly_v1";
        configurations.back().detail_enabled = detailed;
        configurations.back().render_cache_enabled = plan.value("trade_render_cache", true);
        configurations.back().grid_cache_enabled = plan.value("grid_level_cache", true);
        if (chart_pair_index < sources.size()) {
            configurations.back().chart_pair_index = chart_pair_index;
            configurations.back().charts.emplace_back();
        }
        const auto& config = configurations.back();
        require(config_ids.insert(config.id).second &&
                matrix.emplace(number(config.step), number(config.tp), config.exchange_tp_half_percent,
                    config.global_tp_half_percent, config.grid_depth_pct).second,
                "duplicate research configuration");
    }
    for (auto& config : configurations) {
        auto [entry, inserted] = factor_tables.try_emplace(number(config.step));
        if (inserted) {
            auto& table = entry->second; table.reserve(185); table.emplace_back(1);
            for (unsigned i = 1; i <= 184; ++i) table.push_back(table.back() * config.ratio);
            for (const auto& factor : table) (void)factor.ratio();
        }
        config.factors = &entry->second;
        if (inserted && aggregate_matrix) for (auto& source : sources) {
            if (source.rules.significant) continue;
            const auto key = std::make_pair(config.grid_index, number(source.rules.price_tick));
            auto [products, added] = product_tables.try_emplace(key);
            if (added) {
                products->second.reserve(entry->second.size());
                for (const auto& factor : entry->second)
                    products->second.emplace_back(factor, source.rules.price_tick);
            }
            source.grid_products[config.grid_index] = &products->second;
        }
    }
    if (full) {
        require(venues.size() == 7 && configurations.size() == 540, "full replay requires all seven venues and 540 configurations");
        for (std::int64_t step = 1; step <= 20; ++step) for (std::int64_t tp = 4; tp <= 30; ++tp)
            require(matrix.contains({number(Rational(step) / Rational(200)), number(Rational(tp) / Rational(200)), 0, 0, 60}),
                    "full replay matrix differs from the approved 20 by 27 matrix");
    }
    if (matrix_batch && !gap_free) require(venues.size() == 7, "aggregate matrix batch requires all seven venues");
    fs::path output(plan.at("output_dir").get<std::string>());
    require(output.is_absolute() && output != output.root_path(), "absolute dedicated output directory required");
    output = fs::weakly_canonical(output);
    for (const auto* protected_root : {"/opt/datasets", "/opt/module1_n", "/opt/module_1_multi"}) {
        const auto relative = output.lexically_relative(protected_root).string();
        require(relative.starts_with("..") || relative.empty(), "research output cannot modify protected inputs");
    }
    require(!fs::is_symlink(output), "output directory cannot be a symlink");
    if (!resume) require(!fs::exists(output) || fs::is_empty(output), "fresh research output directory must be empty");
    else require(fs::is_directory(output) && !fs::exists(output / "summary.json"), "resume requires an unfinished research directory");
    fs::create_directories(output);
    File lock(::open((output / ".lock").c_str(), O_RDWR | O_CREAT | O_CLOEXEC | O_NOFOLLOW, 0600));
    require(lock.fd >= 0 && ::flock(lock.fd, LOCK_EX | LOCK_NB) == 0, "research output already has an owner");
    const auto plan_hash = scientific_hash(plan), build_hash = executable_hash();
    const auto wall_start = Clock::now();
    J progress { {"schema", "grid-research-sweep-progress-v1"}, {"status", "VALIDATING"}, {"mode", mode},
        {"plan_sha256", plan_hash}, {"build_sha256", build_hash}, {"source_count", sources.size()},
        {"configuration_count", configurations.size()}, {"input_records", input_rows}, {"processed_records", 0},
        {"processed_blocks", 0}, {"validated_sources", 0}, {"checkpoint_count", 0},
        {"chart_mode", chart_mode}, {"charts_available", chart_mode == "eager"} };
    auto last_progress = Clock::time_point{};
    std::string published_status;
    const auto report_progress = [&](bool force = false) {
        const auto now = Clock::now();
        const auto status = progress.at("status").get<std::string>();
        if (!force && status == published_status && now - last_progress < std::chrono::milliseconds(progress_interval_ms)) return;
        progress["elapsed_seconds"] = elapsed(wall_start); progress["rss_bytes"] = rss_bytes();
        atomic_json(output / "progress.json", progress, false);
        if (forward_progress) forward_progress(progress);
        last_progress = now; published_status = status;
    };
    auto last_resource_check = Clock::time_point{};
    const auto check_resources = [&](bool force = false) {
        const auto now = Clock::now();
        if (!force && now - last_resource_check < std::chrono::seconds(1)) return;
        resources(limits, output); last_resource_check = now;
    };
    try {
        check_resources(); report_progress();
        if (!resume) atomic_json(output / "plan.json", plan);
        Audit audited; const auto audit_begin = Clock::now();
        if (batch && !batch->identities.empty()) {
            require(batch->sources_sha256 == sha(plan.at("sources").dump()) && batch->identities.size() == sources.size(),
                    "benchmark source roster changed");
            for (std::size_t i = 0; i < sources.size(); ++i) {
                sources[i].identity = batch->identities[i]; sources[i].dyadic_places = batch->dyadic_places[i];
                sources[i].check_identity();
            }
            audited = batch->audit; progress["validated_sources"] = sources.size();
        } else {
            audited = audit_sources(sources, true, audit_threads, [&](std::size_t count) {
                progress["validated_sources"] = count; check_resources(); report_progress();
            });
            if (batch) {
                batch->sources_sha256 = sha(plan.at("sources").dump()); batch->audit = audited;
                for (const auto& source : sources) {
                    batch->identities.push_back(source.identity); batch->dyadic_places.push_back(source.dyadic_places);
                }
            }
        }
        require(!gap_free || (audited.gaps == 0 && audited.missing_minutes == 0),
                "gap-free selection contains missing source minutes");
        const double audit_seconds = elapsed(audit_begin);
        Scale total_scale;
        std::vector<Scale> venue_scales(venues.size());
        std::map<std::tuple<unsigned, unsigned, std::string>, std::uint32_t> render_groups;
        for (auto& source : sources) {
            const auto amount_scale = Scale::for_value(source.rules.amount_tick);
            source.quantity_scale = amount_scale;
            source.quantity_coefficient = amount_scale.encode(source.rules.amount_tick);
            const auto price_scale = Scale::for_value(source.rules.price_tick);
            source.money_scale = Scale(std::max(source.dyadic_places, price_scale.twos()) + amount_scale.twos() + 2,
                                       price_scale.fives() + amount_scale.fives() + 3);
            source.minimum_cost_units = Integer::from_rational_integer(
                (source.rules.min_cost * Scale().decode(source.money_scale.denominator())).rounded(Rational(1), true));
            // Identical scales and amount ticks imply identical exact numeric
            // trade text. Venue/symbol/time/sequence stay outside this cache.
            const auto key = std::make_tuple(source.money_scale.twos(), source.money_scale.fives(), number(source.rules.amount_tick));
            source.render_group = render_groups.try_emplace(key, static_cast<std::uint32_t>(render_groups.size())).first->second;
            total_scale = units::common_scale(total_scale, source.money_scale);
            venue_scales[source.venue_index] = units::common_scale(venue_scales[source.venue_index], source.money_scale);
        }
        for (auto& source : sources) {
            source.to_total = source.money_scale.coefficient_to(total_scale);
            source.to_venue = source.money_scale.coefficient_to(venue_scales[source.venue_index]);
            source.price_coefficient = std::make_unique<units::Float32Coefficient>(source.rules.amount_tick, source.money_scale);
        }
        for (auto& config : configurations) config.bind_scales(sources, total_scale, venue_scales);
        // Buffers outlive the pool, including an input error after submission.
        Block current, next;
        Workers workers(static_cast<unsigned>(std::min<std::size_t>(thread_count, configurations.size())), configurations, sources,
                        scheduler == "dynamic");
        std::vector<std::uint64_t> positions;
        std::uint64_t processed = 0, blocks = 0, checkpoint_count = 0; std::int64_t last_ms = 0;
        J restored;
        double checkpoint_restore_seconds = 0;
        if (resume) {
            const auto restore_begin = Clock::now();
            progress["status"] = "RESTORING"; report_progress(true);
            const auto pulse = [&] { check_resources(); report_progress(); };
            try { restored = load_checkpoint(output / "checkpoint.latest.json", plan_hash, build_hash, configurations, sources, workers, pulse); }
            catch (const std::exception&) {
                restored = load_checkpoint(output / "checkpoint.previous.json", plan_hash, build_hash, configurations, sources, workers, pulse);
                progress["checkpoint_fallback"] = true;
            }
            positions = restored.at("positions").get<std::vector<std::uint64_t>>();
            processed = restored.at("processed_records"); blocks = restored.at("processed_blocks"); last_ms = restored.at("last_ms");
            checkpoint_count = restored.value("checkpoint_count", std::uint64_t{0});
            progress["checkpoint_count"] = checkpoint_count;
            checkpoint_restore_seconds = elapsed(restore_begin);
        }
        File series(::open((output / (detailed ? "series.bin" : "series.jsonl")).c_str(), O_RDWR | O_CREAT | O_CLOEXEC | O_NOFOLLOW, 0600));
        File trades(tracing ? ::open((output / "trades.jsonl").c_str(), O_RDWR | O_CREAT | O_CLOEXEC | O_NOFOLLOW, 0600) : -1);
        require(series.fd >= 0 && (!tracing || trades.fd >= 0), "cannot open bounded research outputs");
        StreamHash series_hash, trades_hash;
        restore_output(series.fd, resume ? restored.at("series_offset").get<std::uint64_t>() : 0,
                       resume ? restored.at("series_sha256").get<std::string>() : "", &series_hash);
        if (tracing) restore_output(trades.fd, resume ? restored.at("trace_offset").get<std::uint64_t>() : 0,
                                    resume ? restored.at("trace_sha256").get<std::string>() : "", &trades_hash);
        const auto reader_setup_begin = Clock::now();
        Merger merger(sources, positions, start, end, plan.value("max_records", std::uint64_t{0}), block_bytes, max_groups, warmup_until,
                      source_buffer_records);
        const double reader_setup_seconds = elapsed(reader_setup_begin);
        merger.generated = processed;
        const auto record_capacity = block_bytes / sizeof(Record) + 1 + sources.size();
        current.records.reserve(record_capacity); next.records.reserve(record_capacity);
        auto checkpoint_time = Clock::now();
        double checkpoint_elapsed = 0, output_elapsed = 0;
        CheckpointTiming checkpoint_timing, output_timing;
        const auto update_timings = [&] {
            const auto strategy = workers.strategy_timing();
            progress["reader"] = merger.timing.report();
            progress["timings"] = {{"strategy_wall_seconds", strategy.wall_seconds},
                {"checkpoint_restore_seconds", checkpoint_restore_seconds},
                {"reader_setup_seconds", reader_setup_seconds}, {"source_read_seconds", merger.timing.read_seconds},
                {"strategy_worker_wall_seconds", strategy.worker_wall_seconds},
                {"strategy_worker_cpu_seconds", strategy.worker_cpu_seconds},
                {"thread_boundary_wait_seconds", strategy.tail_wait_seconds},
                {"checkpoint_seconds", checkpoint_elapsed},
                {"checkpoint_preparation_wall_seconds", checkpoint_timing.preparation_wall_seconds},
                {"checkpoint_preparation_worker_wall_seconds", checkpoint_timing.preparation_worker_wall_seconds},
                {"checkpoint_preparation_worker_cpu_seconds", checkpoint_timing.preparation_worker_cpu_seconds},
                {"checkpoint_serialization_wait_seconds", checkpoint_timing.serialization_wait_seconds},
                {"checkpoint_write_seconds", checkpoint_timing.write_seconds},
                {"checkpoint_fsync_seconds", checkpoint_timing.fsync_seconds}, {"output_seconds", output_elapsed},
                {"output_preparation_worker_cpu_seconds", output_timing.preparation_worker_cpu_seconds},
                {"output_preparation_worker_wall_seconds", output_timing.preparation_worker_wall_seconds},
                {"output_serialization_wait_seconds", output_timing.serialization_wait_seconds},
                {"output_write_seconds", output_timing.write_seconds}};
        };
        const auto save_checkpoint = [&] {
            const auto began = Clock::now();
            progress["status"] = "CHECKPOINTING"; update_timings(); report_progress();
            const J header { {"schema", "grid-research-sweep-checkpoint-v1"}, {"plan_sha256", plan_hash},
                {"build_sha256", build_hash}, {"processed_records", processed}, {"processed_blocks", blocks},
                {"checkpoint_count", checkpoint_count + 1},
                {"binary_series", detailed},
                {"last_ms", last_ms}, {"positions", positions} };
            checkpoint_timing.add(checkpoint(output, header, configurations, series.fd, trades.fd, workers,
                series_hash, trades_hash, [&] { check_resources(true); report_progress(); }));
            checkpoint_time = Clock::now();
            ++checkpoint_count; progress["checkpoint_count"] = checkpoint_count;
            checkpoint_elapsed += elapsed(began);
            progress["status"] = "RUNNING"; update_timings();
        };
        const auto replay_begin = Clock::now(); double merge_seconds = 0, wait_seconds = 0;
        std::size_t input_buffers_peak_bytes_estimate = 0, input_coefficients_peak_bytes_estimate = 0;
        auto measured_begin = replay_begin;
        bool measuring = warmup_until == 0;
        std::uint64_t measured_records = 0, measured_blocks = 0, measured_filled_blocks = 0;
        auto merge_begin = Clock::now(); bool have = merger.next(current); merge_seconds += elapsed(merge_begin);
        bool duration_stopped = false;
        progress["status"] = "RUNNING"; update_timings(); report_progress(true);
        while (have) {
            if (!measuring && current.records.front().timestamp >= warmup_until) {
                measured_begin = Clock::now(); measuring = true;
            }
            workers.submit(current);
            merge_begin = Clock::now(); const bool have_next = merger.next(next); merge_seconds += elapsed(merge_begin);
            input_buffers_peak_bytes_estimate = std::max(input_buffers_peak_bytes_estimate,
                current.capacity_bytes_estimate() + next.capacity_bytes_estimate());
            input_coefficients_peak_bytes_estimate = std::max(input_coefficients_peak_bytes_estimate,
                current.coefficient_heap_estimate + next.coefficient_heap_estimate);
            const auto wait_begin = Clock::now(); workers.wait(); wait_seconds += elapsed(wait_begin);
            processed += current.records.size(); ++blocks; positions = current.positions;
            if (measuring) {
                measured_records += current.records.size(); ++measured_blocks;
                if (current.filled) ++measured_filled_blocks;
            }
            last_ms = current.records.back().timestamp + 3000;
            const auto output_begin = Clock::now();
            if (chart_mode == "eager" || tracing)
                output_timing.add(append_pending(configurations, series.fd, trades.fd, venues, workers, &series_hash, &trades_hash));
            output_elapsed += elapsed(output_begin);
            progress["status"] = "RUNNING"; progress["processed_records"] = processed;
            progress["processed_blocks"] = blocks; progress["last_ms"] = last_ms;
            update_timings();
            progress["timings"]["merge_seconds"] = merge_seconds;
            progress["timings"]["worker_wait_seconds"] = wait_seconds;
            progress["measured_records"] = measured_records;
            progress["measured_filled_blocks"] = measured_filled_blocks;
            progress["input_buffers_peak_bytes_estimate"] = input_buffers_peak_bytes_estimate;
            check_resources();
            if (interrupted || (stop_after != 0 && blocks >= stop_after)) {
                save_checkpoint(); progress["status"] = "CHECKPOINTED"; report_progress(); return 3;
            }
            if (checkpoint_seconds == 0 || elapsed(checkpoint_time) >= checkpoint_seconds) save_checkpoint();
            report_progress();
            if (duration != 0 && elapsed(replay_begin) >= duration) { duration_stopped = true; save_checkpoint(); break; }
            std::swap(current, next); have = have_next;
        }
        const double replay_seconds = elapsed(replay_begin);
        const double measured_replay_seconds = measuring ? elapsed(measured_begin) : 0;
        for (auto& config : configurations) for (std::size_t i = 0; i < config.charts.size(); ++i) config.flush_chart(i);
        const auto final_output_begin = Clock::now();
        if (chart_mode == "eager" || tracing)
            output_timing.add(append_pending(configurations, series.fd, trades.fd, venues, workers, &series_hash, &trades_hash));
        require(::fsync(series.fd) == 0 && (trades.fd < 0 || ::fsync(trades.fd) == 0), "final series synchronization failed");
        output_elapsed += elapsed(final_output_begin);
        progress["status"] = "VERIFYING"; report_progress();
        const auto verification_begin = Clock::now();
        if (batch) for (const auto& source : sources) source.check_identity();
        else (void)audit_sources(sources, false, audit_threads, [&](std::size_t count) {
            progress["verified_sources"] = count; check_resources(); report_progress();
        });
        const double verification_seconds = elapsed(verification_begin);
        const auto publication_begin = Clock::now();
        const auto statistics_end = plan.contains("max_records") || duration_stopped ? std::min(end, last_ms + 1) : end;
        const auto result_output = plan.value("result_output", std::string("json"));
        require(result_output == "json" || result_output == "framed_msgpack_v1", "unsupported result output");
        const bool binary_results = result_output == "framed_msgpack_v1";
        const auto statistics_begin = Clock::now();
        if (statistics_mode == "monthly_v1") {
            workers.submit_jobs(configurations.size(), [&](std::size_t index) {
                configurations[index].advance_performance(statistics_end);
            });
            workers.wait([&] { check_resources(); report_progress(); });
        }
        double statistics_report_seconds = elapsed(statistics_begin);
        auto fingerprint = publish_pairs(output, configurations, sources, workers, plan.value("keep_results", true), output_timing,
            [&] { check_resources(true); report_progress(); }); check_resources();
        J results = J::array();
        double worker_seconds = 0;
        std::uint64_t render_hits = 0, render_misses = 0, render_bypasses = 0;
        std::uint64_t grid_hits = 0, grid_misses = 0, grid_bypasses = 0;
        for (const auto& config : configurations) {
            render_hits += config.render_cache_hits; render_misses += config.render_cache_misses;
            render_bypasses += config.render_cache_bypasses;
            grid_hits += config.grid_cache_hits; grid_misses += config.grid_cache_misses;
            grid_bypasses += config.grid_cache_bypasses;
            worker_seconds += config.worker_seconds;
        }
        const auto report = [&](std::size_t index) {
            const auto& config = configurations[index];
            J venue_results = J::array();
            for (std::size_t i = 0; i < venues.size(); ++i) {
                auto row = config.venues[i].report(); row["venue"] = venues[i]; venue_results.push_back(std::move(row));
            }
            J result {{"id", config.id}, {"step_pct", number(config.step * Rational(100))},
                {"tp_pct", number(config.tp * Rational(100))}, {"trade_hash", hex(config.digest.data(), config.digest.size())},
                {"total", config.total.report()}, {"venues", std::move(venue_results)}};
            if (config.explicit_grid_depth) result["grid_depth_pct"] = number(Rational(config.grid_depth_pct));
            if (config.aggregate_reporting) {
                result["exchange_tp_pct"] = config.exchange_tp_half_percent == 0 ? J(nullptr) :
                    J(number(Rational(config.exchange_tp_half_percent) / Rational(2)));
                result["global_tp_pct"] = config.global_tp_half_percent == 0 ? J(nullptr) :
                    J(number(Rational(config.global_tp_half_percent) / Rational(2)));
                result["aggregate_cycles"] = config.cycle_report();
                for (std::size_t i = 0; i < venues.size(); ++i) result["aggregate_cycles"]["venues"][i]["venue"] = venues[i];
                result["pair_fingerprint"] = config.pair_fingerprint;
            }
            if (config.performance_enabled) result["performance"] = config.performance_report(statistics_end, venues);
            if (config.detail_enabled) {
                J scoped = J::array();
                for (std::size_t i = 0; i < venues.size(); ++i)
                    scoped.push_back({{"venue", venues[i]}, {"monthly", config.performance[i + 1].drawdown_report(
                        std::min(statistics_end, config.venue_performance_ends[i]), config.venues[i])}});
                result["detail"] = {{"schema", "grid-detail-risk-v1"},
                    {"definition", "maximum_lifetime_peak_shortfall_attained_in_month"},
                    {"total", config.performance[0].drawdown_report(statistics_end, config.total)}, {"venues", std::move(scoped)}};
            }
            return result;
        };
        J binary_descriptor;
        if (binary_results) {
            const auto began = Clock::now();
            const auto temporary = output / "configurations.bin.tmp";
            File file(::open(temporary.c_str(), O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC | O_NOFOLLOW, 0600));
            require(file.fd >= 0, "cannot create framed result output");
            StreamHash file_hash, canonical_hash;
            const auto emit = [&](std::string_view bytes) { write_all(file.fd, bytes); file_hash.add(bytes); };
            emit("GRIDMP1\n"); canonical_hash.add("[");
            // Only four configuration reports are prepared concurrently. Include
            // DOM, canonical text, MessagePack and bounded queue storage in the
            // existing 1 GiB serialization budget; never hold all monthly reports.
            std::vector<std::string> canonical(configurations.size());
            output_timing.add(parallel_stream(configurations.size(), workers,
                [&](std::size_t index, const ChunkSink& append) {
                    auto row = report(index);
                    canonical[index] = row.dump();
                    require(canonical[index].size() <= 16U * 1024U * 1024U, "configuration report exceeds frame bound");
                    const auto bytes = J::to_msgpack(row);
                    require(bytes.size() <= 16U * 1024U * 1024U, "configuration MessagePack exceeds frame bound");
                    const std::string_view payload(reinterpret_cast<const char*>(bytes.data()), bytes.size());
                    const auto checksum = parse_digest(sha(std::string(payload)));
                    std::string header(8, '\0');
                    for (std::size_t i = 0; i < 8; ++i) header[i] = static_cast<char>((static_cast<std::uint64_t>(bytes.size()) >> (8U * i)) & 255U);
                    header.append(reinterpret_cast<const char*>(checksum.data()), checksum.size());
                    append(header); append(payload);
                }, emit, [&](std::size_t index, bool before) {
                    if (!before) {
                        if (index) canonical_hash.add(",");
                        canonical_hash.add(canonical[index]);
                        std::string().swap(canonical[index]);
                        check_resources(true); report_progress();
                    }
                }, 4, 224U * 1024U * 1024U));
            canonical_hash.add("]");
            require(::fsync(file.fd) == 0, "framed result synchronization failed");
            require(::rename(temporary.c_str(), (output / "configurations.bin").c_str()) == 0, "framed result publication failed");
            sync_directory(output);
            fingerprint["configurations_sha256"] = canonical_hash.finish();
            binary_descriptor = {{"format", "framed-msgpack-v1"}, {"file", "configurations.bin"},
                {"bytes", file_hash.bytes}, {"sha256", file_hash.finish()}};
            statistics_report_seconds += elapsed(began);
        } else {
            for (std::size_t index = 0; index < configurations.size(); ++index) results.push_back(report(index));
            fingerprint["configurations_sha256"] = sha(results.dump());
        }
        output_elapsed += elapsed(publication_begin);
        const std::string status = batch ? "PENDING_BATCH_VERIFICATION" :
            (full ? "COMPLETE" : (matrix_batch ? "COMPLETE_BATCH" : "COMPLETE_DIAGNOSTIC"));
        const auto strategy_timing = workers.strategy_timing();
        J summary { {"schema", "grid-research-sweep-result-v1"}, {"status", status}, {"mode", mode},
            {"assumption_based", true}, {"certified_historical_performance", false}, {"plan_sha256", plan_hash},
            {"build_sha256", build_hash}, {"source_count", sources.size()}, {"venue_order", venues},
            {"configuration_count", configurations.size()}, {"input_records", input_rows}, {"processed_records", processed},
            {"measured_records", measured_records}, {"measured_blocks", measured_blocks},
            {"processed_blocks", blocks}, {"last_ms", last_ms}, {"duration_stopped", duration_stopped},
            {"checkpoint_count", checkpoint_count}, {"chart_mode", chart_mode}, {"charts_available", chart_mode == "eager"},
            {"statistics_mode", statistics_mode},
            {"fingerprint", fingerprint}, {"detailed_results_retained", plan.value("keep_results", true)},
            {"grid_level_cache", {{"enabled", plan.value("grid_level_cache", true)}, {"counting_scope", "current_process"},
                {"hits", grid_hits}, {"misses", grid_misses}, {"bypasses", grid_bypasses},
                {"entries_per_thread", GridLevelCache::capacity}, {"data_bytes_per_thread", sizeof(GridLevelCache)}}},
            {"trade_render_cache", {{"enabled", plan.value("trade_render_cache", true)},
                {"counting_scope", "current_process"},
                {"hits", render_hits}, {"misses", render_misses}, {"bypasses", render_bypasses},
                {"numeric_groups", render_groups.size()},
                {"entries_per_thread", TradeRenderCache::capacity},
                {"data_capacity_bound_per_thread", sizeof(TradeRenderCache) +
                    TradeRenderCache::capacity * TradeRenderCache::maximum_string_capacity}}},
            {"reader", merger.timing.report()},
            {"audit", {{"bytes", audited.bytes}, {"records", audited.rows}, {"gaps", audited.gaps},
                       {"missing_minutes", audited.missing_minutes}, {"source_sha256_verified", true}}},
            {"timings", {{"initial_audit_seconds", audit_seconds}, {"replay_seconds", replay_seconds},
                         {"statistics_report_seconds", statistics_report_seconds},
                         {"checkpoint_restore_seconds", checkpoint_restore_seconds},
                         {"reader_setup_seconds", reader_setup_seconds}, {"source_read_seconds", merger.timing.read_seconds},
                         {"input_decode_prepare_merge_seconds", std::max(0.0, reader_setup_seconds + merge_seconds - merger.timing.read_seconds)},
                         {"measured_replay_seconds", measured_replay_seconds},
                         {"merge_seconds", merge_seconds}, {"worker_wait_seconds", wait_seconds},
                         {"strategy_metrics_worker_seconds", worker_seconds}, {"checkpoint_seconds", checkpoint_elapsed},
                         {"strategy_wall_seconds", strategy_timing.wall_seconds},
                         {"strategy_worker_wall_seconds", strategy_timing.worker_wall_seconds},
                         {"strategy_worker_cpu_seconds", strategy_timing.worker_cpu_seconds},
                         {"thread_boundary_wait_seconds", strategy_timing.tail_wait_seconds},
                         {"checkpoint_preparation_wall_seconds", checkpoint_timing.preparation_wall_seconds},
                         {"checkpoint_preparation_worker_wall_seconds", checkpoint_timing.preparation_worker_wall_seconds},
                         {"checkpoint_preparation_worker_cpu_seconds", checkpoint_timing.preparation_worker_cpu_seconds},
                         {"checkpoint_serialization_wait_seconds", checkpoint_timing.serialization_wait_seconds},
                         {"checkpoint_write_seconds", checkpoint_timing.write_seconds},
                         {"checkpoint_fsync_seconds", checkpoint_timing.fsync_seconds},
                         {"output_preparation_worker_cpu_seconds", output_timing.preparation_worker_cpu_seconds},
                         {"output_preparation_worker_wall_seconds", output_timing.preparation_worker_wall_seconds},
                         {"output_serialization_wait_seconds", output_timing.serialization_wait_seconds},
                         {"output_write_seconds", output_timing.write_seconds}, {"output_fsync_seconds", output_timing.fsync_seconds},
                         {"output_seconds", output_elapsed}, {"final_verification_seconds", verification_seconds},
                         {"wall_seconds", elapsed(wall_start)}}},
            {"resources", {{"rss_bytes", rss_bytes()}, {"output_bytes", output_bytes(output)},
                           {"threads", thread_count}, {"block_bytes", block_bytes}, {"max_timestamp_groups_per_block", max_groups},
                           {"scheduler", scheduler}, {"measured_filled_blocks", measured_filled_blocks},
                           {"checkpoint_buffer_budget_bytes", checkpoint_timing.buffer_budget_bytes},
                           {"minimum_state_bytes", minimum_state_bytes}, {"reader_buffer_records", source_buffer_records},
                           {"audit_threads", audit_threads}, {"decoded_record_bytes", sizeof(Record)},
                           {"input_record_capacity_bytes", (current.records.capacity() + next.records.capacity()) * sizeof(Record)},
                           {"input_buffers_peak_bytes_estimate", input_buffers_peak_bytes_estimate},
                           {"input_coefficients_peak_bytes_estimate", input_coefficients_peak_bytes_estimate},
                           {"output_buffer_budget_bytes", output_timing.buffer_budget_bytes}}},
            {"configurations", std::move(results)} };
        if (statistics_mode == "monthly_v1" && gap_free)
            summary["statistics_calendar_scope"] = "venue_own_observed_span_v1";
        if (binary_results) { summary.erase("configurations"); summary["binary_configurations"] = std::move(binary_descriptor); }
        atomic_json(output / "summary.json", summary);
        progress["status"] = status; progress["processed_records"] = processed; progress["processed_blocks"] = blocks;
        update_timings(); report_progress();
        if (!batch && plan.value("summary_stdout", true)) std::cout << summary.dump() << '\n';
        return 0;
    } catch (const std::exception& error) {
        progress["status"] = "FAILED"; progress["error"] = error.what();
        try { report_progress(); } catch (const std::exception&) {}
        throw;
    }
}
int run(const std::string& plan_path) {
    SignalGuard signals;
    auto plan = read_json(plan_path);
    if (!plan.contains("benchmark_variants")) return run_plan(plan);
    const auto variants = plan.at("benchmark_variants");
    require(plan.at("schema") == "grid-research-sweep-plan-v1" && plan.at("mode") == "calibration" &&
            plan.value("threads", 0U) == 32 && plan.value("chart_mode", std::string("eager")) == "on_demand" &&
            !plan.value("resume", false) && !plan.value("trace", false) && plan.value("duration_seconds", 0U) == 0 &&
            plan.value("stop_after_blocks", std::uint64_t{0}) == 0,
            "benchmark batch requires fresh on-demand calibration with 32 calculation threads");
    require(variants.is_array() && !variants.empty() && variants.size() <= 64 &&
            plan.value("max_records", std::uint64_t{0}) > 0 &&
            plan.at("max_records").get<std::uint64_t>() <= 16777216,
            "benchmark batch requires 1..64 variants and bounded frozen input");
    std::set<std::string> ids;
    const std::set<std::string> allowed {"id", "repeat", "scheduler", "block_bytes", "reader_buffer_records",
                                         "max_timestamp_groups", "keep_results"};
    for (const auto& variant : variants) {
        require(variant.is_object(), "invalid benchmark variant");
        for (auto it = variant.begin(); it != variant.end(); ++it) require(allowed.contains(it.key()), "benchmark variant changes frozen inputs");
        const auto id = variant.at("id").get<std::string>();
        require(!id.empty() && id.size() <= 128 && id != "." && id != ".." && ids.insert(id).second &&
                std::all_of(id.begin(), id.end(), [](unsigned char c) {
                    return (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') || c == '-' || c == '_' || c == '.';
                }), "invalid or duplicate benchmark variant id");
    }
    require(variants.front().value("keep_results", true), "first benchmark variant must retain detailed results");
    fs::path output(plan.at("output_dir").get<std::string>());
    require(output.is_absolute() && output != output.root_path() && !fs::is_symlink(output), "absolute fresh benchmark output required");
    output = fs::weakly_canonical(output);
    for (const auto* root : {"/opt/datasets", "/opt/module1_n", "/opt/module_1_multi"}) {
        const auto relative = output.lexically_relative(root).string();
        require(relative.starts_with(".."), "benchmark output cannot modify protected inputs");
    }
    require(!fs::exists(output) || fs::is_empty(output), "benchmark output directory must be empty");
    fs::create_directories(output);
    File lock(::open((output / ".lock").c_str(), O_RDWR | O_CREAT | O_CLOEXEC | O_NOFOLLOW, 0600));
    require(lock.fd >= 0 && ::flock(lock.fd, LOCK_EX | LOCK_NB) == 0, "benchmark output already has an owner");
    atomic_json(output / "plan.json", plan);
    const auto batch_plan_hash = sha(plan.dump());
    const auto& limits_json = plan.at("runtime_limits");
    const Limits limits {limits_json.at("rss_bytes"), limits_json.at("min_available_bytes"),
                         limits_json.at("min_free_disk_bytes"), limits_json.at("max_output_bytes")};
    const auto began = Clock::now();
    BatchAudit audit;
    J results = J::array(), expected_fingerprint;
    J progress {{"schema", "grid-research-sweep-progress-v1"}, {"mode", "calibration"}, {"status", "VALIDATING"},
                {"variant_count", variants.size()}, {"processed_records", 0}, {"threads", 32}};
    plan.erase("benchmark_variants");
    try {
        for (std::size_t i = 0; i < variants.size(); ++i) {
            const auto& variant = variants[i];
            auto member = plan;
            for (auto it = variant.begin(); it != variant.end(); ++it)
                if (it.key() != "id" && it.key() != "repeat") member[it.key()] = it.value();
            const auto id = variant.at("id").get<std::string>();
            const auto directory = output / id;
            member["output_dir"] = directory.string();
            member["keep_results"] = variant.value("keep_results", i == 0);
            const int status = run_plan(member, &audit, [&](const J& value) {
                progress = value; progress["variant_id"] = id; progress["variant_index"] = i;
                progress["variant_count"] = variants.size(); progress["batch_elapsed_seconds"] = elapsed(began);
                resources(limits, output, true); atomic_json(output / "progress.json", progress, false);
            });
            require(status == 0 && !interrupted, "benchmark batch interrupted; remaining members were not started");
            const auto summary = read_json(directory / "summary.json");
            const auto fingerprint = summary.at("fingerprint");
            if (expected_fingerprint.is_null()) expected_fingerprint = fingerprint;
            else require(expected_fingerprint == fingerprint, "benchmark variant exact financial fingerprint mismatch");
            results.push_back({{"id", id}, {"repeat", variant.value("repeat", 0U)}, {"result_dir", directory.string()},
                {"summary_path", (directory / "summary.json").string()}, {"fingerprint", fingerprint}});
            resources(limits, output, true);
        }
        progress["status"] = "VERIFYING"; atomic_json(output / "progress.json", progress, false);
        std::vector<Source> sources; sources.reserve(plan.at("sources").size());
        for (const auto& source : plan.at("sources")) sources.emplace_back(source);
        std::sort(sources.begin(), sources.end(), [](const Source& a, const Source& b) {
            return std::tie(a.venue, a.symbol, a.id) < std::tie(b.venue, b.symbol, b.id);
        });
        require(sources.size() == audit.identities.size(), "benchmark final source roster mismatch");
        for (std::size_t i = 0; i < sources.size(); ++i) sources[i].identity = audit.identities[i];
        const auto verify_begin = Clock::now();
        auto last_progress = Clock::time_point{};
        (void)audit_sources(sources, false, plan.value("audit_threads", 8U), [&](std::size_t count) {
            const auto now = Clock::now();
            if (count != sources.size() && now - last_progress < std::chrono::seconds(1)) return;
            progress["verified_sources"] = count; progress["batch_elapsed_seconds"] = elapsed(began);
            resources(limits, output, true); atomic_json(output / "progress.json", progress, false); last_progress = now;
        });
        const auto verify_seconds = elapsed(verify_begin);
        for (const auto& variant : results) {
            const fs::path directory(variant.at("result_dir").get<std::string>());
            auto summary = read_json(directory / "summary.json");
            summary["status"] = "COMPLETE_DIAGNOSTIC"; summary["batch_source_sha256_verified"] = true;
            atomic_json(directory / "summary.json", summary);
            auto child_progress = read_json(directory / "progress.json"); child_progress["status"] = "COMPLETE_DIAGNOSTIC";
            child_progress["batch_source_sha256_verified"] = true; atomic_json(directory / "progress.json", child_progress, false);
        }
        const J summary {{"schema", "grid-research-sweep-benchmark-batch-v1"}, {"status", "COMPLETE_DIAGNOSTIC"},
            {"mode", "calibration"}, {"variants", results}, {"fingerprint", expected_fingerprint},
            {"source_count", sources.size()}, {"configuration_count", plan.at("configurations").size()},
            {"plan_sha256", batch_plan_hash}, {"sources_sha256", audit.sources_sha256},
            {"build_sha256", executable_hash()}, {"source_sha256_verified", true},
            {"source_audit_passes", 2}, {"final_verification_seconds", verify_seconds}, {"wall_seconds", elapsed(began)}};
        atomic_json(output / "batch-summary.json", summary); atomic_json(output / "summary.json", summary);
        progress["status"] = "COMPLETE_DIAGNOSTIC"; progress["batch_elapsed_seconds"] = elapsed(began);
        atomic_json(output / "progress.json", progress, false); std::cout << summary.dump() << '\n'; return 0;
    } catch (const std::exception& error) {
        progress["status"] = "FAILED"; progress["error"] = error.what();
        try { atomic_json(output / "progress.json", progress, false); } catch (...) {}
        throw;
    }
}
} // namespace grid::sweep
