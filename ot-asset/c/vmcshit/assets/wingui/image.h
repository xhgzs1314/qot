
#pragma once
#include "ui_component.h"
#include "renderer.h"

namespace gui {

// ---------------------------------------------------------------------------
// Image —— 图片组件：WIC 解码（PNG/JPG/BMP/GIF 首帧）→ D2D 位图，
// 拉伸绘制到 bounds。SetFitWidth/Height 可保持比例适配（留白对齐）。
// ---------------------------------------------------------------------------
class Image : public UIComponent {
public:
    Image() = default;
    ~Image() { ReleaseBitmap(); }

    // 加载图片文件；失败返回 false（组件保留空白，不崩溃）
    bool Load(Renderer *renderer, const std::wstring &path)
    {
        ReleaseBitmap();
        if (!renderer) return false;
        m_bitmap = renderer->LoadImageFile(path, &m_imgW, &m_imgH);
        return m_bitmap != nullptr;
    }

    bool IsLoaded() const { return m_bitmap != nullptr; }
    UINT ImageWidth() const { return m_imgW; }
    UINT ImageHeight() const { return m_imgH; }

    // 绘制模式：0 拉伸铺满（默认） 1 等比适配（居中留白）
    void SetFitMode(int mode) { m_fitMode = mode; }
    int GetFitMode() const { return m_fitMode; }

    void Draw(Renderer *renderer) override {
        if (!m_visible || !renderer || !m_bitmap) return;

        Rect dst = m_bounds;
        if (m_fitMode == 1 && m_imgW > 0 && m_imgH > 0)
        {
            float bw = m_bounds.Width(), bh = m_bounds.Height();
            if (bw > 0 && bh > 0)
            {
                float scale = (bw / (float)m_imgW) < (bh / (float)m_imgH)
                                  ? (bw / (float)m_imgW) : (bh / (float)m_imgH);
                float w = (float)m_imgW * scale, h = (float)m_imgH * scale;
                dst = Rect(m_bounds.left + (bw - w) * 0.5f,
                           m_bounds.top + (bh - h) * 0.5f,
                           m_bounds.left + (bw - w) * 0.5f + w,
                           m_bounds.top + (bh - h) * 0.5f + h);
            }
        }

        // 透明度支持：图片淡入淡出走通用组件透明度
        bool layered = false;
        if (GetOpacity() < 0.999f)
        {
            layered = renderer->PushLayerOpacity(GetOpacity(), m_bounds);
        }
        D2D1_RECT_F r = D2D1::RectF(dst.left, dst.top, dst.right, dst.bottom);
        renderer->GetRenderTarget()->DrawBitmap(m_bitmap, &r);
        if (layered) renderer->PopLayerOpacity();
    }

private:
    void ReleaseBitmap()
    {
        if (m_bitmap) { m_bitmap->Release(); m_bitmap = nullptr; }
    }

    ID2D1Bitmap *m_bitmap = nullptr;
    UINT m_imgW = 0;
    UINT m_imgH = 0;
    int m_fitMode = 0;
};

}
