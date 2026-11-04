// ===========================================================================
// qotgui_abi.cpp —— wingui 的 C ABI 实现（解释器与原生程序共用这一份）
//
// 事件双通道：
//   * 控件/窗口事件先入队（轮询语义兼容 v1），消息泵空闲点 drain_events()
//     统一派发给 qg_bind 注册的回调 —— 回调线程 = 消息循环线程，回调内
//     可直接调用任意 qg_* 接口；回调中销毁控件安全（延迟一跳派发，
//     不在控件事件处理栈内同步触发）。
//   * 高频事件（鼠标移动/文本改变/尺寸改变）只对绑定过的来源入队，防堆积。
// ===========================================================================
#include "qotgui_abi.h"
#include "gui_api.h"
#include "canvas.h"
#include "image.h"
#include "scrollpane.h"      // v4：滚动条样式 / 容器接管检测
#include "virtual_list.h"    // v4：容器接管检测
#include "visual_template.h" // v6：声明式视觉模板
#include "table_view.h"      // v6 配套：表格
#include "color_picker.h"    // v6 配套：颜色选择器
#include "date_picker.h"     // v6 配套：日期选择器

#include <windows.h>
#include <windowsx.h>   // GET_Y_LPARAM（v4 自绘菜单）
#include <shobjidl.h>   // ITaskbarList3（v3 栏进度）
#include <string>
#include <vector>
#include <unordered_map>
#include <mutex>
#include <deque>

namespace {

struct Event {
    int id;
    int kind;
    double value;
    int x;
    int y;
};

struct Binding {
    int token;
    int id;              // 0 = 任意来源
    unsigned mask;
    qg_event_fn fn;
    void *ud;
};

std::mutex g_mutex;
int g_nextId = 1;
int g_nextToken = 1;
bool g_ready = false;

struct Ctl {
    int id = 0;
    int win = 0;
    gui::ModernWindow* window = nullptr;
    gui::UIComponent* comp = nullptr;
};

std::unordered_map<int, std::unique_ptr<gui::ModernWindow>> g_windows;
std::unordered_map<int, Ctl> g_ctls;    // 含窗口（win 自身也登记，便于统一销毁）
std::deque<Event> g_events;             // 轮询通道
std::deque<Event> g_cb_events;          // 回调通道（独立副本，drain 消费）
std::vector<Binding> g_binds;

// v3：对话框结果表（win → code）
std::unordered_map<int, int> g_dialogResult;
// v4：菜单数据（数据与呈现分离——自绘 popup，样式可定制）
struct MenuItemDef { std::wstring text; int id = 0; bool sep = false; };
struct MenuStyle {
    gui::Color bg{0.10f, 0.10f, 0.16f, 0.97f};
    gui::Color hover{0.20f, 0.60f, 1.00f, 0.35f};
    gui::Color text{0.95f, 0.95f, 1.0f, 1.0f};
    gui::Color border{0.45f, 0.50f, 0.65f, 0.60f};
    float borderWidth = 1.0f;
    float radius = 8.0f;
};
struct MenuDef {
    std::vector<MenuItemDef> items;
    MenuStyle style;
};
std::unordered_map<int, MenuDef> g_menus;
int g_nextMenuId = 1;
int g_nextMenuItemId = 1;
// v3：最近一次拖放批次（kind=51 事件后回调里立刻取；新批次覆盖旧批次）
std::vector<std::wstring> g_lastDrop;
// 绘制原语执行上下文（Canvas/owner-draw 回调期间有效；单消息线程模型）
gui::Renderer* g_drawRenderer = nullptr;
float g_drawOffX = 0.0f, g_drawOffY = 0.0f;

gui::ModernWindow* win_of(int win) {
    auto it = g_windows.find(win);
    return it == g_windows.end() ? nullptr : it->second.get();
}

unsigned kind_bit(int kind) {
    switch (kind) {
        case QG_EVT_CLICK:      return QG_BIT_CLICK;
        case QG_EVT_CHANGE:     return QG_BIT_CHANGE;
        case QG_EVT_SELECT:     return QG_BIT_SELECT;
        case QG_EVT_ANIM_DONE:  return QG_BIT_ANIM_DONE;
        case QG_EVT_QUIT:       return QG_BIT_QUIT;
        case QG_EVT_MOUSE_DOWN: return QG_BIT_MOUSE_DOWN;
        case QG_EVT_MOUSE_UP:   return QG_BIT_MOUSE_UP;
        case QG_EVT_MOUSE_MOVE: return QG_BIT_MOUSE_MOVE;
        case QG_EVT_ENTER:      return QG_BIT_ENTER;
        case QG_EVT_LEAVE:      return QG_BIT_LEAVE;
        case QG_EVT_DBL_CLICK:  return QG_BIT_DBL_CLICK;
        case QG_EVT_SCROLL:     return QG_BIT_SCROLL;
        case QG_EVT_KEY_DOWN:   return QG_BIT_KEY_DOWN;
        case QG_EVT_CHAR:       return QG_BIT_CHAR;
        case QG_EVT_TEXT:       return QG_BIT_TEXT;
        case QG_EVT_CLOSE:      return QG_BIT_CLOSE;
        case QG_EVT_RESIZE:     return QG_BIT_RESIZE;
        case QG_EVT_FOCUS:      return QG_BIT_FOCUS;
        case QG_EVT_BLUR:       return QG_BIT_BLUR;
        case QG_EVT_LOAD:       return QG_BIT_LOAD;
        case QG_EVT_MENU:       return QG_BIT_MENU;
        case QG_EVT_DROP:       return QG_BIT_DROP;
        case QG_EVT_STATE:      return QG_BIT_STATE;
        default:                return 0;
    }
}

// 某事件是否有人订阅（高频事件入队前的门槛）
bool has_bind_for(int id, int kind) {
    unsigned bit = kind_bit(kind);
    std::lock_guard<std::mutex> lk(g_mutex);
    for (const auto& b : g_binds) {
        if ((b.mask & bit) && (b.id == 0 || b.id == id)) return true;
    }
    return false;
}

// ---------------------------------------------------------------------------
// 事件双通道：同一事件两份副本，通道各自消费、互不抢食 ——
//   g_events     轮询通道（qg_poll_event / qg_wait_event 取走即消费，v1 语义）
//   g_cb_events  回调通道（drain_events 在消息泵空闲点派发给 qg_bind 绑定者）
// 没有双通道的话，轮询者会把事件从队列里抽走，回调永远看不到（反之亦然）。
// 两个队列都有 512 上限兜底防堆积。
// ---------------------------------------------------------------------------
void push_event(int id, int kind, double value, int x = -1, int y = -1) {
    std::lock_guard<std::mutex> lk(g_mutex);
    if (g_events.size() > 512) g_events.pop_front();   // 防无消费者时无限堆积
    g_events.push_back(Event{id, kind, value, x, y});
    if (g_cb_events.size() > 512) g_cb_events.pop_front();
    g_cb_events.push_back(Event{id, kind, value, x, y});
}

// 高频事件：绑定过才入队
void push_event_gated(int id, int kind, double value, int x, int y) {
    if (!has_bind_for(id, kind)) return;
    push_event(id, kind, value, x, y);
}

// 消息泵空闲点：派发回调通道（快照匹配，回调中可 bind/unbind）
void drain_events() {
    for (;;) {
        Event e;
        {
            std::lock_guard<std::mutex> lk(g_mutex);
            if (g_cb_events.empty()) return;
            e = g_cb_events.front();
            g_cb_events.pop_front();
        }
        std::vector<Binding> matched;
        {
            unsigned bit = kind_bit(e.kind);
            std::lock_guard<std::mutex> lk(g_mutex);
            for (const auto& b : g_binds) {
                if ((b.mask & bit) && (b.id == 0 || b.id == e.id))
                    matched.push_back(b);
            }
        }
        for (const auto& b : matched) {
            b.fn(e.id, e.kind, e.value, e.x, e.y, b.ud);
        }
    }
}

std::wstring to_w(const char* s) {
    if (!s) return std::wstring();
    int len = MultiByteToWideChar(CP_UTF8, 0, s, -1, nullptr, 0);
    if (len <= 1) return std::wstring();
    std::wstring out(static_cast<size_t>(len - 1), L'\0');
    MultiByteToWideChar(CP_UTF8, 0, s, -1, &out[0], len);
    return out;
}

std::string to_u8(const std::wstring& w) {
    if (w.empty()) return std::string();
    int len = WideCharToMultiByte(CP_UTF8, 0, w.c_str(), (int)w.size(), nullptr, 0, nullptr, nullptr);
    std::string out(static_cast<size_t>(len), '\0');
    WideCharToMultiByte(CP_UTF8, 0, w.c_str(), (int)w.size(), &out[0], len, nullptr, nullptr);
    return out;
}

// 窗口事件汇点（ModernWindow → ABI 队列）
void window_sink(int kind, int id, double value, int x, int y) {
    // 高频事件只对绑定过的来源入队；其余全量（保持 v1 轮询语义）
    if (kind == QG_EVT_MOUSE_MOVE || kind == QG_EVT_RESIZE ||
        kind == QG_EVT_SCROLL) {
        push_event_gated(id, kind, value, x, y);
    } else {
        push_event(id, kind, value, x, y);
    }
}

int reg_ctl(int win, gui::ModernWindow* w, gui::UIComponent* c) {
    int id = g_nextId++;
    Ctl ctl;
    ctl.id = id;
    ctl.win = win;
    ctl.window = w;
    ctl.comp = c;
    g_ctls[id] = ctl;
    return id;
}

// 控件查询/重绘请求（前移到此处：v6 配套控件的创建段在通用属性段之前
// 也要用；与 reg_ctl 同级同性质）
gui::UIComponent* comp_of(int id) {
    auto it = g_ctls.find(id);
    if (it == g_ctls.end()) return nullptr;
    return it->second.comp;
}

void repaint_of(int id) {
    auto it = g_ctls.find(id);
    if (it != g_ctls.end() && it->second.window) it->second.window->RequestRepaint();
}

}  // namespace

