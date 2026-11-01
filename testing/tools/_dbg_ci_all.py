# -*- coding: utf-8 -*-
"""QOT-CI · 单跑指定 ci 用例并打印明细（交付记录取数用）。"""
import os
import sys

ROOT = r'e:\QGuoat-jsq\QGuoat'
sys.path.insert(0, ROOT)
os.chdir(ROOT)
sys.path.insert(0, os.path.join(ROOT, 'testing', 'tools'))
import ci_suite as CS

names = sys.argv[1:] or ['ci_incremental_leaf']
fns = {n: getattr(CS, n) for n in names}
for n, fn in fns.items():
    r = fn()
    print('[%s] %s' % (r.status, n))
    print('  ' + r.detail.replace('\n', '\n  '))
