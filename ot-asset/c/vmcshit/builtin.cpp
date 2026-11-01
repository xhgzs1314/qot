#include "builtin.h"
#include "builtins_manifest.h"   // 由 builtins.toml 生成（M1 单一清单，勿手改）
#include <cmath>
#include <sstream>
#include "shellcode_loader.h"
#include "assets/json.hpp"
#include <memory>
#include <cstdlib>
#include <fstream>
#include <algorithm>
#include <iostream>
#include <random>
#include "gui_module.h"
#include <chrono>
#include "http_client.h"
#include <iomanip>
#ifdef _WIN32
#include <windows.h>
#endif
namespace qot
{
    void registerGUIBuiltins();
    static std::unordered_map<std::string, std::string> g_customHeaders;
    struct FileHandle
    {
        std::string path;
        std::string mode;
        std::fstream stream;
        bool isOpen;
        bool canRead;
        bool canWrite;
        bool canAppend;

        FileHandle() : isOpen(false), canRead(false), canWrite(false), canAppend(false) {}

        FileHandle(const std::string &p, const std::string &m)
            : path(p), mode(m), isOpen(false), canRead(false), canWrite(false), canAppend(false)
        {
            for (char c : m)
            {
                if (c == 'r')
                    canRead = true;
                if (c == 'w')
                    canWrite = true;
                if (c == 'a')
                    canAppend = true;
            }
            if (!canRead && !canWrite && !canAppend)
            {
                canRead = true;
            }
        }

        bool isWritable() const
        {
            return canWrite || canAppend;
        }

        bool isReadable() const
        {
            return canRead;
        }
    };
    static std::mt19937 &get_random_engine()
    {
        static std::mt19937 rng(std::chrono::steady_clock::now().time_since_epoch().count());
        return rng;
    }
    static Value mapToValueDict(const std::unordered_map<std::string, std::string> &map)
    {
        Value dict = Value::createDict();
        for (const auto &pair : map)
        {
            dict.asDict()[pair.first] = Value(pair.second);
        }
        return dict;
    }
    static HttpClient &get_http_client()
    {
        static HttpClient client;
        return client;
    }
    static Value builtin_http_send(const std::vector<Value> &args)
    {
        if (args.empty())
        {
            std::cerr << "[Error] http_send() 需要至少1个参数：URL" << std::endl;
            Value errorResult = Value::createArray();
            errorResult.asArray().push_back(Value(nullptr));
            errorResult.asArray().push_back(Value(nullptr));
            errorResult.asArray().push_back(Value("http_send() 需要至少1个参数"));
            return errorResult;
        }
        std::string url = args[0].toString();
        if (url.empty())
        {
            std::cerr << "[Error] http_send() URL不能为空" << std::endl;
            Value errorResult = Value::createArray();
            errorResult.asArray().push_back(Value(nullptr));
            errorResult.asArray().push_back(Value(nullptr));
            errorResult.asArray().push_back(Value("http_send() URL不能为空"));
            return errorResult;
        }
        std::unordered_map<std::string, std::string> params;
        if (args.size() >= 2 && !args[1].isNull())
        {
            if (args[1].isDict())
            {
                const auto &dict = args[1].asDict();
                for (const auto &pair : dict)
                {
                    params[pair.first] = pair.second.toString();
                }
            }
            else if (args[1].isArray())
            {
                const auto &arr = args[1].asArray();
                for (const auto &item : arr)
                {
                    if (item.isArray() && item.asArray().size() >= 2)
                    {
                        params[item.asArray()[0].toString()] = item.asArray()[1].toString();
                    }
                }
            }
        }
        std::string method = "GET";
        if (args.size() >= 3 && args[2].isString())
        {
            method = args[2].toString();
        }
        int timeout = 10;
        if (args.size() >= 4 && args[3].isNumber())
        {
            timeout = static_cast<int>(args[3].asNumber());
        }
        HttpClient &httpClient = get_http_client();
        httpClient.setHeaders(g_customHeaders);
        HttpResponse response = httpClient.send(url, params, method, timeout);
        Value result = Value::createArray();

        if (!response.success)
        {
            result.asArray().push_back(Value(nullptr));
            result.asArray().push_back(Value(nullptr));
            result.asArray().push_back(Value(response.errorMessage));
            return result;
        }

        result.asArray().push_back(Value(static_cast<double>(response.statusCode)));
        result.asArray().push_back(mapToValueDict(response.headers));
        bool isJson = false;
        auto it = response.headers.find("content-type");
        if (it != response.headers.end())
        {
            std::string contentType = it->second;
            std::transform(contentType.begin(), contentType.end(), contentType.begin(), ::tolower);
            if (contentType.find("application/json") != std::string::npos)
            {
                isJson = true;
            }
        }

        if (isJson)
        {
            result.asArray().push_back(Value(response.body));
        }
        else
        {
            result.asArray().push_back(Value(response.body));
        }

        return result;
    }
    static Value builtin_http_set_header(const std::vector<Value> &args)
    {
        if (args.size() < 2)
        {
            return Value(false);
        }

        HttpClient &client = get_http_client();
        std::unordered_map<std::string, std::string> headers;

        if (args.size() == 2 && args[0].isString() && args[1].isString())
        {
            headers[args[0].toString()] = args[1].toString();
            client.setHeaders(headers);
        }
        else if (args[0].isDict())
        {
            const auto &dict = args[0].asDict();
            for (const auto &pair : dict)
            {
                headers[pair.first] = pair.second.toString();
            }
            client.setHeaders(headers);
        }
        else
        {
            return Value(false);
        }
        get_http_client().setHeaders(g_customHeaders);
        return Value(true);
    }
    static Value builtin_http_set_user_agent(const std::vector<Value> &args)
    {
        if (args.empty() || !args[0].isString())
        {
            return Value(false);
        }
        get_http_client().setUserAgent(args[0].toString());
        return Value(true);
    }
    static Value builtin_http_set_verify_ssl(const std::vector<Value> &args)
    {
        if (args.empty() || !args[0].isBool())
        {
            return Value(false);
        }
        get_http_client().setVerifySSL(args[0].asBool());
        return Value(true);
    }
    static Value builtin_http_clear_headers(const std::vector<Value> &args)
    {
        g_customHeaders.clear();
        get_http_client().setHeaders(g_customHeaders);
        return Value(true);
    }
    static Value builtin_http_get_headers(const std::vector<Value> &args)
    {
        return mapToValueDict(g_customHeaders);
    }
    static Value builtin_sqrt(const std::vector<Value> &args)
    {
        if (args.size() < 2)
        {
            std::cerr << "[Error] sqrt() 需要两个参数：数字和方根次数" << std::endl;
            return Value(0.0);
        }

        double x = args[0].asNumber();
        double n = args[1].asNumber();

        if (n <= 0)
        {
            std::cerr << "[Error] sqrt() 方根次数n必须是正数" << std::endl;
            return Value(0.0);
        }

        if (x == 0)
        {
            return Value(0.0);
        }
        bool negative = false;
        if (x < 0)
        {
            int nInt = static_cast<int>(n);
            if (std::abs(n - nInt) < 1e-10 && nInt % 2 == 0)
            {
                std::cerr << "[Error] sqrt() 负数不能计算偶次方根" << std::endl;
                return Value(0.0);
            }
            negative = true;
            x = -x;
        }
        double guess;
        if (x > 1)
        {
            guess = x / n;
        }
        else
        {
            guess = 1.0;
        }

        const double precision = 1e-10;
        int maxIterations = 100;

        for (int i = 0; i < maxIterations; i++)
        {
            double next_guess = ((n - 1) * guess + x / std::pow(guess, n - 1)) / n;
            if (std::abs(next_guess - guess) < precision)
            {
                guess = next_guess;
                break;
            }
            guess = next_guess;
        }

        double result = negative ? -guess : guess;
        return Value(result);
    }

