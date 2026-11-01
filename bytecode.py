import sys
import os
import ctypes
import base64
import hmac
import hashlib
from typing import Any, Dict, List, Tuple, Optional, Union
from token_char import TokenType
from ast_encoder import decode_ast, encode_ast
from grammar import parse_oning_gram
from builtin_func import BuiltFunc

SIGNATURE_SEPARATOR = b'||SIGNATURE||'
VERSION_MARKER = b'QOT_SIGN_v1|'
CHUNK_SIZE = 1024 * 1024


def package_file_encode(content: Union[str, bytes]) -> bytes:
    if isinstance(content, str):
        content = content.encode('utf-8')
    encoded = bytearray()
    key = 0x5A
    for b in content:
        encoded_byte = (b ^ key) & 0xFF
        encoded.append(encoded_byte)
        key = (key + encoded_byte) & 0xFF
    return bytes(encoded)


def package_file_decode(encoded_content: bytes) -> bytes:
    decoded = bytearray()
    key = 0x5A
    for b in encoded_content:
        decoded_byte = (b ^ key) & 0xFF
        decoded.append(decoded_byte)
        key = (key + b) & 0xFF
    return bytes(decoded)


def verify_file_signature(signed_file_path: str, secret_key: str) -> Tuple[bool, Optional[bytes]]:
    try:
        if not os.path.exists(signed_file_path):
            return False, "文件不存在"
        with open(signed_file_path, 'rb') as f:
            content = f.read()
        if not content.startswith(VERSION_MARKER):
            return False, "不支持的签名版本"
        content = content[len(VERSION_MARKER):]
        parts = content.split(SIGNATURE_SEPARATOR)
        if len(parts) != 2:
            return False, "文件格式错误，缺少签名分隔符"
        encoded_content, signature_b64 = parts
        key_bytes = secret_key.encode('utf-8')
        h = hmac.new(key_bytes, encoded_content, hashlib.sha256)
        expected_signature = h.digest()
        try:
            signature = base64.b64decode(signature_b64)
        except ValueError:
            return False, "签名格式错误"
        if not hmac.compare_digest(signature, expected_signature):
            return False, "签名验证失败"
        decoded_content = package_file_decode(encoded_content)
        return True, decoded_content
    except Exception as e:
        return False, f"验证失败: {str(e)}"


class VMInstruction:
    PUSH_CONST = 1      
    PUSH_VAR = 2        
    STORE_VAR = 3       
    STORE_CONST = 4     
    POP = 5             
    BINARY_OP = 10      
    UNARY_OP = 11       
    JUMP = 20           
    JUMP_IF_FALSE = 21  
    JUMP_IF_TRUE = 22   
    CALL = 30           
    CALL_METHOD = 31    
    RETURN = 32        
    MAKE_ARRAY = 40     
    MAKE_DICT = 41      
    INDEX_GET = 42      
    INDEX_SET = 43      
    NOP = 0 
    POP_STMT = 50       
    STOP = 51
    REQUIRE = 60
    CALL_STATIC = 33
    BREAK = 52
    # ---- F1/F2（0）：闭包与 try/catch ----
    # VM（冻结）不实现这些 op：VM 模式遇到即运行期报未知指令。
    # 闭包/异常的解释器与 AOT（native）双实现由差分裁判钉住（§8.8.4 交付清单）。
    ENV_NEW = 61        # arg=(env_slot, n_caps)：分配激活环境（嵌套函数/有捕获者）
    MAKE_CLOSURE = 62   # arg=(const_idx, env_slot|-1)：把函数常量变成闭包值
    CALL_VALUE = 63     # arg=argc：调用栈上的闭包值（callee 在实参之下）
    TRY_PUSH = 64       # arg=catch_pc：进入 try 区（设着陆点）
    TRY_POP = 65        # 正常路径退出 try 区（摘着陆点）
    CATCH_BIND = 66     # arg=var_slot：把被抛值绑定到 catch 变量
    THROW = 67          # 抛出栈顶值
    # ---- M4 最小对象模型（0）：字段读写 + 对象方法分派 ----
    # VM（弃管）不实现这些 op；解释器走 AST 不经字节码。三 op 只在
    # AOT 管线（bytecode → lir_lower → cgen）流动。
    FIELD_GET = 68      # arg=fid：栈 [obj] → 弹 obj 压字段值（缺席/非对象 = null）
    FIELD_SET = 69      # arg=fid：栈 [value, obj] → 弹两者写字段（缺席/非对象 = 忽略）
    CALL_OMETHOD = 70   # arg=(func_idx, argc, mid)：对象方法分派调用
                        #   栈序与 CALL 一致（receiver 是 args[0]）；运行期
                        #   receiver 为 VT_OBJ → 类型表方法分派；否则退回
                        #   「receiver 前置」的既有调用定价（内置/用户函数）。
    NULL_MAGIC = 0x4E554C4C
    # 200/201 曾是 RPN_LOOP_START / RPN_LOOP_END，随 RPN 超指令一并删除（SPEC D-03）。
    # 号段保留不再复用，避免旧 .qtc 被误解析。


class VMCode:
    __slots__ = ['instructions', 'constants', 'var_names', 'func_names',
                 'line_numbers', 'source_file', 'local_names', 'is_function',
                 '_const_index', '_var_index', '_func_index',
                 'captured', 'cap_env_idx', 'is_nested',
                 # M4（0）：方法函数的 5 参 clo 形态标记；
                 # 主块的类元数据（cgen 类型表的事实源）
                 'clo_form', 'objmeta']

    def __init__(self):
        self.instructions = []
        self.constants = []
        self.var_names = []
        self.func_names = []
        self.line_numbers = []
        self.source_file = ""
        self.local_names = set()
        self.is_function = False
        self._const_index = {}
        self._var_index = {}
        self._func_index = {}
        # F1 元数据（仅 AOT 编译期使用，不参与 .qtc 序列化）
        self.captured = None
        self.cap_env_idx = None
        self.is_nested = False
        # M4 元数据
        self.clo_form = False
        self.objmeta = None

    def intern_const(self, value):
        key = (type(value), value)
        idx = self._const_index.get(key)
        if idx is not None:
            return idx
        idx = len(self.constants)
        self.constants.append(value)
        self._const_index[key] = idx
        return idx

    def intern_var(self, name):
        idx = self._var_index.get(name)
        if idx is not None:
            return idx
        idx = len(self.var_names)
        self.var_names.append(name)
        self._var_index[name] = idx
        return idx

    def intern_func(self, name):
        idx = self._func_index.get(name)
        if idx is not None:
            return idx
        idx = len(self.func_names)
        self.func_names.append(name)
        self._func_index[name] = idx
        return idx
GLOBAL_BIT = 0x40000000
# ---- F1 闭包：upval 槽编码（与 lir.py 的 GLOBAL_BIT 同一 31 位编号空间）----
# bit29 = UPVAL_BIT；bit20..28 = 向上穿越的激活环境层数（≤511）；bit0..19 = 环境
# 槽下标（每函数独立编号，按捕获名排序定序，两侧编译器无须共享映射）。
UPVAL_BIT = 0x20000000


def upval_make(levels, idx):
    return UPVAL_BIT | ((levels & 0x1FF) << 20) | (idx & 0xFFFFF)


def upval_levels(s):
    return (s >> 20) & 0x1FF


def upval_idx(s):
    return s & 0xFFFFF


def is_upval(s):
    return s >= 0 and bool(s & UPVAL_BIT)


# ---------------------------------------------------------------- 捕获分析
# 纯 AST 预扫（不改 AST）：对每个函数节点求
#   captured = 本函数声明的、被（传递）后代函数引用的名字 —— 进激活环境
#   free     = 本函数用到但未在本函数声明的名字（运行期按祖先帧/全局解析）
# 顶层（main 作用域）的变量永远是全局槽：顶层函数引用它们**不是捕获**，
# 保持既有全局语义（这正是嵌套函数与顶层函数的分界）。
_SCAN_DECL_NODES = ('var_declaration', 'const_declaration',
                    'array_declaration', 'dict_declaration')


