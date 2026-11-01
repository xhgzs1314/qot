import base64
import shutil
import sys
import threading
import time
import traceback
import types
import subprocess
from typing import Optional, Tuple, Union
from bytecode import VMCode, VMInstruction, Compiler
from error_system import ErrorCode, get_error_handler
from error_system_integration import record_exception
from token_char import TokenType
from ast_encoder import encode_ast, decode_ast
from grammar import parse_oning_gram, strip_line_numbers
from builtin_func import BuiltFunc
import os
from server import PHPLocalServer
from buildcpp import compile_and_run
import ctypes
import json
import struct
import pickle
import hmac
import hashlib
from pathlib import Path
_dll_handle = None
_dll_functions = {}
SIGNATURE_SEPARATOR = b'||SIGNATURE||'
CHUNK_SIZE = 1024 * 1024 
VERSION_MARKER = b'QOT_SIGN_v1|'
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
            return False, "签名格式错误，不是有效的base64编码"
        if not hmac.compare_digest(signature, expected_signature):
            return False, "签名验证失败，内容可能被篡改"
        decoded_content = package_file_decode(encoded_content)
        return True, decoded_content
    
    except Exception as e:
        return False, f"验证失败: {str(e)}"

class LoopStopException(Exception):
    pass


class LoopContinueException(Exception):
    """G-01 `continue`：跳过本轮剩余语句。

    只在循环体求值处捕获 —— 捕获后**继续下一轮**（for 的步进由解释器自己的
    for 语句推进，不在 body 里，因此天然不会被跳过；native 侧由
    bytecode._compile_loop 把步进放在"继续点"上，两侧同律）。
    """

class ReturnException(Exception):
    def __init__(self, value):
        self.value = value

# ================================================================= F1/F2
# 闭包与异常的解释器侧原语。语义真相源 = 本文件（native 侧 qot_aot_runtime.h
# 是逐位镜像）；两侧一致性由 testing/unit/test_closure_try.py 差分裁判钉住。

class QotThrowException(Exception):
    """throw 语句的求值期载体。只被最近的 try/catch 捕获（与 Loop/Return
    异常互不吞食）；逃逸到顶层 = 确定性契约：stderr [2001] + exit 124。"""
    def __init__(self, value):
        self.value = value


class QotDepthLimitError(Exception):
    """【CP-N3/S-10 对齐 0】递归深度超限的求值期载体。

    契约与 native（qotrt_fail_depth）逐位一致：stderr
    `[错误] [2000] 递归深度超限（depth=N，上限 M）` + exit 124；上限默认
    5000，QOT_MAX_DEPTH 可覆盖（两侧同律）。旧实现把 Python 栈实限
    （~200 层）当语言上限、报英文文案 + exit 1 —— 三方契约分歧（S-10）。"""
    def __init__(self, depth, limit):
        self.depth = depth
        self.limit = limit
        super().__init__('qot throw')

class Box:
    """捕获变量的共享存储单元（按引用捕获的落点，等价 native 的 env 槽）。"""
    __slots__ = ('v',)
    def __init__(self, v=None):
        self.v = v

# QotError / QotClosure 定义在 builtin_func.py（内置 handler 需要引用，
# 且 index → builtin_func 是既有导入方向）；这里再导出为解释器侧名字。
from builtin_func import QotError, QotClosure, format_value_for_output  # noqa: E402
from builtin_func import QotClass, QotObject  # noqa: E402  # M4（0）

