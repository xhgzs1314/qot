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

#ifdef __cplusplus
}
#endif