
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
                m_clipStack.push_back(0);
            }
        }

        void PopClip()
        {
            if (!m_renderTarget || m_clipStack.empty()) return;
            if (m_clipStack.back() == 0)
                m_renderTarget->PopAxisAlignedClip();
            else
                m_renderTarget->PopLayer();
            m_clipStack.pop_back();
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

        // ================================================================
        // v5：绘制原语
        //   * 路径几何：贝塞尔/圆弧/SVG 弧/子形状，填充/描边/边界/命中；
        //   * 画刷系统：多停靠点线性/径向（焦点可偏移）/位图画刷 + extend
        //     模式与画刷变换，句柄化跨帧复用；
        //   * 线型：线帽/拐角/斜接限制/虚线数组与相位（对全部描边生效）；
        //   * 富文本：inline 混排（每 span 字号/颜色/字重/斜体/下划线/
        //     删除线/渐变画刷文字）+ 行高 + 字距，自定义 TextRenderer 实现；
        //   * 合成效果层：16 种混合模式、任意内容 GPU 高斯真阴影、
        //     画刷透明度蒙版（OpacityMask）、任意路径几何裁剪；
        //   * 离屏面：位图缓存层（复杂静态内容画一次贴多帧）+ 像素读回；
        //   * 图片精控：插值模式/透明度/圆角裁剪。
        // ================================================================

        // ---- 路径几何（构建器模式：reset → moveTo/lineTo/… → fill/stroke） ----
        bool PathReset();               // 新建路径（旧的丢弃）；失败 false（无工厂）
        void PathMoveTo(float x, float y);
        void PathLineTo(float x, float y);
        void PathQuadTo(float cx, float cy, float x, float y);
        void PathBezierTo(float c1x, float c1y, float c2x, float c2y, float x, float y);
        // 极坐标弧：中心 + 半径 + 起始角 + 扫过角（弧度；正 = 屏幕顺时针）
        void PathArcTo(float cx, float cy, float rx, float ry, float startRad, float sweepRad);
        // SVG 端点参数化弧（与 WinUI/SVG Path Data 的 A 命令一致）
        void PathSvgArcTo(float rx, float ry, float rotationRad, bool largeArc, bool sweep, float x, float y);
        void PathAddRect(float x, float y, float w, float h);           // 独立闭合子形状
        void PathAddRoundRect(float x, float y, float w, float h, float rx, float ry);
        void PathAddEllipse(float cx, float cy, float rx, float ry);
        void PathClose();               // 闭合当前子路径（其后 moveTo 开新子路径）
        void FillPath(const Color &color);
        void FillPathBrush(ID2D1Brush *brush);
        void StrokePath(const Color &color, float width);
        void StrokePathBrush(ID2D1Brush *brush, float width);
        bool PathBounds(Rect *out);     // 路径边界（填充域，不含描边加宽）
        bool PathHitTest(float x, float y, bool *out);                  // 填充命中
        bool PathStrokeHitTest(float x, float y, float width, bool *out); // 描边命中

        // ---- 线型状态（对全部描边原语生效；默认 = flat/miter/实线，与旧版一致） ----
        void SetLineCap(int cap);       // 0 flat 1 square 2 round
        void SetLineJoin(int join);     // 0 miter 1 bevel 2 round 3 miter_or_bevel
        void SetMiterLimit(float limit);
        void SetDash(const float *dashes, int count, float phase);  // count<=0 恢复实线

        // ---- 画刷创建（句柄/id 由 ABI 层管理；这里只负责创建对象） ----
        ID2D1Brush *CreateSolidBrushObj(const Color &c);
        ID2D1Brush *CreateLinearBrushObj(float x0, float y0, float x1, float y1,
                                         const float *positions, const Color *colors, int count);
        ID2D1Brush *CreateRadialBrushObj(float cx, float cy, float fx, float fy,
                                         float rx, float ry,
                                         const float *positions, const Color *colors, int count);
        ID2D1BitmapBrush *CreateBitmapBrushObj(ID2D1Bitmap *bmp);
        static bool BrushSetExtend(ID2D1Brush *brush, int modeX, int modeY);   // 0 clamp 1 tile 2 mirror
        static bool BrushSetTransform(ID2D1Brush *brush,
                                      float m11, float m12, float m21, float m22,
                                      float dx, float dy);

        // ---- 形状 × 画刷（纯色版之外的填充表达） ----
        void FillRectBrush(const Rect &rect, ID2D1Brush *brush);
        void DrawRectBrush(const Rect &rect, ID2D1Brush *brush, float width);
        void FillRoundedRectBrush(const RoundedRect &rect, ID2D1Brush *brush);
        void DrawRoundedRectBrush(const RoundedRect &rect, ID2D1Brush *brush, float width);
        void FillEllipseBrush(const Ellipse &ellipse, ID2D1Brush *brush);
        void DrawEllipseBrush(const Ellipse &ellipse, ID2D1Brush *brush, float width);

        // ---- 富文本（inline 混排） ----
        struct RichSpan {
            std::wstring text;
            float size = 16.0f;
            Color color;
            int weight = 400;        // DWRITE_FONT_WEIGHT 数值（400 正常 700 粗）
            bool italic = false;
            bool underline = false;
            bool strikethrough = false;
            ID2D1Brush *brush = nullptr;   // 非空时优先（渐变/位图文字）
        };
        void RichReset();
        void RichAdd(const RichSpan &span);
        void RichSetLineHeight(float lineH, float baseline);   // <=0 恢复默认
        void RichSetSpacing(float leading, float trailing, float advance, float minAdvance);
        bool RichDraw(const Rect &rect, int halign, int valign, bool wrap);
        bool RichMeasure(bool wrap, float maxWidth, Size *out);

        // ---- 合成效果层（混合/真阴影：仅 DComp 路径；蒙版/几何裁剪：全路径） ----
        bool PushBlendLayer(int compositeMode, const Rect &bounds);   // D2D1_COMPOSITE_MODE 枚举值
        void PopBlendLayer();
        bool PushShadowLayer(const Color &color, float blur, float dx, float dy, const Rect &bounds);
        void PopShadowLayer();
        bool PushMaskLayerBrush(ID2D1Brush *brush, const Rect &bounds, float opacity);
        void PopMaskLayer();
        bool PushClipPath();          // 当前路径作几何裁剪（含抗锯齿边）
        bool PushClipRoundRect(const Rect &rect, float rx, float ry);  // 圆角矩形裁剪
        // PopClip 统一弹（内部栈记录 axis-aligned / layer 两种类型）

        // ---- 离屏面（位图缓存层；绘制路由进面，完事整体贴回） ----
        // DComp 路径：返回 TARGET|CPU_READ 位图（可读回像素）；outRT = nullptr
        // HwndRT 回退：返回 bitmapRT 的位图，outRT = ID2D1BitmapRenderTarget*
        ID2D1Bitmap *CreateSurfaceBitmap(UINT w, UINT h, ID2D1BitmapRenderTarget **outRT);
        bool BeginSurface(ID2D1Bitmap *bmp, ID2D1BitmapRenderTarget *rt); // 后续原语画进面（清为透明）
        void EndSurface();
        bool DrawSurfaceBitmap(ID2D1Bitmap *bmp, const Rect &dst, float alpha, int interp);
        bool ReadSurfacePixel(ID2D1Bitmap *bmp, UINT x, UINT y, Color *out);

        // ================================================================
        // v6：句柄几何 + 文字样式块（声明式视觉模板的渲染地基）
        //   * 句柄几何：path store 固化的 ID2D1Geometry 跨帧复用——
        //     填充/描边/命中/裁剪共用同一份（几何挂 factory，跨设备安全）；
        //   * 文字样式块：size/weight/italic/family 全参数 TextFormat
        //     缓存（v2 缓存键 size+bold 的超集，零行为变化）。
        // ================================================================

        // ---- 句柄几何（qg_path_store 的渲染对应物） ----
        ID2D1PathGeometry *PathTakeRef();     // 关闭 sink，AddRef 并返回（表持有引用）
        void FillGeometryBrush(ID2D1Geometry *geo, ID2D1Brush *brush);
        void FillGeometry(ID2D1Geometry *geo, const Color &color);
        void StrokeGeometryBrush(ID2D1Geometry *geo, ID2D1Brush *brush, float width);
        void StrokeGeometry(ID2D1Geometry *geo, const Color &color, float width);
        bool GeometryHitTest(ID2D1Geometry *geo, float x, float y, bool *out);
        bool PushClipGeometry(ID2D1Geometry *geo);   // 任意句柄几何裁剪（pop 用 PopClip）

        // ---- 文字样式块（family 空串 = 当前默认字体族） ----
        IDWriteTextFormat *GetTextFormatEx(float size, int weight, bool italic,
                                           const std::wstring &family);
        void DrawTextStyled(const std::wstring &text, const Rect &rect, const Color &color,
                             float size, int weight, bool italic, const std::wstring &family,
                             int halign, int valign, bool wrap, bool ellipsis);

        // ---- 图片精控：插值模式（0 nearest 1 linear）+ 源矩形 ----
        void DrawImageEx(ID2D1Bitmap *bmp, const Rect &dst, const Rect *src,
                         float alpha, int interp);

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

        // ---- v5：路径构建器 ----
        void ClosePathSink();          // 收尾 sink（未闭合 figure 以 OPEN 收，路径可用）
        void EnsurePathFigure();       // 无开放 figure 时以当前点开启
        ID2D1StrokeStyle *GetStrokeStyle();   // 线型缓存（参数脏时重建）

        // ---- v5：富文本排版构建（span → TextLayout + 区间表） ----
        bool BuildRichLayout(const Rect &rect, int halign, int valign, bool wrap,
                             IDWriteTextLayout **outLayout,
                             std::vector<std::pair<UINT32, UINT32>> *outRanges);

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
            bool bold;                 // 兼容保留（weight < 600 视为非粗）
            int weight = 400;          // v6：DWRITE 字重数值 100..900
            bool italic = false;       // v6：斜体
            std::wstring family;        // v6：字体族（空 = m_fontFamily）
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

        // ---- v5：路径构建器状态 ----
        ID2D1PathGeometry *m_path;          // 构建中的路径（sink 关闭后可用于绘制/查询）
        ID2D1GeometrySink *m_pathSink;      // 打开中的 sink
        bool m_pathFigureOpen;              // 当前 figure 已 BeginFigure
        float m_pathCurX, m_pathCurY;       // 当前点（SVG 弧端点参数化需要）

        // ---- v5：线型状态 ----
        int m_lineCap;                      // D2D1_CAP_STYLE
        int m_lineJoin;                     // D2D1_LINE_JOIN
        float m_miterLimit;
        std::vector<float> m_dash;
        float m_dashPhase;
        ID2D1StrokeStyle *m_strokeStyle;    // 缓存（脏标记控制重建）
        bool m_strokeStyleDirty;

        // ---- v5：富文本状态 ----
        std::vector<RichSpan> m_richSpans;
        float m_richLineH, m_richBaseline;  // <=0 = 默认
        float m_richLeadSpacing, m_richTrailSpacing, m_richAdvanceSpacing, m_richMinAdvance;
        bool m_richSpacingSet;

        // ---- v5：合成效果层栈 ----
        struct EffectLayer {
            int kind;                       // 0 blur 1 blend 2 shadow
            ID2D1CommandList *cmd;          // 捕获中的命令列表
            ID2D1Image *oldTarget;          // 捕获前的目标
            ID2D1Bitmap *snap;              // dest 快照（blend 用）
            int blendMode;
            Color shadowColor;
            float shadowBlur, shadowDx, shadowDy;
            Rect bounds;
        };
        std::vector<EffectLayer> m_effectStack;

        // ---- v5：离屏面状态 ----
        ID2D1Image *m_surfaceOldImage;     // DComp：进入离屏前的 ctx 目标（GetTarget AddRef）
        ID2D1RenderTarget *m_surfaceOldRT; // HwndRT：进入离屏前的渲染目标

        // ---- v5：裁剪类型栈（pop 时弹对 API） ----
        std::vector<int> m_clipStack;      // 0 axis-aligned 1 layer（几何/圆角/蒙版）
    };

}
