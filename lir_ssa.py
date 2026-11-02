# -*- coding: utf-8 -*-
"""N-03 第二、三段：构造性 SSA（块参数）+ 线性扫描寄存器分配（槽合并形式）。

定位（ §1.3）：
    v2（lir2.py，块 IR + CFG/支配/循环/活跃性）是地基；本文件在其上做
    「每槽每定义一个版本」的 SSA 化 + 活区间 + 线性扫描分配。
    后端 A 的「寄存器」= C 局部变量（cl /O2 寄存器化），因此分配目标是
    **最小化不同槽位（颜色）数**：颜色越少 → C 局部越少 → 函数入口的
    qotrt_none_v() 初始化越少、cl 的栈帧与同时活值集合越小；同时把 MOV
    （源/目标同色）整条删除——这就是「消除中间 MOV」的兑现形式。

算法（Hack《Register Allocation for Programs in SSA Form》的 C 后端变体）：
    1. 循环携带槽不版本化（见下「与 N-02 的契约」）；
    2. 其余局部槽做支配树重命名的 pruned SSA；多前驱汇合点用**块参数**
       表达（每条入边尾部插一条拷贝），临界边先拆分；
    3. 读前无定义的槽给「入口值」（prologue 的 qotrt_none_v() 初始化就是
       它的定义——保住 VM 的 NONE 语义）；
    4. 值级活跃性 → 区间取【全部活性点的包络】（min/max，见 _intervals 的
       正确性注记：线性布局上回边会让活性「绕回」，单纯 def/use 端点会
       低估区间 → 两值被误判不交 → 同色竞争。用逐点活性集合求包络则
       覆盖所有活性点，只会高估不会低估——高估只损失合并机会，不损失
       正确性）；
    5. 线性扫描着色（无溢出变体：颜色 = 新槽号，自由表回收过期颜色）；
    6. MOV 边合并（copy coalescing）：仅当两颜色类的区间两两不交；
    7. 改写回 v1（to_v1 单次布局），槽总数 = 颜色数。

与 N-02（循环去装箱）的契约 —— 为什么循环携带槽不版本化：
    unbox_loops 的约束 A2 要求「变体槽在循环体内的每个定义都是组尾
    STORE_F64」。SSA 化若给循环携带值插块参数，回边尾部就会多出一条
    MOV 定义 → A2 失败 → 该循环被整体否决（空循环 1.66 → ~37 ns 的
    灾难性回退）。因此：live_in(任一循环头) 中的槽保持**可变槽语义**
    （单值、多定义、无块参数）——正好就是 unbox 今天看到的形态；它们的
    初始化 MOV 仍可通过与短命临时合并而被删除。
    非 SSA 化的部分在文档上如实登记为已知边界（SPEC §10）。

f 域第二段（coalesce_f，unbox 之后运行）：
    同一引擎换 f 域。特化：循环镜像槽按「循环携带」处理（unbox 出边回写
    依赖其可变槽语义）；**组头保护**——慢路径 F64 组（_match_group 命中）
    的组头 MOV_F 绝不删除，否则 cgen 的组匹配失配、LOAD_F64 的 guard
    兜底丢失（语义风险，见 _try_f64_group 文档）。

安全性要点（每一条都有对应守卫）：
    * 全局槽（GLOBAL_BIT）不版本化、不重编号——跨函数可见性由内存承担；
    * 用户函数的局部槽无别名（参数按值拷贝、无取址）→ SSA 合法；
    * 不可达块：整体跳过版本化，操作数映射到「颜色基数 + 原槽号」的
      私有空间（永不执行但必须可编译）；
    * v 域形参槽 [0, n_bind_slots) 的入口/携带值钉住颜色 0..nb-1
      （cgen 的绑定序列按 args[k] → sK 下标写，重编号会静默错位）；
      其余值从 nb 起分配——形参绑定写在 prologue，非形参值绝不能落进
      会被 prologue 覆盖、而旧值仍可能被读的存储（入口回边未定义读取
      的语义保护）；
    * 颜色类合并用原始区间两两求交，拒绝即保持分离（保守即安全）；
    * v 域前置断言：f 域指令与 GUARD_NUM 不得存在（本 pass 在 infer_f64
      与 unbox 之前运行）。
    端到端护栏：--suite native 全量差分 + fuzz + 单测 test_lir_ssa.py。

A/B 开关：QOT_NO_SSA=1 关 v 域；QOT_NO_FCOAL=1 关 f 域（pass 自检）。
"""
import os

from lir2 import (
    slot_ops, clone_instr,  # noqa: F401  clone_instr 备用
    replace_vuse,
    _layout,
)
from lir import (
    LirInstr,
    CONST_F64, CONST_VAL, LOAD_F64, STORE_F64, MOV_V, MOV_F,
    ADD_F, SUB_F, MUL_F, DIV_F, MOD_F, POW_F, CMP_F, NEG_F,
    ARITH_GEN, UNARY_GEN, BR_IF_NOT, BR_IF, BR_F, JMP, RET_V, RET_F,
    CALL_USER, CALL_BUILTIN, CALL_MODULE, CALL_DYNAMIC,
    INDEX_GET, INDEX_SET, MAKE_ARRAY, MAKE_DICT, REQUIRE, STOP, NOP,
    GUARD_NUM, GLOBAL_BIT,
    THROW, TRY_PUSH, TRY_POP, ENV_NEW, MAKE_CLOSURE, THROWVAL, UPVAL_BIT,
    FIELD_GET, FIELD_SET, CALL_OMETHOD, GUARD_TYPE,
)

_F_USE_A = (MOV_F, STORE_F64, NEG_F, BR_F, RET_F)
_F_USE_AB = (ADD_F, SUB_F, MUL_F, DIV_F, MOD_F, POW_F, CMP_F)

