// Research compatibility implementation adapted from the user's frozen audited
// module1_n/src/backtest.cpp (SHA256 below). It intentionally retains that
// engine's unfunded cash model, synthetic OHLC path and long-double arithmetic.
// It is isolated from the funded exact-money strategy and is never a live mode.
#include "comparison.hpp"
#include <cstdlib>
#include <cstdio>
#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <ctime>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <limits>
#include <stdexcept>
#include <string>
#include <fcntl.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>
#include "json.hpp"

namespace grid::comparison {
using json = nlohmann::json;
namespace fs = std::filesystem;
using Real = long double;
constexpr Real FEE = 0.002L; // 0.2% of quote notional, on both sides.
Real decimal_number(const json& value) { return std::stold(value.dump()); }

void allow_output(const fs::path& path) {
    const auto resolved = fs::weakly_canonical(path).generic_string();
    for (const std::string root : {"/opt/module1_n", "/opt/datasets", "/datasets"})
        if (resolved == root || resolved.starts_with(root + '/'))
            throw std::runtime_error("Comparison outputs cannot modify reference code or historical datasets");
}

class Progress {
    std::string path_;
    std::chrono::steady_clock::time_point last_{};
public:
    Progress() {
        if (const char* path = std::getenv("GRID_BACKTEST_PROGRESS_PATH")) path_ = path;
        if (!path_.empty()) { allow_output(path_); allow_output(path_ + ".tmp"); }
    }
    void update(const char* stage, uint64_t completed, uint64_t total, int64_t timestamp = 0,
                bool force = false) {
        if (path_.empty()) return;
        const auto now = std::chrono::steady_clock::now();
        if (!force && now - last_ < std::chrono::seconds(1)) return;
        last_ = now;
        const auto clock = std::chrono::system_clock::to_time_t(std::chrono::system_clock::now());
        std::tm utc{}; char stamp[32]{};
        if (!::gmtime_r(&clock, &utc) || !std::strftime(stamp, sizeof(stamp), "%Y-%m-%dT%H:%M:%SZ", &utc)) return;
        const json value = {{"stage", stage}, {"engine", "current_comparison"},
            {"message", std::string(stage) == "validation" ? "Validating complete source history" :
                        std::string(stage) == "complete" ? "Shared-policy research complete" :
                        "Replaying shared-policy O/H/L/C events"},
            {"processed", completed}, {"total", total}, {"unit", "candles"},
            {"timestamp_ms", timestamp}, {"updated_at", stamp}};
        const std::string temporary = path_ + ".tmp";
        std::ofstream file(temporary, std::ios::binary | std::ios::trunc);
        file << value.dump() << '\n'; file.close();
        if (file) (void)std::rename(temporary.c_str(), path_.c_str());
    }
};

#pragma pack(push, 1)
struct Candle { int64_t ts; float o, h, l, c; };
struct Trade {
    int64_t ts;
    double price, quantity, quote, fee, position_qty, position_cost, realized_pnl;
    uint32_t side, cycle; // 0 = buy, 1 = sell
};
struct Sample {
    int64_t ts, end_ts;
    double price_open, price_high, price_low, price_close;
    double pnl_open, pnl_high, pnl_low, pnl_close;
    double spent_open, spent_high, spent_low, spent_close;
    double dd_open, dd_high, dd_low, dd_close;
};
#pragma pack(pop)
static_assert(sizeof(Candle) == 24);
static_assert(sizeof(Trade) == 72);
static_assert(sizeof(Sample) == 144);

struct Ohlc {
    Real open=0, high=0, low=0, close=0;
    bool active=false;
    void add(Real value) {
        if(!active){open=high=low=value;active=true;}
        high=std::max(high,value);low=std::min(low,value);close=value;
    }
};

class Dataset {
    int fd_ = -1;
    const char* bytes_ = nullptr;
    size_t size_ = 0;
    struct stat initial_{};
    std::string path_;
public:
    explicit Dataset(const std::string& path) : path_(path) {
        const uint16_t endian = 1;
        if (*reinterpret_cast<const unsigned char*>(&endian) != 1)
            throw std::runtime_error("This build requires a little-endian host");
        fd_ = ::open(path.c_str(), O_RDONLY);
        if (fd_ < 0) throw std::runtime_error("Cannot open dataset: " + path);
        struct stat s{};
        if (fstat(fd_, &s) || !S_ISREG(s.st_mode) || s.st_size <= 0 || s.st_size % static_cast<off_t>(sizeof(Candle))) {
            ::close(fd_); fd_ = -1;
            throw std::runtime_error("Dataset must be nonempty and divisible by 24 bytes");
        }
        size_ = static_cast<size_t>(s.st_size);
        initial_ = s;
        void* p = mmap(nullptr, size_, PROT_READ, MAP_PRIVATE, fd_, 0);
        if (p == MAP_FAILED) {
            ::close(fd_); fd_ = -1;
            throw std::runtime_error("Cannot map dataset");
        }
        bytes_ = static_cast<const char*>(p);
        madvise(p, size_, MADV_SEQUENTIAL);
    }
    Dataset(const Dataset&) = delete;
    Dataset& operator=(const Dataset&) = delete;
    ~Dataset() { if (bytes_) munmap(const_cast<char*>(bytes_), size_); if (fd_ >= 0) ::close(fd_); }
    size_t count() const { return size_ / sizeof(Candle); }
    json identity() const {
        return {{"size",size_},{"mtime_ns",int64_t(initial_.st_mtim.tv_sec)*1000000000+initial_.st_mtim.tv_nsec},
                {"ctime_ns",int64_t(initial_.st_ctim.tv_sec)*1000000000+initial_.st_ctim.tv_nsec},
                {"inode",uint64_t(initial_.st_ino)},{"device",uint64_t(initial_.st_dev)}};
    }
    void verify_unchanged() const {
        struct stat current{}, named{};
        if (fstat(fd_, &current) || ::stat(path_.c_str(), &named) ||
            current.st_size != initial_.st_size || current.st_ino != initial_.st_ino ||
            current.st_mtim.tv_sec != initial_.st_mtim.tv_sec || current.st_mtim.tv_nsec != initial_.st_mtim.tv_nsec ||
            current.st_ctim.tv_sec != initial_.st_ctim.tv_sec || current.st_ctim.tv_nsec != initial_.st_ctim.tv_nsec ||
            named.st_ino != initial_.st_ino || named.st_dev != initial_.st_dev)
            throw std::runtime_error("Dataset changed while being tested; retry on a stable file");
    }
    void validate(Progress& progress) const {
        int64_t previous = 0;
        const int64_t latest = std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::system_clock::now().time_since_epoch()).count() + 60000;
        // Binary search is only safe after the entire timestamp axis is checked.
        progress.update("validation", 0, count(), 0, true);
        for (size_t i=0;i<count();++i) {
            Candle r=at(i);
            std::string problem;
            if (r.ts<=0 || r.ts>latest || r.ts%60000 || r.ts<=previous) problem="timestamp order/alignment/range";
            else if (!std::isfinite(r.o)||!std::isfinite(r.h)||!std::isfinite(r.l)||!std::isfinite(r.c)||
                     std::min({r.o,r.h,r.l,r.c})<=0) problem="nonpositive/nonfinite OHLC";
            else if (r.h<std::max(r.o,r.c)||r.l>std::min(r.o,r.c)||r.h<r.l) problem="OHLC bounds";
            if (!problem.empty()) throw std::runtime_error("Dataset validation failed at record "+std::to_string(i)+": "+problem);
            previous=r.ts;
            if (i % 4096 == 0) progress.update("validation", i + 1, count(), r.ts);
        }
        verify_unchanged();
    }
    Candle at(size_t i) const {
        Candle c{};
        std::memcpy(&c, bytes_ + i * sizeof(Candle), sizeof(Candle));
        return c;
    }
    size_t lower_bound(int64_t ts) const {
        size_t lo = 0, hi = count();
        while (lo < hi) {
            size_t mid = lo + (hi - lo) / 2;
            if (at(mid).ts < ts) lo = mid + 1; else hi = mid;
        }
        return lo;
    }
};