extern "C" {

QOTGUI_API int qg_init(void) {
    std::lock_guard<std::mutex> lk(g_mutex);
    if (g_ready) return 1;
    g_ready = gui::Initialize();
    return g_ready ? 1 : 0;
}

QOTGUI_API void qg_uninit(void) {
    std::lock_guard<std::mutex> lk(g_mutex);
    g_ctls.clear();
    g_windows.clear();
    g_events.clear();
    g_cb_events.clear();
    g_binds.clear();
    g_dialogResult.clear();
    // v4：菜单数据表直接清（自绘 popup，无 HMENU 系统资源）
    g_menus.clear();
    g_lastDrop.clear();
    // 任务栏 COM 单例（qg_uninit 释放；下次 taskbar() 重连）
    {
        extern void qg_release_taskbar();
        qg_release_taskbar();
    }
    gui::Shutdown();
    g_ready = false;
}

QOTGUI_API int qg_create_window(const char* title, int width, int height) {
    if (!g_ready && !qg_init()) return 0;
    gui::ModernWindow* w = nullptr;
    {
        std::lock_guard<std::mutex> lk(g_mutex);
        w = new gui::ModernWindow();
    }
    if (!w->Initialize(GetModuleHandle(nullptr), SW_SHOW, to_w(title ? title : "QOT Window"),
                       width > 0 ? width : 800, height > 0 ? height : 600)) {
        delete w;
        return 0;
    }
    int id = 0;
    {
        std::lock_guard<std::mutex> lk(g_mutex);
        id = g_nextId++;
        g_windows[id] = std::unique_ptr<gui::ModernWindow>(w);
        Ctl ctl; ctl.id = id; ctl.win = id; ctl.window = w; ctl.comp = nullptr;
        g_ctls[id] = ctl;
        w->SetEventSink(window_sink);
        // v3 拖放汇点：文件路径列表暂存 + kind=51 事件（value=文件数，x/y=落点）
        int capturedWin = id;
        w->SetDropSink([capturedWin](const std::vector<std::wstring>& files, int x, int y) {
            g_lastDrop = files;
            push_event(capturedWin, QG_EVT_DROP, (double)files.size(), x, y);
        });
    }
    return id;
}

// 清理已销毁窗口的注册表（WM_DESTROY 栈外统一析构；控件项同步失效）。
// 必须在消息循环的空闲点调用 —— 绝不在 WM_DESTROY 处理栈内析构窗口对象。
void reap_destroyed_windows() {
    std::vector<int> dead;
    {
        std::lock_guard<std::mutex> lk(g_mutex);
        for (auto& kv : g_windows) {
            if (kv.second && !IsWindow(kv.second->GetHwnd())) dead.push_back(kv.first);
        }
    }
    for (int wid : dead) {
        std::lock_guard<std::mutex> lk(g_mutex);
        g_windows.erase(wid);
        for (auto it = g_ctls.begin(); it != g_ctls.end();) {
            if (it->second.win == wid) it = g_ctls.erase(it);
            else ++it;
        }
    }
}

QOTGUI_API int qg_show_window(int win) {
    gui::ModernWindow* w = win_of(win);
    if (!w) return 0;
    ShowWindow(w->GetHwnd(), SW_SHOW);
    w->RequestRepaint();
    push_event(win, QG_EVT_LOAD, 0.0);
    return 1;
}

QOTGUI_API int qg_close_window(int win) {
    gui::ModernWindow* w = win_of(win);
    if (!w) return 0;
    DestroyWindow(w->GetHwnd());
    return 1;
}

QOTGUI_API int qg_run(void) {
    MSG msg;
    while (GetMessage(&msg, nullptr, 0, 0) > 0) {
        TranslateMessage(&msg);
        DispatchMessage(&msg);
        drain_events();          // 回调派发点（延迟一跳，销毁安全）
        reap_destroyed_windows();   // 已销毁窗口出册（栈外析构）
        if (g_windows.empty()) break;
    }
    reap_destroyed_windows();
    push_event(0, QG_EVT_QUIT, 0.0);
    drain_events();              // 全关通知送达回调
    return 1;
}

QOTGUI_API int qg_set_window_title(int win, const char* title) {
    gui::ModernWindow* w = win_of(win);
    if (!w) return 0;
    w->SetTitle(to_w(title ? title : ""));
    return 1;
}

QOTGUI_API int qg_set_window_size(int win, int width, int height) {
    gui::ModernWindow* w = win_of(win);
    if (!w) return 0;
    SetWindowPos(w->GetHwnd(), nullptr, 0, 0, width, height,
                 SWP_NOMOVE | SWP_NOZORDER);
    return 1;
}

QOTGUI_API int qg_window_client_size(int win, int* w, int* h) {
    gui::ModernWindow* mw = win_of(win);
    if (!mw) return 0;
    RECT rc;
    if (!GetClientRect(mw->GetHwnd(), &rc)) return 0;
    if (w) *w = (int)(rc.right - rc.left);
    if (h) *h = (int)(rc.bottom - rc.top);
    return 1;
}

QOTGUI_API int qg_set_window_bg(int win, float r0, float g0, float b0,
                                float r1, float g1, float b1) {
    gui::ModernWindow* w = win_of(win);
    if (!w) return 0;
    w->SetBackgroundGradient(gui::Color(r0, g0, b0, 1), gui::Color(r1, g1, b1, 1));
    w->RequestRepaint();
    return 1;
}

QOTGUI_API int qg_set_window_bg_rgba(int win, float r0, float g0, float b0, float a0,
                                     float r1, float g1, float b1, float a1) {
    gui::ModernWindow* w = win_of(win);
    if (!w) return 0;
    w->SetBackgroundGradient(gui::Color(r0, g0, b0, a0), gui::Color(r1, g1, b1, a1));
    w->RequestRepaint();
    return 1;
}

QOTGUI_API int qg_set_theme(int win, int theme) {
    gui::ModernWindow* w = win_of(win);
    if (!w) return 0;
    if (theme == 1)      gui::Theme::ApplyLight(*w);
    else if (theme == 2) gui::Theme::ApplyBlue(*w);
    else                 gui::Theme::ApplyDark(*w);
    w->RequestRepaint();
    return 1;
}

QOTGUI_API int qg_set_font(const char* family) {
    std::wstring f = to_w(family ? family : "");
    if (f.empty()) return 0;
    for (auto& kv : g_windows) {
        if (kv.second) kv.second->GetRenderer()->SetFontFamily(f);
    }
    return 1;
}

// ---- 窗口合成能力原语 ----

QOTGUI_API int qg_set_window_transparent(int win, int on) {
    gui::ModernWindow* w = win_of(win);
    if (!w) return 0;
    return w->SetWindowTransparent(on != 0) ? 1 : 0;
}

QOTGUI_API int qg_set_window_backdrop(int win, int on, float tint_alpha) {
    gui::ModernWindow* w = win_of(win);
    if (!w) return 0;
    return w->SetWindowBackdrop(on != 0, tint_alpha) ? 1 : 0;
}

QOTGUI_API int qg_set_window_round(int win, int on) {
    gui::ModernWindow* w = win_of(win);
    if (!w) return 0;
    return w->SetWindowRoundCorner(on != 0) ? 1 : 0;
}

QOTGUI_API int qg_set_window_borderless(int win, int on) {
    gui::ModernWindow* w = win_of(win);
    if (!w) return 0;
    return w->SetBorderless(on != 0) ? 1 : 0;
}

QOTGUI_API int qg_set_window_topmost(int win, int on) {
    gui::ModernWindow* w = win_of(win);
    if (!w) return 0;
    w->SetTopmost(on != 0);
    return 1;
}

QOTGUI_API int qg_prevent_close(int win, int on) {
    gui::ModernWindow* w = win_of(win);
    if (!w) return 0;
    w->SetPreventClose(on != 0);
    return 1;
}

QOTGUI_API int qg_window_drag(int win) {
    gui::ModernWindow* w = win_of(win);
    if (!w) return 0;
    w->DragWindow();
    return 1;
}

QOTGUI_API int qg_window_minimize(int win) {
    gui::ModernWindow* w = win_of(win);
    if (!w) return 0;
    ShowWindow(w->GetHwnd(), SW_MINIMIZE);
    return 1;
}

QOTGUI_API int qg_window_maximize(int win) {
    gui::ModernWindow* w = win_of(win);
    if (!w) return 0;
    ShowWindow(w->GetHwnd(), SW_MAXIMIZE);
    return 1;
}

QOTGUI_API int qg_window_restore(int win) {
    gui::ModernWindow* w = win_of(win);
    if (!w) return 0;
    ShowWindow(w->GetHwnd(), SW_RESTORE);
    return 1;
}

// ---- 控件创建 ----

QOTGUI_API int qg_create_label(int win, const char* text, int x, int y, int w, int h) {
    gui::ModernWindow* wnd = win_of(win);
    if (!wnd) return 0;
    auto* lb = new gui::Label();
    lb->SetText(to_w(text ? text : ""));
    lb->SetBounds(gui::Rect((float)x, (float)y, (float)(x + (w > 0 ? w : 160)), (float)(y + (h > 0 ? h : 28))));
    int id = reg_ctl(win, wnd, lb);
    lb->SetId(id);
    wnd->AddComponent(std::unique_ptr<gui::UIComponent>(lb));
    wnd->RequestRepaint();
    return id;
}

QOTGUI_API int qg_create_button(int win, const char* text, int x, int y, int w, int h) {
    gui::ModernWindow* wnd = win_of(win);
    if (!wnd) return 0;
    auto* b = new gui::Button();
    b->SetText(to_w(text ? text : "Button"));
    b->SetBounds(gui::Rect((float)x, (float)y, (float)(x + (w > 0 ? w : 120)), (float)(y + (h > 0 ? h : 40))));
    int id = reg_ctl(win, wnd, b);
    b->SetId(id);
    b->OnClick([id]() { push_event(id, QG_EVT_CLICK, 0.0); });
    wnd->AddComponent(std::unique_ptr<gui::UIComponent>(b));
    wnd->RequestRepaint();
    return id;
}

QOTGUI_API int qg_create_checkbox(int win, const char* text, int x, int y, int w, int h) {
    gui::ModernWindow* wnd = win_of(win);
    if (!wnd) return 0;
    auto* c = new gui::CheckBox();
    c->SetText(to_w(text ? text : ""));
    c->SetBounds(gui::Rect((float)x, (float)y, (float)(x + (w > 0 ? w : 160)), (float)(y + (h > 0 ? h : 28))));
    int id = reg_ctl(win, wnd, c);
    c->SetId(id);
    c->OnChecked([id](bool v) { push_event(id, QG_EVT_CHANGE, v ? 1.0 : 0.0); });
    wnd->AddComponent(std::unique_ptr<gui::UIComponent>(c));
    wnd->RequestRepaint();
    return id;
}

QOTGUI_API int qg_create_toggle(int win, const char* text, int x, int y, int w, int h) {
    gui::ModernWindow* wnd = win_of(win);
    if (!wnd) return 0;
    auto* t = new gui::ToggleSwitch();
    (void)text;   // 开关控件按既有 API 不带文字
    t->SetBounds(gui::Rect((float)x, (float)y, (float)(x + (w > 0 ? w : 160)), (float)(y + (h > 0 ? h : 28))));
    int id = reg_ctl(win, wnd, t);
    t->SetId(id);
    t->OnToggle([id](bool v) { push_event(id, QG_EVT_CHANGE, v ? 1.0 : 0.0); });
    wnd->AddComponent(std::unique_ptr<gui::UIComponent>(t));
    wnd->RequestRepaint();
    return id;
}

QOTGUI_API int qg_create_textbox(int win, const char* placeholder, int x, int y, int w, int h) {
    gui::ModernWindow* wnd = win_of(win);
    if (!wnd) return 0;
    auto* t = new gui::TextBox();
    t->SetPlaceholder(to_w(placeholder ? placeholder : ""));
    t->SetBounds(gui::Rect((float)x, (float)y, (float)(x + (w > 0 ? w : 200)), (float)(y + (h > 0 ? h : 34))));
    int id = reg_ctl(win, wnd, t);
    t->SetId(id);
    t->OnTextChanged([id](const std::wstring&) {
        push_event_gated(id, QG_EVT_TEXT, 0.0, -1, -1);
    });
    wnd->AddComponent(std::unique_ptr<gui::UIComponent>(t));
    wnd->RequestRepaint();
    return id;
}

QOTGUI_API int qg_create_slider(int win, int x, int y, int w, int h) {
    gui::ModernWindow* wnd = win_of(win);
    if (!wnd) return 0;
    auto* s = new gui::Slider();
    s->SetBounds(gui::Rect((float)x, (float)y, (float)(x + (w > 0 ? w : 200)), (float)(y + (h > 0 ? h : 28))));
    s->SetMinMax(0.0f, 100.0f);
    int id = reg_ctl(win, wnd, s);
    s->SetId(id);
    s->OnValueChanged([id](float v) { push_event(id, QG_EVT_CHANGE, (double)v); });
    wnd->AddComponent(std::unique_ptr<gui::UIComponent>(s));
    wnd->RequestRepaint();
    return id;
}

QOTGUI_API int qg_create_progressbar(int win, int x, int y, int w, int h) {
    gui::ModernWindow* wnd = win_of(win);
    if (!wnd) return 0;
    auto* p = new gui::ProgressBar();
    p->SetBounds(gui::Rect((float)x, (float)y, (float)(x + (w > 0 ? w : 200)), (float)(y + (h > 0 ? h : 12))));
    int id = reg_ctl(win, wnd, p);
    p->SetId(id);
    wnd->AddComponent(std::unique_ptr<gui::UIComponent>(p));
    wnd->RequestRepaint();
    return id;
}

QOTGUI_API int qg_create_card(int win, const char* title, int x, int y, int w, int h) {
    gui::ModernWindow* wnd = win_of(win);
    if (!wnd) return 0;
    auto* c = new gui::Card();
    c->SetTitle(to_w(title ? title : ""));
    c->SetBounds(gui::Rect((float)x, (float)y, (float)(x + (w > 0 ? w : 240)), (float)(y + (h > 0 ? h : 140))));
    int id = reg_ctl(win, wnd, c);
    c->SetId(id);
    c->SetOnClick([id]() { push_event(id, QG_EVT_CLICK, 0.0); });
    wnd->AddComponent(std::unique_ptr<gui::UIComponent>(c));
    wnd->RequestRepaint();
    return id;
}

QOTGUI_API int qg_create_combobox(int win, int x, int y, int w, int h) {
    gui::ModernWindow* wnd = win_of(win);
    if (!wnd) return 0;
    auto* c = new gui::ComboBox();
    c->SetBounds(gui::Rect((float)x, (float)y, (float)(x + (w > 0 ? w : 180)), (float)(y + (h > 0 ? h : 34))));
    int id = reg_ctl(win, wnd, c);
    c->SetId(id);
    c->OnSelectionChanged([id](int idx, const std::wstring&) { push_event(id, QG_EVT_SELECT, (double)idx); });
    wnd->AddComponent(std::unique_ptr<gui::UIComponent>(c));
    wnd->RequestRepaint();
    return id;
}

QOTGUI_API int qg_create_listbox(int win, int x, int y, int w, int h) {
    gui::ModernWindow* wnd = win_of(win);
    if (!wnd) return 0;
    auto* l = new gui::ListBox();
    l->SetBounds(gui::Rect((float)x, (float)y, (float)(x + (w > 0 ? w : 200)), (float)(y + (h > 0 ? h : 160))));
    int id = reg_ctl(win, wnd, l);
    l->SetId(id);
    l->OnSelectionChanged([id](int idx, const std::wstring&) { push_event(id, QG_EVT_SELECT, (double)idx); });
    // 事件欠账补线：OnDoubleClick 回调一直存在但从未接 ABI（kind=15）
    l->OnDoubleClick([id](int idx, const std::wstring&) {
        push_event(id, QG_EVT_DBL_CLICK, (double)idx);
    });
    wnd->AddComponent(std::unique_ptr<gui::UIComponent>(l));
    wnd->RequestRepaint();
    return id;
}

QOTGUI_API int qg_create_tabcontrol(int win, int x, int y, int w, int h) {
    gui::ModernWindow* wnd = win_of(win);
    if (!wnd) return 0;
    auto* t = new gui::TabControl();
    t->SetBounds(gui::Rect((float)x, (float)y, (float)(x + (w > 0 ? w : 320)), (float)(y + (h > 0 ? h : 220))));
    int id = reg_ctl(win, wnd, t);
    t->SetId(id);
    t->OnTabChanged([id](int idx, const std::wstring&) { push_event(id, QG_EVT_SELECT, (double)idx); });
    wnd->AddComponent(std::unique_ptr<gui::UIComponent>(t));
    wnd->RequestRepaint();
    return id;
}

QOTGUI_API int qg_create_spinner(int win, int x, int y, int w, int h) {
    gui::ModernWindow* wnd = win_of(win);
    if (!wnd) return 0;
    auto* s = new gui::Spinner();
    s->SetBounds(gui::Rect((float)x, (float)y, (float)(x + (w > 0 ? w : 40)), (float)(y + (h > 0 ? h : 40))));
    int id = reg_ctl(win, wnd, s);
    s->SetId(id);
    wnd->AddComponent(std::unique_ptr<gui::UIComponent>(s));
    wnd->RequestRepaint();
    return id;
}

// ---- v6 配套：表格 / 颜色选择器 / 日期选择器 ----

QOTGUI_API int qg_create_table(int win, int x, int y, int w, int h) {
    gui::ModernWindow* wnd = win_of(win);
    if (!wnd) return 0;
    auto* t = new gui::TableView();
    t->SetBounds(gui::Rect((float)x, (float)y, (float)(x + (w > 0 ? w : 320)), (float)(y + (h > 0 ? h : 200))));
    int id = reg_ctl(win, wnd, t);
    t->SetId(id);
    t->OnSelection([id](int row, int col) {
        push_event(id, QG_EVT_SELECT, (double)row, col, -1);
    });
    wnd->AddComponent(std::unique_ptr<gui::UIComponent>(t));
    wnd->RequestRepaint();
    return id;
}

QOTGUI_API int qg_table_add_column(int id, const char* title, float width) {
    gui::UIComponent* c = comp_of(id);
    if (!c || !title) return 0;
    auto* t = dynamic_cast<gui::TableView*>(c);
    if (!t) return 0;
    t->AddColumn(to_w(title), width);
    repaint_of(id);
    return 1;
}

QOTGUI_API int qg_table_add_row(int id, const char* cells) {
    gui::UIComponent* c = comp_of(id);
    if (!c) return 0;
    auto* t = dynamic_cast<gui::TableView*>(c);
    if (!t) return 0;
    // ';' 分隔的单元格串 → 逐列
    std::vector<std::wstring> row;
    std::wstring s = to_w(cells ? cells : "");
    size_t pos = 0;
    for (;;) {
        size_t semi = s.find(L';', pos);
        row.push_back((semi == std::wstring::npos) ? s.substr(pos)
                                                   : s.substr(pos, semi - pos));
        if (semi == std::wstring::npos) break;
        pos = semi + 1;
    }
    t->AddRow(row);
    repaint_of(id);
    return 1;
}

QOTGUI_API int qg_table_set_cell(int id, int row, int col, const char* text) {
    gui::UIComponent* c = comp_of(id);
    if (!c) return 0;
    auto* t = dynamic_cast<gui::TableView*>(c);
    if (!t) return 0;
    t->SetCell(row, col, to_w(text ? text : ""));
    repaint_of(id);
    return 1;
}

QOTGUI_API int qg_table_get_selected(int id, int* row, int* col) {
    gui::UIComponent* c = comp_of(id);
    if (!c) return 0;
    auto* t = dynamic_cast<gui::TableView*>(c);
    if (!t) return 0;
    if (row) *row = t->GetSelectedRow();
    if (col) *col = t->GetSelectedColumn();
    return 1;
}

QOTGUI_API int qg_table_set_selected(int id, int row, int col) {
    gui::UIComponent* c = comp_of(id);
    if (!c) return 0;
    auto* t = dynamic_cast<gui::TableView*>(c);
    if (!t) return 0;
    t->SetSelected(row, col);
    repaint_of(id);
    return 1;
}

QOTGUI_API int qg_create_colorpicker(int win, int x, int y, int w, int h) {
    gui::ModernWindow* wnd = win_of(win);
    if (!wnd) return 0;
    auto* p = new gui::ColorPicker();
    p->SetBounds(gui::Rect((float)x, (float)y, (float)(x + (w > 0 ? w : 240)), (float)(y + (h > 0 ? h : 200))));
    int id = reg_ctl(win, wnd, p);
    p->SetId(id);
    p->OnColorChanged([id](float r, float g, float b) {
        // 打包 0xRRGGBB 入 value（double 在 2^53 内精确表示 24 位色）
        auto to8 = [](float v) { int n = (int)lround(v * 255.0f);
                                 return n < 0 ? 0 : (n > 255 ? 255 : n); };
        double packed = (double)(((unsigned)to8(r) << 16) |
                                 ((unsigned)to8(g) << 8) | (unsigned)to8(b));
        push_event_gated(id, QG_EVT_CHANGE, packed, -1, -1);
    });
    wnd->AddComponent(std::unique_ptr<gui::UIComponent>(p));
    wnd->RequestRepaint();
    return id;
}

QOTGUI_API int qg_colorpicker_set_rgb(int id, float r, float g, float b) {
    gui::UIComponent* c = comp_of(id);
    if (!c) return 0;
    auto* p = dynamic_cast<gui::ColorPicker*>(c);
    if (!p) return 0;
    p->SetRgb(r, g, b);
    repaint_of(id);
    return 1;
}

QOTGUI_API int qg_colorpicker_get_rgb(int id, float* r, float* g, float* b) {
    gui::UIComponent* c = comp_of(id);
    if (!c) return 0;
    auto* p = dynamic_cast<gui::ColorPicker*>(c);
    if (!p) return 0;
    p->GetRgb(r, g, b);
    return 1;
}

QOTGUI_API int qg_create_datepicker(int win, int x, int y, int w, int h) {
    gui::ModernWindow* wnd = win_of(win);
    if (!wnd) return 0;
    auto* p = new gui::DatePicker();
    p->SetBounds(gui::Rect((float)x, (float)y, (float)(x + (w > 0 ? w : 260)), (float)(y + (h > 0 ? h : 260))));
    int id = reg_ctl(win, wnd, p);
    p->SetId(id);
    p->OnDateChanged([id](int y, int m, int d) {
        push_event(id, QG_EVT_CHANGE, (double)((long long)y * 10000L + m * 100L + d));
    });
    wnd->AddComponent(std::unique_ptr<gui::UIComponent>(p));
    wnd->RequestRepaint();
    return id;
}

QOTGUI_API int qg_datepicker_set_date(int id, int y, int m, int d) {
    gui::UIComponent* c = comp_of(id);
    if (!c) return 0;
    auto* p = dynamic_cast<gui::DatePicker*>(c);
    if (!p) return 0;
    p->SetDate(y, m, d);
    repaint_of(id);
    return 1;
}

QOTGUI_API int qg_datepicker_get_date(int id, int* y, int* m, int* d) {
    gui::UIComponent* c = comp_of(id);
    if (!c) return 0;
    auto* p = dynamic_cast<gui::DatePicker*>(c);
    if (!p) return 0;
    p->GetDate(y, m, d);
    return 1;
}

QOTGUI_API int qg_create_canvas(int win, int x, int y, int w, int h) {
    gui::ModernWindow* wnd = win_of(win);
    if (!wnd) return 0;
    auto* cv = new gui::Canvas();
    cv->SetBounds(gui::Rect((float)x, (float)y, (float)(x + (w > 0 ? w : 200)), (float)(y + (h > 0 ? h : 200))));
    int id = reg_ctl(win, wnd, cv);
    cv->SetId(id);
    wnd->AddComponent(std::unique_ptr<gui::UIComponent>(cv));
    wnd->RequestRepaint();
    return id;
}

QOTGUI_API int qg_create_image(int win, const char* path, int x, int y, int w, int h) {
    gui::ModernWindow* wnd = win_of(win);
    if (!wnd) return 0;
    auto* img = new gui::Image();
    img->SetBounds(gui::Rect((float)x, (float)y, (float)(x + (w > 0 ? w : 120)), (float)(y + (h > 0 ? h : 120))));
    img->Load(wnd->GetRenderer(), to_w(path ? path : ""));
    int id = reg_ctl(win, wnd, img);
    img->SetId(id);
    wnd->AddComponent(std::unique_ptr<gui::UIComponent>(img));
    wnd->RequestRepaint();
    return id;
}

// ---- 通用属性 ----
// （comp_of / repaint_of 已前移至文件头匿名 namespace——v6 配套控件创建段
//   在本段之前就需要；同 TU 内部链接函数唯一）

QOTGUI_API int qg_set_text(int id, const char* text) {
    gui::UIComponent* c = comp_of(id);
    if (!c) return 0;
    std::wstring t = to_w(text ? text : "");
    if (auto* lb = dynamic_cast<gui::Label*>(c))        lb->SetText(t);
    else if (auto* b = dynamic_cast<gui::Button*>(c))   b->SetText(t);
    else if (auto* cb = dynamic_cast<gui::CheckBox*>(c)) cb->SetText(t);
    else if (auto* tb = dynamic_cast<gui::TextBox*>(c)) tb->SetText(t);
    else if (auto* cd = dynamic_cast<gui::Card*>(c))    cd->SetTitle(t);
    else return 0;
    repaint_of(id);
    return 1;
}

QOTGUI_API int qg_get_text(int id, char* buf, int buflen) {
    if (!buf || buflen <= 0) return 0;
    gui::UIComponent* c = comp_of(id);
    if (!c) return 0;
    std::wstring t;
    if (auto* lb = dynamic_cast<gui::Label*>(c))        t = lb->GetText();
    else if (auto* tb = dynamic_cast<gui::TextBox*>(c)) t = tb->GetText();
    else return 0;
    std::string u8 = to_u8(t);
    int n = (int)u8.size() < buflen - 1 ? (int)u8.size() : buflen - 1;
    memcpy(buf, u8.c_str(), n);
    buf[n] = '\0';
    return n;
}

QOTGUI_API int qg_set_value(int id, double v) {
    gui::UIComponent* c = comp_of(id);
    if (!c) return 0;
    if (auto* s = dynamic_cast<gui::Slider*>(c))          s->SetValue((float)v);
    else if (auto* p = dynamic_cast<gui::ProgressBar*>(c)) p->SetTargetValue((float)v);
    else if (auto* cb = dynamic_cast<gui::CheckBox*>(c))  cb->SetChecked(v != 0.0);
    else if (auto* tg = dynamic_cast<gui::ToggleSwitch*>(c)) tg->SetChecked(v != 0.0);
    else if (auto* cmb = dynamic_cast<gui::ComboBox*>(c)) cmb->SetSelectedIndex((int)v);
    else if (auto* lb = dynamic_cast<gui::ListBox*>(c))   lb->SetSelectedIndex((int)v);
    else if (auto* tc = dynamic_cast<gui::TabControl*>(c)) tc->SetSelectedIndex((int)v);
    else return 0;
    repaint_of(id);
    return 1;
}

QOTGUI_API double qg_get_value(int id) {
    gui::UIComponent* c = comp_of(id);
    if (!c) return 0.0;
    if (auto* s = dynamic_cast<gui::Slider*>(c))          return (double)s->GetValue();
    if (auto* p = dynamic_cast<gui::ProgressBar*>(c))     return (double)p->GetValue();
    if (auto* cb = dynamic_cast<gui::CheckBox*>(c))       return cb->IsChecked() ? 1.0 : 0.0;
    if (auto* tg = dynamic_cast<gui::ToggleSwitch*>(c))   return tg->IsChecked() ? 1.0 : 0.0;
    if (auto* cmb = dynamic_cast<gui::ComboBox*>(c))      return (double)cmb->GetSelectedIndex();
    if (auto* lb = dynamic_cast<gui::ListBox*>(c))        return (double)lb->GetSelectedIndex();
    if (auto* tc = dynamic_cast<gui::TabControl*>(c))     return (double)tc->GetSelectedIndex();
    return 0.0;
}

QOTGUI_API int qg_set_pos(int id, int x, int y) {
    gui::UIComponent* c = comp_of(id);
    if (!c) return 0;
    gui::Rect b = c->GetBounds();
    float w = b.Width(), h = b.Height();
    c->SetBounds(gui::Rect((float)x, (float)y, (float)x + w, (float)y + h));
    repaint_of(id);
    return 1;
}

QOTGUI_API int qg_set_size(int id, int w, int h) {
    gui::UIComponent* c = comp_of(id);
    if (!c) return 0;
    gui::Rect b = c->GetBounds();
    c->SetBounds(gui::Rect(b.left, b.top, b.left + (float)w, b.top + (float)h));
    repaint_of(id);
    return 1;
}

QOTGUI_API int qg_get_pos(int id, int* x, int* y) {
    gui::UIComponent* c = comp_of(id);
    if (!c) return 0;
    gui::Rect b = c->GetBounds();
    if (x) *x = (int)b.left;
    if (y) *y = (int)b.top;
    return 1;
}

QOTGUI_API int qg_get_size(int id, int* w, int* h) {
    gui::UIComponent* c = comp_of(id);
    if (!c) return 0;
    gui::Rect b = c->GetBounds();
    if (w) *w = (int)b.Width();
    if (h) *h = (int)b.Height();
    return 1;
}

QOTGUI_API int qg_set_visible(int id, int visible) {
    gui::UIComponent* c = comp_of(id);
    if (!c) return 0;
    c->SetVisible(visible != 0);
    repaint_of(id);
    return 1;
}

QOTGUI_API int qg_set_enabled(int id, int enabled) {
    gui::UIComponent* c = comp_of(id);
    if (!c) return 0;
    c->SetEnabled(enabled != 0);
    repaint_of(id);
    return 1;
}

QOTGUI_API int qg_set_color(int id, float r, float g, float b, float a) {
    gui::UIComponent* c = comp_of(id);
    if (!c) return 0;
    gui::Color col(r, g, b, a);
    if (auto* btn = dynamic_cast<gui::Button*>(c))       btn->SetBackgroundColor(col);
    else if (auto* p = dynamic_cast<gui::ProgressBar*>(c)) p->SetProgressColor(col);
    else if (auto* cd = dynamic_cast<gui::Card*>(c))     cd->SetBackgroundColor(col);
    else if (auto* s = dynamic_cast<gui::Slider*>(c))    s->SetFillColor(col);
    else if (auto* tb = dynamic_cast<gui::TextBox*>(c))  tb->SetBackgroundColor(col);
    else return 0;
    repaint_of(id);
    return 1;
}

QOTGUI_API int qg_set_text_color(int id, float r, float g, float b, float a) {
    gui::UIComponent* c = comp_of(id);
    if (!c) return 0;
    gui::Color col(r, g, b, a);
    if (auto* lb = dynamic_cast<gui::Label*>(c))        lb->SetTextColor(col);
    else if (auto* btn = dynamic_cast<gui::Button*>(c)) btn->SetTextColor(col);
    else if (auto* tb = dynamic_cast<gui::TextBox*>(c)) tb->SetTextColor(col);
    else return 0;
    repaint_of(id);
    return 1;
}

QOTGUI_API int qg_set_font_size(int id, float size) {
    gui::UIComponent* c = comp_of(id);
    if (!c) return 0;
    if (auto* lb = dynamic_cast<gui::Label*>(c))        lb->SetFontSize(size);
    else if (auto* btn = dynamic_cast<gui::Button*>(c)) btn->SetFontSize(size);
    else if (auto* tb = dynamic_cast<gui::TextBox*>(c)) tb->SetFontSize(size);
    else return 0;
    repaint_of(id);
    return 1;
}

QOTGUI_API int qg_set_radius(int id, float radius) {
    gui::UIComponent* c = comp_of(id);
    if (!c) return 0;
    if (auto* btn = dynamic_cast<gui::Button*>(c))       btn->SetBorderRadius(radius);
    else if (auto* cd = dynamic_cast<gui::Card*>(c))     cd->SetBorderRadius(radius);
    else if (auto* p = dynamic_cast<gui::ProgressBar*>(c)) p->SetBorderRadius(radius);
    else if (auto* tb = dynamic_cast<gui::TextBox*>(c))  tb->SetBorderRadius(radius);
    else return 0;
    repaint_of(id);
    return 1;
}

QOTGUI_API int qg_set_opacity(int id, float a) {
    gui::UIComponent* c = comp_of(id);
    if (!c) return 0;
    c->SetOpacity(a);
    repaint_of(id);
    return 1;
}

QOTGUI_API int qg_add_item(int id, const char* item) {
    gui::UIComponent* c = comp_of(id);
    if (!c) return 0;
    std::wstring t = to_w(item ? item : "");
    if (auto* cmb = dynamic_cast<gui::ComboBox*>(c))  cmb->AddItem(t);
    else if (auto* lb = dynamic_cast<gui::ListBox*>(c)) lb->AddItem(t);
    else if (auto* tc = dynamic_cast<gui::TabControl*>(c)) tc->AddTab(t);
    else return 0;
    repaint_of(id);
    return 1;
}

QOTGUI_API int qg_clear_items(int id) {
    // wingui 的组合框/列表框没有清空接口：用重建语义代替（删除后重新创建）
    // —— 这里返回 0 表示「本控件不支持」，让上层明确知道而不是静默无效。
    (void)id;
    return 0;
}

QOTGUI_API int qg_destroy(int id) {
    auto it = g_ctls.find(id);
    if (it == g_ctls.end()) return 0;
    if (it->second.window && it->second.comp) {
        // v3：深度移除（顶层没有则搜滚容器子层——attach 进去的控件也能销毁）
        it->second.window->RemoveComponentDeep(id);
        it->second.window->RequestRepaint();
    }
    g_ctls.erase(it);
    return 1;
}

// ---- 布局系统（v3 ·  1）----

QOTGUI_API int qg_layout_begin(int win, const char* mode) {
    gui::ModernWindow* w = win_of(win);
    if (!w) return 0;
    std::string m = mode ? mode : "";
    int md = 0;
    if (m == "vbox") md = 1;
    else if (m == "hbox") md = 2;
    else if (m == "grid") md = 3;
    w->Layout().Reset(md);          // absolute / 未知 = 关闭布局
    w->RequestRepaint();
    return 1;
}

QOTGUI_API int qg_layout_add(int win, int id) {
    gui::ModernWindow* w = win_of(win);
    if (!w) return 0;
    auto it = g_ctls.find(id);
    if (it == g_ctls.end() || !it->second.comp) return 0;   // 未登记的控件
    const gui::Rect b = it->second.comp->GetBounds();
    w->Layout().AddItem(id, b.Width(), b.Height());   // 自然尺寸快照
    return 1;
}

QOTGUI_API int qg_layout_spacing(int win, float n) {
    gui::ModernWindow* w = win_of(win);
    if (!w) return 0;
    if (n < 0) n = 0;
    w->Layout().spacing = n;
    return 1;
}

QOTGUI_API int qg_layout_padding(int win, float l, float t, float r, float b) {
    gui::ModernWindow* w = win_of(win);
    if (!w) return 0;
    auto& p = w->Layout().padding;
    p.left = l > 0 ? l : 0;
    p.top = t > 0 ? t : 0;
    p.right = r > 0 ? r : 0;
    p.bottom = b > 0 ? b : 0;
    return 1;
}

QOTGUI_API int qg_layout_cols(int win, int n) {
    gui::ModernWindow* w = win_of(win);
    if (!w) return 0;
    w->Layout().cols = n > 0 ? n : 1;
    return 1;
}

QOTGUI_API int qg_layout_props(int id, float ml, float mt, float mr, float mb,
                               int fill, int align, float weight) {
    auto it = g_ctls.find(id);
    if (it == g_ctls.end() || !it->second.window) return 0;
    auto& p = it->second.window->Layout().PropsOf(id);
    p.margin.left = ml > 0 ? ml : 0;
    p.margin.top = mt > 0 ? mt : 0;
    p.margin.right = mr > 0 ? mr : 0;
    p.margin.bottom = mb > 0 ? mb : 0;
    p.fill = fill;
    p.align = align;
    p.weight = weight > 0 ? weight : 0;
    return 1;
}

QOTGUI_API int qg_layout_end(int win) {
    gui::ModernWindow* w = win_of(win);
    if (!w) return 0;
    w->RelayoutNow();   // 立即重排（resize 时 OnResize 会自动再排）
    w->RequestRepaint();
    return 1;
}

// ---- 滚动容器与虚拟化列表（v3 ·  2）----

QOTGUI_API int qg_create_scrollpane(int win, int x, int y, int w, int h) {
    gui::ModernWindow* wnd = win_of(win);
    if (!wnd) return 0;
    auto* sp = new gui::ScrollPane();
    sp->SetBounds(gui::Rect((float)x, (float)y, (float)(x + (w > 0 ? w : 200)),
                            (float)(y + (h > 0 ? h : 160))));
    int id = reg_ctl(win, wnd, sp);
    sp->SetId(id);
    sp->OnScroll([id](float off) { push_event_gated(id, QG_EVT_SCROLL, (double)off, -1, -1); });
    wnd->AddComponent(std::unique_ptr<gui::UIComponent>(sp));
    wnd->RequestRepaint();
    return id;
}

QOTGUI_API int qg_scroll_content(int id, float content_h) {
    gui::UIComponent* c = comp_of(id);
    if (!c) return 0;
    auto* sp = dynamic_cast<gui::ScrollPane*>(c);
    if (!sp) return 0;
    sp->SetContentHeight(content_h);
    repaint_of(id);
    return 1;
}

QOTGUI_API int qg_scroll_attach(int id, int child_id, int x, int y, int w, int h) {
    auto it = g_ctls.find(id);
    auto cit = g_ctls.find(child_id);
    if (it == g_ctls.end() || cit == g_ctls.end()) return 0;
    auto* sp = dynamic_cast<gui::ScrollPane*>(it->second.comp);
    if (!sp || !cit->second.comp || !it->second.window) return 0;
    // 从窗口顶层摘出（所有权转移），按内容坐标收编
    std::unique_ptr<gui::UIComponent> taken = it->second.window->TakeComponent(child_id);
    if (!taken) return 0;
    float ww = w > 0 ? (float)w : 120.0f;
    float hh = h > 0 ? (float)h : 28.0f;
    taken->SetBounds(gui::Rect((float)x, (float)y, (float)x + ww, (float)y + hh));
    sp->AddChild(std::move(taken));
    it->second.window->RequestRepaint();
    return 1;
}

QOTGUI_API double qg_scroll_offset(int id) {
    gui::UIComponent* c = comp_of(id);
    if (!c) return 0.0;
    if (auto* sp = dynamic_cast<gui::ScrollPane*>(c)) return (double)sp->GetScrollOffset();
    if (auto* vl = dynamic_cast<gui::VirtualList*>(c)) return (double)vl->GetScrollOffset();
    return 0.0;
}

QOTGUI_API int qg_scroll_to(int id, float offset) {
    gui::UIComponent* c = comp_of(id);
    if (!c) return 0;
    if (auto* sp = dynamic_cast<gui::ScrollPane*>(c)) sp->SetScrollOffset(offset);
    else if (auto* vl = dynamic_cast<gui::VirtualList*>(c)) vl->SetScrollOffset(offset);
    else return 0;
    repaint_of(id);
    return 1;
}

QOTGUI_API int qg_create_vlist(int win, int x, int y, int w, int h) {
    gui::ModernWindow* wnd = win_of(win);
    if (!wnd) return 0;
    auto* vl = new gui::VirtualList();
    vl->SetBounds(gui::Rect((float)x, (float)y, (float)(x + (w > 0 ? w : 200)),
                            (float)(y + (h > 0 ? h : 160))));
    int id = reg_ctl(win, wnd, vl);
    vl->SetId(id);
    vl->OnSelectionChanged([id](int idx, const std::wstring&) {
        push_event(id, QG_EVT_SELECT, (double)idx);
    });
    vl->OnScroll([id](float off) { push_event_gated(id, QG_EVT_SCROLL, (double)off, -1, -1); });
    wnd->AddComponent(std::unique_ptr<gui::UIComponent>(vl));
    wnd->RequestRepaint();
    return id;
}

QOTGUI_API int qg_list_set_count(int id, int n) {
    gui::UIComponent* c = comp_of(id);
    if (!c) return 0;
    auto* vl = dynamic_cast<gui::VirtualList*>(c);
    if (!vl) return 0;
    vl->SetCount(n);
    repaint_of(id);
    return 1;
}

QOTGUI_API int qg_list_add_item(int id, const char* text) {
    gui::UIComponent* c = comp_of(id);
    if (!c) return 0;
    auto* vl = dynamic_cast<gui::VirtualList*>(c);
    if (!vl) return 0;
    vl->AddItem(to_w(text ? text : ""));
    repaint_of(id);
    return 1;
}

QOTGUI_API int qg_list_draw_count(int id) {
    gui::UIComponent* c = comp_of(id);
    if (!c) return 0;
    auto* vl = dynamic_cast<gui::VirtualList*>(c);
    if (!vl) return 0;
    return (int)vl->DrawCount();
}

namespace {
// 行数据回调的返回缓冲：per-id，返回指针在下次回调前有效
std::unordered_map<int, std::string> g_listbuf;
}  // namespace

QOTGUI_API int qg_list_on_data(int id, qg_list_data_fn cb, void* userdata) {
    gui::UIComponent* c = comp_of(id);
    if (!c) return 0;
    auto* vl = dynamic_cast<gui::VirtualList*>(c);
    if (!vl) return 0;
    if (!cb) {
        vl->OnData(nullptr);
        g_listbuf.erase(id);
        return 1;
    }
    vl->OnData([cb, userdata, id](int row) -> std::wstring {
        const char* s = cb(row, userdata);
        g_listbuf[id] = s ? s : "";
        return to_w(g_listbuf[id].c_str());
    });
    return 1;
}

// ---- 事件回调（v2） ----

QOTGUI_API int qg_bind(int id, unsigned kinds_mask, qg_event_fn cb, void* userdata) {
    if (!cb) return 0;
    std::lock_guard<std::mutex> lk(g_mutex);
    Binding b;
    b.token = g_nextToken++;
    b.id = id;
    b.mask = kinds_mask;
    b.fn = cb;
    b.ud = userdata;
    g_binds.push_back(b);
    return b.token;
}

QOTGUI_API int qg_unbind(int token) {
    std::lock_guard<std::mutex> lk(g_mutex);
    for (auto it = g_binds.begin(); it != g_binds.end(); ++it) {
        if (it->token == token) {
            g_binds.erase(it);
            return 1;
        }
    }
    return 0;
}

// ---- 事件队列（v1 轮询，兼容保留） ----

QOTGUI_API int qg_poll_event(int* id, int* kind, double* value) {
    std::lock_guard<std::mutex> lk(g_mutex);
    if (g_events.empty()) return 0;
    Event e = g_events.front();
    g_events.pop_front();
    if (id) *id = e.id;
    if (kind) *kind = e.kind;
    if (value) *value = e.value;
    return 1;
}

QOTGUI_API int qg_wait_event(int* id, int* kind, double* value, int timeout_ms) {
    // 先看积压事件
    if (qg_poll_event(id, kind, value)) return 1;
    const DWORD tmo = timeout_ms > 0 ? (DWORD)timeout_ms : 0;
    const DWORD deadline = GetTickCount() + tmo;
    for (;;) {
        // 泵光当前积压的消息（输入/绘制/定时器都在这里被分发，
        // 控件回调正是在 DispatchMessage 里把事件推进队列的）
        MSG msg;
        while (PeekMessage(&msg, nullptr, 0, 0, PM_REMOVE)) {
            if (msg.message == WM_QUIT) {
                if (id) *id = 0;
                if (kind) *kind = QG_EVT_QUIT;
                if (value) *value = 0.0;
                push_event(0, QG_EVT_QUIT, 0.0);   // 回调通道同步收到全关通知
                drain_events();
                return 1;
            }
            TranslateMessage(&msg);
            DispatchMessage(&msg);
        }
        // 无条件派发点：窗口空闲（无消息流动）时回调通道也要走——
        // drain 挂在 PeekMessage 体内的话，空闲界面回调永不触发
        drain_events();
        reap_destroyed_windows();
        if (qg_poll_event(id, kind, value)) return 1;
        if (tmo == 0 || GetTickCount() >= deadline) return 0;
        Sleep(1);   // 空闲近零 CPU；事件延迟至多 1ms，手感无差
    }
}

// ---- 模态对话框（v3 ·  3）----

QOTGUI_API int qg_set_dialog_result(int win, int code) {
    if (g_windows.find(win) == g_windows.end()) return 0;
    g_dialogResult[win] = code;
    return 1;
}

QOTGUI_API int qg_show_dialog(int win, int parent) {
    gui::ModernWindow* dw = win_of(win);
    gui::ModernWindow* pw = parent > 0 ? win_of(parent) : nullptr;
    if (!dw) return -1;
    HWND dh = dw->GetHwnd();
    HWND ph = (pw && pw != dw) ? pw->GetHwnd() : nullptr;

    if (ph) EnableWindow(ph, FALSE);   // 模态：父窗冻结（输入被挡）
    SetForegroundWindow(dh);
    g_dialogResult[win] = 0;

    // 对话框自己的消息循环：退出条件 = 目标对话框关闭（而非全部窗口关闭）。
    // 回调通道照常派发；嵌套对话框 = 栈式递归循环，天然支持。
    MSG msg;
    for (;;) {
        BOOL r = GetMessage(&msg, nullptr, 0, 0);
        if (r <= 0) break;   // WM_QUIT / 错误
        TranslateMessage(&msg);
        DispatchMessage(&msg);
        drain_events();             // 回调派发点（与 qg_run 同纪律）
        reap_destroyed_windows();   // 栈外析构（WM_DESTROY 纪律）
        if (!IsWindow(dh)) break;
    }
    int code = g_dialogResult.count(win) ? g_dialogResult[win] : 0;
    g_dialogResult.erase(win);

    if (ph && IsWindow(ph)) {
        EnableWindow(ph, TRUE);    // 解冻父窗并还给前台
        SetForegroundWindow(ph);
    }
    return code;
}

// ---- 右键菜单（v3 ·  3）----

QOTGUI_API int qg_create_menu(void) {
    if (!g_ready && !qg_init()) return 0;
    int id = g_nextMenuId++;
    g_menus[id] = MenuDef{};
    return id;
}

QOTGUI_API int qg_menu_add_item(int mid, const char* text) {
    auto it = g_menus.find(mid);
    if (it == g_menus.end()) return 0;
    int itemId = g_nextMenuItemId++;
    MenuItemDef item;
    item.text = to_w(text ? text : "");
    item.id = itemId;
    it->second.items.push_back(std::move(item));
    return itemId;
}

QOTGUI_API int qg_menu_separator(int mid) {
    auto it = g_menus.find(mid);
    if (it == g_menus.end()) return 0;
    MenuItemDef item;
    item.sep = true;
    it->second.items.push_back(item);
    return 1;
}

QOTGUI_API int qg_menu_destroy(int mid) {
    auto it = g_menus.find(mid);
    if (it == g_menus.end()) return 0;
    g_menus.erase(it);
    return 1;
}

QOTGUI_API int qg_menu_bg(int mid, float r, float g, float b, float a) {
    auto it = g_menus.find(mid);
    if (it == g_menus.end()) return 0;
    it->second.style.bg = gui::Color(r, g, b, a);
    return 1;
}

QOTGUI_API int qg_menu_hover_bg(int mid, float r, float g, float b, float a) {
    auto it = g_menus.find(mid);
    if (it == g_menus.end()) return 0;
    it->second.style.hover = gui::Color(r, g, b, a);
    return 1;
}

QOTGUI_API int qg_menu_text_color(int mid, float r, float g, float b, float a) {
    auto it = g_menus.find(mid);
    if (it == g_menus.end()) return 0;
    it->second.style.text = gui::Color(r, g, b, a);
    return 1;
}

QOTGUI_API int qg_menu_border(int mid, float r, float g, float b, float a, float width) {
    auto it = g_menus.find(mid);
    if (it == g_menus.end()) return 0;
    it->second.style.border = gui::Color(r, g, b, a);
    it->second.style.borderWidth = width;
    return 1;
}

// ---------------------------------------------------------------------------
// v4 自绘菜单 popup：数据（MenuDef）与呈现分离。
// 模态循环期间 owner 窗口消息照常派发（绘制不断，与 qg_show_dialog 同模式）；
// hover 高亮 + 键盘 ↑↓/Enter/ESC + 失焦取消 + 屏幕边缘防越界。
// ---------------------------------------------------------------------------
namespace {

struct MenuPopupState {
    MenuDef* def = nullptr;
    int selId = 0;        // 选中项 id（0 = 取消）
    int hoverIndex = -1;  // 鼠标悬停项（items 索引）
    int keyIndex = -1;    // 键盘导航当前项
    bool done = false;
    float itemH = 32.0f;
    float sepH = 9.0f;
    float padX = 14.0f;
    float width = 180.0f, height = 0.0f;
    gui::Renderer* renderer = nullptr;
};
MenuPopupState g_menuPopup;
HWND g_menuPopupHwnd = nullptr;
gui::Renderer* g_menuRenderer = nullptr;

const wchar_t *kMenuPopupClass = L"QotMenuPopupV4";

int MenuItemAtY(const MenuDef& def, float y) {
    float cur = 2.0f;
    for (int i = 0; i < (int)def.items.size(); ++i) {
        float h = def.items[i].sep ? g_menuPopup.sepH : g_menuPopup.itemH;
        if (y >= cur && y < cur + h) return def.items[i].sep ? -1 : i;
        cur += h;
    }
    return -1;
}

void MenuPopupPaint() {
    gui::Renderer* r = g_menuPopup.renderer;
    MenuDef* d = g_menuPopup.def;
    if (!r || !d) return;
    r->BeginDraw();
    r->Clear(gui::Color(0, 0, 0, 0));
    gui::RoundedRect rr{gui::Rect(0, 0, g_menuPopup.width, g_menuPopup.height),
                        d->style.radius, d->style.radius};
    r->FillRoundedRect(rr, d->style.bg);
    r->DrawRoundedRect(rr, d->style.border, d->style.borderWidth);
    float y = 2.0f;
    for (int i = 0; i < (int)d->items.size(); ++i) {
        const MenuItemDef& item = d->items[i];
        if (item.sep) {
            float cy = y + g_menuPopup.sepH * 0.5f;
            r->DrawLine(gui::Point(g_menuPopup.padX, cy),
                        gui::Point(g_menuPopup.width - g_menuPopup.padX, cy),
                        gui::Color(d->style.border.r, d->style.border.g, d->style.border.b, 0.35f), 1.0f);
            y += g_menuPopup.sepH;
            continue;
        }
        bool hot = (i == g_menuPopup.hoverIndex) || (i == g_menuPopup.keyIndex);
        if (hot) {
            gui::RoundedRect ir{gui::Rect(4.0f, y + 1.0f, g_menuPopup.width - 4.0f, y + g_menuPopup.itemH - 1.0f),
                                d->style.radius * 0.6f, d->style.radius * 0.6f};
            r->FillRoundedRect(ir, d->style.hover);
        }
        gui::Rect tr(g_menuPopup.padX, y, g_menuPopup.width - g_menuPopup.padX, y + g_menuPopup.itemH);
        r->DrawTextAligned(item.text, tr, d->style.text, 14.0f, false, 0, 1, false);
        y += g_menuPopup.itemH;
    }
    r->EndDraw();
}

LRESULT CALLBACK MenuPopupProc(HWND hwnd, UINT msg, WPARAM wp, LPARAM lp) {
    switch (msg) {
        case WM_PAINT: {
            PAINTSTRUCT ps;
            BeginPaint(hwnd, &ps);
            EndPaint(hwnd, &ps);
            MenuPopupPaint();
            return 0;
        }
        case WM_ERASEBKGND:
            return 1;
        case WM_MOUSEMOVE: {
            float y = (float)GET_Y_LPARAM(lp);
            int idx = MenuItemAtY(*g_menuPopup.def, y);
            if (idx != g_menuPopup.hoverIndex) {
                g_menuPopup.hoverIndex = idx;
                InvalidateRect(hwnd, nullptr, FALSE);
            }
            return 0;
        }
        case WM_LBUTTONUP: {
            float y = (float)GET_Y_LPARAM(lp);
            int idx = MenuItemAtY(*g_menuPopup.def, y);
            if (idx >= 0) {
                g_menuPopup.selId = g_menuPopup.def->items[idx].id;
                g_menuPopup.done = true;
            }
            return 0;
        }
        case WM_KEYDOWN: {
            MenuDef* d = g_menuPopup.def;
            if (wp == VK_ESCAPE) {
                g_menuPopup.done = true;
            } else if (wp == VK_UP || wp == VK_DOWN) {
                int dir = (wp == VK_DOWN) ? 1 : -1;
                int n = (int)d->items.size();
                int i = g_menuPopup.keyIndex;
                for (int step = 0; step < n; ++step) {
                    i = (i < 0) ? (dir > 0 ? 0 : n - 1) : (i + dir + n) % n;
                    if (!d->items[i].sep) break;
                }
                if (i >= 0 && i < n && !d->items[i].sep) {
                    g_menuPopup.keyIndex = i;
                    InvalidateRect(hwnd, nullptr, FALSE);
                }
            } else if (wp == VK_RETURN) {
                int i = g_menuPopup.keyIndex;
                if (i >= 0 && i < (int)d->items.size() && !d->items[i].sep) {
                    g_menuPopup.selId = d->items[i].id;
                    g_menuPopup.done = true;
                }
            }
            return 0;
        }
        case WM_ACTIVATE:
            if (LOWORD(wp) == WA_INACTIVE) g_menuPopup.done = true;   // 失焦取消
            return 0;
    }
    return DefWindowProcW(hwnd, msg, wp, lp);
}

void EnsureMenuPopupWindow() {
    if (g_menuPopupHwnd) return;
    WNDCLASSEXW wc{sizeof(wc)};
    wc.style = CS_HREDRAW | CS_VREDRAW;
    wc.lpfnWndProc = MenuPopupProc;
    wc.hInstance = GetModuleHandleW(nullptr);
    wc.hCursor = LoadCursorW(nullptr, MAKEINTRESOURCEW(32512));   // IDC_ARROW（W 版）
    wc.hbrBackground = nullptr;
    wc.lpszClassName = kMenuPopupClass;
    RegisterClassExW(&wc);
    g_menuPopupHwnd = CreateWindowExW(
        WS_EX_TOOLWINDOW | WS_EX_TOPMOST, kMenuPopupClass, L"",
        WS_POPUP, 0, 0, 10, 10, nullptr, nullptr, wc.hInstance, nullptr);
    if (g_menuPopupHwnd) {
        static gui::Renderer menuRenderer;
        if (menuRenderer.Initialize(g_menuPopupHwnd)) {
            g_menuRenderer = &menuRenderer;
        }
    }
}

}  // namespace

QOTGUI_API int qg_menu_popup(int mid, int win, int x, int y) {
    auto it = g_menus.find(mid);
    gui::ModernWindow* w = win_of(win);
    if (it == g_menus.end() || !w || !w->GetHwnd()) return 0;
    if (it->second.items.empty()) {
        push_event(win, QG_EVT_MENU, 0.0, x, y);
        return 0;
    }
    EnsureMenuPopupWindow();
    if (!g_menuPopupHwnd || !g_menuRenderer) return 0;

    // 布局测量：行高按文本实测，宽度取最长项
    const MenuDef& def = it->second;
    float maxTextW = 60.0f;
    float textH = 20.0f;
    {
        gui::Size ts = g_menuRenderer->MeasureText(L"测试 Test", 14.0f, false);
        if (ts.height > 4.0f) textH = ts.height;
        for (const auto& item : def.items) {
            if (item.sep) continue;
            gui::Size s = g_menuRenderer->MeasureText(item.text, 14.0f, false);
            if (s.width > maxTextW) maxTextW = s.width;
        }
    }
    g_menuPopup = MenuPopupState{};
    g_menuPopup.def = const_cast<MenuDef*>(&def);
    g_menuPopup.renderer = g_menuRenderer;
    g_menuPopup.itemH = textH + 12.0f;
    g_menuPopup.width = maxTextW + g_menuPopup.padX * 2.0f + 10.0f;
    float h = 4.0f;
    for (const auto& item : def.items) h += item.sep ? g_menuPopup.sepH : g_menuPopup.itemH;
    g_menuPopup.height = h;

    POINT pt = {x, y};
    ClientToScreen(w->GetHwnd(), &pt);
    HMONITOR mon = MonitorFromPoint(pt, MONITOR_DEFAULTTONEAREST);
    MONITORINFO mi{sizeof(mi)};
    if (GetMonitorInfoW(mon, &mi)) {
        if (pt.x + (int)g_menuPopup.width > mi.rcWork.right)
            pt.x = mi.rcWork.right - (int)g_menuPopup.width;
        if (pt.y + (int)g_menuPopup.height > mi.rcWork.bottom)
            pt.y = mi.rcWork.bottom - (int)g_menuPopup.height;
        if (pt.x < mi.rcWork.left) pt.x = mi.rcWork.left;
        if (pt.y < mi.rcWork.top) pt.y = mi.rcWork.top;
    }
    SetWindowPos(g_menuPopupHwnd, HWND_TOPMOST, pt.x, pt.y,
                 (int)g_menuPopup.width, (int)g_menuPopup.height, SWP_SHOWWINDOW);
    g_menuRenderer->Resize(gui::Size(g_menuPopup.width, g_menuPopup.height));
    SetForegroundWindow(g_menuPopupHwnd);
    SetFocus(g_menuPopupHwnd);

    // 模态循环（owner 窗口消息照常派发，绘制不断）
    MSG msg;
    while (!g_menuPopup.done && GetMessageW(&msg, nullptr, 0, 0) > 0) {
        TranslateMessage(&msg);
        DispatchMessageW(&msg);
    }
    ShowWindow(g_menuPopupHwnd, SW_HIDE);

    int cmd = g_menuPopup.selId;
    push_event(win, QG_EVT_MENU, (double)cmd, x, y);
    return cmd;
}

// ---- Toast 自绘轻提示（v3 ·  3）----

QOTGUI_API int qg_toast(int win, const char* text, int ms) {
    gui::ModernWindow* w = win_of(win);
    if (!w) return 0;
    w->ShowToast(to_w(text ? text : ""), ms);
    return 1;
}

// ---- 系统集成（v3 ·  5）----

QOTGUI_API int qg_clipboard_set(const char* utf8) {
    if (!utf8) return 0;
    std::wstring w = to_w(utf8);
    SIZE_T bytes = (w.size() + 1) * sizeof(wchar_t);
    if (!OpenClipboard(nullptr)) return 0;
    int ok = 0;
    if (EmptyClipboard()) {
        HGLOBAL h = GlobalAlloc(GMEM_MOVEABLE, bytes);
        if (h) {
            void* p = GlobalLock(h);
            if (p) {
                memcpy(p, w.c_str(), bytes);
                GlobalUnlock(h);
                // 所有权归系统（成功后不得 free）
                if (SetClipboardData(CF_UNICODETEXT, h)) ok = 1;
            }
            if (!ok) GlobalFree(h);
        }
    }
    CloseClipboard();
    return ok;
}

QOTGUI_API int qg_clipboard_get(char* buf, int buflen) {
    if (!buf || buflen <= 0) return 0;
    buf[0] = '\0';
    if (!OpenClipboard(nullptr)) return 0;
    int n = 0;
    HANDLE h = GetClipboardData(CF_UNICODETEXT);
    if (h) {
        const wchar_t* p = (const wchar_t*)GlobalLock(h);
        if (p) {
            std::string u8 = to_u8(p);
            n = (int)u8.size() < buflen - 1 ? (int)u8.size() : buflen - 1;
            memcpy(buf, u8.c_str(), (size_t)n);
            buf[n] = '\0';
            GlobalUnlock(h);
        }
    }
    CloseClipboard();
    return n;
}

QOTGUI_API int qg_window_accept_files(int win, int on) {
    gui::ModernWindow* w = win_of(win);
    if (!w) return 0;
    w->EnableDropFiles(on != 0);
    return 1;
}

QOTGUI_API int qg_take_drop_file(int index, char* buf, int buflen) {
    if (!buf || buflen <= 0) return 0;
    buf[0] = '\0';
    if (index < 0 || index >= (int)g_lastDrop.size()) return 0;
    std::string u8 = to_u8(g_lastDrop[index]);
    int n = (int)u8.size() < buflen - 1 ? (int)u8.size() : buflen - 1;
    memcpy(buf, u8.c_str(), (size_t)n);
    buf[n] = '\0';
    return n;
}

// ---- 任务栏进度（v3 ·  5；ITaskbarList3 COM 单例）----

namespace {

// MinGW 部分头文件版本不带 IID_ITaskbarList3 的导出常量——手写标准值
const IID kCLSID_TaskbarList = {0x56FDF344, 0xFD6D, 0x11D0,
                                {0x95, 0x8A, 0x00, 0x60, 0x97, 0xC9, 0xA0, 0x90}};
const IID kIID_ITaskbarList3 = {0xEA1AFB91, 0x9E28, 0x4B86,
                                {0x90, 0xE9, 0x9E, 0x9F, 0x8A, 0x5E, 0xEF, 0xAF}};

ITaskbarList3 *g_taskbar = nullptr;
bool g_taskbarTried = false;

ITaskbarList3 *taskbar() {
    if (!g_taskbarTried) {
        g_taskbarTried = true;
        // COM 已在 gui::Initialize 里 CoInitializeEx(APARTMENTTHREADED)
        if (FAILED(CoCreateInstance(kCLSID_TaskbarList, nullptr, CLSCTX_INPROC_SERVER,
                                    kIID_ITaskbarList3,
                                    reinterpret_cast<void **>(&g_taskbar)))) {
            g_taskbar = nullptr;
        } else if (FAILED(g_taskbar->HrInit())) {
            g_taskbar->Release();
            g_taskbar = nullptr;
        }
    }
    return g_taskbar;
}

void taskbar_release() {
    if (g_taskbar) {
        g_taskbar->Release();
        g_taskbar = nullptr;
    }
    g_taskbarTried = false;   // 下次 taskbar() 重连
}

}  // namespace

// qg_uninit 经此释放任务栏 COM 单例
void qg_release_taskbar() {
    taskbar_release();
}

QOTGUI_API int qg_taskbar_progress(int win, int done, int total) {
    gui::ModernWindow* w = win_of(win);
    if (!w || !w->GetHwnd()) return 0;
    ITaskbarList3* tb = taskbar();
    if (!tb) return 0;
    if (total <= 0) {
        tb->SetProgressState(w->GetHwnd(), TBPF_NOPROGRESS);
        return 1;
    }
    if (done < 0) done = 0;
    tb->SetProgressState(w->GetHwnd(), TBPF_NORMAL);
    tb->SetProgressValue(w->GetHwnd(), (ULONGLONG)done, (ULONGLONG)total);
    return 1;
}

QOTGUI_API int qg_taskbar_state(int win, int state) {
    gui::ModernWindow* w = win_of(win);
    if (!w || !w->GetHwnd()) return 0;
    ITaskbarList3* tb = taskbar();
    if (!tb) return 0;
    TBPFLAG pf = TBPF_NOPROGRESS;
    switch (state) {
        case 1: pf = TBPF_INDETERMINATE; break;
        case 2: pf = TBPF_NORMAL; break;
        case 3: pf = TBPF_ERROR; break;
        case 4: pf = TBPF_PAUSED; break;
        default: pf = TBPF_NOPROGRESS; break;
    }
    return SUCCEEDED(tb->SetProgressState(w->GetHwnd(), pf)) ? 1 : 0;
}

// ---- 样式原语（v4 · 通用组件样式 + 绘制接管）----
// 叠加层语义：未设置走组件默认（零定制 = 行为逐位不变）；
// 状态位：0x1 hover / 0x2 pressed / 0x4 disabled / 0x8 focused。

namespace {
int style_state_index(int which) {
    switch (which) {
        case 1: return 0;   // hover
        case 2: return 1;   // pressed
        case 3: return 2;   // disabled
        case 4: return 3;   // focused
    }
    return -1;
}
}  // namespace

QOTGUI_API int qg_style_bg(int id, float r, float g, float b, float a) {
    gui::UIComponent* c = comp_of(id);
    if (!c) return 0;
    c->Style().bgSet = true;
    c->Style().bg = gui::Color(r, g, b, a);
    repaint_of(id);
    return 1;
}

QOTGUI_API int qg_style_bg_gradient(int id, float r0, float g0, float b0, float a0,
                                    float r1, float g1, float b1, float a1, int vertical) {
    gui::UIComponent* c = comp_of(id);
    if (!c) return 0;
    c->Style().bgGradSet = true;
    c->Style().bgGrad1 = gui::Color(r0, g0, b0, a0);
    c->Style().bgGrad2 = gui::Color(r1, g1, b1, a1);
    c->Style().bgGradVertical = vertical;
    repaint_of(id);
    return 1;
}

QOTGUI_API int qg_style_border(int id, float r, float g, float b, float a, float width) {
    gui::UIComponent* c = comp_of(id);
    if (!c) return 0;
    c->Style().borderSet = true;
    c->Style().borderColor = gui::Color(r, g, b, a);
    c->Style().borderWidth = width;
    repaint_of(id);
    return 1;
}

QOTGUI_API int qg_style_radius(int id, float radius) {
    gui::UIComponent* c = comp_of(id);
    if (!c) return 0;
    c->Style().radiusSet = true;
    c->Style().radius = radius;
    repaint_of(id);
    return 1;
}

QOTGUI_API int qg_style_padding(int id, float l, float t, float r, float b) {
    gui::UIComponent* c = comp_of(id);
    if (!c) return 0;
    c->Style().padSet = true;
    c->Style().padL = l; c->Style().padT = t;
    c->Style().padR = r; c->Style().padB = b;
    repaint_of(id);
    return 1;
}

QOTGUI_API int qg_style_text(int id, float r, float g, float b, float a,
                             float size, int bold) {
    gui::UIComponent* c = comp_of(id);
    if (!c) return 0;
    c->Style().textSet = true;
    c->Style().textColor = gui::Color(r, g, b, a);
    c->Style().textSize = size;
    c->Style().textBold = bold != 0;
    repaint_of(id);
    return 1;
}

QOTGUI_API int qg_style_state_bg(int id, int which, float r, float g, float b, float a) {
    gui::UIComponent* c = comp_of(id);
    if (!c) return 0;
    int idx = style_state_index(which);
    if (idx < 0) return 0;
    c->Style().stateBgSet[idx] = true;
    c->Style().stateBg[idx] = gui::Color(r, g, b, a);
    repaint_of(id);
    return 1;
}

QOTGUI_API int qg_style_state_text(int id, int which, float r, float g, float b, float a) {
    gui::UIComponent* c = comp_of(id);
    if (!c) return 0;
    int idx = style_state_index(which);
    if (idx < 0) return 0;
    c->Style().stateTextSet[idx] = true;
    c->Style().stateText[idx] = gui::Color(r, g, b, a);
    repaint_of(id);
    return 1;
}

QOTGUI_API int qg_style_reset(int id) {
    gui::UIComponent* c = comp_of(id);
    if (!c) return 0;
    c->Style() = gui::UIComponent::CompStyle{};
    repaint_of(id);
    return 1;
}

QOTGUI_API unsigned qg_style_flags(int id) {
    gui::UIComponent* c = comp_of(id);
    if (!c) return 0;
    return c->StyleFlags();
}

QOTGUI_API int qg_on_paint(int id, qg_paint_fn cb, void* userdata) {
    gui::UIComponent* c = comp_of(id);
    if (!c) return 0;
    if (!cb) {
        c->ClearPaintFn();
        repaint_of(id);
        return 1;
    }
    // 容器类不接管（绘制 = 子组件 + 容器面，接管语义混乱）；样式叠加仍可用
    if (dynamic_cast<gui::Card*>(c) || dynamic_cast<gui::ScrollPane*>(c) ||
        dynamic_cast<gui::VirtualList*>(c)) {
        return 0;
    }
    c->SetPaintFn([cb, userdata, c](gui::Renderer* r, float w, float h, unsigned st) {
        g_drawRenderer = r;
        const gui::Rect& b = c->GetBounds();
        g_drawOffX = b.left;
        g_drawOffY = b.top;
        cb(w, h, st, userdata);
        g_drawRenderer = nullptr;
    });
    repaint_of(id);
    return 1;
}

QOTGUI_API int qg_toast_style(int win,
                              float bgR, float bgG, float bgB, float bgA,
                              float tR, float tG, float tB, float tA,
                              float bR, float bG, float bB, float bA,
                              float radius) {
    gui::ModernWindow* w = win_of(win);
    if (!w) return 0;
    gui::ModernWindow::ToastStyle s;
    s.bg = gui::Color(bgR, bgG, bgB, bgA);
    s.text = gui::Color(tR, tG, tB, tA);
    s.border = gui::Color(bR, bG, bB, bA);
    s.radius = radius;
    w->SetToastStyle(s);
    w->RequestRepaint();
    return 1;
}

QOTGUI_API int qg_scroll_style(int id, float tR, float tG, float tB, float tA,
                               float hR, float hG, float hB, float hA, float width) {
    gui::UIComponent* c = comp_of(id);
    if (!c) return 0;
    auto* sp = dynamic_cast<gui::ScrollPane*>(c);
    if (!sp) return 0;
    sp->SetBarStyle(gui::Color(tR, tG, tB, tA), gui::Color(hR, hG, hB, hA), width);
    repaint_of(id);
    return 1;
}

// ---- 帧循环原语（requestAnimationFrame 模型） ----

namespace {

struct FrameEntry {
    int token;
    qg_frame_fn fn;
    void *ud;
};
std::vector<FrameEntry> g_frames;
int g_nextFrameToken = 1;

void run_frame_hooks(double dt) {
    // 快照调用（回调中可注册/注销帧回调）
    std::vector<FrameEntry> snapshot;
    {
        std::lock_guard<std::mutex> lk(g_mutex);
        snapshot = g_frames;
    }
    for (const auto& f : snapshot) f.fn(dt, f.ud);
}

}  // namespace

QOTGUI_API int qg_on_frame(qg_frame_fn cb, void* userdata) {
    if (!cb) return 0;
    int token;
    {
        std::lock_guard<std::mutex> lk(g_mutex);
        token = g_nextFrameToken++;
        g_frames.push_back(FrameEntry{token, cb, userdata});
    }
    gui::ModernWindow::SetFrameHook([](double dt) { run_frame_hooks(dt); });
    // 让帧循环转起来（后续由回调自行续帧）
    for (auto& kv : g_windows) {
        if (kv.second) kv.second->RequestFrame();
    }
    return token;
}

QOTGUI_API int qg_off_frame(int token) {
    std::lock_guard<std::mutex> lk(g_mutex);
    for (auto it = g_frames.begin(); it != g_frames.end(); ++it) {
        if (it->token == token) {
            g_frames.erase(it);
            return 1;
        }
    }
    return 0;
}

QOTGUI_API void qg_request_frame(void) {
    for (auto& kv : g_windows) {
        if (kv.second) kv.second->RequestFrame();
    }
}

// ---- 缓动纯函数（12 种，开发者组合用） ----

QOTGUI_API float qg_easing(int kind, float t) {
    // 直接复用 animation.h 的内联缓动函数（纯函数语义）
    using namespace gui;
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
        default: return EasingLinear(t);
    }
}

