
#pragma once
#include "renderer.h"
#include "animation.h"
#include "ui_component.h"
#include "controls_basic.h"
#include "layout.h"
#include <vector>
#include <memory>
#include <chrono>
#include <string>

namespace gui {


class ModernWindow {
public:
    ModernWindow();
    ~ModernWindow();
    
    bool Initialize(HINSTANCE hInstance, int nCmdShow, 
                    const std::wstring& title = L"Modern GUI Window",
                    int width = 800, int height = 600);
    void Run();
    
    
    void AddComponent(std::unique_ptr<UIComponent> component);
    void RemoveComponent(int id);
    // v3：摘出组件（所有权交还调用方；滚容器 attach 用）
    std::unique_ptr<UIComponent> TakeComponent(int id);
    // v3：深度移除——顶层找不到时搜滚容器子层（qg_destroy 统一入口）
    bool RemoveComponentDeep(int id);
    void ClearComponents();
    
    
    void SetBackgroundColor(const Color& color);
    void SetBackgroundGradient(const Color& start, const Color& end);
    void SetUseGradientBackground(bool use);
    void SetCornerRadius(float radius);
    
    
    void OnChar(wchar_t ch);
    void OnKeyDown(int vkey);
    void OnImeComposition(const std::wstring& compositionString);
    void OnImeStartComposition();
    void OnImeEndComposition();
    
    Renderer* GetRenderer() { return &m_renderer; }
    HWND GetHwnd() const { return m_hwnd; }
    void SetTitle(const std::wstring& title) { if (m_hwnd) SetWindowTextW(m_hwnd, title.c_str()); }
    void UpdateImePosition();

    void SetFocusedComponent(UIComponent* comp);
    UIComponent* GetFocusedComponent() const { return m_focusedComponent; }

    // ---- 事件上报（v2 事件系统）：窗口把输入/生命周期事件推给宿主 ABI 层。
    // kind 语义与 qotgui_abi.h 的事件常量一致（10 按下 11 抬起 12 移动
    // 13 进入 14 离开 15 双击 20 键按下 21 字符 30 关闭 31 尺寸 32 激活 33 失活）
    using EventSink = std::function<void(int kind, int id, double value, int x, int y)>;
    void SetEventSink(EventSink sink) { m_eventSink = std::move(sink); }
    void Emit(int kind, int id, double value, int x, int y)
    {
        if (m_eventSink) m_eventSink(kind, id, value, x, y);
    }

    // ---- 文件拖放（v3  5）：WM_DROPFILES → sink（文件路径列表 + 落点客户坐标）----
    using DropSink = std::function<void(const std::vector<std::wstring> &files, int x, int y)>;
    void SetDropSink(DropSink sink) { m_dropSink = std::move(sink); }
    void EnableDropFiles(bool on)
    {
        if (m_hwnd) DragAcceptFiles(m_hwnd, on ? TRUE : FALSE);
    }

    // ---- 窗口合成能力原语（DWM/合成器层；给能力，不给效果封装） ----
    bool SetWindowTransparent(bool on);            // 重定向表面透明（自制玻璃的基础）
    bool SetWindowBackdrop(bool on, float tint);   // 系统合成器背景模糊原语
    bool SetWindowRoundCorner(bool on);            // Win11 圆角
    bool SetBorderless(bool on);                   // 无边框（保留边缘缩放）
    void SetPreventClose(bool on) { m_preventClose = on; }
    void SetTopmost(bool on) { if (m_hwnd) SetWindowPos(m_hwnd, on ? HWND_TOPMOST : HWND_NOTOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE); }
    // 供无边框窗口在客户区按下时启动系统拖动
    void DragWindow();

    // 是否有动画在跑（WM_TIMER 用它决定是否请求下一帧）
    bool HasActiveAnimation() const { return AnimationManager::Instance().IsAnimating(); }
    // 请求重绘（外部改属性后调用，保证立刻看到变化）
    void RequestRepaint() { if (m_hwnd) InvalidateRect(m_hwnd, nullptr, FALSE); }

