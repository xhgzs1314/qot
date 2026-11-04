/* ===========================================================================
 * qotgui_abi.h —— QOT 图形界面（wingui）的 C ABI
 *
 * 设计：
 *   * 一条实现（C++ wingui / DirectComposition + Direct2D），两个宿主（解释
 *     器经 ctypes、原生程序经链接）—— 行为由同一份代码决定，两侧逐位一致。
 *   * 事件双通道（v2）：
 *       - 队列轮询：qg_poll_event / qg_wait_event（v1 语义，向后兼容）；
 *       - 回调注册：qg_bind(id, mask, fn, userdata) —— 任意宿主语言都能用
 *         函数指针 + userdata 管理事件（ctypes CFUNCTYPE / AOT trampoline），
 *         不再依赖轮询。回调经事件队列延迟一跳同步派发（消息泵空闲点），
 *         回调线程 = 消息循环线程，回调内可直接调用任意 qg_* 接口。
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

/* ---- 事件 kind 常量（队列与回调共用） ---- */
#define QG_EVT_CLICK       1   /* 点击（按钮/卡片）                  */
#define QG_EVT_CHANGE      2   /* 值改变（滑块/勾选/开关）           */
#define QG_EVT_SELECT      3   /* 选中（组合/列表/选项卡）           */
#define QG_EVT_ANIM_DONE   8   /* 属性动画完成（value = 动画 id）    */
#define QG_EVT_QUIT        9   /* 窗口全部关闭                       */
#define QG_EVT_MOUSE_DOWN 10
#define QG_EVT_MOUSE_UP   11
#define QG_EVT_MOUSE_MOVE 12
#define QG_EVT_ENTER      13
#define QG_EVT_LEAVE      14
#define QG_EVT_DBL_CLICK  15
#define QG_EVT_SCROLL     16   /* v3：滚动（value = 当前偏移）        */
#define QG_EVT_KEY_DOWN   20   /* value = 虚拟键码                   */
#define QG_EVT_CHAR       21   /* value = 字符码                     */
#define QG_EVT_TEXT       22   /* 文本框内容实时改变                 */
#define QG_EVT_CLOSE      30   /* 窗口收到关闭请求（可用 vg_prevent_close 否决） */
#define QG_EVT_RESIZE     31   /* value 无意义，x/y = 新客户区宽高   */
#define QG_EVT_FOCUS      32
#define QG_EVT_BLUR       33
#define QG_EVT_LOAD       40   /* 窗口首次显示 */
#define QG_EVT_MENU       50   /* v3：菜单命令（value = 选中项 id；0 = 取消） */
#define QG_EVT_DROP       51   /* v3：拖放文件（value = 文件数，x/y = 落点客户坐标） */
#define QG_EVT_STATE      52   /* v6：控件状态位变化（value = 新状态位全量） */

/* ---- 绑定掩码位（kind 对应的固定 bit） ---- */
#define QG_BIT_CLICK      0x00001u
#define QG_BIT_CHANGE     0x00002u
#define QG_BIT_SELECT     0x00004u
#define QG_BIT_ANIM_DONE  0x00008u
#define QG_BIT_QUIT       0x00010u
#define QG_BIT_MOUSE_DOWN 0x00020u
#define QG_BIT_MOUSE_UP   0x00040u
#define QG_BIT_MOUSE_MOVE 0x00080u
#define QG_BIT_ENTER      0x00100u
#define QG_BIT_LEAVE      0x00200u
#define QG_BIT_DBL_CLICK  0x00400u
#define QG_BIT_KEY_DOWN   0x00800u
#define QG_BIT_CHAR       0x01000u
#define QG_BIT_TEXT       0x02000u
#define QG_BIT_CLOSE      0x04000u
#define QG_BIT_RESIZE     0x08000u
#define QG_BIT_FOCUS      0x10000u
#define QG_BIT_BLUR       0x20000u
#define QG_BIT_LOAD       0x40000u
/* v3 新增 kind 的位（追加高位，不动 v2 已发布位序 —— 兼容铁律） */
#define QG_BIT_SCROLL     0x80000u   /* kind 16 滚动        */
#define QG_BIT_MENU       0x100000u  /* kind 50 菜单命令    */
#define QG_BIT_DROP       0x200000u  /* kind 51 拖放文件    */
#define QG_BIT_STATE      0x400000u  /* kind 52 状态位变化（v6） */
#define QG_BIT_ALL        0x7FFFFFu

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
QOTGUI_API int  qg_window_client_size(int win, int *w, int *h);   /* v3：客户区读回 */
QOTGUI_API int  qg_set_window_bg(int win, float r0, float g0, float b0,
                                 float r1, float g1, float b1);
QOTGUI_API int  qg_set_window_bg_rgba(int win, float r0, float g0, float b0, float a0,
                                      float r1, float g1, float b1, float a1);
QOTGUI_API int  qg_set_theme(int win, int theme);  /* 0 深色 1 浅色 2 蓝 */
QOTGUI_API int  qg_set_font(const char *family);