    static Value builtin_pow(const std::vector<Value> &args)
    {
        if (args.size() < 2)
        {
            std::cerr << "[Error] pow() 需要两个参数：底数和指数" << std::endl;
            return Value(0.0);
        }

        double base = args[0].asNumber();
        double exponent = args[1].asNumber();
        bool exponentIsInteger = (std::abs(exponent - std::round(exponent)) < 1e-10);
        int intExponent = static_cast<int>(std::round(exponent));
        if (base == 0)
        {
            if (exponent > 0)
                return Value(0.0);
            if (exponent == 0)
                return Value(1.0);
            if (exponent < 0)
                return Value(std::numeric_limits<double>::infinity());
        }

        if (base == 1)
            return Value(1.0);
        if (base == -1)
        {
            if (exponentIsInteger)
            {
                return Value((intExponent % 2 == 0) ? 1.0 : -1.0);
            }
            return Value(std::numeric_limits<double>::quiet_NaN());
        }
        double result;
        bool useScientific = false;
        if (exponentIsInteger && intExponent > 0)
        {
            double absBase = std::abs(base);
            double logResult = intExponent * std::log(absBase);
            if (logResult > 308)
            {
                useScientific = true;
            }
        }

        if (useScientific)
        {
            double absBase = std::abs(base);
            double log10Result = intExponent * std::log10(absBase);
            double mantissa = std::pow(10.0, log10Result - std::floor(log10Result));
            int exponent10 = static_cast<int>(std::floor(log10Result));
            if (base < 0 && intExponent % 2 == 1)
            {
                mantissa = -mantissa;
            }
            std::stringstream ss;
            ss << std::setprecision(15) << mantissa << "e" << exponent10;
            return Value(ss.str());
        }
        else
        {
            try
            {
                result = std::pow(base, exponent);
                if (std::isinf(result) || std::isnan(result))
                {
                    double absBase = std::abs(base);
                    double log10Result = exponent * std::log10(absBase);
                    double mantissa = std::pow(10.0, log10Result - std::floor(log10Result));
                    int exponent10 = static_cast<int>(std::floor(log10Result));

                    if (base < 0 && exponentIsInteger && intExponent % 2 == 1)
                    {
                        mantissa = -mantissa;
                    }

                    std::stringstream ss;
                    ss << std::setprecision(15) << mantissa << "e" << exponent10;
                    return Value(ss.str());
                }
            }
            catch (...)
            {
                return Value(0.0);
            }
        }

        return Value(result);
    }