class Evaluator:
    def __init__(self):
        self.variables = {}
        self.builtin = BuiltFunc()
        self.functions = {}
        # 【M4 0】类注册表（构造/方法分派）与字段初始化器登记
        self.classes = {}
        self._class_field_inits = {}
        self._init_builtin_functions()
        # P0.1 高阶原语（arr_map/filter/reduce/sort_by）的回调宿主：
        # 解释器是单实例单线程（builtin 回调只可能发生在主回路线内），
        # 模块级注册即安全 —— 不持 evaluator 的调用面（若未来出现）会得到
        # 明确的 TypeError 而不是静默错译。
        try:
            import primitives as _prims
            _prims.set_evaluator(self)
        except Exception:
            pass
        self.modules = {}
        self.base_path = None
        self._type_cache = {}  
        self._type_cache_maxsize = 10000
        self._type_cache_hits = 0
        self._type_cache_misses = 0
        self._visitor_cache = {}
        self._var_cache = {}  
        self._var_version = {} 
        self._var_version_counter = 0
        self._int_float_tuple = (int, float)
        self._str_tuple = (str,)
        self._list_tuple = (list,)
        self._dict_tuple = (dict,)
        self._bool_tuple = (bool,)
        self.excutevisitsee = 0
        self.error_handler = get_error_handler()
        self.current_file = ""
        self.current_function = ""
        self.current_qot_file = ""
        self.current_line = 0
        self.original_map = {}
        self.current_column = 0
        self.current_node = None
        self.line_map = {}
        # ---- F1/F2 状态 ----
        self._func_depth = 0        # 嵌套判定：>0 时 func_declaration = 闭包
        self._captured_stack = []   # 每层激活的捕获名集合（声明语义用）
        self._cap_memo = {}         # id(函数/lambda 节点) -> (captured, free)
        # 【CP-N3/S-10】递归深度上限：与 native（qotrt_max_depth）同律 ——
        # 默认 5000，QOT_MAX_DEPTH 可覆盖；超限 = [2000] 递归深度超限 + 124。
        try:
            self._max_depth = int(os.environ.get('QOT_MAX_DEPTH') or 0) or 5000
        except ValueError:
            self._max_depth = 5000
        self._visit_map = {
            'start': self.visit_start,
            'var_declaration': self.visit_var_declaration,
            'const_declaration': self.visit_const_declaration,
            'assignment': self.visit_assignment,
            'binary_op': self.visit_binary_op,
            'literal': self.visit_literal,
            'identifier': self.visit_identifier,
            'parenthesized': self.visit_parenthesized,
            'array': self.visit_array,
            'function_call': self.visit_function_call,
            'function_call_statement': self.visit_function_call_statement,
            'array_declaration': self.visit_array_declaration,
            'unary_op': self.visit_unary_op,
            'if_statement': self.visit_if_statement,
            'else_clause': self.visit_else_clause,
            'array_index': self.visit_array_index,
            'while_statement': self.visit_while_statement,
            'stop_statement': self.visit_stop_statement,
    'continue_statement': self.visit_continue_statement,
            'func_declaration': self.visit_func_declaration,
            'return_statement': self.visit_return_statement,
            'for_statement': self.visit_for_statement,
            'method_call': self.visit_method_call,
            'method_call_statement': self.visit_method_call_statement,
            'array_assignment': self.visit_array_assignment,
            'array_index_chain': self.visit_array_index_chain,
            'require_statement': self.visit_require_statement,
            'dict': self.visit_dict,
            'dict_declaration': self.visit_dict_declaration,
            'function_ref': self.visit_function_ref,
            'static_access': self.visit_static_access,
            'static_method_call': self.visit_static_method_call,
            'static_method_call_statement': self.visit_static_method_call_statement,
            'annotation': lambda node: 'unknown',
            # F1/F2
            'try_statement': self.visit_try_statement,
            'throw_statement': self.visit_throw_statement,
            'lambda_expr': self.visit_lambda_expr,
            # 【G-03 0】三元：短路求值（与 native 的跳转形态同语义）
            'ternary_expr': self.visit_ternary_expr,
            # 【M4 0】最小对象模型
            'class_declaration': self.visit_class_declaration,
            'field_access': self.visit_field_access,
            'field_assignment': self.visit_field_assignment,
            # 【GOD-SELF-G2 0】编译期宏：仅宏体执行期可达
            # （macro_hook 由 qot_macro.MacroExpander._run_body 挂上，
            # 值语义 = 实参先求值再展开）；主程序 AST 不应含此节点
            # （展开在求值前完成）—— 走到这里 = 展开器漏洞，明确报错。
            'macro_call': self.visit_macro_call,
            'macro_call_statement': self.visit_macro_call_statement,
        }

    def set_base_path(self, path):
        self.base_path = os.path.dirname(os.path.abspath(path))
        self.current_qot_file = os.path.basename(path)
    def get_node_line(self, node):
        if node and isinstance(node, tuple) and len(node) > 0:
            target = node[-2] if len(node) >= 2 else node[-1]
            while isinstance(target, (tuple, list)) and len(target) > 0:
                target = target[-1]
            if isinstance(target, int) and not isinstance(target, bool):
                return target
        return 0
    def visit_static_access(self, node):
        _, left_expr, member_name, line = node
        left_obj = self.visit(left_expr)
        if not isinstance(left_obj, dict):
            self._raise_error(ErrorCode.ERR_TYPE_NOT_MODULE_OR_DICT, f"'{left_obj}' 不是模块或字典，无法使用 ##", node)
        if member_name not in left_obj:
            self._raise_error(ErrorCode.ERR_ATTRIBUTE_NOT_FOUND, f"模块没有成员 '{member_name}'", node)
        return left_obj[member_name]

    def visit_static_method_call(self, node):
        _, left_expr, method_name, args, line = node
        left_obj = self.visit(left_expr)
        if not isinstance(left_obj, dict):
            self._raise_error(ErrorCode.ERR_TYPE_NOT_MODULE_OR_DICT, f"'{left_obj}' 不是模块或字典，无法调用静态方法", node)
        if method_name not in left_obj:
            self._raise_error(ErrorCode.ERR_ATTRIBUTE_NOT_FOUND, f"模块没有成员 '{method_name}'", node)
        func = left_obj[method_name]
        if not callable(func):
            self._raise_error(ErrorCode.ERR_OBJECT_NOT_CALLABLE, f"成员 '{method_name}' 不是可调用的函数", node)
        arg_values = [self.visit(arg) for arg in (args or [])]
        return func(*arg_values)

    def visit_static_method_call_statement(self, node):
        _, call_node,line = node
        return self.visit_static_method_call(call_node)

    def _get_type_name(self, value):
        if value is None:
            return 'null'
        val_id = id(value)
        if val_id in self._type_cache:
            self._type_cache_hits += 1
            return self._type_cache[val_id]
        
        self._type_cache_misses += 1
        if isinstance(value, int):
            type_name = 'int'
        elif isinstance(value, float):
            type_name = 'float'
        elif isinstance(value, str):
            type_name = 'str'
        elif isinstance(value, list):
            type_name = 'list'
        elif isinstance(value, dict):
            type_name = 'dict'
        elif isinstance(value, bool):
            type_name = 'bool'
        elif value is None:
            type_name = 'null'
        else:
            type_name = 'unknown'
        if len(self._type_cache) < self._type_cache_maxsize:
            self._type_cache[val_id] = type_name
        else:
            keys_to_remove = list(self._type_cache.keys())[:self._type_cache_maxsize // 2]
            for k in keys_to_remove:
                del self._type_cache[k]
            self._type_cache[val_id] = type_name
        
        return type_name
    
    def _is_number(self, value):
        if value is None:
            return False
        val_id = id(value)
        if type(value) in (int, float):
            return True
        if val_id in self._type_cache:
            type_name = self._type_cache[val_id]
            return type_name in ('int', 'float')
        result = isinstance(value, self._int_float_tuple)
        if len(self._type_cache) < self._type_cache_maxsize:
            self._type_cache[val_id] = 'int' if isinstance(value, int) else ('float' if isinstance(value, float) else 'other')
        return result
    
    def _is_string(self, value):
        return type(value) is str  
    
    def _is_list(self, value):
        return type(value) is list
    
    def _is_dict(self, value):
        return type(value) is dict
    
    def _is_bool(self, value):
        return type(value) is bool

    def _get_var_fast(self, var_name):
        result = self.variables.get(var_name)
        if result is None:
            self._raise_error(ErrorCode.ERR_NAME_NOT_DEFINED, f"变量 '{var_name}' 未定义")
        value = result[1]
        if type(value) is Box:
            value = value.v
        self._var_cache[var_name] = value
        return value

    def _set_var_fast(self, var_name, value, var_type='var'):
        # F1：赋值打在共享存储上 —— 现存条目是 Box（本层或祖先的捕获槽）就
        # 原地改写（按引用捕获），否则普通覆写。
        cur = self.variables.get(var_name)
        if cur is not None and type(cur[1]) is Box:
            cur[1].v = value
        else:
            self.variables[var_name] = (var_type, value)
        self._var_cache[var_name] = value

    def _cur_captured(self):
        return self._captured_stack[-1] if self._captured_stack else ()

    def _declare_var(self, var_name, value, var_type='var'):
        """声明语义（var/const/数组/字典声明）：本层捕获名打 Box，其余新绑定
        （可遮蔽外层同名 Box —— 词法作用域的正确形态）。"""
        if var_name in self._cur_captured():
            cur = self.variables.get(var_name)
            if cur is not None and type(cur[1]) is Box:
                cur[1].v = value
            else:
                self.variables[var_name] = (var_type, Box(value))
        else:
            self.variables[var_name] = (var_type, value)
        self._var_cache[var_name] = value

    def _captures_of(self, node, is_lambda=False):
        """节点捕获分析（按 id 备忘）。返回 (captured, free)。"""
        key = id(node)
        hit = self._cap_memo.get(key)
        if hit is None:
            from bytecode import captures_of_func
            captured, free, _esc = captures_of_func(node, is_lambda)
            hit = (captured, free)
            self._cap_memo[key] = hit
        return hit
    
    def _invalidate_var_cache(self, var_name=None):
        if var_name:
            self._var_cache.pop(var_name, None)
            self._var_version.pop(var_name, None)
        else:
            self._var_cache.clear()
            self._var_version.clear()

    def visit_require_statement(self, node):
        _, lib_name,line = node
        module_name = os.path.splitext(os.path.basename(lib_name))[0]
        if module_name in self.modules:
            module_obj = self.modules[module_name]
            self._set_var_fast(module_name, module_obj)
            return None

        lib_path = self._resolve_lib_path(lib_name)
        if lib_path.endswith('.dll'):
            module_obj = self._load_dll_as_module(lib_path, module_name)
        else:
            module_obj = self._load_qot_module(lib_path, module_name)

        self.modules[module_name] = module_obj
        self._set_var_fast(module_name, module_obj)
        return None

    def _load_dll_as_module(self, dll_path, module_name):
        dll = ctypes.CDLL(dll_path)
        linker_path = os.path.join(os.path.dirname(dll_path), 'export.func')
        if not os.path.exists(linker_path):
            self._raise_error(ErrorCode.ERR_MODULE_EXPORT_TABLE_INVALID,f"模块 {module_name} 缺少 export.func 导出表")

        secret_key = "yhsndnggc_ab_qguoat"
        valid, result = verify_file_signature(linker_path, secret_key)
        if not valid:
            self._raise_error(ErrorCode.ERR_MODULE_EXPORT_TABLE_INVALID,f"导出表验证失败: {result}")

        content = result.decode('utf-8')
        module_dict = {}

        for line_num, line in enumerate(content.splitlines(), 1):
            line = line.strip()
            if not line:
                continue
            parts = line.split('|')
            if len(parts) != 3:
                self._raise_error(ErrorCode.ERR_MODULE_SIGNATURE_INVALID,f"export.func 第 {line_num} 行格式错误")
            func_name, params_str, ret_type = parts
            params = [p.strip() for p in params_str.split(',')] if params_str and params_str != '...' else []
            is_variadic = (params_str == '...')
            
            try:
                dll_func = getattr(dll, func_name)
            except AttributeError:
                self._raise_error(ErrorCode.ERR_DLL_FUNCTION_NOT_FOUND,f"DLL 中未找到导出函数 {func_name}")
            
            if is_variadic:
                dll_func.argtypes = None
                dll_func.restype = self._convert_to_ctype(ret_type)
                wrapper = self._make_variadic_wrapper(dll_func, ret_type)
            else:
                dll_func.argtypes = self._convert_to_ctypes(params)
                dll_func.restype = self._convert_to_ctype(ret_type)
                wrapper = self._make_dll_wrapper(dll_func, params, ret_type)
            
            module_dict[func_name] = wrapper

        return module_dict

    def _make_variadic_wrapper(self, dll_func, ret_type):
        def wrapper(*args):
            final_args = []
            for arg in args:
                if isinstance(arg, str):
                    final_args.append(arg.encode('utf-8'))
                elif isinstance(arg, bytes):
                    final_args.append(arg)
                elif isinstance(arg, (int, float)):
                    final_args.append(int(arg))
                elif arg is None:
                    final_args.append(0)
                else:
                    final_args.append(arg)
            
            try:
                result = dll_func(*final_args)
            except Exception as e:
                print(f"DLL变参调用失败: {e}, 参数: {final_args}")
                return None
            if ret_type in ('str', 'string'):
                if isinstance(result, bytes):
                    try:
                        return result.decode('utf-8', errors='replace')
                    except:
                        return result
                return str(result) if result else ""
            elif ret_type in ('int', 'dword', 'uint', 'long', 'size_t', 'bool', 'dword'):
                return int(result) if result is not None else 0
            elif ret_type == 'ptr':
                return result
            else:
                return result
        
        return wrapper

    def _make_dll_wrapper(self, dll_func, param_types, ret_type):
        def wrapper(*args):
            final_args = []
            for i, arg in enumerate(args):
                ptype = param_types[i] if i < len(param_types) else 'ptr'
                
                if ptype == 'str' or ptype == 'string':
                    if isinstance(arg, str):
                        final_args.append(arg.encode('utf-8'))
                    elif isinstance(arg, bytes):
                        final_args.append(arg)
                    else:
                        final_args.append(str(arg).encode('utf-8'))
                elif ptype == 'wca' or ptype == 'wstr':
                    if isinstance(arg, str):
                        final_args.append(arg)
                    else:
                        final_args.append(str(arg))
                elif ptype in ('int', 'dword', 'uint', 'long', 'size_t', 'bool'):
                    final_args.append(int(arg) if arg is not None else 0)
                elif ptype in ('ptr', 'hwnd', 'handle', 'hmodule', 'hinstance', 'hdc', 'hkey', 'void'):
                    final_args.append(arg if arg is not None else 0)
                else:
                    final_args.append(arg if arg is not None else 0)
            
            try:
                result = dll_func(*final_args)
            except Exception as e:
                print(f"DLL调用失败: {e}, 参数: {final_args}")
                return None
            if ret_type in ('str', 'string'):
                if isinstance(result, bytes):
                    try:
                        return result.decode('utf-8', errors='replace')
                    except:
                        return result
                return str(result) if result else ""
            elif ret_type in ('int', 'dword', 'uint', 'long', 'size_t', 'bool'):
                return int(result) if result is not None else 0
            elif ret_type == 'ptr':
                return result
            else:  
                return result
        
        return wrapper

    def _load_qot_module(self, qot_path, module_name):
        # 【P1030-4】循环依赖防护（对齐 require_inliner 的显式报错）：
        # 此前 interp 侧递归 require 会无限递归爆栈（RecursionError），
        # native 侧早已显式 ImportError。装载栈以规范化绝对路径判圈。
        real = os.path.normcase(os.path.abspath(qot_path))
        stack = getattr(self, '_module_stack', None)
        if stack is None:
            stack = self._module_stack = []
        if real in stack:
            chain = ' -> '.join(
                [os.path.basename(x) for x in stack] + [module_name])
            self._raise_error(ErrorCode.ERR_MODULE_CIRCULAR_DEP,
                              f"模块循环依赖: {chain}")
        stack.append(real)
        try:
            return self._load_qot_module_inner(qot_path, module_name)
        finally:
            stack.pop()

    def _load_qot_module_inner(self, qot_path, module_name):
        old_vars = self.variables.copy()
        old_funcs = self.functions.copy()
        old_modules = self.modules.copy()
        # module_unit=True：sema 两遍制 —— 顶层函数先注册，模块内前向互调
        # 不再误报未定义（与运行期全量安装 module_funcs 的语义对齐）。
        ast = parse_oning_gram(qot_path, False, True)
        if not ast or ast == 1:
            self._raise_error(ErrorCode.ERR_MODULE_SIGNATURE_INVALID,f"模块 {module_name} 解析失败")
        old_base = self.base_path
        self.base_path = os.path.dirname(os.path.abspath(qot_path))
        try:
            self.evaluate(ast)
        except ReturnException:
            pass
        finally:
            self.base_path = old_base

        # 【P1030-2】函数与变量分账。旧实现按「不在 builtin.functions」过滤
        # —— 模块求值期 visit_func_declaration 会把模块函数 register_function
        # 进 builtin 注册表，函数因此全部被滤掉，module_vars 不含任何函数；
        # 而函数体互调的解析走 self.variables（'function' 条目）+ self.functions
        # （原始 fdef），wrapper 两张表都没装对 → 模块内互调 [3001] 未定义。
        new_vars = {}
        for name, entry in self.variables.items():
            if name in old_vars:
                continue
            typ = entry[0] if isinstance(entry, tuple) and entry else None
            if typ == 'function':
                continue  # 函数走 func_defs/module_funcs，不混进变量表
            new_vars[name] = entry[1] if isinstance(entry, tuple) and len(entry) > 1 else entry
        func_defs = {}
        for name, fdef in self.functions.items():
            if name not in old_funcs:
                func_defs[name] = fdef
        new_funcs = {}
        for name, fdef in func_defs.items():
            new_funcs[name] = self._make_script_function_wrapper(
                name, new_vars, func_defs, fdef)
        self.variables = old_vars
        self.functions = old_funcs
        self.modules = old_modules
        module_obj = {}
        module_obj.update(new_vars)
        module_obj.update(new_funcs)
        return module_obj

    def _make_script_function_wrapper(self, func_name, module_vars, module_funcs, func_def):
        """模块函数的调用包装。

        module_funcs = 名字 → 原始 fdef（params/body/return_type/captured）。
        【P1030-2】调用期安装两张表 + 函数条目：
          * self.variables[fn] = ('function', fn) —— visit_function_call 的
            解析入口（旧版只装 module_vars，函数条目缺失 → 模块内互调
            [3001] 未定义）；
          * self.functions.update(module_funcs) —— 'function' 分支从这里读
            params/body（旧版装的是 wrapper，不是 fdef，读了会崩）。
        全量安装（序无关）= 模块内互调/前向调用与 native 的模块内联语义
        同源（require_inliner：模块顶层名全量导出）。
        """
        def wrapper(*args):
            old_vars = self.variables.copy()
            old_funcs = self.functions.copy()
            try:
                self.variables.update(module_vars)
                for fn in module_funcs:
                    self.variables[fn] = ('function', fn)
                self.functions.update(module_funcs)
                params = func_def['params']
                body = func_def['body']
                if len(args) != len(params):
                    self._raise_error(TypeError,f"函数 '{func_name}' 需要 {len(params)} 个参数，但传入了 {len(args)} 个")
                for param, arg_val in zip(params, args):
                    self.variables[param] = ('param', arg_val)
                result = None
                try:
                    result = self.visit(body)
                except ReturnException as e:
                    result = e.value
                return result
            finally:
                self.variables = old_vars
                self.functions = old_funcs
        return wrapper

    def _register_dll_functions(self, lib_name, dll, lib_path):
        linker_path = os.path.join(os.path.dirname(lib_path), 'export.func')
        if not os.path.exists(linker_path):
            self._raise_error(ErrorCode.ERR_MODULE_LOAD_FAILED,f"模块 {lib_name} 缺少export.func导出表")
        secret_key = "yhsndnggc_ab_qguoat"  
        valid, result = verify_file_signature(linker_path, secret_key)
        if not valid:
            self._raise_error(ErrorCode.ERR_MODULE_EXPORT_TABLE_INVALID,f"导出表验证失败: {result}")
        try:
            content = result.decode('utf-8')
        except UnicodeDecodeError:
            self._raise_error(ErrorCode.ERR_MODULE_LOAD_FAILED,"导出表内容不是有效的UTF-8编码")
            
        lines = [line.strip() for line in content.splitlines() if line.strip()]
        functions = {}
        for line_num, line in enumerate(lines, 1):
            parts = line.split('|')
            if len(parts) != 3:
                self._raise_error(ErrorCode.ERR_MODULE_LOAD_FAILED,f"export.func第{line_num}行格式错误，应为'函数名|参数类型|返回类型'")
            func_name, params_str, return_type = parts
            params = [p.strip() for p in params_str.split(',')] if params_str else []
            functions[func_name] = {
                'params': params,
                'return': return_type
            }
        for func_name, func_info in functions.items():
            try:
                dll_func = getattr(dll, func_name, None)
                if dll_func is None:
                    self._raise_error(ErrorCode.ERR_FUNCTION_NOTFOUND,f"函数 {func_name} 不存在于库中")
                param_types = func_info.get('params', [])
                return_type = func_info.get('return', 'void')
                original_param_types = param_types.copy()
                dll_func.argtypes = self._convert_to_ctypes(param_types)
                dll_func.restype = self._convert_to_ctype(return_type)
                ptr_indices = [i for i, p in enumerate(param_types) if p == 'ptr']
                def create_wrapper(func, param_types_list, ptr_indices_list, ret_type):
                    def wrapper(*args):
                        ptr_vars = []
                        final_args = []
                        user_arg_idx = 0  
                        for i, ptype in enumerate(param_types_list):
                            if ptype == 'ptr':
                                out_var = ctypes.c_size_t(0)
                                ptr_vars.append(out_var)
                                final_args.append(ctypes.byref(out_var))
                            else:
                                if user_arg_idx >= len(args):
                                    final_args.append(0 if ptype == 'size_t' else None)
                                else:
                                    arg = args[user_arg_idx]
                                    if ptype == 'str' and isinstance(arg, str):
                                        final_args.append(arg.encode('utf-8'))
                                    elif ptype == 'str' and isinstance(arg, bytes):
                                        final_args.append(arg)
                                    else:
                                        final_args.append(arg)
                                user_arg_idx += 1
                        result = func(*final_args)
                        if len(ptr_vars) == 1 and ret_type == 'void':
                            return ptr_vars[0].value
                        elif len(ptr_vars) == 1 and ret_type == 'ptr':
                            if result and ptr_vars[0].value > 0:
                                return ctypes.string_at(result, ptr_vars[0].value)
                            return None
                        elif len(ptr_vars) == 2:
                            if result:
                                return (ctypes.string_at(result, ptr_vars[0].value), ptr_vars[1].value)
                            return (None, 0)
                        elif len(ptr_vars) > 0:
                            outputs = [v.value for v in ptr_vars]
                            if ret_type != 'void':
                                if ret_type == 'str' and isinstance(result, bytes):
                                    return (result, *outputs)
                                return (result, *outputs)
                            return tuple(outputs) if len(outputs) > 1 else outputs[0]
                        else:
                            if ret_type == 'str' and isinstance(result, bytes):
                                return result.decode('utf-8', errors='replace')
                            return result
                
                    return wrapper
            
                wrapped_func = create_wrapper(dll_func, param_types, ptr_indices, return_type)
                self.builtin.register_function(
                    func_name, 
                    wrapped_func,  
                    (len(param_types), param_types, return_type)
                )
                self.variables[func_name] = ('builtin', wrapped_func) 
            except Exception as e:
                self._raise_error(ErrorCode.ERR_FUNCTION_DEFINITION_FAILED,f"注册函数 {func_name} 失败: {str(e)}")

    def _convert_to_ctypes(self, types_list):
        if types_list == ['...']:
            return None
        return [self._convert_to_ctype(t) for t in types_list]

    def _convert_to_ctype(self, type_str):
        type_map = {
            'int': ctypes.c_int,
            'float': ctypes.c_float,
            'double': ctypes.c_double,
            'char': ctypes.c_char,
            'void': ctypes.c_void_p,
            'str': ctypes.c_char_p,
            'long': ctypes.c_long,
            'bool': ctypes.c_bool,
            'size_t': ctypes.c_size_t,
            'byte':ctypes.c_byte,
            'wca':ctypes.c_wchar_p,
            'uint64': ctypes.c_ulonglong,
            'ptr': ctypes.POINTER(ctypes.c_size_t), 
            'uint': ctypes.c_uint, 
            'hmodule': ctypes.c_void_p,
            'hwnd': ctypes.c_void_p,
            'dword': ctypes.c_ulong, 
            'hdc': ctypes.c_void_p,
            'hkey': ctypes.c_void_p,
            'int64': ctypes.c_int64,
            'dword_ptr': ctypes.c_ulong,
            'lparam': ctypes.c_void_p,
            'wparam': ctypes.c_void_p,
            'lresult': ctypes.c_long,
            'hinstance': ctypes.c_void_p,
            'hglobal': ctypes.c_void_p,
            'hlocal': ctypes.c_void_p,
            'hfile': ctypes.c_void_p,
            'heap': ctypes.c_void_p,
            'critical_section': ctypes.c_void_p,
            'handle': ctypes.c_void_p,
        }
        if type_str == '...':
            return None
        ctype = type_map.get(type_str)
        if ctype is None :
            self._raise_error(TypeError,f"不支持的类型: {type_str}")
        return ctype

    def _resolve_lib_path(self, lib_name):
        if not self.base_path:
            self.base_path = os.getcwd()
        if os.path.isabs(lib_name):
            candidate = lib_name
        else:
            candidate = os.path.join(self.base_path, lib_name)
        if os.path.exists(candidate) and os.path.isfile(candidate):
            return candidate
        if not candidate.endswith('.qot'):
            script_path = os.path.abspath(__file__)
            script_dir = os.path.dirname(script_path)
            if(not os.path.exists(script_dir+"\\package\\"+lib_name+"\\"+lib_name+".dll")):
                candidate += '.qot'
                if os.path.exists(candidate) and os.path.isfile(candidate):
                    return candidate
            else:
                return script_dir + "\\package\\" + lib_name + "\\"+lib_name+".dll"
        self._raise_error(ErrorCode.ERR_FILE_NOT_FOUND,f"库文件 {lib_name} 不存在")

    def _init_builtin_functions(self):
        for func_name in self.builtin.functions.keys():
            self.variables[func_name] = ('builtin', self.builtin.get_handler(func_name))

    def evaluate(self, ast):
        if not ast:
            return None
        return self.visit(ast)

    def visit_return_statement(self, node):
        _, expr,line = node
        value = self.visit(expr)
        raise ReturnException(value)

    def visit_literal(self, node):
        if len(node) == 3:
            _, value, _,line = node
        else:
            _, value,line = node
        if isinstance(value, bytes):
            return value
        # 【S-14 终审 0】字符串字面量保持字符串（与 bytecode 编译器
        # 同步废除编译期数字转换；显式转换 = str_to_num/str_to_int/to_num）。
        return value

    def visit_dict(self, node):
        if len(node) == 4:
            _, entries, _, line = node  
        else:  
            _, entries, line = node     
        
        result = {}
        for entry in entries:
            if len(entry) == 3:
                key_node, value_node, _ = entry
            else:
                key_node, value_node = entry
            key = self.visit(key_node)
            value = self.visit(value_node)
            result[key] = value
        return result

    def visit_dict_declaration(self, node):
        _, var_name, entries,line = node
        # D-05 修复（0）：'dict' 表达式节点是 ('dict', entries, line) 三元组
        # （grammar.p_expression_dict），此处必须传同构节点——传 ('dict', entries) 二元组
        # 会让 visit_dict 的解包 `_, entries, line = node` 崩溃（错误 2005），
        # 导致解释器上任何字典字面量声明都直接失败（VM 路径不受影响）。
        value = self.visit(('dict', entries, line))
        self._declare_var(var_name, value, 'var')
        return value

    def _visit_impl(self, node):
        if not node:
            return None
        if isinstance(node, tuple):
            node_type = node[0]
        else:
            node_type = None
    
        visitor = self._visit_map.get(node_type)
        if visitor is not None:
            return visitor(node)
    
        if isinstance(node, list):
            return self.visit_statement_list(node)
    
        self._raise_error(ErrorCode.ERR_UNKNOWN_NODE_TYPE, f"未知节点类型: {node_type}", node)

    def visit(self, node):
        old_node = self.current_node
        self.current_node = node
        try:
            if hasattr(self, 'executor') and self.executor and isinstance(node, tuple):
                result = self.executor.execute(self, node)
                if result is not None:
                    return result
            return self._visit_impl(node)
        finally:
            self.current_node = old_node
    def _raise_error(self, exception_type, message, node=None):
        target_node = node or self.current_node
        line = self.get_node_line(target_node)
        if line:
            full_message = f"{message}\n  --> {self.current_qot_file}:{line}"
        else:
            full_message = message
        record_exception(exception_type, full_message, line)
    def visit_function_ref(self, node):
        _, func_name, args,line = node
        if func_name not in self.variables:
            self._raise_error(NameError,f"函数 '{func_name}' 未定义")
        testlist = []
        if(args != None):
            for i in args:
                testlist.append(self.visit(i))
        return ('function_ref', func_name, testlist)

    def visit_array_index_chain(self, node):
        current_value = self.visit(node[1])  
        current_index_expr = node[2]
        while isinstance(current_value, tuple) and current_value[0] == 'array_index_chain':
            current_index = self.visit(current_value[2])
            if not self.exists_listdict(current_value, current_index):
                self._raise_error(ErrorCode.ERR_INDEX_OUT_OF_RANGE, f"索引 {current_index} 越界", node)
            current_value = current_value[0][current_index]  
        final_index = self.visit(current_index_expr)
        if not self.exists_listdict(current_value, final_index):
            self._raise_error(ErrorCode.ERR_INDEX_OUT_OF_RANGE, f"索引 {final_index} 越界", node)
        return current_value[final_index]

    def extract_index_info(self,data):
        print(data)
        result = []
        stack = [data]  
        while stack:
            element = stack.pop()
            if isinstance(element, tuple):
                if len(element) == 2 and element[0] == 'literal':
                    result.append(element)
                else:
                    for item in reversed(element):
                        stack.append(item)
            elif isinstance(element, list):
                for item in reversed(element):
                    stack.append(item)
        return result

    def exists_listdict(self,container, key):
        if isinstance(container, list):
            return isinstance(key, int) and 0 <= key < len(container)
        elif isinstance(container, dict):
            return key in container
        else:
            self._raise_error(TypeError,"只支持列表和字典类型")

    def visit_array_assignment(self, node):
        _, array_part, value_expr, line = node
        indices = []
        current = array_part
        while current[0] == 'array_index_chain':
            index_value = self.visit(current[2])
            indices.insert(0, index_value)  
            current = current[1]
        if current[0] == 'array_index':
            _, var_name, index_expr, _ = current
            index_value = self.visit(index_expr)
            indices.insert(0, index_value)
        else:
            self._raise_error(ErrorCode.ERR_INVALID_ASSIGNMENT, "无法解析数组索引", node)
            return None
        
        if var_name not in self.variables:
            self._raise_error(ErrorCode.ERR_NAME_NOT_DEFINED, f"变量 '{var_name}' 未定义", node)
        
        var_type, root_value = self.variables[var_name]
        if var_type == 'const':
            self._raise_error(ErrorCode.ERR_CONST_MODIFICATION, f"不能修改常量 '{var_name}'", node)
        value = self.visit(value_expr)
        current_container = root_value
        for idx in indices[:-1]:
            if not self.exists_listdict(current_container, idx):
                self._raise_error(ErrorCode.ERR_INDEX_OUT_OF_RANGE, f"索引 {idx} 越界", node)
            current_container = current_container[idx]
        last_idx = indices[-1]
        if not self.exists_listdict(current_container, last_idx):
            if isinstance(current_container, dict):
                current_container[last_idx] = value
                return value
            self._raise_error(ErrorCode.ERR_INDEX_OUT_OF_RANGE, f"索引 {last_idx} 越界", node)
        
        current_container[last_idx] = value
        return value

    def visit_method_call_statement(self, node):
        _, method_call,line = node
        return self.visit_method_call(method_call)

    def visit_ternary_expr(self, node):
        """【G-03 0】`cond ? a : b` —— 短路：只求值命中的分支。
        与 native（跳转 + 临时槽）同语义：truthy 判定走 is_truthy。"""
        _, cond, then_e, else_e, line = node if len(node) == 5 else (None, node[1], node[2], node[3], 0)
        if self.is_truthy(self.visit(cond)):
            return self.visit(then_e)
        return self.visit(else_e)

    def visit_method_call(self, node):
        _, caller_node, method_name, args,line = node
        caller_value = self.visit(caller_node)  
        args_value  = []
        if(args != None):
            args_value = [self.visit(arg) for arg in args if arg is not None]  
        # 【M4 0】对象接收者 → 类型表方法分派（缺席方法 = null，
        # 不回落到内置 —— 与 native 的 VT_OBJ 分支同律）；其余接收者走
        # 既有「receiver 前置」调用定价（零迁移）。
        if isinstance(caller_value, QotObject):
            return self._call_method(caller_value, method_name, args_value, node)
        args_value.insert(0, caller_value)
        return self.builtin.call_function(method_name, *args_value)

    def visit_for_statement(self, node):
        _, var_name, range_expr, body,line = node
        # 【G-04/M3 0】三种形态；语义与 native 脱糖逐条对齐：
        #   区间 A..B [by S]：半开（B 不含），S 符号定方向，非数字边界零次迭代
        #   E by S：数字 ≡ 0..E by S；序列 = __for_keys(E) 的下标跳步（S<0 倒序）
        #   平面 E：数字 while i < E（原值比较，小数 58.457 → 0..58 共 59 次，
        #           与 native __iter_len 透传 + idx < limit 同律）；数组=元素；
        #           字符串 = s[i] 单位；字典 = 键；null/bool/闭包 = 零次。
        # 旧定价（负数报 2020 / 非数字数组报 2021 / int 截断计数）随 M3 统一
        # 对齐 native 发布面（CP-N4 方向；错误码 2020/2021 保留定义但 for 不再使用）。
        if isinstance(range_expr, tuple) and range_expr[0] == 'range_expr':
            iterable = self._for_range_iter(range_expr)
        elif isinstance(range_expr, tuple) and range_expr[0] == 'stepped_expr':
            iterable = self._for_stepped_iter(range_expr)
        else:
            iterable = self._for_plain_iter(self.visit(range_expr))
        pre_loop_vars = set(self.variables.keys())
        result = None
        try:
            for item in iterable:
                self._set_var_fast(var_name, item, 'loop_var')
                try:
                    result = self.visit(body)
                except LoopContinueException:
                    continue   # 步进由 for 语句自己推进 —— 不会死循环
        except LoopStopException:
            pass
        finally:
            if var_name in self.variables:
                del self.variables[var_name]
            post_loop_vars = set(self.variables.keys())
            new_vars_in_loop = post_loop_vars - pre_loop_vars
            for var in new_vars_in_loop:
                if var != var_name:  
                    del self.variables[var]
        return result

    @staticmethod
    def _interp_is_number(v):
        return isinstance(v, (int, float)) and not isinstance(v, bool)

    def _for_range_iter(self, range_expr):
        """`A..B [by S]`：惰性生成器（大区间不物化）。"""
        start_e = range_expr[1]
        end_e = range_expr[2]
        step_e = range_expr[3] if len(range_expr) >= 4 else None
        start_v = self.visit(start_e)
        end_v = self.visit(end_e)
        step_v = 1.0
        if step_e is not None:
            step_v = float(self.visit(step_e))   # sema 立法：静态字面量
        if not self._interp_is_number(start_v) or not self._interp_is_number(end_v):
            return              # 非数字边界 → 零次（与 native __is_number 守卫同律）
        i = start_v
        if step_v > 0:
            while i < end_v:
                yield i
                i += step_v
        else:
            while i > end_v:
                yield i
                i += step_v

    def _for_stepped_iter(self, range_expr):
        """`E by S`：数字 ≡ 0..E by S；序列下标跳步（S<0 倒序）。"""
        base_e = range_expr[1]
        step_e = range_expr[2]
        base_v = self.visit(base_e)
        step_v = int(self.visit(step_e))         # sema 立法：非零整数字面量
        if self._interp_is_number(base_v):
            i = 0
            if step_v > 0:
                while i < base_v:
                    yield i
                    i += step_v
            else:
                while i > base_v:
                    yield i
                    i += step_v
            return
        # 序列：__for_keys 同律（dict → 键；其余透传判定）
        if isinstance(base_v, list):
            seq = base_v
        elif isinstance(base_v, str):
            seq = base_v       # 字符单位（native = 字节；登记分歧族）
        elif isinstance(base_v, dict):
            seq = list(base_v.keys())   # 插入序（native = 槽序；D-JSON-ORDER 族）
        else:
            return              # null/bool/闭包 → 零次
        if step_v > 0:
            j = 0
            while j < len(seq):
                yield seq[j]
                j += step_v
        else:
            j = len(seq) - 1
            while j >= 0:
                yield seq[j]
                j += step_v

    def _for_plain_iter(self, range_value):
        """平面形态 E（M3 统一）。注意：本函数**不得**是生成器（yield 与
        return 值混用会让全部路径退化为 StopIteration —— 首版实测把列表/
        字符串/字典迭代全变成零次）。数字大区间的惰性由独立生成器承担。"""
        if self._interp_is_number(range_value):
            return self._for_numeric_gen(range_value)
        if isinstance(range_value, (list, types.GeneratorType)):
            return range_value
        if isinstance(range_value, str):
            return iter(range_value)     # 字符单位
        if isinstance(range_value, dict):
            return iter(list(range_value.keys()))   # 键
        return iter(())                   # null/bool/闭包/err → 零次

    @staticmethod
    def _for_numeric_gen(limit):
        i = 0
        while i < limit:
            yield i
            i += 1

    def visit_func_declaration(self, node):
        _, return_type, func_name, params, body,line = node
        if self._func_depth > 0:
            # F1：嵌套函数 = 闭包值（词法作用域）。环境 = 创建点可见的全部
            # 自由名的 Box（祖先激活已为其捕获名装箱，这里直接取同一 Box
            # —— 按引用捕获的关键）。
            captured, free = self._captures_of(node)
            env = {}
            for nm in free:
                cur = self.variables.get(nm)
                if cur is not None and type(cur[1]) is Box:
                    env[nm] = cur[1]
                elif cur is not None:
                    # 兜底：祖先尚未装箱（跨模块求值等罕见路径）→ 现装，
                    # 并让祖先视角也看到同一个 Box
                    b = Box(cur[1])
                    env[nm] = b
                    self.variables[nm] = (cur[0], b)
                # cur is None → 全局回落：不入 env（调用期按全局解析，与
                # native 的「非任何祖先局部 = 全局槽」同一条规则）
            self.variables[func_name] = ('closure', QotClosure(
                func_name, params, body, env, return_type, captured))
            # 自引用（递归闭包）：把自身装进 env（经 Box，调用期安装后
            # 体 内按名字查到的是本闭包值 —— 与 native 的静态符号递归
            # 同语义）。
            clo = self.variables[func_name][1]
            if func_name in clo.env:
                clo.env[func_name].v = clo
            else:
                b = Box(clo)
                clo.env[func_name] = b
            return None
        if func_name in self.variables:
            existing_type, _ = self.variables[func_name]
            if existing_type in ('builtin', 'function', 'closure'):
                self._raise_error(ErrorCode.ERR_FUNCTION_NOT_ISIN, f"函数 '{func_name}' 已存在，无法重复注册", node)
        self.functions[func_name] = {
            'params': params,
            'body': body,
            'return_type': return_type,
            'captured': self._captures_of(node)[0],
        }
        self.variables[func_name] = ('function', func_name)
        captured = self.functions[func_name]['captured']
        def custom_func_wrapper(*args):
            return self._call_user_function(func_name, params, body, captured, args)
        self.builtin.register_function(
            func_name,
            custom_func_wrapper,
            (len(params), ['any'] * len(params), return_type)
        )
        return None

    def _call_user_function(self, func_name, params, body, captured, args):
        """用户函数调用：形参绑定 + 捕获名装箱（激活环境）+ 恢复。"""
        self._func_depth += 1
        if self._func_depth > self._max_depth:
            raise QotDepthLimitError(self._func_depth, self._max_depth)
        self._captured_stack.append(set(captured))
        current_vars = self.variables.copy()
        try:
            for param, arg_val in zip(params, args):
                if param in captured:
                    self.variables[param] = ('param', Box(arg_val))
                else:
                    self.variables[param] = ('param', arg_val)
            for nm in captured:
                if nm in params:
                    continue
                cur = self.variables.get(nm)
                if cur is None or type(cur[1]) is not Box:
                    b = Box(cur[1] if cur else None)
                    self.variables[nm] = (cur[0] if cur else 'var', b)
            result = None
            try:
                result = self.visit(body)
            except ReturnException as e:
                result = e.value
            return result
        finally:
            self.variables = current_vars
            self._captured_stack.pop()
            self._func_depth -= 1

    def _call_closure(self, clo, args):
        """闭包调用：安装创建点环境（Box 按 name 共享）+ 形参绑定。"""
        if len(args) != len(clo.params):
            self._raise_error(ErrorCode.ERR_FUNCTION_ARGS_INVAILD,
                              f"函数 '{clo.name}' 需要 {len(clo.params)} 个参数，但传入了 {len(args)} 个")
        self._func_depth += 1
        if self._func_depth > self._max_depth:
            raise QotDepthLimitError(self._func_depth, self._max_depth)
        self._captured_stack.append(set(clo.captured))
        current_vars = self.variables.copy()
        try:
            for nm, b in clo.env.items():
                self.variables[nm] = ('captured', b)
            for param, arg_val in zip(clo.params, args):
                if param in clo.captured:
                    self.variables[param] = ('param', Box(arg_val))
                else:
                    self.variables[param] = ('param', arg_val)
            result = None
            try:
                result = self.visit(clo.body)
            except ReturnException as e:
                result = e.value
            return result
        finally:
            self.variables = current_vars
            self._captured_stack.pop()
            self._func_depth -= 1

    def visit_function_call(self, node):
        _, func_name, args,line = node
        # 【M4 0】类名调用 = 构造（类注册表先于变量表 —— 类名是
        # 顶层声明面，不经 variables）
        if func_name in self.classes:
            if args is None:
                args = []
            evaluated_args = [self.visit(arg) for arg in args if arg is not None]
            return self._construct_object(func_name, evaluated_args, node)
        if func_name not in self.variables:
            self._raise_error(ErrorCode.ERR_FILE_NOT_FOUND,f"函数 '{func_name}' 未定义",node)
        func_type, func = self.variables[func_name]
        if type(func) is Box:
            func = func.v
        if func_type == 'builtin':
            if(args == None):
                args = []
            evaluated_args = [self.visit(arg) for arg in args if arg is not None]
            try:
                return func(*evaluated_args)
            except TypeError:
                self._raise_error(ErrorCode.ERR_FUNCTION_ARGS_INVAILD,f"调用函数 '{func_name}'参数数量不匹配",node)
            except ctypes.ArgumentError:
                self._raise_error(ErrorCode.ERR_FUNCTION_ARGS_TYPEINV,f"调用函数 '{func_name}'参数类型不匹配,若为字符串类型可考虑使用'utf8_enc'函数处理后再传值",node)
        elif func_type == 'function':
            func_def = self.functions[func_name]
            params = func_def['params']
            body = func_def['body']
            if(args == None):
                args = []
            if len(args) != len(params):
                self._raise_error(ErrorCode.ERR_FUNCTION_ARGS_INVAILD,f"函数 '{func_name}' 需要 {len(params)} 个参数，但传入了 {len(args)} 个",node)
            evaluated_args = [self.visit(arg) for arg in args if arg is not None]
            captured = func_def.get('captured') or ()
            return self._call_user_function(func_name, params, body,
                                            captured, evaluated_args)
        elif func_type == 'closure':
            if(args == None):
                args = []
            evaluated_args = [self.visit(arg) for arg in args if arg is not None]
            return self._call_closure(func, evaluated_args)
        else:
            # F1：变量持闭包值（var c = make() 后 c()）→ 直接调用
            if isinstance(func, QotClosure):
                if(args == None):
                    args = []
                evaluated_args = [self.visit(arg) for arg in args if arg is not None]
                return self._call_closure(func, evaluated_args)
            self._raise_error(ErrorCode.ERR_FUNCTION_NOTIT,f"'{func_name}' 不是函数",node)

    def visit_stop_statement(self, node):
        raise LoopStopException("stop语句终止循环")

    def visit_continue_statement(self, node):
        raise LoopContinueException("continue语句跳过本轮")

    # ---------------- F1/F2 ----------------
    def visit_try_statement(self, node):
        _, try_body, catch_var, catch_body, line = node
        try:
            return self.visit(try_body)
        except QotThrowException as e:
            # 只捕获 throw；Loop/Return/系统异常原样穿透（与 native 的
            # 「stop/return 先摘着陆点再走原路径」逐位对应）
            if catch_var:
                self._declare_var(catch_var, e.value, 'var')
            return self.visit(catch_body)

    def visit_throw_statement(self, node):
        _, throw_expr, line = node
        value = self.visit(throw_expr)
        raise QotThrowException(value)

    # ---------------- M4 最小对象模型（0） ----------------
    def visit_class_declaration(self, node):
        """class 声明：登记类（字段 + 方法 + init）。构造语义 = 字段初始化器
        按声明序 → init 体（this = 新实例）→ 恒返回实例。"""
        _, cname, members, _line = (node + (None,) * 3)[:4]
        fields, methods = [], {}
        init_params, init_body = None, None
        for m in (members or []):
            if not isinstance(m, tuple) or not m:
                continue
            if m[0] == 'field':
                fields.append(m[1])
                if len(m) > 2 and m[2] is not None:
                    # 字段初始化器登记（构造时按声明序求值）
                    self._class_field_inits.setdefault(cname, []).append(
                        (m[1], m[2]))
            elif m[0] == 'method' and len(m) >= 5:
                # strip 后 5 元组 / 带行号 6 元组，位置恒定
                mname, params, body = m[2], m[3], m[4]
                if mname == 'init':
                    init_params, init_body = (params or []), body
                else:
                    methods[mname] = (params or [], body)
        self.classes[cname] = QotClass(cname, fields, methods,
                                       init_params, init_body)
        return None

    def _construct_object(self, cname, args, node):
        cls = self.classes[cname]
        obj = QotObject(cls)
        # 字段初始化器（声明序，先于 init）
        for fname, init_expr in (self._class_field_inits.get(cname) or []):
            obj.fields[fname] = self.visit(init_expr)
        if cls.init_body is not None:
            params = cls.init_params or []
            if len(args) != len(params):
                self._raise_error(
                    ErrorCode.ERR_FUNCTION_ARGS_INVAILD,
                    f"构造 {cname}() 需要 {len(params)} 个参数，但传入了 {len(args)} 个",
                    node)
            current_vars = self.variables
            saved = current_vars.copy()
            try:
                self._declare_var('this', obj, 'param')
                for k, p in enumerate(params):
                    self._declare_var(p, args[k], 'param')
                self._func_depth += 1
                self._max_depth_check(node)
                try:
                    self.visit(cls.init_body)
                except ReturnException:
                    # init 的 return 已被 sema 立法禁止（编译期致命）；
                    # 此处兜底 = 与 native 的「恒返回实例」同律
                    pass
            finally:
                self._func_depth -= 1
                self.variables = saved
        return obj

    def _max_depth_check(self, node):
        # 与 _call_user_function/_call_closure 同款（QotDepthLimitError →
        # [2000] 文案 + exit 124 的 CP-N3 机器）
        if self._func_depth > self._max_depth:
            raise QotDepthLimitError(self._func_depth, self._max_depth)

    def visit_field_access(self, node):
        """obj->f：对象字段读（缺席/非对象 = null，与 native qotrt_obj_get 同律）。"""
        _, obj_expr, fname, _line = (node + (None,) * 3)[:4]
        obj = self.visit(obj_expr)
        if isinstance(obj, QotObject):
            return obj.fields.get(fname)
        return None

    def visit_field_assignment(self, node):
        """obj->f = v：字段写（缺席/非对象 = 忽略，与 native qotrt_obj_set 同律）。"""
        _, obj_expr, fname, value_expr, _line = (node + (None,) * 4)[:5]
        value = self.visit(value_expr)
        obj = self.visit(obj_expr)
        if isinstance(obj, QotObject) and fname in obj.fields:
            obj.fields[fname] = value
        return value

    def _call_method(self, obj, mname, args, node):
        """对象方法分派：缺席方法 → null（宽松定价）；this = 接收者。"""
        m = obj.cls.methods.get(mname)
        if m is None:
            return None
        params, body = m
        if len(args) != len(params):
            # 与用户函数调用同款 2010（native 侧是部分绑定 —— 登记分歧族）
            self._raise_error(
                ErrorCode.ERR_FUNCTION_ARGS_INVAILD,
                f"方法 '{obj.cls.name}->{mname}' 需要 {len(params)} 个参数，"
                f"但传入了 {len(args)} 个", node)
        saved = self.variables.copy()
        try:
            self._declare_var('this', obj, 'param')
            for k, p in enumerate(params):
                self._declare_var(p, args[k], 'param')
            self._func_depth += 1
            self._max_depth_check(node)
            result = None
            try:
                result = self.visit(body)
            except ReturnException as e:
                result = e.value
            return result
        finally:
            self._func_depth -= 1
            self.variables = saved

    def visit_lambda_expr(self, node):
        _, lam_params, lam_expr, line = node
        captured, free = self._captures_of(node, is_lambda=True)
        env = {}
        for nm in free:
            # 内置名不捕获（P0 修正 0）：native 侧内置调用是
            # CALL_BUILTIN 按名全局解析，从不进 env（glen/str_len 在闭包体内
            # 调用曾因此被 ('captured', Box) 遮蔽成"不是函数"[2013]）。
            # 解释器对齐：free 里的内置名留空，调用期经 variables 快照按
            # ('builtin', handler) 解析 —— 与「非任何祖先局部 = 全局」同律。
            if nm in self.builtin.functions:
                continue
            cur = self.variables.get(nm)
            if cur is not None and type(cur[1]) is Box:
                env[nm] = cur[1]
            elif cur is not None:
                b = Box(cur[1])
                env[nm] = b
                self.variables[nm] = (cur[0], b)
        return QotClosure('<lambda>', lam_params or [],
                          [('return_statement', lam_expr, 0)],
                          env, 'any', captured)

    def visit_while_statement(self, node):
        _, condition, body,line = node
        result = None
        visit = self.visit
        is_truthy = self.is_truthy
        try:  
            while True:
                cond_value = visit(condition)
                if not is_truthy(cond_value):
                    break
                try:
                    result = visit(body)
                except LoopContinueException:
                    continue
        except LoopStopException:
            pass
        return result

    def visit_array_index(self, node):
        _, array_name, index_expr, line = node
        array_value = self.visit(('identifier', array_name))
        index = self.visit(index_expr)
        if isinstance(array_value, str):
            if not isinstance(index, int):
                self._raise_error(ErrorCode.ERR_TYPE_MISMATCH, f"字符串索引必须是整数，实际是 {type(index).__name__}", node)
            if index < 0 or index >= len(array_value):
                self._raise_error(ErrorCode.ERR_INDEX_OUT_OF_RANGE, f"字符串索引 {index} 越界（长度为 {len(array_value)}）", node)
            return array_value[index]
        if isinstance(array_value, dict):
            return array_value.get(index, None)
        if not isinstance(array_value, list):
            self._raise_error(ErrorCode.ERR_TYPE_NOT_SUBSCRIPTABLE, f"'{array_name}' 不是数组或字典，无法使用索引访问", node)
        if not isinstance(index, int):
            self._raise_error(ErrorCode.ERR_TYPE_MISMATCH, f"数组索引必须是整数，实际是 {type(index).__name__}", node)
        if index < 0 or index >= len(array_value):
            self._raise_error(ErrorCode.ERR_INDEX_OUT_OF_RANGE, f"数组索引 {index} 越界（长度为 {len(array_value)}）", node)
        return array_value[index]

    def visit_if_statement(self, node):
        _, condition, if_block, eif_blocks, else_block,line = node
        cond_value = self.visit(condition)
        if self.is_truthy(cond_value):
            return self.visit(if_block)
        if eif_blocks:
            for eif in eif_blocks:
                eif_cond = self.visit(eif[1])  
                if self.is_truthy(eif_cond):
                    return self.visit(eif[2])  
        if else_block:
            return self.visit(else_block[1])  
        return None

    def visit_else_clause(self, node):
        _, block,line = node
        return self.visit(block)

    def is_truthy(self, value):
        if value is None:
            return False
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return value != 0
        if isinstance(value, str):
            return len(value) > 0
        if isinstance(value, (list, dict)):
            return len(value) > 0
        return True

    def visit_start(self, node):
        _, statements,line = node
        return self.visit_statement_list(statements)

    def visit_statement_list(self, statements):
        result = None
        if statements:
            visit = self.visit
            for stmt in statements:
                if stmt:
                    result = visit(stmt)
        return result

    def visit_var_declaration(self, node):
        _, var_name, expr,line = node
        value = self.visit(expr)
        self._declare_var(var_name, value, 'var')
        return value

    def visit_const_declaration(self, node):
        _, var_name, expr,line = node
        value = self.visit(expr)
        self._declare_var(var_name, value, 'const')
        return value

    def visit_assignment(self, node):
        _, var_name, expr, line = node
        if var_name not in self.variables:
            self._raise_error(ErrorCode.ERR_NAME_NOT_DEFINED, f"变量 '{var_name}' 未定义", node)
        
        var_type, _ = self.variables[var_name]
        if var_type == 'const':
            self._raise_error(ErrorCode.ERR_CONST_MODIFICATION, f"不能修改常量 '{var_name}'", node)
        
        value = self.visit(expr)
        self._set_var_fast(var_name, value, var_type)
        return value

    def visit_unary_op(self, node):
        _, op, expr,line = node
        if op == 'UMINUS':  
            value = self.visit(expr)
            return -value
        if op == 'NOT':
            # 【GOD-SELF 收尾 0】一元逻辑非：truthy 取反，返回
            # Python bool（输出面 true/false，与 native qotrt_unary op2 =
            # qotrt_bool(!truthy) 逐位同律 —— 同一张 truthy 表：
            # null/false/0/空串/空容器 = 假，其余真，含对象恒真）。
            value = self.visit(expr)
            return not self.is_truthy(value)
        else:
            self._raise_error(ErrorCode.ERR_UNARY_OP_UNKNOWN, f"未知一元运算符: {op}", node)

    def _to_concat_str(self, v):
        """+ 拼接分支的值格式化 —— 逐位复刻 native 的 concat 口径
        （qotrt_arith op1 + qbuf_num_concat + qotrt_tostring）：
          str   → 原样
          num   → 整数值（|v|≤9.2e18）十进制无小数；否则 %f 六位
          bool  → true/false
          null  → ""
          array/dict → qotrt_tostring 的字面形态（format_value_for_output 同源）"""
        if isinstance(v, bool):
            return 'true' if v else 'false'
        if v is None:
            return ''
        if isinstance(v, str):
            return v
        if isinstance(v, (int, float)):
            if isinstance(v, int):
                return str(v)
            if v == int(v) and -9.2e18 <= v <= 9.2e18:
                return str(int(v))
            return '%f' % v
        return format_value_for_output(v)

    def visit_binary_op(self, node):
        _, op, left, right,line = node
        left_val = self.visit(left)
        right_val = self.visit(right)
        if True:
            if op == '+':
                # 【CP-N4 · 语料线抓到 0】+ 的拼接分支向 native 对齐：
                # 任一侧是字符串 → 拼接（数字 int 化/null 得 ""/其余 tostring），
                # 与 qotrt_arith op1 逐位一致（README 20.3 也这么承诺）。
                # 旧实现直接 Python + → str+int 抛 2002（Python 语义渗漏，
                # oracle 与发布面在 README 明文行为上分歧 —— 未登记的那种）。
                ls = isinstance(left_val, str)
                rs = isinstance(right_val, str)
                if ls or rs:
                    return self._to_concat_str(left_val) + self._to_concat_str(right_val)
                return left_val + right_val
            elif op == '-':
                return left_val - right_val
            elif op == '*':
                return left_val * right_val
            elif op == '/':
                return left_val / right_val
            elif op == '%':
                return left_val % right_val
            elif op == '^':                   
                return left_val ** right_val
            elif op == '>':
                return left_val > right_val
            elif op == '<':
                return left_val < right_val
            elif op == '>=':
                return left_val >= right_val
            elif op == '<=':
                return left_val <= right_val
            elif op == '==':
                return left_val == right_val
            elif op == '!=':
                return left_val != right_val
        if op == 'AND' or op == '&&':
            return bool(left_val and right_val)
        elif op == 'OR' or op == '||':
            return bool(left_val or right_val)
        if op == '+' and self._is_string(left_val) and self._is_string(right_val):
            return left_val + right_val
        
        self._raise_error(ErrorCode.ERR_INVALID_OPERATION, f"不支持的运算: {op} 类型 {type(left_val).__name__} {type(right_val).__name__}", node)

    def visit_literal(self, node):
        _, value, line = node
        if isinstance(value, bytes):
            return value
        # 【S-14 终审 0】同上：字面量保持字符串。
        return value

    def visit_identifier(self, node):
        if len(node) == 3:
            _, var_name, line = node
        else:  
            _, var_name = node
        return self._get_var_fast(var_name)

    def visit_parenthesized(self, node):
        _, expr,line = node
        return self.visit(expr)

    def visit_array(self, node):
        _, elements,line = node
        array_value = []
        if(elements is not None):
            for elem in elements:
                if elem is not None:
                    array_value.append(self.visit(elem))  
        return array_value

    def visit_array_declaration(self, node):
        _, var_name, elements,line = node
        array_value = self.visit(('array', elements))
        self._declare_var(var_name, array_value, 'var')
        return array_value

    def visit_function_call_statement(self, node):
        return self.visit_function_call(node[1])

    # ---------------- GOD-SELF-G2 编译期宏（体执行期钩子） ----------------
    def visit_macro_call(self, node):
        """宏体内的表达式位宏调用：值语义展开（经 macro_hook）。"""
        hook = getattr(self, 'macro_hook', None)
        if hook is None:
            self._raise_error(
                ErrorCode.ERR_COMPILE_GENERAL,
                "宏调用 '!' 出现在非展开上下文（主程序 AST 不应含未展开的宏调用）",
                node)
        return hook.expand_body_call(self, node, statement=False)

    def visit_macro_call_statement(self, node):
        """宏体内的语句位宏调用：预展开期已拼接；走到这里 = 展开器漏洞。"""
        hook = getattr(self, 'macro_hook', None)
        if hook is None:
            self._raise_error(
                ErrorCode.ERR_COMPILE_GENERAL,
                "宏调用 '!' 出现在非展开上下文（主程序 AST 不应含未展开的宏调用）",
                node)
        return hook.expand_body_call(self, node[1], statement=True)

def execute_script(filename: str) -> None:
    """【CP-N3/S-10】大栈工作线程里执行（5000 层 QOT 递归的可达性前提）。

    AST 求值器每层 QOT 调用消耗 ~10 个 Python 帧；默认线程栈 ~200 层 QOT
    递归即崩（把 Python 栈实限误当语言上限 —— 旧 S-10 分歧的根因）。这里
    扩到 64MB 栈 + 200k 帧配额，让语言级上限（QOT_MAX_DEPTH，默认 5000）
    成为**先于** Python 栈触发的确定性事件。SystemExit 退出码如实回传主
    线程；其余异常原样重抛（保留全局 excepthook 的既有格式化语义）。"""
    import threading
    box = {}

    def _work():
        try:
            _execute_script_impl(filename)
            box['rc'] = 0
        except SystemExit as e:
            box['rc'] = e.code if isinstance(e.code, int) else (1 if e.code else 0)
        except BaseException as e:  # 主线程重抛（excepthook 语义不变）
            box['rc'] = None
            box['exc'] = e

    old_limit = sys.getrecursionlimit()
    sys.setrecursionlimit(max(old_limit, 200000))
    old_size = None
    try:
        old_size = threading.stack_size(64 * 1024 * 1024)
    except (ValueError, RuntimeError, OverflowError):
        pass
    try:
        t = threading.Thread(target=_work, name='qot-run')
        t.start()
        t.join()
    finally:
        sys.setrecursionlimit(old_limit)
        if old_size is not None:
            try:
                threading.stack_size(old_size)
            except (ValueError, RuntimeError):
                pass
    if box.get('rc') is None:
        raise box['exc']
    if box.get('rc', 0) != 0:
        sys.exit(box['rc'])


def _prepare_ast(ast, filename: str):
    """【GOD-SELF §8.24 · 0】编译前端统一挂点（三条执行路径共用：
    interp run / bstrust compile / aot —— 一次实现双引擎天然一致）：
      ① 宏展开（G2，qot_macro.expand_program：AST→AST，fatal → None）；
      ② 协议绑定（G1，qot_proto.bind_protocols：类型已知 → 运算符/下标/
         for-in/output 静态绑定到 __add__ 等 11 个协议方法）。
    展开在 strip 之前执行（宏体以带行号形态执行，产物行号 = 调用点行号）；
    编译路径随后手动 strip —— 与 parse_oning_gram(…, True) 逐字节同构。
    任一步致命 → None（调用方 exit 1，与解析失败同一出口）。"""
    from qot_macro import expand_program
    ast = expand_program(ast, filename)
    if ast is None or ast == 1:
        return None
    from qot_proto import bind_protocols
    ast = bind_protocols(ast, filename)
    if ast is None or ast == 1:
        return None
    return ast


def _execute_script_impl(filename: str) -> None:
    if filename.endswith('.qbc'):
        with open(filename, 'rb') as f:
            data = f.read()
        result = decode_ast(data)
        if isinstance(result, dict) and 'ast' in result:
            ast = result['ast']
            evaluator = Evaluator()
            from ast_encoder import FastExecutor
            evaluator.executor = FastExecutor(result)
        else:
            ast = result
            evaluator = Evaluator()
    else:
        ast = parse_oning_gram(filename)
        # 【GOD-SELF §8.24】宏展开 + 协议绑定（解释器与编译共用同一前端，
        # 保证双引擎零分歧由构造成立）
        ast = _prepare_ast(ast, filename)
        evaluator = Evaluator()
    # 【CP-N3 契约修复 0】语法/解码错误必须致命（exit 1）—— 旧实现
    # 解析失败时静默落空、进程 exit 0，脚本层无法探测失败（实测勘察
    # _ec_survey 抓到；宪法 6：错误路径必须有确定契约）。.qbc 解码失败同洞。
    # 判据 = 解析器的错误哨兵（ast is 1 / None）；空语句表 [] 是合法空程序。
    if ast is None or ast == 1:
        sys.exit(1)
    if ast and ast != 1:
        evaluator.set_base_path(filename)
        try:
            evaluator.evaluate(ast)
        except ReturnException as e:
            if e.value is not None:
                pass
        except QotThrowException as e:
            # F2 契约：未捕获 throw = 确定性致命，与 native 逐位同格式
            # （stderr `[错误] [2001] 未捕获的 throw: <值>` + exit 124）。
            try:
                sys.stdout.flush()
            except Exception:
                pass
            sys.stderr.write('[错误] [2001] 未捕获的 throw: %s\n' % (e.value,))
            sys.stderr.flush()
            sys.exit(124)
        except QotDepthLimitError as e:
            # 【CP-N3/S-10】与 native 的 qotrt_fail_depth 逐位同格式：
            # `[错误] [2000] 递归深度超限（depth=N，上限 M）` + exit 124。
            try:
                sys.stdout.flush()
            except Exception:
                pass
            sys.stderr.write('[错误] [2000] 递归深度超限（depth=%d，上限 %d）\n'
                             % (e.depth, e.limit))
            sys.stderr.flush()
            sys.exit(124)
        """except Exception as e:
            with open('error.log', 'w') as f:
                traceback.print_exc(file=f)"""
def build_tocpp_script(filename: str,icons:str) -> None:
    try:
        ast = parse_oning_gram(filename)
        # 【GOD-SELF §8.24】旧 C++ 后端同挂点（展开在带行号形态上执行）
        ast = _prepare_ast(ast, filename)
        if ast and ast != 1:
            compile_and_run(ast, icons)
    except Exception as e:
        print(f"{str(e)}", file=sys.stderr)

QTC_VERSION = 2
QTC_FLAG_AGGRESSIVE_OK = 0x1  # T-48：可激进优化（require 全内联、无运行期代码加载）

_builtin_names_cache = None


def _builtin_name_set():
    """解释器侧内置函数名集合（BuiltFunc.functions 的键）。"""
    global _builtin_names_cache
    if _builtin_names_cache is None:
        _builtin_names_cache = set(BuiltFunc().functions.keys())
    return _builtin_names_cache


def _can_aggressive_optimize(code) -> bool:
    """T-48：编译期判定「可激进优化」。

    条件（SPEC 原则五：前提在编译期可验证，运行期还会复核）：
      1. 不存在 REQUIRE 指令（require 已全部内联，或源码根本没 require）；
      2. 不引用 ScLoad / ScExec（运行期代码加载原语）。
    注意：这只是"声明可激进"，VM 端加载 LIR 失败时仍会回退字节码保守路径。
    """
    def has_require(block):
        for op, _arg in block.instructions:
            if op == VMInstruction.REQUIRE:
                return True
        for c in block.constants:
            if isinstance(c, VMCode) and has_require(c):
                return True
        return False

    def uses_dynamic_load(block):
        if set(block.func_names) & {'ScLoad', 'ScExec'}:
            return True
        for c in block.constants:
            if isinstance(c, VMCode) and uses_dynamic_load(c):
                return True
        return False

    return not has_require(code) and not uses_dynamic_load(code)


def _write_qtc(code, output_file: str, aggressive: bool = False, lir_blob: bytes = b''):
    """写出 .qtc v2（SPEC 7.3-T-32 / R6）。

    布局：
      v2 头:  "QTC\\0" | version=2 (u32)                       （v1 的 funcsInfoLen 死字段已删除）
      主体:   instCount(u32) | [op(u8) argLen(u32) arg]* | constsLen|consts
              | varsLen|vars | funcsLen|funcs | sourceLen|source
      扩展段: flags(u32) | builtinHintsLen|builtinHints | lirLen(u32)|lir
      尾部:   crc32(u32)（覆盖其前全部字节）
    v1 文件由 C++ loader 继续支持（版本支持区间 [1,2]），v1 无 CRC、无扩展段。
    """
    import zlib
    body = bytearray()
    body += b'QTC\x00'
    body += struct.pack('<I', QTC_VERSION)
    body += struct.pack('<I', len(code.instructions))
    for op, arg in code.instructions:
        body += struct.pack('<B', op)
        arg_bytes = _serialize_arg(arg)
        body += struct.pack('<I', len(arg_bytes))
        body += arg_bytes
    consts_bytes = _serialize_constants(code.constants)
    body += struct.pack('<I', len(consts_bytes))
    body += consts_bytes
    vars_bytes = _serialize_string_list(code.var_names)
    body += struct.pack('<I', len(vars_bytes))
    body += vars_bytes
    funcs_names_bytes = _serialize_string_list(code.func_names)
    body += struct.pack('<I', len(funcs_names_bytes))
    body += funcs_names_bytes
    source_bytes = code.source_file.encode('utf-8')
    body += struct.pack('<I', len(source_bytes))
    body += source_bytes

    # ---- 扩展段（v2 新增）----
    ext = bytearray()
    flags = (QTC_FLAG_AGGRESSIVE_OK if aggressive else 0)
    ext += struct.pack('<I', flags)
    builtin_names = sorted(n for n in code.func_names if n in _builtin_name_set())
    hints_bytes = _serialize_string_list(builtin_names)
    ext += struct.pack('<I', len(hints_bytes))
    ext += hints_bytes
    ext += struct.pack('<I', len(lir_blob))
    ext += lir_blob
    body += ext

    # ---- CRC32 ----
    body += struct.pack('<I', zlib.crc32(bytes(body)) & 0xFFFFFFFF)

    with open(output_file, 'wb') as f:
        f.write(bytes(body))


def _compile_to_qtc(source_file: str, output_file: str,enable_opt:bool):
    from bytecode import Compiler
    from error_system import get_error_handler
    handler = get_error_handler()
    handler.get_collector().clear()
    ast = parse_oning_gram(source_file)
    ast = _prepare_ast(ast, source_file)
    if not ast or ast == 1:
        record_exception(ErrorCode.ERR_COMPILE_GENERAL,f"解析失败: {source_file}")
    ast = strip_line_numbers(ast)
    if handler.get_collector().has_errors():
        print(f"编译失败: {source_file} 存在语法错误，未生成 {output_file}", file=sys.stderr)
        sys.exit(1)
    fast = None
    if(enable_opt):
        from astcomopt import optimize_ast
        fast = optimize_ast(ast)
    else:
        fast = ast
    compiler = Compiler()
    compiler.set_base_path(source_file)
    code = compiler.compile_ast(fast)
    code.source_file = source_file

    _write_qtc(code, output_file, aggressive=_can_aggressive_optimize(code))


def _serialize_arg(arg):
    if arg is None:
        return struct.pack('<i', 0)
    elif isinstance(arg, int):
        return struct.pack('<i', arg)
    elif isinstance(arg, str):  
        str_bytes = arg.encode('utf-8')
        result = bytearray()
        result.extend(struct.pack('<I', len(str_bytes)))  
        result.extend(str_bytes)
        return bytes(result)
    elif isinstance(arg, tuple):
        if len(arg) == 2:
            if isinstance(arg[0], int) and isinstance(arg[1], int):
                return struct.pack('<ii', arg[0], arg[1])
            elif isinstance(arg[0], str) and isinstance(arg[1], int):
                method_name_bytes = arg[0].encode('utf-8')
                name_len = len(method_name_bytes)
                result = bytearray()
                result.extend(struct.pack('<i', name_len))
                result.extend(method_name_bytes)
                result.extend(struct.pack('<i', arg[1]))
                return bytes(result)
    return struct.pack('<i', 0)


def _serialize_constants(constants):
    data = bytearray()
    for const in constants:
        if const is None:
            data.append(1)  
            data.extend(struct.pack('<i', 0))
        elif isinstance(const, bool):
            data.append(2)  
            data.append(1 if const else 0)
        elif isinstance(const, (int, float)):
            data.append(3)  
            data.extend(struct.pack('<d', float(const)))
        elif isinstance(const, str):
            data.append(4) 
            str_bytes = const.encode('utf-8')
            data.extend(struct.pack('<I', len(str_bytes)))
            data.extend(str_bytes)
        elif isinstance(const, VMCode):  
            data.append(5)  
            func_data = bytearray()
            func_data.extend(struct.pack('<I', len(const.instructions)))
            for op, arg in const.instructions:
                func_data.extend(struct.pack('<B', op))
                arg_bytes = _serialize_arg(arg)
                func_data.extend(struct.pack('<I', len(arg_bytes)))
                func_data.extend(arg_bytes)
            consts_bytes = _serialize_constants(const.constants)
            func_data.extend(struct.pack('<I', len(consts_bytes)))
            func_data.extend(consts_bytes)
            vars_bytes = _serialize_string_list(const.var_names)
            func_data.extend(struct.pack('<I', len(vars_bytes)))
            func_data.extend(vars_bytes)
            funcs_names_bytes = _serialize_string_list(const.func_names)
            func_data.extend(struct.pack('<I', len(funcs_names_bytes)))
            func_data.extend(funcs_names_bytes)
            source_bytes = const.source_file.encode('utf-8')
            func_data.extend(struct.pack('<I', len(source_bytes)))
            func_data.extend(source_bytes)
            data.extend(struct.pack('<I', len(func_data)))
            data.extend(func_data)
        else:
            data.append(1) 
            data.extend(struct.pack('<i', 0))
    return bytes(data)


def _serialize_string_list(str_list):
    data = bytearray()
    data.extend(struct.pack('<I', len(str_list)))
    for s in str_list:
        str_bytes = s.encode('utf-8')
        data.extend(struct.pack('<I', len(str_bytes)))
        data.extend(str_bytes)
    return bytes(data)

def _get_dll_functions():
    global _dll_handle, _dll_functions
    
    if _dll_handle is not None:
        return _dll_functions
    dll_paths = [
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "qotrun.dll"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "qotrun.dll"),
        os.path.join(os.getcwd(), "qotrun.dll"),
        "qotrun.dll"
    ]
    
    dll_path = None
    for path in dll_paths:
        if os.path.exists(path):
            dll_path = path
            break
    
    if not dll_path:
        record_exception(ErrorCode.ERR_FILE_NOT_FOUND,"找不到 qotrun.dll，请确保 DLL 在以下路径之一:\n" + "\n".join(dll_paths))
    _dll_handle = ctypes.CDLL(dll_path)
    _dll_functions = {
        'vm_init': _dll_handle.vm_init,
        'vm_execute_file': _dll_handle.vm_execute_file,
        'vm_get_return_value': _dll_handle.vm_get_return_value,
        'vm_get_last_error': _dll_handle.vm_get_last_error,
        'vm_set_debug': _dll_handle.vm_set_debug,
        'vm_destroy': _dll_handle.vm_destroy,
    }
    _dll_functions['vm_init'].argtypes = []
    _dll_functions['vm_init'].restype = ctypes.c_void_p
    
    _dll_functions['vm_execute_file'].argtypes = [ctypes.c_void_p, ctypes.c_char_p]
    _dll_functions['vm_execute_file'].restype = ctypes.c_int
    
    _dll_functions['vm_get_return_value'].argtypes = [ctypes.c_void_p]
    _dll_functions['vm_get_return_value'].restype = ctypes.c_char_p
    
    _dll_functions['vm_get_last_error'].argtypes = [ctypes.c_void_p]
    _dll_functions['vm_get_last_error'].restype = ctypes.c_char_p
    
    _dll_functions['vm_set_debug'].argtypes = [ctypes.c_void_p, ctypes.c_int]
    _dll_functions['vm_set_debug'].restype = None
    
    _dll_functions['vm_destroy'].argtypes = [ctypes.c_void_p]
    _dll_functions['vm_destroy'].restype = None
    
    return _dll_functions


