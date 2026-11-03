
#include "modern_window.h"
#include "layout_theme.h"   // Anchor 应用（Absolute 模式 resize 路径）
#include "scrollpane.h"     // RemoveComponentDeep 深度搜滚容器子层
#include <commctrl.h>
#include <imm.h>
#include <dwmapi.h>
#include <shellapi.h>       // DragAcceptFiles / DragQueryFileW（v3 拖放）

#pragma comment(lib, "imm32.lib")
#pragma comment(lib, "dwmapi.lib")


#ifndef GET_X_LPARAM
#define GET_X_LPARAM(lp) ((int)(short)LOWORD(lp))
#endif
#ifndef GET_Y_LPARAM
#define GET_Y_LPARAM(lp) ((int)(short)HIWORD(lp))
#endif

// DWM 属性常量（老 SDK 头没有的定义）
#ifndef DWMWA_USE_IMMERSIVE_DARK_MODE
#define DWMWA_USE_IMMERSIVE_DARK_MODE 20
#endif
#ifndef DWMWA_WINDOW_CORNER_PREFERENCE
#define DWMWA_WINDOW_CORNER_PREFERENCE 33
#endif
#ifndef DWMWA_SYSTEMBACKDROP_TYPE
#define DWMWA_SYSTEMBACKDROP_TYPE 38
#endif
#ifndef DWMWCP_ROUND
#define DWMWCP_ROUND 2
#endif
#ifndef DWMSBT_NONE
#define DWMSBT_NONE 1
#define DWMSBT_MAINWINDOW 2        /* Mica */
#define DWMSBT_TRANSIENTWINDOW 3   /* Acrylic */
#endif

namespace gui
{

    int ModernWindow::s_aliveWindows = 0;
    ModernWindow::FrameHook ModernWindow::s_frameHook;

    // ---------------------------------------------------------------------------
    // v3 帧驱动修正（ 6 阶段 A'）：WM_TIMER 的 USER 定时器受系统节拍
    // 限制（默认 ~15.6ms），与 Present 的 vsync（~16.7ms）拍频合并后实测
    // 掉到 ~34fps（空载/重载相同——瓶颈是节拍不是重绘）。动画/帧请求活跃
    // 期间把系统定时器分辨率提到 1ms + 心跳缩短到 8ms，让 vsync 成为唯一
    // 帧率上限（≈60fps）。winmm 动态解析，不进导入表；空闲即还原（省电）。
    // ---------------------------------------------------------------------------
    typedef UINT(WINAPI *PFN_timeBeginPeriod)(UINT);
    typedef UINT(WINAPI *PFN_timeEndPeriod)(UINT);

    static PFN_timeBeginPeriod TimeBeginPeriodFn()
    {
        static PFN_timeBeginPeriod pfn = nullptr;
        static bool tried = false;
        if (!tried)
        {
            tried = true;
            HMODULE m = LoadLibraryW(L"winmm.dll");
            if (m)
                pfn = reinterpret_cast<PFN_timeBeginPeriod>(
                    GetProcAddress(m, "timeBeginPeriod"));
        }
        return pfn;
    }

    static PFN_timeEndPeriod TimeEndPeriodFn()
    {
        static PFN_timeEndPeriod pfn = nullptr;
        static bool tried = false;
        if (!tried)
        {
            tried = true;
            HMODULE m = LoadLibraryW(L"winmm.dll");
            if (m)
                pfn = reinterpret_cast<PFN_timeEndPeriod>(
                    GetProcAddress(m, "timeEndPeriod"));
        }
        return pfn;
    }

    static int g_periodBoost = 0;   // 0 = 未提升；1 = 已提升（幂等开关）

    static void EnsureHighResTiming()
    {
        if (g_periodBoost) return;
        PFN_timeBeginPeriod pfn = TimeBeginPeriodFn();
        if (pfn && pfn(1)) g_periodBoost = 1;
    }

    static void ReleaseHighResTiming()
    {
        if (!g_periodBoost) return;
        PFN_timeEndPeriod pfn = TimeEndPeriodFn();
        if (pfn) pfn(1);
        g_periodBoost = 0;
    }

    ModernWindow::ModernWindow()
        : m_hwnd(nullptr), m_size(800, 600),
          m_bgColorStart(0.05f, 0.05f, 0.15f, 1.0f),
          m_bgColorEnd(0.15f, 0.08f, 0.25f, 1.0f),
          m_useGradientBg(true),
          m_cornerRadius(0),
          m_mousePos(0, 0)
    {
    }

    ModernWindow::~ModernWindow()
    {
        if (m_hwnd)
        {
            KillTimer(m_hwnd, kAnimTimerId);
            DestroyWindow(m_hwnd);   // WM_DESTROY 已减存活计数
            m_hwnd = nullptr;
        }
        // v3：最后一个窗口销毁时还原系统定时器分辨率（帧驱动提升的副作用收尾）
        if (s_aliveWindows <= 0)
        {
            ReleaseHighResTiming();
        }
    }

