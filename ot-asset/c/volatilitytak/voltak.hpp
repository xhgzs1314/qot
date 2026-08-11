/*万物皆流 一恒为碑
让不变之事从此静默
使用参考:test.cpp
*/
/***
*    ____                _           _   ____           ___   ___ _____      ____          ___        __       __  ___   _ 
*   / ___|_ __ ___  __ _| |_ ___  __| | | __ ) _   _   / _ \ / _ |_   _|    / ___|___   __| \ \      / ___     \ \/ | | | |
*  | |   | '__/ _ \/ _` | __/ _ \/ _` | |  _ \| | | | | | | | | | || |_____| |   / _ \ / _` |\ \ /\ / / _ \_____\  /| |_| |
*  | |___| | |  __| (_| | ||  __| (_| | | |_) | |_| | | |_| | |_| || |_____| |__| (_) | (_| | \ V  V |  __|_____/  \|  _  |
*   \____|_|  \___|\__,_|\__\___|\__,_| |____/ \__, |  \__\_\\___/ |_|      \____\___/ \__,_|  \_/\_/ \___|    /_/\_|_| |_|
*                                              |___/                                                                       
*/
#pragma once
#include <string>
#include <unordered_map>
#include <list>
#include <shared_mutex>
#include <optional>
#include <fstream>
#include <sstream>
#include <type_traits>
#include <mutex>
#include <functional>
#include <memory>
#include <any>
#include <utility>
#include <atomic>
#include <string_view>
#include <array>
#include <charconv>
#include <thread>
#include <vector>
namespace volatility
{
    inline size_t fast_hash(std::string_view sv) noexcept
    {
        constexpr size_t FNV_OFFSET = 14695981039346656037ULL;
        constexpr size_t FNV_PRIME = 1099511628211ULL;
        size_t hash = FNV_OFFSET;
        for (char c : sv)
        {
            hash ^= static_cast<size_t>(c);
            hash *= FNV_PRIME;
        }
        return hash;
    }
    class SmallString
    {
    public:
        static constexpr size_t SMALL_SIZE = 24;
        SmallString() : size_(0) { data_.small[0] = '\0'; }

        explicit SmallString(std::string_view sv)
        {
            if (sv.size() < SMALL_SIZE)
            {
                size_ = sv.size();
                memcpy(data_.small, sv.data(), sv.size());
                data_.small[sv.size()] = '\0';
            }
            else
            {
                size_ = sv.size() | LARGE_FLAG;
                data_.large = new std::string(sv);
            }
        }

        SmallString(const SmallString &other)
        {
            copy_from(other);
        }

        SmallString(SmallString &&other) noexcept
        {
            move_from(std::move(other));
        }

        ~SmallString()
        {
            if (is_large())
                delete data_.large;
        }

        SmallString &operator=(const SmallString &other)
        {
            if (this != &other)
            {
                if (is_large())
                    delete data_.large;
                copy_from(other);
            }
            return *this;
        }

        SmallString &operator=(SmallString &&other) noexcept
        {
            if (this != &other)
            {
                if (is_large())
                    delete data_.large;
                move_from(std::move(other));
            }
            return *this;
        }

        std::string_view view() const noexcept
        {
            return is_large() ? std::string_view(*data_.large)
                              : std::string_view(data_.small, size_);
        }

        bool operator==(std::string_view sv) const noexcept
        {
            return view() == sv;
        }

    private:
        static constexpr size_t LARGE_FLAG = 1ULL << (sizeof(size_t) * 8 - 1);

        bool is_large() const noexcept { return size_ & LARGE_FLAG; }

        void copy_from(const SmallString &other)
        {
            if (other.is_large())
            {
                size_ = other.size_;
                data_.large = new std::string(*other.data_.large);
            }
            else
            {
                size_ = other.size_;
                memcpy(data_.small, other.data_.small, other.size_ + 1);
            }
        }

