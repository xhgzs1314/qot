# -*- coding: utf-8 -*-
"""GOD-SELF 单测（ ·  §8.24）：

1. 【G2 最大单件的过关门】AST ↔ QOT 值 双向转换器**往返无损**（§8.24-E2
   顺序纪律：转换器未跑通往返测试前禁止写任何宏用例）—— 对全节点形态
   的合成程序 + 既有真实用例，带行号/strip 两种形态逐字段相等；
2. 展开上限：QOT_MACRO_DEPTH / QOT_MACRO_LIMIT（默认 64 / 1e6，0=不限，
   超限 = [4000] 宏展开超限）；
3. 官方宏 swap!/unless! 与用户宏的展开语义（进程内：expand → evaluate）；
4. 一元逻辑非 `!` 的 AST 形态与 truthy 取反语义（前缀位；与宏调用
   name!( 的文法消歧）。
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from grammar import parse_oning_gram, strip_line_numbers  # noqa: E402
import qot_macro  # noqa: E402
from qot_macro import (MacroExpander, MacroError, ast_to_value,  # noqa: E402
                       value_to_ast, expand_program)

ART = os.path.join(HERE, '.artifacts')
PY = sys.executable

# 全节点形态覆盖的合成样例（每个 statement/expression kind 至少一次）
FULL_SHAPE_SOURCE = '''\
require "nothing_qot_skip_if_missing";
@-@-@gs roundtrip
class P {
    var a = 1;
    var b;
    any_t func m(x) { return this->a + x; }
    func init(v) { this->a = v; }
}
macro twice(x) {
    return &< "kind": "binary_op", "op": "+", "left": x, "right": x >&;
}
number_t func add(n) {
    var s = 0;
    for (i in n) {
        if (i % 2 == 0) { continue; }
        s += i;
    }
    return s;
}
var arr = [1, 2, 3];
var d = &< "k": 1, "j": [true, null] >&;
d["k"] = 2;
var t = 1 > 0 ? 1 : 2;
var u = -t;
var w = !u;
var c1 = add(10);
var lam = func(x) -> x + 1;
var s = "a" + 1;
if (c1 > 0 && true || false) {
    c1 = c1 + twice!(2);
} else {
    c1 = 0;
}
loop (c1 > 100) {
    c1 -= 50;
    if (c1 < 0) { stop; }
}
for (kv in d) {
    output(kv);
}
for (i in 0..3 by 2) {
    output(i);
}
for (i in 10..0 by -5) {
    output(i);
}
try {
    throw "x";
} catch (e) {
    output(e);
}
var obj = P(3);
obj->b = obj->m(4);
var p2 = obj;
output(p2->b);
'''


def _write(tmp, text):
    os.makedirs(ART, exist_ok=True)
    path = os.path.join(ART, tmp)
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        f.write(text)
    return path


class TestRoundTrip(unittest.TestCase):
    """转换器往返无损（G2 的过关门；漂移即失败 —— NODE_FIELDS 登记表
    与 grammar 产生式必须同步）。"""

    def _check_file(self, path):
        ast = parse_oning_gram(path)
        self.assertIsNotNone(ast, path)
        # 带行号形态
        rt = value_to_ast(ast_to_value(ast))
        self.assertEqual(rt, ast, '带行号形态往返不等: %s' % path)
        # strip 形态
        stripped = strip_line_numbers(ast)
        rt2 = value_to_ast(ast_to_value(stripped))
        self.assertEqual(rt2, stripped, 'strip 形态往返不等: %s' % path)

    def test_full_shape_synthetic(self):
        src = FULL_SHAPE_SOURCE.replace(
            'require "nothing_qot_skip_if_missing";\n', '')
        self._check_file(_write('rt_full.qot', src))

    def test_real_cases_breadth(self):
        cases = [
            'testing/cases/native/gs1_proto.qot',
            'testing/cases/native/gs2_macro.qot',
            'testing/cases/native/gs3_emergent.qot',
            'testing/cases/native/obj_model_basic.qot',
            'testing/cases/native/for_in_unified.qot',
            'std/fs.qot',
            'std/data.qot',
        ]
        for rel in cases:
            self._check_file(os.path.join(ROOT, rel))

    def test_unknown_kind_positional_fallback(self):
        # 未登记 kind：位置形态兜底，仍往返无损
        node = ('future_kind_xyz', ('literal', 1, 3), 'plain', 7)
        rt = value_to_ast(ast_to_value(node))
        self.assertEqual(rt, node)


class TestLimits(unittest.TestCase):
    """展开上限（§8.24-A：默认 64 / 1,000,000；env 可调；0 = 不限）。"""

    def _expand(self, src, depth=None, limit=None):
        # 顺序与真实管线一致：展开在带行号形态上执行，strip 在其后
        # （index._prepare_ast 的契约）
        old_d = os.environ.get('QOT_MACRO_DEPTH')
        old_l = os.environ.get('QOT_MACRO_LIMIT')
        try:
            if depth is None:
                os.environ.pop('QOT_MACRO_DEPTH', None)
            else:
                os.environ['QOT_MACRO_DEPTH'] = str(depth)
            if limit is None:
                os.environ.pop('QOT_MACRO_LIMIT', None)
            else:
                os.environ['QOT_MACRO_LIMIT'] = str(limit)
            path = _write('lim_case.qot', src)
            ast = parse_oning_gram(path)
            self.assertIsNotNone(ast)
            got = qot_macro.expand_program(ast, path)
            if got is not None:
                got = strip_line_numbers(got)
            return got
        finally:
            if old_d is None:
                os.environ.pop('QOT_MACRO_DEPTH', None)
            else:
                os.environ['QOT_MACRO_DEPTH'] = old_d
            if old_l is None:
                os.environ.pop('QOT_MACRO_LIMIT', None)
            else:
                os.environ['QOT_MACRO_LIMIT'] = old_l

    def test_depth_limit_default_fires(self):
        # 无限自递归宏：默认深度上限 64 必须拦住（编译期错误而非挂死）
        src = ('macro loop_m(x) { return loop_m!(x); }\n'
               'var v = loop_m!(1);\n')
        got = self._expand(src)
        self.assertIsNone(got, '无限自递归宏未被深度上限拦住')

    def test_depth_limit_env_one(self):
        src = ('macro id1_m(x) { return x; }\n'
               'macro wrap_m(x) { return id1_m!(x); }\n'
               'var v = wrap_m!(1);\n')
        # depth=1：体预展开（深度1）→ wrap 调用（深度2）→ 超限
        got = self._expand(src, depth=1)
        self.assertIsNone(got, 'QOT_MACRO_DEPTH=1 未触发超限')

    def test_step_limit_one_fires(self):
        src = ('macro id1_m(x) { return x; }\n'
               'var v = id1_m!(1);\n')
        got = self._expand(src, limit=1)
        self.assertIsNone(got, 'QOT_MACRO_LIMIT=1 未触发超限')

    def test_zero_means_unlimited(self):
        src = ('macro id1_m(x) { return x; }\n'
               'var v = id1_m!(1);\n')
        got = self._expand(src, depth=0, limit=0)
        self.assertIsNotNone(got, '0 = 不限，正常宏必须通过')


class TestMacroSemantics(unittest.TestCase):
    """官方宏 + 用户宏的展开语义（进程内求值）。"""

    def _eval(self, src):
        path = _write('sem_case.qot', src)
        ast = parse_oning_gram(path)
        self.assertIsNotNone(ast)
        ast = expand_program(ast, path)
        self.assertIsNotNone(ast)
        from index import Evaluator
        ev = Evaluator()
        ev.set_base_path(path)
        ev.evaluate(ast)
        return ev

    def test_official_swap(self):
        ev = self._eval('var a1 = 1; var b1 = 2; swap!(a1, b1);\n'
                        'var out1 = a1 * 10 + b1;\n')
        # swap 后 a1=2, b1=1 → out1 = 21
        self.assertEqual(ev.variables['out1'][1], 21)

    def test_official_unless(self):
        ev = self._eval('var t1 = 0;\n'
                        'unless!(t1 == 1) { t1 = t1 + 5; }\n'
                        'unless!(t1 == 5) { t1 = 99; }\n')
        self.assertEqual(ev.variables['t1'][1], 5)

    def test_user_expr_macro_and_nesting(self):
        ev = self._eval(
            'macro twice9(x) { return &< "kind": "binary_op", "op": "+",'
            ' "left": x, "right": x >&; }\n'
            'macro quad9(x) { return twice9!(twice9!(x)); }\n'
            'var r9 = quad9!(3);\n')
        self.assertEqual(ev.variables['r9'][1], 12)

    def test_body_macro_value_semantics(self):
        # 体内的表达式位宏调用 = 值语义（实参先求值再展开）
        ev = self._eval(
            'macro inc9(x) { return &< "kind": "binary_op", "op": "+",'
            ' "left": x, "right": &< "kind": "literal", "value": 1 >& >&; }\n'
            'macro inc29(x) { return inc9!(inc9!(x)); }\n'
            'var r8 = inc29!(40);\n')
        self.assertEqual(ev.variables['r8'][1], 42)

    def test_no_macro_fastpath_untouched(self):
        # 无宏源：expand_program 原样返回（同一对象），零改写
        path = _write('nomacro.qot', 'var zz = 1 + 2;\n')
        ast = parse_oning_gram(path)
        got = expand_program(ast, path)
        self.assertIs(got, ast)


class TestUnaryNot(unittest.TestCase):
    """一元逻辑非 `!`（GOD-SELF 收尾交付）：AST 形态 + truthy 取反 +
    与宏调用 name!( 的文法消歧。"""

    def test_ast_shape_and_precedence(self):
        path = _write('not_shape.qot',
                      'var a1 = !0;\n'
                      'var b1 = !0 + 1;\n'      # (!0) + 1 = 1
                      'var c1 = !!1;\n'          # 右结合：!(!1)
                      'var d1 = 3 != 4;\n')      # != 不受影响
        ast = parse_oning_gram(path)
        self.assertIsNotNone(ast)
        kinds = []

        def walk(n):
            if isinstance(n, tuple):
                kinds.append(n[0])
                if n[0] == 'unary_op':
                    self.assertIn(n[1], ('UMINUS', 'NOT'))
                for x in n[1:]:
                    walk(x)
            elif isinstance(n, list):
                for x in n:
                    walk(x)
        walk(ast)
        self.assertIn('unary_op', kinds)

    def _run_interp_capture(self, src):
        path = _write('not_run.qot', src)
        import subprocess
        p = subprocess.run([PY, 'index.py', 'run', path], cwd=ROOT,
                           capture_output=True, text=True, encoding='utf-8',
                           errors='replace', timeout=60)
        return p

    def test_truthy_semantics_both_kinds(self):
        p = self._run_interp_capture(
            'if (!0) { output("n1"); }\n'
            'if (!"") { output("n2"); }\n'
            'if (!null) { output("n3"); }\n'
            'if (!1) { output("BAD"); } else { output("n4"); }\n'
            'if (!"x") { output("BAD"); } else { output("n5"); }\n'
            'if (![]) { output("n6"); }\n')
        self.assertEqual(p.returncode, 0, p.stderr[-300:])
        self.assertEqual(p.stdout.split(), ['n1', 'n2', 'n3', 'n4', 'n5', 'n6'])

    def test_bang_macro_vs_not_disambiguation(self):
        # 同一 token：标识符后紧跟 !( = 宏调用；表达式前缀 = 逻辑非
        p = self._run_interp_capture(
            'macro idn(x) { return x; }\n'
            'var m1 = idn!(7);\n'
            'var n1 = !0;\n'
            'output(m1 + n1);\n')
        self.assertEqual(p.returncode, 0, p.stderr[-300:])
        self.assertIn('8', p.stdout)


if __name__ == '__main__':
    unittest.main()
