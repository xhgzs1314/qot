# -*- coding: utf-8 -*-
"""编译 CI 线 · 前置探针：确定性与启动开销（K3 / L3 的事实依据）。

跑法: python _det_probe.py
产出:
  1) 同一 C 源连编 N 次，两两 sha256 比较 —— g++ 产物是否字节级确定；
  2) g++ -E 只做预处理的时间（模拟"每次全量重读 305KB 头"的下界）；
  3) `python index.py aot` 全链路（含解释器启动+导入）与纯工具链的分摊。
"""
import hashlib
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RT = os.path.join(HERE, 'qot_aot_runtime.h')


def sha256_file(p):
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def main():
    cpp = os.path.join(HERE, '_det_probe_src.cpp')
    with open(cpp, 'w', encoding='utf-8', newline='\n') as f:
        f.write('#include <cstdio>\nint main(){ printf("hi\\n"); return 0; }\n')

    print('== ① g++ 字节级确定性（同源连编 3 次） ==')
    hashes = []
    for i in range(3):
        exe = os.path.join(HERE, '_det_probe_%d.exe' % i)
        t0 = time.perf_counter()
        r = subprocess.run(['g++', '-std=c++17', '-O2', '-static',
                            '-static-libgcc', '-static-libstdc++', '-s',
                            cpp, '-o', exe], capture_output=True)
        dt = time.perf_counter() - t0
        assert r.returncode == 0, r.stderr.decode('utf-8', 'replace')
        h = sha256_file(exe)
        hashes.append(h)
        print('  #%d  %.1f ms  %s' % (i, dt * 1000, h[:16]))
        os.remove(exe)
    print('  => %s' % ('字节级确定（K3 通过）' if len(set(hashes)) == 1 else '不一致（需要 --no-insert-timestamp 等标准化）'))

    print('== ② -fno-pie / 链接器确定性复核 ==')
    exe_a = os.path.join(HERE, '_det_pa.exe')
    exe_b = os.path.join(HERE, '_det_pb.exe')
    for exe, extra in ((exe_a, []), (exe_b, ['-Wl,--no-insert-timestamp'])):
        r = subprocess.run(['g++', '-std=c++17', '-O2', '-static',
                            '-static-libgcc', '-static-libstdc++', '-s'] + extra
                           + [cpp, '-o', exe], capture_output=True)
        assert r.returncode == 0, (extra, r.stderr.decode('utf-8', 'replace'))
    ha, hb = sha256_file(exe_a), sha256_file(exe_b)
    print('  plain=%s  no-ts=%s  %s' % (ha[:16], hb[:16],
                                        '相等' if ha == hb else '不等（时间戳进了产物）'))
    os.remove(exe_a)
    os.remove(exe_b)

    print('== ③ python -c pass / import index 启动开销 ==')
    for label, code in (('python -c pass', 'pass'),
                        ('import index', 'import index')):
        ts = []
        for _ in range(3):
            t0 = time.perf_counter()
            subprocess.run([sys.executable, '-c', code], cwd=HERE,
                           capture_output=True)
            ts.append((time.perf_counter() - t0) * 1000)
        print('  %-16s %.0f ms' % (label, min(ts)))

    print('== ④ runtime 头 -E 预处理时间（每次编译必付的下界） ==')
    ts = []
    for _ in range(3):
        t0 = time.perf_counter()
        subprocess.run(['g++', '-std=c++17', '-O2', '-E', '-x', 'c++', RT,
                        '-o', os.devnull], capture_output=True)
        ts.append((time.perf_counter() - t0) * 1000)
    print('  -E %.0f ms × %d 字节' % (min(ts), os.path.getsize(RT)))
    os.remove(cpp)


if __name__ == '__main__':
    main()