/* ---- 窗口合成能力原语（高定制化的基石；不支持的平台静默返回 0） ----
 * 哲学：只给能力开关，不做效果封装。
 *   qg_set_window_transparent —— 窗口本体重定向表面透明（开发者自行绘制
 *                                半透明内容 = 任意自制玻璃/悬浮效果）；
 *   qg_set_window_backdrop    —— 系统合成器背景模糊原语（模糊的是桌面，
 *                                绘制原语做不到，故为窗口级原语）；
 *   两者 + 绘制层 push_blur 可自由组合出亚克力/窗面模糊/混合材质。 */
QOTGUI_API int  qg_set_window_transparent(int win, int on);
QOTGUI_API int  qg_set_window_backdrop(int win, int on, float tint_alpha); /* 系统背景模糊 */
QOTGUI_API int  qg_set_window_round(int win, int on);                 /* Win11 圆角 */
QOTGUI_API int  qg_set_window_borderless(int win, int on);            /* 无边框（保留边缘缩放） */
QOTGUI_API int  qg_set_window_topmost(int win, int on);
QOTGUI_API int  qg_prevent_close(int win, int on);                    /* 收到 close 事件后是否否决关闭 */
QOTGUI_API int  qg_window_drag(int win);                              /* 客户区按下时启动系统拖动 */
QOTGUI_API int  qg_window_minimize(int win);
QOTGUI_API int  qg_window_maximize(int win);
QOTGUI_API int  qg_window_restore(int win);

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
QOTGUI_API int  qg_create_canvas(int win, int x, int y, int w, int h);
QOTGUI_API int  qg_create_image(int win, const char *path, int x, int y, int w, int h);

/* ---- v6 配套：表格 / 颜色选择器 / 日期选择器 ----
 * Table：列（title+width，width<=0 均分剩余）+ 行（';' 分隔单元格串）；
 *   单击行选中 → kind=3 SELECT（value=行索引，x=列索引）；滚轮滚动；
 *   容器框吃 v6 模板（bg part），表头/网格/行文本动态结构代码绘制。
 * ColorPicker：SV 方块（横饱和度/纵明度）+ 色相条 + 当前色预览；
 *   拖拽实时 → kind=2 CHANGE（value = 0xRRGGBB packed）。
 * DatePicker：月历（头部翻月 + 星期行 + 6x7 日格，今天描边、选中填充）；
 *   点选 → kind=2 CHANGE（value = YYYYMMDD packed，如 20261103）。 */
QOTGUI_API int  qg_create_table(int win, int x, int y, int w, int h);
QOTGUI_API int  qg_table_add_column(int id, const char *title, float width);
QOTGUI_API int  qg_table_add_row(int id, const char *cells);   /* ';' 分隔 */
QOTGUI_API int  qg_table_set_cell(int id, int row, int col, const char *text);
QOTGUI_API int  qg_table_get_selected(int id, int *row, int *col);
QOTGUI_API int  qg_table_set_selected(int id, int row, int col);
QOTGUI_API int  qg_create_colorpicker(int win, int x, int y, int w, int h);
QOTGUI_API int  qg_colorpicker_set_rgb(int id, float r, float g, float b);
QOTGUI_API int  qg_colorpicker_get_rgb(int id, float *r, float *g, float *b);
QOTGUI_API int  qg_create_datepicker(int win, int x, int y, int w, int h);
QOTGUI_API int  qg_datepicker_set_date(int id, int y, int m, int d);
QOTGUI_API int  qg_datepicker_get_date(int id, int *y, int *m, int *d);

/* ---- 通用属性 ---- */
QOTGUI_API int  qg_set_text(int id, const char *text);
QOTGUI_API int  qg_get_text(int id, char *buf, int buflen);   /* 返回写入长度 */
QOTGUI_API int  qg_set_value(int id, double v);               /* 滑块/进度条/勾选 */
QOTGUI_API double qg_get_value(int id);
QOTGUI_API int  qg_set_pos(int id, int x, int y);
QOTGUI_API int  qg_set_size(int id, int w, int h);
QOTGUI_API int  qg_get_pos(int id, int *x, int *y);           /* v3：读回左上角 */
QOTGUI_API int  qg_get_size(int id, int *w, int *h);          /* v3：读回尺寸 */
QOTGUI_API int  qg_set_visible(int id, int visible);
QOTGUI_API int  qg_set_enabled(int id, int enabled);
QOTGUI_API int  qg_set_color(int id, float r, float g, float b, float a);
QOTGUI_API int  qg_set_text_color(int id, float r, float g, float b, float a);
QOTGUI_API int  qg_set_font_size(int id, float size);
QOTGUI_API int  qg_set_radius(int id, float radius);
QOTGUI_API int  qg_set_opacity(int id, float a);              /* 组件级透明度 0~1 */
QOTGUI_API int  qg_add_item(int id, const char *item);        /* 组合框/列表框/选项卡 */
QOTGUI_API int  qg_clear_items(int id);
QOTGUI_API int  qg_destroy(int id);

