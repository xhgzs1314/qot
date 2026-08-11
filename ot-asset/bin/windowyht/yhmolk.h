#pragma once
//   ______   __          _
//  | __ \ \ / /    _   _| |__
//  |  _ \\ V _____| | | | '_ \                                                                                       
//  | |_) || |_____| |_| | | | |
//  |____/ |_|      \__, |_| |_|
//      _    _ _   _|___/    _                                       _   _          _
//     / \  | | | | |_| |__ (_)_ __   __ _ ___   _ __   ___  ___  __| | | |_ ___   | |__   ___    ___ ___  __ _ _   _
//    / _ \ | | | | __| '_ \| | '_ \ / _` / __| | '_ \ / _ \/ _ \/ _` | | __/ _ \  | '_ \ / _ \  / _ / __|/ _` | | | |
//   / ___ \| | | | |_| | | | | | | | (_| \__ \ | | | |  __|  __| (_| | | || (_) | | |_) |  __/ |  __\__ | (_| | |_| |
//  /_/   \_|_|_|  \__|_| |_|_|_| |_|\__, |___/ |_| |_|\___|\___|\__,_|  \__\___/  |_.__/ \___|  \___|___/\__,_|\__, |
//                                   |___/                                                                      |___/
/*
When designing this header file, the main objective was to provide a simple and easy-to-use
Windows GUI development framework for Chinese developers, reducing the learning curve of the
Windows API and enabling the rapid construction of basic graphical user interfaces.Considering
that Chinese developers may encounter some difficulties in reading and understanding English
documentation and code,Chinese namingmethods and comments were adopted to enhance the readability and usability of the code.
*/
#pragma once
#ifndef UNICODE
#define UNICODE
#endif

#ifndef _UNICODE
#define _UNICODE
#endif
#pragma execution_character_set("utf-8")
#include <windows.h>
#include <string>
#include <random>
#include <memory>
#include <ctime>
#include <iostream>
#include <thread>
#include <tchar.h>
#include <map>
#include <vector>
#include <regex>
#include <functional>
#include <unordered_map>
#include <commctrl.rh>
#include <CommCtrl.h>
#include <sstream>
#include <shlobj.h>
#define WM_SHOW_HIDE_COMPONENT (WM_USER + 1)
#define WM_FUCKTHE_COMM_HIDEER (0X93610372)
#define WM_SETTEXTCOLOR_y_label (0x93610373)
#define IDC_TAB 100
#pragma comment(linker, "\"/manifestdependency:type='win32' "          \
                        "name='Microsoft.Windows.Common-Controls' "    \
                        "version='6.0.0.0' processorArchitecture='*' " \
                        "publicKeyToken='6595b64144ccf1df' language='*'\"")
#pragma comment(lib, "Comctl32.lib")
using namespace std;
typedef function<void()> ButtonCallback; // 按钮回调事件
typedef std::function<void()> MenuCommandHandler;
map<UINT, MenuCommandHandler> menuCommandHandlers;
HWND g_hEdit;
bool g_bDragging = false;
POINT g_ptOffset;
map<HWND, WCHAR *> filecallback_dragged;
struct ShortcutInfo
{
    UINT id;
    UINT fsModifiers;
    UINT vk;
    void (*callback)(void);
};

vector<ShortcutInfo> g_shortcutInfos;
struct ComponentAttributes
{
    int x = 0;
    int y = 0;
    int width = 0;
    int height = 0;
    std::string content = "";
};
size_t WriteCallback(void *contents, size_t size, size_t nmemb, std::string *output)
{
    size_t totalSize = size * nmemb;
    output->append((char *)contents, totalSize);
    return totalSize;
}

/*类型转换;Wstring_to_string*/
string Wstring_to_string(std::wstring wstr)
{
    std::string res;
    int len = WideCharToMultiByte(CP_ACP, 0, wstr.c_str(), wstr.size(), nullptr, 0, nullptr, nullptr);
    if (len <= 0)
    {
        return res;
    }
    char *buffer = new char[len + 1];
    if (buffer == nullptr)
    {
        return res;
    }
    WideCharToMultiByte(CP_ACP, 0, wstr.c_str(), wstr.size(), buffer, len, nullptr, nullptr);
    buffer[len] = '\0';
    res.append(buffer);
    delete[] buffer;
    return res;
}
/*String_to_lpcwstr*/
LPCWSTR String_to_lpcwstr(const std::string &str)
{
    size_t size = str.length();
    int wLen = ::MultiByteToWideChar(CP_UTF8, 0, str.c_str(), -1, NULL, 0);
    wchar_t *buffer = new wchar_t[wLen + 1];
    memset(buffer, 0, (wLen + 1) * sizeof(wchar_t));
    MultiByteToWideChar(CP_ACP, 0, str.c_str(), size, (LPWSTR)buffer, wLen);
    return buffer;
}
/*systemtime转string*/
string System_to_string(const SYSTEMTIME &st)
{
    std::stringstream ss;
    ss << st.wYear << "-" << st.wMonth << "-" << st.wDay << " " << st.wHour << ":" << st.wMinute << ":" << st.wSecond;
    return ss.str();
}
/*
V1.0.7-部分功能还需改进！
*/
class 熙鹤_GUI
{
public:
    熙鹤_GUI()
    {
        srand(static_cast<unsigned int>(time(nullptr)));
        size_t length = static_cast<size_t>(6) + rand() % (8 - 6 + 1);
        string xxx = generate_random_string(length);
        classOfTheGuiWindow = string_to_wchar(xxx);
        WNDCLASS wc = {};
        wc.lpfnWndProc = StaticWindowProc;
        wc.hInstance = GetModuleHandle(NULL);
        wc.lpszClassName = classOfTheGuiWindow;

        RegisterClass(&wc);
        hWnd = CreateWindow(
            classOfTheGuiWindow,
            classOfTheGuiWindow,
            WS_OVERLAPPEDWINDOW,
            CW_USEDEFAULT, CW_USEDEFAULT,
            800, 600,
            NULL,
            NULL,
            GetModuleHandle(NULL),
            NULL);
        SetWindowLongPtr(hWnd, GWLP_USERDATA, reinterpret_cast<LONG_PTR>(this));
        HBRUSH hNewBrush = CreateSolidBrush(RGB(240, 240, 240));
        SetClassLongPtr(hWnd, GCLP_HBRBACKGROUND, (LONG_PTR)hNewBrush);
        InvalidateRect(hWnd, NULL, TRUE);
        UpdateWindow(hWnd);
        m_hMemDC = nullptr;
        m_hMemBitmap = nullptr;
        m_hOldBitmap = nullptr;
        m_bufferingEnabled = true;
        m_clientWidth = 800;
        m_clientHeight = 600;
        m_hBackgroundBrush = nullptr;
    }

    ~熙鹤_GUI()
    {
        if (m_hBackgroundBrush)
        {
            DeleteObject(m_hBackgroundBrush);
        }
        if (m_hMemDC)
        {
            if (m_hOldBitmap)
            {
                SelectObject(m_hMemDC, m_hOldBitmap);
            }
            if (m_hMemBitmap)
            {
                DeleteObject(m_hMemBitmap);
            }
            DeleteDC(m_hMemDC);
        }
        DestroyWindow(hWnd);
    }

    
    void 刷新窗口()
    {
        InvalidateRect(hWnd, NULL, FALSE); 
        UpdateWindow(hWnd);
    }

 
    void 开始_批量更新()
    {
        SendMessage(hWnd, WM_SETREDRAW, FALSE, 0);
    }

    void 结束_批量更新()
    {
        SendMessage(hWnd, WM_SETREDRAW, TRUE, 0);
        刷新窗口();
    }
    HICON LoadNewIcon(const wchar_t *iconPath)
    {
        return static_cast<HICON>(LoadImage(NULL, iconPath, IMAGE_ICON, 0, 0, LR_LOADFROMFILE | LR_DEFAULTSIZE));
    }
    /*设置新的图标*/
    void 窗口_图标设置(LPCTSTR icopath)
    {
        HICON hNewIcon = static_cast<HICON>(LoadImage(NULL,
                                                      icopath,
                                                      IMAGE_ICON,
                                                      0, 0,
                                                      LR_LOADFROMFILE | LR_DEFAULTSIZE));
        if (!hNewIcon)
        {
            警告框(L"警告", L"无法访问到该图标文件！请根据以下原因排查：\n1.当前路径下不存在该ICO文件\n2.该文件非ICO文件，建议修复");
            销毁窗口();
        }
        WNDCLASS wc;
        GetClassInfo(NULL, classOfTheGuiWindow, &wc);
        if (wc.hIcon != NULL)
            DestroyIcon((HICON)wc.hIcon);
        SetClassLongPtr(hWnd, GCLP_HICON, (LONG_PTR)hNewIcon);
        SetWindowPos(hWnd, NULL, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_FRAMECHANGED);
    }
    void 启用_双缓冲(bool enable)
    {
        m_bufferingEnabled = enable;
        if (!enable && m_hMemDC)
        {
            if (m_hOldBitmap)
            {
                SelectObject(m_hMemDC, m_hOldBitmap);
                m_hOldBitmap = nullptr;
            }
            if (m_hMemBitmap)
            {
                DeleteObject(m_hMemBitmap);
                m_hMemBitmap = nullptr;
            }
            if (m_hMemDC)
            {
                DeleteDC(m_hMemDC);
                m_hMemDC = nullptr;
            }
        }
    }