class Precision {
    std::string mode_;
    Real value_, fixed_, minimum_, maximum_;
public:
    Precision(const std::string& mode, Real value, Real minimum=0, Real maximum=0)
      : mode_(mode), value_(value), fixed_(0), minimum_(minimum), maximum_(maximum) {
        if (!std::isfinite(minimum)||!std::isfinite(maximum)||minimum<0||maximum<0||
            (maximum>0&&minimum>maximum)) throw std::runtime_error("Invalid precision quantum bounds");
        if (!std::isfinite(value)) throw std::runtime_error("Nonfinite precision");
        if (mode == "tick_size") {
            if (value <= 0) throw std::runtime_error("Tick size must be positive");
            fixed_ = value;
        } else if (mode == "decimal_places" || mode == "significant_digits") {
            if (value != std::floor(value) || value < -18 || value > 18 ||
                (mode == "significant_digits" && value == 0))
                throw std::runtime_error("Invalid digit precision");
            if (mode == "decimal_places" || value < 0) fixed_ = std::pow(10.0L, -value);
        } else throw std::runtime_error("Unknown precision mode");
    }
    Real quantum(Real x) const {
        Real q=fixed_ > 0 ? fixed_ : std::pow(10.0L, std::floor(std::log10(x)) - value_ + 1);
        if(maximum_>0)q=std::min(q,maximum_);
        return std::max(q,minimum_);
    }
    bool fixed_quantum() const { return fixed_ > 0; }
    Real round(Real x, bool up = false) const {
        if (!(x > 0) || !std::isfinite(x)) throw std::runtime_error("Invalid price or amount to round");
        Real q = quantum(x), units = x / q;
        // Account for the small arithmetic error accumulated by products and
        // division at an exact lot boundary. The guard stays below 1/8 of a lot;
        // if resolution is exhausted, fail instead of returning unreliable data.
        Real tolerance = 16 * std::numeric_limits<Real>::epsilon() * std::max(1.0L, std::abs(units));
        if (!std::isfinite(units) || tolerance >= 0.125L)
            throw std::runtime_error("Numeric precision exhausted while rounding an order");
        Real nearest = std::round(units);
        if (std::abs(units-nearest) <= tolerance) units = nearest;
        Real result = (up ? std::ceil(units) : std::floor(units)) * q;
        if (!std::isfinite(result) || result > std::numeric_limits<double>::max()) throw std::runtime_error("Precision overflow");
        return result;
    }
};

