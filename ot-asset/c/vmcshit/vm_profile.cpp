// ============================================================================
// QOT VM 剖析数据导出 ——  T-32
// 仅在 QOT_VM_PROFILE 剖析构建中编译进产物；生产构建里本文件内容为空壳。
// ============================================================================
#include "vm_profile.h"

#if QOT_PROFILE_ENABLED

#include <cstdio>
#include <cstdlib>
#include <string>
#include <sstream>
#ifdef _WIN32
#include <windows.h>
#endif

namespace qot
{

    ProfileCounters &profile_counters()
    {
        static ProfileCounters c;
        return c;
    }

    void profile_reset()
    {
        profile_counters().reset();
    }

    static double measure_tsc_hz()
    {
#ifdef _WIN32
        LARGE_INTEGER qpc0, qpc1, freq;
        uint64_t t0 = __rdtsc();
        QueryPerformanceCounter(&qpc0);
        Sleep(50);
        uint64_t t1 = __rdtsc();
        QueryPerformanceCounter(&qpc1);
        QueryPerformanceFrequency(&freq);
        double secs = double(qpc1.QuadPart - qpc0.QuadPart) / double(freq.QuadPart);
        if (secs <= 0)
            return 0.0;
        return double(t1 - t0) / secs;
#else
        return 0.0;
#endif
    }

    char *profile_dump_json()
    {
        ProfileCounters &c = profile_counters();
        double hz = measure_tsc_hz();
        std::ostringstream os;
        os << "{\"tsc_hz\":" << (uint64_t)hz;
        os << ",\"instrExecuted\":" << c.instrExecuted;
        os << ",\"totalRunCycles\":" << c.totalRunCycles;
        os << ",\"counters\":{";
        os << "\"builtinNameLookups\":" << c.builtinNameLookups;
        os << ",\"builtinCalls\":" << c.builtinCalls;
        os << ",\"userCalls\":" << c.userCalls;
        os << ",\"globalFallbackReads\":" << c.globalFallbackReads;
        os << ",\"globalFallbackWrites\":" << c.globalFallbackWrites;
        os << ",\"valueRetain\":" << c.valueRetain;
        os << ",\"valueRelease\":" << c.valueRelease;
        os << ",\"valueCopyCtor\":" << c.valueCopyCtor;
        os << ",\"valueMoveCtor\":" << c.valueMoveCtor;
        os << ",\"valueDtor\":" << c.valueDtor;
        os << ",\"stackGrows\":" << c.stackGrows;
        os << ",\"framePoolHits\":" << c.framePoolHits;
        os << ",\"framePoolMisses\":" << c.framePoolMisses;
        os << ",\"dictFind\":" << c.dictFind;
        os << ",\"dictInsert\":" << c.dictInsert;
        os << "},\"ops\":[";
        bool first = true;
        for (int op = 0; op < 256; op++)
        {
            if (c.opCount[op] == 0)
                continue;
            if (!first)
                os << ",";
            first = false;
            os << "[" << op << "," << c.opCount[op] << "," << c.opCycles[op] << "]";
        }
        os << "]}";
        const std::string s = os.str();
        char *out = static_cast<char *>(std::malloc(s.size() + 1));
        if (!out)
            return nullptr;
        std::memcpy(out, s.c_str(), s.size() + 1);
        return out;
    }

} // namespace qot

#else // 非剖析构建：保持符号存在但为空，导出表稳定

namespace qot
{
    void profile_reset() {}
    char *profile_dump_json() { return nullptr; }
} // namespace qot

#endif // QOT_PROFILE_ENABLED
