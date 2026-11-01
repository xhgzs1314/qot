# -*- coding: utf-8 -*-
"""QOT 原生（后端 A）基准台架 ——  S-04 /  §6.4。

为什么不用「跑一次 exe 减掉固定启动底噪」：
  G-7 已记录 /MT 静态 CRT 的进程启动 ≈9-10ms，而它的抖动与负载、磁盘缓存、
  杀软扫描同量级。直接相减会把 10ms 级的误差灌进 30ms 级的信号里。

这里用**两点差分法**（同一程序编两份，迭代数 N 与 N/2）：
    per_unit = (t(N) - t(N/2)) / (units(N) - units(N/2))
两份程序的常量池、代码结构、进程启动路径完全一致 → 启动开销在差分中精确抵消，
剩下的就是 N/2 次迭代的纯成本。这是唯一能把「空循环 ns/iter」量到可信精度的办法。

迭代数口径（M0 统一）：
  所有负载（.qot/.py/.php）里的迭代数字面量一律写成哨兵 ITER_SENTINEL。
  台架只替换这一个哨兵，其余代码逐字不变，保证两点差分的两份程序除循环次数外
  完全同构。此前每个基准各带一条正则（10000000 / 100000）的做法已废止——
  那种写法下"哪个数字是迭代数"靠人记，改一行代码就可能悄悄换掉被测对象。

用法:
  python testing/bench_native.py                  # 跑全部基准，取 min，打印表格
  python testing/bench_native.py --repeat 7        # 每档重复 7 次
  python testing/bench_native.py --cases empty_loop,b1a_strcat_long
  python testing/bench_native.py --quick           # 小 N 冒烟：验证全部负载可编可跑
  python testing/bench_native.py --verify          # 正确性四方差分（native/VM/interp/py/php）
  python testing/bench_native.py --lang            # 追加 py/php 两点差分对照
  python testing/bench_native.py --longrun         # B3b 长跑存活性 + RSS 泄漏判定
  python testing/bench_native.py --record --tag "M0 基线"
  python testing/bench_native.py --rebuild         # 强制重新 AOT 编译
  python testing/bench_native.py --compare HEAD    # 与 jsonl 历史中最近一次对比
  python testing/bench_native.py --emit-c          # 保留生成的 .cpp 便于人工审阅

退出码: 0 成功 / 2 参数错误 / 3 环境缺失 / 4 正确性校验失败
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
TESTING = HERE
ROOT = os.path.dirname(HERE)
for p in (ROOT, TESTING):
    if p not in sys.path:
        sys.path.insert(0, p)

PY = sys.executable
ART = os.path.join(TESTING, '.artifacts', 'bench')
HISTORY = os.path.join(TESTING, 'perf', 'native-history.jsonl')

# ---------------------------------------------------------------- 迭代数哨兵
# 选一个"不可能在业务代码里自然出现"的 8 位数字；正则加了左右边界断言，
# 防止 876543210 或 1.87654321 这类误命中。
ITER_SENTINEL = 87654321
SENTINEL_RE = r'(?<![0-9.])' + str(ITER_SENTINEL) + r'(?![0-9.])'

# ---------------------------------------------------------------- 工作量换算
# per_unit 的分母不是「N - N/2」，而是「units(N) - units(N/2)」。
# 对线性负载两者相同；对树递归（深度 n 对应 2^n 次调用）必须显式换算，
# 否则 per_unit 会差出几个数量级。


def _u_identity(n):
    return n


def _u_calls(n):
    """线性/互递归：深度 n 对应 n+1 次调用。"""
    return n + 1


def _u_tree(n):
    """二叉递归：深度 n 对应 2^(n+1)-1 次调用（叶子返回的那层也算）。"""
    return (1 << (n + 1)) - 1


def _u_keys101(n):
    """b2c：每轮迭代遍历 101 个键。"""
    return n * 101


def _u_mutual(n):
    """b4c：每轮一次 iseven(2000) = 2001 次互递归调用。"""
    return n * 2001


# ---------------------------------------------------------------- 负载清单
# 字段：
#   key        基准名（唯一）
#   category   §6.2 七类基准矩阵的类别
#   unit       每单位工作的名字（进报表）
#   n          主档位迭代数（两点差分的 N）
#   n_list     多档位时给出列表，展开成 key@N 多个条目
#   units      工作量换算函数（默认 _u_identity）
#   verify_n   正确性校验用的小规模 N（要求各引擎都能秒级跑完）
#   lang_n     py/php 对照用的 N（对照语言慢，可以比 native 档小）
#   expect     安全约束：'ok' 必须成功；'ok_or_halt' 允许确定性报错但禁止崩溃
#   note       负载说明（进报表与文档）
def _stem(path):
    b = os.path.basename(path)
    return b[:b.rindex('.')]


def _load(key, category, unit, n, note, **kw):
    qot = kw.pop('qot', 'testing/bench/%s.qot' % key)
    d = {
        'key': key,
        'qot': qot,
        'py': kw.pop('py', 'testing/bench/%s.py' % _stem(qot)),
        'php': kw.pop('php', 'testing/bench/%s.php' % _stem(qot)),
        'category': category, 'unit': unit, 'n': n, 'note': note,
        'units': kw.pop('units', _u_identity),
        'verify_n': kw.pop('verify_n', 200),
        'lang_n': kw.pop('lang_n', None),
        'expect': kw.pop('expect', 'ok'),
        # verify_skip_vm：原为「VM 冻结不实现闭包/异常」的逐个负载豁免，
        # 【 用户裁决：VM 线彻底弃管（不碰/不测/其报错不管）】→
        # 默认全局 True：四方差分一律退化为三方（native/interp/py/php）。
        # 保留 QOT_VM_SUITE=1 逃生开关供考古比对。
        'verify_skip_vm': kw.pop('verify_skip_vm',
                                 os.environ.get('QOT_VM_SUITE', '0') != '1'),
        'timeout': kw.pop('timeout', 240),
        'n_list': kw.pop('n_list', None),
        # longrun_n：长跑专用 N 档（生命周期须 ≥ ~2s 才够 8 个采样点；主差分
        # 仍用 n。缺省 = n）。longrun_only：只进 --longrun 的对照臂，N 档按
        # 长跑窗口选，主表差分数字无意义 → 默认跳过（--cases 显式指名可跑）。
        'longrun_n': kw.pop('longrun_n', None),
        'longrun_only': kw.pop('longrun_only', False),
        # 长跑预期语义（判读口径的一部分，跑之前写死）：
        #   'bounded' = 有界工作集，RSS 必须平台化，恒速增长即真泄漏；
        #   'linear'  = arena-free 恒定流量负载，RSS 恒速线性涨是设计账面
        #               （斜率须与「每轮净分配字节 × 轮速」对账，见里程碑文档）；
        #   None      = 未声明，只给形状判定（STABLE/LINEAR_GROWTH/ACCELERATING），
        #               语义判读留给文档。
        'longrun_expect': kw.pop('longrun_expect', None),
    }
    if kw:
        raise TypeError('未知字段: %r' % sorted(kw))
    return d


_LOADS = [
    # ---- 控制流地板（既有，口径已迁到哨兵）----
    _load('empty_loop', '混合负载', 'iter', 100000000,
          '空循环：var s=0; for(i in N){ s += 1; }（累加防消除）',
          verify_n=1000),
    _load('order_engine', '混合负载', 'order', 1000000,
          '业务引擎：分支×5 + 函数调用 + 下标 + 浮点（小规模混合基准）',
          verify_n=200),

    # ---- B1 字符串密集 ----
    # b1a N 档定标（M-PERF 收尾，负载定义变更记录在案，非行为变更）：
    # 追加资格分析 + ipappend 落地后 O(n²) 项已灭，N=40000 档的两点差分掉进
    # 噪声门限（M-PERF 轮记 LOW_SIGNAL），判据数字失效。N 档加大到 1e7：
    # 预期 ~5-15ns/append，delta ≈ 5e6 × 10ns = 50ms > 20ms 门限，拿到干净的
    # 极致判定。lang_n 保持 40000：py 的 `s = s + "x"` 无 CPython 原地扩容
    # 优化（那是 `s += x` 的 refcount 特权），O(n²) 项在 py 侧仍然真实存在，
    # 大档会跑不完；php 同理。py/php 比值因此偏保守（对照侧吃亏），不虚报。
    _load('b1a_strcat_long', '字符串密集', 'app', 10000000,
          '长串拼接 O(n²) 探针：N 次 s = s + "x"，拷问是否摊还'
          '（M-PERF 收尾起 N 档 4e4→1e7，lang_n 维持 4e4）',
          lang_n=40000, verify_n=200),
    _load('b1b_strcat_short', '字符串密集', 'str', 2000000,
          '短串构造：每轮 strings(i) + "-tail" 后丢弃，拷问短命对象分配',
          verify_n=200),
    _load('b1d_strscan', '字符串密集', 'char', 10000000,
          '单遍扫描：1e7 字符长串上做 N 次 s[i] 取字符 + 比较（s[i] 每次新分配）',
          verify_n=200),

    # ---- B2 字典密集 ----
    _load('b2a_dict_const_key', '字典密集', 'get', 10000000,
          '常量键查找：5 键热表 N 次 d["alpha"]（隔离探测成本）',
          verify_n=200),
    _load('b2b_dict_dyn_insert', '字典密集', 'ins', 300000,
          '动态键插入：N 次 m[strings(i)] = i（键构造 + 开放寻址 + rehash 摊还）',
          verify_n=200),
    _load('b2c_dict_iter', '字典密集', 'key', 20000,
          '全表迭代：N 轮 dict_keys(m) 物化 101 键数组 + 逐键回查',
          units=_u_keys101, verify_n=20),

    # ---- B3 分配密集 ----
    _load('b3a_short_lived', '分配密集', 'obj', 1000000,
          '短命对象：每轮构造 数组+字典+短串 各一后立即丢弃（裸 malloc 拷问）',
          # 长跑臂 N 档（收尾四重定标）：arena 回收 + 1 字符驻留后轮速快了
          # 一个量级，原 3e6 档生命周期只剩 0.3s（< 8 个采样点 → NO_DATA）。
          # 提到 3e7：生命周期 ~3s ≥ 采样下限，且 RSS 已被 arena 压平
          # （收尾三实测 692KB 平台），峰值仍在安全区 —— 台架控 N，不裸跑。
          longrun_n=30000000,
          # 判读口径重挂（收尾四，重跑前写死，不是看菜下饭）：arena 回收落地
          # 后 b3a 的轮内分配全部落在回滚区、每轮从同一 bump 偏移原地复用，
          # 工作集【有界】（收尾三实测 692KB 平台、本轮 N 提到 3e7 仍 860KB）。
          # 于是 longrun_expect 从 'linear'（arena-free 时代的"池持有账面"）
          # 改为 'bounded'：现在它若还恒速涨，就是真泄漏，不再有"账面"托词。
          longrun_expect='bounded',
          verify_n=200),
    _load('b3c_alloc_bounded', '分配密集', 'hit', 10000000000,
          '有界工作集（B3b 对照臂）：预建 10 键表 + 复用键，纯查找零分配，'
          '长跑 RSS 必须平坦；只进 --longrun',
          longrun_only=True, longrun_expect='bounded', lang_n=100000, verify_n=200),

    # ---- B4 深层递归（多档：安全档 / 深档 / 危险档）----
    # 档位贴着 AOT 的实测上限选：3000 安全档、5000 边界档（= qotrt_max_depth）、
    # 6000 超限档（期望确定性报错 exit 124 而非崩溃）。三档合起来才叫
    # 「每档附安全上限验证」（§6.4 B4 的硬要求）。
    _load('b4a_rec_linear', '深层递归', 'frame', None,
          '线性递归 sumdown(n)：深度 = N，单位 = 每层栈帧 + 调用 + 返回',
          n_list=[3000, 5000, 6000], units=_u_calls,
          verify_n=200, expect='ok_or_halt'),
    _load('b4b_rec_tree', '深层递归', 'call', None,
          '树递归 tsum(d) 二叉：深度 = N，调用数 = 2^(N+1)-1',
          n_list=[18, 22], units=_u_tree, verify_n=8, expect='ok_or_halt'),
    _load('b4c_rec_mutual', '深层递归', 'frame', 20000,
          '互递归 iseven/isodd 交替：深度固定 2000，重复 N 轮放大（兼做前向引用回归）',
          units=_u_mutual, verify_n=20, expect='ok'),

    # ---- B6 大数组 ----
    _load('b6a_array_push', '大数组', 'push', None,
          '大数组 push 扩容到 N：拷问增长因子与摊还代价（1e7 级 ≈160MB）',
          n_list=[1000000, 10000000], verify_n=200),
    _load('b6b_array_rw', '大数组', 'read', 10000000,
          '定长 1e6 数组顺序读 N 次（工作集超末级缓存，拷问下标路径带宽）',
          verify_n=200),

    # ---- B7 大字典 ----
    _load('b7a_dict_insert', '大字典', 'ins', 1000000,
          '大字典插入：1e6 字符串键（rehash 时刻记录 delta_iqr 毛刺）',
          qot='testing/bench/b2b_dict_dyn_insert.qot',
          longrun_n=10000000,  # 长跑臂：1e7 键 ~3.9s / 峰值 ~1GB（表+键缓冲），台架控 N
          verify_n=200),
    _load('b7b_dict_lookup', '大字典', 'get', 5000000,
          '大字典命中查找：1e6 键冷表 + 每轮重新构造动态键',
          verify_n=200),

    # ---- B5 异常路径（F2 落地回填，
    # VM 冻结不实现 try/catch → 差分退化为三方（native/interp/py/php）。
    _load('b5a_try_overhead', '异常路径', 'iter', 10000000,
          'try 热路径：N 轮小算术全部包在 try/catch（零 throw），量「进 try '
          '区」单价（着陆点入栈 + setjmp + 出区摘除）；无 try 程序零开销是'
          '结构性成立，本负载给「有 try 区间」定价',
          verify_n=200, verify_skip_vm=True),
    _load('b5b_throw_catch', '异常路径', 'throw', 1000000,
          '抛接：N 轮 throw 预建 err 对象 + 立即 catch（payload 复用，'
          '对象分配账面归 B3/B8b；量 longjmp + 摘除 + catch 绑定全链）',
          verify_n=200, verify_skip_vm=True),

    # ---- B8 闭包/调用扩展（F1 落地回填，）----
    _load('b8a_closure_call', '闭包调用', 'call', 10000000,
          '闭包调用：N 次 CALL_DYNAMIC 分派 + env 安装/摘除 + 捕获读+写'
          '（F1 性能纪律的 ns 级账单口径）',
          verify_n=200, verify_skip_vm=True),
    _load('b8b_closure_make', '闭包调用', 'make', 1000000,
          '闭包创建：N 轮 make_adder(i%%7) 建 env+闭包对象并调用一次'
          '（内存账面 = arena-free 池持有，RSS 判读归长跑臂）',
          verify_n=200, verify_skip_vm=True),
]


def _build_benches():
    out = {}
    for L in _LOADS:
        ns = L.pop('n_list', None) or [L['n']]
        for n in ns:
            cfg = dict(L)
            cfg['n'] = n
            key = L['key'] if len(ns) == 1 else '%s@%d' % (L['key'], n)
            cfg['key'] = key
            out[key] = cfg
    return out


BENCHES = _build_benches()

# 启动底噪参考（G-7）：只作参考列，不参与 per_unit 计算
STARTUP_PROBE = 'var s = 0;\noutput(s);\n'

# 两点差分的信噪比门限：配对差分量的绝对值低于它，说明「信号 < 噪声」，
# 数字不可用于判定（只可用于冒烟）。来源见 BENCHES 注释的经验值 delta ≥ 50ms；
# 这里取 20ms 作为「可讨论」的下限，兼顾递归类负载受深度上限压制的现实。
MIN_DELTA_S = 0.020

# Windows 结构化异常 → 可读名。出现这些 = 进程被操作系统杀掉，
# 即宪法 6 明令禁止的「跑飞的用户代码带崩宿主」。
WIN_FATAL = {
    -1073741819: 'ACCESS_VIOLATION(0xC0000005)',
    -1073741571: 'STACK_OVERFLOW(0xC00000FD)',
    -1073741676: 'INT_DIVIDE_BY_ZERO(0xC0000094)',
    -1073741675: 'INT_OVERFLOW(0xC0000095)',
    -1073741811: 'NO_MEMORY(0xC0000017)',
    -1073741502: 'DLL_INIT_FAILED(0xC0000142)',
}


def _log(msg):
    try:
        print(msg, flush=True)
    except UnicodeEncodeError:
        print(msg.encode('gbk', 'replace').decode('gbk'), flush=True)


def _median(xs):
    ys = sorted(xs)
    if not ys:
        return 0.0
    return ys[len(ys) // 2]


def _fatal_name(rc):
    return WIN_FATAL.get(rc)


# ---------------------------------------------------------------- 变体生成
def _gen_variant(src_path, n, out_path):
    """把源码里的迭代数哨兵替换为 n，写出 out_path。

    命中次数必须恰好为 1：0 次说明负载没用哨兵（口径漂移），
    >1 次说明被测对象不止一处（差分会把别的循环也算进来）。
    """
    with open(src_path, 'r', encoding='utf-8') as f:
        src = f.read()
    new, cnt = re.subn(SENTINEL_RE, str(n), src)
    if cnt != 1:
        raise SystemExit('基准 %s 的迭代数哨兵命中 %d 次（应为 1）——'
                         '负载必须用 %d 作为唯一迭代数字面量'
                         % (src_path, cnt, ITER_SENTINEL))
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, 'w', encoding='utf-8', newline='\n') as f:
        f.write(new)
    return out_path


# ---------------------------------------------------------------- 编译
# 编译链源文件：任一比 exe 新就必须重编（否则会拿旧产物冒充新优化的数字）
PIPELINE_SRCS = ['index.py', 'lir.py', 'lir_lower.py', 'lir_opt.py',
                 'lir_cgen.py', 'lir2.py', 'bytecode.py', 'grammar.py',
                 'builtin_func.py', 'lir_ssa.py',
                 os.path.join('ot-asset', 'c', 'aot', 'qot_aot_runtime.h')]


def _pipeline_stamp():
    t = 0.0
    for rel in PIPELINE_SRCS:
        p = os.path.join(ROOT, rel)
        if os.path.exists(p):
            t = max(t, os.path.getmtime(p))
    return t


def _aot(qot, exe, emit_c=False):
    """AOT 编译。exe 比「源 + 整条编译链」都新才复用。返回 (ok, 末行原因)。"""
    if os.path.exists(exe):
        newest = max(os.path.getmtime(qot), _pipeline_stamp())
        if os.path.getmtime(exe) >= newest:
            return True, ''
    cmd = [PY, 'index.py', 'aot', qot, exe]
    if emit_c:
        cmd.append('--keep-c')
    try:
        subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                       encoding='utf-8', errors='replace', timeout=300)
    except subprocess.TimeoutExpired:
        return False, 'AOT 编译超时'
    if os.path.exists(exe):
        return True, ''
    return False, 'AOT 未产出 exe'


def _compiler_ok():
    """M-PACK：编译器探测切换为 g++（与 index.py 管线一致）。"""
    return shutil.which('g++') is not None or shutil.which('gcc') is not None


# ---------------------------------------------------------------- 计时
def _run_proc(args, timeout, cwd=None):
    """跑一次进程，返回 (秒, returncode, stdout, stderr)。"""
    t0 = time.perf_counter()
    p = subprocess.run(args, cwd=cwd or ROOT, stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE, timeout=timeout)
    el = time.perf_counter() - t0
    out = p.stdout.decode('utf-8', 'replace') if p.stdout else ''
    err = p.stderr.decode('utf-8', 'replace') if p.stderr else ''
    return el, p.returncode, out, err


def _interleave_times(fn_a, fn_b, repeat):
    """交错测量（A/B/A/B...）：两档在同一时间窗内交替跑，机器负载/频率漂移对
    两者是共模的，配对相减即被抵消。相比「各跑 N 次取 min 再相减」，
    配对差分的中位数在噪声机器上稳定得多（本机实测抖动从 ±14% 降到 ±2%）。"""
    pairs, ta, tb = [], [], []
    for _ in range(repeat):
        a = fn_a()
        b = fn_b()
        ta.append(a)
        tb.append(b)
        pairs.append(a - b)
    ps = sorted(pairs)
    return {
        't_n': min(ta), 't_h': min(tb),
        't_n_med': _median(ta), 't_h_med': _median(tb),
        'delta': ps[len(ps) // 2],
        # 四分位距而非极差：极差会被偶发的后台任务拉爆（本机实测有 0.27s 的
        # 离群帧，而中位数稳定在 0.105s），IQR 才反映真实抖动。
        'iqr': (ps[len(ps) // 4], ps[min(len(ps) - 1, 3 * len(ps) // 4)]),
    }


# ---------------------------------------------------------------- 主跑法
def run_one(name, cfg, repeat, rebuild, emit_c, quick_n=None):
    n = int(quick_n) if quick_n else cfg['n']
    half = n // 2
    if half < 1:
        raise SystemExit('基准 %s 的 N=%d 太小，无法两点差分' % (name, n))
    work = os.path.join(ART, cfg['key'])
    os.makedirs(work, exist_ok=True)
    tag_n = '%s_n%d' % (cfg['key'], n)

    q_n = _gen_variant(os.path.join(ROOT, cfg['qot']), n,
                       os.path.join(work, tag_n + '.qot'))
    q_h = _gen_variant(os.path.join(ROOT, cfg['qot']), half,
                       os.path.join(work, '%s_n%d.qot' % (cfg['key'], half)))
    e_n = os.path.join(work, tag_n + '.exe')
    e_h = os.path.join(work, '%s_n%d.exe' % (cfg['key'], half))
    if rebuild:
        for p in (e_n, e_h):
            if os.path.exists(p):
                os.remove(p)
    ok, why = _aot(q_n, e_n, emit_c)
    if not ok:
        return {'name': name, 'status': 'AOT_FAIL', 'detail': why, 'n': n}
    ok, why = _aot(q_h, e_h, emit_c)
    if not ok:
        return {'name': name, 'status': 'AOT_FAIL', 'detail': why, 'n': n}

    rc_bad = {}

    def ta():
        el, rc, _o, _e = _run_proc([e_n], cfg['timeout'])
        rc_bad['n'] = rc
        return el

    def tb():
        el, rc, _o, _e = _run_proc([e_h], cfg['timeout'])
        rc_bad['h'] = rc
        return el

    m = _interleave_times(ta, tb, repeat)

    # 安全约束（宪法 6）：进程被操作系统杀掉 = 没通过，不管数字多漂亮
    fatals = {k: _fatal_name(v) for k, v in rc_bad.items() if _fatal_name(v)}
    rc_n = rc_bad.get('n', 0)
    status = 'OK'
    detail = ''
    if fatals:
        status = 'CRASH'
        detail = '进程致命退出 ' + ', '.join('%s=%s' % kv for kv in sorted(fatals.items()))
    elif rc_n != 0:
        if cfg['expect'] == 'ok_or_halt':
            status = 'HALT'
            detail = '退出码 %d（安全降级：确定性报错而非崩溃）' % rc_n
        else:
            status = 'RC_FAIL'
            detail = '退出码 %d' % rc_n
    elif abs(m['delta']) < MIN_DELTA_S:
        # 信噪比护栏。两点差分要求差分量远大于调度噪声（经验 ≥20ms）。
        # 递归类负载受 qotrt_max_depth=5000 限制，单档最多几千层，
        # delta 常在 µs 级——实测 b4c 甚至测出负值（噪声大于信号）。
        # 这类数字不能进档案当结论，必须显式标记而不是静默记一个漂亮值。
        status = 'LOW_SIGNAL'
        detail = ('delta=%.4fs < 门限 %.0fms：差分量淹没在调度噪声里，数字仅供参考，'
                  '不可用于判定（可加大 N 或提高 --repeat）'
                  % (m['delta'], MIN_DELTA_S * 1000))

    units_n = cfg['units'](n)
    units_h = cfg['units'](half)
    denom = units_n - units_h
    per_unit = (m['delta'] / denom * 1e9) if denom > 0 else float('nan')

    r = {
        'name': name, 'key': cfg['key'], 'category': cfg['category'],
        'unit': cfg['unit'], 'n': n, 'half': half, 'status': status,
        'detail': detail, 'rc': rc_n,
        't_n_min': m['t_n'], 't_h_min': m['t_h'],
        't_n_med': m['t_n_med'], 't_h_med': m['t_h_med'],
        'delta': m['delta'], 'delta_iqr': m['iqr'],
        'units_n': units_n, 'units_h': units_h,
        'ns_per_unit': per_unit,
    }
    return r


# ---------------------------------------------------------------- 跨语言对照
def _lang_cmd(path, n, kind, work):
    src = os.path.join(ROOT, path)
    if not os.path.exists(src):
        return None
    ext = kind
    out = os.path.join(work, '%s_%s_n%d.%s' % (os.path.basename(path)[:-4], kind, n, ext))
    _gen_variant(src, n, out)
    if kind == 'py':
        return [PY, out]
    # PHP 的 array 是哈希表，1e7 元素要 1GB+；不给足限额会死在 memory_limit 上，
    # 那测的就不是我们要的东西了。
    return ['php', '-d', 'memory_limit=3G', '-d', 'zend.assertions=-1', out]


def run_lang(cfg, kind, repeat, quick_n=None):
    n = int(quick_n) if quick_n else (cfg.get('lang_n') or cfg['n'])
    half = n // 2
    work = os.path.join(ART, '_lang', cfg['key'])
    os.makedirs(work, exist_ok=True)
    fa = _lang_cmd(cfg[kind], n, kind, work)
    fb = _lang_cmd(cfg[kind], half, kind, work)
    if not fa:
        return None
    rc_bad = {}

    def ta():
        el, rc, _o, _e = _run_proc(fa, cfg['timeout'])
        rc_bad['n'] = rc
        return el

    def tb():
        el, rc, _o, _e = _run_proc(fb, cfg['timeout'])
        rc_bad['h'] = rc
        return el

    m = _interleave_times(ta, tb, repeat)
    denom = cfg['units'](n) - cfg['units'](half)
    return {
        'lang': kind, 'n': n, 'rc': rc_bad.get('n', 0),
        'ns_per_unit': (m['delta'] / denom * 1e9) if denom > 0 else float('nan'),
        'delta': m['delta'], 'iqr': m['iqr'],
        'crash': _fatal_name(rc_bad.get('n', 0)),
    }


# ---------------------------------------------------------------- 正确性校验
def _norm_line(s):
    """数值归一化：把可解析成浮点/整数的行统一格式。

    为什么这里必须归一化而 testing/run.py 拒绝归一化：run.py 比的是 QOT 的两个
    后端（VM vs native），数字格式差异恰恰是缺陷信号，掩盖了就是放水。
    本函数比的是**三种不同语言**，它们打印数字的规矩天然不同
    （QOT 走 %f → "4.000000"，Python → "4"，PHP → "4"），
    不归一化就永远不可能相等。归一化规则写在这里、显式且唯一。
    """
    t = s.strip()
    if not t:
        return None
    try:
        v = float(t)
    except ValueError:
        return t
    if v == int(v) and abs(v) < 1e15:
        return '%d' % int(v)
    return '%.6f' % v


def _norm_out(text):
    out = []
    for ln in (text or '').replace('\r\n', '\n').replace('\r', '\n').split('\n'):
        v = _norm_line(ln)
        if v is not None:
            out.append(v)
    return out


# 严格裁判组：产品发布面（native）+ 开发面（VM）+ 两种对照语言。
# 这四者必须逐行一致，否则说明负载在某一侧算错了 —— 基准跑得快但算错是
# 最坏的结果，它会被记进历史档案，污染后面所有里程碑的对比基线。
STRICT_GROUP = ('native', 'vm', 'py', 'php')

# 参考组：Python 解释器。ROADMAP §3.2 R3 已把它退役为「开发期 oracle」，
# 差分裁判从三方缩为 VM vs native。它在本轮负载上的缺口是**它退役的原因
# 本身**，不是本轮要修的对象（例如 glen 不接受 dict、递归深度受 Python
# 调用栈限制）。但缺口必须显式列出，不许静默 —— 见返回值里的 oracle_warn。
ORACLE_GROUP = ('interp',)


def verify_one(cfg, root_msvc=True):
    """小规模四方差分：native / VM / py / php 严格一致，interp 作为 oracle 参考。"""
    n = cfg['verify_n']
    work = os.path.join(ART, '_verify', cfg['key'])
    os.makedirs(work, exist_ok=True)
    qot = _gen_variant(os.path.join(ROOT, cfg['qot']), n,
                       os.path.join(work, 'v_n%d.qot' % n))
    outs = {}

    # native
    exe = os.path.join(work, 'v_n%d.exe' % n)
    ok, why = _aot(qot, exe)
    if ok:
        el, rc, o, e = _run_proc([exe], cfg['timeout'])
        outs['native'] = (_norm_out(o), rc, _fatal_name(rc))
    else:
        outs['native'] = (['<AOT_FAIL:%s>' % why], -1, None)

    # VM（.qtc）；F1/F2 负载按 verify_skip_vm 退出判据（VM 冻结）
    if not cfg.get('verify_skip_vm'):
        qtc = os.path.join(work, 'v_n%d.qtc' % n)
        if os.path.exists(qtc):
            os.remove(qtc)
        subprocess.run([PY, 'index.py', 'bstrust', 'compile', qot, qtc],
                       cwd=ROOT, capture_output=True, text=True,
                       encoding='utf-8', errors='replace', timeout=120)
        if os.path.exists(qtc):
            el, rc, o, e = _run_proc([PY, 'index.py', 'bstrust', 'run', qtc], cfg['timeout'])
            outs['vm'] = (_norm_out(o), rc, None)
        else:
            outs['vm'] = (['<COMPILE_FAIL>'], -1, None)

    # 解释器（开发期 oracle）
    el, rc, o, e = _run_proc([PY, 'index.py', 'run', qot], cfg['timeout'])
    outs['interp'] = (_norm_out(o), rc, None)

    # 对照语言
    for kind in ('py', 'php'):
        cmd = _lang_cmd(cfg[kind], n, kind, work)
        if cmd:
            el, rc, o, e = _run_proc(cmd, cfg['timeout'])
            outs[kind] = (_norm_out(o), rc, _fatal_name(rc))

    # 以 native 为基准（native 是发布面）
    base = outs['native'][0]
    diffs, oracle_warn = [], []
    for k in sorted(outs):
        if outs[k][0] == base:
            continue
        item = '%s=%r' % (k, outs[k][0])
        (diffs if k in STRICT_GROUP else oracle_warn).append(item)
    return {
        'name': cfg['key'], 'n': n, 'base': base,
        'outs': {k: v[0] for k, v in outs.items()},
        'rcs': {k: v[1] for k, v in outs.items()},
        'fatals': {k: v[2] for k, v in outs.items() if v[2]},
        'ok': not diffs, 'diffs': diffs, 'oracle_warn': oracle_warn,
    }


# ---------------------------------------------------------------- B3b 长跑
def _slope(xs, ys):
    """最小二乘斜率（KB/s）。用于把 RSS 轨迹分成前/后两半比恒定性。"""
    n = len(xs)
    if n < 2:
        return 0.0
    mx = sum(xs) / n
    my = sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    den = sum((x - mx) ** 2 for x in xs)
    return num / den if den else 0.0


def _rss_kb(pid):
    """读进程私有内存（PrivateUsage）。Windows 无 psutil 时的最小实现。"""
    import ctypes
    from ctypes import wintypes

    class PMC_EX(ctypes.Structure):
        _fields_ = [
            ('cb', wintypes.DWORD), ('PageFaultCount', wintypes.DWORD),
            ('PeakWorkingSetSize', ctypes.c_size_t), ('WorkingSetSize', ctypes.c_size_t),
            ('QuotaPeakPagedPoolUsage', ctypes.c_size_t), ('QuotaPagedPoolUsage', ctypes.c_size_t),
            ('QuotaPeakNonPagedPoolUsage', ctypes.c_size_t), ('QuotaNonPagedPoolUsage', ctypes.c_size_t),
            ('PagefileUsage', ctypes.c_size_t), ('PeakPagefileUsage', ctypes.c_size_t),
            ('PrivateUsage', ctypes.c_size_t),
        ]

    h = ctypes.windll.kernel32.OpenProcess(0x1000 | 0x0400, False, pid)  # QUERY_INFO|READ
    if not h:
        return None
    try:
        pmc = PMC_EX()
        pmc.cb = ctypes.sizeof(PMC_EX)
        if ctypes.windll.psapi.GetProcessMemoryInfo(h, ctypes.byref(pmc), pmc.cb):
            return pmc.PrivateUsage // 1024
        return None
    finally:
        ctypes.windll.kernel32.CloseHandle(h)


def longrun(cfg, seconds=30.0, interval=0.1):
    """B3b 长跑存活性：持续跑一个分配密集负载，周期性采样 RSS。

    N 档：cfg['longrun_n'] 优先（生命周期须 ≥ ~2s 才够 8 个采样点），
    缺省用 cfg['n']。进程在窗口内自然退出时曲线覆盖完整生命周期
    （比强凑 30s 更符合「轨迹对账」语义）。

    【判读口径 —— 跑之前写死（M-PERF 收尾），不许看菜下饭】
    池分配器是 arena-free：slab / 空闲表设计上只增不减。因此「池持有」
    与「真泄漏」不能用「涨没涨」区分。先做两件机械预处理，再做语义判定：

    预处理 1（终止回落段截断）：进程退出时 OS 回收页，RSS 尾部骤降不是
      泄漏证据 —— 丢弃 RSS 峰值时刻之后的样本，只对递增段判定。
      （真泄漏进程在窗口内不退出、曲线不回落，此截断对它无影响。）
    预处理 2（斜率恒定性）：递增段前后半各做最小二乘拟合，slope× = 后半/前半。

    语义判定（负载的 longrun_expect 字段，跑之前写死）：
      * expect='bounded'（b3c 零分配有界臂；b3a 自【收尾四】起也归此档）
          增幅 ≤ 20% → STABLE（必须落在这里）；
          否则恒速涨 → LEAKY（应有界却恒速涨 = 每轮隐藏分配 = 真泄漏确凿）；
          加速涨   → LEAKY_SUSPECT。
      * expect='linear'（恒定流量臂；本字段自收尾四起暂无负载使用）
          恒速涨 → LINEAR_BY_DESIGN（arena-free 设计账面；总量须与
                   「每轮净分配字节 × 轮速」对账，收尾一实测 558B/轮 vs
                   理论 576B/轮，见里程碑文档）；
          加速涨 → LEAKY_SUSPECT。
        【口径变更记录】b3a 原属本档（arena-free 时代的「池持有账面」）。
        收尾三的循环 arena 回收让 b3a 的轮内分配全部落在回滚区、每轮从
        同一 bump 偏移原地复用 → 工作集变成有界（收尾三 692KB、收尾四
        N=3e7 仍 816KB）。口径因此在【重跑之前】改挂 'bounded'：现在它若
        再恒速涨，就没有「账面」托词，是真泄漏。
      * 未声明语义（b6a/b6b/b7a/b7b）
          只给形状：STABLE / LINEAR_GROWTH / ACCELERATING，语义判读进文档。

    输出列 growth（递增段前后 1/4 中位数增幅）、KB/s（递增段平均速率）、
    slope×（后半斜率 ÷ 前半斜率；前半 < 1KB/s 视为平坦不参与比值）。
    """
    n = cfg.get('longrun_n') or cfg['n']
    work = os.path.join(ART, '_longrun', cfg['key'])
    os.makedirs(work, exist_ok=True)
    qot = _gen_variant(os.path.join(ROOT, cfg['qot']), n,
                       os.path.join(work, 'lr_n%d.qot' % n))
    exe = os.path.join(work, 'lr_n%d.exe' % n)
    ok, why = _aot(qot, exe)
    if not ok:
        return {'name': cfg['key'], 'status': 'AOT_FAIL', 'detail': why}

    p = subprocess.Popen([exe], cwd=ROOT, stdout=subprocess.DEVNULL,
                         stderr=subprocess.PIPE)
    samples = []
    t0 = time.perf_counter()
    while True:
        alive = p.poll() is None
        el = time.perf_counter() - t0
        if not alive or el >= seconds:
            break
        samples.append((el, _rss_kb(p.pid)))
        time.sleep(interval)
    elapsed = time.perf_counter() - t0
    if p.poll() is None:
        samples.append((elapsed, _rss_kb(p.pid)))
        p.kill()
    p.wait(timeout=10)
    rc = p.returncode
    used = [(t, s) for t, s in samples if s]
    # 预处理 1：削掉退出回落段（见函数头 docstring）。只丢「与全局峰值相比
    # 回落 >2%」的尾部 —— 不能截到峰值时刻：平台型负载（b3c 零分配臂）
    # 开局即峰值，截到峰值会把整段平台误删。
    if used:
        peak = max(s for _, s in used)
        last_hi = max(i for i, (_, s) in enumerate(used) if s >= peak * 0.98)
        used = used[:last_hi + 1]
    verdict = 'NO_DATA'
    growth = None
    kb_per_s = None
    slope_ratio = None
    accel = False
    if len(used) >= 8:
        k = max(1, len(used) // 4)
        a = _median([s for _, s in used[:k]])
        b = _median([s for _, s in used[-k:]])
        growth = (b - a) / a if a else 0.0
        span = used[-1][0] - used[0][0]
        kb_per_s = (used[-1][1] - used[0][1]) / span if span > 0 else 0.0
        half = len(used) // 2
        s1 = _slope([t for t, _ in used[:half]], [s for _, s in used[:half]])
        s2 = _slope([t for t, _ in used[half:]], [s for _, s in used[half:]])
        slope_ratio = (s2 / s1) if s1 > 1.0 else None  # 前半 < 1KB/s = 平坦
        accel = slope_ratio is not None and slope_ratio > 2.0
        expect = cfg.get('longrun_expect')
        if expect == 'bounded':
            # 有界臂：平台化 = 健康；恒速/加速涨 = 真泄漏
            verdict = ('LEAKY_SUSPECT' if accel else
                       'LEAKY' if growth > 0.20 else 'STABLE')
        elif expect == 'linear':
            # 恒定流量臂：恒速线性 = 设计账面；加速 = 泄漏形状
            verdict = 'LEAKY_SUSPECT' if accel else 'LINEAR_BY_DESIGN'
        else:
            # 无语义声明：只给形状
            verdict = ('ACCELERATING' if accel else
                       'STABLE' if growth <= 0.20 else 'LINEAR_GROWTH')
    return {
        'name': cfg['key'], 'n': n, 'status': verdict,
        'rc': rc, 'fatal': _fatal_name(rc) if rc else None,
        'elapsed_s': round(elapsed, 2), 'samples': len(used),
        'rss_first_kb': used[0][1] if used else None,
        'rss_peak_kb': max(s for _, s in used) if used else None,
        'rss_last_kb': used[-1][1] if used else None,
        'growth': growth, 'kb_per_s': kb_per_s, 'slope_ratio': slope_ratio,
    }


# ---------------------------------------------------------------- 报表
def _fmt_table(results):
    lines = ['%-24s %-8s %10s %6s %12s %10s %14s  %s'
             % ('case', 'category', 'N', 'stat', 'delta s', '±spread', 'ns/unit', 'unit')]
    for r in results:
        if r.get('status') != 'OK':
            lines.append('%-24s %-8s %10d %6s %s'
                         % (r['name'], r.get('category', '-'), r.get('n', 0),
                            r.get('status'), r.get('detail', '')))
            continue
        sp = r.get('delta_iqr', (r['delta'], r['delta']))
        lines.append('%-24s %-8s %10d %6s %12.4f %10.4f %14.3f  %s'
                     % (r['name'], r['category'], r['n'], 'OK', r['delta'],
                        sp[1] - sp[0], r['ns_per_unit'], r['unit']))
    return lines


def _compare(rec, selector):
    hist = _load_history()
    if not hist:
        return ['', '(无历史可比较)']
    if selector == 'last' or selector == 'HEAD':
        base = hist[-1]
    else:
        cands = [h for h in hist if h.get('tag') == selector]
        if not cands:
            return ['', '(历史中无 tag=%r 的记录)' % selector]
        base = cands[-1]
    out = ['', '与基线对比  baseline: %s %s' % (base.get('commit'), base.get('tag') or '')]
    prev = {r['name']: r for r in base.get('results', [])}
    for r in rec['results']:
        p = prev.get(r['name'])
        if p is None:
            out.append('  %-24s  基线无此项' % r['name'])
            continue
        if r.get('ns_per_unit') is None or p.get('ns_per_unit') is None:
            continue
        d = p['ns_per_unit'] - r['ns_per_unit']
        pct = (d / p['ns_per_unit'] * 100) if p['ns_per_unit'] else 0.0
        verdict = '更快' if d > 0 else ('更慢' if d < 0 else '持平')
        out.append('  %-24s %10.3f -> %10.3f ns/unit  %+7.1f%%  %s'
                   % (r['name'], p['ns_per_unit'], r['ns_per_unit'], pct, verdict))
    return out


def _git_commit():
    try:
        r = subprocess.run(['git', 'rev-parse', '--short', 'HEAD'],
                           cwd=ROOT, capture_output=True, text=True, timeout=10)
        return r.stdout.strip() or 'unknown'
    except Exception:
        return 'unknown'


def _load_history():
    if not os.path.exists(HISTORY):
        return []
    out = []
    with open(HISTORY, 'r', encoding='utf-8') as f:
        for ln in f:
            ln = ln.strip()
            if ln:
                try:
                    out.append(json.loads(ln))
                except ValueError:
                    continue
    return out


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description='QOT 原生（后端 A）基准台架')
    ap.add_argument('--repeat', type=int, default=5)
    ap.add_argument('--cases', default='', help='逗号分隔的子集，默认全部')
    ap.add_argument('--rebuild', action='store_true')
    ap.add_argument('--emit-c', action='store_true')
    ap.add_argument('--record', action='store_true',
                    help='写入 testing/perf/native-history.jsonl')
    ap.add_argument('--tag', default='')
    ap.add_argument('--json', action='store_true')
    ap.add_argument('--compare', default='', help='与历史中指定 tag 的记录比较（或 last）')
    ap.add_argument('--quick', action='store_true',
                    help='小 N 冒烟：只验证全部负载可编可跑，不产生可比数字')
    ap.add_argument('--verify', action='store_true',
                    help='正确性四方差分（native/VM/interp/py/php），必须先 --quick 之外全过')
    ap.add_argument('--lang', action='store_true', help='追加 py/php 两点差分对照')
    ap.add_argument('--longrun', action='store_true', help='B3b 长跑存活性 + RSS 泄漏判定')
    ap.add_argument('--longrun-seconds', type=float, default=30.0)
    ap.add_argument('--matrix', help='把 §6.2 矩阵数据写成 JSON')
    args = ap.parse_args()

    names = [c.strip() for c in args.cases.split(',') if c.strip()] or list(BENCHES)
    for nm in names:
        if nm not in BENCHES:
            _log('[bench] 未知基准: %s' % nm)
            return 2

    # longrun_only 负载（b3c 有界对照臂）的 N 档按长跑窗口选（1e10），
    # 数字主表差分既跑不完也无意义 → 主差分**无条件**跳过（防呆：1e10 档
    # 进差分必然超时）；--quick/--verify 保留（verify_n=200 快）。longrun
    # 阶段按 args.cases 原始指名单独纳入。
    if not args.quick and not args.verify:
        names = [nm for nm in names if not BENCHES[nm].get('longrun_only')]

    if args.quick:
        results = []
        for nm in names:
            cfg = BENCHES[nm]
            # 用 verify_n 本身而不是它的倍数：树递归类负载的工作量是 2^n，
            # 乘个常数会让它从「毫秒」直接跳到「小时」。quick 只负责回答
            # 「能不能编、能不能跑、退不退出」，精度由正式档位负责。
            qn = max(2, int(cfg['verify_n']))
            _log('[quick] %s (N=%d) ...' % (nm, qn))
            r = run_one(nm, cfg, 1, False, False, quick_n=qn)
            results.append(r)
            _log('        -> %s %s' % (r.get('status'),
                                       '' if r.get('status') == 'OK' else r.get('detail', '')))
        # LOW_SIGNAL 在 quick 下是**预期结果**而非异常：小 N 的差分量必然
        # 低于信噪比门限（这正是门限存在的意义）。quick 只回答「能不能编、
        # 能不能跑、退不退出」这三件事，不回答「有多快」。
        ok_states = ('OK', 'HALT', 'LOW_SIGNAL')
        bad = [r for r in results if r.get('status') not in ok_states]
        _log('\n[quick] %d/%d 可编可跑；异常 %d'
             % (len(results) - len(bad), len(results), len(bad)))
        return 1 if bad else 0

    if args.verify:
        return _main_verify(names)

    if not _compiler_ok():
        _log('[bench] 未找到 g++/gcc，无法编译原生基准')
        return 3

    results = []
    for nm in names:
        _log('[bench] %s ...' % nm)
        results.append(run_one(nm, BENCHES[nm], args.repeat, args.rebuild, args.emit_c))

    langs = {}
    if args.lang:
        for nm in names:
            entry = {}
            for kind in ('py', 'php'):
                try:
                    entry[kind] = run_lang(BENCHES[nm], kind, max(3, args.repeat // 2))
                except Exception as ex:  # 对照语言缺席不该拖垮主表
                    entry[kind] = {'lang': kind, 'error': str(ex)}
            langs[nm] = entry
            _log('[lang] %s: %s' % (nm, ', '.join(
                '%s=%s' % (k, ('%.2f ns/u' % v['ns_per_unit'])
                           if v and v.get('ns_per_unit') == v.get('ns_per_unit') else 'n/a')
                for k, v in entry.items())))

    longruns = []
    if args.longrun:
        # longrun 阶段独立选臂：分配密集/大数组/大字典全部 + longrun_only 对照臂
        # （b3c 不进主差分但必须进长跑）。--cases 指名时按原始指名筛选。
        case_names = set(c.strip() for c in args.cases.split(',') if c.strip())
        for nm, bc in BENCHES.items():
            if bc['category'] not in ('分配密集', '大数组', '大字典') \
                    and not bc.get('longrun_only'):
                continue
            if args.cases and nm not in case_names:
                continue
            _log('[longrun] %s (%.0fs) ...' % (nm, args.longrun_seconds))
            longruns.append(longrun(bc, args.longrun_seconds))

    # 启动底噪参考（不参与差分）
    startup = None
    probe_qot = os.path.join(ART, '_startup_probe.qot')
    with open(probe_qot, 'w', encoding='utf-8', newline='\n') as f:
        f.write(STARTUP_PROBE)
    probe_exe = os.path.join(ART, '_startup_probe.exe')
    if _aot(probe_qot, probe_exe)[0]:
        startup = min(_run_proc([probe_exe], 30)[0] for _ in range(args.repeat))

    rec = {
        'ts': time.strftime('%Y-%m-%dT%H:%M:%S'),
        'commit': _git_commit(),
        'tag': args.tag,
        'repeat': args.repeat,
        'startup_s': startup,
        'python': sys.version.split()[0],
        'results': results,
        'langs': langs,
        'longruns': longruns,
    }
    if args.record:
        os.makedirs(os.path.dirname(HISTORY), exist_ok=True)
        with open(HISTORY, 'a', encoding='utf-8') as f:
            f.write(json.dumps(rec, ensure_ascii=False) + '\n')
        _log('[bench] 已记录 -> %s' % HISTORY)

    if args.matrix:
        with open(args.matrix, 'w', encoding='utf-8') as f:
            json.dump(_matrix(results, langs), f, ensure_ascii=False, indent=1)
        _log('[bench] 矩阵 -> %s' % args.matrix)

    if args.json:
        print(json.dumps(rec, ensure_ascii=False, indent=2))
        return 0

    out = ['', '基准（两点差分，启动开销精确抵消）  commit=%s  tag=%s'
           % (rec['commit'], args.tag or '-')]
    if startup is not None:
        out.append('进程启动底噪参考: %.1f ms（G-7；不参与计算）' % (startup * 1000))
    out.append('-' * 100)
    out += _fmt_table(results)
    if args.lang:
        out += _fmt_lang(results, langs)
    if longruns:
        out += _fmt_longrun(longruns)
    if args.compare:
        out += _compare(rec, args.compare)
    # 走 _log 而不是 print：表里含中文类别名，GBK 控制台下 print 会抛
    # UnicodeEncodeError，把整张报表连同前面的进度一起吞掉。
    _log('\n'.join(out))
    return 0


def _main_verify(names):
    bad = 0
    warned = []
    for nm in names:
        v = verify_one(BENCHES[nm])
        if v['ok']:
            _log('[ OK ] %-24s N=%-8d %r' % (v['name'], v['n'], v['base'][:4]))
        else:
            bad += 1
            _log('[FAIL] %-24s N=%-8d' % (v['name'], v['n']))
            for k in sorted(v['outs']):
                _log('         %-7s rc=%-5s %r%s'
                     % (k, v['rcs'].get(k), v['outs'][k][:6],
                        '  FATAL:%s' % v['fatals'][k] if v['fatals'].get(k) else ''))
        if v['oracle_warn']:
            warned.append((v['name'], v['oracle_warn']))
    if warned:
        _log('')
        _log('解释器（oracle，R3 已退役）分歧 —— 记录但不阻塞：')
        for nm, w in warned:
            _log('  [WARN] %-24s %s' % (nm, '; '.join(x.split('=')[0] for x in w)))
    _log('\n[verify] 严格组一致 %d/%d' % (len(names) - bad, len(names)))
    return 4 if bad else 0


def _fmt_lang(results, langs):
    if not langs:
        return []
    out = ['', '跨语言对照（同为两点差分；比值 = 对照语言 ns/unit ÷ QOT native ns/unit）',
           '%-24s %14s %14s %14s %10s %10s'
           % ('case', 'qot ns/unit', 'py ns/unit', 'php ns/unit', 'py÷qot', 'php÷qot')]
    for r in results:
        e = langs.get(r['name']) or {}
        if r.get('status') != 'OK':
            continue
        q = r['ns_per_unit']
        py = (e.get('py') or {}).get('ns_per_unit')
        ph = (e.get('php') or {}).get('ns_per_unit')
        if py is None and ph is None:
            continue
        def fmt(v):
            return ('%14.2f' % v) if isinstance(v, float) else '%14s' % 'n/a'

        def rat(v):
            return ('%10.1fx' % (v / q)) if (isinstance(v, float) and q > 0) else '%10s' % 'n/a'
        out.append('%-24s %14.2f %s %s %s %s'
                   % (r['name'], q, fmt(py), fmt(ph), rat(py), rat(ph)))
    return out


def _fmt_longrun(lrs):
    if not lrs:
        return []
    out = ['', '长跑存活性 / RSS（B3b；口径见 longrun() 注释：终止回落段已截断，只判递增段；',
           '  语义按负载 longrun_expect：bounded 臂必须 STABLE，linear 臂恒速涨 = LINEAR_BY_DESIGN（对账后即账面），',
           '  无语义负载给形状：STABLE / LINEAR_GROWTH / ACCELERATING（加速 = 真泄漏形状）',
           '%-24s %16s %6s %10s %11s %11s %9s %8s %8s'
           % ('case', 'status', 'rc', 'elapsed s', 'first KB', 'peak KB', 'growth', 'KB/s', 'slope×')]
    for r in lrs:
        out.append('%-24s %16s %6s %10.2f %11s %11s %9s %8s %8s'
                   % (r['name'], r['status'], r['rc'], r['elapsed_s'],
                      r['rss_first_kb'], r['rss_peak_kb'],
                      ('%+.1f%%' % (r['growth'] * 100)) if r['growth'] is not None else '-',
                      ('%.0f' % r['kb_per_s']) if r.get('kb_per_s') is not None else '-',
                      ('%.2f' % r['slope_ratio']) if r.get('slope_ratio') is not None else '-'))
    return out


def _matrix(results, langs):
    by_cat = {}
    for r in results:
        if r.get('status') != 'OK':
            continue
        c = by_cat.setdefault(r['category'], {'cases': [], 'best_ns': None})
        e = (langs.get(r['name']) or {})
        c['cases'].append({
            'name': r['name'], 'n': r['n'], 'unit': r['unit'],
            'ns_per_unit': r['ns_per_unit'],
            'py_ns': (e.get('py') or {}).get('ns_per_unit'),
            'php_ns': (e.get('php') or {}).get('ns_per_unit'),
        })
    return by_cat


if __name__ == '__main__':
    sys.exit(main())