// Purchased amounts are exact multiples of decimal lots. Keep their sum as
// integer units rather than accumulating a floating-point balance. Significant
// digit exchanges can change the lot size as an amount changes magnitude.
class Quantity {
    uint64_t units_ = 0;
    Real unit_ = 0;
    uint64_t integer(Real value) const {
        if (!std::isfinite(value) || value < 0 || value >= Real(std::numeric_limits<uint64_t>::max()))
            throw std::runtime_error("Quantity representation overflow");
        return static_cast<uint64_t>(std::round(value));
    }
public:
    void add(Real amount, Real quantum) {
        if (units_==0) unit_=quantum;
        if (quantum<unit_) {
            uint64_t factor=integer(unit_/quantum);
            if (!factor || units_>std::numeric_limits<uint64_t>::max()/factor)
                throw std::runtime_error("Quantity representation overflow");
            units_*=factor;
            unit_=quantum;
        }
        uint64_t added=integer(amount/unit_);
        if (added>std::numeric_limits<uint64_t>::max()-units_)
            throw std::runtime_error("Quantity representation overflow");
        units_+=added;
    }
    Real remove(Real amount) {
        uint64_t removed=integer(amount/unit_);
        if(removed>units_) throw std::runtime_error("Cannot sell more than held");
        units_-=removed;
        return Real(removed)*unit_;
    }
    Real value() const {return Real(units_)*unit_;}
};

