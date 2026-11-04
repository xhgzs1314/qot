# -*- coding: utf-8 -*-
"""字节码 → LIR 降级器

输入：bytecode.Compiler 产出的 VMCode（主块 + 常量池里的函数块）。
输出：lir.LirProgram。

方法：对栈机字节码做确定性栈模拟。
  * PUSH_CONST/PUSH_VAR 把「槽引用」压上模拟栈（变量槽 = 字节码操作数原样透传，
    含 GLOBAL_BIT；每条运算/调用分配一个新临时槽）；
  * 运算/调用弹出槽引用、发射三地址 LIR、压回结果槽；
  * STORE_* 把栈顶槽引用搬到变量槽（MOV_V）；
  * 跳转目标 = LIR 指令下标（与字节码下标一一对应的映射表；POP/POP_STMT/NOP
    不发射指令）；
  * 在每个跳转目标处记录栈快照，回边/汇合处不一致即抛错（结构化字节码的护栏）。

CALL 的 builtin/user 区分用解释器内置表做【编译期提示】；运行期解析以名字为准、
提示为辅（Python 与 C++ 的内置表相互独立，README §21.3，标注错误时以运行时为准）。
"""
from bytecode import VMInstruction, VMCode
from lir import (
    LirFunction, LirProgram, LirInstr,
    CONST_F64, CONST_VAL, LOAD_F64, STORE_F64, MOV_V, MOV_F,
    ADD_F, SUB_F, MUL_F, DIV_F, MOD_F, POW_F, CMP_F, NEG_F,
    ARITH_GEN, UNARY_GEN, BR_IF_NOT, BR_IF, BR_F, JMP, RET_V, RET_F,
    CALL_USER, CALL_BUILTIN, CALL_MODULE, CALL_DYNAMIC,
    INDEX_GET, INDEX_SET, MAKE_ARRAY, MAKE_DICT, REQUIRE, STOP, NOP,
    OP_ADD, OP_SUB, OP_MUL, OP_DIV, OP_MOD, OP_POW,
    OP_GT, OP_LT, OP_GE, OP_LE, OP_EQ, OP_NE, OP_AND, OP_OR,
    OP_UMINUS, OP_NOT, GLOBAL_BIT,
    THROW, TRY_PUSH, TRY_POP, ENV_NEW, MAKE_CLOSURE, THROWVAL, UPVAL_BIT,
    FIELD_GET, FIELD_SET, CALL_OMETHOD,
)

NULL_MAGIC = VMInstruction.NULL_MAGIC

# BINARY_OP 子码 → LIR f64 快速节点（仅当两侧形态为 F64 时由推断期改写）
BINOP_TO_F = {
    OP_ADD: ADD_F, OP_SUB: SUB_F, OP_MUL: MUL_F,
    OP_DIV: DIV_F, OP_MOD: MOD_F, OP_POW: POW_F,
}

# 一元子码
UNOP_UMINUS = 1
UNOP_NOT = 2


class LowerError(Exception):
    pass


def _builtin_names():
    from builtin_func import BuiltFunc
    return set(BuiltFunc().functions.keys())


def _aot_builtin_names():
    """AOT 支持集内的内置名（lir_cgen.SUPPORTED_BUILTINS 的键）。

    G-5：Python 内置表与 C++ 内置表相互独立——`type` 等在 C++ VM 表里有、
    Python 表里没有。CALL 分类若只看 Python 表，这类调用会被降级成 CALL_USER，
    cgen 解析不到用户函数直接编译失败（builtin_fastpath_matrix 实证）。
    【M1 处置】单一清单 `builtins.toml` 已落地：解释器侧现在登记【全部】
    语言级内置名（没有解释器实现的那些登记为明确报错的桩），所以这里的
    分类从第一步起就是对的；AOT 支持集同样由清单派生（lir_cgen）。
    本函数保留为安全网（名字命中 AOT 集却仍被判成 CALL_USER 时兜底重标）。
    """
    try:
        from lir_cgen import SUPPORTED_BUILTINS
        return set(SUPPORTED_BUILTINS.keys())
    except Exception:
        return set()


