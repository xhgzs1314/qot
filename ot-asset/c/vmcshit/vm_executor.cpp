#include "vm_executor.h"
#include <chrono>
#define QOT_DBG(...) dbgSlow([&]() -> std::string { return (__VA_ARGS__); })

namespace qot
{
    static const std::string kNoName;
    static const CodeBlock::CallTarget kEmptyTarget{}; // funcIdx 越界时的安全空目标

    void VMExecutor::resolve_calls(CodeBlock &cb)
    {
        cb.callTargets.assign(cb.funcNames.size(), CodeBlock::CallTarget{});
        for (size_t i = 0; i < cb.funcNames.size(); i++)
        {
            const std::string &name = cb.funcNames[i];
            CodeBlock::CallTarget t;
            if (BuiltinManager::instance().hasFunction(name))
            {
                t.builtin = &BuiltinManager::instance().getFunction(name);
                if (!*t.builtin)
                    t.builtin = nullptr; // 空表项当未找到
            }
            else
            {
                auto it = m_functions.find(name);
                if (it != m_functions.end())
                {
                    t.user = it->second.get();
                }
                else
                {
                    for (const auto &c : cb.constants)
                    {
                        if (c.isCodeBlock())
                        {
                            auto p = c.asCodeBlock();
                            if (!p->funcNames.empty() && p->funcNames[0] == name)
                            {
                                t.user = p.get();
                                break;
                            }
                        }
                    }
                }
            }
            cb.callTargets[i] = t;
        }
    }

    void VMExecutor::resolve_calls_recursive(CodeBlock &cb)
    {
        resolve_calls(cb);
        for (const auto &c : cb.constants)
        {
            if (c.isCodeBlock())
            {
                auto p = c.asCodeBlock();
                if (p->callTargets.size() != p->funcNames.size())
                    resolve_calls_recursive(*p);
            }
        }
    }

