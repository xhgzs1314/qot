# -*- coding: utf-8 -*-
"""前端模块 Cython 编译

用法（经 _build_frontend.bat 调用；build_ext 前须先 vcvarsall x64 设好
MSVC 环境 —— Python 3.9.12 官方 MSVC 版，扩展用 cl 最稳）:

    python setup_frontend.py build_ext --inplace

范围（12 个纯逻辑模块，lir 管线 + 发射层）：
  lir / lir2 / lir_cgen / lir_opt / lir_ssa / lir_lower / lir_native /
  bytecode / ast_encoder / astcomopt / qot_hash / qot_builtins

**不在范围的模块及原因（勿加，会静默崩坏）**：
  * grammar.py / token_char.py：ply 读取 p_*/t_* 函数的 co_varnames 与
    docstring 做语法/词法规则收集，Cython 编译后的代码对象没有真实
    co_varnames —— 规则收集会错乱。
  * qot_macro.py：import index（解释器本体），与构建入口纠缠。

产物：build/frontend_c/ 放生成的 .c 中间产物；.pyd 原地落在源码旁
（EXT_SUFFIX 版本限定 —— 其它 Python 版本自动回落纯 .py，无需分发）。
.freshness 护栏见 _frontend_ext_guard.py（改 .py 不重编 → .pyd 自动
让位回纯 Python，绝不静默用旧扩展）。
"""
import os

# 唯一真相源：_frontend_manifest.py 从这里 import 同一份名单
MODULES = ['lir', 'lir2', 'lir_cgen', 'lir_opt', 'lir_ssa', 'lir_lower',
           'lir_native', 'bytecode', 'ast_encoder', 'astcomopt',
           'qot_hash', 'qot_builtins']

if __name__ == '__main__':
    import shutil
    from setuptools import Extension, setup
    from Cython.Build import cythonize

    exts = cythonize(
        [m + '.py' for m in MODULES],
        language_level=3,
        nthreads=os.cpu_count() or 4,
        compiler_directives={
            'language_level': 3,
            # 纯 Python 语义对齐：不做任何 cdef 特化，行为与 .py 逐位一致
            'embedsignature': False,
            'binding': True,
            'initializedcheck': True,
            # 【关键】Cython 3 默认把 PEP 484 注解当强制类型声明（实参不是
            # 声明类型就 TypeError）—— 纯 Python 从不检查注解，且本项目
            # 实际存在注解与实参不符的容忍用法（astcomopt._make_node 的
            # typ 实参有 tuple 形态，unit/ci 已抓到）。关掉才是纯语义。
            'annotation_typing': False,
        },
    )
    # Cython 3.0 的 cythonize 无 build_path 选项，.c 会生成在源码旁 ——
    # 挪进 build/frontend_c/ 保持项目根干净，Extension 改指新路径。
    c_dir = os.path.join('build', 'frontend_c')
    os.makedirs(c_dir, exist_ok=True)
    for e in exts:
        src = e.sources[0]
        dst = os.path.join(c_dir, os.path.basename(src))
        if os.path.abspath(src) != os.path.abspath(dst):
            if os.path.exists(dst):
                os.remove(dst)
            shutil.move(src, dst)
        e.sources = [dst]
    setup(name='qot-frontend-ext', ext_modules=exts)
