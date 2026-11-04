// ===========================================================================
// date_picker.h —— v6 配套：日期选择器（月历）
//
//   * 头部：< > 翻月 + 「YYYY年M月」标题；星期行（周日起始）+ 6×7 日格；
//   * 今天描边高亮、选中日填充、非本月灰显；
//   * 吃 v6 模板红利：bg part 渲染容器框，月历格是动态 items 保留代码绘制；
//   * 事件：QG_EVT_CHANGE（value = YYYYMMDD packed，如 20261103；
//     double 在 2^53 内精确，8 位日真值无歧义）。
// ===========================================================================
#pragma once
#include "ui_component.h"
#include "renderer.h"
#include "animation.h"
#include "visual_template.h"
#include <functional>
#include <algorithm>
#include <ctime>

namespace gui {

class DatePicker : public UIComponent {
public:
    using DateCallback = std::function<void(int y, int m, int d)>;

    DatePicker()
        : m_year(2026), m_month(11), m_day(3),
          m_viewYear(2026), m_viewMonth(11),
          m_bg(0.16f, 0.16f, 0.24f, 1.0f),
          m_headerColor(0.20f, 0.60f, 1.0f, 1.0f),
          m_textColor(0.90f, 0.90f, 0.95f, 1.0f),
          m_dimColor(0.45f, 0.45f, 0.55f, 1.0f),
          m_weekColor(0.60f, 0.60f, 0.72f, 1.0f),
          m_selectedColor(0.2f, 0.6f, 1.0f, 0.85f),
          m_gridColor(0.22f, 0.22f, 0.32f, 1.0f),
          m_borderColor(0.35f, 0.35f, 0.45f, 1.0f),
          m_hoverCell(-1), m_isHovered(false), m_fontSize(12.0f) {
        SYSTEMTIME st;
        GetLocalTime(&st);
        m_year = m_viewYear = st.wYear;
        m_month = m_viewMonth = st.wMonth;
        m_day = st.wDay;
        m_todayY = st.wYear; m_todayM = st.wMonth; m_todayD = st.wDay;
    }

    void GetDate(int* y, int* m, int* d) const {
        *y = m_year; *m = m_month; *d = m_day;
    }
    long long GetPacked() const {
        return (long long)m_year * 10000L + m_month * 100L + m_day;
    }
    void SetDate(int y, int m, int d) {
        Normalize(y, m);
        if (!IsValidDay(y, m, d)) return;
        m_year = y; m_month = m; m_day = d;
        m_viewYear = y; m_viewMonth = m;
        if (m_callback) m_callback(m_year, m_month, m_day);
    }
    void OnDateChanged(DateCallback cb) { m_callback = cb; }
    void SetFontSize(float s) { m_fontSize = s; }

