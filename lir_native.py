# -*- coding: utf-8 -*-
"""
lir_native.py
================================================================================



实现形态：发射 x86-64 **AT&T 汇编文本**（MinGW GAS 语法），g++ -c 汇编成
COFF。指令编码/重定位/.pdata+.xdata（.seh_* 伪指令）全部由 GNU as 承担 ——
发射器只关心语义；发射 + 汇编总耗时 ~百毫秒级（对照 g++ C 后端 50s+）
"""

import os
import struct
import subprocess
import sys

from lir import (GLOBAL_BIT, UPVAL_BIT,
                 CONST_F64, CONST_VAL, LOAD_F64, STORE_F64, MOV_V, MOV_F,
                 ADD_F, SUB_F, MUL_F, DIV_F, MOD_F, POW_F, CMP_F, NEG_F,
                 ARITH_GEN, UNARY_GEN, BR_IF_NOT, BR_IF, BR_F, JMP,
                 RET_V, RET_F, CALL_USER, CALL_BUILTIN, CALL_MODULE,
                 CALL_DYNAMIC, INDEX_GET, INDEX_SET, MAKE_ARRAY, MAKE_DICT,
                 REQUIRE, STOP, NOP, GUARD_NUM, ARENA_PUSH, ARENA_RESET,
                 THROW, TRY_PUSH, TRY_POP, ENV_NEW, MAKE_CLOSURE, THROWVAL,
                 FIELD_GET, FIELD_SET, CALL_OMETHOD, GUARD_TYPE)
import qot_builtins as _qb
from lir_cgen import BUILTIN_CORE1 as _BUILTIN_CORE1   # N-05 快通道名表（共享）

SUPPORTED_BUILTINS = _qb.aot_supported()


class NativeError(Exception):
    pass


def _sanitize(name):
    import re as _re
    s = _re.sub(r'[^A-Za-z0-9_]', '_', name or 'fn')
    if not s or s[0].isdigit():
        s = '_' + s
    return s


def _is_upval(slot):
    return slot >= 0 and (slot & UPVAL_BIT) != 0


def _upval_levels(slot):
    return (slot & ~UPVAL_BIT) >> 20


def _upval_idx(slot):
    return slot & 0xFFFFF


def _bits_of_double(x):
    return struct.unpack('<Q', struct.pack('<d', float(x)))[0]


def _r16(v):
    return (v + 15) & ~15


VT_NONE, VT_NULL, VT_BOOL, VT_NUM, VT_STR = 0, 1, 2, 3, 4
VT_ARR, VT_DICT, VT_CLOSURE, VT_ERR, VT_ENV, VT_OBJ = 5, 6, 7, 8, 9, 10


class FrameLayout(object):
    """帧布局（rsp 相对静态偏移；帧大小 16 对齐，call 点 rsp%16==0）：
      [0,32)      shadow space（被调可写）
      [32,40)     第 5 参槽（env）
      [40, T)     call 站临时区（args 数组 / sret 缓冲 / landing 结构）
      [T, F)      f 槽 nf*8
      [F, V)      v 槽 nv*16
      [V, ..)     状态槽：argp/argc/depth/env0/tk_lim/ldbase/ret
    """

    def __init__(self, n_slots_v, n_slots_f, temp_size):
        self.arg5 = 32
        self.temp_base = 40
        self.temp_size = temp_size
        self.f_base = self.temp_base + _r16(temp_size)
        self.f_size = n_slots_f * 8
        self.v_base = self.f_base + _r16(self.f_size)
        self.v_size = n_slots_v * 16
        s = self.v_base + _r16(self.v_size)
        self.argp = s
        self.argc = s + 8
        self.depth = s + 12
        self.envp = s + 16     # 传入的词法父环境（第 5 参；ENV_NEW 的 parent）
        self.env0 = s + 24     # 本函数激活环境（ENV_NEW 分配后写入）
        self.tk_lim = s + 32
        self.ldbase = s + 40
        self.retp = s + 48
        self.total = _r16(s + 56)
        # Win64：函数入口 rsp%16==8（返回地址已压）—— prolog 必须再 sub 掉
        # ≡8 (mod 16) 的量，call 点才回到 rsp%16==0（movaps 对齐 + ABI 纪律）。
        self.sub_amt = self.total + 8

    def voff(self, slot):
        return self.v_base + slot * 16

    def foff(self, slot):
        return self.f_base + slot * 8


