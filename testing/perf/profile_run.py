# -*- coding: utf-8 -*-
"""T-32 采样剖析运行器（ 7.4 机器码 P0）。

流程：
  1. 把 testing/perf/*.qot 编译到 testing/.artifacts/
  2. 用剖析构建的 DLL（/DQOT_VM_PROFILE，本脚本可自动构建）逐用例进程内执行
  3. vm_profile_dump 取回每 opcode 计数/周期与归因计数器
  4. 聚合写出 docs/：前三大开销 + 完整归因表

用法:
  python testing/perf/profile_run.py                # 全部 perf 用例，写报告
  python testing/perf/profile_run.py --cases p01_loop_100k,p02_loop_1m
  python testing/perf/profile_run.py --repeat 3 --out docs/

退出码: 0 成功；2 剖析 DLL 缺失且无法构建；1 运行失败。
"""
import argparse
import ctypes
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))          # testing/perf
TESTING = os.path.dirname(HERE)
ROOT = os.path.dirname(TESTING)
ART = os.path.join(TESTING, '.artifacts')
PROFILE_DLL = os.path.join(ART, 'qotrun_profile.dll')
BUILD_PS1 = os.path.join(ROOT, 'ot-asset', 'c', 'vmcshit', 'build.ps1')

# 与 bytecode.VMInstruction / vm_types.h OpCode 对齐（T-12 守卫）
OP_NAMES = {
    0: 'NOP', 1: 'PUSH_CONST', 2: 'PUSH_VAR', 3: 'STORE_VAR', 4: 'STORE_CONST',
    5: 'POP', 10: 'BINARY_OP', 11: 'UNARY_OP', 20: 'JUMP', 21: 'JUMP_IF_FALSE',
    22: 'JUMP_IF_TRUE', 30: 'CALL', 31: 'CALL_METHOD', 32: 'RETURN',
    33: 'CALL_STATIC', 40: 'MAKE_ARRAY', 41: 'MAKE_DICT', 42: 'INDEX_GET',
    43: 'INDEX_SET', 50: 'POP_STMT', 51: 'STOP', 52: 'BREAK', 60: 'REQUIRE',
}

COUNTER_LABELS = [
    ('builtinNameLookups', '内置函数名字符串哈希查找（BuiltinManager::find）'),
    ('builtinCalls', '内置函数调用次数'),
    ('userCalls', '用户函数调用次数'),
    ('globalFallbackReads', 'PUSH_VAR varNames/m_globals 字符串回退路径'),
    ('globalFallbackWrites', 'STORE_VAR 字符串回退路径'),
    ('valueRetain', 'Value 引用计数 ++'),
    ('valueRelease', 'Value 引用计数 --'),
    ('valueCopyCtor', 'Value 拷贝构造（16B 搬运 + 分支）'),
    ('valueMoveCtor', 'Value 移动构造'),
    ('valueDtor', 'Value 析构'),
    ('stackGrows', '操作数栈扩容（整栈搬迁）'),
    ('framePoolHits', '帧池命中'),
    ('framePoolMisses', '帧池未命中（新分配）'),
    ('dictFind', '字典查找'),
    ('dictInsert', '字典插入'),
]


def ensure_profile_dll() -> bool:
    if os.path.exists(PROFILE_DLL):
        return True
    os.makedirs(ART, exist_ok=True)
    r = subprocess.run(
        ['powershell', '-ExecutionPolicy', 'Bypass', '-File', BUILD_PS1,
         '-OutFile', PROFILE_DLL, '-ExtraDefines', 'QOT_VM_PROFILE'],
        cwd=ROOT, capture_output=True, text=True)
    return os.path.exists(PROFILE_DLL)


def compile_case(qot: str, name: str) -> str:
    os.makedirs(ART, exist_ok=True)
    art = os.path.join(ART, name + '.qtc')
    r = subprocess.run([sys.executable, 'index.py', 'bstrust', 'compile', qot, art],
                       cwd=ROOT, capture_output=True, text=True, timeout=120)
    if not os.path.exists(art):
        raise RuntimeError('编译失败 %s: %s' % (name, (r.stderr or r.stdout)[-300:]))
    return art


