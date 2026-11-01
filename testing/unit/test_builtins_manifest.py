# -*- coding: utf-8 -*-
"""内置函数单一清单（M1）的对账单测 —— G-5 根治的常驻护栏。

三件事：
  1. 清单自身健全（名字唯一、engines 合法、别名不撞车）；
  2. 三侧（解释器 / AOT / VM 生成物）与清单一致；
  3. **故意删掉一侧注册必须失败**（M1 验收门）—— 用 QOT_BUILTINS_TOML
     注入一份残缺清单，断言装载即抛错，而不是静默少一个内置。
"""
import io
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

import qot_builtins as qb  # noqa: E402

MANIFEST = qb.manifest_path()


def _read_manifest():
    with io.open(MANIFEST, encoding='utf-8') as f:
        return f.read()


class TestManifestSelf(unittest.TestCase):
    def test_loads_and_unique(self):
        es = qb.entries()
        self.assertTrue(len(es) > 100)
        names = [e['name'] for e in es]
        self.assertEqual(len(names), len(set(names)))

    def test_engines_are_declared_with_impl(self):
        for e in qb.entries():
            if 'interp' in e['engines'] and e['interp'] != qb.STUB_UNSUPPORTED:
                self.assertTrue(e['interp'])
            if 'vm' in e['engines']:
                self.assertTrue(e['vm'], e['name'])
            if 'native' in e['engines']:
                self.assertTrue(e['native'], e['name'])

    def test_aliases_do_not_collide(self):
        m = qb.alias_map()
        canon = set(e['name'] for e in qb.entries())
        self.assertEqual(set(m) & canon, set())

    def test_known_g5_divergence_is_registered(self):
        """G-5 实证：`type` / `numbers` 是 VM/AOT 有、解释器没有的内置。"""
        for nm in ('type', 'numbers'):
            e = qb.by_name()[nm]
            self.assertNotIn('interp', e['engines'])
            self.assertIn('vm', e['engines'])


class TestThreeSides(unittest.TestCase):
    def test_interp_table_matches_manifest(self):
        from builtin_func import BuiltFunc
        b = BuiltFunc()
        self.assertEqual(set(b.functions), set(qb.language_names()))
        for alias, canon in qb.alias_map().items():
            self.assertIs(b.functions[alias], b.functions[canon])

    def test_aot_supported_matches_manifest(self):
        from lir_cgen import SUPPORTED_BUILTINS
        self.assertEqual(set(SUPPORTED_BUILTINS), set(qb.aot_supported()))
        qb.check_aot_symbols(SUPPORTED_BUILTINS)

    def test_vm_generated_header_is_in_sync(self):
        gen = os.path.join(ROOT, 'ot-asset', 'c', 'vmcshit', 'builtins_manifest.h')
        self.assertTrue(os.path.exists(gen))
        sys.path.insert(0, ROOT)
        import _gen_builtins
        self.assertEqual(_gen_builtins.main(['--check']), 0)

    def test_vm_registration_uses_macro(self):
        cpp = os.path.join(ROOT, 'ot-asset', 'c', 'vmcshit', 'builtin.cpp')
        with io.open(cpp, encoding='utf-8') as f:
            src = f.read()
        self.assertIn('builtins_manifest.h', src)
        self.assertIn('QOT_VM_BUILTINS(QOT_REG_BUILTIN)', src)
        # 手写名单不许复活
        self.assertNotIn('registerFunction("output"', src)

    def test_whitelists_come_from_manifest(self):
        import lir_opt
        self.assertEqual(lir_opt.NUMERIC_BUILTINS, qb.numeric_builtins())
        self.assertEqual(lir_opt.ALLOC_FREE_BUILTINS, qb.alloc_free_builtins())


class TestMissingSideFailsBuild(unittest.TestCase):
    """M1 验收门：故意删掉一侧注册 → 构建/装载失败，而不是静默少一个。"""

    def _write_temp(self, text):
        p = os.path.join(TESTING, '.artifacts', '_builtins_tmp.toml')
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with io.open(p, 'w', encoding='utf-8') as f:
            f.write(text)
        return p

    def _run_with(self, text, code):
        p = self._write_temp(text)
        env = dict(os.environ)
        env['QOT_BUILTINS_TOML'] = p
        env['PYTHONIOENCODING'] = 'utf-8'
        # 注意：子进程输出是 UTF-8，父进程默认按本机 locale(gbk) 解码会把
        # 中文回溯炸成 UnicodeDecodeError（并把 stderr 变成 None）—— 显式
        # 指定 encoding，否则"失败原因"反而拿不到。
        r = subprocess.run([sys.executable, '-c', code], cwd=ROOT, env=env,
                           capture_output=True, encoding='utf-8',
                           errors='replace')
        r.out = (r.stdout or '') + (r.stderr or '')
        return r

    def test_interp_impl_name_mismatch_fails(self):
        """清单里写错实现函数名 → 装载即失败（interp 字段不是摆设）。"""
        bad = _read_manifest().replace('interp = "output_handler"',
                                       'interp = "nope_handler"')
        r = self._run_with(bad, 'from builtin_func import BuiltFunc; BuiltFunc()')
        self.assertNotEqual(r.returncode, 0, r.out)
        self.assertIn('BuiltinManifestError', r.out)
        self.assertIn('实现对不上', r.out)

    def test_drop_interp_entry_fails(self):
        """清单里整条删掉一个解释器内置 → 「有实现但清单未登记」。"""
        text = _read_manifest()
        i = text.index('[[builtin]]\nname = "output"')
        j = text.index('[[builtin]]', i + 10)
        r = self._run_with(text[:i] + text[j:],
                           'from builtin_func import BuiltFunc; BuiltFunc()')
        self.assertNotEqual(r.returncode, 0, r.out)
        self.assertIn('清单未登记', r.out)

    def test_unknown_engine_fails(self):
        bad = _read_manifest().replace('engines = ["interp", "vm", "native"]',
                                       'engines = ["gpu"]', 1)
        r = self._run_with(bad, 'import qot_builtins as q; q.entries()')
        self.assertNotEqual(r.returncode, 0, r.out)

    def test_duplicate_alias_fails(self):
        # 【S-11 终审 】split/trim 别名已摘除（曾挂在 interp-only 的
        # parts/beair 上，暗示不存在的跨引擎支持）—— 重复别名示例改用 str。
        bad = _read_manifest().replace('aliases = ["len"]', 'aliases = ["str"]', 1)
        r = self._run_with(bad, 'import qot_builtins as q; q.alias_map()')
        self.assertNotEqual(r.returncode, 0, r.out)


class TestAliasSwitch(unittest.TestCase):
    def test_no_alias_switch_disables_aliases(self):
        code = ('import os;'
                'import qot_builtins as q;'
                'print(len(q.alias_map()))')
        env = dict(os.environ)
        env['PYTHONIOENCODING'] = 'utf-8'
        env['QOT_NO_ALIAS'] = '1'
        r = subprocess.run([sys.executable, '-c', code], cwd=ROOT, env=env,
                           capture_output=True, encoding='utf-8',
                           errors='replace')
        self.assertEqual(r.returncode, 0, (r.stdout or '') + (r.stderr or ''))
        self.assertEqual((r.stdout or '').strip(), '0')


if __name__ == '__main__':
    unittest.main()
