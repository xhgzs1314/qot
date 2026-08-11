import sys
import traceback
import ply.yacc as yacc
from token_char import Lexer, TokenType, Token
from builtin_func import BuiltFunc
from error_system import (
    get_error_handler, ErrorFactory, ErrorCode,
    QOTError, ErrorLevel
)
tokens = (
    'IDENTIFIER', 'NUMBER', 'STRING',
    'VAR', 'CONST',
    'PLUS_EQUAL', 'MINUS_EQUAL', 'MULTIPLY_EQUAL', 'DIVIDE_EQUAL', 'MOD_EQUAL', 
    'EQUAL', 'PLUS', 'MINUS', 'MULTIPLY', 'DIVIDE', 'MOD','NOTEQUAL',
    'LPAREN', 'RPAREN', 'COMMA', 'SEMICOLON',
    'LBRACKET', 'RBRACKET','COMMENT','LBRACE', 'RBRACE','IF', 'EIF', 'ELSE',
    'GREATER','LESS','GREATEREQ','LESSEQ','EQUALS','LOOP','STOP','FUNC', 'RETURN',
    "FOR","IN",'NUMBER_T', 'STRING_T', 'BOOLEAN_T', 'ARRAY_T', 'ANY_T','ARROW','BOOLEAN',"REQUIRE",
    'DICT_START',"ANNOTATION",
    'DICT_END',
    'COLON',
    "FUNCTION_REF",
    'AND', 'OR',
    'NULL','POW',
    'DCOLON'
)

precedence = (
    ('right', 'POW'),  
    ('right', 'ARROW'),
    ('left', 'DCOLON'), 
    ('right', 'UMINUS'),  
    ('left', 'AND', 'OR'),
    ('left', 'PLUS', 'MINUS'),
    ('left', 'MULTIPLY', 'DIVIDE', 'MOD'),
    ('left', 'GREATER', 'LESS', 'GREATEREQ', 'LESSEQ', 'EQUALS', 'NOTEQUAL'),
)

def get_line_number(p, index=1):
    if hasattr(p, 'lineno') and p.lineno(index):
        return p.lineno(index)
    return 0

def p_start(p):
    '''start : statement_list'''
    p[0] = ('start', p[1], get_line_number(p))

def p_statement_annotation(p):
    '''statement : ANNOTATION'''
    p[0] = ('annotation', p[1], get_line_number(p))

def p_statement_list(p):
    '''statement_list : statement
                      | statement_list statement'''
    if len(p) == 2:
        p[0] = [p[1]]
    else:
        p[1].append(p[2])
        p[0] = p[1]

def p_statement_require(p):
    '''statement : REQUIRE STRING SEMICOLON'''
    p[0] = ('require_statement', p[2], get_line_number(p))

#----------
def p_expression_dict(p):
    '''expression : DICT_START dict_entry_list_opt DICT_END'''
    entries = p[2] if p[2] is not None else []
    p[0] = ('dict', entries, get_line_number(p))

def p_dict_entry_list_opt(p):
    '''dict_entry_list_opt : dict_entry_list
                           | empty'''
    if len(p) == 2 and p[1] is not None:
        p[0] = p[1]
    else:
        p[0] = []

def p_dict_entry_list(p):
    '''dict_entry_list : dict_entry
                       | dict_entry_list COMMA dict_entry'''
    if len(p) == 2:
        p[0] = [p[1]]
    else:
        p[1].append(p[3])
        p[0] = p[1]

def p_dict_entry(p):
    '''dict_entry : expression COLON expression'''
    p[0] = (p[1], p[3], get_line_number(p))

def p_statement_dict_declaration(p):
    '''statement : IDENTIFIER EQUAL DICT_START dict_entry_list_opt DICT_END SEMICOLON'''
    entries = p[4] if p[4] is not None else []
    p[0] = ('dict_declaration', p[1], entries, get_line_number(p))

#------------------
def p_statement_var_declaration(p):
    '''statement : VAR IDENTIFIER SEMICOLON
                 | VAR IDENTIFIER EQUAL expression SEMICOLON'''
    if len(p) == 4:  
        p[0] = ('var_declaration', p[2], ('literal', None, get_line_number(p)), get_line_number(p))
    else:  
        p[0] = ('var_declaration', p[2], p[4], get_line_number(p))

def p_statement_const_declaration(p):
    '''statement : CONST IDENTIFIER EQUAL expression SEMICOLON'''
    p[0] = ('const_declaration', p[2], p[4], get_line_number(p))

def p_relational_expression(p):
    '''relational_expression : additive_expression
                             | relational_expression GREATER additive_expression
                             | relational_expression LESS additive_expression
                             | relational_expression GREATEREQ additive_expression
                             | relational_expression EQUALS additive_expression
                             | relational_expression LESSEQ additive_expression
                             | relational_expression NOTEQUAL additive_expression'''
    if len(p) == 2:
        p[0] = p[1]
    else:
        p[0] = ('binary_op', p[2], p[1], p[3], get_line_number(p))

