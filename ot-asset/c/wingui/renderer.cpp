
#include "renderer.h"
#include <d2d1.h>
#include <d2d1_1.h>
#include <dwrite.h>
#include <d3d11.h>
#include <dxgi1_2.h>
#include <dcomp.h>
#include <wincodec.h>
#include <windows.h>
#include <cmath>

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
    , m_blurBounds(0, 0, 0, 0)
    , m_path(nullptr)
    , m_pathSink(nullptr)
    , m_pathFigureOpen(false)
    , m_pathCurX(0.0f)
    , m_pathCurY(0.0f)
    , m_lineCap(0)
    , m_lineJoin(0)
    , m_miterLimit(10.0f)
    , m_dashPhase(0.0f)
    , m_strokeStyle(nullptr)
    , m_strokeStyleDirty(false)
    , m_richLineH(0.0f)
    , m_richBaseline(0.0f)
    , m_richLeadSpacing(0.0f)
    , m_richTrailSpacing(0.0f)
    , m_richAdvanceSpacing(0.0f)
    , m_richMinAdvance(0.0f)
    , m_richSpacingSet(false)
    , m_surfaceOldImage(nullptr)
    , m_surfaceOldRT(nullptr) {
}

Renderer::~Renderer() {
    // 模糊层若悬空（异常路径），先还原目标再整体释放
    if (m_blurCmd && m_ctx) m_ctx->SetTarget(m_blurOldTarget);
    if (m_blurCmd) { m_blurCmd->Release(); m_blurCmd = nullptr; }
    if (m_blurOldTarget) { m_blurOldTarget->Release(); m_blurOldTarget = nullptr; }
    // v5 效果层若悬空（pop 未配对），逐层还原目标再释放
    while (!m_effectStack.empty()) {
        EffectLayer &L = m_effectStack.back();
        if (L.cmd && m_ctx) m_ctx->SetTarget(L.oldTarget);
        if (L.cmd) L.cmd->Release();
        if (L.oldTarget) L.oldTarget->Release();
        if (L.snap) L.snap->Release();
        m_effectStack.pop_back();
    }
    // 离屏面若悬空，先还原渲染目标（防御：pop 未配对就销毁）
    if (m_surfaceOldImage && m_ctx) m_ctx->SetTarget(m_surfaceOldImage);
    if (m_surfaceOldImage) { m_surfaceOldImage->Release(); m_surfaceOldImage = nullptr; }
    if (m_surfaceOldRT && m_renderTarget != m_surfaceOldRT) m_renderTarget = m_surfaceOldRT;
    m_surfaceOldRT = nullptr;
    // 路径构建器悬空收尾
    ClosePathSink();
    if (m_path) { m_path->Release(); m_path = nullptr; }
    if (m_strokeStyle) { m_strokeStyle->Release(); m_strokeStyle = nullptr; }
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
    // v5：离屏绘制若活跃（设备重建于 EndDraw，不可能在离屏中段 —— 防御性
    // 处理），先还原渲染目标再释放；路径/线型挂工厂不随设备失效。
    if (m_surfaceOldRT && m_renderTarget != m_surfaceOldRT) {
        m_renderTarget = m_surfaceOldRT;   // HwndRT 路径的离屏还原
    }
    m_surfaceOldRT = nullptr;
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

// ===========================================================================
// v5：绘制原语 —— 路径几何 / 线型 / 画刷 / 形状×画刷 / 富文本 /
//      合成效果层 / 离屏面 / 图片精控
// ===========================================================================

// ---------------------------------------------------------------------------
// 路径构建器：sink 打开期间逐段写入；fill/stroke/查询前自动收尾 sink。
// 坐标系与 vg_draw_* 完全一致（ABI 层负责画布偏移）。
// ---------------------------------------------------------------------------
void Renderer::ClosePathSink() {
    if (!m_pathSink) return;
    if (m_pathFigureOpen) {
        m_pathSink->EndFigure(D2D1_FIGURE_END_OPEN);   // 未显式 close 的子路径保持开放
        m_pathFigureOpen = false;
    }
    m_pathSink->Close();
    m_pathSink->Release();
    m_pathSink = nullptr;
}

void Renderer::EnsurePathFigure() {
    if (m_pathSink && !m_pathFigureOpen) {
        m_pathSink->BeginFigure(D2D1::Point2F(m_pathCurX, m_pathCurY), D2D1_FIGURE_BEGIN_FILLED);
        m_pathFigureOpen = true;
    }
}

bool Renderer::PathReset() {
    ClosePathSink();
    if (m_path) { m_path->Release(); m_path = nullptr; }
    if (!m_d2dFactory) return false;
    if (FAILED(m_d2dFactory->CreatePathGeometry(&m_path))) return false;
    if (FAILED(m_path->Open(&m_pathSink))) {
        m_path->Release();
        m_path = nullptr;
        return false;
    }
    m_pathFigureOpen = false;
    m_pathCurX = m_pathCurY = 0.0f;
    return true;
}

void Renderer::PathMoveTo(float x, float y) {
    if (!m_pathSink) return;
    if (m_pathFigureOpen) {
        m_pathSink->EndFigure(D2D1_FIGURE_END_OPEN);
        m_pathFigureOpen = false;
    }
    m_pathCurX = x;
    m_pathCurY = y;
}

void Renderer::PathLineTo(float x, float y) {
    if (!m_pathSink) return;
    EnsurePathFigure();
    m_pathSink->AddLine(D2D1::Point2F(x, y));
    m_pathCurX = x;
    m_pathCurY = y;
}

void Renderer::PathQuadTo(float cx, float cy, float x, float y) {
    if (!m_pathSink) return;
    EnsurePathFigure();
    // 二次 → 三次提升（控制点 1/3 处）
    float c1x = m_pathCurX + 2.0f * (cx - m_pathCurX) / 3.0f;
    float c1y = m_pathCurY + 2.0f * (cy - m_pathCurY) / 3.0f;
    float c2x = x + 2.0f * (cx - x) / 3.0f;
    float c2y = y + 2.0f * (cy - y) / 3.0f;
    m_pathSink->AddBezier(D2D1::BezierSegment(
        D2D1::Point2F(c1x, c1y), D2D1::Point2F(c2x, c2y), D2D1::Point2F(x, y)));
    m_pathCurX = x;
    m_pathCurY = y;
}

void Renderer::PathBezierTo(float c1x, float c1y, float c2x, float c2y, float x, float y) {
    if (!m_pathSink) return;
    EnsurePathFigure();
    m_pathSink->AddBezier(D2D1::BezierSegment(
        D2D1::Point2F(c1x, c1y), D2D1::Point2F(c2x, c2y), D2D1::Point2F(x, y)));
    m_pathCurX = x;
    m_pathCurY = y;
}

void Renderer::PathArcTo(float cx, float cy, float rx, float ry, float startRad, float sweepRad) {
    if (!m_pathSink) return;
    if (rx <= 0.0f || ry <= 0.0f || sweepRad == 0.0f) return;
    // 中心角式 → 端点参数化：拆成 |sweep| ≤ π 的段序列（D2D 单弧限制）
    const float kTwoPi = 6.28318530718f;
    const float kPi = 3.14159265359f;
    float s = startRad;
    float remaining = sweepRad;
    while (remaining > kTwoPi) {   // 多圈逐圈吃掉
        PathArcTo(cx, cy, rx, ry, s, kTwoPi);
        s += kTwoPi;
        remaining -= kTwoPi;
    }
    while (remaining < -kTwoPi) {
        PathArcTo(cx, cy, rx, ry, s, -kTwoPi);
        s -= kTwoPi;
        remaining += kTwoPi;
    }
    while (remaining != 0.0f) {
        float seg = remaining;
        if (seg > kPi) seg = kPi;
        else if (seg < -kPi) seg = -kPi;
        float ex = cx + rx * static_cast<float>(std::cos(s + seg));
        float ey = cy + ry * static_cast<float>(std::sin(s + seg));
        if (!m_pathFigureOpen) {
            m_pathCurX = cx + rx * static_cast<float>(std::cos(s));
            m_pathCurY = cy + ry * static_cast<float>(std::sin(s));
        }
        EnsurePathFigure();
        m_pathSink->AddArc(D2D1::ArcSegment(
            D2D1::Point2F(ex, ey),
            D2D1::SizeF(rx, ry),
            0.0f,
            seg > 0.0f ? D2D1_SWEEP_DIRECTION_CLOCKWISE : D2D1_SWEEP_DIRECTION_COUNTER_CLOCKWISE,
            static_cast<float>(std::fabs(seg)) > kPi ? D2D1_ARC_SIZE_LARGE : D2D1_ARC_SIZE_SMALL));
        m_pathCurX = ex;
        m_pathCurY = ey;
        s += seg;
        remaining -= seg;
    }
}

void Renderer::PathSvgArcTo(float rx, float ry, float rotationRad, bool largeArc, bool sweep, float x, float y) {
    if (!m_pathSink) return;
    if (rx <= 0.0f || ry <= 0.0f) {   // 退化直线（SVG 规范行为）
        PathLineTo(x, y);
        return;
    }
    // W3C SVG 1.1 附录 F：端点参数化 → 中心参数化
    float x1 = m_pathCurX, y1 = m_pathCurY;
    float phi = rotationRad;
    float cosp = static_cast<float>(std::cos(phi)), sinp = static_cast<float>(std::sin(phi));
    float dx2 = (x1 - x) / 2.0f, dy2 = (y1 - y) / 2.0f;
    float x1p = cosp * dx2 + sinp * dy2;
    float y1p = -sinp * dx2 + cosp * dy2;
    float rxp = rx, ryp = ry;
    float lambda = (x1p * x1p) / (rxp * rxp) + (y1p * y1p) / (ryp * ryp);
    if (lambda > 1.0f) {
        float s = static_cast<float>(std::sqrt(lambda));
        rxp *= s;
        ryp *= s;
    }
    float num = rxp * rxp * ryp * ryp - rxp * rxp * y1p * y1p - ryp * ryp * x1p * x1p;
    float den = rxp * rxp * y1p * y1p + ryp * ryp * x1p * x1p;
    if (den <= 0.0f) { PathLineTo(x, y); return; }
    float co = static_cast<float>(std::sqrt(num > 0.0f ? num / den : 0.0));
    if (largeArc == sweep) co = -co;
    float cxp = co * rxp * y1p / ryp;
    float cyp = -co * ryp * x1p / rxp;
    // 中心/角度仅用于大弧标志与方向的等价推导（AddArc 只需端点参数化）
    EnsurePathFigure();
    m_pathSink->AddArc(D2D1::ArcSegment(
        D2D1::Point2F(x, y),
        D2D1::SizeF(rxp, ryp),
        phi * 57.2957795131f,   // D2D 旋转角单位是度
        sweep ? D2D1_SWEEP_DIRECTION_CLOCKWISE : D2D1_SWEEP_DIRECTION_COUNTER_CLOCKWISE,
        largeArc ? D2D1_ARC_SIZE_LARGE : D2D1_ARC_SIZE_SMALL));
    m_pathCurX = x;
    m_pathCurY = y;
    (void)cxp; (void)cyp;
}

void Renderer::PathAddRect(float x, float y, float w, float h) {
    if (!m_pathSink) return;
    if (m_pathFigureOpen) {
        m_pathSink->EndFigure(D2D1_FIGURE_END_OPEN);
        m_pathFigureOpen = false;
    }
    m_pathSink->BeginFigure(D2D1::Point2F(x, y), D2D1_FIGURE_BEGIN_FILLED);
    m_pathSink->AddLine(D2D1::Point2F(x + w, y));
    m_pathSink->AddLine(D2D1::Point2F(x + w, y + h));
    m_pathSink->AddLine(D2D1::Point2F(x, y + h));
    m_pathSink->EndFigure(D2D1_FIGURE_END_CLOSED);
    m_pathCurX = x;
    m_pathCurY = y;
}

void Renderer::PathAddRoundRect(float x, float y, float w, float h, float rx, float ry) {
    if (!m_pathSink) return;
    if (rx < 0.0f) rx = 0.0f;
    if (ry < 0.0f) ry = 0.0f;
    if (rx * 2.0f > w) rx = w / 2.0f;
    if (ry * 2.0f > h) ry = h / 2.0f;
    if (rx <= 0.0f || ry <= 0.0f) { PathAddRect(x, y, w, h); return; }
    if (m_pathFigureOpen) {
        m_pathSink->EndFigure(D2D1_FIGURE_END_OPEN);
        m_pathFigureOpen = false;
    }
    // 从左上圆角起点顺时针绕行（AddArc 走小弧、顺时针）
    m_pathSink->BeginFigure(D2D1::Point2F(x, y + ry), D2D1_FIGURE_BEGIN_FILLED);
    m_pathSink->AddLine(D2D1::Point2F(x, y + h - ry));
    m_pathSink->AddArc(D2D1::ArcSegment(
        D2D1::Point2F(x + rx, y + h), D2D1::SizeF(rx, ry), 0.0f,
        D2D1_SWEEP_DIRECTION_CLOCKWISE, D2D1_ARC_SIZE_SMALL));
    m_pathSink->AddLine(D2D1::Point2F(x + w - rx, y + h));
    m_pathSink->AddArc(D2D1::ArcSegment(
        D2D1::Point2F(x + w, y + h - ry), D2D1::SizeF(rx, ry), 0.0f,
        D2D1_SWEEP_DIRECTION_CLOCKWISE, D2D1_ARC_SIZE_SMALL));
    m_pathSink->AddLine(D2D1::Point2F(x + w, y + ry));
    m_pathSink->AddArc(D2D1::ArcSegment(
        D2D1::Point2F(x + w - rx, y), D2D1::SizeF(rx, ry), 0.0f,
        D2D1_SWEEP_DIRECTION_CLOCKWISE, D2D1_ARC_SIZE_SMALL));
    m_pathSink->AddLine(D2D1::Point2F(x + rx, y));
    m_pathSink->AddArc(D2D1::ArcSegment(
        D2D1::Point2F(x, y + ry), D2D1::SizeF(rx, ry), 0.0f,
        D2D1_SWEEP_DIRECTION_CLOCKWISE, D2D1_ARC_SIZE_SMALL));
    m_pathSink->EndFigure(D2D1_FIGURE_END_CLOSED);
    m_pathCurX = x;
    m_pathCurY = y + ry;
}

void Renderer::PathAddEllipse(float cx, float cy, float rx, float ry) {
    if (!m_pathSink) return;
    if (rx <= 0.0f || ry <= 0.0f) return;
    // 老版 w32api 无 sink->AddEllipse —— 手绘四段 90° 弧（等价精确）
    if (m_pathFigureOpen) {
        m_pathSink->EndFigure(D2D1_FIGURE_END_OPEN);
        m_pathFigureOpen = false;
    }
    const float kPi2 = 1.57079632679f;   // π/2
    // 从 (cx+rx, cy) 起顺时针四段
    float px[5] = {cx + rx, cx, cx - rx, cx, cx + rx};
    float py[5] = {cy, cy + ry, cy, cy - ry, cy};
    m_pathSink->BeginFigure(D2D1::Point2F(px[0], py[0]), D2D1_FIGURE_BEGIN_FILLED);
    for (int i = 0; i < 4; ++i) {
        m_pathSink->AddArc(D2D1::ArcSegment(
            D2D1::Point2F(px[i + 1], py[i + 1]),
            D2D1::SizeF(rx, ry),
            0.0f,
            D2D1_SWEEP_DIRECTION_CLOCKWISE,
            D2D1_ARC_SIZE_SMALL));
        (void)kPi2;
    }
    m_pathSink->EndFigure(D2D1_FIGURE_END_CLOSED);
    m_pathCurX = cx + rx;
    m_pathCurY = cy;
}

void Renderer::PathClose() {
    if (!m_pathSink || !m_pathFigureOpen) return;
    m_pathSink->EndFigure(D2D1_FIGURE_END_CLOSED);
    m_pathFigureOpen = false;
}

void Renderer::FillPath(const Color &color) {
    FillPathBrush(GetBrush(color));
}

void Renderer::FillPathBrush(ID2D1Brush *brush) {
    ClosePathSink();   // fill 前收尾（未闭合子路径按开放图形填充）
    if (!m_renderTarget || !m_path || !brush) return;
    m_renderTarget->FillGeometry(m_path, brush);
}

void Renderer::StrokePath(const Color &color, float width) {
    StrokePathBrush(GetBrush(color), width);
}

void Renderer::StrokePathBrush(ID2D1Brush *brush, float width) {
    ClosePathSink();
    if (!m_renderTarget || !m_path || !brush) return;
    m_renderTarget->DrawGeometry(m_path, brush, width, GetStrokeStyle());
}

bool Renderer::PathBounds(Rect *out) {
    ClosePathSink();
    if (!m_path || !out) return false;
    D2D1_RECT_F b = {};
    if (FAILED(m_path->GetBounds(nullptr, &b))) return false;   // 参数序：transform 在前
    *out = Rect(b.left, b.top, b.right, b.bottom);
    return true;
}

bool Renderer::PathHitTest(float x, float y, bool *out) {
    ClosePathSink();
    if (!m_path || !out) return false;
    BOOL hit = FALSE;
    if (FAILED(m_path->FillContainsPoint(D2D1::Point2F(x, y), nullptr, 1e-3f, &hit))) return false;
    *out = (hit != FALSE);
    return true;
}

bool Renderer::PathStrokeHitTest(float x, float y, float width, bool *out) {
    ClosePathSink();
    if (!m_path || !out) return false;
    BOOL hit = FALSE;
    if (FAILED(m_path->StrokeContainsPoint(D2D1::Point2F(x, y), width, GetStrokeStyle(),
                                            nullptr, 1e-3f, &hit)))
        return false;
    *out = (hit != FALSE);
    return true;
}

// ---------------------------------------------------------------------------
// 线型状态：所有描边原语共享（默认值 = flat/miter/10/实线，与旧版逐位一致）
// ---------------------------------------------------------------------------
void Renderer::SetLineCap(int cap) {
    if (cap < 0 || cap > 2) cap = 0;
    if (cap != m_lineCap) { m_lineCap = cap; m_strokeStyleDirty = true; }
}

void Renderer::SetLineJoin(int join) {
    if (join < 0 || join > 3) join = 0;
    if (join != m_lineJoin) { m_lineJoin = join; m_strokeStyleDirty = true; }
}

void Renderer::SetMiterLimit(float limit) {
    if (limit < 1.0f) limit = 1.0f;
    if (limit != m_miterLimit) { m_miterLimit = limit; m_strokeStyleDirty = true; }
}

void Renderer::SetDash(const float *dashes, int count, float phase) {
    std::vector<float> d(dashes, dashes + (count > 0 ? count : 0));
    // D2D 要求正偶数组；奇数组补齐末段（SVG 规范行为）
    std::vector<float> clean;
    for (float v : d) if (v > 0.0f) clean.push_back(v);
    if (!clean.empty() && (clean.size() & 1)) clean.push_back(clean.back());
    bool changed = (clean != m_dash) || (phase != m_dashPhase);
    m_dash = clean;
    m_dashPhase = phase;
    if (changed) m_strokeStyleDirty = true;
}

ID2D1StrokeStyle *Renderer::GetStrokeStyle() {
    bool needCustom = (m_lineCap != 0) || (m_lineJoin != 0) ||
                      (m_miterLimit != 10.0f) || !m_dash.empty() || (m_dashPhase != 0.0f);
    if (!needCustom) {
        if (m_strokeStyle) { m_strokeStyle->Release(); m_strokeStyle = nullptr; }
        m_strokeStyleDirty = false;
        return nullptr;   // nullptr = D2D 内建默认（与旧版行为一致）
    }
    if (!m_strokeStyleDirty && m_strokeStyle) return m_strokeStyle;
    if (m_strokeStyle) { m_strokeStyle->Release(); m_strokeStyle = nullptr; }
    if (!m_d2dFactory) return nullptr;

    D2D1_STROKE_STYLE_PROPERTIES props = {};
    props.startCap = static_cast<D2D1_CAP_STYLE>(m_lineCap);
    props.endCap = static_cast<D2D1_CAP_STYLE>(m_lineCap);
    props.dashCap = static_cast<D2D1_CAP_STYLE>(m_lineCap);
    props.lineJoin = static_cast<D2D1_LINE_JOIN>(m_lineJoin);
    props.miterLimit = m_miterLimit;
    props.dashStyle = m_dash.empty() ? D2D1_DASH_STYLE_SOLID : D2D1_DASH_STYLE_CUSTOM;
    props.dashOffset = m_dashPhase;

    HRESULT hr = m_d2dFactory->CreateStrokeStyle(
        props,
        m_dash.empty() ? nullptr : m_dash.data(),
        static_cast<UINT32>(m_dash.size()),
        &m_strokeStyle);
    if (FAILED(hr)) m_strokeStyle = nullptr;
    m_strokeStyleDirty = false;
    return m_strokeStyle;
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
            m_renderTarget->DrawRectangle(rect.ToD2D(), brush, strokeWidth, GetStrokeStyle());
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
            m_renderTarget->DrawRoundedRectangle(rect.ToD2D(), brush, strokeWidth, GetStrokeStyle());
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
            m_renderTarget->DrawEllipse(ellipse.ToD2D(), brush, strokeWidth, GetStrokeStyle());
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
                brush, strokeWidth, GetStrokeStyle());
        }
    }
}

