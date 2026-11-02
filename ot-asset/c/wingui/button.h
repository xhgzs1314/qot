#pragma once
#include "ui_component.h"
#include "renderer.h"
#include "animation.h"
#include <functional>

namespace gui {
class Button : public UIComponent {
public:
    using ClickCallback = std::function<void()>;
    
    Button();
    ~Button();
    
    void SetText(const std::wstring& text);
    void SetTextColor(const Color& color);
    void SetBackgroundColor(const Color& color);
    void SetHoverColor(const Color& color);
    void SetPressColor(const Color& color);
    void SetBorderRadius(float radius);
    void SetFontSize(float size);
    void OnClick(ClickCallback callback);
    
    void Draw(Renderer* renderer) override;
    void Update(float deltaTime) override;
    bool HandleMouseMove(const Point& mousePos) override;
    bool HandleMouseDown(const Point& mousePos, int button) override;
    bool HandleMouseUp(const Point& mousePos, int button) override;

    // v4：状态位（hover/pressed 补充基类 disabled）
    unsigned StyleFlags() const override {
        unsigned f = UIComponent::StyleFlags();
        if (m_state == State::Hovered || (m_state == State::Normal && m_currentHoverProgress > 0.5f)) f |= 0x1;
        if (m_state == State::Pressed) f |= 0x2;
        return f;
    }
    
private:
    enum class State {
        Normal,
        Hovered,
        Pressed
    };

    // v4：样式叠加解析（isSet 优先，否则组件默认）
    float Radius() const { return m_style.radiusSet ? m_style.radius : m_borderRadius; }
    float FontSize() const { return m_style.textSet ? m_style.textSize : m_fontSize; }
    bool FontBold() const { return m_style.textSet ? m_style.textBold : false; }
    
    std::wstring m_text;
    Color m_textColor;
    Color m_normalColor;
    Color m_hoverColor;
    Color m_pressColor;
    float m_borderRadius;
    float m_fontSize;
    State m_state;
    bool m_isPressed;
    std::unique_ptr<Animation> m_hoverAnim;
    float m_currentHoverProgress;
    
    ClickCallback m_clickCallback;
};
inline Button::Button()
    : m_text(L"Button")
    , m_textColor(1, 1, 1, 1)
    , m_normalColor(0.2f, 0.6f, 1.0f, 0.9f)
    , m_hoverColor(0.3f, 0.7f, 1.0f, 1.0f)
    , m_pressColor(0.15f, 0.5f, 0.85f, 1.0f)
    , m_borderRadius(8.0f)
    , m_fontSize(16.0f)
    , m_state(State::Normal)
    , m_isPressed(false)
    , m_currentHoverProgress(0.0f) {
    
    m_hoverAnim = std::make_unique<Animation>();
    m_hoverAnim->OnUpdate([this](float value) {
        m_currentHoverProgress = value;
    });
}

inline Button::~Button() = default;

inline void Button::SetText(const std::wstring& text) {
    m_text = text;
}

inline void Button::SetTextColor(const Color& color) {
    m_textColor = color;
}

inline void Button::SetBackgroundColor(const Color& color) {
    m_normalColor = color;
}

inline void Button::SetHoverColor(const Color& color) {
    m_hoverColor = color;
}

inline void Button::SetPressColor(const Color& color) {
    m_pressColor = color;
}

inline void Button::SetBorderRadius(float radius) {
    m_borderRadius = radius;
}

inline void Button::SetFontSize(float size) {
    m_fontSize = size;
}

inline void Button::OnClick(ClickCallback callback) {
    m_clickCallback = callback;
}

inline void Button::Draw(Renderer* renderer) {
    if (!m_visible || !renderer) return;
    // v4：owner-draw 接管 —— 默认绘制整体让位，回调里自由作画
    if (m_paintFn) {
        m_paintFn(renderer, m_bounds.Width(), m_bounds.Height(), StyleFlags());
        return;
    }
    unsigned flags = StyleFlags();
    Color bgColor;
    if (!ResolvedStateBg(flags, bgColor)) {
        switch (m_state) {
            case State::Pressed:
                bgColor = m_pressColor;
                break;
            case State::Hovered:
                bgColor = Color::Lerp(m_normalColor, m_hoverColor, m_currentHoverProgress);
                break;
            default:
                bgColor = m_normalColor;
                break;
        }
    }
    if (m_style.bgGradSet && !(flags & 0x6)) {   // 渐变背景（hover/pressed 时不叠加）
        renderer->FillRoundedRectGradient(RoundedRect(m_bounds, Radius(), Radius()),
                                          m_style.bgGrad1, m_style.bgGrad2,
                                          m_style.bgGradVertical != 0);
    }

    RoundedRect rect;
    rect.rect = m_bounds;
    rect.radiusX = Radius();
    rect.radiusY = Radius();

    if (!m_style.bgGradSet || (flags & 0x6)) renderer->FillRoundedRect(rect, bgColor);
    Color bc;
    if (m_style.borderSet) { bc = m_style.borderColor; }
    else bc = Color(1, 1, 1, 0.3f);
    renderer->DrawRoundedRect(rect, bc, m_style.borderSet ? m_style.borderWidth : 1.0f);
    Color tc;
    if (!ResolvedStateText(flags, tc)) tc = m_textColor;
    renderer->DrawText(m_text, m_bounds, tc, FontSize(), FontBold(), true);
}

inline void Button::Update(float deltaTime) {
    if (m_hoverAnim && m_hoverAnim->IsPlaying()) {
        m_hoverAnim->Update(deltaTime);
    }
}

inline bool Button::HandleMouseMove(const Point& mousePos) {
    if (!m_enabled || !m_visible) return false;
    
    bool inside = mousePos.x >= m_bounds.left && mousePos.x <= m_bounds.right &&
                  mousePos.y >= m_bounds.top && mousePos.y <= m_bounds.bottom;
    
    if (inside && m_state != State::Hovered && !m_isPressed) {
        m_state = State::Hovered;
        m_hoverAnim->Start(0, 1, 0.2f, EasingType::QuadOut);
    } else if (!inside && m_state == State::Hovered && !m_isPressed) {
        m_state = State::Normal;
        m_hoverAnim->Start(1, 0, 0.2f, EasingType::QuadOut);
    }
    
    return inside;
}

inline bool Button::HandleMouseDown(const Point& mousePos, int button) {
    if (!m_enabled || !m_visible || button != 0) return false;
    
    bool inside = mousePos.x >= m_bounds.left && mousePos.x <= m_bounds.right &&
                  mousePos.y >= m_bounds.top && mousePos.y <= m_bounds.bottom;
    
    if (inside) {
        m_state = State::Pressed;
        m_isPressed = true;
        return true;
    }
    return false;
}

inline bool Button::HandleMouseUp(const Point& mousePos, int button) {
    if (!m_enabled || !m_visible || button != 0) return false;
    
    bool inside = mousePos.x >= m_bounds.left && mousePos.x <= m_bounds.right &&
                  mousePos.y >= m_bounds.top && mousePos.y <= m_bounds.bottom;
    
    if (m_isPressed) {
        m_isPressed = false;
        if (inside) {
            m_state = State::Hovered;
            if (m_clickCallback) m_clickCallback();
        } else {
            m_state = State::Normal;
            m_hoverAnim->Start(1, 0, 0.2f, EasingType::QuadOut);
        }
        return true;
    }
    return false;
}

} 