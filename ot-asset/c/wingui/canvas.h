
#pragma once
#include "ui_component.h"
#include "renderer.h"
#include <functional>

namespace gui {

// ---------------------------------------------------------------------------
// Canvas —— 自定义绘制画布（高定制化的核心组件）。
// 宿主语言（QOT 脚本 / Python / 任意绑 C ABI 的语言）绑定一个绘制回调，
// 每一帧在窗口绘制流程内被同步调用，回调里用绘制原语（vg_draw_*）自由作画。
// 回调线程 = 消息循环线程；回调期间 vg_draw_* 隐式绑定本画布上下文。
// ---------------------------------------------------------------------------
class Canvas : public UIComponent {
public:
    using DrawFn = std::function<void(Renderer *renderer, float w, float h)>;

    Canvas() = default;

    void OnDraw(DrawFn fn) { m_drawFn = std::move(fn); }
    bool HasDrawFn() const { return static_cast<bool>(m_drawFn); }

    void Draw(Renderer *renderer) override {
        if (!m_visible || !renderer) return;
        // v4：owner-draw（与 qg_canvas_on_draw 等价路径，state 位版）优先
        if (m_paintFn) {
            m_paintFn(renderer, m_bounds.Width(), m_bounds.Height(), StyleFlags());
            return;
        }
        if (m_drawFn) {
            m_drawFn(renderer, m_bounds.Width(), m_bounds.Height());
        }
    }

private:
    DrawFn m_drawFn;
};

}