// ---------------------------------------------------------------------------
// 画刷创建（渐变停靠点由调用方装填；句柄/id 生命周期归 ABI 层，
// 设备重建后失效 —— 与图片位图同一先例）
// ---------------------------------------------------------------------------
ID2D1Brush *Renderer::CreateSolidBrushObj(const Color &c) {
    if (!m_renderTarget) return nullptr;
    ID2D1SolidColorBrush *b = nullptr;
    if (FAILED(m_renderTarget->CreateSolidColorBrush(c.ToD2D(), &b))) return nullptr;
    return b;
}

ID2D1Brush *Renderer::CreateLinearBrushObj(float x0, float y0, float x1, float y1,
                                           const float *positions, const Color *colors, int count) {
    if (!m_renderTarget || count < 2 || !positions || !colors) return nullptr;
    std::vector<D2D1_GRADIENT_STOP> stops;
    stops.reserve(count);
    for (int i = 0; i < count; ++i) {
        float p = positions[i];
        if (p < 0.0f) p = 0.0f;
        if (p > 1.0f) p = 1.0f;
        stops.push_back(D2D1_GRADIENT_STOP{p, colors[i].ToD2D()});
    }
    ID2D1GradientStopCollection *coll = nullptr;
    if (FAILED(m_renderTarget->CreateGradientStopCollection(stops.data(),
                                                            static_cast<UINT32>(stops.size()),
                                                            &coll)))
        return nullptr;
    ID2D1LinearGradientBrush *b = nullptr;
    HRESULT hr = m_renderTarget->CreateLinearGradientBrush(
        D2D1::LinearGradientBrushProperties(D2D1::Point2F(x0, y0), D2D1::Point2F(x1, y1)),
        coll, &b);
    coll->Release();
    return SUCCEEDED(hr) ? b : nullptr;
}

