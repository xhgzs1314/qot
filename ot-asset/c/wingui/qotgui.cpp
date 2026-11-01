



#include <windows.h>
#include <d2d1.h>
#include <d2d1_1.h>
#include <dwrite.h>
#include <commctrl.h>
#include <string>
#include <memory>
#include <vector>
#include <chrono>
#include <cmath>

#pragma comment(lib, "d2d1.lib")
#pragma comment(lib, "dwrite.lib")
#pragma comment(lib, "dxguid.lib")
#pragma comment(lib, "comctl32.lib")


template <class T>
inline void SafeRelease(T **ppT)
{
    if (*ppT)
    {
        (*ppT)->Release();
        *ppT = nullptr;
    }
}


class ModernWindow
{
private:
    HWND m_hwnd;
    ID2D1Factory *m_pD2DFactory;
    ID2D1HwndRenderTarget *m_pRenderTarget;
    ID2D1SolidColorBrush *m_pBackgroundBrush;
    ID2D1LinearGradientBrush *m_pGradientBrush;
    ID2D1RoundedRectangleGeometry *m_pRoundedRect;
    IDWriteFactory *m_pDWriteFactory;
    IDWriteTextFormat *m_pTextFormat;
    IDWriteTextFormat *m_pTitleTextFormat;

    
    float m_animationProgress;
    float m_animationDirection;

    
    D2D1_SIZE_U m_size;

    
    static constexpr D2D1_COLOR_F COLOR_BG_START = {0.05f, 0.05f, 0.15f, 1.0f}; 
    static constexpr D2D1_COLOR_F COLOR_BG_END = {0.15f, 0.08f, 0.25f, 1.0f};   

    static constexpr D2D1_COLOR_F COLOR_ACCENT = {0.2f, 0.6f, 1.0f, 1.0f}; 
    static constexpr D2D1_COLOR_F COLOR_ACCENT_LIGHT = {0.4f, 0.8f, 1.0f, 1.0f};
    static constexpr D2D1_COLOR_F COLOR_TEXT = {0.95f, 0.95f, 1.0f, 1.0f};
    static constexpr D2D1_COLOR_F COLOR_TEXT_SECONDARY = {0.7f, 0.7f, 0.85f, 1.0f};

    
    std::chrono::steady_clock::time_point m_lastFrameTime;

public:
    ModernWindow() : m_hwnd(nullptr), m_pD2DFactory(nullptr), m_pRenderTarget(nullptr),
                     m_pBackgroundBrush(nullptr), m_pGradientBrush(nullptr),
                     m_pRoundedRect(nullptr), m_pDWriteFactory(nullptr),
                     m_pTextFormat(nullptr), m_pTitleTextFormat(nullptr),
                     m_animationProgress(0.0f), m_animationDirection(1.0f)
    {
        m_size.width = 800;
        m_size.height = 600;
    }

    ~ModernWindow()
    {
        Cleanup();
    }

    bool Initialize(HINSTANCE hInstance, int nCmdShow)
    {
        
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
            L"Modern GUI with Direct2D",
            WS_OVERLAPPEDWINDOW | WS_CLIPCHILDREN | WS_CLIPSIBLINGS,
            CW_USEDEFAULT, CW_USEDEFAULT,
            m_size.width, m_size.height,
            nullptr, nullptr, hInstance, this);

        if (!m_hwnd)
        {
            return false;
        }

        
        if (!InitializeD2D())
        {
            return false;
        }

        
        SetProcessDPIAware();

        
        ShowWindow(m_hwnd, nCmdShow);
        UpdateWindow(m_hwnd);

        m_lastFrameTime = std::chrono::steady_clock::now();

        return true;
    }

    void Run()
    {
        MSG msg;
        while (GetMessage(&msg, nullptr, 0, 0))
        {
            TranslateMessage(&msg);
            DispatchMessage(&msg);
        }
    }