def _scan_scope(node, declared, used, nested):
    """单层作用域扫描：不进入嵌套函数体；收集本层声明/使用/嵌套函数节点。"""
    if node is None:
        return
    if isinstance(node, list):
        for x in node:
            _scan_scope(x, declared, used, nested)
        return
    if not isinstance(node, tuple):
        return
    t = node[0]
    if t == 'func_declaration':
        nested.append(node)
        declared.add(node[2])
        return
    if t == 'lambda_expr':
        nested.append(node)
        return
    if t == 'identifier':
        used.add(node[1])
        return
    if t in _SCAN_DECL_NODES:
        declared.add(node[1])
    elif t == 'assignment':
        used.add(node[1])
    elif t == 'function_call':
        used.add(node[1])
    elif t == 'function_ref':
        used.add(node[1])
    elif t == 'for_statement':
        declared.add(node[1])
    elif t == 'try_statement':
        if node[2]:
            declared.add(node[2])
    for child in node[1:]:
        _scan_scope(child, declared, used, nested)


def analyze_captures(ast):
    """全程序捕获分析。返回 {id(函数节点): {'captured','free','nested'}}。

    算法：自底向上单遍。escaped(G) = G 的自由名 ∪ G 的捕获名（祖先视角
    还要继续向上找的家）；F.captured = ⋃escaped(G) ∩ declared(F)。
    """
    info = {}

    def visit_func(node, is_lambda, is_nested):
        declared, used, nested = set(), set(), []
        if is_lambda:
            declared.update(node[1] or [])
            _scan_scope(node[2], declared, used, nested)
        else:
            declared.update(node[3] or [])
            _scan_scope(node[4], declared, used, nested)
        escaped_union = set()
        for g in nested:
            escaped_union |= visit_func(g, g[0] == 'lambda_expr', True)['escaped']
        captured = escaped_union & declared
        free = (used | escaped_union) - declared
        info[id(node)] = {'captured': captured, 'free': free,
                          'nested': is_nested}
        return {'escaped': free | captured}

    body = ast[1] if isinstance(ast, tuple) and ast[0] == 'start' else ast
    _declared, _used, top_funcs = set(), set(), []
    _scan_scope(body, _declared, _used, top_funcs)
    for g in top_funcs:
        visit_func(g, g[0] == 'lambda_expr', False)
    return info


def captures_of_func(node, is_lambda=False):
    """单函数按需分析（解释器运行期用）：返回 (captured, free, escaped)。

    captured = 本函数声明且被（传递）后代引用的名字；free = 需要从祖先/全局
    解析的名字；escaped = 传给上一层的（free | captured）。
    """
    declared, used, nested = set(), set(), []
    if is_lambda:
        declared.update(node[1] or [])
        _scan_scope(node[2], declared, used, nested)
    else:
        declared.update(node[3] or [])
        _scan_scope(node[4], declared, used, nested)
    escaped_union = set()
    for g in nested:
        _c, _f, _e = captures_of_func(g, g[0] == 'lambda_expr')
        escaped_union |= _e
    captured = escaped_union & declared
    free = (used | escaped_union) - declared
    return captured, free, free | captured


