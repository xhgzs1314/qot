# -*- coding: utf-8 -*-
"""qot pkg —— 包格式与构建流水线

包 = 目录 + `qot.pkg` 清单（TOML，qot_toml 解析）：

    [package]
    name = "logstat"
    version = "0.1.0"
    entry = "main.qot"            # 相对包目录

    # 可选：C 插件（M7 v2 ABI 公共头），按声明顺序构建到 <pkg>/build/plugins/
    [[plugin]]
    name = "shaper"
    source = "plugin/shaper.c"    # 相对包目录；依赖 = ot-asset/c/plugin/qot_plugin_abi.h

子命令（`python index.py pkg <sub> ...`）：
  * init  <dir>            生成模板（qot.pkg + main.qot）
  * build <dir> [-o out] [--opt]
                           先建插件（g++ -shared，幂等：源/头比产物旧即跳过），
                           再 AOT 编译 entry → <pkg>/build/<name>.exe
  * run   <dir> [-- args]  build（增量）后运行产物；**cwd = 包目录**（插件与
                           数据的相对路径稳定）；args 透传（args_get 口径）
"""
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_INC = os.path.join(HERE, 'ot-asset', 'c', 'plugin')

PKG_FILE = 'qot.pkg'


class PkgError(Exception):
    pass


def _toml_load(path):
    from qot_toml import load as _load
    return _load(path)


def load_manifest(pkg_dir):
    """读 + 校验 qot.pkg → dict（package 段必填 name/version/entry）。"""
    manifest = os.path.join(pkg_dir, PKG_FILE)
    if not os.path.isfile(manifest):
        raise PkgError('包目录缺少 %s: %s' % (PKG_FILE, pkg_dir))
    data = _toml_load(manifest)
    pkg = data.get('package')
    if not isinstance(pkg, dict):
        raise PkgError('%s: 缺少 [package] 段' % manifest)
    for key in ('name', 'entry'):
        if not isinstance(pkg.get(key), str) or not pkg[key]:
            raise PkgError('%s: [package] 缺少 %s' % (manifest, key))
    pkg.setdefault('version', '0.0.0')
    plugins = data.get('plugin') or []
    if not isinstance(plugins, list):
        raise PkgError('%s: [[plugin]] 必须是表数组' % manifest)
    for plg in plugins:
        for key in ('name', 'source'):
            if not isinstance(plg.get(key), str) or not plg[key]:
                raise PkgError('%s: [[plugin]] 缺少 %s' % (manifest, key))
    return {'package': pkg, 'plugin': plugins}


def _cc():
    return shutil.which('g++') or shutil.which('gcc') or shutil.which('cc')


def build_plugins(pkg_dir, plugins, verbose=True):
    """按声明构建 C 插件 → <pkg>/build/plugins/（幂等）。返回产物路径表。"""
    out_dir = os.path.join(pkg_dir, 'build', 'plugins')
    os.makedirs(out_dir, exist_ok=True)
    cc = _cc()
    if not cc:
        raise PkgError('未找到 C 编译器（g++/gcc）——插件构建需要')
    built = []
    for plg in plugins:
        src = os.path.normpath(os.path.join(pkg_dir, plg['source']))
        if not os.path.isfile(src):
            raise PkgError('插件源不存在: %s' % src)
        suffix = '.dll' if sys.platform == 'win32' else '.so'
        out = os.path.join(out_dir, plg.get('out') or (plg['name'] + suffix))
        deps = [src, os.path.join(PLUGIN_INC, 'qot_plugin_abi.h')]
        if os.path.exists(out) and all(
                os.path.getmtime(d) <= os.path.getmtime(out) for d in deps):
            if verbose:
                print('[pkg] 插件已是最新: %s' % os.path.relpath(out, pkg_dir),
                      flush=True)
        else:
            cmd = [cc, '-std=c++17', '-O1', '-shared', '-I' + PLUGIN_INC,
                   src, '-o', out, '-static-libgcc', '-static-libstdc++']
            p = subprocess.run(cmd, capture_output=True, text=True,
                               encoding='utf-8', errors='replace')
            if p.returncode != 0 or not os.path.exists(out):
                sys.stderr.write((p.stderr or p.stdout or '')[-1200:])
                raise PkgError('插件构建失败: %s' % plg['name'])
            if verbose:
                print('[pkg] 插件已构建: %s' % os.path.relpath(out, pkg_dir),
                      flush=True)
        built.append(out)
    return built


