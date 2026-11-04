# -*- coding: utf-8 -*-
"""qot_cache —— 编译管线 CI 的 L0 层

工具链调用缓存（ccache 语义）+ 预编译头（PCH）+ 内容寻址存储（CAS）+ 统计/
解释/GC。**不懂 QOT 语义**：输入是"C 源文本 + 编译身份"，输出是"工具链产物"。

正确性铁律：
  K1 缓存只能加速，不能改变输出 —— 命中 = 交付上次产物的副本；命中与冷编
     产物的逐字节等价由 testing 的 ci_cache_equivalence 用例交叉断言。
  K2 所有影响输出的输入必须进 key —— 组件表 COMPONENTS 机械清点（见
     toolchain_key），漏一项 = 等价性用例会当场抓住。
  K3 确定性是前置证 —— 所有 g++ 链接带 -Wl,--no-insert-timestamp（PE 时间戳
     归零，0 实测根除；ELF 侧加 -Wl,--build-id=none）。
  K4 未命中可解释、失灵可见 —— stats/每层命中计数 + explain（组件级 diff）；
     PCH 被 g++ 拒绝 = 硬报错（-Winvalid-pch 输出检测），不许静默退化。
  K5 逃生 —— QOT_CACHE=off 一键全冷（连 PCH 一起关，等价性用例的冷臂）。

目录布局：
  <root>/objects/<hash[:2]>/<hash>     内容寻址产物（exe/.o/C 文本）
  <root>/objects/<hash[:2]>/<hash>.key.json   key 组件（explain 与审计用）
  <root>/pch/<pchkey>/qot_aot_runtime.h{,.gch}
  <root>/stats.json                    各层命中/未命中计数
  <root>/slots/<slothash>.json         --explain 的"上次 key 组件"存档
"""
from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time

__all__ = [
    'mode', 'store_root', 'enabled_for',
    'file_hash', 'text_hash', 'sha256_bytes',
    'compiler_id', 'switch_fingerprint', 'pipeline_fingerprint',
    'toolchain_key', 'compile_exe', 'compile_object', 'link_objects',
    'ensure_pch', 'deliver', 'run_toolchain',
    'record_stats', 'stats_snapshot', 'format_stats',
    'explain_note', 'gc_cache', 'store_size',
    'QOT_NO_SWITCHES', 'SEMANTIC_VERSION', 'CacheDisabled',
]

# ---------------------------------------------------------------- 常量

# 语义版本：任何"影响产物但没被 key 组件覆盖"的发射器/链接布局变化都必须
# bump 它（宁可多 bump 造成无害 miss，不可少 bump 造成假命中）。
SEMANTIC_VERSION = 'qot-ci/1'

# K2 · 18 个 QOT_NO_* / QOT_UNSAFE_INDEX 开关全集（_dbg_switches.py 同源）。
# 它们都会改变生成的 C 文本（间接也进 key），显式登记是为了 --explain 能
# 指认"哪个开关变了"，以及防止未来出现"影响工具链调用但不影响 C 文本"的
# 开关漏网。
QOT_NO_SWITCHES = (
    'QOT_NO_INLINE', 'QOT_NO_UNBOX', 'QOT_NO_PEEPH', 'QOT_NO_FOLD',
    'QOT_NO_FWD', 'QOT_NO_COPY', 'QOT_NO_DEAD', 'QOT_NO_PROMOTE',
    'QOT_NO_SSA', 'QOT_NO_FCOAL', 'QOT_NO_FCALL', 'QOT_NO_BICORE',
    'QOT_NO_APPEND', 'QOT_NO_IDXFUSE', 'QOT_NO_ARENA', 'QOT_NO_ALIAS',
    'QOT_NO_CONSTKEY', 'QOT_UNSAFE_INDEX',
    # PROMAX（0）：新增 A/B 开关（类型不变量守卫 / 自制跳转原语回落）
    'QOT_NO_TGUARD', 'QOT_NO_FAST_SJ',
    # FE4（2026-11-04）：native f 域寄存器分配回落（lir_native.py 自检）
    'QOT_NO_FRA',
)

# 影响 AOT 前端语义的源文件清单（L2 前端缓存 key 的一部分；改一个文件 =
# 全体 miss，宁可错杀）。builtins.toml 单列（内容 hash 进 key）。
PIPELINE_FILES = (
    'grammar.py', 'token_char.py', 'astcomopt.py', 'bytecode.py',
    'builtin_func.py', 'require_inliner.py', 'error_system.py',
    'lir.py', 'lir2.py', 'lir_lower.py', 'lir_opt.py', 'lir_ssa.py',
    'lir_cgen.py', 'qot_hash.py', 'qot_builtins.py', 'qot_toml.py',
    'qot_cache.py', 'qot_build.py', 'lir_native.py',
)

_HERE = os.path.dirname(os.path.abspath(__file__))

_STAT_LOCK = threading.Lock()
_COMPILER_ID = None
_SWITCH_FP = None
_PIPELINE_FP = None


class CacheDisabled(Exception):
    """QOT_CACHE=off（K5 全冷）。调用方应走无缓存路径。"""


# ---------------------------------------------------------------- 模式与根

def mode() -> str:
    """QOT_CACHE = on（默认）| off（K5 一键全冷）| pch（只开 PCH，不缓存产物）。"""
    return (os.environ.get('QOT_CACHE') or 'on').strip().lower() or 'on'


def store_root() -> str:
    """CAS 根目录。QOT_CACHE_DIR 可覆盖；默认 ~/.qot/cache（绝不在源码树内）。"""
    root = os.environ.get('QOT_CACHE_DIR')
    if not root:
        root = os.path.join(os.path.expanduser('~'), '.qot', 'cache')
    return root


def enabled_for(layer: str) -> bool:
    m = mode()
    if m == 'off':
        return False
    if m == 'pch':
        return layer == 'pch'
    return True


# ---------------------------------------------------------------- 哈希

def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def file_hash(path: str) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------- 身份

def compiler_id() -> dict:
    """编译器身份（K2：路径 + 版本串都要进 key）。进程内缓存一次。"""
    global _COMPILER_ID
    if _COMPILER_ID is not None:
        return dict(_COMPILER_ID)
    exe = shutil.which('g++') or shutil.which('gcc')
    if exe is None:
        # MSVC 回退路径：cl 的版本探测昂贵且少用；以路径 + mtime 充当身份
        cl = shutil.which('cl')
        if cl is None:
            for c in (r'C:\Program Files\Microsoft Visual Studio\2022',
                      r'C:\Program Files\Microsoft Visual Studio\2019'):
                if os.path.isdir(c):
                    vs = os.path.join(c, sorted(os.listdir(c))[-1])
                    cand = os.path.join(vs, 'VC', 'Tools', 'MSVC')
                    if os.path.isdir(cand):
                        v = sorted(os.listdir(cand))[-1]
                        cl = os.path.join(cand, v, 'bin', 'Hostx64', 'x64', 'cl.exe')
                        break
        if cl is None:
            _COMPILER_ID = {'kind': 'none', 'path': '', 'version': ''}
            return dict(_COMPILER_ID)
        _COMPILER_ID = {'kind': 'cl', 'path': os.path.normcase(os.path.abspath(cl)),
                        'version': str(int(os.path.getmtime(cl)))}
        return dict(_COMPILER_ID)
    try:
        r = subprocess.run([exe, '--version'], capture_output=True,
                           text=True, errors='replace', timeout=30)
        ver = (r.stdout or r.stderr or '').strip().splitlines()[0][:200] if \
            (r.stdout or r.stderr) else ''
    except Exception:
        ver = ''
    _COMPILER_ID = {'kind': 'gcc', 'path': os.path.normcase(os.path.abspath(exe)),
                    'version': ver}
    return dict(_COMPILER_ID)