    // 调整客户区大小时重新创建缓冲区
    void 调整_缓冲区大小(int width, int height)
    {
        if (!m_bufferingEnabled)
            return;
        m_clientWidth = width;
        m_clientHeight = height;
        if (m_hMemDC)
        {
            if (m_hOldBitmap)
            {
                SelectObject(m_hMemDC, m_hOldBitmap);
                m_hOldBitmap = nullptr;
            }
            if (m_hMemBitmap)
            {
                DeleteObject(m_hMemBitmap);
                m_hMemBitmap = nullptr;
            }
            DeleteDC(m_hMemDC);
            m_hMemDC = nullptr;
        }
        HDC hdc = GetDC(hWnd);
        m_hMemDC = CreateCompatibleDC(hdc);
        m_hMemBitmap = CreateCompatibleBitmap(hdc, width, height);
        m_hOldBitmap = (HBITMAP)SelectObject(m_hMemDC, m_hMemBitmap);
        ReleaseDC(hWnd, hdc);
    }

    // 设置背景画刷
    void 设置_背景画刷(COLORREF color)
    {
        if (m_hBackgroundBrush)
        {
            DeleteObject(m_hBackgroundBrush);
        }
        m_hBackgroundBrush = CreateSolidBrush(color);
    }
    /* 窗口循环 */
    void 窗口循环()
    {
        ShowWindow(hWnd, SW_SHOW);
        MSG msg;
        while (GetMessage(&msg, NULL, 0, 0))
        {
            TranslateMessage(&msg);
            DispatchMessage(&msg);
        }
    }

    /* 窗口标题 */
    void 设置窗口标题(const wchar_t *newTitle)
    {
        SetWindowText(hWnd, newTitle);
    }
    /* 返回窗口句柄 */
    HWND 获取窗口句柄()
    {
        return hWnd;
    }

    /* 销毁窗口 */
    void 销毁窗口()
    {
        DestroyWindow(hWnd);
    }
    /* 红绿蓝- */
    void 窗口_背景颜色(int r, int g, int b)
    {
        HBRUSH hNewBrush = CreateSolidBrush(RGB(r, g, b));
        SetClassLongPtr(hWnd, GCLP_HBRBACKGROUND, (LONG_PTR)hNewBrush);
        InvalidateRect(hWnd, NULL, TRUE);
        UpdateWindow(hWnd);
    }
    /*是否支持客户区拖动移动窗口*/
    void 窗口_客户区拖动(bool allowsx)
    {
        m_window_close_canby_abovevar = allowsx;
    }
    /*支持组件可被拖动*/
    void 组件_拖动_设置(HWND hwndset, bool bDraggable)
    {
        if (bDraggable)
        {
            SetWindowLongPtr(hwndset, GWLP_WNDPROC, (LONG_PTR)wnd_plugin_dragged);
        }
        else
        {
            SetWindowLongPtr(hwndset, GWLP_WNDPROC, (LONG_PTR)DefWindowProc);
        }
    }
    /*支持组件可接收拖动的文件*/
    void 文件_拖动_初始化(HWND hsnd, bool trues)
    {
        DragAcceptFiles(hsnd, trues);
    }
    /*支持组件接收可拖动的文件,返回其文件路径
    请勿调用此方法*/
    bool 附_文件_拖动_设置(HWND hwnd, HDROP hDrop)
    {
        UINT numFiles = DragQueryFile(hDrop, 0xFFFFFFFF, NULL, 0);
        if (numFiles == 0)
        {
            return false;
        }
        UINT pathLen = DragQueryFile(hDrop, 0, NULL, 0);
        WCHAR *filePath = new WCHAR[pathLen + 1];
        DragQueryFile(hDrop, 0, filePath, pathLen + 1);
        DragFinish(hDrop);
        filecallback_dragged[hwnd] = filePath;
        return true;
    }
    /*获取能接收文件的组件获取到的文件路径*/
    WCHAR *文件_拖动_获取路径(HWND hwnd)
    {
        if (filecallback_dragged.find(hwnd) != filecallback_dragged.end())
        {
            return filecallback_dragged[hwnd];
        }
        return nullptr;
    }

    /*释放指定组件的拖动文件路径*/
    void 文件_拖动_释放路径(HWND hwnd)
    {
        if (filecallback_dragged.find(hwnd) != filecallback_dragged.end())
        {
            delete[] filecallback_dragged[hwnd];
            filecallback_dragged.erase(hwnd);
        }
    }
    /* 改变窗口大小;宽、高 */
    void 窗口大小(int newWidth, int newHeight)
    {
        RECT rect;
        GetWindowRect(hWnd, &rect);
        SetWindowPos(hWnd, NULL, rect.left, rect.top, newWidth, newHeight, SWP_NOZORDER);
    }
    /*清空entry输入框内容*/
    void 输入框_清空内容(HWND hEdit)
    {
        SendMessage(hEdit, EM_SETSEL, 0, -1);
        SendMessage(hEdit, EM_REPLACESEL, FALSE, (LPARAM) "");
    }
    /*向entry内加入内容(光标后)*/
    void 输入框_加入内容(HWND hEdit, const wchar_t *text)
    {
        int len = lstrlenW(text);
        SendMessage(hEdit, EM_SETSEL, (WPARAM)-1, (LPARAM)-1);
        SendMessage(hEdit, EM_REPLACESEL, FALSE, (LPARAM)text);
    }
    /*为指定组件绑定快捷键*/
    void 组件_绑定快捷键(UINT id, UINT fsModifiers, UINT vk, void (*callback)(void))
    {
        ShortcutInfo info = {id, fsModifiers, vk, callback};
        g_shortcutInfos.push_back(info);
        RegisterHotKey(NULL, id, fsModifiers, vk);
    }
    /*屏幕分辨率获取*/
    void 屏幕分辨率获取(int &width, int &height)
    {
        width = GetSystemMetrics(SM_CXSCREEN);
        height = GetSystemMetrics(SM_CYSCREEN);
    }
    /*判断一个文件是否为只读*/
    bool 文件_是否只读(const wchar_t *filePath)
    {
        DWORD attributes = GetFileAttributes(filePath);
        return (attributes & FILE_ATTRIBUTE_READONLY) != 0;
    }
    /*简化下；消息框*/
    void 消息框(LPCTSTR titles, LPCTSTR content) const
    {
        LPCTSTR msg = content;
        LPCTSTR title = titles;
        MessageBox(hWnd, msg, title, MB_OK | MB_ICONINFORMATION);
    }
    /*鼠标指针设置
 IDC_ARROW：标准箭头指针; IDC_IBEAM：文本输入光标
IDC_WAIT：沙漏或等待指针; IDC_CROSS：十字指针
IDC_UPARROW：向上箭头指针; IDC_SIZEALL：四个方向的箭头指针
IDC_SIZENWSE：西北 - 东南方向的双箭头指针; IDC_SIZENESW：东北 - 西南方向的双箭头指针
IDC_SIZEWE：左右方向的双箭头指针; IDC_SIZENS：上下方向的双箭头指针
IDC_HAND：手形指针
    */
    void 鼠标_指针设置(wchar_t *cursorResourceId)
    {
        HCURSOR hCursor = LoadCursor(NULL, cursorResourceId);
        if (hCursor == NULL)
        {
            MessageBox(NULL, L"无法加载此鼠标指针", L"异常", MB_OK | MB_ICONERROR);
            return;
        }
        SetCursor(hCursor);
    }
    /*简化下：警告框*/
    void 警告框(LPCTSTR titles, LPCTSTR content) const
    {
        LPCTSTR msg = content;
        LPCTSTR title = titles;
        MessageBox(hWnd, msg, title, MB_OK | MB_ICONWARNING);
    }
    /*返回用户选中的文件路径；不填参数则是全部文件，反之vector<string> fixechos = { "txt", "docx" }; 表示仅支持选择txt和docx文件*/
    string 文件询问框(const std::vector<std::string> *extensions = nullptr)
    {
        OPENFILENAMEA ofn;
        char szFile[MAX_PATH] = {0};
        ZeroMemory(&ofn, sizeof(ofn));
        ofn.lStructSize = sizeof(ofn);
        ofn.hwndOwner = NULL;
        ofn.lpstrFile = szFile;
        ofn.nMaxFile = MAX_PATH;
        const char *filterStr = Filter_choose_b(extensions);
        ofn.lpstrFilter = filterStr;
        ofn.nFilterIndex = 1;
        ofn.Flags = OFN_PATHMUSTEXIST | OFN_FILEMUSTEXIST;

        if (GetOpenFileNameA(&ofn))
        {
            return std::string(szFile);
        }
        return "";
    }