/* ---- 布局系统（v3 ·  1）----
 * 方案 A：布局管理器（不引入容器组件）。vg_layout_begin 声明模式并
 * 清空条目表；vg_layout_add 按布局顺序登记控件；vg_layout_end 立即
 * 重排。窗口 resize 时自动重排（ModernWindow::OnResize → RelayoutNow）。
 * 模式：vbox / hbox / grid / absolute（= 关闭布局，恢复绝对坐标）。
 * 绝对坐标与布局共存：未登记进布局的控件不受影响。
 * 控件属性（vg_layout_props，可在 add 前设置）：
 *   margin [l,t,r,b] 或标量全边；fill 0 自然 1 交叉轴拉伸 2 全拉伸；
 *   align 0 起 1 中 2 末（fill>=1 时忽略）；weight 主轴伸展权重。 */
QOTGUI_API int  qg_layout_begin(int win, const char *mode);
QOTGUI_API int  qg_layout_add(int win, int id);
QOTGUI_API int  qg_layout_spacing(int win, float n);
QOTGUI_API int  qg_layout_padding(int win, float l, float t, float r, float b);
QOTGUI_API int  qg_layout_cols(int win, int n);
QOTGUI_API int  qg_layout_props(int id, float ml, float mt, float mr, float mb,
                                int fill, int align, float weight);
QOTGUI_API int  qg_layout_end(int win);

/* ---- 滚动容器与虚拟化列表（v3 ·  2）----
 * ScrollPane：内容虚高 + 滚动偏移；滚轮/拖拽滚动；子组件 attach 进去
 * （坐标 = 内容坐标），绘制裁剪在视口内。滚轮发 kind=16 事件（value=偏移）。
 * VirtualList：数据条数与可视项解耦——万行只画可视窗口内的行；
 * 行文本回调式（vg_list_on_data，fn(row) → 文本）或数组式（vg_list_add_item）；
 * vg_list_draw_count 累计已绘制行数，脚本用它自证虚拟化。 */
QOTGUI_API int  qg_create_scrollpane(int win, int x, int y, int w, int h);
QOTGUI_API int  qg_scroll_content(int id, float content_h);
QOTGUI_API int  qg_scroll_attach(int id, int child_id, int x, int y, int w, int h);
QOTGUI_API double qg_scroll_offset(int id);
QOTGUI_API int  qg_scroll_to(int id, float offset);
QOTGUI_API int  qg_create_vlist(int win, int x, int y, int w, int h);
QOTGUI_API int  qg_list_set_count(int id, int n);
QOTGUI_API int  qg_list_add_item(int id, const char *text);
QOTGUI_API int  qg_list_draw_count(int id);
/* 行数据回调：fn(row) → UTF-8 文本；返回指针须在下次回调前有效。 */
typedef const char *(*qg_list_data_fn)(int row, void *userdata);
QOTGUI_API int  qg_list_on_data(int id, qg_list_data_fn cb, void *userdata);

/* ---- 模态对话框（v3 ·  3）----
 * 独立窗口 + 禁用父窗口 + 该窗口自己的消息循环（阻塞至对话框关闭，
 * 返回结果码）。回调通道在模态循环里照常派发；父窗口被 EnableWindow
 * 挡住输入。嵌套对话框（对话框再弹对话框）天然支持。 */
QOTGUI_API int  qg_show_dialog(int win, int parent);      /* 返回结果码 */
QOTGUI_API int  qg_set_dialog_result(int win, int code);  /* 回调内设置返回值 */

/* ---- 右键菜单（v3  3 → v4 自绘化）----
 * v4：数据（MenuDef）与呈现（自绘无边框 popup 窗口）分离——样式可定制
 * （qg_menu_bg / qg_menu_hover_bg / qg_menu_text_color / qg_menu_border），
 * 未设置走默认深色主题。同步语义保持：popup 阻塞至选择/取消，返回选中项
 * id（0 = 取消），同时发 kind=50（value = 选中项 id，x/y = 弹出点客户坐标）。
 * popup 自绘菜单支持 hover 高亮、键盘 ↑↓/Enter/ESC、失焦取消、屏幕边缘
 * 防越界。qg_menu_popup 的 x/y = 客户区坐标（内部转屏幕）。 */
QOTGUI_API int  qg_create_menu(void);
QOTGUI_API int  qg_menu_add_item(int mid, const char *text);  /* 返回项 id（>0） */
QOTGUI_API int  qg_menu_separator(int mid);
QOTGUI_API int  qg_menu_popup(int mid, int win, int x, int y);
QOTGUI_API int  qg_menu_destroy(int mid);
QOTGUI_API int  qg_menu_bg(int mid, float r, float g, float b, float a);
QOTGUI_API int  qg_menu_hover_bg(int mid, float r, float g, float b, float a);
QOTGUI_API int  qg_menu_text_color(int mid, float r, float g, float b, float a);
QOTGUI_API int  qg_menu_border(int mid, float r, float g, float b, float a, float width);

/* ---- Toast 自绘轻提示（v3 ·  3）：底部居中卡片，自动淡出 ----
 * v4：qg_toast_style 定制背景/文字/描边颜色与圆角。 */
QOTGUI_API int  qg_toast(int win, const char *text, int ms);
QOTGUI_API int  qg_toast_style(int win,
                               float bgR, float bgG, float bgB, float bgA,
                               float tR, float tG, float tB, float tA,
                               float bR, float bG, float bB, float bA,
                               float radius);

