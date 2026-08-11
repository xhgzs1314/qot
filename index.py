import base64
import sys
import threading
import traceback
import types
from typing import Optional, Tuple, Union
from bytecode import VMCode
from error_system import ErrorCode, get_error_handler
from error_system_integration import record_exception
from token_char import TokenType
from ast_encoder import encode_ast, decode_ast
from grammar import parse_oning_gram
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

class ReturnException(Exception):
    def __init__(self, value):
        self.value = value

class Evaluator:
    def __init__(self):
        self.variables = {}
        self.builtin = BuiltFunc()
        self.functions = {}
        self._init_builtin_functions()
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
        self._var_cache[var_name] = value
        return value

    def _set_var_fast(self, var_name, value, var_type='var'):
        self.variables[var_name] = (var_type, value)
        self._var_cache[var_name] = value  
    
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
        old_vars = self.variables.copy()
        old_funcs = self.functions.copy()
        old_modules = self.modules.copy()
        ast = parse_oning_gram(qot_path)
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
        
        new_vars = {}
        for name, (typ, val) in self.variables.items():
            if name not in old_vars and name not in self.builtin.functions:
                new_vars[name] = val
        new_funcs = {}
        func_defs = {}
        for name, fdef in self.functions.items():
            if name not in old_funcs:
                func_defs[name] = fdef
        for name, fdef in func_defs.items():
            new_funcs[name] = self._make_script_function_wrapper(name, new_vars, new_funcs, fdef)
        self.variables = old_vars
        self.functions = old_funcs
        self.modules = old_modules
        module_obj = {}
        module_obj.update(new_vars)
        module_obj.update(new_funcs)
        return module_obj

    def _make_script_function_wrapper(self, func_name, module_vars, module_funcs, func_def):
        def wrapper(*args):
            old_vars = self.variables.copy()
            old_funcs = self.functions.copy()
            try:
                self.variables.update(module_vars)
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
        if isinstance(value, str):
            try:
                if '.' in value:
                    return float(value)
                return int(value)
            except ValueError:
                return value
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
        value = self.visit(('dict', entries))
        self.variables[var_name] = ('var', value)
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

    def visit_method_call(self, node):
        _, caller_node, method_name, args,line = node
        caller_value = self.visit(caller_node)  
        args_value  = []
        if(args != None):
            args_value = [self.visit(arg) for arg in args if arg is not None]  
        args_value.insert(0, caller_value)
        return self.builtin.call_function(method_name, *args_value)

    def visit_for_statement(self, node):
        _, var_name, range_expr, body,line = node
        range_value = self.visit(range_expr)
        iterable = []
        if isinstance(range_value, (int, float)):
            count = int(range_value)
            if count < 0:
                self._raise_error(ErrorCode.ERR_FOR_RANGE_NEGATIVE, "for循环范围不能为负数", node)
            iterable = list(range(count))
        elif isinstance(range_value, (list,types.GeneratorType)):
            iterable = range_value
        else:
            self._raise_error(ErrorCode.ERR_FOR_RANGE_INVALID, f"for循环范围必须是数字或数组，实际是 {type(range_value).__name__}", node)
        pre_loop_vars = set(self.variables.keys())
        result = None
        try:
            for item in iterable:
                self.variables[var_name] = ('loop_var', item)
                result = self.visit(body)
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

    def visit_func_declaration(self, node):
        _, return_type, func_name, params, body,line = node  
        if func_name in self.variables:
            existing_type, _ = self.variables[func_name]
            if existing_type in ('builtin', 'function'):
                self._raise_error(ErrorCode.ERR_FUNCTION_NOT_ISIN, f"函数 '{func_name}' 已存在，无法重复注册", node)
        self.functions[func_name] = {
            'params': params,
            'body': body,
            'return_type': return_type  
        }
        self.variables[func_name] = ('function', func_name)
        def custom_func_wrapper(*args):
            current_vars = self.variables.copy()
            evaluated_args = args  
            for param, arg_val in zip(params, evaluated_args):
                self.variables[param] = ('param', arg_val)
            result = None
            try:
                result = self.visit(body)
            except ReturnException as e:
                result = e.value
            finally:
                self.variables = current_vars
            return result
        self.builtin.register_function(
            func_name, 
            custom_func_wrapper, 
            (len(params), ['any'] * len(params), return_type)
        )
        return None

    def visit_function_call(self, node):
        _, func_name, args,line = node
        if func_name not in self.variables:
            self._raise_error(ErrorCode.ERR_FILE_NOT_FOUND,f"函数 '{func_name}' 未定义",node)
        func_type, func = self.variables[func_name]
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
            current_vars = self.variables.copy()
            evaluated_args = [self.visit(arg) for arg in args if arg is not None]
            for param, arg_val in zip(params, evaluated_args):
                self.variables[param] = ('param', arg_val)
            result = None
            try:
                result = self.visit(body)
            except ReturnException as e:
                result = e.value
            finally:
                self.variables = current_vars
            return result
        else:
            self._raise_error(ErrorCode.ERR_FUNCTION_NOTIT,f"'{func_name}' 不是函数",node)

    def visit_stop_statement(self, node):
        raise LoopStopException("stop语句终止循环")

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
                result = visit(body)
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
        self.variables[var_name] = ('var', value)
        return value

    def visit_const_declaration(self, node):
        _, var_name, expr,line = node
        value = self.visit(expr)
        self.variables[var_name] = ('const', value)
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
        else:
            self._raise_error(ErrorCode.ERR_UNARY_OP_UNKNOWN, f"未知一元运算符: {op}", node)

    def visit_binary_op(self, node):
        _, op, left, right,line = node
        left_val = self.visit(left)
        right_val = self.visit(right)
        if True:
            if op == '+':
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
        _, value,line = node
        if isinstance(value, bytes):
            return value
        if isinstance(value, str):
            try:
                if '.' in value:
                    return float(value)
                return int(value)
            except ValueError:
                return value
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
        self.variables[var_name] = ('var', array_value)
        return array_value

    def visit_function_call_statement(self, node):
        return self.visit_function_call(node[1])

