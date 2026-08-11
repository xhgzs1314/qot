#include "module_loader.h"
#include "platform_export.h"
#include <iostream>
#include <algorithm>
#include <cstring>
#include <vector>
#include <unordered_set>
#ifdef _WIN32
#include <windows.h>
#include <dbghelp.h>
#pragma comment(lib, "dbghelp.lib")
#endif

namespace qot
{
    std::atomic<bool> g_crashOccurred{false};
    std::string g_crashMessage;
    std::mutex g_crashMutex;
    static PluginValue valueToPluginValue(const Value &v)
    {
        if (v.isNull())
            return PluginValue(nullptr);
        if (v.isBool())
            return PluginValue(v.asBool());
        if (v.isNumber())
            return PluginValue(v.asNumber());
        if (v.isString())
            return PluginValue(v.asString());
        if (v.isArray())
            return PluginValue(nullptr);
        if (v.isDict())
            return PluginValue(nullptr);
        return PluginValue(nullptr);
    }

    static Value pluginValueToValue(const PluginValue &v)
    {
        switch (v.type)
        {
        case PluginValue::TYPE_NULL:
            return Value(nullptr);
        case PluginValue::TYPE_BOOL:
            return Value(v.bool_val);
        case PluginValue::TYPE_NUMBER:
            return Value(v.num_val);
        case PluginValue::TYPE_STRING:
            return Value(std::string(v.str_val ? v.str_val : ""));
        default:
            return Value(nullptr);
        }
    }

    std::string ModuleLoader::resolveLibPath(const std::string &libName, const std::string &sourcePath)
    {
        std::string baseDir;
        if (!sourcePath.empty())
        {
            baseDir = sourcePath;
            size_t lastSlash = baseDir.find_last_of("/\\");
            if (lastSlash != std::string::npos)
            {
                baseDir = baseDir.substr(0, lastSlash + 1);
            }
            else
            {
                baseDir = "";
            }
        }
        if (!libName.empty() && (libName[0] == '/' ||
                                 (libName.size() > 2 && libName[1] == ':')))
        {
            return libName;
        }
        if (!baseDir.empty())
        {
            std::string candidate = baseDir + libName;
            if (fileExists(candidate))
            {
                return candidate;
            }
#ifdef _WIN32
            std::string candidateDll = candidate + ".dll";
            if (fileExists(candidateDll))
            {
                return candidateDll;
            }
#else
            std::string candidateSo = candidate + ".so";
            if (fileExists(candidateSo))
            {
                return candidateSo;
            }
#endif
        }
        std::string exePath = getExecutablePath();
#ifdef _WIN32
        std::string packagePath = exePath + "\\package\\" + libName + "\\" + libName + ".dll";
#else
        std::string packagePath = exePath + "/package/" + libName + "/" + libName + ".so";
#endif
        if (fileExists(packagePath))
        {
            return packagePath;
        }
        return libName;
    }

    bool ModuleLoader::fileExists(const std::string &path)
    {
        std::ifstream f(path.c_str());
        return f.good();
    }

    std::string ModuleLoader::getExecutablePath()
    {
#ifdef _WIN32
        char buffer[MAX_PATH];
        GetModuleFileNameA(NULL, buffer, MAX_PATH);
        std::string path(buffer);
        size_t lastSlash = path.find_last_of("\\/");
        if (lastSlash != std::string::npos)
        {
            return path.substr(0, lastSlash);
        }
        return path;
#else
        char buffer[1024];
        ssize_t len = readlink("/proc/self/exe", buffer, sizeof(buffer) - 1);
        if (len != -1)
        {
            buffer[len] = '\0';
            std::string path(buffer);
            size_t lastSlash = path.find_last_of("/");
            if (lastSlash != std::string::npos)
            {
                return path.substr(0, lastSlash);
            }
        }
        return ".";
#endif
    }

