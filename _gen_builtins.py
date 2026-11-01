# -*- coding: utf-8 -*-
"""_gen_builtins.py —— 从 builtins.toml 生成 VM 侧内置清单头。

    python _gen_builtins.py            # 生成 ot-asset/c/vmcshit/builtins_manifest.h
    python _gen_builtins.py --check    # 校验清单与 toml 一致（套件用），一致返回 0

规则（M1 / G-5）：
  * builtins.toml 是唯一真相源；engines 含 "vm" 的条目（含别名，别名各占
    一行、指向同一个 C++ 符号）才进清单；
  * 条目顺序继承 M1 时代的 VM 表序（ORDER 表），清单里没有的新条目按 toml
    顺序追加在尾部——顺序稳定，重跑不产生无谓 diff；
  * 清单是生成物，**不要手改**；改内置请改 builtins.toml 后重跑本脚本。
"""
import io
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(ROOT, 'ot-asset', 'c', 'vmcshit', 'builtins_manifest.h')

# M1 时代的 VM 表序
ORDER = [
    'output', 'cinget', 'glen', 'strings', 'push', 'remove',
    'pop', 'get', 'fopen', 'fread', 'fclose', 'fwrite',
    'unlink', 'fnew', 'sleep', 'gtypes', 'tnow', 'tstamp',
    'fsize', 'sqrt', 'rand', 'strcase', 'strrep', 'pow',
    'round', 'dict_get', 'dict_set', 'dict_keys', 'http_send', 'cmd_exec',
    'ScLoad', 'ScExec', 'ScSweep', 'set_env', 'get_env', 'unset_env',
    '__is_number', '__iter_len', 'json_encode', 'json_decode', 'type', 'numbers',
    'http_set_header', 'http_set_user_agent', 'http_set_verify_ssl',
    'http_clear_headers', 'http_get_headers', 'vg_init',
    'vg_uninit', 'vg_create_window', 'vg_show_window', 'vg_run', 'vg_close_window',
    'vg_set_window_title', 'vg_set_window_size', 'vg_set_window_bg_gradient',
    'vg_create_button', 'vg_button_set_text', 'vg_button_set_color',
    'vg_button_set_hover_color', 'vg_button_set_border_radius', 'vg_button_on_click',
    'vg_button_set_position', 'vg_create_checkbox', 'vg_checkbox_set_checked',
    'vg_checkbox_get_checked', 'vg_checkbox_on_changed', 'vg_create_toggle',
    'vg_toggle_set_checked', 'vg_toggle_get_checked', 'vg_toggle_on_changed',
    'vg_create_textbox', 'vg_textbox_set_text', 'vg_textbox_get_text',
    'vg_textbox_set_placeholder', 'vg_create_slider', 'vg_slider_set_value',
    'vg_slider_get_value', 'vg_slider_on_changed', 'vg_create_progressbar',
    'vg_progressbar_set_value', 'vg_progressbar_get_value', 'vg_progressbar_set_color',
    'vg_create_card', 'vg_card_set_title', 'vg_card_set_elevation',
    'vg_create_combobox', 'vg_combobox_add_item', 'vg_combobox_clear',
    'vg_combobox_set_selected', 'vg_combobox_get_selected', 'vg_combobox_on_selected',
    'vg_create_listbox', 'vg_listbox_add_item', 'vg_listbox_clear',
    'vg_listbox_set_selected', 'vg_listbox_get_selected', 'vg_listbox_on_selected',
    'vg_create_tabcontrol', 'vg_tabcontrol_add_tab', 'vg_tabcontrol_set_selected',
    'vg_create_spinner', 'vg_spinner_start', 'vg_spinner_stop',
    'vg_set_position', 'vg_set_size', 'vg_set_visible', 'vg_set_enabled',
    'vg_destroy', 'input', 'len', 'str', 'replace',
]

HEAD = '''\
// 本文件由 _gen_builtins.py 从 builtins.toml 生成，**不要手改**。
// 改内置请改 builtins.toml，然后重跑 `python _gen_builtins.py`。
//
// M1 / G-5：VM 侧的内置名单不再是独立维护的一份表。清单说有而 C++ 侧
// 没有对应函数 → 展开时符号未声明 → 编译失败（不会静默少注册一个）。
#ifndef QOT_BUILTINS_MANIFEST_H
#define QOT_BUILTINS_MANIFEST_H

// X(内置名, C++ 函数符号)。别名（len/trim/...）也各占一行、指向同一个
// 函数：别名是语言层名字，只在一侧登记就是新的 G-5 分歧。
#define QOT_VM_BUILTINS(X) \\
'''


def _vm_pairs():
    """[(语言名, C++ 符号)]：engines 含 vm 的条目 + 其别名，按 ORDER 优先。"""
    import qot_builtins as qb
    sym = {}
    toml_order = []
    for e in qb.entries():
        if 'vm' not in e['engines']:
            continue
        names = [e['name']] + [a for a in e['aliases'] if a != e['name']]
        for n in names:
            sym[n] = e['vm']
        toml_order.append(e['name'])
    ordered = [n for n in ORDER if n in sym]
    ordered += [n for n in toml_order if n not in ORDER and n in sym]
    return [(n, sym[n]) for n in ordered]


def render():
    pairs = _vm_pairs()
    lines = [HEAD]
    for i, (name, symbol) in enumerate(pairs):
        cont = ' \\' if i < len(pairs) - 1 else ''
        lines.append('    X(%s, %s)%s\n' % (name, symbol, cont))
    lines.append('\n')
    lines.append('#define QOT_VM_BUILTIN_COUNT %d\n' % len(pairs))
    lines.append('\n')
    lines.append('#endif  // QOT_BUILTINS_MANIFEST_H\n')
    return ''.join(lines)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    text = render()
    if '--check' in argv:
        try:
            with io.open(OUT, 'r', encoding='utf-8') as f:
                disk = f.read()
        except OSError:
            print('[FAIL] 清单不存在: %s' % OUT)
            return 1
        if disk == text:
            return 0
        import re
        disk_n = len(re.findall(r'X\(\w+,', disk))
        new_n = len(re.findall(r'X\(\w+,', text))
        print('[FAIL] builtins_manifest.h 与 builtins.toml 不一致'
              '（清单 %d 条 vs 应为 %d 条）—— 重跑 `python _gen_builtins.py`'
              % (disk_n, new_n))
        return 1
    with io.open(OUT, 'w', encoding='utf-8', newline='\n') as f:
        f.write(text)
    print('[OK] 已生成 %s（%d 条）' % (os.path.relpath(OUT, ROOT), len(_vm_pairs())))
    return 0


if __name__ == '__main__':
    sys.exit(main())
