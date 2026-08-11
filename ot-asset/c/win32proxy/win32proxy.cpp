/***
*    ____                _           _   ____           ___   ___ _____      ____          ___        __       __  ___   _ 
*   / ___|_ __ ___  __ _| |_ ___  __| | | __ ) _   _   / _ \ / _ |_   _|    / ___|___   __| \ \      / ___     \ \/ | | | |
*  | |   | '__/ _ \/ _` | __/ _ \/ _` | |  _ \| | | | | | | | | | || |_____| |   / _ \ / _` |\ \ /\ / / _ \_____\  /| |_| |
*  | |___| | |  __| (_| | ||  __| (_| | | |_) | |_| | | |_| | |_| || |_____| |__| (_) | (_| | \ V  V |  __|_____/  \|  _  |
*   \____|_|  \___|\__,_|\__\___|\__,_| |____/ \__, |  \__\_\\___/ |_|      \____\___/ \__,_|  \_/\_/ \___|    /_/\_|_| |_|
*                                              |___/                                                                       
*/
#include <windows.h>
#include <map>
#include <string>
#include <vector>
#include <cstdarg>
#include <vector>
#include <algorithm>
#include "platform_export.h"
#ifdef _WIN32
#define DLL_EXPORT __declspec(dllexport)
#else
#define DLL_EXPORT __attribute__((visibility("default")))
#endif

static std::map<std::string, FARPROC> g_funcCache;
static std::map<std::string, DWORD> g_constCache;

static const char *g_dlls[] = {
    "user32", "kernel32", "gdi32", "shell32", "advapi32",
    "comctl32", "comdlg32", "winmm", "ws2_32", "ole32",
    "oleaut32", "shlwapi", "psapi", "iphlpapi", "version",
    "msvcrt", "ntdll", "crypt32", "secur32", "wtsapi32",
    nullptr};

