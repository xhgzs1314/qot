# -*- coding: utf-8 -*-
"""N-01 全局槽提升单测：安全判定 + 改写正确性 + F64 协同。"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TESTING = os.path.dirname(HERE)
ROOT = os.path.dirname(TESTING)
for p in (ROOT, TESTING):
    if p not in sys.path:
        sys.path.insert(0, p)

import lir_lower  # noqa: E402
import lir        # noqa: E402
import lir_opt    # noqa: E402
from lir import GLOBAL_BIT  # noqa: E402


def compile_qot_to_vmcode(qot_path):
    from grammar import parse_oning_gram, strip_line_numbers
    from bytecode import Compiler
    # 【GOD-SELF 】与产品前端同一条线（同 test_lir.py）
    from index import _prepare_ast
    ast = parse_oning_gram(qot_path)
    ast = _prepare_ast(ast, qot_path)
    if not ast or ast == 1:
        raise AssertionError('解析失败: %s' % qot_path)
    ast = strip_line_numbers(ast)
    c = Compiler()
    c.set_base_path(qot_path)
    return c.compile_ast(ast)


def lower_source(src):
    import tempfile
    fd, path = tempfile.mkstemp(suffix='.qot')
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        f.write(src)
    try:
        code = compile_qot_to_vmcode(path)
        return lir_lower.lower_program(code)
    finally:
        os.remove(path)


class PromoteGlobalsTest(unittest.TestCase):
    def test_pure_main_promoted(self):
        """只被 main 触碰的全局槽 → 全部提升为 main 局部槽。"""
        prog = lower_source('var a = 1;\nvar b = 2;\noutput(a + b);\n')
        n = lir_opt.promote_globals(prog)
        self.assertEqual(n, 2, 'a/b 两个全局槽提升（output 实参临时本就是局部槽）')
        gleft = [i for i in prog.main.instrs
                 for s in _all_slots(i) if s & GLOBAL_BIT]
        self.assertEqual(gleft, [], 'main 中不应残留全局槽引用')

    def test_function_touching_global_blocks_it(self):
        """其它函数【读取】外层全局（编译为 GLOBAL_BIT 槽）→ 该全局槽一票否决。

        语义注记（ 实证）：
          * VM 语义下函数内 `g = x` 的 g 是函数局部（编译器作用域，
            bound_global_write_vm 钉），不触碰真全局；
          * 函数名本身占一个全局槽（函数值 = 全局槽里的 CodeBlock 常量），
            AOT 的 CALL_USER 是直接 C 绑定、运行期不读该槽 → 提升无害；
          * 只有跨函数【读取】才编译为 GLOBAL_BIT 槽 → 阻断。"""
        src = ('var g = 1;\n'
               'any_t func f() {\n'
               '    return g;\n'
               '}\n'
               'output(f() + 1);\n'
               'output(g + 1);\n')
        prog = lower_source(src)
        n = lir_opt.promote_globals(prog)
        # g（槽 0）必须保持全局：main 中残留 GLOBAL_BIT|0 引用
        residual = [i for i in prog.main.instrs for s in _all_slots(i)
                    if s >= 0 and (s & GLOBAL_BIT) and (s & ~GLOBAL_BIT) == 0]
        self.assertTrue(residual, 'g 被函数读取 → 必须保持全局槽')

    def test_partial_promotion(self):
        """f 引用 gA、main 独占 gB → gA 保持、gB 提升。"""
        src = ('var ga = 1;\n'
               'var gb = 2;\n'
               'any_t func f(x) {\n'
               '    return ga + x;\n'
               '}\n'
               'output(f(1));\n'
               'output(gb + 1);\n')
        prog = lower_source(src)
        n = lir_opt.promote_globals(prog)
        self.assertGreaterEqual(n, 1)
        # main 中不再有 gb 的全局引用（gb 槽号=1）
        residual = [i for i in prog.main.instrs
                    for s in _all_slots(i)
                    if s >= 0 and (s & GLOBAL_BIT) and (s & ~GLOBAL_BIT) == 1]
        self.assertEqual(residual, [], 'gb 应被提升')
        # f 中 ga 保留
        f = list(prog.funcs.values())[0]
        self.assertTrue(any(s >= 0 and (s & GLOBAL_BIT) for i in f.instrs
                            for s in _all_slots(i)), 'f 中 ga 应保持全局')

    def test_require_blocks_all(self):
        """存在 REQUIRE → 全局一票否决（运行期代码加载可能触达任意全局）。"""
        prog = lower_source('var a = 1;\nrequire "somelib.dll";\noutput(a);\n')
        n = lir_opt.promote_globals(prog)
        self.assertEqual(n, 0)

    def test_promoted_slots_participate_in_f64(self):
        """提升后的槽必须以 (main, slot) 键参与 F64 形态分析（顺序契约）。"""
        src = 'var s = 0;\nfor (i in 10) {\ns = s + 1;\n}\noutput(s);\n'
        prog = lower_source(src)
        n = lir_opt.promote_globals(prog)
        self.assertGreaterEqual(n, 2)
        count = lir_opt.infer_f64(prog)
        self.assertGreater(count, 0, '提升槽上的数值算术应被 F64 改写')
        ops = [i.op for i in prog.main.instrs]
        self.assertIn(lir_opt.ADD_F, ops, '应出现裸 F64 加法')
        self.assertNotIn(GLOBAL_BIT, [s for i in prog.main.instrs
                                      for s in _all_slots(i)],
                         '全局位应清零')


def _all_slots(i):
    # 与 lir_opt.promote_globals._slots 保持同构（操作数完备性）
    out = []
    if i.op in (lir.CONST_VAL, lir.LOAD_F64, lir.STORE_F64, lir.MOV_V,
                lir.MOV_F, lir.BR_IF_NOT, lir.BR_IF, lir.BR_F, lir.RET_V,
                lir.UNARY_GEN, lir.CALL_DYNAMIC):
        out += [i.dst, i.a]
    if i.op == lir.ARITH_GEN:
        out += [i.dst, i.a, i.b]
    if i.op in (lir.ADD_F, lir.SUB_F, lir.MUL_F, lir.DIV_F, lir.MOD_F,
                lir.POW_F):
        out += [i.dst, i.a, i.b]
    if i.op == lir.CMP_F:
        out += [i.dst]
    if i.op == lir.NEG_F:
        out += [i.dst, i.a]
    if i.op == lir.INDEX_GET:
        out += [i.dst, i.a, i.b]
    if i.op == lir.INDEX_SET:
        out += [i.a, i.b, i.c]
    if i.op in (lir.CALL_USER, lir.CALL_BUILTIN, lir.MAKE_ARRAY,
                lir.MAKE_DICT):
        out += list(i.args)
    return [s for s in out if isinstance(s, int) and s >= 0]


if __name__ == '__main__':
    unittest.main()
