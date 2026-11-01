#ifndef VM_LOADER_H
#define VM_LOADER_H

#include "vm_types.h"
#include <fstream>
#include <cstring>
#include <iostream>
#include <cstdio>
#include <string>
#include <vector>

namespace qot
{

    // zlib 兼容 CRC32（IEEE 802.3），用于 .qtc v2 完整性校验（SPEC 7.3-T-32 / 7.4）。
    inline uint32_t qot_crc32(const uint8_t *data, size_t len, uint32_t crc = 0)
    {
        crc = ~crc;
        for (size_t i = 0; i < len; i++)
        {
            crc ^= data[i];
            for (int k = 0; k < 8; k++)
                crc = (crc >> 1) ^ (0xEDB88320U & (0U - (crc & 1)));
        }
        return ~crc;
    }

    // .qtc v2 扩展段（跟在 v1 主体之后、CRC 之前）
    struct QtcExtra
    {
        static const uint32_t FLAG_AGGRESSIVE_OK = 0x1; // T-48：可激进优化（require 全内联、无运行期代码加载）

        uint32_t qtcVersion = 0;
        uint32_t flags = 0;
        std::vector<std::string> builtinHints; // 编译期解析出的内置函数候选名（元数据/诊断用）
        std::vector<uint8_t> lir;              // LIR 段（含版本号），空 = 无（保守路径）
    };


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

        // 解析 .qtc。v1：无 CRC、funcsInfoLen 死字段（跳过）；v2：CRC32 + 扩展段、删死字段。
        // SPEC R6：loader 必须同时接受 v1 与 v2（版本支持区间 [1,2]）。
        static bool load(const std::string &filename, CodeBlock &code, QtcExtra *extra = nullptr)
        {
            std::ifstream file(filename, std::ios::binary);
            if (!file.is_open())
            {
                std::cerr << "[Error] 无法打开文件: " << filename << std::endl;
                return false;
            }
            file.seekg(0, std::ios::end);
            std::streamoff fsize = file.tellg();
            if (fsize < 8)
            {
                std::cerr << "[Error] 文件过短，不是有效的 .qtc: " << filename << std::endl;
                return false;
            }
            file.seekg(0, std::ios::beg);
            std::vector<uint8_t> buf(static_cast<size_t>(fsize));
            file.read(reinterpret_cast<char *>(buf.data()), fsize);
            file.close();
            return load_from_buffer(buf, code, extra);
        }

        static bool load_from_buffer(const std::vector<uint8_t> &buf, CodeBlock &code, QtcExtra *extra)
        {
            size_t off = 0;
            if (buf.size() < 8 || std::memcmp(buf.data(), "QTC", 3) != 0)
            {
                // 注意：loader 的错误消息保持 ASCII——std::cerr 在管道下按 ANSI 代码页输出，
                // 中文会破坏跨进程 UTF-8 解码（R2 错误流契约的隐含要求）。
                std::cerr << "[Error] bad magic: '"
                          << std::string(buf.begin(), buf.begin() + (buf.size() < 4 ? buf.size() : 4))
                          << "'" << std::endl;
                return false;
            }
            uint8_t magicTail = buf[3];
            if (magicTail != 0 && magicTail != 0x20)
            {
                std::cerr << "[Warning] 4字节异常编码" << std::hex << (int)magicTail << std::dec << std::endl;
            }
            off = 4;
            uint32_t version = read_u32(buf, off);
            if (extra)
                extra->qtcVersion = version;
            if (version == 1)
            {
                // v1：funcsInfoLen 死字段（读了就跳过）
                uint32_t funcsInfoLen = read_u32(buf, off);
                if (off + funcsInfoLen > buf.size())
                {
                    std::cerr << "[Error] funcsInfoLen out of range" << std::endl;
                    return false;
                }
                off += funcsInfoLen;
                return parse_body(buf, off, code);
            }
            if (version == 2)
            {
                // v2：末尾 4 字节 CRC32，覆盖其前的全部字节
                if (buf.size() < 4 + 4)
                {
                    std::cerr << "[Error] file too short, CRC missing" << std::endl;
                    return false;
                }
                uint32_t stored = 0;
                std::memcpy(&stored, buf.data() + buf.size() - 4, 4);
                uint32_t calc = qot_crc32(buf.data(), buf.size() - 4);
                if (stored != calc)
                {
                    std::cerr << "[Error] CRC32 check failed (corrupted or tampered file): stored="
                              << std::hex << stored << " calc=" << calc << std::dec << std::endl;
                    return false;
                }
                size_t bodyEnd = buf.size() - 4;
                bool ok = parse_body(buf, off, code, bodyEnd);
                if (!ok)
                    return false;
                if (extra)
                    parse_ext(buf, off, *extra);
                return true;
            }
            std::cerr << "[Error] unsupported .qtc version: " << version
                      << " (supported range: 1..2)" << std::endl;
            return false;
        }