    /* x坐标.y坐标,宽,高,显示文本,回调事件 */
    HWND 放置组件_按钮(int x, int y, int width, int height, const wchar_t *text, ButtonCallback callback, COLORREF textcolor, bool opitatcy, int fontsize)
    {
        HWND hButton = CreateWindow(
            TEXT("BUTTON"),
            text,
            WS_VISIBLE | WS_CHILD | BS_DEFPUSHBUTTON,
            x, y, width, height,
            hWnd,
            NULL,
            GetModuleHandle(NULL),
            NULL);
        if (callback != NULL)
        {
            buttonCallbacks[hButton] = callback;
        }
        组件_隐藏(hButton);
        颜色_组件新增(hButton, textcolor, 2, opitatcy, fontsize, x, y);
        组件_状态_设置(hButton, 0);
        return hButton;
    }
    /*这玩意作用不太大*/
    ComponentAttributes 组件_获取扩展属性(HWND hComponent)
    {
        ComponentAttributes attributes;
        RECT rect;
        if (GetClientRect(hComponent, &rect))
        {
            attributes.x = rect.left;
            attributes.y = rect.top;
            attributes.width = rect.right - rect.left;
            attributes.height = rect.bottom - rect.top;
        }
        wchar_t buffer[256];
        buffer[0] = L'\0';
        GetWindowTextW(hComponent, buffer, sizeof(buffer) / sizeof(buffer[0]));
        attributes.content = Wstring_to_string(buffer);
        return attributes;
    }
    vector<int> 组件_获取位置属性_plugs(HWND hComponent)
    {
        RECT rect;
        vector<int> attributes;
        if (GetClientRect(hComponent, &rect))
        {
            attributes.push_back(rect.left);
            attributes.push_back(rect.top);
            attributes.push_back(rect.right - rect.left);
            attributes.push_back(rect.bottom - rect.top);
        }
        return attributes;
    }
    /*组件获取其text.单独割出来，某些时候还是有点用的*/
    string 组件_获取TEXT(HWND hComponent)
    {
        wchar_t buffer[256];
        buffer[0] = L'\0';
        GetWindowTextW(hComponent, buffer, sizeof(buffer) / sizeof(buffer[0]));
        return Wstring_to_string(buffer);
    }
    /*改变颜色列表；*/
    void 颜色_组件新增(HWND component, COLORREF col, int types, bool opitaty, int fontsize, int x, int y)
    {
        ComponentAttributes arrgs = 组件_获取扩展属性(component);
        LPCTSTR getufcon = String_to_lpcwstr(arrgs.content);
        for (auto &info : yh_setting_color_com)
        {
            if (info.hwnd == component)
            {
                info.color = col;
                info.content = getufcon;
                return;
            }
        }
        yh_compoment_color newInfo = {component, types, col, x, y, arrgs.width, arrgs.height, opitaty, getufcon, fontsize};
        yh_setting_color_com.push_back(newInfo);
    }
    /*放置标签*/
    HWND 放置组件_标签(int x, int y, int width, int height, const wchar_t *text, COLORREF textcolor, bool opitatcy, int fontsize)
    {
        HWND hLabel = CreateWindow(
            TEXT("STATIC"),
            text,
            WS_VISIBLE | WS_CHILD,
            x, y, width, height,
            hWnd,
            NULL,
            GetModuleHandle(NULL),
            NULL);
        组件_隐藏(hLabel);
        颜色_组件新增(hLabel, textcolor, 1, opitatcy, fontsize, x, y);
        组件_状态_设置(hLabel, 0);
        return hLabel;
    }

    /*放置输入框*/
    HWND 放置组件_输入框(int x, int y, int width, int height, const wchar_t *placeholder = nullptr)
    {
        HWND hEntry = CreateWindow(
            TEXT("EDIT"),
            placeholder,
            WS_VISIBLE | WS_CHILD | WS_BORDER | ES_AUTOHSCROLL,
            x, y, width, height,
            hWnd,
            NULL,
            GetModuleHandle(NULL),
            NULL);
        return hEntry;
    }
    /*创建一个单选框，需要分组*/
    HWND 放置组件_单选框(int x, int y, int width, int height, const wchar_t *text, int groupId)
    {
        DWORD style = WS_VISIBLE | WS_CHILD | BS_AUTORADIOBUTTON;
        HWND hParent = hWnd;
        bool groupExists = false;
        for (const auto &group : radioButtonGroups)
        {
            if (group.groupId == groupId)
            {
                groupExists = true;
                break;
            }
        }
        if (!groupExists)
        {
            RadioButtonGroup newGroup;
            newGroup.groupId = groupId;
            style |= WS_GROUP;
            HWND hRadioButton = CreateWindow(
                TEXT("BUTTON"),
                text,
                style,
                x, y, width, height,
                hParent,
                NULL,
                GetModuleHandle(NULL),
                NULL);

            newGroup.firstRadioButton = hRadioButton;
            radioButtonGroups.push_back(newGroup);
            return hRadioButton;
        }
        else
        {
            HWND hRadioButton = CreateWindow(
                TEXT("BUTTON"),
                text,
                style,
                x, y, width, height,
                hParent,
                NULL,
                GetModuleHandle(NULL),
                NULL);
            return hRadioButton;
        }
    }
    /*判断按钮是否被禁用*/
    BOOL 按钮_是否被禁用(HWND hButton)
    {
        LONG_PTR style = GetWindowLongPtr(hButton, GWL_STYLE);
        if (!(style & WS_DISABLED))
        {
            return false;
        }
        else
        {
            return true;
        }
    }
    /*获取指定组别的选中对象*/
    HWND 单选框_获取选中(int groupId)
    {
        for (const auto &group : radioButtonGroups)
        {
            if (group.groupId == groupId)
            {
                HWND firstRadio = group.firstRadioButton;
                HWND currentRadio = firstRadio;
                while (currentRadio != NULL)
                {
                    if (SendMessage(currentRadio, BM_GETCHECK, 0, 0) == BST_CHECKED)
                    {
                        return currentRadio;
                    }
                    currentRadio = FindWindowEx(GetParent(currentRadio), currentRadio, TEXT("BUTTON"), NULL);
                }
                break;
            }
        }
        return NULL;
    }
    /*放置一个复选框，同样需要分组*/
    HWND 放置组件_复选框(int x, int y, int width, int height, const wchar_t *text, int groupId)
    {
        DWORD style = WS_VISIBLE | WS_CHILD | BS_AUTOCHECKBOX;
        HWND hParent = hWnd;
        bool groupExists = false;
        checkboxgroupmores justbillsn;
        for (const auto &group : checkBoxGroups)
        {
            if (group.groupId == groupId)
            {
                groupExists = true;
                break;
            }
        }
        if (!groupExists)
        {
            CheckBoxGroup newGroup;
            newGroup.groupId = groupId;
            style |= WS_GROUP;
            HWND hCheckBox = CreateWindow(
                TEXT("BUTTON"),
                text,
                style,
                x, y, width, height,
                hParent,
                NULL,
                GetModuleHandle(NULL),
                NULL);

            newGroup.firstCheckBox = hCheckBox;
            checkBoxGroups.push_back(newGroup);
            justbillsn.hWnd = hCheckBox;
            justbillsn.groupId = groupId;

            checkboxgroupmores_btn[groupId].push_back(justbillsn);
            return hCheckBox;
        }
        else
        {
            HWND hCheckBox = CreateWindow(
                TEXT("BUTTON"),
                text,
                style,
                x, y, width, height,
                hParent,
                NULL,
                GetModuleHandle(NULL),
                NULL);
            justbillsn.hWnd = hCheckBox;
            justbillsn.groupId = groupId;
            checkboxgroupmores_btn[groupId].push_back(justbillsn);
            return hCheckBox;
        }
    }
    /*给定某个单选框句柄，判断其是否被选中。选中则返回True*/
    bool 单选框_是否选中(HWND checks, int groupid)
    {
        HWND bxzz = 单选框_获取选中(groupid);
        if (checks == bxzz)
        {
            return true;
        }
        else
        {
            return false;
        }
    }
    /*给定组id，获取被选中的复选框*/
    vector<HWND> 复选框_获取选中(int groupId)
    {
        HWND hParent = hWnd;
        std::vector<HWND> checkedHandles;
        auto groupIter = checkboxgroupmores_btn.find(groupId);
        if (groupIter != checkboxgroupmores_btn.end())
        {
            const auto &group = groupIter->second;
            for (const checkboxgroupmores &button : group)
            {
                LRESULT checkState = SendMessage(button.hWnd, BM_GETCHECK, 0, 0);
                if (checkState == BST_CHECKED)
                {
                    checkedHandles.push_back(button.hWnd);
                }
            }
        }
        return checkedHandles;
    }
    /*请不要使用此方法*/
    bool 附_扩展_lsboolcheck(HWND comhwnd)
    {
        for (const delete_plus_trust &info : delete_plus_trust_shout)
        {
            if (info.hwnd_p == comhwnd)
            {
                return true;
            }
        }
        return false;
    }
    /*请不要使用此方法*/
    int 附_扩展_lsboolcheck2(HWND comhwnd)
    {
        int xplusauth = 10;
        for (const delete_plus_trust &info : delete_plus_trust_shout)
        {
            if (info.hwnd_p == comhwnd)
            {
                if (info.status == 0)
                {
                    xplusauth = 1;
                }
                else
                {
                    xplusauth = 0;
                }
                break;
            }
            else
            {
                xplusauth = 1;
            }
            break;
        }
        return xplusauth;
    }
    /*若要隐藏/显示标签或按钮请使用本方法.0显示;1隐藏*/
    void 组件_状态_设置(HWND comhwnd, int status)
    {
        delete_plus_trust fucdel;
        wstring classcom = 组件_获取类名(comhwnd);
        if (status == 1)
        {
            if (classcom == L"Button")
            {
                按钮_禁用(comhwnd);
            }
        }
        else if (status == 0)
        {
            if (classcom == L"Button")
            {
                if (按钮_是否被禁用(comhwnd))
                {
                    按钮_解除禁用(comhwnd);
                }
            }
        }
        fucdel = {comhwnd, status};
        if (附_扩展_lsboolcheck(fucdel.hwnd_p))
        {
            for (delete_plus_trust &info : delete_plus_trust_shout)
            {
                if (info.hwnd_p == fucdel.hwnd_p)
                {
                    info.status = fucdel.status;
                    break;
                }
            }
        }
        else
        {
            delete_plus_trust_shout.push_back(fucdel);
        }
        InvalidateRect(hWnd, NULL, TRUE);
        UpdateWindow(hWnd);
    }
    /*若要获取按钮或标签状态请使用本方法.True隐藏，false显示;None无此组件*/
    BOOL 组件_状态_获取(HWND comhwnd)
    {
        bool chekcu = false;
        if (附_扩展_lsboolcheck(comhwnd))
        {
            for (delete_plus_trust &info : delete_plus_trust_shout)
            {
                if (info.hwnd_p == comhwnd)
                {
                    if (info.status == 0)
                    {
                        chekcu = false;
                    }
                    else
                    {
                        chekcu = true;
                    }
                    break;
                }
            }
        }
        else
        {
            chekcu = NULL;
        }
        return chekcu;
    }
    /*初始化菜单栏*/
    HMENU 菜单栏_初始化()
    {
        HMENU hMenu = CreateMenu();
        return hMenu;
    }
    /*创建首级菜单*/
    HMENU 菜单栏_创建首级菜单()
    {
        HMENU hSubMenu = CreatePopupMenu();
        return hSubMenu;
    }
    /*为首级菜单创建子级菜单:首级菜单句柄,子级菜单ID，文本*/
    void 菜单栏_创建子级菜单(HMENU hSubMenu, int id, LPCUWSTR text)
    {
        AppendMenu(hSubMenu, MF_STRING, id, text);
    }
    /*将首级菜单添加到菜单栏：菜单栏句柄，首级菜单句柄，首级菜单显示文本*/
    void 菜单栏_首级菜单显示(HMENU hMenu, HMENU hSubMenu, LPCUWSTR text)
    {
        AppendMenu(hMenu, MF_POPUP, (UINT_PTR)hSubMenu, text);
    }
    /*最后一步，关联菜单栏至窗口*/
    void 菜单栏_显示(HMENU hMenu)
    {
        SetMenu(hWnd, hMenu);
    }
    /*为菜单项绑定事件：子级菜单ID；回调事件*/
    void 菜单栏_绑定事件(int id, ButtonCallback callback)
    {
        menuCommandHandlers[id] = callback;
    }
    /*获取输入框内容*/
    wstring 输入框_获取内容(HWND hEntry)
    {
        int textLength = (int)SendMessage(hEntry, WM_GETTEXTLENGTH, 0, 0);
        if (textLength > 0)
        {
            std::wstring text(textLength + 1, L'\0');
            SendMessage(hEntry, WM_GETTEXT, (WPARAM)(textLength + 1), (LPARAM)&text[0]);
            text.resize(textLength);
            return text;
        }
        return std::wstring();
    }

