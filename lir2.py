# -*- coding: utf-8 -*-
"""LIR v2 —— 块级 IR + 控制流/循环分析（ N-03 第 1 段）。

为什么要有这个文件（教训 G-1）：
  LIR v1 是「线性指令流 + 绝对跳转下标」。任何需要**插入/复制/重定向**的变换
  （内联、guard 外提、循环版本化）在 v1 上都要「先生成后回填」，而绝对坐标在
  二次插入后必然错位（t04/t15 的 goto 环）。本文件把坐标改成**块号**：
    * Block 内指令顺序即局部坐标，块间跳转只写块号；
    * 插入块 = 往列表里 append；删除块 = 移除；跳转永不漂移；
    * 落回 v1（to_v1）在**单次布局**里一次性算出每块起始下标，无回填。

v2 与 v1 的关系：v2 不是新的指令集（指令仍是 v1 的 LirInstr，槽位语义不变），
只是**容器形态**升级。因此 v1 的序列化/单测继续有效，后端 A 无感。

本文件提供：
  * Block / Func2（块表 + 显式终结器 + preds/succs）
  * from_v1 / to_v1（双向机械转换）
  * RPO + 支配（Cooper-Harvey-Kennedy 迭代式）
  * 自然循环识别（回边 = 目标支配源）+ 循环体/出边/入边
  * split_edge / new_block / retarget（改写基元）
  * slot_ops（操作数完备性总表，任何 pass 都必须复用，避免 G-2 类漏项）

SSA（N-03 第 2 段）与线性扫描寄存器分配（第 3 段）尚未实现——本文件是它们的地基。
"""
from lir import (
    LirInstr,
    CONST_F64, CONST_VAL, LOAD_F64, STORE_F64, MOV_V, MOV_F,
    ADD_F, SUB_F, MUL_F, DIV_F, MOD_F, POW_F, CMP_F, NEG_F,
    ARITH_GEN, UNARY_GEN, BR_IF_NOT, BR_IF, BR_F, JMP, RET_V, RET_F,
    CALL_USER, CALL_BUILTIN, CALL_MODULE, CALL_DYNAMIC,
    INDEX_GET, INDEX_SET, MAKE_ARRAY, MAKE_DICT, REQUIRE, STOP, NOP,
    GUARD_NUM, GLOBAL_BIT, ARENA_PUSH, ARENA_RESET,
    THROW, TRY_PUSH, TRY_POP, ENV_NEW, MAKE_CLOSURE, THROWVAL, UPVAL_BIT,
    FIELD_GET, FIELD_SET, CALL_OMETHOD, GUARD_TYPE,
)

# 终结器：块内最后一条；块内不再有其它控制流指令
# THROW 也是终结器（longjmp，无顺序后继）
TERMINATORS = (JMP, BR_IF_NOT, BR_IF, BR_F, RET_V, RET_F, STOP, THROW)
BRANCHES = (JMP, BR_IF_NOT, BR_IF, BR_F)          # 有 succs 的终结器
ARITH_F = (ADD_F, SUB_F, MUL_F, DIV_F, MOD_F, POW_F)


class CfgError(Exception):
    pass


