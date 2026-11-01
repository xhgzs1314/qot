# -*- coding: utf-8 -*-
"""E3 常量键哈希折叠：**Python 复刻 vs C 实现**的逐位对齐守卫（致命坑的唯一护栏）。

为什么这条单测是本任务的生命线：
  cgen 把 `d["alpha"]` 的哈希在编译期算好、作为字面量传进 qotrt_dict_find/at。
  C 侧拿这个哈希同时算【桶位】与【探测步长】——Python 算错一位，结果是**静默
  查不到键**（不崩、不报、返回 null）。没有任何运行期断言能发现它，只有这里的
  金值比对能发现。

金值的来历（可复现，不许手改）：
  testing/.artifacts/_hash_ref.cpp 直接 #include 运行时头、调真 qotrt_hash，
  用 g++ -std=c++17 -O2 编译后实跑打印。改了 qot_aot_runtime.h 的 qotrt_hash，
  必须重跑那个程序刷新这里的金值 —— 这正是「两边同步」的强制点。

覆盖的边界：空串（n=0 分支）、1 字节、7/8/9 字节（8 字节块循环的 0/1/2 次与
尾部路径）、13 字节（b7b 的真实键形）、控制字符与 0x7f（unsigned char 符号性）。
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
for p in (ROOT, HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

import qot_hash as Q  # noqa: E402

# (输入 str, C 侧 qotrt_hash 实跑输出) —— 由 _hash_ref.cpp 生成
GOLDEN = [
    ('', 11160912189956057513),
    ('a', 4026525379786585914),
    ('alpha', 16356139571106309704),
    ('beta', 2027855512209861794),
    ('gamma', 8689488768323995466),
    ('delta', 7784132765305722918),
    ('omega', 909077965016865293),
    ('01234567', 1128131620327516330),          # 恰好 8 字节：块循环 1 次、无尾部
    ('012345678', 268151766675332406),          # 9 字节：块 1 次 + 尾部 1 字节
    ('0123456789', 17227159331033951239),
    ('k0.000000', 5235844589996530198),         # b2c 的真实键形
    ('k23.000000', 1727438183746242654),
    ('999999.000000', 16712427004874036450),    # b7b 的真实键形（13 字节）
    ('5.000000', 1060094480731105835),          # 数字键（dict_set 的 tostring 口径）
    ('hello world', 10103409460399153279),
    ('abcdefghijklmnop', 2141702565863731216),
    ('\x01\x02\x03', 16048850033771958477),     # 控制字符
    ('\x7f\x41', 9709384434617272195),          # 0x7f 边界（unsigned char 符号性）
    ('x-tail', 896766363903223573),
    ('a\tb', 16318919919753929521),
]


class TestConstKeyHash(unittest.TestCase):
    def test_matches_c_impl_bit_for_bit(self):
        for s, expect in GOLDEN:
            got = Q.qotrt_hash64_str(s)
            self.assertEqual(got, expect,
                             'qotrt_hash 复刻漂移: %r -> %d (C 侧 %d)。'
                             '哈希算错会导致常量键静默查不到 —— 立即对账 '
                             'qot_hash.py 与 qot_aot_runtime.h 的 qotrt_hash'
                             % (s, got, expect))

    def test_range_and_sentinel(self):
        """值域必须在 (0, 2^64]：C 侧 return h?h:1 把 0 抬成 1，Python 必须同规则。"""
        for s, _e in GOLDEN:
            h = Q.qotrt_hash64_str(s)
            self.assertTrue(0 < h <= 0xFFFFFFFFFFFFFFFF,
                            '哈希必须落在 (0, 2^64]（0 被抬成 1，与 C 的 return h?h:1 一致）')
        self.assertEqual(Q.qotrt_hash64(b''), Q.qotrt_hash64_str(''))

    def test_c_literal_form(self):
        lit = Q.c_literal(Q.qotrt_hash64_str('alpha'))
        self.assertTrue(lit.startswith('0x') and lit.endswith('ULL'))
        self.assertEqual(int(lit[:-3], 16), Q.qotrt_hash64_str('alpha'))

    def test_block_boundary_shapes(self):
        """8 字节块的 0/1/2/3 次循环 + 各长度尾巴都要落在值域内（结构化覆盖）。

        不断言「不同长度必不同值」—— 哈希碰撞是合法的（32/64 位都可能碰），
        把它当断言是在给算法加它没承诺的性质。
        """
        # 注意：base 必须比 n 的上界长，否则 base[:n] 会在尾部截断成同一个串
        # （Python 切片越界不报错 —— 第一次写这个断言时就踩过，误报成哈希碰撞）。
        base = (b'abcdefghijklmnopqrstuvwxyz'
                b'ABCDEFGHIJKLMNOPQRSTUVWXYZ'
                b'0123456789' + bytes(range(0x20, 0x40)))
        self.assertGreater(len(base), 64)
        seen = set()
        for n in range(0, 48):
            h = Q.qotrt_hash64(base[:n])
            self.assertTrue(0 < h <= 0xFFFFFFFFFFFFFFFF)
            seen.add(h)
        # 48 个不同输入必须几乎全互异（允许极少量碰撞，不允许退化成常数）
        self.assertGreaterEqual(len(seen), 46, '哈希退化：48 个不同长度的输入撞成 %d 个值' % len(seen))


if __name__ == '__main__':
    unittest.main()
