// ============================================================================
// FlatDict（T-33）C++ 级单元测试 —— 独立 exe，不进 DLL。
// 由 testing/cpp/run_cpp_tests.py 用 cl 编译执行。
// 覆盖：插入/覆盖/查找/删除（后退移位）/扩容/深拷贝/迭代/空串键/碰撞簇。
// ============================================================================
#include "../../ot-asset/c/vmcshit/vm_types.h"
#include <cstdio>
#include <string>
#include <vector>

using namespace qot;

static int g_failed = 0;
#define CHECK(cond)                                                          \
    do                                                                       \
    {                                                                        \
        if (!(cond))                                                         \
        {                                                                    \
            std::printf("  [FAIL] %s:%d  %s\n", __FILE__, __LINE__, #cond);  \
            g_failed++;                                                      \
        }                                                                    \
    } while (0)

static void test_basic()
{
    std::printf("basic insert/find/overwrite...\n");
    FlatDict d;
    CHECK(d.empty());
    d["a"] = Value(1.0);
    d["b"] = Value(2.0);
    d["a"] = Value(3.0); // 覆盖
    CHECK(d.size() == 2);
    CHECK(d.count("a") == 1);
    CHECK(d.count("zz") == 0);
    CHECK(d["a"].asNumber() == 3.0);
    CHECK(d["b"].asNumber() == 2.0);
    CHECK(!d.empty());
    auto it = d.find("b");
    CHECK(it != d.end());
    CHECK(it->first == "b");
    CHECK(d.find("zz") == d.end());
}

static void test_empty_key()
{
    std::printf("empty-string key...\n");
    FlatDict d;
    d[""] = Value(42.0);
    CHECK(d.size() == 1);
    CHECK(d[""].asNumber() == 42.0);
    CHECK(d.count("") == 1);
}

static void test_bulk_and_rehash()
{
    std::printf("bulk insert (forces rehash chain) + verify all...\n");
    FlatDict d;
    const int N = 2000;
    for (int i = 0; i < N; i++)
        d["k" + std::to_string(i)] = Value(double(i));
    CHECK(d.size() == (size_t)N);
    CHECK(d.bucket_count() >= (size_t)N); // 2 的幂扩容
    for (int i = 0; i < N; i++)
        CHECK(d["k" + std::to_string(i)].asNumber() == double(i));
    CHECK(d.count("nope") == 0);

    // 删除一半，验证后退移位后其余键仍可寻址
    for (int i = 0; i < N; i += 2)
        CHECK(d.erase("k" + std::to_string(i)) == 1);
    CHECK(d.size() == (size_t)N / 2);
    for (int i = 1; i < N; i += 2)
        CHECK(d["k" + std::to_string(i)].asNumber() == double(i));
    for (int i = 0; i < N; i += 2)
        CHECK(d.count("k" + std::to_string(i)) == 0);

    // 删除后重插同一键
    d["k0"] = Value(999.0);
    CHECK(d["k0"].asNumber() == 999.0);
    CHECK(d.size() == (size_t)N / 2 + 1);
}

static void test_erase_all()
{
    std::printf("erase everything, reuse...\n");
    FlatDict d;
    for (int i = 0; i < 500; i++)
        d[std::to_string(i)] = Value(double(i));
    for (int i = 0; i < 500; i++)
        d.erase(std::to_string(i));
    CHECK(d.size() == 0);
    CHECK(d.begin() == d.end());
    for (int i = 0; i < 500; i++)
        d[std::to_string(i)] = Value(double(i) * 2);
    CHECK(d.size() == 500);
    for (int i = 0; i < 500; i++)
        CHECK(d[std::to_string(i)].asNumber() == double(i) * 2);
}

static void test_copy_semantics()
{
    // 注意：QOT 的 Value 是引用语义（与旧 unordered_map 实现一致）：
    // 拷贝 dict 拷贝的是【键集 + Value 句柄】，堆对象（数组/字符串）本身共享。
    // 本测试钉死两件事：键集独立（增删互不影响）+ 值引用共享（与旧实现一致）。
    std::printf("copy: key-set independent, values shared by refcount...\n");
    FlatDict a;
    a["x"] = Value::createArray();
    a["x"].asArray().push_back(Value(1.0));
    FlatDict b = a; // 拷贝：键集独立，x 的数组共享
    b["y"] = Value(7.0);
    CHECK(a.count("y") == 0);
    CHECK(b.count("y") == 1);
    b.erase("x");
    CHECK(a.count("x") == 1);
    CHECK(b.count("x") == 0);
    CHECK(a["x"].asArray().size() == 1); // a 的键还在，数组可访问
    // 引用共享：通过 b 重新放回 a["x"] 的拷贝，改数组两边可见（Value 语义）
    FlatDict c;
    c = a;
    c["x"].asArray().push_back(Value(2.0));
    CHECK(a["x"].asArray().size() == 2);
    CHECK(c["x"].asArray().size() == 2);
    // 标量（非堆）值拷贝后互不影响
    a["n"] = Value(1.0);
    c = a;
    c["n"] = Value(100.0);
    CHECK(a["n"].asNumber() == 1.0);
    CHECK(c["n"].asNumber() == 100.0);
}

static void test_iteration_slot_order()
{
    std::printf("iteration = slot order, deterministic...\n");
    FlatDict d;
    std::vector<std::string> keys;
    for (int i = 0; i < 100; i++)
        keys.push_back("it" + std::to_string(i));
    for (auto &k : keys)
        d[k] = Value(1.0);
    // 迭代一遍记录顺序
    std::vector<std::string> order1;
    for (const auto &p : d)
        order1.push_back(p.first);
    CHECK(order1.size() == 100);
    // 再迭代一遍必须一致（确定性）
    size_t idx = 0;
    for (const auto &p : d)
    {
        CHECK(p.first == order1[idx]);
        idx++;
    }
}

static void test_value_refcount_via_dict()
{
    std::printf("Value refcount across dict ops...\n");
    FlatDict d;
    {
        Value s = Value(std::string("shared"));
        d["k"] = s; // retain
        CHECK(s.asString().size() == 6);
    } // s 析构 → 释放交给 dict 持有
    CHECK(d["k"].isString());
    CHECK(d["k"].asString() == "shared");
}

static void test_structured_binding()
{
    std::printf("structured binding + const iteration...\n");
    FlatDict d;
    d["p"] = Value(1.0);
    d["q"] = Value(2.0);
    const FlatDict &cd = d;
    double sum = 0;
    size_t n = 0;
    for (const auto &[k, v] : cd)
    {
        sum += v.asNumber();
        n++;
        (void)k;
    }
    CHECK(n == 2);
    CHECK(sum == 3.0);
}

int main()
{
    std::printf("== FlatDict unit tests ==\n");
    test_basic();
    test_empty_key();
    test_bulk_and_rehash();
    test_erase_all();
    test_copy_semantics();
    test_iteration_slot_order();
    test_value_refcount_via_dict();
    test_structured_binding();
    if (g_failed)
    {
        std::printf("== %d CHECK(s) FAILED ==\n", g_failed);
        return 1;
    }
    std::printf("== ALL FLATDICT TESTS PASS ==\n");
    return 0;
}
