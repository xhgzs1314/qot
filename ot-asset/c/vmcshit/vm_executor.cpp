#include "vm_executor.h"
#include "rpn_executor.h"
#include <chrono>
#define QOT_DBG(...) dbgSlow([&]() -> std::string { return (__VA_ARGS__); })

namespace qot
{
    static const std::string kNoName;
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
                const std::string &funcName =
                    (funcIdx >= 0 && funcIdx < (int32_t)cb->funcNames.size())
                        ? cb->funcNames[funcIdx]
                        : kNoName;

                if (m_debug)
                {
                    QOT_DBG("CALL func=" + funcName + " argCount=" + std::to_string(argCount) + " current_ip=" + std::to_string(fr->ip));
                }

            
                if (BuiltinManager::instance().hasFunction(funcName))
                {
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
                    fr->stack.push_back(BuiltinManager::instance().call(funcName, args));
                    break;
                }

          
                CodeBlockPtr funcCode = nullptr;
                auto it = m_functions.find(funcName);
                if (it != m_functions.end())
                    funcCode = it->second;

                if (!funcCode)
                {

                    for (const auto &c : cb->constants)
                    {
                        if (c.isCodeBlock())
                        {
                            auto cb = c.asCodeBlock();
                            // 只认 funcNames[0]
                            if (!cb->funcNames.empty() && cb->funcNames[0] == funcName)
                            {
                                funcCode = cb;
                                break;
                            }
                        }
                    }
                }

