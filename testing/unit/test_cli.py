# -*- coding: utf-8 -*-
"""CLI 契约测试（L3）—  T-18。

断言三件套：退出码 + stdout + stderr 归属（ R2）。
⚠ R7：绝不调用 `build --mode 1`，它内部有 input() 会挂住 CI。
"""
import os
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))          # testing/unit
TESTING = os.path.dirname(HERE)
ROOT = os.path.dirname(TESTING)
ART = os.path.join(TESTING, '.artifacts')
PY = sys.executable
INDEX = os.path.join(ROOT, 'index.py')

VM_CASE = os.path.join(TESTING, 'cases', 'native', 't01_arith.qot')


def qot(*args, **kw):
    return subprocess.run([PY, INDEX] + list(args), cwd=ROOT,
                          capture_output=True, text=True,
                          encoding='utf-8', errors='replace', **kw)


class EnvTest(unittest.TestCase):
    def test_layout(self):
        self.assertTrue(os.path.exists(INDEX), INDEX)
        self.assertTrue(os.path.exists(VM_CASE), VM_CASE)
        os.makedirs(ART, exist_ok=True)


class RunContractTest(unittest.TestCase):
    def test_no_args(self):
        r = qot()
        self.assertEqual(1, r.returncode)
        self.assertIn('命令语法错误', r.stderr)

    def test_unknown_command(self):
        r = qot('definitely-not-a-command')
        self.assertEqual(1, r.returncode)
        self.assertIn('未知命令', r.stderr)

    def test_help_lists_commands(self):
        r = qot('help')
        self.assertEqual(0, r.returncode)
        self.assertIn('- run [filepath]', r.stdout)
        self.assertIn('bstrust', r.stdout)

    def test_run_missing_file(self):
        r = qot('run', 'no_such_file.qot')
        self.assertEqual(1, r.returncode)
        self.assertIn('找不到指定的文件', r.stderr)

    def test_run_bad_extension(self):
        p = os.path.join(ART, '_cli_bad.txt')
        with open(p, 'w', encoding='utf-8') as f:
            f.write('var x = 1;\n')
        r = qot('run', p)
        self.assertEqual(1, r.returncode)
        self.assertIn('必须以.qot或.qbc为后缀', r.stderr)

    def test_run_ok_output_on_stdout(self):
        """正常输出必须在 stdout，且 stderr 为空（R2 的关键契约）"""
        r = qot('run', VM_CASE)
        self.assertEqual(0, r.returncode)
        self.assertIn('14', r.stdout)
        self.assertEqual('', r.stderr.strip())


class BstrustContractTest(unittest.TestCase):
    def test_no_subcommand(self):
        r = qot('bstrust')
        self.assertEqual(1, r.returncode)
        self.assertIn('用法: bstrust', r.stdout)

    def test_unknown_subcommand(self):
        r = qot('bstrust', 'nope')
        self.assertEqual(1, r.returncode)
        self.assertIn('未知子命令', r.stdout)

    def test_compile_missing_source(self):
        r = qot('bstrust', 'compile', 'nope.qot', os.path.join(ART, '_x.qtc'))
        self.assertEqual(1, r.returncode)
        self.assertIn('源文件不存在', r.stderr)

    def test_compile_bad_extension(self):
        p = os.path.join(ART, '_cli_bad2.txt')
        with open(p, 'w', encoding='utf-8') as f:
            f.write('x')
        r = qot('bstrust', 'compile', p, os.path.join(ART, '_x.qtc'))
        self.assertEqual(1, r.returncode)
        self.assertIn('源文件必须是.qot后缀', r.stderr)

    def test_compile_success_prints_to_stdout(self):
        out = os.path.join(ART, '_cli_ok.qtc')
        if os.path.exists(out):
            os.remove(out)
        r = qot('bstrust', 'compile', VM_CASE, out)
        self.assertEqual(0, r.returncode)
        self.assertIn('编译成功', r.stdout)
        self.assertTrue(os.path.exists(out))

    def test_run_missing_qtc(self):
        r = qot('bstrust', 'run', 'nope.qtc')
        self.assertEqual(1, r.returncode)
        self.assertIn('文件不存在', r.stderr)

    def test_run_bad_extension(self):
        p = os.path.join(ART, '_cli_bad3.txt')
        with open(p, 'w', encoding='utf-8') as f:
            f.write('x')
        r = qot('bstrust', 'run', p)
        self.assertEqual(1, r.returncode)
        self.assertIn('文件必须是.qtc后缀', r.stderr)

    def test_compile_opt_flag(self):
        out = os.path.join(ART, '_cli_opt.qtc')
        r = qot('bstrust', 'compile', VM_CASE, out, '--opt')
        self.assertEqual(0, r.returncode)
        self.assertTrue(os.path.exists(out))

    def test_syntax_error_goes_to_stderr_and_no_artifact(self):
        """语法错误：只走 stderr，stdout 干净，且不产出 .qtc（SPEC R2）"""
        bad = os.path.join(ART, '_cli_badsyn.qot')
        with open(bad, 'w', encoding='utf-8') as f:
            f.write('var x = 10\noutput(x);\n')
        out = os.path.join(ART, '_cli_badsyn.qtc')
        if os.path.exists(out):
            os.remove(out)
        r = qot('bstrust', 'compile', bad, out)
        self.assertNotEqual(0, r.returncode)
        self.assertIn('[错误]', r.stderr)
        self.assertNotIn('[错误]', r.stdout)
        self.assertFalse(os.path.exists(out), '语法错误不应产出 .qtc')


class DistrustContractTest(unittest.TestCase):
    def test_roundtrip(self):
        out = os.path.join(ART, '_cli_t.qbc')
        r = qot('distrust', VM_CASE, out)
        self.assertEqual(0, r.returncode)
        self.assertIn('重构完成', r.stdout)
        self.assertTrue(os.path.exists(out))
        r2 = qot('run', out)
        self.assertEqual(0, r2.returncode)
        self.assertIn('14', r2.stdout)


class InteractiveHazardTest(unittest.TestCase):
    """R7 守卫：build --mode 1 内部有 input()，CI 绝不能调用。

    若哪天交互被移除，这条会失败 —— 那是提醒你去更新  R7，不是 bug。
    """

    def test_mode1_is_interactive_documented(self):
        src = open(INDEX, encoding='utf-8').read()
        self.assertIn('确定要继续吗', src,
                      'index.py 里找不到 build --mode 1 的交互式提问；'
                      '若交互已移除，请更新  R7 并允许 CI 跑 mode 1')


if __name__ == '__main__':
    unittest.main()
