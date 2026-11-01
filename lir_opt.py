# -*- coding: utf-8 -*-
"""LIR 优化 pass ——  7.4 机器码 P4/P5。

两个与特性无关的 pass（§6.3/§6.5）：
  1. inline_calls（T-43 消间接调用）：把小用户函数体复制进调用点。
     【当前默认禁用】——多遍展开的跳转坐标重定位存在未决 bug（详见
     inline_calls_with_tables 文档注释）。QOT_ENABLE_INLINE=1 可显式开启
     供开发调试；基础设施与单测保留。
  2. infer_f64（T-44 数值特化）：全程序槽形态分析，把数值证明的算术改写为
     ADD_F/CMP_F/NEG_F（消费点 LOAD_F64 guard，定义点 STORE_F64），链式窥孔
     `STORE_F64 v,f; LOAD_F64 f',v → MOV_F` 消中间装箱。
     改写后的每条 f64 指令自带运行期 guard，失败即回退该指令的通用语义
     （§6.5：UNK → 一次 GUARD_TAG → 收敛；guard 失败永不改变程序行为）。

infer_f64 是纯函数式的 LIR→LIR 变换，差分测试（native vs VM 输出一致）是长期护栏。
"""
import os

import qot_builtins as _qb
from lir2 import (rename_instr, clone_instr,  # noqa: F401  （T-43 v2 / N-02 用）
                  slot_ops)
from lir import (
    LirProgram, LirInstr, LirFunction,
    CONST_F64, CONST_VAL, LOAD_F64, STORE_F64, MOV_V, MOV_F,
    ADD_F, SUB_F, MUL_F, DIV_F, MOD_F, POW_F, CMP_F, NEG_F,
    ARITH_GEN, UNARY_GEN, BR_IF_NOT, BR_IF, BR_F, JMP, RET_V, RET_F,
    CALL_USER, CALL_BUILTIN, CALL_MODULE, CALL_DYNAMIC,
    INDEX_GET, INDEX_SET, MAKE_ARRAY, MAKE_DICT, REQUIRE, STOP, NOP,
    GUARD_NUM, GLOBAL_BIT, ARENA_PUSH, ARENA_RESET,
    THROW, TRY_PUSH, TRY_POP, ENV_NEW, MAKE_CLOSURE, THROWVAL, UPVAL_BIT,
    FIELD_GET, FIELD_SET, CALL_OMETHOD,
)

# F1/F2 闭包与异常的 LIR 特征。含这些特征的函数（闭包函数/含 try 的函数）
# 一律不进窥孔/SSA/内联/arena 等槽级改写 pass：upval 槽与 env 槽是跨激活
# 共享的存储（值语义槽模型对它们不成立），逐位保守 = 逐位安全。
_SPECIAL_OPS = (THROW, TRY_PUSH, TRY_POP, ENV_NEW, MAKE_CLOSURE, THROWVAL)


def _is_special_slot(s):
    return s is not None and s >= 0 and bool(s & UPVAL_BIT)


def _has_special_ops(f):
    for i in f.instrs:
        if i.op in _SPECIAL_OPS:
            return True
        for s in slot_ops(i)[0] + slot_ops(i)[1]:
            if _is_special_slot(s):
                return True
    return False

MAX_INLINE_INSTRS = 60
MAX_INLINE_DEPTH = 2
MAX_FUNC_INSTRS = 20000  # 单函数内联膨胀硬上限（防御：异常繁殖立即暴露而非耗尽内存）

# N-02 循环去装箱的规模护栏：被克隆的循环体指令数上限（防御代码爆炸与编译时间）
MAX_UNBOX_INSTRS = 400
# 单个循环允许去装箱的槽数上限（镜像 f 槽 + 出边回写的开销线性于此）
MAX_UNBOX_SLOTS = 24


class InlineError(Exception):
    pass

# 返回值恒为数字的内置函数白名单。
# 【M1】名单来自 builtins.toml 的 numeric 字段（不再在这里另写一份 —— 那是
# 第五处隐式内置表，正是 G-5 的温床）。
NUMERIC_BUILTINS = _qb.numeric_builtins()

# 不产出 arena 块的内置（循环 arena 回收染色的豁免名单），同样来自清单的
# alloc_free 字段。判据写在清单里：这些内置要么恒返回数字、要么不写 dst，
# 不可能把「本轮 bump 区」的块带出循环体。
ALLOC_FREE_BUILTINS = _qb.alloc_free_builtins()

# 【P0-POOL-1】arena 安全分类（同样清单派生，R13：不在这里另写名单）：
#   CONTAINER_MUTATORS —— 把 args[1:] 存进 args[0] 容器的内置；循环 arena
#   回收对它们做内容逃逸检查（否决 3，见 arena_loops 契约第 4 条）。
#   CALLBACK_BUILTINS —— native 侧执行 QOT 闭包回调的内置；按 CALL_USER
#   同类否决（回调体可把 arena 块存进任何跨轮容器）。
CONTAINER_MUTATORS = _qb.container_mutators()
CALLBACK_BUILTINS = _qb.callback_builtins()


class _Ctx(object):
    def __init__(self, prog):
        self.prog = prog
        # 用户函数名 → LirFunction（funcNames[0] 即函数名）
        self.by_name = {}
        for f in prog.funcs.values():
            if f.name:
                self.by_name[f.name] = f
        self.callers = {}   # name -> set(caller name)
        self.inline_count = 0


# ---------------------------------------------------------------- 内联（T-43）
def _callees_of(f):
    """函数直接调用的用户函数名集合（经 funcNames 下标解析）。"""
    out = set()
    for i in f.instrs:
        if i.op == CALL_USER and 0 <= i.func_idx < len(f.name_table):
            out.add(f.name_table[i.func_idx])
    return out


def inline_calls(prog):
    """把满足条件的小函数内联进调用点。返回内联次数。"""
    ctx = _Ctx(prog)
    # LirFunction 需要名字表做解析；降级器没存——挂到函数对象上（name_table = 块 funcNames）
    for f in prog.all_funcs():
        if not hasattr(f, 'name_table'):
            f.name_table = []
    # 主块的名字表 = 编译器 intern 的全部调用名；从 CALL 指令恢复不可行，
    # 由 lower 阶段提供（lir_lower 已把 func_idx 绑定到块 funcNames）。
    # 这里要求调用方先 set_name_tables(prog, main_names, {name: names})。
    raise NotImplementedError('请使用 inline_calls_with_tables')


def set_name_tables(prog, tables):
    """tables: {LirFunction: name_list}，由降级器在 lower 时记录。"""
    for f, names in tables.items():
        f.name_table = list(names)


def inline_calls_with_tables(prog, tables):
    """内联入口。默认禁用（环境变量 QOT_ENABLE_INLINE=1 显式开启）。

    禁用原因（§10 已知限制，如实记录）：多遍展开对「先前已写好的绝对跳转坐标」
    仍有未决的失效路径（t04/t15 复现）。在 LIR 引入 SSA 化的坐标体系之前，
    正确性优先于这一层优化收益。消 dispatch/消栈搬运/F64 特化不受影响。
    开关保留：infrastructure + 单测（test_lir/test_lir_opt）持续守护重建路径。
    """
    if os.environ.get('QOT_ENABLE_INLINE') != '1':
        return 0
    set_name_tables(prog, tables)
    ctx = _Ctx(prog)
    total = 0
    for _round in range(MAX_INLINE_DEPTH):
        n = _inline_one_round(prog, ctx)
        total += n
        if n == 0:
            break
    return total


def _resolve_callee(ctx, f, func_idx):
    if not (0 <= func_idx < len(getattr(f, 'name_table', []))):
        return None
    name = f.name_table[func_idx]
    return ctx.by_name.get(name)


def _inlineable(ctx, callee, chain=()):
    """callee 是否可内联。chain = 调用链上的名字（防环：链上重复即环）。

    当前内联的跳转重定位只在「无内部跳转的直线体」上经过充分验证；
    带循环/分支（JMP/BR）的函数保留为 cgen 直接函数调用——其内部回边
    用的是 lower 时的原始坐标，天然正确。等 LIR 引入 SSA 化 pass 后再放开
    （§10 修订记录跟踪）。"""
    if callee is None or callee.kind != 'user':
        return False
    if len(callee.instrs) > MAX_INLINE_INSTRS:
        return False
    if callee.name in chain:
        return False  # 递归/环不内联
    for i in callee.instrs:
        if i.op in (STOP, REQUIRE, CALL_MODULE, CALL_DYNAMIC, RET_F,
                    JMP, BR_IF_NOT, BR_IF, BR_F):
            return False
        # M4：对象字段/方法分派不在 v1 体重映射的 elif 链里（v1 展开器未
        # 覆盖）—— 一律不内联（防御；主线走 v2，其 rename_instr 已覆盖）
        if i.op in (FIELD_GET, FIELD_SET, CALL_OMETHOD):
            return False
        if i.op == CALL_USER:
            sub = _resolve_callee(ctx, callee, i.func_idx)
            if sub is None:
                return False
    return True


def _inline_one_round(prog, ctx):
    """一轮内联：深度优先递归展开每个可内联的 CALL_USER（一轮到收敛）。

    跳转坐标系（工业做法：每层 body 自洽 + 拼接一次平移）：
      * 每层 body 在构建期是 append-only，层内跳转最终回填为「body 内绝对坐标」；
      * 子 body 作为整体 extend 进父 body，子 body 的跳转统一 += base_sub
        （base_sub 为插入前父 body 长度；此后父 body 尾部追加不影响子 body 内坐标）；
      * body 完成后整体替换 caller 的 CALL——caller 全函数坐标即最终坐标。
    多轮插入漂移问题（t11 goto 环根因）在该结构下不存在。
    """
    n = 0
    for f in prog.all_funcs():
        new_instrs = []
        for i in list(f.instrs):
            if i.op != CALL_USER or getattr(i, '_no_inline', False):
                new_instrs.append(i)
                continue
            callee = _resolve_callee(ctx, f, i.func_idx)
            if not _inlineable(ctx, callee, (f.name,)):
                # 环/递归调用点：永久标记，后续遍绝不展开——
                # 否则第二遍的插入会使第一遍写好的绝对跳转坐标失效（t04 goto 环根因）。
                i._no_inline = True
                new_instrs.append(i)
                continue
            body = _inline_call(f, callee, i, ctx, 0, (f.name,))
            base = len(new_instrs)
            for c in body:
                if c.op in (JMP, BR_IF_NOT, BR_IF, BR_F):
                    c.target += base  # body 内绝对 → 全函数绝对
            new_instrs.extend(body)
            n += 1
            ctx.inline_count += 1
        f.instrs = new_instrs
        if len(f.instrs) > MAX_FUNC_INSTRS:
            raise InlineError(
                '内联膨胀超限: %s 达到 %d 条（疑似调用繁殖 bug，拒绝继续）'
                % (f.name or '<main>', len(f.instrs)))
    return n


