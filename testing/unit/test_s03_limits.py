# -*- coding: utf-8 -*-
"""S-03 资源上限钉：native exe 的 QOT_TIMEOUT_MS / QOT_MAX_DEPTH 行为。

无 MSVC 时 SKIP（与 native 套件同策略）。VM 侧无进程内超时机制（由调用方
执行），故超限行为是 native 独有定价；未设环境变量时行为必须与 VM 一致
（由 --suite native 差分长期守卫）。
"""
import os
import sys
import subprocess
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TESTING = os.path.dirname(HERE)
ROOT = os.path.dirname(TESTING)
sys.path.insert(0, os.path.join(TESTING, 'cpp'))


def _has_cl():
    try:
        sys.path.insert(0, os.path.join(TESTING, 'cpp'))
        import run_cpp_tests
        return run_cpp_tests.find_cl() is not None
    except Exception:
        return False


DEADLOOP = 'var i = 0;\nloop (i >= 0) { i = i + 1; }\noutput(i);\n'
DEEPREC = ('number_t func f(k) {\n'
           '    return f(k + 1) + 1;\n'
           '}\n'
           'output(f(0));\n')


@unittest.skipUnless(_has_cl(), '未找到 MSVC，跳过 S-03 native 行为钉')
class S03LimitsTest(unittest.TestCase):
    def _build_and_run(self, src, env=None, run_timeout=60):
        from index import _aot_compile
        art = os.path.join(TESTING, '.artifacts')
        os.makedirs(art, exist_ok=True)
        fd, qot = tempfile.mkstemp(suffix='.qot', dir=art)
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as f:
            f.write(src)
        # exe 名不得以 .qot 结尾（cl /Fe 对带扩展名者原样使用，会在 .qot 名下
        # 产出 PE 导致 exists 检查失败—— 实测）
        exe = qot[:-4] + '_out.exe'
        try:
            _aot_compile(qot, exe, False)
            e = dict(os.environ)
            if env:
                e.update(env)
            p = subprocess.run([exe], capture_output=True, text=True,
                               timeout=run_timeout, env=e, cwd=ROOT,
                               encoding='utf-8', errors='replace')
            return p
        finally:
            for cand in (qot, exe, exe + '.obj'):
                try:
                    os.remove(cand)
                except OSError:
                    pass

    def test_timeout_kills_deadloop(self):
        p = self._build_and_run(DEADLOOP, env={'QOT_TIMEOUT_MS': '50'})
        self.assertEqual(p.returncode, 124, p.stderr[-200:])
        self.assertIn('[2000]', p.stderr)
        self.assertIn('超时', p.stderr)

    def test_no_timeout_env_runs_normal(self):
        p = self._build_and_run('var i = 0;\ni = i + 1;\noutput(i == 1);\n')
        self.assertEqual(p.returncode, 0)
        self.assertEqual(p.stdout.strip(), 'true')

    def test_depth_limit_kills_runaway_recursion(self):
        p = self._build_and_run(DEEPREC, env={'QOT_MAX_DEPTH': '1000'},
                                run_timeout=60)
        self.assertEqual(p.returncode, 124, p.stderr[-200:])
        self.assertIn('[2000]', p.stderr)
        self.assertIn('递归深度', p.stderr)

    def test_bounded_recursion_unaffected(self):
        src = ('number_t func f(k) {\n'
               '    if (k > 5) { return 0; }\n'
               '    return f(k + 1) + 1;\n'
               '}\n'
               'output(f(0) == 6);\n')
        p = self._build_and_run(src)
        self.assertEqual(p.returncode, 0, p.stderr[-200:])
        self.assertEqual(p.stdout.strip(), 'true')


if __name__ == '__main__':
    unittest.main()
