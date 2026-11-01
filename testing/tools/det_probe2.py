# -*- coding: utf-8 -*-
"""编译 CI 线 · 确定性补测：--no-insert-timestamp 跨时间稳定性（K3）。"""
import hashlib
import os
import subprocess
import time

HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CPP = os.path.join(HERE, '_det_ts_src.cpp')


def sha(p):
    h = hashlib.sha256()
    h.update(open(p, 'rb').read())
    return h.hexdigest()


def main():
    with open(CPP, 'w', encoding='utf-8', newline='\n') as f:
        f.write('#include <cstdio>\nint main(){ printf("hi\\n"); return 0; }\n')
    print('== --no-insert-timestamp ×3（每次间隔 1.1s，跨秒） ==')
    hs = []
    for i in range(3):
        exe = os.path.join(HERE, '_det_ts_%d.exe' % i)
        r = subprocess.run(['g++', '-std=c++17', '-O2', '-static',
                            '-static-libgcc', '-static-libstdc++', '-s',
                            '-Wl,--no-insert-timestamp', CPP, '-o', exe],
                           capture_output=True)
        assert r.returncode == 0, r.stderr.decode('utf-8', 'replace')
        hs.append(sha(exe))
        os.remove(exe)
        time.sleep(1.1)
    for h in hs:
        print('  %s' % h[:24])
    print('  => %s' % ('字节级确定（K3 通过：链接器标准化方案成立）'
                       if len(set(hs)) == 1 else '仍不确定（需查 build-id/导入表）'))
    os.remove(CPP)


if __name__ == '__main__':
    main()