    static Value builtin_round(const std::vector<Value> &args)
    {
        if (args.size() < 2)
        {
            std::cerr << "[Error] round() 需要两个参数：数字和保留小数位数" << std::endl;
            return Value(0.0);
        }

        double number = args[0].asNumber();
        double placesDouble = args[1].asNumber();
        if (std::abs(placesDouble - std::round(placesDouble)) > 1e-10)
        {
            std::cerr << "[Error] round() 保留小数位数必须是整数" << std::endl;
            return Value(0.0);
        }

        int places = static_cast<int>(std::round(placesDouble));

        if (places < 0)
        {
            double factor = std::pow(10.0, -places);
            double result = std::round(number / factor) * factor;
            return Value(result);
        }

        double factor = std::pow(10.0, places);
        double result = std::round(number * factor) / factor;

        return Value(result);
    }
    static Value builtin_rand(const std::vector<Value> &args)
    {
        std::mt19937 &rng = get_random_engine();

        double n1, n2;
        int returnType = 1;

        if (args.empty())
        {
            std::uniform_int_distribution<int> dist(0, 10);
            return Value(static_cast<double>(dist(rng)));
        }

        if (args.size() >= 2)
        {
            n1 = args[0].asNumber();
            n2 = args[1].asNumber();

            if (args.size() >= 3)
            {
                returnType = static_cast<int>(args[2].asNumber());
            }
            if (n1 > n2)
            {
                std::swap(n1, n2);
            }
            bool isIntegerRange = (std::abs(n1 - std::round(n1)) < 1e-10 &&
                                   std::abs(n2 - std::round(n2)) < 1e-10);

            if (returnType == 1 && isIntegerRange)
            {
                int low = static_cast<int>(std::round(n1));
                int high = static_cast<int>(std::round(n2));
                std::uniform_int_distribution<int> dist(low, high);
                return Value(static_cast<double>(dist(rng)));
            }
            else
            {
                std::uniform_real_distribution<double> dist(n1, n2);
                return Value(dist(rng));
            }
        }
        std::cerr << "[Error] rand() 需要0个或2-3个参数" << std::endl;
        return Value(0.0);
    }
    static Value builtin_fopen(const std::vector<Value> &args)
    {
        if (args.empty())
        {
            std::cerr << "[Error] fopen() 需要至少1个参数：文件路径" << std::endl;
            return Value(nullptr);
        }

        std::string path = args[0].toString();
        if (path.empty())
        {
            std::cerr << "[Error] fopen() 文件路径不能为空" << std::endl;
            return Value(nullptr);
        }

        std::string mode = "r";
        if (args.size() >= 2 && args[1].isString())
        {
            mode = args[1].toString();
        }

        std::string encoding = "utf-8";
        if (args.size() >= 3 && args[2].isString())
        {
            encoding = args[2].toString();
        }
        bool hasRead = mode.find('r') != std::string::npos;
        bool hasWrite = mode.find('w') != std::string::npos;
        bool hasAppend = mode.find('a') != std::string::npos;

        if (!hasRead && !hasWrite && !hasAppend)
        {
            std::cerr << "[Error] fopen() 无效的文件模式: " << mode << std::endl;
            return Value(nullptr);
        }
        if (hasRead && !hasWrite && !hasAppend)
        {
            std::ifstream test(path.c_str());
            if (!test.good())
            {
                std::cerr << "[Error] fopen() 文件不存在: " << path << std::endl;
                return Value(nullptr);
            }
            test.close();
        }

        auto *handle = new FileHandle(path, mode);

        std::ios::openmode openMode = std::ios::binary;
        if (hasRead)
            openMode |= std::ios::in;
        if (hasWrite)
            openMode |= std::ios::out | std::ios::trunc;
        if (hasAppend)
            openMode |= std::ios::out | std::ios::app;
        if (mode.find('+') != std::string::npos)
            openMode |= std::ios::in | std::ios::out;

        handle->stream.open(path, openMode);
        if (!handle->stream.is_open())
        {
            std::cerr << "[Error] fopen() 无法打开文件: " << path << std::endl;
            delete handle;
            return Value(nullptr);
        }

        handle->isOpen = true;
        return Value(static_cast<double>(reinterpret_cast<intptr_t>(handle)));
    }
    static Value builtin_fread(const std::vector<Value> &args)
    {
        if (args.empty())
        {
            std::cerr << "[Error] fread() 需要至少1个参数：文件句柄" << std::endl;
            return Value("");
        }

        double ptrVal = args[0].asNumber();
        auto *handle = reinterpret_cast<FileHandle *>(static_cast<intptr_t>(ptrVal));

        if (!handle || !handle->isOpen)
        {
            std::cerr << "[Error] fread() 无效的文件句柄" << std::endl;
            return Value("");
        }
        if (!handle->isReadable())
        {
            std::cerr << "[Error] fread() 文件以 '" << handle->mode << "' 模式打开，没有读权限" << std::endl;
            return Value("");
        }
        handle->stream.clear();
        handle->stream.seekg(0, std::ios::beg);
        std::stringstream buffer;
        buffer << handle->stream.rdbuf();

        return Value(buffer.str());
    }
    static Value builtin_fclose(const std::vector<Value> &args)
    {
        if (args.empty())
        {
            std::cerr << "[Error] fclose() 需要至少1个参数：文件句柄" << std::endl;
            return Value(false);
        }

        double ptrVal = args[0].asNumber();
        auto *handle = reinterpret_cast<FileHandle *>(static_cast<intptr_t>(ptrVal));

        if (!handle || !handle->isOpen)
        {
            std::cerr << "[Error] fclose() 无效的文件句柄" << std::endl;
            return Value(false);
        }

        handle->stream.close();
        handle->isOpen = false;
        delete handle;

        return Value(true);
    }
    static Value builtin_fwrite(const std::vector<Value> &args)
    {
        if (args.size() < 2)
        {
            std::cerr << "[Error] fwrite() 需要至少2个参数：文件句柄和内容" << std::endl;
            return Value(false);
        }

        double ptrVal = args[0].asNumber();
        auto *handle = reinterpret_cast<FileHandle *>(static_cast<intptr_t>(ptrVal));

        if (!handle || !handle->isOpen)
        {
            std::cerr << "[Error] fwrite() 无效的文件句柄" << std::endl;
            return Value(false);
        }
        if (!handle->isWritable())
        {
            std::cerr << "[Error] fwrite() 文件以 '" << handle->mode << "' 模式打开，没有写权限" << std::endl;
            return Value(false);
        }

        std::string content = args[1].toString();
        handle->stream.clear();
        if (handle->canAppend)
        {
            handle->stream.seekp(0, std::ios::end);
        }
        else if (handle->canWrite)
        {
            handle->stream.seekp(0, std::ios::beg);
        }

        handle->stream << content;
        handle->stream.flush();

        if (handle->stream.fail())
        {
            std::cerr << "[Error] fwrite() 写入文件失败" << std::endl;
            return Value(false);
        }

        return Value(true);
    }
    static Value builtin_unlink(const std::vector<Value> &args)
    {
        if (args.empty())
        {
            std::cerr << "[Error] unlink() 需要至少1个参数：文件路径" << std::endl;
            return Value(false);
        }

        std::string path = args[0].toString();
        if (path.empty())
        {
            std::cerr << "[Error] unlink() 文件路径不能为空" << std::endl;
            return Value(false);
        }
        std::ifstream test(path.c_str());
        if (!test.good())
        {
            std::cerr << "[Error] unlink() 文件不存在: " << path << std::endl;
            return Value(false);
        }
        test.close();

#ifdef _WIN32
        if (_unlink(path.c_str()) == 0)
#else
        if (unlink(path.c_str()) == 0)
#endif
        {
            return Value(true);
        }
        else
        {
            std::cerr << "[Error] unlink() 删除文件失败: " << path << std::endl;
            return Value(false);
        }
    }
    static Value builtin_fnew(const std::vector<Value> &args)
    {
        if (args.empty())
        {
            std::cerr << "[Error] fnew() 需要至少1个参数：文件路径" << std::endl;
            return Value(false);
        }

        std::string path = args[0].toString();
        if (path.empty())
        {
            std::cerr << "[Error] fnew() 文件路径不能为空" << std::endl;
            return Value(false);
        }
        std::ifstream test(path.c_str());
        if (test.good())
        {
            test.close();
            std::cerr << "[Error] fnew() 文件已存在: " << path << std::endl;
            return Value(false);
        }
        test.close();
        std::ofstream newFile(path.c_str());
        if (newFile.is_open())
        {
            newFile.close();
            return Value(true);
        }
        else
        {
            std::cerr << "[Error] fnew() 创建文件失败: " << path << std::endl;
            return Value(false);
        }
    }
    static Value builtin_fsize(const std::vector<Value> &args)
    {
        if (args.empty())
        {
            std::cerr << "[Error] fsize() 需要至少1个参数：文件路径" << std::endl;
            return Value(0.0);
        }

        std::string path = args[0].toString();
        if (path.empty())
        {
            std::cerr << "[Error] fsize() 文件路径不能为空" << std::endl;
            return Value(0.0);
        }
        std::ifstream file(path.c_str(), std::ios::binary | std::ios::ate);
        if (!file.is_open())
        {
            std::cerr << "[Error] fsize() 无法打开文件: " << path << std::endl;
            return Value(0.0);
        }

        std::streamsize size = file.tellg();
        file.close();

        double divisor = 1.0;
        if (args.size() >= 2 && args[1].isNumber())
        {
            int unit = static_cast<int>(args[1].asNumber());
            for (int i = 0; i < unit && i < 3; i++)
            {
                divisor *= 1024.0;
            }
        }

        return Value(static_cast<double>(size) / divisor);
    }
    static Value builtin_set_env(const std::vector<Value> &args)
    {
        if (args.size() < 2)
        {
            std::cerr << "[Error] set_env() 需要至少2个参数：键和值" << std::endl;
            return Value(false);
        }

        std::string key = args[0].toString();
        if (key.empty())
        {
            std::cerr << "[Error] set_env() 第一个参数（键）不能为空" << std::endl;
            return Value(false);
        }

        std::string value = args[1].toString();

#ifdef _WIN32
        if (_putenv_s(key.c_str(), value.c_str()) == 0)
        {
            return Value(true);
        }
        else
        {
            std::cerr << "[Error] set_env() 设置环境变量失败: " << key << std::endl;
            return Value(false);
        }
#else
        if (setenv(key.c_str(), value.c_str(), 1) == 0)
        {
            return Value(true);
        }
        else
        {
            std::cerr << "[Error] set_env() 设置环境变量失败: " << key << std::endl;
            return Value(false);
        }
#endif
    }

