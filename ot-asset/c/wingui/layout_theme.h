

#pragma once
#include "core_types.h"
#include "modern_window.h"
#include "ui_component.h"
#include <functional>
#include <unordered_map>
#include <string>
#include <cfloat>   // FLT_MAX（MinGW 下不会随 windows.h 带入）

namespace gui {




enum class LayoutType {
    Absolute,   
    Flex,       
    Grid        
};

enum class AnchorType {
    None,
    TopLeft, TopRight, BottomLeft, BottomRight,
    Left, Right, Top, Bottom,
    Center
};

enum class DockType {
    None,
    Left, Right, Top, Bottom, Fill
};




struct Padding {
    float left = 0, top = 0, right = 0, bottom = 0;
    
    Padding() = default;
    Padding(float all) : left(all), top(all), right(all), bottom(all) {}
    Padding(float lr, float tb) : left(lr), top(tb), right(lr), bottom(tb) {}
    Padding(float l, float t, float r, float b) : left(l), top(t), right(r), bottom(b) {}
};

struct Margin {
    float left = 0, top = 0, right = 0, bottom = 0;
    
    Margin() = default;
    Margin(float all) : left(all), top(all), right(all), bottom(all) {}
    Margin(float lr, float tb) : left(lr), top(tb), right(lr), bottom(tb) {}
    Margin(float l, float t, float r, float b) : left(l), top(t), right(r), bottom(b) {}
};




class LayoutProperties {
public:
    Padding padding;
    Margin margin;
    AnchorType anchor = AnchorType::None;
    DockType dock = DockType::None;
    LayoutType layout = LayoutType::Absolute;
    int flexGrow = 0;
    int gridRow = 0, gridCol = 0;
    int gridRowSpan = 1, gridColSpan = 1;
    
    
    float offsetX = 0, offsetY = 0;
    
    
    bool autoSize = false;
    
    
    float minWidth = 0, minHeight = 0;
    float maxWidth = FLT_MAX, maxHeight = FLT_MAX;
};


class LayoutHelper {
public:
    static LayoutProperties& GetProps(UIComponent* comp) {
        static std::unordered_map<UIComponent*, LayoutProperties> props;
        return props[comp];
    }
    