def _run_with_dll(qtc_file: str, debug: bool = False):
    if sys.platform == 'win32':
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            kernel32.SetConsoleOutputCP(65001) 
            kernel32.SetConsoleCP(65001)
        except:
            pass
    
    functions = _get_dll_functions()
    vm = functions['vm_init']()
    if not vm:
        record_exception(ErrorCode.ERR_VM_INIT_FAILED,"VM 初始化失败")
    
    try:
        if debug:
            functions['vm_set_debug'](vm, 1)
        result = functions['vm_execute_file'](vm, qtc_file.encode('utf-8'))
        if result:
            ret_val = functions['vm_get_return_value'](vm)
            if ret_val:
                print(f"程序返回值: {ret_val.decode('utf-8')}")
        else:
        
            error = functions['vm_get_last_error'](vm)
            if error:
                record_exception(ErrorCode.ERR_ASSERTION_FAILED,error.decode('utf-8', errors='replace'))
            record_exception(ErrorCode.ERR_VM_EXECUTION_FAILED,"执行失败")
    
    finally:
        functions['vm_destroy'](vm)

def _find_vcvars():
    """定位 vcvarsall.bat（后端 A 编译用）。"""
    if sys.platform != 'win32':
        return None
    vswhere = os.path.join(os.environ.get('ProgramFiles(x86)', r'C:\Program Files (x86)'),
                           'Microsoft Visual Studio', 'Installer', 'vswhere.exe')
    vs = None
    if os.path.exists(vswhere):
        try:
            r = subprocess.run([vswhere, '-latest', '-products', '*',
                                '-requires', 'Microsoft.VisualStudio.Component.VC.Tools.x86.x64',
                                '-property', 'installationPath'],
                               capture_output=True, text=True, timeout=30)
            if r.stdout.strip():
                vs = r.stdout.strip().splitlines()[0]
        except Exception:
            pass
    if not vs:
        for c in (r'C:\Program Files\Microsoft Visual Studio\2022',
                  r'C:\Program Files\Microsoft Visual Studio\2019'):
            if os.path.isdir(c):
                subs = sorted(os.listdir(c), reverse=True)
                if subs:
                    vs = os.path.join(c, subs[0])
                    break
    if not vs:
        return None
    p = os.path.join(vs, 'VC', 'Auxiliary', 'Build', 'vcvarsall.bat')
    return p if os.path.exists(p) else None