class NativeCg(object):
    def __init__(self, prog, fast_snaps=None):
        self.prog = prog
        self.consts = list(getattr(prog, 'const_pool', []) or [])
        self.fn_by_name = {}
        for f in prog.funcs.values():
            if f.name:
                self.fn_by_name[f.name] = f
        # ---- v2：cpp 后端发射资产的对齐（N-05 快通道 + 独占追加融合）----
        # 资格分析复用 lir_cgen.Cg 的【追加资格分析/配对守卫/跳转目标】——
        # 单一真相源（G-2 同律）：两后端资格口径漂移 = 语义漂移温床。
        # Cg(prog) 仅做字段装配（无发射副作用）， emission 全在 generate()。
        import lir_cgen as _lcg
        self._ana = _lcg.Cg(prog)
        self.do_append = self._ana.do_append   # QOT_NO_APPEND=1 关（A/B 同款）
        # FE4：f 域寄存器分配（QOT_NO_FRA=1 关，A/B 同款）
        self.do_fra = os.environ.get('QOT_NO_FRA') != '1'
        # N-04 双体快速入口（cpp FastPlanner 复用；QOT_NO_FCALL=1 关）。
        # 构造即完成 arities 收集 + 不动点求解（无独立 plan 步骤）。
        self.fast = None
        if fast_snaps and os.environ.get('QOT_NO_FCALL') != '1':
            self.fast = _lcg.FastPlanner(prog, fast_snaps)
        self._nfast = 0
        self._nfunc_fid = 0
        # 闭包表（与 cgen.plan_closures 同序：按名排序定序）
        names = set()
        for f in prog.all_funcs():
            if self._has_env(f):
                names.add(f.name)
            nt = getattr(f, 'name_table', None) or []
            for i in f.instrs:
                if i.op == MAKE_CLOSURE and 0 <= i.func_idx < len(nt):
                    names.add(nt[i.func_idx])
        ordered = sorted(n for n in names if n)
        self.clo_index = {nm: k for k, nm in enumerate(ordered)}
        # 全局槽上界（qot_g 数组尺寸）
        self._g_upper = 0
        for f in prog.all_funcs():
            for i in f.instrs:
                for s in self._slots_of(i):
                    if isinstance(s, int) and s >= 0 and (s & GLOBAL_BIT):
                        self._g_upper = max(self._g_upper, (s & ~GLOBAL_BIT) + 1)
        self.lines = []
        self._lab = 0
        self.rdata = []          # (label, bytes, align)

    # ---------------------------------------------------------------- 公共
    @staticmethod
    def _has_env(f):
        return any(i.op == ENV_NEW for i in f.instrs) or \
            bool(getattr(f, 'cap_names', None)) or \
            getattr(f, 'clo_form', False)

    @staticmethod
    def _slots_of(i):
        out = []
        if i.op in (GUARD_NUM, GUARD_TYPE):
            out += list(i.args or [])
        for s in (i.dst, i.a, i.b, i.c):
            if isinstance(s, int) and s >= 0:
                out.append(s)
        for s in (i.args or []):
            out.append(s)
        return out

    def lab(self, tag):
        self._lab += 1
        return '.L%s%d' % (tag, self._lab)

    def e(self, s=''):
        self.lines.append(s)

    def e2(self, s, *args):
        """带参数的行（模板内字面 % 必须 %% 双写）。"""
        self.lines.append(s % args)

    def rdata_q(self, data, align=1):
        lid = self.lab('RD')
        self.rdata.append((lid, bytes(data), align))
        return lid

    # ================================================================ 汇编发射
    def generate(self):
        e = self.e
        e2 = self.e2
        e('# Generated by lir_native.py (QOTNative backend) — DO NOT EDIT')
        e('\t.text')
        self.sym_g = None
        if self._g_upper:
            self.sym_g = self.lab('GARR')
            e('\t.bss')
            e('\t.align 16')
            e2('%s:', self.sym_g)
            e2('\t.zero %d', self._g_upper * 16)
            e('\t.text')
        for f in self.prog.funcs.values():
            self.emit_function(f, is_main=False)
        self.emit_function(self.prog.main, is_main=True)
        self.emit_fast_funcs()
        self.emit_main_crt()
        self.emit_init_consts()
        self.emit_tables()
        self.emit_rdata()
        return '\n'.join(self.lines) + '\n'

    # ---- 槽操作数 ----
    def _slot_mem(self, slot, off=0):
        return '%d(%%rsp)' % (self.lay.voff(slot) + off)

    def _f_mem(self, slot):
        return '%d(%%rsp)' % self.lay.foff(slot)

    # ---- FE4：f 域寄存器分配（f 槽 → callee-saved xmm 常驻）----
    # Win64 ABI：xmm6-15 非易变（被调方保存）——qotrt_* / fmod / pow / 用户
    # 函数全遵循，f 值可跨 call 存活，无需任何 spill 插桩。
    # 【硬边界】qot_sjmp/qot_ljmp 只保存 GPR（rbx/r12-r15）不保存 xmm →
    # try 函数（可能被 landing 恢复执行）一律不参与 RA（emit_function 侧排除）。
    _FRA_REGS = ('%xmm6', '%xmm7', '%xmm8', '%xmm9', '%xmm10', '%xmm11',
                 '%xmm12', '%xmm13', '%xmm14', '%xmm15')

    def _plan_f_ra(self, f, instrs):
        """f 槽线性扫描 RA。返回 {slot: '%xmmN'}；未分配槽维持栈模型。

        位置纪律：每个 f 槽全函数**单一位置**（寄存器或栈）——所有 def 都写
        同一处 → 任意汇合点该槽的值必在该位置，CFG 安全由构造保证。
        活跃区间：块级回向数据流（touch 区间在回边下不健全——「def@50、
        use@30 跨回边」的槽会被 [30,50] 短区间骗进共享寄存器）。逐块回扫
        标记 live∩touch 点，取 [min, max] span 作线性扫描区间（span 不相
        交 ⇒ 无同 pc 双活 ⇒ 共享健全；span 是精确活跃集的保守超集）。
        use-before-def（首触为 use 且无更早 def 路径 ⇒ 入口活跃）的槽回落
        栈槽（prologue 清零 0.0 语义）。确定性：全序 (start, end, slot)
        + 固定寄存器池序，无 id() 依赖（K1 asm 逐字节纪律）。
        """
        nf = getattr(f, 'n_slots_f', 0) or 0
        if nf <= 0:
            return {}
        n = len(instrs)
        # ---- 块划分 + 块级 use/def ----
        blocks, bid_of, succs = self._ana._cfg_blocks(f)
        nb = len(blocks)
        b_use = [set() for _ in range(nb)]
        b_def = [set() for _ in range(nb)]
        for bi in range(nb):
            st, en = blocks[bi]
            for p in range(st, en):
                d, u = self._f_du(instrs[p])
                for s in u:
                    if s not in b_def[bi]:
                        b_use[bi].add(s)
                for s in d:
                    b_def[bi].add(s)
        b_succ = []
        for bi in range(nb):
            p = blocks[bi][1] - 1
            tb = set()
            for t in succs.get(p, ()):
                if 0 <= t < n:
                    tb.add(bid_of[t])
            b_succ.append(tb)
        # ---- 回向活跃数据流 ----
        live_in = [set() for _ in range(nb)]
        live_out = [set() for _ in range(nb)]
        changed = True
        while changed:
            changed = False
            for bi in range(nb - 1, -1, -1):
                lo = set()
                for t in b_succ[bi]:
                    lo |= live_in[t]
                li = b_use[bi] | (lo - b_def[bi])
                if live_out[bi] != lo or live_in[bi] != li:
                    live_out[bi] = lo
                    live_in[bi] = li
                    changed = True
        # ---- 逐块回扫标记活跃 touch 点 → span ----
        entry_live = live_in[0] if blocks else set()
        spans = {}    # slot -> [min_pc, max_pc]
        for bi in range(nb):
            st, en = blocks[bi]
            live = set(live_out[bi])
            for p in range(en - 1, st - 1, -1):
                d, u = self._f_du(instrs[p])
                # 标记 = live-out ∪ use ∪ def（live-through 槽同样占位）
                touched = live | set(u) | set(d)
                live = (live | set(u)) - set(d)
                for s in touched:
                    if 0 <= s < nf and not self._is_ref_slot(s):
                        sp = spans.get(s)
                        if sp is None:
                            spans[s] = [p, p]
                        else:
                            if p < sp[0]:
                                sp[0] = p
                            if p > sp[1]:
                                sp[1] = p
        # ---- 候选过滤 + 线性扫描 ----
        cands = []
        for s, sp in spans.items():
            if s in entry_live:
                continue   # 入口活跃 = 存在 use-before-def 路径 → 栈（0.0 语义）
            cands.append((sp[0], sp[1], s))
        if not cands:
            return {}, {}
        cands.sort()
        free = list(range(len(self._FRA_REGS)))
        active = []   # [end, reg_idx]
        reg_of = {}
        for start, end, s in cands:
            still = []
            for ae, ar in active:
                if ae < start:
                    free.append(ar)
                else:
                    still.append([ae, ar])
            active = still
            if free:
                # 取最小空闲寄存器号（确定性）
                r = min(free)
                free.remove(r)
                active.append([end, r])
                reg_of[s] = self._FRA_REGS[r]
        # ---- caller-save：每 pc 的「跨调用仍活跃」RA 槽集 ----
        # 活跃-after(pc) ∩ 已分配 − defs(pc)（defs 的新值由 pc 自身落寄存器，
        # 调用后重载会覆盖结果）。pc 实际 emit 了 call 才生效（发射期判定）。
        call_live = {}
        for bi in range(nb):
            st, en = blocks[bi]
            live = set(live_out[bi])
            for p in range(en - 1, st - 1, -1):
                d, u = self._f_du(instrs[p])
                la = sorted(s for s in (live - set(d)) if s in reg_of)
                if la:
                    call_live[p] = tuple(la)
                live = (live | set(u)) - set(d)
        return reg_of, call_live

    def _f_load(self, slot, xmm):
        """f 槽 → xmm（RA 槽寄存器直取/直拷，否则栈载）。"""
        r = self.f_reg.get(slot)
        if r is None:
            self.e2('\tmovsd %s, %s', self._f_mem(slot), xmm)
        elif r != xmm:
            self.e2('\tmovsd %s, %s', r, xmm)

    def _f_store(self, xmm, slot):
        """xmm → f 槽（RA 槽落寄存器，否则落栈）。"""
        r = self.f_reg.get(slot)
        if r is None:
            self.e2('\tmovsd %s, %s', xmm, self._f_mem(slot))
        elif r != xmm:
            self.e2('\tmovsd %s, %s', xmm, r)

    @staticmethod
    def _is_ref_slot(slot):
        return _is_upval(slot) or (slot >= 0 and (slot & GLOBAL_BIT))

    def _addr_of(self, slot, reg, into_payload=False):
        """槽地址 → reg（V16*）。全局 → qot_g 基址（leaq 取数组地址——
        qot_g 是数组本体，movq (%rip) 是 load 会把首槽当指针）；upval →
        env 链寻址。"""
        e2 = self.e2
        if slot >= 0 and (slot & GLOBAL_BIT):
            e2('\tleaq %s(%%rip), %s', self.sym_g, reg)
            d = (slot & ~GLOBAL_BIT) * 16 + (8 if into_payload else 0)
            if d:
                e2('\tleaq %d(%s), %s', d, reg, reg)
            return
        if _is_upval(slot):
            self._upval_addr(slot, reg, into_payload)
            return
        d = self.lay.voff(slot) + (8 if into_payload else 0)
        if d:
            e2('\tleaq %d(%%rsp), %s', d, reg)
        else:
            e2('\tleaq (%%rsp), %s', reg)

    def _upval_addr(self, slot, reg, into_payload=False):
        """upval 槽寻址：快路径内联（U1 同构），慢路回落 qotrt_env_at。"""
        e2 = self.e2
        lv, ix = _upval_levels(slot), _upval_idx(slot)
        if self.has_env:
            e2('\tmovq %d(%%rsp), %s', self.lay.env0, reg)
        else:
            e2('\txorl %s, %s', reg, reg)
        for _ in range(lv):
            e2('\tmovq 8(%s), %s', reg, reg)          # e = e->parent
        slow = self.lab('upslow')
        done = self.lab('updone')
        e2('\ttestq %s, %s', reg, reg)
        e2('\tje %s', slow)
        e2('\tcmpl $%d, (%s)', ix, reg)               # n（int32 @0）vs ix
        e2('\tjle %s', slow)
        e2('\tleaq %d(%s), %s', 8 + ix * 16, reg, reg)
        e2('\tjmp %s', done)
        e2('%s:', slow)
        e2('\tmovq %s, %%rcx', reg)
        e2('\tmovl $%d, %%edx', ix)
        e2('\tcall qotrt_env_at')
        e2('\tmovq %%rax, %s', reg)
        e2('%s:', done)
        if into_payload:
            e2('\taddq $8, %s', reg)

    def _copy_v(self, src_slot, dst_slot):
        """16B 值拷贝（任一侧可为全局/upval 槽）。"""
        e = self.e
        e2 = self.e2
        src_ref = self._is_ref_slot(src_slot)
        dst_ref = self._is_ref_slot(dst_slot)
        if src_ref and dst_ref:
            self._addr_of(src_slot, '%r10')
            e('\tmovq (%r10), %rax')
            e('\tmovq 8(%r10), %rdx')
            self._addr_of(dst_slot, '%r11')
            e('\tmovq %rax, (%r11)')
            e('\tmovq %rdx, 8(%r11)')
            return
        if src_ref:
            self._addr_of(src_slot, '%r10')
            e('\tmovq (%r10), %rax')
            e('\tmovq 8(%r10), %rdx')
            e2('\tmovq %%rax, %s', self._slot_mem(dst_slot))
            e2('\tmovq %%rdx, %s', self._slot_mem(dst_slot, 8))
            return
        if dst_ref:
            e2('\tmovq %s, %%rax', self._slot_mem(src_slot))
            e2('\tmovq %s, %%rdx', self._slot_mem(src_slot, 8))
            self._addr_of(dst_slot, '%r11')
            e('\tmovq %rax, (%r11)')
            e('\tmovq %rdx, 8(%r11)')
            return
        e2('\tmovq %s, %%rax', self._slot_mem(src_slot))
        e2('\tmovq %s, %%rdx', self._slot_mem(src_slot, 8))
        e2('\tmovq %%rax, %s', self._slot_mem(dst_slot))
        e2('\tmovq %%rdx, %s', self._slot_mem(dst_slot, 8))

    def _store_v_from_rax_rdx(self, dst_slot):
        """rax（tag+pad）+ rdx（payload）写入槽。"""
        e = self.e
        e2 = self.e2
        if self._is_ref_slot(dst_slot):
            self._addr_of(dst_slot, '%r11')
            e('\tmovq %rax, (%r11)')
            e('\tmovq %rdx, 8(%r11)')
            return
        e2('\tmovq %%rax, %s', self._slot_mem(dst_slot))
        e2('\tmovq %%rdx, %s', self._slot_mem(dst_slot, 8))

    def _load_v_to_rax_rdx(self, src_slot):
        e = self.e
        e2 = self.e2
        if self._is_ref_slot(src_slot):
            self._addr_of(src_slot, '%r10')
            e('\tmovq (%r10), %rax')
            e('\tmovq 8(%r10), %rdx')
            return
        e2('\tmovq %s, %%rax', self._slot_mem(src_slot))
        e2('\tmovq %s, %%rdx', self._slot_mem(src_slot, 8))

    def _store_imm_v(self, dst_slot, tag, bits):
        e2 = self.e2
        if self._is_ref_slot(dst_slot):
            self._addr_of(dst_slot, '%r11')
            e2('\tmovb $%d, (%%r11)', tag)
            e2('\tmovabsq $%d, %%rax', bits)
            e2('\tmovq %%rax, 8(%%r11)')
            return
        e2('\tmovb $%d, %s', tag, self._slot_mem(dst_slot))
        e2('\tmovabsq $%d, %%rax', bits)
        e2('\tmovq %%rax, %s', self._slot_mem(dst_slot, 8))

    def _store_null(self, dst_slot):
        self._store_imm_v(dst_slot, VT_NULL, 0)

    def _cmp_tag_jmp(self, slot, tag, target_lab):
        e2 = self.e2
        if self._is_ref_slot(slot):
            self._addr_of(slot, '%r10')
            e2('\tcmpb $%d, (%%r10)', tag)
        else:
            e2('\tcmpb $%d, %s', tag, self._slot_mem(slot))
        e2('\tjne %s', target_lab)

    # ================================================================ 函数
    def emit_function(self, f, is_main=False):
        e = self.e
        e2 = self.e2
        sym = 'qot_main' if is_main else 'qot_u_%s' % _sanitize(f.name)
        self.cur_sym = sym
        self.is_main = is_main
        self.has_env = self._has_env(f) or (f.name in self.clo_index)
        self.has_try = any(i.op == TRY_PUSH for i in f.instrs)
        self.nt = getattr(f, 'name_table', None) or []
        instrs = f.instrs
        # fid 必须确定性（prog CAS 的 K1：命中产物逐字节一致）。原
        # id(f)%1000000 随对象地址漂移 → asm 文本跨进程不稳定 → obj CAS
        # 的跨进程命中沦为地址巧合。改发射序号（funcs 迭代序稳定，
        # pickle 保 dict 序 → 冷/热同序）。
        self._nfunc_fid += 1
        fid = self._nfunc_fid
        self.fid = fid

        # ---- 站区预算（发射前静态分配，emit 期间查表）----
        self.site = {}
        off = 40
        for pc, i in enumerate(instrs):
            if i.op in (CALL_USER, CALL_BUILTIN, CALL_DYNAMIC, CALL_OMETHOD):
                n = max(1, len(i.args or []))
                self.site[('args', pc)] = off
                off += _r16(n * 16)
                self.site[('ret', pc)] = off
                off += 16
            elif i.op in (MAKE_ARRAY, MAKE_DICT):
                self.site[('ret', pc)] = off
                off += 16
            elif i.op == TRY_PUSH:
                self.site[('ld', pc)] = off
                off += _r16(80)
        nv = max(f.n_slots_v, f.n_bind_slots)
        # FE4：f-RA 规划（try 函数排除——qot_sjmp/ljmp 不保存 xmm6-15，
        # landing 恢复执行会拿到陈旧寄存器；has_try 函数 f 槽维持全栈模型）
        if self.do_fra and not self.has_try:
            self.f_reg, self._fra_call_live = self._plan_f_ra(f, instrs)
        else:
            self.f_reg, self._fra_call_live = {}, {}
        self.lay = FrameLayout(nv, f.n_slots_f, off - 40)
        lay = self.lay

        # ---- 函数头 ----
        e('')
        e('\t.align 16')
        e2('\t.globl %s', sym)
        e2('\t.def %s; .scl 2; .type 32; .endef', sym)
        e2('%s:', sym)
        e2('\t.seh_proc %s', sym)
        if lay.total > 4096:
            e2('\tmovl $%d, %%eax', lay.sub_amt)
            # MinGW libgcc 的栈探测符号是三下划线（C 名 __chkstk_ms 前再补 _）
            e('\tcall ___chkstk_ms')
            e('\tsubq %rax, %rsp')
            e2('\t.seh_stackalloc %d', lay.sub_amt)
        else:
            e2('\tsubq $%d, %%rsp', lay.sub_amt)
            e2('\t.seh_stackalloc %d', lay.sub_amt)
        e('\t.seh_endprologue')
        # 状态槽 —— retp 必须最先存：深度护栏重载 %ecx（=rcx 低 32）会毁掉
        # 入口 ret 指针，rcx 在此之后不再可用（Win64 第 1 参 = ret 指针）。
        e2('\tmovq %%rcx, %d(%%rsp)', lay.retp)        # ret 指针
        e2('\tmovq %%rdx, %d(%%rsp)', lay.argp)        # args 指针
        e2('\tmovl %%r8d, %d(%%rsp)', lay.argc)        # argc
        if not is_main:
            e2('\tmovl %%r9d, %d(%%rsp)', lay.depth)   # depth
            l_ok = self.lab('depth_ok')
            e2('\tmovl %d(%%rsp), %%ecx', lay.depth)
            e('\tcmpl qotrt_max_depth(%rip), %ecx')
            e2('\tjle %s', l_ok)
            e2('\tmovl %d(%%rsp), %%ecx', lay.depth)
            e('\tcall qotrt_fail_depth')
            e2('%s:', l_ok)
        if self.has_env:
            # envp = 第 5 参（被调视角 [入口rsp+40]；sub 后 = [rsp+sub_amt+40]）
            e2('\tmovq %d(%%rsp), %%rax', lay.sub_amt + 40)
            e2('\tmovq %%rax, %d(%%rsp)', lay.envp)
            e('\txorl %eax, %eax')
            e2('\tmovq %%rax, %d(%%rsp)', lay.env0)
        # T1：tk_lim = qotrt_tick_limit（函数头快照）
        e('\tmovq qotrt_tick_limit(%rip), %rax')
        e2('\tmovq %%rax, %d(%%rsp)', lay.tk_lim)
        if self.has_try:
            e('\tmovq qotrt_landings(%rip), %rax')
            e2('\tmovq %%rax, %d(%%rsp)', lay.ldbase)
        # v 槽清零（16B 全零 = t=VT_NONE + payload=null）
        if nv:
            e('\txorl %eax, %eax')
            e('\tmovq %rax, %rdx')
            for s in range(nv):
                e2('\tmovq %%rax, %s', self._slot_mem(s))
                e2('\tmovq %%rdx, %s', self._slot_mem(s, 8))
        # f 槽清零
        if f.n_slots_f:
            e('\txorl %eax, %eax')
            for s in range(f.n_slots_f):
                e2('\tmovq %%rax, %s', self._f_mem(s))
        # 形参绑定：nbind = min(argc, nb)；if (nbind > k) s_k = args[k]
        nb = f.n_bind_slots
        if not is_main and nb > 0:
            for k in range(nb):
                l_skip = self.lab('bind')
                e2('\tcmpl $%d, %d(%%rsp)', k, lay.argc)
                e2('\tjle %s', l_skip)
                e2('\tmovq %d(%%rsp), %%r10', lay.argp)
                e2('\tmovq %d(%%r10), %%rax', k * 16)
                e2('\tmovq %d(%%r10), %%rdx', k * 16 + 8)
                e2('\tmovq %%rax, %s', self._slot_mem(k))
                e2('\tmovq %%rdx, %s', self._slot_mem(k, 8))
                e2('%s:', l_skip)

        # ---- 指令流（每个 pc 一个标签：跳转目标恒存在）----
        # v2：循环改步进式——追加融合的「邻接形态」一次消费 2 条指令
        # （ARITH+MOV），被消费 pc 不发标签：_is_jump_target 守卫保证
        # 没有任何跳转能落在配对内的第二条上（cpp 后端同款纪律）。
        self.try_stack = []
        self.depths = self._build_depths(instrs)
        # f 组融合的精确防御预扫：每 f 槽的读取点表（一次性 O(n)；
        # instrs 在发射期不变 → 缓存安全）。窗口内被跳过的定义是否可达
        # 窗口外读者，由 _window_defs_reach_outside 按到达定义精判。
        self._f_readers = {}
        for _pc, _i in enumerate(instrs):
            _d, _u = self._f_du(_i)
            for _s in _u:
                self._f_readers.setdefault(_s, []).append(_pc)
        pc = 0
        n_instr = len(instrs)
        while pc < n_instr:
            e2('.L%d_%d:', fid, pc)
            # FE4 caller-save 包装：本 pc 的发射先进侧缓冲；扫到真实 call
            #（noreturn 的 fail_*/throw 不算——本帧就此废弃，无恢复点）且
            # 有跨调用活跃的 RA 槽时，call 前溢出、发射后重载。活跃集由
            # liveness 精确给出（通常为空）——微函数递归零额外开销。
            body = []
            self.lines, body = body, self.lines
            # 融合优先序（cpp 同款）：独占追加 → CMP+BR（B1）→
            # ARITH(比较子码)+BR（B2）→ f 组窗口（D-11 硬化 + xmm 直通）
            # → 通用逐条。
            consumed = self._try_emit_append(f, instrs, pc) if self.do_append else 0
            if not consumed:
                consumed = self._try_emit_fused_cmp_branch(f, instrs, pc)
            if not consumed:
                consumed = self._try_emit_fused_arith_branch(f, instrs, pc)
            if not consumed:
                consumed = self._try_emit_f64_group(f, instrs, pc)
            if not consumed:
                self._emit_instr(f, instrs[pc], pc)
                consumed = 1
            self.lines, body = body, self.lines
            cl = self._fra_call_live.get(pc)
            if cl:
                has_live_call = False
                for ln in body:
                    t = ln.strip()
                    if t.startswith('call '):
                        if t[5:] not in ('qotrt_fail_timeout',
                                         'qotrt_fail_depth', 'qotrt_throw'):
                            has_live_call = True
                            break
                if has_live_call:
                    for s in cl:
                        e2('\tmovsd %s, %s', self.f_reg[s], self._f_mem(s))
                    self.lines.extend(body)
                    for s in cl:
                        e2('\tmovsd %s, %s', self._f_mem(s), self.f_reg[s])
                    pc += consumed
                    continue
            self.lines.extend(body)
            pc += consumed
        # 尾部兜底（不可达；防 fallthrough 出函数）
        if is_main:
            e2('.L%d_stop:', fid)
        self._emit_ret_null()
        e2('\taddq $%d, %%rsp', lay.sub_amt)
        e('\tret')
        e('\t.seh_endproc')

    def _emit_ret_null(self):
        e = self.e
        e2 = self.e2
        if self.has_try:
            self._restore_landings()
        e('\txorl %eax, %eax')
        e('\tmovq %rax, %rdx')
        e2('\tmovq %d(%%rsp), %%rcx', self.lay.retp)
        e('\tmovq %rax, (%rcx)')
        e('\tmovq %rdx, 8(%rcx)')

    @staticmethod
    def _build_depths(instrs):
        depths = {}
        d = 0
        for p in range(len(instrs) + 1):
            depths[p] = d
            if p < len(instrs):
                if instrs[p].op == TRY_PUSH:
                    d += 1
                elif instrs[p].op == TRY_POP:
                    d -= 1
        return depths

    def _need_unwind_jump(self, i, pc):
        """跳转跨越 try 边界 → 摘除途中着陆点（F2 正确性关键）。"""
        e = self.e
        e2 = self.e2
        if not self.try_stack:
            return
        cur = self.depths.get(pc, 0)
        tgt = self.depths.get(i.target, 0)
        if tgt >= cur:
            return
        for push_pc in reversed(self.try_stack[tgt:cur]):
            ld = self.site[('ld', push_pc)]
            e2('\tmovq %d(%%rsp), %%rax', ld + 64)
            e('\tmovq %rax, qotrt_landings(%rip)')

    # ---------------------------------------------------------- v2 独占追加
    def _try_emit_append(self, f, ins, pc):
        """`s = s + X` 独占追加融合（cpp 后端 _try_append_pattern 的 asm 同构）。

        两种形态（与 cpp 后端逐条同判据，资格分析复用 lir_cgen.Cg 的
        【追加资格分析】—— 单一真相源）：
          直写:  ARITH(1, dst==a==S, b)               → ipappend(&S, &b)，吃 1 条
          邻接:  ARITH(1, dst=D, a==S|b==S), MOV(S,D) → 同上，吃 2 条
        返回消费的指令数（0 = 不资格，走通用 qotrt_arith 路径）。
        语义：ipappend 内部与 qotrt_arith op1 逐字节一致（shared 闸兜底）；
        NUM+NUM 在发射侧内联裸加（资格分析无类型信息，数字累加器同样命中，
        调用退化的 1.4× 是 cpp 后端 p04 实测教训 —— 同款双路径保下来）。
        """
        i = ins[pc]
        if i.op != ARITH_GEN or i.pred != 1:
            return 0
        ana = self._ana
        # 位置纪律（cpp 同款）：acc 必须在左侧（拼接不可交换，`s = x + s`
        # 结果以 x 开头，原地追加无法表达 → 不资格）。
        if i.dst == i.a:
            S, rhs, n = i.dst, i.b, 1
        else:
            # 邻接形态守卫（cpp 同款逐条）：
            if pc + 1 >= len(ins):
                return 0
            j = ins[pc + 1]
            if j.op != MOV_V or j.a != i.dst:
                return 0
            S = j.dst
            if i.a != S and i.b != S:
                return 0
            D = i.dst
            if D < 0 or (D & GLOBAL_BIT) or _is_upval(D) \
                    or S < 0 or (S & GLOBAL_BIT) or _is_upval(S):
                return 0
            # 被移除的定义（pc 处 ARITH 写 D）不得可达配对外任何 D 读取；
            # 配对第二条不得是跳转目标（无标签可落）。
            if ana._def_removed_reaches_read(f, D, pc, (pc, pc + 1)):
                return 0
            if ana._is_jump_target(f, pc + 1):
                return 0
            rhs = i.b if i.a == S else i.a
            n = 2
        if S < 0 or (S & GLOBAL_BIT) or _is_upval(S):
            return 0
        if S not in ana._append_eligible(f):
            return 0
        # ---- 发射：NUM+NUM 内联裸加，否则 qotrt_str_ipappend(&S, &rhs) ----
        e = self.e
        e2 = self.e2
        l_slow = self.lab('aps')
        l_done = self.lab('apd')
        e2('\tcmpb $%d, %s', VT_NUM, self._slot_mem(S))
        e2('\tjne %s', l_slow)
        if self._is_ref_slot(rhs):
            self._addr_of(rhs, '%r10')
            e2('\tcmpb $%d, (%%r10)', VT_NUM)
        else:
            e2('\tcmpb $%d, %s', VT_NUM, self._slot_mem(rhs))
        e2('\tjne %s', l_slow)
        e2('\tmovsd %s, %%xmm0', self._slot_mem(S, 8))
        if self._is_ref_slot(rhs):
            e('\taddsd 8(%r10), %xmm0')
        else:
            e2('\taddsd %s, %%xmm0', self._slot_mem(rhs, 8))
        e2('\tmovsd %%xmm0, %s', self._slot_mem(S, 8))
        e2('\tjmp %s', l_done)
        e2('%s:', l_slow)
        e2('\tleaq %s, %%rcx', self._slot_mem(S))
        self._addr_of(rhs, '%rdx')
        e('\tcall qotrt_str_ipappend')
        e2('%s:', l_done)
        return n

    # ---------------------------------------------------------- 回边 tick（T1 同构）
    def _emit_backedge_tick(self, i, pc):
        """回边（target ≤ pc）超时守卫，cpp 后端 _emit_backedge_tick 的 asm
        同构：lim < 0（未设超时）→ 只剩一次完美预测的比较；超限 →
        qotrt_fail_timeout（exit 124，与 cpp/interp 同口径）。
        native v1 缺此守卫（死循环永挂）——随 B1/B2 融合一并补齐。"""
        if i.target > pc:
            return
        e = self.e
        e2 = self.e2
        lay = self.lay
        l_ok = self.lab('tkok')
        e2('\tcmpq $0, %d(%%rsp)', lay.tk_lim)
        e2('\tjl %s', l_ok)
        e('\taddq $1, qotrt_ticks(%rip)')
        e('\tmovq qotrt_ticks(%rip), %rax')
        e2('\tcmpq %d(%%rsp), %%rax', lay.tk_lim)
        e2('\tjle %s', l_ok)
        # rcx = lim / 200000（除数放 r11 —— cqto 会把符号扩展写进 rdx，
        # 除数放 rdx 的写法在 lim>0 时被冲成高半 0 → idivq 除零，实测踩中）
        e2('\tmovq %d(%%rsp), %%rcx', lay.tk_lim)
        e('\tmovq %rcx, %rax')
        e('\tmovabsq $200000, %r11')
        e('\tcqto')
        e('\tidivq %r11')
        e('\tmovq %rax, %rcx')
        e('\tcall qotrt_fail_timeout')
        e2('%s:', l_ok)

    # ---------------------------------------------------------- v2 比较+分支融合
    def _fused_br_jcc(self, pred, negate, target):
        """比较真值 → 条件跳转（B1/B2 共用 jcc 表）。

        口径 = _emit_fcmp 的 bool 落地 + truthy 分支的精确等价：
        true  ⇔ ucomisd 判 pred 成立（== 须 jp 排 NaN；!= 含 unordered）；
        negate（BR_IF_NOT）= 取反跳转。NaN 语义与 C++ 逐位一致。"""
        e = self.e
        e2 = self.e2
        if pred == 7:                    # a > b：dst=a，ja / 取反 jbe
            e('\tucomisd %xmm1, %xmm0')
            e2('\t%s %s', 'jbe' if negate else 'ja', target)
        elif pred == 8:                  # a < b：dst=b
            e('\tucomisd %xmm0, %xmm1')
            e2('\t%s %s', 'jbe' if negate else 'ja', target)
        elif pred == 9:                  # a >= b：dst=a，jae / 取反 jb
            e('\tucomisd %xmm1, %xmm0')
            e2('\t%s %s', 'jb' if negate else 'jae', target)
        elif pred == 10:                 # a <= b：dst=b
            e('\tucomisd %xmm0, %xmm1')
            e2('\t%s %s', 'jb' if negate else 'jae', target)
        elif pred == 11:                 # ==
            if negate:                   # != 或 unordered → 跳
                e('\tucomisd %xmm1, %xmm0')
                e2('\tjp %s', target)
                e2('\tjne %s', target)
            else:                        # 有序且相等 → 跳
                l_f = self.lab('cmpb')
                e('\tucomisd %xmm1, %xmm0')
                e2('\tjp %s', l_f)
                e2('\tje %s', target)
                e2('%s:', l_f)
        else:                            # 12 !=（含 unordered）
            if negate:                   # 有序相等 → 跳
                l_f = self.lab('cmpb')
                e('\tucomisd %xmm1, %xmm0')
                e2('\tjp %s', l_f)
                e2('\tje %s', target)
                e2('%s:', l_f)
            else:
                e('\tucomisd %xmm1, %xmm0')
                e2('\tjp %s', target)
                e2('\tjne %s', target)

    def _load_v_payload(self, src, xmm):
        """v 槽 payload → xmm（B2 快臂；src 可为全局/upval）。"""
        e = self.e
        e2 = self.e2
        if self._is_ref_slot(src):
            self._addr_of(src, '%r10')
            e2('\tmovsd 8(%%r10), %s', xmm)
        else:
            e2('\tmovsd %s, %s', self._slot_mem(src, 8), xmm)

    def _try_emit_fused_cmp_branch(self, f, ins, pc):
        """【PROMAX-B1】CMP_F + 紧邻 BR 融合（cpp _try_fused_cmp_branch 同构）。

        命中：cmp dst 的唯一消费者 = 紧邻分支（_cmp_fusable 到达定义判据，
        复用 lir_cgen.Cg 分析器）。快路径只剩 ucomisd + jcc，省掉
        bool 装箱 + truthy 调用 —— 循环头从 ~10 条降到 2 条。吃 2 条。
        """
        if pc + 1 >= len(ins):
            return 0
        cmp_i = ins[pc]
        if cmp_i.op != CMP_F:
            return 0
        br = ins[pc + 1]
        if br.op not in (BR_IF_NOT, BR_IF):
            return 0
        vd = cmp_i.dst
        if br.a != vd or vd < 0 or (vd & GLOBAL_BIT) or _is_upval(vd):
            return 0
        if not self._ana._cmp_fusable(f, pc, vd):
            return 0
        if not (7 <= int(cmp_i.pred) <= 12):
            return 0
        self._emit_backedge_tick(br, pc + 1)
        self._need_unwind_jump(br, pc + 1)
        e2 = self.e2
        self._f_load(cmp_i.a, '%xmm0')
        self._f_load(cmp_i.b, '%xmm1')
        self._fused_br_jcc(int(cmp_i.pred), br.op == BR_IF_NOT,
                           '.L%d_%d' % (self.fid, br.target))
        return 2

    def _try_emit_fused_arith_branch(self, f, ins, pc):
        """【PROMAX-B2】ARITH_GEN(比较子码) + 紧邻 BR 融合（cpp 同构）。

        未组化的循环条件每迭代吃「qotrt_arith 装箱 + truthy 调用」。
        快臂（双侧 NUM）：裸 double 比较直跳；慢臂：qotrt_arith 原语义 +
        truthy + 跳转（dst 写回保持语义完备）。真值 = truthy_str(bool) =
        d != 0，与快臂逐位一致。吃 2 条。
        """
        if pc + 1 >= len(ins):
            return 0
        ai = ins[pc]
        if ai.op != ARITH_GEN or not (7 <= int(ai.pred) <= 12):
            return 0
        br = ins[pc + 1]
        if br.op not in (BR_IF_NOT, BR_IF) or br.a != ai.dst:
            return 0
        vd = ai.dst
        if vd < 0 or (vd & GLOBAL_BIT) or _is_upval(vd):
            return 0
        if not self._ana._cmp_fusable(f, pc, vd):
            return 0
        e = self.e
        e2 = self.e2
        self._emit_backedge_tick(br, pc + 1)
        l_slow = self.lab('fab')
        negate = br.op == BR_IF_NOT
        tgt = '.L%d_%d' % (self.fid, br.target)
        # ---- 快臂：双侧 NUM → 裸 double 比较 ----
        for s in (ai.a, ai.b):
            if self._is_ref_slot(s):
                self._addr_of(s, '%r10')
                e2('\tcmpb $%d, (%%r10)', VT_NUM)
            else:
                e2('\tcmpb $%d, %s', VT_NUM, self._slot_mem(s))
            e2('\tjne %s', l_slow)
        self._load_v_payload(ai.a, '%xmm0')
        self._load_v_payload(ai.b, '%xmm1')
        self._fused_br_jcc(int(ai.pred), negate, tgt)
        # ---- 慢臂：qotrt_arith 原语义 → truthy → 分支 ----
        # （慢臂同样跳 tgt：跨 try 边界时同样要摘着陆点 —— 摘链只毁 rax，
        #   放在 arith 调用之后、truthy 取址之前）
        e2('%s:', l_slow)
        self._addr_of(ai.a, '%rdx')
        self._addr_of(ai.b, '%r8')
        self._addr_of(vd, '%r9')
        e2('\tmovl $%d, %%ecx', int(ai.pred))
        e('\tcall qotrt_arith')
        self._need_unwind_jump(br, pc + 1)
        self._addr_of(vd, '%rcx')
        e('\tcall qotrt_truthy_str')
        e('\ttestq %rax, %rax')
        e2('\t%s %s', 'je' if negate else 'jne', tgt)
        return 2

    # ================================================================ 指令
    def _emit_instr(self, f, i, pc):
        e = self.e
        e2 = self.e2
        lay = self.lay
        op = i.op
        tlab = '.L%d_%d' % (self.fid, i.target)

        if op == NOP:
            return

        if op == GUARD_NUM:
            for s in (i.args or []):
                self._cmp_tag_jmp(s, VT_NUM, tlab)
            return

        if op == GUARD_TYPE:
            self._cmp_tag_jmp(i.args[0], int(i.pred) & 0xFF, tlab)
            return

        if op == CONST_F64:
            r = self.f_reg.get(i.dst)
            if r is not None:
                # FE4 常量重物化：.rdata 8 字节常量 rip 相对载（免 movabs+movq）
                lid = self.rdata_q(struct.pack('<d', i.imm), align=8)
                e2('\tmovsd %s(%%rip), %s', lid, r)
                return
            e2('\tmovabsq $%d, %%rax', _bits_of_double(i.imm))
            e('\tmovq %rax, %xmm0')
            e2('\tmovsd %%xmm0, %s', self._f_mem(i.dst))
            return

        if op == CONST_VAL:
            self._emit_const_val(i)
            return

        if op == MOV_V:
            self._copy_v(i.a, i.dst)
            return

        if op == MOV_F:
            ra_, rd_ = self.f_reg.get(i.a), self.f_reg.get(i.dst)
            if ra_ is not None and rd_ is not None:
                if ra_ != rd_:
                    e2('\tmovsd %s, %s', ra_, rd_)
                return
            self._f_load(i.a, '%xmm0')
            self._f_store('%xmm0', i.dst)
            return

        if ADD_F <= op <= POW_F:
            self._emit_farith(op, i)
            return

        if op == NEG_F:
            # 真取负（xorpd 翻符号位）：0-x（subsd）在 a=+0.0 得 +0.0，
            # 与 qotrt_unary case 1 的 C 取负（-0.0）逐位分歧 —— 修复。
            e('\tmovabsq $9223372036854775808, %rax')
            e('\tmovq %rax, %xmm1')
            self._f_load(i.a, '%xmm0')
            e('\txorpd %xmm1, %xmm0')
            self._f_store('%xmm0', i.dst)
            return

        if op == CMP_F:
            self._emit_fcmp(i)
            return

        if op == LOAD_F64:
            # f = (s).t == VT_NUM ? (s).u.d : 0.0
            # v3 硬化：非 NUM 落 0.0（原形态跳过写入 → f 槽保留陈旧值，
            # 后续 f 域运算吃垃圾；0.0 = 确定性数值兜底）。
            # FE4：RA 槽直接装入目标寄存器（0.0 预装进同一寄存器）。
            src = i.a
            l_end = self.lab('ldf')
            dst_r = self.f_reg.get(i.dst)
            tgt = dst_r if dst_r is not None else '%xmm0'
            e('\txorl %eax, %eax')
            e2('\tmovq %%rax, %s', tgt)
            if self._is_ref_slot(src):
                self._addr_of(src, '%r10')
                e2('\tcmpb $%d, (%%r10)', VT_NUM)
                e2('\tjne %s', l_end)
                e2('\tmovsd 8(%%r10), %s', tgt)
            else:
                e2('\tcmpb $%d, %s', VT_NUM, self._slot_mem(src))
                e2('\tjne %s', l_end)
                e2('\tmovsd %s, %s', self._slot_mem(src, 8), tgt)
            e2('%s:', l_end)
            return

        if op == STORE_F64:
            dst = i.dst
            self._f_load(i.a, '%xmm0')
            if self._is_ref_slot(dst):
                self._addr_of(dst, '%r10')
                e2('\tmovb $%d, (%%r10)', VT_NUM)
                e('\tmovsd %xmm0, 8(%r10)')
            else:
                e2('\tmovb $%d, %s', VT_NUM, self._slot_mem(dst))
                e2('\tmovsd %%xmm0, %s', self._slot_mem(dst, 8))
            return

        if op == ARITH_GEN:
            self._addr_of(i.a, '%rdx')
            self._addr_of(i.b, '%r8')
            self._addr_of(i.dst, '%r9')
            e2('\tmovl $%d, %%ecx', int(i.pred))
            e('\tcall qotrt_arith')
            return

        if op == UNARY_GEN:
            self._addr_of(i.a, '%rdx')
            self._addr_of(i.dst, '%r8')
            e2('\tmovl $%d, %%ecx', int(i.pred))
            e('\tcall qotrt_unary')
            return

        if op in (BR_IF_NOT, BR_IF):
            self._emit_backedge_tick(i, pc)
            self._need_unwind_jump(i, pc)
            self._addr_of(i.a, '%rcx')
            e('\tcall qotrt_truthy_str')
            e('\ttestq %rax, %rax')
            e2('\t%s %s', 'je' if op == BR_IF_NOT else 'jne', tlab)
            return

        if op == BR_F:
            self._emit_backedge_tick(i, pc)
            self._need_unwind_jump(i, pc)
            e('\txorl %eax, %eax')
            e('\tmovq %rax, %xmm1')
            self._f_load(i.a, '%xmm0')
            e('\tucomisd %xmm1, %xmm0')
            # f == 0.0 → target；NaN：ZF=1 → je 跳（C++ NaN==0=false → goto ✓）
            e2('\tje %s', tlab)
            return

        if op == JMP:
            self._need_unwind_jump(i, pc)
            if i.target != pc + 1:
                self._emit_backedge_tick(i, pc)
                e2('\tjmp %s', tlab)
            return

        if op == ARENA_PUSH:
            e('\tcall qotrt_arena_push')
            return
        if op == ARENA_RESET:
            e('\tcall qotrt_arena_pop')
            return

        if op == RET_V:
            if self.has_try:
                # 先摘着陆点（_restore_landings 占用 rax），再装载返回值
                self._restore_landings()
            self._load_v_to_rax_rdx(i.a)
            e2('\tmovq %d(%%rsp), %%rcx', lay.retp)
            e('\tmovq %rax, (%rcx)')
            e('\tmovq %rdx, 8(%rcx)')
            e2('\taddq $%d, %%rsp', lay.sub_amt)
            e('\tret')
            return

        if op == RET_F:
            if self.has_try:
                self._restore_landings()
            self._f_load(i.a, '%xmm0')
            e2('\tmovq %d(%%rsp), %%rcx', lay.retp)
            e2('\tmovb $%d, (%%rcx)', VT_NUM)
            e('\tmovsd %xmm0, 8(%rcx)')
            e2('\taddq $%d, %%rsp', lay.sub_amt)
            e('\tret')
            return

        if op in (CALL_USER, CALL_BUILTIN):
            self._emit_call(i, pc)
            return

        if op == CALL_DYNAMIC:
            self._emit_call_dynamic(i, pc)
            return

        if op == INDEX_GET:
            self._addr_of(i.a, '%rcx')
            self._addr_of(i.b, '%rdx')
            self._addr_of(i.dst, '%r8')
            e('\tcall qotrt_index_get')
            return

        if op == INDEX_SET:
            self._addr_of(i.a, '%rcx')
            self._addr_of(i.b, '%rdx')
            self._addr_of(i.c, '%r8')
            e('\tcall qotrt_index_set')
            return

        if op == MAKE_ARRAY:
            ret = self.site[('ret', pc)]
            e2('\tleaq %d(%%rsp), %%rcx', ret)
            e2('\tmovl $%d, %%edx', max(4, i.argc))
            e('\tcall qotrt_arr_new')
            self._copy_v_from_site(ret, i.dst)
            for s in (i.args or []):
                # qotrt_arr_push((QArr*)dst.u.p, &s)：RCX=数组, RDX=元素槽址
                if self._is_ref_slot(i.dst):
                    self._addr_of(i.dst, '%r10', into_payload=True)
                    e('\tmovq (%r10), %rcx')
                else:
                    e2('\tmovq %s, %%rcx', self._slot_mem(i.dst, 8))
                if self._is_ref_slot(s):
                    self._addr_of(s, '%rdx')
                else:
                    e2('\tleaq %s, %%rdx', self._slot_mem(s))
                e('\tcall qotrt_arr_push')
            return

        if op == MAKE_DICT:
            self._emit_make_dict(i, pc)
            return

        if op == THROW:
            self._addr_of(i.a, '%rcx')
            e('\tcall qotrt_throw')
            return

        if op == TRY_PUSH:
            self._emit_try_push(i, pc)
            return

        if op == TRY_POP:
            if not self.try_stack:
                raise NativeError('TRY_POP 无配对的 TRY_PUSH')
            push_pc = self.try_stack.pop()
            ld = self.site[('ld', push_pc)]
            e2('\tmovq %d(%%rsp), %%rax', ld + 64)
            e('\tmovq %rax, qotrt_landings(%rip)')
            return

        if op == ENV_NEW:
            self._emit_env_new(i)
            return

        if op == MAKE_CLOSURE:
            nm = self.nt[i.func_idx] if 0 <= i.func_idx < len(self.nt) else ''
            idx = self.clo_index.get(nm)
            if idx is None:
                raise NativeError('MAKE_CLOSURE 目标 %r 不在闭包入口表中' % nm)
            # qotrt_closure_new(fn, env) sret：RCX=&ret（shadow 区暂存）,EDX,R8
            if i.a >= 0:
                self._addr_of(i.a, '%r8', into_payload=True)
                e('\tmovq (%r8), %r8')
            else:
                e('\txorl %r8d, %r8d')
            e2('\tmovl $%d, %%edx', idx)
            e('\tleaq (%rsp), %rcx')
            e('\tcall qotrt_closure_new')
            e('\tmovq (%rsp), %rax')
            e('\tmovq 8(%rsp), %rdx')
            self._store_v_from_rax_rdx(i.dst)
            return

        if op == THROWVAL:
            # dst = qotrt_thrown（全局 V16）
            e('\tmovq qotrt_thrown(%rip), %rax')
            e('\tmovq qotrt_thrown+8(%rip), %rdx')
            self._store_v_from_rax_rdx(i.dst)
            return

        if op == FIELD_GET:
            self._emit_field_get(i)
            return

        if op == FIELD_SET:
            self._emit_field_set(i)
            return

        if op == CALL_OMETHOD:
            self._emit_omethod(i, pc)
            return

        if op == STOP:
            if self.has_try:
                self._restore_landings()
            if self.is_main:
                e2('\tjmp .L%d_stop', self.fid)
            else:
                self._emit_ret_null()
                e2('\taddq $%d, %%rsp', lay.sub_amt)
                e('\tret')
            return

        if op in (REQUIRE, CALL_MODULE):
            raise NativeError('AOT（native）不支持 require/运行期模块加载')

        raise NativeError('未知 LIR op %d' % op)

    # ---------------------------------------------------------------- 小件
    def _copy_v_from_site(self, site, dst_slot):
        e2 = self.e2
        e2('\tmovq %d(%%rsp), %%rax', site)
        e2('\tmovq %d(%%rsp), %%rdx', site + 8)
        self._store_v_from_rax_rdx(dst_slot)

    def _emit_const_val(self, i):
        cv = self.consts[i.a] if 0 <= i.a < len(self.consts) else None
        if isinstance(cv, bool):
            self._store_imm_v(i.dst, VT_BOOL, _bits_of_double(1.0 if cv else 0.0))
            return
        if isinstance(cv, (int, float)):
            self._store_imm_v(i.dst, VT_NUM, _bits_of_double(cv))
            return
        if 0 <= i.a < len(self.consts):
            # 字符串/其它：16B 从常量池拷贝（PROMAX-C1 直读同构）
            self.e('\tmovq qotrt_consts(%rip), %r10')
            self.e2('\tmovq %d(%%r10), %%rax', i.a * 16)
            self.e2('\tmovq %d(%%r10), %%rdx', i.a * 16 + 8)
            self._store_v_from_rax_rdx(i.dst)
            return
        self._store_imm_v(i.dst, VT_NULL, 0)

    def _emit_farith(self, op, i):
        e = self.e
        e2 = self.e2
        self._f_load(i.a, '%xmm1')
        self._f_load(i.b, '%xmm2')
        if op == ADD_F:
            e('\taddsd %xmm2, %xmm1')
        elif op == SUB_F:
            e('\tsubsd %xmm2, %xmm1')
        elif op == MUL_F:
            e('\tmulsd %xmm2, %xmm1')
        elif op == DIV_F:
            # qotrt_fdiv：b != 0 ? a / b : 0.0（NaN b：!=0 为 true → a/b ✓）
            e('\txorl %eax, %eax')
            e('\tmovq %rax, %xmm0')
            e('\tucomisd %xmm0, %xmm2')      # b ? 0
            l_do = self.lab('fdiv')
            l_end = self.lab('fdivend')
            e2('\tjne %s', l_do)             # b != 0
            e2('\tjp %s', l_do)              # NaN（unordered）→ 保持 a/b 语义
            e('\tmovsd %xmm0, %xmm1')        # b == 0 → 0.0
            e2('\tjmp %s', l_end)
            e2('%s:', l_do)
            e('\tdivsd %xmm2, %xmm1')
            e2('%s:', l_end)
        elif op == MOD_F:
            self._call2dd('fmod', i)
            return
        elif op == POW_F:
            self._call2dd('pow', i)
            return
        self._f_store('%xmm1', i.dst)

    def _call2dd(self, fn, i):
        """double fn(double, double)：xmm0=a, xmm1=b → 返回 xmm0。"""
        e = self.e
        e2 = self.e2
        self._f_load(i.a, '%xmm0')
        self._f_load(i.b, '%xmm1')
        e2('\tcall %s', fn)
        self._f_store('%xmm0', i.dst)

    def _emit_fcmp(self, i):
        """dst = qotrt_bool(fA pred fB)；ucomisd x0,x1 反映 x0 ? x1：
        CF=1 ⇔ x0<x1 或 unordered；ZF=1 ⇔ x0==x1 或 unordered。"""
        e = self.e
        e2 = self.e2
        pred = int(i.pred)
        self._f_load(i.a, '%xmm0')
        self._f_load(i.b, '%xmm1')
        l_true = self.lab('cmpT')
        l_done = self.lab('cmpD')
        # UCOMISD 标志（SDM）：dst>src → ZF=0,PF=0,CF=0；dst<src → ZF=0,PF=0,
        # CF=1；dst==src → ZF=1,PF=0,CF=0；unordered → ZF=PF=CF=1。
        # AT&T ucomisd A,B = Intel ucomisd B,A → dst=AT&T 第二操作数。
        if pred == 7:      # a > b：dst=a，ja（CF=0,ZF=0=仅 greater；NaN 排除）
            e('\tucomisd %xmm1, %xmm0')
            e2('\tja %s', l_true)
        elif pred == 8:    # a < b：dst=b，ja → b>a → a<b
            e('\tucomisd %xmm0, %xmm1')
            e2('\tja %s', l_true)
        elif pred == 9:    # a >= b：dst=a，jae（CF=0 = greater 或 equal；NaN CF=1 排除）
            e('\tucomisd %xmm1, %xmm0')
            e2('\tjae %s', l_true)
        elif pred == 10:   # a <= b：dst=b，jae → b>=a → a<=b（NaN 排除）
            e('\tucomisd %xmm0, %xmm1')
            e2('\tjae %s', l_true)
        elif pred == 11:   # ==：有序且 ZF=1 才真（jp 先排 NaN）
            l_f = self.lab('cmpF')
            e('\tucomisd %xmm1, %xmm0')
            e2('\tjp %s', l_f)
            e2('\tje %s', l_true)
            e2('%s:', l_f)
        else:              # != ：NaN（PF=1）→ true
            e('\tucomisd %xmm1, %xmm0')
            e2('\tjp %s', l_true)
            e2('\tjne %s', l_true)
        self._store_imm_v(i.dst, VT_BOOL, _bits_of_double(0.0))
        e2('\tjmp %s', l_done)
        e2('%s:', l_true)
        self._store_imm_v(i.dst, VT_BOOL, _bits_of_double(1.0))
        e2('%s:', l_done)

    def _restore_landings(self):
        """while (qotrt_landings != ldbase) qotrt_landings = landings->prev;"""
        e = self.e
        e2 = self.e2
        l_loop = self.lab('unw')
        l_done = self.lab('unwd')
        e2('%s:', l_loop)
        e('\tmovq qotrt_landings(%rip), %rax')
        e2('\tcmpq %d(%%rsp), %%rax', self.lay.ldbase)
        e2('\tje %s', l_done)
        e('\tmovq 64(%rax), %rax')
        e('\tmovq %rax, qotrt_landings(%rip)')
        e2('\tjmp %s', l_loop)
        e2('%s:', l_done)

    def _emit_try_push(self, i, pc):
        e = self.e
        e2 = self.e2
        ld = self.site[('ld', pc)]
        self.try_stack.append(pc)
        catch = '.L%d_%d' % (self.fid, i.target)
        l_next = self.lab('trynext')
        # 着陆点入栈
        e2('\tleaq %d(%%rsp), %%rax', ld)
        e('\tmovq qotrt_landings(%rip), %rcx')
        e2('\tmovq %%rcx, %d(%%rsp)', ld + 64)
        e('\tmovq %rax, qotrt_landings(%rip)')
        # qhit = qot_sjmp(&ld)
        e2('\tleaq %d(%%rsp), %%rcx', ld)
        e('\tcall qot_sjmp')
        # 恢复路径（eax≠0）：卸链 + 进 catch；直挂路径（eax==0）：顺行
        e('\ttestl %eax, %eax')
        e2('\tje %s', l_next)
        e2('\tmovq %d(%%rsp), %%rax', ld + 64)
        e('\tmovq %rax, qotrt_landings(%rip)')
        e2('\tjmp %s', catch)
        e2('%s:', l_next)

    def _emit_env_new(self, i):
        e = self.e
        e2 = self.e2
        n_caps = int(i.a)
        # parent = envp（第 5 参传入的词法父环境；cgen 同律：envp if has_env else 0）
        if n_caps == 0:
            # qotrt_env_link0(parent)：RCX=parent（单参！）
            if self.has_env:
                e2('\tmovq %d(%%rsp), %%rcx', self.lay.envp)
            else:
                e('\txorl %ecx, %ecx')
            e('\tcall qotrt_env_link0')
        else:
            # qotrt_env_new(n, parent)：RCX=n, RDX=parent
            if self.has_env:
                e2('\tmovq %d(%%rsp), %%rdx', self.lay.envp)
            else:
                e('\txorl %edx, %edx')
            e2('\tmovl $%d, %%ecx', n_caps)
            e('\tcall qotrt_env_new')
        e('\tmovq %rax, %rdx')
        # env0 必须先写（rax 新鲜；ref 槽寻址路径会毁 rax）—— P-F1 同构
        if self.has_env:
            e2('\tmovq %%rax, %d(%%rsp)', self.lay.env0)
        # tag=VT_ENV 写 dst（rdx=payload）
        if self._is_ref_slot(i.dst):
            self._addr_of(i.dst, '%r11')
            e2('\tmovb $%d, (%%r11)', VT_ENV)
            e2('\tmovq %%rdx, 8(%%r11)')
            return
        e2('\tmovb $%d, %s', VT_ENV, self._slot_mem(i.dst))
        e2('\tmovq %%rdx, %s', self._slot_mem(i.dst, 8))

    def _fill_args_site(self, args, args_site):
        e2 = self.e2
        for k, s in enumerate(args):
            if self._is_ref_slot(s):
                self._addr_of(s, '%r10')
                e2('\tmovq (%%r10), %%rax')
                e2('\tmovq 8(%%r10), %%rdx')
            else:
                e2('\tmovq %s, %%rax', self._slot_mem(s))
                e2('\tmovq %s, %%rdx', self._slot_mem(s, 8))
            e2('\tmovq %%rax, %d(%%rsp)', args_site + k * 16)
            e2('\tmovq %%rdx, %d(%%rsp)', args_site + k * 16 + 8)

    def _emit_depth_plus1(self):
        if self.is_main:
            self.e('\tmovl $1, %r9d')
        else:
            self.e2('\tmovl %d(%%rsp), %%r9d', self.lay.depth)
            self.e('\taddl $1, %r9d')

    # ---------------------------------------------------------- v3 · f 组窗口融合
    @staticmethod
    def _f_du(i):
        """f 槽 (defs, uses)——到达定义数据流的操作数分类。"""
        op = i.op
        if op in (CONST_F64, LOAD_F64):
            return (i.dst,), ()
        if op == MOV_F:
            return (i.dst,), (i.a,)
        if ADD_F <= op <= POW_F:
            return (i.dst,), (i.a, i.b)
        if op == NEG_F:
            return (i.dst,), (i.a,)
        if op == STORE_F64:
            return (), (i.a,)
        if op == CMP_F:
            return (), (i.a, i.b)
        if op == RET_F:
            return (), (i.a,)
        return (), ()

    def _window_defs_reach_outside(self, f, ins, win_pcs, win_defs):
        """窗口内被跳过的 f 定义是否可达窗口外任何同槽读取点（精确判据）。

        版本化循环把同一 f 槽戴多顶帽子（b6b/empty_loop 实证：全函数
        use 计数判据会把无害窗口全灭）——这里按到达定义精判：对窗口
        涉及的每个槽跑一次不动点（IN/OUT 与查询点无关，批量查询），
        窗口外读者的 reach 含窗口内定义 → 拒绝融合。"""
        from lir import JMP as _JMP, BR_IF_NOT as _BRN, BR_IF as _BR, \
            BR_F as _BRF, GUARD_NUM as _GU, RET_V as _RV, RET_F as _RF, \
            STOP as _ST, THROW as _TH
        slots = [s for s, d in win_defs.items() if d]
        if not slots:
            return False
        win = set(win_pcs)
        readers = {s: [u for u in self._f_readers.get(s, ()) if u not in win]
                   for s in slots}
        if not any(readers[s] for s in slots):
            return False
        blocks, bid_of, succs = self._ana._cfg_blocks(f)
        n = len(ins)
        for slot in slots:
            dpcs = win_defs[slot]
            # 块内 gen（最后一个 def）+ 不动点
            gen = [None] * len(blocks)
            succ_b = [set() for _ in blocks]
            for bi, (st, en) in enumerate(blocks):
                for p in range(st, en):
                    d, _u = self._f_du(ins[p])
                    if slot in d:
                        gen[bi] = p
                p = en - 1
                for t in succs[p]:
                    if 0 <= t < n:
                        succ_b[bi].add(bid_of[t])
            preds = [set() for _ in blocks]
            for bi in range(len(blocks)):
                for t in succ_b[bi]:
                    preds[t].add(bi)
            IN = [set() for _ in blocks]
            OUT = [set() for _ in blocks]
            changed = True
            while changed:
                changed = False
                for bi in range(len(blocks)):
                    ni = set()
                    for q in preds[bi]:
                        ni |= OUT[q]
                    no = {gen[bi]} if gen[bi] is not None else ni
                    if IN[bi] != ni or OUT[bi] != no:
                        IN[bi] = ni
                        OUT[bi] = no
                        changed = True
            dset = set(dpcs)
            for upc in readers[slot]:
                b = bid_of[upc]
                st = blocks[b][0]
                reach = IN[b]
                for p in range(st, upc):
                    d, _u = self._f_du(ins[p])
                    if slot in d:
                        reach = {p}
                if reach & dset:
                    return True
        return False

    def _emit_f_group_operand(self, x, xmm):
        """组头操作数 → xmm（LOAD_F64 带 0.0 确定性硬化：非 NUM 落 0.0，
        不再保留陈旧槽值）。返回 False = 非组头形态。"""
        e = self.e
        e2 = self.e2
        op = x.op
        if op == LOAD_F64:
            src = x.a
            l_num = self.lab('gnum')
            l_end = self.lab('gend')
            e('\txorl %eax, %eax')
            e2('\tmovq %%rax, %s', xmm)          # 预置 0.0
            if self._is_ref_slot(src):
                self._addr_of(src, '%r10')
                e2('\tcmpb $%d, (%%r10)', VT_NUM)
                e2('\tjne %s', l_end)
                e('\tmovsd 8(%r10), ' + xmm)
            else:
                e2('\tcmpb $%d, %s', VT_NUM, self._slot_mem(src))
                e2('\tjne %s', l_end)
                e2('\tmovsd %s, %s', self._slot_mem(src, 8), xmm)
            e2('%s:', l_end)
            return True
        if op == MOV_F:
            self._f_load(x.a, xmm)
            return True
        if op == CONST_F64:
            e2('\tmovabsq $%d, %%rax', _bits_of_double(x.imm))
            e2('\tmovq %%rax, %s', xmm)
            return True
        return False

    def _try_emit_f64_group(self, f, ins, pc):
        """f 组窗口融合（v3）：[X1, X2, OP_F, STORE_F64](4) /
        [X1, X2, CMP_F](3) / [X1, NEG_F, STORE_F64](3)。

        收益 = 组私有 f 槽不落地（每算术组省 2 次 mem 往返）+ LOAD_F64
        的 0.0 确定性兜底内联（D-11 部分硬化：非数字得 0.0 而非陈旧槽
        垃圾 —— 字符串语义回退未建，实证边界 = 现版 infer_f64 不对混型
        槽发放组化（fuzz 80 程序 × 20 native 抽样零分歧）。cpp 的完整
        双路径（_src_op + qotrt_arith 回退）如未来需要，形态在此扩展）。"""
        e = self.e
        e2 = self.e2
        if os.environ.get('QOT_NO_FGROUP') == '1':
            return 0
        n = len(ins)
        if pc + 2 >= n:
            return 0
        x1, x2 = ins[pc], ins[pc + 1]
        if x1.op not in (LOAD_F64, MOV_F, CONST_F64) \
                or x2.op not in (LOAD_F64, MOV_F, CONST_F64):
            return 0
        head = ins[pc + 2]
        op = head.op

        if op in (ADD_F, SUB_F, MUL_F, DIV_F, MOD_F, POW_F) \
                and pc + 3 < n and ins[pc + 3].op == STORE_F64 \
                and x1.dst == head.a and x2.dst == head.b \
                and ins[pc + 3].a == head.dst:
            win = (pc, pc + 1, pc + 2, pc + 3)
            win_defs = {head.a: [pc], head.b: [pc + 1], head.dst: [pc + 2]}
            if self._window_defs_reach_outside(f, ins, win, win_defs):
                return 0
            # 操作数寄存器按 op 形态分派：
            #   ADD/SUB/MUL/DIV → (xmm1, xmm2)，结果 xmm1；
            #   MOD/POW → double fn(double, double) ABI：(xmm0, xmm1)，
            #     结果 xmm0。【坑】原实现沿 _emit_farith 的 (xmm1,xmm2)
            #     预装后 movsd xmm1→xmm0 直接 call —— b 停在 xmm2，
            #     fmod 收到 (a, a) → i % 2 变 i % i = 0（for-odd 实证）。
            if op in (MOD_F, POW_F):
                self._emit_f_group_operand(x1, '%xmm0')
                self._emit_f_group_operand(x2, '%xmm1')
                e2('\tcall %s', 'fmod' if op == MOD_F else 'pow')
                res = '%xmm0'
            else:
                self._emit_f_group_operand(x1, '%xmm1')
                self._emit_f_group_operand(x2, '%xmm2')
                if op in (ADD_F, SUB_F, MUL_F):
                    if op == ADD_F:
                        e('\taddsd %xmm2, %xmm1')
                    elif op == SUB_F:
                        e('\tsubsd %xmm2, %xmm1')
                    else:
                        e('\tmulsd %xmm2, %xmm1')
                    res = '%xmm1'
                else:
                    # qotrt_fdiv 口径：b != 0 ? a/b : 0.0（NaN b 走 a/b）
                    e('\txorl %eax, %eax')
                    e('\tmovq %rax, %xmm0')
                    e('\tucomisd %xmm0, %xmm2')
                    l_do = self.lab('gfdiv')
                    l_end2 = self.lab('gfdivend')
                    e2('\tjne %s', l_do)
                    e2('\tjp %s', l_do)
                    e('\tmovsd %xmm0, %xmm1')
                    e2('\tjmp %s', l_end2)
                    e2('%s:', l_do)
                    e('\tdivsd %xmm2, %xmm1')
                    e2('%s:', l_end2)
                    res = '%xmm1'
            self._emit_v_store_num(ins[pc + 3].dst, res)
            return 4

        if op == CMP_F and x1.dst == head.a and x2.dst == head.b:
            if self._window_defs_reach_outside(f, ins, (pc, pc + 1, pc + 2),
                                               {x1.dst: [pc], x2.dst: [pc + 1]}):
                return 0
            self._emit_f_group_operand(x1, '%xmm0')
            self._emit_f_group_operand(x2, '%xmm1')
            self._store_cmp_bool(int(head.pred), head.dst)
            return 3

        if op == NEG_F and pc + 2 < n and ins[pc + 2].op == STORE_F64 \
                and x1.dst == head.a and ins[pc + 2].a == head.dst:
            if self._window_defs_reach_outside(f, ins, (pc, pc + 1, pc + 2),
                                               {x1.dst: [pc], head.dst: [pc + 1]}):
                return 0
            self._emit_f_group_operand(x1, '%xmm0')
            e('\tmovabsq $9223372036854775808, %rax')
            e('\tmovq %rax, %xmm1')
            e('\txorpd %xmm1, %xmm0')
            self._emit_v_store_num(ins[pc + 2].dst, '%xmm0')
            return 3
        return 0

    def _emit_v_store_num(self, dst, res):
        """V 槽直落 NUM：tag=VT_NUM + payload=res（ref 槽走 r10）。
        res 是寄存器名（含 %x 字面）→ 预格式化行必须走 e()（不二次格式化）。"""
        e = self.e
        e2 = self.e2
        if self._is_ref_slot(dst):
            self._addr_of(dst, '%r10')
            e2('\tmovb $%d, (%%r10)', VT_NUM)
            e('\tmovsd %s, 8(%%r10)' % res)
        else:
            e2('\tmovb $%d, %s', VT_NUM, self._slot_mem(dst))
            e('\tmovsd %s, %s' % (res, self._slot_mem(dst, 8)))

    def _store_cmp_bool(self, pred, dst):
        """xmm0=a, xmm1=b 已就位 → bool V 直写（_emit_fcmp 的极性/NaN 口径）。"""
        e = self.e
        e2 = self.e2
        l_true = self.lab('gcmpT')
        l_done = self.lab('gcmpD')
        if pred == 7:
            e('\tucomisd %xmm1, %xmm0')
            e2('\tja %s', l_true)
        elif pred == 8:
            e('\tucomisd %xmm0, %xmm1')
            e2('\tja %s', l_true)
        elif pred == 9:
            e('\tucomisd %xmm1, %xmm0')
            e2('\tjae %s', l_true)
        elif pred == 10:
            e('\tucomisd %xmm0, %xmm1')
            e2('\tjae %s', l_true)
        elif pred == 11:
            l_f = self.lab('gcmpF')
            e('\tucomisd %xmm1, %xmm0')
            e2('\tjp %s', l_f)
            e2('\tje %s', l_true)
            e2('%s:', l_f)
        else:
            e('\tucomisd %xmm1, %xmm0')
            e2('\tjp %s', l_true)
            e2('\tjne %s', l_true)
        self._store_imm_v(dst, VT_BOOL, _bits_of_double(0.0))
        e2('\tjmp %s', l_done)
        e2('%s:', l_true)
        self._store_imm_v(dst, VT_BOOL, _bits_of_double(1.0))
        e2('%s:', l_done)

    def _emit_call(self, i, pc):
        """CALL_USER / CALL_BUILTIN 通用形态（语义单一来源 = argc 入口）。"""
        e = self.e
        e2 = self.e2
        args_site = self.site[('args', pc)]
        ret = self.site[('ret', pc)]
        callee_f = None
        if i.op == CALL_BUILTIN:
            nm = self.nt[i.name_idx] if 0 <= i.name_idx < len(self.nt) else ''
            if nm not in SUPPORTED_BUILTINS:
                eng = _qb.engine_of(nm)
                if eng:
                    raise NativeError(
                        '内置函数 %s() 属于 [%s] 引擎，AOT（native）不支持：'
                        '该调用无法编译。请在 builtins.toml 里为它补上 '
                        'native 引擎与 qotrt 符号，或改用 AOT 支持的写法。'
                        % (nm, '/'.join(eng)))
                raise NativeError('未知内置函数 %s：builtins.toml 未登记该名字'
                                  % nm)
            callee = 'qotrt_' + SUPPORTED_BUILTINS[nm]
        else:
            nm = self.nt[i.func_idx] if 0 <= i.func_idx < len(self.nt) else ''
            callee_f = self.fn_by_name.get(nm)
            if callee_f is None:
                raise NativeError('CALL_USER 目标解析失败 idx=%d' % i.func_idx)
            callee = 'qot_u_%s' % _sanitize(nm)
        fast_done = None
        if i.op == CALL_USER and self.fast is not None:
            fast_done = self._try_fast_call_head(i)
        self._fill_args_site(list(i.args or []), args_site)
        if i.op == CALL_BUILTIN and i.argc == 1 \
                and len(i.args or []) == 1 and nm in _BUILTIN_CORE1:
            # N-05 单参快通道（cpp 后端同款）：直连核心入口，免站拷贝
            # （args 数组构建 + argc 包装层）。核心入口委托 argc 版本，
            # 语义单一来源（G-11）；sret 形态：RCX=&ret, RDX=arg。
            self._addr_of(i.args[0], '%rdx')
            e2('\tleaq %d(%%rsp), %%rcx', ret)
            e2('\tcall %s', _BUILTIN_CORE1[nm])
            self._copy_v_from_site(ret, i.dst)
            return
        if i.op == CALL_BUILTIN and callee == 'qotrt_output':
            # void qotrt_output(V16 *args, int argc)；dst = null
            e2('\tleaq %d(%%rsp), %%rcx', args_site)
            e2('\tmovl $%d, %%edx', i.argc)
            e('\tcall qotrt_output')
            self._store_imm_v(i.dst, VT_NULL, 0)
            return
        if i.op == CALL_USER:
            self._emit_depth_plus1()
            if self._has_env(callee_f):
                if self.has_env:
                    e2('\tmovq %d(%%rsp), %%rax', self.lay.env0)
                else:
                    e('\txorl %eax, %eax')
                e('\tmovq %rax, 32(%rsp)')
            else:
                e('\tmovq $0, 32(%rsp)')
        e2('\tleaq %d(%%rsp), %%rdx', args_site)
        e2('\tmovl $%d, %%r8d', i.argc)
        e2('\tleaq %d(%%rsp), %%rcx', ret)
        e2('\tcall %s', callee)
        self._copy_v_from_site(ret, i.dst)
        if fast_done is not None:
            e2('%s:', fast_done)

    # ---------------------------------------------------------- v2 N-04 快速入口
    def _try_fast_call_head(self, i):
        """N-04 调用点快臂（cpp 同款 guard 全真形态）：全实参 NUM →
        双体入口直连（无站拷贝/无 sret/无装箱返回）；任一非数字回落
        通用 argc 路径（慢臂）。返回 done 标签；不资格返回 None。
        快入口 ABI（本文件私有约定，仅我方发射的两端对接）：
          实参 k=0..argc-1 → xmm0..xmm7；depth+1 → r10d；返回 xmm0。"""
        nm = self.nt[i.func_idx] if 0 <= i.func_idx < len(self.nt) else ''
        if not nm or self.fast.fast_key(nm, i.argc) is None:
            return None
        if len(i.args or []) != i.argc or i.argc > 8:
            return None
        e = self.e
        e2 = self.e2
        l_slow = self.lab('fs')
        l_done = self.lab('fd')
        for s in i.args:
            if self._is_ref_slot(s):
                self._addr_of(s, '%r10')
                e2('\tcmpb $%d, (%%r10)', VT_NUM)
            else:
                e2('\tcmpb $%d, %s', VT_NUM, self._slot_mem(s))
            e2('\tjne %s', l_slow)
        for k, s in enumerate(i.args):
            self._load_v_payload(s, '%%xmm%d' % k)
        # depth+1 → r10d（_emit_depth_plus1 的 r10 变体）
        if self.is_main:
            e('\tmovl $1, %r10d')
        else:
            e2('\tmovl %d(%%rsp), %%r10d', self.lay.depth)
            e('\taddl $1, %r10d')
        e2('\tcall %s', self.fast.symbol(nm, i.argc))
        # dst = qotrt_num(xmm0)：tag=VT_NUM + payload 直落
        if self._is_ref_slot(i.dst):
            self._addr_of(i.dst, '%rax')
            e2('\tmovb $%d, (%%rax)', VT_NUM)
            e('\tmovsd %xmm0, 8(%rax)')
        else:
            e2('\tmovb $%d, %s', VT_NUM, self._slot_mem(i.dst))
            e2('\tmovsd %%xmm0, %s', self._slot_mem(i.dst, 8))
        e2('\tjmp %s', l_done)
        e2('%s:', l_slow)
        return l_done

    def emit_fast_funcs(self):
        """N-04：为每个 (name, argc) 资格键发射双体快速入口。"""
        if self.fast is None:
            return
        for (nm, argc) in sorted(self.fast.forms):
            self._emit_fast_func(nm, argc)

    @staticmethod
    def _frame8(raw):
        """帧尺寸：≥ raw 且 ≡ 8 (mod 16)（call 前 rsp%16==0 纪律）。"""
        n = (raw + 15) & ~15
        if n % 16 == 0:
            n += 8
        return n

    def _emit_fast_func(self, name, argc):
        """双体快速入口：`double qot_u_<name>_fastK(xmm 实参..., r10d=depth)`。

        逐 op 与 cpp FastPlanner._emit_fast_instr 同构（G-11：语义单一来源
        = 快照体 + 资格分析的两后端共享）。布局：[0, nq*8) q 槽（裸
        double）+ [nq*8) depth 槽。无 try/无闭包/无 globals（资格分析
        排除）→ 无着陆点/无环境链/无常量池访问。"""
        info = self.fast.forms[(name, argc)]
        f2 = info['f2']
        b_all = info['b_all']
        snap = self.fast.snaps[name]
        nt = getattr(snap, 'name_table', None) or []
        pool = self.consts
        e = self.e
        e2 = self.e2
        nq = snap.n_slots_v
        dep = nq * 8
        frame = self._frame8(dep + 8)
        fid = 0x40000000 + self._nfast
        self._nfast += 1
        sym = self.fast.symbol(name, argc)

        def q(s):
            return '%d(%%rsp)' % (s * 8)

        e('')
        e2('\t.align 16')
        e2('\t.def %s; .scl 2; .type 32; .endef' % sym)
        e2('%s:', sym)
        e2('\t.seh_proc %s' % sym)
        e2('\tsubq $%d, %%rsp', frame)
        e2('\t.seh_stackalloc %d', frame)
        e('\t.seh_endprologue')
        e2('\tmovl %%r10d, %d(%%rsp)', dep)
        # 深度护栏（cpp 同款：depth > max → fail_depth(depth)）。
        # 【坑】qotrt_max_depth 是 int（4 字节）—— cmpq 读 8 字节会把
        # 相邻 .data 变量的高位拼进来（b4a 实测：depth=1 即误判超限），
        # 必须 cmpl 32 位比较（depth ≥ 1、max ≥ 1，均正数）。
        l_ok = self.lab('fdep')
        e2('\tcmpl qotrt_max_depth(%%rip), %%r10d')
        e2('\tjle %s', l_ok)
        e2('\tmovl %%r10d, %%ecx')
        e('\tcall qotrt_fail_depth')
        e2('%s:', l_ok)
        # q 槽清零 + 形参绑定（cpp：double q=0.0; q_k=p_k —— 防御性一致）
        e('\txorl %eax, %eax')
        for s in range(nq):
            e2('\tmovq %%rax, %s', q(s))
        for k in range(info['nbind']):
            e2('\tmovsd %%xmm%d, %s', k, q(k))
        l_ep = self.lab('fep')
        for b in f2.blocks:
            e2('.L%d_%d:', fid, b.id)
            for ins in b_all[b.id]:
                self._emit_fast_instr(ins, q, dep, fid, l_ep, pool, nt, argc)
        e2('%s:', l_ep)
        e2('\taddq $%d, %%rsp', frame)
        e('\tret')
        e('\t.seh_endproc')

    def _emit_fast_instr(self, i, q, dep, fid, l_ep, pool, nt, argc):
        """快体单指令发射（op 面 = FastPlanner._analyze 的资格集；超出即
        编译期报错 —— 分析已保证不可达，到达即规划/发射口径漂移）。"""
        e = self.e
        e2 = self.e2
        op = i.op
        if op == CONST_VAL:
            cv = None
            if isinstance(i.a, int) and 0 <= i.a < len(pool):
                cv = pool[i.a]
            if isinstance(cv, bool):
                e2('\tmovabsq $%d, %%rax', _bits_of_double(1.0 if cv else 0.0))
                e2('\tmovq %%rax, %s', q(i.dst))
            elif isinstance(cv, (int, float)):
                e2('\tmovabsq $%d, %%rax', _bits_of_double(float(cv)))
                e2('\tmovq %%rax, %s', q(i.dst))
            # None 形态 = 死定义（分析已保证无人读取）→ 不发射
            return
        if op == MOV_V:
            e2('\tmovsd %s, %%xmm0', q(i.a))
            e2('\tmovsd %%xmm0, %s', q(i.dst))
            return
        if op == UNARY_GEN:
            # 真取负（-0.0 口径与 qotrt_unary case 1 逐位一致）：
            # movabsq 符号位 → xmm1；xorpd 翻符号位
            e('\tmovabsq $%d, %%rax' % (1 << 63))
            e('\tmovq %rax, %xmm1')
            e2('\tmovsd %s, %%xmm0', q(i.a))
            e('\txorpd %xmm1, %xmm0')
            e2('\tmovsd %%xmm0, %s', q(i.dst))
            return
        if op == ARITH_GEN:
            pred = int(i.pred)
            if pred in (1, 2, 3):
                e2('\tmovsd %s, %%xmm1', q(i.a))
                e2('\tmovsd %s, %%xmm2', q(i.b))
                if pred == 1:
                    e('\taddsd %xmm2, %xmm1')
                elif pred == 2:
                    e('\tsubsd %xmm2, %xmm1')
                else:
                    e('\tmulsd %xmm2, %xmm1')
                e2('\tmovsd %%xmm1, %s', q(i.dst))
                return
            if pred == 4:
                # qotrt_fdiv 口径：b != 0 ? a/b : 0.0（NaN b 走 a/b）
                e2('\tmovsd %s, %%xmm1', q(i.a))
                e2('\tmovsd %s, %%xmm2', q(i.b))
                e('\txorl %eax, %eax')
                e('\tmovq %rax, %xmm0')
                e('\tucomisd %xmm0, %xmm2')
                l_do = self.lab('qfdiv')
                l_end = self.lab('qfdivend')
                e2('\tjne %s', l_do)
                e2('\tjp %s', l_do)
                e('\tmovsd %xmm0, %xmm1')
                e2('\tjmp %s', l_end)
                e2('%s:', l_do)
                e('\tdivsd %xmm2, %xmm1')
                e2('%s:', l_end)
                e2('\tmovsd %%xmm1, %s', q(i.dst))
                return
            if pred == 5:
                e2('\tmovsd %s, %%xmm0', q(i.a))
                e2('\tmovsd %s, %%xmm1', q(i.b))
                e2('\tcall fmod')
                e2('\tmovsd %%xmm0, %s', q(i.dst))
                return
            if pred == 6:
                e2('\tmovsd %s, %%xmm0', q(i.a))
                e2('\tmovsd %s, %%xmm1', q(i.b))
                e2('\tcall pow')
                e2('\tmovsd %%xmm0, %s', q(i.dst))
                return
            # 7..12：比较 → 0.0/1.0（与 _emit_fcmp 同一 jcc 极性表）
            e2('\tmovsd %s, %%xmm0', q(i.a))
            e2('\tmovsd %s, %%xmm1', q(i.b))
            l_true = self.lab('qcmpT')
            l_done = self.lab('qcmpD')
            if pred == 7:
                e('\tucomisd %xmm1, %xmm0')
                e2('\tja %s', l_true)
            elif pred == 8:
                e('\tucomisd %xmm0, %xmm1')
                e2('\tja %s', l_true)
            elif pred == 9:
                e('\tucomisd %xmm1, %xmm0')
                e2('\tjae %s', l_true)
            elif pred == 10:
                e('\tucomisd %xmm0, %xmm1')
                e2('\tjae %s', l_true)
            elif pred == 11:
                l_f = self.lab('qcmpF')
                e('\tucomisd %xmm1, %xmm0')
                e2('\tjp %s', l_f)
                e2('\tje %s', l_true)
                e2('%s:', l_f)
            else:
                e('\tucomisd %xmm1, %xmm0')
                e2('\tjp %s', l_true)
                e2('\tjne %s', l_true)
            e('\txorl %eax, %eax')
            e2('\tmovq %%rax, %s', q(i.dst))
            e2('\tjmp %s', l_done)
            e2('%s:', l_true)
            e2('\tmovabsq $%d, %%rax', _bits_of_double(1.0))
            e2('\tmovq %%rax, %s', q(i.dst))
            e2('%s:', l_done)
            return
        if op in (BR_IF_NOT, BR_IF):
            # 快体真值 = q != 0.0（bool 槽 1.0/0.0 的数值口径，cpp 同款）
            e2('\tmovsd %s, %%xmm0', q(i.a))
            e('\txorl %eax, %eax')
            e('\tmovq %rax, %xmm1')
            e('\tucomisd %xmm1, %xmm0')
            tgt = '.L%d_%d' % (fid, i.target)
            if op == BR_IF:
                e2('\tjp %s', tgt)          # NaN != 0 为真
                e2('\tjne %s', tgt)
            else:
                l_cont = self.lab('qbn')
                e2('\tjp %s', l_cont)
                e2('\tje %s', tgt)
                e2('%s:', l_cont)
            return
        if op == JMP:
            e2('\tjmp .L%d_%d', fid, i.target)
            return
        if op == RET_V:
            e2('\tmovsd %s, %%xmm0', q(i.a))
            e2('\tjmp %s', l_ep)
            return
        if op == CALL_USER:
            nm2 = nt[i.func_idx] if 0 <= i.func_idx < len(nt) else None
            if nm2 is None or self.fast.fast_key(nm2, i.argc) is None:
                raise NativeError('N-04: 快体内出现无快入口的调用 %r' % (nm2,))
            if len(i.args or []) != i.argc or i.argc > 8:
                raise NativeError('N-04: 快体内调用实参数异常 %r' % (nm2,))
            for k, s in enumerate(i.args):
                e2('\tmovsd %s, %%xmm%d', q(s), k)
            e2('\tmovl %d(%%rsp), %%r10d', dep)
            e('\taddl $1, %r10d')
            e2('\tcall %s', self.fast.symbol(nm2, i.argc))
            e2('\tmovsd %%xmm0, %s', q(i.dst))
            return
        raise NativeError('N-04: 快体内出现不支持的指令 op=%d' % op)

    def _emit_call_dynamic(self, i, pc):
        e = self.e
        e2 = self.e2
        args_site = self.site[('args', pc)]
        ret = self.site[('ret', pc)]
        ntab = max(1, len(self.nt))
        clo_slot = i.a
        self._fill_args_site(list(i.args or []), args_site)
        l_fail_tag = self.lab('clofailT')     # 非闭包 → fail_not_callable(tag)
        l_fail_bnd = self.lab('clofailB')     # 边界 → fail_not_callable(VT_CLOSURE)
        l_done = self.lab('clodone')
        if self._is_ref_slot(clo_slot):
            self._addr_of(clo_slot, '%r10')
            e2('\tcmpb $%d, (%%r10)', VT_CLOSURE)
            e2('\tjne %s', l_fail_tag)
            e('\tmovq 8(%r10), %rax')          # QClosure*
        else:
            e2('\tcmpb $%d, %s', VT_CLOSURE, self._slot_mem(clo_slot))
            e2('\tjne %s', l_fail_tag)
            e2('\tmovq %s, %%rax', self._slot_mem(clo_slot, 8))
        # 0 <= c->fn < ntab（C 版同款边界 → fail(VT_CLOSURE)）
        # 先取 env（rax=closure），再把 fn 索引挪 %rax（RCX 留给 sret ret 指针）
        e('\tmovq 8(%rax), %r11')
        e('\tmovq %r11, 32(%rsp)')
        e('\tmovslq (%rax), %rax')
        e('\ttestl %eax, %eax')
        e2('\tjs %s', l_fail_bnd)
        e2('\tcmpl $%d, %%eax', ntab)
        e2('\tjge %s', l_fail_bnd)
        # 装配：RCX=&ret, RDX=args, R8D=argc, R9D=depth+1, [rsp+32]=c->env
        self._emit_depth_plus1()
        e2('\tleaq %d(%%rsp), %%rdx', args_site)
        e2('\tmovl $%d, %%r8d', i.argc)
        e2('\tleaq %d(%%rsp), %%rcx', ret)
        e('\tleaq qot_clo_fns(%rip), %r10')
        e('\tcall *(%r10,%rax,8)')
        self._copy_v_from_site(ret, i.dst)
        e2('\tjmp %s', l_done)
        e2('%s:', l_fail_bnd)
        e2('\tmovl $%d, %%ecx', VT_CLOSURE)
        e('\tcall qotrt_fail_not_callable')
        e2('%s:', l_fail_tag)
        if self._is_ref_slot(clo_slot):
            self._addr_of(clo_slot, '%r10')
            e('\tmovzbl (%r10), %ecx')
        else:
            e2('\tmovzbl %s, %%ecx', self._slot_mem(clo_slot))
        e('\tcall qotrt_fail_not_callable')
        e2('%s:', l_done)

    def _emit_field_get(self, i):
        e = self.e
        e2 = self.e2
        l_null = self.lab('fldnull')
        l_done = self.lab('flddone')
        src = i.a
        if self._is_ref_slot(src):
            self._addr_of(src, '%r10')
            e2('\tcmpb $%d, (%%r10)', VT_OBJ)
            e2('\tjne %s', l_null)
            e('\tmovq 8(%r10), %rax')
        else:
            e2('\tcmpb $%d, %s', VT_OBJ, self._slot_mem(src))
            e2('\tjne %s', l_null)
            e2('\tmovq %s, %%rax', self._slot_mem(src, 8))
        # rax = QObj*（tid@0, a@8）；行距 32B（QTypeDesc：name@0/nfields@8/
        # foff@16/mfns@24）—— tid<<5（曾误用 tid*8 步长，tid≥1 读到邻行
        # 指针当 foff → 野地址崩溃）
        e('\tmovslq (%rax), %rcx')
        e('\tshlq $5, %rcx')
        e('\tleaq qotrt_types(%rip), %r10')
        e('\tmovq 16(%r10,%rcx,1), %r11')      # foff 数组指针
        e2('\tmovslq %d(%%r11), %%rcx', int(i.pred) * 4)
        e('\ttestl %ecx, %ecx')
        e2('\tjs %s', l_null)
        # 元素地址 = rax + 8 + off*16（两次 leaq：8+off*8 再 +off*8）
        e2('\tleaq 8(%%rax,%%rcx,8), %%r11')
        e2('\tleaq (%%r11,%%rcx,8), %%r11')
        e('\tmovq (%r11), %rax')
        e('\tmovq 8(%r11), %rdx')
        self._store_v_from_rax_rdx(i.dst)
        e2('\tjmp %s', l_done)
        e2('%s:', l_null)
        self._store_imm_v(i.dst, VT_NULL, 0)
        e2('%s:', l_done)

    def _emit_field_set(self, i):
        e = self.e
        e2 = self.e2
        l_done = self.lab('fldsdone')
        src = i.a
        if self._is_ref_slot(src):
            self._addr_of(src, '%r10')
            e2('\tcmpb $%d, (%%r10)', VT_OBJ)
            e2('\tjne %s', l_done)
            e('\tmovq 8(%r10), %rax')
        else:
            e2('\tcmpb $%d, %s', VT_OBJ, self._slot_mem(src))
            e2('\tjne %s', l_done)
            e2('\tmovq %s, %%rax', self._slot_mem(src, 8))
        e('\tmovslq (%rax), %rcx')
        e('\tshlq $5, %rcx')
        e('\tleaq qotrt_types(%rip), %r10')
        e('\tmovq 16(%r10,%rcx,1), %r11')
        e2('\tmovslq %d(%%r11), %%rcx', int(i.pred) * 4)
        e('\ttestl %ecx, %ecx')
        e2('\tjs %s', l_done)
        e2('\tleaq 8(%%rax,%%rcx,8), %%r11')
        e2('\tleaq (%%r11,%%rcx,8), %%r11')
        self._load_v_to_rax_rdx(i.c)
        e('\tmovq %rax, (%r11)')
        e('\tmovq %rdx, 8(%r11)')
        e2('%s:', l_done)

    def _emit_make_dict(self, i, pc):
        e = self.e
        e2 = self.e2
        ret = self.site[('ret', pc)]
        # d = qotrt_dict_new()（内部版无参 V16 → sret：RCX=&ret）
        e2('\tleaq %d(%%rsp), %%rcx', ret)
        e('\tcall qotrt_dict_new')
        self._copy_v_from_site(ret, i.dst)
        for k in range(i.argc):
            key_s, val_s = i.args[2 * k], i.args[2 * k + 1]
            l_done = self.lab('mkdd')
            # if (key.t == VT_STR) { *dict_at(d, kk->s, kk->len, vhash(&k)) = v; }
            if self._is_ref_slot(key_s):
                self._addr_of(key_s, '%r10')
                e2('\tcmpb $%d, (%%r10)', VT_STR)
                e2('\tjne %s', l_done)
            else:
                e2('\tcmpb $%d, %s', VT_STR, self._slot_mem(key_s))
                e2('\tjne %s', l_done)
            # hash = qotrt_vhash(&key)（先算 —— 调用破坏 rcx/rdx/r8）
            if self._is_ref_slot(key_s):
                self._addr_of(key_s, '%rcx')
            else:
                e2('\tleaq %s, %%rcx', self._slot_mem(key_s))
            e('\tcall qotrt_vhash')
            e('\tmovq %rax, %r9')                  # hash
            # rcx = (QDict*)dst.u.p
            if self._is_ref_slot(i.dst):
                self._addr_of(i.dst, '%r10', into_payload=True)
                e('\tmovq (%r10), %rcx')
            else:
                e2('\tmovq %s, %%rcx', self._slot_mem(i.dst, 8))
            # rdx = kk->s, r8d = kk->len（QStr：len@0, cap@4, s@8, hash@16）
            if self._is_ref_slot(key_s):
                self._addr_of(key_s, '%r10', into_payload=True)
                e('\tmovq (%r10), %r10')
            else:
                e2('\tmovq %s, %%r10', self._slot_mem(key_s, 8))
            e('\tmovq 8(%r10), %rdx')
            e('\tmovl 0(%r10), %r8d')
            e('\tcall qotrt_dict_at')
            # *rax = val（16B）
            if self._is_ref_slot(val_s):
                self._addr_of(val_s, '%r11')
                e('\tmovq (%r11), %rdx')
                e('\tmovq 8(%r11), %rcx')
                e('\tmovq %rdx, (%rax)')
                e('\tmovq %rcx, 8(%rax)')
            else:
                e2('\tmovq %s, %%rdx', self._slot_mem(val_s))
                e2('\tmovq %s, %%rcx', self._slot_mem(val_s, 8))
                e('\tmovq %rdx, (%rax)')
                e('\tmovq %rcx, 8(%rax)')
            e2('%s:', l_done)

    def _emit_omethod(self, i, pc):
        e = self.e
        e2 = self.e2
        args_site = self.site[('args', pc)]
        ret = self.site[('ret', pc)]
        self._fill_args_site(list(i.args or []), args_site)
        l_fallback = self.lab('omfb')
        l_done = self.lab('omdone')
        onone = self.lab('omonone')
        # receiver = args[0]（站首槽）
        e2('\tcmpb $%d, %d(%%rsp)', VT_OBJ, args_site)
        e2('\tjne %s', l_fallback)
        e2('\tmovq %d(%%rsp), %%rax', args_site + 8)   # QObj*
        e('\tmovslq (%rax), %rcx')
        e('\tshlq $5, %rcx')
        e('\tleaq qotrt_types(%rip), %r10')
        e('\tmovq 24(%r10,%rcx,1), %r11')              # mfns 数组（行距 32B）
        e2('\tmovq %d(%%r11), %%r11', int(i.pred) * 8)  # mfns[mid]
        e('\ttestq %r11, %r11')
        e2('\tje %s', onone)
        # 调 mfns[mid](&ret, args, argc, depth+1, 0)
        e('\tmovq $0, 32(%rsp)')
        self._emit_depth_plus1()
        e2('\tleaq %d(%%rsp), %%rdx', args_site)
        e2('\tmovl $%d, %%r8d', i.argc)
        e2('\tleaq %d(%%rsp), %%rcx', ret)
        e('\tcall *%r11')
        self._copy_v_from_site(ret, i.dst)
        e2('\tjmp %s', l_done)
        e2('%s:', onone)
        self._store_imm_v(i.dst, VT_NULL, 0)
        e2('\tjmp %s', l_done)
        # ---- 退路（receiver 非 OBJ）：内置 / 用户函数
        e2('%s:', l_fallback)
        nm = self.nt[i.name_idx] if 0 <= i.name_idx < len(self.nt) else ''
        if nm in SUPPORTED_BUILTINS:
            if nm == 'output':
                e2('\tleaq %d(%%rsp), %%rcx', args_site)
                e2('\tmovl $%d, %%edx', i.argc)
                e('\tcall qotrt_output')
                self._store_imm_v(i.dst, VT_NULL, 0)
            else:
                e2('\tleaq %d(%%rsp), %%rcx', ret)
                e2('\tleaq %d(%%rsp), %%rdx', args_site)
                e2('\tmovl $%d, %%r8d', i.argc)
                e2('\tcall qotrt_%s', SUPPORTED_BUILTINS[nm])
                self._copy_v_from_site(ret, i.dst)
        else:
            callee_f = self.fn_by_name.get(nm)
            if callee_f is None:
                meta = getattr(self.prog, 'objmeta', None)
                mids = (meta.get('method_ids') or {}) if meta else {}
                if nm in mids:
                    self._store_imm_v(i.dst, VT_NULL, 0)
                else:
                    raise NativeError(
                        '方法调用 %s() 的退路解析失败：既不是 AOT 支持的内置，'
                        '也不是用户函数或类方法' % nm)
            else:
                if self._has_env(callee_f):
                    if self.has_env:
                        e2('\tmovq %d(%%rsp), %%rax', self.lay.env0)
                    else:
                        e('\txorl %eax, %eax')
                    e('\tmovq %rax, 32(%rsp)')
                else:
                    e('\tmovq $0, 32(%rsp)')
                self._emit_depth_plus1()
                e2('\tleaq %d(%%rsp), %%rdx', args_site)
                e2('\tmovl $%d, %%r8d', i.argc)
                e2('\tleaq %d(%%rsp), %%rcx', ret)
                e2('\tcall qot_u_%s', _sanitize(nm))
                self._copy_v_from_site(ret, i.dst)
        e2('%s:', l_done)

    # ================================================================ main / init / tables
    def emit_main_crt(self):
        e = self.e
        e2 = self.e2
        e('')
        e('\t.text')
        e('\t.globl main')
        e('\t.def main; .scl 2; .type 32; .endef')
        e('main:')
        e('\t.seh_proc main')
        e('\tpushq %rbp')
        e('\t.seh_pushreg %rbp')
        e('\tmovq %rsp, %rbp')
        e('\t.seh_setframe %rbp, 0')
        e('\tsubq $48, %rsp')
        e('\t.seh_stackalloc 48')
        e('\t.seh_endprologue')
        # qotrt_init_args(argc, argv)
        e('\tmovl %ecx, %ecx')
        e('\tcall qotrt_init_args')
        e('\tcall qotrt_init_limits')
        e('\tcall qotrt_init_consts')
        # qot_main(&r, 0, 0)
        e('\tleaq 32(%rsp), %rcx')
        e('\txorl %edx, %edx')
        e('\txorl %r8d, %r8d')
        e('\tcall qot_main')
        e('\txorl %eax, %eax')
        # leave 等价（movq %rbp,%rsp; popq %rbp）—— ret 弹 [rbp+8] 返回地址
        e('\tmovq %rbp, %rsp')
        e('\tpopq %rbp')
        e('\tret')
        e('\t.seh_endproc')

    def emit_init_consts(self):
        e = self.e
        e2 = self.e2
        e('')
        e('\t.text')
        e('\t.globl qotrt_init_consts')
        e('\t.def qotrt_init_consts; .scl 2; .type 32; .endef')
        e('qotrt_init_consts:')
        e('\t.seh_proc qotrt_init_consts')
        e('\tsubq $56, %rsp')
        e('\t.seh_stackalloc 56')
        e('\t.seh_endprologue')
        e2('\tmovl $%d, qotrt_nconsts(%%rip)', len(self.consts))
        e2('\tmovl $%d, %%ecx', max(16, len(self.consts) * 16))
        e('\tcall qotrt_xalloc')
        e('\tmovq %rax, qotrt_consts(%rip)')
        e('\tmovq %rax, %r10')                    # 池基址
        for idx, v in enumerate(self.consts):
            if isinstance(v, bool):
                e2('\tmovabsq $%d, %%rax', _bits_of_double(1.0 if v else 0.0))
                e2('\tmovb $%d, %d(%%r10)', VT_BOOL, idx * 16)
                e2('\tmovq %%rax, %d(%%r10)', idx * 16 + 8)
            elif isinstance(v, (int, float)):
                e2('\tmovabsq $%d, %%rax', _bits_of_double(v))
                e2('\tmovb $%d, %d(%%r10)', VT_NUM, idx * 16)
                e2('\tmovq %%rax, %d(%%r10)', idx * 16 + 8)
            elif isinstance(v, str):
                raw = v.encode('utf-8')
                lid = self.rdata_q(raw + b'\0', 1)
                # qotrt_str_shared(s, len, 0) sret：RCX=&tmp, RDX=s, R8=len, R9=0
                e('\tmovq $0, %r9')
                e2('\tmovl $%d, %%r8d', len(raw))
                e2('\tleaq %s(%%rip), %%rdx', lid)
                e('\tleaq 32(%rsp), %rcx')        # tmp（shadow 上方）
                e('\tcall qotrt_str_shared')
                e('\tmovq 32(%rsp), %rax')
                e('\tmovq 40(%rsp), %rdx')
                e2('\tmovq %%rax, %d(%%r10)', idx * 16)
                e2('\tmovq %%rdx, %d(%%r10)', idx * 16 + 8)
            else:
                # VMCode/None → null 占位（与 cgen 同律）
                e2('\tmovb $%d, %d(%%r10)', VT_NULL, idx * 16)
                e('\txorl %eax, %eax')
                e2('\tmovq %%rax, %d(%%r10)', idx * 16 + 8)
        e('\taddq $56, %rsp')
        e('\tret')
        e('\t.seh_endproc')

    def emit_tables(self):
        e = self.e
        e2 = self.e2
        # ---- qot_clo_fns / qotrt_clo_count（.data：条目=函数地址）----
        e('')
        e('\t.data')
        e('\t.align 8')
        e('\t.globl qotrt_clo_count')
        e('\t.def qotrt_clo_count; .scl 2; .type 4; .endef')
        e('qotrt_clo_count:')
        e2('\t.long %d', len(self.clo_index))
        e('\t.globl qot_clo_fns')
        e('\t.def qot_clo_fns; .scl 2; .type 21; .endef')
        e('\t.align 8')
        e('qot_clo_fns:')
        if self.clo_index:
            for nm in sorted(self.clo_index):
                e2('\t.quad qot_u_%s', _sanitize(nm))
        else:
            e('\t.quad 0')
        # ---- qotrt_types / qotrt_ntypes（.rdata）----
        meta = getattr(self.prog, 'objmeta', None)
        classes = (meta.get('classes') if meta else None) or []
        field_ids = (meta.get('field_ids') or {}) if meta else {}
        method_ids = (meta.get('method_ids') or {}) if meta else {}
        nf = int((meta.get('nfields') if meta else 0) or 0)
        nmth = int((meta.get('nmethods') if meta else 0) or 0)
        e('')
        e('\t.section .rdata,"dr"')
        e('\t.globl qotrt_ntypes')
        e('\t.def qotrt_ntypes; .scl 2; .type 4; .endef')
        e('\t.align 4')
        e('qotrt_ntypes:')
        e2('\t.long %d', len(classes))
        if not classes:
            empty = self.rdata_q(b'\0', 1)
            e('\t.globl qotrt_types')
            e('\t.def qotrt_types; .scl 2; .type 21; .endef')
            e('\t.align 8')
            e('qotrt_types:')
            e2('\t.quad %s', empty)
            e('\t.long 0')
            e('\t.long 0')
            e('\t.quad 0')
            e('\t.quad 0')
            return
        foff_labels = []
        mfns_labels = []
        name_labels = []
        for k, (cname, fields, methods) in enumerate(classes):
            fo = [-1] * max(1, nf)
            for off, fname in enumerate(fields):
                fid = field_ids.get(fname)
                if fid is not None and 0 <= fid < max(1, nf):
                    fo[fid] = off
            flid = self.lab('FOFF')
            self.rdata.append((flid, struct.pack('<%di' % max(1, nf), *fo), 4))
            foff_labels.append(flid)
            row = ['0'] * max(1, nmth)
            for mname, mangled in (methods or {}).items():
                mid = method_ids.get(mname)
                if mid is not None and 0 <= mid < max(1, nmth):
                    row[mid] = 'qot_u_%s' % _sanitize(mangled)
            mlid = self.lab('MFNS')
            # 方法指针条目经重定位 → 文本 .quad（as 处理 ADDR64）
            self.lines.append('\t.section .rdata,"dr"')
            self.lines.append('\t.align 8')
            self.lines.append('%s:' % mlid)
            for x in row:
                self.lines.append('\t.quad %s' % x)
            mfns_labels.append(mlid)
            name_labels.append(self.rdata_q(cname.encode('utf-8') + b'\0', 1))
        e('\t.globl qotrt_types')
        e('\t.def qotrt_types; .scl 2; .type 21; .endef')
        e('\t.align 8')
        e('qotrt_types:')
        for k in range(len(classes)):
            e2('\t.quad %s', name_labels[k])
            e2('\t.long %d', len(classes[k][1]))
            e('\t.long 0')
            e2('\t.quad %s', foff_labels[k])
            e2('\t.quad %s', mfns_labels[k])

    def emit_rdata(self):
        if not self.rdata:
            return
        e = self.e
        e2 = self.e2
        e('')
        e('\t.section .rdata,"dr"')
        for lid, data, align in self.rdata:
            e('\t.align %d' % max(1, align))
            e2('%s:', lid)
            for k in range(0, len(data), 16):
                chunk = data[k:k + 16]
                e('\t.byte ' + ', '.join(str(b) for b in chunk))


