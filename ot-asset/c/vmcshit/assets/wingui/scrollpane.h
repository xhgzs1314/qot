/* ===========================================================================
 * scrollpane.h —— vgui v3 · 滚动容器（ 2）
 *
 * 设计：
 *   * 持内容虚高（content height）+ 滚动偏移；Draw = PushClip(视口) →
 *     translate(-offset) 绘制子组件 → PopClip；滚轮/拖拽驱动偏移；
 *   * 子组件 bounds = **内容坐标**（相对内容左上原点，不受滚动污染）——
 *     命中测试时鼠标坐标 +offset 反推，绘制时 transform 平移；
 *   * 滚动条 = 绘制原语自绘（轨道+滑块），拖拽命中参照 Slider 模式；
 *   * 滚动经 OnScroll 回调上报（ABI 层接事件通道 kind=16）。
 * 纪律：
 *   * 键盘焦点组件（TextBox）不进滚动区（窗口焦点链只认顶层组件），
 *     点击类控件（按钮/列表/滑块）可放心 attach。
 * =========================================================================== */
#pragma once
#include "ui_component.h"
#include "renderer.h"
#include <functional>
#include <vector>
#include <memory>
#include <algorithm>

namespace gui {

class ScrollPane : public UIComponent {
public:
    using ScrollCallback = std::function<void(float offset)>;

    ScrollPane()
        : m_contentHeight(0.0f)
        , m_scrollOffset(0.0f)
        , m_barWidth(8.0f)
        , m_wheelStep(48.0f)
        , m_backgroundColor(0.10f, 0.10f, 0.16f, 1.0f)
        , m_barTrack(0.18f, 0.18f, 0.26f, 0.6f)
        , m_barThumb(0.42f, 0.45f, 0.58f, 0.8f)
        , m_dragging(false)
        , m_dragStartY(0.0f)
        , m_dragStartOffset(0.0f) {}

    // ---- 内容与滚动 ----
    void SetContentHeight(float h) { m_contentHeight = h > 0.0f ? h : 0.0f; ClampOffset(); }
    float GetContentHeight() const { return m_contentHeight; }
    float GetScrollOffset() const { return m_scrollOffset; }
    void SetScrollOffset(float v)
    {
        m_scrollOffset = v;
        ClampOffset();
        if (m_scrollCb) m_scrollCb(m_scrollOffset);   // 滚轮/拖拽/编程统一发 kind=16
    }
    void SetWheelStep(float step) { m_wheelStep = step > 0.0f ? step : 48.0f; }
    void OnScroll(ScrollCallback cb) { m_scrollCb = std::move(cb); }

    // ---- v4 滚动条样式原语：轨道/把手颜色 + 宽度 ----
    void SetBarStyle(const Color& track, const Color& thumb, float width) {
        m_barTrack = track;
        m_barThumb = thumb;
        m_barWidth = width > 2.0f ? width : 2.0f;
    }

    // ---- 子组件（内容坐标）----
    void AddChild(std::unique_ptr<UIComponent> child)
    {
        if (child) m_children.push_back(std::move(child));
    }

    // 按 id 摘除子组件（所有权交还调用方；找不到返回空指针）
    std::unique_ptr<UIComponent> RemoveChild(int id)
    {
        for (auto it = m_children.begin(); it != m_children.end(); ++it) {
            if ((*it)->GetId() == id) {
                std::unique_ptr<UIComponent> p = std::move(*it);
                m_children.erase(it);
                return p;
            }
        }
        return nullptr;
    }

    float ViewportHeight() const { return m_bounds.Height(); }
    float MaxOffset() const
    {
        float m = m_contentHeight - ViewportHeight();
        return m > 0.0f ? m : 0.0f;
    }

    // ---- 绘制 ----
    void Draw(Renderer* renderer) override {
        if (!m_visible || !renderer) return;

        // v4：容器底色走样式叠加（滚动条样式走 SetBarStyle 原语）
        Color bg;
        if (!ResolvedStateBg(StyleFlags(), bg)) bg = m_backgroundColor;
        renderer->FillRect(m_bounds, bg);

        // 内容（clip 在视口内；translate 把内容坐标映到屏幕）
        renderer->PushClip(m_bounds);
        renderer->PushTransform();
        renderer->Translate(0.0f, -m_scrollOffset);
        for (auto& child : m_children) {
            if (child->IsVisible()) {
                bool layered = false;
                if (child->GetOpacity() < 0.999f)
                    layered = renderer->PushLayerOpacity(child->GetOpacity(), child->GetBounds());
                child->Draw(renderer);
                if (layered) renderer->PopLayerOpacity();
            }
        }
        renderer->PopTransform();
        renderer->PopClip();

        DrawScrollbar(renderer);
    }

    void Update(float deltaTime) override {
        for (auto& child : m_children) child->Update(deltaTime);
    }