struct Output {
    std::ofstream trades, samples;
    explicit Output(const std::string& dir) {
        if (dir.empty()) return;
        allow_output(dir);
        for (const auto* name : {"trades.bin", "series.bin", "summary.json"})
            if (fs::exists(fs::symlink_status(fs::path(dir) / name)))
                throw std::runtime_error("Comparison result already exists; choose a new output directory");
        fs::create_directories(dir);
        trades.exceptions(std::ios::badbit | std::ios::failbit);
        samples.exceptions(std::ios::badbit | std::ios::failbit);
        trades.open(fs::path(dir) / "trades.bin", std::ios::binary);
        samples.open(fs::path(dir) / "series.bin", std::ios::binary);
    }
    void trade(const Trade& t) { if (trades.is_open()) trades.write(reinterpret_cast<const char*>(&t), sizeof(t)); }
    void sample(const Sample& s) { if (samples.is_open()) samples.write(reinterpret_cast<const char*>(&s), sizeof(s)); }
    void finish() { if(trades.is_open()){trades.flush();trades.close();} if(samples.is_open()){samples.flush();samples.close();} }
};

class Engine {
    Real step_, tp_, initial_quote_, averaging_, growth_;
    Precision price_precision_, amount_precision_;
    Output& out_;
    Quantity held_;
    Real buy_ = 0, sell_ = 0, next_quote_;
    Real position_fees_ = 0, high_ = 0;
    Ohlc pnl_bar_, spent_bar_, dd_bar_;
    bool active_ = false;
    uint32_t cycle_ = 1;
    uint64_t cycle_buys_ = 0;

