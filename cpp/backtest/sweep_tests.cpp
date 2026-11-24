#include "sweep.hpp"
#include "history.hpp"
#include "vendor/json.hpp"
#include <algorithm>
#include <array>
#include <bit>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <openssl/sha.h>
#include <unistd.h>
#include <vector>

namespace {
void check(bool condition, const char* message) { if (!condition) throw grid::Error(message); }
using J = nlohmann::json;
namespace fs = std::filesystem;
std::string bytes(const fs::path& path) {
    std::ifstream input(path, std::ios::binary);
    check(bool(input), "fixture file missing");
    return {std::istreambuf_iterator<char>(input), std::istreambuf_iterator<char>()};
}
std::string hash(const std::string& value) {
    std::array<unsigned char, SHA256_DIGEST_LENGTH> digest{};
    check(SHA256(reinterpret_cast<const unsigned char*>(value.data()), value.size(), digest.data()) != nullptr, "fixture SHA failed");
    constexpr char digits[] = "0123456789abcdef";
    std::string result;
    for (const auto byte : digest) { result += digits[byte >> 4U]; result += digits[byte & 15U]; }
    return result;
}
J read(const fs::path& path) { return J::parse(bytes(path)); }
void write(const fs::path& path, const J& value) { std::ofstream output(path); output << value.dump(); check(bool(output), "fixture write failed"); }
void replay_paths() {
    std::array<char, 64> pattern{};
    const std::string name = "/tmp/grid-sweep-core-XXXXXX";
    std::copy(name.begin(), name.end(), pattern.begin());
    const auto* created = ::mkdtemp(pattern.data()); check(created != nullptr, "fixture directory creation failed");
    const fs::path root(created);
    struct Cleanup { fs::path path; ~Cleanup() { std::error_code ignored; fs::remove_all(path, ignored); } } cleanup{root};
    constexpr std::int64_t first = 1700000040000;
    const std::array<float, 8> prices {100, 99, 50, 120, 120, 80, 130, 90};
    std::string payload;
    for (std::size_t i = 0; i < prices.size(); ++i) {
        const auto timestamp = static_cast<std::uint64_t>(first + static_cast<std::int64_t>(i) * grid::minute_ms);
        for (unsigned byte = 0; byte < 8; ++byte) payload += static_cast<char>((timestamp >> (byte * 8U)) & 255U);
        const auto bits = std::bit_cast<std::uint32_t>(prices[i]);
        for (unsigned phase = 0; phase < 4; ++phase)
            for (unsigned byte = 0; byte < 4; ++byte) payload += static_cast<char>((bits >> (byte * 8U)) & 255U);
    }
    const auto source_path = root / "source.bin";
    { std::ofstream file(source_path, std::ios::binary); file.write(payload.data(), static_cast<std::streamsize>(payload.size())); }
    J source {{"id", "okx:A\"\\/USDT"}, {"venue", "okx"}, {"symbol", "A\"\\/USDT"}, {"path", source_path.string()},
              {"sha256", hash(payload)}, {"records", prices.size()}, {"first_ms", first},
              {"last_ms", first + static_cast<std::int64_t>(prices.size() - 1) * grid::minute_ms},
              {"rules", {{"price_mode", "tick"}, {"price_tick", "0.01"}, {"amount_tick", "0.1"},
                         {"min_amount", "0.15"}, {"min_cost", "20.0000000000000000001"}}}};
    J plan {{"schema", "grid-research-sweep-plan-v1"}, {"mode", "validation"}, {"sources", J::array({source})},
            {"configurations", J::array({{{"id", "first\"\\"}, {"step_pct", "1"}, {"tp_pct", "10"}},
                                         {{"id", "second"}, {"step_pct", "1.5"}, {"tp_pct", "2"}}})},
            {"threads", 2}, {"block_bytes", 4096}, {"max_timestamp_groups", 1}, {"audit_threads", 2},
            {"reader_buffer_records", 256}, {"trace", true},
            {"runtime_limits", {{"rss_bytes", 0}, {"min_available_bytes", 0}, {"min_free_disk_bytes", 0}, {"max_output_bytes", 0}}}};
    const auto invoke = [&](J member, const std::string& id) {
        const auto path = root / (id + ".json"); write(path, member); return grid::sweep::run(path.string());
    };
    plan["output_dir"] = (root / "eager").string();
    check(invoke(plan, "eager-plan") == 0, "eager replay failed");
    const auto eager = read(root / "eager/summary.json");
    check(eager["chart_mode"] == "eager" && eager["charts_available"] == true && fs::file_size(root / "eager/series.jsonl") > 0,
          "legacy plans must keep eager charts");
    check(eager["configurations"][0]["total"]["buy_count"].get<unsigned>() > 10, "fixture must exercise multiple BUY fills");
    {
        std::ifstream trades(root / "eager/trades.jsonl"); std::string line; std::getline(trades, line);
        const auto first_trade = J::parse(line);
        check(first_trade["quantity"] == "3/10" && first_trade["cost"] == "30/1",
              "minimum notional finer than money scale must require the next whole lot");
    }
    plan["chart_mode"] = "on_demand"; plan["scheduler"] = "dynamic"; plan["threads"] = 32;
    plan["output_dir"] = (root / "on-demand").string();
    check(invoke(plan, "on-demand-plan") == 0, "on-demand replay failed");
    const auto demand = read(root / "on-demand/summary.json");
    check(!demand["charts_available"].get<bool>() && fs::file_size(root / "on-demand/series.jsonl") == 0, "on-demand must not collect series");
    check(eager["configurations"] == demand["configurations"] && bytes(root / "eager/pairs.jsonl") == bytes(root / "on-demand/pairs.jsonl"),
          "chart policy and scheduling must preserve all financial totals and pair results");
    check(bytes(root / "eager/trades.jsonl") == bytes(root / "on-demand/trades.jsonl"), "chart policy must preserve canonical trade payloads");
    check(demand["fingerprint"]["pairs_sha256"] == hash(bytes(root / "on-demand/pairs.jsonl")), "pair fingerprint must hash retained exact bytes");
    plan["output_dir"] = (root / "resume").string(); plan["stop_after_blocks"] = 1; plan["checkpoint_seconds"] = 0;
    check(invoke(plan, "stop-plan") == 3, "explicit block stop must checkpoint");
    plan["stop_after_blocks"] = 0; plan["resume"] = true;
    check(invoke(plan, "resume-plan") == 0, "on-demand resume failed");
    const auto resumed = read(root / "resume/summary.json");
    check(resumed["fingerprint"] == demand["fingerprint"] && resumed["checkpoint_count"].get<unsigned>() >= 2,
          "resume must preserve fingerprint and cumulative checkpoint count");
    plan.erase("resume"); plan.erase("stop_after_blocks"); plan.erase("checkpoint_seconds");
    plan["mode"] = "calibration"; plan["trace"] = false; plan["max_records"] = 1000;
    plan["runtime_limits"] = {{"rss_bytes", 2ULL * 1024 * 1024 * 1024}, {"min_available_bytes", 1},
                              {"min_free_disk_bytes", 1}, {"max_output_bytes", 64 * 1024 * 1024}};
    plan["output_dir"] = (root / "batch").string();
    plan["benchmark_variants"] = J::array({{{"id", "static"}, {"scheduler", "static"}, {"keep_results", true}},
                                          {{"id", "dynamic"}, {"scheduler", "dynamic"}, {"keep_results", false}}});
    check(invoke(plan, "batch-plan") == 0, "shared-audit benchmark batch failed");
    const auto batch = read(root / "batch/batch-summary.json");
    check(batch["source_audit_passes"] == 2 && batch["variants"].size() == 2 &&
          batch["variants"][0]["fingerprint"] == batch["variants"][1]["fingerprint"], "batch must audit twice and retain equal exact fingerprints");
    check(fs::exists(root / "batch/static/pairs.jsonl") && !fs::exists(root / "batch/dynamic/pairs.jsonl"), "compact batch must retain only requested details");
    check(read(root / "batch/dynamic/summary.json")["status"] == "COMPLETE_DIAGNOSTIC", "batch member completion requires final source verification");
    plan["output_dir"] = (root / "rejected-batch").string();
    plan["benchmark_variants"][1]["sources"] = J::array();
    bool refused = false;
    try { (void)invoke(plan, "rejected-batch-plan"); } catch (const grid::Error&) { refused = true; }
    check(refused && !fs::exists(root / "rejected-batch"), "batch variants cannot alter frozen sources");
}
void grid_depth_paths() {
    std::array<char, 64> pattern{};
    const std::string name = "/tmp/grid-sweep-depth-XXXXXX";
    std::copy(name.begin(), name.end(), pattern.begin());
    const auto* created = ::mkdtemp(pattern.data()); check(created != nullptr, "depth fixture directory creation failed");
    const fs::path root(created);
    struct Cleanup { fs::path path; ~Cleanup() { std::error_code ignored; fs::remove_all(path, ignored); } } cleanup{root};
    constexpr std::int64_t first = 1700000040000;
    const auto source = [&](const std::string& source_name, const std::vector<float>& prices, const std::string& tick) {
        std::string payload;
        for (std::size_t i = 0; i < prices.size(); ++i) {
            const auto timestamp = static_cast<std::uint64_t>(first + static_cast<std::int64_t>(i) * grid::minute_ms);
            for (unsigned byte = 0; byte < 8; ++byte) payload += static_cast<char>((timestamp >> (byte * 8U)) & 255U);
            const auto bits = std::bit_cast<std::uint32_t>(prices[i]);
            for (unsigned phase = 0; phase < 4; ++phase)
                for (unsigned byte = 0; byte < 4; ++byte) payload += static_cast<char>((bits >> (byte * 8U)) & 255U);
        }
        const auto path = root / (source_name + ".bin");
        { std::ofstream file(path, std::ios::binary); file.write(payload.data(), static_cast<std::streamsize>(payload.size())); }
        return J{{"id", "okx:A/USDT"}, {"venue", "okx"}, {"symbol", "A/USDT"}, {"path", path.string()},
            {"sha256", hash(payload)}, {"records", prices.size()}, {"first_ms", first},
            {"last_ms", first + static_cast<std::int64_t>(prices.size() - 1) * grid::minute_ms},
            {"rules", {{"price_mode", "tick"}, {"price_tick", tick}, {"amount_tick", "1"}, {"min_amount", "1"}, {"min_cost", "0"}}}};
    };
    J configs = J::array();
    for (unsigned depth = 30; depth <= 60; depth += 5)
        configs.push_back({{"id", "depth" + std::to_string(depth)}, {"step_pct", "10"}, {"tp_pct", "10"},
            {"grid_depth_pct", std::to_string(depth)}});
    J plan {{"schema", "grid-research-sweep-plan-v1"}, {"mode", "validation"}, {"matrix_kind", "aggregate_tp_v1"},
        {"statistics_mode", "monthly_v1"}, {"sources", J::array({source("fall", {100, 30, 200, 1}, "1")})},
        {"configurations", configs}, {"threads", 1}, {"block_bytes", 4096}, {"max_timestamp_groups", 1},
        {"audit_threads", 1}, {"trace", true}, {"chart_mode", "on_demand"},
        {"runtime_limits", {{"rss_bytes", 0}, {"min_available_bytes", 0}, {"min_free_disk_bytes", 0}, {"max_output_bytes", 0}}}};
    const auto invoke = [&](J member, const std::string& id) {
        const auto path = root / (id + "-plan.json"); member["output_dir"] = (root / id).string();
        write(path, member); return grid::sweep::run(path.string());
    };
    check(invoke(plan, "sequential") == 0, "all seven depth sequential replay failed");
    const auto baseline = read(root / "sequential/summary.json");
    plan["threads"] = 32; plan["scheduler"] = "dynamic"; plan["max_timestamp_groups"] = 3;
    check(invoke(plan, "parallel") == 0, "all seven depth parallel replay failed");
    check(read(root / "parallel/summary.json")["configurations"] == baseline["configurations"] &&
          bytes(root / "parallel/pairs.jsonl") == bytes(root / "sequential/pairs.jsonl"),
          "depth changes must preserve sequential/parallel exact results across block sizes");
    // Decimal levels at 10%, rounded down to tick 1: 100,90,81,72,65,59,53,47,43.
    constexpr std::array<unsigned, 7> buy_counts{4, 5, 5, 6, 7, 8, 9};
    constexpr std::array<unsigned, 7> spent{343, 408, 408, 467, 520, 567, 610};
    constexpr std::array<unsigned, 7> sell_prices{95, 90, 90, 86, 82, 78, 75};
    plan["max_timestamp_groups"] = 1; plan["stop_after_blocks"] = 2; plan["checkpoint_seconds"] = 0;
    check(invoke(plan, "resume") == 3, "depth checkpoint stop failed");
    const auto before_sell = read(root / "resume/checkpoint.latest.json");
    for (std::size_t i = 0; i < buy_counts.size(); ++i) {
        const auto& state = before_sell["configurations"][i];
        check(state["grid_depth_pct"] == std::to_string(30 + i * 5) + "/1" &&
              state["pairs"][0]["metrics"]["buy_count"] == buy_counts[i] &&
              state["pairs"][0]["metrics"]["spent"] == std::to_string(spent[i]) + "/1" &&
              state["pairs"][0]["buy"] == "0/1" &&
              state["pairs"][0]["sell"] == std::to_string(sell_prices[i]) + "/1",
              "each depth must stop at the correct rounded level and keep the open basket SELL");
    }
    plan["resume"] = true; plan["stop_after_blocks"] = 3;
    check(invoke(plan, "resume") == 3, "depth checkpoint after SELL failed");
    const auto after_sell = read(root / "resume/checkpoint.latest.json");
    for (std::size_t i = 0; i < sell_prices.size(); ++i)
        check(after_sell["configurations"][i]["pairs"][0]["anchor"] == std::to_string(sell_prices[i]) + "/1",
              "coin SELL must anchor the next depth boundary to its execution price, not observed 200");
    plan["stop_after_blocks"] = 0;
    check(invoke(plan, "resume") == 0, "all seven depths checkpoint resume failed");
    check(read(root / "resume/summary.json")["configurations"] == baseline["configurations"] &&
          bytes(root / "resume/trades.jsonl") == bytes(root / "sequential/trades.jsonl"),
          "depth checkpoint resume must preserve every trade and exact financial output");

    auto legacy = plan; legacy.erase("resume"); legacy.erase("stop_after_blocks"); legacy.erase("checkpoint_seconds");
    legacy["configurations"] = J::array({configs.back()}); legacy["configurations"][0].erase("grid_depth_pct");
    check(invoke(legacy, "legacy") == 0, "legacy default depth replay failed");
    legacy["configurations"][0]["grid_depth_pct"] = "60";
    check(invoke(legacy, "explicit60") == 0, "explicit sixty percent replay failed");
    auto explicit_results = read(root / "explicit60/summary.json")["configurations"];
    explicit_results[0].erase("grid_depth_pct");
    check(explicit_results == read(root / "legacy/summary.json")["configurations"] &&
          bytes(root / "legacy/trades.jsonl") == bytes(root / "explicit60/trades.jsonl") &&
          bytes(root / "legacy/pairs.jsonl") == bytes(root / "explicit60/pairs.jsonl"),
          "explicit 60 percent must preserve legacy exact outcomes and canonical trade bytes");

    auto boundary = legacy;
    boundary["sources"] = J::array({source("boundary", {100, 70, 69, 1}, "10")});
    boundary["configurations"][0]["step_pct"] = "1";
    boundary["configurations"][0]["grid_depth_pct"] = "30";
    check(invoke(boundary, "boundary") == 0, "rounded boundary replay failed");
    std::ifstream trades(root / "boundary/trades.jsonl"); std::string line; unsigned count = 0;
    while (std::getline(trades, line)) {
        const auto trade = J::parse(line); ++count;
        check(trade["side"] == "BUY", "boundary fixture unexpectedly sold");
        if (trade["price"] == "70/1") check(trade["timestamp_ms"] == first + 2 * grid::minute_ms,
            "BUY at exact floor must wait for a strict source crossing");
    }
    check(count == 4, "rounding duplicates must be skipped and no BUY below the floor created");

    auto exit = boundary;
    exit["sources"] = J::array({source("scoped", {100, 104, 104, 1}, "1")});
    exit["configurations"][0]["step_pct"] = "10"; exit["configurations"][0]["tp_pct"] = "15";
    exit["configurations"][0]["global_tp_pct"] = "2";
    exit["stop_after_blocks"] = 3; exit["checkpoint_seconds"] = 0;
    check(invoke(exit, "scoped") == 3, "aggregate exit depth fixture failed");
    const auto scoped = read(root / "scoped/checkpoint.latest.json")["configurations"][0];
    check(scoped["pairs"][0]["anchor"] == "104/1" && scoped["pairs"][0]["metrics"]["buy_count"] == 2,
          "aggregate exit must reseed on the next observed price and refresh its depth anchor");
    exit["stop_after_blocks"] = 0; exit["resume"] = true;
    check(invoke(exit, "scoped") == 0, "aggregate depth fixture continuation failed");
    check(read(root / "scoped/summary.json")["configurations"][0]["total"]["buy_count"] == 5,
          "new aggregate-cycle floor must use 104 times 0.7, admitting 93,84,75 only");

    auto invalid = legacy;
    for (const auto& value : J::array({"25", "31", "65", "-30", "30.1", 30, nullptr})) {
        invalid["configurations"][0]["grid_depth_pct"] = value;
        bool refused = false;
        try { (void)invoke(invalid, "invalid"); } catch (const std::exception&) { refused = true; }
        check(refused && !fs::exists(root / "invalid"), "invalid depth must be rejected before output creation");
    }
    legacy["stop_after_blocks"] = 1; legacy["checkpoint_seconds"] = 0;
    check(invoke(legacy, "changed-depth") == 3, "depth identity checkpoint failed");
    legacy["resume"] = true; legacy["stop_after_blocks"] = 0; legacy["configurations"][0]["grid_depth_pct"] = "30";
    bool refused = false;
    try { refused = invoke(legacy, "changed-depth") != 0; } catch (const std::exception&) { refused = true; }
    check(refused && !fs::exists(root / "changed-depth/summary.json"), "checkpoint cannot resume under changed grid depth");
}
void aggregate_tp_paths() {
    std::array<char, 64> pattern{};
    const std::string name = "/tmp/grid-sweep-aggregate-XXXXXX";
    std::copy(name.begin(), name.end(), pattern.begin());
    const auto* created = ::mkdtemp(pattern.data()); check(created != nullptr, "aggregate fixture directory creation failed");
    const fs::path root(created);
    struct Cleanup { fs::path path; ~Cleanup() { std::error_code ignored; fs::remove_all(path, ignored); } } cleanup{root};
    constexpr std::int64_t first = 1700000040000;
    const auto source = [&](const std::string& venue, const std::string& symbol, const std::vector<float>& prices) {
        std::string payload;
        for (std::size_t i = 0; i < prices.size(); ++i) {
            const auto timestamp = static_cast<std::uint64_t>(first + static_cast<std::int64_t>(i) * grid::minute_ms);
            for (unsigned byte = 0; byte < 8; ++byte) payload += static_cast<char>((timestamp >> (byte * 8U)) & 255U);
            const auto bits = std::bit_cast<std::uint32_t>(prices[i]);
            for (unsigned phase = 0; phase < 4; ++phase)
                for (unsigned byte = 0; byte < 4; ++byte) payload += static_cast<char>((bits >> (byte * 8U)) & 255U);
        }
        const auto path = root / (venue + "-" + symbol + ".bin");
        { std::ofstream file(path, std::ios::binary); file.write(payload.data(), static_cast<std::streamsize>(payload.size())); }
        return J{{"id", venue + ":" + symbol + "/USDT"}, {"venue", venue}, {"symbol", symbol + "/USDT"},
            {"path", path.string()}, {"sha256", hash(payload)}, {"records", prices.size()}, {"first_ms", first},
            {"last_ms", first + static_cast<std::int64_t>(prices.size() - 1) * grid::minute_ms},
            {"rules", {{"price_mode", "tick"}, {"price_tick", "0.01"}, {"amount_tick", "1"}, {"min_amount", "1"}, {"min_cost", "0"}}}};
    };
    J plan {{"schema", "grid-research-sweep-plan-v1"}, {"mode", "validation"}, {"matrix_kind", "aggregate_tp_v1"},
        {"statistics_mode", "monthly_v1"},
        {"sources", J::array({source("okx", "A", {499, 523, 523}), source("gate", "B", {499})})},
        {"configurations", J::array({{{"id", "both"}, {"step_pct", "1"}, {"tp_pct", "15"},
                                      {"exchange_tp_pct", "2"}, {"global_tp_pct", "2"}}})},
        {"threads", 32}, {"scheduler", "dynamic"}, {"block_bytes", 4096}, {"max_timestamp_groups", 1},
        {"audit_threads", 2}, {"reader_buffer_records", 256}, {"trace", true}, {"chart_mode", "on_demand"},
        {"runtime_limits", {{"rss_bytes", 0}, {"min_available_bytes", 0}, {"min_free_disk_bytes", 0}, {"max_output_bytes", 0}}}};
    const auto invoke = [&](J member, const std::string& id) {
        const auto path = root / (id + "-plan.json"); member["output_dir"] = (root / id).string();
        write(path, member); return grid::sweep::run(path.string());
    };
    check(invoke(plan, "global") == 0, "aggregate global replay failed");
    const auto summary = read(root / "global/summary.json");
    check(!summary.contains("statistics_calendar_scope") &&
          summary["configurations"][0]["performance"]["venues"][0]["overall"]["end_ms"] ==
              summary["configurations"][0]["performance"]["total"]["overall"]["end_ms"],
          "legacy stale-quote replay must retain its shared monthly calendar");
    const auto& result = summary.at("configurations").at(0);
    const auto& overall = result.at("performance").at("total").at("overall");
    check(overall["net_profit_closed"] == "5489/250" && overall["net_loss_closed"] == "499/250" &&
          overall["profit_factor"] == "11/1" && overall["profit_factor_status"] == "available" &&
          overall["sample_months"] == 0 && overall["sharpe_status"] == "insufficient_months" &&
          overall["spent"] == "523/1" && overall["peak_spent"] == "998/1",
          "monthly results must allocate both fees to closed positions and retain open exposure");
    check(result["total"]["realized_gross"] == "24/1" && result["total"]["fees"] == "2543/500" &&
          result["total"]["total_pnl"] == "9457/500" && result["total"]["peak_spent"] == "998/1",
          "global equality threshold must include exact buy and exit fees and pre-exit simultaneous peak");
    check(result["total"]["buy_count"] == 3 && result["total"]["sell_count"] == 2 &&
          result["aggregate_cycles"]["global"]["completed_count"] == 1,
          "global exit must own simultaneous venue signals without double fills");
    check(result["aggregate_cycles"]["global"]["baseline_realized_gross"] == "24/1" &&
          result["aggregate_cycles"]["global"]["baseline_fees"] == "101/25" &&
          result["aggregate_cycles"]["global"]["peak_spent"] == "523/1", "global cycle baselines must exclude its new seed fee");
    check(result["pair_fingerprint"]["sha256"] == summary["fingerprint"]["pairs_sha256"] &&
          result["pair_fingerprint"]["rows"] == 2 &&
          result["pair_fingerprint"]["bytes"] == fs::file_size(root / "global/pairs.jsonl"), "per-config fingerprint must bind exact pair details");
    {
        std::ifstream input(root / "global/trades.jsonl"); std::string line; unsigned stale_exits = 0, new_seeds = 0;
        while (std::getline(input, line)) {
            const auto trade = J::parse(line);
            if (trade.contains("exit_scope")) {
                check(trade["exit_scope"] == "global" && trade["timestamp_ms"] == first + grid::minute_ms,
                      "global priority and modeled execution timestamp");
                if (trade["source_id"] == "gate:B/USDT") {
                    check(trade["quote_timestamp_ms"] == first + 3000 && trade["price"] == "499/1", "EOF exit must retain stale quote timestamp and price");
                    ++stale_exits;
                }
            }
            if (trade["side"] == "BUY" && trade["timestamp_ms"].get<std::int64_t>() > first) {
                check(trade["timestamp_ms"] == first + grid::minute_ms + 1000 && trade["source_id"] == "okx:A/USDT" &&
                      trade["price"] == "523/1", "new seed must wait for next real point, including the next OHLC phase");
                ++new_seeds;
            }
        }
        check(stale_exits == 1 && new_seeds == 1, "stale position must close once and never rebuy without an observation");
    }
    plan["stop_after_blocks"] = 2; plan["checkpoint_seconds"] = 0;
    check(invoke(plan, "resume") == 3, "aggregate checkpoint stop failed");
    plan["stop_after_blocks"] = 0; plan["resume"] = true;
    check(invoke(plan, "resume") == 0, "aggregate checkpoint restore failed");
    check(read(root / "resume/summary.json")["fingerprint"] == summary["fingerprint"], "aggregate cycle and stale reseed restoration must be exact");
    plan.erase("resume"); plan.erase("stop_after_blocks"); plan.erase("checkpoint_seconds");
    plan["chart_mode"] = "eager"; plan["chart_pair_source_id"] = "gate:B/USDT";
    check(invoke(plan, "pair-chart") == 0, "aggregate pair graph replay failed");
    check(read(root / "pair-chart/summary.json")["fingerprint"] == summary["fingerprint"], "pair chart must not change financial results or fingerprints");
    {
        std::ifstream input(root / "pair-chart/series.jsonl"); std::string line; bool observed_exit = false;
        while (std::getline(input, line)) {
            const auto row = J::parse(line);
            if (row["scope"] == "pair:gate:B/USDT" && row["spent_min"] == "0/1" && row["pnl_min"] == "-499/250")
                observed_exit = true;
        }
        check(observed_exit, "pair chart must include externally triggered stale-price closure");
    }
    plan["chart_mode"] = "on_demand"; plan.erase("chart_pair_source_id");
    plan["configurations"][0]["global_tp_pct"] = nullptr;
    plan["sources"][0] = source("okx", "C", {499, 511, 511});
    check(invoke(plan, "venue") == 0, "venue aggregate replay failed");
    const auto venue = read(root / "venue/summary.json")["configurations"][0];
    check(venue["total"]["sell_count"] == 1 && venue["aggregate_cycles"]["global"]["completed_count"] == 0 &&
          venue["aggregate_cycles"]["global"]["baseline_realized_gross"] == "0/1" &&
          venue["aggregate_cycles"]["venues"][0]["completed_count"] == 0 &&
          venue["aggregate_cycles"]["venues"][1]["completed_count"] == 1,
          "venue exit must reset only its own cycle and retain global realized history");
    plan["trace"] = false; plan["keep_results"] = false;
    check(invoke(plan, "compact") == 0 && !fs::exists(root / "compact/pairs.jsonl"), "aggregate compact result must omit pair detail files");
    check(read(root / "compact/summary.json")["configurations"] == J::array({venue}), "compact results must preserve exact totals and per-config detail fingerprint");
    const auto compact = read(root / "compact/summary.json")["fingerprint"];
    auto binding = venue["pair_fingerprint"]; binding["config_id"] = venue["id"];
    check(compact["pairs_hash_scope"] == "per_configuration" && !compact.contains("pairs_sha256") &&
          compact["pairs_fingerprints_sha256"] == hash(J::array({binding}).dump()) &&
          compact["pairs_bytes"] == binding["bytes"], "compact manifest must bind exact ordered per-config fingerprints");
    plan["trade_render_cache"] = false;
    plan["grid_level_cache"] = false;
    check(invoke(plan, "uncached") == 0 && read(root / "uncached/summary.json")["configurations"] == J::array({venue}),
          "trade text cache must preserve every exact result and trade digest");
    plan.erase("trade_render_cache");
    plan.erase("grid_level_cache");
    {
        auto directional = plan;
        directional["sources"] = J::array({source("okx", "Extrema", {100, 105, 102, 105, 102, 105, 102})});
        directional["configurations"][0]["step_pct"] = "10";
        directional["configurations"][0]["exchange_tp_pct"] = nullptr;
        directional["configurations"][0]["global_tp_pct"] = nullptr;
        directional["chart_mode"] = "eager";
        check(invoke(directional, "directional") == 0, "price-only directional replay failed");
        const auto direction_result = read(root / "directional/summary.json")["configurations"][0];
        const auto& total = direction_result["total"];
        check(total["buy_count"] == 1 && total["sell_count"] == 0 && total["total_pnl"] == "9/5" &&
              total["max_drawdown"] == "3/1" && total["max_drawdown_ms"] == first + 2 * grid::minute_ms &&
              total["peak_spent_ms"] == first && total["longest_hold_ms"] == 6 * grid::minute_ms + 3000,
              "no-trade rises, falls and ties must preserve exact extrema and their first dates");
        directional["stop_after_blocks"] = 3; directional["checkpoint_seconds"] = 0;
        check(invoke(directional, "directional-resume") == 3, "directional checkpoint failed");
        directional["stop_after_blocks"] = 0; directional["resume"] = true;
        check(invoke(directional, "directional-resume") == 0 &&
              read(root / "directional-resume/summary.json")["configurations"][0] == direction_result,
              "restored directional extrema must keep the original first-occurrence dates");
    }
    {
        auto successive = plan;
        successive["sources"] = J::array({source("gate", "First", {100, 103, 103}), source("okx", "Second", {100, 100, 104})});
        successive["configurations"][0]["exchange_tp_pct"] = "2";
        successive["configurations"][0]["global_tp_pct"] = "2";
        successive["trace"] = true;
        check(invoke(successive, "successive") == 0, "venue then global replay failed");
        const auto successive_result = read(root / "successive/summary.json")["configurations"][0];
        const auto& total = successive_result["total"];
        check(total["buy_count"] == 5 && total["sell_count"] == 3 && total["realized_gross"] == "7/1" &&
              total["fees"] == "41/25" && total["total_pnl"] == "134/25" && total["peak_spent"] == "207/1" &&
              successive_result["aggregate_cycles"]["global"]["last_exit_ms"] == first + 2 * grid::minute_ms,
              "venue sale, reseed fees and a new peak must refresh the later global value boundary");
        std::ifstream trades(root / "successive/trades.jsonl"); std::string line;
        unsigned venue_sales = 0, global_sales = 0;
        while (std::getline(trades, line)) {
            const auto trade = J::parse(line);
            if (!trade.contains("exit_scope")) continue;
            if (trade["exit_scope"] == "venue") {
                ++venue_sales;
                check(trade["timestamp_ms"] == first + grid::minute_ms, "venue exit timing changed");
            } else {
                ++global_sales;
                check(trade["timestamp_ms"] == first + 2 * grid::minute_ms, "global exit must wait for its exact later threshold");
            }
        }
        check(venue_sales == 1 && global_sales == 2, "aggregate exits must retain scope ownership and trade counts");
        successive["stop_after_blocks"] = 2; successive["checkpoint_seconds"] = 0;
        check(invoke(successive, "successive-resume") == 3, "venue-cycle checkpoint failed");
        successive["stop_after_blocks"] = 0; successive["resume"] = true;
        check(invoke(successive, "successive-resume") == 0 &&
              read(root / "successive-resume/summary.json")["configurations"][0] == successive_result &&
              bytes(root / "successive-resume/trades.jsonl") == bytes(root / "successive/trades.jsonl"),
              "restore after venue exit must rebuild the exact global cutoff and preserve all trade bytes");
    }
    plan["configurations"][0]["exchange_tp_pct"] = "2.25";
    bool refused = false;
    try { (void)invoke(plan, "bad-step"); } catch (const grid::Error&) { refused = true; }
    check(refused && !fs::exists(root / "bad-step"), "aggregate TP must reject values outside the approved half-point matrix");
    plan["configurations"][0]["exchange_tp_pct"] = "2"; plan["mode"] = "matrix_batch";
    plan["runtime_limits"] = {{"rss_bytes", 2ULL * 1024 * 1024 * 1024}, {"min_available_bytes", 1},
                              {"min_free_disk_bytes", 1}, {"max_output_bytes", 64 * 1024 * 1024}};
    refused = false;
    try { (void)invoke(plan, "missing-venues"); } catch (const grid::Error&) { refused = true; }
    check(refused && !fs::exists(root / "missing-venues"), "matrix batch must require all seven venues");
    for (const auto* venue_id : {"bingx", "bitfinex", "htx", "kucoin", "xt"})
        plan["sources"].push_back(source(venue_id, "D", {499}));
    check(invoke(plan, "matrix-batch") == 0 && read(root / "matrix-batch/summary.json")["status"] == "COMPLETE_BATCH",
          "bounded seven-venue matrix batch must complete full source histories");
    plan["max_records"] = 1; refused = false;
    try { (void)invoke(plan, "truncated-batch"); } catch (const grid::Error&) { refused = true; }
    check(refused && !fs::exists(root / "truncated-batch"), "matrix batch must reject diagnostic history truncation");
}
void venue_calendar_paths() {
    std::array<char, 64> pattern{};
    const std::string name = "/tmp/grid-sweep-calendar-XXXXXX";
    std::copy(name.begin(), name.end(), pattern.begin());
    const auto* created = ::mkdtemp(pattern.data()); check(created != nullptr, "calendar fixture directory creation failed");
    const fs::path root(created);
    struct Cleanup { fs::path path; ~Cleanup() { std::error_code ignored; fs::remove_all(path, ignored); } } cleanup{root};
    constexpr std::int64_t jan15 = 1705276800000, feb1 = 1706745600000, feb15 = 1707955200000;
    constexpr std::int64_t mar1 = 1709251200000, mar15 = 1710460800000;
    constexpr std::int64_t apr1 = 1711929600000, apr15 = 1713139200000;
    const auto source = [&](const std::string& venue, const std::vector<std::int64_t>& timestamps,
                            const std::vector<float>& prices = {}, const std::string& symbol = "A") {
        std::string payload;
        check(prices.empty() || prices.size() == timestamps.size(), "calendar fixture price count mismatch");
        std::size_t index = 0;
        for (const auto value : timestamps) {
            const auto timestamp = static_cast<std::uint64_t>(value);
            const auto bits = std::bit_cast<std::uint32_t>(prices.empty() ? 100.0F : prices[index]);
            for (unsigned byte = 0; byte < 8; ++byte) payload += static_cast<char>((timestamp >> (byte * 8U)) & 255U);
            for (unsigned phase = 0; phase < 4; ++phase)
                for (unsigned byte = 0; byte < 4; ++byte) payload += static_cast<char>((bits >> (byte * 8U)) & 255U);
            ++index;
        }
        const auto path = root / (venue + "-" + symbol + ".bin");
        { std::ofstream file(path, std::ios::binary); file.write(payload.data(), static_cast<std::streamsize>(payload.size())); }
        return J{{"id", venue + ":" + symbol + "/USDT"}, {"venue", venue}, {"symbol", symbol + "/USDT"},
            {"path", path.string()}, {"sha256", hash(payload)}, {"records", timestamps.size()},
            {"first_ms", timestamps.front()}, {"last_ms", timestamps.back()},
            {"rules", {{"price_mode", "tick"}, {"price_tick", "0.01"}, {"amount_tick", "1"},
                       {"min_amount", "1"}, {"min_cost", "0"}}}};
    };
    std::vector<std::int64_t> full_february;
    for (auto timestamp = feb1; timestamp < mar1; timestamp += grid::minute_ms) full_february.push_back(timestamp);
    const J source_ids = J::array({"bingx:A/USDT", "gate:A/USDT", "htx:A/USDT", "kucoin:A/USDT", "okx:A/USDT"});
    J plan {{"schema", "grid-research-sweep-plan-v1"}, {"mode", "validation"},
        {"matrix_kind", "aggregate_tp_v1"}, {"statistics_mode", "monthly_v1"},
        {"source_selection", {{"history_policy", "gap_free_own_span"},
            {"manifest_sha256", std::string(64, '0')},
            {"expected_venues", J::array({"bingx", "gate", "htx", "kucoin", "okx"})},
            {"source_ids", source_ids}, {"selected_source_ids", source_ids}}},
        {"sources", J::array({source("htx", full_february),
                              source("bingx", {mar15}), source("gate", {feb15}),
                              source("okx", {jan15}), source("kucoin", {apr15})})},
        {"configurations", J::array({{{"id", "a"}, {"step_pct", "1"}, {"tp_pct", "8"}},
                                     {{"id", "b"}, {"step_pct", "2"}, {"tp_pct", "8"}}})},
        {"start_ms", feb1}, {"end_ms", apr1}, {"threads", 1}, {"block_bytes", 1024 * 1024},
        {"max_timestamp_groups", 256}, {"audit_threads", 2}, {"reader_buffer_records", 256},
        {"trace", false}, {"chart_mode", "on_demand"}, {"summary_stdout", false},
        {"runtime_limits", {{"rss_bytes", 0}, {"min_available_bytes", 0},
                             {"min_free_disk_bytes", 0}, {"max_output_bytes", 0}}}};
    const auto invoke = [&](J member, const std::string& id) {
        const auto path = root / (id + "-plan.json"); member["output_dir"] = (root / id).string();
        write(path, member); return grid::sweep::run(path.string());
    };
    check(invoke(plan, "one") == 0, "single-worker venue calendar replay failed");
    const auto summary = read(root / "one/summary.json");
    check(summary["statistics_calendar_scope"] == "venue_own_observed_span_v1", "venue calendar policy marker missing");
    const auto& performance = summary["configurations"][0]["performance"];
    check(performance["total"]["overall"]["start_ms"] == feb1 &&
          performance["total"]["overall"]["end_ms"] == apr1 &&
          performance["total"]["overall"]["sample_months"] == 2,
          "portfolio calendar must retain the whole replay window");
    const auto& venues = performance["venues"];
    const auto& bingx = venues[0], gate = venues[1], htx = venues[2], kucoin = venues[3], okx = venues[4];
    check(bingx["venue"] == "bingx" && bingx["monthly"].size() == 1 &&
          bingx["monthly"][0]["start_ms"] == mar15 &&
          bingx["monthly"][0]["end_ms"] == mar15 + grid::minute_ms &&
          !bingx["monthly"][0]["complete_month"].get<bool>() &&
          bingx["overall"]["sample_months"] == 0 && bingx["overall"]["sharpe_status"] == "insufficient_months",
          "late venue must not gain zero months before its own first quote");
    check(gate["venue"] == "gate" && gate["monthly"].size() == 1 &&
          gate["monthly"][0]["start_ms"] == feb15 && gate["monthly"][0]["end_ms"] == feb15 + grid::minute_ms &&
          gate["overall"]["sample_months"] == 0,
          "early venue must stop after its last available minute");
    check(htx["venue"] == "htx" && htx["monthly"].size() == 1 &&
          htx["monthly"][0]["start_ms"] == feb1 && htx["monthly"][0]["end_ms"] == mar1 &&
          htx["monthly"][0]["complete_month"].get<bool>() &&
          htx["overall"]["sample_months"] == 1 && htx["overall"]["sharpe_status"] == "insufficient_months",
          "only full UTC months in a venue's observed span may enter Sharpe");
    check(kucoin["venue"] == "kucoin" && kucoin["monthly"].empty() &&
          kucoin["overall"]["start_ms"] == apr1 && kucoin["overall"]["end_ms"] == apr1 &&
          kucoin["overall"]["sample_months"] == 0 && kucoin["overall"]["buy_count"] == 0 &&
          okx["venue"] == "okx" && okx["monthly"].empty() &&
          okx["overall"]["start_ms"] == feb1 && okx["overall"]["end_ms"] == feb1 &&
          okx["overall"]["sample_months"] == 0 && okx["overall"]["buy_count"] == 0,
          "venues outside a diagnostic replay window must have no invented observations or trades");
    auto short_plan = plan;
    short_plan["configurations"] = J::array({plan["configurations"][0]});
    short_plan["max_records"] = 1;
    short_plan["detail_mode"] = "monthly_drawdown_v1";
    short_plan["chart_mode"] = "eager";
    check(invoke(short_plan, "early-stop") == 0, "early-stop detail calendar replay failed");
    const auto early = read(root / "early-stop/summary.json")["configurations"][0];
    constexpr auto early_end = feb1 + 3001;
    for (const auto venue : {0U, 1U, 3U}) {
        const auto& scoped = early["performance"]["venues"][venue];
        check(scoped["monthly"].empty() && scoped["overall"]["start_ms"] == early_end &&
              scoped["overall"]["end_ms"] == early_end && scoped["overall"]["sample_months"] == 0 &&
              early["detail"]["venues"][venue]["monthly"].empty(),
              "a capped replay before a venue's first quote must have an empty valid calendar and no detail rows");
    }
    plan["threads"] = 32;
    check(invoke(plan, "many") == 0, "parallel venue calendar replay failed");
    const auto parallel = read(root / "many/summary.json");
    check(parallel["configurations"] == summary["configurations"] &&
          parallel["fingerprint"] == summary["fingerprint"],
          "worker count must preserve exact financial and monthly results");
    plan["stop_after_blocks"] = 1; plan["checkpoint_seconds"] = 0;
    check(invoke(plan, "resume") == 3, "venue calendar checkpoint stop failed");
    plan["stop_after_blocks"] = 0; plan["resume"] = true;
    check(invoke(plan, "resume") == 0 &&
          read(root / "resume/summary.json")["configurations"] == summary["configurations"],
          "checkpoint restoration must preserve distinct venue calendars");
    auto capped = plan;
    capped.erase("stop_after_blocks"); capped.erase("checkpoint_seconds"); capped.erase("resume");
    capped["end_ms"] = apr15 + grid::minute_ms;
    capped["max_records"] = full_february.size() + 2;
    capped["trace"] = true;
    check(invoke(capped, "capped-uninterrupted") == 0,
          "uninterrupted capped venue calendar replay failed");
    const auto capped_baseline = read(root / "capped-uninterrupted/summary.json");
    check(capped_baseline["processed_records"] == full_february.size() + 2 &&
          capped_baseline["configurations"][0]["performance"]["venues"][0]["monthly"].size() == 1,
          "record cap must include the late venue's first observation and stop before the next venue");
    capped["stop_after_blocks"] = 1; capped["checkpoint_seconds"] = 0;
    check(invoke(capped, "capped-resume") == 3,
          "capped venue calendar checkpoint before late venue start failed");
    const auto checkpoint_progress = read(root / "capped-resume/progress.json");
    check(checkpoint_progress["last_ms"] < mar15 &&
          checkpoint_progress["processed_records"] < capped["max_records"],
          "checkpoint must precede the late venue's first observation");
    capped["stop_after_blocks"] = 0; capped["resume"] = true;
    check(invoke(capped, "capped-resume") == 0,
          "capped venue calendar resume across late venue start failed");
    const auto capped_restored = read(root / "capped-resume/summary.json");
    check(capped_restored["processed_records"] == capped_baseline["processed_records"] &&
          capped_restored["configurations"] == capped_baseline["configurations"] &&
          capped_restored["fingerprint"] == capped_baseline["fingerprint"] &&
          bytes(root / "capped-resume/trades.jsonl") == bytes(root / "capped-uninterrupted/trades.jsonl"),
          "capped checkpoint restore must preserve exact finances, monthly statistics, fingerprints and trade bytes");
    auto empty_span = plan;
    empty_span.erase("stop_after_blocks"); empty_span.erase("checkpoint_seconds"); empty_span.erase("resume");
    empty_span["sources"].push_back(source("okx", {apr15}, {}, "B"));
    empty_span["source_selection"]["source_ids"].push_back("okx:B/USDT");
    empty_span["source_selection"]["selected_source_ids"].push_back("okx:B/USDT");
    check(invoke(empty_span, "disjoint-empty") == 0, "disjoint venue coverage fixture failed");
    const auto absent = read(root / "disjoint-empty/summary.json")["configurations"][0]["performance"]["venues"][4];
    check(absent["monthly"].empty() && absent["overall"]["start_ms"] == feb1 &&
          absent["overall"]["end_ms"] == feb1 && absent["overall"]["sample_months"] == 0,
          "a diagnostic window between two disjoint source spans has no venue observations");
    auto stale = plan;
    stale.erase("stop_after_blocks"); stale.erase("checkpoint_seconds"); stale.erase("resume");
    stale["start_ms"] = feb15; stale["end_ms"] = feb15 + 2 * grid::minute_ms;
    stale["sources"] = J::array({source("bingx", {feb15, feb15 + grid::minute_ms}, {100, 110}),
                                  source("gate", {feb15}),
                                  source("htx", {feb15, feb15 + grid::minute_ms}, {100, 110}),
                                  source("kucoin", {feb15, feb15 + grid::minute_ms}, {100, 110}),
                                  source("okx", {feb15, feb15 + grid::minute_ms}, {100, 110})});
    stale["configurations"] = J::array({{{"id", "fresh-exit"}, {"step_pct", "1"},
                                          {"tp_pct", "15"}, {"global_tp_pct", "2"}}});
    check(invoke(stale, "fresh-quote") == 0, "fresh-quote exit fixture failed");
    const auto fresh = read(root / "fresh-quote/summary.json")["configurations"][0];
    check(fresh["total"]["sell_count"] == 0 && fresh["aggregate_cycles"]["global"]["completed_count"] == 0 &&
          fresh["performance"]["venues"][1]["overall"]["end_ms"] == feb15 + grid::minute_ms,
          "a stale Gate position must block a later global exit under gap-free policy");
    stale.erase("source_selection");
    check(invoke(stale, "legacy-stale-quote") == 0, "legacy stale-quote control fixture failed");
    const auto legacy = read(root / "legacy-stale-quote/summary.json");
    check(!legacy.contains("statistics_calendar_scope") &&
          legacy["configurations"][0]["aggregate_cycles"]["global"]["completed_count"] == 1 &&
          legacy["configurations"][0]["performance"]["venues"][1]["overall"]["end_ms"] ==
              legacy["configurations"][0]["performance"]["total"]["overall"]["end_ms"],
          "legacy stale exits must retain the preexisting shared reporting calendar");
}
}
int main() {
    try {
        constexpr std::uint32_t maximum = 0x7f7fffffU;
        for (const auto bits : std::array<std::uint32_t, 10>{1, 2, 0x007fffffU, 0x00800000U,
                0x3dcccccdU, 0x3f800000U, 0x42c80000U, 0x4b800000U, maximum - 1, maximum}) {
            const auto exact = grid::Float32Price{bits}.exact();
            check(grid::sweep::buy_cutoff(exact) == bits - 1, "strict BUY must exclude exact equality");
            check(grid::sweep::sell_cutoff(exact) == bits + 1, "strict SELL must exclude exact equality");
            if (bits < maximum) {
                const auto middle = (exact + grid::Float32Price{bits + 1}.exact()) / grid::Rational(2);
                check(grid::sweep::buy_cutoff(middle) == bits, "BUY cutoff must admit exact lower neighbour");
                check(grid::sweep::sell_cutoff(middle) == bits + 1, "SELL cutoff must admit exact upper neighbour");
            }
        }
        check(grid::sweep::buy_cutoff(grid::Rational()) == 0, "zero BUY must admit no positive source");
        check(grid::sweep::sell_cutoff(grid::Rational()) == 1, "zero SELL boundary must admit positive source");
        const auto above_maximum = grid::Float32Price{maximum}.exact() * grid::Rational(2);
        check(grid::sweep::buy_cutoff(above_maximum) == maximum, "above-range BUY cutoff");
        check(grid::sweep::sell_cutoff(above_maximum) == maximum + 1, "above-range SELL sentinel");
        const auto below_minimum = grid::Float32Price{1}.exact() / grid::Rational(2);
        check(grid::sweep::buy_cutoff(below_minimum) == 0, "below-range BUY sentinel");
        check(grid::sweep::sell_cutoff(below_minimum) == 1, "below-range SELL cutoff");
        replay_paths();
        grid_depth_paths();
        aggregate_tp_paths();
        venue_calendar_paths();
        std::cout << "research sweep strict float32 cutoff tests passed\n";
        return 0;
    } catch (const std::exception& error) { std::cerr << error.what() << '\n'; return 1; }
}
