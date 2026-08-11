#ifndef MODULE_LOADER_H
#define MODULE_LOADER_H

#include "vm_types.h"
#include "builtin.h"
#include <string>
#include <unordered_map>
#include <vector>
#include <fstream>
#include <sstream>
#include <functional>
#include <atomic>
#include <mutex>

#ifdef _WIN32
#include <windows.h>
#else
#include <dlfcn.h>
#endif
#include <unordered_set>

namespace qot
{

    extern std::atomic<bool> g_crashOccurred;
    extern std::string g_crashMessage;
    extern std::mutex g_crashMutex;

    struct FunctionSignature
    {
        std::string name;
        std::vector<std::string> paramTypes;
        std::string returnType;
    };

    class ModuleLoader
    {
    public:
        static ModuleLoader &instance()
        {
            static ModuleLoader loader;
            return loader;
        }

        Value require(const std::string &libName, const std::string &sourcePath = "");
        bool isLoaded(const std::string &libName) const;
        void setDebugCallback(std::function<void(const std::string &)> callback)
        {
            m_debugCallback = callback;
        }

        void setRunningFlag(bool *flag)
        {
            m_runningFlag = flag;
        }

    private:
        ModuleLoader() = default;
        void registerFastPlugin(const std::string &libName, void *handle, const std::string &libPath);
        std::unordered_set<std::string> m_failedLoads;
        std::unordered_map<std::string, Value> m_loadedModules;
        std::string m_basePath;
        std::function<void(const std::string &)> m_debugCallback;
        bool *m_runningFlag = nullptr;

        void debugPrint(const std::string &msg)
        {
            if (m_debugCallback)
            {
                m_debugCallback(msg);
            }
        }

        std::string resolveLibPath(const std::string &libName, const std::string &sourcePath);
        bool fileExists(const std::string &path);
        std::string getExecutablePath();
        std::vector<FunctionSignature> parseExportFunc(const std::string &exportFuncPath);
        void registerDllFunctions(const std::string &libName, void *handle, const std::string &libPath);
        BuiltinFunction createDynamicWrapper(void *funcPtr, const FunctionSignature &sig);
    };

}

#endif