def switch_fingerprint() -> dict:
    """K2：18 个开关 + 清单路径的环境快照（值原样进 key）。"""
    global _SWITCH_FP
    if _SWITCH_FP is None:
        fp = {k: os.environ.get(k, '') for k in QOT_NO_SWITCHES}
        fp['QOT_BUILTINS_TOML'] = os.environ.get('QOT_BUILTINS_TOML', '')
        fp['QOT_MAX_DEPTH'] = os.environ.get('QOT_MAX_DEPTH', '')
        fp['QOT_TIMEOUT_MS'] = os.environ.get('QOT_TIMEOUT_MS', '')
        # 静态链接形态（也进 flags → key；进指纹是 K2 的双保险）
        fp['QOT_STATIC'] = os.environ.get('QOT_STATIC', '1')
        # R0 · dev 档（也进 flags → key；登记同 QOT_STATIC 双保险口径）
        fp['QOT_AOT_DEV'] = os.environ.get('QOT_AOT_DEV', '')
        fp['QOT_AOT_DEV_INLINE'] = os.environ.get('QOT_AOT_DEV_INLINE', '')
        # QOTNative（刀1）：后端选择（native | cpp 缺省）—— 影响产物形态，
        # 进指纹防串味（两后端缓存互不可见）
        fp['QOT_AOT_BACKEND'] = os.environ.get('QOT_AOT_BACKEND', '')
        _SWITCH_FP = fp
    return dict(_SWITCH_FP)


def pipeline_fingerprint() -> str:
    """前端/中端源文件指纹（L2 用）。任何管线源变化 → 前端缓存全体 miss。"""
    global _PIPELINE_FP
    if _PIPELINE_FP is None:
        h = hashlib.sha256()
        h.update(SEMANTIC_VERSION.encode('utf-8'))
        for rel in PIPELINE_FILES:
            p = os.path.join(_HERE, rel)
            if os.path.exists(p):
                h.update(rel.encode('utf-8'))
                h.update(b'\0')
                h.update(file_hash(p).encode('ascii'))
                h.update(b'\0')
            else:
                h.update(rel.encode('utf-8'))
                h.update(b'\0absent\0')
        _PIPELINE_FP = h.hexdigest()
    return _PIPELINE_FP


# ---------------------------------------------------------------- key

def _component_hash(value) -> str:
    """key 组件 → 稳定串。dict/list 走 JSON（排序键），其余 str/repr。"""
    if isinstance(value, str):
        return value
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


def toolchain_key(*, c_text: str = None, c_path: str = None,
                  rt_hash: str = None, flags, form: str = 'exe',
                  extra: dict = None) -> tuple:
    """K2 · 工具链层 key。返回 (key_hash, components_dict)。

    组件（机械清点，逐项对 §9.3-K2）：
      csrc      生成 C 的内容 hash（不是文件名/时间戳）
      rt        qot_aot_runtime.h 内容 hash
      flags     工具链 flags 全串（含 PCH 的 -I 与 -D、确定性链接 flag）
      compiler  编译器路径 + 版本串
      form      目标形态（exe | obj | link）
      switches  18 个 QOT_NO_* + 清单/限额环境变量全集
      semver    本层语义版本（SEMANTIC_VERSION）
      extra     调用方附加组件（如 link 的对象 hash 列表、TU 名）
    """
    if c_text is None and c_path is not None:
        c_text = _read_text(c_path)
    comps = {
        'semver': SEMANTIC_VERSION,
        'form': form,
        'compiler': compiler_id(),
        'flags': ' '.join(str(f) for f in flags),
        'switches': switch_fingerprint(),
    }
    if c_text is not None:
        comps['csrc'] = text_hash(c_text)
    if rt_hash is not None:
        comps['rt'] = rt_hash
    if extra:
        comps['extra'] = {str(k): _component_hash(v) for k, v in extra.items()}
    return _key_of(comps), comps


def _key_of(comps: dict) -> str:
    return sha256_bytes(_component_hash(comps).encode('utf-8'))


def _read_text(path: str) -> str:
    with open(path, 'r', encoding='utf-8', newline='') as f:
        return f.read()


# ---------------------------------------------------------------- CAS 原语

def _atomic_write_bytes(path: str, data: bytes):
    d = os.path.dirname(path)
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix='.tmp_')
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(data)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _atomic_write_json(path: str, obj):
    _atomic_write_bytes(path,
                        json.dumps(obj, ensure_ascii=False, indent=1,
                                   sort_keys=True).encode('utf-8'))


def _cas_object_path(key: str) -> str:
    return os.path.join(store_root(), 'objects', key[:2], key)


def _cas_put(key: str, data_path: str, comps: dict):
    """内容寻址写入：产物 + key 组件 sidecar（原子；已存在 = 幂等）。"""
    dst = _cas_object_path(key)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    if not os.path.exists(dst):
        # 硬链接优先（同卷零拷贝）；跨卷回退 copy
        try:
            os.link(data_path, dst)
        except OSError:
            try:
                _copy_with_retry(data_path, dst)
            except OSError:
                if not os.path.exists(dst):
                    raise
    kp = dst + '.key.json'
    if not os.path.exists(kp):
        try:
            _atomic_write_json(kp, {'key': key, 'components': comps,
                                    'stored_at': time.time()})
        except OSError:
            pass  # sidecar 是审计/加速件，失败不阻断（K4 由 stats 兜底）


def _cas_get(key: str) -> str:
    p = _cas_object_path(key)
    return p if os.path.exists(p) else None


def _copy_with_retry(src: str, dst: str, attempts: int = 5):
    """AV/索引器瞬时锁定下的拷贝（Windows 常态）。"""
    last = None
    for _ in range(attempts):
        try:
            shutil.copyfile(src, dst)
            return
        except PermissionError as e:
            last = e
            time.sleep(0.4)
    raise last


def deliver(src: str, dst: str, remove_first: bool = True):
    """产物交付：硬链接优先，跨卷/失败回退拷贝（AV 重试）。"""
    if remove_first and os.path.exists(dst):
        try:
            os.remove(dst)
        except PermissionError:
            time.sleep(0.4)
            os.remove(dst)
    d = os.path.dirname(dst)
    if d:
        os.makedirs(d, exist_ok=True)
    try:
        os.link(src, dst)
        return
    except OSError as e:
        if e.errno not in (errno.EXDEV, errno.EPERM, errno.EACCES,
                           errno.EEXIST, errno.ENOTSUP, errno.EMLINK):
            # 未知失败也要兜底拷贝（正确性优先）
            pass
    _copy_with_retry(src, dst)


# ---------------------------------------------------------------- 工具链

def determinism_link_flags() -> list:
    """K3 · 链接确定性 flags。PE 时间戳归零（MinGW ld 实测根除秒级抖动）；
    ELF 侧显式关 build-id（部分发行版默认开启）。MSVC 用 /Brepro。"""
    if os.name == 'nt':
        return ['-Wl,--no-insert-timestamp']
    return ['-Wl,--build-id=none']


def run_toolchain(cmd, cwd=None, timeout=300):
    """直接 spawn 工具链（不经 cmd/bat —— 那是 MSVC 环境初始化的遗留）。
    返回 CompletedProcess。"""
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                          errors='replace', timeout=timeout)


