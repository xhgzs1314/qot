# -*- coding: utf-8 -*-
"""LIR → C 源码发射器 ——  7.4 机器码 P3 / T-42（后端 A）。

三件套的兑现方式（T-43，与 §6.3 对应）：
  * 消 dispatch：LIR 线性指令流 → 直线 C 语句（switch 循环不复存在）；
  * 消栈搬运：每个 LIR 槽 → 一个 C 局部变量（/O2 落寄存器），无 push/pop；
  * 消间接调用：小函数已在 LIR 层内联（lir_opt.inline_calls_with_tables），
    内置调用在编译期直接绑定到 qotrt_* 符号。

T-44：f64 指令发射为「guard + 快路径 + 通用兜底」双路径，guard 失败执行该指令的
原始通用语义（行为恒等），由差分测试守卫。

发射出的 C 是自包含单 TU：#include qot_aot_runtime.h + 程序代码。
"""
import re
import sys

import qot_builtins as _qb
import qot_hash as _qh
from lir import (
    LirProgram,
    CONST_F64, CONST_VAL, LOAD_F64, STORE_F64, MOV_V, MOV_F,
    ADD_F, SUB_F, MUL_F, DIV_F, MOD_F, POW_F, CMP_F, NEG_F,
    ARITH_GEN, UNARY_GEN, BR_IF_NOT, BR_IF, BR_F, JMP, RET_V, RET_F,
    CALL_USER, CALL_BUILTIN, CALL_MODULE, CALL_DYNAMIC,
    INDEX_GET, INDEX_SET, MAKE_ARRAY, MAKE_DICT, REQUIRE, STOP, NOP,
    GUARD_NUM, GLOBAL_BIT, ARENA_PUSH, ARENA_RESET,
    THROW, TRY_PUSH, TRY_POP, ENV_NEW, MAKE_CLOSURE, THROWVAL, UPVAL_BIT,
    FIELD_GET, FIELD_SET, CALL_OMETHOD,
)


def _upval_levels(s):
    return (s >> 20) & 0x1FF


def _upval_idx(s):
    return s & 0xFFFFF


def _is_upval(s):
    return s >= 0 and bool(s & UPVAL_BIT)

RUNTIME_INCLUDE = 'qot_aot_runtime.h'

# ARITH_GEN 子码 → C 运算符 / 运行时函数（键是子码 1..6，不是 F64 指令号）
ARITH_OPS = {1: '+', 2: '-', 3: '*'}
ARITH_FNS = {4: 'qotrt_fdiv', 5: 'qotrt_fmod_', 6: 'pow'}
# 比较子码 → C 关系运算符
CMP_PRED_TEXT = {7: '>', 8: '<', 9: '>=', 10: '<=', 11: '==', 12: '!='}

# AOT 内置函数支持集：名字 → qotrt 符号后缀。
# 【M1】不再在这里手写名单 —— 由 builtins.toml 派生（engines 含 native 的
# 条目），别名单列由清单的 aliases 字段带出。这里只保留一次对账调用：
# 清单与 AOT 侧若不一致，导入即失败（G-5 的两侧漂移不可能再静默存在）。
SUPPORTED_BUILTINS = _qb.aot_supported()
_qb.check_aot_symbols(SUPPORTED_BUILTINS)

# N-05 内置快速通道：argc==1 时直连的运行时核心入口（qot_aot_runtime.h 提供，
# 核心入口委托 argc 版本，语义单一来源不变）。
BUILTIN_CORE1 = {
    '__iter_len': 'qotrt_iter_len1',
    '__is_number': 'qotrt_is_number1',
    'glen': 'qotrt_glen1',
    'type': 'qotrt_type1',
    'gtypes': 'qotrt_gtypes1',
    'strings': 'qotrt_strings1',
    'numbers': 'qotrt_numbers1',
}


class CgenError(Exception):
    pass


def _cd(x):
    """把 Python 浮点格式化为 C 的 double 字面量（所有立即数发射的唯一出口）。

    为什么不能直接 %.17g：整数值的 %.17g 不带小数点（2147483648.0 →
    "2147483648"）。落在 (2^31, 2^32) 的整数字面量在 C 里不是 double——
    MSVC 按经典规则解析为 unsigned long，`0 - 2147483648` 会回绕成
    +2147483648（mod_semantics_edge 差分当场抓住的既有 AOT 语义缺陷）。
    这里保证无小数点/无指数时补 ".0"；非有限值走 <cmath> 的 HUGE_VAL/NAN。
    """
    v = float(x)
    s = '%.17g' % v
    if s in ('inf',):
        return 'HUGE_VAL'
    if s in ('-inf',):
        return '(-HUGE_VAL)'
    if s == 'nan':
        return '(double)NAN'
    if not any(ch in s for ch in '.eE'):
        s += '.0'
    return s


def _sanitize(name):
    s = re.sub(r'[^A-Za-z0-9_]', '_', name or 'fn')
    if not s or s[0].isdigit():
        s = '_' + s
    return s