static void InitConstants()
{
    // 窗口样式
    g_constCache["WS_OVERLAPPED"] = WS_OVERLAPPED;
    g_constCache["WS_POPUP"] = WS_POPUP;
    g_constCache["WS_CHILD"] = WS_CHILD;
    g_constCache["WS_MINIMIZE"] = WS_MINIMIZE;
    g_constCache["WS_VISIBLE"] = WS_VISIBLE;
    g_constCache["WS_DISABLED"] = WS_DISABLED;
    g_constCache["WS_CLIPSIBLINGS"] = WS_CLIPSIBLINGS;
    g_constCache["WS_CLIPCHILDREN"] = WS_CLIPCHILDREN;
    g_constCache["WS_MAXIMIZE"] = WS_MAXIMIZE;
    g_constCache["WS_CAPTION"] = WS_CAPTION;
    g_constCache["WS_BORDER"] = WS_BORDER;
    g_constCache["WS_DLGFRAME"] = WS_DLGFRAME;
    g_constCache["WS_VSCROLL"] = WS_VSCROLL;
    g_constCache["WS_HSCROLL"] = WS_HSCROLL;
    g_constCache["WS_SYSMENU"] = WS_SYSMENU;
    g_constCache["WS_THICKFRAME"] = WS_THICKFRAME;
    g_constCache["WS_GROUP"] = WS_GROUP;
    g_constCache["WS_TABSTOP"] = WS_TABSTOP;
    g_constCache["WS_MINIMIZEBOX"] = WS_MINIMIZEBOX;
    g_constCache["WS_MAXIMIZEBOX"] = WS_MAXIMIZEBOX;

    // 扩展窗口样式
    g_constCache["WS_EX_DLGMODALFRAME"] = WS_EX_DLGMODALFRAME;
    g_constCache["WS_EX_NOPARENTNOTIFY"] = WS_EX_NOPARENTNOTIFY;
    g_constCache["WS_EX_TOPMOST"] = WS_EX_TOPMOST;
    g_constCache["WS_EX_ACCEPTFILES"] = WS_EX_ACCEPTFILES;
    g_constCache["WS_EX_TRANSPARENT"] = WS_EX_TRANSPARENT;
    g_constCache["WS_EX_MDICHILD"] = WS_EX_MDICHILD;
    g_constCache["WS_EX_TOOLWINDOW"] = WS_EX_TOOLWINDOW;
    g_constCache["WS_EX_WINDOWEDGE"] = WS_EX_WINDOWEDGE;
    g_constCache["WS_EX_CLIENTEDGE"] = WS_EX_CLIENTEDGE;
    g_constCache["WS_EX_CONTEXTHELP"] = WS_EX_CONTEXTHELP;
    g_constCache["WS_EX_RIGHT"] = WS_EX_RIGHT;
    g_constCache["WS_EX_LEFT"] = WS_EX_LEFT;
    g_constCache["WS_EX_RTLREADING"] = WS_EX_RTLREADING;
    g_constCache["WS_EX_LEFTSCROLLBAR"] = WS_EX_LEFTSCROLLBAR;
    g_constCache["WS_EX_CONTROLPARENT"] = WS_EX_CONTROLPARENT;
    g_constCache["WS_EX_STATICEDGE"] = WS_EX_STATICEDGE;
    g_constCache["WS_EX_APPWINDOW"] = WS_EX_APPWINDOW;
    g_constCache["WS_EX_LAYERED"] = WS_EX_LAYERED;
    g_constCache["WS_EX_NOINHERITLAYOUT"] = WS_EX_NOINHERITLAYOUT;
    g_constCache["WS_EX_LAYOUTRTL"] = WS_EX_LAYOUTRTL;
    g_constCache["WS_EX_COMPOSITED"] = WS_EX_COMPOSITED;
    g_constCache["WS_EX_NOACTIVATE"] = WS_EX_NOACTIVATE;
    g_constCache["MB_OK"] = MB_OK;
    g_constCache["MB_OKCANCEL"] = MB_OKCANCEL;
    g_constCache["MB_ABORTRETRYIGNORE"] = MB_ABORTRETRYIGNORE;
    g_constCache["MB_YESNOCANCEL"] = MB_YESNOCANCEL;
    g_constCache["MB_YESNO"] = MB_YESNO;
    g_constCache["MB_RETRYCANCEL"] = MB_RETRYCANCEL;
    g_constCache["MB_CANCELTRYCONTINUE"] = MB_CANCELTRYCONTINUE;
    g_constCache["MB_ICONHAND"] = MB_ICONHAND;
    g_constCache["MB_ICONQUESTION"] = MB_ICONQUESTION;
    g_constCache["MB_ICONEXCLAMATION"] = MB_ICONEXCLAMATION;
    g_constCache["MB_ICONASTERISK"] = MB_ICONASTERISK;
    g_constCache["MB_ICONINFORMATION"] = MB_ICONINFORMATION;
    g_constCache["MB_ICONERROR"] = MB_ICONERROR;
    g_constCache["MB_ICONWARNING"] = MB_ICONWARNING;
    g_constCache["MB_DEFBUTTON1"] = MB_DEFBUTTON1;
    g_constCache["MB_DEFBUTTON2"] = MB_DEFBUTTON2;
    g_constCache["MB_DEFBUTTON3"] = MB_DEFBUTTON3;
    g_constCache["MB_DEFBUTTON4"] = MB_DEFBUTTON4;
    g_constCache["MB_TOPMOST"] = MB_TOPMOST;
    g_constCache["MB_RIGHT"] = MB_RIGHT;
    g_constCache["MB_RTLREADING"] = MB_RTLREADING;
    g_constCache["SW_HIDE"] = SW_HIDE;
    g_constCache["SW_SHOWNORMAL"] = SW_SHOWNORMAL;
    g_constCache["SW_NORMAL"] = SW_NORMAL;
    g_constCache["SW_SHOWMINIMIZED"] = SW_SHOWMINIMIZED;
    g_constCache["SW_SHOWMAXIMIZED"] = SW_SHOWMAXIMIZED;
    g_constCache["SW_MAXIMIZE"] = SW_MAXIMIZE;
    g_constCache["SW_SHOWNOACTIVATE"] = SW_SHOWNOACTIVATE;
    g_constCache["SW_SHOW"] = SW_SHOW;
    g_constCache["SW_MINIMIZE"] = SW_MINIMIZE;
    g_constCache["SW_SHOWMINNOACTIVE"] = SW_SHOWMINNOACTIVE;
    g_constCache["SW_SHOWNA"] = SW_SHOWNA;
    g_constCache["SW_RESTORE"] = SW_RESTORE;
    g_constCache["SW_SHOWDEFAULT"] = SW_SHOWDEFAULT;
    g_constCache["SW_FORCEMINIMIZE"] = SW_FORCEMINIMIZE;

#define VK_DEF(k) g_constCache[#k] = k
    VK_DEF(VK_LBUTTON);
    VK_DEF(VK_RBUTTON);
    VK_DEF(VK_CANCEL);
    VK_DEF(VK_MBUTTON);
    VK_DEF(VK_XBUTTON1);
    VK_DEF(VK_XBUTTON2);
    VK_DEF(VK_BACK);
    VK_DEF(VK_TAB);
    VK_DEF(VK_CLEAR);
    VK_DEF(VK_RETURN);
    VK_DEF(VK_SHIFT);
    VK_DEF(VK_CONTROL);
    VK_DEF(VK_MENU);
    VK_DEF(VK_PAUSE);
    VK_DEF(VK_CAPITAL);
    VK_DEF(VK_ESCAPE);
    VK_DEF(VK_SPACE);
    VK_DEF(VK_PRIOR);
    VK_DEF(VK_NEXT);
    VK_DEF(VK_END);
    VK_DEF(VK_HOME);
    VK_DEF(VK_LEFT);
    VK_DEF(VK_UP);
    VK_DEF(VK_RIGHT);
    VK_DEF(VK_DOWN);
    VK_DEF(VK_SELECT);
    VK_DEF(VK_PRINT);
    VK_DEF(VK_EXECUTE);
    VK_DEF(VK_SNAPSHOT);
    VK_DEF(VK_INSERT);
    VK_DEF(VK_DELETE);
    VK_DEF(VK_HELP);
#undef VK_DEF

    for (int i = 0; i < 26; i++)
    {
        std::string name = "VK_";
        name += ('A' + i);
        g_constCache[name] = 0x41 + i;
    }
    for (int i = 0; i < 10; i++)
    {
        std::string name = "VK_";
        name += ('0' + i);
        g_constCache[name] = 0x30 + i;
    }
    for (int i = 1; i <= 24; i++)
    {
        std::string name = "VK_F";
        name += std::to_string(i);
        g_constCache[name] = 0x70 + i - 1;
    }

    // 错误码
    g_constCache["ERROR_SUCCESS"] = ERROR_SUCCESS;
    g_constCache["ERROR_FILE_NOT_FOUND"] = ERROR_FILE_NOT_FOUND;
    g_constCache["ERROR_PATH_NOT_FOUND"] = ERROR_PATH_NOT_FOUND;
    g_constCache["ERROR_ACCESS_DENIED"] = ERROR_ACCESS_DENIED;
    g_constCache["ERROR_INVALID_HANDLE"] = ERROR_INVALID_HANDLE;
    g_constCache["ERROR_NOT_ENOUGH_MEMORY"] = ERROR_NOT_ENOUGH_MEMORY;
    g_constCache["ERROR_INVALID_ACCESS"] = ERROR_INVALID_ACCESS;
    g_constCache["ERROR_INVALID_DATA"] = ERROR_INVALID_DATA;
    g_constCache["ERROR_OUTOFMEMORY"] = ERROR_OUTOFMEMORY;
    g_constCache["ERROR_INVALID_PARAMETER"] = ERROR_INVALID_PARAMETER;

    // 文件属性
    g_constCache["FILE_ATTRIBUTE_READONLY"] = FILE_ATTRIBUTE_READONLY;
    g_constCache["FILE_ATTRIBUTE_HIDDEN"] = FILE_ATTRIBUTE_HIDDEN;
    g_constCache["FILE_ATTRIBUTE_SYSTEM"] = FILE_ATTRIBUTE_SYSTEM;
    g_constCache["FILE_ATTRIBUTE_DIRECTORY"] = FILE_ATTRIBUTE_DIRECTORY;
    g_constCache["FILE_ATTRIBUTE_ARCHIVE"] = FILE_ATTRIBUTE_ARCHIVE;
    g_constCache["FILE_ATTRIBUTE_NORMAL"] = FILE_ATTRIBUTE_NORMAL;
    g_constCache["FILE_ATTRIBUTE_TEMPORARY"] = FILE_ATTRIBUTE_TEMPORARY;
    g_constCache["FILE_ATTRIBUTE_COMPRESSED"] = FILE_ATTRIBUTE_COMPRESSED;

    // 进程/线程访问权限
    g_constCache["PROCESS_TERMINATE"] = PROCESS_TERMINATE;
    g_constCache["PROCESS_CREATE_THREAD"] = PROCESS_CREATE_THREAD;
    g_constCache["PROCESS_VM_OPERATION"] = PROCESS_VM_OPERATION;
    g_constCache["PROCESS_VM_READ"] = PROCESS_VM_READ;
    g_constCache["PROCESS_VM_WRITE"] = PROCESS_VM_WRITE;
    g_constCache["PROCESS_DUP_HANDLE"] = PROCESS_DUP_HANDLE;
    g_constCache["PROCESS_CREATE_PROCESS"] = PROCESS_CREATE_PROCESS;
    g_constCache["PROCESS_SET_QUOTA"] = PROCESS_SET_QUOTA;
    g_constCache["PROCESS_SET_INFORMATION"] = PROCESS_SET_INFORMATION;
    g_constCache["PROCESS_QUERY_INFORMATION"] = PROCESS_QUERY_INFORMATION;
    g_constCache["PROCESS_SUSPEND_RESUME"] = PROCESS_SUSPEND_RESUME;
    g_constCache["PROCESS_ALL_ACCESS"] = PROCESS_ALL_ACCESS;

    // 内存分配
    g_constCache["MEM_COMMIT"] = MEM_COMMIT;
    g_constCache["MEM_RESERVE"] = MEM_RESERVE;
    g_constCache["MEM_RELEASE"] = MEM_RELEASE;
    g_constCache["PAGE_EXECUTE_READWRITE"] = PAGE_EXECUTE_READWRITE;
    g_constCache["PAGE_READWRITE"] = PAGE_READWRITE;
    g_constCache["PAGE_READONLY"] = PAGE_READONLY;
    g_constCache["STD_INPUT_HANDLE"] = STD_INPUT_HANDLE;
    g_constCache["STD_OUTPUT_HANDLE"] = STD_OUTPUT_HANDLE;
    g_constCache["STD_ERROR_HANDLE"] = STD_ERROR_HANDLE;
    g_constCache["HKEY_CLASSES_ROOT"] = (DWORD)HKEY_CLASSES_ROOT;
    g_constCache["HKEY_CURRENT_USER"] = (DWORD)HKEY_CURRENT_USER;
    g_constCache["HKEY_LOCAL_MACHINE"] = (DWORD)HKEY_LOCAL_MACHINE;
    g_constCache["HKEY_USERS"] = (DWORD)HKEY_USERS;
    g_constCache["HKEY_CURRENT_CONFIG"] = (DWORD)HKEY_CURRENT_CONFIG;
}