    void Draw(Renderer* renderer) override {
        if (!m_visible || !renderer) return;
        if (m_paintFn) {
            m_paintFn(renderer, m_bounds.Width(), m_bounds.Height(), StyleFlags());
            return;
        }
        // v6：声明式视觉模板（容器框；月历格动态 items 保留代码绘制）
        if (m_tplId) {
            RenderTemplate(this, renderer, StyleFlags());
        } else {
            renderer->FillRect(m_bounds, m_bg);
            renderer->DrawRect(m_bounds, m_borderColor, 1.0f);
        }

        float pad = 8.0f;
        // ---- 头部：< 标题 > ----
        Rect headRect(m_bounds.left + pad, m_bounds.top + pad,
                      m_bounds.right - pad, m_bounds.top + pad + 26.0f);
        wchar_t title[32];
        swprintf_s(title, L"%d年%d月", m_viewYear, m_viewMonth);
        Rect titleRect = headRect;
        titleRect.left += 30.0f; titleRect.right -= 30.0f;
        renderer->DrawTextAlignedEx(title, titleRect, m_headerColor,
                                    m_fontSize + 2.0f, true, 1, 1, false, true);
        // 翻月箭头（< 左  > 右）
        float midY = (headRect.top + headRect.bottom) / 2.0f;
        float lx = headRect.left + 9.0f, rx = headRect.right - 9.0f;
        renderer->DrawLine(Point(lx + 4, midY - 5), Point(lx - 3, midY), m_textColor, 1.6f);
        renderer->DrawLine(Point(lx - 3, midY), Point(lx + 4, midY + 5), m_textColor, 1.6f);
        renderer->DrawLine(Point(rx - 4, midY - 5), Point(rx + 3, midY), m_textColor, 1.6f);
        renderer->DrawLine(Point(rx + 3, midY), Point(rx - 4, midY + 5), m_textColor, 1.6f);
        m_prevHit = Rect(headRect.left, headRect.top, headRect.left + 30.0f, headRect.bottom);
        m_nextHit = Rect(headRect.right - 30.0f, headRect.top, headRect.right, headRect.bottom);

        // ---- 星期行 ----
        float gridTop = headRect.bottom + 4.0f;
        static const wchar_t* kWeek[7] = {L"日", L"一", L"二", L"三", L"四", L"五", L"六"};
        float cw = (m_bounds.Width() - pad * 2.0f) / 7.0f;
        for (int i = 0; i < 7; ++i) {
            Rect c(m_bounds.left + pad + i * cw, gridTop,
                   m_bounds.left + pad + (i + 1) * cw, gridTop + 18.0f);
            renderer->DrawTextAlignedEx(kWeek[i], c, m_weekColor,
                                        m_fontSize, false, 1, 1, false, true);
        }
        gridTop += 18.0f;

        // ---- 6×7 日格 ----
        int firstWeekday = WeekdayOf(m_viewYear, m_viewMonth, 1);
        int dim = DaysInMonth(m_viewYear, m_viewMonth);
        int prevDim = DaysInMonth(m_viewYear, m_viewMonth == 1 ? 12 : m_viewMonth - 1);
        float ch = (m_bounds.bottom - pad - gridTop) / 6.0f;
        for (int cell = 0; cell < 42; ++cell) {
            int row = cell / 7, col = cell % 7;
            int d, m2, y2;
            bool dimmed = false;
            if (cell < firstWeekday) {
                d = prevDim - firstWeekday + 1 + cell;
                m2 = m_viewMonth == 1 ? 12 : m_viewMonth - 1;
                y2 = m_viewMonth == 1 ? m_viewYear - 1 : m_viewYear;
                dimmed = true;
            } else if (cell < firstWeekday + dim) {
                d = cell - firstWeekday + 1;
                m2 = m_viewMonth; y2 = m_viewYear;
            } else {
                d = cell - firstWeekday - dim + 1;
                m2 = m_viewMonth == 12 ? 1 : m_viewMonth + 1;
                y2 = m_viewMonth == 12 ? m_viewYear + 1 : m_viewYear;
                dimmed = true;
            }
            Rect c(m_bounds.left + pad + col * cw, gridTop + row * ch,
                   m_bounds.left + pad + (col + 1) * cw, gridTop + (row + 1) * ch);
            if (cell == m_hoverCell && !dimmed) {
                renderer->FillRoundedRect(RoundedRect(Inflate(c, 2.0f), 4.0f, 4.0f),
                                          Color(1, 1, 1, 0.07f));
            }
            Color tc = dimmed ? m_dimColor : m_textColor;
            bool selected = (y2 == m_year && m2 == m_month && d == m_day);
            if (selected) {
                renderer->FillEllipse(Ellipse((c.left + c.right) / 2.0f,
                                              (c.top + c.bottom) / 2.0f,
                                              std::min(c.Width(), c.Height()) * 0.42f),
                                      m_selectedColor);
                tc = Color(1, 1, 1, 1);
            } else if (y2 == m_todayY && m2 == m_todayM && d == m_todayD) {
                renderer->DrawEllipse(Ellipse((c.left + c.right) / 2.0f,
                                              (c.top + c.bottom) / 2.0f,
                                              std::min(c.Width(), c.Height()) * 0.42f),
                                      m_headerColor, 1.2f);
            }
            wchar_t num[8];
            swprintf_s(num, L"%d", d);
            renderer->DrawTextAlignedEx(num, c, tc, m_fontSize, false, 1, 1, false, true);
            (void)m_gridColor;
        }
        m_gridTop = gridTop;
        m_cellW = cw;
        m_cellH = ch;
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
        m_hoverCell = CellAt(mousePos);
        return m_hoverCell >= 0;
    }

