# -*- coding: utf-8 -*-
"""编译管线分段画像（编译 CI 线立项的事实依据）。

不动业务代码：按 index.py 的 _compile_to_native 主流程复刻各阶段，逐段计时。
阶段：inliner → parse(+sema) → ast opt → bytecode → lower → passes → cgen → 工具链
"""
import os
import sys
import subprocess
import time
import tempfile
import hashlib

HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, HERE)

import io


def _stage(label, fn):
    t0 = time.perf_counter()
    r = fn()
    dt = (time.perf_counter() - t0) * 1000.0
    return r, label, dt


def profile(src, out_exe, enable_opt=True):
    from grammar import parse_oning_gram
    from require_inliner import inline_requires
    from lir_cgen import generate

    rows = []
    compile_src = src

    def _has_require(f):
        return 'require ' in io.open(f, encoding='utf-8').read()

    if _has_require(src):
        def _do():
            fd, tmp = tempfile.mkstemp(suffix='.qot', prefix='prof_', text=True)
            os.close(fd)
            inline_requires(src, tmp)
            return tmp
        tmp, label, dt = _stage('S1 inliner', _do)
        rows.append((label, dt))
        compile_src = tmp
    else:
        rows.append(('S1 inliner(无)', 0.0))

    ast, label, dt = _stage('S2 parse+sema', lambda: parse_oning_gram(compile_src, True))
    rows.append((label, dt))

    fast = ast
    if enable_opt:
        from astcomopt import optimize_ast
        fast, label, dt = _stage('S3 ast opt', lambda: optimize_ast(ast))
        rows.append((label, dt))
    else:
        rows.append(('S3 ast opt(关)', 0.0))

    from bytecode import Compiler
    c = Compiler()
    c.set_base_path(src)
    code, label, dt = _stage('S4 bytecode', lambda: c.compile_ast(fast))
    rows.append((label, dt))
    code.source_file = src

    from lir_lower import lower_program
    prog, label, dt = _stage('S5 lower', lambda: lower_program(code))
    rows.append((label, dt))

    def _passes():
        # 与 index.py:1844-1864 主流程逐条对齐（含 N-04 快照/快体口径）
        from lir_opt import (inline_calls_v2, promote_globals, snapshot_user_funcs,
                             infer_f64, optimize_f64, arena_loops)
        inline_calls_v2(prog)
        promote_globals(prog)
        _snaps = snapshot_user_funcs(prog)
        infer_f64(prog)
        optimize_f64(prog)
        from lir_ssa import ssa_regalloc
        ssa_regalloc(prog)
        arena_loops(prog)
        return None

    _, label, dt = _stage('S6 passes(全)', _passes)
    rows.append((label, dt))

    csrc, label, dt = _stage('S7 cgen', lambda: generate(prog, src))
    rows.append((label, dt))
    rows.append(('   └ C 源码字节', len(csrc.encode('utf-8'))))

    build_dir = os.path.dirname(os.path.abspath(out_exe)) or '.'
    c_path = os.path.join(build_dir, '_prof_gen.cpp')
    io.open(c_path, 'w', encoding='utf-8', newline='\n').write(csrc)
    rt = os.path.join(HERE, 'ot-asset', 'c', 'aot', 'qot_aot_runtime.h')
    rt_dst = os.path.join(build_dir, 'qot_aot_runtime.h')
    io.open(rt_dst, 'w', encoding='utf-8', newline='\n').write(
        io.open(rt, encoding='utf-8').read())
    rt_bytes = os.path.getsize(rt)

    cmd = ['g++', '-std=c++17', '-O2', '-o', out_exe, c_path, '-static', '-lws2_32']
    _, label, dt = _stage('S8 g++ 编译+链接',
                          lambda: subprocess.run(cmd, capture_output=True))
    rows.append((label, dt))

    if compile_src != src and os.path.exists(compile_src):
        os.remove(compile_src)
    return rows, rt_bytes


def main():
    targets = [
        ('std_net', os.path.join(HERE, 'testing/cases/native/std_net.qot')),
        ('mod_forward', os.path.join(HERE, 'testing/cases/native/mod_forward.qot')),
        ('demo_csv_report', os.path.join(HERE, 'examples/demo_csv_report.qot')),
    ]
    print('%-18s %10s' % ('阶段', 'ms'))
    summary = {}
    for name, src in targets:
        if not os.path.exists(src):
            print('skip missing', src)
            continue
        rows, rt_bytes = profile(src, os.path.join(HERE, 'testing/.artifacts/_prof_%s.exe' % name))
        print('---- %s (runtime 头 %d 字节，每次编译必全量重读+全量重解析) ----' % (name, rt_bytes))
        total = 0.0
        for label, dt in rows:
            if isinstance(dt, int):
                print('%-18s %10s' % (label, '{:,}'.format(dt)))
                continue
            total += dt
            print('%-18s %10.1f' % (label, dt))
        print('%-18s %10.1f' % ('合计', total))
        print('   其中：python 前端+中端 = %.1f ms，外部工具链 = %.1f ms' % (
            total - dict(rows).get('S8 g++ 编译+链接', 0.0),
            dict(rows).get('S8 g++ 编译+链接', 0.0)))
        summary[name] = (total, dict(rows).get('S8 g++ 编译+链接', 0.0))
    print()
    print('=== 结论数据 ===')
    for k, (t, c) in summary.items():
        print('%-18s 总 %7.1f ms | 工具链占比 %5.1f%%' % (k, t, c / t * 100 if t else 0))


if __name__ == '__main__':
    main()
