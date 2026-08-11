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
    NULL_MAGIC = 0x4E554C4C
    RPN_LOOP_START = 200
    RPN_LOOP_END = 201


class VMCode:
    __slots__ = ['instructions', 'constants', 'var_names', 'func_names',
                 'line_numbers', 'source_file', 'local_names', 'is_function',
                 '_const_index', '_var_index', '_func_index']

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
        self._rpn_loop_counter = 0
        self._for_counter = 0
        self._rpn_debug = False

    def set_base_path(self, path):
        self.base_path = os.path.dirname(os.path.abspath(path))

    def set_rpn_debug(self, enabled):
        self._rpn_debug = enabled

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

    def _is_pure_numeric_stmt(self, node) -> bool:
        if node is None:
            return True
        
        if not isinstance(node, tuple):
            return True
        
        node_type = node[0]
        
        if node_type == 'binary_op':
            op = node[1]
            if op not in ('+', '-', '*', '/', '%', '^', 
                          '>', '<', '>=', '<=', '==', '!='):
                return False
            return (self._is_pure_numeric_stmt(node[2]) and 
                    self._is_pure_numeric_stmt(node[3]))
        
        elif node_type == 'unary_op':
            op = node[1]
            if op not in ('UMINUS', '!'):
                return False
            return self._is_pure_numeric_stmt(node[2])
        
        elif node_type == 'identifier':
            return True
        
        elif node_type == 'literal':
            value = node[1]
            return isinstance(value, (int, float))
        
        elif node_type == 'assignment':
            return self._is_pure_numeric_stmt(node[2])
        
        elif node_type == 'var_declaration':
            expr = node[2] if len(node) > 2 else None
            if expr:
                return self._is_pure_numeric_stmt(expr)
            return True
        
        elif node_type == 'parenthesized':
            return self._is_pure_numeric_stmt(node[1])
        
        elif node_type == 'while_statement':
            _, condition, body = node
            return (self._is_pure_numeric_stmt(condition) and 
                    self._is_pure_numeric_stmt(body))
        
        elif node_type == 'if_statement':
            return False
        elif node_type in ('function_call', 'method_call', 'method_call_statement',
                           'require_statement', 'array_index', 'array_assignment',
                           'return_statement', 'stop_statement', 'break_statement'):
            return False
        
        return False
    
    def _is_pure_numeric_loop_body(self, body) -> bool:
    
        if body is None:
            return True
        
        if isinstance(body, list):
            for stmt in body:
                if not self._is_pure_numeric_stmt(stmt):
                    return False
            return True
        else:
            return self._is_pure_numeric_stmt(body)
    
    def _compile_method_call_as_function(self, node, code: VMCode, is_statement=True):
        _, caller_node, method_name, args = node
        self._compile_node(caller_node, code)
        arg_count = 1  
        if args:
            for arg in args:
                if arg is not None:
                    self._compile_node(arg, code)
                    arg_count += 1
        func_idx = code.intern_func(method_name)
        code.instructions.append((VMInstruction.CALL, (func_idx, arg_count)))
        if is_statement:
            code.instructions.append((VMInstruction.POP_STMT, None))
    
    def intern_global(self, name):
        idx = self._global_index.get(name)
        if idx is None:
            idx = len(self.global_names)
            self.global_names.append(name)
            self._global_index[name] = idx
        return idx

    def _var_operand(self, code, name, declaring=False):
        if code.is_function:
            if declaring or name.startswith('__'):
                code.local_names.add(name)
            if name in code.local_names:
                return code.intern_var(name)
        return GLOBAL_BIT | self.intern_global(name)

    def compile_ast(self, ast) -> VMCode:
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
        return code
    
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
            if isinstance(value, str):
                try:
                    if '.' in value:
                        value = float(value)
                    else:
                        value = int(value)
                except ValueError:
                    pass
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
            arg_count = 0
            if args:
                for arg in args:
                    if arg is not None:
                        self._compile_node(arg, code)
                        arg_count += 1
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
            is_pure_numeric = self._is_pure_numeric_loop_body(body)
            if is_pure_numeric and self._rpn_debug:
                print(f"[Compiler] 检测到纯数值 while 循环，使用 RPN 优化")
            if is_pure_numeric:
                self._rpn_loop_counter += 1
                loop_id = self._rpn_loop_counter
                code.instructions.append((VMInstruction.RPN_LOOP_START, loop_id))
            loop_start = len(code.instructions)
            self._compile_node(condition, code)
            jump_out_pos = len(code.instructions)
            code.instructions.append((VMInstruction.JUMP_IF_FALSE, 0))
            self.loop_stack.append({
                'start': loop_start,
                'jump_out_pos': jump_out_pos,
                'break_positions': []
            })
            
            if isinstance(body, list):
                for stmt in body:
                    if stmt:
                        self._compile_node(stmt, code)
            else:
                self._compile_node(body, code)
            
            code.instructions.append((VMInstruction.JUMP, loop_start))
            loop_end = len(code.instructions)
            loop_info = self.loop_stack.pop()
            code.instructions[loop_info['jump_out_pos']] = (VMInstruction.JUMP_IF_FALSE, loop_end)
            for break_pos in loop_info['break_positions']:
                code.instructions[break_pos] = (VMInstruction.BREAK, loop_end)
            if is_pure_numeric:
                code.instructions.append((VMInstruction.RPN_LOOP_END, loop_id))
      
        
        elif node_type == 'stop_statement':
            if not self.loop_stack:
                raise ValueError("stop 语句只能在循环内使用")
            loop_info = self.loop_stack[-1]
            break_pos = len(code.instructions)
            code.instructions.append((VMInstruction.BREAK, 0))  
            loop_info['break_positions'].append(break_pos)
        elif node_type == 'for_statement':
            var_name = node[1] if len(node) > 1 else None
            range_expr = node[2] if len(node) > 2 else None
            body = node[3] if len(node) > 3 else None
            line = node[-1] if len(node) > 4 else (node[4] if len(node) == 5 else 0)
            if var_name and code.is_function:
                code.local_names.add(var_name)
            self._for_counter += 1
            unique_id = self._for_counter
            range_tmp = f'__for_range_{unique_id}'
            decl_range = ('var_declaration', range_tmp, range_expr)
            self._compile_node(decl_range, code)
            idx_tmp = f'__for_idx_{unique_id}'
            decl_idx = ('var_declaration', idx_tmp, ('literal', 0))
            self._compile_node(decl_idx, code)
            limit_tmp = f'__for_limit_{unique_id}'
            limit_call = ('function_call', '__iter_len', [('identifier', range_tmp)])
            decl_limit = ('var_declaration', limit_tmp, limit_call)
            self._compile_node(decl_limit, code)
            condition = ('binary_op', '<', ('identifier', idx_tmp), ('identifier', limit_tmp))
            inc_idx = ('assignment', idx_tmp, ('binary_op', '+', ('identifier', idx_tmp), ('literal', 1)))
            body_list = body if isinstance(body, list) else [body]
            numeric_body = [('assignment', var_name, ('identifier', idx_tmp))]
            numeric_body.extend(body_list)
            numeric_body.append(inc_idx)
            if self._range_is_statically_numeric(range_expr):
                self._compile_node(('while_statement', condition, numeric_body), code)
            else:
                seq_body = [('assignment', var_name,
                             ('array_index', range_tmp, ('identifier', idx_tmp)))]
                seq_body.extend(body_list)
                seq_body.append(inc_idx)
                isnum_tmp = f'__for_isnum_{unique_id}'
                self._compile_node(
                    ('var_declaration', isnum_tmp,
                     ('function_call', '__is_number', [('identifier', range_tmp)])), code)
                self._compile_node(
                    ('if_statement',
                     ('identifier', isnum_tmp),
                     [('while_statement', condition, numeric_body)],
                     None,
                     ('else_clause', [('while_statement', condition, seq_body)])), code)
        
        
        elif node_type == 'func_declaration':
            _, return_type, func_name, params, body = node
            func_code = VMCode()
            func_code.source_file = code.source_file
            func_code.intern_func(func_name)
            for fn in code.func_names:
                func_code.intern_func(fn)
            func_code.is_function = True
            for param in params:
                func_code.local_names.add(param)
                func_code.intern_var(param)
            if isinstance(body, list):
                for stmt in body:
                    if stmt:
                        self._compile_node(stmt, func_code)
            else:
                self._compile_node(body, func_code)
            if not func_code.instructions or func_code.instructions[-1][0] != VMInstruction.RETURN:
                func_code.instructions.append((VMInstruction.PUSH_CONST, VMInstruction.NULL_MAGIC))
                func_code.instructions.append((VMInstruction.RETURN, None))
            const_idx = code.intern_const(func_code)
            code.instructions.append((VMInstruction.PUSH_CONST, const_idx))
            var_idx = self._var_operand(code, func_name, declaring=True)
            code.instructions.append((VMInstruction.STORE_VAR, var_idx))
        
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