/* ---- 样式原语（v4 · 通用组件样式 + 绘制接管）----
 * 叠加层语义：未设置的属性走组件默认，零定制 = 行为与旧版逐位一致。
 * which/state（状态）：1 hover / 2 pressed / 3 disabled / 4 focused；
 * 状态色解析优先级 disabled > pressed > focused > hover。
 * qg_on_paint（owner-draw）：注册后组件默认绘制整体被接管，回调坐标 =
 * 组件本地原点（0,0 = 组件左上），期间 vg_draw_* 可用；输入语义（点击/
 * 拖拽/滚轮）不变。容器类（Card/ScrollPane/VirtualList）不支持接管，
 * 返回 0（其样式叠加仍可用）。qg_style_flags 返回当前状态位。 */
typedef void (*qg_paint_fn)(float w, float h, unsigned state, void *userdata);
QOTGUI_API int      qg_style_bg(int id, float r, float g, float b, float a);
QOTGUI_API int      qg_style_bg_gradient(int id, float r0, float g0, float b0, float a0,
                                         float r1, float g1, float b1, float a1, int vertical);
QOTGUI_API int      qg_style_border(int id, float r, float g, float b, float a, float width);
QOTGUI_API int      qg_style_radius(int id, float radius);
QOTGUI_API int      qg_style_padding(int id, float l, float t, float r, float b);
QOTGUI_API int      qg_style_text(int id, float r, float g, float b, float a,
                                  float size, int bold);
QOTGUI_API int      qg_style_state_bg(int id, int which, float r, float g, float b, float a);
QOTGUI_API int      qg_style_state_text(int id, int which, float r, float g, float b, float a);
QOTGUI_API int      qg_style_reset(int id);
QOTGUI_API unsigned qg_style_flags(int id);
QOTGUI_API int      qg_on_paint(int id, qg_paint_fn cb, void *userdata);
/* 滚动条样式原语（v4）：轨道/把手颜色 + 宽度（作用于 ScrollPane） */
QOTGUI_API int      qg_scroll_style(int id, float tR, float tG, float tB, float tA,
                                    float hR, float hG, float hB, float hA, float width);

/* ---- 文本富能力（v3 ·  4）----
 * qg_draw_text_ex：对齐/粗体/自动换行/省略号齐全版（旧 qg_draw_text 保留
 * 转发，兼容）。halign/valign = 0 起 1 中 2 末；wrap=自动换行；
 * ellipsis=超宽尾部省略号（须配 wrap 与有效 w）。
 * qg_draw_measure_text_ex：换行后的实际宽高测量（布局系统的行高依据）。 */
QOTGUI_API void qg_draw_text_ex(const char *utf8, float x, float y, float w, float h,
                                float size, float r, float g, float b, float a,
                                int bold, int halign, int valign, int wrap, int ellipsis);
QOTGUI_API void qg_draw_measure_text_ex(const char *utf8, float size, int bold,
                                        int wrap, float maxw,
                                        float *out_w, float *out_h);

/* ---- 系统集成（v3 ·  5）----
 * 剪贴板：CF_UNICODETEXT 直通（UTF-8 传入传出）；qg_clipboard_get 返回
 * 写入长度（0 = 无文本）。
 * 拖放：vg_window_accept_files 开关 → 文件落入窗口发 kind=51（value=文件数，
 * x/y=落点客户坐标）→ 回调里立刻 qg_take_drop_file(i, buf, len) 逐个取路径
 * （i < 文件数；暂存批次 = 最近一次落盘事件，回调后可能被新事件覆盖——
 * 这是设计定稿：不扩展事件结构，一次性队列 API 取批次）。
 * 任务栏进度：ITaskbarList3（COM 单例，qg_uninit 释放）；
 * state: 0 无 1 不确定 2 正常 3 错误 4 暂停。 */
QOTGUI_API int  qg_clipboard_set(const char *utf8);
QOTGUI_API int  qg_clipboard_get(char *buf, int buflen);
QOTGUI_API int  qg_window_accept_files(int win, int on);
QOTGUI_API int  qg_take_drop_file(int index, char *buf, int buflen);
QOTGUI_API int  qg_taskbar_progress(int win, int done, int total);
QOTGUI_API int  qg_taskbar_state(int win, int state);

/* ---- 事件回调（v2 · 推式绑定） ----
 * 任意宿主语言经函数指针 + userdata 管理事件，不必轮询。
 * cb 在消息循环线程同步调用（消息泵空闲点，经队列延迟一跳）；
 * cb 内可直接调用任意 qg_* 接口。id = 0 表示绑定所有来源。 */
typedef void (*qg_event_fn)(int id, int kind, double value, int x, int y, void *userdata);
QOTGUI_API int  qg_bind(int id, unsigned kinds_mask, qg_event_fn cb, void *userdata); /* 返回 token */
QOTGUI_API int  qg_unbind(int token);

/* ---- 事件队列（v1 轮询，向后兼容；与回调可并存） ----
 * kind 见 QG_EVT_*；有事件返回 1；无事件返回 0；全关 kind = 9。 */
QOTGUI_API int  qg_poll_event(int *id, int *kind, double *value);
QOTGUI_API int  qg_wait_event(int *id, int *kind, double *value, int timeout_ms);

