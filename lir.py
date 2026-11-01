# -*- coding: utf-8 -*-
"""QOT LIR（Low-level IR）——  7.4 机器码 P2 / §6.4。

设计铁律（§6 原则二）：
  * LIR 是固定的低级指令集，积木/后端对应 LIR 节点，不对应表层语法。
    新语法特性 → 脱糖进已有 LIR 节点（字节码层已做），后端零改动。
  * 语义真相源仍是解释器 handler：LIR 的 ARITH_GEN / INDEX_GET / CALL_BUILTIN ...
    在执行时调用与字节码解释器完全相同的语义函数（vm_semantics.*）。
  * 槽位编号与字节码操作数完全兼容：bit31 = GLOBAL_BIT（0x40000000），
    低 31 位为槽号。局部变量与临时共用编号空间（临时从 varNames.size() 起）。
    F64 槽独立编号（f0..fN）。

LIR v1 指令表（32 条，固定后不变；号段冻结，新增只能追加）：
  常量   CONST_F64(0) CONST_VAL(1)
  搬运   LOAD_F64(2=guard unbox) STORE_F64(3=box) MOV_V(4) MOV_F(5)
  f64算术 ADD_F(6) SUB_F(7) MUL_F(8) DIV_F(9) MOD_F(10) POW_F(11) CMP_F(12) NEG_F(13)
  通用   ARITH_GEN(14) UNARY_GEN(15)
  控制   BR_IF_NOT(16) BR_IF(17) BR_F(18) JMP(19) RET_V(20) RET_F(21)
  调用   CALL_USER(22) CALL_BUILTIN(23) CALL_MODULE(24) CALL_DYNAMIC(25)
  容器   INDEX_GET(26) INDEX_SET(27) MAKE_ARRAY(28) MAKE_DICT(29)
  其它   REQUIRE(30) STOP(31) NOP(32)
  去装箱 GUARD_NUM(33)（N-02 追加：槽形态守卫，见 §10）

GUARD_NUM（33）：循环版本化的形态守卫。args = 一组 v 槽；运行期若**全部**为
VT_NUM 则顺序落下（进入去装箱快循环），否则跳到 target（原通用循环）。
它不改变任何槽的值，是纯控制流节点；语义上等价于「一次性的多槽标签合取检查」，
失败路径执行的是未经特化的原指令流，因此永不可能改变可观察行为。

与 SPEC §6.4 初版词汇的差异（§10 修订记录）：
  * PHI 未纳入 v1：lowering 产出可变槽位 LIR，循环承载值用 STORE 表达；
    待引入 SSA 化优化 pass 时随 LIR v2 加入。
  * STR_INDEX 折叠进 INDEX_GET（解释器语义本就按容器类型分派）。
  * CALL_STATIC 拆为 CALL_USER（直接调用，索引化主战场）与 CALL_MODULE（opcode 33
    的模块字典分派语义）。
"""
import struct

LIR_MAGIC = b'QLIR'
LIR_VERSION = 1

GLOBAL_BIT = 0x40000000  # 与 bytecode.VMInstruction 侧一致（T-12 守卫）

