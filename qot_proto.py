# -*- coding: utf-8 -*-
"""GOD-SELF-G1 · 协议元语开放（dunder 面）—— 编译期静态绑定器。

立法出处： §8.24-B（验收定死 · 0）。

协议方法集 **11 个，不许增删**（追加留触发）：

    __add__  __sub__  __mul__  __div__  __mod__  __eq__
    __index_get  __index_set  __for_keys  __iter_len  __to_str

绑定语义（性能显式，两条路写死）：
  * **类型已知**（构造点推断，见下）→ 编译期**静态绑定**到协议方法：
      a + b        → a->__add__(b)        （- * / % 同族：__sub__/__mul__/__div__/__mod__）
      a == b       → a->__eq__(b)
      a[i]         → a->__index_get(i)
      a[i] = v     → a->__index_set(i, v)
      for k in obj → obj->__for_keys()    （键协议，优先）
                   → 序列协议：__iter_len()（+ __index_get 取元素，
                     缺 __index_get 则数字迭代 0..len-1）
      output(obj) / 拼接 → obj->__to_str()（output 参数与 `+` 两侧；
                     __add__ 存在时 + 走 __add__，__to_str 只兜拼接）
    绑定产物 = 普通 method_call 节点 —— interp 与 native 共用同一份改写后
    AST（挂点 index._prepare_ast），**零分歧由构造成立**；native 侧产物 =
    既有 CALL_OMETHOD 直调（qotrt_types[tid].mfns[mid]），无运行时查找表。
  * **类型未知**（推断不出来）→ 运行期现行为不变，不建动态查找表
    （要优化请让类型可推断 —— 开发者自己决定性能，GOD-SELF 精神）。
  * 协议方法不存在 → **既有静默定价**（越界 null / 输出 tostring / 对象
    相等 = 同一性），不报错 —— 本 pass 对未定义协议方法的类**完全不改写**。

类型推断源（§8.24-E1：「构造点推断」路线；推断不了的按类型未知走，
**不许**为绑定率加运行时机制）：
  1. 构造点：`var v = C(...)` / `v = C(...)` → v : C（构造调用 = 类名调用）；
  2. 传递：标识符赋值链 / 括号 / 三元同型分支；
  3. 字段：类字段初始化器 + 方法体内 `this->f = <构造调用>` → 字段类型；
  4. 方法体内 `this` : 本类。
  不追踪：函数返回值（type_specifier 无类类型）、容器元素、lambda 体、
  跨函数全局传播（函数体用独立保守作用域）。

卫生条款（§8.24-A）：推断是**静态乐观**的 —— 声明后把变量改成别的类型
属于开发者自责（两引擎拿到同一份改写，行为仍然一致，只是值面由开发者
负责）。`__gs_` 前缀为本 pass 隐藏临时名保留。
"""
import os

from error_system import ErrorCode

# 协议表（11 个，不许增删）：运算符 op → 方法名
ARITH_DUNDER = {
    '+': '__add__',
    '-': '__sub__',
    '*': '__mul__',
    '/': '__div__',
    '%': '__mod__',
    '==': '__eq__',
}
# 「返回接收者类型」推断的子集：五则按惯例返回同型（Vec2+Vec2→Vec2）；
# __eq__ 返回布尔，不在其列。
SAME_TYPE_DUNDERS = {'__add__', '__sub__', '__mul__', '__div__', '__mod__'}
DUNDER_ALL = set(ARITH_DUNDER.values()) | {
    '__index_get', '__index_set', '__for_keys', '__iter_len', '__to_str',
}

# 这些二目运算不参与协议分派（既有定价不变）
_NON_PROTO_OPS = {'^', '>', '<', '>=', '<=', '!=', 'AND', '&&', 'OR', '||'}


class _ClassInfo:
    __slots__ = ('name', 'fields', 'methods', 'field_types')

    def __init__(self, name):
        self.name = name
        self.fields = []          # [fname]
        self.methods = set()      # {mname}
        self.field_types = {}     # fname -> 类名（可推断面）

    def has(self, mname):
        return mname in self.methods


class _Ctx:
    def __init__(self, classes, with_line):
        self.classes = classes
        self.with_line = with_line
        self._tmp = 0

    def node(self, kind, **fields):
        out = (kind,) + tuple(fields[k] for k in _node_names(kind))
        if self.with_line:
            out = out + (0,)
        return out

    def tmp(self, stem):
        self._tmp += 1
        return '__gs_%s_%d' % (stem, self._tmp)


