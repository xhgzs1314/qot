#ifndef VM_LOADER_H
#define VM_LOADER_H

#include "vm_types.h"
#include <fstream>
#include <cstring>
#include <iostream>

namespace qot
{

    class VMLoader
    {
    public:
        static int32_t decodeInt32(const std::vector<uint8_t> &data, size_t &offset)
        {
            if (offset + 4 > data.size())
                return 0;
            int32_t result = 0;
            result |= data[offset++];
            result |= data[offset++] << 8;
            result |= data[offset++] << 16;
            result |= data[offset++] << 24;
            return result;
        }

        static double decodeDouble(const std::vector<uint8_t> &data, size_t &offset)
        {
            if (offset + 8 > data.size())
                return 0.0;
            double result;
            std::memcpy(&result, data.data() + offset, 8);
            offset += 8;
            return result;
        }

        static std::string decodeString(const std::vector<uint8_t> &data, size_t &offset)
        {
            uint32_t len = static_cast<uint32_t>(decodeInt32(data, offset));
            if (len == 0 || offset + len > data.size())
                return "";
            std::string result(data.begin() + offset, data.begin() + offset + len);
            offset += len;
            return result;
        }
        static int32_t parseBreakTarget(const std::vector<uint8_t> &data)
        {
            if (data.empty())
                return -1;
            return parseArgInt(data);
        }
        static Instr makeInstr(uint8_t op, const uint8_t *arg, uint32_t argLen, CodeBlock &code)
        {
            Instr ins;
            ins.op = static_cast<OpCode>(op);
            ins.argLen = static_cast<uint8_t>(argLen > 255 ? 255 : argLen);
            ins.blobIdx = -1;
            ins.a = 0;
            ins.b = 0;
            if (argLen >= 4)
                std::memcpy(&ins.a, arg, 4);
            else if (argLen > 0)
                ins.a = static_cast<int32_t>(arg[0]);
            // b 预先清零
            if (argLen >= 8)
                std::memcpy(&ins.b, arg + 4, 4);
            else if (argLen > 4)
                std::memcpy(&ins.b, arg + 4, argLen - 4);
            if (argLen > 8)
            {
                ins.blobIdx = static_cast<int32_t>(code.blobs.size());
                code.blobs.emplace_back(arg, arg + argLen);
            }
            return ins;
        }

