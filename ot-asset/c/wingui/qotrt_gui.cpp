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
        strcmp(kind, "spinner") == 0) {
        int x = argc > 1 ? (int)qg_as_num(args[1], 50) : 50;
        int y = argc > 2 ? (int)qg_as_num(args[2], 50) : 50;
        int w = argc > 3 ? (int)qg_as_num(args[3], 160) : 160;
        int h = argc > 4 ? (int)qg_as_num(args[4], 28) : 28;
        if (strcmp(kind, "slider") == 0)      return qg_create_slider(win, x, y, w, h);
        if (strcmp(kind, "progressbar") == 0) return qg_create_progressbar(win, x, y, w, h);
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

}  // extern "C"

