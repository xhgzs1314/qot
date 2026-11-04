# -*- coding: utf-8 -*-
"""LIR 单元测试 ——  T-40（每个节点有单测）+ T-41（现有用例完整降级）。

由 testing/run.py --suite unit 调度。
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
from lir import LirProgram, LirFunction, LirInstr  # noqa: E402
import lir_lower  # noqa: E402

ART = os.path.join(TESTING, '.artifacts')


def compile_qot_to_vmcode(qot_path):
    from grammar import parse_oning_gram, strip_line_numbers
    from bytecode import Compiler
    # 【GOD-SELF 】与产品前端同一条线：宏展开 + 协议绑定
    # （index._prepare_ast）后再编译 —— 测试基建不得绕过前端契约。
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


class LirNodeTest(unittest.TestCase):
    """T-40：LIR v1 全部 33 个节点：构造 + 序列化往返 + 语义字段保真。"""

    def _roundtrip(self, instrs, nsv=4, nsf=2):
        f = LirFunction('t', 'user')
        f.n_slots_v = nsv
        f.n_slots_f = nsf
        f.instrs = instrs
        prog = LirProgram()
        prog.main = LirFunction('', 'main')
        prog.funcs[0] = f
        blob = prog.serialize()
        back = LirProgram.deserialize(blob)
        self.assertEqual(len(back.funcs), 1)
        rf = back.funcs[0]
        self.assertEqual(rf.n_slots_v, nsv)
        self.assertEqual(rf.n_slots_f, nsf)
        self.assertEqual(len(rf.instrs), len(instrs))
        return rf.instrs

    def test_all_opcodes_defined_and_unique(self):
        ops = list(lir.INSTR_NAMES.keys())
        # 指令号 0..32 无重复且连续定义
        self.assertEqual(len(set(ops)), len(ops))
        # 33 = v1 冻结集；34 = GUARD_NUM(33)（N-02）；36 = ARENA_PUSH/RESET
        #（M-PERF 收尾二，后端 A 内部插桩 op）；42 = F1/F2 追加
        # THROW..THROWVAL(36..41)（闭包与异常，号段只增不重编）
        # 【M4 】45 = FIELD_GET/FIELD_SET/CALL_OMETHOD(42..44)
        #（对象字段与方法分派，号段只增不重编）
        # 【G4】46 = GUARD_TYPE(45)（PROMAX 类型不变量版本化；发射侧
        # lir_native/lir_cgen 均已注册，serialize 走既有 varint 路径）
        self.assertEqual(len(ops), 46,
                         'LIR 节点数应为 33(v1) + 1(GUARD_NUM) + 2(ARENA) + 6(F1/F2) + 3(M4) + 1(GUARD_TYPE)')
        for op in ops:
            self.assertTrue(0 <= op <= 45, '指令号应落在冻结号段 0..45: %s' % op)

    def test_const_f64(self):
        (r,) = self._roundtrip([LirInstr(lir.CONST_F64, dst=1, imm=3.25)])
        self.assertEqual((r.dst, r.imm), (1, 3.25))

    def test_const_val(self):
        (r,) = self._roundtrip([LirInstr(lir.CONST_VAL, dst=0, a=7)])
        self.assertEqual((r.dst, r.a), (0, 7))

    def test_const_val_null_sentinel(self):
        (r,) = self._roundtrip([LirInstr(lir.CONST_VAL, dst=0, a=-1)])
        self.assertEqual(r.a, 0xFFFFFFFF, 'null 哨兵以 u32 编码，运行期按 >=len(consts) 判 null')

    def test_load_store_f64(self):
        r = self._roundtrip([LirInstr(lir.LOAD_F64, dst=0, a=lir.GLOBAL_BIT | 3),
                             LirInstr(lir.STORE_F64, dst=lir.GLOBAL_BIT | 3, a=0)])
        self.assertEqual(r[0].dst, 0)
        self.assertEqual(r[0].a, lir.GLOBAL_BIT | 3)
        self.assertEqual(r[1].dst, lir.GLOBAL_BIT | 3)
        self.assertEqual(r[1].a, 0)

    def test_mov_v_mov_f(self):
        r = self._roundtrip([LirInstr(lir.MOV_V, dst=2, a=1),
                             LirInstr(lir.MOV_F, dst=1, a=0)])
        self.assertEqual((r[0].dst, r[0].a), (2, 1))
        self.assertEqual((r[1].dst, r[1].a), (1, 0))

    def test_f64_arithmetic_family(self):
        ops = [lir.ADD_F, lir.SUB_F, lir.MUL_F, lir.DIV_F, lir.MOD_F, lir.POW_F]
        r = self._roundtrip([LirInstr(op, dst=0, a=1, b=2) for op in ops])
        for k, op in enumerate(ops):
            self.assertEqual(r[k].op, op)
            self.assertEqual((r[k].dst, r[k].a, r[k].b), (0, 1, 2))

    def test_cmp_f_pred(self):
        for pred in (lir.OP_GT, lir.OP_LT, lir.OP_GE, lir.OP_LE, lir.OP_EQ, lir.OP_NE):
            (r,) = self._roundtrip([LirInstr(lir.CMP_F, dst=3, a=0, b=1, pred=pred)])
            self.assertEqual((r.dst, r.a, r.b, r.pred), (3, 0, 1, pred))

    def test_neg_f(self):
        (r,) = self._roundtrip([LirInstr(lir.NEG_F, dst=1, a=0)])
        self.assertEqual((r.dst, r.a), (1, 0))

    def test_arith_gen_and_unary_gen(self):
        r = self._roundtrip([LirInstr(lir.ARITH_GEN, dst=5, a=1, b=2, pred=lir.OP_ADD),
                             LirInstr(lir.ARITH_GEN, dst=6, a=5, b=1, pred=lir.OP_EQ),
                             LirInstr(lir.UNARY_GEN, dst=7, a=5, pred=lir.OP_UMINUS),
                             LirInstr(lir.UNARY_GEN, dst=8, a=5, pred=lir.OP_NOT)])
        self.assertEqual((r[0].dst, r[0].a, r[0].b, r[0].pred), (5, 1, 2, 1))
        self.assertEqual(r[1].pred, lir.OP_EQ)
        self.assertEqual((r[2].dst, r[2].pred), (7, 1))
        self.assertEqual((r[3].dst, r[3].pred), (8, 2))

    def test_branches_and_jump(self):
        r = self._roundtrip([LirInstr(lir.BR_IF_NOT, a=0, target=9),
                             LirInstr(lir.BR_IF, a=0, target=9),
                             LirInstr(lir.BR_F, a=1, target=9),
                             LirInstr(lir.JMP, target=9)])
        self.assertEqual([x.target for x in r], [9, 9, 9, 9])
        self.assertEqual(r[0].a, 0)
        self.assertEqual(r[2].a, 1)

    def test_returns(self):
        r = self._roundtrip([LirInstr(lir.RET_V, a=2), LirInstr(lir.RET_F, a=1)])
        self.assertEqual(r[0].a, 2)
        self.assertEqual(r[1].a, 1)

    def test_calls(self):
        r = self._roundtrip([
            LirInstr(lir.CALL_USER, dst=9, func_idx=3, argc=2, args=[1, 2]),
            LirInstr(lir.CALL_BUILTIN, dst=9, name_idx=5, argc=1, args=[lir.GLOBAL_BIT | 4]),
            LirInstr(lir.CALL_MODULE, dst=9, name_idx=6, argc=2, args=[7, 8]),
            LirInstr(lir.CALL_DYNAMIC, dst=9, a=2, argc=1, args=[3]),
        ])
        self.assertEqual((r[0].func_idx, r[0].argc, r[0].args), (3, 2, [1, 2]))
        self.assertEqual((r[1].name_idx, r[1].args[0]), (5, lir.GLOBAL_BIT | 4))
        self.assertEqual(r[2].name_idx, 6)
        self.assertEqual(r[3].a, 2)

    def test_containers(self):
        r = self._roundtrip([
            LirInstr(lir.INDEX_GET, dst=9, a=1, b=2),
            LirInstr(lir.INDEX_SET, a=1, b=2, c=3),
            LirInstr(lir.MAKE_ARRAY, dst=9, argc=3, args=[1, 2, 3]),
            LirInstr(lir.MAKE_DICT, dst=9, argc=2, args=[4, 5, 6, 7]),
        ])
        self.assertEqual((r[0].dst, r[0].a, r[0].b), (9, 1, 2))
        self.assertEqual((r[1].a, r[1].b, r[1].c), (1, 2, 3))
        self.assertEqual(r[2].args, [1, 2, 3])
        self.assertEqual(r[3].args, [4, 5, 6, 7])

    def test_require_stop_nop(self):
        r = self._roundtrip([LirInstr(lir.REQUIRE, dst=2, str_idx=1),
                             LirInstr(lir.STOP), LirInstr(lir.NOP)], nsv=3)
        self.assertEqual((r[0].dst, r[0].str_idx), (2, 1))
        self.assertEqual([x.op for x in r], [lir.REQUIRE, lir.STOP, lir.NOP])

    def test_req_names_roundtrip(self):
        f = LirFunction('m', 'main')
        f.req_names = ['liba.dll', 'lib/b.json']
        f.emit(LirInstr(lir.REQUIRE, dst=0, str_idx=1))
        prog = LirProgram()
        prog.main = f
        back = LirProgram.deserialize(prog.serialize())
        self.assertEqual(back.main.req_names, ['liba.dll', 'lib/b.json'])

    def test_verify_catches_bad_target(self):
        f = LirFunction('t', 'user')
        f.emit(LirInstr(lir.JMP, target=99))
        self.assertTrue(f.verify())

    def test_verify_catches_argc_mismatch(self):
        f = LirFunction('t', 'user')
        f.emit(LirInstr(lir.CALL_BUILTIN, dst=0, name_idx=0, argc=2, args=[1]))
        self.assertTrue(f.verify())


class LirLowerTest(unittest.TestCase):
    """T-41：现有 14 个 vm 用例必须能完整降级 + 结构自检零错误。"""

    def test_lower_all_vm_cases(self):
        vm_dir = os.path.join(TESTING, 'cases', 'native')
        lowered = 0
        for fn in sorted(os.listdir(vm_dir)):
            if not fn.endswith('.qot'):
                continue
            code = compile_qot_to_vmcode(os.path.join(vm_dir, fn))
            prog = lir_lower.lower_program(code)
            self.assertEqual(prog.verify(), [], '%s: LIR 自检失败' % fn)
            self.assertGreater(len(prog.main.instrs), 0, fn)
            lowered += 1
        # 锚点更新（S-01/S-02 轮）：原 14 例 + fuzz 定价钉 + S-02 负例矩阵
        # （guard×5）= 26；D-11 回归钉 bound_guard_arith_str_flow = 27。
        # N-02 后续轮：% 语义矩阵 mod_semantics_edge = 28、
        # N-05 快速通道矩阵 builtin_fastpath_matrix = 29、
        # N-04 双体调用钉 unbox_call_numeric/fallback = 30、31、
        # N-03 SSA 形状钉 ssa_shapes = 32。
        # 【 转向】cases/vm/ → cases/native/（目录重组，用例不改名，
        # 计数锚点不受影响）；E 线新增 2 条 native 口径钉：
        #   native_dict_iter_order（字典槽序）= 33+1、
        #   native_dict_const_vs_dyn_key（E3 常量键折叠对拍）= 33+2 → 35。
        # 【 F1/F2】闭包与异常 8 条：clo_counter/clo_lambda/
        #   clo_recursion/clo_loop_capture/try_basic/try_control_flow/
        #   err_meta/try_uncaught → 43。
        # 【 CP-2】P0.1 原语差分 5 条：p10_prim_str/p11_prim_num/
        #   p12_prim_arr/p13_prim_dict/p14_prim_file → 48。
        # 【 CP-3+P0.2】p15_prim_bytes/p16_prim_time/p17_prim_sys/
        #   p18_prim_type/bool_eq_pricing/empty_string_pricing/std_fs/
        #   std_text/demo_csv_report → 57。
        # 【 P0-POOL-1】std_text 摘钉（arena 逃逸根因修复）。
        # 【 P1030-4】模块前向互调 mod_forward、FFI p19_prim_ffi、
        #   网络 p20_prim_net、std_net → 61。
        # 【 语法收口】expr_cmp_rhs_arith（G-00）、
        #   ctrl_continue_semantics（G-01）、expr_dcolon_in_expr（G-11）、
        #   prim_json_native（G-09 native 化）→ 65。
        # 【 G-03/G-04/M3】expr_compound_family、expr_ternary、
        #   bound_fused_idx_order（融合序缺陷钉）、for_range_step、
        #   for_in_unified → 70；【M4】obj_model_basic → 71。
        # 【 M7】p21_prim_plugin（原生插件 ABI）→ 72。
        # 【 M5】std_data / std_cli（标准库二期）→ 74。
        # 【 M5 尾】p22_prim_bit（位运算 6 条）→ 75。
        # 【 GOD-SELF】gs1_proto（G1 协议绑定）、gs2_macro（G2 编译期
        #   官方+用户宏）、gs3_emergent（涌现验收）→ 78（前端挂点后全部可降级）。
        # 新增用例须同步本锚点
        self.assertEqual(lowered, 78, 'native 用例应全部可降级（新增用例须同步本锚点）')

    def test_arena_no_instrument_std_text(self):
        """P0-POOL-1 回归钉（编译期半）：std_text 程序 arena 插桩必须为 0。

        修复前该程序有 6 处插桩、其中至少 4 处是幻影循环（回边 JMP 链被
        误认成循环头）或容器内容逃逸形态 —— 运行期 0xC0000005/字段截断。
        否决 1.5（t∈body 律 + 向后跳出律）+ 否决 3（mutates_container）
        落地后，本程序的循环一个都不许插桩。
        """
        import lir_opt as _lo
        prog = self._lower_case('std_text.qot')
        self.assertEqual(_lo.arena_loops(prog), 0,
                         'std_text 程序不允许有任何 arena 插桩（P0-POOL-1）')

    def test_arena_still_instrument_b3a(self):
        """P0-POOL-1 回归钉（另一半）：b3a 形态必须仍然插桩。

        否决收紧不许把优化杀成摆设 —— b3a（轮内短命对象、容器同轮生灭）
        是 arena 回收的存在理由，插桩数必须 > 0。
        """
        import lir_opt as _lo
        prog = self._lower_path(os.path.join(TESTING, 'bench',
                                             'b3a_short_lived.qot'))
        self.assertGreater(_lo.arena_loops(prog), 0,
                           'b3a 形态必须仍然插桩（arena 回收不许被否决收紧杀死）')

    def _lower_case(self, name):
        return self._lower_path(os.path.join(TESTING, 'cases', 'native', name))

    def _lower_path(self, src):
        """复刻 index.py AOT 管线到 arena_loops 入口（require 先内联 ——
        否则 std_text 的模块函数不在 LIR 里，钉会空过）。"""
        import tempfile
        import lir_opt as _lo
        import lir_ssa
        from require_inliner import inline_requires
        fd, merged = tempfile.mkstemp(suffix='.qot')
        os.close(fd)
        try:
            inline_requires(src, merged)
            code = compile_qot_to_vmcode(merged)
        finally:
            os.remove(merged)
        prog = lir_lower.lower_program(code)
        _lo.inline_calls_v2(prog)
        _lo.promote_globals(prog)
        _lo.infer_f64(prog)
        lir_ssa.ssa_regalloc(prog)
        _lo.optimize_f64(prog)
        return prog

    def test_loop_shape(self):
        """`var b=0; for(i in 3){ b += i; }` 应降级为含回边 JMP 的线性流。"""
        prog = lower_source('var b = 0;\nfor (i in 3) { b += i; }\noutput(b);\n')
        ops = [i.op for i in prog.main.instrs]
        self.assertIn(lir.JMP, ops, 'for 循环应有回边')
        self.assertIn(lir.BR_IF_NOT, ops, '循环条件应为条件跳转')
        self.assertIn(lir.CALL_BUILTIN, ops, 'output 调用应标注为 builtin')

    def test_stack_consistency_guard(self):
        """结构化字节码下，所有汇合点栈快照一致（正常程序必须通过）。"""
        src = ('var x = 1;\nif (x > 0) { x = 2; } eif (x < 0) { x = 3; } else { x = 4; }\n'
               'var s = 0;\nfor (i in 10) { s += i; if (s > 5) { stop; } }\noutput(s);\n')
        prog = lower_source(src)
        self.assertEqual(prog.verify(), [])

    def test_call_bind_semantics_documented(self):
        """形参绑定 = min(argc, n_slots)：LIR 函数的 n_slots_v 覆盖全部局部。

        与字节码解释器 vm_executor case 30 完全一致（由 bound_call_extra_args
        / bound_call_fewer_args 端到端钉住）；本测试钉住 LIR 侧的前提。
        """
        prog = lower_source(
            'number_t func f(a, b){ var t = a + b; return t; }\noutput(f(1, 2));\n')
        self.assertEqual(len(prog.funcs), 1)
        f = list(prog.funcs.values())[0]
        self.assertEqual(f.kind, 'user')
        self.assertEqual(f.name, 'f')
        # varNames = [a, b, t] → 绑定判定用 n_bind_slots=3（临时另计，n_slots_v≥3）
        self.assertEqual(f.n_bind_slots, 3)
        self.assertGreaterEqual(f.n_slots_v, 3)

    def test_null_sentinel_lowered(self):
        prog = lower_source('var x = null;\noutput(x == null);\n')
        consts = [i.a for i in prog.main.instrs if i.op == lir.CONST_VAL]
        self.assertIn(-1, consts, 'null 字面量应为 CONST_VAL(-1) 哨兵')

    def test_require_lowered_with_names(self):
        prog = lower_source('require "somelib.dll";\noutput(glen(somelib));\n')
        self.assertEqual(prog.main.req_names, ['somelib.dll'])
        self.assertIn(lir.REQUIRE, [i.op for i in prog.main.instrs])

    def test_global_bit_passthrough(self):
        """顶层变量是全局槽：GLOBAL_BIT 必须原样保留在槽号里。"""
        prog = lower_source('var b = 0;\nb = b + 1;\n')
        stores = [i for i in prog.main.instrs if i.op == lir.MOV_V]
        self.assertTrue(any(i.dst & lir.GLOBAL_BIT for i in stores),
                        '顶层 STORE 的目标槽应带 GLOBAL_BIT')

    def test_serialize_roundtrip_full_program(self):
        prog = lower_source(
            'number_t func fib(k){ if (k < 2) { return k; } return fib(k-1) + fib(k-2); }\n'
            'output(fib(10));\n')
        blob = prog.serialize()
        back = LirProgram.deserialize(blob)
        self.assertEqual(back.verify(), [])
        self.assertEqual(len(back.main.instrs), len(prog.main.instrs))
        self.assertEqual(len(back.funcs), len(prog.funcs))
        # 指令逐条一致
        bf = list(back.funcs.values())[0]
        of = list(prog.funcs.values())[0]
        self.assertEqual([(i.op, i.dst, i.a, i.b, i.pred) for i in bf.instrs],
                         [(i.op, i.dst, i.a, i.b, i.pred) for i in of.instrs])

    def test_every_node_reachable_from_real_cases(self):
        """真实用例降级后，v1 节点表中大部分节点必须实际出现（防「死节点」腐化）。"""
        seen = set()
        for fn in sorted(os.listdir(os.path.join(TESTING, 'cases', 'native'))):
            if not fn.endswith('.qot'):
                continue
            code = compile_qot_to_vmcode(os.path.join(TESTING, 'cases', 'native', fn))
            prog = lir_lower.lower_program(code)
            for f in prog.all_funcs():
                seen.update(i.op for i in f.instrs)
        # 现有用例语言面（t01~t15）至少应覆盖这些节点
        must = {lir.CONST_VAL, lir.MOV_V, lir.ARITH_GEN, lir.CMP_F if False else lir.BR_IF_NOT,
                lir.JMP, lir.CALL_BUILTIN, lir.CALL_USER, lir.RET_V, lir.STOP,
                lir.INDEX_GET, lir.MAKE_ARRAY, lir.MAKE_DICT}
        missing = must - seen
        self.assertEqual(set(), missing,
                         '真实用例未覆盖的节点: %s（若为有意，请更新本清单）'
                         % sorted(lir.INSTR_NAMES[o] for o in missing))


if __name__ == '__main__':
    unittest.main()