    std::vector<FunctionSignature> ModuleLoader::parseExportFunc(const std::string &exportFuncPath)
    {
        std::vector<FunctionSignature> signatures;
#ifdef _WIN32
        int size_needed = MultiByteToWideChar(CP_UTF8, 0, exportFuncPath.c_str(), -1, NULL, 0);
        std::wstring wpath(size_needed, 0);
        MultiByteToWideChar(CP_UTF8, 0, exportFuncPath.c_str(), -1, &wpath[0], size_needed);
        std::ifstream file(wpath, std::ios::binary);
#else
        std::ifstream file(exportFuncPath, std::ios::binary);
#endif
        if (!file.is_open())
        {
            debugPrint("[Warning] 无法打开 exports.func: " + exportFuncPath);
            return signatures;
        }

        std::string content((std::istreambuf_iterator<char>(file)),
                            std::istreambuf_iterator<char>());
        file.close();

        if (content.size() >= 3 &&
            (unsigned char)content[0] == 0xEF &&
            (unsigned char)content[1] == 0xBB &&
            (unsigned char)content[2] == 0xBF)
        {
            content = content.substr(3);
        }

        std::istringstream iss(content);
        std::string line;
        int lineNum = 0;

        while (std::getline(iss, line))
        {
            lineNum++;
            if (!line.empty() && line.back() == '\r')
            {
                line.pop_back();
            }
            size_t start = line.find_first_not_of(" \t\r\n");
            if (start == std::string::npos)
                continue;
            size_t end = line.find_last_not_of(" \t\r\n");
            line = line.substr(start, end - start + 1);
            if (line.empty() || line[0] == '#')
                continue;

            std::vector<std::string> parts;
            size_t pos = 0;

            for (int i = 0; i < 3; i++)
            {
                size_t next = line.find('|', pos);
                if (next == std::string::npos)
                {
                    if (i == 2)
                    {
                        parts.push_back(line.substr(pos));
                    }
                    break;
                }
                parts.push_back(line.substr(pos, next - pos));
                pos = next + 1;
            }

            if (parts.size() >= 2)
            {
                FunctionSignature sig;
                sig.name = parts[0];
                if (parts.size() >= 2 && !parts[1].empty())
                {
                    std::string paramsStr = parts[1];
                    size_t p = 0;
                    while (p < paramsStr.size())
                    {
                        size_t comma = paramsStr.find(',', p);
                        std::string paramType;
                        if (comma == std::string::npos)
                        {
                            paramType = paramsStr.substr(p);
                            p = paramsStr.size();
                        }
                        else
                        {
                            paramType = paramsStr.substr(p, comma - p);
                            p = comma + 1;
                        }
                        paramType.erase(0, paramType.find_first_not_of(" \t"));
                        paramType.erase(paramType.find_last_not_of(" \t") + 1);
                        sig.paramTypes.push_back(paramType);
                    }
                }
                if (parts.size() >= 3)
                {
                    sig.returnType = parts[2];
                }
                else
                {
                    sig.returnType = "void";
                }
                signatures.push_back(sig);
            }
        }
        return signatures;
    }