def _relabel_builtin_calls(prog):
    """把「名字在 AOT 支持集内、且不是本程序用户函数」的 CALL_USER 改标为 CALL_BUILTIN。

    为什么安全：
      * .qtc 字节码不受影响（CALL 分类只存在于 LIR/后端 A 路径）；
      * VM 运行期本来就以运行时表为准（README §21.3）：名字命中 C++ 内置表
        就走内置语义——与 CALL_BUILTIN 分类的 cgen 行为一致；
      * 用户函数遮蔽同名内置（`func type(x)`）时保持 CALL_USER 不动——
        用户函数优先是解释器的既定语义，这里绝不改变它。
    """
    extra = _aot_builtin_names()
    if not extra:
        return 0
    user_names = set()
    for f in prog.funcs.values():
        if f.name:
            user_names.add(f.name)
    n = 0
    for f in prog.all_funcs():
        nt = getattr(f, 'name_table', None) or []
        for i in f.instrs:
            if i.op != CALL_USER or not (0 <= i.func_idx < len(nt)):
                continue
            nm = nt[i.func_idx]
            if nm in extra and nm not in user_names:
                i.op = CALL_BUILTIN
                i.name_idx = i.func_idx
                n += 1
    return n


class _BlockLower(object):
    def __init__(self, code, name, kind, builtin_set):
        self.code = code                # VMCode
        self.ir = LirFunction(name, kind)
        self.builtin_set = builtin_set
        self.stack = []                 # 模拟栈：槽号（可含 GLOBAL_BIT）或 ('f', fslot)
        self.snapshots = {}             # 字节码 pc -> tuple(stack)（结构一致性护栏）
        self.b2l = {}                   # 字节码 pc -> LIR pc
        self.n_base = len(code.var_names)  # 变量槽基线；临时槽从此起
        self._tmp = self.n_base

    def alloc_tmp(self):
        s = self._tmp
        self._tmp += 1
        if s >= self.ir.n_slots_v:
            self.ir.n_slots_v = s + 1
        return s

    # ---- 模拟栈操作 ----
    def push(self, slot):
        self.stack.append(slot)

    def pop(self):
        if not self.stack:
            raise LowerError('栈下溢：字节码与栈模型不一致')
        return self.stack.pop()

    # ---- 指令翻译 ----
    def lower(self):
        code = self.code
        for pc, (op, arg) in enumerate(code.instructions):
            self.b2l[pc] = len(self.ir.instrs)
            # 回边/汇合一致性检查：目标 pc 已有快照且此处是「顺序到达」
            if pc in self.snapshots and pc != 0:
                snap = self.snapshots[pc]
                if snap is not None and tuple(self.stack) != snap:
                    raise LowerError(
                        'pc%d 汇合点栈不一致: %r != %r（非结构化字节码）'
                        % (pc, self.stack, list(snap)))
                self.snapshots[pc] = tuple(self.stack)
            self._one(op, arg, pc)
        # 末尾兜底：函数块必须有 RET；主块用 STOP 结束
        if not self.ir.instrs or self.ir.instrs[-1].op not in (RET_V, RET_F, STOP):
            if self.ir.kind == 'user':
                t = self.alloc_tmp()
                self.ir.emit(LirInstr(CONST_VAL, dst=t, a=-1))
                self.ir.emit(LirInstr(RET_V, a=t))
            else:
                self.ir.emit(LirInstr(STOP))
        return self.ir

    def _jump_target_lir(self, target_pc):
        # 目标 pc 的 LIR 下标；若目标在被省略的指令上，b2l 已指向压缩后位置。
        # 但 b2l 在顺序翻译时才逐条登记——向前跳（回边）目标一定已登记；
        # 向后跳（前跳）目标可能还没翻译到 → 需要回填机制。
        if target_pc in self.b2l:
            return self.b2l[target_pc]
        # 未登记：登记「待回填」列表
        self._pending.append((len(self.ir.instrs), target_pc))
        return -1

    _pending = None

    def _one(self, op, arg, pc):
        ir = self.ir
        I = VMInstruction

        if op == I.NOP or op == I.POP or op == I.POP_STMT:
            if op == I.POP or op == I.POP_STMT:
                if self.stack:
                    self.stack.pop()
            return

        if op == I.PUSH_CONST:
            dst = self.alloc_tmp()
            if arg == NULL_MAGIC:
                ir.emit(LirInstr(CONST_VAL, dst=dst, a=-1))
            elif arg is None:
                ir.emit(LirInstr(CONST_VAL, dst=dst, a=-1))
            elif isinstance(arg, int) and 0 <= arg < code_const_len(self.code):
                ir.emit(LirInstr(CONST_VAL, dst=dst, a=arg))
            else:
                # arg 是字面值（_compile_node 对非索引常量直接 push 值的情况）
                idx = code_intern_const(self.code, arg)
                ir.emit(LirInstr(CONST_VAL, dst=dst, a=idx))
            self.push(dst)
            return

        if op == I.PUSH_VAR:
            self.push(arg)  # 变量槽原样透传（含 GLOBAL_BIT）
            return

        if op == I.STORE_VAR or op == I.STORE_CONST:
            v = self.pop()
            ir.emit(LirInstr(MOV_V, dst=arg, a=v))
            return

        if op == I.BINARY_OP:
            b = self.pop()
            a = self.pop()
            dst = self.alloc_tmp()
            ir.emit(LirInstr(ARITH_GEN, dst=dst, a=a, b=b, pred=arg))
            self.push(dst)
            return

        if op == I.UNARY_OP:
            a = self.pop()
            dst = self.alloc_tmp()
            ir.emit(LirInstr(UNARY_GEN, dst=dst, a=a, pred=arg))
            self.push(dst)
            return

        if op == I.JUMP:
            t = self._jump_target(arg)
            ir.emit(LirInstr(JMP, target=t))
            return

        if op == I.JUMP_IF_FALSE or op == I.JUMP_IF_TRUE:
            c = self.pop()
            t = self._jump_target(arg)
            ir.emit(LirInstr(BR_IF_NOT if op == I.JUMP_IF_FALSE else BR_IF,
                             a=c, target=t))
            return

        if op == I.CALL:
            func_idx, argc = arg
            args = [self.pop() for _ in range(argc)]
            args.reverse()
            dst = self.alloc_tmp()
            name = self.code.func_names[func_idx] if 0 <= func_idx < len(self.code.func_names) else ''
            if name in self.builtin_set:
                ir.emit(LirInstr(CALL_BUILTIN, dst=dst, name_idx=func_idx,
                                 argc=argc, args=args))
            else:
                ir.emit(LirInstr(CALL_USER, dst=dst, func_idx=func_idx,
                                 argc=argc, args=args))
            self.push(dst)
            return

        if op == I.CALL_STATIC:
            func_idx, argc = arg
            args = [self.pop() for _ in range(argc)]
            args.reverse()
            dst = self.alloc_tmp()
            ir.emit(LirInstr(CALL_MODULE, dst=dst, name_idx=func_idx,
                             argc=argc, args=args))
            self.push(dst)
            return

        if op == I.CALL_METHOD:
            raise LowerError('CALL_METHOD(31) 在现编译器中不发射')

        if op == I.RETURN:
            if self.stack:
                v = self.pop()
            else:
                # 防御：RETURN 前无值（编译器保证不会发生），按返回 null 处理
                v = self.alloc_tmp()
                ir.emit(LirInstr(CONST_VAL, dst=v, a=-1))
            ir.emit(LirInstr(RET_V, a=v))
            return

        if op == I.MAKE_ARRAY:
            args = [self.pop() for _ in range(arg)]
            args.reverse()
            dst = self.alloc_tmp()
            ir.emit(LirInstr(MAKE_ARRAY, dst=dst, argc=arg, args=args))
            self.push(dst)
            return

        if op == I.MAKE_DICT:
            n = arg
            kv = [self.pop() for _ in range(2 * n)]
            kv.reverse()
            dst = self.alloc_tmp()
            ir.emit(LirInstr(MAKE_DICT, dst=dst, argc=n, args=kv))
            self.push(dst)
            return

        if op == I.INDEX_GET:
            idx = self.pop()
            container = self.pop()
            dst = self.alloc_tmp()
            ir.emit(LirInstr(INDEX_GET, dst=dst, a=container, b=idx))
            self.push(dst)
            return

        if op == I.INDEX_SET:
            # 字节码栈序与 pop 序（与 VM case 43 逐位一致）：栈 [value, container, index]
            #   例：a[0]=99 → PUSH 99; PUSH a; PUSH 0; INDEX_SET
            #   pop：index ← 栈顶(0)，container ← (a)，value ← (99)
            # VM 的 case 43 会把 value 压回栈且编译器不补 POP（每语句栈净 +1），
            # 但语法禁止表达式消费下标赋值（cases/syntax 负例"返回值下标"），
            # 该值永不被读——LIR 按语句级语义不压栈，可观察行为等价。
            index = self.pop()
            container = self.pop()
            value = self.pop()
            ir.emit(LirInstr(INDEX_SET, a=container, b=index, c=value))
            return

        if op == I.STOP:
            ir.emit(LirInstr(STOP))
            return

        # ---- F1/F2：闭包与异常 ----
        if op == I.ENV_NEW:
            dst, ncaps = arg
            ir.emit(LirInstr(ENV_NEW, dst=dst, a=ncaps))
            return

        if op == I.MAKE_CLOSURE:
            const_idx, env_slot = arg
            fc = self.code.constants[const_idx] \
                if 0 <= const_idx < len(self.code.constants) else None
            fname = ''
            if fc is not None and getattr(fc, 'func_names', None):
                fname = fc.func_names[0]
            fidx = self.code.intern_func(fname)
            dst = self.alloc_tmp()
            ir.emit(LirInstr(MAKE_CLOSURE, dst=dst, func_idx=fidx,
                             a=(env_slot if env_slot is not None else -1)))
            self.push(dst)
            return

        if op == I.CALL_VALUE:
            callee_slot, argc = arg
            args = [self.pop() for _ in range(argc)]
            args.reverse()
            dst = self.alloc_tmp()
            ir.emit(LirInstr(CALL_DYNAMIC, dst=dst, a=callee_slot,
                             argc=argc, args=args))
            self.push(dst)
            return

        if op == I.TRY_PUSH:
            t = self._jump_target(arg)
            ir.emit(LirInstr(TRY_PUSH, target=t))
            return

        if op == I.TRY_POP:
            ir.emit(LirInstr(TRY_POP))
            return

        if op == I.CATCH_BIND:
            ir.emit(LirInstr(THROWVAL, dst=arg))
            return

        if op == I.THROW:
            v = self.pop()
            ir.emit(LirInstr(THROW, a=v))
            return

        if op == I.BREAK:
            t = self._jump_target(arg)
            ir.emit(LirInstr(JMP, target=t))
            return

        # ---- M4 最小对象模型（0）：字段与方法分派 ----
        if op == I.FIELD_GET:
            obj = self.pop()
            dst = self.alloc_tmp()
            ir.emit(LirInstr(FIELD_GET, dst=dst, a=obj, pred=arg))
            self.push(dst)
            return

        if op == I.FIELD_SET:
            # 栈序（与 INDEX_SET 同族）：[value, obj] → 弹 obj、value
            obj = self.pop()
            value = self.pop()
            ir.emit(LirInstr(FIELD_SET, a=obj, c=value, pred=arg))
            return

        if op == I.CALL_OMETHOD:
            func_idx, argc, mid = arg
            args = [self.pop() for _ in range(argc)]
            args.reverse()
            dst = self.alloc_tmp()
            ir.emit(LirInstr(CALL_OMETHOD, dst=dst, name_idx=func_idx,
                             pred=mid, argc=argc, args=args))
            self.push(dst)
            return

        if op == I.REQUIRE:
            lib = arg if isinstance(arg, str) else ''
            if lib not in self.ir.req_names:
                self.ir.req_names.append(lib)
            sidx = self.ir.req_names.index(lib)
            dst = self.alloc_tmp()
            ir.emit(LirInstr(REQUIRE, dst=dst, str_idx=sidx))
            self.push(dst)
            return

        raise LowerError('未知字节码 op=%d arg=%r' % (op, arg))

    def _jump_target(self, target_pc):
        """登记跳转；返回 LIR 下标，未翻译到则挂起待回填（返回 -1 占位）。"""
        if target_pc in self.b2l:
            return self.b2l[target_pc]
        if self._pending is None:
            self._pending = []
        self._pending.append((len(self.ir.instrs), target_pc))
        # 目标快照登记：目标处栈 = 当前栈（跳转点与目标点栈一致是结构化保证）
        self.snapshots.setdefault(target_pc, tuple(self.stack))
        return -1

    def resolve_pending(self):
        if not self._pending:
            return
        for lir_pc, target_pc in self._pending:
            if target_pc not in self.b2l:
                raise LowerError('跳转目标 pc%d 未落到任何指令（pc 越界？）' % target_pc)
            self.ir.instrs[lir_pc].target = self.b2l[target_pc]
        self._pending = []