def _find_cl_direct():
    """直接定位 cl.exe 与其 INCLUDE/LIB（跳过 vcvarsall 全量初始化，快数秒）。

    返回 dict(cl/path/include/lib) 或 None（调用方回退 vcvarsall 方案）。
    include/lib 是完整列表（bat 里直接整体赋值，不做 %INCLUDE% 追加——
    cmd 对未定义变量的字面展开会让追加式写法静默失效）。
    """
    if sys.platform != 'win32':
        return None
    vswhere = os.path.join(os.environ.get('ProgramFiles(x86)', r'C:\Program Files (x86)'),
                           'Microsoft Visual Studio', 'Installer', 'vswhere.exe')
    vs = None
    if os.path.exists(vswhere):
        try:
            r = subprocess.run([vswhere, '-latest', '-products', '*',
                                '-requires', 'Microsoft.VisualStudio.Component.VC.Tools.x86.x64',
                                '-property', 'installationPath'],
                               capture_output=True, text=True, timeout=30)
            if r.stdout.strip():
                vs = r.stdout.strip().splitlines()[0]
        except Exception:
            pass
    if not vs:
        return None
    msvc_root = os.path.join(vs, 'VC', 'Tools', 'MSVC')
    if not os.path.isdir(msvc_root):
        return None
    try:
        ver = sorted(os.listdir(msvc_root), reverse=True)[0]
    except OSError:
        return None
    bindir = os.path.join(msvc_root, ver, 'bin', 'Hostx64', 'x64')
    if not os.path.exists(os.path.join(bindir, 'cl.exe')):
        return None
    inc = [os.path.join(msvc_root, ver, 'include')]
    lib = [os.path.join(msvc_root, ver, 'lib', 'x64')]
    # Windows SDK：ucrt + um（取实际版本目录）
    sdk = os.environ.get('WindowsSdkDir') or r'C:\Program Files (x86)\Windows Kits\10'
    sdk = sdk.rstrip('\\')
    try:
        kits = sorted(os.listdir(os.path.join(sdk, 'Include')), reverse=True)
    except OSError:
        kits = []
    if kits:
        kv = kits[0]
        inc.append(os.path.join(sdk, 'Include', kv, 'ucrt'))
        inc.append(os.path.join(sdk, 'Include', kv, 'um'))
        inc.append(os.path.join(sdk, 'Include', kv, 'shared'))
        lib.append(os.path.join(sdk, 'Lib', kv, 'ucrt', 'x64'))
        lib.append(os.path.join(sdk, 'Lib', kv, 'um', 'x64'))
    inc = [p for p in inc if os.path.isdir(p)]
    lib = [p for p in lib if os.path.isdir(p)]
    if not inc:
        return None
    return {'cl': os.path.join(bindir, 'cl.exe'), 'path': bindir,
            'include': inc, 'lib': lib}


