# -*- coding: utf-8 -*-
"""编译 CI 线 · runtime 头 static 状态全量盘点（切分 TU 前置审计）。"""
import re

HERE = r'e:\QGuoat-jsq\QGuoat'
for p in (HERE + r'\ot-asset\c\aot\qot_aot_runtime.h',):
    src = open(p, encoding='utf-8').read()
    lines = src.split('\n')
    # 文件级 static 变量（排除函数：static 后跟类型+名字[数组/初始化]，无 '(' 头）
    pat_var = re.compile(r'^static\s+(?!void\b|V16\s+\w+\s*\(|int\s+\w+\s*\(|bool\s+\w+\s*\(|char\s*\*?\s*\w+\s*\()')
    hits = []
    for i, ln in enumerate(lines, 1):
        if ln.startswith('static ') and not re.match(r'^static\s+(\w[\w\s\*]*?)\s+\w+\s*\(', ln):
            # 排除 static 函数声明/定义（名字后直接跟 '('）
            if '(' in ln.split('=')[0]:
                continue
            hits.append((i, ln))
    print('== %s：文件级 static 变量 %d 个 ==' % (p, len(hits)))
    for i, ln in hits:
        print('%5d  %s' % (i, ln))
