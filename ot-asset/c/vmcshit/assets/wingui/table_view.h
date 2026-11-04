// ===========================================================================
// table_view.h —— v6 配套：表格控件（DataGridView 类似物）
//
//   * 列定义（标题+宽度；width<=0 均分剩余空间）+ 行数据（逐单元格字符串）；
//   * 单击行选中 → OnSelection(row, col)；hover 行高亮；滚轮滚动；
//   * 吃 v6 模板红利：bg part 渲染容器框（RenderTemplate），表头/网格/行
//     文本是动态 items 结构（ItemsPresenter 范畴）保留代码绘制；
//   * 事件：QG_EVT_SELECT（value=行索引，x=列索引）。
// ===========================================================================
#pragma once
#include "ui_component.h"
#include "renderer.h"
#include "animation.h"
#include "visual_template.h"
#include <functional>
#include <vector>
#include <string>

namespace gui {

class TableView : public UIComponent {
public:
    using CellCallback = std::function<void(int row, int col)>;

    TableView()
        : m_headerH(30.0f), m_rowH(26.0f),
          m_bg(0.12f, 0.12f, 0.18f, 1.0f),
          m_headerBg(0.16f, 0.16f, 0.24f, 1.0f),
          m_gridColor(0.22f, 0.22f, 0.32f, 1.0f),
          m_textColor(0.90f, 0.90f, 0.95f, 1.0f),
          m_headerTextColor(0.65f, 0.65f, 0.78f, 1.0f),
          m_selectedColor(0.2f, 0.6f, 1.0f, 0.35f),
          m_hoverColor(0.25f, 0.25f, 0.35f, 0.6f),
          m_selectedRow(-1), m_selectedCol(-1), m_hoverRow(-1),
          m_scrollOffset(0.0f), m_fontSize(13.0f), m_isHovered(false) {}

    // ---- 列与数据 ----
    void AddColumn(const std::wstring& title, float width) {
        Column c; c.title = title; c.width = width;
        m_cols.push_back(c);
    }
    void ClearColumns() { m_cols.clear(); }
    void AddRow(const std::vector<std::wstring>& cells) {
        m_rows.push_back(cells);
        m_rows.back().resize(std::max(m_cols.size(), m_rows.back().size()));
    }
    void ClearRows() { m_rows.clear(); m_selectedRow = -1; m_selectedCol = -1; m_scrollOffset = 0; }
    int GetRowCount() const { return (int)m_rows.size(); }
    int GetColumnCount() const { return (int)m_cols.size(); }
    void SetCell(int row, int col, const std::wstring& text) {
        if (row < 0 || row >= (int)m_rows.size()) return;
        if ((size_t)col >= m_rows[row].size()) m_rows[row].resize(col + 1);
        m_rows[row][col] = text;
    }
    void SetHeaderHeight(float h) { m_headerH = h; }
    void SetRowHeight(float h) { m_rowH = h; }
    void SetFontSize(float s) { m_fontSize = s; }
    void SetBgColor(const Color& c) { m_bg = c; }
    void SetHeaderBgColor(const Color& c) { m_headerBg = c; }
    void SetTextColor(const Color& c) { m_textColor = c; }
    void SetSelectedColor(const Color& c) { m_selectedColor = c; }

    int GetSelectedRow() const { return m_selectedRow; }
    int GetSelectedColumn() const { return m_selectedCol; }
    void SetSelected(int row, int col) {
        if (row >= 0 && (row >= (int)m_rows.size() || col >= (int)m_cols.size()))
            return;   // 越界选中静默忽略（宿主程序化设置防呆）
        m_selectedRow = row;
        m_selectedCol = col;
        if (m_callback) m_callback(row, col);
    }
    void OnSelection(CellCallback cb) { m_callback = cb; }

