# -*- coding: utf-8 -*-
"""P0.1 原语的 native ↔ 解释器 oracle 差分裁判（CP-2 · ）。

三件套纪律（docs/ §7.6 +  §8.9 纪律 1）的第三件：
每条原语的差分用例 = cases/native/p1x_prim_*.qot（golden 按 native 口径），
本测试把同一份 .qot 再喂给解释器（唯一语义对照面），做**零分歧**对账。

归一规则（S-13 教训：不许用"输出归一化"掩盖真实差异 —— 规则必须写死在
用例/裁判里，且唯一）。两侧数字输出口径天然不同（native 4.000000 vs
解释器 4），因此对每个输出行做 **token 级数字归一**：

    每个形如数字的 token → 若整数值且 |v| < 1e15 → '%d'，否则 '%.6f'

其余字符逐字节比较（字符串内容、布尔、null、容器括号形状全都不归一）。
已登记差异（显式差异登记制，CP-N4 口径）：REGISTERED 里每条必须带工单号；
当前为空 —— 空不是"没查"，是这批用例就设计在零分歧面上（dict 打印序、
mtime 绝对值等已知机器/实现相关面被用例本身规避，见各 .qot 头注）。
"""
import os
import re
import subprocess
import sys
import unittest

import json

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

PY = sys.executable
ART = os.path.join(HERE, '.artifacts', 'oracle_diff')

# 显式差异登记制：'(case, 行号从0)': ('工单', '原因')。
# 空表 = 本批用例全零分歧；新增登记必须同时给出不可消除的理由。
# 【M3 】for_in_unified 第 4 行（字典 for-in 键序）：native 槽序
# vs interp 插入序 —— 与 dict_keys / json 键序同一登记族（D-JSON-ORDER），
# 属两边容器数据结构的既定差异，非 for-in 语义分歧；需要稳定序的写法是
# dict_keys + arr_sort（用例与 README 均已注明）。
REGISTERED = {
    ('for_in_unified', 3): ('D-JSON-ORDER',
                            '字典 for-in 键序：native 开放寻址槽序 vs interp 插入序'),
    ('for_range_step', 11): ('D-JSON-ORDER',
                             '字典键倒序（by -1）：native 槽序的逆 vs interp 插入序的逆'),
}

NUM_TOKEN = re.compile(r'-?\d+\.\d+(?:[eE][+-]?\d+)?|-?\d+')


def _norm_token(m):
    t = m.group(0)
    try:
        v = float(t)
    except ValueError:
        return t
    # 【P1030-4 修】先按 native 口径 %.6f 舍入再判整 —— sqrt(2)^2 =
    # 2.0000000000000004，native 打 "2.000000"（%.6f 舍入成整），interp 打
    # 原始 repr → 旧归一得 "2" vs "2.000000" 假分歧。两侧统一：round6 后
    # 成整 → %d；否则 %.6f（浮点噪声在此口径下天然吸收，非"掩盖真实差异"
    # —— S-13 的数字 token 归一本就只对数值面）。
    v = round(v, 6)
    if abs(v - round(v)) < 1e-9 and abs(v) < 1e15:
        return '%d' % int(round(v))
    return '%.6f' % v


def norm_line(s):
    return NUM_TOKEN.sub(_norm_token, s.rstrip())


def norm_out(text):
    out = []
    for ln in (text or '').replace('\r\n', '\n').replace('\r', '\n').split('\n'):
        if ln.strip():
            out.append(norm_line(ln))
    return out


def _spawn(args, timeout=120):
    return subprocess.run(args, cwd=ROOT, capture_output=True, text=True,
                          encoding='utf-8', errors='replace', timeout=timeout)


CASES = ['p10_prim_str', 'p11_prim_num', 'p12_prim_arr', 'p13_prim_dict',
         'p14_prim_file', 'p15_prim_bytes', 'p16_prim_time', 'p17_prim_sys',
         'p18_prim_type',
         # 【P0-POOL-1 后补】标准库与端到端三例（§8.13 遗留：native 修复后
         # 入列）。std_text 差分此前被 P0-POOL-1 挂钉阻塞。
         'std_text', 'std_fs', 'demo_csv_report',
         # 【M5 】标准库二期：统计/分组 + 命令行解析（R15 .qot 自举）
         'std_data', 'std_cli',
         # 【P1030-2】模块内前向互调（module 作用域注册 + inliner 函数前置）
         'mod_forward',
         # 【P1030-3 · CP-4】FFI v0（真实 C 库：msvcrt sqrt/strlen/atoi）
         'p19_prim_ffi',
         # 【M7 · 】FFI ABI v2 冻结：混族 ffi_call_v（p19 修订）
         # + 原生插件 4 条（p21，demo 插件由 prerun 构建到 testing/.artifacts/）
         'p21_prim_plugin',
         # 【M5 尾 · 】P0-D 补：位运算 6 条（int64 域 + uint64 承载移位）
         'p22_prim_bit',
         # 【P1030-4 · P0.3】网络 18 条自环差分（TCP/UDP/DNS，全 hermetic）
         'p20_prim_net',
         'std_net',
         # 【G-04/M3 】for-in 区间/步长与迭代统一（字符串键序分歧
         # 见 REGISTERED 的 D-JSON-ORDER 条目）
         'for_range_step', 'for_in_unified',
         # 【M4 】最小对象模型（class/字段/方法/构造）
         'obj_model_basic',
         # 【GOD-SELF 】G1 协议元语 / G2 编译期宏 / 涌现验收
         #（双引擎共用同一份展开+绑定后 AST，零分歧由构造成立）
         'gs1_proto', 'gs2_macro', 'gs3_emergent']