def code_const_len(code):
    return len(code.constants)


def code_intern_const(code, value):
    idx = code.intern_const(value)
    return idx


def lower_vmcode(code, builtin_set=None, prog=None):
    """把一个 VMCode 块降级为 LirFunction（含递归降级常量池中的函数块）。"""
    if builtin_set is None:
        builtin_set = _builtin_names()
    if prog is None:
        prog = LirProgram()

    is_main = not code.is_function
    name = ''
    if not is_main and code.func_names:
        name = code.func_names[0]
    bl = _BlockLower(code, name, 'main' if is_main else 'user', builtin_set)
    bl._pending = []
    bl.lower()
    bl.resolve_pending()
    # 绑定语义锚点：min(argc, varNames.size())——与字节码解释器逐位一致
    bl.ir.n_bind_slots = len(code.var_names)
    # 形参槽 [0, n_bind_slots) 属于 n_slots_v 编号空间：纯透传函数（body 无临时）
    # 的 n_slots_v 必须 ≥ n_bind_slots，否则 cgen 引用未声明的 s0（0
    # S-02 矩阵 bound_guard_two_state_call 实证）
    if bl.ir.n_slots_v < bl.ir.n_bind_slots:
        bl.ir.n_slots_v = bl.ir.n_bind_slots
    # 名字表：CALL_USER/CALL_BUILTIN 的 idx 解析（内联/F64 推断也要用）
    bl.ir.name_table = list(code.func_names)
    # F1 元数据：捕获名与 env 下标（cgen 的 prologue env 分配 + 形参拷入用）
    if getattr(code, 'captured', None):
        bl.ir.cap_names = list(code.captured)
        bl.ir.cap_env_idx = dict(getattr(code, 'cap_env_idx', {}) or {})
    # 【M4】方法函数的 5 参 clo 形态标记（cgen 签名分派）与主块类元数据
    if getattr(code, 'clo_form', False):
        bl.ir.clo_form = True
    if is_main and getattr(code, 'objmeta', None):
        prog.objmeta = code.objmeta
    # 块相对常量池（lower_program 会统一为全局池）
    if not hasattr(prog, '_block_pool'):
        prog._block_pool = {}
    prog._block_pool[bl.ir] = list(code.constants)
    if is_main:
        prog.main = bl.ir
    else:
        prog.funcs.setdefault(_slot_of(code, prog), bl.ir)

    # 递归：常量池里的函数块
    for ci, c in enumerate(code.constants):
        if isinstance(c, VMCode):
            lower_vmcode(c, builtin_set, prog)
    return prog