    bool ModernWindow::Initialize(HINSTANCE hInstance, int nCmdShow,
                                  const std::wstring &title, int width, int height)
    {
        m_size = Size(static_cast<float>(width), static_cast<float>(height));

        // DPI 感知必须在建窗之前设置，否则窗口尺寸与非客户区按 96dpi 计算
        // （高分屏上界面会被系统拉伸得发虚）。优先 per-monitor v2，逐级回退。
        SetDpiAwarenessOnce();

        
        WNDCLASSEXW wcex = {};
        wcex.cbSize = sizeof(WNDCLASSEXW);
        wcex.style = CS_HREDRAW | CS_VREDRAW | CS_DBLCLKS;
        wcex.lpfnWndProc = WindowProc;
        wcex.hInstance = hInstance;
        wcex.hCursor = LoadCursor(nullptr, IDC_ARROW);
        wcex.hbrBackground = nullptr;
        wcex.lpszClassName = L"ModernWindowClass";

        // 第二个窗口起 RegisterClassEx 会因「类已存在」失败 —— 那是正常情况，
        // 不能当成初始化失败（否则一进程只能开一个窗口）。
        WNDCLASSEXW existing = {};
        existing.cbSize = sizeof(WNDCLASSEXW);
        bool classReady = GetClassInfoExW(hInstance, L"ModernWindowClass", &existing) != 0;
        if (!classReady && !RegisterClassExW(&wcex))
        {
            return false;
        }

        
        m_hwnd = CreateWindowExW(
            0,
            L"ModernWindowClass",
            title.c_str(),
            WS_OVERLAPPEDWINDOW | WS_CLIPCHILDREN | WS_CLIPSIBLINGS,
            CW_USEDEFAULT, CW_USEDEFAULT,
            width, height,
            nullptr, nullptr, hInstance, this);

        if (!m_hwnd)
        {
            return false;
        }

        
        HIMC hImc = ImmAssociateContext(m_hwnd, NULL); 
        
        
        if (hImc)
        {
            ImmAssociateContext(m_hwnd, hImc);
        }

        
        if (!m_renderer.Initialize(m_hwnd))
        {
            return false;
        }

        ++s_aliveWindows;   // 真窗口落成；WM_DESTROY 里递减，减到 0 退出消息循环

        ShowWindow(m_hwnd, nCmdShow);
        UpdateWindow(m_hwnd);

        m_lastFrameTime = std::chrono::steady_clock::now();

        // 动画帧驱动：悬停/按下过渡靠定时器推进，空闲时停表（不空转烧 CPU）。
        // v3：8ms 心跳（配合活跃期 timeBeginPeriod(1)，见 WM_TIMER 处理）
        SetTimer(m_hwnd, kAnimTimerId, 8, nullptr);

        return true;
    }

    void ModernWindow::SetDpiAwarenessOnce()
    {
        static bool done = false;
        if (done)
            return;
        done = true;

        HMODULE shcore = LoadLibraryW(L"shcore.dll");
        if (shcore)
        {
            // PER_MONITOR_AWARE_V2 = 2（Win10 1703+）
            using SetAwarenessCtx = BOOL(WINAPI *)(int);
            auto pSetCtx = reinterpret_cast<SetAwarenessCtx>(
                GetProcAddress(shcore, "SetProcessDpiAwarenessContext"));
            if (pSetCtx)
            {
                // -4 = DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
                if (pSetCtx(-4))
                {
                    FreeLibrary(shcore);
                    return;
                }
            }
            using SetAwareness = HRESULT(WINAPI *)(int);
            auto pSet = reinterpret_cast<SetAwareness>(
                GetProcAddress(shcore, "SetProcessDpiAwareness"));
            if (pSet && SUCCEEDED(pSet(2)))
            {
                FreeLibrary(shcore);
                return;
            }
            FreeLibrary(shcore);
        }

        HMODULE user32 = GetModuleHandleW(L"user32.dll");
        if (user32)
        {
            using SetAware = BOOL(WINAPI *)(void);
            auto pAware = reinterpret_cast<SetAware>(GetProcAddress(user32, "SetProcessDPIAware"));
            if (pAware)
                pAware();
        }
    }

    void ModernWindow::Run()
    {
        MSG msg;
        while (GetMessage(&msg, nullptr, 0, 0))
        {
            TranslateMessage(&msg);
            DispatchMessage(&msg);
        }
    }

    void ModernWindow::AddComponent(std::unique_ptr<UIComponent> component) {
    
    if (auto* textBox = dynamic_cast<TextBox*>(component.get())) {
        textBox->SetRenderer(&m_renderer);
    }
    m_components.push_back(std::move(component));
}

    void ModernWindow::RemoveComponent(int id)
    {
        auto it = std::find_if(m_components.begin(), m_components.end(),
                               [id](const std::unique_ptr<UIComponent> &comp)
                               {
                                   return comp->GetId() == id;
                               });
        if (it != m_components.end())
        {
            m_components.erase(it);
        }
    }

    // v3：摘出组件（所有权转移；滚容器 attach 用）。找不到返回空。
    std::unique_ptr<UIComponent> ModernWindow::TakeComponent(int id)
    {
        auto it = std::find_if(m_components.begin(), m_components.end(),
                               [id](const std::unique_ptr<UIComponent> &comp)
                               {
                                   return comp->GetId() == id;
                               });
        if (it == m_components.end()) return nullptr;
        std::unique_ptr<UIComponent> p = std::move(*it);
        m_components.erase(it);
        return p;
    }

    // v3：深度移除——顶层没有时搜各滚容器子层（qg_destroy 统一入口，
    // attach 进 ScrollPane 的控件也能销毁）。
    bool ModernWindow::RemoveComponentDeep(int id)
    {
        auto it = std::find_if(m_components.begin(), m_components.end(),
                               [id](const std::unique_ptr<UIComponent> &comp)
                               {
                                   return comp->GetId() == id;
                               });
        if (it != m_components.end())
        {
            m_components.erase(it);
            return true;
        }
        for (auto &comp : m_components)
        {
            if (auto *sp = dynamic_cast<ScrollPane *>(comp.get()))
            {
                if (sp->RemoveChild(id)) return true;
            }
        }
        return false;
    }

