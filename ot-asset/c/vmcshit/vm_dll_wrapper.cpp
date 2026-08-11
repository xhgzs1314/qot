#ifndef BUILDING_VM_DLL
#define BUILDING_VM_DLL
#endif
#include "vm_dll_api.h"
#include "vm_executor.h"
#include <memory>
#include <string>
#include <cstring>

#ifdef _WIN32
#include <windows.h>
#endif

struct VMContext {
    std::unique_ptr<qot::VMExecutor> executor;
    std::string lastError;
    qot::Value returnValue;
    bool debug;
    
    VMContext() : executor(std::make_unique<qot::VMExecutor>()), debug(false) {}
};

#ifdef _WIN32
BOOL APIENTRY DllMain(HMODULE hModule, DWORD ul_reason_for_call, LPVOID lpReserved) {
    switch (ul_reason_for_call) {
    case DLL_PROCESS_ATTACH:
    case DLL_THREAD_ATTACH:
    case DLL_THREAD_DETACH:
    case DLL_PROCESS_DETACH:
        break;
    }
    return TRUE;
}
#endif

VM_API VMHandle vm_init(void) {
    try {
        return new VMContext();
    } catch (...) {
        return nullptr;
    }
}

VM_API int vm_execute_file(VMHandle handle, const char* filename) {
    if (!handle || !filename) return 0;
    
    VMContext* ctx = static_cast<VMContext*>(handle);
    ctx->executor->setDebug(ctx->debug);
    
    if (ctx->executor->executeFile(filename)) {
        ctx->returnValue = ctx->executor->getReturnValue();
        ctx->lastError.clear();
        return 1;
    }
    ctx->lastError = "Execution failed";
    return 0;
}

VM_API char* vm_get_return_value(VMHandle handle) {
    if (!handle) return nullptr;
    
    VMContext* ctx = static_cast<VMContext*>(handle);
    if (ctx->returnValue.isNull()) return nullptr;
    
    std::string str = ctx->returnValue.toString();
    char* result = (char*)malloc(str.size() + 1);
    if (result) {
        strcpy(result, str.c_str());
    }
    return result;
}

VM_API const char* vm_get_last_error(VMHandle handle) {
    if (!handle) return "Invalid handle";
    VMContext* ctx = static_cast<VMContext*>(handle);
    return ctx->lastError.c_str();
}

VM_API void vm_set_debug(VMHandle handle, int enable) {
    if (!handle) return;
    VMContext* ctx = static_cast<VMContext*>(handle);
    ctx->debug = (enable != 0);
    ctx->executor->setDebug(ctx->debug);
}

VM_API void vm_destroy(VMHandle handle) {
    if (handle) {
        delete static_cast<VMContext*>(handle);
    }
}