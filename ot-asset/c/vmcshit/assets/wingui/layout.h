/* ===========================================================================
 * layout.h —— vgui v3 · 布局引擎（方案 A：布局管理器，不引入容器组件）
 *
 *   * 脚本层 vg_layout_begin/add/.../end 声明布局；窗口 OnResize 时统一重排；
 *   * 组件全部留在 ModernWindow::m_components（绘制/命中测试遍历零改动，
 *     z 序语义 = 现状）；布局只改写组件 bounds —— 绝对坐标与布局共存；
 *   * Anchor（layout_theme.h 的 ApplyAnchor）并入本引擎：布局模式激活时
 *     布局全权；Absolute 模式下 resize 走 ApplyAnchor —— 两套合一，不留双轨。
 * 自然尺寸纪律：
 *   * vg_layout_add 时快照控件 bounds 为**自然尺寸**，此后所有轮重排
 *     （含 resize）都以快照为基准 —— 布局结果写回 bounds，绝不把上一轮
 *     拉伸结果当自然尺寸（否则多轮布局互相复合，越拉越歪）。
 * 纪律：
 *   * header-only（不新增编译单元，qot_cache.gui_objects 无需改）；
 *   * 只依赖 core_types / ui_component，不反向依赖窗口（避免循环包含）。
 * =========================================================================== */
#pragma once
#include "core_types.h"
#include "ui_component.h"
#include <vector>
#include <string>
#include <unordered_map>
#include <cmath>

namespace gui {

// 四边 inset（与 layout_theme.h 的 Margin 语义一致；本头不 include
// layout_theme.h —— 它引用 ModernWindow，会与 modern_window.h 循环包含）
struct LayoutEdges {
    float left = 0, top = 0, right = 0, bottom = 0;
};

// 单控件布局属性（vg_layout_props 的 C++ 侧宿主）
struct LayoutItemProps {
    LayoutEdges margin;     // 外边距（主轴前进量含 margin，交叉轴定位含 margin）
    int fill = 0;           // 0 自然尺寸 1 交叉轴拉伸 2 主轴+交叉轴全拉伸
    int align = 0;          // 交叉轴对齐：0 起 1 中 2 末（fill=1/2 时忽略）
    float weight = 0.0f;    // 主轴伸展权重（>0 参与剩余空间按比例分配）
};

// 布局条目：控件 id + 登记时的自然尺寸快照
struct LayoutEntry {
    int id = 0;
    float natW = 0.0f;
    float natH = 0.0f;
};

class LayoutManager {
public:
    // 布局模式：0 absolute（关闭）1 vbox 2 hbox 3 grid
    int mode = 0;
    LayoutEdges padding;
    float spacing = 8.0f;
    int cols = 2;           // 网格列数

    // 显式条目表（vg_layout_add 顺序即布局顺序；重复添加忽略）
    std::vector<LayoutEntry> items;
    std::unordered_map<int, LayoutItemProps> props;   // 控件 id → 属性
    // 自然尺寸永久表：控件生命周期内**首次**登记时快照，Reset 不清——
    // 布局会改写 bounds，重复 begin/end 时若重取当前尺寸，拉伸结果会
    // 被当成自然尺寸滚雪球（实测踩过：hbox 段 b1 自然宽 = 上轮 680）。
    std::unordered_map<int, LayoutEntry> natural;

    void Reset(int newMode)
    {
        mode = newMode;
        items.clear();
        props.clear();   // 跨模式重开时控件属性一并失效（防残留串场）
        padding = LayoutEdges();
        spacing = 8.0f;
        cols = 2;
        // 注意：natural 故意不清（见上）
    }

    // 登记条目；自然尺寸取首次快照（natW/natH = 调用方传入的当前 bounds 尺寸）
    void AddItem(int id, float natW, float natH)
    {
        for (const LayoutEntry &e : items)
            if (e.id == id) return;
        auto held = natural.find(id);
        if (held == natural.end()) {
            LayoutEntry first;
            first.id = id;
            first.natW = natW > 0.0f ? natW : 0.0f;
            first.natH = natH > 0.0f ? natH : 0.0f;
            natural[id] = first;
            held = natural.find(id);
        }
        items.push_back(held->second);
    }

    LayoutItemProps &PropsOf(int id) { return props[id]; }

    // 按当前客户区尺寸重排。comps 为窗口组件表（顺序即 z 序，布局不改顺序）。
    void Relayout(std::vector<std::unique_ptr<UIComponent>> &comps, const Size &client)
    {
        if (mode == 0 || items.empty()) return;

        // id → 组件指针（未登记在窗口里的条目跳过：已销毁/未创建）
        m_byId.clear();
        for (auto &c : comps)
            if (c) m_byId[c->GetId()] = c.get();

        Rect content(padding.left, padding.top,
                     client.width - padding.right, client.height - padding.bottom);
        if (content.right - content.left < 1.0f || content.bottom - content.top < 1.0f)
            return;

        // 收集有效条目（组件存在才排版；隐藏控件占位不参与——脚本用
        // vg_set_visible + 重新 begin/end 自行决定，行为显式可预期）
        std::vector<const LayoutEntry *> list;
        list.reserve(items.size());
        for (const LayoutEntry &e : items) {
            auto it = m_byId.find(e.id);
            if (it != m_byId.end() && it->second) list.push_back(&e);
        }
        if (list.empty()) return;

        switch (mode) {
            case 1: LayoutLinear(list, content, true);  break;   // vbox
            case 2: LayoutLinear(list, content, false); break;   // hbox
            case 3: LayoutGrid(list, content);          break;   // grid
            default: break;
        }
    }

private:
    float WeightOf(const LayoutEntry *e)
    {
        const LayoutItemProps &p = props[e->id];
        if (p.weight > 0.0f) return p.weight;
        if (p.fill == 2) return 1.0f;
        return 0.0f;
    }

