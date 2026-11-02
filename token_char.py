import re
import sys
import ply.lex as lex


class LexerError(Exception):
    def __init__(self, code, msg, line=0):
        self.code = code
        self.msg = msg
        self.line = line
        super().__init__(msg)


class TokenType:
    KEYWORD = "KEYWORD"
    IDENTIFIER = "IDENTIFIER"
    NUMBER = "NUMBER"
    STRING = "STRING"
    OPERATOR = "OPERATOR"
    PUNCTUATOR = "PUNCTUATOR"
    EOF_TOKEN = "EOF_TOKEN"
    COMMENT = "COMMENT"
    BOOLEAN = "BOOLEAN"
    REQUIRE = "REQUIRE"
    DICT_START = "DICT_START"  
    DICT_END = "DICT_END"      
    COLON = "COLON"  
    FUNCTION_REF = "FUNCTION_REF"
    ANNOTATION = "ANNOTATION"
    DCOLON = "DCOLON"
class Token:
    def __init__(self, type_, value, line, column):
        self.type = type_
        self.value = value
        self.line = line
        self.column = column

class Lexer:
    tokens = (
        'NUMBER',
        'STRING',
        'OPERATOR',
        'PUNCTUATOR',
        'WSTRING',
        'USTRING', 
        'IDENTIFIER',
        'COMMENT',
        'DICT_START',  
        'DICT_END',    
        'COLON' ,
        "FUNCTION_REF",
        'ANNOTATION',
        "DCOLON"
    )
    def __init__(self):
        self.lexer = lex.lex(module=self,reflags=re.UNICODE)
        self.keywords = {"if", "else","eif", "loop", "for", "in","func", "return", "var",
        "const", "null",'stop','number_t','string_t','boolean_t','array_t','any_t',
        "true","false","require","try","catch","throw",
        "continue",
        "by",
        "class",
        "macro"}
        self.unknown_char_warning_count = 0
        self.MAX_WARNINGS = 5
        self.filename = ""
    def t_ANNOTATION(self, t):
        r'@-@-@.*'
        data = t.lexer.lexdata
        pos = t.lexpos
        if pos > 0 and data[pos - 1] not in '\r\n':
            raise LexerError(1000, f"注解必须独占一行（行 {t.lineno}）",
                             t.lineno)
        t.value = t.value[5:].strip()
        t.type = TokenType.ANNOTATION
        return t
    def input(self, data, filename):
        self.lexer.input(data)
        self.filename = filename
    def t_USTRING(self, t):
        r'U"[^"\\]*(\\.[^"\\]*)*"'
        raw_content = t.value[2:-1]
        content = raw_content.replace('\\n', '\n').replace('\\t', '\t').replace('\\r', '\r')
        content = content.replace('\\"', '"').replace('\\\\', '\\')
        t.value = content.encode('utf-8')
        t.type = TokenType.STRING
        return t

    def t_WSTRING(self, t):
        r'L"[^"\\]*(\\.[^"\\]*)*"'
        raw_content = t.value[2:-1]
        t.value = raw_content.replace('\\n', '\n').replace('\\t', '\t').replace('\\r', '\r')
        t.value = t.value.replace('\\"', '"').replace('\\\\', '\\')
        t.type = TokenType.STRING 
        return t

    def t_STRING(self, t):
        r'(?<!L)(?<!U)"([^"\\]|\\.)*"|\'([^\'\\]|\\.)*\''
        t.value = t.value[1:-1].replace('\\n', '\n').replace('\\t', '\t').replace('\\r', '\r')
        t.value = t.value.replace('\\"', '"').replace("\\'", "'").replace('\\\\', '\\')
        return t

    def t_UNTERMINATED_STRING(self, t):
        r'(?<!L)(?<!U)"([^"\\\n]|\\.)*|(?<!L)(?<!U)\'([^\'\\\n]|\\.)*'
        raise LexerError(1008, f"字符串未正确闭合（行 {t.lineno}）", t.lineno)
    def token(self):
        tok = self.lexer.token()
        if not tok:
            return Token(TokenType.EOF_TOKEN, "", self.lexer.lineno, self.lexer.lexpos)
        if tok.type == 'IDENTIFIER':
            if tok.value in self.keywords:
                if tok.value in ('true', 'false'):
                    return Token(TokenType.BOOLEAN, tok.value, tok.lineno, tok.lexpos)
                if tok.value == 'null':
                    return Token('NULL', None, tok.lineno, tok.lexpos)
                return Token(TokenType.KEYWORD, tok.value, tok.lineno, tok.lexpos)
            return Token(TokenType.IDENTIFIER, tok.value, tok.lineno, tok.lexpos)
        type_map = {
            'NUMBER': TokenType.NUMBER,
            'STRING': TokenType.STRING,
            'OPERATOR': TokenType.OPERATOR,
            'PUNCTUATOR': TokenType.PUNCTUATOR,
            'DICT_START': TokenType.DICT_START,  
            'DICT_END': TokenType.DICT_END,      
            'COLON': TokenType.COLON ,
            'FUNCTION_REF': TokenType.FUNCTION_REF,
            'ANNOTATION': TokenType.ANNOTATION,
            'DCOLON': TokenType.DCOLON,
        }
        return Token(type_map[tok.type], tok.value, tok.lineno, tok.lexpos)
    t_ignore = ' \t\r'
    def t_COMMENT(self, t):
        r'//.*'
        pass
    def t_newline(self, t):
        r'\n+'
        t.lexer.lineno += len(t.value)
    def t_HEX_NUMBER(self, t):
        r'0[xX][0-9a-fA-F]+'
        t.value = int(t.value, 16)
        t.type = 'NUMBER'  
        return t
    def t_BIN_NUMBER(self, t):
        r'0[bB][01]+'
        t.value = int(t.value, 2)
        t.type = 'NUMBER'
        return t
    def t_OCT_NUMBER(self, t):
        r'0[oO][0-7]+'
        t.value = int(t.value, 8)
        t.type = 'NUMBER'
        return t
    def t_NUMBER(self, t):
        r'\d+\.\d+|\d+'
        if '.' in t.value:
            t.value = float(t.value)
        else:
            t.value = int(t.value)
        return t
    def t_DICT_START(self, t):
        r'&\<'
        t.type = TokenType.DICT_START
        return t

    def t_DICT_END(self, t):
        r'>&'
        t.type = TokenType.DICT_END
        return t
    def t_FUNCTION_REF(self, t):
        r'&\(\?\)'
        t.type = TokenType.FUNCTION_REF  
        return t
    def t_COLON(self, t):
        r':'
        t.type = TokenType.COLON
        return t
    def t_DCOLON(self,t):
        r'##'
        t.type = 'DCOLON' 
        return t
    def t_OPERATOR(self,t):
        r'\.\.|##|\+=|-=|\*=|/=|%=|==|!=|<=|>=|<<|>>|\+\+|--|->|&&|\|\||::|=|<|>|\+|-|\*|/|%|!|&|\||\^|~|\?|@|:'
        if t.value == '##':
            t.value = '.'  
            t.type = 'OPERATOR'  
        return t
    def t_PUNCTUATOR(self, t):
        r'\(|\)|\{|\}|\[|\]|;|,|\.|:'
        return t
    def t_IDENTIFIER(self, t):
        r'[a-zA-Z_\u4e00-\u9fa5][a-zA-Z0-9_\u4e00-\u9fa5]*'
        return t
    def t_error(self, t):
        if self.unknown_char_warning_count < self.MAX_WARNINGS:
            print(f"警告: 未知字符 (ASCII: {ord(t.value[0])}) (行 {t.lineno}, 列 {t.lexpos})", file=sys.stderr)
            self.unknown_char_warning_count += 1
        elif self.unknown_char_warning_count == self.MAX_WARNINGS:
            print(f"警告: 未知字符警告已达上限，不再显示警告", file=sys.stderr)
            self.unknown_char_warning_count += 1
        t.lexer.skip(1)