        void move_from(SmallString &&other) noexcept
        {
            size_ = other.size_;
            if (other.is_large())
            {
                data_.large = other.data_.large;
                other.data_.large = nullptr;
                other.size_ = 0;
            }
            else
            {
                memcpy(data_.small, other.data_.small, other.size_ + 1);
                other.size_ = 0;
            }
        }

        size_t size_;
        union
        {
            char small[SMALL_SIZE];
            std::string *large;
        } data_;
    };

    template <typename T>
    std::string value_to_string(const T &value);
    template <typename T>
    T string_to_value(const std::string &str);

    class ChangeDetector
    {
    public:
        virtual ~ChangeDetector() = default;
        virtual bool detect(const std::string &old_val, const std::string &new_val) const = 0;
        virtual bool detect_fast(std::string_view old_val, std::string_view new_val) const
        {
            return old_val != new_val;
        }
        virtual std::unique_ptr<ChangeDetector> clone() const = 0;
    };

    class ExactMatchDetector : public ChangeDetector
    {
    public:
        bool detect(const std::string &old_val, const std::string &new_val) const override
        {
            return old_val != new_val;
        }
        bool detect_fast(std::string_view old_val, std::string_view new_val) const override
        {
            return old_val != new_val;
        }
        std::unique_ptr<ChangeDetector> clone() const override
        {
            return std::make_unique<ExactMatchDetector>();
        }
    };

    template <typename Key, typename Value, size_t ShardCount = 16>
    class ConcurrentLRUCache
    {
    private:
        struct Node
        {
            Key key;
            Value value;
            std::atomic<Node *> next{nullptr};
            std::atomic<Node *> prev{nullptr};
            std::atomic<bool> in_use{true};

            Node() = default;
            Node(Key &&k, Value &&v) : key(std::move(k)), value(std::move(v)) {}
        };

        struct Shard
        {
            alignas(64) std::atomic<Node *> head{nullptr};
            alignas(64) std::atomic<Node *> tail{nullptr};
            alignas(64) std::atomic<size_t> size{0};
            std::atomic<Node *> free_list{nullptr};
            std::unique_ptr<std::mutex> mtx;

            Shard() : mtx(std::make_unique<std::mutex>()) {}

            Node *allocate_node(Key &&key, Value &&value)
            {
                Node *node = nullptr;
                node = free_list.load(std::memory_order_acquire);
                while (node && !free_list.compare_exchange_weak(node, node->next.load()))
                {
                }
                if (node)
                {
                    node->key = std::move(key);
                    node->value = std::move(value);
                    node->in_use.store(true, std::memory_order_release);
                    return node;
                }
                return new Node(std::move(key), std::move(value));
            }

            void recycle_node(Node *node)
            {
                node->in_use.store(false, std::memory_order_release);
                Node *old_head = free_list.load();
                do
                {
                    node->next.store(old_head, std::memory_order_relaxed);
                } while (!free_list.compare_exchange_weak(old_head, node));
            }
        };

        std::array<Shard, ShardCount> shards_;
        size_t capacity_per_shard_;

        size_t get_shard_index(const Key &key) const noexcept
        {
            return fast_hash(key) % ShardCount;
        }

    public:
        explicit ConcurrentLRUCache(size_t total_capacity)
            : capacity_per_shard_((total_capacity + ShardCount - 1) / ShardCount) {}

        ~ConcurrentLRUCache()
        {
            for (auto &shard : shards_)
            {
                Node *node = shard.head.load();
                while (node)
                {
                    Node *next = node->next.load();
                    delete node;
                    node = next;
                }
                node = shard.free_list.load();
                while (node)
                {
                    Node *next = node->next.load();
                    delete node;
                    node = next;
                }
            }
        }

