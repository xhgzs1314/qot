"""QOT 统一测试入口   —    T-02

用法:
  python testing/run.py --suite all|unit|cli|syntax|sema|vm|interp|module|native|perf
  python testing/run.py --case testing/cases/native/t01_arith.qot
  python testing/run.py --suite native --case t08_dict      # 只跑一条（快检通道）

套件语义（ 转向后）：
  native  产品面套件：AOT 编译 + 跑，与 **native 口径 golden** 对账（期望源不再是 VM）
  vm      冻结回归（§8.7）：同一份用例集跑 VM，只许不回退、不再新增用例、不作差分期望源
  python testing/run.py --update-golden --reason "..."  [--suite ...]
  python testing/run.py --suite perf --repeat 7 --report testing/perf/report.md
  python testing/run.py --suite all --junit report.xml

退出码 ( 3.4):
  0  全绿
  1  有用例失败
  2  用例元数据缺失/非法
  3  环境缺失（qotrun.dll 不存在等）
"""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import platform
import re
import statistics
import subprocess
import sys
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

ARTIFACTS = os.path.join(HERE, '.artifacts')
GOLDEN = os.path.join(HERE, 'golden')
CASES = os.path.join(HERE, 'cases')
PERF = os.path.join(HERE, 'perf')
UNIT = os.path.join(HERE, 'unit')
GOLDEN_LOG = os.path.join(GOLDEN, 'CHANGES.log')

PY = sys.executable
DLL = os.path.join(ROOT, 'qotrun.dll')

ANSI_RE = re.compile(r'\x1b\[[0-9;]*[A-Za-z]')

# 套件 -> cases/ 下的子目录。
# ——native 是唯一产品形态，用例集归它所有。--suite vm 因此指向**同一目录**，
# 但语义已降级为「冻结回归」（ §8.7）：只许跑、不许新增用例，
# 也不再作 native 的差分期望源（期望源 = golden，见 run_native）。
#   * runs_on='native' 的新用例只进 native 套件，vm 套件显式跳过（不静默空跑）。
CASE_SUITES = {
    'syntax': 'syntax',
    'sema': 'sema',
    'vm': 'native',
    'native': 'native',
    'interp': 'interp',
    'module': 'module',
}
ALL_SUITES = ['unit', 'cli', 'syntax', 'sema', 'interp', 'module',
              'cpp', 'fuzz', 'native', 'perf', 'ci']

VM_ABANDONED = os.environ.get('QOT_VM_SUITE', '0') != '1'
if not VM_ABANDONED:
    ALL_SUITES.insert(4, 'vm')

DEFAULT_TIMEOUT = 60
PERF_TIMEOUT = 300

# fuzz 套件回归档（S-01）：固定种子保证可复现；深度档见 testing/fuzz/run_fuzz.py
FUZZ_SUITE_COUNT = 240
FUZZ_NATIVE_SAMPLE = 3
FUZZ_SUITE_SEED = 20261025

# ---------------------------------------------------------------- 工具

def normalize(text: str) -> list:
    """ 3.6：去 ANSI -> 统一换行 -> 去行尾空白 -> 丢弃纯空行。

    不做数字格式归一化（那是掩盖双运行时差异，见 R5）。
    """
    text = ANSI_RE.sub('', text or '')
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    lines = [ln.rstrip() for ln in text.split('\n')]
    return [ln for ln in lines if ln.strip()]


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def load_json(path: str):
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def git_commit() -> str:
    try:
        r = subprocess.run(['git', 'rev-parse', '--short', 'HEAD'],
                           cwd=ROOT, capture_output=True, text=True, timeout=10)
        return r.stdout.strip() or 'unknown'
    except Exception:
        return 'unknown'


# ---------------------------------------------------------------- 用例

class CaseError(Exception):
    """用例元数据缺失或非法 -> 退出码 2"""