    uint64_t directCall(void *funcPtr, uint64_t *args, size_t argCount)
    {
        switch (argCount)
        {
        case 0:
            return ((uint64_t (*)())funcPtr)();
        case 1:
            return ((uint64_t (*)(uint64_t))funcPtr)(args[0]);
        case 2:
            return ((uint64_t (*)(uint64_t, uint64_t))funcPtr)(args[0], args[1]);
        case 3:
            return ((uint64_t (*)(uint64_t, uint64_t, uint64_t))funcPtr)(args[0], args[1], args[2]);
        case 4:
            return ((uint64_t (*)(uint64_t, uint64_t, uint64_t, uint64_t))funcPtr)(args[0], args[1], args[2], args[3]);
        case 5:
            return ((uint64_t (*)(uint64_t, uint64_t, uint64_t, uint64_t, uint64_t))funcPtr)(args[0], args[1], args[2], args[3], args[4]);
        case 6:
            return ((uint64_t (*)(uint64_t, uint64_t, uint64_t, uint64_t, uint64_t, uint64_t))funcPtr)(args[0], args[1], args[2], args[3], args[4], args[5]);
        case 7:
            return ((uint64_t (*)(uint64_t, uint64_t, uint64_t, uint64_t, uint64_t, uint64_t, uint64_t))funcPtr)(args[0], args[1], args[2], args[3], args[4], args[5], args[6]);
        case 8:
            return ((uint64_t (*)(uint64_t, uint64_t, uint64_t, uint64_t, uint64_t, uint64_t, uint64_t, uint64_t))funcPtr)(args[0], args[1], args[2], args[3], args[4], args[5], args[6], args[7]);
        case 9:
            return ((uint64_t (*)(uint64_t, uint64_t, uint64_t, uint64_t, uint64_t, uint64_t, uint64_t, uint64_t, uint64_t))funcPtr)(args[0], args[1], args[2], args[3], args[4], args[5], args[6], args[7], args[8]);
        case 10:
            return ((uint64_t (*)(uint64_t, uint64_t, uint64_t, uint64_t, uint64_t, uint64_t, uint64_t, uint64_t, uint64_t, uint64_t))funcPtr)(args[0], args[1], args[2], args[3], args[4], args[5], args[6], args[7], args[8], args[9]);
        case 11:
            return ((uint64_t (*)(uint64_t, uint64_t, uint64_t, uint64_t, uint64_t, uint64_t, uint64_t, uint64_t, uint64_t, uint64_t, uint64_t))funcPtr)(args[0], args[1], args[2], args[3], args[4], args[5], args[6], args[7], args[8], args[9], args[10]);
        case 12:
            return ((uint64_t (*)(uint64_t, uint64_t, uint64_t, uint64_t, uint64_t, uint64_t, uint64_t, uint64_t, uint64_t, uint64_t, uint64_t, uint64_t))funcPtr)(args[0], args[1], args[2], args[3], args[4], args[5], args[6], args[7], args[8], args[9], args[10], args[11]);
        case 13:
            return ((uint64_t (*)(uint64_t, uint64_t, uint64_t, uint64_t, uint64_t, uint64_t, uint64_t, uint64_t, uint64_t, uint64_t, uint64_t, uint64_t, uint64_t))funcPtr)(args[0], args[1], args[2], args[3], args[4], args[5], args[6], args[7], args[8], args[9], args[10], args[11], args[12]);
        case 14:
            return ((uint64_t (*)(uint64_t, uint64_t, uint64_t, uint64_t, uint64_t, uint64_t, uint64_t, uint64_t, uint64_t, uint64_t, uint64_t, uint64_t, uint64_t, uint64_t))funcPtr)(args[0], args[1], args[2], args[3], args[4], args[5], args[6], args[7], args[8], args[9], args[10], args[11], args[12], args[13]);
        case 15:
            return ((uint64_t (*)(uint64_t, uint64_t, uint64_t, uint64_t, uint64_t, uint64_t, uint64_t, uint64_t, uint64_t, uint64_t, uint64_t, uint64_t, uint64_t, uint64_t, uint64_t))funcPtr)(args[0], args[1], args[2], args[3], args[4], args[5], args[6], args[7], args[8], args[9], args[10], args[11], args[12], args[13], args[14]);
        case 16:
            return ((uint64_t (*)(uint64_t, uint64_t, uint64_t, uint64_t, uint64_t, uint64_t, uint64_t, uint64_t, uint64_t, uint64_t, uint64_t, uint64_t, uint64_t, uint64_t, uint64_t, uint64_t))funcPtr)(args[0], args[1], args[2], args[3], args[4], args[5], args[6], args[7], args[8], args[9], args[10], args[11], args[12], args[13], args[14], args[15]);
        default:
            return 0;
        }
    }