        bool put(const Key &key, const Value &value)
        {
            size_t idx = get_shard_index(key);
            auto &shard = shards_[idx];

            Key key_copy = key;
            Value value_copy = value;
            Node *curr = shard.head.load(std::memory_order_acquire);
            while (curr)
            {
                if (curr->in_use.load(std::memory_order_acquire) && curr->key == key)
                {
                    curr->value = std::move(value_copy);
                    return false;
                }
                curr = curr->next.load(std::memory_order_acquire);
            }
            Node *new_node = shard.allocate_node(std::move(key_copy), std::move(value_copy));
            Node *old_head = shard.head.load();
            do
            {
                new_node->next.store(old_head, std::memory_order_relaxed);
                if (old_head)
                    old_head->prev.store(new_node, std::memory_order_relaxed);
            } while (!shard.head.compare_exchange_weak(old_head, new_node));

            if (shard.tail.load() == nullptr)
            {
                shard.tail.store(new_node);
            }

            size_t current_size = shard.size.fetch_add(1, std::memory_order_relaxed) + 1;
            if (current_size > capacity_per_shard_)
            {
                Node *old_tail = shard.tail.load();
                Node *new_tail = old_tail ? old_tail->prev.load() : nullptr;
                if (new_tail)
                    new_tail->next.store(nullptr);
                shard.tail.store(new_tail);
                if (old_tail)
                {
                    shard.recycle_node(old_tail);
                    shard.size.fetch_sub(1);
                }
            }
            return true;
        }

        std::optional<Value> get(const Key &key)
        {
            size_t idx = get_shard_index(key);
            auto &shard = shards_[idx];

            Node *curr = shard.head.load(std::memory_order_acquire);
            Node *prev = nullptr;

            while (curr)
            {
                if (curr->in_use.load(std::memory_order_acquire) && curr->key == key)
                {
                    if (prev)
                    {
                        Node *next = curr->next.load();
                        prev->next.store(next);
                        if (next)
                            next->prev.store(prev);

                        Node *old_head = shard.head.load();
                        do
                        {
                            curr->next.store(old_head, std::memory_order_relaxed);
                            if (old_head)
                                old_head->prev.store(curr, std::memory_order_relaxed);
                        } while (!shard.head.compare_exchange_weak(old_head, curr));

                        if (shard.tail.load() == curr)
                        {
                            shard.tail.store(prev);
                        }
                    }
                    return curr->value;
                }
                prev = curr;
                curr = curr->next.load(std::memory_order_acquire);
            }
            return std::nullopt;
        }

        bool contains(const Key &key) const
        {
            size_t idx = get_shard_index(key);
            auto &shard = const_cast<Shard &>(shards_[idx]);
            Node *curr = shard.head.load(std::memory_order_acquire);
            while (curr)
            {
                if (curr->in_use.load(std::memory_order_acquire) && curr->key == key)
                {
                    return true;
                }
                curr = curr->next.load(std::memory_order_acquire);
            }
            return false;
        }

        size_t size() const
        {
            size_t total = 0;
            for (auto &shard : shards_)
            {
                total += shard.size.load(std::memory_order_relaxed);
            }
            return total;
        }

        void clear()
        {
            for (auto &shard : shards_)
            {
                Node *node = shard.head.load();
                while (node)
                {
                    Node *next = node->next.load();
                    shard.recycle_node(node);
                    node = next;
                }
                shard.head.store(nullptr);
                shard.tail.store(nullptr);
                shard.size.store(0);
            }
        }
    };

    class SharedMutexFast
    {
    private:
        alignas(64) std::atomic<int> state_{0};
        static constexpr int WRITER_BIT = -1;

    public:
        void lock_shared()
        {
            int expected = state_.load(std::memory_order_relaxed);
            do
            {
                if (expected == WRITER_BIT)
                {
                    std::this_thread::yield();
                    expected = state_.load(std::memory_order_relaxed);
                    continue;
                }
            } while (!state_.compare_exchange_weak(expected, expected + 1,
                                                   std::memory_order_acquire,
                                                   std::memory_order_relaxed));
        }