# ================================================================ 编译入口
def generate_asm(prog, fast_snaps=None):
    """LIR 程序 → x64 GAS 汇编文本。fast_snaps = N-04 快入口规划输入。"""
    if os.environ.get('QOT_DUMP_LIR') == '1':
        _d = getattr(prog, 'dump', None) or (lambda: str(prog))
        sys.stderr.write(_d() + '\n')
    return NativeCg(prog, fast_snaps).generate()


def assemble_obj(asm_text, out_obj, gxx='g++', timeout=120):
    """asm 文本 → COFF 目标（GAS 汇编）。返回 (elapsed_s, asm_len)。

    工具链缓存（K2 形态）的冷路径：qot_cache.native_assemble_cached 负责
    key/命中/CAS，本函数只做「写 .s + g++ -c」这两件机械事。
    """
    import time
    t0 = time.perf_counter()
    base = os.path.splitext(os.path.abspath(out_obj))[0]
    s_path = base + '_native.s'
    with open(s_path, 'w', encoding='utf-8', newline='\n') as fh:
        fh.write(asm_text)
    cmd = [gxx, '-c', s_path, '-o', os.path.abspath(out_obj)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              errors='replace', timeout=timeout,
                              cwd=os.path.dirname(s_path) or '.')
    except subprocess.TimeoutExpired:
        raise NativeError('native 汇编超时（>%ss）' % timeout)
    if proc.returncode != 0 or not os.path.exists(out_obj):
        raise NativeError('native 汇编失败(rc=%s):\n%s\n%s'
                          % (proc.returncode, (proc.stderr or '')[-4000:],
                             (proc.stdout or '')[-800:]))
    return time.perf_counter() - t0, len(asm_text)


def compile_obj(prog, out_obj, gxx='g++', timeout=120):
    """LIR 程序 → COFF 目标（发射 + 汇编，无缓存直通形态）。
    常规管线走 index.py → qot_cache.native_assemble_cached（obj CAS）。"""
    asm = generate_asm(prog)
    el, n = assemble_obj(asm, out_obj, gxx, timeout)
    return el, n
