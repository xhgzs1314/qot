/* ===========================================================================
 * qot_plugin_abi.h —— QOT 原生插件公共 ABI（M7 冻结 · v2 · ）
 *
 * 本文件是插件作者的**唯一**依赖：一个自包含公共头（不依赖 qot_aot_runtime.h）。
 * 冻结文档：docs/（布局表 / 所有权 / 错误通道 / 版本规则）。
 *
 * 修改本文件的纪律（SPEC R17）：
 *   * qot_plugin_api 只允许**尾部追加**字段，且必须 bump QOT_PLUGIN_ABI_VERSION；
 *   * 本文件与 ot-asset/c/aot/qot_aot_runtime.h 的
 *     `8< BEGIN/END QOT_PLUGIN_ABI` 嵌入段**逐字节一致**
 *     （testing/unit/test_ffi_abi.py 钉住）—— 改这里必须同步改那里；
 *   * 语义变更（不改布局）同样 bump 版本并在 docs/ 记变更行。
 * =========================================================================== */
#ifndef QOT_PLUGIN_ABI_H
#define QOT_PLUGIN_ABI_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* ABI 版本：v1 = CP-4 的 C 标量调用面（ffi_*）；v2 = M7 冻结（混族
 * ffi_call_v 分派器 + 本插件 ABI）。插件按它判兼容。 */
#define QOT_PLUGIN_ABI_VERSION 2u

/* qot_value 标签（V16 布局的 t 字段；宿主私有标签不进本表） */
#define QOT_VT_NONE 0
#define QOT_VT_NULL 1
#define QOT_VT_BOOL 2
#define QOT_VT_NUM 3
#define QOT_VT_STR 4
#define QOT_VT_ARR 5
#define QOT_VT_DICT 6

/* 值 = 16 字节（tag + pad + 8B 载荷）。**可移植插件只准经 api 访问值**，
 * 不许直接解引用 u.p（那是宿主私有布局：QStr/QArr/QDict 结构与池分配）；
 * 直读布局只对 qotrt 宿主有效，且不受本头保护。 */
typedef struct qot_value
{
    uint8_t t;
    uint8_t pad[7];
    union
    {
        double d;
        void *p;
    } u;
} qot_value;

/* 插件函数签名：ret 为返回值出参（插件必须写或不写=null）；args 指向
 * argc 个连续 qot_value（只读，仅在本次调用内有效）。 */
struct qot_plugin_api;
typedef void (*qot_plugin_fn)(qot_value *ret, const struct qot_plugin_api *api,
                              const qot_value *args, int argc);

/* 宿主提供的能力面（v2 冻结，18 个成员 = 8B 头 + 17 指针；新字段只能
 * 尾部追加）。sizeof(qot_plugin_api) = 144（x64），下方有编译期探针。 */
typedef struct qot_plugin_api
{
    uint32_t abi_version; /* 恒 QOT_PLUGIN_ABI_VERSION */
    uint32_t reserved;    /* 0 */

    /* ---- 值构造（写入 out/ret；宿主在 qot 堆上按需分配） ---- */
    void (*set_null)(qot_value *v);
    void (*set_bool)(qot_value *v, int b);
    void (*set_num)(qot_value *v, double d);
    /* 拷贝进宿主串；len < 0 → 按 NUL 结尾测定（有界 1MB）；len 超界截断 */
    void (*set_str)(qot_value *v, const char *s, int64_t len);

    /* ---- 值访问（借用引用，仅在本次插件函数调用内有效） ---- */
    int (*tag_of)(const qot_value *v); /* QOT_VT_* */
    double (*num_of)(const qot_value *v);      /* 非数值 → 0.0 */
    int (*bool_of)(const qot_value *v);        /* 非布尔 → 0 */
    /* 字符串内容（**非 NUL 结尾**，以 *len 为准）；非字符串 → NULL、*len=-1 */
    const char *(*str_of)(const qot_value *v, int64_t *len);

    /* ---- 容器访问（只读借用；非对应容器 → -1 / NULL） ---- */
    int64_t (*arr_len)(const qot_value *v);
    const qot_value *(*arr_at)(const qot_value *v, int64_t i); /* 越界 → NULL */

    /* ---- 容器构造 ---- */
    void (*arr_new)(qot_value *out);
    void (*arr_push)(qot_value *arr, const qot_value *v);
    void (*dict_new)(qot_value *out);
    /* k 为 UTF-8 字节串（无需 NUL，klen 按 C 串语义读；有界 1MB） */
    void (*dict_set)(qot_value *d, const char *k, const qot_value *v);
    /* 命中 → *out = 值、返回 1；未命中/非字典 → 返回 0、*out = null */
    int (*dict_get)(const qot_value *d, const char *k, qot_value *out);

    /* ---- 错误通道（文本挂宿主 ffi_last_error()；plugin_call 返回 null） ---- */
    void (*set_error)(const char *msg);

    /* ---- 注册（仅在 qot_plugin_init 内调用；name 全局唯一） ----
     * 返回 1 = 成功；0 = 失败（空名 / 重名 / 越界 argc）。 */
    int (*reg)(const char *name, qot_plugin_fn fn, int argc, const char *doc);
} qot_plugin_api;

/* 布局探针（两侧各钉一份：任一副本单方面改结构 → 这里先炸） */
typedef char qot_plugin_api_size_probe[(sizeof(qot_plugin_api) == 144) ? 1 : -1];

/* 插件导出入口：加载时宿主调用一次。返回值 = 成功注册的函数个数
 * （必须与 reg 成功次数一致，否则宿主判定 init 失败并回滚）；
 * 负数 = 插件自认失败（宿主卸载并报错）。允许返回 0（无函数插件）。 */
#if defined(_WIN32)
#define QOT_PLUGIN_EXPORT __declspec(dllexport)
#else
#define QOT_PLUGIN_EXPORT __attribute__((visibility("default")))
#endif

/* 线程契约：插件函数只会在装载它的 QOT 主线程被调用；插件不得把
 * qot_value/宿主指针交给其他线程使用。 */
QOT_PLUGIN_EXPORT int qot_plugin_init(const qot_plugin_api *api);

#ifdef __cplusplus
} /* extern "C" */
#endif

#endif /* QOT_PLUGIN_ABI_H */