def _inline_call(caller, callee, call, ctx, depth=0, chain=()):
    """把 callee 体（深度优先递归展开）复制进调用点。返回 body。

    跳转坐标系：返回的 body 里所有跳转 target 都是「body 内绝对坐标」。
      * 普通跳转：pos_map[orig]（RET 被替换为两条指令的偏移已计入）；
      * RET 的 merge JMP = len(body)（body 末尾 = 调用点下一条指令）；
      * 子 body 整体 extend 进本层，其内部跳转统一 += base_sub（插入前长度）。
    append-only 构建保证以上坐标一旦写下不再漂移。
    """
    if depth > MAX_INLINE_DEPTH:
        raise InlineError('内联深度超限: %s' % (callee.name,))
    caller_nt = getattr(caller, 'name_table', None)
    callee_nt = getattr(callee, 'name_table', None)
    if caller_nt is None:
        caller_nt = []
        caller.name_table = caller_nt
    if callee_nt is None:
        callee_nt = []

    offset = caller.n_slots_v
    caller.n_slots_v += callee.n_slots_v

    def remap(slot):
        if slot & GLOBAL_BIT:
            return slot
        return slot + offset

    def remap_name(idx_src):
        """callee 名字表下标 → caller 名字表下标（按名字；缺失则登记到 caller 表尾）。"""
        if not (0 <= idx_src < len(callee_nt)):
            return idx_src  # 越界原样保留（verify/解析期兜底）
        name = callee_nt[idx_src]
        if name in caller_nt:
            return caller_nt.index(name)
        caller_nt.append(name)
        return len(caller_nt) - 1

    def translate_call(ins):
        """构造 remap 后的 CALL 指令（供递归/直接复制）。"""
        c = LirInstr(ins.op)
        c.dst = remap(ins.dst)
        c.argc = ins.argc
        c.args = [remap(s) for s in ins.args]
        if ins.op == CALL_USER:
            c.func_idx = remap_name(ins.func_idx)
        else:
            c.name_idx = remap_name(ins.name_idx)
        return c

    nbind = min(call.argc, callee.n_bind_slots)
    body_out = []
    for k in range(nbind):
        body_out.append(LirInstr(MOV_V, dst=remap(k), a=call.args[k]))

    pos_map = {}   # callee 指令下标 → body_out 下标
    ret_jmps = []  # RET 替换出的 merge JMP 指令（层末回填 len(body)）
    has_ret = False
    for j, ins in enumerate(callee.instrs):
        pos_map[j] = len(body_out)
        if ins.op == RET_V:
            has_ret = True
            # call.dst 属于 caller（本层 body 的调用者）坐标系，不再 remap
            body_out.append(LirInstr(MOV_V, dst=call.dst, a=remap(ins.a)))
            jm = LirInstr(JMP, target=-1)
            body_out.append(jm)
            ret_jmps.append(jm)
            continue
        if ins.op == CALL_USER and depth < MAX_INLINE_DEPTH:
            sub = _resolve_callee(ctx, callee, ins.func_idx)
            # chain 是「已入栈的调用链」，不含当前候选 sub（否则 _inlineable 的
            # callee.name in chain 会自拒）。候选展开成功后链才延长。
            if _inlineable(ctx, sub, chain):
                sub_call = translate_call(ins)
                base_sub = len(body_out)
                subbody = _inline_call(caller, sub, sub_call, ctx,
                                       depth + 1, chain + (callee.name,))
                for c2 in subbody:
                    if c2.op in (JMP, BR_IF_NOT, BR_IF, BR_F):
                        c2.target += base_sub  # 子 body 相对 → 本层绝对
                body_out.extend(subbody)
                continue
            # 环/递归点：保留为调用，且复制产物永久禁止再展开
            #（否则后续遍的插入会使本层已写好的绝对跳转坐标失效 → goto 环）
            c2 = translate_call(ins)
            c2._no_inline = True
            body_out.append(c2)
            continue
        c = LirInstr(ins.op)
        if ins.op in (CONST_VAL, CONST_F64):
            # dst 是 callee 的槽 → 必须重映射（a=常量下标/立即数不动）
            c.dst, c.a, c.imm = remap(ins.dst), ins.a, ins.imm
        elif ins.op == JMP:
            c.target = ins.target  # 占位，层末经 pos_map 回填
        elif ins.op in (BR_IF_NOT, BR_IF, BR_F):
            c.a = remap(ins.a)
            c.target = ins.target
        elif ins.op in (RET_F, STOP, NOP):
            c.dst, c.a = ins.dst, ins.a
        elif ins.op in (LOAD_F64, STORE_F64, MOV_V, MOV_F):
            c.dst, c.a = remap(ins.dst), remap(ins.a)
        elif ADD_F <= ins.op <= POW_F:
            c.dst, c.a, c.b = remap(ins.dst), remap(ins.a), remap(ins.b)
        elif ins.op == CMP_F:
            c.dst, c.a, c.b = remap(ins.dst), remap(ins.a), remap(ins.b)
            c.pred = ins.pred
            c._src_op = ins._src_op
        elif ins.op == NEG_F:
            c.dst, c.a = remap(ins.dst), remap(ins.a)
            c._src_op = ins._src_op
        elif ins.op in (ARITH_GEN, UNARY_GEN):
            c.dst, c.a, c.b, c.pred = remap(ins.dst), remap(ins.a), remap(ins.b), ins.pred
            c._src_op = ins._src_op
        elif ins.op == INDEX_GET:
            c.dst, c.a, c.b = remap(ins.dst), remap(ins.a), remap(ins.b)
        elif ins.op == INDEX_SET:
            c.a, c.b, c.c = remap(ins.a), remap(ins.b), remap(ins.c)
        elif ins.op in (MAKE_ARRAY, MAKE_DICT):
            c.dst = remap(ins.dst)
            c.argc = ins.argc
            c.args = [remap(s) for s in ins.args]
        elif ins.op in (CALL_USER, CALL_BUILTIN, CALL_MODULE, CALL_DYNAMIC):
            c2 = translate_call(ins)
            body_out.append(c2)
            continue
        elif ins.op == REQUIRE:
            c.dst = remap(ins.dst)
            c.str_idx = ins.str_idx
        body_out.append(c)

    # 层内跳转回填（body 内绝对坐标）
    for j, ins in enumerate(callee.instrs):
        if ins.op in (JMP, BR_IF_NOT, BR_IF, BR_F):
            if ins.target in pos_map:
                body_out[pos_map[j]].target = pos_map[ins.target]
    merge_target = len(body_out)
    for jm in ret_jmps:
        jm.target = merge_target
    # callee 无 RET（lower 兜底保证不会发生，双保险）：补 null 返回
    if not has_ret:
        t = caller.alloc_tmp()
        body_out.append(LirInstr(CONST_VAL, dst=t, a=-1))
        body_out.append(LirInstr(MOV_V, dst=call.dst, a=t))
    return body_out


# ---------------------------------------------------------------- 全局槽提升（N-01）
def promote_globals(prog):
    """N-01 全局槽提升：只被 main 触碰的全局槽降级为 main 的局部槽。

    问题（ §1.1）：QOT 顶层变量全是全局槽，C 后端放进
    `static V16 g[N]` 数组——每读一写都是内存往返，cl /O2 无法寄存器化。
    空循环 31ns/iter 里约 60% 是 g[] 往返 + 装箱。

    安全判定（全部满足才提升）：
      1. 全程序无 REQUIRE / CALL_MODULE / CALL_DYNAMIC —— 运行期代码加载
         或模块分派可能以名字触达任意全局（保守一票否决）；
      2. 该全局槽号的读写者集合 == {main}（其它函数引用即否决，如 GUI
         回调写全局的未来形态）；
      3. main 不被回调：AOT 后端无 executeCodeBlock 路径，结构性成立。

    改写：main 中该槽去 GLOBAL_BIT 并重映射到 [n_slots_v, ...) 的新局部
    槽号（避免与现有局部槽号冲突）；n_slots_v 相应扩展。必须在 infer_f64
    之前运行——提升后的槽以 (main, slot) 键参与 F64 形态分析。

    返回提升的槽数。

    A/B 开关：QOT_NO_PROMOTE=1 关闭（与其它 QOT_NO_* 一致，由 pass 自检）。
    """
    if os.environ.get('QOT_NO_PROMOTE') == '1':
        return 0
    main = prog.main
    # ---- 一票否决：任何函数存在运行期代码加载/动态分派 ----
    for f in prog.all_funcs():
        for i in f.instrs:
            if i.op in (REQUIRE, CALL_MODULE, CALL_DYNAMIC):
                return 0
    # ---- 收集 main 引用的全局槽号 + 其它函数的引用集合 ----
    def _slots(i):
        # 操作数完备性对照 lir.py（漏一个就是静默错位——t13_foreach 实证：
        # INDEX_GET 的下标操作数 b 漏收 → foreach 归纳变量未重写）
        out = []
        if i.op in (CONST_VAL, LOAD_F64, STORE_F64, MOV_V, MOV_F, BR_IF_NOT,
                    BR_IF, BR_F, RET_V, UNARY_GEN, CALL_DYNAMIC,
                    THROW, THROWVAL, ENV_NEW, MAKE_CLOSURE):
            out += [i.dst, i.a]
        if i.op == ARITH_GEN:
            out += [i.dst, i.a, i.b]
        if i.op in (ADD_F, SUB_F, MUL_F, DIV_F, MOD_F, POW_F):
            out += [i.dst, i.a, i.b]
        if i.op == CMP_F:
            out += [i.dst]
        if i.op == NEG_F:
            out += [i.dst, i.a]
        if i.op == INDEX_GET:
            out += [i.dst, i.a, i.b]
        if i.op == INDEX_SET:
            out += [i.a, i.b, i.c]
        # M4：FIELD/OMETHOD（漏收 = t13 同款静默错位）
        if i.op == FIELD_GET:
            out += [i.dst, i.a]
        if i.op == FIELD_SET:
            out += [i.a, i.c]
        if i.op == CALL_OMETHOD:
            out += list(i.args)
        if i.op in (CALL_USER, CALL_BUILTIN, CALL_MODULE, CALL_DYNAMIC,
                    MAKE_ARRAY, MAKE_DICT):
            out += list(i.args)
        return out

    main_globals = set()
    for i in main.instrs:
        for s in _slots(i):
            # 槽号 ≥ 0：-1 等「未用/哨兵」操作数在 Python 负数按位与下会伪装成
            # 全局槽（-1 & GLOBAL_BIT 为真），必须先过滤
            if s >= 0 and (s & GLOBAL_BIT):
                main_globals.add(s & ~GLOBAL_BIT)
    if not main_globals:
        return 0
    others = set()
    for f in prog.all_funcs():
        if f is main:
            continue
        for i in f.instrs:
            for s in _slots(i):
                if s >= 0 and (s & GLOBAL_BIT):
                    others.add(s & ~GLOBAL_BIT)
    promotable = main_globals - others
    if not promotable:
        return 0
    # ---- 改写：旧全局槽号 → 新局部槽号（追加在现有局部之后）----
    remap = {}
    nxt = main.n_slots_v
    for g in sorted(promotable):
        remap[GLOBAL_BIT | g] = nxt
        nxt += 1
    main.n_slots_v = nxt

    def rw(x):
        return remap.get(x, x)

    for i in main.instrs:
        if i.op in (CONST_VAL, LOAD_F64, STORE_F64, MOV_V, MOV_F, BR_IF_NOT,
                    BR_IF, BR_F, RET_V, UNARY_GEN, CALL_DYNAMIC,
                    THROW, THROWVAL, ENV_NEW, MAKE_CLOSURE):
            i.dst = rw(i.dst)
            i.a = rw(i.a)
        if i.op == ARITH_GEN:
            i.dst, i.a, i.b = rw(i.dst), rw(i.a), rw(i.b)
        if i.op in (ADD_F, SUB_F, MUL_F, DIV_F, MOD_F, POW_F):
            i.dst, i.a, i.b = rw(i.dst), rw(i.a), rw(i.b)
        if i.op == CMP_F:
            i.dst = rw(i.dst)
        if i.op == NEG_F:
            i.dst, i.a = rw(i.dst), rw(i.a)
        if i.op == INDEX_GET:
            i.dst, i.a, i.b = rw(i.dst), rw(i.a), rw(i.b)
        if i.op == INDEX_SET:
            i.a, i.b, i.c = rw(i.a), rw(i.b), rw(i.c)
        # M4
        if i.op == FIELD_GET:
            i.dst, i.a = rw(i.dst), rw(i.a)
        if i.op == FIELD_SET:
            i.a, i.c = rw(i.a), rw(i.c)
        if i.op == CALL_OMETHOD:
            i.args = [rw(s) for s in i.args]
        if i.op in (CALL_USER, CALL_BUILTIN, CALL_MODULE, CALL_DYNAMIC,
                    MAKE_ARRAY, MAKE_DICT):
            i.args = [rw(s) for s in i.args]
    return len(remap)