        void unlock_shared()
        {
            state_.fetch_sub(1, std::memory_order_release);
        }

        void lock()
        {
            int expected = 0;
            while (!state_.compare_exchange_weak(expected, WRITER_BIT,
                                                 std::memory_order_acquire,
                                                 std::memory_order_relaxed))
            {
                expected = 0;
                std::this_thread::yield();
            }
            while (state_.load(std::memory_order_relaxed) != WRITER_BIT)
            {
                std::this_thread::yield();
            }
        }

        void unlock()
        {
            state_.store(0, std::memory_order_release);
        }
    };

    class Serializer
    {
    public:
        virtual ~Serializer() = default;
        virtual void export_stable(const std::unordered_map<std::string, std::string> &stable_map, std::ostream &os) const = 0;
        virtual std::unordered_map<std::string, std::string> import_stable(std::istream &is) const = 0;
        virtual std::unique_ptr<Serializer> clone() const = 0;
    };

    class TsvSerializer : public Serializer
    {
    public:
        void export_stable(const std::unordered_map<std::string, std::string> &stable_map, std::ostream &os) const override
        {
            os.rdbuf()->pubsetbuf(nullptr, 8192);
            for (const auto &[key, val] : stable_map)
                os << key << '\t' << val << '\n';
        }

        std::unordered_map<std::string, std::string> import_stable(std::istream &is) const override
        {
            std::unordered_map<std::string, std::string> result;
            result.reserve(1024);
            std::string line, key, value;
            line.reserve(256);
            while (std::getline(is, line))
            {
                size_t tab_pos = line.find('\t');
                if (tab_pos != std::string::npos)
                {
                    key = line.substr(0, tab_pos);
                    value = line.substr(tab_pos + 1);
                    result[std::move(key)] = std::move(value);
                }
            }
            return result;
        }

        std::unique_ptr<Serializer> clone() const override
        {
            return std::make_unique<TsvSerializer>();
        }
    };

    struct VolatilityTrackerConfig
    {
        size_t min_accesses = 100;
        double stability_ratio = 0.99;
        bool auto_commit_stable = true;
        std::unique_ptr<ChangeDetector> detector;
        size_t lru_capacity = 0;
        std::unique_ptr<Serializer> serializer;
        bool use_concurrent_lru = true;
        size_t cache_line_pad = 64;

        VolatilityTrackerConfig();
        VolatilityTrackerConfig(const VolatilityTrackerConfig &other);
        VolatilityTrackerConfig &operator=(const VolatilityTrackerConfig &other);
        VolatilityTrackerConfig(VolatilityTrackerConfig &&) = default;
        VolatilityTrackerConfig &operator=(VolatilityTrackerConfig &&) = default;
    };

    struct alignas(64) UnitInfo
    {
        std::string last_value;
        size_t access_count = 0;
        size_t change_count = 0;
        bool is_stable = false;
        bool ever_changed = false;
        void reset()
        {
            last_value.clear();
            access_count = 0;
            change_count = 0;
            is_stable = false;
            ever_changed = false;
        }
    };

    class VolatilityTrackerExt
    {
    public:
        using Config = VolatilityTrackerConfig;
        explicit VolatilityTrackerExt() : VolatilityTrackerExt(Config{}) {}
        explicit VolatilityTrackerExt(Config cfg);

        bool observe(const std::string &key, const std::string &current_value);

        template <typename T>
        bool observe(const std::string &key, const T &current_value)
        {
            return observe(key, value_to_string(current_value));
        }
        bool observe_fast(std::string_view key, std::string_view current_value);

        bool is_stable(const std::string &key) const;
        std::optional<std::string> get_stable_value(const std::string &key) const;
        std::optional<std::string_view> get_stable_value_fast(std::string_view key) const;

        template <typename T>
        std::optional<T> get_stable_value(const std::string &key) const
        {
            auto sv = get_stable_value(key);
            if (sv)
                return string_to_value<T>(*sv);
            return std::nullopt;
        }

