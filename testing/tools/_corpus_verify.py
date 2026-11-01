# -*- coding: utf-8 -*-
"""QOT 语料线 · 批量验证器：interp 跑 testing/corpus/*.qot，汇总结果。"""
import glob
import os
import subprocess
import sys

ROOT = r'e:\QGuoat-jsq\QGuoat'
os.chdir(ROOT)

fails = []
for qot in sorted(glob.glob(os.path.join(ROOT, 'testing', 'corpus', '*.qot'))):
    name = os.path.basename(qot)
    p = subprocess.run([sys.executable, 'index.py', 'run', qot],
                       capture_output=True, timeout=120,
                       encoding='utf-8', errors='replace')
    status = 'OK ' if p.returncode == 0 else 'FAIL'
    first = (p.stdout or '').strip().splitlines()
    tail = (p.stderr or '').strip().splitlines()
    print('%s %-28s rc=%-3d out=%-2d %s' %
          (status, name, p.returncode, len(first),
           (tail[-1][:110] if tail else (first[0][:60] if first else ''))))
    if p.returncode != 0:
        fails.append(name)
print()
print('total=%d fail=%d %s' % (len(glob.glob(os.path.join(
    ROOT, 'testing', 'corpus', '*.qot'))), len(fails), fails))
