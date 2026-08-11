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
    
    def replace_module_calls(self, content: str, module_name: str, functions: Set[str], variables: Set[str]) -> str:
        result = content
        for name in functions:
            pattern = rf'{module_name}##{name}\s*\('
            result = re.sub(pattern, f'{name}(', result)
        for name in variables:
            pattern = rf'{module_name}##{name}\b'
            result = re.sub(pattern, name, result)
        
        return result
    
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
            processed_content = self.process_content(content, abs_path)
            self.processed_modules.add(abs_path)
            
            return processed_content, functions, variables
        finally:
            self.processing_stack.pop()
    
    def process_content(self, content: str, current_file: str = None) -> str:
        require_lines = self.extract_require_lines(content)
        if not require_lines:
            return content
        lines = content.split('\n')
        for line_num, _, module_path in reversed(require_lines):
            if module_path.lower().endswith('.dll'):
                continue
            
            result = self.inline_module(module_path, current_file)
            if result is None:
                continue
            inline_code, functions, variables = result
            module_name = os.path.basename(module_path).replace('.qot', '').strip('"').strip("'")
            if inline_code:
                lines[line_num - 1] = inline_code
        result = '\n'.join(lines)
        for abs_path in self.processed_modules:
            module_name = os.path.basename(abs_path).replace('.qot', '')
            module_content = self.read_qot_content(abs_path)
            functions, variables = self.extract_module_exports(module_content)
            result = self.replace_module_calls(result, module_name, functions, variables)
        
        return result
    
    def inline_file(self, file_path: str, output_path: str = None) -> str:
        self.set_base_path(file_path)
        self.processed_modules.clear()
        self.processing_stack.clear()
        
        content = self.read_qot_content(file_path)
        processed = self.process_content(content, file_path)
        
        if output_path:
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(processed)
        
        return processed


def inline_requires(source_file: str, output_file: str = None) -> str:
    inliner = RequireInliner()
    return inliner.inline_file(source_file, output_file)


if __name__ == "__main__":
    sys.exit(1)