#ifndef VM_EXECUTOR_H
#define VM_EXECUTOR_H

#define NULL_MAGIC 0x4E554C4C
#include "vm_types.h"
#include "vm_loader.h"
#include <iostream>
#include "builtin.h"
#include <cmath>
#include <vector>
#include <algorithm>
#include "module_loader.h"
#include <chrono>
#include <unordered_map>
#include "gui_module.h"

namespace qot
{
    struct OptimizedCallFrame
    {
        CodeBlock *codeBlock;
        StackFrame frame;
        size_t returnIp;

        OptimizedCallFrame() : codeBlock(nullptr), returnIp(0) {}

        OptimizedCallFrame(OptimizedCallFrame &&other) noexcept
            : codeBlock(other.codeBlock), frame(std::move(other.frame)), returnIp(other.returnIp) {}

        OptimizedCallFrame &operator=(OptimizedCallFrame &&other) noexcept
        {
            if (this != &other)
            {
                codeBlock = other.codeBlock;
                frame = std::move(other.frame);
                returnIp = other.returnIp;
            }
            return *this;
        }

        OptimizedCallFrame(const OptimizedCallFrame &) = delete;
        OptimizedCallFrame &operator=(const OptimizedCallFrame &) = delete;
    };

    class VMExecutor
    {
    public:
        VMExecutor()
        {
            m_running = true;
            m_currentFrame = nullptr;
            m_currentCodeBlock = nullptr;
            m_moduleLoader.setDebugCallback([this](const std::string &msg)
                                            { this->debugPrint(msg); });
            m_moduleLoader.setRunningFlag(&m_running);
            m_callStack.reserve(256);
        }

        bool executeFile(const std::string &filename)
        {
            if (!VMLoader::load(filename, m_code))
                return false;
            m_code.build_name_maps();
            GUIManager::instance().setCallbackExecutor([this](const std::string &funcName, const std::vector<Value> &args)
                                                       {
        if (args.empty()) return;
        CodeBlockPtr funcCode = nullptr;
        if (args[0].isCodeBlock()) {
            funcCode = args[0].asCodeBlock();
        }
        std::string callbackName;
        if (!funcCode) {
            callbackName = args[0].toString();
            auto it = m_functions.find(callbackName);
            if (it != m_functions.end()) {
                funcCode = it->second;
            }
        }
        if (!funcCode) {
            for (const auto& c : m_code.constants) {
                if (c.isCodeBlock()) {
                    auto cb = c.asCodeBlock();
                    if (!cb->funcNames.empty() && cb->funcNames[0] == callbackName) {
                        funcCode = cb;
                        break;
                    }
                }
            }
        }

        if (!funcCode) {
            debugPrint("[GUI] Callback function not found: " + callbackName);
            return;
        }
        std::vector<Value> callbackArgs;
        for (size_t i = 1; i < args.size(); i++) {
            callbackArgs.push_back(args[i]);
        }
        executeCodeBlock(funcCode, callbackArgs); });
            m_functions.clear();
            for (const auto &c : m_code.constants)
            {
                if (c.isCodeBlock())
                {
                    auto cb = c.asCodeBlock();
                    if (!cb->funcNames.empty() && !cb->funcNames[0].empty())
                    {
                        m_functions[cb->funcNames[0]] = cb;
                    }
                }
            }
            // T-34/T-35：加载期一次性解析全部调用点（主块 + 嵌套函数块）
            resolve_calls_recursive(m_code);
            return execute();
        }

        void setDebug(bool enable) { m_debug = enable; }

        void executeCodeBlock(CodeBlockPtr codeBlock, const std::vector<Value> &args)
        {
            if (!codeBlock)
                return;
            CodeBlock savedCode = std::move(m_code);
            std::vector<OptimizedCallFrame> savedCallStack = std::move(m_callStack);
            StackFrame *savedFrame = m_currentFrame;
            CodeBlock *savedCodeBlock = m_currentCodeBlock;
            bool savedRunning = m_running;
            m_code = *codeBlock;
            m_code.build_name_maps();
            m_currentCodeBlock = &m_code;
            std::vector<OptimizedCallFrame> tempCallStack;
            tempCallStack.reserve(1);

            OptimizedCallFrame newFrame;
            newFrame.codeBlock = &m_code;
            newFrame.returnIp = 0;
            newFrame.frame.resize(codeBlock->varNames.size(), codeBlock->funcNames.size());


            for (size_t i = 0; i < args.size() && i < codeBlock->varNames.size(); i++)
            {
                newFrame.frame.set_local(static_cast<uint32_t>(i), args[i]);
            }

            tempCallStack.push_back(std::move(newFrame));
            m_callStack = std::move(tempCallStack);
            m_currentFrame = &m_callStack.back().frame;
            m_currentCodeBlock = m_callStack.back().codeBlock;
            m_running = true;

            try
            {
                execute_fast();
            }
            catch (const std::exception &e)
            {
                debugPrint(std::string("[GUI] Callback error: ") + e.what());
            }
            m_running = savedRunning;
            m_code = std::move(savedCode);
            m_callStack = std::move(savedCallStack);
            m_currentFrame = savedFrame;
            m_currentCodeBlock = savedCodeBlock;
        }