    BuiltinFunction ModuleLoader::createDynamicWrapper(void *funcPtr, const FunctionSignature &sig)
    {
        return [funcPtr, sig](const std::vector<Value> &args) -> Value
        {
            std::vector<uint64_t> rawArgs;
            for (size_t i = 0; i < args.size(); i++)
            {
                const auto &arg = args[i];
                const std::string &expectedType = (i < sig.paramTypes.size()) ? sig.paramTypes[i] : "unknown";
                if (expectedType == "ptr")
                {
                    if (arg.isNumber())
                    {
                        rawArgs.push_back(static_cast<uint64_t>(static_cast<intptr_t>(arg.asNumber())));
                    }
                    else if (arg.isArray() || arg.isDict())
                    {
                        rawArgs.push_back(reinterpret_cast<uint64_t>(arg.getPtr()));
                    }
                    else if (arg.isNull())
                    {
                        rawArgs.push_back(0);
                    }
                    else
                    {
                        rawArgs.push_back(static_cast<uint64_t>(static_cast<intptr_t>(arg.asNumber())));
                    }
                }
                else if (expectedType == "int" || expectedType == "size_t" || expectedType == "long")
                {
                    if (arg.isNumber())
                    {
                        rawArgs.push_back(static_cast<uint64_t>(static_cast<int64_t>(arg.asNumber())));
                    }
                    else if (arg.isBool())
                    {
                        rawArgs.push_back(arg.asBool() ? 1 : 0);
                    }
                    else
                    {
                        rawArgs.push_back(0);
                    }
                }
                else if (expectedType == "str" || expectedType == "string")
                {
                    if (arg.isString())
                    {
                        rawArgs.push_back(reinterpret_cast<uint64_t>(arg.asString().c_str()));
                    }
                    else if (arg.isNull())
                    {
                        rawArgs.push_back(0);
                    }
                    else
                    {
                        std::string str = arg.toString();
                        rawArgs.push_back(reinterpret_cast<uint64_t>(str.c_str()));
                    }
                }
                else if (expectedType == "wca" || expectedType == "wstring")
                {
                    if (arg.isString())
                    {
                        std::string utf8Str = arg.asString();
                        int wideLen = MultiByteToWideChar(CP_UTF8, 0, utf8Str.c_str(), -1, NULL, 0);
                        if (wideLen > 0)
                        {
                            auto wstr = std::make_shared<std::wstring>(wideLen, 0);
                            MultiByteToWideChar(CP_UTF8, 0, utf8Str.c_str(), -1, &(*wstr)[0], wideLen);
                            static std::vector<std::shared_ptr<std::wstring>> tempStorage;
                            tempStorage.push_back(wstr);
                            rawArgs.push_back(reinterpret_cast<uint64_t>(wstr->c_str()));
                        }
                        else
                        {
                            rawArgs.push_back(0);
                        }
                    }
                    else if (arg.isNull())
                    {
                        rawArgs.push_back(0);
                    }
                    else
                    {
                        rawArgs.push_back(0);
                    }
                }
                else if (expectedType == "bool")
                {
                    rawArgs.push_back(arg.isTruthy() ? 1 : 0);
                }
                else if (expectedType == "double" || expectedType == "float")
                {
                    double d = arg.asNumber();
                    uint64_t bits;
                    memcpy(&bits, &d, 8);
                    rawArgs.push_back(bits);
                }
                else
                {
                    double d = arg.isNumber() ? arg.asNumber() : 0.0;
                    uint64_t bits;
                    memcpy(&bits, &d, 8);
                    rawArgs.push_back(bits);
                }
            }
            uint64_t result = directCall(funcPtr, rawArgs.data(), rawArgs.size());
            if (sig.returnType == "void")
            {
                return Value(nullptr);
            }
            else if (sig.returnType == "int" || sig.returnType == "size_t" || sig.returnType == "long")
            {
                int64_t val = static_cast<int64_t>(result);
                return Value(static_cast<double>(val));
            }
            else if (sig.returnType == "ptr")
            {
                return Value(static_cast<double>(static_cast<intptr_t>(result)));
            }
            else if (sig.returnType == "str" || sig.returnType == "string")
            {
                const char *str = reinterpret_cast<const char *>(result);
                return Value(str ? std::string(str) : "");
            }
            else if (sig.returnType == "wca" || sig.returnType == "wstring")
            {
                const wchar_t *wstr = reinterpret_cast<const wchar_t *>(result);
                if (wstr)
                {
                    int utf8Len = WideCharToMultiByte(CP_UTF8, 0, wstr, -1, NULL, 0, NULL, NULL);
                    if (utf8Len > 0)
                    {
                        std::string utf8Str(utf8Len, 0);
                        WideCharToMultiByte(CP_UTF8, 0, wstr, -1, &utf8Str[0], utf8Len, NULL, NULL);
                        return Value(utf8Str);
                    }
                }
                return Value("");
            }
            else if (sig.returnType == "bool")
            {
                return Value(result != 0);
            }
            else if (sig.returnType == "double" || sig.returnType == "float")
            {
                double d;
                memcpy(&d, &result, 8);
                return Value(d);
            }
            else
            {
                return Value(static_cast<double>(static_cast<intptr_t>(result)));
            }
        };
    }

