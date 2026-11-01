# -*- coding: utf-8 -*-
"""VM 批执行器（子进程入口）—— run_fuzz.py 的隔离执行单元。

为什么存在：qotrun.dll 以 /MT 静态 CRT 编译，其 printf 缓冲（重定向到文件时
4KB 全缓冲）无法被父进程的 msvcrt.fflush 触达 —— 进程内捕获必然出现
「输出滞留/串 case」（20261035/37 实证：批内 N-1 例输出漂移，单例复跑全等）。
本进程每批执行 K 个 .qtc，进程退出时 CRT 自然 flush，stdout 按
====CASE <i>==== 分段回传。隔离彻底、启动成本被 K 摊薄。

stdin : JSON {"cases": ["<qtc 路径>", ...]}
stdout: ====CASE <i>====\n<该例 stdout>（UTF-8）
"""
import ctypes
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DLL = os.path.join(ROOT, 'qotrun.dll')


def main():
    payload = json.loads(sys.stdin.buffer.read().decode('utf-8'))
    dll = ctypes.CDLL(DLL)
    dll.vm_init.restype = ctypes.c_void_p
    dll.vm_execute_file.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
    dll.vm_execute_file.restype = ctypes.c_bool
    dll.vm_destroy.argtypes = [ctypes.c_void_p]
    for i, qtc in enumerate(payload['cases']):
        # 二进制写标记：文本模式的 newline 翻译（\n → \r\n）会破坏分段解析
        sys.stdout.buffer.write(('====CASE %d====\n' % i).encode('ascii'))
        sys.stdout.buffer.flush()
        vm = dll.vm_init()
        try:
            dll.vm_execute_file(vm, qtc.encode('utf-8'))
        finally:
            dll.vm_destroy(vm)
    sys.stdout.buffer.flush()


if __name__ == '__main__':
    main()
