# -*- coding: utf-8 -*-
"""QOT 自带的最小 TOML 解析器（子集）。

为什么自己写：
  * 本机 Python 是 3.9.12，`tomllib` 要 3.11+；
  * SPEC R8 明确禁止 3.9 之后的独有 API（不用 tomllib）；
  * 只给 `builtins.toml` 这一份清单用，不需要完整 TOML 1.0 —— 支持面写死
    在下面，超出面一律**报错**（静默误读比不支持危险得多，这是 G-5 的教训）。

支持面：
  * 注释（`#` 行首或行尾；字符串内的 `#` 不当注释）
  * `[table]`、`[[array-of-tables]]`（含 `[a.b]` 一层点号路径）
  * `key = value`，value 支持：基本字符串 `"..."`、字面字符串 `'...'`、
    整数（含 `+/-` 与 `0x`/`0o`/`0b`）、布尔、数组（可跨行、可尾逗号）、
    内联表 `{ k = v, ... }`
  * 支持面之外（三引号多行字符串、浮点、日期时间、深层点号路径）→ 抛
    `TomlError` 并给出行号。

反例自测见 `testing/unit/test_qot_toml.py`（`python testing/run.py --suite unit`）。
"""
import re

__all__ = ['TomlError', 'loads', 'load']


class TomlError(ValueError):
    """TOML 语法/支持面错误（带行号）。"""

    def __init__(self, msg, line=None, text=None):
        self.line = line
        detail = msg
        if line is not None:
            detail = '第 %d 行：%s' % (line, msg)
        if text is not None:
            detail += '  >>> ' + text.strip()
        ValueError.__init__(self, detail)


_INT_RE = re.compile(r'^[+-]?(0x[0-9A-Fa-f_]+|0o[0-7_]+|0b[01_]+|[0-9_]+)$')


def _strip_comment(line):
    """去掉行尾注释（尊重引号内的 #）。返回无注释文本。"""
    out = []
    quote = None
    i = 0
    while i < len(line):
        ch = line[i]
        if quote:
            out.append(ch)
            if ch == '\\' and quote == '"' and i + 1 < len(line):
                out.append(line[i + 1])
                i += 2
                continue
            if ch == quote:
                quote = None
        else:
            if ch == '#':
                break
            if ch in ('"', "'"):
                quote = ch
            out.append(ch)
        i += 1
    return ''.join(out)


def _parse_string(raw, line):
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in ('"', "'"):
        body = raw[1:-1]
        if raw[0] == "'":
            if '"' in body or '\\' in body:
                # 字面字符串不转义 —— 出现反斜杠照样原样收下（子集内够用）
                return body
            return body
        # 基本字符串：只支持最小转义集，其余按字面保留（够用且不做静默变形）
        repl = {'\\n': '\n', '\\t': '\t', '\\r': '\r', '\\"': '"', '\\\\': '\\'}
        out = []
        i = 0
        while i < len(body):
            if body[i] == '\\' and i + 1 < len(body):
                two = body[i:i + 2]
                if two in repl:
                    out.append(repl[two])
                    i += 2
                    continue
            out.append(body[i])
            i += 1
        return ''.join(out)
    raise TomlError('不是合法字符串', line, raw)


def _parse_value(raw, line):
    raw = raw.strip()
    if not raw:
        raise TomlError('空值', line)
    if raw[0] in ('"', "'"):
        return _parse_string(raw, line)
    if raw[0] == '[':
        return _parse_array(raw, line)
    if raw[0] == '{':
        return _parse_inline_table(raw, line)
    if raw in ('true', 'false'):
        return raw == 'true'
    if _INT_RE.match(raw):
        return int(raw.replace('_', ''), 0)
    if re.match(r'^[+-]?[0-9_]*\.[0-9_]*', raw) or 'e' in raw.lower():
        raise TomlError('本解析器不支持浮点（清单里不许出现浮点）', line, raw)
    raise TomlError('无法解析的值', line, raw)


def _split_top_level(raw, line):
    """按顶层逗号切分（跳过引号/嵌套括号）。"""
    items = []
    buf = []
    depth = 0
    quote = None
    for ch in raw:
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in ('"', "'"):
            quote = ch
            buf.append(ch)
        elif ch in '[{':
            depth += 1
            buf.append(ch)
        elif ch in ']}':
            depth -= 1
            buf.append(ch)
        elif ch == ',' and depth == 0:
            items.append(''.join(buf))
            buf = []
        else:
            buf.append(ch)
    tail = ''.join(buf).strip()
    if tail:
        items.append(tail)
    if quote:
        raise TomlError('引号未闭合', line, raw)
    if depth:
        raise TomlError('括号未闭合', line, raw)
    return [it.strip() for it in items if it.strip()]


