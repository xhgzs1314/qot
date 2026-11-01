# -*- coding: utf-8 -*-
"""M7 · FFI/插件 ABI 冻结的结构性守卫（）。

ABI 冻结最阴的失败形态 = 「公共头与宿主实现各改各的」—— 布局漂移在编译期
不一定炸（两个宿主各自自洽），在运行期就是跨宿主内存腐败。三道钉：

  1. 段一致性：runtime 头的 `8< BEGIN/END QOT_PLUGIN_ABI` 嵌入段与公共头
     ot-asset/c/plugin/qot_plugin_abi.h **逐字节一致**（改任一侧必须同步）；
  2. ctypes 镜像尺寸 = 公共头编译期探针常量（144）；
  3. 清单面：ffi_abi_version 双引擎登记为 v2（语义冻结的版本号不许静默回退）。
"""
import io
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

RT_HDR = os.path.join(ROOT, 'ot-asset', 'c', 'aot', 'qot_aot_runtime.h')
PUB_HDR = os.path.join(ROOT, 'ot-asset', 'c', 'plugin', 'qot_plugin_abi.h')

BEGIN = '// ---- 8< BEGIN QOT_PLUGIN_ABI'
END = '// ---- 8< END QOT_PLUGIN_ABI ----'


class TestPluginAbiEmbed(unittest.TestCase):
    """公共头 ↔ runtime 头嵌入段逐字节一致（SPEC R17 的机械形态）。"""

    def test_embed_segment_matches_public_header(self):
        with io.open(PUB_HDR, 'r', encoding='utf-8', newline='') as f:
            pub = f.read()
        with io.open(RT_HDR, 'r', encoding='utf-8', newline='') as f:
            rt = f.read()
        b = rt.index(BEGIN)
        b = rt.index('\n', b) + 1
        e = rt.index(END, b)
        seg = rt[b:e]
        # 比对口径 = 代码主体（自 include guard 起，含 extern "C" 包裹与全部
        # 声明）逐字节；公共头自己的文件级横幅注释属文档面，不进嵌入段
        # （runtime 侧的节注释由 BEGIN 标记行承担）。代码主体一字不许漂。
        key = '#ifndef QOT_PLUGIN_ABI_H'
        pub_body = pub[pub.index(key):]
        if seg.endswith('\n\n'):
            seg = seg[:-1]
        self.assertEqual(
            seg, pub_body,
            'runtime 头的 QOT_PLUGIN_ABI 嵌入段与公共头逐字节不一致 —— '
            '改公共头必须同步 runtime 头（或反之），SPEC R17')

    def test_version_marker_is_v2(self):
        with io.open(PUB_HDR, 'r', encoding='utf-8') as f:
            pub = f.read()
        self.assertIn('#define QOT_PLUGIN_ABI_VERSION 2u', pub)
        with io.open(RT_HDR, 'r', encoding='utf-8') as f:
            rt = f.read()
        self.assertIn('QOT_PLUGIN_ABI_VERSION 2u', rt)


class TestPluginAbiMirror(unittest.TestCase):
    """ctypes 镜像与公共头探针双钉（尺寸漂移先炸测试再炸编译）。"""

    def test_mirror_size_matches_probe(self):
        import ctypes
        from primitives import _plugin_types
        _, _, API = _plugin_types()
        self.assertEqual(ctypes.sizeof(API), 144)

    def test_manifest_pins_v2_on_both_engines(self):
        import qot_builtins as qb
        for e in qb.entries():
            if e['name'] == 'ffi_abi_version':
                self.assertEqual(e['native'], 'ffi_abi_version')
                self.assertIn('interp', e['engines'])
                self.assertIn('native', e['engines'])
                self.assertIn('v2', e['desc'],
                              'ffi_abi_version desc 须登记冻结版本 v2')
                return
        self.fail('builtins.toml 缺 ffi_abi_version 条目')


if __name__ == '__main__':
    unittest.main()