    // ---- 命中与滚动 ----
    bool HandleMouseDown(const Point& mousePos, int button) override {
        if (!m_enabled || !m_visible || button != 0) return false;
        if (!IsInside(mousePos)) return false;

        if (HasScrollbar() && mousePos.x >= m_bounds.right - m_barWidth) {
            // 滑块按下 = 开始拖拽；轨道按下 = 跳滚
            float thumbY, thumbH;
            ThumbGeom(thumbY, thumbH);
            m_dragging = true;
            m_dragStartY = mousePos.y;
            m_dragStartOffset = m_scrollOffset;
            if (mousePos.y < thumbY || mousePos.y > thumbY + thumbH)
                ScrollToPos(mousePos.y);
            return true;
        }

        // 转发子组件：内容坐标 = 客户坐标 + offset
        Point p2(mousePos.x, mousePos.y + m_scrollOffset);
        for (auto it = m_children.rbegin(); it != m_children.rend(); ++it) {
            UIComponent* c = it->get();
            if (!c->IsVisible() || !c->IsEnabled()) continue;
            const Rect& b = c->GetBounds();
            if (p2.x >= b.left && p2.x <= b.right && p2.y >= b.top && p2.y <= b.bottom)
                if (c->HandleMouseDown(p2, button)) return true;
        }
        return true;   // 视口内空白也吃掉点击（防穿透）
    }

    bool HandleMouseMove(const Point& mousePos) override {
        if (!m_enabled || !m_visible) return false;
        if (m_dragging) {
            float thumbY, thumbH;
            ThumbGeom(thumbY, thumbH);
            float trackH = TrackHeight();
            float ratio = (trackH - thumbH) > 1.0f ? MaxOffset() / (trackH - thumbH) : 0.0f;
            SetScrollOffset(m_dragStartOffset + (mousePos.y - m_dragStartY) * ratio);
            return true;
        }
        if (!IsInside(mousePos)) return false;
        Point p2(mousePos.x, mousePos.y + m_scrollOffset);
        for (auto it = m_children.rbegin(); it != m_children.rend(); ++it) {
            if ((*it)->HandleMouseMove(p2)) break;
        }
        return IsInside(mousePos);
    }

    bool HandleMouseUp(const Point& mousePos, int button) override {
        if (m_dragging) {
            m_dragging = false;
            return true;
        }
        if (!m_enabled || !m_visible || !IsInside(mousePos)) return false;
        Point p2(mousePos.x, mousePos.y + m_scrollOffset);
        for (auto it = m_children.rbegin(); it != m_children.rend(); ++it) {
            if ((*it)->HandleMouseUp(p2, button)) return true;
        }
        return false;
    }

    bool HandleMouseWheel(float delta, const Point& mousePos) override {
        if (!m_enabled || !m_visible || !IsInside(mousePos)) return false;
        if (MaxOffset() <= 0.0f) return false;
        SetScrollOffset(m_scrollOffset - delta * m_wheelStep);
        return true;
    }

private:
    bool IsInside(const Point& p) const {
        return p.x >= m_bounds.left && p.x <= m_bounds.right &&
               p.y >= m_bounds.top && p.y <= m_bounds.bottom;
    }

    void ClampOffset() {
        float m = MaxOffset();
        if (m_scrollOffset < 0.0f) m_scrollOffset = 0.0f;
        if (m_scrollOffset > m) m_scrollOffset = m;
    }

    bool HasScrollbar() const { return MaxOffset() > 0.5f; }

    float TrackHeight() const {
        float h = m_bounds.Height() - 2.0f;
        return h > 1.0f ? h : 1.0f;
    }

    // 滑块几何（视口/内容比例，最小高度 24px）
    void ThumbGeom(float& y, float& h) const {
        float trackH = TrackHeight();
        float ratio = m_contentHeight > 0.0f ? ViewportHeight() / m_contentHeight : 1.0f;
        if (ratio > 1.0f) ratio = 1.0f;
        h = trackH * ratio;
        if (h < 24.0f) h = 24.0f;
        if (h > trackH) h = trackH;
        float span = MaxOffset() > 0.0f ? 1.0f : 0.0f;
        y = m_bounds.top + 1.0f +
            (trackH - h) * (span > 0.0f ? m_scrollOffset / MaxOffset() : 0.0f);
    }

    void ScrollToPos(float py) {
        float thumbY, thumbH;
        ThumbGeom(thumbY, thumbH);
        float trackH = TrackHeight();
        if (trackH - thumbH <= 1.0f) return;
        float t = (py - m_bounds.top - 1.0f - thumbH / 2.0f) / (trackH - thumbH);
        if (t < 0.0f) t = 0.0f;
        if (t > 1.0f) t = 1.0f;
        SetScrollOffset(t * MaxOffset());
    }

    void DrawScrollbar(Renderer* renderer) {
        if (!HasScrollbar()) return;
        Rect track(m_bounds.right - m_barWidth, m_bounds.top + 1.0f,
                   m_bounds.right - 1.0f, m_bounds.top + 1.0f + TrackHeight());
        renderer->FillRect(track, m_barTrack);
        float ty, th;
        ThumbGeom(ty, th);
        renderer->FillRect(Rect(track.left, ty, track.right, ty + th), m_barThumb);
    }

    float m_contentHeight;
    float m_scrollOffset;
    float m_barWidth;
    float m_wheelStep;
    Color m_backgroundColor;
    Color m_barTrack;
    Color m_barThumb;

    bool m_dragging;
    float m_dragStartY;
    float m_dragStartOffset;

    std::vector<std::unique_ptr<UIComponent>> m_children;
    ScrollCallback m_scrollCb;
};

}  // namespace gui
