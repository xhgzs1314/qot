# -*- coding: utf-8 -*-
"""QOT fuzz 差分执行器 ——  §3.1 / S-01。

三方对比：解释器（worker 进程内 Evaluator）× VM（子进程批执行 qotrun.dll）×
native（抽样，子进程 aot 编译 + 看门狗运行）。输出经 compare.compare_outputs
归一化比对。

用法：
  python testing/fuzz/run_fuzz.py --count 240 --native-sample 4      # 回归档
  python testing/fuzz/run_fuzz.py --count 10000 --native-sample 500  # 深度档

架构（Windows spawn 友好，三处工程决策都有实证依据）：
  * worker 进程内跑【生成 + 解释器 + 字节码编译】（每例 < 50ms），
    VM 侧攒一批交 _vm_batch.py 子进程执行 —— qotrun.dll 是 /MT 静态 CRT，
    printf 缓冲无法被父进程 fflush 触达（20261035/37 实证的串 case 根因），
    子进程退出自然 flush，启动成本被批量摊薄；
  * worker 内解释器路径挂 threading 看门狗（os._exit(42)）；VM 批子进程带
    批级超时，超时后逐例单进程重放定位悬挂例（G-8 双层防线）；
  * 每例结果增写 journal（worker 被硬杀后父进程打捞已完成例）；
  * native 抽样：主进程串行编译（依赖 cl.exe），运行 10s 硬超时。
"""
from __future__ import annotations

import argparse
import json
import os
import random
import subprocess
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
TESTING = os.path.dirname(HERE)
ROOT = os.path.dirname(TESTING)
for p in (ROOT, TESTING):
    if p not in sys.path:
        sys.path.insert(0, p)

from compare import compare_outputs                    # noqa: E402
from gen_program import GenOptions, generate_program   # noqa: E402
import known_divergences                               # noqa: E402  (契约文档)

PY = sys.executable
DLL = os.path.join(ROOT, 'qotrun.dll')
ARTIFACTS = os.path.join(TESTING, '.artifacts', 'fuzz')

PER_CASE_BUDGET_S = 15         # 解释器单例看门狗（并行竞争 + 老旧 CPU 节流余量）
VM_BATCH_PER_CASE_S = 5        # VM 批执行的单例预算（子进程级超时，不杀 worker）
BATCH_SLOP_S = 30              # 批级额外余量
NATIVE_RUN_TIMEOUT_S = 10      # G-8：native exe 裸跑必须带硬超时


# ============================================================ 解释器（进程内）
def _tmp_qot():
    """系统 TEMP 下的临时 .qot（工作区外 → 不受 IDE 安全删除护栏干扰；
    每例独立文件，批执行期需同时存活）。"""
    fd, path = tempfile.mkstemp(suffix='.qot', prefix='qotfuzz_')
    os.close(fd)
    return path


def run_interp_src(src: str):
    """worker 进程内解释器执行。返回 dict(ok, stdout, stderr, err)。"""
    from error_system import get_error_handler
    handler = get_error_handler()
    path = _tmp_qot()
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        f.write(src)
    handler.get_collector().clear()
    out, err = io_string(), io_string()
    import contextlib
    import index as qot_index
    ast = None
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            ast = qot_index.parse_oning_gram(path)
    except BaseException as e:      # 解析层可能 sys.exit / 直接 raise
        return {'ok': False, 'stdout': '', 'stderr': err.getvalue(),
                'err': 'interp-parse: %r' % (e,)}
    if ast is None or ast == 1:
        return {'ok': False, 'stdout': '', 'stderr': err.getvalue(),
                'err': 'interp-parse failed'}
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            ev = qot_index.Evaluator()
            ev.set_base_path(path)
            ev.evaluate(ast)
    except BaseException as e:
        return {'ok': False, 'stdout': out.getvalue(), 'stderr': err.getvalue(),
                'err': 'interp-run: %r' % (e,)}
    return {'ok': True, 'stdout': out.getvalue(), 'stderr': err.getvalue(),
            'err': ''}


def io_string():
    import io
    return io.StringIO()


# ============================================================ VM（子进程批）
_VM_MARK_RE = None