class Cg(object):
    # 「上帝法则」安全内置白名单：这些内置**不保留**任何实参引用（读完即弃），
    # 因此把它们当纯读取对待不破坏追加资格分析。push/dict_set/dict_keys 等会
    # 持有/借用实参的内置一律按逃逸处理（保守即安全）。
    SAFE_BUILTINS = {'output', 'glen', 'strings', 'numbers', 'type', 'gtypes',
                     '__is_number', '__iter_len', 'dict_get'}

    def __init__(self, prog, fast_snaps=None):
        self.prog = prog
        self.lines = []
        # QOT-CI L1（CP-C2）：切分 TU 模式下用户函数/快入口去 static（跨 TU
        # 链接）。单 TU 路径恒为 'static'（产物与历史逐字节一致）。
        self.linkage = 'static'
        self.label_n = 0
        pool = getattr(prog, 'const_pool', [])
        self.consts = list(pool or [])
        # 名字 → LirFunction
        self.fn_by_name = {}
        for f in prog.funcs.values():
            if f.name:
                self.fn_by_name[f.name] = f
        self.call_graph = {}   # LirFunction → set(LirFunction)（检测递归）
        self._uc = {}          # id(LirFunction) → v 槽读取次数（融合/死代码判定）
        self._ape = {}         # id(LirFunction) → 追加资格槽集合（缓存）
        # N-04 双体快速入口（QOT_NO_FCALL=1 关闭，A/B 归因）
        import os as _os
        # ---- 上帝法则（配置化取舍，开发者自担）----
        # QOT_UNSAFE_INDEX=1：发射下标读写时移除边界检查（越界 = 未定义行为，
        # 通常是读出垃圾/写坏内存）。默认关闭 —— 语言默认仍保证「越界得 null/
        # 静默忽略」；要极限性能的发布构建由开发者显式打开本配置自担边界。
        self.unsafe_index = _os.environ.get('QOT_UNSAFE_INDEX') == '1'
        # A/B 开关（G-14 约定：pass 自检）
        self.do_append = _os.environ.get('QOT_NO_APPEND') != '1'
        self.do_idxfuse = _os.environ.get('QOT_NO_IDXFUSE') != '1'
        # E3：常量键哈希编译期折叠（QOT_NO_CONSTKEY=1 关闭做 A/B 归因）。
        # 归零验证进 _dbg_switches.py（产物里 /*constkey*/ 标记计数归零）。
        self.do_constkey = _os.environ.get('QOT_NO_CONSTKEY') != '1'
        self.n_constkey = 0
        self.fast = None
        if fast_snaps and _os.environ.get('QOT_NO_FCALL') != '1':
            self.fast = FastPlanner(prog, fast_snaps)
        # ---- F1/F2 发射期状态（严格 per-function，emit_func 内更新）----
        self._cur_env0 = None       # 当前函数的 env0 C 变量名（None=无环境）
        self._cur_volatile = False  # 当前函数含 try → 槽声明 volatile（setjmp 契约）
        self._try_stack = []        # 打开的着陆点变量名栈（TRY_PUSH/TRY_POP 配对）
        self.clo_index = {}         # 函数名 → 闭包入口表下标（has-env 函数）

    # ------------------------------------------------------------ 槽引用
    def _vref(self, slot):
        """槽 → C 左值表达式。三类：
          全局（bit30）   → g[idx]
          upval（bit29）  → env 链上的捕获槽（表达式的多次求值 = 多次 env_at
                            调用，幂等且廉价 —— 热路径捕获槽优化是后续项）
          局部            → s<slot>（try 函数中为 volatile）
        """
        if slot >= 0 and (slot & GLOBAL_BIT):
            return 'g[%d]' % (slot & ~GLOBAL_BIT)
        if _is_upval(slot):
            lv, ix = _upval_levels(slot), _upval_idx(slot)
            env_expr = self._cur_env0 or '0'
            if lv > 0:
                env_expr = 'qotrt_env_up(%s, %d)' % (env_expr, lv)
            return '(*qotrt_env_at(%s, %d))' % (env_expr, ix)
        return 's%d' % slot

    def _va(self, slot):
        """槽地址（V16*）。volatile 槽取址必须剥限定（运行时入口都收 V16*）。"""
        if slot >= 0 and (slot & GLOBAL_BIT):
            return '(V16*)&g[%d]' % (slot & ~GLOBAL_BIT)
        if _is_upval(slot):
            return '(V16*)%s' % self._vref_addr_of_upval(slot)
        return '(V16*)&s%d' % slot

    def _vref_addr_of_upval(self, slot):
        lv, ix = _upval_levels(slot), _upval_idx(slot)
        env_expr = self._cur_env0 or '0'
        if lv > 0:
            env_expr = 'qotrt_env_up(%s, %d)' % (env_expr, lv)
        return 'qotrt_env_at(%s, %d)' % (env_expr, ix)

    def emit(self, s):
        self.lines.append(s)

    # ------------------------------------------------------------ 常量池
    def emit_consts(self):
        # qotrt_consts / qotrt_nconsts / qotrt_const 已在 qot_aot_runtime.h 声明
        self.emit('static void qotrt_init_consts(void) {')
        self.emit('  qotrt_nconsts = %d;' % len(self.consts))
        self.emit('  qotrt_consts = (V16*)qotrt_xalloc(sizeof(V16) * %d);' % max(1, len(self.consts)))
        for idx, v in enumerate(self.consts):
            if isinstance(v, bool):
                self.emit('  qotrt_consts[%d] = qotrt_bool(%s);' % (idx, 'true' if v else 'false'))
            elif isinstance(v, (int, float)):
                self.emit('  qotrt_consts[%d] = qotrt_num(%s);' % (idx, _cd(v)))
            elif isinstance(v, str):
                esc = v.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n').replace('\r', '\\r').replace('\t', '\\t')
                # 【0 裁决反转】空串字面量 = 空字符串，不再是 null。
                # 历史：D-10（0 fuzz）发现 VM 常量装载层把 "" 规范化为
                # null，当时裁判=VM，AOT 跟随同构（bound_empty_string_vm 钉）。
                # 0 转向后解释器是唯一 oracle（S-13 口径），interp 侧
                # "" 是合法字符串值（bound_empty_string_interp 钉）——std.text
                # 的 `field = ""` 初始化在 AOT 下变 null 引爆此分歧（宪法 6）。
                # 现裁决：AOT 向 interp 对齐；VM 侧保持冻结原状（其钉只跑
                # --suite vm，§8.7 冻结条款不要求零分歧）。fuzz 禁用空串原子
                # 的禁令同步解除（生成器侧另行处理）。
                self.emit('  qotrt_consts[%d] = qotrt_str_shared("%s", %d, 0);' % (idx, esc, len(v.encode('utf-8'))))
            else:
                # VMCode（函数值）/None → null 占位（AOT 不支持把函数当值使用，
                # CALL_USER 全部在编译期绑定到符号，运行期不读这类常量）
                self.emit('  qotrt_consts[%d] = qotrt_null_v();' % idx)
        self.emit('}')

    # ------------------------------------------------------------ 函数
    def collect_calls(self):
        for f in self.prog.all_funcs():
            s = set()
            nt = getattr(f, 'name_table', None) or []
            for i in f.instrs:
                if i.op == CALL_USER and 0 <= i.func_idx < len(nt):
                    callee = self.fn_by_name.get(nt[i.func_idx])
                    if callee is not None:
                        s.add(callee)
            self.call_graph[f] = s

    def _emit_backedge_tick(self, i, pc, out):
        """S-03：回边（目标 ≤ 当前 pc 的 JMP/BR）注入 tick 检查。

        N-02 修订：先判 `qotrt_tick_limit >= 0` 再自增。未设 QOT_TIMEOUT_MS 时
        tick_limit == -1，原写法仍会无条件 `++qotrt_ticks`——那是一次对静态变量的
        读-改-写，落在最热的循环里就是每迭代一次内存往返（空循环实测 ns/iter
        的显著组成）。改成条件守卫后，快路径只剩一次（可被 cl 提到循环外的）
        加载 + 比较；未设超时时 ticks 不再累加，而 ticks 只服务于超时判定，
        没有其它观察者，语义不变（test_s03_limits 守卫）。
        """
        if i.target <= pc:
            out.append('  if (qotrt_tick_limit >= 0) { ++qotrt_ticks;')
            out.append('    if (qotrt_ticks > qotrt_tick_limit) '
                       'qotrt_fail_timeout(qotrt_tick_limit / 200000LL); }')

    def emit_func(self, f, symbol, is_main=False):
        nt = getattr(f, 'name_table', None) or []
        # ---- F1/F2 函数形态判定 ----
        has_env = self._fn_has_env(f)
        has_try = any(i.op == TRY_PUSH for i in f.instrs)
        n_caps = 0
        env_instr = next((i for i in f.instrs if i.op == ENV_NEW), None)
        if env_instr is not None:
            n_caps = int(env_instr.a)
        # 全局槽上界（本函数引用的最大全局槽号 + 1）；负数 = 哨兵，跳过
        gmax = 0
        for i in f.instrs:
            for s in ([i.dst] if i.op in (STORE_F64, CONST_F64, MOV_F) else []):
                if s >= 0 and (s & GLOBAL_BIT):
                    gmax = max(gmax, (s & ~GLOBAL_BIT) + 1)
            for s in self._slots_of(i):
                if s >= 0 and (s & GLOBAL_BIT):
                    gmax = max(gmax, (s & ~GLOBAL_BIT) + 1)
        if is_main:
            gmax = max(gmax, self._global_upper_bound())
        # 发射期状态（_vref/_va 依赖）
        self._cur_env0 = 'env0' if has_env else None
        self._cur_volatile = has_try
        self._try_stack = []
        # S-03：用户函数携带深度参数（递归护栏）；main 由 qot_main 固定签名。
        # F1：含环境函数统一 5 参（末位 = 词法父激活环境；顶层调用点传 0）。
        if is_main:
            self.emit('static void %s(V16 *ret, V16 *args, int argc) {' % symbol)
        elif has_env:
            self.emit('%s void %s(V16 *ret, V16 *args, int argc, int depth, '
                      'QEnv *envp) {' % (self.linkage, symbol))
            self.emit('  if (depth > qotrt_max_depth) qotrt_fail_depth(depth);')
        else:
            self.emit('%s void %s(V16 *ret, V16 *args, int argc, int depth) {'
                      % (self.linkage, symbol))
            self.emit('  if (depth > qotrt_max_depth) qotrt_fail_depth(depth);')
        # 局部槽声明（v 槽 0..n_slots_v-1 不含 GLOBAL_BIT 的部分）
        # 防御：形参槽 [0, n_bind_slots) 属同一编号空间，必须一并声明
        # F2：含 try 的函数槽声明 volatile —— setjmp/longjmp 契约
        # （C 标准：setjmp 之后被改写的非 volatile 自动对象跨 longjmp 不可信）。
        # 成本只落在含 try 的函数上；无 try 的程序零改动（硬验收）。
        vol = 'volatile ' if has_try else ''
        nv = max(f.n_slots_v, f.n_bind_slots)
        for s in range(nv):
            self.emit('  %sV16 s%d = qotrt_none_v();' % (vol, s))
        for s in range(f.n_slots_f):
            self.emit('  %sdouble f%d = 0.0;' % (vol, s))
        if has_env:
            # 激活环境指针；实际分配在指令 0 的 ENV_NEW 处（避免每次调用
            # 泄漏一个空环境 —— 分配只在该指令执行时发生一次）
            self.emit('  QEnv *env0 = (QEnv *)0;')
        if has_try:
            # 函数入口的着陆基准：RET/STOP/跨 try 跳转按它对齐摘除
            self.emit('  void *qot_ldbase = (void *)qotrt_landings;')
        if not is_main:
            # 形参绑定：nbind = min(argc, n_bind_slots)，顺序展开（与字节码解释器一致；
            # 未绑定的形参槽保持 VT_NONE，与 StackFrame 默认值一致）
            nb = f.n_bind_slots
            self.emit('  int nbind = argc < %d ? argc : %d;' % (nb, nb))
            for k in range(nb):
                self.emit('  if (nbind > %d) { s%d = args[%d]; }' % (k, k, k))
        body = []
        self._emit_body(f, body, nt, is_main, None if is_main else 'depth')
        for s in body:
            self.emit(s)
        if is_main:
            self.emit('  *ret = qotrt_null_v();')
        self.emit('}')
        self._cur_env0 = None
        self._cur_volatile = False
        self._try_stack = []

    def _slots_of(self, i):
        out = []
        if i.op == GUARD_NUM:
            out += list(i.args)
        if i.op in (LOAD_F64, STORE_F64, MOV_V, MOV_F, BR_IF_NOT, BR_IF, BR_F,
                    RET_V, RET_F, UNARY_GEN, NEG_F, INDEX_GET, CALL_DYNAMIC,
                    THROW, THROWVAL, ENV_NEW, MAKE_CLOSURE, FIELD_GET):
            for s in (i.dst, i.a):
                out.append(s)
        if i.op in (ARITH_GEN,):
            out += [i.dst, i.a, i.b]
        if i.op in (ADD_F, SUB_F, MUL_F, DIV_F, MOD_F, POW_F, CMP_F):
            if i.op == CMP_F:
                out.append(i.dst)
            else:
                out += [i.dst, i.a, i.b]
        if i.op in (INDEX_SET, FIELD_SET):
            out += [i.a, i.b, i.c]
        if i.op in (CALL_USER, CALL_BUILTIN, CALL_MODULE, CALL_DYNAMIC,
                    MAKE_ARRAY, MAKE_DICT, CALL_OMETHOD):
            out += list(i.args)
        return out

    def _global_upper_bound(self):
        g = 0
        for f in self.prog.all_funcs():
            for i in f.instrs:
                for s in self._slots_of(i):
                    if s >= 0 and (s & GLOBAL_BIT):
                        g = max(g, (s & ~GLOBAL_BIT) + 1)
        return g

    def _emit_body(self, f, out, nt, is_main, depth_param=None):
        # E3：常量键折叠需要「槽 → 常量池」的函数内信息，而 _emit_index_*
        # 的签名里没有 f（有调用点是融合路径的通用回退）。这里把当前函数挂在
        # self 上，由 _constkey_hash 读取 —— 发射顺序是严格 per-function 的。
        self._cur_f = f
        n = len(f.instrs)
        # F2：try 深度图（每条指令执行时的活动着陆点数）。跨 try 边界的
        # JMP/BR/RET/STOP 必须先摘除途中着陆点，否则着陆栈泄漏/错配。
        depths = {}
        d = 0
        for p in range(n + 1):
            depths[p] = d
            if p < n:
                if f.instrs[p].op == TRY_PUSH:
                    d += 1
                elif f.instrs[p].op == TRY_POP:
                    d -= 1
        self._cur_depths = depths
        pc = 0
        while pc < n:
            self._cur_pc = pc
            i = f.instrs[pc]
            if pc == 0 or self._is_jump_target(f, pc):
                out.append('L%d:;' % pc)
            # 融合优先：先试探「裸比较 + 分支」（去装箱快循环的形态），
            # 再试探「带 guard 的比较组 + 分支」，再试「下标+算术融合」，
            # 再试「独占追加」，最后才是通用组。
            consumed = self._try_fused_cmp_branch(f, pc, out)
            if not consumed:
                consumed = self._try_f64_group(f, pc, out)
            if not consumed:
                consumed = self._try_fused_idx_arith(f, pc, out)
            if not consumed:
                consumed = self._try_append_pattern(f, pc, out)
            if consumed:
                pc += consumed
                continue
            if i.op in (JMP, BR_IF_NOT, BR_IF, BR_F):
                self._emit_backedge_tick(i, pc, out)
            out.extend(self._emit_instr(f, i, nt, pc, is_main, depth_param))
            pc += 1
        if is_main:
            out.append('  qot_stop:;')

    def _emit_try_unwind(self, i, pc, out):
        """跳转/返回跨越 try 边界时，摘除途中全部着陆点（F2 正确性关键）。

        taken 路径专用：条件分支的两条路径深度不同，只有「跳走」的那条需要
        摘除；顺序路径的深度由 TRY_POP 自己维护。
        """
        depths = getattr(self, '_cur_depths', None)
        if not depths or not self._try_stack:
            return 0
        cur = depths.get(pc, 0)
        tgt = depths.get(i.target, 0)
        if tgt >= cur:
            return 0
        k = 0
        for name in reversed(self._try_stack[tgt:cur]):
            out.append('    qotrt_landings = %s.prev;' % name)
            k += 1
        return k

    # ---------------------------------------------------------- 槽角色（M-PERF）
    def _slot_roles(self, i, nt):
        """返回 (defs, reads, escapes)：v 槽在该指令下的三种角色。

        与 lir2.slot_ops 的差异：把「读取」里会复制值出去的部分单独标为
        escapes（逃逸 = 别名可能被外部持有）。操作数完备性仍以 slot_ops
        为唯一真相源（G-2），本函数只是把 uses 再分类。
        """
        op = i.op
        if op == CONST_VAL:
            return (i.dst,), (), ()
        if op == STORE_F64:
            return (i.dst,), (), ()
        if op == LOAD_F64:
            return (), (i.a,), ()   # f0 ← v 槽取裸值：v 槽被读
        if op == CMP_F:
            return (i.dst,), (), ()  # bool 结果落地 v 槽：v 槽被定义
        if op == MOV_V:
            if i.dst == i.a:
                return (), (i.a,), ()
            return (i.dst,), (), (i.a,)
        if op == ARITH_GEN:
            return (i.dst,), (i.a, i.b), ()
        if op == UNARY_GEN:
            return (i.dst,), (i.a,), ()
        if op in (BR_IF_NOT, BR_IF):
            return (), (i.a,), ()
        if op == RET_V:
            return (), (), (i.a,)
        if op == INDEX_GET:
            return (i.dst,), (i.a, i.b), ()
        if op == INDEX_SET:
            return (), (i.a, i.b), (i.c,)
        if op in (MAKE_ARRAY, MAKE_DICT):
            return (i.dst,), (), tuple(i.args)
        if op in (CALL_USER, CALL_MODULE, CALL_DYNAMIC):
            return (i.dst,), (), tuple(i.args)
        if op == CALL_BUILTIN:
            nm = nt[i.name_idx] if 0 <= i.name_idx < len(nt) else ''
            if nm in self.SAFE_BUILTINS:
                return (i.dst,), tuple(i.args), ()
            return (i.dst,), (), tuple(i.args)
        if op == GUARD_NUM:
            return (), tuple(i.args), ()
        return (), (), ()

    def _reads_of(self, f, slot):
        """slot 在函数内的读取总次数（_uc 缓存的是全槽计数表）。"""
        cached = self._uc.get(id(f))
        if cached is None:
            cached = self._use_count(f)
        return cached.get(slot, 0)

    def _reads_outside(self, f, slot, pcs):
        """slot 在 pcs 之外指令里的读取次数（仅诊断用；守卫用 reaching-defs）。"""
        from lir2 import slot_ops
        cnt = 0
        for p, i in enumerate(f.instrs):
            if p in pcs:
                continue
            if slot in slot_ops(i)[1]:
                cnt += 1
        return cnt

    def _cfg_blocks(self, f):
        """块划分（M-PERF 分析共用）：返回 (blocks, bid_of)。

        块 = 线性指令区间；leader = 0 / 跳转 target / 跳转后继 / 末尾。
        """
        ins = f.instrs
        n = len(ins)
        leaders = {0, n}
        succs = {}
        for p, i in enumerate(ins):
            if i.op == JMP:
                succs[p] = (i.target,)
            elif i.op in (BR_IF_NOT, BR_IF, BR_F, GUARD_NUM):
                succs[p] = (p + 1, i.target)
            elif i.op in (RET_V, RET_F, STOP, THROW):
                succs[p] = ()
            else:
                succs[p] = (p + 1,)
            for t in succs[p]:
                if 0 <= t <= n:
                    leaders.add(t)
        starts = sorted(leaders)
        bid_of = {}
        blocks = []
        for bi in range(len(starts) - 1):
            st, en = starts[bi], starts[bi + 1]
            blocks.append((st, en))
            for p in range(st, en):
                bid_of[p] = bi
        return blocks, bid_of, succs

    def _reaching_defs_before(self, f, x, target_pc):
        """单槽 reaching-definitions：返回 target_pc 执行前，x 的哪些定义 pc
        可达（沿着任意控制流路径且中间无 x 的新定义）。

        为什么不用「读取计数」：SSA 槽合并后同一物理槽在不重叠的活跃区里戴
        多顶帽子（b6b 实测：槽 13 同时是循环条件 cmp 结果、fmod 落点、下标
        结果、算术操作数）——全局计数把无害的远处活跃区误判成危险。
        数据流：IN[b] = ∪ OUT[pred]，OUT[b] = {gen[b]}（块内最后一个 x 定义
        杀掉全部旧定义）。
        """
        nt = getattr(f, 'name_table', None) or []
        ins = f.instrs
        n = len(ins)
        if x < 0 or target_pc < 0 or target_pc >= n:
            return set()
        blocks, bid_of, succs = self._cfg_blocks(f)
        nb = len(blocks)
        gen = [None] * nb
        succ_b = [set() for _ in range(nb)]
        for bi, (st, en) in enumerate(blocks):
            for p in range(st, en):
                d, _r, _e = self._slot_roles(ins[p], nt)
                if x in d:
                    gen[bi] = p
            p = en - 1
            for t in succs[p]:
                if 0 <= t < n:
                    succ_b[bi].add(bid_of[t])
        preds = [set() for _ in range(nb)]
        for bi in range(nb):
            for t in succ_b[bi]:
                preds[t].add(bi)
        IN = [set() for _ in range(nb)]
        OUT = [set() for _ in range(nb)]
        changed = True
        while changed:
            changed = False
            for bi in range(nb):
                ni = set()
                for q in preds[bi]:
                    ni |= OUT[q]
                no = {gen[bi]} if gen[bi] is not None else ni
                if IN[bi] != ni or OUT[bi] != no:
                    IN[bi] = ni
                    OUT[bi] = no
                    changed = True
        st, _en = blocks[bid_of[target_pc]]
        reach = set(IN[bid_of[target_pc]])
        for q in range(st, target_pc):
            d, _r, _e = self._slot_roles(ins[q], nt)
            if x in d:
                reach = {q}
        return reach

    def _def_removed_reaches_read(self, f, x, pc, pair_pcs):
        """【配对守卫】x 在 pc 处的定义（融合将移除它）是否可达任何配对外的
        x 读取点。可达 ⇒ 那次读取会观察到被移除的定义 ⇒ 必须拒绝融合。"""
        nt = getattr(f, 'name_table', None) or []
        for p, i in enumerate(f.instrs):
            if p in pair_pcs:
                continue
            _d, r, _e = self._slot_roles(i, nt)
            if x not in r:
                continue
            if pc in self._reaching_defs_before(f, x, p):
                return True
        return False

    def _is_const_slot(self, f, slot, depth=0):
        """slot 的每个定义都是 CONST_VAL 或「从 const 槽 MOV」→ 值必为常量池对象。"""
        if depth > 8 or slot < 0 or (slot & GLOBAL_BIT):
            return False
        ins = f.instrs
        for pc, i in enumerate(ins):
            if i.op == CONST_VAL and i.dst == slot:
                continue
            if i.op == MOV_V and i.dst == slot:
                if self._is_const_slot(f, i.a, depth + 1):
                    continue
            # 其它任何以 slot 为定义者的指令（含 ARITH/STORE_F64/下标/调用）
            d, _r, _e = self._slot_roles(i, getattr(f, 'name_table', None) or [])
            if slot in d:
                return False
        return True

    def _append_eligible(self, f):
        """【追加资格分析】（M-PERF）：返回可以做独占原地追加的 v 槽集合。

        判据（全部保守，宁缺勿滥）：
          1. 槽是局部（无 GLOBAL_BIT）；
          2. 槽的每个定义都是「自拼接」：ARITH(pred=1) 且 dst==a 或 dst==b，
             或紧邻形态 [ARITH(1, D, a==S 或 b==S), MOV(S, D)]；
          3. 在每个自拼接点 C 上，C 之前可达的 S 定义只能是：
             a) 其它自拼接点（本槽私有串，独占性成立）；
             b) 常量来源定义（CONST_VAL / 从 const 槽 MOV —— 常量池对象
                shared=1，运行期由 qotrt_str_ipappend 的 shared 闸退全量
                拷贝，天然安全）；
             c) 空集（值 = VT_NONE，运行期走通用路径）；
             其它来源（CALL/下标/STORE_F64 等）一票否决 —— 那种值可能被
             别名持有，原地改写会破坏字符串不可变语义。
             用 reaching-defs 而非线性顺序：块重排后「程序尾部」可能线性
             在前（b1a 实测：output(glen(s)) 落在循环体前面的线性位置）。
          4. 槽的值从未逃逸：不做 MOV 出源 / 不进容器 / 不进（非白名单）
             调用 / 不 RET —— V16 值拷贝没有引用计数，逃逸过的串可能被
             别名持有。
        """
        cached = self._ape.get(id(f))
        if cached is not None:
            return cached
        nt = getattr(f, 'name_table', None) or []
        ins = f.instrs
        concat_pcs = {}   # slot -> [pc]
        other_defs = {}   # slot -> [pc]
        escaped = set()
        for pc, i in enumerate(ins):
            d, r, e = self._slot_roles(i, nt)
            for s in e:
                if s >= 0:
                    escaped.add(s)
            if i.op == ARITH_GEN and i.pred == 1 and i.dst >= 0 \
                    and not (i.dst & GLOBAL_BIT):
                if i.a == i.dst or i.b == i.dst:
                    concat_pcs.setdefault(i.dst, []).append(pc)
                    d = tuple(x for x in d if x != i.dst)
            if i.op == MOV_V and i.dst >= 0 and not (i.dst & GLOBAL_BIT) \
                    and i.dst != i.a and pc > 0:
                p = ins[pc - 1]
                if p.op == ARITH_GEN and p.pred == 1 and p.dst == i.a \
                        and (p.a == i.dst or p.b == i.dst):
                    concat_pcs.setdefault(i.dst, []).append(pc)
                    d = tuple(x for x in d if x != i.dst)
            for s in d:
                if s >= 0 and not (s & GLOBAL_BIT):
                    other_defs.setdefault(s, []).append(pc)

        def _const_provenance(opc):
            pi = ins[opc]
            if pi.op == CONST_VAL:
                return True
            if pi.op == MOV_V and self._is_const_slot(f, pi.a):
                return True
            return False

        elig = set()
        for s, pcs in concat_pcs.items():
            if s in escaped or not pcs:
                continue
            if _is_upval(s):
                # F1：捕获槽是跨闭包共享的 env 单元，原地追加会改写别名视角
                # —— 一票否决（共享 QStr 原地改写 = 字符串不可变语义被破坏）
                continue
            cset = set(pcs)
            ok = True
            for c in pcs:
                for o in self._reaching_defs_before(f, s, c):
                    if o in cset:
                        continue
                    if o in other_defs.get(s, []) and _const_provenance(o):
                        continue
                    ok = False
                    break
                if not ok:
                    break
            if ok:
                elig.add(s)
        self._ape[id(f)] = elig
        return elig

    def _try_append_pattern(self, f, pc, out):
        """`s = s + X` 独占追加（QOT_NO_APPEND=1 关闭）。

        两种形态：
          直写:  ARITH(1, dst==a==S, b)              → qotrt_str_ipappend(&S, &b)
          邻接:  ARITH(1, dst=D, a==S|b==S), MOV(S,D) → 同上（D 的读取必须仅此一次）
        语义：ipappend 内部与 qotrt_arith op1 逐字节一致（shared 闸兜底）。
        """
        if not self.do_append:
            return 0
        ins = f.instrs
        n = len(ins)
        i = ins[pc]
        if i.op != ARITH_GEN or i.pred != 1:
            return 0
        # 位置纪律：acc 必须在【左侧】（+ 的串拼接不可交换，`s = x + s` 的
        # 结果以 x 开头，原地追加无法表达 —— 该形态不资格，走通用路径）。
        if i.dst == i.a:
            # 直写形态：acc = dst 本体 = 左操作数
            S = i.dst
            if S < 0 or (S & GLOBAL_BIT) or _is_upval(S):
                return 0
            if S not in self._append_eligible(f):
                return 0
            self._emit_append_call(out, S, i.b, pc)
            return 1
        if pc + 1 >= n:
            return 0
        j = ins[pc + 1]
        if j.op != MOV_V or j.a != i.dst:
            return 0
        # 邻接形态：[ARITH(1, D, X, Y), MOV(S, D)]，S ∈ {X, Y}
        S = j.dst
        if (i.a != S and i.b != S):
            return 0
        D = i.dst
        if D < 0 or (D & GLOBAL_BIT) or _is_upval(D) or \
                S < 0 or (S & GLOBAL_BIT) or _is_upval(S):
            return 0
        # 被移除的定义（pc 处 ARITH 写 D）不得可达配对外任何 D 读取
        if self._def_removed_reaches_read(f, D, pc, (pc, pc + 1)):
            return 0
        if self._is_jump_target(f, pc + 1):
            return 0
        if S not in self._append_eligible(f):
            return 0
        rhs = i.b if i.a == S else i.a
        self._emit_append_call(out, S, rhs, pc)
        return 2

    def _emit_append_call(self, out, s_slot, rhs_slot, tag):
        """追加发射（数字内联 + 串追加调用双路径）。

        资格分析没有类型信息，`t = t + fib(i)` 这类数字累加器同样命中本
        形态。原 op1 发射对 NUM+NUM 有内联 f64 快路径（p04 实测：换成
        ipappend 调用会退化 1.4×），这里把它保下来：双数字 → 裸 u.d 加法
        （t 字段已是 VT_NUM，无需重装箱）；其余 → qotrt_str_ipappend
        （内部首分支同样处理 num+num，两路语义逐位一致）。
        """
        vs, vr = self._vref(s_slot), self._vref(rhs_slot)
        out.append('  if (%s.t == VT_NUM && %s.t == VT_NUM) {' % (vs, vr))
        out.append('    %s.u.d = %s.u.d + %s.u.d;' % (vs, vs, vr))
        out.append('  } else {')
        out.append('    qotrt_str_ipappend(%s, %s);'
                   % (self._va(s_slot), self._va(rhs_slot)))
        out.append('  }')

    @staticmethod
    def _emit_backedge_tick_neutral(out):
        return  # 追加发射不引入新回边；占位保持结构对称（历史保留）

    def _try_fused_idx_arith(self, f, pc, out):
        """下标读 + 紧随算术/比较 的融合（QOT_NO_IDXFUSE=1 关闭）。

        b6b/b7b/b2a 的核心形态 `t = t + a[i]` / `s = s + d["k"]`：原样发射要
        为下标结果做一次 V16 装箱落地 + qotrt_arith 通用调用的二次分派。
        融合后快路径只做「类型守卫 + 裸 double 运算」，元素/值不落地；
        任一守卫失败退回逐条原发射（下标与算术皆纯读取，重执行无副作用，
        两条路径对同一输入产出逐位一致 —— 与 T-44 双路径同一纪律）。
        """
        if not self.do_idxfuse:
            return 0
        ins = f.instrs
        n = len(ins)
        if pc + 1 >= n:
            return 0
        gi = ins[pc]
        ai = ins[pc + 1]
        if gi.op != INDEX_GET or ai.op != ARITH_GEN:
            return 0
        if not (1 <= ai.pred <= 12):
            return 0
        X = gi.dst
        if X < 0 or (X & GLOBAL_BIT) or _is_upval(X):
            return 0
        if ai.a != X and ai.b != X:
            return 0
        # 被移除的定义（pc 处 INDEX_GET 写 X）不得可达配对外任何 X 读取
        # （SSA 槽复用下 X 常戴多顶帽子 —— b6b/b2a 实测形态，见守卫函数注释）
        if self._def_removed_reaches_read(f, X, pc, (pc, pc + 1)):
            return 0
        if self._is_jump_target(f, pc + 1):
            return 0
        RO = ai.b if ai.a == X else ai.a
        rd = self._vref(ai.dst)  # 融合结果写入算术目标槽，不是下标结果槽
        rc, rx, ro = self._vref(gi.a), self._vref(gi.b), self._vref(RO)
        tag = pc
        # 【缺陷修复 0】旧实现把下标结果 _ev 恒当**左操作数**发射
        # （`_ev OP ro`）。当 ai.b == X（下标在右侧：`x > a[0]` / `t - a[i]` /
        # `t / a[i]`）时操作数被颠倒 —— 非交换运算（> < >= <= - / %）全部
        # 错序（`x > a[0]` 被编成 `a[0] > x`）。基准负载全是 `t + a[i]` 的
        # 交换形态（b6b/b2a），G-03 三元用例首次踩中。快/慢双路径中的慢
        # 路径（_emit_arith_gen 按 ai.a/ai.b 原序）一直是对的 —— 只有快
        # 路径错。修复 = 按真实左右序发射。
        idx_on_left = (ai.a == X)
        if idx_on_left:
            lhs_txt, rhs_txt = '_ev->u.d', '%s.u.d' % ro
        else:
            lhs_txt, rhs_txt = '%s.u.d' % ro, '_ev->u.d'
        if 1 <= ai.pred <= 6:
            if ai.pred in ARITH_OPS:
                expr = '%s %s %s' % (lhs_txt, ARITH_OPS[ai.pred], rhs_txt)
            else:
                expr = '%s(%s, %s)' % (ARITH_FNS[ai.pred], lhs_txt, rhs_txt)
            fast = '      %s = qotrt_num(%s);' % (rd, expr)
        else:
            fast = '      %s = qotrt_bool(%s %s %s);' % (
                rd, lhs_txt, CMP_PRED_TEXT[ai.pred], rhs_txt)
        bound = '1' if self.unsafe_index else '(_i >= 0 && _i < _a->len)'
        out.append('  { int _fz%d = 0;' % tag)
        out.append('    if (%s.t == VT_ARR && %s.t == VT_NUM) {' % (rc, rx))
        out.append('      QArr* _a = (QArr*)%s.u.p; int _i = (int)%s.u.d;' % (rc, rx))
        out.append('      if (%s) {' % bound)
        out.append('        V16* _ev = &_a->a[_i];')
        out.append('        if (_ev->t == VT_NUM && %s.t == VT_NUM) {' % ro)
        out.append(fast)
        out.append('        _fz%d = 1;' % tag)
        out.append('        }')
        out.append('      }')
        out.append('            } else if (%s.t == VT_DICT && %s.t == VT_STR) {' % (rc, rx))
        out.append('      QStr* _k = (QStr*)%s.u.p;' % rx)
        out.append('      V16* _ev = qotrt_dict_find((QDict*)%s.u.p, _k->s, _k->len,'
                   ' qotrt_vhash(%s));' % (rc, self._va(gi.b)))
        out.append('      if (_ev != 0 && _ev->t == VT_NUM && %s.t == VT_NUM) {' % ro)
        out.append(fast)
        out.append('        _fz%d = 1;' % tag)
        out.append('      }')
        out.append('    }')
        out.append('    if (!_fz%d) {' % tag)
        out.extend(self._emit_index_get(gi, self._vref))
        out.append('    }')
        out.append('    if (!_fz%d) {' % tag)
        out.extend(self._emit_arith_gen(ai, self._vref))
        out.append('    }')
        out.append('  }')
        return 2

    # ---------------------------------------------------------- 融合判定
    def _cmp_branch_only(self, f, slot):
        """slot 的**每一次**读取都形如「紧前一条 CMP_F 定义它、紧接着被 BR 消费」。

        循环版本化后同一个比较槽会被慢/快两份循环各写一次，因此「全函数只读一次」
        这个朴素判据会失效；换成这个结构判据后依然严格安全：既然任何一次读取都
        紧随其定义者，那么在快路径里省掉落地就不可能被别人观察到。
        """
        from lir2 import slot_ops
        if slot < 0 or (slot & GLOBAL_BIT) or _is_upval(slot):
            return False
        ins = f.instrs
        for pc, i in enumerate(ins):
            if slot not in slot_ops(i)[1]:
                continue
            if i.op not in (BR_IF_NOT, BR_IF) or pc == 0:
                return False
            prev = ins[pc - 1]
            if prev.op != CMP_F or prev.dst != slot:
                return False
        return True

    # ---------------------------------------------------------- 使用计数
    def _use_count(self, f):
        """v 槽在本函数内的读取次数（融合/死代码判定用）。"""
        cached = self._uc.get(id(f))
        if cached is not None:
            return cached
        from lir2 import slot_ops
        cnt = {}
        for i in f.instrs:
            for s in slot_ops(i)[1]:
                if s >= 0:
                    cnt[s] = cnt.get(s, 0) + 1
        self._uc[id(f)] = cnt
        return cnt

    def _operand(self, ins, k):
        """组操作数：返回 (fast_expr, guard_ref or None)。

        X ∈ {LOAD_F64（V 槽取裸值，需 guard）、MOV_F（已是裸 double，无 guard）、
        CONST_F64（编译期常数，无 guard；imm 直接进表达式）}。
        """
        i = ins[k]
        if i.op == LOAD_F64:
            r = self._vref(i.a)
            return '%s.u.d' % r, r
        if i.op == MOV_F:
            return 'f%d' % i.a, None
        if i.op == CONST_F64:
            return _cd(i.imm), None
        return None

    @staticmethod
    def _builtin_core(nm, i):
        """返回该 CALL_BUILTIN 可直连的运行时核心入口名；不适用返回 None。

        N-05（QOT_NO_BICORE=1 关闭，A/B 归因）：
          * 单参内置（iter_len/is_number/glen/type/gtypes/strings/numbers）
            且 argc==1 → qotrt_X1(&slot)；
          * output argc==1 → qotrt_output1(&slot)（数字参数内部直通缓冲）；
          * push argc==2 → qotrt_push2(&arr, &v)。
        argc 不匹配一律走通用 args[] 路径——核心入口的参数个数语义必须与
        builtin.cpp 的「参数不足/多余」定价完全一致，多参形态不在快速通道内。
        """
        import os as _os
        if _os.environ.get('QOT_NO_BICORE') == '1':
            return None
        if nm == 'push':
            return 'qotrt_push2' if i.argc == 2 else None
        if nm == 'output':
            return 'qotrt_output1' if i.argc == 1 else None
        core = BUILTIN_CORE1.get(nm)
        if core is not None and i.argc == 1:
            return core
        return None

    @staticmethod
    def _const_divisor(ins, k):
        """组头 k 是值为整数的 CONST_F64 时返回该整数（用于取模常量除数特化）。

        只接受 |c| <= 2^31-1 的整数；0 与超界返回 None（走通用 qotrt_fmod_）。
        2^31 边界与 qotrt_fmod_ 的安全论证保持一致（INT_MIN 除数不进快路径）。
        """
        i = ins[k]
        if i.op != CONST_F64:
            return None
        v = i.imm
        if v != int(v):
            return None
        c = int(v)
        if not (-2147483647 <= c <= 2147483647):
            return None
        return c

    def _fallback_args(self, ins, k0, k1, src, tag):
        """兜底路径的两个 V16* 实参 → (pre_lines, ref_a, ref_b)。

        组头被折成 CONST_F64 后，原常量 V 槽会被死槽消除删掉（那正是折立即数的
        目的），兜底路径不能再取它的地址。这里在 else 分支内**就地**构造一个
        qotrt_num(imm) 临时量——兜底是冷路径，且声明写在 else 块内，
        不会污染快路径的每次迭代。语义与原「x + 常量」逐位一致。
        """
        pre = []
        ra = self._va(src.a)
        rb = self._va(src.b)
        if ins[k0].op == CONST_F64:
            pre.append('    V16 _qg%d_0 = qotrt_num(%s);' % (tag, _cd(ins[k0].imm)))
            ra = '&_qg%d_0' % tag
        if ins[k1].op == CONST_F64:
            pre.append('    V16 _qg%d_1 = qotrt_num(%s);' % (tag, _cd(ins[k1].imm)))
            rb = '&_qg%d_1' % tag
        return pre, ra, rb

    def _try_fused_cmp_branch(self, f, pc, out):
        """裸 CMP_F + BR_IF_NOT/BR_IF 融合为一条比较分支（无 V16 落地）。

        命中条件：CMP_F 的 dst 在本函数内**只读一次**（就是紧随的这条分支），
        且 dst 非全局（全局可能被别的函数读 → 不能省略落地）。
        命中后快路径只剩「比较 + 条件跳转」，省掉一次 bool 装箱 + 一次
        qotrt_truthy_str 调用——这是循环头从 ~10 条指令降到 2 条的关键。
        """
        ins = f.instrs
        n = len(ins)
        if pc + 1 >= n or ins[pc].op != CMP_F:
            return 0
        br = ins[pc + 1]
        if br.op not in (BR_IF_NOT, BR_IF):
            return 0
        cmp_i = ins[pc]
        if br.a != cmp_i.dst or br.a < 0 or (br.a & GLOBAL_BIT) or _is_upval(br.a):
            return 0
        if not self._cmp_branch_only(f, cmp_i.dst):
            return 0
        pred = CMP_PRED_TEXT.get(cmp_i.pred)
        if pred is None:
            return 0
        self._emit_backedge_tick(br, pc + 1, out)
        cond = 'f%d %s f%d' % (cmp_i.a, pred, cmp_i.b)
        if br.op == BR_IF_NOT:
            cond = '!(%s)' % cond
        out.append('  if (%s) goto L%d;' % (cond, br.target))
        return 2

    def _try_f64_group(self, f, pc, out):
        """识别 F64 改写产生的指令组，发射「guard + 通用兜底」双路径。

        返回消费的指令数；不匹配返回 0。
        组形态（lir_opt._rewrite_f64 的产物，窥孔后开头可能是 MOV_F/CONST_F64）：
          算术: [X, X, OP_F(src), STORE_F64]      -> 4 条（或其后有分支 -> 见比较）
          比较: [X, X, CMP_F(src)]                -> 3 条（+1 若可与分支融合）
          取负: [X, NEG_F(src), STORE_F64]        -> 3 条
          X ∈ {LOAD_F64, MOV_F, CONST_F64}
            * LOAD_F64   = V 槽取裸值，需 guard；
            * MOV_F      = 窥孔合并，源刚被 STORE_F64 写入 → 必为 NUM，无需 guard；
            * CONST_F64  = 常数折进立即数（N-02 窥孔），无需 guard；
              a 字段保留「原常量 V 槽」供兜底路径取 & 引用（不让常量槽被判死）。
        """
        ins = f.instrs
        n = len(ins)
        v = self._vref

        def head(k):
            return ins[k].op in (LOAD_F64, MOV_F, CONST_F64)

        def guard_of(pair):
            """两个组头的 guard 合取；全无需 guard 时返回 None（退化为无条件）。"""
            conds = [r + '.t == VT_NUM' for r in pair if r is not None]
            return ' && '.join(conds) if conds else None

        # ---- 算术（4 条）----
        # D-11 修正（0）：_src_op 由 lir_opt._rewrite_f64 挂在【组尾
        # STORE_F64】上（不是 OP_F 上）。原判定读 ins[pc+2]._src_op 恒为 None，
        # 算术组因此永不命中双路径，被逐条发射为「裸 f64 运算 + LOAD_F64 的
        # 0.0 兜底」——非数字输入时语义与 VM 分叉（_probe_hole 实证：
        # "x1" + 2 在 VM 得 "x12"，AOT 得 2.0）。此处改读组尾，并按 src.op
        # 复核是数值子码，双路径兜底随之生效。
        if pc + 3 < n and head(pc) and head(pc + 1) and \
                ADD_F <= ins[pc + 2].op <= POW_F and \
                ins[pc + 3].op == STORE_F64:
            # _src_op 优先取组尾 STORE_F64；组身 OP_F 也带（_rewrite_f64 双挂，
            # 供 MOV_F 窥孔改写后仍能溯源）。两条都没有则不得发射裸运算
            # （会丢失兜底路径）→ 交给逐条发射。
            src = getattr(ins[pc + 3], '_src_op', None)
            if src is None:
                src = getattr(ins[pc + 2], '_src_op', None)
            if src is None or src.op != ARITH_GEN or not (1 <= src.pred <= 6):
                return 0
            ea, ga = self._operand(ins, pc)
            eb, gb = self._operand(ins, pc + 1)
            fd = ins[pc + 2].dst
            va, vb, vd = src.a, src.b, src.dst
            # D-11 修正：表以【ARITH_GEN 子码】为键（1..6），不是 F64 指令号
            # （6..11）。原表键与 pred 错位，一旦双路径真正命中就 KeyError；
            # 这也是该分支此前从未被任何用例走过的物证。
            if src.pred == 5:
                eb_const = self._const_divisor(ins, pc + 1)
                if eb_const is not None:
                    # 除数是编译期已知整数 → qotrt_fmod_ci：免一次转换/检查，
                    # 且 cl /O2 把 `% 常量` 强度削减为乘法+移位（无 div 指令）。
                    # 兜底路径不变（qotrt_arith case 5 → qotrt_fmod_）。
                    eb = str(eb_const)
                    expr = 'qotrt_fmod_ci(%s, %s)' % (ea, eb)
                else:
                    expr = 'qotrt_fmod_(%s, %s)' % (ea, eb)
            else:
                expr = ('%s %s %s' % (ea, ARITH_OPS[src.pred], eb)) if src.pred in ARITH_OPS \
                    else ('%s(%s, %s)' % (ARITH_FNS[src.pred], ea, eb))
            g = guard_of((ga, gb))
            if g is None:
                out.append('  f%d = %s;' % (fd, expr))
                out.append('  %s = qotrt_num(f%d);' % (v(vd), fd))
            else:
                pre, ra, rb = self._fallback_args(ins, pc, pc + 1, src, pc)
                out.append('  if (%s) {' % g)
                out.append('    f%d = %s;' % (fd, expr))
                out.append('    %s = qotrt_num(f%d);' % (v(vd), fd))
                out.append('  } else {')
                out.extend(pre)
                out.append('    qotrt_arith(%d, %s, %s, %s);'
                           % (src.pred, ra, rb, self._va(vd)))
                out.append('  }')
            return 4
        # ---- 比较（3 条，可与紧随的分支融合成 4 条）----
        if pc + 2 < n and head(pc) and head(pc + 1) and \
                ins[pc + 2].op == CMP_F and \
                getattr(ins[pc + 2], '_src_op', None) is not None:
            src = ins[pc + 2]._src_op
            ea, ga = self._operand(ins, pc)
            eb, gb = self._operand(ins, pc + 1)
            cmp_i = ins[pc + 2]
            va, vb, vd = src.a, src.b, src.dst
            pred = CMP_PRED_TEXT.get(src.pred)
            if pred is None:
                return 0
            g = guard_of((ga, gb))
            # 融合：CMP 结果只被紧随的这条分支读 → 快路径不落地 V16
            br = ins[pc + 3] if pc + 3 < n else None
            if (br is not None and br.op in (BR_IF_NOT, BR_IF) and br.a == vd
                    and self._cmp_branch_only(f, vd)):
                self._emit_backedge_tick(br, pc + 3, out)
                fast = '%s %s %s' % (ea, pred, eb)
                if br.op == BR_IF_NOT:
                    fast = '!(%s)' % fast
                if g is None:
                    out.append('  if (%s) goto L%d;' % (fast, br.target))
                else:
                    pre, ra, rb = self._fallback_args(ins, pc, pc + 1, src, pc)
                    out.append('  if (%s) {' % g)
                    out.append('    if (%s) goto L%d;' % (fast, br.target))
                    out.append('  } else {')
                    out.extend(pre)
                    out.append('    qotrt_arith(%d, %s, %s, %s);'
                               % (src.pred, ra, rb, self._va(vd)))
                    out.append('    if (!qotrt_truthy_str(%s)) goto L%d;'
                               % (self._va(vd), br.target))
                    out.append('  }')
                return 4
            if g is None:
                out.append('  %s = qotrt_bool(%s %s %s);' % (v(vd), ea, pred, eb))
            else:
                pre, ra, rb = self._fallback_args(ins, pc, pc + 1, src, pc)
                out.append('  if (%s) {' % g)
                out.append('    %s = qotrt_bool(%s %s %s);' % (v(vd), ea, pred, eb))
                out.append('  } else {')
                out.extend(pre)
                out.append('    qotrt_arith(%d, %s, %s, %s);'
                           % (src.pred, ra, rb, self._va(vd)))
                out.append('  }')
            return 3
        # ---- 取负（3 条）----
        if pc + 2 < n and head(pc) and ins[pc + 1].op == NEG_F and \
                getattr(ins[pc + 1], '_src_op', None) is not None and \
                ins[pc + 2].op == STORE_F64:
            src = ins[pc + 1]._src_op
            ea, ga = self._operand(ins, pc)
            fd = ins[pc + 1].dst
            va, vd = src.a, src.dst
            g = guard_of((ga,))
            if g is None:
                out.append('  f%d = -(%s);' % (fd, ea))
                out.append('  %s = qotrt_num(f%d);' % (v(vd), fd))
            else:
                pre = []
                ra = self._va(va)
                if ins[pc].op == CONST_F64:
                    pre.append('    V16 _qg%d_n = qotrt_num(%s);' % (pc, _cd(ins[pc].imm)))
                    ra = '&_qg%d_n' % pc
                out.append('  if (%s) {' % g)
                out.append('    f%d = -(%s);' % (fd, ea))
                out.append('    %s = qotrt_num(f%d);' % (v(vd), fd))
                out.append('  } else {')
                out.extend(pre)
                out.append('    qotrt_unary(1, %s, %s);' % (ra, self._va(vd)))
                out.append('  }')
            return 3
        return 0

    @staticmethod
    def _is_jump_target(f, pc):
        # 【常数 0】O(1) 集合查（原逐 pc 线性扫全函数 → 发射期
        # O(n²)/函数，48 模块口径的 cgen 大头）。首次调用构建目标集并
        # 缓存在函数对象上（发射期内函数不可变 → 缓存安全）。
        cached = getattr(f, '_jt_cache', None)
        if cached is None:
            cached = set()
            for i in f.instrs:
                if i.op in (JMP, BR_IF_NOT, BR_IF, BR_F, GUARD_NUM, TRY_PUSH):
                    if 0 <= i.target < len(f.instrs):
                        cached.add(i.target)
            try:
                f._jt_cache = cached      # LirFunction 无 __slots__（可挂）
            except AttributeError:
                return any(
                    i.op in (JMP, BR_IF_NOT, BR_IF, BR_F, GUARD_NUM, TRY_PUSH)
                    and i.target == pc for i in f.instrs)
        return pc in cached

    def _emit_instr(self, f, i, nt, pc, is_main, depth_param=None):
        E = []
        v = self._vref
        if i.op == NOP:
            return E
        if i.op == GUARD_NUM:
            # N-02 形态守卫：全部槽为 NUM 才落下（进入去装箱快循环），
            # 否则 goto 未特化的原循环。纯控制流，不写任何槽。
            conds = ' && '.join('%s.t == VT_NUM' % v(s) for s in i.args)
            E.append('  if (!(%s)) goto L%d;' % (conds, i.target))
        elif i.op == CONST_F64:
            E.append('  f%d = %s;' % (i.dst, _cd(i.imm)))
        elif i.op == CONST_VAL:
            E.extend(self._emit_const_val(i, v))
        elif i.op == MOV_V:
            E.append('  %s = %s;' % (v(i.dst), v(i.a)))
        elif i.op == MOV_F:
            E.append('  f%d = f%d;' % (i.dst, i.a))
        elif ADD_F <= i.op <= POW_F:
            ops = {ADD_F: '+', SUB_F: '-', MUL_F: '*',
                   DIV_F: 'qotrt_fdiv', MOD_F: 'qotrt_fmod_', POW_F: 'pow'}
            if i.op in (ADD_F, SUB_F, MUL_F):
                E.append('  f%d = f%d %s f%d;' % (i.dst, i.a, ops[i.op], i.b))
            else:
                E.append('  f%d = %s(f%d, f%d);' % (i.dst, ops[i.op], i.a, i.b))
        elif i.op == NEG_F:
            E.append('  f%d = -f%d;' % (i.dst, i.a))
        elif i.op == CMP_F:
            pred = {7: '>', 8: '<', 9: '>=', 10: '<=', 11: '==', 12: '!='}[i.pred]
            E.append('  %s = qotrt_bool(f%d %s f%d);' % (v(i.dst), i.a, pred, i.b))
        elif i.op == LOAD_F64:
            # guard：非数字 → 走通用语义不可达（改写只对「消费点」产生 LOAD_F64，
            # 消费点自带 fallback），这里保守按 null 处理（推断保证不发生）
            E.append('  f%d = (%s).t == VT_NUM ? (%s).u.d : 0.0;' % (i.dst, v(i.a), v(i.a)))
        elif i.op == STORE_F64:
            E.append('  %s = qotrt_num(f%d);' % (v(i.dst), i.a))
        elif i.op == ARITH_GEN:
            E.extend(self._emit_arith_gen(i, v))
        elif i.op == UNARY_GEN:
            E.extend(self._emit_unary_gen(i, v))
        elif i.op == BR_IF_NOT:
            U = []
            self._emit_try_unwind(i, pc, U)
            if U:
                E.append('  if (!qotrt_truthy_str(%s)) {' % self._va(i.a))
                E.extend(U)
                E.append('    goto L%d;' % i.target)
                E.append('  }')
            else:
                E.append('  if (!qotrt_truthy_str(%s)) goto L%d;' % (self._va(i.a), i.target))
        elif i.op == BR_IF:
            U = []
            self._emit_try_unwind(i, pc, U)
            if U:
                E.append('  if (qotrt_truthy_str(%s)) {' % self._va(i.a))
                E.extend(U)
                E.append('    goto L%d;' % i.target)
                E.append('  }')
            else:
                E.append('  if (qotrt_truthy_str(%s)) goto L%d;' % (self._va(i.a), i.target))
        elif i.op == BR_F:
            E.append('  if (f%d == 0.0) goto L%d;' % (i.a, i.target))
        elif i.op == JMP:
            U = []
            self._emit_try_unwind(i, pc, U)
            E.extend(U)
            E.append('  goto L%d;' % i.target)
        elif i.op == ARENA_PUSH:
            E.append('  qotrt_arena_push();')
        elif i.op == ARENA_RESET:
            E.append('  qotrt_arena_pop();')
        elif i.op == RET_V:
            if self._cur_volatile:
                E.append('  while (qotrt_landings != (QotrtLanding *)qot_ldbase) '
                         'qotrt_landings = qotrt_landings->prev;')
            E.append('  *ret = %s; return;' % v(i.a))
        elif i.op == RET_F:
            if self._cur_volatile:
                E.append('  while (qotrt_landings != (QotrtLanding *)qot_ldbase) '
                         'qotrt_landings = qotrt_landings->prev;')
            E.append('  *ret = qotrt_num(f%d); return;' % i.a)
        elif i.op in (CALL_USER, CALL_BUILTIN):
            callee = self.fn_by_name.get(nt[i.func_idx]) if i.op == CALL_USER else None
            if i.op == CALL_BUILTIN:
                nm = nt[i.name_idx] if 0 <= i.name_idx < len(nt) else ''
                if nm not in SUPPORTED_BUILTINS:
                    # 【M1】错误必须说清「这是引擎归属问题」还是「根本没这个
                    # 内置」—— 旧版只有一句「AOT 不支持内置函数」，把两类
                    # 完全不同的故障混成一句话（G-5 的分歧就是这么藏住的）。
                    eng = _qb.engine_of(nm)
                    if eng:
                        raise CgenError(
                            '内置函数 %s() 属于 [%s] 引擎，AOT（native）不支持：'
                            '该调用无法编译。请在 builtins.toml 里为它补上 '
                            'native 引擎与 qotrt 符号，或改用 AOT 支持的写法。'
                            % (nm, '/'.join(eng)))
                    raise CgenError(
                        '未知内置函数 %s：builtins.toml 未登记该名字'
                        '（单一清单见 builtins.toml）' % nm)
                rt = 'qotrt_' + SUPPORTED_BUILTINS[nm]
                core = self._builtin_core(nm, i)
                if core == 'qotrt_push2':
                    E.append('  %s = qotrt_push2(%s, %s);'
                             % (v(i.dst), self._va(i.args[0]), self._va(i.args[1])))
                elif core is not None:
                    # N-05 快速通道：argc 匹配时直连核心入口，免 V16 args[] 数组
                    # 构建（每参一次 16B 拷贝）。语义单一来源仍是 argc 版本（G-11）。
                    if nm == 'output':
                        E.append('  qotrt_output1(%s);' % self._va(i.args[0]))
                        E.append('  %s = qotrt_null_v();' % v(i.dst))
                    else:
                        E.append('  %s = %s(%s);'
                                 % (v(i.dst), core, self._va(i.args[0])))
                elif nm == 'output':
                    E.append('  { V16 a%d[%d];' % (pc, max(1, i.argc)))
                    for k, s in enumerate(i.args):
                        E.append('    a%d[%d] = %s;' % (pc, k, v(s)))
                    E.append('    qotrt_output(a%d, %d); }' % (pc, i.argc))
                    E.append('  %s = qotrt_null_v();' % v(i.dst))
                else:
                    E.append('  { V16 a%d[%d];' % (pc, max(1, i.argc)))
                    for k, s in enumerate(i.args):
                        E.append('    a%d[%d] = %s;' % (pc, k, v(s)))
                    E.append('    %s = qotrt_%s(a%d, %d); }' % (v(i.dst), SUPPORTED_BUILTINS[nm], pc, i.argc))
            else:
                if callee is None:
                    raise CgenError('CALL_USER 目标解析失败 idx=%d' % i.func_idx)
                sym = self._symbol(callee)
                # 深度实参永远存在：main（无 depth 形参）起始深度 = 1；
                # 用户函数内 = depth + 1。（is_main 指【 enclosing 函数】，
                # 不能用来决定被调者签名——C2660 实证）
                depth_arg = ', 1' if is_main else ', %s + 1' % depth_param
                fname = getattr(callee, 'name', None)
                fkey = self.fast.fast_key(fname, i.argc) if self.fast else None
                use_fast = fkey is not None and fname is not None
                guard = None
                if use_fast and i.args:
                    # N-04：数字到数字直连快入口。guard 全真时省掉 args[] 数组
                    # 构建 + 慢入口的 V16 绑定；guard 假（任一实参非数字）走
                    # 原通用调用——两条路径对同一输入产出逐位一致（快体由
                    # 全量数值证明保证无 bail，不存在半执行重跑）。
                    guard = ' && '.join('%s.t == VT_NUM' % v(s) for s in i.args)
                if use_fast and guard is not None:
                    E.append('  if (%s) {' % guard)
                    fargs = ', '.join(['%s.u.d' % v(s) for s in i.args]
                                      + [depth_arg.lstrip(', ')])
                    E.append('    %s = qotrt_num(%s(%s));'
                             % (v(i.dst), self.fast.symbol(fname, i.argc), fargs))
                    E.append('  } else {')
                elif use_fast:
                    fargs = ', '.join([depth_arg.lstrip(', ')])
                    E.append('  %s = qotrt_num(%s(%s));'
                             % (v(i.dst), self.fast.symbol(fname, i.argc), fargs))
                if not (use_fast and guard is None):
                    E.append('  { V16 a%d[%d];' % (pc, max(1, i.argc)))
                    for k, s in enumerate(i.args):
                        E.append('    a%d[%d] = %s;' % (pc, k, v(s)))
                    env_arg = ''
                    if self._fn_has_env(callee):
                        # F1：被调方需要词法环境链 → 传调用方的激活环境
                        # （直接调用只发生在词法可见的父/自递归之间）
                        env_arg = ', %s' % (self._cur_env0 or '(QEnv *)0')
                    E.append('    %s(%s, a%d, %d%s%s); }'
                             % (sym, self._va(i.dst), pc, i.argc, depth_arg,
                                env_arg))
                if use_fast and guard is not None:
                    E.append('  }')
        elif i.op == CALL_MODULE:
            raise CgenError('AOT 不支持模块调用（require 未内联）')
        elif i.op == CALL_DYNAMIC:
            # F1：闭包调用。callee 槽必须持 VT_CLOSURE；经入口表分派，
            # depth 契约与 CALL_USER 一致。非闭包值 = 确定性致命 [2013]。
            dst = v(i.dst)
            depth_expr = '1' if is_main else '(%s + 1)' % depth_param
            ntab_n = max(1, len(nt))
            E.append('  { V16 a%d[%d];' % (pc, max(1, i.argc)))
            for k, s in enumerate(i.args):
                E.append('    a%d[%d] = %s;' % (pc, k, v(s)))
            E.append('    if (%s.t == VT_CLOSURE) {' % v(i.a))
            E.append('      QClosure *_cl = (QClosure *)%s.u.p;' % v(i.a))
            E.append('      if (_cl->fn < 0 || _cl->fn >= %d) '
                     'qotrt_fail_not_callable(VT_CLOSURE);' % ntab_n)
            E.append('      qot_clo_fns[_cl->fn](%s, a%d, %d, %s, _cl->env);'
                     % (self._va(i.dst), pc, i.argc, depth_expr))
            E.append('    } else {')
            E.append('      qotrt_fail_not_callable(%s.t);' % v(i.a))
            E.append('    }')
            E.append('  }')
        elif i.op == FIELD_GET:
            # M4：obj->f —— 内联快路径（类型表偏移 + 直接下标，无调用帧）；
            # 缺席字段/非对象 = null（宽松定价，两侧同律）。if/else 形态
            #（与三元式差分实测同速、可读性更好；热循环内每字段读边际
            # ≈ 7ns —— 与 INDEX_GET 同族，量级账单见交付记录）。
            E.append('  if (%s.t == VT_OBJ) {' % v(i.a))
            E.append('    QObj *_o = (QObj *)%s.u.p;' % v(i.a))
            E.append('    int32_t _off = qotrt_types[_o->tid].foff[%d];' % i.pred)
            E.append('    %s = (_off >= 0) ? _o->a[_off] : qotrt_null_v();'
                     % v(i.dst))
            E.append('  } else {')
            E.append('    %s = qotrt_null_v();' % v(i.dst))
            E.append('  }')
        elif i.op == FIELD_SET:
            # M4：obj->f = v —— 缺席字段/非对象 = 忽略（两侧同律）。
            E.append('  if (%s.t == VT_OBJ) {' % v(i.a))
            E.append('    QObj *_o = (QObj *)%s.u.p;' % v(i.a))
            E.append('    int32_t _off = qotrt_types[_o->tid].foff[%d];' % i.pred)
            E.append('    if (_off >= 0) _o->a[_off] = %s;' % v(i.c))
            E.append('  }')
        elif i.op == CALL_OMETHOD:
            # M4：obj->m(args) —— 双路径：
            #   receiver 是 VT_OBJ → 类型表方法分派（函数指针直调，5 参，
            #   receiver 即 args[0]，与「receiver 前置」约定同构）；
            #   否则 → 既有 CALL 发射（内置/用户函数，同一份实参数组）。
            E.extend(self._emit_omethod(i, nt, pc, v, is_main, depth_param))
        elif i.op == REQUIRE:
            raise CgenError('AOT 不支持 require（运行期模块加载）')
        elif i.op == ENV_NEW:
            # F1：激活环境分配（emit_func 只声明 env0；分配在此发生一次）。
            # P-F1（0）：n_caps==0 的激活环境没有可写槽，唯一角色是
            # upval 链的一节（env_up 穿越 / 子闭包的 parent）。链节内容由
            # parent 唯一决定且不可变 → 用运行时按父环境缓存的共享节点
            # （qotrt_env_link0），热闭包调用免掉每次一次的池分配。
            # 正确性边界：n>0（真有捕获槽）**不走**共享 —— 槽会被本函数读写，
            # 共享即串值；那种函数维持逐调用分配（旧价）。
            parent = 'envp' if self._cur_env0 else '0'
            n_caps = int(i.a)
            if n_caps == 0:
                E.append('  env0 = qotrt_env_link0(%s);' % parent)
            else:
                E.append('  env0 = qotrt_env_new(%d, %s);' % (n_caps, parent))
            E.append('  %s = qotrt_env_v(env0);' % v(i.dst))
        elif i.op == MAKE_CLOSURE:
            # F1：函数常量 → 闭包值（入口表下标 + 环境）
            nm = nt[i.func_idx] if 0 <= i.func_idx < len(nt) else ''
            idx = self.clo_index.get(nm)
            if idx is None:
                raise CgenError('MAKE_CLOSURE 目标 %r 不在闭包入口表中' % nm)
            env_expr = 'qotrt_env_of(%s)' % v(i.a) if i.a >= 0 else '(QEnv *)0'
            E.append('  %s = qotrt_closure_new(%d, %s);' % (v(i.dst), idx, env_expr))
        elif i.op == THROW:
            E.append('  qotrt_throw(%s);' % v(i.a))
        elif i.op == THROWVAL:
            E.append('  %s = qotrt_thrown;' % v(i.dst))
        elif i.op == TRY_PUSH:
            # F2：着陆点入栈 + setjmp。打开的 { 花括号到 TRY_POP 才闭合。
            # qhit 不带初始化器（C++ 禁止 goto 跳入含非空初始化标量的作用
            # 域；volatile 写在 setjmp 处完成，跨 longjmp 存活）。
            tag = 'qld_%d' % pc
            self._try_stack.append(tag)
            E.append('  { QotrtLanding %s; %s.prev = qotrt_landings; '
                     'qotrt_landings = &%s;' % (tag, tag, tag))
            E.append('    volatile int qhit_%d;' % pc)
            E.append('    qhit_%d = setjmp(%s.jb);' % (pc, tag))
            E.append('    if (qhit_%d) { qotrt_landings = %s.prev; goto L%d; }'
                     % (pc, tag, i.target))
        elif i.op == TRY_POP:
            tag = self._try_stack.pop() if self._try_stack else None
            if tag is None:
                raise CgenError('TRY_POP 无配对的 TRY_PUSH')
            E.append('    qotrt_landings = %s.prev;' % tag)
            E.append('  }')
        elif i.op == INDEX_GET:
            E.extend(self._emit_index_get(i, v))
        elif i.op == INDEX_SET:
            E.extend(self._emit_index_set(i, v))
        elif i.op == MAKE_ARRAY:
            E.append('  %s = qotrt_arr_new(%d);' % (v(i.dst), max(4, i.argc)))
            for k in range(i.argc - 1, -1, -1):
                # 字节码语义：栈顶是首元素 → args[0] 是首元素，直接顺序 push
                pass
            for k, s in enumerate(i.args):
                E.append('  qotrt_arr_push((QArr*)%s.u.p, %s);' % (v(i.dst), v(s)))
        elif i.op == MAKE_DICT:
            E.append('  %s = qotrt_dict_new();' % v(i.dst))
            for k in range(i.argc):
                key_s, val_s = i.args[2 * k], i.args[2 * k + 1]
                E.append('  if (%s.t == VT_STR) { QStr* kk = (QStr*)%s.u.p;'
                         ' *qotrt_dict_at((QDict*)%s.u.p, kk->s, kk->len, qotrt_vhash(%s)) = %s; }'
                         % (v(key_s), v(key_s), v(i.dst), self._va(key_s), v(val_s)))
        elif i.op == STOP:
            if self._cur_volatile:
                E.append('  while (qotrt_landings != (QotrtLanding *)qot_ldbase) '
                         'qotrt_landings = qotrt_landings->prev;')
            E.append('  goto qot_stop;' if is_main else '  *ret = qotrt_null_v(); return;')
        else:
            raise CgenError('未知 LIR op %d' % i.op)
        return E

    def _fn_has_env(self, f):
        """函数是否需要 env 实参（ENV_NEW / 带捕获元数据 / 闭包表成员）。

        闭包表成员（lambda 等经 MAKE_CLOSURE 构造的函数）统一 5 参签名：
        CALL_DYNAMIC 经 qot_clo_fns 表分派，签名必须同构；无捕获者 env0
        恒空、envp 不被解引用，零成本。
        【M4】clo_form：类方法函数 —— 经类型表函数指针分派（QotCloFn 同构
        签名），同样统一 5 参；env0 恒空、envp 不被解引用。
        """
        key = id(f)
        cached = getattr(self, '_has_env_cache', None)
        if cached is None:
            cached = self._has_env_cache = {}
        if key not in cached:
            cached[key] = any(i.op == ENV_NEW for i in f.instrs) or \
                bool(getattr(f, 'cap_names', None)) or \
                getattr(f, 'clo_form', False) or \
                (f.name in self.clo_index)
        return cached[key]

    def plan_closures(self):
        """收集闭包入口表：函数名 → qot_clo_fns 下标（按名排序定序）。

        成员 = 所有 MAKE_CLOSURE 目标（lambda 恒以闭包值形态出现，即使无
        捕获）∪ 所有带环境的函数（防御：构造点应与 MAKE_CLOSURE 一致）。
        """
        names = set()
        for f in self.prog.all_funcs():
            if self._fn_has_env(f):
                names.add(f.name)
            nt = getattr(f, 'name_table', None) or []
            for i in f.instrs:
                if i.op == MAKE_CLOSURE and 0 <= i.func_idx < len(nt):
                    names.add(nt[i.func_idx])
        ordered = sorted(n for n in names if n)
        self.clo_index = {nm: k for k, nm in enumerate(ordered)}
        # 缓存清零：plan 期间的 _fn_has_env 判定发生在 clo_index 就位之前，
        # 「表成员⇒5参」的结论必须重新计算
        self._has_env_cache = {}
        return self.clo_index

    # ---------------------------------------------------------- 通用语义的内联快路径
    # 以下四个发射器做同一件事：把 qotrt_* 运行时函数的**首分支**（也就是它自己
    # 的第一条判据）在调用点展开，判据成立就走裸运算，否则原样调回 qotrt_*。
    # 因为判据与运行时函数内部逐字相同，「快/慢」两路必然与直接调用逐位等价——
    # 这是**无需任何静态推断**就能拿到的特化（F64 推断证明不了的地方也适用）。
    # 收益：省掉一次真实函数调用（cl 不会内联这些带大 switch 的运行时函数）。

    def _emit_const_val(self, i, v):
        """CONST_VAL：数值/bool 常量直接落地成立字面量，省一次常量池堆读。

        常量池由 qotrt_init_consts 一次性填好，数值与 bool 是纯值语义，
        在调用点重建与取池项逐位相同；字符串/其它仍走 qotrt_const（避免
        每次执行都新建堆对象）。
        """
        cv = self.consts[i.a] if 0 <= i.a < len(self.consts) else None
        if isinstance(cv, bool):
            return ['  %s = qotrt_bool(%s);' % (v(i.dst), 'true' if cv else 'false')]
        if isinstance(cv, (int, float)):
            return ['  %s = qotrt_num(%s);' % (v(i.dst), _cd(cv))]
        return ['  %s = qotrt_const(%d);' % (v(i.dst), i.a)]

    def _emit_omethod(self, i, nt, pc, v, is_main, depth_param):
        """M4：对象方法分派调用（CALL_OMETHOD）的 C 发射。

        栈序与 CALL 完全一致（receiver 是 args[0]）—— 两条路径消费同一份
        实参数组 a[pc]：
          * VT_OBJ 分支：qotrt_types[tid].mfns[mid] 函数指针直调（5 参；
            方法签名与 QotCloFn 同构）。缺席方法（指针空）→ null（宽松定价，
            与 interp _call_method 同律）；depth 契约与 CALL_USER 一致。
          * 退路分支：与既有 CALL 发射同一形态 —— 名字命中 AOT 支持集 →
            qotrt_<sym>(a, argc)；命中用户函数 → qot_u_<name>(...)；两者皆无
            → CgenError（与 CALL 未知名同款错误面）。
        """
        E = []
        E.append('  { V16 a%d[%d];' % (pc, max(1, i.argc)))
        for k, s in enumerate(i.args):
            E.append('    a%d[%d] = %s;' % (pc, k, v(s)))
        recv = v(i.args[0]) if i.args else 'qotrt_null_v()'
        depth_expr = '1' if is_main else '(%s + 1)' % depth_param
        E.append('    if (a%d[0].t == VT_OBJ) {' % pc)
        E.append('      QObj *_o = (QObj *)a%d[0].u.p;' % pc)
        E.append('      QotCloFn _mf = qotrt_types[_o->tid].mfns[%d];' % i.pred)
        E.append('      if (_mf) { _mf(%s, a%d, %d, %s, (QEnv *)0); }'
                 % (self._va(i.dst), pc, i.argc, depth_expr))
        E.append('      else { %s = qotrt_null_v(); }' % v(i.dst))
        E.append('    } else {')
        nm = nt[i.name_idx] if 0 <= i.name_idx < len(nt) else ''
        if nm in SUPPORTED_BUILTINS:
            # 退路 = 内置（与 _emit_instr 的 CALL_BUILTIN 通用形态一致；
            # BUILTIN_CORE1 快通道不进退路 —— 对象路径已在前，退路是冷臂）
            if nm == 'output':
                E.append('      qotrt_output(a%d, %d);' % (pc, i.argc))
                E.append('      %s = qotrt_null_v();' % v(i.dst))
            else:
                E.append('      %s = qotrt_%s(a%d, %d);'
                         % (v(i.dst), SUPPORTED_BUILTINS[nm], pc, i.argc))
        else:
            callee = self.fn_by_name.get(nm)
            if callee is None:
                # 【M4】名字不是内置/用户函数：若是**某类的类方法名**，退路
                # （非对象接收者）按宽松定价得 null（与「对象缺席方法 → null」
                # 对称）—— 编译期拒绝会让「接收者恒为对象的合法程序」因死
                # 分支被误杀。既非内置/用户函数也非类方法 = 今天的未知调用
                # 编译错误（口径不变）。
                meta = getattr(self.prog, 'objmeta', None)
                mids = (meta.get('method_ids') or {}) if meta else {}
                if nm in mids:
                    E.append('      %s = qotrt_null_v();' % v(i.dst))
                else:
                    raise CgenError(
                        '方法调用 %s() 的退路解析失败：既不是 AOT 支持的内置，'
                        '也不是用户函数或类方法（接收者不是对象时的 -> 调用定价）'
                        % nm)
            else:
                sym = self._symbol(callee)
                depth_arg = ', 1' if is_main else ', %s + 1' % depth_param
                env_arg = ''
                if self._fn_has_env(callee):
                    env_arg = ', %s' % (self._cur_env0 or '(QEnv *)0')
                E.append('      %s(%s, a%d, %d%s%s);'
                         % (sym, self._va(i.dst), pc, i.argc, depth_arg, env_arg))
        E.append('    }')
        E.append('  }')
        return E

    def _emit_arith_gen(self, i, v):
        ra, rb, rd = v(i.a), v(i.b), v(i.dst)
        if 1 <= i.pred <= 6:
            expr = ('%s.u.d %s %s.u.d' % (ra, ARITH_OPS[i.pred], rb)) if i.pred in ARITH_OPS \
                else ('%s(%s.u.d, %s.u.d)' % (ARITH_FNS[i.pred], ra, rb))
            fast = '    %s = qotrt_num(%s);' % (rd, expr)
        elif 7 <= i.pred <= 12:
            fast = '    %s = qotrt_bool(%s.u.d %s %s.u.d);' % (
                rd, ra, CMP_PRED_TEXT[i.pred], rb)
        else:
            # 13/14（&&/||）：两侧 truthy 合取/析取——判据仍是「两值已定」，
            # 但快路径要调 truthy，展开收益不大 → 保持调用。
            return ['  qotrt_arith(%d, %s, %s, %s);'
                    % (i.pred, self._va(i.a), self._va(i.b), self._va(i.dst))]
        return ['  if (%s.t == VT_NUM && %s.t == VT_NUM) {' % (ra, rb),
                fast,
                '  } else {',
                '    qotrt_arith(%d, %s, %s, %s);'
                % (i.pred, self._va(i.a), self._va(i.b), self._va(i.dst)),
                '  }']

    def _emit_unary_gen(self, i, v):
        ra, rd = v(i.a), v(i.dst)
        if i.pred == 1:
            return ['  if (%s.t == VT_NUM) {' % ra,
                    '    %s = qotrt_num(-%s.u.d);' % (rd, ra),
                    '  } else {',
                    '    qotrt_unary(1, %s, %s);' % (self._va(i.a), self._va(i.dst)),
                    '  }']
        return ['  qotrt_unary(%d, %s, %s);'
                % (i.pred, self._va(i.a), self._va(i.dst))]

    # ---------------------------------------------------------- E3 常量键哈希折叠
    def _const_pool_ids(self, f, slot, depth=0):
        """slot 的所有定义对应的常量池下标集合；任一定义不是常量 → None。

        与 _is_const_slot 同构，但多返回「是哪个常量」——折叠需要下标才能取到
        字面量。多个定义必须落到**同一个**下标，否则值不确定，不折叠。
        """
        if depth > 8 or slot < 0 or (slot & GLOBAL_BIT):
            return None
        out = set()
        for i in f.instrs:
            if i.op == CONST_VAL and i.dst == slot:
                out.add(i.a)
                continue
            if i.op == MOV_V and i.dst == slot and i.a != slot:
                sub = self._const_pool_ids(f, i.a, depth + 1)
                if sub is None:
                    return None
                out |= sub
                continue
            d, _r, _e = self._slot_roles(i, getattr(f, 'name_table', None) or [])
            if slot in d:
                return None  # 存在非常量定义 → 值不确定
        return out

    def _constkey_hash(self, slot):
        """E3：`d["alpha"]` 的键是编译期常量 → 返回 (哈希字面量, 注释标记)。

        折叠的是什么：qotrt_vhash(&k) 要读 QStr 的 hash 字段、判 0、可能还要
        扫一遍键；常量键的哈希是编译期可知的，直接作为字面量传下去即可。
        **致命坑**：哈希算错一位 = 静默查不到键（不崩不报），所以
          * 哈希一律由 qot_hash.qotrt_hash64_str 产（Python 侧逐位复刻），
            并由 testing/unit/test_constkey_hash.py 用 C 侧金值钉住；
          * 只折叠**纯 ASCII** 常量：非 ASCII 要依赖生成源文件的编码解释，
            两边字节可能不一致 —— 宁可不折叠也不冒静默查错的风险；
          * 空串不折叠（D-10：空串字面量在常量池里是 null，不是 VT_STR）。
        """
        if not self.do_constkey:
            return None
        f = getattr(self, '_cur_f', None)
        pc = getattr(self, '_cur_pc', None)
        if f is None or pc is None or slot < 0 or (slot & GLOBAL_BIT):
            return None
        # 必须用【到达定义】而不是「全函数扫描」：b2a 的实际形态是
        # `INDEX_GET dst=6, b=6` —— 键槽同时是结果槽，全函数扫会看到
        # 「该槽还被 INDEX_GET 定义过」而一票否决（实测折叠率 0）。
        # 到达定义只看「本条下标执行前，键的哪些定义可达」，判据精确且不
        # 放松：可达定义里只要有一个不是 CONST_VAL，或落到不同常量，就不折叠。
        ids = set()
        for dp in self._reaching_defs_before(f, slot, pc):
            ins = f.instrs[dp]
            if ins.op == CONST_VAL and ins.dst == slot:
                ids.add(ins.a)
            else:
                return None
        if len(ids) != 1:
            return None
        idx = next(iter(ids))
        if not (0 <= idx < len(self.consts)):
            return None
        cv = self.consts[idx]
        if not isinstance(cv, str) or cv == '':
            return None
        if any(ord(ch) > 127 for ch in cv):
            return None  # 非 ASCII：源编码解释不确定，不折叠（见上）
        self.n_constkey += 1
        return (_qh.c_literal(_qh.qotrt_hash64_str(cv)), '/*constkey*/')

    def _keyhash_expr(self, slot, rx):
        """字典下标路径的哈希实参：能折叠就折叠，否则回落 qotrt_vhash。"""
        ck = self._constkey_hash(slot)
        if ck is None:
            return 'qotrt_vhash(%s)' % self._va(slot)
        return '%s %s' % ck

    def _emit_index_get(self, i, v):
        rc, rx, rd = v(i.a), v(i.b), v(i.dst)
        kh = self._keyhash_expr(i.b, rx)
        # QOT_UNSAFE_INDEX=1（上帝法则配置）：移除边界检查，越界 = 未定义行为。
        # 默认关闭 —— 语言默认保留「越界得 null」定价。
        if self.unsafe_index:
            return ['  if (%s.t == VT_ARR && %s.t == VT_NUM) {' % (rc, rx),
                    '    QArr* _a = (QArr*)%s.u.p; int _i = (int)%s.u.d;' % (rc, rx),
                    '    %s = _a->a[_i]; /* unsafe-index: 越界=UB（配置自担） */' % rd,
                    '  } else if (%s.t == VT_DICT && %s.t == VT_STR) {' % (rc, rx),
                    '    QStr* _k = (QStr*)%s.u.p;' % rx,
                    '    V16* _h = qotrt_dict_find((QDict*)%s.u.p, _k->s, _k->len,'
                    ' %s);' % (rc, kh),
                    '    %s = _h ? *_h : qotrt_null_v();' % rd,
                    '  } else if (%s.t == VT_STR && %s.t == VT_NUM) {' % (rc, rx),
                    '    QStr* _q = (QStr*)%s.u.p; int _j = (int)%s.u.d;' % (rc, rx),
                    '    %s = qotrt_str(_q->s + _j, 1); /* unsafe-index */' % rd,
                    '  } else {',
                    '    qotrt_index_get(%s, %s, %s);'
                    % (self._va(i.a), self._va(i.b), self._va(i.dst)),
                    '  }']
        return ['  if (%s.t == VT_ARR && %s.t == VT_NUM) {' % (rc, rx),
                '    QArr* _a = (QArr*)%s.u.p; int _i = (int)%s.u.d;' % (rc, rx),
                '    %s = (_i >= 0 && _i < _a->len) ? _a->a[_i] : qotrt_null_v();' % rd,
                '  } else if (%s.t == VT_DICT && %s.t == VT_STR) {' % (rc, rx),
                '    QStr* _k = (QStr*)%s.u.p;' % rx,
                '    V16* _h = qotrt_dict_find((QDict*)%s.u.p, _k->s, _k->len,'
                ' %s);' % (rc, kh),
                '    %s = _h ? *_h : qotrt_null_v();' % rd,
                '  } else if (%s.t == VT_STR && %s.t == VT_NUM) {' % (rc, rx),
                '    QStr* _q = (QStr*)%s.u.p; int _j = (int)%s.u.d;' % (rc, rx),
                '    %s = (_j >= 0 && _j < _q->len) ? qotrt_str(_q->s + _j, 1)'
                ' : qotrt_null_v();' % rd,
                '  } else {',
                '    qotrt_index_get(%s, %s, %s);'
                % (self._va(i.a), self._va(i.b), self._va(i.dst)),
                '  }']

    def _emit_index_set(self, i, v):
        rc, rx, rv = v(i.a), v(i.b), v(i.c)
        kh = self._keyhash_expr(i.b, rx)
        if self.unsafe_index:
            return ['  if (%s.t == VT_ARR && %s.t == VT_NUM) {' % (rc, rx),
                    '    QArr* _a = (QArr*)%s.u.p; int _i = (int)%s.u.d;' % (rc, rx),
                    '    _a->a[_i] = %s; /* unsafe-index: 越界=UB（配置自担） */' % rv,
                    '  } else if (%s.t == VT_DICT && %s.t == VT_STR) {' % (rc, rx),
                    '    QStr* _k = (QStr*)%s.u.p;' % rx,
                    '    *qotrt_dict_at((QDict*)%s.u.p, _k->s, _k->len, %s) = %s;'
                    % (rc, kh, rv),
                    '  } else {',
                    '    qotrt_index_set(%s, %s, %s);'
                    % (self._va(i.a), self._va(i.b), self._va(i.c)),
                    '  }']
        return ['  if (%s.t == VT_ARR && %s.t == VT_NUM) {' % (rc, rx),
                '    QArr* _a = (QArr*)%s.u.p; int _i = (int)%s.u.d;' % (rc, rx),
                '    if (_i >= 0 && _i < _a->len) { _a->a[_i] = %s; }' % rv,
                '  } else if (%s.t == VT_DICT && %s.t == VT_STR) {' % (rc, rx),
                '    QStr* _k = (QStr*)%s.u.p;' % rx,
                '    *qotrt_dict_at((QDict*)%s.u.p, _k->s, _k->len, %s) = %s;'
                % (rc, kh, rv),
                '  } else {',
                '    qotrt_index_set(%s, %s, %s);'
                % (self._va(i.a), self._va(i.b), self._va(i.c)),
                '  }']

    def _symbol(self, f):
        return 'qot_u_%s' % _sanitize(f.name)

    def _symbol_by_name(self, name):
        return 'qot_u_%s' % _sanitize(name)