ID2D1Brush *Renderer::CreateRadialBrushObj(float cx, float cy, float fx, float fy,
                                           float rx, float ry,
                                           const float *positions, const Color *colors, int count) {
    if (!m_renderTarget || count < 2 || !positions || !colors) return nullptr;
    if (rx < 0.0f) rx = 0.0f;
    if (ry < 0.0f) ry = 0.0f;
    std::vector<D2D1_GRADIENT_STOP> stops;
    stops.reserve(count);
    for (int i = 0; i < count; ++i) {
        float p = positions[i];
        if (p < 0.0f) p = 0.0f;
        if (p > 1.0f) p = 1.0f;
        stops.push_back(D2D1_GRADIENT_STOP{p, colors[i].ToD2D()});
    }
    ID2D1GradientStopCollection *coll = nullptr;
    if (FAILED(m_renderTarget->CreateGradientStopCollection(stops.data(),
                                                            static_cast<UINT32>(stops.size()),
                                                            &coll)))
        return nullptr;
    // 焦点偏移 = (fx,fy) 相对中心（WinUI RadialGradientBrush GradientOrigin 同语义）
    ID2D1RadialGradientBrush *b = nullptr;
    HRESULT hr = m_renderTarget->CreateRadialGradientBrush(
        D2D1::RadialGradientBrushProperties(
            D2D1::Point2F(cx, cy),
            D2D1::Point2F(fx - cx, fy - cy),
            rx, ry),
        coll, &b);
    coll->Release();
    return SUCCEEDED(hr) ? b : nullptr;
}