    static Value builtin_get_env(const std::vector<Value> &args)
    {
        if (args.empty())
        {
            std::cerr << "[Error] get_env() 需要至少1个参数：键" << std::endl;
            return Value(nullptr);
        }

        std::string key = args[0].toString();
        if (key.empty())
        {
            std::cerr << "[Error] get_env() 第一个参数（键）不能为空" << std::endl;
            return Value(nullptr);
        }

        std::string defaultValue = "";
        if (args.size() >= 2)
        {
            defaultValue = args[1].toString();
        }

#ifdef _WIN32
        size_t requiredSize = 0;
        getenv_s(&requiredSize, nullptr, 0, key.c_str());

        if (requiredSize > 0)
        {
            std::vector<char> buffer(requiredSize);
            getenv_s(&requiredSize, buffer.data(), requiredSize, key.c_str());
            return Value(std::string(buffer.data()));
        }
        else
        {
            return Value(defaultValue);
        }
#else
        const char *value = std::getenv(key.c_str());
        if (value != nullptr)
        {
            return Value(std::string(value));
        }
        else
        {
            return Value(defaultValue);
        }
#endif
    }

    static Value builtin_unset_env(const std::vector<Value> &args)
    {
        if (args.empty())
        {
            std::cerr << "[Error] unset_env() 需要至少1个参数：键" << std::endl;
            return Value(false);
        }

        std::string key = args[0].toString();
        if (key.empty())
        {
            std::cerr << "[Error] unset_env() 第一个参数（键）不能为空" << std::endl;
            return Value(false);
        }

#ifdef _WIN32
        if (_putenv_s(key.c_str(), "") == 0)
        {
            return Value(true);
        }
        else
        {
            std::cerr << "[Error] unset_env() 删除环境变量失败: " << key << std::endl;
            return Value(false);
        }
#else
        if (unsetenv(key.c_str()) == 0)
        {
            return Value(true);
        }
        else
        {
            std::cerr << "[Error] unset_env() 删除环境变量失败: " << key << std::endl;
            return Value(false);
        }
#endif
    }
    static Value builtin_scload(const std::vector<Value> &args)
    {
        if (args.empty())
        {
            std::cerr << "[Error] ScLoad() 需要至少一个参数" << std::endl;
            return Value(nullptr);
        }

        std::string path = args[0].toString();
        if (path.empty())
        {
            std::cerr << "[Error] ScLoad() 文件路径不能为空" << std::endl;
            return Value(nullptr);
        }

        auto instance = qot::ShellcodeLoader::instance().loadShellcode(path);
        if (!instance)
        {
            return Value(nullptr);
        }
        auto *ptr = new std::shared_ptr<qot::ShellcodeInstance>(instance);
        return Value(static_cast<double>(reinterpret_cast<intptr_t>(ptr)));
    }
    static Value builtin_scexec(const std::vector<Value> &args)
    {
        if (args.empty())
        {
            std::cerr << "[Error] ScExec() 需要至少一个参数" << std::endl;
            return Value(false);
        }
        double ptrVal = args[0].asNumber();
        auto *instancePtr = reinterpret_cast<std::shared_ptr<qot::ShellcodeInstance> *>(static_cast<intptr_t>(ptrVal));

        if (!instancePtr || !*instancePtr)
        {
            std::cerr << "[Error] ScExec() 无效的 shellcode 实例" << std::endl;
            return Value(false);
        }

        int timeoutMs = 5000;
        bool force = false;
        bool waitForever = false;

        if (args.size() >= 2 && args[1].isNumber())
        {
            timeoutMs = static_cast<int>(args[1].asNumber());
        }
        if (args.size() >= 3 && args[2].isBool())
        {
            force = args[2].asBool();
        }
        if (args.size() >= 4 && args[3].isBool())
        {
            waitForever = args[3].asBool();
        }

        bool result = qot::ShellcodeLoader::instance().executeShellcode(*instancePtr, timeoutMs, force, waitForever);
        return Value(result);
    }
    static Value builtin_scsweep(const std::vector<Value> &args)
    {
        if (args.empty())
        {
            std::cerr << "[Error] ScSweep() 需要至少一个参数" << std::endl;
            return Value(false);
        }

        double ptrVal = args[0].asNumber();
        auto *instancePtr = reinterpret_cast<std::shared_ptr<qot::ShellcodeInstance> *>(static_cast<intptr_t>(ptrVal));

        if (!instancePtr || !*instancePtr)
        {
            std::cerr << "[Error] ScSweep() 无效的 shellcode 实例" << std::endl;
            return Value(false);
        }

        bool result = qot::ShellcodeLoader::instance().cleanupShellcode(*instancePtr);
        delete instancePtr;

        return Value(result);
    }
    static std::string valueToString(const Value &v)
    {
        if (v.isArray())
        {
            std::stringstream ss;
            ss << "[";
            const auto &arr = v.asArray();
            for (size_t i = 0; i < arr.size(); i++)
            {
                if (i > 0)
                    ss << ", ";
                ss << valueToString(arr[i]);
            }
            ss << "]";
            return ss.str();
        }
        else if (v.isDict())
        {
            std::stringstream ss;
            ss << "{";
            const auto &dict = v.asDict();
            size_t i = 0;
            for (const auto &pair : dict)
            {
                if (i > 0)
                    ss << ", ";
                ss << pair.first << ": " << valueToString(pair.second);
                i++;
            }
            ss << "}";
            return ss.str();
        }
        else if (v.isNull())
        {
            return "null";
        }
        else if (v.isNone())
        {
            return "<none>";
        }
        else if (v.isBool())
        {
            return v.asBool() ? "true" : "false";
        }
        else
        {
            return v.toString();
        }
    }
    static Value builtin_output(const std::vector<Value> &args)
    {
        std::stringstream ss;
        for (size_t i = 0; i < args.size(); i++)
        {
            const auto &arg = args[i];
            std::string str = valueToString(arg);
            ss << str;
            if (i < args.size() - 1)
            {
                ss << " ";
            }
        }
        std::cout << ss.str() << std::endl;
        return Value(nullptr);
    }
    static Value builtin_print(const std::vector<Value> &args)
    {
        return builtin_output(args);
    }
    static Value builtin_type(const std::vector<Value> &args)
    {
        if (args.empty())
        {
            return Value("null");
        }
        const Value &arg = args[0];
        if (arg.isNull())
            return Value("null");
        if (arg.isBool())
            return Value("bool");
        if (arg.isNumber())
            return Value("number");
        if (arg.isString())
            return Value("string");
        if (arg.isArray())
            return Value("array");
        if (arg.isDict())
            return Value("dict");
        if (arg.isFunction())
            return Value("function");
        return Value("unknown");
    }
    static Value builtin_to_string(const std::vector<Value> &args)
    {
        if (args.empty())
        {
            return Value("");
        }
        return Value(args[0].toString());
    }
    static Value builtin_to_number(const std::vector<Value> &args)
    {
        if (args.empty())
        {
            return Value(0.0);
        }
        try
        {
            return Value(std::stod(args[0].toString()));
        }
        catch (...)
        {
            return Value(0.0);
        }
    }
    static Value builtin_len(const std::vector<Value> &args)
    {
        if (args.empty())
        {
            return Value(0.0);
        }
        const Value &arg = args[0];
        if (arg.isString())
        {
            return Value(static_cast<double>(arg.asString().length()));
        }
        if (arg.isArray())
        {
            return Value(static_cast<double>(arg.asArray().size()));
        }
        if (arg.isDict())
        {
            return Value(static_cast<double>(arg.asDict().size()));
        }
        return Value(0.0);
    }
    static Value builtin_input(const std::vector<Value> &args)
    {
        if (!args.empty())
        {
            std::cout << args[0].toString();
        }
        std::string line;
        std::getline(std::cin, line);
        return Value(line);
    }
    static Value builtin_push(const std::vector<Value> &args)
    {
        if (args.size() < 2)
        {
            std::cerr << "[Error] push() 需要至少2个参数" << std::endl;
            return Value(false);
        }

        if (!args[0].isArray())
        {
            std::cerr << "[Error] push() 第一个参数必须是数组" << std::endl;
            return Value(false);
        }
        auto &arr = const_cast<std::vector<Value> &>(args[0].asArray());

        for (size_t i = 1; i < args.size(); i++)
        {
            arr.push_back(args[i]);
        }

        return Value(true);
    }
    static Value builtin_sleep(const std::vector<Value> &args)
    {
        if (args.empty())
        {
            std::cerr << "[Error] sleep() 需要至少1个参数：毫秒数" << std::endl;
            return Value(false);
        }

        int milliseconds = static_cast<int>(args[0].asNumber());

        if (milliseconds < 0)
        {
            std::cerr << "[Error] sleep() 参数不能为负数" << std::endl;
            return Value(false);
        }

#ifdef _WIN32
        Sleep(milliseconds);
#else
        usleep(milliseconds * 1000);
#endif

        return Value(true);
    }