        bool execute()
        {
            if (!m_code.isValid())
            {
                std::cerr << "[Error] 未解析的编码" << std::endl;
                return false;
            }

            if (m_debug)
            {
                debugPrint("========== 常量表详细信息 ==========");
                for (size_t i = 0; i < m_code.constants.size(); i++)
                {
                    debugPrint("  [" + std::to_string(i) + "] = " + m_code.constants[i].toString());
                    if (m_code.constants[i].isCodeBlock())
                    {
                        auto cb = m_code.constants[i].asCodeBlock();
                        debugPrint("    函数名数量: " + std::to_string(cb->funcNames.size()));
                        for (const auto &fn : cb->funcNames)
                        {
                            debugPrint("      函数名: " + fn);
                        }
                        debugPrint("    指令数量: " + std::to_string(cb->instructions.size()));
                    }
                }
                debugPrint("========== 变量名表 ==========");
                for (size_t i = 0; i < m_code.varNames.size(); i++)
                {
                    debugPrint("  [" + std::to_string(i) + "] = " + m_code.varNames[i]);
                }
                debugPrint("========== 函数名表 ==========");
                for (size_t i = 0; i < m_code.funcNames.size(); i++)
                {
                    debugPrint("  [" + std::to_string(i) + "] = " + m_code.funcNames[i]);
                }
                debugPrint("========== 开始执行 ==========");
            }

            m_callStack.clear();
            m_callStack.reserve(256);
            m_globalSlots.assign(m_code.varNames.size(), Value(nullptr));

            OptimizedCallFrame mainFrame;
            mainFrame.codeBlock = &m_code;
            mainFrame.returnIp = 0;
            mainFrame.frame.resize(m_code.varNames.size(), m_code.funcNames.size());


            m_callStack.push_back(std::move(mainFrame));
            m_currentFrame = &m_callStack.back().frame;
            m_currentCodeBlock = m_callStack.back().codeBlock;
            m_running = true;
            m_breakTargets.clear();

            try
            {
                QOT_PROF_RUN_BEGIN();
                execute_fast();
                QOT_PROF_RUN_END();
            }
            catch (const BreakException &e)
            {
                debugPrint("Break caught, jumping to break target");
                if (!m_breakTargets.empty())
                {
                    m_currentFrame->ip = m_breakTargets.back();
                    m_breakTargets.pop_back();
                    return execute();
                }
            }
            return true;
        }

        Value getReturnValue() const
        {
            if (m_callStack.empty() || !m_currentFrame || m_currentFrame->stack.empty())
            {
                return Value(nullptr);
            }
            return m_currentFrame->stack.back();
        }

    private:
        void execute_fast();
        // T-34/T-35：把每个 funcNames[i] 的调用目标解析一次（builtin → 函数指针，
        // 用户函数 → CodeBlock*），填充 cb.callTargets。解析优先级与旧运行时逐条一致：
        // 内置表 → m_functions（后注册同名覆盖先注册）→ 本块 constants 里首个同名块。
        void resolve_calls(CodeBlock &cb);
        void resolve_calls_recursive(CodeBlock &cb);

        bool m_isCallbackExecuting = false;

        CodeBlock m_code;
        std::vector<OptimizedCallFrame> m_callStack;
        std::unordered_map<std::string, CodeBlockPtr> m_functions;
        StackFrame *m_currentFrame = nullptr;
        CodeBlock *m_currentCodeBlock = nullptr;
        std::unordered_map<std::string, Value> m_globals;
        std::vector<Value> m_globalSlots;
        std::vector<size_t> m_breakTargets;
        ModuleLoader &m_moduleLoader = ModuleLoader::instance();
        bool m_running = true;
        bool m_debug = false;
        std::vector<OptimizedCallFrame> m_framePool;
        QOT_NOINLINE void recycleFrame()
        {
            if (m_framePool.size() < 64)
            {
                QOT_PROF_ADD(framePoolHits, 1);
                m_framePool.push_back(std::move(m_callStack.back()));
            }
            else
            {
                QOT_PROF_ADD(framePoolMisses, 1);
            }
            m_callStack.pop_back();
        }

        inline void debugPrint(const std::string &msg)
        {
            if (m_debug)
            {
                std::cerr << "[DEBUG] " << msg << std::endl;
            }
        }

       
        template <class F>
        QOT_NOINLINE void dbgSlow(F &&f)
        {
            debugPrint(f());
        }

        inline void dumpStack()
        {
            if (!m_debug)
                return;
            std::cerr << "[STACK] (size=" << m_currentFrame->stack.size() << "): ";
            for (size_t i = 0; i < m_currentFrame->stack.size(); i++)
            {
                std::cerr << m_currentFrame->stack[i].toString() << " ";
            }
            std::cerr << std::endl;
        }

        inline void dumpLocals()
        {
            if (!m_debug)
                return;
            std::cerr << "[LOCALS]: ";
            for (size_t i = 0; i < m_currentFrame->locals.size(); i++)
            {
                std::cerr << "[" << i << "]=" << m_currentFrame->locals[i].toString() << " ";
            }
            std::cerr << std::endl;
        }
    };
}

#endif