    static void SetPadding(UIComponent* comp, const Padding& p) { GetProps(comp).padding = p; }
    static void SetMargin(UIComponent* comp, const Margin& m) { GetProps(comp).margin = m; }
    static void SetAnchor(UIComponent* comp, AnchorType a) { GetProps(comp).anchor = a; }
    static void SetDock(UIComponent* comp, DockType d) { GetProps(comp).dock = d; }
    static void SetFlexGrow(UIComponent* comp, int grow) { GetProps(comp).flexGrow = grow; }
    static void SetMinSize(UIComponent* comp, float w, float h) { 
        GetProps(comp).minWidth = w; GetProps(comp).minHeight = h; 
    }
    static void SetMaxSize(UIComponent* comp, float w, float h) { 
        GetProps(comp).maxWidth = w; GetProps(comp).maxHeight = h; 
    }
    
    
    static void ApplyAnchor(UIComponent* comp, const Rect& parentBounds, const Rect& originalBounds) {
        auto& props = GetProps(comp);
        if (props.anchor == AnchorType::None) return;
        
        Rect newBounds = originalBounds;
        float parentW = parentBounds.Width();
        float parentH = parentBounds.Height();
        float origW = originalBounds.Width();
        float origH = originalBounds.Height();
        
        switch (props.anchor) {
            case AnchorType::TopLeft:
                newBounds.left = parentBounds.left + props.offsetX;
                newBounds.top = parentBounds.top + props.offsetY;
                break;
            case AnchorType::TopRight:
                newBounds.right = parentBounds.right - props.offsetX;
                newBounds.top = parentBounds.top + props.offsetY;
                newBounds.left = newBounds.right - origW;
                break;
            case AnchorType::BottomLeft:
                newBounds.left = parentBounds.left + props.offsetX;
                newBounds.bottom = parentBounds.bottom - props.offsetY;
                newBounds.top = newBounds.bottom - origH;
                break;
            case AnchorType::BottomRight:
                newBounds.right = parentBounds.right - props.offsetX;
                newBounds.bottom = parentBounds.bottom - props.offsetY;
                newBounds.left = newBounds.right - origW;
                newBounds.top = newBounds.bottom - origH;
                break;
            case AnchorType::Left:
                newBounds.left = parentBounds.left + props.offsetX;
                newBounds.top = parentBounds.top + props.offsetY;
                break;
            case AnchorType::Right:
                newBounds.right = parentBounds.right - props.offsetX;
                newBounds.left = newBounds.right - origW;
                newBounds.top = parentBounds.top + props.offsetY;
                break;
            case AnchorType::Top:
                newBounds.left = parentBounds.left + props.offsetX;
                newBounds.top = parentBounds.top + props.offsetY;
                break;
            case AnchorType::Bottom:
                newBounds.left = parentBounds.left + props.offsetX;
                newBounds.bottom = parentBounds.bottom - props.offsetY;
                newBounds.top = newBounds.bottom - origH;
                break;
            case AnchorType::Center:
                newBounds.left = parentBounds.left + (parentW - origW) / 2 + props.offsetX;
                newBounds.top = parentBounds.top + (parentH - origH) / 2 + props.offsetY;
                break;
            default: break;
        }
        
        
        newBounds.right = newBounds.left + origW;
        newBounds.bottom = newBounds.top + origH;
        if (origW < props.minWidth) newBounds.right = newBounds.left + props.minWidth;
        if (origH < props.minHeight) newBounds.bottom = newBounds.top + props.minHeight;
        if (origW > props.maxWidth) newBounds.right = newBounds.left + props.maxWidth;
        if (origH > props.maxHeight) newBounds.bottom = newBounds.top + props.maxHeight;
        
        comp->SetBounds(newBounds);
    }
};




struct ColorScheme {
    Color background;
    Color surface;
    Color primary;
    Color primaryHover;
    Color primaryPress;
    Color text;
    Color textSecondary;
    Color border;
    Color error;
    Color success;
    Color warning;
    
    
    static ColorScheme Dark() {
        ColorScheme scheme;
        scheme.background = Color(0.07f, 0.07f, 0.10f, 1.0f);
        scheme.surface = Color(0.12f, 0.12f, 0.18f, 1.0f);
        scheme.primary = Color(0.2f, 0.6f, 1.0f, 1.0f);
        scheme.primaryHover = Color(0.3f, 0.7f, 1.0f, 1.0f);
        scheme.primaryPress = Color(0.15f, 0.5f, 0.85f, 1.0f);
        scheme.text = Color(0.95f, 0.95f, 1.0f, 1.0f);
        scheme.textSecondary = Color(0.6f, 0.6f, 0.7f, 1.0f);
        scheme.border = Color(0.25f, 0.25f, 0.35f, 1.0f);
        scheme.error = Color(0.95f, 0.25f, 0.25f, 1.0f);
        scheme.success = Color(0.25f, 0.85f, 0.45f, 1.0f);
        scheme.warning = Color(0.95f, 0.65f, 0.15f, 1.0f);
        return scheme;
    }
    
    static ColorScheme Light() {
        ColorScheme scheme;
        scheme.background = Color(0.95f, 0.95f, 0.98f, 1.0f);
        scheme.surface = Color(1.0f, 1.0f, 1.0f, 1.0f);
        scheme.primary = Color(0.2f, 0.6f, 1.0f, 1.0f);
        scheme.primaryHover = Color(0.3f, 0.7f, 1.0f, 1.0f);
        scheme.primaryPress = Color(0.15f, 0.5f, 0.85f, 1.0f);
        scheme.text = Color(0.1f, 0.1f, 0.15f, 1.0f);
        scheme.textSecondary = Color(0.4f, 0.4f, 0.5f, 1.0f);
        scheme.border = Color(0.75f, 0.75f, 0.85f, 1.0f);
        scheme.error = Color(0.9f, 0.2f, 0.2f, 1.0f);
        scheme.success = Color(0.2f, 0.7f, 0.3f, 1.0f);
        scheme.warning = Color(0.9f, 0.6f, 0.1f, 1.0f);
        return scheme;
    }
};

class ThemeManager {
public:
    using ThemeCallback = std::function<void(const ColorScheme&)>;
    
    static ThemeManager& Instance() {
        static ThemeManager instance;
        return instance;
    }
    
    void SetTheme(const ColorScheme& scheme) {
        m_currentScheme = scheme;
        for (auto& callback : m_callbacks) {
            callback(scheme);
        }
    }
    
    void SetThemePreset(const std::string& name) {
        if (name == "dark") SetTheme(ColorScheme::Dark());
        else if (name == "light") SetTheme(ColorScheme::Light());
    }
    
    const ColorScheme& GetCurrentTheme() const { return m_currentScheme; }
    
    void SetAccentColor(const Color& color) {
        m_currentScheme.primary = color;
        m_currentScheme.primaryHover = Color::Lerp(color, Color(1, 1, 1, 1), 0.15f);
        m_currentScheme.primaryPress = Color::Lerp(color, Color(0, 0, 0, 1), 0.15f);
        SetTheme(m_currentScheme);
    }
    