class Compiler:
    def __init__(self):
        self.builtin = BuiltFunc()
        self.globals = {}
        self.global_names = []
        self._global_index = {}
        self.functions = {}
        self.base_path = None
        self._binop_map = {
            '+': 1, '-': 2, '*': 3, '/': 4, '%': 5, '^': 6,
            '>': 7, '<': 8, '>=': 9, '<=': 10, '==': 11, '!=': 12,
            'AND': 13, '&&': 13, 'OR': 14, '||': 14,
        }
        self._unop_map = {'UMINUS': 1, '!': 2, 'NOT': 2}
        self.loop_stack = []
        self._for_counter = 0
        # 【G-03 0】三元表达式的临时槽计数（每层嵌套独立命名）
        self._tern_counter = 0
        # ---- F1 闭包：词法帧栈与捕获分析 ----
        self.captures = {}      # id(func/lambda 节点) -> analyze_captures 条目
        self.frames = []        # 每元素: {'declared':set, 'funcs':set, 'env_idx':dict, 'captured':set}
        self.top_vars = set()   # 顶层 var/const 声明名（持函数值时的动态调用判定）
        self._lambda_counter = 0
        # ---- M4 最小对象模型（0）----
        self.classes = {}           # 类名 → {'cid', 'fields': [名], 'methods': {名: mangled}}
        self._obj_field_ids = {}    # 字段名 → 全局 fid（首次出现序）
        self._obj_method_ids = {}   # 方法名 → 全局 mid（首次出现序）
        self._obj_this_counter = 0  # this 改写用的隐藏槽计数

    def set_base_path(self, path):
        self.base_path = os.path.dirname(os.path.abspath(path))

    def _range_is_statically_numeric(self, expr) -> bool:
        if not isinstance(expr, tuple):
            return isinstance(expr, (int, float))
        node_type = expr[0]
        if node_type == 'literal':
            return isinstance(expr[1], (int, float))
        if node_type == 'parenthesized':
            return self._range_is_statically_numeric(expr[1])
        if node_type == 'unary_op' and expr[1] in ('UMINUS',):
            return self._range_is_statically_numeric(expr[2])
        if node_type == 'binary_op' and expr[1] in ('+', '-', '*', '/', '%', '^'):
            return (self._range_is_statically_numeric(expr[2]) and
                    self._range_is_statically_numeric(expr[3]))
        return False

    def _compile_method_call_as_function(self, node, code: VMCode, is_statement=True):
        # 防御式解包：astcomopt 的 _cleanup 会丢 None 子节点（空实参的
        # method_call 从 4 元变 3 元 —— 既有全局行为，全库按防御式索引容忍）
        caller_node = node[1]
        method_name = node[2]
        args = node[3] if len(node) > 3 else None
        self._compile_node(caller_node, code)
        arg_count = 1
        if args:
            for arg in args:
                if arg is not None:
                    self._compile_node(arg, code)
                    arg_count += 1
        # 【M4 0】对象方法分派：receiver 为 VT_OBJ → 类型表方法
        # 分派；否则退回「receiver 前置」的既有调用定价（内置/用户函数）。
        # 栈序与旧 CALL 完全一致（receiver 是 args[0]）—— cgen 的 else
        # 分支发射的就是旧 CALL 的调用形态。
        func_idx = code.intern_func(method_name)
        mid = self._obj_method_id(method_name)
        code.instructions.append((VMInstruction.CALL_OMETHOD, (func_idx, arg_count, mid)))
        if is_statement:
            code.instructions.append((VMInstruction.POP_STMT, None))

    # ---- M4 最小对象模型（0）----
    def _obj_field_id(self, name):
        fid = self._obj_field_ids.get(name)
        if fid is None:
            fid = len(self._obj_field_ids)
            self._obj_field_ids[name] = fid
        return fid

    def _obj_method_id(self, name):
        mid = self._obj_method_ids.get(name)
        if mid is None:
            mid = len(self._obj_method_ids)
            self._obj_method_ids[name] = mid
        return mid

    def _rewrite_this(self, node, this_var):
        """方法/init 体内 `this` → 隐藏接收者槽名（词法改写）。

        遮蔽语义：方法体内的 this 恒指接收者（外层同名变量被遮蔽 —— 与
        Python self 同款语义）。改写成普通局部名后，闭包捕获/SSA/作用域
        机制全部自动正确（捕获 this 的嵌套 lambda 走既有 capture 机制）。
        """
        if isinstance(node, tuple):
            if node and node[0] == 'identifier' and len(node) >= 2 and node[1] == 'this':
                return ('identifier', this_var, node[2] if len(node) > 2 else 0)
            return tuple(self._rewrite_this(x, this_var) for x in node)
        if isinstance(node, list):
            return [self._rewrite_this(x, this_var) for x in node]
        return node

    def _analyze_class_func(self, mangled, params, body):
        """【M4】对类方法/构造器的（this 改写后）体跑捕获分析。

        全局 analyze_captures 只认 func_declaration/lambda 节点，且按
        **节点身份**登记 —— this 改写会重建元组、打散身份。这里对每个
        方法构造合成 func_declaration 节点重跑分析：
          * 方法自身的 captured（后代 lambda 引用的本方法局部/形参）→
            驱动 env 分配（否则 lambda 的 MAKE_CLOSURE 会引用未分配的
            env 槽 —— add5(10) 得 <none> 的根因）；
          * 内层 lambda 的登记（captured/free）合并进 self.captures，
            编译 lambda 时按新身份命中。
        """
        synth = ('func_declaration', 'any_t', mangled, list(params or []),
                 body if isinstance(body, list) else [body])
        caps = analyze_captures(('start', [synth]))
        self.captures.update(caps)
        return caps.get(id(synth)) or {}

    def _compile_class_declaration(self, node, code: VMCode):
        """class → ctor 函数 + 方法函数 + 编译期类登记。

        ctor（`Class__ctor`，普通 4 参用户函数，CALL_USER 静态调用）：
            __qot_this_N = __obj_new(<cid>);
            <字段初始化器按声明序写字段>;
            <init 体（this 已改写）>;
            return __qot_this_N;
        方法（`Class__method`，5 参 clo 形态，类型表函数指针分派）：
            首参 __qot_this_M = 接收者（args[0]，与 receiver 前置约定同构）。
        命名面：mangled 名与用户函数共享函数表 —— 冲突 = 编译期错误
        （同槽双定义比静默合并安全）。
        """
        _, cname, members, _line = (node + (None,) * 3)[:4]
        if cname in self.classes:
            raise ValueError("类 '%s' 重复声明" % cname)
        fields, methods = [], {}
        for m in (members or []):
            if not isinstance(m, tuple) or not m:
                continue
            if m[0] == 'field':
                fname = m[1]
                init_expr = m[2] if len(m) > 2 else None
                if fname in fields or fname in methods:
                    raise ValueError("类 '%s' 的成员 '%s' 重复声明" % (cname, fname))
                fields.append((fname, init_expr))
            elif m[0] == 'method' and len(m) >= 5:
                # strip 后 5 元组（rt/name/params/body），带行号 6 元组
                mname = m[2]
                params = m[3]
                body = m[4]
                if mname in methods or any(f == mname for f, _ in fields):
                    raise ValueError("类 '%s' 的成员 '%s' 重复声明" % (cname, mname))
                methods[mname] = (params or [], body)
        # mangled 名冲突检查（对当前编译单元的函数表）
        ctor_name = '%s__ctor' % cname
        for cand in [ctor_name] + ['%s__%s' % (cname, mn) for mn in methods]:
            if cand in code._func_index:
                raise ValueError(
                    "类 '%s' 的内部名 '%s' 与既有函数冲突（类名/方法名组合"
                    "撞上了用户函数名）" % (cname, cand))
        cid = len(self.classes)
        self.classes[cname] = {'cid': cid, 'fields': [f for f, _ in fields],
                               'methods': {}}
        # ---- ctor ----
        self._obj_this_counter += 1
        ctor_this = '__qot_this_%d' % self._obj_this_counter
        ctor_body = []
        ctor_body.append(('var_declaration', ctor_this,
                          ('function_call', '__obj_new', [('literal', cid)])))
        for fname, init_expr in fields:
            self._obj_field_id(fname)   # fid 登记（cgen 类型表的面）
            ctor_body.append(
                ('field_assignment', ('identifier', ctor_this), fname,
                 init_expr if init_expr is not None else ('literal', None), 0))
        init_params = []
        if 'init' in methods:
            init_params, init_body = methods['init']
            ctor_body.extend(self._rewrite_this(
                init_body if isinstance(init_body, list) else [init_body], ctor_this))
        ctor_body.append(('return_statement', ('identifier', ctor_this)))
        ctor_caps = self._analyze_class_func(ctor_name, init_params, ctor_body)
        const_idx, _ = self._compile_func_value(
            code, ctor_name, init_params, ctor_body,
            captured=ctor_caps.get('captured', ()), nested_flag=False)
        code.instructions.append((VMInstruction.PUSH_CONST, const_idx))
        var_idx = self._var_operand(code, ctor_name, declaring=True)
        code.instructions.append((VMInstruction.STORE_VAR, var_idx))
        # ---- 方法 ----
        for mname, (params, body) in methods.items():
            if mname == 'init':
                continue
            mangled = '%s__%s' % (cname, mname)
            self.classes[cname]['methods'][mname] = mangled
            self._obj_method_id(mname)
            self._obj_this_counter += 1
            meth_this = '__qot_this_%d' % self._obj_this_counter
            mparams = [meth_this] + list(params or [])
            mbody = self._rewrite_this(
                body if isinstance(body, list) else [body], meth_this)
            mcaps = self._analyze_class_func(mangled, mparams, mbody)
            const_idx, _ = self._compile_func_value(
                code, mangled, mparams, mbody,
                captured=mcaps.get('captured', ()),
                nested_flag=False, clo_form=True)
            code.instructions.append((VMInstruction.PUSH_CONST, const_idx))
            var_idx = self._var_operand(code, mangled, declaring=True)
            code.instructions.append((VMInstruction.STORE_VAR, var_idx))
    
    def intern_global(self, name):
        idx = self._global_index.get(name)
        if idx is None:
            idx = len(self.global_names)
            self.global_names.append(name)
            self._global_index[name] = idx
        return idx

    def _var_operand(self, code, name, declaring=False):
        # F1：捕获解析先于局部/全局判定——本函数声明的捕获名走 env（level 0），
        # 祖先帧声明的走 env 链（level ≥ 1）。形参槽仍按原样保留（prologue
        # 由 cgen 绑定后拷进 env，体 内的读写全部经 env）。
        if self.frames:
            fr = self.frames[-1]
            if name in fr['captured']:
                idx = fr['env_idx'].get(name)
                if idx is None:
                    idx = len(fr['env_idx'])
                    fr['env_idx'][name] = idx
                return upval_make(0, idx)
            for lvl in range(2, len(self.frames) + 1):
                anc = self.frames[-lvl]
                if name in anc['captured'] or name in anc['declared']:
                    idx = anc['env_idx'].get(name)
                    if idx is None:
                        idx = len(anc['env_idx'])
                        anc['env_idx'][name] = idx
                    return upval_make(lvl - 1, idx)
        if code.is_function:
            if declaring or name.startswith('__'):
                code.local_names.add(name)
            if name in code.local_names:
                return code.intern_var(name)
        return GLOBAL_BIT | self.intern_global(name)

    def compile_ast(self, ast) -> VMCode:
        self.captures = analyze_captures(ast)
        code = VMCode()
        if isinstance(ast, tuple) and ast[0] == 'start':
            statements = ast[1]
            for stmt in statements:
                self._compile_node(stmt, code)
            code.instructions.append((VMInstruction.PUSH_CONST, None))
            code.instructions.append((VMInstruction.POP_STMT, None))
            code.var_names = self.global_names
        else:
            self._compile_node(ast, code)
        # 【M4】对象元数据挂载：cgen 发射类型表（qotrt_types/foff/mfns）的
        # 唯一事实源。fid/mid 是编译期全局编号（FIELD ops 与 CALL_OMETHOD
        # 的立即数引用它们）；field_ids/method_ids 供 cgen 把名字映射回编号。
        if self.classes:
            code.objmeta = {
                'classes': [
                    (nm, info['fields'],
                     dict(info['methods']))     # 方法名 → mangled 函数名
                    for nm, info in self.classes.items()],
                'field_ids': dict(self._obj_field_ids),
                'method_ids': dict(self._obj_method_ids),
                'nfields': len(self._obj_field_ids),
                'nmethods': len(self._obj_method_ids),
            }
        return code
    
    def _compile_func_value(self, code, func_name, params, body,
                            captured=(), nested_flag=False, clo_form=False):
        """把函数体编译为 VMCode 常量。返回 (const_idx, 外层 env 槽|-1)。

        返回的 env_slot 是【外层代码】里持有环境值的槽号（MAKE_CLOSURE 用）：
        外层函数若被嵌套，其自身 ENV_NEW 已在它编译开始时发射（预扫分析保证
        有捕获后代 ⇒ captured 非空 ⇒ 有 env）；顶层 main 永不装箱 → 恒 -1。
        本函数自身的 env（若需要）在本函数指令 0 处 ENV_NEW 分配。
        """
        func_code = VMCode()
        func_code.source_file = code.source_file
        func_code.intern_func(func_name)
        for fn in code.func_names:
            func_code.intern_func(fn)
        func_code.is_function = True
        is_nested = len(self.frames) > 0
        needs_env = is_nested or nested_flag or bool(captured)
        # 【M4】clo_form：方法函数的 5 参签名标记（类型表函数指针分派的
        # 签名同构要求）。不发 ENV_NEW、不分配 env —— envp 恒不被解引用。
        if clo_form:
            func_code.clo_form = True
        for param in params:
            func_code.local_names.add(param)
            func_code.intern_var(param)
        cap_sorted = sorted(captured)
        if needs_env:
            env_local = func_code.intern_var('__env__')
            func_code.local_names.add('__env__')
            func_code.instructions.append(
                (VMInstruction.ENV_NEW, (env_local, len(cap_sorted))))
            func_code.captured = cap_sorted
            func_code.cap_env_idx = {nm: k for k, nm in enumerate(cap_sorted)}
            # 捕获【形参】的初始化拷贝：prologue 已按槽绑定 s[k]（cgen），
            # 体 内读写全走 env —— 这里把形参值抄进 env（否则捕获形参恒
            # NONE，clo_lambda_captures 实证）。非形参捕获名由体 内的
            # var_declaration 经 upval 写入，无需处理。
            for nm in cap_sorted:
                if nm in func_code._var_index:
                    pslot = func_code._var_index[nm]
                    func_code.instructions.append(
                        (VMInstruction.PUSH_VAR, pslot))
                    func_code.instructions.append(
                        (VMInstruction.STORE_VAR,
                         upval_make(0, func_code.cap_env_idx[nm])))
        self.frames.append({'declared': set(params), 'funcs': set(),
                            'env_idx': dict(getattr(func_code,
                                                    'cap_env_idx', {}) or {}),
                            'captured': set(captured)})
        try:
            if isinstance(body, list):
                for stmt in body:
                    if stmt:
                        self._compile_node(stmt, func_code)
            else:
                self._compile_node(body, func_code)
            if not func_code.instructions or \
                    func_code.instructions[-1][0] != VMInstruction.RETURN:
                func_code.instructions.append((VMInstruction.PUSH_CONST,
                                               VMInstruction.NULL_MAGIC))
                func_code.instructions.append((VMInstruction.RETURN, None))
        finally:
            self.frames.pop()
        const_idx = code.intern_const(func_code)
        outer_env = -1
        if is_nested:
            outer_env = code.intern_var('__env__')
        return const_idx, outer_env

    def _compile_loop(self, code, condition, body_list, inc_stmt):
        """统一循环编译（G-01 continue 的前提）。

        形态：  loop_start: cond; JUMP_IF_FALSE → end
                            body...
                cont:       inc（for 的步进；while 传 None → cont == loop_start）
                            JUMP loop_start
                end:
        stop    → JUMP end（BREAK 回填）
        continue → JUMP cont
        把 inc 从 body 里挪出来是**必须**的：旧实现把步进塞在 body 末尾，
        continue 若跳到条件就会跳过步进 → 死循环。
        """
        loop_start = len(code.instructions)
        self._compile_node(condition, code)
        jump_out_pos = len(code.instructions)
        code.instructions.append((VMInstruction.JUMP_IF_FALSE, 0))
        self.loop_stack.append({
            'start': loop_start,
            'jump_out_pos': jump_out_pos,
            'break_positions': [],
            'continue_positions': []
        })
        for stmt in (body_list or []):
            if stmt:
                self._compile_node(stmt, code)
        cont_target = len(code.instructions)
        if inc_stmt is not None:
            self._compile_node(inc_stmt, code)
        else:
            cont_target = loop_start
        code.instructions.append((VMInstruction.JUMP, loop_start))
        loop_end = len(code.instructions)
        loop_info = self.loop_stack.pop()
        code.instructions[loop_info['jump_out_pos']] = (VMInstruction.JUMP_IF_FALSE, loop_end)
        for break_pos in loop_info['break_positions']:
            code.instructions[break_pos] = (VMInstruction.BREAK, loop_end)
        for cont_pos in loop_info.get('continue_positions', []):
            code.instructions[cont_pos] = (VMInstruction.JUMP, cont_target)

    # ---- for-in 脱糖（G-04/M3，0）----
    @staticmethod
    def _for_step_value(step_expr):
        """步长的编译期数值（sema 已立法必须静态；此处防御性复算）。"""
        from grammar import statically_numeric_value
        v = statically_numeric_value(step_expr)
        if v is None or v == 0:
            raise ValueError("for 的 by 步长必须是编译期非零数字（sema 已立法，此处防御）")
        return v

    def _compile_for_plain(self, code, var_name, range_expr, body_list):
        """既有形态 `for (x in E)`（M3 统一版）。

        与旧实现的差异只有一处：序列分支先过 `__for_keys`（dict → 键数组
        物化，其余透传）—— `for (k in d)` 从「静默零次」变为「按槽序迭代键」，
        与官方姿势 `for (k in dict_keys(d))` 同源同价。数字分支不变
        （__iter_len 原值透传 + idx < limit）。
        """
        self._for_counter += 1
        unique_id = self._for_counter
        range_tmp = f'__for_range_{unique_id}'
        self._compile_node(('var_declaration', range_tmp, range_expr), code)
        # 静态数字快路径（既有实现原样保留）：字面量范围不发运行期双分支，
        # 循环体只编译一份 —— 首版疏忽（恒双分支）炸了 unbox/arena/liveness
        # 的全部循环结构单测（2 个自然循环 vs 1、b3a 插桩被否决），性能面
        # 也回退（体量翻倍）。静态可判 → 单循环。
        if self._range_is_statically_numeric(range_expr):
            idx_tmp = f'__for_idx_{unique_id}'
            self._compile_node(('var_declaration', idx_tmp, ('literal', 0)), code)
            limit_tmp = f'__for_limit_{unique_id}'
            self._compile_node(
                ('var_declaration', limit_tmp,
                 ('function_call', '__iter_len', [('identifier', range_tmp)])), code)
            condition = ('binary_op', '<', ('identifier', idx_tmp),
                         ('identifier', limit_tmp))
            inc_idx = ('assignment', idx_tmp,
                       ('binary_op', '+', ('identifier', idx_tmp), ('literal', 1)))
            numeric_body = [('assignment', var_name, ('identifier', idx_tmp))]
            numeric_body.extend(body_list)
            self._compile_loop(code, condition, numeric_body, inc_idx)
            return
        # 运行期数字/序列双分支（E 无法静态定型）
        isnum_tmp = f'__for_isnum_{unique_id}'
        self._compile_node(
            ('var_declaration', isnum_tmp,
             ('function_call', '__is_number', [('identifier', range_tmp)])), code)
        self._compile_node(('identifier', isnum_tmp), code)
        jf_pos = len(code.instructions)
        code.instructions.append((VMInstruction.JUMP_IF_FALSE, 0))
        # 数字分支：计数器从 0，cond idx < limit（__iter_len 数字原值透传）
        idx_tmp = f'__for_idx_{unique_id}'
        self._compile_node(('var_declaration', idx_tmp, ('literal', 0)), code)
        limit_tmp = f'__for_limit_{unique_id}'
        self._compile_node(
            ('var_declaration', limit_tmp,
             ('function_call', '__iter_len', [('identifier', range_tmp)])), code)
        condition = ('binary_op', '<', ('identifier', idx_tmp), ('identifier', limit_tmp))
        inc_idx = ('assignment', idx_tmp,
                   ('binary_op', '+', ('identifier', idx_tmp), ('literal', 1)))
        numeric_body = [('assignment', var_name, ('identifier', idx_tmp))]
        numeric_body.extend(body_list)
        self._compile_loop(code, condition, numeric_body, inc_idx)
        jmp_end_pos = len(code.instructions)
        code.instructions.append((VMInstruction.JUMP, 0))
        else_pos = len(code.instructions)
        code.instructions[jf_pos] = (VMInstruction.JUMP_IF_FALSE, else_pos)
        # 序列分支（M3：__for_keys 包一层）
        seq_tmp = f'__for_seq_{unique_id}'
        self._compile_node(
            ('var_declaration', seq_tmp,
             ('function_call', '__for_keys', [('identifier', range_tmp)])), code)
        sidx_tmp = f'__for_sidx_{unique_id}'
        self._compile_node(('var_declaration', sidx_tmp, ('literal', 0)), code)
        slimit_tmp = f'__for_slimit_{unique_id}'
        self._compile_node(
            ('var_declaration', slimit_tmp,
             ('function_call', '__iter_len', [('identifier', seq_tmp)])), code)
        scond = ('binary_op', '<', ('identifier', sidx_tmp), ('identifier', slimit_tmp))
        sinc = ('assignment', sidx_tmp,
                ('binary_op', '+', ('identifier', sidx_tmp), ('literal', 1)))
        seq_body = [('assignment', var_name,
                     ('array_index', seq_tmp, ('identifier', sidx_tmp)))]
        seq_body.extend(body_list)
        self._compile_loop(code, scond, seq_body, sinc)
        end_pos = len(code.instructions)
        code.instructions[jmp_end_pos] = (VMInstruction.JUMP, end_pos)

    def _compile_for_range(self, code, var_name, range_expr, body_list):
        """G-04：`for (x in A..B [by S])` —— 数值区间，半开（B 不含）。

        * 无 by → 步长 +1：A..B 升序，B <= A → 空区间（与 `for (x in N)` ≡
          `0..N` 完全一致）；
        * by S → S 符号定方向：S>0 升序（i < B），S<0 降序（i > B）；小数
          步长合法（0..1 by 0.25）；by 0 / 非字面量 → sema 编译期致命（G-10 律）；
        * 非静态数字边界 → `__is_number` 守卫（false = 零次迭代，不做字符串
          比较的未定价行为，两侧同律）；
        * 计数器走隐藏槽 `__for_i_N`（体内改循环变量不影响计数 —— 与既有
          for 脱糖同一纪律）。
        """
        # 兼容带行号（5 元）与 strip_line_numbers 后（4 元）两种形态：
        # strip 只剥尾部的行号 int，step 恒在 index 3。
        start_expr = range_expr[1]
        end_expr = range_expr[2]
        step_expr = range_expr[3] if len(range_expr) >= 4 else None
        self._for_counter += 1
        unique_id = self._for_counter
        step_v = 1.0
        if step_expr is not None:
            step_v = float(self._for_step_value(step_expr))
        start_tmp = f'__for_start_{unique_id}'
        self._compile_node(('var_declaration', start_tmp, start_expr), code)
        end_tmp = f'__for_end_{unique_id}'
        self._compile_node(('var_declaration', end_tmp, end_expr), code)
        idx_tmp = f'__for_i_{unique_id}'
        self._compile_node(('var_declaration', idx_tmp, ('identifier', start_tmp)), code)
        cmp_op = '<' if step_v > 0 else '>'
        condition = ('binary_op', cmp_op, ('identifier', idx_tmp), ('identifier', end_tmp))
        inc = ('assignment', idx_tmp,
               ('binary_op', '+', ('identifier', idx_tmp), ('literal', step_v)))
        full_body = [('assignment', var_name, ('identifier', idx_tmp))]
        full_body.extend(body_list)
        if self._range_is_statically_numeric(start_expr) and \
                self._range_is_statically_numeric(end_expr):
            self._compile_loop(code, condition, full_body, inc)
        else:
            ok_tmp = f'__for_ok_{unique_id}'
            ok_expr = ('binary_op', '&&',
                       ('function_call', '__is_number', [('identifier', start_tmp)]),
                       ('function_call', '__is_number', [('identifier', end_tmp)]))
            self._compile_node(('var_declaration', ok_tmp, ok_expr), code)
            self._compile_node(('identifier', ok_tmp), code)
            jf_pos = len(code.instructions)
            code.instructions.append((VMInstruction.JUMP_IF_FALSE, 0))
            self._compile_loop(code, condition, full_body, inc)
            end_pos = len(code.instructions)
            code.instructions[jf_pos] = (VMInstruction.JUMP_IF_FALSE, end_pos)

    def _compile_for_stepped(self, code, var_name, range_expr, body_list):
        """G-04：`for (x in E by S)` —— 序列/数字按步长迭代。

        * 数字 E ≡ `0..E by S`（S<0 且 E>0 → 空区间；E<0 → 从 0 降到 E+1）；
        * 序列（数组/字符串/字典键）：S>0 从 0 正向跳步；S<0 从 len-1 倒序
          跳步（字典经 __for_keys 物化键数组）；
        * S 必须是非零整数字面量（sema 致命立法；此处防御）。
        """
        # 兼容带行号（4 元）/ strip 后（3 元）两种形态，base/step 恒在 1/2。
        base_expr = range_expr[1]
        step_expr = range_expr[2]
        self._for_counter += 1
        unique_id = self._for_counter
        step_v = self._for_step_value(step_expr)
        if float(step_v) != int(step_v):
            raise ValueError("for 序列步长必须是编译期非零整数（sema 已立法，此处防御）")
        step_v = int(step_v)
        range_tmp = f'__for_range_{unique_id}'
        self._compile_node(('var_declaration', range_tmp, base_expr), code)
        # 运行期数字/序列双分支（与 plain 形态同一模式）
        isnum_tmp = f'__for_isnum_{unique_id}'
        self._compile_node(
            ('var_declaration', isnum_tmp,
             ('function_call', '__is_number', [('identifier', range_tmp)])), code)
        self._compile_node(('identifier', isnum_tmp), code)
        jf_pos = len(code.instructions)
        code.instructions.append((VMInstruction.JUMP_IF_FALSE, 0))
        # 数字分支：0..E by S（计数器从 0）
        idx_tmp = f'__for_idx_{unique_id}'
        self._compile_node(('var_declaration', idx_tmp, ('literal', 0)), code)
        ncmp = '<' if step_v > 0 else '>'
        ncond = ('binary_op', ncmp, ('identifier', idx_tmp), ('identifier', range_tmp))
        ninc = ('assignment', idx_tmp,
                ('binary_op', '+', ('identifier', idx_tmp), ('literal', step_v)))
        nbody = [('assignment', var_name, ('identifier', idx_tmp))]
        nbody.extend(body_list)
        self._compile_loop(code, ncond, nbody, ninc)
        jmp_end_pos = len(code.instructions)
        code.instructions.append((VMInstruction.JUMP, 0))
        else_pos = len(code.instructions)
        code.instructions[jf_pos] = (VMInstruction.JUMP_IF_FALSE, else_pos)
        # 序列分支：__for_keys 物化 + 下标跳步（S<0 从 len-1 倒序）
        seq_tmp = f'__for_seq_{unique_id}'
        self._compile_node(
            ('var_declaration', seq_tmp,
             ('function_call', '__for_keys', [('identifier', range_tmp)])), code)
        limit_tmp = f'__for_limit_{unique_id}'
        self._compile_node(
            ('var_declaration', limit_tmp,
             ('function_call', '__iter_len', [('identifier', seq_tmp)])), code)
        sidx_tmp = f'__for_sidx_{unique_id}'
        if step_v > 0:
            self._compile_node(('var_declaration', sidx_tmp, ('literal', 0)), code)
            scond = ('binary_op', '<', ('identifier', sidx_tmp), ('identifier', limit_tmp))
        else:
            init = ('binary_op', '-', ('identifier', limit_tmp), ('literal', 1))
            self._compile_node(('var_declaration', sidx_tmp, init), code)
            scond = ('binary_op', '>=', ('identifier', sidx_tmp), ('literal', 0))
        sinc = ('assignment', sidx_tmp,
                ('binary_op', '+', ('identifier', sidx_tmp), ('literal', step_v)))
        sbody = [('assignment', var_name,
                  ('array_index', seq_tmp, ('identifier', sidx_tmp)))]
        sbody.extend(body_list)
        self._compile_loop(code, scond, sbody, sinc)
        end_pos = len(code.instructions)
        code.instructions[jmp_end_pos] = (VMInstruction.JUMP, end_pos)

    def _compile_node(self, node, code: VMCode, is_lvalue=False):
        if node is None:
            code.instructions.append((VMInstruction.PUSH_CONST, None))
            return
        
        if not isinstance(node, tuple):
            code.instructions.append((VMInstruction.PUSH_CONST, node))
            return
        
        node_type = node[0]
        
        if node_type == 'literal':
            value = node[1]
            # 【S-14 终审 0】字符串字面量保持字符串（不再尝试转数字）。
            # 旧定价（"123"→123 参与 + 得 128）在语言层静默改写值的类型，
            # 三次咬人（b1d 负载失效 / FFI str 码拒收 / std_net 端口错型）后
            # 于 M5 命名规范化终审废除；显式转换 = str_to_num/str_to_int/to_num
            # （既有原语）。既定分歧 D-09（interp 对 "9"+"a" 报 2002、VM 拼接）
            # 随之消亡：三引擎现在一致拼接。
            if value is None:
                code.instructions.append((VMInstruction.PUSH_CONST, VMInstruction.NULL_MAGIC))
            else:
                const_idx = code.intern_const(value)
                code.instructions.append((VMInstruction.PUSH_CONST, const_idx))
        
        elif node_type == 'identifier':
            var_name = node[1]
            var_idx = self._var_operand(code, var_name)

            if is_lvalue:
                code.instructions.append((VMInstruction.PUSH_CONST, var_idx))
            else:
                code.instructions.append((VMInstruction.PUSH_VAR, var_idx))
        
        elif node_type == 'binary_op':
            op = node[1] if len(node) > 1 else None
            left = node[2] if len(node) > 2 else None
            right = node[3] if len(node) > 3 else None
            line = node[4] if len(node) > 4 else 0
            self._compile_node(left, code)
            self._compile_node(right, code)
            op_code = self._binop_map.get(op, 0)
            code.instructions.append((VMInstruction.BINARY_OP, op_code))
        
        elif node_type == 'unary_op':
            _, op, expr = node
            self._compile_node(expr, code)
            op_code = self._unop_map.get(op, 0)
            code.instructions.append((VMInstruction.UNARY_OP, op_code))
        
        elif node_type == 'parenthesized':
            _, expr = node
            self._compile_node(expr, code)
        
        elif node_type == 'var_declaration':
            var_name = node[1] if len(node) > 1 else None
            expr = node[2] if len(node) > 2 else None
            self._compile_node(expr, code)
            var_idx = self._var_operand(code, var_name, declaring=True)
            code.instructions.append((VMInstruction.STORE_VAR, var_idx))
            if not self.frames:
                self.top_vars.add(var_name)
        
        elif node_type == 'annotation':
            pass
        
        elif node_type == 'static_method_call':
            _, left_expr, method_name, args = node
            self._compile_node(left_expr, code)
            arg_count = 1  
            if args:
                for arg in args:
                    if arg is not None:
                        self._compile_node(arg, code)
                        arg_count += 1
            
            func_idx = code.intern_func(method_name)
            code.instructions.append((VMInstruction.CALL_STATIC, (func_idx, arg_count)))
        
        elif node_type == 'static_method_call_statement':
            _, call_node = node
            self._compile_node(call_node, code)
            code.instructions.append((VMInstruction.POP_STMT, None))
        
        elif node_type == 'static_access':
            _, left_expr, member_name = node
            self._compile_node(left_expr, code)
            const_idx = code.intern_const(member_name)
            code.instructions.append((VMInstruction.PUSH_CONST, const_idx))
            code.instructions.append((VMInstruction.INDEX_GET, None))
        
        elif node_type == 'const_declaration':
            _, var_name, expr = node
            self._compile_node(expr, code)
            var_idx = self._var_operand(code, var_name, declaring=True)
            code.instructions.append((VMInstruction.STORE_CONST, var_idx))
            if not self.frames:
                self.top_vars.add(var_name)
        
        elif node_type == 'assignment':
            _, var_name, expr = node
            self._compile_node(expr, code)
            var_idx = self._var_operand(code, var_name)
            code.instructions.append((VMInstruction.STORE_VAR, var_idx))
        elif node_type == 'array':
            _, elements = node
            count = len(elements) if elements else 0
            if elements:
                for elem in elements:
                    self._compile_node(elem, code)
            code.instructions.append((VMInstruction.MAKE_ARRAY, count))
        
        elif node_type == 'array_declaration':
            _, var_name, elements = node
            count = len(elements) if elements else 0
            if elements:
                for elem in elements:
                    self._compile_node(elem, code)
            code.instructions.append((VMInstruction.MAKE_ARRAY, count))
            var_idx = self._var_operand(code, var_name, declaring=True)
            code.instructions.append((VMInstruction.STORE_VAR, var_idx))
            if not self.frames:
                self.top_vars.add(var_name)
        
        elif node_type == 'dict':
            _, entries = node
            count = len(entries) if entries else 0
            if entries:
                for key_node, value_node in entries:
                    self._compile_node(key_node, code)
                    self._compile_node(value_node, code)
            code.instructions.append((VMInstruction.MAKE_DICT, count))
        
        elif node_type == 'dict_declaration':
            _, var_name, entries = node
            count = len(entries) if entries else 0
            if entries:
                for key_node, value_node in entries:
                    self._compile_node(key_node, code)
                    self._compile_node(value_node, code)
            code.instructions.append((VMInstruction.MAKE_DICT, count))
            var_idx = self._var_operand(code, var_name, declaring=True)
            code.instructions.append((VMInstruction.STORE_VAR, var_idx))
            if not self.frames:
                self.top_vars.add(var_name)
        
        elif node_type == 'array_index':
            _, array_name, index_expr = node
            self._compile_node(('identifier', array_name), code)
            self._compile_node(index_expr, code)
            code.instructions.append((VMInstruction.INDEX_GET, None))
        
        elif node_type == 'array_index_chain':
            _, base, index_expr = node
            self._compile_node(base, code)
            self._compile_node(index_expr, code)
            code.instructions.append((VMInstruction.INDEX_GET, None))
        
        elif node_type == 'array_assignment':
            _, array_part, value_expr = node
            self._compile_node(value_expr, code)
            if isinstance(array_part, tuple):
                if array_part[0] == 'array_index_chain':
                    self._compile_array_index_chain_for_assign(array_part, code)
                else:
                    self._compile_array_index_for_assign(array_part, code)
            else:
                self._compile_array_index_for_assign(array_part, code)
            code.instructions.append((VMInstruction.INDEX_SET, None))
        
        elif node_type == 'function_call':
            func_name = node[1] if len(node) > 1 else None
            args = node[2] if len(node) > 2 else None
            line = node[3] if len(node) > 3 else 0
            # 【M4】类名调用 = 构造 → 重写为 ctor 函数（CALL_USER 静态调用）。
            # 类登记先于调用（顶层声明序），编译期即可判定。
            if func_name in self.classes:
                ctor_name = '%s__ctor' % func_name
                arg_count = 0
                if args:
                    for arg in args:
                        if arg is not None:
                            self._compile_node(arg, code)
                            arg_count += 1
                func_idx = code.intern_func(ctor_name)
                code.instructions.append((VMInstruction.CALL, (func_idx, arg_count)))
                return
            # F1：词法帧内的名字（局部变量持函数值 / 祖先帧捕获的闭包）或
            # 顶层 var 声明的函数值（全局可见，任意深度可调）→ CALL_VALUE
            # 动态调用。内置名不参与遮蔽（保持既有行为）。
            is_dynamic = False
            if func_name not in self.builtin.functions:
                if self.frames:
                    for fr in reversed(self.frames):
                        if func_name in fr['declared']:
                            is_dynamic = func_name not in fr['funcs']
                            break
                    else:
                        is_dynamic = func_name in self.top_vars
                elif func_name in self.top_vars:
                    is_dynamic = True
            arg_count = 0
            if args:
                for arg in args:
                    if arg is not None:
                        self._compile_node(arg, code)
                        arg_count += 1
            if is_dynamic:
                # CALL_VALUE 直接引用 callee 槽（不占模拟栈）：语义 =
                # callee 槽的值 + argc 个实参（栈序与 CALL 一致）。
                var_idx = self._var_operand(code, func_name)
                code.instructions.append((VMInstruction.CALL_VALUE,
                                          (var_idx, arg_count)))
            else:
                func_idx = code.intern_func(func_name)
                code.instructions.append((VMInstruction.CALL, (func_idx, arg_count)))
        
        elif node_type == 'function_call_statement':
            _, call_node = node
            self._compile_node(call_node, code)
            code.instructions.append((VMInstruction.POP_STMT, None))
        
        elif node_type == 'method_call':
            self._compile_method_call_as_function(node, code, is_statement=False)
        
        elif node_type == 'method_call_statement':
            if len(node) >= 2:
                method_call_node = node[1]
                self._compile_method_call_as_function(method_call_node, code, is_statement=True)
        
        elif node_type == 'class_declaration':
            # 【M4】类 → ctor + 方法函数 + 编译期登记（脱糖进既有函数机制）
            self._compile_class_declaration(node, code)

        elif node_type == 'field_access':
            # 【M4】obj->f：读字段（缺席/非对象 = null，两侧同律）
            self._compile_node(node[1], code)
            fid = self._obj_field_id(node[2])
            code.instructions.append((VMInstruction.FIELD_GET, fid))

        elif node_type == 'field_assignment':
            # 【M4】obj->f = v：写字段（缺席/非对象 = 忽略，两侧同律）。
            # 栈序与 INDEX_SET 同族：[value, obj] → FIELD_SET 弹 obj、value。
            self._compile_node(node[3], code)
            self._compile_node(node[1], code)
            fid = self._obj_field_id(node[2])
            code.instructions.append((VMInstruction.FIELD_SET, fid))

        elif node_type == 'ternary_expr':            # 【G-03 0】三元 `cond ? a : b` → 跳转 + 临时槽形态：
            #     cond; JUMP_IF_FALSE L_else; then; STORE __tern_N; JUMP L_end;
            #   L_else: else; STORE __tern_N;
            #   L_end:  PUSH_VAR __tern_N
            # 为什么用临时槽而不是"分支各留一值在栈上汇合"：lir_lower 的
            # 结构化护栏在跳转目标处记录栈快照，两条路径的临时值槽号不同
            # → 汇合点栈不一致 → LowerError。临时槽形态让两条路径在汇合点
            # 都是空栈，快照一致 ✓；代价 = 每分支一次 MOV_V（f64 pass 的
            # forward_stores/拷贝传播可消）。VM 侧零新 opcode。
            _, t_cond, t_then, t_else = node[0], node[1], node[2], node[3]
            self._tern_counter += 1
            tern_tmp = '__tern_%d' % self._tern_counter
            self._compile_node(t_cond, code)
            jf_pos = len(code.instructions)
            code.instructions.append((VMInstruction.JUMP_IF_FALSE, 0))
            self._compile_node(t_then, code)
            code.instructions.append(
                (VMInstruction.STORE_VAR, self._var_operand(code, tern_tmp, declaring=True)))
            jmp_pos = len(code.instructions)
            code.instructions.append((VMInstruction.JUMP, 0))
            else_pos = len(code.instructions)
            code.instructions[jf_pos] = (VMInstruction.JUMP_IF_FALSE, else_pos)
            self._compile_node(t_else, code)
            code.instructions.append(
                (VMInstruction.STORE_VAR, self._var_operand(code, tern_tmp)))
            end_pos = len(code.instructions)
            code.instructions[jmp_pos] = (VMInstruction.JUMP, end_pos)
            code.instructions.append((VMInstruction.PUSH_VAR,
                                      self._var_operand(code, tern_tmp)))
        
        elif node_type == 'if_statement':
            condition = node[1]
            if_block = node[2]
            eif_blocks = node[3] if len(node) > 3 else None
            else_block = node[4] if len(node) > 4 else None
            line = node[-1] if len(node) > 5 else (node[5] if len(node) == 6 else 0)
            self._compile_node(condition, code)
            jump_if_false_pos = len(code.instructions)
            code.instructions.append((VMInstruction.JUMP_IF_FALSE, 0))  
            for stmt in if_block:
                if stmt:
                    self._compile_node(stmt, code)
            jump_to_end_pos = len(code.instructions)
            code.instructions.append((VMInstruction.JUMP, 0))  
            code.instructions[jump_if_false_pos] = (VMInstruction.JUMP_IF_FALSE, len(code.instructions))
            eif_ends = []
            if eif_blocks:
                for eif_clause in eif_blocks:
                    if isinstance(eif_clause, tuple) and eif_clause[0] == 'eif_clause':
                        eif_cond = eif_clause[1]
                        eif_block = eif_clause[2]
                    else:
                        eif_cond, eif_block = eif_clause[0], eif_clause[1]
                    self._compile_node(eif_cond, code)
                    eif_jump_if_false = len(code.instructions)
                    code.instructions.append((VMInstruction.JUMP_IF_FALSE, 0))
                    for stmt in eif_block:
                        if stmt:
                            self._compile_node(stmt, code)
                    eif_jump_to_end = len(code.instructions)
                    code.instructions.append((VMInstruction.JUMP, 0))
                    code.instructions[eif_jump_if_false] = (VMInstruction.JUMP_IF_FALSE, len(code.instructions))
                    eif_ends.append(eif_jump_to_end)
            else_start = len(code.instructions)
            if else_block:
                else_body = else_block[1] if isinstance(else_block, tuple) else else_block
                for stmt in else_body:
                    if stmt:
                        self._compile_node(stmt, code)
            end_pos = len(code.instructions)
            code.instructions[jump_to_end_pos] = (VMInstruction.JUMP, end_pos)
            for eif_end in eif_ends:
                code.instructions[eif_end] = (VMInstruction.JUMP, end_pos)
      
       
        elif node_type == 'while_statement':
            _, condition, body = node
            body_list = body if isinstance(body, list) else [body]
            # 编译形态：(cond) body [inc]。while 的 inc 为空 → 继续点 = 条件
            self._compile_loop(code, condition, body_list, None)
      
        
        elif node_type == 'stop_statement':
            if not self.loop_stack:
                raise ValueError("stop 语句只能在循环内使用")
            loop_info = self.loop_stack[-1]
            break_pos = len(code.instructions)
            code.instructions.append((VMInstruction.BREAK, 0))  
            loop_info['break_positions'].append(break_pos)
        elif node_type == 'continue_statement':
            # G-01：跳到本层循环的"继续点"（for = 步进；while = 条件）。
            # 用 JUMP 占位后回填（lir_lower 已按 JMP 处理，无需新 opcode）。
            if not self.loop_stack:
                raise ValueError("continue 语句只能在循环内使用")
            loop_info = self.loop_stack[-1]
            cont_pos = len(code.instructions)
            code.instructions.append((VMInstruction.JUMP, 0))
            loop_info.setdefault('continue_positions', []).append(cont_pos)
        elif node_type == 'for_statement':
            var_name = node[1] if len(node) > 1 else None
            range_expr = node[2] if len(node) > 2 else None
            body = node[3] if len(node) > 3 else None
            line = node[-1] if len(node) > 4 else (node[4] if len(node) == 5 else 0)
            if var_name and code.is_function:
                code.local_names.add(var_name)
            body_list = body if isinstance(body, list) else [body]
            # 【G-04 0】三种形态分流：区间（A..B by S）/ 步长序列
            # （E by S）/ 既有平面形态（E）。全部在字节码层脱糖进既有 opcode
            # （_compile_loop + BINARY_OP + CALL），LIR/cgen 零改动（宪法）。
            if isinstance(range_expr, tuple) and range_expr[0] == 'range_expr':
                self._compile_for_range(code, var_name, range_expr, body_list)
            elif isinstance(range_expr, tuple) and range_expr[0] == 'stepped_expr':
                self._compile_for_stepped(code, var_name, range_expr, body_list)
            else:
                self._compile_for_plain(code, var_name, range_expr, body_list)

        elif node_type == 'func_declaration':
            _, return_type, func_name, params, body = node
            info = self.captures.get(id(node)) or {}
            const_idx, env_slot = self._compile_func_value(
                code, func_name, params, body,
                captured=info.get('captured', ()),
                nested_flag=bool(info.get('nested')))
            if info.get('nested'):
                # 嵌套函数 = 闭包值（词法作用域）；外层帧登记其词法可见性
                code.instructions.append((VMInstruction.MAKE_CLOSURE,
                                          (const_idx, env_slot)))
                var_idx = self._var_operand(code, func_name, declaring=True)
                code.instructions.append((VMInstruction.STORE_VAR, var_idx))
                fr = self.frames[-1]
                fr['funcs'].add(func_name)
                fr['declared'].add(func_name)
            else:
                # 兼容路径：顶层函数声明保持既有字节码形态（VM 面不变）
                code.instructions.append((VMInstruction.PUSH_CONST, const_idx))
                var_idx = self._var_operand(code, func_name, declaring=True)
                code.instructions.append((VMInstruction.STORE_VAR, var_idx))

        elif node_type == 'lambda_expr':
            _, lam_params, lam_expr = node[0], node[1], node[2]
            self._lambda_counter += 1
            lam_name = '__lambda_%d' % self._lambda_counter
            info = self.captures.get(id(node)) or {}
            # 合成的 return_statement 用 stripped 双元形态（与
            # parse_oning_gram(src, True) 的产物一致，编译器按此形态解包）
            const_idx, env_slot = self._compile_func_value(
                code, lam_name, lam_params or [],
                [('return_statement', lam_expr)],
                captured=info.get('captured', ()),
                nested_flag=bool(info.get('nested')))
            code.instructions.append((VMInstruction.MAKE_CLOSURE,
                                      (const_idx, env_slot)))

        elif node_type == 'try_statement':
            _, try_body, catch_var, catch_body = node
            catch_pos = len(code.instructions)
            code.instructions.append((VMInstruction.TRY_PUSH, 0))
            if isinstance(try_body, list):
                for stmt in try_body:
                    if stmt:
                        self._compile_node(stmt, code)
            else:
                self._compile_node(try_body, code)
            code.instructions.append((VMInstruction.TRY_POP, None))
            end_pos = len(code.instructions)
            code.instructions.append((VMInstruction.JUMP, 0))
            code.instructions[catch_pos] = (VMInstruction.TRY_PUSH,
                                            len(code.instructions))
            if catch_var:
                var_idx = self._var_operand(code, catch_var, declaring=True)
                code.instructions.append((VMInstruction.CATCH_BIND, var_idx))
            if isinstance(catch_body, list):
                for stmt in catch_body:
                    if stmt:
                        self._compile_node(stmt, code)
            else:
                self._compile_node(catch_body, code)
            # 终点锚：catch 体可能正好落在函数末尾（tail RETURN 补写之前），
            # JUMP 目标必须是「存在的 pc」→ 落一个 NOP（lowering 不发射指令
            # 但 b2l 会登记，目标解析到下一条真实指令；函数块尾部补写 RET）。
            code.instructions.append((VMInstruction.NOP, None))
            code.instructions[end_pos] = (VMInstruction.JUMP,
                                          len(code.instructions) - 1)

        elif node_type == 'throw_statement':
            _, throw_expr = node
            self._compile_node(throw_expr, code)
            code.instructions.append((VMInstruction.THROW, None))
        
        elif node_type == 'return_statement':
            _, expr = node
            self._compile_node(expr, code)
            code.instructions.append((VMInstruction.RETURN, None))
        
        elif node_type == 'require_statement':
            _, lib_name = node
            temp_name = lib_name.strip('"')
            temp_name = temp_name.replace('\\', '/')
            temp_name = temp_name.split('/')[-1]
            if '.' in temp_name:
                temp_name = temp_name.split('.')[0]
            module_name = temp_name
            
            code.instructions.append((VMInstruction.REQUIRE, lib_name))
            
            var_idx = self._var_operand(code, module_name, declaring=True)
            code.instructions.append((VMInstruction.STORE_VAR, var_idx))
        
        elif node_type == 'function_ref':
            _, func_name, args = node
            self._compile_node(('identifier', func_name), code)
            count = 1
            for arg in (args or []):
                self._compile_node(arg, code)
                count += 1
            code.instructions.append((VMInstruction.MAKE_ARRAY, count))
        
        elif isinstance(node, list):
            for stmt in node:
                if stmt:
                    self._compile_node(stmt, code)
        else:
            pass
    
    def _compile_array_index_for_assign(self, node, code: VMCode):
        if not isinstance(node, tuple):
            return
        
        node_type = node[0]
        
        if node_type == 'array_index':
            _, array_name, index_expr = node
            self._compile_node(('identifier', array_name), code)
            self._compile_node(index_expr, code)
        elif node_type == 'array_index_chain':
            _, base, index = node
            self._compile_array_index_for_assign(base, code)
            self._compile_node(index, code)
            code.instructions.append((VMInstruction.INDEX_GET, None))
        else:
            self._compile_node(node, code)
    
    def _compile_array_index_chain_for_assign(self, node, code: VMCode):
        if not isinstance(node, tuple):
            return
        node_type = node[0]
        if node_type == 'array_index_chain':
            _, base, index_expr = node
            if isinstance(base, tuple) and base[0] == 'array_index_chain':
                self._compile_array_index_chain_for_assign(base, code)
            else:
                self._compile_array_index_for_assign(base, code)
            code.instructions.append((VMInstruction.INDEX_GET, None))
            self._compile_node(index_expr, code)
        elif node_type == 'array_index':
            _, array_name, index_expr = node
            self._compile_node(('identifier', array_name), code)
            self._compile_node(index_expr, code)
        else:
            self._compile_node(node, code)