# ================================================================ N-04 双体快速入口（unbox calls）
MAX_FAST_ARITY = 8       # 快速入口参数个数上限
MAX_FAST_INSTRS = 400    # 快照规模上限（防御代码体积膨胀）


class FastPlanner(object):
    """N-04：为「数字到数字」调用生成双体快速入口的规划器。

    形态（JSC 分层调用的编译期变体）：
        慢入口（现有）：qot_u_F(V16 *ret, V16 *args, int argc, int depth)
        快入口（新增）：double qot_u_F_fastK(double p0, ..., int depth)
    调用点：
        if (a0.t == VT_NUM && a1.t == VT_NUM) {
            dst = qotrt_num(qot_u_F_fast2(a0.u.d, a1.u.d, depth + 1));
        } else {
            ...原通用调用（V16 args 数组）...
        }

    正确性核心 —— 【全量资格】而非「护栏失败回退」：
      快入口只有在**局部数值证明覆盖函数体全部可执行指令**时才生成。
      证明是标准的前向 must 数据流（汇合取交、循环迭代到不动点），初态 =
      「实参槽 [0, argc) 为 NUM」——这正是调用点运行期 guard 保障的事实。
      证明覆盖后快入口**没有 bail 路径**：不会出现「快路径执行了一半副作用、
      慢路径重跑一遍」的双重执行问题（这是 bail 式双体的经典坑）。
      任何不满足的构造（下标/容器/非数值内置/globals/STOP=null 返回/未证明
      形态的读取）→ 整个函数没有快入口（保守即安全）。

    递归/互调用图上的 returns-num 是最大不动点（初值全真、违反即回缩、
    单调递减必收敛）：fib 的 `return fib(n-1)+fib(n-2)` 依赖自调用返回数字。

    快入口与慢入口的逐位一致性：
      * 算术：ADD/SUB/MUL 直接对应；DIV/MOD 经 qotrt_fdiv/qotrt_fmod_——
        与 qotrt_arith case 4/5 是同一个函数（G-11）；
      * 分支：truthy(NUM)=d!=0、truthy(BOOL)=d!=0（qotrt_bool 存 1.0/0.0），
        快入口统一用 `!= 0.0` 判定，与 qotrt_truthy_str 的数值分支逐位一致；
      * 深度：与慢入口同一 depth 计数口径（每层 +1，入口同点检查）。
    """

    def __init__(self, prog, snaps):
        self.prog = prog
        self.snaps = snaps or {}
        # QOT-CI L1：切分 TU 模式下快入口去 static（跨 TU 调用面）
        self.extern = False
        self.eligible = {}    # (fname, argc) -> bool
        self.ret_num = {}     # (fname, argc) -> bool
        self.forms = {}       # (fname, argc) -> {block_id: (num_set, bool_set)}
        self.arities = {}     # fname -> set(argc)（来自最终程序的调用点）
        # 【常数 0】from_v1(snap) 按名缓存 —— 同一函数的不同 arity
        # 键与不动点各轮共享同一份块级克隆（原每键每轮一次全量克隆，
        # 48 模块口径是 cgen 的大头；snap 在规划期内不可变 → 缓存安全）
        self._snap_f2 = {}
        for f in prog.all_funcs():
            nt = getattr(f, 'name_table', None) or []
            for i in f.instrs:
                if i.op == CALL_USER and 0 <= i.func_idx < len(nt):
                    nm = nt[i.func_idx]
                    if nm in self.snaps and i.argc <= MAX_FAST_ARITY:
                        self.arities.setdefault(nm, set()).add(i.argc)
        self._solve()

    # ---- 求解 ----
    def _solve(self):
        keys = [(nm, a) for nm, ar in self.arities.items() for a in sorted(ar)]
        for k in keys:
            self.eligible[k] = True
            self.ret_num[k] = True
        changed = True
        rounds = 0
        while changed and rounds <= 2 * len(keys) + 2:
            changed = False
            rounds += 1
            for k in keys:
                if not self.eligible[k] and not self.ret_num[k]:
                    continue
                ok, rnum, forms = self._analyze(k[0], k[1])
                if self.eligible[k] and not ok:
                    self.eligible[k] = False
                    changed = True
                if self.ret_num[k] and not rnum:
                    self.ret_num[k] = False
                    changed = True
                if ok:
                    self.forms[k] = forms
        for k in keys:
            if not self.eligible[k]:
                self.forms.pop(k, None)

    def _analyze(self, name, argc):
        """对 (name, argc) 做局部数值证明。返回 (ok, returns_num, forms)。"""
        import lir2
        snap = self.snaps[name]
        if len(snap.instrs) > MAX_FAST_INSTRS:
            return False, False, None
        # F1/F2：闭包/异常函数不生成快入口（upval 槽与 setjmp 语义都在
        # 慢入口的 V16 世界里；快体只认裸 double）
        for i in snap.instrs:
            if i.op in (THROW, TRY_PUSH, TRY_POP, ENV_NEW, MAKE_CLOSURE, THROWVAL):
                return False, False, None
            if _is_upval(i.dst) or _is_upval(i.a):
                return False, False, None
        # 【常数 0】按名缓存块级克隆（分析只读 f2；同函数的不同
        # arity 键与不动点各轮共享 —— 原每键每轮 from_v1 全量克隆）
        f2 = self._snap_f2.get(name)
        if f2 is None:
            f2 = lir2.from_v1(snap)
            self._snap_f2[name] = f2
        nbind = min(argc, snap.n_bind_slots)
        pool = getattr(self.prog, 'const_pool', None)

        def const_form(idx):
            if pool is None or not isinstance(idx, int) or not (0 <= idx < len(pool)):
                return None
            v = pool[idx]
            if isinstance(v, bool):
                return 'bool'
            if isinstance(v, (int, float)):
                return 'num'
            return None

        # ---- 前向 must 数据流：IN[b] = ∩ pred.OUT（entry 注入参数形态）----
        top = (frozenset(range(snap.n_slots_v)), frozenset(range(snap.n_slots_v)))
        IN = {b.id: None for b in f2.blocks}
        OUT = {b.id: top for b in f2.blocks}
        entry_in = (frozenset(range(nbind)), frozenset())
        IN[f2.entry] = entry_in
        order = f2.rpo()
        violated = [False]
        ret_num = [True]
        b_all = {}
        for b in f2.blocks:
            ins = list(b.instrs)
            if b.term is not None:
                ins.append(b.term)
            b_all[b.id] = ins
        changed = True
        while changed:
            changed = False
            for bid in order:
                preds_in = [OUT[p] for p in f2.blocks[bid].preds if OUT[p] is not None]
                if bid == f2.entry:
                    new_in = entry_in
                elif preds_in:
                    ns = frozenset.intersection(*[p[0] for p in preds_in])
                    bs = frozenset.intersection(*[p[1] for p in preds_in])
                    new_in = (ns, bs)
                else:
                    new_in = (frozenset(), frozenset())   # 不可达块：空集即无读取保障
                    # 不可达块的指令不参与资格（下面 transfer 跳过空 IN 块）
                IN[bid] = new_in
                if not new_in[0] and not new_in[1] and bid != f2.entry \
                        and not f2.blocks[bid].preds:
                    continue    # 不可达
                num, boo = set(new_in[0]), set(new_in[1])
                for i in b_all[bid]:
                    num, boo = self._step(i, num, boo, const_form, name,
                                          violated, ret_num)
                    if violated[0]:
                        return False, False, None
                if OUT[bid] != (frozenset(num), frozenset(boo)):
                    OUT[bid] = (frozenset(num), frozenset(boo))
                    changed = True
            if violated[0]:
                return False, False, None
        forms = {bid: (set(IN[bid][0]), set(IN[bid][1])) for bid in b_all}
        return True, ret_num[0], {'f2': f2, 'b_all': b_all, 'in': forms,
                                  'nbind': nbind}

    def _step(self, i, num, boo, const_form, caller_name, violated, ret_num):
        """单指令形态转移。违规置 violated[0]=True。"""
        op = i.op
        gbit = GLOBAL_BIT

        def is_g(s):
            return s >= 0 and (s & gbit)

        def need_num(s):
            if is_g(s) or s not in num:
                violated[0] = True

        def need_bool(s):
            if is_g(s) or s not in boo:
                violated[0] = True

        if op == CONST_VAL:
            f = const_form(i.a)
            if f == 'num':
                num.add(i.dst)
                boo.discard(i.dst)
            elif f == 'bool':
                boo.add(i.dst)
                num.discard(i.dst)
            else:
                num.discard(i.dst)
                boo.discard(i.dst)
        elif op == MOV_V:
            if i.a in num:
                num.add(i.dst)
                boo.discard(i.dst)
            elif i.a in boo:
                boo.add(i.dst)
                num.discard(i.dst)
            else:
                num.discard(i.dst)
                boo.discard(i.dst)
        elif op == ARITH_GEN:
            if 1 <= i.pred <= 6:
                need_num(i.a)
                need_num(i.b)
                num.add(i.dst)
                boo.discard(i.dst)
            elif 7 <= i.pred <= 12:
                need_num(i.a)
                need_num(i.b)
                boo.add(i.dst)
                num.discard(i.dst)
            else:
                violated[0] = True
        elif op == UNARY_GEN:
            if i.pred == 1:
                need_num(i.a)
                num.add(i.dst)
                boo.discard(i.dst)
            elif i.pred == 2:
                need_num(i.a)
                boo.add(i.dst)
                num.discard(i.dst)
            else:
                violated[0] = True
        elif op in (BR_IF_NOT, BR_IF):
            if i.a in num or i.a in boo:
                pass
            else:
                violated[0] = True
        elif op == JMP:
            pass
        elif op == RET_V:
            if is_g(i.a) or i.a not in num:
                ret_num[0] = False
                violated[0] = True
        elif op == CALL_USER:
            nt = getattr(self.snaps.get(caller_name), 'name_table', None) or []
            nm = nt[i.func_idx] if 0 <= i.func_idx < len(nt) else None
            key = (nm, i.argc) if nm is not None else None
            for s in i.args:
                need_num(s)
            if key is None or not self.eligible.get(key) or \
                    not self.ret_num.get(key):
                violated[0] = True
                return num, boo
            num.add(i.dst)
            boo.discard(i.dst)
        elif op == STOP:
            # 用户函数的 STOP = 返回 null：快入口无 null 形态 → 不资格
            violated[0] = True
        else:
            # f 域指令（快照期不应存在）/ 下标 / 容器 / 内置 / 模块 / require /
            # guard：一律不资格（保守即安全）
            violated[0] = True
        return num, boo

    # ---- 查询与符号 ----
    def fast_key(self, name, argc):
        k = (name, argc)
        if self.eligible.get(k) and k in self.forms:
            return k
        return None

    def symbol(self, name, argc):
        return 'qot_u_%s_fast%d' % (_sanitize(name), argc)

    # ---- 发射 ----
    def emit_decls(self, E):
        for (nm, argc) in sorted(self.forms):
            params = ', '.join(['double p%d' % k for k in range(argc)] + ['int depth'])
            E('%s double %s(%s);' % ('' if self.extern else 'static',
                                     self.symbol(nm, argc), params))

    def emit_fast_funcs(self, E):
        for (nm, argc) in sorted(self.forms):
            self._emit_one(E, nm, argc)

    def _emit_one(self, E, name, argc):
        info = self.forms[(name, argc)]
        f2 = info['f2']
        b_all = info['b_all']
        ins_map = info['in']
        snap = self.snaps[name]
        pool = getattr(self.prog, 'const_pool', None)
        params = ', '.join(['double p%d' % k for k in range(argc)] + ['int depth'])
        E('')
        E('%s double %s(%s) {' % ('' if self.extern else 'static',
                                  self.symbol(name, argc), params))
        E('  if (depth > qotrt_max_depth) qotrt_fail_depth(depth);')
        for s in range(snap.n_slots_v):
            E('  double q%d = 0.0;' % s)
        for k in range(info['nbind']):
            E('  q%d = p%d;' % (k, k))

        def q(s):
            return 'q%d' % s

        for b in f2.blocks:
            E('L%d:;' % b.id)
            num = set(ins_map[b.id][0])
            boo = set(ins_map[b.id][1])
            for i in b_all[b.id]:
                self._emit_fast_instr(E, i, num, boo, q, pool, name, argc)
            # 终结器已含于 b_all（RET/JMP/BR 都在 _emit_fast_instr 里发射）
        E('}')

    def _emit_fast_instr(self, E, i, num, boo, q, pool, name, argc):
        op = i.op
        if op == CONST_VAL:
            f = None
            if pool is not None and isinstance(i.a, int) and 0 <= i.a < len(pool):
                cv = pool[i.a]
                if isinstance(cv, bool):
                    f = 'bool'
                elif isinstance(cv, (int, float)):
                    f = 'num'
            if f is None:
                return          # 死定义（分析已保证无人读取 None 形态槽）
            if f == 'num':
                E('  %s = %s;' % (q(i.dst), _cd(cv)))
            else:
                E('  %s = %s ? 1.0 : 0.0;' % (q(i.dst), 'true' if cv else 'false'))
        elif op == MOV_V:
            E('  %s = %s;' % (q(i.dst), q(i.a)))
        elif op == ARITH_GEN:
            if 1 <= i.pred <= 6:
                expr = ('%s %s %s' % (q(i.a), ARITH_OPS[i.pred], q(i.b))) \
                    if i.pred in ARITH_OPS \
                    else ('%s(%s, %s)' % (ARITH_FNS[i.pred], q(i.a), q(i.b)))
                E('  %s = %s;' % (q(i.dst), expr))
            else:
                pred = CMP_PRED_TEXT.get(i.pred)
                E('  %s = (%s %s %s) ? 1.0 : 0.0;' % (q(i.dst), q(i.a), pred, q(i.b)))
        elif op == UNARY_GEN:
            E('  %s = -%s;' % (q(i.dst), q(i.a)))
        elif op == BR_IF_NOT:
            E('  if (!(%s != 0.0)) goto L%d;' % (q(i.a), i.target))
        elif op == BR_IF:
            E('  if (%s != 0.0) goto L%d;' % (q(i.a), i.target))
        elif op == JMP:
            E('  goto L%d;' % i.target)
        elif op == RET_V:
            E('  return %s;' % q(i.a))
        elif op == CALL_USER:
            nt = getattr(self.snaps.get(name), 'name_table', None) or []
            nm = nt[i.func_idx] if 0 <= i.func_idx < len(nt) else None
            key = self.fast_key(nm, i.argc) if nm else None
            if key is None:
                # 分析已保证可达调用全部命中快入口；防御性到达即编译期报错
                raise CgenError('N-04: 快体内出现无快入口的调用 %r' % (nm,))
            args = ', '.join([q(s) for s in i.args] + ['depth + 1'])
            E('  %s = %s(%s);' % (q(i.dst), self.symbol(nm, i.argc), args))
        else:
            raise CgenError('N-04: 快体内出现不支持的指令 op=%d' % op)