# ---------------------------------------------------------------- 操作数总表
def slot_ops(i):
    """返回 (def_v, use_v, def_f, use_f)。**操作数完备性唯一真相源**。

    新增指令/新增 pass 必须复用本表（G-2：漏一个操作数就是静默错位）。
    槽号一律原样返回（可含 GLOBAL_BIT，可为 -1 哨兵）；调用方自行过滤。
    """
    op = i.op
    if op == CONST_F64:
        return (), (), (i.dst,), ()
    if op == CONST_VAL:
        return (i.dst,), (), (), ()
    if op == LOAD_F64:
        return (), (i.a,), (i.dst,), ()
    if op == STORE_F64:
        return (i.dst,), (), (), (i.a,)
    if op == MOV_V:
        return (i.dst,), (i.a,), (), ()
    if op == MOV_F:
        return (), (), (i.dst,), (i.a,)
    if op in ARITH_F:
        return (), (), (i.dst,), (i.a, i.b)
    if op == CMP_F:
        return (i.dst,), (), (), (i.a, i.b)
    if op == NEG_F:
        return (), (), (i.dst,), (i.a,)
    if op == ARITH_GEN:
        return (i.dst,), (i.a, i.b), (), ()
    if op == UNARY_GEN:
        return (i.dst,), (i.a,), (), ()
    if op in (BR_IF_NOT, BR_IF):
        return (), (i.a,), (), ()
    if op == BR_F:
        return (), (), (), (i.a,)
    if op == JMP:
        return (), (), (), ()
    if op == RET_V:
        return (), (i.a,), (), ()
    if op == RET_F:
        return (), (), (), (i.a,)
    if op in (CALL_USER, CALL_BUILTIN, CALL_MODULE):
        return (i.dst,), tuple(i.args), (), ()
    if op == CALL_DYNAMIC:
        return (i.dst,), (i.a,) + tuple(i.args), (), ()
    # M4（0）：对象字段与方法分派 —— INDEX_GET/SET/CALL 族同款语义
    if op == FIELD_GET:
        return (i.dst,), (i.a,), (), ()
    if op == FIELD_SET:
        return (), (i.a, i.c), (), ()
    if op == CALL_OMETHOD:
        return (i.dst,), tuple(i.args), (), ()
    if op == INDEX_GET:
        return (i.dst,), (i.a, i.b), (), ()
    if op == INDEX_SET:
        return (), (i.a, i.b, i.c), (), ()
    if op in (MAKE_ARRAY, MAKE_DICT):
        return (i.dst,), tuple(i.args), (), ()
    if op == REQUIRE:
        return (i.dst,), (), (), ()
    if op in (GUARD_NUM, GUARD_TYPE):
        return (), tuple(i.args), (), ()
    if op == THROW:
        return (), (i.a,), (), ()
    if op == TRY_PUSH or op == TRY_POP:
        return (), (), (), ()
    if op == THROWVAL:
        return (i.dst,), (), (), ()
    if op == ENV_NEW:
        return (i.dst,), (), (), ()
    if op == MAKE_CLOSURE:
        return (i.dst,), ((i.a,) if i.a >= 0 else ()), (), ()
    if op in (STOP, NOP, ARENA_PUSH, ARENA_RESET):
        return (), (), (), ()
    raise CfgError('slot_ops: 未知 LIR op %d' % op)


def is_global(slot):
    return slot >= 0 and bool(slot & GLOBAL_BIT)


# 「读取 v 槽」的操作数字段表（与 slot_ops 同源，G-2 完备性的另一半）
# M4（0）：FIELD_GET 读 a；FIELD_SET 读 a/c（b 是未用哨兵 ——
# 不进改写面，与 slot_ops 的 (a, c) 报告逐位一致）；CALL_OMETHOD 读 args
_V_USE_DST = ()          # 没有指令读自己的 dst
_V_USE_A = (LOAD_F64, MOV_V, BR_IF_NOT, BR_IF, RET_V, UNARY_GEN, THROW,
            MAKE_CLOSURE, FIELD_GET)
_V_USE_AB = (ARITH_GEN, INDEX_GET)
_V_USE_ABC = (INDEX_SET,)
_V_USE_AC = (FIELD_SET,)
_V_USE_ARGS = (CALL_USER, CALL_BUILTIN, CALL_MODULE, MAKE_ARRAY, MAKE_DICT,
               CALL_OMETHOD)
_V_USE_A_ARGS = (CALL_DYNAMIC,)
_V_USE_ARGS_ONLY = (GUARD_NUM, GUARD_TYPE)