# ---- 指令号（冻结） ----
CONST_F64 = 0
CONST_VAL = 1
LOAD_F64 = 2
STORE_F64 = 3
MOV_V = 4
MOV_F = 5
ADD_F = 6
SUB_F = 7
MUL_F = 8
DIV_F = 9
MOD_F = 10
POW_F = 11
CMP_F = 12
NEG_F = 13
ARITH_GEN = 14
UNARY_GEN = 15
BR_IF_NOT = 16
BR_IF = 17
BR_F = 18
JMP = 19
RET_V = 20
RET_F = 21
CALL_USER = 22
CALL_BUILTIN = 23
CALL_MODULE = 24
CALL_DYNAMIC = 25
INDEX_GET = 26
INDEX_SET = 27
MAKE_ARRAY = 28
MAKE_DICT = 29
REQUIRE = 30
STOP = 31
NOP = 32
GUARD_NUM = 33   # N-02 追加（号段冻结 → 只能追加，不得插入/重编号）
# M-PERF 收尾二：循环 arena 回收插桩（仅后端 A 内部，永不进 VM opcode 面）。
# ARENA_PUSH 在循环头快照池 bump 指针；ARENA_RESET 在回边/出体跳转前回滚。
# 语义只影响回收（arena-free 账面转平面），不改变任何可观察行为；
# 插桩由 lir_opt.arena_loops 的逃逸判定守门，QOT_NO_ARENA 可整体关闭。
ARENA_PUSH = 34
ARENA_RESET = 35
# ---- F1/F2（0）：闭包与异常（号段只追加，永不重编号）----
# upval 槽编码（bit29 | level<<20 | idx）与 bytecode.py 逐位一致；upval 槽在
# 各 pass 中按「非全局、非 SSA 可合并」的保守语义处理（见 lir_opt/lir_ssa 护栏）。
UPVAL_BIT = 0x20000000
THROW = 36        # a=值槽：抛出（longjmp 到最近着陆点；无着陆点=未捕获 [2001]）
TRY_PUSH = 37     # target=catch LIR 下标：进入 try 区（setjmp 着陆点入栈）
TRY_POP = 38      # 正常路径摘着陆点
ENV_NEW = 39      # dst=env值槽, a=捕获数：分配激活环境（QEnv）
MAKE_CLOSURE = 40 # dst=闭包值槽, func_idx=name_table 下标, a=env值槽|-1
THROWVAL = 41     # dst=槽：取「当前被抛值」（catch 区入口专用）
# ---- M4 最小对象模型（0）：对象字段与方法分派（号段只追加）----
FIELD_GET = 42     # dst=值槽, a=对象槽, pred=fid：读字段（缺席/非对象=null）
FIELD_SET = 43     # a=对象槽, c=值槽, pred=fid：写字段（缺席/非对象=忽略）
CALL_OMETHOD = 44  # dst=值槽, name_idx=方法名表下标(退路发射), pred=mid,
                   # args=[receiver, 实参...], argc=len(args)：对象方法分派；
                   # receiver 为 VT_OBJ → qotrt_types[tid].mfns[mid] 指针直调，
                   # 否则退回「receiver 前置」的内置/用户函数调用定价。

INSTR_NAMES = {
    CONST_F64: 'CONST_F64', CONST_VAL: 'CONST_VAL', LOAD_F64: 'LOAD_F64',
    STORE_F64: 'STORE_F64', MOV_V: 'MOV_V', MOV_F: 'MOV_F', ADD_F: 'ADD_F',
    SUB_F: 'SUB_F', MUL_F: 'MUL_F', DIV_F: 'DIV_F', MOD_F: 'MOD_F',
    POW_F: 'POW_F', CMP_F: 'CMP_F', NEG_F: 'NEG_F', ARITH_GEN: 'ARITH_GEN',
    UNARY_GEN: 'UNARY_GEN', BR_IF_NOT: 'BR_IF_NOT', BR_IF: 'BR_IF',
    BR_F: 'BR_F', JMP: 'JMP', RET_V: 'RET_V', RET_F: 'RET_F',
    CALL_USER: 'CALL_USER', CALL_BUILTIN: 'CALL_BUILTIN',
    CALL_MODULE: 'CALL_MODULE', CALL_DYNAMIC: 'CALL_DYNAMIC',
    INDEX_GET: 'INDEX_GET', INDEX_SET: 'INDEX_SET', MAKE_ARRAY: 'MAKE_ARRAY',
    MAKE_DICT: 'MAKE_DICT', REQUIRE: 'REQUIRE', STOP: 'STOP', NOP: 'NOP',
    GUARD_NUM: 'GUARD_NUM', ARENA_PUSH: 'ARENA_PUSH', ARENA_RESET: 'ARENA_RESET',
    THROW: 'THROW', TRY_PUSH: 'TRY_PUSH', TRY_POP: 'TRY_POP',
    ENV_NEW: 'ENV_NEW', MAKE_CLOSURE: 'MAKE_CLOSURE', THROWVAL: 'THROWVAL',
    FIELD_GET: 'FIELD_GET', FIELD_SET: 'FIELD_SET', CALL_OMETHOD: 'CALL_OMETHOD',
}

