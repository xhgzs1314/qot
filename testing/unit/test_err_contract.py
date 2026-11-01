# -*- coding: utf-8 -*-
"""错误契约对齐用例集（R2 裁判； 建立， 转【两方版】）。

宪法 6 要求：错误必须有**确定契约**（错误码 + stderr 格式 + 退出码）。
VM 弃管后（）本文件收敛为 **AOT ↔ 解释器两方** 裁判（CP-N3/T6
的实际范围）：同一份源码在两侧跑，断言契约项。

断言分三类（读代码时务必分清）：
  1. **同律钉** —— 两侧行为一致，回归即 FAIL（契约主体）；
  2. **登记定价钉** —— 两侧**刻意不同**（发布面宽松 / 开发面严格），钉住
     差异形状本身：错形状 = FAIL（防止"既定分歧"变成"未定义行为"）；
  3. 作废钉 —— VM 相关（M2-EC1~4），skip 保留考古。

【 CP-N3 交付】S-10 对齐落地：两侧递归深度上限同律（5000，
QOT_MAX_DEPTH 覆盖）、同文案（`[错误] [2000] 递归深度超限（depth=N，上限 M）`）、
同退出码（124）。另修复：interp 语法错误曾 exit 0（静默落空）→ 现在 exit 1。
"""
import os
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TESTING = os.path.dirname(HERE)
ROOT = os.path.dirname(TESTING)
for p in (ROOT, TESTING):
    if p not in sys.path:
        sys.path.insert(0, p)

ART = os.path.join(TESTING, '.artifacts', '_errcontract')


def _has_cxx():
    sys.path.insert(0, os.path.join(TESTING, 'cpp'))
    try:
        import run_cpp_tests
        return run_cpp_tests.find_gxx() is not None or run_cpp_tests.find_cl() is not None
    except Exception:
        return False


HAS_CXX = _has_cxx()


def _run(args, timeout=180):
    try:
        p = subprocess.run(args, cwd=ROOT, capture_output=True, timeout=timeout,
                           encoding='utf-8', errors='replace')
        return p.returncode, p.stdout or '', p.stderr or ''
    except subprocess.TimeoutExpired:
        return 'TIMEOUT', '', ''


class Engine:
    """同一份源码在三侧的执行结果。"""

    def __init__(self, name, src):
        self.name = name
        self.src = src
        os.makedirs(ART, exist_ok=True)
        self.qot = os.path.join(ART, name + '.qot')
        with open(self.qot, 'w', encoding='utf-8', newline='\n') as f:
            f.write(src)
        self.exe = os.path.join(ART, name + '.exe')
        self.qtc = os.path.join(ART, name + '.qtc')

    def aot(self):
        if os.path.exists(self.exe):
            os.remove(self.exe)
        _run([sys.executable, 'index.py', 'aot', self.qot, self.exe])
        if not os.path.exists(self.exe):
            return None
        return _run([self.exe], timeout=120)

    def vm(self):
        if os.path.exists(self.qtc):
            os.remove(self.qtc)
        _run([sys.executable, 'index.py', 'bstrust', 'compile', self.qot, self.qtc])
        if not os.path.exists(self.qtc):
            return None
        return _run([sys.executable, 'index.py', 'bstrust', 'run', self.qtc])

    def interp(self):
        return _run([sys.executable, 'index.py', 'run', self.qot])