def _aot_compile(source_file: str, out_exe: str, enable_opt: bool,
                 keep_c: bool = False, emit_c: str = None,
                 split_tu: bool = None, jobs: int = None) -> str:
    """后端 A：.qot → LIR →（内联 + F64 特化）→ C 源码 → g++ -O2 → exe。返回 C 源路径。

    F0（S-15 根治，0）：native 支持 require —— 编译期把被 require
    的 .qot 模块【内联】进主程序（require_inliner，与解释器的 .qot 模块语义
    同源：模块顶层名 = 全量导出，`mod##fn` 调用点改写为直呼）。循环依赖在
    内联期显式报错；.dll require 不适用 native（保持编译期明确报错）。

    【QOT-CI · CP-C1/C2/C3】编译管线 CI 线接入（ §9）：
      * L2 前端缓存（qot_build.frontend_bundle）：合并源+开关+管线指纹 →
        C 文本（纯文本 JSON，禁 pickle）；命中跳过 parse/LIR/优化/cgen 全链。
      * split_tu（--split-tu / QOT_SPLIT_TU）：L1 切分 TU —— 同一份全程序
        LIR（G1：与单 TU 同一条语义管线），generate_split 发射共享头+主 TU+
        模块 TU；对象/链接走 qot_cache 的 CAS（qot_build.build_split）。
      * 工具链缓存（qot_cache.compile_exe）：C → exe 内容寻址；PCH 由内容
        hash 管理；QOT_CACHE=off 一键全冷（K5）。gcc 主线之外的 MSVC 兜底
        保持既有 bat 路径（无缓存 —— 回退形态，主线是 MinGW g++）。
    """
    import tempfile
    from error_system import get_error_handler
    handler = get_error_handler()
    handler.get_collector().clear()
    if split_tu is None:
        split_tu = os.environ.get('QOT_SPLIT_TU', '0') == '1'
    if jobs is None:
        try:
            jobs = int(os.environ.get('QOT_JOBS', '0')) or None
        except ValueError:
            jobs = None

    import qot_cache as _qcc
    import qot_build as _qcb

    compile_src = source_file
    temp_src = None
    merged_text = None
    module_of = {}
    try:
        with open(source_file, 'r', encoding='utf-8') as _f:
            merged_text = _f.read()
        _has_require = 'require ' in merged_text
        if _has_require:
            # 【P1030-4】合并单元写系统临时目录 —— 此前写在源文件同目录，
            # 编译中断（Ctrl+C/异常）时残留 aot_req_*.qot 在 cases/ 里，
            # 套件 discover_cases 会因缺 .json 元数据而中断。
            # require 的相对解析基于 source_file 原路径（inliner 的
            # current_file），与合并单元落点无关。
            # 【QOT-CI】改用 RequireInliner 实例直调（被动登记模块 DAG，
            # split TU 的归属数据源；合并文本与 inline_requires 逐字节一致）。
            from require_inliner import RequireInliner
            fd, temp_src = tempfile.mkstemp(suffix='.qot', prefix='aot_req_',
                                            text=True)
            os.close(fd)
            inliner = RequireInliner()
            inliner.inline_file(source_file, temp_src)
            for m in inliner.modules:
                for fn in m['funcs']:
                    module_of[fn] = m['name']
            with open(temp_src, 'r', encoding='utf-8') as _f:
                merged_text = _f.read()
            compile_src = temp_src
    except ImportError as e:
        record_exception(ErrorCode.ERR_MODULE_CIRCULAR_DEP, f"require 内联失败: {e}")
    except Exception as e:
        record_exception(ErrorCode.ERR_COMPILE_GENERAL, f"require 内联失败: {e}")

    # ---- 前端（L2 缓存包裹；miss 时走既有语义管线 S2~S7）----
    def _compute_frontend():
        ast = parse_oning_gram(compile_src)
        # 【GOD-SELF §8.24】宏展开 + 协议绑定 → 再 strip（与
        # parse_oning_gram(…, True) 的产物逐字节同构；展开产物行号 = 调用点）
        ast = _prepare_ast(ast, source_file)
        if not ast or ast == 1:
            record_exception(ErrorCode.ERR_COMPILE_GENERAL, f"解析失败: {source_file}")
        ast = strip_line_numbers(ast)
        if handler.get_collector().has_errors():
            record_exception(ErrorCode.ERR_COMPILE_GENERAL, f"语法错误: {source_file}")
        fast = ast
        if enable_opt:
            from astcomopt import optimize_ast
            fast = optimize_ast(ast)
        compiler = Compiler()
        compiler.set_base_path(source_file)
        code = compiler.compile_ast(fast)
        code.source_file = source_file

        from lir_lower import lower_program, LowerError
        from lir_opt import (inline_calls_with_tables, infer_f64, promote_globals,
                             optimize_f64, inline_calls_v2, snapshot_user_funcs)
        from lir_cgen import generate, generate_split, CgenError
        try:
            prog = lower_program(code)
            # 优化管线。A/B 开关一律**由各 pass 自检**（QOT_NO_*），调用方不重复
            # 判断 —— 开关散落在调用方迟早会漂移（G-14：开关失灵毫无报错，
            # 必须用 _dbg_switches.py 逐个验证归零）。
            _inlined = inline_calls_v2(prog)
            _promoted = promote_globals(prog)
            _snaps = snapshot_user_funcs(prog)   # N-04：内联后/推断前快照
            infer_f64(prog)
            if split_tu:
                # QOT-CI · CP-C5：stage-1（全局分析：内联/F64 解/全局槽提升）
                # 到此为止；stage-2（窥孔/SSA/arena/cgen，均为逐函数 pass）由
                # qot_build.tu_bundle 按 TU 记忆化 —— 改一个模块只付该模块
                # 的 stage-2 与 g++。单 TU 路径保持全程序顺序（产物不变）。
                return _qcb.tu_bundle(prog, source_file, _snaps, module_of,
                                      enable_opt)
            _opt_stats = optimize_f64(prog)      # 窥孔 + unbox + f 域合并
            # N-03：SSA + 槽合并（v 域）。放在【全部槽级分析之后】——infer_f64
            # 与 unbox 的约束求解都是槽级的，槽共享发生在它们之前会互相毒化
            # （三轮实测：null 临时毒化数值证明 18.5 ns；const 临时毒化 A2 →
            # 快循环全灭 203/337 ns）。放在它们之后是安全的：
            #   * 候选槽 V16 在快循环内本就陈旧、出边回写覆盖 —— dead 临时
            #     写入无害（写写之间无读）；
            #   * 其余 V16 的读取由干涉图保护（活区间点精确）；
            #   * 组指令不被值级死定义删除触碰（STORE/LOAD/CMP ∉ 纯集合）。
            import lir_ssa
            _ssa_stats = lir_ssa.ssa_regalloc(prog)
            # M-PERF 收尾二：循环 arena 回收插桩（b3a 形态；QOT_NO_ARENA 关）。
            # 必须在全部结构 pass 之后、generate 之前 —— 快照（_snaps）已拍完，
            # 快体不含 ARENA op；此后无人再改写指令流，插桩坐标稳定。
            from lir_opt import arena_loops
            _arena_stats = arena_loops(prog)
            if split_tu:
                return generate_split(prog, source_file, fast_snaps=_snaps,
                                      module_of=module_of)
            return generate(prog, source_file, fast_snaps=_snaps)
        except (LowerError, CgenError) as e:
            record_exception(ErrorCode.ERR_COMPILE_QTC_FAILED, f"AOT 编译失败: {e}")
        except Exception as e:
            record_exception(ErrorCode.ERR_COMPILE_QTC_FAILED, f"AOT 编译异常: {e}")

    try:
        payload, _fe_hit = _qcb.frontend_bundle(merged_text, source_file,
                                                split_tu, enable_opt,
                                                _compute_frontend)
    finally:
        if temp_src and os.path.exists(temp_src):
            try:
                os.remove(temp_src)
            except OSError:
                pass

    build_dir = os.path.dirname(os.path.abspath(out_exe)) or '.'
    os.makedirs(build_dir, exist_ok=True)
    rt_src = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          'ot-asset', 'c', 'aot', 'qot_aot_runtime.h')
    netlibs = ['-lws2_32'] if os.name == 'nt' else []

    use_gcc = _qcc.compiler_id()['kind'] == 'gcc'

    if split_tu and isinstance(payload, dict):
        # ---- L1 切分 TU：对象 CAS + 增量链接（qot_build 编排）----
        if not use_gcc:
            record_exception(ErrorCode.ERR_SYSTEM_GENERAL,
                             "切分 TU 当前仅支持 gcc/g++ 工具链（MSVC 兜底未接入 CI 线）")
        c_path = emit_c or os.path.join(
            build_dir, os.path.splitext(os.path.basename(out_exe))[0] + '_parts.h')
        if keep_c or emit_c:
            import io as _io
            with open(c_path, 'w', encoding='utf-8', newline='\n') as f:
                f.write(payload.get('header') or '')
        stats = _qcb.build_split(payload, out_exe, rt_src=rt_src,
                                 keep_c=keep_c or bool(emit_c),
                                 build_dir=build_dir, jobs=jobs)
        if os.environ.get('QOT_CI_VERBOSE'):
            print('  [CI] split: objects=%(objects)d compiled=%(compiled)d '
                  'link_hit=%(link_hit)s %(elapsed).2fs' % stats, flush=True)
        return c_path

    # ---- 单 TU：C 文本落盘（既有契约），工具链走缓存 ----
    csrc = payload if isinstance(payload, str) else payload.get('main', '')
    c_path = emit_c or os.path.join(
        build_dir, os.path.splitext(os.path.basename(out_exe))[0] + '_gen.cpp')
    with open(c_path, 'w', encoding='utf-8', newline='\n') as f:
        f.write(csrc)

    if not use_gcc:
        # MSVC 兜底：既有 bat 路径（M-PACK 决策：gcc 首选，MSVC 回退；
        # CI 线的缓存/PCH/确定性面不覆盖此形态）
        return _aot_compile_msvc(c_path, out_exe, keep_c)

    _qcc.compile_exe(c_path, out_exe, rt_src=rt_src, netlibs=netlibs,
                     slot='aot:%s' % os.path.abspath(out_exe).replace('\\', '/'))
    _qcc.flush_stats()
    return c_path


