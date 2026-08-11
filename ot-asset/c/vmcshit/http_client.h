#ifndef WINHTTP_CLIENT_H
#define WINHTTP_CLIENT_H

#include <string>
#include <unordered_map>
#include <windows.h>
#include <winhttp.h>
#pragma comment(lib, "winhttp.lib")

namespace qot
{

    struct HttpResponse
    {
        int statusCode;
        std::string statusMessage;
        std::unordered_map<std::string, std::string> headers;
        std::string body;
        bool success;
        std::string errorMessage;

        HttpResponse() : statusCode(0), success(false) {}
    };

    class HttpClient
    {
    public:
        HttpClient();
        ~HttpClient();
        HttpResponse send(const std::string &url,
                          const std::unordered_map<std::string, std::string> &params = {},
                          const std::string &method = "GET",
                          int timeoutSeconds = 30);
        void setHeaders(const std::unordered_map<std::string, std::string> &headers) { m_headers = headers; }
        void setUserAgent(const std::string &agent) { m_userAgent = agent; }
        void setVerifySSL(bool verify) { m_verifySSL = verify; }

    private:
        std::unordered_map<std::string, std::string> m_headers;
        std::string m_userAgent;
        bool m_verifySSL;
    };

}

#endif