class TestPrimitiveOracleDiff(unittest.TestCase):
    """native（AOT exe）与解释器 oracle 对同一用例零分歧（数字归一后）。"""

    @classmethod
    def setUpClass(cls):
        os.makedirs(ART, exist_ok=True)

    def _setup_procs(self, case):
        """case json 的 "setup"：后台命令（网络用例的本地服务器等）。
        【P1030-4】与 run.py 的 setup/teardown 同构。"""
        meta = os.path.join(ROOT, 'testing', 'cases', 'native', case + '.json')
        cmds = []
        if os.path.exists(meta):
            with open(meta, 'r', encoding='utf-8') as f:
                cmds = json.load(f).get('setup') or []
        procs = []
        for cmd in cmds:
            try:
                procs.append(subprocess.Popen(
                    cmd, cwd=ROOT, shell=True,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
            except Exception:
                break
        return procs

    @staticmethod
    def _teardown(procs):
        for p in procs or []:
            try:
                p.kill()
            except Exception:
                pass
        for p in procs or []:
            try:
                p.wait(timeout=5)
            except Exception:
                pass

    def _prerun(self, case):
        """case json 的 "prerun"：**同步**构建钩子（M7：插件 ABI 用例的
        C 插件编译）。与 run.py 的 _prerun 同构；失败即 fail。"""
        import json as _json
        meta = os.path.join(ROOT, 'testing', 'cases', 'native', case + '.json')
        cmds = []
        if os.path.exists(meta):
            with open(meta, 'r', encoding='utf-8') as f:
                cmds = _json.load(f).get('prerun') or []
        for cmd in cmds:
            p = subprocess.run(cmd, cwd=ROOT, shell=True, capture_output=True,
                               text=True, encoding='utf-8', errors='replace',
                               timeout=180)
            if p.returncode != 0:
                self.fail('%s prerun 失败(%s):\n%s' % (case, cmd,
                                                       (p.stderr or p.stdout or '')[-400:]))

    def _run_native(self, case, setup):
        qot = os.path.join(ROOT, 'testing', 'cases', 'native', case + '.qot')
        exe = os.path.join(ART, case + '.exe')
        p = _spawn([PY, 'index.py', 'aot', qot, exe])
        if not os.path.exists(exe):
            self.fail('AOT 编译失败 %s:\n%s' % (case, (p.stderr or p.stdout or '')[-800:]))
        procs = self._setup_procs(case) if setup else []
        try:
            return _spawn([exe], timeout=120)
        finally:
            self._teardown(procs)

    def _run_interp(self, case, setup):
        qot = os.path.join(ROOT, 'testing', 'cases', 'native', case + '.qot')
        procs = self._setup_procs(case) if setup else []
        try:
            return _spawn([PY, 'index.py', 'run', qot], timeout=120)
        finally:
            self._teardown(procs)

    def _needs_setup(self, case):
        meta = os.path.join(ROOT, 'testing', 'cases', 'native', case + '.json')
        if not os.path.exists(meta):
            return False
        with open(meta, 'r', encoding='utf-8') as f:
            return bool(json.load(f).get('setup'))

    def _check(self, case):
        self._prerun(case)
        setup = self._needs_setup(case)
        nat = self._run_native(case, setup)
        itr = self._run_interp(case, setup)
        self.assertEqual(nat.returncode, 0,
                         '%s: native 退出码 %s\n%s' % (case, nat.returncode, nat.stderr[-400:]))
        self.assertEqual(itr.returncode, 0,
                         '%s: 解释器退出码 %s\n%s' % (case, itr.returncode, itr.stderr[-400:]))
        a = norm_out(nat.stdout)
        b = norm_out(itr.stdout)
        self.assertEqual(len(a), len(b),
                         '%s: 行数 native=%d interp=%d\nnative=%r\ninterp=%r'
                         % (case, len(a), len(b), a[:8], b[:8]))
        for i, (x, y) in enumerate(zip(a, b)):
            if x != y:
                reg = REGISTERED.get((case, i))
                if reg:
                    continue  # 已登记差异（显式登记制）
                self.fail('%s 第 %d 行分歧:\n  native=%r\n  interp=%r'
                          % (case, i, x, y))

    def test_p10_prim_str(self):
        self._check('p10_prim_str')

    def test_p11_prim_num(self):
        self._check('p11_prim_num')

    def test_p12_prim_arr(self):
        self._check('p12_prim_arr')

    def test_p13_prim_dict(self):
        self._check('p13_prim_dict')

    def test_p14_prim_file(self):
        self._check('p14_prim_file')

    def test_p15_prim_bytes(self):
        self._check('p15_prim_bytes')

    def test_p16_prim_time(self):
        self._check('p16_prim_time')

    def test_p17_prim_sys(self):
        self._check('p17_prim_sys')

    def test_p18_prim_type(self):
        self._check('p18_prim_type')

    def test_std_text_oracle(self):
        """std.text 差分（P0-POOL-1 修复后从挂钉转正）。"""
        self._check('std_text')

    def test_std_fs_oracle(self):
        self._check('std_fs')

    def test_std_data_oracle(self):
        """M5：std.data（mean/median/stddev/group_*）两侧零分歧。"""
        self._check('std_data')

    def test_std_cli_oracle(self):
        """M5：std.cli（parse_flags/usage）两侧零分歧。"""
        self._check('std_cli')

    def test_demo_csv_report_oracle(self):
        """CP-3 端到端验收的 oracle 对账（CSV→统计→报表）。"""
        self._check('demo_csv_report')

    def test_mod_forward_oracle(self):
        """P1030-2：模块内前向互调（module 作用域注册 + inliner 前置）。"""
        self._check('mod_forward')

    def test_p19_prim_ffi_oracle(self):
        """P1030-3 · CP-4：FFI v0（msvcrt 真实 C 库）。【M7】v2 混族分派器
        修订：同族限制废除，ldexp(f64+i32) 混族真实调用。"""
        self._check('p19_prim_ffi')

    def test_p21_prim_plugin_oracle(self):
        """M7：原生插件 ABI 4 条（demo 插件双宿主：qotrt + ctypes 同一 DLL）。"""
        self._check('p21_prim_plugin')

    def test_p22_prim_bit_oracle(self):
        """P0-D 补：位运算 6 条（int64 域 + uint64 承载移位）。"""
        self._check('p22_prim_bit')

    def test_p20_prim_net_oracle(self):
        """P1030-4 · P0.3：网络 18 条自环差分（TCP/UDP/DNS）。"""
        self._check('p20_prim_net')

    def test_std_net_oracle(self):
        """P1030-4 · P0.3：std.net（setup 钩子起本地回显服务器）。"""
        self._check('std_net')

    def test_for_range_step_oracle(self):
        """G-04：for-in 区间/步长（A..B by S / E by S）两侧零分歧。"""
        self._check('for_range_step')

    def test_for_in_unified_oracle(self):
        """M3：for-in 统一（str/dict/null/bool/小数计数）—— dict 键序分歧
        按 REGISTERED 的 D-JSON-ORDER 登记条目跳过。"""
        self._check('for_in_unified')

    def test_obj_model_basic_oracle(self):
        """M4：最小对象模型（class/字段/方法/构造）两侧零分歧。"""
        self._check('obj_model_basic')

    def test_gs1_proto_oracle(self):
        """GOD-SELF-G1：协议元语（dunder 面）编译期静态绑定，两侧零分歧。"""
        self._check('gs1_proto')

    def test_gs2_macro_oracle(self):
        """GOD-SELF-G2：编译期宏（官方 swap!/unless! + 用户宏三形态 +
        嵌套），展开后双引擎逐字节同果。"""
        self._check('gs2_macro')

    def test_gs3_emergent_oracle(self):
        """GOD-SELF 涌现验收：纯 .qot 长出 C4 运算符重载 / C1 接口多态 /
        C6 引用语义（swap!）/ C3 yield! 原型 —— 零内核依赖。"""
        self._check('gs3_emergent')


class TestPrimitiveWiring(unittest.TestCase):
    """清单 ↔ 实现的静态对账（快检，不编译）。"""

    def test_manifest_names_match_impls(self):
        import qot_builtins as qb
        from primitives import PRIMITIVE_IMPLS
        names = {e['name'] for e in qb.entries()
                 if e['native'] and e['name'] not in
                 ('output', 'glen', 'gtypes', 'type', 'numbers', 'strings',
                  'push', 'dict_get', 'dict_set', 'dict_keys', 'err',
                  'is_err', 'err_val', '__is_number', '__iter_len',
                  '__for_keys', '__obj_new')}
        self.assertEqual(names, set(PRIMITIVE_IMPLS.keys()),
                         '清单 P0.1 原语名集与 primitives.py 登记表不一致')

    def test_interp_handler_names_match_manifest(self):
        import qot_builtins as qb
        from primitives import PRIMITIVE_IMPLS
        for e in qb.entries():
            if e['name'] in PRIMITIVE_IMPLS:
                self.assertEqual(getattr(PRIMITIVE_IMPLS[e['name']], '__name__', None),
                                 e['interp'],
                                 '%s 的实现 __name__ 与清单 interp 字段不一致' % e['name'])


if __name__ == '__main__':
    unittest.main()