# 语义 op 常量：与字节码 BINARY_OP/UNARY_OP 的子码完全一致（bytecode.Compiler._binop_map）
OP_ADD, OP_SUB, OP_MUL, OP_DIV, OP_MOD, OP_POW = 1, 2, 3, 4, 5, 6
OP_GT, OP_LT, OP_GE, OP_LE, OP_EQ, OP_NE = 7, 8, 9, 10, 11, 12
OP_AND, OP_OR = 13, 14
OP_UMINUS, OP_NOT = 1, 2

CMP_PREDS = {OP_GT, OP_LT, OP_GE, OP_LE, OP_EQ, OP_NE}

# CALL 形参绑定语义必须与字节码解释器一致（vm_executor.cpp case 30）：
# 绑定数 = min(argc, 块的 varNames.size())——不是声明的参数个数。
# 这是被 cases/sema/bound_call_extra_args / bound_call_fewer_args 钉住的「定价行为」。


class LirInstr(object):
    """可变槽位、线性指令流。操作数为槽号（int，可含 GLOBAL_BIT）或立即数。

    每条指令用 tuple 风格的 (op, ...) 表示并允许带注释字段 dict（可选末位），
    保持可哈希性交给序列化器处理。这里用轻量类以便调试打印与单测断言。
    """
    __slots__ = ('op', 'dst', 'a', 'b', 'c', 'imm', 'pred', 'target',
                 'func_idx', 'argc', 'args', 'name_idx', 'str_idx', '_src_op',
                 '_no_inline')

    def __init__(self, op, **kw):
        self.op = op
        self.dst = kw.get('dst', -1)          # v 槽或 f 槽（按指令）
        self.a = kw.get('a', -1)
        self.b = kw.get('b', -1)
        self.c = kw.get('c', -1)
        self.imm = kw.get('imm', 0.0)         # CONST_F64
        self.pred = kw.get('pred', 0)         # CMP_F / ARITH_GEN / UNARY_GEN 子码
        self.target = kw.get('target', 0)     # 跳转目标（LIR 指令下标）
        self.func_idx = kw.get('func_idx', 0)  # CALL_USER：funcNames 下标
        self.name_idx = kw.get('name_idx', 0)  # CALL_BUILTIN/MODULE：funcNames 下标
        self.str_idx = kw.get('str_idx', 0)    # REQUIRE：req_names 下标
        self.argc = kw.get('argc', 0)
        self.args = kw.get('args') or []       # 实参槽号列表（调用/容器构造）
        self._src_op = None                    # F64 改写组回指原通用指令（cgen 兜底用）
        self._no_inline = False                # 内联展开期标记：环/递归调用点，禁止再展开

    def __repr__(self):
        return 'LirInstr(%s)' % _fmt_instr(self)