// ---- 图片资源（绘制原语用） ----

namespace {
std::unordered_map<int, ID2D1Bitmap *> g_images;
int g_nextImageId = 1;

gui::Renderer *renderer_for_image() {
    // 图片解码需要一个渲染目标做 D2D 位图宿主：任取一个活窗口的渲染器
    for (auto& kv : g_windows) {
        if (kv.second && kv.second->GetRenderer() && kv.second->GetRenderer()->GetRenderTarget())
            return kv.second->GetRenderer();
    }
    return nullptr;
}
}  // namespace

QOTGUI_API int qg_image_load(const char *path) {
    gui::Renderer *r = renderer_for_image();
    if (!r) return 0;
    ID2D1Bitmap *bmp = r->LoadImageFile(to_w(path ? path : ""), nullptr, nullptr);
    if (!bmp) return 0;
    std::lock_guard<std::mutex> lk(g_mutex);
    int id = g_nextImageId++;
    g_images[id] = bmp;
    return id;
}

QOTGUI_API void qg_image_free(int img) {
    std::lock_guard<std::mutex> lk(g_mutex);
    auto it = g_images.find(img);
    if (it == g_images.end()) return;
    if (it->second) it->second->Release();
    g_images.erase(it);
}

QOTGUI_API int qg_image_size(int img, int *w, int *h) {
    std::lock_guard<std::mutex> lk(g_mutex);
    auto it = g_images.find(img);
    if (it == g_images.end() || !it->second) return 0;
    D2D1_SIZE_F sz = it->second->GetSize();
    if (w) *w = (int)sz.width;
    if (h) *h = (int)sz.height;
    return 1;
}