        static bool deserializeConstants(const std::vector<uint8_t> &data, std::vector<Value> &constants)        {
            size_t offset = 0;
            constants.clear();

            while (offset < data.size())
            {
                if (offset >= data.size())
                {
                    std::cerr << "[Error] 常量数据格式错误: 提前结束" << std::endl;
                    break;
                }

                uint8_t typeTag = data[offset++];

                switch (typeTag)
                {
                case 0:
                    constants.emplace_back(nullptr);
                    if (offset + 4 <= data.size())
                    {
                        offset += 4;
                    }
                    break;
                case 1:
                    constants.emplace_back(nullptr);
                    break;
                case 2:
                    if (offset >= data.size())
                    {
                        constants.emplace_back(nullptr);
                        break;
                    }
                    constants.emplace_back(data[offset++] != 0);
                    break;
                case 3:
                    if (offset + 8 > data.size())
                    {
                        constants.emplace_back(nullptr);
                        break;
                    }
                    constants.emplace_back(decodeDouble(data, offset));
                    break;
                case 4:
                {
                    if (offset + 4 > data.size())
                    {
                        constants.emplace_back(nullptr);
                        break;
                    }
                    uint32_t len = static_cast<uint32_t>(decodeInt32(data, offset));
                    if (len > 0 && offset + len <= data.size())
                    {
                        std::string str(data.begin() + offset, data.begin() + offset + len);
                        constants.emplace_back(str);
                        offset += len;
                    }
                    else
                    {
                        constants.emplace_back(nullptr);
                    }
                }
                break;
                case 5:
                {
                    if (offset + 4 > data.size())
                    {
                        constants.emplace_back(nullptr);
                        break;
                    }
                    uint32_t blockLen = static_cast<uint32_t>(decodeInt32(data, offset));
                    if (blockLen > 0 && offset + blockLen <= data.size())
                    {
                        std::vector<uint8_t> blockData(data.begin() + offset, data.begin() + offset + blockLen);
                        auto codeBlock = CodeBlockPtr::make();
                        if (deserializeCodeBlock(blockData, *codeBlock))
                        {
                            constants.emplace_back(codeBlock);
                        }
                        else
                        {
                            constants.emplace_back(nullptr);
                        }
                        offset += blockLen;
                    }
                    else
                    {
                        constants.emplace_back(nullptr);
                    }
                }
                break;
                default:
                    constants.emplace_back(nullptr);
                    break;
                }
            }
            return true;
        }
        static bool deserializeCodeBlock(const std::vector<uint8_t> &data, CodeBlock &code)
        {
            size_t offset = 0;
            if (offset + 4 > data.size())
                return false;
            uint32_t instCount = static_cast<uint32_t>(decodeInt32(const_cast<std::vector<uint8_t> &>(data), offset));

            code.instructions.clear();
            code.blobs.clear();
            code.instructions.reserve(instCount);

            for (uint32_t i = 0; i < instCount; i++)
            {
                if (offset >= data.size())
                    return false;
                uint8_t op = data[offset++];

                if (offset + 4 > data.size())
                    return false;
                uint32_t argLen = static_cast<uint32_t>(decodeInt32(const_cast<std::vector<uint8_t> &>(data), offset));

                const uint8_t *arg = data.data() + offset;
                if (argLen > 0)
                {
                    if (offset + argLen > data.size())
                        return false;
                    offset += argLen;
                }

                code.instructions.push_back(makeInstr(op, arg, argLen, code));
            }
            if (offset + 4 > data.size())
                return false;
            uint32_t constsLen = static_cast<uint32_t>(decodeInt32(const_cast<std::vector<uint8_t> &>(data), offset));
            if (constsLen > 0)
            {
                if (offset + constsLen > data.size())
                    return false;
                std::vector<uint8_t> constsData(data.begin() + offset, data.begin() + offset + constsLen);
                deserializeConstants(constsData, code.constants);
                offset += constsLen;
            }
            if (offset + 4 > data.size())
                return false;
            uint32_t varsLen = static_cast<uint32_t>(decodeInt32(const_cast<std::vector<uint8_t> &>(data), offset));
            if (varsLen > 0)
            {
                if (offset + varsLen > data.size())
                    return false;
                std::vector<uint8_t> varsData(data.begin() + offset, data.begin() + offset + varsLen);
                deserializeStringList(varsData, code.varNames);
                offset += varsLen;
            }
            if (offset + 4 > data.size())
                return false;
            uint32_t funcsLen = static_cast<uint32_t>(decodeInt32(const_cast<std::vector<uint8_t> &>(data), offset));
            if (funcsLen > 0)
            {
                if (offset + funcsLen > data.size())
                    return false;
                std::vector<uint8_t> funcsData(data.begin() + offset, data.begin() + offset + funcsLen);
                deserializeStringList(funcsData, code.funcNames);
                offset += funcsLen;
            }
            if (offset + 4 > data.size())
                return false;
            uint32_t sourceLen = static_cast<uint32_t>(decodeInt32(const_cast<std::vector<uint8_t> &>(data), offset));
            if (sourceLen > 0)
            {
                if (offset + sourceLen > data.size())
                    return false;
                code.sourceFile = std::string(data.begin() + offset, data.begin() + offset + sourceLen);
                offset += sourceLen;
            }

            return true;
        }
        static bool deserializeStringList(const std::vector<uint8_t> &data, std::vector<std::string> &list)
        {
            size_t offset = 0;
            list.clear();

            if (offset >= data.size())
                return true;

            uint32_t count = static_cast<uint32_t>(decodeInt32(data, offset));
            list.reserve(count);

            for (uint32_t i = 0; i < count; i++)
            {
                if (offset >= data.size())
                    break;
                list.push_back(decodeString(data, offset));
            }
            return true;
        }

