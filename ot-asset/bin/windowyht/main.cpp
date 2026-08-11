#include "yhmolk.h"

熙鹤_GUI* g_pGUI = nullptr;
HWND g_hLabel, g_hProgress, g_hListBox, g_hCombo, g_hDatePicker, g_hListView;
HWND g_hEditInput, g_hCheckBox1, g_hCheckBox2, g_hRadio1, g_hRadio2;

// 更新标签显示当前时间
void UpdateTimeLabel() {
    SYSTEMTIME st;
    GetLocalTime(&st);
    wchar_t timeStr[128];
    wsprintfW(timeStr, L"当前时间: %04d-%02d-%02d %02d:%02d:%02d", 
              st.wYear, st.wMonth, st.wDay, st.wHour, st.wMinute, st.wSecond);
    g_pGUI->更改_标签内容(g_hLabel, timeStr, RGB(0, 100, 255));
}

// 按钮点击回调
void OnButtonClick() {
    g_pGUI->消息框(L"提示", L"按钮被点击了！");
}

// 菜单回调
void OnMenuOpen() {
    g_pGUI->消息框(L"菜单", L"点击了「打开」菜单");
}

void OnMenuExit() {
    if (MessageBoxA(NULL, "确定要退出吗？", "确认", MB_YESNO) == IDYES) {
        g_pGUI->销毁窗口();
    }
}

// 获取输入框内容并显示
void OnGetInputClick() {
    wstring content = g_pGUI->输入框_获取内容(g_hEditInput);
    if (!content.empty()) {
        wstring msg = L"输入内容: " + content;
        g_pGUI->消息框(L"输入内容", msg.c_str());
    } else {
        g_pGUI->消息框(L"提示", L"输入框为空");
    }
}

// 清空输入框
void OnClearInputClick() {
    g_pGUI->输入框_清空内容(g_hEditInput);
    g_pGUI->消息框(L"提示", L"输入框已清空");
}

// 进度条 +1
void OnProgressAdd() {
    int now = g_pGUI->进度条_获取进度(g_hProgress);
    if (now < 100) {
        g_pGUI->进度条_修改进度(g_hProgress, now + 10);
    }
}

// 进度条 -1
void OnProgressSub() {
    int now = g_pGUI->进度条_获取进度(g_hProgress);
    if (now > 0) {
        g_pGUI->进度条_修改进度(g_hProgress, now - 10);
    }
}

// 列表框添加内容
void OnListAddClick() {
    wstring input = g_pGUI->输入框_获取内容(g_hEditInput);
    if (!input.empty()) {
        g_pGUI->列表框_添加单项(g_hListBox, input);
    } else {
        g_pGUI->列表框_添加单项(g_hListBox, L"新项目");
    }
}

// 获取列表框选中内容
void OnListGetClick() {
    if (g_pGUI->列表框_获取选中索引(g_hListBox) != -1) {
        wstring selected = g_pGUI->列表框_获取选中内容(g_hListBox);
        wstring msg = L"选中: " + selected;
        g_pGUI->消息框(L"列表框", msg.c_str());
    } else {
        g_pGUI->消息框(L"列表框", L"未选中任何项");
    }
}

// 组合框选中事件
void OnComboSelect() {
    int idx = g_pGUI->组合框_获取选中(g_hCombo);
    if (idx != -1) {
        wchar_t item[64];
        SendMessage(g_hCombo, CB_GETLBTEXT, idx, (LPARAM)item);
        wstring msg = L"选中: " + wstring(item);
        g_pGUI->消息框(L"组合框", msg.c_str());
    }
}

// 日期选择事件
void OnDateSelect() {
    string date = g_pGUI->日期框_获取日期(g_hDatePicker);
    g_pGUI->消息框(L"日期", String_to_lpcwstr(date));
}

// 复选框状态显示
void OnCheckStatus() {
    vector<HWND> checked = g_pGUI->复选框_获取选中(1);
    wstring msg = L"已选中的复选框: ";
    if (checked.empty()) {
        msg += L"无";
    } else {
        for (size_t i = 0; i < checked.size(); i++) {
            if (checked[i] == g_hCheckBox1) msg += L"选项1 ";
            if (checked[i] == g_hCheckBox2) msg += L"选项2 ";
        }
    }
    g_pGUI->消息框(L"复选框状态", msg.c_str());
}

// 单选框状态
void OnRadioStatus() {
    HWND selected = g_pGUI->单选框_获取选中(2);
    if (selected == g_hRadio1) {
        g_pGUI->消息框(L"单选框", L"选中: 选项A");
    } else if (selected == g_hRadio2) {
        g_pGUI->消息框(L"单选框", L"选中: 选项B");
    } else {
        g_pGUI->消息框(L"单选框", L"未选中");
    }
}

