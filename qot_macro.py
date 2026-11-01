# -*- coding: utf-8 -*-
"""GOD-SELF-G2 · 编译期宏（AST→AST）—— AST ↔ QOT 值双向转换器 + 宏展开器。

立法出处： §8.24（业主拍板定稿 · 0）。
本文件是 G2 的**最大单件**，冷启动第一刀切的是转换器（往返无损测试先行，
见 testing/unit/test_macro.py —— 转换器未跑通往返测试前禁止写任何宏用例）。

== AST 值表示契约（转换器头部登记，规格 §8.24-C） ==

宏收到的节点 = 与编译器内部 AST 双向无损转换的 QOT 值。QOT 值面 =
number(f64) / string / boolean / null / array(Python list) / dict(Python dict)。

  * 内部 AST 节点 = Python tuple：`(kind, 字段..., [行号])` —— 尾部行号
    可选（解释器路径带行号，编译路径经 strip_line_numbers 后不带）。
  * 值形态 = dict：`{"kind": <kind>, <字段名>: <转换后的字段值>, ...,
    "line": <行号>}`。行号字段仅在输入 AST 带行号时存在（保形，不补造）。
  * 字段名表 NODE_FIELDS[kind] 在下方逐 kind 登记 —— 以 grammar.py 的
    产生式为准（0 全量盘点）。元组字段（dict entry 的
    `(key, value[, line])` 三元组）转 **数组** `[key值, value值[, line]]`
    （entry 无 kind 名，位置数组即无损形态）。
  * 未知 kind（未来语法演进）：fallback 到 `{"kind":..., "fields":[...]}`
    的位置形态 —— 仍然无损（往返相等），只是字段无名字。往返测试对
    已知/未知两种形态都必须成立（test_macro.py 逐字段断言）。

定点规则（损失源清点）：
  * tuple → dict(kind)、list → list、str/number/bool/None → 原样；
  * 转换是深转换：宏拿到的是副本，改写返回值不会污染原树（宏 = 纯函数）。

== 展开器（执行时机与上限） ==

  * 执行时机 = AST 构建后、bytecode/求值前（index.py 三条路径共用同一
    入口：interp run / bstrust compile / aot —— 一次实现双引擎天然一致）。
  * `macro` 关键字声明（仅限顶层，sema 立法）；调用 = `name!(args)`，
    语句位可带块：`name!(args) { stmts }`（块以语句 AST 列表绑定到宏的
    最后一个形参）。
  * 宏体 = QOT 函数：形参收 AST 节点值，`return` AST 节点值；执行宿主 =
    Evaluator 本身（编译期即解释器）——「编译器本身就是 Python」是既定
    架构（§8.24-E4），与产物性能无关。
  * 上限：展开深度默认 64、总步数默认 1,000,000；env
    QOT_MACRO_DEPTH / QOT_MACRO_LIMIT 可调，0 = 不限（上帝法则）；
    超限 = 编译期错误 `[4000] 宏展开超限`。
  * 卫生：**不做**（业主裁决）—— 展开体共享调用点作用域，名字捕获的
    后果由宏作者负责；`_m_` 前缀是官方宏的约定俗成，非机制保证。
  * 官方宏 swap!/unless! 以 QOT 体（值形态）内建于 _builtin_macro_decls，
    经同一 value_to_ast 落地 —— 官方宏自己就是转换器的使用面；展开机制
    与用户宏完全同一条（无特权路径）。

== 展开产物与 sema 的关系（登记） ==

展开发生在 parse（含 sema）之后：宏【声明体】在 sema 处按「形参预登记 +
体检查」覆盖（grammar.SemanticAnalyzer.visit_macro_declaration）；宏的
【实参】与【展开产物】不经 sema（时序上 sema 已跑完）——两侧引擎拿到的是
同一份展开后 AST，行为一致由构造保证；宏作者的静态责任 = 往返测试 +
差分用例（gs2_macro / gs3_emergent 即裁判）。
"""
import os
import sys