extern "C" DLL_EXPORT void *get_address(const char *funcName)
{
    if (!funcName)
        return nullptr;

    std::string name(funcName);

    auto it = g_funcCache.find(name);
    if (it != g_funcCache.end())
    {
        return it->second;
    }

    for (int i = 0; g_dlls[i] != nullptr; i++)
    {
        std::string dllName = g_dlls[i];
        std::string dllPath = dllName + ".dll";

        HMODULE hModule = GetModuleHandleA(dllPath.c_str());
        if (!hModule)
        {
            hModule = LoadLibraryA(dllPath.c_str());
        }

        if (hModule)
        {
            FARPROC addr = GetProcAddress(hModule, name.c_str());
            if (!addr && (name.back() != 'A' && name.back() != 'W'))
            {
                std::string nameA = name + "A";
                std::string nameW = name + "W";
                addr = GetProcAddress(hModule, nameA.c_str());
                if (!addr)
                {
                    addr = GetProcAddress(hModule, nameW.c_str());
                }
            }

            if (addr)
            {
                g_funcCache[name] = addr;
                return addr;
            }
        }
    }

    return nullptr;
}

extern "C" DLL_EXPORT DWORD get_const(const char *constName)
{
    if (!constName)
        return 0;

    if (g_constCache.empty())
    {
        InitConstants();
    }

    auto it = g_constCache.find(constName);
    if (it != g_constCache.end())
    {
        return it->second;
    }

    return 0;
}
typedef DWORD(WINAPI *FUNC0)();
typedef DWORD(WINAPI *FUNC1)(uintptr_t);
typedef DWORD(WINAPI *FUNC2)(uintptr_t, uintptr_t);
typedef DWORD(WINAPI *FUNC3)(uintptr_t, uintptr_t, uintptr_t);
typedef DWORD(WINAPI *FUNC4)(uintptr_t, uintptr_t, uintptr_t, uintptr_t);
typedef DWORD(WINAPI *FUNC5)(uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t);
typedef DWORD(WINAPI *FUNC6)(uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t);
typedef DWORD(WINAPI *FUNC7)(uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t);
typedef DWORD(WINAPI *FUNC8)(uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t);
typedef DWORD(WINAPI *FUNC9)(uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t);
typedef DWORD(WINAPI *FUNC10)(uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t);
typedef DWORD(WINAPI *FUNC11)(uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t);
typedef DWORD(WINAPI *FUNC12)(uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t);
typedef DWORD(WINAPI *FUNC13)(uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t);
typedef DWORD(WINAPI *FUNC14)(uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t);
typedef DWORD(WINAPI *FUNC15)(uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t);
typedef DWORD(WINAPI *FUNC16)(uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t);
typedef DWORD(WINAPI *FUNC17)(uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t);
typedef DWORD(WINAPI *FUNC18)(uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t);
typedef DWORD(WINAPI *FUNC19)(uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t);
typedef DWORD(WINAPI *FUNC20)(uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t);
extern "C" DLL_EXPORT DWORD call_address(void *address, int argCount, ...)
{
    if (!address)
        return 0;
    FARPROC func = (FARPROC)address;
    va_list vl;
    va_start(vl, argCount);
    uintptr_t args[32] = {0};
    for (int i = 0; i < argCount && i < 32; i++)
    {
        args[i] = va_arg(vl, uintptr_t);
    }
    va_end(vl);

    switch (argCount)
    {
    case 0:
        return ((FUNC0)func)();
    case 1:
        return ((FUNC1)func)(args[0]);
    case 2:
        return ((FUNC2)func)(args[0], args[1]);
    case 3:
        return ((FUNC3)func)(args[0], args[1], args[2]);
    case 4:
        return ((FUNC4)func)(args[0], args[1], args[2], args[3]);
    case 5:
        return ((FUNC5)func)(args[0], args[1], args[2], args[3], args[4]);
    case 6:
        return ((FUNC6)func)(args[0], args[1], args[2], args[3], args[4], args[5]);
    case 7:
        return ((FUNC7)func)(args[0], args[1], args[2], args[3], args[4], args[5], args[6]);
    case 8:
        return ((FUNC8)func)(args[0], args[1], args[2], args[3], args[4], args[5], args[6], args[7]);
    case 9:
        return ((FUNC9)func)(args[0], args[1], args[2], args[3], args[4], args[5], args[6], args[7], args[8]);
    case 10:
        return ((FUNC10)func)(args[0], args[1], args[2], args[3], args[4], args[5], args[6], args[7], args[8], args[9]);
    case 11:
        return ((FUNC11)func)(args[0], args[1], args[2], args[3], args[4], args[5], args[6], args[7], args[8], args[9], args[10]);
    case 12:
        return ((FUNC12)func)(args[0], args[1], args[2], args[3], args[4], args[5], args[6], args[7], args[8], args[9], args[10], args[11]);
    case 13:
        return ((FUNC13)func)(args[0], args[1], args[2], args[3], args[4], args[5], args[6], args[7], args[8], args[9], args[10], args[11], args[12]);
    case 14:
        return ((FUNC14)func)(args[0], args[1], args[2], args[3], args[4], args[5], args[6], args[7], args[8], args[9], args[10], args[11], args[12], args[13]);
    case 15:
        return ((FUNC15)func)(args[0], args[1], args[2], args[3], args[4], args[5], args[6], args[7], args[8], args[9], args[10], args[11], args[12], args[13], args[14]);
    case 16:
        return ((FUNC16)func)(args[0], args[1], args[2], args[3], args[4], args[5], args[6], args[7], args[8], args[9], args[10], args[11], args[12], args[13], args[14], args[15]);
    case 17:
        return ((FUNC17)func)(args[0], args[1], args[2], args[3], args[4], args[5], args[6], args[7], args[8], args[9], args[10], args[11], args[12], args[13], args[14], args[15], args[16]);
    case 18:
        return ((FUNC18)func)(args[0], args[1], args[2], args[3], args[4], args[5], args[6], args[7], args[8], args[9], args[10], args[11], args[12], args[13], args[14], args[15], args[16], args[17]);
    case 19:
        return ((FUNC19)func)(args[0], args[1], args[2], args[3], args[4], args[5], args[6], args[7], args[8], args[9], args[10], args[11], args[12], args[13], args[14], args[15], args[16], args[17], args[18]);
    case 20:
        return ((FUNC20)func)(args[0], args[1], args[2], args[3], args[4], args[5], args[6], args[7], args[8], args[9], args[10], args[11], args[12], args[13], args[14], args[15], args[16], args[17], args[18], args[19]);
    default:
        return 0;
    }
}