def _aot_compile_msvc(c_path: str, out_exe: str, keep_c: bool = False) -> str:
    """MSVC 兜底（既有路径原样保留）：runtime 头拷贝 + bat + cl。"""
    build_dir = os.path.dirname(os.path.abspath(out_exe)) or '.'
    rt_src = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          'ot-asset', 'c', 'aot', 'qot_aot_runtime.h')
    rt_dst = os.path.join(build_dir, 'qot_aot_runtime.h')
    if os.path.abspath(rt_src) != os.path.abspath(rt_dst):
        with open(rt_src, 'r', encoding='utf-8') as f:
            content = f.read()
        last_err = None
        for _attempt in range(4):
            try:
                with open(rt_dst, 'w', encoding='utf-8', newline='\n') as f:
                    f.write(content)
                last_err = None
                break
            except PermissionError as e:
                last_err = e
                time.sleep(0.5)
        if last_err is not None:
            raise last_err

    out_abs = os.path.abspath(out_exe)
    c_abs = os.path.abspath(c_path)
    build_abs = os.path.abspath(build_dir)
    obj_path = os.path.join(build_abs, os.path.splitext(os.path.basename(out_abs))[0] + '_gen.obj')
    bat = c_path + '.bat'
    if os.path.exists(out_abs):
        os.remove(out_abs)
    direct = _find_cl_direct()
    with open(bat, 'w', encoding='ascii') as f:
        f.write('@echo off\r\n')
        if direct:
            f.write('set "PATH=%s;%%PATH%%"\r\n' % direct['path'])
            f.write('set "INCLUDE=%s"\r\n' % ';'.join(direct['include']))
            f.write('set "LIB=%s"\r\n' % ';'.join(direct['lib']))
            cl_net = ' ws2_32.lib' if os.name == 'nt' else ''
            f.write('cl /nologo /EHsc /std:c++17 /utf-8 /O2 /MT /D_CRT_SECURE_NO_WARNINGS '
                    '"%s" /Fe"%s" /Fo"%s"%s 1>&2\r\n' % (c_abs, os.path.splitext(out_abs)[0], obj_path, cl_net))
        else:
            vcvars = _find_vcvars()
            if not vcvars:
                record_exception(ErrorCode.ERR_SYSTEM_GENERAL,
                                 "未找到可用的原生编译器（g++ / MSVC 均缺失），无法编译原生程序")
            f.write('call "%s" x64 >nul 2>&1\r\n' % vcvars)
            cl_net = ' ws2_32.lib' if os.name == 'nt' else ''
            f.write('cl /nologo /EHsc /std:c++17 /utf-8 /O2 /MT /D_CRT_SECURE_NO_WARNINGS '
                    '"%s" /Fe"%s" /Fo"%s"%s 1>&2\r\n' % (c_abs, os.path.splitext(out_abs)[0], obj_path, cl_net))
    print('  [AOT] 调用原生编译器编译中（首次约 5~15 秒，请稍候）...', flush=True)
    r = subprocess.run(['cmd', '/c', os.path.abspath(bat)], cwd=build_abs,
                       capture_output=True, text=True, errors='replace', timeout=300)
    if not os.path.exists(out_exe):
        msg = (r.stderr or r.stdout or '')[-800:]
        if not keep_c and os.path.exists(bat):
            os.remove(bat)
        record_exception(ErrorCode.ERR_COMPILE_QTC_FAILED, f"AOT 原生编译失败:\n{msg}")
    if not keep_c and os.path.exists(bat):
        os.remove(bat)
    return c_path


