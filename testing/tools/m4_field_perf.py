# -*- coding: utf-8 -*-
"""M4 字段访问微基准（）：热循环内 p->x + p->y 的每字段读开销。

口径：与 bench_native 的两点差分同思想 —— 用小 N 交错计时（机器抖动大，
绝对值仅供量级判断）。判据：FIELD_GET 的内联快路径（类型表偏移 + 直接
下标，无调用帧）应显著低于一次函数调用（~40ns 量级），量级应在个位 ns。
"""
import subprocess
import sys
import time
import os

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PY = sys.executable


def build_n(n):
    src = open(os.path.join(ROOT, '_m4_perf.qot'), encoding='utf-8').read()
    src = src.replace('87654321', str(n))
    p = os.path.join(ROOT, '_m4_perf_n.qot')
    open(p, 'w', encoding='utf-8').write(src)
    exe = os.path.join(ROOT, '_m4_perf_n.exe')
    r = subprocess.run([PY, 'index.py', 'aot', p, exe], cwd=ROOT,
                       capture_output=True, text=True)
    if not os.path.exists(exe):
        print(r.stderr[-500:])
        raise SystemExit('AOT 失败')
    return exe


def timeit(exe, rounds=5):
    best = None
    for _ in range(rounds):
        t0 = time.perf_counter()
        subprocess.run([exe], cwd=ROOT, capture_output=True)
        dt = time.perf_counter() - t0
        if best is None or dt < best:
            best = dt
    return best


def main():
    # 逐步求差：N 与 N/2 的墙钟差 ≈ N/2 次迭代的边际成本；
    # 每迭代 = 2 次字段读 + 1 次加法 + 循环框架。
    n1, n2 = 30_000_000, 15_000_000
    e1, e2 = build_n(n1), build_n(n2)
    t1, t2 = timeit(e1), timeit(e2)
    per_iter = (t1 - t2) / (n1 - n2) * 1e9
    # 每迭代 2 次字段读；扣除循环+加法框架（empty_loop 同口径 ~2ns）
    frame_ns = 2.0
    per_field = (per_iter - frame_ns) / 2
    print('每迭代 %.2f ns（含 2 次字段读 + 框架 %.1f ns）' % (per_iter, frame_ns))
    print('每字段读 ≈ %.2f ns（判据：个位数 ns；红线 ≤3ns 的量级面）' % per_field)


if __name__ == '__main__':
    main()
