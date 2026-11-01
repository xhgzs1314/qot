#ifndef QOT_FLATDICT_H
#define QOT_FLATDICT_H
// ============================================================================
// QOT 扁平字典（开放寻址）——  T-33（7.4 机器码 P1 表示层重构）
//
// 取代 Value 字典内部的 std::unordered_map<std::string, Value>：
//   * 节点式哈希表每次访问 = 指针跳转 + 节点分配 + 哈希/比较，缓存极不友好；
//   * 本实现把 (hash, key, value) 内联进【单个】连续槽位数组：
//       - 一次探测 ≈ 一条 cache line（槽 56B，SSO 键数据也在同一 line）；
//       - 槽内缓存哈希，重哈希/删除时不再重算，比较先比 64 位哈希再比键；
//       - 线性探测 + 2 的幂容量 + 装载因子 0.75 + 后退移位删除（无墓碑链残留）；
//   * 迭代顺序 = 槽位顺序（README 已声明 dict_keys 顺序无保证，换表示不破坏兼容）。
//
// 只提供代码库实际使用的 unordered_map 子集（operator[]/find/end/count/size/empty/
// erase/迭代），编译器会在漏实现处报错而不是静默降级。
//
// ⚠ 包含约束：本文件的成员函数体用到 Value 的完整定义，因此【只能】被 vm_types.h
//   在 Value 类体结束之后、其 out-of-line inline 成员定义之前 include，
//   不允许在其它位置单独包含；且本文件处于 namespace qot 内部，不再自行开命名空间。
// ============================================================================
#include <cstdint>
#include <cstring>
#include <string>
#include <utility>
#include <vector>
#include <stdexcept>
#include "vm_profile.h"
#include "qot_hash.h"   // qot_hash_string 在这里（StringBox 也要用，见 vm_types.h）

    class FlatDict
    {
    public:
        struct Pair
        {
            std::string first;
            Value second;
        };

        // 单数组布局：哈希内联进槽位，一次探测一条 cache line。
        // h 值语义：H_EMPTY=0（真空位）、H_DELETED=1（墓碑）、≥2 为有效哈希。
        struct Slot
        {
            uint64_t h;
            Pair kv;
        };
        static const uint64_t H_EMPTY = 0;
        static const uint64_t H_DELETED = 1;

        template <class S>
        class Iter
        {
        public:
            Iter(Slot *slots, size_t cap, size_t i) : m_slots(slots), m_cap(cap), m_i(i)
            {
                skip_dead();
            }
            template <class Q>
            bool operator!=(const Iter<Q> &o) const { return m_i != o.m_i; }
            template <class Q>
            bool operator==(const Iter<Q> &o) const { return m_i == o.m_i; }
            Iter &operator++()
            {
                ++m_i;
                skip_dead();
                return *this;
            }
            // 统一暴露 pair 接口（first/second），对齐 unordered_map 的使用习惯
            auto *operator->() const { return &m_slots[m_i].kv; }
            auto &operator*() const { return m_slots[m_i].kv; }

        private:
            void skip_dead()
            {
                while (m_i < m_cap && m_slots[m_i].h <= H_DELETED)
                    ++m_i;
                if (m_i >= m_cap)
                    m_i = m_cap;
            }
            Slot *m_slots;
            size_t m_cap;
            size_t m_i;
        };

        using iterator = Iter<Slot>;
        using const_iterator = Iter<const Slot>;

        FlatDict() : m_cap(0), m_mask(0), m_size(0) {}
        FlatDict(const FlatDict &o) { copy_from(o); }
        FlatDict(FlatDict &&o) noexcept { move_from(std::move(o)); }
        FlatDict &operator=(const FlatDict &o)
        {
            if (this != &o)
            {
                clear();
                copy_from(o);
            }
            return *this;
        }
        FlatDict &operator=(FlatDict &&o) noexcept
        {
            if (this != &o)
            {
                clear();
                move_from(std::move(o));
            }
            return *this;
        }

        // ---- 容量与状态 ----
        size_t size() const { return m_size; }
        bool empty() const { return m_size == 0; }
        size_t bucket_count() const { return m_cap; }

        void clear()
        {
            for (size_t i = 0; i < m_cap; i++)
            {
                if (m_slots[i].h > H_DELETED)
                    m_slots[i].~Slot();
            }
            ::operator delete(m_slots);
            m_slots = nullptr;
            m_cap = 0;
            m_mask = 0;
            m_size = 0;
        }

        int count(const std::string &key) const
        {
            return find_slot(key) != INVALID ? 1 : 0;
        }

        iterator begin() { return iterator(m_slots, m_cap, 0); }
        iterator end() { return iterator(m_slots, m_cap, m_cap); }
        const_iterator begin() const { return const_iterator(m_slots, m_cap, 0); }
        const_iterator end() const { return const_iterator(m_slots, m_cap, m_cap); }

        iterator find(const std::string &key)
        {
            size_t s = find_slot_h(qot_hash_string(key.data(), key.size()), key);
            return iterator(m_slots, m_cap, s == INVALID ? m_cap : s);
        }
        const_iterator find(const std::string &key) const
        {
            size_t s = find_slot_h(qot_hash_string(key.data(), key.size()), key);
            return const_iterator(m_slots, m_cap, s == INVALID ? m_cap : s);
        }

        // ---- 哈希感知接口（T-33 关键路径）：调用方提供已缓存的键哈希 ----
        // INDEX_GET / INDEX_SET / MAKE_DICT 的键是 Value 字符串（常量池里同一 StringBox
        // 反复出现），哈希在 StringBox 内缓存后，热路径从「每访问重算哈希」变 O(1) 取缓存。
        const Value *find_ptr_h(uint64_t h, const std::string &key) const
        {
            QOT_PROF_ADD(dictFind, 1);
            size_t s = find_slot_h(h, key);
            return s == INVALID ? nullptr : &m_slots[s].kv.second;
        }
        Value &at_h(uint64_t h, const std::string &key)
        {
            QOT_PROF_ADD(dictFind, 1);
            if (m_cap == 0)
                rehash(8);
            else if ((m_size + 1) * 4 >= m_cap * 3)
                rehash(m_cap * 2);

            size_t i = static_cast<size_t>(h) & m_mask;
            size_t tomb = INVALID;
            while (true)
            {
                uint64_t sh = m_slots[i].h;
                if (sh == H_EMPTY)
                {
                    size_t at = (tomb != INVALID) ? tomb : i;
                    // 复用墓碑槽无需再析构：erase_at 已经析构过其键值（h==H_DELETED 即标记）
                    new (&m_slots[at]) Slot{h, Pair{key, Value()}};
                    m_size++;
                    return m_slots[at].kv.second;
                }
                if (sh == H_DELETED)
                {
                    if (tomb == INVALID)
                        tomb = i;
                }
                else if (sh == h && m_slots[i].kv.first == key)
                {
                    return m_slots[i].kv.second;
                }
                i = (i + 1) & m_mask;
            }
        }

        // ---- 插入 / 访问 ----
        Value &operator[](const std::string &key)
        {
            return at_h(qot_hash_string(key.data(), key.size()), key);
        }

        // ---- 删除（后退移位，不留墓碑链） ----
        size_t erase(const std::string &key)
        {
            size_t s = find_slot(key);
            if (s == INVALID)
                return 0;
            erase_at(s);
            return 1;
        }

        ~FlatDict()
        {
            for (size_t i = 0; i < m_cap; i++)
            {
                if (m_slots && m_slots[i].h > H_DELETED)
                    m_slots[i].~Slot();
            }
            ::operator delete(m_slots);
        }

    private:
        static const size_t INVALID = static_cast<size_t>(-1);

        size_t find_slot(const std::string &key) const
        {
            return find_slot_h(qot_hash_string(key.data(), key.size()), key);
        }

        size_t find_slot_h(uint64_t h, const std::string &key) const
        {
            if (m_size == 0)
                return INVALID;
            size_t i = static_cast<size_t>(h) & m_mask;
            while (true)
            {
                uint64_t sh = m_slots[i].h;
                if (sh == H_EMPTY)
                    return INVALID; // 线性探测遇到真空位 = 一定不存在
                if (sh == h && m_slots[i].kv.first == key)
                    return i;
                i = (i + 1) & m_mask;
            }
            // 注意：墓碑（H_DELETED）不终止探测，保证删除后探测链完整
        }

        void erase_at(size_t s)
        {
            // 后退移位：把簇内后续元素逐个前移，保持探测链不断，簇不留墓碑
            size_t hole = s;
            size_t i = (s + 1) & m_mask;
            while (m_slots[i].h != H_EMPTY)
            {
                if (m_slots[i].h > H_DELETED)
                {
                    size_t home = static_cast<size_t>(m_slots[i].h) & m_mask;
                    bool can_move = ((i > hole) ? (home <= hole || home > i)
                                                : (home <= hole && home > i));
                    if (can_move)
                    {
                        m_slots[hole].h = m_slots[i].h;
                        m_slots[hole].kv.first = std::move(m_slots[i].kv.first);
                        m_slots[hole].kv.second = std::move(m_slots[i].kv.second);
                        hole = i;
                    }
                }
                i = (i + 1) & m_mask;
            }
            m_slots[hole].~Slot();
            m_slots[hole].h = H_DELETED;
            m_size--;
        }

        void rehash(size_t new_cap)
        {
            Slot *old_slots = m_slots;
            size_t old_cap = m_cap;

            m_cap = new_cap;
            m_mask = new_cap - 1;
            m_size = 0;
            m_slots = static_cast<Slot *>(::operator new(sizeof(Slot) * new_cap));
            for (size_t i = 0; i < new_cap; i++)
                m_slots[i].h = H_EMPTY;

            for (size_t i = 0; i < old_cap; i++)
            {
                if (old_slots[i].h > H_DELETED)
                {
                    // 哈希已缓存在槽内，rehash 零重算
                    uint64_t h = old_slots[i].h;
                    size_t j = static_cast<size_t>(h) & m_mask;
                    while (m_slots[j].h != H_EMPTY)
                        j = (j + 1) & m_mask;
                    new (&m_slots[j]) Slot{h, std::move(old_slots[i].kv)};
                    old_slots[i].~Slot();
                    m_size++;
                }
            }
            ::operator delete(old_slots);
        }

        void copy_from(const FlatDict &o)
        {
            m_cap = o.m_cap;
            m_mask = o.m_mask;
            m_size = o.m_size;
            if (m_cap)
            {
                m_slots = static_cast<Slot *>(::operator new(sizeof(Slot) * m_cap));
                for (size_t i = 0; i < m_cap; i++)
                {
                    if (o.m_slots[i].h > H_DELETED)
                        new (&m_slots[i]) Slot{o.m_slots[i]}; // 深拷贝（string/Value 拷贝构造）
                    else
                        m_slots[i].h = o.m_slots[i].h;
                }
            }
            else
            {
                m_slots = nullptr;
            }
        }

        void move_from(FlatDict &&o) noexcept
        {
            m_slots = o.m_slots;
            m_cap = o.m_cap;
            m_mask = o.m_mask;
            m_size = o.m_size;
            o.m_slots = nullptr;
            o.m_cap = 0;
            o.m_mask = 0;
            o.m_size = 0;
        }

        Slot *m_slots = nullptr;
        size_t m_cap;
        size_t m_mask;
        size_t m_size;
    };

#endif // QOT_FLATDICT_H