def _emit_obj_table(cg, E):
    """【M4】类型表发射：foff（fid→字段偏移）+ mfns（mid→方法函数指针）
    + qotrt_types[] + qotrt_ntypes。主 TU 单点定义（qot_aot_runtime.h 已有
    extern 声明 → 定义非 static，与 qot_clo_fns 同一模式；无类程序发射
    空表 + 0 —— FIELD op 不可达，防御性兜底）。

    前置：方法函数的前向声明已就位（单 TU：generate 的前向声明区；
    split：qot_parts.h 的全函数 extern 声明）。
    """
    meta = getattr(cg.prog, 'objmeta', None)
    if not meta or not meta.get('classes'):
        E('QTypeDesc qotrt_types[1] = { { "", 0, nullptr, nullptr } };')
        E('int qotrt_ntypes = 0;')
        return
    field_ids = meta.get('field_ids') or {}
    method_ids = meta.get('method_ids') or {}
    nf = int(meta.get('nfields') or 0)
    nm = int(meta.get('nmethods') or 0)
    for k, (cname, fields, methods) in enumerate(meta['classes']):
        # foff：全局 fid → 该类的字段偏移（-1 = 无该字段）
        foff = ['-1'] * max(1, nf)
        for off, fname in enumerate(fields):
            fid = field_ids.get(fname)
            if fid is not None and 0 <= fid < max(1, nf):
                foff[fid] = str(off)
        E('static const int32_t qot_t%d_foff[%d] = { %s };'
          % (k, max(1, nf), ', '.join(foff)))
        # mfns：全局 mid → 方法函数指针（0 = 无该方法）
        mfns = ['(QotCloFn)0'] * max(1, nm)
        for mname, mangled in (methods or {}).items():
            mid = method_ids.get(mname)
            if mid is not None and 0 <= mid < max(1, nm):
                mfns[mid] = '(QotCloFn)%s' % cg._symbol_by_name(mangled)
        E('static const QotCloFn qot_t%d_mfns[%d] = { %s };'
          % (k, max(1, nm), ', '.join(mfns)))
    rows = []
    for k, (cname, fields, methods) in enumerate(meta['classes']):
        rows.append('  { "%s", %d, qot_t%d_foff, qot_t%d_mfns }'
                    % (cname, len(fields), k, k))
    E('QTypeDesc qotrt_types[%d] = {' % len(meta['classes']))
    for r in rows:
        E(r + ',')
    E('};')
    E('int qotrt_ntypes = %d;' % len(meta['classes']))