class Case:
    REQUIRED = ('name', 'runs_on', 'expect', 'tags')

    def __init__(self, qot: str, meta: dict):
        self.qot = qot
        self.meta = meta
        for k in self.REQUIRED:
            if k not in meta:
                raise CaseError('%s: 元数据缺字段 %r' % (os.path.basename(qot), k))
        self.name = meta['name']
        self.runs_on = meta['runs_on']
        # 【 转向】'native' 入列（SPEC R5 的 runs_on 口径）：
        # native 是唯一产品形态，native 口径的定价钉子必须能只跑 AOT。
        if self.runs_on not in ('interp', 'vm', 'native', 'both', 'none'):
            raise CaseError('%s: runs_on 非法: %r' % (self.name, self.runs_on))
        self.expect = meta.get('expect', {})
        self.compile_cfg = meta.get('compile', {})
        self.tags = meta.get('tags', [])
        self.note = meta.get('note', '')

    @property
    def golden_path(self) -> str:
        return os.path.join(GOLDEN, self.name + '.out')

    def artifact(self) -> str:
        return os.path.join(ARTIFACTS, self.name + '.qtc')


def discover_cases(suite: str) -> list:
    sub = CASE_SUITES[suite]
    d = os.path.join(CASES, sub)
    if not os.path.isdir(d):
        return []
    out = []
    for fn in sorted(os.listdir(d)):
        if not fn.endswith('.qot'):
            continue
        qot = os.path.join(d, fn)
        meta_path = qot[:-4] + '.json'
        if not os.path.exists(meta_path):
            raise CaseError('用例缺少元数据文件: %s' % meta_path)
        case = Case(qot, load_json(meta_path))
        # 冻结回归不收 native-only 用例（ §8.7）：VM 已冻结，
        # native 口径的定价钉子对它没有判据意义，收进来只会制造空对比/假失败。
        if suite == 'vm' and case.runs_on == 'native':
            continue
        out.append(case)
    return out


# ---------------------------------------------------------------- 执行

def _spawn(args, timeout):
    return subprocess.run(args, cwd=ROOT, capture_output=True, text=True,
                          encoding='utf-8', errors='replace', timeout=timeout)


def do_compile(case: Case, timeout=DEFAULT_TIMEOUT):
    """返回 (ok, proc)。ok = 是否成功产出 .qtc"""
    art = case.artifact()
    if os.path.exists(art):
        os.remove(art)
    args = [PY, 'index.py', 'bstrust', 'compile', case.qot, art]
    args += list(case.compile_cfg.get('args', []))
    p = _spawn(args, timeout)
    return os.path.exists(art), p


def do_run_vm(qtc: str, timeout=DEFAULT_TIMEOUT):
    return _spawn([PY, 'index.py', 'bstrust', 'run', qtc], timeout)


def do_run_interp(qot: str, timeout=DEFAULT_TIMEOUT):
    return _spawn([PY, 'index.py', 'run', qot], timeout)


class Result:
    def __init__(self, name, status, detail='', expected=None, actual=None):
        self.name = name
        self.status = status          # OK / FAIL / SKIP / NEW
        self.detail = detail
        self.expected = expected
        self.actual = actual


