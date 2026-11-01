

#pragma once
#include "ui_component.h"
#include "renderer.h"
#include "animation.h"
#include <functional>
#include <vector>
#include <string>
#include <algorithm>

namespace gui {


class ModernWindow;




struct ComboBoxItem {
    std::wstring text;
    void* userData = nullptr;
    
    ComboBoxItem() = default;
    ComboBoxItem(const std::wstring& t, void* data = nullptr) : text(t), userData(data) {}
};




class ComboBox : public UIComponent {
public:
    using SelectionCallback = std::function<void(int index, const std::wstring& text)>;
    
    ComboBox()
        : m_selectedIndex(-1)
        , m_isExpanded(false)
        , m_backgroundColor(0.18f, 0.18f, 0.25f, 1.0f)
        , m_borderColor(0.35f, 0.35f, 0.45f, 1.0f)
        , m_textColor(0.95f, 0.95f, 1.0f, 1.0f)
        , m_hoverColor(0.25f, 0.25f, 0.35f, 1.0f)
        , m_fontSize(14.0f)
        , m_borderRadius(6.0f)
        , m_itemHeight(30.0f)
        , m_maxItems(8)
        , m_animProgress(0) {
        
        m_expandAnim = std::make_unique<Animation>();
        m_expandAnim->OnUpdate([this](float value) { m_animProgress = value; });
    }
    
    void AddItem(const std::wstring& text, void* data = nullptr) {
        m_items.push_back(ComboBoxItem(text, data));
        if (m_selectedIndex == -1 && !m_items.empty()) SetSelectedIndex(0);
    }
    
    void ClearItems() { m_items.clear(); m_selectedIndex = -1; }
    int GetItemCount() const { return (int)m_items.size(); }
    
    void SetSelectedIndex(int index) {
        if (index >= 0 && index < (int)m_items.size()) {
            m_selectedIndex = index;
            if (m_callback) m_callback(index, m_items[index].text);
        }
    }
    
    int GetSelectedIndex() const { return m_selectedIndex; }
    std::wstring GetSelectedText() const {
        if (m_selectedIndex >= 0) return m_items[m_selectedIndex].text;
        return L"";
    }
    
    void SetPlaceholder(const std::wstring& text) { m_placeholder = text; }
    void SetItemHeight(float height) { m_itemHeight = height; }
    void SetMaxVisibleItems(int max) { m_maxItems = max; }
    void OnSelectionChanged(SelectionCallback callback) { m_callback = callback; }
    
    void Draw(Renderer* renderer) override {
        if (!m_visible || !renderer) return;
        
        
        RoundedRect mainRect;
        mainRect.rect = m_bounds;
        mainRect.radiusX = m_borderRadius;
        mainRect.radiusY = m_borderRadius;
        renderer->FillRoundedRect(mainRect, m_backgroundColor);
        renderer->DrawRoundedRect(mainRect, m_borderColor, 1.0f);
        
        
        std::wstring displayText = m_selectedIndex >= 0 ? m_items[m_selectedIndex].text : m_placeholder;
        Color textColor = m_selectedIndex >= 0 ? m_textColor : Color(0.5f, 0.5f, 0.6f, 1.0f);
        Rect textRect = m_bounds;
        textRect.left += 10;
        textRect.right -= 30;
        renderer->DrawText(displayText, textRect, textColor, m_fontSize, false, false);
        
        
        float arrowX = m_bounds.right - 20;
        float arrowY = m_bounds.top + m_bounds.Height() / 2;
        renderer->DrawLine(Point(arrowX - 5, arrowY - 3), Point(arrowX, arrowY + 2), m_textColor, 1.5f);
        renderer->DrawLine(Point(arrowX + 5, arrowY - 3), Point(arrowX, arrowY + 2), m_textColor, 1.5f);
        
        
        if (m_isExpanded && m_animProgress > 0.01f) {
            float listHeight = std::min((int)m_items.size(), m_maxItems) * m_itemHeight;
            float expandedHeight = listHeight * m_animProgress;
            
            Rect listRect(m_bounds.left, m_bounds.bottom, m_bounds.right, m_bounds.bottom + expandedHeight);
            RoundedRect listBg;
            listBg.rect = listRect;
            listBg.radiusX = m_borderRadius;
            listBg.radiusY = m_borderRadius;
            renderer->FillRoundedRect(listBg, m_backgroundColor);
            renderer->DrawRoundedRect(listBg, m_borderColor, 1.0f);
            
            for (int i = 0; i < (int)m_items.size() && i < m_maxItems; ++i) {
                Rect itemRect(m_bounds.left, m_bounds.bottom + i * m_itemHeight, 
                              m_bounds.right, m_bounds.bottom + (i + 1) * m_itemHeight);
                if (itemRect.bottom > m_bounds.bottom + expandedHeight) break;
                
                if (i == m_hoverItem) {
                    renderer->FillRect(itemRect, m_hoverColor);
                }
                
                Rect itemTextRect = itemRect;
                itemTextRect.left += 10;
                renderer->DrawText(m_items[i].text, itemTextRect, m_textColor, m_fontSize, false, false);
            }
        }
    }
    