def _slot_of(code, prog):
    """用户函数块的 key：用其 funcNames[0] 在块表中的稳定编号。

    VMCode 没有显式 id；用 (name, id(code)) 会导致反序列化后无法对应。
    这里以「首次出现的顺序」编号（与常量池遍历顺序一致，序列化也按此序）。
    """
    return len(prog.funcs)


def _const_key(v):
    """常量池去重键。VMCode 用 identity（不做值语义合并）。"""
    if isinstance(v, bool):
        return ('b', v)
    if isinstance(v, (int, float)):
        return ('n', float(v))
    if isinstance(v, str):
        return ('s', v)
    if isinstance(v, VMCode):
        return ('c', id(v))
    return ('o', repr(type(v)), str(v))


def _unify_const_pool(prog):
    """把各块的块相对常量池统一为程序级全局池，并重写 CONST_VAL 下标。

    背景：字节码的常量下标是「块相对」的（每块自己的 constants）。内联跨块
    复制与 AOT 单池解释都要求全局唯一下标，否则函数内字面量会命中主块池的
    错位常量（t11 的 add(x,1) 变 add(x,3) 的根因）。VM 解释器不受影响
    （它按块查各自的池），本统一只影响 LIR→C 路径，值语义不变。
    """
    pool = list(prog._block_pool.get(prog.main) or [])
    index = {}
    for i, v in enumerate(pool):
        index.setdefault(_const_key(v), i)
    for lf in list(prog.funcs.values()):
        blk = prog._block_pool.get(lf)
        if blk is None:
            continue
        for ins in lf.instrs:
            if ins.op == CONST_VAL and isinstance(ins.a, int) and 0 <= ins.a < len(blk):
                k = _const_key(blk[ins.a])
                if k not in index:
                    index[k] = len(pool)
                    pool.append(blk[ins.a])
                ins.a = index[k]
    prog.const_pool = pool


def lower_program(code):
    """入口：主块 VMCode → LirProgram。"""
    prog = lower_vmcode(code)
    _unify_const_pool(prog)  # 全局常量池（AOT/内联的块无关性基础）
    _relabel_builtin_calls(prog)  # G-5：两内置表的漂移补偿（见函数注释）
    errs = prog.verify()
    if errs:
        raise LowerError('LIR 结构自检失败: %s' % '; '.join(errs[:5]))
    return prog