# v 域「读取」操作数字段表（与 lir2 的 _V_USE_* 同源，G-2 完备性约定）
# M4（0）：FIELD_GET 读 a；FIELD_SET 读 a/c；CALL_OMETHOD 读 args
_V_READ_A = (LOAD_F64, MOV_V, BR_IF_NOT, BR_IF, RET_V, UNARY_GEN, THROW,
             FIELD_GET)
_V_READ_AB = (ARITH_GEN, INDEX_GET)
_V_READ_ABC = (INDEX_SET, FIELD_SET)
_V_READ_ARGS = (CALL_USER, CALL_BUILTIN, CALL_MODULE, MAKE_ARRAY, MAKE_DICT,
                GUARD_NUM, GUARD_TYPE, CALL_OMETHOD)
_V_READ_A_ARGS = (CALL_DYNAMIC,)


def collect_use_fields(i, domain):
    """列出指令 i 在指定域内的全部读取操作数字段。

    返回 [(field, slot)]；field ∈ 'a' | 'b' | 'c' | ('args', k)。
    改写必须先收集后应用（并行重命名语义）：顺序替换会在
    「8→2 之后又 2→1」时把刚改的字段二次改写（实测：i = i + 1 的
    ARITH_GEN 两操作数被改写成同色）。def 字段不进本表（单独映射）。
    """
    op = i.op
    out = []
    if domain == 'v':
        if op in _V_READ_A:
            out.append(('a', i.a))
        elif op in _V_READ_AB:
            out += [('a', i.a), ('b', i.b)]
        elif op in _V_READ_ABC:
            out += [('a', i.a), ('b', i.b), ('c', i.c)]
        elif op in _V_READ_A_ARGS:
            out.append(('a', i.a))
            for k, s in enumerate(i.args):
                out.append((('args', k), s))
        elif op in _V_READ_ARGS:
            for k, s in enumerate(i.args):
                out.append((('args', k), s))
    else:
        if op in _F_USE_A:
            out.append(('a', i.a))
        elif op in _F_USE_AB:
            out += [('a', i.a), ('b', i.b)]
    return [(f, s) for (f, s) in out if s >= 0]


def apply_use_fields(i, fields, mapping):
    """按 collect_use_fields 的快照并行改写。mapping: slot -> 新号。"""
    for (f, s) in fields:
        c = mapping.get(s)
        if c is None:
            continue
        if f == 'a':
            i.a = c
        elif f == 'b':
            i.b = c
        elif f == 'c':
            i.c = c
        else:
            i.args[f[1]] = c


class SsaError(Exception):
    pass


class _Val(object):
    """一个 SSA 值（或循环携带槽/全局槽的单值化身）。"""

    __slots__ = ('vid', 'slot', 'kind', 'start', 'end', 'color')

    def __init__(self, vid, slot, kind):
        self.vid = vid
        self.slot = slot            # 原槽号（诊断用）
        self.kind = kind            # 'ssa' | 'carry' | 'entry' | 'global'
        self.start = None
        self.end = None
        self.color = None

    def __repr__(self):
        return 'v%d(s%d,%s[%s..%s]c%s)' % (self.vid, self.slot, self.kind,
                                           self.start, self.end, self.color)


# ================================================================ 入口
def ssa_regalloc(prog):
    """v 域：SSA 化 + 槽合并（infer_f64 之前、unbox 之前运行）。"""
    if os.environ.get('QOT_NO_SSA') == '1':
        return {'funcs': 0, 'movs_removed': 0, 'slots_before': 0,
                'slots_after': 0}
    stats = {'funcs': 0, 'movs_removed': 0, 'slots_before': 0, 'slots_after': 0}
    pool = getattr(prog, 'const_pool', None)
    for f in prog.all_funcs():
        if not _v_applicable(f):
            continue
        r = _DomainAlloc(f, domain='v', const_pool=pool).run()
        stats['funcs'] += 1
        stats['movs_removed'] += r['movs_removed']
        stats['slots_before'] += r['slots_before']
        stats['slots_after'] += r['slots_after']
    errs = prog.verify()
    if errs:
        raise SsaError('N-03 SSA 改写后 LIR 自检失败: %s' % '; '.join(errs[:5]))
    return stats


def coalesce_f(prog):
    """f 域：MOV_F 合并（unbox_loops 之后、cgen 之前运行）。

    组头保护在 from_v1 **之后**按块计算（from_v1 会克隆指令对象，
    克隆前按 id 标记必然失配——组只在块内直线区段上，块内匹配与
    线性流匹配等价）。
    """
    if os.environ.get('QOT_NO_FCOAL') == '1':
        return {'funcs': 0, 'movs_removed': 0}
    stats = {'funcs': 0, 'movs_removed': 0}
    for f in prog.all_funcs():
        r = _DomainAlloc(f, domain='f', protected='groups').run()
        stats['funcs'] += 1
        stats['movs_removed'] += r['movs_removed']
    errs = prog.verify()
    if errs:
        raise SsaError('N-03 f 域合并后 LIR 自检失败: %s' % '; '.join(errs[:5]))
    return stats


_V_FORBIDDEN = ()


def _v_applicable(f):
    """v 域前置条件：只要非空、且不含 F1/F2 特征指令即可。

    upval/env 槽是跨激活共享存储，SSA 版本化与槽合并会改写它们的编号
    （cgen 按 UPVAL_BIT 解码 level/idx）→ 含闭包/异常特征的函数整体跳过。
    其余函数照旧：GUARD 的参数是 v 读取（重命名照常处理），失败边是普通
    CFG 边；f 操作数原样透传（f 域由 coalesce_f 处理完毕）。
    """
    if not f.instrs:
        return False
    for i in f.instrs:
        if i.op in (THROW, TRY_PUSH, TRY_POP, ENV_NEW, MAKE_CLOSURE, THROWVAL):
            return False
        dv, uv, _df, _uf = slot_ops(i)
        for s in dv + uv:
            if s >= 0 and (s & UPVAL_BIT):
                return False
    return True


