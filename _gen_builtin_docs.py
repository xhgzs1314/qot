# -*- coding: utf-8 -*-
"""_gen_builtin_docs.py —— 从 builtins.toml 生成站点的内置函数数据。

    python _gen_builtin_docs.py            # 生成 plugin/builtin_data.js

数据形态与 plugin/function_inside.html 的渲染器约定一致：
    FUNCTIONS_DATA = [ { name, desc, params, returns, category,
                         interp, native, vm, aliasOf? }, ... ]
    BUILTIN_STATS   = { total, canonical, aliases, interp, native,
                        both, interpOnly, nativeOnly, vmLegacy }
category（中文分组名）继承自上一版数据文件：同名条目沿用旧 category，
新条目回退到 group 名 —— 保证页面分组文案稳定。
"""
import io
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
TOML = os.path.join(ROOT, 'builtins.toml')
OUT = os.path.join(ROOT, 'plugin', 'builtin_data.js')
OLD = OUT  # category 继承源 = 上一版数据（自身）

sys.stdout.reconfigure(encoding='utf-8')


def _js_str(s):
    return json.dumps(s, ensure_ascii=False)


def parse_toml():
    text = io.open(TOML, encoding='utf-8').read()
    entries = []
    for b in re.split(r'(?=\[\[builtin\]\])', text)[1:]:
        def f(k, dflt=None):
            m = re.search(r'^%s = "(.*?)"' % k, b, re.M)
            return m.group(1) if m else dflt
        name = f('name')
        if not name:
            continue
        em = re.search(r'^engines = \[(.*?)\]', b, re.M)
        engines = re.findall(r'"(\w+)"', em.group(1)) if em else []
        dm = re.search(r'^desc = "(.*?)"', b, re.M)
        desc = dm.group(1) if dm else ''
        am = re.search(r'^aliases = \[(.*?)\]', b, re.M)
        aliases = re.findall(r'"(\w+)"', am.group(1)) if am else []
        entries.append({
            'name': name,
            'group': f('group', 'other'),
            'engines': engines,
            'desc': desc.replace('\\"', '"'),
            'aliases': aliases,
        })
    return entries


def old_categories():
    """name → category（从上一版数据继承；缺文件则空表）。"""
    try:
        s = io.open(OLD, encoding='utf-8').read()
    except OSError:
        return {}
    out = {}
    for m in re.finditer(r'name: "([\w]+)", desc:.*?category: "([^"]*)"', s):
        out[m.group(1)] = m.group(2)
    return out


def main():
    entries = parse_toml()
    cats = old_categories()
    rows = []
    for e in entries:
        eng = e['engines']
        row = {
            'name': e['name'],
            'desc': e['desc'],
            'params': [{'name': '参数...', 'type': 'any', 'desc': ''}],
            'returns': '任意',
            'category': cats.get(e['name'], e['group']),
            'interp': 'interp' in eng,
            'native': 'native' in eng,
            'vm': 'vm' in eng,
        }
        rows.append(row)
        for a in e['aliases']:
            rows.append(dict(row, name=a, aliasOf=e['name'],
                             desc='别名：%s（同一函数）' % e['name']))
    rows.sort(key=lambda r: (r['category'], r['name']))

    body = []
    for r in rows:
        parts = ['name: %s' % _js_str(r['name']),
                 'desc: %s' % _js_str(r['desc']),
                 'params: [{name: "参数...", type: "any", desc: ""}]',
                 'returns: "任意"',
                 'category: %s' % _js_str(r['category']),
                 'interp: %s' % ('true' if r['interp'] else 'false'),
                 'native: %s' % ('true' if r['native'] else 'false'),
                 'vm: %s' % ('true' if r['vm'] else 'false')]
        if r.get('aliasOf'):
            parts.append('aliasOf: %s' % _js_str(r['aliasOf']))
        body.append('    { %s },' % ', '.join(parts))

    canon = [r for r in rows if not r.get('aliasOf')]
    interp_n = sum(1 for r in canon if r['interp'])
    native_n = sum(1 for r in canon if r['native'])
    both_n = sum(1 for r in canon if r['interp'] and r['native'])
    interp_only = sum(1 for r in canon if r['interp'] and not r['native'])
    native_only = sum(1 for r in canon if r['native'] and not r['interp'])
    vm_legacy = sum(1 for r in canon if r['vm'])

    doc = ('/* 本文件由 _gen_builtin_docs.py 自动生成，请勿手工编辑。\n'
           '   数据源：builtins.toml（内置函数唯一真相源）。\n */\n\n'
           'const FUNCTIONS_DATA = [\n' + '\n'.join(body) + '\n];\n'
           'const BUILTIN_STATS = {\n'
           '    total: %d,\n    canonical: %d,\n    aliases: %d,\n'
           '    interp: %d,\n    native: %d,\n    both: %d,\n'
           '    interpOnly: %d,\n    nativeOnly: %d,\n    vmLegacy: %d\n};\n'
           % (len(rows), len(canon), len(rows) - len(canon),
              interp_n, native_n, both_n, interp_only, native_only,
              vm_legacy))
    with io.open(OUT, 'w', encoding='utf-8', newline='\n') as f:
        f.write(doc)
    print('[OK] 已生成 %s（canonical %d + 别名 %d = %d；'
          '双端 %d / 仅解释器 %d / 仅原生 %d / 含 vm %d）'
          % (os.path.relpath(OUT, ROOT), len(canon), len(rows) - len(canon),
             len(rows), both_n, interp_only, native_only, vm_legacy))
    return 0


if __name__ == '__main__':
    sys.exit(main())