    void Update(float deltaTime) override {
        if (m_expandAnim && m_expandAnim->IsPlaying()) m_expandAnim->Update(deltaTime);
    }
    
    bool HandleMouseDown(const Point& mousePos, int button) override {
        if (!m_enabled || !m_visible || button != 0) return false;
        
        if (IsPointInside(mousePos)) {
            SetExpanded(!m_isExpanded);
            return true;
        }
        
        if (m_isExpanded) {
            float listHeight = std::min((int)m_items.size(), m_maxItems) * m_itemHeight;
            Rect listRect(m_bounds.left, m_bounds.bottom, m_bounds.right, m_bounds.bottom + listHeight);
            
            if (mousePos.x >= listRect.left && mousePos.x <= listRect.right &&
                mousePos.y >= listRect.top && mousePos.y <= listRect.bottom) {
                int index = (int)((mousePos.y - listRect.top) / m_itemHeight);
                if (index >= 0 && index < (int)m_items.size()) {
                    SetSelectedIndex(index);
                    SetExpanded(false);
                }
                return true;
            } else {
                SetExpanded(false);
            }
        }
        
        return false;
    }
    
    bool HandleMouseMove(const Point& mousePos) override {
        if (!m_enabled || !m_visible) return false;
        
        if (m_isExpanded) {
            float listHeight = std::min((int)m_items.size(), m_maxItems) * m_itemHeight;
            Rect listRect(m_bounds.left, m_bounds.bottom, m_bounds.right, m_bounds.bottom + listHeight);
            
            if (mousePos.x >= listRect.left && mousePos.x <= listRect.right &&
                mousePos.y >= listRect.top && mousePos.y <= listRect.bottom) {
                m_hoverItem = (int)((mousePos.y - listRect.top) / m_itemHeight);
                if (m_hoverItem >= (int)m_items.size()) m_hoverItem = -1;
            } else {
                m_hoverItem = -1;
            }
        }
        
        return false;
    }
    
private:
    bool IsPointInside(const Point& p) const {
        return p.x >= m_bounds.left && p.x <= m_bounds.right &&
               p.y >= m_bounds.top && p.y <= m_bounds.bottom;
    }
    
    void SetExpanded(bool expanded) {
        if (m_isExpanded == expanded) return;
        m_isExpanded = expanded;
        m_expandAnim->Start(m_animProgress, expanded ? 1.0f : 0.0f, 0.25f, EasingType::QuadOut);
    }
    
    std::vector<ComboBoxItem> m_items;
    int m_selectedIndex;
    std::wstring m_placeholder;
    bool m_isExpanded;
    int m_hoverItem = -1;
    
