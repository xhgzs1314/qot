#ifndef QOT_HASH_H
#define QOT_HASH_H
// ============================================================================
// QOT 字符串哈希 —— T-33 支撑件（被 vm_types.h 顶部与 qot_flatdict.h 共用）
// ============================================================================
#include <cstdint>
#include <cstring>

namespace qot
{

    inline uint64_t qot_hash_string(const char *s, size_t n)
    {
        // 8 字节块乘法混合（wyhash 风格精简版）：
        // 逐字节 FNV 的乘法链是串行依赖，短键吃不满流水线；按块读 + 常数乘 + 折叠，
        // 质量同量级、短键快 2~3 倍。仅用于哈希表内部分布，无对抗碰撞的安全假设。
        uint64_t h = 0x9E3779B97F4A7C15ULL ^ static_cast<uint64_t>(n);
        while (n >= 8)
        {
            uint64_t blk;
            std::memcpy(&blk, s, 8);
            h ^= blk;
            h *= 0xC2B2AE3D27D4EB4FULL;
            h ^= h >> 29;
            s += 8;
            n -= 8;
        }
        if (n > 0)
        {
            uint64_t tail = 0;
            for (size_t i = 0; i < n; i++)
                tail |= static_cast<uint64_t>(static_cast<unsigned char>(s[i])) << (8 * i);
            h ^= tail;
            h *= 0x165667B19E3779F9ULL;
            h ^= h >> 32;
        }
        h *= 0x27D4EB2F165667C5ULL;
        h ^= h >> 32;
        return h ? h : 1; // 0 保留给 FlatDict 的 EMPTY 标记
    }

} // namespace qot

#endif // QOT_HASH_H
