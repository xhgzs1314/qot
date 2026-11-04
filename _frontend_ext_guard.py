# -*- coding: utf-8 -*-
"""前端 Cython 扩展新鲜度护栏

铁律：宁可纯 Python 慢，绝不静默跑过期 .pyd。
  * .pyd 与其源 .py 的内容指纹不符 ⇒ 立即把 .pyd 改名让位
    （后缀 .stale_时间戳），回落纯 .py 并大声告警 —— 行为永远正确，
    最坏情形只是回到编译前速度。
  * manifest（_frontend_ext.manifest.json）由 _build_frontend.bat 构建
    成功尾段生成；无 manifest = 从未构建过扩展 = 纯 .py 直行。
  * .pyd 文件名带 EXT_SUFFIX（cp39-win_amd64），其它 Python 版本天然
    找不到它 → 纯 .py，无需本护栏操心。

使用方式：index.py 第一行 `import _frontend_ext_guard`，模块导入即执行
检查（先于 index.py 顶部对 bytecode/grammar/lir* 的全部导入）。
检查成本 = 12 个 .py 的 md5（约 0.7MB）≈ 数毫秒。

已知边界（如实）：
  * Windows 上已加载的 .pyd 被进程锁定，改名失败 ⇒ 硬退出并提示先结束
    其它 QOT 进程（比静默用错扩展安全）。
  * 直接 import grammar/lir* 而不经 index.py 的旁路入口不受本护栏保护
    （主构建入口 index.py 已覆盖；其余入口本就不该绕过 index）。
"""
import hashlib
import json
import os
import sys
import time

_MANIFEST = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         '_frontend_ext.manifest.json')


def _md5(path):
    h = hashlib.md5()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 16), b''):
            h.update(chunk)
    return h.hexdigest()


def check():
    """过期 .pyd 改名让位（回落纯 .py）；无扩展/无 manifest 时静默直行。"""
    try:
        with open(_MANIFEST, 'r', encoding='utf-8') as f:
            man = json.load(f)
    except (OSError, ValueError):
        return
    root = os.path.dirname(_MANIFEST)
    stale = []
    for name, ent in sorted(man.get('modules', {}).items()):
        pyd_path = os.path.join(root, ent.get('pyd', ''))
        if not os.path.exists(pyd_path):
            continue   # 本机没有该扩展（其它 Python 版本/未构建该模块）
        py_path = os.path.join(root, name + '.py')
        try:
            ok = os.path.exists(py_path) and _md5(py_path) == ent.get('md5')
        except OSError:
            ok = False
        if ok:
            continue
        stamp = time.strftime('%Y%m%d_%H%M%S')
        try:
            os.rename(pyd_path, pyd_path + '.stale_' + stamp)
        except OSError:
            # 已加载的 DLL 锁定（并发 QOT 进程）—— 硬退出，绝不静默用错
            sys.stderr.write(
                '[frontend-ext] 致命: %s 已过期且被进程锁定无法让位，'
                '请先结束其它 QOT/Python 进程后重试。\n' % ent.get('pyd'))
            raise SystemExit(2)
        stale.append(ent['pyd'])
    if stale:
        print('[frontend-ext] 警告: %d 个前端扩展已过期（源码有改动），'
              '本次回落纯 Python: %s' % (len(stale), ', '.join(stale)))
        print('[frontend-ext] 恢复编译请运行: _build_frontend.bat')


check()