# ---------------------------------------------------------------- F64 推断（T-44）
# 槽键：全局槽 ('g', n)（程序级唯一）；局部槽 (fi, n)。f 槽不参与分析。
def infer_f64(prog):
    """全程序槽形态分析 + 数值算术改写。返回改写指令数。

    槽被证明 F64 ⇔ 至少一个写入者，且所有写入者都是数值源；且：
      * 参数槽：所有调用点的对应实参槽也是 F64；
      * 全局槽：所有函数里的所有写点都是数值源（全程序分析天然覆盖）。
    数值源：CONST_VAL(数字常量)、ARITH_GEN(pred 1..6)、UNARY_GEN(1)、
            MOV_V(F64 源)、CALL_BUILTIN(白名单)、(推断后引入的) STORE_F64。
    保守性：bool/字符串/容器/白名单外调用/REQUIRE 一票否决。
    """
    import numbers as _numbers_mod

    funcs = prog.all_funcs()
    fi_of = {id(f): k for k, f in enumerate(funcs)}
    name_to_fn = {}
    for f in prog.funcs.values():
        if f.name:
            name_to_fn[f.name] = f
    pool = getattr(prog, 'const_pool', None)

    def is_num_const(idx):
        if pool is None or idx < 0 or idx >= len(pool):
            return False
        v = pool[idx]
        return isinstance(v, (int, float)) and not isinstance(v, bool)

    def key_of(slot, fi):
        # 槽号 < 0 是未用/哨兵操作数（Python 负数按位与陷阱）→ 独立键，不进全局集
        if slot >= 0 and (slot & GLOBAL_BIT):
            return ('g', slot & ~GLOBAL_BIT)
        if slot >= 0 and (slot & UPVAL_BIT):
            # F1：捕获槽进 env 共享存储，不做 F64 特化（保守；env 访问走通用路径）
            return ('u', slot)
        return (fi, slot)

    def callee_name(f, ins):
        nt = getattr(f, 'name_table', None)
        if nt and 0 <= ins.func_idx < len(nt):
            return nt[ins.func_idx]
        return None

    writers = {}
    call_edges = []   # (callee_fi, param_pos, caller_fi, arg_slot)
    mov_edges = []    # (dst_key, src_key)
    # D-11（0）：算术/取负的「结果必为数字」只在【操作数也是数字】时
    # 成立。原实现无条件把 dst 记成 'num'，导致 b = "x" + 1 → b 被判 F64，
    # 后续 c = b + 2 被特化成裸 double 运算，与 VM 的拼接语义分叉。
    # 这里登记依赖边，不动点阶段要求 dst ∈ f64 ⇒ 全部操作数 ∈ f64。
    arith_deps = []   # (dst_key, [src_key, ...])
    for fi, f in enumerate(funcs):
        for i in f.instrs:
            if i.op in (BR_IF_NOT, BR_IF, JMP, RET_V, STOP, NOP, RET_F, MOV_F):
                continue
            if i.op == CONST_VAL:
                _wk(writers, key_of(i.dst, fi), 'num' if is_num_const(i.a) else 'other')
            elif i.op == ARITH_GEN and 1 <= i.pred <= 6:
                _wk(writers, key_of(i.dst, fi), 'num')
                arith_deps.append((key_of(i.dst, fi),
                                   [key_of(i.a, fi), key_of(i.b, fi)]))
            elif i.op == UNARY_GEN and i.pred == 1:
                _wk(writers, key_of(i.dst, fi), 'num')
                arith_deps.append((key_of(i.dst, fi), [key_of(i.a, fi)]))
            elif i.op == STORE_F64:
                _wk(writers, key_of(i.dst, fi), 'num')
            elif i.op == MOV_V:
                dk, sk = key_of(i.dst, fi), key_of(i.a, fi)
                _wk(writers, dk, 'flow')  # 流写入：数值性继承源
                mov_edges.append((dk, sk))
            elif i.op == CALL_BUILTIN:
                nm = callee_name(f, i)
                _wk(writers, key_of(i.dst, fi),
                    'num' if nm in NUMERIC_BUILTINS else 'other')
            elif i.op in (CALL_MODULE, CALL_DYNAMIC, INDEX_GET, MAKE_ARRAY,
                          MAKE_DICT, REQUIRE, LOAD_F64, CMP_F, ARITH_GEN,
                          UNARY_GEN):
                if i.op in (LOAD_F64, CMP_F):
                    continue  # dst 是 f 槽或 bool 槽（CMP_F 产出 bool → other）
                if i.op in (ARITH_GEN, UNARY_GEN):
                    continue  # 已在上面处理
                _wk(writers, key_of(i.dst, fi), 'other')
            elif i.op in (FIELD_GET, CALL_OMETHOD):
                # M4：字段值/方法返回值 = 'other'（不进 F64 集；方法参数的
                # 数值性传播保守不追 —— 泛型调用面足够正确）
                _wk(writers, key_of(i.dst, fi), 'other')
            if i.op == CALL_USER:
                _wk(writers, key_of(i.dst, fi), 'other')
                callee = name_to_fn.get(callee_name(f, i))
                if callee is not None:
                    ci = fi_of[id(callee)]
                    for pos, s in enumerate(i.args):
                        if pos < callee.n_bind_slots:
                            call_edges.append((ci, pos, fi, s))
            # 布尔结果槽一票否决（绝不能进 F64 集）
            if i.op == CMP_F or (i.op == ARITH_GEN and i.pred >= 7) or \
                    (i.op == UNARY_GEN and i.pred == 2):
                _wk(writers, key_of(i.dst, fi), 'other')

    # ---- 初始集 ----
    f64 = set()
    for k, kinds in writers.items():
        if k[0] == 'u':
            continue  # F1：upval/env 槽永不进 F64 集
        if kinds and kinds <= {'num', 'flow'}:
            f64.add(k)
    # ---- 不动点收缩：MOV 流 + 调用实参 ----
    changed = True
    while changed:
        changed = False
        for dk, sk in mov_edges:
            if sk not in f64 and dk in f64:
                f64.discard(dk)
                changed = True
        for dk, srcs in arith_deps:
            if dk in f64 and any(s not in f64 for s in srcs):
                f64.discard(dk)
                changed = True
        for cfi, cpos, sfi, sslot in call_edges:
            sk = key_of(sslot, sfi)
            ck = (cfi, cpos)
            if sk not in f64 and ck in f64:
                f64.discard(ck)
                changed = True
    # ---- 改写 ----
    count = 0
    for fi, f in enumerate(funcs):
        count += _rewrite_f64(f, f64, fi, key_of)
        # N-03 接口：导出每个函数的已证明 F64 槽集合。SSA 着色用它做
        # 形态感知（不同形态的值不共享颜色），保证共享后的槽级写者
        # 分析（本 pass 的前提）依然成立。
        f.f64_slots = {slot for (k, slot) in f64
                       if k == fi and isinstance(slot, int)}
    return count


def _wk(d, k, v):
    d.setdefault(k, set()).add(v)


def _rewrite_f64(f, f64, fi, key_of):
    """把证明为 F64 的算术改写为 f64 指令 + 指令级 guard（失败走该指令通用语义）。

    关键正确性点：
      * 改写会让指令下标偏移 → 跳转 target 必须按 old→new 映射重定位；
      * 窥孔（STORE_F64 v,fA; LOAD_F64 fB,v → MOV_F）在主循环内做，
        避免事后合并再次打乱下标。
    cgen 对 [LOAD_F64 ×2 + f64op + STORE_F64/CMP_F] 模式生成双路径：
    guard 失败时执行原 ARITH_GEN/UNARY_GEN 通用语义 → 行为恒等。
    """
    out = []
    old2new = {}
    reloc = []           # (new_idx, old_target)
    last_store = None    # (v_slot, f_slot)：窥孔窗口
    n = 0
    tmp_f = [f.n_slots_f]

    def alloc_f():
        s = tmp_f[0]
        tmp_f[0] += 1
        return s

    def v_is_f64(slot):
        return key_of(slot, fi) in f64

    for old_pc, ins in enumerate(f.instrs):
        old2new[old_pc] = len(out)
        if ins.op in (JMP, BR_IF_NOT, BR_IF, BR_F):
            out.append(LirInstr(ins.op, a=ins.a, target=ins.target))
            reloc.append((len(out) - 1, ins.target))
            last_store = None
            continue
        if ins.op == TRY_PUSH:
            # F2：着陆点目标与跳转目标同权重定位（组展开会平移下标；
            # 漏了它 catch 就会跳回 try 头 —— try_control_flow 实证）
            out.append(LirInstr(TRY_PUSH, target=ins.target))
            reloc.append((len(out) - 1, ins.target))
            continue
        expanded = None
        if ins.op == ARITH_GEN and 1 <= ins.pred <= 6 and \
                v_is_f64(ins.a) and v_is_f64(ins.b):
            fa, fb, fd = alloc_f(), alloc_f(), alloc_f()
            g1 = LirInstr(LOAD_F64, dst=fa, a=ins.a)
            g2 = LirInstr(LOAD_F64, dst=fb, a=ins.b)
            opf = LirInstr(ADD_F + (ins.pred - 1), dst=fd, a=fa, b=fb)
            st = LirInstr(STORE_F64, dst=ins.dst, a=fd)
            # 窥孔：LOAD 源恰好是上一条 STORE 的 (v,fA) → MOV_F
            seq = []
            for g in (g1, g2):
                if last_store and last_store[0] == g.a:
                    seq.append(LirInstr(MOV_F, dst=g.dst, a=last_store[1]))
                else:
                    seq.append(g)
                last_store = None
            seq.append(opf)
            seq.append(st)
            expanded = seq
            # 组身与组尾双挂溯源（cgen 双路径兜底用）：窥孔把 LOAD_F64 换成
            # MOV_F 后，组尾仍是权威参考；两条都挂可容忍任一被改写。
            opf._src_op = ins
            st._src_op = ins  # 记录原指令供 cgen 双路径兜底
        elif ins.op == ARITH_GEN and 7 <= ins.pred <= 12 and \
                v_is_f64(ins.a) and v_is_f64(ins.b):
            fa, fb = alloc_f(), alloc_f()
            seq = []
            g1 = LirInstr(LOAD_F64, dst=fa, a=ins.a)
            g2 = LirInstr(LOAD_F64, dst=fb, a=ins.b)
            for g in (g1, g2):
                if last_store and last_store[0] == g.a:
                    seq.append(LirInstr(MOV_F, dst=g.dst, a=last_store[1]))
                else:
                    seq.append(g)
                last_store = None
            cp = LirInstr(CMP_F, dst=ins.dst, a=fa, b=fb, pred=ins.pred)
            cp._src_op = ins
            seq.append(cp)
            expanded = seq
        elif ins.op == UNARY_GEN and ins.pred == 1 and v_is_f64(ins.a):
            fa, fd = alloc_f(), alloc_f()
            g1 = LirInstr(LOAD_F64, dst=fa, a=ins.a)
            if last_store and last_store[0] == g1.a:
                seq = [LirInstr(MOV_F, dst=fa, a=last_store[1])]
            else:
                seq = [g1]
            ng = LirInstr(NEG_F, dst=fd, a=fa)
            ng._src_op = ins
            st = LirInstr(STORE_F64, dst=ins.dst, a=fd)
            seq += [ng, st]
            expanded = seq
        if expanded is None:
            out.append(ins)
            last_store = None
        else:
            out.extend(expanded)
            # 窥孔窗口：STORE_F64 v,f → 下一若是 LOAD_F64 fB,v 可换 MOV_F
            tail = expanded[-1]
            last_store = (tail.dst, tail.a) if tail.op == STORE_F64 else None
            n += 1
    # 跳转重定位
    for new_idx, old_target in reloc:
        if old_target not in old2new:
            raise LowerError2('F64 改写：跳转旧目标 %d 不存在' % old_target)
        out[new_idx].target = old2new[old_target]
    f.instrs = out
    f.n_slots_f = tmp_f[0]
    return n


class LowerError2(Exception):
    pass