private:
    bool InitializeD2D()
    {
        HRESULT hr = D2D1CreateFactory(
            D2D1_FACTORY_TYPE_SINGLE_THREADED,
            &m_pD2DFactory);

        if (FAILED(hr))
            return false;

        
        RECT rc;
        GetClientRect(m_hwnd, &rc);
        m_size.width = rc.right - rc.left;
        m_size.height = rc.bottom - rc.top;

        hr = m_pD2DFactory->CreateHwndRenderTarget(
            D2D1::RenderTargetProperties(
                D2D1_RENDER_TARGET_TYPE_DEFAULT,
                D2D1::PixelFormat(DXGI_FORMAT_B8G8R8A8_UNORM, D2D1_ALPHA_MODE_PREMULTIPLIED)),
            D2D1::HwndRenderTargetProperties(m_hwnd, m_size),
            &m_pRenderTarget);

        if (FAILED(hr))
            return false;

        m_pRenderTarget->SetAntialiasMode(D2D1_ANTIALIAS_MODE_PER_PRIMITIVE);

        
        CreateGradientBrush();

        
        m_pRenderTarget->CreateSolidColorBrush(COLOR_BG_START, &m_pBackgroundBrush);

        
        D2D1_ROUNDED_RECT roundedRect = D2D1::RoundedRect(
            D2D1::RectF(20.0f, 20.0f, 100.0f, 100.0f),
            20.0f, 20.0f);
        m_pD2DFactory->CreateRoundedRectangleGeometry(roundedRect, &m_pRoundedRect);

        
        hr = DWriteCreateFactory(
            DWRITE_FACTORY_TYPE_SHARED,
            __uuidof(IDWriteFactory),
            reinterpret_cast<IUnknown **>(&m_pDWriteFactory));

        if (SUCCEEDED(hr))
        {
            
            m_pDWriteFactory->CreateTextFormat(
                L"Segoe UI",
                nullptr,
                DWRITE_FONT_WEIGHT_LIGHT,
                DWRITE_FONT_STYLE_NORMAL,
                DWRITE_FONT_STRETCH_NORMAL,
                28.0f,
                L"en-us",
                &m_pTitleTextFormat);

            
            m_pDWriteFactory->CreateTextFormat(
                L"Segoe UI",
                nullptr,
                DWRITE_FONT_WEIGHT_NORMAL,
                DWRITE_FONT_STYLE_NORMAL,
                DWRITE_FONT_STRETCH_NORMAL,
                16.0f,
                L"en-us",
                &m_pTextFormat);

            if (m_pTitleTextFormat)
            {
                m_pTitleTextFormat->SetTextAlignment(DWRITE_TEXT_ALIGNMENT_CENTER);
                m_pTitleTextFormat->SetParagraphAlignment(DWRITE_PARAGRAPH_ALIGNMENT_CENTER);
            }

            if (m_pTextFormat)
            {
                m_pTextFormat->SetTextAlignment(DWRITE_TEXT_ALIGNMENT_CENTER);
                m_pTextFormat->SetParagraphAlignment(DWRITE_PARAGRAPH_ALIGNMENT_CENTER);
            }
        }

        return true;
    }

    void CreateGradientBrush()
    {
        if (!m_pRenderTarget)
            return;

        SafeRelease(&m_pGradientBrush);

        D2D1_GRADIENT_STOP stops[] = {
            {0.0f, COLOR_BG_START},
            {1.0f, COLOR_BG_END}};

        ID2D1GradientStopCollection *pGradientStops = nullptr;
        HRESULT hr = m_pRenderTarget->CreateGradientStopCollection(
            stops, 2, &pGradientStops);

        if (SUCCEEDED(hr))
        {
            hr = m_pRenderTarget->CreateLinearGradientBrush(
                D2D1::LinearGradientBrushProperties(
                    D2D1::Point2F(0, 0),
                    D2D1::Point2F(0, static_cast<float>(m_size.height))),
                pGradientStops,
                &m_pGradientBrush);
            pGradientStops->Release();
        }
    }

    void Resize()
    {
        if (!m_pRenderTarget)
            return;

        RECT rc;
        GetClientRect(m_hwnd, &rc);
        m_size.width = rc.right - rc.left;
        m_size.height = rc.bottom - rc.top;

        m_pRenderTarget->Resize(m_size);
        CreateGradientBrush();
    }

    void UpdateAnimation()
    {
        auto now = std::chrono::steady_clock::now();
        float deltaTime = std::chrono::duration<float>(now - m_lastFrameTime).count();
        m_lastFrameTime = now;

        
        if (deltaTime > 0.1f)
            deltaTime = 0.1f;

        
        m_animationProgress += m_animationDirection * deltaTime * 0.5f;
        if (m_animationProgress >= 1.0f)
        {
            m_animationProgress = 1.0f;
            m_animationDirection = -1.0f;
        }
        else if (m_animationProgress <= 0.0f)
        {
            m_animationProgress = 0.0f;
            m_animationDirection = 1.0f;
        }
    }

    void DrawButton(float x, float y, float width, float height, const wchar_t *text, bool isHovered)
    {
        float hoverProgress = isHovered ? 0.2f : 0.0f;

        
        D2D1_ROUNDED_RECT buttonRect = D2D1::RoundedRect(
            D2D1::RectF(x, y, x + width, y + height),
            10.0f, 10.0f);

        
        D2D1_COLOR_F buttonColor = {
            COLOR_ACCENT.r + hoverProgress,
            COLOR_ACCENT.g + hoverProgress * 0.5f,
            COLOR_ACCENT.b + hoverProgress * 0.3f,
            0.8f};

        ID2D1SolidColorBrush *pButtonBrush = nullptr;
        m_pRenderTarget->CreateSolidColorBrush(buttonColor, &pButtonBrush);

        m_pRenderTarget->FillRoundedRectangle(buttonRect, pButtonBrush);
        m_pRenderTarget->DrawRoundedRectangle(buttonRect, m_pBackgroundBrush, 2.0f);

        
        if (m_pTextFormat && m_pRenderTarget)
        {
            
            D2D1_RECT_F textRect = D2D1::RectF(x, y, x + width, y + height);
            m_pRenderTarget->DrawText(
                text, wcslen(text),
                m_pTextFormat,
                textRect,
                m_pBackgroundBrush);
        }

        SafeRelease(&pButtonBrush);
    }

    void DrawCard(float x, float y, float width, float height, float animationOffset)
    {
        
        for (int i = 3; i > 0; i--)
        {
            D2D1_ROUNDED_RECT shadowRect = D2D1::RoundedRect(
                D2D1::RectF(x + static_cast<float>(i), y + static_cast<float>(i),
                            x + width + static_cast<float>(i), y + height + static_cast<float>(i)),
                15.0f, 15.0f);

            ID2D1SolidColorBrush *pShadowBrush = nullptr;
            D2D1_COLOR_F shadowColor = {0, 0, 0, 0.1f - i * 0.02f};
            m_pRenderTarget->CreateSolidColorBrush(shadowColor, &pShadowBrush);
            m_pRenderTarget->FillRoundedRectangle(shadowRect, pShadowBrush);
            SafeRelease(&pShadowBrush);
        }

        
        D2D1_ROUNDED_RECT cardRect = D2D1::RoundedRect(
            D2D1::RectF(x, y, x + width, y + height),
            15.0f, 15.0f);

        ID2D1SolidColorBrush *pCardBrush = nullptr;
        D2D1_COLOR_F cardColor = {
            0.1f + animationOffset * 0.05f,
            0.08f + animationOffset * 0.03f,
            0.12f + animationOffset * 0.08f,
            0.95f};
        m_pRenderTarget->CreateSolidColorBrush(cardColor, &pCardBrush);

        m_pRenderTarget->FillRoundedRectangle(cardRect, pCardBrush);
        m_pRenderTarget->DrawRoundedRectangle(cardRect, m_pGradientBrush, 2.0f);

        SafeRelease(&pCardBrush);
    }

    void DrawContent()
    {
        if (!m_pRenderTarget)
            return;

        float width = static_cast<float>(m_size.width);
        float height = static_cast<float>(m_size.height);

        
        if (m_pTitleTextFormat)
        {
            const wchar_t title[] = L"Modern Direct2D Interface";
            D2D1_RECT_F titleRect = D2D1::RectF(50, 50, width - 50, 100);
            m_pRenderTarget->DrawText(
                title, wcslen(title),
                m_pTitleTextFormat,
                titleRect,
                m_pBackgroundBrush);
        }

        
        float cardWidth = 220.0f;
        float cardHeight = 150.0f;
        float spacing = 30.0f;
        float startX = (width - (cardWidth * 3 + spacing * 2)) / 2;
        float startY = 150.0f;

        const wchar_t *cardTitles[] = {L"Performance", L"Modern UI", L"Direct2D"};

        for (int i = 0; i < 3; i++)
        {
            float x = startX + i * (cardWidth + spacing);
            float animationOffset = sinf(m_animationProgress * 3.14159f + static_cast<float>(i) * 2.0f) * 0.2f;
            DrawCard(x, startY, cardWidth, cardHeight, animationOffset);

            
            D2D1_ELLIPSE iconCircle = D2D1::Ellipse(
                D2D1::Point2F(x + cardWidth / 2, startY + 45),
                25.0f, 25.0f);

            ID2D1SolidColorBrush *pIconBrush = nullptr;
            D2D1_COLOR_F iconColor = {
                COLOR_ACCENT.r + animationOffset,
                COLOR_ACCENT.g,
                COLOR_ACCENT.b,
                0.8f};
            m_pRenderTarget->CreateSolidColorBrush(iconColor, &pIconBrush);
            m_pRenderTarget->FillEllipse(iconCircle, pIconBrush);

            
            if (m_pTextFormat)
            {
                D2D1_RECT_F cardTitleRect = D2D1::RectF(x + 10, startY + 80, x + cardWidth - 10, startY + 110);
                m_pRenderTarget->DrawText(
                    cardTitles[i], wcslen(cardTitles[i]),
                    m_pTextFormat,
                    cardTitleRect,
                    m_pBackgroundBrush);
            }

            SafeRelease(&pIconBrush);
        }

        
        float progressBarY = height - 100;
        float progressBarWidth = width - 100;

        
        D2D1_ROUNDED_RECT progressBg = D2D1::RoundedRect(
            D2D1::RectF(50, progressBarY, 50 + progressBarWidth, progressBarY + 30),
            15.0f, 15.0f);

        ID2D1SolidColorBrush *pBgBrush = nullptr;
        m_pRenderTarget->CreateSolidColorBrush(
            D2D1::ColorF(0.2f, 0.2f, 0.3f, 0.5f),
            &pBgBrush);
        m_pRenderTarget->FillRoundedRectangle(progressBg, pBgBrush);

        
        D2D1_ROUNDED_RECT progressFill = D2D1::RoundedRect(
            D2D1::RectF(50, progressBarY, 50 + progressBarWidth * m_animationProgress, progressBarY + 30),
            15.0f, 15.0f);

        ID2D1LinearGradientBrush *pProgressGradient = nullptr;
        D2D1_GRADIENT_STOP gradientStops[] = {
            {0.0f, COLOR_ACCENT},
            {1.0f, COLOR_ACCENT_LIGHT}};

        ID2D1GradientStopCollection *pStopCollection = nullptr;
        m_pRenderTarget->CreateGradientStopCollection(gradientStops, 2, &pStopCollection);

        m_pRenderTarget->CreateLinearGradientBrush(
            D2D1::LinearGradientBrushProperties(
                D2D1::Point2F(50, progressBarY),
                D2D1::Point2F(50 + progressBarWidth, progressBarY)),
            pStopCollection,
            &pProgressGradient);

        m_pRenderTarget->FillRoundedRectangle(progressFill, pProgressGradient);

        
        wchar_t progressText[64];
        swprintf_s(progressText, L"Loading... %.0f%%", m_animationProgress * 100);

        if (m_pTextFormat)
        {
            
            ID2D1SolidColorBrush *pTextBrush = nullptr;
            m_pRenderTarget->CreateSolidColorBrush(COLOR_TEXT_SECONDARY, &pTextBrush);

            D2D1_RECT_F progressTextRect = D2D1::RectF(50, progressBarY - 30, width - 50, progressBarY);
            m_pRenderTarget->DrawText(
                progressText, wcslen(progressText),
                m_pTextFormat,
                progressTextRect,
                pTextBrush 
            );

            SafeRelease(&pTextBrush);
        }

        SafeRelease(&pBgBrush);
        SafeRelease(&pProgressGradient);
        SafeRelease(&pStopCollection);
    }

    void OnPaint()
    {
        if (!m_pRenderTarget)
            return;

        UpdateAnimation();

        m_pRenderTarget->BeginDraw();

        
        m_pRenderTarget->FillRectangle(
            D2D1::RectF(0, 0, static_cast<float>(m_size.width), static_cast<float>(m_size.height)),
            m_pGradientBrush);

        
        DrawContent();

        HRESULT hr = m_pRenderTarget->EndDraw();
        if (hr == D2DERR_RECREATE_TARGET)
        {
            RecreateRenderTarget();
        }
    }

    void RecreateRenderTarget()
    {
        SafeRelease(&m_pRenderTarget);
        SafeRelease(&m_pBackgroundBrush);
        SafeRelease(&m_pGradientBrush);
        SafeRelease(&m_pRoundedRect);

        RECT rc;
        GetClientRect(m_hwnd, &rc);
        m_size.width = rc.right - rc.left;
        m_size.height = rc.bottom - rc.top;

        HRESULT hr = m_pD2DFactory->CreateHwndRenderTarget(
            D2D1::RenderTargetProperties(),
            D2D1::HwndRenderTargetProperties(m_hwnd, m_size),
            &m_pRenderTarget);

        if (SUCCEEDED(hr))
        {
            m_pRenderTarget->SetAntialiasMode(D2D1_ANTIALIAS_MODE_PER_PRIMITIVE);
            CreateGradientBrush();
            m_pRenderTarget->CreateSolidColorBrush(COLOR_BG_START, &m_pBackgroundBrush);
        }
    }

    void Cleanup()
    {
        SafeRelease(&m_pTitleTextFormat);
        SafeRelease(&m_pTextFormat);
        SafeRelease(&m_pDWriteFactory);
        SafeRelease(&m_pRoundedRect);
        SafeRelease(&m_pGradientBrush);
        SafeRelease(&m_pBackgroundBrush);
        SafeRelease(&m_pRenderTarget);
        SafeRelease(&m_pD2DFactory);

        if (m_hwnd)
        {
            DestroyWindow(m_hwnd);
            m_hwnd = nullptr;
        }
    }

    static LRESULT CALLBACK WindowProc(HWND hwnd, UINT uMsg, WPARAM wParam, LPARAM lParam)
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
            }
                return 0;

            case WM_SIZE:
                pWindow->Resize();
                InvalidateRect(hwnd, nullptr, FALSE);
                return 0;

            case WM_ERASEBKGND:
                return 1; 

            case WM_DESTROY:
                PostQuitMessage(0);
                return 0;
            }
        }

        return DefWindowProcW(hwnd, uMsg, wParam, lParam);
    }
};

int WINAPI WinMain(HINSTANCE hInstance, HINSTANCE, LPSTR, int nCmdShow)
{
    
    INITCOMMONCONTROLSEX icex;
    icex.dwSize = sizeof(INITCOMMONCONTROLSEX);
    icex.dwICC = ICC_WIN95_CLASSES;
    InitCommonControlsEx(&icex);

    ModernWindow window;
    if (!window.Initialize(hInstance, nCmdShow))
    {
        return 1;
    }

    window.Run();
    return 0;
}