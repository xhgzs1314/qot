// ===========================================================================
// qot_demo_plugin.c —— QOT 原生插件 ABI（M7 冻结 v2）的演示/测试插件
//
// 唯一依赖 = ot-asset/c/plugin/qot_plugin_abi.h（可移植插件只经 api 访问值，
// 不直读宿主布局 —— docs/ §插件）。被两个宿主共用：
//   * native（AOT exe，qotrt 宿主）—— p21_prim_plugin 差分用例；
//   * 解释器（ctypes 宿主）—— 同一用例的 oracle 侧。
// 构建：python testing/tools/build_demo_plugin.py（prerun 同步钩子）。
// ===========================================================================
#include "qot_plugin_abi.h"

#include <math.h>
#include <string.h>

/* add(a, b) -> a + b（数值面） */
static void plug_add(qot_value *ret, const qot_plugin_api *api,
                     const qot_value *args, int argc)
{
    (void)argc;
    api->set_num(ret, api->num_of(&args[0]) + api->num_of(&args[1]));
}

/* sjoin(sep, parts) -> 以 sep 连接字符串数组（str 借用读 + set_str 拷贝写；
 * 结果上限 4KB，截断即得 —— 演示有界构造） */
static void plug_sjoin(qot_value *ret, const qot_plugin_api *api,
                       const qot_value *args, int argc)
{
    (void)argc;
    char buf[4096];
    int64_t out = 0;
    int64_t sepl = 0;
    const char *sep = api->str_of(&args[0], &sepl);
    if (!sep)
    {
        api->set_error("sjoin: separator must be string");
        api->set_null(ret);
        return;
    }
    int64_t n = api->arr_len(&args[1]);
    for (int64_t i = 0; i < n; i++)
    {
        if (i > 0)
        {
            for (int64_t k = 0; k < sepl; k++)
            {
                if (out < (int64_t)sizeof(buf) - 1)
                    buf[out++] = sep[k];
            }
        }
        int64_t sl = 0;
        const char *s = api->str_of(api->arr_at(&args[1], i), &sl);
        if (!s)
        {
            api->set_error("sjoin: element must be string");
            api->set_null(ret);
            return;
        }
        for (int64_t k = 0; k < sl; k++)
        {
            if (out < (int64_t)sizeof(buf) - 1)
                buf[out++] = s[k];
        }
    }
    api->set_str(ret, buf, out);
}

/* stats(nums) -> {"sum":Σ, "n":N, "min":m, "max":M}（dict 构造 + 数组遍历） */
static void plug_stats(qot_value *ret, const qot_plugin_api *api,
                       const qot_value *args, int argc)
{
    (void)argc;
    int64_t n = api->arr_len(&args[0]);
    if (n <= 0)
    {
        api->set_error("stats: empty array");
        api->set_null(ret);
        return;
    }
    double sum = 0.0, mn = 0.0, mx = 0.0;
    for (int64_t i = 0; i < n; i++)
    {
        const qot_value *e = api->arr_at(&args[0], i);
        double d = api->num_of(e);
        if (i == 0 || d < mn)
            mn = d;
        if (i == 0 || d > mx)
            mx = d;
        sum += d;
    }
    qot_value d;
    api->dict_new(&d);
    qot_value tmp;
    api->set_num(&tmp, sum);
    api->dict_set(&d, "sum", &tmp);
    api->set_num(&tmp, (double)n);
    api->dict_set(&d, "n", &tmp);
    api->set_num(&tmp, mn);
    api->dict_set(&d, "min", &tmp);
    api->set_num(&tmp, mx);
    api->dict_set(&d, "max", &tmp);
    *ret = d; /* qot_value = 16 字节值拷贝（公共头布局冻结） */
}

/* fail_if_negative(x) -> x；x < 0 走 set_error 通道（plugin_call 得 null +
 * ffi_last_error 非空） */
static void plug_fail_if_negative(qot_value *ret, const qot_plugin_api *api,
                                  const qot_value *args, int argc)
{
    (void)argc;
    double x = api->num_of(&args[0]);
    if (x < 0.0)
    {
        api->set_error("plugin: negative input");
        api->set_null(ret);
        return;
    }
    api->set_num(ret, x);
}

/* hypot3(x, y, z) -> sqrt(x²+y²+z²)：演示数学库 + 多参数数值面 */
static void plug_hypot3(qot_value *ret, const qot_plugin_api *api,
                        const qot_value *args, int argc)
{
    (void)argc;
    double x = api->num_of(&args[0]);
    double y = api->num_of(&args[1]);
    double z = api->num_of(&args[2]);
    api->set_num(ret, sqrt(x * x + y * y + z * z));
}

QOT_PLUGIN_EXPORT int qot_plugin_init(const qot_plugin_api *api)
{
    int n = 0;
    n += api->reg("add", plug_add, 2, "sum of two numbers");
    n += api->reg("sjoin", plug_sjoin, 2, "join string array with separator (4KB cap)");
    n += api->reg("stats", plug_stats, 1, "sum/n/min/max of a number array");
    n += api->reg("fail_if_negative", plug_fail_if_negative, 1, "error channel demo");
    n += api->reg("hypot3", plug_hypot3, 3, "sqrt(x*x+y*y+z*z)");
    return n; /* 契约：返回值 = 成功注册个数（5） */
}
