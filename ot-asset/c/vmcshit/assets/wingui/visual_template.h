// ===========================================================================
// visual_template.h —— v6：声明式视觉模板（ControlTemplate + VisualState +
// 声明式 Transition）
//
// 定位：控件呈现 = 数据驱动的部件模板。控件默认绘制只是一份默认模板实例；
// on_paint 代码通道与模板数据通道长期并存（双定制通道）。
//
//   * TplPart        —— 命名部件：形状（rect/round/ellipse/path/fixed 系列）
//                       × 填充（色/画刷/双色渐变）× 描边 × inset × 文字槽
//                       × 值驱动几何（宽/位移/缩放/透明度/高/色lerp）；
//   * TplOverride    —— 状态覆盖：state_mask 位（1 hover | 2 pressed | 4
//                       disabled | 8 focused | 16 checked | 32 open）全命中
//                       生效，属性 = 填充/描边/平移/缩放/旋转/透明度/圆角；
//   * TplTransition  —— 状态切换自动插值（CSS transition 原语化；12 缓动）；
//   * RenderTemplate —— 渲染主体：按 part 顺序 状态插值变换 → 几何
//                       fill/stroke → 文字槽；过渡期间 requestFrame 续帧，
//                       结束自然停帧。
//
// 模板跨控件共享（句柄化）；动画状态挂控件实例（每 part 一项）。
// 模板/文字样式表是纯数据（设备无关）；画刷/几何句柄经 TplEnv 由 ABI 层
// 解析（画刷挂渲染目标设备，失效时容忍跳过——坑位预警①的既定策略）。
//
// header-only：全 inline（单一定义），依赖单向 core_types → ui_component
// → renderer → animation。
// ===========================================================================
#pragma once
#include "core_types.h"
#include "ui_component.h"
#include "renderer.h"
#include "animation.h"
#include <unordered_map>
#include <vector>
#include <string>
#include <chrono>
#include <cmath>

