import os
import tempfile
import subprocess
from typing import Any, Dict, List, Callable, Optional
class TypeHandler:
    @staticmethod
    def infer_type(value: str) -> str:
        if not value:
            return "any"
        value = value.strip()
        if value.startswith(("[", "{")) and value.endswith(("]", "}")):
            return "list" if value.startswith("[") else "dict"
        if value in ("True", "False"):
            return "bool"
        if value.startswith(('"', "'")) and value.endswith(('"', "'")):
            return "str"
        if "." in value:
            parts = value.split(".")
            if len(parts) == 2 and all(part.isdigit() for part in parts):
                return "float"
        if value.isdigit():
            return "int"
        return "any"

class FunctionConverter:

    def __init__(self):
        self._converters: Dict[str, Callable] = {
            'output': self._convert_print,
            'cinget': self._convert_input,
            'glen': self._convert_len,
            'max': self._convert_max,
            'pow': self._convert_pow,
            'prog_end': self._convert_exit
        }

    def convert(self, func_name: str, args: List[str], caller: Optional[str] = None) -> str:
  
        if func_name not in self._converters:
            raise ValueError(f"不支持的函数: {func_name}")
        return self._converters[func_name](args, caller)

    @staticmethod
    def _convert_print(args: List[str], _: Optional[str]) -> str:
        return f"print({', '.join(args)})"

    @staticmethod
    def _convert_input(args: List[str], _: Optional[str]) -> str:
        return f"input({', '.join(args)})" if args else "input()"

    @staticmethod
    def _convert_len(args: List[str], _: Optional[str]) -> str:
        return f"len({args[0]})" if args else "len()"

    @staticmethod
    def _convert_max(args: List[str], _: Optional[str]) -> str:
        return f"max({', '.join(args)})"

    @staticmethod
    def _convert_pow(args: List[str], _: Optional[str]) -> str:
        return f"pow({', '.join(args)})"

    @staticmethod
    def _convert_exit(args: List[str], _: Optional[str]) -> str:
        return f"exit({', '.join(args)})" if args else "exit()"

class PythonGenerator:
    
    def __init__(self):
        self.indent_level = 0
        self.code: List[str] = []
        self.variable_types: Dict[str, str] = {}
        self.func_converter = FunctionConverter()
        self.type_handler = TypeHandler()

    def _indent(self) -> str:
 
        return "    " * self.indent_level

    def add_line(self, line: str = "") -> None:

        if line.strip():
            self.code.append(f"{self._indent()}{line}")
        else:
            self.code.append("")

    def generate(self, ast: Any) -> None:
        self.add_line("# -*- coding: utf-8 -*-")
        self.visit(ast)

    def visit(self, node: Any) -> Any:
        
        if not node:
            return None

        node_type = node[0] if isinstance(node, tuple) else None
        
        visit_methods = {
            'start': self.visit_start,
            'var_declaration': self.visit_var_declaration,
            'const_declaration': self.visit_const_declaration,
            'assignment': self.visit_assignment,
            'literal': self.visit_literal,
            'identifier': self.visit_identifier,
            'array': self.visit_array,
            'array_declaration': self.visit_array_declaration,
            'dict': self.visit_dict,
            'dict_declaration': self.visit_dict_declaration,
            'array_index': self.visit_array_index,
            'function_call': self.visit_function_call,
            'function_call_statement': self.visit_function_call_statement,
            'while_statement': self.visit_while_statement,
            'if_statement': self.visit_if_statement,
            'binary_op': self.visit_binary_op,
            'for_statement': self.visit_for_statement
        }

        if isinstance(node, list):
            return self.visit_statement_list(node)
        if node_type in visit_methods:
            return visit_methods[node_type](node)
            
        raise ValueError(f"未支持的节点类型: {node_type}")


    def visit_start(self, node: tuple) -> None:
        _, statements = node
        self.visit_statement_list(statements)

    def visit_statement_list(self, statements: List[Any]) -> None:
        for stmt in statements:
            self.visit(stmt)

    def visit_var_declaration(self, node: tuple) -> None:
        _, var_name, expr = node
        value = self.visit(expr)
        var_type = self.type_handler.infer_type(value)
        self.variable_types[var_name] = var_type
        self.add_line(f"{var_name} = {value}")

    def visit_const_declaration(self, node: tuple) -> None:
        _, var_name, expr = node
        value = self.visit(expr)
        var_type = self.type_handler.infer_type(value)
        self.variable_types[var_name] = var_type
        self.add_line(f"{var_name.upper()} = {value}")

    def visit_assignment(self, node: tuple) -> None:
        _, var_name, expr = node
        if var_name not in self.variable_types:
            raise ValueError(f"未声明的变量: {var_name}")
        value = self.visit(expr)
        self.add_line(f"{var_name} = {value}")

    def visit_literal(self, node: tuple) -> str:
        _, value = node
        if isinstance(value, str):
            return f'"{value}"'
        elif isinstance(value, bool):
            return "True" if value else "False"
        return str(value)

    def visit_identifier(self, node: tuple) -> str:
        _, var_name = node
        return var_name

    def visit_array(self, node: tuple) -> str:
        _, elements = node
        elements_str = ", ".join([str(self.visit(elem)) for elem in elements])
        return f"[{elements_str}]"

    def visit_array_declaration(self, node: tuple) -> None:
        _, var_name, elements = node
        array_value = self.visit(('array', elements))
        self.variable_types[var_name] = "list"
        self.add_line(f"{var_name} = {array_value}")

    def visit_dict(self, node: tuple) -> str:
        _, entries = node
        entries_str = ", ".join([
            f"{self.visit(key)}: {self.visit(value)}"
            for key, value in entries
        ])
        return f"{{ {entries_str} }}"

    def visit_dict_declaration(self, node: tuple) -> None:
        _, var_name, entries = node
        dict_value = self.visit(('dict', entries))
        self.variable_types[var_name] = "dict"
        self.add_line(f"{var_name} = {dict_value}")

    def visit_array_index(self, node: tuple) -> str:
        _, arr_name, index_expr = node
        arr_var = self.visit_identifier(('identifier', arr_name))
        index = self.visit(index_expr)
        return f"{arr_var}[{index}]"

    def visit_function_call(self, node: tuple) -> str:
        _, func_name, args = node
        processed_args = [str(self.visit(arg)) for arg in args if arg is not None]
        return self.func_converter.convert(func_name, processed_args)

    def visit_function_call_statement(self, node: tuple) -> None:
        call_code = self.visit_function_call(node[1])
        self.add_line(call_code)

    def visit_while_statement(self, node: tuple) -> None:
        _, condition, body = node
        condition_code = self.visit(condition)
        self.add_line(f"while {condition_code}:")
        self.indent_level += 1
        self.visit(body)
        self.indent_level -= 1

    def visit_if_statement(self, node: tuple) -> None:
        _, condition, if_block, eif_blocks, else_block = node
        condition_code = self.visit(condition)
        self.add_line(f"if {condition_code}:")
        self.indent_level += 1
        self.visit(if_block)
        self.indent_level -= 1

        for eif in eif_blocks:
            eif_condition = self.visit(eif[1])
            self.add_line(f"elif {eif_condition}:")
            self.indent_level += 1
            self.visit(eif[2])
            self.indent_level -= 1

        if else_block:
            self.add_line("else:")
            self.indent_level += 1
            self.visit(else_block[1])
            self.indent_level -= 1

    def visit_binary_op(self, node: tuple) -> str:
        _, op, left, right = node
        left_code = self.visit(left)
        right_code = self.visit(right)
        return f"({left_code} {op} {right_code})"

    def visit_for_statement(self, node: tuple) -> None:
        _, var_name, range_expr, body = node
        range_code = self.visit(range_expr)
        range_node_type = range_expr[0] if isinstance(range_expr, tuple) else None

        if range_node_type in ('array', 'identifier'):
            self.add_line(f"for {var_name} in {range_code}:")
        else:
            self.add_line(f"for {var_name} in range({range_code}):")

        self.indent_level += 1
        self.visit(body)
        self.indent_level -= 1


