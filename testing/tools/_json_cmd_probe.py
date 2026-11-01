# -*- coding: utf-8 -*-
"""抓 AOT 编译的真实 g++ 命令行（定位 runtime 头解析路径）。"""
import os
import sys

ROOT = r'e:\QGuoat-jsq\QGuoat'
os.chdir(ROOT)
sys.path.insert(0, ROOT)
import qot_cache as qc

orig = qc.run_toolchain


def spy(cmd, cwd=None, timeout=300):
    print('CMD:', ' '.join(cmd[:12]))
    print('CWD:', cwd)
    r = orig(cmd, cwd=cwd, timeout=timeout)
    print('RC :', r.returncode)
    if r.returncode != 0:
        print((r.stderr or '')[:400])
    return r


qc.run_toolchain = spy
rt = os.path.join(ROOT, 'ot-asset', 'c', 'aot', 'qot_aot_runtime.h')
qc.compile_exe(os.path.join('testing', 'cases', 'native', '_json_probe.qot'),
               os.path.join('testing', '.artifacts', '_json2.exe'),
               rt_src=rt, netlibs=['-lws2_32'])