    void ModernWindow::ClearComponents()
    {
        m_components.clear();
    }

    void ModernWindow::SetBackgroundColor(const Color &color)
    {
        m_bgColorStart = color;
        m_bgColorEnd = color;
        m_useGradientBg = false;
    }

    void ModernWindow::SetBackgroundGradient(const Color &start, const Color &end)
    {
        m_bgColorStart = start;
        m_bgColorEnd = end;
        m_useGradientBg = true;
    }

    void ModernWindow::SetUseGradientBackground(bool use)
    {
        m_useGradientBg = use;
    }

    void ModernWindow::SetCornerRadius(float radius)
    {
        m_cornerRadius = radius;
    }

    void ModernWindow::SetFocusedComponent(UIComponent *comp)
    {
        
        if (m_focusedComponent)
        {
            if (auto *textBox = dynamic_cast<TextBox *>(m_focusedComponent))
            {
                textBox->SetFocus(false);
            }
        }

        m_focusedComponent = comp;

        
        if (m_focusedComponent)
        {
            if (auto *textBox = dynamic_cast<TextBox *>(m_focusedComponent))
            {
                textBox->SetFocus(true);
            }
        }
    }

    void ModernWindow::OnPaint()
    {
        if (!m_renderer.GetRenderTarget())
            return;

        // 帧步长在本帧时间戳刷新前量出（UpdateAnimation 会更新时间戳）
        auto frameNow = std::chrono::steady_clock::now();
        double frameDt = std::chrono::duration<double>(frameNow - m_lastFrameTime).count();
        if (frameDt > 0.1) frameDt = 0.1;

        UpdateAnimation();
        HandleMouse();

        // 帧原语：每帧绘制前同步调用（开发者在此组合任意动画，
        // 需要下一帧再显式请求 —— 不请求即自然停帧，零空转）
        if (m_framesRequested > 0)
        {
            --m_framesRequested;
            if (s_frameHook)
            {
                s_frameHook(frameDt);
            }
        }

        m_renderer.BeginDraw();

        
        if (m_useGradientBg)
        {
            Rect fullRect(0, 0, m_size.width, m_size.height);
            m_renderer.FillRectGradient(fullRect, m_bgColorStart, m_bgColorEnd, true);
        }
        else
        {
            m_renderer.Clear(m_bgColorStart);
        }

                for (auto &component : m_components)
        {
            if (component->IsVisible())
            {
                // v6：状态位差分 → QG_EVT_STATE（value = 新状态位全量；
                // 绘制点对比——状态变化必然伴随重绘请求，此为最稳采样点）
                unsigned sflags = component->StyleFlags();
                if (!component->m_flagsInited || component->m_lastFlags != sflags)
                {
                    component->m_lastFlags = sflags;
                    component->m_flagsInited = true;
                    Emit(52, component->GetId(), (double)sflags, 0, 0);
                }
                // 组件级透明度（<1 时以透明度层呈现：淡入淡出/动画用）
                bool layered = false;
                if (component->GetOpacity() < 0.999f)
                {
                    layered = m_renderer.PushLayerOpacity(component->GetOpacity(),
                                                          component->GetBounds());
                }
                component->Draw(&m_renderer);
                if (layered) m_renderer.PopLayerOpacity();
            }
        }

        // v3 toast：最上层自绘轻提示（组件之后、EndDraw 之前）
        if (ToastActive()) DrawToast();

        m_renderer.EndDraw();
    }

    void ModernWindow::OnResize()
    {
        RECT rc;
        GetClientRect(m_hwnd, &rc);
        m_size = Size(static_cast<float>(rc.right - rc.left),
                      static_cast<float>(rc.bottom - rc.top));
        m_renderer.Resize(m_size);
        RelayoutNow();   // v3：布局重排（或 Anchor 应用）统一挂进 resize
        Emit(31, 0, 0.0, (int)m_size.width, (int)m_size.height);
    }

    // ---------------------------------------------------------------------------
    // v3 布局重排入口：布局模式激活时布局引擎全权重排；Absolute 模式下
    // 走 layout_theme.h 的 ApplyAnchor（v2 就有实现但没人调用——在此并轨，
    // 不留两套）。anchor 未设置的控件 ApplyAnchor 直接返回，绝对坐标零影响。
    // ---------------------------------------------------------------------------
    void ModernWindow::RelayoutNow()
    {
        if (m_layout.mode != 0)
        {
            m_layout.Relayout(m_components, m_size);
            return;
        }
        Rect parent(0, 0, m_size.width, m_size.height);
        for (auto &c : m_components)
        {
            if (c) LayoutHelper::ApplyAnchor(c.get(), parent, c->GetBounds());
        }
    }

    // ---------------------------------------------------------------------------
    // v3 Toast：底部居中的自绘轻提示卡片（不挡输入、自动淡出）。
    // 到期清理放在绘制路径（下一帧自然抹掉），无额外定时器。
    // ---------------------------------------------------------------------------
    void ModernWindow::ShowToast(const std::wstring &text, int ms)
    {
        m_toastText = text;
        auto now = std::chrono::steady_clock::now();
        m_toastStart = now;
        m_toastUntil = now + std::chrono::milliseconds(ms > 0 ? ms : 2200);
        RequestRepaint();
    }