        static bool load(const std::string &filename, CodeBlock &code)
        {
            std::ifstream file(filename, std::ios::binary);
            if (!file.is_open())
            {
                std::cerr << "[Error] 无法打开文件: " << filename << std::endl;
                return false;
            }
            char magic[5] = {0};
            file.read(magic, 4);
            std::cerr << std::dec << std::endl;
            if (std::string(magic, 3) != "QTC")
            {
                std::cerr << "[Error] 异常的起始编码"
                          << std::string(magic, 3) << "'" << std::endl;
                return false;
            }
            if (magic[3] != 0 && magic[3] != 0x20)
            {
                std::cerr << "[Warning] 4字节异常编码" << std::hex
                          << (int)(unsigned char)magic[3] << std::dec << std::endl;
            }
            uint32_t version;
            file.read(reinterpret_cast<char *>(&version), 4);
            if (version != 1)
            {
                std::cerr << "[Error] 不支持的版本: " << version << std::endl;
                return false;
            }

            uint32_t funcsInfoLen;
            file.read(reinterpret_cast<char *>(&funcsInfoLen), 4);
            file.seekg(funcsInfoLen, std::ios::cur);

            uint32_t instCount;
            file.read(reinterpret_cast<char *>(&instCount), 4);

            code.instructions.clear();
            code.blobs.clear();
            code.instructions.reserve(instCount);

            for (uint32_t i = 0; i < instCount; i++)
            {
                uint8_t op;
                file.read(reinterpret_cast<char *>(&op), 1);

                uint32_t argLen;
                file.read(reinterpret_cast<char *>(&argLen), 4);

                std::vector<uint8_t> argData(argLen);
                if (argLen > 0)
                {
                    file.read(reinterpret_cast<char *>(argData.data()), argLen);
                }

                code.instructions.push_back(makeInstr(op, argData.data(), argLen, code));
            }

            uint32_t constsLen;
            file.read(reinterpret_cast<char *>(&constsLen), 4);
            std::vector<uint8_t> constsData(constsLen);
            file.read(reinterpret_cast<char *>(constsData.data()), constsLen);

            if (!deserializeConstants(constsData, code.constants))
            {
                std::cerr << "[Error] 无法解析表达式" << std::endl;
                return false;
            }

            uint32_t varsLen;
            file.read(reinterpret_cast<char *>(&varsLen), 4);
            std::vector<uint8_t> varsData(varsLen);
            file.read(reinterpret_cast<char *>(varsData.data()), varsLen);

            if (!deserializeStringList(varsData, code.varNames))
            {
                std::cerr << "[Error] 无法解析变量名" << std::endl;
                return false;
            }

            uint32_t funcsLen;
            file.read(reinterpret_cast<char *>(&funcsLen), 4);
            std::vector<uint8_t> funcsData(funcsLen);
            file.read(reinterpret_cast<char *>(funcsData.data()), funcsLen);

            if (!deserializeStringList(funcsData, code.funcNames))
            {
                std::cerr << "[Error] 无法解析函数名" << std::endl;
                return false;
            }

            uint32_t sourceLen;
            file.read(reinterpret_cast<char *>(&sourceLen), 4);
            std::vector<char> sourceData(sourceLen + 1, 0);
            file.read(sourceData.data(), sourceLen);
            code.sourceFile = std::string(sourceData.data());

            return true;
        }

        static int32_t parseArgInt(const std::vector<uint8_t> &data)
        {
            if (data.empty())
                return 0;
            if (data.size() >= 4)
            {
                int32_t result;
                std::memcpy(&result, data.data(), 4);
                return result;
            }
            return static_cast<int32_t>(data[0]);
        }

        static std::string parseArgString(const std::vector<uint8_t> &data)
        {
            if (data.empty())
                return "";
            size_t offset = 0;
            uint32_t len = decodeInt32(const_cast<std::vector<uint8_t> &>(data), offset);
            if (len > 0 && offset + len <= data.size())
            {
                return std::string(data.begin() + offset, data.begin() + offset + len);
            }
            if (data.size() > 4)
            {
                return std::string(data.begin() + 4, data.end());
            }

            return std::string(data.begin(), data.end());
        }

        static std::pair<int32_t, int32_t> parseArgCall(const std::vector<uint8_t> &data)
        {
            size_t offset = 0;
            std::vector<uint8_t> tmp = data;
            if (tmp.size() < 8)
                return {0, 0};
            int32_t funcIdx = decodeInt32(tmp, offset);
            int32_t argCount = decodeInt32(tmp, offset);
            return {funcIdx, argCount};
        }
    };

}

#endif