    void Draw(Renderer* renderer) override {
        if (!m_visible || !renderer) return;
        if (m_paintFn) {
            m_paintFn(renderer, m_bounds.Width(), m_bounds.Height(), StyleFlags());
            return;
        }
        // v6：声明式视觉模板（容器框；表头/网格/行文本动态结构保留代码绘制）
        if (m_tplId) {
            RenderTemplate(this, renderer, StyleFlags());
        } else {
            renderer->FillRect(m_bounds, m_bg);
            renderer->DrawRect(m_bounds, m_gridColor, 1.0f);
        }

        float contentW = m_bounds.Width();
        // 列宽解析（width<=0 均分剩余）
        float fixedW = 0.0f; int flexCount = 0;
        for (auto& c : m_cols) { if (c.width > 0.0f) fixedW += c.width; else ++flexCount; }
        float flexW = flexCount > 0 ? std::max(0.0f, contentW - fixedW) / flexCount : 0.0f;
        std::vector<float> colX(m_cols.size() + 1);
        colX[0] = 0.0f;
        for (size_t i = 0; i < m_cols.size(); ++i) {
            float cw = (m_cols[i].width > 0.0f) ? m_cols[i].width : flexW;
            colX[i + 1] = colX[i] + cw;
        }

        // 表头
        Rect headerRect(m_bounds.left, m_bounds.top, m_bounds.right, m_bounds.top + m_headerH);
        renderer->FillRect(headerRect, m_headerBg);
        for (size_t i = 0; i < m_cols.size(); ++i) {
            float cx0 = m_bounds.left + colX[i];
            float cx1 = m_bounds.left + colX[i + 1];
            Rect cell(cx0, headerRect.top, cx1, headerRect.bottom);
            renderer->DrawLine(Point(cx0, headerRect.top), Point(cx0, headerRect.bottom), m_gridColor, 1.0f);
            Rect tr = cell; tr.left += 8; tr.right -= 4;
            renderer->DrawTextAlignedEx(m_cols[i].title, tr, m_headerTextColor,
                                        m_fontSize, true, 0, 1, false, true);
        }
        renderer->DrawLine(Point(m_bounds.left, headerRect.bottom),
                           Point(m_bounds.right, headerRect.bottom), m_gridColor, 1.0f);

        // 行区（裁剪 + 滚动）
        Rect rowsRect(m_bounds.left, headerRect.bottom, m_bounds.right, m_bounds.bottom);
        float maxScroll = std::max(0.0f, m_rows.size() * m_rowH - rowsRect.Height());
        m_scrollOffset = std::max(0.0f, std::min(maxScroll, m_scrollOffset));

        renderer->PushClip(rowsRect);
        int first = (int)(m_scrollOffset / m_rowH);
        int visible = (int)(rowsRect.Height() / m_rowH) + 1;
        for (int r = first; r < (int)m_rows.size() && r < first + visible; ++r) {
            float y0 = rowsRect.top + r * m_rowH - m_scrollOffset;
            float y1 = y0 + m_rowH;
            if (r == m_selectedRow) {
                renderer->FillRect(Rect(rowsRect.left, y0, rowsRect.right, y1), m_selectedColor);
            } else if (r == m_hoverRow) {
                renderer->FillRect(Rect(rowsRect.left, y0, rowsRect.right, y1), m_hoverColor);
            }
            size_t ncol = m_rows[r].size();
            for (size_t c = 0; c < m_cols.size(); ++c) {
                float cx0 = m_bounds.left + colX[c];
                float cx1 = m_bounds.left + colX[c + 1];
                if (c > 0) renderer->DrawLine(Point(cx0, y0), Point(cx0, y1), m_gridColor, 1.0f);
                if (c >= ncol) continue;
                Rect tr(cx0 + 8, y0, cx1 - 4, y1);
                renderer->DrawTextAlignedEx(m_rows[r][c], tr, m_textColor,
                                            m_fontSize, false, 0, 1, false, true);
            }
        }
        renderer->PopClip();
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
        m_hoverRow = -1;
        if (inside) {
            float rowsTop = m_bounds.top + m_headerH;
            if (mousePos.y >= rowsTop && mousePos.y <= m_bounds.bottom) {
                int r = (int)((mousePos.y - rowsTop + m_scrollOffset) / m_rowH);
                if (r >= 0 && r < (int)m_rows.size()) m_hoverRow = r;
            }
        }
        return m_hoverRow >= 0;
    }

    bool HandleMouseDown(const Point& mousePos, int button) override {
        if (!m_enabled || !m_visible || button != 0) return false;
        if (mousePos.x < m_bounds.left || mousePos.x > m_bounds.right ||
            mousePos.y < m_bounds.top || mousePos.y > m_bounds.bottom) return false;
        if (mousePos.y <= m_bounds.top + m_headerH) return true;   // 表头区：吃掉但不选中
        float rowsTop = m_bounds.top + m_headerH;
        int r = (int)((mousePos.y - rowsTop + m_scrollOffset) / m_rowH);
        if (r < 0 || r >= (int)m_rows.size()) return true;
        // 列命中（与绘制同宽度解析）
        float fixedW = 0.0f; int flexCount = 0;
        for (auto& c : m_cols) { if (c.width > 0.0f) fixedW += c.width; else ++flexCount; }
        float flexW = flexCount > 0 ? std::max(0.0f, m_bounds.Width() - fixedW) / flexCount : 0.0f;
        float rel = mousePos.x - m_bounds.left;
        int col = -1; float acc = 0.0f;
        for (size_t i = 0; i < m_cols.size(); ++i) {
            float cw = (m_cols[i].width > 0.0f) ? m_cols[i].width : flexW;
            if (rel >= acc && rel < acc + cw) { col = (int)i; break; }
            acc += cw;
        }
        SetSelected(r, col);
        return true;
    }

    bool HandleMouseWheel(float delta, const Point& mousePos) override {
        if (!m_enabled || !m_visible) return false;
        if (mousePos.x < m_bounds.left || mousePos.x > m_bounds.right ||
            mousePos.y < m_bounds.top || mousePos.y > m_bounds.bottom) return false;
        float maxScroll = std::max(0.0f, m_rows.size() * m_rowH -
                                       (m_bounds.Height() - m_headerH));
        m_scrollOffset -= delta * 30.0f;
        m_scrollOffset = std::max(0.0f, std::min(maxScroll, m_scrollOffset));
        return true;
    }

private:
    struct Column { std::wstring title; float width; };
    std::vector<Column> m_cols;
    std::vector<std::vector<std::wstring>> m_rows;
    float m_headerH, m_rowH;
    Color m_bg, m_headerBg, m_gridColor, m_textColor, m_headerTextColor;
    Color m_selectedColor, m_hoverColor;
    int m_selectedRow, m_selectedCol, m_hoverRow;
    float m_scrollOffset;
    float m_fontSize;
    bool m_isHovered;
    CellCallback m_callback;
};

}  // namespace gui