def _node_names(kind):
    from qot_macro import NODE_FIELDS
    return NODE_FIELDS[kind]


def _collect_classes(ast):
    """顶层 class_declaration → {类名: _ClassInfo}（类仅限顶层，sema 立法）。"""
    classes = {}
    stmts = None
    if isinstance(ast, tuple) and ast and ast[0] == 'start':
        stmts = ast[1]
    elif isinstance(ast, list):
        stmts = ast
    for stmt in (stmts or []):
        if not (isinstance(stmt, tuple) and stmt and stmt[0] == 'class_declaration'):
            continue
        cname = stmt[1]
        info = _ClassInfo(cname)
        for m in (stmt[2] or []):
            if not (isinstance(m, tuple) and m):
                continue
            if m[0] == 'field':
                info.fields.append(m[1])
            elif m[0] == 'method' and len(m) >= 5:
                info.methods.add(m[2])
        classes[cname] = info
    return classes


def _any_dunder(classes):
    for info in classes.values():
        if info.methods & DUNDER_ALL:
            return True
    return False


def bind_protocols(ast, filename=''):
    """入口：宏展开后调用。返回改写后 ast；致命 → None（当前无致命面，
    哨兵形态与 expand_program 对齐）。快路径：没有任何类带协议方法 →
    原样返回（零改写零风险，既有用例的默认形态）。"""
    classes = _collect_classes(ast)
    if not classes or not _any_dunder(classes):
        return ast
    ctx = _Ctx(classes, _detect_with_line(ast))
    # 字段类型预收集（声明序初始化器；含构造调用）
    for stmt in (_top_statements(ast) or []):
        if isinstance(stmt, tuple) and stmt and stmt[0] == 'class_declaration':
            _collect_field_inits(ctx, stmt[1], stmt[2] or [])
    scope = {}
    stmts = _top_statements(ast)
    if stmts is None:
        return ast
    # 两遍制：先扫各方法体的 this->f = <构造> 记字段类型，再统一改写
    for stmt in (stmts or []):
        if isinstance(stmt, tuple) and stmt and stmt[0] == 'class_declaration':
            _scan_method_field_writes(ctx, stmt[1], stmt[2] or [])
    out = []
    for stmt in (stmts or []):
        got = _rw_stmt(ctx, stmt, scope)
        if isinstance(got, list):
            out.extend(got)
        else:
            out.append(got)
    if isinstance(ast, tuple) and ast and ast[0] == 'start':
        ast = (ast[0], out) + ast[2:]
    else:
        ast = out
    return ast


def _top_statements(ast):
    if isinstance(ast, tuple) and ast and ast[0] == 'start':
        return ast[1]
    if isinstance(ast, list):
        return ast
    return None


def _detect_with_line(ast):
    if isinstance(ast, tuple) and len(ast) > 2 and ast[0] == 'start':
        return isinstance(ast[-1], int) and not isinstance(ast[-1], bool)
    return True


# ---------------------------------------------------------------------------
# 字段类型收集（两遍制的第一遍）
# ---------------------------------------------------------------------------

def _collect_field_inits(ctx, cname, members):
    info = ctx.classes.get(cname)
    if info is None:
        return
    for m in members:
        if isinstance(m, tuple) and m and m[0] == 'field' \
                and len(m) > 2 and m[2] is not None:
            t = _static_type_of(ctx, m[2])
            if t:
                info.field_types[m[1]] = t


def _scan_method_field_writes(ctx, cname, members):
    """方法体内 `this->f = <expr>` 的构造调用面 → 字段类型（不改写）。"""
    info = ctx.classes.get(cname)
    if info is None:
        return
    for m in members:
        if not (isinstance(m, tuple) and m and m[0] == 'method' and len(m) >= 5):
            continue
        body = m[4]
        scope = {'this': cname}
        for p in (m[3] or []):
            scope[p] = None
        _scan_field_writes_stmts(ctx, body, scope, info)


def _scan_field_writes_stmts(ctx, stmts, scope, info):
    if not isinstance(stmts, list):
        return
    for st in stmts:
        if isinstance(st, tuple) and st:
            k = st[0]
            if k == 'field_assignment' and len(st) >= 4:
                obj = st[1]
                if isinstance(obj, tuple) and obj and obj[0] == 'identifier' \
                        and obj[1] == 'this':
                    t = _static_type_of(ctx, st[3])
                    if t:
                        info.field_types[st[2]] = t
            elif k in ('if_statement', 'while_statement', 'for_statement'):
                for part in st[2:-1]:
                    _scan_field_writes_stmts(ctx, part, scope, info)
            elif k in ('func_declaration', 'try_statement'):
                for part in st[2:-1]:
                    if isinstance(part, list):
                        _scan_field_writes_stmts(ctx, part, scope, info)