/* ---- 帧循环原语（动画的根基，requestAnimationFrame 模型） ----
 * qg_on_frame   —— 注册每帧回调（消息循环线程，绘制前同步调用，dt 秒）；
 * qg_request_frame —— 请求下一帧。帧不被自动续期：回调里还需要动画就再
 *                     请求。不请求 + 无动画 + 无输入 = 零开销待机。
 * 任意属性、任意缓动、任意编排由开发者在此回调内组合 —— 框架不设上限。 */
typedef void (*qg_frame_fn)(double dt, void *userdata);
QOTGUI_API int   qg_on_frame(qg_frame_fn cb, void *userdata);  /* 返回 token */
QOTGUI_API int   qg_off_frame(int token);
QOTGUI_API void  qg_request_frame(void);

/* ---- 缓动纯函数（12 种；t∈[0,1] → 映射值，组合动画时随手用） ----
 * kind: 0 Linear 1 QuadIn 2 QuadOut 3 QuadInOut 4 CubicIn 5 CubicOut
 *       6 CubicInOut 7 SineIn 8 SineOut 9 SineInOut 10 ElasticOut 11 BounceOut */
QOTGUI_API float qg_easing(int kind, float t);

/* ---- 图片资源（绘制原语用；与 qg_create_image 组件并存） ---- */
QOTGUI_API int   qg_image_load(const char *path);   /* 返回图片 id；失败 0 */
QOTGUI_API void  qg_image_free(int img);
QOTGUI_API int   qg_image_size(int img, int *w, int *h);

/* ---- 属性动画（便捷糖：帧原语之上的最常用捷径，非能力边界） ----
 * prop: 0 x  1 y  2 w  3 h  4 opacity；easing 同 qg_easing。
 * 返回动画 id（>0）；完成时对该控件发 QG_EVT_ANIM_DONE（value = 动画 id）。 */
QOTGUI_API int  qg_animate(int id, int prop, float from, float to, float ms, int easing);
QOTGUI_API int  qg_animate_stop(int anim_id);

/* ---- Canvas 自定义绘制 ----
 * 绘制回调在每帧绘制本画布时同步调用（消息循环线程）；
 * 回调执行期间 qg_draw_api 的各函数隐式绑定当前画布（坐标相对画布原点）。
 * 全部是无状态原语：clip/transform/blur 以 push/pop 成对组合，
 * 图片经 qg_image_load 的 id 引用 —— 开发者自行叠加出任何效果。 */
typedef struct qg_draw_api {
    void (*fill_rect)     (float x, float y, float w, float h,
                           float r, float g, float b, float a);
    void (*stroke_rect)   (float x, float y, float w, float h,
                           float r, float g, float b, float a, float width);
    void (*fill_round_rect)(float x, float y, float w, float h, float radius,
                           float r, float g, float b, float a);
    void (*stroke_round_rect)(float x, float y, float w, float h, float radius,
                           float r, float g, float b, float a, float width);
    void (*fill_ellipse)  (float cx, float cy, float rx, float ry,
                           float r, float g, float b, float a);
    void (*stroke_ellipse)(float cx, float cy, float rx, float ry,
                           float r, float g, float b, float a, float width);
    void (*draw_line)     (float x1, float y1, float x2, float y2,
                           float r, float g, float b, float a, float width);
    void (*draw_text)     (const char *utf8, float x, float y, float size,
                           float r, float g, float b, float a);
    void (*measure_text)  (const char *utf8, float size, float *out_w, float *out_h);
    /* 渐变：两停靠点线性渐变（垂直/水平），层叠/高光/材质的基底 */
    void (*fill_gradient_rect)(float x, float y, float w, float h,
                           float r0, float g0, float b0, float a0,
                           float r1, float g1, float b1, float a1, int vertical);
    /* 裁剪：矩形区域成对 push/pop（可嵌套） */
    void (*push_clip)     (float x, float y, float w, float h);
    void (*pop_clip)      (void);
    /* 变换：平移/旋转/缩放 push/pop 成对（可嵌套；锚点 cx/cy） */
    void (*push_translate)(float dx, float dy);
    void (*push_rotate)   (float cx, float cy, float radians);
    void (*push_scale)    (float cx, float cy, float sx, float sy);
    void (*pop_transform) (void);
    /* 图片与模糊：图片 id 来自 qg_image_load；层模糊仅合成路径可用 */
    void (*draw_image)    (int img, float x, float y, float w, float h, float alpha);
    void (*push_blur)     (float radius, float x, float y, float w, float h);
    void (*pop_blur)      (void);
} qg_draw_api;

typedef void (*qg_draw_fn)(float w, float h, void *userdata);
QOTGUI_API int  qg_canvas_on_draw(int id, qg_draw_fn cb, void *userdata);
QOTGUI_API const qg_draw_api *qg_draw_api_table(void);

/* 绘制原语的扁平导出（与 api 表等价；ctypes 等简单绑定直接用） */
QOTGUI_API void qg_draw_fill_rect(float x, float y, float w, float h,
                                  float r, float g, float b, float a);
QOTGUI_API void qg_draw_stroke_rect(float x, float y, float w, float h,
                                    float r, float g, float b, float a, float width);
