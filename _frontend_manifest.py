# -*- coding: utf-8 -*-
"""前端扩展 manifest 写手

构建成功后由 _build_frontend.bat 调用：对 MODULES 里每个模块记录源 .py
的 md5 与实际产出的 .pyd 文件名，写 _frontend_ext.manifest.json。
_frontend_ext_guard.py 以此判定 .pyd 是否过期（源码有改动未重编）
"""
import datetime
import hashlib
import json
import os
import sys
import sysconfig

from setup_frontend import MODULES


def _md5(path):
    h = hashlib.md5()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 16), b''):
            h.update(chunk)
    return h.hexdigest()


def main():
    root = os.path.dirname(os.path.abspath(__file__))
    suffix = sysconfig.get_config_var('EXT_SUFFIX')
    modules = {}
    missing = []
    for m in MODULES:
        py_path = os.path.join(root, m + '.py')
        pyd_name = m + suffix
        pyd_path = os.path.join(root, pyd_name)
        if not os.path.exists(pyd_path):
            missing.append(pyd_name)
            continue
        modules[m] = {'pyd': pyd_name, 'md5': _md5(py_path)}
    if missing:
        print('[frontend-manifest] 缺少产物（未写入 manifest）: %s'
              % ', '.join(missing))
        return 1
    man = {
        'built': datetime.datetime.now().isoformat(timespec='seconds'),
        'python': sys.version.split()[0],
        'ext_suffix': suffix,
        'modules': modules,
    }
    target = os.path.join(root, '_frontend_ext.manifest.json')
    tmp = target + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(man, f, ensure_ascii=False, indent=1, sort_keys=True)
    os.replace(tmp, target)
    print('[frontend-manifest] 已写入 %s（%d 模块）' % (target, len(modules)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
