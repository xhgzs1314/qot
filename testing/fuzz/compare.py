# -*- coding: utf-8 -*-
"""fuzz 三方差分的输出比较器 ——  §3.1 / S-01。

问题背景（README §21 / SPEC §3.5）：解释器与 VM 对数字的打印格式不同
（`1` vs `1.000000`、`0.1+0.2` 全精度 vs %.6f），直接逐字节比对必然假红；
但简单的"数字归一化"又会掩盖真实分歧。本模块的做法：

  1. 每行输出切成 token 流：数字样式的子串 → ('n', float)，其余 → ('t', str)；
  2. token 序列必须完全同构（类型、顺序、非数字文本逐字符一致）；
  3. 数字 token 按「|a-b| ≤ max(2e-6, 8*ULP(a))」比较：
       * 2e-6 绝对项覆盖 VM %.6f 的六位小数舍入（误差 ≤ 5e-7）；
       * 8*ULP 覆盖解释器精确 int 与 VM double 在 2^53 附近的合法舍入
         （fuzz 生成器把数值约束在 ±1e9 内，实际 ULP 项远小于绝对项）；
       * NaN==NaN 视为相等；inf/-inf 精确匹配。
  4. 已知定价分歧（负数取模符号、数值 && 的 null）由生成器规避 +
     known_divergences 白名单文档化，不进入本层容差（容差只容格式，不容语义）。

本模块不依赖 QOT 代码，可独立单测。
"""
import math
import re
import struct

# 数字样式：VM %.6f / 解释器 str(float)/str(int) / 科学计数 / inf / nan。
# (?<![\w.]) 防止匹配 "v1" 里的 1、"a.b" 里的 b；(?![\w.]) 防止吃掉后缀字母。
_NUM_RE = re.compile(
    r'(?<![\w.])'
    r'(?:[-+]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][-+]?\d+)?'
    r'|[-+]?(?:inf|nan))'
    r'(?![\w.])',
    re.IGNORECASE,
)

# loose 版：不要求数字后有边界（用于「数字紧贴文本」输出的第二阶段比对）
_NUM_RE_LOOSE = re.compile(
    r'(?<![\w.])'
    r'(?:[-+]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][-+]?\d+)?'
    r'|[-+]?(?:inf|nan))',
    re.IGNORECASE,
)

ABS_TOL = 2e-6
ULP_TOL = 8


def _ulp_distance(a: float, b: float) -> float:
    """两个有限 float 之间的 ULP 距离（非负浮点）。"""
    if a == b:
        return 0.0
    ia = struct.unpack('>q', struct.pack('>d', a))[0]
    ib = struct.unpack('>q', struct.pack('>d', b))[0]
    if ia < 0:
        ia = -0x8000000000000000 - ia
    if ib < 0:
        ib = -0x8000000000000000 - ib
    return float(abs(ia - ib))


def numbers_close(a: float, b: float, abs_tol: float = ABS_TOL,
                  ulp_tol: float = ULP_TOL) -> bool:
    if math.isnan(a) and math.isnan(b):
        return True
    if math.isnan(a) or math.isnan(b):
        return False
    if math.isinf(a) or math.isinf(b):
        return a == b
    if a == b:
        return True
    if abs(a - b) <= abs_tol:
        return True
    return _ulp_distance(a, b) <= ulp_tol


def tokenize_line(line: str, loose: bool = False):
    """一行输出 → token 列表：('n', float) / ('t', str)。

    strict（默认）：数字后必须跟非字母（防把 "v1" 里的 1 当数字）；
    loose：放开尾边界 —— 用于「数字直接拼接文本」的行（interp "3zz" vs
    VM "3.000000zz"，两边的数字 token 后都紧跟字母）。
    """
    rex = _NUM_RE_LOOSE if loose else _NUM_RE
    tokens = []
    pos = 0
    for m in rex.finditer(line):
        if m.start() > pos:
            tokens.append(('t', line[pos:m.start()]))
        text = m.group(0)
        low = text.lower().lstrip('+-')
        if low in ('inf', 'nan'):
            sign = -1.0 if text.startswith('-') else 1.0
            val = float(low) * sign if low == 'inf' else float(low)
        else:
            try:
                val = float(text)
            except ValueError:
                val = float('nan')
            except OverflowError:
                # 309 位十进制大整数（解释器精确 int 语义）→ 量级上就是 ±inf
                val = float('-inf') if text.startswith('-') else float('inf')
        tokens.append(('n', val))
        pos = m.end()
    if pos < len(line):
        tokens.append(('t', line[pos:]))
    return tokens


def canonical_lines(text: str):
    """stdout 全文 → 归一化行列表（空行丢弃，行尾空白剥离；同 run.normalize）。"""
    text = (text or '').replace('\r\n', '\n').replace('\r', '\n')
    out = []
    for ln in text.split('\n'):
        ln = ln.rstrip()
        if ln.strip():
            out.append(ln)
    return out


def _compare_line(ra: str, rb: str, loose: bool):
    ta, tb = tokenize_line(ra, loose), tokenize_line(rb, loose)
    if len(ta) != len(tb):
        return 'token 数不同: %r vs %r' % (ra, rb)
    for k, (x, y) in enumerate(zip(ta, tb)):
        if x[0] != y[0]:
            return '第 %d 个 token 类型不同: %r vs %r' % (k + 1, ra, rb)
        if x[0] == 't':
            if x[1] != y[1]:
                return '文本不同: %r vs %r' % (x[1], y[1])
        else:
            if not numbers_close(x[1], y[1]):
                return ('数值超容差: %r vs %r (a=%r b=%r)'
                        % (ra, rb, x[1], y[1]))
    return None


def compare_outputs(text_a: str, text_b: str):
    """归一化比较两路输出。返回 None（一致）或人可读的差异说明字符串。

    两阶段：先 strict（数字须后随非字母），任一行失败再对该行做 loose
    （数字紧贴文本，如 interp "3zz" vs VM "3.000000zz"）。两阶段都失败才算分歧。
    """
    la = canonical_lines(text_a)
    lb = canonical_lines(text_b)
    if len(la) != len(lb):
        return '行数不同: %d vs %d' % (len(la), len(lb))
    for i, (ra, rb) in enumerate(zip(la, lb)):
        d = _compare_line(ra, rb, loose=False)
        if d is not None:
            d = _compare_line(ra, rb, loose=True)
        if d is not None:
            return '第 %d 行 %s' % (i + 1, d)
    return None
