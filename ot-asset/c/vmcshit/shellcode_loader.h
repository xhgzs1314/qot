#ifndef SHELLCODE_LOADER_H
#define SHELLCODE_LOADER_H

#include <string>
#include <memory>
#include <vector>
#include <cstdint>

namespace qot {

struct ShellcodeMetadata {
    bool hasSignature = false;
    uint32_t version = 0;
    std::string fingerprintId;
    uint64_t timestamp = 0;
    std::string binHash;
    std::string hwFingerprint;
    std::string sysFingerprint;
    std::string userInfo;
};

struct ShellcodeInstance {
    std::string path;
    void* address = nullptr;
    size_t size = 0;
    size_t codeOffset = 0;
    ShellcodeMetadata metadata;
    
    ShellcodeInstance() = default;
    ~ShellcodeInstance() = default;
    
    bool isValid() const { return address != nullptr && size > 0; }
};

class ShellcodeLoader {
public:
    static ShellcodeLoader& instance() {
        static ShellcodeLoader loader;
        return loader;
    }
    std::shared_ptr<ShellcodeInstance> loadShellcode(const std::string& path);
    bool executeShellcode(std::shared_ptr<ShellcodeInstance> instance, 
                          int timeoutMs = 5000, 
                          bool force = false,
                          bool waitForever = false);
    bool cleanupShellcode(std::shared_ptr<ShellcodeInstance> instance);
    
private:
    ShellcodeLoader() = default;
    ~ShellcodeLoader() = default;
    ShellcodeLoader(const ShellcodeLoader&) = delete;
    ShellcodeLoader& operator=(const ShellcodeLoader&) = delete;
    ShellcodeMetadata parseSignatureTail(const std::vector<uint8_t>& data);
#ifdef _WIN32
    void* virtualAlloc(void* addr, size_t size, uint32_t type, uint32_t protect);
    bool virtualFree(void* addr, size_t size, uint32_t type);
    void* createThread(void* addr, size_t stackSize, void* startAddr, void* param, uint32_t flags);
    uint32_t waitForSingleObject(void* handle, uint32_t timeout);
    bool closeHandle(void* handle);
#endif
};

}

#endif 