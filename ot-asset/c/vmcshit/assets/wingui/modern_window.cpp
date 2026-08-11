
#include "modern_window.h"
#include <commctrl.h>
#include <imm.h> 

#pragma comment(lib, "imm32.lib") 


#ifndef GET_X_LPARAM
#define GET_X_LPARAM(lp) ((int)(short)LOWORD(lp))
#endif
#ifndef GET_Y_LPARAM
#define GET_Y_LPARAM(lp) ((int)(short)HIWORD(lp))
#endif

namespace gui
{

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
            DestroyWindow(m_hwnd);
            m_hwnd = nullptr;
        }
    }

    bool ModernWindow::Initialize(HINSTANCE hInstance, int nCmdShow,
                                  const std::wstring &title, int width, int height)
    {
        m_size = Size(static_cast<float>(width), static_cast<float>(height));

        
        WNDCLASSEXW wcex = {};
        wcex.cbSize = sizeof(WNDCLASSEXW);
        wcex.style = CS_HREDRAW | CS_VREDRAW;
        wcex.lpfnWndProc = WindowProc;
        wcex.hInstance = hInstance;
        wcex.hCursor = LoadCursor(nullptr, IDC_ARROW);
        wcex.hbrBackground = nullptr;
        wcex.lpszClassName = L"ModernWindowClass";

        if (!RegisterClassExW(&wcex))
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

        
        SetProcessDPIAware();

        ShowWindow(m_hwnd, nCmdShow);
        UpdateWindow(m_hwnd);

        m_lastFrameTime = std::chrono::steady_clock::now();

        return true;
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

        UpdateAnimation();
        HandleMouse();

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
                component->Draw(&m_renderer);
            }
        }

        m_renderer.EndDraw();
    }

    void ModernWindow::OnResize()
    {
        RECT rc;
        GetClientRect(m_hwnd, &rc);
        m_size = Size(static_cast<float>(rc.right - rc.left),
                      static_cast<float>(rc.bottom - rc.top));
        m_renderer.Resize(m_size);
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
                        break;
                    }
                }
                InvalidateRect(hwnd, nullptr, FALSE);
                return 0;
            }

            case WM_CHAR:
                if (pWindow)
                    pWindow->OnChar(static_cast<wchar_t>(wParam));
                return 0;

            case WM_KEYDOWN:
                if (pWindow)
                    pWindow->OnKeyDown(static_cast<int>(wParam));
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
                InvalidateRect(hwnd, nullptr, FALSE);
                return 0;

            case WM_DESTROY:
                PostQuitMessage(0);
                return 0;
            }
        }

        return DefWindowProcW(hwnd, uMsg, wParam, lParam);
    }

} 