from typing import Any, Dict, List, Optional, Set, Tuple, Union
NODE_TYPES = {
    'start': 'start',
    'statement_list': 'statement_list',
    'var_declaration': 'var_declaration',
    'const_declaration': 'const_declaration',
    'assignment': 'assignment',
    'array_assignment': 'array_assignment',
    'if_statement': 'if_statement',
    'eif_clause': 'eif_clause',
    'else_clause': 'else_clause',
    'while_statement': 'while_statement',
    'for_statement': 'for_statement',
    'func_declaration': 'func_declaration',
    'return_statement': 'return_statement',
    'stop_statement': 'stop_statement',
    'require_statement': 'require_statement',
    'binary_op': 'binary_op',
    'unary_op': 'unary_op',
    'literal': 'literal',
    'identifier': 'identifier',
    'array': 'array',
    'array_declaration': 'array_declaration',
    'array_index': 'array_index',
    'array_index_chain': 'array_index_chain',
    'function_call': 'function_call',
    'function_call_statement': 'function_call_statement',
    'method_call': 'method_call',
    'method_call_statement': 'method_call_statement',
    'dict': 'dict',
    'dict_declaration': 'dict_declaration',
    'dict_index': 'dict_index',
    'dict_assignment': 'dict_assignment',
    'static_method_call': 'static_method_call',
    'static_method_call_statement': 'static_method_call_statement',
    'static_access': 'static_access',
    'function_ref': 'function_ref',
    'parenthesized': 'parenthesized',
    'annotation': 'annotation',
}


def _make_node(typ: str, *args) -> Tuple:
    return (typ, *args)


