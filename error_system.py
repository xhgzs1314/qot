import sys
import traceback as py_traceback
from enum import IntEnum
from typing import Optional, Any, Dict, List, Tuple
from dataclasses import dataclass, field
from datetime import datetime
class ErrorCode(IntEnum):
    SUCCESS = 0                      # 成功，无错误

    # 语法错误 (1000-1099)
    ERR_SYNTAX_GENERAL = 1000        # 通用语法错误
    ERR_SYNTAX_MISSING_SEMICOLON = 1001  # 缺少分号 ';'
    ERR_SYNTAX_MISSING_PAREN = 1002      # 缺少括号 '()'
    ERR_SYNTAX_MISSING_BRACE = 1003      # 缺少花括号 '{}'
    ERR_SYNTAX_MISSING_BRACKET = 1004    # 缺少方括号 '[]'
    ERR_SYNTAX_UNEXPECTED_TOKEN = 1005   # 遇到意外的符号/关键字
    ERR_SYNTAX_INVALID_IDENTIFIER = 1006 # 标识符命名非法
    ERR_SYNTAX_INVALID_NUMBER = 1007     # 数字格式错误
    ERR_SYNTAX_UNTERMINATED_STRING = 1008 # 字符串未正确闭合

    # 运行时错误 (2000-2099)
    ERR_RUNTIME_GENERAL = 2000       # 通用运行时错误
    ERR_NAME_NOT_DEFINED = 2001      # 变量/函数名未定义
    ERR_TYPE_MISMATCH = 2002         # 类型不匹配
    ERR_INDEX_OUT_OF_RANGE = 2003    # 索引越界
    ERR_DIVISION_BY_ZERO = 2004      # 除零错误
    ERR_VALUE_ERROR = 2005           # 值错误
    ERR_CONST_MODIFICATION = 2006    # 试图修改常量
    ERR_RETURN_OUT_OF_FUNCTION = 2007 # 在函数外使用 return
    ERR_STOP_OUT_OF_LOOP = 2008      # 在循环外使用 break/continue
    ERR_INVALID_OPERATION = 2009     # 非法操作
    ERR_FUNCTION_ARGS_INVAILD = 2010 # 函数参数数量不匹配
    ERR_FUNCTION_NOTFOUND = 2011     # 调用不存在的函数
    ERR_FUNCTION_ARGS_TYPEINV = 2012 # 函数参数类型不匹配
    ERR_FUNCTION_NOTIT = 2013        # 对象不是函数
    ERR_FUNCTION_NOT_ISIN = 2014     # 函数已存在，无法重复注册
    ERR_FUNCTION_DEFINITION_FAILED = 2015  # 函数定义失败

    # 循环相关错误 (2020-2029) 
    ERR_FOR_RANGE_NEGATIVE = 2020    # for循环范围不能为负数 
    ERR_FOR_RANGE_INVALID = 2021     # for循环范围必须是数字或数组 
    ERR_LOOP_CONTROL_OUTSIDE = 2022  # 循环控制语句(stop/break/continue)在循环外使用

    # 类型系统错误 (2100-2199)
    ERR_TYPE_GENERAL = 2100          # 通用类型错误
    ERR_TYPE_NOT_CALLABLE = 2101     # 对象不可调用
    ERR_TYPE_NOT_ITERABLE = 2102     # 对象不可迭代
    ERR_TYPE_NOT_SUBSCRIPTABLE = 2103 # 对象不支持下标访问
    ERR_TYPE_INVALID_ARITHMETIC = 2104 # 不支持算术运算
    ERR_TYPE_INVALID_COMPARE = 2105   # 不支持比较运算
    ERR_TYPE_NOT_LIST_OR_DICT = 2106  # 只支持列表和字典类型 

    # I/O 错误 (3000-3099)
    ERR_IO_GENERAL = 3000            # 通用 I/O 错误
    ERR_FILE_NOT_FOUND = 3001        # 文件不存在
    ERR_MODULE_NOT_FOUND = 3002      # 模块/导入文件不存在
    ERR_MODULE_LOAD_FAILED = 3003    # 模块加载失败
    ERR_MODULE_CIRCULAR_DEP = 3004   # 循环导入依赖
    ERR_PERMISSION_DENIED = 3005     # 权限不足
    ERR_MODULE_SIGNATURE_INVALID = 3006  # 模块签名验证失败
    ERR_MODULE_EXPORT_TABLE_INVALID = 3007  # 导出表无效
    ERR_DLL_FUNCTION_NOT_FOUND = 3008      # DLL函数未找到
    ERR_DLL_LOAD_FAILED = 3009             # DLL加载失败
    ERR_DLL_SIGNATURE_FAILED = 3010        # DLL签名验证失败

    # 编译/检查错误 (4000-4099)
    ERR_COMPILE_GENERAL = 4000       # 通用编译错误
    ERR_COMPILE_TYPE_CHECK = 4001    # 静态类型检查失败
    ERR_COMPILE_INLINE_FAILED = 4002 # 内联编译失败
    ERR_COMPILE_DUPLICATE_FUNC = 4003 # 重复函数定义
    ERR_COMPILE_PARSE_FAILED = 4004   # AST解析失败
    ERR_COMPILE_QTC_FAILED = 4005     # QTC编译失败

    # 断言错误 (5000)
    ERR_ASSERTION_FAILED = 5000      # 断言失败

    # 系统错误 (9000-9099)
    ERR_SYSTEM_GENERAL = 9000        # 通用系统错误
    ERR_OUT_OF_MEMORY = 9001         # 内存不足
    ERR_STACK_OVERFLOW = 9002        # 调用栈溢出
    ERR_VM_INIT_FAILED = 9003        # VM初始化失败
    ERR_VM_EXECUTION_FAILED = 9004   # VM执行失败

    # 属性/成员访问错误
    ERR_ATTRIBUTE_NOT_FOUND = 2023      # 模块/对象没有指定成员
    ERR_OBJECT_NOT_CALLABLE = 2024      # 对象不可调用
    
    # 类型错误补充
    ERR_TYPE_NOT_MODULE_OR_DICT = 2107  # 不是模块或字典
    
    # 一元运算符错误
    ERR_UNARY_OP_UNKNOWN = 2025         # 未知一元运算符
    
    # 未知节点类型
    ERR_UNKNOWN_NODE_TYPE = 4006        # 未知AST节点类型


