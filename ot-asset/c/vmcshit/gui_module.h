#pragma once
// wingui 已独立到 ot-asset/c/wingui（VM 侧经相对路径引用；VM 已冻结弃管，
// 当前活跃的两条执行路径各自通过 qotgui ABI 接入同一套实现）
#include "../wingui/gui_api.h"
#include "vm_types.h"
#include <unordered_map>
#include <memory>
#include <functional>

namespace qot
{
    inline std::string gui_wstringToString(const std::wstring &wstr)
    {
        if (wstr.empty())
            return "";
        int len = WideCharToMultiByte(CP_UTF8, 0, wstr.c_str(), (int)wstr.size(), nullptr, 0, nullptr, nullptr);
        std::string result(len, '\0');
        WideCharToMultiByte(CP_UTF8, 0, wstr.c_str(), (int)wstr.size(), &result[0], len, nullptr, nullptr);
        return result;
    }

    inline std::wstring gui_stringToWString(const std::string &str)
    {
        if (str.empty())
            return L"";
        int len = MultiByteToWideChar(CP_UTF8, 0, str.c_str(), (int)str.size(), nullptr, 0);
        std::wstring result(len, L'\0');
        MultiByteToWideChar(CP_UTF8, 0, str.c_str(), (int)str.size(), &result[0], len);
        return result;
    }

    inline std::string gui_valueToString(const Value &v)
    {
        if (v.isString())
            return v.asString();
        return v.toString();
    }

    inline std::wstring gui_valueToWString(const Value &v)
    {
        return gui_stringToWString(gui_valueToString(v));
    }

    inline float gui_valueToFloat(const Value &v)
    {
        if (v.isNumber())
            return static_cast<float>(v.asNumber());
        return 0.0f;
    }

    inline int gui_valueToInt(const Value &v)
    {
        if (v.isNumber())
            return static_cast<int>(v.asNumber());
        return 0;
    }

    inline bool gui_valueToBool(const Value &v)
    {
        if (v.isBool())
            return v.asBool();
        if (v.isNumber())
            return v.asNumber() != 0;
        return false;
    }

    inline gui::Color gui_valueToColor(const Value &v)
    {
        if (v.isArray())
        {
            const auto &arr = v.asArray();
            float r = arr.size() > 0 ? static_cast<float>(arr[0].asNumber()) : 0;
            float g = arr.size() > 1 ? static_cast<float>(arr[1].asNumber()) : 0;
            float b = arr.size() > 2 ? static_cast<float>(arr[2].asNumber()) : 0;
            float a = arr.size() > 3 ? static_cast<float>(arr[3].asNumber()) : 1.0f;
            return gui::Color(r, g, b, a);
        }
        return gui::Color(0, 0, 0, 1);
    }

    inline gui::Rect valueToRect(const Value &v)
    {
        if (v.isArray())
        {
            const auto &arr = v.asArray();
            float x = arr.size() > 0 ? static_cast<float>(arr[0].asNumber()) : 0;
            float y = arr.size() > 1 ? static_cast<float>(arr[1].asNumber()) : 0;
            float w = arr.size() > 2 ? static_cast<float>(arr[2].asNumber()) : 100;
            float h = arr.size() > 3 ? static_cast<float>(arr[3].asNumber()) : 40;
            return gui::Rect(x, y, x + w, y + h);
        }
        return gui::Rect(0, 0, 100, 40);
    }
    struct GUIObject
    {
        enum class Type
        {
            None,
            Window,
            Button,
            CheckBox,
            RadioButton,
            ToggleSwitch,
            TextBox,
            ComboBox,
            ListBox,
            TabControl,
            Spinner,
            ProgressBar,
            Slider,
            Card
        };

        Type type = Type::None;
        void *ptr = nullptr;
        std::string id;

