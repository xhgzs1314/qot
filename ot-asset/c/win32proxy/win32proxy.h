/***
*    ____                _           _   ____           ___   ___ _____      ____          ___        __       __  ___   _ 
*   / ___|_ __ ___  __ _| |_ ___  __| | | __ ) _   _   / _ \ / _ |_   _|    / ___|___   __| \ \      / ___     \ \/ | | | |
*  | |   | '__/ _ \/ _` | __/ _ \/ _` | |  _ \| | | | | | | | | | || |_____| |   / _ \ / _` |\ \ /\ / / _ \_____\  /| |_| |
*  | |___| | |  __| (_| | ||  __| (_| | | |_) | |_| | | |_| | |_| || |_____| |__| (_) | (_| | \ V  V |  __|_____/  \|  _  |
*   \____|_|  \___|\__,_|\__\___|\__,_| |____/ \__, |  \__\_\\___/ |_|      \____\___/ \__,_|  \_/\_/ \___|    /_/\_|_| |_|
*                                              |___/                                                                       
*/
#pragma once
#include <windows.h>
#include <string>
#include <unordered_map>
#include <vector>
#include <variant>

// 参数类型
enum class ArgType {
    INT,
    UINT,
    LONG,
    ULONG,
    DWORD,
    HANDLE,
    HWND,
    HDC,
    HINSTANCE,
    HMODULE,
    HKEY,
    LPCSTR,
    LPCWSTR,
    LPSTR,
    LPWSTR,
    LPVOID,
    BOOL,
    BYTE,
    WORD,
    FLOAT,
    DOUBLE,
    RECT_PTR,
    POINT_PTR,
    SIZE_PTR,
    GUID_PTR,
    SECURITY_ATTRIBUTES_PTR,
    OVERLAPPED_PTR,
    LUID_PTR,
    FILETIME_PTR,
    SYSTEMTIME_PTR,
    MEMORYSTATUSEX_PTR
};

// 值类型
struct Value {
    enum Type {
        TYPE_NULL = 0,
        TYPE_INT = 1,
        TYPE_UINT = 2,
        TYPE_LONG = 3,
        TYPE_ULONG = 4,
        TYPE_DWORD = 5,
        TYPE_HANDLE = 6,
        TYPE_STRING_A = 7,
        TYPE_STRING_W = 8,
        TYPE_BOOL = 9,
        TYPE_DOUBLE = 10,
        TYPE_BYTES = 11,
        TYPE_POINTER = 12
    };
    
    Type type = TYPE_NULL;
    union {
        int int_val;
        unsigned int uint_val;
        long long_val;
        unsigned long ulong_val;
        DWORD dword_val;
        HANDLE handle_val;
        bool bool_val;
        double double_val;
        void* ptr_val;
    };
    std::string str_a;
    std::wstring str_w;
    std::vector<BYTE> bytes;
    
    Value() : type(TYPE_NULL), ptr_val(nullptr) {}
    Value(int v) : type(TYPE_INT), int_val(v) {}
    Value(unsigned int v) : type(TYPE_UINT), uint_val(v) {}
    Value(long v) : type(TYPE_LONG), long_val(v) {}
    Value(unsigned long v) : type(TYPE_ULONG), ulong_val(v) {}
    Value(DWORD v) : type(TYPE_DWORD), dword_val(v) {}
    Value(HANDLE v) : type(TYPE_HANDLE), handle_val(v) {}
    Value(HWND v) : type(TYPE_HANDLE), handle_val((HANDLE)v) {}
    Value(bool v) : type(TYPE_BOOL), bool_val(v) {}
    Value(double v) : type(TYPE_DOUBLE), double_val(v) {}
    Value(const char* v) : type(TYPE_STRING_A), str_a(v ? v : "") {}
    Value(const wchar_t* v) : type(TYPE_STRING_W), str_w(v ? v : L"") {}
    Value(void* v) : type(TYPE_POINTER), ptr_val(v) {}
    
    DWORD asDWORD() const {
        switch(type) {
            case TYPE_INT: return (DWORD)int_val;
            case TYPE_UINT: return uint_val;
            case TYPE_LONG: return (DWORD)long_val;
            case TYPE_ULONG: return (DWORD)ulong_val;
            case TYPE_DWORD: return dword_val;
            case TYPE_HANDLE: return (DWORD)handle_val;
            case TYPE_POINTER: return (DWORD)ptr_val;
            default: return 0;
        }
    }
    
    HANDLE asHANDLE() const {
        if (type == TYPE_HANDLE) return handle_val;
        if (type == TYPE_INT) return (HANDLE)int_val;
        if (type == TYPE_POINTER) return (HANDLE)ptr_val;
        return nullptr;
    }
    
    HWND asHWND() const { return (HWND)asHANDLE(); }
    HMODULE asHMODULE() const { return (HMODULE)asHANDLE(); }
    
    LPCSTR asLPCSTR() const { return str_a.c_str(); }
    LPCWSTR asLPCWSTR() const { return str_w.c_str(); }
    LPSTR asLPSTR() const { return (LPSTR)str_a.c_str(); }
    LPWSTR asLPWSTR() const { return (LPWSTR)str_w.c_str(); }
    LPVOID asLPVOID() const { return ptr_val; }
    bool asBOOL() const { return bool_val; }
};