    bool ModernWindow::ToastActive() const
    {
        // 淡出尾后再续 400ms 表（过期的帧画 alpha=0 的透明卡片 = 干净收尾，
        // 防止定时器在最后一帧淡出前停表留下残影）
        return !m_toastText.empty() &&
               std::chrono::steady_clock::now() < m_toastUntil + std::chrono::milliseconds(400);
    }

    void ModernWindow::DrawToast()
    {
        auto now = std::chrono::steady_clock::now();
        if (now >= m_toastUntil)
        {
            m_toastText.clear();
            return;
        }
        float ageMs = std::chrono::duration<float, std::milli>(now - m_toastStart).count();
        float remainMs = std::chrono::duration<float, std::milli>(m_toastUntil - now).count();
        float alpha = 1.0f;
        if (ageMs < 150.0f) alpha = ageMs / 150.0f;         // 淡入
        if (remainMs < 300.0f) alpha = remainMs / 300.0f;   // 淡出
        if (alpha < 0.0f) alpha = 0.0f;

        Size ts = m_renderer.MeasureText(m_toastText, 14.0f, false);
        float w = ts.width + 36.0f;
        float h = ts.height + 20.0f;
        if (w < 80.0f) w = 80.0f;
        float x = (m_size.width - w) / 2.0f;
        float y = m_size.height - h - 36.0f;

        RoundedRect rr;
        rr.rect = Rect(x, y, x + w, y + h);
        rr.radiusX = rr.radiusY = m_toastStyle.radius;
        Color bg = m_toastStyle.bg;  bg.a *= alpha;
        Color bc = m_toastStyle.border;  bc.a *= alpha;
        Color tc = m_toastStyle.text;  tc.a *= alpha;
        m_renderer.FillRoundedRect(rr, bg);
        m_renderer.DrawRoundedRect(rr, bc, 1.0f);
        Rect textRect(x + 18.0f, y, x + w - 18.0f, y + h);
        m_renderer.DrawTextAligned(m_toastText, textRect, tc, 14.0f, false, 1, 1, false);
    }

    // ---------------------------------------------------------------------------
    // 窗口效果（高定制化）：毛玻璃/圆角/无边框 —— DWM 与合成器层，
    // 各平台能力差异全部在这里吸收：不支持的能力静默返回 false。
    // ---------------------------------------------------------------------------

    // SetWindowCompositionAttribute（user32 未公开 API，Win10 亚克力blur）；
    // 动态解析，拿不到就没有 Win10 路径（Win11 走公开的 backdrop 属性）。
    struct ACCENT_POLICY { int AccentState; int AccentFlags; unsigned GradientColor; int AnimationId; };
    struct WINCOMPATTRDATA { int Attribute; void *Data; unsigned long SizeOfData; };

    typedef BOOL(WINAPI *PFN_SWCA)(HWND, int, void *);
    static PFN_SWCA SwcaFn()
    {
        static PFN_SWCA pfn = nullptr;
        static bool tried = false;
        if (!tried)
        {
            tried = true;
            HMODULE u32 = GetModuleHandleW(L"user32.dll");
            if (u32)
                pfn = reinterpret_cast<PFN_SWCA>(GetProcAddress(u32, "SetWindowCompositionAttribute"));
        }
        return pfn;
    }

    // 原语一：重定向表面透明 —— 自制玻璃/悬浮效果的基础。
    // 经典 DWM 配方：空区域 blur-behind 让重定向表面被忽略，
    // 客户区看到的是开发者自己绘制的（可半透明）内容。
    bool ModernWindow::SetWindowTransparent(bool on)
    {
        if (!m_hwnd) return false;
        DWM_BLURBEHIND bb = {};
        bb.dwFlags = DWM_BB_ENABLE;
        bb.fEnable = on ? TRUE : FALSE;
        bb.hRgnBlur = nullptr;
        if (on)
        {
            bb.fEnable = TRUE;
            bb.hRgnBlur = CreateRectRgn(-1, -1, 0, 0);   // 空区域 = 全透明窗体
            if (!bb.hRgnBlur) return false;
        }
        HRESULT hr = DwmEnableBlurBehindWindow(m_hwnd, &bb);
        if (bb.hRgnBlur) DeleteObject(bb.hRgnBlur);
        if (FAILED(hr)) return false;
        RequestRepaint();
        return true;
    }

    // 原语二：系统合成器背景模糊（模糊的是桌面——绘制原语做不到的能力）
    bool ModernWindow::SetWindowBackdrop(bool on, float tint)
    {
        if (!m_hwnd) return false;
        if (!on)
        {
            int none = DWMSBT_NONE;
            DwmSetWindowAttribute(m_hwnd, DWMWA_SYSTEMBACKDROP_TYPE, &none, sizeof(none));
            PFN_SWCA pfn = SwcaFn();
            if (pfn)
            {
                ACCENT_POLICY ap = {};
                ap.AccentState = 0;   // ACCENT_DISABLED
                WINCOMPATTRDATA d = {19, &ap, sizeof(ap)};   // WCA_ACCENT_POLICY
                pfn(m_hwnd, 19, &d);
            }
            RequestRepaint();
            return true;
        }

        // 玻璃态需要 frame 扩展（把 DWM 背景垫到客户区）
        MARGINS mg = {1, 1, 1, 1};
        DwmExtendFrameIntoClientArea(m_hwnd, &mg);

        // Win11：公开的 systembackdrop（Mica/Acrylic 由系统选定）
        int backdrop = DWMSBT_TRANSIENTWINDOW;   // Acrylic 形态
        BOOL dark = TRUE;
        DwmSetWindowAttribute(m_hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE, &dark, sizeof(dark));
        if (SUCCEEDED(DwmSetWindowAttribute(m_hwnd, DWMWA_SYSTEMBACKDROP_TYPE,
                                            &backdrop, sizeof(backdrop))))
        {
            RequestRepaint();
            return true;
        }

        // Win10：未公开的 acrylic blur-behind（tint 的 alpha 承载质感）
        PFN_SWCA pfn = SwcaFn();
        if (!pfn) return false;

        ACCENT_POLICY ap = {};
        ap.AccentState = 4;   // ACCENT_ENABLE_ACRYLICBLURBEHIND
        ap.AccentFlags = 2;
        unsigned a = (unsigned)(tint * 255.0f);
        if (a > 255) a = 255;
        ap.GradientColor = (a << 24) | 0x00202020;   // 深色基底（ABGR）
        WINCOMPATTRDATA d = {19, &ap, sizeof(ap)};
        if (!pfn(m_hwnd, 19, &d)) return false;

        RequestRepaint();
        return true;
    }

