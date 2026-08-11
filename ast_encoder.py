import sys
import pickle
import zlib
from typing import Any, Dict, List, Tuple, Set

class ASTEncoder:
    def __init__(self):
        self.string_table = []
        self.string_map = {}
        self.var_table = []
        self.var_map = {}
        self.execution_map = {}
        self.node_ids = {}
        self.node_counter = 0
        self.inline_functions = {}
        
    def _get_node_id(self, node) -> int:
        node_key = id(node)
        if node_key not in self.node_ids:
            self.node_counter += 1
            self.node_ids[node_key] = self.node_counter
        return self.node_ids[node_key]
    
    def _encode_string(self, s: str) -> tuple:
        if s not in self.string_map:
            self.string_map[s] = len(self.string_table)
            self.string_table.append(s)
        return ('$s', self.string_map[s])
    
    def _encode_identifier(self, name: str) -> tuple:
        if name not in self.var_map:
            self.var_map[name] = len(self.var_table)
            self.var_table.append(name)
        return ('$v', self.var_map[name])
    
    def _is_static(self, node) -> bool:
        if not isinstance(node, tuple):
            return True
        
        node_type = node[0] if node else None
        
        if node_type == 'literal':
            return True
        
        if node_type == 'binary_op':
            return self._is_static(node[2]) and self._is_static(node[3])
        
        if node_type == 'unary_op':
            return self._is_static(node[2])
        
        if node_type == 'array':
            elements = node[1] if len(node) > 1 else []
            return all(self._is_static(elem) for elem in elements if elem)
        
        if node_type == 'dict':
            entries = node[1] if len(node) > 1 else []
            for entry in entries:
                if len(entry) >= 3:
                    key_node, value_node, _ = entry
                else:
                    key_node, value_node = entry
                if not self._is_static(key_node) or not self._is_static(value_node):
                    return False
            return True
        return False
    
    def _eval_static(self, node):
        
        if not isinstance(node, tuple):
            return node
        
        node_type = node[0] if node else None
        
        if node_type == 'literal':
            return node[1]
        
        if node_type == 'binary_op':
            op = node[1]
            left = self._eval_static(node[2])
            right = self._eval_static(node[3])
            if isinstance(left, (int, float)) and isinstance(right, (int, float)):
                if op == '+': return left + right
                if op == '-': return left - right
                if op == '*': return left * right
                if op == '/': return left / right
                if op == '%': return left % right
                if op == '>': return left > right
                if op == '<': return left < right
                if op == '>=': return left >= right
                if op == '<=': return left <= right
                if op == '==': return left == right
                if op == '!=': return left != right
            if op == '+' and isinstance(left, str) and isinstance(right, str):
                return left + right
            if op in ('AND', '&&'):
                return left and right
            if op in ('OR', '||'):
                return left or right
            return node
        
        if node_type == 'unary_op':
            op = node[1]
            expr = self._eval_static(node[2])
            
            if op == 'UMINUS' and isinstance(expr, (int, float)):
                return -expr
            
            return node
        
        if node_type == 'array':
            elements = node[1] if len(node) > 1 else []
            result = []
            for elem in elements:
                if elem:
                    result.append(self._eval_static(elem))
                else:
                    result.append(None)
            return result
        
        if node_type == 'dict':
            entries = node[1] if len(node) > 1 else []
            result = {}
            for entry in entries:
                if len(entry) >= 3:
                    key_node, value_node, _ = entry
                else:
                    key_node, value_node = entry
                key = self._eval_static(key_node)
                value = self._eval_static(value_node)
                result[key] = value
            return result
        
        return node
    
    def _find_loop_invariants(self, node, loop_vars: Set[str]) -> Dict[int, bool]:
        invariants = {}
        
        def scan(n):
            if not isinstance(n, tuple):
                return
            node_type = n[0] if n else None
            if node_type == 'identifier':
                var_name = n[1]
                if var_name not in loop_vars:
                    nid = self._get_node_id(n)
                    invariants[nid] = True
            elif node_type == 'binary_op' or node_type == 'unary_op':
                all_invariant = True
                for child in n[1:]:
                    if isinstance(child, (tuple, list)):
                        child_id = self._get_node_id(child) if isinstance(child, tuple) else None
                        if child_id and child_id not in invariants:
                            scan(child)
                        if child_id and child_id not in invariants:
                            all_invariant = False
                if all_invariant:
                    nid = self._get_node_id(n)
                    invariants[nid] = True
            else:
                for child in n[1:]:
                    if isinstance(child, (tuple, list)):
                        scan(child)
        
        scan(node)
        return invariants
    
    def encode(self, ast_node, loop_context: Set[str] = None):
        if not isinstance(ast_node, tuple):
            return ast_node
        
        node_id = self._get_node_id(ast_node)
        node_type = ast_node[0] if ast_node else None
        if self._is_static(ast_node):
            try:
                self.execution_map[node_id] = self._eval_static(ast_node)
            except:
                pass  
        if node_type == 'literal' and len(ast_node) >= 2 and isinstance(ast_node[1], str):
            return ('literal', self._encode_string(ast_node[1]), node_id)
        
        if node_type == 'identifier' and len(ast_node) >= 2:
            return ('identifier', self._encode_identifier(ast_node[1]), node_id)
        
        if node_type == 'var_declaration' and len(ast_node) >= 3:
            return ('var_declaration', 
                    self._encode_identifier(ast_node[1]),
                    self.encode(ast_node[2], loop_context),
                    node_id)
        
        if node_type == 'const_declaration' and len(ast_node) >= 3:
            return ('const_declaration',
                    self._encode_identifier(ast_node[1]),
                    self.encode(ast_node[2], loop_context),
                    node_id)
        
        if node_type == 'assignment' and len(ast_node) >= 3:
            return ('assignment',
                    self._encode_identifier(ast_node[1]),
                    self.encode(ast_node[2], loop_context),
                    node_id)
        
        if node_type == 'binary_op' and len(ast_node) >= 4:
            is_invariant = False
            if loop_context and node_id in self._find_loop_invariants(ast_node, loop_context):
                is_invariant = True
            return ('binary_op', 
                    ast_node[1],
                    self.encode(ast_node[2], loop_context),
                    self.encode(ast_node[3], loop_context),
                    node_id,
                    is_invariant)
        
        if node_type == 'unary_op' and len(ast_node) >= 3:
            return ('unary_op',
                    ast_node[1],
                    self.encode(ast_node[2], loop_context),
                    node_id)
        
        if node_type == 'function_call' and len(ast_node) >= 3:
            return ('function_call',
                    self._encode_identifier(ast_node[1]),
                    [self.encode(arg, loop_context) for arg in ast_node[2]] if ast_node[2] else [],
                    node_id)
        
        if node_type == 'method_call' and len(ast_node) >= 4:
            return ('method_call',
                    self.encode(ast_node[1], loop_context),
                    self._encode_identifier(ast_node[2]),
                    [self.encode(arg, loop_context) for arg in ast_node[3]] if ast_node[3] else [],
                    node_id)
        
        if node_type == 'while_statement' and len(ast_node) >= 3:
            loop_vars = self._collect_loop_vars(ast_node[2])
            return ('while_statement',
                    self.encode(ast_node[1], loop_vars),
                    self.encode(ast_node[2], loop_vars),
                    node_id)
        
        if node_type == 'for_statement' and len(ast_node) >= 4:
            loop_vars = {ast_node[1]}
            return ('for_statement',
                    ast_node[1],
                    self.encode(ast_node[2], loop_vars),
                    self.encode(ast_node[3], loop_vars),
                    node_id)
        
        if node_type == 'array_index' and len(ast_node) >= 3:
            return ('array_index',
                    self._encode_identifier(ast_node[1]),
                    self.encode(ast_node[2], loop_context),
                    node_id)
        
        if node_type == 'array_assignment' and len(ast_node) >= 3:
            return ('array_assignment',
                    self.encode(ast_node[1], loop_context),
                    self.encode(ast_node[2], loop_context),
                    node_id)
        
        if node_type == 'if_statement' and len(ast_node) >= 3:
            return ('if_statement',
                    self.encode(ast_node[1], loop_context),
                    self.encode(ast_node[2], loop_context),
                    self.encode(ast_node[3], loop_context) if len(ast_node) > 3 else None,
                    self.encode(ast_node[4], loop_context) if len(ast_node) > 4 else None,
                    node_id)
        encoded = []
        for item in ast_node:
            if isinstance(item, (tuple, list)):
                encoded.append(self.encode(item, loop_context))
            else:
                encoded.append(item)
        
        if isinstance(ast_node, list):
            return encoded
        return tuple(encoded)
    
    def _collect_loop_vars(self, body) -> Set[str]:
        loop_vars = set()
        
        def collect(node):
            if not isinstance(node, tuple):
                return
            node_type = node[0] if node else None
            
            if node_type in ('assignment', 'var_declaration', 'const_declaration'):
                if len(node) >= 2:
                    var_name = node[1]
                    if isinstance(var_name, str):
                        loop_vars.add(var_name)
            elif node_type == 'array_assignment' and len(node) >= 2:
                array_part = node[1]
                if isinstance(array_part, tuple) and array_part[0] == 'array_index':
                    var_name = array_part[1]
                    if isinstance(var_name, str):
                        loop_vars.add(var_name)
            
            for child in node[1:]:
                if isinstance(child, (tuple, list)):
                    collect(child)
        
        collect(body)
        return loop_vars
    
    def encode_to_bytes(self, ast) -> bytes:
        encoded_ast = self.encode(ast)
        data = {
            'version': 2,
            'strings': self.string_table,
            'vars': self.var_table,
            'ast': encoded_ast,
            'exec_map': self.execution_map, 
            'node_ids': self.node_ids,       
        }
        return zlib.compress(pickle.dumps(data, protocol=pickle.HIGHEST_PROTOCOL))
    
    def decode_from_bytes(self, data: bytes):
        loaded = pickle.loads(zlib.decompress(data))
        version = loaded.get('version', 1)
        if version == 2 and loaded.get('type') == 'optimized_ast':
            self.string_table = loaded['strings']
            self.var_table = loaded['vars']
            self.execution_map = loaded.get('exec_map', {})
            self.node_ids = loaded.get('node_ids', {})
            return {
            'ast': loaded['ast'],
            'exec_map': self.execution_map,
            'strings': self.string_table,
            'vars': self.var_table,
        }
        self.string_table = loaded['strings']
        self.var_table = loaded['vars']
        self.execution_map = {}
        return self._decode(loaded['ast'])
    
    def _decode(self, node):
        if not isinstance(node, tuple):
            return node
        
        if len(node) >= 2 and node[0] == '$s':
            return ('literal', self.string_table[node[1]])
        
        if len(node) >= 2 and node[0] == '$v':
            return ('identifier', self.var_table[node[1]])
        
        decoded = []
        for item in node:
            if isinstance(item, (tuple, list)):
                decoded.append(self._decode(item))
            else:
                decoded.append(item)
        
        if isinstance(node, list):
            return decoded
        return tuple(decoded)
class FastExecutor:
    def __init__(self, compiled_data):
        self.exec_map = compiled_data.get('exec_map', {})
        self.strings = compiled_data.get('strings', [])
        self.vars = compiled_data.get('vars', [])
        self.ast = compiled_data.get('ast')
        self.cache_hits = 0
        self.cache_misses = 0
    
    def execute(self, evaluator, node):
        node_id = self._get_node_id_from_node(node)
        if node_id is not None and node_id in self.exec_map:
            self.cache_hits += 1
            return self.exec_map[node_id]
        self.cache_misses += 1
        return evaluator._visit_impl(node)
    
    def _get_node_id_from_node(self, node):
        if isinstance(node, tuple) and len(node) > 0:
            last = node[-1]
            if isinstance(last, int) and last > 0:
                return last
        return None
_encoder = None

def get_encoder():
    global _encoder
    if _encoder is None:
        _encoder = ASTEncoder()
    return _encoder

def encode_ast(ast) -> bytes:
    return get_encoder().encode_to_bytes(ast)

def decode_ast(data: bytes):
    return get_encoder().decode_from_bytes(data)

def create_fast_executor(data: bytes):
    compiled = decode_ast(data)
    return FastExecutor(compiled)