    void ModuleLoader::registerFastPlugin(const std::string &libName, void *handle, const std::string &libPath)
    {
#ifdef _WIN32
        auto getFunc = (PluginFunction (*)(const char *))GetProcAddress((HMODULE)handle, "PluginGetFunction");

        if (!getFunc)
        {
            debugPrint("[Info] 未找到新接口 PluginGetFunction，回退到旧方案");
            registerDllFunctions(libName, handle, libPath);
            return;
        }

        auto getCount = (int (*)())GetProcAddress((HMODULE)handle, "PluginGetFunctionCount");
        auto getName = (const char *(*)(int))GetProcAddress((HMODULE)handle, "PluginGetFunctionName");

        if (getCount && getName)
        {
            debugPrint("[Info] 使用高性能插件接口: " + libName);
            int count = getCount();
            debugPrint("[Info] 发现 " + std::to_string(count) + " 个导出函数");

            for (int i = 0; i < count; i++)
            {
                const char *funcName = getName(i);
                if (funcName)
                {
                    PluginFunction func = getFunc(funcName);
                    if (func)
                    {
                        auto wrapper = [func](const std::vector<Value> &args) -> Value
                        {
                            std::vector<PluginValue> pluginArgs;
                            pluginArgs.reserve(args.size());
                            for (const auto &arg : args)
                            {
                                pluginArgs.push_back(valueToPluginValue(arg));
                            }
                            PluginValue result = func(pluginArgs.data(), (int)pluginArgs.size());
                            return pluginValueToValue(result);
                        };
                        BuiltinManager::instance().registerFunction(funcName, wrapper);
                        debugPrint("  注册函数: " + std::string(funcName));
                    }
                    else
                    {
                        debugPrint("[Warning] 函数 " + std::string(funcName) + " 获取失败");
                    }
                }
            }
        }
        else
        {
            debugPrint("[Info] 未找到枚举接口，回退到旧方案");
            registerDllFunctions(libName, handle, libPath);
        }
#else
        auto getFunc = (PluginFunction (*)(const char *))dlsym(handle, "PluginGetFunction");
        if (!getFunc)
        {
            registerDllFunctions(libName, handle, libPath);
            return;
        }

        auto getCount = (int (*)())dlsym(handle, "PluginGetFunctionCount");
        auto getName = (const char *(*)(int))dlsym(handle, "PluginGetFunctionName");

        if (getCount && getName)
        {
            int count = getCount();
            for (int i = 0; i < count; i++)
            {
                const char *funcName = getName(i);
                if (funcName)
                {
                    PluginFunction func = getFunc(funcName);
                    if (func)
                    {
                        auto wrapper = [func](const std::vector<Value> &args) -> Value
                        {
                            std::vector<PluginValue> pluginArgs;
                            pluginArgs.reserve(args.size());
                            for (const auto &arg : args)
                            {
                                pluginArgs.push_back(valueToPluginValue(arg));
                            }
                            PluginValue result = func(pluginArgs.data(), (int)pluginArgs.size());
                            return pluginValueToValue(result);
                        };
                        BuiltinManager::instance().registerFunction(funcName, wrapper);
                    }
                }
            }
        }
        else
        {
            registerDllFunctions(libName, handle, libPath);
        }
#endif
    }