class ErrorLevel(IntEnum):
    DEBUG = 0      # 调试信息
    INFO = 1       # 信息
    WARNING = 2    # 警告
    ERROR = 3      # 错误
    FATAL = 4      # 致命错误


@dataclass
class ErrorFrame:
    function_name: str = ""
    file_name: str = ""
    line_number: int = 0
    column_number: int = 0
    source_line: str = ""
    frame_id: int = 0
    
    def to_dict(self) -> Dict:
        return {
            'function': self.function_name,
            'file': self.file_name,
            'line': self.line_number,
            'column': self.column_number,
            'source': self.source_line,
            'frame_id': self.frame_id
        }
    
    def __str__(self) -> str:
        return f"  at {self.function_name} ({self.file_name}:{self.line_number}:{self.column_number})"




class QOTError:
    
    def __init__(
        self,
        code: ErrorCode = ErrorCode.ERR_RUNTIME_GENERAL,
        message: str = "",
        level: ErrorLevel = ErrorLevel.ERROR,
        file_name: str = "",
        line_number: int = 0,
        column_number: int = 0,
        source_line: str = ""
    ):
        self.code = code
        self.message = message or self._get_default_message(code)
        self.level = level
        self.file_name = file_name
        self.line_number = line_number
        self.column_number = column_number
        self.source_line = source_line
        self.traceback: List[ErrorFrame] = []
        self.context: Dict[str, Any] = {}
        self.timestamp = datetime.now()
        self._id = id(self)
        
    def _get_default_message(self, code: ErrorCode) -> str:
        messages = {
            ErrorCode.ERR_SYNTAX_GENERAL: "语法错误",
            ErrorCode.ERR_SYNTAX_MISSING_SEMICOLON: "缺少分号 ';'",
            ErrorCode.ERR_SYNTAX_MISSING_PAREN: "缺少括号 '()'",
            ErrorCode.ERR_SYNTAX_MISSING_BRACE: "缺少花括号 '{}'",
            ErrorCode.ERR_SYNTAX_UNEXPECTED_TOKEN: "意外的符号",
            ErrorCode.ERR_NAME_NOT_DEFINED: "变量未定义",
            ErrorCode.ERR_TYPE_MISMATCH: "类型不匹配",
            ErrorCode.ERR_INDEX_OUT_OF_RANGE: "索引超出范围",
            ErrorCode.ERR_DIVISION_BY_ZERO: "除以零",
            ErrorCode.ERR_CONST_MODIFICATION: "不能修改常量",
            ErrorCode.ERR_FILE_NOT_FOUND: "文件不存在",
            ErrorCode.ERR_MODULE_NOT_FOUND: "模块不存在",
            ErrorCode.ERR_ASSERTION_FAILED: "断言失败",
        }
        return messages.get(code, "未知错误")
    
    def add_frame(self, frame: ErrorFrame) -> 'QOTError':
        self.traceback.append(frame)
        return self
    
    def set_context(self, key: str, value: Any) -> 'QOTError':
        self.context[key] = value
        return self
    
    def format(self, color: bool = True) -> str:
        lines = []
        level_str = {
            ErrorLevel.ERROR: "错误",
            ErrorLevel.FATAL: "致命错误",
            ErrorLevel.WARNING: "警告",
            ErrorLevel.INFO: "信息"
        }.get(self.level, "错误")
        
        code_str = f"[{level_str}] [{self.code.value}]"
        
        if color:
            code_str = f"\033[31m{code_str}\033[0m"  
        lines.append(code_str)
        
    
        if color:
            lines.append(f"\033[1m{self.message}\033[0m")
        else:
            lines.append(self.message)
        
        
        if self.file_name:
            location = f"  --> {self.file_name}:line({self.line_number})"
            lines.append(location)
        
    
        if self.source_line:
            lines.append(f"  |")
            lines.append(f"  | {self.source_line}")
            if self.column_number > 0:
                pointer = "  | " + " " * (self.column_number - 1) + "^"
                lines.append(pointer)
        
        
        if self.traceback:
            lines.append("")
            lines.append("堆栈跟踪:")
            for frame in reversed(self.traceback):
                lines.append(f"  {frame}")
        
        
        if self.context:
            lines.append("")
            lines.append("上下文:")
            for key, value in self.context.items():
                lines.append(f"  {key}: {value}")
        
        return "\n".join(lines)
    
    def to_dict(self) -> Dict:
        
        return {
            'code': self.code.value,
            'code_name': self.code.name,
            'message': self.message,
            'level': self.level.value,
            'level_name': self.level.name,
            'file': self.file_name,
            'line': self.line_number,
            'column': self.column_number,
            'source': self.source_line,
            'traceback': [f.to_dict() for f in self.traceback],
            'context': self.context,
            'timestamp': self.timestamp.isoformat(),
            'id': self._id
        }
    
    @classmethod
    def from_dict(cls, data: Dict) -> 'QOTError':
        
        error = cls(
            code=ErrorCode(data.get('code', 2000)),
            message=data.get('message', ''),
            level=ErrorLevel(data.get('level', 3)),
            file_name=data.get('file', ''),
            line_number=data.get('line', 0),
            column_number=data.get('column', 0),
            source_line=data.get('source', '')
        )
        for frame_data in data.get('traceback', []):
            frame = ErrorFrame(
                function_name=frame_data.get('function', ''),
                file_name=frame_data.get('file', ''),
                line_number=frame_data.get('line', 0),
                column_number=frame_data.get('column', 0),
                source_line=frame_data.get('source', ''),
                frame_id=frame_data.get('frame_id', 0)
            )
            error.add_frame(frame)
        error.context = data.get('context', {})
        return error