        GUIObject() = default;
        GUIObject(Type t, void *p, const std::string &i = "")
            : type(t), ptr(p), id(i) {}
    };
    struct GUIBinding
    {
        Value fn;
        std::vector<Value> bound;
    };
    inline bool gui_parseCallback(const std::vector<Value> &args, GUIBinding &out)
    {
        if (args.size() < 2)
            return false;
        const Value &c = args[1];
        out.bound.clear();
        if (c.isArray())
        {
            const auto &a = c.asArray();
            if (a.empty())
                return false;
            out.fn = a[0];
            out.bound.assign(a.begin() + 1, a.end());
        }
        else
        {
            out.fn = c;
        }
        out.bound.insert(out.bound.end(), args.begin() + 2, args.end());
        if (out.fn.isCodeBlock())
            return true;
        return out.fn.isString() && !out.fn.asString().empty();
    }

    using CallbackExecutor = std::function<void(const std::string &, const std::vector<Value> &)>;
    class GUIManager
    {
    private:
        std::unordered_map<std::string, std::unique_ptr<GUIObject>> m_objects;
        gui::ModernWindow *m_mainWindow = nullptr;
        bool m_initialized = false;
        std::unordered_map<std::string, GUIBinding> m_buttonCallbacks;
        std::unordered_map<std::string, GUIBinding> m_checkboxCallbacks;
        std::unordered_map<std::string, GUIBinding> m_toggleCallbacks;
        std::unordered_map<std::string, GUIBinding> m_comboboxCallbacks;
        std::unordered_map<std::string, GUIBinding> m_listboxCallbacks;
        std::unordered_map<std::string, GUIBinding> m_sliderCallbacks;
        static std::vector<Value> buildArgs(const GUIBinding &b,
                                            std::initializer_list<Value> eventArgs)
        {
            std::vector<Value> a;
            a.reserve(1 + b.bound.size() + eventArgs.size());
            a.push_back(b.fn);
            a.insert(a.end(), b.bound.begin(), b.bound.end());
            a.insert(a.end(), eventArgs.begin(), eventArgs.end());
            return a;
        }

        CallbackExecutor m_callbackExecutor;

        std::string generateId()
        {
            static int counter = 0;
            return "obj_" + std::to_string(++counter);
        }

    public:
        static GUIManager &instance()
        {
            static GUIManager manager;
            return manager;
        }

        bool isInitialized() const { return m_initialized; }
        void setInitialized(bool init) { m_initialized = init; }
        void setCallbackExecutor(CallbackExecutor executor)
        {
            m_callbackExecutor = executor;
        }
        gui::ModernWindow *getMainWindow() { return m_mainWindow; }
        void setMainWindow(gui::ModernWindow *window) { m_mainWindow = window; }
        CallbackExecutor &getCallbackExecutor() { return m_callbackExecutor; }
        std::string storeObject(GUIObject::Type type, void *ptr, const std::string &id = "")
        {
            std::string objId = id.empty() ? generateId() : id;
            m_objects[objId] = std::make_unique<GUIObject>(type, ptr, objId);
            return objId;
        }

        GUIObject *getObject(const std::string &id)
        {
            auto it = m_objects.find(id);
            if (it != m_objects.end())
            {
                return it->second.get();
            }
            return nullptr;
        }

        void *getPtr(const std::string &id)
        {
            auto obj = getObject(id);
            return obj ? obj->ptr : nullptr;
        }

        template <typename T>
        T *getComponent(const std::string &id)
        {
            return static_cast<T *>(getPtr(id));
        }
        template <typename T>
        T *getTyped(const std::string &id, GUIObject::Type want)
        {
            auto obj = getObject(id);
            if (!obj || obj->type != want)
                return nullptr;
            return static_cast<T *>(obj->ptr);
        }
        gui::UIComponent *getUIComponent(const std::string &id)
        {
            auto obj = getObject(id);
            if (!obj || !obj->ptr || obj->type == GUIObject::Type::None ||
                obj->type == GUIObject::Type::Window)
                return nullptr;
            return static_cast<gui::UIComponent *>(obj->ptr);
        }

        bool removeObject(const std::string &id)
        {
            return m_objects.erase(id) > 0;
        }

        void clearObjects()
        {
            m_objects.clear();
        }
        void registerButtonCallback(const std::string &buttonId, const Value &callback,
                                    std::vector<Value> bound = {})
        {
            m_buttonCallbacks[buttonId] = GUIBinding{callback, std::move(bound)};
        }