extern "C" DLL_EXPORT DWORD call_func(void *address, ...)
{
    if (!address)
        return 0;

    va_list vl;
    va_start(vl, address);

    uintptr_t args[32] = {0};
    int argCount = 0;
    uintptr_t arg;
    while ((arg = va_arg(vl, uintptr_t)) != 0xFFFFFFFFFFFFFFFFULL && argCount < 32)
    {
        args[argCount++] = arg;
    }
    va_end(vl);

    FARPROC func = (FARPROC)address;

    switch (argCount)
    {
    case 0:
        return ((FUNC0)func)();
    case 1:
        return ((FUNC1)func)(args[0]);
    case 2:
        return ((FUNC2)func)(args[0], args[1]);
    case 3:
        return ((FUNC3)func)(args[0], args[1], args[2]);
    case 4:
        return ((FUNC4)func)(args[0], args[1], args[2], args[3]);
    case 5:
        return ((FUNC5)func)(args[0], args[1], args[2], args[3], args[4]);
    case 6:
        return ((FUNC6)func)(args[0], args[1], args[2], args[3], args[4], args[5]);
    case 7:
        return ((FUNC7)func)(args[0], args[1], args[2], args[3], args[4], args[5], args[6]);
    case 8:
        return ((FUNC8)func)(args[0], args[1], args[2], args[3], args[4], args[5], args[6], args[7]);
    case 9:
        return ((FUNC9)func)(args[0], args[1], args[2], args[3], args[4], args[5], args[6], args[7], args[8]);
    case 10:
        return ((FUNC10)func)(args[0], args[1], args[2], args[3], args[4], args[5], args[6], args[7], args[8], args[9]);
    case 11:
        return ((FUNC11)func)(args[0], args[1], args[2], args[3], args[4], args[5], args[6], args[7], args[8], args[9], args[10]);
    case 12:
        return ((FUNC12)func)(args[0], args[1], args[2], args[3], args[4], args[5], args[6], args[7], args[8], args[9], args[10], args[11]);
    case 13:
        return ((FUNC13)func)(args[0], args[1], args[2], args[3], args[4], args[5], args[6], args[7], args[8], args[9], args[10], args[11], args[12]);
    case 14:
        return ((FUNC14)func)(args[0], args[1], args[2], args[3], args[4], args[5], args[6], args[7], args[8], args[9], args[10], args[11], args[12], args[13]);
    case 15:
        return ((FUNC15)func)(args[0], args[1], args[2], args[3], args[4], args[5], args[6], args[7], args[8], args[9], args[10], args[11], args[12], args[13], args[14]);
    case 16:
        return ((FUNC16)func)(args[0], args[1], args[2], args[3], args[4], args[5], args[6], args[7], args[8], args[9], args[10], args[11], args[12], args[13], args[14], args[15]);
    case 17:
        return ((FUNC17)func)(args[0], args[1], args[2], args[3], args[4], args[5], args[6], args[7], args[8], args[9], args[10], args[11], args[12], args[13], args[14], args[15], args[16]);
    case 18:
        return ((FUNC18)func)(args[0], args[1], args[2], args[3], args[4], args[5], args[6], args[7], args[8], args[9], args[10], args[11], args[12], args[13], args[14], args[15], args[16], args[17]);
    case 19:
        return ((FUNC19)func)(args[0], args[1], args[2], args[3], args[4], args[5], args[6], args[7], args[8], args[9], args[10], args[11], args[12], args[13], args[14], args[15], args[16], args[17], args[18]);
    case 20:
        return ((FUNC20)func)(args[0], args[1], args[2], args[3], args[4], args[5], args[6], args[7], args[8], args[9], args[10], args[11], args[12], args[13], args[14], args[15], args[16], args[17], args[18], args[19]);
    default:
        return 0;
    }
}
extern "C" DLL_EXPORT const char *utf8_to_ansi(const char *utf8_str)
{
    if (!utf8_str)
        return "";

    static std::string result;

    int wide_len = MultiByteToWideChar(CP_UTF8, 0, utf8_str, -1, NULL, 0);
    if (wide_len <= 0)
        return utf8_str;

    std::wstring wstr(wide_len, L'\0');
    MultiByteToWideChar(CP_UTF8, 0, utf8_str, -1, &wstr[0], wide_len);

    int ansi_len = WideCharToMultiByte(CP_ACP, 0, wstr.c_str(), -1, NULL, 0, NULL, NULL);
    if (ansi_len <= 0)
        return utf8_str;

    result.resize(ansi_len, '\0');
    WideCharToMultiByte(CP_ACP, 0, wstr.c_str(), -1, &result[0], ansi_len, NULL, NULL);

    if (!result.empty() && result.back() == '\0')
    {
        result.pop_back();
    }
    return result.c_str();
}
extern "C" DLL_EXPORT DWORD get_last_error()
{
    return GetLastError();
}