def replace_vuse(i, x, y):
    """把指令 i 中**读取** v 槽 x 的操作数全部替换为 y，返回替换个数。

    与 slot_ops 成对维护：任何一方漏项都会静默产生错误改写（G-2）。
    """
    n = 0
    if i.dst == x and i.op in _V_USE_DST:
        i.dst = y
        n += 1
    if i.op in _V_USE_A:
        if i.a == x:
            i.a = y
            n += 1
    elif i.op in _V_USE_AB:
        if i.a == x:
            i.a = y
            n += 1
        if i.b == x:
            i.b = y
            n += 1
    elif i.op in _V_USE_ABC:
        for attr in ('a', 'b', 'c'):
            if getattr(i, attr) == x:
                setattr(i, attr, y)
                n += 1
    elif i.op in _V_USE_AC:
        for attr in ('a', 'c'):
            if getattr(i, attr) == x:
                setattr(i, attr, y)
                n += 1
    elif i.op in _V_USE_A_ARGS:
        if i.a == x:
            i.a = y
            n += 1
        if x in i.args:
            i.args = [y if s == x else s for s in i.args]
            n += i.args.count(y)
    elif i.op in _V_USE_ARGS or i.op in _V_USE_ARGS_ONLY:
        if x in i.args:
            i.args = [y if s == x else s for s in i.args]
            n += i.args.count(y)
    return n


# ---------------------------------------------------------------- 块与函数
class Block(object):
    __slots__ = ('id', 'instrs', 'term', 'succs', 'preds', 'ft')

    def __init__(self, bid):
        self.id = bid
        self.instrs = []        # 非终结器指令
        self.term = None        # 终结器（JMP/BR_*/RET_V/RET_F/STOP）；必不为空
        self.succs = []         # 后继块 id
        self.preds = []
        # 条件分支的「落下」后继。C 里 BR 就是 `if (..) goto T;`，接下来执行的就是
        # 布局中紧随其后的块——所以 ft 块必须紧跟在本块之后（to_v1 会断言）。
        self.ft = None

    @property
    def all_instrs(self):
        return self.instrs + ([self.term] if self.term is not None else [])

    def __repr__(self):
        return 'B%d(succs=%r)' % (self.id, self.succs)


class Loop(object):
    __slots__ = ('header', 'body', 'back_edges', 'parent')

    def __init__(self, header, body, back_edges, parent=None):
        self.header = header
        self.body = body                  # set of block id（含 header）
        self.back_edges = back_edges      # [(tail, header)]
        self.parent = parent

    @property
    def size(self):
        return len(self.body)

    def __repr__(self):
        return 'Loop(header=B%d, body=%d blocks)' % (self.header, len(self.body))


