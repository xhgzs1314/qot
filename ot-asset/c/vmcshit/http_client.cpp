#include "http_client.h"
#include <sstream>
#include <algorithm>

namespace qot {

HttpClient::HttpClient() : m_verifySSL(true), m_userAgent("QOT-HTTP-Client/1.0") {}

HttpClient::~HttpClient() {}

std::wstring utf8ToWide(const std::string& str) {
    if (str.empty()) return L"";
    int size = MultiByteToWideChar(CP_UTF8, 0, str.c_str(), (int)str.size(), NULL, 0);
    std::wstring wstr(size, 0);
    MultiByteToWideChar(CP_UTF8, 0, str.c_str(), (int)str.size(), &wstr[0], size);
    return wstr;
}

std::string wideToUtf8(const std::wstring& wstr) {
    if (wstr.empty()) return "";
    int size = WideCharToMultiByte(CP_UTF8, 0, wstr.c_str(), (int)wstr.size(), NULL, 0, NULL, NULL);
    std::string str(size, 0);
    WideCharToMultiByte(CP_UTF8, 0, wstr.c_str(), (int)wstr.size(), &str[0], size, NULL, NULL);
    return str;
}

std::string urlEncode(const std::string& str) {
    std::stringstream ss;
    for (unsigned char c : str) {
        if (isalnum(c) || c == '-' || c == '_' || c == '.' || c == '~') {
            ss << c;
        } else {
            char buf[4];
            sprintf_s(buf, "%%%02X", c);
            ss << buf;
        }
    }
    return ss.str();
}

std::string buildQueryString(const std::unordered_map<std::string, std::string>& params) {
    if (params.empty()) return "";
    std::stringstream ss;
    bool first = true;
    for (const auto& p : params) {
        if (!first) ss << "&";
        first = false;
        ss << urlEncode(p.first) << "=" << urlEncode(p.second);
    }
    return ss.str();
}

HttpResponse HttpClient::send(const std::string& url,
                               const std::unordered_map<std::string, std::string>& params,
                               const std::string& method,
                               int timeoutSeconds) {
    HttpResponse resp;
    
    
    std::wstring wurl = utf8ToWide(url);
    URL_COMPONENTS urlComp = {0};
    urlComp.dwStructSize = sizeof(urlComp);
    urlComp.dwHostNameLength = (DWORD)-1;
    urlComp.dwUrlPathLength = (DWORD)-1;
    urlComp.dwExtraInfoLength = (DWORD)-1;
    
    if (!WinHttpCrackUrl(wurl.c_str(), 0, 0, &urlComp)) {
        resp.errorMessage = "URL 解析失败";
        return resp;
    }
    
    std::wstring host(urlComp.lpszHostName, urlComp.dwHostNameLength);
    std::wstring path(urlComp.lpszUrlPath, urlComp.dwUrlPathLength);
    
    bool isHttps = (urlComp.nScheme == INTERNET_SCHEME_HTTPS);
    int port = urlComp.nPort;
    
    
    std::string queryStr = buildQueryString(params);
    std::string fullPath = wideToUtf8(path);
    if (!queryStr.empty() && method == "GET") {
        fullPath += "?" + queryStr;
    }
    std::wstring wpath = utf8ToWide(fullPath);
    

    HINTERNET hSession = WinHttpOpen(utf8ToWide(m_userAgent).c_str(), 
                                      WINHTTP_ACCESS_TYPE_DEFAULT_PROXY,
                                      NULL, NULL, 0);
    if (!hSession) {
        resp.errorMessage = "WinHttpOpen 失败";
        return resp;
    }
    
    HINTERNET hConnect = WinHttpConnect(hSession, host.c_str(), port, 0);
    if (!hConnect) {
        resp.errorMessage = "WinHttpConnect 失败";
        WinHttpCloseHandle(hSession);
        return resp;
    }
    
    DWORD flags = isHttps ? WINHTTP_FLAG_SECURE : 0;
    HINTERNET hRequest = WinHttpOpenRequest(hConnect, utf8ToWide(method).c_str(),
                                             wpath.c_str(), NULL, NULL, NULL, flags);
    if (!hRequest) {
        resp.errorMessage = "WinHttpOpenRequest 失败";
        WinHttpCloseHandle(hConnect);
        WinHttpCloseHandle(hSession);
        return resp;
    }
    
    
    WinHttpSetTimeouts(hRequest, timeoutSeconds * 1000, timeoutSeconds * 1000,
                       timeoutSeconds * 1000, timeoutSeconds * 1000);

    if (isHttps && !m_verifySSL) {
        DWORD flags = SECURITY_FLAG_IGNORE_UNKNOWN_CA | SECURITY_FLAG_IGNORE_CERT_CN_INVALID |
                      SECURITY_FLAG_IGNORE_CERT_DATE_INVALID;
        WinHttpSetOption(hRequest, WINHTTP_OPTION_SECURITY_FLAGS, &flags, sizeof(flags));
    }
    for (const auto& h : m_headers) {
        std::string headerStr = h.first + ": " + h.second;
        WinHttpAddRequestHeaders(hRequest, utf8ToWide(headerStr).c_str(), (DWORD)-1,
                                  WINHTTP_ADDREQ_FLAG_ADD);
    }
    std::string postData;
    if (method == "POST" && !queryStr.empty()) {
        postData = queryStr;
        std::string contentType = "Content-Type: application/x-www-form-urlencoded";
        WinHttpAddRequestHeaders(hRequest, utf8ToWide(contentType).c_str(), (DWORD)-1,
                                  WINHTTP_ADDREQ_FLAG_ADD);
    }
    BOOL result = WinHttpSendRequest(hRequest,
                                      NULL, 0,
                                      postData.empty() ? NULL : (LPVOID)postData.c_str(),
                                      (DWORD)postData.size(),
                                      (DWORD)postData.size(), 0);
    
    if (!result) {
        resp.errorMessage = "WinHttpSendRequest 失败";
        WinHttpCloseHandle(hRequest);
        WinHttpCloseHandle(hConnect);
        WinHttpCloseHandle(hSession);
        return resp;
    }
    
    result = WinHttpReceiveResponse(hRequest, NULL);
    if (!result) {
        resp.errorMessage = "WinHttpReceiveResponse 失败";
        WinHttpCloseHandle(hRequest);
        WinHttpCloseHandle(hConnect);
        WinHttpCloseHandle(hSession);
        return resp;
    }
    DWORD statusCode = 0;
    DWORD size = sizeof(statusCode);
    WinHttpQueryHeaders(hRequest, WINHTTP_QUERY_STATUS_CODE | WINHTTP_QUERY_FLAG_NUMBER,
                        NULL, &statusCode, &size, NULL);
    resp.statusCode = statusCode;
    resp.success = (statusCode >= 200 && statusCode < 400);
    std::string body;
    DWORD bytesRead = 0;
    char buffer[4096];
    while (WinHttpReadData(hRequest, buffer, sizeof(buffer), &bytesRead) && bytesRead > 0) {
        body.append(buffer, bytesRead);
    }
    resp.body = body;
    WinHttpCloseHandle(hRequest);
    WinHttpCloseHandle(hConnect);
    WinHttpCloseHandle(hSession);
    
    resp.success = (resp.success && resp.errorMessage.empty());
    return resp;
}

} 