extern "C" DLL_EXPORT void set_last_error(DWORD error)
{
    SetLastError(error);
}

static PluginValue DWORDToPluginValue(DWORD result)
{
    return PluginValue((double)result);
}

static PluginValue plugin_get_address(const PluginValue *args, int arg_count)
{
    if (arg_count < 1 || args[0].type != PluginValue::TYPE_STRING)
    {
        return PluginValue(0.0);
    }
    void *addr = get_address(args[0].str_val);
    return PluginValue((double)(uintptr_t)addr);
}
static uintptr_t PluginValueToUintptr(const PluginValue &val)
{
    switch (val.type)
    {
    case PluginValue::TYPE_NUMBER:
        return (uintptr_t)val.num_val;
    case PluginValue::TYPE_BOOL:
        return val.bool_val ? 1 : 0;
    case PluginValue::TYPE_STRING:
        return (uintptr_t)val.str_val;
    case PluginValue::TYPE_POINTER:
        return (uintptr_t)val.ptr_val;
    default:
        return 0;
    }
}
static PluginValue UintptrToPluginValue(uintptr_t result)
{
    return PluginValue((void *)result);
}

static PluginValue plugin_get_const(const PluginValue *args, int arg_count)
{
    if (arg_count < 1 || args[0].type != PluginValue::TYPE_STRING)
    {
        return PluginValue(0.0);
    }
    DWORD val = get_const(args[0].str_val);
    return PluginValue((double)val);
}