def p_logical_expression(p):
    '''logical_expression : relational_expression
                          | logical_expression AND relational_expression
                          | logical_expression OR relational_expression'''
    if len(p) == 2:
        p[0] = p[1]
    else:
        p[0] = ('binary_op', p[2], p[1], p[3], get_line_number(p))

def p_type_specifier(p):
    '''type_specifier : NUMBER_T
                      | STRING_T
                      | BOOLEAN_T
                      | ARRAY_T
                      | ANY_T'''
    p[0] = p[1]

def p_array_index(p):
    '''array_index : IDENTIFIER LBRACKET expression RBRACKET'''
    p[0] = ('array_index', p[1], p[3], get_line_number(p))

def p_array_index_chain(p):
    '''array_index_chain : array_index LBRACKET expression RBRACKET
                         | array_index_chain LBRACKET expression RBRACKET'''
    if len(p) == 5 and p[1][0] == 'array_index':
        p[0] = ('array_index_chain', p[1], p[3], get_line_number(p))
    else:
        p[0] = ('array_index_chain', p[1], p[3], get_line_number(p))

def p_statement_assignment(p):
    '''statement : IDENTIFIER EQUAL expression SEMICOLON
                 | array_index EQUAL expression SEMICOLON
                 | array_index_chain EQUAL expression SEMICOLON
                 | IDENTIFIER PLUS_EQUAL expression SEMICOLON
                 | IDENTIFIER MINUS_EQUAL expression SEMICOLON
                 | IDENTIFIER MULTIPLY_EQUAL expression SEMICOLON
                 | IDENTIFIER DIVIDE_EQUAL expression SEMICOLON
                 | IDENTIFIER MOD_EQUAL expression SEMICOLON'''
    if isinstance(p[1], tuple) and p[1][0] in ('array_index', 'array_index_chain'):
        p[0] = ('array_assignment', p[1], p[3], get_line_number(p))
    elif p[2] == '=':
        p[0] = ('assignment', p[1], p[3], get_line_number(p))
    else:
        op_map = {
            '+=': '+',
            '-=': '-',
            '*=': '*',
            '/=': '/',
            '%=': '%'
        }
        op = op_map[p[2]]
        left = ('identifier', p[1], get_line_number(p))
        right = p[3]
        binary = ('binary_op', op, left, right, get_line_number(p))
        p[0] = ('assignment', p[1], binary, get_line_number(p))

def p_statement_function_call(p):
    '''statement : function_call SEMICOLON'''
    p[0] = ('function_call_statement', p[1], get_line_number(p))

def p_comment(p):
    '''statement : COMMENT'''
    p[0] = None

def p_statement_array_declaration(p):
    '''statement : IDENTIFIER EQUAL LBRACKET expression_list_opt RBRACKET SEMICOLON'''
    p[0] = ('array_declaration', p[1], p[4], get_line_number(p))

def p_function_call(p):
    '''function_call : IDENTIFIER LPAREN expression_list_opt RPAREN'''
    p[0] = ('function_call', p[1], p[3], get_line_number(p))

def p_expression_list_opt(p):
    '''expression_list_opt : expression_list
                           | empty'''
    p[0] = p[1]

def p_primary_expression_function_ref(p):
    '''primary_expression : FUNCTION_REF function_call'''
    p[0] = ('function_ref', p[2][1], p[2][2], get_line_number(p))
def p_expression(p):
    '''expression : logical_expression
                  | static_method_call'''
    p[0] = p[1]

def p_expression_list(p):
    '''expression_list : expression
                       | expression_list COMMA expression'''
    if len(p) == 2:
        p[0] = [p[1]]
    else:
        p[1].append(p[3])
        p[0] = p[1]

def p_statement_for(p):
    '''statement : FOR LPAREN IDENTIFIER IN expression RPAREN LBRACE statement_list RBRACE'''
    p[0] = ('for_statement', p[3], p[5], p[8], get_line_number(p))

def p_statement_return(p):
    '''statement : RETURN expression SEMICOLON

                 | RETURN SEMICOLON'''
    if len(p) == 4:
        p[0] = ('return_statement', p[2], get_line_number(p))
    else:
        p[0] = ('return_statement', None, get_line_number(p))

def p_statement_func_declaration(p):
    '''statement : type_specifier FUNC IDENTIFIER LPAREN parameter_list_opt RPAREN LBRACE statement_list RBRACE'''
    p[0] = ('func_declaration', p[1], p[3], p[5], p[8], get_line_number(p))

def p_parameter_list_opt(p):
    '''parameter_list_opt : parameter_list
                          | empty'''
    p[0] = p[1] if p[1] is not None else []

def p_parameter_list(p):
    '''parameter_list : IDENTIFIER
                      | parameter_list COMMA IDENTIFIER'''
    if len(p) == 2:
        p[0] = [p[1]]
    else:
        p[1].append(p[3])
        p[0] = p[1]

def p_expression_additive(p):
    '''expression : additive_expression'''
    p[0] = p[1]

