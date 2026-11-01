// ===========================================================================
// qotgui_abi.cpp —— wingui 的 C ABI 实现（解释器与原生程序共用这一份）
// ===========================================================================
#include "qotgui_abi.h"
#include "gui_api.h"

#include <windows.h>
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
};

std::mutex g_mutex;
int g_nextId = 1;
bool g_ready = false;

struct Ctl {
    int id = 0;
    int win = 0;
    gui::ModernWindow* window = nullptr;
    gui::UIComponent* comp = nullptr;
};

std::unordered_map<int, std::unique_ptr<gui::ModernWindow>> g_windows;
std::unordered_map<int, Ctl> g_ctls;    // 含窗口（win 自身也登记，便于统一销毁）
std::deque<Event> g_events;

gui::ModernWindow* win_of(int win) {
    auto it = g_windows.find(win);
    return it == g_windows.end() ? nullptr : it->second.get();
}

void push_event(int id, int kind, double value) {
    std::lock_guard<std::mutex> lk(g_mutex);
    if (g_events.size() > 512) g_events.pop_front();   // 防无消费者时无限堆积
    g_events.push_back(Event{id, kind, value});
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
    gui::Shutdown();
    g_ready = false;
}

QOTGUI_API int qg_create_window(const char* title, int width, int height) {
    if (!g_ready && !qg_init()) return 0;
    auto* w = new gui::ModernWindow();
    if (!w->Initialize(GetModuleHandle(nullptr), SW_SHOW, to_w(title ? title : "QOT Window"),
                       width > 0 ? width : 800, height > 0 ? height : 600)) {
        delete w;
        return 0;
    }
    int id = g_nextId++;
    g_windows[id] = std::unique_ptr<gui::ModernWindow>(w);
    Ctl ctl; ctl.id = id; ctl.win = id; ctl.window = w; ctl.comp = nullptr;
    g_ctls[id] = ctl;
    return id;
}

QOTGUI_API int qg_show_window(int win) {
    gui::ModernWindow* w = win_of(win);
    if (!w) return 0;
    ShowWindow(w->GetHwnd(), SW_SHOW);
    w->RequestRepaint();
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
        if (g_windows.empty()) break;
    }
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

QOTGUI_API int qg_set_window_bg(int win, float r0, float g0, float b0,
                                float r1, float g1, float b1) {
    gui::ModernWindow* w = win_of(win);
    if (!w) return 0;
    w->SetBackgroundGradient(gui::Color(r0, g0, b0, 1), gui::Color(r1, g1, b1, 1));
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
    b->OnClick([id]() { push_event(id, 1, 0.0); });
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
    c->OnChecked([id](bool v) { push_event(id, 2, v ? 1.0 : 0.0); });
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
    t->OnToggle([id](bool v) { push_event(id, 2, v ? 1.0 : 0.0); });
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
    s->OnValueChanged([id](float v) { push_event(id, 2, (double)v); });
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
    c->OnSelectionChanged([id](int idx, const std::wstring&) { push_event(id, 3, (double)idx); });
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
    l->OnSelectionChanged([id](int idx, const std::wstring&) { push_event(id, 3, (double)idx); });
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
    t->OnTabChanged([id](int idx, const std::wstring&) { push_event(id, 3, (double)idx); });
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
// ---- 通用属性 ----

namespace {

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
        it->second.window->RemoveComponent(id);
        it->second.window->RequestRepaint();
    }
    g_ctls.erase(it);
    return 1;
}

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
                if (kind) *kind = 9;
                if (value) *value = 0.0;
                return 1;
            }
            TranslateMessage(&msg);
            DispatchMessage(&msg);
            if (qg_poll_event(id, kind, value)) return 1;
        }
        if (tmo == 0 || GetTickCount() >= deadline) return 0;
        Sleep(1);   // 空闲近零 CPU；事件延迟至多 1ms，手感无差
    }
}

}  // extern "C"