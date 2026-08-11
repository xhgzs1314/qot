#include "shellcode_loader.h"
#include <fstream>
#include <iostream>
#include <cstring>
#include <algorithm>

#ifdef _WIN32
#include <windows.h>
#endif

namespace qot {

// 常量定义
static const uint32_t SC_MEM_COMMIT = 0x00001000;
static const uint32_t SC_MEM_RESERVE = 0x00002000;
static const uint32_t SC_MEM_RELEASE = 0x00008000;
static const uint32_t SC_PAGE_EXECUTE_READWRITE = 0x40;
static const uint32_t SC_INFINITE = 0xFFFFFFFF;
static const uint32_t SIGN_MAGIC_VAL = 0x4E474953; // "SIGN"
static const size_t SIGN_TAIL_SIZE_VAL = 512;
static std::string simpleHash(const uint8_t* data, size_t len) {
    uint32_t hash = 0x811C9DC5;
    for (size_t i = 0; i < len; i++) {
        hash ^= data[i];
        hash *= 0x01000193;
    }
    char buf[17];
    snprintf(buf, sizeof(buf), "%08x%08x", hash, static_cast<uint32_t>(len));
    return std::string(buf);
}

ShellcodeMetadata ShellcodeLoader::parseSignatureTail(const std::vector<uint8_t>& data) {
    ShellcodeMetadata meta;
    
    if (data.size() < SIGN_TAIL_SIZE_VAL) {
        return meta;
    }
    
    size_t offset = data.size() - SIGN_TAIL_SIZE_VAL;
    uint32_t magic = 0;
    std::memcpy(&magic, data.data() + offset, 4);
    if (magic != SIGN_MAGIC_VAL) {
        return meta;
    }
    offset += 4;
    meta.hasSignature = true;
    std::memcpy(&meta.version, data.data() + offset, 4);
    offset += 4;
    char fpId[33] = {0};
    std::memcpy(fpId, data.data() + offset, 32);
    meta.fingerprintId = std::string(fpId);
    offset += 32;
    std::memcpy(&meta.timestamp, data.data() + offset, 8);
    offset += 8;
    char hash[65] = {0};
    for (int i = 0; i < 32; i++) {
        snprintf(hash + i * 2, 3, "%02x", data[offset + i]);
    }
    meta.binHash = std::string(hash);
    offset += 32;
    char hw[65] = {0};
    for (int i = 0; i < 32; i++) {
        snprintf(hw + i * 2, 3, "%02x", data[offset + i]);
    }
    meta.hwFingerprint = std::string(hw);
    offset += 32;
    char sys[65] = {0};
    for (int i = 0; i < 32; i++) {
        snprintf(sys + i * 2, 3, "%02x", data[offset + i]);
    }
    meta.sysFingerprint = std::string(sys);
    offset += 32;
    return meta;
}

#ifdef _WIN32
void* ShellcodeLoader::virtualAlloc(void* addr, size_t size, uint32_t type, uint32_t protect) {
    return VirtualAlloc(addr, size, type, protect);
}

bool ShellcodeLoader::virtualFree(void* addr, size_t size, uint32_t type) {
    return VirtualFree(addr, size, type) != FALSE;
}

void* ShellcodeLoader::createThread(void* addr, size_t stackSize, void* startAddr, void* param, uint32_t flags) {
    return CreateThread(nullptr, stackSize, (LPTHREAD_START_ROUTINE)startAddr, param, flags, nullptr);
}

uint32_t ShellcodeLoader::waitForSingleObject(void* handle, uint32_t timeout) {
    return WaitForSingleObject(handle, timeout);
}

bool ShellcodeLoader::closeHandle(void* handle) {
    return CloseHandle(handle) != FALSE;
}
#endif

std::shared_ptr<ShellcodeInstance> ShellcodeLoader::loadShellcode(const std::string& path) {
    auto instance = std::make_shared<ShellcodeInstance>();
    instance->path = path;
    std::ifstream file(path, std::ios::binary);
    if (!file.is_open()) {
        std::cerr << "[Shellcode] 无法打开文件: " << path << std::endl;
        return nullptr;
    }
    
    file.seekg(0, std::ios::end);
    size_t fileSize = file.tellg();
    file.seekg(0, std::ios::beg);
    
    if (fileSize == 0) {
        std::cerr << "[Shellcode] 文件为空: " << path << std::endl;
        return nullptr;
    }
    
    std::vector<uint8_t> fullData(fileSize);
    file.read(reinterpret_cast<char*>(fullData.data()), fileSize);
    file.close();
    instance->metadata = parseSignatureTail(fullData);
    std::vector<uint8_t> codeData;
    if (instance->metadata.hasSignature && fileSize > SIGN_TAIL_SIZE_VAL) {
        codeData.assign(fullData.begin(), fullData.end() - SIGN_TAIL_SIZE_VAL);
    } else {
        codeData = std::move(fullData);
    }
    
    instance->size = codeData.size();
    
    
#ifdef _WIN32
    instance->address = virtualAlloc(nullptr, instance->size, 
                                      SC_MEM_COMMIT | SC_MEM_RESERVE, 
                                      SC_PAGE_EXECUTE_READWRITE);
#else
    std::cerr << "[Shellcode] 当前仅支持 Windows 平台" << std::endl;
    return nullptr;
#endif
    
    if (!instance->address) {
        std::cerr << "[Shellcode] 内存分配失败" << std::endl;
        return nullptr;
    }
    
    
    std::memcpy(instance->address, codeData.data(), instance->size);
    if (instance->metadata.hasSignature) {
        std::string calcHash = simpleHash(codeData.data(), codeData.size());
    }
    return instance;
}

bool ShellcodeLoader::executeShellcode(std::shared_ptr<ShellcodeInstance> instance,
                                        int timeoutMs, bool force, bool waitForever) {
    if (!instance || !instance->isValid()) {
        std::cerr << "[Shellcode] 无效的 shellcode 实例" << std::endl;
        return false;
    }
    bool hasSig = instance->metadata.hasSignature;
    if (!hasSig && !force) {
        std::cerr << "[Shellcode] 执行拒绝: 未签名的 shellcode - " << instance->path << std::endl;
        return false;
    }
    
#ifdef _WIN32
    void* hThread = createThread(nullptr, 0, instance->address, nullptr, 0);
    if (!hThread) {
        std::cerr << "[Shellcode] 线程创建失败" << std::endl;
        return false;
    }
    
    uint32_t result;
    if (waitForever) {
        result = waitForSingleObject(hThread, SC_INFINITE);
    } else {
        result = waitForSingleObject(hThread, static_cast<uint32_t>(timeoutMs));
        if (result != 0) {
            std::cout << "[Shellcode] 执行超时: " << instance->path << std::endl;
            closeHandle(hThread);
            return false;
        }
    }
    
    closeHandle(hThread);
    return result == 0;
#else
    std::cerr << "[Shellcode] 当前仅支持 Windows 平台" << std::endl;
    return false;
#endif
}

bool ShellcodeLoader::cleanupShellcode(std::shared_ptr<ShellcodeInstance> instance) {
    if (!instance || !instance->address) {
        std::cerr << "[Shellcode] 无效的 shellcode 实例，无法清理" << std::endl;
        return false;
    }
    
#ifdef _WIN32
    bool result = virtualFree(instance->address, 0, SC_MEM_RELEASE);
    if (result) {
        instance->address = nullptr;
        instance->size = 0;
    } else {
        std::cerr << "[Shellcode] 清理失败" << std::endl;
    }
    return result;
#else
    std::cerr << "[Shellcode] 当前仅支持 Windows 平台" << std::endl;
    return false;
#endif
}

} 