    // vbox/hbox：线性主轴排布（weight 分配剩余主轴空间，fill 拉伸交叉轴）
    void LayoutLinear(std::vector<const LayoutEntry *> &list, const Rect &content,
                      bool vertical)
    {
        const size_t n = list.size();
        const float contentMain = vertical ? (content.bottom - content.top)
                                           : (content.right - content.left);
        const float contentCross = vertical ? (content.right - content.left)
                                            : (content.bottom - content.top);

        // 固定条目占用的主轴长度（自然尺寸快照 + margin）+ 权重合计
        float fixedMain = spacing * (float)(n > 0 ? n - 1 : 0);
        float totalWeight = 0.0f;
        for (const LayoutEntry *e : list) {
            const LayoutItemProps &p = props[e->id];
            const float nat = vertical ? e->natH : e->natW;
            const float m = vertical ? (p.margin.top + p.margin.bottom)
                                     : (p.margin.left + p.margin.right);
            const float w = WeightOf(e);
            totalWeight += w;
            if (w <= 0.0f) fixedMain += nat + m;
        }

        float pool = contentMain - fixedMain;
        if (pool < 0.0f) pool = 0.0f;

        float pos = vertical ? content.top : content.left;
        for (const LayoutEntry *e : list) {
            UIComponent *c = byIdSafe(list, e);
            if (!c) continue;
            const LayoutItemProps &p = props[e->id];
            const float w = WeightOf(e);

            const float natMain = vertical ? e->natH : e->natW;
            const float main = w > 0.0f ? pool * w / totalWeight : natMain;

            const float crossNat = vertical ? e->natW : e->natH;
            float cross;
            if (p.fill >= 1)
                cross = contentCross - (vertical ? p.margin.left + p.margin.right
                                                 : p.margin.top + p.margin.bottom);
            else
                cross = crossNat;

            float crossPos;
            if (p.fill >= 1 || p.align == 0)
                crossPos = (vertical ? content.left + p.margin.left
                                     : content.top + p.margin.top);
            else if (p.align == 1)
                crossPos = (vertical ? content.left : content.top) +
                           (contentCross - cross) / 2.0f;
            else
                crossPos = (vertical ? content.right - p.margin.right - cross
                                     : content.bottom - p.margin.bottom - cross);

            Rect r = vertical
                ? Rect(crossPos, pos + p.margin.top,
                       crossPos + cross, pos + p.margin.top + main)
                : Rect(pos + p.margin.left, crossPos,
                       pos + p.margin.left + main, crossPos + cross);
            c->SetBounds(r);
            pos += (vertical ? p.margin.top + main + p.margin.bottom
                             : p.margin.left + main + p.margin.right) + spacing;
        }
    }

    // grid：cols 列，行高 = 行内最大自然高；fill=1 拉宽 2 拉满格
    // （content 按值：行推进游标就地改写）
    void LayoutGrid(std::vector<const LayoutEntry *> &list, Rect content)
    {
        const int c = cols > 0 ? cols : 1;
        const size_t n = list.size();
        const int rows = (int)((n + (size_t)c - 1) / (size_t)c);
        const float contentW = content.right - content.left;
        const float cw = (contentW - spacing * (float)(c - 1)) / (float)c;
        if (cw < 1.0f) return;

        for (int r0 = 0; r0 < rows; ++r0) {
            // 行高：行内条目（自然高快照 + margin）最大值
            float rh = 0.0f;
            for (int cc = 0; cc < c; ++cc) {
                size_t i = (size_t)(r0 * c + cc);
                if (i >= n) break;
                const LayoutEntry *e = list[i];
                const LayoutItemProps &p = props[e->id];
                float h = e->natH + p.margin.top + p.margin.bottom;
                if (h > rh) rh = h;
            }

            for (int cc = 0; cc < c; ++cc) {
                size_t i = (size_t)(r0 * c + cc);
                if (i >= n) break;
                const LayoutEntry *e = list[i];
                UIComponent *comp = byIdSafe(list, e);
                if (!comp) continue;
                const LayoutItemProps &p = props[e->id];

                float w = (p.fill >= 1) ? cw - p.margin.left - p.margin.right : e->natW;
                if (w < 0.0f) w = 0.0f;
                float h = (p.fill == 2 || p.weight > 0.0f)
                        ? rh - p.margin.top - p.margin.bottom : e->natH;
                if (h < 0.0f) h = 0.0f;

                float cellL = content.left + (float)cc * (cw + spacing);
                float x = (p.fill >= 1 || p.align == 0) ? cellL + p.margin.left
                        : (p.align == 1) ? cellL + (cw - w) / 2.0f
                                         : cellL + cw - p.margin.right - w;
                float y = (p.fill == 2 || p.weight > 0.0f || p.align == 0)
                        ? content.top + p.margin.top
                        : (p.align == 1) ? content.top + (rh - h) / 2.0f
                                         : content.top + rh - p.margin.bottom - h;
                comp->SetBounds(Rect(x, y, x + w, y + h));
            }
            content.top += rh + spacing;   // 行推进（content 按值语义使用）
        }
    }

    // 条目 → 组件（Relayout 建的 byId 表；这里用成员副本简化签名）
    std::unordered_map<int, UIComponent *> m_byId;
    UIComponent *byIdSafe(std::vector<const LayoutEntry *> &list, const LayoutEntry *e)
    {
        (void)list;
        auto it = m_byId.find(e->id);
        return it == m_byId.end() ? nullptr : it->second;
    }
};

}  // namespace gui