def p_additive_expression(p):
    '''additive_expression : multiplicative_expression
                           | additive_expression PLUS multiplicative_expression
                           | additive_expression MINUS multiplicative_expression'''
    if len(p) == 2:
        p[0] = p[1]
    else:
        p[0] = ('binary_op', p[2], p[1], p[3], get_line_number(p))

def p_power_expression(p):
    '''power_expression : primary_expression
                        | primary_expression POW power_expression'''
    if len(p) == 2:
        p[0] = p[1]
    else:
        p[0] = ('binary_op', '^', p[1], p[3], get_line_number(p))

def p_multiplicative_expression(p):
    '''multiplicative_expression : power_expression
                                 | multiplicative_expression MULTIPLY power_expression
                                 | multiplicative_expression DIVIDE power_expression
                                 | multiplicative_expression MOD power_expression'''
    if len(p) == 2:
        p[0] = p[1]
    else:
        p[0] = ('binary_op', p[2], p[1], p[3], get_line_number(p))

def p_statement_method_call(p):
    '''statement : primary_expression ARROW IDENTIFIER LPAREN expression_list_opt RPAREN SEMICOLON'''
    p[0] = ('method_call_statement', ('method_call', p[1], p[3], p[5], get_line_number(p)), get_line_number(p))

def p_primary_expression_method_call(p):
    '''primary_expression : primary_expression ARROW IDENTIFIER LPAREN expression_list_opt RPAREN'''
    p[0] = ('method_call', p[1], p[3], p[5], get_line_number(p))

def p_primary_expression(p):
    '''primary_expression : NUMBER
                          | STRING
                          | BOOLEAN
                          | NULL 
                          | IDENTIFIER
                          | function_call
                          | LPAREN expression RPAREN
                          | LBRACKET expression_list_opt RBRACKET
                          | MINUS primary_expression %prec UMINUS
                          | IDENTIFIER LBRACKET expression RBRACKET
                          | array_index
                          | array_index_chain'''
    if len(p) == 2:
        if p.slice[1].type == 'IDENTIFIER':
            p[0] = ('identifier', p[1], get_line_number(p))  
        elif p.slice[1].type == 'BOOLEAN':  
            p[0] = ('literal', True if p[1] == 'true' else False, get_line_number(p))
        elif p.slice[1].type == 'NULL':
            p[0] = ('literal', None, get_line_number(p))
        elif p.slice[1].type in ('NUMBER', 'STRING'):
            p[0] = ('literal', p[1], get_line_number(p))
        else:
            p[0] = p[1]
    elif p[1] == '(':
        p[0] = ('parenthesized', p[2], get_line_number(p))
    elif p[1] == '[':
        p[0] = ('array', p[2], get_line_number(p))
    elif p[1] == '-':  
        p[0] = ('unary_op', 'UMINUS', p[2], get_line_number(p))
    elif len(p) == 5 and p[2] == '[' and isinstance(p[1], str):  
        p[0] = ('array_index', p[1], p[3], get_line_number(p))
    elif len(p) == 5 and p[2] == '[' and isinstance(p[1], tuple):  
        p[0] = ('array_index_chain', p[1], p[3], get_line_number(p))
    elif len(p) == 6 and p[2] == '->':  
        p[0] = ('method_call', p[1], p[3], p[5], get_line_number(p))
    else:
        p[0] = p[1]
def p_static_method_call(p):
    '''static_method_call : IDENTIFIER DCOLON IDENTIFIER LPAREN expression_list_opt RPAREN'''
    p[0] = ('static_method_call', ('identifier', p[1], get_line_number(p)), p[3], p[5], get_line_number(p))

def p_statement_static_method_call(p):
    '''statement : static_method_call SEMICOLON'''
    p[0] = ('static_method_call_statement', p[1], get_line_number(p))

def p_empty(p):
    '''empty :'''
    p[0] = None

def p_statement_if(p):
    '''statement : IF LPAREN expression RPAREN LBRACE statement_list RBRACE eif_clause_opt else_clause_opt'''
    p[0] = ('if_statement', p[3], p[6], p[8], p[9], get_line_number(p))

def p_eif_clause_opt(p):
    '''eif_clause_opt : eif_clause
                      | empty'''
    p[0] = p[1]

def p_eif_clause_chain(p):
    '''eif_clause_chain : EIF LPAREN expression RPAREN LBRACE statement_list RBRACE
                        | eif_clause_chain EIF LPAREN expression RPAREN LBRACE statement_list RBRACE'''
    if len(p) == 8:
        p[0] = [('eif_clause', p[3], p[6], get_line_number(p, 2))]
    else:
        p[1].append(('eif_clause', p[4], p[7], get_line_number(p, 2)))
        p[0] = p[1]

def p_eif_clause(p):
    '''eif_clause : eif_clause_chain'''  
    p[0] = p[1]

def p_else_clause_opt(p):
    '''else_clause_opt : ELSE LBRACE statement_list RBRACE
                       | empty'''
    if len(p) == 5:
        p[0] = ('else_clause', p[3], get_line_number(p))
    else:
        p[0] = None