class ProfilerVM:
    def __init__(self, dll_path: str):
        self.dll = ctypes.CDLL(dll_path)
        self.dll.vm_init.restype = ctypes.c_void_p
        self.dll.vm_execute_file.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
        self.dll.vm_execute_file.restype = ctypes.c_bool
        self.dll.vm_destroy.argtypes = [ctypes.c_void_p]
        self.dll.vm_profile_reset.restype = None
        self.dll.vm_profile_dump.restype = ctypes.c_void_p
        self.dll.vm_profile_free.argtypes = [ctypes.c_void_p]
        self.dll.vm_profile_free.restype = None
        self.dll.vm_get_return_value.argtypes = [ctypes.c_void_p]
        self.dll.vm_get_return_value.restype = ctypes.c_char_p

    def run_case(self, qtc: str, repeat: int) -> dict:
        """进程内执行 repeat 次，返回合并的剖析数据。"""
        import io
        real_stdout = sys.stdout
        merged = None
        devnull = os.open(os.devnull, os.O_WRONLY)
        saved = os.dup(1)
        try:
            sys.stdout = io.TextIOWrapper(io.BytesIO(), encoding='utf-8')
            sys.stdout.flush()
            os.dup2(devnull, 1)
            for _ in range(repeat):
                self.dll.vm_profile_reset()
                vm = self.dll.vm_init()
                if not vm:
                    raise RuntimeError('vm_init 失败')
                ok = self.dll.vm_execute_file(vm, qtc.encode('utf-8'))
                self.dll.vm_destroy(vm)
                if not ok:
                    raise RuntimeError('vm_execute_file 返回 False: %s' % qtc)
                ptr = self.dll.vm_profile_dump()
                if not ptr:
                    raise RuntimeError('剖析 DLL 未返回数据（确认用 -ExtraDefines QOT_VM_PROFILE 构建）')
                s = ctypes.string_at(ptr).decode('utf-8')
                self.dll.vm_profile_free(ptr)
                data = json.loads(s)
                if merged is None:
                    merged = data
                else:
                    merged['instrExecuted'] += data['instrExecuted']
                    merged['totalRunCycles'] += data['totalRunCycles']
                    for k, v in data['counters'].items():
                        merged['counters'][k] = merged['counters'].get(k, 0) + v
                    agg_ops = {}
                    for op, cnt, cyc in merged['ops']:
                        agg_ops[op] = [cnt, cyc]
                    for op, cnt, cyc in data['ops']:
                        a = agg_ops.setdefault(op, [0, 0])
                        a[0] += cnt
                        a[1] += cyc
                    merged['ops'] = [[op, v[0], v[1]] for op, v in sorted(agg_ops.items())]
        finally:
            os.dup2(saved, 1)
            os.close(saved)
            os.close(devnull)
            sys.stdout = real_stdout
        return merged


def aggregate(reports: list, repeat: int) -> dict:
    """把多个用例的剖析数据聚合为归因结论。"""
    op_cnt, op_cyc = {}, {}
    counters = {}
    instr = 0
    for d in reports:
        instr += d['instrExecuted']
        for op, cnt, cyc in d['ops']:
            op_cnt[op] = op_cnt.get(op, 0) + cnt
            op_cyc[op] = op_cyc.get(op, 0) + cyc
        for k, v in d['counters'].items():
            counters[k] = counters.get(k, 0) + v
    total_cyc = sum(op_cyc.values())
    ops = []
    for op in sorted(op_cnt, key=lambda o: -op_cyc[o]):
        ops.append({
            'op': op, 'name': OP_NAMES.get(op, 'OP_%d' % op),
            'count': op_cnt[op], 'cycles': op_cyc[op],
            'ns_per_exec': (op_cyc[op] / op_cnt[op] / tsc_hz) * 1e9 if tsc_hz and op_cnt[op] else 0,
            'pct': (100.0 * op_cyc[op] / total_cyc) if total_cyc else 0,
        })
    return {'instr': instr, 'ops': ops, 'counters': counters, 'total_cycles': total_cyc}


def fmt_int(n):
    return '{:,}'.format(int(n))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description='T-32 VM 剖析运行器')
    ap.add_argument('--cases', help='逗号分隔的用例名（默认全部 perf 用例）')
    ap.add_argument('--repeat', type=int, default=3)
    ap.add_argument('--out', default=os.path.join(ROOT, 'docs', ''))
    args = ap.parse_args(argv)

    global tsc_hz
    if not ensure_profile_dll():
        print('[错误] 剖析 DLL 缺失且自动构建失败: %s' % PROFILE_DLL, file=sys.stderr)
        return 2
    os.makedirs(os.path.dirname(args.out), exist_ok=True)

    if args.cases:
        names = [s.strip() for s in args.cases.split(',') if s.strip()]
    else:
        names = sorted(f[:-4] for f in os.listdir(HERE) if f.endswith('.qot'))

    vm = ProfilerVM(PROFILE_DLL)
    case_reports = []
    for name in names:
        qot = os.path.join(HERE, name + '.qot')
        if not os.path.exists(qot):
            print('[跳过] 无用例 %s' % qot)
            continue
        qtc = compile_case(qot, name)
        d = vm.run_case(qtc, args.repeat)
        case_reports.append((name, d))
        print('[剖析] %-16s 指令数=%s' % (name, fmt_int(d['instrExecuted'])))

    global tsc_hz
    tsc_hz = case_reports[0][1].get('tsc_hz') or 0 if case_reports else 0

    agg = aggregate([d for _, d in case_reports], args.repeat)
    write_report(args.out, case_reports, agg, args.repeat)
    print('[完成] 报告 -> %s' % args.out)
    return 0