class _ToolchainFailure(Exception):
    def __init__(self, msg, proc=None):
        super().__init__(msg)
        self.proc = proc


def _check_output(proc, out_path, context, keep_tail=900):
    # 三重判据：rc==0 + 产物存在 + 非空（mkstemp 会预创建空文件，编译失败
    # 时"文件存在"是假阳性 —— 曾因此把 0 字节对象存进 CAS）
    ok = (proc.returncode == 0) and os.path.exists(out_path) \
        and os.path.getsize(out_path) > 0
    if not ok:
        msg = (proc.stderr or proc.stdout or '')[-keep_tail:]
        raise _ToolchainFailure('%s失败(rc=%s):\n%s'
                                % (context, proc.returncode, msg), proc)
    err = (proc.stderr or '')
    if err and re.search(r'pch.*(invalid|reject|created|cannot)|'
                         r'created by (a )?different', err, re.I):
        # K4：PCH 被拒 = key 计算或 flags 与 PCH 生成不一致 —— 硬失败，
        # 不许"静默不用 PCH 继续"（那会让失效不可见）。
        raise _ToolchainFailure('%s: PCH 校验失败（flags/身份与 PCH 生成期不一致）:\n%s'
                                % (context, err[-600:]), proc)


# ---------------------------------------------------------------- PCH（L0）

def pch_flags_for(base_flags, split=False) -> list:
    """PCH 生成 flags = 编译 flags 的"影响 PCH 合法性"子集（顺序稳定）。
    R0：-O 级随 dev 档走（PCH 与使用侧 flags 必须同源，否则 -Winvalid-pch
    硬失败——K4 禁止静默退化，这里同步换级保证 PCH 合法性判定成立）。"""
    f = ['-std=c++17'] + _opt_level() + ['-finput-charset=utf-8']
    if split:
        f.append('-DQOT_RT_SPLIT')
    return f


_PCH_LOCK = threading.Lock()


def ensure_pch(rt_src: str, base_flags, split: bool = False) -> str:
    """确保 PCH 就绪，返回 include 目录（其内有 qot_aot_runtime.h 与 .gch）。

    key = runtime 头内容 hash + PCH flags + 编译器身份。仅在头内容/flags/
    编译器变化时重新生成（~3.6s 一次性）。返回 None = PCH 不可用（冷编）。
    并发安全：进程内锁（线程池里 4 个 TU 并行调它时只生成一份）。跨进程
    竞争由"内容为准 + 重试"的目录原子就位兜住。
    【教训 0】不做进程内路径 memo：隔离存储（QOT_CACHE_DIR 切换）
    下 memo 命中的是**已删除存储**的路径（ci_module_cache 单跑炸过）——
    每次 os.path.exists 的代价只是两次 stat，换绝对正确性。
    """
    if not os.path.exists(rt_src):
        return None
    rt_h = file_hash(rt_src)
    flags = pch_flags_for(base_flags, split=split)
    comps = {'rt': rt_h, 'flags': ' '.join(flags), 'compiler': compiler_id(),
             'semver': SEMANTIC_VERSION}
    key = _key_of(comps)
    pdir = os.path.join(store_root(), 'pch', key)
    gch = os.path.join(pdir, 'qot_aot_runtime.h.gch')
    hdr = os.path.join(pdir, 'qot_aot_runtime.h')
    if os.path.exists(gch) and os.path.exists(hdr):
        record_stats('pch', True)
        return pdir
    if not enabled_for('pch'):
        return None
    with _PCH_LOCK:
        if os.path.exists(gch) and os.path.exists(hdr):
            record_stats('pch', True)
            return pdir
        record_stats('pch', False)
        os.makedirs(store_root(), exist_ok=True)
        # os.replace 不建目标父目录 —— pch/ 必须先就位（隔离存储当场暴露过）
        os.makedirs(os.path.dirname(pdir), exist_ok=True)
        tmp = tempfile.mkdtemp(prefix='pch_', dir=store_root())
        try:
            thdr = os.path.join(tmp, 'qot_aot_runtime.h')
            _copy_with_retry(rt_src, thdr)
            r = run_toolchain([compiler_id()['path']] + flags +
                              ['-x', 'c++-header', '-o',
                               os.path.join(tmp, 'qot_aot_runtime.h.gch'), thdr],
                              timeout=180)
            if r.returncode != 0 or not os.path.exists(
                    os.path.join(tmp, 'qot_aot_runtime.h.gch')):
                return None
            # 目录级原子就位：rename 失败的真实原因多半是 AV/索引器瞬时锁定
            # 54MB 的 .gch（并发竞争反而罕见）。不管哪种，**以内容为准**：
            # pdir 未见 gch+hdr 之前绝不承认就位（曾因 AV 锁返回空目录 →
            # -I 指向无头目录 → include 失败，K4 可见性原则禁止这种静默态）。
            ok = False
            for _attempt in range(5):
                if os.path.exists(gch) and os.path.exists(hdr):
                    ok = True
                    break
                if os.path.exists(pdir):
                    # 残缺 pdir（崩溃/AV 锁残留；合法就位必是原子 rename，缺
                    # 内容即残缺）→ 清掉再试。竞争窗口内若删掉他人刚就位的
                    # 同 key 内容，随后会以等价内容补回 —— 无损。
                    shutil.rmtree(pdir, ignore_errors=True)
                try:
                    os.replace(tmp, pdir)
                    continue
                except OSError:
                    time.sleep(0.5)
            if not ok:
                if not (os.path.exists(gch) and os.path.exists(hdr)):
                    shutil.rmtree(tmp, ignore_errors=True)
                    return None
            try:
                _atomic_write_json(os.path.join(pdir, 'pch.key.json'), comps)
            except OSError:
                pass
            shutil.rmtree(tmp, ignore_errors=True)  # 已被搬空/未就位的残壳
            return pdir
        except Exception:
            shutil.rmtree(tmp, ignore_errors=True)
            return None


# ---------------------------------------------------------------- 编译（L0 主面）

def _slot_path(slot: str) -> str:
    h = sha256_bytes(slot.encode('utf-8'))
    return os.path.join(store_root(), 'slots', h[:32] + '.json')


def explain_note(slot: str, layer: str, comps: dict) -> list:
    """K4 · --explain：与该 slot 上次 key 的组件级 diff。返回变更清单。"""
    sp = _slot_path(slot)
    try:
        with open(sp, 'r', encoding='utf-8') as f:
            prev = json.load(f)
    except Exception:
        prev = None
    changed = []
    if prev and 'components' in prev:
        pc = prev['components']
        for k in sorted(set(pc) | set(comps)):
            if k == 'extra':
                continue
            if _component_hash(pc.get(k)) != _component_hash(comps.get(k)):
                changed.append('%s: %s → %s'
                               % (k, _short(pc.get(k)), _short(comps.get(k))))
    try:
        os.makedirs(os.path.dirname(sp), exist_ok=True)
        _atomic_write_json(sp, {'slot': slot, 'layer': layer,
                                'components': comps, 'at': time.time()})
    except OSError:
        pass
    if changed:
        EXPLAIN_LOG.append((slot, layer, changed))
    return changed


# 进程内解释日志（--explain 打印面；单进程生命周期内有效）
EXPLAIN_LOG = []


def format_explain() -> str:
    if not EXPLAIN_LOG:
        return '（无 key 组件变化：本次构建各层均与上次同 key）'
    out = []
    for slot, layer, changed in EXPLAIN_LOG[-40:]:
        out.append('  [%s] %s' % (layer, '；'.join(changed)))
    return '\n'.join(out)


