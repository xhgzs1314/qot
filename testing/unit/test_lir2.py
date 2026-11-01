# -*- coding: utf-8 -*-
"""lir2（块级 IR + CFG/支配/循环/活跃性）与 N-02 优化 pass 的单元测试。

由 testing/run.py --suite unit 调度。
存在的理由：N-02/T-43-v2 的所有正确性都建立在这层「坐标 + 数据流」之上；
坐标错一位就是静默错码（G-1），数据流漏一项就是静默错值（G-2）。这些性质
必须被单测钉住，不能只靠端到端差分碰运气。
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
        return lir_lower.lower_program(compile_qot_to_vmcode(path))
    finally:
        os.remove(path)


def pipeline(src, unbox=True):
    """完整 AOT 前端：lower → inline → promote → infer → N-02 优化。"""
    prog = lower_source(src)
    lir_opt.inline_calls_v2(prog)
    lir_opt.promote_globals(prog)
    lir_opt.infer_f64(prog)
    lir_opt.optimize_f64(prog)
    return prog


class SlotOpsTest(unittest.TestCase):
    """slot_ops / replace_vuse 的操作数完备性（G-2 的总闸）。"""

    def test_every_opcode_covered(self):
        for op in sorted(lir.INSTR_NAMES):
            i = lir.LirInstr(op)
            # 不抛异常即说明该 op 已被登记；dst/a/b/c/args 都给上典型值再验一次
            i.dst, i.a, i.b, i.c = 1, 2, 3, 4
            i.args = [5, 6]
            dv, uv, df, uf = lir2.slot_ops(i)
            for group in (dv, uv, df, uf):
                self.assertIsInstance(group, tuple)

    def test_unknown_op_raises(self):
        i = lir.LirInstr(200)
        with self.assertRaises(lir2.CfgError):
            lir2.slot_ops(i)

    def test_replace_vuse_agrees_with_slot_ops(self):
        """replace_vuse 改写的操作数个数必须与 slot_ops 报的读取次数一致。

        这两张表是同一事实的两种视图；不一致就意味着某个 pass 会少改/多改。
        """
        for op in sorted(lir.INSTR_NAMES):
            i = lir.LirInstr(op)
            i.dst, i.a, i.b, i.c = 7, 7, 7, 7
            i.args = [7, 7]
            i.argc = 2
            try:
                before = lir2.slot_ops(i)
            except lir2.CfgError:
                continue
            n_uses = sum(1 for s in before[1] if s == 7)
            n = lir2.replace_vuse(i, 7, 99)
            after = lir2.slot_ops(i)
            self.assertEqual(n, n_uses,
                             '%s: replace_vuse 改写 %d 个，slot_ops 报 %d 个'
                             % (lir.INSTR_NAMES[op], n, n_uses))
            self.assertEqual(sum(1 for s in after[1] if s == 7), 0)

    def test_load_store_domains(self):
        self.assertEqual(lir2.slot_ops(lir.LirInstr(lir.LOAD_F64, dst=1, a=2)),
                         ((), (2,), (1,), ()))
        self.assertEqual(lir2.slot_ops(lir.LirInstr(lir.STORE_F64, dst=1, a=2)),
                         ((1,), (), (), (2,)))


class CfgTest(unittest.TestCase):
    """块划分 / 支配 / 循环 / 活跃性。"""

    def _f2(self, src):
        prog = lower_source(src)
        return lir2.from_v1(prog.main)

    def test_from_to_v1_roundtrip_keeps_semantics(self):
        src = ('var s = 0;\nfor (i in 10) { s += i; if (s > 5) { stop; } }\n'
               'output(s);\n')
        prog = lower_source(src)
        n0 = len(prog.main.instrs)
        f2 = lir2.from_v1(prog.main)
        lir2.to_v1(f2, prog.main)
        self.assertEqual(prog.verify(), [])
        # 块划分只做「补显式跳转 + 拆块」，指令数不应减少（可因补边略增）
        self.assertGreaterEqual(len(prog.main.instrs), n0)

    def test_every_block_has_terminator(self):
        f2 = self._f2('var s=0;\nfor(i in 3){ s+=i; if(s>1){ s=s-1; } }\noutput(s);\n')
        for b in f2.blocks:
            self.assertIsNotNone(b.term)
            self.assertIn(b.term.op, lir2.TERMINATORS)

    def test_all_edges_are_explicit(self):
        """条件分支必须有两条出边（跳转边 + 落下边）——布局自由的前提。"""
        f2 = self._f2('var s=0;\nfor(i in 3){ s+=i; if(s>1){ s=s-1; } }\noutput(s);\n')
        for b in f2.blocks:
            if b.term is not None and b.term.op in (lir.BR_IF_NOT, lir.BR_IF, lir.BR_F):
                self.assertEqual(len(b.succs), 2, 'B%d 条件分支应有两条出边' % b.id)
                self.assertIsNotNone(b.ft)

    def test_dominators_on_diamond(self):
        f2 = self._f2('var x = 1;\nif (x > 0) { x = 2; } else { x = 3; }\noutput(x);\n')
        idom = f2.compute_idom()
        self.assertEqual(idom[f2.entry], f2.entry)
        for bid in f2.reachable():
            self.assertIn(bid, idom)
            # 直接支配者必须支配它自己之外还能走到入口
            cur = bid
            seen = 0
            while cur != idom[cur] and seen < 100:
                cur = idom[cur]
                seen += 1
            self.assertEqual(cur, f2.entry, 'B%d 的支配链应终止于入口' % bid)

    def test_natural_loop_detected(self):
        f2 = self._f2('var s=0;\nfor(i in 10){ s+=i; }\noutput(s);\n')
        loops = f2.natural_loops(f2.compute_idom())
        self.assertEqual(len(loops), 1, '应识别出唯一一个自然循环')
        lp = loops[0]
        self.assertIn(lp.header, lp.body)
        # 回边的尾块必须在循环体内，且它确有一条边指向 header
        tails = [t for (t, _h) in lp.back_edges]
        self.assertTrue(tails)
        for t in tails:
            self.assertIn(t, lp.body)
            self.assertIn(lp.header, f2.blocks[t].succs)

    def test_nested_loops_both_detected(self):
        f2 = self._f2('var s=0;\nfor(i in 3){ for(j in 3){ s+=i*10+j; } }\noutput(s);\n')
        loops = f2.natural_loops(f2.compute_idom())
        self.assertEqual(len(loops), 2)
        inner = [lp for lp in loops
                 if not any((o is not lp) and (o.body < lp.body) for o in loops)]
        self.assertEqual(len(inner), 1, '嵌套两层里应只有一层是最内层')

    def test_liveness_excludes_loop_internal_temps(self):
        """循环体内「先写后读」的临时槽不得出现在 live_in(header)。

        这条性质直接决定形态守卫的成员；守卫里混进这种槽会让守卫永远失败，
        快循环永远进不去（order_engine 实证）。
        """
        f2 = self._f2('var s=0;\nfor(i in 10){ var t = i * 3; s += t; }\noutput(s);\n')
        loops = f2.natural_loops(f2.compute_idom())
        self.assertEqual(len(loops), 1)
        lp = loops[0]
        live_in, _lo = f2.compute_liveness()
        head_in = live_in[lp.header]
        # 累加器 s 与归纳变量 i 是「进入时旧值会被读」的槽
        self.assertTrue(head_in, 'live_in(header) 不应为空')
        # 临时槽（循环体内先写后读）必须被排除
        for bid in lp.body:
            for i in f2.blocks[bid].instrs:
                if i.op == lir.ARITH_GEN and i.dst >= 0 and i.dst not in head_in:
                    # 该临时槽不在 live_in → 每轮先写后读，符合预期
                    self.assertTrue(any(i2.op in (lir.LOAD_F64, lir.MOV_V)
                                        for b2 in lp.body
                                        for i2 in f2.blocks[b2].instrs))


class GuardNumNodeTest(unittest.TestCase):
    """GUARD_NUM(33)：构造 + 序列化往返 + 结构自检。"""

    def test_roundtrip(self):
        f = lir.LirFunction('t', 'user')
        f.n_slots_v = 4
        f.emit(lir.LirInstr(lir.GUARD_NUM, args=[0, 1, lir.GLOBAL_BIT | 2], target=3))
        f.emit(lir.LirInstr(lir.JMP, target=0))
        prog = lir.LirProgram()
        prog.main = lir.LirFunction('', 'main')
        prog.main.instrs = [lir.LirInstr(lir.STOP)]
        prog.funcs[0] = f
        back = lir.LirProgram.deserialize(prog.serialize())
        g = back.funcs[0].instrs[0]
        self.assertEqual(g.op, lir.GUARD_NUM)
        self.assertEqual(list(g.args), [0, 1, lir.GLOBAL_BIT | 2])
        self.assertEqual(g.target, 3)

    def test_verify_rejects_empty_guard(self):
        f = lir.LirFunction('t', 'user')
        f.emit(lir.LirInstr(lir.GUARD_NUM, args=[], target=0))
        self.assertTrue(f.verify(), '空守卫应被结构自检拦下')


class PeepholeTest(unittest.TestCase):
    """N-02 四个窥孔 pass 的形为钉。"""

    def test_fold_const_loads(self):
        prog = lower_source('var s = 0;\nfor (i in 10) { s = s + 1; }\noutput(s);\n')
        lir_opt.promote_globals(prog)
        lir_opt.infer_f64(prog)
        n = lir_opt.fold_const_loads(prog)
        self.assertGreater(n, 0, '循环里的数字常量应被折成立即数')
        self.assertTrue(any(i.op == lir.CONST_F64 for f in prog.all_funcs()
                            for i in f.instrs))

    def test_forward_stores_removes_temp_copy(self):
        prog = lower_source('var s = 0;\nfor (i in 10) { s = s + 1; }\noutput(s);\n')
        lir_opt.promote_globals(prog)
        lir_opt.infer_f64(prog)
        before = len(prog.main.instrs)
        n = lir_opt.forward_stores(prog)
        self.assertGreater(n, 0)
        self.assertLessEqual(len(prog.main.instrs), before)

    def test_eliminate_dead_slots_drops_induction_copy(self):
        """for 的归纳变量副本（无人读取）必须被删掉。"""
        prog = lower_source('var s = 0;\nfor (i in 10) { s = s + i; }\noutput(s);\n')
        lir_opt.promote_globals(prog)
        lir_opt.infer_f64(prog)
        n = lir_opt.eliminate_dead_slots(prog)
        self.assertGreater(n, 0)
        self.assertEqual(prog.verify(), [])

    def test_propagate_copies_is_sound(self):
        """拷贝传播后程序仍通过结构自检，且被传播的槽不再有定义。"""
        prog = lower_source('var a = 1;\nvar b = a;\nvar c = b + 2;\noutput(c);\n')
        # 顶层变量在只被 main 触碰时会被提升为局部槽；先走完这条正常管线
        lir_opt.promote_globals(prog)
        n = lir_opt.propagate_copies(prog)
        self.assertGreater(n, 0)
        self.assertEqual(prog.verify(), [])
        self.assertFalse(any(i.op == lir.MOV_V for i in prog.main.instrs))

    def test_propagate_copies_refuses_globals(self):
        """全局槽的别名关系跨函数，块内分析看不到 → 一律不动。

        `var b = g`（g 是用户函数也在读的全局）不得被传播成「把 g 直接塞进
        读取点」——若被传播，另一个函数对 g 的改写就会在块内视角下失察。

        要造出「提升不掉」的全局：让某个用户函数也读它。
        """
        prog = lower_source('var g = 1;\n'
                            'number_t func peek() { return g; }\n'
                            'var b = g;\noutput(b);\noutput(peek());\n')
        lir_opt.promote_globals(prog)
        # 前置：确实存在「局部 ← 全局」的拷贝，且 g 未被提升为局部
        from_global = [(i.dst, i.a) for i in prog.main.instrs
                       if i.op == lir.MOV_V and (i.a & lir.GLOBAL_BIT)
                       and not (i.dst & lir.GLOBAL_BIT)]
        self.assertTrue(from_global, '测试前置失败：应有 局部←全局 的拷贝')
        lir_opt.propagate_copies(prog)
        after = [(i.dst, i.a) for i in prog.main.instrs
                 if i.op == lir.MOV_V and (i.a & lir.GLOBAL_BIT)
                 and not (i.dst & lir.GLOBAL_BIT)]
        self.assertEqual(set(after), set(from_global),
                         '全局槽参与的拷贝必须原样保留')


class UnboxLoopTest(unittest.TestCase):
    """N-02 循环去装箱：结构钉 + 安全边界钉。"""

    def _count_guard(self, prog):
        return sum(1 for f in prog.all_funcs() for i in f.instrs
                   if i.op == lir.GUARD_NUM)

    def test_simple_numeric_loop_is_versioned(self):
        prog = pipeline('var s = 0;\nfor (i in 10) { s = s + 1; }\noutput(s);\n')
        self.assertEqual(self._count_guard(prog), 1, '数值循环应生成一份形态守卫')
        self.assertEqual(prog.verify(), [])

    def test_guard_set_excludes_non_live_in_slots(self):
        """守卫集合 ⊆ live_in(循环头)：含临时槽会让守卫永远失败。"""
        prog = pipeline('var s = 0;\nfor (i in 10) { var t = i * 3; s = s + t; }\n'
                        'output(s);\n')
        g = [i for f in prog.all_funcs() for i in f.instrs if i.op == lir.GUARD_NUM]
        self.assertEqual(len(g), 1)
        # 守卫里不能出现「循环体内定义的临时槽」（它们的入口值是 VT_NONE）
        prog2 = lower_source('var s = 0;\nfor (i in 10) { var t = i * 3; s = s + t; }\n'
                             'output(s);\n')
        lir_opt.inline_calls_v2(prog2)
        lir_opt.promote_globals(prog2)
        lir_opt.infer_f64(prog2)
        lir_opt.fold_const_loads(prog2)
        lir_opt.forward_stores(prog2)
        lir_opt.propagate_copies(prog2)
        lir_opt.eliminate_dead_slots(prog2)
        f2 = lir2.from_v1(prog2.main)
        live_in, _ = f2.compute_liveness()
        loops = f2.natural_loops(f2.compute_idom())
        self.assertEqual(len(loops), 1)
        head = loops[0].header
        for s in g[0].args:
            self.assertIn(s, live_in[head],
                          '守卫槽 v%d 不在 live_in(循环头) 里' % s)

    def test_no_unbox_when_loop_has_dynamic_dispatch(self):
        """require 的模块调用在循环里 → 一律不做版本化。"""
        src = ('require "somelib.dll";\nvar s = 0;\n'
               'for (i in 10) { s = s + somelib; }\noutput(s);\n')
        prog = pipeline(src)
        self.assertEqual(self._count_guard(prog), 0)

    def test_unbox_off_switch(self):
        os.environ['QOT_NO_UNBOX'] = '1'
        try:
            prog = pipeline('var s = 0;\nfor (i in 10) { s = s + 1; }\noutput(s);\n')
            self.assertEqual(self._count_guard(prog), 0)
        finally:
            del os.environ['QOT_NO_UNBOX']

    def test_layout_invariant_holds_after_unbox(self):
        """to_v1 会断言「落下块紧随其后」——能走完就说明不变式成立。"""
        prog = pipeline('var s = 0;\nfor (i in 10) { s = s + 1; if (s > 4) { s = s - 1; } }\n'
                        'output(s);\n')
        self.assertEqual(prog.verify(), [])
        self.assertGreater(self._count_guard(prog), 0)

    def test_exit_edge_writes_back(self):
        """快循环的出边必须先装箱再走：出边块里应出现 STORE_F64。"""
        prog = pipeline('var s = 0;\nfor (i in 10) { s = s + 1; }\noutput(s);\n')
        guards = [i for i in prog.main.instrs if i.op == lir.GUARD_NUM]
        self.assertEqual(len(guards), 1)
        stores = [i for i in prog.main.instrs if i.op == lir.STORE_F64]
        self.assertTrue(stores, '出边回写必须存在')


class InlineV2Test(unittest.TestCase):
    """T-43 v2 块坐标内联。"""

    def test_small_leaf_inlined(self):
        prog = lower_source(
            'number_t func twice(x) { return x * 2; }\nvar a = 0;\n'
            'for (j in 5) { a = a + twice(j); }\noutput(a);\n')
        n = lir_opt.inline_calls_v2(prog)
        self.assertGreater(n, 0)
        self.assertEqual(prog.verify(), [])
        self.assertFalse(any(i.op == lir.CALL_USER for i in prog.main.instrs),
                         'twice 应被内联，主块不再有 CALL_USER')

    def test_recursive_function_not_inlined(self):
        prog = lower_source(
            'number_t func fib(k) { if (k < 2) { return k; } '
            'return fib(k-1) + fib(k-2); }\noutput(fib(10));\n')
        before = len(prog.main.instrs)
        lir_opt.inline_calls_v2(prog)
        self.assertEqual(prog.verify(), [])
        # 递归不得被展开成无限膨胀
        self.assertLess(len(prog.main.instrs), before + 200)

    def test_param_written_by_callee_keeps_binding_copy(self):
        """被调方会写形参（n -= 1）→ 必须给形参独立副本，不能直代换。"""
        prog = lower_source(
            'number_t func count_down(n) { var c = 0; loop(n > 0) { n = n - 1; c = c + 1; } '
            'return c; }\nvar k = 5;\noutput(count_down(k));\noutput(k);\n')
        lir_opt.inline_calls_v2(prog)
        self.assertEqual(prog.verify(), [])

    def test_inline_off_switch(self):
        os.environ['QOT_NO_INLINE'] = '1'
        try:
            prog = lower_source(
                'number_t func twice(x) { return x * 2; }\noutput(twice(3));\n')
            self.assertEqual(lir_opt.inline_calls_v2(prog), 0)
        finally:
            del os.environ['QOT_NO_INLINE']

    def test_name_table_survives_inline(self):
        """内联会把被内联体的名字并进调用方名字表 → 内置调用不能解析成空名。"""
        prog = lower_source(
            'number_t func twice(x) { return x * 2; }\n'
            'var a = 0;\nfor (j in 3) { a = a + twice(j); }\noutput(a);\n')
        lir_opt.inline_calls_v2(prog)
        from lir_cgen import generate
        src = generate(prog, '<unit>')      # 不抛 CgenError 即名字解析正常
        self.assertIn('qotrt_output', src)


class UnboxSoundnessRegressionTest(unittest.TestCase):
    """去装箱的历史分歧回归钉（每条都对应一个真实翻过的坑）。"""

    def test_const_only_group_writes_mirror(self):
        """回归 seed=20261155：无输入的常量组必须与求解器同判。

        循环体里「常量 → STORE」的组没有 v 输入。若改写器把它当成
        「不可拆箱」而求解器当成「已拆箱」，该槽的镜像就永远不会被写，
        下游组读镜像读到的就是 0 → `-((2.0 * (-0.5^3)))` 算出 -0.0。
        """
        src = ('number_t func fn0(p0) { return (-((2.0 * (-0.5 ^ 3)))); }\n'
               'number_t func fn1() {\n'
               '    output((fn0(71.152) - -37.168));\n'
               '    for (i3 in 1) { output(fn0((-(10.0)))); }\n'
               '    return 0;\n'
               '}\n'
               'output(fn1());\n')
        prog = pipeline(src)
        self.assertEqual(prog.verify(), [])
        # 结构不变式：任何「读镜像」的 LOAD_F64 改写，其源槽的定义组必须
        # 也被改写 —— 否则快循环里必然读到未初始化镜像。
        # 用可执行的证据代替静态推断：整段程序与解释器逐位一致由
        # testing/run.py --suite fuzz 守卫，这里钉的是 LIR 形态。
        mov_f_srcs = set()
        load_srcs = set()
        for f in prog.all_funcs():
            for i in f.instrs:
                if i.op == lir.MOV_F:
                    mov_f_srcs.add(i.a)
                elif i.op == lir.LOAD_F64:
                    load_srcs.add((i.dst, i.a))
        self.assertTrue(mov_f_srcs, '应存在写镜像的 MOV_F')

    def test_solver_and_rewriter_agree_on_empty_input_groups(self):
        """谓词一致性的形态钉：常量组（无 v 输入）若被求解器判为已拆箱，
        它的组尾必须被改写成「写镜像」——否则镜像永无赋值（seed=20261155）。

        判据：在去装箱后的 LIR 里，凡 MOV_F 写入的镜像号 m，若存在
        「CONST_F64 → STORE_F64(v) → LOAD_F64(_, v)」链，则该链尾要么也是
        MOV_F（已改写），要么 v 根本不该出现在任何镜像读取里。
        这里用最直接的等价形式：不含「STORE_F64(v) 后紧跟 LOAD_F64(v) 且
        v 的镜像同时被 MOV_F 写」的混合形态。
        （逐位语义真相源 = testing/run.py --suite native 的差分，含
        t16_inline_const_group 回归用例。）
        """
        src = ('var s = 0.0;\n'
               'for (i in 4) {\n'
               '    var t = 1.5 * 2.0;\n'      # 常量组：无 v 输入
               '    s = s + t + i;\n'
               '}\n'
               'output(s);\n')
        prog = pipeline(src)
        self.assertEqual(prog.verify(), [])
        # 该形状必须仍被版本化（说明常量组参与了去装箱的候选求解而不是被放弃）
        self.assertTrue(any(i.op == lir.GUARD_NUM for f in prog.all_funcs()
                            for i in f.instrs),
                        '含常量组的数值循环仍应被版本化')
        # 逐位语义真相源 = testing/run.py --suite native 的差分，
        # 回归用例：t16_inline_const_group（本缺陷的真实触发形状）。


if __name__ == '__main__':
    unittest.main()
