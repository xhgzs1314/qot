import datetime
from functools import lru_cache
import io
import os
import subprocess
import sys
import time
import http.client
import turtle
import json
import types
from urllib.parse import urlparse, urlencode
import tkinter as tk
from tkinter import messagebox
from server import PHPLocalServer
from shellcode import ShellcodeLoader
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')
sys.stdin = io.TextIOWrapper(sys.stdin.buffer, encoding='utf-8')
is_running_in_ide = os.environ.get('SCRIPT_IDE_MODE', '0') == '1'
shellcode_instance_loader = ShellcodeLoader()
def http_naviat_request(url,params=None,method="GET",timeout=10):
    parsed_url = urlparse(url)
    method = method.strip().upper()
    if method not in ("GET", "POST"):
        return (None, None, f"不支持的请求方法: {method}")
    conn_class = http.client.HTTPSConnection if parsed_url.scheme == 'https' else http.client.HTTPConnection
    conn = conn_class(parsed_url.netloc, timeout=timeout)
    path = parsed_url.path or '/'
    query_elements = []
    if parsed_url.query:
        query_elements.append(parsed_url.query)
    if params and method == "GET":
        query_elements.append(urlencode(params))
    if query_elements:
        path += '?' + '&'.join(query_elements)
    try:
        headers = {"User-Agent": "Python http.client"}
        body = None
        if params and method == "POST":
            body = urlencode(params).encode('utf-8')
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        conn.request(method, path, body=body, headers=headers)
        response = conn.getresponse()
        content = response.read()
        status_code = response.status
        headers_dict = dict(response.getheaders())
        try:
            decoded_content = content.decode('utf-8')
            if 'content-type' in headers_dict and 'application/json' in headers_dict['content-type'].lower():
                return (status_code, headers_dict, json.loads(decoded_content))
            return (status_code, headers_dict, decoded_content)
        except UnicodeDecodeError:
            return (status_code, headers_dict, content)
        except json.JSONDecodeError:
            return (status_code, headers_dict, decoded_content)
    except Exception as e:
        return (None, None, f"请求发生错误: {str(e)}")
    finally:
        conn.close()
# ================================================================= F1/F2
# err() 错误对象与闭包值的解释器侧载体（定义在此而非 index.py：内置 handler
# 需要引用它们，而 builtin_func 被 index 导入 —— 反向导入会成环）。
# native 镜像 = qot_aot_runtime.h 的 QErr / QClosure。

class QotError:
    """err() 错误对象。定价与 native 一致：不参与值相等（== 恒 false）。"""
    __slots__ = ('payload',)
    def __init__(self, payload=None):
        self.payload = payload
    def __eq__(self, other):
        return False
    def __ne__(self, other):
        return True
    def __str__(self):
        return 'err(%s)' % (self.payload,)

class QotClosure:
    """闭包值：函数定义 + 创建点的自由变量环境（name → Box）。"""
    __slots__ = ('name', 'params', 'body', 'env', 'return_type', 'captured')
    def __init__(self, name, params, body, env, return_type, captured):
        self.name = name
        self.params = params
        self.body = body
        self.env = env
        self.return_type = return_type
        self.captured = captured
    def __str__(self):
        return 'closure'

# ================================================================= M4（0）
# 最小对象模型的解释器侧载体。native 镜像 = qot_aot_runtime.h 的
# QTypeDesc / QObj。定价与 native 逐条对齐：
#   * 字段 = 固定声明集（fields 之外读 null / 写忽略）；
#   * 方法缺席 → null；非对象接收者的 -> 走既有 receiver 前置调用；
#   * 相等 = 同一性（默认 __eq__ 不覆写）；
#   * tostring = "<类名>"（native qotrt_tostring 同律）。

class QotClass:
    """类描述：字段声明序 + 方法表 + 构造器（init）。"""
    __slots__ = ('name', 'fields', 'methods', 'init_params', 'init_body')
    def __init__(self, name, fields, methods, init_params=None, init_body=None):
        self.name = name
        self.fields = list(fields)              # 声明序字段名
        self.methods = methods                  # 方法名 → (params, body)
        self.init_params = init_params
        self.init_body = init_body

class QotObject:
    """实例：类 + 字段值（dict，按声明序初始化为 None）。"""
    __slots__ = ('cls', 'fields')
    def __init__(self, cls):
        self.cls = cls
        self.fields = {f: None for f in cls.fields}
    def __str__(self):
        return '<%s>' % self.cls.name


