// ============================================================================
// QOTNative：qotrt.lib 定义 TU
// ----------------------------------------------------------------------------
// 把 header-only 的运行时（qot_aot_runtime.h，函数全 static / TU-local）
// 一次性预编成静态库，供 NativeBackend 的 COFF 目标按 C ABI 直调：
//   * `#define static extern "C"` 让全部 411 个文件作用域函数定义变为外部
//     链接 —— 符号名 = 源码名（qotrt_xalloc、qotrt_bi_arr_new、qot_sjmp ...），
//     发射器零映射成本；
//   * 前置条件（已在头内完成 QOTNative 重构）：
//       - 函数内局部 static 全部提升到文件作用域（宏翻转不改变语义）；
//       - FFI 模板区（extern "C" 不能修饰模板）由 QOT_RT_LIB_EXPORT +
//         push_macro/pop_macro 对还原字面 static —— 模板实例化是
//         COMDAT/weak 符号，静态库可解析，无需 extern 化；
//       - 4 个与内部助手同名重载的内置入口改名 bi_ 前缀（extern "C"
//         同名符号会重定义冲突：bi_is_err/bi_arr_new/bi_arr_push/
//         bi_dict_new，builtins.toml native 字段同步）；
//       - 系统头前置展开（宏不落系统头展开期：swprintf.inl 重载内联、
//         numeric_limits 类内 static 成员都会被 extern "C" 炸掉）；
//   * 【C++ 语义要点】头内无初始化器的 static 变量定义经宏翻转后变成
//     纯 extern 声明（带初始化器才是定义）→ 文件尾「全局量定义补全区」
//     统一补齐（初值逐项对齐头内 #else 分支；*_free 的初值是 -1 不是 0）；
//   * 单线程运行时（与现状一致）：全局量（常量池/着陆栈/tick/句柄表）
//     在本 TU 唯一定义，native 目标 extern 引用（qotrt_consts 等热路径
//     直访，不经函数）；
//   * 编译（qot_cache.ensure_runtime_lib，flags 单点维护）：
//       g++ -std=c++17 -O2 -finput-charset=utf-8 -w -c qotrt_lib.cpp -o ...
//       ar rcs qotrt_<hash>.lib qotrt_lib.o
// ============================================================================

// ---- 系统头前置展开（宏污染隔离）----
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <cstdint>
#include <cstddef>
#include <cmath>
#include <setjmp.h>
#include <ctime>
#include <chrono>
#include <type_traits>
#include <cerrno>
#include <stddef.h>
#include <stdint.h>
#if defined(_WIN32)
#define WIN32_LEAN_AND_MEAN
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <winsock2.h>
#include <ws2tcpip.h>
#include <windows.h>
#include <io.h>
#include <direct.h>
#include <sys/stat.h>
#endif

// FFI 模板区还原字面 static 的开关（runtime.h 内 push_macro/pop_macro 对）
#define QOT_RT_LIB_EXPORT 1

#define static extern "C"
#include "qot_aot_runtime.h"
#undef static

// ============================================================================
// 全局量定义补全区
// ----------------------------------------------------------------------------
// 头内「无初始化器的文件作用域 static 变量」经宏翻转后是纯 extern 声明
// （带初始化器 = 定义，无需处理）—— 此处只补无初始化器者。清单与头内
// 全局量全集同步：新增文件作用域变量若不带初始化器，必须在此补一行。
// ============================================================================
extern "C" {

QotrtArenaMark qotrt_arena_stack[8];
V16 qotrt_thrown;
char qotrt_ffi_err[256];
QotrtCharIntern qotrt_char_intern[256];
QotrtCharInternInit qotrt_char_intern_init;
QotrtScLut qotrt_sc_lut;
V16 qotrt_env_at_none_cell;
const char *qrt_ffi_types_buf[9];
const char *qotrt_ffi_cache_buf[9];

// ---- inline 函数符号钉子 ----
// extern "C" inline 函数在 -O2 下若所有调用点全内联则不发射独立定义
// （lib 里无符号）—— native obj 直调这些名字会缺解析。取地址强制发射
// 非内联副本（weak/COMDAT，库内可解析）。新增被发射器直调的 inline
// 函数时在此补一行。
extern "C" void *qotrt_inline_pins[] = {
    (void *)qotrt_vhash,
    (void *)qotrt_fdiv,
    (void *)qotrt_fmod_,
    (void *)qotrt_env_up2,
    (void *)qotrt_env_at2,
    (void *)qotrt_obj_v,
    (void *)qotrt_slot_keyeq,
    (void *)qotrt_probe_step,
    (void *)qotrt_dict_find1,
};

} // extern "C"