// ---- 属性动画 ----

namespace {

struct AnimEntry {
    int anim_id;
    int comp_id;
    int prop;            // 0 x 1 y 2 w 3 h 4 opacity
    gui::Animation* anim;
};
std::unordered_map<int, AnimEntry> g_anims;
int g_nextAnimId = 1;

}  // namespace

QOTGUI_API int qg_animate(int id, int prop, float from, float to, float ms, int easing) {
    gui::UIComponent* c = comp_of(id);
    if (!c) return 0;
    if (prop < 0 || prop > 4) return 0;
    if (ms <= 0) ms = 1;

    gui::Animation* anim = gui::AnimationManager::Instance().CreateAnimation();
    int animId;
    {
        std::lock_guard<std::mutex> lk(g_mutex);
        animId = g_nextAnimId++;
        g_anims[animId] = AnimEntry{animId, id, prop, anim};
    }
    int captured = animId;
    anim->OnUpdate([captured, id, prop, to](float value) {
        // 每帧改属性 + 请求重绘；到点发完成事件
        gui::UIComponent* cc = comp_of(id);
        if (cc) {
            gui::Rect b = cc->GetBounds();
            switch (prop) {
                case 0: cc->SetBounds(gui::Rect(value, b.top, value + b.Width(), b.bottom)); break;
                case 1: cc->SetBounds(gui::Rect(b.left, value, b.right, value + b.Height())); break;
                case 2: cc->SetBounds(gui::Rect(b.left, b.top, b.left + value, b.bottom)); break;
                case 3: cc->SetBounds(gui::Rect(b.left, b.top, b.right, b.top + value)); break;
                case 4: cc->SetOpacity(value); break;
            }
            repaint_of(id);
        }
        if (value == to) {
            push_event(id, QG_EVT_ANIM_DONE, (double)captured);
            std::lock_guard<std::mutex> lk(g_mutex);
            g_anims.erase(captured);
        }
    });
    gui::EasingType et = gui::EasingType::Linear;
    switch (easing) {
        case 1:  et = gui::EasingType::QuadIn; break;
        case 2:  et = gui::EasingType::QuadOut; break;
        case 3:  et = gui::EasingType::QuadInOut; break;
        case 4:  et = gui::EasingType::CubicIn; break;
        case 5:  et = gui::EasingType::CubicOut; break;
        case 6:  et = gui::EasingType::CubicInOut; break;
        case 7:  et = gui::EasingType::SineIn; break;
        case 8:  et = gui::EasingType::SineOut; break;
        case 9:  et = gui::EasingType::SineInOut; break;
        case 10: et = gui::EasingType::ElasticOut; break;
        case 11: et = gui::EasingType::BounceOut; break;
        default: et = gui::EasingType::Linear; break;
    }
    anim->Start(from, to, ms / 1000.0f, et);

    repaint_of(id);   // 动画帧由窗口 WM_TIMER 驱动（HasActiveAnimation 判定）
    return animId;
}