def p_statement_while(p):
    '''statement : LOOP LPAREN expression RPAREN LBRACE statement_list RBRACE'''
    p[0] = ('while_statement', p[3], p[6], get_line_number(p))

def p_statement_stop(p):
    '''statement : stop_statement'''  
    p[0] = p[1]

def p_stop_statement(p):
    '''stop_statement : STOP SEMICOLON'''
    p[0] = ('stop_statement', get_line_number(p))

def p_error(p):
    handler = get_error_handler()
    if p:
        if p.value == 'func':
            err = ErrorFactory.syntax_error(
                "定义函数时必须指定返回数据类型",
                file=handler._current_source_file,
                line=p.lineno,
                col=p.lexpos
            )
        else:
            msg = f"遇到意外的符号 '{p.value}' (类型: {p.type})"
            if p.type == 'SEMICOLON':
                msg += "，可能是多余的分号"
            elif p.value in (':', '{', '}'):
                msg += "，可能是括号或冒号不匹配"
            else:
                msg += "，请检查语法或是否遗漏分号"
            
            err = ErrorFactory.syntax_error(
                msg,
                file=handler._current_source_file,
                line=p.lineno,
                col=p.lexpos
            )
        
        err.level = ErrorLevel.ERROR
        handler.handle(err)
    else:
        err = ErrorFactory.syntax_error(
            "意外的代码结束，可能存在未闭合的括号或块",
            file=handler._current_source_file
        )
        handler.handle(err)

class QotPlyParser:
    def __init__(self):
        self.lexer = Lexer()
        self.parser = yacc.yacc(debug=False, write_tables=False,outputdir=None)
        self.errors = []
        
    def parse(self, filename):
        handler = get_error_handler()
        handler.set_source_file(filename)
        try:
            with open(filename, 'r', encoding='utf-8') as f:
                data = f.read()
        except FileNotFoundError:
            err = ErrorFactory.file_not_found(filename)
            handler.handle(err)
            raise
        except PermissionError:
            err = QOTError(
                code=ErrorCode.ERR_PERMISSION_DENIED,
                message=f"没有权限读取文件: {filename}",
                file_name=filename
            )
            handler.handle(err)
            raise
        except UnicodeDecodeError:
            err = QOTError(
                code=ErrorCode.ERR_IO_GENERAL,
                message=f"文件编码错误，无法以UTF-8编码读取: {filename}",
                file_name=filename
            )
            handler.handle(err)
            raise
        
        self.lexer.input(data, filename)
        
        def get_next_token():
            try:
                token = self.lexer.token()
                if token.type == TokenType.EOF_TOKEN:
                    return None
                return self._convert_to_ply_token(token)
            except Exception as e:
                err = QOTError(
                    code=ErrorCode.ERR_RUNTIME_GENERAL,
                    message=f"词法分析错误: {str(e)}"
                )
                handler.handle(err)
                raise
        
        try:
            return self.parser.parse(input=data, lexer=None, tokenfunc=get_next_token)
        except SyntaxError:
            raise
        except Exception as e:
            err = QOTError(
                code=ErrorCode.ERR_COMPILE_GENERAL,
                message=f"编译错误: {str(e)}"
            )
            handler.handle(err)
            raise

        
    def _convert_to_ply_token(self, token):
        if token.type == TokenType.KEYWORD:
            token_type = token.value.upper()
            if token.value == 'require':
                token_type = 'REQUIRE'
        elif token.type == TokenType.OPERATOR:
            op_map = {
                '=': 'EQUAL',
                '+': 'PLUS',
                '-': 'MINUS',
                '*': 'MULTIPLY',
                '/': 'DIVIDE',
                '%': 'MOD',
                '+=': 'PLUS_EQUAL',      
                '-=': 'MINUS_EQUAL',     
                '*=': 'MULTIPLY_EQUAL',  
                '/=': 'DIVIDE_EQUAL',    
                '%=': 'MOD_EQUAL',  
                '>': 'GREATER',      
                '<': 'LESS',          
                '>=': 'GREATEREQ',    
                '<=': 'LESSEQ', 
                '==': 'EQUALS',
                '->': 'ARROW',
                '!=': 'NOTEQUAL', 
                '&&': 'AND',  
                '||': 'OR',
                '^': 'POW', 
                '##': 'DCOLON',  
            }
            token_type = op_map.get(token.value, token.value)
        elif token.type == TokenType.PUNCTUATOR:
            punc_map = {
                ';': 'SEMICOLON',
                ',': 'COMMA',
                '(': 'LPAREN',
                ')': 'RPAREN',
                '[': 'LBRACKET',
                ']': 'RBRACKET',
                '{': 'LBRACE',
                '}': 'RBRACE'
            }
            token_type = punc_map.get(token.value, token.value)
        elif token.type == TokenType.DICT_START:
            token_type = "DICT_START"
        elif token.type == TokenType.DICT_END:
            token_type = "DICT_END"
        elif token.type == TokenType.COLON:
            token_type = "COLON"
        else:
            token_type = token.type
        class PlyToken:
            def __init__(self, type_, value, lineno, lexpos):
                self.type = type_
                self.value = value
                self.lineno = lineno
                self.lexpos = lexpos
                
        return PlyToken(token_type, token.value, token.line, token.column)