    private:
        static uint32_t read_u32(const std::vector<uint8_t> &b, size_t &off)
        {
            if (off + 4 > b.size())
                return 0;
            uint32_t v;
            std::memcpy(&v, b.data() + off, 4);
            off += 4;
            return v;
        }

        // v1/v2 共用的主体：instCount | [op,argLen,arg]* | consts | vars | funcs | source
        static bool parse_body(const std::vector<uint8_t> &buf, size_t &off, CodeBlock &code, size_t hardEnd = static_cast<size_t>(-1))
        {
            size_t end = (hardEnd == static_cast<size_t>(-1)) ? buf.size() : hardEnd;
            code.instructions.clear();
            code.blobs.clear();
            code.constants.clear();
            code.varNames.clear();
            code.funcNames.clear();

            if (off + 4 > end)
                return false;
            uint32_t instCount = read_u32(buf, off);
            code.instructions.reserve(instCount);
            for (uint32_t i = 0; i < instCount; i++)
            {
                if (off + 5 > end)
                    return false;
                uint8_t op = buf[off++];
                uint32_t argLen = read_u32(buf, off);
                if (off + argLen > end)
                    return false;
                code.instructions.push_back(makeInstr(op, buf.data() + off, argLen, code));
                off += argLen;
            }
            if (off + 4 > end)
                return false;
            uint32_t constsLen = read_u32(buf, off);
            if (off + constsLen > end)
                return false;
            std::vector<uint8_t> constsData(buf.begin() + off, buf.begin() + off + constsLen);
            off += constsLen;
            if (!deserializeConstants(constsData, code.constants))
            {
                std::cerr << "[Error] 无法解析表达式" << std::endl;
                return false;
            }
            if (off + 4 > end)
                return false;
            uint32_t varsLen = read_u32(buf, off);
            if (off + varsLen > end)
                return false;
            std::vector<uint8_t> varsData(buf.begin() + off, buf.begin() + off + varsLen);
            off += varsLen;
            if (!deserializeStringList(varsData, code.varNames))
            {
                std::cerr << "[Error] 无法解析变量名" << std::endl;
                return false;
            }
            if (off + 4 > end)
                return false;
            uint32_t funcsLen = read_u32(buf, off);
            if (off + funcsLen > end)
                return false;
            std::vector<uint8_t> funcsData(buf.begin() + off, buf.begin() + off + funcsLen);
            off += funcsLen;
            if (!deserializeStringList(funcsData, code.funcNames))
            {
                std::cerr << "[Error] 无法解析函数名" << std::endl;
                return false;
            }
            if (off + 4 > end)
                return false;
            uint32_t sourceLen = read_u32(buf, off);
            if (off + sourceLen > end)
                return false;
            code.sourceFile = std::string(buf.begin() + off, buf.begin() + off + sourceLen);
            off += sourceLen;
            return true;
        }

        // v2 扩展段：flags u32 | builtinHints(string-list) | lirLen u32 | lir
        static void parse_ext(const std::vector<uint8_t> &buf, size_t off, QtcExtra &extra)
        {
            extra.flags = 0;
            extra.builtinHints.clear();
            extra.lir.clear();
            if (off + 4 > buf.size())
                return;
            extra.flags = read_u32(buf, off);
            if (off + 4 > buf.size())
                return;
            uint32_t hintsLen = read_u32(buf, off);
            if (off + hintsLen > buf.size())
                return;
            std::vector<uint8_t> hintsData(buf.begin() + off, buf.begin() + off + hintsLen);
            off += hintsLen;
            deserializeStringList(hintsData, extra.builtinHints);
            if (off + 4 > buf.size())
                return;
            uint32_t lirLen = read_u32(buf, off);
            if (off + lirLen > buf.size())
                return;
            extra.lir.assign(buf.begin() + off, buf.begin() + off + lirLen);
        }

    public:


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