@unittest.skipUnless(HAS_CXX, '无 C++ 编译器，跳过三侧错误契约裁判')
class ErrContractTest(unittest.TestCase):
    def _three(self, name, src):
        e = Engine(name, src)
        return e.aot(), e.vm(), e.interp()

    # ---------------- 1. 已一致：字典缺键（三侧都是静默 null） ----------------
    def test_dict_missing_key_agrees(self):
        a, v, i = self._three('ec_dict_missing',
                              'var d = &< "a": 1 >&;\noutput(d["zz"]);\n')
        for tag, r in (('aot', a), ('vm', v), ('interp', i)):
            self.assertIsNotNone(r, '%s 未产出可执行产物' % tag)
            self.assertEqual(r[0], 0, '%s 退出码 %s' % (tag, r[0]))
            self.assertEqual(r[1].strip(), 'null', '%s 输出 %r' % (tag, r[1]))

    # ---------------- 2. 既定定价：发布面静默、开发面硬错 ----------------
    def test_oob_read_pricing_release_engines_silent(self):
        """README 21.4 的既定定价：越界读在 VM/AOT 得 null 且不中断。"""
        a, v, _ = self._three('ec_oob_read',
                              'var a = [1, 2, 3];\noutput(a[10]);\n')
        self.assertEqual(a[0], 0)
        self.assertEqual(a[1].strip(), 'null')
        self.assertEqual((a[0], a[1].strip()), (v[0], v[1].strip()),
                         'AOT 与 VM 的越界读定价必须一致')

    # ---------------- 3. M2-EC1：push 非数组的诊断，AOT 与 VM 必须一致 ----------------
    # 【 用户裁决】VM 线彻底弃管（不碰/不测/其报错不管）→ M2-EC1~4
    # 四钉**作废**（不是"稍后修"）：它们断言的收敛目标全部以 VM 侧行为为
    # 准，VM 出往后该目标不存在。此后的错误契约写 AOT 与解释器两方同律，
    # 即 CP-N3/T6 的实际范围（见  §8.16）。
    @unittest.skip('VM 线已弃管（）：本钉作废')
    def test_push_non_array_diagnosis_agrees(self):
        """契约：push 非数组在 AOT 与 VM 都应有诊断（现状：VM 有、AOT 静默）。"""
        a, v, _ = self._three('ec_push_non_array',
                              'var x = 5;\noutput(push(x, 1));\noutput("end");\n')
        self.assertEqual(a[1].strip(), v[1].strip(), 'stdout 应一致')
        self.assertTrue(a[2].strip(), 'AOT 侧期望有诊断，实际 stderr 为空')
        self.assertTrue(v[2].strip(), 'VM 侧期望有诊断')

    # ---------------- 4. M2-EC2：诊断的错误码格式三方一致 ----------------
    @unittest.skip('VM 线已弃管（）：本钉作废')
    def test_stderr_format_agrees(self):
        """契约：错误文案统一为 `[错误] [code] 说明`（现状三侧三种写法）。"""
        _, v, i = self._three('ec_push_non_array2',
                              'var x = 5;\noutput(push(x, 1));\n')
        self.assertIn('[错误] [', v[2], 'VM 侧文案 %r' % v[2])
        self.assertIn('[错误] [', i[2], '解释器侧文案 %r' % i[2])

    # ---------------- 5. M2-EC3：未知函数必须在编译期被拒 ----------------
    @unittest.skip('VM 线已弃管（）：本钉作废')
    def test_unknown_function_rejected_everywhere(self):
        """契约：未知函数在 VM/AOT 都是编译期错误（现状：VM 静默得 null）。"""
        a, v, _ = self._three('ec_unknown_func', 'output(nope(1));\n')
        self.assertIsNone(a, 'AOT 应当编译失败')
        self.assertIsNone(v, 'VM 应当编译失败（现状静默产出 .qtc）')

    # ---------------- 6. M2-EC4（= S-10）：递归深度上限三方一致 ----------------
    @unittest.skip('VM 线已弃管（）：本钉作废')
    def test_recursion_depth_limit_agrees(self):
        """契约：深度上限 = AOT 的 5000（超限 exit 124 + [2000]），三侧一致。

        现状：AOT exit 124 + `[错误] [2000] 递归深度超限`；VM 无上限照常跑完；
        解释器撞 Python 栈（~200 层）报 RecursionError。
        """
        src = ('number_t func f(k) {\n'
               '    if (k > 6000) { return 0; }\n'
               '    return f(k + 1) + 1;\n'
               '}\n'
               'output(f(0));\n')
        a, v, i = self._three('ec_deep_rec', src)
        self.assertEqual(a[0], 124, 'AOT 超限应 exit 124')
        self.assertIn('[2000]', a[2])
        self.assertEqual(v[0], 124, 'VM 也应当在同上限处 exit 124，实际 %s' % v[0])
        self.assertEqual(i[0], 124, '解释器也应当是同一个确定性契约')

    # ---------------- 7. AOT 的既有契约不许退化（正向钉） ----------------
    def test_aot_depth_limit_contract_holds(self):
        """AOT 的深度上限契约本身必须稳定（M2 统一时的目标形态就是它）。"""
        src = ('number_t func f(k) {\n'
               '    if (k > 6000) { return 0; }\n'
               '    return f(k + 1) + 1;\n'
               '}\n'
               'output(f(0));\n')
        a, _, _ = self._three('ec_deep_rec2', src)
        self.assertEqual(a[0], 124)
        self.assertIn('[2000]', a[2])
        self.assertIn('递归深度超限', a[2])

    # ================= CP-N3（）两方版新增钉 =================

    def _two(self, name, src):
        e = Engine(name, src)
        return e.aot(), e.interp()

    def test_recursion_depth_limit_agrees_two_party(self):
        """【S-10 终结钉】两侧递归深度契约逐位一致：exit 124 +
        `[错误] [2000] 递归深度超限（depth=N，上限 5000）`。
        interp 侧曾以 Python 栈实限（~200 层）+ 英文文案 + exit 1 兜底 ——
         对齐（求值器 QOT 级计数 + 大栈工作线程）。"""
        src = ('number_t func f(k) {\n'
               '    if (k > 6000) { return 0; }\n'
               '    return f(k + 1) + 1;\n'
               '}\n'
               'output(f(0));\n')
        a, i = self._two('ec_depth_agree', src)
        self.assertEqual(a[0], 124, 'AOT 深度超限应 exit 124')
        self.assertEqual(i[0], 124, '解释器应与 AOT 同退出码，实际 %s' % i[0])
        self.assertIn('[错误] [2000] 递归深度超限（depth=5001，上限 5000）', a[2])
        self.assertIn('[错误] [2000] 递归深度超限（depth=5001，上限 5000）', i[2],
                      '解释器文案必须与 native 逐位一致')

    def test_syntax_error_fatal_both(self):
        """【契约修复钉】语法错误必须致命：AOT 编译失败 exit 1；
        interp 曾静默 exit 0（ CP-N3 勘察抓到并修复）。"""
        a, i = self._two('ec_syntax_fatal', 'var x = ;\n')
        self.assertIsNone(a, 'AOT 语法错误必须编译失败')
        self.assertNotEqual(i[0], 0, 'interp 语法错误必须非零退出（曾为 0）')
        self.assertIn('[1000]', i[2])

    def test_unknown_function_rejected_both(self):
        """未知函数两侧都必须拒绝（阶段不同：AOT 编译期 / interp 运行期
        [3001]）—— 契约 = 不许静默成功。"""
        a, i = self._two('ec_unknown_2p', 'output(nope(1));\n')
        self.assertIsNone(a, 'AOT 应当编译失败')
        self.assertNotEqual(i[0], 0)
        self.assertIn('[3001]', i[2])

    def test_stderr_format_agrees_two_party(self):
        """两方 stderr 统一形态：`[错误] [code]` 前缀（运行期致命错误）。"""
        a, i = self._two('ec_fmt_2p', 'var a = [1, 2, 3];\noutput(a[10]);\n')
        self.assertIn('[错误] [2003]', i[2])

    def test_builtin_arity_mismatch_fatal_both(self):
        """【G-10 钉 】内置实参数量错配 = **编译期致命**。

        旧行为：软警告 + 调用不执行（`str_trim(s, ";")` 原样留分号；
        `to_array(a, b)` 返回 null 无诊断）—— 静默错跑比报错危险得多。
        契约：arity 由 builtins.toml 写死（argc >= 0）→ 静态可判 → 两侧
        都不许产出可执行文件 / 不许 exit 0。
        """
        a, i = self._two('ec_arity_fatal',
                         'var s = "python ;";\n'
                         'var t = str_trim(s, ";");\n'
                         'output("unreachable");\n')
        self.assertIsNone(a, 'AOT 必须编译失败')
        self.assertNotEqual(i[0], 0, 'interp 必须非零退出（曾 exit 0 静默）')
        self.assertIn('需要 1 个参数', i[2])
        self.assertNotIn('unreachable', i[1], '错配程序不许继续执行')
        # AOT 侧此场景为既定定价（静默 null）—— 格式钉只对"有输出"的侧生效

    def test_registered_pricing_oob_read(self):
        """【登记定价钉】越界读：发布面（AOT）静默 null + rc0；开发面
        （interp）[2003] + rc1。差异形状本身是契约 —— 任何一侧漂移即 FAIL。"""
        a, i = self._two('ec_oob_2p', 'var a = [1, 2, 3];\noutput(a[10]);\n')
        self.assertEqual(a[0], 0)
        self.assertEqual(a[1].strip(), 'null')
        self.assertEqual(i[0], 1)
        self.assertIn('[2003]', i[2])

    def test_registered_pricing_div_zero(self):
        """【登记定价钉】除零：AOT 得 0（VM 同款定价）+ rc0；interp
        [2004] + rc1。"""
        a, i = self._two('ec_divz_2p', 'output(1 / 0);\n')
        self.assertEqual(a[0], 0)
        self.assertEqual(a[1].strip(), '0.000000')
        self.assertEqual(i[0], 1)
        self.assertIn('[2004]', i[2])

    def test_dict_missing_key_agrees_two_party(self):
        """字典缺键：两侧同律静默 null + rc0（非致命、非错误定价）。"""
        a, i = self._two('ec_dict_2p',
                         'var d = &< "a": 1 >&;\noutput(d["zz"]);\n')
        self.assertEqual(a[0], 0)
        self.assertEqual(a[1].strip(), 'null')
        self.assertEqual((i[0], i[1].strip()), (0, 'null'))


if __name__ == '__main__':
    unittest.main()