    bool HandleMouseDown(const Point& mousePos, int button) override {
        if (!m_enabled || !m_visible || button != 0) return false;
        if (mousePos.x < m_bounds.left || mousePos.x > m_bounds.right ||
            mousePos.y < m_bounds.top || mousePos.y > m_bounds.bottom) return false;
        if (Contains(m_prevHit, mousePos)) { ShiftMonth(-1); return true; }
        if (Contains(m_nextHit, mousePos)) { ShiftMonth(1); return true; }
        int cell = CellAt(mousePos);
        if (cell >= 0) {
            int y, m2, d;
            CellToDate(cell, y, m2, d);
            SetDate(y, m2, d);
            return true;
        }
        return true;
    }

private:
    static Rect Inflate(const Rect& r, float n) {
        return Rect(r.left - n, r.top - n, r.right + n, r.bottom + n);
    }
    static bool Contains(const Rect& rc, const Point& p) {
        return p.x >= rc.left && p.x <= rc.right && p.y >= rc.top && p.y <= rc.bottom;
    }
    static void Normalize(int& y, int& m) {
        if (m < 1) { m = 12; --y; }
        if (m > 12) { m = 1; ++y; }
        if (y < 1) y = 1;
    }
    static bool IsLeap(int y) {
        return (y % 4 == 0 && y % 100 != 0) || y % 400 == 0;
    }
    static int DaysInMonth(int y, int m) {
        static const int kDays[12] = {31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31};
        if (m == 2 && IsLeap(y)) return 29;
        return kDays[m - 1];
    }
    // 周几（0=周日）：蔡勒/基姆拉尔森式，1..m
    static int WeekdayOf(int y, int m, int d) {
        // 2000-01-01 是周六（6）；以天差推算
        std::tm t = {};
        t.tm_year = y - 1900;
        t.tm_mon = m - 1;
        t.tm_mday = d;
        std::mktime(&t);    // 归一化并填 tm_wday
        return t.tm_wday;   // 0=周日 ... 6=周六
    }
    static bool IsValidDay(int y, int m, int d) {
        if (y < 1 || y > 9999 || m < 1 || m > 12) return false;
        return d >= 1 && d <= DaysInMonth(y, m);
    }
    void ShiftMonth(int delta) {
        m_viewMonth += delta;
        Normalize(m_viewYear, m_viewMonth);
    }
    int CellAt(const Point& p) const {
        if (m_cellH <= 0.0f) return -1;
        float pad = 8.0f;
        if (p.x < m_bounds.left + pad || p.x > m_bounds.right - pad) return -1;
        if (p.y < m_gridTop || p.y > m_bounds.bottom - pad) return -1;
        int col = (int)((p.x - m_bounds.left - pad) / m_cellW);
        int row = (int)((p.y - m_gridTop) / m_cellH);
        if (col < 0 || col > 6 || row < 0 || row > 5) return -1;
        return row * 7 + col;
    }
    void CellToDate(int cell, int& y, int& m2, int& d) const {
        int firstWeekday = WeekdayOf(m_viewYear, m_viewMonth, 1);
        int dim = DaysInMonth(m_viewYear, m_viewMonth);
        int prevDim = DaysInMonth(m_viewYear, m_viewMonth == 1 ? 12 : m_viewMonth - 1);
        if (cell < firstWeekday) {
            d = prevDim - firstWeekday + 1 + cell;
            m2 = m_viewMonth == 1 ? 12 : m_viewMonth - 1;
            y = m_viewMonth == 1 ? m_viewYear - 1 : m_viewYear;
        } else if (cell < firstWeekday + dim) {
            d = cell - firstWeekday + 1;
            m2 = m_viewMonth; y = m_viewYear;
        } else {
            d = cell - firstWeekday - dim + 1;
            m2 = m_viewMonth == 12 ? 1 : m_viewMonth + 1;
            y = m_viewMonth == 12 ? m_viewYear + 1 : m_viewYear;
        }
    }

    int m_year, m_month, m_day;          // 选中日期
    int m_viewYear, m_viewMonth;         // 正在查看的月
    int m_todayY, m_todayM, m_todayD;    // 今天（描边高亮）
    Color m_bg, m_headerColor, m_textColor, m_dimColor, m_weekColor;
    Color m_selectedColor, m_gridColor, m_borderColor;
    int m_hoverCell;
    bool m_isHovered;
    float m_fontSize;
    // 命中缓存（Draw 时更新；同消息线程模型，先画后点）
    Rect m_prevHit, m_nextHit;
    float m_gridTop = 0.0f, m_cellW = 0.0f, m_cellH = 0.0f;
    DateCallback m_callback;
};

}  // namespace gui