QOTGUI_API void qg_draw_fill_round_rect(float x, float y, float w, float h, float radius,
                                        float r, float g, float b, float a);
QOTGUI_API void qg_draw_stroke_round_rect(float x, float y, float w, float h, float radius,
                                          float r, float g, float b, float a, float width);
QOTGUI_API void qg_draw_fill_ellipse(float cx, float cy, float rx, float ry,
                                     float r, float g, float b, float a);
QOTGUI_API void qg_draw_stroke_ellipse(float cx, float cy, float rx, float ry,
                                       float r, float g, float b, float a, float width);
QOTGUI_API void qg_draw_line(float x1, float y1, float x2, float y2,
                             float r, float g, float b, float a, float width);
QOTGUI_API void qg_draw_text(const char *utf8, float x, float y, float size,
                             float r, float g, float b, float a);
QOTGUI_API void qg_draw_measure_text(const char *utf8, float size, float *out_w, float *out_h);
QOTGUI_API void qg_draw_gradient_rect(float x, float y, float w, float h,
                                      float r0, float g0, float b0, float a0,
                                      float r1, float g1, float b1, float a1, int vertical);
QOTGUI_API void qg_draw_push_clip(float x, float y, float w, float h);
QOTGUI_API void qg_draw_pop_clip(void);
QOTGUI_API void qg_draw_push_translate(float dx, float dy);
QOTGUI_API void qg_draw_push_rotate(float cx, float cy, float radians);
QOTGUI_API void qg_draw_push_scale(float cx, float cy, float sx, float sy);
QOTGUI_API void qg_draw_pop_transform(void);
QOTGUI_API void qg_draw_image(int img, float x, float y, float w, float h, float alpha);
QOTGUI_API void qg_draw_push_blur(float radius, float x, float y, float w, float h);
QOTGUI_API void qg_draw_pop_blur(void);

/* ===========================================================================
 * v5：绘制原语
 *
 *   * 路径几何：moveTo/lineTo/quad/bezier/arc/SVG 弧/子形状 → fill/stroke；
 *     bounds/hitTest 让矢量图形可交互；
 *   * 画刷系统：多停靠点线性/径向（焦点偏移）/位图画刷，句柄化跨帧复用；
 *     extend（平铺）与 transform 让位图画刷即 WinUI ImageBrush；
 *   * 线型：cap/join/miter/dash（对全部描边原语生效）；
 *   * 富文本：vg_rich_*（inline 混排：每 span 字号/颜色/字重/斜体/下划线/
 *     删除线/渐变画刷文字 + 行高/字距）；
 *   * 合成效果层：16 种混合模式（qg_draw_push_blend）、任意内容 GPU 高斯
 *     真阴影（qg_draw_push_shadow）、画刷透明度蒙版（qg_draw_push_mask）、
 *     任意路径几何裁剪（qg_draw_push_clip_path，pop 用 qg_draw_pop_clip）；
 *   * 离屏面：qg_surface_*（缓存层：画一次贴多帧）+ 像素读回
 *     （qg_surface_pixel，真值断言/测试用；仅 DComp 路径）；
 *   * 图片精控：qg_draw_image_ex（插值模式 + 源矩形）、
 *     qg_draw_image_round（圆角裁剪）；
 *   * 能力探测：qg_backend_info（合成路径 + DPI）。
 *
 * 状态语义（与 v2 事件通道相同的"单绘制上下文"纪律）：
 *   路径/画刷构建器/富文本 span 都是【当前绘制上下文】的暂存状态，
 *   Canvas 回调内使用（路径坐标相对画布原点，与 vg_draw_* 一致）。
 *   画刷/离屏面句柄（brush id / surface id）跨帧存活（ABI 层表管理，
 *   设备重建后失效 —— 与图片 id 同一先例；free 显式回收）。
 * 混合模式常量 = D2D1_COMPOSITE_MODE 枚举值（0..12）：
 *   0 SOURCE_OVER  1 DESTINATION_OVER  2 SOURCE_IN  3 DESTINATION_IN
 *   4 SOURCE_OUT   5 DESTINATION_OUT   6 SOURCE_ATOP 7 DESTINATION_ATOP
 *   8 XOR          9 PLUS             10 SOURCE_COPY 11 BOUNDED_SOURCE_COPY
 *   12 MASK_INVERT
 * =========================================================================== */

/* ---- 路径几何（构建 → 填充/描边/查询） ---- */
QOTGUI_API int  qg_path_reset(void);
QOTGUI_API int  qg_path_move_to(float x, float y);
QOTGUI_API int  qg_path_line_to(float x, float y);
QOTGUI_API int  qg_path_quad_to(float cx, float cy, float x, float y);
QOTGUI_API int  qg_path_bezier_to(float c1x, float c1y, float c2x, float c2y, float x, float y);
QOTGUI_API int  qg_path_arc(float cx, float cy, float rx, float ry, float startRad, float sweepRad);
QOTGUI_API int  qg_path_svg_arc(float rx, float ry, float rotationRad,
                                 int largeArc, int sweep, float x, float y);