QOTGUI_API int qg_animate_stop(int anim_id) {
    gui::Animation* anim = nullptr;
    {
        std::lock_guard<std::mutex> lk(g_mutex);
        auto it = g_anims.find(anim_id);
        if (it == g_anims.end()) return 0;
        anim = it->second.anim;
        g_anims.erase(it);
    }
    if (anim) anim->Stop();   // Manager 会在下一帧回收已停动画
    return 1;
}

// ---- Canvas 自定义绘制 ----

QOTGUI_API int qg_canvas_on_draw(int id, qg_draw_fn cb, void* userdata) {
    gui::UIComponent* c = comp_of(id);
    if (!c) return 0;
    auto* cv = dynamic_cast<gui::Canvas*>(c);
    if (!cv) return 0;
    if (!cb) {
        cv->OnDraw(nullptr);
        return 1;
    }
    cv->OnDraw([cb, userdata, cv](gui::Renderer* r, float w, float h) {
        g_drawRenderer = r;
        const gui::Rect& b = cv->GetBounds();
        g_drawOffX = b.left;
        g_drawOffY = b.top;
        cb(w, h, userdata);
        g_drawRenderer = nullptr;
    });
    return 1;
}

QOTGUI_API const qg_draw_api* qg_draw_api_table(void) {
    static qg_draw_api api = {
        /* fill_rect */
        [](float x, float y, float w, float h, float r, float g, float b, float a) {
            if (!g_drawRenderer) return;
            g_drawRenderer->FillRect(
                gui::Rect(x + g_drawOffX, y + g_drawOffY,
                          x + g_drawOffX + w, y + g_drawOffY + h),
                gui::Color(r, g, b, a));
        },
        /* stroke_rect */
        [](float x, float y, float w, float h, float r, float g, float b, float a, float width) {
            if (!g_drawRenderer) return;
            g_drawRenderer->DrawRect(
                gui::Rect(x + g_drawOffX, y + g_drawOffY,
                          x + g_drawOffX + w, y + g_drawOffY + h),
                gui::Color(r, g, b, a), width);
        },
        /* fill_round_rect */
        [](float x, float y, float w, float h, float radius, float r, float g, float b, float a) {
            if (!g_drawRenderer) return;
            gui::RoundedRect rr;
            rr.rect = gui::Rect(x + g_drawOffX, y + g_drawOffY,
                                x + g_drawOffX + w, y + g_drawOffY + h);
            rr.radiusX = rr.radiusY = radius;
            g_drawRenderer->FillRoundedRect(rr, gui::Color(r, g, b, a));
        },
        /* stroke_round_rect */
        [](float x, float y, float w, float h, float radius, float r, float g, float b, float a, float width) {
            if (!g_drawRenderer) return;
            gui::RoundedRect rr;
            rr.rect = gui::Rect(x + g_drawOffX, y + g_drawOffY,
                                x + g_drawOffX + w, y + g_drawOffY + h);
            rr.radiusX = rr.radiusY = radius;
            g_drawRenderer->DrawRoundedRect(rr, gui::Color(r, g, b, a), width);
        },
        /* fill_ellipse */
        [](float cx, float cy, float rx, float ry, float r, float g, float b, float a) {
            if (!g_drawRenderer) return;
            gui::Ellipse e;
            e.center = gui::Point(cx + g_drawOffX, cy + g_drawOffY);
            e.radiusX = rx;
            e.radiusY = ry;
            g_drawRenderer->FillEllipse(e, gui::Color(r, g, b, a));
        },
        /* stroke_ellipse */
        [](float cx, float cy, float rx, float ry, float r, float g, float b, float a, float width) {
            if (!g_drawRenderer) return;
            gui::Ellipse e;
            e.center = gui::Point(cx + g_drawOffX, cy + g_drawOffY);
            e.radiusX = rx;
            e.radiusY = ry;
            g_drawRenderer->DrawEllipse(e, gui::Color(r, g, b, a), width);
        },
        /* draw_line */
        [](float x1, float y1, float x2, float y2, float r, float g, float b, float a, float width) {
            if (!g_drawRenderer) return;
            g_drawRenderer->DrawLine(
                gui::Point(x1 + g_drawOffX, y1 + g_drawOffY),
                gui::Point(x2 + g_drawOffX, y2 + g_drawOffY),
                gui::Color(r, g, b, a), width);
        },
        /* draw_text */
        [](const char* utf8, float x, float y, float size, float r, float g, float b, float a) {
            if (!g_drawRenderer) return;
            std::wstring t = to_w(utf8 ? utf8 : "");
            // 允许换行符分隔的多行文本（按行绘制）
            float lineH = size * 1.35f;
            float cy = y + g_drawOffY;
            size_t pos = 0;
            while (pos <= t.size()) {
                size_t nl = t.find(L'\n', pos);
                std::wstring line = (nl == std::wstring::npos) ? t.substr(pos) : t.substr(pos, nl - pos);
                if (!line.empty()) {
                    gui::Rect rr(x + g_drawOffX, cy,
                                 x + g_drawOffX + 10000.0f, cy + lineH);
                    g_drawRenderer->DrawTextAligned(line, rr, gui::Color(r, g, b, a),
                                                    size, false, 0, 0, false);
                }
                cy += lineH;
                if (nl == std::wstring::npos) break;
                pos = nl + 1;
            }
        },
        /* measure_text */
        [](const char* utf8, float size, float* out_w, float* out_h) {
            if (out_w) *out_w = 0;
            if (out_h) *out_h = 0;
            if (!g_drawRenderer || !utf8) return;
            gui::Size sz = g_drawRenderer->MeasureText(to_w(utf8), size, false);
            if (out_w) *out_w = sz.width;
            if (out_h) *out_h = sz.height;
        },
        /* fill_gradient_rect */
        [](float x, float y, float w, float h,
           float r0, float g0, float b0, float a0,
           float r1, float g1, float b1, float a1, int vertical) {
            if (!g_drawRenderer) return;
            g_drawRenderer->FillRectGradient(
                gui::Rect(x + g_drawOffX, y + g_drawOffY,
                          x + g_drawOffX + w, y + g_drawOffY + h),
                gui::Color(r0, g0, b0, a0), gui::Color(r1, g1, b1, a1), vertical != 0);
        },
        /* push_clip */
        [](float x, float y, float w, float h) {
            if (!g_drawRenderer) return;
            g_drawRenderer->PushClip(
                gui::Rect(x + g_drawOffX, y + g_drawOffY,
                          x + g_drawOffX + w, y + g_drawOffY + h));
        },
        /* pop_clip */
        [](void) {
            if (!g_drawRenderer) return;
            g_drawRenderer->PopClip();
        },
        /* push_translate */
        [](float dx, float dy) {
            if (!g_drawRenderer) return;
            g_drawRenderer->PushTransform();
            g_drawRenderer->Translate(dx, dy);
        },
        /* push_rotate */
        [](float cx, float cy, float radians) {
            if (!g_drawRenderer) return;
            g_drawRenderer->PushTransform();
            g_drawRenderer->Rotate(cx + g_drawOffX, cy + g_drawOffY, radians);
        },
        /* push_scale */
        [](float cx, float cy, float sx, float sy) {
            if (!g_drawRenderer) return;
            g_drawRenderer->PushTransform();
            g_drawRenderer->Scale(cx + g_drawOffX, cy + g_drawOffY, sx, sy);
        },
        /* pop_transform */
        [](void) {
            if (!g_drawRenderer) return;
            g_drawRenderer->PopTransform();
        },
        /* draw_image */
        [](int img, float x, float y, float w, float h, float alpha) {
            if (!g_drawRenderer) return;
            ID2D1Bitmap *bmp = nullptr;
            {
                std::lock_guard<std::mutex> lk(g_mutex);
                auto it = g_images.find(img);
                if (it != g_images.end()) bmp = it->second;
            }
            if (!bmp) return;
            D2D1_RECT_F dst = D2D1::RectF(x + g_drawOffX, y + g_drawOffY,
                                          x + g_drawOffX + w, y + g_drawOffY + h);
            bool layered = false;
            if (alpha < 0.999f) {
                layered = g_drawRenderer->PushLayerOpacity(
                    alpha, gui::Rect(dst.left, dst.top, dst.right, dst.bottom));
            }
            g_drawRenderer->GetRenderTarget()->DrawBitmap(bmp, &dst);
            if (layered) g_drawRenderer->PopLayerOpacity();
        },
        /* push_blur */
        [](float radius, float x, float y, float w, float h) {
            if (!g_drawRenderer) return;
            g_drawRenderer->PushBlurLayer(
                radius,
                gui::Rect(x + g_drawOffX, y + g_drawOffY,
                          x + g_drawOffX + w, y + g_drawOffY + h));
        },
        /* pop_blur */
        [](void) {
            if (!g_drawRenderer) return;
            g_drawRenderer->PopBlurLayer();
        },
    };
    return &api;
}