    Color m_backgroundColor;
    Color m_borderColor;
    Color m_textColor;
    Color m_hoverColor;
    float m_fontSize;
    float m_borderRadius;
    float m_itemHeight;
    int m_maxItems;
    
    std::unique_ptr<Animation> m_expandAnim;
    float m_animProgress;
    SelectionCallback m_callback;
};




class ListBox : public UIComponent {
public:
    using SelectionCallback = std::function<void(int index, const std::wstring& text)>;
    using DoubleClickCallback = std::function<void(int index, const std::wstring& text)>;
    
    ListBox()
        : m_selectedIndex(-1)
        , m_multiSelect(false)
        , m_backgroundColor(0.12f, 0.12f, 0.18f, 1.0f)
        , m_itemColor(0.18f, 0.18f, 0.25f, 1.0f)
        , m_selectedColor(0.2f, 0.6f, 1.0f, 0.7f)
        , m_hoverColor(0.25f, 0.25f, 0.35f, 0.8f)
        , m_textColor(0.95f, 0.95f, 1.0f, 1.0f)
        , m_fontSize(14.0f)
        , m_itemHeight(28.0f)
        , m_scrollOffset(0)
        , m_hoverIndex(-1) {}
    
    void AddItem(const std::wstring& text, void* data = nullptr) {
        ListItem item;
        item.text = text;
        item.userData = data;
        m_items.push_back(item);
    }
    
    void ClearItems() { m_items.clear(); m_selectedIndex = -1; m_selectedIndices.clear(); }
    int GetItemCount() const { return (int)m_items.size(); }
    
    void SetSelectedIndex(int index) {
        if (!m_multiSelect) {
            m_selectedIndex = index;
            if (m_selectionCallback) {
                std::wstring text = (index >= 0 && index < (int)m_items.size()) ? m_items[index].text : L"";
                m_selectionCallback(index, text);
            }
        } else {
            
            auto it = std::find(m_selectedIndices.begin(), m_selectedIndices.end(), index);
            if (it != m_selectedIndices.end()) {
                m_selectedIndices.erase(it);
            } else if (index >= 0 && index < (int)m_items.size()) {
                m_selectedIndices.push_back(index);
            }
        }
    }
    
    int GetSelectedIndex() const { return m_selectedIndex; }
    void SetMultiSelect(bool multi) { m_multiSelect = multi; }
    void SetItemHeight(float height) { m_itemHeight = height; }
    void SetBackgroundColor(const Color& color) { m_backgroundColor = color; }
    void OnSelectionChanged(SelectionCallback callback) { m_selectionCallback = callback; }
    void OnDoubleClick(DoubleClickCallback callback) { m_doubleClickCallback = callback; }
    
    void Draw(Renderer* renderer) override {
        if (!m_visible || !renderer) return;
        
        
        renderer->FillRect(m_bounds, m_backgroundColor);
        renderer->DrawRect(m_bounds, Color(0.25f, 0.25f, 0.35f, 1.0f), 1.0f);
        
        
        int startIndex = (int)(m_scrollOffset / m_itemHeight);
        int visibleCount = (int)(m_bounds.Height() / m_itemHeight) + 2;
        startIndex = std::max(0, startIndex);
        int endIndex = std::min((int)m_items.size(), startIndex + visibleCount);
        
        for (int i = startIndex; i < endIndex; ++i) {
            float y = m_bounds.top + i * m_itemHeight - m_scrollOffset;
            Rect itemRect(m_bounds.left, y, m_bounds.right, y + m_itemHeight);
            
            
            bool isSelected = m_multiSelect ? 
                std::find(m_selectedIndices.begin(), m_selectedIndices.end(), i) != m_selectedIndices.end() :
                i == m_selectedIndex;
            
            Color bgColor;
            if (isSelected) bgColor = m_selectedColor;
            else if (i == m_hoverIndex) bgColor = m_hoverColor;
            else bgColor = m_itemColor;
            
            renderer->FillRect(itemRect, bgColor);
            
            
            Rect textRect = itemRect;
            textRect.left += 8;
            renderer->DrawText(m_items[i].text, textRect, m_textColor, m_fontSize, false, false);
        }
    }
    