class ErrorCollector:
    
    def __init__(self):
        self.errors: List[QOTError] = []
        self.warnings: List[QOTError] = []
        self.max_errors: int = 100
        self._error_count = 0
        self._warning_count = 0
        self._fatal_occurred = False
    
    def add(self, error: QOTError) -> None:
     
        if error.level == ErrorLevel.FATAL:
            self._fatal_occurred = True
        elif error.level == ErrorLevel.WARNING:
            self.warnings.append(error)
            self._warning_count += 1
        else:
            self.errors.append(error)
            self._error_count += 1
        
    
        if len(self.errors) > self.max_errors:
            self.errors = self.errors[:self.max_errors]
    
    def has_errors(self) -> bool:
   
        return len(self.errors) > 0 or self._fatal_occurred
    
    def has_warnings(self) -> bool:
      
        return len(self.warnings) > 0
    
    def has_fatal(self) -> bool:
      
        return self._fatal_occurred
    
    def get_errors(self) -> List[QOTError]:
     
        return self.errors.copy()
    
    def get_warnings(self) -> List[QOTError]:
       
        return self.warnings.copy()
    
    def get_all(self) -> List[QOTError]:
        
        return self.errors + self.warnings
    
    def clear(self) -> None:
     
        self.errors.clear()
        self.warnings.clear()
        self._error_count = 0
        self._warning_count = 0
        self._fatal_occurred = False
    
    def print_all(self, color: bool = True) -> None:
        for warning in self.warnings:
            print(warning.format(color=color), file=sys.stderr)
        for error in self.errors:
            print(error.format(color=color), file=sys.stderr)
        
        if self.has_errors() or self.has_warnings():
            print(file=sys.stderr)
            print(f"诊断报告: {self._error_count} 错误, {self._warning_count} 警告", file=sys.stderr)
    
    def get_exit_code(self) -> int:
        if self.has_fatal():
            return 2
        if self.has_errors():
            return 1
        return 0
    
    def to_dict(self) -> Dict:
        return {
            'errors': [e.to_dict() for e in self.errors],
            'warnings': [w.to_dict() for w in self.warnings],
            'error_count': self._error_count,
            'warning_count': self._warning_count,
            'has_fatal': self._fatal_occurred
        }

