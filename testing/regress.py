"""QOT 回归测试工具集：将每个 .qot 文件编译为 .qtc，通过 qotrun.dll 运行并捕获输出。
python _corpus/regress.py record -> 写入 baseline.json
python _corpus/regress.py check -> 与 baseline.json 进行比较
"""
import os, sys, json, subprocess, time, hashlib
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
BASE = os.path.join(HERE, 'baseline.json')
PY_ = sys.executable

def run_one(qot):
    name = os.path.basename(qot)[:-4]
    qtc = os.path.join(HERE, name + '.qtc')
    c = subprocess.run([PY_, 'index.py', 'bstrust', 'compile', qot, qtc],
                       cwd=ROOT, capture_output=True, text=True, timeout=300)
    if not os.path.exists(qtc):
        return {'compile_ok': False, 'stdout': '', 'err': (c.stdout + c.stderr)[-400:]}
    code = hashlib.sha256(open(qtc, 'rb').read()).hexdigest()[:16]
    t = time.perf_counter()
    r = subprocess.run([PY_, 'index.py', 'bstrust', 'run', qtc],
                       cwd=ROOT, capture_output=True, text=True, timeout=300)
    el = time.perf_counter() - t
    out = [ln.rstrip() for ln in r.stdout.splitlines() if ln.strip()]
    return {'compile_ok': True, 'qtc_sha': code, 'stdout': out, 'secs': round(el, 3)}

def collect():
    res = {}
    for f in sorted(os.listdir(HERE)):
        if f.endswith('.qot'):
            res[f] = run_one(os.path.join(HERE, f))
    return res

if __name__ == '__main__':
    mode = sys.argv[1] if len(sys.argv) > 1 else 'check'
    cur = collect()
    if mode == 'record':
        json.dump(cur, open(BASE, 'w'), indent=1)
        for k, v in cur.items():
            print(f"{k:20s} ok={v['compile_ok']} t={v.get('secs')}s out={v.get('stdout')}")
        print(f"\nrecorded {len(cur)} cases -> baseline.json")
    else:
        old = json.load(open(BASE))
        bad = 0
        for k, v in cur.items():
            o = old.get(k)
            if o is None:
                print(f"[NEW ] {k}"); continue
            if o['stdout'] != v['stdout'] or o['compile_ok'] != v['compile_ok']:
                bad += 1
                print(f"[FAIL] {k}\n   was: {o.get('stdout')}\n   now: {v.get('stdout')}")
            else:
                sp = ''
                if o.get('secs') and v.get('secs'):
                    sp = f"  {o['secs']}s -> {v['secs']}s ({o['secs']/max(v['secs'],1e-9):.2f}x)"
                print(f"[ OK ] {k}{sp}")
        print(f"\n{'ALL PASS' if bad==0 else str(bad)+' FAILED'}  ({len(cur)} cases)")
        sys.exit(1 if bad else 0)