        void registerCheckboxCallback(const std::string &checkboxId, const Value &callback,
                                      std::vector<Value> bound = {})
        {
            m_checkboxCallbacks[checkboxId] = GUIBinding{callback, std::move(bound)};
        }

        void registerToggleCallback(const std::string &toggleId, const Value &callback,
                                    std::vector<Value> bound = {})
        {
            m_toggleCallbacks[toggleId] = GUIBinding{callback, std::move(bound)};
        }

        void registerComboboxCallback(const std::string &comboId, const Value &callback,
                                      std::vector<Value> bound = {})
        {
            m_comboboxCallbacks[comboId] = GUIBinding{callback, std::move(bound)};
        }

        void registerListboxCallback(const std::string &listId, const Value &callback,
                                     std::vector<Value> bound = {})
        {
            m_listboxCallbacks[listId] = GUIBinding{callback, std::move(bound)};
        }

        void registerSliderCallback(const std::string &sliderId, const Value &callback,
                                    std::vector<Value> bound = {})
        {
            m_sliderCallbacks[sliderId] = GUIBinding{callback, std::move(bound)};
        }
        void triggerButtonClick(const std::string &buttonId)
        {
            auto it = m_buttonCallbacks.find(buttonId);
            if (it != m_buttonCallbacks.end() && m_callbackExecutor)
            {
                m_callbackExecutor("", buildArgs(it->second, {}));
            }
        }

        void triggerCheckboxChange(const std::string &checkboxId, bool checked)
        {
            auto it = m_checkboxCallbacks.find(checkboxId);
            if (it != m_checkboxCallbacks.end() && m_callbackExecutor)
            {
                m_callbackExecutor("", buildArgs(it->second, {Value(checked)}));
            }
        }

        void triggerToggleChange(const std::string &toggleId, bool checked)
        {
            auto it = m_toggleCallbacks.find(toggleId);
            if (it != m_toggleCallbacks.end() && m_callbackExecutor)
            {
                m_callbackExecutor("", buildArgs(it->second, {Value(checked)}));
            }
        }

        void triggerComboboxSelection(const std::string &comboId, int index, const std::wstring &text)
        {
            auto it = m_comboboxCallbacks.find(comboId);
            if (it != m_comboboxCallbacks.end() && m_callbackExecutor)
            {
                std::string textStr = gui_wstringToString(text);
                m_callbackExecutor("", buildArgs(it->second,
                                                 {Value(static_cast<double>(index)), Value(textStr)}));
            }
        }

        void triggerListboxSelection(const std::string &listId, int index, const std::wstring &text)
        {
            auto it = m_listboxCallbacks.find(listId);
            if (it != m_listboxCallbacks.end() && m_callbackExecutor)
            {
                std::string textStr = gui_wstringToString(text);
                m_callbackExecutor("", buildArgs(it->second,
                                                 {Value(static_cast<double>(index)), Value(textStr)}));
            }
        }

        void triggerSliderChange(const std::string &sliderId, float value)
        {
            auto it = m_sliderCallbacks.find(sliderId);
            if (it != m_sliderCallbacks.end() && m_callbackExecutor)
            {
                m_callbackExecutor("", buildArgs(it->second, {Value(static_cast<double>(value))}));
            }
        }