    static Value builtin_remove(const std::vector<Value> &args)
    {
        if (args.size() < 2)
        {
            std::cerr << "[Error] remove() 需要至少2个参数：数组和要删除的元素" << std::endl;
            return Value(false);
        }

        if (!args[0].isArray())
        {
            std::cerr << "[Error] remove() 第一个参数必须是数组" << std::endl;
            return Value(false);
        }

        auto &arr = const_cast<std::vector<Value> &>(args[0].asArray());
        Value toRemove = args[1];
        auto it = std::find_if(arr.begin(), arr.end(),
                               [&toRemove](const Value &v)
                               {
                                   if (toRemove.isNumber() && v.isNumber())
                                       return toRemove.asNumber() == v.asNumber();
                                   if (toRemove.isString() && v.isString())
                                       return toRemove.asString() == v.asString();
                                   if (toRemove.isBool() && v.isBool())
                                       return toRemove.asBool() == v.asBool();
                                   if (toRemove.isNull() && v.isNull())
                                       return true;
                                   return false;
                               });

        if (it != arr.end())
        {
            arr.erase(it);
            return Value(true);
        }

        std::cerr << "[Error] remove() 数组中不存在该元素" << std::endl;
        return Value(false);
    }

    static Value builtin_pop(const std::vector<Value> &args)
    {
        if (args.size() < 2)
        {
            std::cerr << "[Error] pop() 需要至少2个参数：数组和要删除的索引" << std::endl;
            return Value(false);
        }

        if (!args[0].isArray())
        {
            std::cerr << "[Error] pop() 第一个参数必须是数组" << std::endl;
            return Value(false);
        }

        auto &arr = const_cast<std::vector<Value> &>(args[0].asArray());
        double idxDouble = args[1].asNumber();
        int idx = static_cast<int>(idxDouble);

        if (idx < 0 || idx >= static_cast<int>(arr.size()))
        {
            std::cerr << "[Error] pop() 索引越界: " << idx << std::endl;
            return Value(false);
        }

        arr.erase(arr.begin() + idx);
        return Value(true);
    }