def _start_setup(case: Case):
    """case json 顶层 "setup": [shell 命令] —— 后台进程（网络用例的本地
    服务器等）。【P1030-4】与 oracle 裁判的 setup/teardown 同构；失败即止
    （已启的由调用方 finally 清理）。"""
    procs = []
    for cmd in case.meta.get('setup') or []:
        try:
            procs.append(subprocess.Popen(
                cmd, cwd=ROOT, shell=True,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        except Exception:
            break
    return procs


def _stop_setup(procs):
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


def _prerun(case: Case):
    """case json 顶层 "prerun": [shell 命令] —— **同步**构建钩子（M7：插件
    ABI 用例的 C 插件编译等）。与 setup（后台服务）互补：阻塞至命令退出，
    非零退出 → 返回错误文本（调用方判 FAIL）。【M7 · 】"""
    for cmd in case.meta.get('prerun') or []:
        try:
            p = subprocess.run(cmd, cwd=ROOT, shell=True, capture_output=True,
                               text=True, encoding='utf-8', errors='replace',
                               timeout=180)
        except Exception as e:
            return 'prerun 执行异常: %s' % e
        if p.returncode != 0:
            return 'prerun 失败(%s): %s' % (cmd, (p.stderr or p.stdout or '')[-400:])
    return None


def run_case(case: Case, update_golden: bool, suite: str = '') -> Result:
    if suite == 'vm' and case.runs_on == 'native':
        # 冻结回归不跑 native-only 用例：VM 已冻结（§8.7），native 口径的定价
        # 钉子对它没有意义；不跳过会拿空输出去比 golden，制造假失败。
        return Result(case.name, 'SKIP',
                      detail='native-only 用例；--suite vm 是冻结回归，不执行')
    procs = _start_setup(case)
    try:
        res = _run_case_inner(case, update_golden)
    finally:
        _stop_setup(procs)
    xfail = case.expect.get('xfail')
    if xfail:
        ticket = case.expect.get('ticket', '-')
        if res.status == 'FAIL':
            # 已知缺陷：行为确实不对，但已有工单，不阻塞套件
            return Result(case.name, 'SKIP', detail='xfail[%s] %s' % (ticket, xfail))
        if res.status == 'OK':
            # 严格 xfail：缺陷被修好了 -> 必须移除标记，否则视为失败
            return Result(case.name, 'FAIL',
                          detail='XPASS[%s] 缺陷已修复，请移除 xfail 标记: %s' % (ticket, xfail))
    return res


def _run_case_inner(case: Case, update_golden: bool) -> Result:
    expect = case.expect

    # --- 只验证编译失败的负例 ---
    if case.runs_on == 'none':
        ok, p = do_compile(case)
        if ok:
            return Result(case.name, 'FAIL', detail='期望编译失败，但成功产出了 .qtc')
        errs = []
        if 'exit_not' in expect and p.returncode == expect['exit_not']:
            errs.append('退出码不应为 %s' % expect['exit_not'])
        for sub in expect.get('stderr_contains', []):
            if sub not in (p.stderr or ''):
                errs.append('stderr 缺少 %r' % sub)
        if expect.get('artifact') is None and os.path.exists(case.artifact()):
            errs.append('不应产出 .qtc 但产出了')
        return Result(case.name, 'FAIL', '; '.join(errs)) if errs else Result(case.name, 'OK')

    actual_lines = None
    checked = []

    # --- VM 路径 ---
    if case.runs_on in ('vm', 'both'):
        ok, cp = do_compile(case)
        if not ok:
            return Result(case.name, 'FAIL',
                          detail='编译失败\n' + (cp.stderr or cp.stdout or '')[-400:])
        if 'exit' in expect and cp.returncode != expect['exit']:
            return Result(case.name, 'FAIL', detail='编译退出码 %s != %s' % (cp.returncode, expect['exit']))
        for sub in expect.get('compile_stderr_contains', []):
            if sub not in (cp.stderr or ''):
                return Result(case.name, 'FAIL',
                              detail='编译 stderr 缺少 %r（实际: %r）' % (sub, (cp.stderr or '')[-300:]))
        if expect.get('compile_stderr_empty') and (cp.stderr or '').strip():
            return Result(case.name, 'FAIL', detail='期望编译 stderr 为空，实际:\n%s' % (cp.stderr or '')[-400:])
        p = do_run_vm(case.artifact())
        checked.append(('vm', p))
        actual_lines = normalize(p.stdout)

    # --- 解释器路径 ---
    if case.runs_on in ('interp', 'both'):
        p = do_run_interp(case.qot)
        checked.append(('interp', p))
        if actual_lines is None:
            actual_lines = normalize(p.stdout)

    # --- 断言 ---
    for where, p in checked:
        if 'exit' in expect and p.returncode != expect['exit']:
            return Result(case.name, 'FAIL',
                          detail='[%s] 退出码 %s != %s' % (where, p.returncode, expect['exit']))
        if expect.get('stderr_empty') and (p.stderr or '').strip():
            return Result(case.name, 'FAIL',
                          detail='[%s] 期望 stderr 为空，实际:\n%s' % (where, p.stderr[-400:]))
        for sub in expect.get('stderr_contains', []):
            if sub not in (p.stderr or ''):
                return Result(case.name, 'FAIL', detail='[%s] stderr 缺少 %r' % (where, sub))

    # --- golden 比对 ---
    if actual_lines is None:
        actual_lines = []

    if update_golden:
        os.makedirs(GOLDEN, exist_ok=True)
        with open(case.golden_path, 'w', encoding='utf-8', newline='\n') as f:
            f.write('\n'.join(actual_lines) + ('\n' if actual_lines else ''))
        # 即使重录 golden，写死的硬断言仍然要校验（否则 --update-golden 会掩盖回归）
        if 'stdout' in expect and list(expect['stdout']) != actual_lines:
            return Result(case.name, 'FAIL', detail='硬断言 expect.stdout 不成立（重录 golden 也不能豁免）',
                          expected=list(expect['stdout']), actual=actual_lines)
        return Result(case.name, 'NEW', detail='golden 已写入')

    # expect.stdout 若显式给出，以它为准且不再要求 golden；否则回落到 golden
    if 'stdout' in expect:
        want = list(expect['stdout'])
    elif os.path.exists(case.golden_path):
        with open(case.golden_path, 'r', encoding='utf-8') as f:
            want = normalize(f.read())
    else:
        return Result(case.name, 'FAIL',
                      detail='缺少 golden 文件 %s（用 --update-golden 生成，或在 expect.stdout 里写死期望）' % case.golden_path,
                      actual=actual_lines)

    if want != actual_lines:
        return Result(case.name, 'FAIL', detail='输出不一致', expected=want, actual=actual_lines)

    return Result(case.name, 'OK')


# ---------------------------------------------------------------- 套件

def suite_cases(suite: str, update_golden: bool):
    results = []
    for c in discover_cases(suite):
        try:
            results.append(run_case(c, update_golden, suite=suite))
        except subprocess.TimeoutExpired:
            results.append(Result(c.name, 'FAIL', detail='超时'))
    return results


def run_unit(suite_name: str):
    """返回 Result 列表。cli 套件只跑 unit/test_cli.py"""
    loader = unittest.TestLoader()
    if suite_name == 'cli':
        path = os.path.join(UNIT, 'test_cli.py')
        if not os.path.exists(path):
            return [Result('cli', 'FAIL', detail='缺少 testing/unit/test_cli.py')]
        import importlib.util
        spec = importlib.util.spec_from_file_location('qot_test_cli', path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        s = loader.loadTestsFromModule(mod)
    else:
        s = loader.discover(UNIT, pattern='test_*.py', top_level_dir=UNIT)

    records = []

    class Recorder(unittest.TextTestResult):
        def addSuccess(self, test):
            super().addSuccess(test)
            records.append((test, 'OK', ''))

        def addFailure(self, test, err):
            super().addFailure(test, err)
            records.append((test, 'FAIL', self._msg(err)))

        def addError(self, test, err):
            super().addError(test, err)
            records.append((test, 'FAIL', self._msg(err)))

        def addSkip(self, test, reason):
            super().addSkip(test, reason)
            records.append((test, 'SKIP', reason))

        # 已知缺陷钉（@unittest.expectedFailure）：必须**可见**，否则裁判会睡着
        # —— 修好了（unexpected success）要判 FAIL，逼着回来删标记。
        def addExpectedFailure(self, test, err):
            super().addExpectedFailure(test, err)
            records.append((test, 'SKIP',
                            'xfail（已知缺陷，修复后必须移除标记）: %s'
                            % self._msg(err)))

        def addUnexpectedSuccess(self, test):
            super().addUnexpectedSuccess(test)
            records.append((test, 'FAIL',
                            'XPASS：已知缺陷已修复，请移除 @expectedFailure 标记'))

        @staticmethod
        def _msg(err):
            import traceback
            return traceback.format_exception_only(err[0], err[1])[-1].strip()

    runner = unittest.TextTestRunner(stream=_StringSink(), verbosity=0, resultclass=Recorder)
    runner.run(s)

    return [Result(t.id(), st, detail=d) for t, st, d in records]


class _StringSink:
    def __init__(self):
        self.buf = []

    def write(self, s):
        self.buf.append(s)

    def writeln(self, s):
        self.buf.append(s)

    def flush(self):
        pass


# ---------------------------------------------------------------- fuzz（S-01）
def run_fuzz_suite(count: int, native_sample: int, seed_base: int):
    """fuzz 差分套件（S-01）：interp × VM × native(抽样)。委托 fuzz/run_fuzz.py。"""
    import subprocess as _sp
    results = []
    cmd = [PY, os.path.join(HERE, 'fuzz', 'run_fuzz.py'),
           '--count', str(count), '--seed-base', str(seed_base),
           '--native-sample', str(native_sample),
           '--workers', '2', '--quiet']
    p = _sp.run(cmd, cwd=ROOT, capture_output=True, text=True,
                encoding='utf-8', errors='replace',
                timeout=count * 3 + native_sample * 240 + 300)
    out = (p.stdout or '')
    for line in out.splitlines():
        if line.startswith('['):
            # [STATUS] seed=NNN detail
            body = line[1:line.index(']')]
            seed = line.split('seed=')[1].split(' ')[0]
            detail = line[line.index(']') + 1:].strip()
            status = 'FAIL' if body not in ('OK', 'NATIVE_SKIP',
                                            'KNOWN_ITERLEN_FRAC') else \
                ('OK' if body == 'OK' else 'SKIP')
            results.append(Result('fuzz_%s' % seed, status,
                                  detail='[%s] %s' % (body, detail[:200])))
    if '--- fuzz 汇总' in out:
        results.append(Result('fuzz_summary', 'OK' if p.returncode == 0 else 'FAIL',
                              detail=out.split('--- fuzz 汇总')[1].split('---')[0].strip()))
    elif p.returncode != 0:
        results.append(Result('fuzz_summary', 'FAIL',
                              detail='fuzz 运行器异常退出: %s' % (p.stderr or '')[-300:]))
    return results


# ---------------------------------------------------------------- 原生（后端 A）
def _has_cxx():
    """M-PACK：AOT 管线已切 g++（与 index.py 一致）；MSVC 保留为回退。"""
    sysdir = os.path.join(ROOT, 'testing', 'cpp')
    sys.path.insert(0, sysdir)
    try:
        import run_cpp_tests
        fg = getattr(run_cpp_tests, 'find_gxx', None)
        if callable(fg) and fg():
            return True
        return run_cpp_tests.find_cl() is not None
    except Exception:
        import shutil
        return shutil.which('g++') is not None or shutil.which('gcc') is not None


def _xfold(res: Result, case: Case) -> Result:
    """已知缺陷钉（xfail）的统一折算：FAIL→SKIP，XPASS→FAIL（裁判不许睡着）。"""
    xfail = case.expect.get('xfail')
    if not xfail:
        return res
    ticket = case.expect.get('ticket', '-')
    if res.status == 'FAIL':
        return Result(case.name, 'SKIP', detail='xfail[%s] %s' % (ticket, xfail))
    if res.status == 'OK':
        return Result(case.name, 'FAIL',
                      detail='XPASS[%s] 缺陷已修复，请移除 xfail 标记: %s' % (ticket, xfail))
    return res


def _golden_check(case: Case, actual_lines: list, update_golden: bool,
                  where: str) -> Result:
    """期望源 = golden（或用例里写死的 expect.stdout），与 _run_case_inner 同口径。"""
    if update_golden:
        os.makedirs(GOLDEN, exist_ok=True)
        with open(case.golden_path, 'w', encoding='utf-8', newline='\n') as f:
            f.write('\n'.join(actual_lines) + ('\n' if actual_lines else ''))
        if 'stdout' in case.expect and list(case.expect['stdout']) != actual_lines:
            return Result(case.name, 'FAIL',
                          detail='硬断言 expect.stdout 不成立（重录 golden 也不能豁免）',
                          expected=list(case.expect['stdout']), actual=actual_lines)
        return Result(case.name, 'NEW', detail='golden 已按 %s 口径写入' % where)
    if 'stdout' in case.expect:
        want = list(case.expect['stdout'])
    elif os.path.exists(case.golden_path):
        with open(case.golden_path, 'r', encoding='utf-8') as f:
            want = normalize(f.read())
    else:
        return Result(case.name, 'FAIL',
                      detail='缺少 golden 文件 %s（用 --update-golden --reason 生成，'
                             '或在 expect.stdout 里写死期望）' % case.golden_path,
                      actual=actual_lines)
    if want != actual_lines:
        return Result(case.name, 'FAIL', detail='[%s] 输出与期望不一致' % where,
                      expected=want, actual=actual_lines)
    return Result(case.name, 'OK')


def run_native_case(c: Case, aot_dir: str, update_golden: bool) -> Result:
    pre_err = _prerun(c)
    if pre_err:
        return Result(c.name, 'FAIL', detail='[native] %s' % pre_err)
    exe = os.path.join(aot_dir, c.name + '.exe')
    if os.path.exists(exe):
        os.remove(exe)
    p = _spawn([PY, 'index.py', 'aot', c.qot, exe], DEFAULT_TIMEOUT)
    if not os.path.exists(exe):
        detail = (p.stderr or p.stdout or '').strip().splitlines()
        reason = detail[-1] if detail else 'AOT 编译失败'
        if '不支持' in reason or 'AOT' in reason:
            return Result(c.name, 'SKIP', detail='AOT: %s' % reason)
        return Result(c.name, 'FAIL',
                      detail='AOT 编译失败\n%s' % (p.stderr or '')[-400:])
    procs = _start_setup(c)
    try:
        rp = _spawn([exe], DEFAULT_TIMEOUT)
    finally:
        _stop_setup(procs)
    if 'exit' in c.expect and rp.returncode != c.expect['exit']:
        return _xfold(Result(c.name, 'FAIL',
                             detail='[native] 退出码 %s != %s'
                                    % (rp.returncode, c.expect['exit'])), c)
    for sub in c.expect.get('stderr_contains', []):
        if sub not in (rp.stderr or ''):
            return _xfold(Result(c.name, 'FAIL',
                                 detail='[native] stderr 缺少 %r' % sub), c)
    if c.expect.get('stderr_empty') and (rp.stderr or '').strip():
        return _xfold(Result(c.name, 'FAIL',
                             detail='[native] 期望 stderr 为空，实际:\n%s'
                                    % (rp.stderr or '')[-400:]), c)
    return _xfold(_golden_check(c, normalize(rp.stdout), update_golden, 'native'), c)


def run_native(update_golden: bool, only=None):
    results = []
    cdir = os.path.join(CASES, 'native')
    if not _has_cxx():
        for fn in sorted(os.listdir(cdir)) if os.path.isdir(cdir) else []:
            if fn.endswith('.qot'):
                results.append(Result(fn[:-4], 'SKIP', detail='未找到 g++/cl，跳过原生套件'))
        return results
    aot_dir = os.path.join(ARTIFACTS, 'aot')
    os.makedirs(aot_dir, exist_ok=True)
    for c in discover_cases('native'):
        if c.runs_on == 'vm':
            continue  # VM 冻结钉不进 native 套件（见上）
        if only and c.name != only and os.path.basename(c.qot)[:-4] != only:
            continue
        results.append(run_native_case(c, aot_dir, update_golden))
    return results


# ---------------------------------------------------------------- 性能

def _vm_once(qtc: str):
    """进程内计时。VM 会直接往 stdout 打印，这里临时重定向掉，避免污染报告。"""
    dll = ctypes.CDLL(DLL)
    dll.vm_init.restype = ctypes.c_void_p
    dll.vm_execute_file.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
    dll.vm_execute_file.restype = ctypes.c_bool
    dll.vm_destroy.argtypes = [ctypes.c_void_p]

    devnull = os.open(os.devnull, os.O_WRONLY)
    saved = os.dup(1)
    try:
        sys.stdout.flush()
        os.dup2(devnull, 1)
        vm = dll.vm_init()
        t = time.perf_counter()
        ok = dll.vm_execute_file(vm, qtc.encode('utf-8'))
        el = time.perf_counter() - t
        dll.vm_destroy(vm)
    finally:
        os.dup2(saved, 1)
        os.close(saved)
        os.close(devnull)
    return el, ok


def _startup_noise(repeat=5):
    ts = []
    for _ in range(repeat):
        t = time.perf_counter()
        subprocess.run([PY, '-c', 'pass'], cwd=ROOT, capture_output=True)
        ts.append(time.perf_counter() - t)
    return statistics.median(ts)


def run_perf(repeat: int, report: str, update: bool):
    base_path = os.path.join(PERF, 'perf.json')
    base = load_json(base_path) if os.path.exists(base_path) else {}
    noise = _startup_noise()
    results = []
    new_base = dict(base)
    meta = {
        'cpu': platform.processor() or platform.machine(),
        'os': platform.platform(),
        'python': sys.version.split()[0],
        'commit': git_commit(),
        'date': time.strftime('%Y-%m-%d %H:%M:%S'),
        'startup_noise_s': round(noise, 4),
    }

    if not os.path.isdir(PERF):
        return results
    files = sorted(f for f in os.listdir(PERF) if f.endswith('.qot'))
    for fn in files:
        name = fn[:-4]
        qot = os.path.join(PERF, fn)
        art = os.path.join(ARTIFACTS, name + '.qtc')
        cp = _spawn([PY, 'index.py', 'bstrust', 'compile', qot, art], PERF_TIMEOUT)
        if not os.path.exists(art):
            results.append(Result(name, 'FAIL', detail='编译失败: ' + (cp.stderr or '')[-300:]))
            continue

        vm_ts = []
        for _ in range(repeat):
            el, ok = _vm_once(art)
            if not ok:
                results.append(Result(name, 'FAIL', detail='vm_execute_file 返回 False'))
                break
            vm_ts.append(el)
        if not vm_ts:
            continue
        ip_ts = []
        for _ in range(max(3, repeat // 2)):
            t = time.perf_counter()
            _spawn([PY, 'index.py', 'run', qot], PERF_TIMEOUT)
            ip_ts.append(max(0.0, time.perf_counter() - t - noise))

        rec = {
            'vm_min_s': round(min(vm_ts), 6),
            'vm_median_s': round(statistics.median(vm_ts), 6),
            'interp_min_s': round(min(ip_ts), 6) if ip_ts else None,
            'repeat': repeat,
            'meta': meta,
        }
        new_base[name] = rec

        old = base.get(name)
        if old and old.get('vm_median_s'):
            ratio = rec['vm_median_s'] / old['vm_median_s']
            if ratio > 1.15:
                results.append(Result(name, 'FAIL',
                                      detail='性能退化 %.2fx (%.4fs -> %.4fs)'
                                             % (ratio, old['vm_median_s'], rec['vm_median_s'])))
            else:
                results.append(Result(name, 'OK', detail='%.4fs (%.2fx vs 基线)' % (rec['vm_median_s'], ratio)))
        else:
            results.append(Result(name, 'NEW', detail='%.4fs 已记录为基线' % rec['vm_median_s']))

    if update or not os.path.exists(base_path):
        os.makedirs(PERF, exist_ok=True)
        with open(base_path, 'w', encoding='utf-8') as f:
            json.dump(new_base, f, indent=1, ensure_ascii=False)

    if report:
        with open(report, 'w', encoding='utf-8') as f:
            f.write('# QOT 性能报告\n\n')
            f.write('- 机器: %s / %s\n- Python: %s\n- commit: %s\n- 启动底噪: %.4fs\n\n'
                    % (meta['cpu'], meta['os'], meta['python'], meta['commit'], noise))
            f.write('| 用例 | VM min | VM median | 解释器 min | 相对基线 |\n|---|---|---|---|---|\n')
            for name, rec in new_base.items():
                old = base.get(name)
                r = '-'
                if old and old.get('vm_median_s'):
                    r = '%.2fx' % (rec['vm_median_s'] / old['vm_median_s'])
                f.write('| %s | %.4fs | %.4fs | %s | %s |\n'
                        % (name, rec['vm_min_s'], rec['vm_median_s'], rec['interp_min_s'], r))
    return results


# ---------------------------------------------------------------- 报告

def print_result(r: Result):
    tag = {'OK': '[ OK ]', 'FAIL': '[FAIL]', 'SKIP': '[SKIP]', 'NEW': '[NEW ]'}[r.status]
    line = '%s %s' % (tag, r.name)
    if r.detail:
        line += '  ' + r.detail.replace('\n', '\n        ')
    # GBK 控制台兜底：detail 可能含跨进程捕获的替换字符（\ufffd），不可编码即降级
    try:
        print(line, flush=True)
    except UnicodeEncodeError:
        print(line.encode('gbk', 'replace').decode('gbk'), flush=True)
    if r.status == 'FAIL' and r.expected is not None:
        print('        expected: %r' % (r.expected,), flush=True)
        print('        actual  : %r' % (r.actual,), flush=True)


def write_junit(path, results):
    import xml.sax.saxutils as su
    total = len(results)
    fails = sum(1 for r in results if r.status == 'FAIL')
    with open(path, 'w', encoding='utf-8') as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n')
        f.write('<testsuite name="qot" tests="%d" failures="%d">\n' % (total, fails))
        for r in results:
            f.write('  <testcase name="%s">' % su.escape(r.name))
            if r.status == 'FAIL':
                f.write('<failure message="%s"/>' % su.escape(r.detail))
            elif r.status == 'SKIP':
                f.write('<skipped/>')
            f.write('</testcase>\n')
        f.write('</testsuite>\n')


# ---------------------------------------------------------------- main

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description='QOT 统一测试入口')
    ap.add_argument('--suite', default='all',
                    help=','.join(ALL_SUITES) + ',all')
    ap.add_argument('--case', help='只跑指定的 .qot 用例（需同目录有 .json 元数据）')
    ap.add_argument('--update-golden', action='store_true', help='重录 golden（必须带 --reason）')
    ap.add_argument('--reason', help='更新 golden 的理由，写入 golden/CHANGES.log')
    ap.add_argument('--repeat', type=int, default=5, help='性能套件重复次数')
    ap.add_argument('--report', help='性能报告输出路径（markdown）')
    ap.add_argument('--junit', help='JUnit XML 输出路径')
    args = ap.parse_args(argv)

    if args.update_golden and not args.reason:
        print('[错误] --update-golden 必须带 --reason（ R3）', file=sys.stderr)
        return 2

    os.makedirs(ARTIFACTS, exist_ok=True)
    os.makedirs(GOLDEN, exist_ok=True)

    # 环境检查
    if not os.path.exists(DLL):
        print('[错误] 缺少 %s（先跑 ot-asset/c/vmcshit/build.ps1）' % DLL, file=sys.stderr)
        return 3

    suites = ALL_SUITES if args.suite == 'all' else [s.strip() for s in args.suite.split(',')]
    for s in suites:
        if s not in ALL_SUITES:
            if s == 'vm' and VM_ABANDONED:
                print('[提示] vm 套件已弃管（ 用户裁决：VM 线不碰不测、'
                      '其报错不管）；考古比对请设 QOT_VM_SUITE=1', file=sys.stderr)
                continue
            print('[错误] 未知套件: %s' % s, file=sys.stderr)
            return 2
    suites = [s for s in suites if s in ALL_SUITES]

    results = []
    try:
        if args.case:
            if args.suite == 'native':
                # L1 快检通道：只跑受影响的那一条（编译成本 ~10s，不必跑全套）
                results += run_native(args.update_golden, only=args.case)
            else:
                meta_path = args.case[:-4] + '.json'
                if not os.path.exists(meta_path):
                    print('[错误] 用例缺少元数据: %s' % meta_path, file=sys.stderr)
                    return 2
                results.append(run_case(Case(args.case, load_json(meta_path)), args.update_golden))
        else:
            for s in suites:
                print('--- suite: %s ---' % s, flush=True)
                if s == 'perf':
                    results += run_perf(args.repeat, args.report, args.update_golden)
                elif s in ('unit', 'cli'):
                    results += run_unit(s)
                elif s == 'cpp':
                    sys.path.insert(0, os.path.join(HERE, 'cpp'))
                    import run_cpp_tests
                    for name, ok, detail in run_cpp_tests.run_suite():
                        results.append(Result(name, 'OK' if ok else 'FAIL', detail=detail))
                elif s == 'native':
                    results += run_native(args.update_golden)
                elif s == 'ci':
                    # 【QOT-CI】编译管线 CI 套件（§9.5 验收面）：确定性/等价性/
                    # key 完备性/PCH 透明性/切分行为/增量/命中率（K1~K5、G3、G5）
                    sys.path.insert(0, os.path.join(HERE, 'tools'))
                    import ci_suite
                    results += ci_suite.run_ci()
                elif s == 'fuzz':
                    results += run_fuzz_suite(count=FUZZ_SUITE_COUNT,
                                              native_sample=FUZZ_NATIVE_SAMPLE,
                                              seed_base=FUZZ_SUITE_SEED)
                else:
                    results += suite_cases(s, args.update_golden)
    except CaseError as e:
        print('[错误] %s' % e, file=sys.stderr)
        return 2

    if args.update_golden and args.reason:
        with open(GOLDEN_LOG, 'a', encoding='utf-8') as f:
            f.write('%s  commit=%s  reason=%s\n' % (time.strftime('%Y-%m-%d %H:%M:%S'), git_commit(), args.reason))

    for r in results:
        if r.status != 'OK' or args.case:
            print_result(r)

    ok = sum(1 for r in results if r.status == 'OK')
    new = sum(1 for r in results if r.status == 'NEW')
    skip = sum(1 for r in results if r.status == 'SKIP')
    bad = sum(1 for r in results if r.status == 'FAIL')

    print('')
    print('%s  (%d cases: ok=%d fail=%d new=%d skip=%d)'
          % ('ALL PASS' if bad == 0 else '%d FAILED' % bad, len(results), ok, bad, new, skip), flush=True)

    if args.junit:
        write_junit(args.junit, results)

    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