        void force_stable(const std::string &key, const std::string &constant_value);
        void refresh_stability();
        void export_stable(std::ostream &os) const;
        bool export_stable_to_file(const std::string &path) const;
        void import_stable(std::istream &is);
        bool import_stable_from_file(const std::string &path);

        struct Stats
        {
            size_t total_units = 0;
            size_t stable_units = 0;
            size_t lru_size = 0;
        };
        Stats get_stats() const;
        void batch_observe(const std::vector<std::pair<std::string, std::string>> &observations);

    private:
        struct ShardedData
        {
            mutable SharedMutexFast mutex;
            std::unordered_map<std::string, UnitInfo> units;
            ShardedData() { units.reserve(1024); }
        };

        static constexpr size_t NUM_SHARDS = 8;
        std::array<ShardedData, NUM_SHARDS> shards_;

        Config config_;
        std::unique_ptr<ConcurrentLRUCache<std::string, UnitInfo, 16>> concurrent_lru_;

        size_t get_shard_index(std::string_view key) const noexcept
        {
            return fast_hash(key) % NUM_SHARDS;
        }

        void update_stability(UnitInfo &info) noexcept;
    };

    inline VolatilityTrackerConfig::VolatilityTrackerConfig()
        : detector(std::make_unique<ExactMatchDetector>()),
          serializer(std::make_unique<TsvSerializer>()) {}

    inline VolatilityTrackerConfig::VolatilityTrackerConfig(const VolatilityTrackerConfig &other)
        : min_accesses(other.min_accesses), stability_ratio(other.stability_ratio),
          auto_commit_stable(other.auto_commit_stable), lru_capacity(other.lru_capacity),
          use_concurrent_lru(other.use_concurrent_lru), cache_line_pad(other.cache_line_pad),
          detector(other.detector ? other.detector->clone() : nullptr),
          serializer(other.serializer ? other.serializer->clone() : nullptr) {}

    inline VolatilityTrackerConfig &VolatilityTrackerConfig::operator=(const VolatilityTrackerConfig &other)
    {
        if (this != &other)
        {
            min_accesses = other.min_accesses;
            stability_ratio = other.stability_ratio;
            auto_commit_stable = other.auto_commit_stable;
            lru_capacity = other.lru_capacity;
            use_concurrent_lru = other.use_concurrent_lru;
            cache_line_pad = other.cache_line_pad;
            detector = other.detector ? other.detector->clone() : nullptr;
            serializer = other.serializer ? other.serializer->clone() : nullptr;
        }
        return *this;
    }

    inline VolatilityTrackerExt::VolatilityTrackerExt(Config cfg)
        : config_(std::move(cfg))
    {
        if (!config_.detector)
            config_.detector = std::make_unique<ExactMatchDetector>();
        if (!config_.serializer)
            config_.serializer = std::make_unique<TsvSerializer>();
        if (config_.lru_capacity > 0 && config_.use_concurrent_lru)
        {
            concurrent_lru_ = std::make_unique<ConcurrentLRUCache<std::string, UnitInfo, 16>>(config_.lru_capacity);
        }
    }

    inline void VolatilityTrackerExt::update_stability(UnitInfo &info) noexcept
    {
        if (info.access_count >= config_.min_accesses)
        {
            double stability = 1.0 - static_cast<double>(info.change_count) / info.access_count;
            if (stability >= config_.stability_ratio)
            {
                info.is_stable = true;
            }
        }
    }

    inline bool VolatilityTrackerExt::observe(const std::string &key, const std::string &current_value)
    {
        size_t shard_idx = get_shard_index(key);
        auto &shard = shards_[shard_idx];

        shard.mutex.lock();
        auto &info = shard.units[key];
        bool changed = config_.detector->detect(info.last_value, current_value);

        if (changed)
        {
            info.ever_changed = true;
            info.change_count++;
            info.last_value = current_value;
        }
        info.access_count++;

        if (config_.auto_commit_stable && !info.is_stable)
        {
            update_stability(info);
        }
        shard.mutex.unlock();

        if (concurrent_lru_)
        {
            concurrent_lru_->put(key, info);
        }
        return changed;
    }