        std::string getButtonCallback(const std::string &buttonId) const
        {
            auto it = m_buttonCallbacks.find(buttonId);
            return it != m_buttonCallbacks.end() ? it->second.fn.toString() : "";
        }
    };
    Value builtin_vg_init(const std::vector<Value> &args);
    Value builtin_vg_uninit(const std::vector<Value> &args);
    Value builtin_vg_create_window(const std::vector<Value> &args);
    Value builtin_vg_show_window(const std::vector<Value> &args);
    Value builtin_vg_run(const std::vector<Value> &args);
    Value builtin_vg_close_window(const std::vector<Value> &args);
    Value builtin_vg_set_window_title(const std::vector<Value> &args);
    Value builtin_vg_set_window_size(const std::vector<Value> &args);
    Value builtin_vg_set_window_bg_gradient(const std::vector<Value> &args);
    Value builtin_vg_create_button(const std::vector<Value> &args);
    Value builtin_vg_button_set_text(const std::vector<Value> &args);
    Value builtin_vg_button_set_color(const std::vector<Value> &args);
    Value builtin_vg_button_set_hover_color(const std::vector<Value> &args);
    Value builtin_vg_button_set_border_radius(const std::vector<Value> &args);
    Value builtin_vg_button_on_click(const std::vector<Value> &args);
    Value builtin_vg_button_set_position(const std::vector<Value> &args);
    Value builtin_vg_create_checkbox(const std::vector<Value> &args);
    Value builtin_vg_checkbox_set_checked(const std::vector<Value> &args);
    Value builtin_vg_checkbox_get_checked(const std::vector<Value> &args);
    Value builtin_vg_checkbox_on_changed(const std::vector<Value> &args);
    Value builtin_vg_create_toggle(const std::vector<Value> &args);
    Value builtin_vg_toggle_set_checked(const std::vector<Value> &args);
    Value builtin_vg_toggle_get_checked(const std::vector<Value> &args);
    Value builtin_vg_toggle_on_changed(const std::vector<Value> &args);
    Value builtin_vg_create_textbox(const std::vector<Value> &args);
    Value builtin_vg_textbox_set_text(const std::vector<Value> &args);
    Value builtin_vg_textbox_get_text(const std::vector<Value> &args);
    Value builtin_vg_textbox_set_placeholder(const std::vector<Value> &args);
    Value builtin_vg_create_slider(const std::vector<Value> &args);
    Value builtin_vg_slider_set_value(const std::vector<Value> &args);
    Value builtin_vg_slider_get_value(const std::vector<Value> &args);
    Value builtin_vg_slider_on_changed(const std::vector<Value> &args);
    Value builtin_vg_create_progressbar(const std::vector<Value> &args);
    Value builtin_vg_progressbar_set_value(const std::vector<Value> &args);
    Value builtin_vg_progressbar_get_value(const std::vector<Value> &args);
    Value builtin_vg_progressbar_set_color(const std::vector<Value> &args);
    Value builtin_vg_create_card(const std::vector<Value> &args);
    Value builtin_vg_card_set_title(const std::vector<Value> &args);
    Value builtin_vg_card_set_elevation(const std::vector<Value> &args);
    Value builtin_vg_create_combobox(const std::vector<Value> &args);
    Value builtin_vg_combobox_add_item(const std::vector<Value> &args);
    Value builtin_vg_combobox_clear(const std::vector<Value> &args);
    Value builtin_vg_combobox_set_selected(const std::vector<Value> &args);
    Value builtin_vg_combobox_get_selected(const std::vector<Value> &args);
    Value builtin_vg_combobox_on_selected(const std::vector<Value> &args);
    Value builtin_vg_create_listbox(const std::vector<Value> &args);
    Value builtin_vg_listbox_add_item(const std::vector<Value> &args);
    Value builtin_vg_listbox_clear(const std::vector<Value> &args);
    Value builtin_vg_listbox_set_selected(const std::vector<Value> &args);
    Value builtin_vg_listbox_get_selected(const std::vector<Value> &args);
    Value builtin_vg_listbox_on_selected(const std::vector<Value> &args);
    Value builtin_vg_create_tabcontrol(const std::vector<Value> &args);
    Value builtin_vg_tabcontrol_add_tab(const std::vector<Value> &args);
    Value builtin_vg_tabcontrol_set_selected(const std::vector<Value> &args);
    Value builtin_vg_create_spinner(const std::vector<Value> &args);
    Value builtin_vg_spinner_start(const std::vector<Value> &args);
    Value builtin_vg_spinner_stop(const std::vector<Value> &args);
    Value builtin_vg_set_position(const std::vector<Value> &args);
    Value builtin_vg_set_size(const std::vector<Value> &args);
    Value builtin_vg_set_visible(const std::vector<Value> &args);
    Value builtin_vg_set_enabled(const std::vector<Value> &args);
    Value builtin_vg_destroy(const std::vector<Value> &args);

}