                if (!funcCode)
                {
                    if (m_debug)
                        QOT_DBG("函数未找到: " + funcName);
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
                    newFrame = std::move(m_framePool.back());
                    m_framePool.pop_back();
                    newFrame.frame.reuse();
                }
                newFrame.codeBlock = funcCode.get();
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
                    QOT_DBG("切换到函数 " + funcName + ", 从ip=0开始执行");
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
                            (*dict)[key.asString()] = value;
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
                        auto it = dict.find(index.asString());
                        if (it != dict.end())
                        {
                            result = it->second;
                        }
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
                        dict[index.asString()] = value;
                    }
                    fr->stack.push_back(value);
                }
            }
            break;

            case 51: // STOP
                m_running = false;
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

            case 200: // RPN_LOOP_START
            {
                if (m_rpn_enabled && argLen >= 4)
                {
                    int32_t loop_id = ins.a;
                    if (m_debug)
                        QOT_DBG("[RPN] Loop start: id=" + std::to_string(loop_id));

                    auto it = m_rpn_loops.find(static_cast<uint32_t>(loop_id));
                    if (it != m_rpn_loops.end())
                    {
                        if (it->second.compiled && it->second.iterations > 0)
                        {
                            m_in_rpn_loop = true;
                            m_current_rpn_loop_id = static_cast<uint32_t>(loop_id);
                            ip = it->second.body_end;
                            fr->ip = ip;
                        }
                    }
                    else
                    {
                        RPNLoopInfo info;
                        info.loop_id = static_cast<uint32_t>(loop_id);
                        info.condition_ip = static_cast<uint32_t>(fr->ip - 1);
                        info.compiled = false;
                        info.iterations = -1;
                        info.is_active = false;
                        m_rpn_loops[static_cast<uint32_t>(loop_id)] = info;
                    }
                }
            }
            break;

            case 201: // RPN_LOOP_END
            {
                if (m_rpn_enabled && argLen >= 4)
                {
                    int32_t loop_id = ins.a;
                    if (m_debug)
                        QOT_DBG("[RPN] Loop end: id=" + std::to_string(loop_id));

                    auto it = m_rpn_loops.find(static_cast<uint32_t>(loop_id));
                    if (it == m_rpn_loops.end())
                        break;

                    RPNLoopInfo &info = it->second;
                    if (m_in_rpn_loop && m_current_rpn_loop_id == info.loop_id)
                    {
                        m_in_rpn_loop = false;
                        break;
                    }

                    info.body_end = static_cast<uint32_t>(fr->ip - 1);
                    if (!find_loop_boundaries(info))
                    {
                        m_rpn_loops.erase(it);
                        break;
                    }

                    if (!info.compiled)
                    {
                        if (compile_rpn_loop(info.body_start, info.body_end, info.expr_info))
                        {
                            info.compiled = true;
                        }
                        else
                        {
                            m_rpn_loops.erase(it);
                            break;
                        }
                    }

                    if (info.iterations < 0)
                    {
                        info.iterations = estimate_iterations(info.condition_ip);
                        if (info.iterations <= 0)
                        {
                            m_rpn_loops.erase(it);
                            break;
                        }
                    }

                    std::vector<double> var_values;
                    std::vector<Value *> var_ptrs;
                    bool all_numeric = true;

                    for (uint32_t var_idx : info.expr_info.var_indices)
                    {
                        if (var_idx >= cb->varNames.size())
                        {
                            all_numeric = false;
                            break;
                        }
                        const std::string &var_name = cb->varNames[var_idx];
                        Value *val_ptr = nullptr;
                        uint32_t local_idx = cb->get_var_index(var_name);
                        if (local_idx < fr->locals.size())
                        {
                            val_ptr = &fr->get_local(local_idx);
                        }
                        else
                        {
                            auto git = m_globals.find(var_name);
                            if (git != m_globals.end())
                            {
                                val_ptr = &git->second;
                            }
                        }

                        if (!val_ptr || !val_ptr->isNumber())
                        {
                            all_numeric = false;
                            break;
                        }
                        var_values.push_back(val_ptr->rawNumber());
                        var_ptrs.push_back(val_ptr);
                    }

                    if (!all_numeric || var_values.empty())
                    {
                        m_rpn_loops.erase(it);
                        break;
                    }

                    for (int64_t i = 0; i < info.iterations; i++)
                    {
                        double result = RPNExecutor::execute(
                            info.expr_info.rpn_code.data(),
                            static_cast<uint32_t>(info.expr_info.rpn_code.size()),
                            info.expr_info.constants.data(),
                            var_values.data(),
                            static_cast<uint32_t>(var_values.size()));

                        for (size_t j = 0; j < var_ptrs.size() && j < var_values.size(); j++)
                        {
                            if (var_ptrs[j])
                            {
                                if (is_variable_modified_in_loop(cb->varNames[info.expr_info.var_indices[j]],
                                                                 info.body_start, info.body_end))
                                {
                                    var_values[j] = result;
                                    *var_ptrs[j] = Value(result);
                                }
                            }
                        }
                    }

                    while (!fr->stack.empty())
                    {
                        fr->stack.pop_back();
                    }
                    ip = info.exit_ip;
                    fr->ip = ip;
                    m_rpn_loops.erase(it);
                }
            }
            break;

            default:
                if (m_debug)
                    QOT_DBG("Unknown op: " + std::to_string(op));
                break;
            }
        }
    }

    bool VMExecutor::find_loop_boundaries(RPNLoopInfo &info)
    {
        for (uint32_t ip = info.body_end; ip < m_currentCodeBlock->instructions.size(); ip++)
        {
            auto &instr = m_currentCodeBlock->instructions[ip];
            if (instr.op == OpCode::JUMP)
            {
                int32_t target = instr.a;
                info.jump_back_ip = ip;
                info.body_start = static_cast<uint32_t>(target);
                info.exit_ip = ip + 1;
                if (info.body_start < info.body_end)
                {
                    return true;
                }
                break;
            }
        }
        for (uint32_t ip = info.condition_ip; ip < m_currentCodeBlock->instructions.size(); ip++)
        {
            auto &instr = m_currentCodeBlock->instructions[ip];
            if (instr.op == OpCode::JUMP_IF_FALSE || instr.op == OpCode::JUMP_IF_TRUE)
            {
                int32_t target = instr.a;
                if (target > 0 && target < (int32_t)ip)
                {
                    info.body_start = static_cast<uint32_t>(target);
                    info.exit_ip = ip + 1;
                    return true;
                }
            }
        }
        return false;
    }

    bool VMExecutor::is_variable_modified_in_loop(const std::string &var_name,
                                                  uint32_t loop_start,
                                                  uint32_t loop_end)
    {
        for (uint32_t ip = loop_start; ip < loop_end && ip < m_currentCodeBlock->instructions.size(); ip++)
        {
            auto &instr = m_currentCodeBlock->instructions[ip];
            if (instr.op == OpCode::STORE_VAR)
            {
                int32_t var_idx = instr.a;
                if (var_idx >= 0 && var_idx < (int32_t)m_currentCodeBlock->varNames.size())
                {
                    if (m_currentCodeBlock->varNames[var_idx] == var_name)
                    {
                        return true;
                    }
                }
            }
        }
        return false;
    }

    bool VMExecutor::compile_rpn_loop(uint32_t loop_start, uint32_t loop_end,
                                      ExpressionInfo &out_info)
    {
        if (loop_start >= loop_end)
            return false;

        std::vector<std::pair<uint8_t, std::vector<uint8_t>>> converted;
        for (uint32_t ip = loop_start; ip < loop_end; ip++)
        {
            OpCode op = m_currentCodeBlock->instructions[ip].op;
            if (op == OpCode::JUMP || op == OpCode::JUMP_IF_FALSE ||
                op == OpCode::JUMP_IF_TRUE || op == OpCode::RPN_LOOP_START ||
                op == OpCode::RPN_LOOP_END)
            {
                continue;
            }
            const Instr &src = m_currentCodeBlock->instructions[ip];
            std::vector<uint8_t> raw;
            if (src.blobIdx >= 0)
            {
                raw = m_currentCodeBlock->blobs[src.blobIdx];
            }
            else
            {
                uint8_t buf[8];
                std::memcpy(buf, &src.a, 4);
                std::memcpy(buf + 4, &src.b, 4);
                raw.assign(buf, buf + src.argLen);
            }
            converted.emplace_back(static_cast<uint8_t>(op), std::move(raw));
        }

        if (converted.empty())
            return false;

        return RPNExecutor::compile_from_bytecode(
            converted, 0, static_cast<uint32_t>(converted.size()),
            m_currentCodeBlock->varNames, m_currentCodeBlock->constants, out_info);
    }

    int64_t VMExecutor::estimate_iterations(uint32_t loop_start)
    {
        for (uint32_t ip = loop_start; ip < (uint32_t)m_currentCodeBlock->instructions.size(); ip++)
        {
            auto &instr = m_currentCodeBlock->instructions[ip];
            if (instr.op == OpCode::JUMP_IF_FALSE || instr.op == OpCode::JUMP_IF_TRUE)
            {
                for (uint32_t j = ip; j > loop_start; j--)
                {
                    if (m_currentCodeBlock->instructions[j].op == OpCode::PUSH_CONST)
                    {
                        int32_t const_idx = m_currentCodeBlock->instructions[j].a;
                        if (const_idx >= 0 && const_idx < (int32_t)m_currentCodeBlock->constants.size())
                        {
                            const Value &v = m_currentCodeBlock->constants[const_idx];
                            if (v.isNumber())
                            {
                                int64_t val = static_cast<int64_t>(v.rawNumber());
                                if (val > 0 && val < 100000000)
                                    return val;
                            }
                        }
                        break;
                    }
                }
                break;
            }
        }
        return -1;
    }
}