def generate(prog, source_file='', fast_snaps=None):
    """LirProgram → 完整 C++ 源码字符串。抛 CgenError 表示程序含 AOT 不支持的构造。

    fast_snaps：N-04 快照（lir_opt.snapshot_user_funcs 的产物）。None/缺省 =
    不生成双体快速入口（单元测试与回归对照用）。
    """
    cg = Cg(prog, fast_snaps=fast_snaps)
    cg.collect_calls()
    cg.plan_closures()
    E = cg.emit
    E('// Generated by lir_cgen.py (QOT 后端 A) — DO NOT EDIT')
    E('// source: %s' % source_file.replace('\\', '/'))
    if cg.unsafe_index:
        E('// CONFIG: QOT_UNSAFE_INDEX=1 —— 边界检查已按编译配置移除；'
          '越界为未定义行为，安全性由开发者自担（上帝法则）')
    if not cg.do_append:
        E('// CONFIG: QOT_NO_APPEND=1 —— 独占追加快路径已关闭（A/B 归因用）')
    if not cg.do_idxfuse:
        E('// CONFIG: QOT_NO_IDXFUSE=1 —— 下标+算术融合已关闭（A/B 归因用）')
    E('#include "%s"' % RUNTIME_INCLUDE)
    E('')
    # GUI（vg_*）：原型注进生成源——实现独立成 TU（ot-asset/c/wingui/
    # qotrt_gui.cpp），链接期由 qot_cache.gui_objects() 提供。runtime.h
    # 不含 GUI：不用 GUI 的程序零牵连（特性=库）。
    _vg = set()
    for _f in prog.all_funcs():
        _nt = getattr(_f, 'name_table', None) or []
        for _i in _f.instrs:
            if _i.op == CALL_BUILTIN and 0 <= _i.name_idx < len(_nt):
                _nm = _nt[_i.name_idx]
                if _nm.startswith('vg_'):
                    _vg.add(_nm)
    if _vg:
        E('/* GUI 内置原型（实现：ot-asset/c/wingui/qotrt_gui.cpp）*/')
        E('extern "C" {')
        for _n in sorted(_vg):
            E('V16 qotrt_%s(V16 *args, int argc);' % _n)
        E('}')
        E('')
    cg.emit_consts()
    E('')
    E('static V16 g[%d];' % max(1, cg._global_upper_bound()))
    E('')
    # 前向声明（用户函数互调/递归；S-03 深度参数；N-04 快入口；F1 env 形参）
    for f in prog.funcs.values():
        if cg._fn_has_env(f):
            E('static void %s(V16 *ret, V16 *args, int argc, int depth, QEnv *envp);'
              % cg._symbol(f))
        else:
            E('static void %s(V16 *ret, V16 *args, int argc, int depth);' % cg._symbol(f))
    if cg.fast is not None:
        cg.fast.emit_decls(E)
    E('')
    # F1：闭包入口表。位置纪律 = 紧随前向声明（表项取函数地址，前向声明
    # 已就位），且先于一切函数体（CALL_DYNAMIC 可出现在任何用户函数里）。
    # 空表也要落一个占位项：CALL_DYNAMIC 的引用必须可解析；此时程序不可能
    # 持有 VT_CLOSURE 值，运行期必经 fail_not_callable，占位项永不被调用。
    if cg.clo_index:
        E('QotCloFn qot_clo_fns[%d] = {' % len(cg.clo_index))
        for nm in sorted(cg.clo_index):
            E('  %s,' % cg._symbol_by_name(nm))
        E('};')
    else:
        E('QotCloFn qot_clo_fns[1] = { (QotCloFn)0 };')
    # P0-E 高阶原语（arr_map/arr_filter/arr_reduce/arr_sort_by）从 qotrt 侧
    # 回调闭包需要这张表 → 去 static（原 TU-local），配 qotrt_clo_count 做
    # 边界守卫。qot_aot_runtime.h 里有对应的 extern 声明（单一声明点）。
    E('int qotrt_clo_count = %d;' % len(cg.clo_index))
    E('')
    # M4：类型表（前向声明已就位 —— 表项取方法函数地址）
    _emit_obj_table(cg, E)
    E('')
    if cg.fast is not None:
        cg.fast.emit_fast_funcs(E)
    for f in prog.funcs.values():
        cg.emit_func(f, cg._symbol(f), is_main=False)
    # F1：闭包入口表已随前向声明提前（见上）。
    cg.emit_func(prog.main, 'qot_main', is_main=True)
    E('')
    E('int main(int argc, char **argv) {')
    E('  qotrt_init_args(argc, argv); // P0.2 args_get：脚本参数注册（argv[1..]）')
    E('  qotrt_init_limits();')
    E('  qotrt_init_consts();')
    E('  V16 r;')
    E('  qot_main(&r, (V16*)0, 0);')
    E('  return 0;')
    E('}')
    return '\n'.join(cg.lines) + '\n'