# ================================================================ 引擎
class _DomainAlloc(object):
    """单域（v 或 f）的 SSA 构造 + 活区间 + 线性扫描分配 + 改写。"""

    def __init__(self, f, domain, protected=None, const_pool=None):
        self.f = f
        self.domain = domain            # 'v' | 'f'
        self.protected = protected or set()   # id(instr) 不可删除的 MOV
        self.const_pool = const_pool
        self.f2 = None
        self.vals = []
        self.use_map = {}     # id(instr) -> {slot: _Val}（本域读取操作数）
        self.def_map = {}     # id(instr) -> _Val | None（本域定义）
        self.block_seq = {}   # bid -> [(instr, use_vals, def_val)]
        self.param_copy = {}  # id(instr) -> (param_val, src_slot)
        self.params = {}      # (bid, slot) -> param_val
        self.src_sync = {}    # id(instr) -> (sop, val_a, val_b)（_src_op 到达值）
        self.carry = set()
        self.fixed = {}       # slot -> 单值（carry / global）
        self.reach = set()

    # ---- 域判别 ----
    def _def_slot(self, i):
        if self.domain == 'v':
            dv, _uv, _df, _uf = slot_ops(i)
            for s in dv:
                if s >= 0 and not (s & GLOBAL_BIT):
                    return s
            return None
        _dv, _uv, df, _uf = slot_ops(i)
        for s in df:
            if s >= 0:
                return s
        return None

    def _use_slots(self, i):
        if self.domain == 'v':
            _dv, uv, _df, _uf = slot_ops(i)
            return [s for s in uv if s >= 0]
        _dv, _uv, _df, uf = slot_ops(i)
        return [s for s in uf if s >= 0]

    def _is_domain_slot(self, s):
        if self.domain == 'v':
            return s >= 0 and not (s & GLOBAL_BIT)
        return s >= 0

    def _new_val(self, slot, kind):
        v = _Val(len(self.vals), slot, kind)
        self.vals.append(v)
        return v

    def _f_slot_liveness(self):
        """f 域专用：f 槽的块级活性（lir2.compute_liveness 是 v 域的）。

        槽号本身不携带域信息——v5 与 f5 是两个东西。f 域的 carry/参数判定
        必须基于 f 操作数的向上暴露使用，否则 v 槽会混进 f 的 carry 集
        （bound_guard_num_to_str_loop 实证：镜像 f5 未被判携带 → 循环尾
        写入被合并删除 → 快循环比较值永不更新 → 死循环）。
        """
        f2 = self.f2
        use_sets, def_sets = {}, {}
        for b in f2.blocks:
            uv, dv = set(), set()
            for i in b.all_instrs:
                _dv, _uv, df, uf = slot_ops(i)
                for s in uf:
                    if s >= 0 and s not in dv:
                        uv.add(s)
                for s in df:
                    if s >= 0:
                        dv.add(s)
            use_sets[b.id] = uv
            def_sets[b.id] = dv
        live_in = {b.id: set() for b in f2.blocks}
        live_out = {b.id: set() for b in f2.blocks}
        order = f2.rpo()
        changed = True
        while changed:
            changed = False
            for bid in reversed(order):
                lo = set()
                for s in f2.blocks[bid].succs:
                    lo |= live_in.get(s, set())
                li = use_sets[bid] | (lo - def_sets[bid])
                if li != live_in[bid] or lo != live_out[bid]:
                    live_in[bid], live_out[bid] = li, lo
                    changed = True
        return live_in

    # ---- 主流程 ----
    def run(self):
        import lir2
        f = self.f
        slots_before = f.n_slots_v if self.domain == 'v' else f.n_slots_f
        f2 = lir2.from_v1(f)
        self.f2 = f2
        self.reach = f2.reachable()
        if self.protected == 'groups':
            self.protected = self._mark_group_heads()
        idom0 = f2.compute_idom()
        loops = f2.natural_loops(idom0)
        if self.domain == 'v':
            live_in, _lo = f2.compute_liveness()
        else:
            live_in = self._f_slot_liveness()
        for lp in loops:
            for s in live_in.get(lp.header, set()):
                if self._is_domain_slot(s):
                    self.carry.add(s)
        self._insert_params(live_in)
        # 拆分/插拷贝改了 CFG：支配树必须重算——旧 idom 不含 mid 块，
        # rename 会整块跳过（占位 MOV_V dst=-1 原样漏进 cgen → 运行期崩溃）
        idom = f2.compute_idom()
        self._rename(idom)
        self._intervals()
        ncolors = self._allocate()
        has_unreach = any(b.id not in self.reach for b in f2.blocks)
        base = ncolors
        removed = self._rewrite(ncolors, base)
        # 不可达块占用了 [base, base+原槽数) 的私有颜色空间
        final = ncolors + (slots_before if has_unreach else 0)
        if self.domain == 'v':
            f2.n_slots_v = final
        else:
            f2.n_slots_f = final
        lir2.to_v1(self.f2, self.f)
        return {'movs_removed': removed, 'slots_before': slots_before,
                'slots_after': final}

    def _mark_group_heads(self):
        """f 域：_match_group 命中的组头 MOV_F 标记为不可删除。"""
        import lir_opt
        protected = set()
        for b in self.f2.blocks:
            ins = b.instrs
            k = 0
            n = len(ins)
            while k < n:
                g = lir_opt._match_group(ins, k)
                if g is None:
                    k += 1
                    continue
                for h in g['heads']:
                    if ins[h].op == MOV_F:
                        protected.add(id(ins[h]))
                k += g['len']
        return protected

    # ---- 块参数与入边拷贝 ----
    def _insert_params(self, live_in):
        f2 = self.f2
        params = {}
        for b in f2.blocks:
            if b.id not in self.reach or len(b.preds) < 2:
                continue
            for s in sorted(live_in.get(b.id, set())):
                if self._is_domain_slot(s) and s not in self.carry:
                    params[(b.id, s)] = None
        if not params:
            self.params = params
            return
        succ_count = {}
        for b in f2.blocks:
            for s in b.succs:
                succ_count[s] = succ_count.get(s, 0) + 1
        # 临界边拆分（pred 多后继 ∧ succ 多前驱 ∧ succ 需要参数）
        for b in list(f2.blocks):
            if b.id not in self.reach or len(b.succs) < 2:
                continue
            for s in list(b.succs):
                if succ_count.get(s, 0) >= 2 and \
                        any((s, x) in params for x in live_in.get(s, set())
                            if self._is_domain_slot(x) and x not in self.carry):
                    f2.split_edge(b, s)
        f2.rebuild_cfg()
        # 拆分产生了新块：可达集必须**立即**重算——mid 块就是参数拷贝的
        # 落点，用旧可达集会把它们当不可达前驱跳过 → 块参数永远没有定义
        # （读到的颜色是别的值的存储 → 数据损坏，_probe_rec 实证）
        self.reach = f2.reachable()
        for (bid, s) in sorted(params):
            blk = f2.blocks[bid]
            if not blk.preds:
                params.pop((bid, s))
                continue
            pv = self._new_val(s, 'ssa')
            params[(bid, s)] = pv
            for p in sorted(blk.preds):
                if p not in self.reach:
                    continue    # 不可达前驱的边运行期不可达，无需拷贝
                pb = f2.blocks[p]
                op = MOV_V if self.domain == 'v' else MOV_F
                cp = LirInstr(op, dst=-1, a=s)   # dst 占位：改写期填颜色
                pb.instrs.append(cp)             # 块尾 = 进该边前最后执行
                self.param_copy[id(cp)] = (pv, s)
        self.params = params

    # ---- 支配树重命名（迭代式，防深块链爆栈）----
    def _rename(self, idom):
        f2 = self.f2
        children = {}
        for bid, dom in idom.items():
            if dom != bid:
                children.setdefault(dom, []).append(bid)
        stack = {}          # slot -> [value]（def 栈）
        self.block_params = {}
        for (bid, s), pv in self.params.items():
            self.block_params.setdefault(bid, []).append((s, pv))

        def current(s):
            """当前版本；无定义时的兜底（carry/global 单值或入口值）。

            入口值必须缓存（self.fixed）——同一槽的多次「读前无定义」是
            同一个值（prologue 的 none-init 只有一次）；不缓存会给每个
            读取点造一个假值，分裂颜色类并污染活性。
            """
            st = stack.get(s)
            if st:
                return st[-1]
            v = self.fixed.get(s)
            if v is not None:
                return v
            if s in self.carry or (not self._is_domain_slot(s)):
                kind = 'global' if not self._is_domain_slot(s) else 'carry'
            else:
                kind = 'entry'
            v = self._new_val(s, kind)
            self.fixed[s] = v
            return v

        def process(bid):
            blk = f2.blocks[bid]
            pushed = []
            # 块参数入栈（本块的多前驱汇合值）
            for (s, pv) in self.block_params.get(bid, []):
                stack.setdefault(s, []).append(pv)
                pushed.append((s, len(stack[s]) - 1))
            seq = []
            allins = list(blk.instrs)
            if blk.term is not None:
                allins.append(blk.term)
            for i in allins:
                pcp = self.param_copy.get(id(i))
                if pcp is not None:
                    pv, s = pcp
                    use_vals = {s: current(s)}
                    seq.append((i, use_vals, pv))
                    self.use_map[id(i)] = use_vals
                    self.def_map[id(i)] = pv
                    continue
                use_vals = {}
                for s in self._use_slots(i):
                    use_vals[s] = current(s)
                # _src_op 的到达值快照（v 域）：必须在 def 压栈**之前**记录——
                # 组的形态是 [头读 V; OP; 尾写 V]，尾自身的新版本不能污染
                # 头所读版本的解析（s = s + 1 型组实证）。
                if self.domain == 'v':
                    sop = getattr(i, '_src_op', None)
                    if sop is not None:
                        self.src_sync[id(i)] = (sop, current(sop.a),
                                                current(sop.b))
                ds = self._def_slot(i)
                def_val = None
                if ds is not None:
                    if ds in self.carry:
                        # 循环携带槽不版本化（与 N-02 的契约，见模块 docstring）：
                        # 所有定义共享同一个值——否则回边后的读取会拿到
                        # 支配树重命名给出的「过期版本」，可变槽语义被破坏
                        # （bound_guard_arith_str_flow 实证：k="y" 的赋值
                        #   被写到过期版本上，循环增量丢失 → 死循环）
                        def_val = current(ds)
                    else:
                        def_val = self._new_val(ds, 'ssa')
                        stack.setdefault(ds, []).append(def_val)
                        pushed.append((ds, len(stack[ds]) - 1))
                seq.append((i, use_vals, def_val))
                self.use_map[id(i)] = use_vals
                self.def_map[id(i)] = def_val
            self.block_seq[bid] = seq
            for c in children.get(bid, []):
                if c in self.reach:
                    process(c)
            for (s, depth) in reversed(pushed):
                st = stack.get(s)
                if st and len(st) > depth:
                    del st[depth:]

        process(f2.entry)
        for b in f2.blocks:
            if b.id not in self.block_seq:
                # 不可达块：记空序列；改写期单独映射原槽号
                self.block_seq[b.id] = []

    # ---- 值级活跃性（逐点）----
    def _intervals(self):
        """逐点活性 + 区间（区间只用于排序/诊断；着色正确性来自干涉图）。

        正确性注记：线性布局 + 回边会让活性「绕回」。曾用 def/use 端点的
        线性包络做着色判据——分支代码里 live-through 值的包络会把不相交的
        活性误判为相交，颜色数膨胀（实测 27 → 50 个 C 局部声明，帧反向
        回归）。现在包络只用于扫描顺序，干涉判定改为**逐点活性集合**
        （见 _build_graph）：分支两侧不同时活的值不会得边。
        """
        order = _layout(self.f2)
        pos = {}
        p = 0
        for bid in order:
            for (i, _u, _d) in self.block_seq.get(bid, []):
                pos[id(i)] = p
                p += 1
        self.order = order
        self.pos = pos
        use_sets, def_sets = {}, {}
        for bid in order:
            uv, dv = set(), set()
            for (i, use_vals, def_val) in self.block_seq.get(bid, []):
                # 只收「向上暴露」的使用：同块内先定义后使用的读取不算
                # （否则 live_in 被假使用污染，跨块活性整体高估——
                #   实测把 return-0 常量的活性拉满全函数，颜色数翻倍）
                for v in use_vals.values():
                    if v.vid not in dv:
                        uv.add(v.vid)
                if def_val is not None:
                    dv.add(def_val.vid)
            use_sets[bid] = uv
            def_sets[bid] = dv
        live_in = {b.id: set() for b in self.f2.blocks}
        live_out = {b.id: set() for b in self.f2.blocks}
        changed = True
        while changed:
            changed = False
            for bid in reversed(order):
                lo = set()
                for s in self.f2.blocks[bid].succs:
                    lo |= live_in.get(s, set())
                li = use_sets[bid] | (lo - def_sets[bid])
                if li != live_in[bid] or lo != live_out[bid]:
                    live_in[bid], live_out[bid] = li, lo
                    changed = True
        self.live_in, self.live_out = live_in, live_out
        # 逐点 live_before（按块存），供干涉图与区间
        by_id = {v.vid: v for v in self.vals}
        self.live_at = {}          # bid -> [ set(vid) per point ]（live_before）
        for bid in order:
            lo = set(live_out[bid])
            seq = self.block_seq.get(bid, [])
            pts = [None] * len(seq)
            for k in range(len(seq) - 1, -1, -1):
                (i, use_vals, def_val) = seq[k]
                pts[k] = set(lo)
                if def_val is not None:
                    lo.discard(def_val.vid)
                for v in use_vals.values():
                    lo.add(v.vid)
            self.live_at[bid] = pts
            _ = by_id
        for v in self.vals:
            v.start = None
            v.end = None
        for bid in order:
            seq = self.block_seq.get(bid, [])
            for k, (i, use_vals, def_val) in enumerate(seq):
                ppos = pos[id(i)]
                lo = self.live_at[bid][k]
                if def_val is not None:
                    v = by_id[def_val.vid]
                    v.start = ppos if v.start is None else min(v.start, ppos)
                    v.end = ppos if v.end is None else max(v.end, ppos)
                for vid in lo:
                    v = by_id[vid]
                    v.start = ppos if v.start is None else min(v.start, ppos)
                    v.end = ppos if v.end is None else max(v.end, ppos)
        for v in self.vals:
            if v.start is None:
                v.start = 0
            if v.end is None:
                v.end = v.start

    # ---- 干涉图 + 合并 + 着色 ----
    def _build_graph(self):
        """干涉图构造（0 重构：加入事件法，精确等价于逐点团）。

        【等价性证明（写死在这里，防后人翻烧饼）】
        旧实现：每个程序点对 live_after ∪ {def} 做两两团 —— O(Σ L²)。
        新实现：反向走块（逆 RPO），维护当前活集 L（进入块时 = live_out）；
        每个点先处理 def（与 L 建边 = 原团的 def 部分；再从 L 摘除），
        再处理本点读取且不在 L 的值（与 L 建边后加入 = 值的「最后使用」
        加入事件）。两值 u,v 干涉 ⟺ ∃ 点两者皆活 ⟺ 任取这样的点 p：
        WLOG last_use(v) ≥ last_use(u)，则 u 在 v 的加入点仍在 L（u 的
        活区间 [def_u, last_use_u] 覆盖该点，反向走尚未摘除）→ 边在 v
        加入时建立。def 的边：def 值在 L 中的区间是（反向走）[其最后
        使用 → 其定义点]——期间加入的任何值都会与它建边；「活穿整块」
        的值不会在任何点加入 → def 出生点显式与 L 建边兜住。两法产出的
        图逐位相同（集合语义），复杂度降为 O(Σ_defs|L| + Σ_adds|L|)。

        MOV 的 src–dst 边**不移除**——它记入 `copy_edges`（候选合并对），
        合并判定时忽略这类边（经典 coalescable-edge 规则），着色时它们
        已被并成同色、边自然消失。"""
        adj = {}          # vid -> set(vid)
        copy_edges = set()   # frozenset({a.vid, b.vid}) 候选合并对

        def add_edge(a, b):
            if a == b:
                return
            adj.setdefault(a, set()).add(b)
            adj.setdefault(b, set()).add(a)

        mov_op = MOV_V if self.domain == 'v' else MOV_F
        for bid in reversed(self.order):
            L = set(self.live_out.get(bid) or ())
            seq = self.block_seq.get(bid, [])
            for k in range(len(seq) - 1, -1, -1):
                (i, use_vals, def_val) = seq[k]
                if def_val is not None:
                    d = def_val.vid
                    if i.op == mov_op and id(i) not in self.protected:
                        for v in use_vals.values():
                            if v.kind != 'global':
                                copy_edges.add(frozenset((d, v.vid)))
                    if i.op in (MAKE_ARRAY, MAKE_DICT):
                        # cgen 的容器构造**先写 dst 再读 args**
                        # （s_dst = arr_new(); arr_push(s_dst, s_arg)...）——
                        # dst 与 arg 同色时 push 读到的是半成品容器自己
                        # （t03_ifarr 实证：arr[0] 变成数组自身 → "[...]40"）。
                        # 这类「def 先于 arg 消费」的指令必须强制 def–arg 边。
                        # 现清单：MAKE_ARRAY / MAKE_DICT。其余指令（ARITH/
                        # INDEX_GET/CALL_*）的发射都是先读后写，dst==arg 同色
                        # 安全，不加边（保住 x=f(x) 类合并空间）。
                        for v in use_vals.values():
                            if v.kind != 'global':
                                add_edge(d, v.vid)
                    # def 出生点：与 live_after 集合建边（原逐点团的 def 部分；
                    # 自环由 add_edge 守卫排除），然后从 L 摘除（反向走到达定义点）
                    for other in L:
                        add_edge(d, other)
                    L.discard(d)
                for v in use_vals.values():
                    vid = v.vid
                    if vid not in L:
                        for other in L:
                            add_edge(vid, other)
                        L.add(vid)
        # 形态感知（v 域）：不同「本地形态类」的值不得共享颜色。
        #
        # 为什么需要（两轮实测教训）：
        #   * infer_f64 是槽级写者分析——「dead null 临时」与热循环数值变量
        #     共槽会让 null 写入者毒化数值证明（empty_loop 1.9 → 18.5 ns）；
        #   * unbox 的 A2 约束要求候选槽在循环体内的定义都是组尾 STORE——
        #     const 临时与链变量共槽会引入非 STORE 定义，整条链退出候选
        #     （order_engine 快循环全灭、203 ns）。
        # 这里的分类是**本地、保守、无不动点**的（与 infer_f64 的全程序
        # 不动点分析不构成双实现——G-11：本分类只会更保守，只会损失合并
        # 机会，不会产生错误共享）。
        if self.domain == 'v':
            forms = self._value_forms()
            by_form = {}
            for v in self.vals:
                if v.kind == 'global':
                    continue
                by_form.setdefault(forms.get(v.vid, 'other'), []).append(v.vid)
            form_keys = sorted(by_form)
            for ai in range(len(form_keys)):
                for bi in range(ai + 1, len(form_keys)):
                    for a in by_form[form_keys[ai]]:
                        for b in by_form[form_keys[bi]]:
                            add_edge(a, b)
        self.adj = adj
        self.copy_edges = copy_edges

    def _value_forms(self):
        """值的本地形态类：'num' | 'nonnum'。

        num = 定义者 ∈ {数值常量 CONST_VAL, ARITH_GEN 1..6, UNARY_GEN 1,
        MOV_V(源为 num)}；其余（调用/下标/容器/比较/非数值常量/入口未定义）
        一律 nonnum。多定义值（carry/entry）任一定义非 num 即 nonnum。
        MOV 的流经语义：dst 类随 src（与 infer_f64 的 'flow' 同方向，但只做
        一层本地传播，无不动点——保守即安全）。
        """
        forms = {}
        # 逐值收集定义类别
        def_classes = {}
        pool = self.const_pool

        def const_is_num(idx):
            if pool is None or not isinstance(idx, int) or \
                    not (0 <= idx < len(pool)):
                return False
            v = pool[idx]
            return isinstance(v, (int, float)) and not isinstance(v, bool)

        for bid in self.block_seq:
            for (i, use_vals, def_val) in self.block_seq[bid]:
                if def_val is None or def_val.kind == 'global':
                    continue
                op = i.op
                if op == CONST_VAL:
                    cls = 'num' if const_is_num(i.a) else 'nonnum'
                elif op == ARITH_GEN and 1 <= i.pred <= 6:
                    cls = 'num'
                elif op == UNARY_GEN and i.pred == 1:
                    cls = 'num'
                elif op == MOV_V:
                    src = use_vals.get(i.a)
                    cls = ('num' if src is not None and
                           def_classes.get(src.vid) == {'num'} else 'nonnum')
                else:
                    cls = 'nonnum'
                def_classes.setdefault(def_val.vid, set()).add(cls)
        for vid, cls_set in def_classes.items():
            forms[vid] = 'num' if cls_set <= {'num'} else 'nonnum'
        # 无定义值（entry/carry/global 化身）：看它是否纯由 num 定义流经——
        # entry/carry 的定义都在 def_classes 里（carry 的全部定义都映射到
        # 同一值）；没有定义记录 → nonnum（保守）。
        for v in self.vals:
            if v.kind == 'global':
                continue
            if v.vid not in forms:
                forms[v.vid] = 'nonnum'
        return forms

    # ---- 合并 + 着色（SSA 弦图贪心）----
    def _allocate(self):
        nb = self.f.n_bind_slots if self.domain == 'v' else 0
        base = nb if self.domain == 'v' else 0
        self._build_graph()
        adj = self.adj
        copy_edges = self.copy_edges

        def is_copy_edge(a, b):
            return frozenset((a, b)) in copy_edges

        # 并查集
        parent = {}

        def find(v):
            r = v
            while parent.get(r, r) is not r:
                r = parent[r]
            while parent.get(v, v) is not r:
                nxt = parent[v]
                parent[v] = r
                v = nxt
            return r

        vals = [v for v in self.vals if v.kind != 'global']
        for v in vals:
            parent[v] = v
        members = {}
        for v in vals:
            members.setdefault(v, [v])   # 暂以自身为键；union 时合并

        def classes_mergeable(ra_list, rb_list):
            """两颜色类可合并 ⇔ 跨类无干涉边（copy 边不算干涉）。"""
            for x in ra_list:
                for y in rb_list:
                    if x.vid in adj.get(y.vid, ()) and \
                            not is_copy_edge(x.vid, y.vid):
                        return False
            return True

        def union(a, b):
            ra, rb = find(a), find(b)
            if ra is rb:
                return ra
            ma, mb = members.get(ra, [ra]), members.get(rb, [rb])
            if len(ma) < len(mb):
                ra, rb = rb, ra
                ma, mb = mb, ma
            parent[rb] = ra
            ma.extend(mb)
            members[ra] = ma
            members.pop(rb, None)
            return ra

        # 合并：按扫描序处理拷贝对（保守贪心；Briggs 风格的安全判据
        # 在「目标=最小化颜色数」的 C 后端场景下用精确跨类检查替代）
        mov_op = MOV_V if self.domain == 'v' else MOV_F
        for bid in self.order:
            for (i, use_vals, def_val) in self.block_seq.get(bid, []):
                if i.op != mov_op or def_val is None or len(use_vals) != 1:
                    continue
                if id(i) in self.protected and \
                        self.param_copy.get(id(i)) is None:
                    continue
                src = next(iter(use_vals.values()))
                if src.kind == 'global' or src is def_val:
                    continue
                ra, rb = find(def_val), find(src)
                if ra is rb:
                    continue
                if classes_mergeable(members.get(ra, [ra]),
                                     members.get(rb, [rb])):
                    union(def_val, src)
        # 钉形参（v 域）：entry/carry 形参槽 → 颜色 = 槽号
        if self.domain == 'v':
            for v in vals:
                if v.kind in ('entry', 'carry') and 0 <= v.slot < nb:
                    v.color = v.slot
        # 着色：按类起点排序的贪心（SSA 干涉图上等价于弦图最优序）
        class_of = {}
        for v in vals:
            class_of.setdefault(find(v), []).append(v)
        order_list = sorted(class_of.values(),
                            key=lambda ms: (min(m.start for m in ms),
                                            max(m.end for m in ms)))
        # 【常数 0】vid2val 提出循环外（原每类重建全值字典，
        # O(#类 × #值)）；类内只需查邻接色。
        vid2val = {m.vid: m for m in self.vals}
        for ms in order_list:
            if any(m.color is not None for m in ms):
                pc = next(m.color for m in ms if m.color is not None)
                for m in ms:
                    m.color = pc
                continue
            forbidden = set()
            for m in ms:
                for nb_vid in adj.get(m.vid, ()):
                    other = vid2val.get(nb_vid)
                    if other is not None and other.color is not None and \
                            not is_copy_edge(m.vid, nb_vid):
                        forbidden.add(other.color)
            c = base
            while c in forbidden:
                c += 1
            for m in ms:
                m.color = c
        used = [v.color for v in self.vals if v.color is not None]
        ncolors = max(used + [base - 1 if base > 0 else 0]) + 1
        if os.environ.get('QOT_DBG_SSA') == '1':
            print('[ssa:%s] carry=%r ncolors=%d' % (self.domain,
                                                    sorted(self.carry), ncolors))
            for v in sorted(self.vals, key=lambda x: x.vid):
                print('  v%d slot=%s kind=%s [%d..%d] color=%s adj=%d'
                      % (v.vid, v.slot, v.kind, v.start, v.end, v.color,
                         len(self.adj.get(v.vid, ()))))
        return max(ncolors, nb)

    # ---- 改写 ----
    # 注意：CONST_F64/STORE_F64/LOAD_F64/CMP_F 是 F64 组成员（v 域视角
    # def 为空或非纯），绝不可进本集合——删掉组头/组尾会破坏 cgen 的组匹配
    # 与兜底语义（f 域的同型风险见 coalesce_f 的组头保护）。
    _PURE_V = (CONST_VAL, MOV_V, ARITH_GEN, UNARY_GEN)
    _PURE_F = (CONST_F64, MOV_F, ADD_F, SUB_F, MUL_F, DIV_F, MOD_F, POW_F,
               NEG_F)

    def _find_dead_defs(self):
        """传递死定义：纯指令且其定义值全函数无使用 → 可删。

        为什么必须有（infer_f64 毒化实证）：颜色复用会让「STOP 前的 dead
        null 临时」与热循环变量共享槽号；infer_f64 是**槽级**写者分析，
        null 写入者（'other'）会把整个槽的数值证明毒化 → 热循环的算术
        全部退化回通用路径（empty_loop 1.9 → 18.5 ns/iter 实测）。
        在本 pass 内把死定义删干净，槽的写者集合才反映真实的值形态。
        """
        uses = {}
        for bid in self.block_seq:
            for (i, use_vals, def_val) in self.block_seq[bid]:
                for v in use_vals.values():
                    uses[v.vid] = uses.get(v.vid, 0) + 1
        pure = self._PURE_V if self.domain == 'v' else self._PURE_F
        dead = set()
        changed = True
        while changed:
            changed = False
            for bid in self.block_seq:
                for (i, use_vals, def_val) in self.block_seq[bid]:
                    if def_val is None or def_val.vid in dead:
                        continue
                    if i.op not in pure:
                        continue
                    if uses.get(def_val.vid, 0) == 0:
                        dead.add(def_val.vid)
                        for v in use_vals.values():
                            n = uses.get(v.vid, 0)
                            if n > 0:
                                uses[v.vid] = n - 1
                                changed = True
        return dead

    def _rewrite(self, ncolors, base):
        """改写可达块的操作数为颜色；同色 MOV/参数拷贝删除。

        关键细节：block_seq 的末位条目是块的**终结器**，它不属于
        b.instrs（to_v1 会单独追加 b.term）——这里只改写它的操作数，
        绝不能 append 进 b.instrs（否则终结器重复：STOP 双写、BR 双写
        会产生完全错乱的控制流，实测 main 尾部多出一条 STOP）。
        """
        f2 = self.f2
        removed = 0
        mov_op = MOV_V if self.domain == 'v' else MOV_F
        dead = self._find_dead_defs()
        for b in f2.blocks:
            if b.id not in self.reach:
                # 不可达块：没有 block_seq 记录，直接遍历（含终结器）。
                # 参数拷贝占位 dst=-1 必须一并落到私有空间（防御：
                # 正常情况下不可达前驱已不插拷贝）。
                for i in list(b.instrs) + ([b.term] if b.term is not None else []):
                    pcp = self.param_copy.get(id(i))
                    if pcp is not None:
                        _pv, s = pcp
                        i.dst = base + s
                        i.a = base + s
                        continue
                    mapping = {s: base + s for s in self._use_slots(i)
                               if self._is_domain_slot(s)}
                    apply_use_fields(i, collect_use_fields(i, self.domain),
                                     mapping)
                    ds = self._def_slot(i)
                    if ds is not None:
                        self._rewrite_def(i, base + ds)
                continue
            seq = self.block_seq.get(b.id, [])
            n_body = len(b.instrs)
            new_instrs = []
            synced_dst = set()      # id(_src_op)：dst 已同步
            src_orig_dst = {}       # id(_src_op) -> 原始 dst 槽号（首见时捕获）
            for k, entry in enumerate(seq):
                (i, use_vals, def_val) = entry
                in_body = k < n_body
                if def_val is not None and def_val.vid in dead:
                    removed += 1
                    continue        # 传递死定义：整条删除
                if self.param_copy.get(id(i)) is not None:
                    pv, s = self.param_copy[id(i)]
                    src = use_vals.get(s)
                    if src is not None and src.color == pv.color:
                        removed += 1
                        continue        # 参数拷贝同色：删除
                    i.dst = pv.color
                    if src is not None:
                        i.a = src.color
                    if in_body:
                        new_instrs.append(i)
                    continue
                if i.op == mov_op and def_val is not None and \
                        len(use_vals) == 1 and id(i) not in self.protected:
                    src = next(iter(use_vals.values()))
                    if src is not None and src.kind != 'global' and \
                            src.color == def_val.color:
                        removed += 1
                        continue        # 同色 MOV：删除
                ds_orig = self._def_slot(i)     # 原始槽号（改写前捕获）
                if def_val is not None and def_val.color is not None:
                    self._rewrite_def(i, def_val.color)
                mapping = {}
                for s, v in use_vals.items():
                    if v.color is not None:     # 全局槽不重编号
                        mapping[s] = v.color
                apply_use_fields(i, collect_use_fields(i, self.domain), mapping)
                # _src_op 同步（v 域）：infer_f64 的组携带「回指原始通用指令」
                # 的 _src_op，cgen 兜底路径按其 a/b/dst 取槽。组身指令的槽已
                # 被本 pass 重命名，不同步就会让兜底路径读旧槽号 = 别人的
                # 颜色（unbox_call_fallback 实证：分支读 garbage → 死循环；
                # s24 未声明 → 编译失败）。到达值在 rename 期用与组头一致的
                # current() 快照（self.src_sync），跨块/MOV_F 头都精确。
                if self.domain == 'v':
                    sync = self.src_sync.get(id(i))
                    if sync is not None:
                        sop, va, vb = sync
                        if id(sop) not in src_orig_dst:
                            src_orig_dst[id(sop)] = sop.dst
                        if def_val is not None and def_val.color is not None:
                            sop.dst = def_val.color
                            synced_dst.add(id(sop))
                        if va.color is not None:
                            sop.a = va.color
                        if vb.color is not None:
                            sop.b = vb.color
                    # NEG 组的形态缺陷补偿：infer 的 NEG 展开只把 _src_op 挂在
                    # NEG_F 上，组的 v 定义在随后的 STORE_F64 上（无 _src_op）
                    # ——dst 无人同步 → cgen 发射旧槽号（t09_unary 实证：
                    # s6/s13 未声明）。STORE 处回看近邻未同步的 _src_op，
                    # 原始 dst 与本存储目标一致者补同步。
                    if i.op == STORE_F64 and def_val is not None and \
                            def_val.color is not None:
                        for j in range(len(new_instrs) - 1,
                                       max(-1, len(new_instrs) - 4), -1):
                            cand = getattr(new_instrs[j], '_src_op', None)
                            if cand is None or id(cand) in synced_dst:
                                continue
                            if src_orig_dst.get(id(cand)) == ds_orig:
                                cand.dst = def_val.color
                                synced_dst.add(id(cand))
                                break
                if in_body:
                    new_instrs.append(i)
            b.instrs = new_instrs
        return removed

    def _rewrite_def(self, i, color):
        if self.domain == 'f':
            i.dst = color
            return
        dv, _uv, _df, _uf = slot_ops(i)
        for s in dv:
            if s >= 0 and not (s & GLOBAL_BIT):
                i.dst = color
                return

    def _rewrite_use(self, i, slot, color):
        if self.domain == 'v':
            n = replace_vuse(i, slot, color)
        else:
            n = replace_fuse(i, slot, color)
        if n == 0:
            raise SsaError('N-03 %s 域: use_map 记录的读取未生效 (槽 %d)'
                           % (self.domain, slot))
