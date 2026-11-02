
#pragma once
#include "core_types.h"
#include <d2d1.h>
#include <d2d1_1.h>
#include <dwrite.h>
#include <d2d1effects.h>
#include <d3d11.h>
#include <dxgi1_2.h>
#include <dcomp.h>
#include <memory>
#include <vector>

namespace gui
{


    // ---------------------------------------------------------------------------
    // 渲染后端（v2 · DirectComposition）：
    //   * 首选 D3D11 + DXGI 合成交换链 + DirectComposition visual —— DWM 直接
    //     合成，窗口无重定向表面参与，resize/动画不闪烁，透明/玻璃态可用；
    //   * DComp 栈初始化失败（老系统/无 GPU/无 dcomp.dll）自动回退经典
    //     ID2D1HwndRenderTarget 路径，行为与 v1 完全一致；
    //   * 两条路径对外统一为一个 ID2D1RenderTarget*（ID2D1DeviceContext 是它的
    //     子类），所有绘制原语零感知后端。
    // ---------------------------------------------------------------------------
    class Renderer
    {
    public:
        Renderer();
        ~Renderer();

        bool Initialize(HWND hwnd);
        void Resize(const Size &size);
        void BeginDraw();
        void EndDraw();
        void Clear(const Color &color);


        void FillRect(const Rect &rect, const Color &color);
        void DrawRect(const Rect &rect, const Color &color, float strokeWidth = 1.0f);
        void FillRoundedRect(const RoundedRect &rect, const Color &color);
        void DrawRoundedRect(const RoundedRect &rect, const Color &color, float strokeWidth = 1.0f);
        void FillEllipse(const Ellipse &ellipse, const Color &color);
        void DrawEllipse(const Ellipse &ellipse, const Color &color, float strokeWidth = 1.0f);
        void DrawLine(const Point &p1, const Point &p2, const Color &color, float strokeWidth = 1.0f);


        void FillRectGradient(const Rect &rect, const Color &startColor, const Color &endColor, bool vertical = true);
        void FillRoundedRectGradient(const RoundedRect &rect, const Color &startColor, const Color &endColor, bool vertical = true);
        void PushClip(const Rect &rect)
        {
            if (m_renderTarget)
            {
                m_renderTarget->PushAxisAlignedClip(rect.ToD2D(), D2D1_ANTIALIAS_MODE_PER_PRIMITIVE);
            }
        }

        void PopClip()
        {
            if (m_renderTarget)
            {
                m_renderTarget->PopAxisAlignedClip();
            }
        }

        // 整体透明度层（组件级淡入淡出/复杂动效用）：opacity < 1 时生效
        bool PushLayerOpacity(float opacity, const Rect &bounds);
        void PopLayerOpacity();

        // ---- 绘制变换原语（Canvas 组合动效的根基；可嵌套 push/pop） ----
        void PushTransform();                                  // 保存当前矩阵
        void PopTransform();                                   // 恢复
        void Translate(float dx, float dy);                    // 当前 = 当前 × 平移
        void Rotate(float cx, float cy, float radians);        // 绕点旋转
        void Scale(float cx, float cy, float sx, float sy);    // 绕点缩放
        D2D1_MATRIX_3X2_F CurrentTransform() const;

        // ---- 层模糊原语（把一层绘制经高斯效果回贴；仅合成路径可用） ----
        bool PushBlurLayer(float radius, const Rect &bounds);
        void PopBlurLayer();

        void DrawText(const std::wstring &text, const Rect &rect, const Color &color, float fontSize = 16.0f, bool bold = false, bool center = true);
        Size MeasureText(const std::wstring &text, float fontSize = 16.0f, bool bold = false);