from error_system import (ErrorCode, ErrorLevel, QOTError,
                          get_error_handler)

# ---------------------------------------------------------------------------
# 1. AST 字段名登记表（kind → 除 kind/line 外的字段名序）
#    以 grammar.py 产生式为准（0 全量盘点）。变更语法必须同步这里
#    （test_macro.py 的往返测试会逮住漂移）。
# ---------------------------------------------------------------------------
NODE_FIELDS = {
    'start': ['statements'],
    'var_declaration': ['name', 'value'],
    'const_declaration': ['name', 'value'],
    'assignment': ['name', 'value'],                      # name = 标识符串
    'array_assignment': ['target', 'value'],
    'array_declaration': ['name', 'elements'],
    'dict_declaration': ['name', 'entries'],
    'binary_op': ['op', 'left', 'right'],                 # op = 运算符串
    'unary_op': ['op', 'expr'],                           # UMINUS / NOT(!)
    'ternary_expr': ['cond', 'then', 'else'],
    'literal': ['value'],
    'identifier': ['name'],
    'parenthesized': ['expr'],
    'array': ['elements'],
    'dict': ['entries'],                                  # entry 元素见上
    'function_call': ['name', 'args'],
    'function_call_statement': ['call'],
    'function_ref': ['name', 'args'],
    'method_call': ['receiver', 'name', 'args'],
    'method_call_statement': ['call'],
    'static_method_call': ['module', 'name', 'args'],
    'static_method_call_statement': ['call'],
    'static_access': ['module', 'name'],
    'array_index': ['name', 'index'],                     # name = 标识符串
    'array_index_chain': ['base', 'index'],
    'field_access': ['obj', 'name'],
    'field_assignment': ['obj', 'name', 'value'],
    'if_statement': ['cond', 'then', 'eif', 'else'],
    'eif_clause': ['cond', 'then'],
    'else_clause': ['block'],
    'while_statement': ['cond', 'body'],
    'for_statement': ['var', 'range', 'body'],
    'range_expr': ['start', 'end', 'step'],
    'stepped_expr': ['base', 'step'],
    'return_statement': ['value'],
    'func_declaration': ['ret_type', 'name', 'params', 'body'],
    'class_declaration': ['name', 'members'],
    'field': ['name', 'init'],
    'method': ['ret_type', 'name', 'params', 'body'],
    'try_statement': ['body', 'catch_var', 'catch_body'],
    'throw_statement': ['value'],
    'lambda_expr': ['params', 'expr'],
    'require_statement': ['lib'],
    'annotation': ['text'],
    'stop_statement': [],
    'continue_statement': [],
    'macro_declaration': ['name', 'params', 'body'],
    'macro_call': ['name', 'args', 'block'],
}

MAX_DEPTH_DEFAULT = 64
MAX_STEPS_DEFAULT = 1000000


def _limit_from_env(name, default):
    try:
        v = int(os.environ.get(name) or 0)
    except (TypeError, ValueError):
        return default
    if v == 0:
        return 0            # 0 = 不限（上帝法则：配置买走边界）
    return v if v > 0 else default


class MacroError(Exception):
    """宏面编译期致命（未定义/重定义/超限/展开失败）。expand_program
    捕获后返回 None 哨兵 —— 与解析失败同一出口（调用方 exit 1）。"""

    def __init__(self, message, line=0):
        super().__init__(message)
        self.message = message
        self.line = line


def _report(code, message, filename, line=0):
    """编译期错误出口：stderr 确定格式 `[错误] [4000] …` + collector 登记
    （与 record_exception 同款，但不 exit —— exit 由调用方按哨兵统一走）。"""
    handler = get_error_handler()
    err = QOTError(code=code, message=message, level=ErrorLevel.ERROR,
                   file_name=filename or '', line_number=line or 0)
    try:
        sys.stdout.flush()
    except Exception:
        pass
    try:
        sys.stderr.write(err.format(color=False) + '\n')
        sys.stderr.flush()
    except Exception:
        pass
    try:
        handler.collector.add(err)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# 2. AST ↔ 值 双向转换器（往返无损是唯一验收：test_macro.py 逐字段断言）
