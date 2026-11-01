# -*- coding: utf-8 -*-
"""把 ot-asset 版 runtime 头的【JSON 段】同步进根版（两份副本必须逐字节同段）。

用途：runtime 头有两份正式副本（根 + ot-asset/c/aot），任何手术都必须双侧
同步（RT_SPLIT 手术即如此）。本脚本只搬运被标记的段落，避免手抄漂移。
"""
import io
import os
import sys

ROOT = r'e:\QGuoat-jsq\QGuoat'
SRC = os.path.join(ROOT, 'ot-asset', 'c', 'aot', 'qot_aot_runtime.h')
DST = os.path.join(ROOT, 'qot_aot_runtime.h')

BEGIN = '// ================================================================ JSON'
END = 'static V16 qotrt_dict_set_path(V16 *args, int argc)'


def main():
    src = io.open(SRC, encoding='utf-8').read()
    dst = io.open(DST, encoding='utf-8').read()
    i = src.find(BEGIN)
    j = src.find(END, i)
    if i < 0 or j < 0:
        print('源文件中未找到 JSON 段标记')
        return 1
    block = src[i:j]
    if BEGIN in dst:
        a = dst.find(BEGIN)
        b = dst.find(END, a)
        if b < 0:
            print('目标文件结构异常（有 BEGIN 无 END）')
            return 1
        new = dst[:a] + block + dst[b:]
        action = 'replace'
    else:
        b = dst.find(END)
        if b < 0:
            print('目标文件中未找到锚点')
            return 1
        new = dst[:b] + block + dst[b:]
        action = 'insert'
    if new != dst:
        io.open(DST, 'w', encoding='utf-8', newline='').write(new)
    print('同步完成：%s（%d 字节）' % (action, len(block)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