# ---------------------------------------------------------------- T-43 内联（v2 块坐标）
# 旧内联器（inline_calls_with_tables）在「线性流 + 绝对跳转下标」上做多遍展开，
# 第二遍的插入会让第一遍写好的坐标失效（G-1：t04/t15 的 goto 环）。v2 把坐标
# 换成块号后，内联退化为「复制块 + 重命名」两步，结构上不可能再漂移。
MAX_INLINE_V2_INSTRS = 120     # 被内联函数体的规模上限
MAX_INLINE_V2_DEPTH = 3        # 内联展开的调用链深度上限
MAX_INLINE_V2_SITES = 24       # 单函数内联点上限（防御组合爆炸）


class _InlineScope(object):
    """内联一次调用所需的重命名上下文。

    形参处理有两种：
      * **直接代换**（默认尽力）：callee 体内从不写形参槽 p 时，把 callee 的
        p 号槽直接映射到调用点的实参槽，连绑定拷贝都省掉。更重要的是：实参槽
        通常是「组尾 STORE_F64」写出的数值槽，直接代换后它就以本来面目进入
        F64 推断与循环去装箱的视野——若先拷进形参槽，那次 MOV_V 会让形参槽
        失去「数值确定定义」，整条链都拆不了箱（order_engine 实测）。
      * **绑定拷贝**（回退）：callee 会写形参槽（如 `loop(n>0){ n -= 1; }`），
        必须给形参一个独立副本，否则会改写调用方的变量。
    """

    def __init__(self, caller, callee, call=None):
        self.caller = caller
        self.vmap = {}      # callee v 槽 → caller v 槽
        self.fmap = {}      # callee f 槽 → caller f 槽
        self.base_v = caller.n_slots_v
        self.base_f = caller.n_slots_f
        self.nmap = {}
        self.binds = []     # [(dst_slot, arg_slot)] 仍需显式绑定的形参
        self.no_bind = set()
        for s in range(callee.n_slots_v):
            self.vmap[s] = self.base_v + s
        for s in range(callee.n_slots_f):
            self.fmap[s] = self.base_f + s
        caller.n_slots_v += callee.n_slots_v
        caller.n_slots_f += callee.n_slots_f
        for idx, nm in enumerate(getattr(callee, 'name_table', []) or []):
            self.nmap[idx] = _intern_name(caller, nm)
        if call is not None:
            self._resolve_params(callee, call)

    def _resolve_params(self, callee, call):
        written = set()
        has_call = False
        for _b, i in callee.iter_instrs():
            for s in slot_ops(i)[0]:
                if s >= 0:
                    written.add(s)
            if i.op in (CALL_USER, CALL_BUILTIN, CALL_MODULE, CALL_DYNAMIC,
                        CALL_OMETHOD):
                has_call = True
        nbind = min(call.argc, callee.n_bind_slots)
        for p in range(nbind):
            arg = call.args[p]
            # 直接代换的安全条件：
            #   1. callee 体内从不写该形参槽（否则会污染调用方变量）；
            #   2. 实参非全局，或 callee 体内没有调用（被调方可能改写同名全局）。
            if p not in written and arg >= 0 and \
                    (not (arg & GLOBAL_BIT) or not has_call):
                self.vmap[p] = arg
                self.no_bind.add(p)
            else:
                self.binds.append((self.vmap.get(p, p), arg))

    def apply(self, ins):
        return rename_instr(ins, self.vmap, self.fmap, self.nmap)


def _intern_name(f2, name):
    """把名字登记进 f2.name_table，返回下标。"""
    nt = getattr(f2, 'name_table', None)
    if nt is None:
        nt = []
        f2.name_table = nt
    if name in nt:
        return nt.index(name)
    nt.append(name)
    return len(nt) - 1


def _inlineable_v2(callee, stack):
    """callee 是否可被内联。stack = 当前展开链上的函数名（防环/防递归）。"""
    if callee is None or callee.kind != 'user':
        return False
    if callee.name in stack:
        return False
    if callee.instr_count() > MAX_INLINE_V2_INSTRS:
        return False
    for _b, i in callee.iter_instrs():
        if i.op in (REQUIRE, CALL_MODULE, CALL_DYNAMIC):
            return False
        if i.op in _SPECIAL_OPS or _is_special_slot(i.a) or \
                any(_is_special_slot(s) for s in i.args):
            # F1/F2：闭包函数体含 upval/env 槽与闭包构造，槽重映射会破坏
            # 环境相对寻址 —— 一律不内联（保守即安全）
            return False
    return True


def _inline_one(f2, bid, k, call, callee, stack):
    """把 (bid, k) 处的 CALL_USER 展开为 callee 的块。返回 True。"""
    blk = f2.blocks[bid]
    scope = _InlineScope(f2, callee, call)

    # ---- 拆块：pre(=blk) / callee 克隆 / merge ----
    post = blk.instrs[k + 1:]
    merge = f2.new_block()
    merge.instrs = post
    merge.term = blk.term
    merge.ft = blk.ft
    blk.instrs = blk.instrs[:k]
    blk.ft = None

    idmap = {}
    for cb in callee.blocks:
        nb = f2.new_block()
        idmap[cb.id] = nb.id
    for cb in callee.blocks:
        nb = f2.blocks[idmap[cb.id]]
        nb.instrs = [scope.apply(clone_instr(i)) for i in cb.instrs]
        nb.term = scope.apply(clone_instr(cb.term)) if cb.term is not None else None
        nb.ft = None if cb.ft is None else idmap[cb.ft]
    for cb in callee.blocks:
        nb = f2.blocks[idmap[cb.id]]
        if nb.term is not None and nb.term.op in (JMP, BR_IF_NOT, BR_IF, BR_F):
            nb.term.target = idmap[nb.term.target]
        for i in nb.instrs:
            if i.op == GUARD_NUM:
                i.target = idmap[i.target]

    # ---- 形参绑定：nbind = min(argc, callee.n_bind_slots)（与解释器逐位一致）----
    # 只有「必须拷贝」的形参才发 MOV_V（见 _InlineScope 的两种形参处理）。
    for (dst, arg) in scope.binds:
        blk.instrs.append(LirInstr(MOV_V, dst=dst, a=arg))
    blk.term = LirInstr(JMP, target=idmap[callee.entry])

    # ---- RET/STOP → 写回 call.dst + JMP merge ----
    for cb in callee.blocks:
        nb = f2.blocks[idmap[cb.id]]
        t = nb.term
        if t is None:
            continue
        if t.op == RET_V:
            nb.instrs.append(LirInstr(MOV_V, dst=call.dst, a=t.a))
            nb.term = LirInstr(JMP, target=merge.id)
        elif t.op == RET_F:
            nb.instrs.append(LirInstr(STORE_F64, dst=call.dst, a=t.a))
            nb.term = LirInstr(JMP, target=merge.id)
        elif t.op == STOP:
            # 用户函数里的 STOP = 返回 null（cgen 对非 main 的 STOP 口径）
            nb.instrs.append(LirInstr(CONST_VAL, dst=call.dst, a=-1))
            nb.term = LirInstr(JMP, target=merge.id)

    # ---- 封锁展开链：克隆体里指向「本次展开链上任一函数」的调用不再展开 ----
    # fib(10) 若不封锁，克隆体里的 fib(...) 会被再次展开成 fib 的副本，
    # 24 个内联点上限内就能膨胀出千条指令（单测 test_recursive_..._inlined 钉住）。
    # 注意这只影响**克隆出来的**调用点：原函数体里的递归调用仍是正常 CALL_USER，
    # 会被 cgen 递归发射 —— 语义不变。
    nt_now = getattr(f2, 'name_table', []) or []
    blocked = set(stack) | {callee.name}
    for cb in callee.blocks:
        nb = f2.blocks[idmap[cb.id]]
        for i in nb.instrs:
            if i.op == CALL_USER and 0 <= i.func_idx < len(nt_now):
                if nt_now[i.func_idx] in blocked:
                    i._no_inline = True
    f2.rebuild_cfg()
    return True


def inline_calls_v2(prog):
    """T-43（v2 实现）：块坐标上的用户函数内联。返回内联点数量。

    为什么现在敢开：G-1 的根因是「多遍指令展开 + 绝对跳转坐标」。v2 里跳转只写
    块号，插入块 = 往列表 append，任何一遍都不会让另一遍写好的坐标失效。

    安全边界（逐条对应旧实现踩过的坑）：
      * 只内联**可解析到 LirFunction** 的 CALL_USER；
      * 被内联体不得含 REQUIRE / CALL_MODULE / CALL_DYNAMIC（运行期加载会逃逸）；
      * 展开链上的函数不再展开（防递归/环无限膨胀）；
      * 规模与内联点数量双上限，异常繁殖立即停手而非耗尽内存；
      * 形参绑定复用解释器的 min(argc, varNames.size()) 口径。
    开关：QOT_NO_INLINE=1 关闭（A/B 归因与回归对照）。
    """
    import lir2
    if os.environ.get('QOT_NO_INLINE') == '1':
        return 0
    by_name = {}
    for f in prog.funcs.values():
        if f.name:
            by_name[f.name] = f
    total = 0
    for f in prog.all_funcs():
        for _round in range(MAX_INLINE_V2_DEPTH):
            f2 = lir2.from_v1(f)
            done = False
            sites = 0
            while sites < MAX_INLINE_V2_SITES:
                target = None
                for b in f2.blocks:
                    for k, i in enumerate(b.instrs):
                        if i.op != CALL_USER or getattr(i, '_no_inline', False):
                            continue
                        nt = getattr(f2, 'name_table', []) or []
                        if not (0 <= i.func_idx < len(nt)):
                            continue
                        callee = by_name.get(nt[i.func_idx])
                        if callee is None or callee is f:
                            continue
                        cf2 = lir2.from_v1(callee)
                        if not _inlineable_v2(cf2, {f.name}):
                            i._no_inline = True
                            continue
                        target = (b.id, k, i, cf2)
                        break
                    if target:
                        break
                if not target:
                    break
                bid, k, call, cf2 = target
                if f2.instr_count() + cf2.instr_count() > MAX_FUNC_INSTRS:
                    call._no_inline = True
                    break
                _inline_one(f2, bid, k, call, cf2, {f.name})
                sites += 1
                total += 1
                done = True
            if done:
                lir2.to_v1(f2, f)
            if not done or sites == 0:
                break
    return total


# ---------------------------------------------------------------- N-02 窥孔前置
# 三个纯局部改写，为「循环去装箱」创造条件，同时各自独立带来收益。
# 顺序固定：折常量 → 转发存储 → 消除死槽 → 去装箱（后者依赖前三者的产物形态）。

_PURE_OPS = (CONST_VAL, CONST_F64, MOV_V, MOV_F, LOAD_F64, STORE_F64,
             ADD_F, SUB_F, MUL_F, DIV_F, MOD_F, POW_F, CMP_F, NEG_F)


def _leaders(f):
    """线性流的基本块首下标（与 lir2.from_v1 同一划分规则）。"""
    n = len(f.instrs)
    ld = {0}
    for pc, i in enumerate(f.instrs):
        if i.op in (JMP, BR_IF_NOT, BR_IF, BR_F):
            if 0 <= i.target < n:
                ld.add(i.target)
            if pc + 1 < n:
                ld.add(pc + 1)
        elif i.op == GUARD_NUM or i.op == TRY_PUSH:
            if 0 <= i.target < n:
                ld.add(i.target)
        elif i.op in (RET_V, RET_F, STOP):
            if pc + 1 < n:
                ld.add(pc + 1)
        elif i.op == THROW:
            if pc + 1 < n:
                ld.add(pc + 1)
    return sorted(x for x in ld if 0 <= x < n)


# 【常数 0】leader 二分定位（编译 CI 线剩余项①）。原实现是
# `max((x for x in ld if x <= pc))` 线性扫 —— 每个候选指令 O(#blocks)，
# 48 模块口径是 optimize_f64 的大头之一。ld 已排序 → bisect O(log n)。
def _ld_le(ld, pc):
    """≤ pc 的最大 leader（块起点）。"""
    import bisect as _bs
    k = _bs.bisect_right(ld, pc) - 1
    return ld[k] if k >= 0 else 0


