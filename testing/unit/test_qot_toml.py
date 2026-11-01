# -*- coding: utf-8 -*-
"""qot_toml 单元测试 —— 支持面之内要给对结果，支持面之外必须报错。

解析器只服务 builtins.toml 这一份清单：**静默误读比不支持危险得多**
（G-5 的教训），所以每个反例都断言抛 TomlError。
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TESTING = os.path.dirname(HERE)
ROOT = os.path.dirname(TESTING)
for p in (ROOT, TESTING):
    if p not in sys.path:
        sys.path.insert(0, p)

from qot_toml import TomlError, loads  # noqa: E402


class TestParserHappyPath(unittest.TestCase):
    def test_scalars(self):
        d = loads('a = 1\nb = -2\nc = true\nd = "x"\ne = \'y\'\n')
        self.assertEqual(d['a'], 1)
        self.assertEqual(d['b'], -2)
        self.assertTrue(d['c'] is True)
        self.assertEqual(d['d'], 'x')
        self.assertEqual(d['e'], 'y')

    def test_hex_int(self):
        self.assertEqual(loads('a = 0x1F')['a'], 31)

    def test_array_multiline_with_trailing_comma(self):
        d = loads('a = [\n  "x",\n  "y",\n]\n')
        self.assertEqual(d['a'], ['x', 'y'])

    def test_inline_table(self):
        d = loads('a = { k = "v", n = 2 }\n')
        self.assertEqual(d['a'], {'k': 'v', 'n': 2})

    def test_comment_after_value_and_hash_in_string(self):
        d = loads('a = "v#w"   # 行尾注释\nb = 2 # x\n')
        self.assertEqual(d['a'], 'v#w')
        self.assertEqual(d['b'], 2)

    def test_escapes(self):
        d = loads('a = "x\\ty\\nz"\n')
        self.assertEqual(d['a'], 'x\ty\nz')

    def test_table_and_array_of_tables(self):
        d = loads('[t]\nk = 1\n\n[[builtin]]\nname = "a"\n\n[[builtin]]\nname = "b"\n')
        self.assertEqual(d['t']['k'], 1)
        self.assertEqual([x['name'] for x in d['builtin']], ['a', 'b'])

    def test_bom_tolerated(self):
        self.assertEqual(loads('\ufeffa = 1')['a'], 1)


class TestParserRejects(unittest.TestCase):
    def _bad(self, text):
        with self.assertRaises(TomlError):
            loads(text)

    def test_float_unsupported(self):
        self._bad('a = 1.5')

    def test_duplicate_key(self):
        self._bad('a = 1\na = 2')

    def test_unclosed_array(self):
        self._bad('a = [1, 2')

    def test_unclosed_string(self):
        self._bad('a = "abc')

    def test_junk_line(self):
        self._bad('这不是 toml')

    def test_bad_table_header(self):
        self._bad('[table\na = 1')

    def test_error_has_line_number(self):
        try:
            loads('a = 1\nb = \nc = 3\n')
            self.fail('应当抛错')
        except TomlError as e:
            self.assertEqual(e.line, 2)


if __name__ == '__main__':
    unittest.main()
