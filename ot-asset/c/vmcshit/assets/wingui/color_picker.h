// ===========================================================================
// color_picker.h —— v6 配套：颜色选择器
//
//   * 布局：上部 SV 方块（横=饱和度 0..1，纵=明度 1..0），底部色相条
//     （7 停靠点彩虹渐变），右下当前色预览块；
//   * 交互：SV 区点击/拖拽调 s/v，色相条点击/拖拽调 h；实时 CHANGE；
//   * 吃 v6 模板红利：bg part 渲染容器框，SV/色相条是连续渐变保留代码绘制；
//   * 事件：QG_EVT_CHANGE（value = 0xRRGGBB packed，double 精确表示 24 位色）。
// ===========================================================================
#pragma once
#include "ui_component.h"
#include "renderer.h"
#include "animation.h"
#include "visual_template.h"
#include <d2d1.h>
#include <functional>
#include <algorithm>
#include <cmath>

namespace gui {

// HSV ↔ RGB（h 0..360，s/v 0..1；输出 0..1 线性分量）
inline void HsvToRgb(float h, float s, float v, float* r, float* g, float* b) {
    float c = v * s;
    float hh = h / 60.0f;
    float x = c * (1.0f - std::fabs(std::fmod(hh, 2.0f) - 1.0f));
    float rr = 0, gg = 0, bb = 0;
    if (hh < 1)      { rr = c; gg = x; }
    else if (hh < 2) { rr = x; gg = c; }
    else if (hh < 3) { gg = c; bb = x; }
    else if (hh < 4) { gg = x; bb = c; }
    else if (hh < 5) { rr = x; bb = c; }
    else             { rr = c; bb = x; }
    float m = v - c;
    *r = rr + m; *g = gg + m; *b = bb + m;
}

inline void RgbToHsv(float r, float g, float b, float* h, float* s, float* v) {
    float mx = std::max(r, std::max(g, b));
    float mn = std::min(r, std::min(g, b));
    float d = mx - mn;
    *v = mx;
    *s = mx > 1e-6f ? d / mx : 0.0f;
    if (d < 1e-6f) { *h = 0.0f; return; }
    float hh;
    if (mx == r)      hh = std::fmod((g - b) / d, 6.0f);
    else if (mx == g) hh = (b - r) / d + 2.0f;
    else              hh = (r - g) / d + 4.0f;
    *h = hh * 60.0f;
    if (*h < 0.0f) *h += 360.0f;
}

class ColorPicker : public UIComponent {
public:
    using ColorCallback = std::function<void(float r, float g, float b)>;

    ColorPicker()
        : m_hue(210.0f), m_sat(0.7f), m_val(0.9f),
          m_draggingSv(false), m_draggingHue(false),
          m_borderColor(0.35f, 0.35f, 0.45f, 1.0f), m_isHovered(false) {
        UpdateRgb();
    }

    void GetRgb(float* r, float* g, float* b) const {
        *r = m_r; *g = m_g; *b = m_b;
    }
    Color GetColor() const { return Color(m_r, m_g, m_b, 1.0f); }
    void SetRgb(float r, float g, float b) {
        RgbToHsv(r, g, b, &m_hue, &m_sat, &m_val);
        m_r = r; m_g = g; m_b = b;   // 精确保留调用方给的分量
        if (m_callback) m_callback(m_r, m_g, m_b);
    }
    unsigned GetPackedRgb() const {
        auto to8 = [](float v) { int n = (int)std::lround(v * 255.0f);
                                 return n < 0 ? 0 : (n > 255 ? 255 : n); };
        return ((unsigned)to8(m_r) << 16) | ((unsigned)to8(m_g) << 8) | (unsigned)to8(m_b);
    }
    void OnColorChanged(ColorCallback cb) { m_callback = cb; }