def _ld_gt(ld, pc, n):
    """> pc 的最小 leader（块终点上界），无则 n。"""
    import bisect as _bs
    k = _bs.bisect_right(ld, pc)
    return ld[k] if k < len(ld) else n


def _compact(f, drop):
    """按 drop（下标集合）删除指令，并重映射全部跳转 target。

    删指令在「线性流 + 绝对跳转下标」上等价于整体坐标平移——这正是 G-1 的
    事故面。这里一次性算 old→new 并重映射（删除点若是跳转目标，落到其后第一条
    存活指令上，语义不变因为被删的一定是不产生可观察效果的纯指令）。
    """
    if not drop:
        return
    n = len(f.instrs)
    old2new = {}
    nxt = 0
    for k in range(n):
        if k in drop:
            continue
        nxt = len(old2new)
        old2new[k] = nxt
    # 被删的下标也给出映射（指向其后第一条存活指令），防御「跳到被删指令」
    pending = len(old2new)
    for k in range(n - 1, -1, -1):
        if k in old2new:
            pending = old2new[k]
        else:
            old2new[k] = pending
    out = [i for k, i in enumerate(f.instrs) if k not in drop]
    for i in out:
        if i.op in (JMP, BR_IF_NOT, BR_IF, BR_F, GUARD_NUM, TRY_PUSH):
            i.target = old2new.get(i.target, i.target)
    f.instrs = out


def _is_num_const(pool, idx):
    """常量池项是数值则返回 float(v)，否则 None（bool 不是数字：VM 里是独立类型）。"""
    if pool is None or idx is None or idx < 0 or not isinstance(idx, int):
        return None
    if idx >= len(pool):
        return None
    v = pool[idx]
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    return None


def fold_const_loads(prog):
    """LOAD_F64 f, s → CONST_F64 f, 值（s 由数值 CONST_VAL 在同块内定义）。

    每次迭代省掉一次常量池堆读（qotrt_consts[idx]，含边界检查）+ 一次标签判定。
    安全条件（保守即安全）：
      * 常量池项是数值（bool 排除）；
      * CONST_VAL 与 LOAD_F64 在同一基本块，且 CONST_VAL 在前；
      * 两者之间 s 未被重新定义；
      * s 是全局槽时，两者之间不得有 CALL_*/REQUIRE（被调方可能写同名全局）。
    改写后 CONST_F64 不再持有 s 的引用 → s 的 CONST_VAL 会由死槽消除接手。
    """
    from lir2 import slot_ops
    pool = getattr(prog, 'const_pool', None)
    cnt = 0
    for f in prog.all_funcs():
        if _has_special_ops(f):
            continue
        ld = _leaders(f)
        for pc, i in enumerate(f.instrs):
            if i.op != LOAD_F64:
                continue
            s = i.a
            if s < 0:
                continue
            # 【常数 0】leader 定位 bisect（原线性扫 O(#blocks)/候选）
            start = _ld_le(ld, pc)
            found = None
            for k in range(pc - 1, start - 1, -1):
                j = f.instrs[k]
                if s in slot_ops(j)[0]:
                    if j.op == CONST_VAL:
                        val = _is_num_const(pool, j.a)
                        if val is not None:
                            found = (k, val)
                    break
            if found is None:
                continue
            k, val = found
            if s & GLOBAL_BIT:
                for kk in range(k + 1, pc):
                    if f.instrs[kk].op in (CALL_USER, CALL_BUILTIN, CALL_MODULE,
                                           CALL_DYNAMIC, REQUIRE):
                        found = None
                        break
                if found is None:
                    continue
            i.op = CONST_F64
            i.imm = val
            i.a = -1
            cnt += 1
    return cnt


def forward_stores(prog):
    """STORE_F64 t, f ; MOV_V x, t → STORE_F64 x, f（省一次 16B V16 拷贝）。

    lower 的产物里「算术结果进临时槽、再 MOV 到变量槽」是常态，原样发射就是
    每迭代一次装箱 + 一次拷贝。安全条件：
      * 两条相邻，且 MOV **不是跳转目标**（否则存在「跳过 STORE 直接进 MOV」的路径）；
      * t 非全局（改写入目标等于删掉一次对全局的写入，别的函数会读到旧值）；
      * t 在本函数内只被这条 MOV 读；
      * 能溯源到原通用指令（否则兜底路径的写入目标无法同步 → 宁可不动）。
    """
    from lir2 import slot_ops
    cnt = 0
    for f in prog.all_funcs():
        if _has_special_ops(f):
            continue
        ldset = set(_leaders(f))
        instrs = f.instrs
        n = len(instrs)
        uses = {}
        for i in instrs:
            for s in slot_ops(i)[1]:
                if s >= 0:
                    uses[s] = uses.get(s, 0) + 1
        drop = set()
        for pc in range(n - 1):
            a, b = instrs[pc], instrs[pc + 1]
            if a.op != STORE_F64 or b.op != MOV_V or b.a != a.dst:
                continue
            if (pc + 1) in ldset:
                continue
            t = a.dst
            if t < 0 or (t & GLOBAL_BIT):
                continue
            if uses.get(t, 0) != 1:
                continue
            src = getattr(a, '_src_op', None)
            if src is None and pc >= 1 and \
                    instrs[pc - 1].op in (ADD_F, SUB_F, MUL_F, DIV_F, MOD_F, POW_F):
                src = getattr(instrs[pc - 1], '_src_op', None)
            if src is None:
                continue
            a.dst = b.dst
            src.dst = b.dst      # 兜底路径（qotrt_arith 的 out）同步指向 x
            drop.add(pc + 1)
            cnt += 1
        _compact(f, drop)
    return cnt


def propagate_copies(prog):
    """块内拷贝传播：MOV_V X, Y → 用 Y 替换 X 的全部读取并删除该 MOV。

    为什么需要它（t12_nested 实证）：lower 为 for 的归纳变量发射一份副本
    （`s52 = s50`），副本在循环里被 F64 组读取。不传播掉，归纳变量就有「非组头
    读取」，去装箱的约束求解会直接否决整个循环。

    安全条件（全部满足才做，保守即安全）：
      * X 与 Y 都不是全局槽（全局的别名关系跨函数，块内分析看不到）；
      * X 在**全函数**内只有这一个定义；
      * X 的**每一处**读取都在本基本块内、且位于该 MOV 之后；
      * 从 MOV 到每处读取之间，Y 没有被重新定义（块内直线，扫到重定义即停止）。
    """
    from lir2 import slot_ops, replace_vuse
    cnt = 0
    for f in prog.all_funcs():
        if _has_special_ops(f):
            continue
        ld = _leaders(f)
        uses, ndefs = {}, {}
        for i in f.instrs:
            for s in slot_ops(i)[1]:
                if s >= 0:
                    uses[s] = uses.get(s, 0) + 1
            for s in slot_ops(i)[0]:
                if s >= 0:
                    ndefs[s] = ndefs.get(s, 0) + 1
        drop = set()
        for pc, i in enumerate(f.instrs):
            if i.op != MOV_V:
                continue
            x, y = i.dst, i.a
            if x < 0 or y < 0 or x == y:
                continue
            if (x & GLOBAL_BIT) or (y & GLOBAL_BIT):
                continue
            if ndefs.get(x, 0) != 1:
                continue
            # 【常数 0】leader 定位线性扫 → bisect（原每候选
            # O(#blocks)，48 模块口径 propagate 占 optimize_f64 大头之一）
            start = _ld_le(ld, pc)
            end = _ld_gt(ld, pc, len(f.instrs))
            hits = []      # (k, instr) 需要改写
            for k in range(pc + 1, end):
                j = f.instrs[k]
                if y in slot_ops(j)[0]:
                    break            # Y 被重定义 → 传播窗口到此为止
                if x in slot_ops(j)[1]:
                    hits.append((k, j))
            need = sum(1 for (k, j) in hits for s in slot_ops(j)[1] if s == x)
            if need != uses.get(x, 0):
                continue              # 有窗口外的读取 → 不动
            done = 0
            for (k, j) in hits:
                done += replace_vuse(j, x, y)
            if done != need:
                # 理论上不会发生（replace_vuse 与 slot_ops 应同源）；不冒险
                raise LowerError2('propagate_copies: 操作数改写数不一致 %d != %d'
                                  % (done, need))
            drop.add(pc)
            cnt += 1
        _compact(f, drop)
    return cnt


def eliminate_dead_slots(prog):
    """删除「写入的槽在全函数内从不被读」的纯指令（不动点到收敛）。

    典型收益：for 循环为每个归纳变量发射的副本槽（MOV_V i_copy, i）无人读取，
    原样发射即每迭代一次 16B 拷贝；折常量后残留的 CONST_VAL 也在此清掉。
    安全边界：只删 _PURE_OPS；v 槽必须非全局（全局可能被别的函数读）。
    """
    from lir2 import slot_ops
    cnt = 0
    for f in prog.all_funcs():
        if _has_special_ops(f):
            continue
        while True:
            uses_v, uses_f = set(), set()
            for i in f.instrs:
                _dv, uv, _df, uf = slot_ops(i)
                uses_v.update(s for s in uv if s >= 0)
                uses_f.update(s for s in uf if s >= 0)
            keep = []
            removed = 0
            drop = set()
            for idx, i in enumerate(f.instrs):
                dead = False
                if i.op in _PURE_OPS:
                    dv, _uv, df, _uf = slot_ops(i)
                    if dv or df:
                        dead = True
                        for s in dv:
                            if s < 0 or (s & GLOBAL_BIT) or s in uses_v:
                                dead = False
                                break
                        if dead:
                            for s in df:
                                if s in uses_f:
                                    dead = False
                                    break
                if dead:
                    removed += 1
                    drop.add(idx)
                else:
                    keep.append(i)
            if not removed:
                break
            _compact(f, drop)
            cnt += removed
    return cnt


# ---------------------------------------------------------------- N-02 循环去装箱
def _match_group(ins, k):
    """在块内指令流 ins 的下标 k 处匹配一个 F64 组。命中返回描述 dict。

    组形态（lir_opt._rewrite_f64 的产物 + 本轮窥孔改写后的形态）：
      算术 [X, X, OP_F, STORE_F64] / 比较 [X, X, CMP_F] / 取负 [X, NEG_F, STORE_F64]
      X ∈ {LOAD_F64（V 槽取裸值）、MOV_F（已是裸 double）、CONST_F64（立即数）}
    """
    n = len(ins)

    def head(j):
        return 0 <= j < n and ins[j].op in (LOAD_F64, MOV_F, CONST_F64)

    if k + 3 < n and head(k) and head(k + 1) and \
            ADD_F <= ins[k + 2].op <= POW_F and ins[k + 3].op == STORE_F64:
        src = getattr(ins[k + 3], '_src_op', None)
        if src is None:
            src = getattr(ins[k + 2], '_src_op', None)
        if src is None or src.op != ARITH_GEN or not (1 <= src.pred <= 6):
            return None
        return {'kind': 'arith', 'len': 4, 'heads': [k, k + 1],
                'op': k + 2, 'store': k + 3, 'src': src}
    if k + 2 < n and head(k) and head(k + 1) and ins[k + 2].op == CMP_F:
        src = getattr(ins[k + 2], '_src_op', None)
        if src is None:
            return None
        return {'kind': 'cmp', 'len': 3, 'heads': [k, k + 1],
                'op': k + 2, 'store': None, 'src': src}
    if k + 2 < n and head(k) and ins[k + 1].op == NEG_F and ins[k + 2].op == STORE_F64:
        src = getattr(ins[k + 1], '_src_op', None)
        if src is None:
            return None
        return {'kind': 'neg', 'len': 3, 'heads': [k],
                'op': k + 1, 'store': k + 2, 'src': src}
    return None


