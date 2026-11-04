/* ===========================================================================
 * virtual_list.h —— vgui v3 · 虚拟化列表（ 2）
 *
 * 设计：
 *   * 数据条数与可视项解耦：数据条数可上万，绘制只画可视窗口内的行；
 *   * 行文本两个来源（二选一，回调优先）：
 *       - 数据回调 OnData(fn)：绘制时逐行取（大数组跨 ABI 零拷贝，推荐）；
 *       - 内部数组 AddItem/SetItem：小数据量直用（与 ListBox 心智一致）；
 *   * 滚轮 + 自绘滚动条（与 ScrollPane 同一套几何）；点击行 → 选中回调；
 *   * m_drawCount 累计已绘制行数 —— 脚本侧用它自证虚拟化（绘制行数
 *     与数据行数解耦，只随可视区滚动量增长）。
 * =========================================================================== */
#pragma once
#include "ui_component.h"
#include "renderer.h"
#include <functional>
#include <vector>
#include <string>
#include <algorithm>

namespace gui {

class VirtualList : public UIComponent {
public:
    using DataFn = std::function<std::wstring(int row)>;
    using SelectionCallback = std::function<void(int index, const std::wstring& text)>;
    using ScrollCallback = std::function<void(float offset)>;

    VirtualList()
        : m_count(0)
        , m_rowHeight(26.0f)
        , m_scrollOffset(0.0f)
        , m_barWidth(8.0f)
        , m_fontSize(14.0f)
        , m_selected(-1)
        , m_hover(-1)
        , m_dragging(false)
        , m_dragStartY(0.0f)
        , m_dragStartOffset(0.0f)
        , m_drawCount(0)
        , m_backgroundColor(0.12f, 0.12f, 0.18f, 1.0f)
        , m_rowColor(0.16f, 0.16f, 0.24f, 1.0f)
        , m_selectedColor(0.2f, 0.6f, 1.0f, 0.7f)
        , m_hoverColor(0.25f, 0.25f, 0.35f, 0.8f)
        , m_textColor(0.95f, 0.95f, 1.0f, 1.0f)
        , m_barTrack(0.18f, 0.18f, 0.26f, 0.6f)
        , m_barThumb(0.42f, 0.45f, 0.58f, 0.8f) {}

    // ---- 数据（回调模式：万行级推荐；数组模式：AddItem 兼容直用）----
    // count 语义：SetCount 显式设定优先；未设定时回落到数组行数（AddItem 增长）
    void SetCount(int n) { m_count = n > 0 ? n : 0; ClampOffset(); }
    int GetCount() const { return m_count > 0 ? m_count : (int)m_items.size(); }
    void OnData(DataFn fn) { m_dataFn = std::move(fn); }
    void AddItem(const std::wstring& text) {
        m_items.push_back(text);
        m_count = (int)m_items.size();
        ClampOffset();
    }
    void SetItem(int row, const std::wstring& text) {
        if (row >= 0 && row < (int)m_items.size()) m_items[row] = text;
    }
    void ClearItems() { m_items.clear(); m_count = 0; m_selected = -1; ClampOffset(); }

    void SetRowHeight(float h) { m_rowHeight = h > 8.0f ? h : 8.0f; }
    void SetFontSize(float s) { m_fontSize = s; }
    void SetSelectedIndex(int idx) {
        int n = GetCount();
        if (idx >= -1 && idx < n) {
            m_selected = idx;
            if (m_selCb) m_selCb(idx, RowText(idx));
        }
    }
    int GetSelectedIndex() const { return m_selected; }
    float GetScrollOffset() const { return m_scrollOffset; }
    void SetScrollOffset(float v)
    {
        m_scrollOffset = v;
        ClampOffset();
        if (m_scrollCb) m_scrollCb(m_scrollOffset);   // 滚轮/拖拽/编程统一发 kind=16
    }
    void OnSelectionChanged(SelectionCallback cb) { m_selCb = std::move(cb); }
    void OnScroll(ScrollCallback cb) { m_scrollCb = std::move(cb); }

    long long DrawCount() const { return m_drawCount; }

    std::wstring RowText(int row) const {
        if (row < 0) return L"";
        if (m_dataFn) return m_dataFn(row);
        return (row >= 0 && row < (int)m_items.size()) ? m_items[row] : L"";
    }

    // ---- 绘制（只画可视行——虚拟化的本体）----
    void Draw(Renderer* renderer) override {
        if (!m_visible || !renderer) return;

        renderer->FillRect(m_bounds, m_backgroundColor);
        renderer->DrawRect(m_bounds, Color(0.25f, 0.25f, 0.35f, 1.0f), 1.0f);

        int n = GetCount();
        if (n > 0) {
            float contentH = (float)n * m_rowHeight;
            int first = (int)(m_scrollOffset / m_rowHeight);
            if (first < 0) first = 0;
            int viewRows = (int)(m_bounds.Height() / m_rowHeight) + 2;
            int last = first + viewRows;
            if (last > n) last = n;

            float contentRight = m_bounds.right - (MaxOffset() > 0.5f ? m_barWidth : 0.0f);

            renderer->PushClip(m_bounds);
            for (int i = first; i < last; ++i) {
                float y = m_bounds.top + (float)i * m_rowHeight - m_scrollOffset;
                Rect rowRect(m_bounds.left, y, contentRight, y + m_rowHeight);

                Color bg = (i == m_selected) ? m_selectedColor
                         : (i == m_hover)    ? m_hoverColor : m_rowColor;
                renderer->FillRect(rowRect, bg);

                Rect textRect = rowRect;
                textRect.left += 8;
                renderer->DrawText(RowText(i), textRect, m_textColor, m_fontSize, false, false);
                ++m_drawCount;
            }
            renderer->PopClip();

            if (MaxOffset() > 0.5f) DrawScrollbar(renderer);
        }
    }