def _static_type_of(ctx, expr):
    """不改写的静态类型判定（构造调用面）。"""
    if not isinstance(expr, tuple) or not expr:
        return None
    k = expr[0]
    if k == 'function_call' and expr[1] in ctx.classes:
        return expr[1]
    if k == 'parenthesized':
        return _static_type_of(ctx, expr[1])
    if k == 'literal' or k == 'identifier':
        return None
    return None


# ---------------------------------------------------------------------------
# 语句改写
# ---------------------------------------------------------------------------

def _rw_stmt(ctx, st, scope):
    if not isinstance(st, tuple) or not st:
        return st
    k = st[0]
    line = st[-1] if ctx.with_line and len(st) > 1 else 0

    if k == 'var_declaration':
        val, t = _rw_expr(ctx, st[2], scope)
        scope[st[1]] = t
        return (st[0], st[1], val) + st[3:]
    if k == 'const_declaration':
        val, t = _rw_expr(ctx, st[2], scope)
        scope[st[1]] = t
        return (st[0], st[1], val) + st[3:]
    if k == 'assignment':
        val, t = _rw_expr(ctx, st[2], scope)
        scope[st[1]] = t
        return (st[0], st[1], val) + st[3:]
    if k == 'array_declaration':
        elems = _rw_expr_list(ctx, st[2], scope)
        scope[st[1]] = None
        return (st[0], st[1], elems) + st[3:]
    if k == 'dict_declaration':
        entries = _rw_expr_list(ctx, st[2], scope)
        scope[st[1]] = None
        return (st[0], st[1], entries) + st[3:]
    if k == 'array_assignment':
        return _rw_array_assign(ctx, st, scope)
    if k == 'function_call_statement':
        call, _t = _rw_expr(ctx, st[1], scope)
        return (st[0], call) + st[2:]
    if k == 'method_call_statement':
        call, _t = _rw_expr(ctx, st[1], scope)
        return (st[0], call) + st[2:]
    if k == 'if_statement':
        cond, _t = _rw_expr(ctx, st[1], scope)
        then = _rw_block(ctx, st[2], scope)
        eif = _rw_eif(ctx, st[3], scope)
        els = st[4]
        if els is not None:
            els = (els[0], _rw_block(ctx, els[1], scope)) + els[2:]
        return (k, cond, then, eif, els) + st[5:]
    if k == 'while_statement':
        cond, _t = _rw_expr(ctx, st[1], scope)
        body = _rw_block(ctx, st[2], scope)
        return (k, cond, body) + st[3:]
    if k == 'for_statement':
        return _rw_for(ctx, st, scope)
    if k == 'return_statement':
        if st[1] is None:
            return st
        val, _t = _rw_expr(ctx, st[1], scope)
        return (k, val) + st[2:]
    if k == 'func_declaration':
        fscope = {}
        for p in (st[3] or []):
            fscope[p] = None
        body = _rw_block(ctx, st[4], fscope)
        return (k, st[1], st[2], st[3], body) + st[5:]
    if k == 'class_declaration':
        members = []
        for m in (st[2] or []):
            if isinstance(m, tuple) and m and m[0] == 'method' and len(m) >= 5:
                mscope = {'this': st[1]}
                for p in (m[3] or []):
                    mscope[p] = None
                mbody = _rw_block(ctx, m[4], mscope)
                members.append((m[0], m[1], m[2], m[3], mbody) + m[5:])
            else:
                members.append(m)
        return (k, st[1], members) + st[3:]
    if k == 'throw_statement':
        val, _t = _rw_expr(ctx, st[1], scope)
        return (k, val) + st[2:]
    if k == 'try_statement':
        body = _rw_block(ctx, st[1], scope)
        cbody = _rw_block(ctx, st[3], scope)
        if st[2]:
            scope[st[2]] = None
        return (k, body, st[2], cbody) + st[4:]
    if k == 'field_assignment':
        obj, ot = _rw_expr(ctx, st[1], scope)
        val, vt = _rw_expr(ctx, st[3], scope)
        # 字段类型登记（方法体已预扫；顶层的 obj->f = 构造 在 this 之外
        # 不登记 —— 顶层对象的字段类型只服务本作用域，登记也无害）
        if ot and isinstance(st[1], tuple) and st[1][0] == 'identifier' \
                and st[1][1] == 'this':
            info = ctx.classes.get(ot)
            if info is not None and vt:
                info.field_types[st[2]] = vt
        return (k, obj, st[2], val) + st[4:]
    # 其余语句（require/stop/continue/annotation…）原样
    return st


