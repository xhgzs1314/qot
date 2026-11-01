# -*- coding: utf-8 -*-
"""前端管线常数基准（编译 CI 线剩余项①的量尺 · ）。

48 模块合成工程（与 ci_incremental_leaf 同一生成器）上分 pass 计时前端全链：
inliner → parse → astopt → bytecode → lower → inline → promote → snapshot →
infer → peephole/unbox/coalesce(optimize_f64) → ssa_regalloc → arena → cgen。

口径：进程内直调（无缓存干扰，QOT_CACHE=off 语义），同进程重复 R 轮取
最小值（本机抖动大，S-16：判定看同会话前后对比）。用法：

    python testing/tools/frontend_const_bench.py [rounds]
"""
import os
import sys
import time
import tempfile

HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, 'testing', 'tools'))

ROUNDS = int(sys.argv[1]) if len(sys.argv) > 1 else 3


def _gen_modproj(n_modules, out_dir):
    # 与 ci_suite._gen_modproj 同构（复制以避免 import 级副作用）
    os.makedirs(out_dir, exist_ok=True)
    names = ['m%02d' % i for i in range(n_modules)]
    for i, nm in enumerate(names):
        nxt = names[i + 1] if i + 1 < len(names) else None
        lines = []
        if nxt:
            lines.append('require "%s.qot";' % nxt)
        lines.append('number_t func %s_work(x) {' % nm)
        lines.append('    var t = x;')
        for k in range(24):
            lines.append('    t = t + %d;' % (k + 1))
        lines.append('    return t;')
        lines.append('}')
        lines.append('number_t func %s_api(x) {' % nm)
        if nxt:
            lines.append('    return %s##%s_api(x + 1) + %s##%s_work(x);'
                         % (nxt, nxt, nxt, nxt))
        else:
            lines.append('    var t = x * 7;')
            for k in range(60):
                lines.append('    t = t + %d;' % ((k % 17) + 1))
            lines.append('    return t;')
        lines.append('}')
        with open(os.path.join(out_dir, nm + '.qot'), 'w',
                  encoding='utf-8', newline='\n') as f:
            f.write('\n'.join(lines) + '\n')
    main = ['// frontend_const_bench 主程序', 'require "%s.qot";' % names[0],
            'var pad_a = 7;', 'var pad_b = 9;',
            'output(%s##%s_api(1));' % (names[0], names[0]),
            'output(pad_a + pad_b);']
    with open(os.path.join(out_dir, 'main.qot'), 'w',
              encoding='utf-8', newline='\n') as f:
        f.write('\n'.join(main) + '\n')
    return os.path.join(out_dir, 'main.qot')


def run_once(main_qot):
    """一轮全链计时，返回 [(label, ms)]。直调管线各阶段（零缓存）。"""
    import io
    import tempfile as tf
    from grammar import parse_oning_gram
    from require_inliner import inline_requires

    rows = []

    def stage(label, fn):
        t0 = time.perf_counter()
        r = fn()
        rows.append((label, (time.perf_counter() - t0) * 1000.0))
        return r

    fd, tmp = tf.mkstemp(suffix='.qot', prefix='fcb_', text=True)
    os.close(fd)
    stage('S1 inliner', lambda: inline_requires(main_qot, tmp))
    compile_src = tmp
    ast = stage('S2 parse+sema', lambda: parse_oning_gram(compile_src, True))
    from astcomopt import optimize_ast
    fast = stage('S3 ast opt', lambda: optimize_ast(ast))
    from bytecode import Compiler
    c = Compiler()
    c.set_base_path(main_qot)
    code = stage('S4 bytecode', lambda: c.compile_ast(fast))
    code.source_file = main_qot
    from lir_lower import lower_program
    prog = stage('S5 lower', lambda: lower_program(code))
    from lir_opt import (inline_calls_v2, promote_globals, snapshot_user_funcs,
                         infer_f64, optimize_f64, arena_loops)
    import lir_ssa
    stage('S6a inline_v2', lambda: inline_calls_v2(prog))
    stage('S6b promote', lambda: promote_globals(prog))
    snaps = stage('S6c snapshot', lambda: snapshot_user_funcs(prog))
    stage('S6d infer_f64', lambda: infer_f64(prog))
    stage('S6e optimize_f64', lambda: optimize_f64(prog))
    stage('S6f ssa_regalloc', lambda: lir_ssa.ssa_regalloc(prog))
    stage('S6g arena', lambda: arena_loops(prog))
    from lir_cgen import generate
    stage('S7 cgen', lambda: generate(prog, main_qot, fast_snaps=snaps))
    if os.path.exists(compile_src):
        os.remove(compile_src)
    return rows


def main():
    os.environ['QOT_CACHE'] = 'off'   # 零缓存口径（冷链常数）
    with tempfile.TemporaryDirectory(prefix='fcb_') as td:
        main_qot = _gen_modproj(48, td)
        best = None
        for r in range(ROUNDS):
            rows = run_once(main_qot)
            if best is None:
                best = dict(rows)
            else:
                for k, v in rows:
                    best[k] = min(best[k], v)
        total = sum(best.values())
        print('%-18s %10s' % ('stage', 'ms (min of %d)' % ROUNDS))
        for k, v in best.items():
            print('%-18s %10.1f' % (k, v))
        print('%-18s %10.1f' % ('TOTAL', total))


if __name__ == '__main__':
    main()