    /*设置窗口透明度的方法，参数alpha取值范围是0（完全透明）到255（完全不透明）*/
    void 窗口_设置透明度(BYTE alpha)
    {
        SetLayeredWindowAttributes(hWnd, 0, alpha, LWA_ALPHA);
    }
    /*设置组件字体-输入框*/
    void 设置字体_输入框(HWND hEdit, LPCUWSTR name, int size)
    {
        LOGFONT lf = {0};
        lstrcpyW(lf.lfFaceName, name);
        lf.lfHeight = -MulDiv(size, GetDeviceCaps(GetDC(NULL), LOGPIXELSY), 72);
        lf.lfWeight = FW_BOLD;

        HFONT hFont = CreateFontIndirectW(&lf);
        if (hFont == NULL)
        {
            MessageBox(NULL, L"创建字体失败，请检查系统资源或字体设置", L"字体设置错误", MB_OK | MB_ICONERROR);
            return;
        }
        LRESULT result = SendMessageW(hEdit, WM_SETFONT, (WPARAM)hFont, TRUE);
        if (result == 0)
        {
            MessageBox(NULL, L"设置字体到编辑框时失败，请检查编辑框状态", L"字体设置错误", MB_OK | MB_ICONERROR);
            DeleteObject(hFont);
        }
    }
    /*label设置新内容*/
    void 更改_标签内容(HWND hLabel, const TCHAR *text, COLORREF textcolor)
    {
        HFONT hFont = CreateFont(
            24,                       // 高度（字号），以逻辑单位表示
            0,                        // 平均字符宽度
            0,                        // 字符角度
            0,                        // 字体角度
            FW_NORMAL,                // 字体粗细
            FALSE,                    // 斜体
            FALSE,                    // 下划线
            FALSE,                    // 删除线
            ANSI_CHARSET,             // 字符集
            OUT_DEFAULT_PRECIS,       // 输出精度
            CLIP_DEFAULT_PRECIS,      // 裁剪精度
            DEFAULT_QUALITY,          // 输出质量
            DEFAULT_PITCH | FF_SWISS, // 间距和字体族
            L"宋体");                 // 字体名称

        SendMessage(hLabel, WM_SETFONT, (WPARAM)hFont, TRUE);

        HDC hdc = GetDC(hWnd);
        HFONT hOldFont = (HFONT)SelectObject(hdc, hFont);
        SetWindowText(hLabel, text);
        RECT rect;
        GetClientRect(hLabel, &rect);
        InvalidateRect(hLabel, &rect, TRUE);
        UpdateWindow(hLabel);
        SelectObject(hdc, hOldFont);
        ReleaseDC(hWnd, hdc);
        DeleteObject(hFont);
        vector<int> justbi = 组件_获取位置属性_plugs(hLabel);
        颜色_组件新增(hLabel, textcolor, 1, true, 32, justbi[0], justbi[1]);
        InvalidateRect(hWnd, NULL, TRUE);
        UpdateWindow(hWnd);
    }

    /*获取组件位置2*/
    RECT 组件_获取位置(HWND hComponent)
    {
        RECT rect;
        GetWindowRect(hComponent, &rect);
        return rect;
    }
    /*为指定组件添加点击事件回调函数，参数为组件句柄和对应的回调函数*/
    void 组件_绑定事件(HWND hComponent, ButtonCallback callback)
    {
        buttonCallbacks[hComponent] = callback;
    }
    /*获取单个组件句柄类名*/
    wstring 组件_获取类名(HWND hwnd2)
    {
        const int MAX_CLASS_NAME = 256;
        wchar_t className[MAX_CLASS_NAME];
        if (GetClassName(hwnd2, className, MAX_CLASS_NAME) == 0)
        {
            return L"";
        }
        return std::wstring(className);
    }
    void 附_扩展_组件元判断(HWND testz)
    {
        wstring classname = 组件_获取类名(testz);
        if (classname == L"Button")
        {
            组件_隐藏(testz);
        }
        else if (classname == L"Static")
        {
            组件_隐藏(testz);
        }
    }
    /*隐藏组件*/
    void 组件_隐藏(HWND hComponent)
    {
        ShowWindow(hComponent, SW_HIDE);
        InvalidateRect(hWnd, NULL, TRUE);
        UpdateWindow(hWnd);
        PostMessage(hWnd, WM_FUCKTHE_COMM_HIDEER, 0, 0);
    }
    /*显示组件*/
    void 组件_显示(HWND hComponent)
    {
        ShowWindow(hComponent, SW_SHOW);
        InvalidateRect(hWnd, NULL, TRUE);
        UpdateWindow(hWnd);
        PostMessage(hWnd, WM_FUCKTHE_COMM_HIDEER, 0, 0);
    }

    /*删除指定组件的函数，传入组件的窗口句柄*/
    void 组件_删除(HWND hComponent)
    {
        if (hComponent && IsWindow(hComponent))
        {
            HBRUSH hBrush = (HBRUSH)GetClassLongPtr(hComponent, GCLP_HBRBACKGROUND);
            if (hBrush)
            {
                DeleteObject(hBrush);
            }
            DestroyWindow(hComponent);
            buttonCallbacks.erase(hComponent);
            hComponent = NULL;
            InvalidateRect(hWnd, NULL, TRUE);
            UpdateWindow(hWnd);
        }
    }

