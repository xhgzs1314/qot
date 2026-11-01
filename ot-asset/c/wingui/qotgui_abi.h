/* ===========================================================================
 * qotgui_abi.h —— QOT 图形界面（wingui）的 C ABI
 *
 * 设计：
 *   * 一条实现（C++ wingui / Direct2D），两个宿主（解释器经 ctypes、原生程序
 *     经链接）—— 行为由同一份代码决定，两侧逐位一致。
 *   * 事件走**队列轮询**（qg_poll_event）而不是回调：C 侧无法反向调用各引擎
 *     的函数值，轮询让两侧语义天然一致，也不挑宿主线程模型。
 *   * 字符串一律 UTF-8；颜色一律 r/g/b/a 四个 0~1 浮点；句柄是整数 id。
 * =========================================================================== */
#ifndef QOTGUI_ABI_H
#define QOTGUI_ABI_H

#ifdef __cplusplus
extern "C" {
#endif

#ifdef _WIN32
#define QOTGUI_API __declspec(dllexport)
#else
#define QOTGUI_API __attribute__((visibility("default")))
#endif

/* ---- 生命周期 ---- */
QOTGUI_API int  qg_init(void);
QOTGUI_API void qg_uninit(void);

/* ---- 窗口 ---- */
QOTGUI_API int  qg_create_window(const char *title, int width, int height);
QOTGUI_API int  qg_show_window(int win);
QOTGUI_API int  qg_close_window(int win);
QOTGUI_API int  qg_run(void);                     /* 进入消息循环，全部窗口关闭后返回 */
QOTGUI_API int  qg_set_window_title(int win, const char *title);
QOTGUI_API int  qg_set_window_size(int win, int w, int h);
QOTGUI_API int  qg_set_window_bg(int win, float r0, float g0, float b0,
                                 float r1, float g1, float b1);
QOTGUI_API int  qg_set_theme(int win, int theme);  /* 0 深色 1 浅色 2 蓝 */
QOTGUI_API int  qg_set_font(const char *family);

/* ---- 控件创建（返回控件 id；失败 <= 0）---- */
QOTGUI_API int  qg_create_label(int win, const char *text, int x, int y, int w, int h);
QOTGUI_API int  qg_create_button(int win, const char *text, int x, int y, int w, int h);
QOTGUI_API int  qg_create_checkbox(int win, const char *text, int x, int y, int w, int h);
QOTGUI_API int  qg_create_toggle(int win, const char *text, int x, int y, int w, int h);
QOTGUI_API int  qg_create_textbox(int win, const char *placeholder, int x, int y, int w, int h);
QOTGUI_API int  qg_create_slider(int win, int x, int y, int w, int h);
QOTGUI_API int  qg_create_progressbar(int win, int x, int y, int w, int h);
QOTGUI_API int  qg_create_card(int win, const char *title, int x, int y, int w, int h);
QOTGUI_API int  qg_create_combobox(int win, int x, int y, int w, int h);
QOTGUI_API int  qg_create_listbox(int win, int x, int y, int w, int h);
QOTGUI_API int  qg_create_tabcontrol(int win, int x, int y, int w, int h);
QOTGUI_API int  qg_create_spinner(int win, int x, int y, int w, int h);

/* ---- 通用属性 ---- */
QOTGUI_API int  qg_set_text(int id, const char *text);
QOTGUI_API int  qg_get_text(int id, char *buf, int buflen);   /* 返回写入长度 */
QOTGUI_API int  qg_set_value(int id, double v);               /* 滑块/进度条/勾选 */
QOTGUI_API double qg_get_value(int id);
QOTGUI_API int  qg_set_pos(int id, int x, int y);
QOTGUI_API int  qg_set_size(int id, int w, int h);
QOTGUI_API int  qg_set_visible(int id, int visible);
QOTGUI_API int  qg_set_enabled(int id, int enabled);
QOTGUI_API int  qg_set_color(int id, float r, float g, float b, float a);
QOTGUI_API int  qg_set_text_color(int id, float r, float g, float b, float a);
QOTGUI_API int  qg_set_font_size(int id, float size);
QOTGUI_API int  qg_set_radius(int id, float radius);
QOTGUI_API int  qg_add_item(int id, const char *item);        /* 组合框/列表框/选项卡 */
QOTGUI_API int  qg_clear_items(int id);
QOTGUI_API int  qg_destroy(int id);

/* ---- 事件队列 ---- */
/* kind: 1 点击 2 改变（滑块/勾选/切换）3 选中（组合/列表/选项卡）
 * 有事件返回 1 并写出 id/kind/value；无事件返回 0 */
QOTGUI_API int  qg_poll_event(int *id, int *kind, double *value);

/* 等待事件（推荐主循环用）：泵消息直至有事件或超时。空闲时 Sleep(1)，
 * CPU 占用近零；返回 1 并写出 id/kind/value；超时返回 0；
 * 窗口全部关闭（WM_QUIT）→ 返回 1 且 *kind = 9。 */
QOTGUI_API int  qg_wait_event(int *id, int *kind, double *value, int timeout_ms);

#ifdef __cplusplus
}
#endif
#endif /* QOTGUI_ABI_H */