def clear_explain():
    del EXPLAIN_LOG[:]


def _short(v):
    s = _component_hash(v)
    return s if len(s) <= 48 else s[:45] + '...'


def compile_exe(c_path: str, out_exe: str, *, rt_src: str, keep_c: bool = False,
                netlibs=None, timeout: int = 300, slot: str = None,
                want_stats: dict = None) -> dict:
    """单 TU：C → exe（工具链调用缓存）。返回 {hit, sha, elapsed, pch}。

    命中：跳过 g++ spawn，交付 CAS 产物副本（G2 的来源）。
    未命中：确保 PCH → g++ 编译 → 产物入 CAS。
    模式内聚（K5 单点实现）：
      on  —— 全层缓存（PCH + 产物）；
      pch —— 只开 PCH（无产物缓存；冷编但加速头解析）；
      off —— 一键全冷（无 PCH；runtime 头拷回构建目录 = 既有单 TU 形态，
              这是等价性用例的"冷臂"）。
    """
    t0 = time.perf_counter()
    m = mode()
    if m == 'off':
        _compile_exe_cold(c_path, out_exe, rt_src=rt_src, pch_dir=None,
                          netlibs=netlibs, timeout=timeout, copy_header=True,
                          gui=uses_gui(c_path))
        return {'hit': False, 'sha': file_hash(out_exe),
                'elapsed': time.perf_counter() - t0, 'pch': False}
    rt_h = file_hash(rt_src)
    pch_dir = ensure_pch(rt_src, None, split=False)
    if m == 'pch':
        _compile_exe_cold(c_path, out_exe, rt_src=rt_src, pch_dir=pch_dir,
                          netlibs=netlibs, timeout=timeout, copy_header=False,
                          gui=uses_gui(c_path))
        return {'hit': False, 'sha': file_hash(out_exe),
                'elapsed': time.perf_counter() - t0, 'pch': bool(pch_dir)}
    flags = _exe_flags(pch_dir, netlibs, gui=uses_gui(c_path))
    key, comps = toolchain_key(c_path=c_path, rt_hash=rt_h, flags=flags,
                               form='exe')
    if slot:
        explain_note(slot, 'toolchain', comps)
    obj = _cas_get(key)
    if obj is not None:
        deliver(obj, out_exe)
        record_stats('toolchain', True)
        if want_stats is not None:
            want_stats['toolchain'] = 'hit'
        return {'hit': True, 'sha': key, 'elapsed': time.perf_counter() - t0,
                'pch': bool(pch_dir)}
    record_stats('toolchain', False)
    _compile_exe_cold(c_path, out_exe, rt_src=rt_src, pch_dir=pch_dir,
                      netlibs=netlibs, timeout=timeout, copy_header=False,
                      gui=uses_gui(c_path))
    _cas_put(key, out_exe, comps)
    if want_stats is not None:
        want_stats['toolchain'] = 'miss'
    return {'hit': False, 'sha': file_hash(out_exe),
            'elapsed': time.perf_counter() - t0, 'pch': bool(pch_dir)}


def dev_mode() -> bool:
    """R0 · dev 档（QOT_AOT_DEV=1|true）：本地迭代形态，速度优先。

    语义：
      -O 级 -O2 → -O1（g++ 对生成 C 的优化耗时大降，运行性能让步——
      dev 档明示放弃运行性能闸门，release（默认）维持 -O2 现状）；
      链接默认动态（libgcc/libstdc++ 随 MinGW 运行时分发）——静态链
      接的产物分发独立，但 ld 重定位全量静态库是冷编大头之一。
    该开关进 flags 与开关指纹（→ 进 key，K2）：dev/release 两形态各自
    缓存、互不串味；--explain 可指认"QOT_AOT_DEV 变了"。
    """
    v = os.environ.get('QOT_AOT_DEV', '')
    return v.strip().lower() in ('1', 'true', 'yes', 'on')


def _opt_level() -> list:
    """编译 O 级（单点定义；_exe/_obj/pch 三面同源）。

    R0 实测斜率（tpl_v6 生成 C，compile-only）：
      -O2 = 52.6s | -O1 = 27.2s | -O1 -fno-inline = 8.5s | -O0 ≈ 5s
    dev 档默认 -O0（业主裁决：优化全关）——唯一障碍是 runtime 头的
    qot_sjmp/qot_ljmp asm 原语与 -O0 帧指针冲突，已在原语函数级钉死
    optimize("O2")（runtime 头 R0-O0）后解除。
    QOT_AOT_DEV_INLINE=1 → -O1（dev 期要近似 release 运行性能时的中间档，
    仍无内联）；release（默认）维持 -O2 现状。
    """
    if dev_mode():
        if os.environ.get('QOT_AOT_DEV_INLINE', '').strip().lower() \
                in ('1', 'true', 'yes', 'on'):
            return ['-O1']
        return ['-O0']
    return ['-O2']


def static_link_enabled() -> bool:
    """静态链接开关（QOT_STATIC，默认 1 = 现状：产物无 DLL 依赖）。

    关掉（=0）→ 动态链接 libgcc/libstdc++：链接更快、产物更小，代价是分发
    需要带运行时 DLL（或目标机有 MinGW 运行时）。该设置进 flags（→ 进 key），
    两种形态各自缓存、互不串味（K2）。
    R0 · dev 档下默认翻转为动态（速度优先）；显式 QOT_STATIC=1 仍可强制
    静态（显式用户意志覆盖档位默认）。
    """
    if dev_mode():
        explicit = os.environ.get('QOT_STATIC', '').strip().lower()
        return explicit in ('1', 'true', 'yes', 'on')
    return os.environ.get('QOT_STATIC', '1').strip().lower() not in ('0', 'off', 'no', 'false')


def _static_link_flags() -> list:
    return ['-static', '-static-libgcc', '-static-libstdc++'] if static_link_enabled() else []



# ---------------------------------------------------------------- GUI（wingui）
# 程序用到 vg_* 时，链接 wingui 的目标文件与 Direct2D/DirectWrite 库。
# 不用 GUI 的程序不受影响（不付链接代价、也不改缓存键）。
# v2：DComp 渲染栈（d3d11/dxgi）+ DWM 窗口原语（dwmapi）+ WIC 图片；
# v3：+ shell32（文件拖放 DragAcceptFiles 系列）。
# dcomp.dll 动态解析，不进导入表（老系统自动回退 HwndRT 路径）。
# 注意：与 build_qotgui.py 的 LIBS、wingui/build.bat 三处保持同步。
GUI_LIBS = ['-ld2d1', '-ldwrite', '-lole32', '-luuid', '-lcomctl32',
            '-limm32', '-lgdi32', '-luser32', '-ld3d11', '-ldxgi',
            '-ldwmapi', '-lwindowscodecs', '-lshell32']
_GUI_OBJS = None