    /*禁止指定按钮可点击状态，传入按钮的窗口句柄*/
    void 按钮_禁用(HWND hButton)
    {
        LONG_PTR style = GetWindowLongPtr(hButton, GWL_STYLE);
        style |= WS_DISABLED;
        SetWindowLongPtr(hButton, GWL_STYLE, style);
        InvalidateRect(hWnd, NULL, TRUE);
        UpdateWindow(hWnd);
    }

    /*恢复指定按钮可点击状态，传入按钮的窗口句柄*/
    void 按钮_解除禁用(HWND hButton)
    {
        LONG_PTR style = GetWindowLongPtr(hButton, GWL_STYLE);
        style &= ~WS_DISABLED;
        SetWindowLongPtr(hButton, GWL_STYLE, style);
        InvalidateRect(hWnd, NULL, TRUE);
        UpdateWindow(hWnd);
    }
    /*放置列表框,为true则为多选，反之单选*/
    HWND 放置组件_列表框(int x, int y, int width, int height, const bool getmore)
    {
        DWORD listBoxStyle = WS_VISIBLE | WS_CHILD | WS_BORDER;
        if (getmore)
        {
            listBoxStyle |= LBS_MULTIPLESEL;
        }
        HWND hListBox = CreateWindow(
            TEXT("LISTBOX"),
            NULL,
            listBoxStyle,
            x, y, width, height,
            hWnd,
            NULL,
            GetModuleHandle(NULL),
            NULL);
        return hListBox;
    }
    /*设置窗口关闭前询问框是否打开*/
    void 设置_关闭询问框显示(bool show)
    {
        m_showCloseConfirmation = show;
    }

    /*设置窗口关闭前询问框内容*/
    void 设置_关闭询问框内容(LPCTSTR content)
    {
        m_closeConfirmationContent = content;
    }
    /*批量加入内容至列表框*/
    void 列表框_添加批量项(HWND hListBox, const std::vector<std::wstring> &items)
    {
        for (const auto &item : items)
        {
            SendMessageW(hListBox, LB_ADDSTRING, 0, (LPARAM)item.c_str());
        }
    }
    /*单个内容加入至列表框*/
    void 列表框_添加单项(HWND hListBox, const wstring addcon)
    {
        SendMessageW(hListBox, LB_ADDSTRING, 0, (LPARAM)addcon.c_str());
    }
    /*删除列表框中指定索引的项*/
    void 列表框_删除项(HWND hListBox, int index)
    {
        SendMessageW(hListBox, LB_DELETESTRING, index, 0);
    }
    /*获取当前选中内容(仅支持单选)*/
    wstring 列表框_获取选中内容(HWND hListBox)
    {
        int selectedIndex = (int)SendMessageW(hListBox, LB_GETCURSEL, 0, 0);
        if (selectedIndex != LB_ERR)
        {
            int textLength = (int)SendMessageW(hListBox, LB_GETTEXTLEN, selectedIndex, 0);
            std::wstring selectedText(textLength + 1, L'\0');
            SendMessageW(hListBox, LB_GETTEXT, selectedIndex, (LPARAM)&selectedText[0]);
            selectedText.resize(textLength);
            return selectedText;
        }
        return std::wstring();
    }
    /*获取多选形式下所有被选中项的索引*/
    vector<int> 列表框_获取多选索引(HWND hListBox)
    {
        std::vector<int> selectedIndices;
        int count = SendMessage(hListBox, LB_GETSELCOUNT, 0, 0);
        if (count > 0)
        {
            LPINT selectedItems = new INT[count];
            SendMessage(hListBox, LB_GETSELITEMS, count, (LPARAM)selectedItems);
            for (int i = 0; i < count; ++i)
            {
                selectedIndices.push_back(selectedItems[i]);
            }
            delete[] selectedItems;
        }
        return selectedIndices;
    }
    /*获取多选形式下指定索引的内容*/
    vector<std::string> 列表框_获取多选索引_内容(HWND hListBox, const std::vector<int> &indices)
    {
        std::vector<std::string> selectedContents;
        std::vector<char> buffer(100);
        for (int index : indices)
        {
            SendMessage(hListBox, LB_GETTEXT, index, (LPARAM)buffer.data());
            selectedContents.push_back(buffer.data());
        }
        return selectedContents;
    }
    /*获取当前选中项索引*/
    int 列表框_获取选中索引(HWND hListBox)
    {
        int index = SendMessage(hListBox, LB_GETCURSEL, 0, 0);
        return index;
    }
    /*进度条——放置：x，y，width，height，范围(x-y),默认进度(z)*/
    HWND 放置组件_进度条(int x, int y, int width, int height, int minRange = 0, int maxRange = 100, int initialPos = 0)
    {
        HWND hProgressBar = CreateWindowEx(0, PROGRESS_CLASS, NULL,
                                           WS_CHILD | WS_VISIBLE | PBS_SMOOTH,
                                           x, y, width, height,
                                           hWnd, (HMENU)1,
                                           GetModuleHandle(NULL), NULL);
        SendMessage(hProgressBar, PBM_SETRANGE, 0, MAKELPARAM(minRange, maxRange));
        SendMessage(hProgressBar, PBM_SETPOS, initialPos, 0);
        return hProgressBar;
    }
    /*修改进度条进度*/
    void 进度条_修改进度(HWND progress, int now)
    {
        SendMessage(progress, PBM_SETPOS, now, 0);
    }
    /*获取当前进度条进度返回int*/
    int 进度条_获取进度(HWND hProgressBar)
    {
        return (int)SendMessage(hProgressBar, PBM_GETPOS, 0, 0);
    }
    /*这将会放置一个组合框*/
    HWND 放置组件_组合框(int x, int y, int width, int height)
    {
        return CreateWindowEx(0, L"COMBOBOX", NULL,
                              WS_CHILD | WS_VISIBLE | CBS_DROPDOWNLIST,
                              x, y, width, height,
                              hWnd, (HMENU)802301,
                              (HINSTANCE)GetWindowLongPtr(hWnd, GWLP_HINSTANCE), NULL);
    }
    /*获取组合框选中的项*/
    int 组合框_获取选中(HWND hComboBox)
    {
        return SendMessage(hComboBox, CB_GETCURSEL, 0, 0);
    }

    /*添加项至组合框*/
    void 组合框_添加项(HWND hComboBox, const wchar_t *item)
    {
        SendMessage(hComboBox, CB_ADDSTRING, 0, (LPARAM)item);
    }

    /*删除项于组合框中*/
    void 组合框_删除项(HWND hComboBox, int index)
    {
        SendMessage(hComboBox, CB_DELETESTRING, index, 0);
    }
    /*创建一个日期框*/
    HWND 放置组件_日期框(int x, int y, int width, int height)
    {
        return CreateWindowEx(
            0,
            DATETIMEPICK_CLASS,
            NULL,
            WS_CHILD | WS_VISIBLE | DTS_SHORTDATEFORMAT,
            x, y, width, height,
            hWnd,
            NULL,
            (HINSTANCE)GetWindowLongPtr(hWnd, GWLP_HINSTANCE),
            NULL);
    }

