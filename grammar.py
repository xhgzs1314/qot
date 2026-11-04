import os
import sys
import traceback
import ply.yacc as yacc
from token_char import Lexer, TokenType, Token, LexerError
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
    'GREATER','LESS','GREATEREQ','LESSEQ','EQUALS','LOOP','STOP','CONTINUE','FUNC', 'RETURN',
    "FOR","IN",'NUMBER_T', 'STRING_T', 'BOOLEAN_T', 'ARRAY_T', 'ANY_T','ARROW','BOOLEAN',"REQUIRE",
    'DICT_START',"ANNOTATION",
    'DICT_END',
    'COLON',
    "FUNCTION_REF",
    'AND', 'OR',
    'NULL','POW',
    'DCOLON',
    'TRY','CATCH','THROW',
    # 【G-03 0】三元 ?: 的问号 token（冒号复用 COLON——字典字面量与
    # 三元共用一个冒号 token，靠文法上下文区分，见 p_expression 的冲突说明）
    'QUESTION',
    # 【G-04 0】for-in 区间 `A..B` 与步长子句 `by S`
    'DOTDOT', 'BY',
    # 【M4 0】最小对象模型：class 声明
    'CLASS',
    # 【GOD-SELF §8.24 0】macro 关键字（编译期宏声明）+
    # BANG（`!`：宏调用后缀 name!(...)；GS 收尾后同一 token 兼作
    # 一元逻辑非 —— 前缀位 = 逻辑非，标识符后紧跟 `!(` = 宏调用，
    # 词法/文法零冲突：`!=` 在词法层先于单 `!` 匹配，保持 NOTEQUAL）。
    'MACRO', 'BANG'
)