    void check_accounting() const {
        // These fields change only on trades, not on every market point.
        for (Real value : {cash,quantity,spent,realized,fees,buy_volume,sell_volume,position_fees_})
            if (!std::isfinite(value) || std::abs(value)>std::numeric_limits<double>::max())
                throw std::runtime_error("Numeric overflow in accounting output");
    }
    void mark(Real p) {
        pnl = cash + quantity * p;
        if (!std::isfinite(pnl) || std::abs(pnl)>std::numeric_limits<double>::max())
            throw std::runtime_error("Numeric overflow: reduce averaging or order size");
        high_ = std::max(high_, pnl);
        drawdown = high_ - pnl;
        if(drawdown>std::numeric_limits<double>::max()) throw std::runtime_error("Drawdown output overflow");
        max_drawdown = std::max(max_drawdown, drawdown);
        max_spent = std::max(max_spent, spent);
        max_quantity = std::max(max_quantity, quantity);
        max_cash_deficit = std::max(max_cash_deficit, -cash);
        pnl_bar_.add(pnl);
        spent_bar_.add(spent);
        dd_bar_.add(drawdown);
    }
    Real below(Real price) const {
        Real result = price_precision_.round(price * (1 - step_));
        if (!(result > 0) || !(result < price))
            throw std::runtime_error("Grid reached price precision limit; increase step or shorten period");
        return result;
    }
public:
    Real cash = 0, quantity = 0, spent = 0, pnl = 0, realized = 0, fees = 0;
    Real buy_volume = 0, sell_volume = 0, drawdown = 0;
    Real max_drawdown = 0, max_spent = 0, max_quantity = 0, max_cash_deficit = 0;
    uint64_t buys = 0, sells = 0, max_cycle_buys = 0, amount_too_small = 0;
    explicit Engine(const json& c, Output& out)
      : step_(decimal_number(c.at("step_pct")) / 100),
        tp_(decimal_number(c.at("take_profit_pct")) / 100),
        initial_quote_(decimal_number(c.at("order_quote"))),
        averaging_(decimal_number(c.at("averaging"))),
        growth_(1 + step_ * averaging_),
        price_precision_(c.value("price_precision_mode",c.at("precision_mode").get<std::string>()),
                         decimal_number(c.at("price_precision")),
                         c.contains("price_min_tick")?decimal_number(c.at("price_min_tick")):0,
                         c.contains("price_max_tick")?decimal_number(c.at("price_max_tick")):0),
        amount_precision_(c.value("amount_precision_mode",c.at("precision_mode").get<std::string>()),decimal_number(c.at("amount_precision"))),
        out_(out), next_quote_(initial_quote_) {
        if (!std::isfinite(step_) || !(step_ > 0 && step_ < 1) || !(1-step_<1) ||
            !std::isfinite(tp_) || !(tp_ > 0) || !(1+tp_>1) ||
            !std::isfinite(initial_quote_) || !(initial_quote_ > 0) ||
            !std::isfinite(averaging_) || averaging_<0 ||
            !std::isfinite(growth_) || growth_ < 1)
            throw std::runtime_error("Require 0 < step < 100, TP > 0, quote > 0, averaging >= 0");
    }
    void point(int64_t ts, Real p) {
        if (buy_ == 0) { buy_ = below(p); mark(p); return; }
        // Every crossed standing grid order fills at its own limit price.
        while (p <= buy_) {
            Real amount = amount_precision_.round(next_quote_ / buy_);
            if (!(amount > 0)) throw std::runtime_error("Order quantity rounds to zero; increase order_quote");
            Real cost = amount * buy_, fee = cost * FEE;
            held_.add(amount,amount_precision_.quantum(amount));
            quantity=held_.value();
            spent += cost;
            active_ = true;
            position_fees_ += fee;
            cash -= cost + fee;
            fees += fee;
            buy_volume += cost;
            ++buys; ++cycle_buys_;
            max_cycle_buys = std::max(max_cycle_buys, cycle_buys_);
            check_accounting();
            mark(buy_);
            out_.trade({ts, double(buy_), double(amount), double(cost), double(fee), double(quantity), double(spent), double(realized), 0, cycle_});
            sell_ = price_precision_.round(spent * (1 + tp_) / quantity, true);
            buy_ = below(buy_);
            next_quote_ *= growth_;
            if (!std::isfinite(next_quote_) || !std::isfinite(sell_))
                throw std::runtime_error("Order size overflow");
        }
        if (active_ && quantity > 0 && p >= sell_) {
            mark(sell_);
            // A sum of fixed-lot purchases is already on the exchange lattice.
            // Rounding that floating-point sum again can manufacture one lot of dust.
            Real sold_quantity = amount_precision_.fixed_quantum() ? quantity :
                std::min(quantity, amount_precision_.round(quantity));
            sold_quantity=held_.remove(sold_quantity);
            Real fraction = held_.value()==0 ? 1 : sold_quantity / quantity;
            Real cost = sold_quantity * sell_, fee = cost * FEE;
            cash += cost - fee;
            realized += cost - fee - (spent + position_fees_) * fraction;
            fees += fee;
            sell_volume += cost;
            ++sells;
            // Preserve any exchange-precision dust, including its cost basis.
            quantity=held_.value();
            spent *= 1 - fraction;
            position_fees_ *= 1 - fraction;
            active_ = false;
            check_accounting();
            mark(sell_);
            out_.trade({ts, double(sell_), double(sold_quantity), double(cost), double(fee), double(quantity), double(spent), double(realized), 1, cycle_});
            buy_ = below(sell_);
            sell_ = 0;
            next_quote_ = initial_quote_;
            cycle_buys_ = 0;
            if (cycle_ == std::numeric_limits<uint32_t>::max()) throw std::runtime_error("Cycle counter overflow");
            ++cycle_;
        }
        if (!active_) buy_ = std::max(buy_, below(p));
        mark(p);
    }
    void sample(int64_t ts, int64_t end_ts, const Ohlc& price) {
        out_.sample({ts,end_ts,
            double(price.open),double(price.high),double(price.low),double(price.close),
            double(pnl_bar_.open),double(pnl_bar_.high),double(pnl_bar_.low),double(pnl_bar_.close),
            double(spent_bar_.open),double(spent_bar_.high),double(spent_bar_.low),double(spent_bar_.close),
            double(dd_bar_.open),double(dd_bar_.high),double(dd_bar_.low),double(dd_bar_.close)});
        pnl_bar_=Ohlc{};spent_bar_=Ohlc{};dd_bar_=Ohlc{};
    }
    double buy_order() const { return static_cast<double>(buy_); }
    double take_profit() const { return static_cast<double>(sell_); }
    bool position_active() const { return active_; }
};