def _rw_block(ctx, stmts, scope):
    out = []
    for st in (stmts or []):
        got = _rw_stmt(ctx, st, scope)
        if isinstance(got, list):
            out.extend(got)
        else:
            out.append(got)
    return out


def _rw_eif(ctx, eif_blocks, scope):
    if not eif_blocks:
        return eif_blocks
    out = []
    for e in eif_blocks:
        cond, _t = _rw_expr(ctx, e[1], scope)
        block = _rw_block(ctx, e[2], scope)
        out.append((e[0], cond, block) + e[3:])
    return out


def _rw_array_assign(ctx, st, scope):
    """a[i] = v → a->__index_set(i, v)（仅单层下标 + 类型已知 + 协议在）。"""
    target = st[1]
    if not (isinstance(target, tuple) and target and target[0] == 'array_index'):
        return st
    name = target[1]
    info = ctx.classes.get(scope.get(name))
    if info is None or not info.has('__index_set'):
        # 保守：右侧仍走表达式改写（容器值面）
        val, _t = _rw_expr(ctx, st[2], scope)
        return (st[0], target, val) + st[3:]
    idx, _t1 = _rw_expr(ctx, target[2], scope)
    val, _t2 = _rw_expr(ctx, st[2], scope)
    line = st[-1] if ctx.with_line and len(st) > 1 else 0
    recv = ('identifier', name) + ((line,) if ctx.with_line else ())
    call = ctx.node('method_call', receiver=recv, name='__index_set',
                    args=[idx, val])
    return ctx.node('method_call_statement', call=call)


def _rw_for(ctx, st, scope):
    """for-in 的协议绑定：
      __for_keys 优先（键协议）→ range = obj->__for_keys()
      否则 __iter_len：带 __index_get → 序列脱糖（隐藏临时 + 元素赋值）；
                       缺 __index_get → range = obj->__iter_len()（数字迭代）。
      类型未知 → 原样（两引擎既有定价）。"""
    k, var, rng, body = st[0], st[1], st[2], st[3]
    line = st[-1] if ctx.with_line and len(st) > 1 else 0
    # 区间/步长形态：只改写基表达式（序列 by S 走 __for_keys/数字走 __iter_len）
    if isinstance(rng, tuple) and rng and rng[0] == 'stepped_expr':
        base, bt = _rw_expr(ctx, rng[1], scope)
        info = ctx.classes.get(bt)
        new_base = base
        if info is not None:
            if info.has('__for_keys'):
                new_base = _mcall(ctx, base, '__for_keys', [], line)
            elif info.has('__iter_len'):
                new_base = _mcall(ctx, base, '__iter_len', [], line)
        nstep, _t = _rw_expr(ctx, rng[2], scope)
        nrange = (rng[0], new_base, nstep) + rng[3:]
        nbody = _rw_block(ctx, body, scope)
        scope[var] = None
        return (k, var, nrange, nbody) + st[4:]
    if isinstance(rng, tuple) and rng and rng[0] == 'range_expr':
        s, _t1 = _rw_expr(ctx, rng[1], scope)
        e, _t2 = _rw_expr(ctx, rng[2], scope)
        nrange = (rng[0], s, e) + rng[3:]
        nbody = _rw_block(ctx, body, scope)
        scope[var] = None
        return (k, var, nrange, nbody) + st[4:]
    # 平面形态：协议分派
    expr, et = _rw_expr(ctx, rng, scope)
    info = ctx.classes.get(et)
    if info is None:
        nbody = _rw_block(ctx, body, scope)
        scope[var] = None
        return (k, var, expr, nbody) + st[4:]
    if info.has('__for_keys'):
        nrange = _mcall(ctx, expr, '__for_keys', [], line)
        nbody = _rw_block(ctx, body, scope)
        scope[var] = None
        return (k, var, nrange, nbody) + st[4:]
    if info.has('__iter_len'):
        if info.has('__index_get'):
            # 序列协议：seq/lim 隐藏临时 + idx 数字循环 + 元素赋值
            seq = ctx.tmp('seq')
            lim = ctx.tmp('lim')
            idxv = ctx.tmp('idx')
            recv = ('identifier', seq) + ((line,) if ctx.with_line else ())
            recv2 = ('identifier', seq) + ((line,) if ctx.with_line else ())
            elem = ctx.node('method_call', receiver=recv2,
                            name='__index_get',
                            args=[('identifier', idxv)
                                  + ((line,) if ctx.with_line else ())])
            vd_seq = ctx.node('var_declaration', name=seq, value=expr)
            vd_lim = ctx.node('var_declaration', name=lim,
                              value=_mcall(ctx, recv, '__iter_len', [], line))
            vd_elem = ctx.node('var_declaration', name=var, value=elem)
            inner = _rw_block(ctx, body, scope)
            nbody = [vd_elem] + inner
            nfor = (k, idxv, ('identifier', lim)
                    + ((line,) if ctx.with_line else ()), nbody) \
                + ((line,) if ctx.with_line else ())
            scope[var] = None
            return [vd_seq, vd_lim, nfor]
        nrange = _mcall(ctx, expr, '__iter_len', [], line)
        nbody = _rw_block(ctx, body, scope)
        scope[var] = None
        return (k, var, nrange, nbody) + st[4:]
    nbody = _rw_block(ctx, body, scope)
    scope[var] = None
    return (k, var, expr, nbody) + st[4:]


