# -*- coding: utf-8 -*-
"""QOT 随机程序生成器 ——  §3.1 / S-01。

目标：批量产出【语法合法、双运行时可运行、输出可比】的 QOT 程序，
专朝控制流深嵌套、运算符组合、函数调用、容器操作打。

设计核心（与"随手拼字符串"的差距）：
  * 符号表驱动：每个变量带类型（number/bool/string/array/dict）与数值区间
    [lo, hi]，表达式生成是区间封闭运算——越界候选被拒绝并重试，从根上排除
    「解释器致命语义」（除零 2004 / 越界 2003 / 未定义 2001 / glen(dict) 2010）。
  * 数值有界（|v| ≤ 1e9 ≪ 2^53）：解释器精确 int 与 VM double 的合法舍入差
    被压到比较容差内，不会假红（KNOWN-INT-PRECISION 规避）。
  * 终止性按构造证明：
      - for (i in N)：N ∈ [0, 6]，且嵌套迭代乘积 ≤ ITER_BUDGET；
      - loop (w > C)：w 是模板独占变量，体内第一条必为 `w = w - 1;`，
        初值 = C + 轮数 → 恰好停机；
      - 递归函数只允许「护栏模板」：if (p0 > G) { return base; }
        return f(p0 + step) + step; → 深度 ≤ G 有界。
  * 已知定价分歧由生成约束规避（known_divergences.py 白名单同步文档）：
      - KNOWN-MOD-SIGN：% 只在「被除数区间 ≥ 0 且除数区间 > 0」上生成；
      - KNOWN-LOGIC-NUMERIC：&&/|| 操作数只生成 bool 型；
      - KNOWN-GLOBAL-SCOPE：函数体只可见形参/局部（两运行时对
        「函数读写全局」语义相反，已有 T-15 专钉）；
      - 字典声明语法（`d = &<...>&;`）只在顶层生成（函数内的名字解析
        属于 KNOWN-GLOBAL-SCOPE 灰区）。
  * 确定性：整棵程序由 (seed, opts) 唯一决定 → 分歧最小化 = 报告 seed。

用法：
    from gen_program import GenOptions, generate_program
    src, info = generate_program(12345)
"""
from __future__ import annotations

import os
import random

NUM_LIMIT = 1.0e9          # 数值上界（远小于 2^53）
ITER_BUDGET = 2000         # 嵌套循环迭代乘积预算（控制运行时长）
OUT_BUDGET = 240           # 输出行数预算
MAX_CALL_INTERVAL = (-1.0e6, 1.0e6)   # 未精确追踪的调用结果保守区间

_NUM_CONSTS = [0, 1, 2, 3, 5, 7, 10, 100, -1, -2, -3, -5, -10,
               9007199254740991, 123456789]   # 2^53-1 / 大量级（< 2^53 精确区）
# 字符串原子必须是「非数字样式」：数字样式的字面量（"9"、"-3.5"）会被两侧
# 编译/解释层转成数字（D-09），随后 interp 的 Python + 直接 TypeError。
_STR_ATOMS = ['a', 'b', 'xy', 'qot', 'zz', 'hello', 'x1', 'ok', 'm']
# 不含 ''：空串字面量在 VM 常量装载层就是 null（D-10 / KNOWN-EMPTY-STR）
_DICT_KEYS = ['k1', 'k2', 'k3', 'aa', 'bb']
_TYPE_SPEC = {'number': 'number_t', 'string': 'string_t', 'bool': 'boolean_t'}


class Sym(object):
    """符号表项。kind: number|bool|string|array|dict。

    is_unary（number）：值来自一元负号链 → 语义分析器把此类变量静态定型为
    any，再取负会打警告（KNOWN-UNARY-NEST）→ 污染传播，禁止再取负。
    num_tainted（string）：值内嵌数字来源（strings()/含数字拼接/函数返回）→
    两侧格式不同（README §21），拼进输出行后 token 对齐失效 → 禁止再拼接
    （单独 output 仍允许，独立 token 能对齐）。
    """
    __slots__ = ('name', 'kind', 'lo', 'hi', 'is_int', 'elem_kind',
                 'elem_lo', 'elem_hi', 'length', 'min_len', 'owned_by',
                 'is_unary', 'num_tainted', 'from_concat')

    def __init__(self, name, kind, lo=0.0, hi=0.0, is_int=False,
                 elem_kind=None, elem_lo=0.0, elem_hi=0.0,
                 length=0, min_len=0, owned_by=None, is_unary=False,
                 num_tainted=False, from_concat=False):
        self.name = name
        self.kind = kind
        self.lo = float(lo)
        self.hi = float(hi)
        self.is_int = bool(is_int)
        self.elem_kind = elem_kind
        self.elem_lo = float(elem_lo)
        self.elem_hi = float(elem_hi)
        self.length = int(length)
        self.min_len = int(min_len)
        self.owned_by = owned_by
        self.is_unary = bool(is_unary)
        self.num_tainted = bool(num_tainted)
        self.from_concat = bool(from_concat)

    def clone(self, **kw):
        s = Sym(self.name, self.kind, self.lo, self.hi, self.is_int,
                self.elem_kind, self.elem_lo, self.elem_hi,
                self.length, self.min_len, self.owned_by, self.is_unary,
                self.num_tainted, self.from_concat)
        for k, v in kw.items():
            setattr(s, k, v)
        return s


class GenOptions(object):
    """生成参数。不同 opts 组合打不同侧面。"""
    def __init__(self, max_depth=None, n_funcs=None, n_main=None,
                 allow_arrays=True, allow_dicts=True, allow_funcs=True,
                 allow_recursion=True, allow_stop=True):
        self.max_depth = max_depth      # None → 随机 3..8
        self.n_funcs = n_funcs          # None → 随机 0..3
        self.n_main = n_main            # None → 随机 6..16
        self.allow_arrays = allow_arrays
        self.allow_dicts = allow_dicts
        self.allow_funcs = allow_funcs
        self.allow_recursion = allow_recursion
        self.allow_stop = allow_stop


class _Scope(object):
    def __init__(self, parent=None):
        self.parent = parent
        self.syms = {}

    def lookup(self, name):
        s = self
        while s is not None:
            if name in s.syms:
                return s.syms[name]
            s = s.parent
        return None


