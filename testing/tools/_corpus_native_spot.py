# -*- coding: utf-8 -*-
"""QOT 语料线 · native 抽查：同一样本 interp vs native 输出逐行对拍。"""
import os
import subprocess
import sys

ROOT = r'e:\QGuoat-jsq\QGuoat'
os.chdir(ROOT)
ART = os.path.join(ROOT, 'testing', '.artifacts', '_corpus_native')
os.makedirs(ART, exist_ok=True)

PICK = ['money_format', 'text_stats', 'maze_bfs', 'fib_primes',
        'quicksort_search', 'csv_to_json', 'shopping_cart', 'lru_cache']

for name in PICK:
    qot = os.path.join(ROOT, 'testing', 'corpus', name + '.qot')
    exe = os.path.join(ART, name + '.exe')
    if os.path.exists(exe):
        os.remove(exe)
    r = subprocess.run([sys.executable, 'index.py', 'aot', qot, exe],
                       capture_output=True, timeout=300,
                       encoding='utf-8', errors='replace')
    if not os.path.exists(exe):
        print('%-20s AOT-FAIL %s' % (name, (r.stderr or '')[-120:]))
        continue
    p = subprocess.run([exe], capture_output=True, timeout=120,
                       encoding='utf-8', errors='replace')
    i = subprocess.run([sys.executable, 'index.py', 'run', qot],
                       capture_output=True, timeout=120,
                       encoding='utf-8', errors='replace')
    same = (p.stdout == i.stdout)
    print('%-20s %s  nat=%d lines / int=%d lines' %
          (name, 'SAME ' if same else 'DIFF ', len(p.stdout.splitlines()),
           len(i.stdout.splitlines())))
    if not same:
        pl = p.stdout.splitlines()
        il = i.stdout.splitlines()
        for k in range(max(len(pl), len(il))):
            a = pl[k] if k < len(pl) else '<none>'
            b = il[k] if k < len(il) else '<none>'
            if a != b:
                print('    line %d: nat=%r int=%r' % (k + 1, a[:60], b[:60]))
    os.remove(exe)
