# -*- coding: utf-8 -*-
"""QOT-CI · qot_cache 的 K2 机械校验单测（ §9.3）。

key 组件完备性的**机械**证明（不依赖跑 g++）：
  * 任一 key 组件变化 → key 必变（漏一项 = 假命中 = 静默错编）；
  * 18 个开关逐一翻转 → key 必变；
  * 编译器身份/flags/目标形态/runtime 头 hash → key 必变；
  * 组件相同 → key 稳定（确定性：同输入同 key）；
  * CAS 原子写/deliver 硬链接语义；
  * GC 的 PCH 保留策略。
"""
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ROOT = HERE
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import qot_cache as qc


class KeyCompletenessTest(unittest.TestCase):
    """K2：所有影响输出的输入必须进 key —— 逐组件机械翻转验证。"""

    BASE = {
        'csrc': 'int main(){return 0;}',
        'rt': 'a' * 64,
        'flags': ['-std=c++17', '-O2'],
        'form': 'exe',
    }

    def _key(self, **kw):
        d = dict(self.BASE)
        d.update(kw)
        return qc.toolchain_key(c_text=d['csrc'], rt_hash=d['rt'],
                                flags=d['flags'], form=d['form'])[0]

    def test_key_stable_for_same_inputs(self):
        self.assertEqual(self._key(), self._key())

    def test_csrc_change_changes_key(self):
        k1 = self._key()
        k2 = self._key(csrc=self.BASE['csrc'] + ' // x')
        self.assertNotEqual(k1, k2)

    def test_rt_change_changes_key(self):
        self.assertNotEqual(self._key(), self._key(rt='b' * 64))

    def test_flags_change_changes_key(self):
        self.assertNotEqual(self._key(), self._key(flags=['-std=c++17', '-O1']))

    def test_flags_order_change_changes_key(self):
        # flags 是列表 → 顺序敏感（保守：顺序不同即不同调用）
        self.assertNotEqual(self._key(flags=['-O2', '-std=c++17']),
                            self._key(flags=['-std=c++17', '-O2']))

    def test_form_change_changes_key(self):
        self.assertNotEqual(self._key(), self._key(form='obj'))

    def test_all_18_switches_change_key(self):
        """18 个 QOT_NO_* / QOT_UNSAFE_INDEX 全集逐个翻转 → key 必变。"""
        base_fp = dict(qc.switch_fingerprint())
        base_key = self._key()
        for sw in qc.QOT_NO_SWITCHES:
            old = os.environ.get(sw)
            try:
                qc._SWITCH_FP = None   # 指纹进程内缓存必须作废
                os.environ[sw] = '1'
                self.assertNotEqual(base_key, self._key(),
                                    '%s 未进 key（K2 漏项）' % sw)
                self.assertEqual(qc.switch_fingerprint().get(sw), '1')
            finally:
                if old is None:
                    os.environ.pop(sw, None)
                else:
                    os.environ[sw] = old
                qc._SWITCH_FP = None
        self.assertEqual(base_fp, qc.switch_fingerprint())

    def test_switch_fingerprint_covers_manifest_and_limits(self):
        fp = qc.switch_fingerprint()
        for k in ('QOT_BUILTINS_TOML', 'QOT_MAX_DEPTH', 'QOT_TIMEOUT_MS',
                  'QOT_STATIC'):
            self.assertIn(k, fp)

    def test_static_switch_changes_flags_and_key(self):
        """静态链接形态必须进 flags → 进 key（否则两种形态会共享缓存条目）。"""
        old = os.environ.get('QOT_STATIC')
        try:
            os.environ['QOT_STATIC'] = '1'
            qc._SWITCH_FP = None
            qc._COMPILER_ID = None
            sf_on = list(qc._static_link_flags())
            k_on = self._key(flags=['-std=c++17', '-O2'] + sf_on)
            os.environ['QOT_STATIC'] = '0'
            qc._SWITCH_FP = None
            sf_off = list(qc._static_link_flags())
            k_off = self._key(flags=['-std=c++17', '-O2'] + sf_off)
        finally:
            if old is None:
                os.environ.pop('QOT_STATIC', None)
            else:
                os.environ['QOT_STATIC'] = old
            qc._SWITCH_FP = None
        self.assertTrue(sf_on, '默认应为静态链接（既有行为）')
        self.assertEqual(sf_off, [], 'QOT_STATIC=0 应去掉 -static 族')
        self.assertNotEqual(k_on, k_off, '两种形态必须有不同的 toolchain key')

    def test_compiler_identity_in_key(self):
        k1 = self._key()
        saved = qc._COMPILER_ID
        try:
            qc._COMPILER_ID = {'kind': 'gcc', 'path': 'X:\\other\\g++.exe',
                               'version': 'g++ 13.0'}
            k2 = self._key()
        finally:
            qc._COMPILER_ID = saved
        self.assertNotEqual(k1, k2)

    def test_semver_in_key(self):
        k1 = self._key()
        saved = qc.SEMANTIC_VERSION
        try:
            qc.SEMANTIC_VERSION = 'qot-ci/2'
            k2 = self._key()
        finally:
            qc.SEMANTIC_VERSION = saved
        self.assertNotEqual(k1, k2)

    def test_extra_components_change_key(self):
        k1 = qc.toolchain_key(rt_hash='a' * 64, flags=['-O2'], form='link',
                              extra={'objs': ['h1', 'h2']})[0]
        k2 = qc.toolchain_key(rt_hash='a' * 64, flags=['-O2'], form='link',
                              extra={'objs': ['h1', 'h3']})[0]
        self.assertNotEqual(k1, k2)


