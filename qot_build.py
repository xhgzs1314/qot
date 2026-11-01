# -*- coding: utf-8 -*-
"""qot_build —— 编译管线 CI 的 L1（增量）与 L3（常驻/并行）编排层。

职责（ §9.4）：
  L1 · TU 切分与增量：模块 DAG（require_inliner 被动登记）→ 全程序 LIR（与
      单 TU **同一条语义管线**，G1 的最强形态）→ generate_split 多 TU 发射
      → 每 TU 对象缓存（内容寻址）→ 增量链接（对象集 key → exe 缓存）。
      "导出面指纹"由 **TU 的 C 文本 hash** 承担：严格比名字/签名级指纹精确
      （LIR 内联体已物理进入下游 TU 的 C 文本时，下游必须重编——这是正确性
      要求而不是缓存缺陷）。
  L2 · 前端缓存：合并源 + 开关 + 管线指纹 → C 文本包（纯文本，**禁止
      pickle Python 对象** —— §9.4 L2 策略约束）。
  L3 · 并行与常驻：-j N（TU 级并行编译，ThreadPool + g++ 子进程）；
      watch 常驻（热解释器 + 全层缓存，stdin 命令面 build/stats/gc/stop）。

分层与回滚：每层独立可关（QOT_CACHE=off 一键全冷；QOT_CACHE=pch 只开 PCH）。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor

import qot_cache as _qc

__all__ = [
    'frontend_bundle', 'tu_bundle', 'build_split', 'watch',
    'closure_fingerprint', 'netlibs_for_platform',
]


def netlibs_for_platform():
    """P0.3 网络原语的链接项（与 index.py 既有 bat 口径一致）。"""
    return ['-lws2_32'] if os.name == 'nt' else []


# ---------------------------------------------------------------- L2 前端缓存

def _frontend_key(merged_text: str, source_file: str, split: bool,
                  enable_opt: bool) -> tuple:
    comps = {
        'semver': _qc.SEMANTIC_VERSION,
        'form': 'split-bundle' if split else 'single-c',
        'merged': _qc.text_hash(merged_text),
        'source': os.path.abspath(source_file).replace('\\', '/'),
        'switches': _qc.switch_fingerprint(),
        'pipeline': _qc.pipeline_fingerprint(),
        'builtins_toml': _builtins_hash(),
        'opt': bool(enable_opt),
    }
    return _qc._key_of(comps), comps


def _builtins_hash() -> str:
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'builtins.toml')
    try:
        return _qc.file_hash(p)
    except OSError:
        return 'absent'


def frontend_bundle(merged_text: str, source_file: str, split: bool,
                    enable_opt: bool, compute_fn):
    """L2 · 前端产物缓存。compute_fn() → 单 TU 的 C 文本 或 split 的 bundle dict。

    命中 → 直接返回缓存文本（跳过 parse/bytecode/LIR/优化/cgen 全链）。
    未命中 → compute_fn() 计算并以**纯文本 JSON**落 CAS（禁 pickle：解释器
    类定义一变，反序列化出来的就是"看起来合法、实则错编"的东西 —— §9.4 L2）。
    返回 (payload, hit: bool)。
    """
    if not _qc.enabled_for('frontend'):
        return compute_fn(), False
    key, comps = _frontend_key(merged_text, source_file, split, enable_opt)
    _qc.explain_note('frontend:%s' % os.path.abspath(source_file),
                     'frontend', comps)
    obj = _qc._cas_get(key)
    if obj is not None:
        try:
            with open(obj, 'r', encoding='utf-8') as f:
                payload = json.load(f)
            _qc.record_stats('frontend', True)
            return payload, True
        except Exception:
            pass  # 损坏的缓存条目按 miss 处理（重建覆盖）
    _qc.record_stats('frontend', False)
    payload = compute_fn()
    data = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    tmp = tempfile.NamedTemporaryFile(dir=_qc.store_root(), prefix='fe_',
                                      delete=False, suffix='.json')
    try:
        tmp.write(data)
        tmp.close()
        _qc._cas_put(key, tmp.name, comps)
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
    return payload, False


# ---------------------------------------------------------------- L2.5 TU 记忆化（CP-C5）

def _lir_digest(f):
    """LirFunction 的结构摘要（sha256 hex）+ 其引用的常量池下标集合。

    覆盖 stage-2（窥孔/SSA/arena/cgen —— 均为逐函数 pass）可见的全部输入：
    函数头元数据（槽数/名字表/捕获/env 形态）与每条指令的全字段。
    返回 (digest, const_idx_set)；const_idx_set 供 TU key 的常量切片。
    """
    import hashlib
    from lir import CONST_VAL
    h = hashlib.sha256()
    u = h.update

    def _s(x):
        u(repr(x).encode('utf-8', 'replace'))
        u(b'\n')

    _s(('F', f.name, f.kind, f.n_slots_v, f.n_slots_f, f.n_bind_slots,
        list(f.req_names or []),
        list(getattr(f, 'name_table', []) or []),
        list(getattr(f, 'cap_names', []) or []),
        sorted((getattr(f, 'slot_forms', {}) or {}).items()),
        bool(getattr(f, 'aggressive', False)),
        bool(getattr(f, 'clo_form', False))))   # M4：5 参签名影响发射文本
    cset = set()
    for i in f.instrs:
        _s(('I', i.op, i.dst, i.a, i.b, i.c, i.imm, i.pred, i.target,
            i.func_idx, i.argc, list(i.args or []), i.name_idx, i.str_idx,
            bool(i._no_inline)))
        if i.op == CONST_VAL:
            cset.add(i.a)
    return h.hexdigest(), cset


def _objmeta_tag(prog):
    """【M4】对象类型表的确定性指纹（主 TU 发射 qotrt_types/foff/mfns）。

    与 _pool_tag 同一纪律：只含值语义字段（类名/字段序/方法映射/fid/mid），
    空表 = None（无类程序不进 key，避免老程序 key 漂移）。
    """
    meta = getattr(prog, 'objmeta', None)
    if not meta or not meta.get('classes'):
        return None
    classes = []
    for cname, fields, methods in meta['classes']:
        classes.append((cname, list(fields or []),
                        sorted((methods or {}).items())))
    return (tuple(classes),
            tuple(sorted((meta.get('field_ids') or {}).items())),
            tuple(sorted((meta.get('method_ids') or {}).items())),
            int(meta.get('nfields') or 0), int(meta.get('nmethods') or 0))


def _stage2_subprog(prog, fn_pred):
    """构造仅含 fn_pred(f) 为真的函数的子程序（共享 LirFunction 对象 →
    pass 原地特化）。全局分析（infer/inline/promote）已在 stage-1 完成，
    stage-2 三 pass 均为逐函数纯函数 —— 子程序上的结果与全程序一致（G1）。"""
    import lir
    sub = lir.LirProgram()
    sub.main = prog.main if fn_pred(prog.main, True) else lir.LirFunction('', 'main')
    for k, f in prog.funcs.items():
        if fn_pred(f, False):
            sub.funcs[k] = f
    sub.aggressive = getattr(prog, 'aggressive', False)
    if getattr(prog, 'const_pool', None) is not None:
        sub.const_pool = prog.const_pool
    if getattr(prog, 'fast_snapshots', None) is not None:
        sub.fast_snapshots = prog.fast_snapshots
    return sub


def _tu_run_stage2(sub):
    """子程序上的 stage-2（与 index._aot_compile 全程序顺序逐一致：
    optimize_f64 → ssa_regalloc → arena_loops）。"""
    from lir_opt import optimize_f64, arena_loops
    import lir_ssa
    optimize_f64(sub)
    lir_ssa.ssa_regalloc(sub)
    arena_loops(sub)


def _pool_tag(v):
    """常量池条目的确定性指纹（与 cgen 的值依赖面**逐分支镜像**）：
    bool/num/str 的值影响发射文本；VMCode（函数值）/None 一律发射
    qotrt_null_v() —— 与身份无关（repr 带内存地址会让 key 每次编译漂移，
    TU 记忆化静默失效 —— CP-C5 开发中实测抓到，此为修复）。"""
    if isinstance(v, bool):
        return 'b:%r' % v
    if isinstance(v, (int, float)):
        return 'n:%r' % v
    if isinstance(v, str):
        return 's:%r' % v
    return 'null'


def tu_bundle(prog, source_file, snaps, module_of, enable_opt):
    """CP-C5 · 模块级前端缓存：stage-1 后的 prog → split bundle（按 TU 记忆化）。

    管线拆分（实测 48 模块：stage-1 ≈ 1.7s 全量必付，stage-2 ≈ 5.2s 可缓存）：
      stage-1（全局分析，每次全量）：merge/parse/ast/bytecode/lower/inline/
        promote/snapshot/infer —— 跨函数事实（内联体、F64 全局解、全局槽）
        在此落定进每个函数的 LIR；
      stage-2（逐函数纯函数，按 TU 缓存）：窥孔/去装箱/f 合并 + SSA 槽合并 +
        arena 插桩 + cgen 发射。

    TU key 的完备性（K2 纪律）：TU 文本由且仅由以下决定 ——
      * 该 TU 各函数的 stage-1 后 LIR（快照摘要 + post-infer 摘要，双保险：
        快照供 N-04 快入口、post-infer 供慢体；infer 不保证单射，两份都进 key）；
      * 该 TU 函数引用的常量池切片 [(idx, 值 repr)]（主 TU 为全池 —— 它要
        emit_consts 全量落地）；常量值变化/下标漂移都改变切片；
      * 闭包表布局（全局按名排序定下标 —— 别的模块加闭包会平移下标）；
      * 全局槽上界（主 TU 的 g[N] 定义）；
      * 开关指纹/管线指纹/semver/源路径（主 TU 注释内嵌 source 路径）。
    返回 generate_split 同构的 bundle dict。缓存关（QOT_CACHE=off/pch）时
    走全程序 stage-2 + generate_split（冷路径对照，K1 的冷臂）。
    """
    from lir_cgen import prepare_split, emit_split_tu, generate_split
    if not _qc.enabled_for('frontend'):
        _tu_run_stage2(prog)
        return generate_split(prog, source_file, fast_snaps=snaps,
                              module_of=module_of)

    ctx = prepare_split(prog, source_file, fast_snaps=snaps,
                        module_of=module_of)
    pool = list(getattr(prog, 'const_pool', []) or [])
    clo = sorted(ctx.cg.clo_index)
    gbound = max(1, ctx.cg._global_upper_bound())
    snaps_map = snaps or {}

    # N-04 快入口的全局 arity 面：FastPlanner 按**全程序调用点**收集
    # (函数名, argc) —— 别的模块增删对 F 的调用会改变 F 所在 TU 的快入口
    # 集合（而 F 自身 LIR 不变）。该映射必须进所有 TU 的 key（K2）。
    from lir import CALL_USER
    call_sites = set()
    for f in prog.all_funcs():
        nt = getattr(f, 'name_table', None) or []
        for i in f.instrs:
            if i.op == CALL_USER and 0 <= i.func_idx < len(nt):
                call_sites.add((nt[i.func_idx], i.argc))

    base = {
        'semver': _qc.SEMANTIC_VERSION,
        'form': 'tu',
        'switches': _qc.switch_fingerprint(),
        'pipeline': _qc.pipeline_fingerprint(),
        'builtins_toml': _builtins_hash(),
        'opt': bool(enable_opt),
        'clo': clo,
        'call_sites': sorted(call_sites),
        # 【M4 0】对象类型表（主 TU 发射 qotrt_types；FIELD ops 的
        # fid/mid 立即数在各函数 digest 里，但表内容本身只在主 TU 文本里）
        'objmeta': _objmeta_tag(prog),
    }

    def _tu_funcs(tu, is_main):
        """[(name, LirFunction)]：该 TU 的用户函数（+ 主 TU 的 main）。"""
        out = []
        for f in prog.funcs.values():
            if ctx.fn_tu.get(id(f), ctx.main_tu) == tu:
                out.append((f.name, f))
        if is_main:
            out.append(('<main>', prog.main))
        return out

    def _tu_key(tu, is_main):
        comps = dict(base)
        comps['tu'] = tu
        cset = set()
        fparts = []
        for nm, f in _tu_funcs(tu, is_main):
            d, cs = _lir_digest(f)
            cset |= cs
            snap = snaps_map.get(nm) if nm != '<main>' else None
            sd, _ = _lir_digest(snap) if snap is not None else (None, None)
            fparts.append([nm, d, sd])
        comps['funcs'] = fparts
        if is_main:
            # 主 TU：全常量池（emit_consts）+ g[N] 上界 + 内嵌源路径
            comps['pool'] = [_pool_tag(v) for v in pool]
            comps['gbound'] = gbound
            comps['src'] = os.path.abspath(source_file).replace('\\', '/')
        else:
            comps['pool'] = sorted([idx, _pool_tag(pool[idx])]
                                   for idx in cset if 0 <= idx < len(pool))
        key = _qc._key_of(comps)
        return key, comps

    def _memo_get(key, comps):
        _qc.explain_note('tu:%s' % comps.get('tu'), 'tu', comps)
        obj = _qc._cas_get(key)
        if obj is None:
            return None
        try:
            with open(obj, 'r', encoding='utf-8') as f:
                import json as _json
                d = _json.load(f)
            return d.get('text')
        except Exception:
            return None   # 损坏条目按 miss 处理

    def _memo_put(key, comps, text):
        import json as _json
        data = _json.dumps({'text': text}, ensure_ascii=False).encode('utf-8')
        tmp = tempfile.NamedTemporaryFile(dir=_qc.store_root(), prefix='tu_',
                                          delete=False, suffix='.json')
        try:
            tmp.write(data)
            tmp.close()
            _qc._cas_put(key, tmp.name, comps)
        finally:
            try:
                os.unlink(tmp.name)
            except OSError:
                pass

    def _tu_text(tu, is_main):
        """单 TU：命中取缓存文本；未命中子程序 stage-2 + 发射 + 落缓存。"""
        key, comps = _tu_key(tu, is_main)
        text = _memo_get(key, comps)
        if text is not None:
            _qc.record_stats('tu', True)
            return text
        _qc.record_stats('tu', False)
        members = {id(f) for _, f in _tu_funcs(tu, is_main)}
        sub = _stage2_subprog(prog, lambda f, is_m: id(f) in members)
        _tu_run_stage2(sub)
        text = emit_split_tu(ctx, tu, is_main_tu=is_main)
        _memo_put(key, comps, text)
        return text

    main_src = _tu_text(ctx.main_tu, True)
    tus = []
    for tu in ctx.mod_order:
        src = _tu_text(tu, False)
        if src.strip():
            tus.append((tu, src))
    module_map = {f.name: ctx.fn_tu.get(id(f), ctx.main_tu)
                  for f in prog.funcs.values() if f.name}
    return {'header': ctx.header, 'main': main_src, 'tus': tus,
            'module_map': module_map}



def _write_text(path: str, text: str):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        f.write(text)


# ---------------------------------------------------------------- L1 切分构建

def build_split(bundle: dict, out_exe: str, *, rt_src: str, keep_c: bool = False,
                build_dir: str = None, jobs: int = None, timeout: int = 300,
                want_stats: dict = None) -> dict:
    """L1 · bundle（generate_split 产物）→ exe。

    布局：qot_parts.h 写入构建私有工作区（与 TU 同目录 → quoted include 就地
    解析；工作区随构建回收，无并发互踩），TU 文本本身不改写 → 对象 key 稳定。
    对象缓存（L0o）：TU 文本 + 共享头 hash → .o（未变的 TU 直接命中 ——
    "改一行付受影响 TU 之和"的来源）。链接缓存（L0l）：对象 hash 集合 → exe。
    并行（L3）：未命中的 TU 用 g++ 子进程并行编译（默认 jobs = min(4, cpu)）。
    返回 {'objects': n, 'compiled': n, 'link_hit': bool, 'elapsed': s}。
    """
    t0 = time.perf_counter()
    want_stats = want_stats if want_stats is not None else {}
    jobs = max(1, jobs or min(4, os.cpu_count() or 2))
    # 每次构建独立的临时工作区（TU 源 + qot_parts.h + 冷模式对象）；结束整体
    # 回收。qot_parts.h 与 TU 同目录 → quoted include 就地解析（工作区是本
    # 构建私有的，无并发互踩）；TU 文本本身不改写 → 对象 key 稳定。
    work_dir = tempfile.mkdtemp(prefix='split_%d_' % os.getpid(),
                                dir=_qc.store_root())
    cpp_dir = work_dir

    header = bundle.get('header') or ''
    tus = [('main', bundle['main'])] + list(bundle.get('tus') or [])
    if header:
        _write_text(os.path.join(work_dir, 'qot_parts.h'), header)
    header_hash = _qc.text_hash(header) if header else None
    # PCH 预热（锁内单次生成；线程池首任务不再各自排队等 PCH）
    _qc.ensure_pch(rt_src, None, split=True)

    if keep_c and build_dir:
        if header:
            _write_text(os.path.join(build_dir, 'qot_parts.h'), header)
        for name, text in tus:
            _write_text(os.path.join(build_dir, '%s_gen.cpp' % name), text)

    def compile_one(item):
        name, text = item
        cpp = os.path.join(cpp_dir, 'tu_%s.cpp' % name)
        _write_text(cpp, text)
        try:
            r = _qc.compile_object(cpp, '', rt_src=rt_src, split=True,
                                   tu_name=name, timeout=timeout,
                                   want_stats=want_stats,
                                   work_dir=work_dir,
                                   header_hash=header_hash)
            return name, r
        finally:
            try:
                os.remove(cpp)
            except OSError:
                pass

    compiled = 0
    obj_paths = []
    try:
        if len(tus) == 1:
            results = [compile_one(tus[0])]
        else:
            with ThreadPoolExecutor(max_workers=min(jobs, len(tus))) as ex:
                results = list(ex.map(compile_one, tus))
        for name, r in results:
            if not r.get('hit'):
                compiled += 1
            obj_paths.append(r['path'])

        link = _qc.link_objects(obj_paths, out_exe, rt_src=rt_src,
                                netlibs=netlibs_for_platform(),
                                timeout=timeout, want_stats=want_stats)
    finally:
        import shutil
        shutil.rmtree(work_dir, ignore_errors=True)
    return {'objects': len(obj_paths), 'compiled': compiled,
            'link_hit': link['hit'], 'elapsed': time.perf_counter() - t0}


# ---------------------------------------------------------------- 变更检测（L3）

def closure_fingerprint(source_file: str) -> tuple:
    """require 闭包的指纹：(路径 → (mtime_ns, size, sha256)) 映射。watch 主
    循环比对相邻两次指纹判定变更（内容 hash 杜绝"内容变了 mtime 没变"的
    假阴性；读缺失的文件记 None，闭包增删同样触发重建）。"""
    from require_inliner import RequireInliner
    r = RequireInliner()
    r.set_base_path(source_file)
    # 只走 DAG 发现（不合并文本）：读主文件 require 行，递归解析模块路径
    seen = {}
    stack = [os.path.abspath(source_file)]
    while stack:
        p = stack.pop()
        p = os.path.abspath(p)
        if p in seen:
            continue
        try:
            st = os.stat(p)
        except OSError:
            seen[p] = None
            continue
        try:
            with open(p, 'r', encoding='utf-8') as f:
                content = f.read()
            h = _qc.text_hash(content)
        except OSError:
            seen[p] = None
            continue
        seen[p] = (st.st_mtime_ns, st.st_size, h)
        for _ln, _raw, mod in r.extract_require_lines(content):
            if mod.lower().endswith('.dll'):
                continue
            resolved = r.resolve_qot_path(mod, p)
            if resolved:
                stack.append(resolved)
    return seen


def _fp_equal(a, b) -> bool:
    if a is None or b is None:
        return a is b
    return a[0] == b[0] and a[1] == b[1] and a[2] == b[2]


# ---------------------------------------------------------------- L3 watch

def watch(source_file: str, out_exe: str, *, interval: float = 0.5,
          rebuild_fn=None, quiet: bool = False):
    """L3 · 常驻构建守护：文件变化 → 增量重建（热解释器 + 全层缓存）。

    stdin 命令面（行协议）：build / stats / gc / stop。
    rebuild_fn(source_file, out_exe) 默认调 index._aot_compile（由调用方注入
    以避免循环导入）。
    """
    import threading

    if rebuild_fn is None:
        def rebuild_fn(src, exe):
            import index
            return index._aot_compile(src, exe, True)

    def _log(msg):
        if not quiet:
            print('[watch] %s' % msg, flush=True)

    stop = threading.Event()
    last_fp = None

    def _rebuild(reason):
        t0 = time.perf_counter()
        try:
            rebuild_fn(source_file, out_exe)
            _log('重建完成 %.2fs（%s）' % (time.perf_counter() - t0, reason))
        except Exception as e:
            _log('重建失败: %s' % e)
        _qc.flush_stats()

    def _stdin_loop():
        try:
            for line in sys.stdin:
                cmd = line.strip().lower()
                if cmd == 'build':
                    _rebuild('命令')
                elif cmd == 'stats':
                    print(_qc.format_stats(), flush=True)
                elif cmd == 'gc':
                    print(json.dumps(_qc.gc_cache(), ensure_ascii=False), flush=True)
                elif cmd == 'stop':
                    stop.set()
                    return
                elif cmd:
                    _log('未知命令 %r（可用：build/stats/gc/stop）' % cmd)
        except Exception:
            pass

    threading.Thread(target=_stdin_loop, daemon=True).start()
    _rebuild('启动')
    while not stop.is_set():
        try:
            fp = closure_fingerprint(source_file)
        except Exception as e:
            fp = None
            _log('指纹失败: %s' % e)
        if fp is not None and last_fp is not None and fp != last_fp:
            changed = [p for p in fp if not _fp_equal(fp.get(p), last_fp.get(p))]
            added = [p for p in fp if p not in last_fp]
            gone = [p for p in last_fp if p not in fp]
            why = []
            if changed:
                why.append('变更 %d' % len(changed))
            if added:
                why.append('新增 %d' % len(added))
            if gone:
                why.append('移除 %d' % len(gone))
            _rebuild('；'.join(why) or '变化')
            fp = closure_fingerprint(source_file)
        if fp is not None:
            last_fp = fp
        stop.wait(interval)
    _log('退出')
    return 0