# ---------------------------------------------------------------------------

def ast_to_value(node):
    """内部 AST（tuple/list/标量）→ QOT 值（dict/list/标量）。深转换。
    dict entry（无名 tuple (k,v[,line])）→ 数组 [k值, v值[, line]]；
    tuple↔list 的保形由 kind='dict' 的 entries 字段在 value_to_ast 侧
    恢复（AST 里唯一的无名 tuple 容器就是 dict entries）。"""
    if isinstance(node, tuple):
        if not node:
            return {'kind': '__nil__'}
        kind = node[0]
        if not isinstance(kind, str):
            # 非节点元组（dict entry (k,v[,line]) 等无名形态）：位置数组保形
            return [ast_to_value(x) for x in node]
        body = node[1:]
        has_line = (len(body) > 0 and isinstance(body[-1], int)
                    and not isinstance(body[-1], bool))
        core = body[:-1] if has_line else body
        names = NODE_FIELDS.get(kind)
        out = {'kind': kind}
        if names is not None and len(names) == len(core):
            for nm, val in zip(names, core):
                if nm == 'entries' and isinstance(val, list):
                    out[nm] = [ast_to_value(x) for x in val]
                else:
                    out[nm] = ast_to_value(val)
        else:
            # 未登记/漂移 kind：位置形态兜底（无损，字段无名）
            out['fields'] = [ast_to_value(x) for x in core]
        if has_line:
            out['line'] = body[-1]
        return out
    if isinstance(node, list):
        return [ast_to_value(x) for x in node]
    return node


def _entry_to_ast(entry):
    """entry 值（数组 [键值, 值值[, 行号]]）→ 无名 tuple。"""
    if isinstance(entry, list):
        return tuple(value_to_ast(x) for x in entry)
    return value_to_ast(entry)


def value_to_ast(v):
    """QOT 值 → 内部 AST。与 ast_to_value 严格互逆（往返相等）。"""
    if isinstance(v, list):
        return [value_to_ast(x) for x in v]
    if isinstance(v, dict):
        kind = v.get('kind')
        if not isinstance(kind, str):
            raise MacroError('宏返回值不是 AST 节点（缺 "kind" 字段）: %s'
                             % value_repr(v))
        names = NODE_FIELDS.get(kind)
        ln = v.get('line')
        has_line = isinstance(ln, int) and not isinstance(ln, bool)
        if 'fields' in v:
            core = [value_to_ast(x) for x in (v.get('fields') or [])]
        elif names is not None:
            core = []
            for nm in names:
                if nm not in v:
                    raise MacroError('宏返回节点缺少字段 "%s"（kind=%s）'
                                     % (nm, kind))
                val = v[nm]
                if nm == 'entries' and isinstance(val, list):
                    # dict entries：容器保持数组；entry 恢复无名 tuple
                    # （保形往返的关键；宏作者以数组形态构造 entry：
                    #   [键值, 值值[, 行号]]）
                    core.append([_entry_to_ast(x) for x in val])
                else:
                    core.append(value_to_ast(val))
        else:
            raise MacroError('宏返回了未知节点类型 "%s"（不在 NODE_FIELDS 登记表）'
                             % kind)
        out = (kind,) + tuple(core)
        if has_line:
            out = out + (ln,)
        return out
    return v


def value_repr(v):
    try:
        import json
        return json.dumps(v, ensure_ascii=False, default=str)[:200]
    except Exception:
        return str(v)[:200]


def node_line(node):
    """取节点行号（带行号形态的最后一位 int；strip 形态 → 0）。"""
    if isinstance(node, tuple) and len(node) > 1:
        last = node[-1]
        if isinstance(last, int) and not isinstance(last, bool):
            return last
    return 0