def _fmt_instr(i):
    n = INSTR_NAMES.get(i.op, str(i.op))
    if i.op == CONST_F64:
        return '%s f%d, %r' % (n, i.dst, i.imm)
    if i.op == CONST_VAL:
        return '%s v%d, const[%d]' % (n, i.dst, i.a)
    if i.op in (LOAD_F64, STORE_F64, MOV_V, MOV_F):
        return '%s %s%d, %s%d' % (n, _dom(i.op), i.dst, _srcdom(i.op), i.a)
    if ADD_F <= i.op <= POW_F:
        return '%s f%d, f%d, f%d' % (n, i.dst, i.a, i.b)
    if i.op == CMP_F:
        return '%s v%d, f%d, f%d, pred=%d' % (n, i.dst, i.a, i.b, i.pred)
    if i.op == NEG_F:
        return '%s f%d, f%d' % (n, i.dst, i.a)
    if i.op == ARITH_GEN:
        return '%s v%d, v%d, v%d, op=%d' % (n, i.dst, i.a, i.b, i.pred)
    if i.op == UNARY_GEN:
        return '%s v%d, v%d, op=%d' % (n, i.dst, i.a, i.pred)
    if i.op in (BR_IF_NOT, BR_IF):
        return '%s v%d -> L%d' % (n, i.a, i.target)
    if i.op == BR_F:
        return '%s f%d -> L%d' % (n, i.a, i.target)
    if i.op == JMP:
        return 'JMP -> L%d' % i.target
    if i.op == RET_V:
        return 'RET_V v%d' % i.a
    if i.op == RET_F:
        return 'RET_F f%d' % i.a
    if i.op in (CALL_USER, CALL_BUILTIN, CALL_MODULE):
        return '%s v%d, idx=%d, argc=%d, args=%r' % (n, i.dst, i.func_idx if i.op == CALL_USER else i.name_idx, i.argc, i.args)
    if i.op == CALL_DYNAMIC:
        return 'CALL_DYNAMIC v%d, callee=v%d, argc=%d, args=%r' % (i.dst, i.a, i.argc, i.args)
    if i.op == INDEX_GET:
        return 'INDEX_GET v%d, v%d, v%d' % (i.dst, i.a, i.b)
    if i.op == INDEX_SET:
        return 'INDEX_SET v%d, v%d, v%d' % (i.a, i.b, i.c)
    if i.op == MAKE_ARRAY:
        return 'MAKE_ARRAY v%d, argc=%d, args=%r' % (i.dst, i.argc, i.args)
    if i.op == MAKE_DICT:
        return 'MAKE_DICT v%d, count=%d, kv=%r' % (i.dst, i.argc, i.args)
    if i.op == REQUIRE:
        return 'REQUIRE v%d, req[%d]' % (i.dst, i.str_idx)
    if i.op == THROW:
        return 'THROW v%d' % i.a
    if i.op == TRY_PUSH:
        return 'TRY_PUSH -> L%d' % i.target
    if i.op == THROWVAL:
        return 'THROWVAL v%d' % i.dst
    if i.op == ENV_NEW:
        return 'ENV_NEW v%d, caps=%d' % (i.dst, i.a)
    if i.op == MAKE_CLOSURE:
        return 'MAKE_CLOSURE v%d, fn=idx%d, env=%s' % (
            i.dst, i.func_idx,
            ('v%d' % i.a) if i.a >= 0 else 'null')
    if i.op == FIELD_GET:
        return 'FIELD_GET v%d, v%d, fid=%d' % (i.dst, i.a, i.pred)
    if i.op == FIELD_SET:
        return 'FIELD_SET v%d, v%d, fid=%d' % (i.a, i.c, i.pred)
    if i.op == CALL_OMETHOD:
        return 'CALL_OMETHOD v%d, mid=%d, argc=%d, args=%r' % (
            i.dst, i.pred, i.argc, i.args)
    if i.op == GUARD_NUM:
        return 'GUARD_NUM %r -> L%d' % ([('g%d' % (s & ~GLOBAL_BIT)) if (s & GLOBAL_BIT)
                                         else ('v%d' % s) for s in i.args], i.target)
    if i.op == STOP:
        return 'STOP'
    return n


def _dom(op):
    """指令 dst 所在的域：LOAD_F64/MOV_F 写 f 槽，其余写 v 槽。"""
    return 'f' if op in (LOAD_F64, MOV_F) else 'v'


def _srcdom(op):
    """指令 a 操作数所在的域：MOV_F/STORE_F64 读 f 槽，其余读 v 槽。"""
    return 'f' if op in (MOV_F, STORE_F64) else 'v'