// ---- 绘制原语扁平导出（转发 api 表；语义逐位一致） ----

QOTGUI_API void qg_draw_fill_rect(float x, float y, float w, float h,
                                  float r, float g, float b, float a) {
    qg_draw_api_table()->fill_rect(x, y, w, h, r, g, b, a);
}

QOTGUI_API void qg_draw_stroke_rect(float x, float y, float w, float h,
                                    float r, float g, float b, float a, float width) {
    qg_draw_api_table()->stroke_rect(x, y, w, h, r, g, b, a, width);
}

QOTGUI_API void qg_draw_fill_round_rect(float x, float y, float w, float h, float radius,
                                        float r, float g, float b, float a) {
    qg_draw_api_table()->fill_round_rect(x, y, w, h, radius, r, g, b, a);
}

QOTGUI_API void qg_draw_stroke_round_rect(float x, float y, float w, float h, float radius,
                                          float r, float g, float b, float a, float width) {
    qg_draw_api_table()->stroke_round_rect(x, y, w, h, radius, r, g, b, a, width);
}

QOTGUI_API void qg_draw_fill_ellipse(float cx, float cy, float rx, float ry,
                                     float r, float g, float b, float a) {
    qg_draw_api_table()->fill_ellipse(cx, cy, rx, ry, r, g, b, a);
}

QOTGUI_API void qg_draw_stroke_ellipse(float cx, float cy, float rx, float ry,
                                       float r, float g, float b, float a, float width) {
    qg_draw_api_table()->stroke_ellipse(cx, cy, rx, ry, r, g, b, a, width);
}

QOTGUI_API void qg_draw_line(float x1, float y1, float x2, float y2,
                             float r, float g, float b, float a, float width) {
    qg_draw_api_table()->draw_line(x1, y1, x2, y2, r, g, b, a, width);
}

QOTGUI_API void qg_draw_text(const char *utf8, float x, float y, float size,
                             float r, float g, float b, float a) {
    qg_draw_api_table()->draw_text(utf8, x, y, size, r, g, b, a);
}

QOTGUI_API void qg_draw_measure_text(const char *utf8, float size, float *out_w, float *out_h) {
    qg_draw_api_table()->measure_text(utf8, size, out_w, out_h);
}

QOTGUI_API void qg_draw_gradient_rect(float x, float y, float w, float h,
                                      float r0, float g0, float b0, float a0,
                                      float r1, float g1, float b1, float a1, int vertical) {
    qg_draw_api_table()->fill_gradient_rect(x, y, w, h, r0, g0, b0, a0, r1, g1, b1, a1, vertical);
}

QOTGUI_API void qg_draw_push_clip(float x, float y, float w, float h) {
    qg_draw_api_table()->push_clip(x, y, w, h);
}

QOTGUI_API void qg_draw_pop_clip(void) {
    qg_draw_api_table()->pop_clip();
}

QOTGUI_API void qg_draw_push_translate(float dx, float dy) {
    qg_draw_api_table()->push_translate(dx, dy);
}

QOTGUI_API void qg_draw_push_rotate(float cx, float cy, float radians) {
    qg_draw_api_table()->push_rotate(cx, cy, radians);
}

QOTGUI_API void qg_draw_push_scale(float cx, float cy, float sx, float sy) {
    qg_draw_api_table()->push_scale(cx, cy, sx, sy);
}

QOTGUI_API void qg_draw_pop_transform(void) {
    qg_draw_api_table()->pop_transform();
}

QOTGUI_API void qg_draw_image(int img, float x, float y, float w, float h, float alpha) {
    qg_draw_api_table()->draw_image(img, x, y, w, h, alpha);
}

QOTGUI_API void qg_draw_push_blur(float radius, float x, float y, float w, float h) {
    qg_draw_api_table()->push_blur(radius, x, y, w, h);
}

QOTGUI_API void qg_draw_pop_blur(void) {
    qg_draw_api_table()->pop_blur();
}

// ---- 文本富能力（v3 ·  4；与扁平绘制原语同一执行上下文）----

QOTGUI_API void qg_draw_text_ex(const char *utf8, float x, float y, float w, float h,
                                float size, float r, float g, float b, float a,
                                int bold, int halign, int valign, int wrap, int ellipsis) {
    if (!g_drawRenderer || !utf8) return;
    std::wstring t = to_w(utf8);
    // 多行文本（\n 分隔）逐行富绘制（行高 = size * 1.35，与扁平版一致）
    float lineH = size * 1.35f;
    float cy = y + g_drawOffY;
    size_t pos = 0;
    std::wstring s = t;
    if (s.empty()) return;
    for (;;) {
        size_t nl = s.find(L'\n', pos);
        std::wstring line = (nl == std::wstring::npos) ? s.substr(pos) : s.substr(pos, nl - pos);
        gui::Rect rr(x + g_drawOffX, cy,
                     x + g_drawOffX + (w > 0.0f ? w : 10000.0f), cy + (h > 0.0f ? h : lineH));
        g_drawRenderer->DrawTextAlignedEx(line, rr, gui::Color(r, g, b, a),
                                          size, bold != 0, halign, valign,
                                          wrap != 0, ellipsis != 0);
        if (nl == std::wstring::npos) break;
        cy += lineH;
        pos = nl + 1;
    }
}

QOTGUI_API void qg_draw_measure_text_ex(const char *utf8, float size, int bold,
                                        int wrap, float maxw,
                                        float *out_w, float *out_h) {
    if (out_w) *out_w = 0;
    if (out_h) *out_h = 0;
    if (!utf8) return;
    // 测量不依赖绘制上下文：Canvas 回调内用当前渲染器，回调外任取活窗口
    // （TextFormat 缓存在渲染器上，测量本身不碰渲染目标——布局系统要在
    // 回调外量行高）
    gui::Renderer *r = g_drawRenderer ? g_drawRenderer : renderer_for_image();
    if (!r) return;
    gui::Size sz = r->MeasureTextEx(to_w(utf8), size, bold != 0, wrap != 0, maxw);
    if (out_w) *out_w = sz.width;
    if (out_h) *out_h = sz.height;
}

// ===========================================================================
// 绘制原语 —— ABI 实现
//   状态表：g_brushes（画刷句柄）/ g_surfaces（离屏面句柄）与 g_images
//   同一先例（设备重建后失效；free 显式回收）。路径构建器与富文本 span
//   挂在 g_drawRenderer（当前绘制上下文）上。
// ===========================================================================

namespace {

std::unordered_map<int, ID2D1Brush *> g_brushes;
int g_nextBrushId = 1;

struct BrushBuilder {
    bool active = false;
    bool radial = false;
    float p[8] = {0};                     // 线性 x0,y0,x1,y1 / 径向 cx,cy,fx,fy,rx,ry
    std::vector<float> pos;
    std::vector<gui::Color> colors;
} g_brushBuilder;

struct SurfaceEntry {
    ID2D1Bitmap *bmp = nullptr;
    ID2D1BitmapRenderTarget *rt = nullptr;   // HwndRT 回退路径持有
};
std::unordered_map<int, SurfaceEntry> g_surfaces;
int g_nextSurfaceId = 1;
int g_surfaceActive = 0;                // 当前 begin 的离屏面（0 = 无）

ID2D1Brush *brush_of(int id) {
    std::lock_guard<std::mutex> lk(g_mutex);
    auto it = g_brushes.find(id);
    return it == g_brushes.end() ? nullptr : it->second;
}

SurfaceEntry surface_of(int id) {
    std::lock_guard<std::mutex> lk(g_mutex);
    auto it = g_surfaces.find(id);
    return it == g_surfaces.end() ? SurfaceEntry{} : it->second;
}

// v5 绘制上下文：canvas 回调内的 renderer + 画布偏移（与扁平原语同纪律）
ID2D1Bitmap *image_bmp_of(int img) {
    std::lock_guard<std::mutex> lk(g_mutex);
    auto it = g_images.find(img);
    return it == g_images.end() ? nullptr : it->second;
}

}  // namespace

/* ---- 路径几何 ---- */

// v6：路径构建器回落——无绘制上下文（vg_run 前/Canvas 外）时挂任一活窗口
// 渲染器（几何挂 factory，构建不碰渲染目标；vg_path_store 固化后跨设备安全）。
// 绘制类（fill/stroke）仍要求绘制上下文（语义不变）。
static gui::Renderer *path_renderer() {
    return g_drawRenderer ? g_drawRenderer : renderer_for_image();
}

QOTGUI_API int qg_path_reset(void) {
    gui::Renderer *r = path_renderer();
    if (!r) return 0;
    return r->PathReset() ? 1 : 0;
}

QOTGUI_API int qg_path_move_to(float x, float y) {
    gui::Renderer *r = path_renderer();
    if (!r) return 0;
    r->PathMoveTo(x + g_drawOffX, y + g_drawOffY);
    return 1;
}

QOTGUI_API int qg_path_line_to(float x, float y) {
    gui::Renderer *r = path_renderer();
    if (!r) return 0;
    r->PathLineTo(x + g_drawOffX, y + g_drawOffY);
    return 1;
}

QOTGUI_API int qg_path_quad_to(float cx, float cy, float x, float y) {
    gui::Renderer *r = path_renderer();
    if (!r) return 0;
    r->PathQuadTo(cx + g_drawOffX, cy + g_drawOffY,
                  x + g_drawOffX, y + g_drawOffY);
    return 1;
}