precedence = (
    # 比较运算符必须**低于**加减（C 系惯例）。
    # 旧表把比较排在加减之上 → `i == n - 1` 在状态
    #   relational_expression : relational_expression EQUALS additive_expression .
    # 遇 MINUS 时按"规则优先级 > 记号优先级"判为 REDUCE → 随后无处可放
    # MINUS → 语法错误（语料三个样本当场踩中）。POW/UMINUS/ARROW/DCOLON
    # 位置保持原样（不动已定价的 ^ 语义）。
    ('right', 'POW'),  
    ('right', 'ARROW'),
    ('left', 'DCOLON'), 
    # 【GOD-SELF 收尾 0】一元逻辑非 `!`：与 UMINUS 同级同结合
    # （`!a + b` = `(!a) + b`；`!!x` 右结合）。宏调用 `name!(...)` 的 `!`
    # 不走优先级表 —— 它在 primary 层紧跟标识符，文法形态唯一。
    ('right', 'BANG'),  
    ('right', 'UMINUS'),  
    # 【G-03 0】三元 ?: —— 低于 AND/OR 与比较（C 系惯例：
    # `a && b ? c : d` 解析为 `(a&&b) ? c : d`；`x > 0 ? p : n` 的条件先
    # 归约）。右结合（`a?b:c?d:e` = `a?b:(c?d:e)`，靠默认 shift 达成贪心）。
    # 注意：三元的产生式以 COLON 结尾（COLON 无优先级），本条目主要服务
    # 「条件完整后遇 QUESTION 先 REDUCE」这一组冲突。
    ('right', 'QUESTION'),
    ('left', 'AND', 'OR'),
    ('left', 'GREATER', 'LESS', 'GREATEREQ', 'LESSEQ', 'EQUALS', 'NOTEQUAL'),
    ('left', 'PLUS', 'MINUS'),
    ('left', 'MULTIPLY', 'DIVIDE', 'MOD'),
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

# ---------------- try/catch/throw（F2 错误模型） ----------------
def p_statement_try(p):
    '''statement : TRY LBRACE statement_list RBRACE CATCH LPAREN IDENTIFIER RPAREN LBRACE statement_list RBRACE
                 | TRY LBRACE statement_list RBRACE CATCH LBRACE statement_list RBRACE'''
    if len(p) == 12:
        p[0] = ('try_statement', p[3], p[7], p[10], get_line_number(p))
    else:
        p[0] = ('try_statement', p[3], None, p[7], get_line_number(p))

def p_statement_throw(p):
    '''statement : THROW expression SEMICOLON'''
    p[0] = ('throw_statement', p[2], get_line_number(p))

# ---------------- lambda 字面量（闭包的语法糖） ----------------
def p_primary_expression_lambda(p):
    '''primary_expression : FUNC LPAREN parameter_list_opt RPAREN ARROW expression'''
    p[0] = ('lambda_expr', p[3], p[6], get_line_number(p))

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

# ---------------- M4 最小对象模型（0） ----------------
# 语法面：
#   class Name { var f [= init]; ...; [type] func method(params) { ... } }
#   方法名 init = 构造器（类名调用 Point(1,2) 触发）；方法体里 this = 接收者。
#   字段读写：obj->f / obj->f = e（含复合赋值族）；方法调用：obj->m(args)。
# 语义定价（README §6.4 / SPEC）：
#   * 对象 = 固定布局（字段按声明序偏移）+ 类型表分派方法表 —— 不是字典；
#   * 未声明字段：读得 null、写被忽略（宽松定价，两侧同律）；
#   * 对象缺席方法调用得 null；非对象接收者的 ->m 走既有「receiver 前置」
#     调用定价（内置/用户函数），零迁移；
#   * this 在方法体内恒指接收者（遮蔽外层同名变量）；
#   * init 内禁止 return（编译期致命）；class 声明仅限顶层。
def p_statement_class_declaration(p):
    '''statement : CLASS IDENTIFIER LBRACE class_body_opt RBRACE'''
    p[0] = ('class_declaration', p[2], p[4] or [], get_line_number(p))

def p_class_body_opt(p):
    '''class_body_opt : class_body
                      | empty'''
    p[0] = p[1] if p[1] is not None else []

def p_class_body(p):
    '''class_body : class_member
                  | class_body class_member'''
    if len(p) == 2:
        p[0] = [p[1]]
    else:
        p[1].append(p[2])
        p[0] = p[1]

def p_class_member(p):
    '''class_member : VAR IDENTIFIER SEMICOLON
                    | VAR IDENTIFIER EQUAL expression SEMICOLON
                    | type_specifier FUNC IDENTIFIER LPAREN parameter_list_opt RPAREN LBRACE statement_list RBRACE
                    | FUNC IDENTIFIER LPAREN parameter_list_opt RPAREN LBRACE statement_list RBRACE
                    | type_specifier FUNC IDENTIFIER LPAREN parameter_list_opt RPAREN LBRACE RBRACE
                    | FUNC IDENTIFIER LPAREN parameter_list_opt RPAREN LBRACE RBRACE'''
    ln = get_line_number(p)
    if p.slice[1].type == 'VAR':
        if len(p) == 4:
            p[0] = ('field', p[2], None, ln)
        else:
            p[0] = ('field', p[2], p[4], ln)
    elif p.slice[1].type == 'FUNC':
        # 【M4 人体工学】类方法可省返回类型（隐式 any_t）—— init 尤其
        # 无返回语义，逼用户写 any_t 是纯噪音；顶层函数保持必须显式。
        # 空体（init() { }）是构造器的自然形态 —— 单列变体。
        if len(p) == 9:
            p[0] = ('method', 'any_t', p[2], p[4], p[7], ln)
        else:
            p[0] = ('method', 'any_t', p[2], p[4], [], ln)
    else:
        # 方法（含构造器 init）。与顶层 func_declaration 同构。
        if len(p) == 10:
            p[0] = ('method', p[1], p[3], p[5], p[8], ln)
        else:
            p[0] = ('method', p[1], p[3], p[5], [], ln)

def p_primary_expression_field_access(p):
    # 【M4】字段读：obj->f（无括号形态；带括号是方法调用 —— lookahead 分流）
    '''primary_expression : primary_expression ARROW IDENTIFIER'''
    p[0] = ('field_access', p[1], p[3], get_line_number(p))

def p_statement_field_assignment(p):
    '''statement : primary_expression ARROW IDENTIFIER EQUAL expression SEMICOLON
                 | primary_expression ARROW IDENTIFIER PLUS_EQUAL expression SEMICOLON
                 | primary_expression ARROW IDENTIFIER MINUS_EQUAL expression SEMICOLON
                 | primary_expression ARROW IDENTIFIER MULTIPLY_EQUAL expression SEMICOLON
                 | primary_expression ARROW IDENTIFIER DIVIDE_EQUAL expression SEMICOLON
                 | primary_expression ARROW IDENTIFIER MOD_EQUAL expression SEMICOLON'''
    ln = get_line_number(p)
    if p.slice[4].type == 'EQUAL':
        p[0] = ('field_assignment', p[1], p[3], p[5], ln)
    else:
        op_map = {'+=': '+', '-=': '-', '*=': '*', '/=': '/', '%=': '%'}
        op = op_map[p[4]]
        read = ('field_access', p[1], p[3], ln)
        binary = ('binary_op', op, read, p[5], ln)
        p[0] = ('field_assignment', p[1], p[3], binary, ln)


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
    
    '''expression : logical_expression'''
    p[0] = p[1]

def p_expression_ternary(p):
    # 【G-03 0】三元表达式 `cond ? a : b`。
    # 挂在 expression 层（高于一切二元运算，条件/分支都是完整 expression）。
    # 冲突裁决（实测核对，非拍脑袋）：
    #  * `a && b ? c : d`：QUESTION 优先级低于 AND/OR → 条件先归约 ✓；
    #  * `a ? b : c ? d : e`：右结合贪心（无优先级的 REDUCE 规则默认 SHIFT）✓；
    #  * 与 dict_entry 的 COLON 共存：`{"k": a ? b : c}` —— 完整三元先归约
    #    （本规则声明在 p_expression_list/p_dict_entry 之前？不——dict_entry
    #    在文件更早处，但该状态里没有 dict_entry 项；真正的竞争对
    #    `expression_list : expression .` 在本规则之后 → 三元先归约 ✓，
    #    `f(a?b:c, d)` / `[a?b:c, d]` / `{"k": a?b:c}` 全部按贪心三元解析；
    #  * `{a ? b : c : d}`：键位贪心吃掉第一个三元，再吃字典自己的冒号
    #    → 唯一合法形态是「三元做键」（`{(a?b:c) : d}` 括号形态更清晰）。
    '''expression : logical_expression QUESTION expression COLON expression'''
    p[0] = ('ternary_expr', p[1], p[3], p[5], get_line_number(p))

def p_expression_list(p):
    '''expression_list : expression
                       | expression_list COMMA expression'''
    if len(p) == 2:
        p[0] = [p[1]]
    else:
        p[1].append(p[3])
        p[0] = p[1]

def p_statement_for(p):
    # 【G-04 0】四种形态：
    #   for (x in E)             —— 既有：数字 N / 数组 / 字符串 / 字典（M3 统一）
    #   for (x in E by S)        —— 序列按步长/方向迭代（S 数字字面量）
    #   for (x in A..B)          —— 数值区间（半开：B 不含；B<=A → 空区间）
    #   for (x in A..B by S)     —— 带步长区间（S 符号定方向；by 0 → 编译期致命）
    # 区间/步长节点只允许出现在 for-in 位置（range_expr/stepped_expr 不进
    # 普通表达式层）。
    '''statement : FOR LPAREN IDENTIFIER IN expression RPAREN LBRACE statement_list RBRACE
                 | FOR LPAREN IDENTIFIER IN expression BY expression RPAREN LBRACE statement_list RBRACE
                 | FOR LPAREN IDENTIFIER IN expression DOTDOT expression RPAREN LBRACE statement_list RBRACE
                 | FOR LPAREN IDENTIFIER IN expression DOTDOT expression BY expression RPAREN LBRACE statement_list RBRACE'''
    ln = get_line_number(p)
    if len(p) == 10:
        p[0] = ('for_statement', p[3], p[5], p[8], ln)
    elif len(p) == 12:
        if p.slice[6].type == 'BY':
            p[0] = ('for_statement', p[3], ('stepped_expr', p[5], p[7], ln), p[10], ln)
        else:
            p[0] = ('for_statement', p[3], ('range_expr', p[5], p[7], None, ln), p[10], ln)
    else:
        p[0] = ('for_statement', p[3],
                ('range_expr', p[5], p[7], p[9], ln), p[12], ln)

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
                          | BANG primary_expression %prec BANG
                          | IDENTIFIER LBRACKET expression RBRACKET
                          | array_index
                          | array_index_chain
                          | static_method_call'''
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
    elif p[1] == '!':
        # 【GOD-SELF 收尾 0】一元逻辑非。kind 'NOT' 直通既有下游：
        # bytecode._unop_map {'NOT': 2} → UNARY_OP(11) → LIR UNARY_GEN pred=2
        # → qotrt_unary(2) = !truthy —— 下游零改动（§8.19 前端三处缺口的收官）。
        p[0] = ('unary_op', 'NOT', p[2], get_line_number(p))
    elif len(p) == 5 and p[2] == '[' and isinstance(p[1], str):  
        p[0] = ('array_index', p[1], p[3], get_line_number(p))
    elif len(p) == 5 and p[2] == '[' and isinstance(p[1], tuple):  
        p[0] = ('array_index_chain', p[1], p[3], get_line_number(p))
    elif len(p) == 6 and p[2] == '->':  
        p[0] = ('method_call', p[1], p[3], p[5], get_line_number(p))
    else:
        p[0] = p[1]
def p_statement_macro_declaration(p):
    # 【GOD-SELF-G2 §8.24 0】编译期宏声明：macro name(p...) { body }。
    # 仅限顶层（函数体内声明 → sema 致命，与 class 同律）；宏体 = 普通 QOT
    # 语句表，编译期以解释器执行（形参收 AST 节点值，return 产物）。
    '''statement : MACRO IDENTIFIER LPAREN parameter_list_opt RPAREN LBRACE statement_list RBRACE'''
    p[0] = ('macro_declaration', p[2], p[4] or [], p[7], get_line_number(p))


def p_macro_call_expr(p):
    # 宏调用（表达式位）：name!(args)。`!` 紧跟标识符 + `(` 的形态在文法里
    # 唯一（逻辑非是一元前缀，永不接在标识符后）——与既有文法零冲突。
    '''primary_expression : IDENTIFIER BANG LPAREN expression_list_opt RPAREN'''
    p[0] = ('macro_call', p[1], p[4], None, get_line_number(p))


def p_statement_macro_call(p):
    # 宏调用（语句位）：name!(args);
    '''statement : IDENTIFIER BANG LPAREN expression_list_opt RPAREN SEMICOLON'''
    p[0] = ('macro_call_statement',
            ('macro_call', p[1], p[4], None), get_line_number(p))


def p_statement_macro_call_block(p):
    # 宏调用（语句位·块形态）：name!(args) { stmts }
    # 块以语句 AST 列表绑定到宏的最后一个形参（控制流宏的载体，
    # 官方宏 unless! 即此形态）。块后不要求分号（与 if/loop 一致）。
    '''statement : IDENTIFIER BANG LPAREN expression_list_opt RPAREN LBRACE statement_list RBRACE'''
    p[0] = ('macro_call_statement',
            ('macro_call', p[1], p[4], p[7]), get_line_number(p))


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


# 【G-01 0】continue：跳过本轮剩余语句。语义 = 跳到循环的"继续点"
# —— while 的继续点是条件；for 的继续点是**步进**（步进不在 body 里，若跳
# 到条件就死循环）。与 stop 一样只作用于最内层循环。
def p_statement_continue(p):
    '''statement : continue_statement'''
    p[0] = p[1]


def p_continue_statement(p):
    '''continue_statement : CONTINUE SEMICOLON'''
    p[0] = ('continue_statement', get_line_number(p))

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
        # 前端提速：LALR 表落盘缓存（write_tables=False 曾让每次冷启动
        # 重算整张 LALR 表 ~0.4s）。表带规则签名，grammar 规则一变自动失效
        # 重建，无陈旧风险；表文件 qot_parsetab.py 写在本目录旁（随源码走，
        # 不依赖 cwd）。任何异常回落旧路径（不落表）。
        _tabdir = os.path.dirname(os.path.abspath(__file__))
        try:
            self.parser = yacc.yacc(debug=False, write_tables=True,
                                    outputdir=_tabdir, optimize=True,
                                    tabmodule='qot_parsetab',
                                    module=sys.modules.get(__name__))
        except Exception:
            self.parser = yacc.yacc(debug=False, write_tables=False, outputdir=None)
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
                # 【G-03 0】三元问号（冒号复用既有 COLON）
                '?': 'QUESTION',
                # 【G-04 0】for-in 区间算子
                '..': 'DOTDOT',
                # 【GOD-SELF §8.24 0】一元逻辑非 / 宏调用后缀。
                # `!=` 在词法层先于单 `!` 匹配（NOTEQUAL 不受影响）。
                '!': 'BANG',
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
        # 【G-10 0】静态可判错误的致命通道。内置实参数量错配过去
        # 只发软警告、调用不执行（str_trim(s,";") 原样留分号；to_array(a,b)
        # 返回 null 且无诊断）—— 静默错比报错危险得多（宪法 6 / S-12）。
        # 判据：内置 arity 在清单里是写死的（argc >= 0），错配 = 编译期失败。
        self.fatal = False
        # 【P1030-2 · module 作用域注册】require 的语义面。此前 require
        # 只把 requireif 置 True 且永不复位 —— 模块名/成员完全不认识（每个
        # `mod##fn` 报两条未定义警告），且主程序此后所有「函数未定义」检查
        # 被静默压制。现在：require 登记模块变量 + 解析模块单元登记其导出
        # 函数面（名字 → 参数个数），`mod##fn` 按登记表查成员/参数个数；
        # 登记完成后 requireif 复位，主程序自己的未定义检查恢复。
        self.base_dir = None          # 被分析单元所在目录（require 相对解析基准）
        self._module_funcs = {}       # 模块名 → {函数名: 参数个数}
        self._module_loading = set()  # require 递归登记的循环防护
    
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
            # 【S-14 终审 0】字符串一律 'string'（与运行期值一致）：
            # 字面量已不再编译期转数字（bytecode.visit_literal），sema 的静态
            # 类型必须同步 —— 否则类型面与运行期值面分叉。
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

    def analyze(self, ast, module_unit=False):
        if not ast or ast == 1:
            return False
        if module_unit:
            # 【P1030-2】模块单元两遍制：先登记全部顶层函数（module 作用域），
            # 再走常规访问。运行期 wrapper 全量安装 module_funcs（序无关），
            # sema 的声明点注册曾让前向互调误报未定义 —— 两遍制对齐运行期。
            # 主程序单元保持「先声明后用」（其运行期是顺序求值，前向调用
            # 本就会失败，sema 不许比运行期更松）。
            if isinstance(ast, list):
                for stmt in ast:
                    if (isinstance(stmt, tuple) and stmt
                            and stmt[0] == 'func_declaration' and len(stmt) >= 4):
                        rt = stmt[1] or 'any'
                        if rt.endswith('_t'):
                            rt = rt[:-2]
                        self.variables[stmt[2]] = (
                            'function', False,
                            {'params': stmt[3] or [], 'return_type': rt})
        self.visit(ast)
        return len(self.errors) == 0

    def _prescan_macros(self, ast):
        """【GOD-SELF-G2 §8.24】顶层宏面预登记（先声明后用与主程序同律）：
        name → 参数个数。宏体内对其他宏的调用按本表放行（顺序无关 ——
        宏都是编译期实体，体的执行发生在收集全部宏之后）。"""
        self._macro_names = {}
        stmts = None
        if isinstance(ast, tuple) and ast and ast[0] == 'start':
            stmts = ast[1]
        elif isinstance(ast, list):
            stmts = ast
        for stmt in (stmts or []):
            if (isinstance(stmt, tuple) and stmt
                    and stmt[0] == 'macro_declaration'):
                self._macro_names[stmt[1]] = len(stmt[2] or [])
    
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
        elif node_type == 'try_statement':
            return self.visit_try_statement(node)
        elif node_type == 'throw_statement':
            return self.visit_throw_statement(node)
        elif node_type == 'lambda_expr':
            return self.visit_lambda_expr(node)
        elif node_type == 'ternary_expr':
            # 【G-03 0】三元：三段都查，类型 = 分支公共类型（否则 any）
            return self.visit_ternary_expr(node)
        elif node_type == 'class_declaration':
            # 【M4 0】最小对象模型
            return self.visit_class_declaration(node)
        elif node_type == 'macro_declaration':
            # 【GOD-SELF-G2 §8.24】编译期宏声明（仅顶层；体按普通 QOT 查）
            return self.visit_macro_declaration(node)
        elif node_type == 'macro_call':
            # 【GOD-SELF-G2 §8.24】name!(args) 静态面
            return self.visit_macro_call(node)
        elif node_type == 'field_access':
            return self.visit_field_access(node)
        elif node_type == 'field_assignment':
            return self.visit_field_assignment(node)
        elif node_type == 'return_statement':
            # 【M4】init 体内禁止 return（构造恒返回对象，静态可判 → 致命）
            return self.visit_return_statement(node)
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

    def _check_module_member(self, left_expr, member_name, args, node):
        """`mod##member` 的成员存在性与参数个数检查（module 作用域登记表）。
        登记面为空（DLL / 解析失败 / 未走 require 直呼字典）→ 放行。"""
        if not (isinstance(left_expr, tuple) and left_expr
                and left_expr[0] == 'identifier'):
            return
        mod_name = left_expr[1]
        entry = self.variables.get(mod_name)
        if not entry or entry[0] != 'module':
            return
        funcs = self._module_funcs.get(mod_name) or {}
        if not funcs:
            return  # 无登记面（DLL/解析失败）→ 与旧行为一致，放行
        if member_name not in funcs:
            self.error(f"模块 '{mod_name}' 没有成员 '{member_name}'", node)
            return
        argc = funcs[member_name]
        n_args = len(args or [])
        if argc != n_args:
            self.error(f"模块函数 '{mod_name}##{member_name}' 需要 {argc} 个参数，"
                       f"但传入了 {n_args} 个", node)

    def visit_static_method_call(self, node):
        _, left_expr, method_name, args, line = node
        left_type = self.visit(left_expr)
        if left_type not in ('dict', 'module', 'any'):
            self.error(f"左值不是模块或字典，无法调用成员 '{method_name}'", node)
        self._check_module_member(left_expr, method_name, args, node)
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

    def visit_try_statement(self, node):
        _, body, catch_var, catch_body, line = node if len(node) == 5 else (None, node[1], node[2], node[3], 0)
        self.visit(body)
        if catch_body:
            current_vars = self.variables.copy()
            if catch_var:
                self.variables[catch_var] = ('var', False, 'any')
            self.visit(catch_body)
            self.variables = current_vars
        return None

    def visit_throw_statement(self, node):
        _, expr, line = node if len(node) == 3 else (None, node[1], 0)
        return self.visit(expr)

    def visit_lambda_expr(self, node):
        _, params, expr, line = node if len(node) == 4 else (None, node[1], node[2], 0)
        current_vars = self.variables.copy()
        for param in (params or []):
            self.variables[param] = ('param', False, 'any')
        self._m4_fn_depth = getattr(self, '_m4_fn_depth', 0) + 1
        self._m4_stack = getattr(self, '_m4_stack', [])
        self._m4_stack.append((None, None))
        try:
            result = self.visit(expr)
        finally:
            self._m4_stack.pop()
            self._m4_fn_depth -= 1
            self.variables = current_vars
        return result or 'function'

    def visit_ternary_expr(self, node):
        """【G-03 0】`cond ? a : b`。两分支惰性求值由运行期保证
        （bytecode 用跳转 + 临时槽；interp 直接短路），sema 只做三段类型检查。
        静态类型 = 两分支同型则该型，否则 any（f64 单一数值面下 'number'
        与 'number' 自然同型）。"""
        _, cond, then_e, else_e, line = node if len(node) == 5 else (None, node[1], node[2], node[3], 0)
        self.visit(cond)
        t_type = self.visit(then_e)
        e_type = self.visit(else_e)
        if t_type == e_type and t_type not in (None, 'unknown'):
            return t_type
        return 'any'

    # ---------------- M4 最小对象模型（0） ----------------
    def visit_class_declaration(self, node):
        """class 声明的语义面：
          * 仅限顶层（函数体内声明 → 编译期致命）；
          * 类名占用可调用命名面（Point(1,2) = 构造）；
          * 字段重名 / 方法重名 / init 参数个数（构造调用点按 G-10 律核对）
            均为静态可判 → 致命；
          * init 体内禁止 return（两侧构造语义 = 恒返回对象，静态可判 → 致命）；
          * 方法体以 this 为隐式首参（遮蔽外层同名）。
        """
        _, cname, members, line = node if len(node) == 4 else (None, node[1], node[2], 0)
        if getattr(self, '_m4_fn_depth', 0) > 0:
            self.error(f"class 声明仅限顶层（当前在函数体内）：'{cname}'", node)
            self.fatal = True
            return None
        if cname in self.variables or cname in getattr(self, '_m4_classes', {}):
            self.error(f"类 '{cname}' 已定义（类名与函数/变量共享命名面）", node)
            self.fatal = True
            return None
        if not hasattr(self, '_m4_classes'):
            self._m4_classes = {}
        fields, methods = [], {}
        for m in (members or []):
            if not isinstance(m, tuple) or not m:
                continue
            if m[0] == 'field':
                fname = m[1]
                init_expr = m[2] if len(m) > 2 else None
                if fname in fields:
                    self.error(f"类 '{cname}' 的字段 '{fname}' 重复声明", node)
                    self.fatal = True
                    continue
                fields.append(fname)
                if init_expr is not None:
                    # 字段初始化器在构造语义里先于 init 执行；this 不可用
                    #（尚未绑定接收者）—— 这里只做类型检查
                    self.visit(init_expr)
            elif m[0] == 'method' and len(m) >= 5:
                # strip 后 5 元组（rt/name/params/body），带行号 6 元组
                mname, params, body = m[2], m[3], m[4]
                if mname in methods or mname in fields:
                    self.error(f"类 '{cname}' 的成员 '{mname}' 重复声明", node)
                    self.fatal = True
                    continue
                methods[mname] = (params or [], body)
        self._m4_classes[cname] = {'fields': fields, 'methods': methods}
        # 类名登记为可调用（构造）；类型面 = 'object'
        self.variables[cname] = ('class', True, 'object')
        # 方法体的语义检查（this 为隐式首参；init 禁 return）
        for m in (members or []):
            if isinstance(m, tuple) and m and m[0] == 'method' and len(m) >= 5:
                mname, params, body = m[2], m[3], m[4]
                self._check_method_body(cname, mname, params or [], body, node)
        return 'object'

    # ---------------- GOD-SELF-G2 编译期宏（0） ----------------
    def visit_macro_declaration(self, node):
        """macro 声明的语义面：
          * 仅限顶层（函数体内声明 → 致命，与 class 同律）；
          * 重名 = 重定义报错（F0 现律；官方宏 swap/unless 由展开器注册，
            用户遮蔽同罪 —— 展开器兜底第二道）；
          * 参数个数登记进 _macro_names（visit_macro_call 的静态检查面）；
          * 宏体按普通 QOT 检查（形参预登记 —— 体在编译期以解释器执行，
            体内未定义名在 sema 期即报，不留到展开期才炸）。
        返回 'any'（宏不是运行期实体，不进 variables —— 宏名与函数/变量
        不共享命名面，`name!(...)` 是唯一的调用形态）。"""
        _, mname, params, body, line = (node + (None,) * 4)[:5]
        if getattr(self, '_m4_fn_depth', 0) > 0:
            self.error(f"macro 声明仅限顶层（当前在函数体内）：'{mname}'", node)
            self.fatal = True
            return None
        names = getattr(self, '_macro_names', None)
        if names is None:
            names = self._macro_names = {}
        if mname in names:
            self.error(f"宏 '{mname}' 已定义（宏名不可重复定义）", node)
            self.fatal = True
            return None
        params = list(params or [])
        names[mname] = len(params)
        current_vars = self.variables.copy()
        for p in params:
            self.variables[p] = ('param', False, 'any')
        try:
            self.visit(body)
        finally:
            self.variables = current_vars
        return 'any'

    def visit_macro_call(self, node):
        """name!(args) 的静态面：宏名必须已声明（官方 swap/unless 常驻
        登记表）；实参逐个走类型检查。块形态的块体也过一遍（块语句
        的未定义名等静态可判项不因宏而豁免）。"""
        _, mname, args, block = (node + (None,) * 3)[:4]
        names = getattr(self, '_macro_names', {})
        # 官方宏常驻（展开器同款注册面；sema 在此只是提前报错，展开器兜底）
        names.setdefault('swap', 2)
        names.setdefault('unless', 2)
        if mname not in names:
            self.error(f"宏 '{mname}' 未定义（name!() 调用只解析编译期宏）", node)
            self.fatal = True
            return 'any'
        for arg in (args or []):
            self.visit(arg)
        for stmt in (block or []):
            self.visit(stmt)
        return 'any'

    def _check_method_body(self, cname, mname, params, body, node):
        current_vars = self.variables.copy()
        self.variables['this'] = ('param', False, 'object')
        for p in params:
            self.variables[p] = ('param', False, 'any')
        self._m4_stack = getattr(self, '_m4_stack', [])
        self._m4_stack.append((cname, mname))
        try:
            self.visit(body)
        finally:
            self._m4_stack.pop()
            self.variables = current_vars

    def visit_field_access(self, node):
        _, obj_expr, fname, line = node if len(node) == 4 else (None, node[1], node[2], 0)
        self.visit(obj_expr)
        return 'any'

    def visit_field_assignment(self, node):
        _, obj_expr, fname, value_expr, line = node if len(node) == 5 else (None, node[1], node[2], node[3], 0)
        self.visit(obj_expr)
        self.visit(value_expr)
        return 'any'

    def visit_return_statement(self, node):
        """【M4】return 的语义检查：init 体内禁止（构造语义 = 恒返回对象，
        两侧同律的静态可判错误 → G-10 致命通道）。普通函数/方法的 return
        不受限。"""
        _, expr, _ln = node if len(node) == 3 else (None, node[1] if len(node) > 1 else None, 0)
        stack = getattr(self, '_m4_stack', [])
        if stack and stack[-1][1] == 'init':
            self.error("init（构造器）体内不能使用 return —— 构造恒返回对象本身", node)
            self.fatal = True
            return None
        if expr is not None:
            return self.visit(expr)
        return None
    
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
        # 【G-04 0】区间/步长形态的语义面 + 静态步长立法
        if isinstance(range_expr, tuple) and range_expr[0] == 'range_expr':
            start_t = self.visit(range_expr[1])
            end_t = self.visit(range_expr[2])
            if range_expr[3] is not None:
                self._check_for_step(range_expr[3], node, seq_form=False)
                self.visit(range_expr[3])
            if start_t not in ('number', 'any'):
                self.error(f"for 区间起点必须是数字，实际是 {start_t}", node)
            if end_t not in ('number', 'any'):
                self.error(f"for 区间终点必须是数字，实际是 {end_t}", node)
        elif isinstance(range_expr, tuple) and range_expr[0] == 'stepped_expr':
            base_t = self.visit(range_expr[1])
            self._check_for_step(range_expr[2], node, seq_form=True)
            self.visit(range_expr[2])
            if base_t not in ('number', 'array', 'string', 'dict',
                              'null', 'boolean', 'any', 'object'):
                # 【GOD-SELF-G1 0】object 入列：协议对象（__for_keys/
                # __iter_len）是正式迭代面（编译期静态绑定）；无协议对象 =
                # 既有零次定价。两者都不再告警。
                self.error(f"for 循环的 range 必须是数字/数组/字符串/字典，实际是 {base_t}", node)
        else:
            range_type = self.visit(range_expr)
            # 【M3 0】for-in 统一：字符串（单字节/字符）与字典（键）
            # 成为正式迭代面；null/boolean = 合法的零次迭代范围（不再告警）
            # 【GOD-SELF-G1 0】object = 协议迭代面（同上）
            if range_type not in ('number', 'array', 'string', 'dict',
                                  'null', 'boolean', 'any', 'object'):
                self.error(f"for循环的range必须是数字/数组/字符串/字典，实际是 {range_type}", node)
        current_vars = self.variables.copy()
        self.variables[var_name] = ('loop_var', False, 'any')  
        self.visit(body)
        self.variables = current_vars

    def _check_for_step(self, step_expr, node, seq_form):
        """【G-04 0】by 步长的静态立法（G-10 同款：静态可判错误 =
        编译期致命）：
          * 必须是编译期数字（字面量/括号/一元负号/字面量算术）—— 动态步长
            的方向分派会让循环体按运行期符号编译两份（体量翻倍），且真实
            需求（跳步/倒序）字面量全覆盖；动态步长请用 loop 循环；
          * 步长 != 0（by 0 必然是 bug：半开区间下要么死循环要么空转）；
          * 序列形态（E by S）要求整数步长（下标跳步；小数步长会触发 (int)
            截断的模糊定价）。区间形态（A..B by S）允许小数步长（0..1 by 0.25）。
        """
        v = statically_numeric_value(step_expr)
        if v is None:
            self.error("for 的 by 步长必须是编译期数字字面量（动态步长请用 loop 循环自行控制）", node)
            self.fatal = True
            return
        if v == 0:
            self.error("for 的 by 步长不能为 0（半开区间下 by 0 是死循环/空转 bug）", node)
            self.fatal = True
            return
        if seq_form and float(v) != int(v):
            self.error("序列迭代的 by 步长必须是整数（下标跳步）；小数步长仅区间形态允许", node)
            self.fatal = True
    
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
        # 【M4】函数体上下文：class 声明仅限顶层；嵌套函数的 return 合法
        #（init 的 return 禁令只作用于 init 自己的体）
        self._m4_fn_depth = getattr(self, '_m4_fn_depth', 0) + 1
        self._m4_stack = getattr(self, '_m4_stack', [])
        self._m4_stack.append((None, None))
        try:
            self.visit(body)
        finally:
            self._m4_stack.pop()
            self._m4_fn_depth -= 1
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
        if op == 'NOT':
            # 【GOD-SELF 收尾 0】一元逻辑非 `!`：truthy 取反，
            # 结果面 = boolean（native qotrt_unary op2 / interp is_truthy
            # 同一张 truthy 表：null/false/0/空串/空容器 = 假，其余真）。
            self.visit(expr)
            return 'boolean'
        else:
            self.error(f"未知一元运算符: {op}", node)
            return 'unknown'
    
    def visit_statement_list(self, statements):
        for stmt in statements:
            if stmt:  
                self.visit(stmt)
    
    def visit_require_statement(self, node):
        _, lib_name, line = node if len(node) == 3 else (None, node[1], 0)
        module_name = os.path.splitext(os.path.basename(lib_name))[0]
        self._register_module_unit(module_name, lib_name)
        # 模块变量：与解释器 visit_require_statement 的绑定同名（文件名去后缀）。
        self.variables[module_name] = ('module', True, 'module')
        # 导出面已登记，恢复主程序的「函数未定义」检查（requireif 不再是
        # 一开到底的 suppressing 开关 —— S-13 纪律：不许用宽放掩盖真实分歧）。
        self.requireif = False
        return 'module'

    def _resolve_module_path(self, lib_name):
        """require 路径解析：相对被分析单元所在目录（与运行期
        _resolve_lib_path 的 base_path 同基准）。"""
        if os.path.isabs(lib_name):
            return lib_name
        base = self.base_dir or os.getcwd()
        return os.path.normpath(os.path.join(base, lib_name))

    def _register_module_unit(self, module_name, lib_name):
        """登记一个模块单元的导出函数面（module 作用域注册）。

        解析模块源码（只走 parser，不再触发递归 sema），收集顶层
        func_declaration 的（名字，参数个数）。作用：
          * 主程序单元：`mod##fn` 的成员存在性与参数个数检查有了真相源；
          * 模块单元自身（module_unit=True 时）：顶层函数先行登记 ——
            模块内前向互调在 sema 期合法（运行期 wrapper 全量安装
            module_funcs，本来就序无关；此前只有声明点注册，前向调用
            误报未定义 —— §8.13 遗留②）。
        循环 require 以 _module_loading 防护；非 .qot（DLL 等）与解析失败
        只登记空面（成员检查退化为放行），不改变错误级别。
        """
        if module_name in self._module_funcs or module_name in self._module_loading:
            return
        if not lib_name.endswith('.qot'):
            self._module_funcs[module_name] = {}
            return
        path = self._resolve_module_path(lib_name)
        if not os.path.exists(path):
            # 运行期会给出 [3002] 明确报错；sema 侧留空面并提示一次
            self._module_funcs[module_name] = {}
            return
        self._module_loading.add(module_name)
        try:
            parser = QotPlyParser()
            mast = parser.parse(path)
            funcs = {}
            reqs = []
            if mast and mast != 1 and isinstance(mast, list):
                for stmt in mast:
                    if not isinstance(stmt, tuple) or not stmt:
                        continue
                    if stmt[0] == 'func_declaration' and len(stmt) >= 4:
                        funcs[stmt[2]] = len(stmt[3] or [])
                    elif stmt[0] == 'require_statement' and len(stmt) >= 2:
                        reqs.append(stmt[1])
            self._module_funcs[module_name] = funcs
            # 依赖模块也登记（供模块自身 sema 里的 mod##fn 检查）；相对
            # 基准 = 本模块目录。
            saved_base = self.base_dir
            self.base_dir = os.path.dirname(os.path.abspath(path))
            try:
                for dep in reqs:
                    dep_name = os.path.splitext(os.path.basename(dep))[0]
                    self._register_module_unit(dep_name, dep)
            finally:
                self.base_dir = saved_base
        except Exception:
            # 模块源码解析不出来（语法错等）：留空面。真正的错误由运行期
            # /编辑器语义错误通道报，不在这里二次报告
            self._module_funcs[module_name] = {}
        finally:
            self._module_loading.discard(module_name)
    
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
                        # 【M4】object 参与比较是合法定价（同一性相等；
                        # qotrt_veq/interp 同律）—— 不告警
                        if(left_type != 'object'):
                            self.error(f"比较运算符 '{op}' 左边的操作数必须是数字类型，实际是 {left_type}", node)
            if right_type != 'number' and not self.numbercangetmaybe(right[1]):
                if(right_type != 'any'):
                    if(right_type != 'boolean' and right_type != 'string' and right_type != 'null'):
                        if(right_type != 'object'):
                            self.error(f"比较运算符 '{op}' 右边的操作数必须是数字类型，实际是 {right_type}", node)
            return 'boolean'
        if op in ('+', '-', '*', '/', '%'):
            # 【GOD-SELF-G1 0】object 入列：类型已知的对象参与五则
            # 是协议分派的合法面（__add__ 族编译期静态绑定，§8.24-B）；静态
            # 面分不出"哪个类"也就分不出协议在不在 → 不告警（与 M4 的比较
            # 同款处理）。
            if left_type != 'number' and (not self.numbercangetmaybe(left[1])):
                if(left_type != 'array_known' and left_type != 'string'):
                    if(left_type != 'any' and left_type != None):
                        if(left_type != 'object'):
                            self.error(f"运算符 '{op}' 左边的操作数必须是数字类型，实际是 {left_type}", node)
            if right_type != 'number' and (not self.numbercangetmaybe(right[1])):
                if(right_type != 'array_known' and right_type != 'string'):
                    if(right_type != 'any'):
                        if(right_type != None):
                            if(right_type != 'object'):
                                self.error(f"运算符 '{op}' 右边的操作数必须是数字类型，实际是 {right_type}", node)
            # 【CP-N4 0】string + 非 string 是合法拼接（README 20.3；
            # native qotrt_arith op1 与 interp 运行期已同律），不再告警 ——
            # 旧警告把 README 明文的合法代码标成语义错误（语料线实证）。
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
        if args is None:
            args = []
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
                # G-10：内置 arity 由清单写死 → 错配是静态可判的编译期错误
                self.fatal = True
            for i, arg in enumerate(args):
                arg_type = self.visit(arg)
                if 'any' not in expected_types and arg_type not in expected_types:
                    self.error(f"函数 '{func_name}' 的第 {i+1} 个参数类型错误：预期 {expected_types}，但得到 {arg_type}", node)
        else:
            if func_name not in self.variables:
                return 'any'
            # 【M4】类名调用 = 构造。init 参数个数是清单级静态事实 →
            # 错配按 G-10 律走致命通道（绝不放行「警告 + 静默错跑」）。
            if self.variables[func_name][0] == 'class':
                cls = getattr(self, '_m4_classes', {}).get(func_name) or {}
                init = (cls.get('methods') or {}).get('init')
                if init is not None:
                    want = len(init[0] or [])
                    if want != len(args):
                        self.error(f"构造 {func_name}() 需要 {want} 个参数（init），"
                                   f"但传入了 {len(args)} 个", node)
                        self.fatal = True
                for arg in args:
                    self.visit(arg)
                return 'object'
            if(self.variables[func_name][0] != "function"):
                for arg in args:
                    self.visit(arg)
                return 'any'
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
def statically_numeric_value(expr):
    """【G-04 0】编译期可求值的数字表达式 → 返回数值；否则 None。
    覆盖 literal / 括号 / 一元负号 / + - * / % ^ 的字面量组合（与
    bytecode._range_is_statically_numeric 同一形态面，供步长静态校验用）。"""
    if not isinstance(expr, tuple):
        return expr if isinstance(expr, (int, float)) and not isinstance(expr, bool) else None
    node_type = expr[0]
    if node_type == 'literal':
        v = expr[1]
        return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None
    if node_type == 'parenthesized':
        return statically_numeric_value(expr[1])
    if node_type == 'unary_op' and expr[1] == 'UMINUS':
        v = statically_numeric_value(expr[2])
        return None if v is None else -v
    if node_type == 'binary_op' and expr[1] in ('+', '-', '*', '/', '%', '^'):
        a = statically_numeric_value(expr[2])
        b = statically_numeric_value(expr[3])
        if a is None or b is None:
            return None
        try:
            if expr[1] == '+': return a + b
            if expr[1] == '-': return a - b
            if expr[1] == '*': return a * b
            if expr[1] == '/': return a / b
            if expr[1] == '%': return a % b
            if expr[1] == '^': return a ** b
        except Exception:
            return None
    return None


def parse_oning_gram(filename,edit_closeit=False,module_unit=False):
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
        # require 相对解析基准 = 被分析单元所在目录（主程序/模块同规则）
        analyzer.base_dir = os.path.dirname(os.path.abspath(filename))
        if not analyzer.analyze(ast, module_unit=module_unit):
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
            # G-10：静态可判错误（内置 arity 错配）→ 编译失败（哨兵 None），
            # 与语法错误同一出口（调用方 exit 1）。绝不放行"警告 + 静默错跑"。
            if getattr(analyzer, 'fatal', False):
                return None
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
    except LexerError as e:
        # 【P1030-4 · D-01/D-02】词法硬错误（未闭合字符串 1008 / 行内注解），
        # 此前未闭合串只打警告后照常编译成功。
        err = QOTError(
            code=e.code if e.code in (ErrorCode.ERR_SYNTAX_UNTERMINATED_STRING,
                                      ErrorCode.ERR_SYNTAX_GENERAL) else ErrorCode.ERR_SYNTAX_GENERAL,
            message=e.msg,
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