def _try_unbox_loop(f2, lp, pool):
    """对单个自然循环做去装箱。成功返回 True（f2 已被改写）。

    不变式（正确性全靠它）：
      1. 快循环执行期间，候选集 C 中【变体槽】的 V16 是**陈旧值**；因此变体槽
         在快循环里只允许 (a) 被 F64 组头读取（改写成读镜像 MOV_F）、(b) 被组尾
         写入（改写成写镜像 MOV_F）。任何其它读取（调用实参/下标/返回/分支...）
         都会让该槽退出 C。
      2. 常量槽与不变槽在快循环里从不被写 → V16 始终有效，出边无需回写。
      3. 每条出边都插入回写块（STORE_F64 s, mf_s）→ 循环外看到的 V16 与
         逐条执行逐位一致。
      4. 守卫失败的慢路径是**逐字未改动的原指令流**，所以行为恒等；
         且守卫只在循环入口判一次，失败后再也不会误入快循环。
    """
    from lir2 import slot_ops, is_global, clone_instr

    body = lp.body
    ninstr = sum(len(f2.blocks[b].instrs) + 1 for b in body)
    if ninstr > MAX_UNBOX_INSTRS:
        return False
    # 已版本化的循环不再重复处理：前置头里那条指向本头的 GUARD_NUM 就是标记。
    # 没有它，慢循环（逐字未改动的原指令流）在下一轮会被再克隆一次，
    # 每轮加一份副本 → 代码爆炸（实测一轮变八份）。
    for p in f2.blocks[lp.header].preds:
        for i in f2.blocks[p].instrs:
            if i.op == GUARD_NUM and i.target == lp.header:
                return False

    # ---- 循环体内的 F64 组 ----
    groups = []          # (bid, start_idx, desc)
    for bid in sorted(body):
        ins = f2.blocks[bid].instrs
        k = 0
        while k < len(ins):
            g = _match_group(ins, k)
            if g is None:
                k += 1
            else:
                groups.append((bid, k, g))
                k += g['len']
    if not groups:
        return False

    # ---- 循环体内不允许运行期代码加载 / 动态分派 ----
    for bid in body:
        for i in f2.blocks[bid].instrs:
            if i.op in (REQUIRE, CALL_MODULE, CALL_DYNAMIC):
                return False

    # ---- def/use 收集（含终结器）----
    defs_in, uses_in = {}, {}
    for bid in sorted(body):
        blk = f2.blocks[bid]
        for idx, i in enumerate(blk.instrs):
            dv, uv, _df, _uf = slot_ops(i)
            for s in dv:
                if s >= 0:
                    defs_in.setdefault(s, []).append(i)
            for s in uv:
                if s >= 0:
                    uses_in.setdefault(s, []).append((bid, idx))
        t = blk.term
        if t is not None:
            for s in slot_ops(t)[1]:
                if s >= 0:
                    uses_in.setdefault(s, []).append((bid, -1))

    group_defs = set()
    group_reads = set()
    for (bid, k, g) in groups:
        ins = f2.blocks[bid].instrs
        if g['store'] is not None:
            group_defs.add(ins[g['store']].dst)
        for h in g['heads']:
            if ins[h].op == LOAD_F64 and ins[h].a >= 0:
                group_reads.add(ins[h].a)

    # ---- 约束求解：C 收缩到不动点 ----
    cand = set(group_defs) | set(group_reads)

    def g_vinputs(bid, g):
        ins = f2.blocks[bid].instrs
        out = []
        for h in g['heads']:
            if ins[h].op == LOAD_F64:
                out.append(ins[h].a)
        return out

    head_slot = {}    # (bid, idx) -> 该组头读取的 v 槽（仅 LOAD_F64）
    for (bid, k, g) in groups:
        ins = f2.blocks[bid].instrs
        for h in g['heads']:
            if ins[h].op == LOAD_F64:
                head_slot[(bid, h)] = ins[h].a

    while True:
        dropped = set()
        for s in list(cand):
            # A1 只处理局部槽：全局可能被被调函数改写，不变式不成立
            if is_global(s) or s < 0:
                dropped.add(s)
                continue
            # A2 变体槽在体内的**每一个**定义都必须是「组尾 STORE_F64」
            #     （若还有别的写入者，快循环里没人维护它的 V16）
            for i in defs_in.get(s, []):
                if s not in group_defs or i.op != STORE_F64:
                    dropped.add(s)
                    break
            if s in dropped:
                continue
            # A3 变体槽在体内的每次读取都必须是组头
            for (bid, idx) in uses_in.get(s, []):
                if head_slot.get((bid, idx)) == s:
                    continue
                dropped.add(s)
                break
        cand -= dropped
        # B 组要么整组拆箱，要么整组保留 guard
        for (bid, k, g) in groups:
            vin = g_vinputs(bid, g)
            if all(x in cand for x in vin):
                continue
            # 保留 guard 的组：
            #  · 它读 V16 —— 变体候选槽的 V16 在快循环里是陈旧的，必须退出 C；
            #  · 它写 V16 —— 若目标在 C 里，下游读的是镜像而镜像没被更新，
            #    同样必须退出 C（t12_nested 实证：i*10+j 因归纳变量副本未拆箱，
            #    却仍把结果槽留在 C 里 → total 读到陈旧镜像）。
            for x in vin:
                if x in cand and x in defs_in:
                    dropped.add(x)
            if g['store'] is not None:
                dst = f2.blocks[bid].instrs[g['store']].dst
                if dst in cand and dst in defs_in:
                    dropped.add(dst)
        cand -= dropped
        if not dropped:
            break

    if not cand:
        return False
    if len(cand) > MAX_UNBOX_SLOTS:
        return False
    # 至少要有「变体槽」被拆箱才值得克隆（只拆不变槽的收益极小）
    variant = sorted(s for s in cand if s in defs_in)
    if not variant:
        return False
    # 至少要有一个组的全部输入都在 C 里（否则快循环与慢循环逐字相同）
    if not any(all(x in cand for x in g_vinputs(bid, g)) for (bid, k, g) in groups):
        return False

    # ---- 分配镜像 f 槽 ----
    mirrors = {}
    nxt = f2.n_slots_f
    for s in sorted(cand):
        mirrors[s] = nxt
        nxt += 1
    f2.n_slots_f = nxt

    # ---- 克隆循环体（落下边先登记待定，出边回写块建好后统一解析）----
    idmap = {}
    for bid in sorted(body):
        idmap[bid] = f2.new_block().id
    ft_pending = []
    for bid in sorted(body):
        src = f2.blocks[bid]
        nb = f2.blocks[idmap[bid]]
        nb.instrs = [clone_instr(i) for i in src.instrs]
        nb.term = clone_instr(src.term)
        if src.ft is not None:
            ft_pending.append((nb.id, src.ft))
        nb.ft = None

    # ---- 改写克隆体 ----
    # 注意：这里的「是否拆箱」判据必须与上面约束求解用的**完全一致**
    # （all(vin ⊆ C)，对 vin 为空的常量组恒真）。曾经写成 bool(vin) and ...，
    # 结果是：求解器认为常量组已拆箱、改写器却没拆 —— 该组的 STORE 仍写 V16、
    # 镜像从未被赋值，而下游组读的是镜像 → 快循环算出陈旧值/0（fuzz
    # seed=20261155 实证：fn0 返回 -0.0 而非 0.25）。这类「两处判据」必须
    # 收敛到一个谓词，否则迟早再次漂移。
    for bid in sorted(body):
        blk = f2.blocks[idmap[bid]]
        ins = blk.instrs
        for (gbid, k, g) in groups:
            if gbid != bid:
                continue
            vin = g_vinputs(gbid, g)
            unbox = all(x in cand for x in vin)
            if unbox:
                for h in g['heads']:
                    hi = ins[h]
                    if hi.op == LOAD_F64:
                        hi.op = MOV_F
                        hi.a = mirrors[hi.a]
                ins[g['op']]._src_op = None
                if g['store'] is not None:
                    st = ins[g['store']]
                    st._src_op = None
                    if st.dst in mirrors:
                        fsrc = st.a              # STORE_F64.a 是 f 槽（源）
                        st.op = MOV_F            # MOV_F: dst/a 都是 f 槽
                        st.dst = mirrors[st.dst]
                        st.a = fsrc
            # 非拆箱组：原样保留（guard 读的是 V16，而它的候选输入在快循环里
            # 都是不变槽 → V16 始终有效，由约束求解的 B 规则保证）

    # ---- 出边回写块 ----
    exit_blocks = {}

    def exit_for(target):
        if target in exit_blocks:
            return exit_blocks[target]
        xb = f2.new_block()
        for s in variant:
            xb.instrs.append(LirInstr(STORE_F64, dst=s, a=mirrors[s]))
        xb.term = LirInstr(JMP, target=target)
        exit_blocks[target] = xb.id
        return xb.id

    for bid in sorted(body):
        nb = f2.blocks[idmap[bid]]
        t = nb.term
        if t.op in (JMP, BR_IF_NOT, BR_IF, BR_F):
            if t.target in body:
                t.target = idmap[t.target]
            else:
                t.target = exit_for(t.target)
    # 落下边：循环内 → 指向克隆；循环外（该分支是一条出边）→ 先回写再走，
    # 保证「落下」这条路径出去时 V16 也是最新的。
    for (nb_id, src_ft) in ft_pending:
        if src_ft in body:
            f2.blocks[nb_id].ft = idmap[src_ft]
        else:
            ftb = f2.blocks[src_ft]
            tgt = ftb.term.target if (ftb.term is not None and ftb.term.op == JMP) else None
            if tgt is None:
                return False
            f2.blocks[nb_id].ft = exit_for(tgt)

    # ---- 前置头：GUARD_NUM（只守「进入循环时旧值会被读」的槽）+ 拆箱 ----
    # 守卫集合 = C ∩ live_in(header)；其余槽在循环体内每次读取前必有写入，
    # 旧值无人关心（临时槽进入循环时是 VT_NONE，若把它们塞进守卫，守卫会
    # 永远失败 → 快循环永远进不去，版本化退化成纯代码膨胀）。
    live_in, _live_out = f2.compute_liveness()
    guarded = sorted(s for s in cand if s in live_in.get(lp.header, set()))
    ph = f2.new_block()
    if guarded:
        ph.instrs.append(LirInstr(GUARD_NUM, args=guarded, target=lp.header))
    for s in guarded:
        ph.instrs.append(LirInstr(LOAD_F64, dst=mirrors[s], a=s))
    ph.term = LirInstr(JMP, target=idmap[lp.header])

    entries = f2.loop_entries(lp)
    if lp.header == f2.entry:
        f2.entry = ph.id
    elif not entries:
        return False
    for (p, _h) in entries:
        if p == ph.id:
            continue
        f2.retarget(f2.blocks[p], lp.header, ph.id)

    f2.rebuild_cfg()
    return True


def unbox_loops(prog):
    """N-02：循环去装箱（循环版本化）。返回被版本化的循环数。

    形态（见 _try_unbox_loop 的不变式说明）：
        P : GUARD_NUM [C] -> H     ; 任一槽非 NUM → 走未特化的原循环
            LOAD_F64 mf_s, s ...   ; 拆箱
            JMP H'
        H': ...裸 double 运算...    ; 快循环
            BR -> X
        X : STORE_F64 s, mf_s ...  ; 出边回写
            JMP <原出边目标>

    只处理**最内层**自然循环：外层循环的克隆会把内层也复制一遍，代码体积与
    编译时间都翻倍而收益不叠加。

    一轮处理全部最内层循环后即收工（不再迭代）：支配/循环信息是在同一份 CFG
    上取的，改完再复用就不可靠；而「再来一轮」会让慢循环（逐字未改动的原指令流）
    被反复克隆 —— 每轮多一份副本。一轮之内，各个最内层循环的 body 互不相交，
    彼此的改写互不干扰。
    """
    import lir2
    if os.environ.get('QOT_NO_UNBOX') == '1':
        return 0
    pool = getattr(prog, 'const_pool', None)
    total = 0
    for f in prog.all_funcs():
        if _has_special_ops(f):
            continue
        f2 = lir2.from_v1(f)
        idom = f2.compute_idom()
        loops = f2.natural_loops(idom)
        inner = [lp for lp in loops
                 if not any((o is not lp) and (o.body < lp.body) for o in loops)]
        n = 0
        for lp in inner:
            if _try_unbox_loop(f2, lp, pool):
                n += 1
        if n:
            lir2.to_v1(f2, f)
            total += n
    return total