def print_all_tokens(filename, prints=False):
    try:
        with open(filename, 'r', encoding='utf-8') as f:
            data = f.read()
        
        lexer = Lexer()
        lexer.input(data, filename)
        
        while True:
            token = lexer.token()
            if token.type == TokenType.EOF_TOKEN:
                break
                
            type_map = {
                TokenType.KEYWORD: "关键字",
                TokenType.IDENTIFIER: "标识符",
                TokenType.NUMBER: "数字",
                TokenType.STRING: "字符串",
                TokenType.OPERATOR: "运算符",
                TokenType.PUNCTUATOR: "标点符号",
                TokenType.EOF_TOKEN: "文件结束",
                TokenType.BOOLEAN: "布尔值",
                TokenType.REQUIRE: "引用",
                TokenType.DICT_START: "字典开始",
                TokenType.DICT_END: "字典结束",
                TokenType.COLON: "冒号",
                TokenType.FUNCTION_REF: "函数引用",
                TokenType.ANNOTATION: "注解",
                TokenType.DCOLON: "双冒号",
            }
            type_str = type_map.get(token.type, f"未知({token.type})")
            
            if prints:
                print(f"行 {token.line}, 列 {token.column} | {type_str} | 值: ", end='')
                if token.type == TokenType.STRING:
                    print(f'"{token.value}"')
                else:
                    print(token.value)
                    
    except Exception as e:
        print(f"发生错误: {str(e)}", file=sys.stderr)

if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(1)
    print_all_tokens(sys.argv[1], prints=True)