# ================================================================ QOT-CI L1（CP-C2）切分 TU 发射
# 语义口径：与 generate() **同一份 LIR、同一个 Cg**（同样的全程序优化管线
# 产物），只是发射布局从"单 TU"改为"共享头 + 主 TU + 模块 TU"。因此：
#   * 每个函数的函数体 C 文本与单 TU 模式逐行一致（仅 static → extern）；
#   * 跨模块内联/类型推断等全程序优化**原样保留**（G1 的最强形态）；
#   * "导出面指纹"由 qot_build 以 **TU 的 C 文本 hash** 实现——比名字/签名
#     级指纹严格更精确（内联体已物理进入下游 TU 的 C 文本时，下游必然重编，
#     这不是缓存缺陷而是正确性要求）。

# QOT_RT_SPLIT 状态定义块：39 个单实例运行时状态（与 runtime 头的
# #ifdef QOT_RT_SPLIT extern 声明逐项配对；初值照抄头内原 static 定义）。
RT_STATE_DEFS = (
    'QotrtPool qotrt_pool = { {0}, nullptr, 0 };',
    'QotrtArenaMark qotrt_arena_stack[8];',
    'int qotrt_arena_sp = 0;',
    'QotrtLanding *qotrt_landings = nullptr;',
    'V16 qotrt_thrown;',
    'V16 *qotrt_consts = nullptr;',
    'int qotrt_nconsts = 0;',
    'long long qotrt_ticks = 0;',
    'long long qotrt_tick_limit = -1;',
    'int qotrt_max_depth = 5000;',
    'int qotrt_cb_depth = 0;',
    'QrtFileSlot *qotrt_ftab = nullptr;',
    'int32_t qotrt_ftab_cap = 0;',
    'int32_t qotrt_ftab_free = -1;',
    'uint64_t qotrt_rand_state = 0x853C49E6748FEA9BULL;',
    'int qotrt_os_argc = 0;',
    'char **qotrt_os_argv = nullptr;',
    'QrtProcSlot *qotrt_ptab = nullptr;',
    'int32_t qotrt_ptab_cap = 0;',
    'int32_t qotrt_ptab_free = -1;',
    'QrtFfiLibSlot *qotrt_ffitab = nullptr;',
    'int32_t qotrt_ffitab_cap = 0;',
    'int32_t qotrt_ffitab_free = -1;',
    'QrtFfiSymSlot *qotrt_fsymtab = nullptr;',
    'int32_t qotrt_fsymtab_cap = 0;',
    'int32_t qotrt_fsymtab_free = -1;',
    'char qotrt_ffi_err[256];',
    'QrtSockSlot *qotrt_stab = nullptr;',
    'int32_t qotrt_stab_cap = 0;',
    'int32_t qotrt_stab_free = -1;',
    # ---- L2 原生插件（M7 · 0）----
    'QrtPlugSlot *qotrt_plugtab = nullptr;',
    'int32_t qotrt_plugtab_cap = 0;',
    'int32_t qotrt_plugtab_free = -1;',
    'QrtPlugFn *qotrt_pfn = nullptr;',
    'int32_t qotrt_pfn_cap = 0;',
    'int32_t qotrt_pfn_len = 0;',
    'int32_t qotrt_pfn_loading = -1;',
)
RT_STATE_DEFS_WIN = (
    'LPWSTR *qotrt_os_wargv = nullptr;',
    'int qotrt_os_wargc = 0;',
)