def _fill_lines(node, line, with_line):
    """展开产物行号回填：宏作者没写 "line" 的节点补调用点行号（§8.24-C
    「宏内报错定位到调用点」的机制面）；作者写了以作者为准。深遍历。"""
    if not with_line or not line:
        return node
    if isinstance(node, list):
        return [_fill_lines(x, line, with_line) for x in node]
    if isinstance(node, tuple) and node:
        kind = node[0]
        if isinstance(kind, str) and kind in NODE_FIELDS \
                and len(node) == len(NODE_FIELDS[kind]) + 1:
            node = node + (line,)
        out = []
        for x in node:
            if isinstance(x, (tuple, list)):
                out.append(_fill_lines(x, line, with_line))
            else:
                out.append(x)
        return tuple(out)
    return node


def make_node(kind, with_line, line, **fields):
    """按登记表构造节点（宏生成产物的统一入口）。"""
    names = NODE_FIELDS.get(kind)
    if names is None:
        raise MacroError('make_node: 未知节点类型 "%s"' % kind)
    missing = [nm for nm in names if nm not in fields]
    if missing:
        raise MacroError('make_node(%s): 缺字段 %s' % (kind, missing))
    out = (kind,) + tuple(fields[nm] for nm in names)
    if with_line:
        out = out + (line if line else 0,)
    return out


# ---------------------------------------------------------------------------
# 3. 官方宏（验收载体）：swap!（C6 引用语义涌现）/ unless!（控制流涌现）
#    体 = QOT 值形态（就地构造）。卫生约定：官方宏的临时名用 `_m_` 前缀
#    （「开发者自责」条款的示范；捕获后果由宏作者负责）。
# ---------------------------------------------------------------------------

def _builtin_macro_decls(with_line):
    """{宏名: (params, body_stmts)} —— AST 节点形态。"""

    def ident(name):
        return make_node('identifier', with_line, 1, name=name)

    def lit(v):
        return make_node('literal', with_line, 1, value=v)

    def idx(base_name, key):
        return make_node('array_index', with_line, 1,
                         name=base_name, index=lit(key))

    def var_decl(name, value):
        return make_node('var_declaration', with_line, 1,
                         name=name, value=value)

    def assign(name, value):
        return make_node('assignment', with_line, 1, name=name, value=value)

    def ret(v):
        return make_node('return_statement', with_line, 1, value=v)

    def arr(items):
        # return 的数组面必须是 ('array', …) 节点 —— 宏体经 Evaluator 执行，
        # 裸 Python list 会被当语句表；节点求值后才得到产物 list。
        return make_node('array', with_line, 1, elements=items)

    def dict_node(pairs):
        entries = [(lit(k), val, 1) for k, val in pairs]
        return make_node('dict', with_line, 1, entries=entries)

    # -- swap!(a, b)：展开 = `var _m_swap_t = <a>; <a> = <b>; <b> = _m_swap_t;`
    #    形参收 AST 节点值；a["name"] 取目标名（swap! 契约 = 标识符实参，
    #    非标识符实参在展开期明确报错 —— _check_swap_args）。
    swap_body = [
        var_decl('_m_t', idx('a', 'name')),
        ret(arr([
            dict_node([('kind', lit('var_declaration')),
                       ('name', lit('_m_swap_t')),
                       ('value', ident('a'))]),
            dict_node([('kind', lit('assignment')),
                       ('name', ident('_m_t')),
                       ('value', ident('b'))]),
            dict_node([('kind', lit('assignment')),
                       ('name', idx('b', 'name')),
                       ('value', dict_node([('kind', lit('identifier')),
                                            ('name', lit('_m_swap_t'))]))]),
        ])),
    ]
    # -- unless!(cond) { body }：展开 = `if (cond) {} else { body }`
    #    （then 空块、else 承载块）—— 控制流涌现不需要逻辑非。
    unless_body = [
        ret(arr([
            dict_node([('kind', lit('if_statement')),
                       ('cond', ident('cond')),
                       ('then', make_node('array', with_line, 1, elements=[])),
                       ('eif', lit(None)),
                       ('else', dict_node([
                           ('kind', lit('else_clause')),
                           ('block', ident('body')),
                       ]))]),
        ])),
    ]
    return {
        'swap': (['a', 'b'], swap_body),
        'unless': (['cond', 'body'], unless_body),
    }