    void Update(float deltaTime) override {}
    
    bool HandleMouseMove(const Point& mousePos) override {
        if (!m_enabled || !m_visible) return false;
        
        if (mousePos.x >= m_bounds.left && mousePos.x <= m_bounds.right &&
            mousePos.y >= m_bounds.top && mousePos.y <= m_bounds.bottom) {
            int index = (int)((mousePos.y - m_bounds.top + m_scrollOffset) / m_itemHeight);
            if (index >= 0 && index < (int)m_items.size()) {
                m_hoverIndex = index;
            } else {
                m_hoverIndex = -1;
            }
        } else {
            m_hoverIndex = -1;
        }
        return false;
    }
    
    bool HandleMouseDown(const Point& mousePos, int button) override {
        if (!m_enabled || !m_visible || button != 0) return false;
        
        if (mousePos.x >= m_bounds.left && mousePos.x <= m_bounds.right &&
            mousePos.y >= m_bounds.top && mousePos.y <= m_bounds.bottom) {
            int index = (int)((mousePos.y - m_bounds.top + m_scrollOffset) / m_itemHeight);
            if (index >= 0 && index < (int)m_items.size()) {
                SetSelectedIndex(index);
                return true;
            }
        }
        return false;
    }
    
    bool HandleMouseUp(const Point& mousePos, int button) override {
        if (m_lastClickIndex >= 0 && m_doubleClickCallback) {
            auto now = std::chrono::steady_clock::now();
            float diff = std::chrono::duration<float>(now - m_lastClickTime).count();
            if (diff < 0.3f && m_lastClickIndex == m_hoverIndex) {
                m_doubleClickCallback(m_lastClickIndex, m_items[m_lastClickIndex].text);
            }
        }
        m_lastClickIndex = m_hoverIndex;
        m_lastClickTime = std::chrono::steady_clock::now();
        return false;
    }
    
    bool HandleMouseWheel(float delta) {
        if (!m_enabled || !m_visible) return false;
        float maxScroll = std::max(0.0f, (float)m_items.size() * m_itemHeight - m_bounds.Height());
        m_scrollOffset -= delta * 30.0f;
        m_scrollOffset = std::max(0.0f, std::min(maxScroll, m_scrollOffset));
        return true;
    }
    
private:
    struct ListItem {
        std::wstring text;
        void* userData = nullptr;
    };
    
    std::vector<ListItem> m_items;
    int m_selectedIndex;
    std::vector<int> m_selectedIndices;
    bool m_multiSelect;
    
    Color m_backgroundColor;
    Color m_itemColor;
    Color m_selectedColor;
    Color m_hoverColor;
    Color m_textColor;
    float m_fontSize;
    float m_itemHeight;
    float m_scrollOffset;
    int m_hoverIndex;
    int m_lastClickIndex = -1;
    std::chrono::steady_clock::time_point m_lastClickTime;
    
    SelectionCallback m_selectionCallback;
    DoubleClickCallback m_doubleClickCallback;
};




struct TabPage {
    std::wstring title;
    std::vector<std::unique_ptr<UIComponent>> components;
};




class TabControl : public UIComponent {
public:
    using TabCallback = std::function<void(int index, const std::wstring& title)>;
    
    TabControl()
        : m_selectedIndex(0)
        , m_tabHeight(36.0f)
        , m_backgroundColor(0.12f, 0.12f, 0.18f, 1.0f)
        , m_tabActiveColor(0.2f, 0.6f, 1.0f, 1.0f)
        , m_tabInactiveColor(0.18f, 0.18f, 0.25f, 1.0f)
        , m_textActiveColor(1.0f, 1.0f, 1.0f, 1.0f)
        , m_textInactiveColor(0.6f, 0.6f, 0.7f, 1.0f)
        , m_borderRadius(8.0f)
        , m_hoverTab(-1) {}
    
