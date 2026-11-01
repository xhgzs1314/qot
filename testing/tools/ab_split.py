# -*- coding: utf-8 -*-
"""QOT-CI · A/B 门禁：单 TU vs 切分 TU 的运行性能对比（CP-C2 验收②）。

方法：同一负载编两份（--split-tu 与默认单 TU），交错各跑 N 次取中位数比
值。判据（§9.4-L1 ⚠️ 条款）：跨 TU 内联损失必须量化；>3% 退化时单 TU 仍
是默认形态（混合模式为后续项），数字进交付记录。

用法: python testing/tools/ab_split.py [--cases b1a_strcat_long,...] [--repeat 5]
"""
from __future__ import annotations

import argparse
import os
import re
import statistics
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ROOT = HERE
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'testing'))

PY = sys.executable
ART = os.path.join(ROOT, 'testing', '.artifacts', 'ab_split')

# 与 bench_native 相同的哨兵口径
ITER_SENTINEL = 87654321
SENTINEL_RE = r'(?<![0-9.])' + str(ITER_SENTINEL) + r'(?![0-9.])'

# A/B 面板：合成多模块负载（跨模块热调用 = 跨 TU 内联损失的真实形态）
# + 单文件对照（无模块边界，应 ≈1.0）
SYNTH = {}


def _synth_modwork(n):
    """main + hotlib：热循环里跨模块调用 >120 LIR 指令的数值函数（LIR 拒绝
    内联、GCC 在单 TU 里可能内联的形态 —— 跨 TU 内联损失的最坏情况）。"""
    d = os.path.join(ART, '_synth_modwork')
    os.makedirs(d, exist_ok=True)
    body = []
    for k in range(45):
        body.append('    t = t + %d; t = t * 1;' % ((k % 13) + 1))
    with open(os.path.join(d, 'hotlib.qot'), 'w', encoding='utf-8',
              newline='\n') as f:
        f.write('number_t func work(x) {\n    var t = x;\n'
                + '\n'.join(body)
                + '\n    return t;\n}\n')
    with open(os.path.join(d, 'main.qot'), 'w', encoding='utf-8',
              newline='\n') as f:
        f.write('require "hotlib.qot";\n'
                'var acc = 0;\nvar i = 0;\nloop (i < %d) {\n'
                '    acc = acc + hotlib##work(i);\n    i = i + 1;\n}\n'
                'output(acc);\n' % n)
    return os.path.join(d, 'main.qot')


def _synth_chain(n):
    """main → a → b → c 三模块链，每层中量互调。"""
    d = os.path.join(ART, '_synth_chain')
    os.makedirs(d, exist_ok=True)

    def wr(name, req, calls):
        with open(os.path.join(d, name + '.qot'), 'w', encoding='utf-8',
                  newline='\n') as f:
            f.write(('require "%s.qot";\n' % req) if req else '')
            f.write('number_t func %s_work(x) {\n    var t = x;\n' % name)
            for k in range(30):
                f.write('    t = t + %d;\n' % ((k % 11) + 1))
            if calls:
                f.write('    t = t + %s##%s_work(t);\n' % (calls, calls))
            f.write('    return t;\n}\n')

    wr('c', None, None)
    wr('b', 'c', 'c')
    wr('a', 'b', 'b')
    with open(os.path.join(d, 'main.qot'), 'w', encoding='utf-8',
              newline='\n') as f:
        f.write('require "a.qot";\nvar acc = 0;\nvar i = 0;\n'
                'loop (i < %d) {\n    acc = acc + a##a_work(i);\n    i = i + 1;\n}\n'
                'output(acc);\n' % n)
    return os.path.join(d, 'main.qot')


CASES = [
    # (key, N 主档, units 函数, 合成器)
    ('abmod_hot', 20000000, lambda n: n, _synth_modwork),
    ('abmod_chain', 5000000, lambda n: n, _synth_chain),
    ('b1a_strcat_long', 2000000, lambda n: n, None),
    ('b2a_dict_const_key', 5000000, lambda n: n, None),
    ('empty_loop', 100000000, lambda n: n, None),
]