    void VMExecutor::execute_fast()
    {
        uint8_t op;
        size_t argLen;

        if (!m_currentFrame || !m_currentCodeBlock)
            return;
        StackFrame *fr = nullptr;
        const CodeBlock *cb = nullptr;
        const Instr *instrBase = nullptr;
        size_t instrCount = 0;
        size_t ip = 0;

        while (m_running)
        {
            StackFrame *curFrame = m_currentFrame;
            const CodeBlock *curCode = m_currentCodeBlock;
            if (fr != curFrame || cb != curCode)
            {
                if (!curFrame || !curCode)
                    break;
                fr = curFrame;
                cb = curCode;
                instrBase = cb->instructions.data();
                instrCount = cb->instructions.size();
                ip = fr->ip;
            }

            if (ip >= instrCount)
            {

                if (!m_callStack.empty())
                {
                    Value result(nullptr);
                    if (!fr->stack.empty())
                    {
                        result = fr->stack.back();
                        fr->stack.pop_back();
                    }

                    // 返回地址存在被弹出的那一帧上
                    size_t retIp = m_callStack.back().returnIp;
                    recycleFrame();
                    if (!m_callStack.empty())
                    {
                        m_currentFrame = &m_callStack.back().frame;
                        m_currentCodeBlock = m_callStack.back().codeBlock;
                        m_currentFrame->stack.push_back(result);
                        m_currentFrame->ip = retIp;
                        continue; // 帧变了
                    }
                    else
                    {
                        m_running = false;
                        return;
                    }
                }
                break;
            }

            const Instr &ins = instrBase[ip];
            op = static_cast<uint8_t>(ins.op);
            argLen = ins.argLen;
            fr->ip = ++ip;
            QOT_PROF_OP_BEGIN();
            switch (op)
            {
            case 0: // NOP
                if (m_debug)
                    QOT_DBG("NOP");
                break;

            case 1: // PUSH_CONST
            {
                if (argLen >= 4)
                {
                    int32_t idx = ins.a;
                    if (m_debug)
                        QOT_DBG("PUSH_CONST idx=" + std::to_string(idx));
                    if (idx == NULL_MAGIC)
                    {
                        fr->stack.emplace_back(nullptr);
                    }
                    else if (idx >= 0 && idx < (int32_t)cb->constants.size())
                    {
                        fr->stack.push_back(cb->constants[idx]);
                    }
                    else
                    {
                        fr->stack.emplace_back(nullptr);
                    }
                    if (m_debug)
                        dumpStack();
                }
            }
            break;

            case 2: // PUSH_VAR
            {
                if (argLen >= 4)
                {
                    int32_t idx = ins.a;
                    if (m_debug)
                        QOT_DBG("PUSH_VAR idx=" + std::to_string(idx));
                    if (idx & GLOBAL_BIT)
                    {
                        uint32_t g = static_cast<uint32_t>(idx & ~GLOBAL_BIT);
                        if (g < m_globalSlots.size())
                            fr->stack.push_back(m_globalSlots[g]);
                        else
                            fr->stack.emplace_back(nullptr);
                    }
                    else if (idx >= 0 && idx < (int32_t)fr->locals.size())
                    {
                        fr->stack.push_back(fr->get_local(static_cast<uint32_t>(idx)));
                    }
                    else if (idx >= 0 && idx < (int32_t)fr->constants.size())
                    {
                        fr->stack.push_back(fr->get_const(static_cast<uint32_t>(idx)));
                    }
                    else if (idx >= 0 && idx < (int32_t)cb->varNames.size())
                    {
                        QOT_PROF_ADD(globalFallbackReads, 1);
                        const std::string &name = cb->varNames[idx];
                        auto globalIt = m_globals.find(name);
                        if (globalIt != m_globals.end())
                        {
                            fr->stack.push_back(globalIt->second);
                        }
                        else
                        {
                            fr->stack.emplace_back(nullptr);
                        }
                    }
                    else
                    {
                        fr->stack.emplace_back(nullptr);
                    }
                    if (m_debug)
                        dumpStack();
                }
            }
            break;

            case 3: // STORE_VAR
            {
                if (!fr->stack.empty() && argLen >= 4)
                {
                    int32_t idx = ins.a;
                    if (m_debug)
                        QOT_DBG("STORE_VAR idx=" + std::to_string(idx));
                    Value value = fr->stack.back();
                    fr->stack.pop_back();
                    if (idx & GLOBAL_BIT)
                    {
                        uint32_t g = static_cast<uint32_t>(idx & ~GLOBAL_BIT);
                        if (g >= m_globalSlots.size())
                            m_globalSlots.resize(g + 1);
                        m_globalSlots[g] = value;
                    }
                    else if (idx >= 0 && idx < (int32_t)fr->locals.size())
                    {
                        fr->set_local(static_cast<uint32_t>(idx), value);
                    }
                    else if (idx >= 0 && idx < (int32_t)fr->constants.size())
                    {
                        if (m_debug)
                            QOT_DBG("  错误: 不能修改常量!");
                    }
                    else if (idx >= 0 && idx < (int32_t)cb->varNames.size())
                    {
                        QOT_PROF_ADD(globalFallbackWrites, 1);
                        const std::string &name = cb->varNames[idx];
                        m_globals[name] = value;
                    }
                    if (m_debug)
                        dumpLocals();
                }
            }
            break;

            case 4: // STORE_CONST
            {
                if (!fr->stack.empty() && argLen >= 4)
                {
                    int32_t idx = ins.a;
                    if (idx & GLOBAL_BIT)
                    {
                        // 顶层的 const 声明
                        uint32_t g = static_cast<uint32_t>(idx & ~GLOBAL_BIT);
                        if (g >= m_globalSlots.size())
                            m_globalSlots.resize(g + 1);
                        m_globalSlots[g] = fr->stack.back();
                        fr->stack.pop_back();
                    }
                    else if (idx >= 0 && idx < (int32_t)fr->constants.size())
                    {
                        Value value = fr->stack.back();
                        fr->stack.pop_back();
                        fr->set_const(static_cast<uint32_t>(idx), value);
                        if (idx >= 0 && idx < (int32_t)cb->funcNames.size())
                        {
                            const std::string &name = cb->funcNames[idx];
                            m_globals[name] = value;
                        }
                    }
                }
            }
            break;

            case 5:  // POP
            case 50: // POP_STMT
                if (!fr->stack.empty())
                {
                    fr->stack.pop_back();
                }
                break;

            case 20: // JUMP
            {
                if (argLen >= 4)
                {
                    int32_t target = ins.a;
                    if (target >= 0 && target < (int32_t)cb->instructions.size())
                    {
                        ip = static_cast<size_t>(target);
                        fr->ip = ip;
                    }
                }
            }
            break;

            case 21: // JUMP_IF_FALSE
            {
                if (!fr->stack.empty() && argLen >= 4)
                {
                    Value cond = fr->stack.back();
                    fr->stack.pop_back();
                    if (!cond.isTruthy())
                    {
                        int32_t target = ins.a;
                        if (target >= 0 && target < (int32_t)cb->instructions.size())
                        {
                            ip = static_cast<size_t>(target);
                        fr->ip = ip;
                        }
                    }
                }
            }
            break;

            case 22: // JUMP_IF_TRUE
            {
                if (!fr->stack.empty() && argLen >= 4)
                {
                    Value cond = fr->stack.back();
                    fr->stack.pop_back();
                    if (cond.isTruthy())
                    {
                        int32_t target = ins.a;
                        if (target >= 0 && target < (int32_t)cb->instructions.size())
                        {
                            ip = static_cast<size_t>(target);
                        fr->ip = ip;
                        }
                    }
                }
            }
            break;

            case 10: // BINARY_OP
            {
                if (fr->stack.size() >= 2 && argLen >= 4)
                {
                    int32_t binop = ins.a;
                    Value right = fr->stack.back();
                    fr->stack.pop_back();
                    Value left = fr->stack.back();
                    fr->stack.pop_back();

                    if (left.isNumber() && right.isNumber())
                    {
                        double l = left.rawNumber(), r = right.rawNumber();
                        switch (binop)
                        {
                        case 1:
                            fr->stack.emplace_back(l + r);
                            break;
                        case 2:
                            fr->stack.emplace_back(l - r);
                            break;
                        case 3:
                            fr->stack.emplace_back(l * r);
                            break;
                        case 4:
                            fr->stack.emplace_back(r != 0 ? l / r : 0.0);
                            break;
                        case 5:
                            fr->stack.emplace_back(r != 0 ? std::fmod(l, r) : 0.0);
                            break;
                        case 6:
                            fr->stack.emplace_back(std::pow(l, r));
                            break;
                        case 7:
                            fr->stack.emplace_back(l > r);
                            break;
                        case 8:
                            fr->stack.emplace_back(l < r);
                            break;
                        case 9:
                            fr->stack.emplace_back(l >= r);
                            break;
                        case 10:
                            fr->stack.emplace_back(l <= r);
                            break;
                        case 11:
                            fr->stack.emplace_back(l == r);
                            break;
                        case 12:
                            fr->stack.emplace_back(l != r);
                            break;
                        default:
                            fr->stack.emplace_back(nullptr);
                        }
                    }
                    else if (binop == 11)
                    {
                        bool result = false;
                        if (left.isNumber() && right.isNumber())
                            result = left.rawNumber() == right.rawNumber();
                        else if (left.isBool() && right.isBool())
                            result = left.rawBool() == right.rawBool();
                        else if (left.isString() && right.isString())
                            result = left.asString() == right.asString();
                        else if (left.isNull() && right.isNull())
                            result = true;
                        else if ((left.isNumber() && right.isBool()))
                            result = (left.rawNumber() != 0) == right.rawBool();
                        else if ((left.isBool() && right.isNumber()))
                            result = left.rawBool() == (right.rawNumber() != 0);
                        else
                            result = false;
                        fr->stack.emplace_back(result);
                    }
                    else if (binop == 12)
                    {
                        bool result = false;
                        if (left.isNumber() && right.isNumber())
                            result = left.rawNumber() != right.rawNumber();
                        else if (left.isBool() && right.isBool())
                            result = left.rawBool() != right.rawBool();
                        else if (left.isString() && right.isString())
                            result = left.asString() != right.asString();
                        else if (left.isNull() && right.isNull())
                            result = false;
                        else
                            result = true;
                        fr->stack.emplace_back(result);
                    }
                    else if (binop == 13)
                    {
                        fr->stack.emplace_back(left.isTruthy() && right.isTruthy());
                    }
                    else if (binop == 14)
                    {
                        fr->stack.emplace_back(left.isTruthy() || right.isTruthy());
                    }
                    else if (binop == 1)
                    {
                        auto to_string = [](const Value &v) -> std::string
                        {
                            if (v.isNull())
                                return "";
                            if (v.isBool())
                                return v.rawBool() ? "true" : "false";
                            if (v.isNumber())
                            {
                                double num = v.rawNumber();
                                if (num == static_cast<int64_t>(num))
                                    return std::to_string(static_cast<int64_t>(num));
                                return std::to_string(num);
                            }
                            if (v.isString())
                                return v.asString();
                            return v.toString();
                        };
                        fr->stack.emplace_back(to_string(left) + to_string(right));
                    }
                    else
                    {
                        fr->stack.emplace_back(nullptr);
                    }
                    if (m_debug)
                        dumpStack();
                }
            }
            break;

            case 11: // UNARY_OP
            {
                if (!fr->stack.empty() && argLen >= 4)
                {
                    int32_t unop = ins.a;
                    Value val = fr->stack.back();
                    fr->stack.pop_back();
                    if (unop == 1)
                    {
                        if (val.isNumber())
                        {
                            fr->stack.emplace_back(-val.rawNumber());
                        }
                        else
                        {
                            fr->stack.emplace_back(nullptr);
                        }
                    }
                    else if (unop == 2)
                    {
                        fr->stack.emplace_back(!val.isTruthy());
                    }
                    else
                    {
                        fr->stack.emplace_back(nullptr);
                    }
                }
            }
            break;

            case 30:
            {
                if (argLen < 8)
                    break;

                int32_t funcIdx = ins.a, argCount = ins.b;

                // T-34/T-35：调用目标已在加载期解析（resolve_calls），运行期零字符串哈希。
                // 防御：若目标表与函数名表失配（直接构造的 CodeBlock 等场景），现场补解析。
                if (cb->callTargets.size() != cb->funcNames.size())
                    resolve_calls(*const_cast<CodeBlock *>(cb));
                const CodeBlock::CallTarget &t =
                    (funcIdx >= 0 && funcIdx < (int32_t)cb->callTargets.size())
                        ? cb->callTargets[funcIdx]
                        : kEmptyTarget;

                if (m_debug)
                {
                    const std::string &funcName =
                        (funcIdx >= 0 && funcIdx < (int32_t)cb->funcNames.size())
                            ? cb->funcNames[funcIdx]
                            : kNoName;
                    QOT_DBG("CALL func=" + funcName + " argCount=" + std::to_string(argCount) + " current_ip=" + std::to_string(fr->ip));
                }

                if (t.builtin)
                {
                    QOT_PROF_ADD(builtinCalls, 1);
                    std::vector<Value> args;
                    args.reserve(argCount);
                    for (int32_t i = 0; i < argCount; i++)
                    {
                        if (fr->stack.empty())
                            break;
                        args.push_back(fr->stack.back());
                        fr->stack.pop_back();
                    }
                    std::reverse(args.begin(), args.end());
                    fr->stack.push_back((*t.builtin)(args));
                    break;
                }

                QOT_PROF_ADD(userCalls, 1);
                CodeBlock *funcCode = t.user;

                if (!funcCode)
                {
                    if (m_debug)
                    {
                        const std::string &funcName =
                            (funcIdx >= 0 && funcIdx < (int32_t)cb->funcNames.size())
                                ? cb->funcNames[funcIdx]
                                : kNoName;
                        QOT_DBG("函数未找到: " + funcName);
                    }
                    fr->stack.emplace_back(nullptr);
                    break;
                }
                size_t returnAddress = fr->ip;

                if (m_debug)
                {
                    QOT_DBG("保存返回地址: " + std::to_string(returnAddress));
                    QOT_DBG("函数指令数: " + std::to_string(funcCode->instructions.size()));
                }
                OptimizedCallFrame newFrame;
                if (!m_framePool.empty())
                {
                    QOT_PROF_ADD(framePoolHits, 1);
                    newFrame = std::move(m_framePool.back());
                    m_framePool.pop_back();
                    newFrame.frame.reuse();
                }
                else
                {
                    QOT_PROF_ADD(framePoolMisses, 1);
                }
                newFrame.codeBlock = funcCode;
                newFrame.returnIp = returnAddress;
                newFrame.frame.resize(funcCode->varNames.size(), funcCode->funcNames.size());
                newFrame.frame.ip = 0;
                int32_t paramCount = std::min(argCount, (int32_t)funcCode->varNames.size());
                std::vector<Value> args;
                args.reserve(paramCount);
                for (int32_t i = 0; i < paramCount; i++)
                {
                    if (fr->stack.empty())
                        break;
                    args.push_back(fr->stack.back());
                    fr->stack.pop_back();
                }
                std::reverse(args.begin(), args.end());
                for (int32_t i = 0; i < paramCount && i < (int32_t)args.size(); i++)
                {
                    newFrame.frame.set_local(static_cast<uint32_t>(i), args[i]);
                    if (m_debug)
                    {
                        QOT_DBG("设置参数[" + std::to_string(i) + "] = " + args[i].toString());
                    }
                }
                m_callStack.push_back(std::move(newFrame));
                m_currentFrame = &m_callStack.back().frame;
                m_currentCodeBlock = m_callStack.back().codeBlock;

                if (m_debug)
                {
                    const std::string &fnameDbg =
                        (funcIdx >= 0 && funcIdx < (int32_t)cb->funcNames.size())
                            ? cb->funcNames[funcIdx]
                            : kNoName;
                    QOT_DBG("切换到函数 " + fnameDbg + ", 从ip=0开始执行");
                    QOT_DBG("调用栈深度: " + std::to_string(m_callStack.size()));
                }
            }
            break;

            case 31: // CALL_METHOD
                break;

            case 32: // RETURN
            {
                if (m_debug)
                {
                    QOT_DBG("RETURN: 当前栈深度=" + std::to_string(m_callStack.size()) +
                               ", ip=" + std::to_string(fr->ip));
                }
                Value result(nullptr);
                if (!fr->stack.empty())
                {
                    result = fr->stack.back();
                    fr->stack.pop_back();
                    if (m_debug)
                        QOT_DBG("返回值: " + result.toString());
                }
                if (m_callStack.size() <= 1)
                {
                    if (m_debug)
                        QOT_DBG("主函数返回，结束执行");
                    m_running = false;
                    QOT_PROF_OP_END(32);
                    return;
                }
                size_t returnIp = m_callStack.back().returnIp;

                if (m_debug)
                {
                    QOT_DBG("返回地址: " + std::to_string(returnIp));
                }
                recycleFrame();
                m_currentFrame = &m_callStack.back().frame;
                m_currentCodeBlock = m_callStack.back().codeBlock;
                m_currentFrame->stack.push_back(result);
                m_currentFrame->ip = returnIp;

                if (m_debug)
                {
                    QOT_DBG("返回到: ip=" + std::to_string(m_currentFrame->ip) +
                               ", 栈深度=" + std::to_string(m_callStack.size()));
                    dumpStack();
                }
            }
            break;

            case 33: // CALL_STATIC
            {
                if (argLen >= 8)
                {
                    int32_t funcIdx = ins.a, argCount = ins.b;

                    std::vector<Value> args;
                    args.reserve(argCount);
                    for (int32_t i = 0; i < argCount; i++)
                    {
                        if (fr->stack.empty())
                            break;
                        args.push_back(fr->stack.back());
                        fr->stack.pop_back();
                    }
                    std::reverse(args.begin(), args.end());

                    std::string funcName;
                    if (funcIdx >= 0 && funcIdx < (int32_t)cb->funcNames.size())
                    {
                        funcName = cb->funcNames[funcIdx];
                    }

                    if (!args.empty() && args[0].isDict())
                    {
                        const auto &moduleDict = args[0].asDict();
                        auto it = moduleDict.find(funcName);
                        if (it != moduleDict.end() && (it->second.isFunction() || it->second.isBuiltin()))
                        {
                            std::vector<Value> realArgs;
                            for (size_t i = 1; i < args.size(); i++)
                            {
                                realArgs.push_back(args[i]);
                            }
                            const auto &func = it->second.asFunction();
                            Value result = func(realArgs);
                            fr->stack.push_back(result);
                        }
                        else
                        {
                            fr->stack.emplace_back(nullptr);
                        }
                    }
                    else
                    {
                        fr->stack.emplace_back(nullptr);
                    }
                }
            }
            break;

            case 40: // MAKE_ARRAY
            {
                if (argLen >= 4)
                {
                    int32_t count = ins.a;
                    auto arr = ArrayPtr::make();
                    arr->reserve(count);
                    for (int32_t i = 0; i < count; i++)
                    {
                        if (fr->stack.empty())
                            break;
                        arr->push_back(std::move(fr->stack.back()));
                        fr->stack.pop_back();
                    }
                    std::reverse(arr->begin(), arr->end());
                    fr->stack.emplace_back(arr);
                }
            }
            break;

            case 41: // MAKE_DICT
            {
                if (argLen >= 4)
                {
                    int32_t count = ins.a;
                    auto dict = DictPtr::make();
                    for (int32_t i = 0; i < count; i++)
                    {
                        if (fr->stack.size() < 2)
                            break;
                        Value value = fr->stack.back();
                        fr->stack.pop_back();
                        Value key = fr->stack.back();
                        fr->stack.pop_back();
                        if (key.isString())
                        {
#ifdef QOT_DICT_LEGACY_BASELINE
                            (*dict)[key.asString()] = value;
#else
                            dict->at_h(key.string_hash(), key.asString()) = value;
#endif
                        }
                    }
                    fr->stack.emplace_back(dict);
                }
            }
            break;

            case 42: // INDEX_GET
            {
                if (fr->stack.size() >= 2)
                {
                    Value index = fr->stack.back();
                    fr->stack.pop_back();
                    Value container = fr->stack.back();
                    fr->stack.pop_back();

                    Value result(nullptr);
                    if (container.isArray() && index.isNumber())
                    {
                        int idx = static_cast<int>(index.rawNumber());
                        const auto &arr = container.asArray();
                        if (idx >= 0 && idx < static_cast<int>(arr.size()))
                        {
                            result = arr[idx];
                        }
                    }
                    else if (container.isDict() && index.isString())
                    {
                        const auto &dict = container.asDict();
#ifdef QOT_DICT_LEGACY_BASELINE
                        auto it = dict.find(index.asString());
                        if (it != dict.end())
                        {
                            result = it->second;
                        }
#else
                        // T-33：键哈希缓存在 StringBox 上，热路径零重算
                        const Value *hit = dict.find_ptr_h(index.string_hash(), index.asString());
                        if (hit)
                        {
                            result = *hit;
                        }
#endif
                    }
                    else if (container.isString() && index.isNumber())
                    {
                        int idx = static_cast<int>(index.rawNumber());
                        const std::string &str = container.asString();
                        if (idx >= 0 && idx < static_cast<int>(str.size()))
                        {
                            result = Value(std::string(1, str[idx]));
                        }
                    }
                    fr->stack.push_back(result);
                }
                else
                {
                    fr->stack.emplace_back(nullptr);
                }
            }
            break;

            case 43: // INDEX_SET
            {
                if (fr->stack.size() >= 3)
                {
                    Value index = fr->stack.back();
                    fr->stack.pop_back();
                    Value container = fr->stack.back();
                    fr->stack.pop_back();
                    Value value = fr->stack.back();
                    fr->stack.pop_back();

                    if (container.isArray() && index.isNumber())
                    {
                        int idx = static_cast<int>(index.rawNumber());
                        auto &arr = container.asArray();
                        if (idx >= 0 && idx < (int)arr.size())
                        {
                            arr[idx] = value;
                        }
                    }
                    else if (container.isDict() && index.isString())
                    {
                        auto &dict = container.asDict();
#ifdef QOT_DICT_LEGACY_BASELINE
                        dict[index.asString()] = value;
#else
                        dict.at_h(index.string_hash(), index.asString()) = value;
#endif
                    }
                    fr->stack.push_back(value);
                }
            }
            break;

            case 51: // STOP
                m_running = false;
                QOT_PROF_OP_END(51);
                return;

            case 52: // BREAK
            {
                if (argLen >= 4)
                {
                    int32_t target = ins.a;
                    if (target >= 0 && target < (int32_t)cb->instructions.size())
                    {
                        ip = static_cast<size_t>(target);
                        fr->ip = ip;
                        break;
                    }
                }
                if (!m_breakTargets.empty())
                {
                    ip = m_breakTargets.back();
                    fr->ip = ip;
                    m_breakTargets.pop_back();
                    break;
                }
                m_running = false;
                QOT_PROF_OP_END(52);
                return;
            }

            case 60: // REQUIRE
            {
                if (argLen >= 4)
                {
                    uint8_t inlineBuf[8];
                    const uint8_t *argData;
                    size_t rawLen;
                    if (ins.blobIdx >= 0)
                    {
                        const auto &blob = cb->blobs[ins.blobIdx];
                        argData = blob.data();
                        rawLen = blob.size();
                    }
                    else
                    {
                        std::memcpy(inlineBuf, &ins.a, 4);
                        std::memcpy(inlineBuf + 4, &ins.b, 4);
                        argData = inlineBuf;
                        rawLen = argLen;
                    }
                    uint32_t len;
                    std::memcpy(&len, argData, 4);
                    std::string libName;
                    if (len > 0 && 4 + len <= rawLen)
                    {
                        libName = std::string(reinterpret_cast<const char *>(argData + 4), len);
                    }
                    Value module = m_moduleLoader.require(libName, cb->sourceFile);
                    fr->stack.push_back(module);
                }
            }
            break;

            default:
                if (m_debug)
                    QOT_DBG("Unknown op: " + std::to_string(op));
                break;
            }
            QOT_PROF_OP_END(op);
        }
    }

}