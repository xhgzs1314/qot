#include <windows.h>
#include <vector>
#include <cstring>
#include <cstdlib>
#include <mutex>
#define MEMORY_POOL_BLOCK_SIZE (64 * 1024)  
#define MEMORY_POOL_ALIGNMENT 8             
#define MEMORY_POOL_MAX_BLOCK (1024 * 1024) 
#define ALIGN_UP(x, align) (((x) + (align) - 1) & ~((align) - 1))
struct MemoryBlock {
    void* data;
    size_t used;
    size_t size;
    MemoryBlock* next;
    
    MemoryBlock(size_t block_size) : used(0), size(block_size), next(nullptr) {
        data = VirtualAlloc(NULL, block_size, MEM_COMMIT, PAGE_READWRITE);
    }
    
    ~MemoryBlock() {
        if(data) VirtualFree(data, 0, MEM_RELEASE);
    }
    
    void* alloc(size_t alloc_size) {
        alloc_size = ALIGN_UP(alloc_size, MEMORY_POOL_ALIGNMENT);
        if(used + alloc_size > size) return nullptr;
        void* ptr = (char*)data + used;
        used += alloc_size;
        return ptr;
    }
    
    size_t remaining() const { return size - used; }
};
class MemoryPool {
private:
    MemoryBlock* current_block;
    std::vector<MemoryBlock*> all_blocks;
    std::mutex pool_mutex;
    size_t total_allocated;
    size_t total_used;
    size_t block_size;
    MemoryBlock* allocate_block(size_t min_size) {
        size_t alloc_size = block_size;
        if(min_size > alloc_size) alloc_size = min_size;
        
        MemoryBlock* new_block = new MemoryBlock(alloc_size);
        if(!new_block->data) {
            delete new_block;
            return nullptr;
        }
        
        new_block->next = current_block;
        current_block = new_block;
        all_blocks.push_back(new_block);
        total_allocated += alloc_size;
        return new_block;
    }
    
public:
    MemoryPool(size_t block_size = MEMORY_POOL_BLOCK_SIZE) 
        : current_block(nullptr), total_allocated(0), total_used(0), block_size(block_size) {
        allocate_block(block_size);
    }
    