    // ---- 布局系统（v3）：布局管理器宿主 + resize 统一重排 ----
    LayoutManager &Layout() { return m_layout; }
    // 立即按布局重排（vg_layout_end / 属性变更后调用；resize 路径自动走）
    void RelayoutNow();

    // ---- Toast 自绘轻提示（v3  3）：底部居中卡片，自动淡出 ----
    void ShowToast(const std::wstring &text, int ms);
    bool ToastActive() const;
    void DrawToast();

    // ---- v4 Toast 样式原语：背景/文字颜色 + 圆角（未设置走默认）----
    struct ToastStyle {
        Color bg{0.08f, 0.08f, 0.14f, 0.92f};
        Color text{0.95f, 0.95f, 1.0f, 1.0f};
        Color border{0.45f, 0.50f, 0.65f, 0.50f};
        float radius = 8.0f;
    };
    void SetToastStyle(const ToastStyle &s) { m_toastStyle = s; }

    // ---- 帧循环原语（requestAnimationFrame 模型） ----
    // 进程级单帧钩子（ABI 层管理回调表）：每帧绘制前同步调用，dt 秒
    using FrameHook = std::function<void(double dt)>;
    static void SetFrameHook(FrameHook fn) { s_frameHook = std::move(fn); }
    // 请求下一帧：帧不被自动续期，需要连续动画由帧回调内再次请求
    void RequestFrame() { m_framesRequested = 1; }
    bool NeedsFrame() const { return m_framesRequested > 0; }

private:
    // 动画帧定时器 id（16ms ≈ 60fps；OnPaint 里发现无动画在跑就停表）
    static constexpr UINT_PTR kAnimTimerId = 0x514F;
    static void SetDpiAwarenessOnce();
    void OnPaint();
    void OnResize();
    void UpdateAnimation();
    void HandleMouse();
    void UpdateHover(const Point& pos);   // hover 追踪 + enter/leave/move 事件上报
    void SetCompositionFont(HIMC hImc);
    void UpdateCompositionWindow();
    // 无边框时把窗口边缘映射为缩放手柄（WM_NCHITTEST 用）
    LRESULT HitTestNCA(POINT pt);
    static LRESULT CALLBACK WindowProc(HWND hwnd, UINT uMsg, WPARAM wParam, LPARAM lParam);

    HWND m_hwnd;
    Renderer m_renderer;
    Size m_size;


    std::vector<std::unique_ptr<UIComponent>> m_components;


    Color m_bgColorStart;
    Color m_bgColorEnd;
    bool m_useGradientBg;
    float m_cornerRadius;


    std::chrono::steady_clock::time_point m_lastFrameTime;


    Point m_mousePos;
    UIComponent* m_focusedComponent = nullptr;
    UIComponent* m_hoverComponent = nullptr;


    std::wstring m_imeCompositionString;
    bool m_isImeComposing = false;

    // v2 事件系统
    EventSink m_eventSink;
    DropSink m_dropSink;   // v3 文件拖放（WM_DROPFILES 汇点）
    bool m_preventClose = false;
    static int s_aliveWindows;   // 多窗口：最后一个销毁才 PostQuitMessage
    static FrameHook s_frameHook;    // 帧钩子（ABI 层注册）
    int m_framesRequested = 0;   // 帧请求计数（帧执行时消耗）

    // v3 布局管理器（Resize 重排；Absolute 模式下退化为 Anchor 应用）
    LayoutManager m_layout;

    // v3 toast 状态（OnPaint 末尾自绘；WM_TIMER 重绘条件含 ToastActive）
    std::wstring m_toastText;
    std::chrono::steady_clock::time_point m_toastStart;
    std::chrono::steady_clock::time_point m_toastUntil;
    ToastStyle m_toastStyle;   // v4 样式原语
};

} 