def _load(key, n, split):
    """把哨兵替换为 N 写入临时负载。"""
    src = os.path.join(ROOT, 'testing', 'bench', key + '.qot')
    if not os.path.exists(src):
        return None
    with open(src, 'r', encoding='utf-8') as f:
        text = f.read()
    new, cnt = re.subn(SENTINEL_RE, str(n), text)
    if cnt != 1:
        return None
    out = os.path.join(ART, '%s_%s_n%d.qot' % (key, 'split' if split else 'single', n))
    os.makedirs(ART, exist_ok=True)
    with open(out, 'w', encoding='utf-8', newline='\n') as f:
        f.write(new)
    return out


def _aot(qot, exe, split):
    cmd = [PY, 'index.py', 'aot', qot, exe]
    if split:
        cmd.append('--split-tu')
    r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                       encoding='utf-8', errors='replace', timeout=600)
    return os.path.exists(exe), (r.stderr or '')


def _timeit(exe, repeat):
    ts = []
    for _ in range(repeat):
        t0 = time.perf_counter()
        subprocess.run([exe], capture_output=True, timeout=300)
        ts.append(time.perf_counter() - t0)
    return statistics.median(ts)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--repeat', type=int, default=5)
    ap.add_argument('--cases', default='')
    args = ap.parse_args()
    only = set(x for x in args.cases.split(',') if x)

    rows = []
    for key, n, units, synth in CASES:
        if only and key not in only:
            continue
        if synth is not None:
            q = synth(n)
            q1 = q2 = q
        else:
            q1 = _load(key, n, False)
            q2 = _load(key, n, True)
        if not (q1 and q2):
            print('[SKIP] %s（哨兵/负载缺失）' % key)
            continue
        e1 = os.path.join(ART, key + '_single.exe')
        e2 = os.path.join(ART, key + '_split.exe')
        ok1, err1 = _aot(q1, e1, False)
        ok2, err2 = _aot(q2, e2, True)
        if not (ok1 and ok2):
            print('[FAIL] %s 编译失败 single=%s split=%s %s' % (key, ok1, ok2, err2[-200:]))
            continue
        # 交错计时（同会话消调度抖动）
        ts1, ts2 = [], []
        for _ in range(args.repeat):
            for ts, e in ((ts1, e1), (ts2, e2)):
                t0 = time.perf_counter()
                subprocess.run([e], capture_output=True, timeout=300)
                ts.append(time.perf_counter() - t0)
        m1, m2 = statistics.median(ts1), statistics.median(ts2)
        ratio = m2 / m1 if m1 else 0.0
        u = units(n)
        rows.append((key, n, u, m1, m2, ratio))
        print('%-20s N=%-9d 单TU %8.3fs | 切分 %8.3fs | 比值 %.3fx'
              % (key, n, m1, m2, ratio))
        for junk in (q1, q2, e1, e2):
            if os.path.exists(junk):
                os.remove(junk)

    if not rows:
        print('无可测负载')
        return 2
    print()
    print('=== A/B 汇总（<1.0 = 切分更快；>1.0 = 跨 TU 内联损失） ===')
    worst = max(r[5] for r in rows)
    avg = statistics.mean(r[5] for r in rows)
    for key, n, u, m1, m2, ratio in rows:
        flag = '  <-退化' if ratio > 1.03 else ''
        print('  %-20s %.3fx%s' % (key, ratio, flag))
    print('均值 %.3fx | 最差 %.3fx' % (avg, worst))
    if worst > 1.03:
        print('判据：存在 >3% 的退化负载 —— 单 TU 保持默认形态（混合模式为后续项）；')
        print('      数字已记录，作为 CP-C2 验收②的量化依据。')
        return 0
    print('判据：全部负载退化 ≤3% —— 切分形态的性能代价可接受。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