    /*获取日期框中的时间*/
    string 日期框_获取日期(HWND hDatePicker)
    {
        SYSTEMTIME st;
        SendMessage(hDatePicker, DTM_GETSYSTEMTIME, 0, (LPARAM)&st);
        return System_to_string(st);
    }
    /*这将会创建一个选择夹*/
    HWND 放置组件_选择夹(int x, int y, int width, int height)
    {
        HWND hWndParent = hWnd;
        INITCOMMONCONTROLSEX icex;
        icex.dwSize = sizeof(INITCOMMONCONTROLSEX);
        icex.dwICC = ICC_TAB_CLASSES;
        InitCommonControlsEx(&icex);
        return CreateWindowEx(0, WC_TABCONTROL, L"", WS_CHILD | WS_VISIBLE | WS_CLIPSIBLINGS | TCS_TABS | TCS_FIXEDWIDTH,
                              x, y, width, height, hWndParent, (HMENU)IDC_TAB, (HINSTANCE)GetWindowLongPtr(hWndParent, GWLP_HINSTANCE), NULL);
    }
    /*这将会为指定选择夹创建一个项，并返回索引请妥善保存*/
    int 选择夹_创建项(HWND hTabCtrl, const wchar_t *pszText)
    {
        TCITEM tie;
        tie.mask = TCIF_TEXT;
        tie.pszText = const_cast<wchar_t *>(pszText);
        tie.cchTextMax = wcslen(pszText);
        return TabCtrl_InsertItem(hTabCtrl, TabCtrl_GetItemCount(hTabCtrl), &tie);
    }
    /*这将会删除指定选择夹的指定索引项*/
    BOOL 选择夹_删除项(HWND hTabCtrl, int nItemIndex)
    {
        if (TabCtrl_DeleteItem(hTabCtrl, nItemIndex))
        {
            if (nItemIndex < tabChildWindows.size())
            {
                DestroyWindow(tabChildWindows[nItemIndex]);
                tabChildWindows.erase(tabChildWindows.begin() + nItemIndex);
            }
            return TRUE;
        }
        return FALSE;
    }
    /*这将会为指定选择夹的指定索引项隐藏*/
    BOOL 选择夹_隐藏项(HWND hTabCtrl, int nItemIndex)
    {
        TCITEM tie;
        tie.mask = TCIF_STATE;
        if (TabCtrl_GetItem(hTabCtrl, nItemIndex, &tie))
        {
            tie.dwState &= ~TCIS_BUTTONPRESSED;
            tie.dwStateMask = TCIS_BUTTONPRESSED;
            return TabCtrl_SetItem(hTabCtrl, nItemIndex, &tie);
        }
        return FALSE;
    }
    /*为选择夹添加组件*/
    HWND 选择夹_添加组件(HWND hTabCtrl, int nItemIndex, function<HWND()> func)
    {
        RECT tabRect;
        TabCtrl_AdjustRect(hTabCtrl, FALSE, &tabRect);
        HWND hChildWnd = CreateWindowEx(0, L"STATIC", L"", WS_CHILD | WS_VISIBLE | WS_CLIPSIBLINGS,
                                        tabRect.left, tabRect.top, tabRect.right - tabRect.left, tabRect.bottom - tabRect.top,
                                        hTabCtrl, NULL, (HINSTANCE)GetWindowLongPtr(hTabCtrl, GWLP_HINSTANCE), NULL);
        tabChildWindows.push_back(hChildWnd);
        HWND hwnd_may = func();
        SetParent(hwnd_may, hTabCtrl);
        bool found = false;
        std::vector<HWND> hwnd_may_vector = {hwnd_may};
        for (auto &item : tab_shout_open)
        {
            if (item.tab == hTabCtrl && item.tabid == nItemIndex)
            {
                item.compement.insert(item.compement.end(), hwnd_may_vector.begin(), hwnd_may_vector.end());
                found = true;
                break;
            }
        }
        if (!found)
        {
            geography_tab newinfo;
            newinfo.tab = hTabCtrl;
            newinfo.tabid = nItemIndex;
            newinfo.compement = hwnd_may_vector;
            tab_shout_open.push_back(newinfo);
        }
        NMHDR nmhdr;
        nmhdr.hwndFrom = hTabCtrl;
        nmhdr.idFrom = GetDlgCtrlID(hTabCtrl);
        nmhdr.code = TCN_SELCHANGE;
        SendMessage(hWnd, WM_NOTIFY, GetDlgCtrlID(hTabCtrl), (LPARAM)&nmhdr);
    }
    /*加载图片*/
    HBITMAP 资源_图片加载(const wchar_t *imagePath)
    {
        HBITMAP hBitmap = (HBITMAP)LoadImage(NULL, imagePath, IMAGE_BITMAP, 0, 0, LR_LOADFROMFILE);
        if (hBitmap == NULL)
        {
            MessageBox(NULL, L"加载失败", L"异常", MB_OK | MB_ICONERROR);
        }
        return hBitmap;
    }
    /*创建一个列表视图*/
    HWND 放置组件_列表视图(int x, int y, int width, int height)
    {
        HWND hParentWnd = hWnd;
        HWND hListView = CreateWindowEx(0, WC_LISTVIEW, L"",
                                        WS_CHILD | WS_VISIBLE | LVS_REPORT,
                                        x, y, width, height,
                                        hParentWnd, (HMENU)10054,
                                        (HINSTANCE)GetWindowLongPtr(hParentWnd, GWLP_HINSTANCE), NULL);
        if (hListView == NULL)
        {
            MessageBoxW(hParentWnd, L"异常的加载", L"异常", MB_OK | MB_ICONERROR);
        }
        return hListView;
    }
    /*为列表视图插入新列：句柄，索引，文本，宽*/
    void 列表视图_列_插入(HWND hListView, int columnIndex, const wchar_t *columnText, int columnWidth)
    {
        LVCOLUMNW lvc;
        lvc.mask = LVCF_TEXT | LVCF_WIDTH;
        lvc.cx = columnWidth;
        lvc.pszText = (LPWSTR)columnText;
        ListView_InsertColumn(hListView, columnIndex, &lvc);
    }
    /*为列表视图的指定列插入项*/
    void 列表视图_项_插入(HWND hListView, int itemIndex, const wchar_t *itemText)
    {
        LVITEMW lvi;
        lvi.mask = LVIF_TEXT;
        lvi.iItem = itemIndex;
        lvi.iSubItem = 0;
        lvi.pszText = (LPWSTR)itemText;
        ListView_InsertItem(hListView, &lvi);
    }

