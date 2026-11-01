# -*- coding: utf-8 -*-
"""QOT 单元测试（L1）。用 unittest 编写，pytest 也能直接收集。

 T-11 / T-12。放在 testing/unit/ 下，由 testing/run.py --suite unit 调度。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))          # testing/unit
TESTING = os.path.dirname(HERE)
ROOT = os.path.dirname(TESTING)
for p in (ROOT, TESTING):
    if p not in sys.path:
        sys.path.insert(0, p)

import unittest  # noqa: E402

ART = os.path.join(TESTING, '.artifacts')


class OpcodeAlignmentTest(unittest.TestCase):
    """T-12：bytecode.py 与 vm_types.h 的 opcode 必须逐项同名同值。

    任一侧改动都会让这里变红 —— 这是  R6 的执行者。
    """

    PY_SRC = os.path.join(ROOT, 'bytecode.py')
    CPP_SRC = os.path.join(ROOT, 'ot-asset', 'c', 'vmcshit', 'vm_types.h')

    @staticmethod
    def _py_opcodes():
        import re
        src = open(OpcodeAlignmentTest.PY_SRC, encoding='utf-8').read()
        m = re.search(r'class VMInstruction:(.*?)\nclass ', src, re.S)
        body = m.group(1) if m else src
        out = {}
        for line in body.splitlines():
            mm = re.match(r'\s*([A-Z][A-Z0-9_]*)\s*=\s*(\d+)\s*(?:#.*)?$', line)
            if mm:
                out[mm.group(1)] = int(mm.group(2))
        return out

    @staticmethod
    def _cpp_opcodes():
        import re
        src = open(OpcodeAlignmentTest.CPP_SRC, encoding='utf-8').read()
        m = re.search(r'enum class OpCode\s*:\s*uint8_t\s*\{(.*?)\};', src, re.S)
        assert m, '在 vm_types.h 里找不到 enum class OpCode'
        out = {}
        for line in m.group(1).splitlines():
            line = line.split('//')[0].strip()
            if not line or line.startswith('/*'):
                continue
            mm = re.match(r'([A-Z][A-Z0-9_]*)\s*=\s*(\d+)\s*,?\s*$', line)
            if mm:
                out[mm.group(1)] = int(mm.group(2))
        return out

    def test_files_exist(self):
        self.assertTrue(os.path.exists(self.PY_SRC), self.PY_SRC)
        self.assertTrue(os.path.exists(self.CPP_SRC), self.CPP_SRC)

    def test_python_side_has_expected_ops(self):
        ops = self._py_opcodes()
        for must in ('PUSH_CONST', 'BINARY_OP', 'JUMP_IF_FALSE', 'CALL', 'MAKE_ARRAY',
                     'INDEX_GET', 'INDEX_SET', 'RETURN', 'STOP', 'REQUIRE'):
            self.assertIn(must, ops, 'bytecode.py 缺少 opcode %s' % must)

    def test_cpp_side_has_expected_ops(self):
        ops = self._cpp_opcodes()
        for must in ('PUSH_CONST', 'BINARY_OP', 'JUMP_IF_FALSE', 'CALL', 'MAKE_ARRAY',
                     'INDEX_GET', 'INDEX_SET', 'RETURN', 'STOP', 'REQUIRE'):
            self.assertIn(must, ops, 'vm_types.h 缺少 opcode %s' % must)

    def test_concrete_values_pinned(self):
        """钉住具体取值，防止解析器本身失效导致后续断言空跑。"""
        py = self._py_opcodes()
        cpp = self._cpp_opcodes()
        for name, val in (('NOP', 0), ('PUSH_CONST', 1), ('PUSH_VAR', 2), ('STORE_VAR', 3),
                          ('BINARY_OP', 10), ('CALL', 30), ('RETURN', 32), ('BREAK', 52)):
            self.assertEqual(val, py.get(name), 'bytecode.py %s 应为 %d，实际 %r' % (name, val, py.get(name)))
            self.assertEqual(val, cpp.get(name), 'vm_types.h %s 应为 %d，实际 %r' % (name, val, cpp.get(name)))

    def test_rpn_opcodes_are_retired_not_reused(self):
        """200/201 曾属 RPN_LOOP_START/END，随 RPN 删除（SPEC D-03）。

        号段必须保持空洞：旧 .qtc 里可能还残留这两个 op，一旦被新语义复用就会静默误解析。
        """
        py = self._py_opcodes()
        cpp = self._cpp_opcodes()
        for name in ('RPN_LOOP_START', 'RPN_LOOP_END'):
            self.assertNotIn(name, py, '%s 应已从 bytecode.py 删除' % name)
            self.assertNotIn(name, cpp, '%s 应已从 vm_types.h 删除' % name)
        for val in (200, 201):
            self.assertNotIn(val, py.values(), 'opcode %d 已冻结，禁止复用' % val)
            self.assertNotIn(val, cpp.values(), 'opcode %d 已冻结，禁止复用' % val)

    # F1/F2（）：闭包与异常的字节码 op。VM 已冻结（
    # §8.7：不加功能），这些 op 刻意【不进】vm_types.h —— VM 模式遇到即运行
    # 期报未知指令，闭包/try 的双实现 = 解释器 + AOT（§8.8.4 交付清单第 5 项
    # 的"别把 VM 拖进来"红线）。此清单即"显式差异登记制"的落点：VM 要支持
    # 任何一项，必须先解冻并在这里除名。
    VM_FROZEN_EXEMPT = {
        'ENV_NEW', 'MAKE_CLOSURE', 'CALL_VALUE',
        'TRY_PUSH', 'TRY_POP', 'CATCH_BIND', 'THROW',
        # 【M4 】对象字段与方法分派（同一条冻结红线：VM 不实现，
        # 双实现 = 解释器（AST 直达）+ AOT（bytecode→LIR→cgen））
        'FIELD_GET', 'FIELD_SET', 'CALL_OMETHOD',
    }

    def test_two_sides_agree(self):
        py = self._py_opcodes()
        cpp = self._cpp_opcodes()
        self.assertGreaterEqual(len(py), 20, '解析出的 Python opcode 太少: %d' % len(py))
        self.assertGreaterEqual(len(cpp), 20, '解析出的 C++ opcode 太少: %d' % len(cpp))
        only_py = sorted((set(py) - set(cpp)) - self.VM_FROZEN_EXEMPT)
        only_cpp = sorted(set(cpp) - set(py))
        self.assertEqual([], only_py, '只存在于 bytecode.py 的 opcode: %s' % only_py)
        self.assertEqual([], only_cpp, '只存在于 vm_types.h 的 opcode: %s' % only_cpp)
        diff = {k: (py[k], cpp[k]) for k in py
                if k not in self.VM_FROZEN_EXEMPT and py[k] != cpp.get(k)}
        self.assertEqual({}, diff, 'opcode 取值不一致: %s' % diff)


class QtcHeaderTest(unittest.TestCase):
    """T-11：.qtc 文件头与序列化格式的契约。

    SPEC R6：版本可 bump，但 loader 必须支持区间 [1,2]。
    v2（7.3-T-32 / 7.4-T-34/35/48）：删 funcsInfoLen 死字段；尾部扩展段
    （flags | builtinHints | lir）+ CRC32（覆盖其前全部字节）。
    """

    def test_magic_and_version(self):
        import struct
        p = self._compile_one()
        with open(p, 'rb') as f:
            magic = f.read(4)
            ver = struct.unpack('<I', f.read(4))[0]
        self.assertEqual(b'QTC\x00', magic)
        self.assertEqual(2, ver, '当前编译器应产出 .qtc v2（R6 版本化）')

    def test_v2_crc32_valid(self):
        import struct
        import zlib
        p = self._compile_one()
        with open(p, 'rb') as f:
            data = f.read()
        self.assertGreaterEqual(len(data), 8)
        stored = struct.unpack('<I', data[-4:])[0]
        self.assertEqual(zlib.crc32(data[:-4]) & 0xFFFFFFFF, stored,
                         'v2 文件尾部必须是合法 CRC32')

    def test_v2_ext_flags_and_hints(self):
        """扩展段 flags/builtinHints 必须可被独立解析（Python 侧对照）。"""
        import struct
        p = self._compile_one()
        with open(p, 'rb') as f:
            data = f.read()
        # 手工按 v2 布局走到 source 末尾（复用 index.py 的序列化器做参考实现）
        off = 8
        inst_count = struct.unpack_from('<I', data, off)[0]
        off += 4
        for _ in range(inst_count):
            off += 1
            arg_len = struct.unpack_from('<I', data, off)[0]
            off += 4 + arg_len
        consts_len = struct.unpack_from('<I', data, off)[0]
        off += 4 + consts_len
        vars_len = struct.unpack_from('<I', data, off)[0]
        off += 4 + vars_len
        funcs_len = struct.unpack_from('<I', data, off)[0]
        off += 4 + funcs_len
        source_len = struct.unpack_from('<I', data, off)[0]
        off += 4 + source_len
        # ext
        flags = struct.unpack_from('<I', data, off)[0]
        off += 4
        hints_len = struct.unpack_from('<I', data, off)[0]
        off += 4 + hints_len
        lir_len = struct.unpack_from('<I', data, off)[0]
        off += 4 + lir_len
        self.assertEqual(off + 4, len(data), '扩展段之后应只剩 4 字节 CRC')
        self.assertIn(flags, (0, 1), 'flags 当前只定义 bit0（AGGRESSIVE_OK）')

    def test_v1_still_loads(self):
        """R6：v1 文件（无 CRC、带 funcsInfoLen 死字段）必须仍可被现役 DLL 执行。"""
        import struct
        import subprocess
        from bytecode import Compiler
        from index import (_serialize_arg, _serialize_constants,
                           _serialize_string_list)
        from grammar import parse_oning_gram
        src = os.path.join(TESTING, 'cases', 'native', 't01_arith.qot')
        ast = parse_oning_gram(src, True)
        compiler = Compiler()
        compiler.set_base_path(src)
        code = compiler.compile_ast(ast)
        code.source_file = src
        # 手工拼 v1 布局（含 funcsInfoLen=0 死字段，无 CRC）
        body = bytearray()
        body += b'QTC\x00'
        body += struct.pack('<I', 1)          # version
        body += struct.pack('<I', 0)          # funcsInfoLen（死字段）
        body += struct.pack('<I', len(code.instructions))
        for op, arg in code.instructions:
            body += struct.pack('<B', op)
            a = _serialize_arg(arg)
            body += struct.pack('<I', len(a)) + a
        c = _serialize_constants(code.constants)
        body += struct.pack('<I', len(c)) + c
        v = _serialize_string_list(code.var_names)
        body += struct.pack('<I', len(v)) + v
        fn = _serialize_string_list(code.func_names)
        body += struct.pack('<I', len(fn)) + fn
        s = code.source_file.encode('utf-8')
        body += struct.pack('<I', len(s)) + s
        out = os.path.join(ART, '_unit_v1.qtc')
        with open(out, 'wb') as f:
            f.write(bytes(body))
        r = subprocess.run([sys.executable, 'index.py', 'bstrust', 'run', out],
                           cwd=ROOT, capture_output=True, text=True,
                           errors='replace', timeout=60)
        self.assertEqual(0, r.returncode, 'v1 文件应可运行: %s' % (r.stderr[-300:] if r.stderr else ''))
        self.assertIn('14.000000', r.stdout)

    def test_corrupted_v2_rejected(self):
        """损坏的 v2 文件（CRC 不符）必须被拒绝且退出非 0（7.3-T-32 验收）。"""
        import subprocess
        p = self._compile_one()
        with open(p, 'rb') as f:
            data = bytearray(f.read())
        # 篡改主体中部一个字节（保留 magic/version，破坏 CRC 校验）
        mid = len(data) // 2
        data[mid] ^= 0xFF
        bad = os.path.join(ART, '_unit_bad.qtc')
        with open(bad, 'wb') as f:
            f.write(bytes(data))
        r = subprocess.run([sys.executable, 'index.py', 'bstrust', 'run', bad],
                           cwd=ROOT, capture_output=True, text=True,
                           errors='replace', timeout=60)
        self.assertNotEqual(0, r.returncode, 'CRC 损坏文件必须运行失败')
        self.assertIn('CRC32', r.stderr, 'stderr 应指明 CRC 校验失败（loader 消息为 ASCII）')

    def test_bad_magic_rejected(self):
        import subprocess
        bad = os.path.join(ART, '_unit_badmagic.qtc')
        with open(bad, 'wb') as f:
            f.write(b'XXXX\x02\x00\x00\x00' + b'\x00' * 16)
        r = subprocess.run([sys.executable, 'index.py', 'bstrust', 'run', bad],
                           cwd=ROOT, capture_output=True, text=True,
                           errors='replace', timeout=60)
        self.assertNotEqual(0, r.returncode)
        self.assertIn('bad magic', r.stderr)

    def test_future_version_rejected(self):
        import struct
        import subprocess
        bad = os.path.join(ART, '_unit_future.qtc')
        with open(bad, 'wb') as f:
            f.write(b'QTC\x00' + struct.pack('<I', 99) + b'\x00' * 16)
        r = subprocess.run([sys.executable, 'index.py', 'bstrust', 'run', bad],
                           cwd=ROOT, capture_output=True, text=True,
                           errors='replace', timeout=60)
        self.assertNotEqual(0, r.returncode)
        self.assertIn('unsupported .qtc version', r.stderr)

    def test_aggressive_flag_semantics(self):
        """T-48 编译期判定：无 require → 置位；有 require 未内联 → 复位。"""
        import struct
        from index import _can_aggressive_optimize, _write_qtc
        from grammar import parse_oning_gram
        from bytecode import Compiler

        clean = os.path.join(ART, '_unit_flag_clean.qot')
        with open(clean, 'w', encoding='utf-8') as f:
            f.write('var a = 1;\noutput(a + 1);\n')
        ast = parse_oning_gram(clean, True)
        code = Compiler().compile_ast(ast)
        self.assertTrue(_can_aggressive_optimize(code))
        out = os.path.join(ART, '_unit_flag_clean.qtc')
        _write_qtc(code, out, aggressive=True)
        with open(out, 'rb') as f:
            data = f.read()
        # 第一个 ext 字段在文件尾部，直接解析 flags（借助上面的布局走查太长，这里独立解析）
        off = 8
        inst_count = struct.unpack_from('<I', data, off)[0]
        off += 4
        for _ in range(inst_count):
            off += 1
            arg_len = struct.unpack_from('<I', data, off)[0]
            off += 4 + arg_len
        for _ in range(3):  # consts, vars, funcs
            ln = struct.unpack_from('<I', data, off)[0]
            off += 4 + ln
        source_len = struct.unpack_from('<I', data, off)[0]
        off += 4 + source_len
        flags = struct.unpack_from('<I', data, off)[0]
        self.assertEqual(1, flags, '无 require 的程序应带 AGGRESSIVE_OK 位')

        # require 未内联：require 指令存在 → 不置位
        req = os.path.join(ART, '_unit_flag_req.qot')
        with open(req, 'w', encoding='utf-8') as f:
            f.write('require("nosuch.dll");\n')
        ast2 = parse_oning_gram(req, True)
        if ast2 and ast2 != 1:
            code2 = Compiler().compile_ast(ast2)
            # 语法上 require 是合法语句（加载期才失败），编译期必须检测到 REQUIRE 指令
            self.assertFalse(_can_aggressive_optimize(code2),
                             '存在 REQUIRE 指令时不得置位（负例）')

    @staticmethod
    def _compile_one():
        import subprocess
        os.makedirs(ART, exist_ok=True)
        src = os.path.join(TESTING, 'cases', 'native', 't01_arith.qot')
        out = os.path.join(ART, '_unit_t01.qtc')
        r = subprocess.run([sys.executable, 'index.py', 'bstrust', 'compile', src, out],
                           cwd=ROOT, capture_output=True, text=True)
        if not os.path.exists(out):
            raise AssertionError('编译失败: %s' % (r.stderr or r.stdout))
        return out


if __name__ == '__main__':
    unittest.main()