    void AddTab(const std::wstring& title) {
        TabPage page;
        page.title = title;
        m_pages.push_back(std::move(page));
        if (m_selectedIndex == -1) m_selectedIndex = 0;
    }
    
    void RemoveTab(int index) {
        if (index >= 0 && index < (int)m_pages.size()) {
            m_pages.erase(m_pages.begin() + index);
            if (m_selectedIndex >= index) m_selectedIndex = std::max(0, m_selectedIndex - 1);
        }
    }
    
    void SetSelectedIndex(int index) {
        if (index >= 0 && index < (int)m_pages.size()) {
            m_selectedIndex = index;
            if (m_callback) m_callback(index, m_pages[index].title);
        }
    }
    
    int GetSelectedIndex() const { return m_selectedIndex; }
    
    void AddComponentToTab(int tabIndex, std::unique_ptr<UIComponent> component) {
        if (tabIndex >= 0 && tabIndex < (int)m_pages.size()) {
            m_pages[tabIndex].components.push_back(std::move(component));
        }
    }
    
    void Draw(Renderer* renderer) override {
        if (!m_visible || !renderer) return;
        
        
        Rect contentRect = GetContentRect();
        renderer->FillRect(contentRect, m_backgroundColor);
        renderer->DrawRect(contentRect, Color(0.25f, 0.25f, 0.35f, 1.0f), 1.0f);
        
        
        float tabWidth = 120.0f;
        for (size_t i = 0; i < m_pages.size(); ++i) {
            float tabLeft = m_bounds.left + i * tabWidth;
            float tabRight = tabLeft + tabWidth;
            
            bool isActive = (int)i == m_selectedIndex;
            bool isHover = (int)i == m_hoverTab;
            
            
            RoundedRect tabRect;
            tabRect.rect = Rect(tabLeft, m_bounds.top, tabRight, m_bounds.top + m_tabHeight);
            if (isActive) {
                tabRect.radiusX = m_borderRadius;
                tabRect.radiusY = m_borderRadius;
                renderer->FillRoundedRect(tabRect, m_tabActiveColor);
            } else {
                tabRect.radiusX = m_borderRadius;
                tabRect.radiusY = m_borderRadius;
                Color bgColor = isHover ? Color::Lerp(m_tabInactiveColor, m_tabActiveColor, 0.3f) : m_tabInactiveColor;
                renderer->FillRoundedRect(tabRect, bgColor);
            }
            
            
            Rect textRect = tabRect.rect;
            Color textColor = isActive ? m_textActiveColor : m_textInactiveColor;
            renderer->DrawText(m_pages[i].title, textRect, textColor, 14.0f, false, true);
        }
        
        
        if (m_selectedIndex >= 0 && m_selectedIndex < (int)m_pages.size()) {
            for (auto& comp : m_pages[m_selectedIndex].components) {
                comp->Draw(renderer);
            }
        }
    }
    
    void Update(float deltaTime) override {
        if (m_selectedIndex >= 0 && m_selectedIndex < (int)m_pages.size()) {
            for (auto& comp : m_pages[m_selectedIndex].components) {
                comp->Update(deltaTime);
            }
        }
    }
    
    bool HandleMouseMove(const Point& mousePos) override {
        float tabWidth = 120.0f;
        m_hoverTab = -1;
        
        for (size_t i = 0; i < m_pages.size(); ++i) {
            float tabLeft = m_bounds.left + i * tabWidth;
            float tabRight = tabLeft + tabWidth;
            if (mousePos.x >= tabLeft && mousePos.x <= tabRight &&
                mousePos.y >= m_bounds.top && mousePos.y <= m_bounds.top + m_tabHeight) {
                m_hoverTab = (int)i;
                break;
            }
        }
        
        
        if (m_selectedIndex >= 0 && m_selectedIndex < (int)m_pages.size()) {
            for (auto& comp : m_pages[m_selectedIndex].components) {
                comp->HandleMouseMove(mousePos);
            }
        }
        return false;
    }
    