    bool ModernWindow::SetWindowRoundCorner(bool on)
    {
        if (!m_hwnd) return false;
        int pref = on ? DWMWCP_ROUND : 0;   // 0 = DWMWCP_DEFAULT
        return SUCCEEDED(DwmSetWindowAttribute(m_hwnd, DWMWA_WINDOW_CORNER_PREFERENCE,
                                               &pref, sizeof(pref)));
    }

    bool ModernWindow::SetBorderless(bool on)
    {
        if (!m_hwnd) return false;
        LONG_PTR style = GetWindowLongPtrW(m_hwnd, GWL_STYLE);
        if (on)
        {
            style &= ~(WS_CAPTION | WS_THICKFRAME | WS_SYSMENU |
                       WS_MINIMIZEBOX | WS_MAXIMIZEBOX);
        }
        else
        {
            style |= (WS_CAPTION | WS_THICKFRAME | WS_SYSMENU |
                      WS_MINIMIZEBOX | WS_MAXIMIZEBOX);
        }
        SetWindowLongPtrW(m_hwnd, GWL_STYLE, style);
        SetWindowPos(m_hwnd, nullptr, 0, 0, 0, 0,
                     SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_FRAMECHANGED);
        return true;
    }

    void ModernWindow::DragWindow()
    {
        if (!m_hwnd) return;
        ReleaseCapture();
        SendMessageW(m_hwnd, WM_NCLBUTTONDOWN, HTCAPTION, 0);
    }

    // 无边框时把 8px 边缘映射为系统缩放手柄
    LRESULT ModernWindow::HitTestNCA(POINT pt)
    {
        RECT rc;
        GetWindowRect(m_hwnd, &rc);
        const int k = 8;
        bool l = pt.x < rc.left + k, r = pt.x >= rc.right - k;
        bool t = pt.y < rc.top + k, b = pt.y >= rc.bottom - k;
        if (t && l) return HTTOPLEFT;
        if (t && r) return HTTOPRIGHT;
        if (b && l) return HTBOTTOMLEFT;
        if (b && r) return HTBOTTOMRIGHT;
        if (t) return HTTOP;
        if (b) return HTBOTTOM;
        if (l) return HTLEFT;
        if (r) return HTRIGHT;
        return HTCLIENT;
    }

    void ModernWindow::UpdateAnimation()
    {
        auto now = std::chrono::steady_clock::now();
        float deltaTime = std::chrono::duration<float>(now - m_lastFrameTime).count();
        m_lastFrameTime = now;

        if (deltaTime > 0.1f)
            deltaTime = 0.1f;

        AnimationManager::Instance().Update(deltaTime);

        for (auto &component : m_components)
        {
            component->Update(deltaTime);
        }
    }

    void ModernWindow::HandleMouse()
    {
        POINT pt;
        GetCursorPos(&pt);
        ScreenToClient(m_hwnd, &pt);

        Point mousePos(static_cast<float>(pt.x), static_cast<float>(pt.y));


        for (auto &component : m_components)
        {
            if (component->HandleMouseMove(mousePos))
            {
                break;
            }
        }
    }

    // hover 追踪 + enter/leave/move 事件上报（v2 事件系统）
    void ModernWindow::UpdateHover(const Point &pos)
    {
        UIComponent *target = nullptr;
        for (auto &component : m_components)
        {
            if (!component->IsVisible()) continue;
            const Rect &b = component->GetBounds();
            if (pos.x >= b.left && pos.x <= b.right && pos.y >= b.top && pos.y <= b.bottom)
            {
                target = component.get();
                break;
            }
        }

        if (target != m_hoverComponent)
        {
            if (m_hoverComponent)
                Emit(14, m_hoverComponent->GetId(), 0.0, (int)pos.x, (int)pos.y);
            m_hoverComponent = target;
            if (m_hoverComponent)
                Emit(13, m_hoverComponent->GetId(), 0.0, (int)pos.x, (int)pos.y);
        }
        else if (m_hoverComponent)
        {
            Emit(12, m_hoverComponent->GetId(), 0.0, (int)pos.x, (int)pos.y);
        }
    }

    void ModernWindow::OnChar(wchar_t ch)
    {
        if (!m_focusedComponent)
            return;

        
        if (m_isImeComposing)
            return;

        if (auto *textBox = dynamic_cast<TextBox *>(m_focusedComponent))
        {
            textBox->HandleChar(ch);
            InvalidateRect(m_hwnd, nullptr, FALSE);
        }
    }

