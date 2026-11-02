
#pragma once
#include "core_types.h"
#include <functional>
#include <string>

namespace gui {

class Renderer;   // 前向声明（ui_component 只用其指针；完整定义在 renderer.h）

// ---------------------------------------------------------------------------
// UIComponent —— 一切控件的基类。
//
// v4 样式系统（原语哲学贯彻到组件层）：
//   * CompStyle 样式叠加层：字段 isSet 才生效，未设置走组件默认绘制
//     —— 零定制时行为与旧版逐位一致（向后兼容铁律）。
//   * SetPaintFn 绘制接管（owner-draw）：注册后组件默认绘制整体被替换，
//     回调里用绘制原语（vg_draw_*）自由作画，坐标 = 组件本地原点。
//     组件退化为「交互语义 + 自由画布」：点击/拖拽/滚轮等输入语义不变。
//   * StyleFlags 状态位：0x1 hover / 0x2 pressed / 0x4 disabled / 0x8
//     focused —— 回调/状态色解析共用；子类重写补充自身知道的状态。
// ---------------------------------------------------------------------------
class UIComponent {
public:
    virtual ~UIComponent() = default;

    virtual void Draw(Renderer* renderer) = 0;
    virtual void Update(float deltaTime) {}
    virtual bool HandleMouseMove(const Point& mousePos) { return false; }
    virtual bool HandleMouseDown(const Point& mousePos, int button) { return false; }
    virtual bool HandleMouseUp(const Point& mousePos, int button) { return false; }
    // 滚轮（v3）：delta = 格数（WM_MOUSEWHEEL / WHEEL_DELTA，正=上滚）；
    // mousePos = 客户区坐标（命中测试由组件自查）。返回 true = 已消费。
    virtual bool HandleMouseWheel(float delta, const Point& mousePos) { return false; }

    void SetBounds(const Rect& bounds) { m_bounds = bounds; }
    Rect GetBounds() const { return m_bounds; }
    void SetVisible(bool visible) { m_visible = visible; }
    bool IsVisible() const { return m_visible; }
    void SetEnabled(bool enabled) { m_enabled = enabled; }
    bool IsEnabled() const { return m_enabled; }
    void SetId(int id) { m_id = id; }
    int GetId() const { return m_id; }
    // 组件级透明度（0~1；窗口绘制时以透明度层呈现，动画/淡入淡出用）
    void SetOpacity(float a) { m_opacity = a < 0.0f ? 0.0f : (a > 1.0f ? 1.0f : a); }
    float GetOpacity() const { return m_opacity; }

    // ---- v4 样式叠加层 ----
    // 状态索引：0 hover / 1 pressed / 2 disabled / 3 focused
    struct CompStyle {
        bool bgSet = false;      Color bg;
        bool bgGradSet = false;  Color bgGrad1, bgGrad2;  int bgGradVertical = 1;
        bool borderSet = false;  Color borderColor;       float borderWidth = 1.0f;
        bool radiusSet = false;  float radius = 8.0f;
        bool padSet = false;     float padL = 0.0f, padT = 0.0f, padR = 0.0f, padB = 0.0f;
        bool textSet = false;    Color textColor;  float textSize = 14.0f;  bool textBold = false;
        bool stateBgSet[4] = {false, false, false, false};
        Color stateBg[4];
        bool stateTextSet[4] = {false, false, false, false};
        Color stateText[4];
    };
    CompStyle& Style() { return m_style; }
    const CompStyle& Style() const { return m_style; }

    // ---- v4 绘制接管（owner-draw）----
    using PaintFn = std::function<void(Renderer*, float w, float h, unsigned state)>;
    void SetPaintFn(PaintFn fn) { m_paintFn = std::move(fn); }
    void ClearPaintFn() { m_paintFn = nullptr; }
    bool HasPaintFn() const { return static_cast<bool>(m_paintFn); }

    // 组件当前状态位（子类重写补充自身知道的状态；基类只知 disabled）
    virtual unsigned StyleFlags() const { return m_enabled ? 0u : 0x4u; }

    // ---- 状态色解析：命中优先级 disabled > pressed > focused > hover ----
    // 返回 true = out 已填（调用方直接用）；false = 无覆盖，走组件默认。
    bool ResolvedStateBg(unsigned flags, Color& out) const {
        int idx = -1;
        if (flags & 0x4) idx = 2;
        else if (flags & 0x2) idx = 1;
        else if (flags & 0x8) idx = 3;
        else if (flags & 0x1) idx = 0;
        if (idx >= 0 && m_style.stateBgSet[idx]) { out = m_style.stateBg[idx]; return true; }
        if (m_style.bgSet) { out = m_style.bg; return true; }
        return false;
    }
    bool ResolvedStateText(unsigned flags, Color& out) const {
        int idx = -1;
        if (flags & 0x4) idx = 2;
        else if (flags & 0x2) idx = 1;
        else if (flags & 0x8) idx = 3;
        else if (flags & 0x1) idx = 0;
        if (idx >= 0 && m_style.stateTextSet[idx]) { out = m_style.stateText[idx]; return true; }
        if (m_style.textSet) { out = m_style.textColor; return true; }
        return false;
    }

protected:
    Rect m_bounds;
    bool m_visible = true;
    bool m_enabled = true;
    int m_id = 0;
    float m_opacity = 1.0f;

    CompStyle m_style;
    PaintFn m_paintFn;
};

}  
