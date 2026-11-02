import hashlib
import os
import re
import sys
from typing import List, Tuple, Set, Optional
class RequireInliner:
    def __init__(self, base_path: str = None):
        self.base_path = base_path or os.getcwd()
        self.processed_modules: Set[str] = set()
        self.processing_stack: List[str] = []
        self.module_functions: Set[str] = set()  
        self.module_variables: Set[str] = set() 
        # ---- QOT-CI L1（CP-C2）· 模块登记面（被动记录，不参与合并语义）----
        # modules：模块按首次处理序登记（name/path/content_hash/funcs/vars/
        # requires/required_by）；module_funcs_index：顶层函数名 → 模块名。
        # 只读消费方：qot_build 的 TU 切分归属（见 lir_cgen.generate_split）。
        self.modules: List[dict] = []
        self.module_funcs_index: dict = {}
        self._unit_stack: List[str] = []   # 当前处理单元（require 边的起点）
    
    def set_base_path(self, path: str):
        if os.path.isfile(path):
            self.base_path = os.path.dirname(os.path.abspath(path))
        else:
            self.base_path = os.path.abspath(path)
    
    def resolve_qot_path(self, module_name: str, current_file: str = None) -> Optional[str]:
        module_name = module_name.strip('"').strip("'")
        
        if module_name.lower().endswith('.dll'):
            return None
        
        candidates = [module_name]
        if not module_name.endswith('.qot'):
            candidates.append(module_name + '.qot')
        
        if current_file:
            current_dir = os.path.dirname(os.path.abspath(current_file))
            for cand in candidates[:]:
                candidates.append(os.path.join(current_dir, cand))
        
        if self.base_path:
            for cand in candidates[:]:
                candidates.append(os.path.join(self.base_path, cand))
        
        cwd = os.getcwd()
        for cand in candidates[:]:
            candidates.append(os.path.join(cwd, cand))
        
        seen = set()
        for cand in candidates:
            if cand in seen:
                continue
            seen.add(cand)
            if os.path.exists(cand) and os.path.isfile(cand) and cand.endswith('.qot'):
                return cand
        return None
    
    def read_qot_content(self, file_path: str) -> str:
        with open(file_path, 'r', encoding='utf-8') as f:
            return f.read()
    
    def extract_require_lines(self, content: str) -> List[Tuple[int, str, str]]:
        require_lines = []
        lines = content.split('\n')
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            if stripped.startswith('require ') and stripped.endswith(';'):
                match = re.search(r'require\s+["\']([^"\']+)["\']\s*;', stripped)
                if match:
                    require_lines.append((i, line, match.group(1)))
        return require_lines
    
    def extract_module_exports(self, content: str) -> Tuple[Set[str], Set[str]]:
        functions = set()
        variables = set()
        func_pattern = r'\w+_t\s+func\s+(\w+)\s*\('
        for match in re.finditer(func_pattern, content):
            functions.add(match.group(1))
        var_pattern = r'var\s+(\w+)\s*='
        for match in re.finditer(var_pattern, content):
            variables.add(match.group(1))
        const_pattern = r'const\s+(\w+)\s*='
        for match in re.finditer(const_pattern, content):
            variables.add(match.group(1))
        
        return functions, variables
    
    def replace_module_calls_multi(self, content: str,
                                   exports_by_module: dict) -> str:
        """【QOT-CI · 0】全部模块的 `mod##name → name` 改写**单遍**完成。

        与逐模块调用 replace_module_calls 语义一致（跳过字符串/注释，只改写
        代码位，成员必须在该模块导出集内），差异仅在病态输入（`xm00##f` 这类
        模块名作为更长单词后缀的形态）：原实现会从单词中部起匹配改写（本身
        是缺陷），本实现按整词匹配不改写。深 require 链的旧形态是
        O(N²·全文) 字符扫描（48 模块链实测 ~1.5s 且随 N 平方增长 —— 写大
        项目的直接障碍），单遍后为 O(全文 + N)。
        """
        if not exports_by_module:
            return content
        pat = re.compile(r'(\w+)##(\w+)')
        out = []
        i = 0
        n = len(content)
        state = 0  # 0=code 1=line_comment 2=block_comment 3=string
        while i < n:
            c = content[i]
            nxt = content[i + 1] if i + 1 < n else ''
            if state == 0:
                if c == '/' and nxt == '/':
                    state = 1
                    out.append(c)
                    i += 1
                    continue
                if c == '/' and nxt == '*':
                    state = 2
                    out.append(c)
                    i += 1
                    continue
                if c == '"':
                    state = 3
                    out.append(c)
                    i += 1
                    continue
                if c.isalnum() or c == '_':
                    m = pat.match(content, i)
                    if m:
                        mod, name = m.group(1), m.group(2)
                        names = exports_by_module.get(mod)
                        if names and name in names:
                            out.append(name)
                            i = m.end()
                            continue
                    # 普通词（或非导出成员的 mod##name）：原样吞掉整个词
                    j = i
                    while j < n and (content[j].isalnum() or content[j] == '_'
                                     or content[j] == '#'):
                        j += 1
                    out.append(content[i:j])
                    i = j
                    continue
                out.append(c)
                i += 1
                continue
            if state == 1:
                if c == '\n':
                    state = 0
                out.append(c)
                i += 1
                continue
            if state == 2:
                if c == '*' and nxt == '/':
                    out.append('*/')
                    i += 2
                    state = 0
                    continue
                out.append(c)
                i += 1
                continue
            # state == 3：字符串字面量
            if c == '\\':
                out.append(c)
                if nxt:
                    out.append(nxt)
                    i += 2
                else:
                    i += 1
                continue
            if c == '"':
                state = 0
            out.append(c)
            i += 1
        return ''.join(out)

    def replace_module_calls(self, content: str, module_name: str, functions: Set[str], variables: Set[str]) -> str:
        """`mod##name` → `name` 的改写（F0 · CP-3 加固）。

        v1 对全文做正则替换 —— 字符串字面量与注释里的 `mod##name` 也会被改写
        （标准库自举时立刻踩到：帮助文本/print 内容含 `##` 即静默改错程序）。
        v2 扫描时跳过字符串字面量（含转义）与 //、/* */ 注释，只改写代码位。
        """
        if not functions and not variables:
            return content
        names = set(functions) | set(variables)
        # 最长优先：防止 `get_path` 抢先吃掉 `get_path_x` 的前缀（名字集互为前缀时）
        pat = re.compile(re.escape(module_name) + r'##(\w+)')
        out = []
        i = 0
        n = len(content)
        state = 0  # 0=code 1=line_comment 2=block_comment 3=string 4=char?（QOT 无 char，仅串）
        while i < n:
            c = content[i]
            nxt = content[i + 1] if i + 1 < n else ''
            if state == 0:
                if c == '/' and nxt == '/':
                    state = 1
                    out.append(c)
                    i += 1
                    continue
                if c == '/' and nxt == '*':
                    state = 2
                    out.append(c)
                    i += 1
                    continue
                if c == '"':
                    state = 3
                    out.append(c)
                    i += 1
                    continue
                # 正常字符 + `mod##name` 匹配（i 恰在模块名起点时命中）
                m = pat.match(content, i)
                if m and m.group(1) in names:
                    out.append(m.group(1))
                    i = m.end()
                    continue
                out.append(c)
                i += 1
                continue
            if state == 1:  # 行注释
                if c == '\n':
                    state = 0
                out.append(c)
                i += 1
                continue
            if state == 2:  # 块注释
                if c == '*' and nxt == '/':
                    out.append('*/')
                    i += 2
                    state = 0
                    continue
                out.append(c)
                i += 1
                continue
            # state == 3：字符串字面量
            if c == '\\':
                out.append(c)
                if nxt:
                    out.append(nxt)
                    i += 2
                else:
                    i += 1
                continue
            if c == '"':
                state = 0
            out.append(c)
            i += 1
        return ''.join(out)
    
    def inline_module(self, module_path: str, current_file: str = None) -> Optional[Tuple[str, Set[str], Set[str]]]:
        full_path = self.resolve_qot_path(module_path, current_file)
        if full_path is None:
            return None
        
        abs_path = os.path.abspath(full_path)
        
        if abs_path in self.processing_stack:
            raise ImportError(f"循环依赖: {' -> '.join(self.processing_stack + [abs_path])}")
        
        if abs_path in self.processed_modules:
            return "", set(), set()
        
        self.processing_stack.append(abs_path)
        try:
            content = self.read_qot_content(abs_path)
            functions, variables = self.extract_module_exports(content)
            # QOT-CI L1 · 模块登记（被动；同模块重复 require 只登记一次）
            mod_name = os.path.splitext(os.path.basename(abs_path))[0]
            if not any(m['path'] == abs_path for m in self.modules):
                self.modules.append({
                    'name': mod_name,
                    'path': abs_path,
                    'content_hash': hashlib.sha256(
                        content.encode('utf-8')).hexdigest(),
                    'funcs': sorted(functions),
                    'vars': sorted(variables),
                    'requires': [],       # 本模块 require 的模块名
                    'required_by': [],    # require 本模块的单元名
                })
                for fn in functions:
                    # 重名防御：两个模块同名顶层函数 → 合并单元会在 parse 期
                    # 报"函数重定义"，这里只做显式登记（后者覆盖前者 + 标记）。
                    self.module_funcs_index[fn] = mod_name
            processed_content = self.process_content(content, abs_path)
            self.processed_modules.add(abs_path)
            
            return processed_content, functions, variables
        finally:
            self.processing_stack.pop()
    
    def _split_top_level_funcs(self, content: str):
        """把合并单元拆成（顶层函数块列表，其余顶层行列表）。稳定序。

        字符级扫描：跳过字符串字面量（含转义）与 //、/* */ 注释；顶层 =
        花括号深度 0。函数块 = `[_t 类型] func 名(...)` 头所在行起、到与之
        配对的深度归零行止；函数头之前**连续的**纯注释/空行行随函数一起
        前置（文档注释不许与函数失散）。
        """
        lines = content.split('\n')
        func_lines = []
        stmt_lines = []
        depth = 0
        state = 0  # 0=code 1=// 2=/* */ 3="..."
        cur = None           # 当前函数块的行累积（None = 不在函数块内）
        entered = False      # 函数体花括号已开启（配对判据）
        header_re = re.compile(r'^(?:\w+_t\s+)?func\s+\w+\s*\(')
        pending_comments = []  # 紧邻函数头之前的注释/空行
        for line in lines:
            stripped = line.strip()
            if cur is None:
                if depth == 0 and state == 0 and stripped and \
                        (stripped.startswith('//') or stripped == ''):
                    pending_comments.append(line)
                    continue
                if depth == 0 and state == 0 and header_re.match(stripped):
                    cur = list(pending_comments) + [line]
                    pending_comments = []
                    entered = False
                else:
                    stmt_lines.extend(pending_comments)
                    pending_comments = []
                    stmt_lines.append(line)
            else:
                cur.append(line)
            # 逐字符推进 depth/state（无论是否在函数块内都要走，字符串里
            # 的 `{`/`}` 不算结构）
            i = 0
            n = len(line)
            while i < n:
                c = line[i]
                nxt = line[i + 1] if i + 1 < n else ''
                if state == 0:
                    if c == '/' and nxt == '/':
                        state = 1
                        i += 2
                        continue
                    if c == '/' and nxt == '*':
                        state = 2
                        i += 2
                        continue
                    if c == '"':
                        state = 3
                        i += 1
                        continue
                    if c == '{':
                        depth += 1
                        if cur is not None and depth == 1:
                            entered = True
                    elif c == '}':
                        depth -= 1
                    i += 1
                    continue
                if state == 1:
                    break  # 行注释到行尾
                if state == 2:
                    if c == '*' and nxt == '/':
                        state = 0
                        i += 2
                        continue
                    i += 1
                    continue
                # state == 3：字符串
                if c == '\\':
                    i += 2
                    continue
                if c == '"':
                    state = 0
                i += 1
            if state == 1:
                state = 0
            # 函数块收口：体已开启且深度归零
            if cur is not None and entered and depth == 0:
                func_lines.extend(cur)
                cur = None
                entered = False
        if cur is not None:
            # 防御：未收口的函数块（括号不平衡 = 语法错，交给编译期报）
            func_lines.extend(cur)
        stmt_lines.extend(pending_comments)
        return func_lines, stmt_lines

    def _hoist_top_level_funcs(self, content: str) -> str:
        """顶层函数声明稳定前置（P1030-2）。

        interp 侧模块互调序无关（wrapper 全量安装 module_funcs）；native
        合并单元此前继承「先声明后用」限制 —— §8.13 的「std 库私有函数
        必须前置」纪律即其症状。前置后两侧同律：模块函数互调/前向调用
        均合法。仅在内联发生过时调用：无模块的源文件逐字节原样
        （诊断行号零迁移）。
        """
        func_lines, stmt_lines = self._split_top_level_funcs(content)
        if not func_lines:
            return content
        return '\n'.join(func_lines + stmt_lines)

    def process_content(self, content: str, current_file: str = None) -> str:
        require_lines = self.extract_require_lines(content)
        if not require_lines:
            return content
        # QOT-CI L1 · require 边登记（current_file 单元 → 被引模块）
        cur_name = None
        if current_file:
            cur_name = os.path.splitext(os.path.basename(current_file))[0]
        self._unit_stack.append(cur_name)
        try:
            return self._process_content_inner(content, current_file,
                                               require_lines, cur_name)
        finally:
            self._unit_stack.pop()

    def _process_content_inner(self, content: str, current_file: str,
                               require_lines: list, cur_name) -> str:
        lines = content.split('\n')
        for line_num, _, module_path in reversed(require_lines):
            if module_path.lower().endswith('.dll'):
                continue
            
            result = self.inline_module(module_path, current_file)
            if result is None:
                continue
            inline_code, functions, variables = result
            module_name = os.path.basename(module_path).replace('.qot', '').strip('"').strip("'")
            self._record_edge(cur_name, module_name)
            # 重复 require（菱形依赖：主程序与模块都 require 同一模块）：
            # inline_module 返回 ""（已处理去重）——require 行必须【置空】而不是
            # 原样保留，否则残余的 require 语句会被后续编译期当模块加载报错。
            lines[line_num - 1] = inline_code
        # 【QOT-CI · 0】`mod##name` 重写从"每层递归全量重扫"提升为
        # 【inline_file 的单次顶层扫描】（见其注释）——深 require 链的旧形态
        # 是 O(N³) 字符扫描（48 模块链实测 ~30s，写大项目的直接障碍）。
        return '\n'.join(lines)

    def _record_edge(self, src_name, dst_name):
        """QOT-CI L1 · 模块 DAG 边登记（去重；src 为 None 表示入口文件）。"""
        if not dst_name:
            return
        for m in self.modules:
            if m['name'] == dst_name:
                if src_name and src_name not in m['required_by']:
                    m['required_by'].append(src_name)
                src = next((x for x in self.modules if x['name'] == src_name), None)
                if src is not None and dst_name not in src['requires']:
                    src['requires'].append(dst_name)
                return

    def inline_file(self, file_path: str, output_path: str = None) -> str:
        self.set_base_path(file_path)
        self.processed_modules.clear()
        self.processing_stack.clear()
        self.modules = []
        self.module_funcs_index = {}
        self._unit_stack = []

        content = self.read_qot_content(file_path)
        # QOT-CI L1 · 主单元登记（名字保留原文件名；funcs/vars 供 TU 归属）
        main_name = os.path.splitext(os.path.basename(
            os.path.abspath(file_path)))[0]
        if not any(m['path'] == os.path.abspath(file_path) for m in self.modules):
            mfuncs, mvars = self.extract_module_exports(content)
            self.modules.insert(0, {
                'name': main_name,
                'path': os.path.abspath(file_path),
                'content_hash': hashlib.sha256(
                    content.encode('utf-8')).hexdigest(),
                'funcs': sorted(mfuncs),
                'vars': sorted(mvars),
                'requires': [],
                'required_by': [],
                'main': True,
            })
        processed = self.process_content(content, file_path)
        # 【QOT-CI · 0】`mod##name → name` 的改写统一在**顶层单遍**
        # 完成（原实现每层递归都对"当时已处理的模块集"重读文件 + 重提导出 +
        # 全文重扫 —— 各层的改写集合互为子集且模式互不相交，全部被顶层
        exports_by_module = {}
        for m in self.modules:
            if m.get('main'):
                continue
            exports_by_module[m['name']] = set(m['funcs']) | set(m['vars'])
        processed = self.replace_module_calls_multi(processed, exports_by_module)
        # 顶层函数前置（仅当真有模块被内联；无模块保持逐字节原样）
        if self.processed_modules:
            processed = self._hoist_top_level_funcs(processed)

        if output_path:
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(processed)

        return processed


def inline_requires(source_file: str, output_file: str = None) -> str:
    inliner = RequireInliner()
    return inliner.inline_file(source_file, output_file)


if __name__ == "__main__":
    sys.exit(1)