def execute_script(filename: str) -> None:
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
        evaluator = Evaluator()
    if ast and ast != 1:
        evaluator.set_base_path(filename)
        try:
            evaluator.evaluate(ast)
        except ReturnException as e:
            if e.value is not None:
                pass
        """except Exception as e:
            with open('error.log', 'w') as f:
                traceback.print_exc(file=f)"""
def build_tocpp_script(filename: str,icons:str) -> None:
    try:
        ast = parse_oning_gram(filename)
        if ast and ast != 1:
            compile_and_run(ast, icons)
    except Exception as e:
        print(f"{str(e)}", file=sys.stderr)

def _compile_to_qtc(source_file: str, output_file: str,enable_opt:bool):
    from bytecode import Compiler
    from error_system import get_error_handler
    handler = get_error_handler()
    handler.get_collector().clear()
    ast = parse_oning_gram(source_file, True)
    if not ast or ast == 1:
        record_exception(ErrorCode.ERR_COMPILE_GENERAL,f"解析失败: {source_file}")
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
    
    with open(output_file, 'wb') as f:
        f.write(b'QTC\x00')
        f.write(struct.pack('<I', 1))  # version
        f.write(struct.pack('<I', 0))
        f.write(struct.pack('<I', len(code.instructions)))
        for op, arg in code.instructions:
            f.write(struct.pack('<B', op))
            arg_bytes = _serialize_arg(arg)
            f.write(struct.pack('<I', len(arg_bytes)))
            f.write(arg_bytes)
        consts_bytes = _serialize_constants(code.constants)
        f.write(struct.pack('<I', len(consts_bytes)))
        f.write(consts_bytes)
        vars_bytes = _serialize_string_list(code.var_names)
        f.write(struct.pack('<I', len(vars_bytes)))
        f.write(vars_bytes)
        funcs_names_bytes = _serialize_string_list(code.func_names)
        f.write(struct.pack('<I', len(funcs_names_bytes)))
        f.write(funcs_names_bytes)
        source_bytes = code.source_file.encode('utf-8')
        f.write(struct.pack('<I', len(source_bytes)))
        f.write(source_bytes)


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