int run(const std::string& configuration) {
    const auto started = std::chrono::steady_clock::now();
    try {
        if (fs::file_size(configuration) > 1024 * 1024) throw std::runtime_error("Comparison config exceeds 1 MiB");
        std::ifstream input(configuration);
        json c; input >> c;
        const std::string symbol = c.value("symbol", std::string());
        const auto separator = symbol.find('/');
        if (separator == std::string::npos || separator == 0 || symbol.substr(separator + 1) != "USDT")
            throw std::runtime_error("USDT-only policy: comparison requires a BASE/USDT symbol");
        Progress progress;
        int64_t from = c.value("start_ts", int64_t(1));
        int64_t to = c.value("end_ts", std::numeric_limits<int64_t>::max());
        if (from < 1 || from >= to) throw std::runtime_error("Require 0 < start_ts < end_ts (exclusive)");
        Dataset data(c.at("dataset"));
        if(c.contains("dataset_identity") && c.at("dataset_identity")!=data.identity())
            throw std::runtime_error("Dataset identity differs from saved run; regenerate config for the new data");
        data.validate(progress);
        size_t first = data.lower_bound(from), last = data.lower_bound(to);
        if (first == last) throw std::runtime_error("No candles in selected period");
        const size_t max_points = c.value("max_points", size_t(3000));
        if (max_points < 1 || max_points > 100000) throw std::runtime_error("max_points must be 1..100000");
        int64_t chart_interval=c.value("chart_interval_ms",int64_t(0));
        std::string calendar=c.value("chart_calendar",std::string());
        if((!calendar.empty()&&calendar!="month")||(!calendar.empty()&&chart_interval!=0))
            throw std::runtime_error("Invalid chart calendar period");
        if(chart_interval==0&&calendar.empty()) {
            int64_t span=data.at(last-1).ts-data.at(first).ts+60000;
            int64_t required=(span+int64_t(max_points)-1)/int64_t(max_points);
            for(int64_t candidate:{60000LL,300000LL,900000LL,3600000LL,14400000LL,86400000LL,259200000LL,604800000LL,2592000000LL})
                if(candidate>=required){chart_interval=candidate;break;}
            if(chart_interval==0)chart_interval=((required+59999)/60000)*60000;
        }
        if(calendar.empty()&&(chart_interval<60000||chart_interval%60000))
            throw std::runtime_error("chart_interval_ms must be a positive whole number of minutes");
        std::string output_dir = c.value("output_dir", std::string());
        Output output(output_dir);
        Engine engine(c, output);
        uint64_t candles = 0, invalid = 0, gaps = 0, missing_minutes = 0;
        int64_t previous_ts = 0, first_ts = 0, final_ts = 0;
        double last_price = 0;
        int64_t bucket=-1;
        int64_t month_end=0;
        Ohlc price_bar;
        uint64_t chart_points=0;
        progress.update("replay", 0, last-first, 0, true);
        for (size_t i = first; i < last; ++i) {
            Candle row = data.at(i);
            int64_t next_bucket=bucket;
            if(calendar=="month") {
                if(bucket==-1||row.ts>=month_end) {
                    time_t seconds=static_cast<time_t>(row.ts/1000);
                    std::tm utc{};
                    if(!gmtime_r(&seconds,&utc))throw std::runtime_error("Cannot determine UTC month");
                    utc.tm_mday=1;utc.tm_hour=utc.tm_min=utc.tm_sec=0;utc.tm_isdst=0;
                    next_bucket=int64_t(timegm(&utc))*1000;
                    ++utc.tm_mon;month_end=int64_t(timegm(&utc))*1000;
                }
            } else next_bucket=(row.ts/chart_interval)*chart_interval;
            if(bucket!=-1&&next_bucket!=bucket) {
                engine.sample(bucket,final_ts+60000,price_bar);
                ++chart_points;price_bar=Ohlc{};
            }
            bucket=next_bucket;
            if (row.ts % 60000 || (previous_ts && row.ts <= previous_ts))
                throw std::runtime_error("Invalid timestamp order/alignment at record " + std::to_string(i));
            if (previous_ts && row.ts - previous_ts > 60000) {
                ++gaps; missing_minutes += static_cast<uint64_t>((row.ts - previous_ts) / 60000 - 1);
            }
            previous_ts = row.ts;
            if (!first_ts) first_ts = row.ts;
            // Offsets only identify the four synthetic events; they are not real tick times.
            engine.point(row.ts, row.o);
            engine.point(row.ts + 1, row.h);
            engine.point(row.ts + 2, row.l);
            engine.point(row.ts + 3, row.c);
            price_bar.add(row.o);price_bar.add(row.h);price_bar.add(row.l);price_bar.add(row.c);
            ++candles;
            final_ts = row.ts;
            last_price = row.c;
            if (candles % 4096 == 0) progress.update("replay", candles, last-first, row.ts);
        }
        if (!candles) throw std::runtime_error("No valid candles in selected period");
        engine.sample(bucket,final_ts+60000,price_bar);++chart_points;
        data.verify_unchanged();
        output.finish();
        const double elapsed = std::chrono::duration<double>(std::chrono::steady_clock::now() - started).count();
        json summary = {
            {"format_version", 1}, {"engine_revision",3}, {"series_format_version",2},
            {"research_mode", "audited_grid_compatibility_v1"},
            {"engine_identity", "grid-current-comparison"},
            {"funding_model", "unfunded_not_live"},
            {"ohlc_model", "synthetic_open_high_low_close"},
            {"numeric_model", "long_double_compatibility_with_integer_quantity_lots"},
            {"reference_source_sha256", "196f65c5df6af76aca65ac8c67be7db91e57ca32039313773d877c1c8b3d765e"}, // pragma: allowlist secret
            {"chart_points",chart_points},{"chart_interval_ms",chart_interval},{"chart_calendar",calendar},{"dataset_identity",data.identity()},
            {"dataset_validated_records",data.count()}, {"fee_pct", 0.2}, {"candles", candles}, {"events", candles * 4},
            {"invalid_candles", invalid}, {"gaps", gaps}, {"missing_minutes", missing_minutes},
            {"first_ts", first_ts}, {"last_ts", final_ts}, {"last_price", last_price},
            {"buys", engine.buys}, {"sells", engine.sells}, {"trades", engine.buys + engine.sells},
            {"pnl", engine.pnl}, {"realized_pnl", engine.realized}, {"unrealized_pnl", engine.pnl - engine.realized},
            {"fees", engine.fees}, {"buy_volume", engine.buy_volume}, {"sell_volume", engine.sell_volume},
            {"cash", engine.cash}, {"position_qty", engine.quantity}, {"position_cost", engine.spent},
            {"position_active", engine.position_active()}, {"dust_qty", engine.position_active() ? 0 : engine.quantity},
            {"max_position_cost", engine.max_spent}, {"max_position_qty", engine.max_quantity},
            {"max_cash_deficit", engine.max_cash_deficit}, {"max_drawdown", engine.max_drawdown},
            {"max_buys_in_cycle", engine.max_cycle_buys}, {"amount_too_small_events", engine.amount_too_small},
            {"next_buy_price", engine.buy_order()}, {"take_profit_price", engine.take_profit()},
            {"elapsed_seconds", elapsed}, {"candles_per_second", static_cast<double>(candles) / std::max(elapsed, 1e-9)}
        };
        if (!output_dir.empty()) {
            std::ofstream file(fs::path(output_dir) / "summary.json");
            file.exceptions(std::ios::badbit | std::ios::failbit);
            file << summary.dump(2) << '\n';
            file.flush();file.close();
        }
        std::cout << summary.dump() << '\n';
        std::cout.flush();
        if (!std::cout) throw std::runtime_error("Comparison output write failed");
        progress.update("complete", candles, last-first, final_ts, true);
        return 0;
    } catch (const std::exception& e) {
        std::cerr << e.what() << '\n';
        return 1;
    }
}

} // namespace grid::comparison