// 列表视图演示
void InitListView() {
    g_hListView = g_pGUI->放置组件_列表视图(520, 80, 250, 200);
    g_pGUI->列表视图_列_插入(g_hListView, 0, L"姓名", 100);
    g_pGUI->列表视图_列_插入(g_hListView, 1, L"年龄", 80);
    g_pGUI->列表视图_列_插入(g_hListView, 2, L"城市", 100);
    
    g_pGUI->列表视图_项_插入(g_hListView, 0, L"张三");
    g_pGUI->列表视图_子项_插入(g_hListView, 0, 1, L"25");
    g_pGUI->列表视图_子项_插入(g_hListView, 0, 2, L"北京");
    
    g_pGUI->列表视图_项_插入(g_hListView, 1, L"李四");
    g_pGUI->列表视图_子项_插入(g_hListView, 1, 1, L"30");
    g_pGUI->列表视图_子项_插入(g_hListView, 1, 2, L"上海");
    
    g_pGUI->列表视图_项_插入(g_hListView, 2, L"王五");
    g_pGUI->列表视图_子项_插入(g_hListView, 2, 1, L"28");
    g_pGUI->列表视图_子项_插入(g_hListView, 2, 2, L"深圳");
}

// 文件拖动测试
void OnFileDrop() {
    WCHAR* path = g_pGUI->文件_拖动_获取路径(g_pGUI->获取窗口句柄());
    if (path) {
        wstring msg = L"拖入文件: ";
        msg += path;
        g_pGUI->消息框(L"文件拖拽", msg.c_str());
        g_pGUI->文件_拖动_释放路径(g_pGUI->获取窗口句柄());
    }
}

// 定时器更新时间和进度条
void TimerThread() {
    while (true) {
        Sleep(1000);
        if (g_pGUI) {
            UpdateTimeLabel();
            int now = g_pGUI->进度条_获取进度(g_hProgress);
            if (now < 100) {
                g_pGUI->进度条_修改进度(g_hProgress, now + 1);
            } else {
                g_pGUI->进度条_修改进度(g_hProgress, 0);
            }
        }
    }
}