def cmd_build(pkg_dir, out=None, opt=False, verbose=True):
    """build 子命令：插件编排 + AOT 编译 entry。返回产物路径。"""
    man = load_manifest(pkg_dir)
    pkg = man['package']
    entry = os.path.normpath(os.path.join(pkg_dir, pkg['entry']))
    if not os.path.isfile(entry):
        raise PkgError('入口不存在: %s' % entry)
    build_dir = os.path.join(pkg_dir, 'build')
    os.makedirs(build_dir, exist_ok=True)
    build_plugins(pkg_dir, man['plugin'], verbose=verbose)
    exe = out or os.path.join(build_dir, pkg['name'] + '.exe')
    args = [sys.executable, os.path.join(HERE, 'index.py'), 'aot', entry, exe]
    if opt:
        args.append('--opt')
    if verbose:
        print('[pkg] AOT 编译: %s -> %s' % (pkg['entry'], os.path.relpath(exe, pkg_dir)),
              flush=True)
    p = subprocess.run(args, capture_output=True, text=True,
                       encoding='utf-8', errors='replace')
    if not os.path.exists(exe):
        detail = (p.stderr or p.stdout or '').strip().splitlines()
        raise PkgError('AOT 编译失败:\n%s' % '\n'.join(detail[-8:]))
    return exe


def cmd_run(pkg_dir, extra, opt=False, verbose=True):
    """run 子命令：build（增量）+ 运行；cwd = 包目录；透传 args。返回退出码。"""
    exe = cmd_build(pkg_dir, opt=opt, verbose=verbose)
    return subprocess.run([exe] + list(extra), cwd=pkg_dir).returncode


def cmd_init(pkg_dir):
    """init 子命令：生成最小可跑的模板包（清单 + hello 入口）。"""
    if os.path.exists(os.path.join(pkg_dir, PKG_FILE)):
        raise PkgError('%s 已存在（拒绝覆盖）' % os.path.join(pkg_dir, PKG_FILE))
    os.makedirs(pkg_dir, exist_ok=True)
    name = os.path.basename(os.path.normpath(pkg_dir)) or 'hello'
    with open(os.path.join(pkg_dir, PKG_FILE), 'w', encoding='utf-8', newline='\n') as f:
        f.write('[package]\nname = "%s"\nversion = "0.1.0"\nentry = "main.qot"\n' % name)
    with open(os.path.join(pkg_dir, 'main.qot'), 'w', encoding='utf-8', newline='\n') as f:
        f.write('// 由 `qot pkg init` 生成的模板包（M6）。\n'
                'output("hello from " + args_get()[0]);\n')
    print('[pkg] 模板已生成: %s' % pkg_dir)
    print('[pkg] 试试: python index.py pkg run %s -- world' % pkg_dir)
    return 0


def main(argv):
    """pkg 子命令入口（index.py 转发 argv[2:]）。"""
    if not argv:
        print('用法: pkg <init|build|run> <包目录> [选项] [-- 透传参数...]')
        print('  init  <dir>              生成模板包')
        print('  build <dir> [-o 产物] [--opt]')
        print('  run   <dir> [--opt] [-- args...]   构建并运行（cwd=包目录）')
        return 1
    sub = argv[0]
    rest = argv[1:]
    try:
        if sub == 'init':
            if not rest:
                raise PkgError('用法: pkg init <dir>')
            return cmd_init(rest[0])
        if sub in ('build', 'run'):
            if not rest:
                raise PkgError('用法: pkg %s <包目录> [...]' % sub)
            pkg_dir = rest[0]
            if not os.path.isdir(pkg_dir):
                raise PkgError('包目录不存在: %s' % pkg_dir)
            extra = []
            opt = '--opt' in rest
            if '--' in rest:
                extra = rest[rest.index('--') + 1:]
            if sub == 'build':
                out = None
                if '-o' in rest:
                    idx = rest.index('-o')
                    if idx + 1 >= len(rest):
                        raise PkgError('-o 需要路径参数')
                    out = rest[idx + 1]
                cmd_build(pkg_dir, out=out, opt=opt)
                return 0
            return cmd_run(pkg_dir, extra, opt=opt)
        raise PkgError('未知子命令: %s' % sub)
    except PkgError as e:
        print('[错误] [pkg] %s' % e, file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