def compile_and_run(ast: Any, icon_path: Optional[str] = None) -> None:
    generator = PythonGenerator()
    generator.generate(ast)
    py_code = "\n".join(generator.code)
    build_dir = os.path.join(os.getcwd(), "build")
    os.makedirs(build_dir, exist_ok=True)
    
    try:
        with tempfile.NamedTemporaryFile(
            mode='w',
            suffix='.py',
            prefix='qguoat_',
            dir=build_dir,
            delete=False,
            encoding='utf-8'
        ) as temp_file:
            temp_file.write(py_code)
            temp_file_name = temp_file.name
        print("脚本正在编译中!请耐心等待......")
        python_paths = [
            os.path.join(os.getcwd(), "env", "python", "python.exe"),
            os.path.join(os.getcwd(), "env", "bin", "python")
        ]
        python_path = next((p for p in python_paths if os.path.exists(p)), "python")
        cmd = [
            python_path,
            "-m", "PyInstaller",
            "--onefile",
            "--distpath", build_dir,
            "--workpath", os.path.join(build_dir, "build"),
            "--specpath", build_dir,
            "--log-level", "ERROR",  
            temp_file_name
        ]
        if icon_path and os.path.exists(icon_path):
            cmd.insert(4, f"--icon={icon_path}")
        
        result = subprocess.run(
            cmd,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        print(f"编译完成，exe文件已生成到: {build_dir}")

    except subprocess.CalledProcessError as e:
        print(f"编译失败: {e.stderr}")
    except Exception as e:
        print(f"发生错误: {str(e)}")
    finally:
        if 'temp_file_name' in locals():
            if os.path.exists(temp_file_name):
                os.remove(temp_file_name)
            spec_file = os.path.splitext(temp_file_name)[0] + ".spec"
            if os.path.exists(spec_file):
                os.remove(spec_file)
            work_dir = os.path.join(build_dir, "build")
            if os.path.exists(work_dir):
                import shutil
                try:
                    shutil.rmtree(work_dir)
                except Exception as e:
                    print(f"清理工作目录时出错: {str(e)}")