QOTGUI_API int  qg_path_rect(float x, float y, float w, float h);
QOTGUI_API int  qg_path_round_rect(float x, float y, float w, float h, float rx, float ry);
QOTGUI_API int  qg_path_ellipse(float cx, float cy, float rx, float ry);
QOTGUI_API int  qg_path_close(void);
QOTGUI_API int  qg_path_fill(float r, float g, float b, float a);
QOTGUI_API int  qg_path_fill_brush(int brush);
QOTGUI_API int  qg_path_stroke(float r, float g, float b, float a, float width);
QOTGUI_API int  qg_path_stroke_brush(int brush, float width);
QOTGUI_API int  qg_path_bounds(float *x, float *y, float *w, float *h);
QOTGUI_API int  qg_path_hit(float x, float y);               /* 填充命中 1/0 */
QOTGUI_API int  qg_path_stroke_hit(float x, float y, float width);
/* 线型状态（对全部描边生效；cap 0..2 / join 0..3 见 d2d1.h） */
QOTGUI_API int  qg_path_line_cap(int cap);
QOTGUI_API int  qg_path_line_join(int join);
QOTGUI_API int  qg_path_miter_limit(float limit);
QOTGUI_API int  qg_path_dash(const float *dashes, int count, float phase);

/* ---- 画刷系统（id 句柄；渐变构建器 begin → add_stop×N → end） ---- */
QOTGUI_API int  qg_brush_solid(float r, float g, float b, float a);
QOTGUI_API int  qg_brush_linear_begin(float x0, float y0, float x1, float y1);
QOTGUI_API int  qg_brush_radial_begin(float cx, float cy, float fx, float fy,
                                       float rx, float ry);
QOTGUI_API int  qg_brush_add_stop(float pos, float r, float g, float b, float a);
QOTGUI_API int  qg_brush_end(void);
QOTGUI_API void qg_brush_free(int brush);
QOTGUI_API int  qg_brush_extend(int brush, int modeX, int modeY);  /* 仅位图画刷 */
QOTGUI_API int  qg_brush_transform(int brush,
                                    float m11, float m12, float m21, float m22,
                                    float dx, float dy);
QOTGUI_API int  qg_brush_bitmap(int img);   /* 从图片 id 建位图画刷 */

/* ---- 形状 × 画刷 ---- */
QOTGUI_API int  qg_draw_fill_rect_brush(int brush, float x, float y, float w, float h);
QOTGUI_API int  qg_draw_stroke_rect_brush(int brush, float x, float y, float w, float h, float width);
QOTGUI_API int  qg_draw_fill_round_rect_brush(int brush, float x, float y, float w, float h, float radius);
QOTGUI_API int  qg_draw_stroke_round_rect_brush(int brush, float x, float y, float w, float h, float radius, float width);
QOTGUI_API int  qg_draw_fill_ellipse_brush(int brush, float cx, float cy, float rx, float ry);
QOTGUI_API int  qg_draw_stroke_ellipse_brush(int brush, float cx, float cy, float rx, float ry, float width);

/* ---- 富文本（inline 混排；weight = DWRITE_FONT_WEIGHT 数值 100..900） ---- */
QOTGUI_API int  qg_rich_begin(void);
QOTGUI_API int  qg_rich_add(const char *utf8, float size,
                             float r, float g, float b, float a,
                             int weight, int italic, int underline, int strikethrough);
QOTGUI_API int  qg_rich_add_brush(const char *utf8, float size, int brush,
                                   int weight, int italic, int underline, int strikethrough);
QOTGUI_API int  qg_rich_line_height(float lineH, float baseline);   /* <=0 恢复默认 */
QOTGUI_API int  qg_rich_spacing(float leading, float trailing,
                                 float advance, float minAdvance);
QOTGUI_API int  qg_rich_draw(float x, float y, float w, float h,
                              int halign, int valign, int wrap);
QOTGUI_API int  qg_rich_measure(int wrap, float maxw, float *out_w, float *out_h);

/* ---- 合成效果层（push/pop 成对；blend/shadow 仅 DComp 路径，失败返回 0） ---- */
QOTGUI_API int  qg_draw_push_blend(int mode, float x, float y, float w, float h);
QOTGUI_API int  qg_draw_pop_blend(void);
QOTGUI_API int  qg_draw_push_shadow(float r, float g, float b, float a,
                                     float blur, float dx, float dy,
                                     float x, float y, float w, float h);
QOTGUI_API int  qg_draw_pop_shadow(void);
QOTGUI_API int  qg_draw_push_mask(int brush, float x, float y, float w, float h, float opacity);
QOTGUI_API int  qg_draw_pop_mask(void);
QOTGUI_API int  qg_draw_push_clip_path(void);   /* pop 用 qg_draw_pop_clip */

/* ---- 离屏面（缓存层：画一次贴多帧；像素读回仅 DComp 路径） ---- */
QOTGUI_API int  qg_surface_create(int w, int h);
QOTGUI_API int  qg_surface_begin(int id);
QOTGUI_API int  qg_surface_end(void);
QOTGUI_API int  qg_surface_draw(int id, float x, float y, float w, float h,
                                 float alpha, int interp);
QOTGUI_API int  qg_surface_pixel(int id, int x, int y,
                                  float *r, float *g, float *b, float *a);