# ---------------------------------------------------------------------------
# 表达式改写（返回 (node, type_or_None)）
# ---------------------------------------------------------------------------

def _mcall(ctx, recv, mname, args, line):
    return ctx.node('method_call', receiver=recv, name=mname, args=args)


def _rw_expr(ctx, e, scope):
    """改写 + 类型推断。type = 类名 str 或 None（未知/非类）。"""
    if not isinstance(e, tuple) or not e:
        return e, None
    k = e[0]
    line = e[-1] if ctx.with_line and len(e) > 1 else 0

    if k == 'literal':
        return e, None
    if k == 'identifier':
        return e, scope.get(e[1])
    if k == 'parenthesized':
        return _rw_expr(ctx, e[1], scope)
    if k == 'function_call':
        args = []
        types = []
        any_changed = False
        for a in (e[2] or []):
            na, ta = _rw_expr(ctx, a, scope)
            if na is not a:
                any_changed = True
            args.append(na)
            types.append(ta)
        t = e[1] if e[1] in ctx.classes else None
        if e[1] == 'output':
            # output(obj) → output(obj->__to_str())（仅类型已知 + 协议在；
            # 容器内容物不在此列 —— 那是运行期 tostring 的既有定价）
            out_args = []
            for i, a in enumerate(args):
                info = ctx.classes.get(types[i])
                if info is not None and info.has('__to_str'):
                    out_args.append(_mcall(ctx, a, '__to_str', [], line))
                    any_changed = True
                else:
                    out_args.append(a)
            args = out_args
        if not any_changed:
            return e, t
        return (k, e[1], args if e[2] is not None else None) + e[3:], t
    if k == 'method_call':
        recv, rt = _rw_expr(ctx, e[1], scope)
        args = []
        for a in (e[3] or []):
            na, _t = _rw_expr(ctx, a, scope)
            args.append(na)
        # 返回类型推断：五则协议方法（__add__ 族，不含 __eq__）按惯例返回
        # 接收者类型（运算符链 (a+b)*c-d 由此可组合绑定）；其余方法不可知
        # → None。推断错了属于「开发者自责」卫生条款（两引擎拿到同一份
        # 改写，行为仍一致，只是值面由开发者负责）。
        t = None
        rinfo = ctx.classes.get(rt)
        if rinfo is not None and e[2] in SAME_TYPE_DUNDERS \
                and rinfo.has(e[2]):
            t = rt
        return (k, recv, e[2], args if e[3] is not None else None) + e[4:], t
    if k == 'field_access':
        obj, ot = _rw_expr(ctx, e[1], scope)
        t = None
        info = ctx.classes.get(ot)
        if info is not None:
            t = info.field_types.get(e[2])
        return (k, obj, e[2]) + e[3:], t
    if k == 'field_assignment':
        obj, ot = _rw_expr(ctx, e[1], scope)
        val, vt = _rw_expr(ctx, e[3], scope)
        if ot and isinstance(e[1], tuple) and e[1][0] == 'identifier' \
                and e[1][1] == 'this':
            info = ctx.classes.get(ot)
            if info is not None and vt:
                info.field_types[e[2]] = vt
        return (k, obj, e[2], val) + e[4:], None
    if k == 'binary_op':
        return _rw_binary(ctx, e, scope)
    if k == 'unary_op':
        inner, _t = _rw_expr(ctx, e[2], scope)
        return (k, e[1], inner) + e[3:], None
    if k == 'ternary_expr':
        c, _t1 = _rw_expr(ctx, e[1], scope)
        a, ta = _rw_expr(ctx, e[2], scope)
        b, tb = _rw_expr(ctx, e[3], scope)
        return (k, c, a, b) + e[4:], (ta if ta == tb else None)
    if k == 'array':
        elems = []
        for a in (e[1] or []):
            na, _t = _rw_expr(ctx, a, scope)
            elems.append(na)
        return (k, elems if e[1] is not None else None) + e[2:], None
    if k == 'dict':
        entries = []
        changed = False
        for entry in (e[1] or []):
            key, val = entry[0], entry[1]
            nk, _t1 = _rw_expr(ctx, key, scope)
            nv, _t2 = _rw_expr(ctx, val, scope)
            if nk is not key or nv is not val:
                changed = True
            rest = entry[2:]
            entries.append((nk, nv) + rest)
        if not changed:
            return e, None
        return (k, entries if e[1] is not None else None) + e[2:], None
    if k == 'array_index':
        idx, _t = _rw_expr(ctx, e[2], scope)
        info = ctx.classes.get(scope.get(e[1]))
        if info is not None and info.has('__index_get'):
            recv = ('identifier', e[1]) + ((line,) if ctx.with_line else ())
            return _mcall(ctx, recv, '__index_get', [idx], line), None
        return (k, e[1], idx) + e[3:], None
    if k == 'array_index_chain':
        base, bt = _rw_expr(ctx, e[1], scope)
        idx, _t = _rw_expr(ctx, e[2], scope)
        info = ctx.classes.get(bt)
        if info is not None and info.has('__index_get'):
            return _mcall(ctx, base, '__index_get', [idx], line), None
        return (k, base, idx) + e[3:], None
    if k == 'lambda_expr':
        # lambda 体不改写（创建点类型 ≠ 调用点值面；保守 = 既有定价）
        return e, None
    if k == 'try_statement' or k == 'throw_statement':
        return e, None
    # 其余（static_method_call / function_ref / range 形态…）原样
    return e, None


