# -*- coding: utf-8 -*-
"""构建 qotgui（wingui 的 C ABI 产物）。

    python ot-asset/c/wingui/build_qotgui.py            # 产出 build/qotgui.dll
    python ot-asset/c/wingui/build_qotgui.py --static   # 只产出目标文件（供 AOT 链接）

产物的两个消费者：
  * 解释器（Oracle）：ctypes 装载 qotgui.dll；
  * 原生程序（AOT）：把同一份 C++ 源编进产物（--static 的目标文件）。
两者共用同一份实现，行为由代码决定、不靠对齐。
"""
import argparse
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
OUT_DIR = os.path.join(ROOT, 'build')

# wingui 是 header-only；只有这几个 .cpp 需要编译
SRCS = ['qotgui_abi.cpp', 'modern_window.cpp', 'renderer.cpp']
LIBS = ['-ld2d1', '-ldwrite', '-lole32', '-luuid', '-lcomctl32', '-limm32',
        '-lgdi32', '-luser32']


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--static', action='store_true',
                    help='只编译目标文件（供 AOT 链接），不产出 dll')
    ap.add_argument('--out', default=None, help='输出路径')
    args = ap.parse_args()

    os.makedirs(OUT_DIR, exist_ok=True)
    objs = []
    for s in SRCS:
        obj = os.path.join(OUT_DIR, s.replace('.cpp', '.o'))
        cmd = ['g++', '-std=c++17', '-O2', '-I', HERE,
               '-c', os.path.join(HERE, s), '-o', obj]
        print('[cc ]', ' '.join(cmd))
        r = subprocess.run(cmd)
        if r.returncode != 0:
            print('[FAIL] 编译失败:', s)
            return 1
        objs.append(obj)

    if args.static:
        print('[OK ] 目标文件已产出:', ', '.join(objs))
        return 0

    dll = args.out or os.path.join(OUT_DIR, 'qotgui.dll')
    cmd = ['g++', '-shared', '-o', dll] + objs + LIBS + ['-Wl,--out-implib',
                                                         os.path.join(OUT_DIR, 'libqotgui.a')]
    print('[ld ]', ' '.join(cmd))
    r = subprocess.run(cmd)
    if r.returncode != 0:
        print('[FAIL] 链接失败')
        return 1
    print('[OK ] 已产出', dll, '(%.1f KB)' % (os.path.getsize(dll) / 1024.0))
    return 0


if __name__ == '__main__':
    sys.exit(main())