    void Update(float) override {}

    // ---- 命中 ----
    bool HandleMouseDown(const Point& mousePos, int button) override {
        if (!m_enabled || !m_visible || button != 0) return false;
        if (!IsInside(mousePos)) return false;

        if (MaxOffset() > 0.5f && mousePos.x >= m_bounds.right - m_barWidth) {
            float ty, th;
            ThumbGeom(ty, th);
            m_dragging = true;
            m_dragStartY = mousePos.y;
            m_dragStartOffset = m_scrollOffset;
            if (mousePos.y < ty || mousePos.y > ty + th) ScrollToPos(mousePos.y);
            return true;
        }

        int idx = RowAt(mousePos);
        if (idx >= 0) {
            SetSelectedIndex(idx);   // 触发选中回调
            return true;
        }
        return true;   // 列表空白吃掉点击
    }

    bool HandleMouseMove(const Point& mousePos) override {
        if (!m_enabled || !m_visible) return false;
        if (m_dragging) {
            float ty, th;
            ThumbGeom(ty, th);
            float trackH = TrackHeight();
            float ratio = (trackH - th) > 1.0f ? MaxOffset() / (trackH - th) : 0.0f;
            SetScrollOffset(m_dragStartOffset + (mousePos.y - m_dragStartY) * ratio);
            return true;
        }
        if (!IsInside(mousePos)) { m_hover = -1; return false; }
        m_hover = RowAt(mousePos);
        return true;
    }

    bool HandleMouseUp(const Point& mousePos, int button) override {
        if (m_dragging) { m_dragging = false; return true; }
        return false;
    }

    bool HandleMouseWheel(float delta, const Point& mousePos) override {
        if (!m_enabled || !m_visible || !IsInside(mousePos)) return false;
        if (MaxOffset() <= 0.0f) return false;
        SetScrollOffset(m_scrollOffset - delta * m_rowHeight * 3.0f);
        return true;
    }

private:
    bool IsInside(const Point& p) const {
        return p.x >= m_bounds.left && p.x <= m_bounds.right &&
               p.y >= m_bounds.top && p.y <= m_bounds.bottom;
    }

    int RowAt(const Point& p) const {
        if (p.x < m_bounds.left || p.x > m_bounds.right) return -1;
        int idx = (int)((p.y - m_bounds.top + m_scrollOffset) / m_rowHeight);
        int n = GetCount();
        if (idx >= 0 && idx < n &&
            p.y >= m_bounds.top && p.y <= m_bounds.bottom) return idx;
        return -1;
    }

    float MaxOffset() const {
        float m = (float)GetCount() * m_rowHeight - m_bounds.Height();
        return m > 0.0f ? m : 0.0f;
    }

    void ClampOffset() {
        float m = MaxOffset();
        if (m_scrollOffset < 0.0f) m_scrollOffset = 0.0f;
        if (m_scrollOffset > m) m_scrollOffset = m;
    }

    float TrackHeight() const {
        float h = m_bounds.Height() - 2.0f;
        return h > 1.0f ? h : 1.0f;
    }

    void ThumbGeom(float& y, float& h) const {
        float trackH = TrackHeight();
        float total = (float)GetCount() * m_rowHeight;
        float ratio = total > 0.0f ? m_bounds.Height() / total : 1.0f;
        if (ratio > 1.0f) ratio = 1.0f;
        h = trackH * ratio;
        if (h < 24.0f) h = 24.0f;
        if (h > trackH) h = trackH;
        y = m_bounds.top + 1.0f +
            (trackH - h) * (MaxOffset() > 0.0f ? m_scrollOffset / MaxOffset() : 0.0f);
    }

    void ScrollToPos(float py) {
        float ty, th;
        ThumbGeom(ty, th);
        float trackH = TrackHeight();
        if (trackH - th <= 1.0f) return;
        float t = (py - m_bounds.top - 1.0f - th / 2.0f) / (trackH - th);
        if (t < 0.0f) t = 0.0f;
        if (t > 1.0f) t = 1.0f;
        SetScrollOffset(t * MaxOffset());
    }

    void DrawScrollbar(Renderer* renderer) {
        Rect track(m_bounds.right - m_barWidth, m_bounds.top + 1.0f,
                   m_bounds.right - 1.0f, m_bounds.top + 1.0f + TrackHeight());
        renderer->FillRect(track, m_barTrack);
        float ty, th;
        ThumbGeom(ty, th);
        renderer->FillRect(Rect(track.left, ty, track.right, ty + th), m_barThumb);
    }

    int m_count;
    float m_rowHeight;
    float m_scrollOffset;
    float m_barWidth;
    float m_fontSize;
    int m_selected;
    int m_hover;
    bool m_dragging;
    float m_dragStartY;
    float m_dragStartOffset;
    long long m_drawCount;

    std::vector<std::wstring> m_items;
    DataFn m_dataFn;
    SelectionCallback m_selCb;
    ScrollCallback m_scrollCb;

    Color m_backgroundColor;
    Color m_rowColor;
    Color m_selectedColor;
    Color m_hoverColor;
    Color m_textColor;
    Color m_barTrack;
    Color m_barThumb;
};

}  // namespace gui
