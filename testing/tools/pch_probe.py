# -*- coding: utf-8 -*-
"""编译 CI 线 · 核心可行性探针：预编译头（PCH）对"外部工具链阶段"的收益。

事实前提：单次 AOT 编译 ~8.4s，其中 g++ 编译+链接 ~7.6s（占 91%）；
g++ 处理的是「单向夹率式单 TU」（自带 runtime 头 305KB + 生成的 C 源）。
runtime 头是**编译器自带资产**（内容随编译器版本而非用户源码变化）——
天然适合 PCH：每编译器版本编一次，此后每次编译直接载入已解析 AST。
"""
import os
import subprocess
import sys
import time
import io

HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BUILD = os.path.join(HERE, 'testing', '.artifacts')
RT = os.path.join(HERE, 'qot_aot_runtime.h')
CPP = os.path.join(BUILD, '_prof_gen.cpp')
EXE = os.path.join(BUILD, '_pch_out.exe')


def timed(label, cmd):
    t0 = time.perf_counter()
    p = subprocess.run(cmd, capture_output=True, shell=True)
    dt = (time.perf_counter() - t0) * 1000.0
    ok = (p.returncode == 0)
    print('%-34s %9.1f ms  %s' % (label, dt, 'OK' if ok else 'FAIL: %s' % (
        (p.stderr or b'').decode('gbk', 'replace')[:200])))
    return dt, ok


def main():
    if not os.path.exists(CPP):
        print('缺少 %s —— 先跑 _profile_pipeline.py 生成产物' % CPP)
        return
    rt_dst = os.path.join(BUILD, 'qot_aot_runtime.h')
    io.open(rt_dst, 'w', encoding='utf-8', newline='\n').write(
        io.open(RT, encoding='utf-8').read())

    print('runtime 头: %d 字节' % os.path.getsize(rt_dst))

    # 0) 基线：当前实践（无 PCH）
    base = ['g++', '-std=c++17', '-O2', '-o', EXE, CPP, '-static', '-lws2_32']
    t_cold, _ = timed('① 冷编译（现状）', base)

    # 1) 生成 PCH（每个编译器版本一次）
    gch = rt_dst + '.gch'
    t_pchgen, ok = timed('② 生成 PCH（一次性）',
                         ['g++', '-std=c++17', '-O2', '-x', 'c++-header',
                          '-o', gch, rt_dst])
    if not ok or not os.path.exists(gch):
        print('PCH 生成失败')
        return
    print('   PCH 大小: %d 字节' % os.path.getsize(gch))

    # 2) PCH 命中编译（-Winvalid-pch 会让"未命中"显性报错，而不是静默变慢）
    t_pch, ok2 = timed('③ PCH 命中编译',
                       ['g++', '-std=c++17', '-O2', '-Winvalid-pch',
                        '-o', EXE, CPP, '-static', '-lws2_32'])
    if not ok2:
        print('   PCH 未命中（flags 不一致会错，不是静默变慢 —— 这是安全性质）')

    print()
    print('=== 收益 ===')
    if t_cold > 0:
        print('单次编译：%.1f ms → %.1f ms（省 %.1f%%，绝对省 %.1f ms）' % (
            t_cold, t_pch, (1 - t_pch / t_cold) * 100, t_cold - t_pch))
    print('摊销：PCH 生成 %.1f ms，只需在编译器版本变化时重做；' % t_pchgen)
    print('     本会话约 ~150 次编译 → 省 %.0f 秒 ≈ %.1f 分钟' % (
        max(0.0, (t_cold - t_pch)) * 150 / 1000,
        max(0.0, (t_cold - t_pch)) * 150 / 1000 / 60))


if __name__ == '__main__':
    main()