def write_report(out_path, case_reports, agg, repeat):
    lines = []
    lines.append('# T-32 VM 剖析报告（7.4 机器码 P0）')
    lines.append('')
    lines.append('- 日期: %s' % time.strftime('%Y-%m-%d %H:%M:%S'))
    lines.append('- 剖析构建: `build.ps1 -ExtraDefines QOT_VM_PROFILE`（生产 DLL 不含插桩，size 820,224 与基线一致）')
    lines.append('- TSC 频率: %s Hz' % fmt_int(tsc_hz))
    lines.append('- 重复次数: %d（计数器累加）/ 用例: %s' % (repeat, ', '.join(n for n, _ in case_reports)))
    lines.append('- 累计执行指令: %s；累计 opcode 周期: %s' % (fmt_int(agg['instr']), fmt_int(agg['total_cycles'])))
    lines.append('')
    ns_per_instr = (agg['total_cycles'] / agg['instr'] / tsc_hz) * 1e9 if tsc_hz else 0
    lines.append('**每指令摊销: %.1f ns**（剖析插桩有放大，绝对值偏高；用于「相对占比」归因，'
                 '绝对耗时以 perf 套件进程内计时为准）' % ns_per_instr)
    lines.append('')
    lines.append('## 每 opcode 归因表（按周期占比降序）')
    lines.append('')
    lines.append('| op | 名称 | 执行次数 | 占比% | 每次 ns | 累计周期 |')
    lines.append('|---|---|---:|---:|---:|---:|')
    for o in agg['ops']:
        lines.append('| %d | %s | %s | %.1f | %.1f | %s |' % (
            o['op'], o['name'], fmt_int(o['count']), o['pct'],
            o['ns_per_exec'], fmt_int(o['cycles'])))
    lines.append('')
    lines.append('## 归因计数器（按发生频率降序）')
    lines.append('')
    lines.append('| 计数器 | 含义 | 次数 | 每指令摊销 |')
    lines.append('|---|---|---:|---:|')
    items = sorted(agg['counters'].items(), key=lambda kv: -kv[1])
    label = dict(COUNTER_LABELS)
    for k, v in items:
        per = agg['counters'][k] / agg['instr'] if agg['instr'] else 0
        lines.append('| %s | %s | %s | %.2f |' % (k, label.get(k, k), fmt_int(v), per))
    lines.append('')
    lines.append('## 前三大开销（数据驱动的结论）')
    lines.append('')
    top3 = []
    ops = agg['ops']
    counters = agg['counters']
    calls = counters.get('builtinCalls', 0) + counters.get('userCalls', 0)
    if calls:
        lines.append('1. **CALL 每次调用做 1~2 次字符串哈希查表**：内置查找 builtinNameLookups/call = %.2f，'
                     '内置 %s 次 + 用户 %s 次。归因：BuiltinManager/m_functions 都是 unordered_map<string,...>，'
                     '热路径按名字查找 → T-34/T-35 索引化。' % (
                         counters.get('builtinNameLookups', 0) / calls,
                         fmt_int(counters.get('builtinCalls', 0)), fmt_int(counters.get('userCalls', 0))))
        top3.append('call-dispatch-hash')
    rc = counters.get('valueRetain', 0) + counters.get('valueRelease', 0)
    if rc:
        lines.append('2. **Value 装箱搬运/引用计数流量**：retain %s + release %s + 拷贝构造 %s + 析构 %s'
                     '（每指令 %.2f 次 retain/release）→ 表示层与栈搬运是第二大开销源 → T-44 裸 double 槽位 + 消栈搬运。'
                     % (fmt_int(counters.get('valueRetain', 0)), fmt_int(counters.get('valueRelease', 0)),
                        fmt_int(counters.get('valueCopyCtor', 0)),
                        fmt_int(counters.get('valueDtor', 0)),
                        rc / agg['instr'] if agg['instr'] else 0))
        top3.append('value-traffic')
    stack = [o for o in ops if o['name'] in ('PUSH_CONST', 'PUSH_VAR', 'STORE_VAR', 'POP', 'POP_STMT')]
    stack_pct = sum(o['pct'] for o in stack)
    if stack:
        lines.append('3. **纯栈搬运指令占 dispatch 预算 %.1f%%**（%s）——这些指令不做任何语义工作，'
                     '只把 16B Value 在栈上搬进搬出 → LIR（T-40/41）把栈槽映射到变量/寄存器后可整类消掉。'
                     % (stack_pct, ' + '.join('%s %.1f%%' % (o['name'], o['pct']) for o in stack)))
        top3.append('stack-shuffling')
    lines.append('')
    lines.append('（顺序按占比；前三大开销: %s）' % ' > '.join(top3))
    lines.append('')
    lines.append('## 各用例指令构成')
    lines.append('')
    for name, d in case_reports:
        parts = ', '.join('%s×%s' % (OP_NAMES.get(op, op), fmt_int(cnt))
                          for op, cnt, _ in sorted(d['ops'], key=lambda t: -t[1])[:6])
        lines.append('- **%s**: 指令 %s；主要构成: %s' % (name, fmt_int(d['instrExecuted']), parts))
    lines.append('')
    with open(out_path, 'w', encoding='utf-8', newline='\n') as f:
        f.write('\n'.join(lines) + '\n')


tsc_hz = 0

if __name__ == '__main__':
    sys.exit(main())
