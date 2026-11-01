# -*- coding: utf-8 -*-
"""QOT-CI · PCH 字节透明性预测试：同一 TU 用 PCH 与不用 PCH 编译，产物必须逐字节一致
（K1 的等价性设计前提：PCH 只能加速，不能改变输出）。"""
import hashlib
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RT = os.path.join(ROOT, 'ot-asset', 'c', 'aot', 'qot_aot_runtime.h')
WORK = os.path.join(ROOT, 'testing', '.artifacts', '_pch_eq')
FLAGS = ['-std=c++17', '-O2', '-finput-charset=utf-8']
DET = ['-Wl,--no-insert-timestamp']


def sha(p):
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        h.update(f.read())
    return h.hexdigest()


def main():
    os.makedirs(WORK, exist_ok=True)
    hdr = os.path.join(WORK, 'qot_aot_runtime.h')
    shutil.copyfile(RT, hdr)
    cpp = os.path.join(WORK, 't.cpp')
    with open(cpp, 'w', encoding='utf-8', newline='\n') as f:
        f.write('#include "qot_aot_runtime.h"\n'
                'static V16 g[4];\n'
                'static void qot_main(V16 *ret, V16 *args, int argc){\n'
                '  (void)ret;(void)args;(void)argc; g[0]=qotrt_num(1.5);\n'
                '  qotrt_output(&g[0],1);\n'
                '}\n'
                'int main(int argc,char**argv){qotrt_init_args(argc,argv);'
                'qotrt_init_limits();V16 r;qot_main(&r,(V16*)0,0);return 0;}\n')
    exe_a = os.path.join(WORK, 'a.exe')
    exe_b = os.path.join(WORK, 'b.exe')
    # a：无 PCH（头在 TU 目录，直接解析）
    r1 = subprocess.run(['g++'] + FLAGS + [cpp, '-o', exe_a, '-static',
                        '-static-libgcc', '-static-libstdc++', '-s',
                        '-lws2_32'] + DET, capture_output=True)
    assert r1.returncode == 0, r1.stderr.decode('utf-8', 'replace')
    # b：生成 PCH 后编译（头解析走 .gch）
    r2 = subprocess.run(['g++'] + FLAGS + ['-x', 'c++-header', '-o', hdr + '.gch', hdr],
                        capture_output=True)
    assert r2.returncode == 0, r2.stderr.decode('utf-8', 'replace')
    r3 = subprocess.run(['g++'] + FLAGS + ['-Winvalid-pch', cpp, '-o', exe_b,
                        '-static', '-static-libgcc', '-static-libstdc++', '-s',
                        '-lws2_32'] + DET, capture_output=True)
    assert r3.returncode == 0, r3.stderr.decode('utf-8', 'replace')
    err = (r3.stderr or b'').decode('utf-8', 'replace')
    if 'pch' in err.lower():
        print('[WARN] PCH 校验输出：', err[:300])
    a, b = sha(exe_a), sha(exe_b)
    print('无 PCH :', a[:24])
    print('有 PCH :', b[:24])
    ok = a == b
    print('[%s] PCH %s 产物字节' % ('OK' if ok else 'FAIL',
                                    '不影响' if ok else '影响'))
    shutil.rmtree(WORK, ignore_errors=True)
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