    void ModernWindow::OnKeyDown(int vkey)
    {
        if (!m_focusedComponent)
            return;

        if (auto *textBox = dynamic_cast<TextBox *>(m_focusedComponent))
        {
            textBox->HandleKey(vkey);
            InvalidateRect(m_hwnd, nullptr, FALSE);
        }
    }

    void ModernWindow::OnImeComposition(const std::wstring &compositionString)
    {
        m_imeCompositionString = compositionString;

        if (!m_focusedComponent)
            return;

        if (auto *textBox = dynamic_cast<TextBox *>(m_focusedComponent))
        {
            
            textBox->HandleImeComposition(compositionString);
            InvalidateRect(m_hwnd, nullptr, FALSE);
        }
    }

    void ModernWindow::OnImeStartComposition()
    {
        m_isImeComposing = true;

        if (!m_focusedComponent)
            return;

        if (auto *textBox = dynamic_cast<TextBox *>(m_focusedComponent))
        {
            textBox->SetImeComposing(true);
        }
    }

    void ModernWindow::OnImeEndComposition()
    {
        m_isImeComposing = false;
        m_imeCompositionString.clear();

        if (!m_focusedComponent)
            return;

        if (auto *textBox = dynamic_cast<TextBox *>(m_focusedComponent))
        {
            textBox->SetImeComposing(false);

            
            if (!m_imeCompositionString.empty())
            {
                textBox->HandleImeCommit(m_imeCompositionString);
                m_imeCompositionString.clear();
            }
            InvalidateRect(m_hwnd, nullptr, FALSE);
        }
    }
    void ModernWindow::SetCompositionFont(HIMC hImc)
    {
        if (!hImc)
            return;

        
        COMPOSITIONFORM cf = {0};
        cf.dwStyle = CFS_POINT;

        if (m_focusedComponent)
        {
            Rect bounds = m_focusedComponent->GetBounds();
            POINT pt = {(LONG)bounds.left, (LONG)bounds.bottom + 5};
            ClientToScreen(m_hwnd, &pt);
            cf.ptCurrentPos = pt;
        }
        else
        {
            POINT pt = {0, 0};
            ClientToScreen(m_hwnd, &pt);
            cf.ptCurrentPos = pt;
        }

        ImmSetCompositionWindow(hImc, &cf);

        
        LOGFONTW lf = {0};
        lf.lfHeight = -16;
        lf.lfWeight = FW_NORMAL;
        wcscpy_s(lf.lfFaceName, L"Segoe UI");
        ImmSetCompositionFontW(hImc, &lf);
    }