class Func2(object):
    """块级 LIR 函数。self.blocks 的**列表顺序即最终布局顺序**（to_v1 按此拼接）。"""

    def __init__(self, src=None):
        self.blocks = []
        self.entry = 0
        self.name = ''
        self.kind = 'main'
        self.n_slots_v = 0
        self.n_slots_f = 0
        self.n_bind_slots = 0
        self.req_names = []
        self.name_table = []
        self.slot_forms = {}
        self.entry_guards = []
        self.const_pool = None
        if src is not None:
            self._adopt(src)

    def _adopt(self, f):
        self.name = f.name
        self.kind = f.kind
        self.n_slots_v = f.n_slots_v
        self.n_slots_f = f.n_slots_f
        self.n_bind_slots = f.n_bind_slots
        self.req_names = list(getattr(f, 'req_names', []) or [])
        self.name_table = list(getattr(f, 'name_table', []) or [])
        self.slot_forms = dict(getattr(f, 'slot_forms', {}) or {})
        self.entry_guards = list(getattr(f, 'entry_guards', []) or [])

    # ---- 构造 ----
    def new_block(self):
        b = Block(len(self.blocks))
        self.blocks.append(b)
        return b

    def link(self, blk, succ):
        """登记 blk → succ（去重，保持顺序）。"""
        if succ.id not in blk.succs:
            blk.succs.append(succ.id)
        if blk.id not in succ.preds:
            succ.preds.append(blk.id)

    def unlink(self, blk, succ_id):
        if succ_id in blk.succs:
            blk.succs.remove(succ_id)
        if succ_id < len(self.blocks) and blk.id in self.blocks[succ_id].preds:
            self.blocks[succ_id].preds.remove(blk.id)

    def retarget(self, blk, old_succ_id, new_succ_id):
        """把 blk 指向 old 的那一条边改指 new（其余边不动）。"""
        if old_succ_id not in blk.succs:
            raise CfgError('retarget: B%d 无后继 B%d' % (blk.id, old_succ_id))
        if old_succ_id == new_succ_id:
            return
        self.unlink(blk, old_succ_id)
        # term 里保存的 target 是块号（v2 语义），逐个 succ 槽位重写
        if blk.term is not None and blk.term.op in BRANCHES:
            blk.term.target = new_succ_id
        self.link(blk, self.blocks[new_succ_id])

    def split_edge(self, blk, succ_id):
        """在 blk → succ 这条边上插入一个新块，返回新块。

        用于「出边回写」（循环退出时把去装箱值装箱回去）等需要在边上插码的场景。
        """
        mid = self.new_block()
        self.retarget(blk, succ_id, mid.id)
        mid.term = LirInstr(JMP, target=succ_id)
        self.link(mid, self.blocks[succ_id])
        return mid

    # ---- 遍历 ----
    def iter_instrs(self):
        for b in self.blocks:
            for i in b.instrs:
                yield b, i
            if b.term is not None:
                yield b, b.term

    def instr_count(self):
        n = 0
        for b in self.blocks:
            n += len(b.instrs) + (1 if b.term is not None else 0)
        return n

    def rebuild_cfg(self):
        """按当前指令重建 succs/preds（任何改写基元之后统一调用）。

        四类边都必须登记，preds 才完整（支配/循环识别依赖完整性）：
          1. 块内 GUARD_NUM 的失败边（失败才跳，成功则顺序落下）；
          2. 终结器是 BRANCHES 时的跳转边；
          3. 条件分支的落下边 b.ft；
          4. F2：块内 TRY_PUSH 的 catch 边（异常路径，成功则顺序落下）。
        """
        for b in self.blocks:
            b.succs = []
            b.preds = []
        for b in self.blocks:
            for i in b.instrs:
                if i.op in (GUARD_NUM, GUARD_TYPE) or i.op == TRY_PUSH:
                    self.link(b, self.blocks[i.target])
            if b.term is not None and b.term.op in BRANCHES:
                self.link(b, self.blocks[b.term.target])
            if b.ft is not None:
                self.link(b, self.blocks[b.ft])

    def reachable(self):
        seen = set()
        stack = [self.entry]
        while stack:
            bid = stack.pop()
            if bid in seen:
                continue
            seen.add(bid)
            for s in self.blocks[bid].succs:
                if s not in seen:
                    stack.append(s)
        return seen

    def rpo(self):
        """逆后序（迭代式 DFS，避免深函数爆栈）。"""
        post = []
        seen = set()
        stack = [(self.entry, 0)]
        while stack:
            bid, ci = stack[-1]
            b = self.blocks[bid]
            if ci == 0 and bid not in seen:
                seen.add(bid)
            if ci < len(b.succs):
                stack[-1] = (bid, ci + 1)
                ns = b.succs[ci]
                if ns not in seen:
                    stack.append((ns, 0))
            else:
                post.append(bid)
                stack.pop()
        post.reverse()
        # 后序的逆 = RPO（只对可达块）；不可达块按 id 追加在后（不影响分析正确性）
        for b in self.blocks:
            if b.id not in seen:
                post.append(b.id)
        return post

    # ---- 支配 ----
    def compute_idom(self):
        """Cooper-Harvey-Kennedy 迭代式直接支配者。返回 idom: dict(bid -> bid)。

        idom[entry] = entry。不可达块不在表中。
        """
        order = self.rpo()
        idx = {bid: k for k, bid in enumerate(order)}
        idom = {self.entry: self.entry}

        def intersect(a, b):
            while a != b:
                while idx[a] > idx[b]:
                    a = idom[a]
                while idx[b] > idx[a]:
                    b = idom[b]
            return a

        changed = True
        while changed:
            changed = False
            for bid in order:
                if bid == self.entry:
                    continue
                new = None
                for p in self.blocks[bid].preds:
                    if p not in idom:
                        continue
                    if p == bid:
                        continue
                    new = p if new is None else intersect(p, new)
                if new is not None and idom.get(bid) != new:
                    idom[bid] = new
                    changed = True
        return idom

    def dominates(self, idom, a, b):
        """a 支配 b（含 a == b）。"""
        if a == b:
            return True
        cur = b
        while cur in idom and cur != idom[cur]:
            cur = idom[cur]
            if cur == a:
                return True
        return cur == a

    # ---- 活跃性 ----
    def compute_liveness(self):
        """反向数据流求 v 槽活跃集。返回 (live_in, live_out)：bid -> set(v 槽)。

        live_in[b] 的语义正是循环去装箱需要的判据：
        「从 b 的入口出发，存在一条路径在写入之前读取该槽」。
        因此 s ∉ live_in[header] ⟺ 循环体内每次读取前必有写入 ⟹ 槽进入循环时的
        旧值无人关心 ⟹ **不必进形态守卫**（否则守卫会因为临时槽初始为 NONE
        而永远失败，快循环形同虚设——order_engine 实测）。
        """
        use, dfn = {}, {}
        for b in self.blocks:
            u, d = set(), set()
            for i in b.all_instrs:
                dv, uv, _df, _uf = slot_ops(i)
                for s in dv:
                    if s >= 0:
                        d.add(s)
                for s in uv:
                    if s >= 0 and s not in d:
                        u.add(s)
            use[b.id], dfn[b.id] = u, d
        live_in = {b.id: set() for b in self.blocks}
        live_out = {b.id: set() for b in self.blocks}
        order = self.rpo()
        changed = True
        while changed:
            changed = False
            for bid in reversed(order):
                lo = set()
                for s in self.blocks[bid].succs:
                    lo |= live_in.get(s, set())
                li = use[bid] | (lo - dfn[bid])
                if li != live_in[bid] or lo != live_out[bid]:
                    live_in[bid], live_out[bid] = li, lo
                    changed = True
        return live_in, live_out

    # ---- 循环 ----
    def back_edges(self, idom):
        return [(b.id, s) for b in self.blocks for s in b.succs
                if self.dominates(idom, s, b.id)]

    def natural_loops(self, idom):
        """每个回边对应一个自然循环；嵌套关系由包含判定给出。"""
        loops = []
        for tail, header in self.back_edges(idom):
            body = {header}
            stack = [tail]
            while stack:
                n = stack.pop()
                if n in body:
                    continue
                body.add(n)
                for p in self.blocks[n].preds:
                    if p not in body:
                        stack.append(p)
            loops.append(Loop(header, body, [(tail, header)]))
        # 合并共享同一 header 的回边
        merged = {}
        for lp in loops:
            if lp.header in merged:
                m = merged[lp.header]
                m.body |= lp.body
                m.back_edges += lp.back_edges
            else:
                merged[lp.header] = lp
        out = list(merged.values())
        for lp in out:
            lp.parent = None
            for other in out:
                if other is lp:
                    continue
                if lp.body < other.body:
                    lp.parent = other
                    break
        return out

    def loop_exits(self, lp):
        """出边列表 [(blk_id, succ_id)]：blk ∈ body，succ ∉ body。"""
        out = []
        for bid in sorted(lp.body):
            for s in self.blocks[bid].succs:
                if s not in lp.body:
                    out.append((bid, s))
        return out

    def loop_entries(self, lp):
        """入边列表 [(pred_id, header)]：pred ∉ body。"""
        return [(p, lp.header) for p in self.blocks[lp.header].preds
                if p not in lp.body]

    def dump(self):
        lines = ['=== v2 %s (%s) blocks=%d slotsV=%d slotsF=%d ==='
                 % (self.name or '<main>', self.kind, len(self.blocks),
                    self.n_slots_v, self.n_slots_f)]
        for b in self.blocks:
            lines.append('B%d: preds=%r succs=%r' % (b.id, b.preds, b.succs))
            for i in b.instrs:
                lines.append('     %s' % i)
            if b.term is not None:
                lines.append('   T %s' % b.term)
        return '\n'.join(lines)


