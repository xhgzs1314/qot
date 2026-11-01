# -*- coding: utf-8 -*-
"""F1/F2 闭包与异常的两侧差分裁判（ 建立）。

宪法 6 / §8.8.4 交付清单：同一份源码在 AOT（native）与解释器（oracle）两
侧跑一遍，stdout（经【数字格式归一】）与退出码必须一致。

归一规则（S-13 的显式登记，不许掩盖真实差异 —— 只做字面格式归一）：
  native 的数字定价是 %f（`1` → `1.000000`），解释器是 `1`。
  本裁判把每行中「整数位相同的 X.000000」折叠为 X —— 该变换只可能
  把两侧对同一数字的两种字面写法对齐；任何真实语义差异（值不同、
  行序不同、布尔/字符串不同）仍会判 FAIL。
"""
import os
import re
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TESTING = os.path.dirname(HERE)
ROOT = os.path.dirname(TESTING)
for p in (ROOT, TESTING):
    if p not in sys.path:
        sys.path.insert(0, p)

ART = os.path.join(TESTING, '.artifacts', '_closetry')

_NUM_FMT = re.compile(r'(-?\d+)\.000000(?=\b|$)')


def _norm(text):
    """数字格式归一（见模块 docstring）+ 空白归一。"""
    lines = []
    for ln in (text or '').replace('\r\n', '\n').split('\n'):
        ln = _NUM_FMT.sub(r'\1', ln).rstrip()
        if ln.strip():
            lines.append(ln)
    return lines


def _run(args, timeout=180):
    try:
        p = subprocess.run(args, cwd=ROOT, capture_output=True, timeout=timeout,
                           encoding='utf-8', errors='replace')
        return p.returncode, p.stdout or '', p.stderr or ''
    except subprocess.TimeoutExpired:
        return 'TIMEOUT', '', ''


def _has_cxx():
    sys.path.insert(0, os.path.join(TESTING, 'cpp'))
    try:
        import run_cpp_tests
        return run_cpp_tests.find_gxx() is not None or \
            run_cpp_tests.find_cl() is not None
    except Exception:
        return False


HAS_CXX = _has_cxx()

# ---------------------------------------------------------------- 用例源码
# 每条 = (名字, 源码, 期望退出码)。stdout 由两侧对账，不写死在源码边。
CASES = [
    ('clo_counter', '''
number_t func make_counter() {
    var n = 0;
    number_t func step() {
        n = n + 1;
        return n;
    }
    return step;
}
var c1 = make_counter();
var c2 = make_counter();
output(c1());
output(c1());
output(c2());
''', 0),
    ('clo_shared_capture', '''
number_t func make_pair_ops() {
    var total = 0;
    number_t func add_to(k) {
        total = total + k;
        return total;
    }
    number_t func get_total() {
        return total;
    }
    return add_to;
}
var adder = make_pair_ops();
output(adder(10));
output(adder(5));
''', 0),
    ('clo_lambda', '''
var add = func(a, b) -> a + b;
var inc = func(x) -> add(x, 1);
output(add(3, 4));
output(inc(41));
''', 0),
    ('clo_lambda_captures', '''
number_t func make_adder(k) {
    return func(x) -> x + k;
}
var add7 = make_adder(7);
var add3 = make_adder(3);
output(add7(1));
output(add3(1));
''', 0),
    ('clo_recursion', '''
number_t func make_fib() {
    number_t func fib(n) {
        if (n < 2) { return n; }
        return fib(n - 1) + fib(n - 2);
    }
    return fib;
}
var f = make_fib();
output(f(15));
''', 0),
    ('clo_loop_capture', '''
// 既有定价（Python 同款）：闭包按引用捕获【变量】而非每次迭代的值 ——
// 循环结束后读取是最后一次写入（3）。
number_t func make_getter() {
    var i = 0;
    loop (i < 3) {
        number_t func get_i() {
            return i;
        }
        i = i + 1;
    }
    return get_i;
}
var g = make_getter();
output(g());
''', 0),
    ('try_basic', '''
output("before");
try {
    output("in-try");
    throw "boom";
} catch (e) {
    output("caught");
    output(e);
}
output("after");
''', 0),
    ('try_value_types', '''
try {
    throw 42;
} catch (e) {
    output(e + 1);
}
try {
    throw [1, 2];
} catch (e) {
    output(glen(e));
}
''', 0),
    ('try_nested_rethrow', '''
try {
    try {
        throw "inner";
    } catch (e) {
        output("inner-caught");
        throw e;
    }
} catch (e2) {
    output("outer-caught");
    output(e2);
}
''', 0),
    ('try_return_inside', '''
number_t func f() {
    try {
        return 7;
    } catch (e) {
        return -1;
    }
}
output(f());
''', 0),
    ('try_stop_inside', '''
var i = 0;
loop (i < 10) {
    try {
        if (i == 2) { stop; }
        output(i);
    } catch (e) {
        output("never");
    }
    i = i + 1;
}
output("done");
''', 0),
    ('err_meta', '''
var e = err("payload");
output(is_err(e));
output(is_err(7));
output(err_val(e));
output(err_val(9));
''', 0),
    ('throw_err_object', '''
try {
    throw err("wrapped");
} catch (x) {
    output(is_err(x));
    output(err_val(x));
}
''', 0),
    ('throw_deep_chain', '''
number_t func level3() {
    throw "deep";
    return 0;
}
number_t func level2() {
    return level3();
}
number_t func level1() {
    return level2();
}
try {
    output(level1());
} catch (e) {
    output("caught-deep");
    output(e);
}
''', 0),
    ('closure_in_try', '''
number_t func make() {
    var saved = 0;
    number_t func grab(v) {
        saved = v;
        return saved;
    }
    return grab;
}
var g = make();
try {
    output(g(11));
    throw "x";
} catch (e) {
    output(g(22));
}
''', 0),
]


