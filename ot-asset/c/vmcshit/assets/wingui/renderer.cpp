
#include "renderer.h"
#include <d2d1.h>
#include <dwrite.h>

namespace gui {

Renderer::Renderer()
    : m_hwnd(nullptr)
    , m_d2dFactory(nullptr)
    , m_renderTarget(nullptr)
    , m_writeFactory(nullptr) {
}

Renderer::~Renderer() {
    ReleaseResources();
    
    for (auto& cache : m_brushCache) {
        if (cache.brush) cache.brush->Release();
    }
    for (auto& cache : m_textFormatCache) {
        if (cache.format) cache.format->Release();
    }
    
    if (m_d2dFactory) m_d2dFactory->Release();
}

bool Renderer::Initialize(HWND hwnd) {
    m_hwnd = hwnd;
    
    HRESULT hr = D2D1CreateFactory(
        D2D1_FACTORY_TYPE_SINGLE_THREADED,
        &m_d2dFactory);
    if (FAILED(hr)) return false;
    
    hr = DWriteCreateFactory(
        DWRITE_FACTORY_TYPE_SHARED,
        __uuidof(IDWriteFactory),
        reinterpret_cast<IUnknown**>(&m_writeFactory));
    if (FAILED(hr)) return false;
    
    RECT rc;
    GetClientRect(hwnd, &rc);
    m_size = Size(static_cast<float>(rc.right - rc.left), 
                  static_cast<float>(rc.bottom - rc.top));
    
    CreateResources();
    return true;
}

void Renderer::CreateResources() {
    if (m_renderTarget) return;
    
    HRESULT hr = m_d2dFactory->CreateHwndRenderTarget(
        D2D1::RenderTargetProperties(
            D2D1_RENDER_TARGET_TYPE_DEFAULT,
            D2D1::PixelFormat(DXGI_FORMAT_B8G8R8A8_UNORM, D2D1_ALPHA_MODE_PREMULTIPLIED)),
        D2D1::HwndRenderTargetProperties(m_hwnd, D2D1::SizeU(static_cast<UINT>(m_size.width), static_cast<UINT>(m_size.height))),
        &m_renderTarget);
    
    if (SUCCEEDED(hr)) {
        m_renderTarget->SetAntialiasMode(D2D1_ANTIALIAS_MODE_PER_PRIMITIVE);
    }
}

void Renderer::ReleaseResources() {
    if (m_renderTarget) {
        m_renderTarget->Release();
        m_renderTarget = nullptr;
    }
}

void Renderer::Resize(const Size& size) {
    m_size = size;
    if (m_renderTarget) {
        m_renderTarget->Resize(D2D1::SizeU(static_cast<UINT>(size.width), static_cast<UINT>(size.height)));
    }
}

void Renderer::BeginDraw() {
    if (!m_renderTarget) CreateResources();
    if (m_renderTarget) {
        m_renderTarget->BeginDraw();
    }
}

void Renderer::EndDraw() {
    if (m_renderTarget) {
        HRESULT hr = m_renderTarget->EndDraw();
        if (hr == D2DERR_RECREATE_TARGET) {
            ReleaseResources();
        }
    }
}

void Renderer::Clear(const Color& color) {
    if (m_renderTarget) {
        m_renderTarget->Clear(color.ToD2D());
    }
}

ID2D1SolidColorBrush* Renderer::GetBrush(const Color& color) {
    
    for (auto& cache : m_brushCache) {
        if (cache.color.r == color.r && cache.color.g == color.g &&
            cache.color.b == color.b && cache.color.a == color.a) {
            return cache.brush;
        }
    }
    
    
    ID2D1SolidColorBrush* brush = nullptr;
    if (m_renderTarget) {
        m_renderTarget->CreateSolidColorBrush(color.ToD2D(), &brush);
        if (brush) {
            m_brushCache.push_back({color, brush});
        }
    }
    return brush;
}

void Renderer::FillRect(const Rect& rect, const Color& color) {
    if (m_renderTarget) {
        ID2D1SolidColorBrush* brush = GetBrush(color);
        if (brush) {
            m_renderTarget->FillRectangle(rect.ToD2D(), brush);
        }
    }
}

void Renderer::DrawRect(const Rect& rect, const Color& color, float strokeWidth) {
    if (m_renderTarget) {
        ID2D1SolidColorBrush* brush = GetBrush(color);
        if (brush) {
            m_renderTarget->DrawRectangle(rect.ToD2D(), brush, strokeWidth);
        }
    }
}

void Renderer::FillRoundedRect(const RoundedRect& rect, const Color& color) {
    if (m_renderTarget) {
        ID2D1SolidColorBrush* brush = GetBrush(color);
        if (brush) {
            m_renderTarget->FillRoundedRectangle(rect.ToD2D(), brush);
        }
    }
}

void Renderer::DrawRoundedRect(const RoundedRect& rect, const Color& color, float strokeWidth) {
    if (m_renderTarget) {
        ID2D1SolidColorBrush* brush = GetBrush(color);
        if (brush) {
            m_renderTarget->DrawRoundedRectangle(rect.ToD2D(), brush, strokeWidth);
        }
    }
}

void Renderer::FillEllipse(const Ellipse& ellipse, const Color& color) {
    if (m_renderTarget) {
        ID2D1SolidColorBrush* brush = GetBrush(color);
        if (brush) {
            m_renderTarget->FillEllipse(ellipse.ToD2D(), brush);
        }
    }
}
void Renderer::DrawEllipse(const Ellipse& ellipse, const Color& color, float strokeWidth) {
    if (m_renderTarget) {
        ID2D1SolidColorBrush* brush = GetBrush(color);
        if (brush) {
            m_renderTarget->DrawEllipse(ellipse.ToD2D(), brush, strokeWidth);
        }
    }
}
void Renderer::DrawLine(const Point& p1, const Point& p2, const Color& color, float strokeWidth) {
    if (m_renderTarget) {
        ID2D1SolidColorBrush* brush = GetBrush(color);
        if (brush) {
            m_renderTarget->DrawLine(
                D2D1::Point2F(p1.x, p1.y),
                D2D1::Point2F(p2.x, p2.y),
                brush, strokeWidth);
        }
    }
}

void Renderer::FillRectGradient(const Rect& rect, const Color& startColor, const Color& endColor, bool vertical) {
    if (!m_renderTarget) return;
    
    ID2D1GradientStopCollection* stopCollection = nullptr;
    D2D1_GRADIENT_STOP stops[] = {
        {0.0f, startColor.ToD2D()},
        {1.0f, endColor.ToD2D()}
    };
    
    m_renderTarget->CreateGradientStopCollection(stops, 2, &stopCollection);
    
    ID2D1LinearGradientBrush* gradientBrush = nullptr;
    Point p1(rect.left, vertical ? rect.top : rect.left);
    Point p2(rect.left, vertical ? rect.bottom : rect.right);
    
    m_renderTarget->CreateLinearGradientBrush(
        D2D1::LinearGradientBrushProperties(
            D2D1::Point2F(p1.x, p1.y),
            D2D1::Point2F(p2.x, p2.y)),
        stopCollection,
        &gradientBrush);
    
    if (gradientBrush) {
        m_renderTarget->FillRectangle(rect.ToD2D(), gradientBrush);
        gradientBrush->Release();
    }
    if (stopCollection) stopCollection->Release();
}

void Renderer::FillRoundedRectGradient(const RoundedRect& rect, const Color& startColor, const Color& endColor, bool vertical) {
    if (!m_renderTarget) return;
    
    ID2D1GradientStopCollection* stopCollection = nullptr;
    D2D1_GRADIENT_STOP stops[] = {
        {0.0f, startColor.ToD2D()},
        {1.0f, endColor.ToD2D()}
    };
    
    m_renderTarget->CreateGradientStopCollection(stops, 2, &stopCollection);
    
    ID2D1LinearGradientBrush* gradientBrush = nullptr;
    Point p1(rect.rect.left, vertical ? rect.rect.top : rect.rect.left);
    Point p2(rect.rect.left, vertical ? rect.rect.bottom : rect.rect.right);
    
    m_renderTarget->CreateLinearGradientBrush(
        D2D1::LinearGradientBrushProperties(
            D2D1::Point2F(p1.x, p1.y),
            D2D1::Point2F(p2.x, p2.y)),
        stopCollection,
        &gradientBrush);
    
    if (gradientBrush) {
        m_renderTarget->FillRoundedRectangle(rect.ToD2D(), gradientBrush);
        gradientBrush->Release();
    }
    if (stopCollection) stopCollection->Release();
}

IDWriteTextFormat* Renderer::GetTextFormat(float fontSize, bool bold) {
    for (auto& cache : m_textFormatCache) {
        if (cache.fontSize == fontSize && cache.bold == bold) {
            return cache.format;
        }
    }
    
    IDWriteTextFormat* format = nullptr;
    if (m_writeFactory) {
        m_writeFactory->CreateTextFormat(
            L"Segoe UI",
            nullptr,
            bold ? DWRITE_FONT_WEIGHT_BOLD : DWRITE_FONT_WEIGHT_NORMAL,
            DWRITE_FONT_STYLE_NORMAL,
            DWRITE_FONT_STRETCH_NORMAL,
            fontSize,
            L"en-us",
            &format);
        
        if (format) {
            m_textFormatCache.push_back({fontSize, bold, format});
        }
    }
    return format;
}

void Renderer::DrawText(const std::wstring& text, const Rect& rect, const Color& color, float fontSize, bool bold, bool center) {
    if (!m_renderTarget || text.empty()) return;
    
    IDWriteTextFormat* format = GetTextFormat(fontSize, bold);
    ID2D1SolidColorBrush* brush = GetBrush(color);
    
    if (format && brush) {
        if (center) {
            format->SetTextAlignment(DWRITE_TEXT_ALIGNMENT_CENTER);
            format->SetParagraphAlignment(DWRITE_PARAGRAPH_ALIGNMENT_CENTER);
        } else {
            format->SetTextAlignment(DWRITE_TEXT_ALIGNMENT_LEADING);
            format->SetParagraphAlignment(DWRITE_PARAGRAPH_ALIGNMENT_NEAR);
        }
        
        m_renderTarget->DrawText(
            text.c_str(), static_cast<UINT32>(text.length()),
            format, rect.ToD2D(), brush);
    }
}

Size Renderer::MeasureText(const std::wstring& text, float fontSize, bool bold) {
    if (text.empty()) return Size(0, 0);
    
    IDWriteTextFormat* format = GetTextFormat(fontSize, bold);
    if (!format) return Size(0, 0);
    
    IDWriteTextLayout* layout = nullptr;
    m_writeFactory->CreateTextLayout(
        text.c_str(), static_cast<UINT32>(text.length()),
        format, 10000.0f, 10000.0f, &layout);
    
    DWRITE_TEXT_METRICS metrics = {};
    if (layout) {
        layout->GetMetrics(&metrics);
        layout->Release();
    }
    
    return Size(metrics.width, metrics.height);
}

} 