# ---------------------------------------------------------------- v1 ⇄ v2
def from_v1(f):
    """LirFunction（线性流）→ Func2（块表）。

    划分规则：leader = {0} ∪ {跳转 target} ∪ {终结器之后}。

    **所有边都显式化**（这是布局自由的前提，也是 G-1 的根治）：
      * 顺序落下 → 补一条显式 JMP；
      * 条件分支（BR_IF_NOT/BR_IF/BR_F）有两条边，但指令只有一个 target 字段，
        因此在它后面**紧邻**插入一个只含 `JMP 落下目标` 的边块。这样 CFG 完整，
        且后续任何插入/重排都不会依赖「列表中相邻 = 落下」这条脆弱约定。
    """
    f2 = Func2(f)
    f2.const_pool = getattr(f, '_const_pool', None)
    n = len(f.instrs)
    if n == 0:
        b = f2.new_block()
        b.term = LirInstr(STOP)
        return f2

    leaders = set([0])
    for pc, i in enumerate(f.instrs):
        if i.op in BRANCHES or i.op in (GUARD_NUM, GUARD_TYPE, TRY_PUSH):
            leaders.add(i.target)
            if i.op in BRANCHES and pc + 1 < n:
                leaders.add(pc + 1)
        elif i.op in (RET_V, RET_F, STOP):
            if pc + 1 < n:
                leaders.add(pc + 1)
    starts = sorted(x for x in leaders if 0 <= x < n)
    start_of = {s: k for k, s in enumerate(starts)}

    # ---- 原型块： (body_instrs, term_instr_or_None) ----
    # 指令一律**克隆**：v2 拥有自己的指令对象。这样 from_v1 里把「指令下标」改写成
    # 「块号」时不会污染原 LirFunction——变换失败被整体丢弃时原函数仍是干净的。
    protos = []
    for k, s in enumerate(starts):
        end = starts[k + 1] if k + 1 < len(starts) else n
        body = [clone_instr(i) for i in f.instrs[s:end]]
        term = None
        if body and body[-1].op in TERMINATORS:
            term = body[-1]
            body = body[:-1]
        protos.append((body, term))

    # ---- 落料：proto_i 与其（可选的）落下边块交替进入列表顺序 ----
    ids = []
    for _body, term in protos:
        b = f2.new_block()
        ft = f2.new_block() if (term is not None and term.op in (BR_IF_NOT, BR_IF, BR_F)) else None
        if ft is not None:
            b.ft = ft.id
        ids.append((b.id, ft.id if ft is not None else None))
    for k, (body, term) in enumerate(protos):
        b = f2.blocks[ids[k][0]]
        b.instrs = body
        ft_id = ids[k][1]
        nxt_id = ids[k + 1][0] if k + 1 < len(protos) else None
        if term is None:
            b.term = LirInstr(JMP, target=nxt_id) if nxt_id is not None else LirInstr(STOP)
        elif term.op in BRANCHES:
            b.term = LirInstr(term.op, a=term.a,
                              target=ids[start_of[term.target]][0])
            if ft_id is not None:
                f2.blocks[ft_id].term = (
                    LirInstr(JMP, target=nxt_id) if nxt_id is not None else LirInstr(STOP))
        else:   # RET_V / RET_F / STOP
            b.term = term
            if ft_id is not None:       # 理论不可达（条件分支才有 ft 块）
                f2.blocks[ft_id].term = LirInstr(STOP)
    # 块内 GUARD_NUM / TRY_PUSH：target 从「指令下标」改写为「块号」
    for k, (_body, _term) in enumerate(protos):
        for i in f2.blocks[ids[k][0]].instrs:
            if i.op in (GUARD_NUM, GUARD_TYPE) or i.op == TRY_PUSH:
                i.target = ids[start_of[i.target]][0]
    f2.rebuild_cfg()
    return f2