def _parse_array(raw, line):
    raw = raw.strip()
    if not raw.endswith(']'):
        raise TomlError('数组未闭合', line, raw)
    body = raw[1:-1].strip()
    if not body:
        return []
    return [_parse_value(it, line) for it in _split_top_level(body, line)]


def _parse_inline_table(raw, line):
    raw = raw.strip()
    if not raw.endswith('}'):
        raise TomlError('内联表未闭合', line, raw)
    body = raw[1:-1].strip()
    out = {}
    if not body:
        return out
    for it in _split_top_level(body, line):
        if '=' not in it:
            raise TomlError('内联表缺少 =', line, it)
        k, v = it.split('=', 1)
        out[k.strip().strip('"\'')] = _parse_value(v, line)
    return out


def _scan_state(s, depth, quote):
    """跟踪括号深度与引号状态（供跨行续接判断）。"""
    for ch in s:
        if quote:
            if ch == quote:
                quote = None
        elif ch in ('"', "'"):
            quote = ch
        elif ch in '[{':
            depth += 1
        elif ch in ']}':
            depth -= 1
    return depth, quote


def _logical_lines(text):
    """把物理行合并成逻辑行：数组/内联表/引号跨行时续接到闭合为止。"""
    out = []
    buf = ''
    buf_line = 0
    depth = 0
    quote = None
    for idx, ln in enumerate(text.splitlines()):
        s = _strip_comment(ln).strip()
        if buf:
            buf = (buf + ' ' + s).strip()
        else:
            buf_line = idx + 1
            buf = s
        depth, quote = _scan_state(s, depth, quote)
        if depth < 0:
            raise TomlError('括号不匹配', idx + 1, ln)
        if depth == 0 and quote is None:
            if buf:
                out.append((buf_line, buf))
            buf = ''
    if buf:
        raise TomlError('块未闭合（数组或引号一直跨到文件尾）', buf_line, buf)
    return out


def loads(text):
    """解析 TOML 文本 → dict。"""
    if text.startswith('\ufeff'):
        text = text[1:]  # 容忍 BOM（编辑器/管道很容易加上）
    root = {}
    cur = root
    i = 0
    logical = _logical_lines(text)
    while i < len(logical):
        lineno, raw = logical[i]
        i += 1
        if not raw:
            continue
        if raw.startswith('[['):
            if not raw.endswith(']]'):
                raise TomlError('数组表头未闭合', lineno, raw)
            path = raw[2:-2].strip().split('.')
            if not path or any(not p for p in path):
                raise TomlError('空表路径', lineno, raw)
            cur = root
            for seg in path[:-1]:
                cur = cur.setdefault(seg, {})
                if not isinstance(cur, dict):
                    raise TomlError('表路径冲突', lineno, raw)
            arr = cur.setdefault(path[-1], [])
            if not isinstance(arr, list):
                raise TomlError('[[..]] 与既有键冲突', lineno, raw)
            item = {}
            arr.append(item)
            cur = item
            continue
        if raw.startswith('['):
            if not raw.endswith(']'):
                raise TomlError('表头未闭合', lineno, raw)
            path = raw[1:-1].strip().split('.')
            if not path or any(not p for p in path):
                raise TomlError('空表路径', lineno, raw)
            cur = root
            for seg in path[:-1]:
                cur = cur.setdefault(seg, {})
                if not isinstance(cur, dict):
                    raise TomlError('表路径冲突', lineno, raw)
            prev = cur.get(path[-1])
            if isinstance(prev, dict):
                cur = prev
            elif prev is None:
                cur[path[-1]] = {}
                cur = cur[path[-1]]
            else:
                raise TomlError('表与既有键冲突', lineno, raw)
            continue
        if '=' not in raw:
            raise TomlError('既不是表头也不是键值对', lineno, raw)
        k, v = raw.split('=', 1)
        k = k.strip()
        if not k:
            raise TomlError('空键名', lineno, raw)
        val = _parse_value(v, lineno)
        if k in cur:
            raise TomlError('重复键 %s' % k, lineno, raw)
        cur[k] = val
    return root


def load(path):
    with open(path, 'r', encoding='utf-8-sig') as f:
        return loads(f.read())