class ErrorFactory:
    
    @staticmethod
    def name_error(var_name: str, file: str = "", line: int = 0, col: int = 0) -> QOTError:
        return QOTError(
            code=ErrorCode.ERR_NAME_NOT_DEFINED,
            message=f"变量 '{var_name}' 未定义",
            file_name=file,
            line_number=line,
            column_number=col
        )
    
    @staticmethod
    def type_error(expected: str, got: str, file: str = "", line: int = 0, col: int = 0) -> QOTError:
        return QOTError(
            code=ErrorCode.ERR_TYPE_MISMATCH,
            message=f"类型错误: 期望 {expected}, 实际得到 {got}",
            file_name=file,
            line_number=line,
            column_number=col
        )
    
    @staticmethod
    def index_error(index: int, size: int, file: str = "", line: int = 0, col: int = 0) -> QOTError:
        return QOTError(
            code=ErrorCode.ERR_INDEX_OUT_OF_RANGE,
            message=f"索引 {index} 超出范围 (长度 {size})",
            file_name=file,
            line_number=line,
            column_number=col
        )
    
    @staticmethod
    def syntax_error(msg: str, file: str = "", line: int = 0, col: int = 0) -> QOTError:
        return QOTError(
            code=ErrorCode.ERR_SYNTAX_GENERAL,
            message=msg,
            file_name=file,
            line_number=line,
            column_number=col
        )
    
    @staticmethod
    def zero_division_error(file: str = "", line: int = 0, col: int = 0) -> QOTError:
        return QOTError(
            code=ErrorCode.ERR_DIVISION_BY_ZERO,
            message="除以零",
            file_name=file,
            line_number=line,
            column_number=col
        )
    
    @staticmethod
    def const_error(var_name: str, file: str = "", line: int = 0, col: int = 0) -> QOTError:
        return QOTError(
            code=ErrorCode.ERR_CONST_MODIFICATION,
            message=f"不能修改常量 '{var_name}'",
            file_name=file,
            line_number=line,
            column_number=col
        )
    
    @staticmethod
    def file_not_found(path: str, file: str = "", line: int = 0, col: int = 0) -> QOTError:
        return QOTError(
            code=ErrorCode.ERR_FILE_NOT_FOUND,
            message=f"文件不存在: {path}",
            file_name=file,
            line_number=line,
            column_number=col
        )
    
    @staticmethod
    def assertion_error(msg: str, file: str = "", line: int = 0, col: int = 0) -> QOTError:
        return QOTError(
            code=ErrorCode.ERR_ASSERTION_FAILED,
            message=f"断言失败: {msg}",
            file_name=file,
            line_number=line,
            column_number=col
        )