BOOL WINAPI DllMain(HINSTANCE hinstDLL, DWORD fdwReason, LPVOID lpvReserved) {
    if (fdwReason == DLL_PROCESS_ATTACH) {
        DisableThreadLibraryCalls(hinstDLL);
        
        g_pGUI = new 熙鹤_GUI();
        g_pGUI->设置窗口标题(L"DEMO GUI SHELLCODE");
        g_pGUI->窗口_背景颜色(240, 248, 255);  // 淡蓝色背景
        g_pGUI->窗口_客户区拖动(true);          // 支持拖动移动窗口
        g_pGUI->设置_关闭询问框显示(true);
        g_pGUI->设置_关闭询问框内容(L"确定要退出演示程序吗？");
        g_pGUI->文件_拖动_初始化(g_pGUI->获取窗口句柄(), true);  // 支持文件拖拽
        
        // ========== 标签区 ==========
        g_hLabel = g_pGUI->放置组件_标签(20, 10, 300, 35, L"当前时间: --", RGB(0, 100, 255), true, 16);
        g_pGUI->组件_显示(g_hLabel);
        
        // ========== 按钮区 ==========
        HWND hBtn1 = g_pGUI->放置组件_按钮(20, 60, 100, 35, L"弹出消息", OnButtonClick, RGB(0,0,0), false, 12);
        g_pGUI->组件_显示(hBtn1);
        
        HWND hBtnGet = g_pGUI->放置组件_按钮(130, 60, 100, 35, L"获取输入", OnGetInputClick, RGB(0,0,0), false, 12);
        g_pGUI->组件_显示(hBtnGet);
        
        HWND hBtnClear = g_pGUI->放置组件_按钮(240, 60, 100, 35, L"清空输入", OnClearInputClick, RGB(0,0,0), false, 12);
        g_pGUI->组件_显示(hBtnClear);
        
        HWND hBtnProgAdd = g_pGUI->放置组件_按钮(350, 160, 80, 35, L"+10", OnProgressAdd, RGB(0,0,0), false, 12);
        g_pGUI->组件_显示(hBtnProgAdd);
        
        HWND hBtnProgSub = g_pGUI->放置组件_按钮(440, 160, 80, 35, L"-10", OnProgressSub, RGB(0,0,0), false, 12);
        g_pGUI->组件_显示(hBtnProgSub);
        
        HWND hBtnListAdd = g_pGUI->放置组件_按钮(20, 260, 100, 35, L"添加到列表", OnListAddClick, RGB(0,0,0), false, 12);
        g_pGUI->组件_显示(hBtnListAdd);
        
        HWND hBtnListGet = g_pGUI->放置组件_按钮(130, 260, 100, 35, L"获取选中", OnListGetClick, RGB(0,0,0), false, 12);
        g_pGUI->组件_显示(hBtnListGet);
        
        HWND hBtnCombo = g_pGUI->放置组件_按钮(350, 260, 100, 35, L"组合框选中", OnComboSelect, RGB(0,0,0), false, 12);
        g_pGUI->组件_显示(hBtnCombo);
        
        HWND hBtnDate = g_pGUI->放置组件_按钮(350, 360, 100, 35, L"获取日期", OnDateSelect, RGB(0,0,0), false, 12);
        g_pGUI->组件_显示(hBtnDate);
        
        HWND hBtnCheck = g_pGUI->放置组件_按钮(20, 400, 120, 35, L"复选框状态", OnCheckStatus, RGB(0,0,0), false, 12);
        g_pGUI->组件_显示(hBtnCheck);
        
        HWND hBtnRadio = g_pGUI->放置组件_按钮(150, 400, 120, 35, L"单选框状态", OnRadioStatus, RGB(0,0,0), false, 12);
        g_pGUI->组件_显示(hBtnRadio);
        
        // ========== 输入框 ==========
        g_hEditInput = g_pGUI->放置组件_输入框(20, 110, 320, 30, L"在这里输入文字...");
        g_pGUI->组件_显示(g_hEditInput);
        
        // ========== 进度条 ==========
        g_hProgress = g_pGUI->放置组件_进度条(350, 120, 250, 25, 0, 100, 0);
        g_pGUI->组件_显示(g_hProgress);
        
        // ========== 列表框 ==========
        g_hListBox = g_pGUI->放置组件_列表框(20, 310, 250, 150, false);
        g_pGUI->组件_显示(g_hListBox);
        g_pGUI->列表框_添加单项(g_hListBox, L"示例项 1");
        g_pGUI->列表框_添加单项(g_hListBox, L"示例项 2");
        g_pGUI->列表框_添加单项(g_hListBox, L"示例项 3");
        
        // ========== 组合框 ==========
        g_hCombo = g_pGUI->放置组件_组合框(350, 210, 250, 100);
        g_pGUI->组件_显示(g_hCombo);
        g_pGUI->组合框_添加项(g_hCombo, L"苹果");
        g_pGUI->组合框_添加项(g_hCombo, L"香蕉");
        g_pGUI->组合框_添加项(g_hCombo, L"橙子");
        g_pGUI->组合框_添加项(g_hCombo, L"葡萄");
        
        // ========== 日期框 ==========
        g_hDatePicker = g_pGUI->放置组件_日期框(350, 310, 250, 30);
        g_pGUI->组件_显示(g_hDatePicker);
        
        // ========== 复选框 ==========
        g_hCheckBox1 = g_pGUI->放置组件_复选框(20, 350, 100, 30, L"选项1", 1);
        g_hCheckBox2 = g_pGUI->放置组件_复选框(120, 350, 100, 30, L"选项2", 1);
        g_pGUI->组件_显示(g_hCheckBox1);
        g_pGUI->组件_显示(g_hCheckBox2);
        
        // ========== 单选框 ==========
        g_hRadio1 = g_pGUI->放置组件_单选框(20, 450, 100, 30, L"选项A", 2);
        g_hRadio2 = g_pGUI->放置组件_单选框(120, 450, 100, 30, L"选项B", 2);
        g_pGUI->组件_显示(g_hRadio1);
        g_pGUI->组件_显示(g_hRadio2);
        
        // ========== 列表视图 ==========
        InitListView();
        g_pGUI->组件_显示(g_hListView);
        
        // ========== 菜单栏 ==========
        HMENU hMenu = g_pGUI->菜单栏_初始化();
        HMENU hFileMenu = g_pGUI->菜单栏_创建首级菜单();
        g_pGUI->菜单栏_创建子级菜单(hFileMenu, 1001, L"打开");
        g_pGUI->菜单栏_创建子级菜单(hFileMenu, 1002, L"退出");
        g_pGUI->菜单栏_首级菜单显示(hMenu, hFileMenu, L"文件");
        
        HMENU hHelpMenu = g_pGUI->菜单栏_创建首级菜单();
        g_pGUI->菜单栏_创建子级菜单(hHelpMenu, 2001, L"关于");
        g_pGUI->菜单栏_首级菜单显示(hMenu, hHelpMenu, L"帮助");
        
        g_pGUI->菜单栏_绑定事件(1001, OnMenuOpen);
        g_pGUI->菜单栏_绑定事件(1002, OnMenuExit);
        g_pGUI->菜单栏_绑定事件(2001, []() {
            g_pGUI->消息框(L"关于", L"GUI演示程序 v1.0");
        });
        g_pGUI->菜单栏_显示(hMenu);
        
        // ========== 快捷键 ==========
        g_pGUI->组件_绑定快捷键(3001, MOD_CONTROL, 'D', []() {
            g_pGUI->消息框(L"快捷键", L"按下了 Ctrl+D");
        });
        
        // ========== 启动定时器线程 ==========
        std::thread timerThread(TimerThread);
        timerThread.detach();
        
        // ========== 启动消息循环 ==========
        g_pGUI->窗口循环();
    }
    return TRUE;
}