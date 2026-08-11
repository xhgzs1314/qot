/***
*    ____                _           _   ____           ___   ___ _____      ____          ___        __       __  ___   _ 
*   / ___|_ __ ___  __ _| |_ ___  __| | | __ ) _   _   / _ \ / _ |_   _|    / ___|___   __| \ \      / ___     \ \/ | | | |
*  | |   | '__/ _ \/ _` | __/ _ \/ _` | |  _ \| | | | | | | | | | || |_____| |   / _ \ / _` |\ \ /\ / / _ \_____\  /| |_| |
*  | |___| | |  __| (_| | ||  __| (_| | | |_) | |_| | | |_| | |_| || |_____| |__| (_) | (_| | \ V  V |  __|_____/  \|  _  |
*   \____|_|  \___|\__,_|\__\___|\__,_| |____/ \__, |  \__\_\\___/ |_|      \____\___/ \__,_|  \_/\_/ \___|    /_/\_|_| |_|
*                                              |___/                                                                       
*/
#include "voltak.hpp"
#include <iostream>
#include <vector>
#include <string>
#include <chrono>
#include <iomanip>
#include <regex>
#include <fstream>

struct ParsedLog {
    std::string ip;
    std::string timestamp;
    std::string method;
    std::string url;
    int status;
};

class NaiveLogParser {
public:
    ParsedLog parse(const std::string& line) {
        static const std::regex pattern(
            R"((\d+\.\d+\.\d+\.\d+)\s+-\s+-\s+\[(.*?)\]\s+\"(\w+)\s+(\/[^\s]*)\s+HTTP\/\d\.\d\"\s+(\d+))"
        );
        std::smatch match;
        if (!std::regex_search(line, match, pattern)) {
            throw std::runtime_error("parse failed: " + line);
        }
        return {match[1], match[2], match[3], match[4], std::stoi(match[5])};
    }
};

class OptimizedLogParser {
public:
    OptimizedLogParser(volatility::VolatilityTrackerExt& tracker, const std::string& key)
        : tracker_(tracker), key_(key) {}

    ParsedLog parse(const std::string& line) {
        if (auto cached = tracker_.get_stable_value<std::string>(key_)) {
            return parse_with_fixed_offsets(line);
        }
        ParsedLog result = expensive_parse(line);
        tracker_.observe(key_, std::string("nginx_fixed_format"));
        return result;
    }

private:
    ParsedLog expensive_parse(const std::string& line) {
        static std::regex pattern(
            R"((\d+\.\d+\.\d+\.\d+)\s+-\s+-\s+\[(.*?)\]\s+\"(\w+)\s+(\/[^\s]*)\s+HTTP\/\d\.\d\"\s+(\d+))"
        );
        std::smatch match;
        if (!std::regex_search(line, match, pattern)) {
            throw std::runtime_error("expensive_parse failed");
        }
        return {match[1], match[2], match[3], match[4], std::stoi(match[5])};
    }

    ParsedLog parse_with_fixed_offsets(const std::string& line) {
        size_t ip_end = line.find(' ');
        std::string ip = line.substr(0, ip_end);
        size_t time_start = line.find('[') + 1;
        size_t time_end = line.find(']', time_start);
        std::string timestamp = line.substr(time_start, time_end - time_start);
        size_t quote1 = line.find('"', time_end) + 1;
        size_t space_after_method = line.find(' ', quote1);
        std::string method = line.substr(quote1, space_after_method - quote1);
        size_t url_start = space_after_method + 1;
        size_t url_end = line.find(' ', url_start);
        std::string url = line.substr(url_start, url_end - url_start);
        size_t status_start = line.find_last_of(' ') + 1;
        int status = std::stoi(line.substr(status_start));
        return {ip, timestamp, method, url, status};
    }

    volatility::VolatilityTrackerExt& tracker_;
    std::string key_;
};

std::vector<std::string> generate_log_lines(int count) {
    std::vector<std::string> lines;
    lines.reserve(count);
    for (int i = 0; i < count; ++i) {
        lines.push_back(
            "192.168.1." + std::to_string(i % 255) + " - - [10/Oct/2025:13:55:36 +0800] "
            "\"GET /api/user?id=" + std::to_string(i) + "&name=test HTTP/1.1\" 200 1234"
        );
    }
    return lines;
}

void run_benchmark(const std::vector<std::string>& logs, bool use_optimized) {
    volatility::VolatilityTrackerExt tracker;
    OptimizedLogParser opt_parser(tracker, "log_format_key");
    NaiveLogParser naive_parser;

    auto start = std::chrono::steady_clock::now();
    for (const auto& line : logs) {
        if (use_optimized) {
            opt_parser.parse(line);
        } else {
            naive_parser.parse(line);
        }
    }
    auto end = std::chrono::steady_clock::now();
    double elapsed_ms = std::chrono::duration<double, std::milli>(end - start).count();
    std::cout << (use_optimized ? "震荡位优化" : "未优化") << " 解析 " << logs.size() << " 行"
              << " 耗时: " << std::fixed << std::setprecision(2) << elapsed_ms << " ms"
              << " 平均: " << (elapsed_ms * 1000 / logs.size()) << " us/行\n";
}

int main() {
    constexpr int LOG_COUNT = 100000;
    auto logs = generate_log_lines(LOG_COUNT);
    std::cout << "Nginx 日志解析器(Test)  ======\n";
    std::cout << "日志行数: " << LOG_COUNT << "\n\n";
    run_benchmark(logs, false);
    run_benchmark(logs, true);
    {
        volatility::VolatilityTrackerExt tracker;
        OptimizedLogParser learner(tracker, "log_format_key");
        for (int i = 0; i < 100; ++i) {
            learner.parse(logs[i]);
        }
        tracker.export_stable_to_file("log_format_cache.txt");

        volatility::VolatilityTrackerExt tracker2;
        tracker2.import_stable_from_file("log_format_cache.txt");
        OptimizedLogParser optimized_with_cache(tracker2, "log_format_key");

        auto start = std::chrono::steady_clock::now();
        for (const auto& line : logs) {
            optimized_with_cache.parse(line);
        }
        auto end = std::chrono::steady_clock::now();
        double elapsed_ms = std::chrono::duration<double, std::milli>(end - start).count();
        std::cout << "固化后优化解析 " << LOG_COUNT << " 行"
                  << " 耗时: " << elapsed_ms << " ms"
                  << " 平均: " << (elapsed_ms * 1000 / LOG_COUNT) << " us/行\n";
    }

    return 0;
}