def main() -> int:
    if len(sys.argv) <= 1:
        record_exception(ErrorCode.ERR_COMPILE_GENERAL,f"命令语法错误,输入help以获得帮助")
        return 1
    if sys.argv[1] == "run":
        filename = sys.argv[2]
        if not os.path.exists(filename):
            record_exception(ErrorCode.ERR_COMPILE_GENERAL,"错误: 找不到指定的文件")
            return 1
        if not filename.endswith(".qot") and not filename.endswith('.qbc'):
            record_exception(ErrorCode.ERR_COMPILE_GENERAL,"错误: 脚本文件必须以.qot或.qbc为后缀")
            return 1
        use_optimzconsole = '--console' in sys.argv or '-cole' in sys.argv
        # P0.2 args_get：run 子命令的脚本参数透传（argv[3..]，剔除控制开关）。
        # native 侧口径 = main(argc, argv) 的 argv[1..] —— 两侧都不含程序名
        # 与脚本名。std.cli（M5）的 parse_flags 以此为地基。
        try:
            from primitives import set_script_args
            set_script_args([a for a in sys.argv[3:] if a not in ('--console', '-cole')])
        except ImportError:
            pass
        if use_optimzconsole:
            from console import PrintCapture
            def run_script():
                execute_script(filename)
            capture = PrintCapture(
                host="0.0.0.0",
                port=8000,
                enable_demo=False,
                demo_interval=2.0,
                html_template_path=os.path.join(os.path.dirname(__file__), 'plugin', 'myconsole', 'index.html')
            )
            capture.set_script(run_script)
            print(f"[系统] 启动控制台服务器...")
            capture.run()
        else:
            execute_script(filename)
    elif sys.argv[1] == "bstrust":
        if len(sys.argv) < 3:
            print("用法: bstrust <subcommand> [args]")
            print("子命令:")
            print("  compile <input.qot> [output.qtc]  - 编译为字节码文件")
            print("  run <file.qtc>                    - 运行字节码文件")
            sys.exit(1)
        subcmd = sys.argv[2]
        if subcmd == "compile":
            if len(sys.argv) < 4:
                print("用法: bstrust compile <input.qot> [output.qtc] [--inline 0|1] [--opt]")
                print("参数:")
                print("  --inline 0|1    是否启用内联模式 (默认: 0)")
                print("  --opt           启用优化 (死代码消除、常量折叠等)")
                sys.exit(1)
            
            input_file = sys.argv[3]
            output_file = None
            inline_mode = 0
            enable_opt = False
            i = 4
            while i < len(sys.argv):
                arg = sys.argv[i]
                if arg == '--inline' and i + 1 < len(sys.argv):
                    inline_mode = int(sys.argv[i + 1])
                    i += 2
                elif arg == '--opt':
                    enable_opt = True
                    i += 1
                elif output_file is None:
                    output_file = arg
                    i += 1
                else:
                    print(f"警告: 未知参数 {arg}")
                    i += 1
            
            if output_file is None:
                output_file = input_file.replace('.qot', '.qtc')
            
            if not os.path.exists(input_file):
                record_exception(ErrorCode.ERR_COMPILE_GENERAL,f"错误: 源文件不存在 - {input_file}")
                sys.exit(1)
            if not input_file.endswith('.qot'):
                record_exception(ErrorCode.ERR_COMPILE_GENERAL,"错误: 源文件必须是.qot后缀")
                sys.exit(1)
            
            try:
                if inline_mode == 1:
                    from require_inliner import inline_requires
                    import tempfile
                    temp_fd, temp_file = tempfile.mkstemp(suffix='.qot', prefix='inlined_', text=True)
                    os.close(temp_fd)
                    try:
                        inline_requires(input_file, temp_file)
                        _compile_to_qtc(temp_file, output_file, enable_opt)
                        print(f"编译成功: {output_file}")
                    finally:
                        if os.path.exists(temp_file):
                            os.remove(temp_file)
                else:
                    _compile_to_qtc(input_file, output_file, enable_opt)
                    print(f"编译成功: {output_file}")
                        
            except Exception as e:
                record_exception(ErrorCode.ERR_COMPILE_GENERAL,f"编译失败: {e}")
                #traceback.print_tb(e.__traceback__)
                sys.exit(1)
        
        elif subcmd == "run":
            if len(sys.argv) < 4:
                print("用法: bstrust run <file.qtc> [--debug]")
                sys.exit(1)
            target_file = sys.argv[3]
            debug = '--debug' in sys.argv or '-d' in sys.argv
    
            if not os.path.exists(target_file):
                record_exception(ErrorCode.ERR_COMPILE_GENERAL,f"错误: 文件不存在 - {target_file}")
                sys.exit(1)
    
            if not target_file.endswith('.qtc'):
                record_exception(ErrorCode.ERR_COMPILE_GENERAL,"错误: 文件必须是.qtc后缀")
                sys.exit(1)
    
            try:
                _run_with_dll(target_file, debug)
            except Exception as e:
                record_exception(ErrorCode.ERR_COMPILE_GENERAL,f"运行失败: {e}")
                traceback.print_tb(e.__traceback__)
                sys.exit(1)
        
        else:
            print(f"错误: 未知子命令 '{subcmd}'")
            print("可用子命令: compile, run")
            sys.exit(1)
    elif sys.argv[1] == "aot":
        # 后端 A（SPEC 7.4 T-42）：.qot → LIR → C 源码 → g++ /O2 → 原生 exe
        # 【QOT-CI】编译管线 CI 旗标（ §9）：
        #   --split-tu       L1 切分 TU（模块级增量对象 + 增量链接；QOT_SPLIT_TU=1 同）
        #   --cache-stats    构建后打印各层缓存命中率（K4）
        #   --explain        打印本次各层 hit/miss 与 key 组件 diff（K4）
        #   --gc-cache       执行缓存 GC（LRU + PCH 只留 2 版）后退出
        #   --determinism    K3 自测：同输入连编两次（全冷）比产物 sha256
        #   -j N / --jobs N  TU 级并行度（默认 min(4, cpu)；QOT_JOBS 同）
        # 环境开关：QOT_CACHE=on|off|pch（K5 一键全冷）、QOT_CACHE_DIR、
        #           QOT_SPLIT_TU、QOT_JOBS
        if len(sys.argv) < 4:
            print("用法: aot <input.qot> <output.exe> [--opt] [--keep-c] "
                  "[--emit-c <file.cpp>] [--unsafe-index] [--split-tu] "
                  "[-j N] [--cache-stats] [--explain] [--determinism] [--gc-cache]")
            print("  把 QOT 程序定形编译为独立原生程序（后端 A：LIR → C → g++）")
            print("  --unsafe-index  上帝法则配置：移除下标边界检查（越界=UB，")
            print("                  性能取舍由开发者自担；默认关闭=越界得 null）")
            print("  --split-tu      切分 TU 增量编译（模块对象缓存 + 增量链接）")
            print("  --cache-stats   打印编译缓存命中率（工具链/PCH/前端/对象/链接）")
            print("  --determinism   确定性自测：同输入连编两次比产物 hash（K3）")
            print("  --static/--no-static  链接形态（默认静态 = 无 DLL 依赖；")
            print("                        --no-static 动态链接：链接更快、产物更小）")
            print("  --gc-cache      缓存 GC（LRU，上限 QOT_CACHE_MAX_BYTES，默认 4GiB）")
            sys.exit(1)
        input_file = sys.argv[2]
        output_file = sys.argv[3]
        aot_opt = '--opt' in sys.argv
        aot_keep_c = '--keep-c' in sys.argv
        aot_split = '--split-tu' in sys.argv
        aot_cache_stats = '--cache-stats' in sys.argv
        aot_explain = '--explain' in sys.argv
        aot_determinism = '--determinism' in sys.argv
        if '--gc-cache' in sys.argv:
            import qot_cache as _qcg
            import json as _json
            print(_json.dumps(_qcg.gc_cache(), ensure_ascii=False, indent=1))
            sys.exit(0)
        if '--unsafe-index' in sys.argv:
            # G-14 约定：开关由各 pass 自检环境变量，CLI 只负责把配置接进来
            os.environ['QOT_UNSAFE_INDEX'] = '1'
        # 静态链接形态（默认静态 = 既有行为；--no-static 走动态，链接更快、
        # 产物更小，代价是分发需带运行时）。进 flags → 进 key（K2）。
        if '--no-static' in sys.argv:
            os.environ['QOT_STATIC'] = '0'
        elif '--static' in sys.argv:
            os.environ['QOT_STATIC'] = '1'
        aot_emit_c = None
        aot_jobs = None
        i = 4
        while i < len(sys.argv):
            arg = sys.argv[i]
            if arg == '--emit-c' and i + 1 < len(sys.argv):
                aot_emit_c = sys.argv[i + 1]
                i += 2
            elif arg in ('-j', '--jobs') and i + 1 < len(sys.argv):
                try:
                    aot_jobs = max(1, int(sys.argv[i + 1]))
                except ValueError:
                    pass
                i += 2
            else:
                i += 1
        if not os.path.exists(input_file):
            record_exception(ErrorCode.ERR_COMPILE_GENERAL, f"错误: 源文件不存在 - {input_file}")
            sys.exit(1)
        if not input_file.endswith('.qot'):
            record_exception(ErrorCode.ERR_COMPILE_GENERAL, "错误: 源文件必须是.qot后缀")
            sys.exit(1)
        try:
            csrc = _aot_compile(input_file, output_file, aot_opt,
                                keep_c=aot_keep_c, emit_c=aot_emit_c,
                                split_tu=aot_split, jobs=aot_jobs)
            print(f"AOT 编译成功: {output_file} (C 源: {csrc})")
            if aot_determinism:
                # K3 自测：全冷连编两次，产物必须字节级一致（确定性是缓存的
                # 前置证，不是后置假设 —— §9.3-K3）
                import qot_cache as _qcd
                h1 = _qcd.file_hash(output_file)
                os.environ['QOT_CACHE'] = 'off'
                _aot_compile(input_file, output_file, aot_opt,
                             split_tu=aot_split, jobs=aot_jobs)
                h2 = _qcd.file_hash(output_file)
                if h1 != h2:
                    print(f"[FAIL] 确定性自测不通过: {h1[:16]} != {h2[:16]}")
                    sys.exit(1)
                print(f"[OK] 确定性自测通过: {h1[:16]}")
            if aot_cache_stats:
                import qot_cache as _qcs
                print('---- 编译缓存统计 ----')
                print(_qcs.format_stats())
            if aot_explain:
                import qot_cache as _qce
                print('---- key 组件解释（--explain） ----')
                print(_qce.format_explain())
        except SystemExit:
            raise
        except Exception as e:
            record_exception(ErrorCode.ERR_COMPILE_GENERAL, f"AOT 编译失败: {e}")
            sys.exit(1)
    elif sys.argv[1] == "watch":
        # 【QOT-CI · L3】常驻构建守护：热解释器 + 全层缓存；文件变化增量重建。
        # stdin 命令面（行协议）：build / stats / gc / stop
        if len(sys.argv) < 4:
            print("用法: watch <input.qot> <output.exe> [--opt] [--interval 秒]")
            sys.exit(1)
        w_input = sys.argv[2]
        w_out = sys.argv[3]
        w_opt = '--opt' in sys.argv
        w_interval = 0.5
        if '--interval' in sys.argv:
            try:
                w_interval = float(sys.argv[sys.argv.index('--interval') + 1])
            except (ValueError, IndexError):
                pass
        if not os.path.exists(w_input):
            record_exception(ErrorCode.ERR_COMPILE_GENERAL, f"错误: 源文件不存在 - {w_input}")
            sys.exit(1)

        def _watch_rebuild(src, exe):
            return _aot_compile(src, exe, w_opt)

        import qot_build as _qbw
        sys.exit(_qbw.watch(w_input, w_out, interval=w_interval,
                            rebuild_fn=_watch_rebuild))
    elif sys.argv[1] == "pkg":
        # M6（0）：包格式 + 构建流水线（init/build/run）。
        # 实现独立成 qot_pkg.py（index.py 不再吸收逻辑，R11）；验收 =
        # 「拿到包目录 → python index.py pkg run <dir>」一条命令跑起来。
        import qot_pkg as _pkg
        sys.exit(_pkg.main(sys.argv[2:]))
    elif sys.argv[1] == "build":
        if len(sys.argv) < 3:
            print("用法: build <file.qot> [options]")
            print("选项:")
            print("  --mode <1|2|3>           编译模式: 1 - V1, 2 - V2 (默认2) , 3 - V3")
            print("  --icon <icon_path>     图标文件路径")
            print("  --exportfile <path>    额外资源文件(可多次使用，最多15个)")
            print("  --output <output_path> 输出文件路径")
            print("示例:")
            print("  build script.qot --mode 2 --icon logo.ico --exportfile data.txt --exportfile config.json")
            return 1

        filename = sys.argv[2]
        if not filename.endswith(".qot") and not filename.endswith('.qtc'):
            record_exception(ErrorCode.ERR_COMPILE_GENERAL,"错误: 脚本文件必须以.qot或.qtc为后缀")
            return 1
        if not os.path.exists(filename):
            record_exception(ErrorCode.ERR_COMPILE_GENERAL,"错误: 找不到指定的文件")
            return 1
    
        mode = 2
        icon_path = None
        extra_files = []
        output_path = None
        i = 3
        while i < len(sys.argv):
            arg = sys.argv[i]
            if arg == '--mode' and i + 1 < len(sys.argv):
                mode = int(sys.argv[i + 1])
                i += 2
            elif arg == '--icon' and i + 1 < len(sys.argv):
                icon_path = sys.argv[i + 1]
                i += 2
            elif arg == '--exportfile' and i + 1 < len(sys.argv):
                extra_files.append(sys.argv[i + 1])
                i += 2
            elif arg == '--output' and i + 1 < len(sys.argv): 
                output_path = sys.argv[i + 1]
                i += 2
            elif arg.startswith('--'):
                print(f"警告: 未知参数 {arg}")
                i += 1
            else:
                if arg.isdigit():
                    mode = int(arg)
                elif os.path.exists(arg) and (arg.endswith('.ico') or arg.endswith('.png')):
                    icon_path = arg
                else:
                    print(f"警告: 未知参数 {arg}")
                i += 1

        if mode == 1:
            if icon_path is None:
                if input("目前只支持部分语法编译,确定要继续吗? y/n: ").lower().startswith('y'):
                    a_icon = input("请输入图标路径:")
                    if not os.path.exists(a_icon):
                        record_exception(ErrorCode.ERR_COMPILE_GENERAL,"错误: 找不到指定的图标文件")
                        a_icon = os.path.join(os.getcwd(), 'logo.ico')
                    build_tocpp_script(filename, a_icon)
                else:
                    return 1
            else:
                if not os.path.exists(icon_path):
                    record_exception(ErrorCode.ERR_COMPILE_GENERAL,"错误: 找不到指定的图标文件")
                    icon_path = os.path.join(os.getcwd(), 'logo.ico')
                build_tocpp_script(filename, icon_path)

        elif mode == 2:
            try:
                from building import pack_qtc_to_exe
                pack_qtc_to_exe(filename, icon_path=icon_path, extra_files=extra_files)
            except Exception as e:
                record_exception(ErrorCode.ERR_COMPILE_GENERAL,f"编译失败: {e}")
                traceback.print_tb(e.__traceback__)
                return 1
        elif mode == 3:
            try:
                from building_v3 import pack_qtc_to_exe_v3
                pack_qtc_to_exe_v3(filename, icon_path=icon_path, extra_files=extra_files, output_path=output_path)
            except Exception as e:
                record_exception(ErrorCode.ERR_COMPILE_GENERAL,f"编译失败 (V3): {e}")
                traceback.print_tb(e.__traceback__)
                return 1
        else:
            record_exception(ErrorCode.ERR_COMPILE_GENERAL,f"错误: 无效的 mode '{mode}'，只能是 1 或 2 或 3")
            return 1
        
    elif sys.argv[1] == "help":
        print("- run [filepath]: 执行指定的QOT脚本")
        print("- build [filepath] [mode] [ico_path]: 编译指定的QOT脚本")
        print("- aot [input.qot] [output.exe]: 定形编译为原生程序（后端 A，需 MSVC）")
        print("- hdps: 帮助文档-插件助手(访问地址：http://localhost:8080)")
        print("- bstrust [subcommand] [args]: 字节码相关操作")
        print("- distrust [input.qot] [output.qbc]: 重构指定的QOT脚本")
    elif sys.argv[1] == "hdps":
        pm  = PHPLocalServer()
        script_path = os.path.abspath(__file__)
        script_dir = os.path.dirname(script_path)
        pm.set_base_dir(script_dir+'\\plugin\\')
        pm.start()
    elif sys.argv[1] == "distrust":
        if len(sys.argv) < 3:
            print("用法: distrust <input.qot> [output.qbc]")
            sys.exit(1)
        input_file = sys.argv[2]
        output_file = sys.argv[3] if len(sys.argv) > 3 else input_file.replace('.qot', '.qbc')
        ast = parse_oning_gram(input_file)
        if ast and ast != 1:
            encoded = encode_ast(ast)
            with open(output_file, 'wb') as f:
                f.write(encoded)
            print(f"重构完成: {output_file}")
    else:
        record_exception(ErrorCode.ERR_COMPILE_GENERAL,f"错误: 未知命令 '{sys.argv[1]}'")
        return 1
    return 0

if __name__ == "__main__":
    sys.exit(main())