class ErrorHandler:
    
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self.collector = ErrorCollector()
        self._current_source_file = ""
        self._current_function = ""
        self._callbacks: List[callable] = []
        self._quit_on_error = True
        self._color_output = True
    
    def set_source_file(self, filename: str) -> None:
       
        self._current_source_file = filename
    
    def set_current_function(self, func_name: str) -> None:
    
        self._current_function = func_name
    
    def add_callback(self, callback: callable) -> None:
     
        self._callbacks.append(callback)
    
    def set_quit_on_error(self, quit: bool) -> None:
        
        self._quit_on_error = quit
    
    def set_color_output(self, color: bool) -> None:
     
        self._color_output = color
    
    def _notify_callbacks(self, error: QOTError) -> None:
     
        for callback in self._callbacks:
            try:
                callback(error)
            except Exception:
                pass
    
    def handle(self, error: QOTError) -> None:
        if not error.file_name and self._current_source_file:
            error.file_name = self._current_source_file
        if self._current_function:
            frame = ErrorFrame(
                function_name=self._current_function,
                file_name=error.file_name,
                line_number=error.line_number,
                column_number=error.column_number
            )
            error.traceback.insert(0, frame)
        self.collector.add(error)
        self._notify_callbacks(error)
        if error.level == ErrorLevel.FATAL:
            print(error.format(color=self._color_output), file=sys.stderr)
            sys.exit(2)
        elif error.level == ErrorLevel.ERROR:
            print(error.format(color=self._color_output), file=sys.stderr)
            if self._quit_on_error:
                sys.exit(1)
        elif error.level == ErrorLevel.WARNING:
            if self._color_output:
                print(f"\033[33m{error.format(color=False)}\033[0m", file=sys.stderr)
            else:
                print(error.format(color=False), file=sys.stderr)
    
    def clear(self) -> None:
      
        self.collector.clear()
    
    def get_collector(self) -> ErrorCollector:
    
        return self.collector
    
    def print_summary(self) -> None:
      
        self.collector.print_all(color=self._color_output)




_error_handler = None

def get_error_handler() -> ErrorHandler:
    
    global _error_handler
    if _error_handler is None:
        _error_handler = ErrorHandler()
    return _error_handler




def error(code: ErrorCode, msg: str = "", file: str = "", line: int = 0, col: int = 0) -> None:
    handler = get_error_handler()
    err = QOTError(
        code=code,
        message=msg,
        file_name=file or handler._current_source_file,
        line_number=line,
        column_number=col
    )
    handler.handle(err)

def warning(msg: str, file: str = "", line: int = 0, col: int = 0) -> None:
    handler = get_error_handler()
    err = QOTError(
        code=ErrorCode.ERR_RUNTIME_GENERAL,
        message=msg,
        level=ErrorLevel.WARNING,
        file_name=file or handler._current_source_file,
        line_number=line,
        column_number=col
    )
    handler.handle(err)




def save_errors_to_file(filename: str, collector: ErrorCollector) -> bool:
    try:
        import json
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(collector.to_dict(), f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False

def load_errors_from_file(filename: str) -> Optional[ErrorCollector]:
    try:
        import json
        with open(filename, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        collector = ErrorCollector()
        for err_data in data.get('errors', []):
            collector.add(QOTError.from_dict(err_data))
        for warn_data in data.get('warnings', []):
            collector.add(QOTError.from_dict(warn_data))
        
        return collector
    except Exception:
        return None