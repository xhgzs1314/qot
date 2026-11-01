# -*- coding: utf-8 -*-
"""`builtins.toml` 的装载与派生视图（M1 · G-5 根治的唯一真相源入口）。

三侧都从这里取数，谁也不许再自己维护一份名单：

  * 解释器（`builtin_func.BuiltFunc`）：`iter_entries('interp')` + 实现查表
  * VM（C++，`builtin.cpp`）：由 `_gen_builtins.py` 生成 `builtins_manifest.h`
  * AOT（`lir_cgen.SUPPORTED_BUILTINS`）：`aot_supported()`

开关（G-14 面板 `_dbg_switches.py` 验证）：
  * `QOT_NO_ALIAS=1` —— 关闭别名登记，验证别名机制真的生效（关掉后 `trim()`
    这类新名应当回到"未定义"）。

装载只做一次（进程内缓存）；`check_*` 系列对账函数供 `_dbg_builtins.py`
调用，任何一侧漏注册都抛 `BuiltinManifestError`（= 构建失败）。
"""
import os
import sys

from qot_toml import load as _toml_load

__all__ = [
    'BuiltinManifestError', 'manifest_path', 'entries', 'by_name', 'alias_map',
    'interp_names', 'vm_entries', 'aot_supported', 'numeric_builtins',
    'alloc_free_builtins', 'container_mutators', 'callback_builtins',
    'engine_of', 'check_interp_impls', 'check_aot_symbols',
    'STUB_UNSUPPORTED',
]

STUB_UNSUPPORTED = '@unsupported'
ENGINES = ('interp', 'vm', 'native')

_HERE = os.path.dirname(os.path.abspath(__file__))
_CACHE = {}


class BuiltinManifestError(Exception):
    """清单与某一侧实现不一致（应当让构建失败，而不是静默降级）。"""


def manifest_path():
    """清单路径。可用 `QOT_BUILTINS_TOML` 覆盖（单测用它注入残缺清单，
    验证「缺一侧注册 = 构建失败」这道门真的会响）。"""
    return os.environ.get('QOT_BUILTINS_TOML') or os.path.join(_HERE, 'builtins.toml')


def _load():
    path = manifest_path()
    if _CACHE.get('_path') == path and 'entries' in _CACHE:
        return _CACHE['entries']
    _CACHE.clear()
    if not os.path.exists(path):
        raise BuiltinManifestError('缺少内置清单 %s' % path)
    data = _toml_load(path)
    raw = data.get('builtin')
    if not isinstance(raw, list) or not raw:
        raise BuiltinManifestError('%s 里没有 [[builtin]] 条目' % path)

    out = []
    seen = set()
    for i, e in enumerate(raw):
        if not isinstance(e, dict):
            raise BuiltinManifestError('第 %d 个 [[builtin]] 不是表' % (i + 1))
        name = e.get('name')
        if not name:
            raise BuiltinManifestError('第 %d 个 [[builtin]] 缺 name' % (i + 1))
        if name in seen:
            raise BuiltinManifestError('内置名重复登记：%s' % name)
        seen.add(name)
        eng = e.get('engines')
        if not eng:
            raise BuiltinManifestError('%s 缺 engines' % name)
        for g in eng:
            if g not in ENGINES:
                raise BuiltinManifestError('%s 的 engines 含未知引擎 %r' % (name, g))
        item = {
            'name': name,
            'group': e.get('group', 'misc'),
            'engines': list(eng),
            'argc': int(e.get('argc', -1)),
            'args': list(e.get('args') or ['any']),
            'ret': e.get('ret', 'any'),
            'interp': e.get('interp'),
            'vm': e.get('vm'),
            'native': e.get('native'),
            'aliases': list(e.get('aliases') or []),
            'numeric': bool(e.get('numeric', False)),
            'alloc_free': bool(e.get('alloc_free', False)),
            'mutates_container': bool(e.get('mutates_container', False)),
            'calls_back': bool(e.get('calls_back', False)),
            'stub_hint': e.get('stub_hint', ''),
            'desc': e.get('desc', ''),
        }
        for g in eng:
            if g == 'interp' and not item['interp']:
                raise BuiltinManifestError(
                    '%s 声明了 interp 引擎却没有 interp 字段（无实现写 "%s"）'
                    % (name, STUB_UNSUPPORTED))
            if g == 'vm' and not item['vm']:
                raise BuiltinManifestError('%s 声明了 vm 引擎却没有 vm 字段' % name)
            if g == 'native' and not item['native']:
                raise BuiltinManifestError(
                    '%s 声明了 native 引擎却没有 native 字段' % name)
        out.append(item)

    # 别名冲突检查（别名之间、别名与规范名之间都不能撞）
    owner = {}
    for it in out:
        for a in it['aliases']:
            if a in owner:
                raise BuiltinManifestError(
                    '别名 %s 被 %s 与 %s 同时登记' % (a, owner[a], it['name']))
            owner[a] = it['name']
    for a in list(owner):
        if a in seen:
            raise BuiltinManifestError(
                '别名 %s 与某个规范内置名撞名（%s）' % (a, a))
    _CACHE['_path'] = path
    _CACHE['entries'] = out
    return out