    void RegisterCallback(ThemeCallback callback) {
        m_callbacks.push_back(callback);
    }
    
private:
    ThemeManager() {
        m_currentScheme = ColorScheme::Dark();
    }
    
    ColorScheme m_currentScheme;
    std::vector<ThemeCallback> m_callbacks;
};




class WindowHelper {
public:
    static void SetMinWindowSize(ModernWindow* window, float width, float height) {
        if (window && window->GetHwnd()) {
            RECT rc = { 0, 0, (LONG)width, (LONG)height };
            AdjustWindowRectEx(&rc, GetWindowLong(window->GetHwnd(), GWL_STYLE), FALSE, 
                               GetWindowLong(window->GetHwnd(), GWL_EXSTYLE));
            SetWindowPos(window->GetHwnd(), nullptr, 0, 0, rc.right - rc.left, rc.bottom - rc.top,
                         SWP_NOMOVE | SWP_NOZORDER | SWP_NOACTIVATE);
        }
    }
    
    static void SetMaxWindowSize(ModernWindow* window, float width, float height) {
        if (window && window->GetHwnd()) {
            RECT rc = { 0, 0, (LONG)width, (LONG)height };
            AdjustWindowRectEx(&rc, GetWindowLong(window->GetHwnd(), GWL_STYLE), FALSE,
                               GetWindowLong(window->GetHwnd(), GWL_EXSTYLE));
            
            SetWindowPos(window->GetHwnd(), nullptr, 0, 0, rc.right - rc.left, rc.bottom - rc.top,
                         SWP_NOMOVE | SWP_NOZORDER | SWP_NOACTIVATE);
        }
    }
    
    static void SetTopMost(ModernWindow* window, bool topMost) {
        if (window && window->GetHwnd()) {
            SetWindowPos(window->GetHwnd(), topMost ? HWND_TOPMOST : HWND_NOTOPMOST,
                         0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE);
        }
    }
    
    static void SetWindowOpacity(ModernWindow* window, float opacity) {
        if (window && window->GetHwnd()) {
            SetWindowLong(window->GetHwnd(), GWL_EXSTYLE, 
                          GetWindowLong(window->GetHwnd(), GWL_EXSTYLE) | WS_EX_LAYERED);
            SetLayeredWindowAttributes(window->GetHwnd(), 0, (BYTE)(opacity * 255), LWA_ALPHA);
        }
    }
    
    static void Minimize(ModernWindow* window) {
        if (window && window->GetHwnd()) ShowWindow(window->GetHwnd(), SW_MINIMIZE);
    }
    
    static void Maximize(ModernWindow* window) {
        if (window && window->GetHwnd()) ShowWindow(window->GetHwnd(), SW_MAXIMIZE);
    }
    
    static void Restore(ModernWindow* window) {
        if (window && window->GetHwnd()) ShowWindow(window->GetHwnd(), SW_RESTORE);
    }
};




class EventHelper {
public:
    using HoverCallback = std::function<void(bool hovered)>;
    using FocusCallback = std::function<void(bool focused)>;
    using KeyCallback = std::function<void(int vkey, bool down)>;
    using DragCallback = std::function<void(const Point& pos, bool dragging)>;
    
    struct Events {
        HoverCallback onHover;
        FocusCallback onFocus;
        KeyCallback onKey;
        DragCallback onDrag;
        bool isHovered = false;
        bool isFocused = false;
        bool isDragging = false;
        Point dragStart;
    };
    
    static Events& GetEvents(UIComponent* comp) {
        static std::unordered_map<UIComponent*, Events> events;
        return events[comp];
    }
    
    static void SetOnHover(UIComponent* comp, HoverCallback cb) { GetEvents(comp).onHover = cb; }
    static void SetOnFocus(UIComponent* comp, FocusCallback cb) { GetEvents(comp).onFocus = cb; }
    static void SetOnKey(UIComponent* comp, KeyCallback cb) { GetEvents(comp).onKey = cb; }
    static void SetOnDrag(UIComponent* comp, DragCallback cb) { GetEvents(comp).onDrag = cb; }
    
    static void UpdateHover(UIComponent* comp, bool hovered) {
        auto& events = GetEvents(comp);
        if (events.isHovered != hovered && events.onHover) {
            events.onHover(hovered);
        }
        events.isHovered = hovered;
    }
    
    static void UpdateFocus(UIComponent* comp, bool focused) {
        auto& events = GetEvents(comp);
        if (events.isFocused != focused && events.onFocus) {
            events.onFocus(focused);
        }
        events.isFocused = focused;
    }
};

} 