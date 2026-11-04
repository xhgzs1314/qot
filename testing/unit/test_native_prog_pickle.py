# -*- coding: utf-8 -*-
"""QOTNative · prog CAS 的 K1 断言：pickle 往返后发射产物逐字节一致。

前端管线产出的 prog/snaps 携带大量非序列化字段（name_table/objmeta/
_src_op/_jt_cache/闭包标志……）——pickle 按对象图完整保真；本测试用真实
前端产物做往返，断言 generate_asm 逐字节一致（K1：缓存命中 = 冷编产物）。
"""
import os
import pickle
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

SRC = os.path.join(ROOT, 'testing', 'cases', 'native', 'expr_ternary.qot')


class TestNativeProgPickle(unittest.TestCase):
    """prog CAS 命中路径的 K1：往返后 asm 逐字节一致。"""

    def test_pickle_roundtrip_asm_identical(self):
        os.environ['QOT_AOT_BACKEND'] = 'native'
        import index
        import lir_native
        import qot_cache as qc

        captured = {}
        _orig = lir_native.generate_asm

        def _spy(prog, fast_snaps=None):
            captured['prog'] = prog
            captured['snaps'] = fast_snaps
            return _orig(prog, fast_snaps)

        lir_native.generate_asm = _spy
        try:
            os.environ['QOT_CACHE'] = 'off'
            index._aot_compile(SRC,
                               os.path.join(HERE, '.artifacts',
                                            'k1_probe.exe'), True)
        finally:
            lir_native.generate_asm = _orig
        self.assertIn('prog', captured)
        prog, snaps = captured['prog'], captured['snaps']

        asm_cold = _orig(prog, snaps)
        bundle = pickle.loads(pickle.dumps({'prog': prog, 'snaps': snaps},
                                           protocol=pickle.HIGHEST_PROTOCOL))
        asm_warm = lir_native.generate_asm(bundle['prog'], bundle['snaps'])
        self.assertEqual(asm_cold, asm_warm,
                         'prog CAS 往返后发射产物不一致 —— pickle 丢字段'
                         '（K1：命中必须等于冷编）')


if __name__ == '__main__':
    unittest.main()