def _expr_type(ctx, node, scope, parent, idx):  # noqa: 保留签名兼容（内部不再使用）
    return None


def _rw_expr_list(ctx, items, scope):
    """表达式列表位（数组字面量元素等）的改写。"""
    if not isinstance(items, list):
        return items
    out = []
    for a in items:
        na, _t = _rw_expr(ctx, a, scope)
        out.append(na)
    return out


def _rw_binary(ctx, e, scope):
    """运算符协议分派（左优先）：
      arith/==：左类型已知 + 协议在 → method_call；
      '+' 左未中：__to_str 兜拼接（左有 __to_str，或左未知而右有）。"""
    k, op, left, right = e[0], e[1], e[2], e[3]
    line = e[-1] if ctx.with_line and len(e) > 1 else 0
    ln, lt = _rw_expr(ctx, left, scope)
    rn, rt = _rw_expr(ctx, right, scope)
    info = ctx.classes.get(lt)
    if info is not None and op in ARITH_DUNDER and info.has(ARITH_DUNDER[op]):
        # 返回类型：五则 = 接收者类（链式组合推断源）；__eq__ = 布尔（不推断）
        return _mcall(ctx, ln, ARITH_DUNDER[op], [rn], line), \
            (lt if op != '==' else None)
    if op == '+':
        linfo = info
        if linfo is not None and linfo.has('__to_str'):
            left2 = _mcall(ctx, ln, '__to_str', [], line)
            return (k, op, left2, rn) + e[4:], None
        rinfo = ctx.classes.get(rt)
        if rinfo is not None and rinfo.has('__to_str'):
            right2 = _mcall(ctx, rn, '__to_str', [], line)
            return (k, op, ln, right2) + e[4:], None
    if (ln is not left) or (rn is not right):
        return (k, op, ln, rn) + e[4:], None
    return e, None
