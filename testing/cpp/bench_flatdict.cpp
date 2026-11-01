// FlatDict vs unordered_map 微基准 —— T-33 表示层收益的纯净度量。
// 工作负载：200k 插入（10 字符键）+ 200k 查找 + 100k 删除。
#include "../../ot-asset/c/vmcshit/vm_types.h"
#include <chrono>
#include <cstdio>
#include <string>
#include <unordered_map>
#include <vector>

using namespace qot;

static std::vector<std::string> make_keys(int n)
{
    std::vector<std::string> keys;
    keys.reserve(n);
    for (int i = 0; i < n; i++)
        keys.push_back("user_key_" + std::to_string(i));
    return keys;
}

int main()
{
    const int N = 200000;
    auto keys = make_keys(N);
    // QOT 运行时的真实键路径：键是 Value 字符串（常量池同一 StringBox），哈希缓存于 Box。
    std::vector<Value> keyvals;
    keyvals.reserve(N);
    for (int i = 0; i < N; i++)
        keyvals.push_back(Value(keys[i]));

    // ---------- FlatDict + 缓存哈希（运行时真实路径） ----------
    {
        FlatDict d;
        auto t0 = std::chrono::steady_clock::now();
        for (int i = 0; i < N; i++)
            d.at_h(keyvals[i].string_hash(), keyvals[i].asString()) = Value(double(i));
        auto t1 = std::chrono::steady_clock::now();
        double sink = 0;
        for (int r = 0; r < 3; r++)
            for (int i = 0; i < N; i++)
            {
                const Value *v = d.find_ptr_h(keyvals[i].string_hash(), keyvals[i].asString());
                if (v)
                    sink += v->asNumber();
            }
        auto t2 = std::chrono::steady_clock::now();
        std::printf("FlatDict+hsh  insert=%.1fms  3x lookup=%.1fms  (sink=%.0f)\n",
                    std::chrono::duration<double, std::milli>(t1 - t0).count(),
                    std::chrono::duration<double, std::milli>(t2 - t1).count(), sink);
    }
    // ---------- unordered_map（旧实现，键相同但每次重算哈希） ----------
    {
        std::unordered_map<std::string, Value> d;
        auto t0 = std::chrono::steady_clock::now();
        for (int i = 0; i < N; i++)
            d[keyvals[i].asString()] = Value(double(i));
        auto t1 = std::chrono::steady_clock::now();
        double sink = 0;
        for (int r = 0; r < 3; r++)
            for (int i = 0; i < N; i++)
            {
                auto it = d.find(keyvals[i].asString());
                if (it != d.end())
                    sink += it->second.asNumber();
            }
        auto t2 = std::chrono::steady_clock::now();
        std::printf("unordered_map insert=%.1fms  3x lookup=%.1fms  (sink=%.0f)\n",
                    std::chrono::duration<double, std::milli>(t1 - t0).count(),
                    std::chrono::duration<double, std::milli>(t2 - t1).count(), sink);
    }
    return 0;
}