    bool HandleMouseDown(const Point& mousePos, int button) override {
        float tabWidth = 120.0f;
        for (size_t i = 0; i < m_pages.size(); ++i) {
            float tabLeft = m_bounds.left + i * tabWidth;
            float tabRight = tabLeft + tabWidth;
            if (mousePos.x >= tabLeft && mousePos.x <= tabRight &&
                mousePos.y >= m_bounds.top && mousePos.y <= m_bounds.top + m_tabHeight) {
                SetSelectedIndex((int)i);
                return true;
            }
        }
        
        
        if (m_selectedIndex >= 0 && m_selectedIndex < (int)m_pages.size()) {
            for (auto& comp : m_pages[m_selectedIndex].components) {
                if (comp->HandleMouseDown(mousePos, button)) return true;
            }
        }
        return false;
    }
    
    bool HandleMouseUp(const Point& mousePos, int button) override {
        if (m_selectedIndex >= 0 && m_selectedIndex < (int)m_pages.size()) {
            for (auto& comp : m_pages[m_selectedIndex].components) {
                if (comp->HandleMouseUp(mousePos, button)) return true;
            }
        }
        return false;
    }
    
    void OnTabChanged(TabCallback callback) { m_callback = callback; }
    
private:
    Rect GetContentRect() const {
        return Rect(m_bounds.left, m_bounds.top + m_tabHeight + 1, 
                    m_bounds.right, m_bounds.bottom);
    }
    
    std::vector<TabPage> m_pages;
    int m_selectedIndex;
    float m_tabHeight;
    
    Color m_backgroundColor;
    Color m_tabActiveColor;
    Color m_tabInactiveColor;
    Color m_textActiveColor;
    Color m_textInactiveColor;
    float m_borderRadius;
    int m_hoverTab;
    
    TabCallback m_callback;
};




class Tooltip {
public:
    static Tooltip& Instance() {
        static Tooltip instance;
        return instance;
    }
    
    void Show(const std::wstring& text, const Point& position, UIComponent* target = nullptr) {
        m_text = text;
        m_position = position;
        m_target = target;
        m_visible = true;
        m_alphaAnim.Start(0, 1, 0.15f, EasingType::QuadOut);
    }
    
    void Hide() {
        m_visible = false;
        m_target = nullptr;
    }
    
    void Update(float deltaTime) {
        m_alphaAnim.Update(deltaTime);
        m_alpha = m_alphaAnim.GetValue();
    }
    
    void Draw(Renderer* renderer) {
        if (!m_visible || m_text.empty()) return;
        
        Size textSize = renderer->MeasureText(m_text, 12.0f, false);
        float padding = 8.0f;
        float width = textSize.width + padding * 2;
        float height = textSize.height + padding * 2;
        
        float x = m_position.x + 10;
        float y = m_position.y + 20;
        
        
        if (x + width > 1000) x = m_position.x - width - 10;
        if (y + height > 800) y = m_position.y - height - 10;
        
        RoundedRect bgRect;
        bgRect.rect = Rect(x, y, x + width, y + height);
        bgRect.radiusX = 6.0f;
        bgRect.radiusY = 6.0f;
        
        Color bgColor(0.1f, 0.1f, 0.15f, 0.95f * m_alpha);
        renderer->FillRoundedRect(bgRect, bgColor);
        
        Rect textRect(x + padding, y + padding, x + width - padding, y + height - padding);
        renderer->DrawText(m_text, textRect, Color(1, 1, 1, m_alpha), 12.0f, false, false);
    }
    
    bool IsVisible() const { return m_visible; }
    UIComponent* GetTarget() const { return m_target; }
    
private:
    Tooltip() : m_visible(false), m_alpha(0) {}
    
