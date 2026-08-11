import sys
import os
from error_system import (
    ErrorCode, ErrorLevel, QOTError,
    get_error_handler
)

_error_handler = get_error_handler()
_error_handler.set_color_output(True)
_error_handler.set_quit_on_error(False)

def _get_error_code(class_name: str) -> ErrorCode:
    codes = {
        'NameError': ErrorCode.ERR_NAME_NOT_DEFINED,
        'TypeError': ErrorCode.ERR_TYPE_MISMATCH,
        'IndexError': ErrorCode.ERR_INDEX_OUT_OF_RANGE,
        'ZeroDivisionError': ErrorCode.ERR_DIVISION_BY_ZERO,
        'ValueError': ErrorCode.ERR_VALUE_ERROR,
        'FileNotFoundError': ErrorCode.ERR_FILE_NOT_FOUND,
        'ImportError': ErrorCode.ERR_MODULE_NOT_FOUND,
        'SyntaxError': ErrorCode.ERR_SYNTAX_GENERAL,
    }
    return codes.get(class_name, ErrorCode.ERR_RUNTIME_GENERAL)

def qot_excepthook(exc_type, exc_value, exc_tb):
    try:
        error_msg = str(exc_value)
        file_name = ""
        line_num = 0
        
        if "-->" in error_msg:
            parts = error_msg.split("-->")
            if len(parts) > 1:
                location_part = parts[1].strip()
                import re
                match = re.search(r'([^:]+):(\d+)', location_part)
                if match:
                    file_name = match.group(1)
                    line_num = int(match.group(2))
                error_msg = parts[0].strip()
        
        qot_error = QOTError(
            code=_get_error_code(exc_type.__name__),
            message=error_msg,
            file_name=file_name,
            line_number=line_num,
            column_number=0  
        )
        
        _error_handler.collector.add(qot_error)
        print(qot_error.format(color=True), file=sys.stderr)
    except Exception as e:
        sys.__excepthook__(exc_type, exc_value, exc_tb)
    else:
        sys.exit(1)

sys.excepthook = qot_excepthook

def record_exception(e, error_msg, line=0):
    exc_type = e
    file_name = ""
    if "-->" in error_msg:
        parts = error_msg.split("-->")
        if len(parts) > 1:
            location_part = parts[1].strip()
            import re
            match = re.search(r'([^:]+):(\d+)', location_part)
            if match:
                file_name = match.group(1)
                line_num = int(match.group(2))
            error_msg = parts[0].strip()
    qot_error = QOTError(
        code=exc_type,
        message=str(error_msg),
        file_name=file_name,
        line_number=line,
        column_number=0  
    )

    _error_handler.collector.add(qot_error)
    print(qot_error.format(color=True), file=sys.stderr)
    sys.exit(1)
    return qot_error