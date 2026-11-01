# -*- coding: utf-8 -*-
"""M6 包流水线单测（）。

覆盖三件事：
  1. 清单解析与校验（缺段/缺字段 = 明确报错，不静默）；
  2. `pkg build` 的**插件编排**（[[plugin]] → g++ -shared → build/plugins/，
     产物 = M7 v2 ABI 的 DLL ——「dll 输出可用」的 native 形态）；
  3. `pkg run` 端到端（构建 + 运行 + args 透传，cwd=包目录）。

插件源复用 testing/tools/qot_demo_plugin.c（M7 的差分插件，双宿主已验证），
本测试只关心流水线把**同一个源**装进包并编排构建。
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
PY = sys.executable

PLUGIN_SRC = os.path.join(ROOT, 'testing', 'tools', 'qot_demo_plugin.c')

MANIFEST = '''[package]
name = "pkgtest"
version = "0.1.0"
entry = "main.qot"

[[plugin]]
name = "demo"
source = "{src}"
'''

MAIN = '''var h = plugin_load("build/plugins/demo.dll");
output(is_null(h));
output(plugin_call(h, "add", [2.0, 3.0]));
output(plugin_free(h));
'''


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        f.write(text)


def _pkg(argv, **kw):
    return subprocess.run([PY, os.path.join(ROOT, 'index.py'), 'pkg'] + argv,
                          cwd=ROOT, capture_output=True, text=True,
                          encoding='utf-8', errors='replace', **kw)


class TestPkgManifest(unittest.TestCase):
    """清单解析：缺段/缺字段必须显式失败（R13 同款门）。"""

    def _mk(self, manifest_text):
        d = tempfile.mkdtemp(prefix='qot_pkg_')
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        _write(os.path.join(d, 'qot.pkg'), manifest_text)
        return d

    def test_missing_manifest(self):
        d = tempfile.mkdtemp(prefix='qot_pkg_')
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        p = _pkg(['build', d])
        self.assertNotEqual(p.returncode, 0)
        self.assertIn('qot.pkg', p.stderr)

    def test_missing_entry_field(self):
        d = self._mk('[package]\nname = "x"\n')
        p = _pkg(['build', d])
        self.assertNotEqual(p.returncode, 0)
        self.assertIn('entry', p.stderr)

    def test_plugin_missing_source(self):
        d = self._mk(MANIFEST.format(src='nope/missing.c'))
        _write(os.path.join(d, 'main.qot'), 'output(1);')
        p = _pkg(['build', d])
        self.assertNotEqual(p.returncode, 0)
        self.assertIn('插件源不存在', p.stderr)


class TestPkgPipeline(unittest.TestCase):
    """build（插件编排）+ run 端到端。编译型，归类为慢测。"""

    @classmethod
    def setUpClass(cls):
        # e2e 包放仓库 artifacts 内（tempfile 可能跨盘，relpath 会炸）；
        # 同盘 relpath 才能把插件源写进清单的相对路径。
        cls.pkg = os.path.join(ROOT, 'testing', '.artifacts', 'pkg_e2e')
        shutil.rmtree(cls.pkg, ignore_errors=True)
        os.makedirs(cls.pkg, exist_ok=True)
        _write(os.path.join(cls.pkg, 'qot.pkg'),
               MANIFEST.format(src=os.path.relpath(PLUGIN_SRC, cls.pkg)
                               .replace('\\', '/')))
        _write(os.path.join(cls.pkg, 'main.qot'), MAIN)
        cls._cleanup_done = False

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.pkg, ignore_errors=True)

    def test_10_build_produces_exe_and_plugin_dll(self):
        p = _pkg(['build', self.pkg])
        self.assertEqual(p.returncode, 0, p.stderr[-500:])
        exe = os.path.join(self.pkg, 'build', 'pkgtest.exe')
        dll = os.path.join(self.pkg, 'build', 'plugins', 'demo.dll')
        self.assertTrue(os.path.isfile(exe), '缺 AOT 产物')
        self.assertTrue(os.path.isfile(dll), '缺插件 DLL（dll 输出验收）')

    def test_20_run_end_to_end(self):
        p = _pkg(['run', self.pkg, '--'])
        self.assertEqual(p.returncode, 0, p.stderr[-500:])
        self.assertIn('false', p.stdout)   # plugin_load 成功（is_null = false）
        self.assertIn('5', p.stdout)       # add(2,3) = 5
        self.assertIn('true', p.stdout)    # plugin_free 成功

    def test_30_init_template(self):
        # 目录放仓库 artifacts 内；清理只删**自建目录自身**（绝不可删其父目录）
        d = os.path.join(ROOT, 'testing', '.artifacts', 'pkg_init_t')
        shutil.rmtree(d, ignore_errors=True)
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        p = _pkg(['init', d])
        self.assertEqual(p.returncode, 0, p.stderr[-500:])
        self.assertTrue(os.path.isfile(os.path.join(d, 'qot.pkg')))
        self.assertTrue(os.path.isfile(os.path.join(d, 'main.qot')))
        # 拒绝覆盖已存在的清单（幂等保护）
        p2 = _pkg(['init', d])
        self.assertNotEqual(p2.returncode, 0)


if __name__ == '__main__':
    unittest.main()
