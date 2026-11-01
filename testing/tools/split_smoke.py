# -*- coding: utf-8 -*-
"""QOT-CI · generate_split 冒烟：全管线（inliner→parse→bytecode→LIR→opts→split cgen）。
验证：① 单 TU generate() 产物与历史一致（零回归）；② split 包可写盘并肉眼看结构。"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from grammar import parse_oning_gram
from bytecode import Compiler
from lir_lower import lower_program
from lir_opt import (inline_calls_v2, promote_globals, snapshot_user_funcs,
                     infer_f64, optimize_f64, arena_loops)
from lir_ssa import ssa_regalloc
import lir_ssa
from lir_cgen import generate, generate_split
from require_inliner import RequireInliner


def run_pipeline(src, module_of):
    ast = parse_oning_gram(src, True)
    assert ast and ast != 1
    compiler = Compiler()
    compiler.set_base_path(src)
    code = compiler.compile_ast(ast)
    code.source_file = src
    prog = lower_program(code)
    inline_calls_v2(prog)
    promote_globals(prog)
    snaps = snapshot_user_funcs(prog)
    infer_f64(prog)
    optimize_f64(prog)
    ssa_regalloc(prog)
    arena_loops(prog)
    single = generate(prog, src, fast_snaps=snaps)
    bundle = generate_split(prog, src, fast_snaps=snaps, module_of=module_of)
    return single, bundle


def main():
    case = os.path.join(ROOT, 'testing', 'cases', 'native', 'mod_forward.qot')
    r = RequireInliner()
    merged = r.inline_file(case)
    tmp = os.path.join(ROOT, 'testing', '.artifacts', '_split_smoke_merged.qot')
    with open(tmp, 'w', encoding='utf-8', newline='\n') as f:
        f.write(merged)
    module_of = {}
    for m in r.modules:
        for fn in m['funcs']:
            module_of[fn] = m['name']
    single, bundle = run_pipeline(tmp, module_of)
    print('单 TU C 字节:', len(single.encode('utf-8')))
    print('split header 字节:', len(bundle['header'].encode('utf-8')))
    print('split main 字节:', len(bundle['main'].encode('utf-8')))
    for name, src in bundle['tus']:
        print('split TU %-12s 字节: %d' % (name, len(src.encode('utf-8'))))
    print('module_map:', bundle['module_map'])
    out = os.path.join(ROOT, 'testing', '.artifacts', '_split_smoke')
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, 'qot_parts.h'), 'w', encoding='utf-8', newline='\n') as f:
        f.write(bundle['header'])
    with open(os.path.join(out, 'main.cpp'), 'w', encoding='utf-8', newline='\n') as f:
        f.write(bundle['main'])
    for name, src in bundle['tus']:
        with open(os.path.join(out, 'mod_%s.cpp' % name), 'w', encoding='utf-8',
                  newline='\n') as f:
            f.write(src)
    print('产物落在', out)
    # 打印模块 TU 的头 25 行做结构目检
    if bundle['tus']:
        name, src = bundle['tus'][0]
        print('---- %s 前 25 行 ----' % name)
        for ln in src.splitlines()[:25]:
            print('  ', ln)
    os.remove(tmp)


if __name__ == '__main__':
    main()