class SemanticAnalyzer:
    def __init__(self):
        self.variables = {} 
        self.errors = []     
        self.builtin_func = BuiltFunc()
        self._init_builtin_functions_in_scope()
        self.node_counter = 0 
        self.requireif = False
        self.waring_error_bit = True
    
    def _init_builtin_functions_in_scope(self):
        for func_name in self.builtin_func.functions.keys():
            self.variables[func_name] = ('builtin_function', True, 'function')
    
    def get_line(self, node):
        if isinstance(node, tuple) and len(node) > 0:
            for item in reversed(node):
                if isinstance(item, (int, float)) and not isinstance(item, bool):
                    return int(item)
        return 0
    
    def error(self, message, node=None):
        if self.waring_error_bit:
            line = self.get_line(node) if node else None
            if line:
                self.errors.append(f"语义错误: 行 {line} - {message}")
            else:
                self.errors.append(f"语义错误: {message}")

    def check_type(self, value):
        if isinstance(value, bool):  
            return 'boolean'
        if isinstance(value, (int, float)):
            return 'number'
        elif isinstance(value, str):
            if value.lstrip('-').isdigit() or self.is_float(value):
                return 'number'
            return 'string'
        elif value is None:
            return 'null'
        elif isinstance(value, list):
            return 'array'
        elif isinstance(value, dict):
            return 'dict'
        else:
            return 'unknown'

    def is_float(self, s):
        try:
            float(s)
            return True
        except ValueError:
            return False

    def analyze(self, ast):
        if not ast or ast == 1:  
            return False
        self.visit(ast)
        return len(self.errors) == 0
    
    def visit_dict(self, node):
        _, entries, line = node if len(node) == 3 else (None, node[1], 0)
        dict_type = 'dict'
        for key_value in entries:
            if isinstance(key_value, tuple) and len(key_value) == 3:
                key, value, _ = key_value
                self.visit(key)
                self.visit(value)
        return dict_type
    
    def visit_dict_index(self, node):
        _, dict_name, key_expr, line = node if len(node) == 4 else (None, node[1], node[2], 0)
        dict_type = self.visit(('identifier', dict_name))
        if dict_type != 'dict' and dict_type != 'any':
            self.error(f"变量 '{dict_name}' 不是字典，无法使用键访问", node)
        key_type = self.visit(key_expr)
        if key_type != 'string' and key_type != 'any':
            self.error(f"字典键必须是字符串类型，实际是 {key_type}", node)
        return 'any'  

    def visit_dict_assignment(self, node):
        _, dict_part, value_expr, line = node if len(node) == 4 else (None, node[1], node[2], 0)
        if dict_part[0] == 'dict_index':
            _, dict_name, key_expr, _ = dict_part
            dict_type = self.visit(('identifier', dict_name))
            if dict_type != 'dict' and dict_type != 'any':
                self.error(f"变量 '{dict_name}' 不是字典，无法赋值", node)
            key_type = self.visit(key_expr)
            if key_type != 'string' and key_type != 'any':
                self.error(f"字典键必须是字符串类型，实际是 {key_type}", node)
            self.visit(value_expr)
            return 'any'
        else:
            self.error(f"无效的字典赋值语法", node)
            return 'any'
    
    def visit_dict_declaration(self, node):
        _, var_name, entries, line = node if len(node) == 4 else (None, node[1], node[2], 0)
        self.visit_var_declaration(('var_declaration', var_name, ('dict', entries, line), line))
        return 'dict'
    
    def visit(self, node):
        if not node:
            return None
        self.node_counter = (self.node_counter + 1)
        node_type = node[0] if isinstance(node, tuple) else None
        
        if node_type == 'start':
            return self.visit_statement_list(node[1])
        elif node_type == 'var_declaration':
            return self.visit_var_declaration(node)
        elif node_type == 'const_declaration':
            return self.visit_const_declaration(node)
        elif node_type == 'assignment':
            return self.visit_assignment(node)
        elif node_type == 'binary_op':
            return self.visit_binary_op(node)
        elif node_type == 'literal':
            return self.check_type(node[1])
        elif node_type == 'identifier':
            return self.visit_identifier(node)
        elif node_type == 'parenthesized':
            return self.visit(node[1])
        elif node_type == 'array':
            return self.visit_array(node)
        elif node_type == 'function_call':
            return self.visit_function_call(node)
        elif node_type == 'function_call_statement':
            return self.visit_function_call(node[1])
        elif node_type == 'array_declaration':
            return self.visit_array_declaration(node)
        elif node_type == 'unary_op':
            return self.visit_unary_op(node)
        elif node_type == 'if_statement':
            return self.visit_if_statement(node)
        elif node_type == 'eif_clause':
            return self.visit_eif_clause(node)
        elif node_type == 'else_clause':
            return self.visit_else_clause(node)
        elif node_type == 'array_index':
            return self.visit_array_index(node)
        elif node_type == 'while_statement':
            return self.visit_while_statement(node)
        elif node_type == 'func_declaration':
            return self.visit_func_declaration(node)
        elif node_type == 'for_statement':
            return self.visit_for_statement(node)
        elif node_type == 'method_call':
            return self.visit_method_call(node)
        elif node_type == 'require_statement':
            return self.visit_require_statement(node)
        elif node_type == 'dict_index':
            return self.visit_dict_index(node)
        elif node_type == 'dict_assignment':
            return self.visit_dict_assignment(node)
        elif node_type == 'dict':
            return self.visit_dict(node)
        elif node_type == 'dict_declaration':
            return self.visit_dict_declaration(node)
        elif node_type == 'function_ref':
            return self.visit_function_ref(node)
        elif node_type == 'annotation':  
            return self.visit_annotation(node)
        elif node_type == 'static_access':
            return self.visit_static_access(node)
        elif node_type == 'static_method_call':
            return self.visit_static_method_call(node)
        elif isinstance(node, list):
            return self.visit_statement_list(node)
        return None

    def visit_static_access(self, node):
        _, left_expr, member_name, line = node if len(node) == 4 else (None, node[1], node[2], 0)
        left_type = self.visit(left_expr)
        if left_type not in ('dict', 'module', 'any'):
            self.error(f"左值不是模块或字典，无法使用 '##' 访问成员", node)
        return 'any'

    def visit_static_method_call(self, node):
        _, left_expr, method_name, args, line = node
        left_type = self.visit(left_expr)
        if left_type not in ('dict', 'module', 'any'):
            self.error(f"左值不是模块或字典，无法调用成员 '{method_name}'", node)
        if args:
            for arg in args:
                self.visit(arg)
        return 'any'

    def visit_annotation(self, node):
        _, content, line = node if len(node) == 3 else (None, node[1], 0)
        if(content == 'rule::nowarning'):
            self.waring_error_bit = False
        return 'annotation'  
    
    def visit_function_ref(self, node):
        _, func_name, args, line = node if len(node) == 4 else (None, node[1], node[2], 0)
        if func_name not in self.variables:
            self.error(f"函数引用 '{func_name}' 未定义", node)
            return 'any'
        return 'function_ref'  
    
    def visit_method_call(self, node):
        _, caller_node, method_name, args, line = node if len(node) == 5 else (None, node[1], node[2], node[3], 0)
        caller_type = self.visit(caller_node) 
        if(args != None):
            for arg in args:
                arg_type = self.visit(arg)
        return 'any' 
    
    def visit_for_statement(self, node):
        _, var_name, range_expr, body, line = node if len(node) == 5 else (None, node[1], node[2], node[3], 0)
        if var_name in self.variables:
            self.error(f"循环变量 '{var_name}' 已定义", node)
        range_type = self.visit(range_expr)
        if range_type not in ('number', 'array','any'):
            self.error(f"for循环的range必须是数字或数组，实际是 {range_type}", node)
        current_vars = self.variables.copy()
        self.variables[var_name] = ('loop_var', False, 'any')  
        self.visit(body)
        self.variables = current_vars
    
    def visit_func_declaration(self, node):
        _, return_type, func_name, params, body, line = node if len(node) == 6 else (None, node[1], node[2], node[3], node[4], 0)
        if func_name in self.variables:
            self.error(f"函数 '{func_name}' 已重复定义", node)
        if return_type.endswith("_t"):
            return_type = return_type[:-2]
        self.variables[func_name] = (
            'function', False, 
            {'params': params, 'return_type': return_type}
        )
        current_vars = self.variables.copy()
        for param in params:
            self.variables[param] = ('param', False, 'any')
        self.visit(body)
        self.variables = current_vars
        return 'function'
    
    def visit_while_statement(self, node):
        _, condition, body, line = node if len(node) == 4 else (None, node[1], node[2], 0)
        cond_type = self.visit(condition)
        if cond_type != 'number' and not (isinstance(cond_type, str) and cond_type == 'boolean'):
            self.error(f"while条件表达式必须是布尔类型，实际是 {cond_type}", node)
        self.visit(body)
        return None
    
    def visit_array_index(self, node):
        _, var_name, index_expr, line = node if len(node) == 4 else (None, node[1], node[2], 0)
        var_type = self.visit(('identifier', var_name))
        if var_type == 'dict':
            index_type = self.visit(index_expr)
            if index_type != 'string' and index_type != 'any':
                self.error(f"字典键必须是字符串类型，实际是 {index_type}", node)
            return 'any'
        elif var_type == 'array':
            index_type = self.visit(index_expr)
            if index_type != 'number' and index_type != 'any':
                self.error(f"数组索引必须是数字类型，实际是 {index_type}", node)
            return 'array_known'
        elif var_type == 'any':
            return 'any'
        elif var_type == 'string':
            return 'string'
        else:
            return 'unknown'
    
    def visit_if_statement(self, node):
        _, condition, if_block, eif_blocks, else_block, line = node if len(node) == 6 else (None, node[1], node[2], node[3], node[4], 0)
        cond_type = self.visit(condition)
        if cond_type != 'number' and not (isinstance(cond_type, str)):
            self.error(f"if条件表达式必须是布尔类型，实际是 {cond_type}", node)
        self.visit(if_block)
        if eif_blocks:
            for eif in eif_blocks:
                self.visit(eif)
        if else_block:
            self.visit(else_block)
        return None

    def visit_eif_clause(self, node):
        _, condition, block, line = node if len(node) == 4 else (None, node[1], node[2], 0)
        cond_type = self.visit(condition)
        if cond_type != 'number' and not (isinstance(cond_type, str) and cond_type == 'boolean'):
            self.error(f"eif条件表达式必须是布尔类型，实际是 {cond_type}", node)
        self.visit(block)
        return None

    def visit_else_clause(self, node):
        _, block, line = node if len(node) == 3 else (None, node[1], 0)
        self.visit(block)
        return None
    
    def numbercangetmaybe(self, stris):
        try:
            int_z = int(stris)
            if(type(int_z) == int):
                return True
            else:
                return False
        except Exception as e:
            return False
    
    def visit_unary_op(self, node):
        _, op, expr, line = node if len(node) == 4 else (None, node[1], node[2], 0)
        if op == 'UMINUS':
            expr_type = self.visit(expr)
            if expr_type != 'number' and not self.numbercangetmaybe(expr[1]):
                self.error(f"一元负号不能用于 {expr_type} 类型", node)
            return 'number'  
        else:
            self.error(f"未知一元运算符: {op}", node)
            return 'unknown'
    
    def visit_statement_list(self, statements):
        for stmt in statements:
            if stmt:  
                self.visit(stmt)
    
    def visit_require_statement(self, node):
        _, lib_name, line = node if len(node) == 3 else (None, node[1], 0)
        self.requireif = True
        return 'module'
    
    def visit_var_declaration(self, node):
        _, var_name, expr, line = node if len(node) == 4 else (None, node[1], node[2], 0)
        var_type = self.visit(expr)
        if var_name in self.variables:
            existing = self.variables[var_name]
            self.variables[var_name] = ('var', False, var_type)
            return var_type
        self.variables[var_name] = ('var', False, var_type)
        return var_type

    def visit_const_declaration(self, node):
        _, var_name, expr, line = node if len(node) == 4 else (None, node[1], node[2], 0)
        var_type = self.visit(expr)
        if var_name in self.variables:
            self.error(f"常量 '{var_name}' 已重复定义", node)  
            return var_type
        self.variables[var_name] = ('const', True, var_type)
        return var_type

    def visit_assignment(self, node):
        if len(node) == 4 and node[0] == 'assignment':
            _, var_name, expr, line = node
            expr_type = self.visit(expr)
            if var_name not in self.variables:
                self.error(f"变量 '{var_name}' 未定义", node)
                return expr_type
            var_info = self.variables[var_name]
            if var_info[1]:  
                self.error(f"不能修改常量 '{var_name}' 的值", node)
            self.variables[var_name] = (var_info[0], var_info[1], expr_type)
            return expr_type
        return None

    def visit_identifier(self, node):
        _, var_name, line = node if len(node) == 3 else (None, node[1], 0)
        if var_name not in self.variables:
            self.error(f"变量 '{var_name}' 未定义，请检查是否拼写错误或未声明", node)  
            return 'unknown'
        return self.variables[var_name][2]

    def visit_binary_op(self, node):
        _, op, left, right, line = node if len(node) == 5 else (None, node[1], node[2], node[3], 0)
        left_type = self.visit(left)
        right_type = self.visit(right)
        if op in ('>', '<', '>=', '<=','==','!='):
            if left_type != 'number' and not self.numbercangetmaybe(left[1]):
                if(left_type != 'any'):
                    if(left_type not in ('boolean', 'string','null')):
                        self.error(f"比较运算符 '{op}' 左边的操作数必须是数字类型，实际是 {left_type}", node)
            if right_type != 'number' and not self.numbercangetmaybe(right[1]):
                if(right_type != 'any'):
                    if(right_type != 'boolean' and right_type != 'string' and right_type != 'null'):
                        self.error(f"比较运算符 '{op}' 右边的操作数必须是数字类型，实际是 {right_type}", node)
            return 'boolean'
        if op in ('+', '-', '*', '/', '%'):
            if left_type != 'number' and (not self.numbercangetmaybe(left[1])):
                if(left_type != 'array_known' and left_type != 'string'):
                    if(left_type != 'any' and left_type != None):
                        self.error(f"运算符 '{op}' 左边的操作数必须是数字类型，实际是 {left_type}", node)
            if right_type != 'number' and (not self.numbercangetmaybe(right[1])):
                if(right_type != 'array_known' and right_type != 'string'):
                    if(right_type != 'any'):
                        if(right_type != None):
                            self.error(f"运算符 '{op}' 右边的操作数必须是数字类型，实际是 {right_type}", node)
            if(left_type == 'string' and right_type != 'string'):
                self.error(f"字符串和数字不能进行加法运算", node)
            return 'number'  
        return left_type

    def visit_array(self, node):
        _, elements, line = node if len(node) == 3 else (None, node[1], 0)
        if not elements:
            return 'array'
    
        element_types = [self.visit(elem) for elem in elements if elem is not None]
        if len(element_types) > 0:
            pass
        return 'array'

    def visit_array_declaration(self, node):
        _, var_name, elements, line = node if len(node) == 4 else (None, node[1], node[2], 0)
        self.visit_var_declaration(('var_declaration', var_name, ('array', elements, line), line))
        return 'array'
    
    def visit_function_call(self, node):
        _, func_name, args, line = node if len(node) == 4 else (None, node[1], node[2], 0)
        if func_name not in self.variables and (not self.requireif):
            self.error(f"函数 '{func_name}' 未定义", node)
            return 'any'
        if func_name in self.builtin_func.type_hints:
            hint = self.builtin_func.type_hints[func_name]
            expected_arg_count, expected_types,_ = hint
            if(args == None):
                args = []
            if expected_arg_count != -1 and len(args) != expected_arg_count:
                self.error(f"函数 '{func_name}' 需要 {expected_arg_count} 个参数，但传入了 {len(args)} 个", node)
            for i, arg in enumerate(args):
                arg_type = self.visit(arg)
                if 'any' not in expected_types and arg_type not in expected_types:
                    self.error(f"函数 '{func_name}' 的第 {i+1} 个参数类型错误：预期 {expected_types}，但得到 {arg_type}", node)
        else:
            if func_name not in self.variables:
                return 'any'
            if(self.variables[func_name][0] != "function"):
                for arg in args:
                    self.visit(arg)
            else:
                return self.variables[func_name][2]['return_type']
        return self.builtin_func.type_hints[func_name][2]

