#pragma once
#include <cstring>
#include <string>
#include <vector>
#include <unordered_map>
#ifdef _WIN32
#define PLUGIN_EXPORT __declspec(dllexport)
#else
#define PLUGIN_EXPORT __attribute__((visibility("default")))
#endif

struct PluginValue
{
    enum Type : uint8_t
    {
        TYPE_NULL = 0,
        TYPE_BOOL = 1,
        TYPE_NUMBER = 2,
        TYPE_STRING = 3,
        TYPE_POINTER = 4
    };

    Type type = TYPE_NULL;

    union
    {
        bool bool_val;
        double num_val;
        const char *str_val;
        void* ptr_val;
    };

    PluginValue() : type(TYPE_NULL), num_val(0) {}
    PluginValue(std::nullptr_t) : type(TYPE_NULL), num_val(0) {}
    PluginValue(bool v) : type(TYPE_BOOL), bool_val(v) {}
    PluginValue(double v) : type(TYPE_NUMBER), num_val(v) {}
    PluginValue(int v) : type(TYPE_NUMBER), num_val((double)v) {}
    PluginValue(const char *v) : type(TYPE_STRING), str_val(v) {}
    PluginValue(const std::string &v) : type(TYPE_STRING)
    {
        static std::string storage;
        storage = v;
        str_val = storage.c_str();
    }
    PluginValue(void* v) : type(TYPE_POINTER), ptr_val(v) {} 
};

using PluginFunction = PluginValue (*)(const PluginValue *args, int arg_count);

class PluginExporter
{
public:
    PluginExporter &add(const char *name, PluginFunction func)
    {
        m_functions[name] = func;
        m_names.push_back(name);
        return *this;
    }

    PluginFunction get(const char *name) const
    {
        auto it = m_functions.find(name);
        return it != m_functions.end() ? it->second : nullptr;
    }

    int getCount() const
    {
        return (int)m_names.size();
    }

    const char *getName(int index) const
    {
        if (index >= 0 && index < (int)m_names.size())
        {
            return m_names[index].c_str();
        }
        return nullptr;
    }

private:
    std::unordered_map<std::string, PluginFunction> m_functions;
    std::vector<std::string> m_names;
};

#define PLUGIN_BEGIN                             \
    static PluginExporter *g_exporter = nullptr; \
    namespace                                    \
    {                                            \
        struct PluginRegistrar                   \
        {                                        \
            PluginRegistrar()                    \
            {                                    \
                static PluginExporter exporter;  \
                g_exporter = &exporter;

#define PLUGIN_END                                                       \
    }                                                                    \
    }                                                                    \
    ;                                                                    \
    static PluginRegistrar _plugin_registrar;                            \
    }                                                                    \
    extern "C"                                                           \
    {                                                                    \
        PLUGIN_EXPORT PluginFunction PluginGetFunction(const char *name) \
        {                                                                \
            return g_exporter ? g_exporter->get(name) : nullptr;         \
        }                                                                \
        PLUGIN_EXPORT int PluginGetFunctionCount()                       \
        {                                                                \
            return g_exporter ? g_exporter->getCount() : 0;              \
        }                                                                \
        PLUGIN_EXPORT const char *PluginGetFunctionName(int index)       \
        {                                                                \
            return g_exporter ? g_exporter->getName(index) : nullptr;    \
        }                                                                \
    }