static PluginValue plugin_call_address(const PluginValue *args, int arg_count)
{
    if (arg_count < 2)
    {
        return PluginValue(0.0);
    }

    uintptr_t addr = PluginValueToUintptr(args[0]);
    if (addr == 0)
    {
        return PluginValue(0.0);
    }

    int argCount = 0;
    if (args[1].type == PluginValue::TYPE_NUMBER)
    {
        argCount = (int)args[1].num_val;
    }
    static std::vector<std::vector<wchar_t>> utf16_strings;
    utf16_strings.clear();

    uintptr_t callArgs[32] = {0};
    int maxArgs = (arg_count - 2 < 32) ? (arg_count - 2) : 32;

    for (int i = 0; i < maxArgs; i++)
    {
        const PluginValue &arg = args[i + 2];

        if (arg.type == PluginValue::TYPE_STRING && arg.str_val)
        {
            int wide_len = MultiByteToWideChar(CP_UTF8, 0, arg.str_val, -1, NULL, 0);
            if (wide_len > 0)
            {
                std::vector<wchar_t> wstr(wide_len);
                MultiByteToWideChar(CP_UTF8, 0, arg.str_val, -1, wstr.data(), wide_len);
                utf16_strings.push_back(std::move(wstr));
                callArgs[i] = (uintptr_t)utf16_strings.back().data();
            }
            else
            {
                callArgs[i] = 0;
            }
        }
    }

    FARPROC func = (FARPROC)addr;

    switch (argCount)
    {
    case 0:
        return DWORDToPluginValue(((FUNC0)func)());
    case 1:
        return DWORDToPluginValue(((FUNC1)func)(callArgs[0]));
    case 2:
        return DWORDToPluginValue(((FUNC2)func)(callArgs[0], callArgs[1]));
    case 3:
        return DWORDToPluginValue(((FUNC3)func)(callArgs[0], callArgs[1], callArgs[2]));
    case 4:
        return DWORDToPluginValue(((FUNC4)func)(callArgs[0], callArgs[1], callArgs[2], callArgs[3]));
    case 5:
        return DWORDToPluginValue(((FUNC5)func)(callArgs[0], callArgs[1], callArgs[2], callArgs[3], callArgs[4]));
    case 6:
        return DWORDToPluginValue(((FUNC6)func)(callArgs[0], callArgs[1], callArgs[2], callArgs[3], callArgs[4], callArgs[5]));
    case 7:
        return DWORDToPluginValue(((FUNC7)func)(callArgs[0], callArgs[1], callArgs[2], callArgs[3], callArgs[4], callArgs[5], callArgs[6]));
    case 8:
        return DWORDToPluginValue(((FUNC8)func)(callArgs[0], callArgs[1], callArgs[2], callArgs[3], callArgs[4], callArgs[5], callArgs[6], callArgs[7]));
    case 9:
        return DWORDToPluginValue(((FUNC9)func)(callArgs[0], callArgs[1], callArgs[2], callArgs[3], callArgs[4], callArgs[5], callArgs[6], callArgs[7], callArgs[8]));
    case 10:
        return DWORDToPluginValue(((FUNC10)func)(callArgs[0], callArgs[1], callArgs[2], callArgs[3], callArgs[4], callArgs[5], callArgs[6], callArgs[7], callArgs[8], callArgs[9]));
    case 11:
        return DWORDToPluginValue(((FUNC11)func)(callArgs[0], callArgs[1], callArgs[2], callArgs[3], callArgs[4], callArgs[5], callArgs[6], callArgs[7], callArgs[8], callArgs[9], callArgs[10]));
    case 12:
        return DWORDToPluginValue(((FUNC12)func)(callArgs[0], callArgs[1], callArgs[2], callArgs[3], callArgs[4], callArgs[5], callArgs[6], callArgs[7], callArgs[8], callArgs[9], callArgs[10], callArgs[11]));
    case 13:
        return DWORDToPluginValue(((FUNC13)func)(callArgs[0], callArgs[1], callArgs[2], callArgs[3], callArgs[4], callArgs[5], callArgs[6], callArgs[7], callArgs[8], callArgs[9], callArgs[10], callArgs[11], callArgs[12]));
    case 14:
        return DWORDToPluginValue(((FUNC14)func)(callArgs[0], callArgs[1], callArgs[2], callArgs[3], callArgs[4], callArgs[5], callArgs[6], callArgs[7], callArgs[8], callArgs[9], callArgs[10], callArgs[11], callArgs[12], callArgs[13]));
    case 15:
        return DWORDToPluginValue(((FUNC15)func)(callArgs[0], callArgs[1], callArgs[2], callArgs[3], callArgs[4], callArgs[5], callArgs[6], callArgs[7], callArgs[8], callArgs[9], callArgs[10], callArgs[11], callArgs[12], callArgs[13], callArgs[14]));
    case 16:
        return DWORDToPluginValue(((FUNC16)func)(callArgs[0], callArgs[1], callArgs[2], callArgs[3], callArgs[4], callArgs[5], callArgs[6], callArgs[7], callArgs[8], callArgs[9], callArgs[10], callArgs[11], callArgs[12], callArgs[13], callArgs[14], callArgs[15]));
    case 17:
        return DWORDToPluginValue(((FUNC17)func)(callArgs[0], callArgs[1], callArgs[2], callArgs[3], callArgs[4], callArgs[5], callArgs[6], callArgs[7], callArgs[8], callArgs[9], callArgs[10], callArgs[11], callArgs[12], callArgs[13], callArgs[14], callArgs[15], callArgs[16]));
    case 18:
        return DWORDToPluginValue(((FUNC18)func)(callArgs[0], callArgs[1], callArgs[2], callArgs[3], callArgs[4], callArgs[5], callArgs[6], callArgs[7], callArgs[8], callArgs[9], callArgs[10], callArgs[11], callArgs[12], callArgs[13], callArgs[14], callArgs[15], callArgs[16], callArgs[17]));
    case 19:
        return DWORDToPluginValue(((FUNC19)func)(callArgs[0], callArgs[1], callArgs[2], callArgs[3], callArgs[4], callArgs[5], callArgs[6], callArgs[7], callArgs[8], callArgs[9], callArgs[10], callArgs[11], callArgs[12], callArgs[13], callArgs[14], callArgs[15], callArgs[16], callArgs[17], callArgs[18]));
    case 20:
        return DWORDToPluginValue(((FUNC20)func)(callArgs[0], callArgs[1], callArgs[2], callArgs[3], callArgs[4], callArgs[5], callArgs[6], callArgs[7], callArgs[8], callArgs[9], callArgs[10], callArgs[11], callArgs[12], callArgs[13], callArgs[14], callArgs[15], callArgs[16], callArgs[17], callArgs[18], callArgs[19]));
    default:
        return PluginValue(0.0);
    }
}