    ~MemoryPool() {
        clear();
    }
    void* alloc(size_t size) {
        if(size == 0) return nullptr;
        if(size > MEMORY_POOL_MAX_BLOCK) return nullptr;  
        std::lock_guard<std::mutex> lock(pool_mutex);
        void* ptr = current_block->alloc(size);
        if(ptr) {
            total_used += size;
            return ptr;
        }
        MemoryBlock* new_block = allocate_block(size);
        if(!new_block) return nullptr;
        ptr = new_block->alloc(size);
        if(ptr) {
            total_used += size;
            return ptr;
        }
        
        return nullptr;
    }
    void* alloc_array(size_t count, size_t elem_size) {
        size_t total_size = count * elem_size;
        return alloc(total_size);
    }
    void* alloc_zero(size_t size) {
        void* ptr = alloc(size);
        if(ptr) memset(ptr, 0, size);
        return ptr;
    }
    char* alloc_string(const char* str) {
        if(!str) return nullptr;
        size_t len = strlen(str) + 1;
        char* ptr = (char*)alloc(len);
        if(ptr) memcpy(ptr, str, len);
        return ptr;
    }
    char* dup_string(const char* str) {
        if(!str) return nullptr;
        size_t len = strlen(str) + 1;
        char* ptr = (char*)alloc(len);
        if(ptr) memcpy(ptr, str, len);
        return ptr;
    }
    void reset() {
        std::lock_guard<std::mutex> lock(pool_mutex);
        for(auto block : all_blocks) {
            delete block;
        }
        all_blocks.clear();
        total_allocated = 0;
        total_used = 0;
        current_block = nullptr;
        allocate_block(block_size);
    }
    void clear() {
        std::lock_guard<std::mutex> lock(pool_mutex);
        
        for(auto block : all_blocks) {
            delete block;
        }
        all_blocks.clear();
        current_block = nullptr;
        total_allocated = 0;
        total_used = 0;
    }
    size_t get_total_allocated() const { return total_allocated; }
    size_t get_total_used() const { return total_used; }
    size_t get_utilization() const {
        if(total_allocated == 0) return 0;
        return (total_used * 100) / total_allocated;
    }
    size_t get_remaining() const {
        if(!current_block) return 0;
        return current_block->remaining();
    }
};
static MemoryPool g_memory_pool;
static std::mutex g_init_mutex;
static bool g_pool_initialized = false;
extern "C" __declspec(dllexport) int pool_init(size_t block_size) {
    std::lock_guard<std::mutex> lock(g_init_mutex);
    if(g_pool_initialized) return 1;  
    
    if(block_size == 0) block_size = MEMORY_POOL_BLOCK_SIZE;
    new (&g_memory_pool) MemoryPool(block_size);  
    g_pool_initialized = true;
    return 1;
}
extern "C" __declspec(dllexport) void* pool_alloc(size_t size) {
    if(!g_pool_initialized) {
        pool_init(0);
    }
    return g_memory_pool.alloc(size);
}
extern "C" __declspec(dllexport) void* pool_alloc_zero(size_t size) {
    void* ptr = pool_alloc(size);
    if(ptr) memset(ptr, 0, size);
    return ptr;
}
extern "C" __declspec(dllexport) void* pool_alloc_array(size_t count, size_t elem_size) {
    return pool_alloc(count * elem_size);
}
extern "C" __declspec(dllexport) char* pool_alloc_string(const char* str) {
    if(!str) return nullptr;
    size_t len = strlen(str) + 1;
    char* ptr = (char*)pool_alloc(len);
    if(ptr) memcpy(ptr, str, len);
    return ptr;
}
extern "C" __declspec(dllexport) char* pool_dup_string(const char* str) {
    return pool_alloc_string(str);
}
extern "C" __declspec(dllexport) void pool_reset() {
    if(g_pool_initialized) {
        g_memory_pool.reset();
    }
}
extern "C" __declspec(dllexport) void pool_clear() {
    if(g_pool_initialized) {
        g_memory_pool.clear();
        g_pool_initialized = false;
    }
}
extern "C" __declspec(dllexport) size_t pool_get_total_allocated() {
    return g_pool_initialized ? g_memory_pool.get_total_allocated() : 0;
}
extern "C" __declspec(dllexport) size_t pool_get_total_used() {
    return g_pool_initialized ? g_memory_pool.get_total_used() : 0;
}
extern "C" __declspec(dllexport) int pool_get_utilization() {
    return g_pool_initialized ? (int)g_memory_pool.get_utilization() : 0;
}
extern "C" __declspec(dllexport) size_t pool_get_remaining() {
    return g_pool_initialized ? g_memory_pool.get_remaining() : 0;
}
extern "C" __declspec(dllexport) char* pool_concat_strings(const char** strings, int count) {
    if(!strings || count <= 0) return nullptr;
    size_t total_len = 1;  
    for(int i = 0; i < count; i++) {
        if(strings[i]) total_len += strlen(strings[i]);
    }
    
    char* result = (char*)pool_alloc(total_len);
    if(!result) return nullptr;
    
    result[0] = '\0';
    for(int i = 0; i < count; i++) {
        if(strings[i]) strcat(result, strings[i]);
    }
    
    return result;
}
extern "C" __declspec(dllexport) char* pool_format_string(const char* format, ...) {
    if(!format) return nullptr;
    va_list args;
    va_start(args, format);
    int len = _vscprintf(format, args);
    if(len < 0) {
        va_end(args);
        return nullptr;
    }
    
    char* result = (char*)pool_alloc(len + 1);
    if(result) {
        vsnprintf(result, len + 1, format, args);
    }
    
    va_end(args);
    return result;
}
BOOL WINAPI DllMain(HINSTANCE hinstDLL, DWORD fdwReason, LPVOID lpvReserved) {
    if(fdwReason == DLL_PROCESS_DETACH) {
        if(g_pool_initialized) {
            g_memory_pool.clear();
            g_pool_initialized = false;
        }
    }
    return TRUE;
}