class DeadCodeOptimizer:
    def __init__(self):
        self.stats = {
            'const_folded': 0,
            'dead_branches_removed': 0,
            'unreachable_removed': 0,
            'unused_vars_removed': 0,
        }
        self.constants: Dict[str, Any] = {}
        self.modified_vars: Set[str] = set()
        self.used_vars: Set[str] = set()
        self.defined_vars: Set[str] = set()
        self.scope_stack: List[Dict[str, Any]] = []
        self.in_function = False
        self.current_func_constants: List[Dict[str, Any]] = [] 

   
    def optimize(self, ast: Tuple) -> Tuple:
        if not ast or ast == 1:
            return ast

        self._reset()
        self._collect_usage(ast)
        optimized = self._optimize_node(ast)
        optimized = self._cleanup(optimized)
        return optimized

    def get_stats(self) -> Dict[str, int]:
        return self.stats.copy()

    def print_stats(self) -> None:
        print(f"常量折叠: {self.stats['const_folded']}")
        print(f"死分支移除: {self.stats['dead_branches_removed']}")
        print(f"不可达代码移除: {self.stats['unreachable_removed']}")
        print(f"未使用变量移除: {self.stats['unused_vars_removed']}")
        print("=====================\n")

   
    def _reset(self) -> None:
        self.constants.clear()
        self.modified_vars.clear()
        self.used_vars.clear()
        self.defined_vars.clear()
        self.scope_stack.clear()
        self.current_func_constants.clear()
        self.in_function = False
        for k in self.stats:
            self.stats[k] = 0

   
    def _collect_usage(self, node: Union[Tuple, List, Any]) -> None:
        if not node:
            return
        if isinstance(node, tuple):
            typ = node[0]
            if typ == 'identifier':
                self.used_vars.add(node[1])
            elif typ == 'func_declaration':
                name = node[2]
                self.used_vars.add(name)
                params = node[3] if len(node) >= 4 else []
                old_used = self.used_vars.copy()
                old_modified = self.modified_vars.copy()
                old_constants = self.constants.copy()
                self.used_vars = set()
                self.modified_vars = set()
                self.constants = {}
                for p in params:
                    self.defined_vars.add(p)
                body = node[4] if len(node) >= 5 else node[3]
                self._collect_usage(body)
                if not hasattr(self, '_func_used_vars'):
                    self._func_used_vars = {}
                self._func_used_vars[name] = self.used_vars.copy()
                self.used_vars = old_used
                self.modified_vars = old_modified
                self.constants = old_constants
            elif typ == 'array_index':
                if len(node) >= 2 and isinstance(node[1], str):
                    self.used_vars.add(node[1])  
                if len(node) >= 3:
                    self._collect_usage(node[2])  
            elif typ == 'array_index_chain':
                self._collect_usage(node[1])  
                self._collect_usage(node[2])
            elif typ == 'return_statement':
                if len(node) > 1 and node[1]:
                    self._collect_usage(node[1])
            elif typ in ('var_declaration', 'const_declaration'):
                name = node[1]
                self.defined_vars.add(name)
                self._collect_usage(node[2])
            elif typ == 'assignment':
                name = node[1]
                self.modified_vars.add(name)
                self._collect_usage(node[2])
            else:
                for child in node[1:]:
                    self._collect_usage(child)
        elif isinstance(node, list):
            for item in node:
                self._collect_usage(item)

   
    def _optimize_node(self, node: Union[Tuple, List, Any]) -> Union[Tuple, List, Any, None]:
        if not node:
            return node
        if isinstance(node, tuple):
            typ = node[0]
            method = getattr(self, f'_opt_{typ}', None)
            if method:
                return method(node)
            new_children = []
            for child in node[1:]:
                opt_child = self._optimize_node(child)
                if opt_child is not None:
                    new_children.append(opt_child)
            if not new_children and len(node) > 1:
                return None
            return _make_node(typ, *new_children)
        elif isinstance(node, list):
            return self._optimize_list(node)
        return node

    def _optimize_list(self, stmts: List) -> Optional[List]:
        if not stmts:
            return None
        new_stmts = []
        terminated = False
        for stmt in stmts:
            if terminated:
                self.stats['unreachable_removed'] += 1
                continue
            opt_stmt = self._optimize_node(stmt)
            if opt_stmt is None:
                continue
            new_stmts.append(opt_stmt)
            if self._is_terminator(opt_stmt):
                terminated = True
        return new_stmts if new_stmts else None

    def _is_terminator(self, node: Any) -> bool:
        if isinstance(node, tuple):
            typ = node[0]
            if typ in ('return_statement', 'stop_statement'):
                return True
        return False


    def _const_expr(self, expr: Any) -> Any:
        if not isinstance(expr, tuple):
            return None
        typ = expr[0]
        if typ == 'literal':
            return expr[1]
        if typ == 'identifier':
            name = expr[1]
            if name in self.constants:
                return self.constants[name]
            return None
        if typ == 'unary_op':
            op = expr[1]
            val = self._const_expr(expr[2])
            if val is not None:
                if op == 'UMINUS':
                    return -val
                if op == '!':
                    return not val
            return None
        if typ == 'binary_op':
            op = expr[1]
            left = self._const_expr(expr[2])
            right = self._const_expr(expr[3])
            if left is not None and right is not None:
                try:
                    if op == '+':
                        return left + right
                    if op == '-':
                        return left - right
                    if op == '*':
                        return left * right
                    if op == '/':
                        return left / right if right != 0 else None
                    if op == '%':
                        return left % right if right != 0 else None
                    if op == '^':
                        return left ** right
                    if op == '>':
                        return left > right
                    if op == '<':
                        return left < right
                    if op == '>=':
                        return left >= right
                    if op == '<=':
                        return left <= right
                    if op == '==':
                        return left == right
                    if op == '!=':
                        return left != right
                    if op in ('AND', '&&'):
                        return left and right
                    if op in ('OR', '||'):
                        return left or right
                except Exception:
                    pass
            return None
        return None

    def _has_side_effect(self, node: Any) -> bool:
        if not node:
            return False
        if isinstance(node, tuple):
            typ = node[0]
            if typ in ('function_call', 'method_call', 'static_method_call',
                       'assignment', 'array_assignment', 'dict_assignment',
                       'require_statement'):
                return True
            for child in node[1:]:
                if self._has_side_effect(child):
                    return True
        elif isinstance(node, list):
            for item in node:
                if self._has_side_effect(item):
                    return True
        return False

    
    def _opt_start(self, node: Tuple) -> Optional[Tuple]:
        _, stmts = node[0], node[1]
        opt_stmts = self._optimize_node(stmts)
        if opt_stmts is None:
            return None
        return _make_node('start', opt_stmts)

    def _opt_statement_list(self, node: Tuple) -> Optional[Tuple]:
        _, stmts = node[0], node[1]
        opt_stmts = self._optimize_list(stmts)
        if opt_stmts is None:
            return None
        return _make_node('statement_list', opt_stmts)

    def _opt_var_declaration(self, node: Tuple) -> Optional[Tuple]:
        _, name, expr = node
        opt_expr = self._optimize_node(expr)
        is_used = name in self.used_vars
        if not is_used and self.in_function and hasattr(self, '_func_used_vars'):
            for func_name, func_vars in self._func_used_vars.items():
                if name in func_vars:
                    is_used = True
                    break
        
        if not is_used and not self._has_side_effect(opt_expr):
            self.stats['unused_vars_removed'] += 1
            return None
        
        const_val = self._const_expr(opt_expr)
        if const_val is not None and name not in self.modified_vars:
            self.constants[name] = const_val
        return _make_node('var_declaration', name, opt_expr)

    def _opt_const_declaration(self, node: Tuple) -> Optional[Tuple]:
        _, name, expr = node
        opt_expr = self._optimize_node(expr)
        const_val = self._const_expr(opt_expr)
        if const_val is not None:
            self.constants[name] = const_val
        if name not in self.used_vars:
            self.stats['unused_vars_removed'] += 1
            return None
        return _make_node('const_declaration', name, opt_expr)

    def _opt_assignment(self, node: Tuple) -> Optional[Tuple]:
        _, name, expr = node
        if name in self.constants:
            del self.constants[name]
        opt_expr = self._optimize_node(expr)
        if name not in self.used_vars and not self._has_side_effect(opt_expr):
            return None
        return _make_node('assignment', name, opt_expr)

    def _opt_array_assignment(self, node: Tuple) -> Optional[Tuple]:
        _, array_part, expr = node
        opt_array = self._optimize_node(array_part)
        opt_expr = self._optimize_node(expr)
        return _make_node('array_assignment', opt_array, opt_expr)

    def _opt_identifier(self, node: Tuple) -> Union[Tuple, Any]:
        _, name = node
        if name in self.constants:
            self.stats['const_folded'] += 1
            return _make_node('literal', self.constants[name])
        return node

    def _opt_binary_op(self, node: Tuple) -> Union[Tuple, Any]:
        _, op, left, right = node
        left_opt = self._optimize_node(left)
        right_opt = self._optimize_node(right)

        left_val = self._const_expr(left_opt)
        right_val = self._const_expr(right_opt)
        if left_val is not None and right_val is not None:
            self.stats['const_folded'] += 1
            try:
                if op == '+':
                    return _make_node('literal', left_val + right_val)
                if op == '-':
                    return _make_node('literal', left_val - right_val)
                if op == '*':
                    return _make_node('literal', left_val * right_val)
                if op == '/':
                    if right_val == 0:
                        return _make_node('binary_op', op, left_opt, right_opt)
                    return _make_node('literal', left_val / right_val)
                if op == '%':
                    if right_val == 0:
                        return _make_node('binary_op', op, left_opt, right_opt)
                    return _make_node('literal', left_val % right_val)
                if op == '^':
                    return _make_node('literal', left_val ** right_val)
                if op == '>':
                    return _make_node('literal', left_val > right_val)
                if op == '<':
                    return _make_node('literal', left_val < right_val)
                if op == '>=':
                    return _make_node('literal', left_val >= right_val)
                if op == '<=':
                    return _make_node('literal', left_val <= right_val)
                if op == '==':
                    return _make_node('literal', left_val == right_val)
                if op == '!=':
                    return _make_node('literal', left_val != right_val)
                if op in ('AND', '&&'):
                    return _make_node('literal', left_val and right_val)
                if op in ('OR', '||'):
                    return _make_node('literal', left_val or right_val)
            except Exception:
                pass
        if op == '+' and left_val == 0:
            return right_opt
        if op == '+' and right_val == 0:
            return left_opt
        if op == '*' and (left_val == 0 or right_val == 0):
            return _make_node('literal', 0)
        if op == '*' and left_val == 1:
            return right_opt
        if op == '*' and right_val == 1:
            return left_opt
        if op == '-' and right_val == 0:
            return left_opt
        if op == '/' and right_val == 1:
            return left_opt

        return _make_node('binary_op', op, left_opt, right_opt)

    def _opt_unary_op(self, node: Tuple) -> Union[Tuple, Any]:
        _, op, expr = node
        opt_expr = self._optimize_node(expr)
        val = self._const_expr(opt_expr)
        if val is not None:
            self.stats['const_folded'] += 1
            if op == 'UMINUS':
                return _make_node('literal', -val)
            if op == '!':
                return _make_node('literal', not val)
        return _make_node('unary_op', op, opt_expr)

    def _opt_if_statement(self, node: Tuple) -> Optional[Union[Tuple, Any]]:
        _, cond, if_block, eif_blocks, else_block = node
        opt_cond = self._optimize_node(cond)
        opt_if = self._optimize_node(if_block)
        opt_eifs = None
        if eif_blocks:
            filtered = []
            for eif in eif_blocks:
                if eif is None:
                    continue
                opt_eif = self._optimize_node(eif)
                if opt_eif is not None:
                    filtered.append(opt_eif)
            if filtered:
                opt_eifs = filtered
        
        opt_else = self._optimize_node(else_block) if else_block else None

        cond_val = self._const_expr(opt_cond)
        if cond_val is not None:
            self.stats['const_folded'] += 1
            if cond_val:
                self.stats['dead_branches_removed'] += 1
                return opt_if
            else:
                self.stats['dead_branches_removed'] += 1
                if opt_eifs:
                    first_eif = opt_eifs[0]
                    if isinstance(first_eif, tuple) and len(first_eif) >= 3:
                        new_cond = first_eif[1]
                        new_block = first_eif[2]
                        new_eifs = opt_eifs[1:] if len(opt_eifs) > 1 else None
                        return self._opt_if_statement(
                            _make_node('if_statement', new_cond, new_block, new_eifs, opt_else)
                        )
                return opt_else if opt_else else None
        return _make_node('if_statement', opt_cond, opt_if, opt_eifs, opt_else)

    def _opt_eif_clause(self, node: Tuple) -> Optional[Tuple]:
        _, cond, block = node
        opt_cond = self._optimize_node(cond)
        opt_block = self._optimize_node(block)
        if self._const_expr(opt_cond) is False:
            self.stats['dead_branches_removed'] += 1
            return None
        return _make_node('eif_clause', opt_cond, opt_block)

    def _opt_else_clause(self, node: Tuple) -> Optional[Tuple]:
        _, block = node
        opt_block = self._optimize_node(block)
        if opt_block is None or (isinstance(opt_block, list) and len(opt_block) == 0):
            return None
        return _make_node('else_clause', opt_block)

    def _opt_while_statement(self, node: Tuple) -> Optional[Tuple]:
        _, cond, body = node
        opt_cond = self._optimize_node(cond)
        opt_body = self._optimize_node(body)
        if self._const_expr(opt_cond) is False:
            self.stats['dead_branches_removed'] += 1
            return None
        return _make_node('while_statement', opt_cond, opt_body)

    def _opt_for_statement(self, node: Tuple) -> Optional[Tuple]:
        if len(node) >= 4:
            _, var, range_expr, body = node[:4]
        else:
            _, var, range_expr = node
            body = None
        opt_range = self._optimize_node(range_expr)
        opt_body = self._optimize_node(body) if body else None
        range_val = self._const_expr(opt_range)
        if isinstance(range_val, (int, float)) and range_val <= 0:
            self.stats['dead_branches_removed'] += 1
            return None
        if opt_body is None or (isinstance(opt_body, list) and len(opt_body) == 0):
            self.stats['dead_branches_removed'] += 1
            return None
        
        return _make_node('for_statement', var, opt_range, opt_body)

    def _opt_return_statement(self, node: Tuple) -> Tuple:
        _, expr = node
        opt_expr = self._optimize_node(expr) if expr else None
        return _make_node('return_statement', opt_expr)

    def _opt_stop_statement(self, node: Tuple) -> Tuple:
        return node

    def _opt_func_declaration(self, node: Tuple) -> Tuple:
        _, ret_type, name, params, body = node
        outer_constants = self.constants.copy()
        outer_modified = self.modified_vars.copy()
        outer_used = self.used_vars.copy()
        self.constants.clear()
        self.modified_vars.clear()
        self.used_vars.clear()
        old_in_func = self.in_function
        self.in_function = True
        self._collect_usage(body)
        opt_body = self._optimize_node(body)
        self.constants = outer_constants
        self.modified_vars = outer_modified
        self.used_vars = outer_used
        self.in_function = old_in_func
        if opt_body is None or (isinstance(opt_body, list) and len(opt_body) == 0):
            opt_body = None
        
        return _make_node('func_declaration', ret_type, name, params, opt_body)

    
    def _opt_require_statement(self, node: Tuple) -> Tuple:
        return node

    def _opt_function_call(self, node: Tuple) -> Tuple:
        _, name, args = node
        opt_args = self._optimize_node(args) if args else None
        return _make_node('function_call', name, opt_args)

    def _opt_function_call_statement(self, node: Tuple) -> Tuple:
        _, call_node = node
        opt_call = self._optimize_node(call_node)
        return _make_node('function_call_statement', opt_call)

   
    def _opt_method_call(self, node: Tuple) -> Tuple:
        return node

    def _opt_method_call_statement(self, node: Tuple) -> Tuple:
        return node

    def _opt_dict(self, node: Tuple) -> Tuple:
        return node

    def _opt_dict_declaration(self, node: Tuple) -> Tuple:
        return node

    def _opt_dict_index(self, node: Tuple) -> Tuple:
        return node

    def _opt_dict_assignment(self, node: Tuple) -> Tuple:
        return node

    def _opt_static_method_call(self, node: Tuple) -> Tuple:
        return node

    def _opt_static_method_call_statement(self, node: Tuple) -> Tuple:
        return node

    def _opt_static_access(self, node: Tuple) -> Tuple:
        return node

    def _opt_function_ref(self, node: Tuple) -> Tuple:
        return node

    def _opt_parenthesized(self, node: Tuple) -> Tuple:
        return node

    def _opt_annotation(self, node: Tuple) -> Tuple:
        return node

    def _opt_array(self, node: Tuple) -> Tuple:
        _, elements = node
        if elements:
            opt_elements = []
            for elem in elements:
                opt_elem = self._optimize_node(elem)
                if opt_elem is not None:
                    opt_elements.append(opt_elem)
            return _make_node('array', opt_elements)
        return node

    def _opt_array_declaration(self, node: Tuple) -> Tuple:
        return node

    def _opt_array_index(self, node: Tuple) -> Tuple:
        _, array_name, index_expr = node
        opt_index = self._optimize_node(index_expr)
        return _make_node('array_index', array_name, opt_index)
    
    def _opt_array_index_chain(self, node: Tuple) -> Tuple:
        _, base, index_expr = node
        opt_base = self._optimize_node(base)
        opt_index = self._optimize_node(index_expr)
        return _make_node('array_index_chain', opt_base, opt_index)
    def _cleanup(self, node: Any) -> Any:
        if not node:
            return node
        if isinstance(node, tuple):
            typ = node[0]
            if typ == 'if_statement' and len(node) >= 3:
                if_block = node[2]
                eif_blocks = node[3] if len(node) > 3 else None
                else_block = node[4] if len(node) > 4 else None
                is_empty_if = (if_block is None or (isinstance(if_block, list) and not if_block))
                is_empty_eif = (eif_blocks is None or (isinstance(eif_blocks, list) and not eif_blocks))
                if is_empty_if and is_empty_eif and else_block is None:
                    return None
                return _make_node('if_statement', node[1], if_block, eif_blocks, else_block)
            
            new_children = []
            for child in node[1:]:
                cleaned = self._cleanup(child)
                if cleaned is not None:
                    new_children.append(cleaned)
                elif typ == 'if_statement' and len(node) > 3 and child is None:
                    new_children.append(None)
            
            if not new_children and len(node) > 1:
                if typ == 'if_statement' and len(node) == 5:
                    return _make_node('if_statement', node[1], node[2], node[3], node[4])
                return None
            return _make_node(typ, *new_children)
        elif isinstance(node, list):
            new_list = []
            for item in node:
                cleaned = self._cleanup(item)
                if cleaned is not None:
                    new_list.append(cleaned)
            return new_list if new_list else None
        return node


def optimize_ast(ast: Tuple, verbose: bool = False) -> Tuple:
    opt = DeadCodeOptimizer()
    result = opt.optimize(ast)
    if verbose:
        opt.print_stats()
    return result