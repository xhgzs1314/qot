#pragma once
#ifdef _WIN32
    #ifdef BUILDING_VM_DLL
        #define VM_API __declspec(dllexport)
    #else
        #define VM_API __declspec(dllimport)
    #endif
#else
    #define VM_API
#endif

#ifdef __cplusplus
extern "C" {
#endif

typedef void* VMHandle;
VM_API VMHandle vm_init(void);
VM_API int vm_execute_file(VMHandle handle, const char* filename);
VM_API char* vm_get_return_value(VMHandle handle);
VM_API const char* vm_get_last_error(VMHandle handle);
VM_API void vm_set_debug(VMHandle handle, int enable);
VM_API void vm_destroy(VMHandle handle);

/*  T-32: 剖析导出。剖析构建（/DQOT_VM_PROFILE）返回 JSON（用 vm_profile_free 释放，
   DLL 为 /MT 静态 CRT，跨模块 free 会破坏堆）；生产构建 reset 为空操作、dump 返回 NULL。 */
VM_API void vm_profile_reset(void);
VM_API char* vm_profile_dump(void);
VM_API void vm_profile_free(char* p);

#ifdef __cplusplus
}
#endif