class LirFunction(object):
    """一个 VMCode（主块或用户函数）对应的 LIR 函数。

    n_slots_v：VAL 槽总数（局部变量 + 临时；全局槽不占本函数槽数组，
               用 GLOBAL_BIT 区分，槽号语义与字节码操作数一致）。
    n_slots_f：F64 槽总数。
    instrs：   线性指令流；跳转 target 为本列表下标。
    req_names：REQUIRE 引用的库名表。
    param_bind：绑定语义 = min(argc, n_slots_v)（与字节码解释器一致）。
    """

    def __init__(self, name='', kind='main'):
        self.name = name
        self.kind = kind              # 'main' | 'user'
        self.n_slots_v = 0
        self.n_slots_f = 0
        # T-44 语义关键：形参绑定数 = min(argc, n_bind_slots)。
        # n_bind_slots = 字节码 varNames 数（参数+具名局部，不含临时），
        # 与 vm_executor case 30 的 funcCode->varNames.size() 逐位一致。
        # （n_slots_v 含临时槽，不能用于绑定判定——bound_call_* 用例钉住该行为。）
        self.n_bind_slots = 0
        self.instrs = []
        self.req_names = []
        # 形态标注（T-44）：'v'=VAL / 'f'=F64。f64_guarded_slots 记录入口守卫。
        self.slot_forms = {}          # v槽号 -> 'v' | 'f'（仅局部/临时有意义）
        self.entry_guards = []        # [(v槽, f槽)] 函数入口 LOAD_F64 守卫（参数 UNK→F64 收敛）

    def alloc_v(self):
        s = self.n_slots_v
        self.n_slots_v += 1
        return s

    def alloc_f(self):
        s = self.n_slots_f
        self.n_slots_f += 1
        return s

    def emit(self, instr):
        self.instrs.append(instr)
        return len(self.instrs) - 1

    def patch_target(self, at, target):
        self.instrs[at].target = target

    def verify(self):
        """结构自检：跳转目标界内、调用 argc 与实参数一致、槽号界内。返回错误串列表。"""
        errs = []
        n = len(self.instrs)
        for pc, i in enumerate(self.instrs):
            if i.op in (BR_IF_NOT, BR_IF, BR_F, JMP, GUARD_NUM, TRY_PUSH):
                if not (0 <= i.target < n):
                    errs.append('pc%d: %s 目标越界 L%d' % (pc, INSTR_NAMES[i.op], i.target))
            if i.op == GUARD_NUM and not i.args:
                errs.append('pc%d: GUARD_NUM 槽列表为空' % pc)
            if i.op in (CALL_USER, CALL_BUILTIN, CALL_MODULE, CALL_DYNAMIC,
                        MAKE_ARRAY, CALL_OMETHOD):
                if len(i.args) != i.argc:
                    errs.append('pc%d: %s argc=%d 但 args=%d 个'
                                % (pc, INSTR_NAMES[i.op], i.argc, len(i.args)))
            if i.op == MAKE_DICT and len(i.args) != 2 * i.argc:
                errs.append('pc%d: MAKE_DICT count=%d 但 kv=%d 个'
                            % (pc, i.argc, len(i.args)))
        return errs


class LirProgram(object):
    """整个 .qtc 的 LIR：main + 用户函数（按 CodeBlock 常量下标关联）。"""

    def __init__(self):
        self.main = LirFunction('', 'main')
        self.funcs = {}   # const_idx -> LirFunction
        self.aggressive = False

    def all_funcs(self):
        out = [self.main]
        for _, f in sorted(self.funcs.items()):
            out.append(f)
        return out

    def verify(self):
        errs = []
        for f in self.all_funcs():
            for e in f.verify():
                errs.append('%s: %s' % (f.name or '<main>', e))
        return errs

    def dump(self):
        lines = []
        for f in self.all_funcs():
            lines.append('=== %s (%s)  slotsV=%d slotsF=%d ==='
                         % (f.name or '<main>', f.kind, f.n_slots_v, self.nf_of(f)))
            for pc, i in enumerate(f.instrs):
                lines.append('%4d: %s' % (pc, i))
        return '\n'.join(lines)

    @staticmethod
    def nf_of(f):
        return f.n_slots_f

    # ------------------------------------------------------------ 序列化
    def serialize(self):
        out = bytearray()
        out += LIR_MAGIC
        out += struct.pack('<H', LIR_VERSION)
        funcs = self.all_funcs()
        out += struct.pack('<I', len(funcs))
        for f in funcs:
            out += struct.pack('<B', 0 if f.kind == 'main' else 1)
            nb = (f.name or '').encode('utf-8')
            out += struct.pack('<H', len(nb)) + nb
            out += struct.pack('<HHH', f.n_slots_v, f.n_slots_f, f.n_bind_slots)
            rn = _pack_string_list(f.req_names)
            out += struct.pack('<I', len(rn)) + rn
            out += struct.pack('<I', len(f.instrs))
            for i in f.instrs:
                out += _pack_instr(i)
        return bytes(out)

    @staticmethod
    def deserialize(data):
        if len(data) < 10 or data[:4] != LIR_MAGIC:
            raise ValueError('LIR: bad magic')
        ver = struct.unpack_from('<H', data, 4)[0]
        if ver != LIR_VERSION:
            raise ValueError('LIR: unsupported version %d (support %d)' % (ver, LIR_VERSION))
        off = 6
        (count,) = struct.unpack_from('<I', data, off)
        off += 4
        prog = LirProgram()
        for k in range(count):
            kind = data[off]
            off += 1
            (nlen,) = struct.unpack_from('<H', data, off)
            off += 2
            name = data[off:off + nlen].decode('utf-8')
            off += nlen
            nsv, nsf, nbs = struct.unpack_from('<HHH', data, off)
            off += 6
            (rn_len,) = struct.unpack_from('<I', data, off)
            off += 4
            req_names = _unpack_string_list(data[off:off + rn_len])
            off += rn_len
            (nins,) = struct.unpack_from('<I', data, off)
            off += 4
            f = LirFunction(name, 'main' if kind == 0 else 'user')
            f.n_slots_v = nsv
            f.n_slots_f = nsf
            f.n_bind_slots = nbs
            f.req_names = req_names
            for _ in range(nins):
                i, off = _unpack_instr(data, off)
                f.instrs.append(i)
            if k == 0:
                prog.main = f
            else:
                prog.funcs[k - 1] = f
        prog._end_offset = off
        return prog