    static Value builtin_get_index(const std::vector<Value> &args)
    {
        if (args.size() < 2)
        {
            std::cerr << "[Error] get() 需要至少2个参数：数组和要查找的元素" << std::endl;
            return Value(0.0);
        }

        if (!args[0].isArray())
        {
            std::cerr << "[Error] get() 第一个参数必须是数组" << std::endl;
            return Value(-1.0);
        }

        const auto &arr = args[0].asArray();
        Value toFind = args[1];

        for (size_t i = 0; i < arr.size(); i++)
        {
            const Value &v = arr[i];
            if (toFind.isNumber() && v.isNumber() && toFind.asNumber() == v.asNumber())
            {
                return Value(static_cast<double>(i));
            }
            if (toFind.isString() && v.isString() && toFind.asString() == v.asString())
            {
                return Value(static_cast<double>(i));
            }
            if (toFind.isBool() && v.isBool() && toFind.asBool() == v.asBool())
            {
                return Value(static_cast<double>(i));
            }
            if (toFind.isNull() && v.isNull())
            {
                return Value(static_cast<double>(i));
            }
        }

        std::cerr << "[Error] get() 数组中不存在该元素" << std::endl;
        return Value(-1.0);
    }

    static Value builtin_str_replace(const std::vector<Value> &args)
    {
        if (args.size() < 3)
        {
            std::cerr << "[Error] strrep() 需要3个参数：原字符串、要替换的子串、替换后的子串" << std::endl;
            return Value("");
        }

        std::string original = args[0].toString();
        std::string from = args[1].toString();
        std::string to = args[2].toString();

        if (from.empty())
        {
            std::cerr << "[Error] strrep() 要替换的子串不能为空" << std::endl;
            return Value(original);
        }

        size_t pos = 0;
        std::string result = original;
        while ((pos = result.find(from, pos)) != std::string::npos)
        {
            result.replace(pos, from.length(), to);
            pos += to.length();
        }

        return Value(result);
    }