    inline bool VolatilityTrackerExt::observe_fast(std::string_view key, std::string_view current_value)
    {
        size_t shard_idx = get_shard_index(key);
        auto &shard = shards_[shard_idx];

        shard.mutex.lock();
        std::string key_str(key);
        std::string val_str(current_value);
        auto &info = shard.units[std::move(key_str)];
        bool changed = config_.detector->detect_fast(info.last_value, val_str);

        if (changed)
        {
            info.ever_changed = true;
            info.change_count++;
            info.last_value = std::move(val_str);
        }
        info.access_count++;

        if (config_.auto_commit_stable && !info.is_stable)
        {
            update_stability(info);
        }
        shard.mutex.unlock();

        if (concurrent_lru_)
        {
            concurrent_lru_->put(std::string(key), info);
        }
        return changed;
    }

    inline void VolatilityTrackerExt::batch_observe(const std::vector<std::pair<std::string, std::string>> &observations)
    {
        std::array<std::vector<std::pair<std::string, std::string>>, NUM_SHARDS> groups;
        for (const auto &[key, val] : observations)
        {
            groups[get_shard_index(key)].emplace_back(key, val);
        }

        for (size_t i = 0; i < NUM_SHARDS; ++i)
        {
            if (groups[i].empty())
                continue;
            auto &shard = shards_[i];
            shard.mutex.lock();
            for (const auto &[key, val] : groups[i])
            {
                auto &info = shard.units[key];
                bool changed = config_.detector->detect(info.last_value, val);
                if (changed)
                {
                    info.ever_changed = true;
                    info.change_count++;
                    info.last_value = val;
                }
                info.access_count++;
                if (config_.auto_commit_stable && !info.is_stable)
                {
                    update_stability(info);
                }
            }
            shard.mutex.unlock();
        }
    }

    inline bool VolatilityTrackerExt::is_stable(const std::string &key) const
    {
        size_t shard_idx = get_shard_index(key);
        auto &shard = shards_[shard_idx];
        std::shared_lock<SharedMutexFast> lock(shard.mutex);
        auto it = shard.units.find(key);
        return it != shard.units.end() && it->second.is_stable;
    }

    inline std::optional<std::string> VolatilityTrackerExt::get_stable_value(const std::string &key) const
    {
        size_t shard_idx = get_shard_index(key);
        auto &shard = shards_[shard_idx];
        std::shared_lock<SharedMutexFast> lock(shard.mutex);
        auto it = shard.units.find(key);
        if (it != shard.units.end() && it->second.is_stable)
        {
            return it->second.last_value;
        }
        return std::nullopt;
    }

    inline void VolatilityTrackerExt::force_stable(const std::string &key, const std::string &constant_value)
    {
        size_t shard_idx = get_shard_index(key);
        auto &shard = shards_[shard_idx];
        shard.mutex.lock();
        auto &info = shard.units[key];
        info.last_value = constant_value;
        info.ever_changed = false;
        info.change_count = 0;
        info.access_count = config_.min_accesses;
        info.is_stable = true;
        shard.mutex.unlock();

        if (concurrent_lru_)
        {
            concurrent_lru_->put(key, info);
        }
    }

    inline void VolatilityTrackerExt::refresh_stability()
    {
        for (auto &shard : shards_)
        {
            shard.mutex.lock();
            for (auto &[_, info] : shard.units)
            {
                update_stability(info);
            }
            shard.mutex.unlock();
        }
    }

