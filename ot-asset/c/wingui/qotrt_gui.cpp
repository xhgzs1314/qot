// ===========================================================================
// qotrt_gui.cpp —— vg_* 的原生（AOT）实现 · 独立编译单元
//
// 归属（特性 = 库）：实现只存在于本 TU，runtime.h 不含 GUI；生成的用户
// 源码自带原型（cgen 注入），链接期由 qot_cache.gui_objects() 把本 TU 与
// qotgui_abi / modern_window / renderer 一起链入。解释器侧则经 ctypes 驱动
// qotgui.dll —— 两侧调用的是同一份 wingui C++ 实现，行为由代码保证一致。
//
// 事件模型：轮询（vg_poll_event）。C 侧无法反向调用引擎的函数值，轮询让
// 解释器与原生程序天然同语义。
// ===========================================================================
#include "qot_aot_runtime.h"
#include "qotgui_abi.h"

#include <cstring>
#include <cstdio>
#include <string>
#include <vector>

namespace {

double qg_as_num(const V16 &v, double dflt = 0.0) {
    if (v.t == VT_NUM) return v.u.d;
    if (v.t == VT_BOOL) return v.u.d != 0.0 ? 1.0 : 0.0;
    return dflt;
}

bool qg_as_bool(const V16 &v) {
    if (v.t == VT_BOOL) return v.u.d != 0.0;
    if (v.t == VT_NUM) return v.u.d != 0.0;
    return false;
}

void qg_as_cstr(const V16 &v, char *buf, int buflen) {
    buf[0] = '\0';
    QrtBytes b;
    qotrt_prim_bytes((V16 *)&v, &b);
    if (b.p && b.len > 0) {
        int n = b.len < buflen - 1 ? b.len : buflen - 1;
        memcpy(buf, b.p, (size_t)n);
        buf[n] = '\0';
    }
    qotrt_prim_bytes_free(&b);
}

V16 qg_null() {
    V16 v;
    memset(&v, 0, sizeof(v));
    v.t = VT_NULL;
    return v;
}

bool qg_as_color(const V16 &v, float *r, float *g, float *b, float *a) {
    if (v.t != VT_ARR || !v.u.p) return false;
    QArr *arr = (QArr *)v.u.p;
    *r = arr->len > 0 ? (float)qg_as_num(arr->a[0], 0) : 0.0f;
    *g = arr->len > 1 ? (float)qg_as_num(arr->a[1], 0) : 0.0f;
    *b = arr->len > 2 ? (float)qg_as_num(arr->a[2], 0) : 0.0f;
    *a = arr->len > 3 ? (float)qg_as_num(arr->a[3], 1) : 1.0f;
    return true;
}

int qg_create_kind(const char *kind, const V16 *args, int argc) {
    int win = argc > 0 ? (int)qg_as_num(args[0], 0) : 0;
    if (strcmp(kind, "slider") == 0 || strcmp(kind, "progressbar") == 0 ||
        strcmp(kind, "spinner") == 0 || strcmp(kind, "combobox") == 0 ||
        strcmp(kind, "listbox") == 0 || strcmp(kind, "tabcontrol") == 0 ||
        strcmp(kind, "table") == 0 || strcmp(kind, "colorpicker") == 0 ||
        strcmp(kind, "datepicker") == 0) {
        // 无 text 参数的控件族（C ABI 5 参）；v6 修正：combobox/listbox/
        // tabcontrol 此前误走 6 参分支（text 被忽略但坐标错位一档）
        int x = argc > 1 ? (int)qg_as_num(args[1], 50) : 50;
        int y = argc > 2 ? (int)qg_as_num(args[2], 50) : 50;
        int w = argc > 3 ? (int)qg_as_num(args[3], 160) : 160;
        int h = argc > 4 ? (int)qg_as_num(args[4], 28) : 28;
        if (strcmp(kind, "slider") == 0)      return qg_create_slider(win, x, y, w, h);
        if (strcmp(kind, "progressbar") == 0) return qg_create_progressbar(win, x, y, w, h);
        if (strcmp(kind, "combobox") == 0)   return qg_create_combobox(win, x, y, w, h);
        if (strcmp(kind, "listbox") == 0)    return qg_create_listbox(win, x, y, w, h);
        if (strcmp(kind, "tabcontrol") == 0) return qg_create_tabcontrol(win, x, y, w, h);
        if (strcmp(kind, "table") == 0)      return qg_create_table(win, x, y, w, h);
        if (strcmp(kind, "colorpicker") == 0) return qg_create_colorpicker(win, x, y, w, h);
        if (strcmp(kind, "datepicker") == 0) return qg_create_datepicker(win, x, y, w, h);
        return qg_create_spinner(win, x, y, w, h);
    }
    char buf[512] = "";
    if (argc > 1) qg_as_cstr(args[1], buf, sizeof(buf));
    int x = argc > 2 ? (int)qg_as_num(args[2], 50) : 50;
    int y = argc > 3 ? (int)qg_as_num(args[3], 50) : 50;
    int w = argc > 4 ? (int)qg_as_num(args[4], 160) : 160;
    int h = argc > 5 ? (int)qg_as_num(args[5], 32) : 32;
    if (strcmp(kind, "label") == 0)      return qg_create_label(win, buf, x, y, w, h);
    if (strcmp(kind, "button") == 0)     return qg_create_button(win, buf, x, y, w, h);
    if (strcmp(kind, "checkbox") == 0)   return qg_create_checkbox(win, buf, x, y, w, h);
    if (strcmp(kind, "toggle") == 0)     return qg_create_toggle(win, buf, x, y, w, h);
    if (strcmp(kind, "textbox") == 0)    return qg_create_textbox(win, buf, x, y, w, h);
    if (strcmp(kind, "card") == 0)       return qg_create_card(win, buf, x, y, w, h);
    if (strcmp(kind, "combobox") == 0)   return qg_create_combobox(win, x, y, w, h);
    if (strcmp(kind, "listbox") == 0)    return qg_create_listbox(win, x, y, w, h);
    if (strcmp(kind, "tabcontrol") == 0) return qg_create_tabcontrol(win, x, y, w, h);
    return 0;
}

}  // namespace