static PluginValue plugin_call_func(const PluginValue *args, int arg_count)
{
    if (arg_count < 1)
    {
        return PluginValue(0.0);
    }

    uintptr_t addr = PluginValueToUintptr(args[0]);
    if (addr == 0)
    {
        return PluginValue(0.0);
    }

    uintptr_t callArgs[32] = {0};
    int argCount = arg_count - 1;
    if (argCount > 32)
        argCount = 32;

    for (int i = 0; i < argCount; i++)
    {
        callArgs[i] = PluginValueToUintptr(args[i + 1]);
    }

    FARPROC func = (FARPROC)addr;

    switch (argCount)
    {
    case 0:
        return DWORDToPluginValue(((FUNC0)func)());
    case 1:
        return DWORDToPluginValue(((FUNC1)func)(callArgs[0]));
    case 2:
        return DWORDToPluginValue(((FUNC2)func)(callArgs[0], callArgs[1]));
    case 3:
        return DWORDToPluginValue(((FUNC3)func)(callArgs[0], callArgs[1], callArgs[2]));
    case 4:
        return DWORDToPluginValue(((FUNC4)func)(callArgs[0], callArgs[1], callArgs[2], callArgs[3]));
    case 5:
        return DWORDToPluginValue(((FUNC5)func)(callArgs[0], callArgs[1], callArgs[2], callArgs[3], callArgs[4]));
    case 6:
        return DWORDToPluginValue(((FUNC6)func)(callArgs[0], callArgs[1], callArgs[2], callArgs[3], callArgs[4], callArgs[5]));
    case 7:
        return DWORDToPluginValue(((FUNC7)func)(callArgs[0], callArgs[1], callArgs[2], callArgs[3], callArgs[4], callArgs[5], callArgs[6]));
    case 8:
        return DWORDToPluginValue(((FUNC8)func)(callArgs[0], callArgs[1], callArgs[2], callArgs[3], callArgs[4], callArgs[5], callArgs[6], callArgs[7]));
    case 9:
        return DWORDToPluginValue(((FUNC9)func)(callArgs[0], callArgs[1], callArgs[2], callArgs[3], callArgs[4], callArgs[5], callArgs[6], callArgs[7], callArgs[8]));
    case 10:
        return DWORDToPluginValue(((FUNC10)func)(callArgs[0], callArgs[1], callArgs[2], callArgs[3], callArgs[4], callArgs[5], callArgs[6], callArgs[7], callArgs[8], callArgs[9]));
    case 11:
        return DWORDToPluginValue(((FUNC11)func)(callArgs[0], callArgs[1], callArgs[2], callArgs[3], callArgs[4], callArgs[5], callArgs[6], callArgs[7], callArgs[8], callArgs[9], callArgs[10]));
    case 12:
        return DWORDToPluginValue(((FUNC12)func)(callArgs[0], callArgs[1], callArgs[2], callArgs[3], callArgs[4], callArgs[5], callArgs[6], callArgs[7], callArgs[8], callArgs[9], callArgs[10], callArgs[11]));
    case 13:
        return DWORDToPluginValue(((FUNC13)func)(callArgs[0], callArgs[1], callArgs[2], callArgs[3], callArgs[4], callArgs[5], callArgs[6], callArgs[7], callArgs[8], callArgs[9], callArgs[10], callArgs[11], callArgs[12]));
    case 14:
        return DWORDToPluginValue(((FUNC14)func)(callArgs[0], callArgs[1], callArgs[2], callArgs[3], callArgs[4], callArgs[5], callArgs[6], callArgs[7], callArgs[8], callArgs[9], callArgs[10], callArgs[11], callArgs[12], callArgs[13]));
    case 15:
        return DWORDToPluginValue(((FUNC15)func)(callArgs[0], callArgs[1], callArgs[2], callArgs[3], callArgs[4], callArgs[5], callArgs[6], callArgs[7], callArgs[8], callArgs[9], callArgs[10], callArgs[11], callArgs[12], callArgs[13], callArgs[14]));
    case 16:
        return DWORDToPluginValue(((FUNC16)func)(callArgs[0], callArgs[1], callArgs[2], callArgs[3], callArgs[4], callArgs[5], callArgs[6], callArgs[7], callArgs[8], callArgs[9], callArgs[10], callArgs[11], callArgs[12], callArgs[13], callArgs[14], callArgs[15]));
    case 17:
        return DWORDToPluginValue(((FUNC17)func)(callArgs[0], callArgs[1], callArgs[2], callArgs[3], callArgs[4], callArgs[5], callArgs[6], callArgs[7], callArgs[8], callArgs[9], callArgs[10], callArgs[11], callArgs[12], callArgs[13], callArgs[14], callArgs[15], callArgs[16]));
    case 18:
        return DWORDToPluginValue(((FUNC18)func)(callArgs[0], callArgs[1], callArgs[2], callArgs[3], callArgs[4], callArgs[5], callArgs[6], callArgs[7], callArgs[8], callArgs[9], callArgs[10], callArgs[11], callArgs[12], callArgs[13], callArgs[14], callArgs[15], callArgs[16], callArgs[17]));
    case 19:
        return DWORDToPluginValue(((FUNC19)func)(callArgs[0], callArgs[1], callArgs[2], callArgs[3], callArgs[4], callArgs[5], callArgs[6], callArgs[7], callArgs[8], callArgs[9], callArgs[10], callArgs[11], callArgs[12], callArgs[13], callArgs[14], callArgs[15], callArgs[16], callArgs[17], callArgs[18]));
    case 20:
        return DWORDToPluginValue(((FUNC20)func)(callArgs[0], callArgs[1], callArgs[2], callArgs[3], callArgs[4], callArgs[5], callArgs[6], callArgs[7], callArgs[8], callArgs[9], callArgs[10], callArgs[11], callArgs[12], callArgs[13], callArgs[14], callArgs[15], callArgs[16], callArgs[17], callArgs[18], callArgs[19]));
    default:
        return PluginValue(0.0);
    }
}