def strip_line_numbers(node):
    if not isinstance(node, tuple):
        return node
    if len(node) > 1 and isinstance(node[-1], int) and not isinstance(node[-1], bool):
        cleaned = node[:-1]
    else:
        cleaned = node
    result = []
    for element in cleaned:
        if isinstance(element, tuple):
            result.append(strip_line_numbers(element))
        elif isinstance(element, list):
            result.append([strip_line_numbers(item) if isinstance(item, tuple) else item for item in element])
        else:
            result.append(element)
    return tuple(result)
def parse_oning_gram(filename,edit_closeit=False):
    handler = get_error_handler()
    
    with open(filename, encoding='utf-8') as f:
        contents = f.read()
        if not contents:
            err = QOTError(
                code=ErrorCode.ERR_COMPILE_GENERAL,
                message="脚本内容为空",
                level=ErrorLevel.ERROR,
                file_name=filename
            )
            print(err.format())
            handler.handle(err)
            sys.exit(1)
    
    try:
        parser = QotPlyParser()
        ast = parser.parse(filename)
        if ast in (None, 1):
            return None
        analyzer = SemanticAnalyzer()
        if not analyzer.analyze(ast):
            def extract_line_number(error_msg):
                try:
                    if "行 " in error_msg:
                        parts = error_msg.split("行 ")
                        if len(parts) > 1:
                            line_part = parts[1].split(" ")[0].split(" -")[0]
                            return int(line_part)
                    elif "行" in error_msg:
                        parts = error_msg.split("行")
                        if len(parts) > 1:
                            line_part = parts[1].strip().split(" ")[0].split(":")[0]
                            return int(line_part)
                except (ValueError, IndexError):
                    pass
                return 0
            
            for error in sorted(analyzer.errors, key=extract_line_number):
                err = QOTError(
                    code=ErrorCode.ERR_RUNTIME_GENERAL,
                    message=error,
                    level=ErrorLevel.WARNING,
                    file_name=handler._current_source_file
                )
                handler.handle(err)
        if(edit_closeit):
            cast = strip_line_numbers(ast)
            return cast
        else:
            return ast
        
    except SyntaxError as e:
        err = QOTError(
            code=ErrorCode.ERR_SYNTAX_GENERAL,
            message=f"代码运行失败: {str(e)}",
            level=ErrorLevel.ERROR,
            file_name=filename
        )
        handler.handle(err)
        return None
    except (FileNotFoundError, PermissionError, UnicodeDecodeError) as e:
        err = QOTError(
            code=ErrorCode.ERR_IO_GENERAL,
            message=f"文件处理错误: {str(e)}",
            level=ErrorLevel.ERROR,
            file_name=filename
        )
        handler.handle(err)
        return None
    except Exception as e:
        err = QOTError(
            code=ErrorCode.ERR_COMPILE_GENERAL,
            message=f"代码运行过程中发生未预期错误: {str(e)}",
            level=ErrorLevel.ERROR,
            file_name=filename
        )
        handler.handle(err)
        return None