    void ModernWindow::UpdateCompositionWindow()
    {
        HIMC hImc = ImmGetContext(m_hwnd);
        if (hImc)
        {
            SetCompositionFont(hImc);
            ImmReleaseContext(m_hwnd, hImc);
        }
    }
    LRESULT CALLBACK ModernWindow::WindowProc(HWND hwnd, UINT uMsg, WPARAM wParam, LPARAM lParam)
    {
        ModernWindow *pWindow = nullptr;

        if (uMsg == WM_NCCREATE)
        {
            CREATESTRUCTW *pCreate = reinterpret_cast<CREATESTRUCTW *>(lParam);
            pWindow = reinterpret_cast<ModernWindow *>(pCreate->lpCreateParams);
            SetWindowLongPtr(hwnd, GWLP_USERDATA, reinterpret_cast<LONG_PTR>(pWindow));
        }
        else
        {
            pWindow = reinterpret_cast<ModernWindow *>(GetWindowLongPtr(hwnd, GWLP_USERDATA));
        }

        if (pWindow)
        {
            switch (uMsg)
            {
            case WM_PAINT:
            {
                PAINTSTRUCT ps;
                BeginPaint(hwnd, &ps);
                pWindow->OnPaint();
                EndPaint(hwnd, &ps);
                return 0;
            }

            case WM_SIZE:
                pWindow->OnResize();
                InvalidateRect(hwnd, nullptr, FALSE);
                return 0;

            case WM_ERASEBKGND:
                return 1;

            case WM_NCHITTEST:
            {
                // 无边框模式：把窗口边缘映射为系统缩放手柄
                LONG_PTR style = GetWindowLongPtrW(hwnd, GWL_STYLE);
                if (!(style & WS_CAPTION))
                {
                    POINT pt = {GET_X_LPARAM(lParam), GET_Y_LPARAM(lParam)};
                    LRESULT ht = pWindow->HitTestNCA(pt);
                    if (ht != HTCLIENT) return ht;
                }
                break;   // 走 DefWindowProc
            }

            case WM_CLOSE:
                // 可否决关闭：上报后由 prevent 状态决定是否真关
                pWindow->Emit(30, 0, 0.0, 0, 0);
                if (pWindow->m_preventClose) return 0;
                DestroyWindow(hwnd);
                return 0;

            case WM_ACTIVATE:
                pWindow->Emit(LOWORD(wParam) == WA_INACTIVE ? 33 : 32, 0, 0.0, 0, 0);
                break;

            case WM_LBUTTONDOWN:
            {
                POINT pt = {GET_X_LPARAM(lParam), GET_Y_LPARAM(lParam)};
                Point mousePos(static_cast<float>(pt.x), static_cast<float>(pt.y));

                bool found = false;
                for (auto &comp : pWindow->m_components)
                {
                    if (comp->HandleMouseDown(mousePos, 0))
                    {
                        found = true;
                        pWindow->Emit(10, comp->GetId(), 0.0,
                                      (int)mousePos.x, (int)mousePos.y);

                        if (auto *textBox = dynamic_cast<TextBox *>(comp.get()))
                        {
                            pWindow->SetFocusedComponent(comp.get());
                        }
                        else
                        {
                            pWindow->SetFocusedComponent(nullptr);
                        }
                        break;
                    }
                }
                if (!found)
                {
                    pWindow->SetFocusedComponent(nullptr);
                }
                if (pWindow->m_focusedComponent && dynamic_cast<TextBox *>(pWindow->m_focusedComponent))
                {
                    HIMC hImc = ImmGetContext(hwnd);
                    if (hImc)
                    {
                        Rect bounds = pWindow->m_focusedComponent->GetBounds();
                        COMPOSITIONFORM cf = {0};
                        cf.dwStyle = CFS_POINT;
                        POINT pt = {(LONG)bounds.left, (LONG)bounds.bottom + 5};
                        ClientToScreen(hwnd, &pt);
                        cf.ptCurrentPos = pt;
                        ImmSetCompositionWindow(hImc, &cf);
                        ImmReleaseContext(hwnd, hImc);
                    }
                }
                InvalidateRect(hwnd, nullptr, FALSE);
                return 0;
            }

            case WM_LBUTTONUP:
            {
                POINT pt = {GET_X_LPARAM(lParam), GET_Y_LPARAM(lParam)};
                Point mousePos(static_cast<float>(pt.x), static_cast<float>(pt.y));
                for (auto &comp : pWindow->m_components)
                {
                    if (comp->HandleMouseUp(mousePos, 0))
                    {
                        pWindow->Emit(11, comp->GetId(), 0.0,
                                      (int)mousePos.x, (int)mousePos.y);
                        break;
                    }
                }
                InvalidateRect(hwnd, nullptr, FALSE);
                return 0;
            }

            case WM_LBUTTONDBLCLK:
            {
                POINT pt = {GET_X_LPARAM(lParam), GET_Y_LPARAM(lParam)};
                Point mousePos(static_cast<float>(pt.x), static_cast<float>(pt.y));
                for (auto &comp : pWindow->m_components)
                {
                    const Rect &b = comp->GetBounds();
                    if (mousePos.x >= b.left && mousePos.x <= b.right &&
                        mousePos.y >= b.top && mousePos.y <= b.bottom)
                    {
                        pWindow->Emit(15, comp->GetId(), 0.0,
                                      (int)mousePos.x, (int)mousePos.y);
                        break;
                    }
                }
                return 0;
            }

            case WM_TIMER:
                // 动画帧：只在确实有动画在跑、有人请求帧或 toast 活跃时才重绘
                //（省电、不空转；toast 淡入淡出靠这里驱动到过期为止）。
                // v3：活跃期间提升系统定时器分辨率（WM_TIMER 8ms 心跳下
                // vsync 主导帧率 ≈60fps；空闲即还原）。
                if (wParam == kAnimTimerId)
                {
                    if (pWindow->HasActiveAnimation() || pWindow->NeedsFrame() ||
                        pWindow->ToastActive())
                    {
                        EnsureHighResTiming();
                        InvalidateRect(hwnd, nullptr, FALSE);
                    }
                    else
                    {
                        ReleaseHighResTiming();
                    }
                }
                return 0;

            case WM_CHAR:
                if (pWindow)
                {
                    pWindow->OnChar(static_cast<wchar_t>(wParam));
                    // 字符事件：有焦点组件给组件，否则窗口级（id=0）
                    int tid = pWindow->m_focusedComponent ? pWindow->m_focusedComponent->GetId() : 0;
                    pWindow->Emit(21, tid, (double)(int)static_cast<wchar_t>(wParam), 0, 0);
                }
                return 0;

            case WM_KEYDOWN:
                if (pWindow)
                {
                    pWindow->OnKeyDown(static_cast<int>(wParam));
                    int tid = pWindow->m_focusedComponent ? pWindow->m_focusedComponent->GetId() : 0;
                    pWindow->Emit(20, tid, (double)(int)wParam, 0, 0);
                }
                return 0;

            case WM_IME_STARTCOMPOSITION:
                if (pWindow)
                {
                    pWindow->m_isImeComposing = true;

                    
                    HIMC hImc = ImmGetContext(hwnd);
                    if (hImc)
                    {
                        COMPOSITIONFORM cf = {0};
                        cf.dwStyle = CFS_POINT;

                        if (pWindow->m_focusedComponent)
                        {
                            Rect bounds = pWindow->m_focusedComponent->GetBounds();
                            POINT pt = {(LONG)bounds.left, (LONG)bounds.bottom + 5};
                            ClientToScreen(hwnd, &pt);
                            cf.ptCurrentPos = pt;
                            ImmSetCompositionWindow(hImc, &cf);
                        }

                        
                        LOGFONTW lf = {0};
                        lf.lfHeight = -16;
                        lf.lfWeight = FW_NORMAL;
                        wcscpy_s(lf.lfFaceName, L"Segoe UI");
                        ImmSetCompositionFontW(hImc, &lf);

                        ImmReleaseContext(hwnd, hImc);
                    }
                }
                return DefWindowProc(hwnd, uMsg, wParam, lParam);

            case WM_IME_COMPOSITION:
                if (pWindow)
                {
                    HIMC hImc = ImmGetContext(hwnd);
                    if (hImc)
                    {
                        
                        if (lParam & GCS_COMPSTR)
                        {
                            LONG len = ImmGetCompositionStringW(hImc, GCS_COMPSTR, NULL, 0);
                            if (len > 0)
                            {
                                std::wstring compStr(len / sizeof(wchar_t), L'\0');
                                ImmGetCompositionStringW(hImc, GCS_COMPSTR, &compStr[0], len);
                                while (!compStr.empty() && compStr.back() == L'\0')
                                    compStr.pop_back();

                                if (pWindow->m_focusedComponent)
                                {
                                    if (auto *textBox = dynamic_cast<TextBox *>(pWindow->m_focusedComponent))
                                    {
                                        textBox->HandleImeComposition(compStr);
                                        InvalidateRect(hwnd, nullptr, FALSE);
                                    }
                                }
                            }
                        }

                        
                        if (lParam & GCS_RESULTSTR)
                        {
                            LONG len = ImmGetCompositionStringW(hImc, GCS_RESULTSTR, NULL, 0);
                            if (len > 0)
                            {
                                std::wstring result(len / sizeof(wchar_t), L'\0');
                                ImmGetCompositionStringW(hImc, GCS_RESULTSTR, &result[0], len);
                                while (!result.empty() && result.back() == L'\0')
                                    result.pop_back();

                                if (pWindow->m_focusedComponent)
                                {
                                    if (auto *textBox = dynamic_cast<TextBox *>(pWindow->m_focusedComponent))
                                    {
                                        
                                        textBox->SetImeComposing(false);
                                        
                                        if (!result.empty())
                                        {
                                            textBox->HandleImeCommit(result);
                                        }
                                        InvalidateRect(hwnd, nullptr, FALSE);
                                    }
                                }
                            }
                        }

                        ImmReleaseContext(hwnd, hImc);
                    }
                }
                return DefWindowProc(hwnd, uMsg, wParam, lParam);

            case WM_IME_ENDCOMPOSITION:
                if (pWindow)
                {
                    pWindow->m_isImeComposing = false;

                    if (pWindow->m_focusedComponent)
                    {
                        if (auto *textBox = dynamic_cast<TextBox *>(pWindow->m_focusedComponent))
                        {
                            textBox->SetImeComposing(false);
                            textBox->HandleImeComposition(L""); 
                            InvalidateRect(hwnd, nullptr, FALSE);
                        }
                    }
                }
                return DefWindowProc(hwnd, uMsg, wParam, lParam);

            
            case WM_IME_NOTIFY:
                
                return DefWindowProc(hwnd, uMsg, wParam, lParam);

            case WM_MOUSEMOVE:
            {
                POINT pt = {GET_X_LPARAM(lParam), GET_Y_LPARAM(lParam)};
                pWindow->UpdateHover(Point(static_cast<float>(pt.x),
                                           static_cast<float>(pt.y)));
                InvalidateRect(hwnd, nullptr, FALSE);
                return 0;
            }

            case WM_MOUSEWHEEL:
            {
                // 滚轮坐标是**屏幕坐标**，转客户区后按组件命中（v3  2）；
                // 逆序遍历 = 后加组件优先（与双击命中一致）。
                POINT pt = {GET_X_LPARAM(lParam), GET_Y_LPARAM(lParam)};
                ScreenToClient(hwnd, &pt);
                Point mousePos(static_cast<float>(pt.x), static_cast<float>(pt.y));
                float delta = (float)GET_WHEEL_DELTA_WPARAM(wParam) / (float)WHEEL_DELTA;
                for (auto it = pWindow->m_components.rbegin();
                     it != pWindow->m_components.rend(); ++it)
                {
                    if ((*it)->IsVisible() && (*it)->HandleMouseWheel(delta, mousePos))
                        break;
                }
                InvalidateRect(hwnd, nullptr, FALSE);
                return 0;
            }

            case WM_DROPFILES:
            {
                // v3  5：文件拖放 → DropSink（路径列表 + 落点客户坐标）。
                // HDROP 用完必须 DragFinish（系统释放内部分配）。
                HDROP hd = reinterpret_cast<HDROP>(wParam);
                UINT n = DragQueryFileW(hd, 0xFFFFFFFF, nullptr, 0);
                POINT pt = {0, 0};
                DragQueryPoint(hd, &pt);
                std::vector<std::wstring> files;
                files.reserve(n);
                for (UINT i = 0; i < n; ++i)
                {
                    UINT len = DragQueryFileW(hd, i, nullptr, 0);
                    if (len == 0) continue;
                    std::wstring s(len, L'\0');
                    DragQueryFileW(hd, i, &s[0], len + 1);
                    while (!s.empty() && s.back() == L'\0') s.pop_back();
                    files.push_back(s);
                }
                DragFinish(hd);
                if (pWindow->m_dropSink)
                    pWindow->m_dropSink(files, pt.x, pt.y);
                return 0;
            }

            case WM_DESTROY:
                // 多窗口：最后一个窗口销毁才退出消息循环。
                // 注意：这里**禁止同步析构窗口对象**（宿主在消息循环外统一
                // reap）—— 在 WM_DESTROY 栈内 erase unique_ptr 会触发
                // ~ModernWindow → DestroyWindow 重入销毁。
                {
                    pWindow->m_hwnd = nullptr;   // 防析构里重复 DestroyWindow
                    if (--s_aliveWindows <= 0)
                        PostQuitMessage(0);
                }
                return 0;
            }
        }

        return DefWindowProcW(hwnd, uMsg, wParam, lParam);
    }

} 