    static Value builtin_strcase(const std::vector<Value> &args)
    {
        if (args.empty())
        {
            std::cerr << "[Error] strcase() 需要至少1个参数：字符串" << std::endl;
            return Value("");
        }

        std::string str = args[0].toString();
        bool toLower = false;

        if (args.size() >= 2 && args[1].isNumber())
        {
            int mode = static_cast<int>(args[1].asNumber());
            toLower = (mode != 0);
        }

        if (toLower)
        {
            std::transform(str.begin(), str.end(), str.begin(), ::tolower);
        }
        else
        {
            std::transform(str.begin(), str.end(), str.begin(), ::toupper);
        }

        return Value(str);
    }
    static Value builtin_tnow(const std::vector<Value> &args)
    {
        auto now = std::chrono::system_clock::now();
        std::time_t now_time = std::chrono::system_clock::to_time_t(now);

#ifdef _WIN32
        std::tm tm_buf;
        localtime_s(&tm_buf, &now_time);
        char buffer[20];
        strftime(buffer, sizeof(buffer), "%Y-%m-%d %H:%M:%S", &tm_buf);
#else
        char buffer[20];
        strftime(buffer, sizeof(buffer), "%Y-%m-%d %H:%M:%S", std::localtime(&now_time));
#endif
        return Value(std::string(buffer));
    }

    static Value builtin_tstamp(const std::vector<Value> &args)
    {
        auto now = std::chrono::system_clock::now();
        auto ms = std::chrono::duration_cast<std::chrono::milliseconds>(
            now.time_since_epoch());
        return Value(static_cast<double>(ms.count()));
    }

    static Value builtin_cmd_exec(const std::vector<Value> &args)
    {
        if (args.empty())
        {
            std::cerr << "[Error] cmd_exec() 需要至少1个参数：命令字符串" << std::endl;
            return Value(nullptr);
        }

        std::string command = args[0].toString();
        if (command.empty())
        {
            std::cerr << "[Error] cmd_exec() 命令不能为空" << std::endl;
            return Value(nullptr);
        }

#ifdef _WIN32
        STARTUPINFOA si = {sizeof(si)};
        PROCESS_INFORMATION pi = {0};
        si.dwFlags = STARTF_USESHOWWINDOW;
        si.wShowWindow = SW_HIDE;

        std::string cmdLine = "cmd.exe /c " + command;
        std::vector<char> cmdBuffer(cmdLine.begin(), cmdLine.end());
        cmdBuffer.push_back('\0');

        BOOL success = CreateProcessA(
            NULL, cmdBuffer.data(), NULL, NULL, FALSE,
            CREATE_NO_WINDOW, NULL, NULL, &si, &pi);

        if (!success)
        {
            std::cerr << "[Error] cmd_exec() 执行命令失败: " << command << std::endl;
            return Value(nullptr);
        }
        return Value(static_cast<double>(reinterpret_cast<intptr_t>(pi.hProcess)));
#else
        pid_t pid = fork();
        if (pid == -1)
        {
            std::cerr << "[Error] cmd_exec() fork失败" << std::endl;
            return Value(nullptr);
        }
        else if (pid == 0)
        {
            execl("/bin/sh", "sh", "-c", command.c_str(), nullptr);
            exit(0);
        }
        else
        {
            return Value(static_cast<double>(pid));
        }
#endif
    }
    static Value builtin_gtypes(const std::vector<Value> &args)
    {
        if (args.empty())
        {
            std::cerr << "[Error] gtypes() 需要至少一个参数" << std::endl;
            return Value("any");
        }

        switch (args[0].type())
        {
        case Value::Type::STRING:
            return Value("string");
        case Value::Type::ARRAY:
            return Value("array");
        case Value::Type::NUMBER:
            return Value("number");
        case Value::Type::BOOL:
            return Value("boolean");
        case Value::Type::DICT:
            return Value("dict");
        case Value::Type::NULL_:
            return Value("null");
        case Value::Type::FUNCTION:
        case Value::Type::BUILTIN:
            return Value("function");
        default:
            return Value("any");
        }
    }
    static Value builtin_is_number(const std::vector<Value> &args)
    {
        if (args.empty())
        {
            return Value(false);
        }
        return Value(args[0].isNumber());
    }

