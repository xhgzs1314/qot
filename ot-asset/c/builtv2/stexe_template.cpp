/***
*    ____                _           _   ____           ___   ___ _____      ____          ___        __       __  ___   _ 
*   / ___|_ __ ___  __ _| |_ ___  __| | | __ ) _   _   / _ \ / _ |_   _|    / ___|___   __| \ \      / ___     \ \/ | | | |
*  | |   | '__/ _ \/ _` | __/ _ \/ _` | |  _ \| | | | | | | | | | || |_____| |   / _ \ / _` |\ \ /\ / / _ \_____\  /| |_| |
*  | |___| | |  __| (_| | ||  __| (_| | | |_) | |_| | | |_| | |_| || |_____| |__| (_) | (_| | \ V  V |  __|_____/  \|  _  |
*   \____|_|  \___|\__,_|\__\___|\__,_| |____/ \__, |  \__\_\\___/ |_|      \____\___/ \__,_|  \_/\_/ \___|    /_/\_|_| |_|
*                                              |___/                                                                       
*/
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>


//#define DEBUG_CONSOLE

typedef HMODULE(WINAPI *pfnLoadLibraryExW)(LPCWSTR, HANDLE, DWORD);
typedef FARPROC(WINAPI *pfnGetProcAddress)(HMODULE, LPCSTR);
typedef BOOL(WINAPI *pfnFreeLibrary)(HMODULE);