@unittest.skipUnless(HAS_CXX, '无 C++ 编译器，跳过两侧差分裁判')
class ClosureTryDifferential(unittest.TestCase):
    """AOT（native 产品面）与解释器（oracle）的逐用例对账。"""

    def _both(self, name, src):
        os.makedirs(ART, exist_ok=True)
        qot = os.path.join(ART, name + '.qot')
        with open(qot, 'w', encoding='utf-8', newline='\n') as f:
            f.write(src)
        exe = os.path.join(ART, name + '.exe')
        if os.path.exists(exe):
            os.remove(exe)
        _run([sys.executable, 'index.py', 'aot', qot, exe])
        if not os.path.exists(exe):
            self.fail('AOT 编译失败: %s' % name)
        a = _run([exe])
        i = _run([sys.executable, 'index.py', 'run', qot])
        return a, i

    def test_differential_battery(self):
        for name, src, want_rc in CASES:
            with self.subTest(case=name):
                a, i = self._both(name, src.lstrip('\n'))
                self.assertEqual(a[0], want_rc,
                                 '[%s] native 退出码 %s（stderr=%r）'
                                 % (name, a[0], a[2][:200]))
                self.assertEqual(i[0], want_rc,
                                 '[%s] 解释器退出码 %s（stderr=%r）'
                                 % (name, i[0], i[2][:200]))
                self.assertEqual(_norm(a[1]), _norm(i[1]),
                                 '[%s] 两侧输出不一致\nnative: %r\ninterp: %r'
                                 % (name, _norm(a[1]), _norm(i[1])))

    def test_uncaught_throw_contract_agrees(self):
        """未捕获 throw = 确定性致命：stderr `[错误] [2001] ...` + exit 124，
        两引擎逐字节同格式。"""
        src = ('number_t func level3() {\n'
               '    throw "deep";\n'
               '    return 0;\n'
               '}\n'
               'number_t func level2() {\n'
               '    return level3();\n'
               '}\n'
               'number_t func level1() {\n'
               '    return level2();\n'
               '}\n'
               'output(level1());\n')
        a, i = self._both('uncaught_chain', src)
        for tag, r in (('native', a), ('interp', i)):
            self.assertEqual(r[0], 124, '[%s] 退出码应为 124，实际 %s' % (tag, r[0]))
            self.assertIn('[错误] [2001] 未捕获的 throw: deep', r[2],
                          '[%s] stderr 契约不符: %r' % (tag, r[2]))
            self.assertNotIn('never', _norm(r[1]) and ''.join(r[1]),
                             '[%s] throw 之后不应继续执行' % tag)


if __name__ == '__main__':
    unittest.main()