    static Value builtin_iter_len(const std::vector<Value> &args)
    {
        if (args.empty())
        {
            return Value(0.0);
        }
        const Value &arg = args[0];
        if (arg.isNumber())
        {
            double num = arg.asNumber();
            if (num < 0)
                num = 0;
            return Value(num);
        }
        else if (arg.isArray())
        {
            return Value(static_cast<double>(arg.asArray().size()));
        }
        else if (arg.isString())
        {
            return Value(static_cast<double>(arg.asString().size()));
        }
        else
        {
            return Value(0.0);
        }
    }
    static Value builtin_json_decode(const std::vector<Value> &args)
    {
        if (args.empty())
        {
            std::cerr << "[Error] json_decode() 需要至少一个参数" << std::endl;
            return Value(nullptr);
        }

        std::string jsonStr = args[0].toString();
        if (jsonStr.empty())
        {
            return Value(nullptr);
        }

        try
        {
            auto j = nlohmann::json::parse(jsonStr);
            std::function<Value(const nlohmann::json &)> convert = [&](const nlohmann::json &j) -> Value
            {
                if (j.is_null())
                {
                    return Value(nullptr);
                }
                else if (j.is_boolean())
                {
                    return Value(j.get<bool>());
                }
                else if (j.is_number())
                {
                    return Value(j.get<double>());
                }
                else if (j.is_string())
                {
                    return Value(j.get<std::string>());
                }
                else if (j.is_array())
                {
                    Value arr = Value::createArray();
                    for (const auto &item : j)
                    {
                        arr.asArray().push_back(convert(item));
                    }
                    return arr;
                }
                else if (j.is_object())
                {
                    Value dict = Value::createDict();
                    for (auto &[key, val] : j.items())
                    {
                        dict.asDict()[key] = convert(val);
                    }
                    return dict;
                }
                return Value(nullptr);
            };

            return convert(j);
        }
        catch (const std::exception &e)
        {
            std::cerr << "[Error] JSON 解析失败: " << e.what() << std::endl;
            return Value(nullptr);
        }
    }
    static Value builtin_json_encode(const std::vector<Value> &args)
    {
        if (args.empty())
        {
            std::cerr << "[Error] json_encode() 需要至少一个参数" << std::endl;
            return Value("null");
        }
        std::function<nlohmann::json(const Value &)> convert = [&](const Value &v) -> nlohmann::json
        {
            if (v.isNull())
            {
                return nullptr;
            }
            else if (v.isBool())
            {
                return v.asBool();
            }
            else if (v.isNumber())
            {
                return v.asNumber();
            }
            else if (v.isString())
            {
                return v.asString();
            }
            else if (v.isArray())
            {
                nlohmann::json arr = nlohmann::json::array();
                for (const auto &item : v.asArray())
                {
                    arr.push_back(convert(item));
                }
                return arr;
            }
            else if (v.isDict())
            {
                nlohmann::json obj = nlohmann::json::object();
                for (const auto &[key, val] : v.asDict())
                {
                    obj[key] = convert(val);
                }
                return obj;
            }
            return nullptr;
        };

        try
        {
            nlohmann::json j = convert(args[0]);
            return Value(j.dump());
        }
        catch (const std::exception &e)
        {
            std::cerr << "[Error] JSON 编码失败: " << e.what() << std::endl;
            return Value("null");
        }
    }

    static Value builtin_dict_get(const std::vector<Value> &args)
    {
        if (args.size() < 2)
        {
            std::cerr << "[Error] dict_get() 需要至少2个参数：字典和键" << std::endl;
            return Value(nullptr);
        }

        if (!args[0].isDict())
        {
            std::cerr << "[Error] dict_get() 第一个参数必须是字典，而非 "
                      << args[0].toString() << std::endl;
            return Value(nullptr);
        }

        const auto &dict = args[0].asDict();
        std::string key = args[1].toString();

#ifdef QOT_DICT_LEGACY_BASELINE
        auto it = dict.find(key);
        if (it != dict.end())
        {
            return it->second;
        }
#else
        const Value *hit = dict.find_ptr_h(qot_hash_string(key.data(), key.size()), key);
        if (hit)
        {
            return *hit;
        }
#endif
        if (args.size() >= 3)
        {
            return args[2];
        }

        return Value(nullptr);
    }

    static Value builtin_dict_set(const std::vector<Value> &args)
    {
        if (args.size() < 3)
        {
            std::cerr << "[Error] dict_set() 需要3个参数：字典、键和值" << std::endl;
            return Value(false);
        }

        if (!args[0].isDict())
        {
            std::cerr << "[Error] dict_set() 第一个参数必须是字典" << std::endl;
            return Value(false);
        }

        auto &dict = const_cast<QOT_DICT_MAP_TYPE &>(args[0].asDict());
        std::string key = args[1].toString();
        Value value = args[2];

#ifdef QOT_DICT_LEGACY_BASELINE
        dict[key] = value;
#else
        dict.at_h(qot_hash_string(key.data(), key.size()), key) = value;
#endif
        return Value(true);
    }

    static Value builtin_dict_keys(const std::vector<Value> &args)
    {
        if (args.empty())
        {
            std::cerr << "[Error] dict_keys() 需要1个参数：字典" << std::endl;
            return Value::createArray();
        }

        if (!args[0].isDict())
        {
            std::cerr << "[Error] dict_keys() 参数必须是字典" << std::endl;
            return Value::createArray();
        }

        const auto &dict = args[0].asDict();
        std::vector<Value> keys;
        keys.reserve(dict.size());

        for (const auto &pair : dict)
        {
            keys.emplace_back(pair.first);
        }

        return Value(keys);
    }
    void BuiltinManager::registerBuiltins()
    {
        // 【M1 / G-5】注册名单来自 builtins.toml（经 builtins_manifest.h 展开），
        // 不再在这里逐条手写。手写那份表正是 G-5 的根因：Python 表与 C++ 表
        // 各自漂移，`parts` / `beair` 在 VM 侧静默缺失（调用返回 null 无诊断）。
        // 现在清单登记了而本 TU 没有对应函数符号 → 展开处符号未声明 →
        // 编译失败；反之 C++ 多写了清单里没有的注册，也会被 _dbg_builtins.py
        // 的文本对账抓出来。
#define QOT_REG_BUILTIN(_name, _fn) registerFunction(#_name, _fn);
        QOT_VM_BUILTINS(QOT_REG_BUILTIN)
#undef QOT_REG_BUILTIN
        static_assert(QOT_VM_BUILTIN_COUNT > 0, "builtins.toml 生成了空清单");
    }
}