static PluginValue plugin_get_last_error(const PluginValue *args, int arg_count)
{
    return PluginValue((double)GetLastError());
}
static PluginValue plugin_utf8_to_ansi(const PluginValue *args, int arg_count)
{
    if (arg_count < 1 || args[0].type != PluginValue::TYPE_STRING)
    {
        return PluginValue("");
    }

    const char *utf8_str = args[0].str_val;
    if (!utf8_str)
    {
        return PluginValue("");
    }

    static std::string result;

    int wide_len = MultiByteToWideChar(CP_UTF8, 0, utf8_str, -1, NULL, 0);
    if (wide_len <= 0)
    {
        return PluginValue(utf8_str);
    }

    std::wstring wstr(wide_len, L'\0');
    MultiByteToWideChar(CP_UTF8, 0, utf8_str, -1, &wstr[0], wide_len);

    int ansi_len = WideCharToMultiByte(CP_ACP, 0, wstr.c_str(), -1, NULL, 0, NULL, NULL);
    if (ansi_len <= 0)
    {
        return PluginValue(utf8_str);
    }

    result.resize(ansi_len, '\0');
    WideCharToMultiByte(CP_ACP, 0, wstr.c_str(), -1, &result[0], ansi_len, NULL, NULL);

    if (!result.empty() && result.back() == '\0')
    {
        result.pop_back();
    }

    return PluginValue(result.c_str());
}
static PluginValue plugin_set_last_error(const PluginValue *args, int arg_count)
{
    if (arg_count >= 1 && args[0].type == PluginValue::TYPE_NUMBER)
    {
        SetLastError((DWORD)args[0].num_val);
    }
    return PluginValue(0.0);
}
static PluginValue plugin_utf8_to_utf16(const PluginValue *args, int arg_count)
{
    if (arg_count < 1 || args[0].type != PluginValue::TYPE_STRING)
    {
        return PluginValue(0.0);
    }

    const char *utf8_str = args[0].str_val;
    if (!utf8_str || !*utf8_str)
    {
        static wchar_t empty[] = L"";
        return PluginValue((double)(uintptr_t)empty);
    }

    int wide_len = MultiByteToWideChar(CP_UTF8, 0, utf8_str, -1, NULL, 0);
    if (wide_len <= 0)
    {
        static wchar_t empty[] = L"";
        return PluginValue((double)(uintptr_t)empty);
    }

    static std::vector<std::vector<wchar_t>> string_pool;

    std::vector<wchar_t> wstr(wide_len);
    MultiByteToWideChar(CP_UTF8, 0, utf8_str, -1, wstr.data(), wide_len);
    string_pool.push_back(std::move(wstr));
    return PluginValue((double)(uintptr_t)string_pool.back().data());
}
PLUGIN_BEGIN
g_exporter->add("get_address", plugin_get_address);
g_exporter->add("get_const", plugin_get_const);
g_exporter->add("call_address", plugin_call_address);
g_exporter->add("call_func", plugin_call_func);
g_exporter->add("get_last_error", plugin_get_last_error);
g_exporter->add("set_last_error", plugin_set_last_error);
PLUGIN_END

BOOL WINAPI DllMain(HINSTANCE hinstDLL, DWORD fdwReason, LPVOID lpvReserved)
{
    if (fdwReason == DLL_PROCESS_ATTACH)
    {
        InitConstants();
    }
    return TRUE;
}