def _check_swap_args(args, line):
    """swap! 的契约：两个标识符实参（引用语义涌现的最小面）。"""
    for i, a in enumerate(args or []):
        if not (isinstance(a, tuple) and a and a[0] == 'identifier'):
            got = a[0] if isinstance(a, tuple) and a else type(a).__name__
            raise MacroError(
                "宏 'swap' 第 %d 个实参必须是标识符（swap! 只交换变量），得到 %s"
                % (i + 1, got), line)


# ---------------------------------------------------------------------------
# 4. 展开器
# ---------------------------------------------------------------------------

class MacroExpander:
    def __init__(self, filename='', with_line=True):
        self.filename = filename
        self.with_line = with_line
        self.macros = {}          # name -> (params, body_stmts, decl_line)
        self.depth_limit = _limit_from_env('QOT_MACRO_DEPTH',
                                           MAX_DEPTH_DEFAULT)
        self.step_limit = _limit_from_env('QOT_MACRO_LIMIT',
                                          MAX_STEPS_DEFAULT)
        self.steps = 0
        self.depth = 0
        self._body_cache = {}     # 宏名 -> 预展开体
        self._in_body = False     # 体预展开中（表达式位宏调用不展开）

    # -- 上限记账 -----------------------------------------------------------
    def _tick(self, n=1):
        self.steps += n
        if self.step_limit and self.steps > self.step_limit:
            raise MacroError('宏展开超限（steps=%d，上限 %d；可用环境变量 '
                             'QOT_MACRO_LIMIT 调整，0 = 不限）'
                             % (self.steps, self.step_limit))

    def _enter(self):
        self.depth += 1
        if self.depth_limit and self.depth > self.depth_limit:
            raise MacroError('宏展开超限（depth=%d，上限 %d；可用环境变量 '
                             'QOT_MACRO_DEPTH 调整，0 = 不限）'
                             % (self.depth, self.depth_limit))

    def _leave(self):
        self.depth -= 1

    # -- 顶层收集 -----------------------------------------------------------
    def collect_macros(self, ast):
        """登记顶层 macro_declaration（就地从语句表摘除）。官方宏先注册；
        用户×用户重名 → 重定义报错（F0 现律）；遮蔽官方宏同罪。"""
        statements = None
        if isinstance(ast, tuple) and ast and ast[0] == 'start':
            statements = ast[1]
        elif isinstance(ast, list):
            statements = ast
        if statements is None:
            return
        for name, (params, body) in _builtin_macro_decls(self.with_line).items():
            self.macros[name] = (params, body, 0)
        kept = []
        for stmt in statements:
            if isinstance(stmt, tuple) and stmt and stmt[0] == 'macro_declaration':
                mname = stmt[1]
                if mname in self.macros:
                    raise MacroError("宏 '%s' 已定义（宏名不可重复定义，"
                                     "官方宏 swap/unless 亦不可遮蔽）" % mname,
                                     node_line(stmt))
                self.macros[mname] = (list(stmt[2] or []), stmt[3],
                                      node_line(stmt))
            else:
                kept.append(stmt)
        statements[:] = kept

    # -- 体预展开（体内【语句位】宏调用拼接；表达式位保留给执行期钩子） ---
    # 语义裁定：宏体内的表达式位宏调用（inc!(inc!(x))）走【值语义】——
    # 体以解释器执行时经 macro_hook 自底向上展开（实参先求值成节点值）；
    # 若在预展开期按符号展开成裸 AST，体执行时会把「产物节点」当代码
    # 求值（binary_op 节点触发算术）—— 首版实测的 dict+int TypeError。
    # 语句位宏调用（swap!(a,b);）保持拼接语义：实参是 AST（形参名在
    # 体执行期解析），拼接产物作为体的语句执行。
    def _body_of(self, name):
        if name in self._body_cache:
            return self._body_cache[name]
        params, body, _ln = self.macros[name]
        self._enter()
        self._in_body = True
        try:
            self._walk_block(body)
        finally:
            self._in_body = False
            self._leave()
        self._body_cache[name] = body
        return body

    # -- 展开主流程 ---------------------------------------------------------
    def expand(self, ast):
        self.collect_macros(ast)
        if isinstance(ast, tuple) and ast and ast[0] == 'start':
            self._walk_block(ast[1])
        elif isinstance(ast, list):
            self._walk_block(ast)
        return ast

    def _walk_block(self, statements):
        out = []
        for stmt in statements:
            self._tick()
            got = self._walk_stmt(stmt)
            if got is None:
                out.append(stmt)
            elif isinstance(got, list):
                out.extend(got)
            else:
                out.append(got)
        statements[:] = out

    def _walk_stmt(self, stmt):
        """语句位：返回 None = 原样；list = 语句块替换；node = 单语句替换。"""
        if not isinstance(stmt, tuple) or not stmt:
            return None
        kind = stmt[0]
        if kind == 'macro_call_statement':
            return self._expand_call_stmt(stmt[1], node_line(stmt))
        if kind == 'macro_call':
            # 裸宏调用节点出现在语句位（防御面）：按语句展开
            return self._expand_call_stmt(stmt, node_line(stmt))
        return self._rewrite_node(stmt)

    def _expand_call_stmt(self, call, line):
        result = self._expand_call(call, line, statement_pos=True)
        if isinstance(result, list):
            return result
        if isinstance(result, tuple):
            if result[0] == 'function_call':
                # 调用表达式落在语句位 = 语言的 function_call_statement
                # 形态（yield! 原型的载体：宏返回 __emit(v) 调用）
                out = [('function_call_statement', result)
                       + ((line,) if self.with_line else ())]
                self._enter()
                try:
                    self._walk_block(out)
                finally:
                    self._leave()
                return out
            raise MacroError("宏 '%s' 展开为表达式，不能单独作为语句"
                             "（用 var 接住结果，或让宏返回语句块）"
                             % (call[1],), line)
        raise MacroError('宏展开结果类型非法（%s）' % type(result).__name__,
                         line)

    def _expand_call(self, call, line, statement_pos):
        """执行一次宏展开 → 表达式节点 / 语句块 list。四步：
        ① 实参整形 ② 体预展开（体内宏调用递归，深度+1）③ Evaluator 执行
        ④ 值→AST 后对产物再走一遍（产物里的宏调用继续展开，深度+1）。"""
        mname = call[1]
        args = list(call[2] or [])
        block = call[3] if len(call) > 3 else None
        if mname not in self.macros:
            raise MacroError("宏 '%s' 未定义（name!() 调用只解析编译期宏）"
                             % mname, line)
        params, _body, _dl = self.macros[mname]
        if block is not None:
            args.append(block)          # 块 = 绑定到最后一个形参的语句列表
        if len(args) != len(params):
            raise MacroError("宏 '%s' 需要 %d 个参数，但传入了 %d 个"
                             % (mname, len(params), len(args)), line)
        if mname == 'swap':
            _check_swap_args(args, line)
        body = self._body_of(mname)
        self._enter()
        self._tick()
        try:
            value = self._run_body(params, args, body, line)
        finally:
            self._leave()
        if isinstance(value, list):
            if not statement_pos:
                raise MacroError("宏 '%s' 返回语句块，不能用于表达式位置"
                                 % mname, line)
            out = [_fill_lines(value_to_ast(st), line, self.with_line)
                   for st in value]
            self._enter()
            try:
                self._walk_block(out)
            finally:
                self._leave()
            return out
        node = _fill_lines(value_to_ast(value), line, self.with_line)
        self._enter()
        try:
            got = self._rewrite_node(node)
        finally:
            self._leave()
        return node if got is None else got

    def expand_body_call(self, ev, node, statement=False):
        """【体执行期钩子】Evaluator 在宏体内遇到 macro_call 时回调。
        值语义：实参先经 Evaluator 求值（自底向上 —— 内层宏调用先展开成
        节点值），再以「形参 = 节点值」执行宏，产物转回节点值。"""
        line = node_line(node)
        mname = node[1]
        if mname not in self.macros:
            raise MacroError("宏 '%s' 未定义" % mname, line)
        args = []
        for a in (node[2] or []):
            args.append(ev.visit(a))            # 值语义：实参先求值
        block = node[3] if len(node) > 3 else None
        if block is not None:
            args.append(block)                  # 块 = 语句 AST 列表（不求值）
        params, _body, _dl = self.macros[mname]
        if len(args) != len(params):
            raise MacroError("宏 '%s' 需要 %d 个参数，但传入了 %d 个"
                             % (mname, len(params), len(args)), line)
        if mname == 'swap':
            _check_swap_args([value_to_ast(v) if isinstance(v, dict) else v
                              for v in args], line)
        body = self._body_of(mname)
        self._enter()
        self._tick()
        try:
            value = self._run_body(params, args, body, line)
        finally:
            self._leave()
        if isinstance(value, list):
            if not statement:
                raise MacroError("宏 '%s' 返回语句块，不能用于表达式位置"
                                 % mname, line)
            out = [_fill_lines(value_to_ast(st), line, self.with_line)
                   for st in value]
            self._enter()
            try:
                self._walk_block(out)
            finally:
                self._leave()
            return out
        node_out = _fill_lines(value_to_ast(value), line, self.with_line)
        if statement:
            if node_out[0] == 'function_call':
                return [('function_call_statement', node_out)
                        + ((line,) if self.with_line else ())]
            raise MacroError("宏 '%s' 展开为表达式，不能单独作为语句" % mname,
                             line)
        return ast_to_value(node_out)

    def _run_body(self, params, args, body, line):
        """以 Evaluator 执行宏体（形参 = AST 值；return 值 = 展开产物）。
        宿主 = 进程级单例（编译期一次性设施）；变量快照进出保证隔离；
        macro_hook 供体内的表达式位宏调用走值语义。"""
        from index import Evaluator, ReturnException
        host = getattr(MacroExpander, '_host', None)
        if host is None:
            host = Evaluator()
            MacroExpander._host = host
        ev = host
        saved = ev.variables.copy()
        saved_hook = getattr(ev, 'macro_hook', None)
        ev.macro_hook = self
        try:
            for p, a in zip(params, args):
                ev.variables[p] = ('param', ast_to_value(a))
            result = None
            try:
                ev.visit(body)
            except ReturnException as e:
                result = e.value
            return result
        except MacroError:
            raise
        except Exception as e:
            raise MacroError('宏体执行失败（调用点行 %s）：%s: %s'
                             % (line, type(e).__name__, e), line)
        finally:
            ev.macro_hook = saved_hook
            ev.variables.clear()
            ev.variables.update(saved)

    # -- 通用节点改写（在任意表达式位找 macro_call） ------------------------
    def _rewrite_node(self, node):
        """后序遍历：子节点先改写；本节点是 macro_call → 展开。
        返回 None = 原样；否则返回替换节点。"""
        if isinstance(node, list):
            changed = False
            out = []
            for x in node:
                self._tick()
                got = self._rewrite_item(x)
                if got is None:
                    out.append(x)
                elif isinstance(got, list):
                    out.extend(got)
                    changed = True
                else:
                    out.append(got)
                    changed = True
            return out if changed else None
        if not isinstance(node, tuple) or not node:
            return None
        kind = node[0]
        if kind == 'macro_call':
            if self._in_body:
                # 体预展开期：表达式位宏调用保留原节点 —— 体执行时经
                # macro_hook 走值语义（见 _body_of 的语义裁定）
                return None
            res = self._expand_call(node, node_line(node),
                                    statement_pos=False)
            if isinstance(res, list):
                raise MacroError('宏展开为语句块，不能用于表达式位置',
                                 node_line(node))
            return res
        names = NODE_FIELDS.get(kind)
        if names is None:
            # 未知 kind：按位置保守扫描（tuple/list 才进）
            changed = False
            parts = [node[0]]
            for x in node[1:]:
                self._tick()
                if isinstance(x, (tuple, list)):
                    got = self._rewrite_item(x)
                    if got is None:
                        parts.append(x)
                    elif isinstance(got, list) and not isinstance(x, list):
                        raise MacroError('语句块展开出现在非法位置',
                                         node_line(node))
                    else:
                        parts.append(got)
                        changed = changed or (got is not x)
                else:
                    parts.append(x)
            return tuple(parts) if changed else None
        changed = False
        parts = [node[0]]
        for i, x in enumerate(node[1:]):
            self._tick()
            nm = names[i] if i < len(names) else None
            if nm in ('name', 'op', 'ret_type', 'var', 'lib', 'text',
                      'catch_var') or not isinstance(x, (tuple, list)):
                parts.append(x)
                continue
            if isinstance(x, list):
                # 列表字段（args/块/elements…）：元素改写，列表形态合法
                got = self._rewrite_item(x)
                if got is None:
                    parts.append(x)
                elif isinstance(got, list):
                    parts.append(got)
                    changed = changed or (got is not x)
                else:
                    parts.append(got)
                    changed = True
                continue
            got = self._rewrite_item(x)
            if got is None:
                parts.append(x)
            elif isinstance(got, list):
                raise MacroError('语句块展开出现在表达式字段（kind=%s）'
                                 % kind, node_line(node))
            else:
                parts.append(got)
                changed = True
        if not changed:
            return None
        return tuple(parts)

    def _rewrite_item(self, x):
        """列表元素位：语句 / 表达式 / 嵌套列表。"""
        if isinstance(x, list):
            return self._rewrite_node(x)
        if isinstance(x, tuple) and x and x[0] == 'macro_call_statement':
            return self._walk_stmt(x)
        return self._rewrite_node(x)