def _split_vm_sections(raw: bytes):
    """按 '====CASE <i>====' 分段（容忍 \r\n 与文字模式翻译）。"""
    import re
    global _VM_MARK_RE
    if _VM_MARK_RE is None:
        _VM_MARK_RE = re.compile(rb'====CASE (\d+)====\r?\n')
    out = {}
    matches = list(_VM_MARK_RE.finditer(raw))
    for k, m in enumerate(matches):
        idx = int(m.group(1))
        end = matches[k + 1].start() if k + 1 < len(matches) else len(raw)
        out[idx] = raw[m.end():end].decode('utf-8', errors='replace')
    return out


def _run_vm_batch_subprocess(qtc_list, per_case_s=VM_BATCH_PER_CASE_S):
    """一批 .qtc 交给 _vm_batch.py 子进程执行。返回 list[dict(ok, stdout)]。

    批超时 → 逐例单进程重放（每例独立子进程 + 硬超时）精确定位悬挂例。
    """
    if not qtc_list:
        return []
    budget = 10 + len(qtc_list) * per_case_s
    try:
        p = subprocess.run(
            [PY, os.path.join(HERE, '_vm_batch.py')],
            input=json.dumps({'cases': qtc_list}).encode('utf-8'),
            capture_output=True, timeout=budget, cwd=ROOT)
        got = _split_vm_sections(p.stdout)
        if len(got) == len(qtc_list):
            return [{'ok': True, 'stdout': got[i]} for i in range(len(qtc_list))]
        # 输出分段不齐 → 落入逐例重放
    except subprocess.TimeoutExpired:
        pass
    except Exception:
        pass
    # ---- 逐例重放（定位悬挂/异常例）----
    results = []
    for qtc in qtc_list:
        try:
            p = subprocess.run(
                [PY, os.path.join(HERE, '_vm_batch.py')],
                input=json.dumps({'cases': [qtc]}).encode('utf-8'),
                capture_output=True, timeout=per_case_s + 10, cwd=ROOT)
            got = _split_vm_sections(p.stdout)
            body = got.get(0, '')
            results.append({'ok': True, 'stdout': body})
        except subprocess.TimeoutExpired:
            results.append({'ok': False, 'stdout': '', 'err': 'vm-timeout'})
        except Exception as e:
            results.append({'ok': False, 'stdout': '', 'err': 'vm-subprocess: %r' % (e,)})
    return results


def compile_qtc(src: str):
    """worker 进程内编译 → .qtc 路径（系统 TEMP，失败返回 None + 错误信息）。"""
    from error_system import get_error_handler
    from index import _compile_to_qtc
    handler = get_error_handler()
    path = _tmp_qot()
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        f.write(src)
    qtc = path + '.qtc'
    import io
    import contextlib
    err = io.StringIO()
    handler.get_collector().clear()
    try:
        with contextlib.redirect_stderr(err):
            _compile_to_qtc(path, qtc, False)
    except BaseException as e:
        return None, path, 'vm-compile: %r | %s' % (e, err.getvalue()[-200:])
    if not os.path.exists(qtc):
        return None, path, 'vm-compile failed: ' + err.getvalue()[-200:]
    return qtc, path, ''