class SplitCtx(object):
    """prepare_split 的产物（QOT-CI · CP-C5）：TU 划分 + 共享头 + 发射上下文。

    发射顺序无关（TU 记忆化的前提）：label 为 pc 基（L%d % pc），每个 TU 的
    文本只由【该 TU 各函数 stage-2 后的 LIR + 全局 cgen 上下文（闭包表布局/
    常量池/头声明/开关）】决定 —— 单独发射一个 TU 与全量发射时逐字节一致。
    """
    __slots__ = ('prog', 'cg', 'source_file', 'fn_tu', 'fn_tu_by_name',
                 'main_tu', 'mod_order', 'header')


def prepare_split(prog, source_file='', fast_snaps=None, module_of=None):
    """generate_split 的前半（CP-C5 拆分点）：Cg 上下文 + TU 归属 + 共享头。

    调用时机 = stage-1（inline/promote/snapshot/infer，含全局分析）之后。
    头部内容（函数声明 / g[N] / 快入口声明）只依赖签名面：stage-2（窥孔/
    SSA/arena，均为逐函数 pass）不改签名与全局槽界，故在此一次成型、复用。
    """
    module_of = module_of or {}
    cg = Cg(prog, fast_snaps=fast_snaps)
    cg.collect_calls()
    cg.plan_closures()
    if cg.fast is not None:
        cg.fast.extern = True
    cg.linkage = 'extern'

    # ---- 归属与 TU 命名 ----
    tu_names = {}
    used = set()

    def _tu_name(mod):
        if mod in tu_names:
            return tu_names[mod]
        base = _sanitize(mod or 'm')
        name, k = base, 1
        while name in used or name == 'main':
            k += 1
            name = '%s_%d' % (base, k)
        used.add(name)
        tu_names[mod] = name
        return name

    fn_tu = {}
    for f in prog.funcs.values():
        if not f.name:
            fn_tu[id(f)] = 'main'  # 防御：无名函数归主 TU（不丢弃）
            continue
        fn_tu[id(f)] = _tu_name(module_of.get(f.name, 'main'))

    ctx = SplitCtx()
    ctx.prog = prog
    ctx.cg = cg
    ctx.source_file = source_file
    ctx.fn_tu = fn_tu
    ctx.main_tu = 'main'  # 主 TU 固定名（qot_build 用它定 exe 档名）

    # fn_tu_by_name：函数名 → TU（快入口归属用；funcs 键序无关）
    fn_tu_by_name = {}
    for f in prog.funcs.values():
        if f.name:
            fn_tu_by_name[f.name] = fn_tu.get(id(f), ctx.main_tu)
    ctx.fn_tu_by_name = fn_tu_by_name

    # ---- 共享头 ----
    saved = cg.lines
    cg.lines = []
    try:
        E = cg.emit
        E('// Generated by lir_cgen.py (QOT 后端 A · split-TU) — DO NOT EDIT')
        E('// 跨 TU 符号面：全局槽 / 用户函数 / N-04 快入口。')
        E('#ifndef QOT_PARTS_H_')
        E('#define QOT_PARTS_H_')
        E('#include "%s"' % RUNTIME_INCLUDE)
        E('')
        # GUI（vg_*）：split-TU 形态同样把原型放共享头（实现 = qotrt_gui.cpp TU）
        _vg = set()
        for _f in prog.all_funcs():
            _nt = getattr(_f, 'name_table', None) or []
            for _i in _f.instrs:
                if _i.op == CALL_BUILTIN and 0 <= _i.name_idx < len(_nt):
                    _nm = _nt[_i.name_idx]
                    if _nm.startswith('vg_'):
                        _vg.add(_nm)
        if _vg:
            E('/* GUI 内置原型（实现：ot-asset/c/wingui/qotrt_gui.cpp）*/')
            E('extern "C" {')
            for _n in sorted(_vg):
                E('V16 qotrt_%s(V16 *args, int argc);' % _n)
            E('}')
            E('')
        E('extern V16 g[%d];' % max(1, cg._global_upper_bound()))
        E('')
        for f in prog.funcs.values():
            if cg._fn_has_env(f):
                E('void %s(V16 *ret, V16 *args, int argc, int depth, QEnv *envp);'
                  % cg._symbol(f))
            else:
                E('void %s(V16 *ret, V16 *args, int argc, int depth);'
                  % cg._symbol(f))
        if cg.fast is not None:
            cg.fast.emit_decls(E)
        E('')
        E('#endif')
        ctx.header = '\n'.join(cg.lines) + '\n'
    finally:
        cg.lines = saved

    # 模块 TU 稳定序（prog.funcs 迭代序的首次出现序；与发射次序无关）
    mod_order = []
    for f in prog.funcs.values():
        if not f.name:
            continue
        tu = fn_tu.get(id(f), ctx.main_tu)
        if tu != ctx.main_tu and tu not in mod_order:
            mod_order.append(tu)
    ctx.mod_order = mod_order
    return ctx