def snapshot_user_funcs(prog):
    """N-04：给每个用户函数拍一份 v1 快照（深拷贝），供 cgen 生成双体快速入口。

    快照时机 = 内联之后、infer_f64 之前：
      * 内联后：被内联掉的函数不再有调用点，快照白拍；留下的调用点集合即
        「不内联」的最终集合（递归/超限），正是快速入口的目标人群；
      * infer_f64 前：快照是【未特化】的通用形态（ARITH_GEN 形态 + 常量池
        下标完整），N-04 的局部数值证明在它上面独立做——与主路径的全程序
        F64 推断互不干扰（主路径可能因个别非数值调用点否决整个函数，
        快速入口只看「本次调用实参全为数字」这一条运行期事实）。
    f 槽在快照里必为空（infer_f64 是唯一生产者）；防御性断言。
    """
    snaps = {}
    for f in prog.funcs.values():
        if f.kind != 'user':
            continue
        g = LirFunction(f.name, 'user')
        g.n_slots_v = f.n_slots_v
        g.n_slots_f = f.n_slots_f
        g.n_bind_slots = f.n_bind_slots
        g.req_names = list(f.req_names)
        g.name_table = list(getattr(f, 'name_table', []) or [])
        g.slot_forms = dict(getattr(f, 'slot_forms', {}) or {})
        g.instrs = [clone_instr(i) for i in f.instrs]
        for i in g.instrs:
            if i.op in (LOAD_F64, STORE_F64, MOV_F, ADD_F, SUB_F, MUL_F,
                        DIV_F, MOD_F, POW_F, CMP_F, NEG_F, RET_F, BR_F):
                raise LowerError2('N-04 快照发现 f 槽指令（应只出现在 infer_f64 后）')
        snaps[f.name] = g
    prog.fast_snapshots = snaps
    return snaps


def peephole_stage(prog):
    """窥孔四连（fold → forward → copy → dead），供 SSA 之前调用。

    N-03 阶段序修订：窥孔必须在 **SSA 之前**运行——fold_const_loads 会把
    [CONST_V; ...; LOAD f←V] 折成 CONST_F64，令 V 常量临时变成死定义；
    若这发生在 SSA **之后**，值级死代码（每迭代一次 16B 装箱写）残留在
    快循环里，而槽级 eliminate_dead_slots 无法看见（slot 仍被「别的值」
    读取——order_engine 203 ns 实测）。先窥孔后 SSA，死临时在 SSA 的
    值级死定义消除中被正确清掉。
    """
    off = lambda k: os.environ.get(k) == '1'          # noqa: E731
    peeph = not off('QOT_NO_PEEPH')
    n = {}
    n['fold_const_loads'] = fold_const_loads(prog) if (peeph and not off('QOT_NO_FOLD')) else 0
    n['forward_stores'] = forward_stores(prog) if (peeph and not off('QOT_NO_FWD')) else 0
    n['propagate_copies'] = propagate_copies(prog) if (peeph and not off('QOT_NO_COPY')) else 0
    n['eliminate_dead_slots'] = eliminate_dead_slots(prog) if (peeph and not off('QOT_NO_DEAD')) else 0
    return n


def optimize_f64(prog):
    """N-02 数值优化流水线：循环去装箱 → f 域合并。

    阶段序（N-03 修订）：infer_f64 与窥孔阶段（peephole_stage）已在
    **本函数之前**运行（类型特化与窥孔先于槽共享），本函数在 SSA 合并后
    的形态上做去装箱与 f 域合并。窥孔在本函数里保留执行（对已处理流是
    近 no-op），以兼容不含 SSA 的调用方（单元测试管线）。

    A/B 归因开关（与 N-01 的 QOT_NO_PROMOTE 同约定）：
      QOT_NO_PEEPH=1   关掉全部窥孔（只留去装箱）
      QOT_NO_FOLD=1 / QOT_NO_FWD=1 / QOT_NO_COPY=1 / QOT_NO_DEAD=1  逐个关
      QOT_NO_UNBOX=1   关掉循环去装箱
      QOT_NO_FCOAL=1   关掉 f 域合并（lir_ssa.coalesce_f）
    """
    off = lambda k: os.environ.get(k) == '1'          # noqa: E731
    n = peephole_stage(prog)
    n['unbox_loops'] = unbox_loops(prog)
    # N-03 第二段：f 域 MOV_F 合并（unbox 之后、cgen 之前；QOT_NO_FCOAL 关）
    import lir_ssa
    n['coalesce_f'] = lir_ssa.coalesce_f(prog)
    errs = prog.verify()
    if errs:
        raise LowerError2('N-02 优化后 LIR 自检失败: %s' % '; '.join(errs[:5]))
    return n


# ---------------------------------------------------------------- arena 回收
# M-PERF 收尾二（b3a 形态：轮内短命对象）。对逃逸判定通过的循环，在循环头插
# ARENA_PUSH、回边与每个出体跳转前插 ARENA_RESET —— 运行时把池 bump 指针
# 回滚到循环头快照，轮内切出的 slab 块被下一轮原地覆盖：分配回归纯指针加法、
# RSS 平坦、无 slab 进货/首触页错误的轮均摊销。
#
# 【安全契约（五条一票否决，宁可不插桩也不冒悬空风险）】
#   1. 体内不得有 CALL_USER / CALL_DYNAMIC / CALL_MODULE / REQUIRE / STOP /
#      RET_V / RET_F / INDEX_SET，以及清单 calls_back=true 的内置
#      （map/filter/reduce/sort_by —— 回调体可把 arena 块存进任何跨轮容器，
#      插桩期无法证明其不逃逸）：未内联调用与容器写都可能把循环内分配的
#      对象存到跨轮位置（第一版不做逃逸闭包，见分支）；
#   2. 出口后 use 的槽（回边之后全部指令的 use_v，含 GUARD args 与全局槽）
#      若被「染色」——即其值可能来自本循环的 arena 分配 —— 否决。染色规则：
#      MAKE_ARRAY/MAKE_DICT/INDEX_GET/ARITH_GEN/UNARY_GEN/CALL_BUILTIN(白名单
#      外) 的 dst 初始染色，MOV_V 传播；两条豁免：
#        * dst ∈ f.f64_slots（infer_f64 已证明恒数字）不染；
#        * NUMERIC_BUILTINS 的 dst 恒数字不染。
#   3. 只处理「体 [t, j] 内无第二个回边、出口目标全部向前」的规整循环
#      （多出口/怪跳转一律不插），出口跳转前对称插 ARENA_RESET 保栈配对。
#      「向前」= 出体跳转 target > pc。goto 布局把出口块（output/STOP 等）
#      物理放在头与体之间，跳向它们的出体跳转是合法前向出口（trampoline
#      兜住）；【P0-POOL-1】向后跳出候选区（target < pc 且 ∉ body）的一律
#      否决 —— 那是回边 JMP 链被误认成"循环头"的幻影循环：形状上 [t,j]
#      只圈住真循环的尾部指令，PUSH 却开在真循环体外、横跨整个真循环体
#      （csv_parse 实证：区域跨着 _stx_csv_row 的全部轮内分配，RESET 落在
#      arr_push(rows,row) 之后 → row 的内容被回滚 → 悬空）。
#   4.【P0-POOL-1 · 否决 3】容器内容逃逸：体内出现清单 mutates_container=
#      true 的内置（arr_push/arr_set/arr_insert/dict_set）时，槽级染色
#      （第 2 条）结构性失明 —— arr_push(row, str_trim(f)) 的返回值不在
#      任何 dst 上，直接经 qotrt_arr_push 存进 row。若此时容器可能跨轮
#      存活（容器槽来自区域外 / 轮内新建但出口仍用 / 被存进另一个容器），
#      且被存值可能染（体内有产物写或 MOV 链可达产物写）→ 否决。
#      轮内新建且出口不用、也不外存的容器（b3a 形态）不否决 —— 内容与
#      容器同轮生灭，回滚一起回收。
#   5. branchy 退化路径的染色检查必须追 MOV 链（`out = out + c` 形态：
#      出槽只剩 MOV_V 写，直接写的是 ARITH_GEN 中间槽）—— 否则会放过
#      出口仍用 out 的循环（normalize/wrap 形态）。
#
# 正确性护栏：QOT_NO_ARENA=1 整体关闭（_dbg_switches 登记）；suite 全量 +
# bench --verify 四方一致是行为不变的长期差分裁判；test_lir 的 std_text
# 零插桩 + b3a 仍插桩双锚（P0-POOL-1 回归钉）。
def arena_loops(prog):
    if os.environ.get('QOT_NO_ARENA') == '1':
        return 0
    total = 0
    for f in prog.all_funcs():
        total += _arena_loops_one(f)
    errs = prog.verify()
    if errs:
        raise LowerError2('arena 插桩后 LIR 自检失败: %s' % '; '.join(errs[:5]))
    return total


def _arena_use_v(i):
    """指令 i 读取的 v 槽集合（复用 slot_ops 真相源，过滤全局位不滤 ——
    全局槽同样可能引用 arena 块）。"""
    dv, uv, _df, _uf = slot_ops(i)
    return set(uv)