    void Draw(Renderer* renderer) override {
        if (!m_visible || !renderer) return;
        if (m_paintFn) {
            m_paintFn(renderer, m_bounds.Width(), m_bounds.Height(), StyleFlags());
            return;
        }
        // v6：声明式视觉模板（容器框；SV/色相条连续渐变保留代码绘制）
        if (m_tplId) {
            RenderTemplate(this, renderer, StyleFlags());
        } else {
            renderer->FillRect(m_bounds, Color(0.16f, 0.16f, 0.24f, 1.0f));
            renderer->DrawRect(m_bounds, m_borderColor, 1.0f);
        }

        Rect sv = SvRect();
        Rect hue = HueRect();
        Rect prev = PreviewRect();

        // ---- SV 方块：hue 纯色底 + 白(顶→透明) + 透明(→底黑) 两层叠加 ----
        float hr, hg, hb;
        HsvToRgb(m_hue, 1.0f, 1.0f, &hr, &hg, &hb);
        renderer->FillRect(sv, Color(hr, hg, hb, 1.0f));
        renderer->FillRectGradient(sv, Color(1, 1, 1, 1), Color(1, 1, 1, 0), true);
        renderer->FillRectGradient(sv, Color(0, 0, 0, 0), Color(0, 0, 0, 1), true);

        // SV 指示圈
        float sx = sv.left + m_sat * sv.Width();
        float sy = sv.top + (1.0f - m_val) * sv.Height();
        renderer->DrawEllipse(Ellipse(sx, sy, 6.0f, 6.0f), Color(1, 1, 1, 0.95f), 1.5f);
        renderer->DrawEllipse(Ellipse(sx, sy, 7.5f, 7.5f), Color(0, 0, 0, 0.6f), 1.0f);

        // ---- 色相条：7 停靠点彩虹（v5 画刷；一次性用完即释） ----
        float stopPos[7] = {0.0f, 1.0f / 6.0f, 2.0f / 6.0f, 3.0f / 6.0f,
                            4.0f / 6.0f, 5.0f / 6.0f, 1.0f};
        float stopCol[7][3] = {{1,0,0},{1,1,0},{0,1,0},{0,1,1},{0,0,1},{1,0,1},{1,0,0}};
        float positions[7];
        Color colors[7];
        for (int i = 0; i < 7; ++i) {
            positions[i] = stopPos[i];
            colors[i] = Color(stopCol[i][0], stopCol[i][1], stopCol[i][2], 1.0f);
        }
        float hcy = (hue.top + hue.bottom) / 2.0f;
        ID2D1Brush* hueBrush = renderer->CreateLinearBrushObj(
            hue.left, hcy, hue.right, hcy, positions, colors, 7);
        if (hueBrush) {
            renderer->FillRectBrush(hue, hueBrush);
            hueBrush->Release();
        } else {
            renderer->FillRect(hue, Color(0.5f, 0.5f, 0.5f, 1.0f));
        }
        // 色相指示（双描边小胶囊）
        float hx = hue.left + (m_hue / 360.0f) * hue.Width();
        renderer->DrawRoundedRect(RoundedRect(Rect(hx - 4, hue.top, hx + 4, hue.bottom),
                                              3.0f, 3.0f), Color(1, 1, 1, 0.95f), 2.0f);
        renderer->DrawRoundedRect(RoundedRect(Rect(hx - 5.5f, hue.top - 1.5f, hx + 5.5f, hue.bottom + 1.5f),
                                              3.5f, 3.5f), Color(0, 0, 0, 0.55f), 1.0f);

        // ---- 当前色预览 ----
        renderer->FillRect(prev, Color(m_r, m_g, m_b, 1.0f));
        renderer->DrawRect(prev, Color(0, 0, 0, 0.4f), 1.0f);
    }

    void Update(float) override {}

    unsigned StyleFlags() const override {
        unsigned f = UIComponent::StyleFlags();
        if (m_isHovered) f |= 0x1;
        return f;
    }

    bool HandleMouseMove(const Point& mousePos) override {
        if (!m_enabled || !m_visible) return false;
        bool inside = mousePos.x >= m_bounds.left && mousePos.x <= m_bounds.right &&
                      mousePos.y >= m_bounds.top && mousePos.y <= m_bounds.bottom;
        m_isHovered = inside;
        if (m_draggingSv) { ApplySv(mousePos); return true; }
        if (m_draggingHue) { ApplyHue(mousePos); return true; }
        return inside;
    }

    bool HandleMouseDown(const Point& mousePos, int button) override {
        if (!m_enabled || !m_visible || button != 0) return false;
        if (Contains(SvRect(), mousePos)) {
            m_draggingSv = true;
            ApplySv(mousePos);
            return true;
        }
        if (Contains(HueRect(), mousePos)) {
            m_draggingHue = true;
            ApplyHue(mousePos);
            return true;
        }
        return false;
    }

    bool HandleMouseUp(const Point& mousePos, int button) override {
        (void)mousePos; (void)button;
        if (m_draggingSv || m_draggingHue) {
            m_draggingSv = m_draggingHue = false;
            return true;
        }
        return false;
    }

private:
    static bool Contains(const Rect& rc, const Point& p) {
        return p.x >= rc.left && p.x <= rc.right && p.y >= rc.top && p.y <= rc.bottom;
    }
    // 布局：pad 8；SV 方块占上部；底部行 = 色相条(18 高) + 预览块(18×18)
    Rect SvRect() const {
        float pad = 8.0f, gap = 8.0f;
        return Rect(m_bounds.left + pad, m_bounds.top + pad,
                    m_bounds.right - pad,
                    m_bounds.bottom - pad - 18.0f - gap);
    }
    Rect HueRect() const {
        float pad = 8.0f, gap = 8.0f;
        float top = m_bounds.bottom - pad - 18.0f;
        return Rect(m_bounds.left + pad, top,
                    m_bounds.right - pad - 18.0f - gap, top + 18.0f);
    }
    Rect PreviewRect() const {
        float pad = 8.0f;
        float top = m_bounds.bottom - pad - 18.0f;
        return Rect(m_bounds.right - pad - 18.0f, top,
                    m_bounds.right - pad, top + 18.0f);
    }

    void ApplySv(const Point& p) {
        Rect sv = SvRect();
        float w = std::max(1.0f, sv.Width());
        float h = std::max(1.0f, sv.Height());
        m_sat = std::max(0.0f, std::min(1.0f, (p.x - sv.left) / w));
        m_val = 1.0f - std::max(0.0f, std::min(1.0f, (p.y - sv.top) / h));
        UpdateRgb();
    }
    void ApplyHue(const Point& p) {
        Rect hue = HueRect();
        float w = std::max(1.0f, hue.Width());
        m_hue = std::max(0.0f, std::min(359.999f, (p.x - hue.left) / w * 360.0f));
        UpdateRgb();
    }
    void UpdateRgb() {
        HsvToRgb(m_hue, m_sat, m_val, &m_r, &m_g, &m_b);
        if (m_callback) m_callback(m_r, m_g, m_b);
    }

    float m_hue, m_sat, m_val;       // HSV 状态（真源）
    float m_r, m_g, m_b;             // RGB 缓存（CHANGE 载荷）
    bool m_draggingSv, m_draggingHue;
    Color m_borderColor;
    bool m_isHovered;
    ColorCallback m_callback;
};

}  // namespace gui
