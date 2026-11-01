# -*- coding: utf-8 -*-
"""C++ 级单元测试运行器（testing/cpp/*.cpp）。

用 g++（MinGW-w64，M-PACK 切换）把每个 test_*.cpp 编成独立 exe 并执行。
无 g++ 时全部 SKIP，不阻塞套件。

由 testing/run.py --suite cpp 调度；也可手工运行。
"""
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TESTING = os.path.dirname(HERE)
ROOT = os.path.dirname(TESTING)
ART = os.path.join(TESTING, '.artifacts', 'cpp')
VSWHERE = os.path.join(os.environ.get('ProgramFiles(x86)', r'C:\Program Files (x86)'),
                       'Microsoft Visual Studio', 'Installer', 'vswhere.exe')

DEFAULT_TIMEOUT = 120


def find_gxx():
    """M-PACK（）：编译器探测从 MSVC 切到 MinGW-w64 g++。
    返回 g++ 绝对路径或 None。"""
    return shutil.which('g++') or shutil.which('gcc')


def find_cl():
    """【兼容保留】返回 (vcvarsall, arch) 或 None。旧 MSVC 探测，仅作回退。"""
    if not sys.platform.startswith('win'):
        return None
    vs = None
    if os.path.exists(VSWHERE):
        try:
            r = subprocess.run([VSWHERE, '-latest', '-products', '*',
                                '-requires', 'Microsoft.VisualStudio.Component.VC.Tools.x86.x64',
                                '-property', 'installationPath'],
                               capture_output=True, text=True, timeout=30)
            if r.stdout.strip():
                vs = r.stdout.strip().splitlines()[0]
        except Exception:
            pass
    if not vs:
        for c in (r'C:\Program Files\Microsoft Visual Studio\2022',
                  r'C:\Program Files\Microsoft Visual Studio\2019'):
            if os.path.isdir(c):
                subs = sorted(os.listdir(c), reverse=True)
                if subs:
                    vs = os.path.join(c, subs[0])
                    break
    if not vs:
        return None
    vcvars = os.path.join(vs, 'VC', 'Auxiliary', 'Build', 'vcvarsall.bat')
    return vcvars if os.path.exists(vcvars) else None


def run_suite():
    """返回 [(name, ok, detail)]；无编译器时全部 SKIP（ok=True, detail 注明）。"""
    os.makedirs(ART, exist_ok=True)
    tests = sorted(f for f in os.listdir(HERE) if f.startswith('test_') and f.endswith('.cpp'))
    out = []
    if not tests:
        return out
    gxx = find_gxx()
    if not gxx:
        for t in tests:
            out.append((t[:-4], True, 'SKIP：未找到 g++，无法编译 C++ 测试'))
        return out
    bat_path = os.path.join(ART, '_build_test.bat')
    for src in tests:
        name = src[:-4]
        exe = os.path.join(ART, name + '.exe')
        with open(bat_path, 'w', encoding='ascii') as f:
            f.write('@echo off\r\n')
            f.write('"%s" -std=c++17 -O2 -static -static-libgcc -static-libstdc++ '
                    '-finput-charset=utf-8 "%s" -o "%s"\r\n'
                    % (gxx, os.path.join(HERE, src), exe))
        rb = subprocess.run(['cmd', '/c', os.path.abspath(bat_path)], cwd=ROOT,
                            capture_output=True, text=True, errors='replace',
                            timeout=DEFAULT_TIMEOUT)
        if rb.returncode != 0 or not os.path.exists(exe):
            out.append((name, False, '编译失败\n%s' % (rb.stderr or rb.stdout or '')[-500:]))
            continue
        rr = subprocess.run([exe], cwd=ROOT, capture_output=True, text=True,
                            errors='replace', timeout=DEFAULT_TIMEOUT)
        if rr.returncode != 0:
            out.append((name, False, (rr.stdout or '') + (rr.stderr or '')))
        else:
            out.append((name, True, ''))
    return out


def main() -> int:
    failed = 0
    for name, ok, detail in run_suite():
        if ok and not detail.startswith('SKIP'):
            print('[ OK ] %s' % name)
        elif ok:
            print('[SKIP] %s  %s' % (name, detail))
        else:
            print('[FAIL] %s\n%s' % (name, detail))
            failed += 1
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
