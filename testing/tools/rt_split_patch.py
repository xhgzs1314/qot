# -*- coding: utf-8 -*-
"""QOT-CI L1 · runtime 头切分手术（一次性补丁，带断言）。

把 32 个「必须单实例」的可变 static 状态改为条件编译：
  -DQOT_RT_SPLIT（切分 TU 编译）→ extern 声明，定义由 cgen 主 TU 发射；
  不定义（单 TU，现状）        → 原 static 定义，预处理产物逐字节不变。

per-TU 安全（不动）：QSC_N/QSC_SIZE/QD_EMPTY/QD_DELETED/QRT_REPL/QRT_B64TAB
（const）、qotrt_char_intern(+Init)/qotrt_sc_lut（内容恒定、幂等构造）、
qotrt_dbg_tab/QRT_DBG_TOMB（QOT_RT_POOL_DEBUG 专用）。
两份副本（ot-asset 规范位 + 根目录探针位）同步修改并保持一致。
"""
import io
import sys

ROOT = r'e:\QGuoat-jsq\QGuoat'
PATHS = [ROOT + r'\ot-asset\c\aot\qot_aot_runtime.h',
         ROOT + r'\qot_aot_runtime.h']

BANNER = ('【QOT-CI L1 · CP-C2】QOT_RT_SPLIT（切分 TU）下状态单实例化：extern 声明，'
          '定义由 cgen 在主 TU 发射；单 TU 不定义该宏，预处理产物与历史逐字节一致。')

# (原行, extern 行)  —— 原行必须逐字符精确匹配（含注释与空白）
SITES = [
    ('static QotrtPool qotrt_pool = {{0}, nullptr, 0};',
     'extern QotrtPool qotrt_pool;'),
    ('static QotrtArenaMark qotrt_arena_stack[8];',
     'extern QotrtArenaMark qotrt_arena_stack[8];'),
    ('static int qotrt_arena_sp = 0;',
     'extern int qotrt_arena_sp;'),
    ('static QotrtLanding *qotrt_landings = nullptr;',
     'extern QotrtLanding *qotrt_landings;'),
    ('static V16 qotrt_thrown;',
     'extern V16 qotrt_thrown;'),
    ('static V16 *qotrt_consts = nullptr;',
     'extern V16 *qotrt_consts;'),
    ('static int qotrt_nconsts = 0;',
     'extern int qotrt_nconsts;'),
    ('static long long qotrt_ticks = 0;',
     'extern long long qotrt_ticks;'),
    ('static long long qotrt_tick_limit = -1;   // -1 = 未初始化；INT64_MAX = 不限',
     'extern long long qotrt_tick_limit;'),
    ('static int qotrt_max_depth = 5000;        // 递归深度上限（QOT_MAX_DEPTH 可覆盖）',
     'extern int qotrt_max_depth;'),
    ('static int qotrt_cb_depth = 0;',
     'extern int qotrt_cb_depth;'),
    ('static QrtFileSlot *qotrt_ftab = nullptr;',
     'extern QrtFileSlot *qotrt_ftab;'),
    ('static int32_t qotrt_ftab_cap = 0;',
     'extern int32_t qotrt_ftab_cap;'),
    ('static int32_t qotrt_ftab_free = -1;',
     'extern int32_t qotrt_ftab_free;'),
    ('static uint64_t qotrt_rand_state = 0x853C49E6748FEA9BULL;',
     'extern uint64_t qotrt_rand_state;'),
    ('static LPWSTR *qotrt_os_wargv = nullptr;',
     'extern LPWSTR *qotrt_os_wargv;'),
    ('static int qotrt_os_wargc = 0;',
     'extern int qotrt_os_wargc;'),
    ('static int qotrt_os_argc = 0;',
     'extern int qotrt_os_argc;'),
    ('static char **qotrt_os_argv = nullptr;',
     'extern char **qotrt_os_argv;'),
    ('static QrtProcSlot *qotrt_ptab = nullptr;',
     'extern QrtProcSlot *qotrt_ptab;'),
    ('static int32_t qotrt_ptab_cap = 0;',
     'extern int32_t qotrt_ptab_cap;'),
    ('static int32_t qotrt_ptab_free = -1;',
     'extern int32_t qotrt_ptab_free;'),
    ('static QrtFfiLibSlot *qotrt_ffitab = nullptr;',
     'extern QrtFfiLibSlot *qotrt_ffitab;'),
    ('static int32_t qotrt_ffitab_cap = 0;',
     'extern int32_t qotrt_ffitab_cap;'),
    ('static int32_t qotrt_ffitab_free = -1;',
     'extern int32_t qotrt_ffitab_free;'),
    ('static QrtFfiSymSlot *qotrt_fsymtab = nullptr;',
     'extern QrtFfiSymSlot *qotrt_fsymtab;'),
    ('static int32_t qotrt_fsymtab_cap = 0;',
     'extern int32_t qotrt_fsymtab_cap;'),
    ('static int32_t qotrt_fsymtab_free = -1;',
     'extern int32_t qotrt_fsymtab_free;'),
    ('static char qotrt_ffi_err[256];',
     'extern char qotrt_ffi_err[256];'),
    ('static QrtSockSlot *qotrt_stab = nullptr;',
     'extern QrtSockSlot *qotrt_stab;'),
    ('static int32_t qotrt_stab_cap = 0;',
     'extern int32_t qotrt_stab_cap;'),
    ('static int32_t qotrt_stab_free = -1;',
     'extern int32_t qotrt_stab_free;'),
]


def patch(path):
    with io.open(path, 'r', encoding='utf-8', newline='') as f:
        src = f.read()
    # 保持原换行风格（探测）
    nl = '\r\n' if '\r\n' in src else '\n'
    lines = src.split('\n')
    hits = 0
    for old, ext in SITES:
        idx = [i for i, ln in enumerate(lines) if ln.rstrip('\r') == old]
        if len(idx) != 1:
            print('  [FAIL] %s 匹配 %d 次: %s' % (path, len(idx), old[:60]))
            sys.exit(2)
        i = idx[0]
        eol = '\r' if lines[i].endswith('\r') else ''
        block = [
            '#ifdef QOT_RT_SPLIT',
            ext + eol,
            '#else',
            old + eol,
            '#endif',
        ]
        lines[i:i + 1] = block
        hits += 1
    out = '\n'.join(lines)
    with io.open(path, 'w', encoding='utf-8', newline='') as f:
        f.write(out)
    print('  [OK] %s：%d 处，换行风格 %r' % (path, hits, nl))


if __name__ == '__main__':
    print('== runtime 头切分手术（%d 个状态位） ==' % len(SITES))
    for p in PATHS:
        patch(p)
    # 术后一致性：两份副本必须逐字节一致
    a = io.open(PATHS[0], 'rb').read()
    b = io.open(PATHS[1], 'rb').read()
    print('副本一致: %s' % (a == b))
    assert a == b