def format_value_for_output(val, indent=0, pretty=False):
    if val is None:
        return 'null'
    if isinstance(val, bool):
        return 'true' if val else 'false'
    if isinstance(val, QotError):
        return 'err(' + format_value_for_output(val.payload) + ')'
    if isinstance(val, QotClosure):
        return 'closure'
    if isinstance(val, QotObject):
        return '<%s>' % val.cls.name
    if isinstance(val, (int, float)):
        return str(val)
    if isinstance(val, bytes):
        hex_str = ' '.join(f'{b:02X}' for b in val)
        return f'<<{hex_str}>>'
    if isinstance(val, list):
        if not pretty:
            return '[' + ', '.join(format_value_for_output(item) for item in val) + ']'
        if not val:
            return '[]'
        indent_str = '  ' * indent
        next_indent = indent + 1
        items = [f'{indent_str}  {format_value_for_output(item, next_indent, pretty)}' for item in val]
        return '[\n' + ',\n'.join(items) + f'\n{indent_str}]'
    if isinstance(val, tuple):
        if not pretty:
            return '[' + '@'.join(format_value_for_output(item) for item in val) + ']'
        if not val:
            return '[]'
        indent_str = '  ' * indent
        next_indent = indent + 1
        items = [f'{indent_str}  {format_value_for_output(item, next_indent, pretty)}' for item in val]
        return '[\n' + '@\n'.join(items) + f'\n{indent_str}]'
    if isinstance(val, set):
        if not val:
            return '[::]'
        items = sorted(format_value_for_output(item) for item in val)
        return '[::' + '@'.join(items) + ']'
    if isinstance(val, range):
        start = val.start
        stop = val.stop - 1 if val.step == 1 else val.stop
        step = val.step
        if step == 1:
            return f'[{start}::{stop}]'
        else:
            return f'[{start}::{stop}:{step}]'
    if isinstance(val, frozenset):
        if not val:
            return '[:::frozen]'
        items = sorted(format_value_for_output(item) for item in val)
        return '[:::frozen@' + '@'.join(items) + ']'
    if isinstance(val, type):
        return f'[@::{val.__name__}]'
    if callable(val) and not isinstance(val, type):
        func_name = getattr(val, '__name__', str(val))
        return f'[::@{func_name}]'
    if isinstance(val, types.ModuleType):
        return f'[@@{val.__name__}]'
    if isinstance(val, types.GeneratorType):
        return f'[@::generator]'
    if isinstance(val, complex):
        return f'[{val.real}@{val.imag}i]'
    if val is Ellipsis:
        return '[::...]'
    if val is NotImplemented:
        return '[::NOT]'
    if isinstance(val, property):
        return f'[@::property]'
    if isinstance(val, staticmethod):
        return f'[@::static]'
    if isinstance(val, classmethod):
        return f'[@::class]'
    if isinstance(val, dict):
        if not pretty:
            items = [f'{format_value_for_output(k)}: {format_value_for_output(v)}' 
                    for k, v in val.items()]
            return '{' + ', '.join(items) + '}'
        if not val:
            return '{}'
        indent_str = '  ' * indent
        next_indent = indent + 1
        items = [f'{indent_str}  {format_value_for_output(k, next_indent, pretty)}: {format_value_for_output(v, next_indent, pretty)}' 
                for k, v in val.items()]
        return '{\n' + ',\n'.join(items) + f'\n{indent_str}}}'
    if hasattr(val, '__dict__') and not isinstance(val, (type, types.ModuleType)):
        return f'[QOT@@{val.__class__.__name__}]'
    return str(val)