def expand_program(ast, filename=''):
    """入口：AST 构建后调用。返回改写后的 ast；致命 → None（调用方 exit 1）。

    快路径：源里没有 macro（声明或调用）→ 原样返回，零改写零开销
    （既有 300+ 用例与全部语料的默认形态）。
    """
    try:
        if not _has_macro_nodes(ast):
            return ast
        with_line = _detect_with_line(ast)
        exp = MacroExpander(filename=filename, with_line=with_line)
        return exp.expand(ast)
    except MacroError as e:
        _report(ErrorCode.ERR_COMPILE_GENERAL, e.message, filename, e.line)
        return None
    except RecursionError:
        _report(ErrorCode.ERR_COMPILE_GENERAL,
                '宏展开超限（递归过深：宏体互相展开形成循环；'
                '可用 QOT_MACRO_DEPTH 收紧上限以提前失败）', filename)
        return None


def _detect_with_line(ast):
    if isinstance(ast, tuple) and len(ast) > 2 and ast[0] == 'start':
        return isinstance(ast[-1], int) and not isinstance(ast[-1], bool)
    return True


def _has_macro_nodes(node):
    if isinstance(node, tuple):
        if node and node[0] in ('macro_declaration', 'macro_call',
                                'macro_call_statement'):
            return True
        for x in node:
            if _has_macro_nodes(x):
                return True
        return False
    if isinstance(node, list):
        for x in node:
            if _has_macro_nodes(x):
                return True
    return False