    /*为列表视图的指定列插入数据*/
    void 列表视图_子项_插入(HWND hListView, int itemIndex, int subItemIndex, const wchar_t *subItemText)
    {
        LVITEMW lvi;
        lvi.mask = LVIF_TEXT;
        lvi.iItem = itemIndex;
        lvi.iSubItem = subItemIndex;
        lvi.pszText = (LPWSTR)subItemText;
        ListView_SetItem(hListView, &lvi);
    }
    /*为列表视图删除列*/
    void 列表视图_列_删除(HWND hListView, int columnIndex)
    {
        ListView_DeleteColumn(hListView, columnIndex);
    }
    /*为列表视图删除项*/
    void 列表视图_项_删除(HWND hListView, int itemIndex)
    {
        ListView_DeleteItem(hListView, itemIndex);
    }
    /*为列表视图删除指定项的子项*/
    void 列表视图_子项_删除(HWND hListView, int itemIndex, int subItemIndex)
    {
        LVITEMW lvi;
        lvi.mask = LVIF_TEXT;
        lvi.iItem = itemIndex;
        lvi.iSubItem = subItemIndex;
        lvi.pszText = NULL;
        ListView_SetItem(hListView, &lvi);
    }
    /*为列表视图修改指定项的子项*/
    void 列表视图_子项_修改(HWND hListView, int itemIndex, int subItemIndex, const wchar_t *newText)
    {
        LVITEMW lvi;
        lvi.mask = LVIF_TEXT;
        lvi.iItem = itemIndex;
        lvi.iSubItem = subItemIndex;
        lvi.pszText = (LPWSTR)newText;
        ListView_SetItem(hListView, &lvi);
    }
    /*为列表视图修改指定项*/
    void 列表视图_项_修改(HWND hListView, int itemIndex, const wchar_t *newItemText)
    {
        LVITEMW lvi;
        lvi.mask = LVIF_TEXT;
        lvi.iItem = itemIndex;
        lvi.iSubItem = 0;
        lvi.pszText = (LPWSTR)newItemText;
        ListView_SetItem(hListView, &lvi);
    }
    /*为列表视图列修改*/
    void 列表视图_列_修改(HWND hListView, int columnIndex, const wchar_t *newColumnText, int newColumnWidth)
    {
        LVCOLUMNW lvc;
        lvc.mask = LVCF_TEXT | LVCF_WIDTH;
        lvc.cx = newColumnWidth;
        lvc.pszText = (LPWSTR)newColumnText;
        ListView_SetColumn(hListView, columnIndex, &lvc);
    }
    void 直接绘制_窗口(HDC hdc)
    {
        RECT clientRect;
        GetClientRect(hWnd, &clientRect);

        // 填充背景
        if (m_hBackgroundBrush)
        {
            FillRect(hdc, &clientRect, m_hBackgroundBrush);
        }
        else
        {
            HBRUSH hBrush = CreateSolidBrush(RGB(240, 240, 240));
            FillRect(hdc, &clientRect, hBrush);
            DeleteObject(hBrush);
        }

        // 设置高质量绘图
        SetStretchBltMode(hdc, HALFTONE);
        SetGraphicsMode(hdc, GM_ADVANCED);

        HFONT hFont = CreateFont(
            0, 0, 0, 0, FW_NORMAL, FALSE, FALSE, FALSE,
            DEFAULT_CHARSET, OUT_DEFAULT_PRECIS, CLIP_DEFAULT_PRECIS,
            ANTIALIASED_QUALITY, DEFAULT_PITCH | FF_SWISS, TEXT("Microsoft YaHei"));
        HFONT hOldFont = (HFONT)SelectObject(hdc, hFont);

        for (const auto &info : yh_setting_color_com)
        {
            if (GetParent(info.hwnd) != hWnd)
                continue;
            if (附_扩展_lsboolcheck2(info.hwnd) != 1)
                continue;

            SetTextColor(hdc, info.color == NULL ? RGB(0, 0, 0) : info.color);
            SetBkMode(hdc, info.opitacty ? TRANSPARENT : OPAQUE);

            HFONT hNewFont = CreateFont(
                -MulDiv(info.font_size, GetDeviceCaps(hdc, LOGPIXELSY), 72),
                0, 0, 0, FW_NORMAL, FALSE, FALSE, FALSE,
                DEFAULT_CHARSET, OUT_DEFAULT_PRECIS, CLIP_DEFAULT_PRECIS,
                ANTIALIASED_QUALITY, DEFAULT_PITCH | FF_SWISS, TEXT("Microsoft YaHei"));
            HFONT hOldNewFont = (HFONT)SelectObject(hdc, hNewFont);

            if (info.typecom == 1)
            {
                RECT textRect = {info.x, info.y, info.x + info.width, info.y + info.height};
                DrawTextW(hdc, info.content, -1, &textRect, DT_SINGLELINE | DT_VCENTER | DT_CENTER);
            }
            else if (info.typecom == 2)
            {
                RECT buttonRect = {info.x, info.y, info.x + info.width, info.y + info.height};

                HBRUSH hBrush;
                if (!按钮_是否被禁用(info.hwnd))
                {
                    hBrush = CreateSolidBrush(RGB(240, 240, 240));
                }
                else
                {
                    hBrush = CreateSolidBrush(RGB(200, 200, 200));
                }
                FillRect(hdc, &buttonRect, hBrush);
                DeleteObject(hBrush);

                SetBkMode(hdc, TRANSPARENT);
                DrawTextW(hdc, info.content, -1, &buttonRect, DT_SINGLELINE | DT_VCENTER | DT_CENTER);
            }

            SelectObject(hdc, hOldNewFont);
            DeleteObject(hNewFont);
        }

        SelectObject(hdc, hOldFont);
        DeleteObject(hFont);
    }

private:
    static LRESULT CALLBACK StaticWindowProc(HWND hWnd, UINT message, WPARAM wParam, LPARAM lParam)
    {
        熙鹤_GUI *pThis = reinterpret_cast<熙鹤_GUI *>(GetWindowLongPtr(hWnd, GWLP_USERDATA));
        if (pThis)
        {
            return pThis->WindowProc(hWnd, message, wParam, lParam);
        }
        else
        {
            return DefWindowProc(hWnd, message, wParam, lParam);
        }
    }
    HDC m_hMemDC;              // 内存DC
    HBITMAP m_hMemBitmap;      // 内存位图
    HBITMAP m_hOldBitmap;      // 旧位图
    int m_clientWidth;         // 客户区宽度
    int m_clientHeight;        // 客户区高度
    bool m_bufferingEnabled;   // 是否启用双缓冲
    HBRUSH m_hBackgroundBrush; // 背景画刷
    static LRESULT CALLBACK wnd_plugin_dragged(HWND hWnd23, UINT message, WPARAM wParam, LPARAM lParam)
    {
        switch (message)
        {
        case WM_LBUTTONDOWN:
            g_bDragging = true;
            g_ptOffset.x = LOWORD(lParam);
            g_ptOffset.y = HIWORD(lParam);
            SetCapture(hWnd23);
            break;
        case WM_MOUSEMOVE:
            if (g_bDragging)
            {
                int x = LOWORD(lParam);
                int y = HIWORD(lParam);
                RECT rect;
                GetWindowRect(hWnd23, &rect);
                int newX = rect.left + (x - g_ptOffset.x);
                int newY = rect.top + (y - g_ptOffset.y);
                MoveWindow(hWnd23, newX, newY, rect.right - rect.left, rect.bottom - rect.top, TRUE);
            }
            break;
        case WM_LBUTTONUP:
            g_bDragging = false;
            ReleaseCapture();
            break;
        default:
            return DefWindowProc(hWnd23, message, wParam, lParam);
        }
        return 0;
    }
    LRESULT CALLBACK WindowProc(HWND hWnd2, UINT message, WPARAM wParam, LPARAM lParam)
    {
        switch (message)
        {
        case WM_COMMAND:
        {
            if (HIWORD(wParam) == BN_CLICKED)
            {
                HWND hButton = (HWND)lParam;
                auto it = buttonCallbacks.find(hButton);
                if (it != buttonCallbacks.end())
                {
                    it->second();
                }
            }
            auto it = menuCommandHandlers.find(LOWORD(wParam));
            if (it != menuCommandHandlers.end())
            {
                MenuCommandHandler handler = it->second;
                handler();
            }

            InvalidateRect(hWnd, NULL, TRUE);
            UpdateWindow(hWnd);
            break;
        }
        case WM_PAINT:
        {
            PAINTSTRUCT ps;
            HDC hdc = BeginPaint(hWnd, &ps);

            if (m_bufferingEnabled && m_hMemDC)
            {
                // 获取客户区大小
                RECT clientRect;
                GetClientRect(hWnd, &clientRect);
                int width = clientRect.right - clientRect.left;
                int height = clientRect.bottom - clientRect.top;

                // 如果缓冲区大小不匹配，重新创建
                if (width != m_clientWidth || height != m_clientHeight)
                {
                    调整_缓冲区大小(width, height);
                }

                if (m_hMemDC)
                {
                    // 在内存DC中绘制
                    // 1. 先用背景色填充
                    RECT fullRect = {0, 0, width, height};
                    if (m_hBackgroundBrush)
                    {
                        FillRect(m_hMemDC, &fullRect, m_hBackgroundBrush);
                    }
                    else
                    {
                        HBRUSH hBrush = CreateSolidBrush(RGB(240, 240, 240));
                        FillRect(m_hMemDC, &fullRect, hBrush);
                        DeleteObject(hBrush);
                    }

                    // 2. 设置高质量的绘图模式
                    SetStretchBltMode(m_hMemDC, HALFTONE);
                    SetBrushOrgEx(m_hMemDC, 0, 0, nullptr);

                    // 3. 设置字体渲染质量
                    SetTextAlign(m_hMemDC, GetTextAlign(m_hMemDC) | TA_UPDATECP);

                    // 4. 绘制所有自定义组件
                    HFONT hFont = CreateFont(
                        0, 0, 0, 0, FW_NORMAL, FALSE, FALSE, FALSE,
                        DEFAULT_CHARSET, OUT_DEFAULT_PRECIS, CLIP_DEFAULT_PRECIS,
                        ANTIALIASED_QUALITY, // 使用抗锯齿字体渲染
                        DEFAULT_PITCH | FF_SWISS, TEXT("Microsoft YaHei"));
                    HFONT hOldFont = (HFONT)SelectObject(m_hMemDC, hFont);

                    // 启用窗口扩展（用于更好的绘制质量）
                    SetGraphicsMode(m_hMemDC, GM_ADVANCED);

                    for (const auto &info : yh_setting_color_com)
                    {
                        // 检查组件是否应该绘制
                        if (GetParent(info.hwnd) != hWnd)
                            continue;
                        if (附_扩展_lsboolcheck2(info.hwnd) != 1)
                            continue;

                        // 设置文字颜色
                        COLORREF textColor = (info.color == NULL) ? RGB(0, 0, 0) : info.color;
                        SetTextColor(m_hMemDC, textColor);

                        // 设置背景模式
                        SetBkMode(m_hMemDC, info.opitacty ? TRANSPARENT : OPAQUE);
                        if (!info.opitacty)
                        {
                            SetBkColor(m_hMemDC, RGB(240, 240, 240));
                        }

                        // 创建字体
                        HFONT hNewFont = CreateFont(
                            -MulDiv(info.font_size, GetDeviceCaps(m_hMemDC, LOGPIXELSY), 72),
                            0, 0, 0, FW_NORMAL, FALSE, FALSE, FALSE,
                            DEFAULT_CHARSET, OUT_DEFAULT_PRECIS, CLIP_DEFAULT_PRECIS,
                            ANTIALIASED_QUALITY, // 抗锯齿
                            DEFAULT_PITCH | FF_SWISS, TEXT("Microsoft YaHei"));
                        HFONT hOldNewFont = (HFONT)SelectObject(m_hMemDC, hNewFont);

                        if (info.typecom == 1)
                        { // 标签
                            RECT textRect = {info.x, info.y, info.x + info.width, info.y + info.height};
                            DrawTextW(m_hMemDC, info.content, -1, &textRect,
                                      DT_SINGLELINE | DT_VCENTER | DT_CENTER);
                        }
                        else if (info.typecom == 2)
                        { // 按钮
                            RECT buttonRect = {info.x, info.y, info.x + info.width, info.y + info.height};

                            // 绘制圆角按钮
                            HRGN hRgn = CreateRoundRectRgn(info.x, info.y,
                                                           info.x + info.width,
                                                           info.y + info.height, 8, 8);
                            SelectClipRgn(m_hMemDC, hRgn);

                            // 按钮背景
                            HBRUSH hBrush;
                            if (!按钮_是否被禁用(info.hwnd))
                            {
                                // 检查鼠标悬停
                                POINT pt;
                                GetCursorPos(&pt);
                                ScreenToClient(hWnd, &pt);
                                BOOL isHovering = (pt.x >= info.x && pt.x <= info.x + info.width &&
                                                   pt.y >= info.y && pt.y <= info.y + info.height);

                                if (isHovering)
                                {
                                    hBrush = CreateSolidBrush(RGB(235, 245, 255)); // 悬停效果
                                }
                                else
                                {
                                    hBrush = CreateSolidBrush(RGB(240, 240, 240)); // 正常状态
                                }
                            }
                            else
                            {
                                hBrush = CreateSolidBrush(RGB(200, 200, 200)); // 禁用状态
                            }

                            FillRect(m_hMemDC, &buttonRect, hBrush);
                            DeleteObject(hBrush);

                            // 绘制边框
                            HPEN hPen = CreatePen(PS_SOLID, 1, RGB(170, 170, 170));
                            HPEN hOldPen = (HPEN)SelectObject(m_hMemDC, hPen);
                            FrameRect(m_hMemDC, &buttonRect, (HBRUSH)GetStockObject(NULL_BRUSH));

                            // 绘制按钮文字
                            SetBkMode(m_hMemDC, TRANSPARENT);
                            SetTextColor(m_hMemDC, textColor);
                            DrawTextW(m_hMemDC, info.content, -1, &buttonRect,
                                      DT_SINGLELINE | DT_VCENTER | DT_CENTER);

                            SelectObject(m_hMemDC, hOldPen);
                            DeleteObject(hPen);
                            SelectClipRgn(m_hMemDC, NULL);
                            DeleteObject(hRgn);
                        }

                        SelectObject(m_hMemDC, hOldNewFont);
                        DeleteObject(hNewFont);
                    }

                    // 恢复字体
                    SelectObject(m_hMemDC, hOldFont);
                    DeleteObject(hFont);

                    // 5. 将内存DC的内容一次性复制到屏幕
                    BitBlt(hdc, 0, 0, m_clientWidth, m_clientHeight, m_hMemDC, 0, 0, SRCCOPY);
                }
                else
                {
                    // 缓冲区创建失败，回退到直接绘制
                    直接绘制_窗口(hdc);
                }
            }
            else
            {
                // 不使用双缓冲，直接绘制
                直接绘制_窗口(hdc);
            }

            EndPaint(hWnd, &ps);
            break;
        }
        case WM_CREATE:
        {
            RECT clientRect;
            GetClientRect(hWnd, &clientRect);
            调整_缓冲区大小(clientRect.right - clientRect.left,
                            clientRect.bottom - clientRect.top);
            return 0;
        }
        case WM_SIZE:
        {
            if (m_bufferingEnabled)
            {
                int width = LOWORD(lParam);
                int height = HIWORD(lParam);
                调整_缓冲区大小(width, height);
            }
            return DefWindowProc(hWnd, message, wParam, lParam);
        }
        case WM_FUCKTHE_COMM_HIDEER:
        {
            HWND hComponentToHide = (HWND)wParam;
            if (hComponentToHide && IsWindow(hComponentToHide))
            {
                std::wcout << L"未知的数据传输异常 " << hComponentToHide << std::endl;
                ShowWindow(hComponentToHide, SW_HIDE);
                InvalidateRect(hComponentToHide, NULL, TRUE);
                UpdateWindow(hComponentToHide);
            }
            break;
        }
        case WM_LBUTTONDOWN:
        {
            int xPos = LOWORD(lParam);
            int yPos = HIWORD(lParam);
            POINT pt = {xPos, yPos};
            for (const auto &info : yh_setting_color_com)
            {
                RECT rect;
                rect.left = info.x;
                rect.top = info.y;
                rect.right = info.x + info.width;
                rect.bottom = info.y + info.height;

                if (PtInRect(&rect, pt))
                {
                    HWND hButton = info.hwnd;
                    auto it = buttonCallbacks.find(hButton);
                    LONG_PTR style = GetWindowLongPtr(hButton, GWL_STYLE);
                    if (!按钮_是否被禁用(hButton))
                    {
                        if (it != buttonCallbacks.end())
                        {

                            it->second();
                        }
                    }
                }
            }
            break;
        }
        case WM_NOTIFY:
        {
            LPNMHDR pnmh = (LPNMHDR)lParam;
            if (((LPNMHDR)lParam)->code == TCN_SELCHANGE && ((LPNMHDR)lParam)->idFrom == IDC_TAB)
            {
                HWND hTab = (HWND)pnmh->hwndFrom;
                int iSelectedTab = TabCtrl_GetCurSel(hTab);
                for (const auto &info : tab_shout_open)
                {
                    if (info.tab == hTab)
                    {
                        if (info.tabid == iSelectedTab)
                        {
                            for (const auto &info2 : info.compement)
                            {
                                ShowWindow(info2, SW_SHOW);
                            }
                        }
                        else
                        {
                            for (const auto &info2 : info.compement)
                            {
                                ShowWindow(info2, SW_HIDE);
                            }
                        }
                    }
                }
            }
            break;
        }
        case WM_DESTROY:
        { // 退出时
            PostQuitMessage(0);
            return 0;
        }

        case WM_CLOSE:
        {
            if (m_showCloseConfirmation)
            {
                int result = MessageBox(hWnd, m_closeConfirmationContent, L"确认关闭", MB_YESNO | MB_ICONQUESTION);
                if (result == IDYES)
                {
                    DestroyWindow(hWnd);
                }
                else
                {
                    return 0;
                }
            }
            else
            {
                DestroyWindow(hWnd);
            }
            break;
        }
        case WM_NCHITTEST:
        {
            if (m_window_close_canby_abovevar)
            {
                LRESULT hit = DefWindowProc(hWnd, message, wParam, lParam);
                if (hit == HTCLIENT)
                    return HTCAPTION;
                return hit;
            }
        }
        case WM_DROPFILES:
        {
            HDROP hDrop = (HDROP)wParam;
            if (附_文件_拖动_设置(hWnd2, hDrop))
            {
            }
        }
        default:
            return DefWindowProc(hWnd2, message, wParam, lParam);
        }
        return 0;
    }