class BuiltFunc:
    def __init__(self):
        self.functions = {}
        self.type_hints = {}
        self._register_builtin_functions()
        self.tkinter_events = {
        0: ('<Button-1>', '鼠标左键点击'),
        1: ('<Button-2>', '鼠标中键点击（或滚轮点击）'),
        2: ('<Button-3>', '鼠标右键点击'),
        3: ('<ButtonPress-1>', '鼠标左键按下'),
        4: ('<ButtonRelease-1>', '鼠标左键释放'),
        5: ('<Double-Button-1>', '鼠标左键双击'),
        6: ('<Enter>', '鼠标进入组件'),
        7: ('<Leave>', '鼠标离开组件'),
        8: ('<Motion>', '鼠标在组件上移动'),
        9: ('<B1-Motion>', '按住鼠标左键移动'),
        10: ('<MouseWheel>', '鼠标滚轮滚动（Windows系统）'),
        11: ('<Button-4>', '鼠标滚轮向上滚动（Linux系统）'),
        12: ('<Button-5>', '鼠标滚轮向下滚动（Linux系统）'),
        13: ('<Key>', '任意按键按下'),
        14: ('<KeyPress-A>', '按下A键（可替换为其他字母）'),
        15: ('<KeyRelease-A>', '释放A键（可替换为其他字母）'),
        16: ('<Return>', '按下回车键'),
        17: ('<BackSpace>', '按下退格键'),
        18: ('<Tab>', '按下Tab键'),
        19: ('<Escape>', '按下ESC键'),
        20: ('<Delete>', '按下删除键'),
        21: ('<Up>', '按下上方向键'),
        22: ('<Down>', '按下下方向键'),
        23: ('<Left>', '按下左方向键'),
        24: ('<Right>', '按下右方向键'),
        25: ('<FocusIn>', '组件获得焦点'),
        26: ('<FocusOut>', '组件失去焦点'),
        27: ('<Configure>', '组件大小或位置改变'),
        28: ('<Destroy>', '组件被销毁'),
        29: ('<Visibility>', '组件变为可见'),
        30: ('<Activate>', '组件被激活'),
        31: ('<Deactivate>', '组件被停用'),
        32: ('<Map>', '组件被映射到屏幕'),
        33: ('<Unmap>', '组件从屏幕上移除'),
        34: ('<Enter>', '按下Enter键（与鼠标进入事件相同，根据上下文区分）'),
        35: ('<Control-Key-a>', '按下Ctrl+A组合键'),
        36: ('<Shift-Key-A>', '按下Shift+A组合键'),
        37: ('<Alt-Key-F4>', '按下Alt+F4组合键')
    }
    
    def register_function(self, name, handler, type_hint):
        self.functions[name] = handler
        self.type_hints[name] = type_hint
    def _register_builtin_functions(self):
        def output_handler(*args):
            formatted = [format_value_for_output(arg) for arg in args]
            print(' '.join(formatted))
            return None
        def input_handler(*args):
            prompt = ' '.join(map(str, args)) if args else ''
            if(is_running_in_ide):
                return input(f"[INIDE]{prompt}")
            else:
                return input(prompt)
        def len_handler(*args):
            if not args:
                raise ValueError("glen()需要至少一个参数")
            arg = args[0]
            if not isinstance(arg, (str, list)):  
                raise TypeError(f"glen() 参数必须是字符串或数组，而非 {type(arg).__name__}")
            test_coloregg = args[1] if len(args) > 1 else 0
            return len(arg)+test_coloregg
        def str_exchange_handler(*args):
            if not args:
                raise ValueError("string()需要至少一个参数")
            arg = args[0]
            # 【P1030】布尔/null 渲染对齐 native 口径（CP-N4：oracle 向发布面
            # 对齐）：true/false/null 是语言字面量 —— str(True)='True' 是
            # Python 渗漏（D-07a 的 'True' 钉是 VM 裁判时代的记录，转向后
            # 由 native 口径取代；demo_csv_report 的 oracle 裁判实证该分歧）。
            # 数字渲染（'4' vs '4.000000'）维持 S-13 既定差异（oracle 裁判
            # token 归一），不在此扫荡。
            if arg is True:
                return 'true'
            if arg is False:
                return 'false'
            if arg is None:
                return 'null'
            return str(arg)
        def list_advice_append(*args):
            if not args:
                raise ValueError("push方法需要至少一个参数")
            arg = args[0]
            if not isinstance(arg, list):  
                raise TypeError(f"push方法参数必须是数组，而非 {type(arg).__name__}")
            arg.append(args[1])
            return True
        def list_advice_remove(*args):
            if not args:
                raise ValueError("remove方法需要至少一个参数")
            arg = args[0]
            if not isinstance(arg, list):  
                raise TypeError(f"remove方法参数必须是数组，而非 {type(arg).__name__}")
            if args[1] not in arg:
                raise ValueError(f"数组中不存在元素 {args[1]}")
            arg.remove(args[1])
            return True
        def list_advice_pop(*args):
            if not args:
                raise ValueError("pop方法需要至少一个参数")
            arg = args[0]
            if not isinstance(arg, list):  
                raise TypeError(f"pop方法参数必须是数组，而非 {type(arg).__name__}")
            if args[1] not in arg:
                raise ValueError(f"数组中不存在元素 {args[1]}")
            arg.pop(args[1])
            return True
        def list_advice_get(*args):
            if not args:
                raise ValueError("get方法需要至少一个参数")
            arg = args[0]
            if not isinstance(arg, list):  
                raise TypeError(f"get方法参数必须是数组，而非 {type(arg).__name__}")
            if args[1] not in arg:
                raise ValueError(f"数组中不存在元素 {args[1]}")
            return arg.index(args[1])
        def str_advice_split(*args):
            if not args:
                raise ValueError("part方法需要至少一个参数")
            arg = args[0]
            if not isinstance(arg, str):  
                raise TypeError(f"part方法参数必须是字符串，而非 {type(arg).__name__}")
            splitter = args[1] if len(args) > 1 else ","
            # 【S-11 终审 0】返回 list（清单 ret='array' 的既定语义）。
            # 旧实现返回生成器 —— 与自declared返回类型矛盾，且一次性消费后
            # 二次遍历静默为空。规范形态 = str_split（interp+native 双引擎）。
            return list(arg.split(splitter))
        def file_open_handler(*args):
            if not args:
                raise ValueError("fopen()需要至少一个参数")

            file_path = args[0]
            mode = args[1] if len(args) > 1 else 'r' 
            encoding = args[2] if len(args) > 2 and args[2] is not None else 'utf-8'

            if not os.path.exists(file_path):
                raise FileNotFoundError(f"文件 {file_path} 不存在")

            return open(file_path, mode, encoding=encoding)
        def file_read_handler(*args):
            if not args:
                raise ValueError("fread()需要至少一个参数")
            content = args[0].read()
            return content
        def file_close_handler(*args):
            if not args:
                raise ValueError("fclose()需要至少一个参数")
            args[0].close()
            return True
        def file_write_handler(*args):
            if len(args) < 2:
                raise ValueError("fwrite()需要至少两个参数：文件句柄和写入内容")
            file_handle, content = args[0], args[1]
            if not hasattr(file_handle, 'write'):
                raise ValueError("第一个参数必须是支持写入的文件对象")
            if hasattr(file_handle, 'mode'):
                if file_handle.mode == 'r' or ('r' in file_handle.mode and '+' not in file_handle.mode):
                    raise IOError(f"文件以只读模式（{file_handle.mode}）打开，无法写入")
            try:
                file_handle.write(content)
            except Exception as e:
                raise IOError(f"写入文件失败：{e}") from e
    
            return True
        def file_unlink_handler(*args):
            if not args:
                raise ValueError("unlink()需要至少一个参数")
            if not os.path.exists(args[0]):
                raise FileNotFoundError(f"文件 {args[0]} 不存在")
            os.unlink(args[0])
            return True
        def file_new_handler(*args):
            if not args:
                raise ValueError("new()需要至少一个参数")
            if os.path.exists(args[0]):
                raise FileExistsError(f"文件 {args[0]} 已存在")
            with open(args[0], 'w') as f:
                pass
            return True
        def file_size_get(*args):
            if not args:
                raise ValueError("fsize()需要至少一个参数")
            size = os.stat(args[0]).st_size
            if(len(args)>1 and args[1]!=None and args[1] != 0):
                size = size / (1024 ** args[1])
            return size
        def program_exit_handler():
            exit()
        def time_program_sleep(*args):
            if not args:
                raise ValueError("sleep()需要至少一个参数")
            time.sleep(args[0]/1000)
            return True
        def numbers_exchange_toint(*args):
            if not args:
                raise ValueError("ints()需要至少一个参数")
            try:
                return int(args[0])
            except ValueError:
                raise ValueError(f"无法将 {args[0]} 转换为整数")
        def numbers_exchange_tofloat(*args):
            if not args:
                raise ValueError("floats()需要至少一个参数")
            try:
                return float(args[0])
            except ValueError:
                raise ValueError(f"无法将 {args[0]} 转换为浮点数")
        def strings_exchange_tobool(*args):
            if not args:
                raise ValueError("bools()需要至少一个参数")
            true_values = ['true', 'True', 'TRUE']
            false_values = ['false', 'False', 'FALSE',""]
            if args[0] in true_values or args[0] > 0:
                return True
            elif args[0] in false_values or args[0] <= 0:
                return False
            else:
                raise ValueError(f"{args[0]}不是有效的布尔值")
        def others_exchange_tolists(*args):
            if not args:
                raise ValueError("lists()需要至少一个参数")
            if not isinstance(args[0], (str, bytes,types.GeneratorType)):
                raise TypeError(f"lists()的第一个参数必须是字符串或字节序列，而不是{type(args[0])}")
            return [item for item in args[0]]
        def list_batch_append(*args):
            if len(args) < 2:
                raise ValueError("batch_push需要至少2个参数（数组和元素列表）")
            arr = args[0]
            elements = args[1]
            if not isinstance(arr, list) or not isinstance(elements, list):
                raise TypeError("参数必须为数组")
            arr.extend(elements)  
            return True
        def types_search_dev(*args):
            if not args:
                raise ValueError("gtypes()需要至少一个参数")
            arg = args[0]
            tname = type(arg).__name__
            if tname == 'str':
                return 'string'
            elif tname == 'list':
                return 'array'
            elif tname == 'int' or tname == 'float':
                return 'number'
            elif tname == 'bool':
                # 【P1030-4】对齐 native 口径（CP-N4：oracle 向发布面对齐）：
                # native gtypes(布尔)='boolean'（qotrt_type_name），interp 原
                # 'any' 是 Python 类型面渗漏 —— 与 strings(bool) 的 'True'
                # 同族分歧，KNOWN-GTYPES-BOOL 随之收敛。
                return 'boolean'
            elif isinstance(arg, QotObject):
                # 【M4】对象：type/gtypes 同为 'object'（native qotrt_type 同律）
                return 'object'
            elif arg is None:
                return 'null'
            else:
                return 'any'
        def time_now_handler():
            return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        def time_timestamp():
            return int(time.time() * 1000)
        def math_sqrt_evolute(*args):
            if not args:
                raise ValueError("sqrt()需要至少一个参数")
            if(len(args) !=2):
                raise ValueError("sqrt()需要两个参数")
            x = args[0]
            n = args[1]
            precision=1e-10
            if x == 0:
                return 0.0
            if n <= 0:
                raise ValueError("方根次数n必须是正整数")
            negative = False
            if x < 0:
                if n % 2 == 0:
                    raise ValueError("负数不能计算偶次方根")
                negative = True
                x = -x  
            if x > 1:
                guess = x / n
            else:
                guess = 1.0
            while True:
                next_guess = ((n - 1) * guess + x / (guess ** (n - 1))) / n
                if abs(next_guess - guess) < precision:
                    break
                guess = next_guess
            return -next_guess if negative else next_guess
        def math_rand_handler(*args):
            n1,n2 = None,None
            n3 = 1
            if(len(args)>=2):
                n1 = args[0]
                n2 = args[1]
                if(len(args)==3):
                    n3 = args[2]
            if n1 is None and n2 is None:
                n1, n2 = 0, 10
            elif n1 is not None and n2 is not None:
                if n1 > n2:
                    n1, n2 = n2, n1
            else:
                raise ValueError("n1和n2必须同时提供或同时为None")
            seed = int(time.time() * 1000000) % 1000000
            def pseudo_random(seed):
                a = 1103515245
                c = 12345
                m = 2**31
                return (a * seed + c) % m
            rand_num = pseudo_random(seed)
            normalized = rand_num / (2**31 - 1)
            range_size = n2 - n1
            result = n1 + normalized * range_size
            if n3 == 1:
                return int(round(result))
            else:
                return result
        def math_number_max(*args):
            if not args:
                raise ValueError("max()需要至少一个参数")
            if isinstance(args[0], list):
                max_value = max(args[0])
            else:
                max_value = args[0]
                for i in range(1, len(args)):
                    if args[i] > max_value:
                        max_value = args[i]
            return max_value
        def math_number_min(*args):
            if not args:
                raise ValueError("min()需要至少一个参数")
            if isinstance(args[0], list):
                min_value = min(args[0])
            else:
                min_value = args[0]
                for i in range(1, len(args)):
                    if args[i] < min_value:
                        min_value = args[i]
            return min_value
        def math_number_power(*args):
            if not args:
                raise ValueError("pow()需要两个参数")
            base = args[0]
            exponent = args[1]
            if(isinstance(base,int) and isinstance(exponent,int)):
                return pow(base,exponent)
            else:
                raise ValueError("基数和指数必须都是整数")
        def math_number_sum(*args):
            if not args:
                raise ValueError("sum()需要至少一个参数")
            total = 0
            if isinstance(args[0], list):
                for num in args[0]:
                    total += num
            else:
                for i in range(1, len(args)):
                    total += args[i]
            return total
        def math_number_round(*args):
            if not args:
                raise ValueError("round()需要两个参数")
            number = args[0]
            places = args[1]
            if(isinstance(places,int)):
                return round(number,places)
            else:
                raise ValueError("保留小数位数必须都是整数")
        def str_trim_handler(*args):
            if not args:
                raise ValueError("beair()需要字符串参数")
            if not isinstance(args[0], str):
                raise TypeError("参数必须是字符串")
            return args[0].strip()
        def str_case_bigorsmall(*args):
            if not args:
                raise ValueError("strcase()需要字符串参数")
            if not isinstance(args[0], str):
                raise TypeError("参数必须是字符串")
            if(len(args)!=1 and args[1] != None):
                return args[0].lower()
            return args[0].upper()
        def str_replace_init(*args):
            if not args:
                raise ValueError("strrep()需要三个参数")
            if not isinstance(str(args[0]), str):
                raise TypeError("第一个参数必须是字符串")
            if not isinstance(str(args[1]), str):
                raise TypeError("第二个参数必须是字符串")
            if not isinstance(str(args[2]), str):
                raise TypeError("第三个参数必须是字符串")
            return str(args[0]).replace(str(args[1]), str(args[2]))
        def dict_get_handler(*args):
            if len(args) < 2:
                raise ValueError("dict_get()需要至少两个参数：字典和键")
            if not isinstance(args[0], dict):
                raise TypeError(f"dict_get()第一个参数必须是字典，而非{type(args[0]).__name__}")
            return args[0].get(args[1], None)

        def dict_set_handler(*args):
            if len(args) < 3:
                raise ValueError("dict_set()需要三个参数：字典、键和值")
            if not isinstance(args[0], dict):
                raise TypeError(f"dict_set()第一个参数必须是字典，而非{type(args[0]).__name__}")
            args[0][args[1]] = args[2]
            return True

        def dict_keys_handler(*args):
            if not args:
                raise ValueError("dict_keys()需要一个参数：字典")
            if not isinstance(args[0], dict):
                raise TypeError(f"dict_keys()参数必须是字典，而非{type(args[0]).__name__}")
            return list(args[0].keys())
        def http_sendpromise_handler(*args):
            if not args:
                raise ValueError("http_send()需要至少1个参数")
            if(len(args)<1):
                raise ValueError("http_send()至少需要1个参数")
            if(args[0] == None or args[0] == ''):
                raise ValueError("http_send()第一个参数不能为空")
            return http_naviat_request(*args)
        def window_tk_window_start(*args):
            return tk.Tk()
        def window_tk_window_title(*args):
            if not args:
                raise ValueError("window_title()需要至少2个参数")
            if(len(args)<2):
                raise ValueError("window_title()至少需要2个参数")
            if(args[0] == None or args[0] == ''):
                raise ValueError("window_title()第一个参数不能为空")
            if(args[1] == None or args[1] == ''):
                raise ValueError("window_title()第二个参数不能为空")
            args[0].title(args[1])
        def window_tk_window_size(*args):
            if not args:
                raise ValueError("window_size()需要至少2个参数")
            if(len(args)<2):
                raise ValueError("window_size()至少需要2个参数")
            if(args[0] == None or args[0] == ''):
                raise ValueError("window_size()第一个参数不能为空")
            if(args[1] == None or args[1] == ''):
                raise ValueError("window_size()第二个参数不能为空")
            args[0].geometry(args[1])
        def window_tk_window_loop(*args):
            if not args:
                raise ValueError("window_loop()需要一个参数")
            if(len(args)<1):
                raise ValueError("window_loop()至少需要1个参数")
            if(args[0] == None or args[0] == ''):
                raise ValueError("window_loop()第一个参数不能为空")
            args[0].mainloop()
        def window_tk_ments_moudle(title,args):
            args = list(args)
            if not args:
                raise ValueError(f"{title}()需要至少2个参数")
            if(len(args)<2):
                raise ValueError(f"{title}()至少需要2个参数")
            if(args[0] == None or args[0] == ''):
                raise ValueError(f"{title}()第一个参数不能为空")
            if(args[1] == None or args[1] == ''):
                raise ValueError(f"{title}()第二个参数不能为空")
            if not len(args) > 2:
                args.append('black')
            if not len(args) > 3:
                args.append(None)
            if not len(args) > 4:
                args.append(None)
            return args
        def window_tk_ment_button(*args):
            args = window_tk_ments_moudle("window_button",args)
            return tk.Button(args[0], text=args[1],bg=args[2],fg=args[3],font=args[4])
        def window_tk_ment_label(*args):
            args = window_tk_ments_moudle("window_label",args)
            return tk.Label(args[0], text=args[1], bg=args[2],fg=args[3],font=args[4])
        def window_tk_ment_entry(*args):
            args = window_tk_ments_moudle("window_entry",args)
            return tk.Entry(args[0], textvariable=args[1], bg=args[2],fg=args[3],font=args[4])
        def window_tk_grid_place(*args):
            if not args:
                raise ValueError("window_place()需要至少4个参数")
            if(len(args)<4):
                raise ValueError("window_place()至少需要4个参数")
            if(args[0] == None or args[0] == ''):
                raise ValueError("window_place()第一个参数不能为空")
            if(args[1] == None or args[1] == ''):
                raise ValueError("window_place()第二个参数不能为空")
            if(args[2] == None or args[2] == ''):
                raise ValueError("window_place()第三个参数不能为空")
            if(args[3] == None or args[3] == ''):
                raise ValueError("window_place()第四个参数不能为空")
            args[0].place(x=args[1], y=args[2], width=args[3], height=args[4])
        def window_tk_thing_bind(*args):
            if not args:
                raise ValueError("window_bind()需要至少3个参数")
            if len(args) < 3:
                raise ValueError("window_bind()至少需要3个参数")
            if args[0] is None or args[0] == '':
                raise ValueError("window_bind()第一个参数不能为空")
            if args[1] is None or args[1] == '':
                raise ValueError("window_bind()第二个参数不能为空")
            if(isinstance(args[1], str)):
                raise ValueError("window_bind()第二个参数不是有效的窗口事件")
            if(not int(args[1]) in self.tkinter_events):
                raise ValueError(f"无序列为{args[1]}的窗口事件")
            window_bind_things = self.tkinter_events[int(args[1])][0]
            callback = args[2]
            if isinstance(callback, tuple) and callback[0] == 'function_ref':
                func_name = callback[1]
                func_args = callback[2]
                def wrapper(e):
                    return self.call_function(func_name, *func_args)
                callback = wrapper
            elif not callable(callback):
                def wrapper(e):
                    return callback
                callback = wrapper
            args[0].bind(window_bind_things, lambda e: callback(e))
        def windoe_tk_thingbind_get(*args):
            if(len(args)<1):
                for event_id in self.tkinter_events:
                    print(f"{event_id}: {self.tkinter_events[event_id][1]}")
            else:
                return self.tkinter_events[int(args[0])][1]
        def window_entry_inputget(*args):
            if not args:
                raise ValueError("entry_get()需要一个参数")
            if(len(args)<1):
                raise ValueError("entry_get()至少需要1个参数")
            if(args[0] == None or args[0] == ''):
                raise ValueError("entry_get()第一个参数不能为空")
            return args[0].get()
        def window_msgbox_information(*args):
            if not args:
                raise ValueError("window_msg_info()需要至少2个参数")
            if(len(args)<2):
                raise ValueError("window_msg_info()至少需要2个参数")
            if(args[0] == None or args[0] == ''):
                raise ValueError("window_msg_info()第一个参数不能为空")
            if(args[1] == None or args[1] == ''):
                raise ValueError("window_msg_info()第二个参数不能为空")
            messagebox.showinfo(args[0], args[1])
        def window_compement_config(*args):
            if not args:
                raise ValueError("window_config()需要至少2个参数：组件和属性字典")
            if len(args) < 2:
                raise ValueError("window_config()至少需要2个参数：组件和属性字典")
            component = args[0]
            props = args[1]
    
            if component is None or (isinstance(component, str) and component.strip() == ''):
                raise ValueError("window_config()第一个参数（组件）不能为空")
            if props is None or not isinstance(props, dict):
                raise TypeError("window_config()第二个参数必须是字典类型")
            allowed_props = args[2] if len(args) > 2 and isinstance(args[2], (list, tuple)) else None
            if allowed_props is not None:
                filtered_props = {k: v for k, v in props.items() if k in allowed_props}
            else:
                filtered_props = props
            try:
                component.config(**filtered_props)
                return True
            except Exception as e:
                raise ValueError(f"设置组件属性失败: {str(e)}")
        def window_compement_config_hidden(*args):
            if not args:
                raise ValueError("window_visible()需要至少2个参数：组件和属性值")
            if len(args) < 2:
                raise ValueError("window_visible()至少需要2个参数：组件和属性值")
            component = args[0]
            props = args[1]
            if component is None or (isinstance(component, str) and component.strip() == ''):
                raise ValueError("window_visible()第一个参数（组件）不能为空")
            if props is None or not isinstance(props, int):
                raise TypeError("window_visible()第二个参数必须是属性值")
            if props == 0:
                component.place_forget()
            elif props == 1:
                component.place()
            elif props == 2:
                component.destroy()
            else:
                raise ValueError("window_visible()第二个参数必须是0-2之间的值")
        def cmd_outfnc_exec(*args):
            if not args:
                raise ValueError("cmd_exec()需要至少1个参数")
            if(len(args)<1):
                raise ValueError("cmd_exec()至少需要1个参数")
            if(args[0] == None or args[0] == ''):
                raise ValueError("cmd_exec()第一个参数不能为空")
            return subprocess.Popen(args[0], shell=True)
        def webapp_local_init(*args):
            return PHPLocalServer()
        def webapp_local_setbasepath(*args):
            if not args:
                raise ValueError("webloc_sbp()需要至少2个参数")
            if(len(args)<2):
                raise ValueError("webloc_sbp()至少需要2个参数")
            if(args[0] == None or args[0] == ''):
                raise ValueError("webloc_sbp()第一个参数不能为空")
            if(args[1] == None or args[1] == ''):
                raise ValueError("webloc_sbp()第二个参数不能为空")
            args[0].set_base_dir(args[1])
        def webapp_local_start(*args):
            if not args:
                raise ValueError("webloc_run()需要至少1个参数")
            if(len(args)<1):
                raise ValueError("webloc_run()至少需要1个参数")
            if(args[0] == None or args[0] == ''):
                raise ValueError("webloc_rn()第一个参数不能为空")
            args[0].start()
        def get_os_filepathnow(*args):
            if not args:
                raise ValueError("getpath()需要至少1个参数")
            if(len(args)<1):
                raise ValueError("getpath()至少需要1个参数")
            if(args[0] == None or args[0] == ''):
                raise ValueError("getpath()第一个参数不能为空")
            if(args[0] == 1):
                return os.getcwd()
            else:
                script_path = os.path.abspath(__file__)
                script_dir = os.path.dirname(script_path)
                return script_dir
        def utf_8_encode_handler(*args):
            if not args:
                raise ValueError("utf8_enc()需要至少1个参数")
            if(len(args)<1):
                raise ValueError("utf8_enc()至少需要1个参数")
            if(args[0] == None or args[0] == ''):
                raise ValueError("utf8_enc()第一个参数不能为空")
            return args[0].encode('utf-8')
        def utf_8_decode_handler(*args):
            if not args:
                raise ValueError("utf8_dec()需要至少1个参数")
            if(len(args)<1):
                raise ValueError("utf8_dec()至少需要1个参数")
            if(args[0] == None or args[0] == ''):
                raise ValueError("utf8_dec()第一个参数不能为空")
            return args[0].decode('utf-8')
        def shellcode_instance_make(*args):
            if not args:
                raise ValueError("Scload()需要至少1个参数")
            if(len(args)<1):
                raise ValueError("ScLoad()至少需要1个参数")
            if(args[0] == None or args[0] == ''):
                raise ValueError("ScLoad()第一个参数不能为空")
            return shellcode_instance_loader.load_shellcode(args[0])
        def shellcode_instance_exec(*args):
            a = None
            b = False
            c = False
            if not args:
                raise ValueError("ScExec()需要至少1个参数")
            if(len(args) < 1):
                raise ValueError("ScExec()至少需要1个参数")
            if(args[0] == None or args[0] == ''):
                print("ScExec()第一个参数不能为空")
                return False
            if len(args) >= 2 and args[1] is not None and args[1] != '':
                a = args[1]
            else:
                a = 5000  
            if len(args) >= 3 and args[2] is not None and args[2] != '':
                b = args[2]
            if len(args) >= 4 and args[3] is not None and args[3] != '':
                c = args[3]
            return shellcode_instance_loader.execute_shellcode(args[0], a,b,c)
        def shellcode_instance_cleanup(*args):
            if not args:
                raise ValueError("ScSweep()需要至少1个参数")
            if(len(args)<1):
                raise ValueError("ScSweep()至少需要1个参数")
            if(args[0] == None or args[0] == ''):
                print("ScSweep()第一个参数不能为空")
                return False
            return shellcode_instance_loader.cleanup_shellcode(args[0])
        def set_environment_variable(*args):
            if len(args) < 2:
                raise ValueError("set_env()需要至少2个参数：键和值")
            if args[0] is None or args[0] == '':
                raise ValueError("set_env()第一个参数（键）不能为空")
            key = str(args[0])
            value = str(args[1]) if args[1] is not None else ''
            os.environ[key] = value
            return True
        def get_environment_variable(*args):
            if len(args) < 1:
                raise ValueError("get_env()需要至少1个参数：键")
            if args[0] is None or args[0] == '':
                raise ValueError("get_env()第一个参数（键）不能为空")
            key = str(args[0])
            default = args[1] if len(args) > 1 else None
            return os.environ.get(key, default)

        def unset_environment_variable(*args):
            if len(args) < 1:
                raise ValueError("unset_env()需要至少1个参数：键")
            key = str(args[0])
            if key in os.environ:
                del os.environ[key]
            return True
        def __is_number(*args):
            if not args:
                return False
            return isinstance(args[0], (int, float))

        # ---- F2 错误对象元语（err/is_err/err_val）----
        def err_handler(*args):
            return QotError(args[0] if args else None)

        def is_err_handler(*args):
            if not args:
                return False
            return isinstance(args[0], QotError)

        def err_val_handler(*args):
            if not args or not isinstance(args[0], QotError):
                return None
            return args[0].payload

        def __iter_len(*args):
            if not args:
                return 0
            val = args[0]
            if isinstance(val, (int, float)):
                return max(0, int(val))
            elif isinstance(val, list):
                return len(val)
            elif isinstance(val, str):
                return len(val)
            else:
                return 0

        # 【M3 0】for-in 脱糖内部元语：dict → 键列表（插入序 ——
        # native 侧是槽序，D-JSON-ORDER 登记分歧族），其余透传。与
        # qotrt_for_keys 同律（dict_keys 物化一次，每 for 入口调用一次）。
        def __for_keys(*args):
            if not args:
                return None
            val = args[0]
            if isinstance(val, dict):
                return list(val.keys())
            return val
        def json_encode_handler(*args):
            if not args:
                raise ValueError("json_encode()需要至少一个参数")
            
            obj = args[0]
            indent = args[1] if len(args) > 1 and isinstance(args[1], int) else None
            ensure_ascii = args[2] if len(args) > 2 and isinstance(args[2], bool) else False
            try:
                converted_obj = _convert_to_json_serializable(obj)
                # 【0 · native json 对齐】QOT 数值是 **单一 f64 类型**
                # （没有 int/float 之分，native 侧 V16 只有 double）。Python 的
                # 1（int）与 1.0（float）会分别编码成 "1" / "1.0" —— 那是解释器
                # 的 Python 类型渗漏。规范形：整数值一律无小数（与 native
                # qotrt_json_num 逐位一致）。
                converted_obj = _json_canon_numbers(converted_obj)
                if indent is not None:
                    return json.dumps(converted_obj, ensure_ascii=ensure_ascii, indent=indent)
                else:
                    return json.dumps(converted_obj, ensure_ascii=ensure_ascii)
            except TypeError as e:
                raise TypeError(f"无法将对象转换为JSON: {str(e)}")
            except Exception as e:
                raise ValueError(f"JSON编码失败: {str(e)}")

        def json_decode_handler(*args):
            if not args:
                raise ValueError("json_decode()需要至少一个参数")
            
            json_str = args[0]
            
            if not isinstance(json_str, str):
                raise TypeError(f"json_decode()第一个参数必须是字符串，而非{type(json_str).__name__}")
            
            try:
                return json.loads(json_str)
            except json.JSONDecodeError as e:
                raise ValueError(f"JSON解码失败: {str(e)}")

        def _json_canon_numbers(obj):
            """整数值的 float 归一为 int（json 数字规范形 = native 侧口径）。"""
            if isinstance(obj, bool) or obj is None:
                return obj
            if isinstance(obj, float):
                if obj == int(obj) and abs(obj) < 9.2e18:
                    return int(obj)
                return obj
            if isinstance(obj, int):
                return obj
            if isinstance(obj, list):
                return [_json_canon_numbers(x) for x in obj]
            if isinstance(obj, dict):
                return {k: _json_canon_numbers(v) for k, v in obj.items()}
            return obj

        def _convert_to_json_serializable(obj):
            if obj is None:
                return None
            if isinstance(obj, (bool, int, float, str)):
                return obj
            if isinstance(obj, (list, tuple, set, frozenset)):
                return [_convert_to_json_serializable(item) for item in obj]
            if isinstance(obj, dict):
                return {_convert_to_json_serializable(k): _convert_to_json_serializable(v) 
                        for k, v in obj.items()}
            if isinstance(obj, bytes):
                return obj.hex()
            if isinstance(obj, datetime.datetime):
                return obj.strftime("%Y-%m-%d %H:%M:%S")
            if isinstance(obj, types.GeneratorType):
                return [_convert_to_json_serializable(item) for item in obj]
            if hasattr(obj, '__dict__'):
                return _convert_to_json_serializable(obj.__dict__)
            return str(obj)

        def json_pretty_handler(*args):
            if not args:
                raise ValueError("json_pretty()需要至少一个参数")
            
            obj = args[0]
            indent = args[1] if len(args) > 1 and isinstance(args[1], int) else 2
            
            return json_encode_handler(obj, indent, True)

        def json_validate_handler(*args):
        
            if not args:
                raise ValueError("json_valid()需要至少一个参数")
            
            json_str = args[0]
            
            if not isinstance(json_str, str):
                return False
            
            try:
                json.loads(json_str)
                return True
            except json.JSONDecodeError:
                return False

        # 实现注册表：name → 解释器实现。只登记【实现】，
        # 参数个数/类型/引擎归属/别名等全部元数据在 builtins.toml（M1 单一清单）。
        impls = {}
        impls['output'] = output_handler
        impls['cinget'] = input_handler
        impls['glen'] = len_handler
        impls['strings'] = str_exchange_handler
        impls['push'] = list_advice_append
        impls['remove'] = list_advice_remove
        impls['pop'] = list_advice_pop
        impls['get'] = list_advice_get
        impls['parts'] = str_advice_split
        impls['fopen'] = file_open_handler
        impls['fread'] = file_read_handler
        impls['fclose'] = file_close_handler
        impls['fwrite'] = file_write_handler
        impls['unlink'] = file_unlink_handler
        impls['fnew'] = file_new_handler
        impls['prog_end'] = program_exit_handler
        impls['sleep'] = time_program_sleep
        impls['ints'] = numbers_exchange_toint
        impls['floats'] = numbers_exchange_tofloat
        impls['bools'] = strings_exchange_tobool
        impls['lists'] = others_exchange_tolists
        impls['gtypes'] = types_search_dev
        impls['tnow'] = time_now_handler
        impls['tstamp'] = time_timestamp
        impls['fsize'] = file_size_get
        impls['sqrt'] = math_sqrt_evolute
        impls['rand'] = math_rand_handler
        impls['beair'] = str_trim_handler
        impls['strcase'] = str_case_bigorsmall
        impls['strrep'] = str_replace_init
        impls['max'] = math_number_max
        impls['min'] = math_number_min
        impls['pow'] = math_number_power
        impls['sum'] = math_number_sum
        impls['round'] = math_number_round
        impls['dict_get'] = dict_get_handler
        impls['dict_set'] = dict_set_handler
        impls['dict_keys'] = dict_keys_handler
        impls['http_send'] = http_sendpromise_handler
        impls['window_new'] = window_tk_window_start
        impls['window_title'] = window_tk_window_title
        impls['window_size'] = window_tk_window_size
        impls['window_loop'] = window_tk_window_loop
        impls['window_button'] = window_tk_ment_button
        impls['window_label'] = window_tk_ment_label
        impls['window_entry'] = window_tk_ment_entry
        impls['window_place'] = window_tk_grid_place
        impls['window_bind'] = window_tk_thing_bind
        impls['window_bind_get'] = windoe_tk_thingbind_get
        impls['entry_get'] = window_entry_inputget
        impls['window_msg_info'] = window_msgbox_information
        impls['window_config'] = window_compement_config
        impls['window_visible'] = window_compement_config_hidden
        impls['cmd_exec'] = cmd_outfnc_exec
        impls['webloc_init'] = webapp_local_init
        impls['webloc_sbp'] = webapp_local_setbasepath
        impls['webloc_run'] = webapp_local_start
        impls['getpath'] = get_os_filepathnow
        impls['batch_push'] = list_batch_append
        impls['utf8_enc'] = utf_8_encode_handler
        impls['utf8_dec'] = utf_8_decode_handler
        impls['ScLoad'] = shellcode_instance_make
        impls['ScExec'] = shellcode_instance_exec
        impls['ScSweep'] = shellcode_instance_cleanup
        impls['set_env'] = set_environment_variable
        impls['get_env'] = get_environment_variable
        impls['unset_env'] = unset_environment_variable
        impls['__is_number'] = __is_number
        impls['__iter_len'] = __iter_len
        impls['__for_keys'] = __for_keys
        impls['err'] = err_handler
        impls['is_err'] = is_err_handler
        impls['err_val'] = err_val_handler
        impls['json_encode'] = json_encode_handler
        impls['json_decode'] = json_decode_handler
        impls['json_pretty'] = json_pretty_handler
        impls['json_valid'] = json_validate_handler

        # P0.1 原语（CP-2 · primitives.py）：解释器 oracle 侧实现按模块聚拢，
        # 键集 = 清单新增条目集（_apply_builtin_manifest 的对账会机械验证
        # 「实现表 ↔ 清单」双向一致，多一个少一个名字都直接构建失败）。
        try:
            from primitives import PRIMITIVE_IMPLS as _prim_impls
            impls.update(_prim_impls)
        except ImportError as _e:  # pragma: no cover - 缺模块是安装级故障
            raise ImportError('primitives.py 缺失或损坏（P0.1 原语装载失败）: %s' % _e)

        # 登记表（functions / type_hints）与别名全部由 builtins.toml 派生：
        # 清单里没有的名字一律不进解释器表，反之实现表里有、清单里没有
        # 也会直接报错 —— 两侧再也不会各自漂移（G-5）。
        self._apply_builtin_manifest(impls)

    @staticmethod
    def _unsupported_stub(name, engines, hint):
        """清单声明「本引擎无实现」时的自动桩。

        为什么要登记它而不是干脆不登记：不登记的话语义分析只报
        「函数未定义」，把真正的病因（这个内置属于别的引擎）藏起来 ——
        这正是 G-5 那类分歧能长期存在的原因。现在解释器会明确说：
        它在清单里、属于哪些引擎、该怎么跑到那个引擎上。
        """
        def handler(*args):
            raise RuntimeError(
                "内置函数 %s() 在【解释器】引擎下没有实现：builtins.toml 登记"
                "的引擎是 [%s]。请用 VM/AOT 运行（index.py bstrust compile "
                "<源码> <目标.qtc> 后再 bstrust run）%s"
                % (name, '/'.join(engines), ('；' + hint) if hint else '。'))
        return handler

    def _apply_builtin_manifest(self, impls):
        """按 builtins.toml 生成解释器侧登记表（M1 唯一真相源）。

        注意这里遍历【全部条目】而不是只看 engines 含 interp 的：语义分析
        必须认识所有语言级内置名（含 VM/AOT 专属的那些），否则 `type()`
        这类调用会先在语义层被判「函数未定义」，根本走不到后端 —— 那就是
        G-5 的老症状。没实现的那些登记为明确报错的桩。
        """
        import qot_builtins as _bm
        for e in _bm.entries():
            name = e['name']
            if 'interp' not in e['engines'] or e['interp'] == _bm.STUB_UNSUPPORTED:
                handler = self._unsupported_stub(name, e['engines'], e['stub_hint'])
            else:
                if name not in impls:
                    # 构建失败而不是静默少一个内置（M1 验收门：故意删掉一侧
                    # 注册必须失败）。
                    raise _bm.BuiltinManifestError(
                        'builtins.toml 声明 %s 需要解释器实现 interp="%s"，'
                        '但实现表里没有它' % (name, e['interp']))
                handler = impls[name]
                # 清单里写死的 handler 名必须真的对得上：改了实现函数名却没
                # 同步清单 → 装载即失败（否则清单里的 interp 字段就是摆设）。
                if getattr(handler, '__name__', None) != e['interp']:
                    raise _bm.BuiltinManifestError(
                        '内置 %s 的实现对不上：builtins.toml 写 interp="%s"，'
                        '实际绑定的函数叫 %r'
                        % (name, e['interp'], getattr(handler, '__name__', None)))
            hint = (e['argc'], list(e['args']), e['ret'])
            self.register_function(name, handler, hint)
            for alias in e['aliases']:
                # 别名：M5 命名规范化的平滑迁移层（旧名仍是规范名）。
                # QOT_NO_ALIAS=1 时 canonical(alias) != name → 一个都不登记
                # （A/B 开关的归零验证见 _dbg_switches.py）。
                if _bm.canonical(alias) == name:
                    self.register_function(alias, handler, hint)
        _bm.check_interp_impls(impls, self.functions)

    def has_function(self, func_name):
        return func_name in self.functions
    
    def get_handler(self, func_name):
        if not self.has_function(func_name):
            raise ValueError(f"内置函数 '{func_name}' 未定义")
        return self.functions[func_name]
    
    def call_function(self, func_name, *args):
        try:
            handler = self.get_handler(func_name)
            return handler(*args)
        except Exception as e:
            print(f"调用内置函数 '{func_name}' 时发生错误: {e}")
            return None