HMODULE MemoryLoadLibrary(const void *dllData, size_t dllSize)
{
    wchar_t tempPath[MAX_PATH];
    wchar_t tempFile[MAX_PATH];
    GetTempPathW(MAX_PATH, tempPath);
    GetTempFileNameW(tempPath, L"QTD", 0, tempFile);

    HANDLE hFile = CreateFileW(tempFile, GENERIC_WRITE, 0, NULL,
                               CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    if (hFile == INVALID_HANDLE_VALUE)
        return NULL;

    DWORD written;
    WriteFile(hFile, dllData, (DWORD)dllSize, &written, NULL);
    CloseHandle(hFile);

    HMODULE hMod = LoadLibraryW(tempFile);
    DeleteFileW(tempFile);
    return hMod;
}

typedef struct
{
    const char *name;
    const void *data;
    DWORD size;
} FileEntry;

BOOL FindResourceMarker(HANDLE hFile, DWORD *dataOffset, DWORD *dataSize)
{
    const char marker[] = "QTCPACK";
    char buf[8];
    DWORD fileSize = GetFileSize(hFile, NULL);
    if (fileSize < 16)
        return FALSE;
    SetFilePointer(hFile, fileSize - 8, NULL, FILE_BEGIN);
    DWORD read;
    ReadFile(hFile, buf, 8, &read, NULL);

    if (memcmp(buf, marker, 8) == 0)
    {
        SetFilePointer(hFile, fileSize - 16, NULL, FILE_BEGIN);
        ReadFile(hFile, dataOffset, 4, &read, NULL);
        ReadFile(hFile, dataSize, 4, &read, NULL);
        return TRUE;
    }
    return FALSE;
}

int ParsePackage(const void *package, DWORD packageSize, FileEntry *entries, int maxEntries)
{
    const BYTE *p = (const BYTE *)package;
    const BYTE *end = p + packageSize;
    int count = 0;

    while (p + 2 <= end && count < maxEntries)
    {
        WORD nameLen = *(WORD *)p;
        p += 2;
        if (p + nameLen > end)
            break;
        char *nameBuf = (char *)malloc(nameLen + 1);
        if (nameBuf)
        {
            memcpy(nameBuf, p, nameLen);
            nameBuf[nameLen] = '\0';
            while (nameLen > 0 && (unsigned char)nameBuf[nameLen - 1] < 0x20)
            {
                nameBuf[nameLen - 1] = '\0';
                nameLen--;
            }

            entries[count].name = nameBuf;
        }
        else
        {
            entries[count].name = (const char *)p;
        }

        p += nameLen;

        if (p + 4 > end)
            break;
        DWORD fileSize = *(DWORD *)p;
        p += 4;
        if (p + fileSize > end)
            break;

        entries[count].data = p;
        entries[count].size = fileSize;
        p += fileSize;
        count++;
    }
    return count;
}

void EnsureDirectoryExists(const wchar_t *path)
{
    wchar_t tmp[MAX_PATH];
    wcscpy_s(tmp, path);
    for (wchar_t *p = tmp + 1; *p; p++)
    {
        if (*p == L'\\' || *p == L'/')
        {
            *p = 0;
            CreateDirectoryW(tmp, NULL);
            *p = L'\\';
        }
    }
    CreateDirectoryW(tmp, NULL);
}


bool WriteFileToDisk(const char *name, const void *data, DWORD size)
{
    wchar_t fullPath[MAX_PATH];
    MultiByteToWideChar(CP_UTF8, 0, name, -1, fullPath, MAX_PATH);
    wchar_t *lastSlash = wcsrchr(fullPath, L'\\');
    if (lastSlash)
    {
        *lastSlash = 0;
        EnsureDirectoryExists(fullPath);
        *lastSlash = L'\\';
    }

    HANDLE hOut = CreateFileW(fullPath, GENERIC_WRITE, 0,
                              NULL, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    if (hOut == INVALID_HANDLE_VALUE)
    {
        return false;
    }

    DWORD written;
    WriteFile(hOut, data, size, &written, NULL);
    CloseHandle(hOut);

    return (written == size);
}

int WINAPI WinMain(HINSTANCE hInst, HINSTANCE hPrev, LPSTR lpCmdLine, int nCmdShow)
{
#ifdef DEBUG_CONSOLE
    AllocConsole();
    FILE *f;
    freopen_s(&f, "CONOUT$", "w", stdout);
    freopen_s(&f, "CONOUT$", "w", stderr);
    printf("=== QTC Packed Executable ===\n");
#endif

    wchar_t exePath[MAX_PATH];
    GetModuleFileNameW(NULL, exePath, MAX_PATH);

#ifdef DEBUG_CONSOLE
    printf("Exe path: %ls\n", exePath);
#endif

    HANDLE hFile = CreateFileW(exePath, GENERIC_READ, FILE_SHARE_READ,
                               NULL, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, NULL);
    if (hFile == INVALID_HANDLE_VALUE)
    {
#ifdef DEBUG_CONSOLE
        printf("Failed to open exe file\n");
        system("pause");
#endif
        return 1;
    }

    DWORD dataOffset, dataSize;
    if (!FindResourceMarker(hFile, &dataOffset, &dataSize))
    {
#ifdef DEBUG_CONSOLE
        printf("Failed to find resource marker\n");
        system("pause");
#endif
        CloseHandle(hFile);
        return 1;
    }

#ifdef DEBUG_CONSOLE
    printf("Data offset: %u, size: %u\n", dataOffset, dataSize);
#endif

    HANDLE hMapping = CreateFileMappingW(hFile, NULL, PAGE_READONLY, 0, 0, NULL);
    if (!hMapping)
    {
#ifdef DEBUG_CONSOLE
        printf("CreateFileMapping failed, error: %d\n", GetLastError());
        system("pause");
#endif
        CloseHandle(hFile);
        return 1;
    }
    SYSTEM_INFO sysInfo;
    GetSystemInfo(&sysInfo);
    DWORD alignment = sysInfo.dwAllocationGranularity;
    DWORD alignedOffset = (dataOffset / alignment) * alignment;
    DWORD offsetDiff = dataOffset - alignedOffset;
    DWORD mapSize = dataSize + offsetDiff;

#ifdef DEBUG_CONSOLE
    printf("Alignment: %u, alignedOffset: %u, offsetDiff: %u, mapSize: %u\n",
           alignment, alignedOffset, offsetDiff, mapSize);
#endif

    const BYTE *pBase = (const BYTE *)MapViewOfFile(hMapping, FILE_MAP_READ,
                                                    0, alignedOffset, mapSize);
    if (!pBase)
    {
#ifdef DEBUG_CONSOLE
        printf("MapViewOfFile failed, error: %d\n", GetLastError());
        system("pause");
#endif
        CloseHandle(hMapping);
        CloseHandle(hFile);
        return 1;
    }

    
    const BYTE *pPackage = pBase + offsetDiff;

#ifdef DEBUG_CONSOLE
    printf("Package mapped successfully at %p (base %p, diff %u)\n", pPackage, pBase, offsetDiff);
    printf("First 32 bytes: ");
    for (int i = 0; i < 32 && i < (int)dataSize; i++)
    {
        printf("%02X ", pPackage[i]);
    }
    printf("\n");
#endif

    FileEntry entries[64];
    int fileCount = ParsePackage(pPackage, dataSize, entries, 64);

#ifdef DEBUG_CONSOLE
    printf("ParsePackage returned %d files\n", fileCount);
    for (int i = 0; i < fileCount; i++)
    {
        printf("  [%d] name='%s', size=%u\n", i, entries[i].name, entries[i].size);
    }
#endif

    if (fileCount == 0)
    {
#ifdef DEBUG_CONSOLE
        printf("No files found in package!\n");
        system("pause");
#endif
        UnmapViewOfFile(pBase);
        CloseHandle(hMapping);
        CloseHandle(hFile);
        return 1;
    }
    wchar_t tempDir[MAX_PATH];
    GetTempPathW(MAX_PATH, tempDir);
    wchar_t workDir[MAX_PATH];
    wsprintfW(workDir, L"%sQTC_%d", tempDir, GetCurrentProcessId());
    CreateDirectoryW(workDir, NULL);
    SetCurrentDirectoryW(workDir);
    SetDllDirectoryW(workDir);

#ifdef DEBUG_CONSOLE
    printf("Work directory: %ls\n", workDir);
#endif
    int extractedCount = 0;
    for (int i = 0; i < fileCount; i++)
    {
        if (WriteFileToDisk(entries[i].name, entries[i].data, entries[i].size))
        {
            extractedCount++;
#ifdef DEBUG_CONSOLE
            printf("Extracted: %s (%u bytes)\n", entries[i].name, entries[i].size);
#endif
        }
        else
        {
#ifdef DEBUG_CONSOLE
            printf("Failed to extract: %s\n", entries[i].name);
#endif
        }
    }

#ifdef DEBUG_CONSOLE
    printf("Extracted %d/%d files\n", extractedCount, fileCount);
#endif

    UnmapViewOfFile(pBase);
    CloseHandle(hMapping);
    CloseHandle(hFile);

    
#ifdef DEBUG_CONSOLE
    printf("\nFiles in work directory:\n");
    WIN32_FIND_DATAW findData;
    HANDLE hFind = FindFirstFileW(L"*", &findData);
    if (hFind != INVALID_HANDLE_VALUE)
    {
        do
        {
            printf("  %ls\n", findData.cFileName);
        } while (FindNextFileW(hFind, &findData));
        FindClose(hFind);
    }
#endif

    HMODULE hDll = NULL;
    for (int i = 0; i < fileCount; i++)
    {
        if (strcmp(entries[i].name, "qotrun.dll") == 0)
        {
#ifdef DEBUG_CONSOLE
            printf("Loading qotrun.dll from disk\n");
#endif
            hDll = LoadLibraryW(L"qotrun.dll");
            break;
        }
    }

    if (!hDll)
    {
#ifdef DEBUG_CONSOLE
        printf("Failed to load qotrun.dll, error: %d\n", GetLastError());
        system("pause");
#endif
        return 1;
    }

#ifdef DEBUG_CONSOLE
    printf("qotrun.dll loaded successfully, handle: %p\n", hDll);
#endif

    typedef void *(__cdecl * VM_INIT)();
    typedef int(__cdecl * VM_EXECUTE_FILE)(void *, const char *);
    typedef void(__cdecl * VM_DESTROY)(void *);
    typedef const char *(__cdecl * VM_GET_LAST_ERROR)(void *);

    VM_INIT vm_init = (VM_INIT)GetProcAddress(hDll, "vm_init");
    VM_EXECUTE_FILE vm_execute = (VM_EXECUTE_FILE)GetProcAddress(hDll, "vm_execute_file");
    VM_DESTROY vm_destroy = (VM_DESTROY)GetProcAddress(hDll, "vm_destroy");
    VM_GET_LAST_ERROR vm_get_last_error = (VM_GET_LAST_ERROR)GetProcAddress(hDll, "vm_get_last_error");

#ifdef DEBUG_CONSOLE
    printf("vm_init: %p, vm_execute: %p, vm_destroy: %p\n", vm_init, vm_execute, vm_destroy);
#endif

    if (!vm_init || !vm_execute || !vm_destroy)
    {
#ifdef DEBUG_CONSOLE
        printf("Failed to get VM functions\n");
        system("pause");
#endif
        FreeLibrary(hDll);
        return 1;
    }

    void *vm = vm_init();
    if (!vm)
    {
#ifdef DEBUG_CONSOLE
        printf("Failed to initialize VM\n");
        system("pause");
#endif
        FreeLibrary(hDll);
        return 1;
    }

#ifdef DEBUG_CONSOLE
    printf("VM initialized\n");
#endif

    
    char qtcPath[MAX_PATH] = "script.qtc";

#ifdef DEBUG_CONSOLE
    printf("Executing: %s\n", qtcPath);
#endif

    int result = vm_execute(vm, qtcPath);

    if (result == 0 && vm_get_last_error)
    {
        const char *err = vm_get_last_error(vm);
        if (err && err[0])
        {
#ifdef DEBUG_CONSOLE
            printf("VM Error: %s\n", err);
#endif
        }
    }

#ifdef DEBUG_CONSOLE
    printf("VM execution result: %d\n", result);
    printf("Press any key to exit...\n");
    system("pause");
#endif

    vm_destroy(vm);
    FreeLibrary(hDll);

    return result;
}