def _layout(f2):
    """块布局顺序：入口第一（C 从 pc0 顺序落入），且每个条件分支块的落下块紧随其后。

    ft 块只可能由落下到达（从不作为跳转目标：from_v1 只为 leader 建块，合成块不是
    leader），因此「先放拥有者、立刻放它的 ft」总能满足。BFS 保证入口在首位。
    """
    order = []
    seen = set()

    def place(bid):
        if bid in seen:
            return
        seen.add(bid)
        order.append(bid)
        ft = f2.blocks[bid].ft
        if ft is not None and ft not in seen:
            seen.add(ft)
            order.append(ft)

    def sweep(roots):
        q = list(roots)
        while q:
            bid = q.pop(0)
            place(bid)
            for s in f2.blocks[bid].succs:
                if s not in seen:
                    q.append(s)

    sweep([f2.entry])
    for b in f2.blocks:
        if b.id not in seen:
            sweep([b.id])
    for bid in order:
        ft = f2.blocks[bid].ft
        if ft is not None:
            k = order.index(bid)
            if k + 1 >= len(order) or order[k + 1] != ft:
                raise CfgError('布局: B%d 的落下块 B%d 未紧随其后' % (bid, ft))
    return order


def to_v1(f2, f):
    """Func2 → LirFunction（就地写回 f.instrs）。

    单次布局：先按块顺序算出每块起始下标，再一次性重写跳转 target。
    不存在「先写后回填」，因此没有 G-1 的坐标漂移面。

    唯一的布局约束：条件分支块 b 的落下块 b.ft 必须是列表中的**紧邻下一块**
    （C 里 `if (..) goto T;` 之后就是下一段代码）。这条不变式由构造顺序保证
    （ft 块总在其拥有者之后立即 new 出来，且所有 pass 只 append 不改序），
    这里显式断言——违反它会静默产生错误的落下边。
    """
    order = _layout(f2)
    out = []
    start = {}
    for bid in order:
        b = f2.blocks[bid]
        start[b.id] = len(out)
        for i in b.instrs:
            out.append(i)
        if b.term is not None:
            out.append(b.term)
    for bid in order:
        b = f2.blocks[bid]
        # GUARD_NUM / TRY_PUSH 是**块内**跳转（不在 term 上）——漏了就会把
        # 块号当成指令下标（跳进别的块中间），必须和 term 一起重映射。
        for i in b.instrs:
            if i.op in (GUARD_NUM, GUARD_TYPE) or i.op == TRY_PUSH:
                i.target = start[i.target]
        if b.term is not None and b.term.op in BRANCHES:
            b.term.target = start[b.term.target]
    f.instrs = out
    f.n_slots_v = f2.n_slots_v
    f.n_slots_f = f2.n_slots_f
    # 名字表必须回写：内联会把被内联体的名字登记进调用方的表（T-43 v2），
    # 丢了它就会导致 CALL_BUILTIN 的下标解析成空名字。
    f.name_table = list(f2.name_table)
    return f