class CasPrimitiveTest(unittest.TestCase):
    """CAS 原语：原子写 / 硬链接交付 / 对象路径布局。"""

    def test_atomic_write_and_cas_layout(self):
        with tempfile.TemporaryDirectory() as d:
            os.environ['QOT_CACHE_DIR'] = d
            try:
                key = 'ab' + 'c' * 62
                data = os.path.join(d, 'payload.bin')
                with open(data, 'wb') as f:
                    f.write(b'hello')
                qc._cas_put(key, data, {'k': 'v'})
                obj = qc._cas_object_path(key)
                self.assertTrue(os.path.exists(obj))
                with open(obj, 'rb') as f:
                    self.assertEqual(f.read(), b'hello')
                # sidecar
                self.assertTrue(os.path.exists(obj + '.key.json'))
                # 幂等重放
                qc._cas_put(key, data, {'k': 'v'})
                with open(obj, 'rb') as f:
                    self.assertEqual(f.read(), b'hello')
                # deliver：硬链接（同卷）
                dst = os.path.join(d, 'out.bin')
                qc.deliver(obj, dst)
                with open(dst, 'rb') as f:
                    self.assertEqual(f.read(), b'hello')
                self.assertEqual(os.stat(obj).st_ino, os.stat(dst).st_ino)
            finally:
                os.environ.pop('QOT_CACHE_DIR', None)

    def test_mode_parsing(self):
        old = os.environ.get('QOT_CACHE')
        try:
            for v, want in (('', 'on'), ('on', 'on'), ('off', 'off'),
                            ('pch', 'pch'), ('PCH', 'pch')):
                os.environ['QOT_CACHE'] = v
                self.assertEqual(qc.mode(), want, 'QOT_CACHE=%r' % v)
            os.environ.pop('QOT_CACHE', None)
            self.assertEqual(qc.mode(), 'on')
            # K5：off → 全层关
            os.environ['QOT_CACHE'] = 'off'
            for layer in ('toolchain', 'object', 'link', 'frontend', 'pch'):
                self.assertFalse(qc.enabled_for(layer))
            # pch → 只开 PCH
            os.environ['QOT_CACHE'] = 'pch'
            self.assertTrue(qc.enabled_for('pch'))
            for layer in ('toolchain', 'object', 'link', 'frontend'):
                self.assertFalse(qc.enabled_for(layer))
        finally:
            if old is None:
                os.environ.pop('QOT_CACHE', None)
            else:
                os.environ['QOT_CACHE'] = old


class GcTest(unittest.TestCase):
    def test_gc_keeps_recent_pch(self):
        with tempfile.TemporaryDirectory() as d:
            os.environ['QOT_CACHE_DIR'] = d
            try:
                import time as _t
                pch = os.path.join(d, 'pch')
                for i, age in enumerate((100, 200, 300)):
                    p = os.path.join(pch, 'k%d' % i)
                    os.makedirs(p)
                    with open(os.path.join(p, 'x.gch'), 'wb') as f:
                        f.write(b'x')
                    past = _t.time() - age
                    os.utime(p, (past, past))
                rep = qc.gc_cache(keep_pch=2)
                self.assertEqual(rep['removed'], 1)
                self.assertEqual(sorted(os.listdir(pch)), ['k0', 'k1'])
            finally:
                os.environ.pop('QOT_CACHE_DIR', None)


if __name__ == '__main__':
    unittest.main()
