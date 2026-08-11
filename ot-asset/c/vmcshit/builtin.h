#ifndef BUILTIN_H
#define BUILTIN_H
#include "vm_types.h"
#include <iostream>
#include <unordered_map>
#include <functional>
namespace qot
{
    class BuiltinManager
    {
    public:
        static BuiltinManager &instance()
        {
            static BuiltinManager manager;
            return manager;
        }
        void registerFunction(const std::string &name, BuiltinFunction func)
        {
            m_functions[name] = func;
        }
        bool hasFunction(const std::string &name) const
        {
            return m_functions.find(name) != m_functions.end();
        }
        const BuiltinFunction &getFunction(const std::string &name) const
        {
            static BuiltinFunction empty;
            auto it = m_functions.find(name);
            if (it != m_functions.end())
            {
                return it->second;
            }
            return empty;
        }
        Value call(const std::string &name, const std::vector<Value> &args)
        {
            auto it = m_functions.find(name);
            if (it != m_functions.end() && it->second)
            {
                return it->second(args);
            }
            std::cerr << "[Error] 函数未找到: " << name << std::endl;
            return Value(nullptr);
        }

    private:
        BuiltinManager()
        {
            registerBuiltins();
        }

        void registerBuiltins();

        std::unordered_map<std::string, BuiltinFunction> m_functions;
    };

}
#endif