# ------------------------------------------------------------ 编码细节
# v 槽号 = u32（可含 GLOBAL_BIT）；f 槽号 = u16；名字/串下标 = u32；argc = u16。
def _u32(b, v):
    b += struct.pack('<I', v & 0xFFFFFFFF)


def _u16(b, v):
    b += struct.pack('<H', v & 0xFFFF)


def _rd_u32(data, off):
    return struct.unpack_from('<I', data, off)[0], off + 4


def _rd_u16(data, off):
    return struct.unpack_from('<H', data, off)[0], off + 2


def _pack_instr(i):
    b = bytearray()
    b.append(i.op)
    if i.op == CONST_F64:
        _u16(b, i.dst)
        b += struct.pack('<d', i.imm)
    elif i.op == CONST_VAL:
        _u32(b, i.dst)
        _u32(b, i.a)
    elif i.op == LOAD_F64:
        _u16(b, i.dst)
        _u32(b, i.a)
    elif i.op == STORE_F64:
        _u32(b, i.dst)
        _u16(b, i.a)
    elif i.op == MOV_V:
        _u32(b, i.dst)
        _u32(b, i.a)
    elif i.op == MOV_F:
        _u16(b, i.dst)
        _u16(b, i.a)
    elif ADD_F <= i.op <= POW_F:
        _u16(b, i.dst)
        _u16(b, i.a)
        _u16(b, i.b)
    elif i.op == CMP_F:
        _u32(b, i.dst)
        _u16(b, i.a)
        _u16(b, i.b)
        b.append(i.pred)
    elif i.op == NEG_F:
        _u16(b, i.dst)
        _u16(b, i.a)
    elif i.op == ARITH_GEN:
        _u32(b, i.dst)
        _u32(b, i.a)
        _u32(b, i.b)
        b.append(i.pred)
    elif i.op == UNARY_GEN:
        _u32(b, i.dst)
        _u32(b, i.a)
        b.append(i.pred)
    elif i.op in (BR_IF_NOT, BR_IF):
        _u32(b, i.a)
        _u32(b, i.target)
    elif i.op == BR_F:
        _u16(b, i.a)
        _u32(b, i.target)
    elif i.op == JMP:
        _u32(b, i.target)
    elif i.op == RET_V:
        _u32(b, i.a)
    elif i.op == RET_F:
        _u16(b, i.a)
    elif i.op in (CALL_USER, CALL_BUILTIN, CALL_MODULE):
        idx = i.func_idx if i.op == CALL_USER else i.name_idx
        _u32(b, i.dst)
        _u32(b, idx)
        _u16(b, i.argc)
        for a in i.args:
            _u32(b, a)
    elif i.op == CALL_DYNAMIC:
        _u32(b, i.dst)
        _u32(b, i.a)
        _u16(b, i.argc)
        for a in i.args:
            _u32(b, a)
    elif i.op == INDEX_GET:
        _u32(b, i.dst)
        _u32(b, i.a)
        _u32(b, i.b)
    elif i.op == INDEX_SET:
        _u32(b, i.a)
        _u32(b, i.b)
        _u32(b, i.c)
    elif i.op == MAKE_ARRAY:
        _u32(b, i.dst)
        _u16(b, i.argc)
        for a in i.args:
            _u32(b, a)
    elif i.op == MAKE_DICT:
        _u32(b, i.dst)
        _u16(b, i.argc)
        for a in i.args:
            _u32(b, a)
    elif i.op == REQUIRE:
        _u32(b, i.dst)
        _u32(b, i.str_idx)
    elif i.op == GUARD_NUM:
        _u32(b, i.target)
        _u16(b, len(i.args))
        for s in i.args:
            _u32(b, s)
    elif i.op == THROW:
        _u32(b, i.a)
    elif i.op == TRY_PUSH:
        _u32(b, i.target)
    elif i.op == THROWVAL:
        _u32(b, i.dst)
    elif i.op == ENV_NEW:
        _u32(b, i.dst)
        _u32(b, i.a)
    elif i.op == MAKE_CLOSURE:
        _u32(b, i.dst)
        _u32(b, i.func_idx)
        _u32(b, i.a)
    elif i.op == FIELD_GET:
        _u32(b, i.dst)
        _u32(b, i.a)
        _u32(b, i.pred)
    elif i.op == FIELD_SET:
        _u32(b, i.a)
        _u32(b, i.c)
        _u32(b, i.pred)
    elif i.op == CALL_OMETHOD:
        _u32(b, i.dst)
        _u32(b, i.name_idx)
        _u32(b, i.pred)
        _u16(b, i.argc)
        for a in i.args:
            _u32(b, a)
    elif i.op in (STOP, NOP, ARENA_PUSH, ARENA_RESET, TRY_POP):
        pass
    else:
        raise ValueError('LIR: unknown op %d' % i.op)
    return bytes(b)