QOTGUI_API int qg_path_bezier_to(float c1x, float c1y, float c2x, float c2y, float x, float y) {
    gui::Renderer *r = path_renderer();
    if (!r) return 0;
    r->PathBezierTo(c1x + g_drawOffX, c1y + g_drawOffY,
                    c2x + g_drawOffX, c2y + g_drawOffY,
                    x + g_drawOffX, y + g_drawOffY);
    return 1;
}

QOTGUI_API int qg_path_arc(float cx, float cy, float rx, float ry, float startRad, float sweepRad) {
    gui::Renderer *r = path_renderer();
    if (!r) return 0;
    r->PathArcTo(cx + g_drawOffX, cy + g_drawOffY, rx, ry, startRad, sweepRad);
    return 1;
}

QOTGUI_API int qg_path_svg_arc(float rx, float ry, float rotationRad,
                                int largeArc, int sweep, float x, float y) {
    gui::Renderer *r = path_renderer();
    if (!r) return 0;
    r->PathSvgArcTo(rx, ry, rotationRad, largeArc != 0, sweep != 0,
                    x + g_drawOffX, y + g_drawOffY);
    return 1;
}

QOTGUI_API int qg_path_rect(float x, float y, float w, float h) {
    gui::Renderer *r = path_renderer();
    if (!r) return 0;
    r->PathAddRect(x + g_drawOffX, y + g_drawOffY, w, h);
    return 1;
}

QOTGUI_API int qg_path_round_rect(float x, float y, float w, float h, float rx, float ry) {
    gui::Renderer *r = path_renderer();
    if (!r) return 0;
    r->PathAddRoundRect(x + g_drawOffX, y + g_drawOffY, w, h, rx, ry);
    return 1;
}

QOTGUI_API int qg_path_ellipse(float cx, float cy, float rx, float ry) {
    gui::Renderer *r = path_renderer();
    if (!r) return 0;
    r->PathAddEllipse(cx + g_drawOffX, cy + g_drawOffY, rx, ry);
    return 1;
}

QOTGUI_API int qg_path_close(void) {
    gui::Renderer *r = path_renderer();
    if (!r) return 0;
    r->PathClose();
    return 1;
}

QOTGUI_API int qg_path_fill(float r, float g, float b, float a) {
    if (!g_drawRenderer) return 0;
    g_drawRenderer->FillPath(gui::Color(r, g, b, a));
    return 1;
}

QOTGUI_API int qg_path_fill_brush(int brush) {
    if (!g_drawRenderer) return 0;
    ID2D1Brush *br = brush_of(brush);
    if (!br) return 0;
    g_drawRenderer->FillPathBrush(br);
    return 1;
}

QOTGUI_API int qg_path_stroke(float r, float g, float b, float a, float width) {
    if (!g_drawRenderer) return 0;
    g_drawRenderer->StrokePath(gui::Color(r, g, b, a), width);
    return 1;
}

QOTGUI_API int qg_path_stroke_brush(int brush, float width) {
    if (!g_drawRenderer) return 0;
    ID2D1Brush *br = brush_of(brush);
    if (!br) return 0;
    g_drawRenderer->StrokePathBrush(br, width);
    return 1;
}

QOTGUI_API int qg_path_bounds(float *x, float *y, float *w, float *h) {
    if (x) *x = 0;
    if (y) *y = 0;
    if (w) *w = 0;
    if (h) *h = 0;
    gui::Renderer *r = path_renderer();
    if (!r) return 0;
    gui::Rect b;
    if (!r->PathBounds(&b)) return 0;
    if (x) *x = b.left - g_drawOffX;    // 返回画布本地坐标（与输入同系）
    if (y) *y = b.top - g_drawOffY;
    if (w) *w = b.Width();
    if (h) *h = b.Height();
    return 1;
}

QOTGUI_API int qg_path_hit(float x, float y) {
    gui::Renderer *r = path_renderer();
    if (!r) return 0;
    bool hit = false;
    if (!r->PathHitTest(x + g_drawOffX, y + g_drawOffY, &hit)) return 0;
    return hit ? 1 : 0;
}

QOTGUI_API int qg_path_stroke_hit(float x, float y, float width) {
    gui::Renderer *r = path_renderer();
    if (!r) return 0;
    bool hit = false;
    if (!r->PathStrokeHitTest(x + g_drawOffX, y + g_drawOffY, width, &hit)) return 0;
    return hit ? 1 : 0;
}

QOTGUI_API int qg_path_line_cap(int cap) {
    if (!g_drawRenderer) return 0;
    g_drawRenderer->SetLineCap(cap);
    return 1;
}

QOTGUI_API int qg_path_line_join(int join) {
    if (!g_drawRenderer) return 0;
    g_drawRenderer->SetLineJoin(join);
    return 1;
}

QOTGUI_API int qg_path_miter_limit(float limit) {
    if (!g_drawRenderer) return 0;
    g_drawRenderer->SetMiterLimit(limit);
    return 1;
}

QOTGUI_API int qg_path_dash(const float *dashes, int count, float phase) {
    if (!g_drawRenderer) return 0;
    g_drawRenderer->SetDash(dashes, count, phase);
    return 1;
}

/* ---- 画刷系统 ---- */

QOTGUI_API int qg_brush_solid(float r, float g, float b, float a) {
    gui::Renderer *rend = g_drawRenderer ? g_drawRenderer : renderer_for_image();
    if (!rend) return 0;
    ID2D1Brush *br = rend->CreateSolidBrushObj(gui::Color(r, g, b, a));
    if (!br) return 0;
    std::lock_guard<std::mutex> lk(g_mutex);
    int id = g_nextBrushId++;
    g_brushes[id] = br;
    return id;
}

QOTGUI_API int qg_brush_linear_begin(float x0, float y0, float x1, float y1) {
    g_brushBuilder = BrushBuilder{};
    g_brushBuilder.active = true;
    g_brushBuilder.radial = false;
    g_brushBuilder.p[0] = x0;
    g_brushBuilder.p[1] = y0;
    g_brushBuilder.p[2] = x1;
    g_brushBuilder.p[3] = y1;
    return 1;
}

QOTGUI_API int qg_brush_radial_begin(float cx, float cy, float fx, float fy,
                                      float rx, float ry) {
    g_brushBuilder = BrushBuilder{};
    g_brushBuilder.active = true;
    g_brushBuilder.radial = true;
    g_brushBuilder.p[0] = cx;
    g_brushBuilder.p[1] = cy;
    g_brushBuilder.p[2] = fx;
    g_brushBuilder.p[3] = fy;
    g_brushBuilder.p[4] = rx;
    g_brushBuilder.p[5] = ry;
    return 1;
}

QOTGUI_API int qg_brush_add_stop(float pos, float r, float g, float b, float a) {
    if (!g_brushBuilder.active) return 0;
    g_brushBuilder.pos.push_back(pos);
    g_brushBuilder.colors.push_back(gui::Color(r, g, b, a));
    return 1;
}

QOTGUI_API int qg_brush_end(void) {
    if (!g_brushBuilder.active) return 0;
    gui::Renderer *rend = g_drawRenderer ? g_drawRenderer : renderer_for_image();
    if (!rend || g_brushBuilder.pos.size() < 2) {
        g_brushBuilder = BrushBuilder{};
        return 0;
    }
    // 画布偏移：渐变几何随绘制上下文平移（与形状原语同坐标系）
    float ox = g_drawRenderer ? g_drawOffX : 0.0f;
    float oy = g_drawRenderer ? g_drawOffY : 0.0f;
    ID2D1Brush *br = nullptr;
    if (g_brushBuilder.radial) {
        br = rend->CreateRadialBrushObj(
            g_brushBuilder.p[0] + ox, g_brushBuilder.p[1] + oy,
            g_brushBuilder.p[2] + ox, g_brushBuilder.p[3] + oy,
            g_brushBuilder.p[4], g_brushBuilder.p[5],
            g_brushBuilder.pos.data(), g_brushBuilder.colors.data(),
            static_cast<int>(g_brushBuilder.pos.size()));
    } else {
        br = rend->CreateLinearBrushObj(
            g_brushBuilder.p[0] + ox, g_brushBuilder.p[1] + oy,
            g_brushBuilder.p[2] + ox, g_brushBuilder.p[3] + oy,
            g_brushBuilder.pos.data(), g_brushBuilder.colors.data(),
            static_cast<int>(g_brushBuilder.pos.size()));
    }
    g_brushBuilder = BrushBuilder{};
    if (!br) return 0;
    std::lock_guard<std::mutex> lk(g_mutex);
    int id = g_nextBrushId++;
    g_brushes[id] = br;
    return id;
}

QOTGUI_API void qg_brush_free(int brush) {
    ID2D1Brush *br = nullptr;
    {
        std::lock_guard<std::mutex> lk(g_mutex);
        auto it = g_brushes.find(brush);
        if (it == g_brushes.end()) return;
        br = it->second;
        g_brushes.erase(it);
    }
    if (br) br->Release();
}

QOTGUI_API int qg_brush_extend(int brush, int modeX, int modeY) {
    ID2D1Brush *br = brush_of(brush);
    if (!br) return 0;
    return gui::Renderer::BrushSetExtend(br, modeX, modeY) ? 1 : 0;
}

QOTGUI_API int qg_brush_transform(int brush,
                                   float m11, float m12, float m21, float m22,
                                   float dx, float dy) {
    ID2D1Brush *br = brush_of(brush);
    if (!br) return 0;
    return gui::Renderer::BrushSetTransform(br, m11, m12, m21, m22, dx, dy) ? 1 : 0;
}

QOTGUI_API int qg_brush_bitmap(int img) {
    ID2D1Bitmap *bmp = image_bmp_of(img);
    if (!bmp) return 0;
    gui::Renderer *rend = g_drawRenderer ? g_drawRenderer : renderer_for_image();
    if (!rend) return 0;
    ID2D1Brush *br = rend->CreateBitmapBrushObj(bmp);
    if (!br) return 0;
    std::lock_guard<std::mutex> lk(g_mutex);
    int id = g_nextBrushId++;
    g_brushes[id] = br;
    return id;
}

/* ---- 形状 × 画刷 ---- */

QOTGUI_API int qg_draw_fill_rect_brush(int brush, float x, float y, float w, float h) {
    if (!g_drawRenderer) return 0;
    ID2D1Brush *br = brush_of(brush);
    if (!br) return 0;
    g_drawRenderer->FillRectBrush(
        gui::Rect(x + g_drawOffX, y + g_drawOffY,
                  x + g_drawOffX + w, y + g_drawOffY + h), br);
    return 1;
}

QOTGUI_API int qg_draw_stroke_rect_brush(int brush, float x, float y, float w, float h, float width) {
    if (!g_drawRenderer) return 0;
    ID2D1Brush *br = brush_of(brush);
    if (!br) return 0;
    g_drawRenderer->DrawRectBrush(
        gui::Rect(x + g_drawOffX, y + g_drawOffY,
                  x + g_drawOffX + w, y + g_drawOffY + h), br, width);
    return 1;
}

QOTGUI_API int qg_draw_fill_round_rect_brush(int brush, float x, float y, float w, float h, float radius) {
    if (!g_drawRenderer) return 0;
    ID2D1Brush *br = brush_of(brush);
    if (!br) return 0;
    gui::RoundedRect rr;
    rr.rect = gui::Rect(x + g_drawOffX, y + g_drawOffY,
                        x + g_drawOffX + w, y + g_drawOffY + h);
    rr.radiusX = rr.radiusY = radius;
    g_drawRenderer->FillRoundedRectBrush(rr, br);
    return 1;
}

QOTGUI_API int qg_draw_stroke_round_rect_brush(int brush, float x, float y, float w, float h, float radius, float width) {
    if (!g_drawRenderer) return 0;
    ID2D1Brush *br = brush_of(brush);
    if (!br) return 0;
    gui::RoundedRect rr;
    rr.rect = gui::Rect(x + g_drawOffX, y + g_drawOffY,
                        x + g_drawOffX + w, y + g_drawOffY + h);
    rr.radiusX = rr.radiusY = radius;
    g_drawRenderer->DrawRoundedRectBrush(rr, br, width);
    return 1;
}

QOTGUI_API int qg_draw_fill_ellipse_brush(int brush, float cx, float cy, float rx, float ry) {
    if (!g_drawRenderer) return 0;
    ID2D1Brush *br = brush_of(brush);
    if (!br) return 0;
    g_drawRenderer->FillEllipseBrush(
        gui::Ellipse(gui::Point(cx + g_drawOffX, cy + g_drawOffY), rx, ry), br);
    return 1;
}

QOTGUI_API int qg_draw_stroke_ellipse_brush(int brush, float cx, float cy, float rx, float ry, float width) {
    if (!g_drawRenderer) return 0;
    ID2D1Brush *br = brush_of(brush);
    if (!br) return 0;
    g_drawRenderer->DrawEllipseBrush(
        gui::Ellipse(gui::Point(cx + g_drawOffX, cy + g_drawOffY), rx, ry), br, width);
    return 1;
}

/* ---- 富文本（inline 混排） ---- */

QOTGUI_API int qg_rich_begin(void) {
    if (!g_drawRenderer) return 0;
    g_drawRenderer->RichReset();
    return 1;
}

QOTGUI_API int qg_rich_add(const char *utf8, float size,
                            float r, float g, float b, float a,
                            int weight, int italic, int underline, int strikethrough) {
    if (!g_drawRenderer || !utf8) return 0;
    gui::Renderer::RichSpan span;
    span.text = to_w(utf8);
    if (span.text.empty()) return 0;
    span.size = size > 0.0f ? size : 16.0f;
    span.color = gui::Color(r, g, b, a);
    span.weight = (weight >= 100 && weight <= 900) ? weight : 400;
    span.italic = italic != 0;
    span.underline = underline != 0;
    span.strikethrough = strikethrough != 0;
    g_drawRenderer->RichAdd(span);
    return 1;
}

QOTGUI_API int qg_rich_add_brush(const char *utf8, float size, int brush,
                                  int weight, int italic, int underline, int strikethrough) {
    if (!g_drawRenderer || !utf8) return 0;
    ID2D1Brush *br = brush_of(brush);
    if (!br) return 0;
    gui::Renderer::RichSpan span;
    span.text = to_w(utf8);
    if (span.text.empty()) return 0;
    span.size = size > 0.0f ? size : 16.0f;
    span.weight = (weight >= 100 && weight <= 900) ? weight : 400;
    span.italic = italic != 0;
    span.underline = underline != 0;
    span.strikethrough = strikethrough != 0;
    span.brush = br;
    g_drawRenderer->RichAdd(span);
    return 1;
}

QOTGUI_API int qg_rich_line_height(float lineH, float baseline) {
    if (!g_drawRenderer) return 0;
    g_drawRenderer->RichSetLineHeight(lineH, baseline);
    return 1;
}

QOTGUI_API int qg_rich_spacing(float leading, float trailing,
                                float advance, float minAdvance) {
    if (!g_drawRenderer) return 0;
#if defined(__IDWriteTextLayout1_INTERFACE_DEFINED__)
    g_drawRenderer->RichSetSpacing(leading, trailing, advance, minAdvance);
    return 1;
#else
    // 当前构建环境的 SDK 头无 IDWriteTextLayout1（DWrite2）—— 如实返回不支持
    (void)leading; (void)trailing; (void)advance; (void)minAdvance;
    return 0;
#endif
}

QOTGUI_API int qg_rich_draw(float x, float y, float w, float h,
                             int halign, int valign, int wrap) {
    if (!g_drawRenderer) return 0;
    gui::Rect r(x + g_drawOffX, y + g_drawOffY,
                x + g_drawOffX + (w > 0.0f ? w : 10000.0f),
                y + g_drawOffY + (h > 0.0f ? h : 10000.0f));
    return g_drawRenderer->RichDraw(r, halign, valign, wrap != 0) ? 1 : 0;
}

QOTGUI_API int qg_rich_measure(int wrap, float maxw, float *out_w, float *out_h) {
    if (out_w) *out_w = 0;
    if (out_h) *out_h = 0;
    if (!g_drawRenderer) return 0;
    gui::Size sz;
    if (!g_drawRenderer->RichMeasure(wrap != 0, maxw, &sz)) return 0;
    if (out_w) *out_w = sz.width;
    if (out_h) *out_h = sz.height;
    return 1;
}

/* ---- 合成效果层 ---- */

QOTGUI_API int qg_draw_push_blend(int mode, float x, float y, float w, float h) {
    if (!g_drawRenderer) return 0;
    gui::Rect bounds(x + g_drawOffX, y + g_drawOffY,
                     x + g_drawOffX + w, y + g_drawOffY + h);
    return g_drawRenderer->PushBlendLayer(mode, bounds) ? 1 : 0;
}

QOTGUI_API int qg_draw_pop_blend(void) {
    if (!g_drawRenderer) return 0;
    g_drawRenderer->PopBlendLayer();
    return 1;
}

QOTGUI_API int qg_draw_push_shadow(float r, float g, float b, float a,
                                    float blur, float dx, float dy,
                                    float x, float y, float w, float h) {
    if (!g_drawRenderer) return 0;
    gui::Rect bounds(x + g_drawOffX, y + g_drawOffY,
                     x + g_drawOffX + w, y + g_drawOffY + h);
    return g_drawRenderer->PushShadowLayer(gui::Color(r, g, b, a), blur, dx, dy, bounds) ? 1 : 0;
}