# ---------------------------------------------------------------- 重命名
def rename_instr(i, vmap, fmap, nmap=None):
    """按映射重命名一条指令的全部操作数（就地）。

    vmap/fmap：v 槽 / f 槽 → 新号（缺失即原样，全局槽与哨兵应原样透传）。
    nmap：名字表下标 → 新下标（内联跨块合并名字表时用）。
    与 slot_ops / replace_vuse 同一完备性约定（G-2）：新增指令必须同步这里。
    """
    rv = lambda s: vmap.get(s, s)                                    # noqa: E731
    rf = lambda s: fmap.get(s, s)                                    # noqa: E731
    op = i.op
    if op == CONST_F64:
        i.dst = rf(i.dst)
    elif op == CONST_VAL:
        i.dst = rv(i.dst)
    elif op == LOAD_F64:
        i.dst = rf(i.dst)
        i.a = rv(i.a)
    elif op == STORE_F64:
        i.dst = rv(i.dst)
        i.a = rf(i.a)
    elif op == MOV_V:
        i.dst = rv(i.dst)
        i.a = rv(i.a)
    elif op == MOV_F:
        i.dst = rf(i.dst)
        i.a = rf(i.a)
    elif ADD_F <= op <= POW_F:
        i.dst, i.a, i.b = rf(i.dst), rf(i.a), rf(i.b)
    elif op == CMP_F:
        i.dst = rv(i.dst)
        i.a, i.b = rf(i.a), rf(i.b)
    elif op == NEG_F:
        i.dst, i.a = rf(i.dst), rf(i.a)
    elif op == ARITH_GEN:
        i.dst, i.a, i.b = rv(i.dst), rv(i.a), rv(i.b)
    elif op == UNARY_GEN:
        i.dst, i.a = rv(i.dst), rv(i.a)
    elif op in (BR_IF_NOT, BR_IF, RET_V):
        i.a = rv(i.a)
    elif op == BR_F:
        i.a = rf(i.a)
    elif op == RET_F:
        i.a = rf(i.a)
    elif op in (CALL_USER, CALL_BUILTIN, CALL_MODULE):
        i.dst = rv(i.dst)
        i.args = [rv(s) for s in i.args]
        idx = i.func_idx if op == CALL_USER else i.name_idx
        new = nmap.get(idx, idx) if nmap else idx
        if op == CALL_USER:
            i.func_idx = new
        else:
            i.name_idx = new
    elif op == CALL_DYNAMIC:
        i.dst = rv(i.dst)
        i.a = rv(i.a)
        i.args = [rv(s) for s in i.args]
    elif op == INDEX_GET:
        i.dst, i.a, i.b = rv(i.dst), rv(i.a), rv(i.b)
    elif op == INDEX_SET:
        i.a, i.b, i.c = rv(i.a), rv(i.b), rv(i.c)
    # M4（0）：对象字段/方法分派（内联构造器体需要走这里）
    elif op == FIELD_GET:
        i.dst, i.a = rv(i.dst), rv(i.a)
    elif op == FIELD_SET:
        i.a, i.c = rv(i.a), rv(i.c)
    elif op == CALL_OMETHOD:
        i.dst = rv(i.dst)
        i.args = [rv(s) for s in i.args]
        if nmap:
            i.name_idx = nmap.get(i.name_idx, i.name_idx)
    elif op in (MAKE_ARRAY, MAKE_DICT):
        i.dst = rv(i.dst)
        i.args = [rv(s) for s in i.args]
    elif op == REQUIRE:
        i.dst = rv(i.dst)
    elif op in (GUARD_NUM, GUARD_TYPE):
        i.args = [rv(s) for s in i.args]
    elif op in (JMP, STOP, NOP, ARENA_PUSH, ARENA_RESET):
        pass
    else:
        raise CfgError('rename_instr: 未知 LIR op %d' % op)
    return i


# ---------------------------------------------------------------- 克隆
def clone_instr(i):
    """深拷贝一条 LirInstr（含 args 列表与 _src_op 溯源引用）。

    _src_op 是**共享引用**（指向原始通用指令），克隆后必须能继续溯源；
    调用方若需要「解除溯源」自行置 None（去装箱快循环就是这么做的）。
    """
    c = LirInstr(i.op)
    c.dst, c.a, c.b, c.c = i.dst, i.a, i.b, i.c
    c.imm, c.pred = i.imm, i.pred
    c.target = i.target
    c.func_idx, c.name_idx, c.str_idx = i.func_idx, i.name_idx, i.str_idx
    c.argc = i.argc
    c.args = list(i.args)
    c._src_op = getattr(i, '_src_op', None)
    c._no_inline = getattr(i, '_no_inline', False)
    c._spec_type = getattr(i, '_spec_type', -1)
    return c