def _arena_loops_one(f):
    instrs = f.instrs
    n = len(instrs)
    if n < 4:
        return 0
    nt = getattr(f, 'name_table', None) or []

    # ---- 收集候选回边（JMP target <= pc）----
    cands = []
    for pc, i in enumerate(instrs):
        if i.op == JMP and 0 <= i.target <= pc - 2:
            cands.append((i.target, pc))
    if not cands:
        return 0

    # ---- 写点索引：dst 写（非 MOV）/ MOV 流写 ----
    # 【警告】不要用 f.f64_slots 做染色豁免：它是 infer_f64 在 SSA/窥孔
    # 【之前】按当时槽号导出的，optimize_f64 与 SSA 着色改写槽号后映射
    # 已过时（bound_guard_arith_str_flow 实证：装串的槽 15 撞上旧 F64 槽
    # 号 → 豁免 → 插桩 → 出口 output 读悬空）。染色一律走出口点程序顺序
    # 分析（color_at），不读任何跨 pass 元数据。

    def _write_kind(i):
        """写指令的染色定性：True=可能产出 arena 块（染）；
        False=恒数字/bool/无 dst 写（不染）；None=MOV 流写（回溯源）。"""
        op = i.op
        if op == MOV_V:
            return None
        if op in (MAKE_ARRAY, MAKE_DICT, INDEX_GET, ARITH_GEN, UNARY_GEN,
                  MAKE_CLOSURE, ENV_NEW):
            return True
        # M4：字段值/方法返回值 = 任意值（可能持 arena 块引用）→ 染色
        if op in (FIELD_GET, CALL_OMETHOD):
            return True
        if op == CALL_BUILTIN:
            nm = nt[i.name_idx] if 0 <= i.name_idx < len(nt) else ''
            # 豁免名单取自 builtins.toml 的 alloc_free 字段（glen /
            # __iter_len / __is_number 恒数字；output 语句型不写 dst，
            # 其 dst 槽沿用旧值）；strings/numbers/type 等返回新块。
            return nm not in ALLOC_FREE_BUILTINS
        return False  # CONST_VAL / STORE_F64 / 其它 = 恒非 arena

    def _last_write(slot, limit):
        """程序顺序 < limit 内对 slot 的最后一条写指令下标；无则 -1。
        MOV_V 记为写（流），由 color_at 回溯其源。"""
        best = -1
        for pc in range(limit):
            i = instrs[pc]
            d = i.dst
            if d is None or d < 0:
                continue
            if d == slot:
                best = pc
        return best

    def _color_at(slot, limit, seen):
        """出口点 limit 时刻，slot 的值是否可能指向 arena 块。
        沿 MOV 链回溯到 < limit 的最后写，按写类型定性。
        体含内层条件分支时此法不健全 —— 由调用方的 branchy 退化兜底。"""
        if slot in seen or slot is None or slot < 0:
            return False
        w = _last_write(slot, limit)
        if w < 0:
            return False
        i = instrs[w]
        if i.op == MOV_V:
            return _color_at(i.a, limit, seen | {slot})
        return bool(_write_kind(i))

    def _nm(i):
        """CALL_BUILTIN 的清单名（防御：name_idx 越界得 ''）。"""
        if i.op != CALL_BUILTIN or not (0 <= i.name_idx < len(nt)):
            return ''
        return nt[i.name_idx]

    def _body_dyed(slot, seen=frozenset()):
        """branchy 退化用的体内染色判定：slot 在体内是否可能有 arena 块。
        直接产物写（_write_kind True）→ 染；MOV_V 写 → 沿源槽递归（P0-POOL-1
        第 5 条契约：`out = out + c` 形态的出槽只剩 MOV 写，不追链会漏）。
        seen 防环；slot 从未在体内写 → 值来自区域外 → 不染。"""
        if slot is None or slot < 0 or slot in seen:
            return False
        for pc in body:
            i = instrs[pc]
            if i.dst != slot:
                continue
            k = _write_kind(i)
            if k is True:
                return True
            if k is None and _body_dyed(i.a, seen | {slot}):
                return True
        return False

    # ---- 逐候选判定（按回边 pc 降序处理） ----
    # 【P0-POOL-1】下标迁移账本：插桩在 t/j 处【前插】指令（PUSH/RESET 各 1
    # 条），此后所有候选（包括嵌套在已插桩区域内的内层循环候选）的下标都要
    # 平移 —— 旧注释"插桩只影响其后的下标"只对平坦循环成立，wrap 这类嵌套
    # 形态会拿陈旧下标圈错区域。ins_at 记录每次前插位置（当前坐标空间），
    # 候选处理前先换算到当前空间。
    ins_at = []
    inserted = 0
    for (t0, j0) in sorted(cands, key=lambda p: -p[1]):
        t = t0 + sum(1 for q in ins_at if q <= t0)
        j = j0 + sum(1 for q in ins_at if q <= j0)
        if j >= len(instrs) or instrs[j].target != t:
            continue  # 迁移后回边失配（防御：不该发生）
        # 真实循环体 = [t, j] 中【能到达回边 j】的指令。goto 布局会把出口块
        # （output/STOP 等，物理上插在循环头与体之间）圈进线性区间 —— 它们
        # 到不了回边，不是每轮执行的工作，必须排除（否则出口块里的 STOP 会
        # 误触否决 1）。
        rev = {j}
        work = [j]
        while work:
            cur = work.pop()
            cands_pred = []
            if cur - 1 >= t and instrs[cur - 1].op not in \
                    (JMP, RET_V, RET_F, STOP):
                # 顺序前驱：前一条必须不是终结指令（JMP/RET/STOP 无落下边）
                cands_pred.append(cur - 1)
            for pc in range(t, j + 1):
                pi = instrs[pc]
                if pi.op in (BR_IF_NOT, BR_IF, BR_F, JMP, GUARD_NUM) \
                        and pi.target == cur and pc != cur:
                    cands_pred.append(pc)
            for p in cands_pred:
                if p not in rev:
                    rev.add(p)
                    work.append(p)
        body = rev

        # 否决 1：体含跨轮引用边界指令
        # （F1/F2：THROW/TRY/闭包构造也在否决列 —— 异常跨越 arena 区会留下
        # 未配对的 push；闭包对象/env 生命周期与轮回收正交，一律不插桩。
        #  P0-POOL-1：calls_back 内置（map/filter/reduce/sort_by）按 CALL_USER
        #  同类否决 —— 回调体在 arena 区内执行任意 QOT 代码。）
        bad = False
        for pc in body:
            i = instrs[pc]
            # M4：CALL_OMETHOD 按 CALL_USER 同类否决（方法体可把 arena 块
            # 存进跨轮容器）；FIELD_SET 按 INDEX_SET 同类否决（对象可跨轮
            # 存活，写进字段的 arena 块会随回边回滚 → 悬空）
            if i.op in (CALL_USER, CALL_DYNAMIC, CALL_MODULE, REQUIRE,
                        STOP, RET_V, RET_F, INDEX_SET,
                        CALL_OMETHOD, FIELD_SET) or i.op in _SPECIAL_OPS:
                bad = True
                break
            if i.op == CALL_BUILTIN and _nm(i) in CALLBACK_BUILTINS:
                bad = True
                break
        if bad:
            continue

        # 否决 1.5（P0-POOL-1 · 幻影循环）：两条形状律，任一命中即否决。
        #   (a) t ∈ body —— 真循环的回边 target 必在体内可达（从 j 反向走
        #       前驱必然经过 t：执行上每一轮都要经过头）。csv_parse 的
        #       候选 (29,33) 实证：body={31,32,33}，t=29 是【出体 JMP】，
        #       给它插 PUSH 会把 arena 区开在真循环体外、横跨真循环体
        #       （第 2 轮起区域罩着 _stx_csv_row 的全部轮内分配，RESET 又
        #       落在 arr_push(rows,row) 之后 → rows 里的 row 内容被回滚
        #       → 悬空 → 字段截断/同块双发）。
        #   (b) 体（除回边 j 外）不得向后跳出候选区：goto 布局下出体跳转
        #       全部向前（跳向出口块 target>pc，或跳向 > j 的出口），向后
        #       跳出（target < pc 且 ∉ body）= 怪形状，不插。
        if t not in body:
            continue
        phantom = False
        for pc in body:
            i = instrs[pc]
            if pc == j:
                continue
            if i.op in (JMP, BR_IF_NOT, BR_IF, BR_F, GUARD_NUM) \
                    and i.target not in body and i.target < pc:
                phantom = True
                break
        if phantom:
            continue

        # 否决 2：pop 之后执行的代码若读到 arena 块 → 否决。pop 后存活的
        # 代码两处：回边之后 [j+1:]，以及出口块（[t, j] \ body —— 经出体
        # trampoline 的 RESET 后执行）。
        # 「读到 arena 块」判定（出口点语义）：槽在出口指令处 use 时，沿
        # MOV 链回溯到该出口【程序顺序之前】的最后写，写类型可产出 arena
        # 块 → 染 → 否决。单路径直线体上这是精确判定；体含内层条件分支
        # （target ∈ body 的 BR/GUARD）时「最后写」不再代表所有路径，
        # 退化为「体曾被可产出 arena 块的指令写过即染」。
        branchy = any(instrs[pc].op in (BR_IF_NOT, BR_IF, BR_F, GUARD_NUM)
                      and instrs[pc].target in body for pc in body)

        exit_slots = set()   # pop 后存活代码 use 的槽
        for i in instrs[j + 1:]:
            exit_slots |= _arena_use_v(i)
        for pc in range(t, j + 1):
            if pc not in body:
                exit_slots |= _arena_use_v(instrs[pc])

        leaked = False
        for slot in exit_slots:
            if slot is None or slot < 0:
                continue
            if branchy:
                # 退化：体曾被染写 → 该槽可能存活到出口（P0-POOL-1：MOV 链
                # 必须追 —— `out = out + c` 出槽只剩 MOV 写，漏追 = 放过
                # normalize/wrap 形态的悬空）
                if _body_dyed(slot):
                    leaked = True
                    break
            else:
                if _color_at(slot, j + 1, frozenset()):
                    leaked = True
                    break
        if leaked:
            continue

        # 否决 3（P0-POOL-1 · 容器内容逃逸）：mutates_container 内置把
        # args[1:] 经 qotrt_arr_push/dict_set 存进 args[0] —— 被存值不在
        # 任何 dst 上，槽级染色（否决 2）结构性看不见。仅当「被存值可能
        # 染」且「容器可能跨轮存活」时才否决：轮内新建、出口不用、也不被
        # 外存的容器（b3a 形态）与内容同轮生灭，插桩收益保住。
        for pc in body:
            i = instrs[pc]
            if i.op != CALL_BUILTIN or _nm(i) not in CONTAINER_MUTATORS:
                continue
            cargs = i.args or ()
            if len(cargs) < 2:
                continue
            cslot = cargs[0]
            # 容器逃逸面：区域外来的（体内无写）必然跨轮；轮内新建但出口
            # 仍用（trim_all 的 nr2 形态）或被存进另一容器（链式外溢）同样
            # 跨轮。
            c_in_body = any(instrs[q].dst == cslot for q in body)
            c_escapes = (not c_in_body) or (cslot in exit_slots) or any(
                instrs[q].op == CALL_BUILTIN
                and _nm(instrs[q]) in CONTAINER_MUTATORS
                and cslot in (instrs[q].args or ())[1:]
                for q in body)
            if not c_escapes:
                continue
            if any(_body_dyed(v) for v in cargs[1:]):
                leaked = True
                break
        if leaked:
            continue

        # 否决 4（P1030 · 零分配循环）：体内不存在任何可能产出 arena 块的
        # 写（所有体内被写槽都过不了 _body_dyed）→ 回滚无事可做，PUSH/RESET
        # 是纯开销 —— 交错 A/B 实证 empty_loop 形态 7.9 vs 3.8 ns/iter
        # （+108% 纯亏）。跳过插桩，收益面（b3a）不受影响。
        written = {instrs[q].dst for q in body
                   if instrs[q].dst is not None and instrs[q].dst >= 0}
        if written and not any(_body_dyed(s) for s in written):
            continue

        # ---- 插桩 ----
        # 出体跳转（target ∉ body 且 target > j 或 target ∈ [t,j] 的出口块）
        # 一律改道尾部 trampoline：[ARENA_RESET, JMP(原target)]。不能用
        # 「指令前插 RESET」：BR 的 fallthrough 还在体内，前插会让继续路径
        # 也 pop（与回边 pop 叠加，弹掉外层 mark）。回边 JMP 单后继，
        # RESET 前插于 j；PUSH 前插于 t。
        instrs = list(instrs)
        tail_ops = []
        for pc in sorted(body):
            i = instrs[pc]
            if i.op in (BR_IF_NOT, BR_IF, BR_F, GUARD_NUM, JMP) \
                    and i.target not in body and pc != j:
                if i.op == JMP:
                    continue  # 体 JMP 目标必在体内（body 定义），防御
                tail_ops.append(i)
        for i in tail_ops:
            pos = len(instrs)
            instrs.append(LirInstr(ARENA_RESET))
            instrs.append(LirInstr(JMP, target=i.target))
            i.target = pos
        marks = {t: ARENA_PUSH, j: ARENA_RESET}
        new_instrs = []
        shift = {}
        total = len(instrs)
        for pc in range(total):
            shift[pc] = len(new_instrs)
            if pc in marks:
                new_instrs.append(LirInstr(marks[pc]))
            new_instrs.append(instrs[pc])
        for i in new_instrs:
            if i.op in (BR_IF_NOT, BR_IF, BR_F, JMP, GUARD_NUM):
                if i.target in shift:
                    i.target = shift[i.target]
                else:  # 防御：越界 target 不该出现（verify 会拦）
                    raise LowerError2('arena 插桩：跳转目标越界 %d' % i.target)
        f.instrs = new_instrs
        instrs = new_instrs
        n = len(instrs)
        inserted += 2 + len(tail_ops)
        # 下标迁移账本（原始坐标）：本步在原 t0/j0 两条指令前各插入 1 条，
        # 之后处理的候选（原始下标 ≥ 插入点）相应 +1。（尾 trampoline 追加
        # 在末尾，不影响既有下标，不入账。）
        ins_at.append(t0)
        ins_at.append(j0)
    return inserted