def gui_objects():
    """wingui 的目标文件（缺则现编一次；结果缓存在 build/）。"""
    global _GUI_OBJS
    if _GUI_OBJS is not None:
        return _GUI_OBJS
    root = os.path.dirname(os.path.abspath(__file__))
    srcdir = os.path.join(root, 'ot-asset', 'c', 'wingui')
    aotdir = os.path.join(root, 'ot-asset', 'c', 'aot')
    outdir = os.path.join(root, 'build')
    # qotrt_gui：vg_* 的 V16 包装（vg_* -> qg_* ABI）；其余三个是 wingui 本体
    names = ['qotgui_abi', 'modern_window', 'renderer', 'qotrt_gui']
    objs = []
    ok = True
    for n in names:
        src = os.path.join(srcdir, n + '.cpp')
        obj = os.path.join(outdir, 'gui_' + n + '.o')
        if not os.path.exists(src):
            ok = False
            break
        need = (not os.path.exists(obj)) or                os.path.getmtime(src) > os.path.getmtime(obj)
        if need:
            os.makedirs(outdir, exist_ok=True)
            cmd = ['g++', '-std=c++17', '-O2', '-w', '-I', srcdir,
                   '-I', aotdir, '-c', src, '-o', obj]
            r = subprocess.run(cmd, capture_output=True, text=True)
            if r.returncode != 0:
                ok = False
                break
        objs.append(obj)
    _GUI_OBJS = objs if ok else []
    return _GUI_OBJS


def uses_gui(c_path):
    """生成的 C 里是否引用了 qotrt_vg_*（决定是否接 GUI 目标与库）。"""
    try:
        with open(c_path, 'r', encoding='utf-8', errors='replace') as f:
            return 'qotrt_vg_' in f.read()
    except OSError:
        return False


# ---------------------------------------------------------------- QOTNative（刀1）
_RT_LIB = None


def ensure_runtime_lib() -> str:
    """QOTNative：qotrt.lib 预编（ot-asset/c/aot/qotrt_lib.cpp → build/qotrt_<hash>.lib）。

    runtime 头（header-only、函数全 static）经 qotrt_lib.cpp 的
    `#define static extern "C"` 宏翻转变 C ABI 导出面，一次编译终身复用 ——
    native 后端的 obj 直调其中 qotrt_* 符号，不再随程序重编 runtime。
    key = qotrt_lib.cpp + runtime 头内容 hash + 编译器身份（任一变化 → 重编）。
    返回 .lib 路径；失败抛 _ToolchainFailure。
    """
    global _RT_LIB
    if _RT_LIB is not None:
        return _RT_LIB
    root = os.path.dirname(os.path.abspath(__file__))
    cpp = os.path.join(root, 'ot-asset', 'c', 'aot', 'qotrt_lib.cpp')
    rt = os.path.join(root, 'ot-asset', 'c', 'aot', 'qot_aot_runtime.h')
    flags = ['-std=c++17', '-O2', '-finput-charset=utf-8', '-w']
    material = '|'.join([file_hash(cpp), file_hash(rt),
                         compiler_id().get('path', ''), ' '.join(flags),
                         SEMANTIC_VERSION])
    h = sha256_bytes(material.encode('utf-8'))[:16]
    build_dir = os.path.join(root, 'build')
    os.makedirs(build_dir, exist_ok=True)
    lib = os.path.join(build_dir, 'qotrt_%s.lib' % h)
    if not os.path.exists(lib) or os.path.getsize(lib) == 0:
        cid = compiler_id()
        obj = os.path.join(build_dir, 'qotrt_lib_%s.o' % h)
        ar = os.path.join(os.path.dirname(cid['path']), 'ar')
        if not os.path.exists(ar):
            ar = 'ar'
        p1 = run_toolchain([cid['path']] + flags + ['-c', cpp, '-o', obj],
                           timeout=600)
        if p1.returncode != 0 or not os.path.exists(obj):
            raise _ToolchainFailure(
                'qotrt.lib 编译失败(rc=%s):\n%s'
                % (p1.returncode, (p1.stderr or '')[-3000:]), p1)
        p2 = run_toolchain([ar, 'rcs', lib, obj], timeout=120)
        if p2.returncode != 0 or not os.path.exists(lib):
            raise _ToolchainFailure(
                'qotrt.lib 打包失败(ar rc=%s):\n%s'
                % (p2.returncode, (p2.stderr or '')[-800:]), p2)
        try:
            os.remove(obj)
        except OSError:
            pass
    _RT_LIB = lib
    return lib


def native_prog_get(merged_text: str, want_stats: dict = None):
    """QOTNative · 前端 prog CAS（K2 形态）：同源热编免全量 Python 前端。

    native 分支绕开 C 文本/frontend 文本缓存（它要的是 prog 对象不是 C
    文本）——代价是每次重编都全量跑 parse→bytecode→LIR 全管线（大程序
    ~6.5s）。本层把 (prog, snaps) pickle 进 CAS：key = 源文本内容 hash +
    管线指纹（PIPELINE_FILES 含 lir_native.py → 发射器改动 = 全 miss）+
    开关指纹 + semver。命中返回反序列化的 (prog, snaps)（pickle 保全部
    属性：name_table/objmeta/_src_op/_jt_cache），发射产物逐字节一致
    （K1 由 testing/unit/test_native_prog_pickle.py 断言）。
    返回 (prog, snaps) 或 None（miss/停用）。
    """
    if not enabled_for('frontend'):
        if want_stats is not None:
            want_stats['frontend'] = 'off'
        return None
    key, comps = toolchain_key(c_text=merged_text, flags=['native-prog'],
                               form='prog', extra={'backend': 'native',
                                                   'fmt': 'pickle-v1'})
    blob_path = _cas_get(key)
    if blob_path is None:
        record_stats('frontend', False)
        if want_stats is not None:
            want_stats['frontend'] = 'miss'
        return None
    import pickle
    try:
        with open(blob_path, 'rb') as f:
            bundle = pickle.load(f)
        prog, snaps = bundle['prog'], bundle['snaps']
    except Exception:
        # 缓存体损坏 = 当 miss（K4：可解释降级，不阻断）
        record_stats('frontend', False)
        if want_stats is not None:
            want_stats['frontend'] = 'corrupt'
        return None
    record_stats('frontend', True)
    if want_stats is not None:
        want_stats['frontend'] = 'hit'
    return prog, snaps


def native_prog_store(merged_text: str, prog, snaps) -> None:
    """QOTNative · 前端 prog CAS 写入（幂等；失败不阻断编译）。"""
    if not enabled_for('frontend'):
        return
    import pickle
    key, comps = toolchain_key(c_text=merged_text, flags=['native-prog'],
                               form='prog', extra={'backend': 'native',
                                                   'fmt': 'pickle-v1'})
    import tempfile
    fd, tmp = tempfile.mkstemp(suffix='.prog.pkl')
    try:
        with os.fdopen(fd, 'wb') as f:
            pickle.dump({'prog': prog, 'snaps': snaps}, f,
                        protocol=pickle.HIGHEST_PROTOCOL)
        _cas_put(key, tmp, comps)
    except Exception:
        pass  # CAS 写失败不阻断（K4）
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


