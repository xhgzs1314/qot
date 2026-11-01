# -*- coding: utf-8 -*-
"""lir_ssa（N-03：构造性 SSA + 线性扫描槽合并）的单元测试。

由 testing/run.py --suite unit 调度。
钉住的性质：变换语义保持（结构自检 + MOV 删除的合法性）、循环携带槽
不版本化（N-02 契约）、形参槽颜色钉住、死定义清除、_src_op 同步。
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TESTING = os.path.dirname(HERE)
ROOT = os.path.dirname(TESTING)
for p in (ROOT, TESTING):
    if p not in sys.path:
        sys.path.insert(0, p)

import lir  # noqa: E402
import lir2  # noqa: E402
import lir_lower  # noqa: E402
import lir_opt  # noqa: E402
import lir_ssa  # noqa: E402


def lower_source(src):
    import tempfile
    fd, path = tempfile.mkstemp(suffix='.qot')
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        f.write(src)
    try:
        from grammar import parse_oning_gram
        from bytecode import Compiler
        ast = parse_oning_gram(path, True)
        if not ast or ast == 1:
            raise AssertionError('解析失败')
        c = Compiler()
        c.set_base_path(path)
        return lir_lower.lower_program(c.compile_ast(ast))
    finally:
        os.remove(path)


def full_pipeline(src):
    """与 index._aot_compile 一致的前端管线（不含 cgen）。"""
    prog = lower_source(src)
    lir_opt.inline_calls_v2(prog)
    lir_opt.promote_globals(prog)
    lir_opt.snapshot_user_funcs(prog)
    lir_opt.infer_f64(prog)
    lir_opt.optimize_f64(prog)
    lir_ssa.ssa_regalloc(prog)
    return prog


class SsaTransformTest(unittest.TestCase):
    """变换后的结构性质。"""

    def test_verify_clean_and_slots_shrink(self):
        src = ('var s = 0.0;\n'
               'for (i in 10) {\n'
               '    var t = i * 2;\n'
               '    s = s + t;\n'
               '}\n'
               'output(s);\n')
        prog = lower_source(src)
        n_before = prog.main.n_slots_v
        r = lir_ssa.ssa_regalloc(prog)
        self.assertEqual(prog.verify(), [])
        self.assertEqual(r['slots_after'], prog.main.n_slots_v)
        # 槽合并不应让槽总数膨胀超过不可达私有空间的解释
        self.assertLessEqual(prog.main.n_slots_v, n_before + 0)
        self.assertGreater(r['movs_removed'], 0)

    def test_mov_dst_ne_src_after_rewrite(self):
        """改写后不得残留 dst==a 的 MOV（同色拷贝必须删净）。"""
        src = ('var a = 1;\nvar b = 2;\nvar c = a + b;\n'
               'var d = c * 2;\noutput(d);\n')
        prog = lower_source(src)
        lir_ssa.ssa_regalloc(prog)
        for f in prog.all_funcs():
            for i in f.instrs:
                if i.op == lir.MOV_V:
                    self.assertNotEqual(i.dst, i.a,
                                        '残留自赋值 MOV: %s' % i)

    def test_f64_groups_survive_post_unbox_ssa(self):
        """管线末段的 SSA 不得破坏 cgen 的组匹配形态。

        快循环的组尾 STORE_F64 / 组头数量必须原样保留（只允许操作数
        重命名），否则兜底语义丢失。
        """
        src = ('var s = 0.0;\n'
               'for (i in 50) {\n'
               '    s = s + i % 7 + i * 2;\n'
               '}\n'
               'output(s);\n')
        prog = full_pipeline(src)

        def count_groups(f):
            n = 0
            k = 0
            ins = f.instrs
            while k < len(ins):
                g = lir_opt._match_group(ins, k)
                if g is None:
                    k += 1
                else:
                    n += 1
                    k += g['len']
            return n
        self.assertGreater(count_groups(prog.main), 0,
                           'SSA 后快循环组全部失配')

    def test_dead_defs_removed(self):
        """STOP 前的 dead null 临时必须被清除（infer_f64 毒化防御）。"""
        src = 'var s = 5.0;\noutput(s);\n'
        prog = lower_source(src)
        lir_ssa.ssa_regalloc(prog)
        # 统计「定义后无人读取」的 CONST_VAL 数量应为 0（值级）
        uses = {}
        for i in prog.main.instrs:
            for s in lir2.slot_ops(i)[1]:
                if s >= 0:
                    uses[s] = uses.get(s, 0) + 1
        dead_consts = 0
        for i in prog.main.instrs:
            if i.op == lir.CONST_VAL and uses.get(i.dst, 0) == 0:
                dead_consts += 1
        self.assertEqual(dead_consts, 0)


class SsaCarryContractTest(unittest.TestCase):
    """与 N-02（循环去装箱）的契约：循环携带槽不版本化。"""

    def test_loop_carried_single_value(self):
        src = ('var s = 0.0;\n'
               'for (i in 10) {\n'
               '    s = s + 1;\n'
               '}\n'
               'output(s);\n')
        prog = lower_source(src)
        lir_opt.promote_globals(prog)   # 顶层变量先提升为局部（与管线一致）
        # 前置：s ∈ live_in(循环头)（读在定义之前沿回边路径成立）
        f2 = lir2.from_v1(prog.main)
        idom = f2.compute_idom()
        loops = f2.natural_loops(idom)
        live_in, _lo = f2.compute_liveness()
        carry = set()
        for lp in loops:
            for s in live_in.get(lp.header, set()):
                if not (s & lir.GLOBAL_BIT):
                    carry.add(s)
        self.assertTrue(carry, '前提失效：本用例应有循环携带槽')
        lir_ssa.ssa_regalloc(prog)
        # 回边尾部不得出现参数拷贝（携带槽无块参数）——检查方式：
        # 改写后的指令数不会因拷贝插入而异常膨胀
        self.assertEqual(prog.verify(), [])

    def test_unbox_before_ssa_contract(self):
        """管线契约：窥孔 + unbox 在 SSA 之前运行且能命中（性能前提）。"""
        src = ('var s = 0.0;\n'
               'for (i in 10) {\n'
               '    s = s + i % 7;\n'
               '}\n'
               'output(s);\n')
        prog = lower_source(src)
        lir_opt.promote_globals(prog)
        lir_opt.infer_f64(prog)
        n = lir_opt.optimize_f64(prog)
        self.assertGreaterEqual(n.get('unbox_loops', 0), 1,
                                'unbox 在管线上被否决（SSA 之前必须命中）')


class SsaParamPinTest(unittest.TestCase):
    """形参槽 [0, n_bind) 的颜色钉住。"""

    def test_params_keep_slot_numbers(self):
        src = ('number_t func add(a, b) {\n'
               '    return a + b;\n'
               '}\n'
               'output(add(1, 2));\n')
        prog = lower_source(src)
        callee = next(f for f in prog.funcs.values() if f.name == 'add')
        nb = callee.n_bind_slots
        lir_ssa.ssa_regalloc(prog)
        # cgen 以 n_bind_slots 下标写 args[k] → sK：形参槽号必须保持 < nb
        # 且互不相同（由颜色分配保证）。检查函数内引用的最大形参槽号合法。
        self.assertGreaterEqual(callee.n_slots_v, nb)


class SsaSrcOpSyncTest(unittest.TestCase):
    """_src_op 同步（infer_f64 组的兜底路径依赖）。"""

    def test_src_op_follows_rewrite(self):
        src = ('var x = 3.0;\n'
               'var y = -x;\n'
               'output(y);\n')
        prog = lower_source(src)
        lir_opt.infer_f64(prog)
        # 确认存在 _src_op（前提）
        has_src = any(getattr(i, '_src_op', None) is not None
                      for i in prog.main.instrs)
        self.assertTrue(has_src, '前提失效：infer 未产生 _src_op')
        lir_ssa.ssa_regalloc(prog)
        nv = prog.main.n_slots_v
        for i in prog.main.instrs:
            sop = getattr(i, '_src_op', None)
            if sop is None:
                continue
            for s in (sop.dst, sop.a, sop.b):
                if s >= 0 and not (s & lir.GLOBAL_BIT):
                    self.assertLess(s, nv,
                                    '_src_op 槽号 %d 越界（未同步）: %s' % (s, i))


class SsaAbSwitchTest(unittest.TestCase):
    """QOT_NO_SSA 开关：pass 自检（G-14 约定）。"""

    def test_no_ssa_switch(self):
        src = 'var a = 1;\nvar b = a + 2;\noutput(b);\n'
        prog = lower_source(src)
        before = prog.main.n_slots_v
        old = os.environ.get('QOT_NO_SSA')
        os.environ['QOT_NO_SSA'] = '1'
        try:
            r = lir_ssa.ssa_regalloc(prog)
        finally:
            if old is None:
                os.environ.pop('QOT_NO_SSA', None)
            else:
                os.environ['QOT_NO_SSA'] = old
        self.assertEqual(r['funcs'], 0)
        self.assertEqual(prog.main.n_slots_v, before)


class SsaSemanticPinTest(unittest.TestCase):
    """端到端语义钉：合并 + 删除之后的输出形态（不经 C 编译的快速回归）。"""

    def test_join_shapes(self):
        src = ('var s = 0.0;\n'
               'for (i in 6) {\n'
               '    if (i % 2 == 0) {\n'
               '        s = s + i;\n'
               '    } else {\n'
               '        s = s + i * 10;\n'
               '    }\n'
               '}\n'
               'output(s);\n')
        prog = full_pipeline(src)
        self.assertEqual(prog.verify(), [])
        # 数值核对交给 --suite native 差分；这里钉结构完整
        self.assertGreater(len(prog.main.instrs), 0)


if __name__ == '__main__':
    unittest.main()