class GenStats(object):
    def __init__(self):
        self.stmts = 0
        self.depth_max = 0
        self.outputs = 0


class _Gen(object):
    def __init__(self, seed, opts):
        self.rng = random.Random(seed)
        self.opts = opts
        self.seed = seed
        self.lines = []
        self.tmp_n = 0
        # fn_sigs: name -> {'ret': kind, 'params': [(kind, lo, hi)], 'ret_iv': (lo,hi),
        #                  'guarded': bool}
        self.fn_sigs = {}
        self.iter_prod = 1          # 嵌套循环迭代乘积（停机/时长预算）
        self.out_left = OUT_BUDGET
        self.indent = 0
        self.loop_syms = []         # 栈：[(变量名, Sym)]，_safe_index 动态校验区间
        self.stats = GenStats()
        self.cur_fn_ret = None      # 当前函数的返回区间收集 [lo, hi] or None
        self.cur_fn_name = None     # 当前正在生成的函数名（禁自调用）
        self.in_func = False
        self.dict_keys = {}         # dict 变量名 -> set(已键)
        self.for_iter_arrs = []     # 栈：正在被 for-in 遍历的数组名（禁 push/写）
        self.loop_frozen = []       # 栈：循环入口的可见符号快照（循环体赋值不得
                                    # 收缩/污染入口约束——循环体会重复执行，
                                    # 线性记账在第二轮起失真，20261045/80 实证）
        self.branch_frames = []     # 栈：条件分支的赋值暂存帧。分支内赋值运行期
                                    # 可能不执行 → 出分支时按 pre ∪ final 并集
                                    # 合并回家作用域（777148 实证：eif 内
                                    # v1="x1" 未运行，外层仍按干净放行长度位）

    @staticmethod
    def _merge_union(pre, new):
        """条件分支赋值的保守并集：运行期变量可能是 pre 或 new 任一状态。"""
        if pre is None:
            return new
        if pre.kind == 'number' and new.kind == 'number':
            return pre.clone(lo=min(pre.lo, new.lo), hi=max(pre.hi, new.hi),
                             is_int=pre.is_int and new.is_int,
                             is_unary=pre.is_unary or new.is_unary)
        if pre.kind == 'string' and new.kind == 'string':
            return pre.clone(length=max(pre.length, new.length),
                             min_len=min(pre.min_len, new.min_len),
                             num_tainted=pre.num_tainted or new.num_tainted,
                             from_concat=pre.from_concat or new.from_concat)
        return pre

    def _freeze_check(self, scope, name, new_sym):
        """循环体内的赋值必须保持入口约束（区间不外扩、串不缩短/不新引入污染）。

        没有循环包裹时线性记账即真相，直接放行。"""
        if not self.loop_frozen:
            return True
        for snap in self.loop_frozen:
            f = snap.get(name)
            if f is None:
                continue
            if f.kind == 'number' and new_sym.kind == 'number':
                if new_sym.lo < f.lo or new_sym.hi > f.hi:
                    return False
            elif f.kind == 'string' and new_sym.kind == 'string':
                if (new_sym.min_len < f.min_len
                        or new_sym.num_tainted != f.num_tainted
                        or new_sym.from_concat != f.from_concat):
                    return False
        return True

    @property
    def loop_names(self):
        return set(n for n, _ in self.loop_syms)

    # ------------------------------------------------------------ 基础
    def emit(self, s):
        self.lines.append('  ' * self.indent + s)

    def fresh(self, hint):
        self.tmp_n += 1
        return '%s%d' % (hint, self.tmp_n)

    def pick_boundary_const(self):
        """边界值优先：0 / ±1 / 小数（§3.1 要求边界值覆盖）。"""
        r = self.rng.random()
        if r < 0.45:
            return self.rng.choice([0, 1, -1, 2, -2])
        if r < 0.75:
            return self.rng.choice([0.5, -0.5, 0.1, 0.25, 2.0, 10.0])
        return self.rng.choice(_NUM_CONSTS)

    # ------------------------------------------------------------ 表达式
    # 返回 (text, Sym)；失败返回 None（调用方换候选/重试）。
    def expr(self, scope, kind, depth):
        if kind not in ('number', 'bool', 'string'):
            return None
        if depth <= 0:
            return self.expr_leaf(scope, kind)
        cands = {
            'number': [self.ex_binop, self.ex_unary, self.ex_call,
                       self.ex_iter_len, self.ex_index_num, self.expr_leaf],
            'bool': [self.ex_compare, self.ex_logic, self.ex_call,
                     self.ex_is_number, self.expr_leaf],
            'string': [self.ex_concat, self.ex_strings, self.ex_index_str,
                       self.ex_call, self.expr_leaf],
        }[kind]
        order = list(cands)
        self.rng.shuffle(order)
        for c in order:
            for _ in range(6):
                r = c(scope, kind, depth)
                if r is not None:
                    return r
        return None

    def expr_leaf(self, scope, kind, depth=0):
        r = self.rng.random()
        if kind == 'number':
            if r < 0.55:
                v = self.pick_boundary_const()
                return (repr(v), Sym('#c', 'number', v, v, isinstance(v, int)))
            cands = self._visible(scope, 'number')
            if cands and r < 0.9:
                s = self.rng.choice(cands)
                return (s.name, s)
            v = round(self.rng.uniform(-100, 100), 3)
            return (repr(v), Sym('#c', 'number', v, v, False))
        if kind == 'bool':
            if r < 0.6:
                return ('true' if self.rng.random() < 0.5 else 'false',
                        Sym('#c', 'bool'))
            # 布尔变量若是不透明值（is_unary：形参/归纳变量）不得作条件/操作数
            cands = [s for s in self._visible(scope, 'bool') if not s.is_unary]
            if cands:
                return (self.rng.choice(cands).name, Sym('#v', 'bool'))
            return ('true', Sym('#c', 'bool'))
        if kind == 'string':
            if r < 0.6:
                t = self.rng.choice(_STR_ATOMS)
                return ('"%s"' % t,
                        Sym('#c', 'string', length=len(t), min_len=len(t)))
            cands = self._visible(scope, 'string')
            if cands:
                s = self.rng.choice(cands)
                return (s.name, s)
            return ('"q"', Sym('#c', 'string', length=1, min_len=1))
        return None

    def _visible(self, scope, kind, exclude_owned=False):
        """可见符号（按 kind 过滤）。【最近绑定优先】——同名内层声明遮蔽外层，
        属性判定（污染/区间/长度）必须以内层为准：运行期名字解析就是最近绑定，
        收集到被遮蔽的外层副本会让过滤形同虚设（777157 实证：外层干净 v6
        通过了污染过滤，运行期解析到内层被 strings() 污染的 v6）。"""
        out = []
        seen = set()
        s = scope
        owned = self.loop_names if exclude_owned else set()
        while s is not None:
            for name, sym in s.syms.items():
                if name in seen:
                    continue
                seen.add(name)
                if sym.kind == kind and name not in owned:
                    out.append(sym)
            s = s.parent
        return out

    def _num(self, scope, depth):
        return self.expr(scope, 'number', depth)

    def ex_binop(self, scope, kind, depth):
        op = self.rng.choice(['+', '-', '*', '/', '%', '^'])
        if op == '^':
            return self._ex_pow(scope, depth)
        a = self._num(scope, depth - 1)
        b = self._num(scope, depth - 1)
        if a is None or b is None:
            return None
        # 下标结果等不透明值（is_unary）参与算术 → 分析器「unknown 类型」警告
        if a[1].is_unary or b[1].is_unary:
            return None
        ta, sa, tb, sb = a[0], a[1], b[0], b[1]
        if op == '%':
            # KNOWN-MOD-SIGN 规避：被除数区间 ≥ 0，除数区间 > 0（下界 > 0）
            if sa.lo < 0 or sb.lo <= 0:
                return None
            lo, hi = 0.0, max(sb.hi, 0.0)
            is_int = sa.is_int and sb.is_int
        elif op == '/':
            if sb.lo <= 0.0 <= sb.hi:
                return None  # 除数区间含 0
            corners = [x / y for x in (sa.lo, sa.hi) for y in (sb.lo, sb.hi) if y != 0]
            lo, hi = min(corners), max(corners)
            is_int = False
        else:
            if op == '+':
                lo, hi = sa.lo + sb.lo, sa.hi + sb.hi
            elif op == '-':
                lo, hi = sa.lo - sb.hi, sa.hi - sb.lo
            else:
                c = [sa.lo * sb.lo, sa.lo * sb.hi, sa.hi * sb.lo, sa.hi * sb.hi]
                lo, hi = min(c), max(c)
            is_int = sa.is_int and sb.is_int and op in ('+', '-', '*')
        if abs(lo) > NUM_LIMIT or abs(hi) > NUM_LIMIT:
            return None
        return ('(%s %s %s)' % (ta, op, tb), Sym('#e', 'number', lo, hi, is_int))

    def _ex_pow(self, scope, depth):
        # ^ 右结合；指数强制小非负整数字面量（1e9 界内 + int/double 一致性）
        a = self._num(scope, depth - 1)
        if a is None or a[1].is_unary:
            return None   # 分析器对 '^' 泄漏左操作数类型（不透明左 → 不透明结果）
        ta, sa = a
        e = self.rng.randrange(0, 7)
        corners = [sa.lo ** e, sa.hi ** e]
        if e % 2 == 1 and sa.lo < 0 < sa.hi:
            pass  # 两角已覆盖
        if e == 0:
            corners = [1.0, 1.0]
        lo, hi = min(corners), max(corners)
        if abs(lo) > NUM_LIMIT or abs(hi) > NUM_LIMIT:
            return None
        is_int = sa.is_int
        # 分析器对 '^' 节点直接返回左操作数类型（grammar.py visit_binary_op
        # 尾部 return left_type）→ pow 结果的分析器类型 = 左操作数类型 →
        # 左侧不透明（any）时 pow 结果也不透明（is_unary 传染）
        return ('(%s ^ %d)' % (ta, e),
                Sym('#e', 'number', lo, hi, is_int, is_unary=sa.is_unary))

    def ex_unary(self, scope, kind, depth):
        a = self._num(scope, depth - 1)
        if a is None or a[1].is_unary:
            # 禁止一元负号链：分析器对 -x 及循环归纳变量一律定型 any，
            # 再取负/嵌套必触发「一元负号不能用于 any 类型」警告
            return None
        ta, sa = a
        if ta.lstrip('() ').startswith('-'):
            return None   # 负字面量/负文本：-( -37.433 ) 同样触发分析器 any 链
        # 操作数必须加括号：`(- -66.89)` 会被词法器读成 '--'（T-14 负例）
        return ('(-(%s))' % ta, sa.clone(name='#e', lo=-sa.hi, hi=-sa.lo,
                                         is_unary=True))

    def ex_compare(self, scope, kind, depth):
        op = self.rng.choice(['>', '<', '>=', '<=', '==', '!='])
        a = self._num(scope, depth - 1)
        b = self._num(scope, depth - 1)
        if a is None or b is None:
            return None
        # 分析器不透明值（is_unary：形参/归纳变量/下标/调用结果）作比较会触发
        # 「比较运算符左/右边必须是数字类型，实际是 any/array_known」警告
        if a[1].is_unary or b[1].is_unary:
            return None
        return ('(%s %s %s)' % (a[0], op, b[0]), Sym('#e', 'bool'))

    def ex_logic(self, scope, kind, depth):
        # KNOWN-LOGIC-NUMERIC 规避：操作数只生成 bool 型
        op = self.rng.choice(['&&', '||'])
        a = self.expr(scope, 'bool', depth - 1)
        b = self.expr(scope, 'bool', depth - 1)
        if a is None or b is None:
            return None
        return ('(%s %s %s)' % (a[0], op, b[0]), Sym('#e', 'bool'))

    def ex_is_number(self, scope, kind, depth):
        a = self._num(scope, depth - 1)
        if a is None:
            return None
        return ('__is_number(%s)' % a[0], Sym('#e', 'bool'))

    def ex_concat(self, scope, kind, depth):
        a = self.expr(scope, 'string', depth - 1)
        b = self.expr(scope, 'string', depth - 1)
        if a is None or b is None:
            return None
        # 三个禁用条件（均为已实证的分析器/比较器雷区）：
        #  1. 含数字来源的串不得拼接：interp "0" vs VM "0.000000" 嵌进行内后
        #     token 流结构性不可对齐（"hello0xy" vs "hello0.000000xy"）
        #  2. 嵌套拼接（from_concat）：语义分析器把一切二元表达式定型为
        #     number（grammar.py:925），外层 concat 的右操作数=内层 concat →
        #     「字符串和数字不能进行加法运算」警告
        if a[1].num_tainted or b[1].num_tainted:
            return None
        if a[1].from_concat or b[1].from_concat:
            return None
        # 拼接结果长度不再精确追踪 → min_len=0（永不被下标）
        return ('(%s + %s)' % (a[0], b[0]),
                Sym('#e', 'string', from_concat=True))

    def ex_strings(self, scope, kind, depth):
        # 仅 number：strings(布尔) 是已钉分歧（KNOWN-STRINGS-BOOL：'True' vs 'true'）
        a = self._num(scope, depth - 1)
        if a is None:
            return None
        return ('strings(%s)' % a[0], Sym('#e', 'string', num_tainted=True))

    def ex_iter_len(self, scope, kind, depth):
        """glen/__iter_len：number / array / string（dict 是解释器 2010，禁止）。

        number 路径仅限【整数区间】：__iter_len(非整数) 是已钉分歧
        （KNOWN-ITERLEN-FRAC：解释器 int 截断 vs VM 原值透传）。
        """
        _t = os.environ.get('QOT_FUZZ_TRACE')
        if self.rng.random() < 0.4:
            a = self._num(scope, depth - 1)
            if _t:
                print('[iter-len num-path] %r is_int=%s -> %s'
                      % (a[0] if a else None, a[1].is_int if a else None,
                         'EMIT' if a and a[1].is_int else 'reject'), flush=True)
            if a is None or not a[1].is_int:
                return None
            return ('__iter_len(%s)' % a[0],
                    Sym('#e', 'number', 0, max(0.0, a[1].hi), True))
        cands = [s for s in self._visible(scope, 'array')
                 + self._visible(scope, 'string')
                 if not s.num_tainted]
        # num_tainted 串（strings() 产物等）不得进长度位：interp "5" vs VM
        # "5.000000" 会变成 len 1 vs 8 的值分歧（20261029 实证）
        if not cands:
            return None
        s = self.rng.choice(cands)
        fn = self.rng.choice(['glen', '__iter_len'])
        return ('%s(%s)' % (fn, s.name),
                Sym('#e', 'number', s.min_len, s.length, True))

    def ex_index_str(self, scope, kind, depth):
        cands = [s for s in self._visible(scope, 'string')
                 if s.min_len > 0 and not s.num_tainted]
        if not cands:
            return None
        s = self.rng.choice(cands)
        i = self.rng.randrange(0, s.min_len)
        return ('%s[%d]' % (s.name, i),
                Sym('#e', 'string', length=1, min_len=1,
                    num_tainted=s.num_tainted))

    def ex_index_num(self, scope, kind, depth):
        """数组读 a[i]：i 落在安全界 < min_len（越界 = 解释器 2003）。"""
        cands = [s for s in self._visible(scope, 'array')
                 if s.elem_kind == 'number' and s.min_len > 0]
        if not cands:
            return None
        s = self.rng.choice(cands)
        idx_txt = self._safe_index(s)
        if idx_txt is None:
            return None
        return ('%s[%s]' % (s.name, idx_txt),
                Sym('#e', 'number', s.elem_lo, s.elem_hi, False,
                    is_unary=True))   # 下标结果被分析器视为不透明（any/array_known）

    def _safe_index(self, arr):
        """优先：当前 loop 归纳变量（动态校验区间 ⊆ [0, min_len) 且为整型）；
        否则安全常量。任何一步不满足都退回常量，绝不放宽。"""
        for name, sym in reversed(self.loop_syms):
            if sym.is_int and 0 <= sym.lo and sym.hi < arr.min_len:
                return name
        if arr.min_len > 0:
            return repr(self.rng.randrange(0, arr.min_len))
        return None

    def ex_call(self, scope, kind, depth):
        fns = [n for n, sig in self.fn_sigs.items()
               if sig['ret'] == kind and not sig['guarded']
               and n != self.cur_fn_name]   # 禁自调用（未护栏递归 = 解释器
                                            # RecursionError / VM 无界栈，G-8 级）
        if not fns:
            return None
        name = self.rng.choice(fns)
        sig = self.fn_sigs[name]
        args = []
        for pt, plo, phi in sig['params']:
            r = self._call_arg(scope, pt, plo, phi, depth)
            if r is None:
                return None
            args.append(r[0])
        lo, hi = sig.get('ret_iv') or MAX_CALL_INTERVAL
        return ('%s(%s)' % (name, ', '.join(args)),
                Sym('#e', kind, lo, hi, False,
                    num_tainted=(kind == 'string')))   # 函数返回串：来源未知 → 视为含数字

    def _call_arg(self, scope, ptype, plo, phi, depth):
        """实参必须【类型一致】且落在形参声明区间内（区间分析合法性前提）。

        bool/string 的 Sym lo=hi=0，仅查区间会把它们漏成 number——必须先查 kind。
        """
        for _ in range(8):
            # 深度必须递减：实参表达式可再嵌套调用（其参数又是表达式）……
            # 不递减会在「调用→实参→调用」链上无限递归（20261082 实证）
            r = self.expr(scope, ptype, depth - 1)
            if r is None:
                continue
            s = r[1]
            if s.kind != ptype:
                continue
            if ptype == 'string' and s.num_tainted:
                continue   # 污染串不跨函数边界（长度语义在函数内不可追踪）
            if ptype != 'number' or (s.lo >= plo and s.hi <= phi):
                return r
        if ptype == 'number':
            v = min(max(plo, 0.0), phi)
            if plo > 0:
                v = plo
            return (repr(v), Sym('#c', 'number', v, v, v.is_integer()))
        if ptype == 'bool':
            return ('true', Sym('#c', 'bool'))
        if ptype == 'string':
            return ('"a"', Sym('#c', 'string', length=1, min_len=1))
        return None

    # ------------------------------------------------------------ 语句
    def stmts(self, scope, depth, budget, ret_kind=None):
        n = 0
        misses = 0
        while n < budget and misses < 24:
            if self.rng.random() < 0.14 and depth > 0:
                got = self.stmt_ctrl(scope, depth, ret_kind)
            else:
                got = self.stmt_simple(scope, ret_kind)
            if got is None:
                misses += 1
                continue
            n += 1
            self.stats.stmts += 1
        return n

    def stmt_simple(self, scope, ret_kind=None):
        r = self.rng.random()
        if self.in_func and ret_kind is not None and r < 0.18:
            return self.st_return(scope, ret_kind)
        if r < 0.30:
            return self.st_output(scope)
        if r < 0.44:
            return self.st_decl(scope)
        if r < 0.56:
            return self.st_assign(scope)
        if r < 0.64:
            return self.st_compound(scope)
        if r < 0.71 and self.opts.allow_arrays:
            return self.st_push(scope)
        if r < 0.78 and self.opts.allow_dicts and not self.in_func:
            return self.st_dict(scope)
        if r < 0.86:
            return self.st_call_stmt(scope)
        if self.opts.allow_arrays:
            return self.st_arr_write(scope)
        return self.st_output(scope)

    def st_output(self, scope):
        if self.out_left <= self.iter_prod:
            return None
        kind = self.rng.choice(['number', 'bool', 'string', 'bool', 'number'])
        e = self.expr(scope, kind, 2)
        if e is None:
            return None
        self.out_left -= self.iter_prod
        self.stats.outputs += self.iter_prod
        self.emit('output(%s);' % e[0])
        return True

    def st_decl(self, scope):
        kind = self.rng.choice(['number', 'number', 'bool', 'string'])
        if self.opts.allow_arrays and self.rng.random() < 0.22:
            kind = 'array'
        if (self.opts.allow_dicts and self.rng.random() < 0.12
                and not self.in_func):
            kind = 'dict'
        name = self.fresh('v')
        if kind == 'array':
            return self._decl_array(scope, name)
        if kind == 'dict':
            return self._decl_dict(scope, name)
        e = self.expr(scope, kind, 2)
        if e is None:
            return None
        s = e[1]
        if kind == 'number':
            scope.syms[name] = Sym(name, 'number', s.lo, s.hi, s.is_int,
                                   is_unary=s.is_unary)
        elif kind == 'string':
            scope.syms[name] = Sym(name, 'string', length=s.length,
                                   min_len=s.min_len,
                                   num_tainted=s.num_tainted,
                                   from_concat=s.from_concat)
        else:
            scope.syms[name] = Sym(name, kind)
        self.emit('var %s = %s;' % (name, e[0]))
        return True

    def _decl_array(self, scope, name):
        n = self.rng.randrange(0, 5)
        ek = 'string' if self.rng.random() < 0.25 else 'number'
        elems = []
        e_lo, e_hi = float('inf'), float('-inf')
        for _ in range(n):
            e = self.expr(scope, ek, 2)
            if e is None:
                return None
            elems.append(e[0])
            if ek == 'number':
                e_lo = min(e_lo, e[1].lo)
                e_hi = max(e_hi, e[1].hi)
        if n == 0:
            e_lo, e_hi = 0.0, 0.0
        # 元素整型性不可靠（文本判断会漏）→ is_int=False（禁止做下标，保守）
        scope.syms[name] = Sym(name, 'array', elem_kind=ek,
                               elem_lo=e_lo, elem_hi=e_hi, length=n, min_len=n)
        self.emit('var %s = [%s];' % (name, ', '.join(elems)))
        return True

    def _decl_dict(self, scope, name):
        n = self.rng.randrange(1, 4)
        keys = self.rng.sample(_DICT_KEYS, n)
        entries = []
        for k in keys:
            e = self.expr(scope, 'number', 2)
            if e is None:
                return None
            entries.append('"%s": %s' % (k, e[0]))
        scope.syms[name] = Sym(name, 'dict')
        self.dict_keys[name] = set(keys)
        self.emit('%s = &< %s >&;' % (name, ', '.join(entries)))
        return True

    def st_assign(self, scope):
        cands = [s for s in self._visible(scope, 'number', exclude_owned=True) +
                 self._visible(scope, 'string', exclude_owned=True) +
                 self._visible(scope, 'bool', exclude_owned=True)]
        if not cands:
            return None
        s = self.rng.choice(cands)
        e = self.expr(scope, s.kind, 2)
        if e is None:
            return None
        ns = e[1]
        if not self._freeze_check(scope, s.name, ns):
            return None
        # 记账必须写到【变量的家作用域】：QOT 无块作用域，分支/循环体内的
        # 赋值运行期持久到外层——记到当前块内层会让兄弟分支和后续代码读到
        # 过期的干净状态（777157 实证：then 内 strings() 污染，外层仍按
        # 干净放行 glen(v6)）。
        home = self._home_scope(scope, s.name)
        if s.kind == 'number':
            stored = s.clone(
                lo=ns.lo, hi=ns.hi, is_int=ns.is_int,
                # is_unary 只增不减：它代表「分析器静态定型为 any」——形参/
                # 归纳变量一旦 any，运行期重赋值不会改变分析器视图
                is_unary=(s.is_unary or ns.is_unary))
        elif s.kind == 'string':
            stored = s.clone(length=ns.length, min_len=ns.min_len,
                             num_tainted=ns.num_tainted,
                             from_concat=ns.from_concat)
        else:
            stored = s
        if self.branch_frames:
            # 条件分支内：当前分支内线性可见（块内影子）+ 出分支时并集合并。
            # 影子写当前块，家作用域暂不动（分支可能没运行）。
            # 帧内同变量多次赋值：保留【首次写的前状态】与【末次写的终态】
            #（setdefault 会丢终态 → 500388 实证 0.1 赋值被 v1=v1 首写覆盖）
            scope.syms[s.name] = stored
            key = (id(home), s.name)
            if key in self.branch_frames[-1]:
                old = self.branch_frames[-1][key]
                self.branch_frames[-1][key] = (home, s.name, old[2], stored)
            else:
                self.branch_frames[-1][key] = (home, s.name, s, stored)
        else:
            home.syms[s.name] = stored
        self.emit('%s = %s;' % (s.name, e[0]))
        return True

    def _home_scope(self, scope, name):
        s = scope
        while s is not None:
            if name in s.syms:
                return s
            s = s.parent
        return scope

    def st_compound(self, scope):
        """v op= e（数字变量；语法糖 = v = v op e，区间同向合并）。
        累加是 QOT 热负载的主流形态（t14/p01），必须有覆盖。
        【G-03 】算子族扩全：+= -= *= /= %=。插桩实证通用表达式
        操作数区间几乎恒跨 0（/ 与 % 的门永不开）→ / 与 % 改为**合成安全
        字面量除数**（[2,9] 整数，远离 0）；% 仍守 KNOWN-MOD-SIGN 规避
        （被除数 ≥0 且除数 >0 才生成）。"""
        cands = self._visible(scope, 'number', exclude_owned=True)
        if not cands:
            return None
        s = self.rng.choice(cands)
        op = self.rng.choice(['+=', '-=', '*=', '/=', '%='])
        if op in ('/=', '%='):
            # 合成安全除数：字面量整数 ∈ [2,9]，区间 [d,d] 不含 0
            div = self.rng.choice([2, 3, 4, 5, 6, 7, 8, 9])
            e_text = str(div)
            ns = s.clone(lo=float(div), hi=float(div), is_int=True, is_unary=False)
        else:
            e = self.expr(scope, 'number', 2)
            if e is None or e[1].is_unary:
                return None   # 不透明值（下标/形参）作 +=/-= 操作数 → 分析器警告
            e_text, ns = e[0], e[1]
        if op == '+=':
            lo, hi = s.lo + ns.lo, s.hi + ns.hi
        elif op == '-=':
            lo, hi = s.lo - ns.hi, s.hi - ns.lo
        elif op == '*=':
            if s.lo <= 0 <= s.hi or ns.lo <= 0 <= ns.hi:
                # 区间跨 0 时乘积区间是四个端点的 min/max
                cand = (s.lo * ns.lo, s.lo * ns.hi, s.hi * ns.lo, s.hi * ns.hi)
                lo, hi = min(cand), max(cand)
            else:
                cand = (s.lo * ns.lo, s.hi * ns.hi)
                lo, hi = min(cand), max(cand)
        elif op == '/=':
            cand = (s.lo / ns.lo, s.lo / ns.hi, s.hi / ns.lo, s.hi / ns.hi)
            lo, hi = min(cand), max(cand)
        else:  # '%='：KNOWN-MOD-SIGN 规避 —— 被除数 ≥0 且除数 >0 才生成
            if s.lo >= 0 and ns.lo > 0:
                lo, hi = 0.0, max(abs(ns.lo), abs(ns.hi)) - 1
            else:
                return None
        if abs(lo) > NUM_LIMIT or abs(hi) > NUM_LIMIT:
            return None
        # 整数性：/= 恒可能出小数；*= 只在双方皆正/皆负区间时保持
        is_int = s.is_int and ns.is_int
        if op == '/=':
            is_int = False
        elif op == '*=' and (s.lo <= 0 <= s.hi or ns.lo <= 0 <= ns.hi):
            is_int = False
        merged = s.clone(lo=lo, hi=hi, is_int=is_int,
                         is_unary=s.is_unary or ns.is_unary)
        if not self._freeze_check(scope, s.name, merged):
            return None
        home = self._home_scope(scope, s.name)   # 同 st_assign：家作用域记账
        if self.branch_frames:
            scope.syms[s.name] = merged
            key = (id(home), s.name)
            if key in self.branch_frames[-1]:
                old = self.branch_frames[-1][key]
                self.branch_frames[-1][key] = (home, s.name, old[2], merged)
            else:
                self.branch_frames[-1][key] = (home, s.name, s, merged)
        else:
            home.syms[s.name] = merged
        self.emit('%s %s %s;' % (s.name, op, e_text))
        return True

    def st_push(self, scope):
        # 被遍历中的数组禁 push：interp 是活列表迭代（越 push 越长 → 无限循环
        # 20261054 实证），VM 快照语义未知 —— 双侧不同风险，一律禁止
        cands = [s for s in self._visible(scope, 'array')
                 if s.name not in self.for_iter_arrs]
        if not cands:
            return None
        arr = self.rng.choice(cands)
        ek = 'string' if arr.elem_kind == 'string' else 'number'
        e = self.expr(scope, ek, 2)
        if e is None:
            return None
        if ek == 'number':
            scope.syms[arr.name] = arr.clone(
                elem_lo=min(arr.elem_lo, e[1].lo),
                elem_hi=max(arr.elem_hi, e[1].hi),
                length=arr.length + 1)
        else:
            scope.syms[arr.name] = arr.clone(length=arr.length + 1)
        self.emit('push(%s, %s);' % (arr.name, e[0]))
        return True

    def st_arr_write(self, scope):
        cands = [s for s in self._visible(scope, 'array')
                 if s.elem_kind == 'number' and s.min_len > 0
                 and s.name not in self.for_iter_arrs]
        if not cands:
            return None
        arr = self.rng.choice(cands)
        idx_txt = self._safe_index(arr)
        if idx_txt is None:
            return None
        e = self.expr(scope, 'number', 2)
        if e is None:
            return None
        scope.syms[arr.name] = arr.clone(
            elem_lo=min(arr.elem_lo, e[1].lo),
            elem_hi=max(arr.elem_hi, e[1].hi))
        self.emit('%s[%s] = %s;' % (arr.name, idx_txt, e[0]))
        return True

    def st_dict(self, scope):
        dicts = self._visible(scope, 'dict')
        if not dicts:
            return None
        d = self.rng.choice(dicts)
        keys = sorted(self.dict_keys.get(d.name, set()))
        if keys and self.rng.random() < 0.5:
            k = self.rng.choice(keys)
            self.emit('output(dict_get(%s, "%s") != null);' % (d.name, k))
        else:
            k = self.fresh('k')
            e = self.expr(scope, 'number', 2)
            if e is None:
                return None
            self.dict_keys.setdefault(d.name, set()).add(k)
            self.emit('dict_set(%s, "%s", %s);' % (d.name, k, e[0]))
            self.emit('output(dict_get(%s, "%s") != null);' % (d.name, k))
            self.out_left -= 1
        return True

    def st_call_stmt(self, scope):
        fns = [n for n, sig in self.fn_sigs.items()
               if not sig['guarded'] and n != self.cur_fn_name]
        if not fns:
            return None
        name = self.rng.choice(fns)
        sig = self.fn_sigs[name]
        args = []
        for pt, plo, phi in sig['params']:
            r = self._call_arg(scope, pt, plo, phi, 2)
            if r is None:
                return None
            args.append(r[0])
        self.emit('output(%s(%s));' % (name, ', '.join(args)))
        if self.iter_prod >= 1:
            self.out_left -= self.iter_prod
        return True

    def st_return(self, scope, ret_kind):
        e = self.expr(scope, ret_kind, 3)
        if e is None:
            e = self.expr_leaf(scope, ret_kind)
            if e is None:
                return None
        self.emit('return %s;' % e[0])
        if ret_kind == 'number' and self.cur_fn_ret is not None:
            self.cur_fn_ret[0] = min(self.cur_fn_ret[0], e[1].lo)
            self.cur_fn_ret[1] = max(self.cur_fn_ret[1], e[1].hi)
        return True

    # ------------------------------------------------------------ 控制流
    def stmt_ctrl(self, scope, depth, ret_kind=None):
        self.stats.depth_max = max(self.stats.depth_max, depth)
        choice = self.rng.choice(['if', 'if', 'for', 'loop'])
        if choice == 'if':
            return self.st_if(scope, depth, ret_kind)
        if choice == 'for':
            return self.st_for(scope, depth, ret_kind)
        return self.st_loop(scope, depth, ret_kind)

    def st_if(self, scope, depth, ret_kind):
        cond = self.expr(scope, 'bool', 3)
        if cond is None:
            return None
        self.branch_frames.append({})   # 分支帧：收集本 if 全体分支的赋值
        try:
            self.emit('if (%s) {' % cond[0])
            inner = _Scope(scope)
            self.indent += 1
            self.stmts(inner, depth - 1, self.rng.randrange(1, 4), ret_kind)
            self.indent -= 1
            if self.rng.random() < 0.35:
                c2 = self.expr(scope, 'bool', 3)
                if c2 is not None:
                    self.emit('} eif (%s) {' % c2[0])
                    inner2 = _Scope(scope)
                    self.indent += 1
                    self.stmts(inner2, depth - 1, self.rng.randrange(1, 3), ret_kind)
                    self.indent -= 1
            if self.rng.random() < 0.55:
                self.emit('} else {')
                inner3 = _Scope(scope)
                self.indent += 1
                self.stmts(inner3, depth - 1, self.rng.randrange(1, 3), ret_kind)
                self.indent -= 1
            self.emit('}')
        finally:
            frame = self.branch_frames.pop()
        # ---- 出分支：pre ∪ final 并集合并回家作用域 ----
        for (home, name, pre, final) in frame.values():
            home.syms[name] = self._merge_union(pre, final)
        return True

    def _snapshot_visible(self, scope):
        snap = {}
        s = scope
        while s is not None:
            for name, sym in s.syms.items():
                if name not in snap:
                    snap[name] = sym
            s = s.parent
        return snap

    def st_for(self, scope, depth, ret_kind):
        """for (i in N)：N = 常量 [0..6] 或遍历已知数组。停机性显然。"""
        budget = self.rng.randrange(0, 7)
        if self.iter_prod * max(1, budget) > ITER_BUDGET:
            return None
        arr_sym = None
        if self.opts.allow_arrays and self.rng.random() < 0.3:
            cands = [s for s in self._visible(scope, 'array') if s.length > 0]
            if cands:
                arr_sym = self.rng.choice(cands)
        name = self.fresh('i')
        if arr_sym is not None:
            self.emit('for (%s in %s) {' % (name, arr_sym.name))
            ek = arr_sym.elem_kind or 'number'
            # 元素整型性未知 → is_int=False（不得做下标）；归纳变量被分析器
            # 定型 any → is_unary=True（禁取负，否则分析器警告）
            iv = Sym(name, ek, arr_sym.elem_lo, arr_sym.elem_hi,
                     False, length=1, min_len=1, is_unary=True)
            iters = max(1, arr_sym.length)
        else:
            self.emit('for (%s in %d) {' % (name, budget))
            iv = Sym(name, 'number', 0, max(0, budget - 1), True, is_unary=True)
            iters = max(1, budget)
        self.iter_prod *= iters
        inner = _Scope(scope)
        inner.syms[name] = iv
        self.indent += 1
        self.loop_syms.append((name, iv))
        self.loop_frozen.append(self._snapshot_visible(scope))
        if arr_sym is not None:
            self.for_iter_arrs.append(arr_sym.name)
        if (self.opts.allow_stop and not self.in_func
                and budget >= 2 and self.rng.random() < 0.2):
            self.emit('if (%s == %d) { stop; }' % (name, self.rng.randrange(0, budget)))
        self.stmts(inner, depth - 1, self.rng.randrange(1, 4), ret_kind)
        if arr_sym is not None:
            self.for_iter_arrs.pop()
        self.loop_syms.pop()
        self.loop_frozen.pop()
        self.indent -= 1
        self.iter_prod //= iters
        self.emit('}')
        return True

    def st_loop(self, scope, depth, ret_kind):
        """loop (w > C) { w = w - 1; ... } —— 停机证明模板（恰好 d0 轮）。"""
        if self.iter_prod * 5 > ITER_BUDGET:
            return None
        d0 = self.rng.randrange(1, 6)
        c = self.rng.randrange(0, 3)
        v0 = d0 + c
        name = self.fresh('w')
        self.emit('var %s = %d;' % (name, v0))
        self.emit('loop (%s > %d) {' % (name, c))
        # 体内区间是【减 1 之后】：w ∈ [c, v0-1]。若按入口区间 [c+1, v0] 记账，
        # `w % w` 会在 w=c=0 时变成 0%0（解释器 ZeroDivisionError）——区间必须
        # 如实反映「体内在减 1 之后」的事实，供 % / 下标安全判定使用。
        # is_unary=True：loop 变量同样被分析器定型 any，禁取负。
        iv = Sym(name, 'number', c, v0 - 1, True, is_unary=True)
        inner = _Scope(scope)
        inner.syms[name] = iv
        self.iter_prod *= d0
        self.loop_syms.append((name, iv))
        self.loop_frozen.append(self._snapshot_visible(scope))
        self.indent += 1
        self.emit('%s = %s - 1;' % (name, name))   # 停机证明语句（独占写）
        # stop 只在【非函数体】内生成：函数内 stop 两侧语义相反
        #（interp 的 LoopStopException 会穿透函数调用帧，VM 按 BREAK 出函数内循环）
        if (self.opts.allow_stop and not self.in_func and d0 >= 2
                and self.rng.random() < 0.2):
            self.emit('if (%s == %d) { stop; }' % (name, c))
        self.stmts(inner, depth - 1, self.rng.randrange(1, 4), ret_kind)
        self.loop_syms.pop()
        self.loop_frozen.pop()
        self.indent -= 1
        self.iter_prod //= d0
        self.emit('}')
        return True

    # ------------------------------------------------------------ 顶层
    def program(self):
        parts = []
        n_funcs = self.opts.n_funcs
        if n_funcs is None:
            n_funcs = self.rng.randrange(0, 4) if self.opts.allow_funcs else 0
        for fi in range(n_funcs):
            parts.append(self._gen_function(fi))
        main_scope = _Scope()
        n_main = self.opts.n_main
        if n_main is None:
            n_main = self.rng.randrange(6, 17)
        main_lines = []
        real_emit = self.emit
        self.emit = main_lines.append
        try:
            self.stmts(main_scope, self._depth(), n_main, None)
        finally:
            self.emit = real_emit
        parts.extend(main_lines)
        return '\n'.join(p for p in parts if p) + '\n'

    def _depth(self):
        return self.opts.max_depth or self.rng.randrange(3, 9)

    def _gen_function(self, fi):
        name = 'fn%d' % fi
        ret_kind = self.rng.choice(['number', 'number', 'bool', 'string'])
        nparams = self.rng.randrange(0, 3)
        # 形参声明区间（函数体区间分析的基準）；调用方实参被约束落在其中
        params = []
        for _ in range(nparams):
            k = self.rng.choice(['number', 'number', 'bool', 'string'])
            if k == 'number':
                params.append((k, -10.0, 100.0))
            else:
                params.append((k, 0.0, 0.0))
        guarded = (self.opts.allow_recursion and ret_kind == 'number'
                   and params and params[0][0] == 'number'
                   and self.rng.random() < 0.3)
        if guarded:
            # 护栏模板：p0 ∈ [0,5]、G ∈ [2,5]、步长 ≥1 → 递归深度 ≤ 6 有界
            params = [('number', 0.0, 5.0)] + params[1:]
        tspec = _TYPE_SPEC[ret_kind]
        plist = ['p%d' % i for i in range(len(params))]
        self.fn_sigs[name] = {'ret': ret_kind, 'params': params,
                              'ret_iv': None, 'guarded': guarded}
        fscope = _Scope()
        for i, (pt, plo, phi) in enumerate(params):
            pn = 'p%d' % i
            if pt == 'number':
                # QOT 形参无类型声明 → 分析器定型 any → 形参禁取负/禁比较（is_unary）
                fscope.syms[pn] = Sym(pn, 'number', plo, phi,
                                      is_int=guarded and i == 0, is_unary=True)
            elif pt == 'string':
                # min_len=0：实参长度不定 → 形参禁止下标访问
                fscope.syms[pn] = Sym(pn, 'string', length=0, min_len=0,
                                      num_tainted=True)
            else:
                # 布尔形参同样是分析器 any → 禁作条件/逻辑操作数（is_unary）
                fscope.syms[pn] = Sym(pn, 'bool', is_unary=True)
        body = []
        real_emit = self.emit
        self.emit = body.append
        saved_in_func, saved_ret = self.in_func, self.cur_fn_ret
        saved_fn_name = self.cur_fn_name
        saved_dk = self.dict_keys
        self.in_func = True
        self.cur_fn_name = name
        self.cur_fn_ret = [float('inf'), float('-inf')] if ret_kind == 'number' else None
        self.dict_keys = {}          # 函数内不开新顶层 dict（作用域灰区禁入）
        try:
            self.indent = 1
            if guarded:
                g = self.rng.randrange(2, 6)
                step = self.rng.choice([1, 2, 10])
                base = self.rng.choice([0, 1, 10])
                self.emit('if (p0 > %d) { return %d; }' % (g, base))
                self.emit('return %s(p0 + %d) + %d;' % (name, step, step))
                self.fn_sigs[name]['ret_iv'] = (float(base), float(base + (g + 1) * step + step))
            else:
                self.stmts(fscope, min(3, self.opts.max_depth or 5),
                           self.rng.randrange(2, 6), ret_kind)
                # 默认返回 0 必须计入返回区间：它运行期可达（前面的 return
                # 可能全在死分支里），漏记会让调用方按「正区间」生成
                # `x % fn()` 而运行期除零（777079 实证）
                if ret_kind == 'number' and self.cur_fn_ret:
                    self.cur_fn_ret[0] = min(self.cur_fn_ret[0], 0.0)
                    self.cur_fn_ret[1] = max(self.cur_fn_ret[1], 0.0)
                self.emit('return %s;' % {'number': '0', 'bool': 'true',
                                          'string': '"r"'}[ret_kind])
                if ret_kind == 'number' and self.cur_fn_ret and \
                        self.cur_fn_ret[0] <= self.cur_fn_ret[1]:
                    self.fn_sigs[name]['ret_iv'] = tuple(self.cur_fn_ret)
            self.indent = 0
            self.emit('}')
        finally:
            self.emit = real_emit
            self.in_func, self.cur_fn_ret = saved_in_func, saved_ret
            self.cur_fn_name = saved_fn_name
            self.dict_keys = saved_dk
        head = '%s func %s(%s) {' % (tspec, name, ', '.join(plist))
        return head + '\n' + '\n'.join(body)


def generate_program(seed, opts=None):
    """seed → (source_text, info_dict)。同一 (seed, opts) 恒等复现。"""
    opts = opts or GenOptions()
    for attempt in range(4):
        g = _Gen(seed * 1000 + attempt, opts)
        src = g.program()
        if src.strip() and g.out_left < OUT_BUDGET:
            return src, {'seed': seed, 'gen_seed': seed * 1000 + attempt,
                         'stmts': g.stats.stmts, 'outputs': g.stats.outputs,
                         'depth_max': g.stats.depth_max,
                         'lines': src.count('\n')}
    raise GenError('生成失败 seed=%r（连续 4 次产出空程序/无输出）' % seed)