def native_assemble_cached(asm_text: str, out_obj: str,
                           want_stats: dict = None) -> dict:
    """QOTNative · obj CAS（K2 形态）：asm 文本 → COFF 目标。

    key 组件（机械清点，逐项对 §9.3-K2）：asm **内容** hash（lir_native.py
    或任何管线源变化 → 发射产物变 → 必 miss，无需另行登记）、编译器身份、
    '-c' flags、开关指纹、semver、backend='native'。GAS 汇编不读 runtime 头
    （纯文本→COFF），rt_hash 不参与本层 —— runtime 的正确性由链接层 key
    （qotrt.lib 内容 hash）覆盖。
    QOT_CACHE=off/pch → 全冷直通（K5）。命中交付 CAS 副本（K1：命中与冷编
    等价 = 同一 g++ 对同一 asm 文本的确定性产物）。
    返回 {'hit', 'sha', 'path', 'elapsed'}。
    """
    t0 = time.perf_counter()
    cid = compiler_id()
    if not enabled_for('object'):
        import lir_native as _ln
        el, _n = _ln.assemble_obj(asm_text, out_obj, cid['path'])
        return {'hit': False, 'sha': text_hash(asm_text), 'path': out_obj,
                'elapsed': time.perf_counter() - t0}
    key, comps = toolchain_key(c_text=asm_text, flags=['-c'], form='obj',
                               extra={'backend': 'native'})
    obj = _cas_get(key)
    if obj is not None:
        deliver(obj, out_obj)
        record_stats('object', True)
        if want_stats is not None:
            want_stats['object'] = 'hit'
        return {'hit': True, 'sha': key, 'path': out_obj,
                'elapsed': time.perf_counter() - t0}
    record_stats('object', False)
    if want_stats is not None:
        want_stats['object'] = 'miss'
    import lir_native as _ln
    _ln.assemble_obj(asm_text, out_obj, cid['path'])
    _cas_put(key, out_obj, comps)
    return {'hit': False, 'sha': key, 'path': out_obj,
            'elapsed': time.perf_counter() - t0}


def link_native(obj_path: str, out_exe: str, netlibs=None,
                gui: bool = False, want_stats: dict = None) -> dict:
    """QOTNative：native obj + qotrt.lib → exe（g++ 驱动链接；crt/libgcc 自动）。

    v2：exe CAS（form='link'，K2 形态）。key = qotrt.lib **内容** hash +
    obj **内容** hash + 链接 flags（static/-s/确定性）+ netlibs/gui +
    编译器/开关/semver。命中交付副本（K1）；PE 时间戳由 K3 的
    --no-insert-timestamp 归零 → 同 key 重链逐字节可复现，缓存不改变输出。
    """
    t0 = time.perf_counter()
    lib = ensure_runtime_lib()
    cid = compiler_id()
    if not enabled_for('link'):
        _link_native_cold(obj_path, out_exe, cid, lib, netlibs, gui)
        return {'hit': False, 'sha': file_hash(out_exe),
                'elapsed': time.perf_counter() - t0}
    flags = _static_link_flags() + ['-s'] + determinism_link_flags()
    key, comps = toolchain_key(
        rt_hash=file_hash(lib), flags=flags, form='link',
        extra={'objs': [file_hash(obj_path)], 'gui': bool(gui),
               'netlibs': sorted(netlibs or [])})
    exe = _cas_get(key)
    if exe is not None:
        deliver(exe, out_exe)
        record_stats('link', True)
        if want_stats is not None:
            want_stats['link'] = 'hit'
        return {'hit': True, 'sha': key, 'elapsed': time.perf_counter() - t0}
    record_stats('link', False)
    if want_stats is not None:
        want_stats['link'] = 'miss'
    _link_native_cold(obj_path, out_exe, cid, lib, netlibs, gui)
    _cas_put(key, out_exe, comps)
    return {'hit': False, 'sha': key, 'elapsed': time.perf_counter() - t0}


def _link_native_cold(obj_path: str, out_exe: str, cid: dict, lib: str,
                      netlibs, gui: bool):
    """link_native 冷路径（原实现本体）。"""
    if os.path.exists(out_exe):
        try:
            os.remove(out_exe)
        except PermissionError:
            time.sleep(0.5)
            os.remove(out_exe)
    cmd = [cid['path']] + _static_link_flags() + ['-s',
           os.path.abspath(obj_path), os.path.abspath(lib),
           '-o', os.path.abspath(out_exe)]
    if gui:
        objs = gui_objects()
        if not objs:
            raise CacheDisabled('GUI 目标文件构建失败（vg_* 程序无法链接）')
        cmd += objs + GUI_LIBS
    if netlibs:
        cmd += list(netlibs)
    cmd += determinism_link_flags()
    proc = run_toolchain(cmd, cwd=os.path.dirname(os.path.abspath(out_exe)) or '.',
                         timeout=300)
    _check_output(proc, out_exe, 'QOTNative 链接')


def _exe_flags(pch_dir, netlibs, gui=False) -> list:
    """key 用语义 flags（稳定序；不含源/目标路径）。"""
    flags = ['-std=c++17'] + _opt_level() + ['-finput-charset=utf-8']
    if pch_dir:
        flags += ['-Winvalid-pch', '-I' + pch_dir]
    flags += _static_link_flags() + ['-s']
    if gui:
        flags += ['-D_QOT_GUI'] + GUI_LIBS
    if netlibs:
        flags += list(netlibs)
    flags += determinism_link_flags()
    return flags


def _exe_cmd(gxx, pch_dir, netlibs, c_path, out_exe) -> list:
    """实际命令行：库/链接 flags 必须在输入文件**之后**（GNU ld 按序解析
    库：`-l` 放源文件前面 = 源文件需要的符号不被解析 —— std_net 实证的
    __imp_htonl 未定义即此因）。"""
    cmd = [gxx, '-std=c++17'] + _opt_level() + ['-finput-charset=utf-8']
    if pch_dir:
        cmd += ['-Winvalid-pch', '-I' + pch_dir]
    cmd += _static_link_flags() + ['-s',
            os.path.abspath(c_path), '-o', os.path.abspath(out_exe)]
    if netlibs:
        cmd += list(netlibs)
    cmd += determinism_link_flags()
    return cmd


def _compile_exe_cold(c_path: str, out_exe: str, *, rt_src: str,
                      pch_dir: str, netlibs, timeout: int,
                      copy_header: bool = False, gui: bool = False):
    """冷编（gcc 直 spawn，不经 bat —— bat 是 MSVC 环境初始化的遗留）。"""
    cid = compiler_id()
    if cid['kind'] != 'gcc':
        raise CacheDisabled('非 gcc 工具链走既有 MSVC 兜底路径')
    # 语义陷阱守卫：TU 同目录不得存在 runtime 头副本（quoted include 先搜
    # TU 目录，会让 -I 指向的 PCH 副本静默旁路 → PCH 失效且不可见，K4 禁止）。
    # 缓存开启时由我们负责清掉（历史上 index.py 曾把头拷进构建目录）。
    # copy_header=True（全冷模式）：相反——必须把头拷回构建目录（无 -I 时
    # include 的唯一解析点），与既有单 TU 形态逐字节一致。
    rt_name = os.path.basename(rt_src)
    side = os.path.join(os.path.dirname(os.path.abspath(c_path)), rt_name)
    # 【0 加固】旧守卫只在"同 hash"时删副本 —— 恰好放过**陈旧**
    # 副本（hash 不同），而 quoted include 先搜 TU 目录 → 静默用旧 runtime
    # 编译（K4 禁止的不可见态；json native 化时实测撞上：新增的 qotrt_json_*
    # 未声明，因为编译吃的是构建目录里的旧头）。
    # 规则：copy_header 模式 → 用规范版本覆盖；否则一律清掉（让 -I/PCH 生效）。
    if copy_header and os.path.abspath(side) != os.path.abspath(rt_src):
        d = os.path.dirname(side)
        os.makedirs(d, exist_ok=True)
        _copy_with_retry(rt_src, side)
    elif os.path.exists(side):
        try:
            os.remove(side)
        except OSError:
            pass
    # 同目录的**陈旧 .gch** 同样致命：gcc 对 `#include "x.h"` 会优先吃同目录
    # 的 x.h.gch（先于 -I 的 PCH 目录）—— 旧 PCH 会静默吞掉新增的运行时符号
    # （json native 化时实测：qotrt_json_* "未声明"，而 -E 里明明可见）。
    if not copy_header:
        side_gch = side + '.gch'
        if os.path.exists(side_gch):
            try:
                os.remove(side_gch)
            except OSError:
                pass
    if os.path.exists(out_exe):
        try:
            os.remove(out_exe)
        except PermissionError:
            time.sleep(0.5)
            os.remove(out_exe)
    cmd = _exe_cmd(cid['path'], pch_dir, netlibs, c_path, out_exe)
    if gui:
        # wingui 目标文件 + Direct2D 库：必须排在输入文件**之后**（GNU ld
        # 按序解析符号）；插在 '-o' 之前即输入之后，与 netlibs 同侧不冲突。
        objs = gui_objects()
        if not objs:
            raise CacheDisabled('GUI 目标文件构建失败（vg_* 程序无法链接）——'
                                '检查 ot-asset/c/wingui 下四个 .cpp 是否可编译')
        o_idx = cmd.index('-o') if '-o' in cmd else len(cmd) - 2
        cmd = cmd[:o_idx] + objs + GUI_LIBS + cmd[o_idx:]
    proc = run_toolchain(cmd, cwd=os.path.dirname(os.path.abspath(out_exe)) or '.',
                         timeout=timeout)
    _check_output(proc, out_exe, 'AOT 原生编译')