        // 对齐文本：halign/valign = 0 起 / 1 居中 / 2 末（Label 与控件文字共用）
        void DrawTextAligned(const std::wstring &text, const Rect &rect, const Color &color,
                             float fontSize, bool bold, int halign, int valign, bool wrap = false);
        // 富文本（v3  4）：wrap=true 自动换行；ellipsis=true 超宽裁剪加省略号
        void DrawTextAlignedEx(const std::wstring &text, const Rect &rect, const Color &color,
                               float fontSize, bool bold, int halign, int valign,
                               bool wrap, bool ellipsis);
        // 富测量（v3  4）：wrap=true 时返回换行后的实际宽高（布局系统用）
        Size MeasureTextEx(const std::wstring &text, float fontSize, bool bold,
                           bool wrap, float maxWidth);
        // 字体族（默认 Segoe UI，中文回落微软雅黑；可切）
        void SetFontFamily(const std::wstring &family);
        const std::wstring &GetFontFamily() const { return m_fontFamily; }
        // 圆角阴影（卡片/按钮的立体感；offsetY 向下为正，blur 为软度）
        void FillRoundedRectShadow(const RoundedRect &rr, float offsetY, float blur, float strength);
        // 文本描边宽度上限（防溢出）：按 rect 宽度裁剪
        Size MeasureTextConstrained(const std::wstring &text, float fontSize, bool bold, float maxWidth);

        // 图片解码（WIC → D2D 位图，Image 组件用）；失败返回 nullptr
        ID2D1Bitmap *LoadImageFile(const std::wstring &path, UINT *outW, UINT *outH);

        // 当前渲染目标（HwndRT 回退 / DComp 设备上下文，统一基类）
        ID2D1RenderTarget *GetRenderTarget() { return m_renderTarget; }
        IDWriteFactory *GetWriteFactory() { return m_writeFactory; }
        // 是否跑在 DirectComposition 合成路径
        bool IsComposed() const { return m_useDComp; }

    private:
        void CreateResources();        // 依序尝试 DComp → HwndRT 回退
        bool CreateDCompResources();   // DComp 栈（成功即 m_useDComp = true）
        void CreateHwndResources();    // 经典 HwndRenderTarget 回退
        bool RebuildDCompTarget();     // resize / 设备重建后重挂交换链位图
        void ReleaseRenderTargets();   // 释放渲染目标（两条路径共用；刷掉笔刷缓存）
        void ReleaseDCompStack();      // 释放 DComp 全套
        void FlushBrushCache();        // 笔刷挂在目标设备上：目标重建即整体失效
        ID2D1SolidColorBrush *GetBrush(const Color &color);
        IDWriteTextFormat *GetTextFormat(float fontSize, bool bold);
        std::wstring m_fontFamily;

        HWND m_hwnd;
        Size m_size;
        ID2D1Factory1 *m_d2dFactory;
        ID2D1RenderTarget *m_renderTarget;   // 统一入口：DeviceContext（DComp）或 HwndRT（回退）
        ID2D1HwndRenderTarget *m_hwndRT;     // 回退路径的原生目标（Resize 用）
        IDWriteFactory *m_writeFactory;

        // ---- DirectComposition 后端 ----
        IDXGISwapChain1 *m_swapchain;
        ID2D1Device *m_d2dDevice;
        ID2D1DeviceContext *m_ctx;
        ID2D1Bitmap1 *m_targetBitmap;
        IDCompositionDevice *m_dcompDevice;
        IDCompositionTarget *m_dcompTarget;
        IDCompositionVisual *m_dcompVisual;
        bool m_useDComp;

        struct BrushCache
        {
            Color color;
            ID2D1SolidColorBrush *brush;
        };
        std::vector<BrushCache> m_brushCache;

        struct TextFormatCache
        {
            float fontSize;
            bool bold;
            IDWriteTextFormat *format;
        };
        std::vector<TextFormatCache> m_textFormatCache;

        // 图片解码（WIC 工厂进程级共享）
        void *m_wicFactory;   // IWICImagingFactory*（避免所有 TU 都拉 wincodec.h）

        // 变换栈与模糊层状态
        std::vector<D2D1_MATRIX_3X2_F> m_transformStack;
        ID2D1CommandList *m_blurCmd;        // 模糊层捕获中的命令列表
        ID2D1Image *m_blurOldTarget;        // 捕获前的目标
        float m_blurRadius;
        Rect m_blurBounds;
    };

}