QOTGUI_API int qg_draw_pop_shadow(void) {
    if (!g_drawRenderer) return 0;
    g_drawRenderer->PopShadowLayer();
    return 1;
}

QOTGUI_API int qg_draw_push_mask(int brush, float x, float y, float w, float h, float opacity) {
    if (!g_drawRenderer) return 0;
    ID2D1Brush *br = brush_of(brush);
    if (!br) return 0;
    gui::Rect bounds(x + g_drawOffX, y + g_drawOffY,
                     x + g_drawOffX + w, y + g_drawOffY + h);
    return g_drawRenderer->PushMaskLayerBrush(br, bounds, opacity) ? 1 : 0;
}

QOTGUI_API int qg_draw_pop_mask(void) {
    if (!g_drawRenderer) return 0;
    g_drawRenderer->PopMaskLayer();
    return 1;
}

QOTGUI_API int qg_draw_push_clip_path(void) {
    if (!g_drawRenderer) return 0;
    return g_drawRenderer->PushClipPath() ? 1 : 0;
}

/* ---- 离屏面 ---- */

QOTGUI_API int qg_surface_create(int w, int h) {
    if (w <= 0 || h <= 0) return 0;
    gui::Renderer *rend = g_drawRenderer ? g_drawRenderer : renderer_for_image();
    if (!rend) return 0;
    ID2D1BitmapRenderTarget *rt = nullptr;
    ID2D1Bitmap *bmp = rend->CreateSurfaceBitmap(static_cast<UINT>(w), static_cast<UINT>(h), &rt);
    if (!bmp) return 0;
    std::lock_guard<std::mutex> lk(g_mutex);
    int id = g_nextSurfaceId++;
    g_surfaces[id] = SurfaceEntry{bmp, rt};
    return id;
}

QOTGUI_API int qg_surface_begin(int id) {
    if (g_surfaceActive != 0) return 0;   // 不嵌套
    SurfaceEntry e = surface_of(id);
    if (!e.bmp) return 0;
    gui::Renderer *rend = g_drawRenderer ? g_drawRenderer : renderer_for_image();
    if (!rend) return 0;
    if (!rend->BeginSurface(e.bmp, e.rt)) return 0;
    g_surfaceActive = id;
    return 1;
}

QOTGUI_API int qg_surface_end(void) {
    if (g_surfaceActive == 0) return 0;
    gui::Renderer *rend = g_drawRenderer ? g_drawRenderer : renderer_for_image();
    if (!rend) return 0;
    rend->EndSurface();
    g_surfaceActive = 0;
    return 1;
}

QOTGUI_API int qg_surface_draw(int id, float x, float y, float w, float h,
                                float alpha, int interp) {
    if (!g_drawRenderer) return 0;
    if (g_surfaceActive == id) return 0;   // 自己不能贴自己（正在绘制中）
    SurfaceEntry e = surface_of(id);
    if (!e.bmp) return 0;
    gui::Rect dst(x + g_drawOffX, y + g_drawOffY,
                  x + g_drawOffX + w, y + g_drawOffY + h);
    return g_drawRenderer->DrawSurfaceBitmap(e.bmp, dst, alpha, interp) ? 1 : 0;
}

QOTGUI_API int qg_surface_pixel(int id, int x, int y,
                                 float *r, float *g, float *b, float *a) {
    if (r) *r = 0;
    if (g) *g = 0;
    if (b) *b = 0;
    if (a) *a = 0;
    SurfaceEntry e = surface_of(id);
    if (!e.bmp) return 0;
    // 像素读回需要 renderer 的 Map 通道（读回不依赖绘制上下文）
    gui::Renderer *rend = g_drawRenderer ? g_drawRenderer : renderer_for_image();
    if (!rend) return 0;
    gui::Color c;
    if (!rend->ReadSurfacePixel(e.bmp, static_cast<UINT>(x), static_cast<UINT>(y), &c))
        return 0;
    if (r) *r = c.r;
    if (g) *g = c.g;
    if (b) *b = c.b;
    if (a) *a = c.a;
    return 1;
}

QOTGUI_API void qg_surface_free(int id) {
    SurfaceEntry e;
    {
        std::lock_guard<std::mutex> lk(g_mutex);
        auto it = g_surfaces.find(id);
        if (it == g_surfaces.end()) return;
        e = it->second;
        g_surfaces.erase(it);
    }
    if (e.bmp) e.bmp->Release();
    if (e.rt) e.rt->Release();
}

/* ---- 图片精控 ---- */

QOTGUI_API int qg_draw_image_ex(int img, float x, float y, float w, float h,
                                 float alpha, int interp,
                                 float srcX, float srcY, float srcW, float srcH) {
    if (!g_drawRenderer) return 0;
    ID2D1Bitmap *bmp = image_bmp_of(img);
    if (!bmp) return 0;
    D2D1_SIZE_U sz = bmp->GetPixelSize();
    float sw = (srcW > 0.0f) ? srcW : static_cast<float>(sz.width);
    float sh = (srcH > 0.0f) ? srcH : static_cast<float>(sz.height);
    gui::Rect src(srcX, srcY, srcX + sw, srcY + sh);
    gui::Rect dst(x + g_drawOffX, y + g_drawOffY,
                  x + g_drawOffX + w, y + g_drawOffY + h);
    g_drawRenderer->DrawImageEx(bmp, dst, &src, alpha, interp);
    return 1;
}

QOTGUI_API int qg_draw_image_round(int img, float x, float y, float w, float h,
                                    float alpha, int interp, float radius) {
    if (!g_drawRenderer) return 0;
    ID2D1Bitmap *bmp = image_bmp_of(img);
    if (!bmp) return 0;
    gui::Rect dst(x + g_drawOffX, y + g_drawOffY,
                  x + g_drawOffX + w, y + g_drawOffY + h);
    if (radius > 0.0f) {
        if (!g_drawRenderer->PushClipRoundRect(dst, radius, radius)) return 0;
        g_drawRenderer->DrawImageEx(bmp, dst, nullptr, alpha, interp);
        g_drawRenderer->PopClip();
    } else {
        g_drawRenderer->DrawImageEx(bmp, dst, nullptr, alpha, interp);
    }
    return 1;
}

/* ---- 能力探测 ---- */

QOTGUI_API int qg_backend_info(int *composed, int *dpi) {
    gui::Renderer *rend = g_drawRenderer ? g_drawRenderer : renderer_for_image();
    if (!rend) return 0;
    if (composed) *composed = rend->IsComposed() ? 1 : 0;
    if (dpi) {
        // GetDpiForWindow（Win10 1607+）；老系统回落 GetDeviceCaps
        HWND owner = nullptr;
        for (auto &kv : g_windows) {
            if (kv.second && kv.second->GetHwnd()) {
                owner = kv.second->GetHwnd();
                break;
            }
        }
        UINT d = 96;
        if (owner) {
            HMODULE u32 = GetModuleHandleW(L"user32.dll");
            using FnGetDpi = UINT(WINAPI *)(HWND);
            auto pGetDpi = reinterpret_cast<FnGetDpi>(
                u32 ? GetProcAddress(u32, "GetDpiForWindow") : nullptr);
            if (pGetDpi) {
                d = pGetDpi(owner);
            } else {
                HDC dc = GetDC(owner);
                d = static_cast<UINT>(GetDeviceCaps(dc, LOGPIXELSX));
                ReleaseDC(owner, dc);
            }
        }
        *dpi = static_cast<int>(d);
    }
    return 1;
}

// ===========================================================================
// v6：声明式视觉模板 —— ABI 实现
//   * 句柄表：g_geometries（几何，挂 factory 跨设备安全）/
//     g_textStyles（文字样式，纯参数）；模板本体表在 gui 层（visual_template.h）
//   * TplEnv 延迟绑定：首个 v6 API 调用时注入（渲染前必然已 apply）
// ===========================================================================

namespace {

std::unordered_map<int, ID2D1PathGeometry *> g_geometries;
int g_nextGeoId = 1;

std::unordered_map<int, gui::TplTsParams> g_textStyles;
int g_nextTsId = 1;

ID2D1PathGeometry *geo_of(int id) {
    std::lock_guard<std::mutex> lk(g_mutex);
    auto it = g_geometries.find(id);
    return it == g_geometries.end() ? nullptr : it->second;
}

const gui::TplTsParams *ts_of(int id) {
    std::lock_guard<std::mutex> lk(g_mutex);
    auto it = g_textStyles.find(id);
    return it == g_textStyles.end() ? nullptr : &it->second;
}

// 持走调用方的几何引用（PathTakeRef 已 AddRef），返回句柄
int tpl_geo_store(ID2D1PathGeometry *geo) {
    if (!geo) return 0;
    std::lock_guard<std::mutex> lk(g_mutex);
    int id = g_nextGeoId++;
    g_geometries[id] = geo;
    return id;
}

// TplEnv 注入（v6 API 入口统一调用；函数内 static 懒初始化）
void ensure_tpl_env() {
    static bool bound = false;
    if (bound) return;
    bound = true;
    gui::g_tplEnv.brushOf = brush_of;
    gui::g_tplEnv.geoOf = geo_of;
    gui::g_tplEnv.geoStore = tpl_geo_store;
    gui::g_tplEnv.tsOf = ts_of;
    gui::g_tplEnv.requestFrame = qg_request_frame;
}

}  // namespace

/* ---- 路径句柄化（几何固化：部件形状/命中/裁剪共用） ---- */

QOTGUI_API int qg_path_store(void) {
    ensure_tpl_env();
    gui::Renderer *rend = g_drawRenderer ? g_drawRenderer : renderer_for_image();
    if (!rend) return 0;
    ID2D1PathGeometry *geo = rend->PathTakeRef();
    if (!geo) return 0;
    return tpl_geo_store(geo);
}

QOTGUI_API void qg_path_free(int path) {
    ID2D1PathGeometry *geo;
    {
        std::lock_guard<std::mutex> lk(g_mutex);
        auto it = g_geometries.find(path);
        if (it == g_geometries.end()) return;
        geo = it->second;
        g_geometries.erase(it);
    }
    if (geo) geo->Release();
}

QOTGUI_API int qg_geo_hit(int path, float x, float y) {
    ID2D1PathGeometry *geo = geo_of(path);
    if (!geo) return 0;
    BOOL hit = FALSE;
    if (FAILED(geo->FillContainsPoint(D2D1::Point2F(x, y), nullptr, 1e-3f, &hit))) return 0;
    return hit ? 1 : 0;
}

/* ---- 文字样式块 ---- */

QOTGUI_API int qg_textstyle_create(float size, int weight, int italic,
                                   int halign, int wrap, const char *family) {
    ensure_tpl_env();
    if (size <= 0.0f) return 0;
    gui::TplTsParams ts;
    ts.size = size;
    ts.weight = (weight >= 100 && weight <= 900) ? weight : 400;
    ts.italic = italic != 0;
    ts.halign = (halign >= 0 && halign <= 2) ? halign : 1;
    ts.valign = 1;
    ts.wrap = wrap != 0;
    ts.family = to_w(family ? family : "");
    std::lock_guard<std::mutex> lk(g_mutex);
    int id = g_nextTsId++;
    g_textStyles[id] = ts;
    return id;
}

QOTGUI_API void qg_textstyle_free(int ts) {
    std::lock_guard<std::mutex> lk(g_mutex);
    g_textStyles.erase(ts);
}

/* ---- 模板数据层 ---- */

QOTGUI_API int qg_tpl_create(void) {
    ensure_tpl_env();
    return gui::tpl_create();
}

QOTGUI_API int qg_tpl_part(int tpl, const char *name, int shape,
                           float shape_a, float shape_b, float shape_c, int path_id,
                           int anchor, int fill_kind, float fr, float fg, float fb, float fa,
                           int fill_brush,
                           int stroke_set, float sr, float sg, float sb, float sa, float sw,
                           float il, float it, float ir, float ib, int textslot) {
    gui::VisualTemplate *t = gui::tpl_find(tpl);
    if (!t || !name || !*name) return 0;
    if (shape < 0 || shape > 6) return 0;
    if (fill_kind < 0 || fill_kind > 3) return 0;

    gui::TplPart p;
    p.name = name;
    p.shape = shape;
    p.shapeA = shape_a; p.shapeB = shape_b; p.shapeC = shape_c;
    p.pathId = path_id;
    p.anchor = (anchor >= 0 && anchor <= 2) ? anchor : 0;
    p.fillKind = fill_kind;
    p.fillColor = gui::Color(fr, fg, fb, fa);
    p.fillBrush = fill_brush;
    p.strokeSet = stroke_set != 0;
    p.strokeColor = gui::Color(sr, sg, sb, sa);
    p.strokeWidth = sw > 0.0f ? sw : 1.0f;
    p.inset[0] = il; p.inset[1] = it; p.inset[2] = ir; p.inset[3] = ib;
    p.textSlot = textslot != 0;

    // 同名 part = 更新（渐进定制默认模板部件）
    gui::TplPart *dst = gui::tpl_part_find(t, p.name);
    if (dst) *dst = p;
    else t->parts.push_back(p);
    return 1;
}

QOTGUI_API int qg_tpl_part_text(int tpl, const char *part, int ts_id,
                                int halign, int valign,
                                float ml, float mt, float mr, float mb,
                                int color_set, float r, float g, float b, float a) {
    gui::VisualTemplate *t = gui::tpl_find(tpl);
    gui::TplPart *p = t ? gui::tpl_part_find(t, part ? part : "") : nullptr;
    if (!p) return 0;
    p->tsId = ts_id;
    if (halign >= 0 && halign <= 2) p->halign = halign;
    if (valign >= 0 && valign <= 2) p->valign = valign;
    p->tmargin[0] = ml; p->tmargin[1] = mt;
    p->tmargin[2] = mr; p->tmargin[3] = mb;
    p->textColorSet = color_set != 0;
    p->textColor = gui::Color(r, g, b, a);
    return 1;
}

QOTGUI_API int qg_tpl_part_dyn(int tpl, const char *part, int value_mode, int value_src,
                               float range, float c2r, float c2g, float c2b, float c2a) {
    gui::VisualTemplate *t = gui::tpl_find(tpl);
    gui::TplPart *p = t ? gui::tpl_part_find(t, part ? part : "") : nullptr;
    if (!p) return 0;
    if (value_mode < 0 || value_mode > 6) return 0;
    p->valueMode = value_mode;
    p->valueSrc = value_src;
    p->valueRange = range;
    p->c2[0] = c2r; p->c2[1] = c2g; p->c2[2] = c2b; p->c2[3] = c2a;
    return 1;
}

QOTGUI_API int qg_tpl_state(int tpl, unsigned state_mask, const char *part, int prop,
                            float v0, float v1, float v2, float v3) {
    gui::VisualTemplate *t = gui::tpl_find(tpl);
    if (!t) return 0;
    // part 名非空时必须指向已登记部件（渐进定制防拼错名静默失效）
    if (part && *part && !gui::tpl_part_find(t, part)) return 0;
    gui::TplOverride ov;
    ov.mask = state_mask;
    ov.part = part ? part : "";
    switch (prop) {
        case 0: ov.fillColorSet = true;  ov.fillColor = gui::Color(v0, v1, v2, v3); break;
        case 1: ov.fillBrushSet = true;  ov.fillBrush = (int)v0; break;
        case 2: ov.strokeColorSet = true; ov.strokeColor = gui::Color(v0, v1, v2, v3); break;
        case 3: ov.strokeWidthSet = true; ov.strokeWidth = v0; break;
        case 4: ov.translateSet = true;  ov.tx = v0; ov.ty = v1; break;
        case 5: ov.scaleSet = true;      ov.sx = v0; ov.sy = (v1 != 0.0f ? v1 : v0); break;
        case 6: ov.rotateSet = true;     ov.rot = v0; break;
        case 7: ov.opacitySet = true;    ov.opacity = v0; break;
        case 8: ov.radiusSet = true;     ov.radius = v0; break;
        default: return 0;
    }
    t->states.push_back(ov);
    return 1;
}

QOTGUI_API int qg_tpl_transition(int tpl, unsigned state_mask, float ms, int easing) {
    gui::VisualTemplate *t = gui::tpl_find(tpl);
    if (!t) return 0;
    gui::TplTransition tr;
    tr.mask = state_mask;
    tr.ms = ms;
    tr.easing = easing;
    t->transitions.push_back(tr);
    return 1;
}

QOTGUI_API void qg_tpl_free(int tpl) {
    gui::tpl_free(tpl);
}

/* ---- 应用与默认模板 ---- */

QOTGUI_API int qg_apply_template(int id, int tpl) {
    ensure_tpl_env();
    gui::UIComponent *c = comp_of(id);
    if (!c) return 0;
    if (tpl <= 0 || !gui::tpl_find(tpl)) return 0;
    c->SetTemplateId(tpl);
    repaint_of(id);
    return 1;
}

QOTGUI_API int qg_clear_template(int id) {
    gui::UIComponent *c = comp_of(id);
    if (!c) return 0;
    c->SetTemplateId(0);
    repaint_of(id);
    return 1;
}

QOTGUI_API int qg_tpl_default(int type) {
    ensure_tpl_env();
    if (type < 0 || type > 6) return 0;
    int id = gui::tpl_create();
    gui::VisualTemplate *t = gui::tpl_find(id);
    if (!t) return 0;
    t->defaultType = type;   // 首次渲染时物化 parts（此时必有 Renderer，path 几何可建）
    return id;
}

}  // extern "C"