ID2D1BitmapBrush *Renderer::CreateBitmapBrushObj(ID2D1Bitmap *bmp) {
    if (!m_renderTarget || !bmp) return nullptr;
    ID2D1BitmapBrush *b = nullptr;
    if (FAILED(m_renderTarget->CreateBitmapBrush(bmp, &b))) return nullptr;
    return b;
}

bool Renderer::BrushSetExtend(ID2D1Brush *brush, int modeX, int modeY) {
    if (!brush) return false;
    ID2D1BitmapBrush *bb = nullptr;
    if (FAILED(brush->QueryInterface(__uuidof(ID2D1BitmapBrush),
                                     reinterpret_cast<void **>(&bb))))
        return false;   // 渐变/纯色画刷无平铺语义（D2D 限制，如实返回）
    D2D1_EXTEND_MODE mx = modeX <= 0 ? D2D1_EXTEND_MODE_CLAMP
                        : (modeX == 1 ? D2D1_EXTEND_MODE_WRAP : D2D1_EXTEND_MODE_MIRROR);
    D2D1_EXTEND_MODE my = modeY <= 0 ? D2D1_EXTEND_MODE_CLAMP
                        : (modeY == 1 ? D2D1_EXTEND_MODE_WRAP : D2D1_EXTEND_MODE_MIRROR);
    bb->SetExtendModeX(mx);
    bb->SetExtendModeY(my);
    bb->Release();
    return true;
}

bool Renderer::BrushSetTransform(ID2D1Brush *brush,
                                 float m11, float m12, float m21, float m22,
                                 float dx, float dy) {
    if (!brush) return false;
    brush->SetTransform(D2D1::Matrix3x2F(m11, m12, m21, m22, dx, dy));
    return true;
}

// ---------------------------------------------------------------------------
// 形状 × 画刷（渐变/位图画刷直接作用于常规形状）
// ---------------------------------------------------------------------------
void Renderer::FillRectBrush(const Rect &rect, ID2D1Brush *brush) {
    if (m_renderTarget && brush) m_renderTarget->FillRectangle(rect.ToD2D(), brush);
}

void Renderer::DrawRectBrush(const Rect &rect, ID2D1Brush *brush, float width) {
    if (m_renderTarget && brush)
        m_renderTarget->DrawRectangle(rect.ToD2D(), brush, width, GetStrokeStyle());
}

void Renderer::FillRoundedRectBrush(const RoundedRect &rect, ID2D1Brush *brush) {
    if (m_renderTarget && brush) m_renderTarget->FillRoundedRectangle(rect.ToD2D(), brush);
}

void Renderer::DrawRoundedRectBrush(const RoundedRect &rect, ID2D1Brush *brush, float width) {
    if (m_renderTarget && brush)
        m_renderTarget->DrawRoundedRectangle(rect.ToD2D(), brush, width, GetStrokeStyle());
}

void Renderer::FillEllipseBrush(const Ellipse &ellipse, ID2D1Brush *brush) {
    if (m_renderTarget && brush) m_renderTarget->FillEllipse(ellipse.ToD2D(), brush);
}

void Renderer::DrawEllipseBrush(const Ellipse &ellipse, ID2D1Brush *brush, float width) {
    if (m_renderTarget && brush)
        m_renderTarget->DrawEllipse(ellipse.ToD2D(), brush, width, GetStrokeStyle());
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
    // v6：统一走全参数版（weight/italic/family 是 size+bold 的超集，行为不变）
    return GetTextFormatEx(fontSize, bold ? 700 : 400, false, m_fontFamily);
}

IDWriteTextFormat* Renderer::GetTextFormatEx(float size, int weight, bool italic,
                                              const std::wstring &family) {
    const std::wstring &fam = family.empty() ? m_fontFamily : family;
    for (auto& cache : m_textFormatCache) {
        if (cache.fontSize == size && cache.weight == weight &&
            cache.italic == italic && cache.family == fam) {
            return cache.format;
        }
    }
    IDWriteTextFormat* format = nullptr;
    if (m_writeFactory) {
        m_writeFactory->CreateTextFormat(
            fam.c_str(),
            nullptr,
            (DWRITE_FONT_WEIGHT)weight,
            italic ? DWRITE_FONT_STYLE_ITALIC : DWRITE_FONT_STYLE_NORMAL,
            DWRITE_FONT_STRETCH_NORMAL,
            size,
            L"en-us",
            &format);
        if (format) {
            TextFormatCache c;
            c.fontSize = size;
            c.bold = weight >= 600;
            c.weight = weight;
            c.italic = italic;
            c.family = fam;
            c.format = format;
            m_textFormatCache.push_back(c);
        }
    }
    return format;
}

void Renderer::DrawTextStyled(const std::wstring &text, const Rect &rect, const Color &color,
                              float size, int weight, bool italic, const std::wstring &family,
                              int halign, int valign, bool wrap, bool ellipsis) {
    if (!m_renderTarget || text.empty()) return;
    IDWriteTextFormat* format = GetTextFormatEx(size, weight, italic, family);
    ID2D1SolidColorBrush* brush = GetBrush(color);
    if (!format || !brush) return;

    format->SetTextAlignment(halign == 0 ? DWRITE_TEXT_ALIGNMENT_LEADING
                          : (halign == 1 ? DWRITE_TEXT_ALIGNMENT_CENTER
                                         : DWRITE_TEXT_ALIGNMENT_TRAILING));
    format->SetParagraphAlignment(valign == 0 ? DWRITE_PARAGRAPH_ALIGNMENT_NEAR
                              : (valign == 1 ? DWRITE_PARAGRAPH_ALIGNMENT_CENTER
                                             : DWRITE_PARAGRAPH_ALIGNMENT_FAR));
    format->SetWordWrapping(wrap ? DWRITE_WORD_WRAPPING_WRAP : DWRITE_WORD_WRAPPING_NO_WRAP);

    DWRITE_TRIMMING trim = {DWRITE_TRIMMING_GRANULARITY_NONE, 0, 0};
    if (ellipsis && rect.Width() > 0.0f) {
        trim.granularity = DWRITE_TRIMMING_GRANULARITY_CHARACTER;
    }
    format->SetTrimming(&trim, nullptr);

    m_renderTarget->DrawText(text.c_str(), static_cast<UINT32>(text.length()),
                              format, rect.ToD2D(), brush);
}

// ===========================================================================
// v6：句柄几何（path store 的渲染对应物；几何挂 factory，跨设备安全）
// ===========================================================================

ID2D1PathGeometry* Renderer::PathTakeRef() {
    ClosePathSink();
    if (!m_path) return nullptr;
    m_path->AddRef();   // 表持有一个引用；构建器 PathReset 只释放自己的
    return m_path;
}

void Renderer::FillGeometryBrush(ID2D1Geometry* geo, ID2D1Brush* brush) {
    if (!m_renderTarget || !geo || !brush) return;
    m_renderTarget->FillGeometry(geo, brush);
}

void Renderer::FillGeometry(ID2D1Geometry* geo, const Color& color) {
    FillGeometryBrush(geo, GetBrush(color));
}

void Renderer::StrokeGeometryBrush(ID2D1Geometry* geo, ID2D1Brush* brush, float width) {
    if (!m_renderTarget || !geo || !brush) return;
    m_renderTarget->DrawGeometry(geo, brush, width, GetStrokeStyle());
}

void Renderer::StrokeGeometry(ID2D1Geometry* geo, const Color& color, float width) {
    StrokeGeometryBrush(geo, GetBrush(color), width);
}