    std::wstring m_text;
    Point m_position;
    UIComponent* m_target = nullptr;
    bool m_visible;
    float m_alpha;
    Animation m_alphaAnim;
};





class Spinner : public UIComponent {
public:
    Spinner()
        : m_angle(0)
        , m_radius(15.0f)
        , m_thickness(3.0f)
        , m_color(0.2f, 0.6f, 1.0f, 1.0f)
        , m_speed(8.0f)  
        , m_running(true)
        , m_dotCount(12) {}  
    
    void SetRadius(float r) { m_radius = r; }
    void SetColor(const Color& c) { m_color = c; }
    void SetSpeed(float speed) { m_speed = speed; }
    void Start() { m_running = true; }
    void Stop() { m_running = false; }
    
    void Draw(Renderer* renderer) override {
        if (!m_visible || !renderer || !m_running) return;
        
        float cx = (m_bounds.left + m_bounds.right) / 2;
        float cy = (m_bounds.top + m_bounds.bottom) / 2;
        
        
        float angleStep = 360.0f / m_dotCount;
        
        for (int i = 0; i < m_dotCount; ++i) {
            
            float angle = m_angle + i * angleStep;
            float rad = angle * 3.14159f / 180.0f;
            
            
            float angleDiff = std::abs(angle - m_angle);
            if (angleDiff > 180) angleDiff = 360 - angleDiff;
            float opacity = 0.2f + (1.0f - angleDiff / 180.0f) * 0.8f;
            
            float x = cx + cosf(rad) * m_radius;
            float y = cy + sinf(rad) * m_radius;
            
            Ellipse dot(x, y, m_thickness);
            renderer->FillEllipse(dot, Color(m_color.r, m_color.g, m_color.b, opacity));
        }
    }
    
    void Update(float deltaTime) override {
        if (m_running) {
            
            float dt = std::min(deltaTime, 0.033f);
            m_angle += dt * m_speed * 180.0f;  
            if (m_angle >= 360) m_angle -= 360;
        }
    }
    
private:
    float m_angle;
    float m_radius;
    float m_thickness;
    Color m_color;
    float m_speed;
    bool m_running;
    int m_dotCount;  
};




enum class MessageBoxButton {
    OK,
    OKCancel,
    YesNo,
    YesNoCancel
};

enum class MessageBoxIcon {
    None,
    Info,
    Warning,
    Error,
    Question
};

enum class MessageBoxResult {
    OK,
    Cancel,
    Yes,
    No
};

class MessageBox {
public:
    static MessageBoxResult Show(ModernWindow* parent, const std::wstring& title, 
                                  const std::wstring& message,
                                  MessageBoxButton buttons = MessageBoxButton::OK,
                                  MessageBoxIcon icon = MessageBoxIcon::None) {
        
        UINT type = MB_TASKMODAL;
        switch (buttons) {
            case MessageBoxButton::OK: type |= MB_OK; break;
            case MessageBoxButton::OKCancel: type |= MB_OKCANCEL; break;
            case MessageBoxButton::YesNo: type |= MB_YESNO; break;
            case MessageBoxButton::YesNoCancel: type |= MB_YESNOCANCEL; break;
        }
        switch (icon) {
            case MessageBoxIcon::Info: type |= MB_ICONINFORMATION; break;
            case MessageBoxIcon::Warning: type |= MB_ICONWARNING; break;
            case MessageBoxIcon::Error: type |= MB_ICONERROR; break;
            case MessageBoxIcon::Question: type |= MB_ICONQUESTION; break;
            default: break;
        }
        
       
        int result = ::MessageBoxW(parent ? parent->GetHwnd() : nullptr, 
                                   message.c_str(), 
                                   title.c_str(), 
                                   type);
        
        switch (result) {
            case IDOK: return MessageBoxResult::OK;
            case IDCANCEL: return MessageBoxResult::Cancel;
            case IDYES: return MessageBoxResult::Yes;
            case IDNO: return MessageBoxResult::No;
            default: return MessageBoxResult::Cancel;
        }
    }
};

} 