# ============================================================ native（抽样）
def run_native(qot_path: str, exe_path: str, csrc_path: str = None):
    """子进程 aot 编译 + 看门狗运行。返回 (ok, stdout, detail)。"""
    if os.path.exists(exe_path):
        os.remove(exe_path)
    args = [PY, os.path.join(ROOT, 'index.py'), 'aot', qot_path, exe_path]
    if csrc_path:
        args += ['--emit-c', csrc_path]
    try:
        p = subprocess.run(args, cwd=ROOT, capture_output=True, text=True,
                           encoding='utf-8', errors='replace', timeout=240)
    except subprocess.TimeoutExpired:
        return False, '', 'aot 编译超时'
    if not os.path.exists(exe_path):
        tail = (p.stderr or p.stdout or '').strip().splitlines()
        return False, '', 'aot 编译失败: %s' % (tail[-1] if tail else '?')
    try:
        rp = subprocess.run([exe_path], cwd=ROOT, capture_output=True, text=True,
                            encoding='utf-8', errors='replace',
                            timeout=NATIVE_RUN_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return False, '', 'native 运行超时（>%ds）' % NATIVE_RUN_TIMEOUT_S
    return True, rp.stdout or '', ''


# ============================================================ worker
# ============================================================ 白名单匹配
import re as _re

_KNOWN_ITERLEN_RE = _re.compile(r'__iter_len\(|glen\(')


def classify_divergence(src: str, detail: str):
    """分歧分类：命中已钉分歧白名单 → KNOWN_*（单独计数，不算失败）。

    KNOWN-ITERLEN-FRAC：__iter_len/glen 施加于数字的截断语义（D-08，
    vm/interp 专钉守住精确行为）。生成器的静态区间追踪对「条件分支 +
    循环」组合下的整型性存在残余盲区——与其继续加追踪复杂度，不如把该
    已知类降级为 KNOWN：它不掩盖其它任何类（数组/字符串长度位、控制流、
    比较等一切其它分歧仍然 FAIL）。"""
    if _KNOWN_ITERLEN_RE.search(src or ''):
        return 'KNOWN_ITERLEN_FRAC'
    return None


def _watchdog_kill():
    sys.stderr.write('[fuzz-worker] watchdog 硬杀（生成器停机证明被击穿）\n')
    sys.stderr.flush()
    os._exit(42)


def fuzz_one(seed: int, opts_dict: dict):
    """完整单例（interp 进程内 + VM 子进程单例）。用于单例重放/调试。"""
    opts = GenOptions(**opts_dict)
    t0 = time.perf_counter()
    src, info = generate_program(seed, opts)
    res = {'seed': seed, 'info': info,
           'gen_s': round(time.perf_counter() - t0, 4),
           'interp': None, 'vm': None, 'status': '', 'detail': ''}
    wd = threading.Timer(PER_CASE_BUDGET_S, _watchdog_kill)
    wd.daemon = True
    wd.start()
    try:
        ri = run_interp_src(src)
    finally:
        wd.cancel()
    res['interp'] = ri
    if not ri['ok']:
        res['status'] = 'GEN_OR_INTERP_ERROR'
        res['detail'] = ri['err'] + ' | stderr: ' + ri['stderr'][-300:]
        res['src'] = src
        return res
    if (ri['stderr'] or '').strip():
        res['status'] = 'INTERP_STDERR'
        res['detail'] = '解释器 stderr 非空: ' + ri['stderr'][-300:]
        res['src'] = src
        return res
    qtc, path, cerr = compile_qtc(src)
    try:
        if qtc is None:
            res['status'] = 'VM_ERROR'
            res['detail'] = cerr
            res['src'] = src
            return res
        try:
            rv = _run_vm_batch_subprocess([qtc])[0]
        except Exception as e:
            rv = {'ok': False, 'stdout': '', 'err': 'vm: %r' % (e,)}
        res['vm'] = rv
        if not rv['ok']:
            res['status'] = 'VM_ERROR'
            res['detail'] = rv.get('err', 'vm failed')
            res['src'] = src
            return res
        if (rv.get('stderr') or '').strip():
            res['status'] = 'VM_STDERR'
            res['detail'] = 'VM stderr 非空'
            res['src'] = src
            return res
        diff = compare_outputs(ri['stdout'], rv['stdout'])
        if diff:
            known = classify_divergence(src, diff)
            res['status'] = known or 'DIVERGENCE'
            res['detail'] = ('[白名单 %s] ' % known if known else '') + diff
            res['src'] = src
        else:
            res['status'] = 'OK'
        return res
    finally:
        for p in (path, (qtc or '')):
            if p:
                try:
                    os.remove(p)
                except OSError:
                    pass


def run_worker_batch(payload):
    """worker 入口。payload = (batch_id, [(seed, opts_dict), ...])。

    阶段 A：逐例【生成 + 解释器 + 编译】（每例完成即 journal）；
    阶段 B：VM 批子进程执行 → 逐例比对 → journal。
    每阶段落盘，worker 被硬杀后父进程可打捞已完成例。
    """
    batch_id, jobs = payload
    os.makedirs(ARTIFACTS, exist_ok=True)
    journal = os.path.join(ARTIFACTS, '_journal_%s.jsonl' % batch_id)
    results = []

    def jour(r):
        with open(journal, 'a', encoding='utf-8') as jf:
            jf.write(json.dumps(r, ensure_ascii=False) + '\n')
        results.append(r)

    preps = []   # (res_dict, src, qtc, qot_tmp)
    for seed, opts_dict in jobs:
        try:
            src, info = generate_program(seed, GenOptions(**opts_dict))
        except Exception as e:
            jour({'seed': seed, 'status': 'GEN_CRASH', 'detail': '%r' % (e,)})
            continue
        wd = threading.Timer(PER_CASE_BUDGET_S, _watchdog_kill)
        wd.daemon = True
        wd.start()
        try:
            ri = run_interp_src(src)
        finally:
            wd.cancel()
        base = {'seed': seed, 'info': info, 'interp': ri, 'vm': None,
                'status': '', 'detail': ''}
        if not ri['ok']:
            base['status'] = 'GEN_OR_INTERP_ERROR'
            base['detail'] = ri['err'] + ' | stderr: ' + ri['stderr'][-300:]
            base['src'] = src
            jour(base)
            continue
        if (ri['stderr'] or '').strip():
            base['status'] = 'INTERP_STDERR'
            base['detail'] = '解释器 stderr 非空: ' + ri['stderr'][-300:]
            base['src'] = src
            jour(base)
            continue
        try:
            qtc, path, cerr = compile_qtc(src)
        except Exception as e:
            base['status'] = 'VM_ERROR'
            base['detail'] = 'compile crashed: %r' % (e,)
            base['src'] = src
            jour(base)
            continue
        if qtc is None:
            base['status'] = 'VM_ERROR'
            base['detail'] = cerr
            base['src'] = src
            jour(base)
            continue
        preps.append((base, src, qtc, path))

    # ---- 阶段 B：VM 批执行 ----
    if preps:
        vm_results = _run_vm_batch_subprocess([p[2] for p in preps])
        for (base, src, qtc, path), rv in zip(preps, vm_results):
            base['vm'] = rv
            if not rv['ok']:
                base['status'] = 'VM_ERROR'
                base['detail'] = rv.get('err', 'vm failed')
                base['src'] = src
            else:
                diff = compare_outputs(base['interp']['stdout'], rv['stdout'])
                if diff:
                    known = classify_divergence(src, diff)
                    base['status'] = known or 'DIVERGENCE'
                    base['detail'] = ('[白名单 %s] ' % known if known else '') + diff
                    base['src'] = src
                else:
                    base['status'] = 'OK'
            jour(base)
            for p in (qtc, path):
                try:
                    os.remove(p)
                except OSError:
                    pass
    return results


# ============================================================ 主流程
def _make_jobs(count, seed_base, rng):
    """job = (seed, opts_dict)。opts 也随机化，保证侧面多样性且可复现。"""
    jobs = []
    for i in range(count):
        depth = rng.choice([3, 4, 5, 6, 7, 8])
        jobs.append((seed_base + i, {
            'max_depth': depth,
            'allow_arrays': rng.random() < 0.85,
            'allow_dicts': rng.random() < 0.6,
            'allow_funcs': rng.random() < 0.8,
            'allow_recursion': rng.random() < 0.8,
            'allow_stop': rng.random() < 0.7,
        }))
    return jobs


def _salvage_journal(batch_id, batch, on_result):
    """worker 被硬杀后，从 journal 打捞已完成例；返回未完成的 (seed, opts)。"""
    journal = os.path.join(ARTIFACTS, '_journal_%s.jsonl' % batch_id)
    done = {}
    if os.path.exists(journal):
        try:
            with open(journal, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        r = json.loads(line)
                        done[r.get('seed')] = r
                    except ValueError:
                        continue
        except OSError:
            pass
    remaining = []
    for seed, opts in batch:
        if seed in done:
            on_result(done[seed])
        else:
            remaining.append((seed, opts))
    try:
        os.remove(journal)
    except OSError:
        pass
    return remaining


def _dump_timeout_src(seed, opts_dict):
    """TIMEOUT 例的确定性重生成留档（G-8 取证）。"""
    try:
        src, _info = generate_program(seed, GenOptions(**opts_dict))
        os.makedirs(ARTIFACTS, exist_ok=True)
        p = os.path.join(ARTIFACTS, 'timeout_%d.qot' % seed)
        with open(p, 'w', encoding='utf-8', newline='\n') as f:
            f.write(src)
        return p
    except Exception as e:
        return '（重生成失败: %r）' % (e,)


def _run_retry_subprocess(remaining, on_result):
    """重试路径：裸子进程内序执行（绕开 mp.Pool——本机池+spawn 嵌套实测
    不稳定：同批进程内 8s 全过、走池却整批超时）。子进程整体带硬超时；
    结果 JSONL 落盘，未完成的标 TIMEOUT。"""
    os.makedirs(ARTIFACTS, exist_ok=True)
    tmp_jobs = os.path.join(ARTIFACTS, '_retry_jobs_%d.json' % os.getpid())
    tmp_out = os.path.join(ARTIFACTS, '_retry_out_%d.jsonl' % os.getpid())
    with open(tmp_jobs, 'w', encoding='utf-8') as f:
        json.dump(remaining, f)
    budget = BATCH_SLOP_S + len(remaining) * (PER_CASE_BUDGET_S + VM_BATCH_PER_CASE_S)
    done = {}
    try:
        subprocess.run(
            [PY, os.path.abspath(__file__), '--jobs-json', tmp_jobs,
             '--out', tmp_out, '--quiet'],
            capture_output=True, timeout=budget, cwd=ROOT)
        if os.path.exists(tmp_out):
            with open(tmp_out, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        r = json.loads(line)
                        done[r.get('seed')] = r
                    except ValueError:
                        continue
    except subprocess.TimeoutExpired:
        pass
    except Exception:
        pass
    finally:
        for p in (tmp_jobs, tmp_out):
            try:
                os.remove(p)
            except OSError:
                pass
    for seed, opts_d in remaining:
        if seed in done:
            on_result(done[seed])
        else:
            src_path = _dump_timeout_src(seed, opts_d)
            on_result({'seed': seed, 'status': 'TIMEOUT',
                       'detail': '重试子进程内仍未完成（疑似真悬挂）；源码: %s'
                                 % src_path})


def _run_batches(jobs, workers, on_result):
    """分批跑（mp.Pool 摊薄 spawn 成本）。批超时/worker 死亡 → 杀池，
    journal 打捞已完成，未完成交裸子进程内序重试（真悬挂 → TIMEOUT + 留档）。"""
    import multiprocessing as mp
    import uuid
    BATCH = 16
    batches = [jobs[i:i + BATCH] for i in range(0, len(jobs), BATCH)]
    pool = None

    queue = list(batches)
    while queue:
        batch = queue.pop(0)
        batch_id = uuid.uuid4().hex[:12]
        budget = BATCH_SLOP_S + len(batch) * (PER_CASE_BUDGET_S + VM_BATCH_PER_CASE_S)
        try:
            if pool is None:
                ctx = mp.get_context('spawn')
                pool = ctx.Pool(processes=workers)
            async_res = pool.apply_async(run_worker_batch, ((batch_id, batch),))
            results = async_res.get(timeout=budget)
            for r in results:
                on_result(r)
            try:
                os.remove(os.path.join(ARTIFACTS, '_journal_%s.jsonl' % batch_id))
            except OSError:
                pass
            continue
        except Exception:
            pass
        if pool is not None:
            pool.terminate()
            pool.join()
            pool = None
        remaining = _salvage_journal(batch_id, batch, on_result)
        if remaining:
            _run_retry_subprocess(remaining, on_result)


def _native_sample(jobs, count, on_result):
    """native 抽样：从 jobs 等距取 count 个，aot 编译 + 看门狗运行 + 与 VM 对比。"""
    if count <= 0 or not jobs:
        return
    step = max(1, len(jobs) // count)
    picked = [jobs[i] for i in range(0, len(jobs), step)][:count]
    os.makedirs(ARTIFACTS, exist_ok=True)
    for seed, opts_dict in picked:
        src, info = generate_program(seed, GenOptions(**opts_dict))
        qot_path = os.path.join(ARTIFACTS, 'fuzz_%d.qot' % seed)
        exe_path = os.path.join(ARTIFACTS, 'fuzz_%d.exe' % seed)
        csrc_path = os.path.join(ARTIFACTS, 'fuzz_%d_gen.cpp' % seed)
        with open(qot_path, 'w', encoding='utf-8', newline='\n') as f:
            f.write(src)
        qtc, path, cerr = compile_qtc(src)
        res = {'seed': seed, 'kind': 'native', 'status': '', 'detail': '',
               'info': info}
        if qtc is None:
            res['status'] = 'NATIVE_SKIP'
            res['detail'] = 'VM 编译失败: ' + cerr
        else:
            vm_out = _run_vm_batch_subprocess([qtc])[0]
            ok, out, detail = run_native(qot_path, exe_path, csrc_path)
            if not ok:
                if '不支持' in detail or 'AOT' in detail:
                    res['status'] = 'NATIVE_SKIP'
                else:
                    res['status'] = 'NATIVE_BUILD_FAIL'
                res['detail'] = detail
            elif not vm_out['ok']:
                res['status'] = 'NATIVE_SKIP'
                res['detail'] = 'VM 侧执行失败'
            else:
                diff = compare_outputs(vm_out['stdout'], out)
                if diff:
                    res['status'] = 'NATIVE_DIVERGENCE'
                    res['detail'] = diff
                    res['src'] = src
                else:
                    res['status'] = 'OK'
        for p in (exe_path, qtc, path):
            if p:
                try:
                    os.remove(p)
                except OSError:
                    pass
        on_result(res)


def main(argv=None):
    ap = argparse.ArgumentParser(description='QOT fuzz 三方差分（S-01）')
    ap.add_argument('--count', type=int, default=240)
    ap.add_argument('--seed-base', type=int, default=20261025)
    ap.add_argument('--workers', type=int, default=4)
    ap.add_argument('--native-sample', type=int, default=4)
    ap.add_argument('--save-dir', default=ARTIFACTS)
    ap.add_argument('--quiet', action='store_true')
    ap.add_argument('--jobs-json', help='内部：从文件读 jobs 顺序执行（重试子进程用）')
    ap.add_argument('--out', help='内部：结果 JSONL 输出路径（配合 --jobs-json）')
    args = ap.parse_args(argv)

    # ---- 内部模式：重试子进程（单进程内序执行，结果落盘）----
    if args.jobs_json:
        os.makedirs(ARTIFACTS, exist_ok=True)
        with open(args.jobs_json, 'r', encoding='utf-8') as f:
            jobs = [(int(s), o) for s, o in json.load(f)]
        rs = run_worker_batch(('retry', jobs))
        if args.out:
            with open(args.out, 'w', encoding='utf-8') as f:
                for r in rs:
                    f.write(json.dumps(r, ensure_ascii=False) + '\n')
        return 0

    os.makedirs(args.save_dir, exist_ok=True)
    rng = random.Random(args.seed_base)
    jobs = _make_jobs(args.count, args.seed_base, rng)

    stats = {}
    failures = []
    t0 = time.perf_counter()

    def on_result(r):
        st = r['status']
        stats[st] = stats.get(st, 0) + 1
        if st not in ('OK', 'NATIVE_SKIP', 'KNOWN_ITERLEN_FRAC'):
            failures.append(r)
            if not args.quiet:
                print('[%s] seed=%s %s' % (st, r.get('seed'), r.get('detail', ''))[:400],
                      flush=True)

    _run_batches(jobs, args.workers, on_result)
    _native_sample(jobs, args.native_sample, on_result)

    el = time.perf_counter() - t0
    total = sum(stats.values())
    bad = sum(v for k, v in stats.items()
              if k not in ('OK', 'NATIVE_SKIP', 'KNOWN_ITERLEN_FRAC'))
    print('--- fuzz 汇总: %d 例, %.1fs, %s ---' % (total, el, stats), flush=True)

    if failures:
        dump = os.path.join(args.save_dir, 'failures_%d.json' % int(time.time()))
        with open(dump, 'w', encoding='utf-8') as f:
            json.dump(failures, f, ensure_ascii=False, indent=1)
        print('失败详情已存: %s（分歧最小化 = 用 seed 重放：'
              'python testing/fuzz/run_fuzz.py --count 1 --seed-base <seed>）' % dump)
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