namespace gui {

// ---- v6 状态位（qg_style_flags 高位追加——兼容铁律：不动 v2 已发布位序） ----
constexpr unsigned kTplFlagHover    = 0x1u;
constexpr unsigned kTplFlagPressed  = 0x2u;
constexpr unsigned kTplFlagDisabled = 0x4u;
constexpr unsigned kTplFlagFocused  = 0x8u;
constexpr unsigned kTplFlagChecked  = 0x10u;   // checkbox/toggle 勾选态
constexpr unsigned kTplFlagOpen     = 0x20u;   // combobox 下拉展开态

// ---- 默认模板类型（qg_tpl_default 的 type 参数） ----
constexpr int kTplDefButton     = 0;
constexpr int kTplDefCheckbox   = 1;
constexpr int kTplDefToggle     = 2;
constexpr int kTplDefSlider     = 3;
constexpr int kTplDefProgress   = 4;
constexpr int kTplDefComboBox   = 5;
constexpr int kTplDefTabControl = 6;

// ---------------------------------------------------------------------------
// 文字样式块（qg_textstyle_create 的表条目；纯参数，渲染时经 Renderer
// GetTextFormatEx 落成 IDWriteTextFormat——挂 DWrite 工厂，设备无关）
// ---------------------------------------------------------------------------
struct TplTsParams {
    float size = 14.0f;
    int weight = 400;        // DWRITE 字重 100..900
    bool italic = false;
    int halign = 1;          // 0 起 1 中 2 末
    int valign = 1;
    bool wrap = false;
    std::wstring family;     // 空 = 当前默认族
};

// ---------------------------------------------------------------------------
// ABI 层注入的句柄解析环境（qg_init 时绑定；单消息线程模型，无锁）
// ---------------------------------------------------------------------------
struct TplEnv {
    ID2D1Brush* (*brushOf)(int) = nullptr;              // 画刷句柄 → 画刷（失效返回 null）
    ID2D1PathGeometry* (*geoOf)(int) = nullptr;         // 几何句柄 → 几何
    int (*geoStore)(ID2D1PathGeometry*) = nullptr;      // 持走引用 → 新几何句柄
    const TplTsParams* (*tsOf)(int) = nullptr;          // 文字样式句柄 → 参数
    void (*requestFrame)() = nullptr;                   // 过渡续帧
};
inline TplEnv g_tplEnv;

// ---------------------------------------------------------------------------
// 模板数据结构
// ---------------------------------------------------------------------------
struct TplPart {
    std::string name;
    // 形状：0 rect（inset 区域） 1 round（shapeA=radius）
    //       2 ellipse（inset 区域） 3 path（pathId；shapeA>0 = fixed 框内适配）
    //       4 fixed-round（A=w B=h C=radius，垂直居中锚定） 5 fixed-ellipse（A=w B=h）
    //       6 hstrip（A=height B=radius，水平满宽垂直居中）
    int shape = 0;
    float shapeA = 0.0f, shapeB = 0.0f, shapeC = 0.0f;
    int pathId = 0;          // shape=3 的几何句柄
    int anchor = 0;          // fixed 布局水平锚：0 左 1 中 2 右
    int fillKind = 0;        // 0 none 1 color 2 brush(句柄) 3 双色垂直渐变(fillColor→c2 色)
    Color fillColor;
    int fillBrush = 0;       // fillKind=2 的画刷句柄
    bool strokeSet = false;  Color strokeColor;  float strokeWidth = 1.0f;
    float inset[4] = {0, 0, 0, 0};   // l,t,r,b（可负 = 外扩）
    bool textSlot = false;   // 文字槽（画控件文本，不画几何）
    int tsId = 0;            // 文字样式句柄（0 = 控件默认字号/粗细/族）
    int halign = -1, valign = -1;   // -1 = 未设（用 ts 的默认；都没有则居中）
    float tmargin[4] = {0, 0, 0, 0}; // 文字槽内边距 l,t,r,b
    bool textColorSet = false;  Color textColor;
    // 值驱动几何（控件 TplValue(src) 实时驱动，不参与状态插值）：
    //   0 无  1 宽度按值（锚左）  2 x 按值位移（range<0 = 自动：容器宽-part宽）
    //   3 整体缩放按值（c2[0]=最小比例）  4 透明度按值  5 高度按值（锚底）
    //   6 fill 色按值 lerp（fillColor → c2 色）
    int valueMode = 0;
    int valueSrc = 0;        // 0 主值 1 检查动画进度
    float valueRange = 0.0f;
    float c2[4] = {0, 0, 0, 1};   // mode3 min 比例 / mode6 第二色
};

struct TplOverride {
    unsigned mask = 0;       // 状态位组合（全部命中当前 flags 才生效）
    std::string part;        // 空 = 作用于全部 part
    bool fillBrushSet = false;  int fillBrush = 0;
    bool fillColorSet = false;  Color fillColor;
    bool strokeColorSet = false; Color strokeColor;
    bool strokeWidthSet = false; float strokeWidth = 1.0f;
    bool translateSet = false;  float tx = 0.0f, ty = 0.0f;
    bool scaleSet = false;      float sx = 1.0f, sy = 1.0f;
    bool rotateSet = false;     float rot = 0.0f;
    bool opacitySet = false;    float opacity = 1.0f;
    bool radiusSet = false;     float radius = 0.0f;
};

struct TplTransition {
    unsigned mask = 0;       // 与「发生变化的状态位」有交集即参与（进出双向）
    float ms = 0.0f;
    int easing = 0;          // 12 缓动编号（同 qg_easing）
};

struct VisualTemplate {
    std::vector<TplPart> parts;
    std::vector<TplOverride> states;
    std::vector<TplTransition> transitions;
    int defaultType = -1;    // >=0 = 默认模板（首次渲染时物化 parts，之后置 -1）
};

// ---- 模板表（单消息线程模型：脚本调用与渲染同线程，无锁） ----
inline std::unordered_map<int, VisualTemplate> g_tplStore;
inline int g_nextTplId = 1;

inline VisualTemplate* tpl_find(int id) {
    auto it = g_tplStore.find(id);
    return it == g_tplStore.end() ? nullptr : &it->second;
}
inline int tpl_create() {
    int id = g_nextTplId++;
    g_tplStore[id] = VisualTemplate{};
    return id;
}
inline void tpl_free(int id) { g_tplStore.erase(id); }
inline TplPart* tpl_part_find(VisualTemplate* t, const std::string& name) {
    if (!t) return nullptr;
    for (auto& p : t->parts)
        if (p.name == name) return &p;
    return nullptr;
}

// ---------------------------------------------------------------------------
// 渲染参数集（TplPartAnim 同布局，见 ui_component.h 的索引注释）
// ---------------------------------------------------------------------------
struct TplParams {
    float v[kTplParamCount];
    TplParams() { for (int i = 0; i < kTplParamCount; ++i) v[i] = 0; }
    void fromPart(const TplPart& p) {
        v[0] = p.fillColor.r; v[1] = p.fillColor.g; v[2] = p.fillColor.b; v[3] = p.fillColor.a;
        v[4] = (float)p.fillKind; v[5] = (float)p.fillBrush;
        v[6] = p.strokeColor.r; v[7] = p.strokeColor.g; v[8] = p.strokeColor.b; v[9] = p.strokeColor.a;
        v[10] = p.strokeWidth; v[11] = p.strokeSet ? 1.0f : 0.0f;
        v[12] = 0; v[13] = 0; v[14] = 1; v[15] = 1; v[16] = 0; v[17] = 1;
        v[18] = (p.shape == 1) ? p.shapeA : p.shapeC;
    }
};
inline bool tpl_params_differ(const TplParams& a, const TplParams& b) {
    for (int i = 0; i < kTplParamCount; ++i) {
        if (i == 4 || i == 5 || i == 11) { if (a.v[i] != b.v[i]) return true; }
        else if (std::fabs(a.v[i] - b.v[i]) > 1e-4f) return true;
    }
    return false;
}
// 数组版（TplPartAnim 的 last/lastRender 是裸数组）
inline bool tpl_params_differ_arr(const float* a, const TplParams& b) {
    for (int i = 0; i < kTplParamCount; ++i) {
        if (i == 4 || i == 5 || i == 11) { if (a[i] != b.v[i]) return true; }
        else if (std::fabs(a[i] - b.v[i]) > 1e-4f) return true;
    }
    return false;
}
inline void tpl_params_copy(float* dst, const TplParams& src) {
    for (int i = 0; i < kTplParamCount; ++i) dst[i] = src.v[i];
}
inline void tpl_arr_copy(float* dst, const float* src) {
    for (int i = 0; i < kTplParamCount; ++i) dst[i] = src[i];
}
inline void tpl_params_load(TplParams& out, const float* src) {
    for (int i = 0; i < kTplParamCount; ++i) out.v[i] = src[i];
}
// 插值：颜色/标量渐变；离散槽（fillKind/fillBrush/strokeSet）直接取目标
inline TplParams tpl_params_lerp(const TplParams& a, const TplParams& b, float t) {
    TplParams o;
    if (t < 0.0f) t = 0.0f;
    if (t > 1.0f) t = 1.0f;
    for (int i = 0; i < kTplParamCount; ++i) {
        if (i == 4 || i == 5 || i == 11) o.v[i] = b.v[i];
        else o.v[i] = a.v[i] + (b.v[i] - a.v[i]) * t;
    }
    return o;
}

// 状态覆盖叠加
inline void tpl_override_apply(const TplOverride& ov, TplParams& cur) {
    if (ov.fillBrushSet) { cur.v[5] = (float)ov.fillBrush; cur.v[4] = 2.0f; }
    if (ov.fillColorSet) {
        cur.v[0] = ov.fillColor.r; cur.v[1] = ov.fillColor.g;
        cur.v[2] = ov.fillColor.b; cur.v[3] = ov.fillColor.a;
        if (!ov.fillBrushSet) cur.v[4] = 1.0f;
    }
    if (ov.strokeColorSet) {
        cur.v[6] = ov.strokeColor.r; cur.v[7] = ov.strokeColor.g;
        cur.v[8] = ov.strokeColor.b; cur.v[9] = ov.strokeColor.a;
        cur.v[11] = 1.0f;
    }
    if (ov.strokeWidthSet) { cur.v[10] = ov.strokeWidth; cur.v[11] = 1.0f; }
    if (ov.translateSet) { cur.v[12] = ov.tx; cur.v[13] = ov.ty; }
    if (ov.scaleSet) { cur.v[14] = ov.sx; cur.v[15] = ov.sy; }
    if (ov.rotateSet) cur.v[16] = ov.rot;
    if (ov.opacitySet) cur.v[17] = ov.opacity;
    if (ov.radiusSet) cur.v[18] = ov.radius;
}

// 缓动（12 种，编号与 qg_easing / animation.h 一致）
inline float tpl_ease(int kind, float t) {
    switch (kind) {
        case 1:  return EasingQuadIn(t);
        case 2:  return EasingQuadOut(t);
        case 3:  return EasingQuadInOut(t);
        case 4:  return EasingCubicIn(t);
        case 5:  return EasingCubicOut(t);
        case 6:  return EasingCubicInOut(t);
        case 7:  return EasingSineIn(t);
        case 8:  return EasingSineOut(t);
        case 9:  return EasingSineInOut(t);
        case 10: return EasingElasticOut(t);
        case 11: return EasingBounceOut(t);
        default: return t;
    }
}

// 过渡匹配：与「变化的状态位」（lastFlags XOR flags）交集最大者胜
inline const TplTransition* tpl_match_transition(const VisualTemplate* t,
                                                  unsigned lastFlags, unsigned flags) {
    unsigned delta = lastFlags ^ flags;
    if (!delta) return nullptr;
    const TplTransition* best = nullptr;
    unsigned bestPop = 0;
    for (const auto& tr : t->transitions) {
        unsigned ov = tr.mask & delta;
        if (!ov) continue;
        unsigned pop = 0;
        for (unsigned m = ov; m; m &= m - 1) ++pop;
        if (pop > bestPop) { best = &tr; bestPop = pop; }
    }
    return best;
}

// ---------------------------------------------------------------------------
// part 几何布局（bounds + inset + fixed 布局 + value 驱动 → 实际矩形）
// ---------------------------------------------------------------------------
inline Rect tpl_part_rect(const TplPart& p, const Rect& b, float val) {
    float l = b.left + p.inset[0];
    float t = b.top + p.inset[1];
    float r = b.right - p.inset[2];
    float bo = b.bottom - p.inset[3];
    float w = r - l, h = bo - t;

    bool fixed = (p.shape == 4 || p.shape == 5 || p.shape == 6) ||
                 (p.shape == 3 && p.shapeA > 0.0f);
    if (fixed) {
        if (p.shape == 6) {
            h = p.shapeA;                       // 水平条：满宽（收缩 inset）垂直居中
        } else {
            w = p.shapeA;
            h = (p.shapeB > 0.0f) ? p.shapeB : p.shapeA;
        }
        t = b.top + (b.Height() - h) / 2.0f;    // 垂直居中
        if (p.anchor == 1)      l = b.left + (b.Width() - w) / 2.0f;
        else if (p.anchor == 2) l = b.right - p.inset[2] - w;
        else                    l = b.left + p.inset[0];
    }

    if (p.valueMode == 1) {
        w *= val;                                // 宽度按值（锚左）
    } else if (p.valueMode == 5) {
        float h0 = h;
        h *= val;
        t += h0 - h;                             // 高度按值（锚底）
    } else if (p.valueMode == 2) {
        float range = p.valueRange;
        if (range < 0.0f) range = b.Width() - w; // 自动行程
        l += val * range;
    }
    return Rect(l, t, l + w, t + h);
}

// ---------------------------------------------------------------------------
// 单 part 绘制
// ---------------------------------------------------------------------------
inline void tpl_draw_part(UIComponent* comp, Renderer* r, const TplPart& p,
                          const TplParams& cur, const Rect& bounds, float val) {
    Rect pr = tpl_part_rect(p, bounds, val);

    float sx = cur.v[14], sy = cur.v[15];
    if (p.valueMode == 3) {
        float s = p.c2[0] + val * (1.0f - p.c2[0]);
        sx *= s; sy *= s;
    }
    float opacity = cur.v[17];
    if (p.valueMode == 4) opacity *= val;
    if (opacity < 0.0f) opacity = 0.0f;
    if (opacity > 1.0f) opacity = 1.0f;

    bool xf = (cur.v[12] != 0.0f || cur.v[13] != 0.0f ||
               sx != 1.0f || sy != 1.0f || cur.v[16] != 0.0f);
    if (xf) {
        r->PushTransform();
        if (cur.v[12] != 0.0f || cur.v[13] != 0.0f)
            r->Translate(cur.v[12], cur.v[13]);
        float cx = pr.left + pr.Width() / 2.0f;
        float cy = pr.top + pr.Height() / 2.0f;
        if (sx != 1.0f || sy != 1.0f) r->Scale(cx, cy, sx, sy);
        if (cur.v[16] != 0.0f) r->Rotate(cx, cy, cur.v[16]);
    }
    bool layered = opacity < 0.999f;
    if (layered) layered = r->PushLayerOpacity(opacity, pr);

    if (p.textSlot) {
        Rect tr = pr;
        tr.left += p.tmargin[0]; tr.top += p.tmargin[1];
        tr.right -= p.tmargin[2]; tr.bottom -= p.tmargin[3];
        std::wstring text = comp->TplText();
        if (!text.empty() && tr.Width() > 0.5f) {
            // 色解析优先级：模板显式色 > v4 状态色叠加 > 控件默认（坑位预警⑤）
            Color tc;
            if (p.textColorSet) tc = p.textColor;
            else if (!comp->ResolvedStateText(comp->StyleFlags(), tc)) tc = comp->TplTextColor();
            const TplTsParams* ts = (p.tsId > 0 && g_tplEnv.tsOf) ? g_tplEnv.tsOf(p.tsId) : nullptr;
            if (ts) {
                int ha = (p.halign >= 0) ? p.halign : ts->halign;
                int va = (p.valign >= 0) ? p.valign : ts->valign;
                r->DrawTextStyled(text, tr, tc, ts->size, ts->weight, ts->italic,
                                  ts->family, ha, va, ts->wrap, false);
            } else {
                int ha = (p.halign >= 0) ? p.halign : 1;
                int va = (p.valign >= 0) ? p.valign : 1;
                r->DrawTextStyled(text, tr, tc, comp->TplFontSize(),
                                  comp->TplBold() ? 700 : 400, false, std::wstring(),
                                  ha, va, false, false);
            }
        }
    } else {
        Color fc(cur.v[0], cur.v[1], cur.v[2], cur.v[3]);
        if (p.valueMode == 6) {
            Color c2(p.c2[0], p.c2[1], p.c2[2], p.c2[3]);
            fc = Color::Lerp(fc, c2, val);
        }
        int fk = (int)cur.v[4];
        ID2D1Brush* br = (fk == 2 && g_tplEnv.brushOf) ? g_tplEnv.brushOf((int)cur.v[5]) : nullptr;
        Color sc(cur.v[6], cur.v[7], cur.v[8], cur.v[9]);
        float sw = cur.v[10];
        float rad = cur.v[18];
        if (p.shape == 6) rad = p.shapeB;       // hstrip 圆角（不参与状态插值）
        if (rad * 2.0f > pr.Width())  rad = pr.Width() / 2.0f;
        if (rad * 2.0f > pr.Height()) rad = pr.Height() / 2.0f;
        if (rad < 0.0f) rad = 0.0f;
        bool stroke = cur.v[11] > 0.5f;

        if (pr.Width() <= 0.01f || pr.Height() <= 0.01f) {
            // 塌缩几何（值=0 的填充条）：跳过几何但保留描边外框？不——直接跳过
        } else if (p.shape == 2 || p.shape == 5) {
            Ellipse e(pr.left + pr.Width() / 2.0f, pr.top + pr.Height() / 2.0f,
                      pr.Width() / 2.0f, pr.Height() / 2.0f);
            if (fk == 2) { if (br) r->FillEllipseBrush(e, br); }
            else if (fk != 0) r->FillEllipse(e, fc);
            if (stroke) {
                if (fk == 2 && br) r->DrawEllipseBrush(e, br, sw);
                else r->DrawEllipse(e, sc, sw);
            }
        } else if (p.shape == 3) {
            ID2D1PathGeometry* geo = g_tplEnv.geoOf ? g_tplEnv.geoOf(p.pathId) : nullptr;
            if (geo) {
                // 几何 bounds → part 区域的平移+缩放适配
                D2D1_RECT_F gb = {};
                geo->GetBounds(nullptr, &gb);
                float gw = gb.right - gb.left, gh = gb.bottom - gb.top;
                float ssx = gw > 0.01f ? pr.Width() / gw : 1.0f;
                float ssy = gh > 0.01f ? pr.Height() / gh : 1.0f;
                r->PushTransform();
                r->Translate(-gb.left, -gb.top);
                r->Scale(0.0f, 0.0f, ssx, ssy);
                r->Translate(pr.left, pr.top);
                if (fk == 2) { if (br) r->FillGeometryBrush(geo, br); }
                else if (fk != 0) r->FillGeometry(geo, fc);
                if (stroke) {
                    if (fk == 2 && br) r->StrokeGeometryBrush(geo, br, sw);
                    else r->StrokeGeometry(geo, sc, sw);
                }
                r->PopTransform();
            }
        } else {
            RoundedRect rr(pr, rad, rad);
            if (rad > 0.01f) {
                if (fk == 2) { if (br) r->FillRoundedRectBrush(rr, br); }
                else if (fk == 3) r->FillRoundedRectGradient(rr, fc, Color(p.c2[0], p.c2[1], p.c2[2], p.c2[3]), true);
                else if (fk != 0) r->FillRoundedRect(rr, fc);
                if (stroke) {
                    if (fk == 2 && br) r->DrawRoundedRectBrush(rr, br, sw);
                    else r->DrawRoundedRect(rr, sc, sw);
                }
            } else {
                if (fk == 2) { if (br) r->FillRectBrush(pr, br); }
                else if (fk == 3) r->FillRectGradient(pr, fc, Color(p.c2[0], p.c2[1], p.c2[2], p.c2[3]), true);
                else if (fk != 0) r->FillRect(pr, fc);
                if (stroke) {
                    if (fk == 2 && br) r->DrawRectBrush(pr, br, sw);
                    else r->DrawRect(pr, sc, sw);
                }
            }
        }
    }

    if (layered) r->PopLayerOpacity();
    if (xf) r->PopTransform();
}

// ---------------------------------------------------------------------------
// 渲染主体（控件 Draw 的模板分支调用）
// ---------------------------------------------------------------------------
inline void tpl_materialize_default(VisualTemplate* tpl, Renderer* r);   // 前置声明（定义在下方）

inline void RenderTemplate(UIComponent* comp, Renderer* renderer, unsigned flags) {
    if (!comp || !renderer) return;
    VisualTemplate* tpl = tpl_find(comp->GetTemplateId());
    if (!tpl) return;
    if (tpl->defaultType >= 0) tpl_materialize_default(tpl, renderer);
    if (!tpl || tpl->parts.empty()) return;
    if (!g_tplEnv.brushOf || !g_tplEnv.geoOf) return;   // env 未注入（异常防御）

    if (comp->m_tplAnim.size() != tpl->parts.size())
        comp->m_tplAnim.assign(tpl->parts.size(), TplPartAnim{});

    Rect bounds = comp->GetBounds();
    auto nowTp = std::chrono::steady_clock::now();
    double now = std::chrono::duration<double>(nowTp.time_since_epoch()).count();

    for (size_t i = 0; i < tpl->parts.size(); ++i) {
        const TplPart& p = tpl->parts[i];
        TplPartAnim& an = comp->m_tplAnim[i];

        // 1) 基础参数 + 状态覆盖（mask 全命中才生效）
        TplParams cur;
        cur.fromPart(p);
        for (const auto& st : tpl->states) {
            if (st.mask == 0) continue;
            if ((flags & st.mask) != st.mask) continue;
            if (!st.part.empty() && st.part != p.name) continue;
            tpl_override_apply(st, cur);
        }
        // 2) 值驱动（几何模式；不进参数集）
        float val = comp->TplValue(p.valueSrc);
        if (val < 0.0f) val = 0.0f;
        if (val > 1.0f) val = 1.0f;
        // 3) 过渡推进 / 变化检测
        if (an.active) {
            float t = an.ms > 0.0f ? (float)((now - an.t0) * 1000.0 / an.ms) : 1.0f;
            if (t >= 1.0f) {
                an.active = false;
                tpl_params_load(cur, an.to);          // 收尾帧 = 目标值
            } else {
                TplParams a, b;
                tpl_params_load(a, an.from);
                tpl_params_load(b, an.to);
                cur = tpl_params_lerp(a, b, tpl_ease(an.easing, t));
                if (g_tplEnv.requestFrame) g_tplEnv.requestFrame();
            }
        } else if (!an.hasLast || tpl_params_differ_arr(an.last, cur)) {
            const TplTransition* tr = an.hasLast
                ? tpl_match_transition(tpl, an.lastFlags, flags) : nullptr;
            if (tr && tr->ms > 0.0f) {
                an.active = true;
                an.t0 = now;
                an.ms = tr->ms;
                an.easing = tr->easing;
                if (an.hasLast) tpl_arr_copy(an.from, an.lastRender);   // 从当前视觉状态起跳
                else { TplParams base; base.fromPart(p); tpl_params_copy(an.from, base); }
                tpl_params_copy(an.to, cur);
                // 首帧渲染起点值（下一帧开始插值）
                TplParams f;
                tpl_params_load(f, an.from);
                cur = f;
                if (g_tplEnv.requestFrame) g_tplEnv.requestFrame();
            }
            tpl_params_copy(an.last, cur);
            an.hasLast = true;
        }
        an.lastFlags = flags;
        tpl_params_copy(an.lastRender, cur);
        // 4) 绘制
        tpl_draw_part(comp, renderer, p, cur, bounds, val);
    }
}

// ---------------------------------------------------------------------------
// 默认模板物化（首次渲染时——此时必有 Renderer，path 几何可建）
// ---------------------------------------------------------------------------
inline int tpl_build_check_path(Renderer* r) {
    if (!r || !g_tplEnv.geoStore) return 0;
    if (!r->PathReset()) return 0;
    // 对勾（18×18 局部坐标；复刻 CheckBox 三点比例 0.3/0.45/0.7）
    r->PathMoveTo(5.4f, 9.0f);
    r->PathLineTo(8.1f, 12.6f);
    r->PathLineTo(12.6f, 5.4f);
    ID2D1PathGeometry* geo = r->PathTakeRef();
    if (!geo) return 0;
    return g_tplEnv.geoStore(geo);
}

inline int tpl_build_arrow_path(Renderer* r) {
    if (!r || !g_tplEnv.geoStore) return 0;
    if (!r->PathReset()) return 0;
    // 下拉箭头（10×5 局部坐标，两个 figure 共享底点）
    r->PathMoveTo(0.0f, 0.0f);
    r->PathLineTo(5.0f, 5.0f);
    r->PathMoveTo(10.0f, 0.0f);
    r->PathLineTo(5.0f, 5.0f);
    ID2D1PathGeometry* geo = r->PathTakeRef();
    if (!geo) return 0;
    return g_tplEnv.geoStore(geo);
}

inline void tpl_materialize_default(VisualTemplate* tpl, Renderer* r) {
    if (!tpl) return;
    int type = tpl->defaultType;
    tpl->defaultType = -1;
    std::vector<TplPart> defs;
    switch (type) {
        case kTplDefButton: {
            TplPart bg;  bg.name = "bg";  bg.shape = 1; bg.shapeA = 8.0f;
            bg.fillKind = 1; bg.fillColor = Color(0.2f, 0.6f, 1.0f, 0.9f);
            bg.strokeSet = true; bg.strokeColor = Color(1, 1, 1, 0.3f); bg.strokeWidth = 1.0f;
            TplPart tx;   tx.name = "text"; tx.textSlot = true; tx.halign = 1; tx.valign = 1;
            defs.push_back(bg); defs.push_back(tx);
            TplOverride hov; hov.mask = kTplFlagHover;   hov.part = "bg";
            hov.fillColorSet = true; hov.fillColor = Color(0.3f, 0.7f, 1.0f, 1.0f);
            TplOverride prs; prs.mask = kTplFlagPressed; prs.part = "bg";
            prs.fillColorSet = true; prs.fillColor = Color(0.15f, 0.5f, 0.85f, 1.0f);
            tpl->states.push_back(hov); tpl->states.push_back(prs);
            TplTransition tr; tr.mask = kTplFlagHover | kTplFlagPressed;
            tr.ms = 200.0f; tr.easing = 2;      // 复刻 hoverAnim 0.2s QuadOut
            tpl->transitions.push_back(tr);
        } break;
        case kTplDefCheckbox: {
            TplPart box;  box.name = "box"; box.shape = 4;
            box.shapeA = 18.0f; box.shapeB = 18.0f; box.shapeC = 4.0f;
            box.fillKind = 1; box.fillColor = Color(0.25f, 0.25f, 0.35f, 0.9f);
            box.strokeSet = true; box.strokeColor = Color(0.4f, 0.4f, 0.5f, 0.5f);
            box.strokeWidth = 1.0f;
            TplPart chk;  chk.name = "check"; chk.shape = 3;
            chk.shapeA = 18.0f; chk.shapeB = 18.0f;
            chk.pathId = tpl_build_check_path(r);
            chk.fillKind = 0;
            chk.strokeSet = true; chk.strokeColor = Color(0.2f, 0.6f, 1.0f, 1.0f);
            chk.strokeWidth = 2.0f;
            chk.valueMode = 3; chk.valueSrc = 1; chk.c2[0] = 0.6f;  // 勾随动画进度缩放
            TplPart tx;   tx.name = "text"; tx.textSlot = true;
            tx.halign = 0; tx.valign = 1; tx.inset[0] = 26.0f;      // 18 + 8
            defs.push_back(box); defs.push_back(chk); defs.push_back(tx);
        } break;
        case kTplDefToggle: {
            TplPart bg;   bg.name = "bg"; bg.shape = 4;
            bg.shapeA = 50.0f; bg.shapeB = 26.0f; bg.shapeC = 13.0f;
            bg.fillKind = 1; bg.fillColor = Color(0.35f, 0.35f, 0.45f, 0.9f);  // offColor
            bg.valueMode = 6; bg.valueSrc = 1;
            bg.c2[0] = 0.2f; bg.c2[1] = 0.6f; bg.c2[2] = 1.0f; bg.c2[3] = 1.0f; // onColor
            TplPart th;   th.name = "thumb"; th.shape = 5;
            th.shapeA = 22.0f; th.shapeB = 22.0f;
            th.fillKind = 1; th.fillColor = Color(1, 1, 1, 1);
            th.valueMode = 2; th.valueSrc = 1; th.valueRange = 28.0f;  // 50 - 22
            defs.push_back(bg); defs.push_back(th);
        } break;
        case kTplDefSlider: {
            TplPart trk;  trk.name = "track"; trk.shape = 6;
            trk.shapeA = 4.0f; trk.shapeB = 2.0f;
            trk.fillKind = 1; trk.fillColor = Color(0.2f, 0.2f, 0.3f, 0.7f);
            TplPart fil;  fil.name = "fill"; fil.shape = 6;
            fil.shapeA = 4.0f; fil.shapeB = 2.0f;
            fil.fillKind = 1; fil.fillColor = Color(0.2f, 0.6f, 1.0f, 1.0f);
            fil.valueMode = 1; fil.valueSrc = 0;
            TplPart th;   th.name = "thumb"; th.shape = 5;
            th.shapeA = 16.0f; th.shapeB = 16.0f;
            th.fillKind = 1; th.fillColor = Color(0.3f, 0.7f, 1.0f, 1.0f);
            th.valueMode = 2; th.valueSrc = 0; th.valueRange = -1.0f;  // 自动行程
            defs.push_back(trk); defs.push_back(fil); defs.push_back(th);
            // hover：thumb 变色 + 放大 16→18px（复刻 thumbHoverAnim）
            TplOverride hov; hov.mask = kTplFlagHover; hov.part = "thumb";
            hov.fillColorSet = true; hov.fillColor = Color(0.4f, 0.8f, 1.0f, 1.0f);
            hov.scaleSet = true; hov.sx = 1.125f; hov.sy = 1.125f;
            TplOverride drg; drg.mask = kTplFlagPressed; drg.part = "thumb";
            drg.fillColorSet = true; drg.fillColor = Color(0.4f, 0.8f, 1.0f, 1.0f);
            tpl->states.push_back(hov); tpl->states.push_back(drg);
            TplTransition tt; tt.mask = kTplFlagHover | kTplFlagPressed;
            tt.ms = 150.0f; tt.easing = 2;
            tpl->transitions.push_back(tt);
        } break;
        case kTplDefProgress: {
            TplPart trk;  trk.name = "track"; trk.shape = 6;
            trk.shapeA = 8.0f; trk.shapeB = 10.0f;
            trk.fillKind = 1; trk.fillColor = Color(0.2f, 0.2f, 0.3f, 0.5f);
            TplPart fil;  fil.name = "fill"; fil.shape = 6;
            fil.shapeA = 8.0f; fil.shapeB = 10.0f;
            fil.fillKind = 3; fil.fillColor = Color(0.2f, 0.6f, 1.0f, 1.0f);
            fil.c2[0] = 0.4f; fil.c2[1] = 0.8f; fil.c2[2] = 1.0f; fil.c2[3] = 1.0f;  // 渐变尾色
            fil.valueMode = 1; fil.valueSrc = 0;
            defs.push_back(trk); defs.push_back(fil);
            // 取舍说明：百分比标签是随 value 变化的行为性附属文本，
            // 不属于静态部件（模板 textSlot 语义 = 控件文本）——默认模板不含，
            // 需要 vg_tpl_part 的 textSlot 自行加（margin 负值可上移）。
        } break;
        case kTplDefComboBox: {
            TplPart bg;   bg.name = "bg"; bg.shape = 1; bg.shapeA = 6.0f;
            bg.fillKind = 1; bg.fillColor = Color(0.18f, 0.18f, 0.25f, 1.0f);
            bg.strokeSet = true; bg.strokeColor = Color(0.35f, 0.35f, 0.45f, 1.0f);
            bg.strokeWidth = 1.0f;
            TplPart ar;   ar.name = "arrow"; ar.shape = 3;
            ar.shapeA = 10.0f; ar.shapeB = 5.0f; ar.anchor = 2;
            ar.inset[2] = 15.0f;                     // 箭头右缘：right-25..right-15
            ar.pathId = tpl_build_arrow_path(r);
            ar.fillKind = 0;
            ar.strokeSet = true; ar.strokeColor = Color(0.95f, 0.95f, 1.0f, 1.0f);
            ar.strokeWidth = 1.5f;
            TplPart tx;   tx.name = "text"; tx.textSlot = true;
            tx.halign = 0; tx.valign = 1;
            tx.inset[0] = 10.0f; tx.inset[2] = 30.0f;
            defs.push_back(bg); defs.push_back(ar); defs.push_back(tx);
            // 下拉列表是动态 items 结构（ItemsPresenter 范畴），保留代码绘制。
        } break;
        case kTplDefTabControl: {
            TplPart bg;   bg.name = "bg"; bg.shape = 0;
            bg.inset[1] = 37.0f;                      // 内容区 = tab 头(36)+1 之下
            bg.fillKind = 1; bg.fillColor = Color(0.12f, 0.12f, 0.18f, 1.0f);
            bg.strokeSet = true; bg.strokeColor = Color(0.25f, 0.25f, 0.35f, 1.0f);
            bg.strokeWidth = 1.0f;
            defs.push_back(bg);
            // tab 头是动态重复结构，保留代码绘制；模板只管内容区背景。
        } break;
        default: break;
    }
    if (!defs.empty())
        tpl->parts.insert(tpl->parts.begin(), defs.begin(), defs.end());
}

}  // namespace gui