    inline void VolatilityTrackerExt::export_stable(std::ostream &os) const
    {
        std::unordered_map<std::string, std::string> stable_map;
        for (size_t i = 0; i < NUM_SHARDS; ++i)
        {
            auto &shard = shards_[i];
            std::shared_lock<SharedMutexFast> lock(shard.mutex);
            for (const auto &[key, info] : shard.units)
            {
                if (info.is_stable)
                {
                    stable_map[key] = info.last_value;
                }
            }
        }
        config_.serializer->export_stable(stable_map, os);
    }

    inline bool VolatilityTrackerExt::export_stable_to_file(const std::string &path) const
    {
        std::ofstream ofs(path);
        if (!ofs)
            return false;
        export_stable(ofs);
        return true;
    }

    inline void VolatilityTrackerExt::import_stable(std::istream &is)
    {
        auto stable_map = config_.serializer->import_stable(is);
        for (const auto &[key, val] : stable_map)
        {
            force_stable(key, val);
        }
    }

    inline bool VolatilityTrackerExt::import_stable_from_file(const std::string &path)
    {
        std::ifstream ifs(path);
        if (!ifs)
            return false;
        import_stable(ifs);
        return true;
    }

    inline VolatilityTrackerExt::Stats VolatilityTrackerExt::get_stats() const
    {
        Stats s;
        for (auto &shard : shards_)
        {
            std::shared_lock<SharedMutexFast> lock(shard.mutex);
            s.total_units += shard.units.size();
            for (const auto &[_, info] : shard.units)
            {
                if (info.is_stable)
                    ++s.stable_units;
            }
        }
        if (concurrent_lru_)
            s.lru_size = concurrent_lru_->size();
        return s;
    }
    template <typename T>
    class OptimizedExt
    {
    public:
        using Fetcher = std::function<T()>;

        OptimizedExt(VolatilityTrackerExt &tracker, std::string key, Fetcher fetcher)
            : tracker_(tracker), key_(std::move(key)), fetcher_(std::move(fetcher)) {}

        T get()
        {
            if (auto stable = tracker_.get_stable_value<T>(key_))
            {
                return *stable;
            }
            T fresh = fetcher_();
            tracker_.observe(key_, fresh);
            return fresh;
        }
        std::vector<T> get_batch(size_t count)
        {
            std::vector<T> results;
            results.reserve(count);

            if (auto stable = tracker_.get_stable_value<T>(key_))
            {
                results.assign(count, *stable);
                return results;
            }

            for (size_t i = 0; i < count; ++i)
            {
                results.push_back(fetcher_());
            }
            tracker_.observe(key_, results[0]);
            return results;
        }

        void refresh()
        {
            T fresh = fetcher_();
            tracker_.observe(key_, fresh);
        }

    private:
        VolatilityTrackerExt &tracker_;
        std::string key_;
        Fetcher fetcher_;
    };
    template <typename T>
    inline std::string value_to_string(const T &value)
    {
        if constexpr (std::is_same_v<T, std::string>)
            return value;
        else if constexpr (std::is_arithmetic_v<T>)
        {
            std::string result;
            result.resize(32);
            auto [ptr, ec] = std::to_chars(result.data(), result.data() + result.size(), value);
            result.resize(ptr - result.data());
            return result;
        }
        else
            static_assert(sizeof(T) == 0, "需要自定义 value_to_string");
    }

    template <typename T>
    inline T string_to_value(const std::string &str)
    {
        if constexpr (std::is_same_v<T, std::string>)
            return str;
        else if constexpr (std::is_integral_v<T> && !std::is_same_v<T, bool>)
        {
            T result;
            std::from_chars(str.data(), str.data() + str.size(), result);
            return result;
        }
        else if constexpr (std::is_floating_point_v<T>)
        {
            T result;
            std::from_chars(str.data(), str.data() + str.size(), result);
            return result;
        }
        else if constexpr (std::is_same_v<T, bool>)
            return str == "1" || str == "true";
        else
            static_assert(sizeof(T) == 0, "需要自定义 string_to_value");
    }
}