    void ModuleLoader::registerDllFunctions(const std::string &libName, void *handle, const std::string &libPath)
    {
        std::string exportFuncPath = libPath;
        size_t lastSlash = exportFuncPath.find_last_of("/\\");
        if (lastSlash != std::string::npos)
        {
            exportFuncPath = exportFuncPath.substr(0, lastSlash + 1);
        }
        exportFuncPath += "exports.func";
        std::vector<FunctionSignature> signatures = parseExportFunc(exportFuncPath);

        if (signatures.empty())
        {
            debugPrint("[Error] 未找到 exports.func 或文件为空: " + exportFuncPath);
            return;
        }

        for (const auto &sig : signatures)
        {
#ifdef _WIN32
            FARPROC funcPtr = GetProcAddress((HMODULE)handle, sig.name.c_str());
            if (!funcPtr)
            {
                debugPrint("[Warning] 函数 " + sig.name + " 不存在于库 " + libName + " 中");
                continue;
            }

            auto wrapper = createDynamicWrapper(funcPtr, sig);
            BuiltinManager::instance().registerFunction(sig.name, wrapper);
            debugPrint("[旧方案] 注册函数: " + sig.name);
#else
            void *funcPtr = dlsym(handle, sig.name.c_str());
            if (!funcPtr)
            {
                debugPrint("[Warning] 函数 " + sig.name + " 不存在于库 " + libName + " 中");
                continue;
            }

            auto wrapper = createDynamicWrapper(funcPtr, sig);
            BuiltinManager::instance().registerFunction(sig.name, wrapper);
            debugPrint("[旧方案] 注册函数: " + sig.name);
#endif
        }
    }
    Value ModuleLoader::require(const std::string &libName, const std::string &sourcePath)
    {
        std::string trimmed = libName;
        trimmed.erase(0, trimmed.find_first_not_of(" \t\r\n"));
        trimmed.erase(trimmed.find_last_not_of(" \t\r\n") + 1);
        if (trimmed.empty())
        {
            std::cerr << "[Error] require() 库名不能为空" << std::endl;
            return Value(nullptr);
        }
        if (m_failedLoads.find(trimmed) != m_failedLoads.end())
        {
            std::cerr << "[Fatal] 模块 " << trimmed << " 加载失败，程序终止" << std::endl;
            std::exit(1);
        }
        auto it = m_loadedModules.find(trimmed);
        if (it != m_loadedModules.end())
        {
            return it->second;
        }
        std::string libPath = resolveLibPath(trimmed, sourcePath);

        debugPrint("原始库名: '" + libName + "'");
        debugPrint("处理后库名: '" + trimmed + "'");
        debugPrint("sourcePath: '" + sourcePath + "'");
        debugPrint("解析后的 DLL 路径: '" + libPath + "'");

#ifdef _WIN32
        std::string dllDir = libPath;
        size_t lastSlash = dllDir.find_last_of("/\\");
        if (lastSlash != std::string::npos)
        {
            dllDir = dllDir.substr(0, lastSlash);
            SetDllDirectoryA(dllDir.c_str());
        }

        int size_needed = MultiByteToWideChar(CP_UTF8, 0, libPath.c_str(), -1, NULL, 0);
        std::wstring wpath(size_needed, 0);
        MultiByteToWideChar(CP_UTF8, 0, libPath.c_str(), -1, &wpath[0], size_needed);

        HMODULE hModule = LoadLibraryExW(wpath.c_str(), NULL, LOAD_WITH_ALTERED_SEARCH_PATH);
        if (!hModule)
        {
            DWORD error = GetLastError();
            hModule = LoadLibraryW(wpath.c_str());
            if (!hModule)
            {
                error = GetLastError();
                std::cerr << "[Error] 无法加载库: " << libName << std::endl;
                std::cerr << "[Error] 路径: " << libPath << std::endl;
                std::cerr << "[Error] 错误码: " << error << std::endl;

                if (error == 126)
                {
                    std::cerr << "[Error] 错误 126: 找不到指定的模块" << std::endl;
                    std::cerr << "[Hint] 可能缺少依赖 DLL" << std::endl;
                }
                else if (error == 193)
                {
                    std::cerr << "[Error] 错误 193: 不是有效的 Win32 应用程序（32位/64位不匹配）" << std::endl;
                }

                m_failedLoads.insert(trimmed);
                std::exit(1);
            }
        }

        debugPrint("DLL 加载成功！句柄: " + std::to_string(reinterpret_cast<intptr_t>(hModule)));
        Value moduleObj = Value::createDict();
        auto &moduleDict = moduleObj.asDict();
        auto getFunc = (PluginFunction (*)(const char *))GetProcAddress(hModule, "PluginGetFunction");

        if (getFunc)
        {
            auto getCount = (int (*)())GetProcAddress(hModule, "PluginGetFunctionCount");
            auto getName = (const char *(*)(int))GetProcAddress(hModule, "PluginGetFunctionName");

            if (getCount && getName)
            {
                debugPrint("[Info] 使用高性能插件接口: " + libName);
                int count = getCount();
                debugPrint("[Info] 发现 " + std::to_string(count) + " 个导出函数");

                for (int i = 0; i < count; i++)
                {
                    const char *funcName = getName(i);
                    if (funcName)
                    {
                        PluginFunction func = getFunc(funcName);
                        if (func)
                        {
                            auto wrapper = [func](const std::vector<Value> &args) -> Value
                            {
                                std::vector<PluginValue> pluginArgs;
                                pluginArgs.reserve(args.size());
                                for (const auto &arg : args)
                                {
                                    pluginArgs.push_back(valueToPluginValue(arg));
                                }
                                PluginValue result = func(pluginArgs.data(), (int)pluginArgs.size());
                                return pluginValueToValue(result);
                            };

                            moduleDict[funcName] = Value(wrapper);
                            debugPrint("  添加模块函数: " + std::string(funcName));
                        }
                        else
                        {
                            debugPrint("[Warning] 函数 " + std::string(funcName) + " 获取失败");
                        }
                    }
                }
            }
            else
            {
                debugPrint("[Info] 未找到枚举接口，回退到旧方案");

                std::string exportFuncPath = libPath;
                size_t lastSlashPos = exportFuncPath.find_last_of("/\\");
                if (lastSlashPos != std::string::npos)
                {
                    exportFuncPath = exportFuncPath.substr(0, lastSlashPos + 1);
                }
                exportFuncPath += "exports.func";
                std::vector<FunctionSignature> signatures = parseExportFunc(exportFuncPath);

                for (const auto &sig : signatures)
                {
                    FARPROC funcPtr = GetProcAddress(hModule, sig.name.c_str());
                    if (funcPtr)
                    {
                        auto wrapper = createDynamicWrapper(funcPtr, sig);

                        moduleDict[sig.name] = Value(wrapper);
                        debugPrint("[旧方案] 添加模块函数: " + sig.name);
                    }
                }
            }
        }
        else
        {
            debugPrint("[Info] 未找到 PluginGetFunction 接口，尝试旧方案");
            std::string exportFuncPath = libPath;
            size_t lastSlashPos = exportFuncPath.find_last_of("/\\");
            if (lastSlashPos != std::string::npos)
            {
                exportFuncPath = exportFuncPath.substr(0, lastSlashPos + 1);
            }
            exportFuncPath += "exports.func";
            std::vector<FunctionSignature> signatures = parseExportFunc(exportFuncPath);

            for (const auto &sig : signatures)
            {
                FARPROC funcPtr = GetProcAddress(hModule, sig.name.c_str());
                if (funcPtr)
                {
                    auto wrapper = createDynamicWrapper(funcPtr, sig);

                    moduleDict[sig.name] = Value(wrapper);
                    debugPrint("[旧方案] 添加模块函数: " + sig.name);
                }
            }
        }

        m_loadedModules[trimmed] = moduleObj;
        return moduleObj;
#else

        void *handle = dlopen(libPath.c_str(), RTLD_LAZY);
        if (!handle)
        {
            std::cerr << "[Error] 无法加载库: " << trimmed
                      << " (" << dlerror() << ")" << std::endl;
            return Value(nullptr);
        }

        Value moduleObj = Value::createDict();
        auto &moduleDict = moduleObj.asDict();

        m_loadedModules[trimmed] = moduleObj;
        return moduleObj;
#endif
    }
}