def _unpack_instr(data, off):
    op = data[off]
    off += 1
    i = LirInstr(op)
    if op == CONST_F64:
        i.dst, off = _rd_u16(data, off)
        i.imm = struct.unpack_from('<d', data, off)[0]
        off += 8
    elif op == CONST_VAL:
        i.dst, off = _rd_u32(data, off)
        i.a, off = _rd_u32(data, off)
    elif op == LOAD_F64:
        i.dst, off = _rd_u16(data, off)
        i.a, off = _rd_u32(data, off)
    elif op == STORE_F64:
        i.dst, off = _rd_u32(data, off)
        i.a, off = _rd_u16(data, off)
    elif op == MOV_V:
        i.dst, off = _rd_u32(data, off)
        i.a, off = _rd_u32(data, off)
    elif op == MOV_F:
        i.dst, off = _rd_u16(data, off)
        i.a, off = _rd_u16(data, off)
    elif ADD_F <= op <= POW_F:
        i.dst, off = _rd_u16(data, off)
        i.a, off = _rd_u16(data, off)
        i.b, off = _rd_u16(data, off)
    elif op == CMP_F:
        i.dst, off = _rd_u32(data, off)
        i.a, off = _rd_u16(data, off)
        i.b, off = _rd_u16(data, off)
        i.pred = data[off]
        off += 1
    elif op == NEG_F:
        i.dst, off = _rd_u16(data, off)
        i.a, off = _rd_u16(data, off)
    elif op == ARITH_GEN:
        i.dst, off = _rd_u32(data, off)
        i.a, off = _rd_u32(data, off)
        i.b, off = _rd_u32(data, off)
        i.pred = data[off]
        off += 1
    elif op == UNARY_GEN:
        i.dst, off = _rd_u32(data, off)
        i.a, off = _rd_u32(data, off)
        i.pred = data[off]
        off += 1
    elif op in (BR_IF_NOT, BR_IF):
        i.a, off = _rd_u32(data, off)
        i.target, off = _rd_u32(data, off)
    elif op == BR_F:
        i.a, off = _rd_u16(data, off)
        i.target, off = _rd_u32(data, off)
    elif op == JMP:
        i.target, off = _rd_u32(data, off)
    elif op == RET_V:
        i.a, off = _rd_u32(data, off)
    elif op == RET_F:
        i.a, off = _rd_u16(data, off)
    elif op in (CALL_USER, CALL_BUILTIN, CALL_MODULE):
        i.dst, off = _rd_u32(data, off)
        idx, off = _rd_u32(data, off)
        i.argc, off = _rd_u16(data, off)
        if op == CALL_USER:
            i.func_idx = idx
        else:
            i.name_idx = idx
        i.args = []
        for _ in range(i.argc):
            s, off = _rd_u32(data, off)
            i.args.append(s)
    elif op == CALL_DYNAMIC:
        i.dst, off = _rd_u32(data, off)
        i.a, off = _rd_u32(data, off)
        i.argc, off = _rd_u16(data, off)
        i.args = []
        for _ in range(i.argc):
            s, off = _rd_u32(data, off)
            i.args.append(s)
    elif op == INDEX_GET:
        i.dst, off = _rd_u32(data, off)
        i.a, off = _rd_u32(data, off)
        i.b, off = _rd_u32(data, off)
    elif op == INDEX_SET:
        i.a, off = _rd_u32(data, off)
        i.b, off = _rd_u32(data, off)
        i.c, off = _rd_u32(data, off)
    elif op == MAKE_ARRAY:
        i.dst, off = _rd_u32(data, off)
        i.argc, off = _rd_u16(data, off)
        i.args = []
        for _ in range(i.argc):
            s, off = _rd_u32(data, off)
            i.args.append(s)
    elif op == MAKE_DICT:
        i.dst, off = _rd_u32(data, off)
        i.argc, off = _rd_u16(data, off)
        i.args = []
        for _ in range(2 * i.argc):  # argc = 键值对数；槽数 = 2*argc
            s, off = _rd_u32(data, off)
            i.args.append(s)
    elif op == REQUIRE:
        i.dst, off = _rd_u32(data, off)
        i.str_idx, off = _rd_u32(data, off)
    elif op == GUARD_NUM:
        i.target, off = _rd_u32(data, off)
        na, off = _rd_u16(data, off)
        i.args = []
        for _ in range(na):
            s, off = _rd_u32(data, off)
            i.args.append(s)
        i.argc = na
    elif op == THROW:
        i.a, off = _rd_u32(data, off)
    elif op == TRY_PUSH:
        i.target, off = _rd_u32(data, off)
    elif op == THROWVAL:
        i.dst, off = _rd_u32(data, off)
    elif op == ENV_NEW:
        i.dst, off = _rd_u32(data, off)
        i.a, off = _rd_u32(data, off)
    elif op == MAKE_CLOSURE:
        i.dst, off = _rd_u32(data, off)
        i.func_idx, off = _rd_u32(data, off)
        i.a, off = _rd_u32(data, off)
    elif op == FIELD_GET:
        i.dst, off = _rd_u32(data, off)
        i.a, off = _rd_u32(data, off)
        i.pred, off = _rd_u32(data, off)
    elif op == FIELD_SET:
        i.a, off = _rd_u32(data, off)
        i.c, off = _rd_u32(data, off)
        i.pred, off = _rd_u32(data, off)
    elif op == CALL_OMETHOD:
        i.dst, off = _rd_u32(data, off)
        i.name_idx, off = _rd_u32(data, off)
        i.pred, off = _rd_u32(data, off)
        i.argc, off = _rd_u16(data, off)
        for _k in range(i.argc):
            s, off = _rd_u32(data, off)
            i.args.append(s)
    elif op in (STOP, NOP, ARENA_PUSH, ARENA_RESET, TRY_POP):
        pass
    else:
        raise ValueError('LIR: unknown op %d' % op)
    return i, off


def _pack_string_list(lst):
    b = bytearray()
    b += struct.pack('<I', len(lst))
    for s in lst:
        sb = s.encode('utf-8')
        b += struct.pack('<I', len(sb)) + sb
    return bytes(b)


def _unpack_string_list(data):
    if len(data) < 4:
        return []
    (n,) = struct.unpack_from('<I', data, 0)
    off = 4
    out = []
    for _ in range(n):
        (ln,) = struct.unpack_from('<I', data, off)
        off += 4
        out.append(data[off:off + ln].decode('utf-8'))
        off += ln
    return out