    std::wstring string_to_wstring(const std::string &str)
    {
        int size_needed = MultiByteToWideChar(CP_UTF8, 0, &str[0], (int)str.size(), NULL, 0);
        std::wstring wstr(size_needed, L'\0');
        MultiByteToWideChar(CP_UTF8, 0, &str[0], (int)str.size(), &wstr[0], size_needed);
        return wstr;
    }

    wchar_t *string_to_wchar(const std::string &str)
    {
        int size_needed = MultiByteToWideChar(CP_UTF8, 0, &str[0], (int)str.size(), NULL, 0);
        wchar_t *wstr = new wchar_t[size_needed];
        MultiByteToWideChar(CP_UTF8, 0, &str[0], (int)str.size(), wstr, size_needed);
        return wstr;
    }

    string generate_random_string(size_t length)
    {
        const string CHARACTERS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz";
        random_device random_device;
        mt19937 generator(random_device());
        uniform_int_distribution<> distribution(0, CHARACTERS.size() - 1);

        string random_string;
        for (size_t i = 0; i < length; ++i)
        {
            random_string += CHARACTERS[distribution(generator)];
        }
        return random_string;
    }

    HWND hWnd;
    WCHAR *filePath_hexso = nullptr;
    std::map<HWND, ButtonCallback> buttonCallbacks;
    bool m_showCloseConfirmation = false;                     // 默认显示关闭询问框
    bool m_window_close_canby_abovevar = false;               // 支持拖动标题栏下移动窗口
    LPCTSTR m_closeConfirmationContent = L"确认关闭窗口吗？"; // 默认提示内容
    struct yh_compoment_color
    {
        HWND hwnd;        // 句柄区分其它
        int typecom;      // 组件类型
        COLORREF color;   // 组件对应的颜色
        int x;            // 横坐标
        int y;            // 纵坐标
        int width;        // 宽
        int height;       // 高
        bool opitacty;    // 是否透明
        LPCUWSTR content; // 内容
        int font_size;    // 字体大小
    };
    wchar_t *classOfTheGuiWindow; // 预加载类名
    vector<yh_compoment_color> yh_setting_color_com;
    struct RadioButtonGroup
    {
        int groupId;
        HWND firstRadioButton;
    };

    // 全局变量，用于存储所有的单选框分组信息
    std::vector<RadioButtonGroup> radioButtonGroups;
    struct CheckBoxGroup
    {
        int groupId;
        HWND firstCheckBox;
    };

    std::vector<CheckBoxGroup> checkBoxGroups;
    struct checkboxgroupmores
    {
        HWND hWnd;
        int groupId;
    };
    std::unordered_map<int, std::vector<checkboxgroupmores>> checkboxgroupmores_btn;
    const char *Filter_choose_b(const std::vector<std::string> *extensions = nullptr)
    {
        if (extensions == nullptr)
        {
            const char *guil = "All Files (*.*)\0*.*\0";
            return guil;
        }
        std::string filter;
        for (const auto &ext : *extensions)
        {
            filter += std::string(ext) + " Files (*." + ext + ")\0*." + ext + "\0";
        }
        filter += "All Files (*.*)\0*.*\0";
        const char *filter2 = filter.c_str();
        return filter2;
    }
    std::vector<HWND> tabChildWindows;
    struct geography_tab
    {
        HWND tab;               // 记录标签页句柄
        int tabid;              // 记录其项索引
        vector<HWND> compement; // 所有放在此索引里的组件句柄
    };
    vector<geography_tab> tab_shout_open;
    struct delete_plus_trust
    {
        HWND hwnd_p; // 记录句柄
        int status;  // 记录其状态，0显示，1隐藏
    };
    vector<delete_plus_trust> delete_plus_trust_shout;
};