def entries():
    """全部清单条目（顺序 = 文件顺序）。"""
    return list(_load())


def by_name():
    d = _CACHE.get('by_name')
    if d is None:
        d = {}
        for it in _load():
            d[it['name']] = it
        _CACHE['by_name'] = d
    return d


def _aliases_enabled():
    return os.environ.get('QOT_NO_ALIAS') != '1'


def alias_map():
    """别名 → 规范名（QOT_NO_ALIAS=1 时为空表）。"""
    if not _aliases_enabled():
        return {}
    m = _CACHE.get('alias_map')
    if m is None:
        m = {}
        for it in _load():
            for a in it['aliases']:
                m[a] = it['name']
        _CACHE['alias_map'] = m
    return m


def canonical(name):
    return alias_map().get(name, name)


def interp_names():
    """解释器侧有【真实实现】的名字（规范名 + 别名）。"""
    names = [it['name'] for it in _load()
             if 'interp' in it['engines'] and it['interp'] != STUB_UNSUPPORTED]
    if _aliases_enabled():
        for it in _load():
            if 'interp' in it['engines'] and it['interp'] != STUB_UNSUPPORTED:
                names.extend(it['aliases'])
    return names


def language_names():
    """语言层可见的全部内置名（= 语义分析应当认识的名字）。

    包含没有解释器实现的那些（VM/AOT 专属内置）：它们在解释器里登记为
    明确报错的桩，而不是让语义分析报「函数未定义」把引擎归属问题藏起来
    （G-5 的老坑）。
    """
    names = [it['name'] for it in _load()]
    if _aliases_enabled():
        for it in _load():
            names.extend(it['aliases'])
    return names


def vm_entries():
    return [it for it in _load() if 'vm' in it['engines']]


def vm_registrations():
    """VM 侧要注册的名字 → C++ 符号（规范名 + 别名）。

    别名必须一起注册：别名是【语言层】的名字，只在解释器侧登记会让
    `len()` / `trim()` 这类新名在 VM/AOT 下静默得 null（G-5 同款分歧）。
    """
    out = [(it['name'], it['vm']) for it in vm_entries()]
    if _aliases_enabled():
        for it in vm_entries():
            for a in it['aliases']:
                out.append((a, it['vm']))
    return out


def aot_supported():
    """AOT（native）支持集：规范名 → qotrt 符号后缀。"""
    d = {}
    for it in _load():
        if 'native' in it['engines']:
            d[it['name']] = it['native']
    if _aliases_enabled():
        for it in _load():
            if 'native' in it['engines']:
                for a in it['aliases']:
                    d[a] = it['native']
    return d


def numeric_builtins():
    """可返回数值的内置（lir_opt 的 F64 推断白名单）。"""
    return set(it['name'] for it in _load() if it['numeric'])