QOTGUI_API void qg_surface_free(int id);

/* ---- 图片精控（interp 0 nearest 1 linear；src 空矩形 = 整图） ---- */
QOTGUI_API int  qg_draw_image_ex(int img, float x, float y, float w, float h,
                                  float alpha, int interp,
                                  float srcX, float srcY, float srcW, float srcH);
QOTGUI_API int  qg_draw_image_round(int img, float x, float y, float w, float h,
                                     float alpha, int interp, float radius);

/* ---- 能力探测（合成路径 + 当前 DPI；自适应布局用） ---- */
QOTGUI_API int  qg_backend_info(int *composed, int *dpi);

/* ===========================================================================
 * v6：声明式视觉模板（ControlTemplate + VisualState + Transition）
 *
 *   * 路径句柄化：vg_path_store 把当前构建路径固化为几何句柄（挂 factory，
 *     跨设备安全）——部件形状/命中/裁剪共用；qg_geo_hit 命中测试；
 *   * 文字样式块：qg_textstyle_create 打包 size/weight/italic/align/wrap/
 *     family → ts 句柄（模板文字槽用）；
 *   * 模板：qg_tpl_create → qg_tpl_part 逐部件（形状/填充/描边/inset/文字槽）
 *     → qg_tpl_part_text 文字精调 / qg_tpl_part_dyn 值驱动几何
 *     → qg_tpl_state 状态覆盖（mask 位：1 hover|2 pressed|4 disabled|8 focused
 *     |16 checked|32 open）→ qg_tpl_transition 状态切换自动插值（12 缓动）；
 *   * 应用：qg_apply_template(id, tpl) 后控件默认绘制让位（owner-draw 接管
 *     优先级最高，模板次之）；qg_tpl_default(type) 取默认模板副本渐进定制。
 *
 * shape 编码：0 rect（inset 区域） 1 round（shape_a=radius） 2 ellipse
 *   3 path（path_id；shape_a>0 = fixed 框内适配，shape_b=h）
 *   4 fixed-round（a=w b=h c=radius，垂直居中锚定） 5 fixed-ellipse（a=w b=h）
 *   6 hstrip（a=height b=radius，水平满宽垂直居中）；anchor：0 左 1 中 2 右
 * fill_kind：0 none 1 color 2 brush（fill_brush 句柄）3 双色垂直渐变
 *   （fill_color → part 的 fill2Color，后者经 qg_tpl_part_dyn 的 c2 设置）
 * qg_tpl_state 的 prop：0 fill_color(v0..v3) 1 fill_brush(v0=句柄)
 *   2 stroke_color 3 stroke_width(v0) 4 translate(v0,v1) 5 scale(v0,v1)
 *   6 rotate(v0) 7 opacity(v0) 8 radius(v0)
 * qg_tpl_part_dyn 的 value_mode：0 无 1 宽度按值 2 x 位移（range<0 自动）
 *   3 缩放按值（c2[0]=最小比例） 4 透明度按值 5 高度按值（锚底）
 *   6 fill 色按值 lerp（c2 = 第二色；兼作 fill_kind=3 渐变尾色）
 * value_src：0 主值（滑块/进度/勾选） 1 检查动画进度
 * =========================================================================== */
QOTGUI_API int  qg_path_store(void);                    /* 当前路径 → 几何句柄 */
QOTGUI_API void qg_path_free(int path);
QOTGUI_API int  qg_geo_hit(int path, float x, float y);  /* 句柄几何填充命中 */

QOTGUI_API int  qg_textstyle_create(float size, int weight, int italic,
                                    int halign, int wrap, const char *family);
QOTGUI_API void qg_textstyle_free(int ts);

QOTGUI_API int  qg_tpl_create(void);
QOTGUI_API int  qg_tpl_part(int tpl, const char *name, int shape,
                            float shape_a, float shape_b, float shape_c, int path_id,
                            int anchor, int fill_kind, float fr, float fg, float fb, float fa,
                            int fill_brush,
                            int stroke_set, float sr, float sg, float sb, float sa, float sw,
                            float il, float it, float ir, float ib, int textslot);
QOTGUI_API int  qg_tpl_part_text(int tpl, const char *part, int ts_id,
                                 int halign, int valign,
                                 float ml, float mt, float mr, float mb,
                                 int color_set, float r, float g, float b, float a);
QOTGUI_API int  qg_tpl_part_dyn(int tpl, const char *part, int value_mode, int value_src,
                                float range, float c2r, float c2g, float c2b, float c2a);
QOTGUI_API int  qg_tpl_state(int tpl, unsigned state_mask, const char *part, int prop,
                             float v0, float v1, float v2, float v3);
QOTGUI_API int  qg_tpl_transition(int tpl, unsigned state_mask, float ms, int easing);
QOTGUI_API void qg_tpl_free(int tpl);

QOTGUI_API int  qg_apply_template(int id, int tpl);
QOTGUI_API int  qg_clear_template(int id);
QOTGUI_API int  qg_tpl_default(int type);   /* 0 btn 1 chk 2 tgl 3 sld 4 prg 5 cbx 6 tab */

#ifdef __cplusplus
}
#endif
#endif /* QOTGUI_ABI_H */
