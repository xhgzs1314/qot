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
#include <cstring>
#include <string>
#include <vector>
#include <algorithm>
#define THROW_ERROR(msg) do { \
    fprintf(stderr, "错误: %s\n", msg); \
    return false; \
} while(0)
void WriteU16(FILE* f, uint16_t val) {
    fputc(val & 0xFF, f);
    fputc((val >> 8) & 0xFF, f);
}
void WriteU32(FILE* f, uint32_t val) {
    fputc(val & 0xFF, f);
    fputc((val >> 8) & 0xFF, f);
    fputc((val >> 16) & 0xFF, f);
    fputc((val >> 24) & 0xFF, f);
}
DWORD GetFileSizeUtf8(const char* path) {
    wchar_t wpath[MAX_PATH];
    MultiByteToWideChar(CP_UTF8, 0, path, -1, wpath, MAX_PATH);
    HANDLE hFile = CreateFileW(wpath, GENERIC_READ, FILE_SHARE_READ,
                                NULL, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, NULL);
    if (hFile == INVALID_HANDLE_VALUE) return 0;
    DWORD size = GetFileSize(hFile, NULL);
    CloseHandle(hFile);
    return size;
}
bool AppendFileContent(FILE* out, const char* path) {
    wchar_t wpath[MAX_PATH];
    MultiByteToWideChar(CP_UTF8, 0, path, -1, wpath, MAX_PATH);
    HANDLE hFile = CreateFileW(wpath, GENERIC_READ, FILE_SHARE_READ,
                                NULL, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, NULL);
    if (hFile == INVALID_HANDLE_VALUE) {
        return false;
    }
    
    DWORD size = GetFileSize(hFile, NULL);
    const DWORD BUFFER_SIZE = 64 * 1024;
    char* buffer = (char*)malloc(BUFFER_SIZE);
    if (!buffer) {
        CloseHandle(hFile);
        return false;
    }
    
    DWORD bytesRead;
    DWORD remaining = size;
    bool success = true;
    
    while (remaining > 0 && success) {
        DWORD toRead = (remaining < BUFFER_SIZE) ? remaining : BUFFER_SIZE;
        if (!ReadFile(hFile, buffer, toRead, &bytesRead, NULL) || bytesRead != toRead) {
            success = false;
            break;
        }
        if (fwrite(buffer, 1, bytesRead, out) != bytesRead) {
            success = false;
            break;
        }
        remaining -= bytesRead;
    }
    
    free(buffer);
    CloseHandle(hFile);
    return success;
}

bool CreateExeFast(const char* templatePath, const char* outputPath,
                   const std::vector<std::pair<std::string, std::string>>& resources) {
    FILE* out = fopen(outputPath, "wb");
    if (!out) {
        fprintf(stderr, "无法创建输出文件: %s\n", outputPath);
        return false;
    }
    FILE* tmpl = fopen(templatePath, "rb");
    if (!tmpl) {
        fclose(out);
        fprintf(stderr, "无法打开模板: %s\n", templatePath);
        return false;
    }
    
    fseek(tmpl, 0, SEEK_END);
    long tmplSize = ftell(tmpl);
    fseek(tmpl, 0, SEEK_SET);
    
    char* tmplBuf = (char*)malloc(tmplSize);
    if (!tmplBuf) {
        fclose(tmpl);
        fclose(out);
        fprintf(stderr, "内存分配失败\n");
        return false;
    }
    
    if (fread(tmplBuf, 1, tmplSize, tmpl) != (size_t)tmplSize) {
        free(tmplBuf);
        fclose(tmpl);
        fclose(out);
        fprintf(stderr, "读取模板失败\n");
        return false;
    }
    
    if (fwrite(tmplBuf, 1, tmplSize, out) != (size_t)tmplSize) {
        free(tmplBuf);
        fclose(tmpl);
        fclose(out);
        fprintf(stderr, "写入模板失败\n");
        return false;
    }
    
    free(tmplBuf);
    fclose(tmpl);
    long dataStart = ftell(out);
    for (const auto& res : resources) {
        const std::string& filename = res.first;
        const std::string& filepath = res.second;
        
        uint16_t nameLen = filename.size();
        WriteU16(out, nameLen);
        fwrite(filename.c_str(), 1, nameLen, out);
        
        DWORD fileSize = GetFileSizeUtf8(filepath.c_str());
        WriteU32(out, fileSize);
        
        if (!AppendFileContent(out, filepath.c_str())) {
            fclose(out);
            fprintf(stderr, "写入文件失败: %s\n", filepath.c_str());
            return false;
        }
    }
    long dataEnd = ftell(out);
    DWORD dataOffset = dataStart;
    DWORD dataSize = dataEnd - dataStart;
    WriteU32(out, dataOffset);
    WriteU32(out, dataSize);
    fwrite("QTCPACK", 1, 8, out);
    
    fclose(out);
    return true;
}

int main(int argc, char* argv[]) {
    if (argc < 3) {
        printf("用法: qotbuild <template.exe> <output.exe> [file:name] ...\n");
        printf("示例: qotbuild template.exe out.exe script.qtc:script.qtc qotrun.dll:qotrun.dll\n");
        return 1;
    }
    
    const char* templatePath = argv[1];
    const char* outputPath = argv[2];
    
    std::vector<std::pair<std::string, std::string>> resources;
    
    for (int i = 3; i < argc; i++) {
        std::string arg = argv[i];
        size_t colon = arg.rfind(':');
        if (colon != std::string::npos && colon > 0) {
            std::string filepath = arg.substr(0, colon);
            std::string name = arg.substr(colon + 1);
            if (GetFileSizeUtf8(filepath.c_str()) == 0) {
                fprintf(stderr, "错误: 文件不存在或为空: %s\n", filepath.c_str());
                return 1;
            }
            
            resources.push_back({name, filepath});
        } else {
            size_t slash = arg.find_last_of("/\\");
            std::string name = (slash != std::string::npos) ? arg.substr(slash + 1) : arg;
            resources.push_back({name, arg});
        }
    }
    
    if (CreateExeFast(templatePath, outputPath, resources)) {
        printf("打包成功: %s\n", outputPath);
        return 0;
    }
    
    return 1;
}