def alloc_free_builtins():
    """不产出 arena 块的内置（arena 染色白名单）。"""
    return set(it['name'] for it in _load() if it['alloc_free'])


def container_mutators():
    """把实参存进容器的内置（P0-POOL-1 后新增的 arena 安全分类）。

    约定：args[0] = 目标容器，args[1:] = 被存进容器的值/键。lir_opt 的
    循环 arena 回收对这类调用做内容逃逸检查 —— 槽级逃逸分析看不见
    容器内容（arr_push(row, dyed_str) 的 dyed_str 不在任何 dst 上）。
    """
    return set(it['name'] for it in _load() if it['mutates_container'])


def callback_builtins():
    """native 侧执行 QOT 闭包回调的内置（map/filter/reduce/sort_by）。

    循环 arena 回收按 CALL_USER 同类否决：回调体可把 arena 块存进任何
    跨轮容器，插桩期无法证明其不逃逸（宁可不插桩也不冒悬空风险）。
    """
    return set(it['name'] for it in _load() if it['calls_back'])


def engine_of(name):
    """返回该名字（规范名或别名）的引擎列表；未登记返回 None。"""
    it = by_name().get(canonical(name))
    return list(it['engines']) if it else None


# ---------------------------------------------------------------- 一侧对账

def check_interp_impls(impls, registered):
    """解释器侧对账：清单 ↔ 实现表 ↔ 登记表。

    impls      : 名字 → 实现（BuiltFunc 里真正写出来的 handler）
    registered : 名字 → 是否已在解释器表里登记（BuiltFunc.functions 的键）
    任一方向缺失都抛 BuiltinManifestError（构建失败，符合 M1 验收门）。
    """
    problems = []
    want = set(language_names())        # 语言层全部名字（含桩）
    canon = set(it['name'] for it in _load()
                if 'interp' in it['engines'] and it['interp'] != STUB_UNSUPPORTED)
    for it in _load():
        if 'interp' not in it['engines']:
            continue
        if it['interp'] == STUB_UNSUPPORTED:
            continue  # 桩由清单自动生成，不需要手写实现
        if it['name'] not in impls:
            problems.append('解释器缺实现：%s（清单声明 interp="%s"）'
                            % (it['name'], it['interp']))
    for n in impls:
        if n not in canon:
            problems.append('解释器有实现但清单未登记：%s' % n)
    for n in want:
        if n not in registered:
            problems.append('清单声明 interp 但解释器表未登记：%s' % n)
    for n in registered:
        if n not in want:
            problems.append('解释器登记了清单之外的名字：%s' % n)
    if problems:
        raise BuiltinManifestError(
            '内置清单与解释器侧不一致（%d 处）：\n  - ' % len(problems)
            + '\n  - '.join(problems))
    return len(want)


def check_aot_symbols(supported):
    """AOT 侧对账：清单 native 集 == lir_cgen 的支持集。"""
    want = aot_supported()
    problems = []
    for k in want:
        if k not in supported:
            problems.append('AOT 支持集缺：%s' % k)
    for k in supported:
        if k not in want:
            problems.append('AOT 支持集多出（清单未登记）：%s' % k)
    if problems:
        raise BuiltinManifestError(
            '内置清单与 AOT 侧不一致（%d 处）：\n  - ' % len(problems)
            + '\n  - '.join(problems))
    return len(want)


def check_manifest_self():
    """清单自身健全性（供 _dbg_builtins.py 先跑，错误信息更友好）。"""
    _load()
    alias_map()
    return len(entries())


if __name__ == '__main__':
    # 自描述：python qot_builtins.py 打印清单概览
    _ = check_manifest_self()
    es = entries()
    print('内置清单：%s' % manifest_path())
    print('条目 %d' % len(es))
    for g in ENGINES:
        print('  %-7s %d' % (g, len([e for e in es if g in e['engines']])))
    print('别名 %d，AOT 支持 %d，numeric %d，alloc_free %d'
          % (len(alias_map()), len(aot_supported()),
             len(numeric_builtins()), len(alloc_free_builtins())))
    sys.exit(0)
