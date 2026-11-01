# -*- coding: utf-8 -*-
"""fuzz 基建单测 —— S-01：比较器、生成器确定性/合法性/停机性。"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TESTING = os.path.dirname(HERE)
ROOT = os.path.dirname(TESTING)
for p in (ROOT, TESTING):
    if p not in sys.path:
        sys.path.insert(0, p)
sys.path.insert(0, os.path.join(TESTING, 'fuzz'))

from compare import (compare_outputs, numbers_close, tokenize_line,
                     canonical_lines)                       # noqa: E402
from gen_program import GenOptions, generate_program      # noqa: E402


class CompareTest(unittest.TestCase):
    def test_number_format_equivalence(self):
        # interp "1" vs VM "1.000000"；全精度 vs %.6f 舍入
        self.assertIsNone(compare_outputs('1\n2.5\n', '1.000000\n2.500000\n'))
        self.assertIsNone(compare_outputs('0.30000000000000004\n',
                                          '0.300000\n'))

    def test_real_value_divergence_detected(self):
        self.assertIsNotNone(compare_outputs('4\n', '9.000000\n'))
        self.assertIsNotNone(compare_outputs('-7 % 3 = 2\n',
                                             '-7 % 3 = -1.000000\n'))

    def test_number_glued_to_text(self):
        # 行首数字紧贴文本：loose 阶段可对齐（'3zz' vs '3.000000zz'）
        self.assertIsNone(compare_outputs('3zz\n', '3.000000zz\n'))
        # 词中数字（'hello0xy'，数字前有字母）：结构性不可对齐——这类形态由
        # 生成器的拼接污染约束禁止（KNOWN-PRINT-FORMAT 家族），比较器必须
        # 如实报分歧而非静默放过
        self.assertIsNotNone(compare_outputs('hello0xy\n', 'hello0.000000xy\n'))

    def test_empty_vs_null_is_divergence(self):
        # D-10：interp 空行 vs VM "null" —— 归一化后行数不同，必须报分歧
        self.assertIsNotNone(compare_outputs('\n', 'null\n'))

    def test_nan_inf_and_big_ints(self):
        self.assertIsNone(compare_outputs('nan\ninf\n-inf\n', 'nan\ninf\n-inf\n'))
        # 解释器精确 int（>2^53）vs VM %.6f 全位打印：parse 后 ULP 邻位
        self.assertIsNone(compare_outputs(
            '123456789123456789123456789\n',
            '123456789123456789123456789.000000\n'))

    def test_token_structure_change_detected(self):
        self.assertIsNotNone(compare_outputs('a 1 b\n', 'a b 1\n'))
        self.assertIsNotNone(compare_outputs('true\n', 'false\n'))

    def test_empty_line_dropping(self):
        self.assertEqual(canonical_lines('a\n\n  \nb\n'), ['a', 'b'])


class GenProgramTest(unittest.TestCase):
    def test_determinism(self):
        a, _ = generate_program(12345)
        b, _ = generate_program(12345)
        self.assertEqual(a, b, '同 seed 必须逐字节复现（最小化=报 seed）')

    def test_seeds_differ(self):
        a, _ = generate_program(1)
        b, _ = generate_program(2)
        self.assertNotEqual(a, b)

    def test_batch_generation_smoke(self):
        """50 个 seed 全部产出非空、含 output、语法面完整的程序。"""
        for seed in range(50):
            src, info = generate_program(seed, GenOptions(max_depth=5))
            self.assertIn('output(', src, seed)
            self.assertGreater(info['lines'], 0)

    def test_known_divergence_constraints(self):
        """生成约束抽查：不许出现已知分歧形态（抽查 200 个程序）。"""
        import re
        bad_mod = re.compile(r'%\s*-')
        for seed in range(200, 400):
            src, _ = generate_program(seed)
            # KNOWN-LOGIC-NUMERIC：&&/|| 操作数只能是 bool 型（比较/布尔）
            # —— 静态检查过粗，这里只抽查最明确的硬禁忌：
            self.assertIsNone(bad_mod.search(src) and '', seed)
            # KNOWN-EMPTY-STR：禁空串原子
            self.assertNotIn('""', src, seed)
            # KNOWN-UNARY-NEST：无嵌套一元负号（-- 且非注释）
            self.assertNotIn('--', src.replace('--', '--'), seed)


if __name__ == '__main__':
    unittest.main()
