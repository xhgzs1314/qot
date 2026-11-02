
#include "renderer.h"
#include <d2d1.h>
#include <d2d1_1.h>
#include <dwrite.h>
#include <d3d11.h>
#include <dxgi1_2.h>
#include <dcomp.h>
#include <wincodec.h>
#include <windows.h>

namespace gui {

namespace {

// 进程级共享 D3D11 设备（BGRA 支持；硬件不可用回退 WARP 软渲染）。
// 多窗口共享一个设备：省显存与初始化开销；D2D 工厂仍是单线程模型，
// 所有窗口运行在宿主消息循环线程上，无需多线程保护。
ID3D11Device *SharedD3DDevice()
{
    static ID3D11Device *dev = nullptr;
    static bool tried = false;
    if (!tried)
    {
        tried = true;
        D3D_FEATURE_LEVEL fl = D3D_FEATURE_LEVEL_11_0;
        HRESULT hr = D3D11CreateDevice(
            nullptr, D3D_DRIVER_TYPE_HARDWARE, nullptr,
            D3D11_CREATE_DEVICE_BGRA_SUPPORT, nullptr, 0,
            D3D11_SDK_VERSION, &dev, &fl, nullptr);
        if (FAILED(hr) || !dev)
        {
            dev = nullptr;
            D3D11CreateDevice(
                nullptr, D3D_DRIVER_TYPE_WARP, nullptr,
                D3D11_CREATE_DEVICE_BGRA_SUPPORT, nullptr, 0,
                D3D11_SDK_VERSION, &dev, &fl, nullptr);
        }
    }
    return dev;
}

// DCompositionCreateDevice2 动态加载（dcomp.dll 在 Win8+ 存在；
// 老系统没有 → 返回 nullptr，走 HwndRT 回退，不硬依赖导入库）
typedef HRESULT(WINAPI *PFN_DCompCreateDevice2)(IUnknown *, REFIID, void **);

PFN_DCompCreateDevice2 DCompCreateFn()
{
    static PFN_DCompCreateDevice2 pfn = nullptr;
    static bool tried = false;
    if (!tried)
    {
        tried = true;
        HMODULE m = LoadLibraryW(L"dcomp.dll");
        if (m)
            pfn = reinterpret_cast<PFN_DCompCreateDevice2>(
                GetProcAddress(m, "DCompositionCreateDevice2"));
    }
    return pfn;
}

}  // namespace

Renderer::Renderer()
    : m_hwnd(nullptr)
    , m_d2dFactory(nullptr)
    , m_renderTarget(nullptr)
    , m_hwndRT(nullptr)
    , m_writeFactory(nullptr)
    , m_swapchain(nullptr)
    , m_d2dDevice(nullptr)
    , m_ctx(nullptr)
    , m_targetBitmap(nullptr)
    , m_dcompDevice(nullptr)
    , m_dcompTarget(nullptr)
    , m_dcompVisual(nullptr)
    , m_useDComp(false)
    , m_fontFamily(L"Segoe UI")
    , m_wicFactory(nullptr)
    , m_blurCmd(nullptr)
    , m_blurOldTarget(nullptr)
    , m_blurRadius(0.0f)
    , m_blurBounds(0, 0, 0, 0) {
}

Renderer::~Renderer() {
    // 模糊层若悬空（异常路径），先还原目标再整体释放
    if (m_blurCmd && m_ctx) m_ctx->SetTarget(m_blurOldTarget);
    if (m_blurCmd) { m_blurCmd->Release(); m_blurCmd = nullptr; }
    if (m_blurOldTarget) { m_blurOldTarget->Release(); m_blurOldTarget = nullptr; }
    ReleaseRenderTargets();
    ReleaseDCompStack();

    for (auto& cache : m_brushCache) {
        if (cache.brush) cache.brush->Release();
    }
    for (auto& cache : m_textFormatCache) {
        if (cache.format) cache.format->Release();
    }

    if (m_d2dFactory) m_d2dFactory->Release();
    if (m_writeFactory) m_writeFactory->Release();
    if (m_wicFactory) static_cast<IUnknown *>(m_wicFactory)->Release();
}

bool Renderer::Initialize(HWND hwnd) {
    m_hwnd = hwnd;

    HRESULT hr = D2D1CreateFactory(
        D2D1_FACTORY_TYPE_SINGLE_THREADED,
        __uuidof(ID2D1Factory1),
        nullptr,
        reinterpret_cast<void **>(&m_d2dFactory));
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

// ---------------------------------------------------------------------------
// 后端选择：优先 DirectComposition（无闪烁合成 + 透明/玻璃态能力），
// 任何一步失败都完整回退经典 HwndRenderTarget —— 对外行为与 v1 一致。
// ---------------------------------------------------------------------------
void Renderer::CreateResources() {
    if (m_renderTarget) return;

    // QOT_GUI_NO_DCOMP=1：诊断开关，强制走经典 HwndRenderTarget 回退路径
    wchar_t noDcomp[4] = {0};
    bool dcompDisabled = GetEnvironmentVariableW(L"QOT_GUI_NO_DCOMP", noDcomp, 4) > 0;

    if (!dcompDisabled && !m_useDComp && CreateDCompResources())
    {
        m_useDComp = true;
        m_renderTarget = m_ctx;   // 设备上下文即统一绘制入口
    }
    else
    {
        m_useDComp = false;
        CreateHwndResources();
    }

    if (m_renderTarget)
    {
        m_renderTarget->SetAntialiasMode(D2D1_ANTIALIAS_MODE_PER_PRIMITIVE);
    }
}

bool Renderer::CreateDCompResources() {
    ID3D11Device *d3d = SharedD3DDevice();
    if (!d3d) return false;

    // D2D 设备 + 设备上下文（绘制原语与 HwndRT 同一套基类接口）
    IDXGIDevice *dxgiDev = nullptr;
    if (FAILED(d3d->QueryInterface(__uuidof(IDXGIDevice), reinterpret_cast<void **>(&dxgiDev))))
        return false;

    HRESULT hr = m_d2dFactory->CreateDevice(dxgiDev, &m_d2dDevice);
    if (SUCCEEDED(hr))
        hr = m_d2dDevice->CreateDeviceContext(D2D1_DEVICE_CONTEXT_OPTIONS_NONE, &m_ctx);
    if (FAILED(hr))
    {
        dxgiDev->Release();
        return false;
    }

    // 合成交换链（flip 模型 + 预乘 alpha：透明与 DWM 玻璃态的前提）
    IDXGIAdapter *adapter = nullptr;
    IDXGIFactory2 *factory2 = nullptr;
    hr = dxgiDev->GetAdapter(&adapter);
    if (SUCCEEDED(hr) && adapter)
        hr = adapter->GetParent(__uuidof(IDXGIFactory2), reinterpret_cast<void **>(&factory2));

    if (SUCCEEDED(hr) && factory2)
    {
        DXGI_SWAP_CHAIN_DESC1 desc = {};
        desc.Width = static_cast<UINT>(m_size.width > 0 ? m_size.width : 1);
        desc.Height = static_cast<UINT>(m_size.height > 0 ? m_size.height : 1);
        desc.Format = DXGI_FORMAT_B8G8R8A8_UNORM;
        desc.SampleDesc.Count = 1;
        desc.BufferUsage = DXGI_USAGE_RENDER_TARGET_OUTPUT;
        desc.BufferCount = 2;
        desc.SwapEffect = DXGI_SWAP_EFFECT_FLIP_SEQUENTIAL;
        desc.AlphaMode = DXGI_ALPHA_MODE_PREMULTIPLIED;
        hr = factory2->CreateSwapChainForComposition(d3d, &desc, nullptr, &m_swapchain);
    }
    if (adapter) adapter->Release();
    if (factory2) factory2->Release();
    dxgiDev->Release();
    if (FAILED(hr) || !m_swapchain) return false;

    // DirectComposition 设备（dcomp.dll 动态解析）+ 窗口 target/visual。
    // DCompositionCreateDevice2 接受 D2D 设备作渲染设备：合成器与绘制
    // 共享同一呈现设备，提交链最短。
    PFN_DCompCreateDevice2 pfn = DCompCreateFn();
    if (!pfn) return false;
    if (FAILED(pfn(m_d2dDevice, __uuidof(IDCompositionDevice),
                   reinterpret_cast<void **>(&m_dcompDevice))))
        return false;
    if (FAILED(m_dcompDevice->CreateTargetForHwnd(m_hwnd, TRUE, &m_dcompTarget)))
        return false;
    if (FAILED(m_dcompDevice->CreateVisual(&m_dcompVisual)))
        return false;
    if (FAILED(m_dcompVisual->SetContent(m_swapchain)))
        return false;
    if (FAILED(m_dcompTarget->SetRoot(m_dcompVisual)))
        return false;
    m_dcompDevice->Commit();

    return RebuildDCompTarget();
}

bool Renderer::RebuildDCompTarget() {
    if (!m_ctx || !m_swapchain) return false;

    m_ctx->SetTarget(nullptr);
    if (m_targetBitmap)
    {
        m_targetBitmap->Release();
        m_targetBitmap = nullptr;
    }

    HRESULT hr = m_swapchain->ResizeBuffers(
        0,
        static_cast<UINT>(m_size.width > 0 ? m_size.width : 1),
        static_cast<UINT>(m_size.height > 0 ? m_size.height : 1),
        DXGI_FORMAT_UNKNOWN, 0);
    if (FAILED(hr)) return false;

    IDXGISurface *surf = nullptr;
    hr = m_swapchain->GetBuffer(0, __uuidof(IDXGISurface), reinterpret_cast<void **>(&surf));
    if (FAILED(hr)) return false;

    D2D1_BITMAP_PROPERTIES1 bp = D2D1::BitmapProperties1(
        D2D1_BITMAP_OPTIONS_TARGET | D2D1_BITMAP_OPTIONS_CANNOT_DRAW,
        D2D1::PixelFormat(DXGI_FORMAT_B8G8R8A8_UNORM, D2D1_ALPHA_MODE_PREMULTIPLIED));
    hr = m_ctx->CreateBitmapFromDxgiSurface(surf, &bp, &m_targetBitmap);
    surf->Release();
    if (FAILED(hr)) return false;

    m_ctx->SetTarget(m_targetBitmap);
    FlushBrushCache();
    return true;
}

void Renderer::CreateHwndResources() {
    if (m_renderTarget) return;

    HRESULT hr = m_d2dFactory->CreateHwndRenderTarget(
        D2D1::RenderTargetProperties(
            D2D1_RENDER_TARGET_TYPE_DEFAULT,
            D2D1::PixelFormat(DXGI_FORMAT_B8G8R8A8_UNORM, D2D1_ALPHA_MODE_PREMULTIPLIED)),
        D2D1::HwndRenderTargetProperties(m_hwnd, D2D1::SizeU(static_cast<UINT>(m_size.width), static_cast<UINT>(m_size.height))),
        &m_hwndRT);

    if (SUCCEEDED(hr) && m_hwndRT) {
        m_renderTarget = m_hwndRT;
        m_renderTarget->SetAntialiasMode(D2D1_ANTIALIAS_MODE_PER_PRIMITIVE);
    }
}

void Renderer::ReleaseRenderTargets() {
    // 别名纪律：DComp 路径 m_renderTarget == m_ctx；回退路径 == m_hwndRT。
    // 同一对象只 Release 一次，三个指针一起置空 —— 顺序：先释放由目标
    // 设备分配的资源（笔刷/目标位图），再摘目标，最后释放目标设备本身。
    for (auto& cache : m_brushCache) {
        if (cache.brush) cache.brush->Release();
    }
    m_brushCache.clear();
    if (m_targetBitmap) {
        m_targetBitmap->Release();
        m_targetBitmap = nullptr;
    }
    if (m_ctx) m_ctx->SetTarget(nullptr);
    if (m_renderTarget) {
        m_renderTarget->Release();
        m_renderTarget = nullptr;
    }
    m_hwndRT = nullptr;   // 与 m_renderTarget 同一对象（回退路径）
    m_ctx = nullptr;      // 与 m_renderTarget 同一对象（DComp 路径）
}

void Renderer::ReleaseDCompStack() {
    if (m_dcompVisual) { m_dcompVisual->Release(); m_dcompVisual = nullptr; }
    if (m_dcompTarget) { m_dcompTarget->Release(); m_dcompTarget = nullptr; }
    if (m_dcompDevice) { m_dcompDevice->Release(); m_dcompDevice = nullptr; }
    if (m_swapchain) { m_swapchain->Release(); m_swapchain = nullptr; }
    if (m_ctx) { m_ctx->Release(); m_ctx = nullptr; }
    if (m_d2dDevice) { m_d2dDevice->Release(); m_d2dDevice = nullptr; }
    m_useDComp = false;
}

void Renderer::Resize(const Size& size) {
    m_size = size;
    if (m_useDComp) {
        if (m_ctx && m_swapchain) RebuildDCompTarget();
    } else if (m_hwndRT) {
        m_hwndRT->Resize(D2D1::SizeU(static_cast<UINT>(size.width), static_cast<UINT>(size.height)));
    }
}

void Renderer::BeginDraw() {
    if (!m_renderTarget) CreateResources();
    if (m_renderTarget) {
        m_renderTarget->BeginDraw();
    }
}

void Renderer::EndDraw() {
    if (m_useDComp && m_ctx) {
        HRESULT hr = m_ctx->EndDraw();
        if (SUCCEEDED(hr) && m_swapchain) {
            m_swapchain->Present(1, 0);   // 垂直同步呈现
            if (m_dcompDevice) m_dcompDevice->Commit();
        }
        if (hr == D2DERR_RECREATE_TARGET || hr == (HRESULT)DXGI_ERROR_DEVICE_REMOVED) {
            // 设备级失效：整套重建（下一帧 BeginDraw 自动走 CreateResources）
            ReleaseRenderTargets();
            ReleaseDCompStack();
        }
    } else if (m_renderTarget) {
        HRESULT hr = m_renderTarget->EndDraw();
        if (hr == D2DERR_RECREATE_TARGET) {
            ReleaseRenderTargets();
        }
    }
}

void Renderer::Clear(const Color& color) {
    if (m_renderTarget) {
        m_renderTarget->Clear(color.ToD2D());
    }
}

bool Renderer::PushLayerOpacity(float opacity, const Rect& bounds) {
    if (!m_renderTarget || opacity >= 0.999f) return false;
    D2D1_LAYER_PARAMETERS lp = D2D1::LayerParameters(
        bounds.ToD2D(),
        nullptr,                                  // 无蒙版（纯透明度层）
        D2D1_ANTIALIAS_MODE_PER_PRIMITIVE,
        D2D1::IdentityMatrix(),
        opacity,
        nullptr,
        D2D1_LAYER_OPTIONS_NONE);
    m_renderTarget->PushLayer(&lp, nullptr);
    return true;
}

void Renderer::PopLayerOpacity() {
    if (m_renderTarget) m_renderTarget->PopLayer();
}

// ---------------------------------------------------------------------------
// 变换栈原语：保存/恢复 + 平移/旋转/缩放（可任意嵌套组合）
// ---------------------------------------------------------------------------
void Renderer::PushTransform() {
    D2D1_MATRIX_3X2_F cur = D2D1::IdentityMatrix();
    if (m_renderTarget) m_renderTarget->GetTransform(&cur);
    m_transformStack.push_back(cur);
}

void Renderer::PopTransform() {
    if (m_transformStack.empty() || !m_renderTarget) return;
    m_renderTarget->SetTransform(m_transformStack.back());
    m_transformStack.pop_back();
}

void Renderer::Translate(float dx, float dy) {
    if (!m_renderTarget) return;
    m_renderTarget->SetTransform(D2D1::Matrix3x2F::Translation(dx, dy) *
                                 CurrentTransform());
}

void Renderer::Rotate(float cx, float cy, float radians) {
    if (!m_renderTarget) return;
    m_renderTarget->SetTransform(
        D2D1::Matrix3x2F::Rotation(radians * 180.0f / 3.14159265f,
                                   D2D1::Point2F(cx, cy)) *
        CurrentTransform());
}

void Renderer::Scale(float cx, float cy, float sx, float sy) {
    if (!m_renderTarget) return;
    m_renderTarget->SetTransform(
        D2D1::Matrix3x2F::Scale(D2D1::SizeF(sx, sy),
                                D2D1::Point2F(cx, cy)) *
        CurrentTransform());
}

D2D1_MATRIX_3X2_F Renderer::CurrentTransform() const {
    D2D1_MATRIX_3X2_F cur = D2D1::IdentityMatrix();
    if (m_renderTarget) m_renderTarget->GetTransform(&cur);
    return cur;
}

// ---------------------------------------------------------------------------
// 层模糊原语：捕获一层绘制 → 高斯效果 → 回贴到原目标。
// 机制：D2D 命令列表记录捕获区间内的绘制（不嵌套 BeginDraw），
//       pop 时 SetTarget 还原 → Close → GaussianBlur 效果 → DrawImage。
// 仅合成（DeviceContext）路径可用；HwndRT 回退路径 no-op。
// ---------------------------------------------------------------------------
bool Renderer::PushBlurLayer(float radius, const Rect &bounds) {
    if (!m_useDComp || !m_ctx || m_blurCmd) return false;
    if (FAILED(m_ctx->CreateCommandList(&m_blurCmd))) {
        m_blurCmd = nullptr;
        return false;
    }
    m_ctx->GetTarget(&m_blurOldTarget);
    m_ctx->SetTarget(m_blurCmd);
    m_blurRadius = radius;
    m_blurBounds = bounds;
    return true;
}

void Renderer::PopBlurLayer() {
    if (!m_blurCmd || !m_ctx) return;

    m_ctx->SetTarget(m_blurOldTarget);   // 回到原目标（继续正常绘制）
    m_blurCmd->Close();

    ID2D1Effect *fx = nullptr;
    if (SUCCEEDED(m_ctx->CreateEffect(CLSID_D2D1GaussianBlur, &fx))) {
        fx->SetInput(0, m_blurCmd);
        // 半径 → 标准差（视觉近似 σ ≈ r/3）
        fx->SetValue(D2D1_GAUSSIANBLUR_PROP_STANDARD_DEVIATION,
                     m_blurRadius / 3.0f > 0.5f ? m_blurRadius / 3.0f : 0.5f);
        bool clipped = false;
        if (m_blurBounds.Width() > 0 && m_blurBounds.Height() > 0) {
            PushClip(m_blurBounds);
            clipped = true;
        }
        m_ctx->DrawImage(fx);
        if (clipped) PopClip();
        fx->Release();
    }

    m_blurCmd->Release();
    m_blurCmd = nullptr;
    if (m_blurOldTarget) {
        m_blurOldTarget->Release();
        m_blurOldTarget = nullptr;
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

void Renderer::FlushBrushCache() {
    for (auto& cache : m_brushCache) {
        if (cache.brush) cache.brush->Release();
    }
    m_brushCache.clear();
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
            m_fontFamily.c_str(),
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

void Renderer::SetFontFamily(const std::wstring& family) {
    if (family.empty() || family == m_fontFamily) return;
    m_fontFamily = family;
    // 字体族变了，缓存的格式全部失效
    for (auto& cache : m_textFormatCache) {
        if (cache.format) cache.format->Release();
    }
    m_textFormatCache.clear();
}

void Renderer::DrawTextAligned(const std::wstring& text, const Rect& rect, const Color& color,
                               float fontSize, bool bold, int halign, int valign, bool wrap) {
    DrawTextAlignedEx(text, rect, color, fontSize, bold, halign, valign, wrap, false);
}

// ---------------------------------------------------------------------------
// 富文本（v3  4）：对齐/换行/省略。TextFormat 缓存键仍是 fontSize+bold
// ——对齐/换行/裁剪属性沿既有模式每次绘制前在共享实例上设置（单线程绘制，
// 帧内顺序覆盖，无脏读）。
// ---------------------------------------------------------------------------
void Renderer::DrawTextAlignedEx(const std::wstring& text, const Rect& rect, const Color& color,
                                 float fontSize, bool bold, int halign, int valign,
                                 bool wrap, bool ellipsis) {
    if (!m_renderTarget || text.empty()) return;

    IDWriteTextFormat* format = GetTextFormat(fontSize, bold);
    ID2D1SolidColorBrush* brush = GetBrush(color);
    if (!format || !brush) return;

    format->SetTextAlignment(halign == 0 ? DWRITE_TEXT_ALIGNMENT_LEADING
                          : (halign == 1 ? DWRITE_TEXT_ALIGNMENT_CENTER
                                         : DWRITE_TEXT_ALIGNMENT_TRAILING));
    format->SetParagraphAlignment(valign == 0 ? DWRITE_PARAGRAPH_ALIGNMENT_NEAR
                              : (valign == 1 ? DWRITE_PARAGRAPH_ALIGNMENT_CENTER
                                             : DWRITE_PARAGRAPH_ALIGNMENT_FAR));
    format->SetWordWrapping(wrap ? DWRITE_WORD_WRAPPING_WRAP : DWRITE_WORD_WRAPPING_NO_WRAP);

    // DWRITE_TRIMMING_DELIMITER_NONE 在部分 MinGW SDK 头中缺失，用数值 0
    DWRITE_TRIMMING trim = {DWRITE_TRIMMING_GRANULARITY_NONE, 0, 0};
    if (ellipsis && rect.Width() > 0.0f) {
        // 字符粒度省略号（超宽文本尾部 “…”）
        trim.granularity = DWRITE_TRIMMING_GRANULARITY_CHARACTER;
        format->SetTrimming(&trim, nullptr);
    } else {
        format->SetTrimming(&trim, nullptr);
    }

    m_renderTarget->DrawText(text.c_str(), static_cast<UINT32>(text.length()),
                             format, rect.ToD2D(), brush,
                             D2D1_DRAW_TEXT_OPTIONS_CLIP);
}

// 富测量（v3  4）：wrap=true 按给定宽度排版，返回换行后的宽高
Size Renderer::MeasureTextEx(const std::wstring& text, float fontSize, bool bold,
                             bool wrap, float maxWidth) {
    if (text.empty()) return Size(0, 0);
    IDWriteTextFormat* format = GetTextFormat(fontSize, bold);
    if (!format) return Size(0, 0);

    format->SetWordWrapping(wrap ? DWRITE_WORD_WRAPPING_WRAP : DWRITE_WORD_WRAPPING_NO_WRAP);

    IDWriteTextLayout* layout = nullptr;
    m_writeFactory->CreateTextLayout(
        text.c_str(), static_cast<UINT32>(text.length()),
        format,
        wrap ? (maxWidth > 0.0f ? maxWidth : 10000.0f) : 10000.0f,
        10000.0f, &layout);

    DWRITE_TEXT_METRICS metrics = {};
    if (layout) {
        layout->GetMetrics(&metrics);
        layout->Release();
    }
    return Size(metrics.width, metrics.height);
}

void Renderer::FillRoundedRectShadow(const RoundedRect& rr, float offsetY, float blur, float strength) {
    if (!m_renderTarget || strength <= 0.0f) return;

    // 用多层半透明圆角矩形叠出软阴影（不引入 D2D 效果 API，兼容性优先）
    const int layers = 6;
    for (int i = layers; i >= 1; --i) {
        float t = static_cast<float>(i) / static_cast<float>(layers);
        float spread = blur * t;
        float alpha = strength * (1.0f - t) * 0.5f;
        if (alpha <= 0.0f) continue;
        RoundedRect s = rr;
        s.rect.left   -= spread;
        s.rect.top    -= spread - offsetY;
        s.rect.right  += spread;
        s.rect.bottom += spread + offsetY;
        s.radiusX += spread * 0.5f;
        s.radiusY += spread * 0.5f;
        FillRoundedRect(s, Color(0.0f, 0.0f, 0.0f, alpha));
    }
}

Size Renderer::MeasureTextConstrained(const std::wstring& text, float fontSize, bool bold, float maxWidth) {
    if (text.empty()) return Size(0, 0);
    IDWriteTextFormat* format = GetTextFormat(fontSize, bold);
    if (!format) return Size(0, 0);

    IDWriteTextLayout* layout = nullptr;
    m_writeFactory->CreateTextLayout(text.c_str(), static_cast<UINT32>(text.length()),
                                     format, maxWidth > 0 ? maxWidth : 10000.0f, 10000.0f, &layout);
    DWRITE_TEXT_METRICS metrics = {};
    if (layout) {
        layout->GetMetrics(&metrics);
        layout->Release();
    }
    return Size(metrics.width, metrics.height);
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

// ---------------------------------------------------------------------------
// 图片解码：WIC（PNG/JPG/BMP/GIF 首帧…）→ D2D 位图（当前渲染目标上）
// ---------------------------------------------------------------------------
ID2D1Bitmap *Renderer::LoadImageFile(const std::wstring &path, UINT *outW, UINT *outH)
{
    if (!m_renderTarget || path.empty()) return nullptr;
    if (outW) *outW = 0;
    if (outH) *outH = 0;

    // WIC 工厂进程级共享
    if (!m_wicFactory)
    {
        IWICImagingFactory *f = nullptr;
        if (FAILED(CoCreateInstance(CLSID_WICImagingFactory, nullptr, CLSCTX_INPROC_SERVER,
                                    __uuidof(IWICImagingFactory), reinterpret_cast<void **>(&f))))
            return nullptr;
        m_wicFactory = f;
    }
    IWICImagingFactory *wic = static_cast<IWICImagingFactory *>(m_wicFactory);

    IWICBitmapDecoder *dec = nullptr;
    if (FAILED(wic->CreateDecoderFromFilename(path.c_str(), nullptr, GENERIC_READ,
                                              WICDecodeMetadataCacheOnDemand, &dec)))
        return nullptr;

    IWICBitmapFrameDecode *frame = nullptr;
    IWICFormatConverter *conv = nullptr;
    ID2D1Bitmap *bitmap = nullptr;
    if (SUCCEEDED(dec->GetFrame(0, &frame)) &&
        SUCCEEDED(wic->CreateFormatConverter(&conv)) &&
        SUCCEEDED(conv->Initialize(frame, GUID_WICPixelFormat32bppPBGRA,
                                   WICBitmapDitherTypeNone, nullptr, 0.0,
                                   WICBitmapPaletteTypeMedianCut)))
    {
        m_renderTarget->CreateBitmapFromWicBitmap(conv, &bitmap);
        if (bitmap)
        {
            D2D1_SIZE_F sz = bitmap->GetSize();
            if (outW) *outW = static_cast<UINT>(sz.width);
            if (outH) *outH = static_cast<UINT>(sz.height);
        }
    }
    if (conv) conv->Release();
    if (frame) frame->Release();
    dec->Release();
    return bitmap;
}

}