extern "C" {

V16 qotrt_vg_init(V16 *args, int argc) {
    (void)args; (void)argc;
    return qotrt_bool(qg_init() == 1);
}

V16 qotrt_vg_uninit(V16 *args, int argc) {
    (void)args; (void)argc;
    qg_uninit();
    return qotrt_bool(true);
}

V16 qotrt_vg_create_window(V16 *args, int argc) {
    char buf[512] = "QOT Window";
    if (argc > 0) {
        char t[512];
        qg_as_cstr(args[0], t, sizeof(t));
        snprintf(buf, sizeof(buf), "%s", t);
    }
    int w = argc > 1 ? (int)qg_as_num(args[1], 800) : 800;
    int h = argc > 2 ? (int)qg_as_num(args[2], 600) : 600;
    int id = qg_create_window(buf, w, h);
    return id > 0 ? qotrt_num((double)id) : qg_null();
}

V16 qotrt_vg_show_window(V16 *args, int argc) {
    if (argc < 1) return qotrt_bool(false);
    return qotrt_bool(qg_show_window((int)qg_as_num(args[0], 0)) == 1);
}

V16 qotrt_vg_close_window(V16 *args, int argc) {
    if (argc < 1) return qotrt_bool(false);
    return qotrt_bool(qg_close_window((int)qg_as_num(args[0], 0)) == 1);
}

V16 qotrt_vg_run(V16 *args, int argc) {
    (void)args; (void)argc;
    qg_run();
    return qotrt_bool(true);
}

V16 qotrt_vg_set_window_title(V16 *args, int argc) {
    if (argc < 2) return qotrt_bool(false);
    char buf[512];
    qg_as_cstr(args[1], buf, sizeof(buf));
    return qotrt_bool(qg_set_window_title((int)qg_as_num(args[0], 0), buf) == 1);
}

V16 qotrt_vg_set_window_size(V16 *args, int argc) {
    if (argc < 3) return qotrt_bool(false);
    return qotrt_bool(qg_set_window_size((int)qg_as_num(args[0], 0),
                                         (int)qg_as_num(args[1], 800),
                                         (int)qg_as_num(args[2], 600)) == 1);
}

V16 qotrt_vg_window_client_size(V16 *args, int argc) {
    if (argc < 1) return qg_null();
    int w = 0, h = 0;
    if (!qg_window_client_size((int)qg_as_num(args[0], 0), &w, &h)) return qg_null();
    V16 arr = qotrt_arr_new(2);
    QArr *a = (QArr *)arr.u.p;
    qotrt_arr_push(a, qotrt_num((double)w));
    qotrt_arr_push(a, qotrt_num((double)h));
    return arr;
}

V16 qotrt_vg_set_window_bg_gradient(V16 *args, int argc) {
    if (argc < 2) return qotrt_bool(false);
    float r0, g0, b0, a0, r1, g1, b1, a1;
    if (!qg_as_color(args[1], &r0, &g0, &b0, &a0)) return qotrt_bool(false);
    if (argc > 2 && qg_as_color(args[2], &r1, &g1, &b1, &a1)) {
        return qotrt_bool(qg_set_window_bg((int)qg_as_num(args[0], 0),
                                           r0, g0, b0, r1, g1, b1) == 1);
    }
    return qotrt_bool(qg_set_window_bg((int)qg_as_num(args[0], 0),
                                       r0, g0, b0, 0.12f, 0.08f, 0.22f) == 1);
}

/* ---- 控件创建 ---- */
V16 qotrt_vg_create_label(V16 *args, int argc) {
    int id = qg_create_kind("label", args, argc);
    return id > 0 ? qotrt_num((double)id) : qg_null();
}

V16 qotrt_vg_create_button(V16 *args, int argc) {
    int id = qg_create_kind("button", args, argc);
    return id > 0 ? qotrt_num((double)id) : qg_null();
}

V16 qotrt_vg_create_checkbox(V16 *args, int argc) {
    int id = qg_create_kind("checkbox", args, argc);
    return id > 0 ? qotrt_num((double)id) : qg_null();
}

V16 qotrt_vg_create_toggle(V16 *args, int argc) {
    int id = qg_create_kind("toggle", args, argc);
    return id > 0 ? qotrt_num((double)id) : qg_null();
}

V16 qotrt_vg_create_textbox(V16 *args, int argc) {
    int id = qg_create_kind("textbox", args, argc);
    return id > 0 ? qotrt_num((double)id) : qg_null();
}

V16 qotrt_vg_create_slider(V16 *args, int argc) {
    int id = qg_create_kind("slider", args, argc);
    return id > 0 ? qotrt_num((double)id) : qg_null();
}

V16 qotrt_vg_create_progressbar(V16 *args, int argc) {
    int id = qg_create_kind("progressbar", args, argc);
    return id > 0 ? qotrt_num((double)id) : qg_null();
}

V16 qotrt_vg_create_card(V16 *args, int argc) {
    int id = qg_create_kind("card", args, argc);
    return id > 0 ? qotrt_num((double)id) : qg_null();
}

V16 qotrt_vg_create_combobox(V16 *args, int argc) {
    int id = qg_create_kind("combobox", args, argc);
    return id > 0 ? qotrt_num((double)id) : qg_null();
}

V16 qotrt_vg_create_listbox(V16 *args, int argc) {
    int id = qg_create_kind("listbox", args, argc);
    return id > 0 ? qotrt_num((double)id) : qg_null();
}

V16 qotrt_vg_create_tabcontrol(V16 *args, int argc) {
    int id = qg_create_kind("tabcontrol", args, argc);
    return id > 0 ? qotrt_num((double)id) : qg_null();
}

V16 qotrt_vg_create_spinner(V16 *args, int argc) {
    int id = qg_create_kind("spinner", args, argc);
    return id > 0 ? qotrt_num((double)id) : qg_null();
}

V16 qotrt_vg_create_table(V16 *args, int argc) {
    int id = qg_create_kind("table", args, argc);
    return id > 0 ? qotrt_num((double)id) : qg_null();
}

V16 qotrt_vg_create_colorpicker(V16 *args, int argc) {
    int id = qg_create_kind("colorpicker", args, argc);
    return id > 0 ? qotrt_num((double)id) : qg_null();
}

V16 qotrt_vg_create_datepicker(V16 *args, int argc) {
    int id = qg_create_kind("datepicker", args, argc);
    return id > 0 ? qotrt_num((double)id) : qg_null();
}

/* ---- 通用属性 ---- */
V16 qotrt_vg_set_position(V16 *args, int argc) {
    if (argc < 3) return qotrt_bool(false);
    qg_set_pos((int)qg_as_num(args[0], 0), (int)qg_as_num(args[1], 0),
               (int)qg_as_num(args[2], 0));
    return qotrt_bool(true);
}

V16 qotrt_vg_set_size(V16 *args, int argc) {
    if (argc < 3) return qotrt_bool(false);
    qg_set_size((int)qg_as_num(args[0], 0), (int)qg_as_num(args[1], 0),
                (int)qg_as_num(args[2], 0));
    return qotrt_bool(true);
}

V16 qotrt_vg_get_pos(V16 *args, int argc) {
    if (argc < 1) return qg_null();
    int x = 0, y = 0;
    if (!qg_get_pos((int)qg_as_num(args[0], 0), &x, &y)) return qg_null();
    V16 arr = qotrt_arr_new(2);
    QArr *a = (QArr *)arr.u.p;
    qotrt_arr_push(a, qotrt_num((double)x));
    qotrt_arr_push(a, qotrt_num((double)y));
    return arr;
}

V16 qotrt_vg_get_size(V16 *args, int argc) {
    if (argc < 1) return qg_null();
    int w = 0, h = 0;
    if (!qg_get_size((int)qg_as_num(args[0], 0), &w, &h)) return qg_null();
    V16 arr = qotrt_arr_new(2);
    QArr *a = (QArr *)arr.u.p;
    qotrt_arr_push(a, qotrt_num((double)w));
    qotrt_arr_push(a, qotrt_num((double)h));
    return arr;
}

V16 qotrt_vg_set_visible(V16 *args, int argc) {
    if (argc < 2) return qotrt_bool(false);
    return qotrt_bool(qg_set_visible((int)qg_as_num(args[0], 0),
                                     qg_as_bool(args[1]) ? 1 : 0) == 1);
}

V16 qotrt_vg_set_enabled(V16 *args, int argc) {
    if (argc < 2) return qotrt_bool(false);
    return qotrt_bool(qg_set_enabled((int)qg_as_num(args[0], 0),
                                     qg_as_bool(args[1]) ? 1 : 0) == 1);
}

V16 qotrt_vg_destroy(V16 *args, int argc) {
    if (argc < 1) return qotrt_bool(false);
    return qotrt_bool(qg_destroy((int)qg_as_num(args[0], 0)) == 1);
}

/* ---- 文字（一族同形，一对一委托 qg_*）---- */
V16 qotrt_vg_button_set_text(V16 *args, int argc) {
    if (argc < 2) return qotrt_bool(false);
    char buf[4096];
    qg_as_cstr(args[1], buf, sizeof(buf));
    return qotrt_bool(qg_set_text((int)qg_as_num(args[0], 0), buf) == 1);
}

V16 qotrt_vg_label_set_text(V16 *args, int argc) {
    return qotrt_vg_button_set_text(args, argc);
}

V16 qotrt_vg_card_set_title(V16 *args, int argc) {
    return qotrt_vg_button_set_text(args, argc);
}

V16 qotrt_vg_textbox_set_text(V16 *args, int argc) {
    return qotrt_vg_button_set_text(args, argc);
}

V16 qotrt_vg_textbox_set_placeholder(V16 *args, int argc) {
    return qotrt_vg_button_set_text(args, argc);
}

V16 qotrt_vg_textbox_get_text(V16 *args, int argc) {
    if (argc < 1) return qotrt_str("", 0);
    char buf[4096];
    int n = qg_get_text((int)qg_as_num(args[0], 0), buf, sizeof(buf));
    if (n <= 0) return qotrt_str("", 0);
    return qotrt_str(buf, n);
}

/* ---- 数值 ---- */
V16 qotrt_vg_slider_set_value(V16 *args, int argc) {
    if (argc < 2) return qotrt_bool(false);
    return qotrt_bool(qg_set_value((int)qg_as_num(args[0], 0),
                                   qg_as_num(args[1], 0)) == 1);
}

V16 qotrt_vg_progressbar_set_value(V16 *args, int argc) {
    return qotrt_vg_slider_set_value(args, argc);
}

V16 qotrt_vg_checkbox_set_checked(V16 *args, int argc) {
    return qotrt_vg_slider_set_value(args, argc);
}

V16 qotrt_vg_toggle_set_checked(V16 *args, int argc) {
    return qotrt_vg_slider_set_value(args, argc);
}

V16 qotrt_vg_combobox_set_selected(V16 *args, int argc) {
    return qotrt_vg_slider_set_value(args, argc);
}

V16 qotrt_vg_listbox_set_selected(V16 *args, int argc) {
    return qotrt_vg_slider_set_value(args, argc);
}

V16 qotrt_vg_tabcontrol_set_selected(V16 *args, int argc) {
    return qotrt_vg_slider_set_value(args, argc);
}

V16 qotrt_vg_slider_get_value(V16 *args, int argc) {
    if (argc < 1) return qotrt_num(0);
    return qotrt_num(qg_get_value((int)qg_as_num(args[0], 0)));
}

V16 qotrt_vg_progressbar_get_value(V16 *args, int argc) {
    return qotrt_vg_slider_get_value(args, argc);
}

V16 qotrt_vg_checkbox_get_checked(V16 *args, int argc) {
    return qotrt_vg_slider_get_value(args, argc);
}

V16 qotrt_vg_toggle_get_checked(V16 *args, int argc) {
    return qotrt_vg_slider_get_value(args, argc);
}

V16 qotrt_vg_combobox_get_selected(V16 *args, int argc) {
    return qotrt_vg_slider_get_value(args, argc);
}

V16 qotrt_vg_listbox_get_selected(V16 *args, int argc) {
    return qotrt_vg_slider_get_value(args, argc);
}

/* ---- 颜色 ---- */
V16 qotrt_vg_button_set_color(V16 *args, int argc) {
    float r, g, b, a;
    if (argc < 2 || !qg_as_color(args[1], &r, &g, &b, &a)) return qotrt_bool(false);
    return qotrt_bool(qg_set_color((int)qg_as_num(args[0], 0), r, g, b, a) == 1);
}

V16 qotrt_vg_button_set_hover_color(V16 *args, int argc) {
    return qotrt_vg_button_set_color(args, argc);
}

V16 qotrt_vg_progressbar_set_color(V16 *args, int argc) {
    return qotrt_vg_button_set_color(args, argc);
}

V16 qotrt_vg_card_set_bg_color(V16 *args, int argc) {
    return qotrt_vg_button_set_color(args, argc);
}

V16 qotrt_vg_slider_set_fill_color(V16 *args, int argc) {
    return qotrt_vg_button_set_color(args, argc);
}

V16 qotrt_vg_label_set_text_color(V16 *args, int argc) {
    float r, g, b, a;
    if (argc < 2 || !qg_as_color(args[1], &r, &g, &b, &a)) return qotrt_bool(false);
    return qotrt_bool(qg_set_text_color((int)qg_as_num(args[0], 0), r, g, b, a) == 1);
}

V16 qotrt_vg_button_set_text_color(V16 *args, int argc) {
    return qotrt_vg_label_set_text_color(args, argc);
}

V16 qotrt_vg_textbox_set_text_color(V16 *args, int argc) {
    return qotrt_vg_label_set_text_color(args, argc);
}

/* ---- 条目与细节 ---- */
V16 qotrt_vg_combobox_add_item(V16 *args, int argc) {
    if (argc < 2) return qotrt_bool(false);
    char buf[4096];
    qg_as_cstr(args[1], buf, sizeof(buf));
    return qotrt_bool(qg_add_item((int)qg_as_num(args[0], 0), buf) == 1);
}

V16 qotrt_vg_listbox_add_item(V16 *args, int argc) {
    return qotrt_vg_combobox_add_item(args, argc);
}

V16 qotrt_vg_tabcontrol_add_tab(V16 *args, int argc) {
    return qotrt_vg_combobox_add_item(args, argc);
}

V16 qotrt_vg_combobox_clear(V16 *args, int argc) {
    if (argc < 1) return qotrt_bool(false);
    return qotrt_bool(qg_clear_items((int)qg_as_num(args[0], 0)) == 1);
}

V16 qotrt_vg_listbox_clear(V16 *args, int argc) {
    return qotrt_vg_combobox_clear(args, argc);
}

V16 qotrt_vg_label_set_font_size(V16 *args, int argc) {
    if (argc < 2) return qotrt_bool(false);
    return qotrt_bool(qg_set_font_size((int)qg_as_num(args[0], 0),
                                       (float)qg_as_num(args[1], 15)) == 1);
}

V16 qotrt_vg_button_set_font_size(V16 *args, int argc) {
    return qotrt_vg_label_set_font_size(args, argc);
}

V16 qotrt_vg_textbox_set_font_size(V16 *args, int argc) {
    return qotrt_vg_label_set_font_size(args, argc);
}

V16 qotrt_vg_button_set_border_radius(V16 *args, int argc) {
    if (argc < 2) return qotrt_bool(false);
    return qotrt_bool(qg_set_radius((int)qg_as_num(args[0], 0),
                                    (float)qg_as_num(args[1], 8)) == 1);
}

V16 qotrt_vg_card_set_border_radius(V16 *args, int argc) {
    return qotrt_vg_button_set_border_radius(args, argc);
}

V16 qotrt_vg_progressbar_set_border_radius(V16 *args, int argc) {
    return qotrt_vg_button_set_border_radius(args, argc);
}

V16 qotrt_vg_card_set_elevation(V16 *args, int argc) {
    (void)args; (void)argc;
    return qotrt_bool(true);   /* 阴影强度：wingui 内部按 elevation 处理 */
}

V16 qotrt_vg_spinner_start(V16 *args, int argc) {
    (void)args; (void)argc;
    return qotrt_bool(true);
}

V16 qotrt_vg_spinner_stop(V16 *args, int argc) {
    (void)args; (void)argc;
    return qotrt_bool(true);
}

/* ---- 事件与杂项 ---- */
V16 qotrt_vg_poll_event(V16 *args, int argc) {
    (void)args; (void)argc;
    int id = 0, kind = 0;
    double val = 0.0;
    if (qg_poll_event(&id, &kind, &val) == 0) return qg_null();
    V16 arr = qotrt_arr_new(3);
    QArr *a = (QArr *)arr.u.p;
    qotrt_arr_push(a, qotrt_num((double)id));
    qotrt_arr_push(a, qotrt_num((double)kind));
    qotrt_arr_push(a, qotrt_num(val));
    return arr;
}

V16 qotrt_vg_wait_event(V16 *args, int argc) {
    int tmo = argc > 0 ? (int)qg_as_num(args[0], 15) : 15;
    if (tmo <= 0) tmo = 15;
    int id = 0, kind = 0;
    double val = 0.0;
    if (qg_wait_event(&id, &kind, &val, tmo) == 0) return qg_null();
    V16 arr = qotrt_arr_new(3);
    QArr *a = (QArr *)arr.u.p;
    qotrt_arr_push(a, qotrt_num((double)id));
    qotrt_arr_push(a, qotrt_num((double)kind));
    qotrt_arr_push(a, qotrt_num(val));
    return arr;
}

V16 qotrt_vg_last_error(V16 *args, int argc) {
    (void)args; (void)argc;
    return qotrt_str("", 0);
}

/* ---- v3 · 布局系统 ---- */
V16 qotrt_vg_layout_begin(V16 *args, int argc) {
    if (argc < 2) return qotrt_bool(false);
    char buf[64];
    qg_as_cstr(args[1], buf, sizeof(buf));
    return qotrt_bool(qg_layout_begin((int)qg_as_num(args[0], 0), buf) == 1);
}

V16 qotrt_vg_layout_add(V16 *args, int argc) {
    if (argc < 2) return qotrt_bool(false);
    return qotrt_bool(qg_layout_add((int)qg_as_num(args[0], 0),
                                    (int)qg_as_num(args[1], 0)) == 1);
}

V16 qotrt_vg_layout_spacing(V16 *args, int argc) {
    if (argc < 2) return qotrt_bool(false);
    return qotrt_bool(qg_layout_spacing((int)qg_as_num(args[0], 0),
                                        (float)qg_as_num(args[1], 8.0)) == 1);
}

V16 qotrt_vg_layout_padding(V16 *args, int argc) {
    if (argc < 1) return qotrt_bool(false);
    // 1 参全边 2 参 lr/tb 4 参 l t r b
    double a = argc > 1 ? qg_as_num(args[1], 0) : 0;
    double b = argc > 2 ? qg_as_num(args[2], a) : a;
    double c = argc > 3 ? qg_as_num(args[3], a) : a;
    double d = argc > 4 ? qg_as_num(args[4], b) : b;
    return qotrt_bool(qg_layout_padding((int)qg_as_num(args[0], 0),
                                        (float)a, (float)b, (float)c, (float)d) == 1);
}

V16 qotrt_vg_layout_cols(V16 *args, int argc) {
    if (argc < 2) return qotrt_bool(false);
    return qotrt_bool(qg_layout_cols((int)qg_as_num(args[0], 0),
                                     (int)qg_as_num(args[1], 2)) == 1);
}

V16 qotrt_vg_layout_props(V16 *args, int argc) {
    if (argc < 2) return qotrt_bool(false);
    // margin：数组 [l,t,r,b] 或标量（全边）
    float ml = 0, mt = 0, mr = 0, mb = 0;
    if (args[1].t == VT_ARR && args[1].u.p) {
        QArr *a = (QArr *)args[1].u.p;
        ml = a->len > 0 ? (float)qg_as_num(a->a[0], 0) : 0;
        mt = a->len > 1 ? (float)qg_as_num(a->a[1], 0) : 0;
        mr = a->len > 2 ? (float)qg_as_num(a->a[2], 0) : 0;
        mb = a->len > 3 ? (float)qg_as_num(a->a[3], 0) : 0;
    } else {
        ml = mt = mr = mb = (float)qg_as_num(args[1], 0);
    }
    int fill = argc > 2 ? (int)qg_as_num(args[2], 0) : 0;
    int align = argc > 3 ? (int)qg_as_num(args[3], 0) : 0;
    float weight = argc > 4 ? (float)qg_as_num(args[4], 0) : 0.0f;
    return qotrt_bool(qg_layout_props((int)qg_as_num(args[0], 0),
                                      ml, mt, mr, mb, fill, align, weight) == 1);
}

V16 qotrt_vg_layout_end(V16 *args, int argc) {
    if (argc < 1) return qotrt_bool(false);
    return qotrt_bool(qg_layout_end((int)qg_as_num(args[0], 0)) == 1);
}

/* ---- v3 · 滚动容器与虚拟化列表 ---- */
V16 qotrt_vg_create_scrollpane(V16 *args, int argc) {
    int win = argc > 0 ? (int)qg_as_num(args[0], 0) : 0;
    int x = argc > 1 ? (int)qg_as_num(args[1], 20) : 20;
    int y = argc > 2 ? (int)qg_as_num(args[2], 20) : 20;
    int w = argc > 3 ? (int)qg_as_num(args[3], 200) : 200;
    int h = argc > 4 ? (int)qg_as_num(args[4], 160) : 160;
    int id = qg_create_scrollpane(win, x, y, w, h);
    return id > 0 ? qotrt_num((double)id) : qg_null();
}

V16 qotrt_vg_scroll_content(V16 *args, int argc) {
    if (argc < 2) return qotrt_bool(false);
    return qotrt_bool(qg_scroll_content((int)qg_as_num(args[0], 0),
                                        (float)qg_as_num(args[1], 0)) == 1);
}

V16 qotrt_vg_scroll_attach(V16 *args, int argc) {
    if (argc < 6) return qotrt_bool(false);
    return qotrt_bool(qg_scroll_attach((int)qg_as_num(args[0], 0),
                                       (int)qg_as_num(args[1], 0),
                                       (int)qg_as_num(args[2], 0),
                                       (int)qg_as_num(args[3], 0),
                                       (int)qg_as_num(args[4], 0),
                                       (int)qg_as_num(args[5], 0)) == 1);
}

V16 qotrt_vg_scroll_offset(V16 *args, int argc) {
    if (argc < 1) return qotrt_num(0);
    return qotrt_num(qg_scroll_offset((int)qg_as_num(args[0], 0)));
}

V16 qotrt_vg_scroll_to(V16 *args, int argc) {
    if (argc < 2) return qotrt_bool(false);
    return qotrt_bool(qg_scroll_to((int)qg_as_num(args[0], 0),
                                   (float)qg_as_num(args[1], 0)) == 1);
}

V16 qotrt_vg_create_vlist(V16 *args, int argc) {
    int win = argc > 0 ? (int)qg_as_num(args[0], 0) : 0;
    int x = argc > 1 ? (int)qg_as_num(args[1], 20) : 20;
    int y = argc > 2 ? (int)qg_as_num(args[2], 20) : 20;
    int w = argc > 3 ? (int)qg_as_num(args[3], 200) : 200;
    int h = argc > 4 ? (int)qg_as_num(args[4], 160) : 160;
    int id = qg_create_vlist(win, x, y, w, h);
    return id > 0 ? qotrt_num((double)id) : qg_null();
}

V16 qotrt_vg_list_set_count(V16 *args, int argc) {
    if (argc < 2) return qotrt_bool(false);
    return qotrt_bool(qg_list_set_count((int)qg_as_num(args[0], 0),
                                        (int)qg_as_num(args[1], 0)) == 1);
}

V16 qotrt_vg_list_add_item(V16 *args, int argc) {
    if (argc < 2) return qotrt_bool(false);
    char buf[4096];
    qg_as_cstr(args[1], buf, sizeof(buf));
    return qotrt_bool(qg_list_add_item((int)qg_as_num(args[0], 0), buf) == 1);
}

V16 qotrt_vg_list_draw_count(V16 *args, int argc) {
    if (argc < 1) return qotrt_num(0);
    return qotrt_num((double)qg_list_draw_count((int)qg_as_num(args[0], 0)));
}

V16 qotrt_vg_set_font(V16 *args, int argc) {
    if (argc < 1) return qotrt_bool(false);
    char buf[256];
    qg_as_cstr(args[0], buf, sizeof(buf));
    return qotrt_bool(qg_set_font(buf) == 1);
}

V16 qotrt_vg_set_theme(V16 *args, int argc) {
    if (argc < 2) return qotrt_bool(false);
    return qotrt_bool(qg_set_theme((int)qg_as_num(args[0], 0),
                                   (int)qg_as_num(args[1], 0)) == 1);
}

/* ===========================================================================
 * v2 · 事件回调 / 帧原语 / Canvas 绘制 / 窗口能力（原语化）
 *
 * 机制：C 回调 trampoline + cookie 表。QOT 闭包（VT_CLOSURE）存入本 TU 的
 * 注册表，cookie 作 userdata 传给 qg_*；事件触发时 trampoline 用
 * qotrt_clo_call 反向调用 QOT 闭包 —— 外部语言（原生程序）从此用回调
 * 管理事件，不必轮询。回调线程 = 消息循环线程；异常就地捕获不穿透 C 层。
 * =========================================================================== */
namespace {

struct CloSlot {
    int cookie;
    V16 clo;
    std::string buf;   // 数据回调（vg_list_on_data）返回值缓冲
};
std::vector<CloSlot> g_clo_slots;      // 事件/帧/绘制/数据回调共用
int g_next_cookie = 1;

int clo_slot_put(const V16 &clo) {
    CloSlot s;
    s.cookie = g_next_cookie++;
    s.clo = clo;
    g_clo_slots.push_back(s);
    return s.cookie;
}

CloSlot *clo_slot_get(int cookie) {
    for (auto &s : g_clo_slots)
        if (s.cookie == cookie) return &s;
    return nullptr;
}

// QOT 回调统一签名：fn(id, value, x, y)
void qg_event_trampoline(int id, int kind, double value, int x, int y, void *ud) {
    CloSlot *s = clo_slot_get((int)(intptr_t)ud);
    if (!s) return;
    V16 args[4] = {
        qotrt_num((double)id),
        qotrt_num(value),
        qotrt_num((double)x),
        qotrt_num((double)y),
    };
    try {
        qotrt_clo_call(s->clo, args, 4);
    } catch (...) {
        /* 回调异常不穿透 C 消息循环（与解释器侧口径一致） */
    }
}

// 帧回调签名：fn(dt)
void qg_frame_trampoline(double dt, void *ud) {
    CloSlot *s = clo_slot_get((int)(intptr_t)ud);
    if (!s) return;
    V16 args[1] = { qotrt_num(dt) };
    try {
        qotrt_clo_call(s->clo, args, 1);
    } catch (...) {}
}

// Canvas 绘制回调签名：fn(w, h)
void qg_draw_trampoline(float w, float h, void *ud) {
    CloSlot *s = clo_slot_get((int)(intptr_t)ud);
    if (!s) return;
    V16 args[2] = { qotrt_num((double)w), qotrt_num((double)h) };
    try {
        qotrt_clo_call(s->clo, args, 2);
    } catch (...) {}
}

// v4 owner-draw 回调签名：fn(w, h, state)
void qg_paint_trampoline(float w, float h, unsigned state, void *ud) {
    CloSlot *s = clo_slot_get((int)(intptr_t)ud);
    if (!s) return;
    V16 args[3] = { qotrt_num((double)w), qotrt_num((double)h),
                    qotrt_num((double)state) };
    try {
        qotrt_clo_call(s->clo, args, 3);
    } catch (...) {}
}

const qg_draw_api *dapi() { return qg_draw_api_table(); }

// [r,g,b,a] 数组 → 四分量
bool clo_color(const V16 &v, float *r, float *g, float *b, float *a) {
    return qg_as_color(v, r, g, b, a);
}

}  // namespace

/* ---- 事件绑定（原语）---- */

/* 行数据回调 trampoline（v3 虚拟列表）：fn(row) → 文本；返回 per-cookie
 * 缓冲的 c_str（下次回调前有效——VirtualList 取完即拷入 wstring）。 */
const char *qg_list_data_trampoline_str(int row, void *ud) {
    CloSlot *s = clo_slot_get((int)(intptr_t)ud);
    if (!s) return "";
    V16 args[1] = { qotrt_num((double)row) };
    try {
        V16 ret = qotrt_clo_call(s->clo, args, 1);
        char buf[4096];
        qg_as_cstr(ret, buf, sizeof(buf));
        s->buf = buf;
    } catch (...) {
        s->buf.clear();
    }
    return s->buf.c_str();
}

V16 qotrt_vg_list_on_data(V16 *args, int argc) {
    if (argc < 2) return qotrt_bool(false);
    V16 clo = args[1];
    if (!qotrt_is_clo(clo)) return qotrt_bool(false);
    int cookie = clo_slot_put(clo);
    return qotrt_bool(qg_list_on_data((int)qg_as_num(args[0], 0),
                                      qg_list_data_trampoline_str,
                                      (void *)(intptr_t)cookie) == 1);
}

/* ---- v3 · 模态对话框 / 菜单 / toast ---- */
V16 qotrt_vg_show_dialog(V16 *args, int argc) {
    if (argc < 1) return qotrt_num(-1);
    int parent = argc > 1 ? (int)qg_as_num(args[1], 0) : 0;
    return qotrt_num((double)qg_show_dialog((int)qg_as_num(args[0], 0), parent));
}

V16 qotrt_vg_set_dialog_result(V16 *args, int argc) {
    if (argc < 2) return qotrt_bool(false);
    return qotrt_bool(qg_set_dialog_result((int)qg_as_num(args[0], 0),
                                           (int)qg_as_num(args[1], 0)) == 1);
}

V16 qotrt_vg_create_menu(V16 *args, int argc) {
    (void)args; (void)argc;
    int mid = qg_create_menu();
    return mid > 0 ? qotrt_num((double)mid) : qg_null();
}

V16 qotrt_vg_menu_add_item(V16 *args, int argc) {
    if (argc < 2) return qg_null();
    char buf[512];
    qg_as_cstr(args[1], buf, sizeof(buf));
    int itemId = qg_menu_add_item((int)qg_as_num(args[0], 0), buf);
    return itemId > 0 ? qotrt_num((double)itemId) : qg_null();
}

V16 qotrt_vg_menu_separator(V16 *args, int argc) {
    if (argc < 1) return qotrt_bool(false);
    return qotrt_bool(qg_menu_separator((int)qg_as_num(args[0], 0)) == 1);
}

V16 qotrt_vg_menu_popup(V16 *args, int argc) {
    if (argc < 4) return qotrt_num(0);
    return qotrt_num((double)qg_menu_popup((int)qg_as_num(args[0], 0),
                                           (int)qg_as_num(args[1], 0),
                                           (int)qg_as_num(args[2], 0),
                                           (int)qg_as_num(args[3], 0)));
}

V16 qotrt_vg_menu_destroy(V16 *args, int argc) {
    if (argc < 1) return qotrt_bool(false);
    return qotrt_bool(qg_menu_destroy((int)qg_as_num(args[0], 0)) == 1);
}

V16 qotrt_vg_toast(V16 *args, int argc) {
    if (argc < 2) return qotrt_bool(false);
    char buf[1024];
    qg_as_cstr(args[1], buf, sizeof(buf));
    int ms = argc > 2 ? (int)qg_as_num(args[2], 2200) : 2200;
    return qotrt_bool(qg_toast((int)qg_as_num(args[0], 0), buf, ms) == 1);
}

/* ---- v3 · 系统集成 ---- */
V16 qotrt_vg_clipboard_set(V16 *args, int argc) {
    if (argc < 1) return qotrt_bool(false);
    char buf[8192];
    qg_as_cstr(args[0], buf, sizeof(buf));
    return qotrt_bool(qg_clipboard_set(buf) == 1);
}

V16 qotrt_vg_clipboard_get(V16 *args, int argc) {
    (void)args; (void)argc;
    char buf[8192];
    int n = qg_clipboard_get(buf, sizeof(buf));
    if (n <= 0) return qotrt_str("", 0);
    return qotrt_str(buf, n);
}

V16 qotrt_vg_window_accept_files(V16 *args, int argc) {
    if (argc < 2) return qotrt_bool(false);
    return qotrt_bool(qg_window_accept_files((int)qg_as_num(args[0], 0),
                                             qg_as_bool(args[1]) ? 1 : 0) == 1);
}

V16 qotrt_vg_take_drop_file(V16 *args, int argc) {
    if (argc < 1) return qotrt_str("", 0);
    char buf[4096];
    int n = qg_take_drop_file((int)qg_as_num(args[0], 0), buf, sizeof(buf));
    if (n <= 0) return qotrt_str("", 0);
    return qotrt_str(buf, n);
}

V16 qotrt_vg_taskbar_progress(V16 *args, int argc) {
    if (argc < 3) return qotrt_bool(false);
    return qotrt_bool(qg_taskbar_progress((int)qg_as_num(args[0], 0),
                                          (int)qg_as_num(args[1], 0),
                                          (int)qg_as_num(args[2], 0)) == 1);
}

V16 qotrt_vg_taskbar_state(V16 *args, int argc) {
    if (argc < 2) return qotrt_bool(false);
    return qotrt_bool(qg_taskbar_state((int)qg_as_num(args[0], 0),
                                       (int)qg_as_num(args[1], 0)) == 1);
}

/* ---- v4 · 样式原语 ---- */
V16 qotrt_vg_style_bg(V16 *args, int argc) {
    if (argc < 2) return qotrt_bool(false);
    float r, g, b, a;
    if (!clo_color(args[1], &r, &g, &b, &a)) return qotrt_bool(false);
    return qotrt_bool(qg_style_bg((int)qg_as_num(args[0], 0), r, g, b, a) == 1);
}

V16 qotrt_vg_style_bg_gradient(V16 *args, int argc) {
    if (argc < 4) return qotrt_bool(false);
    float r0, g0, b0, a0, r1, g1, b1, a1;
    if (!clo_color(args[1], &r0, &g0, &b0, &a0)) return qotrt_bool(false);
    if (!clo_color(args[2], &r1, &g1, &b1, &a1)) return qotrt_bool(false);
    int vert = qg_as_bool(args[3]) ? 1 : 0;
    return qotrt_bool(qg_style_bg_gradient((int)qg_as_num(args[0], 0),
                                           r0, g0, b0, a0, r1, g1, b1, a1, vert) == 1);
}

V16 qotrt_vg_style_border(V16 *args, int argc) {
    if (argc < 3) return qotrt_bool(false);
    float r, g, b, a;
    if (!clo_color(args[1], &r, &g, &b, &a)) return qotrt_bool(false);
    float width = (float)qg_as_num(args[2], 1.0);
    return qotrt_bool(qg_style_border((int)qg_as_num(args[0], 0), r, g, b, a, width) == 1);
}

V16 qotrt_vg_style_radius(V16 *args, int argc) {
    if (argc < 2) return qotrt_bool(false);
    return qotrt_bool(qg_style_radius((int)qg_as_num(args[0], 0),
                                      (float)qg_as_num(args[1], 8.0)) == 1);
}

V16 qotrt_vg_style_padding(V16 *args, int argc) {
    if (argc < 5) return qotrt_bool(false);
    return qotrt_bool(qg_style_padding((int)qg_as_num(args[0], 0),
                                       (float)qg_as_num(args[1], 0), (float)qg_as_num(args[2], 0),
                                       (float)qg_as_num(args[3], 0), (float)qg_as_num(args[4], 0)) == 1);
}

V16 qotrt_vg_style_text(V16 *args, int argc) {
    if (argc < 3) return qotrt_bool(false);
    float r, g, b, a;
    if (!clo_color(args[1], &r, &g, &b, &a)) return qotrt_bool(false);
    float size = (float)qg_as_num(args[2], 14.0);
    int bold = argc > 3 && qg_as_bool(args[3]) ? 1 : 0;
    return qotrt_bool(qg_style_text((int)qg_as_num(args[0], 0), r, g, b, a, size, bold) == 1);
}

V16 qotrt_vg_style_state_bg(V16 *args, int argc) {
    if (argc < 3) return qotrt_bool(false);
    float r, g, b, a;
    if (!clo_color(args[2], &r, &g, &b, &a)) return qotrt_bool(false);
    return qotrt_bool(qg_style_state_bg((int)qg_as_num(args[0], 0),
                                        (int)qg_as_num(args[1], 1), r, g, b, a) == 1);
}

V16 qotrt_vg_style_state_text(V16 *args, int argc) {
    if (argc < 3) return qotrt_bool(false);
    float r, g, b, a;
    if (!clo_color(args[2], &r, &g, &b, &a)) return qotrt_bool(false);
    return qotrt_bool(qg_style_state_text((int)qg_as_num(args[0], 0),
                                          (int)qg_as_num(args[1], 1), r, g, b, a) == 1);
}

V16 qotrt_vg_style_reset(V16 *args, int argc) {
    if (argc < 1) return qotrt_bool(false);
    return qotrt_bool(qg_style_reset((int)qg_as_num(args[0], 0)) == 1);
}

V16 qotrt_vg_style_flags(V16 *args, int argc) {
    if (argc < 1) return qotrt_num(0);
    return qotrt_num((double)qg_style_flags((int)qg_as_num(args[0], 0)));
}

V16 qotrt_vg_on_paint(V16 *args, int argc) {
    if (argc < 2) return qotrt_bool(false);
    V16 clo = args[1];
    if (!qotrt_is_clo(clo)) return qotrt_bool(false);
    int cookie = clo_slot_put(clo);
    return qotrt_bool(qg_on_paint((int)qg_as_num(args[0], 0),
                                  qg_paint_trampoline,
                                  (void *)(intptr_t)cookie) == 1);
}

V16 qotrt_vg_toast_style(V16 *args, int argc) {
    if (argc < 5) return qotrt_bool(false);
    int win = (int)qg_as_num(args[0], 0);
    float br, bg2, bb, ba, tr, tg, tb, ta, dr, dg, db, da;
    if (!clo_color(args[1], &br, &bg2, &bb, &ba)) return qotrt_bool(false);
    if (!clo_color(args[2], &tr, &tg, &tb, &ta)) return qotrt_bool(false);
    if (!clo_color(args[3], &dr, &dg, &db, &da)) return qotrt_bool(false);
    float radius = (float)qg_as_num(args[4], 8.0);
    return qotrt_bool(qg_toast_style(win, br, bg2, bb, ba, tr, tg, tb, ta,
                                     dr, dg, db, da, radius) == 1);
}

V16 qotrt_vg_scroll_style(V16 *args, int argc) {
    if (argc < 4) return qotrt_bool(false);
    int id = (int)qg_as_num(args[0], 0);
    float tr, tg, tb, ta, hr, hg, hb, ha;
    if (!clo_color(args[1], &tr, &tg, &tb, &ta)) return qotrt_bool(false);
    if (!clo_color(args[2], &hr, &hg, &hb, &ha)) return qotrt_bool(false);
    float width = (float)qg_as_num(args[3], 8.0);
    return qotrt_bool(qg_scroll_style(id, tr, tg, tb, ta, hr, hg, hb, ha, width) == 1);
}

V16 qotrt_vg_menu_bg(V16 *args, int argc) {
    if (argc < 2) return qotrt_bool(false);
    float r, g, b, a;
    if (!clo_color(args[1], &r, &g, &b, &a)) return qotrt_bool(false);
    return qotrt_bool(qg_menu_bg((int)qg_as_num(args[0], 0), r, g, b, a) == 1);
}

V16 qotrt_vg_menu_hover_bg(V16 *args, int argc) {
    if (argc < 2) return qotrt_bool(false);
    float r, g, b, a;
    if (!clo_color(args[1], &r, &g, &b, &a)) return qotrt_bool(false);
    return qotrt_bool(qg_menu_hover_bg((int)qg_as_num(args[0], 0), r, g, b, a) == 1);
}

V16 qotrt_vg_menu_text_color(V16 *args, int argc) {
    if (argc < 2) return qotrt_bool(false);
    float r, g, b, a;
    if (!clo_color(args[1], &r, &g, &b, &a)) return qotrt_bool(false);
    return qotrt_bool(qg_menu_text_color((int)qg_as_num(args[0], 0), r, g, b, a) == 1);
}

V16 qotrt_vg_menu_border(V16 *args, int argc) {
    if (argc < 3) return qotrt_bool(false);
    float r, g, b, a;
    if (!clo_color(args[1], &r, &g, &b, &a)) return qotrt_bool(false);
    float width = (float)qg_as_num(args[2], 1.0);
    return qotrt_bool(qg_menu_border((int)qg_as_num(args[0], 0), r, g, b, a, width) == 1);
}

V16 qotrt_vg_bind(V16 *args, int argc) {
    if (argc < 3) return qg_null();
    V16 clo = args[2];
    if (!qotrt_is_clo(clo)) return qg_null();
    int cookie = clo_slot_put(clo);
    int token = qg_bind((int)qg_as_num(args[0], 0),
                        (unsigned)qg_as_num(args[1], 0),
                        qg_event_trampoline, (void *)(intptr_t)cookie);
    return token > 0 ? qotrt_num((double)token) : qg_null();
}

V16 qotrt_vg_unbind(V16 *args, int argc) {
    if (argc < 1) return qotrt_bool(false);
    return qotrt_bool(qg_unbind((int)qg_as_num(args[0], 0)) == 1);
}

/* ---- 生命周期/便利绑定糖（kind 见 qotgui_abi.h 的常量表） ---- */
static V16 vg_on_kind_impl(V16 *args, int argc, unsigned bit) {
    if (argc < 2) return qg_null();
    V16 clo = args[1];
    if (!qotrt_is_clo(clo)) return qg_null();
    int cookie = clo_slot_put(clo);
    int token = qg_bind((int)qg_as_num(args[0], 0), bit,
                        qg_event_trampoline, (void *)(intptr_t)cookie);
    return token > 0 ? qotrt_num((double)token) : qg_null();
}

V16 qotrt_vg_on_click(V16 *args, int argc)  { return vg_on_kind_impl(args, argc, QG_BIT_CLICK); }
V16 qotrt_vg_on_change(V16 *args, int argc) { return vg_on_kind_impl(args, argc, QG_BIT_CHANGE); }
V16 qotrt_vg_on_select(V16 *args, int argc) { return vg_on_kind_impl(args, argc, QG_BIT_SELECT); }
V16 qotrt_vg_on_mouse(V16 *args, int argc) {
    // vg_on_mouse(id, kind, fn)：kind = 10..15（按下/抬起/移动/进入/离开/双击）
    if (argc < 3) return qg_null();
    V16 clo = args[2];
    if (!qotrt_is_clo(clo)) return qg_null();
    unsigned bit = (unsigned)qg_as_num(args[1], 0);
    int cookie = clo_slot_put(clo);
    int token = qg_bind((int)qg_as_num(args[0], 0), bit,
                        qg_event_trampoline, (void *)(intptr_t)cookie);
    return token > 0 ? qotrt_num((double)token) : qg_null();
}
V16 qotrt_vg_on_key(V16 *args, int argc)   { return vg_on_kind_impl(args, argc, QG_BIT_KEY_DOWN); }
V16 qotrt_vg_on_text(V16 *args, int argc)  { return vg_on_kind_impl(args, argc, QG_BIT_TEXT); }
V16 qotrt_vg_on_close(V16 *args, int argc) { return vg_on_kind_impl(args, argc, QG_BIT_CLOSE); }
V16 qotrt_vg_on_resize(V16 *args, int argc){ return vg_on_kind_impl(args, argc, QG_BIT_RESIZE); }
V16 qotrt_vg_on_load(V16 *args, int argc)  { return vg_on_kind_impl(args, argc, QG_BIT_LOAD); }
/* v3 新事件绑定糖（kind 16/50/51 —— 追加位，不动 v2 位序） */
V16 qotrt_vg_on_scroll(V16 *args, int argc){ return vg_on_kind_impl(args, argc, QG_BIT_SCROLL); }
V16 qotrt_vg_on_menu(V16 *args, int argc)  { return vg_on_kind_impl(args, argc, QG_BIT_MENU); }
V16 qotrt_vg_on_drop(V16 *args, int argc)  { return vg_on_kind_impl(args, argc, QG_BIT_DROP); }

/* ---- 帧原语 ---- */
V16 qotrt_vg_on_frame(V16 *args, int argc) {
    if (argc < 1) return qg_null();
    V16 clo = args[0];
    if (!qotrt_is_clo(clo)) return qg_null();
    int cookie = clo_slot_put(clo);
    int token = qg_on_frame(qg_frame_trampoline, (void *)(intptr_t)cookie);
    return token > 0 ? qotrt_num((double)token) : qg_null();
}

V16 qotrt_vg_off_frame(V16 *args, int argc) {
    if (argc < 1) return qotrt_bool(false);
    return qotrt_bool(qg_off_frame((int)qg_as_num(args[0], 0)) == 1);
}

V16 qotrt_vg_request_frame(V16 *args, int argc) {
    (void)args; (void)argc;
    qg_request_frame();
    return qotrt_bool(true);
}

V16 qotrt_vg_easing(V16 *args, int argc) {
    if (argc < 2) return qotrt_num(0);
    return qotrt_num((double)qg_easing((int)qg_as_num(args[1], 0),
                                       (float)qg_as_num(args[0], 0.0)));
}

/* ---- 图片资源 ---- */
V16 qotrt_vg_image_load(V16 *args, int argc) {
    if (argc < 1) return qg_null();
    char buf[1024];
    qg_as_cstr(args[0], buf, sizeof(buf));
    int id = qg_image_load(buf);
    return id > 0 ? qotrt_num((double)id) : qg_null();
}

V16 qotrt_vg_image_free(V16 *args, int argc) {
    if (argc < 1) return qotrt_bool(false);
    qg_image_free((int)qg_as_num(args[0], 0));
    return qotrt_bool(true);
}

V16 qotrt_vg_image_size(V16 *args, int argc) {
    if (argc < 1) return qg_null();
    int w = 0, h = 0;
    if (!qg_image_size((int)qg_as_num(args[0], 0), &w, &h)) return qg_null();
    V16 arr = qotrt_arr_new(2);
    QArr *a = (QArr *)arr.u.p;
    qotrt_arr_push(a, qotrt_num((double)w));
    qotrt_arr_push(a, qotrt_num((double)h));
    return arr;
}

/* ---- 窗口合成能力原语 ---- */
V16 qotrt_vg_window_transparent(V16 *args, int argc) {
    if (argc < 2) return qotrt_bool(false);
    return qotrt_bool(qg_set_window_transparent((int)qg_as_num(args[0], 0),
                                                qg_as_bool(args[1]) ? 1 : 0) == 1);
}

V16 qotrt_vg_window_backdrop(V16 *args, int argc) {
    if (argc < 2) return qotrt_bool(false);
    return qotrt_bool(qg_set_window_backdrop((int)qg_as_num(args[0], 0),
                                             qg_as_bool(args[1]) ? 1 : 0,
                                             argc > 2 ? (float)qg_as_num(args[2], 0.5) : 0.5f) == 1);
}

V16 qotrt_vg_set_window_round(V16 *args, int argc) {
    if (argc < 2) return qotrt_bool(false);
    return qotrt_bool(qg_set_window_round((int)qg_as_num(args[0], 0),
                                          qg_as_bool(args[1]) ? 1 : 0) == 1);
}

V16 qotrt_vg_set_window_borderless(V16 *args, int argc) {
    if (argc < 2) return qotrt_bool(false);
    return qotrt_bool(qg_set_window_borderless((int)qg_as_num(args[0], 0),
                                               qg_as_bool(args[1]) ? 1 : 0) == 1);
}

V16 qotrt_vg_set_window_topmost(V16 *args, int argc) {
    if (argc < 2) return qotrt_bool(false);
    return qotrt_bool(qg_set_window_topmost((int)qg_as_num(args[0], 0),
                                            qg_as_bool(args[1]) ? 1 : 0) == 1);
}

V16 qotrt_vg_prevent_close(V16 *args, int argc) {
    if (argc < 2) return qotrt_bool(false);
    return qotrt_bool(qg_prevent_close((int)qg_as_num(args[0], 0),
                                       qg_as_bool(args[1]) ? 1 : 0) == 1);
}

V16 qotrt_vg_window_drag(V16 *args, int argc) {
    if (argc < 1) return qotrt_bool(false);
    return qotrt_bool(qg_window_drag((int)qg_as_num(args[0], 0)) == 1);
}

V16 qotrt_vg_window_minimize(V16 *args, int argc) {
    if (argc < 1) return qotrt_bool(false);
    return qotrt_bool(qg_window_minimize((int)qg_as_num(args[0], 0)) == 1);
}

V16 qotrt_vg_window_maximize(V16 *args, int argc) {
    if (argc < 1) return qotrt_bool(false);
    return qotrt_bool(qg_window_maximize((int)qg_as_num(args[0], 0)) == 1);
}

V16 qotrt_vg_window_restore(V16 *args, int argc) {
    if (argc < 1) return qotrt_bool(false);
    return qotrt_bool(qg_window_restore((int)qg_as_num(args[0], 0)) == 1);
}

V16 qotrt_vg_set_window_bg_rgba(V16 *args, int argc) {
    if (argc < 3) return qotrt_bool(false);
    float r0, g0, b0, a0, r1, g1, b1, a1;
    if (!qg_as_color(args[1], &r0, &g0, &b0, &a0)) return qotrt_bool(false);
    if (argc > 2 && qg_as_color(args[2], &r1, &g1, &b1, &a1)) {
        return qotrt_bool(qg_set_window_bg_rgba((int)qg_as_num(args[0], 0),
                                                r0, g0, b0, a0, r1, g1, b1, a1) == 1);
    }
    return qotrt_bool(qg_set_window_bg_rgba((int)qg_as_num(args[0], 0),
                                            r0, g0, b0, a0, r0, g0, b0, a0) == 1);
}

/* ---- 透明度与动画糖 ---- */
V16 qotrt_vg_set_opacity(V16 *args, int argc) {
    if (argc < 2) return qotrt_bool(false);
    return qotrt_bool(qg_set_opacity((int)qg_as_num(args[0], 0),
                                     (float)qg_as_num(args[1], 1.0)) == 1);
}

V16 qotrt_vg_animate(V16 *args, int argc) {
    if (argc < 6) return qg_null();
    int anim = qg_animate((int)qg_as_num(args[0], 0), (int)qg_as_num(args[1], 0),
                          (float)qg_as_num(args[2], 0), (float)qg_as_num(args[3], 0),
                          (float)qg_as_num(args[4], 200), (int)qg_as_num(args[5], 2));
    return anim > 0 ? qotrt_num((double)anim) : qg_null();
}

V16 qotrt_vg_animate_stop(V16 *args, int argc) {
    if (argc < 1) return qotrt_bool(false);
    return qotrt_bool(qg_animate_stop((int)qg_as_num(args[0], 0)) == 1);
}

/* ---- Canvas 与绘制原语 ---- */
V16 qotrt_vg_create_canvas(V16 *args, int argc) {
    int win = argc > 0 ? (int)qg_as_num(args[0], 0) : 0;
    int x = argc > 1 ? (int)qg_as_num(args[1], 20) : 20;
    int y = argc > 2 ? (int)qg_as_num(args[2], 20) : 20;
    int w = argc > 3 ? (int)qg_as_num(args[3], 200) : 200;
    int h = argc > 4 ? (int)qg_as_num(args[4], 200) : 200;
    int id = qg_create_canvas(win, x, y, w, h);
    return id > 0 ? qotrt_num((double)id) : qg_null();
}

V16 qotrt_vg_canvas_on_draw(V16 *args, int argc) {
    if (argc < 2) return qotrt_bool(false);
    V16 clo = args[1];
    if (!qotrt_is_clo(clo)) return qotrt_bool(false);
    int cookie = clo_slot_put(clo);
    return qotrt_bool(qg_canvas_on_draw((int)qg_as_num(args[0], 0),
                                        qg_draw_trampoline,
                                        (void *)(intptr_t)cookie) == 1);
}

V16 qotrt_vg_create_image(V16 *args, int argc) {
    if (argc < 2) return qg_null();
    int win = (int)qg_as_num(args[0], 0);
    char buf[1024] = "";
    qg_as_cstr(args[1], buf, sizeof(buf));
    int x = argc > 2 ? (int)qg_as_num(args[2], 20) : 20;
    int y = argc > 3 ? (int)qg_as_num(args[3], 20) : 20;
    int w = argc > 4 ? (int)qg_as_num(args[4], 120) : 120;
    int h = argc > 5 ? (int)qg_as_num(args[5], 120) : 120;
    int id = qg_create_image(win, buf, x, y, w, h);
    return id > 0 ? qotrt_num((double)id) : qg_null();
}

/* 绘制原语一族：回调执行期间隐式绑定当前画布（C 侧 g_drawRenderer） */
V16 qotrt_vg_draw_fill_rect(V16 *args, int argc) {
    if (argc < 5) return qotrt_bool(false);
    float r, g, b, a;
    if (!clo_color(args[4], &r, &g, &b, &a)) return qotrt_bool(false);
    dapi()->fill_rect((float)qg_as_num(args[0], 0), (float)qg_as_num(args[1], 0),
                      (float)qg_as_num(args[2], 0), (float)qg_as_num(args[3], 0),
                      r, g, b, a);
    return qotrt_bool(true);
}

V16 qotrt_vg_draw_stroke_rect(V16 *args, int argc) {
    if (argc < 6) return qotrt_bool(false);
    float r, g, b, a;
    if (!clo_color(args[4], &r, &g, &b, &a)) return qotrt_bool(false);
    dapi()->stroke_rect((float)qg_as_num(args[0], 0), (float)qg_as_num(args[1], 0),
                        (float)qg_as_num(args[2], 0), (float)qg_as_num(args[3], 0),
                        r, g, b, a, (float)qg_as_num(args[5], 1.0));
    return qotrt_bool(true);
}

V16 qotrt_vg_draw_fill_round_rect(V16 *args, int argc) {
    if (argc < 6) return qotrt_bool(false);
    float r, g, b, a;
    if (!clo_color(args[5], &r, &g, &b, &a)) return qotrt_bool(false);
    dapi()->fill_round_rect((float)qg_as_num(args[0], 0), (float)qg_as_num(args[1], 0),
                            (float)qg_as_num(args[2], 0), (float)qg_as_num(args[3], 0),
                            (float)qg_as_num(args[4], 4.0), r, g, b, a);
    return qotrt_bool(true);
}

V16 qotrt_vg_draw_stroke_round_rect(V16 *args, int argc) {
    if (argc < 7) return qotrt_bool(false);
    float r, g, b, a;
    if (!clo_color(args[5], &r, &g, &b, &a)) return qotrt_bool(false);
    dapi()->stroke_round_rect((float)qg_as_num(args[0], 0), (float)qg_as_num(args[1], 0),
                              (float)qg_as_num(args[2], 0), (float)qg_as_num(args[3], 0),
                              (float)qg_as_num(args[4], 4.0), r, g, b, a,
                              (float)qg_as_num(args[6], 1.0));
    return qotrt_bool(true);
}

V16 qotrt_vg_draw_fill_ellipse(V16 *args, int argc) {
    if (argc < 5) return qotrt_bool(false);
    float r, g, b, a;
    if (!clo_color(args[4], &r, &g, &b, &a)) return qotrt_bool(false);
    dapi()->fill_ellipse((float)qg_as_num(args[0], 0), (float)qg_as_num(args[1], 0),
                         (float)qg_as_num(args[2], 10), (float)qg_as_num(args[3], 10),
                         r, g, b, a);
    return qotrt_bool(true);
}

V16 qotrt_vg_draw_stroke_ellipse(V16 *args, int argc) {
    if (argc < 6) return qotrt_bool(false);
    float r, g, b, a;
    if (!clo_color(args[4], &r, &g, &b, &a)) return qotrt_bool(false);
    dapi()->stroke_ellipse((float)qg_as_num(args[0], 0), (float)qg_as_num(args[1], 0),
                           (float)qg_as_num(args[2], 10), (float)qg_as_num(args[3], 10),
                           r, g, b, a, (float)qg_as_num(args[5], 1.0));
    return qotrt_bool(true);
}

V16 qotrt_vg_draw_line(V16 *args, int argc) {
    if (argc < 6) return qotrt_bool(false);
    float r, g, b, a;
    if (!clo_color(args[4], &r, &g, &b, &a)) return qotrt_bool(false);
    dapi()->draw_line((float)qg_as_num(args[0], 0), (float)qg_as_num(args[1], 0),
                      (float)qg_as_num(args[2], 0), (float)qg_as_num(args[3], 0),
                      r, g, b, a, (float)qg_as_num(args[5], 1.0));
    return qotrt_bool(true);
}

V16 qotrt_vg_draw_text(V16 *args, int argc) {
    if (argc < 5) return qotrt_bool(false);
    char buf[4096];
    qg_as_cstr(args[0], buf, sizeof(buf));
    float r, g, b, a;
    if (!clo_color(args[4], &r, &g, &b, &a)) return qotrt_bool(false);
    dapi()->draw_text(buf, (float)qg_as_num(args[1], 0), (float)qg_as_num(args[2], 0),
                      (float)qg_as_num(args[3], 14.0), r, g, b, a);
    return qotrt_bool(true);
}

V16 qotrt_vg_draw_measure_text(V16 *args, int argc) {
    if (argc < 2) return qg_null();
    char buf[4096];
    qg_as_cstr(args[0], buf, sizeof(buf));
    float w = 0, h = 0;
    dapi()->measure_text(buf, (float)qg_as_num(args[1], 14.0), &w, &h);
    V16 arr = qotrt_arr_new(2);
    QArr *a = (QArr *)arr.u.p;
    qotrt_arr_push(a, qotrt_num((double)w));
    qotrt_arr_push(a, qotrt_num((double)h));
    return arr;
}

V16 qotrt_vg_draw_gradient_rect(V16 *args, int argc) {
    if (argc < 7) return qotrt_bool(false);
    float r0, g0, b0, a0, r1, g1, b1, a1;
    if (!clo_color(args[4], &r0, &g0, &b0, &a0)) return qotrt_bool(false);
    if (!clo_color(args[5], &r1, &g1, &b1, &a1)) return qotrt_bool(false);
    dapi()->fill_gradient_rect((float)qg_as_num(args[0], 0), (float)qg_as_num(args[1], 0),
                               (float)qg_as_num(args[2], 0), (float)qg_as_num(args[3], 0),
                               r0, g0, b0, a0, r1, g1, b1, a1,
                               (int)qg_as_num(args[6], 1.0));
    return qotrt_bool(true);
}

V16 qotrt_vg_draw_push_clip(V16 *args, int argc) {
    if (argc < 4) return qotrt_bool(false);
    dapi()->push_clip((float)qg_as_num(args[0], 0), (float)qg_as_num(args[1], 0),
                      (float)qg_as_num(args[2], 0), (float)qg_as_num(args[3], 0));
    return qotrt_bool(true);
}

V16 qotrt_vg_draw_pop_clip(V16 *args, int argc) {
    (void)args; (void)argc;
    dapi()->pop_clip();
    return qotrt_bool(true);
}

V16 qotrt_vg_draw_push_translate(V16 *args, int argc) {
    if (argc < 2) return qotrt_bool(false);
    dapi()->push_translate((float)qg_as_num(args[0], 0), (float)qg_as_num(args[1], 0));
    return qotrt_bool(true);
}

V16 qotrt_vg_draw_push_rotate(V16 *args, int argc) {
    if (argc < 3) return qotrt_bool(false);
    dapi()->push_rotate((float)qg_as_num(args[0], 0), (float)qg_as_num(args[1], 0),
                        (float)qg_as_num(args[2], 0));
    return qotrt_bool(true);
}

V16 qotrt_vg_draw_push_scale(V16 *args, int argc) {
    if (argc < 4) return qotrt_bool(false);
    dapi()->push_scale((float)qg_as_num(args[0], 0), (float)qg_as_num(args[1], 0),
                       (float)qg_as_num(args[2], 1.0), (float)qg_as_num(args[3], 1.0));
    return qotrt_bool(true);
}

V16 qotrt_vg_draw_pop_transform(V16 *args, int argc) {
    (void)args; (void)argc;
    dapi()->pop_transform();
    return qotrt_bool(true);
}

V16 qotrt_vg_draw_image(V16 *args, int argc) {
    if (argc < 5) return qotrt_bool(false);
    dapi()->draw_image((int)qg_as_num(args[0], 0),
                       (float)qg_as_num(args[1], 0), (float)qg_as_num(args[2], 0),
                       (float)qg_as_num(args[3], 0), (float)qg_as_num(args[4], 0),
                       argc > 5 ? (float)qg_as_num(args[5], 1.0) : 1.0f);
    return qotrt_bool(true);
}

V16 qotrt_vg_draw_push_blur(V16 *args, int argc) {
    if (argc < 5) return qotrt_bool(false);
    dapi()->push_blur((float)qg_as_num(args[0], 5.0),
                      (float)qg_as_num(args[1], 0), (float)qg_as_num(args[2], 0),
                      (float)qg_as_num(args[3], 0), (float)qg_as_num(args[4], 0));
    return qotrt_bool(true);
}

V16 qotrt_vg_draw_pop_blur(V16 *args, int argc) {
    (void)args; (void)argc;
    dapi()->pop_blur();
    return qotrt_bool(true);
}

/* ===========================================================================
 * v5 · 绘制原语（AOT 侧）
 * =========================================================================== */

V16 qotrt_vg_path_begin(V16 *args, int argc) {
    (void)args; (void)argc;
    return qotrt_bool(qg_path_reset() == 1);
}

V16 qotrt_vg_path_move_to(V16 *args, int argc) {
    if (argc < 2) return qotrt_bool(false);
    return qotrt_bool(qg_path_move_to((float)qg_as_num(args[0]), (float)qg_as_num(args[1])) == 1);
}

V16 qotrt_vg_path_line_to(V16 *args, int argc) {
    if (argc < 2) return qotrt_bool(false);
    return qotrt_bool(qg_path_line_to((float)qg_as_num(args[0]), (float)qg_as_num(args[1])) == 1);
}

V16 qotrt_vg_path_quad_to(V16 *args, int argc) {
    if (argc < 4) return qotrt_bool(false);
    return qotrt_bool(qg_path_quad_to((float)qg_as_num(args[0]), (float)qg_as_num(args[1]),
                                       (float)qg_as_num(args[2]), (float)qg_as_num(args[3])) == 1);
}

V16 qotrt_vg_path_curve_to(V16 *args, int argc) {
    if (argc < 6) return qotrt_bool(false);
    return qotrt_bool(qg_path_bezier_to(
        (float)qg_as_num(args[0]), (float)qg_as_num(args[1]),
        (float)qg_as_num(args[2]), (float)qg_as_num(args[3]),
        (float)qg_as_num(args[4]), (float)qg_as_num(args[5])) == 1);
}

V16 qotrt_vg_path_arc(V16 *args, int argc) {
    if (argc < 6) return qotrt_bool(false);
    return qotrt_bool(qg_path_arc(
        (float)qg_as_num(args[0]), (float)qg_as_num(args[1]),
        (float)qg_as_num(args[2]), (float)qg_as_num(args[3]),
        (float)qg_as_num(args[4]), (float)qg_as_num(args[5])) == 1);
}

V16 qotrt_vg_path_svg_arc(V16 *args, int argc) {
    if (argc < 7) return qotrt_bool(false);
    return qotrt_bool(qg_path_svg_arc(
        (float)qg_as_num(args[0]), (float)qg_as_num(args[1]), (float)qg_as_num(args[2]),
        (int)qg_as_num(args[3], 0), (int)qg_as_num(args[4], 0),
        (float)qg_as_num(args[5]), (float)qg_as_num(args[6])) == 1);
}

V16 qotrt_vg_path_rect(V16 *args, int argc) {
    if (argc < 4) return qotrt_bool(false);
    return qotrt_bool(qg_path_rect(
        (float)qg_as_num(args[0]), (float)qg_as_num(args[1]),
        (float)qg_as_num(args[2]), (float)qg_as_num(args[3])) == 1);
}

V16 qotrt_vg_path_round_rect(V16 *args, int argc) {
    if (argc < 5) return qotrt_bool(false);
    float rr = (float)qg_as_num(args[4], 4);
    return qotrt_bool(qg_path_round_rect(
        (float)qg_as_num(args[0]), (float)qg_as_num(args[1]),
        (float)qg_as_num(args[2]), (float)qg_as_num(args[3]), rr, rr) == 1);
}

V16 qotrt_vg_path_ellipse(V16 *args, int argc) {
    if (argc < 4) return qotrt_bool(false);
    return qotrt_bool(qg_path_ellipse(
        (float)qg_as_num(args[0]), (float)qg_as_num(args[1]),
        (float)qg_as_num(args[2]), (float)qg_as_num(args[3])) == 1);
}

V16 qotrt_vg_path_close(V16 *args, int argc) {
    (void)args; (void)argc;
    return qotrt_bool(qg_path_close() == 1);
}

V16 qotrt_vg_path_fill(V16 *args, int argc) {
    if (argc < 1) return qotrt_bool(false);
    float r, g, b, a;
    if (!clo_color(args[0], &r, &g, &b, &a)) return qotrt_bool(false);
    return qotrt_bool(qg_path_fill(r, g, b, a) == 1);
}

V16 qotrt_vg_path_fill_brush(V16 *args, int argc) {
    if (argc < 1) return qotrt_bool(false);
    return qotrt_bool(qg_path_fill_brush((int)qg_as_num(args[0], 0)) == 1);
}

V16 qotrt_vg_path_stroke(V16 *args, int argc) {
    if (argc < 2) return qotrt_bool(false);
    float r, g, b, a;
    if (!clo_color(args[0], &r, &g, &b, &a)) return qotrt_bool(false);
    return qotrt_bool(qg_path_stroke(r, g, b, a, (float)qg_as_num(args[1], 1.0)) == 1);
}

V16 qotrt_vg_path_stroke_brush(V16 *args, int argc) {
    if (argc < 2) return qotrt_bool(false);
    return qotrt_bool(qg_path_stroke_brush((int)qg_as_num(args[0], 0),
                                           (float)qg_as_num(args[1], 1.0)) == 1);
}

V16 qotrt_vg_path_bounds(V16 *args, int argc) {
    (void)args; (void)argc;
    float x = 0, y = 0, w = 0, h = 0;
    if (qg_path_bounds(&x, &y, &w, &h) != 1) return qg_null();
    // 返回 [x, y, w, h]
    V16 arr = qotrt_arr_new(4);
    QArr *a = static_cast<QArr *>(arr.u.p);
    qotrt_arr_push(a, qotrt_num(x));
    qotrt_arr_push(a, qotrt_num(y));
    qotrt_arr_push(a, qotrt_num(w));
    qotrt_arr_push(a, qotrt_num(h));
    return arr;
}

V16 qotrt_vg_path_hit(V16 *args, int argc) {
    if (argc < 2) return qotrt_bool(false);
    return qotrt_bool(qg_path_hit((float)qg_as_num(args[0]), (float)qg_as_num(args[1])) == 1);
}

V16 qotrt_vg_path_stroke_hit(V16 *args, int argc) {
    if (argc < 3) return qotrt_bool(false);
    return qotrt_bool(qg_path_stroke_hit((float)qg_as_num(args[0]), (float)qg_as_num(args[1]),
                                         (float)qg_as_num(args[2], 1.0)) == 1);
}

V16 qotrt_vg_path_line_cap(V16 *args, int argc) {
    if (argc < 1) return qotrt_bool(false);
    return qotrt_bool(qg_path_line_cap((int)qg_as_num(args[0], 0)) == 1);
}

V16 qotrt_vg_path_line_join(V16 *args, int argc) {
    if (argc < 1) return qotrt_bool(false);
    return qotrt_bool(qg_path_line_join((int)qg_as_num(args[0], 0)) == 1);
}

V16 qotrt_vg_path_miter_limit(V16 *args, int argc) {
    if (argc < 1) return qotrt_bool(false);
    return qotrt_bool(qg_path_miter_limit((float)qg_as_num(args[0], 10.0)) == 1);
}

V16 qotrt_vg_path_dash(V16 *args, int argc) {
    if (argc < 1) return qotrt_bool(false);
    if (args[0].t != VT_ARR || !args[0].u.p) return qotrt_bool(false);
    QArr *arr = (QArr *)args[0].u.p;
    float dashes[64];
    int n = arr->len < 64 ? (int)arr->len : 64;
    for (int i = 0; i < n; ++i)
        dashes[i] = (float)qg_as_num(arr->a[i], 0);
    float phase = argc > 1 ? (float)qg_as_num(args[1], 0) : 0.0f;
    return qotrt_bool(qg_path_dash(n > 0 ? dashes : nullptr, n, phase) == 1);
}

V16 qotrt_vg_brush_solid(V16 *args, int argc) {
    if (argc < 1) return qg_null();
    float r, g, b, a;
    if (!clo_color(args[0], &r, &g, &b, &a)) return qg_null();
    int id = qg_brush_solid(r, g, b, a);
    return id > 0 ? qotrt_num((double)id) : qg_null();
}

// 嵌套停靠点数组 [[pos, [r,g,b,a]], ...] → begin/add_stop×N/end → brush id
// （static 内部链接：不受 extern "C" 链接说明影响）
static int qg_brush_from_stops_impl(const V16 &stops, bool radial, const float *params) {
    if (stops.t != VT_ARR || !stops.u.p) return 0;
    QArr *arr = (QArr *)stops.u.p;
    if (arr->len < 2) return 0;
    if (radial) {
        if (qg_brush_radial_begin(params[0], params[1], params[2], params[3],
                                  params[4], params[5]) != 1)
            return 0;
    } else {
        if (qg_brush_linear_begin(params[0], params[1], params[2], params[3]) != 1)
            return 0;
    }
    for (size_t i = 0; i < arr->len; ++i) {
        const V16 &ent = arr->a[i];
        if (ent.t != VT_ARR || !ent.u.p) continue;
        QArr *pair = (QArr *)ent.u.p;
        if (pair->len < 2) continue;
        float pos = (float)qg_as_num(pair->a[0], 0);
        float r = 0, g = 0, b = 0, a = 1;
        clo_color(pair->a[1], &r, &g, &b, &a);
        qg_brush_add_stop(pos, r, g, b, a);
    }
    return qg_brush_end();
}

V16 qotrt_vg_brush_linear(V16 *args, int argc) {
    if (argc < 5) return qg_null();
    float params[4] = {(float)qg_as_num(args[0]), (float)qg_as_num(args[1]),
                       (float)qg_as_num(args[2]), (float)qg_as_num(args[3])};
    int id = qg_brush_from_stops_impl(args[4], false, params);
    return id > 0 ? qotrt_num((double)id) : qg_null();
}

V16 qotrt_vg_brush_radial(V16 *args, int argc) {
    if (argc < 4) return qg_null();
    float cx = (float)qg_as_num(args[0]), cy = (float)qg_as_num(args[1]);
    float rr = (float)qg_as_num(args[2], 1);
    float params[6] = {cx, cy, cx, cy, rr, rr};
    int id = qg_brush_from_stops_impl(args[3], true, params);
    return id > 0 ? qotrt_num((double)id) : qg_null();
}

V16 qotrt_vg_brush_radial_focus(V16 *args, int argc) {
    if (argc < 7) return qg_null();
    float params[6];
    for (int i = 0; i < 6; ++i)
        params[i] = (float)qg_as_num(args[i]);
    int id = qg_brush_from_stops_impl(args[6], true, params);
    return id > 0 ? qotrt_num((double)id) : qg_null();
}

V16 qotrt_vg_brush_bitmap(V16 *args, int argc) {
    if (argc < 1) return qg_null();
    int id = qg_brush_bitmap((int)qg_as_num(args[0], 0));
    return id > 0 ? qotrt_num((double)id) : qg_null();
}

V16 qotrt_vg_brush_extend(V16 *args, int argc) {
    if (argc < 3) return qotrt_bool(false);
    return qotrt_bool(qg_brush_extend((int)qg_as_num(args[0], 0),
                                      (int)qg_as_num(args[1], 0),
                                      (int)qg_as_num(args[2], 0)) == 1);
}

V16 qotrt_vg_brush_transform(V16 *args, int argc) {
    if (argc < 7) return qotrt_bool(false);
    return qotrt_bool(qg_brush_transform((int)qg_as_num(args[0], 0),
                                         (float)qg_as_num(args[1]), (float)qg_as_num(args[2]),
                                         (float)qg_as_num(args[3]), (float)qg_as_num(args[4]),
                                         (float)qg_as_num(args[5]), (float)qg_as_num(args[6])) == 1);
}

V16 qotrt_vg_brush_free(V16 *args, int argc) {
    if (argc < 1) return qotrt_bool(false);
    qg_brush_free((int)qg_as_num(args[0], 0));
    return qotrt_bool(true);
}

V16 qotrt_vg_draw_fill_rect_brush(V16 *args, int argc) {
    if (argc < 5) return qotrt_bool(false);
    return qotrt_bool(qg_draw_fill_rect_brush((int)qg_as_num(args[0], 0),
        (float)qg_as_num(args[1]), (float)qg_as_num(args[2]),
        (float)qg_as_num(args[3]), (float)qg_as_num(args[4])) == 1);
}

V16 qotrt_vg_draw_stroke_rect_brush(V16 *args, int argc) {
    if (argc < 6) return qotrt_bool(false);
    return qotrt_bool(qg_draw_stroke_rect_brush((int)qg_as_num(args[0], 0),
        (float)qg_as_num(args[1]), (float)qg_as_num(args[2]),
        (float)qg_as_num(args[3]), (float)qg_as_num(args[4]),
        (float)qg_as_num(args[5], 1.0)) == 1);
}

V16 qotrt_vg_draw_fill_round_rect_brush(V16 *args, int argc) {
    if (argc < 6) return qotrt_bool(false);
    return qotrt_bool(qg_draw_fill_round_rect_brush((int)qg_as_num(args[0], 0),
        (float)qg_as_num(args[1]), (float)qg_as_num(args[2]),
        (float)qg_as_num(args[3]), (float)qg_as_num(args[4]),
        (float)qg_as_num(args[5], 4.0)) == 1);
}

V16 qotrt_vg_draw_stroke_round_rect_brush(V16 *args, int argc) {
    if (argc < 7) return qotrt_bool(false);
    return qotrt_bool(qg_draw_stroke_round_rect_brush((int)qg_as_num(args[0], 0),
        (float)qg_as_num(args[1]), (float)qg_as_num(args[2]),
        (float)qg_as_num(args[3]), (float)qg_as_num(args[4]),
        (float)qg_as_num(args[5], 4.0), (float)qg_as_num(args[6], 1.0)) == 1);
}

V16 qotrt_vg_draw_fill_ellipse_brush(V16 *args, int argc) {
    if (argc < 5) return qotrt_bool(false);
    return qotrt_bool(qg_draw_fill_ellipse_brush((int)qg_as_num(args[0], 0),
        (float)qg_as_num(args[1]), (float)qg_as_num(args[2]),
        (float)qg_as_num(args[3]), (float)qg_as_num(args[4])) == 1);
}

V16 qotrt_vg_draw_stroke_ellipse_brush(V16 *args, int argc) {
    if (argc < 6) return qotrt_bool(false);
    return qotrt_bool(qg_draw_stroke_ellipse_brush((int)qg_as_num(args[0], 0),
        (float)qg_as_num(args[1]), (float)qg_as_num(args[2]),
        (float)qg_as_num(args[3]), (float)qg_as_num(args[4]),
        (float)qg_as_num(args[5], 1.0)) == 1);
}

/* ---- v5 · 富文本 ---- */

V16 qotrt_vg_rich_begin(V16 *args, int argc) {
    (void)args; (void)argc;
    return qotrt_bool(qg_rich_begin() == 1);
}

V16 qotrt_vg_rich_add(V16 *args, int argc) {
    if (argc < 3) return qotrt_bool(false);
    char buf[4096];
    qg_as_cstr(args[0], buf, sizeof(buf));
    float r, g, b, a;
    if (!clo_color(args[2], &r, &g, &b, &a)) return qotrt_bool(false);
    int bold = argc > 3 ? (qg_as_bool(args[3]) ? 1 : 0) : 0;
    int italic = argc > 4 ? (qg_as_bool(args[4]) ? 1 : 0) : 0;
    int underline = argc > 5 ? (qg_as_bool(args[5]) ? 1 : 0) : 0;
    int strike = argc > 6 ? (qg_as_bool(args[6]) ? 1 : 0) : 0;
    return qotrt_bool(qg_rich_add(buf, (float)qg_as_num(args[1], 16), r, g, b, a,
                                  bold ? 700 : 400, italic, underline, strike) == 1);
}

V16 qotrt_vg_rich_add_weight(V16 *args, int argc) {
    if (argc < 4) return qotrt_bool(false);
    char buf[4096];
    qg_as_cstr(args[0], buf, sizeof(buf));
    float r, g, b, a;
    if (!clo_color(args[2], &r, &g, &b, &a)) return qotrt_bool(false);
    int weight = (int)qg_as_num(args[3], 400);
    int italic = argc > 4 ? (qg_as_bool(args[4]) ? 1 : 0) : 0;
    int underline = argc > 5 ? (qg_as_bool(args[5]) ? 1 : 0) : 0;
    int strike = argc > 6 ? (qg_as_bool(args[6]) ? 1 : 0) : 0;
    return qotrt_bool(qg_rich_add(buf, (float)qg_as_num(args[1], 16), r, g, b, a,
                                  weight, italic, underline, strike) == 1);
}

V16 qotrt_vg_rich_add_brush(V16 *args, int argc) {
    if (argc < 3) return qotrt_bool(false);
    char buf[4096];
    qg_as_cstr(args[0], buf, sizeof(buf));
    int weight = argc > 3 ? (int)qg_as_num(args[3], 400) : 400;
    int italic = argc > 4 ? (qg_as_bool(args[4]) ? 1 : 0) : 0;
    int underline = argc > 5 ? (qg_as_bool(args[5]) ? 1 : 0) : 0;
    int strike = argc > 6 ? (qg_as_bool(args[6]) ? 1 : 0) : 0;
    return qotrt_bool(qg_rich_add_brush(buf, (float)qg_as_num(args[1], 16),
                                        (int)qg_as_num(args[2], 0),
                                        weight, italic, underline, strike) == 1);
}

V16 qotrt_vg_rich_line_height(V16 *args, int argc) {
    if (argc < 1) return qotrt_bool(false);
    float baseline = argc > 1 ? (float)qg_as_num(args[1], 0) : 0.0f;
    return qotrt_bool(qg_rich_line_height((float)qg_as_num(args[0], 0), baseline) == 1);
}

V16 qotrt_vg_rich_spacing(V16 *args, int argc) {
    if (argc < 4) return qotrt_bool(false);
    return qotrt_bool(qg_rich_spacing((float)qg_as_num(args[0]), (float)qg_as_num(args[1]),
                                      (float)qg_as_num(args[2]), (float)qg_as_num(args[3])) == 1);
}

V16 qotrt_vg_rich_draw(V16 *args, int argc) {
    if (argc < 7) return qotrt_bool(false);
    return qotrt_bool(qg_rich_draw(
        (float)qg_as_num(args[0]), (float)qg_as_num(args[1]),
        (float)qg_as_num(args[2]), (float)qg_as_num(args[3]),
        (int)qg_as_num(args[4], 0), (int)qg_as_num(args[5], 0),
        (int)qg_as_num(args[6], 0)) == 1);
}

V16 qotrt_vg_rich_measure(V16 *args, int argc) {
    if (argc < 2) return qg_null();
    float w = 0, h = 0;
    if (qg_rich_measure((int)qg_as_num(args[0], 0), (float)qg_as_num(args[1]), &w, &h) != 1)
        return qg_null();
    V16 arr = qotrt_arr_new(2);
    QArr *a = static_cast<QArr *>(arr.u.p);
    qotrt_arr_push(a, qotrt_num(w));
    qotrt_arr_push(a, qotrt_num(h));
    return arr;
}

/* ---- v5 · 合成效果层 ---- */

V16 qotrt_vg_draw_push_blend(V16 *args, int argc) {
    if (argc < 1) return qotrt_bool(false);
    float bx = argc > 1 ? (float)qg_as_num(args[1]) : 0.0f;
    float by = argc > 2 ? (float)qg_as_num(args[2]) : 0.0f;
    float bw = argc > 3 ? (float)qg_as_num(args[3]) : 0.0f;
    float bh = argc > 4 ? (float)qg_as_num(args[4]) : 0.0f;
    return qotrt_bool(qg_draw_push_blend((int)qg_as_num(args[0], 0), bx, by, bw, bh) == 1);
}

V16 qotrt_vg_draw_pop_blend(V16 *args, int argc) {
    (void)args; (void)argc;
    return qotrt_bool(qg_draw_pop_blend() == 1);
}

V16 qotrt_vg_draw_push_shadow(V16 *args, int argc) {
    if (argc < 4) return qotrt_bool(false);
    float r, g, b, a;
    if (!clo_color(args[0], &r, &g, &b, &a)) return qotrt_bool(false);
    float bx = argc > 4 ? (float)qg_as_num(args[4]) : 0.0f;
    float by = argc > 5 ? (float)qg_as_num(args[5]) : 0.0f;
    float bw = argc > 6 ? (float)qg_as_num(args[6]) : 0.0f;
    float bh = argc > 7 ? (float)qg_as_num(args[7]) : 0.0f;
    return qotrt_bool(qg_draw_push_shadow(r, g, b, a,
        (float)qg_as_num(args[1], 8), (float)qg_as_num(args[2], 0),
        (float)qg_as_num(args[3], 0), bx, by, bw, bh) == 1);
}

V16 qotrt_vg_draw_pop_shadow(V16 *args, int argc) {
    (void)args; (void)argc;
    return qotrt_bool(qg_draw_pop_shadow() == 1);
}

V16 qotrt_vg_draw_push_mask(V16 *args, int argc) {
    if (argc < 5) return qotrt_bool(false);
    float opacity = argc > 5 ? (float)qg_as_num(args[5], 1) : 1.0f;
    return qotrt_bool(qg_draw_push_mask((int)qg_as_num(args[0], 0),
        (float)qg_as_num(args[1]), (float)qg_as_num(args[2]),
        (float)qg_as_num(args[3]), (float)qg_as_num(args[4]),
        opacity) == 1);
}

V16 qotrt_vg_draw_pop_mask(V16 *args, int argc) {
    (void)args; (void)argc;
    return qotrt_bool(qg_draw_pop_mask() == 1);
}

V16 qotrt_vg_draw_push_clip_path(V16 *args, int argc) {
    (void)args; (void)argc;
    return qotrt_bool(qg_draw_push_clip_path() == 1);
}

/* ---- v5 · 离屏面 ---- */

V16 qotrt_vg_surface(V16 *args, int argc) {
    if (argc < 2) return qg_null();
    int id = qg_surface_create((int)qg_as_num(args[0], 0), (int)qg_as_num(args[1], 0));
    return id > 0 ? qotrt_num((double)id) : qg_null();
}

V16 qotrt_vg_surface_begin(V16 *args, int argc) {
    if (argc < 1) return qotrt_bool(false);
    return qotrt_bool(qg_surface_begin((int)qg_as_num(args[0], 0)) == 1);
}

V16 qotrt_vg_surface_end(V16 *args, int argc) {
    (void)args; (void)argc;
    return qotrt_bool(qg_surface_end() == 1);
}

V16 qotrt_vg_surface_draw(V16 *args, int argc) {
    if (argc < 5) return qotrt_bool(false);
    float alpha = argc > 5 ? (float)qg_as_num(args[5], 1) : 1.0f;
    int interp = argc > 6 ? (int)qg_as_num(args[6], 1) : 1;
    return qotrt_bool(qg_surface_draw((int)qg_as_num(args[0], 0),
        (float)qg_as_num(args[1]), (float)qg_as_num(args[2]),
        (float)qg_as_num(args[3]), (float)qg_as_num(args[4]),
        alpha, interp) == 1);
}

V16 qotrt_vg_surface_pixel(V16 *args, int argc) {
    if (argc < 3) return qg_null();
    float r = 0, g = 0, b = 0, a = 0;
    if (qg_surface_pixel((int)qg_as_num(args[0], 0), (int)qg_as_num(args[1], 0),
                         (int)qg_as_num(args[2], 0), &r, &g, &b, &a) != 1)
        return qg_null();
    V16 arr = qotrt_arr_new(4);
    QArr *pa = static_cast<QArr *>(arr.u.p);
    qotrt_arr_push(pa, qotrt_num(r));
    qotrt_arr_push(pa, qotrt_num(g));
    qotrt_arr_push(pa, qotrt_num(b));
    qotrt_arr_push(pa, qotrt_num(a));
    return arr;
}

V16 qotrt_vg_surface_free(V16 *args, int argc) {
    if (argc < 1) return qotrt_bool(false);
    qg_surface_free((int)qg_as_num(args[0], 0));
    return qotrt_bool(true);
}

/* ---- v5 · 图片精控 + 能力探测 ---- */

V16 qotrt_vg_draw_image_ex(V16 *args, int argc) {
    if (argc < 6) return qotrt_bool(false);
    float alpha = argc > 5 ? (float)qg_as_num(args[5], 1) : 1.0f;
    int interp = argc > 6 ? (int)qg_as_num(args[6], 1) : 1;
    float sx = argc > 7 ? (float)qg_as_num(args[7]) : 0.0f;
    float sy = argc > 8 ? (float)qg_as_num(args[8]) : 0.0f;
    float sw = argc > 9 ? (float)qg_as_num(args[9]) : 0.0f;
    float sh = argc > 10 ? (float)qg_as_num(args[10]) : 0.0f;
    return qotrt_bool(qg_draw_image_ex((int)qg_as_num(args[0], 0),
        (float)qg_as_num(args[1]), (float)qg_as_num(args[2]),
        (float)qg_as_num(args[3]), (float)qg_as_num(args[4]),
        alpha, interp, sx, sy, sw, sh) == 1);
}

V16 qotrt_vg_draw_image_round(V16 *args, int argc) {
    if (argc < 6) return qotrt_bool(false);
    float alpha = argc > 5 ? (float)qg_as_num(args[5], 1) : 1.0f;
    int interp = argc > 6 ? (int)qg_as_num(args[6], 1) : 1;
    float radius = argc > 7 ? (float)qg_as_num(args[7], 0) : 0.0f;
    return qotrt_bool(qg_draw_image_round((int)qg_as_num(args[0], 0),
        (float)qg_as_num(args[1]), (float)qg_as_num(args[2]),
        (float)qg_as_num(args[3]), (float)qg_as_num(args[4]),
        alpha, interp, radius) == 1);
}

V16 qotrt_vg_backend(V16 *args, int argc) {
    (void)args; (void)argc;
    int composed = 0, dpi = 0;
    if (qg_backend_info(&composed, &dpi) != 1) return qg_null();
    V16 arr = qotrt_arr_new(2);
    QArr *a = static_cast<QArr *>(arr.u.p);
    qotrt_arr_push(a, qotrt_num(composed));
    qotrt_arr_push(a, qotrt_num(dpi));
    return arr;
}

/* ---- v3 · 文本富能力 ---- */
V16 qotrt_vg_draw_text_ex(V16 *args, int argc) {
    if (argc < 10) return qotrt_bool(false);
    char buf[4096];
    qg_as_cstr(args[0], buf, sizeof(buf));
    float r, g, b, a;
    if (!clo_color(args[6], &r, &g, &b, &a)) return qotrt_bool(false);
    qg_draw_text_ex(buf,
                    (float)qg_as_num(args[1], 0), (float)qg_as_num(args[2], 0),
                    (float)qg_as_num(args[3], 0), (float)qg_as_num(args[4], 0),
                    (float)qg_as_num(args[5], 14.0),
                    r, g, b, a,
                    (int)qg_as_num(args[7], 0),
                    (int)qg_as_num(args[8], 0),
                    (int)qg_as_num(args[9], 0),
                    argc > 10 ? (int)qg_as_num(args[10], 0) : 0,
                    argc > 11 ? (int)qg_as_num(args[11], 0) : 0);
    return qotrt_bool(true);
}

V16 qotrt_vg_draw_measure_text_ex(V16 *args, int argc) {
    if (argc < 2) return qg_null();
    char buf[4096];
    qg_as_cstr(args[0], buf, sizeof(buf));
    float w = 0, h = 0;
    qg_draw_measure_text_ex(buf,
                            (float)qg_as_num(args[1], 14.0),
                            argc > 2 ? (int)qg_as_num(args[2], 0) : 0,
                            argc > 3 ? (int)qg_as_num(args[3], 0) : 0,
                            argc > 4 ? (float)qg_as_num(args[4], 0) : 0.0f,
                            &w, &h);
    V16 arr = qotrt_arr_new(2);
    QArr *a = (QArr *)arr.u.p;
    qotrt_arr_push(a, qotrt_num((double)w));
    qotrt_arr_push(a, qotrt_num((double)h));
    return arr;
}

/* ===========================================================================
 * v6 · 声明式视觉模板（AOT 侧）
 * =========================================================================== */

V16 qotrt_vg_path_store(V16 *args, int argc) {
    (void)args; (void)argc;
    int id = qg_path_store();
    return id > 0 ? qotrt_num((double)id) : qg_null();
}

V16 qotrt_vg_path_free(V16 *args, int argc) {
    if (argc < 1) return qotrt_bool(false);
    qg_path_free((int)qg_as_num(args[0], 0));
    return qotrt_bool(true);
}

V16 qotrt_vg_geo_hit(V16 *args, int argc) {
    if (argc < 3) return qotrt_bool(false);
    return qotrt_bool(qg_geo_hit((int)qg_as_num(args[0], 0),
                                  (float)qg_as_num(args[1]),
                                  (float)qg_as_num(args[2])) == 1);
}

V16 qotrt_vg_textstyle(V16 *args, int argc) {
    if (argc < 1) return qg_null();
    char fam[256];
    fam[0] = 0;
    if (argc > 5) qg_as_cstr(args[5], fam, sizeof(fam));
    int id = qg_textstyle_create(
        (float)qg_as_num(args[0], 14.0),
        argc > 1 ? (int)qg_as_num(args[1], 400) : 400,
        argc > 2 ? (int)qg_as_num(args[2], 0) : 0,
        argc > 3 ? (int)qg_as_num(args[3], 1) : 1,
        argc > 4 ? (int)qg_as_num(args[4], 0) : 0,
        fam);
    return id > 0 ? qotrt_num((double)id) : qg_null();
}

V16 qotrt_vg_textstyle_free(V16 *args, int argc) {
    if (argc < 1) return qotrt_bool(false);
    qg_textstyle_free((int)qg_as_num(args[0], 0));
    return qotrt_bool(true);
}

V16 qotrt_vg_tpl_create(V16 *args, int argc) {
    (void)args; (void)argc;
    int id = qg_tpl_create();
    return id > 0 ? qotrt_num((double)id) : qg_null();
}

// vg_tpl_part(tpl, name, shape, a, b, c, path_id, fill_kind, fill_color,
//             fill_brush, stroke, inset, textslot, anchor)
V16 qotrt_vg_tpl_part(V16 *args, int argc) {
    if (argc < 3) return qotrt_bool(false);
    char name[128];
    qg_as_cstr(args[1], name, sizeof(name));
    float a = argc > 3 ? (float)qg_as_num(args[3], 0) : 0.0f;
    float b = argc > 4 ? (float)qg_as_num(args[4], 0) : 0.0f;
    float c = argc > 5 ? (float)qg_as_num(args[5], 0) : 0.0f;
    int path_id = argc > 6 ? (int)qg_as_num(args[6], 0) : 0;
    int fill_kind = argc > 7 ? (int)qg_as_num(args[7], 0) : 0;
    float fr = 0, fg = 0, fb = 0, fa = 1;
    if (argc > 8) clo_color(args[8], &fr, &fg, &fb, &fa);   // 容错：null 保持默认
    int fill_brush = argc > 9 ? (int)qg_as_num(args[9], 0) : 0;
    int stroke_set = 0;
    float sr = 0, sg = 0, sb = 0, sa = 1, sw = 1;
    if (argc > 10 && args[10].t == VT_ARR && args[10].u.p) {
        QArr *st = (QArr *)args[10].u.p;
        if (st->len >= 1) { clo_color(st->a[0], &sr, &sg, &sb, &sa); stroke_set = 1; }
        if (st->len >= 2) sw = (float)qg_as_num(st->a[1], 1.0);
    }
    float il = 0, it2 = 0, ir = 0, ib = 0;
    if (argc > 11 && args[11].t == VT_ARR && args[11].u.p) {
        QArr *ins = (QArr *)args[11].u.p;
        if (ins->len > 0) il = (float)qg_as_num(ins->a[0], 0);
        if (ins->len > 1) it2 = (float)qg_as_num(ins->a[1], 0);
        if (ins->len > 2) ir = (float)qg_as_num(ins->a[2], 0);
        if (ins->len > 3) ib = (float)qg_as_num(ins->a[3], 0);
    }
    int textslot = argc > 12 ? (int)qg_as_num(args[12], 0) : 0;
    int anchor = argc > 13 ? (int)qg_as_num(args[13], 0) : 0;
    return qotrt_bool(qg_tpl_part((int)qg_as_num(args[0], 0), name,
                                   (int)qg_as_num(args[2], 0),
                                   a, b, c, path_id, anchor, fill_kind,
                                   fr, fg, fb, fa, fill_brush,
                                   stroke_set, sr, sg, sb, sa, sw,
                                   il, it2, ir, ib, textslot) == 1);
}

// vg_tpl_part_text(tpl, part, ts, halign, valign, margin, color)
V16 qotrt_vg_tpl_part_text(V16 *args, int argc) {
    if (argc < 3) return qotrt_bool(false);
    char part[128];
    qg_as_cstr(args[1], part, sizeof(part));
    int ts = (int)qg_as_num(args[2], 0);
    int ha = argc > 3 ? (int)qg_as_num(args[3], -1) : -1;
    int va = argc > 4 ? (int)qg_as_num(args[4], -1) : -1;
    float ml = 0, mt = 0, mr = 0, mb = 0;
    if (argc > 5 && args[5].t == VT_ARR && args[5].u.p) {
        QArr *mg = (QArr *)args[5].u.p;
        if (mg->len > 0) ml = (float)qg_as_num(mg->a[0], 0);
        if (mg->len > 1) mt = (float)qg_as_num(mg->a[1], 0);
        if (mg->len > 2) mr = (float)qg_as_num(mg->a[2], 0);
        if (mg->len > 3) mb = (float)qg_as_num(mg->a[3], 0);
    }
    int color_set = 0;
    float r = 0, g = 0, b = 0, a = 1;
    if (argc > 6 && args[6].t == VT_ARR && args[6].u.p) {
        clo_color(args[6], &r, &g, &b, &a);
        color_set = 1;
    }
    return qotrt_bool(qg_tpl_part_text((int)qg_as_num(args[0], 0), part, ts,
                                       ha, va, ml, mt, mr, mb,
                                       color_set, r, g, b, a) == 1);
}

// vg_tpl_part_dyn(tpl, part, value_mode, value_src, range, c2)
V16 qotrt_vg_tpl_part_dyn(V16 *args, int argc) {
    if (argc < 4) return qotrt_bool(false);
    char part[128];
    qg_as_cstr(args[1], part, sizeof(part));
    float c2r = 0, c2g = 0, c2b = 0, c2a = 1;
    if (argc > 5) clo_color(args[5], &c2r, &c2g, &c2b, &c2a);   // 兼 [minScale] 单元素
    return qotrt_bool(qg_tpl_part_dyn((int)qg_as_num(args[0], 0), part,
                                      (int)qg_as_num(args[2], 0),
                                      (int)qg_as_num(args[3], 0),
                                      argc > 4 ? (float)qg_as_num(args[4], 0) : 0.0f,
                                      c2r, c2g, c2b, c2a) == 1);
}

// vg_tpl_state(tpl, mask, part, prop, [v0,v1,v2,v3]) 或尾参展开
V16 qotrt_vg_tpl_state(V16 *args, int argc) {
    if (argc < 4) return qotrt_bool(false);
    char part[128];
    part[0] = 0;
    qg_as_cstr(args[2], part, sizeof(part));   // null → 空 = 全部 part
    float v[4] = {0, 0, 0, 0};
    if (argc > 4 && args[4].t == VT_ARR && args[4].u.p) {
        QArr *va = (QArr *)args[4].u.p;
        for (int i = 0; i < 4 && i < (int)va->len; ++i)
            v[i] = (float)qg_as_num(va->a[i], 0);
    } else {
        for (int i = 0; i < 4; ++i)
            if (argc > 4 + i) v[i] = (float)qg_as_num(args[4 + i], 0);
    }
    return qotrt_bool(qg_tpl_state((int)qg_as_num(args[0], 0),
                                   (unsigned)qg_as_num(args[1], 0),
                                   part, (int)qg_as_num(args[3], 0),
                                   v[0], v[1], v[2], v[3]) == 1);
}

V16 qotrt_vg_tpl_transition(V16 *args, int argc) {
    if (argc < 4) return qotrt_bool(false);
    return qotrt_bool(qg_tpl_transition((int)qg_as_num(args[0], 0),
                                        (unsigned)qg_as_num(args[1], 0),
                                        (float)qg_as_num(args[2], 200.0),
                                        (int)qg_as_num(args[3], 0)) == 1);
}

V16 qotrt_vg_tpl_free(V16 *args, int argc) {
    if (argc < 1) return qotrt_bool(false);
    qg_tpl_free((int)qg_as_num(args[0], 0));
    return qotrt_bool(true);
}

V16 qotrt_vg_apply_tpl(V16 *args, int argc) {
    if (argc < 2) return qotrt_bool(false);
    return qotrt_bool(qg_apply_template((int)qg_as_num(args[0], 0),
                                        (int)qg_as_num(args[1], 0)) == 1);
}

V16 qotrt_vg_clear_tpl(V16 *args, int argc) {
    if (argc < 1) return qotrt_bool(false);
    return qotrt_bool(qg_clear_template((int)qg_as_num(args[0], 0)) == 1);
}

V16 qotrt_vg_tpl_default(V16 *args, int argc) {
    if (argc < 1) return qg_null();
    int id = qg_tpl_default((int)qg_as_num(args[0], 0));
    return id > 0 ? qotrt_num((double)id) : qg_null();
}

/* ---- v6 配套 · 表格 / 颜色选择器 / 日期选择器（AOT 侧） ---- */

V16 qotrt_vg_table_add_column(V16 *args, int argc) {
    if (argc < 2) return qotrt_bool(false);
    char title[256];
    qg_as_cstr(args[1], title, sizeof(title));
    return qotrt_bool(qg_table_add_column((int)qg_as_num(args[0], 0), title,
                                          argc > 2 ? (float)qg_as_num(args[2], 0) : 0.0f) == 1);
}

V16 qotrt_vg_table_add_row(V16 *args, int argc) {
    if (argc < 2) return qotrt_bool(false);
    char cells[4096];
    qg_as_cstr(args[1], cells, sizeof(cells));
    return qotrt_bool(qg_table_add_row((int)qg_as_num(args[0], 0), cells) == 1);
}

V16 qotrt_vg_table_set_cell(V16 *args, int argc) {
    if (argc < 4) return qotrt_bool(false);
    char text[1024];
    qg_as_cstr(args[3], text, sizeof(text));
    return qotrt_bool(qg_table_set_cell((int)qg_as_num(args[0], 0),
                                        (int)qg_as_num(args[1], 0),
                                        (int)qg_as_num(args[2], 0), text) == 1);
}

V16 qotrt_vg_table_get_selected(V16 *args, int argc) {
    (void)argc;
    if (argc < 1) return qg_null();
    int row = -1, col = -1;
    if (qg_table_get_selected((int)qg_as_num(args[0], 0), &row, &col) != 1)
        return qg_null();
    V16 arr = qotrt_arr_new(2);
    QArr *a = static_cast<QArr *>(arr.u.p);
    qotrt_arr_push(a, qotrt_num(row));
    qotrt_arr_push(a, qotrt_num(col));
    return arr;
}

V16 qotrt_vg_table_set_selected(V16 *args, int argc) {
    if (argc < 3) return qotrt_bool(false);
    return qotrt_bool(qg_table_set_selected((int)qg_as_num(args[0], 0),
                                            (int)qg_as_num(args[1], 0),
                                            (int)qg_as_num(args[2], 0)) == 1);
}

V16 qotrt_vg_colorpicker_set_rgb(V16 *args, int argc) {
    // 两种形态：[r,g,b] 数组或展开三参（与解释器 handler 对齐）
    if (argc < 2) return qotrt_bool(false);
    float r, g, b;
    if (args[1].t == VT_ARR && args[1].u.p) {
        QArr *a = (QArr *)args[1].u.p;
        r = a->len > 0 ? (float)qg_as_num(a->a[0], 0) : 0.0f;
        g = a->len > 1 ? (float)qg_as_num(a->a[1], 0) : 0.0f;
        b = a->len > 2 ? (float)qg_as_num(a->a[2], 0) : 0.0f;
    } else if (argc >= 4) {
        r = (float)qg_as_num(args[1]);
        g = (float)qg_as_num(args[2]);
        b = (float)qg_as_num(args[3]);
    } else {
        return qotrt_bool(false);
    }
    return qotrt_bool(qg_colorpicker_set_rgb((int)qg_as_num(args[0], 0), r, g, b) == 1);
}

V16 qotrt_vg_colorpicker_get_rgb(V16 *args, int argc) {
    (void)argc;
    if (argc < 1) return qg_null();
    float r = 0, g = 0, b = 0;
    if (qg_colorpicker_get_rgb((int)qg_as_num(args[0], 0), &r, &g, &b) != 1)
        return qg_null();
    V16 arr = qotrt_arr_new(3);
    QArr *a = static_cast<QArr *>(arr.u.p);
    qotrt_arr_push(a, qotrt_num(r));
    qotrt_arr_push(a, qotrt_num(g));
    qotrt_arr_push(a, qotrt_num(b));
    return arr;
}

V16 qotrt_vg_datepicker_set_date(V16 *args, int argc) {
    // 两种形态：[y,m,d] 数组或展开三参
    if (argc < 2) return qotrt_bool(false);
    int y, m, d;
    if (args[1].t == VT_ARR && args[1].u.p) {
        QArr *a = (QArr *)args[1].u.p;
        y = (int)qg_as_num(a->a[0], 0);
        m = a->len > 1 ? (int)qg_as_num(a->a[1], 0) : 0;
        d = a->len > 2 ? (int)qg_as_num(a->a[2], 0) : 0;
    } else if (argc >= 4) {
        y = (int)qg_as_num(args[1]);
        m = (int)qg_as_num(args[2]);
        d = (int)qg_as_num(args[3]);
    } else {
        return qotrt_bool(false);
    }
    return qotrt_bool(qg_datepicker_set_date((int)qg_as_num(args[0], 0), y, m, d) == 1);
}

V16 qotrt_vg_datepicker_get_date(V16 *args, int argc) {
    (void)argc;
    if (argc < 1) return qg_null();
    int y = 0, m = 0, d = 0;
    if (qg_datepicker_get_date((int)qg_as_num(args[0], 0), &y, &m, &d) != 1)
        return qg_null();
    V16 arr = qotrt_arr_new(3);
    QArr *a = static_cast<QArr *>(arr.u.p);
    qotrt_arr_push(a, qotrt_num(y));
    qotrt_arr_push(a, qotrt_num(m));
    qotrt_arr_push(a, qotrt_num(d));
    return arr;
}

}  // extern "C"