def _split_emit_fast_of(ctx, tu):
    cg = ctx.cg
    if cg.fast is None:
        return
    for (nm, argc) in sorted(cg.fast.forms):
        # forms 键是函数名：归属经 fn_tu_by_name 查表（快入口与慢入口同 TU）
        owner_tu = ctx.fn_tu_by_name.get(nm, ctx.main_tu)
        if owner_tu == tu:
            cg.fast._emit_one(cg.emit, nm, argc)


def _split_emit_funcs_of(ctx, tu, is_main_tu=False):
    """发射属于 tu 的用户函数（保持 prog.funcs 稳定序）。"""
    cg = ctx.cg
    for f in ctx.prog.funcs.values():
        if ctx.fn_tu.get(id(f), ctx.main_tu) == tu:
            cg.emit_func(f, cg._symbol(f), is_main=False)
    if is_main_tu:
        cg.linkage = 'static'
        cg.emit_func(ctx.prog.main, 'qot_main', is_main=True)
        cg.linkage = 'extern'


def emit_split_tu(ctx, tu, is_main_tu=False):
    """发射单个 TU 的 C 文本（CP-C5）。

    前置：该 TU 的函数已完成 stage-2 特化（qot_build 的子程序 pass 原地
    改写同一批 LirFunction 对象）。is_main_tu=True 发射主 TU（运行时状态
    单实例 / g[] / 常量池 / 闭包表 / main()）。
    """
    cg = ctx.cg
    saved = cg.lines
    cg.lines = []
    try:
        E = cg.emit
        if is_main_tu:
            E('// Generated by lir_cgen.py (QOT 后端 A · split-TU) — DO NOT EDIT')
            E('// source: %s' % ctx.source_file.replace('\\', '/'))
            if cg.unsafe_index:
                E('// CONFIG: QOT_UNSAFE_INDEX=1 —— 边界检查已按编译配置移除；'
                  '越界为未定义行为，安全性由开发者自担（上帝法则）')
            if not cg.do_append:
                E('// CONFIG: QOT_NO_APPEND=1 —— 独占追加快路径已关闭（A/B 归因用）')
            if not cg.do_idxfuse:
                E('// CONFIG: QOT_NO_IDXFUSE=1 —— 下标+算术融合已关闭（A/B 归因用）')
            E('#include "qot_parts.h"')
            E('')
            E('// QOT_RT_SPLIT：运行时状态单实例定义（声明在 runtime 头 extern）')
            for d in RT_STATE_DEFS:
                E(d)
            if sys.platform == 'win32':
                for d in RT_STATE_DEFS_WIN:
                    E(d)
            E('')
            E('V16 g[%d];' % max(1, cg._global_upper_bound()))
            E('')
            cg.emit_consts()
            E('')
            if cg.clo_index:
                E('QotCloFn qot_clo_fns[%d] = {' % len(cg.clo_index))
                for nm in sorted(cg.clo_index):
                    E('  %s,' % cg._symbol_by_name(nm))
                E('};')
            else:
                E('QotCloFn qot_clo_fns[1] = { (QotCloFn)0 };')
            E('int qotrt_clo_count = %d;' % len(cg.clo_index))
            E('')
            # M4：类型表（主 TU 单点定义；qot_parts.h 的函数 extern 声明
            # 已覆盖方法符号）
            _emit_obj_table(cg, E)
            E('')
            _split_emit_fast_of(ctx, ctx.main_tu)
            _split_emit_funcs_of(ctx, ctx.main_tu, is_main_tu=True)
            E('')
            E('int main(int argc, char **argv) {')
            E('  qotrt_init_args(argc, argv); // P0.2 args_get：脚本参数注册（argv[1..]）')
            E('  qotrt_init_limits();')
            E('  qotrt_init_consts();')
            E('  V16 r;')
            E('  qot_main(&r, (V16*)0, 0);')
            E('  return 0;')
            E('}')
        else:
            E('// Generated by lir_cgen.py (QOT 后端 A · split-TU) — DO NOT EDIT')
            E('// module TU: %s' % tu)
            E('#include "qot_parts.h"')
            E('')
            _split_emit_fast_of(ctx, tu)
            _split_emit_funcs_of(ctx, tu)
        return '\n'.join(cg.lines) + '\n'
    finally:
        cg.lines = saved


def generate_split(prog, source_file='', fast_snaps=None, module_of=None):
    """LirProgram → 多 TU C 包（CP-C2 既有契约）。返回 dict：

        header  共享头（qot_parts.h 文本；含全部跨 TU 声明）
        main    主 TU 文本（状态定义 + g/consts/闭包表 + main 程序函数 + main()）
        tus     [(tu_name, cpp 文本)] —— 模块 TU（按模块归属）
        module_map  {函数名: tu_name}（审计/解释用）

    module_of：函数名 → 模块名（require_inliner.module_funcs_index 的产物）。
    未命中（主程序函数/嵌套闭包）→ 主 TU。
    CP-C5 起增量路径走 prepare_split + qot_build.tu_bundle（按 TU 记忆化）；
    本函数保留为"全程序 stage-2 已完成"的全量发射形态（冷路径/对照）。
    """
    ctx = prepare_split(prog, source_file, fast_snaps=fast_snaps,
                        module_of=module_of)
    main_src = emit_split_tu(ctx, ctx.main_tu, is_main_tu=True)
    tus = []
    for tu in ctx.mod_order:
        src = emit_split_tu(ctx, tu)
        if src.strip():
            tus.append((tu, src))
    module_map = {f.name: ctx.fn_tu.get(id(f), ctx.main_tu)
                  for f in prog.funcs.values() if f.name}
    return {'header': ctx.header, 'main': main_src, 'tus': tus,
            'module_map': module_map}