# ---------------------------------------------------------------- 对象与链接（L1 的工具链面）

def compile_object(c_path: str, out_obj: str, *, rt_src: str, split: bool,
                   tu_name: str, timeout: int = 300,
                   want_stats: dict = None, work_dir: str = None,
                   header_hash: str = None) -> dict:
    """切分 TU：C → .o（对象缓存）。语义同 compile_exe，形态 form='obj'。

    TU 源引用共享头（qot_parts.h，qot_build 放在 TU 同目录）——头的**内容
    hash 必须进 key**（K2：头里的签名/声明变化会改变 TU 的编译结果，漏掉
    = 假命中 = 静默错编；如 has-env 签名变化的 ABI 错配）。
    out_obj 可为 ''：缓存模式只产出 CAS 副本（增量链接直接引用 CAS 路径）。
    冷模式（off/pch）产物落 work_dir（调用方管理生命周期）。
    """
    t0 = time.perf_counter()
    m = mode()
    pch_dir = ensure_pch(rt_src, None, split=split) if m != 'off' else None
    flags = _obj_flags(pch_dir, split)
    if m != 'on':
        cid = compiler_id()
        os.makedirs(work_dir or store_root(), exist_ok=True)
        obj = os.path.join(work_dir, '%s.o' % _sanitize_tu(tu_name))
        cmd = [cid['path']] + flags + ['-c', os.path.abspath(c_path), '-o',
                                       os.path.abspath(obj)]
        proc = run_toolchain(cmd, timeout=timeout)
        _check_output(proc, obj, '对象编译(%s)' % tu_name)
        if want_stats is not None:
            want_stats['object'] = 'miss'
        return {'hit': False, 'sha': None, 'path': obj,
                'elapsed': time.perf_counter() - t0, 'pch': bool(pch_dir)}
    rt_h = file_hash(rt_src)
    extra = {'tu': tu_name}
    if header_hash:
        extra['header'] = header_hash
    key, comps = toolchain_key(c_path=c_path, rt_hash=rt_h, flags=flags,
                               form='obj', extra=extra)
    obj = _cas_get(key)
    if obj is not None:
        if out_obj:
            deliver(obj, out_obj)
        record_stats('object', True)
        if want_stats is not None:
            want_stats['object'] = 'hit'
        return {'hit': True, 'sha': key, 'path': obj,
                'elapsed': time.perf_counter() - t0, 'pch': bool(pch_dir)}
    record_stats('object', False)
    cid = compiler_id()
    tmp_dir = os.path.join(store_root(), 'tmp')
    os.makedirs(tmp_dir, exist_ok=True)
    fd, tmp_obj = tempfile.mkstemp(dir=tmp_dir, prefix='obj_', suffix='.o')
    os.close(fd)
    try:
        cmd = [cid['path']] + flags + ['-c', os.path.abspath(c_path), '-o',
                                       os.path.abspath(tmp_obj)]
        proc = run_toolchain(cmd, timeout=timeout)
        _check_output(proc, tmp_obj, '对象编译(%s)' % tu_name)
        _cas_put(key, tmp_obj, comps)
        if out_obj:
            deliver(_cas_object_path(key), out_obj)
    finally:
        try:
            os.remove(tmp_obj)
        except OSError:
            pass
    if want_stats is not None:
        want_stats['object'] = 'miss'
    return {'hit': False, 'sha': key, 'path': _cas_object_path(key),
            'elapsed': time.perf_counter() - t0, 'pch': bool(pch_dir)}


def _sanitize_tu(name: str) -> str:
    import re as _re
    s = _re.sub(r'[^A-Za-z0-9_]', '_', name or 'tu')
    return s or 'tu'


def _obj_flags(pch_dir, split: bool) -> list:
    flags = ['-std=c++17'] + _opt_level() + ['-finput-charset=utf-8']
    if split:
        flags.append('-DQOT_RT_SPLIT')
    if pch_dir:
        flags += ['-Winvalid-pch', '-I' + pch_dir]
    return flags


def link_objects(obj_paths, out_exe: str, *, rt_src: str, netlibs=None,
                 timeout: int = 300, want_stats: dict = None) -> dict:
    """切分 TU：对象集 → exe（链接缓存；key = 对象 hash 集合 + 链接 flags）。"""
    t0 = time.perf_counter()
    objs = sorted(os.path.abspath(p) for p in obj_paths)
    flags = _link_flags(netlibs)
    if mode() != 'on':
        cid = compiler_id()
        if os.path.exists(out_exe):
            try:
                os.remove(out_exe)
            except PermissionError:
                time.sleep(0.5)
                os.remove(out_exe)
        cmd = _link_cmd(cid['path'], netlibs, objs, out_exe)
        proc = run_toolchain(cmd, cwd=os.path.dirname(
            os.path.abspath(out_exe)) or '.', timeout=timeout)
        _check_output(proc, out_exe, '增量链接')
        return {'hit': False, 'sha': None,
                'elapsed': time.perf_counter() - t0}
    obj_hashes = [file_hash(p) for p in objs]
    key, comps = toolchain_key(rt_hash=file_hash(rt_src), flags=flags,
                               form='link', extra={'objs': obj_hashes})
    exe = _cas_get(key)
    if exe is not None:
        deliver(exe, out_exe)
        record_stats('link', True)
        if want_stats is not None:
            want_stats['link'] = 'hit'
        return {'hit': True, 'sha': key, 'elapsed': time.perf_counter() - t0}
    record_stats('link', False)
    cid = compiler_id()
    if cid['kind'] != 'gcc':
        raise CacheDisabled('非 gcc 工具链走既有 MSVC 兜底路径')
    if os.path.exists(out_exe):
        try:
            os.remove(out_exe)
        except PermissionError:
            time.sleep(0.5)
            os.remove(out_exe)
    cmd = _link_cmd(cid['path'], netlibs, objs, out_exe)
    proc = run_toolchain(cmd, cwd=os.path.dirname(os.path.abspath(out_exe)) or '.',
                         timeout=timeout)
    _check_output(proc, out_exe, '增量链接')
    _cas_put(key, out_exe, comps)
    if want_stats is not None:
        want_stats['link'] = 'miss'
    return {'hit': False, 'sha': key, 'elapsed': time.perf_counter() - t0}


