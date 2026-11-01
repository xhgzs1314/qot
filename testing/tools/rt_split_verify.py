# -*- coding: utf-8 -*-
"""QOT-CI · 零回归不变量校验：runtime 头的 QOT_RT_SPLIT 手术对单 TU 预处理
产物零影响（不定义宏时，剥离行号指令后的 token 流必须与术前逐字节一致）。"""
import hashlib
import io
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _old_header(src):
    pat = re.compile(r'#ifdef QOT_RT_SPLIT\n(extern [^\n]+)\n#else\n(static [^\n]+)\n#endif\n')
    old, n = pat.subn(lambda m: m.group(2) + '\n', src)
    return old, n


def _pp_norm(path):
    r = subprocess.run(['g++', '-std=c++17', '-O2', '-finput-charset=utf-8',
                        '-E', '-x', 'c++', path], capture_output=True)
    if r.returncode != 0:
        print(r.stderr.decode('utf-8', 'replace')[:400])
        sys.exit(2)
    # token 流口径：剥行号标记与空行（被跳过的条件编译组只留空行占位，
    # 属于位置信息而非 token；真正要守住的是 token 流逐字节一致）
    out = [ln for ln in r.stdout.decode('utf-8', 'replace').splitlines()
           if ln and not re.match(r'# \d+', ln)]
    return hashlib.sha256('\n'.join(out).encode('utf-8')).hexdigest()


def main():
    hdr = os.path.join(ROOT, 'ot-asset', 'c', 'aot', 'qot_aot_runtime.h')
    src = io.open(hdr, encoding='utf-8').read()
    old, n = _old_header(src)
    # 【M7 】32 → 39：L2 原生插件宿主新增 7 个单实例状态
    # （qotrt_plugtab/_cap/_free + qotrt_pfn/_cap/_len/_loading）。
    if n != 39:
        print('[FAIL] 反演块数 %d != 39（手术形态漂移，请对照 rt_split_patch.py）' % n)
        return 1
    tmp_dir = os.path.join(ROOT, 'testing', '.artifacts', '_tmp_cmp')
    os.makedirs(tmp_dir, exist_ok=True)
    old_path = os.path.join(tmp_dir, 'qot_aot_runtime.h')
    io.open(old_path, 'w', encoding='utf-8', newline='').write(old)
    a = _pp_norm(old_path)
    b = _pp_norm(hdr)
    os.remove(old_path)
    os.rmdir(tmp_dir)
    print('术前预处理指纹: %s' % a[:24])
    print('术后预处理指纹: %s' % b[:24])
    if a != b:
        print('[FAIL] 单 TU 预处理产物发生变化（零回归不变量被破坏）')
        return 1
    print('[OK] 单 TU 预处理产物逐字节一致（K1/G1 零回归不变量成立）')
    return 0


if __name__ == '__main__':
    sys.exit(main())
