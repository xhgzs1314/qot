# -*- coding: utf-8 -*-
"""qotrt_hash 的 Python 侧逐位复刻 —— E3（常量键哈希编译期折叠）的**正确性生命线**。

对齐要点（改任一处都必须同步两边 + 重跑单测）：
  * 所有算术模 2^64（C 的 uint64 回绕）；
  * 8 字节块按**小端**装载（x86 的 memcpy 到 uint64）；
  * 尾部不足 8 字节按 `byte << 8*i` 低位在前拼接（等价于小端整数）；
  * 末了 `return h ? h : 1`（哈希 0 被抬成 1，与 C 一致）。

Python 3.9 兼容（R8）：只用 int.from_bytes / 位运算，不依赖 3.11+ 特性。
"""
from __future__ import annotations

_M64 = (1 << 64) - 1

# 与 qot_aot_runtime.h 的 qotrt_hash 常量逐位相同
_C1 = 0x9E3779B97F4A7C15
_C2 = 0xC2B2AE3D27D4EB4F
_C3 = 0x165667B19E3779F9
_C4 = 0x27D4EB2F165667C5


def qotrt_hash64(data: bytes) -> int:
    """逐位复刻 C 的 static uint64_t qotrt_hash(const char *s, size_t n)。"""
    n = len(data)
    h = (_C1 ^ n) & _M64
    i = 0
    while n - i >= 8:
        blk = int.from_bytes(data[i:i + 8], 'little')
        h ^= blk
        h = (h * _C2) & _M64
        h ^= h >> 29
        i += 8
    if i < n:
        tail = int.from_bytes(data[i:n], 'little')  # 等价 `byte << 8*i` 的低位在前
        h ^= tail
        h = (h * _C3) & _M64
        h ^= h >> 32
    h = (h * _C4) & _M64
    h ^= h >> 32
    return h if h else 1


def qotrt_hash64_str(s: str) -> int:
    """按 QOT 的口径取 utf-8 字节后哈希（与常量池 qotrt_str_shared 的 len 同源）。"""
    return qotrt_hash64(s.encode('utf-8'))


def c_literal(value: int) -> str:
    """C 的 uint64 字面量（带 ULL 后缀，防 32 位截断）。"""
    return '0x%016XULL' % (value & _M64)