def _link_flags(netlibs) -> list:
    """key 用语义 flags（稳定序；对象路径不入 key —— 对象 hash 已入 extra）。"""
    flags = _static_link_flags() + ['-s']
    if netlibs:
        flags += list(netlibs)
    flags += determinism_link_flags()
    return flags


def _link_cmd(gxx, netlibs, objs, out_exe) -> list:
    """实际命令行：库/链接 flags 在对象之后（同 _exe_cmd 的库序约束）。"""
    cmd = [gxx] + _static_link_flags() + ['-s']
    cmd += list(objs)
    cmd += ['-o', os.path.abspath(out_exe)]
    if netlibs:
        cmd += list(netlibs)
    cmd += determinism_link_flags()
    return cmd


# ---------------------------------------------------------------- 统计（K4）

STAT_LAYERS = ('toolchain', 'object', 'link', 'pch', 'frontend', 'tu', 'bundle')


def record_stats(layer: str, hit: bool):
    """进程内累计 + 周期性落盘（多进程下计数可能偶尔丢一次落盘 —— 可接受，
    命中率统计是观测件不是正确性件；K4 的硬保证来自等价性用例与 PCH 硬报错）。"""
    try:
        with _STAT_LOCK:
            _STATS.setdefault(layer, {'hits': 0, 'misses': 0})
            _STATS[layer]['hits' if hit else 'misses'] += 1
            _STATS['_dirty'] = _STATS.get('_dirty', 0) + 1
            if _STATS['_dirty'] >= 8:
                _flush_stats()
    except Exception:
        pass


_STATS = {}


def _stats_path() -> str:
    return os.path.join(store_root(), 'stats.json')


def _flush_stats():
    p = _stats_path()
    cur = {}
    try:
        with open(p, 'r', encoding='utf-8') as f:
            cur = json.load(f)
    except Exception:
        cur = {}
    for k, v in _STATS.items():
        if k.startswith('_'):
            continue
        c = cur.setdefault(k, {'hits': 0, 'misses': 0})
        c['hits'] = int(c.get('hits', 0)) + int(v.get('hits', 0))
        c['misses'] = int(c.get('misses', 0)) + int(v.get('misses', 0))
    cur['updated_at'] = time.time()
    _STATS.clear()
    _STATS['_dirty'] = 0
    _atomic_write_json(p, cur)


def stats_snapshot() -> dict:
    p = _stats_path()
    out = {}
    try:
        with open(p, 'r', encoding='utf-8') as f:
            out = json.load(f)
    except Exception:
        out = {}
    for k, v in _STATS.items():
        if k.startswith('_'):
            continue
        c = out.setdefault(k, {'hits': 0, 'misses': 0})
        c['hits'] = int(c.get('hits', 0)) + int(v.get('hits', 0))
        c['misses'] = int(c.get('misses', 0)) + int(v.get('misses', 0))
    return {k: v for k, v in out.items() if not k.startswith('_')}


def format_stats() -> str:
    st = stats_snapshot()
    if not st:
        return '（缓存统计为空：还没有任何编译经过缓存层）'
    lines = []
    total_h = total_m = 0
    for layer in ('frontend', 'pch', 'object', 'toolchain', 'link', 'bundle'):
        if layer not in st:
            continue
        h, m = st[layer].get('hits', 0), st[layer].get('misses', 0)
        total_h += h
        total_m += m
        rate = ('%.0f%%' % (100.0 * h / (h + m))) if (h + m) else '-'
        lines.append('  %-10s hits=%-6d misses=%-6d 命中率 %s'
                     % (layer, h, m, rate))
    rate = ('%.0f%%' % (100.0 * total_h / (total_h + total_m))) \
        if (total_h + total_m) else '-'
    lines.append('  %-10s hits=%-6d misses=%-6d 命中率 %s'
                 % ('合计', total_h, total_m, rate))
    try:
        sz = store_size()
        lines.append('  存储：%s（objects+pch）' % _fmt_bytes(sz))
    except Exception:
        pass
    return '\n'.join(lines)


def _fmt_bytes(n: int) -> str:
    for unit in ('B', 'KB', 'MB', 'GB'):
        if n < 1024 or unit == 'GB':
            return '%.1f%s' % (n, unit)
        n /= 1024.0
    return '%d B' % n


def store_size() -> int:
    root = store_root()
    total = 0
    for sub in ('objects', 'pch'):
        d = os.path.join(root, sub)
        for dirpath, _dirs, files in os.walk(d):
            for fn in files:
                p = os.path.join(dirpath, fn)
                try:
                    total += os.path.getsize(p)
                except OSError:
                    pass
    return total


# ---------------------------------------------------------------- GC（§9.7-1）

def gc_cache(*, max_bytes=None, keep_pch=2, dry_run=False) -> dict:
    """LRU GC。默认上限 4 GiB（QOT_CACHE_MAX_BYTES 可覆盖）；PCH 只留最近
    keep_pch 个版本（54.6MB × 版本数是已知风险）。key.json sidecar 一并回收。"""
    root = store_root()
    if max_bytes is None:
        env = os.environ.get('QOT_CACHE_MAX_BYTES')
        max_bytes = int(env) if env else 4 * 1024 * 1024 * 1024
    report = {'removed': 0, 'freed': 0, 'kept': 0, 'size_before': store_size()}
    # PCH：按 mtime 保留最近 keep_pch 个
    pch_dir = os.path.join(root, 'pch')
    if os.path.isdir(pch_dir):
        vers = []
        for name in os.listdir(pch_dir):
            p = os.path.join(pch_dir, name)
            if os.path.isdir(p):
                vers.append((os.path.getmtime(p), name, p))
        vers.sort(reverse=True)
        for _t, name, p in vers[keep_pch:]:
            sz = _dir_size(p)
            if not dry_run:
                shutil.rmtree(p, ignore_errors=True)
            report['removed'] += 1
            report['freed'] += sz
    # objects：按 atime LRU（无 atime 的文件按 mtime），超限逐个删最旧
    objs = []
    obj_root = os.path.join(root, 'objects')
    for h2 in (os.listdir(obj_root) if os.path.isdir(obj_root) else []):
        d = os.path.join(obj_root, h2)
        for fn in os.listdir(d):
            if fn.startswith('.'):
                continue
            p = os.path.join(d, fn)
            try:
                t = os.path.getatime(p)
            except OSError:
                t = 0.0
            objs.append((t, p))
    objs.sort()
    total = sum(os.path.getsize(p) for _t, p in objs if os.path.exists(p))
    for t, p in objs:
        if total <= max_bytes:
            break
        try:
            sz = os.path.getsize(p)
        except OSError:
            continue
        if not dry_run:
            try:
                os.remove(p)
                side = p + '.key.json'
                if os.path.exists(side):
                    os.remove(side)
                # 空分桶回收
                pd = os.path.dirname(p)
                if not os.listdir(pd):
                    os.rmdir(pd)
            except OSError:
                continue
        total -= sz
        report['removed'] += 1
        report['freed'] += sz
    report['kept'] = len(objs) - report['removed']
    report['size_after'] = store_size() if not dry_run else report['size_before']
    return report


def _dir_size(p: str) -> int:
    total = 0
    for dp, _dns, fns in os.walk(p):
        for fn in fns:
            try:
                total += os.path.getsize(os.path.join(dp, fn))
            except OSError:
                pass
    return total


# ---------------------------------------------------------------- 收尾

def flush_stats():
    with _STAT_LOCK:
        if _STATS.get('_dirty'):
            try:
                _flush_stats()
            except Exception:
                pass
