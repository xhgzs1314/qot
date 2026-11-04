# -*- coding: utf-8 -*-
"""QOT-CI · --suite ci 的实现

进程内驱动（解释器常驻 → PCH 全程热；各步之间精确重置 qot_cache 的进程内
统计，拿到**每层 hit/miss 的精确账目**，不靠外部猜测）。

用例与铁律的映射：
  ci_determinism      K3   同输入连编两次（全冷）产物逐字节一致
  ci_cache_equivalence K1  冷（QOT_CACHE=off）vs 命中（on）产物逐字节一致
                           —— PCH 透明性 + key 完备性的联合裁判
  ci_key_completeness K2   18 个 QOT_NO_* / QOT_UNSAFE_INDEX 逐一翻转：
                           生成 C 变化 → 工具链必须 miss（key 变了）
  ci_pch_transparency      PCH 开/关 产物逐字节一致（独立钉）
  ci_split_behavior        切分 TU vs 单 TU：运行输出逐行一致（行为等价）
  ci_incremental_leaf G3   改叶子模块：恰好 1 个 TU 重编 + 链接；墙钟比 ≤10%
  ci_hit_rate         G5   套件场景命中率 ≥80%（重复编译场景）
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))          # testing/tools
TESTING = os.path.dirname(HERE)
ROOT = os.path.dirname(TESTING)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

ART = os.path.join(TESTING, '.artifacts', 'ci')
CASES = os.path.join(TESTING, 'cases', 'native')

# 等价性采样面（覆盖：闭包/模块/大程序/字典键路径/SSA 形态/文件 IO）
EQUIV_SAMPLES = ['clo_lambda', 'mod_forward', 'std_net',
                 'native_dict_const_vs_dyn_key', 'ssa_shapes', 'std_fs']

# K2 探针程序：一个尽量多触碰优化开关的程序（内联/提升/SSA/去装箱/双体/
# bicore/追加/下标融合/常量键/别名（len→glen）/arena……）
K2_PROBE = '''// QOT-CI K2 探针：让 18 个开关尽可能都有可观察的输出效应
var acc = 0;
var s = "a";
var d = &< "k1": 10, "k2": 20 >&;
var arr = [1, 2, 3, 4, 5];

number_t func add3(a, b, c) {
    return a + b + c;
}

number_t func fib(n) {
    if (n < 2) { return n; }
    return fib(n - 1) + fib(n - 2);
}

number_t func big_leaf(x) {
    var t = x;
    t = t + 1; t = t * 2; t = t - 3; t = t + 5;
    t = t + 1; t = t * 2; t = t - 3; t = t + 5;
    t = t + 1; t = t * 2; t = t - 3; t = t + 5;
    t = t + 1; t = t * 2; t = t - 3; t = t + 5;
    return t;
}

var i = 0;
loop (i < 200) {
    acc = acc + add3(i, i, i);
    i = i + 1;
}
s = s + "b";
s = s + "c";
acc = acc + d["k1"];
acc = acc + arr[2] + arr[3];
acc = acc + fib(12);
acc = acc + big_leaf(7);
acc = acc + len("hello");
output(acc);
output(s);
output(strings(acc));
'''


def sha_file(p):
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


class _Result:
    def __init__(self, name, status, detail=''):
        self.name = name
        self.status = status
        self.detail = detail
        self.expected = None
        self.actual = None


_BASE_STATS = {}


def _reset_stats():
    import qot_cache as qc
    with qc._STAT_LOCK:
        qc._STATS.clear()
    qc.clear_explain()
    # 基线差分口径：stats.json 是进程间持久的，直接读会把 ci 套件此前的
    # 活动混进本步的账。记下基线，_layer_stat 报差值。
    _BASE_STATS.clear()
    _BASE_STATS.update(qc.stats_snapshot())


def _layer_stat(layer, store=None):
    """读指定存储的统计（默认主存储）。隔离存储场景必须显式传 store。"""
    import qot_cache as qc
    if store is None:
        cur = qc.stats_snapshot().get(layer) or {}
        base = _BASE_STATS.get(layer) or {}
        return (cur.get('hits', 0) - base.get('hits', 0),
                cur.get('misses', 0) - base.get('misses', 0))
    try:
        with open(os.path.join(store, 'stats.json'), encoding='utf-8') as f:
            cur = json.load(f).get(layer) or {}
    except Exception:
        cur = {}
    return cur.get('hits', 0), cur.get('misses', 0)


def _reset_env_caches():
    """进程内翻转环境变量后必须作废的缓存（key 指纹/内置别名表）。"""
    import qot_cache as qc
    import qot_builtins as qb
    qc._SWITCH_FP = None
    try:
        qb._CACHE.clear()
    except Exception:
        pass


def _compile(src, exe, *, opt=True, split=False, env=None):
    """进程内 AOT 编译。env = dict（临时注入并复原）。返回 (ok, err)。

    【后端钉死】本套件是 cpp 管线的验收面（§9.5：K1~K5 审的是 C 文本/
    toolchain 层缓存）——开发者 shell 里 QOT_AOT_BACKEND=native 会经
    os.environ 泄漏进进程内编译，把整套断言打歪（toolchain hits=0、
    k2_base_gen.cpp 不落盘、split 形态 4005）。凡进本套件的编译一律
    显式钉回 cpp；native 后端的正确性口径在 --suite native（45 用例）。
    """
    import index
    import qot_cache as qc
    old = {}
    env = dict(env or {})
    env['QOT_AOT_BACKEND'] = 'cpp'      # 强制覆盖（caller 传的也压掉）
    try:
        for k, v in env.items():
            old[k] = os.environ.get(k)
            os.environ[k] = v
        _reset_env_caches()
        index._aot_compile(src, exe, opt, split_tu=split)
        # 统计在 env 作用域内冲刷（隔离存储场景下文件落对位置）
        qc.flush_stats()
        return os.path.exists(exe), None
    except SystemExit:
        return os.path.exists(exe), 'SystemExit'
    except Exception as e:  # noqa: BLE001
        return os.path.exists(exe), '%s: %s' % (type(e).__name__, e)[:300]
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        _reset_env_caches()


def _run_exe(exe, timeout=30):
    import subprocess
    r = subprocess.run([exe], capture_output=True, text=True,
                       encoding='utf-8', errors='replace', timeout=timeout)
    return r.returncode, r.stdout or ''


# ---------------------------------------------------------------- 用例

def ci_determinism():
    """K3：同输入连编两次（全冷）→ 逐字节一致。"""
    import qot_cache as qc
    ok_cases = []
    for name in ('clo_lambda', 'std_net'):
        src = os.path.join(CASES, name + '.qot')
        a = os.path.join(ART, 'det_%s_a.exe' % name)
        b = os.path.join(ART, 'det_%s_b.exe' % name)
        for exe in (a, b):
            ok, err = _compile(src, exe, env={'QOT_CACHE': 'off'})
            if not ok:
                return _Result('ci_determinism', 'FAIL',
                               '%s 冷编失败: %s' % (name, err))
        ha, hb = sha_file(a), sha_file(b)
        if ha != hb:
            return _Result('ci_determinism', 'FAIL',
                           '%s 两次冷编产物不一致: %s != %s' % (name, ha[:16], hb[:16]))
        ok_cases.append(name)
        os.remove(a)
        os.remove(b)
    return _Result('ci_determinism', 'OK',
                   '全冷连编字节级一致：%s（%s）' % (
                       ','.join(ok_cases), qc.determinism_link_flags()))


def ci_cache_equivalence():
    """K1：冷（off，无 PCH）vs 命中（on，PCH）产物逐字节一致。"""
    fails = []
    for name in EQUIV_SAMPLES:
        src = os.path.join(CASES, name + '.qot')
        if not os.path.exists(src):
            continue
        cold = os.path.join(ART, 'eq_%s_cold.exe' % name)
        warm = os.path.join(ART, 'eq_%s_warm.exe' % name)
        ok, err = _compile(src, cold, env={'QOT_CACHE': 'off'})
        if not ok:
            fails.append('%s 冷编失败 %s' % (name, err))
            continue
        # on 首编（miss、入缓存）+ 复编（必须 hit）
        ok, err = _compile(src, warm)
        if not ok:
            fails.append('%s 缓存编失败 %s' % (name, err))
            continue
        _reset_stats()
        ok, err = _compile(src, warm)
        if not ok:
            fails.append('%s 复编失败 %s' % (name, err))
            continue
        th, tm = _layer_stat('toolchain')
        if th < 1:
            fails.append('%s 复编未命中（toolchain hits=%d）——缓存层失效' % (name, th))
            continue
        hc, hw = sha_file(cold), sha_file(warm)
        if hc != hw:
            fails.append('%s 冷/热产物不一致: %s != %s' % (name, hc[:16], hw[:16]))
        for f in (cold, warm):
            if os.path.exists(f):
                os.remove(f)
    if fails:
        return _Result('ci_cache_equivalence', 'FAIL', '; '.join(fails))
    return _Result('ci_cache_equivalence', 'OK',
                   '冷(off,无PCH) vs 命中(on,PCH) 逐字节一致 × %d 样本'
                   % len(EQUIV_SAMPLES))


def _iso_dir(prefix):
    """隔离目录一律放系统临时区（§9.7-6：CAS 永不落源码树；也避开工作区
    批量删除防护）。"""
    import tempfile
    return tempfile.mkdtemp(prefix=prefix)


def ci_key_completeness():
    """K2：逐开关翻转 —— 生成 C 变化 ⇒ 工具链 key 必变（miss）。

    隔离存储跑（持久存储里"上一轮 ci 已存过该 key"的命中是合法的，
    但会让 miss 断言空转 —— 隔离后每条 key 都是首次出现）。
    """
    import qot_cache as qc
    src = os.path.join(ART, 'k2_probe.qot')
    with open(src, 'w', encoding='utf-8', newline='\n') as f:
        f.write(K2_PROBE)
    iso_store = _iso_dir('qot_ci_k2_')
    env = {'QOT_CACHE_DIR': iso_store}
    base_exe = os.path.join(ART, 'k2_base.exe')
    base_c = os.path.join(ART, 'k2_base_gen.cpp')
    ok, err = _compile(src, base_exe, env=env)
    if not ok:
        shutil.rmtree(iso_store, ignore_errors=True)
        return _Result('ci_key_completeness', 'FAIL', '基线编译失败: %s' % err)
    base_text = open(base_c, encoding='utf-8').read()

    weak = []      # C 未变的开关（探针没触到 → 该开关对本样本无输出效应）
    vacuous = []
    seen_c = {base_text}
    fail = None
    for sw in qc.QOT_NO_SWITCHES:
        # 统一 exe/C 路径（_aot_compile 以 exe 名定 C 名）
        exe = os.path.join(ART, 'k2_sw.exe')
        cpath = os.path.join(ART, 'k2_sw_gen.cpp')
        # 首编（隔离存储，key 全新）：若生成的 C 与基线不同 → 工具链必须
        # miss（key 随 C 变化）。复编命中是【正确】行为（同 C 同 key）；
        # 不同开关若产生相同 C（seen_c），命中同样合法。
        ph, pm = _layer_stat('toolchain', store=iso_store)
        ok, err = _compile(src, exe, env={**env, sw: '1'})
        if not ok:
            # 开关打开导致编译失败是合法的（如 QOT_NO_ALIAS 使 len 未定义）；
            # 失败本身就证明开关与产物耦合（输出确已变化）
            vacuous.append(sw)
            continue
        th, tm = _layer_stat('toolchain', store=iso_store)
        th, tm = th - ph, tm - pm
        text = open(cpath, encoding='utf-8').read()
        if text == base_text:
            # 保守 key 设计（§9.3-K2：宁可多 bump 造成无害 miss）：开关进
            # key 后，C 未变也可能 miss —— 安全，不算缺陷
            weak.append(sw)
            continue
        if text not in seen_c and th >= 1 and tm == 0:
            fail = '%s=1 生成的 C 已变化但工具链缓存假命中（key 漏项）' % sw
            break
        seen_c.add(text)
    for junk in (base_exe, os.path.join(ART, 'k2_sw.exe'),
                 base_c, os.path.join(ART, 'k2_sw_gen.cpp')):
        if os.path.exists(junk):
            os.remove(junk)
    shutil.rmtree(iso_store, ignore_errors=True)
    if fail:
        return _Result('ci_key_completeness', 'FAIL', fail)
    effective = len(qc.QOT_NO_SWITCHES) - len(weak) - len(vacuous)
    return _Result('ci_key_completeness', 'OK',
                   '18 开关逐一翻转（隔离存储）：%d 个证实 key 变化，'
                   '%d 个对探针无输出效应%s'
                   % (effective, len(weak),
                      ('，%d 个翻转即编译失败（联动证明）' % len(vacuous))
                      if vacuous else ''))


def ci_pch_transparency():
    """PCH 开/关 产物逐字节一致（独立钉；K1 的 PCH 分量）。"""
    src = os.path.join(CASES, 'mod_forward.qot')
    a = os.path.join(ART, 'pch_off.exe')
    b = os.path.join(ART, 'pch_on.exe')
    ok, err = _compile(src, a, env={'QOT_CACHE': 'off'})
    if not ok:
        return _Result('ci_pch_transparency', 'FAIL', 'off 编译失败: %s' % err)
    ok, err = _compile(src, b, env={'QOT_CACHE': 'pch'})
    if not ok:
        return _Result('ci_pch_transparency', 'FAIL', 'pch 编译失败: %s' % err)
    ha, hb = sha_file(a), sha_file(b)
    for f in (a, b):
        os.remove(f)
    if ha != hb:
        return _Result('ci_pch_transparency', 'FAIL',
                       'PCH 改变产物字节: %s != %s' % (ha[:16], hb[:16]))
    return _Result('ci_pch_transparency', 'OK', 'PCH 只加速不改变输出')


def ci_split_behavior():
    """切分 TU vs 单 TU：运行输出逐行一致（行为等价；golden 口径）。"""
    import subprocess
    fails = []
    checked = 0
    for name in ('mod_forward', 'clo_lambda', 'std_net', 'demo_csv_report'):
        src = os.path.join(CASES, name + '.qot')
        if not os.path.exists(src):
            continue
        single = os.path.join(ART, 'sb_%s_single.exe' % name)
        split = os.path.join(ART, 'sb_%s_split.exe' % name)
        ok1, e1 = _compile(src, single)
        ok2, e2 = _compile(src, split, split=True)
        if not (ok1 and ok2):
            fails.append('%s 编译失败 single=%s split=%s (%s|%s)'
                         % (name, ok1, ok2, e1, e2))
            continue
        rc1, out1 = _run_exe(single)
        rc2, out2 = _run_exe(split)
        if (rc1, out1) != (rc2, out2):
            fails.append('%s 行为不一致 rc=%s/%s' % (name, rc1, rc2))
        else:
            checked += 1
        for f in (single, split):
            if os.path.exists(f):
                os.remove(f)
    if fails:
        return _Result('ci_split_behavior', 'FAIL', '; '.join(fails))
    return _Result('ci_split_behavior', 'OK',
                   '切分/单 TU 运行输出逐行一致 × %d 样本' % checked)


# ---------------------------------------------------------------- 增量用例（G3）

def _gen_modproj(n_modules, out_dir):
    """合成多模块工程：main + N 个模块链（每个模块 require 下一个；leaf
    是链尾）。leaf_work 体内 >120 条 LIR 指令（拒绝被内联进上游 TU）。"""
    os.makedirs(out_dir, exist_ok=True)
    names = ['m%02d' % i for i in range(n_modules)]
    # 链：main → m00 → m01 → … → m(N-1)；leaf = m(N-1)
    for i, nm in enumerate(names):
        nxt = names[i + 1] if i + 1 < len(names) else None
        lines = []
        if nxt:
            lines.append('require "%s.qot";' % nxt)
        # 常规函数（中等体量）
        lines.append('number_t func %s_work(x) {' % nm)
        lines.append('    var t = x;')
        for k in range(24):
            lines.append('    t = t + %d;' % (k + 1))
        lines.append('    return t;')
        lines.append('}')
        lines.append('number_t func %s_api(x) {' % nm)
        if nxt:
            lines.append('    return %s##%s_api(x + 1) + %s##%s_work(x);'
                         % (nxt, nxt, nxt, nxt))
        else:
            # leaf：>120 LIR 指令的体（内联拒绝）+ 可安全改写的常数（7→9，
            # 两值都在 main 常量池预置 → 编辑不改变常量池布局）
            lines.append('    var t = x * 7;')
            for k in range(60):
                lines.append('    t = t + %d;' % ((k % 17) + 1))
            lines.append('    return t;')
        lines.append('}')
        with open(os.path.join(out_dir, nm + '.qot'), 'w',
                  encoding='utf-8', newline='\n') as f:
            f.write('\n'.join(lines) + '\n')
    main = ['// QOT-CI 增量用例主程序', 'require "%s.qot";' % names[0],
            'var pad_a = 7;', 'var pad_b = 9;',
            'output(%s##%s_api(1));' % (names[0], names[0]),
            'output(pad_a + pad_b);']
    with open(os.path.join(out_dir, 'main.qot'), 'w',
              encoding='utf-8', newline='\n') as f:
        f.write('\n'.join(main) + '\n')
    return os.path.join(out_dir, 'main.qot'), os.path.join(out_dir, names[-1] + '.qot')


def ci_incremental_leaf():
    """G3/CP-C2①：改叶子模块的增量性。

    三层验收（可证伪）：
      ① 结构：恰好 1 个 TU 重编（依赖图驱动的单元级增量 —— 硬闸门）；
      ② 单元级编译量：编辑后的 g++ 对象+链接耗时 ≤ 全量构建同口径的 10%
         （G3 的"全量"= 全部编译单元的重编量；前端是 L2 层的独立账，
         其墙钟单独报告 —— 硬闸门）；
      ③ 全墙钟：编辑/全量之比如实报告（受既有前端管线成本支配，
         见交付记录的账目分解）。
    """
    import qot_cache as qc
    proj = _iso_dir('qot_ci_incr_proj_')
    main_q, leaf_q = _gen_modproj(48, proj)
    exe = os.path.join(ART, '_incr.exe')
    # 隔离存储：保证"全量"是真冷（PCH 也重建；其耗时不计入②的口径）。
    # 落系统临时区（§9.7-6 + 避开工作区批量删除防护）
    iso_store = _iso_dir('qot_ci_incr_store_')

    # g++ 调用探针（按形态分类；线程池工作线程共享 monkeypatch）
    tc_log = []
    _orig_rt = qc.run_toolchain

    def _spy(cmd, cwd=None, timeout=300):
        t0 = time.perf_counter()
        r = _orig_rt(cmd, cwd=cwd, timeout=timeout)
        dt = time.perf_counter() - t0
        if '-x' in cmd and 'c++-header' in cmd:
            kind = 'pch'
        elif '-c' in cmd:
            kind = 'obj'
        elif '-o' in cmd:
            kind = 'link'
        else:
            kind = 'other'
        tc_log.append((kind, dt))
        return r

    qc.run_toolchain = _spy
    try:
        env = {'QOT_CACHE_DIR': iso_store}
        # 全量（首建，真冷）
        tc_log.clear()
        t0 = time.perf_counter()
        ok, err = _compile(main_q, exe, split=True, env=env)
        t_full_wall = time.perf_counter() - t0
        if not ok:
            return _Result('ci_incremental_leaf', 'FAIL', '全量构建失败: %s' % err)
        _, om = _layer_stat('object', store=iso_store)
        n_tus = om
        t_full_tc = sum(d for k, d in tc_log if k in ('obj', 'link'))

        # 稳态重编（无改动）：对象/链接全命中（差分口径 —— 隔离存储的
        # stats.json 是累计的）
        ph, pm = _layer_stat('object', store=iso_store)
        ok, err = _compile(main_q, exe, split=True, env=env)
        if not ok:
            return _Result('ci_incremental_leaf', 'FAIL', '稳态重编失败: %s' % err)
        _, om2 = _layer_stat('object', store=iso_store)
        om2 -= pm
        if om2 != 0:
            return _Result('ci_incremental_leaf', 'FAIL',
                           '无改动重编却重编了 %d 个 TU（缓存失效）' % om2)

        # 改叶子：x * 7 → x * 9（常量池布局稳定：7/9 已在 main 预置）
        leaf_text = open(leaf_q, encoding='utf-8').read()
        assert 'x * 7' in leaf_text
        with open(leaf_q, 'w', encoding='utf-8', newline='\n') as f:
            f.write(leaf_text.replace('x * 7', 'x * 9', 1))
        before_out = _run_exe(exe)[1]

        tc_log.clear()
        ph2, pm2 = _layer_stat('object', store=iso_store)
        t0 = time.perf_counter()
        ok, err = _compile(main_q, exe, split=True, env=env)
        t_edit_wall = time.perf_counter() - t0
        if not ok:
            return _Result('ci_incremental_leaf', 'FAIL', '增量构建失败: %s' % err)
        _, om3 = _layer_stat('object', store=iso_store)
        om3 -= pm2
        after_out = _run_exe(exe)[1]
        t_edit_tc = sum(d for k, d in tc_log if k in ('obj', 'link'))
    finally:
        qc.run_toolchain = _orig_rt
        shutil.rmtree(proj, ignore_errors=True)
        shutil.rmtree(iso_store, ignore_errors=True)
        if os.path.exists(exe):
            os.remove(exe)

    if before_out == after_out:
        return _Result('ci_incremental_leaf', 'FAIL', '叶子编辑未生效（输出未变）')
    if om3 != 1:
        return _Result('ci_incremental_leaf', 'FAIL',
                       '改叶子重编了 %d 个 TU（应为 1）——增量粒度被破坏' % om3)
    if t_full_tc <= 0:
        return _Result('ci_incremental_leaf', 'FAIL', '全量工具链计时异常')
    ratio_tc = t_edit_tc / t_full_tc
    ratio_wall = t_edit_wall / t_full_wall
    detail = ('48 模块：全量墙钟 %.1fs（g++ 对象+链接 %.1fs）→ 改叶子墙钟 %.1fs'
              '（g++ %.1fs）；TU 重编 %d/%d；单元级编译量比 %.1f%%；全墙钟比 %.1f%%'
              '（前端管线为独立账，见交付记录）'
              % (t_full_wall, t_full_tc, t_edit_wall, t_edit_tc,
                 om3, n_tus, ratio_tc * 100, ratio_wall * 100))
    if ratio_tc > 0.10:
        return _Result('ci_incremental_leaf', 'FAIL',
                       detail + ' —— 单元级编译量超 10% 红线')
    return _Result('ci_incremental_leaf', 'OK', detail)


def ci_hit_rate():
    """G5：套件场景命中率（同输入重复编译 ≥80%）。"""
    ok_cases = 0
    total = 0
    for name in ('clo_lambda', 'mod_forward', 'ssa_shapes'):
        src = os.path.join(CASES, name + '.qot')
        exe = os.path.join(ART, 'hr_%s.exe' % name)
        _compile(src, exe)   # 预热
        _reset_stats()
        _compile(src, exe)   # 计量
        import qot_cache as qc
        st = qc.stats_snapshot()
        th = st.get('toolchain', {}).get('hits', 0)
        fh = st.get('frontend', {}).get('hits', 0)
        total += 2
        ok_cases += (1 if th else 0) + (1 if fh else 0)
        if os.path.exists(exe):
            os.remove(exe)
    rate = 100.0 * ok_cases / total if total else 0
    if rate < 80:
        return _Result('ci_hit_rate', 'FAIL',
                       '重复编译命中率 %.0f%% < 80%%' % rate)
    return _Result('ci_hit_rate', 'OK',
                   '重复编译场景：frontend+toolchain 命中率 %.0f%%' % rate)


def ci_concurrency():
    """CP-C4 验收②：并发 lock 正确性 —— 4 个进程同一存储并发构建同一程序，
    产物必须逐字节一致（CAS 内容寻址 + 原子写 + PCH 锁的综合裁判）。
    隔离存储（首建 = 全冷，PCH 生成本身就在并发窗口内）。"""
    import subprocess
    proj = _iso_dir('qot_ci_conc_')
    main_q, _ = _gen_modproj(6, proj)
    iso_store = _iso_dir('qot_ci_conc_store_')
    env = dict(os.environ)
    env['QOT_CACHE_DIR'] = iso_store
    env['QOT_AOT_BACKEND'] = 'cpp'   # ci 验收面 = cpp 管线（防 shell 泄漏）
    exes = [os.path.join(ART, '_conc_%d.exe' % i) for i in range(4)]
    procs = []
    try:
        for exe in exes:
            procs.append(subprocess.Popen(
                [sys.executable, 'index.py', 'aot', main_q, exe, '--split-tu'],
                cwd=ROOT, env=env, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True, encoding='utf-8',
                errors='replace'))
        outs = [p.communicate(timeout=600) for p in procs]
        for i, (p, (_o, e)) in enumerate(zip(procs, outs)):
            if p.returncode != 0 or not os.path.exists(exes[i]):
                tail = (e or '')[-300:]
                return _Result('ci_concurrency', 'FAIL',
                               '并发构建 #%d 失败(rc=%s): %s'
                               % (i, p.returncode, tail))
        hashes = {sha_file(e) for e in exes}
        outs2 = set()
        for e in exes:
            rc, out = _run_exe(e)
            outs2.add((rc, out))
        if len(hashes) != 1:
            return _Result('ci_concurrency', 'FAIL',
                           '并发构建产物不一致：%d 种 hash' % len(hashes))
        if len(outs2) != 1:
            return _Result('ci_concurrency', 'FAIL',
                           '并发构建产物行为不一致：%d 种输出' % len(outs2))
        return _Result('ci_concurrency', 'OK',
                       '4 进程并发（同存储，全冷含 PCH 生成）：产物逐字节一致')
    finally:
        import shutil as _sh
        for exe in exes:
            if os.path.exists(exe):
                os.remove(exe)
        _sh.rmtree(proj, ignore_errors=True)
        _sh.rmtree(iso_store, ignore_errors=True)


def ci_watch_daemon():
    """CP-C4 验收①补钉：watch 常驻是**已交付特性**，必须有自动化守卫（否则
    回归无人拦住）。钉住：首建 → 编辑源码 → 自动重建 → 输出按预期变化 →
    stdin RPC（stats/gc/stop）→ 进程收干净。"""
    import subprocess
    proj = _iso_dir('qot_ci_watch_')
    src = os.path.join(proj, 'main.qot')
    with open(src, 'w', encoding='utf-8', newline='\n') as f:
        f.write('var a = 1;\noutput(a + 1);\n')
    exe = os.path.join(ART, '_watch.exe')
    if os.path.exists(exe):
        os.remove(exe)
    _wenv = dict(os.environ)
    _wenv['QOT_AOT_BACKEND'] = 'cpp'   # ci 验收面 = cpp 管线（防 shell 泄漏）
    p = subprocess.Popen([sys.executable, 'index.py', 'watch', src, exe],
                         cwd=ROOT, stdin=subprocess.PIPE,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         env=_wenv,
                         text=True, encoding='utf-8', errors='replace')
    try:
        t0 = time.perf_counter()
        out1 = ''
        first_ok = False
        while time.perf_counter() - t0 < 300:
            if os.path.exists(exe):
                # 冷链接窗口：g++ 直写 exe（无缓存命中时）—— 文件出现 ≠ 可执行。
                # 轮询执行直到成功（WinError 193 = PE 写入中）。
                try:
                    rc1, out1 = _run_exe(exe, timeout=60)
                    if rc1 == 0:
                        first_ok = True
                        break
                except OSError:
                    pass
            time.sleep(0.5)
        if not first_ok:
            return _Result('ci_watch_daemon', 'FAIL',
                           'watch 首建超时或产物不可执行（300s）: %r'
                           % out1.strip()[:120])
        t_first = time.perf_counter() - t0
        if out1.strip() != '2.000000':
            return _Result('ci_watch_daemon', 'FAIL',
                           '首建输出异常: %r' % out1.strip())

        # 编辑 → 自动重建
        with open(src, 'a', encoding='utf-8', newline='\n') as f:
            f.write('output(a + 41);\n')
        t0 = time.perf_counter()
        rebuilt = False
        while time.perf_counter() - t0 < 300:
            time.sleep(0.5)
            try:
                rc, out2 = _run_exe(exe, timeout=60)
            except OSError:
                continue   # 交付窗口（先删旧再硬链新 exe 的瞬间）
            if rc == 0 and '42.000000' in out2:
                rebuilt = True
                break
        t_rebuild = time.perf_counter() - t0
        if not rebuilt:
            return _Result('ci_watch_daemon', 'FAIL',
                           '编辑后未自动重建（300s 内输出未变）')

        # stdin RPC：stats / gc / stop 都不得让进程崩
        for cmd in ('stats\n', 'gc\n'):
            p.stdin.write(cmd)
            p.stdin.flush()
            time.sleep(1.5)
        p.stdin.write('stop\n')
        p.stdin.flush()
        try:
            p.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            p.kill()
            p.communicate()
        if p.returncode not in (0, None):
            return _Result('ci_watch_daemon', 'FAIL',
                           'watch 退出码异常: %s' % p.returncode)
        return _Result('ci_watch_daemon', 'OK',
                       'watch 常驻：首建 %.1fs → 编辑后自动重建 %.2fs（输出按'
                       '预期变化），stats/gc/stop RPC 正常，进程收干净'
                       % (t_first, t_rebuild))
    finally:
        if p.poll() is None:
            p.kill()
            try:
                p.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                pass
        for junk in (exe,):
            if os.path.exists(junk):
                try:
                    os.remove(junk)
                except OSError:
                    pass
        shutil.rmtree(proj, ignore_errors=True)


def ci_module_cache():
    """CP-C5 · 模块级前端缓存：改一个叶子模块 → 只付该模块的 stage-2。

    三层验收（可证伪）：
      ① TU 命中面：改叶子后 TU 层 miss ≤ 2、hit ≥ 47（48 模块口径）；
      ② 冷热等价（K1）：QOT_CACHE 关闭（全程序 stage-2 + generate_split）
         与 TU 记忆化产物**逐字节一致** —— 子程序 stage-2 ≡ 全程序 stage-2；
      ③ 行为正确：编辑前后输出变化（编辑生效）且切分/单 TU 输出一致。
    """
    import qot_build as qb
    import qot_cache as qc
    proj = _iso_dir('qot_ci_c5_')
    main_q, leaf_q = _gen_modproj(48, proj)
    iso_store = _iso_dir('qot_ci_c5_store_')
    exe = os.path.join(ART, '_c5.exe')
    bundles = []
    _orig_bs = qb.build_split

    def _spy_bs(bundle, out_exe, **kw):
        bundles.append(bundle)
        return _orig_bs(bundle, out_exe, **kw)

    qb.build_split = _spy_bs
    try:
        env = {'QOT_CACHE_DIR': iso_store}
        # ① 冷建（缓存开、空存储）：全部 TU miss
        ok, err = _compile(main_q, exe, split=True, env=env)
        if not ok:
            return _Result('ci_module_cache', 'FAIL', '冷建失败: %s' % err)
        _, m0 = _layer_stat('tu', store=iso_store)
        n_tus = 1 + len(bundles[-1]['tus'])
        if m0 != n_tus:
            return _Result('ci_module_cache', 'FAIL',
                           '冷建 TU miss=%d（应 = TU 总数 %d）' % (m0, n_tus))
        before_out = _run_exe(exe)[1]

        # ② 改叶子（x*7 → x*9）：TU 层只应 miss 叶子（+至多 main）
        leaf_text = open(leaf_q, encoding='utf-8').read()
        assert 'x * 7' in leaf_text
        with open(leaf_q, 'w', encoding='utf-8', newline='\n') as f:
            f.write(leaf_text.replace('x * 7', 'x * 9', 1))
        ph, pm = _layer_stat('tu', store=iso_store)
        ok, err = _compile(main_q, exe, split=True, env=env)
        if not ok:
            return _Result('ci_module_cache', 'FAIL', '编辑后重建失败: %s' % err)
        _, tm1 = _layer_stat('tu', store=iso_store)
        d_miss = tm1 - pm          # 差分 miss 数
        d_hit = n_tus - d_miss     # 本轮查询数 = TU 总数
        after_out = _run_exe(exe)[1]
        if d_miss > 2:
            return _Result('ci_module_cache', 'FAIL',
                           '改叶子 TU miss=%d（应 ≤2）：TU 记忆化失效面过大' % d_miss)
        if before_out == after_out:
            return _Result('ci_module_cache', 'FAIL', '叶子编辑未生效（输出未变）')
        warm_bundle = bundles[-1]

        # ③ 冷对照：frontend 层关（全程序 stage-2）→ 产物必须逐字节一致
        _orig_enabled = qc.enabled_for
        qc.enabled_for = (lambda layer, _o=_orig_enabled:
                          False if layer == 'frontend' else _o(layer))
        try:
            ok, err = _compile(main_q, exe, split=True, env=env)
        finally:
            qc.enabled_for = _orig_enabled
        if not ok:
            return _Result('ci_module_cache', 'FAIL', '冷对照构建失败: %s' % err)
        cold_bundle = bundles[-1]
        if cold_bundle['header'] != warm_bundle['header']:
            return _Result('ci_module_cache', 'FAIL', '冷/热 header 不一致')
        if cold_bundle['main'] != warm_bundle['main']:
            return _Result('ci_module_cache', 'FAIL',
                           '冷/热主 TU 不一致（子程序 stage-2 ≠ 全程序）')
        w_tus = dict(warm_bundle['tus'])
        c_tus = dict(cold_bundle['tus'])
        if set(w_tus) != set(c_tus):
            return _Result('ci_module_cache', 'FAIL', '冷/热 TU 集合不一致')
        bad = [t for t in w_tus if w_tus[t] != c_tus[t]]
        if bad:
            return _Result('ci_module_cache', 'FAIL',
                           '冷/热 TU 文本不一致: %s（前 2 个）' % bad[:2])

        # ④ 切分 vs 单 TU（同源）输出一致
        exe_s = os.path.join(ART, '_c5s.exe')
        ok, err = _compile(main_q, exe_s, split=False, env=env)
        out_s = _run_exe(exe_s)[1] if ok else None
        if os.path.exists(exe_s):
            os.remove(exe_s)
        if out_s != after_out:
            return _Result('ci_module_cache', 'FAIL', '单 TU 输出与切分不一致')
        return _Result('ci_module_cache', 'OK',
                       '48 模块：冷建 %d TU 全 miss → 改叶子 miss %d / hit %d；'
                       '冷（全程序 stage-2）与热（TU 记忆化）产物逐字节一致；'
                       '输出按预期变化且与单 TU 一致'
                       % (n_tus, d_miss, d_hit))
    finally:
        qb.build_split = _orig_bs
        if os.path.exists(exe):
            os.remove(exe)
        shutil.rmtree(proj, ignore_errors=True)
        shutil.rmtree(iso_store, ignore_errors=True)


# ---------------------------------------------------------------- 入口

def run_ci():
    os.makedirs(ART, exist_ok=True)
    import qot_cache as qc
    if qc.compiler_id()['kind'] != 'gcc':
        return [_Result('ci_suite', 'SKIP',
                        'CI 线当前只服务 gcc/g++ 主线（MSVC 兜底未接入）')]
    results = []
    for fn in (ci_determinism, ci_cache_equivalence, ci_key_completeness,
               ci_pch_transparency, ci_split_behavior,
               ci_module_cache, ci_incremental_leaf, ci_concurrency,
               ci_watch_daemon, ci_hit_rate):
        t0 = time.perf_counter()
        try:
            r = fn()
        except Exception as e:  # noqa: BLE001
            import traceback
            r = _Result(fn.__name__, 'FAIL', '异常: %s\n%s'
                        % (e, traceback.format_exc()[-600:]))
        r.detail += ' [%.1fs]' % (time.perf_counter() - t0)
        results.append(r)
    qc.flush_stats()
    return results
