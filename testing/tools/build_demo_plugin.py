# -*- coding: utf-8 -*-
"""M7 插件 ABI 测试基建：构建 demo 插件动态库（幂等，源未变则跳过）。

用法（用例 json 的 "prerun" 同步钩子）：
    python testing/tools/build_demo_plugin.py

产物 = testing/.artifacts/qot_demo_plugin.dll（Windows）/ .so（POSIX）。
源 = testing/tools/qot_demo_plugin.c，唯一依赖 = ot-asset/c/plugin/qot_plugin_abi.h
（源或头比产物新 → 重编）。失败以非零退出 + stderr 输出（prerun 判 FAIL）。
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
SRC = os.path.join(HERE, 'qot_demo_plugin.c')
INC = os.path.join(ROOT, 'ot-asset', 'c', 'plugin')
OUT_DIR = os.path.join(ROOT, 'testing', '.artifacts')
NAME = 'qot_demo_plugin.dll' if sys.platform == 'win32' else 'qot_demo_plugin.so'
OUT = os.path.join(OUT_DIR, NAME)


def main():
    import shutil
    cc = shutil.which('g++') or shutil.which('gcc') or shutil.which('cc')
    if not cc:
        print('[FAIL] 未找到 C 编译器（g++/gcc）', file=sys.stderr)
        return 2
    os.makedirs(OUT_DIR, exist_ok=True)
    deps = [SRC, os.path.join(INC, 'qot_plugin_abi.h')]
    if os.path.exists(OUT) and all(
            os.path.getmtime(d) <= os.path.getmtime(OUT) for d in deps):
        print('[SKIP] %s 已是最新' % OUT)
        return 0
    cmd = [cc, '-std=c++17', '-O1', '-shared', '-I' + INC, SRC, '-o', OUT,
           '-static-libgcc', '-static-libstdc++']
    p = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                       encoding='utf-8', errors='replace')
    if p.returncode != 0 or not os.path.exists(OUT):
        sys.stderr.write((p.stderr or p.stdout or '')[-1200:])
        print('[FAIL] 插件构建失败', file=sys.stderr)
        return 1
    print('[OK] %s' % OUT)
    return 0


if __name__ == '__main__':
    sys.exit(main())
