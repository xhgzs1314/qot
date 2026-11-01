#ifndef VM_PROFILE_H
#define VM_PROFILE_H
// ============================================================================
// QOT VM 剖析插桩 ——  T-32（7.4 机器码 P0）
//
// 设计约束：
//   * 生产构建（不加 QOT_VM_PROFILE）所有剖析宏展开为空，零开销（R9：可构建且不退化）。
//   * 剖析构建用于把「每条指令的开销」拆成可归因项：每 opcode 的执行次数与周期数、
//     内置函数字符串哈希次数、Value 引用计数流量、操作数栈扩容、帧池命中等。
//   * 数据通过新增 DLL 导出 vm_profile_dump 取出（JSON），供 testing/perf/profile_run.py 聚合。
//
// 结构约束（本次踩过的坑）：#if/#endif 绝不允许包裹 namespace 的开括号或闭括号的一半，
//   否则另一侧构建配置下整个翻译单元被卷进 namespace（qot::std 事故，）。
// ============================================================================
#include <cstdint>
#include <cstddef>
#include <cstring>
#ifdef _MSC_VER
#include <intrin.h>
#pragma intrinsic(__rdtsc)
#endif

#if defined(QOT_VM_PROFILE)
#define QOT_PROFILE_ENABLED 1
#else
#define QOT_PROFILE_ENABLED 0
#endif

namespace qot
{

    // 两种构建下都完整定义：剖析构建用；生产构建不实例化、零成本。
    struct ProfileCounters
    {
        // 每 opcode：执行次数 / 消耗周期（__rdtsc 粗粒度）
        uint64_t opCount[256];
        uint64_t opCycles[256];
        uint64_t instrExecuted;

        // 归因计数器
        uint64_t builtinNameLookups;   // BuiltinManager 字符串哈希查找次数（每次调用 1~2 次）
        uint64_t builtinCalls;         // 内置函数调用
        uint64_t userCalls;            // 用户函数调用
        uint64_t globalFallbackReads;  // PUSH_VAR 走 varNames/m_globals 回退路径
        uint64_t globalFallbackWrites; // STORE_VAR 走回退路径
        uint64_t valueRetain;          // Value 引用计数 ++
        uint64_t valueRelease;         // Value 引用计数 --（含析构判断）
        uint64_t valueCopyCtor;
        uint64_t valueMoveCtor;
        uint64_t valueDtor;
        uint64_t stackGrows;           // OperandStack 扩容（搬迁全部元素）
        uint64_t framePoolHits;
        uint64_t framePoolMisses;
        uint64_t dictFind;             // 字典查找（哈希 + 探测）
        uint64_t dictInsert;
        uint64_t totalRunCycles;       // execute_fast 总周期

        ProfileCounters() { reset(); }
        void reset()
        {
            std::memset(this, 0, sizeof(*this));
        }
    };

    ProfileCounters &profile_counters();
    void profile_reset();
    // 返回 malloc 的 JSON 字符串（调用方负责 free）；非剖析构建返回 nullptr。
    char *profile_dump_json();

} // namespace qot

#if QOT_PROFILE_ENABLED
// 剖析构建的 per-opcode 计时宏（粗粒度 rdtsc，剖析专用，不用于生产判断）
#define QOT_PROF_OP_BEGIN() const uint64_t _qot_prof_t0 = __rdtsc()
#define QOT_PROF_OP_END(op)                                   \
    do                                                        \
    {                                                         \
        qot::ProfileCounters &_pc = qot::profile_counters();  \
        _pc.opCount[op]++;                                    \
        _pc.opCycles[op] += __rdtsc() - _qot_prof_t0;         \
        _pc.instrExecuted++;                                  \
    } while (0)
#define QOT_PROF_ADD(field, n) qot::profile_counters().field += (uint64_t)(n)
#define QOT_PROF_RUN_BEGIN() const uint64_t _qot_run_t0 = __rdtsc()
#define QOT_PROF_RUN_END() (qot::profile_counters().totalRunCycles += __rdtsc() - _qot_run_t0)
#else
#define QOT_PROF_OP_BEGIN() ((void)0)
#define QOT_PROF_OP_END(op) ((void)0)
#define QOT_PROF_ADD(field, n) ((void)0)
#define QOT_PROF_RUN_BEGIN() ((void)0)
#define QOT_PROF_RUN_END() ((void)0)
#endif

#endif // VM_PROFILE_H