bool Renderer::GeometryHitTest(ID2D1Geometry* geo, float x, float y, bool* out) {
    if (!geo || !out) return false;
    BOOL hit = FALSE;
    if (FAILED(geo->FillContainsPoint(D2D1::Point2F(x, y), nullptr, 1e-3f, &hit))) return false;
    *out = (hit != FALSE);
    return true;
}

bool Renderer::PushClipGeometry(ID2D1Geometry* geo) {
    if (!m_renderTarget || !geo) return false;
    D2D1_RECT_F b = {};
    geo->GetBounds(nullptr, &b);
    D2D1_LAYER_PARAMETERS lp = D2D1::LayerParameters(
        D2D1::RectF(b.left, b.top, b.right, b.bottom),
        geo,
        D2D1_ANTIALIAS_MODE_PER_PRIMITIVE,
        D2D1::IdentityMatrix(),
        1.0f,
        nullptr,
        D2D1_LAYER_OPTIONS_NONE);
    m_renderTarget->PushLayer(&lp, nullptr);
    m_clipStack.push_back(1);
    return true;
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

// ===========================================================================
// v5：富文本（inline 混排）
//   机制：span → 全文 TextLayout（逐区间覆盖字号/字重/斜体/下划线/删除线，
//   颜色经 SetDrawingEffect 携带 RichEffect）→ IDWriteTextLayout::Draw +
//   自定义 ID2D1SimplifiedTextRenderer 逐 run 绘制（多色/渐变画刷文字）。
// ===========================================================================

namespace {

// SetDrawingEffect 携带的绘制效果（颜色或画刷；生命周期归 RichDraw 栈帧）
struct RichEffect : public IUnknown {
    ULONG refs = 1;
    gui::Color color;
    ID2D1Brush *brush = nullptr;   // 非空优先（渐变/位图文字）

    ULONG STDMETHODCALLTYPE AddRef() override { return ++refs; }
    ULONG STDMETHODCALLTYPE Release() override {
        ULONG r = --refs;
        if (r == 0) delete this;
        return r;
    }
    HRESULT STDMETHODCALLTYPE QueryInterface(REFIID riid, void **ppv) override {
        if (!ppv) return E_POINTER;
        if (riid == __uuidof(IUnknown) || riid == __uuidof(IDWriteTextRenderer)) {
            *ppv = static_cast<IUnknown *>(this);
            AddRef();
            return S_OK;
        }
        *ppv = nullptr;
        return E_NOINTERFACE;
    }
};

// 自定义文本渲染器：每 run 从 RichEffect 取笔刷绘制字形/下划线/删除线。
// 直接实现 IDWriteTextRenderer（dwrite.h 完整定义；D2D 的 Simplified 版
// 在部分老版 w32api 头中缺失，而两者虚表签名一致——baselineOriginX/Y 版）。
class RichTextRenderer : public IDWriteTextRenderer {
public:
    explicit RichTextRenderer(ID2D1RenderTarget *target) : m_target(target), m_refs(1) {}

    ULONG STDMETHODCALLTYPE AddRef() override { return ++m_refs; }
    ULONG STDMETHODCALLTYPE Release() override {
        ULONG r = --m_refs;
        if (r == 0) delete this;
        return r;
    }
    HRESULT STDMETHODCALLTYPE QueryInterface(REFIID riid, void **ppv) override {
        if (!ppv) return E_POINTER;
        if (riid == __uuidof(IUnknown) || riid == __uuidof(IDWriteTextRenderer)) {
            *ppv = static_cast<IUnknown *>(static_cast<IDWriteTextRenderer *>(this));
            AddRef();
            return S_OK;
        }
        *ppv = nullptr;
        return E_NOINTERFACE;
    }

    // ---- IDWritePixelSnapping ----
    HRESULT STDMETHODCALLTYPE IsPixelSnappingDisabled(void *, BOOL *disabled) override {
        if (!disabled) return E_POINTER;
        *disabled = FALSE;   // 跟随 D2D 抗锯齿管线（不强制像素对齐）
        return S_OK;
    }
    HRESULT STDMETHODCALLTYPE GetCurrentTransform(void *, DWRITE_MATRIX *m) override {
        if (!m) return E_POINTER;
        D2D1_MATRIX_3X2_F t = D2D1::IdentityMatrix();
        if (m_target) m_target->GetTransform(&t);
        m->m11 = t.m11; m->m12 = t.m12;
        m->m21 = t.m21; m->m22 = t.m22;
        m->dx = t.dx;   m->dy = t.dy;
        return S_OK;
    }
    HRESULT STDMETHODCALLTYPE GetPixelsPerDip(void *, FLOAT *ppd) override {
        if (!ppd) return E_POINTER;
        *ppd = 1.0f;
        return S_OK;
    }

    // ---- IDWriteTextRenderer ----
    HRESULT STDMETHODCALLTYPE DrawGlyphRun(
        void *, FLOAT baselineOriginX, FLOAT baselineOriginY,
        DWRITE_MEASURING_MODE measuringMode,
        const DWRITE_GLYPH_RUN *glyphRun,
        const DWRITE_GLYPH_RUN_DESCRIPTION *,
        IUnknown *clientDrawingEffect) override {
        ID2D1Brush *br = BrushOf(clientDrawingEffect);
        if (!br || !glyphRun) return S_OK;
        m_target->DrawGlyphRun(D2D1::Point2F(baselineOriginX, baselineOriginY),
                               glyphRun, br, measuringMode);
        return S_OK;
    }
    HRESULT STDMETHODCALLTYPE DrawUnderline(
        void *, FLOAT x, FLOAT y,
        const DWRITE_UNDERLINE *underline,
        IUnknown *clientDrawingEffect) override {
        ID2D1Brush *br = BrushOf(clientDrawingEffect);
        if (!br || !underline) return S_OK;
        m_target->FillRectangle(
            D2D1::RectF(x, y + underline->offset,
                        x + underline->width, y + underline->offset + underline->thickness),
            br);
        return S_OK;
    }
    HRESULT STDMETHODCALLTYPE DrawStrikethrough(
        void *, FLOAT x, FLOAT y,
        const DWRITE_STRIKETHROUGH *strikethrough,
        IUnknown *clientDrawingEffect) override {
        ID2D1Brush *br = BrushOf(clientDrawingEffect);
        if (!br || !strikethrough) return S_OK;
        m_target->FillRectangle(
            D2D1::RectF(x, y + strikethrough->offset,
                        x + strikethrough->width, y + strikethrough->offset + strikethrough->thickness),
            br);
        return S_OK;
    }
    HRESULT STDMETHODCALLTYPE DrawInlineObject(
        void *, FLOAT, FLOAT, IDWriteInlineObject *, BOOL, BOOL, IUnknown *) override {
        return S_OK;   // 未启用 inline 对象（不触发即无副作用）
    }

private:
    ID2D1Brush *BrushOf(IUnknown *effect) {
        if (!m_target) return nullptr;
        if (effect) {
            RichEffect *re = static_cast<RichEffect *>(effect);   // 单继承无偏移
            if (re->brush) return re->brush;
            m_colorScratch = re->color;
        } else {
            m_colorScratch = gui::Color(0, 0, 0, 1);
        }
        if (!m_scratchBrush) {
            if (FAILED(m_target->CreateSolidColorBrush(m_colorScratch.ToD2D(), &m_scratchBrush)))
                return nullptr;
        }
        m_scratchBrush->SetColor(m_colorScratch.ToD2D());
        return m_scratchBrush;
    }

    ID2D1RenderTarget *m_target;
    ID2D1SolidColorBrush *m_scratchBrush = nullptr;
    gui::Color m_colorScratch;
    ULONG m_refs;
};

}  // namespace

void Renderer::RichReset() {
    m_richSpans.clear();
}

void Renderer::RichAdd(const RichSpan &span) {
    m_richSpans.push_back(span);
}

void Renderer::RichSetLineHeight(float lineH, float baseline) {
    m_richLineH = lineH;
    m_richBaseline = baseline;
}

void Renderer::RichSetSpacing(float leading, float trailing, float advance, float minAdvance) {
    m_richLeadSpacing = leading;
    m_richTrailSpacing = trailing;
    m_richAdvanceSpacing = advance;
    m_richMinAdvance = minAdvance;
    m_richSpacingSet = true;
}

bool Renderer::BuildRichLayout(const Rect &rect, int halign, int valign, bool wrap,
                               IDWriteTextLayout **outLayout,
                               std::vector<std::pair<UINT32, UINT32>> *outRanges) {
    *outLayout = nullptr;
    if (outRanges) outRanges->clear();
    if (!m_writeFactory || m_richSpans.empty()) return false;

    // 全文拼接 + span 区间表
    std::wstring full;
    std::vector<std::pair<UINT32, UINT32>> ranges;
    ranges.reserve(m_richSpans.size());
    for (auto &s : m_richSpans) {
        ranges.emplace_back(static_cast<UINT32>(full.size()),
                            static_cast<UINT32>(s.text.size()));
        full += s.text;
    }
    if (full.empty()) return false;

    // 基础格式取首个 span（其余逐区间覆盖）
    IDWriteTextFormat *fmt = GetTextFormat(m_richSpans[0].size,
                                           m_richSpans[0].weight >= 700);
    if (!fmt) return false;
    fmt->SetTextAlignment(halign == 0 ? DWRITE_TEXT_ALIGNMENT_LEADING
                          : (halign == 1 ? DWRITE_TEXT_ALIGNMENT_CENTER
                                         : DWRITE_TEXT_ALIGNMENT_TRAILING));
    fmt->SetParagraphAlignment(valign == 0 ? DWRITE_PARAGRAPH_ALIGNMENT_NEAR
                              : (valign == 1 ? DWRITE_PARAGRAPH_ALIGNMENT_CENTER
                                             : DWRITE_PARAGRAPH_ALIGNMENT_FAR));
    fmt->SetWordWrapping(wrap ? DWRITE_WORD_WRAPPING_WRAP : DWRITE_WORD_WRAPPING_NO_WRAP);

    float layoutW = (wrap && rect.Width() > 0.0f) ? rect.Width() : 10000.0f;
    IDWriteTextLayout *layout = nullptr;
    if (FAILED(m_writeFactory->CreateTextLayout(full.c_str(),
                                                static_cast<UINT32>(full.size()),
                                                fmt, layoutW, 10000.0f, &layout)))
        return false;

    for (size_t i = 0; i < ranges.size(); ++i) {
        if (ranges[i].second == 0) continue;
        const RichSpan &s = m_richSpans[i];
        DWRITE_TEXT_RANGE tr{ranges[i].first, ranges[i].second};
        layout->SetFontSize(s.size, tr);
        layout->SetFontWeight(static_cast<DWRITE_FONT_WEIGHT>(s.weight), tr);
        layout->SetFontStyle(s.italic ? DWRITE_FONT_STYLE_ITALIC : DWRITE_FONT_STYLE_NORMAL, tr);
        layout->SetUnderline(s.underline ? TRUE : FALSE, tr);
        layout->SetStrikethrough(s.strikethrough ? TRUE : FALSE, tr);
    }

    if (m_richLineH > 0.0f) {
        layout->SetLineSpacing(DWRITE_LINE_SPACING_METHOD_UNIFORM, m_richLineH,
                               m_richBaseline > 0.0f ? m_richBaseline : m_richLineH * 0.8f);
    }
    if (m_richSpacingSet) {
// 字距（IDWriteTextLayout1，DWrite2/Win8.1+；老版 w32api 头无此接口定义，
// 宏探测编译期跳过 —— 运行时如实返回不支持（RichSpacing 返回 0）
#if defined(__IDWriteTextLayout1_INTERFACE_DEFINED__)
        IDWriteTextLayout1 *l1 = nullptr;
        if (SUCCEEDED(layout->QueryInterface(__uuidof(IDWriteTextLayout1),
                                             reinterpret_cast<void **>(&l1)))) {
            DWRITE_TEXT_RANGE all{0, static_cast<UINT32>(full.size())};
            l1->SetCharacterSpacing(m_richLeadSpacing, m_richTrailSpacing,
                                    m_richAdvanceSpacing, m_richMinAdvance, all);
            l1->Release();
        }
#endif
    }

    *outLayout = layout;
    if (outRanges) *outRanges = ranges;
    return true;
}

bool Renderer::RichDraw(const Rect &rect, int halign, int valign, bool wrap) {
    if (!m_renderTarget) return false;
    IDWriteTextLayout *layout = nullptr;
    std::vector<std::pair<UINT32, UINT32>> ranges;
    if (!BuildRichLayout(rect, halign, valign, wrap, &layout, &ranges)) return false;

    // 每 span 挂绘制效果（颜色/画刷），随绘制栈帧释放
    std::vector<RichEffect *> effects(m_richSpans.size(), nullptr);
    for (size_t i = 0; i < ranges.size() && i < m_richSpans.size(); ++i) {
        if (ranges[i].second == 0) continue;
        RichEffect *e = new RichEffect();
        e->color = m_richSpans[i].color;
        e->brush = m_richSpans[i].brush;
        layout->SetDrawingEffect(e, DWRITE_TEXT_RANGE{ranges[i].first, ranges[i].second});
        effects[i] = e;
    }

    RichTextRenderer renderer(m_renderTarget);
    // 裁剪到布局矩形（wrap 排版的忠实呈现）
    bool clipped = false;
    if (rect.Width() > 0.0f && rect.Height() > 0.0f) {
        PushClip(rect);
        clipped = true;
    }
    layout->Draw(nullptr, &renderer, rect.left, rect.top);
    if (clipped) PopClip();

    layout->Release();
    for (RichEffect *e : effects)
        if (e) e->Release();
    return true;
}

bool Renderer::RichMeasure(bool wrap, float maxWidth, Size *out) {
    if (!out) return false;
    *out = Size(0, 0);
    Rect r(0, 0, maxWidth > 0.0f ? maxWidth : 0.0f, 0);
    IDWriteTextLayout *layout = nullptr;
    if (!BuildRichLayout(r, 0, 0, wrap, &layout, nullptr)) return false;
    DWRITE_TEXT_METRICS metrics = {};
    layout->GetMetrics(&metrics);
    layout->Release();
    *out = Size(metrics.width, metrics.height);
    return true;
}

// ===========================================================================
// v5：合成效果层（混合 / 真阴影 / 蒙版 / 几何裁剪）
//   * blend：dest 快照 + D2D1Composite 效果（16 种模式）；
//   * shadow：D2D1Shadow 效果（GPU 高斯）平移绘制后叠内容本体 —— WinUI
//     DropShadow 语义，任意内容（路径/文字/位图）真软阴影；
//   * mask/clip：PushLayer 原生（画刷 alpha 蒙版 / 几何蒙版），全路径可用。
//   回贴时强制 identity 变换（捕获内容已含各自变换）。
// ===========================================================================
bool Renderer::PushBlendLayer(int compositeMode, const Rect &bounds) {
    if (!m_useDComp || !m_ctx) return false;

    // dest 快照（composite 的 destination 输入）
    ID2D1Bitmap1 *snap = nullptr;
    D2D1_BITMAP_PROPERTIES1 bp = D2D1::BitmapProperties1(
        D2D1_BITMAP_OPTIONS_NONE,
        D2D1::PixelFormat(DXGI_FORMAT_B8G8R8A8_UNORM, D2D1_ALPHA_MODE_PREMULTIPLIED));
    D2D1_SIZE_U sz = m_ctx->GetPixelSize();
    if (sz.width == 0 || sz.height == 0) return false;
    if (FAILED(m_ctx->CreateBitmap(sz, nullptr, 0, &bp, &snap))) return false;
    if (FAILED(snap->CopyFromBitmap(nullptr, m_targetBitmap, nullptr))) {
        snap->Release();
        return false;
    }

    // 捕获新绘制
    ID2D1CommandList *cmd = nullptr;
    if (FAILED(m_ctx->CreateCommandList(&cmd))) {
        snap->Release();
        return false;
    }
    ID2D1Image *old = nullptr;
    m_ctx->GetTarget(&old);
    m_ctx->SetTarget(cmd);

    EffectLayer L{};
    L.kind = 1;
    L.cmd = cmd;
    L.oldTarget = old;
    L.snap = snap;
    L.blendMode = compositeMode;
    L.bounds = bounds;
    m_effectStack.push_back(L);
    return true;
}

void Renderer::PopBlendLayer() {
    if (m_effectStack.empty() || m_effectStack.back().kind != 1) return;
    EffectLayer L = m_effectStack.back();
    m_effectStack.pop_back();
    if (!L.cmd || !m_ctx) return;

    m_ctx->SetTarget(L.oldTarget);
    L.cmd->Close();

    ID2D1Effect *fx = nullptr;
    if (SUCCEEDED(m_ctx->CreateEffect(CLSID_D2D1Composite, &fx))) {
        fx->SetInput(0, L.snap);   // destination
        fx->SetInput(1, L.cmd);    // source
        int mode = L.blendMode;
        if (mode < 0 || mode > 12) mode = 0;
        fx->SetValue(D2D1_COMPOSITE_PROP_MODE, static_cast<D2D1_COMPOSITE_MODE>(mode));

        PushTransform();
        m_ctx->SetTransform(D2D1::IdentityMatrix());   // 捕获内容已含各自变换
        bool clipped = false;
        if (L.bounds.Width() > 0.0f && L.bounds.Height() > 0.0f) {
            PushClip(L.bounds);
            clipped = true;
        }
        m_ctx->DrawImage(fx);
        if (clipped) PopClip();
        PopTransform();
        fx->Release();
    }

    L.cmd->Release();
    if (L.oldTarget) L.oldTarget->Release();
    if (L.snap) L.snap->Release();
}

bool Renderer::PushShadowLayer(const Color &color, float blur, float dx, float dy,
                               const Rect &bounds) {
    if (!m_useDComp || !m_ctx) return false;
    ID2D1CommandList *cmd = nullptr;
    if (FAILED(m_ctx->CreateCommandList(&cmd))) return false;
    ID2D1Image *old = nullptr;
    m_ctx->GetTarget(&old);
    m_ctx->SetTarget(cmd);

    EffectLayer L{};
    L.kind = 2;
    L.cmd = cmd;
    L.oldTarget = old;
    L.shadowColor = color;
    L.shadowBlur = blur;
    L.shadowDx = dx;
    L.shadowDy = dy;
    L.bounds = bounds;
    m_effectStack.push_back(L);
    return true;
}

void Renderer::PopShadowLayer() {
    if (m_effectStack.empty() || m_effectStack.back().kind != 2) return;
    EffectLayer L = m_effectStack.back();
    m_effectStack.pop_back();
    if (!L.cmd || !m_ctx) return;

    m_ctx->SetTarget(L.oldTarget);
    L.cmd->Close();

    ID2D1Effect *fx = nullptr;
    if (SUCCEEDED(m_ctx->CreateEffect(CLSID_D2D1Shadow, &fx))) {
        fx->SetInput(0, L.cmd);
        fx->SetValue(D2D1_SHADOW_PROP_COLOR, L.shadowColor.ToD2D());
        float sd = L.shadowBlur / 3.0f;
        fx->SetValue(D2D1_SHADOW_PROP_BLUR_STANDARD_DEVIATION, sd > 0.5f ? sd : 0.5f);

        PushTransform();
        // 1) 阴影：identity + 平移（阴影本体）
        m_ctx->SetTransform(D2D1::Matrix3x2F::Translation(L.shadowDx, L.shadowDy));
        m_ctx->DrawImage(fx);
        // 2) 内容本体：identity 原位
        m_ctx->SetTransform(D2D1::IdentityMatrix());
        bool clipped = false;
        if (L.bounds.Width() > 0.0f && L.bounds.Height() > 0.0f) {
            PushClip(L.bounds);
            clipped = true;
        }
        m_ctx->DrawImage(L.cmd);
        if (clipped) PopClip();
        PopTransform();
        fx->Release();
    }

    L.cmd->Release();
    if (L.oldTarget) L.oldTarget->Release();
}

bool Renderer::PushMaskLayerBrush(ID2D1Brush *brush, const Rect &bounds, float opacity) {
    if (!m_renderTarget || !brush) return false;
    if (opacity < 0.0f) opacity = 0.0f;
    if (opacity > 1.0f) opacity = 1.0f;
    D2D1_LAYER_PARAMETERS lp = D2D1::LayerParameters(
        bounds.ToD2D(),
        nullptr,                                // 几何蒙版（无）
        D2D1_ANTIALIAS_MODE_PER_PRIMITIVE,
        D2D1::IdentityMatrix(),
        opacity,
        brush,                                  // opacity mask（画刷 alpha）
        D2D1_LAYER_OPTIONS_NONE);
    m_renderTarget->PushLayer(&lp, nullptr);
    m_clipStack.push_back(1);
    return true;
}

void Renderer::PopMaskLayer() {
    PopClip();   // 统一经裁剪类型栈（layer 类）
}

bool Renderer::PushClipPath() {
    ClosePathSink();
    if (!m_renderTarget || !m_path) return false;
    D2D1_RECT_F b = {};
    m_path->GetBounds(nullptr, &b);
    D2D1_LAYER_PARAMETERS lp = D2D1::LayerParameters(
        D2D1::RectF(b.left, b.top, b.right, b.bottom),
        m_path,                                // 几何蒙版（含 AA 边）
        D2D1_ANTIALIAS_MODE_PER_PRIMITIVE,
        D2D1::IdentityMatrix(),
        1.0f,
        nullptr,
        D2D1_LAYER_OPTIONS_NONE);
    m_renderTarget->PushLayer(&lp, nullptr);
    m_clipStack.push_back(1);
    return true;
}

bool Renderer::PushClipRoundRect(const Rect &rect, float rx, float ry) {
    if (!m_renderTarget || !m_d2dFactory) return false;
    if (rx < 0.0f) rx = 0.0f;
    if (ry < 0.0f) ry = 0.0f;
    if (rx * 2.0f > rect.Width()) rx = rect.Width() / 2.0f;
    if (ry * 2.0f > rect.Height()) ry = rect.Height() / 2.0f;
    if (rx <= 0.0f || ry <= 0.0f) {
        PushClip(rect);   // 退化为轴对齐裁剪
        return true;
    }
    // 圆角矩形几何（独立于构建器路径，手绘四弧——sink 无 AddRoundedRect）
    ID2D1PathGeometry *g = nullptr;
    if (FAILED(m_d2dFactory->CreatePathGeometry(&g))) return false;
    ID2D1GeometrySink *sink = nullptr;
    if (FAILED(g->Open(&sink))) {
        g->Release();
        return false;
    }
    float x = rect.left, y = rect.top, w = rect.Width(), h = rect.Height();
    sink->BeginFigure(D2D1::Point2F(x, y + ry), D2D1_FIGURE_BEGIN_FILLED);
    sink->AddLine(D2D1::Point2F(x, y + h - ry));
    sink->AddArc(D2D1::ArcSegment(
        D2D1::Point2F(x + rx, y + h), D2D1::SizeF(rx, ry), 0.0f,
        D2D1_SWEEP_DIRECTION_CLOCKWISE, D2D1_ARC_SIZE_SMALL));
    sink->AddLine(D2D1::Point2F(x + w - rx, y + h));
    sink->AddArc(D2D1::ArcSegment(
        D2D1::Point2F(x + w, y + h - ry), D2D1::SizeF(rx, ry), 0.0f,
        D2D1_SWEEP_DIRECTION_CLOCKWISE, D2D1_ARC_SIZE_SMALL));
    sink->AddLine(D2D1::Point2F(x + w, y + ry));
    sink->AddArc(D2D1::ArcSegment(
        D2D1::Point2F(x + w - rx, y), D2D1::SizeF(rx, ry), 0.0f,
        D2D1_SWEEP_DIRECTION_CLOCKWISE, D2D1_ARC_SIZE_SMALL));
    sink->AddLine(D2D1::Point2F(x + rx, y));
    sink->AddArc(D2D1::ArcSegment(
        D2D1::Point2F(x, y + ry), D2D1::SizeF(rx, ry), 0.0f,
        D2D1_SWEEP_DIRECTION_CLOCKWISE, D2D1_ARC_SIZE_SMALL));
    sink->EndFigure(D2D1_FIGURE_END_CLOSED);
    sink->Close();
    sink->Release();
    D2D1_LAYER_PARAMETERS lp = D2D1::LayerParameters(
        rect.ToD2D(), g, D2D1_ANTIALIAS_MODE_PER_PRIMITIVE,
        D2D1::IdentityMatrix(), 1.0f, nullptr, D2D1_LAYER_OPTIONS_NONE);
    m_renderTarget->PushLayer(&lp, nullptr);
    g->Release();
    m_clipStack.push_back(1);
    return true;
}

// ===========================================================================
// v5：离屏面（位图缓存层）+ 图片精控
// ===========================================================================
ID2D1Bitmap *Renderer::CreateSurfaceBitmap(UINT w, UINT h, ID2D1BitmapRenderTarget **outRT) {
    if (outRT) *outRT = nullptr;
    if (w == 0 || h == 0) return nullptr;

    if (m_useDComp && m_ctx) {
        // 仅 TARGET（可绘制、可贴回作 DrawBitmap 输入）；
        // 像素读回经临时 CPU_READ 位图拷贝（CPU_READ 与 TARGET 不能组合）
        D2D1_BITMAP_PROPERTIES1 bp = D2D1::BitmapProperties1(
            D2D1_BITMAP_OPTIONS_TARGET,
            D2D1::PixelFormat(DXGI_FORMAT_B8G8R8A8_UNORM, D2D1_ALPHA_MODE_PREMULTIPLIED));
        ID2D1Bitmap1 *bmp = nullptr;
        if (FAILED(m_ctx->CreateBitmap(D2D1::SizeU(w, h), nullptr, 0, &bp, &bmp)))
            return nullptr;
        return bmp;
    }

    // HwndRT 回退：兼容渲染目标（位图 + RT 成对，RT 归 ABI 层持有）
    if (!m_hwndRT) return nullptr;
    ID2D1BitmapRenderTarget *brt = nullptr;
    if (FAILED(m_hwndRT->CreateCompatibleRenderTarget(
            D2D1::SizeF(static_cast<FLOAT>(w), static_cast<FLOAT>(h)), &brt)))
        return nullptr;
    ID2D1Bitmap *bmp = nullptr;
    brt->GetBitmap(&bmp);
    if (!bmp) {
        brt->Release();
        return nullptr;
    }
    if (outRT) {
        *outRT = brt;
    } else {
        brt->Release();   // 调用方不持 RT（位图贴回仍可用，但不能再重绘）
    }
    return bmp;
}

bool Renderer::BeginSurface(ID2D1Bitmap *bmp, ID2D1BitmapRenderTarget *rt) {
    if (!bmp) return false;
    if (m_useDComp && m_ctx) {
        if (m_surfaceOldImage) return false;   // 不嵌套（与模糊层同纪律）
        ID2D1Image *old = nullptr;
        m_ctx->GetTarget(&old);
        m_ctx->SetTarget(bmp);
        m_surfaceOldImage = old;
        m_ctx->Clear(D2D1::ColorF(0, 0, 0, 0));   // 清为透明（缓存层语义）
        return true;
    }
    // HwndRT 路径：绘制路由切到兼容 RT（自带 Begin/EndDraw 纪律）
    if (!rt || !m_renderTarget) return false;
    if (m_surfaceOldRT) return false;
    m_surfaceOldRT = m_renderTarget;
    m_renderTarget = rt;
    rt->BeginDraw();
    rt->Clear(D2D1::ColorF(0, 0, 0, 0));
    return true;
}

void Renderer::EndSurface() {
    if (m_useDComp && m_ctx) {
        if (!m_surfaceOldImage) return;
        m_ctx->SetTarget(m_surfaceOldImage);
        m_surfaceOldImage->Release();
        m_surfaceOldImage = nullptr;
        return;
    }
    if (!m_surfaceOldRT) return;
    if (m_renderTarget)
        m_renderTarget->EndDraw();   // 兼容 RT 收帧
    m_renderTarget = m_surfaceOldRT;
    m_surfaceOldRT = nullptr;
}

bool Renderer::DrawSurfaceBitmap(ID2D1Bitmap *bmp, const Rect &dst, float alpha, int interp) {
    if (!m_renderTarget || !bmp) return false;
    if (alpha < 0.0f) alpha = 0.0f;
    if (alpha > 1.0f) alpha = 1.0f;
    D2D1_RECT_F d = dst.ToD2D();
    m_renderTarget->DrawBitmap(
        bmp, &d, alpha,
        interp == 0 ? D2D1_BITMAP_INTERPOLATION_MODE_NEAREST_NEIGHBOR
                    : D2D1_BITMAP_INTERPOLATION_MODE_LINEAR,
        nullptr);
    return true;
}

bool Renderer::ReadSurfacePixel(ID2D1Bitmap *bmp, UINT x, UINT y, Color *out) {
    if (!out) return false;
    *out = Color(0, 0, 0, 0);
    if (!bmp) return false;
    if (!m_useDComp || !m_ctx) return false;   // 仅 DComp 路径（HwndRT 无读回）

    // CPU_READ 临时位图拷贝再 Map（CPU_READ 不能与 TARGET 组合，须借桥）
    ID2D1Bitmap1 *src = nullptr;
    if (FAILED(bmp->QueryInterface(__uuidof(ID2D1Bitmap1),
                                   reinterpret_cast<void **>(&src))))
        return false;
    D2D1_SIZE_U sz = src->GetPixelSize();
    bool ok = false;
    if (x < sz.width && y < sz.height) {
        ID2D1Bitmap1 *tmp = nullptr;
        D2D1_BITMAP_PROPERTIES1 bp = D2D1::BitmapProperties1(
            D2D1_BITMAP_OPTIONS_CPU_READ | D2D1_BITMAP_OPTIONS_CANNOT_DRAW,
            D2D1::PixelFormat(DXGI_FORMAT_B8G8R8A8_UNORM, D2D1_ALPHA_MODE_PREMULTIPLIED));
        if (SUCCEEDED(m_ctx->CreateBitmap(sz, nullptr, 0, &bp, &tmp))) {
            m_ctx->Flush();   // 绘制命令队列强制提交（CopyFromBitmap 立即执行，须先同步）
            if (SUCCEEDED(tmp->CopyFromBitmap(nullptr, src, nullptr))) {
                D2D1_MAPPED_RECT mr = {};
                if (SUCCEEDED(tmp->Map(D2D1_MAP_OPTIONS_READ, &mr))) {
                    const unsigned char *p = mr.bits + static_cast<SIZE_T>(y) * mr.pitch +
                                             static_cast<SIZE_T>(x) * 4;
                    // BGRA（premultiplied alpha）→ 直 alpha（便于脚本断言纯色）
                    float b = p[0] / 255.0f, g = p[1] / 255.0f, rr = p[2] / 255.0f, a = p[3] / 255.0f;
                    if (a > 0.0001f) { b /= a; g /= a; rr /= a; }
                    else { b = g = rr = 0.0f; }
                    *out = Color(rr, g, b, a);
                    ok = true;
                    tmp->Unmap();
                }
            }
            tmp->Release();
        }
    }
    src->Release();
    return ok;
}

void Renderer::DrawImageEx(ID2D1Bitmap *bmp, const Rect &dst, const Rect *src,
                           float alpha, int interp) {
    if (!m_renderTarget || !bmp) return;
    if (alpha < 0.0f) alpha = 0.0f;
    if (alpha > 1.0f) alpha = 1.0f;
    D2D1_RECT_F d = dst.ToD2D();
    D2D1_RECT_F s{};
    if (src) s = src->ToD2D();
    m_renderTarget->DrawBitmap(
        bmp, &d, alpha,
        interp == 0 ? D2D1_BITMAP_INTERPOLATION_MODE_NEAREST_NEIGHBOR
                    : D2D1_BITMAP_INTERPOLATION_MODE_LINEAR,
        src ? &s : nullptr);
}

}
