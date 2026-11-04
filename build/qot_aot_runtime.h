// ============================================================================
// QOT AOT 运行时
//
// 使用方式：lir_cgen.py 生成的程序源码 #include 本头文件，整个 exe 只编译一个 TU。
//
// 语义对齐铁律：
//   * 本文件每个 qotrt_* 函数都逐条对应字节码解释器（vm_executor.cpp / builtin.cpp）
//     的一个语义 handler；输出必须与 VM 逐字节一致，由 testing/run.py --suite native
//     与 A/B 差分长期守卫。
//   * 内存策略：Arena-free（只分配不释放，进程生命周期回收）——AOT exe 是整程序
//   * M-PERF 分配器：qotrt_xalloc/xfree size-class 池（本文件「分配器」节）。
//     程序级垃圾仍不回收；可证明所有权的临时量（qbuf 扩容旧块 / dict rehash
//     旧槽表 / 数组扩容旧块）经 xfree 进空闲表真实复用。大块直还 OS。
//   * 【字符串缓冲不变式】（字典键零拷贝借用与 1 字符缓存的安全性地基）：
//     一个 char 缓冲一旦成为某 QStr 的 s，就**永不释放、永不再写入**；
//     增长 = 分配新缓冲 + 迁移 + 重指（旧缓冲对别名保持旧字节）。
//     因此 (ptr,len) 视角永恒有效 —— 字典槽直接借用键缓冲（不再 strdup）。
// ============================================================================
#pragma once
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <cstdint>
#include <cmath>
#include <cstddef>
#include <setjmp.h>


#if defined(_MSC_VER)
#include <intrin.h>
#define QOTRT_PREFETCH(p) _mm_prefetch(reinterpret_cast<const char *>(p), _MM_HINT_T0)
#elif defined(__GNUC__) || defined(__clang__)
#define QOTRT_PREFETCH(p) __builtin_prefetch(static_cast<const void *>(p), 0, 0)
#else
#define QOTRT_PREFETCH(p) ((void)(p))
#endif

// ---------------------------------------------------------------- Value
enum QotType : uint8_t
{
    VT_NONE = 0,
    VT_NULL = 1,
    VT_BOOL = 2,
    VT_NUM = 3,
    VT_STR = 4,
    VT_ARR = 5,
    VT_DICT = 6,
    //闭包 / 错误对象 / 激活环境（env 仅内部使用）
    VT_CLOSURE = 7,
    VT_ERR = 8,
    VT_ENV = 9,
    //最小对象模型的实例（QObj：固定布局 + 类型 id）
    VT_OBJ = 10
};

// F2 拷贝控制的字节化通道：volatile 成员的地址要先经 cv-void* 两段式
// 转换（g++ 把「reinterpret_cast 直接去掉 volatile」按错误处理）。
#define QRT_V16_U_BYTES(v) \
    (reinterpret_cast<char *>(const_cast<void *>( \
         static_cast<const volatile void *>(&(v).u))))

struct V16
{
    uint8_t t;
    uint8_t pad[7];
    union
    {
        double d;
        void *p;
    } u;

    // try 函数的槽声明为 volatile（setjmp/longjmp 契约）。
    // 隐式拷贝控制对 volatile 左值/右值都不成立，这里补全一组：
    //   * 默认构造（声明了构造函数后必须显式保留）
    //   * 普通拷贝构造（= 声明任何构造函数后需显式给回）
    //   * 从 volatile 的转换构造（volatile 槽作右值：g[10] = s47）
    //   * volatile 限定的赋值（volatile 槽作左值：s41 = expr）
    // 全部逐成员拷贝；pad 无任何读取者不拷贝。/O2 下与平凡拷贝同价
    // （内联为一条 16B 搬运），基线由 --record 轮守卫。
    V16() = default;
    V16(const V16 &o)
    {
        t = o.t;
        char *du = QRT_V16_U_BYTES(*this);
        const char *su = QRT_V16_U_BYTES(o);
        for (int k = 0; k < 8; k++)
            du[k] = su[k];
    }
    V16(const volatile V16 &o)
    {
        t = o.t;
        char *du = QRT_V16_U_BYTES(*this);
        const char *su = QRT_V16_U_BYTES(o);
        for (int k = 0; k < 8; k++)
            du[k] = su[k];
    }
    V16 &operator=(const V16 &o)
    {
        t = o.t;
        char *du = QRT_V16_U_BYTES(*this);
        const char *su = QRT_V16_U_BYTES(o);
        for (int k = 0; k < 8; k++)
            du[k] = su[k];
        return *this;
    }
    V16 &operator=(const V16 &o) volatile
    {
        t = o.t;
        char *du = QRT_V16_U_BYTES(*this);
        const char *su = QRT_V16_U_BYTES(o);
        for (int k = 0; k < 8; k++)
            du[k] = su[k];
        return *const_cast<V16 *>(this);
    }
    // volatile 槽作右值的赋值（`g[10] = s47;` / `a[0] = s48;`）：
    // const volatile 引用可【直接绑定】volatile 左值，免去转换序列
    V16 &operator=(const volatile V16 &o)
    {
        t = o.t;
        char *du = QRT_V16_U_BYTES(*this);
        const char *su = QRT_V16_U_BYTES(o);
        for (int k = 0; k < 8; k++)
            du[k] = su[k];
        return *this;
    }
    V16 &operator=(const volatile V16 &o) volatile
    {
        t = o.t;
        char *du = QRT_V16_U_BYTES(*this);
        const char *su = QRT_V16_U_BYTES(o);
        for (int k = 0; k < 8; k++)
            du[k] = su[k];
        return *const_cast<V16 *>(this);
    }
};
#undef QRT_V16_U_BYTES

// ---------------------------------------------------------------- QStr（M-PERF 布局）
// len/cap/s 语义不变；新增：hash 缓存（T-33 的 qotrt 版，VM StringBox 同能力）
// 与 shared 标志。QStr 头 = 32B（含填充），走池分配。
//   * shared=1 的串（常量池 / 1 字符缓存 / dict_keys 借用视图）内容永不变更，
//     追加快路径必须对它退全量拷贝；
//   * shared=0 的串仍可能被别名持有（V16 值拷贝无引用计数），原地追加只允许
//     发生在 cgen 追加资格分析证明「本槽独占」的槽位上（lir_cgen.py），shared
//     位是运行期第二道闸。
struct QStr
{
    int32_t len;
    int32_t cap;           // 私有追加预算（≥len；shared 串恒 == len）
    char *s;               // NUL 结尾；len 为字节数（不含 NUL）
    mutable uint64_t hash; // 0 = 尚未计算
    uint8_t shared;
};

struct QArr
{
    int32_t len;
    int32_t cap;
    V16 *a;
};


struct QDictSlot
{
    // 四个标量凑满 8B 头（顺序不能动：k 与 v 都要 8 字节对齐，标量必须
    // 排在前面才能把两段 padding 吃掉 —— 这 8B 就是 40B 与 32B 的全部差别）。
    uint64_t h; // 0=空 1=墓碑 >=2 有效
    uint32_t klen; // 真实键长
    uint32_t inl;  // 1=键内联在 k.inl；0=外部借用 k.ext
    union
    {
        char *ext;   // inl==0：借用键缓冲（见文件头不变式）
        char inl[8]; // inl==1：键内容内联（随槽进同一条 cache line）
    } k;
    V16 v;
};

struct QDict
{
    int32_t size;
    int32_t cap; // 2 的幂
    int32_t mask;
    int32_t grow_at; // 【PROMAX-D1】size 到达即 grow（= cap*5/8-1；替代每次插入的乘法比较，逐位等价）
    QDictSlot *slots;
};

// 构造函数不再 memset 整个 V16：pad 字节无任何读取者（值语义只看 t 与 u），
// memset 16B 在热路径装箱（每轮 push/累加各一次）上是可测量开销。
static V16 qotrt_null_v()
{
    V16 v;
    v.t = VT_NULL;
    v.u.p = nullptr;
    return v;
}
static V16 qotrt_none_v()
{
    V16 v;
    v.t = VT_NONE;
    v.u.p = nullptr;
    return v;
}
static V16 qotrt_num(double d)
{
    V16 v;
    v.t = VT_NUM;
    v.u.d = d;
    return v;
}
static V16 qotrt_bool(bool b)
{
    V16 v;
    v.t = VT_BOOL;
    v.u.d = b ? 1.0 : 0.0;
    return v;
}

// ---------------------------------------------------------------- 分配器（M-PERF）
// size-class 空闲表 + bump 段。只解决「分配本身贵」（裸 malloc ~100ns/次），
// 不引入 GC/RC —— 那是 B3b 长跑泄漏判定定案后的决策（§6.3.1：禁止先写 GC 再测）。
//   * 类尺寸（≤6144B）：空闲表 O(1) 复用，段内 bump 进货（256KB 段）；
//   * 大块：直通 malloc/free（Windows 对大块走 VirtualAlloc，free 直还 OS）——
//     数组/字典扩容的旧块因此能真实还给系统，压低扩容期提交峰值；
//   * 单线程假设：qotrt 运行期是单线程执行模型（与现状一致），不加锁。
// QOTNative：hex 查找表提升到文件作用域（原为函数内 static，宏翻转安全化）
static const char *qotrt_hexd_lower = "0123456789abcdef";
static const char *qotrt_hexd_upper = "0123456789ABCDEF";

static const int QSC_N = 18;
static const size_t QSC_SIZE[QSC_N] = {
    16, 24, 32, 48, 64, 96, 128, 192, 256, 384, 512, 768, 1024, 1536, 2048, 3072, 4096, 6144
};

struct QotrtPool
{
    void* free_list[QSC_N];
    char* slab;
    size_t slab_left;
};

#ifdef QOT_RT_SPLIT
extern QotrtPool qotrt_pool;
#else
static QotrtPool qotrt_pool = {{0}, nullptr, 0};
#endif

// 循环 arena 回收栈（见下方 qotrt_arena_push/pop 注释；前置声明供
// qotrt_xfree 查 arena 深度 —— arena 区间内释放直接丢弃，防双重分配）。
struct QotrtArenaMark
{
    char *slab;
    size_t left;
    char *id; // push 时的 slab 身份（pop 侧判断是否发生过 slab 进货，仅 debug 扫描用）
};

#ifdef QOT_RT_SPLIT
extern QotrtArenaMark qotrt_arena_stack[8];
#else
static QotrtArenaMark qotrt_arena_stack[8];
#endif
#ifdef QOT_RT_SPLIT
extern int qotrt_arena_sp;
#else
static int qotrt_arena_sp = 0;
#endif

// ---- 1 字符串驻留表----

struct QotrtCharIntern
{
    QStr q;       // len=1 cap=1 shared=1 hash 惰性计算
    char buf[2];  // {c, '\0'}；q.s 指向它
};
static QotrtCharIntern qotrt_char_intern[256];
struct QotrtCharInternInit
{
    QotrtCharInternInit()
    {
        for (int i = 0; i < 256; i++)
        {
            QStr *q = &qotrt_char_intern[i].q;
            q->len = 1;
            q->cap = 1;
            q->hash = 0;
            q->shared = 1;
            qotrt_char_intern[i].buf[0] = static_cast<char>(i);
            qotrt_char_intern[i].buf[1] = 0;
            q->s = qotrt_char_intern[i].buf;
        }
    }
};
static QotrtCharInternInit qotrt_char_intern_init;


struct QotrtScLut
{
    // 大小 = QSC_SIZE[QSC_N-1] + 1 = 6145。字面量直写：MSVC 下 const 数组的
    // 下标访问（QSC_SIZE[17]）不是整型常量表达式，不能做数组边界。
    uint8_t v[6145]; // [0..6144] → 类索引 0..17
    QotrtScLut()
    {
        int ci = 0;
        for (size_t n = 0; n <= QSC_SIZE[QSC_N - 1]; n++)
        {
            while (ci < QSC_N - 1 && n > QSC_SIZE[ci])
                ci++;
            v[n] = static_cast<uint8_t>(ci);
        }
    }
};
static QotrtScLut qotrt_sc_lut;

static int qotrt_sc_index(size_t n)
{
    if (n <= QSC_SIZE[QSC_N - 1])
        return qotrt_sc_lut.v[n];
    return -1;
}

#ifdef QOT_RT_POOL_DEBUG
struct QrtDbgEnt
{
    void *p;
    size_t n;
    void *site;   // xalloc 调用点返回地址
    void *caller; // xalloc 调用者的调用点（RA1）
};
static QrtDbgEnt qotrt_dbg_tab[1 << 16];
static const void *QRT_DBG_TOMB = (const void *)(size_t)1; // 墓碑（防探针断链）
static size_t qotrt_dbg_idx(void *p)
{
    return (((size_t)p >> 4) * 0x9E3779B97F4A7C15ULL >> 48) & 0xFFFF;
}
static void qotrt_dbg_add(void *p, size_t n, void *site, void *caller)
{
    // 同块双发检测：登记时先探针同名块（存活块不该再登记）。
    // 墓碑不参与「已占用」判定（可复用为插入位），探针必须越过墓碑。
    size_t i = qotrt_dbg_idx(p);
    size_t hole = (size_t)-1;
    for (size_t probe = 0; probe < 65536; probe++)
    {
        void *q = qotrt_dbg_tab[i].p;
        if (q == p)
        {
            std::fprintf(stderr,
                         "[POOL-DEBUG] 同块双发 %p：在册 alloc=%zu site=%p caller=%p；"
                         "本次 alloc=%zu site=%p caller=%p\n",
                         p, qotrt_dbg_tab[i].n, qotrt_dbg_tab[i].site,
                         qotrt_dbg_tab[i].caller, n, site, caller);
            // 邻块反查：双发块的 [0,8) 被写成指针 → 全表区间扫描，指认该指针
            // 落在哪个登记块内部（内部指针也算——q->s/q->a 类字段越界）
            void *val = *(void **)p;
            for (size_t t = 0; t < 65536; t++)
            {
                void *q = qotrt_dbg_tab[t].p;
                if (!q || q == QRT_DBG_TOMB || q == p)
                    continue;
                char *lo = static_cast<char *>(q);
                char *hi = lo + qotrt_dbg_tab[t].n;
                if (val >= (void *)lo && val < (void *)hi)
                {
                    size_t off = (char *)val - lo;
                    std::fprintf(stderr,
                                 "[POOL-DEBUG] 被写值 %p 落在登记块 %p(+%zu) alloc=%zu 内"
                                 " site=%p caller=%p → 该块的指针字段越界写入双发块头\n",
                                 val, q, off, qotrt_dbg_tab[t].n,
                                 qotrt_dbg_tab[t].site, qotrt_dbg_tab[t].caller);
                }
            }
            std::abort();
        }
        if (q == nullptr)
            break;
        if (q == QRT_DBG_TOMB && hole == (size_t)-1)
            hole = i;
        i = (i + 1) & 0xFFFF;
    }
    if (hole != (size_t)-1)
        i = hole;
    else
    {
        i = qotrt_dbg_idx(p);
        while (qotrt_dbg_tab[i].p)
            i = (i + 1) & 0xFFFF;
    }
    qotrt_dbg_tab[i].p = p;
    qotrt_dbg_tab[i].n = n;
    qotrt_dbg_tab[i].site = site;
    qotrt_dbg_tab[i].caller = caller;
}
static size_t qotrt_dbg_take(void *p, void **site, void **caller)
{
    size_t i = qotrt_dbg_idx(p);
    for (size_t probe = 0; probe < 65536; probe++)
    {
        void *q = qotrt_dbg_tab[i].p;
        if (q == nullptr)
            break; // 真空 = 不在表
        if (q == p)
        {
            size_t n = qotrt_dbg_tab[i].n;
            if (site)
                *site = qotrt_dbg_tab[i].site;
            if (caller)
                *caller = qotrt_dbg_tab[i].caller;
            qotrt_dbg_tab[i].p = const_cast<void *>(QRT_DBG_TOMB); // 墓碑
            qotrt_dbg_tab[i].n = 0;
            return n;
        }
        i = (i + 1) & 0xFFFF;
    }
    return (size_t)-1;
}
#endif

static void* qotrt_xalloc(size_t n)
{
    if (n == 0)
        n = 1;
#ifdef QOT_RT_POOL_DEBUG
    {
        // 池 + 毒化：走真实池路径（slab/空闲表），但 free 时毒化块体、
        // alloc 时校验毒化仍在 —— 「释放后经陈旧指针写」在 pop 时刻现形。
        int sc_d = qotrt_sc_index(n);
        if (sc_d >= 0)
        {
            void *p = qotrt_pool.free_list[sc_d];
            if (p != nullptr)
            {
                unsigned char *ub = static_cast<unsigned char *>(p);
                for (size_t k = 8; k < QSC_SIZE[sc_d]; k++)
                {
                    if (ub[k] != 0xBB)
                    {
                        std::fprintf(stderr,
                                     "[POOL-DEBUG] pop(%zu, class %d) 毒化被写（偏移 %zu 值 0x%02x）"
                                     "—— 释放后有人经陈旧指针写入本块\n",
                                     n, sc_d, k, ub[k]);
                        std::abort();
                    }
                }
                qotrt_pool.free_list[sc_d] = *static_cast<void **>(p);
                for (size_t k = 8; k < QSC_SIZE[sc_d]; k++)
                    ub[k] = 0xBB; // 重新毒化（本块现在"活"，下次 free 再毒化）
                qotrt_dbg_add(p, n, __builtin_return_address(0), __builtin_return_address(1));
                return p;
            }
        }
        // 【P0-POOL-FIX】bump 尺寸与类表统一：块物理尺寸恒 = QSC_SIZE[sc]。
        // 旧实现 (n+15)&~15 与类表错配（实测 n∈(64,80] 的块物理 80B 却进
        // 96 类链；pop 后按 96B 使用 → 尾部越界写——QrtProc 的 rd_out/rd_err
        // 恰在偏移 80/88，句柄值踩进邻块头 → 池 free_list 链损坏 → 循环
        // spawn 场景段错误。同型危险区间：128/192/384/768/1536/3072/6144
        // 类的「前一档 16 对齐区」。sc<0 的大块维持 16 对齐语义（malloc
        // 直通路径，与 xfree 的 std::free 对偶，不受类表管辖）。
        size_t need = (sc_d >= 0) ? QSC_SIZE[sc_d]
                                  : ((n + 15u) & ~(size_t)15u);
        void *p;
        if (need > QSC_SIZE[QSC_N - 1])
        {
            p = std::malloc(need);
            qotrt_dbg_add(p, n, __builtin_return_address(0), __builtin_return_address(1));
            return p;
        }
        if (qotrt_pool.slab_left < need)
        {
            size_t sz = 256 * 1024;
            qotrt_pool.slab = static_cast<char *>(std::malloc(sz));
            qotrt_pool.slab_left = sz;
        }
        p = qotrt_pool.slab;
        qotrt_pool.slab += need;
        qotrt_pool.slab_left -= need;
        qotrt_dbg_add(p, n, __builtin_return_address(0), __builtin_return_address(1));
        return p;
    }
#endif
    int sc = qotrt_sc_index(n);
    if (sc >= 0)
    {
        void* p = qotrt_pool.free_list[sc];
        if (p != nullptr)
        {
            qotrt_pool.free_list[sc] = *static_cast<void **>(p);
            return p;
        }
    }
    // 【P0-POOL-FIX】bump 尺寸 = 类尺寸（与 xfree 推链/毒化的类表完全
    // 一致）。旧 (n+15)&~15 与类表错配：块物理小于类尺寸，pop 复用后
    // 尾部越界写（crash_repro：n=70 物理 80B 进 96 类，QrtProc 写偏移
    // 80/88 踩邻块 → free_list 链野 → 循环 spawn 段错误，-O2 放大显形）。
    size_t need = (sc >= 0) ? QSC_SIZE[sc] : ((n + 15u) & ~(size_t)15u);
    // 阈值必须与 qotrt_xfree 的类表一致（QSC_SIZE 上界 6144）：类尺寸走
    // slab 切块（xfree 进空闲表复用），超界走 malloc（xfree 对应 std::free）。
    // b6b 崩溃复盘（0xC0000374 堆损坏）：旧阈值 8192 让 6145~8192 的块从
    // slab 切出、却被 xfree 判成大块 free() 掉 —— free 到了 slab 内部。
    if (need > QSC_SIZE[QSC_N - 1])
        return std::malloc(need); // 大块直通（qotrt_xfree 里对应 std::free）
    if (qotrt_pool.slab_left < need)
    {
        size_t sz = 256 * 1024;
        qotrt_pool.slab = static_cast<char *>(std::malloc(sz));
        qotrt_pool.slab_left = sz;
    }
    void* p = qotrt_pool.slab;
    qotrt_pool.slab += need;
    qotrt_pool.slab_left -= need;
    return p;
}
static void qotrt_xfree(void* p, size_t n)
{
    if (p == nullptr)
        return;
#ifdef QOT_RT_POOL_DEBUG
    {
        void *site = nullptr;
        void *caller = nullptr;
        size_t own = qotrt_dbg_take(p, &site, &caller);
        if (own == (size_t)-1)
        {
            std::fprintf(stderr, "[POOL-DEBUG] xfree(%p, %zu) 未登记（野指针/重复释放）\n", p, n);
            std::abort();
        }
        if (own != n)
        {
            std::fprintf(stderr,
                         "[POOL-DEBUG] xfree(%p) 尺寸错配 alloc=%zu free=%zu alloc_site=%p alloc_caller=%p free_caller=%p\n",
                         p, own, n, site, caller, __builtin_return_address(1));
            std::abort();
        }
        int sc_d = qotrt_sc_index(n);
        if (sc_d < 0)
        {
            std::free(p);
            return;
        }
        if (qotrt_arena_sp > 0)
            return; // arena 区间内丢弃（与正式口径一致）
        // 双推检测：推栈前全链查重
        for (int c2 = 0; c2 < QSC_N; c2++)
        {
            int depth = 0;
            for (void *q = qotrt_pool.free_list[c2]; q && depth < 200000;
                 q = *static_cast<void **>(q))
            {
                if (q == p)
                {
                    std::fprintf(stderr,
                                 "[POOL-DEBUG] xfree(%p, %zu) 双推：已在 class %d 链上\n",
                                 p, n, c2);
                    std::abort();
                }
                depth++;
            }
        }
        unsigned char *ub = static_cast<unsigned char *>(p);
        for (size_t k = 8; k < QSC_SIZE[sc_d]; k++)
            ub[k] = 0xBB; // 毒化（[0,8) 留给 next 指针）
        *static_cast<void **>(p) = qotrt_pool.free_list[sc_d];
        qotrt_pool.free_list[sc_d] = p;
        return;
    }
#endif
    int sc = qotrt_sc_index(n);
    if (sc >= 0)
    {
        // arena 区间内禁止进 free_list：轮内释放的块可能在 bump 回滚区里，
        // 进表后 free_list 与 bump 线会指向同一地址 → 双重分配（数据损坏）。
        // 丢弃即可 —— pop 会把整段 bump 区一并回收；mark 之前的块被丢弃
        // 只是泄漏（保守，正确性无损）。
        if (qotrt_arena_sp > 0)
            return;
        *static_cast<void **>(p) = qotrt_pool.free_list[sc];
        qotrt_pool.free_list[sc] = p;
        return;
    }
    std::free(p); // 大块直还 OS（不归 slab bump 管，与 arena 回滚无关）
}


struct QEnv
{
    int32_t n;
    QEnv *parent;   // 词法父帧的激活环境（链上溯由 cgen 静态定层）
    V16 a[1];       // 柔性尾部（实际 n 项）
};

static QEnv *qotrt_env_new(int32_t n, QEnv *parent)
{
    QEnv *e = static_cast<QEnv *>(
        qotrt_xalloc(sizeof(QEnv) + (n > 0 ? (size_t)(n - 1) : 0) * sizeof(V16)));
    e->n = n;
    e->parent = parent;
    for (int32_t i = 0; i < n; i++)
    {
        e->a[i].t = VT_NONE;
        e->a[i].u.p = nullptr;
    }
    return e;
}


// QOTNative：函数内 static 提升为文件作用域（qotrt_lib.cpp 的
// #define static extern "C" 宏翻转要求 static 只出现在文件作用域；
// 单线程语义不变 —— 缓存/单例用途与函数内 static 等价）。
static QEnv *qotrt_env_link0_cp = nullptr; // 上一父环境
static QEnv *qotrt_env_link0_cl = nullptr; // 其链节
static QEnv *qotrt_env_link0(QEnv *parent)
{
    if (qotrt_env_link0_cl != nullptr && qotrt_env_link0_cp == parent)
        return qotrt_env_link0_cl;
    QEnv *e = qotrt_env_new(0, parent);
    qotrt_env_link0_cp = parent;
    qotrt_env_link0_cl = e;
    return e;
}

// 读/写捕获槽的唯一入口：返回槽指针（cgen 以 (*qotrt_env_at(env,i)) 形式
// 内联到读写表达式里；env 空或越界返回兜底槽而非 UB —— 上游编译器保证
// 不发生，这里防御的是手写/反序列化产物）。
static V16 qotrt_env_at_none_cell; // QOTNative：同上提升（VT_NONE 兜底槽单例）
static V16 *qotrt_env_at(QEnv *e, int32_t i)
{
    if (e == nullptr)
    {
        qotrt_env_at_none_cell.t = VT_NONE;
        qotrt_env_at_none_cell.u.p = nullptr;
        return &qotrt_env_at_none_cell;
    }
    if (i < 0)
        i = 0;
    if (i >= e->n)
        i = e->n - 1;
    return &e->a[i];
}

// 上溯 k 层词法父环境（cgen 的 level≥1 upval 用；k 超链返回最外层）
static QEnv *qotrt_env_up(QEnv *e, int32_t k)
{
    while (k-- > 0 && e != nullptr)
        e = e->parent;
    return e;
}

// 【PROMAX-U1】upval 寻址的内联快路径（发射点用）。判据与 env_up/env_at
// 逐字一致；热判据（链非空、常量合法下标）不调函数，慢形态回落原函数。
// cgen 的每次 upval 读写从「env_up 调用 + env_at 调用」缩为一条寻址，
// 且 gcc 内联后同级链自动 CSE（b8a 的 step 体内 4 次寻址共享一次 parent walk）。
static inline QEnv *qotrt_env_up2(QEnv *e, int32_t k)
{
    while (k-- > 0 && e != nullptr)
        e = e->parent;
    return e;
}
static inline V16 *qotrt_env_at2(QEnv *e, int32_t i)
{
    if (e != nullptr && i >= 0 && i < e->n)
        return &e->a[i];
    return qotrt_env_at(e, i); // 慢形态（null/越界钳制）语义单一来源
}

// env 值槽（VT_ENV）→ 裸指针；未分配（VT_NONE 等）→ nullptr
static QEnv *qotrt_env_of(V16 v)
{
    return (v.t == VT_ENV) ? static_cast<QEnv *>(v.u.p) : nullptr;
}

static V16 qotrt_env_v(QEnv *e)
{
    V16 v;
    v.t = VT_ENV;
    v.u.p = e;
    return v;
}

// ---- 闭包值：函数入口表下标 + 环境 ----
struct QClosure
{
    int32_t fn;   // cgen 生成的 qot_clo_fns[] 下标
    QEnv *env;
};

typedef void (*QotCloFn)(V16 *ret, V16 *args, int argc, int depth, QEnv *env);

static V16 qotrt_closure_new(int32_t fn, QEnv *env)
{
    QClosure *c = static_cast<QClosure *>(qotrt_xalloc(sizeof(QClosure)));
    c->fn = fn;
    c->env = env;
    V16 v;
    v.t = VT_CLOSURE;
    v.u.p = c;
    return v;
}

// ---- 错误对象元语（F2 第一步：err()/is_err()/err_val()）----
struct QErr
{
    V16 payload;
};

static V16 qotrt_err_make(V16 payload)
{
    QErr *e = static_cast<QErr *>(qotrt_xalloc(sizeof(QErr)));
    e->payload = payload;
    V16 v;
    v.t = VT_ERR;
    v.u.p = e;
    return v;
}

static bool qotrt_is_err(V16 v)
{
    return v.t == VT_ERR;
}

static V16 qotrt_err_payload(V16 v)
{
    if (v.t != VT_ERR)
        return qotrt_null_v();
    return static_cast<QErr *>(v.u.p)->payload;
}

// ---- 最小对象模型：QObj + 类型表 ----
struct QObj
{
    int32_t tid;   // qotrt_types 下标
    V16 a[1];      // 柔性尾部：实际 nfields 项（声明序偏移）
};

struct QTypeDesc
{
    const char *name;      // 类名（tostring 用：<Name>）
    int32_t nfields;       // 字段数（= 声明序偏移上界）
    const int32_t *foff;   // [全局 fid] → 字段偏移（-1 = 无）
    const QotCloFn *mfns;  // [全局 mid] → 方法函数指针（0 = 无）
};

// cgen 主 TU 单点定义（无类的程序发射空表 + 0，FIELD op 恒得 null ——
// 无类程序不会发射 FIELD op，这是防御性兜底而非可达路径）。与
// qot_clo_fns/qotrt_clo_count 同一模式：头内 extern 声明、cgen 非 static
// 定义（C++ 链接，两侧同 mangling）。
extern QTypeDesc qotrt_types[];
extern int qotrt_ntypes;

static inline V16 qotrt_obj_v(QObj *o)
{
    V16 v;
    v.t = VT_OBJ;
    v.u.p = o;
    return v;
}

static V16 qotrt_obj_new(V16 *args, int argc)
{
    if (argc < 1 || args[0].t != VT_NUM)
        return qotrt_null_v();
    int tid = (int)args[0].u.d;
    if (tid < 0 || tid >= qotrt_ntypes)
        return qotrt_null_v();
    const QTypeDesc *td = &qotrt_types[tid];
    int n = td->nfields;
    QObj *o = static_cast<QObj *>(
        qotrt_xalloc(sizeof(QObj) + (n > 0 ? (size_t)(n - 1) * sizeof(V16) : 0)));
    o->tid = (int32_t)tid;
    for (int k = 0; k < n; ++k)
        o->a[k] = qotrt_null_v();
    return qotrt_obj_v(o);
}

static inline int32_t qotrt_obj_field_off(const QObj *o, int fid)
{
    return qotrt_types[o->tid].foff[fid];
}

static V16 qotrt_obj_get(V16 obj, int fid)
{
    if (obj.t != VT_OBJ)
        return qotrt_null_v();
    const QObj *o = static_cast<const QObj *>(obj.u.p);
    int32_t off = qotrt_obj_field_off(o, fid);
    if (off < 0)
        return qotrt_null_v();
    return o->a[off];
}

static void qotrt_obj_set(V16 obj, int fid, V16 value)
{
    if (obj.t != VT_OBJ)
        return;
    QObj *o = static_cast<QObj *>(obj.u.p);
    int32_t off = qotrt_obj_field_off(o, fid);
    if (off < 0)
        return;
    o->a[off] = value;
}

// ---- 异常着陆栈（try/catch 的运行时半边；另一半在 cgen 的 setjmp 发射）----
// 契约：
//   * 正常路径零开销 —— 无 try 的程序不触碰这里的任何全局；
//   * throw 经最近的着陆点 longjmp；被抛值放 qotrt_thrown（跨 longjmp 存活）；
//   * 未捕获 = 确定性致命：stderr [2001] + exit 124（与深度/超时同档）；
//   * 深度/超时上限不可被 catch（它们是资源契约，不是用户错误）；
//   * throw 放弃的堆对象按 arena-free 定价由池持有（README 21.4 记账），
//     不做回收 —— 修复它的 scope-arena 登记（路线图 F3 坑点）等 GC 决策一起动。

// 【PROMAX-E1】自制 x64 局部跳转原语（qot_sjmp/qot_ljmp）。
// 为什么：MinGW-w64 的 longjmp 走 SEH/展开路径，本机实测 ~1500ns/次 ——
// b5b 的 throw/catch 单价 1628ns 里 94% 是它。原语只保存/恢复 RIP/RSP 与
// callee-saved（RBP/RBX/R12-R15），单次 ~15ns：
//   * qot_sjmp 挂 __attribute__((returns_twice))：编译器把它当 setjmp
//     对待 —— 返回后强制重载寄存器缓存值，跨恢复的正确性与正规
//     setjmp/longjmp 完全同律（这正是 returns_twice 属性存在的意义）；
//   * 恢复集合 = x64 调用约定的 callee-saved 集合 + 栈 + 返回地址：
//     qot_ljmp 之后执行流精确回到「qot_sjmp 调用点之后」且寄存器态与
//     「刚从 qot_sjmp 正常返回」一致（除返回值为 1 而非 0）；
//   * 与正规 longjmp 的差别仅在于不做 SEH 展开登记 —— 本运行时的着陆点
//     全部是 POD（无 C++ 析构语义可跳过），展开是纯开销；
//   * MSVC / 非 x64 / QOT_NO_FAST_SJ=1：回落正规 setjmp/longjmp。
#if defined(__GNUC__) && defined(__x86_64__) && !defined(QOT_NO_FAST_SJ)
#define QOT_FAST_SJ 1
struct QrtJmpBuf
{
    void *rip, *rsp, *rbp, *rbx, *r12, *r13, *r14, *r15;
};
// 【R0-O0】optimize("O2")：dev 档全 TU 走 -O0 时，这两函数仍按 O2 编——
// -O0 强制帧指针（RBP 为帧基），与 asm 内改写 RSP/RBP 的恢复语义冲突
// （实测 "bp cannot be used in 'asm' here"）；函数级钉死优化级后 -O0 可用。
static __attribute__((returns_twice, noinline, optimize("O2"))) int qot_sjmp(QrtJmpBuf *j)
{
    // SysV 与 Win64 的差别只在参数寄存器；%0 由编译器分配（任何 GP 寄存器皆可）。
    __asm__ __volatile__(
        "movq (%%rsp), %%rax\n\t"   // 返回地址（qot_sjmp 的 ret 目标）
        "movq %%rax, 0(%0)\n\t"
        "leaq 8(%%rsp), %%rax\n\t"  // 调用者 ret 之后的 rsp 快照
        "movq %%rax, 8(%0)\n\t"
        "movq %%rbp, 16(%0)\n\t"
        "movq %%rbx, 24(%0)\n\t"
        "movq %%r12, 32(%0)\n\t"
        "movq %%r13, 40(%0)\n\t"
        "movq %%r14, 48(%0)\n\t"
        "movq %%r15, 56(%0)\n\t"
        :
        : "r"(j)
        : "rax", "memory");
    return 0; // 直接路径返回 0；恢复路径由 qot_ljmp 代返回 1
}
static __attribute__((noreturn, noinline, optimize("O2"))) void qot_ljmp(QrtJmpBuf *j, int v)
{
    __asm__ __volatile__(
        "movq 8(%0), %%rsp\n\t"  // 恢复调用者栈
        "movq 0(%0), %%rax\n\t"  // rip（qot_sjmp 的返回地址）
        "pushq %%rax\n\t"        // 作为 ret 目标压栈（写在新 rsp-8，即原 ret 槽）
        "movq %1, %%rax\n\t"     // 返回值 = v（≠0）
        "movq 16(%0), %%rbp\n\t"
        "movq 24(%0), %%rbx\n\t"
        "movq 32(%0), %%r12\n\t"
        "movq 40(%0), %%r13\n\t"
        "movq 48(%0), %%r14\n\t"
        "movq 56(%0), %%r15\n\t"
        "ret\n\t"
        :
        : "r"(j), "r"((int64_t)v)
        : "rax", "rbp", "rbx", "r12", "r13", "r14", "r15", "rsp", "memory");
    __builtin_unreachable();
}
struct QotrtLanding
{
    QrtJmpBuf jb;
    QotrtLanding *prev;
};
#define QOT_SJ_SET(l) qot_sjmp(&(l)->jb)
#define QOT_SJ_JMP(l) qot_ljmp(&(l)->jb, 1)
#else
struct QotrtLanding
{
    jmp_buf jb;
    QotrtLanding *prev;
};
#define QOT_SJ_SET(l) setjmp((l)->jb)
#define QOT_SJ_JMP(l) longjmp((l)->jb, 1)
#endif

#ifdef QOT_RT_SPLIT
extern QotrtLanding *qotrt_landings;
#else
static QotrtLanding *qotrt_landings = nullptr;
#endif
#ifdef QOT_RT_SPLIT
extern V16 qotrt_thrown;
#else
static V16 qotrt_thrown;
#endif

static void qotrt_fail_not_callable(uint8_t t)
{
    std::fprintf(stderr,
                 "[错误] [2013] 该值不可调用（类型 %u）：只有函数/闭包可被调用\n",
                 (unsigned)t);
    std::exit(124);
}

// qotrt_throw 定义在 qotrt_to_str_value 之后（未捕获文案要字符串化被抛值）


// ---- 循环 arena 回收（M-PERF 收尾二，b3a 形态）----
// lir_opt.arena_loops 对「逃逸判定通过」的循环在头/回边插桩本对函数：
// push 快照 bump 指针，pop 回滚 —— 本轮切出的 slab 块被下一轮原地覆盖，
// 分配回归 O(1) 且无进货（RSS 平坦）。安全性由两级保证：
//   * 编译期：逃逸否决 —— 循环内分配的槽若在出口后被使用（或流向跨轮
//     容器/未内联调用），整个循环不插桩（pass 保守判定；P0-POOL-1 后
//     含容器内容逃逸否决与幻影循环否决，见 lir_opt.arena_loops 契约）；
//   * 运行期：arena 区间内 xfree 丢弃不进 free_list（见 xfree 注释），
//     free_list 借块地址与 bump 无关、pop 后依然有效；跨 slab 进货的新
//     slab 被 pop 遗忘 —— 与 arena-free 账面一致。
// 嵌套循环用栈配对（深度 8；pass 只对无函数调用边界的循环插桩，实际深度 1~2）。
static void qotrt_arena_push(void)
{
    if (qotrt_arena_sp < 8)
    {
        qotrt_arena_stack[qotrt_arena_sp].slab = qotrt_pool.slab;
        qotrt_arena_stack[qotrt_arena_sp].left = qotrt_pool.slab_left;
        qotrt_arena_stack[qotrt_arena_sp].id = qotrt_pool.slab; // slab 身份（pop 侧防跨 slab 扫描）
    }
    qotrt_arena_sp++; // 溢出时 pop 侧对称少退，保持配对
}

static void qotrt_arena_pop(void)
{
    if (qotrt_arena_sp > 0)
    {
        qotrt_arena_sp--;
        if (qotrt_arena_sp < 8)
        {
            char *mark_slab = qotrt_arena_stack[qotrt_arena_sp].slab;
            size_t mark_left = qotrt_arena_stack[qotrt_arena_sp].left;
            char *mark_id = qotrt_arena_stack[qotrt_arena_sp].id;
            char *old_slab = qotrt_pool.slab;
#ifdef QOT_RT_POOL_DEBUG
            // 取证工装与 arena 语义对齐（P0-POOL-1）：回滚区 [mark, old)
            // 在 pop 后回到"自由"状态 —— 注册表对应条目墓碑化（同块双发
            // 探针对合法 bump 复用不再误报），块体毒化（逃逸指针的陈旧读
            // 立即显形为 0xBB 垃圾，而不是静默吞掉）。slab 进货发生过
            // （mark 身份 ≠ 当前 slab）时区间跨两个 malloc 块，跳过扫描
            // —— 旧 slab 的地址不再会被 bump 复用，无此需求。
            if (mark_id == old_slab && old_slab > mark_slab)
            {
                for (size_t t = 0; t < (1u << 16); t++)
                {
                    void *q = qotrt_dbg_tab[t].p;
                    if (!q || q == QRT_DBG_TOMB)
                        continue;
                    if (static_cast<char *>(q) >= mark_slab &&
                        static_cast<char *>(q) < old_slab)
                    {
                        qotrt_dbg_tab[t].p = const_cast<void *>(QRT_DBG_TOMB);
                        qotrt_dbg_tab[t].n = 0;
                    }
                }
                for (char *z = mark_slab; z < old_slab; z++)
                    *z = static_cast<char>(0xBB);
            }
#endif
            qotrt_pool.slab = mark_slab;
            qotrt_pool.slab_left = mark_left;
            // 注：1 字符驻留表是静态对象（见 qotrt_char_intern 注释），
            // 与 arena 回滚无关 —— 这里不需要任何清障动作（旧方案要扫
            // 256 项指针表，本轮已整段删除）。
        }
    }
}

static char *qotrt_strdup(const char *s, int32_t len)
{
    char *p = static_cast<char *>(qotrt_xalloc(static_cast<size_t>(len) + 1));
    std::memcpy(p, s, static_cast<size_t>(len));
    p[len] = 0;
    return p;
}

static QStr *qotrt_str_new(const char *s, int32_t len)
{
    // 小对象合并分配（M-PERF 收尾）：QStr 头 + 缓冲合成单块 xalloc（原两次
    // 分配，微测 QStr 构造 ~44ns）。q->s 指向块内偏移；追加增长仍是
    // 「新缓冲 + 迁移 + 重指」（qotrt_str_ipappend），块内缓冲按不变式永不
    // 释放/不再写入；qotrt 无任何 QStr 释放路径（arena-free），单块不存在
    // 「头活缓冲死」的悬挂面。strings(num) 验收：微测 ≤70ns。
    size_t total = sizeof(QStr) + static_cast<size_t>(len) + 1;
    QStr *q = static_cast<QStr *>(qotrt_xalloc(total));
    q->len = len;
    q->cap = len;
    q->hash = 0;
    q->shared = 0;
    char *p = reinterpret_cast<char *>(q + 1);
    if (len > 0)
        std::memcpy(p, s, static_cast<size_t>(len));
    p[len] = 0;
    q->s = p;
    return q;
}

// 1 字符串驻留：len==1 直接返回静态驻留对象（零分配，见 qotrt_char_intern
// 注释）。b1d（1e7 次取字符）从「每次 1~2 次堆分配」降到「一次数组取址」。
// 语义前提与旧缓存一致：字符串不可变 + shared=1 挡住原地追加 → 多槽共享
// 同一个驻留对象在语言层不可观测（四方差分与 --suite 守卫）。

static V16 qotrt_str(const char *s, int32_t len)
{
    QStr *q;
    if (len == 1)
    {
        q = &qotrt_char_intern[static_cast<unsigned char>(s[0])].q;
    }
    else
    {
        q = qotrt_str_new(s, len);
    }
    V16 v;
    v.t = VT_STR;
    v.u.p = q;
    return v;
}

// 常量池/借用视图：shared=1，hash 可外部预填（dict_keys 零拷贝键用槽内哈希）
static V16 qotrt_str_shared(const char *s, int32_t len, uint64_t hash_hint)
{
    QStr *q = qotrt_str_new(s, len);
    q->shared = 1;
    q->hash = hash_hint;
    V16 v;
    v.t = VT_STR;
    v.u.p = q;
    return v;
}

static V16 qotrt_arr_new(int32_t cap_hint)
{
    QArr *a = static_cast<QArr *>(qotrt_xalloc(sizeof(QArr)));
    int32_t cap = cap_hint > 4 ? cap_hint : 4;
    a->a = static_cast<V16 *>(qotrt_xalloc(sizeof(V16) * static_cast<size_t>(cap)));
    a->len = 0;
    a->cap = cap;
    V16 v;
    v.t = VT_ARR;
    v.u.p = a;
    return v;
}

// QArr 的元素缓冲是独占所有权（qotrt 之外无人持有其地址 —— cgen 全部经
// (QArr*)a->a 访问），扩容旧块可安全入池复用。
static void qotrt_arr_push(QArr *a, V16 v)
{
    if (a->len >= a->cap)
    {
        int32_t nc = a->cap * 2;
        V16 *na = static_cast<V16 *>(qotrt_xalloc(sizeof(V16) * static_cast<size_t>(nc)));
        std::memcpy(na, a->a, sizeof(V16) * static_cast<size_t>(a->len));
        qotrt_xfree(a->a, sizeof(V16) * static_cast<size_t>(a->cap)); // 旧块还池/还 OS
        a->a = na;
        a->cap = nc;
    }
    a->a[a->len++] = v;
}

// ---------------------------------------------------------------- 字符串哈希/字典
static uint64_t qotrt_hash(const char *s, size_t n)
{
    // 与 qot_flatdict.h 的 qot_hash_string 同算法（分布特性一致）
    uint64_t h = 0x9E3779B97F4A7C15ULL ^ static_cast<uint64_t>(n);
    while (n >= 8)
    {
        uint64_t blk;
        std::memcpy(&blk, s, 8);
        h ^= blk;
        h *= 0xC2B2AE3D27D4EB4FULL;
        h ^= h >> 29;
        s += 8;
        n -= 8;
    }
    if (n > 0)
    {
        uint64_t tail = 0;
        for (size_t i = 0; i < n; i++)
            tail |= static_cast<uint64_t>(static_cast<unsigned char>(s[i])) << (8 * i);
        h ^= tail;
        h *= 0x165667B19E3779F9ULL;
        h ^= h >> 32;
    }
    h *= 0x27D4EB2F165667C5ULL;
    h ^= h >> 32;
    return h ? h : 1;
}

static const uint64_t QD_EMPTY = 0;
static const uint64_t QD_DELETED = 1;

// 键写入：≤8B 内联（拷贝进槽，含于槽的 cache line），>8B 外部借用。
static void qotrt_slot_setkey(QDictSlot *at, const char *k, int32_t klen)
{
    if (klen <= 8)
    {
        at->inl = 1;
        std::memcpy(at->k.inl, k, static_cast<size_t>(klen));
    }
    else
    {
        at->inl = 0;
        at->k.ext = const_cast<char *>(k); // 借用，不复制
    }
    at->klen = static_cast<uint32_t>(klen);
}

// 键比较：at/find 共用（放置与查找同判据，正确性）。先比键长再 memcmp，
// 与既有序列语义一致。**内容比较是最终判据** —— 槽里的 h 只是预筛，32 位
// 标签相同时也必须 memcmp 才算命中（E1 的安全边界，见 QDictSlot 注释）。
static inline bool qotrt_slot_keyeq(const QDictSlot *sl, const char *k, int32_t klen)
{
    if (sl->klen != static_cast<uint32_t>(klen))
        return false;
    return std::memcmp(sl->inl ? sl->k.inl : sl->k.ext, k,
                       static_cast<size_t>(klen)) == 0;
}

static V16 qotrt_dict_new()
{
    QDict *d = static_cast<QDict *>(qotrt_xalloc(sizeof(QDict)));
    d->size = 0;
    d->cap = 8;
    d->mask = 7;
    d->grow_at = 8 * 5 / 8 - 1;
    d->slots = static_cast<QDictSlot *>(qotrt_xalloc(sizeof(QDictSlot) * 8));
    for (int32_t i = 0; i < 8; i++)
        d->slots[i].h = QD_EMPTY;
    V16 v;
    v.t = VT_DICT;
    v.u.p = d;
    return v;
}

// 双哈希步进（M-PERF 收尾）：步长取哈希高位、恒为奇数。cap 是 2 的幂 →
// 奇步长与 cap 互素，探测序列仍保证遍历全表（完整性与线性探测等价），但
// 相邻哈希不再共享同一条探查路径 —— 线性探测的主聚类消失，负载因子 α 下
// 期望探测数回到 O(1/(1-α))，0.625 下 ~2.7 槽 vs 线性 ~8.5 槽。
// at / find / grow 三处必须共用本函数：插入放置与查找走的是**同一条**
// 序列，任何一处单独改步进都会让已插入的键找不到（正确性，非性能）。
static inline int32_t qotrt_probe_step(uint64_t h, int32_t mask)
{
    (void)mask;
    return static_cast<int32_t>((h >> 32) | 1u);
}

static void qotrt_dict_grow(QDict *d, int32_t ncap)
{
    QDictSlot *old = d->slots;
    int32_t ocap = d->cap;
    d->cap = ncap;
    d->mask = ncap - 1;
    d->grow_at = ncap * 5 / 8 - 1;
    d->size = 0;
    d->slots = static_cast<QDictSlot *>(qotrt_xalloc(sizeof(QDictSlot) * static_cast<size_t>(ncap)));
    for (int32_t i = 0; i < ncap; i++)
        d->slots[i].h = QD_EMPTY;
    for (int32_t i = 0; i < ocap; i++)
    {
        if (old[i].h > QD_DELETED)
        {
            uint64_t h = old[i].h;
            int32_t stp = qotrt_probe_step(h, d->mask);
            int32_t j = static_cast<int32_t>(h & static_cast<uint64_t>(d->mask));
            while (d->slots[j].h != QD_EMPTY)
                j = (j + stp) & d->mask;
            d->slots[j] = old[i];
            (void)h;
            d->size++;
        }
    }
    qotrt_xfree(old, sizeof(QDictSlot) * static_cast<size_t>(ocap));
}

// 插入路径的键处理从 strdup 改为【借用】（M-PERF）：
//   * 键缓冲来自 QStr（qotrt_str* 家族）或 qbuf —— 按文件头不变式，
//     这些缓冲永不释放、永不再写入（增长=换新缓冲+重指）；
//   * 借用视角因此永恒有效：被追加的原串会拿到新缓冲，旧缓冲字节不变，
//     字典里已入槽的键保持原值 —— 与 strdup 复制语义逐字节可观察等价；
//   * 每键省一次堆分配 + 全量拷贝（b2b/b7a 的插入路径大头之一）。
static V16 *qotrt_dict_at(QDict *d, const char *k, int32_t klen, uint64_t h)
{
    // 增长触发线 0.75 → 0.625（M-PERF 收尾探测三连之一）：更稀的表把期望
    // 探测数压下来；rehash 提前 ~17% 触发，摊还成本不变（倍增）。
    // 【PROMAX-D1】乘法比较换缓存阈值（语义逐位等价：cap 为 2^n 时 cap*5/8
    // 是整数，原判据 (size+1)*8 >= cap*5 ⇔ size >= cap*5/8 - 1 == grow_at）。
    if (d->size >= d->grow_at)
        qotrt_dict_grow(d, d->cap * 2);
    int32_t mask = d->mask;
    int32_t step = qotrt_probe_step(h, mask);
    bool pf = d->cap > 512; // 大表才预取：小表整体在 L1/L2，预取是纯开销
    int32_t i = static_cast<int32_t>(h & static_cast<uint64_t>(mask));
    uint64_t tag = h;
    // 【E 线 0】首槽预取也必须受 pf 守卫：小表（b2a 的 5 键热表，
    // cap=8）整体常驻 L1，这里的一次 prefetch 是纯指令开销 —— gcc 分支生效
    // 后实测 b2a -16.7%（大表那两笔是 +5~7%）。预取只对「槽数组远超缓存」
    // 的形态成立，守卫口径与循环内那次保持一致。
    if (pf)
        QOTRT_PREFETCH(&d->slots[i]);
    int32_t tomb = -1;
    for (;;)
    {
        QDictSlot *sl = &d->slots[i];
        if (pf)
            QOTRT_PREFETCH(&d->slots[(i + (step << 2)) & mask]); // 序列前方第 4 步
        if (sl->h == QD_EMPTY)
        {
            QDictSlot *at = (tomb >= 0) ? &d->slots[tomb] : sl;
            at->h = tag;
            qotrt_slot_setkey(at, k, klen);
            at->v.t = VT_NULL;
            at->v.u.p = nullptr;
            d->size++;
            return &at->v;
        }
        if (sl->h == QD_DELETED)
        {
            if (tomb < 0)
                tomb = i;
        }
        else if (sl->h == tag && qotrt_slot_keyeq(sl, k, klen))
        {
            return &sl->v;
        }
        i = (i + step) & mask;
    }
}

static V16 *qotrt_dict_find(QDict *d, const char *k, int32_t klen, uint64_t h)
{
    int32_t mask = d->mask;
    int32_t step = qotrt_probe_step(h, mask);
    bool pf = d->cap > 512;
    int32_t i = static_cast<int32_t>(h & static_cast<uint64_t>(mask));
    uint64_t tag = h;
    if (pf) // 同 at()：小表不预取（见 at() 里的说明与实测账单）
        QOTRT_PREFETCH(&d->slots[i]);
    for (;;)
    {
        QDictSlot *sl = &d->slots[i];
        if (pf)
            QOTRT_PREFETCH(&d->slots[(i + (step << 2)) & mask]);
        if (sl->h == QD_EMPTY)
            return nullptr;
        if (sl->h == tag && qotrt_slot_keyeq(sl, k, klen))
            return &sl->v;
        i = (i + step) & mask;
    }
}

static inline uint64_t qotrt_vhash(V16 *v)
{
    QStr *q = static_cast<QStr *>(v->u.p);
    if (q->hash == 0)
        q->hash = qotrt_hash(q->s, static_cast<size_t>(q->len));
    return q->hash; // 哈希缓存：常量键/复用键只算一次（T-33 对齐）
}

// 【PROMAX-D2】find 的首槽特化（发射点内联展开用）。
// 判据与 qotrt_dict_find 逐字一致（同 h → 同 i 起点）：只比较首槽。
//   * 首槽 tag 命中且 keyeq → 值槽指针（负载 0.625 下 ~62% 的查找走这条）；
//   * 否则返回 nullptr —— 调用方回落完整 qotrt_dict_find 重探（首槽重比一次
//     ~2ns，只在 miss 分支付）。
// 与直接给 find 挂 always_inline 的取舍：内联整 find 是 ~40 指令 × 全部调用点
// （icache 与编译体积代价），首槽特化是 ~12 指令且 miss 分支冷。
static inline V16 *qotrt_dict_find1(QDict *d, const char *k, int32_t klen, uint64_t h)
{
    QDictSlot *sl = &d->slots[static_cast<int32_t>(h & static_cast<uint64_t>(d->mask))];
    if (sl->h == h && qotrt_slot_keyeq(sl, k, klen))
        return &sl->v;
    return nullptr;
}

// ---------------------------------------------------------------- 字符串化（逐字节对齐 VM）
// 整数 → 十进制（手工拼位，~10ns；snprintf 同任务 ~150ns）。
// 仅用于「结果可证明与 snprintf 逐字节一致」的两条路径：
//   * concat 的整数分支：原实现就是 %lld，手工输出与它逐字节一致（含 INT64_MIN）；
//   * %f 的整数值特化：|v| < 9e15 < 2^53 时 double 精确表示该整数，UCRT 的 %f
//     输出 = 整数部分逐位 + ".000000"，手工拼位一致。非整数值/超界一律回 snprintf。
static std::size_t qotrt_fmt_i64(char *dst, int64_t v)
{
    char *p = dst;
    uint64_t u = (v < 0) ? (static_cast<uint64_t>(-(v + 1)) + 1u) : static_cast<uint64_t>(v);
    do
    {
        *p++ = static_cast<char>('0' + static_cast<int>(u % 10));
        u /= 10;
    } while (u != 0);
    if (v < 0)
        *p++ = '-';
    std::size_t n = static_cast<std::size_t>(p - dst);
    for (std::size_t i = 0, j = n - 1; i < j; i++, j--)
    {
        char t = dst[i];
        dst[i] = dst[j];
        dst[j] = t;
    }
    return n;
}

// 路径一（Value::toString / output / strings()）：NUMBER 一律 %f（6 位小数）。
// -0.0 不进整数特化（%f 会保负号，特化会丢 —— VM/AOT 输出一致性由套件守卫）。
static std::size_t qotrt_num_to_buf(double num, char *buf, size_t cap)
{
    if (num == static_cast<double>(static_cast<int64_t>(num)) &&
        num > -9.0e15 && num < 9.0e15 &&
        !(num == 0.0 && std::signbit(num)))
    {
        std::size_t k = qotrt_fmt_i64(buf, static_cast<int64_t>(num));
        std::memcpy(buf + k, ".000000", 7);
        buf[k + 7] = 0;
        return k + 7;
    }
    return static_cast<size_t>(std::snprintf(buf, cap, "%f", num));
}

// 路径二（+ 拼接 lambda）：整数值 → int64 十进制；否则 %f
static std::size_t qotrt_num_to_buf_concat(double num, char *buf, size_t cap)
{
    if (num == static_cast<double>(static_cast<int64_t>(num)) &&
        num >= -9.2e18 && num <= 9.2e18)
    {
        return qotrt_fmt_i64(buf, static_cast<int64_t>(num)); // 与 %lld 逐字节一致
    }
    return static_cast<size_t>(std::snprintf(buf, cap, "%f", num));
}

struct QBuf
{
    char *p;
    size_t len;
    size_t cap;
};

static void qbuf_need(QBuf *b, size_t n)
{
    if (b->len + n + 1 > b->cap)
    {
        size_t nc = b->cap ? b->cap * 2 : 64;
        while (nc < b->len + n + 1)
            nc *= 2;
        char *np = static_cast<char *>(qotrt_xalloc(nc));
        if (b->p)
        {
            std::memcpy(np, b->p, b->len + 1);
            qotrt_xfree(b->p, b->cap); // 扩容旧块入池复用（qbuf 缓冲是独占临时量）
        }
        b->p = np;
        b->cap = nc;
    }
}

// 前置声明（ipappend/采纳路径用；实现在下方原位）
static void qbuf_append(QBuf *b, const char *s, size_t n);
static void qbuf_num_concat(QBuf *b, double num);
static void qotrt_tostring(V16 *v, QBuf *b);

// QBuf → QStr 的零拷贝采纳：缓冲所有权直接移交（免一次全量 strdup+拷贝）。
// 采纳后的缓冲遵守字符串缓冲不变式（永不释放/永不再写入）。
static V16 qotrt_str_from_qbuf(QBuf *b)
{
    QStr *q;
    if (b->p != nullptr)
    {
        q = static_cast<QStr *>(qotrt_xalloc(sizeof(QStr)));
        q->len = static_cast<int32_t>(b->len);
        q->cap = static_cast<int32_t>(b->len);
        q->hash = 0;
        q->shared = 0;
        q->s = b->p;
        b->p = nullptr; // 所有权移交，调用方不要再 xfree
    }
    else
    {
        q = qotrt_str_new("", 0); // 空串也走合并分配（单块 33B，免 strdup 二次分配）
    }
    V16 v;
    v.t = VT_STR;
    v.u.p = q;
    return v;
}

// ---------------------------------------------------------------- str+str 直拼（M-PERF）
// 两侧都是串时绕开 QBuf 通用路径（tostring 分派 + 增长式缓冲 + 再拷贝），
// 直接精确分配一次、两段 memcpy。语义与 qotrt_arith op1 的串+串分支逐字节一致。
static V16 qotrt_strcat_ss(QStr *a, QStr *b)
{
    int64_t n64 = static_cast<int64_t>(a->len) + static_cast<int64_t>(b->len);
    if (n64 < 0 || n64 > 0x7FFFFFF0LL)
        return qotrt_str("", 0); // 防御：len 溢出面（实际不可达，int32 已约束）
    int32_t n = static_cast<int32_t>(n64);
    // 头+缓冲合并单块（M-PERF 收尾，同 qotrt_str_new）：b1b 每轮从 2 次分配
    // 降为 1 次；无 QStr 释放路径，单块安全。
    QStr *q = static_cast<QStr *>(qotrt_xalloc(sizeof(QStr) + static_cast<size_t>(n) + 1));
    q->len = n;
    q->cap = n;
    q->hash = 0;
    q->shared = 0;
    char *p = reinterpret_cast<char *>(q + 1);
    std::memcpy(p, a->s, static_cast<size_t>(a->len));
    std::memcpy(p + a->len, b->s, static_cast<size_t>(b->len));
    p[n] = 0;
    q->s = p;
    V16 v;
    v.t = VT_STR;
    v.u.p = q;
    return v;
}

// ---------------------------------------------------------------- 独占追加（M-PERF 核心）
// `s = s + rhs` 的原地追加快路径。仅供 lir_cgen.py 的【追加资格分析】发射
// （QOT_NO_APPEND=1 关闭）—— 静态证明「该槽当前值由本槽自拼接产生且从未
// 泄漏给其它槽/容器/调用」，shared 位是运行期第二道闸。
// 语义与 qotrt_arith(1, acc, rhs) 逐字节一致，差异只在缓冲策略：
//   * acc 为私有串 → 增长 = 新缓冲 + 迁移 + 重指（旧缓冲不释放 —— Arena-free，
//     别名视角永远看到旧字节，等价于全量拷贝语义对世界的可观察效果）；
//   * 其余情形（acc 非串 / shared / 越界）→ 全量拷贝出全新私有串。
// 结果写回 *acc 本体（同一 V16 槽，无新临时）。
static void qotrt_str_ipappend(V16 *acc, V16 *rhs)
{
    // op1 的第一分支：双数字 = 纯算术加（不是拼接）——资格分析没有类型信息，
    // 数字累加槽（如 t = t + a[i]）也可能落到本入口，必须先分派数字语义。
    if (acc->t == VT_NUM && rhs->t == VT_NUM)
    {
        acc->u.d = acc->u.d + rhs->u.d;
        return;
    }
    // 右侧字节化（与 op1 拼接格式逐字节一致）
    char tmp[64];
    const char *rp;
    int32_t rn = 0;
    QBuf tb = {nullptr, 0, 0};
    bool rhs_heap = false;
    if (rhs->t == VT_NUM)
    {
        size_t k = qotrt_num_to_buf_concat(rhs->u.d, tmp, sizeof(tmp));
        if (k < sizeof(tmp))
        {
            rp = tmp;
            rn = static_cast<int32_t>(k);
        }
        else
        { // %f 巨数截断 → 走缓冲
            qbuf_num_concat(&tb, rhs->u.d);
            rp = tb.p;
            rn = static_cast<int32_t>(tb.len);
            rhs_heap = true;
        }
    }
    else if (rhs->t == VT_STR)
    {
        QStr *q = static_cast<QStr *>(rhs->u.p);
        rp = q->s;
        rn = q->len;
    }
    else if (rhs->t != VT_NULL)
    {
        qotrt_tostring(rhs, &tb);
        rp = tb.p ? tb.p : "";
        rn = static_cast<int32_t>(tb.len);
        rhs_heap = true;
    }
    else
    {
        rp = "";
        rn = 0;
    }

    if (acc->t == VT_STR)
    {
        QStr *q = static_cast<QStr *>(acc->u.p);
        if (!q->shared && q->len >= 0 && q->cap >= q->len &&
            static_cast<int64_t>(q->len) + rn <= 0x7FFFFFF0LL)
        {
            int32_t nl = q->len + rn;
            if (q->cap < nl)
            {
                int32_t nc = q->cap * 2;
                if (nc < nl)
                    nc = nl;
                char *np = static_cast<char *>(qotrt_xalloc(static_cast<size_t>(nc) + 1));
                std::memcpy(np, q->s, static_cast<size_t>(q->len)); // 旧缓冲不释放
                q->s = np;
                q->cap = nc;
            }
            if (rn > 0)
                std::memcpy(q->s + q->len, rp, static_cast<size_t>(rn));
            q->len = nl;
            q->s[nl] = 0;
            q->hash = 0; // 内容变了，缓存哈希作废
            if (rhs_heap)
                qotrt_xfree(tb.p, tb.cap);
            return;
        }
    }
    // 通用兜底：全新私有串（acc 老值语义 = op1 的左侧分支）
    QBuf bo = {nullptr, 0, 0};
    if (acc->t == VT_NUM)
        qbuf_num_concat(&bo, acc->u.d);
    else if (acc->t == VT_STR)
    {
        QStr *qa = static_cast<QStr *>(acc->u.p);
        qbuf_append(&bo, qa->s, static_cast<size_t>(qa->len));
    }
    else if (acc->t != VT_NULL)
        qotrt_tostring(acc, &bo);
    if (rn > 0)
        qbuf_append(&bo, rp, static_cast<size_t>(rn));
    *acc = qotrt_str_from_qbuf(&bo);
    if (rhs_heap)
        qotrt_xfree(tb.p, tb.cap);
}

static void qbuf_append(QBuf *b, const char *s, size_t n)
{
    qbuf_need(b, n);
    std::memcpy(b->p + b->len, s, n);
    b->len += n;
    b->p[b->len] = 0;
}

static void qbuf_num(QBuf *b, double num)
{
    char tmp[64];
    size_t n = qotrt_num_to_buf(num, tmp, sizeof(tmp));
    qbuf_append(b, tmp, n);
}

static void qbuf_num_concat(QBuf *b, double num)
{
    char tmp[64];
    size_t n = qotrt_num_to_buf_concat(num, tmp, sizeof(tmp));
    qbuf_append(b, tmp, n);
}

// Value::toString 的复刻（vm_types.h:749）：数组/字典是【字面】"[...]"/"{...}"，
// number 走 std::to_string（%f），NONE→"<none>"。strings() 与 + 拼接用这一份。
static void qotrt_tostring(V16 *v, QBuf *b)
{
    switch (v->t)
    {
    case VT_NONE:
        qbuf_append(b, "<none>", 6);
        break;
    case VT_NULL:
        qbuf_append(b, "null", 4);
        break;
    case VT_BOOL:
        qbuf_append(b, v->u.d != 0 ? "true" : "false", v->u.d != 0 ? 4 : 5);
        break;
    case VT_NUM:
        qbuf_num(b, v->u.d);
        break;
    case VT_STR:
    {
        QStr *q = static_cast<QStr *>(v->u.p);
        qbuf_append(b, q->s, static_cast<size_t>(q->len));
        break;
    }
    case VT_ARR:
        qbuf_append(b, "[...]", 5);
        break;
    case VT_DICT:
        qbuf_append(b, "{...}", 5);
        break;
    case VT_CLOSURE:
        qbuf_append(b, "closure", 7);
        break;
    case VT_ERR:
    {
        QErr *e = static_cast<QErr *>(v->u.p);
        qbuf_append(b, "err(", 4);
        qotrt_tostring(&e->payload, b);
        qbuf_append(b, ")", 1);
        break;
    }
    case VT_ENV:
        qbuf_append(b, "<env>", 5);
        break;
    case VT_OBJ:
    {
        // M4："<类名>" —— 确定性输出（无地址，golden 可钉）
        QObj *o = static_cast<QObj *>(v->u.p);
        const char *nm = qotrt_types[o->tid].name;
        size_t nl = std::strlen(nm);
        qbuf_append(b, "<", 1);
        qbuf_append(b, nm, nl);
        qbuf_append(b, ">", 1);
        break;
    }
    default:
        qbuf_append(b, "unknown", 7);
        break;
    }
}

// valueToString 的复刻（builtin.cpp:934）：数组 [..] 字典 {k: v, ..} 递归（仅 output 用）
static void qotrt_to_str_value(V16 *v, QBuf *b)
{
    switch (v->t)
    {
    case VT_ARR:
    {
        QArr *a = static_cast<QArr *>(v->u.p);
        qbuf_append(b, "[", 1);
        for (int32_t i = 0; i < a->len; i++)
        {
            if (i > 0)
                qbuf_append(b, ", ", 2);
            qotrt_to_str_value(&a->a[i], b);
        }
        qbuf_append(b, "]", 1);
        break;
    }
    case VT_DICT:
    {
        QDict *d = static_cast<QDict *>(v->u.p);
        qbuf_append(b, "{", 1);
        int32_t printed = 0;
        for (int32_t i = 0; i < d->cap; i++)
        {
            if (d->slots[i].h > QD_DELETED)
            {
                if (printed > 0)
                    qbuf_append(b, ", ", 2);
                qbuf_append(b, d->slots[i].inl ? d->slots[i].k.inl : d->slots[i].k.ext,
                            static_cast<size_t>(d->slots[i].klen));
                qbuf_append(b, ": ", 2);
                qotrt_to_str_value(&d->slots[i].v, b);
                printed++;
            }
        }
        qbuf_append(b, "}", 1);
        break;
    }
    case VT_NULL:
        qbuf_append(b, "null", 4);
        break;
    case VT_NONE:
        qbuf_append(b, "<none>", 6);
        break;
    case VT_BOOL:
        qbuf_append(b, v->u.d != 0 ? "true" : "false", v->u.d != 0 ? 4 : 5);
        break;
    case VT_NUM:
        qbuf_num(b, v->u.d);
        break;
    case VT_STR:
    {
        QStr *q = static_cast<QStr *>(v->u.p);
        qbuf_append(b, q->s, static_cast<size_t>(q->len));
        break;
    }
    case VT_CLOSURE:
        qbuf_append(b, "closure", 7);
        break;
    case VT_ERR:
    {
        QErr *e = static_cast<QErr *>(v->u.p);
        qbuf_append(b, "err(", 4);
        qotrt_to_str_value(&e->payload, b);
        qbuf_append(b, ")", 1);
        break;
    }
    case VT_ENV:
        qbuf_append(b, "<env>", 5);
        break;
    case VT_OBJ:
    {
        QObj *o = static_cast<QObj *>(v->u.p);
        const char *nm = qotrt_types[o->tid].name;
        size_t nl = std::strlen(nm);
        qbuf_append(b, "<", 1);
        qbuf_append(b, nm, nl);
        qbuf_append(b, ">", 1);
        break;
    }
    default:
        qbuf_append(b, "unknown", 7);
        break;
    }
}

// ---- throw（未捕获文案需要 to_str_value，故置于此）----
static void qotrt_throw(V16 v)
{
    if (qotrt_landings == nullptr)
    {
        qotrt_thrown = v;
        QBuf b = {nullptr, 0, 0};
        qotrt_to_str_value(&qotrt_thrown, &b);
        std::fprintf(stderr, "[错误] [2001] 未捕获的 throw: %s\n",
                     b.p ? b.p : "null");
        if (b.p)
            qotrt_xfree(b.p, b.cap);
        std::exit(124);
    }
    qotrt_thrown = v;
    QotrtLanding *top = qotrt_landings;
    QOT_SJ_JMP(top);
}

// ---- err()/is_err()/err_val() 的内置入口（args/argc 签名，进 builtins.toml）----
static V16 qotrt_err(V16 *args, int argc)
{
    if (argc < 1)
        return qotrt_err_make(qotrt_null_v());
    return qotrt_err_make(args[0]);
}

static V16 qotrt_bi_is_err(V16 *args, int argc)
{
    if (argc < 1)
        return qotrt_bool(false);
    return qotrt_bool(qotrt_is_err(args[0]));
}

static V16 qotrt_err_val(V16 *args, int argc)
{
    if (argc < 1)
        return qotrt_null_v();
    return qotrt_err_payload(args[0]);
}

static const char *qotrt_truthy_str(V16 *v)
{
    // 仅用于分支判断：VM isTruthy —— none/null=false, bool, num!=0, str/arr/dict 非空
    switch (v->t)
    {
    case VT_NONE:
    case VT_NULL:
        return nullptr;
    case VT_BOOL:
        return v->u.d != 0 ? (const char *)1 : nullptr;
    case VT_NUM:
        return v->u.d != 0 ? (const char *)1 : nullptr;
    case VT_STR:
    {
        QStr *q = static_cast<QStr *>(v->u.p);
        return q->len > 0 ? (const char *)1 : nullptr;
    }
    case VT_ARR:
    {
        QArr *a = static_cast<QArr *>(v->u.p);
        return a->len > 0 ? (const char *)1 : nullptr;
    }
    case VT_DICT:
    {
        QDict *d = static_cast<QDict *>(v->u.p);
        return d->size > 0 ? (const char *)1 : nullptr;
    }
    default:
        return (const char *)1;
    }
}

// ---------------------------------------------------------------- 算术（复刻 vm_executor case 10）
// op: 1..14 与字节码 BINARY_OP 子码一致
//
// f64 快速路径（T-44）：语义与上面一致，输入已是裸 double。
// 定义在 qotrt_arith 之前（case 4/5 委托到这里，单一实现 G-11）。

static inline double qotrt_fdiv(double a, double b) { return b != 0 ? a / b : 0.0; }

// qotrt_fmod_ —— % 运算的唯一实现（qotrt_arith case 5 与 LIR MOD_F 都走这里）。
//
// 精确整数快路径（N-02 后续：% 是 order_engine 热循环的剩余大头）：
// 当 a、b 都是 [-2^31+1, 2^31-1] 内的整数值时，数学余数 r = a - b*trunc(a/b)
// 满足 |r| < |b| <= 2^31-1，本身是 int32 域内的整数，因此 double 必能精确表示；
// 而 C 的 % 与 fmod 同为「向零截断」语义、余数符号都随被除数，
// 故 (double)(ia % ib) 与 std::fmod(a, b) 逐位相等（G-10：不是近似替换，是精确改写）。
// 两个特意的保守边界：
//   * 下界取 -2^31+1 而非 INT_MIN：ia=INT_MIN, ib=-1 时 32 位 idiv 的商会溢出，
//     触发 #DE 硬件异常（进程崩溃）。排除后任意组合 |q| <= 2^31-1 不可能溢出；
//     落在界外的输入原样走 std::fmod（精确，只是慢）。
//   * 零余数按被除数符号位回填 -0.0：fmod(-5, 5) = -0.0 而 0 % 5 = +0，
//     VM 的 %f 输出会把两者打成 "-0.000000" / "0.000000"——可观察，必须保号。
//     （判据用 std::signbit 而不是 a < 0：-0.0 < 0.0 在 IEEE 下为假。）
// b == 0 仍按 VM 定价行为返回 0（fmod 本身是 NaN，这里对齐 case 5 的 b!=0 判式）。
static inline double qotrt_fmod_(double a, double b)
{
    if (b != 0.0)
    {
        if (a >= -2147483647.0 && a <= 2147483647.0 &&
            b >= -2147483647.0 && b <= 2147483647.0)
        {
            int ia = (int)a;
            int ib = (int)b;
            if ((double)ia == a && (double)ib == b)
            {
                int r = ia % ib;
                if (r == 0 && std::signbit(a))
                    return -0.0;
                return (double)r;
            }
        }
        return std::fmod(a, b);
    }
    return 0.0;
}

// 常量除数特化：cgen 对「MOD_F 且除数是编译期已知的非零小整数」发射本入口。
// 除数已知后免掉一半的范围检查与一次 int 转换，且 MSVC /O2 会把 `% c`
// （常量）强度削减成乘法+移位序列，完全绕开 div 硬件指令。
// 精确性论证与 qotrt_fmod_ 相同；c==0 / 超界 / 非整数输入委托回通用路径。
static inline double qotrt_fmod_ci(double a, int c)
{
    if (c != 0 && a >= -2147483647.0 && a <= 2147483647.0)
    {
        int ia = (int)a;
        if ((double)ia == a)
        {
            int r = ia % c;
            if (r == 0 && std::signbit(a))
                return -0.0;
            return (double)r;
        }
    }
    return qotrt_fmod_(a, (double)c);
}

static void qotrt_arith(int op, V16 *l, V16 *r, V16 *out)
{
    if (l->t == VT_NUM && r->t == VT_NUM)
    {
        double a = l->u.d, b = r->u.d;
        switch (op)
        {
        case 1:
            *out = qotrt_num(a + b);
            break;
        case 2:
            *out = qotrt_num(a - b);
            break;
        case 3:
            *out = qotrt_num(a * b);
            break;
        case 4:
            *out = qotrt_num(qotrt_fdiv(a, b)); // 除零得 0（VM 定价行为）
            break;
        case 5:
            *out = qotrt_num(qotrt_fmod_(a, b));
            break;
        case 6:
            *out = qotrt_num(std::pow(a, b));
            break;
        case 7:
            *out = qotrt_bool(a > b);
            break;
        case 8:
            *out = qotrt_bool(a < b);
            break;
        case 9:
            *out = qotrt_bool(a >= b);
            break;
        case 10:
            *out = qotrt_bool(a <= b);
            break;
        case 11:
            *out = qotrt_bool(a == b);
            break;
        case 12:
            *out = qotrt_bool(a != b);
            break;
        default:
            *out = qotrt_null_v();
        }
        return;
    }
    if (op == 11) // == 的跨类型语义（复刻 case 10 binop==11）
    {
        // VM 的 isNull() 对 VT_NONE 与 VT_NULL 均为真（vm_types.h Value::isNull，
        // 未赋值全局槽 == null 为 true —— bound_guard_global_rw 差分钉住）
        bool ln = (l->t == VT_NULL || l->t == VT_NONE);
        bool rn = (r->t == VT_NULL || r->t == VT_NONE);
        bool res = false;
        if (l->t == VT_NUM && r->t == VT_BOOL)
            res = (l->u.d != 0) == (r->u.d != 0);
        else if (l->t == VT_BOOL && r->t == VT_NUM)
            res = (l->u.d != 0) == (r->u.d != 0);
        else if (l->t == VT_BOOL && r->t == VT_BOOL)
            res = (l->u.d != 0) == (r->u.d != 0); // 【P0.2 轮修复】BOOL/BOOL 曾恒 false
        else if (l->t == VT_STR && r->t == VT_STR)
        {
            QStr *a = static_cast<QStr *>(l->u.p);
            QStr *b = static_cast<QStr *>(r->u.p);
            if (a->hash != 0 && b->hash != 0 && a->hash != b->hash)
                res = false; // 哈希快筛：两侧哈希都已知且不同 → 必不等
            else
                res = a->len == b->len && std::memcmp(a->s, b->s, static_cast<size_t>(a->len)) == 0;
        }
        else if (ln && rn)
            res = true;
        else if (l->t == VT_OBJ && r->t == VT_OBJ)
            res = l->u.p == r->u.p; // 【M4】对象相等 = 同一实例（qotrt_veq 同律）
        *out = qotrt_bool(res);
        return;
    }
    if (op == 12) // != 的跨类型语义
    {
        bool ln = (l->t == VT_NULL || l->t == VT_NONE);
        bool rn = (r->t == VT_NULL || r->t == VT_NONE);
        bool res;
        if (ln && rn)
            res = false;
        else if (l->t == VT_STR && r->t == VT_STR)
        {
            QStr *a = static_cast<QStr *>(l->u.p);
            QStr *b = static_cast<QStr *>(r->u.p);
            res = !(a->len == b->len && std::memcmp(a->s, b->s, static_cast<size_t>(a->len)) == 0);
        }
        else if (l->t == VT_BOOL && r->t == VT_NUM)
            res = (l->u.d != 0) != (r->u.d != 0);
        else if (l->t == VT_NUM && r->t == VT_BOOL)
            res = (l->u.d != 0) != (r->u.d != 0);
        else if (l->t == VT_BOOL && r->t == VT_BOOL)
            res = (l->u.d != 0) != (r->u.d != 0); // 【P0.2 轮修复】BOOL/BOOL 曾恒 true
        else if (l->t == VT_OBJ && r->t == VT_OBJ)
            res = l->u.p != r->u.p; // 【M4】对象不等 = 不同实例
        else
            res = true;
        *out = qotrt_bool(res);
        return;
    }
    if (op == 13) // &&：两侧都求值后的 truthy 合取（VM 无短路）
    {
        *out = qotrt_bool(qotrt_truthy_str(l) && qotrt_truthy_str(r));
        return;
    }
    if (op == 14) // ||
    {
        *out = qotrt_bool(qotrt_truthy_str(l) || qotrt_truthy_str(r));
        return;
    }
    if (op == 1) // + 的拼接分支：null→""；数字 int 化（concat 专用格式）；其余 toString
    {
        if (l->t == VT_STR && r->t == VT_STR)
        {
            *out = qotrt_strcat_ss(static_cast<QStr *>(l->u.p), static_cast<QStr *>(r->u.p));
            return;
        }
        QBuf bo = {nullptr, 0, 0};
        if (l->t == VT_NUM)
        {
            qbuf_num_concat(&bo, l->u.d);
        }
        else if (l->t != VT_NULL)
        {
            qotrt_tostring(l, &bo);
        }
        if (r->t == VT_NUM)
        {
            qbuf_num_concat(&bo, r->u.d);
        }
        else if (r->t != VT_NULL)
        {
            qotrt_tostring(r, &bo);
        }
        *out = qotrt_str_from_qbuf(&bo); // 缓冲采纳，免二次全量拷贝
        return;
    }
    *out = qotrt_null_v(); // 未知子码（VM 同款兜底）
}

static void qotrt_unary(int op, V16 *a, V16 *out)
{
    if (op == 1)
    {
        if (a->t == VT_NUM)
            *out = qotrt_num(-a->u.d);
        else
            *out = qotrt_null_v();
    }
    else if (op == 2)
    {
        *out = qotrt_bool(!qotrt_truthy_str(a));
    }
    else
    {
        *out = qotrt_null_v();
    }
}

// ---------------------------------------------------------------- 下标（复刻 case 42/43）
static void qotrt_index_get(V16 *c, V16 *idx, V16 *out)
{
    if (c->t == VT_ARR && idx->t == VT_NUM)
    {
        QArr *a = static_cast<QArr *>(c->u.p);
        int i = static_cast<int>(idx->u.d);
        if (i >= 0 && i < a->len)
            *out = a->a[i];
        else
            *out = qotrt_null_v(); // 越界得 null（VM 定价行为）
    }
    else if (c->t == VT_DICT && idx->t == VT_STR)
    {
        QDict *d = static_cast<QDict *>(c->u.p);
        QStr *k = static_cast<QStr *>(idx->u.p);
        V16 *hit = qotrt_dict_find(d, k->s, k->len, qotrt_vhash(idx));
        *out = hit ? *hit : qotrt_null_v(); // 缺键得 null
    }
    else if (c->t == VT_STR && idx->t == VT_NUM)
    {
        QStr *q = static_cast<QStr *>(c->u.p);
        int i = static_cast<int>(idx->u.d);
        if (i >= 0 && i < q->len)
            *out = qotrt_str(q->s + i, 1);
        else
            *out = qotrt_null_v();
    }
    else
    {
        *out = qotrt_null_v();
    }
}

static void qotrt_index_set(V16 *c, V16 *idx, V16 *val)
{
    if (c->t == VT_ARR && idx->t == VT_NUM)
    {
        QArr *a = static_cast<QArr *>(c->u.p);
        int i = static_cast<int>(idx->u.d);
        if (i >= 0 && i < a->len)
            a->a[i] = *val; // 越界写忽略（VM 定价行为）
    }
    else if (c->t == VT_DICT && idx->t == VT_STR)
    {
        QDict *d = static_cast<QDict *>(c->u.p);
        QStr *k = static_cast<QStr *>(idx->u.p);
        *qotrt_dict_at(d, k->s, k->len, qotrt_vhash(idx)) = *val;
    }
}

// ---------------------------------------------------------------- 内置函数（复刻 builtin.cpp，支持集内的）
static void qotrt_output(V16 *args, int argc)
{
    // builtin_output：多参空格连接 + 换行；每参走 valueToString
    QBuf b = {nullptr, 0, 0};
    for (int i = 0; i < argc; i++)
    {
        if (i > 0)
            qbuf_append(&b, " ", 1);
        qotrt_to_str_value(&args[i], &b);
    }
    qbuf_append(&b, "\n", 1);
    if (b.p)
    {
        std::fputs(b.p, stdout);
        qotrt_xfree(b.p, b.cap); // output 不保留引用，缓冲可还池
    }
    else
        std::fputc('\n', stdout);
}

static V16 qotrt_input(V16 *args, int argc)
{
    if (argc > 0)
    {
        QBuf p = {nullptr, 0, 0};
        for (int i = 0; i < argc; i++)
        {
            if (i > 0)
                qbuf_append(&p, " ", 1);
            qotrt_to_str_value(&args[i], &p);
        }
        if (p.p)
        {
            std::fputs(p.p, stdout);
            qotrt_xfree(p.p, p.cap); // 提示串不保留引用
        }
        std::fflush(stdout); // 与 Python input(prompt) 同律：提示先落地再等输入
    }
    QBuf b = {nullptr, 0, 0};
    for (;;)
    {
        int c = std::fgetc(stdin);
        if (c == EOF)
            break; // EOF：已读到的部分行（或空串）直接交付
        if (c == '\n')
            break; // 整行到手（'\n' 未入缓冲，无需剥尾；CRLF 已在文本模式归一）
        char ch = static_cast<char>(c);
        qbuf_append(&b, &ch, 1);
    }
    return qotrt_str_from_qbuf(&b); // 缓冲所有权移交字符串（零拷贝采纳）
}

static V16 qotrt_push(V16 *args, int argc)
{
    if (argc < 2 || args[0].t != VT_ARR)
        return qotrt_bool(false); // 参数错误路径（与 builtin_push 一致）
    QArr *a = static_cast<QArr *>(args[0].u.p);
    for (int i = 1; i < argc; i++)
        qotrt_arr_push(a, args[i]);
    return qotrt_bool(true);
}

static V16 qotrt_glen(V16 *args, int argc)
{
    if (argc < 1)
        return qotrt_num(0.0);
    V16 *a = &args[0];
    if (a->t == VT_STR)
        return qotrt_num(static_cast<double>(static_cast<QStr *>(a->u.p)->len));
    if (a->t == VT_ARR)
        return qotrt_num(static_cast<double>(static_cast<QArr *>(a->u.p)->len));
    if (a->t == VT_DICT)
        return qotrt_num(static_cast<double>(static_cast<QDict *>(a->u.p)->size));
    return qotrt_num(0.0);
}

static V16 qotrt_dict_set(V16 *args, int argc)
{
    if (argc < 3 || args[0].t != VT_DICT)
        return qotrt_bool(false);
    QDict *d = static_cast<QDict *>(args[0].u.p);
    // dict_set 的键经 Value::toString（数字键 5 → "5.000000"，与 VM 一致）
    QBuf b = {nullptr, 0, 0};
    qotrt_tostring(&args[1], &b);
    V16 *slot = qotrt_dict_at(d, b.p ? b.p : "", static_cast<int32_t>(b.len),
                              qotrt_hash(b.p ? b.p : "", b.len));
    *slot = args[2];
    return qotrt_bool(true);
}

static V16 qotrt_dict_get(V16 *args, int argc)
{
    if (argc < 2 || args[0].t != VT_DICT)
        return qotrt_null_v();
    QDict *d = static_cast<QDict *>(args[0].u.p);
    QBuf b = {nullptr, 0, 0};
    qotrt_tostring(&args[1], &b);
    V16 *hit = qotrt_dict_find(d, b.p ? b.p : "", static_cast<int32_t>(b.len),
                               qotrt_hash(b.p ? b.p : "", b.len));
    V16 out;
    if (hit)
        out = *hit;
    else if (argc >= 3)
        out = args[2];
    else
        out = qotrt_null_v();
    if (b.p)
        qotrt_xfree(b.p, b.cap); // 查找不保留键引用，临时缓冲还池
    return out;
}

static V16 qotrt_dict_keys(V16 *args, int argc)
{
    if (argc >= 1 && args[0].t == VT_DICT)
    {
        QDict *d = static_cast<QDict *>(args[0].u.p);
        // 两遍法：先数有效槽（精确容量，免扩容），再物化键视图。
        // 键视图 = 新 QStr 头 + 键缓冲：外部键零拷贝借用（不变式保证永恒
        // 有效）；内联键（≤8B，键内容在槽里，无独立缓冲可借）拷出一小块
        // 池缓冲。
        // 【E1】hash 不再能继承槽里的值 —— 槽里是 32 位标签，而回查要用
        // 完整 64 位去算桶位/步长；拿标签当哈希会算出**错误的桶位**，表现是
        // 「键明明在表里却查不到」（静默错误，最坏形态）。因此置 0 交给
        // qotrt_vhash 惰性重算：正确性优先，代价是每轮每键一次哈希
        // （b2c 的已知账单，见 §6.2.6 与里程碑文档）。
        int32_t n = 0;
        for (int32_t i = 0; i < d->cap; i++)
            if (d->slots[i].h > QD_DELETED)
                n++;
        QArr *a = static_cast<QArr *>(qotrt_arr_new(n > 4 ? n : 4).u.p);
        for (int32_t i = 0; i < d->cap; i++)
        {
            if (d->slots[i].h > QD_DELETED)
            {
                QStr *q = static_cast<QStr *>(qotrt_xalloc(sizeof(QStr)));
                q->len = d->slots[i].klen;
                q->cap = q->len;
                q->hash = d->slots[i].h;
                q->shared = 1;
                if (d->slots[i].inl)
                {
                    char *buf = static_cast<char *>(
                        qotrt_xalloc(static_cast<size_t>(q->len) + 1));
                    std::memcpy(buf, d->slots[i].k.inl, static_cast<size_t>(q->len));
                    buf[q->len] = 0;
                    q->s = buf;
                }
                else
                {
                    q->s = d->slots[i].k.ext;
                }
                a->a[a->len].t = VT_STR;
                a->a[a->len].u.p = q;
                a->len++;
            }
        }
        V16 out;
        out.t = VT_ARR;
        out.u.p = a;
        return out;
    }
    return qotrt_arr_new(8);
}

static V16 qotrt_type(V16 *args, int argc)
{
    if (argc < 1)
        return qotrt_str("null", 4);
    switch (args[0].t)
    {
    case VT_NULL:
        return qotrt_str("null", 4);
    case VT_BOOL:
        return qotrt_str("bool", 4);
    case VT_NUM:
        return qotrt_str("number", 6);
    case VT_STR:
        return qotrt_str("string", 6);
    case VT_ARR:
        return qotrt_str("array", 5);
    case VT_DICT:
        return qotrt_str("dict", 4);
    case VT_CLOSURE:
        return qotrt_str("function", 8);
    case VT_ERR:
        return qotrt_str("error", 5);
    case VT_OBJ:
        return qotrt_str("object", 6);
    default:
        return qotrt_str("unknown", 7);
    }
}

static V16 qotrt_gtypes(V16 *args, int argc)
{
    if (argc < 1)
        return qotrt_str("any", 3);
    switch (args[0].t)
    {
    case VT_STR:
        return qotrt_str("string", 6);
    case VT_ARR:
        return qotrt_str("array", 5);
    case VT_NUM:
        return qotrt_str("number", 6);
    case VT_BOOL:
        return qotrt_str("boolean", 7);
    case VT_DICT:
        return qotrt_str("dict", 4);
    case VT_NULL:
        return qotrt_str("null", 4);
    case VT_CLOSURE:
        return qotrt_str("function", 8);
    case VT_ERR:
        return qotrt_str("error", 5);
    case VT_OBJ:
        return qotrt_str("object", 6);
    default:
        return qotrt_str("any", 3);
    }
}

static V16 qotrt_strings(V16 *args, int argc)
{
    // builtin_to_string = Value::toString（数组是字面 "[...]"，不是递归展开）。
    // 数字实参走栈上缓冲快路径（qotrt_num_to_buf 的整数值特化保证与 %f
    // 逐字节一致）—— b2b/b7b 的动态键构造从 ~300ns(snprintf+堆) 降到 ~20ns。
    if (argc < 1)
        return qotrt_str("", 0);
    if (args[0].t == VT_NUM)
    {
        char tmp[64];
        size_t n = qotrt_num_to_buf(args[0].u.d, tmp, sizeof(tmp));
        return qotrt_str(tmp, static_cast<int32_t>(n));
    }
    QBuf b = {nullptr, 0, 0};
    qotrt_tostring(&args[0], &b);
    return qotrt_str_from_qbuf(&b);
}

static V16 qotrt_numbers(V16 *args, int argc)
{
    // builtin_to_number = std::stod(toString(arg))，失败/空前缀 → 0
    if (argc < 1)
        return qotrt_num(0.0);
    QBuf b = {nullptr, 0, 0};
    qotrt_tostring(&args[0], &b);
    char *end = nullptr;
    double d = std::strtod(b.p ? b.p : "", &end);
    if (end == (b.p ? b.p : ""))
        return qotrt_num(0.0);
    return qotrt_num(d);
}

static V16 qotrt_is_number(V16 *args, int argc)
{
    if (argc < 1)
        return qotrt_bool(false);
    return qotrt_bool(args[0].t == VT_NUM);
}

static V16 qotrt_iter_len(V16 *args, int argc)
{
    if (argc < 1)
        return qotrt_num(0.0);
    V16 *a = &args[0];
    if (a->t == VT_NUM)
    {
        double n = a->u.d;
        return qotrt_num(n < 0 ? 0 : n);
    }
    if (a->t == VT_ARR)
        return qotrt_num(static_cast<double>(static_cast<QArr *>(a->u.p)->len));
    if (a->t == VT_STR)
        return qotrt_num(static_cast<double>(static_cast<QStr *>(a->u.p)->len));
    return qotrt_num(0.0);
}

// 【M3 0】for-in 脱糖内部元语：dict → 键数组物化（槽序，与
// dict_keys 同一实现），其余类型原样透传。`for (k in d)` 由此从「静默
// 零次」（iter_len(dict)==0 的历史行为保持不动）变为按槽序迭代键 ——
// 与官方姿势 `for (k in dict_keys(d))` 同源同价。每条 for 语句入口调用
// 一次（非每迭代），分配代价 = 一次键数组物化。
static V16 qotrt_for_keys(V16 *args, int argc)
{
    if (argc < 1)
        return qotrt_null_v();
    if (args[0].t == VT_DICT)
        return qotrt_dict_keys(args, argc);
    return args[0];
}

// ---------------------------------------------------------------- 单参核心入口（N-05 内置快速通道）
// cgen 对 argc 匹配的 CALL_BUILTIN 直接发核心入口，免掉调用点的 V16 args[]
// 数组构建（每参一次 16B 拷贝）。核心入口一律委托上面的 argc 版本——语义
// 只有一份实现（G-11：两处手写同一语义迟早漂移）。cl /O2 会把它们整体内联，
// args 指针退化为调用点槽的直接引用，等效于把函数体展开进调用点。
static V16 qotrt_iter_len1(V16 *a) { return qotrt_iter_len(a, 1); }
static V16 qotrt_is_number1(V16 *a) { return qotrt_is_number(a, 1); }
static V16 qotrt_glen1(V16 *a) { return qotrt_glen(a, 1); }
static V16 qotrt_type1(V16 *a) { return qotrt_type(a, 1); }
static V16 qotrt_gtypes1(V16 *a) { return qotrt_gtypes(a, 1); }
static V16 qotrt_strings1(V16 *a) { return qotrt_strings(a, 1); }
static V16 qotrt_numbers1(V16 *a) { return qotrt_numbers(a, 1); }

static V16 qotrt_push2(V16 *arr, V16 *v)
{
    // qotrt_push 的 argc==2 特化（循环展开由 cl 完成，这里显式化保证无循环框架）
    if (arr->t != VT_ARR)
        return qotrt_bool(false); // 参数错误路径（与 builtin_push 一致）
    qotrt_arr_push(static_cast<QArr *>(arr->u.p), *v);
    return qotrt_bool(true);
}

static void qotrt_output1(V16 *a)
{
    // 单参 output：数字参数直通 printf 缓冲（qotrt_output 的 QBuf 构建 +
    // 堆分配对热路径的数字打印是纯开销）。格式化唯一来源仍是
    // qotrt_num_to_buf（"%f"），换行与 fputs 的字节序与 qotrt_output 逐字节一致。
    if (a->t == VT_NUM)
    {
        char tmp[64];
        size_t n = qotrt_num_to_buf(a->u.d, tmp, sizeof(tmp));
        std::fwrite(tmp, 1, n, stdout);
        std::fputc('\n', stdout);
        return;
    }
    qotrt_output(a, 1);
}

// ---------------------------------------------------------------- 常量池
// 由 cgen 生成的初始化器填 consts[]（字符串在运行期建堆对象）
#ifdef QOT_RT_SPLIT
extern V16 *qotrt_consts;
#else
static V16 *qotrt_consts = nullptr;
#endif
#ifdef QOT_RT_SPLIT
extern int qotrt_nconsts;
#else
static int qotrt_nconsts = 0;
#endif

static V16 qotrt_const(int idx)
{
    if (idx < 0 || idx >= qotrt_nconsts)
        return qotrt_null_v(); // -1 哨兵 = null（与 VM PUSH_CONST NULL_MAGIC 一致）
    return qotrt_consts[idx];
}

// ---------------------------------------------------------------- 资源上限（S-03）
// QOT_TIMEOUT_MS：回跳计数预算（main() 里读环境变量，0/未设 = 不限）。
// 每次循环回边（JMP/BR 目标 ≤ 当前 pc）计数一次；超限 → stderr [2000] + exit 124。
// 粒度是粗校准（TICKS_PER_MS 为经验常数），目的是兜底死循环而非精确计时。
#ifdef QOT_RT_SPLIT
extern long long qotrt_ticks;
#else
static long long qotrt_ticks = 0;
#endif
#ifdef QOT_RT_SPLIT
extern long long qotrt_tick_limit;
#else
static long long qotrt_tick_limit = -1;   // -1 = 未初始化；INT64_MAX = 不限
#endif
#ifdef QOT_RT_SPLIT
extern int qotrt_max_depth;
#else
static int qotrt_max_depth = 5000;        // 递归深度上限（QOT_MAX_DEPTH 可覆盖）
#endif

static void qotrt_fail_timeout(long long ms)
{
    std::fprintf(stderr, "[错误] [2000] 执行超时（QOT_TIMEOUT_MS=%lld，回跳计数超限）\n",
                 ms);
    std::exit(124);
}

static void qotrt_fail_depth(int depth)
{
    std::fprintf(stderr, "[错误] [2000] 递归深度超限（depth=%d，上限 %d）\n",
                 depth, qotrt_max_depth);
    std::exit(124);
}

static void qotrt_init_limits()
{
    const char *t = std::getenv("QOT_TIMEOUT_MS");
    if (t != nullptr)
    {
        long long ms = std::atoll(t);
        if (ms > 0)
            qotrt_tick_limit = ms * 200000LL; // 经验校准：~200k 回跳/ms（i5 级）
    }
    const char *d = std::getenv("QOT_MAX_DEPTH");
    if (d != nullptr)
    {
        int dv = std::atoi(d);
        if (dv > 0)
            qotrt_max_depth = dv;
    }
}

static void qotrt_tick()
{
    if (qotrt_tick_limit >= 0 && ++qotrt_ticks > qotrt_tick_limit)
        qotrt_fail_timeout(qotrt_tick_limit / 200000LL);
}

// ============================================================================
// P0.1 原语层（0 · CP-2 · docs/ §4.1–4.6）
// ----------------------------------------------------------------------------
// 规格 = P0.1 批：A 文件与路径 / B 字符串 / D 数字 / E 数组 / F 字典。
// 语义纪律（§4.0/§7，与 builtins.toml 的 desc 一一对应）：
//   * 失败哨兵不抛异常：数值 → -1；字符串 → ""；数组/字典 → null；布尔 → false；
//   * 成功哨兵不与失败混淆（str_find 未命中 -1；file_read 读空 ""）；
//   * 字符串 = UTF-8 字节序列：str_* 按字节，char_* 按 UTF-8 码点
//     （非法字节各按 1 个字符计，与解释器 oracle 的 surrogateescape 口径一致）；
//   * 文件一律**字节语义**（无文本模式换行翻译；§8 跨平台表）；
//   * 句柄 = 数字（1 起，0 无效；close 后永久失效，可被复用检测——槽存世代）；
//   * 数组越界读 null / 写忽略；字典键统一 tostring（数字键 "5.000000"）；
//   * 平台差异全部吸收在这一层：Windows 走 W 系 API（UTF-8↔UTF-16 边界
//     转换，中文/非 ANSI 路径安全），POSIX 走 dirent/stat/flock。
// 内存纪律（文件头不变式）：QStr/QArr/QDict 分配后永不释放；xfree 只用于
// 「独占临时缓冲」，且必须带原分配尺寸。
// ============================================================================

// ---- 高阶原语的闭包回调（arr_map/filter/reduce/sort_by）----
// 生成代码的入口表是 TU 定义（lir_cgen 去 static），这里 extern 引用。
// 深度守卫：原语回调不经过 cgen 的 depth 参数链，用独立计数对 qotrt_max_depth
// 兜底 —— 否则 fn 里递归 arr_map 会打穿 C 栈（硬崩溃，违宪法 6）。
typedef void (*QotCloFn)(V16 *ret, V16 *args, int argc, int depth, QEnv *env);
extern QotCloFn qot_clo_fns[];
extern int qotrt_clo_count;

#ifdef QOT_RT_SPLIT
extern int qotrt_cb_depth;
#else
static int qotrt_cb_depth = 0;
#endif

static V16 qotrt_clo_call(V16 clov, V16 *args, int argc)
{
    if (clov.t != VT_CLOSURE)
        qotrt_fail_not_callable(clov.t);
    QClosure *c = static_cast<QClosure *>(clov.u.p);
    if (c->fn < 0 || c->fn >= qotrt_clo_count)
        qotrt_fail_not_callable(VT_CLOSURE);
    if (qotrt_cb_depth + 1 > qotrt_max_depth)
        qotrt_fail_depth(qotrt_cb_depth + 1);
    V16 ret;
    qotrt_cb_depth++;
    qot_clo_fns[c->fn](&ret, args, argc, qotrt_cb_depth + 1, c->env);
    qotrt_cb_depth--;
    return ret;
}

static bool qotrt_is_clo(V16 v)
{
    return v.t == VT_CLOSURE;
}

// ---- 实参归一助手（两侧 oracle 逐位对齐的公共口径）----

// 数值化：NUM/BOOL 原样（bool 真=1.0）；其余 tostring 后 strtod
// （空前缀/不可解析 → 0.0，与 qotrt_numbers 定价一致）。
static double qotrt_prim_num(V16 *v)
{
    if (v->t == VT_NUM)
        return v->u.d;
    if (v->t == VT_BOOL)
        return v->u.d;
    QBuf b = {nullptr, 0, 0};
    qotrt_tostring(v, &b);
    double d = std::strtod(b.p ? b.p : "", nullptr);
    if (b.p)
        qotrt_xfree(b.p, b.cap);
    return d;
}

// int64 化（向零截断）。超域/NaN → ok=false，调用方按各自失败哨兵返回。
static int64_t qotrt_prim_i64(V16 *v, bool *ok)
{
    double d = qotrt_prim_num(v);
    if (!(d >= -9.2e18 && d <= 9.2e18))
    {
        if (ok)
            *ok = false;
        return -1;
    }
    if (ok)
        *ok = true;
    return static_cast<int64_t>(d);
}

// 字节串读取：VT_STR 借用其缓冲（永不失效，不变式）；其余 tostring 进
// 临时 qbuf。调用方用完必须 qotrt_prim_bytes_free。
struct QrtBytes
{
    const char *p;
    int32_t len;
    QBuf tmp; // tmp.p != 0 时拥有缓冲
};

static void qotrt_prim_bytes(V16 *v, QrtBytes *out)
{
    out->tmp.p = nullptr;
    out->tmp.len = 0;
    out->tmp.cap = 0;
    if (v->t == VT_STR)
    {
        QStr *q = static_cast<QStr *>(v->u.p);
        out->p = q->s;
        out->len = q->len;
        return;
    }
    qotrt_tostring(v, &out->tmp);
    out->p = out->tmp.p ? out->tmp.p : "";
    out->len = static_cast<int32_t>(out->tmp.len);
}

static void qotrt_prim_bytes_free(QrtBytes *b)
{
    if (b->tmp.p)
        qotrt_xfree(b->tmp.p, b->tmp.cap);
    b->tmp.p = nullptr;
}

// 值相等（arr_index_of/remove/unique/eq 用）。口径 = 语言 `==` 的值语义：
// STR 比字节；NUM/NUM 比数值；NUM/BOOL 混合按真值（op11 定价：2 == true）；
// null/none 互等；其余（容器/闭包/err）不参与值相等 → false。
static bool qotrt_veq(V16 *a, V16 *b)
{
    bool an = (a->t == VT_NULL || a->t == VT_NONE);
    bool bn = (b->t == VT_NULL || b->t == VT_NONE);
    if (an && bn)
        return true;
    if (a->t == VT_STR && b->t == VT_STR)
    {
        QStr *x = static_cast<QStr *>(a->u.p);
        QStr *y = static_cast<QStr *>(b->u.p);
        return x->len == y->len &&
               (x->len == 0 || std::memcmp(x->s, y->s, static_cast<size_t>(x->len)) == 0);
    }
    bool anum = (a->t == VT_NUM);
    bool bnum = (b->t == VT_NUM);
    if (anum && bnum)
        return a->u.d == b->u.d;
    if ((anum || a->t == VT_BOOL) && (bnum || b->t == VT_BOOL))
        return (a->u.d != 0) == (b->u.d != 0);
    // M4：对象相等 = 引用同一性（同一实例）。interp 的 QotObject 默认
    // __eq__ 也是同一性 —— 两侧同律；值语义相等（逐字段）留给后续决策。
    if (a->t == VT_OBJ && b->t == VT_OBJ)
        return a->u.p == b->u.p;
    return false;
}

// ---- UTF-8 码点助手（char_* 组；非法字节 = 1 个字符，逐字节跳过）----

static bool qrt_utf8_valid(const char *s, size_t len)
{
    size_t i = 0;
    while (i < len)
    {
        unsigned char c = static_cast<unsigned char>(s[i]);
        if (c < 0x80)
        {
            i++;
            continue;
        }
        int n;
        uint32_t cp;
        if ((c & 0xE0) == 0xC0)
        {
            n = 1;
            cp = c & 0x1F;
        }
        else if ((c & 0xF0) == 0xE0)
        {
            n = 2;
            cp = c & 0x0F;
        }
        else if ((c & 0xF8) == 0xF0)
        {
            n = 3;
            cp = c & 0x07;
        }
        else
            return false; // 0x80-0xBF 前导或 0xF8+ 非法
        for (int k = 1; k <= n; k++)
        {
            if (i + static_cast<size_t>(k) >= len)
                return false; // 截断序列
            unsigned char cc = static_cast<unsigned char>(s[i + static_cast<size_t>(k)]);
            if ((cc & 0xC0) != 0x80)
                return false; // 续字节非法
            cp = (cp << 6) | (cc & 0x3F);
        }
        // 过约束编码/代理区/超 U+10FFFF
        if (n == 1 && cp < 0x80)
            return false;
        if (n == 2 && cp < 0x800)
            return false;
        if (n == 3 && (cp < 0x800 || (cp >= 0xD800 && cp <= 0xDFFF)))
            return false;
        if (n == 4 && (cp < 0x10000 || cp > 0x10FFFF))
            return false;
        i += static_cast<size_t>(n) + 1;
    }
    return true;
}

// 第 i 个码点的起始字节下标；越界返回 len+1 哨兵
static size_t qrt_char_start(const char *s, size_t len, int64_t ci)
{
    size_t i = 0;
    int64_t k = 0;
    while (i < len)
    {
        if (k == ci)
            return i;
        unsigned char c = static_cast<unsigned char>(s[i]);
        size_t step = 1;
        if ((c & 0xE0) == 0xC0)
            step = 2;
        else if ((c & 0xF0) == 0xE0)
            step = 3;
        else if ((c & 0xF8) == 0xF0)
            step = 4;
        if (i + step > len)
            step = 1; // 截断序列按 1 字节走（非法字节 = 1 字符口径）
        i += step;
        k++;
    }
    return (k == ci) ? i : static_cast<size_t>(-1);
}

static size_t qrt_char_count(const char *s, size_t len)
{
    size_t i = 0, n = 0;
    while (i < len)
    {
        unsigned char c = static_cast<unsigned char>(s[i]);
        size_t step = 1;
        if ((c & 0xE0) == 0xC0)
            step = 2;
        else if ((c & 0xF0) == 0xE0)
            step = 3;
        else if ((c & 0xF8) == 0xF0)
            step = 4;
        if (i + step > len)
            step = 1;
        i += step;
        n++;
    }
    return n;
}

// ---- 文件句柄表（句柄 = 1 起的槽号；0 无效）----
// 槽内容 = {FILE*, 世代}。close 后槽进空闲链但世代 +1 → 旧句柄数字立即失效
// （杜绝「close 后旧句柄数字摸到新文件」的复用错配；R15 句柄约定）。
struct QrtFileSlot
{
    FILE *f;
    uint32_t gen;
    int32_t next_free;
};

#ifdef QOT_RT_SPLIT
extern QrtFileSlot *qotrt_ftab;
#else
static QrtFileSlot *qotrt_ftab = nullptr;
#endif
#ifdef QOT_RT_SPLIT
extern int32_t qotrt_ftab_cap;
#else
static int32_t qotrt_ftab_cap = 0;
#endif
#ifdef QOT_RT_SPLIT
extern int32_t qotrt_ftab_free;
#else
static int32_t qotrt_ftab_free = -1;
#endif

static int32_t qotrt_file_put(FILE *f)
{
    if (qotrt_ftab_free >= 0)
    {
        int32_t h = qotrt_ftab_free;
        qotrt_ftab_free = qotrt_ftab[h].next_free;
        qotrt_ftab[h].f = f;
        return h;
    }
    if (qotrt_ftab_cap == 0)
    {
        qotrt_ftab_cap = 16;
        qotrt_ftab = static_cast<QrtFileSlot *>(
            qotrt_xalloc(sizeof(QrtFileSlot) * static_cast<size_t>(qotrt_ftab_cap)));
        for (int32_t i = 0; i < qotrt_ftab_cap; i++)
        {
            qotrt_ftab[i].f = nullptr;
            qotrt_ftab[i].gen = 0;
            qotrt_ftab[i].next_free = (i == qotrt_ftab_cap - 1) ? -1 : i + 1;
        }
        qotrt_ftab_free = 1; // 槽 0 恒无效（句柄 0 = 无效哨兵）
        int32_t h = qotrt_ftab_free;
        qotrt_ftab_free = qotrt_ftab[h].next_free;
        qotrt_ftab[h].f = f;
        return h;
    }
    int32_t old_cap = qotrt_ftab_cap;
    int32_t nc = qotrt_ftab_cap * 2;
    QrtFileSlot *nt = static_cast<QrtFileSlot *>(
        qotrt_xalloc(sizeof(QrtFileSlot) * static_cast<size_t>(nc)));
    std::memcpy(nt, qotrt_ftab, sizeof(QrtFileSlot) * static_cast<size_t>(old_cap));
    qotrt_xfree(qotrt_ftab, sizeof(QrtFileSlot) * static_cast<size_t>(old_cap));
    for (int32_t i = old_cap; i < nc; i++)
    {
        nt[i].f = nullptr;
        nt[i].gen = 0;
        nt[i].next_free = (i == nc - 1) ? -1 : i + 1;
    }
    qotrt_ftab = nt;
    qotrt_ftab_cap = nc;
    int32_t h = old_cap;
    qotrt_ftab_free = nt[h].next_free;
    nt[h].f = f;
    return h;
}

// 取活跃 FILE*；句柄无效/已关 → nullptr
static FILE *qotrt_file_of(V16 *v)
{
    if (v->t != VT_NUM)
        return nullptr;
    double d = v->u.d;
    if (!(d >= 1.0 && d < 2147483648.0) || d != static_cast<double>(static_cast<int64_t>(d)))
        return nullptr;
    int32_t h = static_cast<int32_t>(static_cast<int64_t>(d));
    if (h >= qotrt_ftab_cap)
        return nullptr;
    return qotrt_ftab[h].f;
}

static bool qotrt_file_close_h(int32_t h)
{
    if (h < 1 || h >= qotrt_ftab_cap || qotrt_ftab[h].f == nullptr)
        return false;
    std::fclose(qotrt_ftab[h].f);
    qotrt_ftab[h].f = nullptr;
    qotrt_ftab[h].gen++;
    qotrt_ftab[h].next_free = qotrt_ftab_free;
    qotrt_ftab_free = h;
    return true;
}

// ---- 平台层（Windows: W 系 API + UTF-8 边界转换 / POSIX: 直用 UTF-8）----
#if defined(_WIN32)
#define WIN32_LEAN_AND_MEAN
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#include <io.h>
#include <direct.h>
#include <sys/stat.h>

// UTF-8 → UTF-16。分配尺寸经 out_wchars 精确回传（xfree 必须带原分配尺寸
// 的池纪律），释放用 qrt_u16_free。
static wchar_t *qrt_u16(const char *s, int32_t len, int32_t *out_wchars)
{
    int n = MultiByteToWideChar(CP_UTF8, 0, s, len, nullptr, 0);
    if (n <= 0)
        return nullptr;
    wchar_t *w = static_cast<wchar_t *>(
        qotrt_xalloc(sizeof(wchar_t) * static_cast<size_t>(n + 1)));
    MultiByteToWideChar(CP_UTF8, 0, s, len, w, n);
    w[n] = 0;
    if (out_wchars)
        *out_wchars = n;
    return w;
}
static void qrt_u16_free(wchar_t *w, int32_t wchars)
{
    if (w)
        qotrt_xfree(w, sizeof(wchar_t) * static_cast<size_t>(wchars + 1));
}
static FILE *qrt_fopen(const char *path, int32_t plen, const char *mode)
{
    int32_t wn = 0;
    wchar_t *wp = qrt_u16(path, plen, &wn);
    if (!wp)
        return nullptr;
    wchar_t wm[8];
    int mn = 0;
    for (; mode[mn] && mn < 7; mn++)
        wm[mn] = static_cast<wchar_t>(mode[mn]);
    wm[mn] = 0;
    FILE *f = _wfopen(wp, wm);
    qrt_u16_free(wp, wn);
    return f;
}
static int qrt_remove(const char *path, int32_t plen)
{
    int32_t wn = 0;
    wchar_t *wp = qrt_u16(path, plen, &wn);
    if (!wp)
        return -1;
    int r = _wremove(wp);
    qrt_u16_free(wp, wn);
    return r;
}
static int qrt_rename(const char *a, int32_t al, const char *b, int32_t bl)
{
    int32_t wan = 0, wbn = 0;
    wchar_t *wa = qrt_u16(a, al, &wan);
    wchar_t *wb = qrt_u16(b, bl, &wbn);
    if (!wa || !wb)
    {
        qrt_u16_free(wa, wan);
        qrt_u16_free(wb, wbn);
        return -1;
    }
    int r = _wrename(wa, wb);
    qrt_u16_free(wa, wan);
    qrt_u16_free(wb, wbn);
    return r;
}
static bool qrt_copy(const char *a, int32_t al, const char *b, int32_t bl)
{
    int32_t wan = 0, wbn = 0;
    wchar_t *wa = qrt_u16(a, al, &wan);
    wchar_t *wb = qrt_u16(b, bl, &wbn);
    if (!wa || !wb)
    {
        qrt_u16_free(wa, wan);
        qrt_u16_free(wb, wbn);
        return false;
    }
    bool ok = CopyFileW(wa, wb, FALSE) != 0;
    qrt_u16_free(wa, wan);
    qrt_u16_free(wb, wbn);
    return ok;
}
static bool qrt_mkdir1(const char *path, int32_t plen)
{
    int32_t wn = 0;
    wchar_t *wp = qrt_u16(path, plen, &wn);
    if (!wp)
        return false;
    bool ok = _wmkdir(wp) == 0;
    qrt_u16_free(wp, wn);
    return ok;
}
static bool qrt_rmdir(const char *path, int32_t plen)
{
    int32_t wn = 0;
    wchar_t *wp = qrt_u16(path, plen, &wn);
    if (!wp)
        return false;
    bool ok = _wrmdir(wp) == 0;
    qrt_u16_free(wp, wn);
    return ok;
}
struct QrtStat
{
    int64_t size;
    int64_t mtime;
    bool is_dir;
    bool is_file;
    bool readonly;
    bool ok;
};
static bool qrt_stat(const char *path, int32_t plen, QrtStat *out)
{
    int32_t wn = 0;
    wchar_t *wp = qrt_u16(path, plen, &wn);
    if (!wp)
        return false;
    struct __stat64 st;
    bool ok = _wstat64(wp, &st) == 0;
    if (ok)
    {
        out->size = st.st_size;
        out->mtime = static_cast<int64_t>(st.st_mtime);
        out->is_dir = (st.st_mode & _S_IFMT) == _S_IFDIR;
        out->is_file = (st.st_mode & _S_IFMT) == _S_IFREG;
        out->readonly = false;
    }
    DWORD attr = GetFileAttributesW(wp);
    if (attr != INVALID_FILE_ATTRIBUTES)
    {
        out->readonly = (attr & FILE_ATTRIBUTE_READONLY) != 0;
        out->is_dir = (attr & FILE_ATTRIBUTE_DIRECTORY) != 0;
        out->is_file = !out->is_dir;
        ok = true;
    }
    else
    {
        ok = false;
    }
    qrt_u16_free(wp, wn);
    return ok;
}
static bool qrt_exists(const char *path, int32_t plen)
{
    int32_t wn = 0;
    wchar_t *wp = qrt_u16(path, plen, &wn);
    if (!wp)
        return false;
    DWORD attr = GetFileAttributesW(wp);
    qrt_u16_free(wp, wn);
    return attr != INVALID_FILE_ATTRIBUTES;
}
static bool qrt_truncate(FILE *f, int64_t len)
{
    return _chsize_s(_fileno(f), len) == 0;
}
static void qrt_sync(FILE *f)
{
    _commit(_fileno(f));
}
static bool qrt_lock(FILE *f, const char *mode, int32_t mlen)
{
    HANDLE h = reinterpret_cast<HANDLE>(_get_osfhandle(_fileno(f)));
    if (h == INVALID_HANDLE_VALUE)
        return false;
    bool unlock = (mlen >= 4 && std::memcmp(mode, "none", 4) == 0) || mlen == 0;
    if (unlock)
        return UnlockFile(h, 0, 0, 0x7FFFFFFF, 0) != 0;
    bool shared = (mlen == 6 && std::memcmp(mode, "shared", 6) == 0);
    OVERLAPPED ov;
    std::memset(&ov, 0, sizeof(ov));
    DWORD flags = shared ? LOCKFILE_FAIL_IMMEDIATELY : (LOCKFILE_EXCLUSIVE_LOCK | LOCKFILE_FAIL_IMMEDIATELY);
    return LockFileEx(h, flags, 0, 0x7FFFFFFF, 0, &ov) != 0;
}
#else
#include <dirent.h>
#include <unistd.h>
#include <sys/stat.h>
#include <sys/types.h>
static FILE *qrt_fopen(const char *path, int32_t plen, const char *mode)
{
    (void)plen;
    return std::fopen(path, mode);
}
static int qrt_remove(const char *path, int32_t plen)
{
    (void)plen;
    return std::remove(path);
}
static int qrt_rename(const char *a, int32_t al, const char *b, int32_t bl)
{
    (void)al;
    (void)bl;
    return std::rename(a, b);
}
static bool qrt_copy(const char *a, int32_t al, const char *b, int32_t bl)
{
    (void)al;
    (void)bl;
    FILE *fa = std::fopen(a, "rb");
    if (!fa)
        return false;
    FILE *fb = std::fopen(b, "wb");
    if (!fb)
    {
        std::fclose(fa);
        return false;
    }
    char buf[65536];
    size_t n;
    bool ok = true;
    while ((n = std::fread(buf, 1, sizeof(buf), fa)) > 0)
    {
        if (std::fwrite(buf, 1, n, fb) != n)
        {
            ok = false;
            break;
        }
    }
    std::fclose(fa);
    std::fclose(fb);
    return ok;
}
static bool qrt_mkdir1(const char *path, int32_t plen)
{
    (void)plen;
    return mkdir(path, 0777) == 0;
}
static bool qrt_rmdir(const char *path, int32_t plen)
{
    (void)plen;
    return rmdir(path) == 0;
}
struct QrtStat
{
    int64_t size;
    int64_t mtime;
    bool is_dir;
    bool is_file;
    bool readonly;
    bool ok;
};
static bool qrt_stat(const char *path, int32_t plen, QrtStat *out)
{
    (void)plen;
    struct stat st;
    if (stat(path, &st) != 0)
        return false;
    out->size = static_cast<int64_t>(st.st_size);
    out->mtime = static_cast<int64_t>(st.st_mtime);
    out->is_dir = S_ISDIR(st.st_mode);
    out->is_file = S_ISREG(st.st_mode);
    out->readonly = access(path, W_OK) != 0;
    return true;
}
static bool qrt_exists(const char *path, int32_t plen)
{
    (void)plen;
    struct stat st;
    return stat(path, &st) == 0;
}
static bool qrt_truncate(FILE *f, int64_t len)
{
    return ftruncate(fileno(f), static_cast<off_t>(len)) == 0;
}
static void qrt_sync(FILE *f)
{
    fsync(fileno(f));
}
static bool qrt_lock(FILE *f, const char *mode, int32_t mlen)
{
    int op;
    if (mlen == 6 && std::memcmp(mode, "shared", 6) == 0)
        op = LOCK_SH;
    else if (mlen == 9 && std::memcmp(mode, "exclusive", 9) == 0)
        op = LOCK_EX;
    else
        op = LOCK_UN;
    return flock(fileno(f), op) == 0;
}
#endif

// mode 串 → fopen 模式（一律字节语义）。非法 → nullptr。
static const char *qrt_fmode(const char *m, int32_t len)
{
    if (len <= 0)
        return nullptr;
    bool bin = false;
    int32_t k = (len >= 1 && (m[len - 1] == 'b' || m[len - 1] == 'B')) ? len - 1 : len;
    if (k != len)
        bin = true;
    char c0 = m[0];
    if (k == 2 && c0 == 'r' && m[1] == 'w')
        return bin ? "r+b" : "r+";
    if (k == 1 && c0 == 'r')
        return "rb"; // 字节语义：读也按二进制（换行不翻译，§8）
    if (k == 1 && c0 == 'w')
        return "wb";
    if (k == 1 && c0 == 'a')
        return "ab";
    return nullptr;
}

static V16 qotrt_file_open(V16 *args, int argc)
{
    if (argc < 2 || args[0].t != VT_STR)
        return qotrt_null_v();
    QStr *ps = static_cast<QStr *>(args[0].u.p);
    QrtBytes mb;
    qotrt_prim_bytes(&args[1], &mb);
    const char *md = qrt_fmode(mb.p, mb.len);
    qotrt_prim_bytes_free(&mb);
    if (!md)
        return qotrt_null_v();
    FILE *f = qrt_fopen(ps->s, ps->len, md);
    if (!f)
        return qotrt_null_v();
    return qotrt_num(static_cast<double>(qotrt_file_put(f)));
}

static V16 qotrt_file_close(V16 *args, int argc)
{
    if (argc < 1 || args[0].t != VT_NUM)
        return qotrt_bool(false);
    double d = args[0].u.d;
    if (!(d >= 1.0 && d < 2147483648.0) || d != static_cast<double>(static_cast<int64_t>(d)))
        return qotrt_bool(false);
    return qotrt_bool(qotrt_file_close_h(static_cast<int32_t>(static_cast<int64_t>(d))));
}

static V16 qotrt_handle_valid(V16 *args, int argc)
{
    if (argc < 1)
        return qotrt_bool(false);
    return qotrt_bool(qotrt_file_of(&args[0]) != nullptr);
}

static V16 qotrt_file_read(V16 *args, int argc)
{
    if (argc < 2)
        return qotrt_str("", 0);
    FILE *f = qotrt_file_of(&args[0]);
    if (!f)
        return qotrt_str("", 0);
    bool ok;
    int64_t want = qotrt_prim_i64(&args[1], &ok);
    if (!ok || want <= 0)
        return qotrt_str("", 0);
    if (want > 0x7FFFFF00LL)
        want = 0x7FFFFF00LL;
    size_t total = static_cast<size_t>(want);
    char *buf = static_cast<char *>(qotrt_xalloc(total + 1));
    size_t got = std::fread(buf, 1, total, f);
    buf[got] = 0;
    // 读取缓冲直接成为 QStr 的 s（合并分配形态：缓冲带 NUL；不变式保证
    // 永不释放/不再写 → 无悬挂面）。
    QStr *q = static_cast<QStr *>(qotrt_xalloc(sizeof(QStr)));
    q->len = static_cast<int32_t>(got);
    q->cap = static_cast<int32_t>(total);
    q->hash = 0;
    q->shared = 0;
    q->s = buf;
    V16 v;
    v.t = VT_STR;
    v.u.p = q;
    return v;
}

static V16 qotrt_file_read_all(V16 *args, int argc)
{
    if (argc < 1)
        return qotrt_str("", 0);
    FILE *f = qotrt_file_of(&args[0]);
    if (!f)
        return qotrt_str("", 0);
    QBuf b = {nullptr, 0, 0};
    char chunk[65536];
    size_t got;
    while ((got = std::fread(chunk, 1, sizeof(chunk), f)) > 0)
    {
        qbuf_need(&b, static_cast<size_t>(got));
        qbuf_append(&b, chunk, got);
    }
    return qotrt_str_from_qbuf(&b);
}

static V16 qotrt_file_write(V16 *args, int argc)
{
    if (argc < 2)
        return qotrt_num(-1.0);
    FILE *f = qotrt_file_of(&args[0]);
    if (!f)
        return qotrt_num(-1.0);
    QrtBytes b;
    qotrt_prim_bytes(&args[1], &b);
    size_t n = b.len > 0 ? std::fwrite(b.p, 1, static_cast<size_t>(b.len), f) : 0;
    qotrt_prim_bytes_free(&b);
    return qotrt_num(static_cast<double>(static_cast<int64_t>(n)));
}

static int qrt_fseek_off(FILE *f, int64_t off, int whence); // 定义在 seek 原语旁
static int64_t qrt_ftell_off(FILE *f);

static V16 qotrt_file_write_at(V16 *args, int argc)
{
    if (argc < 3)
        return qotrt_num(-1.0);
    FILE *f = qotrt_file_of(&args[0]);
    if (!f)
        return qotrt_num(-1.0);
    bool ok;
    int64_t off = qotrt_prim_i64(&args[1], &ok);
    if (!ok || off < 0)
        return qotrt_num(-1.0);
    if (qrt_fseek_off(f, off, SEEK_SET) != 0)
        return qotrt_num(-1.0);
    QrtBytes b;
    qotrt_prim_bytes(&args[2], &b);
    size_t n = b.len > 0 ? std::fwrite(b.p, 1, static_cast<size_t>(b.len), f) : 0;
    qotrt_prim_bytes_free(&b);
    return qotrt_num(static_cast<double>(static_cast<int64_t>(n)));
}

static int qrt_fseek_off(FILE *f, int64_t off, int whence)
{
#if defined(_WIN32)
    return _fseeki64(f, off, SEEK_SET);
#else
    return fseeko(f, static_cast<off_t>(off), SEEK_SET);
#endif
}

static int64_t qrt_ftell_off(FILE *f)
{
#if defined(_WIN32)
    return _ftelli64(f);
#else
    return static_cast<int64_t>(ftello(f));
#endif
}

static V16 qotrt_file_seek(V16 *args, int argc)
{
    if (argc < 3)
        return qotrt_num(-1.0);
    FILE *f = qotrt_file_of(&args[0]);
    if (!f)
        return qotrt_num(-1.0);
    bool ok;
    int64_t off = qotrt_prim_i64(&args[1], &ok);
    if (!ok)
        return qotrt_num(-1.0);
    int64_t w = qotrt_prim_i64(&args[2], &ok);
    if (!ok || w < 0 || w > 2)
        return qotrt_num(-1.0);
    int whence = (w == 0) ? SEEK_SET : ((w == 1) ? SEEK_CUR : SEEK_END);
#if defined(_WIN32)
    if (_fseeki64(f, off, whence) != 0)
        return qotrt_num(-1.0);
#else
    if (fseeko(f, static_cast<off_t>(off), whence) != 0)
        return qotrt_num(-1.0);
#endif
    return qotrt_num(static_cast<double>(qrt_ftell_off(f)));
}

static V16 qotrt_file_tell(V16 *args, int argc)
{
    if (argc < 1)
        return qotrt_num(-1.0);
    FILE *f = qotrt_file_of(&args[0]);
    if (!f)
        return qotrt_num(-1.0);
    int64_t p = qrt_ftell_off(f);
    if (p < 0)
        return qotrt_num(-1.0);
    return qotrt_num(static_cast<double>(p));
}

static V16 qotrt_file_size(V16 *args, int argc)
{
    if (argc < 1)
        return qotrt_num(-1.0);
    FILE *f = qotrt_file_of(&args[0]);
    if (!f)
        return qotrt_num(-1.0);
    int64_t cur = qrt_ftell_off(f);
    if (qrt_fseek_off(f, 0, SEEK_END) != 0)
        return qotrt_num(-1.0);
    int64_t end = qrt_ftell_off(f);
    if (cur >= 0)
        qrt_fseek_off(f, cur, SEEK_SET); // 恢复偏移（语义：只读不改位）
    return qotrt_num(static_cast<double>(end));
}

static V16 qotrt_file_flush(V16 *args, int argc)
{
    if (argc < 1)
        return qotrt_bool(false);
    FILE *f = qotrt_file_of(&args[0]);
    if (!f)
        return qotrt_bool(false);
    return qotrt_bool(std::fflush(f) == 0);
}

static V16 qotrt_file_truncate(V16 *args, int argc)
{
    if (argc < 2)
        return qotrt_bool(false);
    FILE *f = qotrt_file_of(&args[0]);
    if (!f)
        return qotrt_bool(false);
    bool ok;
    int64_t len = qotrt_prim_i64(&args[1], &ok);
    if (!ok || len < 0)
        return qotrt_bool(false);
    return qotrt_bool(qrt_truncate(f, len));
}

static V16 qotrt_file_stat(V16 *args, int argc)
{
    if (argc < 1)
        return qotrt_null_v();
    QrtBytes b;
    qotrt_prim_bytes(&args[0], &b);
    QrtStat st;
    bool ok = qrt_stat(b.p, b.len, &st);
    qotrt_prim_bytes_free(&b);
    if (!ok)
        return qotrt_null_v();
    V16 d = qotrt_dict_new();
    QDict *dict = static_cast<QDict *>(d.u.p);
    struct
    {
        const char *k;
        V16 v;
    } fields[5] = {
        {"size", qotrt_num(static_cast<double>(st.size))},
        {"mtime", qotrt_num(static_cast<double>(st.mtime))},
        {"is_dir", qotrt_bool(st.is_dir)},
        {"is_file", qotrt_bool(st.is_file)},
        {"readonly", qotrt_bool(st.readonly)},
    };
    for (int i = 0; i < 5; i++)
        *qotrt_dict_at(dict, fields[i].k, static_cast<int32_t>(std::strlen(fields[i].k)),
                       qotrt_hash(fields[i].k, std::strlen(fields[i].k))) = fields[i].v;
    return d;
}

static V16 qotrt_file_exists(V16 *args, int argc)
{
    if (argc < 1)
        return qotrt_bool(false);
    QrtBytes b;
    qotrt_prim_bytes(&args[0], &b);
    bool ok = qrt_exists(b.p, b.len);
    qotrt_prim_bytes_free(&b);
    return qotrt_bool(ok);
}

static V16 qotrt_file_remove(V16 *args, int argc)
{
    if (argc < 1)
        return qotrt_bool(false);
    QrtBytes b;
    qotrt_prim_bytes(&args[0], &b);
    int r = qrt_remove(b.p, b.len);
    qotrt_prim_bytes_free(&b);
    return qotrt_bool(r == 0);
}

static V16 qotrt_file_rename(V16 *args, int argc)
{
    if (argc < 2)
        return qotrt_bool(false);
    QrtBytes a;
    QrtBytes b;
    qotrt_prim_bytes(&args[0], &a);
    qotrt_prim_bytes(&args[1], &b);
    int r = qrt_rename(a.p, a.len, b.p, b.len);
    qotrt_prim_bytes_free(&a);
    qotrt_prim_bytes_free(&b);
    return qotrt_bool(r == 0);
}

static V16 qotrt_file_copy(V16 *args, int argc)
{
    if (argc < 2)
        return qotrt_bool(false);
    QrtBytes a;
    QrtBytes b;
    qotrt_prim_bytes(&args[0], &a);
    qotrt_prim_bytes(&args[1], &b);
    bool ok = qrt_copy(a.p, a.len, b.p, b.len);
    qotrt_prim_bytes_free(&a);
    qotrt_prim_bytes_free(&b);
    return qotrt_bool(ok);
}

static V16 qotrt_file_mtime(V16 *args, int argc)
{
    if (argc < 1)
        return qotrt_num(-1.0);
    QrtBytes b;
    qotrt_prim_bytes(&args[0], &b);
    QrtStat st;
    bool ok = qrt_stat(b.p, b.len, &st);
    qotrt_prim_bytes_free(&b);
    if (!ok)
        return qotrt_num(-1.0);
    return qotrt_num(static_cast<double>(st.mtime));
}

static V16 qotrt_file_lock(V16 *args, int argc)
{
    if (argc < 2)
        return qotrt_bool(false);
    FILE *f = qotrt_file_of(&args[0]);
    if (!f)
        return qotrt_bool(false);
    QrtBytes b;
    qotrt_prim_bytes(&args[1], &b);
    bool ok = qrt_lock(f, b.p, b.len);
    qotrt_prim_bytes_free(&b);
    return qotrt_bool(ok);
}

static V16 qotrt_file_sync(V16 *args, int argc)
{
    if (argc < 1)
        return qotrt_bool(false);
    FILE *f = qotrt_file_of(&args[0]);
    if (!f)
        return qotrt_bool(false);
    std::fflush(f);
    qrt_sync(f);
    return qotrt_bool(true);
}

// ---- P0-A 路径原语（纯字节算法；分隔符 / 与 \ 都收，输出统一 /）----

static bool qrt_is_sep(char c)
{
    return c == '/' || c == '\\';
}

// 规范化：分隔符统一 /，折叠重复，解析 . / ..（词法级，不碰文件系统）。
// 保留盘符（C:/）与开头的 /；.. 超出根时保留（语义：不假装能越过根）。
static V16 qotrt_path_normalize_impl(const char *s, size_t len)
{
    if (len == 0)
        return qotrt_str("", 0);
    QBuf b = {nullptr, 0, 0};
    size_t i = 0;
    // 前缀：盘符或 UNC（//）或根 /
    bool started = false;
    if (len >= 2 && s[1] == ':' &&
        ((s[0] >= 'a' && s[0] <= 'z') || (s[0] >= 'A' && s[0] <= 'Z')))
    {
        char drv[2] = {s[0], ':'};
        qbuf_append(&b, drv, 2);
        i = 2;
        started = true;
    }
    else if (len >= 2 && qrt_is_sep(s[0]) && qrt_is_sep(s[1]))
    {
        qbuf_append(&b, "//", 2);
        i = 2;
        started = true;
    }
    else if (len > 0 && qrt_is_sep(s[0]))
    {
        qbuf_append(&b, "/", 1);
        i = 1;
        started = true;
    }
    // 段处理栈式：段数组直接用 qbuf 存最终形式，遇 .. 回退
    // 简化实现：先拷贝到一个段表再重组（段数 ≤ len）
    size_t max_segs = len / 1 + 2;
    char **segs = static_cast<char **>(qotrt_xalloc(sizeof(char *) * max_segs));
    size_t *seg_len = static_cast<size_t *>(qotrt_xalloc(sizeof(size_t) * max_segs));
    bool *drop = static_cast<bool *>(qotrt_xalloc(sizeof(bool) * max_segs));
    size_t nseg = 0;
    while (i <= len)
    {
        size_t j = i;
        while (j < len && !qrt_is_sep(s[j]))
            j++;
        size_t sl = j - i;
        if (sl == 1 && s[i] == '.')
        {
            // 跳过
        }
        else if (sl == 2 && s[i] == '.' && s[i + 1] == '.')
        {
            bool removed = false;
            for (size_t k = nseg; k > 0; k--)
            {
                size_t kk = k - 1;
                if (!drop[kk])
                {
                    drop[kk] = true; // 词法回退（不真删，标脏保持容量）
                    removed = true;
                    break;
                }
            }
            if (!removed && started && nseg > 0 && !(b.len >= 2 && b.p[0] == '/' && b.p[1] == '/'))
            {
                // 越根的 ..：根锚定形式下丢弃（/.. == /）
            }
            else if (!removed && !started)
            {
                // 相对路径的 .. 无处可退 → 保留
                segs[nseg] = const_cast<char *>("..");
                seg_len[nseg] = 2;
                drop[nseg] = false;
                nseg++;
            }
        }
        else if (sl > 0)
        {
            segs[nseg] = const_cast<char *>(s + i);
            seg_len[nseg] = sl;
            drop[nseg] = false;
            nseg++;
        }
        if (j >= len)
            break;
        i = j + 1;
    }
    bool first = true;
    for (size_t k = 0; k < nseg; k++)
    {
        if (drop[k])
            continue;
        // 分隔符规则：输出缓冲非空且不以 / 结尾时才补（锚头 "/"、"C:"、
        // "//" 各自决定首段前是否已有分隔 —— 旧式 `!first || started` 对
        // 锚头 "/" 会产出 "//b"）。
        if (b.len > 0 && b.p[b.len - 1] != '/')
            qbuf_append(&b, "/", 1);
        first = false;
        qbuf_append(&b, segs[k], seg_len[k]);
    }
    (void)first;
    (void)started;
    qotrt_xfree(segs, sizeof(char *) * max_segs);
    qotrt_xfree(seg_len, sizeof(size_t) * max_segs);
    qotrt_xfree(drop, sizeof(bool) * max_segs);
    return qotrt_str_from_qbuf(&b);
}

static V16 qotrt_path_join(V16 *args, int argc)
{
    if (argc < 1)
        return qotrt_str("", 0);
    QBuf b = {nullptr, 0, 0};
    for (int k = 0; k < argc; k++)
    {
        QrtBytes sb;
        qotrt_prim_bytes(&args[k], &sb);
        if (sb.len > 0)
        {
            if (b.len > 0 && !qrt_is_sep(b.p[b.len - 1]) && !qrt_is_sep(sb.p[0]))
                qbuf_append(&b, "/", 1);
            qbuf_append(&b, sb.p, static_cast<size_t>(sb.len));
        }
        qotrt_prim_bytes_free(&sb);
    }
    // join 后按 §8 约定走一次规范化（分隔符统一、重复折叠；不做 .. 解析以
    // 免改变用户拼接意图 —— 规范化的 .. 解析由 path_normalize 承担）。
    // 这里只做轻量归一：分隔符 + 重复斜杠。
    size_t w = 0;
    for (size_t r = 0; r < b.len; r++)
    {
        char c = b.p[r];
        if (qrt_is_sep(c))
            c = '/';
        if (c == '/' && w > 0 && b.p[w - 1] == '/')
            continue;
        b.p[w++] = c;
    }
    b.len = w;
    if (b.p)
        b.p[w] = 0;
    return qotrt_str_from_qbuf(&b);
}

// 尾部分隔符剥离（basename/dirname 共用；根 "/" 剥完为空）
static size_t qrt_path_trim_tail(const char *s, size_t len)
{
    while (len > 1 && qrt_is_sep(s[len - 1]))
        len--;
    if (len == 1 && qrt_is_sep(s[0]))
        return 0;
    return len;
}

static V16 qotrt_path_basename(V16 *args, int argc)
{
    if (argc < 1)
        return qotrt_str("", 0);
    QrtBytes b;
    qotrt_prim_bytes(&args[0], &b);
    size_t len = qrt_path_trim_tail(b.p, static_cast<size_t>(b.len));
    if (len == 0)
    {
        qotrt_prim_bytes_free(&b);
        // 根路径 "/" 的 basename = "/"（posixpath 约定）
        if (b.len > 0 && qrt_is_sep(b.p[0]))
        {
            V16 r = qotrt_str("/", 1);
            qotrt_prim_bytes_free(&b);
            return r;
        }
        return qotrt_str("", 0);
    }
    size_t i = len;
    while (i > 0 && !qrt_is_sep(b.p[i - 1]))
        i--;
    V16 r = qotrt_str(b.p + i, static_cast<int32_t>(len - i));
    qotrt_prim_bytes_free(&b);
    return r;
}

static V16 qotrt_path_dirname(V16 *args, int argc)
{
    if (argc < 1)
        return qotrt_str("", 0);
    QrtBytes b;
    qotrt_prim_bytes(&args[0], &b);
    size_t len = qrt_path_trim_tail(b.p, static_cast<size_t>(b.len));
    if (len == 0)
    {
        qotrt_prim_bytes_free(&b);
        if (b.len > 0 && qrt_is_sep(b.p[0]))
        {
            V16 r = qotrt_str("/", 1);
            qotrt_prim_bytes_free(&b);
            return r;
        }
        return qotrt_str("", 0);
    }
    size_t i = len;
    while (i > 0 && !qrt_is_sep(b.p[i - 1]))
        i--;
    if (i == 0)
    {
        qotrt_prim_bytes_free(&b);
        return qotrt_str("", 0);
    }
    i--;                    // 指向分隔符
    while (i > 0 && qrt_is_sep(b.p[i - 1]))
        i--;                // 折叠连续分隔符
    V16 r = qotrt_str(b.p, static_cast<int32_t>(i));
    qotrt_prim_bytes_free(&b);
    return r;
}

static V16 qotrt_path_ext(V16 *args, int argc)
{
    if (argc < 1)
        return qotrt_str("", 0);
    QrtBytes b;
    qotrt_prim_bytes(&args[0], &b);
    size_t len = qrt_path_trim_tail(b.p, static_cast<size_t>(b.len));
    size_t i = len;
    while (i > 0 && !qrt_is_sep(b.p[i - 1]))
        i--;
    size_t name_start = i;
    size_t dot = static_cast<size_t>(-1);
    for (size_t k = len; k > name_start; k--)
    {
        if (b.p[k - 1] == '.')
        {
            dot = k - 1;
            break;
        }
    }
    V16 r = (dot == static_cast<size_t>(-1) || dot == name_start)
                ? qotrt_str("", 0) // 无点 / 隐藏文件（.gitignore）无扩展
                : qotrt_str(b.p + dot, static_cast<int32_t>(len - dot));
    qotrt_prim_bytes_free(&b);
    return r;
}

static V16 qotrt_path_stem(V16 *args, int argc)
{
    if (argc < 1)
        return qotrt_str("", 0);
    QrtBytes b;
    qotrt_prim_bytes(&args[0], &b);
    size_t len = qrt_path_trim_tail(b.p, static_cast<size_t>(b.len));
    size_t i = len;
    while (i > 0 && !qrt_is_sep(b.p[i - 1]))
        i--;
    size_t name_start = i;
    size_t dot = static_cast<size_t>(-1);
    for (size_t k = len; k > name_start; k--)
    {
        if (b.p[k - 1] == '.')
        {
            dot = k - 1;
            break;
        }
    }
    size_t end = (dot == static_cast<size_t>(-1) || dot == name_start) ? len : dot;
    V16 r = qotrt_str(b.p + name_start, static_cast<int32_t>(end - name_start));
    qotrt_prim_bytes_free(&b);
    return r;
}

static V16 qotrt_path_normalize(V16 *args, int argc)
{
    if (argc < 1)
        return qotrt_str("", 0);
    QrtBytes b;
    qotrt_prim_bytes(&args[0], &b);
    V16 r = qotrt_path_normalize_impl(b.p, static_cast<size_t>(b.len));
    qotrt_prim_bytes_free(&b);
    return r;
}

static V16 qotrt_path_is_absolute(V16 *args, int argc)
{
    if (argc < 1)
        return qotrt_bool(false);
    QrtBytes b;
    qotrt_prim_bytes(&args[0], &b);
    bool abs = false;
    if (b.len >= 1 && qrt_is_sep(b.p[0]))
        abs = true; // /x 或 //unc
    if (!abs && b.len >= 2 && b.p[1] == ':' &&
        ((b.p[0] >= 'a' && b.p[0] <= 'z') || (b.p[0] >= 'A' && b.p[0] <= 'Z')))
        abs = true; // 盘符
    qotrt_prim_bytes_free(&b);
    return qotrt_bool(abs);
}

static V16 qotrt_path_is_dir(V16 *args, int argc)
{
    if (argc < 1)
        return qotrt_bool(false);
    QrtBytes b;
    qotrt_prim_bytes(&args[0], &b);
    QrtStat st;
    bool ok = qrt_stat(b.p, b.len, &st) && st.is_dir;
    qotrt_prim_bytes_free(&b);
    return qotrt_bool(ok);
}

static V16 qotrt_path_is_file(V16 *args, int argc)
{
    if (argc < 1)
        return qotrt_bool(false);
    QrtBytes b;
    qotrt_prim_bytes(&args[0], &b);
    QrtStat st;
    bool ok = qrt_stat(b.p, b.len, &st) && st.is_file;
    qotrt_prim_bytes_free(&b);
    return qotrt_bool(ok);
}

// ---- 目录枚举（结果按字节序排序 = 跨平台确定序；不含 . 与 ..）----

static bool qrt_names_sort_swap(const char *a, size_t al, const char *b, size_t bl)
{
    size_t n = al < bl ? al : bl;
    int c = std::memcmp(a, b, n);
    if (c != 0)
        return c > 0;
    return al > bl;
}

static V16 qotrt_dir_list(V16 *args, int argc)
{
    if (argc < 1)
        return qotrt_null_v();
    QrtBytes b;
    qotrt_prim_bytes(&args[0], &b);
#if defined(_WIN32)
    QBuf pat = {nullptr, 0, 0};
    {
        // 目录 + "\\*"
        char last = b.len > 0 ? b.p[b.len - 1] : 0;
        qbuf_append(&pat, b.p, static_cast<size_t>(b.len));
        if (!(qrt_is_sep(last)))
            qbuf_append(&pat, "/", 1);
        qbuf_append(&pat, "*", 1);
        if (pat.p)
            pat.p[pat.len] = 0;
    }
    int32_t wn = 0;
    wchar_t *wp = qrt_u16(pat.p ? pat.p : "", static_cast<int32_t>(pat.len), &wn);
    if (pat.p)
        qotrt_xfree(pat.p, pat.cap);
    if (!wp)
    {
        qotrt_prim_bytes_free(&b);
        return qotrt_null_v();
    }
    WIN32_FIND_DATAW fd;
    HANDLE h = FindFirstFileW(wp, &fd);
    qrt_u16_free(wp, wn);
    if (h == INVALID_HANDLE_VALUE)
    {
        qotrt_prim_bytes_free(&b);
        return qotrt_null_v();
    }
    // 收集（动态数组；名字暂存 UTF-8 独占缓冲）
    int32_t cap = 16, n = 0;
    char **names = static_cast<char **>(qotrt_xalloc(sizeof(char *) * 16));
    int32_t *nlens = static_cast<int32_t *>(qotrt_xalloc(sizeof(int32_t) * 16));
    do
    {
        if (fd.cFileName[0] == L'.' &&
            (fd.cFileName[1] == 0 || (fd.cFileName[1] == L'.' && fd.cFileName[2] == 0)))
            continue;
        int wl = 0;
        while (fd.cFileName[wl])
            wl++;
        int need = WideCharToMultiByte(CP_UTF8, 0, fd.cFileName, wl, nullptr, 0, nullptr, nullptr);
        if (need <= 0)
            continue;
        if (n >= cap)
        {
            int32_t nc = cap * 2;
            char **nn = static_cast<char **>(qotrt_xalloc(sizeof(char *) * static_cast<size_t>(nc)));
            int32_t *nnl = static_cast<int32_t *>(qotrt_xalloc(sizeof(int32_t) * static_cast<size_t>(nc)));
            std::memcpy(nn, names, sizeof(char *) * static_cast<size_t>(cap));
            std::memcpy(nnl, nlens, sizeof(int32_t) * static_cast<size_t>(cap));
            qotrt_xfree(names, sizeof(char *) * static_cast<size_t>(cap));
            qotrt_xfree(nlens, sizeof(int32_t) * static_cast<size_t>(cap));
            names = nn;
            nlens = nnl;
            cap = nc;
        }
        char *nb = static_cast<char *>(qotrt_xalloc(static_cast<size_t>(need) + 1));
        WideCharToMultiByte(CP_UTF8, 0, fd.cFileName, wl, nb, need, nullptr, nullptr);
        nb[need] = 0;
        names[n] = nb;
        nlens[n] = need;
        n++;
    } while (FindNextFileW(h, &fd));
    FindClose(h);
#else
    DIR *dp = opendir(b.len > 0 ? b.p : ".");
    if (!dp)
    {
        qotrt_prim_bytes_free(&b);
        return qotrt_null_v();
    }
    int32_t cap = 16, n = 0;
    char **names = static_cast<char **>(qotrt_xalloc(sizeof(char *) * 16));
    int32_t *nlens = static_cast<int32_t *>(qotrt_xalloc(sizeof(int32_t) * 16));
    struct dirent *de;
    while ((de = readdir(dp)) != nullptr)
    {
        if (de->d_name[0] == '.' &&
            (de->d_name[1] == 0 || (de->d_name[1] == '.' && de->d_name[2] == 0)))
            continue;
        int32_t l = 0;
        while (de->d_name[l])
            l++;
        if (n >= cap)
        {
            int32_t nc = cap * 2;
            char **nn = static_cast<char **>(qotrt_xalloc(sizeof(char *) * static_cast<size_t>(nc)));
            int32_t *nnl = static_cast<int32_t *>(qotrt_xalloc(sizeof(int32_t) * static_cast<size_t>(nc)));
            std::memcpy(nn, names, sizeof(char *) * static_cast<size_t>(cap));
            std::memcpy(nnl, nlens, sizeof(int32_t) * static_cast<size_t>(cap));
            qotrt_xfree(names, sizeof(char *) * static_cast<size_t>(cap));
            qotrt_xfree(nlens, sizeof(int32_t) * static_cast<size_t>(cap));
            names = nn;
            nlens = nnl;
            cap = nc;
        }
        names[n] = qotrt_strdup(de->d_name, l);
        nlens[n] = l;
        n++;
    }
    closedir(dp);
#endif
    // 字节序插入排序（目录条目量小；确定性 = 跨引擎可对账的前提）
    for (int32_t i = 1; i < n; i++)
    {
        char *kn = names[i];
        int32_t kl = nlens[i];
        int32_t j = i - 1;
        while (j >= 0 && qrt_names_sort_swap(names[j], static_cast<size_t>(nlens[j]), kn, static_cast<size_t>(kl)))
        {
            names[j + 1] = names[j];
            nlens[j + 1] = nlens[j];
            j--;
        }
        names[j + 1] = kn;
        nlens[j + 1] = kl;
    }
    V16 out = qotrt_arr_new(n > 4 ? n : 4);
    QArr *a = static_cast<QArr *>(out.u.p);
    for (int32_t i = 0; i < n; i++)
    {
        V16 v;
        v.t = VT_STR;
        v.u.p = static_cast<QStr *>(qotrt_xalloc(sizeof(QStr)));
        QStr *q = static_cast<QStr *>(v.u.p);
        // 名字缓冲所有权直接移交 QStr（免二次拷贝；不变式：永不释放/不再写）
        q->len = nlens[i];
        q->cap = nlens[i];
        q->hash = 0;
        q->shared = 0;
        q->s = names[i];
        qotrt_arr_push(a, v);
    }
    qotrt_xfree(names, sizeof(char *) * static_cast<size_t>(cap));
    qotrt_xfree(nlens, sizeof(int32_t) * static_cast<size_t>(cap));
    qotrt_prim_bytes_free(&b);
    return out;
}

static V16 qotrt_dir_mkdir(V16 *args, int argc)
{
    if (argc < 1)
        return qotrt_bool(false);
    QrtBytes b;
    qotrt_prim_bytes(&args[0], &b);
    bool ok = qrt_mkdir1(b.p, b.len);
    qotrt_prim_bytes_free(&b);
    return qotrt_bool(ok);
}

static V16 qotrt_dir_mkdirs(V16 *args, int argc)
{
    if (argc < 1)
        return qotrt_bool(false);
    QrtBytes b;
    qotrt_prim_bytes(&args[0], &b);
    // 逐段创建（已存在不算失败；R15 §8：mkdirs 的「已存在 true」语义）
    QBuf cur = {nullptr, 0, 0};
    bool ok = true;
    size_t i = 0;
    while (i < static_cast<size_t>(b.len))
    {
        size_t j = i;
        while (j < static_cast<size_t>(b.len) && !qrt_is_sep(b.p[j]))
            j++;
        if (j > i)
        {
            if (cur.len > 0 && !qrt_is_sep(cur.p[cur.len - 1]))
                qbuf_append(&cur, "/", 1);
            qbuf_append(&cur, b.p + i, j - i);
            if (cur.p)
                cur.p[cur.len] = 0;
            if (!qrt_exists(cur.p ? cur.p : "", static_cast<int32_t>(cur.len)))
            {
                if (!qrt_mkdir1(cur.p ? cur.p : "", static_cast<int32_t>(cur.len)))
                {
                    QrtStat st;
                    if (!(qrt_stat(cur.p ? cur.p : "", static_cast<int32_t>(cur.len), &st) && st.is_dir))
                        ok = false;
                }
            }
        }
        i = j + 1;
    }
    if (cur.p)
        qotrt_xfree(cur.p, cur.cap);
    qotrt_prim_bytes_free(&b);
    return qotrt_bool(ok);
}

static V16 qotrt_dir_remove(V16 *args, int argc)
{
    if (argc < 1)
        return qotrt_bool(false);
    QrtBytes b;
    qotrt_prim_bytes(&args[0], &b);
    bool ok = qrt_rmdir(b.p, b.len);
    qotrt_prim_bytes_free(&b);
    return qotrt_bool(ok);
}

static int64_t qrt_dir_size_walk(const char *path, int32_t plen)
{
    QrtStat st;
    if (!qrt_stat(path, plen, &st))
        return -1;
    if (!st.is_dir)
        return st.size;
#if defined(_WIN32)
    QBuf pat = {nullptr, 0, 0};
    char last = plen > 0 ? path[plen - 1] : 0;
    qbuf_append(&pat, path, static_cast<size_t>(plen));
    if (!qrt_is_sep(last))
        qbuf_append(&pat, "/", 1);
    qbuf_append(&pat, "*", 1);
    if (pat.p)
        pat.p[pat.len] = 0;
    int32_t wn = 0;
    wchar_t *wp = qrt_u16(pat.p ? pat.p : "", static_cast<int32_t>(pat.len), &wn);
    if (pat.p)
        qotrt_xfree(pat.p, pat.cap);
    if (!wp)
        return -1;
    WIN32_FIND_DATAW fd;
    HANDLE h = FindFirstFileW(wp, &fd);
    qrt_u16_free(wp, wn);
    if (h == INVALID_HANDLE_VALUE)
        return -1;
    int64_t total = 0;
    do
    {
        if (fd.cFileName[0] == L'.' &&
            (fd.cFileName[1] == 0 || (fd.cFileName[1] == L'.' && fd.cFileName[2] == 0)))
            continue;
        if (fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY)
        {
            if (fd.dwFileAttributes & FILE_ATTRIBUTE_REPARSE_POINT)
                continue; // 不跟随链接（语义：物理字节）
            int wl = 0;
            while (fd.cFileName[wl])
                wl++;
            // 子路径 = path + / + name（qbuf 重组）
            QBuf sub = {nullptr, 0, 0};
            qbuf_append(&sub, path, static_cast<size_t>(plen));
            if (!qrt_is_sep(last))
                qbuf_append(&sub, "/", 1);
            int need = WideCharToMultiByte(CP_UTF8, 0, fd.cFileName, wl, nullptr, 0, nullptr, nullptr);
            if (need > 0)
            {
                char *nb = static_cast<char *>(qotrt_xalloc(static_cast<size_t>(need) + 1));
                WideCharToMultiByte(CP_UTF8, 0, fd.cFileName, wl, nb, need, nullptr, nullptr);
                nb[need] = 0;
                qbuf_append(&sub, nb, static_cast<size_t>(need));
                if (sub.p)
                    sub.p[sub.len] = 0;
                int64_t r = qrt_dir_size_walk(sub.p ? sub.p : "", static_cast<int32_t>(sub.len));
                if (r > 0)
                    total += r;
                qotrt_xfree(nb, static_cast<size_t>(need) + 1);
            }
            if (sub.p)
                qotrt_xfree(sub.p, sub.cap);
        }
        else
        {
            total += (static_cast<int64_t>(fd.nFileSizeHigh) << 32) | fd.nFileSizeLow;
        }
    } while (FindNextFileW(h, &fd));
    FindClose(h);
    return total;
#else
    DIR *dp = opendir(path);
    if (!dp)
        return -1;
    int64_t total = 0;
    struct dirent *de;
    while ((de = readdir(dp)) != nullptr)
    {
        if (de->d_name[0] == '.' &&
            (de->d_name[1] == 0 || (de->d_name[1] == '.' && de->d_name[2] == 0)))
            continue;
        int32_t l = 0;
        while (de->d_name[l])
            l++;
        QBuf sub = {nullptr, 0, 0};
        qbuf_append(&sub, path, static_cast<size_t>(plen));
        if (plen > 0 && !qrt_is_sep(path[plen - 1]))
            qbuf_append(&sub, "/", 1);
        qbuf_append(&sub, de->d_name, static_cast<size_t>(l));
        if (sub.p)
            sub.p[sub.len] = 0;
        struct stat st2;
        if (lstat(sub.p ? sub.p : "", &st2) == 0 && S_ISREG(st2.st_mode))
            total += static_cast<int64_t>(st2.st_size);
        else if (lstat(sub.p ? sub.p : "", &st2) == 0 && S_ISDIR(st2.st_mode))
        {
            int64_t r = qrt_dir_size_walk(sub.p ? sub.p : "", static_cast<int32_t>(sub.len));
            if (r > 0)
                total += r;
        }
        if (sub.p)
            qotrt_xfree(sub.p, sub.cap);
    }
    closedir(dp);
    return total;
#endif
}

static V16 qotrt_dir_size(V16 *args, int argc)
{
    if (argc < 1)
        return qotrt_num(-1.0);
    QrtBytes b;
    qotrt_prim_bytes(&args[0], &b);
    int64_t n = qrt_dir_size_walk(b.p, b.len);
    qotrt_prim_bytes_free(&b);
    if (n < 0)
        return qotrt_num(-1.0);
    return qotrt_num(static_cast<double>(n));
}

// ============================================================
// P0-B 字符串原语（字节级 + UTF-8 码点级）
// ============================================================

static V16 qrt_str_v(QStr *q)
{
    V16 v;
    v.t = VT_STR;
    v.u.p = q;
    return v;
}

static V16 qotrt_str_len(V16 *args, int argc)
{
    if (argc < 1)
        return qotrt_num(0.0);
    QrtBytes b;
    qotrt_prim_bytes(&args[0], &b);
    double n = static_cast<double>(b.len);
    qotrt_prim_bytes_free(&b);
    return qotrt_num(n);
}

static V16 qotrt_str_empty(V16 *args, int argc)
{
    if (argc < 1)
        return qotrt_bool(true);
    QrtBytes b;
    qotrt_prim_bytes(&args[0], &b);
    bool e = b.len == 0;
    qotrt_prim_bytes_free(&b);
    return qotrt_bool(e);
}

static V16 qotrt_str_slice(V16 *args, int argc)
{
    if (argc < 3)
        return qotrt_str("", 0);
    QrtBytes b;
    qotrt_prim_bytes(&args[0], &b);
    bool ok;
    int64_t from = qotrt_prim_i64(&args[1], &ok);
    int64_t to = ok ? qotrt_prim_i64(&args[2], &ok) : -1;
    if (!ok)
    {
        qotrt_prim_bytes_free(&b);
        return qotrt_str("", 0);
    }
    int64_t len = b.len;
    if (from < 0)
        from = 0;
    if (to > len)
        to = len;
    V16 r;
    if (from >= to)
        r = qotrt_str("", 0);
    else
        r = qrt_str_v(qotrt_str_new(b.p + from, static_cast<int32_t>(to - from)));
    qotrt_prim_bytes_free(&b);
    return r;
}

static V16 qotrt_str_byte_at(V16 *args, int argc)
{
    if (argc < 2)
        return qotrt_num(-1.0);
    QrtBytes b;
    qotrt_prim_bytes(&args[0], &b);
    bool ok;
    int64_t i = qotrt_prim_i64(&args[1], &ok);
    double r = (ok && i >= 0 && i < b.len)
                   ? static_cast<double>(static_cast<unsigned char>(b.p[i]))
                   : -1.0;
    qotrt_prim_bytes_free(&b);
    return qotrt_num(r);
}

static int32_t qrt_find_bytes(const char *hay, size_t hl, const char *nd, size_t nl, size_t from)
{
    if (nl == 0)
        return (from <= hl) ? static_cast<int32_t>(from) : -1;
    if (nl > hl || from > hl - nl)
        return -1;
    for (size_t i = from; i + nl <= hl; i++)
    {
        if (hay[i] == nd[0] && std::memcmp(hay + i, nd, nl) == 0)
            return static_cast<int32_t>(i);
    }
    return -1;
}

static V16 qotrt_str_find(V16 *args, int argc)
{
    if (argc < 3)
        return qotrt_num(-1.0);
    QrtBytes s;
    QrtBytes sub;
    qotrt_prim_bytes(&args[0], &s);
    qotrt_prim_bytes(&args[1], &sub);
    bool ok;
    int64_t from = qotrt_prim_i64(&args[2], &ok);
    int32_t r = -1;
    if (ok)
    {
        if (from < 0)
            from = 0;
        r = qrt_find_bytes(s.p, static_cast<size_t>(s.len), sub.p,
                           static_cast<size_t>(sub.len), static_cast<size_t>(from));
    }
    qotrt_prim_bytes_free(&s);
    qotrt_prim_bytes_free(&sub);
    return qotrt_num(static_cast<double>(r));
}

static V16 qotrt_str_rfind(V16 *args, int argc)
{
    if (argc < 2)
        return qotrt_num(-1.0);
    QrtBytes s;
    QrtBytes sub;
    qotrt_prim_bytes(&args[0], &s);
    qotrt_prim_bytes(&args[1], &sub);
    size_t hl = static_cast<size_t>(s.len), nl = static_cast<size_t>(sub.len);
    int32_t r = -1;
    if (nl == 0)
        r = static_cast<int32_t>(hl);
    else if (nl <= hl)
    {
        for (size_t i = hl - nl + 1; i-- > 0;)
        {
            if (s.p[i] == sub.p[0] && std::memcmp(s.p + i, sub.p, nl) == 0)
            {
                r = static_cast<int32_t>(i);
                break;
            }
        }
    }
    qotrt_prim_bytes_free(&s);
    qotrt_prim_bytes_free(&sub);
    return qotrt_num(static_cast<double>(r));
}

static V16 qotrt_str_contains(V16 *args, int argc)
{
    if (argc < 2)
        return qotrt_bool(false);
    QrtBytes s;
    QrtBytes sub;
    qotrt_prim_bytes(&args[0], &s);
    qotrt_prim_bytes(&args[1], &sub);
    bool r = qrt_find_bytes(s.p, static_cast<size_t>(s.len), sub.p,
                            static_cast<size_t>(sub.len), 0) >= 0;
    qotrt_prim_bytes_free(&s);
    qotrt_prim_bytes_free(&sub);
    return qotrt_bool(r);
}

static V16 qotrt_str_starts_with(V16 *args, int argc)
{
    if (argc < 2)
        return qotrt_bool(false);
    QrtBytes s;
    QrtBytes p;
    qotrt_prim_bytes(&args[0], &s);
    qotrt_prim_bytes(&args[1], &p);
    bool r = p.len <= s.len && (p.len == 0 || std::memcmp(s.p, p.p, static_cast<size_t>(p.len)) == 0);
    qotrt_prim_bytes_free(&s);
    qotrt_prim_bytes_free(&p);
    return qotrt_bool(r);
}

static V16 qotrt_str_ends_with(V16 *args, int argc)
{
    if (argc < 2)
        return qotrt_bool(false);
    QrtBytes s;
    QrtBytes p;
    qotrt_prim_bytes(&args[0], &s);
    qotrt_prim_bytes(&args[1], &p);
    bool r = p.len <= s.len && (p.len == 0 || std::memcmp(s.p + s.len - p.len, p.p, static_cast<size_t>(p.len)) == 0);
    qotrt_prim_bytes_free(&s);
    qotrt_prim_bytes_free(&p);
    return qotrt_bool(r);
}

static V16 qotrt_str_count(V16 *args, int argc)
{
    if (argc < 2)
        return qotrt_num(0.0);
    QrtBytes s;
    QrtBytes sub;
    qotrt_prim_bytes(&args[0], &s);
    qotrt_prim_bytes(&args[1], &sub);
    size_t nl = static_cast<size_t>(sub.len), hl = static_cast<size_t>(s.len);
    int64_t cnt = 0;
    if (nl > 0 && nl <= hl)
    {
        size_t i = 0;
        while (i + nl <= hl)
        {
            if (s.p[i] == sub.p[0] && std::memcmp(s.p + i, sub.p, nl) == 0)
            {
                cnt++; // 非重叠：命中后跳过整段
                i += nl;
            }
            else
                i++;
        }
    }
    qotrt_prim_bytes_free(&s);
    qotrt_prim_bytes_free(&sub);
    return qotrt_num(static_cast<double>(cnt));
}

static V16 qotrt_str_split(V16 *args, int argc)
{
    if (argc < 2 || args[0].t == VT_NULL)
        return qotrt_null_v();
    QrtBytes s;
    QrtBytes sep;
    qotrt_prim_bytes(&args[0], &s);
    qotrt_prim_bytes(&args[1], &sep);
    V16 out = qotrt_arr_new(8);
    QArr *a = static_cast<QArr *>(out.u.p);
    if (sep.len == 0)
    {
        // 空 sep：按字节切（字符串 = 字节序列的直系推论）
        for (int32_t i = 0; i < s.len; i++)
            qotrt_arr_push(a, qrt_str_v(qotrt_str_new(s.p + i, 1)));
    }
    else
    {
        size_t start = 0;
        size_t hl = static_cast<size_t>(s.len), nl = static_cast<size_t>(sep.len);
        while (true)
        {
            int32_t hit = qrt_find_bytes(s.p, hl, sep.p, nl, start);
            if (hit < 0)
                break;
            qotrt_arr_push(a, qrt_str_v(qotrt_str_new(s.p + start, static_cast<int32_t>(static_cast<size_t>(hit) - start))));
            start = static_cast<size_t>(hit) + nl;
        }
        qotrt_arr_push(a, qrt_str_v(qotrt_str_new(s.p + start, static_cast<int32_t>(hl - start))));
    }
    qotrt_prim_bytes_free(&s);
    qotrt_prim_bytes_free(&sep);
    return out;
}

static V16 qotrt_str_split_lines(V16 *args, int argc)
{
    if (argc < 1)
        return qotrt_null_v();
    QrtBytes s;
    qotrt_prim_bytes(&args[0], &s);
    V16 out = qotrt_arr_new(8);
    QArr *a = static_cast<QArr *>(out.u.p);
    // 按 \n 切行；每行剥一个行尾 \r；保留行尾空元素（"a\n" → ["a",""]）
    size_t start = 0;
    size_t hl = static_cast<size_t>(s.len);
    for (size_t i = 0; i <= hl; i++)
    {
        if (i == hl || s.p[i] == '\n')
        {
            size_t end = i;
            if (end > start && s.p[end - 1] == '\r')
                end--;
            qotrt_arr_push(a, qrt_str_v(qotrt_str_new(s.p + start, static_cast<int32_t>(end - start))));
            start = i + 1;
        }
    }
    qotrt_prim_bytes_free(&s);
    return out;
}

static V16 qotrt_str_split_n(V16 *args, int argc)
{
    if (argc < 3)
        return qotrt_null_v();
    QrtBytes s;
    QrtBytes sep;
    qotrt_prim_bytes(&args[0], &s);
    qotrt_prim_bytes(&args[1], &sep);
    bool ok;
    int64_t nmax = qotrt_prim_i64(&args[2], &ok);
    V16 out = qotrt_arr_new(8);
    QArr *a = static_cast<QArr *>(out.u.p);
    if (ok && nmax > 0 && sep.len > 0)
    {
        size_t start = 0;
        size_t hl = static_cast<size_t>(s.len), nl = static_cast<size_t>(sep.len);
        int64_t cuts = 0;
        while (cuts < nmax)
        {
            int32_t hit = qrt_find_bytes(s.p, hl, sep.p, nl, start);
            if (hit < 0)
                break;
            qotrt_arr_push(a, qrt_str_v(qotrt_str_new(s.p + start, static_cast<int32_t>(static_cast<size_t>(hit) - start))));
            start = static_cast<size_t>(hit) + nl;
            cuts++;
        }
        qotrt_arr_push(a, qrt_str_v(qotrt_str_new(s.p + start, static_cast<int32_t>(hl - start))));
    }
    else
    {
        qotrt_arr_push(a, qrt_str_v(qotrt_str_new(s.p, s.len))); // n<=0 / 空 sep：整串单元素
    }
    qotrt_prim_bytes_free(&s);
    qotrt_prim_bytes_free(&sep);
    return out;
}

static V16 qotrt_str_join(V16 *args, int argc)
{
    if (argc < 2 || args[0].t != VT_ARR)
        return qotrt_str("", 0);
    QArr *a = static_cast<QArr *>(args[0].u.p);
    QrtBytes sep;
    qotrt_prim_bytes(&args[1], &sep);
    QBuf b = {nullptr, 0, 0};
    for (int32_t i = 0; i < a->len; i++)
    {
        if (i > 0 && sep.len > 0)
            qbuf_append(&b, sep.p, static_cast<size_t>(sep.len));
        QrtBytes eb;
        qotrt_prim_bytes(&a->a[i], &eb);
        qbuf_append(&b, eb.p, static_cast<size_t>(eb.len));
        qotrt_prim_bytes_free(&eb);
    }
    if (b.p)
        b.p[b.len] = 0;
    qotrt_prim_bytes_free(&sep);
    return qotrt_str_from_qbuf(&b);
}

// nmax：-1 = 全部；>0 = 限次。3 参形态（replace）与 4 参形态（replace_n）共用。
static V16 qotrt_str_replace_impl(V16 *args, int argc, int64_t nmax)
{
    (void)argc;
    QrtBytes s;
    QrtBytes old;
    QrtBytes nw;
    qotrt_prim_bytes(&args[0], &s);
    qotrt_prim_bytes(&args[1], &old);
    qotrt_prim_bytes(&args[2], &nw);
    QBuf b = {nullptr, 0, 0};
    size_t start = 0;
    size_t hl = static_cast<size_t>(s.len), ol = static_cast<size_t>(old.len);
    if (ol == 0 || nmax == 0)
    {
        qbuf_append(&b, s.p, hl); // 空 old：原串返回（防死循环）
    }
    else
    {
        int64_t done = 0;
        while (nmax < 0 || done < nmax)
        {
            int32_t hit = qrt_find_bytes(s.p, hl, old.p, ol, start);
            if (hit < 0)
                break;
            qbuf_append(&b, s.p + start, static_cast<size_t>(hit) - start);
            qbuf_append(&b, nw.p, static_cast<size_t>(nw.len));
            start = static_cast<size_t>(hit) + ol;
            done++;
        }
        qbuf_append(&b, s.p + start, hl - start);
    }
    if (b.p)
        b.p[b.len] = 0;
    qotrt_prim_bytes_free(&s);
    qotrt_prim_bytes_free(&old);
    qotrt_prim_bytes_free(&nw);
    return qotrt_str_from_qbuf(&b);
}

static V16 qotrt_str_replace(V16 *args, int argc)
{
    if (argc < 3)
        return qotrt_str("", 0);
    return qotrt_str_replace_impl(args, argc, -1);
}

static V16 qotrt_str_replace_n(V16 *args, int argc)
{
    if (argc < 4)
        return qotrt_str("", 0);
    bool ok;
    int64_t n = qotrt_prim_i64(&args[3], &ok);
    if (!ok || n < 0)
        return qotrt_str("", 0);
    return qotrt_str_replace_impl(args, argc, n);
}

static V16 qotrt_str_case_impl(V16 *args, int argc, bool upper)
{
    if (argc < 1)
        return qotrt_str("", 0);
    QrtBytes b;
    qotrt_prim_bytes(&args[0], &b);
    QStr *q = qotrt_str_new(b.p, b.len);
    qotrt_prim_bytes_free(&b);
    for (int32_t i = 0; i < q->len; i++)
    {
        char c = q->s[i];
        if (upper && c >= 'a' && c <= 'z')
            q->s[i] = static_cast<char>(c - 'a' + 'A');
        else if (!upper && c >= 'A' && c <= 'Z')
            q->s[i] = static_cast<char>(c - 'A' + 'a');
    }
    return qrt_str_v(q);
}

static V16 qotrt_str_upper(V16 *args, int argc)
{
    return qotrt_str_case_impl(args, argc, true);
}

static V16 qotrt_str_lower(V16 *args, int argc)
{
    return qotrt_str_case_impl(args, argc, false);
}

static bool qrt_is_ws(char c)
{
    return c == ' ' || c == '\t' || c == '\n' || c == '\v' || c == '\f' || c == '\r';
}

static V16 qotrt_str_trim(V16 *args, int argc)
{
    if (argc < 1)
        return qotrt_str("", 0);
    QrtBytes b;
    qotrt_prim_bytes(&args[0], &b);
    int32_t i = 0, j = b.len;
    while (i < j && qrt_is_ws(b.p[i]))
        i++;
    while (j > i && qrt_is_ws(b.p[j - 1]))
        j--;
    V16 r = qotrt_str(b.p + i, j - i);
    qotrt_prim_bytes_free(&b);
    return r;
}

static V16 qotrt_str_ltrim(V16 *args, int argc)
{
    if (argc < 1)
        return qotrt_str("", 0);
    QrtBytes b;
    qotrt_prim_bytes(&args[0], &b);
    int32_t i = 0;
    while (i < b.len && qrt_is_ws(b.p[i]))
        i++;
    V16 r = qotrt_str(b.p + i, b.len - i);
    qotrt_prim_bytes_free(&b);
    return r;
}

static V16 qotrt_str_rtrim(V16 *args, int argc)
{
    if (argc < 1)
        return qotrt_str("", 0);
    QrtBytes b;
    qotrt_prim_bytes(&args[0], &b);
    int32_t j = b.len;
    while (j > 0 && qrt_is_ws(b.p[j - 1]))
        j--;
    V16 r = qotrt_str(b.p, j);
    qotrt_prim_bytes_free(&b);
    return r;
}

static V16 qotrt_str_pad_impl(V16 *args, int argc, bool left)
{
    if (argc < 3)
        return qotrt_str("", 0);
    QrtBytes s;
    QrtBytes pad;
    qotrt_prim_bytes(&args[0], &s);
    qotrt_prim_bytes(&args[2], &pad);
    bool ok;
    int64_t n = qotrt_prim_i64(&args[1], &ok);
    if (!ok || n <= s.len || n > 0x7FFFFF00LL)
    {
        V16 keep = qrt_str_v(qotrt_str_new(s.p, s.len));
        qotrt_prim_bytes_free(&s);
        qotrt_prim_bytes_free(&pad);
        return keep;
    }
    char pc = (pad.len > 0) ? pad.p[0] : ' '; // pad 取首字节；空 pad 用空格
    int64_t fill = n - s.len;
    size_t total = static_cast<size_t>(n);
    char *buf = static_cast<char *>(qotrt_xalloc(total + 1));
    if (left)
    {
        std::memset(buf, pc, static_cast<size_t>(fill));
        std::memcpy(buf + fill, s.p, static_cast<size_t>(s.len));
    }
    else
    {
        std::memcpy(buf, s.p, static_cast<size_t>(s.len));
        std::memset(buf + s.len, pc, static_cast<size_t>(fill));
    }
    buf[total] = 0;
    QStr *q = static_cast<QStr *>(qotrt_xalloc(sizeof(QStr)));
    q->len = static_cast<int32_t>(total);
    q->cap = q->len;
    q->hash = 0;
    q->shared = 0;
    q->s = buf;
    V16 v = qrt_str_v(q);
    qotrt_prim_bytes_free(&s);
    qotrt_prim_bytes_free(&pad);
    return v;
}

static V16 qotrt_str_pad_left(V16 *args, int argc)
{
    return qotrt_str_pad_impl(args, argc, true);
}

static V16 qotrt_str_pad_right(V16 *args, int argc)
{
    return qotrt_str_pad_impl(args, argc, false);
}

static V16 qotrt_str_repeat(V16 *args, int argc)
{
    if (argc < 2)
        return qotrt_str("", 0);
    QrtBytes s;
    qotrt_prim_bytes(&args[0], &s);
    bool ok;
    int64_t n = qotrt_prim_i64(&args[1], &ok);
    if (!ok || n <= 0 || s.len == 0)
    {
        qotrt_prim_bytes_free(&s);
        return qotrt_str("", 0);
    }
    if (n > 0x7FFFFF00LL / (s.len > 0 ? s.len : 1))
    {
        qotrt_prim_bytes_free(&s);
        return qotrt_str("", 0); // 超界防御（int32 串长上限）
    }
    size_t total = static_cast<size_t>(s.len) * static_cast<size_t>(n);
    char *buf = static_cast<char *>(qotrt_xalloc(total + 1));
    for (int64_t i = 0; i < n; i++)
        std::memcpy(buf + static_cast<size_t>(i) * static_cast<size_t>(s.len), s.p, static_cast<size_t>(s.len));
    buf[total] = 0;
    QStr *q = static_cast<QStr *>(qotrt_xalloc(sizeof(QStr)));
    q->len = static_cast<int32_t>(total);
    q->cap = q->len;
    q->hash = 0;
    q->shared = 0;
    q->s = buf;
    V16 v = qrt_str_v(q);
    qotrt_prim_bytes_free(&s);
    return v;
}

static V16 qotrt_str_reverse(V16 *args, int argc)
{
    if (argc < 1)
        return qotrt_str("", 0);
    QrtBytes b;
    qotrt_prim_bytes(&args[0], &b);
    QStr *q = qotrt_str_new(b.p, b.len);
    qotrt_prim_bytes_free(&b);
    for (int32_t i = 0, j = q->len - 1; i < j; i++, j--)
    {
        char t = q->s[i];
        q->s[i] = q->s[j];
        q->s[j] = t;
    }
    return qrt_str_v(q);
}

static V16 qotrt_str_cmp(V16 *args, int argc)
{
    if (argc < 2)
        return qotrt_num(0.0);
    QrtBytes a;
    QrtBytes b;
    qotrt_prim_bytes(&args[0], &a);
    qotrt_prim_bytes(&args[1], &b);
    size_t n = static_cast<size_t>(a.len < b.len ? a.len : b.len);
    int c = n > 0 ? std::memcmp(a.p, b.p, n) : 0;
    double r = (c != 0) ? ((c < 0) ? -1.0 : 1.0)
                        : ((a.len < b.len) ? -1.0 : ((a.len > b.len) ? 1.0 : 0.0));
    qotrt_prim_bytes_free(&a);
    qotrt_prim_bytes_free(&b);
    return qotrt_num(r);
}

static V16 qotrt_str_eq_ignore_case(V16 *args, int argc)
{
    if (argc < 2)
        return qotrt_bool(false);
    QrtBytes a;
    QrtBytes b;
    qotrt_prim_bytes(&args[0], &a);
    qotrt_prim_bytes(&args[1], &b);
    bool eq = a.len == b.len;
    if (eq)
    {
        for (int32_t i = 0; i < a.len; i++)
        {
            char x = a.p[i], y = b.p[i];
            if (x >= 'A' && x <= 'Z')
                x = static_cast<char>(x - 'A' + 'a');
            if (y >= 'A' && y <= 'Z')
                y = static_cast<char>(y - 'A' + 'a');
            if (x != y)
            {
                eq = false;
                break;
            }
        }
    }
    qotrt_prim_bytes_free(&a);
    qotrt_prim_bytes_free(&b);
    return qotrt_bool(eq);
}

static V16 qotrt_str_index_of_any(V16 *args, int argc)
{
    if (argc < 2)
        return qotrt_num(-1.0);
    QrtBytes s;
    QrtBytes set;
    qotrt_prim_bytes(&args[0], &s);
    qotrt_prim_bytes(&args[1], &set);
    int32_t r = -1;
    if (set.len > 0)
    {
        for (int32_t i = 0; i < s.len && r < 0; i++)
            for (int32_t k = 0; k < set.len; k++)
                if (s.p[i] == set.p[k])
                {
                    r = i;
                    break;
                }
    }
    qotrt_prim_bytes_free(&s);
    qotrt_prim_bytes_free(&set);
    return qotrt_num(static_cast<double>(r));
}

static V16 qotrt_str_starts_with_any(V16 *args, int argc)
{
    if (argc < 2 || args[1].t != VT_ARR)
        return qotrt_bool(false);
    QArr *a = static_cast<QArr *>(args[1].u.p);
    bool hit = false;
    QStr *ss = (args[0].t == VT_STR) ? static_cast<QStr *>(args[0].u.p) : nullptr;
    for (int32_t i = 0; i < a->len && !hit; i++)
    {
        QrtBytes p;
        qotrt_prim_bytes(&a->a[i], &p);
        if (ss != nullptr)
            hit = p.len <= ss->len && (p.len == 0 || std::memcmp(ss->s, p.p, static_cast<size_t>(p.len)) == 0);
        qotrt_prim_bytes_free(&p);
    }
    return qotrt_bool(hit);
}

static V16 qotrt_str_format(V16 *args, int argc)
{
    if (argc < 1)
        return qotrt_str("", 0);
    QrtBytes f;
    qotrt_prim_bytes(&args[0], &f);
    QBuf b = {nullptr, 0, 0};
    int argi = 1;
    for (int32_t i = 0; i < f.len; i++)
    {
        char c = f.p[i];
        if (c != '%')
        {
            qbuf_append(&b, &c, 1);
            continue;
        }
        if (i + 1 >= f.len)
        {
            qbuf_append(&b, "%", 1); // 尾部孤立 %
            break;
        }
        char d = f.p[++i];
        if (d == '%')
        {
            qbuf_append(&b, "%", 1);
        }
        else if (d == 's')
        {
            if (argi < argc)
            {
                QrtBytes sb;
                qotrt_prim_bytes(&args[argi++], &sb);
                qbuf_append(&b, sb.p, static_cast<size_t>(sb.len));
                qotrt_prim_bytes_free(&sb);
            }
            else
            {
                qbuf_append(&b, "%s", 2); // 实参耗尽：指令原样保留
            }
        }
        else if (d == 'd')
        {
            if (argi < argc)
            {
                bool ok;
                int64_t v = qotrt_prim_i64(&args[argi++], &ok);
                if (!ok)
                {
                    qbuf_append(&b, "%d", 2);
                }
                else
                {
                    char tmp[24];
                    size_t n = qotrt_fmt_i64(tmp, v);
                    qbuf_append(&b, tmp, n);
                }
            }
            else
                qbuf_append(&b, "%d", 2);
        }
        else if (d == 'f')
        {
            if (argi < argc)
            {
                char tmp[64];
                size_t n = qotrt_num_to_buf(qotrt_prim_num(&args[argi++]), tmp, sizeof(tmp));
                qbuf_append(&b, tmp, n);
            }
            else
                qbuf_append(&b, "%f", 2);
        }
        else
        {
            // 未知指令原样保留（%x → "%x"），对齐「轻量格式化」的窄语义
            qbuf_append(&b, "%", 1);
            qbuf_append(&b, &d, 1);
        }
    }
    if (b.p)
        b.p[b.len] = 0;
    qotrt_prim_bytes_free(&f);
    return qotrt_str_from_qbuf(&b);
}

// ---- char_*（UTF-8 码点级；非法字节 = 1 字符口径）----

static V16 qotrt_char_len(V16 *args, int argc)
{
    if (argc < 1)
        return qotrt_num(0.0);
    QrtBytes b;
    qotrt_prim_bytes(&args[0], &b);
    double n = static_cast<double>(qrt_char_count(b.p, static_cast<size_t>(b.len)));
    qotrt_prim_bytes_free(&b);
    return qotrt_num(n);
}

static V16 qotrt_char_slice(V16 *args, int argc)
{
    if (argc < 3)
        return qotrt_str("", 0);
    QrtBytes b;
    qotrt_prim_bytes(&args[0], &b);
    bool ok;
    int64_t from = qotrt_prim_i64(&args[1], &ok);
    int64_t to = ok ? qotrt_prim_i64(&args[2], &ok) : -1;
    if (!ok)
    {
        qotrt_prim_bytes_free(&b);
        return qotrt_str("", 0);
    }
    size_t total = qrt_char_count(b.p, static_cast<size_t>(b.len));
    if (from < 0)
        from = 0;
    if (to > static_cast<int64_t>(total))
        to = static_cast<int64_t>(total);
    V16 r = qotrt_str("", 0);
    if (from < to)
    {
        size_t s0 = qrt_char_start(b.p, static_cast<size_t>(b.len), from);
        size_t s1 = qrt_char_start(b.p, static_cast<size_t>(b.len), to);
        if (s0 <= static_cast<size_t>(b.len) && s1 <= static_cast<size_t>(b.len) && s1 > s0)
            r = qrt_str_v(qotrt_str_new(b.p + s0, static_cast<int32_t>(s1 - s0)));
    }
    qotrt_prim_bytes_free(&b);
    return r;
}

static V16 qotrt_char_at(V16 *args, int argc)
{
    if (argc < 2)
        return qotrt_null_v();
    QrtBytes b;
    qotrt_prim_bytes(&args[0], &b);
    bool ok;
    int64_t i = qotrt_prim_i64(&args[1], &ok);
    V16 r = qotrt_null_v();
    if (ok && i >= 0)
    {
        size_t s0 = qrt_char_start(b.p, static_cast<size_t>(b.len), i);
        if (s0 < static_cast<size_t>(b.len))
        {
            unsigned char c = static_cast<unsigned char>(b.p[s0]);
            size_t step = 1;
            if ((c & 0xE0) == 0xC0)
                step = 2;
            else if ((c & 0xF0) == 0xE0)
                step = 3;
            else if ((c & 0xF8) == 0xF0)
                step = 4;
            if (s0 + step > static_cast<size_t>(b.len))
                step = 1;
            r = qrt_str_v(qotrt_str_new(b.p + s0, static_cast<int32_t>(step)));
        }
    }
    qotrt_prim_bytes_free(&b);
    return r;
}

static V16 qotrt_char_index_byte(V16 *args, int argc)
{
    if (argc < 2)
        return qotrt_num(-1.0);
    QrtBytes b;
    qotrt_prim_bytes(&args[0], &b);
    bool ok;
    int64_t i = qotrt_prim_i64(&args[1], &ok);
    double r = -1.0;
    if (ok && i >= 0)
    {
        size_t off = qrt_char_start(b.p, static_cast<size_t>(b.len), i);
        if (off < static_cast<size_t>(b.len))
            r = static_cast<double>(off);
    }
    qotrt_prim_bytes_free(&b);
    return qotrt_num(r);
}

static V16 qotrt_char_upper(V16 *args, int argc)
{
    if (argc < 1)
        return qotrt_str("", 0);
    QrtBytes b;
    qotrt_prim_bytes(&args[0], &b);
    QStr *q = qotrt_str_new(b.p, b.len);
    qotrt_prim_bytes_free(&b);
    if (q->len > 0 && q->s[0] >= 'a' && q->s[0] <= 'z')
        q->s[0] = static_cast<char>(q->s[0] - 'a' + 'A');
    return qrt_str_v(q);
}

static V16 qotrt_char_lower(V16 *args, int argc)
{
    if (argc < 1)
        return qotrt_str("", 0);
    QrtBytes b;
    qotrt_prim_bytes(&args[0], &b);
    QStr *q = qotrt_str_new(b.p, b.len);
    qotrt_prim_bytes_free(&b);
    if (q->len > 0 && q->s[0] >= 'A' && q->s[0] <= 'Z')
        q->s[0] = static_cast<char>(q->s[0] - 'A' + 'a');
    return qrt_str_v(q);
}

static V16 qotrt_str_utf8_valid(V16 *args, int argc)
{
    if (argc < 1)
        return qotrt_bool(false);
    QrtBytes b;
    qotrt_prim_bytes(&args[0], &b);
    bool ok = qrt_utf8_valid(b.p, static_cast<size_t>(b.len));
    qotrt_prim_bytes_free(&b);
    return qotrt_bool(ok);
}

static V16 qotrt_str_hash(V16 *args, int argc)
{
    if (argc < 1)
        return qotrt_num(0.0);
    QrtBytes b;
    qotrt_prim_bytes(&args[0], &b);
    // qotrt_hash 是 64 位；double 只能精确表示 53 位整数 → mod 2^53
    // （哈希用途允许折叠；两侧同式 ⇒ 逐位一致）
    uint64_t h = qotrt_hash(b.p, static_cast<size_t>(b.len));
    qotrt_prim_bytes_free(&b);
    return qotrt_num(static_cast<double>(h & 0x1FFFFFFFFFFFFFULL));
}

// ============================================================
// P0-D 数字原语
// ============================================================

static V16 qotrt_num_to_str(V16 *args, int argc)
{
    if (argc < 1)
        return qotrt_str("", 0);
    if (argc < 2)
        return qotrt_strings(args, 1); // 1 参 = strings 同形（6 位定点）
    double d = qotrt_prim_num(&args[0]);
    double dec = qotrt_prim_num(&args[1]);
    char tmp[512];
    int n;
    if (dec < 0.0)
        n = std::snprintf(tmp, sizeof(tmp), "%e", d);
    else
    {
        int prec = static_cast<int>(dec);
        if (prec < 0)
            prec = 0;
        if (prec > 340)
            prec = 340;
        n = std::snprintf(tmp, sizeof(tmp), "%.*f", prec, d);
    }
    if (n < 0)
        return qotrt_str("", 0);
    if (static_cast<size_t>(n) >= sizeof(tmp))
        n = static_cast<int>(sizeof(tmp)) - 1;
    return qotrt_str(tmp, n);
}

static V16 qotrt_num_to_str_int(V16 *args, int argc)
{
    if (argc < 1)
        return qotrt_str("", 0);
    bool ok;
    int64_t v = qotrt_prim_i64(&args[0], &ok);
    char tmp[512];
    size_t n;
    if (ok)
        n = qotrt_fmt_i64(tmp, v);
    else
        n = static_cast<size_t>(std::snprintf(tmp, sizeof(tmp), "%f", qotrt_prim_num(&args[0])));
    return qotrt_str(tmp, static_cast<int32_t>(n));
}

// 前后 ASCII 空白裁剪 + strtod 全串消耗（str_to_num/num_is_valid 同一口径）
static double qrt_parse_num(const char *s, int32_t len, bool *ok)
{
    int32_t i = 0, j = len;
    while (i < j && qrt_is_ws(s[i]))
        i++;
    while (j > i && qrt_is_ws(s[j - 1]))
        j--;
    if (j <= i)
    {
        if (ok)
            *ok = false;
        return 0.0;
    }
    char buf[512];
    char *heap = nullptr;
    const char *p;
    int32_t n = j - i;
    if (n >= static_cast<int32_t>(sizeof(buf)))
    {
        heap = static_cast<char *>(qotrt_xalloc(static_cast<size_t>(n) + 1));
        std::memcpy(heap, s + i, static_cast<size_t>(n));
        heap[n] = 0;
        p = heap;
    }
    else
    {
        std::memcpy(buf, s + i, static_cast<size_t>(n));
        buf[n] = 0;
        p = buf;
    }
    char *end = nullptr;
    double d = std::strtod(p, &end);
    bool good = (end != p) && (*end == 0);
    if (heap)
        qotrt_xfree(heap, static_cast<size_t>(n) + 1);
    if (ok)
        *ok = good;
    return good ? d : 0.0;
}

static V16 qotrt_str_to_num(V16 *args, int argc)
{
    if (argc < 1)
        return qotrt_num(-1.0);
    QrtBytes b;
    qotrt_prim_bytes(&args[0], &b);
    bool ok;
    double d = qrt_parse_num(b.p, b.len, &ok);
    qotrt_prim_bytes_free(&b);
    return ok ? qotrt_num(d) : qotrt_num(-1.0);
}

static V16 qotrt_num_is_valid(V16 *args, int argc)
{
    if (argc < 1)
        return qotrt_bool(false);
    QrtBytes b;
    qotrt_prim_bytes(&args[0], &b);
    bool ok;
    qrt_parse_num(b.p, b.len, &ok);
    qotrt_prim_bytes_free(&b);
    return qotrt_bool(ok);
}

static V16 qotrt_int_to_str(V16 *args, int argc)
{
    if (argc < 1)
        return qotrt_str("", 0);
    bool ok;
    int64_t v = qotrt_prim_i64(&args[0], &ok);
    if (!ok)
        return qotrt_str("", 0);
    char tmp[24];
    size_t n = qotrt_fmt_i64(tmp, v);
    return qotrt_str(tmp, static_cast<int32_t>(n));
}

static V16 qotrt_str_to_int(V16 *args, int argc)
{
    if (argc < 1)
        return qotrt_num(-1.0);
    QrtBytes b;
    qotrt_prim_bytes(&args[0], &b);
    int32_t i = 0, j = b.len;
    while (i < j && qrt_is_ws(b.p[i]))
        i++;
    while (j > i && qrt_is_ws(b.p[j - 1]))
        j--;
    char buf[64];
    char *heap = nullptr;
    const char *p = b.p + i;
    int32_t n = j - i;
    if (n >= static_cast<int32_t>(sizeof(buf)))
    {
        heap = static_cast<char *>(qotrt_xalloc(static_cast<size_t>(n) + 1));
        std::memcpy(heap, b.p + i, static_cast<size_t>(n));
        heap[n] = 0;
        p = heap;
    }
    else if (n > 0)
    {
        std::memcpy(buf, b.p + i, static_cast<size_t>(n));
        buf[n] = 0;
        p = buf;
    }
    char *end = nullptr;
    long long v = (n > 0) ? std::strtoll(p, &end, 10) : 0;
    bool good = (n > 0) && (end != p) && (*end == 0);
    if (heap)
        qotrt_xfree(heap, static_cast<size_t>(n) + 1);
    qotrt_prim_bytes_free(&b);
    return qotrt_num(good ? static_cast<double>(v) : -1.0);
}

static V16 qotrt_int_div(V16 *args, int argc)
{
    if (argc < 2)
        return qotrt_num(-1.0);
    bool ok;
    int64_t a = qotrt_prim_i64(&args[0], &ok);
    int64_t b = ok ? qotrt_prim_i64(&args[1], &ok) : 0;
    if (!ok || b == 0 || (a == (-9223372036854775807LL - 1) && b == -1))
        return qotrt_num(-1.0);
    return qotrt_num(static_cast<double>(a / b));
}

// ---- P0-D 补：位运算（int64 域，失败哨兵 -1 与 int_div 同族定价）----
// 口径（与解释器镜像逐字同步，builtins.toml desc 同文）：
//   * 参数 = qotrt_prim_i64 严格整数化；非整 → -1；
//   * bit_shl 用 **uint64 承载**：有符号左移在 C 是 UB（1<<63），uint64
//     截断移位是定义行为；bit_shr = 算术右移（gcc/MSVC 的有符号 >> 均为
//     算术移位，C++20 起为标准保证）；移位数 s ∈ [0,63]，越域 → -1
//     （拒绝而非钳制——可预测优先于宽容）。
static V16 qotrt_bit_and(V16 *args, int argc)
{
    if (argc < 2)
        return qotrt_num(-1.0);
    bool ok;
    int64_t a = qotrt_prim_i64(&args[0], &ok);
    int64_t b = ok ? qotrt_prim_i64(&args[1], &ok) : 0;
    if (!ok)
        return qotrt_num(-1.0);
    return qotrt_num(static_cast<double>(a & b));
}

static V16 qotrt_bit_or(V16 *args, int argc)
{
    if (argc < 2)
        return qotrt_num(-1.0);
    bool ok;
    int64_t a = qotrt_prim_i64(&args[0], &ok);
    int64_t b = ok ? qotrt_prim_i64(&args[1], &ok) : 0;
    if (!ok)
        return qotrt_num(-1.0);
    return qotrt_num(static_cast<double>(a | b));
}

static V16 qotrt_bit_xor(V16 *args, int argc)
{
    if (argc < 2)
        return qotrt_num(-1.0);
    bool ok;
    int64_t a = qotrt_prim_i64(&args[0], &ok);
    int64_t b = ok ? qotrt_prim_i64(&args[1], &ok) : 0;
    if (!ok)
        return qotrt_num(-1.0);
    return qotrt_num(static_cast<double>(a ^ b));
}

static V16 qotrt_bit_not(V16 *args, int argc)
{
    if (argc < 1)
        return qotrt_num(-1.0);
    bool ok;
    int64_t a = qotrt_prim_i64(&args[0], &ok);
    if (!ok)
        return qotrt_num(-1.0);
    return qotrt_num(static_cast<double>(~a));
}

static V16 qotrt_bit_shl(V16 *args, int argc)
{
    if (argc < 2)
        return qotrt_num(-1.0);
    bool ok;
    int64_t a = qotrt_prim_i64(&args[0], &ok);
    int64_t s = ok ? qotrt_prim_i64(&args[1], &ok) : 0;
    if (!ok || s < 0 || s > 63)
        return qotrt_num(-1.0);
    uint64_t u = static_cast<uint64_t>(a) << static_cast<unsigned>(s);
    return qotrt_num(static_cast<double>(static_cast<int64_t>(u)));
}

static V16 qotrt_bit_shr(V16 *args, int argc)
{
    if (argc < 2)
        return qotrt_num(-1.0);
    bool ok;
    int64_t a = qotrt_prim_i64(&args[0], &ok);
    int64_t s = ok ? qotrt_prim_i64(&args[1], &ok) : 0;
    if (!ok || s < 0 || s > 63)
        return qotrt_num(-1.0);
    return qotrt_num(static_cast<double>(a >> static_cast<unsigned>(s)));
}

static V16 qotrt_int_mod(V16 *args, int argc)
{
    if (argc < 2)
        return qotrt_num(-1.0);
    bool ok;
    int64_t a = qotrt_prim_i64(&args[0], &ok);
    int64_t b = ok ? qotrt_prim_i64(&args[1], &ok) : 0;
    if (!ok || b == 0 || (a == (-9223372036854775807LL - 1) && b == -1))
        return qotrt_num(-1.0);
    return qotrt_num(static_cast<double>(a % b)); // C %：符号随被除数
}

static V16 qotrt_int_floor_div(V16 *args, int argc)
{
    if (argc < 2)
        return qotrt_num(-1.0);
    bool ok;
    int64_t a = qotrt_prim_i64(&args[0], &ok);
    int64_t b = ok ? qotrt_prim_i64(&args[1], &ok) : 0;
    if (!ok || b == 0 || (a == (-9223372036854775807LL - 1) && b == -1))
        return qotrt_num(-1.0);
    int64_t q = a / b;
    int64_t r = a % b;
    if (r != 0 && ((r < 0) != (b < 0)))
        q--; // Python // 语义：商向 -∞ 取整
    return qotrt_num(static_cast<double>(q));
}

static V16 qotrt_num_abs(V16 *args, int argc)
{
    if (argc < 1)
        return qotrt_num(0.0);
    return qotrt_num(std::fabs(qotrt_prim_num(&args[0])));
}

static V16 qotrt_num_sign(V16 *args, int argc)
{
    if (argc < 1)
        return qotrt_num(0.0);
    double d = qotrt_prim_num(&args[0]);
    if (d != d)
        return qotrt_num(0.0); // NaN → 0
    return qotrt_num((d > 0) ? 1.0 : ((d < 0) ? -1.0 : 0.0));
}

static V16 qotrt_num_min(V16 *args, int argc)
{
    if (argc < 2)
        return qotrt_num(0.0);
    double a = qotrt_prim_num(&args[0]);
    double b = qotrt_prim_num(&args[1]);
    return qotrt_num(a < b ? a : b);
}

static V16 qotrt_num_max(V16 *args, int argc)
{
    if (argc < 2)
        return qotrt_num(0.0);
    double a = qotrt_prim_num(&args[0]);
    double b = qotrt_prim_num(&args[1]);
    return qotrt_num(a > b ? a : b);
}

static V16 qotrt_num_clamp(V16 *args, int argc)
{
    if (argc < 3)
        return qotrt_num(0.0);
    double v = qotrt_prim_num(&args[0]);
    double lo = qotrt_prim_num(&args[1]);
    double hi = qotrt_prim_num(&args[2]);
    if (lo > hi)
        return qotrt_num(lo); // 区间倒置：夹到 lo（确定性优先）
    if (v < lo)
        return qotrt_num(lo);
    if (v > hi)
        return qotrt_num(hi);
    return qotrt_num(v);
}

static V16 qotrt_num_floor(V16 *args, int argc)
{
    if (argc < 1)
        return qotrt_num(0.0);
    return qotrt_num(std::floor(qotrt_prim_num(&args[0])));
}

static V16 qotrt_num_ceil(V16 *args, int argc)
{
    if (argc < 1)
        return qotrt_num(0.0);
    return qotrt_num(std::ceil(qotrt_prim_num(&args[0])));
}

static V16 qotrt_num_round(V16 *args, int argc)
{
    if (argc < 1)
        return qotrt_num(0.0);
    double a = qotrt_prim_num(&args[0]);
    double dec = (argc >= 2) ? qotrt_prim_num(&args[1]) : 0.0;
    double p = std::pow(10.0, dec);
    if (!(p > 0) || !std::isfinite(p))
        return qotrt_num(a); // 小数位超界：原值返回（确定性）
    return qotrt_num(std::round(a * p) / p); // C round = 半数远离零
}

static V16 qotrt_num_trunc(V16 *args, int argc)
{
    if (argc < 1)
        return qotrt_num(0.0);
    return qotrt_num(std::trunc(qotrt_prim_num(&args[0])));
}

static V16 qotrt_num_is_nan(V16 *args, int argc)
{
    if (argc < 1)
        return qotrt_bool(false);
    return qotrt_bool(std::isnan(qotrt_prim_num(&args[0])) != 0);
}

static V16 qotrt_num_is_inf(V16 *args, int argc)
{
    if (argc < 1)
        return qotrt_bool(false);
    return qotrt_bool(std::isinf(qotrt_prim_num(&args[0])) != 0);
}

static V16 qotrt_num_eq(V16 *args, int argc)
{
    if (argc < 2)
        return qotrt_bool(false);
    double a = qotrt_prim_num(&args[0]);
    double b = qotrt_prim_num(&args[1]);
    return qotrt_bool(a == b); // NaN == NaN 恒 false（IEEE + 语言 ==）
}

static V16 qotrt_exp(V16 *args, int argc)
{
    if (argc < 1)
        return qotrt_num(0.0);
    return qotrt_num(std::exp(qotrt_prim_num(&args[0])));
}

static V16 qotrt_log(V16 *args, int argc)
{
    if (argc < 1)
        return qotrt_num(0.0);
    return qotrt_num(std::log(qotrt_prim_num(&args[0])));
}

static V16 qotrt_log2(V16 *args, int argc)
{
    if (argc < 1)
        return qotrt_num(0.0);
    return qotrt_num(std::log2(qotrt_prim_num(&args[0])));
}

static V16 qotrt_log10(V16 *args, int argc)
{
    if (argc < 1)
        return qotrt_num(0.0);
    return qotrt_num(std::log10(qotrt_prim_num(&args[0])));
}

static V16 qotrt_sin(V16 *args, int argc)
{
    if (argc < 1)
        return qotrt_num(0.0);
    return qotrt_num(std::sin(qotrt_prim_num(&args[0])));
}

static V16 qotrt_cos(V16 *args, int argc)
{
    if (argc < 1)
        return qotrt_num(0.0);
    return qotrt_num(std::cos(qotrt_prim_num(&args[0])));
}

static V16 qotrt_tan(V16 *args, int argc)
{
    if (argc < 1)
        return qotrt_num(0.0);
    return qotrt_num(std::tan(qotrt_prim_num(&args[0])));
}

static V16 qotrt_asin(V16 *args, int argc)
{
    if (argc < 1)
        return qotrt_num(0.0);
    return qotrt_num(std::asin(qotrt_prim_num(&args[0])));
}

static V16 qotrt_acos(V16 *args, int argc)
{
    if (argc < 1)
        return qotrt_num(0.0);
    return qotrt_num(std::acos(qotrt_prim_num(&args[0])));
}

static V16 qotrt_atan(V16 *args, int argc)
{
    if (argc < 1)
        return qotrt_num(0.0);
    return qotrt_num(std::atan(qotrt_prim_num(&args[0])));
}

static V16 qotrt_atan2(V16 *args, int argc)
{
    if (argc < 2)
        return qotrt_num(0.0);
    return qotrt_num(std::atan2(qotrt_prim_num(&args[0]), qotrt_prim_num(&args[1])));
}

static V16 qotrt_hypot(V16 *args, int argc)
{
    if (argc < 2)
        return qotrt_num(0.0);
    return qotrt_num(std::hypot(qotrt_prim_num(&args[0]), qotrt_prim_num(&args[1])));
}

// splitmix64：两侧同式的确定性伪随机（§8：rand 种子固定可复现）
#ifdef QOT_RT_SPLIT
extern uint64_t qotrt_rand_state;
#else
static uint64_t qotrt_rand_state = 0x853C49E6748FEA9BULL;
#endif

static uint64_t qotrt_splitmix64(uint64_t *st)
{
    uint64_t z = (*st += 0x9E3779B97F4A7C15ULL);
    z = (z ^ (z >> 30)) * 0xBF58476D1CE4E5B9ULL;
    z = (z ^ (z >> 27)) * 0x94D049BB133111EBULL;
    return z ^ (z >> 31);
}

static V16 qotrt_rand_seed(V16 *args, int argc)
{
    if (argc < 1)
        return qotrt_bool(false);
    bool ok;
    int64_t s = qotrt_prim_i64(&args[0], &ok);
    if (!ok)
        return qotrt_bool(false);
    qotrt_rand_state = static_cast<uint64_t>(s);
    return qotrt_bool(true);
}

static V16 qotrt_rand_int(V16 *args, int argc)
{
    if (argc < 2)
        return qotrt_num(-1.0);
    bool ok;
    int64_t lo = qotrt_prim_i64(&args[0], &ok);
    int64_t hi = ok ? qotrt_prim_i64(&args[1], &ok) : 0;
    if (!ok || lo > hi)
        return qotrt_num(-1.0);
    uint64_t range = static_cast<uint64_t>(hi - lo) + 1;
    uint64_t z = qotrt_splitmix64(&qotrt_rand_state);
    return qotrt_num(static_cast<double>(lo + static_cast<int64_t>(z % range)));
}

static V16 qotrt_rand_range(V16 *args, int argc)
{
    if (argc < 1)
        return qotrt_num(-1.0);
    bool ok;
    int64_t n = qotrt_prim_i64(&args[0], &ok);
    if (!ok || n <= 0)
        return qotrt_num(-1.0);
    uint64_t z = qotrt_splitmix64(&qotrt_rand_state);
    return qotrt_num(static_cast<double>(static_cast<int64_t>(z % static_cast<uint64_t>(n))));
}

static V16 qotrt_num_pi(V16 *args, int argc)
{
    (void)args;
    (void)argc;
    return qotrt_num(3.141592653589793);
}

static V16 qotrt_num_e(V16 *args, int argc)
{
    (void)args;
    (void)argc;
    return qotrt_num(2.718281828459045);
}

// ============================================================
// P0-E 数组原语（含高阶回调）
// ============================================================

static QArr *qotrt_arr_of(V16 *v)
{
    return (v->t == VT_ARR) ? static_cast<QArr *>(v->u.p) : nullptr;
}

static V16 qotrt_bi_arr_new(V16 *args, int argc)
{
    (void)args;
    (void)argc;
    return qotrt_arr_new(4);
}

static V16 qotrt_arr_new_n(V16 *args, int argc)
{
    if (argc < 1)
        return qotrt_null_v();
    bool ok;
    int64_t n = qotrt_prim_i64(&args[0], &ok);
    if (!ok || n < 0 || n > 0x7FFFFF0LL)
        return qotrt_null_v();
    V16 out = qotrt_arr_new(static_cast<int32_t>(n > 4 ? n : 4));
    QArr *a = static_cast<QArr *>(out.u.p);
    for (int64_t i = 0; i < n; i++)
        qotrt_arr_push(a, qotrt_null_v());
    return out;
}

static V16 qotrt_arr_from(V16 *args, int argc)
{
    QArr *src = (argc >= 1) ? qotrt_arr_of(&args[0]) : nullptr;
    if (!src)
        return qotrt_null_v();
    V16 out = qotrt_arr_new(src->len > 4 ? src->len : 4);
    QArr *a = static_cast<QArr *>(out.u.p);
    for (int32_t i = 0; i < src->len; i++)
        qotrt_arr_push(a, src->a[i]);
    return out;
}

static V16 qotrt_arr_len(V16 *args, int argc)
{
    QArr *a = (argc >= 1) ? qotrt_arr_of(&args[0]) : nullptr;
    return qotrt_num(a ? static_cast<double>(a->len) : 0.0);
}

static V16 qotrt_arr_get(V16 *args, int argc)
{
    if (argc < 2)
        return qotrt_null_v();
    QArr *a = qotrt_arr_of(&args[0]);
    if (!a)
        return qotrt_null_v();
    bool ok;
    int64_t i = qotrt_prim_i64(&args[1], &ok);
    if (!ok || i < 0 || i >= a->len)
        return qotrt_null_v();
    return a->a[static_cast<size_t>(i)];
}

static V16 qotrt_arr_set(V16 *args, int argc)
{
    if (argc < 3)
        return qotrt_bool(false);
    QArr *a = qotrt_arr_of(&args[0]);
    if (!a)
        return qotrt_bool(false);
    bool ok;
    int64_t i = qotrt_prim_i64(&args[1], &ok);
    if (!ok || i < 0)
        return qotrt_bool(false);
    if (i >= a->len)
        return qotrt_bool(true); // 越界写忽略=true（发布面定价，§4.0）
    a->a[static_cast<size_t>(i)] = args[2];
    return qotrt_bool(true);
}

static V16 qotrt_bi_arr_push(V16 *args, int argc)
{
    if (argc < 2)
        return qotrt_num(-1.0);
    QArr *a = qotrt_arr_of(&args[0]);
    if (!a)
        return qotrt_num(-1.0);
    qotrt_arr_push(a, args[1]);
    return qotrt_num(static_cast<double>(a->len));
}

static V16 qotrt_arr_pop(V16 *args, int argc)
{
    if (argc < 1)
        return qotrt_null_v();
    QArr *a = qotrt_arr_of(&args[0]);
    if (!a || a->len == 0)
        return qotrt_null_v();
    return a->a[--a->len];
}

static V16 qotrt_arr_insert(V16 *args, int argc)
{
    if (argc < 3)
        return qotrt_num(-1.0);
    QArr *a = qotrt_arr_of(&args[0]);
    if (!a)
        return qotrt_num(-1.0);
    bool ok;
    int64_t i = qotrt_prim_i64(&args[1], &ok);
    if (!ok || i < 0 || i > a->len)
        return qotrt_num(-1.0);
    qotrt_arr_push(a, qotrt_null_v()); // 保底扩容
    for (int64_t k = a->len - 1; k > i; k--)
        a->a[static_cast<size_t>(k)] = a->a[static_cast<size_t>(k - 1)];
    a->a[static_cast<size_t>(i)] = args[2];
    return qotrt_num(static_cast<double>(a->len));
}

static V16 qotrt_arr_remove_at(V16 *args, int argc)
{
    if (argc < 2)
        return qotrt_null_v();
    QArr *a = qotrt_arr_of(&args[0]);
    if (!a)
        return qotrt_null_v();
    bool ok;
    int64_t i = qotrt_prim_i64(&args[1], &ok);
    if (!ok || i < 0 || i >= a->len)
        return qotrt_null_v();
    V16 gone = a->a[static_cast<size_t>(i)];
    for (int64_t k = i; k + 1 < a->len; k++)
        a->a[static_cast<size_t>(k)] = a->a[static_cast<size_t>(k + 1)];
    a->len--;
    return gone;
}

static V16 qotrt_arr_remove(V16 *args, int argc)
{
    if (argc < 2)
        return qotrt_bool(false);
    QArr *a = qotrt_arr_of(&args[0]);
    if (!a)
        return qotrt_bool(false);
    for (int32_t i = 0; i < a->len; i++)
    {
        if (qotrt_veq(&a->a[static_cast<size_t>(i)], &args[1]))
        {
            for (int32_t k = i; k + 1 < a->len; k++)
                a->a[static_cast<size_t>(k)] = a->a[static_cast<size_t>(k + 1)];
            a->len--;
            return qotrt_bool(true);
        }
    }
    return qotrt_bool(false);
}

static V16 qotrt_arr_clear(V16 *args, int argc)
{
    QArr *a = (argc >= 1) ? qotrt_arr_of(&args[0]) : nullptr;
    if (!a)
        return qotrt_bool(false);
    a->len = 0; // 容量保留（语义写进 desc）
    return qotrt_bool(true);
}

static V16 qotrt_arr_index_of(V16 *args, int argc)
{
    if (argc < 2)
        return qotrt_num(-1.0);
    QArr *a = qotrt_arr_of(&args[0]);
    if (!a)
        return qotrt_num(-1.0);
    for (int32_t i = 0; i < a->len; i++)
        if (qotrt_veq(&a->a[static_cast<size_t>(i)], &args[1]))
            return qotrt_num(static_cast<double>(i));
    return qotrt_num(-1.0);
}

static V16 qotrt_arr_contains(V16 *args, int argc)
{
    if (argc < 2)
        return qotrt_bool(false);
    QArr *a = qotrt_arr_of(&args[0]);
    if (!a)
        return qotrt_bool(false);
    for (int32_t i = 0; i < a->len; i++)
        if (qotrt_veq(&a->a[static_cast<size_t>(i)], &args[1]))
            return qotrt_bool(true);
    return qotrt_bool(false);
}

static V16 qotrt_arr_count(V16 *args, int argc)
{
    if (argc < 2)
        return qotrt_num(0.0);
    QArr *a = qotrt_arr_of(&args[0]);
    if (!a)
        return qotrt_num(0.0);
    int64_t n = 0;
    for (int32_t i = 0; i < a->len; i++)
        if (qotrt_veq(&a->a[static_cast<size_t>(i)], &args[1]))
            n++;
    return qotrt_num(static_cast<double>(n));
}

static V16 qotrt_arr_slice(V16 *args, int argc)
{
    if (argc < 3)
        return qotrt_null_v();
    QArr *a = qotrt_arr_of(&args[0]);
    if (!a)
        return qotrt_null_v();
    bool ok;
    int64_t from = qotrt_prim_i64(&args[1], &ok);
    int64_t to = ok ? qotrt_prim_i64(&args[2], &ok) : -1;
    if (!ok)
        return qotrt_null_v();
    if (from < 0)
        from = 0;
    if (to > a->len)
        to = a->len;
    if (from >= to)
        return qotrt_arr_new(4);
    V16 out = qotrt_arr_new(static_cast<int32_t>(to - from > 4 ? to - from : 4));
    QArr *r = static_cast<QArr *>(out.u.p);
    for (int64_t i = from; i < to; i++)
        qotrt_arr_push(r, a->a[static_cast<size_t>(i)]);
    return out;
}

static V16 qotrt_arr_concat(V16 *args, int argc)
{
    if (argc < 2)
        return qotrt_null_v();
    QArr *a = qotrt_arr_of(&args[0]);
    QArr *b = qotrt_arr_of(&args[1]);
    if (!a || !b)
        return qotrt_null_v();
    V16 out = qotrt_arr_new(a->len + b->len > 4 ? a->len + b->len : 4);
    QArr *r = static_cast<QArr *>(out.u.p);
    for (int32_t i = 0; i < a->len; i++)
        qotrt_arr_push(r, a->a[static_cast<size_t>(i)]);
    for (int32_t i = 0; i < b->len; i++)
        qotrt_arr_push(r, b->a[static_cast<size_t>(i)]);
    return out;
}

static V16 qotrt_arr_reverse(V16 *args, int argc)
{
    if (argc < 1)
        return qotrt_bool(false);
    QArr *a = qotrt_arr_of(&args[0]);
    if (!a)
        return qotrt_bool(false);
    for (int32_t i = 0, j = a->len - 1; i < j; i++, j--)
    {
        V16 t = a->a[static_cast<size_t>(i)];
        a->a[static_cast<size_t>(i)] = a->a[static_cast<size_t>(j)];
        a->a[static_cast<size_t>(j)] = t;
    }
    return qotrt_bool(true);
}

// ---- 稳定排序（数字 < 字符串 < 其它；同 rank2 保持原相对序；并列稳定）----
static int qrt_rank_of(const V16 *v)
{
    if (v->t == VT_NUM || v->t == VT_BOOL)
        return 0;
    if (v->t == VT_STR)
        return 1;
    return 2;
}

static bool qrt_val_less(const V16 *a, const V16 *b)
{
    int ra = qrt_rank_of(a), rb = qrt_rank_of(b);
    if (ra != rb)
        return ra < rb;
    if (ra == 0)
    {
        double x = a->u.d; // bool 已按 1.0/0.0 装箱
        double y = b->u.d;
        return x < y;
    }
    if (ra == 1)
    {
        QStr *x = static_cast<QStr *>(a->u.p);
        QStr *y = static_cast<QStr *>(b->u.p);
        size_t n = static_cast<size_t>(x->len < y->len ? x->len : y->len);
        int c = n > 0 ? std::memcmp(x->s, y->s, n) : 0;
        if (c != 0)
            return c < 0;
        return x->len < y->len;
    }
    return false; // 同为 rank2：互不小于 → 归并取左侧 = 稳定
}

static void qrt_msort_v16(V16 *a, V16 *tmp, int32_t lo, int32_t hi)
{
    if (hi - lo <= 1)
        return;
    int32_t mid = lo + (hi - lo) / 2;
    qrt_msort_v16(a, tmp, lo, mid);
    qrt_msort_v16(a, tmp, mid, hi);
    int32_t i = lo, j = mid, k = lo;
    while (i < mid && j < hi)
    {
        if (qrt_val_less(&a[static_cast<size_t>(j)], &a[static_cast<size_t>(i)]))
            tmp[static_cast<size_t>(k++)] = a[static_cast<size_t>(j++)];
        else
            tmp[static_cast<size_t>(k++)] = a[static_cast<size_t>(i++)]; // 并列取左 = 稳定
    }
    while (i < mid)
        tmp[static_cast<size_t>(k++)] = a[static_cast<size_t>(i++)];
    while (j < hi)
        tmp[static_cast<size_t>(k++)] = a[static_cast<size_t>(j++)];
    for (int32_t x = lo; x < hi; x++)
        a[static_cast<size_t>(x)] = tmp[static_cast<size_t>(x)];
}

static V16 qotrt_arr_sort(V16 *args, int argc)
{
    if (argc < 1)
        return qotrt_bool(false);
    QArr *a = qotrt_arr_of(&args[0]);
    if (!a)
        return qotrt_bool(false);
    if (a->len > 1)
    {
        V16 *tmp = static_cast<V16 *>(qotrt_xalloc(sizeof(V16) * static_cast<size_t>(a->len)));
        qrt_msort_v16(a->a, tmp, 0, a->len);
        qotrt_xfree(tmp, sizeof(V16) * static_cast<size_t>(a->len)); // 独占临时 → 还池
    }
    return qotrt_bool(true);
}

struct QrtSortPair
{
    V16 key;
    int32_t idx;
};

static void qrt_msort_pairs(QrtSortPair *a, QrtSortPair *tmp, int32_t lo, int32_t hi)
{
    if (hi - lo <= 1)
        return;
    int32_t mid = lo + (hi - lo) / 2;
    qrt_msort_pairs(a, tmp, lo, mid);
    qrt_msort_pairs(a, tmp, mid, hi);
    int32_t i = lo, j = mid, k = lo;
    while (i < mid && j < hi)
    {
        if (qrt_val_less(&a[static_cast<size_t>(j)].key, &a[static_cast<size_t>(i)].key))
            tmp[static_cast<size_t>(k++)] = a[static_cast<size_t>(j++)];
        else
            tmp[static_cast<size_t>(k++)] = a[static_cast<size_t>(i++)];
    }
    while (i < mid)
        tmp[static_cast<size_t>(k++)] = a[static_cast<size_t>(i++)];
    while (j < hi)
        tmp[static_cast<size_t>(k++)] = a[static_cast<size_t>(j++)];
    for (int32_t x = lo; x < hi; x++)
        a[static_cast<size_t>(x)] = tmp[static_cast<size_t>(x)];
}

static V16 qotrt_arr_sort_by(V16 *args, int argc)
{
    if (argc < 2)
        return qotrt_bool(false);
    QArr *a = qotrt_arr_of(&args[0]);
    if (!a || !qotrt_is_clo(args[1]))
        return qotrt_bool(false);
    if (a->len <= 1)
        return qotrt_bool(true);
    QrtSortPair *pairs = static_cast<QrtSortPair *>(
        qotrt_xalloc(sizeof(QrtSortPair) * static_cast<size_t>(a->len)));
    for (int32_t i = 0; i < a->len; i++)
    {
        pairs[static_cast<size_t>(i)].key = qotrt_clo_call(args[1], &a->a[static_cast<size_t>(i)], 1);
        pairs[static_cast<size_t>(i)].idx = i;
    }
    QrtSortPair *tmp = static_cast<QrtSortPair *>(
        qotrt_xalloc(sizeof(QrtSortPair) * static_cast<size_t>(a->len)));
    qrt_msort_pairs(pairs, tmp, 0, a->len);
    qotrt_xfree(tmp, sizeof(QrtSortPair) * static_cast<size_t>(a->len));
    V16 *ordered = static_cast<V16 *>(qotrt_xalloc(sizeof(V16) * static_cast<size_t>(a->len)));
    for (int32_t i = 0; i < a->len; i++)
        ordered[static_cast<size_t>(i)] = a->a[static_cast<size_t>(pairs[static_cast<size_t>(i)].idx)];
    for (int32_t i = 0; i < a->len; i++)
        a->a[static_cast<size_t>(i)] = ordered[static_cast<size_t>(i)];
    qotrt_xfree(ordered, sizeof(V16) * static_cast<size_t>(a->len));
    qotrt_xfree(pairs, sizeof(QrtSortPair) * static_cast<size_t>(a->len));
    return qotrt_bool(true);
}

static V16 qotrt_arr_unique(V16 *args, int argc)
{
    if (argc < 1)
        return qotrt_null_v();
    QArr *a = qotrt_arr_of(&args[0]);
    if (!a)
        return qotrt_null_v();
    V16 out = qotrt_arr_new(a->len > 4 ? a->len : 4);
    QArr *r = static_cast<QArr *>(out.u.p);
    for (int32_t i = 0; i < a->len; i++)
    {
        bool seen = false;
        for (int32_t k = 0; k < r->len && !seen; k++)
            seen = qotrt_veq(&r->a[static_cast<size_t>(k)], &a->a[static_cast<size_t>(i)]);
        if (!seen)
            qotrt_arr_push(r, a->a[static_cast<size_t>(i)]);
    }
    return out;
}

static V16 qotrt_arr_flatten(V16 *args, int argc)
{
    if (argc < 1)
        return qotrt_null_v();
    QArr *a = qotrt_arr_of(&args[0]);
    if (!a)
        return qotrt_null_v();
    V16 out = qotrt_arr_new(a->len > 4 ? a->len : 4);
    QArr *r = static_cast<QArr *>(out.u.p);
    for (int32_t i = 0; i < a->len; i++)
    {
        QArr *sub = qotrt_arr_of(&a->a[static_cast<size_t>(i)]);
        if (sub)
        {
            for (int32_t k = 0; k < sub->len; k++) // 展平一层
                qotrt_arr_push(r, sub->a[static_cast<size_t>(k)]);
        }
        else
        {
            qotrt_arr_push(r, a->a[static_cast<size_t>(i)]);
        }
    }
    return out;
}

static V16 qotrt_arr_map(V16 *args, int argc)
{
    if (argc < 2)
        return qotrt_null_v();
    QArr *a = qotrt_arr_of(&args[0]);
    if (!a || !qotrt_is_clo(args[1]))
        return qotrt_null_v();
    V16 out = qotrt_arr_new(a->len > 4 ? a->len : 4);
    QArr *r = static_cast<QArr *>(out.u.p);
    for (int32_t i = 0; i < a->len; i++)
        qotrt_arr_push(r, qotrt_clo_call(args[1], &a->a[static_cast<size_t>(i)], 1));
    return out;
}

static bool qrt_truthy_of(const V16 *v)
{
    // 过滤回调的 truthy 口径 = 语言条件判定（bool 原样；num 非零；其余真值
    // 由 qotrt_truthy_str 承接 —— 该函数对 null/none 为假、字符串非空为真）
    if (v->t == VT_BOOL)
        return v->u.d != 0;
    if (v->t == VT_NUM)
        return v->u.d != 0;
    if (v->t == VT_NULL || v->t == VT_NONE)
        return false;
    if (v->t == VT_STR)
    {
        QStr *q = static_cast<QStr *>(v->u.p);
        return q->len > 0;
    }
    return true;
}

static V16 qotrt_arr_filter(V16 *args, int argc)
{
    if (argc < 2)
        return qotrt_null_v();
    QArr *a = qotrt_arr_of(&args[0]);
    if (!a || !qotrt_is_clo(args[1]))
        return qotrt_null_v();
    V16 out = qotrt_arr_new(4);
    QArr *r = static_cast<QArr *>(out.u.p);
    for (int32_t i = 0; i < a->len; i++)
    {
        V16 keep = qotrt_clo_call(args[1], &a->a[static_cast<size_t>(i)], 1);
        if (qrt_truthy_of(&keep))
            qotrt_arr_push(r, a->a[static_cast<size_t>(i)]);
    }
    return out;
}

static V16 qotrt_arr_reduce(V16 *args, int argc)
{
    if (argc < 3)
        return qotrt_null_v();
    QArr *a = qotrt_arr_of(&args[0]);
    if (!a || !qotrt_is_clo(args[1]))
        return qotrt_null_v();
    V16 acc = args[2];
    for (int32_t i = 0; i < a->len; i++)
    {
        V16 pair[2];
        pair[0] = acc;
        pair[1] = a->a[static_cast<size_t>(i)];
        acc = qotrt_clo_call(args[1], pair, 2);
    }
    return acc;
}

static double qrt_arr_num_fold(V16 *args, int argc, int mode)
{
    // mode: 0=sum 1=min 2=max；元素经 qotrt_prim_num 归一（不可解析=0）
    QArr *a = (argc >= 1) ? qotrt_arr_of(&args[0]) : nullptr;
    if (!a || a->len == 0)
        return 0.0;
    double acc = qotrt_prim_num(&a->a[0]);
    for (int32_t i = 1; i < a->len; i++)
    {
        double v = qotrt_prim_num(&a->a[static_cast<size_t>(i)]);
        if (mode == 0)
            acc += v;
        else if (mode == 1)
            acc = (v < acc) ? v : acc;
        else
            acc = (v > acc) ? v : acc;
    }
    return acc;
}

static V16 qotrt_arr_sum(V16 *args, int argc)
{
    return qotrt_num(qrt_arr_num_fold(args, argc, 0));
}

static V16 qotrt_arr_min(V16 *args, int argc)
{
    return qotrt_num(qrt_arr_num_fold(args, argc, 1));
}

static V16 qotrt_arr_max(V16 *args, int argc)
{
    return qotrt_num(qrt_arr_num_fold(args, argc, 2));
}

static V16 qotrt_arr_join(V16 *args, int argc)
{
    return qotrt_str_join(args, argc); // 与 str_join 同语义（共用单一实现）
}

static V16 qotrt_arr_copy(V16 *args, int argc)
{
    return qotrt_arr_from(args, argc); // 一维浅拷贝 = arr_from 语义
}

static V16 qotrt_arr_eq(V16 *args, int argc)
{
    if (argc < 2)
        return qotrt_bool(false);
    QArr *a = qotrt_arr_of(&args[0]);
    QArr *b = qotrt_arr_of(&args[1]);
    if (!a || !b || a->len != b->len)
        return qotrt_bool(false);
    for (int32_t i = 0; i < a->len; i++)
        if (!qotrt_veq(&a->a[static_cast<size_t>(i)], &b->a[static_cast<size_t>(i)]))
            return qotrt_bool(false);
    return qotrt_bool(true);
}

// ============================================================
// P0-F 字典原语（dict_get/set/keys 已有，见 builtin 节）
// ============================================================

static QDict *qotrt_dict_of(V16 *v)
{
    return (v->t == VT_DICT) ? static_cast<QDict *>(v->u.p) : nullptr;
}

// 从槽里取键字节指针（内联键拷到栈缓冲；外部键借用——调用内不失效）
static char *qrt_slot_key(QDictSlot *sl, char kbuf[9])
{
    if (sl->inl)
    {
        std::memcpy(kbuf, sl->k.inl, static_cast<size_t>(sl->klen));
        kbuf[sl->klen] = 0;
        return kbuf;
    }
    return sl->k.ext;
}

static V16 qotrt_bi_dict_new(V16 *args, int argc)
{
    (void)args;
    (void)argc;
    return qotrt_dict_new();
}

static V16 qotrt_dict_from(V16 *args, int argc)
{
    if (argc < 1)
        return qotrt_null_v();
    QArr *p = qotrt_arr_of(&args[0]);
    if (!p)
        return qotrt_null_v();
    // 两遍法：先验证形状（任一元素非 [k,v] → 整体 null，不产生半成品）
    for (int32_t i = 0; i < p->len; i++)
    {
        QArr *e = qotrt_arr_of(&p->a[static_cast<size_t>(i)]);
        if (!e || e->len < 2)
            return qotrt_null_v();
    }
    V16 out = qotrt_dict_new();
    QDict *d = static_cast<QDict *>(out.u.p);
    for (int32_t i = 0; i < p->len; i++)
    {
        QArr *e = qotrt_arr_of(&p->a[static_cast<size_t>(i)]);
        QBuf kb = {nullptr, 0, 0};
        qotrt_tostring(&e->a[0], &kb);
        // 槽借键缓冲（>8B ext），键缓冲必须永恒有效 → 不还池（arena-free）
        *qotrt_dict_at(d, kb.p ? kb.p : "", static_cast<int32_t>(kb.len),
                       qotrt_hash(kb.p ? kb.p : "", kb.len)) = e->a[1];
    }
    return out;
}

static V16 qotrt_dict_len(V16 *args, int argc)
{
    QDict *d = (argc >= 1) ? qotrt_dict_of(&args[0]) : nullptr;
    return qotrt_num(d ? static_cast<double>(d->size) : 0.0);
}

static V16 qotrt_dict_get_or(V16 *args, int argc)
{
    if (argc < 3)
        return qotrt_null_v();
    QDict *d = qotrt_dict_of(&args[0]);
    if (!d)
        return args[2];
    QBuf b = {nullptr, 0, 0};
    qotrt_tostring(&args[1], &b);
    V16 *hit = qotrt_dict_find(d, b.p ? b.p : "", static_cast<int32_t>(b.len),
                               qotrt_hash(b.p ? b.p : "", b.len));
    V16 out = hit ? *hit : args[2];
    if (b.p)
        qotrt_xfree(b.p, b.cap); // find 不留键引用 → 临时还池
    return out;
}

static V16 qotrt_dict_has(V16 *args, int argc)
{
    if (argc < 2)
        return qotrt_bool(false);
    QDict *d = qotrt_dict_of(&args[0]);
    if (!d)
        return qotrt_bool(false);
    QBuf b = {nullptr, 0, 0};
    qotrt_tostring(&args[1], &b);
    V16 *hit = qotrt_dict_find(d, b.p ? b.p : "", static_cast<int32_t>(b.len),
                               qotrt_hash(b.p ? b.p : "", b.len));
    if (b.p)
        qotrt_xfree(b.p, b.cap);
    return qotrt_bool(hit != nullptr);
}

static V16 qotrt_dict_del(V16 *args, int argc)
{
    if (argc < 2)
        return qotrt_bool(false);
    QDict *d = qotrt_dict_of(&args[0]);
    if (!d)
        return qotrt_bool(false);
    QBuf b = {nullptr, 0, 0};
    qotrt_tostring(&args[1], &b);
    V16 *hit = qotrt_dict_find(d, b.p ? b.p : "", static_cast<int32_t>(b.len),
                               qotrt_hash(b.p ? b.p : "", b.len));
    if (b.p)
        qotrt_xfree(b.p, b.cap);
    if (!hit)
        return qotrt_bool(false);
    // 墓碑删除：槽进探测链（qotrt_dict_at 的复用逻辑已处理）；外部键缓冲
    // 按不变式不释放（永恒有效口径，墓碑槽不再读它）。
    // hit 指向槽的 v 字段 → 经 offsetof 回推槽基址（v 不在偏移 0，直转是 UB）。
    QDictSlot *sl = reinterpret_cast<QDictSlot *>(
        reinterpret_cast<char *>(hit) - offsetof(QDictSlot, v));
    sl->h = QD_DELETED;
    d->size--;
    return qotrt_bool(true);
}

static V16 qotrt_dict_values(V16 *args, int argc)
{
    if (argc < 1)
        return qotrt_null_v();
    QDict *d = qotrt_dict_of(&args[0]);
    if (!d)
        return qotrt_null_v();
    int32_t n = 0;
    for (int32_t i = 0; i < d->cap; i++)
        if (d->slots[i].h > QD_DELETED)
            n++;
    V16 out = qotrt_arr_new(n > 4 ? n : 4);
    QArr *a = static_cast<QArr *>(out.u.p);
    for (int32_t i = 0; i < d->cap; i++)
        if (d->slots[i].h > QD_DELETED)
            qotrt_arr_push(a, d->slots[i].v);
    return out;
}

static V16 qotrt_dict_items(V16 *args, int argc)
{
    if (argc < 1)
        return qotrt_null_v();
    QDict *d = qotrt_dict_of(&args[0]);
    if (!d)
        return qotrt_null_v();
    int32_t n = 0;
    for (int32_t i = 0; i < d->cap; i++)
        if (d->slots[i].h > QD_DELETED)
            n++;
    V16 out = qotrt_arr_new(n > 4 ? n : 4);
    QArr *a = static_cast<QArr *>(out.u.p);
    for (int32_t i = 0; i < d->cap; i++)
    {
        if (d->slots[i].h > QD_DELETED)
        {
            V16 pair = qotrt_arr_new(2);
            QArr *pa = static_cast<QArr *>(pair.u.p);
            char kbuf[9];
            char *kp = qrt_slot_key(&d->slots[i], kbuf);
            // 键物化为新 QStr（快照语义：不借用槽内存）
            qotrt_arr_push(pa, qrt_str_v(qotrt_str_new(kp, d->slots[i].klen)));
            qotrt_arr_push(pa, d->slots[i].v);
            qotrt_arr_push(a, pair);
        }
    }
    return out;
}

static V16 qotrt_dict_clear(V16 *args, int argc)
{
    if (argc < 1)
        return qotrt_bool(false);
    QDict *d = qotrt_dict_of(&args[0]);
    if (!d)
        return qotrt_bool(false);
    for (int32_t i = 0; i < d->cap; i++)
        d->slots[i].h = QD_EMPTY;
    d->size = 0;
    return qotrt_bool(true);
}

static V16 qotrt_dict_copy(V16 *args, int argc)
{
    if (argc < 1)
        return qotrt_null_v();
    QDict *src = qotrt_dict_of(&args[0]);
    if (!src)
        return qotrt_null_v();
    V16 out = qotrt_dict_new();
    QDict *d = static_cast<QDict *>(out.u.p);
    for (int32_t i = 0; i < src->cap; i++)
    {
        if (src->slots[i].h > QD_DELETED)
        {
            char kbuf[9];
            char *kp = qrt_slot_key(&src->slots[i], kbuf);
            *qotrt_dict_at(d, kp, src->slots[i].klen, src->slots[i].h) = src->slots[i].v;
        }
    }
    return out;
}

static V16 qotrt_dict_merge(V16 *args, int argc)
{
    if (argc < 2)
        return qotrt_null_v();
    QDict *a = qotrt_dict_of(&args[0]);
    QDict *b = qotrt_dict_of(&args[1]);
    if (!a || !b)
        return qotrt_null_v();
    V16 out = qotrt_dict_new();
    QDict *d = static_cast<QDict *>(out.u.p);
    QDict *srcs[2] = {a, b};
    for (int s = 0; s < 2; s++)
    {
        for (int32_t i = 0; i < srcs[s]->cap; i++)
        {
            if (srcs[s]->slots[i].h > QD_DELETED)
            {
                char kbuf[9];
                char *kp = qrt_slot_key(&srcs[s]->slots[i], kbuf);
                *qotrt_dict_at(d, kp, srcs[s]->slots[i].klen, srcs[s]->slots[i].h) = srcs[s]->slots[i].v;
            }
        }
    }
    return out;
}

static V16 qotrt_dict_update(V16 *args, int argc)
{
    if (argc < 2)
        return qotrt_num(-1.0);
    QDict *a = qotrt_dict_of(&args[0]);
    QDict *b = qotrt_dict_of(&args[1]);
    if (!a || !b)
        return qotrt_num(-1.0);
    for (int32_t i = 0; i < b->cap; i++)
    {
        if (b->slots[i].h > QD_DELETED)
        {
            char kbuf[9];
            char *kp = qrt_slot_key(&b->slots[i], kbuf);
            *qotrt_dict_at(a, kp, b->slots[i].klen, b->slots[i].h) = b->slots[i].v;
        }
    }
    return qotrt_num(static_cast<double>(a->size));
}

static V16 qotrt_dict_invert(V16 *args, int argc)
{
    if (argc < 1)
        return qotrt_null_v();
    QDict *d = qotrt_dict_of(&args[0]);
    if (!d)
        return qotrt_null_v();
    V16 out = qotrt_dict_new();
    QDict *r = static_cast<QDict *>(out.u.p);
    for (int32_t i = 0; i < d->cap; i++)
    {
        if (d->slots[i].h > QD_DELETED)
        {
            // 新键 = tostring(旧值)；新值 = 旧键（物化为 QStr）
            QBuf nb = {nullptr, 0, 0};
            qotrt_tostring(&d->slots[i].v, &nb);
            char kbuf[9];
            char *kp = qrt_slot_key(&d->slots[i], kbuf);
            V16 keyv = qrt_str_v(qotrt_str_new(kp, d->slots[i].klen));
            *qotrt_dict_at(r, nb.p ? nb.p : "", static_cast<int32_t>(nb.len),
                           qotrt_hash(nb.p ? nb.p : "", nb.len)) = keyv;
        }
    }
    return out;
}

// 路径切分（"a.b.c" 按字节 '.'；空段跳过 —— "a..b" 同 "a.b"）
static int32_t qrt_path_segs(const char *p, int32_t len, const char **segs, int32_t *seg_len, int32_t max_segs)
{
    int32_t n = 0;
    int32_t i = 0;
    while (i <= len)
    {
        int32_t j = i;
        while (j < len && p[j] != '.')
            j++;
        if (j > i && n < max_segs)
        {
            segs[n] = p + i;
            seg_len[n] = j - i;
            n++;
        }
        if (j >= len)
            break;
        i = j + 1;
    }
    return n;
}

static V16 qotrt_dict_get_path(V16 *args, int argc)
{
    if (argc < 2)
        return qotrt_null_v();
    V16 cur = args[0];
    QrtBytes pb;
    qotrt_prim_bytes(&args[1], &pb);
    const char *segs[32];
    int32_t seg_len[32];
    int32_t n = qrt_path_segs(pb.p, pb.len, segs, seg_len, 32);
    V16 r = qotrt_null_v();
    bool ok = n > 0;
    for (int32_t k = 0; k < n && ok; k++)
    {
        QDict *d = qotrt_dict_of(&cur);
        if (!d)
        {
            ok = false;
            break;
        }
        V16 *hit = qotrt_dict_find(d, segs[k], seg_len[k], qotrt_hash(segs[k], static_cast<size_t>(seg_len[k])));
        if (!hit)
        {
            ok = false;
            break;
        }
        cur = *hit;
    }
    if (ok)
        r = cur;
    qotrt_prim_bytes_free(&pb);
    return r;
}

// ================================================================ JSON
// native 侧 JSON（0）：与解释器逐位对齐 —— 数字走 f64 规范形
// （整数值无小数；非整数值用最短往返，等价 Python repr）；字符串默认不做
// \uXXXX 转义（ensure_ascii=False，与解释器默认一致）。
static void qotrt_json_num(QBuf *b, double d)
{
    char tmp[64];
    if (d == static_cast<double>(static_cast<int64_t>(d)) &&
        d >= -9.2e18 && d <= 9.2e18)
    {
        qbuf_append(b, tmp, qotrt_fmt_i64(tmp, static_cast<int64_t>(d)));
        return;
    }
    if (d != d || d == HUGE_VAL || d == -HUGE_VAL)   // NaN/Inf 不是合法 JSON
    {
        qbuf_append(b, "null", 4);
        return;
    }
    for (int prec = 15; prec <= 17; prec++)
    {
        std::snprintf(tmp, sizeof(tmp), "%.*g", prec, d);
        if (std::strtod(tmp, nullptr) == d)
            break;
    }
    qbuf_append(b, tmp, std::strlen(tmp));
}

static void qotrt_json_str(QBuf *b, const char *s, int32_t len, bool esc_ascii)
{
    qbuf_append(b, "\"", 1);
    for (int32_t i = 0; i < len; i++)
    {
        unsigned char c = static_cast<unsigned char>(s[i]);
        switch (c)
        {
        case '"': qbuf_append(b, "\\\"", 2); continue;
        case '\\': qbuf_append(b, "\\\\", 2); continue;
        case '\b': qbuf_append(b, "\\b", 2); continue;
        case '\f': qbuf_append(b, "\\f", 2); continue;
        case '\n': qbuf_append(b, "\\n", 2); continue;
        case '\r': qbuf_append(b, "\\r", 2); continue;
        case '\t': qbuf_append(b, "\\t", 2); continue;
        default: break;
        }
        if (c < 0x20)
        {
            char u[8];
            std::snprintf(u, sizeof(u), "\\u%04x", static_cast<int>(c));
            qbuf_append(b, u, 6);
            continue;
        }
        if (esc_ascii && c >= 0x80)
        {   // ensure_ascii：按 UTF-8 解码码点后 \uXXXX（含代理对）
            int32_t cp = 0; int extra = 0;
            if ((c & 0xE0) == 0xC0) { cp = c & 0x1F; extra = 1; }
            else if ((c & 0xF0) == 0xE0) { cp = c & 0x0F; extra = 2; }
            else if ((c & 0xF8) == 0xF0) { cp = c & 0x07; extra = 3; }
            else { extra = 0; }
            bool ok = extra > 0 && i + extra < len + 0;
            for (int k = 1; k <= extra && ok; k++)
            {
                unsigned char cc = static_cast<unsigned char>(s[i + k]);
                if ((cc & 0xC0) != 0x80) ok = false;
                else cp = (cp << 6) | (cc & 0x3F);
                if (i + k >= len) ok = false;
            }
            if (!ok) { qbuf_append(b, s + i, 1); continue; }
            i += extra;
            char u[16];
            if (cp >= 0x10000)
            {
                int32_t v = cp - 0x10000;
                std::snprintf(u, sizeof(u), "\\u%04x\\u%04x",
                              0xD800 + (v >> 10), 0xDC00 + (v & 0x3FF));
                qbuf_append(b, u, 12);
            }
            else
            {
                std::snprintf(u, sizeof(u), "\\u%04x", static_cast<int>(cp));
                qbuf_append(b, u, 6);
            }
            continue;
        }
        qbuf_append(b, s + i, 1);
    }
    qbuf_append(b, "\"", 1);
}

static void qotrt_json_write(V16 v, QBuf *b, int indent, int depth, bool esc_ascii)
{
    if (v.t == VT_NULL)
    {
        qbuf_append(b, "null", 4);
        return;
    }
    if (v.t == VT_BOOL)
    {
        qbuf_append(b, v.u.d != 0.0 ? "true" : "false", v.u.d != 0.0 ? 4 : 5);
        return;
    }
    if (v.t == VT_NUM)
    {
        qotrt_json_num(b, v.u.d);
        return;
    }
    if (v.t == VT_STR)
    {
        QStr *q = static_cast<QStr *>(v.u.p);
        qotrt_json_str(b, q->s, q->len, esc_ascii);
        return;
    }
    if (v.t == VT_ARR)
    {
        QArr *a = static_cast<QArr *>(v.u.p);
        qbuf_append(b, "[", 1);
        if (a->len == 0)
        {
            qbuf_append(b, "]", 1);
            return;
        }
        for (int32_t i = 0; i < a->len; i++)
        {
            if (i > 0) qbuf_append(b, indent > 0 ? "," : ", ", indent > 0 ? 1 : 2);
            if (indent > 0)
            {
                qbuf_append(b, "\n", 1);
                for (int k = 0; k < indent * (depth + 1); k++) qbuf_append(b, " ", 1);
            }
            qotrt_json_write(a->a[i], b, indent, depth + 1, esc_ascii);
        }
        if (indent > 0)
        {
            qbuf_append(b, "\n", 1);
            for (int k = 0; k < indent * depth; k++) qbuf_append(b, " ", 1);
        }
        qbuf_append(b, "]", 1);
        return;
    }
    if (v.t == VT_DICT)
    {
        QDict *d = static_cast<QDict *>(v.u.p);
        qbuf_append(b, "{", 1);
        if (d->size == 0)
        {
            qbuf_append(b, "}", 1);
            return;
        }
        bool first = true;
        // 【键序定价】QDict 是开放寻址哈希表，遍历序 = 槽序（非插入序）；
        // 解释器侧 dict 是插入序。json_encode 的键序因此两侧不同 —— 已登记
        // 定价（GAPS/D-JSON-ORDER）：需要稳定序的代码请显式
        // `dict_keys + arr_sort` 或按序构造。
        for (int32_t i = 0; i < d->cap; i++)
        {
            QDictSlot *sl = &d->slots[i];
            if (sl->h <= QD_DELETED) continue;
            const char *k = sl->inl ? sl->k.inl : sl->k.ext;
            if (!first) qbuf_append(b, indent > 0 ? "," : ", ", indent > 0 ? 1 : 2);
            first = false;
            if (indent > 0)
            {
                qbuf_append(b, "\n", 1);
                for (int q2 = 0; q2 < indent * (depth + 1); q2++) qbuf_append(b, " ", 1);
            }
            qotrt_json_str(b, k ? k : "", static_cast<int32_t>(sl->klen), esc_ascii);
            qbuf_append(b, ": ", 2);
            qotrt_json_write(sl->v, b, indent, depth + 1, esc_ascii);
        }
        if (indent > 0)
        {
            qbuf_append(b, "\n", 1);
            for (int k = 0; k < indent * depth; k++) qbuf_append(b, " ", 1);
        }
        qbuf_append(b, "}", 1);
        return;
    }
    // 闭包/句柄等：按字符串字面（与解释器 str(obj) 兜底同向）
    QBuf tb = {nullptr, 0, 0};
    qotrt_tostring(&v, &tb);
    qotrt_json_str(b, tb.p ? tb.p : "", tb.len, esc_ascii);
    if (tb.p) qotrt_xfree(tb.p, tb.cap);
}

static V16 qotrt_json_encode(V16 *args, int argc)
{
    if (argc < 1) return qotrt_null_v();
    int indent = (argc > 1 && args[1].t == VT_NUM) ? static_cast<int>(args[1].u.d) : 0;
    bool esc = (argc > 2 && args[2].t == VT_BOOL && args[2].u.d != 0.0);
    QBuf b = {nullptr, 0, 0};
    qotrt_json_write(args[0], &b, indent, 0, esc);
    return qotrt_str_from_qbuf(&b);
}

static V16 qotrt_json_pretty(V16 *args, int argc)
{
    if (argc < 1) return qotrt_null_v();
    V16 a[3];
    a[0] = args[0];
    a[1] = qotrt_num(2.0);
    a[2] = qotrt_bool(true);   // json_pretty 与解释器一致：ensure_ascii=true
    return qotrt_json_encode(a, 3);
}

// ---- 解码：递归下降；失败 → null（解释器侧抛错 —— 登记定价，见 GAPS） ----
struct QJsonP
{
    const char *s; int32_t n; int32_t i; bool ok;
};

static void qjp_ws(QJsonP *p)
{
    while (p->i < p->n)
    {
        char c = p->s[p->i];
        if (c == ' ' || c == '\t' || c == '\n' || c == '\r') p->i++;
        else break;
    }
}

static V16 qjp_value(QJsonP *p, int depth);

static void qjp_utf8(QBuf *b, unsigned int cp)
{
    char t[8]; int k = 0;
    if (cp < 0x80) { t[k++] = static_cast<char>(cp); }
    else if (cp < 0x800)
    {
        t[k++] = static_cast<char>(0xC0 | (cp >> 6));
        t[k++] = static_cast<char>(0x80 | (cp & 0x3F));
    }
    else
    {
        t[k++] = static_cast<char>(0xE0 | (cp >> 12));
        t[k++] = static_cast<char>(0x80 | ((cp >> 6) & 0x3F));
        t[k++] = static_cast<char>(0x80 | (cp & 0x3F));
    }
    qbuf_append(b, t, static_cast<size_t>(k));
}

static V16 qjp_string(QJsonP *p)
{
    QBuf b = {nullptr, 0, 0};
    if (p->i >= p->n || p->s[p->i] != '"') { p->ok = false; return qotrt_null_v(); }
    p->i++;
    while (p->i < p->n)
    {
        char c = p->s[p->i];
        if (c == '"') { p->i++; return qotrt_str_from_qbuf(&b); }
        if (c == '\\')
        {
            p->i++;
            if (p->i >= p->n) break;
            char e = p->s[p->i++];
            switch (e)
            {
            case '"': qbuf_append(&b, "\"", 1); break;
            case '\\': qbuf_append(&b, "\\", 1); break;
            case '/': qbuf_append(&b, "/", 1); break;
            case 'b': qbuf_append(&b, "\b", 1); break;
            case 'f': qbuf_append(&b, "\f", 1); break;
            case 'n': qbuf_append(&b, "\n", 1); break;
            case 'r': qbuf_append(&b, "\r", 1); break;
            case 't': qbuf_append(&b, "\t", 1); break;
            case 'u':
            {
                if (p->i + 4 > p->n) { p->ok = false; return qotrt_null_v(); }
                unsigned int cp = 0;
                for (int k = 0; k < 4; k++)
                {
                    char h = p->s[p->i + k];
                    int d = (h >= '0' && h <= '9') ? (h - '0')
                          : ((h | 32) >= 'a' && (h | 32) <= 'f') ? ((h | 32) - 'a' + 10) : -1;
                    if (d < 0) { p->ok = false; return qotrt_null_v(); }
                    cp = cp * 16 + static_cast<unsigned int>(d);
                }
                p->i += 4;
                if (cp >= 0xD800 && cp <= 0xDBFF && p->i + 6 <= p->n &&
                    p->s[p->i] == '\\' && p->s[p->i + 1] == 'u')
                {
                    unsigned int lo = 0; bool good = true;
                    for (int k = 0; k < 4; k++)
                    {
                        char h = p->s[p->i + 2 + k];
                        int d = (h >= '0' && h <= '9') ? (h - '0')
                              : ((h | 32) >= 'a' && (h | 32) <= 'f') ? ((h | 32) - 'a' + 10) : -1;
                        if (d < 0) { good = false; break; }
                        lo = lo * 16 + static_cast<unsigned int>(d);
                    }
                    if (good && lo >= 0xDC00 && lo <= 0xDFFF)
                    {
                        p->i += 6;
                        cp = 0x10000 + ((cp - 0xD800) << 10) + (lo - 0xDC00);
                    }
                }
                qjp_utf8(&b, cp);
                break;
            }
            default: p->ok = false; return qotrt_null_v();
            }
            continue;
        }
        qbuf_append(&b, p->s + p->i, 1);
        p->i++;
    }
    p->ok = false;
    return qotrt_null_v();
}

static V16 qjp_value(QJsonP *p, int depth)
{
    if (depth > 200) { p->ok = false; return qotrt_null_v(); }
    qjp_ws(p);
    if (p->i >= p->n) { p->ok = false; return qotrt_null_v(); }
    char c = p->s[p->i];
    if (c == '{')
    {
        p->i++;
        V16 d = qotrt_dict_new();
        QDict *dd = static_cast<QDict *>(d.u.p);
        qjp_ws(p);
        if (p->i < p->n && p->s[p->i] == '}') { p->i++; return d; }
        while (p->i < p->n)
        {
            qjp_ws(p);
            V16 k = qjp_string(p);
            if (!p->ok) return qotrt_null_v();
            qjp_ws(p);
            if (p->i >= p->n || p->s[p->i] != ':') { p->ok = false; return qotrt_null_v(); }
            p->i++;
            V16 v = qjp_value(p, depth + 1);
            if (!p->ok) return qotrt_null_v();
            QStr *ks = static_cast<QStr *>(k.u.p);
            V16 *slot = qotrt_dict_at(dd, ks->s, ks->len, qotrt_hash(ks->s, ks->len));
            *slot = v;
            qjp_ws(p);
            if (p->i < p->n && p->s[p->i] == ',') { p->i++; continue; }
            if (p->i < p->n && p->s[p->i] == '}') { p->i++; return d; }
            p->ok = false;
            return qotrt_null_v();
        }
        p->ok = false;
        return qotrt_null_v();
    }
    if (c == '[')
    {
        p->i++;
        V16 a = qotrt_arr_new(8);
        QArr *aa = static_cast<QArr *>(a.u.p);
        qjp_ws(p);
        if (p->i < p->n && p->s[p->i] == ']') { p->i++; return a; }
        while (p->i < p->n)
        {
            V16 v = qjp_value(p, depth + 1);
            if (!p->ok) return qotrt_null_v();
            qotrt_arr_push(aa, v);
            qjp_ws(p);
            if (p->i < p->n && p->s[p->i] == ',') { p->i++; continue; }
            if (p->i < p->n && p->s[p->i] == ']') { p->i++; return a; }
            p->ok = false;
            return qotrt_null_v();
        }
        p->ok = false;
        return qotrt_null_v();
    }
    if (c == '"') return qjp_string(p);
    if (c == 't' && p->i + 4 <= p->n && std::memcmp(p->s + p->i, "true", 4) == 0)
    {
        p->i += 4;
        return qotrt_bool(true);
    }
    if (c == 'f' && p->i + 5 <= p->n && std::memcmp(p->s + p->i, "false", 5) == 0)
    {
        p->i += 5;
        return qotrt_bool(false);
    }
    if (c == 'n' && p->i + 4 <= p->n && std::memcmp(p->s + p->i, "null", 4) == 0)
    {
        p->i += 4;
        return qotrt_null_v();
    }
    // 数字 / NaN / Infinity（Python json.loads 也接受后两者）
    int32_t start = p->i;
    if (p->i < p->n && (p->s[p->i] == '-' || p->s[p->i] == '+')) p->i++;
    bool any = false;
    while (p->i < p->n)
    {
        char d2 = p->s[p->i];
        if ((d2 >= '0' && d2 <= '9') || d2 == '.' || d2 == 'e' || d2 == 'E' ||
            d2 == '-' || d2 == '+')
        {
            p->i++;
            any = true;
            continue;
        }
        break;
    }
    if (any)
    {
        char tmp[64];
        int32_t len = p->i - start;
        if (len >= static_cast<int32_t>(sizeof(tmp))) len = static_cast<int32_t>(sizeof(tmp)) - 1;
        std::memcpy(tmp, p->s + start, static_cast<size_t>(len));
        tmp[len] = 0;
        return qotrt_num(std::strtod(tmp, nullptr));
    }
    if (p->i + 3 <= p->n && std::memcmp(p->s + p->i, "NaN", 3) == 0)
    {
        p->i += 3;
        return qotrt_num(std::nan(""));
    }
    if (p->i + 8 <= p->n && std::memcmp(p->s + p->i, "Infinity", 8) == 0)
    {
        p->i += 8;
        return qotrt_num(HUGE_VAL);
    }
    if (p->i + 9 <= p->n && std::memcmp(p->s + p->i, "-Infinity", 9) == 0)
    {
        p->i += 9;
        return qotrt_num(-HUGE_VAL);
    }
    p->ok = false;
    return qotrt_null_v();
}

static V16 qotrt_json_decode(V16 *args, int argc)
{
    if (argc < 1 || args[0].t != VT_STR) return qotrt_null_v();
    QStr *q = static_cast<QStr *>(args[0].u.p);
    QJsonP p = {q->s, q->len, 0, true};
    V16 v = qjp_value(&p, 0);
    if (!p.ok) return qotrt_null_v();
    qjp_ws(&p);
    if (p.i != p.n) return qotrt_null_v();   // 尾部垃圾：Python json.loads 也拒
    return v;
}

static V16 qotrt_json_valid(V16 *args, int argc)
{
    if (argc < 1 || args[0].t != VT_STR) return qotrt_bool(false);
    QStr *q = static_cast<QStr *>(args[0].u.p);
    QJsonP p = {q->s, q->len, 0, true};
    qjp_value(&p, 0);
    if (!p.ok) return qotrt_bool(false);
    qjp_ws(&p);
    return qotrt_bool(p.i == p.n);
}

static V16 qotrt_dict_set_path(V16 *args, int argc)
{
    if (argc < 3 || args[0].t != VT_DICT)
        return qotrt_bool(false);
    QDict *cur = static_cast<QDict *>(args[0].u.p);
    QrtBytes pb;
    qotrt_prim_bytes(&args[1], &pb);
    const char *segs[32];
    int32_t seg_len[32];
    int32_t n = qrt_path_segs(pb.p, pb.len, segs, seg_len, 32);
    bool ok = n > 0;
    for (int32_t k = 0; k < n && ok; k++)
    {
        if (k == n - 1)
        {
            // 终段：直写（值任意）。键缓冲必须稳定副本 —— dict_at 借用 >8B
            // 键指针，而 pb 可能是临时 qbuf（函数尾释放即悬挂）。
            char *stable = qotrt_strdup(segs[k], seg_len[k]);
            *qotrt_dict_at(cur, stable, seg_len[k], qotrt_hash(stable, static_cast<size_t>(seg_len[k]))) = args[2];
        }
        else
        {
            V16 *hit = qotrt_dict_find(cur, segs[k], seg_len[k], qotrt_hash(segs[k], static_cast<size_t>(seg_len[k])));
            if (hit && hit->t == VT_DICT)
            {
                cur = static_cast<QDict *>(hit->u.p);
            }
            else if (!hit)
            {
                // 缺失中间层 → 就地建字典（键缓冲用稳定副本）
                V16 nd = qotrt_dict_new();
                char *stable = qotrt_strdup(segs[k], seg_len[k]); // 借用键，永不释放
                *qotrt_dict_at(cur, stable, seg_len[k], qotrt_hash(stable, static_cast<size_t>(seg_len[k]))) = nd;
                cur = static_cast<QDict *>(nd.u.p);
            }
            else
            {
                ok = false; // 中途遇非字典 → false（R15 语义）
            }
        }
    }
    qotrt_prim_bytes_free(&pb);
    return qotrt_bool(ok);
}

// ============================================================================
// P0.2 原语层（0 · docs/ §4.3 / §4.7 / §4.8 / §4.11）
// ----------------------------------------------------------------------------
// 规格 = P0.2 批：C 编码与字节(15) + G 时间与调度(11) + H 系统与进程(21) +
// K 类型与错误(11)，共 58 条。与 P0.1 同一套纪律：
//   * 失败哨兵不抛异常：数值 → -1；字符串 → ""；数组/字典/句柄 → null；布尔 → false；
//   * 两侧 oracle 逐位一致：本节每个可观察口径（UTF-8 扫描器、hex/base64 字母表、
//     env 排序、time_parse 子集、weekday/yday 约定）在 primitives.py 有逐行镜像；
//   * 句柄 = 数字（文件表 + 进程表共用判定；§4.0）；
//   * timer_after 明确缓议（需事件循环，§4.15 判例 9：调度策略进标准库）；
//     error_new/error_code 系按 §4.11 自注留给标准库期（宪法 7）。
// ============================================================================

#include <ctime>
#include <chrono>

#if !defined(_WIN32)
#include <unistd.h>
#include <fcntl.h>
#include <poll.h>
#include <signal.h>
#include <sys/wait.h>
#include <sys/types.h>
#include <pwd.h>
#endif

// ---------------------------------------------------------------------------
// C 组 · UTF-8 严格扫描器（utf8_decode / utf8_char_count 的唯一口径）
// 规则（两侧镜像，逐字节确定）：
//   * 合法序列（RFC 3629 严格表）→ 解出码点；
//   * 其余每个非法前导字节产出 1 个 U+FFFD 并只前进 1 字节（后续字节各自
//     重新判定）—— "每非法字节一个替换符" 的朴素口径，不追 Unicode 的
//     maximal-subpart 建议（两边同式即可，确定性优先）。
// ---------------------------------------------------------------------------
static bool qrt_u8_cont(unsigned char c)
{
    return c >= 0x80 && c <= 0xBF;
}

// 返回 0 = 在 i 处解出 1 个码点（写入 cp）；否则 = 非法，消费 1 字节。
static int32_t qrt_u8_step(const unsigned char *b, int32_t n, int32_t i, uint32_t *cp)
{
    unsigned char c = b[i];
    if (c < 0x80)
    {
        *cp = c;
        return 1;
    }
    if (c >= 0xC2 && c <= 0xDF)
    {
        if (i + 1 < n && qrt_u8_cont(b[i + 1]))
        {
            *cp = (static_cast<uint32_t>(c & 0x1F) << 6) | (b[i + 1] & 0x3F);
            return 2;
        }
        return 0;
    }
    if (c >= 0xE0 && c <= 0xEF)
    {
        unsigned char lo = (c == 0xE0) ? 0xA0 : 0x80;
        unsigned char hi = (c == 0xED) ? 0x9F : 0xBF;
        if (i + 2 < n && b[i + 1] >= lo && b[i + 1] <= hi && qrt_u8_cont(b[i + 2]))
        {
            *cp = (static_cast<uint32_t>(c & 0x0F) << 12) |
                  (static_cast<uint32_t>(b[i + 1] & 0x3F) << 6) | (b[i + 2] & 0x3F);
            return 3;
        }
        return 0;
    }
    if (c >= 0xF0 && c <= 0xF4)
    {
        unsigned char lo = (c == 0xF0) ? 0x90 : 0x80;
        unsigned char hi = (c == 0xF4) ? 0x8F : 0xBF;
        if (i + 3 < n && b[i + 1] >= lo && b[i + 1] <= hi &&
            qrt_u8_cont(b[i + 2]) && qrt_u8_cont(b[i + 3]))
        {
            *cp = (static_cast<uint32_t>(c & 0x07) << 18) |
                  (static_cast<uint32_t>(b[i + 1] & 0x3F) << 12) |
                  (static_cast<uint32_t>(b[i + 2] & 0x3F) << 6) | (b[i + 3] & 0x3F);
            return 4;
        }
        return 0;
    }
    return 0;
}

static const char QRT_REPL[3] = {static_cast<char>(0xEF), static_cast<char>(0xBF), static_cast<char>(0xBD)};

static V16 qotrt_utf8_decode(V16 *args, int argc)
{
    if (argc < 1)
        return qotrt_null_v();
    QrtBytes b;
    qotrt_prim_bytes(&args[0], &b);
    const unsigned char *u = reinterpret_cast<const unsigned char *>(b.p);
    QBuf o = {nullptr, 0, 0};
    int32_t i = 0;
    while (i < b.len)
    {
        uint32_t cp = 0;
        int32_t k = qrt_u8_step(u, b.len, i, &cp);
        if (k > 0)
        {
            i += k;
            // 码点 → UTF-8（含 4 字节形）
            if (cp < 0x80)
            {
                char c = static_cast<char>(cp);
                qbuf_append(&o, &c, 1);
            }
            else if (cp < 0x800)
            {
                char t[2] = {static_cast<char>(0xC0 | (cp >> 6)),
                             static_cast<char>(0x80 | (cp & 0x3F))};
                qbuf_append(&o, t, 2);
            }
            else if (cp < 0x10000)
            {
                char t[3] = {static_cast<char>(0xE0 | (cp >> 12)),
                             static_cast<char>(0x80 | ((cp >> 6) & 0x3F)),
                             static_cast<char>(0x80 | (cp & 0x3F))};
                qbuf_append(&o, t, 3);
            }
            else
            {
                char t[4] = {static_cast<char>(0xF0 | (cp >> 18)),
                             static_cast<char>(0x80 | ((cp >> 12) & 0x3F)),
                             static_cast<char>(0x80 | ((cp >> 6) & 0x3F)),
                             static_cast<char>(0x80 | (cp & 0x3F))};
                qbuf_append(&o, t, 4);
            }
        }
        else
        {
            qbuf_append(&o, QRT_REPL, 3);
            i += 1;
        }
    }
    qotrt_prim_bytes_free(&b);
    return qotrt_str_from_qbuf(&o);
}

static V16 qotrt_utf8_char_count(V16 *args, int argc)
{
    if (argc < 1)
        return qotrt_num(0.0);
    QrtBytes b;
    qotrt_prim_bytes(&args[0], &b);
    const unsigned char *u = reinterpret_cast<const unsigned char *>(b.p);
    int32_t i = 0;
    int64_t cnt = 0;
    while (i < b.len)
    {
        uint32_t cp = 0;
        int32_t k = qrt_u8_step(u, b.len, i, &cp);
        i += (k > 0) ? k : 1;
        cnt++;
    }
    qotrt_prim_bytes_free(&b);
    return qotrt_num(static_cast<double>(cnt));
}

// QOT 字符串即 UTF-8 字节序列（文件头不变式）→ utf8_encode 是直通。
// 保留原语位：跨语言口径（解释器 utf8_enc 的规范名迁移目标）与未来内部
// 表示重构的稳定接缝（§4.3 状态行）。
static V16 qotrt_utf8_encode(V16 *args, int argc)
{
    if (argc < 1)
        return qotrt_null_v();
    QrtBytes b;
    qotrt_prim_bytes(&args[0], &b);
    V16 r = qotrt_str(b.p, b.len);
    qotrt_prim_bytes_free(&b);
    return r;
}

// ---------------------------------------------------------------------------
// C 组 · 字节基础（bytes_* 六条 + hex/base64/url 九条）
// ---------------------------------------------------------------------------
static V16 qotrt_bytes_len(V16 *args, int argc)
{
    if (argc < 1)
        return qotrt_num(0.0);
    QrtBytes b;
    qotrt_prim_bytes(&args[0], &b);
    int32_t n = b.len;
    qotrt_prim_bytes_free(&b);
    return qotrt_num(static_cast<double>(n));
}

static V16 qotrt_bytes_slice(V16 *args, int argc)
{
    if (argc < 3)
        return qotrt_str("", 0);
    QrtBytes b;
    qotrt_prim_bytes(&args[0], &b);
    bool ok;
    int64_t from = qotrt_prim_i64(&args[1], &ok);
    int64_t to = ok ? qotrt_prim_i64(&args[2], &ok) : -1;
    if (!ok)
    {
        qotrt_prim_bytes_free(&b);
        return qotrt_str("", 0);
    }
    if (from < 0)
        from = 0;
    if (to > b.len)
        to = b.len;
    V16 r = (from >= to) ? qotrt_str("", 0)
                         : qotrt_str(b.p + from, static_cast<int32_t>(to - from));
    qotrt_prim_bytes_free(&b);
    return r;
}

static V16 qotrt_bytes_byte_at(V16 *args, int argc)
{
    if (argc < 2)
        return qotrt_num(-1.0);
    QrtBytes b;
    qotrt_prim_bytes(&args[0], &b);
    bool ok;
    int64_t i = qotrt_prim_i64(&args[1], &ok);
    int32_t r = -1;
    if (ok && i >= 0 && i < b.len)
        r = static_cast<unsigned char>(b.p[i]);
    qotrt_prim_bytes_free(&b);
    return qotrt_num(static_cast<double>(r));
}

static V16 qotrt_bytes_find(V16 *args, int argc)
{
    if (argc < 3)
        return qotrt_num(-1.0);
    QrtBytes hb;
    QrtBytes nb;
    qotrt_prim_bytes(&args[0], &hb);
    qotrt_prim_bytes(&args[1], &nb);
    bool ok;
    int64_t from = qotrt_prim_i64(&args[2], &ok);
    int32_t r = -1;
    if (ok)
    {
        if (from < 0)
            from = 0;
        if (from <= hb.len)
        {
            const char *hit = nullptr;
            if (nb.len == 0)
                hit = hb.p + from; // 空 sub 命中于起点（与 str_find 同口径）
            else if (nb.len <= hb.len)
            {
                int64_t lim = hb.len - nb.len;
                for (int64_t i = from; i <= lim; i++)
                {
                    if (hb.p[i] == nb.p[0] &&
                        std::memcmp(hb.p + i, nb.p, static_cast<size_t>(nb.len)) == 0)
                    {
                        hit = hb.p + i;
                        break;
                    }
                }
            }
            if (hit)
                r = static_cast<int32_t>(hit - hb.p);
        }
    }
    qotrt_prim_bytes_free(&hb);
    qotrt_prim_bytes_free(&nb);
    return qotrt_num(static_cast<double>(r));
}

static V16 qotrt_bytes_concat(V16 *args, int argc)
{
    if (argc < 2)
        return qotrt_str("", 0);
    QrtBytes a;
    QrtBytes b;
    qotrt_prim_bytes(&args[0], &a);
    qotrt_prim_bytes(&args[1], &b);
    QBuf o = {nullptr, 0, 0};
    qbuf_append(&o, a.p, static_cast<size_t>(a.len));
    qbuf_append(&o, b.p, static_cast<size_t>(b.len));
    qotrt_prim_bytes_free(&a);
    qotrt_prim_bytes_free(&b);
    return qotrt_str_from_qbuf(&o);
}

static V16 qotrt_bytes_eq(V16 *args, int argc)
{
    if (argc < 2)
        return qotrt_bool(false);
    QrtBytes a;
    QrtBytes b;
    qotrt_prim_bytes(&args[0], &a);
    qotrt_prim_bytes(&args[1], &b);
    bool eq = (a.len == b.len) &&
              (a.len == 0 || std::memcmp(a.p, b.p, static_cast<size_t>(a.len)) == 0);
    qotrt_prim_bytes_free(&a);
    qotrt_prim_bytes_free(&b);
    return qotrt_bool(eq);
}

static int qrt_hex_val(unsigned char c)
{
    if (c >= '0' && c <= '9')
        return c - '0';
    if (c >= 'a' && c <= 'f')
        return c - 'a' + 10;
    if (c >= 'A' && c <= 'F')
        return c - 'A' + 10;
    return -1;
}

static V16 qotrt_bytes_to_hex(V16 *args, int argc)
{
    if (argc < 1)
        return qotrt_str("", 0);
    QrtBytes b;
    qotrt_prim_bytes(&args[0], &b);
    QBuf o = {nullptr, 0, 0};
    for (int32_t i = 0; i < b.len; i++)
    {
        char t[2] = {qotrt_hexd_lower[static_cast<unsigned char>(b.p[i]) >> 4],
                     qotrt_hexd_lower[static_cast<unsigned char>(b.p[i]) & 0x0F]};
        qbuf_append(&o, t, 2);
    }
    qotrt_prim_bytes_free(&b);
    return qotrt_str_from_qbuf(&o);
}

static V16 qotrt_bytes_from_hex(V16 *args, int argc)
{
    if (argc < 1)
        return qotrt_null_v();
    QrtBytes b;
    qotrt_prim_bytes(&args[0], &b);
    if (b.len % 2 != 0)
    {
        qotrt_prim_bytes_free(&b);
        return qotrt_null_v();
    }
    QBuf o = {nullptr, 0, 0};
    for (int32_t i = 0; i < b.len; i += 2)
    {
        int hi = qrt_hex_val(static_cast<unsigned char>(b.p[i]));
        int lo = qrt_hex_val(static_cast<unsigned char>(b.p[i + 1]));
        if (hi < 0 || lo < 0)
        {
            qotrt_prim_bytes_free(&b);
            qotrt_xfree(o.p, o.cap);
            return qotrt_null_v();
        }
        char c = static_cast<char>((hi << 4) | lo);
        qbuf_append(&o, &c, 1);
    }
    qotrt_prim_bytes_free(&b);
    return qotrt_str_from_qbuf(&o);
}

static const char QRT_B64TAB[64] = {
    'A', 'B', 'C', 'D', 'E', 'F', 'G', 'H', 'I', 'J', 'K', 'L', 'M',
    'N', 'O', 'P', 'Q', 'R', 'S', 'T', 'U', 'V', 'W', 'X', 'Y', 'Z',
    'a', 'b', 'c', 'd', 'e', 'f', 'g', 'h', 'i', 'j', 'k', 'l', 'm',
    'n', 'o', 'p', 'q', 'r', 's', 't', 'u', 'v', 'w', 'x', 'y', 'z',
    '0', '1', '2', '3', '4', '5', '6', '7', '8', '9', '+', '/'};

static V16 qotrt_bytes_to_base64(V16 *args, int argc)
{
    if (argc < 1)
        return qotrt_str("", 0);
    QrtBytes b;
    qotrt_prim_bytes(&args[0], &b);
    QBuf o = {nullptr, 0, 0};
    int32_t i = 0;
    for (; i + 2 < b.len; i += 3)
    {
        uint32_t v = (static_cast<unsigned char>(b.p[i]) << 16) |
                     (static_cast<unsigned char>(b.p[i + 1]) << 8) |
                     static_cast<unsigned char>(b.p[i + 2]);
        char t[4] = {QRT_B64TAB[(v >> 18) & 63], QRT_B64TAB[(v >> 12) & 63],
                     QRT_B64TAB[(v >> 6) & 63], QRT_B64TAB[v & 63]};
        qbuf_append(&o, t, 4);
    }
    int32_t rem = b.len - i;
    if (rem == 1)
    {
        uint32_t v = static_cast<unsigned char>(b.p[i]) << 16;
        char t[4] = {QRT_B64TAB[(v >> 18) & 63], QRT_B64TAB[(v >> 12) & 63], '=', '='};
        qbuf_append(&o, t, 4);
    }
    else if (rem == 2)
    {
        uint32_t v = (static_cast<unsigned char>(b.p[i]) << 16) |
                     (static_cast<unsigned char>(b.p[i + 1]) << 8);
        char t[4] = {QRT_B64TAB[(v >> 18) & 63], QRT_B64TAB[(v >> 12) & 63],
                     QRT_B64TAB[(v >> 6) & 63], '='};
        qbuf_append(&o, t, 4);
    }
    qotrt_prim_bytes_free(&b);
    return qotrt_str_from_qbuf(&o);
}

static bool qrt_b64_val(unsigned char c, int *out)
{
    if (c >= 'A' && c <= 'Z')
    {
        *out = c - 'A';
        return true;
    }
    if (c >= 'a' && c <= 'z')
    {
        *out = c - 'a' + 26;
        return true;
    }
    if (c >= '0' && c <= '9')
    {
        *out = c - '0' + 52;
        return true;
    }
    if (c == '+' || c == '-')
    {
        *out = 62; // 标准与 URL-safe 字母表都收（decode 侧宽容）
        return true;
    }
    if (c == '/' || c == '_')
    {
        *out = 63;
        return true;
    }
    return false;
}

static V16 qotrt_bytes_from_base64(V16 *args, int argc)
{
    if (argc < 1)
        return qotrt_null_v();
    QrtBytes b;
    qotrt_prim_bytes(&args[0], &b);
    // 跳过 ASCII 空白；其余必须是 base64 字母或 '='（'=' 只能在尾部，≤2 个）
    int vals[4];
    int nv = 0;
    int pad = 0;
    QBuf o = {nullptr, 0, 0};
    bool ok = true;
    for (int32_t i = 0; i < b.len && ok; i++)
    {
        unsigned char c = static_cast<unsigned char>(b.p[i]);
        if (c == ' ' || c == '\t' || c == '\n' || c == '\r' || c == '\v' || c == '\f')
            continue;
        if (c == '=')
        {
            pad++;
            if (pad > 2)
                ok = false;
            continue;
        }
        if (pad > 0)
        {
            ok = false; // '=' 之后又出现数据字符
            break;
        }
        int v = 0;
        if (!qrt_b64_val(c, &v) || nv >= 4)
        {
            ok = false;
            break;
        }
        vals[nv++] = v;
        if (nv == 4)
        {
            uint32_t x = (static_cast<uint32_t>(vals[0]) << 18) |
                         (static_cast<uint32_t>(vals[1]) << 12) |
                         (static_cast<uint32_t>(vals[2]) << 6) |
                         static_cast<uint32_t>(vals[3]);
            char t[3] = {static_cast<char>(x >> 16), static_cast<char>(x >> 8),
                         static_cast<char>(x)};
            qbuf_append(&o, t, 3);
            nv = 0;
        }
    }
    if (ok && (nv == 1 || (nv == 2 && pad != 2) || (nv == 3 && pad != 1) || (nv == 0 && pad > 0)))
        ok = false;
    qotrt_prim_bytes_free(&b);
    if (!ok)
    {
        qotrt_xfree(o.p, o.cap);
        return qotrt_null_v();
    }
    if (nv == 2)
    {
        uint32_t x = (static_cast<uint32_t>(vals[0]) << 18) |
                     (static_cast<uint32_t>(vals[1]) << 12);
        char t[1] = {static_cast<char>(x >> 16)};
        qbuf_append(&o, t, 1);
    }
    else if (nv == 3)
    {
        uint32_t x = (static_cast<uint32_t>(vals[0]) << 18) |
                     (static_cast<uint32_t>(vals[1]) << 12) |
                     (static_cast<uint32_t>(vals[2]) << 6);
        char t[2] = {static_cast<char>(x >> 16), static_cast<char>(x >> 8)};
        qbuf_append(&o, t, 2);
    }
    return qotrt_str_from_qbuf(&o);
}

static bool qrt_url_unreserved(unsigned char c)
{
    return (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') ||
           (c >= '0' && c <= '9') || c == '-' || c == '_' || c == '.' || c == '~';
}

static V16 qotrt_url_encode(V16 *args, int argc)
{
    if (argc < 1)
        return qotrt_str("", 0);
    QrtBytes b;
    qotrt_prim_bytes(&args[0], &b);
    QBuf o = {nullptr, 0, 0};
    for (int32_t i = 0; i < b.len; i++)
    {
        unsigned char c = static_cast<unsigned char>(b.p[i]);
        if (qrt_url_unreserved(c))
        {
            char t = static_cast<char>(c);
            qbuf_append(&o, &t, 1);
        }
        else
        {
            char t[3] = {'%', qotrt_hexd_upper[c >> 4], qotrt_hexd_upper[c & 0x0F]};
            qbuf_append(&o, t, 3);
        }
    }
    qotrt_prim_bytes_free(&b);
    return qotrt_str_from_qbuf(&o);
}

static V16 qotrt_url_decode(V16 *args, int argc)
{
    if (argc < 1)
        return qotrt_str("", 0);
    QrtBytes b;
    qotrt_prim_bytes(&args[0], &b);
    QBuf o = {nullptr, 0, 0};
    int32_t i = 0;
    while (i < b.len)
    {
        unsigned char c = static_cast<unsigned char>(b.p[i]);
        // %XX：两个都必须是十六进制，否则整段按字面输出（确定性口径）
        if (c == '%' && i + 2 < b.len)
        {
            int hi = qrt_hex_val(static_cast<unsigned char>(b.p[i + 1]));
            int lo = qrt_hex_val(static_cast<unsigned char>(b.p[i + 2]));
            if (hi >= 0 && lo >= 0)
            {
                char t = static_cast<char>((hi << 4) | lo);
                qbuf_append(&o, &t, 1);
                i += 3;
                continue;
            }
        }
        qbuf_append(&o, b.p + i, 1);
        i += 1;
    }
    qotrt_prim_bytes_free(&b);
    return qotrt_str_from_qbuf(&o);
}

// ---------------------------------------------------------------------------
// G 组 · 时间与调度（11 条；timer_after 缓议见节头）
// 约定：time_now = 整秒 F64；weekday 周一=0..周日=6；yday 1 起（两侧镜像）。
// ---------------------------------------------------------------------------
static V16 qotrt_time_now(V16 *args, int argc)
{
    (void)args;
    (void)argc;
    return qotrt_num(static_cast<double>(std::time(nullptr)));
}

static V16 qotrt_time_monotonic(V16 *args, int argc)
{
    (void)args;
    (void)argc;
    auto d = std::chrono::steady_clock::now().time_since_epoch();
    return qotrt_num(std::chrono::duration_cast<std::chrono::duration<double>>(d).count());
}

static V16 qotrt_time_now_ms(V16 *args, int argc)
{
    (void)args;
    (void)argc;
    auto d = std::chrono::system_clock::now().time_since_epoch();
    return qotrt_num(static_cast<double>(
        std::chrono::duration_cast<std::chrono::milliseconds>(d).count()));
}

static V16 qotrt_time_monotonic_ms(V16 *args, int argc)
{
    (void)args;
    (void)argc;
    auto d = std::chrono::steady_clock::now().time_since_epoch();
    return qotrt_num(static_cast<double>(
        std::chrono::duration_cast<std::chrono::milliseconds>(d).count()));
}

static void qrt_fill_date_parts(QDict *d, const std::tm *t)
{
    struct
    {
        const char *k;
        double v;
    } fields[8] = {
        {"year", static_cast<double>(t->tm_year + 1900)},
        {"month", static_cast<double>(t->tm_mon + 1)},
        {"day", static_cast<double>(t->tm_mday)},
        {"hour", static_cast<double>(t->tm_hour)},
        {"minute", static_cast<double>(t->tm_min)},
        {"second", static_cast<double>(t->tm_sec)},
        {"weekday", static_cast<double>((t->tm_wday + 6) % 7)}, // 周一=0
        {"yday", static_cast<double>(t->tm_yday + 1)},          // 1 起
    };
    for (int i = 0; i < 8; i++)
        *qotrt_dict_at(d, fields[i].k, static_cast<int32_t>(std::strlen(fields[i].k)),
                       qotrt_hash(fields[i].k, std::strlen(fields[i].k))) =
            qotrt_num(fields[i].v);
}

static V16 qotrt_time_date_parts(V16 *args, int argc)
{
    if (argc < 1)
        return qotrt_null_v();
    std::time_t t = static_cast<std::time_t>(qotrt_prim_num(&args[0]));
    std::tm *lt = std::localtime(&t);
    if (!lt)
        return qotrt_null_v();
    V16 d = qotrt_dict_new();
    qrt_fill_date_parts(static_cast<QDict *>(d.u.p), lt);
    return d;
}

static V16 qotrt_time_utc_parts(V16 *args, int argc)
{
    if (argc < 1)
        return qotrt_null_v();
    std::time_t t = static_cast<std::time_t>(qotrt_prim_num(&args[0]));
    std::tm *ut = std::gmtime(&t);
    if (!ut)
        return qotrt_null_v();
    V16 d = qotrt_dict_new();
    qrt_fill_date_parts(static_cast<QDict *>(d.u.p), ut);
    return d;
}

static V16 qotrt_time_make(V16 *args, int argc)
{
    if (argc < 6)
        return qotrt_num(-1.0);
    std::tm t;
    std::memset(&t, 0, sizeof(t));
    t.tm_year = static_cast<int>(qotrt_prim_num(&args[0])) - 1900;
    t.tm_mon = static_cast<int>(qotrt_prim_num(&args[1])) - 1;
    t.tm_mday = static_cast<int>(qotrt_prim_num(&args[2]));
    t.tm_hour = static_cast<int>(qotrt_prim_num(&args[3]));
    t.tm_min = static_cast<int>(qotrt_prim_num(&args[4]));
    t.tm_sec = static_cast<int>(qotrt_prim_num(&args[5]));
    t.tm_isdst = -1; // 让 mktime 裁决 DST
    std::time_t r = std::mktime(&t);
    if (r == static_cast<std::time_t>(-1))
        return qotrt_num(-1.0);
    return qotrt_num(static_cast<double>(r));
}

// strftime 风格格式化（本地时区）。缓冲 512；溢出 → ""（失败哨兵）。
static V16 qotrt_time_strftime(V16 *args, int argc)
{
    if (argc < 2)
        return qotrt_str("", 0);
    std::time_t t = static_cast<std::time_t>(qotrt_prim_num(&args[0]));
    std::tm *lt = std::localtime(&t);
    if (!lt)
        return qotrt_str("", 0);
    QrtBytes fb;
    qotrt_prim_bytes(&args[1], &fb);
    char fmt[256];
    int32_t fl = fb.len < 255 ? fb.len : 255;
    std::memcpy(fmt, fb.p, static_cast<size_t>(fl));
    fmt[fl] = 0;
    qotrt_prim_bytes_free(&fb);
    char buf[512];
    size_t n = std::strftime(buf, sizeof(buf), fmt, lt);
    if (n == 0)
        return qotrt_str("", 0);
    return qotrt_str(buf, static_cast<int32_t>(n));
}

// time_parse(s, fmt)：极简子集 %Y(4位) %m %d %H %M %S(各2位) + 字面字符
// （' ' 精确匹配一个空白字节）。前缀匹配（fmt 消耗完即可，尾部输入忽略）。
// 未指定字段的默认值 = 1900-01-01 00:00:00（本地时区）。失败 -1。
static V16 qotrt_time_parse(V16 *args, int argc)
{
    if (argc < 2)
        return qotrt_num(-1.0);
    QrtBytes sb;
    QrtBytes fb;
    qotrt_prim_bytes(&args[0], &sb);
    qotrt_prim_bytes(&args[1], &fb);
    int32_t si = 0;
    int32_t fi = 0;
    bool ok = true;
    int y = 1900, mo = 1, dd = 1, hh = 0, mi = 0, ss = 0;
    while (fi < fb.len && ok)
    {
        char f = fb.p[fi];
        if (f == '%' && fi + 1 < fb.len)
        {
            char d = fb.p[fi + 1];
            fi += 2;
            int width = 0;
            if (d == 'Y')
                width = 4;
            else if (d == 'm' || d == 'd' || d == 'H' || d == 'M' || d == 'S')
                width = 2;
            else
            {
                ok = false; // 未知指令 → 失败（不做静默宽容）
                break;
            }
            int v = 0;
            for (int k = 0; k < width; k++)
            {
                if (si >= sb.len || sb.p[si] < '0' || sb.p[si] > '9')
                {
                    ok = false;
                    break;
                }
                v = v * 10 + (sb.p[si] - '0');
                si++;
            }
            if (!ok)
                break;
            if (d == 'Y')
                y = v;
            else if (d == 'm')
                mo = v;
            else if (d == 'd')
                dd = v;
            else if (d == 'H')
                hh = v;
            else if (d == 'M')
                mi = v;
            else
                ss = v;
        }
        else if (f == ' ')
        {
            if (si >= sb.len || sb.p[si] != ' ')
            {
                ok = false;
                break;
            }
            si++;
            fi++;
        }
        else
        {
            if (si >= sb.len || sb.p[si] != f)
            {
                ok = false;
                break;
            }
            si++;
            fi++;
        }
    }
    qotrt_prim_bytes_free(&sb);
    qotrt_prim_bytes_free(&fb);
    if (!ok)
        return qotrt_num(-1.0);
    std::tm t;
    std::memset(&t, 0, sizeof(t));
    t.tm_year = y - 1900;
    t.tm_mon = mo - 1;
    t.tm_mday = dd;
    t.tm_hour = hh;
    t.tm_min = mi;
    t.tm_sec = ss;
    t.tm_isdst = -1;
    std::time_t r = std::mktime(&t);
    if (r == static_cast<std::time_t>(-1))
        return qotrt_num(-1.0);
    return qotrt_num(static_cast<double>(r));
}

static V16 qotrt_time_sleep_ms(V16 *args, int argc)
{
    if (argc < 1)
        return qotrt_bool(false);
    bool ok;
    int64_t ms = qotrt_prim_i64(&args[0], &ok);
    if (!ok || ms < 0)
        return qotrt_bool(false);
#if defined(_WIN32)
    while (ms > 0)
    {
        DWORD chunk = ms > 1000000 ? 1000000u : static_cast<DWORD>(ms);
        Sleep(chunk);
        ms -= chunk;
    }
#else
    while (ms > 0)
    {
        int64_t chunk = ms > 1000000 ? 1000000 : ms;
        struct timespec ts;
        ts.tv_sec = static_cast<time_t>(chunk / 1000);
        ts.tv_nsec = static_cast<long>((chunk % 1000) * 1000000L);
        nanosleep(&ts, nullptr);
        ms -= chunk;
    }
#endif
    return qotrt_bool(true);
}

// 本地时区对给定时间戳的偏移（小时；含 DST）：mktime(local) - mkgmtime(gmt)。
static V16 qotrt_time_zone_offset(V16 *args, int argc)
{
    if (argc < 1)
        return qotrt_num(0.0);
    std::time_t t = static_cast<std::time_t>(qotrt_prim_num(&args[0]));
    std::tm *lt = std::localtime(&t);
    std::tm *ut = std::gmtime(&t);
    if (!lt || !ut)
        return qotrt_num(0.0);
    std::tm lc = *lt;
    std::tm uc = *ut;
    std::time_t l = std::mktime(&lc);
#if defined(_WIN32)
    std::time_t g = _mkgmtime(&uc);
#else
    std::time_t g = timegm(&uc);
#endif
    if (l == static_cast<std::time_t>(-1) || g == static_cast<std::time_t>(-1))
        return qotrt_num(0.0);
    return qotrt_num(static_cast<double>(l - g) / 3600.0);
}

// ---------------------------------------------------------------------------
// H 组 · 系统与进程（21 条）
// 进程句柄 = 与文件表同款世代计数表（§4.0 句柄约定）。子进程 stdin 接
// /dev/null·NUL（读即 EOF）；stdout/stderr 经管道收集。
// ---------------------------------------------------------------------------

// ---- 命令行参数（main → qotrt_init_args 注册；args_get 取 argv[1..]）----
#if defined(_WIN32)
#ifdef QOT_RT_SPLIT
extern LPWSTR *qotrt_os_wargv;
#else
static LPWSTR *qotrt_os_wargv = nullptr;
#endif
#ifdef QOT_RT_SPLIT
extern int qotrt_os_wargc;
#else
static int qotrt_os_wargc = 0;
#endif
#endif
#ifdef QOT_RT_SPLIT
extern int qotrt_os_argc;
#else
static int qotrt_os_argc = 0;
#endif
#ifdef QOT_RT_SPLIT
extern char **qotrt_os_argv;
#else
static char **qotrt_os_argv = nullptr;
#endif

static void qotrt_init_args(int argc, char **argv)
{
    qotrt_os_argc = argc;
    qotrt_os_argv = argv;
}

// QOTNative：懒探测缓存提升到文件作用域（原为函数内 static，宏翻转安全化）
#if defined(_WIN32)
typedef LPWSTR *(WINAPI *CmdLineToArgvWFn)(LPCWSTR, int *);
static CmdLineToArgvWFn qotrt_cmdline_fn = nullptr;
static bool qotrt_cmdline_probed = false;
#endif

static V16 qotrt_args_get(V16 *args, int argc)
{
    (void)args;
    (void)argc;
#if defined(_WIN32)
    // W 系命令行：Unicode 参数不经过 ANSI 撕裂。shell32 动态加载（不加链接依赖）。
    if (!qotrt_cmdline_probed)
    {
        qotrt_cmdline_probed = true;
        HMODULE sh = LoadLibraryA("shell32.dll");
        if (sh)
        {
            qotrt_cmdline_fn = reinterpret_cast<CmdLineToArgvWFn>(
                reinterpret_cast<void *>(GetProcAddress(sh, "CommandLineToArgvW")));
        }
    }
    CmdLineToArgvWFn fn = qotrt_cmdline_fn;
    if (fn)
    {
        LPWSTR *w = fn(GetCommandLineW(), &qotrt_os_wargc);
        if (w)
        {
            qotrt_os_wargv = w;
            V16 out = qotrt_arr_new(qotrt_os_wargc > 1 ? qotrt_os_wargc - 1 : 4);
            QArr *a = static_cast<QArr *>(out.u.p);
            for (int i = 1; i < qotrt_os_wargc; i++)
            {
                int32_t need = WideCharToMultiByte(CP_UTF8, 0, w[i], -1, nullptr, 0, nullptr, nullptr);
                if (need <= 0)
                    continue;
                QBuf o = {nullptr, 0, 0};
                int32_t got = need - 1;
                qbuf_need(&o, static_cast<size_t>(got));
                WideCharToMultiByte(CP_UTF8, 0, w[i], -1, o.p, need, nullptr, nullptr);
                o.len = static_cast<size_t>(got);
                qotrt_arr_push(a, qotrt_str_from_qbuf(&o));
            }
            LocalFree(w);
            qotrt_os_wargv = nullptr;
            return out;
        }
    }
#endif
    V16 out = qotrt_arr_new(4);
    QArr *a = static_cast<QArr *>(out.u.p);
    for (int i = 1; i < qotrt_os_argc; i++)
    {
        int32_t n = static_cast<int32_t>(std::strlen(qotrt_os_argv[i]));
        qotrt_arr_push(a, qotrt_str(qotrt_os_argv[i], n));
    }
    return out;
}

// ---- 环境变量（5 条）----
static V16 qotrt_env_get(V16 *args, int argc)
{
    if (argc < 1)
        return qotrt_str("", 0);
    QrtBytes b;
    qotrt_prim_bytes(&args[0], &b);
    char key[512];
    int32_t kl = b.len < 511 ? b.len : 511;
    std::memcpy(key, b.p, static_cast<size_t>(kl));
    key[kl] = 0;
    qotrt_prim_bytes_free(&b);
    const char *v = std::getenv(key);
    if (!v)
        return qotrt_str("", 0);
    return qotrt_str(v, static_cast<int32_t>(std::strlen(v)));
}

#if defined(_WIN32)
static bool qrt_putenv2(const char *k, const char *v)
{
    return _putenv_s(k, v) == 0;
}
#else
static bool qrt_putenv2(const char *k, const char *v)
{
    return setenv(k, v, 1) == 0;
}
#endif

static V16 qotrt_env_set(V16 *args, int argc)
{
    if (argc < 2)
        return qotrt_bool(false);
    QrtBytes kb;
    QrtBytes vb;
    qotrt_prim_bytes(&args[0], &kb);
    qotrt_prim_bytes(&args[1], &vb);
    char key[512];
    char val[4096];
    int32_t kl = kb.len < 511 ? kb.len : 511;
    int32_t vl = vb.len < 4095 ? vb.len : 4095;
    std::memcpy(key, kb.p, static_cast<size_t>(kl));
    key[kl] = 0;
    std::memcpy(val, vb.p, static_cast<size_t>(vl));
    val[vl] = 0;
    qotrt_prim_bytes_free(&kb);
    qotrt_prim_bytes_free(&vb);
    return qotrt_bool(qrt_putenv2(key, val));
}

static V16 qotrt_env_unset(V16 *args, int argc)
{
    if (argc < 1)
        return qotrt_bool(false);
    QrtBytes kb;
    qotrt_prim_bytes(&args[0], &kb);
    char key[512];
    int32_t kl = kb.len < 511 ? kb.len : 511;
    std::memcpy(key, kb.p, static_cast<size_t>(kl));
    key[kl] = 0;
    qotrt_prim_bytes_free(&kb);
#if defined(_WIN32)
    // Windows 语义：_putenv_s(k, "") = 删除
    return qotrt_bool(qrt_putenv2(key, ""));
#else
    return qotrt_bool(unsetenv(key) == 0);
#endif
}

static V16 qotrt_env_list(V16 *args, int argc)
{
    (void)args;
    (void)argc;
    // 【P0.2 轮重写】v1 用「裸 k/v 临时缓冲 + strlen 尺寸 free」——是整个
    // runtime 里唯一的小字符串 malloc/free 舞蹈，与 arena-free 范式（分配
    // 即持有、零 free 路径）相悖，且在「宽字符→UTF-8 尺寸两遍不一致」时
    // 会产生 free 尺寸错配（毒化仪器实证）。v2 直接把键/值造成 QStr
    // （缓冲永不释放，与全 runtime 同一账面），排序基于 QStr 字节，无任何
    // 中间 free 路径。
    struct QrtEnvPair
    {
        V16 k;
        V16 v;
    };
    QrtEnvPair pairs[512];
    int32_t n = 0;
#if defined(_WIN32)
    LPWCH blk = GetEnvironmentStringsW();
    if (!blk)
        return qotrt_null_v();
    LPWCH p = blk;
    while (*p && n < 512)
    {
        LPWCH eq = p;
        while (*eq && *eq != L'=')
            eq++;
        if (*eq == L'=' && eq > p)
        {
            int kn = WideCharToMultiByte(CP_UTF8, 0, p, static_cast<int>(eq - p),
                                         nullptr, 0, nullptr, nullptr);
            int vn = WideCharToMultiByte(CP_UTF8, 0, eq + 1, -1,
                                         nullptr, 0, nullptr, nullptr);
            if (kn > 0 && vn > 0)
            {
                // 一次转换 + str_new 直纳（同一份字节只量一次、只写一次）
                QBuf kb = {nullptr, 0, 0};
                QBuf vb = {nullptr, 0, 0};
                qbuf_need(&kb, static_cast<size_t>(kn));
                qbuf_need(&vb, static_cast<size_t>(vn));
                WideCharToMultiByte(CP_UTF8, 0, p, static_cast<int>(eq - p),
                                    kb.p, kn, nullptr, nullptr);
                kb.len = static_cast<size_t>(kn);
                kb.p[kn] = 0;
                WideCharToMultiByte(CP_UTF8, 0, eq + 1, -1,
                                    vb.p, vn, nullptr, nullptr);
                vb.len = static_cast<size_t>(vn - 1); // -1：写 pass 的 NUL 不计
                pairs[n].k = qotrt_str_from_qbuf(&kb);
                pairs[n].v = qotrt_str_from_qbuf(&vb);
                n++;
            }
        }
        while (*p)
            p++;
        p++;
    }
    FreeEnvironmentStringsW(blk);
#else
    extern char **environ;
    for (char **e = environ; *e && n < 512; e++)
    {
        const char *eq = std::strchr(*e, '=');
        if (eq && eq > *e)
        {
            pairs[n].k = qotrt_str(*e, static_cast<int32_t>(eq - *e));
            pairs[n].v = qotrt_str(eq + 1, static_cast<int32_t>(std::strlen(eq + 1)));
            n++;
        }
    }
#endif
    // 插入排序（n ≤ 512；按键字节序，确定性口径供差分）。QStr 指针稳定，
    // 比较走 QStr 字节（与 Python 侧 bytes 排序同口径）。
    for (int32_t i = 1; i < n; i++)
    {
        QrtEnvPair tmp = pairs[i];
        int32_t j = i - 1;
        while (j >= 0)
        {
            QStr *x = static_cast<QStr *>(pairs[j].k.u.p);
            QStr *y = static_cast<QStr *>(tmp.k.u.p);
            size_t mn = static_cast<size_t>(x->len < y->len ? x->len : y->len);
            int c = mn > 0 ? std::memcmp(x->s, y->s, mn) : 0;
            if (c < 0 || (c == 0 && x->len <= y->len))
                break;
            pairs[j + 1] = pairs[j];
            j--;
        }
        pairs[j + 1] = tmp;
    }
    V16 out = qotrt_arr_new(n > 4 ? n : 4);
    QArr *a = static_cast<QArr *>(out.u.p);
    for (int32_t i = 0; i < n; i++)
    {
        V16 pair = qotrt_arr_new(2);
        QArr *pa = static_cast<QArr *>(pair.u.p);
        qotrt_arr_push(pa, pairs[i].k);
        qotrt_arr_push(pa, pairs[i].v);
        qotrt_arr_push(a, pair);
    }
    return out;
}

// env_expand：%VAR% / ${VAR} / $VAR 三形态；未知变量保留原样。
// 名字字符集 [A-Za-z0-9_]；不递归展开（策略归标准库）。
static V16 qotrt_env_expand(V16 *args, int argc)
{
    if (argc < 1)
        return qotrt_str("", 0);
    QrtBytes b;
    qotrt_prim_bytes(&args[0], &b);
    QBuf o = {nullptr, 0, 0};
    int32_t i = 0;
    while (i < b.len)
    {
        unsigned char c = static_cast<unsigned char>(b.p[i]);
        if ((c == '$' || c == '%') && i + 1 < b.len)
        {
            bool braced = false;
            int32_t j = i + 1;
            if (c == '$' && b.p[j] == '{')
            {
                braced = true;
                j++;
            }
            int32_t ns = j;
            while (j < b.len)
            {
                unsigned char d = static_cast<unsigned char>(b.p[j]);
                if (!((d >= 'A' && d <= 'Z') || (d >= 'a' && d <= 'z') ||
                      (d >= '0' && d <= '9') || d == '_'))
                    break;
                j++;
            }
            int32_t ne = j;
            bool closed = true;
            if (braced)
            {
                closed = (j < b.len && b.p[j] == '}');
                if (closed)
                    ne = j; // 名字到 } 前
            }
            else if (c == '%')
            {
                closed = (j < b.len && b.p[j] == '%');
            }
            if (closed && ne > ns)
            {
                char key[512];
                int32_t kl = ne - ns < 511 ? ne - ns : 511;
                std::memcpy(key, b.p + ns, static_cast<size_t>(kl));
                key[kl] = 0;
                const char *v = std::getenv(key);
                int32_t syn_end = (braced || c == '%') ? j + 1 : j; // 语法消费终点
                if (syn_end > b.len)
                    syn_end = b.len;
                if (v)
                    qbuf_append(&o, v, std::strlen(v));
                else
                    qbuf_append(&o, b.p + i, static_cast<size_t>(syn_end - i)); // 原样保留
                i = syn_end;
                continue;
            }
        }
        qbuf_append(&o, b.p + i, 1);
        i++;
    }
    qotrt_prim_bytes_free(&b);
    return qotrt_str_from_qbuf(&o);
}

// ---- 进程表（世代计数；与文件表同款布局）----
struct QrtProc
{
    int64_t pid;
    bool exited;
    int64_t exit_code;
    QBuf out;
    QBuf err;
#if defined(_WIN32)
    HANDLE hproc;
    HANDLE rd_out;
    HANDLE rd_err;
#else
    int fd_out;
    int fd_err;
    bool reaped;
#endif
};

struct QrtProcSlot
{
    QrtProc *p;
    uint32_t gen;
    int32_t next_free;
};

#ifdef QOT_RT_SPLIT
extern QrtProcSlot *qotrt_ptab;
#else
static QrtProcSlot *qotrt_ptab = nullptr;
#endif
#ifdef QOT_RT_SPLIT
extern int32_t qotrt_ptab_cap;
#else
static int32_t qotrt_ptab_cap = 0;
#endif
#ifdef QOT_RT_SPLIT
extern int32_t qotrt_ptab_free;
#else
static int32_t qotrt_ptab_free = -1;
#endif

static QrtProc *qotrt_proc_of(V16 *v)
{
    if (v->t != VT_NUM)
        return nullptr;
    double d = v->u.d;
    if (!(d >= 1.0 && d < 2147483648.0) || d != static_cast<double>(static_cast<int64_t>(d)))
        return nullptr;
    int32_t h = static_cast<int32_t>(static_cast<int64_t>(d));
    if (h >= qotrt_ptab_cap || h < 1)
        return nullptr;
    return qotrt_ptab[h].p;
}

static bool qotrt_proc_alive_h(int32_t h)
{
    return h >= 1 && h < qotrt_ptab_cap && qotrt_ptab[h].p != nullptr;
}

static int32_t qotrt_ptab_add(QrtProc *p)
{
    if (qotrt_ptab_free >= 0)
    {
        int32_t h = qotrt_ptab_free;
        qotrt_ptab_free = qotrt_ptab[h].next_free;
        qotrt_ptab[h].p = p;
        return h;
    }
    if (qotrt_ptab_cap == 0)
    {
        qotrt_ptab_cap = 8;
        qotrt_ptab = static_cast<QrtProcSlot *>(
            qotrt_xalloc(sizeof(QrtProcSlot) * static_cast<size_t>(qotrt_ptab_cap)));
        qotrt_ptab[0].p = nullptr;
        qotrt_ptab[0].gen = 0;
        qotrt_ptab[0].next_free = -1;
        for (int32_t i = 1; i < qotrt_ptab_cap; i++)
        {
            qotrt_ptab[i].p = nullptr;
            qotrt_ptab[i].gen = 0;
            qotrt_ptab[i].next_free = (i == qotrt_ptab_cap - 1) ? -1 : i + 1;
        }
        qotrt_ptab_free = 1;
        return qotrt_ptab_add(p);
    }
    int32_t nc = qotrt_ptab_cap * 2;
    QrtProcSlot *nt = static_cast<QrtProcSlot *>(
        qotrt_xalloc(sizeof(QrtProcSlot) * static_cast<size_t>(nc)));
    std::memcpy(nt, qotrt_ptab, sizeof(QrtProcSlot) * static_cast<size_t>(qotrt_ptab_cap));
    qotrt_xfree(qotrt_ptab, sizeof(QrtProcSlot) * static_cast<size_t>(qotrt_ptab_cap));
    for (int32_t i = qotrt_ptab_cap; i < nc; i++)
    {
        nt[i].p = nullptr;
        nt[i].gen = 0;
        nt[i].next_free = (i == nc - 1) ? -1 : i + 1;
    }
    qotrt_ptab = nt;
    int32_t h = qotrt_ptab_cap;
    qotrt_ptab_cap = nc;
    qotrt_ptab_free = nt[h].next_free;
    nt[h].p = p;
    return h;
}

static void qotrt_ptab_remove(int32_t h)
{
    if (h < 1 || h >= qotrt_ptab_cap || qotrt_ptab[h].p == nullptr)
        return;
    QrtProc *p = qotrt_ptab[h].p;
    qotrt_ptab[h].p = nullptr;
    qotrt_ptab[h].gen++;
    qotrt_ptab[h].next_free = qotrt_ptab_free;
    qotrt_ptab_free = h;
    if (p->out.p)
        qotrt_xfree(p->out.p, p->out.cap);
    if (p->err.p)
        qotrt_xfree(p->err.p, p->err.cap);
#if defined(_WIN32)
    if (p->rd_out)
        CloseHandle(p->rd_out);
    if (p->rd_err)
        CloseHandle(p->rd_err);
    if (p->hproc)
        CloseHandle(p->hproc);
#else
    if (p->fd_out >= 0)
        close(p->fd_out);
    if (p->fd_err >= 0)
        close(p->fd_err);
#endif
    qotrt_xfree(p, sizeof(QrtProc));
}

// ---- Windows 命令行引号（MSVCRT 规则）----
static void qrt_quote_arg(QBuf *b, const char *s, int32_t n)
{
    bool need = (n == 0);
    for (int32_t i = 0; i < n && !need; i++)
        if (s[i] == ' ' || s[i] == '\t' || s[i] == '"')
            need = true;
    if (!need)
    {
        qbuf_append(b, s, static_cast<size_t>(n));
        return;
    }
    qbuf_append(b, "\"", 1);
    int32_t bs = 0;
    for (int32_t i = 0; i < n; i++)
    {
        char c = s[i];
        if (c == '\\')
        {
            bs++;
            continue;
        }
        if (c == '"')
        {
            for (int k = 0; k < bs * 2 + 1; k++)
                qbuf_append(b, "\\", 1);
            qbuf_append(b, "\"", 1);
        }
        else
        {
            for (int k = 0; k < bs; k++)
                qbuf_append(b, "\\", 1);
            qbuf_append(b, &c, 1);
        }
        bs = 0;
    }
    for (int k = 0; k < bs * 2; k++) // 尾随反斜杠（闭引号前）翻倍
        qbuf_append(b, "\\", 1);
    qbuf_append(b, "\"", 1);
}

static QrtProc *qrt_proc_spawn_impl(const char *exe, int32_t elen, const char **argv, int32_t ac)
{
    QrtProc *p = static_cast<QrtProc *>(qotrt_xalloc(sizeof(QrtProc)));
    std::memset(p, 0, sizeof(QrtProc));
    p->pid = -1;
    p->exit_code = -1;
#if defined(_WIN32)
    (void)elen;
    SECURITY_ATTRIBUTES sa;
    sa.nLength = sizeof(sa);
    sa.bInheritHandle = TRUE;
    sa.lpSecurityDescriptor = nullptr;
    HANDLE ro = nullptr, wo = nullptr, re = nullptr, we = nullptr;
    if (!CreatePipe(&ro, &wo, &sa, 0) || !CreatePipe(&re, &we, &sa, 0))
    {
        if (ro) CloseHandle(ro);
        if (wo) CloseHandle(wo);
        if (re) CloseHandle(re);
        if (we) CloseHandle(we);
        qotrt_xfree(p, sizeof(QrtProc));
        return nullptr;
    }
    SetHandleInformation(ro, HANDLE_FLAG_INHERIT, 0);
    SetHandleInformation(re, HANDLE_FLAG_INHERIT, 0);
    // 子进程 stdin = NUL（读即 EOF）
    int32_t nn = 0;
    wchar_t *nulw = qrt_u16("NUL", 3, &nn);
    HANDLE hnul = nulw ? CreateFileW(nulw, GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE,
                                     &sa, OPEN_EXISTING, 0, nullptr)
                       : nullptr;
    if (nulw)
        qrt_u16_free(nulw, nn);
    // 命令行（引号规则见 qrt_quote_arg）
    QBuf cl = {nullptr, 0, 0};
    qrt_quote_arg(&cl, exe, elen);
    for (int32_t i = 0; i < ac; i++)
    {
        qbuf_append(&cl, " ", 1);
        qrt_quote_arg(&cl, argv[i], static_cast<int32_t>(std::strlen(argv[i])));
    }
    qbuf_append(&cl, "\0", 1);
    int32_t wcl_n = 0;
    wchar_t *wcl = qrt_u16(cl.p, static_cast<int32_t>(cl.len - 1 > 0 ? cl.len - 1 : 0), &wcl_n);
    qotrt_xfree(cl.p, cl.cap);
    STARTUPINFOW si;
    std::memset(&si, 0, sizeof(si));
    si.cb = sizeof(si);
    si.dwFlags = STARTF_USESTDHANDLES;
    si.hStdInput = hnul;
    si.hStdOutput = wo;
    si.hStdError = we;
    PROCESS_INFORMATION pi;
    std::memset(&pi, 0, sizeof(pi));
    BOOL okf = wcl ? CreateProcessW(nullptr, wcl, nullptr, nullptr, TRUE, 0,
                                    nullptr, nullptr, &si, &pi)
                   : FALSE;
    if (wcl)
        qrt_u16_free(wcl, wcl_n);
    CloseHandle(wo); // 父侧写端关闭（读端见 EOF 的前提）
    CloseHandle(we);
    if (hnul)
        CloseHandle(hnul);
    if (!okf)
    {
        CloseHandle(ro);
        CloseHandle(re);
        qotrt_xfree(p, sizeof(QrtProc));
        return nullptr;
    }
    p->pid = static_cast<int64_t>(pi.dwProcessId);
    p->hproc = pi.hProcess;
    p->rd_out = ro;
    p->rd_err = re;
    CloseHandle(pi.hThread);
    return p;
#else
    (void)elen;
    int outfd[2] = {-1, -1};
    int errfd[2] = {-1, -1};
    if (pipe(outfd) != 0 || pipe(errfd) != 0)
    {
        if (outfd[0] >= 0) { close(outfd[0]); close(outfd[1]); }
        if (errfd[0] >= 0) { close(errfd[0]); close(errfd[1]); }
        qotrt_xfree(p, sizeof(QrtProc));
        return nullptr;
    }
    int devnull = open("/dev/null", O_RDONLY);
    pid_t pid = fork();
    if (pid < 0)
    {
        if (devnull >= 0) close(devnull);
        close(outfd[0]); close(outfd[1]);
        close(errfd[0]); close(errfd[1]);
        qotrt_xfree(p, sizeof(QrtProc));
        return nullptr;
    }
    if (pid == 0)
    {
        dup2(devnull >= 0 ? devnull : open("/dev/null", O_RDONLY), 0);
        dup2(outfd[1], 1);
        dup2(errfd[1], 2);
        close(outfd[0]); close(outfd[1]);
        close(errfd[0]); close(errfd[1]);
        char *argv2[4096];
        int32_t n = ac < 4095 ? ac : 4095;
        for (int32_t i = 0; i < n; i++)
            argv2[i] = const_cast<char *>(argv[i]);
        argv2[n] = nullptr;
        execvp(exe, argv2);
        _exit(127);
    }
    if (devnull >= 0)
        close(devnull);
    close(outfd[1]);
    close(errfd[1]);
    // 非阻塞：等待循环用 poll 驱动，read 永不卡死主线程
    fcntl(outfd[0], F_SETFL, fcntl(outfd[0], F_GETFL) | O_NONBLOCK);
    fcntl(errfd[0], F_SETFL, fcntl(errfd[0], F_GETFL) | O_NONBLOCK);
    p->pid = static_cast<int64_t>(pid);
    p->fd_out = outfd[0];
    p->fd_err = errfd[0];
    return p;
#endif
}

#if defined(_WIN32)
static void qrt_drain_pipe(HANDLE h, QBuf *b)
{
    for (;;)
    {
        DWORD avail = 0;
        if (!PeekNamedPipe(h, nullptr, 0, nullptr, &avail, nullptr) || avail == 0)
            return;
        char tmp[4096];
        DWORD rd = 0;
        if (!ReadFile(h, tmp, avail < sizeof(tmp) ? avail : sizeof(tmp), &rd, nullptr) || rd == 0)
            return;
        qbuf_append(b, tmp, rd);
    }
}
#endif

#if !defined(_WIN32)
static void qrt_drain_fd(int fd, QBuf *b)
{
    for (;;)
    {
        char tmp[4096];
        ssize_t r = read(fd, tmp, sizeof(tmp));
        if (r <= 0)
            return;
        qbuf_append(b, tmp, static_cast<size_t>(r));
    }
}
#endif

// 等待进程结束（至多 timeout_ms；<0 = 无限）。返回 true = 已退出。
static bool qrt_proc_wait_impl(QrtProc *p, int64_t timeout_ms)
{
    if (p->exited)
        return true;
#if defined(_WIN32)
    auto t0 = std::chrono::steady_clock::now();
    for (;;)
    {
        qrt_drain_pipe(p->rd_out, &p->out);
        qrt_drain_pipe(p->rd_err, &p->err);
        DWORD w = WaitForSingleObject(p->hproc, 5);
        if (w == WAIT_OBJECT_0)
        {
            // 终局排水：进程已死，读到管道断/空为止
            for (;;)
            {
                DWORD a1 = 0, a2 = 0;
                BOOL p1 = PeekNamedPipe(p->rd_out, nullptr, 0, nullptr, &a1, nullptr);
                BOOL p2 = PeekNamedPipe(p->rd_err, nullptr, 0, nullptr, &a2, nullptr);
                if ((!p1 || a1 == 0) && (!p2 || a2 == 0))
                    break;
                qrt_drain_pipe(p->rd_out, &p->out);
                qrt_drain_pipe(p->rd_err, &p->err);
            }
            DWORD code = 0;
            GetExitCodeProcess(p->hproc, &code);
            p->exit_code = static_cast<int64_t>(code);
            p->exited = true;
            return true;
        }
        if (timeout_ms >= 0)
        {
            double el = std::chrono::duration<double>(
                            std::chrono::steady_clock::now() - t0)
                            .count() *
                        1000.0;
            if (el >= static_cast<double>(timeout_ms))
                return false;
        }
    }
#else
    int64_t waited = 0;
    for (;;)
    {
        // 非阻塞读（O_NONBLOCK 才能轮询；这里以 poll 驱动）
        struct pollfd fds[2];
        fds[0].fd = p->fd_out;
        fds[1].fd = p->fd_err;
        fds[0].events = POLLIN;
        fds[1].events = POLLIN;
        int slice = (timeout_ms >= 0 && timeout_ms - waited < 5) ? static_cast<int>(timeout_ms - waited) : 5;
        if (slice < 0)
            slice = 0;
        int pr = poll(fds, 2, slice);
        if (pr > 0)
        {
            if (fds[0].revents & (POLLIN | POLLHUP))
                qrt_drain_fd(p->fd_out, &p->out);
            if (fds[1].revents & (POLLIN | POLLHUP))
                qrt_drain_fd(p->fd_err, &p->err);
        }
        int st = 0;
        pid_t r = waitpid(static_cast<pid_t>(p->pid), &st, WNOHANG);
        if (r == static_cast<pid_t>(p->pid))
        {
            // 终局排水（进程死 → 所有写已完成，读到 EOF 为止）
            for (;;)
            {
                char tmp[4096];
                ssize_t r1 = read(p->fd_out, tmp, sizeof(tmp));
                ssize_t r2 = read(p->fd_err, tmp, sizeof(tmp));
                if (r1 <= 0 && r2 <= 0)
                    break;
                if (r1 > 0)
                    qbuf_append(&p->out, tmp, static_cast<size_t>(r1));
                if (r2 > 0)
                    qbuf_append(&p->err, tmp, static_cast<size_t>(r2));
            }
            p->reaped = true;
            p->exited = true;
            p->exit_code = WIFEXITED(st) ? WEXITSTATUS(st)
                                         : (WIFSIGNALED(st) ? 128 + WTERMSIG(st) : -1);
            return true;
        }
        if (timeout_ms >= 0)
        {
            waited += slice;
            if (waited >= timeout_ms)
                return false;
        }
    }
#endif
}

static V16 qotrt_proc_result_dict(QrtProc *p)
{
    V16 d = qotrt_dict_new();
    QDict *dict = static_cast<QDict *>(d.u.p);
    struct
    {
        const char *k;
        V16 v;
    } fields[3] = {
        {"code", qotrt_num(static_cast<double>(p->exit_code))},
        {"stdout", qotrt_str_from_qbuf(&p->out)}, // 缓冲所有权移交
        {"stderr", qotrt_str_from_qbuf(&p->err)},
    };
    for (int i = 0; i < 3; i++)
        *qotrt_dict_at(dict, fields[i].k, static_cast<int32_t>(std::strlen(fields[i].k)),
                       qotrt_hash(fields[i].k, std::strlen(fields[i].k))) = fields[i].v;
    return d;
}

static int32_t qrt_spawn_argv(const char *exe, int32_t elen, const char **argv, int32_t ac)
{
    QrtProc *p = qrt_proc_spawn_impl(exe, elen, argv, ac);
    if (!p)
        return -1;
    return qotrt_ptab_add(p);
}

static V16 qotrt_proc_run(V16 *args, int argc)
{
    if (argc < 2)
        return qotrt_null_v();
    QrtBytes eb;
    qotrt_prim_bytes(&args[0], &eb);
    QArr *aa = qotrt_arr_of(&args[1]);
    const char *argv[256];
    char *store[256];
    int32_t ac = 0;
    if (aa)
    {
        for (int32_t i = 0; i < aa->len && ac < 255; i++)
        {
            QrtBytes ab;
            qotrt_prim_bytes(&aa->a[static_cast<size_t>(i)], &ab);
            char *cp = qotrt_strdup(ab.p, ab.len);
            qotrt_prim_bytes_free(&ab);
            store[ac] = cp;
            argv[ac] = cp;
            ac++;
        }
    }
    // opts（第 3 参）按 R15 预留：策略（超时/环境/目录）进标准库，原语不内置
    int32_t h = qrt_spawn_argv(eb.p, eb.len, argv, ac);
    qotrt_prim_bytes_free(&eb);
    for (int32_t i = 0; i < ac; i++)
        qotrt_xfree(store[i], std::strlen(store[i]) + 1);
    if (h < 0)
        return qotrt_null_v();
    QrtProc *p = qotrt_ptab[h].p;
    qrt_proc_wait_impl(p, -1);
    V16 d = qotrt_proc_result_dict(p);
    qotrt_ptab_remove(h);
    return d;
}

static V16 qotrt_proc_shell(V16 *args, int argc)
{
    if (argc < 1)
        return qotrt_null_v();
    QrtBytes cb;
    qotrt_prim_bytes(&args[0], &cb);
    char *cmd = qotrt_strdup(cb.p, cb.len);
    qotrt_prim_bytes_free(&cb);
    const char *argv[4];
    int32_t ac = 0;
#if defined(_WIN32)
    char comspec[512];
    const char *cs = std::getenv("ComSpec");
    if (!cs || !*cs)
        cs = "cmd.exe";
    size_t csl = std::strlen(cs);
    if (csl > 500)
        csl = 500;
    std::memcpy(comspec, cs, csl);
    comspec[csl] = 0;
    argv[ac++] = comspec;
    argv[ac++] = "/c";
#else
    argv[ac++] = const_cast<char *>("/bin/sh");
    argv[ac++] = const_cast<char *>("-c");
#endif
    argv[ac++] = cmd;
    int32_t h = qrt_spawn_argv(argv[0], static_cast<int32_t>(std::strlen(argv[0])), argv, ac);
    qotrt_xfree(cmd, std::strlen(cmd) + 1);
    if (h < 0)
        return qotrt_null_v();
    QrtProc *p = qotrt_ptab[h].p;
    qrt_proc_wait_impl(p, -1);
    V16 d = qotrt_proc_result_dict(p);
    qotrt_ptab_remove(h);
    return d;
}

static V16 qotrt_proc_spawn(V16 *args, int argc)
{
    if (argc < 2)
        return qotrt_null_v();
    QrtBytes eb;
    qotrt_prim_bytes(&args[0], &eb);
    QArr *aa = qotrt_arr_of(&args[1]);
    const char *argv[256];
    char *store[256];
    int32_t ac = 0;
    if (aa)
    {
        for (int32_t i = 0; i < aa->len && ac < 255; i++)
        {
            QrtBytes ab;
            qotrt_prim_bytes(&aa->a[static_cast<size_t>(i)], &ab);
            char *cp = qotrt_strdup(ab.p, ab.len);
            qotrt_prim_bytes_free(&ab);
            store[ac] = cp;
            argv[ac] = cp;
            ac++;
        }
    }
    int32_t h = qrt_spawn_argv(eb.p, eb.len, argv, ac);
    qotrt_prim_bytes_free(&eb);
    for (int32_t i = 0; i < ac; i++)
        qotrt_xfree(store[i], std::strlen(store[i]) + 1);
    if (h < 0)
        return qotrt_null_v();
    return qotrt_num(static_cast<double>(h));
}

static V16 qotrt_proc_wait(V16 *args, int argc)
{
    if (argc < 2)
        return qotrt_null_v();
    QrtProc *p = qotrt_proc_of(&args[0]);
    if (!p)
        return qotrt_null_v();
    bool ok;
    int64_t tmo = qotrt_prim_i64(&args[1], &ok);
    if (!ok)
        return qotrt_null_v();
    if (!qrt_proc_wait_impl(p, tmo))
        return qotrt_null_v(); // 超时：句柄继续有效，可重试
    int32_t h = static_cast<int32_t>(static_cast<int64_t>(qotrt_prim_num(&args[0])));
    V16 d = qotrt_proc_result_dict(p);
    qotrt_ptab_remove(h);
    return d;
}

static V16 qotrt_proc_kill(V16 *args, int argc)
{
    if (argc < 1)
        return qotrt_bool(false);
    QrtProc *p = qotrt_proc_of(&args[0]);
    if (!p || p->exited)
        return qotrt_bool(false);
#if defined(_WIN32)
    return qotrt_bool(TerminateProcess(p->hproc, 1) != 0);
#else
    return qotrt_bool(kill(static_cast<pid_t>(p->pid), SIGKILL) == 0);
#endif
}

static V16 qotrt_proc_pid(V16 *args, int argc)
{
    if (argc < 1)
        return qotrt_num(-1.0);
    QrtProc *p = qotrt_proc_of(&args[0]);
    if (!p)
        return qotrt_num(-1.0);
    return qotrt_num(static_cast<double>(p->pid));
}

static V16 qotrt_proc_is_running(V16 *args, int argc)
{
    if (argc < 1)
        return qotrt_bool(false);
    QrtProc *p = qotrt_proc_of(&args[0]);
    if (!p)
        return qotrt_bool(false);
    if (p->exited)
        return qotrt_bool(false);
#if defined(_WIN32)
    if (WaitForSingleObject(p->hproc, 0) == WAIT_OBJECT_0)
    {
        DWORD code = 0;
        GetExitCodeProcess(p->hproc, &code);
        p->exit_code = static_cast<int64_t>(code);
        p->exited = true;
        return qotrt_bool(false);
    }
    return qotrt_bool(true);
#else
    int st = 0;
    pid_t r = waitpid(static_cast<pid_t>(p->pid), &st, WNOHANG);
    if (r == static_cast<pid_t>(p->pid))
    {
        p->reaped = true;
        p->exited = true;
        p->exit_code = WIFEXITED(st) ? WEXITSTATUS(st)
                                     : (WIFSIGNALED(st) ? 128 + WTERMSIG(st) : -1);
        return qotrt_bool(false);
    }
    return qotrt_bool(true);
#endif
}

static V16 qotrt_proc_exit_code(V16 *args, int argc)
{
    if (argc < 1)
        return qotrt_num(-1.0);
    QrtProc *p = qotrt_proc_of(&args[0]);
    if (!p)
        return qotrt_num(-1.0);
    if (!p->exited)
    {
        // 未等待先问码：尽力探测一次（非阻塞）
#if defined(_WIN32)
        if (WaitForSingleObject(p->hproc, 0) == WAIT_OBJECT_0)
        {
            DWORD code = 0;
            GetExitCodeProcess(p->hproc, &code);
            p->exit_code = static_cast<int64_t>(code);
            p->exited = true;
        }
#else
        int st = 0;
        pid_t r = waitpid(static_cast<pid_t>(p->pid), &st, WNOHANG);
        if (r == static_cast<pid_t>(p->pid))
        {
            p->reaped = true;
            p->exited = true;
            p->exit_code = WIFEXITED(st) ? WEXITSTATUS(st)
                                         : (WIFSIGNALED(st) ? 128 + WTERMSIG(st) : -1);
        }
#endif
    }
    return qotrt_num(p->exited ? static_cast<double>(p->exit_code) : -1.0);
}

// ---- cwd / hostname / username ----
static V16 qotrt_cwd(V16 *args, int argc)
{
    (void)args;
    (void)argc;
    char buf[4096];
#if defined(_WIN32)
    if (!_getcwd(buf, sizeof(buf)))
        return qotrt_str("", 0);
#else
    if (!getcwd(buf, sizeof(buf)))
        return qotrt_str("", 0);
#endif
    return qotrt_str(buf, static_cast<int32_t>(std::strlen(buf)));
}

static V16 qotrt_cwd_set(V16 *args, int argc)
{
    if (argc < 1)
        return qotrt_bool(false);
    QrtBytes b;
    qotrt_prim_bytes(&args[0], &b);
    char path[4096];
    int32_t pl = b.len < 4095 ? b.len : 4095;
    std::memcpy(path, b.p, static_cast<size_t>(pl));
    path[pl] = 0;
    qotrt_prim_bytes_free(&b);
#if defined(_WIN32)
    return qotrt_bool(_chdir(path) == 0);
#else
    return qotrt_bool(chdir(path) == 0);
#endif
}

static V16 qotrt_hostname(V16 *args, int argc)
{
    (void)args;
    (void)argc;
#if defined(_WIN32)
    wchar_t w[MAX_COMPUTERNAME_LENGTH + 1];
    DWORD n = MAX_COMPUTERNAME_LENGTH + 1;
    if (!GetComputerNameW(w, &n))
        return qotrt_str("", 0);
    int need = WideCharToMultiByte(CP_UTF8, 0, w, static_cast<int>(n),
                                   nullptr, 0, nullptr, nullptr);
    if (need <= 0)
        return qotrt_str("", 0);
    QBuf o = {nullptr, 0, 0};
    qbuf_need(&o, static_cast<size_t>(need));
    WideCharToMultiByte(CP_UTF8, 0, w, static_cast<int>(n), o.p, need, nullptr, nullptr);
    o.len = static_cast<size_t>(need);
    return qotrt_str_from_qbuf(&o);
#else
    char buf[256];
    if (gethostname(buf, sizeof(buf) - 1) != 0)
        return qotrt_str("", 0);
    buf[sizeof(buf) - 1] = 0;
    return qotrt_str(buf, static_cast<int32_t>(std::strlen(buf)));
#endif
}

// QOTNative：懒探测缓存提升到文件作用域（原为函数内 static，宏翻转安全化）
#if defined(_WIN32)
typedef BOOL(WINAPI * GetUserNameWFn)(LPWSTR, LPDWORD);
static GetUserNameWFn qotrt_username_fn = nullptr;
static bool qotrt_username_probed = false;
#endif

static V16 qotrt_username(V16 *args, int argc)
{
    (void)args;
    (void)argc;
#if defined(_WIN32)
    // advapi32 动态加载（不加链接依赖）；失败回落 %USERNAME%
    if (!qotrt_username_probed)
    {
        qotrt_username_probed = true;
        HMODULE h = LoadLibraryA("advapi32.dll");
        if (h)
            qotrt_username_fn = reinterpret_cast<GetUserNameWFn>(
                reinterpret_cast<void *>(GetProcAddress(h, "GetUserNameW")));
    }
    GetUserNameWFn fn = qotrt_username_fn;
    if (fn)
    {
        wchar_t w[257];
        DWORD n = 257;
        if (fn(w, &n))
        {
            int need = WideCharToMultiByte(CP_UTF8, 0, w, -1, nullptr, 0, nullptr, nullptr);
            if (need > 1)
            {
                QBuf o = {nullptr, 0, 0};
                qbuf_need(&o, static_cast<size_t>(need));
                WideCharToMultiByte(CP_UTF8, 0, w, -1, o.p, need, nullptr, nullptr);
                o.len = static_cast<size_t>(need - 1);
                return qotrt_str_from_qbuf(&o);
            }
        }
    }
    const char *u = std::getenv("USERNAME");
    if (!u)
        return qotrt_str("", 0);
    return qotrt_str(u, static_cast<int32_t>(std::strlen(u)));
#else
    const char *u = std::getenv("USER");
    if (!u || !*u)
        u = std::getenv("LOGNAME");
    if (u && *u)
        return qotrt_str(u, static_cast<int32_t>(std::strlen(u)));
    uid_t uid = geteuid();
    struct passwd *pw = getpwuid(uid);
    if (pw && pw->pw_name)
        return qotrt_str(pw->pw_name, static_cast<int32_t>(std::strlen(pw->pw_name)));
    return qotrt_str("", 0);
#endif
}

// ---- 三条流原语（不经 output 的格式化；字节语义）----
static V16 qotrt_stdout_write(V16 *args, int argc)
{
    if (argc < 1)
        return qotrt_num(-1.0);
    QrtBytes b;
    qotrt_prim_bytes(&args[0], &b);
    size_t n = b.len > 0 ? std::fwrite(b.p, 1, static_cast<size_t>(b.len), stdout) : 0;
    std::fflush(stdout);
    qotrt_prim_bytes_free(&b);
    return qotrt_num(static_cast<double>(static_cast<int64_t>(n)));
}

static V16 qotrt_stderr_write(V16 *args, int argc)
{
    if (argc < 1)
        return qotrt_num(-1.0);
    QrtBytes b;
    qotrt_prim_bytes(&args[0], &b);
    size_t n = b.len > 0 ? std::fwrite(b.p, 1, static_cast<size_t>(b.len), stderr) : 0;
    std::fflush(stderr);
    qotrt_prim_bytes_free(&b);
    return qotrt_num(static_cast<double>(static_cast<int64_t>(n)));
}

static V16 qotrt_stdin_read_line(V16 *args, int argc)
{
    (void)args;
    (void)argc;
    QBuf o = {nullptr, 0, 0};
    int c;
    while ((c = std::fgetc(stdin)) != EOF)
    {
        if (c == '\n')
            break;
        char ch = static_cast<char>(c);
        qbuf_append(&o, &ch, 1);
    }
    // 行尾 \r 剥除（CRLF 输入归一为 LF 语义，两侧一致）
    if (o.len > 0 && o.p[o.len - 1] == '\r')
        o.len--;
    if (o.p)
        o.p[o.len] = 0;
    return qotrt_str_from_qbuf(&o);
}

// ---------------------------------------------------------------------------
// K 组 · 类型与错误
// ---------------------------------------------------------------------------
// 句柄存活判定（文件表 + 进程表；§4.0 句柄=数字的固有模糊性：恰好等于
// 活跃句柄值的普通数字也会被判成 handle —— 可观测口径，两侧镜像一致）。
// FFI 句柄表（L 组，定义在文件尾）——前置声明供句柄存活判定
struct QrtFfiLib;
struct QrtFfiSym;
static QrtFfiLib *qotrt_ffi_lib_of(V16 *v);
static QrtFfiSym *qotrt_ffi_sym_of(V16 *v);
struct QrtSock;
static QrtSock *qotrt_sock_of(V16 *v);
// 插件表（L2 组，定义在 FFI 节之后）——同款前置声明
struct QrtPlugSlot;
static QrtPlugSlot *qotrt_plug_of(V16 *v);

static bool qotrt_handle_alive(V16 *v)
{
    if (qotrt_file_of(v))
        return true;
    if (qotrt_proc_of(v))
        return true;
    if (qotrt_ffi_lib_of(v))
        return true;
    if (qotrt_ffi_sym_of(v))
        return true;
    if (qotrt_sock_of(v))
        return true;
    if (qotrt_plug_of(v))
        return true;
    return false;
}

static const char *qotrt_type_name(V16 *v)
{
    if (v->t == VT_NUM && qotrt_handle_alive(v))
        return "handle";
    switch (v->t)
    {
    case VT_NUM:
        return "number";
    case VT_STR:
        return "string";
    case VT_BOOL:
        return "boolean";
    case VT_ARR:
        return "array";
    case VT_DICT:
        return "dict";
    case VT_NULL:
    case VT_NONE:
        return "null";
    case VT_CLOSURE:
        return "function";
    case VT_ERR:
        return "error";
    default:
        return "unknown";
    }
}

static V16 qotrt_type_of(V16 *args, int argc)
{
    if (argc < 1)
        return qotrt_str("", 0);
    const char *t = qotrt_type_name(&args[0]);
    return qotrt_str(t, static_cast<int32_t>(std::strlen(t)));
}

static V16 qotrt_type_is(V16 *args, int argc)
{
    if (argc < 2)
        return qotrt_bool(false);
    QrtBytes b;
    qotrt_prim_bytes(&args[1], &b);
    char name[64];
    int32_t nl = b.len < 63 ? b.len : 63;
    std::memcpy(name, b.p, static_cast<size_t>(nl));
    name[nl] = 0;
    qotrt_prim_bytes_free(&b);
    return qotrt_bool(std::strcmp(qotrt_type_name(&args[0]), name) == 0);
}

static V16 qotrt_is_null(V16 *args, int argc)
{
    if (argc < 1)
        return qotrt_bool(true);
    return qotrt_bool(args[0].t == VT_NULL || args[0].t == VT_NONE);
}

static V16 qotrt_is_array(V16 *args, int argc)
{
    if (argc < 1)
        return qotrt_bool(false);
    return qotrt_bool(args[0].t == VT_ARR);
}

static V16 qotrt_is_dict(V16 *args, int argc)
{
    if (argc < 1)
        return qotrt_bool(false);
    return qotrt_bool(args[0].t == VT_DICT);
}

static V16 qotrt_is_string(V16 *args, int argc)
{
    if (argc < 1)
        return qotrt_bool(false);
    return qotrt_bool(args[0].t == VT_STR);
}

static V16 qotrt_is_bool(V16 *args, int argc)
{
    if (argc < 1)
        return qotrt_bool(false);
    return qotrt_bool(args[0].t == VT_BOOL);
}

static V16 qotrt_is_handle(V16 *args, int argc)
{
    if (argc < 1)
        return qotrt_bool(false);
    return qotrt_bool(qotrt_handle_alive(&args[0]));
}

// 数值化：NUM/BOOL 原样；其余按 tostring 后走 str_to_num 的同一严格解析
// （qrt_parse_num：裁空白 + 全串消耗），不可解析 -1 —— 与 num_is_valid 同族口径。
static V16 qotrt_to_num(V16 *args, int argc)
{
    if (argc < 1)
        return qotrt_num(-1.0);
    if (args[0].t == VT_NUM)
        return args[0];
    if (args[0].t == VT_BOOL)
        return qotrt_num(args[0].u.d);
    QBuf b = {nullptr, 0, 0};
    qotrt_tostring(&args[0], &b);
    bool ok;
    double d = qrt_parse_num(b.p ? b.p : "", static_cast<int32_t>(b.len), &ok);
    qotrt_xfree(b.p, b.cap);
    return ok ? qotrt_num(d) : qotrt_num(-1.0);
}

// 布尔化 = 语言条件判定口径（同 qrt_truthy_of，err/闭包/容器为真）
static V16 qotrt_to_bool(V16 *args, int argc)
{
    if (argc < 1)
        return qotrt_bool(false);
    return qotrt_bool(qrt_truthy_of(&args[0]));
}

// 数组化：数组原样；null → null（无意义）；其余标量 → [v]
static V16 qotrt_to_array(V16 *args, int argc)
{
    if (argc < 1)
        return qotrt_null_v();
    if (args[0].t == VT_ARR)
        return args[0];
    if (args[0].t == VT_NULL || args[0].t == VT_NONE)
        return qotrt_null_v();
    V16 out = qotrt_arr_new(4);
    QArr *a = static_cast<QArr *>(out.u.p);
    qotrt_arr_push(a, args[0]);
    return out;
}


#if !defined(_WIN32)
#include <dlfcn.h>
#endif

struct QrtFfiLib
{
#if defined(_WIN32)
    HMODULE mod;
#else
    void *mod;
#endif
};

struct QrtFfiLibSlot
{
    QrtFfiLib *p;
    uint32_t gen;
    int32_t next_free;
};

#ifdef QOT_RT_SPLIT
extern QrtFfiLibSlot *qotrt_ffitab;
#else
static QrtFfiLibSlot *qotrt_ffitab = nullptr;
#endif
#ifdef QOT_RT_SPLIT
extern int32_t qotrt_ffitab_cap;
#else
static int32_t qotrt_ffitab_cap = 0;
#endif
#ifdef QOT_RT_SPLIT
extern int32_t qotrt_ffitab_free;
#else
static int32_t qotrt_ffitab_free = -1;
#endif

struct QrtFfiSym
{
    void *fn;      // 函数地址（farproc）
    int32_t lib;   // 所属 lib 槽下标（ffi_free 时按它批量失效）
    // 【PROMAX-F1】类型数组解析缓存：typed ffi_call 的逐项 prim_bytes +
    // memcpy + NUL 终结是每次调用的固定税（类型串不变时纯浪费）。键 =
    // QArr 对象指针 + 元素 QStr 指针串（字符串不可变 ⇒ 指针相同 ⇒ 内容
    // 相同），比对 8 次指针等价于免全部解析。
    const QArr *tc_arr;   // 上次的类型数组对象（nullptr = 无缓存）
    const void *tc_ptrs[9];
    int32_t tc_n;
    char tc_codes[9][16];
    int tc_ret_fam;
};

struct QrtFfiSymSlot
{
    QrtFfiSym *p;
    uint32_t gen;
    int32_t next_free;
};

#ifdef QOT_RT_SPLIT
extern QrtFfiSymSlot *qotrt_fsymtab;
#else
static QrtFfiSymSlot *qotrt_fsymtab = nullptr;
#endif
#ifdef QOT_RT_SPLIT
extern int32_t qotrt_fsymtab_cap;
#else
static int32_t qotrt_fsymtab_cap = 0;
#endif
#ifdef QOT_RT_SPLIT
extern int32_t qotrt_fsymtab_free;
#else
static int32_t qotrt_fsymtab_free = -1;
#endif

// 上一次 FFI 错误（固定缓冲；单线程假设同全运行时）
#ifdef QOT_RT_SPLIT
extern char qotrt_ffi_err[256];
#else
static char qotrt_ffi_err[256];
#endif

static void qotrt_ffi_seterr(const char *msg)
{
    if (!msg)
        msg = "";
    size_t i = 0;
    for (; msg[i] && i < sizeof(qotrt_ffi_err) - 1; i++)
        qotrt_ffi_err[i] = msg[i];
    qotrt_ffi_err[i] = 0;
}

static void qotrt_ffi_clearerr(void)
{
    qotrt_ffi_err[0] = 0;
}

// 槽表（文件表同款：0 恒无效；add 复用空闲链；remove gen++ 断复用）
static int32_t qotrt_ffitab_add(QrtFfiLib *p)
{
    if (qotrt_ffitab_free >= 0)
    {
        int32_t h = qotrt_ffitab_free;
        qotrt_ffitab_free = qotrt_ffitab[h].next_free;
        qotrt_ffitab[h].p = p;
        return h;
    }
    if (qotrt_ffitab_cap == 0)
    {
        qotrt_ffitab_cap = 8;
        qotrt_ffitab = static_cast<QrtFfiLibSlot *>(
            qotrt_xalloc(sizeof(QrtFfiLibSlot) * static_cast<size_t>(qotrt_ffitab_cap)));
        for (int32_t i = 0; i < qotrt_ffitab_cap; i++)
        {
            qotrt_ffitab[i].p = nullptr;
            qotrt_ffitab[i].gen = 0;
            qotrt_ffitab[i].next_free = (i == qotrt_ffitab_cap - 1) ? -1 : i + 1;
        }
        qotrt_ffitab_free = 1; // 槽 0 恒无效
    }
    else
    {
        int32_t old_cap = qotrt_ffitab_cap;
        int32_t nc = qotrt_ffitab_cap * 2;
        QrtFfiLibSlot *nt = static_cast<QrtFfiLibSlot *>(
            qotrt_xalloc(sizeof(QrtFfiLibSlot) * static_cast<size_t>(nc)));
        std::memcpy(nt, qotrt_ffitab, sizeof(QrtFfiLibSlot) * static_cast<size_t>(old_cap));
        qotrt_xfree(qotrt_ffitab, sizeof(QrtFfiLibSlot) * static_cast<size_t>(old_cap));
        for (int32_t i = old_cap; i < nc; i++)
        {
            nt[i].p = nullptr;
            nt[i].gen = 0;
            nt[i].next_free = (i == nc - 1) ? -1 : i + 1;
        }
        qotrt_ffitab = nt;
        qotrt_ffitab_cap = nc;
        // 【P0-FFI-TAB】扩容后必须重置自由链头：新槽 [old_cap, nc) 才是
        // 空闲区。旧实现漏了这一步 —— qotrt_ffitab_free 仍为 -1，
        // 下面 table[-1] 越界读写 + 返回句柄 -1（坏句柄）。
        qotrt_ffitab_free = old_cap;
    }
    int32_t h = qotrt_ffitab_free;
    qotrt_ffitab_free = qotrt_ffitab[h].next_free;
    qotrt_ffitab[h].p = p;
    return h;
}

static int32_t qotrt_fsymtab_add(QrtFfiSym *p)
{
    if (qotrt_fsymtab_free >= 0)
    {
        int32_t h = qotrt_fsymtab_free;
        qotrt_fsymtab_free = qotrt_fsymtab[h].next_free;
        qotrt_fsymtab[h].p = p;
        return h;
    }
    if (qotrt_fsymtab_cap == 0)
    {
        qotrt_fsymtab_cap = 16;
        qotrt_fsymtab = static_cast<QrtFfiSymSlot *>(
            qotrt_xalloc(sizeof(QrtFfiSymSlot) * static_cast<size_t>(qotrt_fsymtab_cap)));
        for (int32_t i = 0; i < qotrt_fsymtab_cap; i++)
        {
            qotrt_fsymtab[i].p = nullptr;
            qotrt_fsymtab[i].gen = 0;
            qotrt_fsymtab[i].next_free = (i == qotrt_fsymtab_cap - 1) ? -1 : i + 1;
        }
        qotrt_fsymtab_free = 1;
    }
    else
    {
        int32_t old_cap = qotrt_fsymtab_cap;
        int32_t nc = qotrt_fsymtab_cap * 2;
        QrtFfiSymSlot *nt = static_cast<QrtFfiSymSlot *>(
            qotrt_xalloc(sizeof(QrtFfiSymSlot) * static_cast<size_t>(nc)));
        std::memcpy(nt, qotrt_fsymtab, sizeof(QrtFfiSymSlot) * static_cast<size_t>(old_cap));
        qotrt_xfree(qotrt_fsymtab, sizeof(QrtFfiSymSlot) * static_cast<size_t>(old_cap));
        for (int32_t i = old_cap; i < nc; i++)
        {
            nt[i].p = nullptr;
            nt[i].gen = 0;
            nt[i].next_free = (i == nc - 1) ? -1 : i + 1;
        }
        qotrt_fsymtab = nt;
        qotrt_fsymtab_cap = nc;
           qotrt_fsymtab_free = old_cap;
    }
    int32_t h = qotrt_fsymtab_free;
    qotrt_fsymtab_free = qotrt_fsymtab[h].next_free;
    qotrt_fsymtab[h].p = p;
    return h;
}

static QrtFfiLib *qotrt_ffi_lib_of(V16 *v)
{
    if (v->t != VT_NUM)
        return nullptr;
    double d = v->u.d;
    if (!(d >= 1.0 && d < 2147483648.0) || d != static_cast<double>(static_cast<int64_t>(d)))
        return nullptr;
    int32_t h = static_cast<int32_t>(static_cast<int64_t>(d));
    if (h < 1 || h >= qotrt_ffitab_cap)
        return nullptr;
    return qotrt_ffitab[h].p;
}

static QrtFfiSym *qotrt_ffi_sym_of(V16 *v)
{
    if (v->t != VT_NUM)
        return nullptr;
    double d = v->u.d;
    if (!(d >= 1.0 && d < 2147483648.0) || d != static_cast<double>(static_cast<int64_t>(d)))
        return nullptr;
    int32_t h = static_cast<int32_t>(static_cast<int64_t>(d));
    if (h < 1 || h >= qotrt_fsymtab_cap)
        return nullptr;
    return qotrt_fsymtab[h].p;
}

static void qotrt_ffi_lib_slot_remove(int32_t h)
{
    if (h < 1 || h >= qotrt_ffitab_cap || qotrt_ffitab[h].p == nullptr)
        return;
    QrtFfiLib *p = qotrt_ffitab[h].p;
    qotrt_ffitab[h].p = nullptr;
    qotrt_ffitab[h].gen++;
    qotrt_ffitab[h].next_free = qotrt_ffitab_free;
    qotrt_ffitab_free = h;
    qotrt_xfree(p, sizeof(QrtFfiLib));
}

static void qotrt_ffi_sym_slot_remove(int32_t h)
{
    if (h < 1 || h >= qotrt_fsymtab_cap || qotrt_fsymtab[h].p == nullptr)
        return;
    QrtFfiSym *p = qotrt_fsymtab[h].p;
    qotrt_fsymtab[h].p = nullptr;
    qotrt_fsymtab[h].gen++;
    qotrt_fsymtab[h].next_free = qotrt_fsymtab_free;
    qotrt_fsymtab_free = h;
    qotrt_xfree(p, sizeof(QrtFfiSym));
}

// ffi_abi_version()：v2 = M7 冻结（混族分派器 + 原生插件 ABI，docs/）。
// 插件按它判兼容；后续语义/布局变更必须 bump。
static V16 qotrt_ffi_abi_version(V16 *args, int argc)
{
    (void)args;
    (void)argc;
    return qotrt_num(2.0);
}

static V16 qotrt_ffi_load(V16 *args, int argc)
{
    qotrt_ffi_clearerr();
    if (argc < 1)
    {
        qotrt_ffi_seterr("ffi_load: missing path");
        return qotrt_null_v();
    }
    QrtBytes b;
    qotrt_prim_bytes(&args[0], &b);
    if (!b.p || b.len == 0)
    {
        qotrt_ffi_seterr("ffi_load: empty path");
        return qotrt_null_v();
    }
#if defined(_WIN32)
    int32_t wn = 0;
    wchar_t *w = qrt_u16(b.p, b.len, &wn);
    qotrt_prim_bytes_free(&b);
    if (!w)
    {
        qotrt_ffi_seterr("ffi_load: path encoding");
        return qotrt_null_v();
    }
    HMODULE m = LoadLibraryW(w);
    qrt_u16_free(w, wn);
    if (!m)
    {
        qotrt_ffi_seterr("ffi_load: LoadLibrary failed");
        return qotrt_null_v();
    }
#else
    char *pathz = static_cast<char *>(qotrt_xalloc(static_cast<size_t>(b.len) + 1));
    std::memcpy(pathz, b.p, static_cast<size_t>(b.len));
    pathz[b.len] = 0;
    qotrt_prim_bytes_free(&b);
    void *m = dlopen(pathz, RTLD_NOW | RTLD_LOCAL);
    qotrt_xfree(pathz, static_cast<size_t>(b.len) + 1);
    if (!m)
    {
        const char *e = dlerror();
        qotrt_ffi_seterr(e ? e : "ffi_load: dlopen failed");
        return qotrt_null_v();
    }
#endif
    QrtFfiLib *p = static_cast<QrtFfiLib *>(qotrt_xalloc(sizeof(QrtFfiLib)));
    p->mod = m;
    return qotrt_num(static_cast<double>(qotrt_ffitab_add(p)));
}

static V16 qotrt_ffi_sym(V16 *args, int argc)
{
    qotrt_ffi_clearerr();
    if (argc < 2)
    {
        qotrt_ffi_seterr("ffi_sym: missing args");
        return qotrt_null_v();
    }
    QrtFfiLib *lib = qotrt_ffi_lib_of(&args[0]);
    if (!lib)
    {
        qotrt_ffi_seterr("ffi_sym: bad lib handle");
        return qotrt_null_v();
    }
    QrtBytes b;
    qotrt_prim_bytes(&args[1], &b);
    if (!b.p || b.len == 0)
    {
        qotrt_ffi_seterr("ffi_sym: empty symbol");
        return qotrt_null_v();
    }
    char name[256];
    int32_t nl = b.len < 255 ? b.len : 255;
    std::memcpy(name, b.p, static_cast<size_t>(nl));
    name[nl] = 0;
    qotrt_prim_bytes_free(&b);
#if defined(_WIN32)
    void *fn = reinterpret_cast<void *>(GetProcAddress(lib->mod, name));
#else
    void *fn = dlsym(lib->mod, name);
#endif
    if (!fn)
    {
        qotrt_ffi_seterr("ffi_sym: symbol not found");
        return qotrt_null_v();
    }
    QrtFfiSym *s = static_cast<QrtFfiSym *>(qotrt_xalloc(sizeof(QrtFfiSym)));
    s->fn = fn;
    s->lib = 0;
    s->tc_arr = nullptr;   // PROMAX-F1：缓存空初始化
    s->tc_n = 0;
    s->tc_ret_fam = 0;
    // 反查 lib 槽下标（供 ffi_free 批量失效）；找不到 = 野句柄，防御置 -1
    for (int32_t i = 1; i < qotrt_ffitab_cap; i++)
    {
        if (qotrt_ffitab[i].p == lib)
        {
            s->lib = i;
            break;
        }
    }
    return qotrt_num(static_cast<double>(qotrt_fsymtab_add(s)));
}

// 类型码 → 家族：0=浮点族 1=整型族 -1=未知
static int qrt_ffi_family(const char *t)
{
    if (std::strcmp(t, "f64") == 0)
        return 0;
    if (std::strcmp(t, "i32") == 0 || std::strcmp(t, "i64") == 0 ||
        std::strcmp(t, "str") == 0 || std::strcmp(t, "ptr") == 0)
        return 1;
    return -1;
}

// QOTNative：码表载体提升文件作用域（原为 qrt_ffi_types 内 static）
static const char *qrt_ffi_types_buf[9]; // [0]=返回码 [1..]=实参码
// QOTNative：ffi_call_impl 缓存命中时的 tbuf 载体提升文件作用域（指向 sym->tc_codes）
static const char *qotrt_ffi_cache_buf[9];

// types 数组（QOT 字符串数组）→ C 侧码表；返回参数个数（不含返回码），
// ret 家族经 out；失败 nullptr。
static const char **qrt_ffi_types(V16 *tv, int32_t *out_n, char (*codes)[16],
                                  int32_t max_n, int *ret_fam)
{
    if (!tv || tv->t != VT_ARR)
        return nullptr;
    QArr *a = static_cast<QArr *>(tv->u.p);
    int32_t n = a ? a->len : 0;
    if (n < 1 || n > max_n + 1)
        return nullptr;
    const char **buf = qrt_ffi_types_buf; // QOTNative：文件作用域提升（宏翻转安全化）
    for (int32_t i = 0; i < n; i++)
    {
        QrtBytes tb;
        qotrt_prim_bytes(&a->a[i], &tb);
        int32_t tl = tb.len < 15 ? tb.len : 15;
        std::memcpy(codes[i], tb.p, static_cast<size_t>(tl));
        codes[i][tl] = 0;
        qotrt_prim_bytes_free(&tb);
        buf[i] = codes[i];
    }
    *ret_fam = qrt_ffi_family(buf[0]);
    if (*ret_fam < 0 && std::strcmp(buf[0], "void") != 0)
        return nullptr;
    *out_n = n - 1;
    return buf;
}

// 实参数值化：i32/i64/ptr → int64；str → 临时 NUL 串指针（副本记入 temps，
// 调用后统一释放）。失败（非数值/数组越界）返回 false。
static bool qrt_ffi_int_arg(V16 *v, int64_t *out, char **temps, int32_t ti)
{
    if (v->t == VT_NUM)
    {
        double d = v->u.d;
        if (d != static_cast<double>(static_cast<int64_t>(d)) ||
            d < -9.2e18 || d > 9.2e18)
            return false;
        *out = static_cast<int64_t>(d);
        return true;
    }
    if (v->t == VT_STR)
    {
        QStr *q = static_cast<QStr *>(v->u.p);
        int32_t n = q ? q->len : 0;
        char *z = static_cast<char *>(std::malloc(static_cast<size_t>(n) + 1));
        if (!z)
            return false;
        if (n)
            std::memcpy(z, q->s, static_cast<size_t>(n));
        z[n] = 0;
        temps[ti] = z;
        *out = reinterpret_cast<int64_t>(z);
        return true;
    }
    if (v->t == VT_BOOL)
    {
        *out = v->u.d != 0.0 ? 1 : 0;
        return true;
    }
    if (v->t == VT_NULL || v->t == VT_NONE)
    {
        *out = 0; // null → NULL 指针
        return true;
    }
    return false;
}

// ---- v2 混族分派器（M7）----
// 掩码位 i = 实参 i 是否 f64（1 → XMM/浮点寄存器类；0 → GP/整型·指针类）。
// 每个 (掩码, 返回类) 组合实例化一个「固定 8 参原型」的转型直调：被调方只读
// 自己声明的个数，多余槽传 0；调用方按 8 参建帧/清帧，两侧平衡（Win64 与
// SysV x64 均成立；多余实参为被调 ABI 明文忽略项）。256 掩码 × 2 返回类
// 全枚举 —— 无 libffi、无 JIT、无内联汇编。分派 = 线性比较链（≤255 次
// 整数比较，FFI 调用本身 µs 级，成本可忽略）。模板形态用部分特化递归
// （Live=false 为编译期递归底），不依赖 C++17 if-constexpr，MSVC 回退管线同源。
#include <type_traits>

// QOTNative：FFI 模板区在 qotrt_lib.cpp（QOT_RT_LIB_EXPORT）下暂时还原
// 字面 static —— extern "C" 链接性不能修饰模板/模板成员；模板实例化
// 本就是 COMDAT/weak 符号，静态库可解析，无需 extern 化。普通 TU 无感。
#ifdef QOT_RT_LIB_EXPORT
#pragma push_macro("static")
#undef static
#endif

template <int M, int I>
struct QrtFfiArgT
{
    using T = typename std::conditional<((M >> I) & 1) != 0, double, int64_t>::type;
};

template <int M, int I>
static inline typename QrtFfiArgT<M, I>::T qrt_ffi_sel(const int64_t *iv,
                                                       const double *dv)
{
    if ((M >> I) & 1)
        return dv[I];
    return static_cast<int64_t>(iv[I]);
}

template <int M>
struct QrtFfiCall
{
    static int64_t g(void *fn, const int64_t *iv, const double *dv)
    {
        typedef int64_t (*F)(typename QrtFfiArgT<M, 0>::T,
                             typename QrtFfiArgT<M, 1>::T,
                             typename QrtFfiArgT<M, 2>::T,
                             typename QrtFfiArgT<M, 3>::T,
                             typename QrtFfiArgT<M, 4>::T,
                             typename QrtFfiArgT<M, 5>::T,
                             typename QrtFfiArgT<M, 6>::T,
                             typename QrtFfiArgT<M, 7>::T);
        F f = reinterpret_cast<F>(fn);
        return f(qrt_ffi_sel<M, 0>(iv, dv), qrt_ffi_sel<M, 1>(iv, dv),
                 qrt_ffi_sel<M, 2>(iv, dv), qrt_ffi_sel<M, 3>(iv, dv),
                 qrt_ffi_sel<M, 4>(iv, dv), qrt_ffi_sel<M, 5>(iv, dv),
                 qrt_ffi_sel<M, 6>(iv, dv), qrt_ffi_sel<M, 7>(iv, dv));
    }
    static double d(void *fn, const int64_t *iv, const double *dv)
    {
        typedef double (*F)(typename QrtFfiArgT<M, 0>::T,
                            typename QrtFfiArgT<M, 1>::T,
                            typename QrtFfiArgT<M, 2>::T,
                            typename QrtFfiArgT<M, 3>::T,
                            typename QrtFfiArgT<M, 4>::T,
                            typename QrtFfiArgT<M, 5>::T,
                            typename QrtFfiArgT<M, 6>::T,
                            typename QrtFfiArgT<M, 7>::T);
        F f = reinterpret_cast<F>(fn);
        return f(qrt_ffi_sel<M, 0>(iv, dv), qrt_ffi_sel<M, 1>(iv, dv),
                 qrt_ffi_sel<M, 2>(iv, dv), qrt_ffi_sel<M, 3>(iv, dv),
                 qrt_ffi_sel<M, 4>(iv, dv), qrt_ffi_sel<M, 5>(iv, dv),
                 qrt_ffi_sel<M, 6>(iv, dv), qrt_ffi_sel<M, 7>(iv, dv));
    }
};

// 掩码 → 实例化链；Live=false（M<0）为编译期递归底，运行期不可达
// （掩码恒 ∈ [0,255]，M==0 档必然命中）。
template <int M, bool Live>
struct QrtFfiDisp
{
    static int64_t g(int, void *, const int64_t *, const double *)
    {
        return 0;
    }
    static double d(int, void *, const int64_t *, const double *)
    {
        return 0.0;
    }
};
template <int M>
struct QrtFfiDisp<M, true>
{
    static int64_t g(int mask, void *fn, const int64_t *iv, const double *dv)
    {
        if (M == mask)
            return QrtFfiCall<M>::g(fn, iv, dv);
        return QrtFfiDisp<M - 1, (M - 1 >= 0)>::g(mask, fn, iv, dv);
    }
    static double d(int mask, void *fn, const int64_t *iv, const double *dv)
    {
        if (M == mask)
            return QrtFfiCall<M>::d(fn, iv, dv);
        return QrtFfiDisp<M - 1, (M - 1 >= 0)>::d(mask, fn, iv, dv);
    }
};

#ifdef QOT_RT_LIB_EXPORT
#pragma pop_macro("static")
#endif

static V16 qotrt_ffi_call_impl(V16 *args, int argc, bool typed)
{
    qotrt_ffi_clearerr();
    if (argc < (typed ? 3 : 2))
    {
        qotrt_ffi_seterr("ffi_call: missing args");
        return qotrt_null_v();
    }
    QrtFfiSym *sym = qotrt_ffi_sym_of(&args[0]);
    if (!sym || !sym->fn)
    {
        qotrt_ffi_seterr("ffi_call: bad symbol handle");
        return qotrt_null_v();
    }
    const char *ret_code = "f64";
    int arg_n = 0;
    char codes[9][16];
    const char **tbuf = nullptr;
    const char **cache_buf = qotrt_ffi_cache_buf; // QOTNative：文件作用域提升（宏翻转安全化）
    if (typed)
    {
        // 【PROMAX-F1】缓存命中：类型数组对象 + 元素 QStr 指针串全等
        //（字符串不可变 ⇒ 内容必然相同）→ 复用上次解析，免逐项
        // prim_bytes/memcpy/NUL 终结。未命中回落原解析并回填缓存。
        bool hit = false;
        if (args[1].t == VT_ARR)
        {
            QArr *ta = static_cast<QArr *>(args[1].u.p);
            int32_t tn = ta ? ta->len : 0;
            if (sym->tc_arr == ta && sym->tc_n == tn && tn >= 1 && tn <= 9)
            {
                hit = true;
                for (int32_t i = 0; i < tn; i++)
                {
                    if (ta->a[i].t != VT_STR || ta->a[i].u.p != sym->tc_ptrs[i])
                    {
                        hit = false;
                        break;
                    }
                }
                if (hit)
                {
                    arg_n = sym->tc_n - 1;
                    for (int32_t i = 0; i < sym->tc_n; i++)
                        cache_buf[i] = sym->tc_codes[i];
                    tbuf = cache_buf;
                    ret_code = tbuf[0];
                }
            }
        }
        if (!hit)
        {
            int ret_fam = 0;
            tbuf = qrt_ffi_types(&args[1], &arg_n, codes, 8, &ret_fam);
            if (!tbuf)
            {
                qotrt_ffi_seterr("ffi_call_v: bad types array");
                return qotrt_null_v();
            }
            ret_code = tbuf[0];
            // 回填缓存（只在全部元素是 STR 时建立——prim_bytes 对非 STR 的
            // 数字化视图无法用指针等价追踪）
            if (args[1].t == VT_ARR)
            {
                QArr *ta = static_cast<QArr *>(args[1].u.p);
                int32_t tn = ta ? ta->len : 0;
                bool fillable = (tn >= 1 && tn <= 9);
                for (int32_t i = 0; fillable && i < tn; i++)
                    fillable = (ta->a[i].t == VT_STR);
                if (fillable)
                {
                    sym->tc_arr = ta;
                    sym->tc_n = tn;
                    for (int32_t i = 0; i < tn; i++)
                        sym->tc_ptrs[i] = ta->a[i].u.p;
                    std::memcpy(sym->tc_codes, codes, sizeof(codes));
                    sym->tc_ret_fam = ret_fam;
                }
            }
        }
    }
    // 实参数组
    V16 *arrv = &args[typed ? 2 : 1];
    if (arrv->t != VT_ARR)
    {
        qotrt_ffi_seterr("ffi_call: args must be array");
        return qotrt_null_v();
    }
    QArr *aa = static_cast<QArr *>(arrv->u.p);
    int32_t given = aa ? aa->len : 0;
    if (typed && given != arg_n)
    {
        qotrt_ffi_seterr("ffi_call_v: args/types length mismatch");
        return qotrt_null_v();
    }
    // 逐参数值化 + 定类（v2 混族：每个实参按自己的码独立定类，不再要求同族）
    int mask = 0; // 位 i = 实参 i 是 f64（XMM 类）
    int64_t ivals[8] = {0, 0, 0, 0, 0, 0, 0, 0};
    double dvals[8] = {0, 0, 0, 0, 0, 0, 0, 0};
    char *temps[8] = {nullptr, nullptr, nullptr, nullptr,
                      nullptr, nullptr, nullptr, nullptr};
    int32_t ntemps = 0;
    int32_t n = typed ? arg_n : given;
    if (n > 8)
    {
        qotrt_ffi_seterr("ffi_call: max 8 args");
        return qotrt_null_v();
    }
    bool ok = true;
    for (int32_t i = 0; i < n && ok; i++)
    {
        if (typed)
        {
            int f = qrt_ffi_family(tbuf[i + 1]);
            if (f < 0)
            {
                // 未知类型码（如 "u64"）—— 与 bad types array 同义报错
                qotrt_ffi_seterr("ffi_call_v: bad types array");
                ok = false;
                break;
            }
            if (f == 1)
            {
                if (std::strcmp(tbuf[i + 1], "str") == 0)
                {
                    // "str" 码只收字符串/null —— 数字值当指针传 = 必崩
                    // （S-14 教训：数字样字符串字面量在语言层就是数字，
                    // "123" 到不了这里；数字→指针必须走 ffi_ptr 显式转换）
                    if (aa->a[i].t != VT_STR &&
                        !(aa->a[i].t == VT_NULL || aa->a[i].t == VT_NONE))
                        ok = false;
                    else if (i >= 8 || ntemps >= 8 ||
                             !qrt_ffi_int_arg(&aa->a[i], &ivals[i], temps,
                                              ntemps++))
                        ok = false;
                }
                else if (aa->a[i].t == VT_STR)
                    ok = false; // ptr/i32/i64 不收字符串（字符串走 "str" 码）
                else if (!qrt_ffi_int_arg(&aa->a[i], &ivals[i], temps, -1))
                    ok = false;
                if (!ok)
                {
                    qotrt_ffi_seterr(
                        "ffi_call_v: argument does not match its type code");
                    break;
                }
            }
            else
            {
                if (aa->a[i].t != VT_NUM)
                {
                    qotrt_ffi_seterr(
                        "ffi_call_v: argument does not match its type code");
                    ok = false;
                    break;
                }
                dvals[i] = aa->a[i].u.d;
                mask |= (1 << i);
            }
        }
        else
        {
            // 推断版：全数字 → f64；出现字符串/其他 → 明确报错
            if (aa->a[i].t != VT_NUM)
            {
                qotrt_ffi_seterr("ffi_call: numeric args only "
                                 "(strings/pointers use ffi_call_v)");
                ok = false;
                break;
            }
            dvals[i] = aa->a[i].u.d;
            mask |= (1 << i);
        }
    }
    if (!ok)
    {
        for (int32_t i = 0; i < ntemps; i++)
            std::free(temps[i]);
        return qotrt_null_v();
    }
    // 无实参 → 掩码 0 = 全整型槽（与 v0 的 fam=1 语义一致）
    V16 r = qotrt_null_v();
    if ((typed && std::strcmp(ret_code, "f64") == 0) || !typed)
    {
        // v2：f64 返回走浮点分派器，与实参掩码正交（v0 的
        // “f64 return requires float family”限制随同族检查一并废除）
        double rv = QrtFfiDisp<255, true>::d(mask, sym->fn, ivals, dvals);
        r = qotrt_num(rv);
    }
    else
    {
        int64_t rv = QrtFfiDisp<255, true>::g(mask, sym->fn, ivals, dvals);
        if (std::strcmp(ret_code, "void") == 0)
            r = qotrt_null_v();
        else if (std::strcmp(ret_code, "str") == 0)
        {
            // 返回 C 串：有界读（1MB 防跑飞），拷成 QStr
            const char *s = reinterpret_cast<const char *>(rv);
            if (!s)
                r = qotrt_str("", 0);
            else
            {
                size_t k = 0;
                const size_t cap = 1u << 20;
                while (k < cap && s[k])
                    k++;
                r = qotrt_str(s, static_cast<int32_t>(k));
            }
        }
        else if (std::strcmp(ret_code, "ptr") == 0)
            r = qotrt_num(static_cast<double>(rv));
        else if (std::strcmp(ret_code, "i32") == 0)
            r = qotrt_num(static_cast<double>(static_cast<int32_t>(rv)));
        else
            r = qotrt_num(static_cast<double>(rv)); // i64
    }
    for (int32_t i = 0; i < ntemps; i++)
        std::free(temps[i]);
    return r;
}

static V16 qotrt_ffi_call(V16 *args, int argc)
{
    return qotrt_ffi_call_impl(args, argc, false);
}

static V16 qotrt_ffi_call_v(V16 *args, int argc)
{
    return qotrt_ffi_call_impl(args, argc, true);
}

static V16 qotrt_ffi_free(V16 *args, int argc)
{
    qotrt_ffi_clearerr();
    if (argc < 1)
        return qotrt_bool(false);
    QrtFfiLib *lib = qotrt_ffi_lib_of(&args[0]);
    if (!lib)
    {
        // sym 句柄不属于 ffi_free（那是 lib 的操作）
        return qotrt_bool(false);
    }
    int32_t h = -1;
    for (int32_t i = 1; i < qotrt_ffitab_cap; i++)
    {
        if (qotrt_ffitab[i].p == lib)
        {
            h = i;
            break;
        }
    }
    if (h < 0)
        return qotrt_bool(false);
#if defined(_WIN32)
    bool done = FreeLibrary(lib->mod) != 0;
#else
    bool done = dlclose(lib->mod) == 0;
#endif
    // sym 批量失效（无论卸载成败，句柄语义即作废 —— 世代计数断复用）
    for (int32_t i = 1; i < qotrt_fsymtab_cap; i++)
    {
        if (qotrt_fsymtab[i].p && qotrt_fsymtab[i].p->lib == h)
            qotrt_ffi_sym_slot_remove(i);
    }
    qotrt_ffi_lib_slot_remove(h);
    if (!done)
        qotrt_ffi_seterr("ffi_free: unload failed");
    return qotrt_bool(done);
}

static V16 qotrt_ffi_last_error(V16 *args, int argc)
{
    (void)args;
    (void)argc;
    return qotrt_str(qotrt_ffi_err, static_cast<int32_t>(std::strlen(qotrt_ffi_err)));
}

static V16 qotrt_ffi_str(V16 *args, int argc)
{
    qotrt_ffi_clearerr();
    if (argc < 1)
    {
        qotrt_ffi_seterr("ffi_str: missing ptr");
        return qotrt_str("", 0);
    }
    bool ok;
    int64_t p = qotrt_prim_i64(&args[0], &ok);
    if (!ok || p == 0)
    {
        if (!ok)
            qotrt_ffi_seterr("ffi_str: bad ptr");
        return qotrt_str("", 0);
    }
    const char *s = reinterpret_cast<const char *>(static_cast<uintptr_t>(p));
    size_t k = 0;
    const size_t cap = 1u << 20; // 有界读：跑飞的指针最多吃 1MB，不挂进程
    while (k < cap && s[k])
        k++;
    if (k >= cap)
        qotrt_ffi_seterr("ffi_str: unterminated (1MB bound)");
    return qotrt_str(s, static_cast<int32_t>(k));
}

static V16 qotrt_ffi_ptr(V16 *args, int argc)
{
    qotrt_ffi_clearerr();
    if (argc < 1)
    {
        qotrt_ffi_seterr("ffi_ptr: missing value");
        return qotrt_num(0.0);
    }
    if (args[0].t == VT_STR)
    {
        // 进程生命周期副本：std::malloc 直取（绕开池/arena —— P0-POOL-1
        // 教训：arena 回滚区里的块不可被跨轮持有）。账面=arena-free 持有。
        QStr *q = static_cast<QStr *>(args[0].u.p);
        int32_t n = q ? q->len : 0;
        char *z = static_cast<char *>(std::malloc(static_cast<size_t>(n) + 1));
        if (!z)
        {
            qotrt_ffi_seterr("ffi_ptr: oom");
            return qotrt_num(0.0);
        }
        if (n)
            std::memcpy(z, q->s, static_cast<size_t>(n));
        z[n] = 0;
        return qotrt_num(static_cast<double>(reinterpret_cast<uintptr_t>(z)));
    }
    bool ok;
    int64_t p = qotrt_prim_i64(&args[0], &ok);
    if (!ok)
    {
        qotrt_ffi_seterr("ffi_ptr: numeric or string expected");
        return qotrt_num(0.0);
    }
    return qotrt_num(static_cast<double>(p));
}

// ============================================================ L2 组 · 原生插件
// M7 冻结（v2 · 0）：公共头 ot-asset/c/plugin/qot_plugin_abi.h 的
// 宿主侧实现 + 运行期装载/调用面。契约（冻结文档 = docs/ §插件）：
//   * 插件 = 导出 `int qot_plugin_init(const qot_plugin_api*)` 的动态库，
//     init 期内经 api->reg 注册若干 (name, fn, argc, doc)；返回值必须等于
//     成功注册个数，负数 = 自认失败 —— 违反即回滚 + 卸载（不静默半装载）；
//   * 插件函数签名 = qot_plugin_fn（ret 出参 + api + 只读 args + argc）；
//   * 函数名跨插件全局唯一；plugin_free 摘除该插件全部注册并 gen++ 断复用；
//   * 错误通道 = FFI 同一条：api->set_error → plugin_call 得 null +
//     ffi_last_error()（文本两侧各自持有，差分只断非空 —— S-13 同律）；
//   * 注册表/插件表 = 池外长生命周期（xalloc 对账 free），句柄与文件/FFI
//     表同款世代计数；可移植插件只经 api 访问值（直读 V16 布局仅 qotrt 宿主
//     有效，不受公共头保护）。
// ---- 8< BEGIN QOT_PLUGIN_ABI（与 ot-asset/c/plugin/qot_plugin_abi.h 逐字节同步；test_ffi_abi 钉住）----
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
// ---- 8< END QOT_PLUGIN_ABI ----

struct QrtPlugSlot
{
    void *mod;
    uint32_t gen;
    int32_t next_free;
};

#ifdef QOT_RT_SPLIT
extern QrtPlugSlot *qotrt_plugtab;
#else
static QrtPlugSlot *qotrt_plugtab = nullptr;
#endif
#ifdef QOT_RT_SPLIT
extern int32_t qotrt_plugtab_cap;
#else
static int32_t qotrt_plugtab_cap = 0;
#endif
#ifdef QOT_RT_SPLIT
extern int32_t qotrt_plugtab_free;
#else
static int32_t qotrt_plugtab_free = -1;
#endif

struct QrtPlugFn
{
    char *name;
    int32_t name_len; // 含 NUL 的精确分配尺寸（xfree 对账）
    qot_plugin_fn fn;
    int32_t argc;
    char *doc; // 可为 NULL
    int32_t doc_len;
    int32_t owner; // 插件槽下标（plugin_free 批量摘除）
};

#ifdef QOT_RT_SPLIT
extern QrtPlugFn *qotrt_pfn;
#else
static QrtPlugFn *qotrt_pfn = nullptr;
#endif
#ifdef QOT_RT_SPLIT
extern int32_t qotrt_pfn_cap;
#else
static int32_t qotrt_pfn_cap = 0;
#endif
#ifdef QOT_RT_SPLIT
extern int32_t qotrt_pfn_len;
#else
static int32_t qotrt_pfn_len = 0;
#endif
// init 期“正在装载的插件槽”（reg 回调据此记账；装载完置 -1）
#ifdef QOT_RT_SPLIT
extern int32_t qotrt_pfn_loading;
#else
static int32_t qotrt_pfn_loading = -1;
#endif

// ---- api 实现（公共头回调逐一落地；借用引用仅在调用内有效） ----
static void qrt_plug_set_null(qot_value *v)
{
    V16 *x = reinterpret_cast<V16 *>(v);
    x->t = VT_NULL;
    x->u.p = nullptr;
}

static void qrt_plug_set_bool(qot_value *v, int b)
{
    V16 *x = reinterpret_cast<V16 *>(v);
    x->t = VT_BOOL;
    x->u.d = b ? 1.0 : 0.0;
}

static void qrt_plug_set_num(qot_value *v, double d)
{
    V16 *x = reinterpret_cast<V16 *>(v);
    x->t = VT_NUM;
    x->u.d = d;
}

static void qrt_plug_set_str(qot_value *v, const char *s, int64_t len)
{
    V16 *x = reinterpret_cast<V16 *>(v);
    if (!s || len == 0)
    {
        *x = qotrt_str("", 0);
        return;
    }
    if (len < 0)
    {
        // NUL 测定口径（有界 1MB 防跑飞），与 ffi_str 同律
        size_t k = 0;
        const size_t cap = 1u << 20;
        while (k < cap && s[k])
            k++;
        *x = qotrt_str(s, static_cast<int32_t>(k));
        return;
    }
    if (len > 0x7FFFFFFF)
        len = 0x7FFFFFFF;
    *x = qotrt_str(s, static_cast<int32_t>(len));
}

static int qrt_plug_tag_of(const qot_value *v)
{
    return v ? static_cast<int>(reinterpret_cast<const V16 *>(v)->t)
             : QOT_VT_NONE;
}

static double qrt_plug_num_of(const qot_value *v)
{
    const V16 *x = reinterpret_cast<const V16 *>(v);
    return (x && x->t == VT_NUM) ? x->u.d : 0.0;
}

static int qrt_plug_bool_of(const qot_value *v)
{
    const V16 *x = reinterpret_cast<const V16 *>(v);
    return (x && x->t == VT_BOOL && x->u.d != 0.0) ? 1 : 0;
}

static const char *qrt_plug_str_of(const qot_value *v, int64_t *len)
{
    const V16 *x = reinterpret_cast<const V16 *>(v);
    if (len)
        *len = -1;
    if (!x || x->t != VT_STR)
        return nullptr;
    QStr *q = static_cast<QStr *>(x->u.p);
    if (len)
        *len = q ? q->len : 0;
    return q ? q->s : nullptr;
}

static int64_t qrt_plug_arr_len(const qot_value *v)
{
    const V16 *x = reinterpret_cast<const V16 *>(v);
    if (!x || x->t != VT_ARR)
        return -1;
    QArr *a = static_cast<QArr *>(x->u.p);
    return a ? a->len : 0;
}

static const qot_value *qrt_plug_arr_at(const qot_value *v, int64_t i)
{
    const V16 *x = reinterpret_cast<const V16 *>(v);
    if (!x || x->t != VT_ARR)
        return nullptr;
    QArr *a = static_cast<QArr *>(x->u.p);
    if (!a || i < 0 || i >= a->len)
        return nullptr;
    return reinterpret_cast<const qot_value *>(&a->a[i]);
}

static void qrt_plug_arr_new(qot_value *out)
{
    *reinterpret_cast<V16 *>(out) = qotrt_arr_new(4);
}

static void qrt_plug_arr_push(qot_value *arr, const qot_value *v)
{
    V16 *x = reinterpret_cast<V16 *>(arr);
    if (!x || x->t != VT_ARR || !x->u.p || !v)
        return;
    qotrt_arr_push(static_cast<QArr *>(x->u.p),
                   *reinterpret_cast<const V16 *>(v));
}

static void qrt_plug_dict_new(qot_value *out)
{
    *reinterpret_cast<V16 *>(out) = qotrt_dict_new();
}

static void qrt_plug_dict_set(qot_value *d, const char *k, const qot_value *v)
{
    V16 *x = reinterpret_cast<V16 *>(d);
    if (!x || x->t != VT_DICT || !x->u.p || !k || !v)
        return;
    size_t kl = 0;
    const size_t cap = 1u << 20;
    while (kl < cap && k[kl])
        kl++;
    QDict *dict = static_cast<QDict *>(x->u.p);
    *qotrt_dict_at(dict, k, static_cast<int32_t>(kl),
                   qotrt_hash(k, kl)) = *reinterpret_cast<const V16 *>(v);
}

static int qrt_plug_dict_get(const qot_value *d, const char *k, qot_value *out)
{
    const V16 *x = reinterpret_cast<const V16 *>(d);
    V16 *o = reinterpret_cast<V16 *>(out);
    *o = qotrt_null_v();
    if (!x || x->t != VT_DICT || !x->u.p || !k)
        return 0;
    size_t kl = 0;
    const size_t cap = 1u << 20;
    while (kl < cap && k[kl])
        kl++;
    QDict *dict = static_cast<QDict *>(x->u.p);
    V16 *hit = qotrt_dict_find(dict, k, static_cast<int32_t>(kl),
                               qotrt_hash(k, kl));
    if (!hit)
        return 0;
    *o = *hit;
    return 1;
}

static void qrt_plug_set_error(const char *msg)
{
    qotrt_ffi_seterr(msg);
}

static char *qrt_plug_dup(const char *s, int32_t *out_len, int32_t cap)
{
    size_t n = 0;
    while (s && s[n] && n < static_cast<size_t>(cap))
        n++;
    char *z = static_cast<char *>(qotrt_xalloc(n + 1));
    if (n)
        std::memcpy(z, s, n);
    z[n] = 0;
    *out_len = static_cast<int32_t>(n + 1);
    return z;
}

static int qrt_plug_reg(const char *name, qot_plugin_fn fn, int argc,
                        const char *doc)
{
    if (qotrt_pfn_loading < 0 || !name || !name[0] || !fn || argc < 0 ||
        argc > 64)
        return 0;
    for (int32_t i = 0; i < qotrt_pfn_len; i++)
    {
        if (std::strcmp(qotrt_pfn[i].name, name) == 0)
            return 0; // 名字跨插件全局唯一
    }
    if (qotrt_pfn_len == qotrt_pfn_cap)
    {
        int32_t nc = qotrt_pfn_cap ? qotrt_pfn_cap * 2 : 16;
        QrtPlugFn *nt = static_cast<QrtPlugFn *>(
            qotrt_xalloc(sizeof(QrtPlugFn) * static_cast<size_t>(nc)));
        if (qotrt_pfn_len)
            std::memcpy(nt, qotrt_pfn,
                        sizeof(QrtPlugFn) * static_cast<size_t>(qotrt_pfn_len));
        if (qotrt_pfn)
            qotrt_xfree(qotrt_pfn,
                        sizeof(QrtPlugFn) * static_cast<size_t>(qotrt_pfn_cap));
        qotrt_pfn = nt;
        qotrt_pfn_cap = nc;
    }
    QrtPlugFn *e = &qotrt_pfn[qotrt_pfn_len];
    e->name = qrt_plug_dup(name, &e->name_len, 256);
    e->fn = fn;
    e->argc = argc;
    if (doc && doc[0])
        e->doc = qrt_plug_dup(doc, &e->doc_len, 512);
    else
    {
        e->doc = nullptr;
        e->doc_len = 0;
    }
    e->owner = qotrt_pfn_loading;
    qotrt_pfn_len++;
    return 1;
}

static const qot_plugin_api qotrt_plugin_api = {
    QOT_PLUGIN_ABI_VERSION,
    0u,
    &qrt_plug_set_null,
    &qrt_plug_set_bool,
    &qrt_plug_set_num,
    &qrt_plug_set_str,
    &qrt_plug_tag_of,
    &qrt_plug_num_of,
    &qrt_plug_bool_of,
    &qrt_plug_str_of,
    &qrt_plug_arr_len,
    &qrt_plug_arr_at,
    &qrt_plug_arr_new,
    &qrt_plug_arr_push,
    &qrt_plug_dict_new,
    &qrt_plug_dict_set,
    &qrt_plug_dict_get,
    &qrt_plug_set_error,
    &qrt_plug_reg,
};

// ---- 注册表维护（memmove 保注册序；plugin_free 按属主批量摘除） ----
static void qrt_plugfn_remove_at(int32_t idx)
{
    qotrt_xfree(qotrt_pfn[idx].name,
                static_cast<size_t>(qotrt_pfn[idx].name_len));
    if (qotrt_pfn[idx].doc)
        qotrt_xfree(qotrt_pfn[idx].doc,
                    static_cast<size_t>(qotrt_pfn[idx].doc_len));
    for (int32_t i = idx; i + 1 < qotrt_pfn_len; i++)
        qotrt_pfn[i] = qotrt_pfn[i + 1];
    qotrt_pfn_len--;
}

static void qrt_plugfn_remove_owner(int32_t owner)
{
    for (int32_t i = qotrt_pfn_len - 1; i >= 0; i--)
    {
        if (qotrt_pfn[i].owner == owner)
            qrt_plugfn_remove_at(i);
    }
}

// ---- 插件表（FFI lib 表同款：0 恒无效；add 复用空闲链；remove gen++） ----
static int32_t qotrt_plugtab_add(void *mod)
{
    if (qotrt_plugtab_free >= 0)
    {
        int32_t h = qotrt_plugtab_free;
        qotrt_plugtab_free = qotrt_plugtab[h].next_free;
        qotrt_plugtab[h].mod = mod;
        return h;
    }
    if (qotrt_plugtab_cap == 0)
    {
        qotrt_plugtab_cap = 8;
        qotrt_plugtab = static_cast<QrtPlugSlot *>(
            qotrt_xalloc(sizeof(QrtPlugSlot) * static_cast<size_t>(qotrt_plugtab_cap)));
        for (int32_t i = 0; i < qotrt_plugtab_cap; i++)
        {
            qotrt_plugtab[i].mod = nullptr;
            qotrt_plugtab[i].gen = 0;
            qotrt_plugtab[i].next_free = (i == qotrt_plugtab_cap - 1) ? -1 : i + 1;
        }
        qotrt_plugtab_free = 1; // 槽 0 恒无效
    }
    else
    {
        int32_t old_cap = qotrt_plugtab_cap;
        int32_t nc = qotrt_plugtab_cap * 2;
        QrtPlugSlot *nt = static_cast<QrtPlugSlot *>(
            qotrt_xalloc(sizeof(QrtPlugSlot) * static_cast<size_t>(nc)));
        std::memcpy(nt, qotrt_plugtab,
                    sizeof(QrtPlugSlot) * static_cast<size_t>(old_cap));
        qotrt_xfree(qotrt_plugtab, sizeof(QrtPlugSlot) * static_cast<size_t>(old_cap));
        for (int32_t i = old_cap; i < nc; i++)
        {
            nt[i].mod = nullptr;
            nt[i].gen = 0;
            nt[i].next_free = (i == nc - 1) ? -1 : i + 1;
        }
        qotrt_plugtab = nt;
        qotrt_plugtab_cap = nc;
    }
    int32_t h = qotrt_plugtab_free;
    qotrt_plugtab_free = qotrt_plugtab[h].next_free;
    qotrt_plugtab[h].mod = mod;
    return h;
}

static void qotrt_plugtab_slot_remove(int32_t h)
{
    if (h < 1 || h >= qotrt_plugtab_cap || qotrt_plugtab[h].mod == nullptr)
        return;
    qotrt_plugtab[h].mod = nullptr;
    qotrt_plugtab[h].gen++;
    qotrt_plugtab[h].next_free = qotrt_plugtab_free;
    qotrt_plugtab_free = h;
}

static QrtPlugSlot *qotrt_plug_of(V16 *v)
{
    if (v->t != VT_NUM)
        return nullptr;
    double d = v->u.d;
    if (!(d >= 1.0 && d < 2147483648.0) ||
        d != static_cast<double>(static_cast<int64_t>(d)))
        return nullptr;
    int32_t h = static_cast<int32_t>(static_cast<int64_t>(d));
    if (h < 1 || h >= qotrt_plugtab_cap)
        return nullptr;
    return qotrt_plugtab[h].mod ? &qotrt_plugtab[h] : nullptr;
}

static int32_t qotrt_plug_slot_of(QrtPlugSlot *plug)
{
    for (int32_t i = 1; i < qotrt_plugtab_cap; i++)
    {
        if (qotrt_plugtab[i].mod != nullptr && &qotrt_plugtab[i] == plug)
            return i;
    }
    return -1;
}

// ---- 四条语言面原语 ----
static V16 qotrt_plugin_load(V16 *args, int argc)
{
    qotrt_ffi_clearerr();
    if (argc < 1)
    {
        qotrt_ffi_seterr("plugin_load: missing path");
        return qotrt_null_v();
    }
    QrtBytes b;
    qotrt_prim_bytes(&args[0], &b);
    if (!b.p || b.len == 0)
    {
        qotrt_ffi_seterr("plugin_load: empty path");
        return qotrt_null_v();
    }
#if defined(_WIN32)
    int32_t wn = 0;
    wchar_t *w = qrt_u16(b.p, b.len, &wn);
    qotrt_prim_bytes_free(&b);
    if (!w)
    {
        qotrt_ffi_seterr("plugin_load: path encoding");
        return qotrt_null_v();
    }
    HMODULE m = LoadLibraryW(w);
    qrt_u16_free(w, wn);
    if (!m)
    {
        qotrt_ffi_seterr("plugin_load: LoadLibrary failed");
        return qotrt_null_v();
    }
#else
    char *pathz = static_cast<char *>(qotrt_xalloc(static_cast<size_t>(b.len) + 1));
    std::memcpy(pathz, b.p, static_cast<size_t>(b.len));
    pathz[b.len] = 0;
    qotrt_prim_bytes_free(&b);
    void *m = dlopen(pathz, RTLD_NOW | RTLD_LOCAL);
    qotrt_xfree(pathz, static_cast<size_t>(b.len) + 1);
    if (!m)
    {
        const char *e = dlerror();
        qotrt_ffi_seterr(e ? e : "plugin_load: dlopen failed");
        return qotrt_null_v();
    }
#endif
#if defined(_WIN32)
    void *init_fn = reinterpret_cast<void *>(GetProcAddress(m, "qot_plugin_init"));
#else
    void *init_fn = dlsym(m, "qot_plugin_init");
#endif
    if (!init_fn)
    {
        qotrt_ffi_seterr("plugin_load: qot_plugin_init not found");
#if defined(_WIN32)
        FreeLibrary(m);
#else
        dlclose(m);
#endif
        return qotrt_null_v();
    }
    typedef int (*InitT)(const qot_plugin_api *);
    InitT init = reinterpret_cast<InitT>(init_fn);
    int32_t slot = qotrt_plugtab_add(m);
    int32_t base = qotrt_pfn_len;
    qotrt_pfn_loading = slot;
    int rv = init(&qotrt_plugin_api);
    qotrt_pfn_loading = -1;
    int32_t added = qotrt_pfn_len - base;
    if (rv < 0 || rv != added)
    {
        // init 契约违反 = 明确失败：回滚本次注册 + 释放槽 + 卸载，
        // 不留“半装载”状态（可观察行为优先于诊断细节）
        qrt_plugfn_remove_owner(slot);
        qotrt_plugtab_slot_remove(slot);
        qotrt_ffi_seterr(rv < 0 ? "plugin_load: plugin init failed"
                                : "plugin_load: init count mismatch");
#if defined(_WIN32)
        FreeLibrary(m);
#else
        dlclose(m);
#endif
        return qotrt_null_v();
    }
    return qotrt_num(static_cast<double>(slot));
}

static V16 qotrt_plugin_call(V16 *args, int argc)
{
    qotrt_ffi_clearerr();
    if (argc < 3)
    {
        qotrt_ffi_seterr("plugin_call: missing args");
        return qotrt_null_v();
    }
    QrtPlugSlot *plug = qotrt_plug_of(&args[0]);
    if (!plug)
    {
        qotrt_ffi_seterr("plugin_call: bad plugin handle");
        return qotrt_null_v();
    }
    QrtBytes nb;
    qotrt_prim_bytes(&args[1], &nb);
    if (!nb.p || nb.len == 0)
    {
        qotrt_prim_bytes_free(&nb);
        qotrt_ffi_seterr("plugin_call: bad function name");
        return qotrt_null_v();
    }
    char name[256];
    int32_t nl = nb.len < 255 ? nb.len : 255;
    std::memcpy(name, nb.p, static_cast<size_t>(nl));
    name[nl] = 0;
    qotrt_prim_bytes_free(&nb);
    if (args[2].t != VT_ARR)
    {
        qotrt_ffi_seterr("plugin_call: bad args array");
        return qotrt_null_v();
    }
    QArr *aa = static_cast<QArr *>(args[2].u.p);
    int32_t given = aa ? aa->len : 0;
    qot_plugin_fn fn = nullptr;
    int32_t want = 0;
    for (int32_t i = 0; i < qotrt_pfn_len; i++)
    {
        if (std::strcmp(qotrt_pfn[i].name, name) == 0)
        {
            fn = qotrt_pfn[i].fn;
            want = qotrt_pfn[i].argc;
            break;
        }
    }
    if (!fn)
    {
        qotrt_ffi_seterr("plugin_call: bad function name");
        return qotrt_null_v();
    }
    if (given != want)
    {
        qotrt_ffi_seterr("plugin_call: arity mismatch");
        return qotrt_null_v();
    }
    // args 只读借用；空表给占位槽（argc=0 时插件不应解引用）
    V16 no_arg;
    no_arg.t = VT_NONE;
    no_arg.u.p = nullptr;
    const V16 *av = (aa && aa->len > 0 && aa->a) ? aa->a : &no_arg;
    V16 ret;
    ret.t = VT_NULL;
    ret.u.p = nullptr;
    fn(reinterpret_cast<qot_value *>(&ret), &qotrt_plugin_api,
       reinterpret_cast<const qot_value *>(av), given);
    if (qotrt_ffi_err[0] != 0)
        return qotrt_null_v(); // set_error 通道（文本已在 ffi_last_error）
    return ret;
}

static V16 qotrt_plugin_free(V16 *args, int argc)
{
    qotrt_ffi_clearerr();
    if (argc < 1)
        return qotrt_bool(false);
    QrtPlugSlot *plug = qotrt_plug_of(&args[0]);
    if (!plug)
        return qotrt_bool(false);
    int32_t h = qotrt_plug_slot_of(plug);
    if (h < 0)
        return qotrt_bool(false);
#if defined(_WIN32)
    bool done = FreeLibrary(static_cast<HMODULE>(plug->mod)) != 0;
#else
    bool done = dlclose(plug->mod) == 0;
#endif
    // 注册批量摘除（无论卸载成败，句柄语义即作废 —— 世代计数断复用）
    qrt_plugfn_remove_owner(h);
    qotrt_plugtab_slot_remove(h);
    if (!done)
        qotrt_ffi_seterr("plugin_free: unload failed");
    return qotrt_bool(done);
}

static V16 qotrt_plugin_names(V16 *args, int argc)
{
    qotrt_ffi_clearerr();
    if (argc < 1)
        return qotrt_null_v();
    QrtPlugSlot *plug = qotrt_plug_of(&args[0]);
    if (!plug)
        return qotrt_null_v();
    int32_t h = qotrt_plug_slot_of(plug);
    if (h < 0)
        return qotrt_null_v();
    int32_t n = 0;
    for (int32_t i = 0; i < qotrt_pfn_len; i++)
    {
        if (qotrt_pfn[i].owner == h)
            n++;
    }
    V16 out = qotrt_arr_new(n > 4 ? n : 4);
    QArr *a = static_cast<QArr *>(out.u.p);
    for (int32_t i = 0; i < qotrt_pfn_len; i++)
    {
        if (qotrt_pfn[i].owner == h)
            qotrt_arr_push(a, qotrt_str(qotrt_pfn[i].name,
                                        static_cast<int32_t>(
                                            std::strlen(qotrt_pfn[i].name))));
    }
    return out;
}

// ================================================================ O 组 · 网络
// P0.3（0）§4.9：TCP 14 + DNS 1 + UDP 3 = 18 条（tls_connect 留口
// FFI，不在此）。纪律与 file/ffi 同源：
//   * 句柄 = 数字（本进程槽表下标，1 起；gen 世代 + next_free 空闲链，
//     close 即失效 —— 世代断复用与 file/ffi 同律；差分用例不打印裸句柄）；
//   * 失败契约按规格：建/收侧 null、连接类 false、发送 -1、接收 ""；
//   * 字符串 = 字节序列（D-12）：recv/recv_line 原样字节，不验 UTF-8；
//   * recv_line 按字节逐读，剥一个行尾 \n（连 \r 一并），上限 64KB；
//   * bind 恒设 SO_REUSEADDR（本地回环测试/代理重启友好；v0 仅限本机面）；
//   * 超时经 socket_set_timeout（SO_RCVTIMEO/SO_SNDTIMEO），0 = 还原阻塞；
//   * v0 = AF_INET（IPv4）；socket_resolve 只产 v4 面（跨引擎一致性优先，
//     IPv6 面随 M 级 ABI 审查放开）；
//   * WSAStartup 懒初始化（首次 socket_* 调用；进程生命周期一次）。
#if defined(_WIN32)
#include <winsock2.h>
#include <ws2tcpip.h>
typedef SOCKET qrt_net_sock_t;
#define QRT_NET_SOCK_INVALID INVALID_SOCKET
typedef int qrt_net_socklen_t;
#else
#include <sys/socket.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <arpa/inet.h>
#include <netdb.h>
#include <unistd.h>
#include <cerrno>
typedef int qrt_net_sock_t;
#define QRT_NET_SOCK_INVALID (-1)
typedef socklen_t qrt_net_socklen_t;
#endif

// QOTNative：WSAStartup 懒初始化标志提升文件作用域（原为函数内 static，宏翻转安全化）
static bool qotrt_net_init_done = false;

static bool qotrt_net_init()
{
#if defined(_WIN32)
    if (!qotrt_net_init_done)
    {
        WSADATA w;
        if (WSAStartup(MAKEWORD(2, 2), &w) != 0)
            return false;
        qotrt_net_init_done = true;
    }
#endif
    return true;
}

// v4 点分解析/格式化：手写（inet_pton/inet_ntop 是 Vista+ API，MinGW 按
// _WIN32_WINNT 裁剪声明且宏被 windows.h 链提前定型 —— 零版本依赖最稳；
// inet_addr 弃用：对 "255.255.255.255" 返回 INADDR_NONE 误判）。
static bool qotrt_net_parse_ipv4(const char *s, in_addr *out)
{
    if (!s || !*s)
        return false;
    const char *p = s;
    int dots = 0;
    for (; *p; p++)
    {
        if (*p == '.')
            dots++;
        else if (*p < '0' || *p > '9')
            return false;
    }
    if (dots != 3)
        return false;
    unsigned b[4] = {0, 0, 0, 0};
    if (std::sscanf(s, "%u.%u.%u.%u", &b[0], &b[1], &b[2], &b[3]) != 4)
        return false;
    for (int i = 0; i < 4; i++)
        if (b[i] > 255)
            return false;
    out->s_addr = htonl((b[0] << 24) | (b[1] << 16) | (b[2] << 8) | b[3]);
    return true;
}

static void qotrt_net_ipv4_str(const in_addr *a, char *out, int outsz)
{
    unsigned v = ntohl(a->s_addr);
    std::snprintf(out, static_cast<size_t>(outsz), "%u.%u.%u.%u",
                  (v >> 24) & 0xFF, (v >> 16) & 0xFF, (v >> 8) & 0xFF,
                  v & 0xFF);
}

struct QrtSock
{
    qrt_net_sock_t fd;
    bool udp;
    char peer[64]; // 最近对端 "ip:port"（accept/recvfrom 填充；remote_addr 用）
    int32_t timeout_ms; // 收发与 accept 的超时（0 = 阻塞）。accept 不受
                        // SO_RCVTIMEO 约束（Windows 明确不、POSIX 不保证）——
                        // 统一经 select() 实现跨平台一致的超时语义
};

struct QrtSockSlot
{
    QrtSock *p;
    uint32_t gen;
    int32_t next_free;
};

#ifdef QOT_RT_SPLIT
extern QrtSockSlot *qotrt_stab;
#else
static QrtSockSlot *qotrt_stab = nullptr;
#endif
#ifdef QOT_RT_SPLIT
extern int32_t qotrt_stab_cap;
#else
static int32_t qotrt_stab_cap = 0;
#endif
#ifdef QOT_RT_SPLIT
extern int32_t qotrt_stab_free;
#else
static int32_t qotrt_stab_free = -1;
#endif

static int32_t qotrt_stab_add(QrtSock *p)
{
    if (qotrt_stab_free >= 0)
    {
        int32_t h = qotrt_stab_free;
        qotrt_stab_free = qotrt_stab[h].next_free;
        qotrt_stab[h].p = p;
        return h;
    }
    if (qotrt_stab_cap == 0)
    {
        qotrt_stab_cap = 8;
        qotrt_stab = static_cast<QrtSockSlot *>(
            qotrt_xalloc(sizeof(QrtSockSlot) * static_cast<size_t>(qotrt_stab_cap)));
        for (int32_t i = 0; i < qotrt_stab_cap; i++)
        {
            qotrt_stab[i].p = nullptr;
            qotrt_stab[i].gen = 0;
            qotrt_stab[i].next_free = (i == qotrt_stab_cap - 1) ? -1 : i + 1;
        }
        qotrt_stab_free = 1; // 槽 0 恒无效
    }
    else
    {
        int32_t old_cap = qotrt_stab_cap;
        int32_t nc = qotrt_stab_cap * 2;
        QrtSockSlot *nt = static_cast<QrtSockSlot *>(
            qotrt_xalloc(sizeof(QrtSockSlot) * static_cast<size_t>(nc)));
        std::memcpy(nt, qotrt_stab, sizeof(QrtSockSlot) * static_cast<size_t>(old_cap));
        qotrt_xfree(qotrt_stab, sizeof(QrtSockSlot) * static_cast<size_t>(old_cap));
        for (int32_t i = old_cap; i < nc; i++)
        {
            nt[i].p = nullptr;
            nt[i].gen = 0;
            nt[i].next_free = (i == nc - 1) ? -1 : i + 1;
        }
        qotrt_stab = nt;
        qotrt_stab_cap = nc;
    }
    int32_t h = qotrt_stab_free;
    qotrt_stab_free = qotrt_stab[h].next_free;
    qotrt_stab[h].p = p;
    return h;
}

static QrtSock *qotrt_sock_of(V16 *v)
{
    if (v->t != VT_NUM)
        return nullptr;
    double d = v->u.d;
    if (!(d >= 1.0 && d < 2147483648.0) || d != static_cast<double>(static_cast<int64_t>(d)))
        return nullptr;
    int32_t h = static_cast<int32_t>(static_cast<int64_t>(d));
    if (h < 1 || h >= qotrt_stab_cap)
        return nullptr;
    return qotrt_stab[h].p;
}

static void qotrt_sock_slot_remove(int32_t h)
{
    if (h < 1 || h >= qotrt_stab_cap || qotrt_stab[h].p == nullptr)
        return;
    QrtSock *p = qotrt_stab[h].p;
    qotrt_stab[h].p = nullptr;
    qotrt_stab[h].gen++;
    qotrt_stab[h].next_free = qotrt_stab_free;
    qotrt_stab_free = h;
    qotrt_xfree(p, sizeof(QrtSock)); // 池纪律：按分配尺寸归还（Debug 审计对账）
}

static void qotrt_sock_set_peer(QrtSock *p, const sockaddr_in *a)
{
    char ip[INET_ADDRSTRLEN] = {0};
    qotrt_net_ipv4_str(&a->sin_addr, ip, sizeof(ip));
    std::snprintf(p->peer, sizeof(p->peer), "%s:%d", ip,
                  static_cast<int>(ntohs(a->sin_port)));
}

// host → IPv4 sockaddr_in。host 为空/"*"/"0.0.0.0" → INADDR_ANY。
// 数字点分直接 inet_pton（快路径）；其余 getaddrinfo（AI_NUMERICHOST 不设，
// 允许主机名 —— connect/udp_send 的 host 语义一致）。
static bool qotrt_net_host_addr(const char *host, int port, sockaddr_in *out)
{
    std::memset(out, 0, sizeof(*out));
    out->sin_family = AF_INET;
    out->sin_port = htons(static_cast<unsigned short>(port));
    if (!host || !host[0] || std::strcmp(host, "*") == 0 ||
        std::strcmp(host, "0.0.0.0") == 0)
    {
        out->sin_addr.s_addr = INADDR_ANY;
        return true;
    }
    if (qotrt_net_parse_ipv4(host, &out->sin_addr))
        return true;
    addrinfo hints;
    std::memset(&hints, 0, sizeof(hints));
    hints.ai_family = AF_INET;
    hints.ai_socktype = SOCK_STREAM;
    addrinfo *res = nullptr;
    if (getaddrinfo(host, nullptr, &hints, &res) != 0 || !res)
        return false;
    bool ok = false;
    for (addrinfo *ai = res; ai; ai = ai->ai_next)
    {
        if (ai->ai_family == AF_INET && ai->ai_addr &&
            ai->ai_addrlen >= static_cast<size_t>(sizeof(sockaddr_in)))
        {
            std::memcpy(out, ai->ai_addr, sizeof(sockaddr_in));
            out->sin_port = htons(static_cast<unsigned short>(port));
            ok = true;
            break;
        }
    }
    freeaddrinfo(res);
    return ok;
}

static V16 qotrt_socket_new(V16 *args, int argc)
{
    (void)args;
    (void)argc;
    if (!qotrt_net_init())
        return qotrt_null_v();
    qrt_net_sock_t fd = socket(AF_INET, SOCK_STREAM, 0);
    if (fd == QRT_NET_SOCK_INVALID)
        return qotrt_null_v();
    QrtSock *p = static_cast<QrtSock *>(qotrt_xalloc(sizeof(QrtSock)));
    p->fd = fd;
    p->udp = false;
    p->peer[0] = 0;
    p->timeout_ms = 0;
    return qotrt_num(static_cast<double>(qotrt_stab_add(p)));
}

static V16 qotrt_socket_connect(V16 *args, int argc)
{
    if (argc < 3)
        return qotrt_bool(false);
    QrtSock *p = qotrt_sock_of(&args[0]);
    if (!p || p->udp)
        return qotrt_bool(false);
    QrtBytes host;
    qotrt_prim_bytes(&args[1], &host);
    bool okn;
    int64_t port = qotrt_prim_i64(&args[2], &okn);
    sockaddr_in sa;
    if (!okn || port < 0 || port > 65535 ||
        !qotrt_net_host_addr(host.p, static_cast<int>(port), &sa))
    {
        qotrt_prim_bytes_free(&host);
        return qotrt_bool(false);
    }
    qotrt_prim_bytes_free(&host);
    if (::connect(p->fd, reinterpret_cast<sockaddr *>(&sa), sizeof(sa)) != 0)
        return qotrt_bool(false);
    qotrt_sock_set_peer(p, &sa);
    return qotrt_bool(true);
}

static V16 qotrt_socket_bind(V16 *args, int argc)
{
    if (argc < 3)
        return qotrt_bool(false);
    QrtSock *p = qotrt_sock_of(&args[0]);
    if (!p)
        return qotrt_bool(false);
    QrtBytes host;
    qotrt_prim_bytes(&args[1], &host);
    bool okn;
    int64_t port = qotrt_prim_i64(&args[2], &okn);
    sockaddr_in sa;
    if (!okn || port < 0 || port > 65535 ||
        !qotrt_net_host_addr(host.p, static_cast<int>(port), &sa))
    {
        qotrt_prim_bytes_free(&host);
        return qotrt_bool(false);
    }
    qotrt_prim_bytes_free(&host);
    // SO_REUSEADDR：监听/测试套接字重启友好（TIME_WAIT 复绑）。Windows 语义
    // 宽（允许抢绑）—— v0 服务面仅限本机回环测试，风险面受控。
    int one = 1;
    setsockopt(p->fd, SOL_SOCKET, SO_REUSEADDR,
               reinterpret_cast<const char *>(&one), sizeof(one));
    if (::bind(p->fd, reinterpret_cast<sockaddr *>(&sa), sizeof(sa)) != 0)
        return qotrt_bool(false);
    return qotrt_bool(true);
}

static V16 qotrt_socket_listen(V16 *args, int argc)
{
    if (argc < 2)
        return qotrt_bool(false);
    QrtSock *p = qotrt_sock_of(&args[0]);
    if (!p || p->udp)
        return qotrt_bool(false);
    bool okn;
    int64_t backlog = qotrt_prim_i64(&args[1], &okn);
    if (!okn || backlog < 1)
        backlog = 8; // 宽容：缺省 backlog（SOMAXCONN 过大无谓）
    return qotrt_bool(::listen(p->fd, static_cast<int>(backlog)) == 0);
}

static V16 qotrt_socket_accept(V16 *args, int argc)
{
    if (argc < 1)
        return qotrt_null_v();
    QrtSock *p = qotrt_sock_of(&args[0]);
    if (!p || p->udp)
        return qotrt_null_v();
    if (p->timeout_ms > 0)
    {
        // accept 不受 SO_RCVTIMEO 约束（Windows 明确不、POSIX 不保证）——
        // 统一经 select 等可读，超时即 null
        fd_set rs;
        timeval tv;
        tv.tv_sec = static_cast<long>(p->timeout_ms / 1000);
        tv.tv_usec = static_cast<long>((p->timeout_ms % 1000) * 1000);
        FD_ZERO(&rs);
#if defined(_WIN32)
        FD_SET(p->fd, &rs);
        if (select(0, &rs, nullptr, nullptr, &tv) <= 0)
            return qotrt_null_v();
#else
        FD_SET(static_cast<int>(p->fd), &rs);
        if (select(static_cast<int>(p->fd) + 1, &rs, nullptr, nullptr, &tv) <= 0)
            return qotrt_null_v();
#endif
    }
    sockaddr_in peer;
    qrt_net_socklen_t pl = static_cast<qrt_net_socklen_t>(sizeof(peer));
    qrt_net_sock_t c = ::accept(p->fd, reinterpret_cast<sockaddr *>(&peer), &pl);
    if (c == QRT_NET_SOCK_INVALID)
        return qotrt_null_v(); // 超时/失败同 null（可重试或放弃，两态不区分）
    QrtSock *np = static_cast<QrtSock *>(qotrt_xalloc(sizeof(QrtSock)));
    np->fd = c;
    np->udp = false;
    np->peer[0] = 0;
    np->timeout_ms = 0;
    qotrt_sock_set_peer(np, &peer);
    return qotrt_num(static_cast<double>(qotrt_stab_add(np)));
}

static V16 qotrt_socket_send(V16 *args, int argc)
{
    if (argc < 2)
        return qotrt_num(-1.0);
    QrtSock *p = qotrt_sock_of(&args[0]);
    if (!p || p->udp)
        return qotrt_num(-1.0);
    QrtBytes b;
    qotrt_prim_bytes(&args[1], &b);
    int n = static_cast<int>(::send(p->fd, b.p ? b.p : "",
                                    static_cast<int>(b.len), 0));
    qotrt_prim_bytes_free(&b);
    return qotrt_num(static_cast<double>(n));
}

static V16 qotrt_socket_send_all(V16 *args, int argc)
{
    if (argc < 2)
        return qotrt_num(-1.0);
    QrtSock *p = qotrt_sock_of(&args[0]);
    if (!p || p->udp)
        return qotrt_num(-1.0);
    QrtBytes b;
    qotrt_prim_bytes(&args[1], &b);
    int32_t off = 0;
    while (off < b.len)
    {
        int n = static_cast<int>(::send(p->fd, b.p + off,
                                        static_cast<int>(b.len - off), 0));
        if (n <= 0)
        {
            qotrt_prim_bytes_free(&b);
            return qotrt_num(-1.0);
        }
        off += n;
    }
    qotrt_prim_bytes_free(&b);
    return qotrt_num(static_cast<double>(b.len));
}

static V16 qotrt_socket_recv(V16 *args, int argc)
{
    if (argc < 2)
        return qotrt_str("", 0);
    QrtSock *p = qotrt_sock_of(&args[0]);
    if (!p)
        return qotrt_str("", 0);
    bool okn;
    int64_t want = qotrt_prim_i64(&args[1], &okn);
    if (!okn || want < 0)
        want = 0;
    if (want > 1 << 20)
        want = 1 << 20; // 单次上限 1MB（对齐 ffi_str 有界纪律）
    if (want == 0)
        return qotrt_str("", 0);
    QStr *q = static_cast<QStr *>(qotrt_xalloc(sizeof(QStr) +
                                               static_cast<size_t>(want) + 1));
    int n = static_cast<int>(::recv(p->fd, reinterpret_cast<char *>(q + 1),
                                    static_cast<int>(want), 0));
    if (n <= 0)
    {
        // n==0 = 对端关序（EOF）；<0 = 错误/超时 —— 契约统一 ""（规格如此，
        // EOF 与失败不区分；需要区分的用 recv_line 的余量读）
        qotrt_xfree(q, sizeof(QStr) + static_cast<size_t>(want) + 1);
        return qotrt_str("", 0);
    }
    char *buf = reinterpret_cast<char *>(q + 1);
    q->len = n;
    q->cap = n;
    q->hash = 0;
    q->shared = 0;
    q->s = buf;
    buf[n] = 0;
    return qrt_str_v(q);
}

static V16 qotrt_socket_recv_line(V16 *args, int argc)
{
    if (argc < 1)
        return qotrt_str("", 0);
    QrtSock *p = qotrt_sock_of(&args[0]);
    if (!p)
        return qotrt_str("", 0);
    // 逐字节读到 \n（或 EOF/上限 64KB）。剥行尾 \n（连 \r）。
    QStr *q = static_cast<QStr *>(qotrt_xalloc(sizeof(QStr) + 65536 + 1));
    char *buf = reinterpret_cast<char *>(q + 1);
    int32_t n = 0;
    while (n < 65536)
    {
        int r = static_cast<int>(::recv(p->fd, buf + n, 1, 0));
        if (r <= 0)
            break; // EOF/失败：返回已收到的部分（对端半关语义）
        if (buf[n] == '\n')
        {
            if (n > 0 && buf[n - 1] == '\r')
                n--;
            break;
        }
        n++;
    }
    q->len = n;
    q->cap = n;
    q->hash = 0;
    q->shared = 0;
    q->s = buf;
    buf[n] = 0;
    return qrt_str_v(q);
}

static V16 qotrt_socket_close(V16 *args, int argc)
{
    if (argc < 1)
        return qotrt_bool(false);
    if (args[0].t != VT_NUM)
        return qotrt_bool(false);
    double d = args[0].u.d;
    if (!(d >= 1.0 && d < 2147483648.0) || d != static_cast<double>(static_cast<int64_t>(d)))
        return qotrt_bool(false);
    int32_t h = static_cast<int32_t>(static_cast<int64_t>(d));
    QrtSock *p = qotrt_sock_of(&args[0]);
    if (!p)
        return qotrt_bool(false);
#if defined(_WIN32)
    bool ok = closesocket(p->fd) == 0;
#else
    bool ok = ::close(p->fd) == 0;
#endif
    qotrt_sock_slot_remove(h);
    return qotrt_bool(ok);
}

static V16 qotrt_socket_set_timeout(V16 *args, int argc)
{
    if (argc < 2)
        return qotrt_bool(false);
    QrtSock *p = qotrt_sock_of(&args[0]);
    if (!p)
        return qotrt_bool(false);
    bool okn;
    int64_t ms = qotrt_prim_i64(&args[1], &okn);
    if (!okn || ms < 0)
        return qotrt_bool(false);
    p->timeout_ms = static_cast<int32_t>(ms); // accept 的超时经 select（见上）
    bool ok = true;
#if defined(_WIN32)
    // 0 = 永不超时（Windows DWORD 无穷大哨兵不可用 → 0 表示还原阻塞）
    DWORD tv = static_cast<DWORD>(ms);
    if (setsockopt(p->fd, SOL_SOCKET, SO_RCVTIMEO,
                   reinterpret_cast<const char *>(&tv), sizeof(tv)) != 0)
        ok = false;
    if (ok && setsockopt(p->fd, SOL_SOCKET, SO_SNDTIMEO,
                         reinterpret_cast<const char *>(&tv), sizeof(tv)) != 0)
        ok = false;
#else
    timeval tv;
    tv.tv_sec = static_cast<long>(ms / 1000);
    tv.tv_usec = static_cast<long>((ms % 1000) * 1000);
    if (setsockopt(p->fd, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv)) != 0)
        ok = false;
    if (ok && setsockopt(p->fd, SOL_SOCKET, SO_SNDTIMEO, &tv, sizeof(tv)) != 0)
        ok = false;
#endif
    return qotrt_bool(ok);
}

static V16 qotrt_socket_set_nodelay(V16 *args, int argc)
{
    if (argc < 2)
        return qotrt_bool(false);
    QrtSock *p = qotrt_sock_of(&args[0]);
    if (!p || p->udp)
        return qotrt_bool(false);
    bool on = (args[1].t == VT_BOOL) ? (args[1].u.d != 0) : true;
    int one = on ? 1 : 0;
    return qotrt_bool(setsockopt(p->fd, IPPROTO_TCP, TCP_NODELAY,
                                 reinterpret_cast<const char *>(&one),
                                 sizeof(one)) == 0);
}

// "ip:port"（v4）。TCP：getsockname/getpeername 即时查询；UDP：recvfrom 后
// 的最近对端（远端未知时 ""）。
static V16 qotrt_sock_addr_impl(V16 *args, bool remote)
{
    QrtSock *p = qotrt_sock_of(&args[0]);
    if (!p)
        return qotrt_str("", 0);
    if (remote && p->udp)
        return qotrt_str(p->peer, static_cast<int32_t>(std::strlen(p->peer)));
    sockaddr_in sa;
    qrt_net_socklen_t sl = static_cast<qrt_net_socklen_t>(sizeof(sa));
    int r = remote ? ::getpeername(p->fd, reinterpret_cast<sockaddr *>(&sa), &sl)
                   : ::getsockname(p->fd, reinterpret_cast<sockaddr *>(&sa), &sl);
    if (r != 0)
        return qotrt_str("", 0);
    char ip[INET_ADDRSTRLEN] = {0};
    qotrt_net_ipv4_str(&sa.sin_addr, ip, sizeof(ip));
    char out[80];
    std::snprintf(out, sizeof(out), "%s:%d", ip,
                  static_cast<int>(ntohs(sa.sin_port)));
    return qotrt_str(out, static_cast<int32_t>(std::strlen(out)));
}

static V16 qotrt_socket_local_addr(V16 *args, int argc)
{
    (void)argc;
    if (argc < 1)
        return qotrt_str("", 0);
    return qotrt_sock_addr_impl(args, false);
}

static V16 qotrt_socket_remote_addr(V16 *args, int argc)
{
    (void)argc;
    if (argc < 1)
        return qotrt_str("", 0);
    return qotrt_sock_addr_impl(args, true);
}

static V16 qotrt_socket_resolve(V16 *args, int argc)
{
    if (argc < 1)
        return qotrt_null_v();
    QrtBytes host;
    qotrt_prim_bytes(&args[0], &host);
    if (!qotrt_net_init())
    {
        qotrt_prim_bytes_free(&host);
        return qotrt_null_v();
    }
    addrinfo hints;
    std::memset(&hints, 0, sizeof(hints));
    hints.ai_family = AF_INET; // v0 只产 v4 面（跨引擎一致性）
    hints.ai_socktype = SOCK_STREAM;
    addrinfo *res = nullptr;
    if (getaddrinfo(host.p ? host.p : "", nullptr, &hints, &res) != 0 || !res)
    {
        qotrt_prim_bytes_free(&host);
        return qotrt_null_v();
    }
    qotrt_prim_bytes_free(&host);
    V16 out = qotrt_arr_new(4);
    QArr *a = static_cast<QArr *>(out.u.p);
    for (addrinfo *ai = res; ai; ai = ai->ai_next)
    {
        if (ai->ai_family != AF_INET || !ai->ai_addr ||
            ai->ai_addrlen < static_cast<size_t>(sizeof(sockaddr_in)))
            continue;
        sockaddr_in sa;
        std::memcpy(&sa, ai->ai_addr, sizeof(sa));
        char ip[INET_ADDRSTRLEN] = {0};
        qotrt_net_ipv4_str(&sa.sin_addr, ip, sizeof(ip));
        // 去重（getaddrinfo 可能返回重复地址面）
        bool dup = false;
        for (int32_t i = 0; i < a->len; i++)
        {
            QStr *q = static_cast<QStr *>(a->a[i].u.p);
            if (q->len == static_cast<int32_t>(std::strlen(ip)) &&
                std::memcmp(q->s, ip, static_cast<size_t>(q->len)) == 0)
            {
                dup = true;
                break;
            }
        }
        if (!dup)
            qotrt_arr_push(a, qrt_str_v(qotrt_str_new(ip, static_cast<int32_t>(std::strlen(ip)))));
    }
    freeaddrinfo(res);
    if (a->len == 0)
        return qotrt_null_v();
    return out;
}

static V16 qotrt_socket_udp_new(V16 *args, int argc)
{
    (void)args;
    (void)argc;
    if (!qotrt_net_init())
        return qotrt_null_v();
    qrt_net_sock_t fd = socket(AF_INET, SOCK_DGRAM, 0);
    if (fd == QRT_NET_SOCK_INVALID)
        return qotrt_null_v();
    QrtSock *p = static_cast<QrtSock *>(qotrt_xalloc(sizeof(QrtSock)));
    p->fd = fd;
    p->udp = true;
    p->peer[0] = 0;
    p->timeout_ms = 0;
    return qotrt_num(static_cast<double>(qotrt_stab_add(p)));
}

static V16 qotrt_socket_udp_send(V16 *args, int argc)
{
    if (argc < 4)
        return qotrt_num(-1.0);
    QrtSock *p = qotrt_sock_of(&args[0]);
    if (!p || !p->udp)
        return qotrt_num(-1.0);
    QrtBytes host;
    QrtBytes data;
    qotrt_prim_bytes(&args[1], &host);
    qotrt_prim_bytes(&args[3], &data);
    bool okn;
    int64_t port = qotrt_prim_i64(&args[2], &okn);
    sockaddr_in sa;
    if (!okn || port < 0 || port > 65535 ||
        !qotrt_net_host_addr(host.p, static_cast<int>(port), &sa))
    {
        qotrt_prim_bytes_free(&host);
        qotrt_prim_bytes_free(&data);
        return qotrt_num(-1.0);
    }
    qotrt_prim_bytes_free(&host);
    int n = static_cast<int>(sendto(p->fd, data.p ? data.p : "",
                                    static_cast<int>(data.len), 0,
                                    reinterpret_cast<sockaddr *>(&sa),
                                    sizeof(sa)));
    qotrt_prim_bytes_free(&data);
    return qotrt_num(static_cast<double>(n));
}

static V16 qotrt_socket_udp_recv(V16 *args, int argc)
{
    if (argc < 2)
        return qotrt_str("", 0);
    QrtSock *p = qotrt_sock_of(&args[0]);
    if (!p || !p->udp)
        return qotrt_str("", 0);
    bool okn;
    int64_t want = qotrt_prim_i64(&args[1], &okn);
    if (!okn || want < 0)
        want = 0;
    if (want > 1 << 20)
        want = 1 << 20;
    if (want == 0)
        return qotrt_str("", 0);
    sockaddr_in peer;
    qrt_net_socklen_t pl = static_cast<qrt_net_socklen_t>(sizeof(peer));
    QStr *q = static_cast<QStr *>(qotrt_xalloc(sizeof(QStr) +
                                               static_cast<size_t>(want) + 1));
    char *buf = reinterpret_cast<char *>(q + 1);
    int n = static_cast<int>(recvfrom(p->fd, buf, static_cast<int>(want), 0,
                                      reinterpret_cast<sockaddr *>(&peer), &pl));
    if (n <= 0)
    {
        qotrt_xfree(q, sizeof(QStr) + static_cast<size_t>(want) + 1);
        return qotrt_str("", 0); // 超时/失败同 ""
    }
    qotrt_sock_set_peer(p, &peer);
    q->len = n;
    q->cap = n;
    q->hash = 0;
    q->shared = 0;
    q->s = buf;
    buf[n] = 0;
    return qrt_str_v(q);
}

// 【PROMAX-NET】select 多路复用：句柄数组 + 超时 ms → 就绪句柄数组。
// 读就绪面（accept/可读/对端关闭全算就绪——关闭由后续 recv 的 "" 判定，
// 与 recv 的 EOF 契约闭合）。v0 契约：
//   * 入参非数组/超时非法 → null；句柄集中非法项跳过（不炸）；
//   * 就绪数组是新数组（入参不变）；超时/错误 → 空数组（语义：无就绪，
//     与"错误"不可区分——调用方拿 recv 结果判定真伪）；
//   * 句柄数上限 FD_SETSIZE-1（Windows 63 / POSIX 1023），超限忽略
//     （std.net 的 http_server v0 按 63 并发设计，超出走排队）。
static V16 qotrt_socket_select(V16 *args, int argc)
{
    (void)argc;
    if (args[0].t != VT_ARR)
        return qotrt_null_v();
    bool okn;
    int64_t ms = qotrt_prim_i64(&args[1], &okn);
    if (!okn || ms < 0)
        ms = 0;
    if (!qotrt_net_init())
        return qotrt_null_v();
    QArr *in = static_cast<QArr *>(args[0].u.p);
    int32_t n = in ? in->len : 0;
    fd_set rs;
    FD_ZERO(&rs);
    int nfds = 0, count = 0;
    for (int32_t i = 0; i < n && count < FD_SETSIZE - 1; i++)
    {
        QrtSock *p = qotrt_sock_of(&in->a[i]);
        if (!p)
            continue;
#if defined(_WIN32)
        FD_SET(p->fd, &rs);
#else
        FD_SET(static_cast<int>(p->fd), &rs);
        if (static_cast<int>(p->fd) + 1 > nfds)
            nfds = static_cast<int>(p->fd) + 1;
#endif
        count++;
    }
    if (count == 0)
        return qotrt_arr_new(0);
    timeval tv;
    tv.tv_sec = static_cast<long>(ms / 1000);
    tv.tv_usec = static_cast<long>((ms % 1000) * 1000);
    int r = ::select(
#if defined(_WIN32)
        0,
#else
        nfds,
#endif
        &rs, nullptr, nullptr, &tv);
    if (r <= 0)
        return qotrt_arr_new(0); // 超时/错误 → 空集（调用方语义上无就绪）
    V16 out = qotrt_arr_new(static_cast<int32_t>(r));
    QArr *a = static_cast<QArr *>(out.u.p);
    for (int32_t i = 0; i < n; i++)
    {
        QrtSock *p = qotrt_sock_of(&in->a[i]);
        if (!p)
            continue;
#if defined(_WIN32)
        if (FD_ISSET(p->fd, &rs))
#else
        if (FD_ISSET(static_cast<int>(p->fd), &rs))
#endif
            qotrt_arr_push(a, qotrt_num(in->a[i].u.d)); // 原句柄值直通
    }
    return out;
}
